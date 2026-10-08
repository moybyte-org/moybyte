// moy_perf: the PERF line, ONE format and ONE accounting path for every board
// (#206 item 2; docs/kernel_survival_2026-10.md §7.3). runtime/perf_line.py
// keeps the parser the host tools read; this is the only writer, and
// tests/test_moy_loop.py holds every line it can print to that parser.
//
// The field set is the union, the same on every board, in this order. A field
// a board cannot measure prints `-`, never 0 (the 2026-08-22 doctrine: a
// frozen 0 is also what a broken lever reads as). A compound field joins its
// parts with `/`, each part absent on its own. The cart title is slugged to
// one token, because both readers split the line on whitespace.
//
// Nothing is formatted while PERF DIAG is off: the window closes every period
// either way, so the first line after the diag comes on is a whole period of
// its own.

#ifndef MOY_PERF_H
#define MOY_PERF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    MOY_PF_CART, MOY_PF_FPS, MOY_PF_NET, MOY_PF_TICK, MOY_PF_MISS, MOY_PF_BUSY,
    MOY_PF_DRAW, MOY_PF_FLUSH, MOY_PF_LOGIC, MOY_PF_RENDER, MOY_PF_CHROME,
    MOY_PF_WMR, MOY_PF_WMW, MOY_PF_WMS, MOY_PF_PPA, MOY_PF_FENCE_MS,
    MOY_PF_GFENCE_MS, MOY_PF_HOME, MOY_PF_GC, MOY_PF_FIELDS,
};

#define MOY_PF_PARTS 5
#define MOY_PF_CART_MAX 40
#define MOY_PERF_LINE_MAX 320

typedef struct {
    double v[MOY_PF_FIELDS][MOY_PF_PARTS];
    uint8_t has[MOY_PF_FIELDS];         // a bit per part present
    char cart[MOY_PF_CART_MAX];         // the title, slugged ("" prints as "?")
} moy_perf_values_t;

void moy_perf_clear(moy_perf_values_t *v);
size_t moy_perf_values_size(void);
// A field absent again, every part.
void moy_perf_unset(moy_perf_values_t *v, int field);
// A field by its name, or -1.
int moy_perf_field(const char *name);
// One part of a field; `part` 0 for a scalar.
void moy_perf_set(moy_perf_values_t *v, int field, int part, double value);
void moy_perf_set_cart(moy_perf_values_t *v, const char *title);
const char *moy_perf_field_name(int field);
int moy_perf_field_parts(int field);
// The line into `out` (at most cap bytes, terminated); its length.
size_t moy_perf_format(const moy_perf_values_t *v, char *out, size_t cap);

// The overlap engine's cumulative counters (moy_present.h's order: deferred,
// obsolete, fences, fence_us, game_n, game_us, timeouts), a slot a path does
// not have reading absent.
#define MOY_PERF_OVERLAP 7
typedef struct {
    uint32_t v[MOY_PERF_OVERLAP];
    uint8_t has;                        // a bit per slot
} moy_perf_overlap_t;

// The sampler: accumulate every frame, and once a period, while the diag is
// on, write the line from this window and the console's latest values.
typedef struct {
    uint32_t period_ms, secs, at;
    uint32_t n, busy, drawn_at;
    bool have_ov, have_gc;
    moy_perf_overlap_t ov;
    uint32_t gc[3];
    moy_perf_values_t console;          // the console's half, as it pushed it
} moy_perf_t;

void moy_perf_init(moy_perf_t *p, uint32_t now, uint32_t period_ms);
// One frame. Answers true when the period closed (the caller samples).
bool moy_perf_account(moy_perf_t *p, uint32_t now, uint32_t elapsed);
// The period's line from the kernel's own counts and the console's pushed
// values; `overlap` and `gc` NULL where the board has none.
size_t moy_perf_sample(moy_perf_t *p, uint32_t drawn, const moy_perf_overlap_t *overlap,
                       const uint32_t gc[3], char *out, size_t cap);
// The window closes with no line (the diag is off): baselines are dropped.
void moy_perf_skip(moy_perf_t *p, uint32_t drawn);
void moy_perf_close(moy_perf_t *p, uint32_t now, uint32_t drawn);

#endif // MOY_PERF_H
