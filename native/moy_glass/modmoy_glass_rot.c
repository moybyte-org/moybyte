// moy_glass's binding of the rotated DSI compositor (moy_present_rot.c):
// RotatedCompositor, the Guition P4's landscape desk over its portrait panel.

#include "modmoy_glass_int.h"

#if MOY_GLASS_PRESENT

// -- present: the rotated DSI compositor (moy_present.h) -----------------------------
//
// RotatedCompositor(dsi, ppa, angle=90): the Guition P4's landscape desk over
// its portrait panel, the frame state machine in C, its transport the two
// modules passed in. The canvas hands each frame's game composite in with
// mark_game (its buffers and its paint()/quiet() callables) and the WM its
// damage with note_damage; the shell sets the bar strips with set_strips and
// says which it repainted with strip_moved; the deferred window stamp is
// `_stamp_pending`.

static void rot_show(void *ctx, int n) {
    glass_rot_obj_t *c = ctx;
    mp_call_function_1(mp_load_attr(c->dsi, MP_QSTR_show), MP_OBJ_NEW_SMALL_INT(n));
}

static int32_t rot_refreshes(void *ctx) {
    return (int32_t)mp_obj_get_int(mp_call_function_0(((glass_rot_obj_t *)ctx)->refreshes));
}

static void rot_ppa_sync(void *ctx) {
    glass_dsi_call0(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_sync);
}

static bool rot_ppa_done(void *ctx) {
    return mp_obj_is_true(glass_dsi_call0(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_done));
}

static void rot_ppa_wait(void *ctx, int keep) {
    mp_call_function_1(mp_load_attr(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_wait),
                       MP_OBJ_NEW_SMALL_INT(keep));
}

static void rot_snap_wait(void *ctx) {
    glass_dsi_call0(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_snap_wait);
}

#define I(v) MP_OBJ_NEW_SMALL_INT(v)

// A queued submit the full queue refuses raises OSError: -1, for the engine
// to fence and resubmit blocking. A blocking one raises through.
static int rot_call(mp_obj_t fn, size_t n, const mp_obj_t *a, bool nb) {
    if (!nb) {
        mp_call_function_n_kw(fn, n, 0, a);
        return 0;
    }
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_call_function_n_kw(fn, n, 0, a);
        nlr_pop();
        return 0;
    }
    if (!mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(((mp_obj_base_t *)nlr.ret_val)->type),
                                 MP_OBJ_FROM_PTR(&mp_type_OSError))) {
        nlr_jump(nlr.ret_val);
    }
    return -1;
}

static int rot_rotate(void *ctx, bool nb, bool wb, int dst, int dw, int dh, int dx, int dy,
                      int src, int sw, int sh, int sx, int sy, int w, int h, int angle) {
    glass_rot_obj_t *c = ctx;
    mp_obj_t a[15] = { c->bufs[dst], I(dw), I(dh), I(dx), I(dy), c->bufs[src], I(sw),
                       I(sh), I(sx), I(sy), I(w), I(h), I(angle), mp_obj_new_bool(nb),
                       mp_obj_new_bool(wb) };
    return rot_call(c->rotate, 15, a, nb);
}

static int rot_rotate_scale(void *ctx, bool nb, bool wb, int dst, int dw, int dh, int dx,
                            int dy, int src, int sw, int sh, int scale, int angle,
                            const int16_t *blk) {
    glass_rot_obj_t *c = ctx;
    mp_obj_t a[16] = { c->bufs[dst], I(dw), I(dh), I(dx), I(dy), c->bufs[src], I(sw),
                       I(sh), I(scale), I(angle), mp_obj_new_bool(nb), mp_obj_new_bool(wb),
                       0, 0, 0, 0 };
    size_t n = 12;
    if (blk != NULL) {
        for (int i = 0; i < 4; i++) {
            a[n++] = I(blk[i]);
        }
    }
    return rot_call(c->rotate_scale, n, a, nb);
}

