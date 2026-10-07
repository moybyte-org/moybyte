// moy_net for MicroPython: the wire's pure half (moy_net.h) as the module the
// boards and the browser import, name for name with runtime/net_binding.py.

#include <string.h>

#include "py/objarray.h"
#include "py/objlist.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_net.h"

// A str of the bytes: as they stand when they are UTF-8, else each byte as
// its Latin-1 character (a client's malformed head still names a route).
static mp_obj_t text_of(const char *s, size_t n) {
    if (moy_utf8_valid(s, n)) {
        return mp_obj_new_str(s, n);
    }
    vstr_t v;
    vstr_init(&v, n * 2);
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c < 0x80) {
            vstr_add_byte(&v, c);
        } else {
            vstr_add_byte(&v, (byte)(0xC0 | (c >> 6)));
            vstr_add_byte(&v, (byte)(0x80 | (c & 0x3F)));
        }
    }
    return mp_obj_new_str_from_vstr(&v);
}

static mp_obj_t mod_parse_request(mp_obj_t raw) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(raw, &b, MP_BUFFER_READ);
    moy_http_req_t r;
    int rc = moy_http_parse(b.buf, b.len, &r);
    mp_obj_t t[4];
    if (rc != MOY_HTTP_OK) {
        t[0] = t[1] = mp_const_none;
        t[2] = MP_OBJ_NEW_SMALL_INT(0);
        t[3] = MP_OBJ_NEW_SMALL_INT(-1);
    } else {
        t[0] = text_of(r.method, r.method_n);
        t[1] = text_of(r.target, r.target_n);
        t[2] = mp_obj_new_int_from_uint(r.clen);
        t[3] = mp_obj_new_int_from_uint(r.head_end);
    }
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_parse_request_obj, mod_parse_request);

static mp_obj_t mod_query_param(mp_obj_t target, mp_obj_t name) {
    if (target == mp_const_none) {
        return MP_OBJ_NEW_QSTR(MP_QSTR_);
    }
    size_t tn, nn;
    const char *t = mp_obj_str_get_data(target, &tn);
    const char *n = mp_obj_str_get_data(name, &nn);
    const char *v;
    size_t vn;
    moy_http_query(t, tn, n, nn, &v, &vn);
    return mp_obj_new_str(v, vn);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_query_param_obj, mod_query_param);

static mp_obj_t mod_http_response(size_t n_args, const mp_obj_t *args) {
    mp_int_t status = mp_obj_get_int(args[0]);
    mp_buffer_info_t body;
    mp_get_buffer_raise(args[1], &body, MP_BUFFER_READ);
    const char *ctype = n_args > 2 ? mp_obj_str_get_str(args[2])
                                   : "application/json";
    size_t head = moy_http_head(NULL, 0, (int)status, ctype, body.len);
    vstr_t v;
    vstr_init_len(&v, head + body.len);
    moy_http_head(v.buf, head + 1, (int)status, ctype, body.len);
    memcpy(v.buf + head, body.buf, body.len);
    return mp_obj_new_bytes_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_http_response_obj, 2, 3,
                                           mod_http_response);

// The JSON text json.dumps writes for `o`, into `v` (the port's own printer,
// the one its json module uses).
static void json_of(vstr_t *v, mp_obj_t o) {
    #if MICROPY_PY_JSON_SEPARATORS
    // The container printers read their separators from the extended print
    // json.dumps passes; a plain mp_print_t would hand them its neighbours.
    mp_print_ext_t ext;
    vstr_init_print(v, 64, &ext.base);
    ext.item_separator = ", ";
    ext.key_separator = ": ";
    mp_obj_print_helper(&ext.base, o, PRINT_JSON);
    #else
    mp_print_t p;
    vstr_init_print(v, 64, &p);
    mp_obj_print_helper(&p, o, PRINT_JSON);
    #endif
}

