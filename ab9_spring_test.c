// AB9 low-force centering-spring test.
// Uploads a standard Linux FF_SPRING effect for a bounded interval, then removes it.
#define _POSIX_C_SOURCE 200809L

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

static int device_fd = -1;
static int effect_id = -1;

static void sleep_ms(long milliseconds) {
    struct timespec delay = {
        .tv_sec = milliseconds / 1000,
        .tv_nsec = (milliseconds % 1000) * 1000000L,
    };
    while (nanosleep(&delay, &delay) < 0 && errno == EINTR) {}
}

static void stop_effect(void) {
    if (device_fd < 0 || effect_id < 0) return;
    struct input_event stop = { .type = EV_FF, .code = effect_id, .value = 0 };
    (void)write(device_fd, &stop, sizeof stop);
    (void)ioctl(device_fd, EVIOCRMFF, effect_id);
    effect_id = -1;
}

static void handle_signal(int sig) {
    stop_effect();
    if (device_fd >= 0) close(device_fd);
    _exit(128 + sig);
}

static int bit_is_set(const unsigned long *bits, unsigned int bit) {
    const unsigned int width = 8U * sizeof(unsigned long);
    return !!(bits[bit / width] & (1UL << (bit % width)));
}

static int find_ab9(char *path_out, size_t path_size) {
    DIR *dir = opendir("/dev/input");
    if (!dir) return -1;

    struct dirent *entry;
    while ((entry = readdir(dir))) {
        if (strncmp(entry->d_name, "event", 5) != 0) continue;

        char path[512];
        char name[256] = "";
        snprintf(path, sizeof path, "/dev/input/%s", entry->d_name);
        int fd = open(path, O_RDWR);
        if (fd < 0) continue;
        (void)ioctl(fd, EVIOCGNAME(sizeof name), name);
        if (strstr(name, "AB9")) {
            unsigned long bits[(FF_MAX / (8 * sizeof(unsigned long))) + 2];
            memset(bits, 0, sizeof bits);
            (void)ioctl(fd, EVIOCGBIT(EV_FF, sizeof bits), bits);
            if (!bit_is_set(bits, FF_SPRING)) {
                fprintf(stderr, "%s does not advertise FF_SPRING\n", name);
                close(fd);
                closedir(dir);
                return -1;
            }
            snprintf(path_out, path_size, "%s", path);
            printf("device: %s (%s), FF_SPRING supported\n", path, name);
            closedir(dir);
            return fd;
        }
        close(fd);
    }
    closedir(dir);
    return -1;
}

int main(int argc, char **argv) {
    if (argc < 2 || strcmp(argv[1], "--move-stick") != 0) {
        fprintf(stderr,
                "DANGER: this test activates a spring on a 12 N-m stick.\n"
                "Clear its full travel and run: %s --move-stick "
                "[strength 500..8000] [duration_ms 1000..15000]\n",
                argv[0]);
        return 2;
    }
    int strength = argc > 2 ? atoi(argv[2]) : 2000;
    int duration_ms = argc > 3 ? atoi(argv[3]) : 5000;
    if (strength < 500 || strength > 8000 || duration_ms < 1000 || duration_ms > 15000) {
        fprintf(stderr,
                "usage: %s --move-stick [strength 500..8000] "
                "[duration_ms 1000..15000]\n",
                argv[0]);
        return 2;
    }

    char path[512];
    device_fd = find_ab9(path, sizeof path);
    if (device_fd < 0) {
        fprintf(stderr, "AB9 force-feedback event device not found\n");
        return 1;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    struct ff_effect effect;
    memset(&effect, 0, sizeof effect);
    effect.type = FF_SPRING;
    effect.id = -1;
    effect.direction = 0;
    effect.replay.length = duration_ms;

    for (int axis = 0; axis < 2; axis++) {
        effect.u.condition[axis].right_saturation = strength;
        effect.u.condition[axis].left_saturation = strength;
        effect.u.condition[axis].right_coeff = strength;
        effect.u.condition[axis].left_coeff = strength;
        effect.u.condition[axis].deadband = 250;
        effect.u.condition[axis].center = 0;
    }

    if (ioctl(device_fd, EVIOCSFF, &effect) < 0) {
        perror("EVIOCSFF spring upload");
        close(device_fd);
        return 1;
    }
    effect_id = effect.id;

    struct input_event play = { .type = EV_FF, .code = effect_id, .value = 1 };
    if (write(device_fd, &play, sizeof play) != sizeof play) {
        perror("start spring");
        stop_effect();
        close(device_fd);
        return 1;
    }

    printf("LOW-FORCE SPRING ACTIVE: strength=%d, duration=%dms\n", strength, duration_ms);
    printf("Gently move the grip off center; it should resist and return.\n");
    fflush(stdout);
    sleep_ms(duration_ms);

    stop_effect();
    close(device_fd);
    device_fd = -1;
    printf("SPRING STOPPED AND REMOVED\n");
    return 0;
}
