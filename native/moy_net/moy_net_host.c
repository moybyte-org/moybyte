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
