// moy_recovery: the recovery screen's raster. The header has the design.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "moy_recovery.h"
#include "moy_str.h"

// libmoy's font (native/moy_gfx/libmoy/moy_data.c): 96 glyphs from 0x20, eight
// bytes each, one per COLUMN, bit k the row k from the top.
extern const uint8_t moy_font_data[96 * 8];

#define C_BG      0x18C3u
#define C_BAR     0xA000u
#define C_TEXT    0xFFFFu
#define C_DIM     0xAD55u
#define C_BUTTON  0x39E7u
#define C_SEL     0xFD20u
#define C_SEL_TXT 0x0000u

// The choices' labels, in order (native/moy_spine/strings/en.json).
static const int16_t CHOICES[MOY_RV_CHOICES] = {
    MOY_S_RECOVERY_RETRY, MOY_S_RECOVERY_SAFE, MOY_S_RECOVERY_REPL,
};

// The serial protocol's name for a choice: English whatever pack draws, since
// tools/kernel_gate.py parses it.
const char *moy_recovery_choice_name(int choice) {
    return (choice >= 0 && choice < MOY_RV_CHOICES) ? moy_str_en[CHOICES[choice]]
                                                    : moy_str_en[MOY_S_RECOVERY_UNKNOWN];
}

void moy_rgeom_init(moy_rgeom_t *g, int w, int h, int rot, int swap) {
    g->w = w;
    g->h = h;
    g->rot = rot;
    g->swap = swap;
    if (rot == 90 || rot == 270) {
        g->fb_w = h;
        g->fb_h = w;
    } else {
        g->fb_w = w;
        g->fb_h = h;
    }
    g->scale = (w + 160) / 320;
    if (g->scale < 1) {
        g->scale = 1;
    }
}

// ---- the layout -------------------------------------------------------------

typedef struct {
    int cell, margin, line_y, line_step, max_lines;
    int btn_y, btn_h, btn_w, hint_y;
} layout_t;

static void layout(const moy_rgeom_t *g, layout_t *l) {
    int c = 8 * g->scale;
    l->cell = c;
    l->margin = c;
    l->line_y = 3 * c;
    l->line_step = c + 2 * g->scale;
    l->btn_h = 3 * c;
    l->btn_y = g->h - 5 * c;
    l->btn_w = (g->w - 4 * l->margin) / MOY_RV_CHOICES;
    l->hint_y = g->h - c - c / 2;
    l->max_lines = (l->btn_y - c - l->line_y) / l->line_step;
    if (l->max_lines > MOY_RV_LINES) {
        l->max_lines = MOY_RV_LINES;
    }
}

int moy_recovery_hit(const moy_rgeom_t *g, int x, int y) {
    layout_t l;
    layout(g, &l);
    if (y < l.btn_y || y >= l.btn_y + l.btn_h) {
        return -1;
    }
    for (int i = 0; i < MOY_RV_CHOICES; i++) {
        int bx = l.margin + i * (l.btn_w + l.margin);
        if (x >= bx && x < bx + l.btn_w) {
            return i;
        }
    }
    return -1;
}

// ---- the raster -------------------------------------------------------------

static void fill(uint16_t *fb, const moy_rgeom_t *g, int x, int y, int w, int h, uint16_t c) {
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > g->w) { w = g->w - x; }
    if (y + h > g->h) { h = g->h - y; }
    if (w <= 0 || h <= 0) {
        return;
    }
    uint16_t px = g->swap ? (uint16_t)((c >> 8) | (c << 8)) : c;
    for (int j = y; j < y + h; j++) {
        for (int i = x; i < x + w; i++) {
            int fx, fy;
            switch (g->rot) {
                case 90:  fx = j;              fy = g->w - 1 - i; break;
                case 180: fx = g->w - 1 - i;   fy = g->h - 1 - j; break;
                case 270: fx = g->h - 1 - j;   fy = i;            break;
                default:  fx = i;              fy = j;            break;
            }
            fb[(size_t)fy * (size_t)g->fb_w + (size_t)fx] = px;
        }
    }
}

void moy_recovery_fill(uint16_t *fb, const moy_rgeom_t *g, int x, int y, int w, int h,
                       uint16_t c) {
    fill(fb, g, x, y, w, h, c);
}

// Text from (x, y), at most `cols` glyphs.
static void text(uint16_t *fb, const moy_rgeom_t *g, int x, int y, const char *s,
                 int cols, uint16_t c) {
    int sc = g->scale;
    for (int n = 0; s[n] != '\0' && n < cols; n++) {
        int code = (unsigned char)s[n];
        if (code < 0x20 || code > 0x7F) {
            code = '?';
        }
        const uint8_t *gl = moy_font_data + (code - 0x20) * 8;
        for (int col = 0; col < 8; col++) {
            for (int row = 0, bits = gl[col]; bits; row++, bits >>= 1) {
                if (bits & 1) {
                    fill(fb, g, x + (n * 8 + col) * sc, y + row * sc, sc, sc, c);
                }
            }
        }
    }
}

