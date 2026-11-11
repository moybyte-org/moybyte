// libc: the SDK's C library answering on stdout, a line a check, for the
// native tier's tests (tests/test_sdk_libc.py), which know each answer.
#include <ctype.h>
#include <errno.h>
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int cmp(const void *a, const void *b) {
    return *(const int *)a - *(const int *)b;
}

static void formats(void) {
    printf("[%d|%5d|%-5d|%05d|%+d|% d]\n", 42, 42, 42, 42, 42, 42);
    printf("[%u|%x|%X|%#x|%o|%#o]\n", 3000000000u, 255u, 255u, 255u, 8u, 8u);
    printf("[%lld|%llu|%lx]\n", -9000000000LL, 18000000000ULL, 0xdeadbeefUL);
    printf("[%s|%.3s|%8s|%-8s|%c]\n", "hello", "hello", "hi", "hi", 'z');
    printf("[%f|%.2f|%10.3f|%-10.1f|%.0f]\n", 3.14159, 2.675, -1.5, 0.25, 2.5);
    printf("[%e|%.3E|%g|%g|%g|%G]\n", 12345.678, 0.000123, 100000.0, 1000000.0, 0.0001, 1e-5);
    printf("[%g|%#g|%.10g|%g]\n", 1.5, 1.5, 1.0 / 3, 123456789.0);
    printf("[%.1f|%.1f|%.2f|%f]\n", 0.05, 0.15, 1.005, 1e20);
    printf("[%5.1f%%|%p]\n", 99.5, (void *)0);
    char buf[8];
    int n = snprintf(buf, sizeof buf, "%s", "truncated");
    printf("[snprintf %d %s]\n", n, buf);
}

static void numbers(void) {
    char *end;
    errno = 0;
    printf("strtol %ld %ld %ld %ld\n", strtol("  -123xyz", &end, 10), strtol("0x1f", NULL, 0),
           strtol("077", NULL, 0), strtol("zz", NULL, 36));
    printf("endptr %s\n", end);
    errno = 0;
    long big = strtol("99999999999999999999", NULL, 10);
    printf("range %d %d\n", big == 2147483647L || big == 9223372036854775807L, errno == ERANGE);
    printf("strtoul %lu atoi %d atol %ld\n", strtoul("4294967295", NULL, 10), atoi("17abc"),
           atol("-5"));
    printf("strtod %.4f %.1e %g\n", strtod("3.1416", NULL), strtod("-2.5e3", NULL),
           strtod("1e-3", NULL));
}

static void scans(void) {
    int a = 0, b = 0;
    char word[16] = "", rest[16] = "";
    float f = 0;
    double d = 0;
    unsigned x = 0;
    int got = sscanf("12 34 word 1.5 0x1f 2.25 rest!", "%d %d %15s %f %x %lf %[a-z]", &a, &b,
                     word, &f, &x, &d, rest);
    printf("sscanf %d %d %d %s %.2f %u %.2f %s\n", got, a, b, word, f, x, d, rest);
    printf("sscanf-eof %d\n", sscanf("", "%d", &a));
}

static void heap(void) {
    char *p[64];
    size_t total = 0;
    for (int i = 0; i < 64; i++) {
        size_t n = (size_t)(i * 37 % 500) + 1;
        p[i] = malloc(n);
        memset(p[i], i, n);
        total += n;
    }
    for (int i = 0; i < 64; i += 2) {
        free(p[i]);
    }
    char *big = malloc(200000);
    memset(big, 7, 200000);
    int ok = big[199999] == 7;
    for (int i = 1; i < 64; i += 2) {
        size_t n = (size_t)(i * 37 % 500) + 1;
        ok &= (unsigned char)p[i][n - 1] == (unsigned)i;
        p[i] = realloc(p[i], n * 3);
        ok &= (unsigned char)p[i][n - 1] == (unsigned)i;
        free(p[i]);
    }
    free(big);
    int *z = calloc(100, sizeof(int));
    for (int i = 0; i < 100; i++) {
        ok &= z[i] == 0;
    }
    free(z);
    char *s = strdup("dup");
    ok &= strcmp(s, "dup") == 0;
    free(s);
    printf("heap %d %d\n", ok, total > 0);
}

static void misc(int argc, char **argv) {
    int v[5] = { 5, 3, 9, 1, 7 };
    qsort(v, 5, sizeof v[0], cmp);
    printf("qsort %d %d %d %d %d\n", v[0], v[1], v[2], v[3], v[4]);
    printf("ctype %d %d %d %d %c %c\n", !!isdigit('7'), !!isalpha('7'), !!isspace('\t'),
           !!isupper('Q'), toupper('a'), tolower('B'));
    printf("strerror %s\n", strerror(ENOENT));
    int c, seen = 0;
    char *arg = NULL;
    while ((c = getopt(argc, argv, "ab:")) != -1) {
        if (c == 'a') {
            seen++;
        } else if (c == 'b') {
            arg = optarg;
        }
    }
    printf("getopt %d %s %d\n", seen, arg != NULL ? arg : "-", argc - optind);
    char *line = NULL;
    size_t cap = 0;
    ssize_t n = getline(&line, &cap, stdin);
    printf("getline %d %s", (int)n, n > 0 ? line : "\n");
    free(line);
    fprintf(stderr, "to stderr\n");
    puts("puts");
    putchar('!');
    putchar('\n');
}

int main(int argc, char **argv) {
    formats();
    numbers();
    scans();
    heap();
    misc(argc, argv);
    printf("unflushed at exit");
    return 0;
}
