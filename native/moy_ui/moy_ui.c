// moy_ui's style resolution (moy_ui.h).

#include <string.h>

#include "moy_ui.h"
#include "moy_theme.h"

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define UI_ALLOC(n) heap_caps_malloc((n), MALLOC_CAP_SPIRAM)
#else
#include <stdlib.h>
#define UI_ALLOC(n) malloc(n)
#endif

typedef struct {
    int32_t tok[MOY_UI_TOKENS];
    uint8_t have[MOY_UI_KINDS];
    int16_t flat[MOY_UI_KINDS][MOY_UI_STATES][3];
    int16_t slot[MOY_THEME_SPRITE_SLOTS];   // a sheet slot's palette index, -1 transparent
} look_t;

// One row of the live theme's button leaves: row 0 its `buttons`, a later
// row an `app_buttons` app's (docs/look_2026-10.md step 10).
typedef struct {
    char app[MOY_THEME_MAPNAME_MAX + 1];
    int32_t v[MOY_THEME_BTN_KINDS][MOY_THEME_BTN_LEAVES];
} btn_row_t;

typedef struct {
    look_t look[MOY_UI_LOOKS];
    moy_ui_shape_t shape;
    btn_row_t btn[1 + MOY_THEME_APPBTN_CAP];
    uint8_t n_btn;                  // rows in use, row 0 always
    uint32_t calls;                 // primitive entries: one per widget drawn
} ui_t;

static void btn_row_clear(btn_row_t *r) {
    r->app[0] = 0;
    for (int k = 0; k < MOY_THEME_BTN_KINDS; k++) {
        for (int i = 0; i < MOY_THEME_BTN_LEAVES; i++) {
            r->v[k][i] = MOY_THEME_NONE;
        }
    }
}

static ui_t *s_ui;

static ui_t *ui(void) {
    if (s_ui == NULL) {
        ui_t *u = UI_ALLOC(sizeof(ui_t));
        if (u == NULL) {
            return NULL;
        }
        memset(u, 0, sizeof(*u));
        for (int i = 0; i < MOY_UI_LOOKS; i++) {
            for (int t = 0; t < MOY_UI_TOKENS; t++) {
                u->look[i].tok[t] = MOY_UI_ABSENT;
            }
        }
        moy_ui_shape_clear(&u->shape);
        btn_row_clear(&u->btn[0]);
        u->n_btn = 1;
        s_ui = u;
    }
    return s_ui;
}

static int find(const char *const *names, int count, const char *name, size_t n) {
    for (int i = 0; i < count; i++) {
        if (strlen(names[i]) == n && memcmp(names[i], name, n) == 0) {
            return i;
        }
    }
    return -1;
}

int moy_ui_kind_known(const char *name, size_t n) {
    return find(moy_ui_kind_names, MOY_UI_KINDS, name, n);
}

int moy_ui_kind(const char *name, size_t n) {
    int k = moy_ui_kind_known(name, n);
    return k < 0 ? MOY_UI_K_ROW : k;
}

int moy_ui_button_kind(const char *name, size_t n) {
    for (int i = 0; i < MOY_UI_BUTTON_KINDS; i++) {
        if (strlen(moy_ui_button_kinds[i][0]) == n
            && memcmp(moy_ui_button_kinds[i][0], name, n) == 0) {
            const char *k = moy_ui_button_kinds[i][1];
            return moy_ui_kind(k, strlen(k));
        }
    }
    return MOY_UI_K_BUTTON;
}

int moy_ui_state(const char *name, size_t n) {
    return find(moy_ui_state_names, MOY_UI_STATES, name, n);
}

void moy_ui_count_call(void) {
    ui_t *u = ui();
    if (u != NULL) {
        u->calls++;
    }
}

uint32_t moy_ui_calls(void) {
    return s_ui != NULL ? s_ui->calls : 0;
}

