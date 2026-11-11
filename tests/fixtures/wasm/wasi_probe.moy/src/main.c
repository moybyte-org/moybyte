// probe: one WASI preview1 behaviour a verb, for the host and on-glass tests
// of the compiled command tier (native/moy_wasm/moy_wasi.h). `app` calls the
// files role's rows (moybyte.os, the store's named documents in the folder
// its `files` permission names): a document saved, counted, listed, loaded,
// renamed, loaded by its new name and deleted into the trash, and a kind
// outside its folder, each answer printed with its errno.
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "moybyte/os.h"

__attribute__((import_module("moybyte.term"), import_name("term_size")))
int term_size(unsigned *cols, unsigned *rows);

static int fail(const char *what) {
    printf("%s: %s\n", what, strerror(errno));
    return 2;
}

static int files(void) {
    if (mkdir("probe.d", 0755) != 0) {
        return fail("mkdir");
    }
    FILE *f = fopen("probe.d/a.txt", "w");
    if (f == NULL) {
        return fail("create");
    }
    fputs("one\ntwo\n", f);
    fclose(f);
    if (rename("probe.d/a.txt", "probe.d/b.txt") != 0) {
        return fail("rename");
    }
    f = fopen("probe.d/b.txt", "a");
    if (f == NULL) {
        return fail("append");
    }
    fputs("three\n", f);
    fclose(f);
    struct stat st;
    if (stat("probe.d/b.txt", &st) != 0) {
        return fail("stat");
    }
    printf("size %ld\n", (long)st.st_size);
    DIR *d = opendir("probe.d");
    if (d == NULL) {
        return fail("opendir");
    }
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (e->d_name[0] != '.') {
            printf("entry %s\n", e->d_name);
        }
    }
    closedir(d);
    f = fopen("probe.d/b.txt", "r");
    char line[64];
    fseek(f, 4, SEEK_SET);
    while (fgets(line, sizeof line, f) != NULL) {
        printf("read %s", line);
    }
    fclose(f);
    if (unlink("probe.d/b.txt") != 0) {
        return fail("unlink");
    }
    if (rmdir("probe.d") != 0) {
        return fail("rmdir");
    }
    printf("gone %s\n", access("probe.d", F_OK) != 0 ? "yes" : "no");
    return 0;
}

// `hostile CASE`: preview1's imports called raw, with the arguments a
// hostile module passes, each answer printed as its errno (a trap ends the
// program): `abs` an absolute path whose bytes go on past its length, `end`
// a path in the last byte of memory, `wrap` a pointer and length that wrap,
// `iov` an iovec array past the end of memory, `iovlen` a buffer that runs
// off it, `badfd` descriptors that were never opened, `prestat0` a name
// buffer of no length, `poll0` no subscriptions, `renum` a descriptor
// renumbered past the table.
#define WASI(name) __attribute__((import_module("wasi_snapshot_preview1"), import_name(#name)))
int32_t raw_path_open(int32_t fd, int32_t dirflags, int32_t path, int32_t path_len, int32_t oflags,
                      int64_t base, int64_t inheriting, int32_t fdflags, int32_t out)
    WASI(path_open);
int32_t raw_fd_write(int32_t fd, int32_t iovs, int32_t n, int32_t out) WASI(fd_write);
int32_t raw_fd_close(int32_t fd) WASI(fd_close);
int32_t raw_fd_prestat_get(int32_t fd, int32_t out) WASI(fd_prestat_get);
int32_t raw_fd_prestat_dir_name(int32_t fd, int32_t buf, int32_t n) WASI(fd_prestat_dir_name);
int32_t raw_poll_oneoff(int32_t in, int32_t out, int32_t n, int32_t nout) WASI(poll_oneoff);
int32_t raw_fd_renumber(int32_t fd, int32_t to) WASI(fd_renumber);

static char g_path[] = "/files/Documents/a.txt" "XYZ";

static int hostile(const char *how) {
    uint32_t end = (uint32_t)__builtin_wasm_memory_size(0) * 65536u;
    uint32_t got = 0, word[2];
    int32_t dir = -1;
    for (int32_t fd = 3; fd < 16 && dir < 0; fd++) {
        if (raw_fd_prestat_get(fd, (int32_t)(uintptr_t)word) == 0) {
            dir = fd;
        }
    }
    int64_t read = 1 << 1;
    if (strcmp(how, "abs") == 0) {
        printf("abs %d\n", (int)raw_path_open(dir, 0, (int32_t)(uintptr_t)g_path, 22, 0, read, 0,
                                               0, (int32_t)(uintptr_t)&got));
    } else if (strcmp(how, "end") == 0) {
        *(volatile char *)(uintptr_t)(end - 1) = '/';
        int32_t r = raw_path_open(dir, 0, (int32_t)(end - 1), 1, 0, read, 0, 0,
                                  (int32_t)(uintptr_t)&got);
        printf("end %s\n", r >= 0 ? "answered" : "?");
    } else if (strcmp(how, "wrap") == 0) {
        printf("wrap %d\n", (int)raw_path_open(dir, 0, (int32_t)0xfffffff0u, 0x20, 0, read, 0, 0,
                                                (int32_t)(uintptr_t)&got));
    } else if (strcmp(how, "iov") == 0) {
        printf("iov %d\n", (int)raw_fd_write(1, (int32_t)(end - 4), 1, (int32_t)(uintptr_t)&got));
    } else if (strcmp(how, "iovlen") == 0) {
        word[0] = end - 2;
        word[1] = 16;
        printf("iovlen %d\n", (int)raw_fd_write(1, (int32_t)(uintptr_t)word, 1,
                                                 (int32_t)(uintptr_t)&got));
    } else if (strcmp(how, "badfd") == 0) {
        printf("badfd %d %d\n", (int)raw_fd_write(77, (int32_t)(uintptr_t)word, 0,
                                                  (int32_t)(uintptr_t)&got),
               (int)raw_fd_close(-1));
    } else if (strcmp(how, "prestat0") == 0) {
        printf("prestat0 %d\n", (int)raw_fd_prestat_dir_name(dir, (int32_t)(uintptr_t)word, 0));
    } else if (strcmp(how, "poll0") == 0) {
        printf("poll0 %d\n", (int)raw_poll_oneoff((int32_t)(uintptr_t)word, (int32_t)(uintptr_t)word,
                                                   0, (int32_t)(uintptr_t)&got));
    } else if (strcmp(how, "renum") == 0) {
        printf("renum %d\n", (int)raw_fd_renumber(dir, 9999));
    } else {
        return 64;
    }
    return 0;
}

