// moy_glass's MicroPython binding: the glass's tables (moy_buf.h, moy_canvas.h,
// moy_surface.h) as the module `moy_glass`, the same names the host's ctypes
// binding (runtime/glass_binding.py) gives CPython.
//
//   buf(nbytes, role, owner=0) -> Buf     a BUF row and its bytes
//   Canvas(w, h, caps=0, owner=0)         a CANVAS row
//   owner(tag, cls=CLASS_CART) -> h       an OWNER row
//   reclaim(owner, roles=0), owner_end(owner)
//   row(h), loans(owner), pool(), stats(), set_pool_bound(n), evict()
//   the surface table's verbs: surface(sid), touch, move, animating, epoch,
//   content_gen, mint, place, drop, sync, kernel_epoch, kernel_bump
//
// A Buf holds its row: `view` is a writable memoryview of its bytes, and when
// the row goes -- release(), its owner's end, or the Buf's own collection --
// the view is neutered (length 0, no items), so a stale draw raises instead of
// writing memory the pool has lent again. A HEAP row's bytes are a bytearray
// the Buf holds: the last resort when PSRAM refuses even after the pool is
// evicted, counted so the gate sees it.
//
// A Canvas holds its row: `state` and `pal` are views of the row's draw state
// and colour table, which the draw gates read in C (native/moy_gfx), and
// point(buf) is where it draws. Every table byte is PSRAM on a board
// (docs/native_kernel_2026-09.md §4.6); pixels are DMA-reachable PSRAM.

#include <string.h>

#include "py/binary.h"
#include "py/gc.h"
#include "py/objarray.h"
#include "py/objlist.h"
#include "py/objtuple.h"
#include "py/runtime.h"
#include "py/mphal.h"

#if __has_include("esp_heap_caps.h")
#define MOY_GLASS_BOARD 1
#include "esp_heap_caps.h"
#else
#include <stdlib.h>
#endif

#include "moy_buf.h"
#include "moy_canvas.h"
#include "moy_present.h"
#include "moy_surface.h"

#ifndef MOY_GLASS_POOL_BYTES
#define MOY_GLASS_POOL_BYTES (4u * 1024u * 1024u)
#endif

// A gc-heap fallback this big collects first: the run it needs is contiguous.
#define HEAP_COMPACT_BYTES (128u * 1024u)

// -- the allocators -------------------------------------------------------------

#ifdef MOY_GLASS_BOARD
static void *tab_alloc(size_t n) {
    n = n ? n : 1u;
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0) {
        p = heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
    }
    return p;
}

static void tab_release(void *p, size_t n) {
    (void)n;
    heap_caps_free(p);
}

static void *px_alloc(size_t n) {
    return heap_caps_aligned_calloc(64, 1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_DMA);
}

static void px_release(void *p, size_t n) {
    (void)n;
    heap_caps_free(p);
}

static size_t px_free_total(void) {
    return heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
}

static size_t px_largest(void) {
    return heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
}
#else
static void *tab_alloc(size_t n) {
    return calloc(1, n ? n : 1u);
}

static void tab_release(void *p, size_t n) {
    (void)n;
    free(p);
}

static void *px_alloc(size_t n) {
    return calloc(1, n);
}

static void px_release(void *p, size_t n) {
    (void)n;
    free(p);
}

#endif

static const moy_htab_mem_t glass_mem = { tab_alloc, tab_release };
#ifdef MOY_GLASS_BOARD
static const moy_glass_px_t glass_px = { px_alloc, px_release, px_free_total,
                                         px_largest };
#else
static const moy_glass_px_t glass_px = { px_alloc, px_release, NULL, NULL };
#endif

static void ready(void) {
    if (!moy_glass_ready()
        && moy_glass_init(&glass_mem, &glass_px, MOY_GLASS_POOL_BYTES) != MOY_GLASS_OK) {
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("glass: no tables"));
    }
}

static MP_NORETURN void raise_rc(int rc, const char *what) {
    if (rc == MOY_GLASS_STALE) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("stale %s handle"), what);
    }
    if (rc == MOY_GLASS_FULL) {
        mp_raise_msg_varg(&mp_type_MemoryError, MP_ERROR_TEXT("the glass's %s table is full"),
                          what);
    }
    if (rc == MOY_GLASS_BAD) {
        mp_raise_ValueError(MP_ERROR_TEXT("glass: bad argument"));
    }
    mp_raise_type(&mp_type_MemoryError);
}

static uint32_t h_arg(mp_obj_t o) {
    mp_int_t v = mp_obj_get_int(o);
    return (v > 0 && v < ((mp_int_t)1 << 30)) ? (uint32_t)v : 0u;
}

static void neuter(mp_obj_t view) {
    if (view != MP_OBJ_NULL && mp_obj_is_type(view, &mp_type_memoryview)) {
        mp_obj_array_t *v = MP_OBJ_TO_PTR(view);
        v->len = 0;
        v->items = NULL;
    }
}

// -- Buf ------------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    uint32_t h;
    mp_obj_t view;              // the memoryview handed out
    mp_obj_t heap;              // a HEAP row's bytearray, or MP_OBJ_NULL
} glass_buf_obj_t;

static const mp_obj_type_t glass_buf_type;

static void buf_drop(glass_buf_obj_t *b) {
    if (b->h != 0u) {
        moy_buf_release(b->h);
        b->h = 0u;
    }
    neuter(b->view);
    b->heap = MP_OBJ_NULL;
}