int moy_ui_look_set(int look, const int32_t tokens[MOY_UI_TOKENS]) {
    ui_t *u = ui();
    if (u == NULL) {
        return MOY_UI_NOMEM;
    }
    if (look < 0 || look >= MOY_UI_LOOKS) {
        return MOY_UI_BAD;
    }
    look_t *l = &u->look[look];
    memcpy(l->tok, tokens, sizeof(l->tok));
    for (int i = 0; i < MOY_UI_TOKENS; i++) {
        // an RGB value no block holds: its nearest MOY64 index
        if (l->tok[i] != MOY_UI_ABSENT && l->tok[i] >= MOY_THEME_SLOT0 + MOY_THEME_BLOCK) {
            l->tok[i] = moy_theme_nearest(l->tok[i]);
        }
    }
    memset(l->have, 0, sizeof(l->have));
    for (int i = 0; i < MOY_THEME_SPRITE_SLOTS; i++) {
        int8_t t = moy_theme_sprite_slots[i];
        int32_t v = t == INT8_MIN ? MOY_UI_ABSENT : t >= 0 ? t : l->tok[-1 - t];
        l->slot[i] = (int16_t)(v == MOY_UI_ABSENT ? -1 : (v & 127));
    }
    return MOY_UI_OK;
}

const int16_t *moy_ui_sprite_slots(int look) {
    ui_t *u = ui();
    if (u == NULL || look < 0 || look >= MOY_UI_LOOKS) {
        return NULL;
    }
    return u->look[look].slot;
}

// One term of a chain: a palette index, or a role through the token row
// (an alias role through its chain of tokens). -1: the theme is silent.
static int16_t term(const look_t *l, int t) {
    if (t >= 0) {
        return (int16_t)t;
    }
    int r = -1 - t;
    if (r < MOY_UI_TOKENS) {
        return l->tok[r] == MOY_UI_ABSENT ? -1 : (int16_t)l->tok[r];
    }
    const int8_t *chain = moy_ui_alias[r - MOY_UI_TOKENS];
    for (int i = 0; i < 3 && chain[i] >= 0; i++) {
        if (l->tok[chain[i]] != MOY_UI_ABSENT) {
            return (int16_t)l->tok[chain[i]];
        }
    }
    return -1;
}

static int16_t resolve(const look_t *l, uint8_t spec) {
    const moy_ui_spec_t *s = &moy_ui_specs[spec];
    for (int i = 0; i < s->n; i++) {
        int16_t v = term(l, s->t[i]);
        if (v >= 0) {
            return v;
        }
    }
    return -1;
}

// A kind's own states (rest, hover, hot) whose (field, ink) misses the floor
// take its first floor form that reads, or its last.
static void floor_kind(look_t *l, int kind) {
    static const uint8_t own[3] = {MOY_UI_REST, MOY_UI_HOVER, MOY_UI_HOT};
    int nf = moy_ui_floor_n[kind];
    for (int i = 0; nf > 0 && i < 3; i++) {
        int16_t *c = l->flat[kind][own[i]];
        if (c[0] < 0 || c[1] < 0 || moy_theme_reads(c[0], c[1])) {
            continue;
        }
        for (int a = 0; a < nf; a++) {
            int16_t f = resolve(l, moy_ui_floor_specs[kind][a][0]);
            int16_t k = resolve(l, moy_ui_floor_specs[kind][a][1]);
            if (a == nf - 1 || (f >= 0 && k >= 0 && moy_theme_reads(f, k))) {
                c[0] = f;
                c[1] = k;
                break;
            }
        }
    }
}

static void flatten(look_t *l, int kind) {
    const uint8_t (*specs)[3] = moy_ui_kind_specs[kind];
    for (int st = 0; st < MOY_UI_STATES; st++) {
        const uint8_t *tr = specs[st];
        if (tr[0] == 0xff) {
            tr = specs[MOY_UI_REST];
        }
        for (int c = 0; c < 3; c++) {
            l->flat[kind][st][c] = resolve(l, tr[c]);
        }
    }
    floor_kind(l, kind);
    l->have[kind] = 1;
}

