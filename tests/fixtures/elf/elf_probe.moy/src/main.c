// probe: one POSIX row family per mode, its answers on stdout, for the
// native tier's tests (tests/test_elf_commands.py).
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "moybyte/os.h"

static void put(const char *s) {
    write(1, s, strlen(s));
}

static void num(long v) {
    char b[24];
    int i = sizeof b;
    int neg = v < 0;
    unsigned long u = neg ? (unsigned long)-v : (unsigned long)v;
    b[--i] = 0;
    do {
        b[--i] = (char)('0' + u % 10);
        u /= 10;
    } while (u);
    if (neg) {
        b[--i] = '-';
    }
    put(b + i);
}

static void line(const char *k, long v) {
    put(k);
    put(" ");
    num(v);
    put("\n");
}

// A decimal reader of its own: the probe exercises the rows, not the C
// library.
static int toint(const char *s) {
    int v = 0;
    while (*s >= '0' && *s <= '9') {
        v = v * 10 + (*s++ - '0');
    }
    return v;
}

static pthread_mutex_t g_m = PTHREAD_MUTEX_INITIALIZER;
static long g_count;

static void *bump(void *arg) {
    for (int i = 0; i < 1000; i++) {
        pthread_mutex_lock(&g_m);
        g_count++;
        pthread_mutex_unlock(&g_m);
    }
    return arg;
}

static void *nap(void *arg) {
    (void)arg;
    for (;;) {
        sleep(10);
    }
    return NULL;
}

static void *quit(void *arg) {
    exit((int)(long)arg);
}

static int files(const char *dir) {
    char p[256], q[256];
    strcpy(p, dir);
    strcat(p, "/x");
    line("mkdir", mkdir(p, 0755) == 0 ? 0 : errno);
    strcat(p, "/a");
    int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    line("open", fd >= 0 ? 0 : errno);
    line("write", write(fd, "native\n", 7));
    close(fd);
    struct stat st;
    line("stat", stat(p, &st) == 0 ? (long)st.st_size : -errno);
    line("isdir", stat(dir, &st) == 0 && S_ISDIR(st.st_mode));
    strcpy(q, dir);
    strcat(q, "/x/b");
    line("rename", rename(p, q) == 0 ? 0 : errno);
    strcpy(p, dir);
    strcat(p, "/x");
    DIR *d = opendir(p);
    struct dirent *e;
    while (d != NULL && (e = readdir(d)) != NULL) {
        put("entry ");
        put(e->d_name);
        put("\n");
    }
    closedir(d);
    fd = open(q, O_RDONLY);
    char buf[16] = {0};
    line("read", read(fd, buf, sizeof buf - 1));
    put(buf);
    close(fd);
    line("unlink", unlink(q) == 0 ? 0 : errno);
    line("rmdir", rmdir(p) == 0 ? 0 : errno);
    return 0;
}

int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "";
    const char *arg = argc > 2 ? argv[2] : "";
    if (strcmp(mode, "env") == 0) {
        const char *v = getenv(arg);
        put(v != NULL ? v : "(unset)");
        put("\n");
    } else if (strcmp(mode, "cwd") == 0) {
        char b[256];
        put(getcwd(b, sizeof b) != NULL ? b : "(none)");
        put("\n");
    } else if (strcmp(mode, "term") == 0) {
        int c = 0, r = 0;
        line("term", moy_term_size(&c, &r) == 0 && c > 0 && r > 0);
    } else if (strcmp(mode, "time") == 0) {
        struct timespec ts;
        line("mono", clock_gettime(CLOCK_MONOTONIC, &ts) == 0 && (ts.tv_sec > 0 || ts.tv_nsec > 0));
        line("wall", time(NULL) > 0);
    } else if (strcmp(mode, "files") == 0) {
        return files(arg);
    } else if (strcmp(mode, "deny") == 0) {
        int fd = open(arg, O_RDONLY);
        line("open", fd >= 0 ? 0 : errno == EACCES ? -1 : errno);
    } else if (strcmp(mode, "exit") == 0) {
        exit(toint(arg));
    } else if (strcmp(mode, "texit") == 0) {
        pthread_t t;
        pthread_create(&t, NULL, quit, (void *)(long)toint(arg));
        for (;;) {
            usleep(1000);
        }
    } else if (strcmp(mode, "threads") == 0) {
        pthread_t t[4];
        for (int i = 0; i < 4; i++) {
            if (pthread_create(&t[i], NULL, bump, (void *)(long)i) != 0) {
                put("create failed\n");
                return 1;
            }
        }
        long sum = 0;
        for (int i = 0; i < 4; i++) {
            void *out;
            pthread_join(t[i], &out);
            sum += (long)out;
        }
        line("count", g_count);
        line("joined", sum);
    } else if (strcmp(mode, "leak") == 0) {
        int fd = open(arg, O_WRONLY | O_CREAT, 0644);
        DIR *d = opendir(".");
        pthread_mutex_t m;
        pthread_mutex_init(&m, NULL);
        pthread_t t;
        pthread_create(&t, NULL, nap, NULL);
        pthread_detach(t);
        line("held", fd >= 0 && d != NULL);
        return 3;
    } else if (strcmp(mode, "sleep") == 0) {
        for (;;) {
            usleep(20000);
        }
    } else if (strcmp(mode, "stdin") == 0) {
        char b[64];
        ssize_t n;
        while ((n = read(0, b, sizeof b)) > 0) {
            write(1, b, (size_t)n);
        }
    } else {
        put("probe: no mode\n");
        return 2;
    }
    return 0;
}
