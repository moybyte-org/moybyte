// moy_index's MicroPython binding: the native index (moy_index.h) as the
// module runtime/moy_index.py is, name for name -- Index, StaleHandle,
// SLOT_BITS, SLOTS, GEN_MAX. The same binding serves either twin; which one
// links under it is the build's MOY_INDEX_IMPL (tools/moy_index_spike.py).
//
// It registers EXTENSIBLE, so a moy_index.py on the import path wins over it.
// A build that takes a twin stages no such file (tools/board_config.py), and
// the desktop MicroPython's suites reach this one by importing it with the
// path emptied (tests/test_moy_index_twins.py).
//
// A path is a str; a handle is an int, and one outside 1 .. 2**30 - 1 names
// no row. The table's memory is the gc heap's: every block hangs off the
// Index object, so the collector keeps it while the object lives.

#include <string.h>

#include "py/mperrno.h"
#include "py/objexcept.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_index.h"

void *moy_index_host_alloc(size_t n) {
    void *p = m_malloc_maybe(n ? n : 1);
    if (p != NULL) {
        memset(p, 0, n);
    }
    return p;
}

void moy_index_host_free(void *p, size_t n) {
    if (p != NULL) {
        m_del(uint8_t, p, n ? n : 1);
    }
}

MP_DEFINE_EXCEPTION(StaleHandle, ValueError)

typedef struct {
    mp_obj_base_t base;
    moy_index_t *ix;
} index_obj_t;

static const mp_obj_type_t index_type;

static moy_index_t *ix_of(mp_obj_t self) {
    return ((index_obj_t *)MP_OBJ_TO_PTR(self))->ix;
}

static MP_NORETURN void raise_rc(int rc) {
    if (rc == MOY_INDEX_STALE) {
        mp_raise_msg(&mp_type_StaleHandle, MP_ERROR_TEXT("stale store handle"));
    }
    if (rc == MOY_INDEX_FULL) {
        mp_raise_OSError(MP_ENOSPC);
    }
    mp_raise_type(&mp_type_MemoryError);
}

static const char *path_arg(mp_obj_t p, size_t *len) {
    if (!mp_obj_is_str(p)) {
        mp_raise_TypeError(MP_ERROR_TEXT("store path must be a str"));
    }
    return mp_obj_str_get_data(p, len);
}

// The handle an int names, 0 for an int no row can have, TypeError for
// anything else. A bool is the int it is, as it is to the Python twin.
static bool handle_of(mp_obj_t h, uint32_t *out) {
    if (mp_obj_is_small_int(h)) {
        mp_int_t v = MP_OBJ_SMALL_INT_VALUE(h);
        *out = (v > 0 && v < ((mp_int_t)1 << 30)) ? (uint32_t)v : 0u;
        return true;
    }
    if (mp_obj_is_bool(h)) {
        *out = h == mp_const_true ? 1u : 0u;
        return true;
    }
    if (mp_obj_is_int(h)) {
        *out = 0u;
        return true;
    }
    return false;
}

static uint32_t handle_arg(mp_obj_t h) {
    uint32_t v;
    if (!handle_of(h, &v)) {
        mp_raise_TypeError(MP_ERROR_TEXT("store handle must be an int"));
    }
    return v;
}

// The table under an Index, for C that drives the ABI itself
// (bench_moy_index.c).
moy_index_t *moy_index_of(mp_obj_t index) {
    if (!mp_obj_is_type(index, &index_type)) {
        mp_raise_TypeError(MP_ERROR_TEXT("not a moy_index.Index"));
    }
    return ix_of(index);
}