int moy_ui_colors(int look, int kind, int state, int16_t out[3]) {
    ui_t *u = ui();
    if (u == NULL) {
        return MOY_UI_NOMEM;
    }
    if (look < 0 || look >= MOY_UI_LOOKS || state < 0 || state >= MOY_UI_STATES) {
        return MOY_UI_BAD;
    }
    if (kind < 0 || kind >= MOY_UI_KINDS
        || moy_ui_kind_specs[kind][MOY_UI_REST][0] == 0xff) {
        kind = MOY_UI_K_ROW;
    }
    look_t *l = &u->look[look];
    if (!l->have[kind]) {
        flatten(l, kind);
    }
    memcpy(out, l->flat[kind][state], 3 * sizeof(int16_t));
    return MOY_UI_OK;
}

// -- button kinds (docs/look_2026-10.md step 10) ------------------------------------

// The `button` fields' leaves, in style.json's theme.look.button order.
enum { BL_FILL = 0, BL_INK, BL_BORDER_W, BL_BORDER_C, BL_SHAPE, BL_R, BL_FONT, BL_BEVEL };

// The leaves a kind does not take from `normal` (DESIGN.md, "Button kinds"):
// primary its fill, ink, border and font; tonal its ink and font; danger its
// ink; toggle none.
static const uint8_t k_own[MOY_THEME_BTN_KINDS] = {
    0xff,
    1 << BL_FILL | 1 << BL_INK | 1 << BL_BORDER_W | 1 << BL_BORDER_C | 1 << BL_FONT,
    1 << BL_INK | 1 << BL_FONT,
    1 << BL_INK,
    0,
};

void moy_ui_buttons_clear(void) {
    ui_t *u = ui();
    if (u != NULL) {
        btn_row_clear(&u->btn[0]);
        u->n_btn = 1;
    }
}

int moy_ui_buttons_app(const char *name, size_t n) {
    ui_t *u = ui();
    if (u == NULL || name == NULL) {
        return -1;
    }
    for (int i = 1; i < u->n_btn; i++) {
        if (strlen(u->btn[i].app) == n && memcmp(u->btn[i].app, name, n) == 0) {
            return i - 1;
        }
    }
    return -1;
}

int moy_ui_buttons_set(const char *app, size_t n, int bkind,
                       const int32_t leaves[MOY_THEME_BTN_LEAVES]) {
    ui_t *u = ui();
    if (u == NULL) {
        return MOY_UI_NOMEM;
    }
    if (bkind < 0 || bkind >= MOY_THEME_BTN_KINDS || (app != NULL && n > MOY_THEME_MAPNAME_MAX)) {
        return MOY_UI_BAD;
    }
    btn_row_t *r = &u->btn[0];
    if (app != NULL) {
        int row = moy_ui_buttons_app(app, n);
        if (row < 0) {
            if (u->n_btn >= 1 + MOY_THEME_APPBTN_CAP) {
                return MOY_UI_FULL;
            }
            r = &u->btn[u->n_btn++];
            btn_row_clear(r);
            memcpy(r->app, app, n);
            r->app[n] = 0;
        } else {
            r = &u->btn[row + 1];
        }
    }
    memcpy(r->v[bkind], leaves, sizeof(r->v[bkind]));
    return MOY_UI_OK;
}

int moy_ui_button_of(int kind) {
    for (int i = 0; i < MOY_UI_BUTTON_KINDS; i++) {
        const char *k = moy_ui_button_kinds[i][1];
        if (moy_ui_kind_known(k, strlen(k)) == kind) {
            return i;
        }
    }
    return -1;
}

