// moy_glass's binding of the DSI compositor (moy_present_dsi.c):
// DsiCompositor over moy_dsi's scan buffers and the PPA's fences.

#include "modmoy_glass_int.h"

#if MOY_GLASS_PRESENT

// -- present: the DSI compositor (moy_present.h) ------------------------------------
//
// DsiCompositor(dsi, ppa=None): the Waveshare P4's compositor -- the backend
// contract of docs/surface_model_v1.md §4 plus the deferred present -- with the
// frame state machine in C over moy_dsi's scan buffers and the PPA's fences,
// reached through the two modules passed in (the boards' moy_dsi and moy_ppa,
// a test's doubles on the desktop). The canvas's word that this frame's
// composite flies is `_composite_pending`, and a window stamp it defers is
// `_stamp_pending`: the arguments of moy_ppa.blit_async, kicked here, as the
// frame's first op, at the next present.

mp_obj_t glass_dsi_call0(mp_obj_t mod, qstr verb) {
    return mp_call_function_0(mp_load_attr(mod, verb));
}

static void dsi_show(void *ctx, int n) {
    glass_dsi_obj_t *c = ctx;
    mp_call_function_1(mp_load_attr(c->dsi, MP_QSTR_show), MP_OBJ_NEW_SMALL_INT(n));
}

static void dsi_msync(void *ctx) {
    glass_dsi_call0(((glass_dsi_obj_t *)ctx)->dsi, MP_QSTR_flush);
}

static void dsi_ppa_sync(void *ctx) {
    glass_dsi_obj_t *c = ctx;
    if (c->ppa != mp_const_none) {
        glass_dsi_call0(c->ppa, MP_QSTR_sync);
    }
}

static bool dsi_ppa_done(void *ctx) {
    glass_dsi_obj_t *c = ctx;
    mp_obj_t dest[2];
    if (c->ppa == mp_const_none) {
        return true;
    }
    mp_load_method_maybe(c->ppa, MP_QSTR_done, dest);
    return dest[0] == MP_OBJ_NULL || mp_obj_is_true(mp_call_method_n_kw(0, 0, dest));
}

static void dsi_snap_wait(void *ctx) {
    glass_dsi_obj_t *c = ctx;
    mp_obj_t dest[2];
    if (c->ppa == mp_const_none) {
        return;
    }
    mp_load_method_maybe(c->ppa, MP_QSTR_snap_wait, dest);
    if (dest[0] != MP_OBJ_NULL) {
        mp_call_method_n_kw(0, 0, dest);
    }
}

uint32_t glass_dsi_ticks(void *ctx) {
    (void)ctx;
    return (uint32_t)mp_hal_ticks_us();
}

static int32_t dsi_refreshes(void *ctx) {
    return (int32_t)mp_obj_get_int(mp_call_function_0(((glass_dsi_obj_t *)ctx)->refreshes));
}

// The kernel's first light (native/moy_kernel/moy_boot.h); none off a board.
#include "moy_boot.h"
#pragma weak moy_boot_lit

bool glass_kernel_lit(void) {
    return moy_boot_lit != NULL && moy_boot_lit();
}

