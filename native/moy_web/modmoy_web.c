// Moybyte moy_web: the browser console, baked into the firmware image.
//
// WHY THIS EXISTS. `moy_webhost` serves the wasm console over the board's own
// WiFi, from a copy of `firmware/web_runner/dist` that a human had put on the
// board's storage. That copy drifts and nothing detects it: on 2026-08-15 a
// board served a bundle old enough to still carry a desktop-blackout bug fixed
// in dist/ hours earlier. The P4 got a push tool; the T-Deck cannot even be
// pushed to, because the push hands the board a url over serial and that
// board's USB-CDC RX is dead under the desktop. So the image carries the
// bundle, and a console that boots is a console current with its firmware.
//
// WHAT IT COSTS: 572,693 B of the app slot for the four pre-GZIPPED assets
// (1,155,953 B raw would not fit the T-Deck at all). Both boards' build.sh
// print the remaining slot headroom and FAIL if the image outgrows the slot.
//
// ZERO-COPY, and that is the whole design of this module: `asset()` returns a
// READ-ONLY memoryview straight at the flash-mapped blob. Building a `bytes`
// per request would be ~523 KB of it for the wasm on a board with ~23 KB of
// internal SRAM free during play (#66) -- not a slow path, a path that does
// not run. The socket's sendall() reads the buffer protocol directly, so the
// bytes go flash -> lwip with nothing in between.

#include <string.h>

#include "py/obj.h"
#include "py/objarray.h"
#include "py/runtime.h"
#include "py/binary.h"

#include "moy_web_blob.h"

// asset(name) -> read-only memoryview of the baked bytes, or None.
//
// `name` is the served name including any `.gz` (see moy_web_blob.h). The
// memoryview is deliberately NOT marked writable (moy_alloc's `typecode |=
// 0x80` is the opposite case): the target is flash, so a write would fault,
// and read-only makes that a Python TypeError instead.
static mp_obj_t moy_web_asset(mp_obj_t name_in) {
    size_t nlen;
    const char *name = mp_obj_str_get_data(name_in, &nlen);
    for (unsigned int i = 0; i < moy_web_asset_count; i++) {
        const moy_web_asset_t *a = &moy_web_assets[i];
        if (strlen(a->name) == nlen && memcmp(a->name, name, nlen) == 0) {
            return mp_obj_new_memoryview(BYTEARRAY_TYPECODE, a->len,
                                         (void *)a->data);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_web_asset_obj, moy_web_asset);

// assets() -> tuple of every baked asset name. Empty on an image built with no
// bundle, which is what makes "was a console baked into this firmware?" a
// question the board can answer.
//
// NOT `names()`, and that is not taste. A qstr this module compiles in that
// the FROZEN Python also uses collides with mpy-tool's supplementary enum on a
// build tree whose frozen_content.c predates it ("redeclaration of enumerator
// MP_QSTR_names"). The boards regenerate that file on every build (their
// manifest carries a source fingerprint) but the unix test build does not, so
// a warm tree stops compiling. If a future verb here ever trips it anyway, the
// fix is to delete <port>/build-moybyte/frozen_content.c -- or, better, to
// pick a word the frozen console does not use.
static mp_obj_t moy_web_names(void) {
    mp_obj_t items[16];
    unsigned int n = moy_web_asset_count;
    if (n > 16) {
        n = 16;
    }
    for (unsigned int i = 0; i < n; i++) {
        items[i] = mp_obj_new_str(moy_web_assets[i].name,
                                  strlen(moy_web_assets[i].name));
    }
    return mp_obj_new_tuple(n, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_web_names_obj, moy_web_names);

// stamp() -> "<count> <total bytes> <digest>" for the bundle in this image.
//
// The point of a stamp is the question that started all this: which console is
// this board serving? On a board with a REPL that is one line -- `py
// moy_web.stamp()` -- and it answers with the bundle's own digest rather than
// with a build date that could be a day older than the files.
static mp_obj_t moy_web_stamp_fn(void) {
    return mp_obj_new_str(moy_web_stamp, strlen(moy_web_stamp));
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_web_stamp_obj, moy_web_stamp_fn);

// total() -> baked bytes, for the same diagnostics (and for a size guard that
// wants to report what the console is costing the slot).
static mp_obj_t moy_web_total(void) {
    mp_uint_t total = 0;
    for (unsigned int i = 0; i < moy_web_asset_count; i++) {
        total += moy_web_assets[i].len;
    }
    return mp_obj_new_int_from_uint(total);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_web_total_obj, moy_web_total);

static const mp_rom_map_elem_t moy_web_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_OBJ_NEW_QSTR(MP_QSTR_moy_web) },
    { MP_ROM_QSTR(MP_QSTR_asset),    MP_ROM_PTR(&moy_web_asset_obj) },
    { MP_ROM_QSTR(MP_QSTR_assets),   MP_ROM_PTR(&moy_web_names_obj) },
    { MP_ROM_QSTR(MP_QSTR_stamp),    MP_ROM_PTR(&moy_web_stamp_obj) },
    { MP_ROM_QSTR(MP_QSTR_total),    MP_ROM_PTR(&moy_web_total_obj) },
};

static MP_DEFINE_CONST_DICT(moy_web_globals, moy_web_globals_table);

const mp_obj_module_t mp_module_moy_web = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_web_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_web, mp_module_moy_web);
