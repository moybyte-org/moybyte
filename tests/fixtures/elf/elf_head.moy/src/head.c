// head: the first lines of each file named, or of standard input. A plain
// POSIX program over stdio, getopt and strtol.
//
//   head [-n lines] [-q] [file ...]
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int head(FILE *f, long lines) {
    char buf[256];
    long seen = 0;
    while (seen < lines && fgets(buf, sizeof buf, f) != NULL) {
        fputs(buf, stdout);
        if (strchr(buf, '\n') != NULL) {
            seen++;
        }
    }
    return ferror(f) ? 1 : 0;
}

int main(int argc, char **argv) {
    long lines = 10;
    int quiet = 0, c, status = 0;
    while ((c = getopt(argc, argv, "n:q")) != -1) {
        if (c == 'n') {
            char *end;
            errno = 0;
            lines = strtol(optarg, &end, 10);
            if (*end != '\0' || errno == ERANGE || lines < 0) {
                fprintf(stderr, "head: invalid number of lines: '%s'\n", optarg);
                return 1;
            }
        } else if (c == 'q') {
            quiet = 1;
        } else {
            fprintf(stderr, "usage: head [-n lines] [-q] [file ...]\n");
            return 2;
        }
    }
    if (optind == argc) {
        return head(stdin, lines);
    }
    for (int i = optind; i < argc; i++) {
        FILE *f = fopen(argv[i], "r");
        if (f == NULL) {
            fprintf(stderr, "head: cannot open '%s' for reading: %s\n", argv[i], strerror(errno));
            status = 1;
            continue;
        }
        if (!quiet && argc - optind > 1) {
            printf("%s==> %s <==\n", i > optind ? "\n" : "", argv[i]);
        }
        status |= head(f, lines);
        fclose(f);
    }
    return status;
}