static int rot_bounce(void *ctx, int fb, int pw, int ph, int fx, int fy, int paint, int w,
                      int h, int sx, int sy, int bw, int bh, int angle, bool nb) {
    glass_rot_obj_t *c = ctx;
    mp_obj_t a[14] = { c->bufs[fb], I(pw), I(ph), I(fx), I(fy), c->bufs[paint], I(w), I(h),
                       I(sx), I(sy), I(bw), I(bh), I(angle), mp_obj_new_bool(nb) };
    return (int)mp_obj_get_int(mp_call_function_n_kw(c->bounce, 14, 0, a));
}

static void rot_paint(void *ctx) {
    mp_call_function_0(((glass_rot_obj_t *)ctx)->paint);
}

static bool rot_quiet(void *ctx) {
    return mp_obj_is_true(mp_call_function_0(((glass_rot_obj_t *)ctx)->quiet));
}

static mp_obj_t rot_buf(glass_rot_obj_t *c, size_t n) {
    mp_obj_t a[2] = { mp_obj_new_int_from_uint(n), I(MOY_ROLE_PAINT) };
    mp_obj_t b = glass_buf(2, a);
    mp_obj_list_append(c->held, b);
    return ((glass_buf_obj_t *)MP_OBJ_TO_PTR(b))->view;
}

static void rot_scratch(void *ctx, size_t n) {
    glass_rot_obj_t *c = ctx;
    if (c->bufs[MOY_ROT_SCRATCH] == mp_const_none || c->scratch_n < n) {
        c->bufs[MOY_ROT_SCRATCH] = rot_buf(c, n);
        c->scratch_n = n;
    }
}

static mp_obj_t rot_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                             const mp_obj_t *args) {
    enum { ARG_dsi, ARG_ppa, ARG_angle };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_dsi, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_ppa, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_angle, MP_ARG_INT, { .u_int = 90 } },
    };
    mp_arg_val_t a[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all_kw_array(n_args, n_kw, args, MP_ARRAY_SIZE(allowed), allowed, a);
    mp_int_t angle = a[ARG_angle].u_int;
    if (angle != 90 && angle != 270) {
        mp_raise_ValueError(MP_ERROR_TEXT("angle 90 or 270"));
    }
    mp_obj_t dsi = a[ARG_dsi].u_obj, ppa = a[ARG_ppa].u_obj;
    glass_rot_obj_t *c = mp_obj_malloc(glass_rot_obj_t, type);
    c->shift_fly = false;
    c->shifts = c->shift_refused = 0;
    c->dsi = dsi;
    c->ppa = ppa;
    c->stamp = mp_const_none;
    c->paint = c->quiet = mp_const_none;
    c->held = mp_obj_new_list(0, NULL);
    c->scratch_n = 0;
    for (int i = 0; i < 10; i++) {
        c->bufs[i] = mp_const_none;
    }
    if (!glass_kernel_lit()) {
        mp_call_function_1(mp_load_attr(dsi, MP_QSTR_backlight), mp_const_false);
    }
    glass_dsi_call0(dsi, MP_QSTR_init);
    if (!mp_obj_is_true(glass_dsi_call0(ppa, MP_QSTR_init))) {
        mp_raise_msg(&mp_type_OSError,
                     MP_ERROR_TEXT("moy_ppa init failed: a portrait panel needs the rotate"));
    }
    int pw = mp_obj_get_int(mp_load_attr(dsi, MP_QSTR_WIDTH));
    int ph = mp_obj_get_int(mp_load_attr(dsi, MP_QSTR_HEIGHT));
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        c->gfx = mp_import_name(MP_QSTR_moy_gfx, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
        nlr_pop();
    } else {
        c->gfx = mp_const_none;
    }
    mp_obj_t fb = mp_load_attr(dsi, MP_QSTR_fb);
    for (int i = 0; i < 3; i++) {
        c->bufs[i] = mp_call_function_1(fb, I(i));
    }
    for (int i = 0; i < 2; i++) {
        c->bufs[MOY_ROT_PAINT0 + i] = rot_buf(c, (size_t)pw * ph * 2u);
    }
    if (c->gfx != mp_const_none) {
        mp_obj_t fill = mp_load_attr(c->gfx, MP_QSTR_fill);
        for (int i = 0; i < 5; i++) {
            mp_obj_t fa[3] = { c->bufs[i], I(pw * ph), I(0) };
            mp_call_function_n_kw(fill, 3, 0, fa);
        }
    }
    mp_obj_t dest[2];
    c->rotate = mp_load_attr(ppa, MP_QSTR_rotate);
    c->rotate_scale = mp_load_attr(ppa, MP_QSTR_rotate_scale);
    mp_load_method_maybe(ppa, MP_QSTR_rotate_bounce, dest);
    c->bounce = dest[0] == MP_OBJ_NULL ? mp_const_none
              : (dest[1] == MP_OBJ_NULL ? dest[0] : mp_obj_new_bound_meth(dest[0], dest[1]));
    mp_load_method_maybe(dsi, MP_QSTR_refreshes, dest);
    c->refreshes = dest[0] == MP_OBJ_NULL ? mp_const_none
                 : (dest[1] == MP_OBJ_NULL ? dest[0] : mp_obj_new_bound_meth(dest[0], dest[1]));
    mp_load_method_maybe(ppa, MP_QSTR_wait, dest);
    bool async = dest[0] != MP_OBJ_NULL;
    c->ops.show = rot_show;
    c->ops.refreshes = c->refreshes == mp_const_none ? NULL : rot_refreshes;
    c->ops.ppa_sync = rot_ppa_sync;
    c->ops.ppa_done = rot_ppa_done;
    c->ops.ppa_wait = rot_ppa_wait;
    c->ops.snap_wait = rot_snap_wait;
    c->ops.rotate = rot_rotate;
    c->ops.rotate_scale = rot_rotate_scale;
    c->ops.bounce = c->bounce == mp_const_none ? NULL : rot_bounce;
    c->ops.paint = rot_paint;
    c->ops.quiet = rot_quiet;
    c->ops.scratch = rot_scratch;
    c->ops.ticks_us = glass_dsi_ticks;
    c->ops.ctx = c;
    moy_rot_init(&c->r, &c->ops, pw, ph, (int)angle, async, c->bounce != mp_const_none);
    return MP_OBJ_FROM_PTR(c);
}

