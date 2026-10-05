// moy_rsprobe: a MicroPython module over the Rust probe staticlib (#224 sprint
// 1a). It exists to prove the library links into the target's real build and
// that every export is callable, not to be shipped.
#include "py/runtime.h"
#include <stdint.h>
#include <stddef.h>

int moy_rs_probe(int x);
uint64_t moy_rs_probe_u64(uint64_t a, uint64_t b);
float moy_rs_probe_f32(float x, float y);
uint32_t moy_rs_probe_atomic(uint32_t *p);
uint32_t moy_rs_probe_load_u32(const uint8_t *p);
uint8_t moy_rs_probe_at(const uint8_t *p, size_t n, size_t i);
uint32_t moy_rs_probe_sum(const uint8_t *p, size_t n);

static mp_obj_t rs_probe(mp_obj_t x) {
    return mp_obj_new_int(moy_rs_probe(mp_obj_get_int(x)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(rs_probe_obj, rs_probe);

static mp_obj_t rs_u64(mp_obj_t a, mp_obj_t b) {
    return mp_obj_new_int_from_ull(
        moy_rs_probe_u64(mp_obj_get_int_truncated(a), mp_obj_get_int_truncated(b)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(rs_u64_obj, rs_u64);

static mp_obj_t rs_f32(mp_obj_t x, mp_obj_t y) {
    return mp_obj_new_float((mp_float_t)moy_rs_probe_f32((float)mp_obj_get_float(x), (float)mp_obj_get_float(y)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(rs_f32_obj, rs_f32);

// atomic(n): fetch-add on a counter n times; returns the final counter.
static mp_obj_t rs_atomic(mp_obj_t n) {
    static uint32_t counter;
    for (mp_int_t i = mp_obj_get_int(n); i > 0; i--) {
        moy_rs_probe_atomic(&counter);
    }
    return mp_obj_new_int_from_uint(counter);
}
static MP_DEFINE_CONST_FUN_OBJ_1(rs_atomic_obj, rs_atomic);

// load(buf, off): an unaligned 32-bit read at buf[off:off+4].
static mp_obj_t rs_load(mp_obj_t buf, mp_obj_t off) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    mp_uint_t o = mp_obj_get_int(off);
    if (o + 4 > b.len) {
        mp_raise_ValueError(NULL);
    }
    return mp_obj_new_int_from_uint(moy_rs_probe_load_u32((const uint8_t *)b.buf + o));
}
static MP_DEFINE_CONST_FUN_OBJ_2(rs_load_obj, rs_load);

static mp_obj_t rs_sum(mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return mp_obj_new_int_from_uint(moy_rs_probe_sum(b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_1(rs_sum_obj, rs_sum);

// at(buf, i): buf[i] through the Rust bounds check; out of range aborts.
static mp_obj_t rs_at(mp_obj_t buf, mp_obj_t i) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_READ);
    return mp_obj_new_int(moy_rs_probe_at(b.buf, b.len, mp_obj_get_int(i)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(rs_at_obj, rs_at);

static const mp_rom_map_elem_t rsprobe_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_rsprobe)},
    {MP_ROM_QSTR(MP_QSTR_probe), MP_ROM_PTR(&rs_probe_obj)},
    {MP_ROM_QSTR(MP_QSTR_u64), MP_ROM_PTR(&rs_u64_obj)},
    {MP_ROM_QSTR(MP_QSTR_f32), MP_ROM_PTR(&rs_f32_obj)},
    {MP_ROM_QSTR(MP_QSTR_atomic), MP_ROM_PTR(&rs_atomic_obj)},
    {MP_ROM_QSTR(MP_QSTR_load), MP_ROM_PTR(&rs_load_obj)},
    {MP_ROM_QSTR(MP_QSTR_sum), MP_ROM_PTR(&rs_sum_obj)},
    {MP_ROM_QSTR(MP_QSTR_at), MP_ROM_PTR(&rs_at_obj)},
};
static MP_DEFINE_CONST_DICT(rsprobe_globals, rsprobe_globals_table);

const mp_obj_module_t rsprobe_module = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&rsprobe_globals,
};
MP_REGISTER_MODULE(MP_QSTR_moy_rsprobe, rsprobe_module);
