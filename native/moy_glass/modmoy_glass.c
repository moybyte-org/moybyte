// moy_glass's MicroPython binding: the glass's tables (moy_buf.h, moy_canvas.h)
// as the module `moy_glass`, the same names the host's ctypes
// binding (runtime/host/glass_binding.py) gives CPython.
//
//   buf(nbytes, role, owner=0) -> Buf     a BUF row and its bytes
//   Canvas(w, h, caps=0, owner=0)         a CANVAS row
//   owner(tag, cls=CLASS_CART) -> h       an OWNER row
//   reclaim(owner, roles=0), owner_end(owner)
//   row(h), loans(owner), pool(), stats(), set_pool_bound(n), evict()
//   kernel_epoch(), kernel_bump()
//
// A Buf holds its row: `view` is a writable memoryview of its bytes, and when
// the row goes -- release(), its owner's end or reclaim, or the Buf's own
// collection -- the view is neutered (length 0, no items) in that same call, so
// a draw through a view a canvas still holds writes nothing instead of writing
// memory the pool has lent again. A slice taken of the view is a view of its
// own and is not neutered: nothing keeps one. A HEAP row's bytes are a bytearray
// the Buf holds: the last resort when PSRAM refuses even after the pool is
// evicted, counted so the gate sees it.
//
// A Canvas holds its row: `state` and `pal` are views of the row's draw state
// and colour table, which the draw gates read in C (native/moy_gfx), and
// point(buf) is where it draws. Every table byte is PSRAM on a board
// (native/moy_kernel/moy_kernel.h); pixels are DMA-reachable PSRAM.
//
// The compositors' bindings are files of their own, one per moy_present_*.c
// (modmoy_glass_banded.c, modmoy_glass_dsi.c, modmoy_glass_rot.c), sharing
// modmoy_glass_int.h with this one.

#include "modmoy_glass_int.h"

#ifndef MOY_GLASS_POOL_BYTES
#define MOY_GLASS_POOL_BYTES (4u * 1024u * 1024u)
#endif

// A gc-heap fallback this big collects first: the run it needs is contiguous.
#define HEAP_COMPACT_BYTES (128u * 1024u)

// -- the allocators -------------------------------------------------------------

#ifdef MOY_GLASS_BOARD
// The kernel's small-block pool (moy_kernel.h), where the image has it.
void *moy_kpool_alloc(size_t n) __attribute__((weak));
bool moy_kpool_free(void *p) __attribute__((weak));

static void *tab_alloc(size_t n) {
    n = n ? n : 1u;
    if (moy_kpool_alloc != NULL) {
        void *q = moy_kpool_alloc(n);
        if (q != NULL) {
            return q;
        }
    }
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0) {
        p = heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
    }
    return p;
}

