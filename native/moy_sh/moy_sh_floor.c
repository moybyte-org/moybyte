// moy_sh_floor: the recovery console's screen. moy_sh_floor.h has the contract.

#include <string.h>

#include "moy_canvas.h"
#include "moy_shape.h"
#include "moy_sh_board.h"
#include "moy_sh_floor.h"
#include "moy_str.h"
#include "moy_text.h"
#include "moy_theme.h"

#define B_LEFT (1 << 0)
#define B_RIGHT (1 << 1)
#define B_UP (1 << 2)
#define B_DOWN (1 << 3)

struct moy_sh_floor {
    moy_sh_floor_geom_t g;
    void (*free)(void *ctx, void *p);
    moy_sh_face_t *face;
    moy_ui_ctx_t *ctx;
    moy_ui_shape_t *shape;
    uint16_t *px;
    moy_canvas_row_t row;
    char line[MOY_SH_FLOOR_LINES][MOY_SH_FLOOR_COLS];
    int nlines;
    char hint[MOY_SH_FLOOR_COLS];
    int32_t box[MOY_SH_FLOOR_ROWS][4];
    int nbox;
    int32_t key;                        // the byte typed last frame
    int32_t ptr;                        // the pointer's flags last frame
    int drawn, owed;
};

static const char *const WORDS[] = {"retry", "safe", "reboot", "repl"};
static const int LABELS[MOY_SH_FLOOR_ROWS] = {
    MOY_S_RECOVERY_RETRY, MOY_S_RECOVERY_SAFE, MOY_S_RECOVERY_REBOOT,
};

static moy_ui_span_t span(const char *p) {
    moy_ui_span_t s = {(const uint8_t *)p, (uint16_t)strlen(p)};
    return s;
}