static mp_obj_t rot_size(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    mp_obj_t t[2] = { I(c->r.w), I(c->r.h) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_size_obj, rot_size);

static mp_obj_t rot_paint_buf(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    return c->bufs[MOY_ROT_PAINT0 + c->r.pi];
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_paint_buf_obj, rot_paint_buf);

// copy_last(x, y, w, h): DsiCompositor.copy_last over the two landscape
// paint buffers (moy_rot_last fences a stamp still landing in the last one).
// The caller notes what it copied as damage, as it would a paint.
static mp_obj_t rot_copy_last(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_int_t r[4] = { mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                      mp_obj_get_int(a[3]), mp_obj_get_int(a[4]) };
    glass_shift_fence(c->ppa, &c->shift_fly);
    int l = moy_rot_last(&c->r);
    if (l < 0) {
        return mp_const_false;
    }
    glass_copy(c->ppa, c->bufs[MOY_ROT_PAINT0 + c->r.pi], c->bufs[MOY_ROT_PAINT0 + l],
               c->r.w, c->r.h, r);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rot_copy_last_obj, 5, 5, rot_copy_last);

// shift_last (above the banded compositor) over the two landscape paint
// buffers. False while a rotate (or any PPA op) is in flight: it shares the
// DMA2D, and a shift queued behind it would wait its 20-30 ms. The block it
// moved is noted as this frame's damage, or the frame would never rotate it,
// so a frame that shifts notes every other rect it paints too.
static mp_obj_t rot_shift_last(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    glass_shift_fence(c->ppa, &c->shift_fly);
    if (!rot_ppa_done(c)) {
        c->shift_refused++;
        return mp_const_false;
    }
    int l = moy_rot_last(&c->r);
    if (l < 0) {
        return mp_const_false;
    }
    int b[6];
    if (glass_shift(c->ppa, c->bufs[MOY_ROT_PAINT0 + c->r.pi], c->bufs[MOY_ROT_PAINT0 + l],
                    c->r.w, c->r.h, a + 1, &c->shift_fly, b)) {
        moy_rot_note_damage(&c->r, b[0], b[1], b[4], b[5]);
    }
    c->shifts++;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rot_shift_last_obj, 7, 7, rot_shift_last);

static mp_obj_t rot_shift_fence(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_shift_fence_obj, rot_shift_fence);

// shift_stats() -> (shifts taken, refused behind a rotate).
static mp_obj_t rot_shift_stats(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(c->shifts),
                      mp_obj_new_int_from_uint(c->shift_refused) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_shift_stats_obj, rot_shift_stats);

static mp_obj_t rot_copy_last_into(size_t n_args, const mp_obj_t *a) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    glass_shift_fence(c->ppa, &c->shift_fly);
    int l = moy_rot_last(&c->r);
    if (l < 0) {
        return mp_const_false;
    }
    glass_copy_into(c->ppa, c->bufs[MOY_ROT_PAINT0 + l], c->r.w, c->r.h, n_args - 1, a + 1);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rot_copy_last_into_obj, 7, 9, rot_copy_last_into);

