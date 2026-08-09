// Poll AB9 PID State input report (0x12) and PID Pool feature (0x23) via hidraw.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <sys/ioctl.h>
#include <linux/hidraw.h>

static int find_ab9_hidraw(char *path_out, size_t path_size) {
    DIR *d = opendir("/dev");
    struct dirent *e;
    while (d && (e = readdir(d))) {
        if (strncmp(e->d_name, "hidraw", 6)) continue;
        char path[512], name[256] = "";
        snprintf(path, sizeof path, "/dev/%s", e->d_name);
        int fd = open(path, O_RDWR);
        if (fd < 0) continue;
        ioctl(fd, HIDIOCGRAWNAME(sizeof name), name);
        if (strstr(name, "AB9")) {
            printf("hidraw: %s (%s)\n", path, name);
            snprintf(path_out, path_size, "%s", path);
            closedir(d);
            return fd;
        }
        close(fd);
    }
    if (d) closedir(d);
    return -1;
}

int main(void) {
    char path[512];
    int fd = find_ab9_hidraw(path, sizeof path);
    if (fd < 0) { fprintf(stderr, "AB9 hidraw not found\n"); return 1; }

    unsigned char buf[64];

    // PID State input report 0x12: [id][effect block idx][state bits]
    memset(buf, 0, sizeof buf);
    buf[0] = 0x12;
    int r = ioctl(fd, HIDIOCGINPUT(3), buf);
    if (r < 0) perror("GET_INPUT 0x12");
    else {
        printf("PID State (0x12), %d bytes:", r);
        for (int i = 0; i < r; i++) printf(" %02x", buf[i]);
        unsigned s = buf[2];
        printf("\n  block index      : %u\n", buf[1]);
        printf("  device paused    : %u\n", (s >> 0) & 1);
        printf("  actuators enabled: %u\n", (s >> 1) & 1);
        printf("  safety switch    : %u\n", (s >> 2) & 1);
        printf("  actuator power   : %u\n", (s >> 3) & 1);
        printf("  effect playing   : %u\n", (s >> 4) & 1);
    }

    // PID Pool feature 0x23: [id][ram pool sz x2][simultaneous max][managed/shared bits]
    memset(buf, 0, sizeof buf);
    buf[0] = 0x23;
    r = ioctl(fd, HIDIOCGFEATURE(6), buf);
    if (r < 0) perror("GET_FEATURE 0x23");
    else {
        printf("PID Pool (0x23), %d bytes:", r);
        for (int i = 0; i < r; i++) printf(" %02x", buf[i]);
        printf("\n  ram pool size    : %u\n", buf[1] | (buf[2] << 8));
        printf("  simultaneous max : %u\n", buf[3]);
        printf("  managed/shared   : %02x\n", buf[4]);
    }

    // Block Load feature 0x22
    memset(buf, 0, sizeof buf);
    buf[0] = 0x22;
    r = ioctl(fd, HIDIOCGFEATURE(5), buf);
    if (r < 0) perror("GET_FEATURE 0x22");
    else {
        printf("Block Load (0x22), %d bytes:", r);
        for (int i = 0; i < r; i++) printf(" %02x", buf[i]);
        printf("\n");
    }
    close(fd);
    return 0;
}
