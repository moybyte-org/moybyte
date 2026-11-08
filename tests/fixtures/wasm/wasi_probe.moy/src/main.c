// probe: one WASI preview1 behaviour a verb, for the host and on-glass tests
// of the compiled command tier (native/moy_wasm/moy_wasi.h).
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

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