// Leaf `leaf` of button kind `bk` for app row `app`: the app's, then the
// theme's, then, for a leaf the kind takes from `normal`, normal's the same way.
static int32_t btn_leaf(const ui_t *u, int app, int bk, int leaf) {
    for (int pass = 0; pass < 2; pass++) {
        int k = pass ? 0 : bk;
        if (pass && (k_own[bk] >> leaf & 1)) {
            break;
        }
        if (app >= 0 && app + 1 < u->n_btn && u->btn[app + 1].v[k][leaf] != MOY_THEME_NONE) {
            return u->btn[app + 1].v[k][leaf];
        }
        if (u->btn[0].v[k][leaf] != MOY_THEME_NONE) {
            return u->btn[0].v[k][leaf];
        }
    }
    return MOY_THEME_NONE;
}

static int16_t btn_colour(int32_t v) {
    return v == MOY_THEME_OFF ? -1 : (int16_t)(v & 127);
}

static int8_t btn_small(int32_t v) {
    return v == MOY_THEME_NONE || v == MOY_THEME_OFF ? -1 : (int8_t)(v > 127 ? 127 : v);
}

int moy_ui_button_style(int look, int kind, int state, int app, moy_ui_btn_style_t *out) {
    int rc = moy_ui_colors(look, kind, state, out->c);
    out->edge_w = out->shape = out->r = out->font = out->bevel = -1;
    ui_t *u = ui();
    int bk = moy_ui_button_of(kind);
    if (rc != MOY_UI_OK || u == NULL || bk < 0) {
        return rc;
    }
    int own = state == MOY_UI_REST || state == MOY_UI_HOVER || state == MOY_UI_HOT;
    if (state == MOY_UI_HOT) {
        // A kind whose hot field is a cue of its own (a cell's danger) keeps it.
        int16_t rest[3];
        own = moy_ui_colors(look, kind, MOY_UI_REST, rest) == MOY_UI_OK && rest[0] == out->c[0];
    }
    int32_t v = btn_leaf(u, app, bk, BL_FILL);
    if (v != MOY_THEME_NONE && (own || state == MOY_UI_DISABLED)) {
        out->c[0] = btn_colour(v);
    }
    v = btn_leaf(u, app, bk, BL_INK);
    if (v != MOY_THEME_NONE && own) {
        out->c[1] = btn_colour(v);
    }
    int32_t bw = btn_leaf(u, app, bk, BL_BORDER_W), bc = btn_leaf(u, app, bk, BL_BORDER_C);
    if (state != MOY_UI_HOVER) {
        if (bc == MOY_THEME_OFF || bw == 0 || bw == MOY_THEME_OFF) {
            out->c[2] = -1;
        } else if (bc != MOY_THEME_NONE) {
            out->c[2] = btn_colour(bc);
        }
    }
    if (bw != MOY_THEME_NONE && bw != MOY_THEME_OFF) {
        out->edge_w = (int8_t)((bw + 50) / 100);
    }
    // shape, radius and bevel where the theme is silent: a round key of the
    // depth's key_r, bevelled under `edges: bevel` (a theme with no look: 0, square)
    const moy_ui_depth_t *dp = moy_ui_depth();
    int8_t v8 = btn_small(btn_leaf(u, app, bk, BL_SHAPE));
    out->shape = v8 >= 0 ? v8 : 0;
    v8 = btn_small(btn_leaf(u, app, bk, BL_R));
    out->r = v8 >= 0 ? v8 : (int8_t)(dp->on ? dp->key_r : 0);
    out->font = btn_small(btn_leaf(u, app, bk, BL_FONT));
    v8 = btn_small(btn_leaf(u, app, bk, BL_BEVEL));
    out->bevel = v8 >= 0 ? v8 : (int8_t)(dp->on && dp->edges == 1);
    return MOY_UI_OK;
}

const int16_t *moy_ui_metrics(int kind, int *n) {
    if (kind < 0 || kind >= MOY_UI_KINDS || moy_ui_metric_n[kind] == 0) {
        *n = 0;
        return NULL;
    }
    *n = moy_ui_metric_n[kind];
    return moy_ui_metric_vals[kind];
}

int moy_ui_token(int look, int token) {
    ui_t *u = ui();
    if (u == NULL || look < 0 || look >= MOY_UI_LOOKS || token < 0 || token >= MOY_UI_TOKENS) {
        return -1;
    }
    int32_t v = u->look[look].tok[token];
    return v == MOY_UI_ABSENT ? -1 : (int)(v & 127);
}

