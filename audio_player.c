#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <opus/opus.h>
#include <SLES/OpenSLES.h>
#include <SLES/OpenSLES_Android.h>

#define SAMPLE_RATE 48000
#define CHANNELS 2
#define OPUS_FRAME_FRAMES 960     // 20ms @ 48kHz
#define RING_BUFFER_FRAMES 19200  // 400ms jitter buffer
#define CHUNK_FRAMES 480          // 10ms playback slice
#define RX_BUF_SIZE 4096

typedef struct {
    int16_t left;
    int16_t right;
} AudioFrame;

static AudioFrame g_ring_buffer[RING_BUFFER_FRAMES];
static volatile int g_write_frame = 0;
static volatile int g_read_frame = 0;
static pthread_mutex_t g_buf_mutex = PTHREAD_MUTEX_INITIALIZER;

static int g_sock_fd = -1;
static volatile int g_running = 1;

static SLObjectItf g_engineObj = NULL;
static SLEngineItf g_engine = NULL;
static SLObjectItf g_outputMixObj = NULL;
static SLObjectItf g_playerObj = NULL;
static SLPlayItf g_playerPlay = NULL;
static SLAndroidSimpleBufferQueueItf g_playerBufferQueue = NULL;

static AudioFrame g_play_buffers[2][CHUNK_FRAMES];
static int g_play_buf_idx = 0;
static OpusDecoder *g_opus_decoder = NULL;

static void ring_buffer_read(AudioFrame *dest, int frame_count) {
    pthread_mutex_lock(&g_buf_mutex);
    for (int i = 0; i < frame_count; i++) {
        if (g_read_frame == g_write_frame) {
            memset(&dest[i], 0, (frame_count - i) * sizeof(AudioFrame));
            pthread_mutex_unlock(&g_buf_mutex);
            return;
        }
        dest[i] = g_ring_buffer[g_read_frame];
        g_read_frame = (g_read_frame + 1) % RING_BUFFER_FRAMES;
    }
    pthread_mutex_unlock(&g_buf_mutex);
}

static void ring_buffer_write(const AudioFrame *src, int frame_count) {
    pthread_mutex_lock(&g_buf_mutex);
    for (int i = 0; i < frame_count; i++) {
        int next_w = (g_write_frame + 1) % RING_BUFFER_FRAMES;
        if (next_w == g_read_frame) {
            g_read_frame = (g_read_frame + 1) % RING_BUFFER_FRAMES;
        }
        g_ring_buffer[g_write_frame] = src[i];
        g_write_frame = next_w;
    }
    pthread_mutex_unlock(&g_buf_mutex);
}

static void bqPlayerCallback(SLAndroidSimpleBufferQueueItf bq, void *context) {
    if (!g_running) return;

    AudioFrame *buf = g_play_buffers[g_play_buf_idx];
    g_play_buf_idx = 1 - g_play_buf_idx;

    ring_buffer_read(buf, CHUNK_FRAMES);
    (*bq)->Enqueue(bq, buf, CHUNK_FRAMES * sizeof(AudioFrame));
}

static void cleanup_and_exit(int signum) {
    g_running = 0;
    if (g_playerObj) (*g_playerObj)->Destroy(g_playerObj);
    if (g_outputMixObj) (*g_outputMixObj)->Destroy(g_outputMixObj);
    if (g_engineObj) (*g_engineObj)->Destroy(g_engineObj);
    if (g_opus_decoder) opus_decoder_destroy(g_opus_decoder);
    if (g_sock_fd >= 0) close(g_sock_fd);
    _exit(0);
}

int main(int argc, char *argv[]) {
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    signal(SIGINT, cleanup_and_exit);
    signal(SIGTERM, cleanup_and_exit);
    signal(SIGHUP, cleanup_and_exit);

    int port = argc > 1 ? atoi(argv[1]) : 12345;

    int opus_err = 0;
    g_opus_decoder = opus_decoder_create(SAMPLE_RATE, CHANNELS, &opus_err);
    if (opus_err != OPUS_OK) {
        fprintf(stderr, "Failed to initialize Opus decoder: %s\n", opus_strerror(opus_err));
        return 1;
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

    int rcvbuf = 65536;
    setsockopt(g_sock_fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(port);

    if (bind(g_sock_fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        fprintf(stderr, "Socket bind failed: %s\n", strerror(errno));
        cleanup_and_exit(1);
    }

    printf("[AUDIO-OPUS-UDP] Listening on UDP port %d...\n", port);

    uint8_t rx_buf[RX_BUF_SIZE];
    AudioFrame decoded_pcm[OPUS_FRAME_FRAMES];

    while (g_running) {
        ssize_t n = recvfrom(g_sock_fd, rx_buf, RX_BUF_SIZE, 0, NULL, NULL);
        if (n <= 0) continue;

        int decoded_frames = opus_decode(
            g_opus_decoder,
            rx_buf,
            (opus_int32)n,
            (opus_int16 *)decoded_pcm,
            OPUS_FRAME_FRAMES,
            0
        );

        if (decoded_frames > 0) {
            ring_buffer_write(decoded_pcm, decoded_frames);
        }
    }

    cleanup_and_exit(0);
    return 0;
}