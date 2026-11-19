// Sakura C: the Sakura wallpaper (moybyte.sakura.moy, moybyte.sakura_lua.moy)
// in C, built from this one source to main.wasm and to a native module per
// chip (sdk/include/moybyte/cart.h). The backdrop is images/bg.moyimg and the
// canopy's shedding points scenes/blossoms.moyscene, the twins' own bytes
// (tools/import_sakura_bg.py writes all three carts' copies); the petals'
// colours are sprites.moygfx's, a column of three shades per blossom choice.
// The arithmetic is the twins', in float.

#include <math.h>
#include <stdint.h>

#include "moybyte/cart.h"

#define W 320
#define H 240
#define PETALS 256
#define EMITS 256
#define R 52.0f

typedef struct {
    float x, y, fall, phase, amp;
    int shade;
} petal_t;

static const char *const BLOSSOMS[] = { "pink", "white", "peach", "mixed" };

static moy_actor_t emit[EMITS];
static int emits;
static float sin_lut[256];
static int32_t lay;
static petal_t petals[PETALS];
static int n_petals;
static int base;

static int same(const char *a, int an, const char *b) {
    int i = 0;
    for (; i < an && b[i]; i++) {
        if (a[i] != b[i]) {
            return 0;
        }
    }
    return i == an && b[i] == 0;
}

// cfg(key) as a number, `dflt` when absent or no number.
static float cfg_num(const char *key, float dflt) {
    char buf[24];
    int k = 0;
    while (key[k]) {
        k++;
    }
    int32_t n = moy_cfg(key, k, buf, sizeof buf - 1);
    if (n < 0) {
        return dflt;
    }
    buf[n < (int32_t)sizeof buf - 1 ? n : (int32_t)sizeof buf - 1] = 0;
    float v = 0.0f, scale = 0.0f;
    int neg = 0, any = 0;
    for (const char *p = buf; *p; p++) {
        if (*p == '-' && p == buf) {
            neg = 1;
        } else if (*p == '.' && scale == 0.0f) {
            scale = 1.0f;
        } else if (*p >= '0' && *p <= '9') {
            any = 1;
            if (scale > 0.0f) {
                scale *= 0.1f;
                v += (float)(*p - '0') * scale;
            } else {
                v = v * 10.0f + (float)(*p - '0');
            }
        } else {
            break;
        }
    }
    return any ? (neg ? -v : v) : dflt;
}

// A run's blossom colour fixes the sheet column; unknown names fall back to pink.
static int blossom_base(void) {
    char buf[16];
    int32_t n = moy_cfg("blossom", 7, buf, sizeof buf);
    for (int i = 0; n > 0 && n <= (int32_t)sizeof buf && i < 4; i++) {
        if (same(buf, n, BLOSSOMS[i])) {
            return i * 3;
        }
    }
    return 0;
}

static float wave(float turn) {
    return sin_lut[(int)floorf(turn * 256.0f) & 255];
}

// A petal at a random canopy blossom: `fresh` starts it at the cluster, else
// down the column, so the air starts full.
static void shed(petal_t *p, int fresh) {
    float ex, ey;
    if (emits > 0) {
        const moy_actor_t *a = &emit[(int)moy_rnd((float)emits) % emits];
        ex = (float)a->x;
        ey = (float)a->y;
    } else {
        ex = moy_rnd((float)W);
        ey = 0.0f;
    }
    p->x = ex + moy_rnd(7.0f) - 3.0f;
    p->y = fresh ? ey - 2.0f : ey + moy_rnd((float)H - ey + 10.0f);
    p->phase = moy_rnd(1.0f);
}

void init(void) {
    int32_t n = scene(0, 0, emit, sizeof emit);
    emits = n < 0 ? 0 : n > EMITS ? EMITS : n;
    for (int i = 0; i < 256; i++) {
        sin_lut[i] = sinf((float)i / 256.0f * 6.2831853f);
    }
    if (lay == 0) {
        lay = moy_make_layer(W, H);
    }
    int32_t bg = image("bg", 2);
    if (bg != 0 && lay != 0) {
        moy_target(lay);
        image_spr(bg, 0, 0);
        moy_target(0);
    }
    int count = (int)cfg_num("petal_count", 120.0f);
    float fall = cfg_num("fall_speed", 30.0f);
    n_petals = count < 0 ? 0 : count > PETALS ? PETALS : count;
    base = blossom_base();
    for (int i = 0; i < n_petals; i++) {
        petal_t *p = &petals[i];
        p->shade = i % 3;
        p->fall = fall * (1.0f - 0.18f * (float)p->shade) * (0.7f + moy_rnd(0.6f));
        p->amp = 4.0f + moy_rnd(9.0f);
        shed(p, 0);
    }
}

void update(float dt) {
    if (dt > 0.1f) {
        dt = 0.1f;
    }
    float breeze = cfg_num("breeze", 18.0f);
    int32_t t[4];
    float cx = -999.0f, cy = -999.0f;
    if (moy_touch(t)) {
        cx = (float)t[0];
        cy = (float)t[1];
    }
    for (int i = 0; i < n_petals; i++) {
        petal_t *p = &petals[i];
        float shade = (float)p->shade;
        p->phase += dt * (0.32f + 0.06f * shade);
        float sway = wave(p->phase) * p->amp;
        p->x += (breeze * (1.0f - 0.15f * shade) + sway) * dt;
        p->y += p->fall * dt;
        float dx = p->x - cx, dy = p->y - cy;
        if (-R < dx && dx < R && -R < dy && dy < R) {
            float far = dx >= 0.0f ? dx : -dx;
            float ady = dy >= 0.0f ? dy : -dy;
            if (ady > far) {
                far = ady;
            }
            float k = (R - far) / R * 130.0f;
            float inv = 1.0f / (far + 4.0f);
            p->x += dx * inv * k * dt;
            p->y += dy * inv * k * dt;
        }
        if (p->y > (float)H + 4.0f) {
            shed(p, 1);
        } else if (p->x < -8.0f) {
            p->x += (float)W + 16.0f;
        } else if (p->x > (float)W + 8.0f) {
            p->x -= (float)W + 16.0f;
        }
    }
}

void draw(void) {
    moy_draw_layer(lay, 0, 0);
    for (int i = 0; i < n_petals; i++) {
        const petal_t *p = &petals[i];
        moy_spr(base + p->shade, (int32_t)p->x, (int32_t)p->y, 0, 1, 0);
    }
}
