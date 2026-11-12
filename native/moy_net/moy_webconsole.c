// The web-console switch (moy_net.h): off, joining the link, serving, saying
// goodbye. The webhost (moy_webhost.c) is the server; this is the mode the
// Settings row and the dev channel's `web` turn on and off, and it never
// waits: a start configures the webhost, dials the network the WiFi driver
// kept when the link is down, and returns, and each poll brings the switch
// one step on -- a bound listener once the link has an address, a failure
// once MOY_WC_JOIN_MS has passed without one. Its state is the kernel's, so a
// soft reset leaves a serving console serving.
//
// The link is the kernel's WiFi driver where the image has one
// (moy_net_link); elsewhere (the Zero, the host) the caller names the address
// it serves on.

#include <stdio.h>
#include <string.h>

#include "moy_net.h"
#include "moy_seal.h"

#pragma weak moy_net_link
#pragma weak moy_wifi_connect_kept

typedef struct {
    moy_wc_state_t st;
    uint32_t since;                 // the join's start, moy_net_ms
    uint32_t given_ip;              // the caller's address, where there is no driver
} wc_t;

static wc_t *s_wc;

static int ready(void) {
    if (s_wc == NULL) {
        s_wc = moy_net_alloc(sizeof(wc_t));
    }
    return s_wc != NULL;
}

// The caller's address when it named one, else the kernel's link.
static int link_up(uint32_t *ip) {
    if (s_wc->given_ip != 0 || moy_net_link == NULL) {
        *ip = s_wc->given_ip;
        return *ip != 0;
    }
    return moy_net_link(ip);
}

int moy_wc_on(const moy_web_cfg_t *cfg, uint32_t ip) {
    if (!ready()) {
        return 12;                  // ENOMEM
    }
    moy_web_state_t ws;
    moy_web_state(&ws);
    if (s_wc->st.state == MOY_WC_SERVING && ws.listening) {
        int rc = moy_web_start(cfg);    // a new configuration, the socket kept or moved
        if (rc == 0) {
            s_wc->st.port = cfg->port;
            if (ip != 0) {
                s_wc->given_ip = ip;
                s_wc->st.ip = ip;
            }
        }
        return rc;
    }
    moy_web_cfg_t c = *cfg;
    c.port = 0;                     // configure only: the bind waits for the link
    int rc = moy_web_start(&c);
    if (rc != 0) {
        return rc;
    }
    s_wc->st.state = MOY_WC_JOINING;
    s_wc->st.port = cfg->port;
    s_wc->st.err = 0;
    s_wc->st.ip = 0;
    s_wc->st.dialled = 0;
    s_wc->given_ip = ip;
    s_wc->since = moy_net_ms();
    uint32_t now_ip;
    if (!link_up(&now_ip) && moy_wifi_connect_kept != NULL) {
        s_wc->st.dialled = moy_wifi_connect_kept() == 0;
    }
    moy_wc_poll();
    return 0;
}

void moy_wc_off(const char *why) {
    if (!ready()) {
        return;
    }
    switch (s_wc->st.state) {
        case MOY_WC_SERVING:
            moy_web_stop(why);
            s_wc->st.state = why != NULL ? MOY_WC_CLOSING : MOY_WC_OFF;
            break;
        case MOY_WC_CLOSING:
            break;
        default:
            s_wc->st.state = MOY_WC_OFF;
            break;
    }
}

int moy_wc_poll(void) {
    if (!ready()) {
        return 0;
    }
    moy_wc_state_t *st = &s_wc->st;
    moy_web_state_t ws;
    uint32_t ip;
    switch (st->state) {
        case MOY_WC_JOINING:
            if (link_up(&ip)) {
                st->ip = ip;
                int rc = moy_web_bind(st->port);
                st->err = rc;
                st->state = rc == 0 ? MOY_WC_SERVING : MOY_WC_FAILED;
                return 1;
            }
            if (moy_net_ms() - s_wc->since >= MOY_WC_JOIN_MS) {
                st->err = 110;      // ETIMEDOUT: no link
                st->state = MOY_WC_FAILED;
                return 1;
            }
            return 0;
        case MOY_WC_SERVING:
            moy_web_state(&ws);
            if (!ws.listening) {
                st->state = MOY_WC_OFF;
                return 1;
            }
            if (link_up(&ip) && ip != st->ip) {
                st->ip = ip;        // a re-association took a new address
                return 1;
            }
            return 0;
        case MOY_WC_CLOSING:
            moy_web_state(&ws);
            if (!ws.listening) {
                st->state = MOY_WC_OFF;
                return 1;
            }
            return 0;
        default:
            return 0;
    }
}

void moy_wc_state(moy_wc_state_t *out) {
    if (!ready()) {
        memset(out, 0, sizeof(*out));
        return;
    }
    *out = s_wc->st;
}

void moy_wc_park(int on) {
    if (ready() && s_wc->st.parked != (on != 0)) {
        s_wc->st.parked = on != 0;
    }
}

// base64url, no padding, NUL-terminated.
static void b64url(const uint8_t *p, size_t n, char *out) {
    static const char A[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    size_t k = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)p[i] << 16 | (i + 1 < n ? (uint32_t)p[i + 1] << 8 : 0)
                     | (i + 2 < n ? p[i + 2] : 0);
        out[k++] = A[v >> 18 & 63];
        out[k++] = A[v >> 12 & 63];
        if (i + 1 < n) {
            out[k++] = A[v >> 6 & 63];
        }
        if (i + 2 < n) {
            out[k++] = A[v & 63];
        }
    }
    out[k] = '\0';
}

size_t moy_wc_url(char *out, size_t cap, int paired) {
    if (!ready() || s_wc->st.ip == 0) {
        if (cap > 0) {
            out[0] = '\0';
        }
        return 0;
    }
    uint32_t ip = s_wc->st.ip;      // network order
    char port[8] = "";
    if (s_wc->st.port != 80) {
        snprintf(port, sizeof(port), ":%u", (unsigned)s_wc->st.port);
    }
    // The QR's link (moy_net.h): base64url keeps it inside the code's
    // version 4 (78 bytes), the token whole and the board's id's first 8
    // bytes, which the page holds against the hello's.
    char hex[33] = "", tok[24] = "", id[12] = "";
    if (paired) {
        uint8_t raw[MOY_SEAL_RAND];
        moy_web_token(hex);
        if (moy_seal_unhex(hex, strlen(hex), raw, MOY_SEAL_RAND)) {
            b64url(raw, MOY_SEAL_RAND, tok);
            moy_web_board_id(hex);
            moy_seal_unhex(hex, strlen(hex), raw, MOY_SEAL_RAND);
            b64url(raw, 8, id);
        }
        moy_seal_wipe(raw, sizeof raw);
        moy_seal_wipe(hex, sizeof hex);
    }
    int n = snprintf(out, cap, "http://%u.%u.%u.%u%s/%s%s%s%s", (unsigned)(ip & 0xff),
                     (unsigned)((ip >> 8) & 0xff), (unsigned)((ip >> 16) & 0xff),
                     (unsigned)(ip >> 24), port, tok[0] ? "#t=" : "", tok,
                     tok[0] ? "&b=" : "", tok[0] ? id : "");
    moy_seal_wipe(tok, sizeof tok);
    return n < 0 ? 0 : (size_t)n;
}
