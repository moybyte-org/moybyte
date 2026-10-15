// moy_ui's style resolution (moy_ui.h).

#include <string.h>

#include "moy_ui.h"

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

typedef struct {
    look_t look[MOY_UI_LOOKS];
    moy_ui_shape_t shape;
    uint32_t calls;                 // primitive entries: one per widget drawn
} ui_t;

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
    memset(l->have, 0, sizeof(l->have));
    for (int i = 0; i < MOY_THEME_SPRITE_SLOTS; i++) {
        int8_t t = moy_theme_sprite_slots[i];
        int32_t v = t == INT8_MIN ? MOY_UI_ABSENT : t >= 0 ? t : l->tok[-1 - t];
        l->slot[i] = (int16_t)(v == MOY_UI_ABSENT ? -1 : (v & 63));
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
    return v == MOY_UI_ABSENT ? -1 : (int)(v & 63);
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
    if (a < 0 || a > 63 || b < 0 || b > 63) {
        return 1;
    }
    int32_t la = moy_theme_lum[a], lb = moy_theme_lum[b];
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
