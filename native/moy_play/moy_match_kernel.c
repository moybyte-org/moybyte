// The kernel's link (moy_match.h): the board's one instance over
// native/moy_net's ESP-NOW ring where the image has the link (MOY_NET_LINK),
// and none anywhere else. This file defines only that.

#include <stddef.h>
#include <stdint.h>

#include "moy_match.h"

#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "py/mpconfig.h"
#endif
#endif

#if defined(MOY_NET_LINK) && MOY_NET_LINK
#include "../moy_net/moy_link.h"

static int k_send(void *ctx, const uint8_t mac[6], const uint8_t *data, size_t n) {
    (void)ctx;
    return moy_link_send(mac, data, n);
}

static int k_recv(void *ctx, uint8_t mac[6], uint8_t *data, uint32_t cap) {
    (void)ctx;
    int n = moy_link_recv(mac, data, cap);
    return n < 0 ? -1 : n;
}

static int k_add_peer(void *ctx, const uint8_t mac[6]) {
    (void)ctx;
    return moy_link_add_peer(mac);
}

static const moy_match_io_t K_IO = {k_send, k_recv, k_add_peer, NULL, NULL, NULL};
#define K_HAS_LINK 1
#endif

#include "moy_loop.h"

static moy_match_t *g_kernel;

moy_match_t *moy_match_kernel(void) {
    return g_kernel;
}

moy_match_t *moy_match_kernel_make(const char *name, const char *board, uint32_t entropy) {
#ifdef K_HAS_LINK
    if (g_kernel == NULL) {
        moy_match_t *m = moy_loop_alloc(sizeof(moy_match_t));
        if (m == NULL) {
            return NULL;
        }
        moy_match_init(m, &K_IO, name, board, entropy);
        g_kernel = m;
    }
    return g_kernel;
#else
    (void)name;
    (void)board;
    (void)entropy;
    return NULL;
#endif
}

void moy_match_kernel_poll(uint32_t now) {
    if (g_kernel != NULL && g_kernel->active) {
        moy_match_poll(g_kernel, now);
    }
}
