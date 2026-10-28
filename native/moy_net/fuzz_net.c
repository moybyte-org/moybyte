// moy_net's wire half under the sanitizers: random and mutated requests,
// queries and batch bodies, with every returned span held inside its buffer,
// and every encoded batch decoded back to the fields it was given; the
// updater's pure half (moy_ota.c): response heads, URLs and manifests, with
// the canonical text and the judge never reading past what they were given.
//
//   cc -std=c99 -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=all \
//      -I native/moy_net -I native/moy_spine native/moy_net/fuzz_net.c \
//      native/moy_net/moy_http.c native/moy_net/moy_sync.c \
//      native/moy_net/moy_link.c native/moy_net/moy_ota.c \
//      native/moy_net/moy_gpio.c native/moy_net/moy_dns.c \
//      native/moy_spine/moy_json.c -o fuzz_net
//   ./fuzz_net SEED ROUNDS

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_json.h"
#include "moy_link.h"
#include "moy_net.h"
#include "moy_ota.h"

// The platform moy_ota.c is linked against: memory, and no connection or slot.
void *moy_net_alloc(size_t n) {
    return calloc(1, n ? n : 1);
}

void moy_net_free(void *p) {
    free(p);
}

int moy_conn_open(const char *host, uint16_t port, int tls, void **conn) {
    (void)host, (void)port, (void)tls;
    *conn = NULL;
    return -111;
}

int moy_conn_read(void *conn, void *p, size_t n) {
    (void)conn, (void)p, (void)n;
    return -5;
}

int moy_conn_write(void *conn, const void *p, size_t n) {
    (void)conn, (void)p, (void)n;
    return -5;
}

void moy_conn_close(void *conn) {
    (void)conn;
}

int moy_slot_open(uint32_t size, uint32_t *cap) {
    (void)size;
    *cap = 0;
    return -19;
}

int moy_slot_write(const void *p, size_t n) {
    (void)p, (void)n;
    return -9;
}

int moy_slot_close(void) {
    return -9;
}

int moy_slot_boot(char *label, size_t cap) {
    (void)label, (void)cap;
    return -9;
}

void moy_slot_abort(void) {
}

int moy_udp_listen(uint16_t port) {
    (void)port;
    return -98;
}

uint16_t moy_udp_port(int fd) {
    (void)fd;
    return 0;
}

int moy_udp_recv(int fd, void *buf, size_t cap, uint8_t from[MOY_UDP_ADDR]) {
    (void)fd, (void)buf, (void)cap, (void)from;
    return 0;
}

int moy_udp_send(int fd, const void *buf, size_t n, const uint8_t to[MOY_UDP_ADDR]) {
    (void)fd, (void)buf, (void)n, (void)to;
    return -1;
}

void moy_http_close(int fd) {
    (void)fd;
}

// The Zero's pins, for the /gpio route: three of them, levels kept.
static const uint8_t PINS[3] = {1, 2, 21};
static int levels[64];

int moy_gpio_pins(const uint8_t **pins) {
    *pins = PINS;
    return 3;
}

int moy_gpio_drive(int pin, int level) {
    if (pin != 1 && pin != 2 && pin != 21) {
        abort();                    // a pin off the allowlist was driven
    }
    levels[pin] = level;
    return 0;
}

int moy_gpio_sense(int pin) {
    if (pin != 1 && pin != 2 && pin != 21) {
        abort();
    }
    return levels[pin];
}

static uint32_t rng = 1;

