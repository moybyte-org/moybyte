// What the AOT fuzz targets link beside WAMR and moy_ota.c: the host's
// allocation and hash, and a native-call entry that never runs code.
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include "moy_ota.h"
void *moy_net_alloc(size_t n) { return malloc(n); }
void moy_net_free(void *p) { free(p); }
void moy_net_sha256(const void *p, size_t n, uint8_t out[32])
{
    moy_sha256_t h;
    moy_sha256_init(&h);
    moy_sha256_update(&h, p, n);
    moy_sha256_final(&h, out);
}
void invokeNative(void) { abort(); }