static mp_obj_t index_make_new(const mp_obj_type_t *type, size_t n_args,
                               size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    index_obj_t *o = mp_obj_malloc(index_obj_t, type);
    o->ix = moy_index_new();
    if (o->ix == NULL) {
        raise_rc(MOY_INDEX_NOMEM);
    }
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t index_intern(mp_obj_t self, mp_obj_t path) {
    size_t len;
    const char *p = path_arg(path, &len);
    uint32_t h;
    int rc = moy_index_intern(ix_of(self), p, len, &h);
    if (rc != MOY_INDEX_OK) {
        raise_rc(rc);
    }
    return MP_OBJ_NEW_SMALL_INT(h);
}
static MP_DEFINE_CONST_FUN_OBJ_2(index_intern_obj, index_intern);

static mp_obj_t index_find(mp_obj_t self, mp_obj_t path) {
    size_t len;
    const char *p = path_arg(path, &len);
    return MP_OBJ_NEW_SMALL_INT(moy_index_find(ix_of(self), p, len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(index_find_obj, index_find);

static mp_obj_t index_path(mp_obj_t self, mp_obj_t h) {
    const char *p;
    size_t len;
    int rc = moy_index_path(ix_of(self), handle_arg(h), &p, &len);
    if (rc != MOY_INDEX_OK) {
        raise_rc(rc);
    }
    // The bytes came in as a str, so they are UTF-8 already: copied as they
    // are, without mp_obj_new_str's validation and search of the qstr pools.
    return mp_obj_new_str_copy(&mp_type_str, (const byte *)p, len);
}
static MP_DEFINE_CONST_FUN_OBJ_2(index_path_obj, index_path);

static mp_obj_t index_valid(mp_obj_t self, mp_obj_t h) {
    uint32_t v;
    return mp_obj_new_bool(handle_of(h, &v) && moy_index_valid(ix_of(self), v));
}
static MP_DEFINE_CONST_FUN_OBJ_2(index_valid_obj, index_valid);

static mp_obj_t index_release(mp_obj_t self, mp_obj_t h) {
    int rc = moy_index_release(ix_of(self), handle_arg(h));
    if (rc != MOY_INDEX_OK) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(index_release_obj, index_release);

static mp_obj_t index_handles(mp_obj_t self) {
    moy_index_t *ix = ix_of(self);
    mp_obj_list_t *out = MP_OBJ_TO_PTR(mp_obj_new_list(moy_index_count(ix), NULL));
    size_t k = 0;
    for (uint32_t s = 0, n = moy_index_slots(ix); s < n && k < out->len; s++) {
        uint32_t h = moy_index_at(ix, s);
        if (h) {
            out->items[k++] = MP_OBJ_NEW_SMALL_INT(h);
        }
    }
    return MP_OBJ_FROM_PTR(out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(index_handles_obj, index_handles);

static mp_obj_t index_count(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_index_count(ix_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(index_count_obj, index_count);


static const mp_rom_map_elem_t index_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_intern), MP_ROM_PTR(&index_intern_obj) },
    { MP_ROM_QSTR(MP_QSTR_find), MP_ROM_PTR(&index_find_obj) },
    { MP_ROM_QSTR(MP_QSTR_path), MP_ROM_PTR(&index_path_obj) },
    { MP_ROM_QSTR(MP_QSTR_valid), MP_ROM_PTR(&index_valid_obj) },
    { MP_ROM_QSTR(MP_QSTR_release), MP_ROM_PTR(&index_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_handles), MP_ROM_PTR(&index_handles_obj) },
    { MP_ROM_QSTR(MP_QSTR_count), MP_ROM_PTR(&index_count_obj) },
};
static MP_DEFINE_CONST_DICT(index_locals, index_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    index_type, MP_QSTR_Index, MP_TYPE_FLAG_NONE,
    make_new, index_make_new,
    locals_dict, &index_locals
    );

static const mp_rom_map_elem_t moy_index_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_index) },
    { MP_ROM_QSTR(MP_QSTR_Index), MP_ROM_PTR(&index_type) },
    { MP_ROM_QSTR(MP_QSTR_StaleHandle), MP_ROM_PTR(&mp_type_StaleHandle) },
    { MP_ROM_QSTR(MP_QSTR_SLOT_BITS), MP_ROM_INT(MOY_INDEX_SLOT_BITS) },
    { MP_ROM_QSTR(MP_QSTR_SLOTS), MP_ROM_INT(MOY_INDEX_SLOTS) },
    { MP_ROM_QSTR(MP_QSTR_GEN_MAX), MP_ROM_INT(MOY_INDEX_GEN_MAX) },
};
static MP_DEFINE_CONST_DICT(moy_index_globals, moy_index_globals_table);

const mp_obj_module_t moy_index_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_index_globals,
};

MP_REGISTER_EXTENSIBLE_MODULE(MP_QSTR_moy_index, moy_index_module);