static uint32_t rnd(void) {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

#define CHECK(c)                                                         \
    do {                                                                 \
        if (!(c)) {                                                      \
            fprintf(stderr, "fuzz_net: %s:%d %s\n", __FILE__, __LINE__, #c); \
            abort();                                                     \
        }                                                                \
    } while (0)

static const char *SEEDS[] = {
    "GET /carts.json?pin=1234 HTTP/1.1\r\nHost: x\r\n\r\n",
    "POST /sync HTTP/1.1\r\nContent-Length: 12\r\n\r\n{\"v\": 1}",
    "PUT  /x HTTP/1.1\ncontent-length:  +7 \n\nbody",
    "GET /a?x&pin=&pin=9 HTTP/1.0\r\nCONTENT-LENGTH: 99999999999\r\n\r\n",
    "{\"v\": 2, \"root\": \"files\", \"ops\": [{\"p\": \"x\", \"t\": \"\\u00e9\"}], \"pin\": \"1\"}",
    "{\"v\": 1, \"ops\": [], \"v\": \"again\"}",
    "[1, 2]",
    "\xff\xfe",
    "HTTP/1.1 302 Found\r\nLocation: /next?a=1\r\ncontent-length: 0\r\n\r\nbody",
    "{\"board\": \"tdeck\", \"channel\": \"stable\", \"version\": 7, \"size\": \"12\", "
    "\"sha256\": \"AB\", \"sig\": \"0f\", \"c6\": {\"version\": 1.5, \"size\": null}}",
    "https://h:8443/a/b.json",
    "{\"v\": 1, \"pin\": \"1\", \"ops\": [{\"p\": 21, \"mode\": \"out\", \"v\": 0}, "
    "{\"p\": 2, \"mode\": \"read\"}, {\"p\": 43, \"mode\": \"out\", \"v\": 1}]}",
    "\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01",
};

static const char ALPHA[] = "GETPOS /?&=:\r\n\"{}[],0123456789vrootpsin-Content-Length\\u\xc3\xa9\xff";

static size_t mutate(char *buf, size_t n, size_t cap) {
    int k = 1 + (int)(rnd() % 6);
    while (k--) {
        uint32_t op = rnd() % 4;
        if (op == 0 && n < cap) {               // insert
            size_t at = n ? rnd() % (n + 1) : 0;
            memmove(buf + at + 1, buf + at, n - at);
            buf[at] = ALPHA[rnd() % (sizeof(ALPHA) - 1)];
            n++;
        } else if (op == 1 && n) {              // delete
            size_t at = rnd() % n;
            memmove(buf + at, buf + at + 1, n - at - 1);
            n--;
        } else if (op == 2 && n) {              // replace
            buf[rnd() % n] = ALPHA[rnd() % (sizeof(ALPHA) - 1)];
        } else if (n) {                         // truncate
            n = rnd() % (n + 1);
        }
    }
    return n;
}

static void inside(const char *buf, size_t n, const char *a, size_t k) {
    CHECK(a >= buf && a + k <= buf + n);
}

static void one(const char *src, size_t n) {
    // An exact-size heap copy, so a read past the end is ASan's.
    char *buf = malloc(n ? n : 1);
    memcpy(buf, src, n);
    moy_http_req_t r;
    int rc = moy_http_parse(buf, n, &r);
    CHECK(rc == MOY_HTTP_OK || rc == MOY_HTTP_PARTIAL || rc == MOY_HTTP_BAD);
    if (rc == MOY_HTTP_OK) {
        inside(buf, n, r.method, r.method_n);
        inside(buf, n, r.target, r.target_n);
        CHECK(r.head_end <= n && r.clen <= MOY_HTTP_CLEN_MAX);
        const char *v;
        size_t vn;
        if (moy_http_query(r.target, r.target_n, "pin", 3, &v, &vn)) {
            inside(r.target, r.target_n, v, vn);
        }
    }
    moy_sync_env_t e;
    if (moy_sync_decode(buf, n, &e) == MOY_SYNC_OK) {
        CHECK(moy_utf8_valid(buf, n));
        const char *spans[4][2] = {{e.v, e.v_end}, {e.root, e.root_end},
                                   {e.ops, e.ops_end}, {e.pin, e.pin_end}};
        for (int i = 0; i < 4; i++) {
            if (spans[i][0]) {
                inside(buf, n, spans[i][0], (size_t)(spans[i][1] - spans[i][0]));
                CHECK(moy_json_valid(spans[i][0],
                                     (size_t)(spans[i][1] - spans[i][0]))
                      == MOY_JSON_OK);
            }
        }
        // Re-encoded from its own fields, a batch decodes to the same spans.
        if (e.v && e.ops) {
            size_t need = moy_sync_encode(
                NULL, 0, e.v, (size_t)(e.v_end - e.v), e.root,
                e.root ? (size_t)(e.root_end - e.root) : 0, e.ops,
                (size_t)(e.ops_end - e.ops), e.pin,
                e.pin ? (size_t)(e.pin_end - e.pin) : 0);
            char *out = malloc(need);
            CHECK(moy_sync_encode(out, need, e.v, (size_t)(e.v_end - e.v),
                                  e.root,
                                  e.root ? (size_t)(e.root_end - e.root) : 0,
                                  e.ops, (size_t)(e.ops_end - e.ops), e.pin,
                                  e.pin ? (size_t)(e.pin_end - e.pin) : 0)
                  == need);
            moy_sync_env_t back;
            CHECK(moy_sync_decode(out, need, &back) == MOY_SYNC_OK);
            CHECK((size_t)(back.ops_end - back.ops) == (size_t)(e.ops_end - e.ops));
            CHECK(memcmp(back.ops, e.ops, (size_t)(e.ops_end - e.ops)) == 0);
            CHECK((back.root == NULL) == (e.root == NULL));
            CHECK((back.pin == NULL) == (e.pin == NULL));
            free(out);
        }
    }
    // The receiver's batch rule: a batch it takes is one the envelope took, its
    // ops an array and its pin a span inside the body.
    moy_sync_batch_t bt;
    if (moy_sync_batch(buf, n, &bt) == MOY_SYNC_OK) {
        CHECK(moy_sync_decode(buf, n, &e) == MOY_SYNC_OK);
        CHECK(bt.root == MOY_SYNC_CARTS || bt.root == MOY_SYNC_FILES);
        inside(buf, n, bt.ops, (size_t)(bt.ops_end - bt.ops));
        CHECK(moy_json_kind(bt.ops, bt.ops_end) == MOY_JSON_ARR);
        if (bt.pin) {
            inside(buf, n, bt.pin, (size_t)(bt.pin_end - bt.pin));
        }
        CHECK(moy_sync_pin_ok(&bt, NULL) && moy_sync_pin_ok(&bt, ""));
    }
    char head[256];
    size_t hn = moy_http_head(head, sizeof(head), 200 + (int)(rnd() % 400),
                              "text/plain", n);
    CHECK(hn < sizeof(head));
    moy_http_resp_t hr;
    if (moy_http_resp_parse(buf, n, &hr) == MOY_HTTP_OK) {
        CHECK(hr.head_end <= n && hr.status >= 0 && hr.status < 1000);
        if (hr.loc) {
            inside(buf, n, hr.loc, hr.loc_n);
        }
    }
    char *z = malloc(n + 1);              // the URL parser takes a C string
    memcpy(z, buf, n);
    z[n] = '\0';
    int tls;
    char host[MOY_URL_HOST_MAX];
    uint16_t port;
    const char *path;
    if (moy_url_parse(z, &tls, host, sizeof(host), &port, &path) == 0) {
        CHECK(port > 0 && strlen(host) > 0 && path[0] == '/');
    }
    free(z);
    char canon[600];
    size_t cn = moy_ota_canonical(buf, n, canon, sizeof(canon));
    CHECK(cn == (size_t)-1 || cn < 1000);
    cn = moy_ota_canonical_c6(buf, n, canon, sizeof(canon));
    CHECK(cn == (size_t)-1 || cn < 1000);
    char why[MOY_OTA_ERR_MAX];
    int jr = moy_ota_judge(buf, n, "tdeck", (int)(rnd() & 1), NULL, 0, why, sizeof(why));
    CHECK(jr == MOY_OTA_OK || jr == MOY_OTA_ERR);
    jr = moy_ota_judge_c6(buf, n, (int)(rnd() & 1), NULL, 0, why, sizeof(why));
    CHECK(jr == MOY_OTA_OK || jr == MOY_OTA_ERR);
    CHECK(moy_ota_verify(buf, n, buf, n < 600 ? n : 600, NULL, 0) == 0);
    char gdoc[512];
    int gs = moy_gpio_request((int)(rnd() & 1), "/gpio?pin=1", 11, buf, n,
                              (rnd() & 1) ? "1" : NULL, gdoc, sizeof(gdoc));
    CHECK(gs == 200 || gs == 400 || gs == 403);
    CHECK(strlen(gdoc) < sizeof(gdoc));
    uint8_t dns[MOY_DNS_QUERY_MAX + 16];
    size_t dn = moy_dns_reply((const uint8_t *)buf, n, 0x0104a8c0u, dns, sizeof(dns));
    CHECK(dn == 0 || (dn >= 17 && dn <= n + 16));
    if (dn) {
        CHECK(memcmp(dns, buf, 2) == 0 && (dns[2] & 0x80));
    }
    free(buf);
}


// The link's ring against a model: a FIFO of records that either fit whole or
// are dropped and counted, with the bytes wrapping the end of the buffer.
static void ring_walk(long rounds) {
    enum { CAP = 1031, MODEL = 64 };
    uint8_t *buf = malloc(CAP);
    moy_link_ring_t r;
    moy_link_ring_init(&r, buf, CAP);
    uint8_t q[MODEL][MOY_LINK_MAX + 7];
    uint32_t qn[MODEL];
    int qh = 0, qc = 0;
    uint32_t drops = 0;
    for (long i = 0; i < rounds; i++) {
        if (rnd() % 3) {
            uint8_t mac[6], data[MOY_LINK_MAX + 8];
            uint32_t len = rnd() % (MOY_LINK_MAX + 8);
            for (int k = 0; k < 6; k++) mac[k] = (uint8_t)rnd();
            for (uint32_t k = 0; k < len; k++) data[k] = (uint8_t)rnd();
            int fits = len <= MOY_LINK_MAX && len + MOY_LINK_REC <= CAP - r.used
                       && qc < MODEL;
            if (qc == MODEL) {
                continue;
            }
            CHECK(moy_link_ring_put(&r, mac, data, len) == fits);
            if (fits) {
                int t = (qh + qc) % MODEL;
                memcpy(q[t], mac, 6);
                memcpy(q[t] + 6, data, len);
                qn[t] = len;
                qc++;
            } else {
                drops++;
            }
        } else {
            uint8_t mac[6], data[MOY_LINK_MAX];
            int n = moy_link_ring_get(&r, mac, data, sizeof(data));
            if (qc == 0) {
                CHECK(n == -1);
                continue;
            }
            CHECK(n == (int)qn[qh]);
            CHECK(memcmp(mac, q[qh], 6) == 0 && memcmp(data, q[qh] + 6, (size_t)n) == 0);
            qh = (qh + 1) % MODEL;
            qc--;
        }
        CHECK(r.drops == drops && r.used <= CAP && r.peak <= CAP);
    }
    free(buf);
}

int main(int argc, char **argv) {
    rng = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 10) | 1u : 1u;
    long rounds = argc > 2 ? strtol(argv[2], NULL, 10) : 20000;
    char buf[512];
    for (size_t i = 0; i < sizeof(SEEDS) / sizeof(SEEDS[0]); i++) {
        one(SEEDS[i], strlen(SEEDS[i]));
    }
    for (long i = 0; i < rounds; i++) {
        const char *s = SEEDS[rnd() % (sizeof(SEEDS) / sizeof(SEEDS[0]))];
        size_t n = strlen(s);
        memcpy(buf, s, n);
        n = mutate(buf, n, sizeof(buf));
        one(buf, n);
    }
    ring_walk(rounds * 4);
    // SHA-256 against its published vectors, fed in uneven pieces.
    static const char *abc = "abc";
    uint8_t d[32];
    moy_sha256_t h;
    moy_sha256_init(&h);
    moy_sha256_update(&h, abc, 1);
    moy_sha256_update(&h, abc + 1, 2);
    moy_sha256_final(&h, d);
    CHECK(d[0] == 0xba && d[1] == 0x78 && d[30] == 0x15 && d[31] == 0xad);
    moy_sha256_init(&h);
    for (int i = 0; i < 1000000; i += 1000) {
        char a[1000];
        memset(a, 'a', sizeof(a));
        moy_sha256_update(&h, a, (size_t)(i % 3000 == 0 ? 1000 : 1000));
    }
    moy_sha256_final(&h, d);
    CHECK(d[0] == 0xcd && d[1] == 0xc7 && d[31] == 0xd0);
    CHECK(moy_wifi_use_stored(0, 3) && !moy_wifi_use_stored(2, 3));
    CHECK(moy_wifi_remember(0, "a", 1, NULL, 0));
    CHECK(!moy_wifi_remember(0, "a", 1, "a", 1));
    printf("fuzz_net: %ld rounds\n", rounds);
    return 0;
}
