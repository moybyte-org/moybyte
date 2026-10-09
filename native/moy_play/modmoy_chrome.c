// moy_play's chrome functions (moy_chrome.h) as a VM sees them; runtime/
// moy_play.py has the same names on CPython. Each piece answers its display
// list: [(op, c, scale, x, y, w, h, text)], which the shell replays through
// its canvas (runtime/ui.py's replay).
//
//   chrome_inks(inks, bar_light, rings)
//   chrome_pill(cw, ch, held_ms, hold_ms)
//   chrome_panel(cw, ch, notice, title, text, compiled)
//   chrome_toast(title, glyph)
//   chrome_banner(lw, fs, status_h, title, sub, ok)
//   chrome_strip_crash(cw, edit, clock, wifi), chrome_strip_band(cw, bar_h,
//   light), chrome_strip_right(lay|None, clock, wifi, show_x),
//   chrome_strip_title(title, zx, zw, dy)
//   chrome_menu(rows, sel, x, y, w, h, fs, cs), chrome_about(cw, ch, fs, ver)
//   chrome_glyph(kind, rows), chrome_notice(title, sub, ok, until_ms),
//   chrome_toast_arm(title, glyph, until_ms)

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_chrome.h"
#include "moy_loop.h"

// One list, reused (a VM's chrome draws one piece at a time), from the
// kernel's allocator: it outlives every VM, so no soft reset can leave a
// pointer to it in a swept heap.
static moy_chrome_list_t *g_list;

static moy_chrome_list_t *list(void) {
    if (g_list == NULL) {
        g_list = moy_loop_alloc(sizeof(moy_chrome_list_t));
        if (g_list == NULL) {
            mp_raise_type(&mp_type_MemoryError);
        }
    }
    moy_chrome_clear(g_list);
    return g_list;
}

static mp_obj_t ops(const moy_chrome_list_t *l) {
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (uint16_t i = 0; i < l->n; i++) {
        const moy_chrome_op_t *o = &l->op[i];
        mp_obj_t t[8] = {
            MP_OBJ_NEW_SMALL_INT(o->op), MP_OBJ_NEW_SMALL_INT(o->c),
            MP_OBJ_NEW_SMALL_INT(o->scale), MP_OBJ_NEW_SMALL_INT(o->x),
            MP_OBJ_NEW_SMALL_INT(o->y), MP_OBJ_NEW_SMALL_INT(o->w),
            MP_OBJ_NEW_SMALL_INT(o->h), mp_obj_new_str(l->text + o->s, o->n),
        };
        mp_obj_list_append(out, mp_obj_new_tuple(8, t));
    }
    return out;
}

static const char *s_of(mp_obj_t o) {
    return o == mp_const_none ? "" : mp_obj_str_get_str(o);
}

