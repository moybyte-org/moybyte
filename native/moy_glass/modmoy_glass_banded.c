// moy_glass's binding of the banded compositor (moy_present_banded.c):
// BandedCompositor over the panel module's transport, and its game fold.

#include "modmoy_glass_int.h"

#if MOY_GLASS_PRESENT

// -- present: the banded compositor (moy_present.h) -------------------------------
//
// BandedCompositor(panel, nfbs=2, async_flush=True): the T-Deck's and the
// Guition S3's compositor -- the backend contract docs/surface_model_v1.md §4
// pins (size, framebuffer, back_buffer, gfx, flush, sync) with the frame state
// machine in C over the panel module's kernel transport (MOY_KERNEL_PANEL's
// kwait/kkick/kship). `panel` is the board's panel module (moy_lcd, moy_axs):
// its own verbs are what the meters and the game fold reach, and a fold verb
// the panel lacks is ABSENT here too, never a stub (a board without a lever
// reports None, the console's probes are getattr).

#ifdef MOY_GLASS_BANDED_TYPE

#if MOY_GLASS_BANDED
// A board: the panel module's kernel entries (modmoy_glass_int.h), called
// straight.

static bool banded_wait(void *ctx) {
    (void)ctx;
    return MOY_KERNEL_PANEL(wait)();
}

static int banded_kick(void *ctx, int n) {
    (void)ctx;
    return MOY_KERNEL_PANEL(kick)(n);
}

static int banded_ship(void *ctx, int n) {
    (void)ctx;
    return MOY_KERNEL_PANEL(ship)(n);
}

void glass_banded_light(mp_obj_t panel, bool on) {
    (void)panel;
    MOY_KERNEL_PANEL(backlight)(on ? MOY_LIGHT_FULL : 0);
}
#else
// The desktop MicroPython: the panel module is whatever was passed in (a
// test's double), reached through its verbs -- drain(), kick(n), show(n),
// backlight(on) -- which raise their own errors.
static bool banded_wait(void *ctx) {
    return mp_obj_is_true(mp_call_function_0(
        mp_load_attr(MP_OBJ_FROM_PTR(ctx), MP_QSTR_drain)));
}

static int banded_kick(void *ctx, int n) {
    mp_call_function_1(mp_load_attr(MP_OBJ_FROM_PTR(ctx), MP_QSTR_kick),
                       MP_OBJ_NEW_SMALL_INT(n));
    return 0;
}

static int banded_ship(void *ctx, int n) {
    mp_call_function_1(mp_load_attr(MP_OBJ_FROM_PTR(ctx), MP_QSTR_show),
                       MP_OBJ_NEW_SMALL_INT(n));
    return 0;
}

void glass_banded_light(mp_obj_t panel, bool on) {
    mp_call_function_1(mp_load_attr(panel, MP_QSTR_backlight), mp_obj_new_bool(on));
}
#endif

