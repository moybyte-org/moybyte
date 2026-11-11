// moy_lease's MicroPython binding: the lease table (moy_lease.h) as the module
// `moy_lease`, the same names runtime/host/moy_lease.py gives CPython. A pin
// set is a tuple of pin numbers; a refusal raises OSError (EBUSY, EINVAL for
// what the board does not offer, EBADF for a stale handle, ENOSPC, ENOMEM).
//
//   offer(spec)          the offers made again from a spec ("gpio:0,1/uart1:4,5")
//   offers()             [(name, cls, unit, named pins, every pin), ...]
//   take(owner, cls, unit, pins, flags=0) -> h
//   drop(h)  sweep(owner) -> n  live(h)  leases() -> [h, ...]
//   get(h) -> (owner, cls, unit, flags, pins, ((pin, src), ...))
//   held(flags=0) -> n   of_pin(pin) -> h or 0
//   edge(h, pin, depth, loop=False) -> src   edge_close(h, pin)
//   pin_state(pin) -> PIN_* bits
//   sim_pin(pin, state, level)  sim_level(pin, level)  sim_get(pin)   the host's stand-in
//   vm_stop() -> n       the VM's end: its device and VM rows dropped (THE VM'S END)
//   reset()
//   Wifi()               the radio's holders (moy_lease.h's THE RADIO), a view
//                        of its rows: hold(tag, owner=1) -> mask,
//                        release(tag, owner=1) -> mask (owner 0: every
//                        owner's), mask(), held(tag), holders() -> [tag, ...],
//                        the shell's tags bare and a process's as "tag:pid";
//                        a tag outside WIFI_TAGS is a ValueError, a non-str a
//                        TypeError
//   wifi_reset()         every holder let go (a fresh start)
//   GPIO UART SPI WIFI, WIFI_TAGS, VM PINS, PIN_IN PIN_OUT PIN_PU PIN_PD PIN_PERIPH PIN_SAFE, ROWS_MAX,
//   SHELL (the console's own radio holds' owner)

#include <stdio.h>
#include <string.h>

#include "py/mperrno.h"
#include "py/runtime.h"

#include "moy_lease.h"

static void check(int rc) {
    static const int err[] = { 0, MP_EBADF, MP_ENOSPC, MP_ENOMEM, MP_EINVAL, MP_EBUSY };
    if (rc != MOY_LEASE_OK) {
        mp_raise_OSError(rc > 0 && rc < (int)(sizeof(err) / sizeof(err[0])) ? err[rc] : MP_EIO);
    }
}

static mp_obj_t pins_tuple(uint64_t pins) {
    mp_obj_t items[MOY_LEASE_PIN_MAX];
    size_t n = 0;
    for (int p = 0; p < (int)MOY_LEASE_PIN_MAX; p++) {
        if (pins & ((uint64_t)1u << p)) {
            items[n++] = MP_OBJ_NEW_SMALL_INT(p);
        }
    }
    return mp_obj_new_tuple(n, items);
}

static uint64_t pins_of(mp_obj_t seq) {
    size_t n;
    mp_obj_t *items;
    mp_obj_get_array(seq, &n, &items);
    uint64_t pins = 0;
    for (size_t i = 0; i < n; i++) {
        mp_int_t p = mp_obj_get_int(items[i]);
        if (p < 0 || p >= (mp_int_t)MOY_LEASE_PIN_MAX) {
            mp_raise_OSError(MP_EINVAL);
        }
        pins |= (uint64_t)1u << p;
    }
    return pins;
}

