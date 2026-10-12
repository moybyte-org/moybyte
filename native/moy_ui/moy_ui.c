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
    uint32_t gen;                   // the skin generation `have` was built under
    uint8_t have[MOY_UI_KINDS];
    int16_t flat[MOY_UI_KINDS][MOY_UI_STATES][3];
} look_t;

typedef struct {
    look_t look[MOY_UI_LOOKS];
    moy_ui_shape_t shape;
    uint32_t gen;
    uint32_t calls;                 // primitive entries: one per widget drawn
    uint8_t skin;
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
        u->gen = 1;
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

int moy_ui_skin_use(const char *name, size_t n) {
    ui_t *u = ui();
    int s = find(moy_ui_skin_names, MOY_UI_SKINS, name, n);
    if (s < 0) {
        s = 0;
    }
    if (u != NULL) {
        u->skin = (uint8_t)s;
        u->gen++;
    }
    return s;
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

int moy_ui_skin(void) {
    return s_ui != NULL ? s_ui->skin : 0;
}

uint32_t moy_ui_gen(void) {
    return s_ui != NULL ? s_ui->gen : 1;
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
    l->gen = u->gen;
    return MOY_UI_OK;
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

static void flatten(const ui_t *u, look_t *l, int kind) {
    const uint8_t (*specs)[3] = moy_ui_kind_specs[u->skin][kind];
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
        || moy_ui_kind_specs[u->skin][kind][MOY_UI_REST][0] == 0xff) {
        kind = MOY_UI_K_ROW;
    }
    look_t *l = &u->look[look];
    if (l->gen != u->gen) {
        memset(l->have, 0, sizeof(l->have));
        l->gen = u->gen;
    }
    if (!l->have[kind]) {
        flatten(u, l, kind);
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
    return moy_ui_metric_vals[moy_ui_skin()][kind];
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