static mp_obj_t buf_release(mp_obj_t self_in) {
    buf_drop(MP_OBJ_TO_PTR(self_in));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(buf_release_obj, buf_release);

// The row may have gone without this object (its owner ended): then the view
// is neutered here, the first time anything asks.
static moy_buf_row_t *buf_row(glass_buf_obj_t *b) {
    moy_buf_row_t *r;
    if (b->h == 0u || moy_buf_get(b->h, &r) != MOY_GLASS_OK) {
        b->h = 0u;
        neuter(b->view);
        b->heap = MP_OBJ_NULL;
        return NULL;
    }
    return r;
}

static void buf_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    glass_buf_obj_t *b = MP_OBJ_TO_PTR(self_in);
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    moy_buf_row_t *r = buf_row(b);
    switch (attr) {
        case MP_QSTR_h:
            dest[0] = mp_obj_new_int_from_uint(b->h);
            break;
        case MP_QSTR_view:
            dest[0] = b->view;
            break;
        case MP_QSTR_live:
            dest[0] = mp_obj_new_bool(r != NULL);
            break;
        case MP_QSTR_nbytes:
            dest[0] = MP_OBJ_NEW_SMALL_INT(r ? (mp_int_t)r->nbytes : 0);
            break;
        case MP_QSTR_origin:
            dest[0] = MP_OBJ_NEW_SMALL_INT(r ? r->origin : -1);
            break;
        case MP_QSTR_role:
            dest[0] = MP_OBJ_NEW_SMALL_INT(r ? r->role : -1);
            break;
        case MP_QSTR_release:
            dest[0] = MP_OBJ_FROM_PTR(&buf_release_obj);
            dest[1] = self_in;
            break;
        case MP_QSTR___del__:
            dest[0] = MP_OBJ_FROM_PTR(&buf_release_obj);
            dest[1] = self_in;
            break;
        default:
            break;
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    glass_buf_type, MP_QSTR_Buf, MP_TYPE_FLAG_NONE,
    attr, buf_attr
    );

// buf(nbytes, role, owner=0) -> Buf.
static mp_obj_t glass_buf(size_t n_args, const mp_obj_t *a) {
    ready();
    mp_int_t n = mp_obj_get_int(a[0]);
    mp_int_t role = mp_obj_get_int(a[1]);
    uint32_t owner = n_args > 2 ? h_arg(a[2]) : 0u;
    if (n <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("size must be positive"));
    }
    uint32_t h;
    int rc = moy_buf_new(&h, (size_t)n, (uint8_t)role, owner);
    if (rc == MOY_GLASS_FULL) {
        gc_collect();           // a dead Buf's finaliser gives its row back
        rc = moy_buf_new(&h, (size_t)n, (uint8_t)role, owner);
    }
    glass_buf_obj_t *b = mp_obj_malloc_with_finaliser(glass_buf_obj_t, &glass_buf_type);
    b->h = 0u;
    b->view = MP_OBJ_NULL;
    b->heap = MP_OBJ_NULL;
    if (rc == MOY_GLASS_OK) {
        moy_buf_row_t *r;
        moy_buf_get(h, &r);
        b->h = h;
        mp_obj_array_t *v = MP_OBJ_TO_PTR(
            mp_obj_new_memoryview(BYTEARRAY_TYPECODE, (size_t)n, r->px));
        v->typecode |= 0x80;
        b->view = MP_OBJ_FROM_PTR(v);
        return MP_OBJ_FROM_PTR(b);
    }
    if (rc != MOY_GLASS_NOMEM) {
        raise_rc(rc, "buffer");
    }
    // PSRAM refused twice: the gc heap, after the compact a big buffer needs.
    if ((size_t)n >= HEAP_COMPACT_BYTES) {
        gc_collect();
    }
    mp_obj_t ba = mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_bytearray),
                                     MP_OBJ_NEW_SMALL_INT(n));
    rc = moy_buf_heap(&h, (size_t)n, (uint8_t)role, owner);
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "buffer");
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(ba, &bi, MP_BUFFER_RW);
    b->h = h;
    b->heap = ba;
    mp_obj_array_t *v = MP_OBJ_TO_PTR(
        mp_obj_new_memoryview(BYTEARRAY_TYPECODE, (size_t)n, bi.buf));
    v->typecode |= 0x80;
    b->view = MP_OBJ_FROM_PTR(v);
    return MP_OBJ_FROM_PTR(b);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_buf_obj, 2, 3, glass_buf);

// -- owners and rows ------------------------------------------------------------

static mp_obj_t glass_owner(size_t n_args, const mp_obj_t *a) {
    ready();
    const char *tag = mp_obj_str_get_str(a[0]);
    uint8_t cls = n_args > 1 ? (uint8_t)mp_obj_get_int(a[1]) : MOY_CLASS_CART;
    uint32_t h;
    int rc = moy_owner_new(&h, tag, cls);
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "owner");
    }
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_owner_obj, 1, 2, glass_owner);

static mp_obj_t glass_reclaim(size_t n_args, const mp_obj_t *a) {
    ready();
    uint32_t roles = n_args > 1 ? (uint32_t)mp_obj_get_int(a[1]) : 0u;
    int rc = moy_owner_reclaim(h_arg(a[0]), roles);
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "owner");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_reclaim_obj, 1, 2, glass_reclaim);

static mp_obj_t glass_owner_end(mp_obj_t h) {
    ready();
    int rc = moy_owner_end(h_arg(h));
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "owner");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_owner_end_obj, glass_owner_end);

// row(h) -> (nbytes, role, origin, owner, holder, cls)
static mp_obj_t glass_row(mp_obj_t h) {
    ready();
    moy_buf_row_t *r;
    if (moy_buf_get(h_arg(h), &r) != MOY_GLASS_OK) {
        raise_rc(MOY_GLASS_STALE, "buffer");
    }
    mp_obj_t t[6] = {
        mp_obj_new_int_from_uint(r->nbytes), MP_OBJ_NEW_SMALL_INT(r->role),
        MP_OBJ_NEW_SMALL_INT(r->origin), mp_obj_new_int_from_uint(r->owner),
        mp_obj_new_int_from_uint(r->holder), MP_OBJ_NEW_SMALL_INT(r->cls),
    };
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_row_obj, glass_row);