static mp_obj_t ch_inks(mp_obj_t inks, mp_obj_t light, mp_obj_t rings) {
    int16_t v[MOY_INKS];
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(inks, &n, &items);
    for (size_t i = 0; i < MOY_INKS; i++) {
        v[i] = (int16_t)(i < n && items[i] != mp_const_none ? mp_obj_get_int(items[i]) : -1);
    }
    moy_chrome_set_inks(v, mp_obj_is_true(light), mp_obj_get_int(rings));
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_3(moy_chrome_inks_obj, ch_inks);

static mp_obj_t ch_pill(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_pill(l, mp_obj_get_int(a[0]), mp_obj_get_int(a[1]),
                    (uint32_t)mp_obj_get_int(a[2]), (uint32_t)mp_obj_get_int(a[3]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_pill_obj, 4, 4, ch_pill);

static mp_obj_t ch_panel(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_panel(l, mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_is_true(a[2]),
                     s_of(a[3]), s_of(a[4]), mp_obj_is_true(a[5]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_panel_obj, 6, 6, ch_panel);

static mp_obj_t ch_toast(mp_obj_t title, mp_obj_t glyph) {
    moy_chrome_list_t *l = list();
    moy_chrome_toast(l, s_of(title), s_of(glyph));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_2(moy_chrome_toast_obj, ch_toast);

static mp_obj_t ch_banner(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_banner(l, mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                      s_of(a[3]), s_of(a[4]), mp_obj_is_true(a[5]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_banner_obj, 6, 6, ch_banner);

static mp_obj_t ch_crash(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_strip_crash(l, mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), s_of(a[2]),
                           s_of(a[3]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_crash_obj, 4, 4, ch_crash);

static mp_obj_t ch_band(mp_obj_t cw, mp_obj_t bar_h, mp_obj_t light) {
    moy_chrome_list_t *l = list();
    moy_chrome_strip_band(l, mp_obj_get_int(cw), mp_obj_get_int(bar_h), mp_obj_is_true(light));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_3(moy_chrome_band_obj, ch_band);

static void rect4(mp_obj_t r, int16_t out[4]) {
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(r, &n, &items);
    for (size_t i = 0; i < 4; i++) {
        out[i] = (int16_t)(i < n ? mp_obj_get_int(items[i]) : 0);
    }
}

static mp_obj_t ch_right(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_lay_t lay;
    const moy_chrome_lay_t *lp = NULL;
    if (a[0] != mp_const_none) {
        mp_obj_t *it;
        mp_obj_get_array_fixed_n(a[0], 7, &it);
        lay.clock_x = (int16_t)mp_obj_get_int(it[0]);
        lay.text_dy = (int16_t)mp_obj_get_int(it[1]);
        lay.cs = (int16_t)mp_obj_get_int(it[2]);
        rect4(it[3], lay.wifi);
        rect4(it[4], lay.batt);
        rect4(it[5], lay.menu);
        rect4(it[6], lay.close);
        lp = &lay;
    }
    moy_chrome_strip_right(l, lp, s_of(a[1]), s_of(a[2]), mp_obj_is_true(a[3]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_right_obj, 4, 4, ch_right);

static mp_obj_t ch_title(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_strip_title(l, s_of(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                           mp_obj_get_int(a[3]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_title_obj, 4, 4, ch_title);

static mp_obj_t ch_menu(size_t n, const mp_obj_t *a) {
    (void)n;
    size_t rn;
    mp_obj_t *rows;
    mp_obj_get_array(a[0], &rn, &rows);
    if (rn > MOY_MENU_ROWS) {
        rn = MOY_MENU_ROWS;
    }
    uint8_t kind[MOY_MENU_ROWS];
    const char *label[MOY_MENU_ROWS];
    for (size_t i = 0; i < rn; i++) {
        size_t k;
        mp_obj_t *it;
        mp_obj_get_array(rows[i], &k, &it);
        kind[i] = (uint8_t)(k > 0 ? mp_obj_get_int(it[0]) : 0);
        label[i] = k > 1 ? s_of(it[1]) : "";
    }
    moy_chrome_list_t *l = list();
    moy_chrome_menu(l, kind, label, (int)rn, mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                    mp_obj_get_int(a[3]), mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                    mp_obj_get_int(a[6]), mp_obj_get_int(a[7]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_menu_obj, 8, 8, ch_menu);

static mp_obj_t ch_about(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_about(l, mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                     s_of(a[3]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_about_obj, 4, 4, ch_about);

static mp_obj_t ch_glyph(mp_obj_t kind, mp_obj_t rows) {
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(rows, &n, &it);
    uint16_t r[12] = {0};
    for (size_t i = 0; i < 12 && i < n; i++) {
        r[i] = (uint16_t)(mp_obj_get_int(it[i]) & 0xFFF);
    }
    return mp_obj_new_bool(moy_chrome_glyph_put(mp_obj_str_get_str(kind), r) == 0);
}
MP_DEFINE_CONST_FUN_OBJ_2(moy_chrome_glyph_obj, ch_glyph);

static mp_obj_t ch_notice(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_notice(s_of(a[0]), s_of(a[1]), mp_obj_is_true(a[2]),
                      (uint32_t)mp_obj_get_int_truncated(a[3]));
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_notice_obj, 4, 4, ch_notice);

static mp_obj_t ch_toast_arm(mp_obj_t title, mp_obj_t glyph, mp_obj_t until) {
    moy_chrome_toast_arm(s_of(title), s_of(glyph), (uint32_t)mp_obj_get_int_truncated(until));
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_3(moy_chrome_toast_arm_obj, ch_toast_arm);
