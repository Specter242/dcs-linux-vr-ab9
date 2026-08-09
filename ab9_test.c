// AB9 FFB probe: play constant-force pulses and measure stick movement via ABS axes.
// A working motor moves the unheld stick, so axis deviation == physical force output.
#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <dirent.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <time.h>
#include <linux/input.h>

static long long now_ms(void) {
    struct timeval tv; gettimeofday(&tv, NULL);
    return (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void sleep_ms(long milliseconds) {
    struct timespec delay = {
        .tv_sec = milliseconds / 1000,
        .tv_nsec = (milliseconds % 1000) * 1000000L,
    };
    while (nanosleep(&delay, &delay) < 0 && errno == EINTR) {}
}

static int find_ab9(char *path_out, size_t path_size) {
    DIR *d = opendir("/dev/input");
    struct dirent *e;
    while (d && (e = readdir(d))) {
        if (strncmp(e->d_name, "event", 5)) continue;
        char path[512], name[256] = "";
        snprintf(path, sizeof path, "/dev/input/%s", e->d_name);
        int fd = open(path, O_RDWR);
        if (fd < 0) continue;
        ioctl(fd, EVIOCGNAME(sizeof name), name);
        if (strstr(name, "AB9")) {
            unsigned long ffbits[(FF_MAX + 1) / (8 * sizeof(unsigned long)) + 1];
            memset(ffbits, 0, sizeof ffbits);
            ioctl(fd, EVIOCGBIT(EV_FF, sizeof ffbits), ffbits);
            int has_const = !!(ffbits[FF_CONSTANT / (8 * sizeof(unsigned long))] &
                               (1UL << (FF_CONSTANT % (8 * sizeof(unsigned long)))));
            printf("device: %s (%s) FF_CONSTANT=%d\n", path, name, has_const);
            snprintf(path_out, path_size, "%s", path);
            closedir(d);
            return fd;
        }
        close(fd);
    }
    if (d) closedir(d);
    return -1;
}

static int get_abs(int fd, int axis) {
    struct input_absinfo ai;
    if (ioctl(fd, EVIOCGABS(axis), &ai) < 0) return -1;
    return ai.value;
}

int main(int argc, char **argv) {
    if (argc < 2 || strcmp(argv[1], "--move-stick") != 0) {
        fprintf(stderr,
                "DANGER: this test moves a 12 N-m stick in four directions.\n"
                "Clear its full travel and run: %s --move-stick [level 500..9830] "
                "[duration_ms 250..2000]\n",
                argv[0]);
        return 2;
    }
    int level = argc > 2 ? atoi(argv[2]) : 0x1000;   // ~12.5% default
    int dur_ms = argc > 3 ? atoi(argv[3]) : 750;
    if (level < 500 || level > 9830 || dur_ms < 250 || dur_ms > 2000) {
        fprintf(stderr,
                "usage: %s --move-stick [level 500..9830] [duration_ms 250..2000]\n",
                argv[0]);
        return 2;
    }

    char path[512];
    int fd = find_ab9(path, sizeof path);
    if (fd < 0) { fprintf(stderr, "AB9 event device not found\n"); return 1; }

    struct input_absinfo ax, ay;
    ioctl(fd, EVIOCGABS(ABS_X), &ax);
    ioctl(fd, EVIOCGABS(ABS_Y), &ay);
    printf("ABS_X range [%d..%d] start=%d | ABS_Y range [%d..%d] start=%d\n",
           ax.minimum, ax.maximum, ax.value, ay.minimum, ay.maximum, ay.value);

    // max gain
    struct input_event gain = { .type = EV_FF, .code = FF_GAIN, .value = 0xffff };
    if (write(fd, &gain, sizeof gain) != sizeof gain)
        perror("set FF_GAIN");
    else
        printf("FF_GAIN set to 0xffff\n");
    sleep_ms(100);

    static const struct { const char *name; unsigned dir; } dirs[] = {
        { "east (0x4000)",  0x4000 }, { "west (0xC000)",  0xC000 },
        { "north (0x0000)", 0x0000 }, { "south (0x8000)", 0x8000 },
    };

    int any_motion = 0;
    for (int i = 0; i < 4; i++) {
        struct ff_effect eff;
        memset(&eff, 0, sizeof eff);
        eff.type = FF_CONSTANT;
        eff.id = -1;
        eff.direction = dirs[i].dir;
        eff.replay.length = dur_ms;
        eff.u.constant.level = level;

        if (ioctl(fd, EVIOCSFF, &eff) < 0) { perror("EVIOCSFF"); return 1; }

        int x0 = get_abs(fd, ABS_X), y0 = get_abs(fd, ABS_Y);
        int xmin = x0, xmax = x0, ymin = y0, ymax = y0;

        struct input_event play = { .type = EV_FF, .code = eff.id, .value = 1 };
        if (write(fd, &play, sizeof play) != sizeof play) perror("play");
        printf("[%s] effect id=%d level=%d playing %dms... ", dirs[i].name, eff.id, level, dur_ms);
        fflush(stdout);

        long long t_end = now_ms() + dur_ms + 200;
        while (now_ms() < t_end) {
            int x = get_abs(fd, ABS_X), y = get_abs(fd, ABS_Y);
            if (x < xmin) xmin = x;
            if (x > xmax) xmax = x;
            if (y < ymin) ymin = y;
            if (y > ymax) ymax = y;
            sleep_ms(5);
        }

        struct input_event stop = { .type = EV_FF, .code = eff.id, .value = 0 };
        write(fd, &stop, sizeof stop);
        ioctl(fd, EVIOCRMFF, eff.id);

        int dx = xmax - xmin, dy = ymax - ymin;
        printf("deviation dx=%d dy=%d %s\n", dx, dy,
               (dx > 500 || dy > 500) ? "<-- MOTION" : "(no motion)");
        if (dx > 500 || dy > 500) any_motion = 1;
        sleep_ms(300);
    }

    printf("\nRESULT: %s\n", any_motion ? "PHYSICAL FORCE DETECTED" : "NO PHYSICAL FORCE");
    close(fd);
    return any_motion ? 0 : 2;
}