static mp_obj_t banded_make_new(const mp_obj_type_t *type, size_t n_args,
                                size_t n_kw, const mp_obj_t *args) {
    enum { ARG_panel, ARG_nfbs, ARG_async };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_panel, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_nfbs, MP_ARG_INT, { .u_int = 2 } },
        { MP_QSTR_async_flush, MP_ARG_OBJ, { .u_obj = mp_const_true } },
    };
    mp_arg_val_t a[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all_kw_array(n_args, n_kw, args, MP_ARRAY_SIZE(allowed), allowed, a);
    mp_obj_t panel = a[ARG_panel].u_obj;
    // Dark until the first composed frame: a freshly powered panel's GRAM is
    // noise, so every panel module's init leaves the backlight off; the boot
    // lights it after the first present.
    mp_obj_t init = mp_load_attr(panel, MP_QSTR_init);
    mp_obj_t kw[2] = { MP_OBJ_NEW_QSTR(MP_QSTR_nfbs),
                       MP_OBJ_NEW_SMALL_INT(a[ARG_nfbs].u_int) };
    mp_call_function_n_kw(init, 0, 1, kw);
    glass_banded_obj_t *c = mp_obj_malloc(glass_banded_obj_t, type);
    c->panel = panel;
    c->w = mp_obj_get_int(mp_load_attr(panel, MP_QSTR_WIDTH));
    c->h = mp_obj_get_int(mp_load_attr(panel, MP_QSTR_HEIGHT));
    mp_int_t n = mp_obj_get_int(mp_call_function_0(mp_load_attr(panel, MP_QSTR_nfbs)));
    if (n < 1 || n > MOY_PRESENT_MAX_FBS) {
        mp_raise_ValueError(MP_ERROR_TEXT("panel framebuffers"));
    }
    // The memoryviews made once: back_buffer() is asked every frame.
    mp_obj_t fb = mp_load_attr(panel, MP_QSTR_fb);
    for (mp_int_t i = 0; i < n; i++) {
        c->fbs[i] = mp_call_function_1(fb, MP_OBJ_NEW_SMALL_INT(i));
    }
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        c->gfx = mp_import_name(MP_QSTR_moy_gfx, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
        nlr_pop();
    } else {
        c->gfx = mp_const_none;
    }
    mp_obj_t dest[2];
    mp_load_method_maybe(panel, MP_QSTR_kick, dest);
    c->ops.drain = banded_wait;
    c->ops.kick = banded_kick;
    c->ops.show = banded_ship;
    c->ops.ctx = MP_OBJ_TO_PTR(panel);
    moy_banded_init(&c->b, &c->ops, (int)n,
                    mp_obj_is_true(a[ARG_async].u_obj) && dest[0] != MP_OBJ_NULL);
    return MP_OBJ_FROM_PTR(c);
}

static MP_NORETURN void banded_raise(int e) {
    mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("panel flush: err 0x%x"),
                      (unsigned)e);
}