static mp_obj_t mod_encode_batch(size_t n_args, const mp_obj_t *args) {
    vstr_t jv, jr, jo, jp;
    json_of(&jv, args[0]);
    int has_root = args[1] != mp_const_none;
    int has_pin = n_args > 3 && mp_obj_is_true(args[3]);
    if (has_root) {
        json_of(&jr, args[1]);
    }
    json_of(&jo, args[2]);
    if (has_pin) {
        json_of(&jp, args[3]);
    }
    size_t need = moy_sync_encode(NULL, 0, jv.buf, jv.len,
                                  has_root ? jr.buf : NULL, has_root ? jr.len : 0,
                                  jo.buf, jo.len, has_pin ? jp.buf : NULL,
                                  has_pin ? jp.len : 0);
    vstr_t out;
    vstr_init_len(&out, need);
    moy_sync_encode(out.buf, need, jv.buf, jv.len, has_root ? jr.buf : NULL,
                    has_root ? jr.len : 0, jo.buf, jo.len,
                    has_pin ? jp.buf : NULL, has_pin ? jp.len : 0);
    vstr_clear(&jv);
    vstr_clear(&jo);
    if (has_root) {
        vstr_clear(&jr);
    }
    if (has_pin) {
        vstr_clear(&jp);
    }
    return mp_obj_new_str_from_vstr(&out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_encode_batch_obj, 3, 4,
                                           mod_encode_batch);

static mp_obj_t loads(mp_obj_t fn, const char *a, const char *b) {
    if (a == NULL) {
        return mp_const_none;
    }
    return mp_call_function_1(fn, mp_obj_new_str(a, (size_t)(b - a)));
}

static mp_obj_t mod_decode_batch(mp_obj_t body) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(body, &b, MP_BUFFER_READ);
    moy_sync_env_t e;
    if (moy_sync_decode(b.buf, b.len, &e) != MOY_SYNC_OK) {
        return mp_const_none;
    }
    mp_obj_t json = mp_import_name(MP_QSTR_json, mp_const_none,
                                   MP_OBJ_NEW_SMALL_INT(0));
    mp_obj_t fn = mp_load_attr(json, MP_QSTR_loads);
    mp_obj_t t[4] = {loads(fn, e.v, e.v_end), loads(fn, e.root, e.root_end),
                     loads(fn, e.ops, e.ops_end), loads(fn, e.pin, e.pin_end)};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_decode_batch_obj, mod_decode_batch);

static size_t str_len_or_0(mp_obj_t o, const char **s) {
    *s = NULL;
    if (o == mp_const_none) {
        return 0;
    }
    size_t n;
    *s = mp_obj_str_get_data(o, &n);
    return n;
}

static mp_obj_t mod_wifi_password(mp_obj_t password, mp_obj_t stored) {
    const char *p, *s;
    size_t pn = str_len_or_0(password, &p);
    size_t sn = str_len_or_0(stored, &s);
    return moy_wifi_use_stored(pn, sn) ? stored : password;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_wifi_password_obj, mod_wifi_password);

static mp_obj_t mod_wifi_remember(mp_obj_t ok, mp_obj_t password,
                                  mp_obj_t stored) {
    const char *p, *s;
    size_t pn = str_len_or_0(password, &p);
    size_t sn = str_len_or_0(stored, &s);
    return mp_obj_new_bool(moy_wifi_remember(mp_obj_is_true(ok), p, pn, s, sn));
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_wifi_remember_obj, mod_wifi_remember);


#if defined(MOY_NET_LINK) && MOY_NET_LINK

#include "esp_wifi.h"
#include "moy_link.h"

// moy_net.Link: the radio link's face, the subset of the port's espnow.ESPNow
// that device/moy_espnow.py drives. The radio and its ring are the kernel's
// (moy_link.c); this object holds only what irecv hands back, reused.
typedef struct {
    mp_obj_base_t base;
    uint32_t rxbuf;
    mp_obj_t pair;              // [mac, msg], reused by irecv
    mp_obj_t msg;               // the bytearray irecv fills
    mp_obj_t macs[8];           // the MACs heard, as bytes, reused
    uint8_t data[MOY_LINK_MAX];
} link_obj_t;