int moy_ui_part(const char *name, size_t n) {
    return find(moy_theme_part_names, MOY_THEME_PARTS, name, n);
}

// -- the theme's shape ------------------------------------------------------------

const int moy_ui_shape_size = (int)sizeof(moy_ui_shape_t);  // the bindings' layout check

void moy_ui_shape_clear(moy_ui_shape_t *sh) {
    memset(sh, 0, sizeof(*sh));
    for (int p = 0; p < MOY_THEME_PARTS; p++) {
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            sh->effect[p][st][1] = 0xff;
        }
    }
}

// Fill each state a part leaves unnamed from its chain, so a draw reads one
// entry (docs/theming_2026-09.md §5: resolved at install, never at draw).
static void fill_chains(moy_ui_shape_t *sh) {
    for (int p = 0; p < MOY_THEME_PARTS; p++) {
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            int at = st;
            while (sh->stid[p][st] == 0 && moy_theme_state_next[at] != 0xff) {
                at = moy_theme_state_next[at];
                sh->stid[p][st] = sh->stid[p][at];
            }
            at = st;
            while (sh->effect[p][st][0] == 0 && sh->effect[p][st][1] != MOY_UI_FX_NAMED_NONE
                   && moy_theme_state_next[at] != 0xff) {
                at = moy_theme_state_next[at];
                sh->effect[p][st][0] = sh->effect[p][at][0];
                sh->effect[p][st][1] = sh->effect[p][at][1];
            }
        }
    }
}

int moy_ui_shape_use(const moy_ui_shape_t *sh) {
    ui_t *u = ui();
    if (u == NULL) {
        return MOY_UI_NOMEM;
    }
    if (sh == NULL) {
        moy_ui_shape_clear(&u->shape);
    } else {
        memcpy(&u->shape, sh, sizeof(*sh));
        fill_chains(&u->shape);
    }
    return MOY_UI_OK;
}

const moy_ui_shape_t *moy_ui_shape(void) {
    ui_t *u = ui();
    return u != NULL ? &u->shape : NULL;
}

// -- the fonts the roles draw in --------------------------------------------------

static uint32_t s_font[MOY_THEME_FONT_ROLES];   // 0, petme128, where none is installed

int moy_ui_font_use(int role, uint32_t font) {
    moy_font_info_t fi;
    if (role < 0 || role >= MOY_THEME_FONT_ROLES || !moy_font_info(font, &fi)) {
        return 0;
    }
    s_font[role] = font;
    return 1;
}

static int s_cart;                              // a cart's code is running
static uint32_t s_cart_print;                   // the print font it put aside

int moy_ui_cart_text(int on) {
    int was = s_cart;
    if (on && !s_cart) {
        s_cart_print = moy_text_print_font();
        moy_text_print_font_use(MOY_FONT_PETME128);
    } else if (!on && s_cart) {
        moy_text_print_font_use(s_cart_print);    // refused if freed meanwhile
    }
    s_cart = on != 0;
    return was;
}

int moy_ui_cmask(void) {
    return s_cart ? 63 : 127;
}

int moy_ui_pidx(int ci) {
    if (!s_cart) {
        return ci & 127;
    }
    // The theme's colours are the shell's block at 64..127, which a cart's
    // canvas does not have: inside the boundary one is its nearest MOY64. A
    // negative index (none) keeps the cart's modulo.
    return ci >= MOY_THEME_SLOT0 && ci < MOY_THEME_SLOT0 + MOY_THEME_BLOCK
               ? (int)moy_theme_nearest(ci) : ci & 63;
}

int64_t moy_ui_print_font(uint32_t font) {
    if (!s_cart) {
        return moy_text_print_font_use(font);
    }
    moy_font_info_t fi;
    if (!moy_font_info(font, &fi)) {
        return -1;
    }
    uint32_t was = s_cart_print;
    s_cart_print = font;
    return was;
}

