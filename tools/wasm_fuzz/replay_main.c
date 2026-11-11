// The harnesses' main where libFuzzer's is not linked: each file named on
// the command line through LLVMFuzzerTestOneInput, its path printed first
// so a run that dies names the input it died on (tools/wasm_fuzz.py replay).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (f == NULL) {
            perror(argv[i]);
            return 2;
        }
        fseek(f, 0, SEEK_END);
        long n = ftell(f);
        fseek(f, 0, SEEK_SET);
        uint8_t *buf = malloc(n > 0 ? (size_t)n : 1);
        if (buf == NULL || fread(buf, 1, (size_t)n, f) != (size_t)n) {
            fprintf(stderr, "%s: could not read\n", argv[i]);
            return 2;
        }
        fclose(f);
        printf("replay %s\n", argv[i]);
        fflush(stdout);
        LLVMFuzzerTestOneInput(buf, (size_t)n);
        free(buf);
    }
    return 0;
}