static mp_obj_t dsi_make_new(const mp_obj_type_t *type, size_t n_args,
                             size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 1, 2, false);
    mp_obj_t dsi = args[0];
    glass_dsi_obj_t *c = mp_obj_malloc(glass_dsi_obj_t, type);
    c->shift_fly = false;
    c->dsi = dsi;
    c->ppa = n_args > 1 ? args[1] : mp_const_none;
    c->stamp = mp_const_none;
    // Dark until the first composed frame: a fresh panel scans noise. One the
    // kernel lit with the logo (moy_boot.h) scans the logo and stays lit.
    if (!glass_kernel_lit()) {
        mp_call_function_1(mp_load_attr(dsi, MP_QSTR_backlight), mp_const_false);
    }
    glass_dsi_call0(dsi, MP_QSTR_init);
    c->w = mp_obj_get_int(mp_load_attr(dsi, MP_QSTR_WIDTH));
    c->h = mp_obj_get_int(mp_load_attr(dsi, MP_QSTR_HEIGHT));
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        c->gfx = mp_import_name(MP_QSTR_moy_gfx, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
        nlr_pop();
    } else {
        c->gfx = mp_const_none;
    }
    mp_obj_t dest[2];
    mp_int_t n = 1;
    mp_load_method_maybe(dsi, MP_QSTR_nfbs, dest);
    if (dest[0] != MP_OBJ_NULL) {
        n = mp_obj_get_int(mp_call_method_n_kw(0, 0, dest));
    }
    if (n < 1 || n > MOY_PRESENT_MAX_FBS) {
        mp_raise_ValueError(MP_ERROR_TEXT("panel framebuffers"));
    }
    c->fbs = mp_obj_new_list(0, NULL);
    mp_obj_t fb = mp_load_attr(dsi, MP_QSTR_fb);
    for (mp_int_t i = 0; i < n; i++) {
        mp_obj_t v = n > 1 ? mp_call_function_1(fb, MP_OBJ_NEW_SMALL_INT(i))
                           : mp_call_function_0(fb);
        mp_obj_list_append(c->fbs, v);
        if (c->gfx != mp_const_none) {
            mp_obj_t a[3] = { v, MP_OBJ_NEW_SMALL_INT(c->w * c->h), MP_OBJ_NEW_SMALL_INT(0) };
            mp_call_function_n_kw(mp_load_attr(c->gfx, MP_QSTR_fill), 3, 0, a);
        }
    }
    c->ops.show = dsi_show;
    c->ops.msync = dsi_msync;
    c->ops.ppa_sync = dsi_ppa_sync;
    c->ops.ppa_done = dsi_ppa_done;
    c->ops.snap_wait = dsi_snap_wait;
    c->ops.ticks_us = glass_dsi_ticks;
    mp_load_method_maybe(dsi, MP_QSTR_refreshes, dest);
    c->refreshes = dest[0] == MP_OBJ_NULL ? mp_const_none
                 : (dest[1] == MP_OBJ_NULL ? dest[0] : mp_obj_new_bound_meth(dest[0], dest[1]));
    c->ops.refreshes = c->refreshes == mp_const_none ? NULL : dsi_refreshes;
    c->ops.ctx = c;
    moy_dsi_init(&c->d, &c->ops, (int)n);
    return MP_OBJ_FROM_PTR(c);
}

static mp_obj_t dsi_back(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    return mp_obj_subscr(c->fbs, MP_OBJ_NEW_SMALL_INT(c->d.back), MP_OBJ_SENTINEL);
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_back_obj, dsi_back);

static mp_obj_t dsi_size(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(c->w), MP_OBJ_NEW_SMALL_INT(c->h) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_size_obj, dsi_size);

static mp_obj_t dsi_gfx(mp_obj_t self_in) {
    return ((glass_dsi_obj_t *)MP_OBJ_TO_PTR(self_in))->gfx;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_gfx_obj, dsi_gfx);

// flush: show the queued frames whose composites have landed, kick the
// deferred window stamp as the frame's first op (a refused kick draws it on
// the CPU instead -- worst case one stale frame), then the engine's present.
static mp_obj_t dsi_flush(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    bool kicked = false;
    glass_shift_fence(c->ppa, &c->shift_fly);
    moy_dsi_show_landed(&c->d);
    if (c->stamp != mp_const_none) {
        mp_obj_t st = c->stamp;
        c->stamp = mp_const_none;
        size_t n;
        mp_obj_t *items;
        mp_obj_get_array(st, &n, &items);
        nlr_buf_t nlr;
        if (nlr_push(&nlr) == 0) {
            // (dst, dw, dh, x, y, src, sw, sh[, bx, by, bw, bh]): the layer at
            // (x, y), or its block that lies on the glass, landing where it sits.
            mp_obj_t a[13];
            for (size_t i = 0; i < 8 && i < n; i++) {
                a[i] = items[i];
            }
            a[8] = MP_OBJ_NEW_SMALL_INT(1);
            size_t na = 9;
            if (n >= 12) {
                a[3] = mp_obj_new_int(mp_obj_get_int(items[3]) + mp_obj_get_int(items[8]));
                a[4] = mp_obj_new_int(mp_obj_get_int(items[4]) + mp_obj_get_int(items[9]));
                for (size_t i = 0; i < 4; i++) {
                    a[9 + i] = items[8 + i];
                }
                na = 13;
            }
            mp_call_function_n_kw(mp_load_attr(c->ppa, MP_QSTR_blit_async), na, 0, a);
            nlr_pop();
            kicked = true;
        } else {
            mp_printf(&mp_plat_print, "Moybyte P4 stamp kick failed -> CPU\n");
            if (c->gfx != mp_const_none && n >= 8) {
                mp_obj_t a[9];
                for (size_t i = 0; i < 8; i++) {
                    a[i] = items[i];
                }
                a[8] = MP_OBJ_NEW_SMALL_INT(-1);
                if (nlr_push(&nlr) == 0) {
                    mp_call_function_n_kw(mp_load_attr(c->gfx, MP_QSTR_blit565), 9, 0, a);
                    nlr_pop();
                }
            }
        }
    }
    moy_dsi_present(&c->d, kicked);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_flush_obj, dsi_flush);