static mp_obj_t rot_gfx(mp_obj_t self_in) {
    return ((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->gfx;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_gfx_obj, rot_gfx);

static mp_obj_t rot_set_angle(mp_obj_t self_in, mp_obj_t angle_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    mp_int_t angle = mp_obj_get_int(angle_in);
    if (angle != 90 && angle != 270) {
        mp_raise_ValueError(MP_ERROR_TEXT("angle 90 or 270"));
    }
    moy_rot_set_angle(&c->r, (int)angle);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(rot_set_angle_obj, rot_set_angle);

// mark_game(src, sw, sh, ox, oy, scale, paint, quiet, direct, frame=None)
// mark_game(game, sw, sh, ox, oy, scale, paint, quiet, direct, frame=None)
static mp_obj_t rot_mark_game(size_t n_args, const mp_obj_t *a, mp_map_t *kw) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_obj_t frame = n_args > 10 ? a[10] : mp_const_none;
    mp_map_elem_t *kf = kw ? mp_map_lookup(kw, MP_OBJ_NEW_QSTR(MP_QSTR_frame),
                                           MP_MAP_LOOKUP) : NULL;
    if (kf != NULL) {
        frame = kf->value;
    }
    moy_rot_game_t *g = &c->r.game;
    g->sw = mp_obj_get_int(a[2]);
    g->sh = mp_obj_get_int(a[3]);
    g->ox = mp_obj_get_int(a[4]);
    g->oy = mp_obj_get_int(a[5]);
    g->scale = mp_obj_get_int(a[6]);
    c->paint = a[7];
    c->quiet = a[8];
    g->direct = mp_obj_is_true(a[9]);
    c->bufs[MOY_ROT_GAME] = a[1];
    c->bufs[MOY_ROT_PIC] = mp_const_none;
    g->frame = frame != mp_const_none;
    if (g->frame) {
        size_t n;
        mp_obj_t *f;
        mp_obj_get_array(frame, &n, &f);
        if (n < 8) {
            mp_raise_ValueError(MP_ERROR_TEXT("frame"));
        }
        g->bx = (int16_t)mp_obj_get_int(f[0]);
        g->by = (int16_t)mp_obj_get_int(f[1]);
        g->bw = (int16_t)mp_obj_get_int(f[2]);
        g->bh = (int16_t)mp_obj_get_int(f[3]);
        c->bufs[MOY_ROT_PIC] = f[4];
        g->rows = mp_obj_get_int(f[5]);
        mp_int_t np = mp_obj_get_int(f[7]);
        g->npatch = np < 0 ? 0 : (np > 4 ? 4 : (int)np);
        for (int k = 0; k < 6 * g->npatch; k++) {
            g->patches[k] = (int16_t)mp_obj_get_int(
                mp_obj_subscr(f[6], I(k), MP_OBJ_SENTINEL));
        }
    }
    c->r.has_game = true;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(rot_mark_game_obj, 10, rot_mark_game);

static mp_obj_t rot_note_damage(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    moy_rot_note_damage(&c->r, mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                        mp_obj_get_int(a[3]), mp_obj_get_int(a[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rot_note_damage_obj, 5, 5, rot_note_damage);

// set_strips([(x, y, w, h), ...]): the bar strips, set on relayout.
static mp_obj_t rot_set_strips(mp_obj_t self_in, mp_obj_t rects_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    size_t n;
    mp_obj_t *it;
    mp_obj_get_array(rects_in, &n, &it);
    moy_rect_t rs[MOY_ROT_DAMAGE];
    if (n > MOY_ROT_DAMAGE) {
        mp_raise_ValueError(MP_ERROR_TEXT("strips"));
    }
    for (size_t i = 0; i < n; i++) {
        size_t m;
        mp_obj_t *v;
        mp_obj_get_array(it[i], &m, &v);
        if (m != 4) {
            mp_raise_ValueError(MP_ERROR_TEXT("strip"));
        }
        rs[i] = (moy_rect_t){ (int16_t)mp_obj_get_int(v[0]), (int16_t)mp_obj_get_int(v[1]),
                              (int16_t)mp_obj_get_int(v[2]), (int16_t)mp_obj_get_int(v[3]) };
    }
    moy_rot_set_strips(&c->r, rs, (int)n);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(rot_set_strips_obj, rot_set_strips);

static mp_obj_t rot_strip_moved(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    moy_rot_strip_moved(&c->r, mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                        mp_obj_get_int(a[3]), mp_obj_get_int(a[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rot_strip_moved_obj, 5, 5, rot_strip_moved);

// The strips as set, after coalescing.
static mp_obj_t rot_strips(mp_obj_t self_in) {
    moy_rot_t *r = &((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r;
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < r->nstrips; i++) {
        const moy_rect_t *s = &r->strips[i];
        mp_obj_t t[4] = { I(s->x), I(s->y), I(s->w), I(s->h) };
        mp_obj_list_append(out, mp_obj_new_tuple(4, t));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_strips_obj, rot_strips);

static mp_obj_t rot_flush(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    moy_rot_flush(&c->r);
    if (!c->r.has_stamp) {
        c->stamp = mp_const_none;
        c->bufs[MOY_ROT_STAMP_DST] = c->bufs[MOY_ROT_STAMP_SRC] = mp_const_none;
    }
    c->paint = c->quiet = mp_const_none;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_flush_obj, rot_flush);

static mp_obj_t rot_present_pending(mp_obj_t self_in) {
    moy_rot_present_pending(&((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_present_pending_obj, rot_present_pending);

static mp_obj_t rot_sync(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    moy_rot_fence(&c->r);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_sync_obj, rot_sync);

static mp_obj_t rot_snap_fence(mp_obj_t self_in) {
    rot_snap_wait(MP_OBJ_TO_PTR(self_in));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_snap_fence_obj, rot_snap_fence);

static mp_obj_t rot_frame_fence(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    glass_shift_fence(c->ppa, &c->shift_fly);
    if (c->r.has_game && c->r.game.frame) {
        c->r.has_game = false;
    }
    rot_snap_wait(c);
    rot_ppa_sync(c);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_frame_fence_obj, rot_frame_fence);

static mp_obj_t rot_tuple(const uint32_t *const *v, size_t n) {
    mp_obj_t t[7];
    for (size_t i = 0; i < n; i++) {
        t[i] = mp_obj_new_int_from_uint(*v[i]);
    }
    return mp_obj_new_tuple(n, t);
}

static mp_obj_t rot_async_stats(mp_obj_t self_in) {
    moy_rot_t *r = &((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r;
    const uint32_t *v[7] = { &r->def_n, &r->pres_n, &r->late_n, &r->wait_us, &r->stamp_n,
                             &r->refused, &r->bounced };
    return rot_tuple(v, 7);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_async_stats_obj, rot_async_stats);

static mp_obj_t rot_rotate_stats(mp_obj_t self_in) {
    moy_rot_t *r = &((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r;
    const uint32_t *v[5] = { &r->full_n, &r->full_us, &r->rect_n, &r->rect_us, &r->copies };
    return rot_tuple(v, 5);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_rotate_stats_obj, rot_rotate_stats);

static mp_obj_t rot_damage_stats(mp_obj_t self_in) {
    moy_rot_t *r = &((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r;
    const uint32_t *v[4] = { &r->dmg_n, &r->dmg_rects, &r->dmg_declined, &r->grown };
    return rot_tuple(v, 4);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_damage_stats_obj, rot_damage_stats);

// (strip rects rotated, moved strips a damage rect already covered)
static mp_obj_t rot_strip_stats(mp_obj_t self_in) {
    moy_rot_t *r = &((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r;
    const uint32_t *v[2] = { &r->strip_n, &r->strip_covered };
    return rot_tuple(v, 2);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_strip_stats_obj, rot_strip_stats);

// OVERLAP_FIELDS: obsolete is None -- this path never drops a queued show.
mp_obj_t glass_rot_overlap_stats(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    moy_rot_t *r = &c->r;
    mp_obj_t t[7] = {
        mp_obj_new_int_from_uint(r->def_n), mp_const_none,
        mp_obj_new_int_from_uint(r->fences), mp_obj_new_int_from_uint(r->fence_us),
        mp_obj_new_int_from_uint(r->wait_n), mp_obj_new_int_from_uint(r->wait_us),
        glass_ppa_timeouts(c->ppa),
    };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_overlap_stats_obj, glass_rot_overlap_stats);

static mp_obj_t rot_underruns(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    nlr_buf_t nlr;
    mp_obj_t r = mp_const_none;
    if (nlr_push(&nlr) == 0) {
        r = glass_dsi_call0(c->dsi, MP_QSTR_underruns);
        nlr_pop();
    }
    return r;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_underruns_obj, rot_underruns);

static mp_obj_t rot_set_backlight(size_t n_args, const mp_obj_t *a) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_call_function_1(mp_load_attr(c->dsi, MP_QSTR_backlight),
                       mp_obj_new_bool(n_args < 2 || mp_obj_is_true(a[1])));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rot_set_backlight_obj, 1, 2, rot_set_backlight);

static void rot_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    if (dest[0] == MP_OBJ_SENTINEL) {           // a store
        if (attr == MP_QSTR__stamp_pending) {
            mp_obj_t st = dest[1];
            if (st == mp_const_none) {
                c->r.has_stamp = false;
            } else {
                size_t n;
                mp_obj_t *it;
                mp_obj_get_array(st, &n, &it);
                if (n < 8) {
                    mp_raise_ValueError(MP_ERROR_TEXT("stamp"));
                }
                c->bufs[MOY_ROT_STAMP_DST] = it[0];
                c->bufs[MOY_ROT_STAMP_SRC] = it[5];
                moy_rot_stamp_t *s = &c->r.stamp;
                s->dw = mp_obj_get_int(it[1]);
                s->dh = mp_obj_get_int(it[2]);
                s->x = mp_obj_get_int(it[3]);
                s->y = mp_obj_get_int(it[4]);
                s->sw = mp_obj_get_int(it[6]);
                s->sh = mp_obj_get_int(it[7]);
                c->r.has_stamp = true;
            }
            c->stamp = st;
            dest[0] = MP_OBJ_NULL;
        } else if (attr == MP_QSTR__composite_pending) {
            dest[0] = MP_OBJ_NULL;              // the Waveshare's flag; inert here
        }
        return;
    }
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    switch (attr) {
        case MP_QSTR_angle:
            dest[0] = I(c->r.angle);
            return;
        case MP_QSTR_rotated:
            dest[0] = mp_const_true;
            return;
        case MP_QSTR__fbs:          // the portrait scan buffers
            dest[0] = mp_obj_new_list(3, &c->bufs[MOY_ROT_FB0]);
            return;
        case MP_QSTR__front:
            dest[0] = I(c->r.front);
            return;
        case MP_QSTR__w:
            dest[0] = I(c->r.w);
            return;
        case MP_QSTR__h:
            dest[0] = I(c->r.h);
            return;
        case MP_QSTR__pw:
            dest[0] = I(c->r.pw);
            return;
        case MP_QSTR__ph:
            dest[0] = I(c->r.ph);
            return;
        case MP_QSTR_retained_frames:
            dest[0] = I(2);
            return;
        case MP_QSTR__async:
            dest[0] = mp_obj_new_bool(c->r.async);
            return;
        case MP_QSTR__stamp_pending:
            dest[0] = c->r.has_stamp ? c->stamp : mp_const_none;
            return;
        case MP_QSTR__composite_pending:
            dest[0] = mp_const_false;
            return;
        case MP_QSTR__pending:
            dest[0] = c->r.pending < 0 ? mp_const_none : I(c->r.pending);
            return;
        default:
            dest[1] = MP_OBJ_SENTINEL;
            return;
    }
}

static const mp_rom_map_elem_t rot_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_size), MP_ROM_PTR(&rot_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_framebuffer), MP_ROM_PTR(&rot_paint_buf_obj) },
    { MP_ROM_QSTR(MP_QSTR_back_buffer), MP_ROM_PTR(&rot_paint_buf_obj) },
    { MP_ROM_QSTR(MP_QSTR_gfx), MP_ROM_PTR(&rot_gfx_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_angle), MP_ROM_PTR(&rot_set_angle_obj) },
    { MP_ROM_QSTR(MP_QSTR_mark_game), MP_ROM_PTR(&rot_mark_game_obj) },
    { MP_ROM_QSTR(MP_QSTR_note_damage), MP_ROM_PTR(&rot_note_damage_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_last), MP_ROM_PTR(&rot_copy_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_shift_last), MP_ROM_PTR(&rot_shift_last_obj) },
    { MP_ROM_QSTR(MP_QSTR_shift_fence), MP_ROM_PTR(&rot_shift_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_shift_stats), MP_ROM_PTR(&rot_shift_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_last_into), MP_ROM_PTR(&rot_copy_last_into_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_strips), MP_ROM_PTR(&rot_set_strips_obj) },
    { MP_ROM_QSTR(MP_QSTR_strip_moved), MP_ROM_PTR(&rot_strip_moved_obj) },
    { MP_ROM_QSTR(MP_QSTR_strips), MP_ROM_PTR(&rot_strips_obj) },
    { MP_ROM_QSTR(MP_QSTR_strip_stats), MP_ROM_PTR(&rot_strip_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_flush), MP_ROM_PTR(&rot_flush_obj) },
    { MP_ROM_QSTR(MP_QSTR_present_pending), MP_ROM_PTR(&rot_present_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&rot_sync_obj) },
    { MP_ROM_QSTR(MP_QSTR_snap_fence), MP_ROM_PTR(&rot_snap_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_fence), MP_ROM_PTR(&rot_frame_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_async_stats), MP_ROM_PTR(&rot_async_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_rotate_stats), MP_ROM_PTR(&rot_rotate_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_damage_stats), MP_ROM_PTR(&rot_damage_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_overlap_stats), MP_ROM_PTR(&rot_overlap_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_underruns), MP_ROM_PTR(&rot_underruns_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_backlight), MP_ROM_PTR(&rot_set_backlight_obj) },
};
static MP_DEFINE_CONST_DICT(rot_locals, rot_locals_table);

MP_DEFINE_CONST_OBJ_TYPE(
    glass_rot_type, MP_QSTR_RotatedCompositor, MP_TYPE_FLAG_NONE,
    make_new, rot_make_new,
    attr, rot_attr,
    locals_dict, &rot_locals
    );

#undef I

#endif // MOY_GLASS_PRESENT
