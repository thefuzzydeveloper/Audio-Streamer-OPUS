#include <jni.h>
#include <android/log.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <opus/opus.h>
#include <SLES/OpenSLES.h>
#include <SLES/OpenSLES_Android.h>

#define TAG "OpusAudioPlayer"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

#define SAMPLE_RATE 48000
#define CHANNELS 2
#define OPUS_MAX_FRAME_SAMPLES 960  // 20ms @ 48kHz
#define CHUNK_SAMPLES 480           // 10ms audio slice
#define RING_BUF_MASK 32767         // 32768 samples (Power of 2 for fast atomic wrapping)
#define RING_BUF_SIZE 32768

typedef struct {
    int16_t left;
    int16_t right;
} AudioSample;

static AudioSample g_ring_buf[RING_BUF_SIZE];
static volatile uint32_t g_write_pos = 0;
static volatile uint32_t g_read_pos = 0;

static int g_sock_fd = -1;
static volatile int g_running = 0;
static volatile int g_is_playing = 0;
static int g_prebuffer_samples = 1920; // 40ms default
static int g_low_cpu_mode = 1;
static int g_silence_cycles = 0;

static pthread_t g_rx_thread;
static int g_listen_port = 12345;

static SLObjectItf g_engineObj = NULL;
static SLEngineItf g_engine = NULL;
static SLObjectItf g_outputMixObj = NULL;
static SLObjectItf g_playerObj = NULL;
static SLPlayItf g_playerPlay = NULL;
static SLAndroidSimpleBufferQueueItf g_playerBufferQueue = NULL;

static AudioSample g_out_buf[2][CHUNK_SAMPLES];
static int g_out_buf_idx = 0;
static OpusDecoder *g_opus_decoder = NULL;

static inline uint32_t get_available_samples() {
    uint32_t w = g_write_pos;
    uint32_t r = g_read_pos;
    return (w >= r) ? (w - r) : (RING_BUF_SIZE - (r - w));
}

static void bqPlayerCallback(SLAndroidSimpleBufferQueueItf bq, void *context) {
    if (!g_running) return;

    AudioSample *dest = g_out_buf[g_out_buf_idx];
    g_out_buf_idx = 1 - g_out_buf_idx;

    uint32_t avail = get_available_samples();

    // If available audio drops below 1 chunk, enter underrun recovery
    if (avail < CHUNK_SAMPLES) {
        memset(dest, 0, CHUNK_SAMPLES * sizeof(AudioSample));
        g_is_playing = 0; // Halt and require re-buffering to target bufferMs
        if (!g_low_cpu_mode) {
            (*bq)->Enqueue(bq, dest, CHUNK_SAMPLES * sizeof(AudioSample));
        }
        return;
    }

    uint32_t r = g_read_pos;
    for (int i = 0; i < CHUNK_SAMPLES; i++) {
        dest[i] = g_ring_buf[(r + i) & RING_BUF_MASK];
    }
    g_read_pos = (r + CHUNK_SAMPLES) & RING_BUF_MASK;

    (*bq)->Enqueue(bq, dest, CHUNK_SAMPLES * sizeof(AudioSample));
}

static void trigger_playback() {
    if (g_is_playing || !g_playerBufferQueue) return;

    g_is_playing = 1;
    g_silence_cycles = 0;

    AudioSample *buf = g_out_buf[0];
    uint32_t r = g_read_pos;
    for (int i = 0; i < CHUNK_SAMPLES; i++) {
        buf[i] = g_ring_buf[(r + i) & RING_BUF_MASK];
    }
    g_read_pos = (r + CHUNK_SAMPLES) & RING_BUF_MASK;

    (*g_playerPlay)->SetPlayState(g_playerPlay, SL_PLAYSTATE_PLAYING);
    (*g_playerBufferQueue)->Enqueue(g_playerBufferQueue, buf, CHUNK_SAMPLES * sizeof(AudioSample));
}

static void *udp_receiver_thread(void *arg) {
    uint8_t rx_buf[2048];
    AudioSample decoded_pcm[OPUS_MAX_FRAME_SAMPLES];

    LOGI("UDP audio receiver active on port %d", g_listen_port);

    while (g_running) {
        ssize_t n = recvfrom(g_sock_fd, rx_buf, sizeof(rx_buf), 0, NULL, NULL);
        if (n <= 0) continue;

        int decoded_samples = opus_decode(
            g_opus_decoder,
            rx_buf,
            (opus_int32)n,
            (opus_int16 *)decoded_pcm,
            OPUS_MAX_FRAME_SAMPLES,
            0
        );

        if (decoded_samples > 0) {
            uint32_t w = g_write_pos;
            for (int i = 0; i < decoded_samples; i++) {
                g_ring_buf[(w + i) & RING_BUF_MASK] = decoded_pcm[i];
            }
            g_write_pos = (w + decoded_samples) & RING_BUF_MASK;

            if (!g_is_playing && get_available_samples() >= (uint32_t)g_prebuffer_samples) {
                trigger_playback();
            }
        }
    }
    return NULL;
}