static void copy(char *dst, const char *src) {
    size_t n = strlen(src);
    n = n < MOY_SH_FLOOR_COLS - 1 ? n : MOY_SH_FLOOR_COLS - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static uint16_t wire(uint16_t v, int swap) {
    return swap ? (uint16_t)((v >> 8) | (v << 8)) : v;
}

// The default seed's dark mode: the kernel's look, its shape and depth, and
// its colours over MOY64 in the row's palette.
static void look(moy_sh_floor_t *fl) {
    const char *name = moy_theme_name(0);
    size_t n = strlen(name);
    int32_t row[MOY_THEME_ROLES], placed[MOY_THEME_ROLES], near[MOY_THEME_ROLES];
    uint16_t block[MOY_THEME_BLOCK];
    moy_theme_resolve(name, n, MOY_THEME_DARK, row, NULL);
    int used = moy_theme_place(row, MOY_THEME_ROLES, placed, near, block);
    moy_theme_block_use(block, used);
    moy_ui_look_set(MOY_UI_LOOK_KERNEL, placed);
    for (int i = 0; i < MOY_CANVAS_BLOCK; i++) {
        fl->row.pal[i] = wire(moy_theme_rgb565(i), fl->g.swap);
        fl->row.pal[MOY_CANVAS_BLOCK + i] = i < used ? wire(block[i], fl->g.swap) : 0;
    }
    moy_theme_shape(name, n, fl->shape);
    moy_ui_shape_use(fl->shape);
    moy_ui_depth_use(name, n, 0);
    moy_text_wire(fl->g.swap);
    moy_shape_wire_swapped(fl->g.swap);
    moy_ui_set_input(fl->g.input);
    moy_ui_set_hint_input(fl->g.input);
}

moy_sh_floor_t *moy_sh_floor_new(const moy_sh_floor_geom_t *g, const char *root) {
    moy_sh_host_t h;
    moy_sh_board_host(&h, NULL, NULL, NULL);
    moy_sh_floor_t *fl = h.alloc(NULL, sizeof *fl);
    if (fl == NULL) {
        return NULL;
    }
    memset(fl, 0, sizeof *fl);
    fl->g = *g;
    fl->g.fs = g->fs > 1 ? g->fs : 1;
    fl->free = h.free;
    size_t cn = moy_ui_api_size();
    void *cm = h.alloc(NULL, cn);
    fl->ctx = cm != NULL ? moy_ui_api_ctx(cm, cn) : NULL;
    fl->shape = h.alloc(NULL, sizeof *fl->shape);
    fl->px = h.alloc(NULL, (size_t)g->w * (size_t)g->h * 2u);
    fl->face = moy_sh_face_new(root);
    if (fl->ctx == NULL || fl->shape == NULL || fl->px == NULL || fl->face == NULL) {
        if (cm == NULL) {
            fl->ctx = NULL;
        }
        moy_sh_floor_free(fl);
        return NULL;
    }
    memset(fl->px, 0, (size_t)g->w * (size_t)g->h * 2u);
    moy_canvas_row_t *r = &fl->row;
    r->px = fl->px;
    r->cap = (uint32_t)(g->w * g->h);
    r->w = (uint16_t)g->w;
    r->h = (uint16_t)g->h;
    look(fl);
    moy_sh_face_open(fl->face);
    return fl;
}

void moy_sh_floor_free(moy_sh_floor_t *fl) {
    if (fl == NULL) {
        return;
    }
    if (fl->shape != NULL && moy_ui_shape() == fl->shape) {
        moy_ui_shape_use(NULL);
    }
    moy_sh_face_free(fl->face);
    if (fl->ctx != NULL) {
        fl->free(NULL, fl->ctx);
    }
    if (fl->shape != NULL) {
        fl->free(NULL, fl->shape);
    }
    if (fl->px != NULL) {
        fl->free(NULL, fl->px);
    }
    fl->free(NULL, fl);
}

void moy_sh_floor_say(moy_sh_floor_t *fl, const char *const *lines, int n, const char *hint) {
    n = n < 0 ? 0 : n > MOY_SH_FLOOR_LINES ? MOY_SH_FLOOR_LINES : n;
    for (int i = 0; i < n; i++) {
        copy(fl->line[i], lines[i] != NULL ? lines[i] : "");
    }
    fl->nlines = n;
    copy(fl->hint, hint != NULL ? hint : "");
    fl->owed = 1;
}

const char *moy_sh_floor_word(int choice) {
    return choice >= 0 && choice < (int)(sizeof WORDS / sizeof WORDS[0]) ? WORDS[choice] : "";
}

int moy_sh_floor_choice(const char *word) {
    while (*word == ' ') {
        word++;
    }
    size_t n = strlen(word);
    while (n > 0 && word[n - 1] == ' ') {
        n--;
    }
    for (int i = 0; i < (int)(sizeof WORDS / sizeof WORDS[0]); i++) {
        if (strlen(WORDS[i]) == n && memcmp(WORDS[i], word, n) == 0) {
            return i;
        }
    }
    return MOY_SH_FLOOR_NONE;
}

moy_sh_face_t *moy_sh_floor_face(moy_sh_floor_t *fl) {
    return fl->face;
}

const uint16_t *moy_sh_floor_pixels(const moy_sh_floor_t *fl) {
    return fl->px;
}

int moy_sh_floor_row(const moy_sh_floor_t *fl, int i, int32_t box[4]) {
    if (i < 0 || i >= fl->nbox) {
        return 0;
    }
    memcpy(box, fl->box[i], sizeof fl->box[i]);
    return 1;
}

static void label(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int x, int y, int w, int h,
                  const char *text, int tone, int role) {
    moy_ui_api_at(c, x, y, w, h);
    moy_ui_api_text(c, 1, 0, tone, role);
    moy_ui_api_label(c, s, span(text), NULL);
}

// The head over the face: its height, drawn when `s` is given. A button
// pressed this frame comes back in *choice.
static int32_t head(moy_sh_floor_t *fl, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int *choice) {
    const moy_sh_floor_geom_t *g = &fl->g;
    int32_t fs = g->fs;
    int32_t pad = 6 * fs, lh = 10 * fs, gap = 4 * fs;
    int32_t bh = (g->desk ? 24 : 20) * fs;
    int32_t budget = (g->h / 4) / lh;
    budget = budget < 3 ? 3 : budget;
    int detail = fl->nlines - 1;
    detail = detail < 0 ? 0 : detail > budget - 2 ? budget - 2 : detail;
    int32_t y = pad;
    int32_t tw = g->w - 2 * pad;
    if (s != NULL) {
        label(c, s, pad, y, tw, lh, fl->nlines > 0 ? fl->line[0] : "", MOY_UI_TONE_INK,
              MOY_UI_FONT_TITLE);
        for (int i = 0; i < detail; i++) {
            label(c, s, pad, y + (i + 1) * lh, tw, lh, fl->line[i + 1], MOY_UI_TONE_DIM, -1);
        }
        label(c, s, pad, y + (detail + 1) * lh, tw, lh, fl->hint, MOY_UI_TONE_VALUE, -1);
    }
    y += (detail + 2) * lh + gap;
    if (s != NULL) {
        int32_t bw = (tw - gap * (MOY_SH_FLOOR_ROWS - 1)) / MOY_SH_FLOOR_ROWS;
        for (int i = 0; i < MOY_SH_FLOOR_ROWS; i++) {
            int32_t x = pad + i * (bw + gap);
            int32_t w = i == MOY_SH_FLOOR_ROWS - 1 ? pad + tw - x : bw;
            int32_t *b = fl->box[i];
            b[0] = x;
            b[1] = y;
            b[2] = w;
            b[3] = bh;
            moy_ui_api_at(c, x, y, w, bh);
            if (moy_ui_api_button(c, s, span(moy_str(LABELS[i])), i == 0 ? 1 : 0, NULL)) {
                *choice = i;
            }
        }
        fl->nbox = MOY_SH_FLOOR_ROWS;
    }
    y += bh + gap;
    if (s != NULL) {
        moy_ui_api_at(c, 0, y, g->w, 2 * fs);
        moy_ui_api_rule(c, s);
    }
    return y + 2 * fs;
}

int moy_sh_floor_frame(moy_sh_floor_t *fl, const moy_sh_floor_in_t *in, int *choice) {
    *choice = MOY_SH_FLOOR_NONE;
    moy_sh_face_t *f = fl->face;
    const moy_sh_floor_geom_t *g = &fl->g;
    int32_t key = in->key;
    int typed = key != 0 && key != fl->key;
    fl->key = key;
    if (typed && (key == '\r' || key == '\n') && !moy_sh_face_in_row(f)
        && !moy_sh_face_more(f)) {
        int32_t col;
        int ch = moy_sh_floor_choice(moy_sh_face_line(f, &col));
        if (ch != MOY_SH_FLOOR_NONE) {
            moy_sh_face_set_line(f, "", 0);
            *choice = ch;
            return 0;
        }
    }
    int r = moy_sh_face_input(f, g->desk, key, in->repeats, in->repeat_key, in->left,
                              in->right, in->up, in->down);
    int dirty = !fl->drawn || fl->owed || (r & MOY_SH_FACE_HIT) || in->ptr || fl->ptr;
    fl->ptr = in->ptr;
    if (!dirty) {
        return 0;
    }
    int32_t a[MOY_UI_IN_LEN];
    memset(a, 0, sizeof a);
    a[MOY_UI_IN_X] = in->x;
    a[MOY_UI_IN_Y] = in->y;
    a[MOY_UI_IN_PTR] = in->ptr;
    a[MOY_UI_IN_DT] = in->dt;
    if (r & MOY_SH_FACE_KEYS) {
        int32_t b = (in->left > 0 ? B_LEFT : 0) | (in->right > 0 ? B_RIGHT : 0)
                    | (in->up > 0 ? B_UP : 0) | (in->down > 0 ? B_DOWN : 0);
        a[MOY_UI_IN_BTN] = a[MOY_UI_IN_BTNP] = b;
        a[MOY_UI_IN_KEY] = typed ? key : 0;
    }
    moy_canvas_row_t *row = &fl->row;
    memset(row->st, 0, sizeof row->st);
    row->st[MOY_ST_CX1] = row->st[MOY_ST_W] = g->w;
    row->st[MOY_ST_CY1] = row->st[MOY_ST_H] = g->h;
    row->st[MOY_ST_FONT_SCALE] = g->fs;
    moy_ui_sink_t s = {MOY_UI_SINK_RUN, (uint8_t)g->fs, MOY_UI_LOOK_KERNEL, row};
    moy_ui_ctx_t *c = fl->ctx;
    moy_ui_box_t surf = {0, 0, (int16_t)g->w, (int16_t)g->h};
    int flags = (g->desk ? MOY_UI_DESK : 0)
                | (moy_sh_face_elsewhere(f, g->desk) ? MOY_UI_ELSEWHERE : 0);
    moy_ui_api_begin(c, a, surf, flags, g->fs);
    int32_t top = head(fl, c, NULL, choice);
    moy_sh_face_frame_t fr = {0, top, g->w, g->h - top, g->fs, g->desk, NULL, 0};
    int out = moy_sh_face_draw(f, c, &s, &fr);
    head(fl, c, &s, choice);
    moy_ui_api_end(c, &s);
    fl->drawn = 1;
    fl->owed = (out & (MOY_SH_FACE_AGAIN | MOY_SH_FACE_DAMAGE)) != 0 || moy_ui_api_owes(c)
               || moy_ui_api_moving(c);
    return 1;
}
