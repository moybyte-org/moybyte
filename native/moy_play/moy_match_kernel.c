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

static int k_channel(void *ctx, int set) {
    (void)ctx;
    return moy_link_channel(set);
}

static const moy_match_io_t K_IO = {k_send, k_recv, k_add_peer, NULL, k_channel, NULL, NULL};
#define K_HAS_LINK 1
#endif

#include "moy_chrome.h"
#include "moy_loop.h"
#include "moy_perf.h"
#include "moy_str.h"

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

// The match's desync, said over whatever screen is up -- a run the kernel
// drives included, whose frames the console's own notice never reaches.
static int32_t g_said = -1;
static uint8_t g_said_session;

void moy_match_kernel_poll(uint32_t now) {
    moy_match_t *m = g_kernel;
    if (m == NULL || !m->active) {
        return;
    }
    moy_match_poll(m, now);
    if (m->s.live && m->s.desync >= 0
            && (g_said != m->s.desync || g_said_session != m->s.session)) {
        g_said = m->s.desync;
        g_said_session = m->s.session;
        moy_chrome_notice(moy_str(MOY_S_NOTICE_OUT_OF_SYNC),
                          moy_str(MOY_S_NOTICE_OUT_OF_SYNC_WHY), false, now + 8000u);
    }
}

static uint32_t g_pf_at, g_pf_late, g_pf_stall;
static int32_t g_pf_slew;
static uint8_t g_pf_session;
static bool g_pf_has;

void moy_match_kernel_perf(void *values, uint32_t now) {
    moy_match_t *m = g_kernel;
    if (m == NULL || !m->s.live) {
        g_pf_has = false;
        return;
    }
    moy_perf_values_t *v = values;
    const moy_lockstep_t *s = &m->s;
    moy_perf_set(v, MOY_PF_NET, 0, (double)moy_lockstep_tps(m, now));
    if (g_pf_has && g_pf_session == s->session && now != g_pf_at) {
        double ms = (double)(uint32_t)(now - g_pf_at);
        moy_perf_set(v, MOY_PF_LOCK, 0, (double)(uint32_t)(s->late_ms - g_pf_late) * 1000.0 / ms);
        moy_perf_set(v, MOY_PF_LOCK, 1, (double)(uint32_t)(s->stall_ms - g_pf_stall) * 1000.0 / ms);
        moy_perf_set(v, MOY_PF_LOCK, 2, (double)(s->slew_ms - g_pf_slew) * 1000.0 / ms);
    }
    uint32_t hz = s->tick_hz ? s->tick_hz : MOY_MATCH_TICK_HZ;
    moy_perf_set(v, MOY_PF_LOCK, 3, (double)((uint32_t)s->delay * 1000u / hz));
    if (s->has_ema) {
        moy_perf_set(v, MOY_PF_LOCK, 4, (double)s->m_ema);
    }
    g_pf_has = true;
    g_pf_session = s->session;
    g_pf_at = now;
    g_pf_late = s->late_ms;
    g_pf_stall = s->stall_ms;
    g_pf_slew = s->slew_ms;
}