static mp_obj_t banded_flush(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    int e = moy_banded_present(&c->b);
    if (e != 0) {
        banded_raise(e);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_flush_obj, banded_flush);

static mp_obj_t banded_sync(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    moy_banded_fence(&c->b);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_sync_obj, banded_sync);

static mp_obj_t banded_none(mp_obj_t self_in) {
    (void)self_in;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_none_obj, banded_none);

static mp_obj_t banded_size(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(c->w), MP_OBJ_NEW_SMALL_INT(c->h) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_size_obj, banded_size);

static mp_obj_t banded_back(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    return c->fbs[c->b.back];
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_back_obj, banded_back);

static mp_obj_t banded_gfx(mp_obj_t self_in) {
    return ((glass_banded_obj_t *)MP_OBJ_TO_PTR(self_in))->gfx;
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_gfx_obj, banded_gfx);

static mp_obj_t banded_has_gfx(mp_obj_t self_in) {
    return mp_obj_new_bool(((glass_banded_obj_t *)MP_OBJ_TO_PTR(self_in))->gfx
                           != mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_has_gfx_obj, banded_has_gfx);

static mp_obj_t banded_set_backlight(size_t n_args, const mp_obj_t *a) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    glass_banded_light(c->panel, n_args < 2 || mp_obj_is_true(a[1]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(banded_set_backlight_obj, 1, 2,
                                           banded_set_backlight);

static mp_obj_t panel_call0(glass_banded_obj_t *c, qstr verb) {
    return mp_call_function_0(mp_load_attr(c->panel, verb));
}

// (flushes, last_flush_us): the wall span of the last frame's transfer.
static mp_obj_t banded_stats(mp_obj_t self_in) {
    return panel_call0(MP_OBJ_TO_PTR(self_in), MP_QSTR_stats);
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_stats_obj, banded_stats);

// The whole moy_flush pump tuple (pump_us, idle_us, idle_n, feed_us, bands,
// blocked_us, timeouts, errs, stop_fails); a serialized compositor has no
// feed to pace and reports feed_us's -1 "never measured".
static mp_obj_t banded_bounce_stats(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    if (!c->b.overlap) {
        mp_obj_t t[9];
        for (int i = 0; i < 9; i++) {
            t[i] = MP_OBJ_NEW_SMALL_INT(i == 3 ? -1 : 0);
        }
        return mp_obj_new_tuple(9, t);
    }
    mp_obj_t st = panel_call0(c, MP_QSTR_pump_stats);
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(st, &n, &items);
    return mp_obj_new_tuple(n < 9 ? n : 9, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_bounce_stats_obj, banded_bounce_stats);

// -- the game fold, where the panel module has it (moy_fold.h) ------------------

static mp_obj_t banded_disarm(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    return mp_call_function_1(mp_load_attr(c->panel, MP_QSTR_disarm_fold),
                              MP_OBJ_NEW_SMALL_INT(c->b.back));
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_disarm_obj, banded_disarm);

// A fold verb forwards to the panel's: (the compositor's name, the panel's).
static const qstr banded_fold[][2] = {
    { MP_QSTR_fold_fence, MP_QSTR_fold_fence },
    { MP_QSTR_snap_scale_fold, MP_QSTR_arm_fold_snap },
    { MP_QSTR_snap_fence, MP_QSTR_fold_snap_fence },
    { MP_QSTR_snap_stats, MP_QSTR_snap_stats },
    { MP_QSTR_frame_fold, MP_QSTR_arm_fold_frame },
    { MP_QSTR_sd_bracket, MP_QSTR_sd_guard },
};

static void banded_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    mp_obj_t probe[2];
    switch (attr) {
        case MP_QSTR_bounce_flush:
            dest[0] = mp_obj_new_bool(c->b.overlap);
            return;
        case MP_QSTR_pump_last_us:
            dest[0] = c->b.overlap
                ? mp_obj_subscr(panel_call0(c, MP_QSTR_pump_stats),
                                MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_SENTINEL)
                : MP_OBJ_NEW_SMALL_INT(0);
            return;
        case MP_QSTR__lcd:
            dest[0] = c->panel;
            return;
        case MP_QSTR__fbs:
            dest[0] = mp_obj_new_list(c->b.nfbs, c->fbs);
            return;
        case MP_QSTR__w:
            dest[0] = MP_OBJ_NEW_SMALL_INT(c->w);
            return;
        case MP_QSTR__h:
            dest[0] = MP_OBJ_NEW_SMALL_INT(c->h);
            return;
        case MP_QSTR__back:
            dest[0] = MP_OBJ_NEW_SMALL_INT(c->b.back);
            return;
        case MP_QSTR_fold_supported:
            mp_load_method_maybe(c->panel, MP_QSTR_arm_fold_snap, probe);
            dest[0] = mp_obj_new_bool(probe[0] != MP_OBJ_NULL);
            return;
        case MP_QSTR_frames_supported:
            mp_load_method_maybe(c->panel, MP_QSTR_arm_fold_frame, probe);
            dest[0] = mp_obj_new_bool(probe[0] != MP_OBJ_NULL);
            return;
        case MP_QSTR_fold_count:
            mp_load_method_maybe(c->panel, MP_QSTR_fold_stats, probe);
            if (probe[0] != MP_OBJ_NULL) {
                dest[0] = mp_obj_subscr(mp_call_method_n_kw(0, 0, probe),
                                        MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_SENTINEL);
            }
            return;
        case MP_QSTR_frame_fold_count:
            mp_load_method_maybe(c->panel, MP_QSTR_frame_arms, probe);
            if (probe[0] != MP_OBJ_NULL) {
                dest[0] = mp_call_method_n_kw(0, 0, probe);
            }
            return;
        case MP_QSTR_disarm_scale_fold:
            mp_load_method_maybe(c->panel, MP_QSTR_disarm_fold, probe);
            if (probe[0] != MP_OBJ_NULL) {
                dest[0] = MP_OBJ_FROM_PTR(&banded_disarm_obj);
                dest[1] = self_in;
            }
            return;
        default:
            break;
    }
    for (size_t i = 0; i < MP_ARRAY_SIZE(banded_fold); i++) {
        if (banded_fold[i][0] == attr) {
            mp_load_method_maybe(c->panel, banded_fold[i][1], probe);
            if (probe[0] != MP_OBJ_NULL) {
                dest[0] = probe[1] == MP_OBJ_NULL ? probe[0]
                        : mp_obj_new_bound_meth(probe[0], probe[1]);
            }
            return;
        }
    }
    dest[1] = MP_OBJ_SENTINEL;          // the locals dict
}

// copy_last(x, y, w, h) -> True once that rect of the buffer the last frame
// drew into is in the one this frame draws into: a row memcpy, the feeder
// reading the same buffer alongside. With one buffer the last frame's is the
// drawn-into one: True, nothing copied. False before a frame was presented.
static mp_obj_t banded_copy_last(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_int_t r[4] = { mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                      mp_obj_get_int(a[3]), mp_obj_get_int(a[4]) };
    int l = moy_banded_last(&c->b);
    if (l < 0) {
        return mp_const_false;
    }
    if (l != c->b.back) {
        glass_copy(mp_const_none, c->fbs[c->b.back], c->fbs[l], c->w, c->h, r);
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(banded_copy_last_obj, 5, 5, banded_copy_last);

// shift_last / copy_last_into (above): a row copy on the CPU, the feeder
// reading the last frame's buffer alongside.
static mp_obj_t banded_shift_last(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    int l = moy_banded_last(&c->b);
    if (l < 0) {
        return mp_const_false;
    }
    bool fly = false;
    int b[6];
    glass_shift(mp_const_none, c->fbs[c->b.back], c->fbs[l], c->w, c->h, a + 1, &fly, b);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(banded_shift_last_obj, 7, 7, banded_shift_last);

static mp_obj_t banded_copy_last_into(size_t n_args, const mp_obj_t *a) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    int l = moy_banded_last(&c->b);
    if (l < 0) {
        return mp_const_false;
    }
    glass_copy_into(mp_const_none, c->fbs[l], c->w, c->h, n_args - 1, a + 1);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(banded_copy_last_into_obj, 7, 9,
                                           banded_copy_last_into);

// last_buffer() -> the buffer the last presented frame drew into, where it
// is not the one this frame draws into; None with one buffer or before a
// frame was presented. A pass that reads it (a wash over the last frame)
// reads alongside the feeder.
static mp_obj_t banded_last_buffer(mp_obj_t self_in) {
    glass_banded_obj_t *c = MP_OBJ_TO_PTR(self_in);
    int l = moy_banded_last(&c->b);
    return l < 0 || l == c->b.back ? mp_const_none : c->fbs[l];
}
static MP_DEFINE_CONST_FUN_OBJ_1(banded_last_buffer_obj, banded_last_buffer);

static const mp_rom_map_elem_t banded_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_size), MP_ROM_PTR(&banded_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_framebuffer), MP_ROM_PTR(&banded_back_obj) },
    { MP_ROM_QSTR(MP_QSTR_back_buffer), MP_ROM_PTR(&banded_back_obj) },
    { MP_ROM_QSTR(MP_QSTR_gfx), MP_ROM_PTR(&banded_gfx_obj) },
    { MP_ROM_QSTR(MP_QSTR_has_gfx), MP_ROM_PTR(&banded_has_gfx_obj) },
    { MP_ROM_QSTR(MP_QSTR_flush), MP_ROM_PTR(&banded_flush_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&banded_sync_obj) },
    { MP_ROM_QSTR(MP_QSTR_present_pending), MP_ROM_PTR(&banded_none_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_backlight), MP_ROM_PTR(&banded_set_backlight_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&banded_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_bounce_stats), MP_ROM_PTR(&banded_bounce_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_last), MP_ROM_PTR(&banded_copy_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_shift_last), MP_ROM_PTR(&banded_shift_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_last_into), MP_ROM_PTR(&banded_copy_last_into_obj) },
    { MP_ROM_QSTR(MP_QSTR_last_buffer), MP_ROM_PTR(&banded_last_buffer_obj) },
};
static MP_DEFINE_CONST_DICT(banded_locals, banded_locals_table);

MP_DEFINE_CONST_OBJ_TYPE(
    glass_banded_type, MP_QSTR_BandedCompositor, MP_TYPE_FLAG_NONE,
    make_new, banded_make_new,
    attr, banded_attr,
    locals_dict, &banded_locals
    );

#endif // MOY_GLASS_BANDED_TYPE

#endif // MOY_GLASS_PRESENT