uint32_t moy_ui_font(int role, moy_font_info_t *out) {
    uint32_t id = !s_cart && role >= 0 && role < MOY_THEME_FONT_ROLES ? s_font[role]
                                                                      : MOY_FONT_PETME128;
    if (!moy_font_info(id, out)) {
        id = MOY_FONT_PETME128;
        moy_font_info(id, out);
    }
    return id;
}

const moy_ui_surface_t *moy_ui_shape_surface(const moy_ui_shape_t *sh, int part, int state) {
    if (sh == NULL || sh->n_surf == 0 || part < 0 || part >= MOY_THEME_PARTS || state < 0
        || state >= MOY_THEME_STATES) {
        return NULL;
    }
    uint8_t id = sh->stid[part][state];
    return id != 0 && id <= sh->n_surf ? &sh->surf[id - 1] : NULL;
}

int moy_ui_shape_effect(const moy_ui_shape_t *sh, int part, int state, int *role) {
    if (sh == NULL || sh->n_fx == 0 || part < 0 || part >= MOY_THEME_PARTS || state < 0
        || state >= MOY_THEME_STATES) {
        return 0;
    }
    *role = sh->effect[part][state][1];
    return sh->effect[part][state][0];
}

// A colour term against `look`: the style's own colour `own` for
// MOY_UI_TERM_STYLE, a token's index, or the index itself.
static int term_ci(int look, int t, int own) {
    if (t == MOY_UI_TERM_STYLE) {
        return own;
    }
    return t < 0 ? moy_ui_token(look, -1 - t) : t;
}

// The ink a part's label takes in `state`: the style's for a kind, the frame's
// for the chrome; -1 for a part that paints no text.
static int part_ink(int look, int part, int state, int *field) {
    *field = -1;
    if (part < MOY_UI_KINDS) {
        if (state >= MOY_UI_STATES) {
            return -1;
        }
        int16_t c[3];
        moy_ui_colors(look, part, state, c);
        *field = c[0];
        return c[1];
    }
    switch (part) {
        case MOY_THEME_P_WINDOW_TITLE:
        case MOY_THEME_P_WINDOW_BUTTON:
            return moy_ui_token(look, state == MOY_THEME_S_INACTIVE ? MOY_UI_T_CHROME_INK_DIM
                                                                    : MOY_UI_T_TITLE_INK);
        case MOY_THEME_P_BAR:
        case MOY_THEME_P_BAR_ITEM:
        case MOY_THEME_P_DESK:
            return moy_ui_token(look, MOY_UI_T_CHROME_INK);
        default:
            return -1;
    }
}

static int readable(int a, int b) {
    if (a < 0 || a > 127 || b < 0 || b > 127) {
        return 1;
    }
    int32_t la = moy_theme_lum_of(a), lb = moy_theme_lum_of(b);
    int32_t hi = la > lb ? la : lb, lo = la > lb ? lb : la;
    return (hi + 500) * 10 >= MOY_THEME_CONTRAST * (lo + 500);
}

int moy_ui_pattern_unreadable(int look, const moy_ui_shape_t *sh, uint8_t (*out)[2], int cap) {
    int bad = 0;
    for (int part = 0; sh != NULL && part < MOY_THEME_PARTS; part++) {
        for (int state = 0; state < MOY_THEME_STATES; state++) {
            uint8_t id = sh->stid[part][state];
            if (id == 0 || id > sh->n_surf) {
                continue;
            }
            const moy_ui_surface_t *sf = &sh->surf[id - 1];
            if (sf->fill != MOY_UI_FILL_DITHER && sf->fill != MOY_UI_FILL_PINSTRIPE) {
                continue;
            }
            int field;
            int ink = part_ink(look, part, state, &field);
            if (ink < 0) {
                continue;
            }
            if (!readable(ink, term_ci(look, sf->a, field))
                || !readable(ink, term_ci(look, sf->b, field))) {
                if (bad < cap) {
                    out[bad][0] = (uint8_t)part;
                    out[bad][1] = (uint8_t)state;
                }
                bad++;
            }
        }
    }
    return bad;
}

