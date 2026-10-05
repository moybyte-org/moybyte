// moy_index_bench: the native index's hot path driven from C, with no VM
// between calls, for tools/moy_index_spike.py's `bench`, which reads moycore's
// counters on either side. Compiled in only under MOY_INDEX_BENCH=1, so no
// image the size table measures carries it.
//
//   run(index, paths, rounds, op) -> checksum
//
// `rounds` passes over every path in `paths` (all interned) of one op: 0
// intern of a live row (a rescan), 1 path() of its handle, 2 valid(), 3
// release then intern again. What precedes the loop is the fixed cost a
// `rounds=0` call measures.

#include "py/runtime.h"

#include "moy_index.h"

moy_index_t *moy_index_of(mp_obj_t index);

static mp_obj_t bench_run(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    moy_index_t *ix = moy_index_of(args[0]);
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(args[1], &n, &items);
    mp_int_t rounds = mp_obj_get_int(args[2]);
    mp_int_t op = mp_obj_get_int(args[3]);
    size_t cap = n ? n : 1;
    const char **ps = m_new(const char *, cap);
    size_t *ls = m_new(size_t, cap);
    uint32_t *hs = m_new(uint32_t, cap);
    for (size_t i = 0; i < n; i++) {
        ps[i] = mp_obj_str_get_data(items[i], &ls[i]);
        hs[i] = moy_index_find(ix, ps[i], ls[i]);
    }
    uint32_t sum = 0;
    for (mp_int_t r = 0; r < rounds; r++) {
        for (size_t i = 0; i < n; i++) {
            const char *p;
            size_t len;
            uint32_t h = 0;
            switch (op) {
                case 0:
                    moy_index_intern(ix, ps[i], ls[i], &h);
                    sum += h;
                    break;
                case 1:
                    if (moy_index_path(ix, hs[i], &p, &len) == MOY_INDEX_OK) {
                        sum += (uint32_t)len;
                    }
                    break;
                case 2:
                    sum += (uint32_t)moy_index_valid(ix, hs[i]);
                    break;
                default:
                    moy_index_release(ix, hs[i]);
                    moy_index_intern(ix, ps[i], ls[i], &hs[i]);
                    sum += hs[i];
                    break;
            }
        }
    }
    m_del(uint32_t, hs, cap);
    m_del(size_t, ls, cap);
    m_del(const char *, ps, cap);
    return mp_obj_new_int_from_uint(sum);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(bench_run_obj, 4, 4, bench_run);

static const mp_rom_map_elem_t bench_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_index_bench) },
    { MP_ROM_QSTR(MP_QSTR_run), MP_ROM_PTR(&bench_run_obj) },
};
static MP_DEFINE_CONST_DICT(bench_globals, bench_globals_table);

const mp_obj_module_t moy_index_bench_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&bench_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_index_bench, moy_index_bench_module);
