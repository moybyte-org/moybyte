// moy_recovery: the recovery screen's raster. The header has the design.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "moy_recovery.h"
#include "moy_scr.h"
#include "moy_str.h"

// Moy Night's (native/moy_ui/themes/moy.json, dark): the panel, the
// surface, the ink, the dim ink, the border and the accent;
// tests/test_moy_kernel.py holds them to the theme.
enum { N_BG, N_SURFACE, N_INK, N_DIM, N_LINE, N_VALUE, N_PAL };
static const uint16_t NIGHT[N_PAL] = {0x0884u, 0x18C7u, 0xEF9Fu, 0xA539u, 0x31CBu, 0xB55Eu};

// The choices' labels, in order (native/moy_spine/strings/en.json), and
// their marks.
static const int16_t CHOICES[MOY_RV_CHOICES] = {
    MOY_S_RECOVERY_RETRY, MOY_S_RECOVERY_SAFE, MOY_S_RECOVERY_REPL,
};

static const int8_t MARKS[MOY_RV_CHOICES] = {
    MOY_SCR_ICON_RETRY, MOY_SCR_ICON_HOME, MOY_SCR_ICON_CODE,
};

static const char *const PROTOCOL[MOY_RV_CHOICES] = {"RETRY", "SAFE", "REPL"};

const char *moy_recovery_choice_name(int choice) {
    return (choice >= 0 && choice < MOY_RV_CHOICES) ? PROTOCOL[choice] : "?";
}

void moy_rgeom_init(moy_rgeom_t *g, int w, int h, int rot, int swap) {
    memset(g, 0, sizeof(*g));
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
}

static void scr_of(moy_scr_t *s, uint16_t *fb, const moy_rgeom_t *g) {
    memset(s, 0, sizeof(*s));
    s->fb = fb;
    s->fb_w = g->fb_w;
    s->w = g->w;
    s->h = g->h;
    s->rot = g->rot;
    s->swap = g->swap;
    if (g->rot != 0) {
        s->band = g->band;
        s->band_rows = g->band_rows;
    }
    s->scratch = g->scratch;
    s->scratch_n = g->scratch_n;
}

// ---- the recovery screen ----------------------------------------------------------

typedef struct {
    int desk, touch;
    int x, cw;                       // the column
    int top, n, scale;               // Moy's box and its art's scale
    int title, body;                 // the title's and the reason's sizes
    int rows_y, rh, label;           // the choices' card, a row, its label's size
    int code, lh;                    // the code face's size, its line
    int foot, foot_base;             // the foot's size and baseline
} layout_t;

static void layout(const moy_rgeom_t *g, layout_t *l) {
    int desk = g->w >= 800, touch = g->touch && !desk;
    l->desk = desk;
    l->touch = touch;
    l->cw = desk ? 560 : g->w - 32;
    l->x = (g->w - l->cw) / 2;
    l->top = desk ? 72 : (touch ? 30 : 22);
    l->n = desk ? 48 : 24;
    l->scale = desk ? 3 : 1;
    l->title = desk ? 26 : (touch ? 17 : 15);
    l->body = desk ? 16 : (touch ? 13 : 12);
    l->rows_y = l->top + l->n + (desk ? 28 : 12);
    l->rh = desk ? 40 : (touch ? 28 : 22);
    l->label = desk ? 16 : 13;
    l->code = desk ? 14 : 10;
    l->lh = desk ? 22 : (touch ? 13 : 11);
    l->foot = desk ? 15 : 11;
    l->foot_base = g->h - (desk ? 20 : (touch ? 12 : 7));
}

int moy_recovery_hit(const moy_rgeom_t *g, int x, int y) {
    layout_t l;
    layout(g, &l);
    if (x < l.x || x >= l.x + l.cw || y < l.rows_y || y >= l.rows_y + MOY_RV_CHOICES * l.rh) {
        return -1;
    }
    return (y - l.rows_y) / l.rh;
}

typedef struct {
    const moy_rgeom_t *g;
    const moy_rview_t *v;
} rctx_t;

static int text(moy_scr_t *s, int face, int size, int x, int base, const char *t, uint16_t c) {
    return moy_scr_text(s, face, size, x, base, t, strlen(t), c);
}