static mp_obj_t glass_set_holder(mp_obj_t h, mp_obj_t holder) {
    ready();
    if (moy_buf_set_holder(h_arg(h), (uint32_t)mp_obj_get_int(holder)) != MOY_GLASS_OK) {
        raise_rc(MOY_GLASS_STALE, "buffer");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(glass_set_holder_obj, glass_set_holder);

// rows(owner=None) -> [h]: every live loan in slot order (the pool's free rows
// when owner is -1), or one owner's.
static mp_obj_t glass_rows(size_t n_args, const mp_obj_t *a) {
    ready();
    mp_obj_t out = mp_obj_new_list(0, NULL);
    bool all = n_args == 0 || a[0] == mp_const_none;
    mp_int_t want = all ? 0 : mp_obj_get_int(a[0]);
    for (uint32_t s = 0; s < moy_buf_slots(); s++) {
        uint32_t h = moy_buf_at(s);
        moy_buf_row_t *r;
        if (h == 0u || moy_buf_get(h, &r) != MOY_GLASS_OK) {
            continue;
        }
        bool pooled = r->role == MOY_ROLE_POOL;
        if (all ? !pooled
                : (want < 0 ? pooled : (!pooled && r->owner == (uint32_t)want))) {
            mp_obj_list_append(out, mp_obj_new_int_from_uint(h));
        }
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_rows_obj, 0, 1, glass_rows);

static mp_obj_t glass_stats(void) {
    ready();
    moy_glass_stats_t s;
    moy_glass_stats(&s);
    mp_obj_t t[14] = {
        MP_OBJ_NEW_SMALL_INT(s.rows), MP_OBJ_NEW_SMALL_INT(s.peak),
        MP_OBJ_NEW_SMALL_INT(s.pool_rows), mp_obj_new_int_from_uint(s.pool_bytes),
        mp_obj_new_int_from_uint(s.pool_bound), MP_OBJ_NEW_SMALL_INT(s.heap_rows),
        mp_obj_new_int_from_uint(s.heap_bytes), mp_obj_new_int_from_uint(s.kernel_bytes),
        mp_obj_new_int_from_uint(s.cart_bytes), MP_OBJ_NEW_SMALL_INT(s.owners),
        MP_OBJ_NEW_SMALL_INT(s.evictions), mp_obj_new_int_from_uint(s.px_free),
        mp_obj_new_int_from_uint(s.px_largest), MP_OBJ_NEW_SMALL_INT(moy_canvas_count()),
    };
    return mp_obj_new_tuple(14, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_stats_obj, glass_stats);

static mp_obj_t glass_set_pool_bound(mp_obj_t n) {
    ready();
    moy_glass_set_pool_bound((uint32_t)mp_obj_get_int(n));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_set_pool_bound_obj, glass_set_pool_bound);

static mp_obj_t glass_evict(void) {
    ready();
    moy_glass_evict();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_evict_obj, glass_evict);

// -- Canvas ---------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    uint32_t h;
    mp_obj_t target;            // what it draws into, held for the collector
    mp_obj_t state;             // views of the row
    mp_obj_t pal;
} glass_canvas_obj_t;

static const mp_obj_type_t glass_canvas_type;

static mp_obj_t row_view(char typecode, size_t n, void *p) {
    mp_obj_array_t *v = MP_OBJ_TO_PTR(mp_obj_new_memoryview(typecode, n, p));
    v->typecode |= 0x80;
    return MP_OBJ_FROM_PTR(v);
}

static mp_obj_t canvas_make_new(const mp_obj_type_t *type, size_t n_args,
                                size_t n_kw, const mp_obj_t *a) {
    mp_arg_check_num(n_args, n_kw, 2, 4, false);
    ready();
    mp_int_t w = mp_obj_get_int(a[0]);
    mp_int_t hp = mp_obj_get_int(a[1]);
    uint32_t caps = n_args > 2 ? (uint32_t)mp_obj_get_int(a[2]) : 0u;
    uint32_t owner = n_args > 3 ? h_arg(a[3]) : 0u;
    if (w < 0 || hp < 0 || w > 0xffff || hp > 0xffff) {
        mp_raise_ValueError(MP_ERROR_TEXT("canvas size"));
    }
    uint32_t h;
    int rc = moy_canvas_new(&h, (uint16_t)w, (uint16_t)hp, caps, owner);
    if (rc == MOY_GLASS_FULL) {
        gc_collect();
        rc = moy_canvas_new(&h, (uint16_t)w, (uint16_t)hp, caps, owner);
    }
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "canvas");
    }
    moy_canvas_row_t *r;
    moy_canvas_get(h, &r);
    glass_canvas_obj_t *c = mp_obj_malloc_with_finaliser(glass_canvas_obj_t, type);
    c->h = h;
    c->target = mp_const_none;
    c->state = row_view('i', MOY_ST_LEN, r->st);
    c->pal = row_view('H', 64, r->pal);
    return MP_OBJ_FROM_PTR(c);
}

static mp_obj_t canvas_release(mp_obj_t self_in) {
    glass_canvas_obj_t *c = MP_OBJ_TO_PTR(self_in);
    if (c->h != 0u) {
        moy_canvas_release(c->h);
        c->h = 0u;
        neuter(c->state);
        neuter(c->pal);
        c->target = mp_const_none;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(canvas_release_obj, canvas_release);

// point(buf, buf_h=0): draw into `buf` from now on.
static mp_obj_t canvas_point(size_t n_args, const mp_obj_t *a) {
    glass_canvas_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(a[1], &bi, MP_BUFFER_RW);
    uint32_t bh = n_args > 2 ? h_arg(a[2]) : 0u;
    if (moy_canvas_point(c->h, (uint16_t *)bi.buf, (uint32_t)(bi.len / 2u), bh)
        != MOY_GLASS_OK) {
        raise_rc(MOY_GLASS_STALE, "canvas");
    }
    c->target = a[1];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(canvas_point_obj, 2, 3, canvas_point);

static const mp_rom_map_elem_t canvas_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_point), MP_ROM_PTR(&canvas_point_obj) },
    { MP_ROM_QSTR(MP_QSTR_release), MP_ROM_PTR(&canvas_release_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&canvas_release_obj) },
};
static MP_DEFINE_CONST_DICT(canvas_locals, canvas_locals_table);

static void canvas_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    glass_canvas_obj_t *c = MP_OBJ_TO_PTR(self_in);
    if (dest[0] != MP_OBJ_NULL) {
        return;
    }
    if (attr == MP_QSTR_h) {
        dest[0] = mp_obj_new_int_from_uint(c->h);
    } else if (attr == MP_QSTR_state) {
        dest[0] = c->state;
    } else if (attr == MP_QSTR_pal) {
        dest[0] = c->pal;
    } else {
        dest[1] = MP_OBJ_SENTINEL;      // the locals dict
    }
}