void moy_recovery_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v) {
    layout_t l;
    layout(g, &l);
    int c = l.cell, cols = (g->w - 2 * l.margin) / c;
    fill(fb, g, 0, 0, g->w, g->h, C_BG);
    fill(fb, g, 0, 0, g->w, 2 * c, C_BAR);
    text(fb, g, l.margin, c / 2, moy_str(MOY_S_RECOVERY_TITLE), cols, C_TEXT);
    int n = v->nlines < l.max_lines ? v->nlines : l.max_lines;
    for (int i = 0; i < n; i++) {
        text(fb, g, l.margin, l.line_y + i * l.line_step, v->line[i], cols,
             i == 0 ? C_TEXT : C_DIM);
    }
    for (int i = 0; i < MOY_RV_CHOICES; i++) {
        int bx = l.margin + i * (l.btn_w + l.margin);
        int sel = i == v->sel;
        fill(fb, g, bx, l.btn_y, l.btn_w, l.btn_h, sel ? C_SEL : C_BUTTON);
        const char *label = moy_str(CHOICES[i]);
        int len = (int)strlen(label);
        text(fb, g, bx + (l.btn_w - len * c) / 2, l.btn_y + c, label, len,
             sel ? C_SEL_TXT : C_TEXT);
    }
    text(fb, g, l.margin, l.hint_y, v->hint, cols, C_DIM);
}

// The kernel's PLAIN screen: the bar with its title, the lines, the hint,
// and no choices -- what the kernel shows on its own while no VM runs (the
// web console's address, docs/kernel_survival_2026-10.md §13 answer 8).
void moy_recovery_render_plain(uint16_t *fb, const moy_rgeom_t *g, const char *title,
                               const moy_rview_t *v) {
    layout_t l;
    layout(g, &l);
    int c = l.cell, cols = (g->w - 2 * l.margin) / c;
    fill(fb, g, 0, 0, g->w, g->h, C_BG);
    fill(fb, g, 0, 0, g->w, 2 * c, C_BAR);
    text(fb, g, l.margin, c / 2, title, cols, C_TEXT);
    int max = (l.hint_y - c - l.line_y) / l.line_step;
    int n = v->nlines < max ? v->nlines : max;
    for (int i = 0; i < n; i++) {
        text(fb, g, l.margin, l.line_y + i * l.line_step, v->line[i], cols,
             i == 0 ? C_TEXT : C_DIM);
    }
    text(fb, g, l.margin, l.hint_y, v->hint, cols, C_DIM);
}

// ---- what it says -----------------------------------------------------------

static void add(moy_rview_t *v, const char *s) {
    if (v->nlines < MOY_RV_LINES) {
        moy_crash_strcpy(v->line[v->nlines++], MOY_RV_COLS, s);
    }
}

// A line formatted over the table's text for `id` (its conversions are
// English's, moy_str.h).
static void add_fmt(moy_rview_t *v, int id, ...) {
    if (v->nlines >= MOY_RV_LINES) {
        return;
    }
    char b[MOY_RV_COLS + 16];
    va_list ap;
    va_start(ap, id);
    vsnprintf(b, sizeof(b), moy_str(id), ap);
    va_end(ap);
    add(v, b);
}

void moy_web_screen_view(moy_rview_t *v, const char *url, const char *pin,
                         const char *label) {
    memset(v, 0, sizeof(*v));
    add(v, moy_str(MOY_S_WEB_OPEN));
    add(v, url != NULL && url[0] ? url : moy_str(MOY_S_WEB_WAITING));
    if (pin != NULL && pin[0]) {
        add_fmt(v, MOY_S_WEB_PIN, pin);
    }
    add_fmt(v, MOY_S_RECOVERY_FW, label ? label : moy_str(MOY_S_RECOVERY_UNKNOWN));
    moy_crash_strcpy(v->hint, MOY_RV_COLS, moy_str(MOY_S_WEB_RESTARTING));
}

void moy_recovery_view(moy_rview_t *v, int why, const moy_crash_rec_t *rec,
                       const char *label, const char *hint) {
    const char *unknown = moy_str(MOY_S_RECOVERY_UNKNOWN);
    memset(v, 0, sizeof(*v));
    switch (why) {
        case MOY_WHY_BOOT_LOOP: add(v, moy_str(MOY_S_RECOVERY_BOOT_LOOP)); break;
        case MOY_WHY_HEAP:      add(v, moy_str(MOY_S_RECOVERY_NO_MEMORY)); break;
        default:                add(v, moy_str(MOY_S_RECOVERY_NOT_STARTED)); break;
    }
    if (rec != NULL) {
        add_fmt(v, MOY_S_RECOVERY_LAST, moy_crash_kind_name(rec->kind),
                rec->task[0] ? rec->task : unknown);
        if (rec->id[0]) {
            add_fmt(v, MOY_S_RECOVERY_ROLE, moy_crash_role_name(rec->role), rec->id);
        }
        if (rec->what[0]) {
            add(v, rec->what);
        }
        if (rec->kind != MOY_CRASH_VM && rec->kind != MOY_CRASH_RESET) {
            add_fmt(v, MOY_S_RECOVERY_PC, (unsigned)rec->pc, (unsigned)rec->addr);
        }
        add_fmt(v, MOY_S_RECOVERY_UPTIME, (unsigned)(rec->uptime_ms / 1000),
                (unsigned)rec->boot);
    }
    add_fmt(v, MOY_S_RECOVERY_FW, label ? label : unknown);
    moy_crash_strcpy(v->hint, MOY_RV_COLS, hint);
}