static mp_obj_t dsi_present_pending(mp_obj_t self_in) {
    moy_dsi_present_pending(&((glass_dsi_obj_t *)MP_OBJ_TO_PTR(self_in))->d);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_present_pending_obj, dsi_present_pending);

static mp_obj_t dsi_sync(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    moy_dsi_fence(&c->d);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_sync_obj, dsi_sync);

// A compiled cart's frame snapshot has landed.
static mp_obj_t dsi_snap_fence(mp_obj_t self_in) {
    glass_dsi_call0(((glass_dsi_obj_t *)MP_OBJ_TO_PTR(self_in))->ppa, MP_QSTR_snap_wait);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_snap_fence_obj, dsi_snap_fence);

// Nothing still writes or reads a compiled cart's frame copy.
static mp_obj_t dsi_frame_fence(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    glass_dsi_call0(c->ppa, MP_QSTR_snap_wait);
    glass_dsi_call0(c->ppa, MP_QSTR_sync);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_frame_fence_obj, dsi_frame_fence);

// copy_last(x, y, w, h) -> True once that rect of the buffer the last frame
// drew into is in the one this frame draws into (moy_dsi_last has fenced
// what still writes it). Whole rows go through moy_ppa.copy, the AXI GDMA,
// which moves them about twice as fast as the CPU on glass; any other rect,
// or a span the engine refuses, is a row memcpy. With one buffer the last
// frame's is the drawn-into one: True, nothing copied. False when there is
// none to copy from yet.
// A rect (r[0..3], clipped here to W x H) of `src` into `dst`, two W x H
// RGB565 pictures: whole rows through ppa.copy where it takes them, else a
// row memcpy. False: the rect is empty.
bool glass_copy(mp_obj_t ppa, mp_obj_t dst, mp_obj_t src, mp_int_t W, mp_int_t H,
                mp_int_t *r) {
    if (r[0] < 0) {
        r[2] += r[0];
        r[0] = 0;
    }
    if (r[1] < 0) {
        r[3] += r[1];
        r[1] = 0;
    }
    if (r[0] + r[2] > W) {
        r[2] = W - r[0];
    }
    if (r[1] + r[3] > H) {
        r[3] = H - r[1];
    }
    if (r[2] <= 0 || r[3] <= 0) {
        return false;
    }
    if (r[0] == 0 && r[2] == W && ppa != mp_const_none) {
        mp_obj_t m[6];
        mp_load_method_maybe(ppa, MP_QSTR_copy, m);
        if (m[0] != MP_OBJ_NULL) {
            m[2] = dst;
            m[3] = src;
            m[4] = mp_obj_new_int(r[1] * W * 2);
            m[5] = mp_obj_new_int(r[3] * W * 2);
            if (mp_obj_is_true(mp_call_method_n_kw(4, 0, m))) {
                return true;
            }
        }
    }
    mp_buffer_info_t db, sb;
    mp_get_buffer_raise(dst, &db, MP_BUFFER_WRITE);
    mp_get_buffer_raise(src, &sb, MP_BUFFER_READ);
    size_t need = (size_t)W * (size_t)H * 2u;
    if (db.len < need || sb.len < need) {
        mp_raise_ValueError(MP_ERROR_TEXT("copy_last buffers"));
    }
    moy_present_copy_rect(db.buf, sb.buf, (int)(W * 2), (int)r[0], (int)r[1], (int)r[2],
                          (int)r[3]);
    return true;
}

