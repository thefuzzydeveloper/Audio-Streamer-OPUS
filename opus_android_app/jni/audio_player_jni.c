#include <jni.h>
#include <android/log.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <sys/resource.h>
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
#define RING_BUFFER_SIZE 192000     // 4000ms buffer capacity

typedef struct {
    int16_t left;
    int16_t right;
} AudioSample;

static AudioSample g_ring_buf[RING_BUFFER_SIZE];
static int g_write_idx = 0;
static int g_read_idx = 0;
static int g_buffered_samples = 0;
static pthread_mutex_t g_mutex = PTHREAD_MUTEX_INITIALIZER;

static int g_sock_fd = -1;
static volatile int g_running = 0;
static volatile int g_prebuffered = 0;

// Increased default baseline buffer to 60ms to absorb Wi-Fi roaming jitter
static int g_target_buffer_samples = 2880; // 60ms @ 48kHz
static int g_max_drift_samples = 5760;     // 120ms max drift limit

static pthread_t g_rx_thread;
static int g_listen_port = 12345;

static SLObjectItf g_engineObj = NULL;
static SLEngineItf g_engine = NULL;
static SLObjectItf g_outputMixObj = NULL;
static SLObjectItf g_playerObj = NULL;
static SLPlayItf g_playerPlay = NULL;
static SLAndroidSimpleBufferQueueItf g_playerBufferQueue = NULL;

static AudioSample g_play_buffers[2][CHUNK_SAMPLES];
static int g_play_buf_idx = 0;
static OpusDecoder *g_opus_decoder = NULL;

// Packet sequence tracking for Loss Concealment (PLC)
static int g_has_first_seq = 0;
static uint16_t g_last_seq = 0;

static void ring_buffer_write_locked(const AudioSample *src, int count) {
    int max_allowed = g_target_buffer_samples + g_max_drift_samples;
    if (max_allowed > RING_BUFFER_SIZE) max_allowed = RING_BUFFER_SIZE;

    // Smoothly advance read index if network burst exceeds max drift headroom
    while (g_buffered_samples + count > max_allowed) {
        g_read_idx = (g_read_idx + count) % RING_BUFFER_SIZE;
        g_buffered_samples -= count;
        if (g_buffered_samples < 0) g_buffered_samples = 0;
    }

    for (int i = 0; i < count; i++) {
        g_ring_buf[g_write_idx] = src[i];
        g_write_idx = (g_write_idx + 1) % RING_BUFFER_SIZE;
    }
    g_buffered_samples += count;

    if (!g_prebuffered && g_buffered_samples >= g_target_buffer_samples) {
        g_prebuffered = 1;
    }
}

static void ring_buffer_write(const AudioSample *src, int count) {
    pthread_mutex_lock(&g_mutex);
    ring_buffer_write_locked(src, count);
    pthread_mutex_unlock(&g_mutex);
}

static void ring_buffer_read(AudioSample *dest, int count) {
    pthread_mutex_lock(&g_mutex);

    // Initial buffering / rebuffering gate
    if (!g_prebuffered) {
        memset(dest, 0, count * sizeof(AudioSample));
        pthread_mutex_unlock(&g_mutex);
        return;
    }

    // Normal continuous playback path
    if (g_buffered_samples >= count) {
        for (int i = 0; i < count; i++) {
            dest[i] = g_ring_buf[g_read_idx];
            g_read_idx = (g_read_idx + 1) % RING_BUFFER_SIZE;
        }
        g_buffered_samples -= count;
        pthread_mutex_unlock(&g_mutex);
        return;
    }

    // Underrun condition: drain remaining samples with smooth fade-out
    int available = g_buffered_samples;
    for (int i = 0; i < available; i++) {
        dest[i] = g_ring_buf[g_read_idx];
        g_read_idx = (g_read_idx + 1) % RING_BUFFER_SIZE;
    }

    if (available > 0) {
        for (int i = 0; i < available; i++) {
            float gain = 1.0f - ((float)i / (float)available);
            dest[i].left = (int16_t)(dest[i].left * gain);
            dest[i].right = (int16_t)(dest[i].right * gain);
        }
    }
    memset(&dest[available], 0, (count - available) * sizeof(AudioSample));
    g_buffered_samples = 0;

    // Immediately reset prebuffer flag to stop oscillation and wait for cushion
    g_prebuffered = 0;

    pthread_mutex_unlock(&g_mutex);
}

static void bqPlayerCallback(SLAndroidSimpleBufferQueueItf bq, void *context) {
    if (!g_running) return;

    AudioSample *buf = g_play_buffers[g_play_buf_idx];
    g_play_buf_idx = 1 - g_play_buf_idx;

    ring_buffer_read(buf, CHUNK_SAMPLES);
    (*bq)->Enqueue(bq, buf, CHUNK_SAMPLES * sizeof(AudioSample));
}