// -- the icon set (moy_ui.h) ---------------------------------------------------------

static uint32_t s_icon_font[2];         // the 16 and 24 px masks
static uint8_t s_icon_code[3];

void moy_ui_icons_use(uint32_t f16, uint32_t f24, const uint8_t codes[3]) {
    s_icon_font[0] = f16;
    s_icon_font[1] = f24;
    memcpy(s_icon_code, codes, sizeof(s_icon_code));
}

// The slots, tools/make_icons.py's order (runtime/iconsets.py SLOTS;
// tests/test_ui_icon_progress.py holds the two equal).
static const char *const s_icon_slots[] = {
    "moy", "home", "wifi", "wifi_off", "battery", "search", "play", "projects",
    "paint", "storybook", "files", "calc", "getcarts", "settings", "music", "notes",
    "code", "sprite", "map", "blocks", "scene", "undo", "redo", "run",
    "save", "pencil", "bucket", "line", "rect", "oval", "select", "flip",
    "rotate", "game", "app", "art", "wallpaper", "doc", "folder", "trash",
    "close", "minimize", "maximize", "back", "forward", "down", "new", "dup",
    "del", "enter", "backspace", "cart_py", "cart_lua", "cart_wasm", "cart_p8", "mute",
};
#define ICON_SLOTS ((int)(sizeof(s_icon_slots) / sizeof(s_icon_slots[0])))
// An alias and the slot it names, which is also the glyph table's name.
static const char *const s_icon_alias[][2] = {
    {"gear", "settings"}, {"batt", "battery"}, {"edit", "pencil"},
};
#define ICON_ALIASES ((int)(sizeof(s_icon_alias) / sizeof(s_icon_alias[0])))

static int named(const char *a, const uint8_t *name, size_t n) {
    return strlen(a) == n && memcmp(a, name, n) == 0;
}

int moy_ui_icon_slot(const uint8_t *name, size_t n) {
    for (int k = 0; k < ICON_ALIASES; k++) {
        if (named(s_icon_alias[k][0], name, n)) {
            name = (const uint8_t *)s_icon_alias[k][1];
            n = strlen(s_icon_alias[k][1]);
            break;
        }
    }
    for (int i = 0; i < ICON_SLOTS; i++) {
        if (named(s_icon_slots[i], name, n)) {
            return i;
        }
    }
    return -1;
}

int moy_ui_icon_glyph(const uint8_t *name, size_t n) {
    int g = moy_ui_glyph_id((const char *)name, n);
    for (int k = 0; g < 0 && k < ICON_ALIASES; k++) {
        if (named(s_icon_alias[k][1], name, n)) {
            g = moy_ui_glyph_id(s_icon_alias[k][0], strlen(s_icon_alias[k][0]));
        }
    }
    return g;
}

uint32_t moy_ui_icon_font(int size24) {
    return s_icon_font[size24 ? 1 : 0];
}

int moy_ui_icon_button(const moy_text_target_t *t, int id, moy_ui_box_t b, uint16_t col) {
    if (id < 0 || id > 2 || s_icon_code[id] == 0) {
        return 0;
    }
    int box = b.w < b.h ? b.w : b.h;
    int k = box >= 24 && s_icon_font[1] != 0 ? 1 : 0;
    uint32_t font = s_icon_font[k] != 0 ? s_icon_font[k] : s_icon_font[1 - k];
    int size = font == s_icon_font[1] ? 24 : 16;
    if (font == 0) {
        return 0;
    }
    uint8_t code = s_icon_code[id];
    return moy_text(t, font, MOY_TEXT_DATA, &code, 1, b.x + moy_ui_fdiv(b.w - size, 2),
                    b.y + moy_ui_fdiv(b.h - size, 2), col, 1, MOY_TEXT_LEFT, -1) >= 0;
}

