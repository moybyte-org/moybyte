// The module `moy_ui` (moy_ui.h): what runtime/ui.py's face calls. Names in,
// ids out once; after that the face passes ints, and caches what it gets
// back, so a widget draw reaches here only on a cache miss.

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"
#include "py/objtuple.h"
#include "py/gc.h"

#include "moy_ui.h"
#include "moy_canvas.h"
#include "moy_theme.h"
#include "moy_str.h"
#include "moy_ui_text.h"
#include "moy_ui_wasm.h"

#define NAMES_CAP (MOY_THEME_PARTS > MOY_THEME_ROLES ? MOY_THEME_PARTS : MOY_THEME_ROLES)

static mp_obj_t names_tuple(const char *const *names, int n) {
    mp_obj_t items[NAMES_CAP];
    for (int i = 0; i < n; i++) {
        items[i] = mp_obj_new_str(names[i], strlen(names[i]));
    }
    return mp_obj_new_tuple((size_t)n, items);
}

static mp_obj_t ui_tokens(void) {
    return names_tuple(moy_ui_token_names, MOY_UI_TOKENS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_tokens_obj, ui_tokens);

static mp_obj_t ui_states(void) {
    return names_tuple(moy_ui_state_names, MOY_UI_STATES);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_states_obj, ui_states);

// kinds(): the kinds a style entry colours; metric_kinds(): those with slots.
static mp_obj_t ui_kinds(void) {
    return names_tuple(moy_ui_kind_names, MOY_UI_COLOR_KINDS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_kinds_obj, ui_kinds);

static mp_obj_t ui_metric_kinds(void) {
    mp_obj_t items[MOY_UI_KINDS];
    size_t n = 0;
    for (int k = 0; k < MOY_UI_KINDS; k++) {
        if (moy_ui_metric_n[k]) {
            items[n++] = mp_obj_new_str(moy_ui_kind_names[k], strlen(moy_ui_kind_names[k]));
        }
    }
    return mp_obj_new_tuple(n, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_metric_kinds_obj, ui_metric_kinds);

static mp_obj_t ui_kind(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_kind(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_kind_obj, ui_kind);

static mp_obj_t ui_button_kind(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_button_kind(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_button_kind_obj, ui_button_kind);

// -- themes (moy_theme.h) ----------------------------------------------------------

// themes(): the seed packages' names, the default first.
static mp_obj_t ui_themes(void) {
    return names_tuple(moy_theme_seed_names, MOY_THEME_SEEDS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_themes_obj, ui_themes);

// theme_roles(): the role row's names, the tokens first.
static mp_obj_t ui_theme_roles(void) {
    return names_tuple(moy_theme_role_names, MOY_THEME_ROLES);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_roles_obj, ui_theme_roles);

// theme(name, light) -> (row, unreadable, rc): the flattened role row as a
// tuple of MOY_THEME_ROLES ints, the readability bits, and OK or the refusal
// the default's row stands in for.
static mp_obj_t ui_theme(mp_obj_t name, mp_obj_t light) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    int32_t row[MOY_THEME_ROLES];
    uint32_t bad = 0;
    int rc = moy_theme_resolve(s, n, mp_obj_is_true(light) ? MOY_THEME_LIGHT
                               : MOY_THEME_DARK, row, &bad);
    mp_obj_t items[MOY_THEME_ROLES];
    for (int i = 0; i < MOY_THEME_ROLES; i++) {
        items[i] = mp_obj_new_int(row[i]);
    }
    mp_obj_t out[3] = {mp_obj_new_tuple(MOY_THEME_ROLES, items),
                       mp_obj_new_int_from_uint(bad), MP_OBJ_NEW_SMALL_INT(rc)};
    return mp_obj_new_tuple(3, out);
}

// theme_modes(name) -> the MOY_THEME_MODE_* bits theme `name` has
static mp_obj_t ui_theme_modes(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_theme_modes(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_modes_obj, ui_theme_modes);
static MP_DEFINE_CONST_FUN_OBJ_2(ui_theme_obj, ui_theme);

static mp_obj_t name_obj(const char *s) {
    return mp_obj_new_str(s, strlen(s));
}

static mp_obj_t list_tuple(mp_obj_t list) {
    size_t len;
    mp_obj_t *items;
    mp_obj_list_get(list, &len, &items);
    return mp_obj_new_tuple(len, items);
}

static mp_obj_t surface_obj(const moy_ui_surface_t *r) {
    mp_obj_t f[6] = {MP_OBJ_NEW_SMALL_INT(r->edge), MP_OBJ_NEW_SMALL_INT(r->fill),
                     MP_OBJ_NEW_SMALL_INT(r->flags), MP_OBJ_NEW_SMALL_INT(r->a),
                     MP_OBJ_NEW_SMALL_INT(r->b), MP_OBJ_NEW_SMALL_INT(r->line)};
    return mp_obj_new_tuple(6, f);
}

static void shape_of(mp_obj_t name, moy_ui_shape_t *sh) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    moy_theme_shape(s, n, sh);
}

// theme_shape(name) -> (metrics, effects, surfaces): the metrics as a tuple
// in style.json's order; each text effect other than none as (part, state,
// effect, role) of names; each surface as (part, state, record), the record
// (edge, fill, flags, a, b, line) as moy_ui.h's moy_ui_surface_t holds it.
static mp_obj_t ui_theme_shape(mp_obj_t name) {
    moy_ui_shape_t *sh = m_new(moy_ui_shape_t, 1);
    shape_of(name, sh);
    mp_obj_t mi[MOY_THEME_METRICS];
    for (int i = 0; i < MOY_THEME_METRICS; i++) {
        mi[i] = MP_OBJ_NEW_SMALL_INT(sh->metric[i]);
    }
    mp_obj_t fx = mp_obj_new_list(0, NULL), sf = mp_obj_new_list(0, NULL);
    for (int p = 0; p < MOY_THEME_PARTS; p++) {
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            const uint8_t *e = sh->effect[p][st];
            if (e[0] != 0) {
                mp_obj_t t[4] = {name_obj(moy_theme_part_names[p]),
                                 name_obj(moy_theme_state_names[st]),
                                 name_obj(moy_theme_effect_names[e[0]]),
                                 name_obj(moy_theme_role_names[e[1]])};
                mp_obj_list_append(fx, mp_obj_new_tuple(4, t));
            }
            if (sh->stid[p][st] != 0) {
                mp_obj_t t[3] = {name_obj(moy_theme_part_names[p]),
                                 name_obj(moy_theme_state_names[st]),
                                 surface_obj(&sh->surf[sh->stid[p][st] - 1])};
                mp_obj_list_append(sf, mp_obj_new_tuple(3, t));
            }
        }
    }
    m_del(moy_ui_shape_t, sh, 1);
    mp_obj_t out[3] = {mp_obj_new_tuple(MOY_THEME_METRICS, mi), list_tuple(fx), list_tuple(sf)};
    return mp_obj_new_tuple(3, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_shape_obj, ui_theme_shape);

// surface(text) -> (record, notes), or None for text the grammar refuses.
static mp_obj_t ui_surface(mp_obj_t text) {
    size_t n;
    const char *s = mp_obj_str_get_data(text, &n);
    moy_ui_surface_t r;
    uint8_t notes = 0;
    if (moy_theme_surface(s, n, &r, &notes) != MOY_THEME_OK) {
        return mp_const_none;
    }
    mp_obj_t out[2] = {surface_obj(&r), MP_OBJ_NEW_SMALL_INT(notes)};
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_surface_obj, ui_surface);

// use_shape(name): install theme `name`'s shape for every primitive (None:
// the empty one); the resolve's return.
static mp_obj_t ui_use_shape(mp_obj_t name) {
    int rc = MOY_THEME_OK;
    if (name == mp_const_none) {
        moy_ui_shape_use(NULL);
    } else {
        moy_ui_shape_t *sh = m_new(moy_ui_shape_t, 1);
        size_t n;
        const char *s = mp_obj_str_get_data(name, &n);
        rc = moy_theme_shape(s, n, sh);
        moy_ui_shape_use(sh);
        m_del(moy_ui_shape_t, sh, 1);
    }
    return MP_OBJ_NEW_SMALL_INT(rc);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_use_shape_obj, ui_use_shape);

// pattern_unreadable(look): the installed shape's pattern fills whose label
// ink misses the contrast floor in `look`, as (part, state) name pairs.
static mp_obj_t ui_pattern_unreadable(mp_obj_t look) {
    uint8_t bad[16][2];
    int n = moy_ui_pattern_unreadable(mp_obj_get_int(look), moy_ui_shape(), bad, 16);
    n = n > 16 ? 16 : n;
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        const char *p = moy_theme_part_names[bad[i][0]];
        const char *st = moy_theme_state_names[bad[i][1]];
        mp_obj_t pair[2] = {mp_obj_new_str(p, strlen(p)), mp_obj_new_str(st, strlen(st))};
        mp_obj_list_append(out, mp_obj_new_tuple(2, pair));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_pattern_unreadable_obj, ui_pattern_unreadable);

// parts(), theme_states(): what a theme's `text` and `surfaces` are keyed by.
static mp_obj_t ui_parts(void) {
    return names_tuple(moy_theme_part_names, MOY_THEME_PARTS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_parts_obj, ui_parts);

static mp_obj_t ui_theme_states(void) {
    return names_tuple(moy_theme_state_names, MOY_THEME_STATES);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_states_obj, ui_theme_states);

// theme_install(src) -> (rc, notes): src a theme.json's bytes.
static mp_obj_t ui_theme_install(mp_obj_t src) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(src, &b, MP_BUFFER_READ);
    uint8_t notes = 0;
    int rc = moy_theme_install(b.buf, b.len, &notes);
    mp_obj_t out[2] = {MP_OBJ_NEW_SMALL_INT(rc), MP_OBJ_NEW_SMALL_INT(notes)};
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_install_obj, ui_theme_install);

// theme_installed(): the installed package's name, or None.
static mp_obj_t ui_theme_installed(void) {
    const char *s = moy_theme_installed();
    return s == NULL ? mp_const_none : mp_obj_new_str(s, strlen(s));
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_installed_obj, ui_theme_installed);

// theme_uninstall(): drop the installed package.
static mp_obj_t ui_theme_uninstall(void) {
    moy_theme_uninstall();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_uninstall_obj, ui_theme_uninstall);

// theme_fonts(name) -> ((role, file), ...): theme `name`'s font files, each
// from the nearest package that names one, in style.json's role order.
static mp_obj_t ui_theme_fonts(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    char files[MOY_THEME_FONT_ROLES][MOY_THEME_NAME_MAX + 1];
    moy_theme_fonts(s, n, files);
    mp_obj_t out[MOY_THEME_FONT_ROLES];
    size_t k = 0;
    for (int i = 0; i < MOY_THEME_FONT_ROLES; i++) {
        if (files[i][0]) {
            mp_obj_t t[2] = {name_obj(moy_theme_font_roles[i]),
                             mp_obj_new_str(files[i], strlen(files[i]))};
            out[k++] = mp_obj_new_tuple(2, t);
        }
    }
    return mp_obj_new_tuple(k, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_fonts_obj, ui_theme_fonts);

// A sheet key's file, or None.
static mp_obj_t sheet_name(const char *f) {
    return f == NULL || !f[0] ? mp_const_none : mp_obj_new_str(f, strlen(f));
}

// theme_sheets(name) -> (sprites, icons): theme `name`'s sheet files, each
// from the nearest package that names one, None where none does.
static mp_obj_t ui_theme_sheets(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    char files[MOY_THEME_SHEETS][MOY_THEME_NAME_MAX + 1];
    moy_theme_sheets(s, n, files);
    mp_obj_t out[MOY_THEME_SHEETS];
    for (int i = 0; i < MOY_THEME_SHEETS; i++) {
        out[i] = sheet_name(files[i]);
    }
    return mp_obj_new_tuple(MOY_THEME_SHEETS, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_sheets_obj, ui_theme_sheets);

// sheet_check(src, rows) -> (rc, rows): a sheet's bytes read as the loader
// reads one of at most `rows` rows (moy_theme_sheet_check).
static mp_obj_t ui_sheet_check(mp_obj_t src, mp_obj_t rows_max) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(src, &b, MP_BUFFER_READ);
    int rows = 0;
    int rc = moy_theme_sheet_check(b.buf, b.len, mp_obj_get_int(rows_max), &rows);
    mp_obj_t out[2] = {MP_OBJ_NEW_SMALL_INT(rc), MP_OBJ_NEW_SMALL_INT(rc == 0 ? rows : 0)};
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_sheet_check_obj, ui_sheet_check);

// sheet_load(src) -> rc: the role sheet every sprite part draws from
// (moy_theme_sheet_load); a refusal leaves none. sheet_clear(): none.
static mp_obj_t ui_sheet_load(mp_obj_t src) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(src, &b, MP_BUFFER_READ);
    return MP_OBJ_NEW_SMALL_INT(moy_theme_sheet_load(b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_sheet_load_obj, ui_sheet_load);

static mp_obj_t ui_sheet_clear(void) {
    moy_theme_sheet_clear();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_sheet_clear_obj, ui_sheet_clear);

// sheet_cells() -> ((part, state, button, x, y, w, h, nine), ...): the role
// sheet's cells in pixels (style.json's theme.sprite_cells; button is a
// title button's index of frame_button_names, else 0), and whether each is
// set in the loaded sheet: sheet_set() -> (bool, ...).
static mp_obj_t ui_sheet_cells(void) {
    mp_obj_t out[MOY_THEME_CELLS];
    for (int i = 0; i < MOY_THEME_CELLS; i++) {
        const moy_theme_cell_t *c = &moy_theme_cells[i];
        mp_obj_t t[8] = {name_obj(moy_theme_part_names[c->part]),
                         name_obj(moy_theme_state_names[c->state]),
                         MP_OBJ_NEW_SMALL_INT(c->sub), MP_OBJ_NEW_SMALL_INT(c->x),
                         MP_OBJ_NEW_SMALL_INT(c->y), MP_OBJ_NEW_SMALL_INT(c->w),
                         MP_OBJ_NEW_SMALL_INT(c->h), mp_obj_new_bool(c->nine)};
        out[i] = mp_obj_new_tuple(8, t);
    }
    return mp_obj_new_tuple(MOY_THEME_CELLS, out);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_sheet_cells_obj, ui_sheet_cells);

static mp_obj_t ui_sheet_set(void) {
    mp_obj_t out[MOY_THEME_CELLS];
    for (int i = 0; i < MOY_THEME_CELLS; i++) {
        const moy_theme_cell_t *c = &moy_theme_cells[i];
        out[i] = mp_obj_new_bool(moy_theme_sheet_cell(c->part, c->state, c->sub) == i);
    }
    return mp_obj_new_tuple(MOY_THEME_CELLS, out);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_sheet_set_obj, ui_sheet_set);

// sheet_caps() -> (width, sprite rows, icon rows, sprite bytes, icon bytes).
static mp_obj_t ui_sheet_caps(void) {
    mp_obj_t out[5] = {MP_OBJ_NEW_SMALL_INT(MOY_THEME_SHEET_W),
                       MP_OBJ_NEW_SMALL_INT(MOY_THEME_SPRITES_MAX_ROWS),
                       MP_OBJ_NEW_SMALL_INT(MOY_THEME_ICONS_MAX_ROWS),
                       MP_OBJ_NEW_SMALL_INT(MOY_THEME_SHEET_BYTES(MOY_THEME_SPRITES_MAX_ROWS)),
                       MP_OBJ_NEW_SMALL_INT(MOY_THEME_SHEET_BYTES(MOY_THEME_ICONS_MAX_ROWS))};
    return mp_obj_new_tuple(5, out);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_sheet_caps_obj, ui_sheet_caps);

// theme_layout(name) -> (refused, bars): theme `name`'s layout, whole, from
// the nearest package that sets one (moy_theme_layout); refused is True when
// the default's stands in. A bar is (edge, size, left, center, right,
// texts): each slot a tuple of widget names, texts its `text` widgets'
// strings in slot order.
static mp_obj_t ui_theme_layout(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    moy_theme_layout_t lay;
    moy_theme_layout(s, n, &lay);
    mp_obj_t bars[MOY_THEME_BARS];
    for (int i = 0; i < lay.n_bars; i++) {
        const moy_theme_bar_t *b = &lay.bar[i];
        mp_obj_t t[3 + MOY_THEME_SLOTS];
        t[0] = name_obj(moy_theme_bar_edges[b->edge]);
        t[1] = MP_OBJ_NEW_SMALL_INT(b->size);
        for (int sl = 0; sl < MOY_THEME_SLOTS; sl++) {
            mp_obj_t w[MOY_THEME_SLOT_CAP];
            for (int k = 0; k < b->n[sl]; k++) {
                w[k] = name_obj(moy_theme_widget_names[b->w[sl][k]]);
            }
            t[2 + sl] = mp_obj_new_tuple(b->n[sl], w);
        }
        mp_obj_t tx[MOY_THEME_WIDGET_CAP];
        for (int k = 0; k < b->n_text; k++) {
            tx[k] = name_obj(lay.text[b->text0 + k]);
        }
        t[2 + MOY_THEME_SLOTS] = mp_obj_new_tuple(b->n_text, tx);
        bars[i] = mp_obj_new_tuple(3 + MOY_THEME_SLOTS, t);
    }
    mp_obj_t out[2] = {mp_obj_new_bool(lay.set == MOY_THEME_LAYOUT_REFUSED),
                       mp_obj_new_tuple(lay.n_bars, bars)};
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_layout_obj, ui_theme_layout);

// theme_home(name) -> the home's arrangement in theme `name`'s layout (the
// one moy_theme_layout resolves), a name of moy_theme_home_names.
static mp_obj_t ui_theme_home(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    moy_theme_layout_t lay;
    moy_theme_layout(s, n, &lay);
    return name_obj(moy_theme_home_names[lay.home]);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_home_obj, ui_theme_home);

// theme_resolve(src) -> (rc, notes, name, dark, light, fonts, layout, sheets,
// looks, backdrops, modes): a stored package resolved from its bytes without
// installing it (moy_theme_probe). dark and light are theme()'s answers, fonts
// the package's OWN (role, file) pairs (its folder provides them), layout
// (refused, bars, home) as theme_layout and theme_home answer, sheets the
// package's OWN (sprites, icons) files, None where it names none, looks
// theme_look's (dark, light) and backdrops theme_backdrop's ((dark home, dark
// desk), (light home, light desk)), modes theme_modes'. On a refusal every
// field after notes is None.
// -- the look and RGB (docs/look_2026-10.md step 5) ---------------------------

static mp_obj_t ints(const int32_t *v, int n) {
    mp_obj_t t = mp_obj_new_tuple(n, NULL);
    mp_obj_tuple_t *tt = MP_OBJ_TO_PTR(t);
    for (int i = 0; i < n; i++) {
        tt->items[i] = mp_obj_new_int(v[i]);
    }
    return t;
}

// theme_look(name, light) -> (leaves, paints, tiles, app_buttons, texts, rc):
// the look resolved with the model's defaults (moy_theme_look); a paint is
// (mode, steps, ((t, colour), ...)) with t in thousandths, a tile (app,
// colour), an app_buttons entry (app, kind, leaves).
static mp_obj_t ui_theme_look(mp_obj_t name, mp_obj_t light) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    moy_theme_look_t *lk = m_new(moy_theme_look_t, 1);
    int rc = moy_theme_look(s, n, mp_obj_is_true(light) ? MOY_THEME_LIGHT : MOY_THEME_DARK,
                            1, lk);
    mp_obj_t out[6];
    out[0] = ints(lk->v, MOY_THEME_LEAVES);
    mp_obj_t paints = mp_obj_new_tuple(lk->n_paints, NULL);
    for (int i = 0; i < lk->n_paints; i++) {
        const moy_theme_paint_t *p = &lk->paint[i];
        mp_obj_t stops = mp_obj_new_tuple(p->n, NULL);
        for (int k = 0; k < p->n; k++) {
            mp_obj_t st[2] = {MP_OBJ_NEW_SMALL_INT(p->t[k]), mp_obj_new_int(p->c[k])};
            ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(stops))->items[k] = mp_obj_new_tuple(2, st);
        }
        mp_obj_t pt[3] = {name_obj(moy_theme_paint_modes[p->mode]),
                          MP_OBJ_NEW_SMALL_INT(p->steps), stops};
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(paints))->items[i] = mp_obj_new_tuple(3, pt);
    }
    out[1] = paints;
    mp_obj_t tiles = mp_obj_new_tuple(lk->n_tiles, NULL);
    for (int i = 0; i < lk->n_tiles; i++) {
        mp_obj_t t[2] = {mp_obj_new_str(lk->tile[i].name, strlen(lk->tile[i].name)),
                         mp_obj_new_int(lk->tile[i].v[0])};
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(tiles))->items[i] = mp_obj_new_tuple(2, t);
    }
    out[2] = tiles;
    mp_obj_t ab = mp_obj_new_tuple(lk->n_appbtn, NULL);
    for (int i = 0; i < lk->n_appbtn; i++) {
        const moy_theme_mapent_t *e = &lk->appbtn[i];
        mp_obj_t t[3] = {mp_obj_new_str(e->name, strlen(e->name)),
                         name_obj(moy_theme_btn_kinds[e->kind]),
                         ints(e->v, MOY_THEME_BTN_LEAVES)};
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(ab))->items[i] = mp_obj_new_tuple(3, t);
    }
    out[3] = ab;
    mp_obj_t texts = mp_obj_new_tuple(lk->n_text, NULL);
    for (int i = 0; i < lk->n_text; i++) {
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(texts))->items[i] =
            mp_obj_new_str(lk->text[i], strlen(lk->text[i]));
    }
    out[4] = texts;
    out[5] = MP_OBJ_NEW_SMALL_INT(rc);
    m_del(moy_theme_look_t, lk, 1);
    return mp_obj_new_tuple(6, out);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_theme_look_obj, ui_theme_look);

// theme_backdrop(name, light, surface) -> bytes: the backdrop of `surface`
// ("home" or "desk") as moy_gfx.backdrop's records (moy_theme_backdrop);
// empty where the surface has none.
static mp_obj_t ui_theme_backdrop(mp_obj_t name, mp_obj_t light, mp_obj_t surface) {
    size_t n, sn;
    const char *s = mp_obj_str_get_data(name, &n);
    const char *sf = mp_obj_str_get_data(surface, &sn);
    int k = -1;
    for (int i = 0; i < MOY_THEME_BD_SURFACES; i++) {
        if (strlen(moy_theme_bd_surfaces[i]) == sn && memcmp(moy_theme_bd_surfaces[i], sf, sn) == 0) {
            k = i;
        }
    }
    if (k < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("surface"));
    }
    uint8_t *out = m_new(uint8_t, MOY_THEME_BD_BYTES);
    int got = moy_theme_backdrop(s, n, mp_obj_is_true(light) ? MOY_THEME_LIGHT : MOY_THEME_DARK,
                                 k, out, MOY_THEME_BD_BYTES);
    mp_obj_t b = mp_obj_new_bytes(out, got > 0 ? (size_t)got : 0);
    m_del(uint8_t, out, MOY_THEME_BD_BYTES);
    return b;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_theme_backdrop_obj, ui_theme_backdrop);