static mp_obj_t link_mac(link_obj_t *self, const uint8_t mac[6]) {
    size_t i;
    for (i = 0; i < MP_ARRAY_SIZE(self->macs) && self->macs[i] != MP_OBJ_NULL; i++) {
        mp_buffer_info_t b;
        mp_get_buffer_raise(self->macs[i], &b, MP_BUFFER_READ);
        if (memcmp(b.buf, mac, 6) == 0) {
            return self->macs[i];
        }
    }
    mp_obj_t o = mp_obj_new_bytes(mac, 6);
    if (i == MP_ARRAY_SIZE(self->macs)) {
        memmove(self->macs, self->macs + 1, sizeof(self->macs) - sizeof(mp_obj_t));
        i--;
    }
    self->macs[i] = o;
    return o;
}

static void check(int err) {
    if (err != 0) {
        mp_raise_OSError(err);
    }
}

extern const mp_obj_type_t moy_net_link_type;

static mp_obj_t link_make_new(const mp_obj_type_t *type, size_t n_args,
                              size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    link_obj_t *self = mp_obj_malloc(link_obj_t, type);
    self->rxbuf = 526;
    self->msg = mp_obj_new_bytearray(0, NULL);
    mp_obj_t pair[2] = {mp_const_none, mp_const_none};
    self->pair = mp_obj_new_list(2, pair);
    for (size_t i = 0; i < MP_ARRAY_SIZE(self->macs); i++) {
        self->macs[i] = MP_OBJ_NULL;
    }
    return MP_OBJ_FROM_PTR(self);
}

