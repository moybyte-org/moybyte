// The host build's shim for runtime/net_binding.py: what moy_net.h keeps
// inline, exported for ctypes.

#include "moy_net.h"

int moy_net_host_use_stored(size_t password_n, size_t stored_n) {
    return moy_wifi_use_stored(password_n, stored_n);
}

int moy_net_host_remember(int ok, const char *password, size_t password_n,
                          const char *stored, size_t stored_n) {
    return moy_wifi_remember(ok, password, password_n, stored, stored_n);
}

// -- what the image links beside the webhost, standing in for its tests ----------

#include <stdbool.h>
#include <stdio.h>

// The baked bundle, native/moy_web's table (moy_web_blob.h declares it const:
// the image's is generated rodata, this one a test sets): empty by default.
typedef struct _moy_web_asset_t {
    const char *name;
    const unsigned char *data;
    unsigned int len;
} moy_web_asset_t;

moy_web_asset_t moy_web_assets[8];
unsigned int moy_web_asset_count;
char moy_web_stamp[64];

void moy_net_host_bake(unsigned i, const char *name, const unsigned char *data,
                       unsigned len, unsigned count, const char *stamp) {
    if (i < 8) {
        moy_web_assets[i].name = name;
        moy_web_assets[i].data = data;
        moy_web_assets[i].len = len;
    }
    moy_web_asset_count = count < 8 ? count : 8;
    snprintf(moy_web_stamp, sizeof(moy_web_stamp), "%s", stamp ? stamp : "");
}

// The panel feeder's drain (native/moy_flush), counted: every store touch the
// webhost makes waits out a band in flight first.
long moy_net_host_drains;

bool moy_flush_drain(void) {
    moy_net_host_drains++;
    return true;
}

// The companion radio's sink, standing in for native/p4/moy_c6: off unless a
// test turns it on; it records what it was given.
#include <stdlib.h>
#include <string.h>

int moy_net_host_c6_on;
int moy_net_host_c6_fail_at = -1;
int moy_net_host_c6_ver = -1;
uint8_t *moy_net_host_c6_data;
uint32_t moy_net_host_c6_n;
int moy_net_host_c6_ended, moy_net_host_c6_active, moy_net_host_c6_writes;

int moy_c6_ota_begin(void) {
    if (!moy_net_host_c6_on) {
        return 0x103;
    }
    free(moy_net_host_c6_data);
    moy_net_host_c6_data = malloc(4u << 20);
    moy_net_host_c6_n = 0;
    moy_net_host_c6_ended = 0;
    moy_net_host_c6_active = 0;
    moy_net_host_c6_writes = 0;
    return 0;
}

int moy_c6_ota_write(const void *p, size_t n) {
    if (moy_net_host_c6_writes++ == moy_net_host_c6_fail_at || n > 1500
        || moy_net_host_c6_n + n > (4u << 20)) {
        return 0x103;
    }
    memcpy(moy_net_host_c6_data + moy_net_host_c6_n, p, n);
    moy_net_host_c6_n += (uint32_t)n;
    return 0;
}

int moy_c6_ota_end(void) {
    moy_net_host_c6_ended = 1;
    return 0;
}

int moy_c6_ota_activate(void) {
    moy_net_host_c6_active = 1;
    return 0;
}

int moy_c6_version(void) {
    return moy_net_host_c6_ver;
}
