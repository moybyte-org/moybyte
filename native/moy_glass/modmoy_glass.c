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

#if __has_include("esp_heap_caps.h")
#define MOY_GLASS_BOARD 1
#include "esp_heap_caps.h"
#else
#include <stdlib.h>
#endif

#include "moy_buf.h"
#include "moy_canvas.h"
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

// -- the module -----------------------------------------------------------------

static const mp_rom_map_elem_t glass_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_glass) },
    { MP_ROM_QSTR(MP_QSTR_buf), MP_ROM_PTR(&glass_buf_obj) },
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