// theme_look_fields() -> (leaf names, leaf kinds, app_buttons leaf names):
// the look's vocabulary, what theme_look's tuples are in the order of.
static mp_obj_t ui_theme_look_fields(void) {
    static const char *const kinds[] = {"colour", "paint", "enum", "int", "num", "bool",
                                        "text", "corners", "bits"};
    mp_obj_t names = mp_obj_new_tuple(MOY_THEME_LEAVES, NULL);
    mp_obj_t ks = mp_obj_new_tuple(MOY_THEME_LEAVES, NULL);
    for (int i = 0; i < MOY_THEME_LEAVES; i++) {
        const moy_theme_leaf_t *l = &moy_theme_leaves[i];
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(names))->items[i] = name_obj(moy_theme_leaf_names[i]);
        mp_obj_t k = name_obj(kinds[l->kind]);
        if (l->kind == MOY_THEME_LK_ENUM) {
            mp_obj_t en = mp_obj_new_tuple(l->hi, NULL);
            for (int j = 0; j < l->hi; j++) {
                ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(en))->items[j] =
                    name_obj(moy_theme_enum_names[l->lo + j]);
            }
            mp_obj_t pair[2] = {k, en};
            k = mp_obj_new_tuple(2, pair);
        }
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(ks))->items[i] = k;
    }
    mp_obj_t b = mp_obj_new_tuple(MOY_THEME_BTN_LEAVES, NULL);
    for (int i = 0; i < MOY_THEME_BTN_LEAVES; i++) {
        const char *full = moy_theme_leaf_names[MOY_THEME_BTN_LEAF0 + i];
        const char *rest = strchr(strchr(full, '.') + 1, '.') + 1;   // buttons.<kind>.
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(b))->items[i] = name_obj(rest);
    }
    mp_obj_t out[3] = {names, ks, b};
    return mp_obj_new_tuple(3, out);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_look_fields_obj, ui_theme_look_fields);

// theme_place(values) -> (placed, near, block, used): moy_theme_place over
// a sequence of colour values.
static mp_obj_t ui_theme_place(mp_obj_t values) {
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(values, &n, &items);
    int32_t *in = m_new(int32_t, 3 * n + 1);
    for (size_t i = 0; i < n; i++) {
        in[i] = (int32_t)mp_obj_get_int_truncated(items[i]);
    }
    uint16_t block[MOY_THEME_BLOCK];
    int used = moy_theme_place(in, (int)n, in + n, in + 2 * n, block);
    mp_obj_t bl = mp_obj_new_tuple(MOY_THEME_BLOCK, NULL);
    for (int i = 0; i < MOY_THEME_BLOCK; i++) {
        ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(bl))->items[i] = MP_OBJ_NEW_SMALL_INT(block[i]);
    }
    mp_obj_t out[4] = {ints(in + n, (int)n), ints(in + 2 * n, (int)n), bl,
                       MP_OBJ_NEW_SMALL_INT(used)};
    m_del(int32_t, in, 3 * n + 1);
    return mp_obj_new_tuple(4, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_place_obj, ui_theme_place);

// theme_block(block, used): the live theme's block (canonical RGB565 words),
// which the readability check reads a slot 64.. through.
static mp_obj_t ui_theme_block(mp_obj_t block, mp_obj_t used) {
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(block, &n, &items);
    uint16_t b[MOY_THEME_BLOCK] = {0};
    for (size_t i = 0; i < n && i < MOY_THEME_BLOCK; i++) {
        b[i] = (uint16_t)mp_obj_get_int(items[i]);
    }
    moy_theme_block_use(b, (int)mp_obj_get_int(used));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_theme_block_obj, ui_theme_block);

// theme_rgb(v) -> (rgb565, luminance x10000, nearest MOY64 index).
static mp_obj_t ui_theme_rgb(mp_obj_t v) {
    int32_t x = (int32_t)mp_obj_get_int_truncated(v);
    mp_obj_t out[3] = {MP_OBJ_NEW_SMALL_INT(moy_theme_rgb565(x)),
                       MP_OBJ_NEW_SMALL_INT(moy_theme_lum_of(x)),
                       MP_OBJ_NEW_SMALL_INT(moy_theme_nearest(x))};
    return mp_obj_new_tuple(3, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_rgb_obj, ui_theme_rgb);

static mp_obj_t ui_theme_resolve(mp_obj_t src) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(src, &b, MP_BUFFER_READ);
    moy_theme_pkg_t *pkg = m_new(moy_theme_pkg_t, 1);
    uint8_t notes = 0;
    int rc = moy_theme_probe(b.buf, b.len, pkg, &notes);
    mp_obj_t out[11] = {MP_OBJ_NEW_SMALL_INT(rc), MP_OBJ_NEW_SMALL_INT(notes),
                        mp_const_none, mp_const_none, mp_const_none, mp_const_none,
                        mp_const_none, mp_const_none, mp_const_none, mp_const_none,
                        mp_const_none};
    if (rc == MOY_THEME_OK) {
        nlr_buf_t nlr;
        if (nlr_push(&nlr) == 0) {
            mp_obj_t name = mp_obj_new_str(pkg->name, strlen(pkg->name));
            out[2] = name;
            out[3] = ui_theme(name, mp_const_false);
            out[4] = ui_theme(name, mp_const_true);
            mp_obj_t fonts[MOY_THEME_FONT_ROLES];
            size_t k = 0;
            for (int i = 0; i < MOY_THEME_FONT_ROLES; i++) {
                if (pkg->font[i][0]) {
                    mp_obj_t t[2] = {name_obj(moy_theme_font_roles[i]),
                                     mp_obj_new_str(pkg->font[i], strlen(pkg->font[i]))};
                    fonts[k++] = mp_obj_new_tuple(2, t);
                }
            }
            out[5] = mp_obj_new_tuple(k, fonts);
            mp_obj_t lay = ui_theme_layout(name);
            mp_obj_t *l;
            mp_obj_get_array_fixed_n(lay, 2, &l);
            mp_obj_t t[3] = {l[0], l[1], ui_theme_home(name)};
            out[6] = mp_obj_new_tuple(3, t);
            mp_obj_t sh[MOY_THEME_SHEETS];
            for (int i = 0; i < MOY_THEME_SHEETS; i++) {
                sh[i] = sheet_name(moy_theme_probe_sheet(i));
            }
            out[7] = mp_obj_new_tuple(MOY_THEME_SHEETS, sh);
            mp_obj_t lk[2] = {ui_theme_look(name, mp_const_false),
                              ui_theme_look(name, mp_const_true)};
            out[8] = mp_obj_new_tuple(2, lk);
            mp_obj_t bd[2];
            for (int v = 0; v < 2; v++) {
                mp_obj_t sf[MOY_THEME_BD_SURFACES];
                for (int i = 0; i < MOY_THEME_BD_SURFACES; i++) {
                    sf[i] = ui_theme_backdrop(name, mp_obj_new_bool(v),
                                              name_obj(moy_theme_bd_surfaces[i]));
                }
                bd[v] = mp_obj_new_tuple(MOY_THEME_BD_SURFACES, sf);
            }
            out[9] = mp_obj_new_tuple(2, bd);
            out[10] = ui_theme_modes(name);
            nlr_pop();
        } else {
            moy_theme_probe_end();
            m_del(moy_theme_pkg_t, pkg, 1);
            nlr_jump(nlr.ret_val);
        }
    }
    moy_theme_probe_end();
    m_del(moy_theme_pkg_t, pkg, 1);
    return mp_obj_new_tuple(11, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_theme_resolve_obj, ui_theme_resolve);

// theme_custom(source, accent, tint) -> (rc, bytes or None): the bytes of
// `custom`'s theme.json (moy_theme_custom) derived from `source`, a seed's
// name or a stored package's bytes; accent the A of a theme_accents() entry,
// the dark value of a theme_presets() entry, or None for the source's own, tint a theme_tints() name or None. A name
// that is no seed answers MOY_THEME_CHAIN.
static mp_obj_t ui_theme_custom(mp_obj_t source, mp_obj_t accent, mp_obj_t tint) {
    const uint8_t *src;
    size_t n;
    if (mp_obj_is_str(source)) {
        size_t sn;
        const char *s = mp_obj_str_get_data(source, &sn);
        int seed = -1;
        for (int i = 0; i < MOY_THEME_SEEDS; i++) {
            if (strlen(moy_theme_seed_names[i]) == sn && memcmp(moy_theme_seed_names[i], s, sn) == 0) {
                seed = i;
            }
        }
        if (seed < 0) {
            mp_obj_t out[2] = {MP_OBJ_NEW_SMALL_INT(MOY_THEME_CHAIN), mp_const_none};
            return mp_obj_new_tuple(2, out);
        }
        src = (const uint8_t *)moy_theme_seed_src[seed];
        n = moy_theme_seed_len[seed];
    } else {
        mp_buffer_info_t b;
        mp_get_buffer_raise(source, &b, MP_BUFFER_READ);
        src = b.buf;
        n = b.len;
    }
    int ai = -1, ti = -1;
    if (accent != mp_const_none) {
        mp_int_t a = mp_obj_get_int(accent);
        for (int i = 0; i < MOY_THEME_ACCENTS && ai < 0; i++) {
            ai = moy_theme_accents[i][0] == a ? i : -1;
        }
        for (int i = 0; i < MOY_THEME_PRESETS && ai < 0; i++) {
            ai = moy_theme_presets[i][0] == a ? MOY_THEME_ACCENTS + i : -1;
        }
        if (ai < 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("no such accent"));
        }
    }
    if (tint != mp_const_none) {
        size_t tn;
        const char *t = mp_obj_str_get_data(tint, &tn);
        for (int i = 0; i < MOY_THEME_TINTS && ti < 0; i++) {
            ti = strlen(moy_theme_tint_names[i]) == tn && memcmp(moy_theme_tint_names[i], t, tn) == 0
                 ? i : -1;
        }
        if (ti < 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("no such tint"));
        }
    }
    moy_theme_pkg_t *scratch = m_new(moy_theme_pkg_t, 2);
    uint8_t *buf = m_new(uint8_t, MOY_THEME_BYTES);
    size_t len = 0;
    int rc = moy_theme_custom(src, n, ai, ti, scratch, buf, MOY_THEME_BYTES, &len);
    m_del(moy_theme_pkg_t, scratch, 2);
    mp_obj_t out[2] = {MP_OBJ_NEW_SMALL_INT(rc), mp_const_none};
    if (rc == MOY_THEME_OK) {
        out[1] = mp_obj_new_bytes(buf, len);
    }
    m_del(uint8_t, buf, MOY_THEME_BYTES);
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_theme_custom_obj, ui_theme_custom);

