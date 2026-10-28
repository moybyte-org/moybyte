// Calls the probe through its C ABI under AddressSanitizer. argv[1] = "oob"
// drives the deliberate bug (built with `--features bug`).
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
int moy_rs_probe(int);
uint8_t moy_rs_probe_oob(const uint8_t *p, size_t n);
uint32_t moy_rs_probe_sum(const uint8_t *p, size_t n);

int main(int argc, char **argv) {
    uint8_t *buf = malloc(16);
    memset(buf, 1, 16);
    printf("probe=%d sum=%u\n", moy_rs_probe(41), moy_rs_probe_sum(buf, 16));
    if (argc > 1 && !strcmp(argv[1], "oob")) {
        printf("oob=%u\n", moy_rs_probe_oob(buf, 16));
    }
    free(buf);
    return 0;
}
