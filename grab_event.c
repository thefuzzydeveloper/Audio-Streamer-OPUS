#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <linux/input.h>
#include <sys/ioctl.h>

static int g_fd = -1;
static const char *g_dev_path = NULL;

static void cleanup_and_exit(int signum) {
    if (g_fd >= 0) {
        ioctl(g_fd, EVIOCGRAB, 0); // Release exclusive lock back to Android
        close(g_fd);
        g_fd = -1;
    }
    _exit(0);
}

int main(int argc, char *argv[]) {
    // Unbuffer stdout so Python receives data instantly without delays
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    g_dev_path = argc > 1 ? argv[1] : "/dev/input/event11";
    printf("[C-LOG] Starting grab_event on %s\n", g_dev_path);

    signal(SIGINT, cleanup_and_exit);
    signal(SIGTERM, cleanup_and_exit);
    signal(SIGHUP, cleanup_and_exit);
    signal(SIGPIPE, cleanup_and_exit);

    g_fd = open(g_dev_path, O_RDONLY);
    if (g_fd < 0) {
        printf("[C-LOG] FATAL: Failed to open %s: %s (errno=%d)\n", g_dev_path, strerror(errno), errno);
        return 1;
    }

    if (ioctl(g_fd, EVIOCGRAB, 1) < 0) {
        printf("[C-LOG] FATAL: ioctl(EVIOCGRAB) failed on %s: %s (errno=%d)\n", g_dev_path, strerror(errno), errno);
        close(g_fd);
        g_fd = -1;
        return 1;
    }

    printf("[C-LOG] SUCCESS: Exclusive grab locked on %s. Android OS input detached.\n", g_dev_path);

    struct input_event ev;
    while (read(g_fd, &ev, sizeof(struct input_event)) > 0) {
        if (ev.type == EV_KEY) {
            // Output raw event triplet: [type] [code] [value]
            printf("%u %u %d\n", ev.type, ev.code, ev.value);
        }
    }

    cleanup_and_exit(0);
    return 0;
}