static mp_obj_t dsi_copy_last(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_int_t r[4] = { mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                      mp_obj_get_int(a[3]), mp_obj_get_int(a[4]) };
    glass_shift_fence(c->ppa, &c->shift_fly);
    int l = moy_dsi_last(&c->d);
    if (l < 0) {
        return mp_const_false;
    }
    if (l != c->d.back) {
        glass_copy(c->ppa, mp_obj_subscr(c->fbs, MP_OBJ_NEW_SMALL_INT(c->d.back), MP_OBJ_SENTINEL),
                   mp_obj_subscr(c->fbs, MP_OBJ_NEW_SMALL_INT(l), MP_OBJ_SENTINEL),
                   c->w, c->h, r);
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dsi_copy_last_obj, 5, 5, dsi_copy_last);

// shift_last / copy_last_into / shift_fence (above the banded compositor).
static mp_obj_t dsi_fb(glass_dsi_obj_t *c, int n) {
    return mp_obj_subscr(c->fbs, MP_OBJ_NEW_SMALL_INT(n), MP_OBJ_SENTINEL);
}

static mp_obj_t dsi_shift_last(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    glass_shift_fence(c->ppa, &c->shift_fly);
    int l = moy_dsi_last(&c->d);
    if (l < 0) {
        return mp_const_false;
    }
    int b[6];
    glass_shift(c->ppa, dsi_fb(c, c->d.back), dsi_fb(c, l), c->w, c->h, a + 1,
                &c->shift_fly, b);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dsi_shift_last_obj, 7, 7, dsi_shift_last);

static mp_obj_t dsi_shift_fence(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_shift_fence_obj, dsi_shift_fence);

static mp_obj_t dsi_copy_last_into(size_t n_args, const mp_obj_t *a) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    glass_shift_fence(c->ppa, &c->shift_fly);
    int l = moy_dsi_last(&c->d);
    if (l < 0) {
        return mp_const_false;
    }
    glass_copy_into(c->ppa, dsi_fb(c, l), c->w, c->h, n_args - 1, a + 1);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dsi_copy_last_into_obj, 7, 9, dsi_copy_last_into);

mp_obj_t glass_ppa_timeouts(mp_obj_t ppa) {
    if (ppa == mp_const_none) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    nlr_buf_t nlr;
    mp_obj_t t = MP_OBJ_NEW_SMALL_INT(0);
    if (nlr_push(&nlr) == 0) {
        t = mp_obj_subscr(glass_dsi_call0(ppa, MP_QSTR_stats), MP_OBJ_NEW_SMALL_INT(2),
                          MP_OBJ_SENTINEL);
        nlr_pop();
    }
    return t;
}

// OVERLAP_FIELDS, cumulative since boot: (deferred, obsolete, fences,
// fence_us, game_n, game_us, ppa timeouts).
mp_obj_t glass_dsi_overlap_stats(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    moy_dsi_t *d = &c->d;
    mp_obj_t t[7] = {
        mp_obj_new_int_from_uint(d->deferred), mp_obj_new_int_from_uint(d->obsolete),
        mp_obj_new_int_from_uint(d->fences), mp_obj_new_int_from_uint(d->fence_us),
        mp_obj_new_int_from_uint(d->game_n), mp_obj_new_int_from_uint(d->game_us),
        glass_ppa_timeouts(c->ppa),
    };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_overlap_stats_obj, glass_dsi_overlap_stats);

static mp_obj_t dsi_underruns(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    nlr_buf_t nlr;
    mp_obj_t r = mp_const_none;
    if (nlr_push(&nlr) == 0) {
        r = glass_dsi_call0(c->dsi, MP_QSTR_underruns);
        nlr_pop();
    }
    return r;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_underruns_obj, dsi_underruns);