static void stop_audio_engine() {
    if (!g_running && g_sock_fd < 0) return;

    g_running = 0;
    g_is_playing = 0;

    if (g_sock_fd >= 0) {
        shutdown(g_sock_fd, SHUT_RDWR);
        close(g_sock_fd);
        g_sock_fd = -1;
    }

    pthread_join(g_rx_thread, NULL);

    if (g_playerObj) {
        (*g_playerObj)->Destroy(g_playerObj);
        g_playerObj = NULL;
    }
    if (g_outputMixObj) {
        (*g_outputMixObj)->Destroy(g_outputMixObj);
        g_outputMixObj = NULL;
    }
    if (g_engineObj) {
        (*g_engineObj)->Destroy(g_engineObj);
        g_engineObj = NULL;
    }
    if (g_opus_decoder) {
        opus_decoder_destroy(g_opus_decoder);
        g_opus_decoder = NULL;
    }

    g_write_pos = 0;
    g_read_pos = 0;
    LOGI("Audio Engine stopped.");
}

// Add dynamic JNI method to update buffer size while streaming
JNIEXPORT void JNICALL
Java_com_example_opusplayer_NativeAudio_setBufferMs(JNIEnv *env, jclass clazz, jint bufferMs) {
    if (bufferMs < 10) bufferMs = 10;
    if (bufferMs > 50) bufferMs = 50;

    g_prebuffer_samples = (SAMPLE_RATE * bufferMs) / 1000;
    LOGI("Dynamic buffer updated to %d ms (%d samples)", bufferMs, g_prebuffer_samples);
}

JNIEXPORT jboolean JNICALL
Java_com_example_opusplayer_NativeAudio_startAudio(JNIEnv *env, jclass clazz, jint port, jboolean lowCpu, jint bufferMs) {
    if (g_running) return JNI_TRUE;

    // Enforce 5ms min and 50ms max buffer range
    if (bufferMs < 5) bufferMs = 5;
    if (bufferMs > 50) bufferMs = 50;

    // Disable low-CPU sleep mode under 10ms to prevent buffer underrun jitter
    if (bufferMs < 10) {
        lowCpu = JNI_FALSE;
    }

    g_listen_port = port;
    g_low_cpu_mode = lowCpu ? 1 : 0;
    g_prebuffer_samples = (SAMPLE_RATE * bufferMs) / 1000;

    int opus_err = 0;
    g_opus_decoder = opus_decoder_create(SAMPLE_RATE, CHANNELS, &opus_err);
    if (opus_err != OPUS_OK) {
        LOGE("Failed to init Opus decoder: %s", opus_strerror(opus_err));
        return JNI_FALSE;
    }

    slCreateEngine(&g_engineObj, 0, NULL, 0, NULL, NULL);
    (*g_engineObj)->Realize(g_engineObj, SL_BOOLEAN_FALSE);
    (*g_engineObj)->GetInterface(g_engineObj, SL_IID_ENGINE, &g_engine);

    (*g_engine)->CreateOutputMix(g_engine, &g_outputMixObj, 0, NULL, NULL);
    (*g_outputMixObj)->Realize(g_outputMixObj, SL_BOOLEAN_FALSE);

    SLDataLocator_AndroidSimpleBufferQueue loc_bufq = { SL_DATALOCATOR_ANDROIDSIMPLEBUFFERQUEUE, 2 };
    SLDataFormat_PCM format_pcm = {
        SL_DATAFORMAT_PCM,
        2,
        SL_SAMPLINGRATE_48,
        SL_PCMSAMPLEFORMAT_FIXED_16,
        SL_PCMSAMPLEFORMAT_FIXED_16,
        SL_SPEAKER_FRONT_LEFT | SL_SPEAKER_FRONT_RIGHT,
        SL_BYTEORDER_LITTLEENDIAN
    };

    SLDataSource audioSrc = { &loc_bufq, &format_pcm };
    SLDataLocator_OutputMix loc_outmix = { SL_DATALOCATOR_OUTPUTMIX, g_outputMixObj };
    SLDataSink audioSnk = { &loc_outmix, NULL };

    const SLInterfaceID ids[1] = { SL_IID_ANDROIDSIMPLEBUFFERQUEUE };
    const SLboolean req[1] = { SL_BOOLEAN_TRUE };

    (*g_engine)->CreateAudioPlayer(g_engine, &g_playerObj, &audioSrc, &audioSnk, 1, ids, req);
    (*g_playerObj)->Realize(g_playerObj, SL_BOOLEAN_FALSE);

    (*g_playerObj)->GetInterface(g_playerObj, SL_IID_PLAY, &g_playerPlay);
    (*g_playerObj)->GetInterface(g_playerObj, SL_IID_ANDROIDSIMPLEBUFFERQUEUE, &g_playerBufferQueue);

    (*g_playerBufferQueue)->RegisterCallback(g_playerBufferQueue, bqPlayerCallback, NULL);

    g_sock_fd = socket(AF_INET, SOCK_DGRAM, 0);
    int opt = 1;
    setsockopt(g_sock_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    int rcvbuf = 262144;
    setsockopt(g_sock_fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(g_listen_port);

    if (bind(g_sock_fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        LOGE("Socket bind failed: %s", strerror(errno));
        stop_audio_engine();
        return JNI_FALSE;
    }

    g_write_pos = 0;
    g_read_pos = 0;
    g_running = 1;
    g_is_playing = 0;

    pthread_create(&g_rx_thread, NULL, udp_receiver_thread, NULL);
    return JNI_TRUE;
}

JNIEXPORT void JNICALL
Java_com_example_opusplayer_NativeAudio_stopAudio(JNIEnv *env, jclass clazz) {
    stop_audio_engine();
}