// App Talk: a compiled app in C (docs/app_api_v1.md sections 8 to 10), built
// by tools/wasm_cart.py with wasi-sdk as a reactor: its exports are its loop.
// It reaches the console's calls through its moybyte.app imports, as a
// Python or Lua app reaches them by name.
//
// At _init it listens on "pong", pings the Echo service (local.echo), posts a
// notification, awards the badge its manifest declares, sets an alarm that is
// already due, a timer that goes off once and one that repeats, asks its
// documents for a free name, writes the OS log, and opens leases by handle
// (a pin it drives high and keeps until its run ends, a UART it writes and
// reads back through its peer and closes) and the LoRa radio by handle,
// starting an OTAA join it keeps running (an uplink before it is accepted
// refused, the radio busy while it runs), then asks for minute-long timers
// until the console answers FULL and cancels all but the first, which it keeps
// for its run's end to sweep. Every _update reads the messages queued for it, one record
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
int32_t files_new_name(const char *kind, uint32_t kind_n, const char *title, uint32_t title_n,
                       char *name, uint32_t cap) APP(files_new_name);
int32_t moybyte_log(int32_t level, const char *text, uint32_t text_n) APP(moybyte_log);
int32_t moybyte_item_read(int32_t item, char *data, uint32_t cap) APP(moybyte_item_read);
int32_t moybyte_dev_open(const char *name, uint32_t name_n, const char *cfg,
                         uint32_t cfg_n) APP(moybyte_dev_open);
int32_t moybyte_dev_close(int32_t h) APP(moybyte_dev_close);
int32_t moybyte_dev_read(int32_t h, char *data, uint32_t cap) APP(moybyte_dev_read);
int32_t moybyte_dev_write(int32_t h, const char *data, uint32_t n) APP(moybyte_dev_write);
int32_t moybyte_dev_get(int32_t h, const char *what, uint32_t what_n) APP(moybyte_dev_get);
int32_t moybyte_dev_set(int32_t h, const char *cfg, uint32_t cfg_n) APP(moybyte_dev_set);
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
    static char name[32];
    int32_t k = files_new_name(S("docs"), S("note"), name, sizeof name);
    put(S("new_name "));
    if (k >= 0) {
        put(name, (uint32_t)k);
    } else {
        put_int(k);
    }
    say();
    ticker = moybyte_every(1);
    put(S("every "));
    put_int(ticker);
    say();
    int32_t pin = moybyte_dev_open(S("gpio6"), S("mode=out value=1"));
    put(S("dev_open "));
    put_int(pin > 0 ? 1 : pin);
    say();
    put(S("dev_get "));
    put_int(moybyte_dev_get(pin, S("value")));
    say();
    int32_t u = moybyte_dev_open(S("uart1"), S("baudrate=9600"));
    put(S("dev_write "));
    put_int(moybyte_dev_write(u, S("ping")));
    say();
    static char got[16];
    int32_t n = moybyte_dev_read(u, got, sizeof got);
    put(S("dev_read "));
    if (n >= 0) {
        put(got, (uint32_t)n);
    } else {
        put_int(n);
    }
    say();
    put(S("dev_close "));
    put_int(moybyte_dev_close(u));
    say();
    int32_t lora = moybyte_dev_open(S("lora"), S("send=2"));
    put(S("lora_open "));
    put_int(lora > 0 ? 1 : lora);
    say();
    put(S("lora_join "));
    put_int(moybyte_dev_set(lora, S("join=otaa dev_eui=70B3D57ED0050001 "
                                    "join_eui=0000000000000000 "
                                    "app_key=8E3F2A1B0C9D8E7F6A5B4C3D2E1F0A9B")));
    say();
    put(S("lora_busy "));
    put_int(moybyte_dev_get(lora, S("busy")));
    say();
    put(S("lora_uplink "));
    put_int(moybyte_dev_write(lora, S("early")));
    say();
    static int32_t held[64];
    int32_t t = 0, r = 0;
    while (t < 64 && (r = moybyte_after(60000)) > 0) {
        held[t++] = r;
    }
    put(S("timers "));
    put_int(t);
    put(S(" "));
    put_int(r);
    say();
    int32_t undone = 0;
    for (int32_t i = 1; i < t; i++) {
        undone += moybyte_cancel(held[i]);
    }
    put(S("uncap "));
    put_int(undone);
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