static void tab_release(void *p, size_t n) {
    (void)n;
    if (moy_kpool_free != NULL && moy_kpool_free(p)) {
        return;
    }
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

static const mp_obj_type_t glass_buf_type;

#if !MICROPY_ENABLE_FINALISER
#error "moy_glass: a Buf's finaliser takes it out of buf_at before it is freed"
#endif

// The Buf holding each BUF slot's row, so an owner's end or reclaim neuters the
// views of the rows it returned before anything can draw through them. Not a
// gc root: a Buf's finaliser takes it out (buf_drop) before it is freed.
static glass_buf_obj_t *buf_at[MOY_GLASS_ROWS];

static uint32_t buf_slot(uint32_t h) {
    return h & (MOY_GLASS_ROWS - 1u);
}

static void buf_gone(glass_buf_obj_t *b) {
    if (b->h != 0u && buf_at[buf_slot(b->h)] == b) {
        buf_at[buf_slot(b->h)] = NULL;
    }
    b->h = 0u;
    neuter(b->view);
    b->heap = MP_OBJ_NULL;
}

static void buf_hold(glass_buf_obj_t *b, uint32_t h) {
    glass_buf_obj_t *was = buf_at[buf_slot(h)];
    if (was != NULL) {
        buf_gone(was);          // its row went without a sweep: the slot is reused
    }
    b->h = h;
    buf_at[buf_slot(h)] = b;
}

// After an owner's end or reclaim: every Buf whose row went is neutered now.
static void bufs_sweep(void) {
    for (uint32_t s = 0u; s < MOY_GLASS_ROWS; s++) {
        glass_buf_obj_t *b = buf_at[s];
        moy_buf_row_t *r;
        if (b != NULL && moy_buf_get(b->h, &r) != MOY_GLASS_OK) {
            buf_gone(b);
        }
    }
}

static void buf_drop(glass_buf_obj_t *b) {
    uint32_t h = b->h;
    buf_gone(b);
    if (h != 0u) {
        moy_buf_release(h);
    }
}

static mp_obj_t buf_release(mp_obj_t self_in) {
    buf_drop(MP_OBJ_TO_PTR(self_in));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(buf_release_obj, buf_release);

static moy_buf_row_t *buf_row(glass_buf_obj_t *b) {
    moy_buf_row_t *r;
    if (b->h == 0u || moy_buf_get(b->h, &r) != MOY_GLASS_OK) {
        buf_gone(b);
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
mp_obj_t glass_buf(size_t n_args, const mp_obj_t *a) {
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
        buf_hold(b, h);
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
    buf_hold(b, h);
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
    bufs_sweep();
    if (rc != MOY_GLASS_OK) {
        raise_rc(rc, "owner");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_reclaim_obj, 1, 2, glass_reclaim);

static mp_obj_t glass_owner_end(mp_obj_t h) {
    ready();
    int rc = moy_owner_end(h_arg(h));
    bufs_sweep();
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

// -- the kernel epoch -----------------------------------------------------------

static mp_obj_t glass_kernel_epoch(void) {
    ready();
    return mp_obj_new_int_from_uint(moy_glass_kernel_epoch());
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_kernel_epoch_obj, glass_kernel_epoch);

static mp_obj_t glass_kernel_bump(void) {
    ready();
    moy_glass_kernel_bump();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_kernel_bump_obj, glass_kernel_bump);

// -- the theme's block and the cart boundary (moy_canvas.h) --------------------

// block(words): the live theme's 64 RGB565 words, in this build's wire order,
// into every canvas row's entries 64..127; None clears it.
static mp_obj_t glass_block(mp_obj_t words) {
    if (words == mp_const_none) {
        moy_canvas_block(NULL);
        return mp_const_none;
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(words, &bi, MP_BUFFER_READ);
    if (bi.len != MOY_CANVAS_BLOCK * 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("block"));
    }
    moy_canvas_block((const uint16_t *)bi.buf);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_block_obj, glass_block);

// cart(on): the cart boundary's colour mask, 63 while a cart's code runs.
static mp_obj_t glass_cart(mp_obj_t on) {
    moy_canvas_cart(mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(glass_cart_obj, glass_cart);

// cmask(): a one-byte view of the mask, which the canvas class's Python verbs read.
static mp_obj_t glass_cmask(void) {
    return mp_obj_new_bytearray_by_ref(1, &moy_canvas_cmask);
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_cmask_obj, glass_cmask);

// block_view(): the block's 64 words as a view.
static mp_obj_t glass_block_view(void) {
    return row_view('H', MOY_CANVAS_BLOCK, moy_canvas_block_words());
}
static MP_DEFINE_CONST_FUN_OBJ_0(glass_block_view_obj, glass_block_view);

// -- the panel's backlight -------------------------------------------------------
//
// backlight(x=True): the panel entry the recovery floor lights with
// (MOY_KERNEL_PANEL), on every console -- True, False or a level (moy_light.h).

#ifdef MOY_KERNEL_PANEL
static mp_obj_t glass_backlight(size_t n_args, const mp_obj_t *a) {
    MOY_KERNEL_PANEL(backlight)(n_args < 1 ? MOY_LIGHT_FULL : moy_light_arg(a[0]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_backlight_obj, 0, 1, glass_backlight);
#endif

#if MOY_GLASS_PRESENT

// -- shift_last and copy_last_into: every compositor's ---------------------------
//
// shift_last(x, y, w, h, dx, dy) -> True once the rect of the buffer the last
// presented frame drew into is in the one this frame draws into, moved by
// (dx, dy) inside the rect (moy_gfx's mg_shift_rect: pixels that would leave
// it are dropped, the band the move exposes keeps what this buffer held, the
// caller draws it). Buffer coordinates. Across two buffers the 2D-DMA moves it
// where the transport has one (moy_ppa.fbcpy), queued, and every verb that
// reads or presents this buffer fences it first, as shift_fence() does; the
// CPU otherwise. With one buffer the last frame's is this one: in place on the
// CPU. False when there is no last frame to shift from (or the engine is busy
// with what the shift must not wait behind), and the caller draws the rect.
//
// copy_last_into(buf, stride, x, y, w, h[, lx, ly]) -> True once the rect of
// that buffer is at (lx, ly) of `buf`, an RGB565 picture `stride` px a row (a
// window's layer), clipped to both: blocking, the 2D-DMA where it takes the
// block, else the CPU.

// moy_gfx_kernels.h (moy_gfx is in every console).
void mg_shift_rect(uint16_t *dst, const uint16_t *src, size_t cap, int stride,
                   int rx, int ry, int rw, int rh, int dx, int dy);
int mg_shift_clip(int stride, int rows, int rx, int ry, int rw, int rh,
                  int dx, int dy, int *b);
void mg_copy_block(uint16_t *dst, size_t dcap, int dstride,
                   const uint16_t *src, size_t scap, int sstride,
                   int dx, int dy, int sx, int sy, int w, int h);
int mg_block_clip(int dw, int dh, int sw, int sh, int *b);

// The transport's 2D-DMA block copy (moy_ppa.fbcpy): True when it took it.
static bool glass_fbcpy(mp_obj_t ppa, mp_obj_t dst, mp_int_t dw, mp_int_t dh, mp_obj_t src,
                        mp_int_t sw, mp_int_t sh, const int *b, bool nb) {
    if (ppa == MP_OBJ_NULL || ppa == mp_const_none) {
        return false;
    }
    mp_obj_t m[15];
    mp_load_method_maybe(ppa, MP_QSTR_fbcpy, m);
    if (m[0] == MP_OBJ_NULL) {
        return false;
    }
    m[2] = dst;
    m[3] = mp_obj_new_int(dw);
    m[4] = mp_obj_new_int(dh);
    m[5] = mp_obj_new_int(b[0]);
    m[6] = mp_obj_new_int(b[1]);
    m[7] = src;
    m[8] = mp_obj_new_int(sw);
    m[9] = mp_obj_new_int(sh);
    m[10] = mp_obj_new_int(b[2]);
    m[11] = mp_obj_new_int(b[3]);
    m[12] = mp_obj_new_int(b[4]);
    m[13] = mp_obj_new_int(b[5]);
    m[14] = mp_obj_new_bool(nb);
    return mp_obj_is_true(mp_call_method_n_kw(13, 0, m));
}

// A shift still flying has landed and its rows are out of the CPU's cache.
void glass_shift_fence(mp_obj_t ppa, bool *fly) {
    if (*fly) {
        *fly = false;
        mp_obj_t m[2];
        mp_load_method_maybe(ppa, MP_QSTR_fbcpy_wait, m);
        if (m[0] != MP_OBJ_NULL) {
            mp_call_method_n_kw(0, 0, m);
        }
    }
}

// shift_last's body over `dst` (this frame's buffer) and `src` (the last
// frame's), both W x H: the block it moved in `b` (x, y, sx, sy, w, h); false
// when nothing moves. `fly`: the copy is queued.
bool glass_shift(mp_obj_t ppa, mp_obj_t dst, mp_obj_t src, mp_int_t W, mp_int_t H,
                 const mp_obj_t *a, bool *fly, int *b) {
    mp_buffer_info_t db, sb;
    mp_get_buffer_raise(dst, &db, MP_BUFFER_WRITE);
    mp_get_buffer_raise(src, &sb, MP_BUFFER_READ);
    size_t need = (size_t)W * (size_t)H * 2u;
    if (db.len < need || sb.len < need) {
        mp_raise_ValueError(MP_ERROR_TEXT("shift_last buffers"));
    }
    int x = (int)mp_obj_get_int(a[0]), y = (int)mp_obj_get_int(a[1]);
    int w = (int)mp_obj_get_int(a[2]), h = (int)mp_obj_get_int(a[3]);
    int dx = (int)mp_obj_get_int(a[4]), dy = (int)mp_obj_get_int(a[5]);
    if (!mg_shift_clip((int)W, (int)H, x, y, w, h, dx, dy, b)) {
        return false;
    }
    if (db.buf != sb.buf && glass_fbcpy(ppa, dst, W, H, src, W, H, b, true)) {
        *fly = true;
        return true;
    }
    mg_shift_rect(db.buf, sb.buf, (size_t)W * (size_t)H, (int)W, x, y, w, h, dx, dy);
    return true;
}

// copy_last_into's body: the rect of `src` (W x H) into the picture a[0].
void glass_copy_into(mp_obj_t ppa, mp_obj_t src, mp_int_t W, mp_int_t H,
                     size_t n_args, const mp_obj_t *a) {
    mp_buffer_info_t db, sb;
    mp_get_buffer_raise(a[0], &db, MP_BUFFER_WRITE);
    mp_get_buffer_raise(src, &sb, MP_BUFFER_READ);
    mp_int_t stride = mp_obj_get_int(a[1]);
    if (stride <= 0 || sb.len < (size_t)W * (size_t)H * 2u) {
        mp_raise_ValueError(MP_ERROR_TEXT("copy_last_into buffers"));
    }
    mp_int_t rows = (mp_int_t)(db.len / 2u / (size_t)stride);
    int b[6] = { n_args > 6 ? (int)mp_obj_get_int(a[6]) : 0,
                 n_args > 7 ? (int)mp_obj_get_int(a[7]) : 0,
                 (int)mp_obj_get_int(a[2]), (int)mp_obj_get_int(a[3]),
                 (int)mp_obj_get_int(a[4]), (int)mp_obj_get_int(a[5]) };
    if (!mg_block_clip((int)stride, (int)rows, (int)W, (int)H, b)) {
        return;
    }
    if (db.buf != sb.buf && glass_fbcpy(ppa, a[0], stride, rows, src, W, H, b, false)) {
        return;
    }
    mg_copy_block(db.buf, db.len / 2u, (int)stride, sb.buf, sb.len / 2u, (int)W,
                  b[0], b[1], b[2], b[3], b[4], b[5]);
}

#endif // MOY_GLASS_PRESENT

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
// given back, its buf_at entry gone); the lifetimes it named are ended, and
// buf_at names nothing of the freed heap.
void moy_glass_vm_swept(void) {
    memset(buf_at, 0, sizeof(buf_at));
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

#ifdef MOY_GLASS_BANDED_TYPE
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
    #if MOY_GLASS_PRESENT
    if (mp_obj_is_type(c, &glass_dsi_type)) {
        moy_dsi_present_pending(&((glass_dsi_obj_t *)MP_OBJ_TO_PTR(c))->d);
    } else if (mp_obj_is_type(c, &glass_rot_type)) {
        moy_rot_present_pending(&((glass_rot_obj_t *)MP_OBJ_TO_PTR(c))->r);
    }
    #endif
}

void moy_glass_loop_fence(void) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return;
    }
    #ifdef MOY_GLASS_BANDED_TYPE
    if (LOOP_BANDED(c)) {
        moy_banded_fence(&((glass_banded_obj_t *)MP_OBJ_TO_PTR(c))->b);
        return;
    }
    #endif
    #if MOY_GLASS_PRESENT
    if (mp_obj_is_type(c, &glass_dsi_type)) {
        moy_dsi_fence(&((glass_dsi_obj_t *)MP_OBJ_TO_PTR(c))->d);
    } else if (mp_obj_is_type(c, &glass_rot_type)) {
        moy_rot_fence(&((glass_rot_obj_t *)MP_OBJ_TO_PTR(c))->r);
    }
    #endif
}

// A frame that drew nothing: a banded panel's queued bands drained.
void moy_glass_loop_idle(void) {
    mp_obj_t c = loop_comp();
    #ifdef MOY_GLASS_BANDED_TYPE
    if (c != MP_OBJ_NULL && LOOP_BANDED(c)) {
        moy_banded_fence(&((glass_banded_obj_t *)MP_OBJ_TO_PTR(c))->b);
    }
    #else
    (void)c;
    #endif
}

// The light at `level` (moy_light.h). A console's panel entry takes the level
// and answers true; the desktop build's compositors take on or off, so a level
// between shows as on and it answers false.
bool moy_glass_loop_light(int level) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return false;
    }
    #ifdef MOY_KERNEL_PANEL
    MOY_KERNEL_PANEL(backlight)(level);
    return true;
    #else
    bool on = level > 0;
    #ifdef MOY_GLASS_BANDED_TYPE
    if (LOOP_BANDED(c)) {
        glass_banded_light(((glass_banded_obj_t *)MP_OBJ_TO_PTR(c))->panel, on);
        return false;
    }
    #endif
    mp_call_function_1(mp_load_attr(c, MP_QSTR_set_backlight), mp_obj_new_bool(on));
    return false;
    #endif
}

// The DSI compositors' overlap counters (moy_present.h's order); false on a
// compositor that has none. `has` a bit per slot measured.
bool moy_glass_loop_overlap(uint32_t v[7], uint8_t *has) {
    mp_obj_t c = loop_comp();
    if (c == MP_OBJ_NULL) {
        return false;
    }
    mp_obj_t t;
    #if MOY_GLASS_PRESENT
    if (mp_obj_is_type(c, &glass_dsi_type)) {
        t = glass_dsi_overlap_stats(c);
    } else if (mp_obj_is_type(c, &glass_rot_type)) {
        t = glass_rot_overlap_stats(c);
    } else
    #endif
    {
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

// -- the kernel run in front (native/moy_play's moy_play.h) ------------------------

#if MOY_GLASS_PRESENT
//
// front(comp[, game]) gives the kernel's Player this compositor as the two
// ends of a run in the loop's foreground: where the run's frame is drawn and
// how it reaches the glass. A banded compositor's game canvas IS the panel,
// drawn straight into the back buffer and presented as the console presents
// it. A DSI compositor's run draws into the game canvas `game` -- (buf, w, h,
// ox, oy, scale, smooth) -- which each present scales into the back buffer:
// the PPA's bilinear scale where the board has it and `smooth` holds, the
// CPU kernel's nearest one otherwise, then the switch. A banded panel larger
// than its game canvas (the Guition S3's) takes `game` and the fold's
// `scratch` -- (buf, w, h, ox, oy, scale, smooth, scratch) -- and each present
// snapshots the canvas into the scratch, which the flush resolves band by band
// with the bezel around it (moy_fold.h), as the console's blit_game does.
// front(None) withdraws.

typedef struct {
    uint16_t *(*canvas)(void *ctx, int *w, int *h);
    void (*present)(void *ctx, bool drew);
    bool (*map)(void *ctx, int32_t *x, int32_t *y);
    void *ctx;
} glass_front_ops_t;                    // moy_play.h's moy_front_ops_t, field for field

void moy_play_front_ops(const glass_front_ops_t *ops) __attribute__((weak));
int moy_ppa_k_scale(uint16_t *dst, int dw, int dh, int dx, int dy, const uint16_t *src,
                    int sw, int sh, int scale) __attribute__((weak));
// moy_gfx_kernels.h's nearest-neighbour upscale (moy_gfx is in every console).
void mg_blit565_scale(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                      const uint16_t *src, size_t scap, int sw, int sh, int scale);

static struct {
    glass_front_ops_t ops;
    void *comp;                         // the compositor object (rooted below)
    bool dsi;
    uint16_t *game;
    int gw, gh, ox, oy, scale;
    bool smooth;
    uint8_t *scr;                       // the fold's scratch (a banded game canvas)
    size_t scr_len;
} G;
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_glass_front_scr);
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_glass_front_comp);
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_glass_front_game);

static uint16_t *view_px(mp_obj_t view, size_t *n) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(view, &b, MP_BUFFER_RW);
    *n = b.len / 2u;
    return (uint16_t *)b.buf;
}

#ifdef MOY_GLASS_BANDED_TYPE
static uint16_t *front_banded_canvas(void *ctx, int *w, int *h) {
    glass_banded_obj_t *c = ctx;
    size_t n;
    *w = (int)c->w;
    *h = (int)c->h;
    return view_px(c->fbs[c->b.back], &n);
}

static void front_banded_present(void *ctx, bool drew) {
    glass_banded_obj_t *c = ctx;
    if (drew) {
        moy_banded_present(&c->b);      // a transport error is the next frame's
    }
}

static bool front_identity(void *ctx, int32_t *x, int32_t *y) {
    glass_banded_obj_t *c = ctx;
    return *x >= 0 && *y >= 0 && *x < c->w && *y < c->h;
}

#if MOY_GLASS_BANDED
// The run draws once the last snapshot has read the canvas.
static uint16_t *front_fold_canvas(void *ctx, int *w, int *h) {
    (void)ctx;
    moy_fold_snap_fence();
    *w = G.gw;
    *h = G.gh;
    return G.game;
}

static void front_fold_present(void *ctx, bool drew) {
    glass_banded_obj_t *c = ctx;
    if (!drew) {
        return;
    }
    moy_fold_fence();                   // the last flush has read the scratch
    bool async_copy = false;
    if (!moy_fold_arm_snap((const uint8_t *)G.game, (size_t)G.gw * (size_t)G.gh * 2u, 0,
                           G.scr, G.scr_len, G.gw, G.gh, 0, G.gw, G.ox, G.oy, G.scale,
                           (int)c->w, (int)c->h, &async_copy)) {
        // Geometry the fold cannot express: the bezel and the scale on the CPU.
        size_t n;
        uint16_t *dst = view_px(c->fbs[c->b.back], &n);
        memset(dst, 0, n * 2u);
        mg_blit565_scale(dst, n, (int)c->w, (int)c->h, G.ox, G.oy, G.game,
                         (size_t)G.gw * (size_t)G.gh, G.gw, G.gh, G.scale);
    }
    moy_banded_present(&c->b);
}
#endif
#endif

static uint16_t *front_dsi_canvas(void *ctx, int *w, int *h) {
    (void)ctx;
    *w = G.gw;
    *h = G.gh;
    return G.game;
}

static void front_dsi_present(void *ctx, bool drew) {
    glass_dsi_obj_t *c = ctx;
    if (!drew) {
        return;
    }
    size_t n;
    uint16_t *dst = view_px(mp_obj_subscr(c->fbs, MP_OBJ_NEW_SMALL_INT(c->d.back),
                                          MP_OBJ_SENTINEL), &n);
    if (!(G.smooth && moy_ppa_k_scale != NULL
          && moy_ppa_k_scale(dst, (int)c->w, (int)c->h, G.ox, G.oy, G.game, G.gw, G.gh,
                             G.scale) == 0)) {
        mg_blit565_scale(dst, n, (int)c->w, (int)c->h, G.ox, G.oy, G.game,
                         (size_t)G.gw * (size_t)G.gh, G.gw, G.gh, G.scale);
    }
    moy_dsi_present(&c->d, false);
}

static bool front_game_map(void *ctx, int32_t *x, int32_t *y) {
    (void)ctx;
    int32_t gx = (*x - G.ox) / G.scale, gy = (*y - G.oy) / G.scale;
    bool in = *x >= G.ox && *y >= G.oy && gx < G.gw && gy < G.gh;
    *x = gx;
    *y = gy;
    return in;
}

static mp_obj_t glass_front(size_t n_args, const mp_obj_t *a) {
    if (moy_play_front_ops == NULL) {
        return mp_const_false;          // no Player in this image
    }
    if (a[0] == mp_const_none) {
        moy_play_front_ops(NULL);
        MP_STATE_VM(moy_glass_front_comp) = MP_OBJ_NULL;
        MP_STATE_VM(moy_glass_front_game) = MP_OBJ_NULL;
        MP_STATE_VM(moy_glass_front_scr) = MP_OBJ_NULL;
        return mp_const_true;
    }
    memset(&G, 0, sizeof(G));
    if (false) {
#if MOY_GLASS_BANDED
    } else if (mp_obj_is_type(a[0], &glass_banded_type) && n_args > 1) {
        mp_obj_t *g;
        mp_obj_get_array_fixed_n(a[1], 8, &g);
        size_t n;
        G.game = view_px(g[0], &n);
        G.gw = (int)mp_obj_get_int(g[1]);
        G.gh = (int)mp_obj_get_int(g[2]);
        G.ox = (int)mp_obj_get_int(g[3]);
        G.oy = (int)mp_obj_get_int(g[4]);
        G.scale = (int)mp_obj_get_int(g[5]);
        mp_buffer_info_t sb;
        mp_get_buffer_raise(g[7], &sb, MP_BUFFER_WRITE);
        G.scr = sb.buf;
        G.scr_len = sb.len;
        glass_banded_obj_t *c = MP_OBJ_TO_PTR(a[0]);
        if (G.scale < 1 || n < (size_t)G.gw * (size_t)G.gh || G.ox < 0 || G.oy < 0
                || G.ox + G.gw * G.scale > c->w || G.oy + G.gh * G.scale > c->h) {
            mp_raise_ValueError(MP_ERROR_TEXT("front: game"));
        }
        G.ops.canvas = front_fold_canvas;
        G.ops.present = front_fold_present;
        G.ops.map = front_game_map;
        MP_STATE_VM(moy_glass_front_game) = g[0];
        MP_STATE_VM(moy_glass_front_scr) = g[7];
#endif
#ifdef MOY_GLASS_BANDED_TYPE
    } else if (mp_obj_is_type(a[0], &glass_banded_type)) {
        G.ops.canvas = front_banded_canvas;
        G.ops.present = front_banded_present;
        G.ops.map = front_identity;
#endif
    } else if (mp_obj_is_type(a[0], &glass_dsi_type) && n_args > 1) {
        mp_obj_t *g;
        mp_obj_get_array_fixed_n(a[1], 7, &g);
        size_t n;
        G.game = view_px(g[0], &n);
        G.gw = (int)mp_obj_get_int(g[1]);
        G.gh = (int)mp_obj_get_int(g[2]);
        G.ox = (int)mp_obj_get_int(g[3]);
        G.oy = (int)mp_obj_get_int(g[4]);
        G.scale = (int)mp_obj_get_int(g[5]);
        G.smooth = mp_obj_is_true(g[6]);
        if (G.scale < 1 || n < (size_t)G.gw * (size_t)G.gh) {
            mp_raise_ValueError(MP_ERROR_TEXT("front: game"));
        }
        G.dsi = true;
        // The bezel: every scan buffer black once, so none shows what the
        // screen before the run left around the game.
        glass_dsi_obj_t *c = MP_OBJ_TO_PTR(a[0]);
        size_t nf;
        mp_obj_t *fv;
        mp_obj_get_array(c->fbs, &nf, &fv);
        for (size_t i = 0; i < nf; i++) {
            size_t k;
            uint16_t *p = view_px(fv[i], &k);
            memset(p, 0, k * 2u);
        }
        G.ops.canvas = front_dsi_canvas;
        G.ops.present = front_dsi_present;
        G.ops.map = front_game_map;
        MP_STATE_VM(moy_glass_front_game) = g[0];
    } else {
        return mp_const_false;          // a compositor with no front (rotated)
    }
    G.ops.ctx = MP_OBJ_TO_PTR(a[0]);
    MP_STATE_VM(moy_glass_front_comp) = a[0];
    moy_play_front_ops(&G.ops);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(glass_front_obj, 1, 2, glass_front);

#endif // MOY_GLASS_PRESENT

static const mp_rom_map_elem_t glass_globals_table[] = {
    #if MOY_GLASS_PRESENT
    { MP_ROM_QSTR(MP_QSTR_front), MP_ROM_PTR(&glass_front_obj) },
    #endif
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
    { MP_ROM_QSTR(MP_QSTR_kernel_epoch), MP_ROM_PTR(&glass_kernel_epoch_obj) },
    { MP_ROM_QSTR(MP_QSTR_kernel_bump), MP_ROM_PTR(&glass_kernel_bump_obj) },
    { MP_ROM_QSTR(MP_QSTR_block), MP_ROM_PTR(&glass_block_obj) },
    { MP_ROM_QSTR(MP_QSTR_cart), MP_ROM_PTR(&glass_cart_obj) },
    { MP_ROM_QSTR(MP_QSTR_cmask), MP_ROM_PTR(&glass_cmask_obj) },
    { MP_ROM_QSTR(MP_QSTR_block_view), MP_ROM_PTR(&glass_block_view_obj) },
    #ifdef MOY_KERNEL_PANEL
    { MP_ROM_QSTR(MP_QSTR_backlight), MP_ROM_PTR(&glass_backlight_obj) },
    #endif
    #if MOY_GLASS_PRESENT
    { MP_ROM_QSTR(MP_QSTR_DsiCompositor), MP_ROM_PTR(&glass_dsi_type) },
    { MP_ROM_QSTR(MP_QSTR_RotatedCompositor), MP_ROM_PTR(&glass_rot_type) },
    #endif
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