static mp_obj_t dsi_set_backlight(size_t n_args, const mp_obj_t *a) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_call_function_1(mp_load_attr(c->dsi, MP_QSTR_backlight),
                       mp_obj_new_bool(n_args < 2 || mp_obj_is_true(a[1])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(dsi_set_backlight_obj, 1, 2, dsi_set_backlight);

static void dsi_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    if (dest[0] == MP_OBJ_SENTINEL) {           // a store
        if (attr == MP_QSTR__composite_pending) {
            c->d.composite_pending = mp_obj_is_true(dest[1]);
            dest[0] = MP_OBJ_NULL;
        } else if (attr == MP_QSTR__stamp_pending) {
            c->stamp = dest[1];
            dest[0] = MP_OBJ_NULL;
        }
        return;
    }
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    switch (attr) {
        case MP_QSTR__composite_pending:
            dest[0] = mp_obj_new_bool(c->d.composite_pending);
            return;
        case MP_QSTR__stamp_pending:
            dest[0] = c->stamp;
            return;
        case MP_QSTR__fbs:
            dest[0] = c->fbs;
            return;
        case MP_QSTR__w:
            dest[0] = MP_OBJ_NEW_SMALL_INT(c->w);
            return;
        case MP_QSTR__h:
            dest[0] = MP_OBJ_NEW_SMALL_INT(c->h);
            return;
        case MP_QSTR__back:
            dest[0] = MP_OBJ_NEW_SMALL_INT(c->d.back);
            return;
        case MP_QSTR__last:
            dest[0] = c->d.last < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(c->d.last);
            return;
        case MP_QSTR__pending:
            dest[0] = c->d.pending < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(c->d.pending);
            return;
        case MP_QSTR__pend3: {
            mp_obj_t l = mp_obj_new_list(0, NULL);
            for (int i = 0; i < c->d.npend; i++) {
                mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(c->d.pend[i]),
                                  MP_OBJ_NEW_QSTR(c->d.kind[i] ? MP_QSTR_stamp
                                                               : MP_QSTR_game) };
                mp_obj_list_append(l, mp_obj_new_tuple(2, t));
            }
            dest[0] = l;
            return;
        }
        case MP_QSTR__busy3: {
            mp_obj_t l = mp_obj_new_list(0, NULL);
            for (int i = 0; i < c->d.nbusy; i++) {
                mp_obj_list_append(l, MP_OBJ_NEW_SMALL_INT(c->d.busy[i]));
            }
            dest[0] = l;
            return;
        }
        default:
            dest[1] = MP_OBJ_SENTINEL;
            return;
    }
}

static const mp_rom_map_elem_t dsi_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_size), MP_ROM_PTR(&dsi_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_framebuffer), MP_ROM_PTR(&dsi_back_obj) },
    { MP_ROM_QSTR(MP_QSTR_back_buffer), MP_ROM_PTR(&dsi_back_obj) },
    { MP_ROM_QSTR(MP_QSTR_gfx), MP_ROM_PTR(&dsi_gfx_obj) },
    { MP_ROM_QSTR(MP_QSTR_flush), MP_ROM_PTR(&dsi_flush_obj) },
    { MP_ROM_QSTR(MP_QSTR_present_pending), MP_ROM_PTR(&dsi_present_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&dsi_sync_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_last), MP_ROM_PTR(&dsi_copy_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_shift_last), MP_ROM_PTR(&dsi_shift_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_shift_fence), MP_ROM_PTR(&dsi_shift_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_last_into), MP_ROM_PTR(&dsi_copy_last_into_obj) },
    { MP_ROM_QSTR(MP_QSTR_snap_fence), MP_ROM_PTR(&dsi_snap_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_fence), MP_ROM_PTR(&dsi_frame_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_overlap_stats), MP_ROM_PTR(&dsi_overlap_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_underruns), MP_ROM_PTR(&dsi_underruns_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_backlight), MP_ROM_PTR(&dsi_set_backlight_obj) },
};
static MP_DEFINE_CONST_DICT(dsi_locals, dsi_locals_table);

MP_DEFINE_CONST_OBJ_TYPE(
    glass_dsi_type, MP_QSTR_DsiCompositor, MP_TYPE_FLAG_NONE,
    make_new, dsi_make_new,
    attr, dsi_attr,
    locals_dict, &dsi_locals
    );

#endif // MOY_GLASS_PRESENT