static mp_obj_t link_config(size_t n_args, const mp_obj_t *args,
                            mp_map_t *kw) {
    link_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    mp_map_elem_t *e = mp_map_lookup(kw, MP_OBJ_NEW_QSTR(MP_QSTR_rxbuf),
                                     MP_MAP_LOOKUP);
    if (e != NULL) {
        self->rxbuf = (uint32_t)mp_obj_get_int(e->value);
    }
    e = mp_map_lookup(kw, MP_OBJ_NEW_QSTR(MP_QSTR_rate), MP_MAP_LOOKUP);
    if (e != NULL) {
        check(moy_link_rate((int)mp_obj_get_int(e->value)));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(link_config_obj, 1, link_config);

static mp_obj_t link_active(size_t n_args, const mp_obj_t *args) {
    link_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    if (n_args > 1) {
        if (mp_obj_is_true(args[1])) {
            check(moy_link_start(self->rxbuf));
        } else {
            moy_link_stop();
        }
    }
    moy_link_stats_t s;
    moy_link_stats(&s);
    return mp_obj_new_bool(s.up);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(link_active_obj, 1, 2, link_active);

static const uint8_t *mac_of(mp_obj_t o) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    if (b.len != 6) {
        mp_raise_ValueError(MP_ERROR_TEXT("mac"));
    }
    return b.buf;
}

static mp_obj_t link_add_peer(mp_obj_t self_in, mp_obj_t mac) {
    check(moy_link_add_peer(mac_of(mac)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(link_add_peer_obj, link_add_peer);

static mp_obj_t link_send(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(args[2], &b, MP_BUFFER_READ);
    check(moy_link_send(mac_of(args[1]), b.buf, b.len));
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(link_send_obj, 3, 4, link_send);

// [mac, msg] with the oldest frame, or [None, None]; the list and the message
// are reused by the next call, as the port's irecv reuses them.
static mp_obj_t link_irecv(size_t n_args, const mp_obj_t *args) {
    link_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    mp_obj_list_t *pair = MP_OBJ_TO_PTR(self->pair);
    uint8_t mac[6];
    int n = moy_link_recv(mac, self->data, sizeof(self->data));
    if (n < 0) {
        pair->items[0] = pair->items[1] = mp_const_none;
        return self->pair;
    }
    mp_obj_array_t *msg = MP_OBJ_TO_PTR(self->msg);
    if (msg->len + msg->free < (size_t)n) {
        msg->items = m_renew(byte, msg->items, msg->len + msg->free, MOY_LINK_MAX);
        msg->free = MOY_LINK_MAX - msg->len;
    }
    memcpy(msg->items, self->data, (size_t)n);
    msg->free = msg->len + msg->free - (size_t)n;
    msg->len = (size_t)n;
    pair->items[0] = link_mac(self, mac);
    pair->items[1] = self->msg;
    return self->pair;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(link_irecv_obj, 1, 2, link_irecv);

// (rx, drops, peak, ring bytes, held bytes, up): the ring's counters.
static mp_obj_t link_stats(mp_obj_t self_in) {
    moy_link_stats_t s;
    moy_link_stats(&s);
    mp_obj_t t[6] = {mp_obj_new_int_from_uint(s.rx), mp_obj_new_int_from_uint(s.drops),
                     mp_obj_new_int_from_uint(s.peak), mp_obj_new_int_from_uint(s.cap),
                     mp_obj_new_int_from_uint(s.used), mp_obj_new_bool(s.up)};
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(link_stats_obj, link_stats);

static const mp_rom_map_elem_t link_locals_table[] = {
    {MP_ROM_QSTR(MP_QSTR_config), MP_ROM_PTR(&link_config_obj)},
    {MP_ROM_QSTR(MP_QSTR_active), MP_ROM_PTR(&link_active_obj)},
    {MP_ROM_QSTR(MP_QSTR_add_peer), MP_ROM_PTR(&link_add_peer_obj)},
    {MP_ROM_QSTR(MP_QSTR_send), MP_ROM_PTR(&link_send_obj)},
    {MP_ROM_QSTR(MP_QSTR_irecv), MP_ROM_PTR(&link_irecv_obj)},
    {MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&link_stats_obj)},
};
static MP_DEFINE_CONST_DICT(link_locals, link_locals_table);

MP_DEFINE_CONST_OBJ_TYPE(moy_net_link_type, MP_QSTR_Link, MP_TYPE_FLAG_NONE,
                         make_new, link_make_new, locals_dict, &link_locals);

#endif

static const mp_rom_map_elem_t moy_net_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_net)},
    {MP_ROM_QSTR(MP_QSTR_parse_request), MP_ROM_PTR(&mod_parse_request_obj)},
    {MP_ROM_QSTR(MP_QSTR_query_param), MP_ROM_PTR(&mod_query_param_obj)},
    {MP_ROM_QSTR(MP_QSTR_http_response), MP_ROM_PTR(&mod_http_response_obj)},
    {MP_ROM_QSTR(MP_QSTR_encode_batch), MP_ROM_PTR(&mod_encode_batch_obj)},
    {MP_ROM_QSTR(MP_QSTR_decode_batch), MP_ROM_PTR(&mod_decode_batch_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_password), MP_ROM_PTR(&mod_wifi_password_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_remember), MP_ROM_PTR(&mod_wifi_remember_obj)},
    #if defined(MOY_NET_LINK) && MOY_NET_LINK
    {MP_ROM_QSTR(MP_QSTR_Link), MP_ROM_PTR(&moy_net_link_type)},
    {MP_ROM_QSTR(MP_QSTR_RATE_54M), MP_ROM_INT(WIFI_PHY_RATE_54M)},
    #endif
};
static MP_DEFINE_CONST_DICT(moy_net_globals, moy_net_globals_table);

const mp_obj_module_t moy_net_module = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&moy_net_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_net, moy_net_module);
