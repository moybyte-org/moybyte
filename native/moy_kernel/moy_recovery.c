// moy_recovery: the recovery screen's raster. The header has the design.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "moy_recovery.h"
#include "moy_str.h"

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

#if MOY_KERNEL_SCR
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

#endif

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
        case MOY_WHY_CRASHES:   add(v, moy_str(MOY_S_RECOVERY_CRASHES)); break;
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