static void said(const char *what, int32_t r, char *buf) {
    if (r < 0) {
        printf("%s -1 errno %d\n", what, errno);
        return;
    }
    if (buf == NULL) {
        printf("%s %d\n", what, (int)r);
        return;
    }
    for (int32_t i = 0; i < r; i++) {
        if (buf[i] == 0 || buf[i] == '\n') {
            buf[i] = ',';
        }
    }
    printf("%s %.*s\n", what, (int)r, buf);
}

static int app(void) {
    char buf[128], name[64];
    said("files.ready", moy_files_ready(), NULL);
    said("files.save", moy_files_save("docs", "probe", "# probe\n", 8, name, sizeof name), name);
    said("files.count", moy_files_count("docs"), NULL);
    said("files.list", moy_files_list("docs", buf, sizeof buf), buf);
    said("files.load", moy_files_load("docs", "probe", buf, sizeof buf), buf);
    said("files.load.other", moy_files_load("drawings", "x", buf, sizeof buf), NULL);
    said("files.rename", moy_files_rename("docs", "probe", "Moved", name, sizeof name), name);
    said("files.load.moved", moy_files_load("docs", "moved", buf, sizeof buf), buf);
    said("files.delete", moy_files_delete("docs", "moved", buf, sizeof buf), buf);
    said("files.count.after", moy_files_count("docs"), NULL);
    return 0;
}

int main(int argc, char **argv) {
    const char *verb = argc > 1 ? argv[1] : "args";
    if (strcmp(verb, "args") == 0) {
        for (int i = 0; i < argc; i++) {
            printf("%d=%s\n", i, argv[i]);
        }
        return 0;
    }
    if (strcmp(verb, "env") == 0 && argc > 2) {
        const char *v = getenv(argv[2]);
        printf("%s\n", v != NULL ? v : "(unset)");
        return 0;
    }
    if (strcmp(verb, "exit") == 0 && argc > 2) {
        fprintf(stderr, "leaving with %s\n", argv[2]);
        exit(atoi(argv[2]));
    }
    if (strcmp(verb, "files") == 0) {
        return files();
    }
    if (strcmp(verb, "app") == 0) {
        return app();
    }
    if (strcmp(verb, "hostile") == 0 && argc > 2) {
        return hostile(argv[2]);
    }
    if (strcmp(verb, "open") == 0 && argc > 2) {
        FILE *f = fopen(argv[2], "r");
        if (f == NULL) {
            return fail("open");
        }
        fclose(f);
        printf("opened\n");
        return 0;
    }
    if (strcmp(verb, "stdin") == 0) {
        long bytes = 0, lines = 0;
        int c;
        while ((c = getchar()) != EOF) {
            bytes++;
            lines += c == '\n';
        }
        printf("%ld bytes %ld lines\n", bytes, lines);
        return 0;
    }
    if (strcmp(verb, "clock") == 0) {
        struct timespec a, b, r;
        clock_gettime(CLOCK_REALTIME, &r);
        clock_gettime(CLOCK_MONOTONIC, &a);
        usleep(30000);
        clock_gettime(CLOCK_MONOTONIC, &b);
        long ms = (b.tv_sec - a.tv_sec) * 1000 + (b.tv_nsec - a.tv_nsec) / 1000000;
        printf("since 2000 %s, slept %s\n", r.tv_sec >= 946684800 ? "yes" : "no",
               ms >= 25 ? "yes" : "no");
        return 0;
    }
    if (strcmp(verb, "random") == 0) {
        unsigned char b[32] = {0};
        getentropy(b, sizeof b);
        int nonzero = 0;
        for (size_t i = 0; i < sizeof b; i++) {
            nonzero += b[i] != 0;
        }
        printf("random %s\n", nonzero > 8 ? "yes" : "no");
        return 0;
    }
    if (strcmp(verb, "term") == 0) {
        unsigned cols = 0, rows = 0;
        int r = term_size(&cols, &rows);
        printf("%d %u %u\n", r, cols, rows);
        return 0;
    }
    if (strcmp(verb, "spin") == 0) {
        struct timespec t;
        for (;;) {
            clock_gettime(CLOCK_MONOTONIC, &t);
        }
    }
    if (strcmp(verb, "sleep") == 0) {
        for (;;) {
            usleep(20000);
        }
    }
    if (strcmp(verb, "trap") == 0) {
        __builtin_trap();
    }
    fprintf(stderr, "probe: no verb %s\n", verb);
    return 64;
}