static MP_DEFINE_CONST_OBJ_TYPE(
    glass_canvas_type, MP_QSTR_Canvas, MP_TYPE_FLAG_NONE,
    make_new, canvas_make_new,
    attr, canvas_attr,
    locals_dict, &canvas_locals
    );

// -- surfaces -------------------------------------------------------------------

static mp_obj_t glass_surface(size_t n_args, const mp_obj_t *a) {
    ready();
    uint32_t h;
    int rc = moy_surface_get(&h, mp_obj_str_get_str(a[0]),
                             n_args > 1 ? (uint8_t)mp_obj_get_int(a[1]) : 0u);
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "surface");
    }
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_surface_obj, 1, 2, glass_surface);

static mp_obj_t glass_surface_find(mp_obj_t sid) {
    ready();
    return mp_obj_new_int_from_uint(moy_surface_find(mp_obj_str_get_str(sid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_surface_find_obj, glass_surface_find);

static mp_obj_t glass_touch(mp_obj_t h) {
    moy_surface_touch(h_arg(h));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_touch_obj, glass_touch);

static mp_obj_t glass_move(mp_obj_t h) {
    moy_surface_move(h_arg(h));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_move_obj, glass_move);

static mp_obj_t glass_animating(size_t n_args, const mp_obj_t *a) {
    if (n_args > 1) {
        moy_surface_animating(h_arg(a[0]), mp_obj_is_true(a[1]));
        return mp_const_none;
    }
    moy_surf_row_t *r;
    return mp_obj_new_bool(moy_surface_row(h_arg(a[0]), &r) == MOY_GLASS_OK
                           && r->animating);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_animating_obj, 1, 2, glass_animating);

static mp_obj_t glass_epoch(void) {
    ready();
    moy_surface_epoch();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_epoch_obj, glass_epoch);

static mp_obj_t glass_content_gen(mp_obj_t h) {
    ready();
    return mp_obj_new_int_from_uint(moy_surface_content_gen(h_arg(h)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_content_gen_obj, glass_content_gen);

// gens(h) -> (content_gen, place_gen), the row's own.
static mp_obj_t glass_gens(mp_obj_t h) {
    moy_surf_row_t *r;
    if (moy_surface_row(h_arg(h), &r) != MOY_GLASS_OK) {
        raise_rc(MOY_GLASS_STALE, "surface");
    }
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(r->content_gen),
                      mp_obj_new_int_from_uint(r->place_gen) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_gens_obj, glass_gens);

static mp_obj_t glass_mint(void) {
    ready();
    return mp_obj_new_int_from_uint(moy_surface_mint());
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_mint_obj, glass_mint);

// place(h, x, y, w, h, z) -> True when the placement moved (its gen minted).
static mp_obj_t glass_place(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_surf_row_t *r;
    if (moy_surface_row(h_arg(a[0]), &r) != MOY_GLASS_OK) {
        raise_rc(MOY_GLASS_STALE, "surface");
    }
    int16_t x = (int16_t)mp_obj_get_int(a[1]), y = (int16_t)mp_obj_get_int(a[2]);
    uint16_t w = (uint16_t)mp_obj_get_int(a[3]), hh = (uint16_t)mp_obj_get_int(a[4]);
    int16_t z = (int16_t)mp_obj_get_int(a[5]);
    if (r->x == x && r->y == y && r->w == w && r->h == hh && r->z == z) {
        return mp_const_false;
    }
    r->x = x;
    r->y = y;
    r->w = w;
    r->h = hh;
    r->z = z;
    moy_surface_move(h_arg(a[0]));
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_place_obj, 6, 6, glass_place);

// placement(h) -> [x, y, scale, z], the wire shape (surface_model_v1 §6).
static mp_obj_t glass_placement(mp_obj_t h) {
    moy_surf_row_t *r;
    if (moy_surface_row(h_arg(h), &r) != MOY_GLASS_OK) {
        raise_rc(MOY_GLASS_STALE, "surface");
    }
    mp_obj_t t[4] = { MP_OBJ_NEW_SMALL_INT(r->x), MP_OBJ_NEW_SMALL_INT(r->y),
                      MP_OBJ_NEW_SMALL_INT(r->scale), MP_OBJ_NEW_SMALL_INT(r->z) };
    return mp_obj_new_list(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_placement_obj, glass_placement);

static mp_obj_t glass_drop(mp_obj_t h) {
    moy_surface_drop(h_arg(h));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_drop_obj, glass_drop);

// sync(alive, prefix="win:"): drop every `prefix` surface not in `alive`.
static mp_obj_t glass_sync(size_t n_args, const mp_obj_t *a) {
    ready();
    const char *prefix = n_args > 1 ? mp_obj_str_get_str(a[1]) : "win:";
    mp_obj_t it = mp_getiter(a[0], NULL);
    const char *alive[MOY_GLASS_ROWS];
    size_t n = 0;
    mp_obj_t o;
    while ((o = mp_iternext(it)) != MP_OBJ_STOP_ITERATION && n < MOY_GLASS_ROWS) {
        alive[n++] = mp_obj_str_get_str(o);
    }
    moy_surface_sync(alive, n, prefix);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_sync_obj, 1, 2, glass_sync);

static mp_obj_t glass_kernel_epoch(void) {
    ready();
    return mp_obj_new_int_from_uint(moy_surface_kernel_epoch());
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_kernel_epoch_obj, glass_kernel_epoch);

static mp_obj_t glass_kernel_bump(void) {
    ready();
    moy_surface_kernel_bump();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_kernel_bump_obj, glass_kernel_bump);

// -- the panel's backlight -------------------------------------------------------
//
// backlight(on): the panel entry sprint 2 gave the recovery floor
// (MOY_KERNEL_PANEL), on every console.

#ifdef MOY_KERNEL_PANEL
extern void MOY_KERNEL_PANEL(backlight)(int on);

static mp_obj_t glass_backlight(size_t n_args, const mp_obj_t *a) {
    MOY_KERNEL_PANEL(backlight)(n_args < 1 || mp_obj_is_true(a[0]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_backlight_obj, 0, 1, glass_backlight);
#endif

#ifndef MOY_GLASS_BANDED
#define MOY_GLASS_BANDED 0
#endif

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

#if MOY_GLASS_BANDED || !defined(MOY_GLASS_BOARD)
#define MOY_GLASS_BANDED_TYPE 1

#if MOY_GLASS_BANDED
// A board: the panel module's kernel entries, called straight.
extern bool MOY_KERNEL_PANEL(wait)(void);
extern int MOY_KERNEL_PANEL(kick)(int n);
extern int MOY_KERNEL_PANEL(ship)(int n);

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

static void banded_light(mp_obj_t panel, bool on) {
    (void)panel;
    MOY_KERNEL_PANEL(backlight)(on);
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

static void banded_light(mp_obj_t panel, bool on) {
    mp_call_function_1(mp_load_attr(panel, MP_QSTR_backlight), mp_obj_new_bool(on));
}
#endif

typedef struct {
    mp_obj_base_t base;
    moy_banded_t b;
    moy_banded_ops_t ops;
    mp_obj_t panel;
    mp_obj_t gfx;
    mp_obj_t fbs[MOY_PRESENT_MAX_FBS];
    mp_int_t w, h;
} glass_banded_obj_t;

static const mp_obj_type_t glass_banded_type;

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
    banded_light(c->panel, n_args < 2 || mp_obj_is_true(a[1]));
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
};
static MP_DEFINE_CONST_DICT(banded_locals, banded_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    glass_banded_type, MP_QSTR_BandedCompositor, MP_TYPE_FLAG_NONE,
    make_new, banded_make_new,
    attr, banded_attr,
    locals_dict, &banded_locals
    );

#endif // MOY_GLASS_BANDED_TYPE

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

typedef struct {
    mp_obj_base_t base;
    moy_dsi_t d;
    moy_dsi_ops_t ops;
    mp_obj_t dsi;
    mp_obj_t ppa;
    mp_obj_t gfx;
    mp_obj_t fbs;               // list of the scan buffers' memoryviews
    mp_obj_t stamp;             // the deferred stamp's argument tuple, or None
    mp_int_t w, h;
} glass_dsi_obj_t;

static const mp_obj_type_t glass_dsi_type;

static mp_obj_t dsi_call0(mp_obj_t mod, qstr verb) {
    return mp_call_function_0(mp_load_attr(mod, verb));
}

static void dsi_show(void *ctx, int n) {
    glass_dsi_obj_t *c = ctx;
    mp_call_function_1(mp_load_attr(c->dsi, MP_QSTR_show), MP_OBJ_NEW_SMALL_INT(n));
}

static void dsi_msync(void *ctx) {
    dsi_call0(((glass_dsi_obj_t *)ctx)->dsi, MP_QSTR_flush);
}

static void dsi_ppa_sync(void *ctx) {
    glass_dsi_obj_t *c = ctx;
    if (c->ppa != mp_const_none) {
        dsi_call0(c->ppa, MP_QSTR_sync);
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

static uint32_t dsi_ticks(void *ctx) {
    (void)ctx;
    return (uint32_t)mp_hal_ticks_us();
}

static mp_obj_t dsi_make_new(const mp_obj_type_t *type, size_t n_args,
                             size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 1, 2, false);
    mp_obj_t dsi = args[0];
    glass_dsi_obj_t *c = mp_obj_malloc(glass_dsi_obj_t, type);
    c->dsi = dsi;
    c->ppa = n_args > 1 ? args[1] : mp_const_none;
    c->stamp = mp_const_none;
    // Dark until the first composed frame: a fresh panel scans noise.
    mp_call_function_1(mp_load_attr(dsi, MP_QSTR_backlight), mp_const_false);
    dsi_call0(dsi, MP_QSTR_init);
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
    c->ops.ticks_us = dsi_ticks;
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

// flush: kick the deferred window stamp as the frame's first op (a refused
// kick draws it on the CPU instead -- worst case one stale frame), then the
// engine's present.
static mp_obj_t dsi_flush(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    bool kicked = false;
    if (c->stamp != mp_const_none) {
        mp_obj_t st = c->stamp;
        c->stamp = mp_const_none;
        size_t n;
        mp_obj_t *items;
        mp_obj_get_array(st, &n, &items);
        nlr_buf_t nlr;
        if (nlr_push(&nlr) == 0) {
            mp_obj_t a[9];
            for (size_t i = 0; i < 8 && i < n; i++) {
                a[i] = items[i];
            }
            a[8] = MP_OBJ_NEW_SMALL_INT(1);
            mp_call_function_n_kw(mp_load_attr(c->ppa, MP_QSTR_blit_async), 9, 0, a);
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
    moy_dsi_fence(&((glass_dsi_obj_t *)MP_OBJ_TO_PTR(self_in))->d);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_sync_obj, dsi_sync);

// A compiled cart's frame snapshot has landed.
static mp_obj_t dsi_snap_fence(mp_obj_t self_in) {
    dsi_call0(((glass_dsi_obj_t *)MP_OBJ_TO_PTR(self_in))->ppa, MP_QSTR_snap_wait);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_snap_fence_obj, dsi_snap_fence);

// Nothing still writes or reads a compiled cart's frame copy.
static mp_obj_t dsi_frame_fence(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    dsi_call0(c->ppa, MP_QSTR_snap_wait);
    dsi_call0(c->ppa, MP_QSTR_sync);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_frame_fence_obj, dsi_frame_fence);

static mp_obj_t ppa_timeouts(mp_obj_t ppa) {
    if (ppa == mp_const_none) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    nlr_buf_t nlr;
    mp_obj_t t = MP_OBJ_NEW_SMALL_INT(0);
    if (nlr_push(&nlr) == 0) {
        t = mp_obj_subscr(dsi_call0(ppa, MP_QSTR_stats), MP_OBJ_NEW_SMALL_INT(2),
                          MP_OBJ_SENTINEL);
        nlr_pop();
    }
    return t;
}

// OVERLAP_FIELDS, cumulative since boot: (deferred, obsolete, fences,
// fence_us, game_n, game_us, ppa timeouts).
static mp_obj_t dsi_overlap_stats(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    moy_dsi_t *d = &c->d;
    mp_obj_t t[7] = {
        mp_obj_new_int_from_uint(d->deferred), mp_obj_new_int_from_uint(d->obsolete),
        mp_obj_new_int_from_uint(d->fences), mp_obj_new_int_from_uint(d->fence_us),
        mp_obj_new_int_from_uint(d->game_n), mp_obj_new_int_from_uint(d->game_us),
        ppa_timeouts(c->ppa),
    };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(dsi_overlap_stats_obj, dsi_overlap_stats);

static mp_obj_t dsi_underruns(mp_obj_t self_in) {
    glass_dsi_obj_t *c = MP_OBJ_TO_PTR(self_in);
    nlr_buf_t nlr;
    mp_obj_t r = mp_const_none;
    if (nlr_push(&nlr) == 0) {
        r = dsi_call0(c->dsi, MP_QSTR_underruns);
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
    { MP_ROM_QSTR(MP_QSTR_snap_fence), MP_ROM_PTR(&dsi_snap_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_fence), MP_ROM_PTR(&dsi_frame_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_overlap_stats), MP_ROM_PTR(&dsi_overlap_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_underruns), MP_ROM_PTR(&dsi_underruns_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_backlight), MP_ROM_PTR(&dsi_set_backlight_obj) },
};
static MP_DEFINE_CONST_DICT(dsi_locals, dsi_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    glass_dsi_type, MP_QSTR_DsiCompositor, MP_TYPE_FLAG_NONE,
    make_new, dsi_make_new,
    attr, dsi_attr,
    locals_dict, &dsi_locals
    );

// -- present: the rotated DSI compositor (moy_present.h) -----------------------------
//
// RotatedCompositor(dsi, ppa, angle=90): the Guition P4's landscape desk over
// its portrait panel, the frame state machine in C, its transport the two
// modules passed in. The canvas hands each frame's game composite in with
// mark_game (its buffers and its paint()/quiet() callables) and the WM its
// damage with note_damage; the deferred window stamp is `_stamp_pending`.

typedef struct {
    mp_obj_base_t base;
    moy_rot_t r;
    moy_rot_ops_t ops;
    mp_obj_t dsi, ppa, gfx;
    mp_obj_t bufs[10];          // by the engine's buffer ids
    mp_obj_t paint, quiet;      // this frame's game callables
    mp_obj_t stamp;             // the deferred stamp's tuple, or None
    mp_obj_t held;              // the glass Bufs the paint buffers and scratch are
    mp_obj_t rotate, rotate_scale, bounce, refreshes;   // the transport's verbs
    size_t scratch_n;
} glass_rot_obj_t;

static const mp_obj_type_t glass_rot_type;

static void rot_show(void *ctx, int n) {
    glass_rot_obj_t *c = ctx;
    mp_call_function_1(mp_load_attr(c->dsi, MP_QSTR_show), MP_OBJ_NEW_SMALL_INT(n));
}

static int32_t rot_refreshes(void *ctx) {
    return (int32_t)mp_obj_get_int(mp_call_function_0(((glass_rot_obj_t *)ctx)->refreshes));
}

static void rot_ppa_sync(void *ctx) {
    dsi_call0(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_sync);
}

static bool rot_ppa_done(void *ctx) {
    return mp_obj_is_true(dsi_call0(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_done));
}

static void rot_ppa_wait(void *ctx, int keep) {
    mp_call_function_1(mp_load_attr(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_wait),
                       MP_OBJ_NEW_SMALL_INT(keep));
}

static void rot_snap_wait(void *ctx) {
    dsi_call0(((glass_rot_obj_t *)ctx)->ppa, MP_QSTR_snap_wait);
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
    c->dsi = dsi;
    c->ppa = ppa;
    c->stamp = mp_const_none;
    c->paint = c->quiet = mp_const_none;
    c->held = mp_obj_new_list(0, NULL);
    c->scratch_n = 0;
    for (int i = 0; i < 10; i++) {
        c->bufs[i] = mp_const_none;
    }
    mp_call_function_1(mp_load_attr(dsi, MP_QSTR_backlight), mp_const_false);
    dsi_call0(dsi, MP_QSTR_init);
    if (!mp_obj_is_true(dsi_call0(ppa, MP_QSTR_init))) {
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
    c->ops.ticks_us = dsi_ticks;
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

static mp_obj_t rot_flush(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
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
    moy_rot_fence(&((glass_rot_obj_t *)MP_OBJ_TO_PTR(self_in))->r);
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

// OVERLAP_FIELDS: obsolete is None -- this path never drops a queued show.
static mp_obj_t rot_overlap_stats(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    moy_rot_t *r = &c->r;
    mp_obj_t t[7] = {
        mp_obj_new_int_from_uint(r->def_n), mp_const_none,
        mp_obj_new_int_from_uint(r->fences), mp_obj_new_int_from_uint(r->fence_us),
        mp_obj_new_int_from_uint(r->wait_n), mp_obj_new_int_from_uint(r->wait_us),
        ppa_timeouts(c->ppa),
    };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rot_overlap_stats_obj, rot_overlap_stats);

static mp_obj_t rot_underruns(mp_obj_t self_in) {
    glass_rot_obj_t *c = MP_OBJ_TO_PTR(self_in);
    nlr_buf_t nlr;
    mp_obj_t r = mp_const_none;
    if (nlr_push(&nlr) == 0) {
        r = dsi_call0(c->dsi, MP_QSTR_underruns);
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
        if (attr == MP_QSTR_strip_h) {
            c->r.strip_h = mp_obj_get_int(dest[1]);
            dest[0] = MP_OBJ_NULL;
        } else if (attr == MP_QSTR__stamp_pending) {
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
        case MP_QSTR_strip_h:
            dest[0] = I(c->r.strip_h);
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

static MP_DEFINE_CONST_OBJ_TYPE(
    glass_rot_type, MP_QSTR_RotatedCompositor, MP_TYPE_FLAG_NONE,
    make_new, rot_make_new,
    attr, rot_attr,
    locals_dict, &rot_locals
    );

#undef I

// -- the VM's teardown (moy_kernel.c's soft-reset exit) -----------------------------

#ifdef MOY_GLASS_BOARD
#if MOY_GLASS_BANDED
#include "moy_fold.h"
#endif

bool moy_gfx_k_copy_wait(void);
__attribute__((weak)) void moy_ppa_k_sync(void);    // the P4s' PPA

// Before the sweep: the feeder drained, the fold's snapshot landed and its
// latches cleared, the async layer copy waited out -- nothing the kernel's
// present started still reads a buffer the sweep frees. The next present
// after the restart is the kernel's own; nothing of the old VM's is held.
void moy_glass_vm_stop(void) {
    #if MOY_GLASS_BANDED
    MOY_KERNEL_PANEL(wait)();
    moy_fold_fence();
    moy_fold_reset();
    #endif
    if (moy_ppa_k_sync != NULL) {
        moy_ppa_k_sync();
    }
    moy_gfx_k_copy_wait();
}

// After the sweep: every Buf and Canvas the VM held was finalised (its row
// given back); the lifetimes it named are ended.
void moy_glass_vm_swept(void) {
    if (moy_glass_ready()) {
        moy_glass_end_owners();
    }
}
#endif

// -- the kernel's frame (native/moy_kernel/moy_loop.c) ------------------------------
//
// loop_bind(comp): the compositor the loop's stages drive -- present_pending
// before the console's upcalls, the fence before first light and on a frame
// that drew nothing, the panel light, the overlap counters PERF reads. A root
// pointer for the VM's life, like every VM object the kernel reaches.

MP_REGISTER_ROOT_POINTER(mp_obj_t moy_glass_loop_comp);

#if MOY_GLASS_BANDED || !defined(MOY_GLASS_BOARD)
#define LOOP_BANDED(c) mp_obj_is_type((c), &glass_banded_type)
#else
#define LOOP_BANDED(c) false
#endif

static mp_obj_t glass_loop_bind(mp_obj_t comp) {
    MP_STATE_VM(moy_glass_loop_comp) = comp;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_loop_bind_obj, glass_loop_bind);

void moy_glass_loop_clear(void) {
    MP_STATE_VM(moy_glass_loop_comp) = MP_OBJ_NULL;
}

static mp_obj_t loop_comp(void) {
    mp_obj_t c = MP_STATE_VM(moy_glass_loop_comp);
    return c == mp_const_none ? MP_OBJ_NULL : c;
}

void moy_glass_loop_present(void) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return;
    }
    if (mp_obj_is_type(c, &glass_dsi_type)) {
        moy_dsi_present_pending(&((glass_dsi_obj_t *)MP_OBJ_TO_PTR(c))->d);
    } else if (mp_obj_is_type(c, &glass_rot_type)) {
        moy_rot_present_pending(&((glass_rot_obj_t *)MP_OBJ_TO_PTR(c))->r);
    }
}

void moy_glass_loop_fence(void) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return;
    }
    #if MOY_GLASS_BANDED || !defined(MOY_GLASS_BOARD)
    if (LOOP_BANDED(c)) {
        moy_banded_fence(&((glass_banded_obj_t *)MP_OBJ_TO_PTR(c))->b);
        return;
    }
    #endif
    if (mp_obj_is_type(c, &glass_dsi_type)) {
        moy_dsi_fence(&((glass_dsi_obj_t *)MP_OBJ_TO_PTR(c))->d);
    } else if (mp_obj_is_type(c, &glass_rot_type)) {
        moy_rot_fence(&((glass_rot_obj_t *)MP_OBJ_TO_PTR(c))->r);
    }
}

// A frame that drew nothing: a banded panel's queued bands drained.
void moy_glass_loop_idle(void) {
    mp_obj_t c = loop_comp();
    #if MOY_GLASS_BANDED || !defined(MOY_GLASS_BOARD)
    if (c != MP_OBJ_NULL && LOOP_BANDED(c)) {
        moy_banded_fence(&((glass_banded_obj_t *)MP_OBJ_TO_PTR(c))->b);
    }
    #else
    (void)c;
    #endif
}

// The light on or off; a level between is the dim rung's, which a binary
// light shows as on. Answers false: no compositor here dims.
bool moy_glass_loop_light(int level) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return false;
    }
    bool on = level > 0;
    #if MOY_GLASS_BANDED || !defined(MOY_GLASS_BOARD)
    if (LOOP_BANDED(c)) {
        banded_light(((glass_banded_obj_t *)MP_OBJ_TO_PTR(c))->panel, on);
        return false;
    }
    #endif
    mp_call_function_1(mp_load_attr(c, MP_QSTR_set_backlight), mp_obj_new_bool(on));
    return false;
}

// The DSI compositors' overlap counters (moy_present.h's order); false on a
// compositor that has none. `has` a bit per slot measured.
bool moy_glass_loop_overlap(uint32_t v[7], uint8_t *has) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return false;
    }
    mp_obj_t t;
    if (mp_obj_is_type(c, &glass_dsi_type)) {
        t = dsi_overlap_stats(c);
    } else if (mp_obj_is_type(c, &glass_rot_type)) {
        t = rot_overlap_stats(c);
    } else {
        return false;
    }
    size_t n;
    mp_obj_t *items;
    mp_obj_tuple_get(t, &n, &items);
    *has = 0;
    for (size_t i = 0; i < n && i < 7; i++) {
        if (items[i] != mp_const_none) {
            v[i] = (uint32_t)mp_obj_get_int_truncated(items[i]);
            *has |= (uint8_t)(1u << i);
        }
    }
    return true;
}

// -- the module -----------------------------------------------------------------

static const mp_rom_map_elem_t glass_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_glass) },
    { MP_ROM_QSTR(MP_QSTR_buf), MP_ROM_PTR(&glass_buf_obj) },
    { MP_ROM_QSTR(MP_QSTR_loop_bind), MP_ROM_PTR(&glass_loop_bind_obj) },
    { MP_ROM_QSTR(MP_QSTR_Canvas), MP_ROM_PTR(&glass_canvas_type) },
    { MP_ROM_QSTR(MP_QSTR_owner), MP_ROM_PTR(&glass_owner_obj) },
    { MP_ROM_QSTR(MP_QSTR_reclaim), MP_ROM_PTR(&glass_reclaim_obj) },
    { MP_ROM_QSTR(MP_QSTR_owner_end), MP_ROM_PTR(&glass_owner_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_row), MP_ROM_PTR(&glass_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_holder), MP_ROM_PTR(&glass_set_holder_obj) },
    { MP_ROM_QSTR(MP_QSTR_rows), MP_ROM_PTR(&glass_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&glass_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_pool_bound), MP_ROM_PTR(&glass_set_pool_bound_obj) },
    { MP_ROM_QSTR(MP_QSTR_evict), MP_ROM_PTR(&glass_evict_obj) },
    { MP_ROM_QSTR(MP_QSTR_surface), MP_ROM_PTR(&glass_surface_obj) },
    { MP_ROM_QSTR(MP_QSTR_surface_find), MP_ROM_PTR(&glass_surface_find_obj) },
    { MP_ROM_QSTR(MP_QSTR_touch), MP_ROM_PTR(&glass_touch_obj) },
    { MP_ROM_QSTR(MP_QSTR_move), MP_ROM_PTR(&glass_move_obj) },
    { MP_ROM_QSTR(MP_QSTR_animating), MP_ROM_PTR(&glass_animating_obj) },
    { MP_ROM_QSTR(MP_QSTR_epoch), MP_ROM_PTR(&glass_epoch_obj) },
    { MP_ROM_QSTR(MP_QSTR_content_gen), MP_ROM_PTR(&glass_content_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_gens), MP_ROM_PTR(&glass_gens_obj) },
    { MP_ROM_QSTR(MP_QSTR_mint), MP_ROM_PTR(&glass_mint_obj) },
    { MP_ROM_QSTR(MP_QSTR_place), MP_ROM_PTR(&glass_place_obj) },
    { MP_ROM_QSTR(MP_QSTR_placement), MP_ROM_PTR(&glass_placement_obj) },
    { MP_ROM_QSTR(MP_QSTR_drop), MP_ROM_PTR(&glass_drop_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&glass_sync_obj) },
    { MP_ROM_QSTR(MP_QSTR_kernel_epoch), MP_ROM_PTR(&glass_kernel_epoch_obj) },
    { MP_ROM_QSTR(MP_QSTR_kernel_bump), MP_ROM_PTR(&glass_kernel_bump_obj) },
    #ifdef MOY_KERNEL_PANEL
    { MP_ROM_QSTR(MP_QSTR_backlight), MP_ROM_PTR(&glass_backlight_obj) },
    #endif
    { MP_ROM_QSTR(MP_QSTR_DsiCompositor), MP_ROM_PTR(&glass_dsi_type) },
    { MP_ROM_QSTR(MP_QSTR_RotatedCompositor), MP_ROM_PTR(&glass_rot_type) },
    #ifdef MOY_GLASS_BANDED_TYPE
    { MP_ROM_QSTR(MP_QSTR_BandedCompositor), MP_ROM_PTR(&glass_banded_type) },
    #endif
    { MP_ROM_QSTR(MP_QSTR_ROLE_LAYER), MP_ROM_INT(MOY_ROLE_LAYER) },
    { MP_ROM_QSTR(MP_QSTR_ROLE_BAKE), MP_ROM_INT(MOY_ROLE_BAKE) },
    { MP_ROM_QSTR(MP_QSTR_ROLE_SCRATCH), MP_ROM_INT(MOY_ROLE_SCRATCH) },
    { MP_ROM_QSTR(MP_QSTR_ROLE_CACHE), MP_ROM_INT(MOY_ROLE_CACHE) },
    { MP_ROM_QSTR(MP_QSTR_ROLE_PAINT), MP_ROM_INT(MOY_ROLE_PAINT) },
    { MP_ROM_QSTR(MP_QSTR_ROLE_POOL), MP_ROM_INT(MOY_ROLE_POOL) },
    { MP_ROM_QSTR(MP_QSTR_ORIGIN_HEAP), MP_ROM_INT(MOY_ORIGIN_HEAP) },
    { MP_ROM_QSTR(MP_QSTR_ORIGIN_POOL), MP_ROM_INT(MOY_ORIGIN_POOL) },
    { MP_ROM_QSTR(MP_QSTR_ORIGIN_ALLOC), MP_ROM_INT(MOY_ORIGIN_ALLOC) },
    { MP_ROM_QSTR(MP_QSTR_CLASS_KERNEL), MP_ROM_INT(MOY_CLASS_KERNEL) },
    { MP_ROM_QSTR(MP_QSTR_CLASS_CART), MP_ROM_INT(MOY_CLASS_CART) },
    { MP_ROM_QSTR(MP_QSTR_ROWS), MP_ROM_INT(MOY_GLASS_ROWS) },
};
static MP_DEFINE_CONST_DICT(glass_globals, glass_globals_table);

const mp_obj_module_t mp_module_moy_glass = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&glass_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_glass, mp_module_moy_glass);
