// cat: each file named, or stdin, to stdout. A plain C program, built by
// wasi-sdk as any program is (tools/wasm_cart.py).
#include <errno.h>
#include <stdio.h>
#include <string.h>

static int cat(FILE *f) {
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) {
        if (fwrite(buf, 1, n, stdout) != n) {
            return 1;
        }
    }
    return ferror(f) ? 1 : 0;
}

int main(int argc, char **argv) {
    int status = 0;
    if (argc < 2) {
        return cat(stdin);
    }
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-") == 0) {
            status |= cat(stdin);
            continue;
        }
        FILE *f = fopen(argv[i], "rb");
        if (f == NULL) {
            fprintf(stderr, "cat: %s: %s\n", argv[i], strerror(errno));
            status = 1;
            continue;
        }
        status |= cat(f);
        fclose(f);
    }
    return status;
}
