// The index's API sequence, fuzzed against a model, under the sanitizers.
//
// tools/moy_index_spike.py builds it (`sanitize`), and so does
// tests/test_moy_index_twins.py. With clang's libFuzzer
// (-fsanitize=fuzzer,address,undefined) it is a LLVMFuzzerTestOneInput; with
// -DMOY_INDEX_FUZZ_MAIN it is a seeded random driver any compiler builds,
// `fuzz_index SEED RUNS`. Either way it links one implementation of
// moy_index.h, whichever twin, and defines the host's two imports itself.
//
// An input is a program: an op byte, then its operands. The model is the
// table done the obvious way, arrays scanned linearly, and every answer the
// implementation gives is checked against it. The imports count every byte,
// hold each free to the size it was allocated with, and refuse an allocation
// on a countdown the input sets, so the out-of-memory paths run too and must
// leave the table exactly as it was.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_index.h"

#define SLOTS MOY_INDEX_SLOTS
#define MAXP 24
#define STALE_RING 16

#define CHECK(c) do { \
        if (!(c)) { \
            fprintf(stderr, "fuzz_index: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            abort(); \
        } \
} while (0)

// -- the host's imports, counted --------------------------------------------

static long live_bytes;
static int fail_in;         // refuse the allocation this many calls on; 0 off
static int failed;          // an allocation was refused during this op

void *moy_index_host_alloc(size_t n) {
    if (fail_in > 0 && --fail_in == 0) {
        failed = 1;
        return NULL;
    }
    size_t *p = calloc(1, n + 2 * sizeof(size_t));
    if (p == NULL) {
        return NULL;
    }
    p[0] = n;
    live_bytes += (long)n;
    return p + 2;
}

void moy_index_host_free(void *q, size_t n) {
    if (q == NULL) {
        return;
    }
    size_t *p = (size_t *)q - 2;
    CHECK(p[0] == n);
    live_bytes -= (long)n;
    free(p);
}

// -- the model ----------------------------------------------------------------

static uint32_t m_gen[SLOTS];
static uint8_t m_live[SLOTS];
static uint8_t m_len[SLOTS];
static char m_path[SLOTS][MAXP];
static uint32_t m_used, m_count;
static uint32_t stale[STALE_RING];
static uint32_t stale_at, serial;
static int16_t m_head[1024];        // live rows chained by a hash of the path
static int16_t m_next[SLOTS];

static void m_reset(void) {
    memset(m_live, 0, sizeof m_live);
    memset(m_head, 0xff, sizeof m_head);
    m_used = m_count = 0;
}

static int16_t *m_chain(const char *p, size_t n) {
    uint32_t k = (uint32_t)n;
    for (size_t i = 0; i < n; i++) {
        k = k * 31u + (uint8_t)p[i];
    }
    return &m_head[k % 1024u];
}

static uint32_t m_handle(uint32_t s) {
    return (m_gen[s] << MOY_INDEX_SLOT_BITS) | s;
}

static int m_find(const char *p, size_t n) {
    for (int s = *m_chain(p, n); s >= 0; s = m_next[s]) {
        if (m_len[s] == n && memcmp(m_path[s], p, n) == 0) {
            return s;
        }
    }
    return -1;
}

static int m_valid(uint32_t h) {
    uint32_t s = h & (SLOTS - 1u);
    return h != 0 && s < m_used && m_live[s]
           && m_gen[s] == (h >> MOY_INDEX_SLOT_BITS);
}

static void verify_row(const moy_index_t *ix, uint32_t s) {
    CHECK(moy_index_at(ix, s) == (m_live[s] ? m_handle(s) : 0u));
    if (!m_live[s]) {
        return;
    }
    uint32_t h = m_handle(s);
    const char *p;
    size_t n;
    CHECK(moy_index_path(ix, h, &p, &n) == MOY_INDEX_OK);
    CHECK(n == m_len[s] && memcmp(p, m_path[s], n) == 0);
    CHECK(moy_index_find(ix, m_path[s], m_len[s]) == h);
    CHECK(moy_index_valid(ix, h));
}

static void verify(const moy_index_t *ix) {
    CHECK(moy_index_count(ix) == m_count);
    CHECK(moy_index_slots(ix) == m_used);
    for (uint32_t s = 0; s < m_used; s++) {
        verify_row(ix, s);
    }
    CHECK(moy_index_at(ix, m_used) == 0u);
}

// -- the program --------------------------------------------------------------

typedef struct {
    const uint8_t *b;
    size_t n, i;
} input_t;

static uint8_t next(input_t *in) {
    return in->i < in->n ? in->b[in->i++] : 0u;
}

static size_t path_of(input_t *in, char *out) {
    size_t n = next(in) % (MAXP + 1u);
    for (size_t k = 0; k < n; k++) {
        out[k] = (char)next(in);
    }
    return n;
}

// A handle worth asking about: a live one, one that went stale, or any word.
static uint32_t handle_of(input_t *in) {
    uint8_t mode = next(in) % 4u;
    if (mode < 2u && m_count) {
        uint32_t k = next(in) % m_count;
        for (uint32_t s = 0; s < m_used; s++) {
            if (m_live[s] && k-- == 0u) {
                return m_handle(s);
            }
        }
    }
    if (mode == 2u) {
        return stale[next(in) % STALE_RING];
    }
    uint32_t h = 0;
    for (int k = 0; k < 4; k++) {
        h = (h << 8) | next(in);
    }
    return h;
}

// The model slot `p` is in afterwards, or -1. `at` is where the model holds
// it now: -1 for a path known absent, FIND to look.
#define FIND (-2)
static int intern(moy_index_t *ix, const char *p, size_t n, int at) {
    if (at == FIND) {
        at = m_find(p, n);
    }
    uint32_t h = 0;
    failed = 0;
    int rc = moy_index_intern(ix, p, n, &h);
    if (at >= 0) {
        CHECK(rc == MOY_INDEX_OK && h == m_handle((uint32_t)at));
        return at;
    }
    if (m_count == SLOTS) {
        CHECK(rc == MOY_INDEX_FULL);
        return -1;
    }
    if (rc == MOY_INDEX_NOMEM) {
        CHECK(failed);
        CHECK(moy_index_find(ix, p, n) == 0u);
        verify(ix);
        return -1;
    }
    CHECK(rc == MOY_INDEX_OK);
    const uint8_t *hole = memchr(m_live, 0, m_used);
    uint32_t s = hole ? (uint32_t)(hole - m_live) : m_used;
    if (s == m_used) {
        m_gen[s] = 1u;
        m_used++;
    }
    m_live[s] = 1u;
    m_len[s] = (uint8_t)n;
    memcpy(m_path[s], p, n);
    int16_t *head = m_chain(p, n);
    m_next[s] = *head;
    *head = (int16_t)s;
    m_count++;
    CHECK(h == m_handle(s));
    CHECK(h != 0u && h < (1u << 30));
    verify_row(ix, s);
    return (int)s;
}

static void release(moy_index_t *ix, uint32_t h) {
    int ok = m_valid(h);
    CHECK(moy_index_release(ix, h) == (ok ? MOY_INDEX_OK : MOY_INDEX_STALE));
    if (!ok) {
        return;
    }
    uint32_t s = h & (SLOTS - 1u);
    int16_t *link = m_chain(m_path[s], m_len[s]);
    while (*link != (int16_t)s) {
        link = &m_next[*link];
    }
    *link = m_next[s];
    m_live[s] = 0u;
    m_gen[s] = m_gen[s] >= MOY_INDEX_GEN_MAX ? 1u : m_gen[s] + 1u;
    m_count--;
    stale[stale_at++ % STALE_RING] = h;
    CHECK(!moy_index_valid(ix, h));
    verify_row(ix, s);
}

static void run(const uint8_t *data, size_t size) {
    m_reset();
    stale_at = serial = 0;
    memset(stale, 0, sizeof stale);
    fail_in = 0;
    live_bytes = 0;
    moy_index_t *ix = moy_index_new();
    CHECK(ix != NULL);
    input_t in = { data, size, 0 };
    unsigned ops = 0;
    while (in.i < in.n) {
        char p[MAXP];
        size_t n;
        uint32_t h;
        uint8_t op = next(&in) % 10u;
        switch (op) {
            case 0:
                n = path_of(&in, p);
                intern(ix, p, n, FIND);
                break;
            case 1: {
                n = path_of(&in, p);
                int at = m_find(p, n);
                CHECK(moy_index_find(ix, p, n)
                      == (at >= 0 ? m_handle((uint32_t)at) : 0u));
                break;
            }
            case 2: {
                const char *q;
                size_t qn;
                h = handle_of(&in);
                int rc = moy_index_path(ix, h, &q, &qn);
                CHECK(rc == (m_valid(h) ? MOY_INDEX_OK : MOY_INDEX_STALE));
                break;
            }
            case 3:
                h = handle_of(&in);
                CHECK(!!moy_index_valid(ix, h) == m_valid(h));
                break;
            case 4:
                release(ix, handle_of(&in));
                break;
            case 5:
                verify(ix);
                break;
            case 6:
                fail_in = next(&in) % 8u;
                break;
            case 7:                         // towards a full table
                for (unsigned k = (next(&in) + 1u) * 20u; k; k--) {
                    n = (size_t)snprintf(p, sizeof p, "#%u", serial++ % 5000u);
                    intern(ix, p, n, FIND);
                }
                break;
            case 8:                         // round one slot's generations,
                if (m_count) {              // all the way round on a 255
                    uint32_t s = 0, k = next(&in) % m_count;
                    for (; s < m_used; s++) {
                        if (m_live[s] && k-- == 0u) {
                            break;
                        }
                    }
                    char q[MAXP];
                    size_t qn = m_len[s];
                    memcpy(q, m_path[s], qn);
                    fail_in = 0;
                    uint8_t k8 = next(&in);
                    unsigned r = k8 == 255u ? MOY_INDEX_GEN_MAX + 1u
                                            : (k8 % 16u + 1u) * 64u;
                    for (int at = (int)s; r; r--) {
                        release(ix, m_handle((uint32_t)at));
                        at = intern(ix, q, qn, -1);
                        CHECK(at >= 0);
                    }
                }
                break;
            default:                        // a new table; the old one freed
                verify(ix);
                moy_index_free(ix);
                CHECK(live_bytes == 0);
                m_reset();
                fail_in = 0;
                ix = moy_index_new();
                CHECK(ix != NULL);
                break;
        }
        if ((++ops & 15u) == 0u) {
            verify(ix);
        }
    }
    verify(ix);
    moy_index_free(ix);
    CHECK(live_bytes == 0);
}

#ifdef MOY_INDEX_FUZZ_MAIN
static uint32_t rng;

static uint32_t xorshift(void) {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

int main(int argc, char **argv) {
    uint32_t seed = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 1u;
    unsigned long runs = argc > 2 ? strtoul(argv[2], NULL, 0) : 1000ul;
    static uint8_t buf[4096];
    rng = seed ? seed : 1u;
    for (unsigned long r = 0; r < runs; r++) {
        size_t n = xorshift() % sizeof buf;
        for (size_t k = 0; k < n; k++) {
            uint32_t x = xorshift();
            // Mostly small operands, so paths repeat and handles land.
            buf[k] = (uint8_t)((x & 0x300u) ? (x % 7u) : (x >> 24));
        }
        run(buf, n);
    }
    printf("fuzz_index: %lu programs, seed %u, ok\n", runs, seed);
    return 0;
}
#else
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    run(data, size);
    return 0;
}
#endif