static mp_obj_t lease_offer(mp_obj_t spec) {
    check(moy_lease_offer(spec == mp_const_none ? NULL : mp_obj_str_get_str(spec)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_offer_obj, lease_offer);

static mp_obj_t lease_offers(void) {
    mp_obj_t out = mp_obj_new_list(0, NULL);
    moy_lease_offer_t o;
    for (uint32_t i = 0; moy_lease_offer_at(i, &o) == MOY_LEASE_OK; i++) {
        mp_obj_t named[3];
        for (uint8_t k = 0; k < o.npins; k++) {
            named[k] = MP_OBJ_NEW_SMALL_INT(o.pin[k]);
        }
        mp_obj_t row[5] = {
            mp_obj_new_str(o.name, strlen(o.name)), MP_OBJ_NEW_SMALL_INT(o.cls),
            MP_OBJ_NEW_SMALL_INT(o.unit), mp_obj_new_tuple(o.npins, named), pins_tuple(o.pins),
        };
        mp_obj_list_append(out, mp_obj_new_tuple(5, row));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(lease_offers_obj, lease_offers);

static mp_obj_t lease_take(size_t n_args, const mp_obj_t *args) {
    uint32_t h;
    check(moy_lease_take((uint32_t)mp_obj_get_int(args[0]), (uint8_t)mp_obj_get_int(args[1]),
                         (uint8_t)mp_obj_get_int(args[2]), pins_of(args[3]),
                         n_args > 4 ? (uint8_t)mp_obj_get_int(args[4]) : 0u, &h));
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lease_take_obj, 4, 5, lease_take);

static mp_obj_t lease_drop(mp_obj_t h) {
    check(moy_lease_drop((uint32_t)mp_obj_get_int(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_drop_obj, lease_drop);

static mp_obj_t lease_sweep(mp_obj_t owner) {
    return MP_OBJ_NEW_SMALL_INT(moy_lease_sweep((uint32_t)mp_obj_get_int(owner)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_sweep_obj, lease_sweep);

static mp_obj_t lease_vm_stop(void) {
    return MP_OBJ_NEW_SMALL_INT(moy_lease_vm_stop());
}
static MP_DEFINE_CONST_FUN_OBJ_0(lease_vm_stop_obj, lease_vm_stop);

static mp_obj_t lease_live(mp_obj_t h) {
    return mp_obj_new_bool(moy_lease_live((uint32_t)mp_obj_get_int(h)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_live_obj, lease_live);

static mp_obj_t lease_leases(void) {
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (uint32_t s = 0; s < moy_lease_slots(); s++) {
        uint32_t h = moy_lease_at(s);
        if (h != 0) {
            mp_obj_list_append(out, mp_obj_new_int_from_uint(h));
        }
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(lease_leases_obj, lease_leases);

static mp_obj_t lease_get(mp_obj_t h) {
    moy_lease_row_t r;
    check(moy_lease_get((uint32_t)mp_obj_get_int(h), &r));
    mp_obj_t edges[MOY_LEASE_EDGES];
    size_t ne = 0;
    for (uint32_t i = 0; i < MOY_LEASE_EDGES; i++) {
        if (r.edge[i] != 0) {
            mp_obj_t pair[2] = { MP_OBJ_NEW_SMALL_INT(r.edge_pin[i]),
                                 mp_obj_new_int_from_uint(r.edge[i]) };
            edges[ne++] = mp_obj_new_tuple(2, pair);
        }
    }
    mp_obj_t row[6] = {
        mp_obj_new_int_from_uint(r.owner), MP_OBJ_NEW_SMALL_INT(r.cls),
        MP_OBJ_NEW_SMALL_INT(r.unit), MP_OBJ_NEW_SMALL_INT(r.flags), pins_tuple(r.pins),
        mp_obj_new_tuple(ne, edges),
    };
    return mp_obj_new_tuple(6, row);
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_get_obj, lease_get);

static mp_obj_t lease_held(size_t n_args, const mp_obj_t *args) {
    return MP_OBJ_NEW_SMALL_INT(moy_lease_held(n_args ? (uint8_t)mp_obj_get_int(args[0]) : 0u));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lease_held_obj, 0, 1, lease_held);

static mp_obj_t lease_of_pin(mp_obj_t pin) {
    return mp_obj_new_int_from_uint(moy_lease_of_pin(mp_obj_get_int(pin)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_of_pin_obj, lease_of_pin);

static mp_obj_t lease_edge(size_t n_args, const mp_obj_t *args) {
    uint32_t src;
    check(moy_lease_edge((uint32_t)mp_obj_get_int(args[0]), mp_obj_get_int(args[1]),
                         (uint32_t)mp_obj_get_int(args[2]),
                         n_args > 3 && mp_obj_is_true(args[3]), &src));
    return mp_obj_new_int_from_uint(src);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(lease_edge_obj, 3, 4, lease_edge);

static mp_obj_t lease_edge_close(mp_obj_t h, mp_obj_t pin) {
    check(moy_lease_edge_close((uint32_t)mp_obj_get_int(h), mp_obj_get_int(pin)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(lease_edge_close_obj, lease_edge_close);

static mp_obj_t lease_pin_state(mp_obj_t pin) {
    return MP_OBJ_NEW_SMALL_INT(moy_lease_pin_state(mp_obj_get_int(pin)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_pin_state_obj, lease_pin_state);

// device_name(cls) -> "mic", "lora", "ble", "media" for a device class, else None.
static mp_obj_t lease_device_name(mp_obj_t cls) {
    const char *n = moy_lease_device_name((uint8_t)mp_obj_get_int(cls));
    return n != NULL ? mp_obj_new_str(n, strlen(n)) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_device_name_obj, lease_device_name);

static mp_obj_t lease_sim_pin(mp_obj_t pin, mp_obj_t state, mp_obj_t level) {
    moy_lease_sim_pin(mp_obj_get_int(pin), (uint8_t)mp_obj_get_int(state), mp_obj_get_int(level));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(lease_sim_pin_obj, lease_sim_pin);

static mp_obj_t lease_sim_level(mp_obj_t pin, mp_obj_t level) {
    moy_lease_sim_level(mp_obj_get_int(pin), mp_obj_get_int(level));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(lease_sim_level_obj, lease_sim_level);

static mp_obj_t lease_sim_get(mp_obj_t pin) {
    return MP_OBJ_NEW_SMALL_INT(moy_lease_sim_get(mp_obj_get_int(pin)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(lease_sim_get_obj, lease_sim_get);

static mp_obj_t lease_reset(void) {
    moy_lease_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(lease_reset_obj, lease_reset);

// -- the radio -----------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
} wifi_obj_t;

static mp_obj_t wifi_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                              const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    return MP_OBJ_FROM_PTR(mp_obj_malloc(wifi_obj_t, type));
}

static const char *wifi_arg(mp_obj_t tag, size_t *n) {
    if (!mp_obj_is_str(tag)) {
        mp_raise_TypeError(MP_ERROR_TEXT("a lease tag is a str"));
    }
    const char *s = mp_obj_str_get_data(tag, n);
    if (moy_lease_wifi_bit(s, *n) == 0u) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("unknown lease tag %s"), s);
    }
    return s;
}

static uint32_t wifi_owner(size_t n_args, const mp_obj_t *args) {
    return n_args > 2 ? (uint32_t)mp_obj_get_int(args[2]) : MOY_LEASE_SHELL;
}

static mp_obj_t wifi_hold(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *s = wifi_arg(args[1], &n);
    uint32_t mask;
    check(moy_lease_wifi_hold(wifi_owner(n_args, args), s, n, &mask));
    return MP_OBJ_NEW_SMALL_INT(mask);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(wifi_hold_obj, 2, 3, wifi_hold);

static mp_obj_t wifi_release(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *s = wifi_arg(args[1], &n);
    uint32_t mask;
    check(moy_lease_wifi_release(wifi_owner(n_args, args), s, n, &mask));
    return MP_OBJ_NEW_SMALL_INT(mask);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(wifi_release_obj, 2, 3, wifi_release);

static mp_obj_t wifi_mask(mp_obj_t self) {
    (void)self;
    return MP_OBJ_NEW_SMALL_INT(moy_lease_wifi_mask());
}
static MP_DEFINE_CONST_FUN_OBJ_1(wifi_mask_obj, wifi_mask);

static mp_obj_t wifi_held(mp_obj_t self, mp_obj_t tag) {
    (void)self;
    size_t n;
    const char *s = wifi_arg(tag, &n);
    return mp_obj_new_bool(moy_lease_wifi_mask() & moy_lease_wifi_bit(s, n));
}
static MP_DEFINE_CONST_FUN_OBJ_2(wifi_held_obj, wifi_held);

static mp_obj_t wifi_holders(mp_obj_t self) {
    (void)self;
    mp_obj_t out = mp_obj_new_list(0, NULL);
    uint32_t owner, tags;
    for (uint32_t h = 0; moy_lease_wifi_holder(h, &owner, &tags) == MOY_LEASE_OK; h++) {
        for (uint32_t i = 0; i < MOY_LEASE_WIFI_TAGS; i++) {
            if (tags & (1u << i)) {
                char name[24];
                const char *tag = moy_lease_wifi_tag(i);
                int k = owner == MOY_LEASE_SHELL
                        ? snprintf(name, sizeof name, "%s", tag)
                        : snprintf(name, sizeof name, "%s:%u", tag, (unsigned)owner);
                mp_obj_list_append(out, mp_obj_new_str(name, (size_t)k));
            }
        }
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(wifi_holders_obj, wifi_holders);

static const mp_rom_map_elem_t wifi_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_hold), MP_ROM_PTR(&wifi_hold_obj) },
    { MP_ROM_QSTR(MP_QSTR_release), MP_ROM_PTR(&wifi_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_mask), MP_ROM_PTR(&wifi_mask_obj) },
    { MP_ROM_QSTR(MP_QSTR_held), MP_ROM_PTR(&wifi_held_obj) },
    { MP_ROM_QSTR(MP_QSTR_holders), MP_ROM_PTR(&wifi_holders_obj) },
};
static MP_DEFINE_CONST_DICT(wifi_locals, wifi_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    wifi_type, MP_QSTR_Wifi, MP_TYPE_FLAG_NONE,
    make_new, wifi_make_new,
    locals_dict, &wifi_locals
    );

static mp_obj_t lease_wifi_reset(void) {
    moy_lease_wifi_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(lease_wifi_reset_obj, lease_wifi_reset);

static const mp_rom_obj_tuple_t wifi_tags_tuple = {
    { &mp_type_tuple }, MOY_LEASE_WIFI_TAGS, {
        MP_ROM_QSTR(MP_QSTR_web), MP_ROM_QSTR(MP_QSTR_update), MP_ROM_QSTR(MP_QSTR_settings),
        MP_ROM_QSTR(MP_QSTR_cart), MP_ROM_QSTR(MP_QSTR_link), MP_ROM_QSTR(MP_QSTR_carts),
        MP_ROM_QSTR(MP_QSTR_dev), MP_ROM_QSTR(MP_QSTR_http), MP_ROM_QSTR(MP_QSTR_media),
        MP_ROM_QSTR(MP_QSTR_ssh), MP_ROM_QSTR(MP_QSTR_native), MP_ROM_QSTR(MP_QSTR_net),
        MP_ROM_QSTR(MP_QSTR_kstop),
    }
};

static const mp_rom_map_elem_t lease_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_lease) },
    { MP_ROM_QSTR(MP_QSTR_offer), MP_ROM_PTR(&lease_offer_obj) },
    { MP_ROM_QSTR(MP_QSTR_offers), MP_ROM_PTR(&lease_offers_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&lease_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_drop), MP_ROM_PTR(&lease_drop_obj) },
    { MP_ROM_QSTR(MP_QSTR_sweep), MP_ROM_PTR(&lease_sweep_obj) },
    { MP_ROM_QSTR(MP_QSTR_vm_stop), MP_ROM_PTR(&lease_vm_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_live), MP_ROM_PTR(&lease_live_obj) },
    { MP_ROM_QSTR(MP_QSTR_leases), MP_ROM_PTR(&lease_leases_obj) },
    { MP_ROM_QSTR(MP_QSTR_get), MP_ROM_PTR(&lease_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_held), MP_ROM_PTR(&lease_held_obj) },
    { MP_ROM_QSTR(MP_QSTR_of_pin), MP_ROM_PTR(&lease_of_pin_obj) },
    { MP_ROM_QSTR(MP_QSTR_edge), MP_ROM_PTR(&lease_edge_obj) },
    { MP_ROM_QSTR(MP_QSTR_edge_close), MP_ROM_PTR(&lease_edge_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_pin_state), MP_ROM_PTR(&lease_pin_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_name), MP_ROM_PTR(&lease_device_name_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_pin), MP_ROM_PTR(&lease_sim_pin_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_level), MP_ROM_PTR(&lease_sim_level_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_get), MP_ROM_PTR(&lease_sim_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&lease_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_Wifi), MP_ROM_PTR(&wifi_type) },
    { MP_ROM_QSTR(MP_QSTR_wifi_reset), MP_ROM_PTR(&lease_wifi_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_WIFI_TAGS), MP_ROM_PTR(&wifi_tags_tuple) },
    { MP_ROM_QSTR(MP_QSTR_WIFI), MP_ROM_INT(MOY_LEASE_WIFI) },
    { MP_ROM_QSTR(MP_QSTR_GPIO), MP_ROM_INT(MOY_LEASE_GPIO) },
    { MP_ROM_QSTR(MP_QSTR_UART), MP_ROM_INT(MOY_LEASE_UART) },
    { MP_ROM_QSTR(MP_QSTR_SPI), MP_ROM_INT(MOY_LEASE_SPI) },
    { MP_ROM_QSTR(MP_QSTR_VM), MP_ROM_INT(MOY_LEASE_VM) },
    { MP_ROM_QSTR(MP_QSTR_PINS), MP_ROM_INT(MOY_LEASE_PINS) },
    { MP_ROM_QSTR(MP_QSTR_PIN_IN), MP_ROM_INT(MOY_PIN_IN) },
    { MP_ROM_QSTR(MP_QSTR_PIN_OUT), MP_ROM_INT(MOY_PIN_OUT) },
    { MP_ROM_QSTR(MP_QSTR_PIN_PU), MP_ROM_INT(MOY_PIN_PU) },
    { MP_ROM_QSTR(MP_QSTR_PIN_PD), MP_ROM_INT(MOY_PIN_PD) },
    { MP_ROM_QSTR(MP_QSTR_PIN_PERIPH), MP_ROM_INT(MOY_PIN_PERIPH) },
    { MP_ROM_QSTR(MP_QSTR_PIN_SAFE), MP_ROM_INT(MOY_PIN_SAFE) },
    { MP_ROM_QSTR(MP_QSTR_ROWS_MAX), MP_ROM_INT(MOY_LEASE_ROWS_MAX) },
    { MP_ROM_QSTR(MP_QSTR_SHELL), MP_ROM_INT(MOY_LEASE_SHELL) },
};
static MP_DEFINE_CONST_DICT(lease_globals, lease_globals_table);

const mp_obj_module_t moy_lease_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&lease_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_lease, moy_lease_module);
