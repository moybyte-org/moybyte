// moy_perf: the PERF line's one writer. moy_perf.h has the contract.

#include <string.h>

#include "moy_perf.h"

typedef struct {
    const char *name;
    uint8_t parts;
    uint8_t decimals;           // 0 or 1
    const char *unit;           // after the number: busy's "ms"
} field_t;

// runtime/perf_line.py's FIELDS, in its order: the parser's half of the
// contract reads exactly these names and units. Numbers are written here, not
// by printf, so no board's libc picks the rounding: half away from zero.
static const field_t FIELDS[MOY_PF_FIELDS] = {
    {"cart", 1, 0, ""},
    {"fps", 2, 0, ""},
    {"net", 1, 0, ""},
    {"tick", 2, 0, ""},
    {"miss", 1, 0, ""},
    {"busy", 1, 0, "ms"},
    {"draw", 1, 0, ""},
    {"flush", 1, 0, ""},
    {"logic", 1, 0, ""},
    {"render", 1, 0, ""},
    {"chrome", 1, 0, ""},
    {"wmr", 1, 0, ""},
    {"wmw", 1, 0, ""},
    {"wms", 1, 0, ""},
    {"ppa", 5, 0, ""},
    {"fence_ms", 1, 1, ""},
    {"gfence_ms", 1, 1, ""},
    {"home", 3, 0, ""},
    {"gc", 3, 0, ""},
};

// `v` with `decimals` places into tmp (at least 24 bytes).
static void number(char *tmp, double v, int decimals) {
    bool neg = v < 0;
    if (neg) {
        v = -v;
    }
    uint64_t scale = decimals ? 10u : 1u;
    uint64_t q = (uint64_t)(v * (double)scale + 0.5);
    char digits[24];
    int n = 0;
    uint64_t whole = q / scale;
    do {
        digits[n++] = (char)('0' + whole % 10u);
        whole /= 10u;
    } while (whole && n < 20);
    char *p = tmp;
    if (neg && q) {
        *p++ = '-';
    }
    while (n) {
        *p++ = digits[--n];
    }
    if (decimals) {
        *p++ = '.';
        *p++ = (char)('0' + q % 10u);
    }
    *p = 0;
}

void moy_perf_clear(moy_perf_values_t *v) {
    memset(v, 0, sizeof(*v));
}

size_t moy_perf_values_size(void) {
    return sizeof(moy_perf_values_t);
}

void moy_perf_unset(moy_perf_values_t *v, int field) {
    if (field >= 0 && field < MOY_PF_FIELDS) {
        v->has[field] = 0;
    }
}

int moy_perf_field(const char *name) {
    for (int f = 0; f < MOY_PF_FIELDS; f++) {
        if (strcmp(name, FIELDS[f].name) == 0) {
            return f;
        }
    }
    return -1;
}

void moy_perf_set(moy_perf_values_t *v, int field, int part, double value) {
    if (field < 0 || field >= MOY_PF_FIELDS || part < 0 || part >= MOY_PF_PARTS) {
        return;
    }
    v->v[field][part] = value;
    v->has[field] |= (uint8_t)(1u << part);
}

void moy_perf_set_cart(moy_perf_values_t *v, const char *title) {
    if (title == NULL) {
        v->cart[0] = 0;
        v->has[MOY_PF_CART] = 0;
        return;
    }
    size_t i = 0;
    for (; title[i] && i + 1 < sizeof(v->cart); i++) {
        char c = title[i];
        v->cart[i] = (c == ' ' || c == '\t' || c == '\n' || c == '\r') ? '_' : c;
    }
    v->cart[i] = 0;
    v->has[MOY_PF_CART] = 1;
}

const char *moy_perf_field_name(int field) {
    return field >= 0 && field < MOY_PF_FIELDS ? FIELDS[field].name : NULL;
}

int moy_perf_field_parts(int field) {
    return field >= 0 && field < MOY_PF_FIELDS ? FIELDS[field].parts : 0;
}

typedef struct {
    char *p;
    size_t cap, n;
} out_t;

static void put(out_t *o, const char *s) {
    while (*s && o->n + 1 < o->cap) {
        o->p[o->n++] = *s++;
    }
    o->p[o->n] = 0;
}