static void *udp_receiver_thread(void *arg) {
    setpriority(PRIO_PROCESS, 0, -19); // Real-time priority

    uint8_t rx_buf[2048];
    AudioSample decoded_pcm[OPUS_MAX_FRAME_SAMPLES];

    LOGI("Audio UDP receiver listening on port %d", g_listen_port);

    while (g_running) {
        ssize_t n = recvfrom(g_sock_fd, rx_buf, sizeof(rx_buf), 0, NULL, NULL);
        if (n <= 2) {
            continue;
        }

        // Extract 2-byte big-endian sequence number
        uint16_t seq = ((uint16_t)rx_buf[0] << 8) | (uint16_t)rx_buf[1];
        uint8_t *opus_payload = rx_buf + 2;
        opus_int32 payload_len = (opus_int32)(n - 2);

        pthread_mutex_lock(&g_mutex);

        if (!g_has_first_seq) {
            g_has_first_seq = 1;
            g_last_seq = seq;
        } else {
            int diff = (int)seq - (int)g_last_seq;
            if (diff < 0) diff += 65536;

            // Handle packet loss using Opus Packet Loss Concealment (PLC)
            if (diff > 1 && diff <= 5) {
                for (int lost = 1; lost < diff; lost++) {
                    int plc_samples = opus_decode(
                        g_opus_decoder,
                        NULL,
                        0,
                        (opus_int16 *)decoded_pcm,
                        OPUS_MAX_FRAME_SAMPLES,
                        0
                    );
                    if (plc_samples > 0) {
                        ring_buffer_write_locked(decoded_pcm, plc_samples);
                    }
                }
            }
            g_last_seq = seq;
        }

        // Decode actual Opus frame
        int decoded_samples = opus_decode(
            g_opus_decoder,
            opus_payload,
            payload_len,
            (opus_int16 *)decoded_pcm,
            OPUS_MAX_FRAME_SAMPLES,
            0
        );

        if (decoded_samples > 0) {
            ring_buffer_write_locked(decoded_pcm, decoded_samples);
        }

        pthread_mutex_unlock(&g_mutex);
    }
    return NULL;
}

static void stop_audio_engine() {
    if (!g_running && g_sock_fd < 0) return;

    g_running = 0;
    g_prebuffered = 0;

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

    pthread_mutex_lock(&g_mutex);
    g_write_idx = 0;
    g_read_idx = 0;
    g_buffered_samples = 0;
    g_has_first_seq = 0;
    pthread_mutex_unlock(&g_mutex);

    LOGI("Audio engine stopped cleanly.");
}

JNIEXPORT jboolean JNICALL
Java_com_example_opusplayer_NativeAudio_startAudio(JNIEnv *env, jclass clazz, jint port, jboolean lowCpu, jint bufferMs) {
    if (g_running) return JNI_TRUE;

    g_listen_port = port;

    if (bufferMs < 30) bufferMs = 60; // 60ms recommended minimum baseline for Wi-Fi
    if (bufferMs > 1000) bufferMs = 1000;

    g_target_buffer_samples = (SAMPLE_RATE * bufferMs) / 1000;
    g_max_drift_samples = (SAMPLE_RATE * (bufferMs > 100 ? bufferMs : 100)) / 1000;

    int opus_err = 0;
    g_opus_decoder = opus_decoder_create(SAMPLE_RATE, CHANNELS, &opus_err);
    if (opus_err != OPUS_OK) {
        LOGE("Opus decoder creation failed: %s", opus_strerror(opus_err));
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
    (*g_playerPlay)->SetPlayState(g_playerPlay, SL_PLAYSTATE_PLAYING);

    memset(g_play_buffers[0], 0, sizeof(g_play_buffers[0]));
    memset(g_play_buffers[1], 0, sizeof(g_play_buffers[1]));
    (*g_playerBufferQueue)->Enqueue(g_playerBufferQueue, g_play_buffers[0], sizeof(g_play_buffers[0]));
    (*g_playerBufferQueue)->Enqueue(g_playerBufferQueue, g_play_buffers[1], sizeof(g_play_buffers[1]));

    g_sock_fd = socket(AF_INET, SOCK_DGRAM, 0);
    int opt = 1;
    setsockopt(g_sock_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    int rcvbuf = 2097152; // 2MB socket buffer
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

    g_write_idx = 0;
    g_read_idx = 0;
    g_buffered_samples = 0;
    g_prebuffered = 0;
    g_has_first_seq = 0;
    g_running = 1;

    pthread_create(&g_rx_thread, NULL, udp_receiver_thread, NULL);
    return JNI_TRUE;
}

JNIEXPORT void JNICALL
Java_com_example_opusplayer_NativeAudio_setBufferMs(JNIEnv *env, jclass clazz, jint bufferMs) {
    if (bufferMs < 30) bufferMs = 60;
    if (bufferMs > 1000) bufferMs = 1000;

    pthread_mutex_lock(&g_mutex);
    g_target_buffer_samples = (SAMPLE_RATE * bufferMs) / 1000;
    g_max_drift_samples = (SAMPLE_RATE * (bufferMs > 100 ? bufferMs : 100)) / 1000;
    pthread_mutex_unlock(&g_mutex);
}

JNIEXPORT void JNICALL
Java_com_example_opusplayer_NativeAudio_stopAudio(JNIEnv *env, jclass clazz) {
    stop_audio_engine();
}