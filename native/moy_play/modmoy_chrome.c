// moy_play's chrome functions (moy_chrome.h) as a VM sees them; runtime/
// moy_play.py has the same names on CPython. Each piece answers its display
// list: [(op, c, scale, x, y, w, h, text)], which the shell replays through
// its canvas (runtime/ui.py's replay).
//
//   chrome_inks(inks, bar_light, rings)
//   chrome_panel(cw, ch, notice, title, text)
//   chrome_say_title(say, line), chrome_crash_title(text, line|None),
//   chrome_fit_text(title, total, block, free, largest),
//   chrome_newer_text(missing): the panels' words (#143)
//   chrome_strip_band(cw, bar_h,
//   light), chrome_strip_cell(kind, rect, cs, dy, s|None, across)
//   chrome_menu(rows, sel, x, y, w, h, fs, cs)
//   chrome_notice(title, sub, ok, until_ms),
//   chrome_toast_arm(head, name, icon, secret, until_ms): the banners a run the
//   kernel drives shows over its frames

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

static mp_obj_t ch_panel(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    moy_chrome_panel(l, mp_obj_get_int(a[0]), mp_obj_get_int(a[1]), mp_obj_is_true(a[2]),
                     s_of(a[3]), s_of(a[4]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_panel_obj, 5, 5, ch_panel);

// The panels' words (#143), as strings.
static mp_obj_t say_str(const char *b, int len) {
    size_t n = len < 0 ? 0u : (size_t)len;
    return mp_obj_new_str(b, n);
}

static mp_obj_t ch_say_title(mp_obj_t say, mp_obj_t line) {
    char b[64];
    int n = moy_chrome_title(b, sizeof(b), mp_obj_get_int(say), mp_obj_get_int(line));
    return say_str(b, n < (int)sizeof(b) ? n : (int)sizeof(b) - 1);
}
MP_DEFINE_CONST_FUN_OBJ_2(moy_chrome_say_title_obj, ch_say_title);

static mp_obj_t ch_crash_title(mp_obj_t text, mp_obj_t line) {
    char b[64];
    int n = moy_chrome_crash_title(b, sizeof(b), s_of(text), mp_obj_get_int(line));
    return say_str(b, n < (int)sizeof(b) ? n : (int)sizeof(b) - 1);
}
MP_DEFINE_CONST_FUN_OBJ_2(moy_chrome_crash_title_obj, ch_crash_title);

static mp_obj_t ch_fit_text(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    char b[320];
    int n = moy_chrome_fit_text(b, sizeof(b), s_of(a[0]), (uint32_t)mp_obj_get_int(a[1]),
                                (uint32_t)mp_obj_get_int(a[2]), (uint32_t)mp_obj_get_int(a[3]),
                                (uint32_t)mp_obj_get_int(a[4]));
    return say_str(b, n < (int)sizeof(b) ? n : (int)sizeof(b) - 1);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_fit_text_obj, 5, 5, ch_fit_text);

static mp_obj_t ch_newer_text(mp_obj_t missing) {
    char b[320];
    int n = moy_chrome_newer_text(b, sizeof(b), s_of(missing));
    return say_str(b, n < (int)sizeof(b) ? n : (int)sizeof(b) - 1);
}
MP_DEFINE_CONST_FUN_OBJ_1(moy_chrome_newer_text_obj, ch_newer_text);

static mp_obj_t ch_band(mp_obj_t cw, mp_obj_t bar_h, mp_obj_t light) {
    moy_chrome_list_t *l = list();
    moy_chrome_strip_band(l, mp_obj_get_int(cw), mp_obj_get_int(bar_h), mp_obj_is_true(light));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_3(moy_chrome_band_obj, ch_band);

static mp_obj_t ch_cell(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_list_t *l = list();
    mp_obj_t *r;
    mp_obj_get_array_fixed_n(a[1], 4, &r);
    moy_chrome_strip_cell(l, mp_obj_str_get_str(a[0]), mp_obj_get_int(r[0]), mp_obj_get_int(r[1]),
                          mp_obj_get_int(r[2]), mp_obj_get_int(r[3]), mp_obj_get_int(a[2]),
                          mp_obj_get_int(a[3]), s_of(a[4]), mp_obj_is_true(a[5]));
    return ops(l);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_cell_obj, 6, 6, ch_cell);

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

static mp_obj_t ch_notice(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_notice(s_of(a[0]), s_of(a[1]), mp_obj_is_true(a[2]),
                      (uint32_t)mp_obj_get_int_truncated(a[3]));
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_notice_obj, 4, 4, ch_notice);

static mp_obj_t ch_toast_arm(size_t n, const mp_obj_t *a) {
    (void)n;
    moy_chrome_toast_arm(s_of(a[0]), s_of(a[1]), s_of(a[2]), mp_obj_is_true(a[3]),
                         (uint32_t)mp_obj_get_int_truncated(a[4]));
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_chrome_toast_arm_obj, 5, 5, ch_toast_arm);
