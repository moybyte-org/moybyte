// The input table under a random walk: sources made and moved between players,
// held bits, keys and one-shot keys written, frames begun, edges kept and
// taken. A model beside the C says what every read must answer; ASan and
// UBSan say what the C did to memory. A seeded driver (`fuzz_input SEED STEPS
// SEEDS`) or libFuzzer (`-DMOY_LIBFUZZER`) over the same step.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_input.h"

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "fuzz_input: %s:%d %s\n", \
                                          __FILE__, __LINE__, #c); abort(); } } while (0)

static uint32_t rng;

static uint32_t next(void) {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

// The model: what each source holds, and the union's edges.
typedef struct {
    uint32_t h;
    uint32_t held;
    uint8_t player;
    int32_t key;
    uint16_t q[MOY_INPUT_KEYQ];
    int qn;
    int queued;
    uint16_t qlast;
} msrc_t;

static msrc_t src[MOY_INPUT_SOURCES];
static int nsrc;
static uint32_t m_held, m_last, m_pressed, m_kept;
static int m_taken;
static int32_t m_last_key;
static int m_key_src;

static void model_key(int s, int32_t key) {
    int32_t old = src[s].key;
    src[s].key = key;
    if (key) {
        if (key != old || m_key_src < 0) {
            m_key_src = s;
            m_last_key = key;
        } else if (m_key_src == s) {
            m_last_key = key;
        }
    } else if (m_key_src == s) {
        m_key_src = -1;
        m_last_key = 0;
        for (int i = 0; i < nsrc; i++) {
            if (src[i].key) {
                m_key_src = i;
                m_last_key = src[i].key;
                break;
            }
        }
    }
}

static int32_t model_dequeue(msrc_t *s) {
    if (s->qn == 0 || s->q[0] == s->qlast) {
        s->qlast = 0;
        return 0;
    }
    uint16_t k = s->q[0];
    memmove(&s->q[0], &s->q[1], (size_t)(s->qn - 1) * sizeof(s->q[0]));
    s->qn--;
    s->qlast = k;
    return k;
}

static void step(moy_input_t *t, uint8_t nb) {
    uint32_t r = next();
    int s = nsrc ? (int)(next() % (uint32_t)nsrc) : 0;
    switch (r % 12u) {
        case 0: {
            char name[8];
            snprintf(name, sizeof(name), "s%u", (unsigned)(next() % 16u));
            uint32_t h;
            uint8_t player = (uint8_t)(next() % (MOY_INPUT_PLAYERS + 1u));
            int rc = moy_input_source(t, name, player, &h);
            int known = -1;
            for (int i = 0; i < nsrc; i++) {
                char got[MOY_INPUT_NAME + 1];
                CHECK(moy_input_source_name(t, src[i].h, got, sizeof(got)) == MOY_INPUT_OK);
                if (strcmp(got, name) == 0) {
                    known = i;
                }
            }
            if (player >= MOY_INPUT_PLAYERS) {
                CHECK(rc == MOY_INPUT_BAD);
            } else if (known >= 0) {
                CHECK(rc == MOY_INPUT_OK && h == src[known].h);
            } else if (nsrc == (int)MOY_INPUT_SOURCES) {
                CHECK(rc == MOY_INPUT_FULL);
            } else {
                CHECK(rc == MOY_INPUT_OK);
                memset(&src[nsrc], 0, sizeof(src[nsrc]));
                src[nsrc].h = h;
                src[nsrc].player = player;
                nsrc++;
            }
            break;
        }
        case 1:
        case 2: {
            uint8_t b = (uint8_t)(next() % 16u);
            int on = (int)(next() & 1u);
            int rc = moy_input_set_held(t, src[s].h, b, on);
            if (b >= nb) {
                CHECK(rc == MOY_INPUT_BAD);
            } else {
                CHECK(rc == MOY_INPUT_OK);
                src[s].held = on ? (src[s].held | (1u << b)) : (src[s].held & ~(1u << b));
            }
            break;
        }
        case 3:
            CHECK(moy_input_release(t, src[s].h) == MOY_INPUT_OK);
            src[s].held = 0;
            break;
        case 4: {
            int32_t k = (next() & 1u) ? (int32_t)(next() % 4u) : 0;
            CHECK(moy_input_set_key(t, src[s].h, k) == MOY_INPUT_OK);
            src[s].queued = 0;
            src[s].qn = 0;
            model_key(s, k);
            break;
        }
        case 5: {
            int32_t k = 1 + (int32_t)(next() % 3u);
            int rc = moy_input_key(t, src[s].h, k);
            src[s].queued = 1;
            if (src[s].qn < (int)MOY_INPUT_KEYQ) {
                CHECK(rc == MOY_INPUT_OK);
                src[s].q[src[s].qn++] = (uint16_t)k;
            } else {
                CHECK(rc == MOY_INPUT_FULL);
            }
            break;
        }
        case 6:
        case 7: {
            moy_input_begin_frame(t);
            uint32_t held = 0;
            for (int i = 0; i < nsrc; i++) {
                if (src[i].queued) {
                    model_key(i, model_dequeue(&src[i]));
                }
                held |= src[i].held;
            }
            m_pressed = held & ~m_last;
            m_last = held;
            m_held = held;
            m_taken = 0;
            break;
        }
        case 8:
            moy_input_release_all(t);
            for (int i = 0; i < nsrc; i++) {
                src[i].held = 0;
            }
            m_held = 0;
            break;
        case 9:
            moy_input_keep_edges(t);
            m_kept |= m_pressed;
            break;
        case 10:
            moy_input_tick_edges(t);
            if (m_taken) {
                m_pressed = 0;
            } else {
                m_taken = 1;
                m_pressed |= m_kept;
                m_kept = 0;
            }
            break;
        case 11: {
            uint8_t p = (uint8_t)(next() % MOY_INPUT_PLAYERS);
            CHECK(moy_input_set_player(t, src[s].h, p) == MOY_INPUT_OK);
            src[s].player = p;
            break;
        }
    }
    uint32_t h, p;
    moy_input_masks(t, MOY_INPUT_UNION, &h, &p);
    CHECK(h == m_held);
    CHECK(p == m_pressed);
    CHECK(moy_input_kept(t) == m_kept);
    CHECK(moy_input_last_key(t) == m_last_key);
    CHECK(h == (h & ((1u << nb) - 1u)));
    uint8_t players[MOY_INPUT_SOURCES];
    uint8_t n = moy_input_players(t, players);
    CHECK(n >= 1 && n <= nsrc);
}

static void run(uint32_t seed, long steps) {
    rng = seed ? seed : 1u;
    uint8_t nb = (seed & 1u) ? MOY_INPUT_BUTTONS : MOY_INPUT_HOST_BUTTONS;
    moy_input_t *t = moy_input_new(nb);
    CHECK(t != NULL);
    memset(src, 0, sizeof(src));
    nsrc = 1;
    CHECK(moy_input_source(t, "local", 0, &src[0].h) == MOY_INPUT_OK);
    m_held = m_last = m_pressed = m_kept = 0;
    m_taken = 0;
    m_last_key = 0;
    m_key_src = -1;
    for (long i = 0; i < steps; i++) {
        step(t, nb);
    }
    moy_input_ptr_t ptr;
    moy_input_ptr_init(&ptr, 320, 240, 100, 5);
    moy_input_ptr_move(&ptr, -1000, 1000, MOY_INPUT_TICKS_PERIOD - 1u);
    CHECK(ptr.x == 0 && ptr.y == 239 && ptr.visible);
    moy_input_ptr_tick(&ptr, 98);
    CHECK(ptr.visible);
    moy_input_ptr_tick(&ptr, 99);
    CHECK(!ptr.visible);
    moy_input_free(t);
}

#ifdef MOY_LIBFUZZER
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t n) {
    uint32_t seed = 1u;
    for (size_t i = 0; i < n && i < 4; i++) {
        seed = seed * 31u + data[i];
    }
    run(seed, 400);
    return 0;
}
#else
int main(int argc, char **argv) {
    uint32_t seed = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 1u;
    long steps = argc > 2 ? strtol(argv[2], NULL, 0) : 20000;
    long seeds = argc > 3 ? strtol(argv[3], NULL, 0) : 16;
    for (long k = 0; k < seeds; k++) {
        run(seed + (uint32_t)k, steps);
    }
    printf("fuzz_input: %ld seeds x %ld steps\n", seeds, steps);
    return 0;
}
#endif