size_t moy_perf_format(const moy_perf_values_t *v, char *out, size_t cap) {
    out_t o = {out, cap, 0};
    if (cap == 0) {
        return 0;
    }
    out[0] = 0;
    put(&o, "PERF");
    char tmp[32];
    for (int f = 0; f < MOY_PF_FIELDS; f++) {
        put(&o, " ");
        put(&o, FIELDS[f].name);
        put(&o, "=");
        if (v->has[f] == 0) {
            put(&o, "-");
            continue;
        }
        if (f == MOY_PF_CART) {
            put(&o, v->cart[0] ? v->cart : "?");
            continue;
        }
        int parts = moy_perf_field_parts(f);
        for (int i = 0; i < parts; i++) {
            if (i) {
                put(&o, "/");
            }
            if (v->has[f] & (1u << i)) {
                number(tmp, v->v[f][i], FIELDS[f].decimals);
                put(&o, tmp);
                put(&o, FIELDS[f].unit);
            } else {
                put(&o, "-");
            }
        }
    }
    return o.n;
}

void moy_perf_init(moy_perf_t *p, uint32_t now, uint32_t period_ms) {
    memset(p, 0, sizeof(*p));
    p->period_ms = period_ms ? period_ms : 2000;
    p->secs = p->period_ms / 1000u ? p->period_ms / 1000u : 1u;
    p->at = now + p->period_ms;
}

bool moy_perf_account(moy_perf_t *p, uint32_t now, uint32_t elapsed) {
    p->n++;
    p->busy += elapsed;
    return (int32_t)(now - p->at) >= 0;
}

void moy_perf_close(moy_perf_t *p, uint32_t now, uint32_t drawn) {
    p->at = now + p->period_ms;
    p->n = 0;
    p->busy = 0;
    p->drawn_at = drawn;
}

void moy_perf_skip(moy_perf_t *p, uint32_t drawn) {
    (void)drawn;
    p->have_ov = false;
    p->have_gc = false;
}

size_t moy_perf_sample(moy_perf_t *p, uint32_t drawn, const moy_perf_overlap_t *overlap,
                       const uint32_t gc[3], char *out, size_t cap) {
    moy_perf_values_t v = p->console;
    v.has[MOY_PF_FPS] = 0;
    moy_perf_set(&v, MOY_PF_FPS, 0, (double)((drawn - p->drawn_at) / p->secs));
    moy_perf_set(&v, MOY_PF_FPS, 1, (double)(p->n / p->secs));
    v.has[MOY_PF_BUSY] = 0;
    moy_perf_set(&v, MOY_PF_BUSY, 0, (double)(p->busy / (p->n ? p->n : 1u)));
    v.has[MOY_PF_GC] = 0;
    if (gc != NULL) {
        if (p->have_gc) {
            moy_perf_set(&v, MOY_PF_GC, 0, (double)(uint32_t)(gc[0] - p->gc[0]));
            moy_perf_set(&v, MOY_PF_GC, 1, (double)(uint32_t)(gc[1] - p->gc[1]));
            moy_perf_set(&v, MOY_PF_GC, 2, (double)gc[2]);
        }
        memcpy(p->gc, gc, sizeof(p->gc));
        p->have_gc = true;
    }
    v.has[MOY_PF_PPA] = v.has[MOY_PF_FENCE_MS] = v.has[MOY_PF_GFENCE_MS] = 0;
    if (overlap != NULL) {
        if (p->have_ov) {
            // ppa= is deferred/obsolete/fences/game_n/timeouts (slots 0,1,2,4,6);
            // the two _us slots are the fences' ms.
            static const uint8_t PPA[5] = {0, 1, 2, 4, 6};
            for (int i = 0; i < 5; i++) {
                int s = PPA[i];
                if ((overlap->has & p->ov.has) & (1u << s)) {
                    moy_perf_set(&v, MOY_PF_PPA, i, (double)(uint32_t)(overlap->v[s] - p->ov.v[s]));
                }
            }
            if ((overlap->has & p->ov.has) & (1u << 3)) {
                moy_perf_set(&v, MOY_PF_FENCE_MS, 0,
                             (double)(uint32_t)(overlap->v[3] - p->ov.v[3]) / 1000.0);
            }
            if ((overlap->has & p->ov.has) & (1u << 5)) {
                moy_perf_set(&v, MOY_PF_GFENCE_MS, 0,
                             (double)(uint32_t)(overlap->v[5] - p->ov.v[5]) / 1000.0);
            }
        }
        p->ov = *overlap;
        p->have_ov = true;
    }
    // The windowed WM's meters are TAKEN: only a frame that drew that layer
    // this window may answer, so they clear once printed.
    p->console.has[MOY_PF_WMR] = p->console.has[MOY_PF_WMW] = p->console.has[MOY_PF_WMS] = 0;
    // The miss count is a window's; the console pushes it anew.
    p->console.has[MOY_PF_MISS] = 0;
    p->console.has[MOY_PF_NET] = 0;
    return moy_perf_format(&v, out, cap);
}