static void recovery_paint(moy_scr_t *s, const void *ctx) {
    const rctx_t *r = ctx;
    const moy_rview_t *v = r->v;
    layout_t l;
    layout(r->g, &l);
    moy_scr_fill(s, 0, 0, s->w, s->h, NIGHT[N_BG]);
    int off = (l.n - 16 * l.scale) / 2;
    moy_scr_moy(s, l.x + off, l.top + off, l.scale);
    int tx = l.x + l.n + (l.desk ? 16 : 10);
    text(s, MOY_SCR_BOLD, l.title, tx, l.top + (l.desk ? 22 : 12),
         moy_str(MOY_S_RECOVERY_TITLE), NIGHT[N_INK]);
    if (v->nlines > 0) {
        text(s, MOY_SCR_SANS, l.body, tx, l.top + (l.desk ? 48 : 29), v->line[0], NIGHT[N_DIM]);
    }
    // the choices: a card of rows, the highlighted one marked at its edge
    int y = l.rows_y, ch = l.rh * MOY_RV_CHOICES;
    moy_scr_rrect(s, l.x, y, l.cw, ch, 8, 0, NIGHT[N_SURFACE]);
    moy_scr_rrect(s, l.x, y, l.cw, ch, 8, 1, NIGHT[N_LINE]);
    int cap = moy_scr_cap(MOY_SCR_BOLD, l.label);
    for (int i = 0; i < MOY_RV_CHOICES; i++) {
        int ry = y + i * l.rh;
        if (i > 0) {
            moy_scr_fill(s, l.x + 10, ry, l.cw - 20, 1, NIGHT[N_LINE]);
        }
        if (i == v->sel) {
            moy_scr_rrect(s, l.x + 1, ry + 4, 3, l.rh - 8, 1, 0, NIGHT[N_VALUE]);
        }
        int is = l.desk ? 18 : 14;
        moy_scr_icon(s, MARKS[i], l.x + 10, ry + (l.rh - is) / 2, is, NIGHT[N_DIM]);
        text(s, MOY_SCR_BOLD, l.label, l.x + 18 + is, ry + (l.rh + cap) / 2,
             moy_str(CHOICES[i]), NIGHT[N_INK]);
    }
    // what the kernel knows, as many lines as stand above the foot
    y += ch + (l.desk ? 24 : 12);
    int room = (l.foot_base - l.foot - 4 - y) / l.lh;
    for (int i = 1; i < v->nlines && i <= room; i++) {
        text(s, MOY_SCR_MONO, l.code, l.x, y + l.lh - 4, v->line[i], NIGHT[N_DIM]);
        y += l.lh;
    }
    text(s, MOY_SCR_SANS, l.foot, l.x, l.foot_base, v->hint, NIGHT[N_DIM]);
}

int moy_recovery_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v) {
    moy_scr_t s;
    scr_of(&s, fb, g);
    rctx_t r = {g, v};
    return moy_scr_draw(&s, recovery_paint, &r);
}

// ---- the web-console screen -----------------------------------------------------------

typedef struct {
    const moy_rgeom_t *g;
    const moy_rview_t *v;
    const uint16_t *pal;
} wctx_t;

static void centred(moy_scr_t *s, int face, int size, int base, const char *t, uint16_t c) {
    size_t n = strlen(t);
    moy_scr_text(s, face, size, (s->w - moy_scr_width(face, size, t, n)) / 2, base, t, n, c);
}

static void web_paint(moy_scr_t *s, const void *ctx) {
    const wctx_t *w = ctx;
    const moy_rview_t *v = w->v;
    const uint16_t *pal = w->pal;
    int desk = s->w >= 800, touch = w->g->touch && !desk;
    moy_scr_fill(s, 0, 0, s->w, s->h, pal[MOY_SPLASH_BG]);
    int n = desk ? 48 : 28, scale = desk ? 3 : 1;
    int y = s->h / 2 - (desk ? 150 : 92);
    moy_scr_moy(s, (s->w - 16 * scale) / 2, y + (n - 16 * scale) / 2, scale);
    y += n + (desk ? 44 : 26);
    centred(s, MOY_SCR_BOLD, desk ? 28 : (touch ? 18 : 16), y, moy_str(MOY_S_WEB_TITLE),
            pal[MOY_SPLASH_INK]);
    y += desk ? 32 : 22;
    int body = desk ? 16 : 13;
    if (v->nlines < 3) {
        if (v->nlines > 0) {
            centred(s, MOY_SCR_SANS, body, y + (desk ? 12 : 6), v->line[0], pal[MOY_SPLASH_DIM]);
        }
        return;
    }
    centred(s, MOY_SCR_SANS, body, y, v->line[0], pal[MOY_SPLASH_DIM]);
    y += desk ? 40 : 24;
    centred(s, MOY_SCR_MONO, desk ? 26 : 15, y, v->line[1], pal[MOY_SPLASH_VALUE]);
    if (v->line[2][0]) {
        y += desk ? 30 : 20;
        centred(s, MOY_SCR_SANS, desk ? 14 : 11, y, moy_str(MOY_S_WEB_PIN), pal[MOY_SPLASH_DIM]);
        y += desk ? 52 : 30;
        centred(s, MOY_SCR_BOLD, desk ? 44 : 26, y, v->line[2], pal[MOY_SPLASH_INK]);
    }
}

int moy_web_screen_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v,
                          const uint16_t *pal) {
    moy_scr_t s;
    scr_of(&s, fb, g);
    wctx_t w = {g, v, pal != NULL ? pal : moy_splash_day};
    return moy_scr_draw(&s, web_paint, &w);
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

void moy_web_screen_view(moy_rview_t *v, const char *url) {
    memset(v, 0, sizeof(*v));
    const char *at = url != NULL ? url : "";
    if (strncmp(at, "http://", 7) == 0) {
        at += 7;
    }
    size_t n = strcspn(at, "/?");
    if (n == 0) {
        add(v, moy_str(MOY_S_WEB_RESTARTING));
        return;
    }
    add(v, moy_str(MOY_S_WEB_OPEN));
    if (n >= MOY_RV_COLS) {
        n = MOY_RV_COLS - 1;
    }
    memcpy(v->line[v->nlines++], at, n);
    const char *pin = strstr(at + n, "pin=");
    add(v, pin != NULL ? pin + 4 : "");
}

void moy_recovery_view(moy_rview_t *v, int why, const moy_crash_rec_t *rec,
                       const char *label, const char *hint, int serial) {
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
    if (serial) {
        add(v, moy_str(MOY_S_RECOVERY_SERIAL));
    }
    moy_crash_strcpy(v->hint, MOY_RV_COLS, hint);
}
