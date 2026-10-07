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

extern int moy_net_link(uint32_t *ip) __attribute__((weak));
extern int moy_wifi_connect_kept(void) __attribute__((weak));
extern void moy_surface_epoch(void) __attribute__((weak));

typedef struct {
    moy_wc_state_t st;
    uint32_t since;                 // the join's start, moy_net_ms
    uint32_t given_ip;              // the caller's address, where there is no driver
    char pin[16];
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
    snprintf(s_wc->pin, sizeof(s_wc->pin), "%s", cfg->pin != NULL ? cfg->pin : "");
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

void moy_wc_set_pin(const char *pin) {
    if (ready()) {
        snprintf(s_wc->pin, sizeof(s_wc->pin), "%s", pin != NULL ? pin : "");
    }
    moy_web_set_pin(pin);
}

void moy_wc_park(int on) {
    if (ready() && s_wc->st.parked != (on != 0)) {
        s_wc->st.parked = on != 0;
        if (moy_surface_epoch != NULL) {
            moy_surface_epoch();    // the glass changes owner: everything is dirty
        }
    }
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
    int n = snprintf(out, cap, "http://%u.%u.%u.%u%s/%s%s", (unsigned)(ip & 0xff),
                     (unsigned)((ip >> 8) & 0xff), (unsigned)((ip >> 16) & 0xff),
                     (unsigned)(ip >> 24), port,
                     paired && s_wc->pin[0] ? "?pin=" : "", paired ? s_wc->pin : "");
    return n < 0 ? 0 : (size_t)n;
}
