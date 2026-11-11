// moy_scan: the shared GAP master (moy_scan.h).

#include "moy_scan.h"

void moy_scan_init(moy_scan_t *s, const moy_scan_ops_t *ops, void *ctx) {
    for (int c = 0; c < MOY_SCAN_CLIENTS; c++) {
        s->on[c] = 0;
        s->flags[c] = 0;
        s->until[c] = 0;
    }
    s->ops = ops;
    s->ctx = ctx;
    s->running = -1;
    s->connecting = -1;
}

uint32_t moy_scan_size(void) {
    return sizeof(moy_scan_t);
}

// What the air should carry: the union of the clients on, nothing while a
// connection attempt holds the master.
static int wanted(const moy_scan_t *s) {
    if (s->connecting >= 0) {
        return -1;
    }
    int want = -1;
    for (int c = 0; c < MOY_SCAN_CLIENTS; c++) {
        if (s->on[c]) {
            want = (want < 0 ? 0 : want) | s->flags[c];
        }
    }
    return want;
}

static int settle(moy_scan_t *s) {
    int want = wanted(s);
    if (s->running == want) {
        return 0;
    }
    if (s->running >= 0) {
        s->ops->cancel(s->ctx);
        s->running = -1;
    }
    if (want < 0) {
        return 0;
    }
    int rc = s->ops->disc(s->ctx, want);
    if (rc == 0) {
        s->running = (int8_t)want;
    }
    return rc;
}

static void end_all(moy_scan_t *s, int err) {
    for (int c = 0; c < MOY_SCAN_CLIENTS; c++) {
        if (s->on[c]) {
            s->on[c] = 0;
            s->ops->done(s->ctx, c, err);
        }
    }
}

// The air as the clients on it want it, or every one of them ended.
static void settle_or_end(moy_scan_t *s) {
    int rc = settle(s);
    if (rc != 0) {
        end_all(s, rc);
    }
}

int moy_scan_start(moy_scan_t *s, int client, int flags, uint32_t ms, uint32_t now) {
    uint8_t was = s->on[client];
    s->on[client] = 1;
    s->flags[client] = (uint8_t)flags;
    s->until[client] = now + ms;
    int rc = settle(s);
    if (rc != 0) {
        s->on[client] = 0;
        settle_or_end(s);
        if (was) {
            s->ops->done(s->ctx, client, rc);
        }
    }
    return rc;
}

void moy_scan_stop(moy_scan_t *s, int client) {
    if (!s->on[client]) {
        return;
    }
    s->on[client] = 0;
    settle_or_end(s);
    s->ops->done(s->ctx, client, 0);
}

int moy_scan_on(const moy_scan_t *s, int client) {
    return s->on[client];
}

void moy_scan_tick(moy_scan_t *s, uint32_t now) {
    uint8_t ended[MOY_SCAN_CLIENTS];
    int any = 0;
    for (int c = 0; c < MOY_SCAN_CLIENTS; c++) {
        ended[c] = s->on[c] && (int32_t)(now - s->until[c]) >= 0;
        if (ended[c]) {
            s->on[c] = 0;
            any = 1;
        }
    }
    if (!any) {
        return;
    }
    settle_or_end(s);
    for (int c = 0; c < MOY_SCAN_CLIENTS; c++) {
        if (ended[c]) {
            s->ops->done(s->ctx, c, 0);
        }
    }
}

int32_t moy_scan_due(const moy_scan_t *s, uint32_t now) {
    int32_t due = -1;
    for (int c = 0; c < MOY_SCAN_CLIENTS; c++) {
        if (s->on[c]) {
            int32_t left = (int32_t)(s->until[c] - now);
            if (left < 0) {
                left = 0;
            }
            if (due < 0 || left < due) {
                due = left;
            }
        }
    }
    return due;
}

int moy_scan_connect(moy_scan_t *s, int client) {
    if (s->connecting >= 0) {
        return -MOY_SCAN_EBUSY;
    }
    s->connecting = (int8_t)client;
    settle(s);
    return 0;
}

void moy_scan_connected(moy_scan_t *s, int client) {
    if (s->connecting != client) {
        return;
    }
    s->connecting = -1;
    settle_or_end(s);
}

int moy_scan_connecting(const moy_scan_t *s) {
    return s->connecting;
}

void moy_scan_lost(moy_scan_t *s, int err) {
    if (s->running < 0) {
        return;
    }
    s->running = -1;
    if (settle(s) != 0) {
        end_all(s, err != 0 ? err : -1);
    }
}