// theme_accents() -> ((name, A, deep), ...): style.json's theme.accents.
static mp_obj_t ui_theme_accents(void) {
    mp_obj_t items[MOY_THEME_ACCENTS];
    for (int i = 0; i < MOY_THEME_ACCENTS; i++) {
        mp_obj_t t[3] = {name_obj(moy_theme_accent_names[i]),
                         MP_OBJ_NEW_SMALL_INT(moy_theme_accents[i][0]),
                         MP_OBJ_NEW_SMALL_INT(moy_theme_accents[i][1])};
        items[i] = mp_obj_new_tuple(3, t);
    }
    return mp_obj_new_tuple(MOY_THEME_ACCENTS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_accents_obj, ui_theme_accents);

// theme_presets() -> ((name, dark, light), ...): style.json's theme.presets.
static mp_obj_t ui_theme_presets(void) {
    mp_obj_t items[MOY_THEME_PRESETS];
    for (int i = 0; i < MOY_THEME_PRESETS; i++) {
        mp_obj_t t[3] = {name_obj(moy_theme_preset_names[i]),
                         MP_OBJ_NEW_SMALL_INT(moy_theme_presets[i][0]),
                         MP_OBJ_NEW_SMALL_INT(moy_theme_presets[i][1])};
        items[i] = mp_obj_new_tuple(3, t);
    }
    return mp_obj_new_tuple(MOY_THEME_PRESETS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_presets_obj, ui_theme_presets);

// theme_tints() -> ((name, (ramp...)), ...): style.json's theme.tints.
static mp_obj_t ui_theme_tints(void) {
    mp_obj_t items[MOY_THEME_TINTS];
    for (int i = 0; i < MOY_THEME_TINTS; i++) {
        mp_obj_t ramp[MOY_THEME_RAMP_MAX];
        for (int k = 0; k < moy_theme_tints[i][0]; k++) {
            ramp[k] = MP_OBJ_NEW_SMALL_INT(moy_theme_tints[i][1 + k]);
        }
        mp_obj_t t[2] = {name_obj(moy_theme_tint_names[i]),
                         mp_obj_new_tuple(moy_theme_tints[i][0], ramp)};
        items[i] = mp_obj_new_tuple(2, t);
    }
    return mp_obj_new_tuple(MOY_THEME_TINTS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_theme_tints_obj, ui_theme_tints);

// font_load(src) -> (rc, font): src a font file's bytes (moy_text.h); font
// the loaded row's id, or None when refused.
static mp_obj_t ui_font_load(mp_obj_t src) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(src, &b, MP_BUFFER_READ);
    uint32_t id = 0;
    int rc = moy_font_load(b.buf, b.len, &id);
    mp_obj_t out[2] = {MP_OBJ_NEW_SMALL_INT(rc),
                       rc == MOY_FONT_OK ? mp_obj_new_int_from_uint(id) : mp_const_none};
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_font_load_obj, ui_font_load);

// font_free(font) -> bool: False for an id that names no loaded font.
static mp_obj_t ui_font_free(mp_obj_t font) {
    return mp_obj_new_bool(moy_font_free((uint32_t)mp_obj_get_int_truncated(font)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_font_free_obj, ui_font_free);

// font_reset(): free every loaded font (moy_text.h).
static mp_obj_t ui_font_reset(void) {
    moy_font_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_font_reset_obj, ui_font_reset);

// text_wire() -> bool: whether the glyph lane blends byte-swapped words;
// text_wire(swapped) sets it (moy_text.h) and answers what it was.
static mp_obj_t ui_text_wire(size_t n_args, const mp_obj_t *a) {
    int was = moy_text_wire_swapped();
    if (n_args > 0) {
        moy_text_wire(mp_obj_is_true(a[0]));
    }
    return mp_obj_new_bool(was);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_wire_obj, 0, 1, ui_text_wire);

// text_lanes() -> (store, read): the version 2 glyphs drawn since boot that
// read nothing and that read the target (moy_text_lanes).
static mp_obj_t ui_text_lanes(void) {
    uint32_t n[2];
    moy_text_lanes(n);
    mp_obj_t t[2] = {mp_obj_new_int_from_uint(n[0]), mp_obj_new_int_from_uint(n[1])};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_text_lanes_obj, ui_text_lanes);

static mp_obj_t ret_tuple(size_t n, const mp_obj_t *items);

// font_info(font) -> (w, h, line, ascent, first, count), or None.
static mp_obj_t ui_font_info(mp_obj_t font) {
    moy_font_info_t f;
    if (!moy_font_info((uint32_t)mp_obj_get_int_truncated(font), &f)) {
        return mp_const_none;
    }
    mp_obj_t t[6] = {MP_OBJ_NEW_SMALL_INT(f.w), MP_OBJ_NEW_SMALL_INT(f.h),
                     MP_OBJ_NEW_SMALL_INT(f.line), MP_OBJ_NEW_SMALL_INT(f.ascent),
                     MP_OBJ_NEW_SMALL_INT(f.first), MP_OBJ_NEW_SMALL_INT(f.count)};
    return ret_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_font_info_obj, ui_font_info);

// A font role's index by name (style.json's theme.font_roles); ValueError
// for another name.
static int font_role(mp_obj_t name) {
    const char *r = mp_obj_str_get_str(name);
    for (int i = 0; i < MOY_THEME_FONT_ROLES; i++) {
        if (strcmp(r, moy_theme_font_roles[i]) == 0) {
            return i;
        }
    }
    mp_raise_ValueError(MP_ERROR_TEXT("font role"));
}

// use_font(role, font) -> bool: the toolkit's `role` text draws in `font`
// (0: petme128); False for a font that is not loaded, which installs nothing.
static mp_obj_t ui_use_font(mp_obj_t role, mp_obj_t font) {
    return mp_obj_new_bool(
        moy_ui_font_use(font_role(role), (uint32_t)mp_obj_get_int_truncated(font)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_use_font_obj, ui_use_font);

// font(role) -> (font, w, line, ascent): the font `role` draws in, its glyph
// box's width, its line advance and its baseline.
static mp_obj_t ui_font(mp_obj_t role) {
    moy_font_info_t f;
    uint32_t id = moy_ui_font(font_role(role), &f);
    mp_obj_t t[4] = {mp_obj_new_int_from_uint(id), MP_OBJ_NEW_SMALL_INT(f.w),
                     MP_OBJ_NEW_SMALL_INT(f.line), MP_OBJ_NEW_SMALL_INT(f.ascent)};
    return ret_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_font_obj, ui_font);

// font_file(name) -> bytes, or None: a seed package's font, or a font's licence text.
static mp_obj_t ui_font_file(mp_obj_t name) {
    size_t n, len;
    const char *s = mp_obj_str_get_data(name, &n);
    const uint8_t *p = moy_theme_font_file(s, n, &len);
    return p == NULL ? mp_const_none : mp_obj_new_bytes(p, len);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_font_file_obj, ui_font_file);

// print_font() -> the font a canvas's print draws in now; print_font(font) ->
// the font it replaced, or None for a font that is not loaded (which installs
// nothing).
static mp_obj_t ui_print_font(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        return mp_obj_new_int_from_uint(moy_text_print_font());
    }
    int64_t was = moy_ui_print_font((uint32_t)mp_obj_get_int_truncated(args[0]));
    return was < 0 ? mp_const_none : mp_obj_new_int_from_uint((uint32_t)was);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_print_font_obj, 0, 1, ui_print_font);

// cart_text(on) -> bool: the app-cart boundary (moy_ui.h); whether it was on.
static mp_obj_t ui_cart_text(mp_obj_t on) {
    moy_canvas_cart(mp_obj_is_true(on));    // the canvases' colour mask with it
    return mp_obj_new_bool(moy_ui_cart_text(mp_obj_is_true(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_cart_text_obj, ui_cart_text);

static void check(int rc) {
    if (rc == MOY_UI_NOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (rc != MOY_UI_OK) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: bad argument"));
    }
}

// look(slot, tokens): point a look at a token row, an array('i') of
// MOY_UI_TOKENS values.
static mp_obj_t ui_look(mp_obj_t slot, mp_obj_t tokens) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(tokens, &b, MP_BUFFER_READ);
    if (b.len != MOY_UI_TOKENS * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: a token row is one int32 per token"));
    }
    int32_t row[MOY_UI_TOKENS];
    memcpy(row, b.buf, sizeof(row));
    check(moy_ui_look_set(mp_obj_get_int(slot), row));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_look_obj, ui_look);

static mp_obj_t colour(int16_t v) {
    return v < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(v);
}

// colors(look, kind, state) -> (field, ink, edge), None for nothing.
static mp_obj_t ui_colors(mp_obj_t look, mp_obj_t kind, mp_obj_t state) {
    int16_t c[3];
    check(moy_ui_colors(mp_obj_get_int(look), mp_obj_get_int(kind),
                        mp_obj_get_int(state), c));
    mp_obj_t t[3] = {colour(c[0]), colour(c[1]), colour(c[2])};
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_colors_obj, ui_colors);

// button_style(look, kind, state, app) -> (field, ink, edge, edge_w, shape, r,
// font, bevel): moy_ui_button_style, a colour None for nothing, a number -1
// where silent.
static mp_obj_t ui_button_style(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_btn_style_t bs;
    check(moy_ui_button_style(mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                              mp_obj_get_int(a[3]), &bs));
    mp_obj_t t[8] = {colour(bs.c[0]), colour(bs.c[1]), colour(bs.c[2]),
                     MP_OBJ_NEW_SMALL_INT(bs.edge_w), MP_OBJ_NEW_SMALL_INT(bs.shape),
                     MP_OBJ_NEW_SMALL_INT(bs.r), MP_OBJ_NEW_SMALL_INT(bs.font),
                     MP_OBJ_NEW_SMALL_INT(bs.bevel)};
    return mp_obj_new_tuple(8, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_button_style_obj, 4, 4, ui_button_style);

// buttons_set(app or None, kind index, leaves): one row of the live theme's
// button leaves (moy_ui_buttons_set); buttons_clear() drops them all;
// buttons_app(name) -> the app's row, or -1.
static mp_obj_t ui_buttons_set(mp_obj_t app, mp_obj_t kind, mp_obj_t leaves) {
    int32_t v[MOY_THEME_BTN_LEAVES];
    size_t n = 0;
    mp_obj_t *items;
    mp_obj_get_array_fixed_n(leaves, MOY_THEME_BTN_LEAVES, &items);
    for (int i = 0; i < MOY_THEME_BTN_LEAVES; i++) {
        v[i] = (int32_t)mp_obj_get_int_truncated(items[i]);
    }
    const char *s = app == mp_const_none ? NULL : mp_obj_str_get_data(app, &n);
    check(moy_ui_buttons_set(s, n, mp_obj_get_int(kind), v));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_buttons_set_obj, ui_buttons_set);

static mp_obj_t ui_buttons_clear(void) {
    moy_ui_buttons_clear();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_buttons_clear_obj, ui_buttons_clear);

// disabled(v): the live theme's disabled ink (moy_ui_set_disabled), its
// look's placed `disabled` leaf; None or a negative value for none.
static mp_obj_t ui_disabled(mp_obj_t v) {
    moy_ui_set_disabled(v == mp_const_none ? -1 : (int32_t)mp_obj_get_int(v));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_disabled_obj, ui_disabled);

// danger_ink(v): the live theme's menu danger ink (moy_ui_set_danger_ink), its
// look's placed `danger_ink` leaf; None or a negative value for none.
static mp_obj_t ui_danger_ink(mp_obj_t v) {
    moy_ui_set_danger_ink(v == mp_const_none ? -1 : (int32_t)mp_obj_get_int(v));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_danger_ink_obj, ui_danger_ink);

static mp_obj_t ui_buttons_app(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_buttons_app(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_buttons_app_obj, ui_buttons_app);

// theme_reads(a, b): the two colours reach the contrast floor.
static mp_obj_t ui_theme_reads(mp_obj_t a, mp_obj_t b) {
    return mp_obj_new_bool(moy_theme_reads((int32_t)mp_obj_get_int_truncated(a),
                                           (int32_t)mp_obj_get_int_truncated(b)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_theme_reads_obj, ui_theme_reads);

// metrics(kind) -> the tuple for a kind's name, or None.
static mp_obj_t ui_metrics(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    int k = moy_ui_kind_known(s, n);
    int count = 0;
    const int16_t *v = moy_ui_metrics(k, &count);
    if (v == NULL) {
        return mp_const_none;
    }
    mp_obj_t items[MOY_UI_SLOTS];
    for (int i = 0; i < count; i++) {
        items[i] = MP_OBJ_NEW_SMALL_INT(v[i]);
    }
    return mp_obj_new_tuple((size_t)count, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_metrics_obj, ui_metrics);

// text_width(s, scale=1, font=0): the pixels `s` draws at `scale` in `font`
// (moy_text.h: bytes; 0 for an unknown font).
static mp_obj_t ui_text_width(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *s = mp_obj_str_get_data(args[0], &n);
    int scale = n_args > 1 ? (int)mp_obj_get_int(args[1]) : 1;
    uint32_t font = n_args > 2 ? (uint32_t)mp_obj_get_int_truncated(args[2]) : MOY_FONT_PETME128;
    return MP_OBJ_NEW_SMALL_INT(moy_text_width(font, (const uint8_t *)s, n, scale));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_width_obj, 1, 3, ui_text_width);

// text_cut(s, maxw, scale=1, font=0): `s` as a box `maxw` pixels wide shows
// it (moy_text_cut_px): whole where it fits, else cut at a codepoint with
// the font's ellipsis after it where the font has one.
static mp_obj_t ui_text_cut(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *s = mp_obj_str_get_data(args[0], &n);
    int maxw = (int)mp_obj_get_int(args[1]);
    int scale = n_args > 2 ? (int)mp_obj_get_int(args[2]) : 1;
    uint32_t font = n_args > 3 ? (uint32_t)mp_obj_get_int_truncated(args[3]) : MOY_FONT_PETME128;
    int ell = 0;
    size_t k = moy_text_cut_px(font, (const uint8_t *)s, n, maxw < 0 ? 0 : maxw, scale, &ell);
    if (k == n && !ell) {
        return args[0];
    }
    vstr_t v;
    vstr_init(&v, k + 3);
    vstr_add_strn(&v, s, k);
    if (ell) {
        vstr_add_strn(&v, "\xe2\x80\xa6", 3);
    }
    return mp_obj_new_str_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_cut_obj, 2, 4, ui_text_cut);

// text_covers(s, font=0): whether `font` draws every character of `s` with
// a glyph of its own (moy_text_covers).
static mp_obj_t ui_text_covers(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *s = mp_obj_str_get_data(args[0], &n);
    uint32_t font = n_args > 1 ? (uint32_t)mp_obj_get_int_truncated(args[1]) : MOY_FONT_PETME128;
    return mp_obj_new_bool(moy_text_covers(font, (const uint8_t *)s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_covers_obj, 1, 2, ui_text_covers);

// wrap(s, cols) -> [line, ...]: the toolkit's word wrap (moy_text_wrap's
// words mode), each line its words joined by single spaces. wrap(s, px,
// font): lines of at most `px` pixels of `font`'s advances.
#define WRAP_LINES 32
static mp_obj_t ui_wrap(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const uint8_t *s = (const uint8_t *)mp_obj_str_get_data(args[0], &n);
    int cols = (int)mp_obj_get_int(args[1]);
    uint32_t font = n_args > 2 ? (uint32_t)mp_obj_get_int_truncated(args[2]) : MOY_FONT_PETME128;
    int mode = n_args > 2 ? MOY_WRAP_WORDS_PX : MOY_WRAP_WORDS;
    moy_text_line_t stack[WRAP_LINES];
    moy_text_line_t *lines = stack;
    int count = moy_text_wrap(font, s, n, cols, mode, lines, WRAP_LINES);
    if (count > WRAP_LINES) {
        lines = m_new(moy_text_line_t, count);
        moy_text_wrap(font, s, n, cols, mode, lines, count);
    }
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < count; i++) {
        const moy_text_line_t *l = &lines[i];
        if (!l->collapse) {
            mp_obj_list_append(out, mp_obj_new_str((const char *)s + l->off, l->len));
            continue;
        }
        vstr_t v;
        vstr_init_len(&v, l->len);
        v.len = moy_text_collapse(s, l, (uint8_t *)v.buf);
        mp_obj_list_append(out, mp_obj_new_str_from_vstr(&v));
    }
    if (lines != stack) {
        m_del(moy_text_line_t, lines, count);
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_wrap_obj, 2, 3, ui_wrap);

static mp_obj_t ui_glyph_names(void) {
    mp_obj_t items[MOY_UI_GLYPHS];
    for (int i = 0; i < MOY_UI_GLYPHS; i++) {
        items[i] = mp_obj_new_str(moy_ui_glyph_names[i], strlen(moy_ui_glyph_names[i]));
    }
    return mp_obj_new_tuple(MOY_UI_GLYPHS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_glyph_names_obj, ui_glyph_names);

// glyph_id(name) -> its id, or -1.
static mp_obj_t ui_glyph_id(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_glyph_id(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_glyph_id_obj, ui_glyph_id);

// -- the strings C draws (native/moy_spine/moy_str.h) ------------------------------

static int str_id(mp_obj_t name) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    int id = moy_str_id(s, n);
    if (id < 0) {
        mp_raise_type_arg(&mp_type_KeyError, name);
    }
    return id;
}

// s(id) -> the id's text: the pack's entry, else English.
static mp_obj_t ui_s(mp_obj_t name) {
    const char *t = moy_str(str_id(name));
    return mp_obj_new_str(t, strlen(t));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_s_obj, ui_s);

// str_set(id, text) -> True, or False when the pack's entry is refused (its
// conversions differ from English's: the id reads English). None removes it.
static mp_obj_t ui_str_set(mp_obj_t name, mp_obj_t text) {
    int id = str_id(name);
    int rc;
    if (text == mp_const_none) {
        rc = moy_str_set(id, NULL, 0);
    } else {
        size_t n;
        const char *t = mp_obj_str_get_data(text, &n);
        rc = moy_str_set(id, t, n);
    }
    if (rc == MOY_STR_NOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    return mp_obj_new_bool(rc == MOY_STR_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_str_set_obj, ui_str_set);

// str_reset(): every pack entry removed; the table reads English.
static mp_obj_t ui_str_reset(void) {
    moy_str_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_str_reset_obj, ui_str_reset);

// -- the canvas, the box, the text and the colours as the faces pass them --------------
//
// A face passes the canvas object itself, its rect as it has it and its
// text and colours raw: the conversions are here, in C, because on a board
// every Python call a face makes costs more than the widget it draws.

// The sink of canvas `cv` (its `_ui`: the CANVAS handle and the sprite
// queue), once the pending sprite batch has landed (#63's order rule).
static void sink_of(moy_ui_sink_t *s, mp_obj_t cv, mp_obj_t look) {
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(mp_load_attr(cv, MP_QSTR__ui), &n, &it);
    if (n < 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: not a canvas"));
    }
    mp_buffer_info_t b;
    mp_get_buffer_raise(it[1], &b, MP_BUFFER_READ);
    if (b.len >= 2 && ((const int16_t *)b.buf)[0] > 4) {
        mp_obj_t dest[2];
        mp_load_method(cv, MP_QSTR_flush_batch, dest);
        mp_call_method_n_kw(0, 0, dest);
    }
    int rc = moy_ui_sink_row(s, (uint32_t)mp_obj_get_int(it[0]),
                             look == mp_const_none ? 0 : mp_obj_get_int(look));
    if (rc == MOY_UI_STALE) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: stale canvas"));
    }
    check(rc);
}

// A coordinate, converted toward zero as the print gate's gate_num does.
static int32_t num(mp_obj_t o) {
    if (mp_obj_is_small_int(o)) {
        return (int32_t)MP_OBJ_SMALL_INT_VALUE(o);
    }
#if MICROPY_PY_BUILTINS_FLOAT
    if (mp_obj_is_float(o)) {
        return (int32_t)mp_obj_get_float(o);
    }
#endif
    return (int32_t)mp_obj_get_int(o);
}

static void rect_of(mp_obj_t o, int32_t r[4]) {
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(o, &n, &it);
    if (n < 4) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: a rect is (x, y, w, h)"));
    }
    for (int i = 0; i < 4; i++) {
        r[i] = num(it[i]);
    }
}

static moy_ui_box_t box_of(mp_obj_t o) {
    int32_t r[4];
    rect_of(o, r);
    moy_ui_box_t b = {(int16_t)r[0], (int16_t)r[1], (int16_t)r[2], (int16_t)r[3]};
    return b;
}

static int16_t i16(mp_obj_t o) {
    return (int16_t)num(o);
}

static int16_t opt(mp_obj_t o, int16_t none) {
    return o == mp_const_none ? none : (int16_t)num(o);
}

// A colour: the style's (None), else the index the canvas would mask it to.
static int16_t col(mp_obj_t o, int16_t none) {
    return o == mp_const_none ? none : (int16_t)(mp_obj_get_int(o) & moy_ui_cmask());
}

static void colors3(mp_obj_t o, int16_t out[3]) {
    if (o == mp_const_none) {
        out[0] = out[1] = out[2] = MOY_UI_FROM_STYLE;
        return;
    }
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(o, &n, &it);
    for (size_t i = 0; i < 3; i++) {
        out[i] = i < n ? col(it[i], -1) : -1;
    }
}

// Text: a str as it is; anything else as str() makes it (None reads "").
static moy_ui_span_t span_of(mp_obj_t o) {
    moy_ui_span_t sp = {NULL, 0};
    if (o == mp_const_none) {
        return sp;
    }
    if (!mp_obj_is_str(o)) {
        o = mp_obj_str_make_new(&mp_type_str, 1, 0, &o);
    }
    size_t n;
    sp.p = (const uint8_t *)mp_obj_str_get_data(o, &n);
    sp.n = (uint16_t)(n > 0xFFFF ? 0xFFFF : n);
    return sp;
}

// A label a falsy value hides (row's `if label:`).
static moy_ui_span_t span_if(mp_obj_t o) {
    if (!mp_obj_is_true(o)) {
        moy_ui_span_t sp = {(const uint8_t *)"", 0};
        return sp;
    }
    return span_of(o);
}

// The answers the verbs hand back lately (a page's content rect, a table's
// (cur, opened), a card's rect): an answer equal to one of them IS that tuple,
// so a frame that lays out as the last one did allocates no answer. Tuples are
// immutable, so sharing one is invisible to the app. A new answer takes the
// next slot whose answer was not handed back again since the hand passed it
// (a clock), so the answers every frame repeats -- a page's rect, a font's --
// outlive the ones a scroll moves, a card's rect, that come new each frame.
#define RET_RING 32
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_ui_ret[32]);
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_ui_bar_last);
static uint8_t s_ret_at;
static uint8_t s_ret_used[RET_RING];

// The same for the words the verbs hand back (the focused id): an answer
// equal to a recent one IS that str.
#define RET_STRS 8
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_ui_ret_str[8]);
static uint8_t s_ret_str_at;

static mp_obj_t ret_str(const char *p, size_t n) {
    mp_obj_t *ring = MP_STATE_VM(moy_ui_ret_str);
    for (int i = 0; i < RET_STRS; i++) {
        mp_obj_t r = ring[i];
        if (r == MP_OBJ_NULL) {
            continue;
        }
        if (!mp_obj_is_obj(r) || gc_nbytes(MP_OBJ_TO_PTR(r)) == 0
            || ((mp_obj_base_t *)MP_OBJ_TO_PTR(r))->type != &mp_type_str) {
            ring[i] = MP_OBJ_NULL;
            continue;
        }
        size_t len;
        const char *q = mp_obj_str_get_data(r, &len);
        if (len == n && memcmp(p, q, n) == 0) {
            return r;
        }
    }
    mp_obj_t r = mp_obj_new_str(p, n);
    if (mp_obj_is_qstr(r)) {
        return r;                   // interned: no heap object to keep
    }
    ring[s_ret_str_at] = r;
    s_ret_str_at = (uint8_t)((s_ret_str_at + 1) % RET_STRS);
    return r;
}

static mp_obj_t ret_tuple(size_t n, const mp_obj_t *items) {
    mp_obj_t *ring = MP_STATE_VM(moy_ui_ret);
    for (int i = 0; i < RET_RING; i++) {
        mp_obj_t r = ring[i];
        if (r == MP_OBJ_NULL) {
            continue;
        }
        // A soft reset keeps the ring and starts a new heap: an entry is
        // trusted only while it heads a live block that is a tuple.
        if (gc_nbytes(MP_OBJ_TO_PTR(r)) == 0 || !mp_obj_is_type(r, &mp_type_tuple)) {
            ring[i] = MP_OBJ_NULL;
            continue;
        }
        mp_obj_tuple_t *t = MP_OBJ_TO_PTR(r);
        if (t->len != n) {
            continue;
        }
        size_t k = 0;
        while (k < n && t->items[k] == items[k]) {
            k++;
        }
        if (k == n) {
            s_ret_used[i] = 1;
            return r;
        }
    }
    for (int i = 0; i < RET_RING && s_ret_used[s_ret_at]; i++) {
        s_ret_used[s_ret_at] = 0;
        s_ret_at = (uint8_t)((s_ret_at + 1) % RET_RING);
    }
    mp_obj_t r = mp_obj_new_tuple(n, items);
    ring[s_ret_at] = r;
    s_ret_used[s_ret_at] = 0;
    s_ret_at = (uint8_t)((s_ret_at + 1) % RET_RING);
    return r;
}

static mp_obj_t box_tuple(moy_ui_box_t b) {
    mp_obj_t t[4] = {MP_OBJ_NEW_SMALL_INT(b.x), MP_OBJ_NEW_SMALL_INT(b.y),
                     MP_OBJ_NEW_SMALL_INT(b.w), MP_OBJ_NEW_SMALL_INT(b.h)};
    return ret_tuple(4, t);
}

static mp_obj_t rect_tuple(const int32_t r[4]) {
    mp_obj_t t[4] = {mp_obj_new_int(r[0]), mp_obj_new_int(r[1]), mp_obj_new_int(r[2]),
                     mp_obj_new_int(r[3])};
    return mp_obj_new_tuple(4, t);
}

static int fs_or(mp_obj_t o, const moy_ui_sink_t *s) {
    return o == mp_const_none ? s->fs : (int)num(o);
}

// -- glyphs ---------------------------------------------------------------------------

// glyph(cv, g, rect, ci, scale): scale None is the canvas's font scale
static mp_obj_t ui_glyph(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    moy_ui_box_t b = box_of(a[2]);
    check(moy_ui_sink_glyph(&s, mp_obj_get_int(a[1]), b.x, b.y, b.w, b.h,
                            mp_obj_get_int(a[3]), fs_or(a[4], &s)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_glyph_obj, 5, 5, ui_glyph);

// icon_mask(cv, font, code, x, y, ci): the glyph at index byte `code` of icon set
// file `font`
static mp_obj_t ui_icon_mask(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_sink_mask(&s, (uint32_t)mp_obj_get_int_truncated(a[1]), mp_obj_get_int(a[2]),
                           mp_obj_get_int(a[3]), mp_obj_get_int(a[4]), mp_obj_get_int(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_icon_mask_obj, 6, 6, ui_icon_mask);

// icons_use(f16, f24, close, maximize, minimize): the icon set the C parts draw
static mp_obj_t ui_icons_use(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    uint8_t codes[3] = {(uint8_t)mp_obj_get_int(a[2]), (uint8_t)mp_obj_get_int(a[3]),
                        (uint8_t)mp_obj_get_int(a[4])};
    moy_ui_icons_use((uint32_t)mp_obj_get_int_truncated(a[0]),
                     (uint32_t)mp_obj_get_int_truncated(a[1]), codes);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_icons_use_obj, 5, 5, ui_icons_use);

// moy_use(px, clear): Moy's 16x16 tile of palette indices (256 bytes), its
// clear index; px None takes it away (moy_ui_moy_use).
static mp_obj_t ui_moy_use(mp_obj_t px, mp_obj_t clear) {
    if (px == mp_const_none) {
        moy_ui_moy_use(NULL, 0);
        return mp_const_none;
    }
    mp_buffer_info_t b;
    mp_get_buffer_raise(px, &b, MP_BUFFER_READ);
    if (b.len < 256) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_use: 256 pixels"));
    }
    moy_ui_moy_use((const uint8_t *)b.buf, mp_obj_get_int(clear));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_moy_use_obj, ui_moy_use);

// -- rows, cells and lists ---------------------------------------------------------------

// row(cv, look, rect, kind, flags, interact, label, value, glyph, glyph_size,
//     glyph_ink, value_ink, pad, pad_right, text_dy, scale, fs, colors, phase)
static mp_obj_t ui_row(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_row_t r;
    r.label = span_if(a[6]);
    r.value = span_of(a[7]);
    r.glyph = i16(a[8]);
    r.glyph_size = opt(a[9], MOY_UI_DEFAULT);
    r.glyph_ink = col(a[10], MOY_UI_FROM_STYLE);
    r.value_ink = col(a[11], MOY_UI_FROM_STYLE);
    r.pad = opt(a[12], MOY_UI_DEFAULT);
    r.pad_right = opt(a[13], MOY_UI_DEFAULT);
    r.text_dy = opt(a[14], MOY_UI_DEFAULT);
    r.scale = i16(a[15]);
    r.fs = (int16_t)fs_or(a[16], &s);
    colors3(a[17], r.colors);
    check(moy_ui_row(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                     mp_obj_get_int(a[5]), &r, mp_obj_get_int(a[18])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_row_obj, 19, 19, ui_row);

// cell(cv, look, rect, kind, flags, interact, label, glyph, pad, caption_h,
//      band_fill, band_ink, text_dx, text_dy, fs, colors, phase) -> the art box
static mp_obj_t ui_cell(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_cell_t k;
    k.label = span_of(a[6]);
    k.glyph = i16(a[7]);
    k.pad = opt(a[8], MOY_UI_DEFAULT);
    k.caption_h = opt(a[9], MOY_UI_DEFAULT);
    k.band_fill = col(a[10], MOY_UI_FROM_STYLE);
    k.band_ink = col(a[11], MOY_UI_FROM_STYLE);
    k.text_dx = opt(a[12], MOY_UI_DEFAULT);
    k.text_dy = opt(a[13], MOY_UI_DEFAULT);
    k.fs = (int16_t)fs_or(a[14], &s);
    colors3(a[15], k.colors);
    moy_ui_box_t art;
    check(moy_ui_cell(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                      mp_obj_get_int(a[5]), &k, mp_obj_get_int(a[16]), &art));
    return box_tuple(art);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_cell_obj, 17, 17, ui_cell);

// label(cv, rect, text, ink, align, bg=None, shadow=None) -> the drawn width:
// bg the colour index filling the whole rect, shadow (ci, alpha, dx, dy)
// (moy_ui_label_paint).
static mp_obj_t ui_label(size_t n_args, const mp_obj_t *a) {
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    moy_ui_label_paint_t lp = {-1, -1, 0, 0, 0};
    if (n_args > 5 && a[5] != mp_const_none) {
        lp.bg = (int16_t)mp_obj_get_int(a[5]);
    }
    if (n_args > 6 && a[6] != mp_const_none) {
        size_t n;
        mp_obj_t *it;
        mp_obj_get_array(a[6], &n, &it);
        if (n != 4) {
            mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: shadow is (ci, alpha, dx, dy)"));
        }
        lp.shadow = (int16_t)mp_obj_get_int(it[0]);
        lp.shadow_a = (int16_t)mp_obj_get_int(it[1]);
        lp.dx = (int16_t)mp_obj_get_int(it[2]);
        lp.dy = (int16_t)mp_obj_get_int(it[3]);
    }
    return MP_OBJ_NEW_SMALL_INT(moy_ui_label_paint(&s, box_of(a[1]), span_if(a[2]),
                                                   mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                                                   &lp));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_label_obj, 5, 7, ui_label);

// bar_rect(view, content, offset, horizontal, fs, bar_w) -> the thumb, or None
static mp_obj_t ui_bar_rect(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_box_t r;
    if (!moy_ui_bar_rect(box_of(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                         mp_obj_is_true(a[3]), mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                         &r)) {
        return mp_const_none;
    }
    return box_tuple(r);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_bar_rect_obj, 6, 6, ui_bar_rect);

// scrollbar(cv, look, view, content, offset, horizontal, bar_w)
static mp_obj_t ui_scrollbar(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_scrollbar(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                           mp_obj_is_true(a[5]), s.fs, mp_obj_get_int(a[6])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scrollbar_obj, 7, 7, ui_scrollbar);

// scroll_cues(cv, up_xy, dn_xy, can_up, can_dn, ci)
static mp_obj_t ui_scroll_cues(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int up = mp_obj_is_true(a[3]), dn = mp_obj_is_true(a[4]);
    if (!up && !dn) {
        return mp_const_none;
    }
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    size_t n;
    mp_obj_t *u, *d;
    mp_obj_get_array(a[1], &n, &u);
    mp_obj_get_array(a[2], &n, &d);
    check(moy_ui_scroll_cues(&s, num(u[0]), num(u[1]), num(d[0]), num(d[1]), up, dn,
                             mp_obj_get_int(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_cues_obj, 6, 6, ui_scroll_cues);

// fill_uncovered(cv, inner, outer, ci)
static mp_obj_t ui_fill_uncovered(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_fill_uncovered(&s, box_of(a[1]), box_of(a[2]), mp_obj_get_int(a[3])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_fill_uncovered_obj, 4, 4, ui_fill_uncovered);

// list(cv, look, rect, kind, items, count, row_h, offset, sel, bar_w)
static mp_obj_t ui_list(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[4], &b, MP_BUFFER_READ);
    check(moy_ui_list(&s, box_of(a[2]), mp_obj_get_int(a[3]), (const uint8_t *)b.buf, b.len,
                      mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
                      mp_obj_get_int(a[8]), s.fs, mp_obj_get_int(a[9])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_list_obj, 10, 10, ui_list);

// -- buttons and frames --------------------------------------------------------------------

// button(cv, look, rect, kind, flags, interact, label, glyph, phase, app)
static mp_obj_t ui_button(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_button_t k;
    k.label = span_of(a[6]);
    k.glyph = i16(a[7]);
    k.fs = s.fs;
    k.colors[0] = k.colors[1] = k.colors[2] = MOY_UI_FROM_STYLE;
    k.app = i16(a[9]);
    k.icon = -1;
    check(moy_ui_button(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                        mp_obj_get_int(a[5]), &k, mp_obj_get_int(a[8])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_button_obj, 10, 10, ui_button);

// button_x(rect, flags, label, fs): where its icon or glyph lands
static mp_obj_t ui_button_x(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return MP_OBJ_NEW_SMALL_INT(moy_ui_button_x(box_of(a[0]), mp_obj_get_int(a[1]),
                                                span_of(a[2]), mp_obj_get_int(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_button_x_obj, 4, 4, ui_button_x);

// chip(cv, look, rect, kind, flags, interact, label, glyph, fs, colors, phase)
static mp_obj_t ui_chip(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_button_t k;
    k.label = span_of(a[6]);
    k.glyph = i16(a[7]);
    k.fs = (int16_t)fs_or(a[8], &s);
    colors3(a[9], k.colors);
    k.app = -1;
    k.icon = -1;
    check(moy_ui_chip(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                      mp_obj_get_int(a[5]), &k, mp_obj_get_int(a[10])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_chip_obj, 11, 11, ui_chip);

#define TABS 32
// tab_rects(rect, labels, count, fs, gap) -> (labels_on, [box, ...])
static mp_obj_t ui_tab_rects(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[1], &b, MP_BUFFER_READ);
    moy_ui_box_t out[TABS];
    int on = 0;
    int count = mp_obj_get_int(a[2]);
    int n = moy_ui_tab_rects(box_of(a[0]), (const uint8_t *)b.buf, b.len,
                             count > TABS ? TABS : count, mp_obj_get_int(a[3]),
                             opt(a[4], MOY_UI_DEFAULT), out, &on);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        mp_obj_list_append(list, box_tuple(out[i]));
    }
    mp_obj_t t[2] = {mp_obj_new_bool(on), list};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_tab_rects_obj, 5, 5, ui_tab_rects);

// tab(cv, look, rect, active, labels_on, label, ink, phase)
static mp_obj_t ui_tab(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_tab(&s, box_of(a[2]), mp_obj_is_true(a[3]), mp_obj_is_true(a[4]),
                     span_of(a[5]), col(a[6], MOY_UI_FROM_STYLE), s.fs,
                     mp_obj_get_int(a[7])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_tab_obj, 8, 8, ui_tab);

// status(cv, look, rect, items, count)
static mp_obj_t ui_status(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[3], &b, MP_BUFFER_READ);
    check(moy_ui_status(&s, box_of(a[2]), (const uint8_t *)b.buf, b.len, mp_obj_get_int(a[4]),
                        s.fs));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_status_obj, 5, 5, ui_status);

// hints(cv, look, rect, items, desk, draw) -> ((x0, x1), ...): keycap hints in
// band `rect` (moy_ui_hints_at), each pair's span across it; drawn when `draw`.
static mp_obj_t ui_hints(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[3], &b, MP_BUFFER_READ);
    int16_t spans[32];
    int n = moy_ui_hints_at(&s, box_of(a[2]), (const uint8_t *)b.buf, b.len, s.fs,
                            mp_obj_is_true(a[4]), mp_obj_is_true(a[5]), spans, 16);
    if (n > 16) {
        n = 16;
    }
    mp_obj_t t[16];
    for (int k = 0; k < n; k++) {
        mp_obj_t p[2] = {MP_OBJ_NEW_SMALL_INT(spans[2 * k]), MP_OBJ_NEW_SMALL_INT(spans[2 * k + 1])};
        t[k] = mp_obj_new_tuple(2, p);
    }
    return mp_obj_new_tuple((size_t)n, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_hints_obj, 6, 6, ui_hints);

// input([kind]) -> kind: the input in use (MOY_UI_INPUT_*), which the hints
// draw the keys of; set when `kind` is given.
static mp_obj_t ui_input(size_t n_args, const mp_obj_t *a) {
    if (n_args > 0) {
        moy_ui_set_input(mp_obj_get_int(a[0]));
    }
    return MP_OBJ_NEW_SMALL_INT(moy_ui_input());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_input_obj, 0, 1, ui_input);

// hint_input([kind]) -> kind: the input whose keys the hints draw
// (moy_ui_hint_input); set when `kind` is given.
static mp_obj_t ui_hint_input(size_t n_args, const mp_obj_t *a) {
    if (n_args > 0) {
        moy_ui_set_hint_input(mp_obj_get_int(a[0]));
    }
    return MP_OBJ_NEW_SMALL_INT(moy_ui_hint_input());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_hint_input_obj, 0, 1, ui_hint_input);

// game_btn(cv, rect, label, fill)
static mp_obj_t ui_game_btn(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_game_btn(&s, box_of(a[1]), span_of(a[2]), mp_obj_get_int(a[3]), s.fs));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_game_btn_obj, 4, 4, ui_game_btn);

// game_icon_btn(cv, rect, glyph, label, fill, phase)
static mp_obj_t ui_game_icon_btn(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_game_icon_btn(&s, box_of(a[1]), mp_obj_get_int(a[2]), span_if(a[3]),
                               mp_obj_get_int(a[4]), s.fs, mp_obj_get_int(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_game_icon_btn_obj, 6, 6, ui_game_icon_btn);

// mini_btn(cv, rect, label, fill, label_rect)
static mp_obj_t ui_mini_btn(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    moy_ui_box_t b = box_of(a[1]);
    moy_ui_box_t l = a[4] == mp_const_none ? b : box_of(a[4]);
    check(moy_ui_mini_btn(&s, b, span_of(a[2]), mp_obj_get_int(a[3]), l.x, l.y));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_mini_btn_obj, 5, 5, ui_mini_btn);

// toolbar(cv, look, rect) -> the label ink
static mp_obj_t ui_toolbar(mp_obj_t cv, mp_obj_t look, mp_obj_t rect) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    int ink;
    check(moy_ui_toolbar(&s, box_of(rect), &ink));
    return mp_obj_new_int(ink);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_toolbar_obj, ui_toolbar);

// paint(cv, look, rect, part, state) -> painted: part and state are theme ids
static mp_obj_t ui_paint(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    int painted = 0;
    check(moy_ui_paint(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]), &painted));
    return mp_obj_new_bool(painted);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_paint_obj, 5, 5, ui_paint);

// frame(cv, look, rect, title_h, title, active, btns, fs): a window's frame,
// `btns` its MOY_UI_FRAME_* word: the buttons (True: a minimise), the matte
static mp_obj_t ui_frame(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_frame(&s, box_of(a[2]), mp_obj_get_int(a[3]), span_of(a[4]),
                       mp_obj_is_true(a[5]), mp_obj_get_int(a[6]), mp_obj_get_int(a[7])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_frame_obj, 8, 8, ui_frame);

// frame_buttons(rect, title_h, title, btns, fs) -> ((id, x, y, w, h, right), ...)
// left to right
static mp_obj_t ui_frame_buttons(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_frame_button_t b[MOY_UI_FRAME_BUTTONS];
    int n = moy_ui_frame_buttons(box_of(a[0]), mp_obj_get_int(a[1]), span_of(a[2]),
                                 mp_obj_get_int(a[3]), mp_obj_get_int(a[4]), b);
    mp_obj_t out[MOY_UI_FRAME_BUTTONS];
    for (int i = 0; i < n; i++) {
        mp_obj_t t[6] = {MP_OBJ_NEW_SMALL_INT(b[i].id), MP_OBJ_NEW_SMALL_INT(b[i].box.x),
                         MP_OBJ_NEW_SMALL_INT(b[i].box.y), MP_OBJ_NEW_SMALL_INT(b[i].box.w),
                         MP_OBJ_NEW_SMALL_INT(b[i].box.h), MP_OBJ_NEW_SMALL_INT(b[i].right)};
        out[i] = mp_obj_new_tuple(6, t);
    }
    return mp_obj_new_tuple(n, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_frame_buttons_obj, 5, 5, ui_frame_buttons);

// frame_icon(rect, title_h, btns) -> the line icon's slot (x, y, w, h), or None
static mp_obj_t ui_frame_icon(mp_obj_t rect, mp_obj_t th, mp_obj_t btns) {
    moy_ui_box_t b;
    if (!moy_ui_frame_icon(box_of(rect), mp_obj_get_int(th), mp_obj_get_int(btns), &b)) {
        return mp_const_none;
    }
    mp_obj_t t[4] = {MP_OBJ_NEW_SMALL_INT(b.x), MP_OBJ_NEW_SMALL_INT(b.y),
                     MP_OBJ_NEW_SMALL_INT(b.w), MP_OBJ_NEW_SMALL_INT(b.h)};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_frame_icon_obj, ui_frame_icon);

// bar_part(cv, look, part, rect, on[, fill]) -> whether the depth drew the
// bar part; `fill`, a look colour, fills a desk icon's tile (BARP_TILE)
static mp_obj_t ui_bar_part(size_t n_args, const mp_obj_t *a) {
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    int part = mp_obj_get_int(a[2]);
    if (part == MOY_UI_BARP_TILE && n_args > 5 && a[5] != mp_const_none) {
        return mp_obj_new_bool(moy_ui_bar_tile(&s, box_of(a[3]), mp_obj_get_int(a[5])));
    }
    return mp_obj_new_bool(moy_ui_bar_part(&s, part, box_of(a[3]), mp_obj_get_int(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_bar_part_obj, 5, 6, ui_bar_part);

// scrim(cv, look, rect): what a modal dims under it
// scrim(cv, look, rect[, src, also]): over the pixels of buffer `src`, each
// result stored to buffer `also` as well, both laid out as the canvas's
// (None: the canvas's own, no second store)
static mp_obj_t ui_scrim(size_t n_args, const mp_obj_t *a) {
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    const void *src = NULL;
    void *also = NULL;
    size_t len = (size_t)-1;
    mp_buffer_info_t b;
    if (n_args > 3 && a[3] != mp_const_none) {
        mp_get_buffer_raise(a[3], &b, MP_BUFFER_READ);
        src = b.buf;
        len = b.len;
    }
    if (n_args > 4 && a[4] != mp_const_none) {
        mp_get_buffer_raise(a[4], &b, MP_BUFFER_WRITE);
        also = b.buf;
        len = b.len < len ? b.len : len;
    }
    check(moy_ui_scrim_io(&s, box_of(a[2]), src, also, len));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scrim_obj, 3, 5, ui_scrim);

// tile(cv, look, rect, icon, tone, r, fs, desk): a badge's tile on a card's surface
static mp_obj_t ui_tile(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_span_t ic = span_of(a[3]);
    check(moy_ui_tile(&s, box_of(a[2]), moy_ui_icon_slot(ic.p, ic.n),
                      moy_ui_icon_glyph(ic.p, ic.n), num(a[4]), num(a[5]), num(a[6]),
                      num(a[7])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_tile_obj, 8, 8, ui_tile);

// unlock(cv, look, cx, y, head, name, icon, burst, fs, desk) -> (x, y, w, h): the
// unlock banner
static mp_obj_t ui_unlock(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_span_t ic = span_of(a[6]);
    moy_ui_box_t b;
    check(moy_ui_unlock(&s, num(a[2]), num(a[3]), span_of(a[4]), span_of(a[5]),
                        moy_ui_icon_slot(ic.p, ic.n), moy_ui_icon_glyph(ic.p, ic.n), num(a[7]),
                        num(a[8]), num(a[9]), &b));
    mp_obj_t t[4] = {MP_OBJ_NEW_SMALL_INT(b.x), MP_OBJ_NEW_SMALL_INT(b.y),
                     MP_OBJ_NEW_SMALL_INT(b.w), MP_OBJ_NEW_SMALL_INT(b.h)};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_unlock_obj, 10, 10, ui_unlock);

// month_h(weeks, fs, desk) -> a month's height (moy_ui_month_h)
static mp_obj_t ui_month_h(mp_obj_t weeks, mp_obj_t fs, mp_obj_t desk) {
    return mp_obj_new_int(moy_ui_month_h(num(weeks), num(fs), num(desk)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_month_h_obj, ui_month_h);

// modal_band(fs, desk) -> the rows a modal card's body keeps clear of its
// top and foot (moy_ui_modal_band)
static mp_obj_t ui_modal_band(mp_obj_t fs, mp_obj_t desk) {
    return MP_OBJ_NEW_SMALL_INT(moy_ui_modal_band(num(fs), num(desk)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_modal_band_obj, ui_modal_band);

// modal(cv, look, rect, fs, desk): a system card, flat (moy_ui_modal)
static mp_obj_t ui_modal(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_modal(&s, box_of(a[2]), (moy_ui_span_t){NULL, 0}, num(a[3]), num(a[4]), 0));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_modal_obj, 5, 5, ui_modal);

// perf(cv, look, args, words, out): the perf HUD; `args` sixteen int16 --
// right, bottom, fps, frame (tenths of a ms), detail, fs, desk, draw, then
// the parts (draw, flush, logic, idle, tenths of a ms) -- and its box into
// `out` (four int32), so a frame that paints it allocates nothing (a call of
// few arguments keeps its caller's frame on the C stack)
static mp_obj_t ui_perf(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    mp_buffer_info_t ab, ob;
    mp_get_buffer_raise(a[2], &ab, MP_BUFFER_READ);
    mp_get_buffer_raise(a[4], &ob, MP_BUFFER_WRITE);
    if (ab.len < 24 || ob.len < 16) {
        mp_raise_ValueError(NULL);
    }
    const int16_t *v = (const int16_t *)ab.buf;
    moy_ui_box_t b;
    check(moy_ui_perf(&s, v[0], v[1], v[2], v + 8, v[3], v[4], span_of(a[3]), v[5], v[6], v[7],
                      &b));
    int32_t *o = (int32_t *)ob.buf;
    o[0] = b.x;
    o[1] = b.y;
    o[2] = b.w;
    o[3] = b.h;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_perf_obj, 5, 5, ui_perf);

// frame_band(cv, look, rect, light, side): the bar's band
static mp_obj_t ui_frame_band(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_frame_band(&s, box_of(a[2]), mp_obj_is_true(a[3]), mp_obj_get_int(a[4])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_frame_band_obj, 5, 5, ui_frame_band);

// -- depth (moy_ui.h, docs/look_2026-10.md step 6) -------------------------------

static mp_obj_t depth_tuple(void) {
    const moy_ui_depth_t *d = moy_ui_depth();
    mp_obj_t t[15] = {
        MP_OBJ_NEW_SMALL_INT(d->win_r), MP_OBJ_NEW_SMALL_INT(d->card_r),
        MP_OBJ_NEW_SMALL_INT(d->key_r), MP_OBJ_NEW_SMALL_INT(d->corners),
        MP_OBJ_NEW_SMALL_INT(d->stepped), MP_OBJ_NEW_SMALL_INT(d->shadow),
        MP_OBJ_NEW_SMALL_INT(d->edges), MP_OBJ_NEW_SMALL_INT(d->alpha),
        MP_OBJ_NEW_SMALL_INT(d->blur), MP_OBJ_NEW_SMALL_INT(d->dx),
        MP_OBJ_NEW_SMALL_INT(d->dy), MP_OBJ_NEW_SMALL_INT(d->back),
        MP_OBJ_NEW_SMALL_INT(d->frames), MP_OBJ_NEW_SMALL_INT(d->bar_h),
        MP_OBJ_NEW_SMALL_INT(d->hh_h)};
    return mp_obj_new_tuple(15, t);
}

// use_depth(name, light): install theme `name`'s depth in a mode (None: the
// default's); the depth as (win_r, card_r, key_r, corners, stepped, shadow,
// edges, alpha, blur, dx, dy, back, frames, bar_h, hhbar_h).
static mp_obj_t ui_use_depth(mp_obj_t name, mp_obj_t light) {
    if (name == mp_const_none) {
        moy_ui_depth_use(NULL, 0, 0);
    } else {
        size_t n;
        const char *s = mp_obj_str_get_data(name, &n);
        moy_ui_depth_use(s, n, mp_obj_is_true(light));
    }
    return depth_tuple();
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_use_depth_obj, ui_use_depth);

// frame_geom(w, h, title_h, active, mode) -> (r, foot, pad_l, pad_t, pad_r, pad_b)
static mp_obj_t ui_frame_geom(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int g[MOY_UI_GEOM];
    moy_ui_frame_geom(mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                      mp_obj_is_true(a[3]), mp_obj_get_int(a[4]), g);
    mp_obj_t t[MOY_UI_GEOM];
    for (int i = 0; i < MOY_UI_GEOM; i++) {
        t[i] = MP_OBJ_NEW_SMALL_INT(g[i]);
    }
    return mp_obj_new_tuple(MOY_UI_GEOM, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_frame_geom_obj, 5, 5, ui_frame_geom);

// frame_under(cv, look, rect, title_h, active, mode)
static mp_obj_t ui_frame_under(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_frame_under(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_is_true(a[4]),
                             mp_obj_get_int(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_frame_under_obj, 6, 6, ui_frame_under);

// frame_under_onto(cv, look, rect, title_h, active, mode, onto, onto_title_h, onto_active)
static mp_obj_t ui_frame_under_onto(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    check(moy_ui_frame_under_onto(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_is_true(a[4]),
                                  mp_obj_get_int(a[5]), box_of(a[6]), mp_obj_get_int(a[7]),
                                  mp_obj_is_true(a[8])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_frame_under_onto_obj, 9, 9, ui_frame_under_onto);

// cast(cv, look, rect, part, mode) -> whether the theme casts a shadow
static mp_obj_t ui_cast(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    return mp_obj_new_bool(moy_ui_cast(&s, box_of(a[2]), mp_obj_get_int(a[3]),
                                       mp_obj_get_int(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_cast_obj, 5, 5, ui_cast);

// dialog(cv, rect, ring, fill)
static mp_obj_t ui_dialog(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    check(moy_ui_dialog(&s, box_of(a[1]), col(a[2], MOY_UI_FROM_STYLE),
                        col(a[3], MOY_UI_FROM_STYLE)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_dialog_obj, 4, 4, ui_dialog);

// text_field(cv, rect, text, hint, colors, fs): fs None is 1 (the quirk)
static mp_obj_t ui_text_field(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], mp_const_none);
    int16_t c[5];
    const int16_t *cp = NULL;
    if (a[4] != mp_const_none) {
        size_t n;
        mp_obj_t *it;
        mp_obj_get_array(a[4], &n, &it);
        for (size_t i = 0; i < 5; i++) {
            c[i] = i < n ? col(it[i], -1) : -1;
        }
        cp = c;
    }
    check(moy_ui_text_field(&s, box_of(a[1]), span_if(a[2]), span_if(a[3]), cp,
                            a[5] == mp_const_none ? 1 : (int)num(a[5])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_text_field_obj, 6, 6, ui_text_field);

// panel(cv, look, rect, title, fs) -> the content box
static mp_obj_t ui_panel(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_box_t ct;
    check(moy_ui_panel(&s, box_of(a[2]), span_if(a[3]), fs_or(a[4], &s), &ct));
    return box_tuple(ct);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_panel_obj, 5, 5, ui_panel);

// focus(cv, look, rect, cls, r, flags, fs) -> (ink or None, content rect)
static mp_obj_t ui_focus(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    moy_ui_box_t ct;
    int ink;
    check(moy_ui_focus(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                       mp_obj_get_int(a[5]), fs_or(a[6], &s), &ct, &ink));
    mp_obj_t out[2] = {ink < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(ink), box_tuple(ct)};
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_focus_obj, 7, 7, ui_focus);

// current(cv, look, rect, r, flags, fs) -> ink or None
static mp_obj_t ui_current(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[0], a[1]);
    int ink;
    check(moy_ui_current(&s, box_of(a[2]), mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                         fs_or(a[5], &s), &ink));
    return ink < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(ink);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_current_obj, 6, 6, ui_current);

// marks(active=None, reset=False) -> (active, focus marks drawn, a card's
// focus reach)
static mp_obj_t ui_marks(mp_obj_t active, mp_obj_t reset) {
    if (active != mp_const_none) {
        moy_ui_marks_active(mp_obj_is_true(active));
    }
    mp_obj_t out[3] = {mp_obj_new_bool(moy_ui_marks_shown()),
                       mp_obj_new_int_from_uint(moy_ui_marks_drawn(mp_obj_is_true(reset))),
                       MP_OBJ_NEW_SMALL_INT(moy_ui_mark_reach(MOY_UI_MARK_CARD))};
    return mp_obj_new_tuple(3, out);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_marks_obj, ui_marks);

// mark_reach(cls) -> how far class `cls`'s focus mark reaches past its box
// (moy_ui_mark_reach): the ring's gap, width and outline as the look sets them.
static mp_obj_t ui_mark_reach(mp_obj_t cls) {
    return MP_OBJ_NEW_SMALL_INT(moy_ui_mark_reach(mp_obj_get_int(cls)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_mark_reach_obj, ui_mark_reach);

// -- rect algebra ---------------------------------------------------------------------------

// inset(rect, dx, dy)
static mp_obj_t ui_inset(mp_obj_t rect, mp_obj_t dx, mp_obj_t dy) {
    int32_t r[4], o[4];
    rect_of(rect, r);
    moy_ui_inset32(r, num(dx), num(dy), o);
    return rect_tuple(o);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_inset_obj, ui_inset);

// cut(rect, side, n) -> (band, rest)
static mp_obj_t ui_cut(mp_obj_t rect, mp_obj_t side, mp_obj_t n) {
    int32_t r[4], band[4], rest[4];
    rect_of(rect, r);
    moy_ui_cut(r, mp_obj_get_int(side), num(n), band, rest);
    mp_obj_t t[2] = {rect_tuple(band), rect_tuple(rest)};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_cut_obj, ui_cut);

#define SPLITS 64
// split(rect, n, gap, horizontal) -> [rect, ...]
static mp_obj_t ui_split(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t r[4];
    rect_of(a[0], r);
    int n = mp_obj_get_int(a[1]);
    int32_t out[SPLITS][4];
    int got = moy_ui_split(r, n > SPLITS ? SPLITS : n, num(a[2]), mp_obj_is_true(a[3]),
                           out, SPLITS);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (int i = 0; i < got && i < SPLITS; i++) {
        mp_obj_list_append(list, rect_tuple(out[i]));
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_split_obj, 4, 4, ui_split);

// row_content(rect, fs, pad, pad_right, icon, glyph, glyph_size)
static mp_obj_t ui_row_content(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t r[4], o[4];
    rect_of(a[0], r);
    moy_ui_row_content(r, mp_obj_get_int(a[1]), opt(a[2], MOY_UI_DEFAULT),
                       opt(a[3], MOY_UI_DEFAULT), mp_obj_is_true(a[4]), mp_obj_is_true(a[5]),
                       opt(a[6], MOY_UI_DEFAULT), o);
    return rect_tuple(o);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_row_content_obj, 7, 7, ui_row_content);

// cell_art(rect, fs, pad, caption_h)
static mp_obj_t ui_cell_art(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t r[4], o[4];
    rect_of(a[0], r);
    moy_ui_cell_art(r, mp_obj_get_int(a[1]), opt(a[2], MOY_UI_DEFAULT), num(a[3]), o);
    return rect_tuple(o);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_cell_art_obj, 4, 4, ui_cell_art);


// -- interaction over caller-owned buffers ------------------------------------------------

static void *rw(mp_obj_t o, size_t need, size_t *len) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_RW);
    if (b.len < need) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: state buffer too small"));
    }
    if (len != NULL) {
        *len = b.len;
    }
    return b.buf;
}

static float dt_of(mp_obj_t o) {
    return o == mp_const_none ? 0.0f : (float)mp_obj_get_float(o);
}

static moy_ui_scroll_t *scroll_of(mp_obj_t o) {
    return rw(o, sizeof(moy_ui_scroll_t), NULL);
}

static mp_obj_t ui_scroll_stop(mp_obj_t st) {
    moy_ui_scroll_stop(scroll_of(st));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_scroll_stop_obj, ui_scroll_stop);

static mp_obj_t ui_scroll_drag_start(mp_obj_t st, mp_obj_t p) {
    moy_ui_scroll_drag_start(scroll_of(st), num(p));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_scroll_drag_start_obj, ui_scroll_drag_start);

// scroll_drag_move(st, p, dt, offset, content, extent) -> the new offset, or
// -1 when no drag is live
static mp_obj_t ui_scroll_drag_move(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t off = num(a[3]);
    if (!moy_ui_scroll_drag_move(scroll_of(a[0]), num(a[1]), dt_of(a[2]), &off,
                                 num(a[4]), num(a[5]))) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    return mp_obj_new_int(off);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_drag_move_obj, 6, 6, ui_scroll_drag_move);

static mp_obj_t ui_scroll_drag_end(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_scroll_drag_end(scroll_of(a[0]), num(a[1]), num(a[2]), num(a[3]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_drag_end_obj, 4, 4, ui_scroll_drag_end);

// scroll_tick(st, dt, offset, content, extent) -> the new offset, or -1 when
// nothing coasts
static mp_obj_t ui_scroll_tick(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t off = num(a[2]);
    if (!moy_ui_scroll_tick(scroll_of(a[0]), dt_of(a[1]), &off, num(a[3]), num(a[4]))) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    return mp_obj_new_int(off);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_tick_obj, 5, 5, ui_scroll_tick);

// scroll_glide_to(st, offset, target, slide, content, extent)
static mp_obj_t ui_scroll_glide_to(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_scroll_glide_to(scroll_of(a[0]), num(a[1]), num(a[2]), mp_obj_is_true(a[3]),
                           num(a[4]), num(a[5]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_glide_to_obj, 6, 6, ui_scroll_glide_to);

// scroll_fling(st, offset, vel): add vel px/ms to the fling
static mp_obj_t ui_scroll_fling(mp_obj_t st, mp_obj_t offset, mp_obj_t vel) {
    moy_ui_scroll_fling(scroll_of(st), num(offset), dt_of(vel));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_scroll_fling_obj, ui_scroll_fling);

// scroll_step(st, offset, n, size, content, extent) -> the glide's target
static mp_obj_t ui_scroll_step(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return mp_obj_new_int(moy_ui_scroll_step(scroll_of(a[0]), num(a[1]), num(a[2]), num(a[3]),
                                             num(a[4]), num(a[5])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_scroll_step_obj, 6, 6, ui_scroll_step);

// scroll_land(st, offset) -> the offset the live motion lands at, or -1
// when nothing moves
static mp_obj_t ui_scroll_land(mp_obj_t st, mp_obj_t offset) {
    int32_t off = num(offset);
    if (!moy_ui_scroll_land(scroll_of(st), &off)) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    return mp_obj_new_int(off);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_scroll_land_obj, ui_scroll_land);

// dragtap(tap, st, view, horizontal, px, py, click, down, slop, dt, offset,
//         content, out) -> True on a clean tap; out (array('i')) gets the
// offset and the tap's x and y
static mp_obj_t ui_dragtap(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_dragtap_t *t = rw(a[0], sizeof(moy_ui_dragtap_t), NULL);
    int32_t view[4];
    rect_of(a[2], view);
    int32_t *out = rw(a[12], 3 * sizeof(int32_t), NULL);
    out[0] = num(a[10]);
    int tapped = moy_ui_dragtap_frame(t, scroll_of(a[1]), view, mp_obj_is_true(a[3]),
                                      num(a[4]), num(a[5]), mp_obj_is_true(a[6]),
                                      mp_obj_is_true(a[7]), num(a[8]), dt_of(a[9]),
                                      &out[0], num(a[11]), &out[1], &out[2]);
    return mp_obj_new_bool(tapped);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_dragtap_obj, 13, 13, ui_dragtap);

// -- the public UI API (moy_ui_api.h; runtime/ui.py is its Python face) ---------------
//
// Each verb takes the app's context (a bytearray of api_size() bytes), the
// canvas and look it draws in, its arguments, and `o`: the override block
// (moy_ui_api.h's words and id) as bytes, or None.

static moy_ui_ctx_t *ctx_of(mp_obj_t o) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_WRITE);
    moy_ui_ctx_t *c = moy_ui_api_ctx(b.buf, b.len);
    if (c == NULL) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: not a ui context"));
    }
    return c;
}

// A key of an override dict, or MP_OBJ_NULL where it is absent or None.
static mp_obj_t over_key(mp_map_t *m, qstr k) {
    mp_map_elem_t *e = mp_map_lookup(m, MP_OBJ_NEW_QSTR(k), MP_MAP_LOOKUP);
    return e == NULL || e->value == mp_const_none ? MP_OBJ_NULL : e->value;
}

// A colour key: a role by its token name, an RGB colour, or absent (an
// unknown name too).
static int32_t over_colour(mp_map_t *m, qstr k) {
    mp_obj_t v = over_key(m, k);
    if (v == MP_OBJ_NULL) {
        return MOY_UI_O_NONE;
    }
    if (mp_obj_is_str(v)) {
        const char *name = mp_obj_str_get_str(v);
        for (int i = 0; i < MOY_UI_TOKENS; i++) {
            if (strcmp(name, moy_ui_token_names[i]) == 0) {
                return MOY_UI_O_ROLE + i;
            }
        }
        return MOY_UI_O_NONE;
    }
    return (int32_t)(mp_obj_get_int_truncated(v) & 0xffffff);
}

static int8_t over_flag(mp_map_t *m, qstr k) {
    mp_obj_t v = over_key(m, k);
    return v == MP_OBJ_NULL ? -1 : (int8_t)mp_obj_is_true(v);
}

// The override table (§3) straight from the app's dict, as runtime/ui.py's
// block reads: no block is built, so a verb given a table allocates nothing.
static void over_dict(mp_obj_t d, moy_ui_over_t *out) {
    mp_map_t *m = mp_obj_dict_get_map(d);
    moy_ui_over_none(out);
    out->fill = over_colour(m, MP_QSTR_fill);
    out->ink = over_colour(m, MP_QSTR_ink);
    out->border = over_colour(m, MP_QSTR_border);
    mp_obj_t v = over_key(m, MP_QSTR_id);
    if (v != MP_OBJ_NULL) {
        out->id = span_of(v);
    }
    out->current = over_flag(m, MP_QSTR_current);
    out->disabled = over_flag(m, MP_QSTR_disabled);
    out->nav = over_flag(m, MP_QSTR_nav);
    out->mark = over_flag(m, MP_QSTR_mark);
    out->steps = over_flag(m, MP_QSTR_steps);
    v = over_key(m, MP_QSTR_hint);
    if (v != MP_OBJ_NULL) {
        out->hint = span_of(v);
    }
    mp_obj_t wash = over_key(m, MP_QSTR_wash);
    mp_obj_t lead = over_key(m, MP_QSTR_lead);
    if (wash != MP_OBJ_NULL || lead != MP_OBJ_NULL) {
        out->wash = (int8_t)(wash != MP_OBJ_NULL && mp_obj_is_true(wash));
        out->lead = (int8_t)(lead != MP_OBJ_NULL && mp_obj_is_true(lead));
    }
    v = over_key(m, MP_QSTR_gen);
    if (v != MP_OBJ_NULL && v != mp_const_none) {
        out->gen = (int32_t)mp_obj_get_int_truncated(v);
        out->has_gen = 1;
    }
    v = over_key(m, MP_QSTR_part);
    if (v != MP_OBJ_NULL) {
        size_t n = 0;
        mp_obj_t *wh = NULL;
        mp_obj_get_array(v, &n, &wh);
        if (n == 2) {
            out->has_part = 1;
            out->part_w = (int16_t)mp_obj_get_int_truncated(wh[0]);
            out->part_h = (int16_t)mp_obj_get_int_truncated(wh[1]);
        }
    }
    out->bar = over_flag(m, MP_QSTR_bar);
}

static const moy_ui_over_t *over_of(mp_obj_t o, moy_ui_over_t *out) {
    if (o == mp_const_none) {
        return NULL;
    }
    if (mp_obj_is_type(o, &mp_type_dict)) {
        if (mp_obj_dict_len(o) == 0) {
            return NULL;
        }
        over_dict(o, out);
        return out;
    }
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    check(moy_ui_over_block(out, (const uint8_t *)b.buf, b.len));
    return out;
}

// A verb's options as the engine reads them, NUL-separated: bytes as they
// are, or a list or tuple of the items, each as str() makes it, joined into
// `buf` (OPTS_BUF bytes, the caller's) or, where they run longer, into the
// heap. runtime/ui.py's `_opts` hands a sequence straight over where the
// module takes one (`opts_seq`), so a verb given its options builds nothing.
#define OPTS_BUF 512
static void opts_of(mp_obj_t o, uint8_t *buf, mp_buffer_info_t *out) {
    if (!mp_obj_is_type(o, &mp_type_list) && !mp_obj_is_type(o, &mp_type_tuple)) {
        mp_get_buffer_raise(o, out, MP_BUFFER_READ);
        return;
    }
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(o, &n, &it);
    for (size_t i = 0; i < n; i++) {
        if (!mp_obj_is_str(it[i])) {
            mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(n, NULL));
            for (size_t j = 0; j < n; j++) {
                mp_obj_t v = it[j];
                t->items[j] = mp_obj_is_str(v) ? v : mp_obj_str_make_new(&mp_type_str, 1, 0, &v);
            }
            it = t->items;
            break;
        }
    }
    size_t len = n ? n - 1 : 0;
    for (size_t i = 0; i < n; i++) {
        size_t k;
        mp_obj_str_get_data(it[i], &k);
        len += k;
    }
    uint8_t *dst = len <= OPTS_BUF ? buf : m_new(uint8_t, len);
    size_t at = 0;
    for (size_t i = 0; i < n; i++) {
        size_t k;
        const char *p = mp_obj_str_get_data(it[i], &k);
        if (i) {
            dst[at++] = 0;
        }
        memcpy(dst + at, p, k);
        at += k;
    }
    out->buf = dst;
    out->len = len;
}

// api_size() -> the bytes a context takes
static mp_obj_t ui_api_size(void) {
    return MP_OBJ_NEW_SMALL_INT((mp_int_t)moy_ui_api_size());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_api_size_obj, ui_api_size);

// api_begin(ctx, inp, rect, flags, fs) -> settled. `flags` moy_ui.h's
// MOY_UI_DESK and MOY_UI_WINDOW; `inp` an array('i') of MOY_UI_IN_LEN words,
// or None.
static mp_obj_t ui_api_begin(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    const int32_t *in = NULL;
    mp_buffer_info_t b;
    if (a[1] != mp_const_none) {
        mp_get_buffer_raise(a[1], &b, MP_BUFFER_READ);
        if (b.len < MOY_UI_IN_LEN * sizeof(int32_t)) {
            mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: short input"));
        }
        in = (const int32_t *)b.buf;
    }
    return mp_obj_new_bool(moy_ui_api_begin(c, in, box_of(a[2]), mp_obj_get_int(a[3]),
                                            mp_obj_get_int(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_begin_obj, 5, 5, ui_api_begin);

// api_page(ctx, cv, look, title, o) -> the content rect
static mp_obj_t ui_api_page(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    moy_ui_box_t b;
    moy_ui_api_page(c, &s, span_of(a[3]), over_of(a[4], &o), &b);
    return box_tuple(b);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_page_obj, 5, 5, ui_api_page);

// Which of `n` words string `v` is, -1 none (or no string).
static int word_of(mp_obj_t v, const char *const *w, int n) {
    if (!mp_obj_is_str(v)) {
        return -1;
    }
    size_t len;
    const char *p = mp_obj_str_get_data(v, &len);
    for (int i = 0; i < n; i++) {
        if (strlen(w[i]) == len && memcmp(w[i], p, len) == 0) {
            return i;
        }
    }
    return -1;
}

// A label's text options from its override dict, read as runtime/ui.py's
// `label` reads them where the module takes no dict: any of `wrap` (with
// `lines`), `align`, `inset`, `tone` and `face` present sets the next label's
// text.
static void label_text(moy_ui_ctx_t *c, mp_obj_t o) {
    static const char *const tones[] = {"ink", "dim", "value", "danger"};
    static const char *const faces[] = {"ui", "title", "mono", "caption", "key", "op",
                                        "result"};
    static const char *const centre[] = {"center"};
    if (!mp_obj_is_type(o, &mp_type_dict)) {
        return;
    }
    mp_map_t *m = mp_obj_dict_get_map(o);
    mp_map_elem_t *wrap = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_wrap), MP_MAP_LOOKUP);
    mp_map_elem_t *align = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_align), MP_MAP_LOOKUP);
    mp_map_elem_t *tone = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_tone), MP_MAP_LOOKUP);
    mp_map_elem_t *face = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_face), MP_MAP_LOOKUP);
    mp_map_elem_t *inset = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_inset), MP_MAP_LOOKUP);
    mp_map_elem_t *sunk = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_sunk), MP_MAP_LOOKUP);
    if (wrap == NULL && align == NULL && tone == NULL && face == NULL && inset == NULL
        && sunk == NULL) {
        return;
    }
    int lines = 0;
    if (wrap != NULL && mp_obj_is_true(wrap->value)) {
        mp_map_elem_t *k = mp_map_lookup(m, MP_OBJ_NEW_QSTR(MP_QSTR_lines), MP_MAP_LOOKUP);
        lines = k != NULL && mp_obj_is_true(k->value) ? (int)num(k->value) : 16;
    }
    int t = tone != NULL ? word_of(tone->value, tones, 4) : -1;
    int al = align != NULL && word_of(align->value, centre, 1) == 0 ? 1
             : sunk != NULL && mp_obj_is_true(sunk->value) ? 3
             : inset != NULL && mp_obj_is_true(inset->value) ? 2 : 0;
    moy_ui_api_text(c, lines, al,
                    t < 0 ? 0 : t, face != NULL ? word_of(face->value, faces, 7) : -1);
}

// api_label(ctx, cv, look, text, o): a dict `o` carries the text options too
// (label_text)
static mp_obj_t ui_api_label(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    label_text(c, a[4]);
    moy_ui_api_label(c, &s, span_of(a[3]), over_of(a[4], &o));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_label_obj, 5, 5, ui_api_label);

// api_button(ctx, cv, look, label, kind, o) -> activated; `kind` a button
// kind's index
static mp_obj_t ui_api_button(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    return mp_obj_new_bool(moy_ui_api_button(c, &s, span_of(a[3]), mp_obj_get_int(a[4]),
                                             over_of(a[5], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_button_obj, 6, 6, ui_api_button);

// api_toggle(ctx, cv, look, label, on, o) -> the new on
static mp_obj_t ui_api_toggle(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    return mp_obj_new_bool(moy_ui_api_toggle(c, &s, span_of(a[3]), mp_obj_is_true(a[4]),
                                             over_of(a[5], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_toggle_obj, 6, 6, ui_api_toggle);

// api_slider(ctx, cv, look, label, v, lo, hi, o) -> the new v
static mp_obj_t ui_api_slider(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    return mp_obj_new_int(moy_ui_api_slider(c, &s, span_of(a[3]), num(a[4]), num(a[5]),
                                            num(a[6]), over_of(a[7], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_slider_obj, 8, 8, ui_api_slider);

// api_icon(ctx, name) -> whether `name` names an icon: the next row's
static mp_obj_t ui_api_icon(mp_obj_t ctx, mp_obj_t name) {
    return mp_obj_new_bool(moy_ui_api_icon(ctx_of(ctx), span_of(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_icon_obj, ui_api_icon);

// api_box(ctx, id) -> (x, y, w, h) where stop `id` drew last frame, or None
static mp_obj_t ui_api_box(mp_obj_t ctx, mp_obj_t id) {
    int32_t v[4];
    if (!moy_ui_api_box(ctx_of(ctx), span_of(id), v)) {
        return mp_const_none;
    }
    mp_obj_t t[4] = {mp_obj_new_int(v[0]), mp_obj_new_int(v[1]), mp_obj_new_int(v[2]),
                     mp_obj_new_int(v[3])};
    return ret_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_box_obj, ui_api_box);

// api_progress(ctx, cv, look, label, v, hi, o)
static mp_obj_t ui_api_progress(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    moy_ui_api_progress(c, &s, span_of(a[3]), num(a[4]), num(a[5]), over_of(a[6], &o));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_progress_obj, 7, 7, ui_api_progress);

// api_list(ctx, cv, look, label, items, cur, o) -> the new cur; `items` the
// rows NUL-separated (bytes)
static mp_obj_t ui_api_list(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[4], ob, &b);
    return mp_obj_new_int(moy_ui_api_list(c, &s, span_of(a[3]), (const uint8_t *)b.buf, b.len,
                                          num(a[5]), over_of(a[6], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_list_obj, 7, 7, ui_api_list);

// api_group(ctx, cv, look, caption, o): a caption of length 0 ends the group
static mp_obj_t ui_api_group(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    moy_ui_api_group(c, &s, span_of(a[3]), over_of(a[4], &o));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_group_obj, 5, 5, ui_api_group);

// api_value(ctx, cv, look, kind, label, value, o) -> activated; `kind` an
// info's or a link's (moy_ui.h's MOY_UI_V_*)
static mp_obj_t ui_api_value(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    return mp_obj_new_bool(moy_ui_api_value(c, &s, num(a[3]), span_of(a[4]), span_of(a[5]),
                                            over_of(a[6], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_value_obj, 7, 7, ui_api_value);

// api_choice(ctx, cv, look, kind, label, opts, cur, o) -> the new cur; `opts`
// the options NUL-separated, or a swatch picker's colours as words (bytes)
static mp_obj_t ui_api_choice(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[5], ob, &b);
    return mp_obj_new_int(moy_ui_api_choice(c, &s, num(a[3]), span_of(a[4]),
                                            (const uint8_t *)b.buf, b.len, num(a[6]),
                                            over_of(a[7], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_choice_obj, 8, 8, ui_api_choice);

// api_hints(ctx, cv, look, items, o): `items` the key and action pairs
// NUL-separated (bytes)
static mp_obj_t ui_api_hints(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[3], ob, &b);
    moy_ui_api_hints(c, &s, (const uint8_t *)b.buf, b.len, over_of(a[4], &o));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_hints_obj, 5, 5, ui_api_hints);

// api_table_rows(ctx) -> (row0_y, row_h, view, pic_x, pic_side) of the table
// drawn last, or None where it had no picture column
static mp_obj_t ui_api_table_rows(mp_obj_t ctx) {
    int32_t v[8];
    if (!moy_ui_api_table_rows(ctx_of(ctx), v)) {
        return mp_const_none;
    }
    mp_obj_t view[4] = {mp_obj_new_int(v[2]), mp_obj_new_int(v[3]), mp_obj_new_int(v[4]),
                        mp_obj_new_int(v[5])};
    mp_obj_t t[5] = {mp_obj_new_int(v[0]), mp_obj_new_int(v[1]), ret_tuple(4, view),
                     mp_obj_new_int(v[6]), mp_obj_new_int(v[7])};
    return ret_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_table_rows_obj, ui_api_table_rows);

// api_table_pics(ctx, cv, look): the table drawn last's edge fades again over
// its picture column, its pictures drawn
static mp_obj_t ui_api_table_pics(mp_obj_t ctx, mp_obj_t cv, mp_obj_t look) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    moy_ui_api_table_pics(ctx_of(ctx), &s);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_api_table_pics_obj, ui_api_table_pics);

// api_table_part(ctx, cv, look, r) -> the next part's row of the table drawn
// last, its cell's x, y, w, h and what shows of it into r (eight words), or
// -1 after the last
static mp_obj_t ui_api_table_part(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[3], &b, MP_BUFFER_WRITE);
    if (b.len < 8 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("eight words"));
    }
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    return mp_obj_new_int(moy_ui_api_table_part(ctx_of(a[0]), &s, (int32_t *)b.buf));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_table_part_obj, 4, 4, ui_api_table_part);

// api_end(ctx, cv, look): the page's end, after its rows
static mp_obj_t ui_api_end(mp_obj_t ctx, mp_obj_t cv, mp_obj_t look) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    moy_ui_api_end(ctx_of(ctx), &s);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_api_end_obj, ui_api_end);

// fade(cv, rect, ci, up[, look]): a fade toward colour `ci` over `rect`
static mp_obj_t ui_fade(size_t n_args, const mp_obj_t *a) {
    moy_ui_sink_t s;
    sink_of(&s, a[0], n_args > 4 ? a[4] : mp_const_none);
    check(moy_ui_fade(&s, box_of(a[1]), mp_obj_get_int(a[2]), mp_obj_is_true(a[3]), s.fs, 0));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_fade_obj, 4, 5, ui_fade);

// api_hints_w(ctx, cv, look, items) -> the width those hints take
static mp_obj_t ui_api_hints_w(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[3], ob, &b);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_hints_w(c, &s, (const uint8_t *)b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_hints_w_obj, 4, 4, ui_api_hints_w);

// api_part(ctx, cv, look, id, w, h, o) -> (x, y, w, h, state)
static mp_obj_t ui_api_part(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    int32_t v[5];
    moy_ui_api_part(c, &s, span_of(a[3]), num(a[4]), num(a[5]), over_of(a[6], &o), v);
    mp_obj_t t[5];
    for (int i = 0; i < 5; i++) {
        t[i] = mp_obj_new_int(v[i]);
    }
    return ret_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_part_obj, 7, 7, ui_api_part);

// api_grid(ctx, cv, look, id, w, h, o): an id of length 0 ends the grid
static mp_obj_t ui_api_grid(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    moy_ui_api_grid(c, &s, span_of(a[3]), num(a[4]), num(a[5]), over_of(a[6], &o));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_grid_obj, 7, 7, ui_api_grid);

// api_card(ctx, cv, look, title, sub, o) -> (x, y, w, h, state): the picture's rect
static mp_obj_t ui_api_card(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    int32_t v[5];
    moy_ui_api_card(c, &s, span_of(a[3]), span_of(a[4]), over_of(a[5], &o), v);
    mp_obj_t t[5];
    for (int i = 0; i < 5; i++) {
        t[i] = mp_obj_new_int(v[i]);
    }
    return ret_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_card_obj, 6, 6, ui_api_card);

// api_table(ctx, cv, look, label, cols, cells, cur, o) -> (cur, opened); `cols`
// and `cells` NUL-separated (bytes), the cells row by row
static mp_obj_t ui_api_table(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t cb, eb;
    mp_get_buffer_raise(a[4], &cb, MP_BUFFER_READ);
    mp_get_buffer_raise(a[5], &eb, MP_BUFFER_READ);
    int32_t v[2];
    moy_ui_api_table(c, &s, span_of(a[3]), (const uint8_t *)cb.buf, cb.len,
                     (const uint8_t *)eb.buf, eb.len, num(a[6]), over_of(a[7], &o), v);
    mp_obj_t t[2] = {mp_obj_new_int(v[0]), mp_obj_new_bool(v[1])};
    return ret_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_table_obj, 8, 8, ui_api_table);

// A verb that opens a nest: its answer, or RuntimeError where MOY_UI_API_NEST
// were open already.
static mp_int_t nested(int32_t v) {
    if (v == MOY_UI_API_DEEP) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moy_ui: nested too deep"));
    }
    return v;
}

// api_scroll(ctx, cv, look, id, o): an id of length 0 ends the region
static mp_obj_t ui_api_scroll(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    nested(moy_ui_api_scroll(c, &s, span_of(a[3]), over_of(a[4], &o)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_scroll_obj, 5, 5, ui_api_scroll);

// ui.text_view's lines: a list's (or a tuple's) str items, by index.
static const uint8_t *tv_line(void *src, int32_t i, size_t *n) {
    size_t len;
    mp_obj_t *it;
    mp_obj_get_array((mp_obj_t)src, &len, &it);
    if (i < 0 || (size_t)i >= len) {
        *n = 0;
        return NULL;
    }
    return (const uint8_t *)mp_obj_str_get_data(it[i], n);
}

// api_text_view(ctx, cv, look, id, lines, opt, o[, err, find, into]) -> (first
// line shown, rows shown); `opt` the MOY_UI_TXO_* words (an array('i')), `err`
// an error's words and `find` the find query (None: none); `into`, an
// array('i') of two, takes the answer and is handed back: no tuple is made
static mp_obj_t ui_api_text_view(size_t n_args, const mp_obj_t *a) {
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    mp_buffer_info_t ob;
    mp_get_buffer_raise(a[5], &ob, MP_BUFFER_READ);
    if (ob.len < MOY_UI_TXO_LEN * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: text_view's options"));
    }
    size_t nl;
    mp_obj_t *it;
    mp_obj_get_array(a[4], &nl, &it);
    moy_ui_lines_t L = {(void *)a[4], (int32_t)nl, tv_line};
    moy_ui_over_t o;
    int32_t out[2];
    moy_ui_span_t marks[2] = {span_of(n_args > 7 ? a[7] : mp_const_none),
                              span_of(n_args > 8 ? a[8] : mp_const_none)};
    nested(moy_ui_api_text_view(c, &s, span_of(a[3]), &L, (const int32_t *)ob.buf, marks,
                                over_of(a[6], &o), out));
    if (n_args > 9 && a[9] != mp_const_none) {
        mp_buffer_info_t ib;
        mp_get_buffer_raise(a[9], &ib, MP_BUFFER_WRITE);
        if (ib.len < 2 * sizeof(int32_t)) {
            mp_raise_ValueError(MP_ERROR_TEXT("moy_ui: text_view's answer"));
        }
        ((int32_t *)ib.buf)[0] = out[0];
        ((int32_t *)ib.buf)[1] = out[1];
        return a[9];
    }
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(out[0]), MP_OBJ_NEW_SMALL_INT(out[1])};
    return ret_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_text_view_obj, 7, 10, ui_api_text_view);

// api_text_at(ctx, id, px, py) -> (line, column) under the point in view
// `id` as its last pass drew it, or None
static mp_obj_t ui_api_text_at(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t v[2];
    if (!moy_ui_api_text_at(ctx_of(a[0]), span_of(a[1]), mp_obj_get_int(a[2]),
                            mp_obj_get_int(a[3]), v)) {
        return mp_const_none;
    }
    mp_obj_t t[2] = {mp_obj_new_int(v[0]), mp_obj_new_int(v[1])};
    return ret_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_text_at_obj, 4, 4, ui_api_text_at);

// api_text_cover(ctx, id, y0, y1): rows y0..y1 of view `id` were drawn over
static mp_obj_t ui_api_text_cover(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_api_text_cover(ctx_of(a[0]), span_of(a[1]), mp_obj_get_int(a[2]),
                          mp_obj_get_int(a[3]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_text_cover_obj, 4, 4, ui_api_text_cover);

// api_text_aim(ctx, id, line, lines) -> whether the view moved: before the
// frame begins, its first line (-1 its foot), so the move is the frame's shift
static mp_obj_t ui_api_text_aim(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return mp_obj_new_bool(moy_ui_api_text_aim(ctx_of(a[0]), span_of(a[1]),
                                               mp_obj_get_int(a[2]), mp_obj_get_int(a[3])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_text_aim_obj, 4, 4, ui_api_text_aim);

// text_tones(tones): the colour indices the syntax classes take under the
// live theme (text, keyword, string, number, comment, builtin; -1 a token's)
static mp_obj_t ui_text_tones(mp_obj_t tones) {
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(tones, &n, &it);
    int16_t t[6];
    for (size_t i = 0; i < 6; i++) {
        t[i] = i < n && it[i] != mp_const_none ? (int16_t)mp_obj_get_int(it[i]) : -1;
    }
    moy_ui_text_tones(t);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_text_tones_obj, ui_text_tones);

// api_field(ctx, cv, look, label, buf, n, o) -> the new length, plus 0x10000
// the frame Enter is pressed in it; `buf` a bytearray, its room the field's
static mp_obj_t ui_api_field(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t b;
    mp_get_buffer_raise(a[4], &b, MP_BUFFER_RW);
    return mp_obj_new_int(moy_ui_api_field(c, &s, span_of(a[3]), (uint8_t *)b.buf, b.len,
                                           (size_t)num(a[5]), over_of(a[6], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_field_obj, 7, 7, ui_api_field);

// api_dialog(ctx, cv, look, title, buttons, kind, o) -> the button activated
// (from 0), -1 none; a title of length 0 ends the dialog
static mp_obj_t ui_api_dialog(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[4], ob, &b);
    return mp_obj_new_int(nested(moy_ui_api_dialog(c, &s, span_of(a[3]), (const uint8_t *)b.buf,
                                                   b.len, num(a[5]), over_of(a[6], &o))));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_dialog_obj, 7, 7, ui_api_dialog);

// api_wash(ctx, cv, look): wash what a dialog begun now would
static mp_obj_t ui_api_wash(mp_obj_t ctx, mp_obj_t cv, mp_obj_t look) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    moy_ui_api_wash(ctx_of(ctx), &s);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_api_wash_obj, ui_api_wash);

// api_wash_box(ctx) -> (x, y, w, h): what api_wash covers
static mp_obj_t ui_api_wash_box(mp_obj_t ctx) {
    return box_tuple(moy_ui_api_wash_box(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_wash_box_obj, ui_api_wash_box);

// api_popover(ctx, cv, look, id, w, o) -> 1, 0 the frame it is dismissed; an
// id of length 0 ends the popover
static mp_obj_t ui_api_popover(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    return mp_obj_new_int(nested(moy_ui_api_popover(c, &s, span_of(a[3]), num(a[4]),
                                                    over_of(a[5], &o))));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_popover_obj, 6, 6, ui_api_popover);

// api_menu(ctx, cv, look, label, items, icons, o) -> the item activated (from
// 0), -1 none
static mp_obj_t ui_api_menu(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    mp_buffer_info_t b, ib;
    uint8_t ob[OPTS_BUF], oi[OPTS_BUF];
    opts_of(a[4], ob, &b);
    opts_of(a[5], oi, &ib);
    return mp_obj_new_int(moy_ui_api_menu(c, &s, span_of(a[3]), (const uint8_t *)b.buf, b.len,
                                          (const uint8_t *)ib.buf, ib.len, over_of(a[6], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_menu_obj, 7, 7, ui_api_menu);

// api_menu_danger(ctx, rows): the next api_menu's danger rows, bit i row i.
static mp_obj_t ui_api_menu_danger(mp_obj_t ctx, mp_obj_t rows) {
    moy_ui_api_menu_danger(ctx_of(ctx), (uint32_t)mp_obj_get_int_truncated(rows));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_menu_danger_obj, ui_api_menu_danger);

// api_tool(ctx, cv, look, icon, label, o) -> activated
static mp_obj_t ui_api_tool(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    moy_ui_over_t o;
    return mp_obj_new_bool(moy_ui_api_tool(c, &s, span_of(a[3]), span_of(a[4]),
                                           over_of(a[5], &o)));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_tool_obj, 6, 6, ui_api_tool);

// api_bar(ctx, cv, look, title, tools, off) -> the tool the host's bar
// activated (from 0), -2 its way back, -1 none
static mp_obj_t ui_api_bar(size_t n_args, const mp_obj_t *a) {
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[4], ob, &b);
    // `back` apart from the bits: MOY_UI_BAR_BACK is no small int on a board.
    uint32_t off = (uint32_t)mp_obj_get_int_truncated(a[5]);
    if (n_args > 6 && mp_obj_is_true(a[6])) {
        off |= MOY_UI_BAR_BACK;
    }
    return mp_obj_new_int(moy_ui_api_bar(c, &s, span_of(a[3]), (const uint8_t *)b.buf, b.len,
                                         off));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_bar_obj, 6, 7, ui_api_bar);

// api_bar_lent(ctx) -> (title, slots, off, chip) the frame drawn last lent
// its bar, or None
static mp_obj_t ui_api_bar_lent(mp_obj_t ctx) {
    const char *title = NULL;
    int16_t slots[MOY_UI_API_BAR];
    int n = 0;
    uint32_t off = 0;
    if (!moy_ui_api_bar_lent(ctx_of(ctx), &title, slots, &n, &off)) {
        return mp_const_none;
    }
    mp_obj_t sl[MOY_UI_API_BAR];
    for (int i = 0; i < n; i++) {
        sl[i] = MP_OBJ_NEW_SMALL_INT(slots[i]);
    }
    int at = -1;
    const char *chip = moy_ui_api_bar_chip(ctx_of(ctx), &at);
    // The host's bar asks every frame it draws, and the bar lent is the last
    // one's as a rule: the answer it had, where it says the same, allocates
    // nothing.
    mp_obj_t last = MP_STATE_VM(moy_ui_bar_last);
    if (last != MP_OBJ_NULL && gc_nbytes(MP_OBJ_TO_PTR(last)) != 0
            && mp_obj_is_type(last, &mp_type_tuple)) {
        mp_obj_tuple_t *lt = MP_OBJ_TO_PTR(last);
        if (lt->len == 4 && mp_obj_is_str(lt->items[0]) && mp_obj_is_str(lt->items[3])
                && mp_obj_is_type(lt->items[1], &mp_type_tuple)
                && strcmp(mp_obj_str_get_str(lt->items[0]), title) == 0
                && strcmp(mp_obj_str_get_str(lt->items[3]), chip) == 0
                && mp_obj_get_int_truncated(lt->items[2]) == (mp_int_t)off) {
            mp_obj_tuple_t *ls = MP_OBJ_TO_PTR(lt->items[1]);
            int k = 0;
            while (k < n && (size_t)k < ls->len && ls->items[k] == sl[k]) {
                k++;
            }
            if (k == n && ls->len == (size_t)n) {
                return last;
            }
        }
    }
    mp_obj_t t[4] = {mp_obj_new_str(title, strlen(title)), mp_obj_new_tuple(n, sl),
                     mp_obj_new_int_from_uint(off), mp_obj_new_str(chip, strlen(chip))};
    last = mp_obj_new_tuple(4, t);
    MP_STATE_VM(moy_ui_bar_last) = last;
    return last;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_bar_lent_obj, ui_api_bar_lent);

// api_rule(ctx, cv, look)
static mp_obj_t ui_api_rule(mp_obj_t ctx, mp_obj_t cv, mp_obj_t look) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    moy_ui_api_rule(ctx_of(ctx), &s);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_api_rule_obj, ui_api_rule);

// api_bar_tap(ctx, i): the host's bar activated tool i of the bar lent last
static mp_obj_t ui_api_bar_tap(mp_obj_t ctx, mp_obj_t i) {
    moy_ui_api_bar_tap(ctx_of(ctx), mp_obj_get_int(i));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_bar_tap_obj, ui_api_bar_tap);

// api_text(ctx, lines, centre, tone, role): the next label's text options.
static mp_obj_t ui_api_text(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_api_text(ctx_of(a[0]), num(a[1]), num(a[2]), num(a[3]), num(a[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_text_obj, 5, 5, ui_api_text);

// api_card_mark(ctx, badge, tone, blank): the next card's badge, tone and
// blank picture's icon.
static mp_obj_t ui_api_card_mark(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_api_card_mark(ctx_of(a[0]), span_of(a[1]), num(a[2]), span_of(a[3]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_card_mark_obj, 4, 4, ui_api_card_mark);

// api_grid_spread(ctx, cols, foot, top, margin, wrap): the next grid's spread.
static mp_obj_t ui_api_grid_spread(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_api_grid_spread(ctx_of(a[0]), num(a[1]), num(a[2]), num(a[3]), num(a[4]),
                           num(a[5]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_grid_spread_obj, 6, 6, ui_api_grid_spread);

// api_group_card(ctx, cv, look): a group with no caption.
static mp_obj_t ui_api_group_card(mp_obj_t ctx, mp_obj_t cv, mp_obj_t look) {
    moy_ui_sink_t s;
    sink_of(&s, cv, look);
    moy_ui_api_group_card(ctx_of(ctx), &s);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_api_group_card_obj, ui_api_group_card);

// api_empty(ctx, cv, look, icon, title, lines) -> the head's foot y
static mp_obj_t ui_api_empty(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    mp_buffer_info_t b;
    uint8_t ob[OPTS_BUF];
    opts_of(a[5], ob, &b);
    return mp_obj_new_int(moy_ui_api_empty(c, &s, span_of(a[3]), span_of(a[4]),
                                           (const uint8_t *)b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_empty_obj, 6, 6, ui_api_empty);

// api_month(ctx, cv, look, title, days, first, n, today) -> -1, 0 or 1
static mp_obj_t ui_api_month(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    return mp_obj_new_int(moy_ui_api_month(c, &s, span_of(a[3]), span_of(a[4]), num(a[5]),
                                           num(a[6]), num(a[7])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_month_obj, 8, 8, ui_api_month);

// api_toast(ctx, cv, look, text, kind, flat) -> the pill's (x, y, w, h)
static mp_obj_t ui_api_toast(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_ctx_t *c = ctx_of(a[0]);
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    int32_t b[4];
    moy_ui_api_toast(c, &s, span_of(a[3]), span_of(a[4]), mp_obj_is_true(a[5]), b);
    mp_obj_t t[4] = {mp_obj_new_int(b[0]), mp_obj_new_int(b[1]), mp_obj_new_int(b[2]),
                     mp_obj_new_int(b[3])};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_toast_obj, 6, 6, ui_api_toast);

// api_owes(ctx) -> whether the frame drawn last owes the next
static mp_obj_t ui_api_owes(mp_obj_t ctx) {
    return mp_obj_new_bool(moy_ui_api_owes(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_owes_obj, ui_api_owes);

// api_moved(ctx) -> whether this frame's begin moved the focus or a scroll
static mp_obj_t ui_api_moved(mp_obj_t ctx) {
    return mp_obj_new_bool(moy_ui_api_moved(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_moved_obj, ui_api_moved);

// api_moving(ctx) -> whether a page of the context flings or glides
static mp_obj_t ui_api_moving(mp_obj_t ctx) {
    return mp_obj_new_bool(moy_ui_api_moving(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_moving_obj, ui_api_moving);

// api_busy(ctx) -> whether the frame drawn last owes the next or a page of
// the context flings or glides: api_owes or api_moving, in one call
static mp_obj_t ui_api_busy(mp_obj_t ctx) {
    const moy_ui_ctx_t *c = ctx_of(ctx);
    return mp_obj_new_bool(moy_ui_api_owes(c) || moy_ui_api_moving(c));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_busy_obj, ui_api_busy);

// api_offset(ctx, x, y) -> the scroll of the page under (x, y), -1 none
static mp_obj_t ui_api_offset(mp_obj_t ctx, mp_obj_t x, mp_obj_t y) {
    return mp_obj_new_int(moy_ui_api_offset(ctx_of(ctx), num(x), num(y)));
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_api_offset_obj, ui_api_offset);

// api_fades(reset) -> the list fades drawn since the count was reset
static mp_obj_t ui_api_fades(mp_obj_t reset) {
    return mp_obj_new_int_from_uint(moy_ui_api_fades(mp_obj_is_true(reset)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_fades_obj, ui_api_fades);

// api_focus(ctx, id=None) -> the focused id, or None
static mp_obj_t ui_api_focus(mp_obj_t ctx, mp_obj_t id) {
    moy_ui_ctx_t *c = ctx_of(ctx);
    moy_ui_span_t sp = {NULL, 0};
    if (id != mp_const_none) {
        sp = span_of(id);
        if (sp.p == NULL) {
            sp.p = (const uint8_t *)"";
        }
    }
    char buf[MOY_UI_API_NAME];
    int n = moy_ui_api_focus(c, sp, buf, sizeof(buf));
    return n < 0 ? mp_const_none : ret_str(buf, strlen(buf));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_focus_obj, ui_api_focus);

// api_focus_ahead(ctx, id): focus on `id` from the next frame's begin
static mp_obj_t ui_api_focus_ahead(mp_obj_t ctx, mp_obj_t id) {
    moy_ui_span_t sp = span_of(id);
    if (sp.p == NULL) {
        sp.p = (const uint8_t *)"";
    }
    moy_ui_api_focus_ahead(ctx_of(ctx), sp);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_focus_ahead_obj, ui_api_focus_ahead);

// api_shift_plan(ctx, out) -> the shift frame begin licensed (moy_ui_api.h's
// MOY_UI_SHIFT_*); `out`, eight writable int32 words: R's x, y, w, h, dy,
// the floor's rows, the pictures' rows it leaves and, for a dialog's rows
// frame, the frames its card has stood (0: a page's)
static mp_obj_t ui_api_shift_plan(mp_obj_t ctx, mp_obj_t out) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(out, &b, MP_BUFFER_WRITE);
    if (b.len < 8 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("eight words"));
    }
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_shift_plan(ctx_of(ctx), (int32_t *)b.buf));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_shift_plan_obj, ui_api_shift_plan);

// api_shift_arm(ctx): R's pixels are moved; this frame's pass draws its floor
static mp_obj_t ui_api_shift_arm(mp_obj_t ctx) {
    moy_ui_api_shift_arm(ctx_of(ctx));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_shift_arm_obj, ui_api_shift_arm);

// api_shift_rows(ctx, rects, n, out) -> the rows frame its host asks for
// (moy_ui_api_shift_rows); `rects`, n x, y, w, h int32 words; `out`, eight
// writable int32 words, the plan as api_shift_plan's
static mp_obj_t ui_api_shift_rows(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t r, o;
    mp_get_buffer_raise(a[1], &r, MP_BUFFER_READ);
    mp_get_buffer_raise(a[3], &o, MP_BUFFER_WRITE);
    int n = mp_obj_get_int(a[2]);
    if (o.len < 8 * sizeof(int32_t) || n < 0 || r.len < (size_t)n * 4 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("rects"));
    }
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_shift_rows(ctx_of(a[0]), (const int32_t *)r.buf, n,
                                                      (int32_t *)o.buf));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_shift_rows_obj, 4, 4, ui_api_shift_rows);

// api_floor_take(ctx, cv, look, x, y, w, h) -> whether the rect joined an
// armed frame's floor over the page's ground (moy_ui_api_floor_take)
static mp_obj_t ui_api_floor_take(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    return mp_obj_new_bool(moy_ui_api_floor_take(ctx_of(a[0]), &s, num(a[3]), num(a[4]),
                                                 num(a[5]), num(a[6])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_floor_take_obj, 7, 7, ui_api_floor_take);

// api_shift_break(ctx): the frame draws whole from here
static mp_obj_t ui_api_shift_break(mp_obj_t ctx) {
    moy_ui_api_shift_break(ctx_of(ctx));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_shift_break_obj, ui_api_shift_break);

// api_shift_drawn(ctx) -> what the pass begun last drew (MOY_UI_SHIFT_*),
// plus 4 where it left R the page's alone
static mp_obj_t ui_api_shift_drawn(mp_obj_t ctx) {
    int clean = 0;
    int k = moy_ui_api_shift_drawn(ctx_of(ctx), &clean);
    return MP_OBJ_NEW_SMALL_INT(k | (clean ? 4 : 0));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_shift_drawn_obj, ui_api_shift_drawn);

// api_card_clip(ctx, r): `r`, four writable int32 words x, y, w, h, cut in
// place to the floor's rect the last card's picture draws through
// (moy_ui_api_card_clip); nothing made
static mp_obj_t ui_api_card_clip(mp_obj_t ctx, mp_obj_t r) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(r, &b, MP_BUFFER_WRITE);
    if (b.len < 4 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("four words"));
    }
    moy_ui_api_card_clip(ctx_of(ctx), (int32_t *)b.buf);
    return r;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_card_clip_obj, ui_api_card_clip);

// api_band(ctx[, r]) -> (x, y, w, h) of the open page this frame draws, in
// the page's coordinates, or None with no page open; given `r`, four
// writable int32 words, written there and `r` answered (nothing made)
static mp_obj_t ui_api_band(size_t n_args, const mp_obj_t *a) {
    int32_t v[4];
    if (n_args > 1) {
        mp_buffer_info_t b;
        mp_get_buffer_raise(a[1], &b, MP_BUFFER_WRITE);
        if (b.len < 4 * sizeof(int32_t)) {
            mp_raise_ValueError(MP_ERROR_TEXT("four words"));
        }
        return moy_ui_api_band(ctx_of(a[0]), (int32_t *)b.buf) ? a[1] : mp_const_none;
    }
    if (!moy_ui_api_band(ctx_of(a[0]), v)) {
        return mp_const_none;
    }
    mp_obj_t t[4];
    for (int i = 0; i < 4; i++) {
        t[i] = mp_obj_new_int(v[i]);
    }
    return ret_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_band_obj, 1, 2, ui_api_band);

// api_floor_into(ctx, out) -> the floor's rects into `out`, writable int32
// words, x, y, w, h each, as many as it holds: their count, -1 where the
// pass draws whole (moy_ui_api_floor)
static mp_obj_t ui_api_floor_into(mp_obj_t ctx, mp_obj_t out) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(out, &b, MP_BUFFER_WRITE);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_floor(ctx_of(ctx), (int32_t *)b.buf,
                                                 (int)(b.len / (4 * sizeof(int32_t)))));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_floor_into_obj, ui_api_floor_into);

// api_floor_box(ctx, out) -> whether the pass drew a floor; `out`, four
// writable int32 words, its bounding rect on screen (moy_ui_api_floor_box)
static mp_obj_t ui_api_floor_box(mp_obj_t ctx, mp_obj_t out) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(out, &b, MP_BUFFER_WRITE);
    if (b.len < 4 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("four words"));
    }
    return mp_obj_new_bool(moy_ui_api_floor_box(ctx_of(ctx), (int32_t *)b.buf));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_floor_box_obj, ui_api_floor_box);

// api_lag_note(ctx, hist, bands, f, kind): frame f's slot of the lag history
// `hist`, writable int32 words in slots of 2 + 2 * bands, from the pass begun
// last (moy_ui_api_lag_note)
static mp_obj_t ui_api_lag_note(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(args[1], &b, MP_BUFFER_WRITE);
    int bands = mp_obj_get_int(args[2]);
    int slots = (int)(b.len / (sizeof(int32_t) * (size_t)(2 + 2 * bands)));
    moy_ui_api_lag_note(ctx_of(args[0]), (int32_t *)b.buf, slots, bands,
                        mp_obj_get_int(args[3]), mp_obj_get_int(args[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_lag_note_obj, 5, 5, ui_api_lag_note);

// api_lag(hist, bands, f, n, out) -> the bands the buffer frame f paints lags
// by into `out` (writable int32 pairs), their count, -1 where it lags
// otherwise (moy_ui_api_lag)
static mp_obj_t ui_api_lag(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t h, o;
    mp_get_buffer_raise(args[0], &h, MP_BUFFER_READ);
    mp_get_buffer_raise(args[4], &o, MP_BUFFER_WRITE);
    int bands = mp_obj_get_int(args[1]);
    int slots = (int)(h.len / (sizeof(int32_t) * (size_t)(2 + 2 * bands)));
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_lag((const int32_t *)h.buf, slots, bands,
                                               mp_obj_get_int(args[2]), mp_obj_get_int(args[3]),
                                               (int32_t *)o.buf,
                                               (int)(o.len / (2 * sizeof(int32_t)))));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_lag_obj, 5, 5, ui_api_lag);

// int32 words of `o`, at least `n` of them, writable where `w`
static int32_t *words_of(mp_obj_t o, size_t n, int w, mp_rom_error_text_t what) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, w ? MP_BUFFER_WRITE : MP_BUFFER_READ);
    if (b.len < n * sizeof(int32_t)) {
        mp_raise_ValueError(what);
    }
    return (int32_t *)b.buf;
}

// api_host_begin(ctx, inp, rect, flags, fs, pf, hp) -> MOY_UI_HB_* bits: an
// app host's frame begins (moy_ui_api_host_begin). `inp` an array('i') of
// MOY_UI_IN_LEN words, `pf` of MOY_UI_PF_LEN, `hp` of MOY_UI_HP_LEN; nothing
// made.
static mp_obj_t ui_api_host_begin(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int32_t *in = words_of(a[1], MOY_UI_IN_LEN, 1, MP_ERROR_TEXT("moy_ui: short input"));
    const int32_t *pf = words_of(a[5], MOY_UI_PF_LEN, 0, MP_ERROR_TEXT("moy_ui: short frame"));
    int32_t *hp = words_of(a[6], MOY_UI_HP_LEN, 1, MP_ERROR_TEXT("moy_ui: short host"));
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_host_begin(ctx_of(a[0]), in, box_of(a[2]),
                                                      mp_obj_get_int(a[3]),
                                                      mp_obj_get_int(a[4]), pf, hp));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_host_begin_obj, 7, 7, ui_api_host_begin);

// api_host_arm(ctx) -> the frame's dy: the host made its begin's moves
static mp_obj_t ui_api_host_arm(mp_obj_t ctx) {
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_host_arm(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_host_arm_obj, ui_api_host_arm);

// api_host_rows(ctx, rects, n, move): the rows the frame begun next draws
// alone (moy_ui_api_host_rows): `rects` int32 words; `move` five ints, or None
static mp_obj_t ui_api_host_rows(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    int n = mp_obj_get_int(a[2]);
    n = n < 0 ? 0 : n > MOY_UI_API_HOST ? MOY_UI_API_HOST : n;
    const int32_t *r = words_of(a[1], 4 * (size_t)n, 0, MP_ERROR_TEXT("moy_ui: short rows"));
    int32_t m[5];
    if (a[3] != mp_const_none) {
        mp_obj_t *it;
        mp_obj_get_array_fixed_n(a[3], 5, &it);
        for (int i = 0; i < 5; i++) {
            m[i] = mp_obj_get_int(it[i]);
        }
    }
    moy_ui_api_host_rows(ctx_of(a[0]), r, n, a[3] == mp_const_none ? NULL : m);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_host_rows_obj, 4, 4, ui_api_host_rows);

// api_host_whole(ctx): the frame begun next draws whole
static mp_obj_t ui_api_host_whole(mp_obj_t ctx) {
    moy_ui_api_host_whole(ctx_of(ctx));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_host_whole_obj, ui_api_host_whole);


// -- text views (moy_ui_text.h) ----------------------------------------------------

// api_pop_box(ctx, out) -> whether the pass drew a popover; `out`, four
// writable int32 words, its card
static mp_obj_t ui_api_pop_box(mp_obj_t ctx, mp_obj_t out) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(out, &b, MP_BUFFER_WRITE);
    if (b.len < 4 * sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("four words"));
    }
    return mp_obj_new_bool(moy_ui_api_pop_box(ctx_of(ctx), (int32_t *)b.buf));
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_api_pop_box_obj, ui_api_pop_box);

// text_words(syntax, words, tone): words (NUL-separated) a syntax colours
static mp_obj_t ui_text_words(mp_obj_t syn, mp_obj_t words, mp_obj_t tone) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(words, &b, MP_BUFFER_READ);
    check(moy_ui_text_words(mp_obj_get_int(syn), (const uint8_t *)b.buf, b.len,
                            mp_obj_get_int(tone)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(ui_text_words_obj, ui_text_words);

// text_runs(line, syntax) -> the line's colour runs, a flat list of (byte
// start, byte end, column, tone) per run
static mp_obj_t ui_text_runs(mp_obj_t line, mp_obj_t syn) {
    size_t n;
    const uint8_t *p = (const uint8_t *)mp_obj_str_get_data(line, &n);
    int k = moy_ui_text_runs(p, n, mp_obj_get_int(syn), NULL, 0);
    int32_t *w = m_new(int32_t, 4 * k + 1);
    moy_ui_text_runs(p, n, mp_obj_get_int(syn), w, 4 * k);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < 4 * k; i++) {
        mp_obj_list_append(out, MP_OBJ_NEW_SMALL_INT(w[i]));
    }
    m_del(int32_t, w, 4 * k + 1);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_2(ui_text_runs_obj, ui_text_runs);

// api_keep(ctx, cv, look, id, h) -> the state bits of a stop carried forward
// undrawn
static mp_obj_t ui_api_keep(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_sink_t s;
    sink_of(&s, a[1], a[2]);
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_keep(ctx_of(a[0]), &s, span_of(a[3]), num(a[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_keep_obj, 5, 5, ui_api_keep);

// api_audit(on) -> the shift frames whose records moved outside the floor
// since the last call (the tests' net); on -1 leaves the switch
static mp_obj_t ui_api_audit(mp_obj_t on) {
    return mp_obj_new_int_from_uint(moy_ui_api_audit(mp_obj_get_int(on)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_audit_obj, ui_api_audit);

// api_keys_taken(ctx) -> whether the ui used this frame's keys
static mp_obj_t ui_api_keys_taken(mp_obj_t ctx) {
    return mp_obj_new_bool(moy_ui_api_keys_taken(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_keys_taken_obj, ui_api_keys_taken);

// api_edge(ctx) -> whether this frame's up press went past the page's top
static mp_obj_t ui_api_edge(mp_obj_t ctx) {
    return mp_obj_new_bool(moy_ui_api_edge(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_edge_obj, ui_api_edge);

// api_tap_armed(ctx) -> whether the press down may tap where it is released
static mp_obj_t ui_api_tap_armed(mp_obj_t ctx) {
    return mp_obj_new_bool(moy_ui_api_tap_armed(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_tap_armed_obj, ui_api_tap_armed);

// api_at(ctx, x, y, w, h)
static mp_obj_t ui_api_at(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_ui_api_at(ctx_of(a[0]), num(a[1]), num(a[2]), num(a[3]), num(a[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(ui_api_at_obj, 5, 5, ui_api_at);

// api_dup(ctx) -> a duplicate id drawn this frame, once per id, or None
static mp_obj_t ui_api_dup(mp_obj_t ctx) {
    const char *d = moy_ui_api_dup(ctx_of(ctx));
    return d == NULL ? mp_const_none : mp_obj_new_str(d, strlen(d));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_dup_obj, ui_api_dup);

// api_lost(ctx) -> the stops the last frame drew past the ones it records,
// once per count, else 0
static mp_obj_t ui_api_lost(mp_obj_t ctx) {
    return MP_OBJ_NEW_SMALL_INT(moy_ui_api_lost(ctx_of(ctx)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(ui_api_lost_obj, ui_api_lost);

// wasm_rows() -> the public UI API's rows a compiled app imports ("ui_page", ...),
// in roles.json's order: what this image serves (moy_ui_wasm.h).
static mp_obj_t ui_wasm_rows(void) {
    mp_obj_t items[MOY_UI_WASM_ROWS];
    for (int i = 0; i < MOY_UI_WASM_ROWS; i++) {
        const char *n = moy_ui_wasm_name(i);
        items[i] = mp_obj_new_str(n, strlen(n));
    }
    return mp_obj_new_tuple(MOY_UI_WASM_ROWS, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_wasm_rows_obj, ui_wasm_rows);

static mp_obj_t ui_calls(void) {
    return mp_obj_new_int_from_uint(moy_ui_calls());
}
static MP_DEFINE_CONST_FUN_OBJ_0(ui_calls_obj, ui_calls);

static const mp_rom_map_elem_t moy_ui_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ui) },
    { MP_ROM_QSTR(MP_QSTR_tokens), MP_ROM_PTR(&ui_tokens_obj) },
    { MP_ROM_QSTR(MP_QSTR_over_dict), MP_ROM_TRUE },
    { MP_ROM_QSTR(MP_QSTR_opts_seq), MP_ROM_TRUE },
    { MP_ROM_QSTR(MP_QSTR_states), MP_ROM_PTR(&ui_states_obj) },
    { MP_ROM_QSTR(MP_QSTR_kinds), MP_ROM_PTR(&ui_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR_metric_kinds), MP_ROM_PTR(&ui_metric_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR_kind), MP_ROM_PTR(&ui_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_button_kind), MP_ROM_PTR(&ui_button_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_themes), MP_ROM_PTR(&ui_themes_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme), MP_ROM_PTR(&ui_theme_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_modes), MP_ROM_PTR(&ui_theme_modes_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_roles), MP_ROM_PTR(&ui_theme_roles_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_shape), MP_ROM_PTR(&ui_theme_shape_obj) },
    { MP_ROM_QSTR(MP_QSTR_surface), MP_ROM_PTR(&ui_surface_obj) },
    { MP_ROM_QSTR(MP_QSTR_use_shape), MP_ROM_PTR(&ui_use_shape_obj) },
    { MP_ROM_QSTR(MP_QSTR_use_depth), MP_ROM_PTR(&ui_use_depth_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_geom), MP_ROM_PTR(&ui_frame_geom_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_under), MP_ROM_PTR(&ui_frame_under_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_under_onto), MP_ROM_PTR(&ui_frame_under_onto_obj) },
    { MP_ROM_QSTR(MP_QSTR_cast), MP_ROM_PTR(&ui_cast_obj) },
    { MP_ROM_QSTR(MP_QSTR_pattern_unreadable), MP_ROM_PTR(&ui_pattern_unreadable_obj) },
    { MP_ROM_QSTR(MP_QSTR_parts), MP_ROM_PTR(&ui_parts_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_states), MP_ROM_PTR(&ui_theme_states_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_install), MP_ROM_PTR(&ui_theme_install_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_installed), MP_ROM_PTR(&ui_theme_installed_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_uninstall), MP_ROM_PTR(&ui_theme_uninstall_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_fonts), MP_ROM_PTR(&ui_theme_fonts_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_sheets), MP_ROM_PTR(&ui_theme_sheets_obj) },
    { MP_ROM_QSTR(MP_QSTR_sheet_check), MP_ROM_PTR(&ui_sheet_check_obj) },
    { MP_ROM_QSTR(MP_QSTR_sheet_load), MP_ROM_PTR(&ui_sheet_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_sheet_clear), MP_ROM_PTR(&ui_sheet_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_sheet_cells), MP_ROM_PTR(&ui_sheet_cells_obj) },
    { MP_ROM_QSTR(MP_QSTR_sheet_set), MP_ROM_PTR(&ui_sheet_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_sheet_caps), MP_ROM_PTR(&ui_sheet_caps_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_layout), MP_ROM_PTR(&ui_theme_layout_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_home), MP_ROM_PTR(&ui_theme_home_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_resolve), MP_ROM_PTR(&ui_theme_resolve_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_look), MP_ROM_PTR(&ui_theme_look_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_backdrop), MP_ROM_PTR(&ui_theme_backdrop_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_look_fields), MP_ROM_PTR(&ui_theme_look_fields_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_place), MP_ROM_PTR(&ui_theme_place_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_block), MP_ROM_PTR(&ui_theme_block_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_rgb), MP_ROM_PTR(&ui_theme_rgb_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_custom), MP_ROM_PTR(&ui_theme_custom_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_accents), MP_ROM_PTR(&ui_theme_accents_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_presets), MP_ROM_PTR(&ui_theme_presets_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_tints), MP_ROM_PTR(&ui_theme_tints_obj) },
    { MP_ROM_QSTR(MP_QSTR_font_load), MP_ROM_PTR(&ui_font_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_font_file), MP_ROM_PTR(&ui_font_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_font_reset), MP_ROM_PTR(&ui_font_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_wire), MP_ROM_PTR(&ui_text_wire_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_lanes), MP_ROM_PTR(&ui_text_lanes_obj) },
    { MP_ROM_QSTR(MP_QSTR_print_font), MP_ROM_PTR(&ui_print_font_obj) },
    { MP_ROM_QSTR(MP_QSTR_cart_text), MP_ROM_PTR(&ui_cart_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_font_free), MP_ROM_PTR(&ui_font_free_obj) },
    { MP_ROM_QSTR(MP_QSTR_font_info), MP_ROM_PTR(&ui_font_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_use_font), MP_ROM_PTR(&ui_use_font_obj) },
    { MP_ROM_QSTR(MP_QSTR_font), MP_ROM_PTR(&ui_font_obj) },
    { MP_ROM_QSTR(MP_QSTR_look), MP_ROM_PTR(&ui_look_obj) },
    { MP_ROM_QSTR(MP_QSTR_colors), MP_ROM_PTR(&ui_colors_obj) },
    { MP_ROM_QSTR(MP_QSTR_button_style), MP_ROM_PTR(&ui_button_style_obj) },
    { MP_ROM_QSTR(MP_QSTR_buttons_set), MP_ROM_PTR(&ui_buttons_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_buttons_clear), MP_ROM_PTR(&ui_buttons_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_disabled), MP_ROM_PTR(&ui_disabled_obj) },
    { MP_ROM_QSTR(MP_QSTR_danger_ink), MP_ROM_PTR(&ui_danger_ink_obj) },
    { MP_ROM_QSTR(MP_QSTR_buttons_app), MP_ROM_PTR(&ui_buttons_app_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_reads), MP_ROM_PTR(&ui_theme_reads_obj) },
    { MP_ROM_QSTR(MP_QSTR_metrics), MP_ROM_PTR(&ui_metrics_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_width), MP_ROM_PTR(&ui_text_width_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_cut), MP_ROM_PTR(&ui_text_cut_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_covers), MP_ROM_PTR(&ui_text_covers_obj) },
    { MP_ROM_QSTR(MP_QSTR_wrap), MP_ROM_PTR(&ui_wrap_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph_names), MP_ROM_PTR(&ui_glyph_names_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph_id), MP_ROM_PTR(&ui_glyph_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_icon_mask), MP_ROM_PTR(&ui_icon_mask_obj) },
    { MP_ROM_QSTR(MP_QSTR_icons_use), MP_ROM_PTR(&ui_icons_use_obj) },
    { MP_ROM_QSTR(MP_QSTR_moy_use), MP_ROM_PTR(&ui_moy_use_obj) },
    { MP_ROM_QSTR(MP_QSTR_s), MP_ROM_PTR(&ui_s_obj) },
    { MP_ROM_QSTR(MP_QSTR_wasm_rows), MP_ROM_PTR(&ui_wasm_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_size), MP_ROM_PTR(&ui_api_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_begin), MP_ROM_PTR(&ui_api_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_page), MP_ROM_PTR(&ui_api_page_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_label), MP_ROM_PTR(&ui_api_label_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_button), MP_ROM_PTR(&ui_api_button_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_toggle), MP_ROM_PTR(&ui_api_toggle_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_group), MP_ROM_PTR(&ui_api_group_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_value), MP_ROM_PTR(&ui_api_value_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_choice), MP_ROM_PTR(&ui_api_choice_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_hints), MP_ROM_PTR(&ui_api_hints_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_hints_w), MP_ROM_PTR(&ui_api_hints_w_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_slider), MP_ROM_PTR(&ui_api_slider_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_icon), MP_ROM_PTR(&ui_api_icon_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_box), MP_ROM_PTR(&ui_api_box_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_progress), MP_ROM_PTR(&ui_api_progress_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_list), MP_ROM_PTR(&ui_api_list_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_part), MP_ROM_PTR(&ui_api_part_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_grid), MP_ROM_PTR(&ui_api_grid_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_card), MP_ROM_PTR(&ui_api_card_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_table), MP_ROM_PTR(&ui_api_table_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_scroll), MP_ROM_PTR(&ui_api_scroll_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_field), MP_ROM_PTR(&ui_api_field_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_dialog), MP_ROM_PTR(&ui_api_dialog_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_wash), MP_ROM_PTR(&ui_api_wash_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_wash_box), MP_ROM_PTR(&ui_api_wash_box_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_owes), MP_ROM_PTR(&ui_api_owes_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_moved), MP_ROM_PTR(&ui_api_moved_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_moving), MP_ROM_PTR(&ui_api_moving_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_busy), MP_ROM_PTR(&ui_api_busy_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_offset), MP_ROM_PTR(&ui_api_offset_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_fades), MP_ROM_PTR(&ui_api_fades_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_end), MP_ROM_PTR(&ui_api_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_table_rows), MP_ROM_PTR(&ui_api_table_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_table_pics), MP_ROM_PTR(&ui_api_table_pics_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_table_part), MP_ROM_PTR(&ui_api_table_part_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_popover), MP_ROM_PTR(&ui_api_popover_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_menu), MP_ROM_PTR(&ui_api_menu_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_menu_danger), MP_ROM_PTR(&ui_api_menu_danger_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_tool), MP_ROM_PTR(&ui_api_tool_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_bar), MP_ROM_PTR(&ui_api_bar_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_bar_lent), MP_ROM_PTR(&ui_api_bar_lent_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_bar_tap), MP_ROM_PTR(&ui_api_bar_tap_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_rule), MP_ROM_PTR(&ui_api_rule_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_text), MP_ROM_PTR(&ui_api_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_card_mark), MP_ROM_PTR(&ui_api_card_mark_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_grid_spread), MP_ROM_PTR(&ui_api_grid_spread_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_group_card), MP_ROM_PTR(&ui_api_group_card_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_empty), MP_ROM_PTR(&ui_api_empty_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_month), MP_ROM_PTR(&ui_api_month_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_toast), MP_ROM_PTR(&ui_api_toast_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_focus), MP_ROM_PTR(&ui_api_focus_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_focus_ahead), MP_ROM_PTR(&ui_api_focus_ahead_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_shift_plan), MP_ROM_PTR(&ui_api_shift_plan_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_shift_arm), MP_ROM_PTR(&ui_api_shift_arm_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_shift_rows), MP_ROM_PTR(&ui_api_shift_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_floor_take), MP_ROM_PTR(&ui_api_floor_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_shift_break), MP_ROM_PTR(&ui_api_shift_break_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_shift_drawn), MP_ROM_PTR(&ui_api_shift_drawn_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_band), MP_ROM_PTR(&ui_api_band_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_floor_box), MP_ROM_PTR(&ui_api_floor_box_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_lag_note), MP_ROM_PTR(&ui_api_lag_note_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_lag), MP_ROM_PTR(&ui_api_lag_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_host_begin), MP_ROM_PTR(&ui_api_host_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_host_arm), MP_ROM_PTR(&ui_api_host_arm_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_host_rows), MP_ROM_PTR(&ui_api_host_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_host_whole), MP_ROM_PTR(&ui_api_host_whole_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_pop_box), MP_ROM_PTR(&ui_api_pop_box_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_text_view), MP_ROM_PTR(&ui_api_text_view_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_text_at), MP_ROM_PTR(&ui_api_text_at_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_text_cover), MP_ROM_PTR(&ui_api_text_cover_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_text_aim), MP_ROM_PTR(&ui_api_text_aim_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_tones), MP_ROM_PTR(&ui_text_tones_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_words), MP_ROM_PTR(&ui_text_words_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_runs), MP_ROM_PTR(&ui_text_runs_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_floor_into), MP_ROM_PTR(&ui_api_floor_into_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_card_clip), MP_ROM_PTR(&ui_api_card_clip_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_keep), MP_ROM_PTR(&ui_api_keep_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_audit), MP_ROM_PTR(&ui_api_audit_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_keys_taken), MP_ROM_PTR(&ui_api_keys_taken_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_edge), MP_ROM_PTR(&ui_api_edge_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_tap_armed), MP_ROM_PTR(&ui_api_tap_armed_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_at), MP_ROM_PTR(&ui_api_at_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_dup), MP_ROM_PTR(&ui_api_dup_obj) },
    { MP_ROM_QSTR(MP_QSTR_api_lost), MP_ROM_PTR(&ui_api_lost_obj) },
    { MP_ROM_QSTR(MP_QSTR_str_set), MP_ROM_PTR(&ui_str_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_str_reset), MP_ROM_PTR(&ui_str_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph), MP_ROM_PTR(&ui_glyph_obj) },
    { MP_ROM_QSTR(MP_QSTR_row), MP_ROM_PTR(&ui_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_cell), MP_ROM_PTR(&ui_cell_obj) },
    { MP_ROM_QSTR(MP_QSTR_label), MP_ROM_PTR(&ui_label_obj) },
    { MP_ROM_QSTR(MP_QSTR_bar_rect), MP_ROM_PTR(&ui_bar_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_scrollbar), MP_ROM_PTR(&ui_scrollbar_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_cues), MP_ROM_PTR(&ui_scroll_cues_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_uncovered), MP_ROM_PTR(&ui_fill_uncovered_obj) },
    { MP_ROM_QSTR(MP_QSTR_fade), MP_ROM_PTR(&ui_fade_obj) },
    { MP_ROM_QSTR(MP_QSTR_list), MP_ROM_PTR(&ui_list_obj) },
    { MP_ROM_QSTR(MP_QSTR_calls), MP_ROM_PTR(&ui_calls_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_stop), MP_ROM_PTR(&ui_scroll_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_drag_start), MP_ROM_PTR(&ui_scroll_drag_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_drag_move), MP_ROM_PTR(&ui_scroll_drag_move_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_drag_end), MP_ROM_PTR(&ui_scroll_drag_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_tick), MP_ROM_PTR(&ui_scroll_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_glide_to), MP_ROM_PTR(&ui_scroll_glide_to_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_fling), MP_ROM_PTR(&ui_scroll_fling_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_step), MP_ROM_PTR(&ui_scroll_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_land), MP_ROM_PTR(&ui_scroll_land_obj) },
    { MP_ROM_QSTR(MP_QSTR_dragtap), MP_ROM_PTR(&ui_dragtap_obj) },
    { MP_ROM_QSTR(MP_QSTR_button), MP_ROM_PTR(&ui_button_obj) },
    { MP_ROM_QSTR(MP_QSTR_button_x), MP_ROM_PTR(&ui_button_x_obj) },
    { MP_ROM_QSTR(MP_QSTR_chip), MP_ROM_PTR(&ui_chip_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab_rects), MP_ROM_PTR(&ui_tab_rects_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab), MP_ROM_PTR(&ui_tab_obj) },
    { MP_ROM_QSTR(MP_QSTR_hints), MP_ROM_PTR(&ui_hints_obj) },
    { MP_ROM_QSTR(MP_QSTR_input), MP_ROM_PTR(&ui_input_obj) },
    { MP_ROM_QSTR(MP_QSTR_hint_input), MP_ROM_PTR(&ui_hint_input_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&ui_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_game_btn), MP_ROM_PTR(&ui_game_btn_obj) },
    { MP_ROM_QSTR(MP_QSTR_game_icon_btn), MP_ROM_PTR(&ui_game_icon_btn_obj) },
    { MP_ROM_QSTR(MP_QSTR_mini_btn), MP_ROM_PTR(&ui_mini_btn_obj) },
    { MP_ROM_QSTR(MP_QSTR_toolbar), MP_ROM_PTR(&ui_toolbar_obj) },
    { MP_ROM_QSTR(MP_QSTR_paint), MP_ROM_PTR(&ui_paint_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame), MP_ROM_PTR(&ui_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_buttons), MP_ROM_PTR(&ui_frame_buttons_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_icon), MP_ROM_PTR(&ui_frame_icon_obj) },
    { MP_ROM_QSTR(MP_QSTR_bar_part), MP_ROM_PTR(&ui_bar_part_obj) },
    { MP_ROM_QSTR(MP_QSTR_scrim), MP_ROM_PTR(&ui_scrim_obj) },
    { MP_ROM_QSTR(MP_QSTR_tile), MP_ROM_PTR(&ui_tile_obj) },
    { MP_ROM_QSTR(MP_QSTR_unlock), MP_ROM_PTR(&ui_unlock_obj) },
    { MP_ROM_QSTR(MP_QSTR_modal), MP_ROM_PTR(&ui_modal_obj) },
    { MP_ROM_QSTR(MP_QSTR_month_h), MP_ROM_PTR(&ui_month_h_obj) },
    { MP_ROM_QSTR(MP_QSTR_modal_band), MP_ROM_PTR(&ui_modal_band_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf), MP_ROM_PTR(&ui_perf_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_band), MP_ROM_PTR(&ui_frame_band_obj) },
    { MP_ROM_QSTR(MP_QSTR_dialog), MP_ROM_PTR(&ui_dialog_obj) },
    { MP_ROM_QSTR(MP_QSTR_text_field), MP_ROM_PTR(&ui_text_field_obj) },
    { MP_ROM_QSTR(MP_QSTR_panel), MP_ROM_PTR(&ui_panel_obj) },
    { MP_ROM_QSTR(MP_QSTR_focus), MP_ROM_PTR(&ui_focus_obj) },
    { MP_ROM_QSTR(MP_QSTR_current), MP_ROM_PTR(&ui_current_obj) },
    { MP_ROM_QSTR(MP_QSTR_marks), MP_ROM_PTR(&ui_marks_obj) },
    { MP_ROM_QSTR(MP_QSTR_mark_reach), MP_ROM_PTR(&ui_mark_reach_obj) },
    { MP_ROM_QSTR(MP_QSTR_inset), MP_ROM_PTR(&ui_inset_obj) },
    { MP_ROM_QSTR(MP_QSTR_cut), MP_ROM_PTR(&ui_cut_obj) },
    { MP_ROM_QSTR(MP_QSTR_split), MP_ROM_PTR(&ui_split_obj) },
    { MP_ROM_QSTR(MP_QSTR_row_content), MP_ROM_PTR(&ui_row_content_obj) },
    { MP_ROM_QSTR(MP_QSTR_cell_art), MP_ROM_PTR(&ui_cell_art_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ui_globals, moy_ui_globals_table);

const mp_obj_module_t moy_ui_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ui_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ui, moy_ui_user_cmodule);
