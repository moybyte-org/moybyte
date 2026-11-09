// App Talk: a compiled app in C (docs/app_api_v1.md sections 8 to 10), built
// by tools/wasm_cart.py with wasi-sdk as a reactor: its exports are its loop.
// It reaches the console's calls through its moybyte.app imports, as a
// Python or Lua app reaches them by name.
//
// At _init it listens on "pong", pings the Echo service (local.echo), posts a
// notification, awards the badge its manifest declares, sets an alarm that is
// already due, a timer that goes off once and one that repeats, and writes
// the OS log. Every _update reads the messages queued for it, one record
// each (moy_evq_ev_t, sdk/include/moybyte/os.h, its arguments a JSON array
// after it), and logs what it heard: a timer is the system's message
// "moybyte.timer" carrying its id, and the repeating one's first tick
// cancels it; an item handed
// to it (its window event moy_open, a message from the system carrying the
// item's handle first) is read and its text logged. A notification refused
// for want of the permission is logged with its answer.
// tests/test_wasm_app.py drives it beside tests/fixtures/apps/echo_lua.moy.

#include <stdint.h>

#include "moybyte/os.h"

#define APP(name) __attribute__((import_module("moybyte.app"), import_name(#name)))
#define MOY(name) __attribute__((import_module("moy"), import_name(#name)))
#define EXPORT(name) __attribute__((export_name(#name)))

int32_t moybyte_send(const char *app, uint32_t app_n, const char *name, uint32_t name_n,
                     const char *args, uint32_t args_n) APP(moybyte_send);
int32_t moybyte_on(const char *name, uint32_t name_n, int32_t on) APP(moybyte_on);
int32_t moybyte_recv(char *record, uint32_t cap) APP(moybyte_recv);
int32_t moybyte_notify(const char *summary, uint32_t summary_n, const char *body,
                       uint32_t body_n) APP(moybyte_notify);
int32_t moybyte_award(const char *id, uint32_t id_n) APP(moybyte_award);
int32_t moybyte_at(int32_t t) APP(moybyte_at);
int32_t moybyte_after(int32_t ms) APP(moybyte_after);
int32_t moybyte_every(int32_t ms) APP(moybyte_every);
int32_t moybyte_cancel(int32_t id) APP(moybyte_cancel);
int32_t moybyte_log(int32_t level, const char *text, uint32_t text_n) APP(moybyte_log);
int32_t moybyte_item_read(int32_t item, char *data, uint32_t cap) APP(moybyte_item_read);
void cls(int32_t c) MOY(cls);

static uint32_t len(const char *s) {
    uint32_t n = 0;
    while (s[n]) {
        n++;
    }
    return n;
}

#define S(s) s, len(s)

// A line being written, and what is appended to it.
static char line[256];
static uint32_t at;

static void put(const char *s, uint32_t n) {
    for (uint32_t i = 0; i < n && at < sizeof line - 1; i++) {
        line[at++] = s[i];
    }
}

static void put_int(int32_t v) {
    char d[12];
    int k = 0;
    uint32_t u = v < 0 ? (uint32_t)-v : (uint32_t)v;
    do {
        d[k++] = (char)('0' + u % 10u);
        u /= 10u;
    } while (u);
    if (v < 0) {
        put("-", 1);
    }
    while (k) {
        put(&d[--k], 1);
    }
}

static void say(void) {
    moybyte_log(MOY_LOG_INFO, line, at);
    at = 0;
}

static int32_t ticker;

EXPORT(_init) void init(void) {
    moybyte_on(S("pong"), 1);
    moybyte_on(S("moy_open"), 1);
    put(S("send "));
    put_int(moybyte_send(S("local.echo"), S("ping"), S("[41]")));
    say();
    put(S("notify "));
    put_int(moybyte_notify(S("Talk"), S("Sent a ping")));
    say();
    put(S("award "));
    put_int(moybyte_award(S("hello")));
    say();
    put(S("at "));
    put_int(moybyte_at(1));
    say();
    put(S("after "));
    put_int(moybyte_after(0));
    say();
    ticker = moybyte_every(1);
    put(S("every "));
    put_int(ticker);
    say();
}

static int same(const char *a, const char *b) {
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

// The whole number that starts the JSON array at `p`.
static int32_t first_int(const char *p) {
    int32_t v = 0;
    while (*p && (*p < '0' || *p > '9')) {
        p++;
    }
    while (*p >= '0' && *p <= '9') {
        v = v * 10 + (*p++ - '0');
    }
    return v;
}

static char item[64];

static union {
    moy_evq_ev_t ev;
    char b[sizeof(moy_evq_ev_t) + 128];
} m;

EXPORT(_update) void update(float dt) {
    (void)dt;
    int32_t n;
    while ((n = moybyte_recv(m.b, sizeof m.b)) > 0) {
        put(S("heard "));
        put(S(m.ev.name));
        put(S(" from "));
        put(S(m.ev.app));
        put(S(" "));
        put(m.b + sizeof m.ev, m.ev.n);
        say();
        if (same(m.ev.name, "moybyte.timer") && first_int(m.b + sizeof m.ev) == ticker) {
            put(S("cancel "));
            put_int(moybyte_cancel(first_int(m.b + sizeof m.ev)));
            say();
        }
        if (same(m.ev.name, "moy_open")) {
            int32_t k = moybyte_item_read(first_int(m.b + sizeof m.ev), item, sizeof item);
            put(S("item "));
            if (k >= 0) {
                put(item, (uint32_t)k);
            } else {
                put_int(k);
            }
            say();
        }
    }
}

EXPORT(_draw) void draw(void) {
    cls(1);
}
