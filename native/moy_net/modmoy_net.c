// moy_net for MicroPython: the wire's pure half (moy_net.h) as the module the
// boards and the browser import, name for name with runtime/net_binding.py.

#include <stdio.h>
#include <string.h>

#include "py/objarray.h"
#include "py/objlist.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_json.h"
#include "moy_net.h"
#include "moy_ota.h"

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


#if defined(MOY_NET_WIFI) && MOY_NET_WIFI

// The WiFi driver's face (moy_wifi.c): device/device_wifi.py's service and
// the link call these; the spine's lease is what calls wifi_on and wifi_off.

static void wcheck(int err) {
    if (err != 0) {
        mp_raise_OSError(err);
    }
}

static mp_obj_t mod_wifi_on(void) {
    wcheck(moy_wifi_on());
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_wifi_on_obj, mod_wifi_on);

static mp_obj_t mod_wifi_off(void) {
    moy_wifi_off();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_wifi_off_obj, mod_wifi_off);

static mp_obj_t mod_wifi_connect(mp_obj_t ssid, mp_obj_t password) {
    wcheck(moy_wifi_connect(mp_obj_str_get_str(ssid),
                            password == mp_const_none ? "" : mp_obj_str_get_str(password)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_wifi_connect_obj, mod_wifi_connect);

static mp_obj_t mod_wifi_forget(mp_obj_t ssid) {
    moy_wifi_forget_kept(mp_obj_str_get_str(ssid));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_wifi_forget_obj, mod_wifi_forget);

static mp_obj_t mod_wifi_disconnect(void) {
    moy_wifi_disconnect();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_wifi_disconnect_obj, mod_wifi_disconnect);

// (driver, on, connected, ssid or None, ip or None, last disconnect reason)
static mp_obj_t mod_wifi_status(void) {
    moy_wifi_state_t st;
    moy_wifi_state(&st);
    mp_obj_t ip = mp_const_none;
    if (st.connected && st.ip) {
        char b[16];
        int n = snprintf(b, sizeof(b), "%u.%u.%u.%u", (unsigned)(st.ip & 0xff),
                         (unsigned)((st.ip >> 8) & 0xff), (unsigned)((st.ip >> 16) & 0xff),
                         (unsigned)(st.ip >> 24));
        ip = mp_obj_new_str(b, (size_t)n);
    }
    mp_obj_t t[6] = {mp_obj_new_bool(st.driver), mp_obj_new_bool(st.on),
                     mp_obj_new_bool(st.connected),
                     st.ssid[0] ? mp_obj_new_str(st.ssid, strlen(st.ssid)) : mp_const_none,
                     ip, MP_OBJ_NEW_SMALL_INT(st.reason)};
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_wifi_status_obj, mod_wifi_status);

// [(ssid bytes, rssi, auth)], [] while the radio is down.
static mp_obj_t mod_wifi_scan(void) {
    moy_wifi_ap_t *aps = m_new(moy_wifi_ap_t, MOY_WIFI_SCAN_MAX);
    int n = moy_wifi_scan(aps, MOY_WIFI_SCAN_MAX);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        mp_obj_t t[3] = {mp_obj_new_bytes((const byte *)aps[i].ssid, strlen(aps[i].ssid)),
                         MP_OBJ_NEW_SMALL_INT(aps[i].rssi), MP_OBJ_NEW_SMALL_INT(aps[i].auth)};
        mp_obj_list_append(out, mp_obj_new_tuple(3, t));
    }
    m_del(moy_wifi_ap_t, aps, MOY_WIFI_SCAN_MAX);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_wifi_scan_obj, mod_wifi_scan);

static mp_obj_t mod_wifi_mac(void) {
    uint8_t mac[6];
    wcheck(moy_wifi_mac(mac));
    return mp_obj_new_bytes(mac, 6);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_wifi_mac_obj, mod_wifi_mac);

static mp_obj_t mod_wifi_ps(size_t n_args, const mp_obj_t *args) {
    int v = moy_wifi_ps(n_args > 0 ? (int)mp_obj_get_int(args[0]) : -1);
    return v < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(v);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_wifi_ps_obj, 0, 1, mod_wifi_ps);

#endif

// -- the sync apply and the webhost -------------------------------------------------

#include "py/mphal.h"

void moy_store_unwind(void);

// A store call raised under one of these (a block device's read, through the
// VM's mount table): the connection it was serving is closed and the store's
// open files and scratch freed before the exception goes on.
#define WEB_GUARD_BEGIN { nlr_buf_t nlr_; if (nlr_push(&nlr_) == 0) {
#define WEB_GUARD_END nlr_pop(); } else { moy_web_abort(); moy_store_unwind(); \
    nlr_jump(nlr_.ret_val); } }

// A tuple of str (or None) as a NUL-separated list ending in "": in `buf`.
static const char *names_of(mp_obj_t o, char *buf, size_t cap) {
    if (o == mp_const_none) {
        return NULL;
    }
    size_t n, k = 0;
    mp_obj_t *items;
    mp_obj_get_array(o, &n, &items);
    for (size_t i = 0; i < n; i++) {
        size_t len;
        const char *s = mp_obj_str_get_data(items[i], &len);
        if (len == 0 || k + len + 2u > cap) {
            mp_raise_ValueError(MP_ERROR_TEXT("names"));
        }
        memcpy(buf + k, s, len);
        buf[k + len] = '\0';
        k += len + 1u;
    }
    buf[k] = '\0';
    return buf;
}

static const char *str_or_null(mp_obj_t o) {
    return o == mp_const_none ? NULL : mp_obj_str_get_str(o);
}

#if MICROPY_EPOCH_IS_2000
#define VM_EPOCH 946684800
#else
#define VM_EPOCH 0
#endif

// sync_batch(body) -> (root id, pin or None, ops JSON text), or None.
static mp_obj_t mod_sync_batch(mp_obj_t body) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(body, &b, MP_BUFFER_READ);
    moy_sync_batch_t bt;
    if (moy_sync_batch(b.buf, b.len, &bt) != MOY_SYNC_OK) {
        return mp_const_none;
    }
    mp_obj_t fn = mp_load_attr(mp_import_name(MP_QSTR_json, mp_const_none,
                                              MP_OBJ_NEW_SMALL_INT(0)),
                               MP_QSTR_loads);
    mp_obj_t t[3] = {
        MP_OBJ_NEW_QSTR(bt.root == MOY_SYNC_FILES ? MP_QSTR_files : MP_QSTR_carts),
        loads(fn, bt.pin, bt.pin_end),
        mp_obj_new_str(bt.ops, (size_t)(bt.ops_end - bt.ops)),
    };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_sync_batch_obj, mod_sync_batch);

// sync_apply(carts, files, kinds, root id, ops JSON, journal)
//   -> (applied, [(index, reason)], shelf, refused)
static mp_obj_t mod_sync_apply(size_t n_args, const mp_obj_t *args) {
    char kinds[96];
    moy_sync_store_t s = {
        .carts = mp_obj_str_get_str(args[0]),
        .files = str_or_null(args[1]),
        .kinds = names_of(args[2], kinds, sizeof(kinds)),
        .journal = mp_obj_is_true(args[5]),
        .ts = (int64_t)(mp_hal_time_ns() / 1000000000ull) - VM_EPOCH,
    };
    int root = strcmp(mp_obj_str_get_str(args[3]), "files") == 0
               ? MOY_SYNC_FILES : MOY_SYNC_CARTS;
    size_t on;
    const char *ops = mp_obj_str_get_data(args[4], &on);
    if (moy_json_valid(ops, on) != MOY_JSON_OK) {
        mp_raise_ValueError(MP_ERROR_TEXT("ops"));
    }
    const char *a = moy_json_ws(ops, ops + on);
    moy_sync_result_t r;
    WEB_GUARD_BEGIN
    moy_sync_apply(&s, root, a, moy_json_value(a, ops + on, 0), &r);
    WEB_GUARD_END
    mp_obj_t errs = mp_obj_new_list(0, NULL);
    for (uint32_t i = 0; i < r.nerr; i++) {
        mp_obj_t e[2] = {mp_obj_new_int_from_uint(r.err[i].index),
                         mp_obj_new_str(r.err[i].why, strlen(r.err[i].why))};
        mp_obj_list_append(errs, mp_obj_new_tuple(2, e));
    }
    mp_obj_t t[4] = {mp_obj_new_int_from_uint(r.applied), errs,
                     mp_obj_new_bool(r.shelf), mp_obj_new_int_from_uint(r.refused)};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_sync_apply_obj, 6, 6, mod_sync_apply);

// web_start(port, carts, files, kinds, pin, defer): raises OSError on a bind.
static mp_obj_t mod_web_start(size_t n_args, const mp_obj_t *args) {
    char kinds[96], defer[96];
    moy_web_cfg_t cfg = {
        .port = (uint16_t)mp_obj_get_int(args[0]),
        .carts = mp_obj_str_get_str(args[1]),
        .files = str_or_null(args[2]),
        .kinds = names_of(args[3], kinds, sizeof(kinds)),
        .pin = str_or_null(args[4]),
        .defer = names_of(args[5], defer, sizeof(defer)),
        .epoch = VM_EPOCH,
    };
    int rc = moy_web_start(&cfg);
    if (rc != 0) {
        mp_raise_OSError(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_web_start_obj, 6, 6, mod_web_start);

static mp_obj_t mod_web_stop(size_t n_args, const mp_obj_t *args) {
    moy_web_stop(n_args ? str_or_null(args[0]) : NULL);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_web_stop_obj, 0, 1, mod_web_stop);

static mp_obj_t mod_web_set_pin(mp_obj_t pin) {
    moy_web_set_pin(str_or_null(pin));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_web_set_pin_obj, mod_web_set_pin);

static mp_obj_t mod_web_poll(void) {
    int did = 0;
    WEB_GUARD_BEGIN
    did = moy_web_poll();
    WEB_GUARD_END
    return mp_obj_new_bool(did);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_web_poll_obj, mod_web_poll);

// (serving, closing, parked, port, requests, err)
static mp_obj_t mod_web_state(void) {
    moy_web_state_t st;
    moy_web_state(&st);
    mp_obj_t t[6] = {mp_obj_new_bool(st.serving), mp_obj_new_bool(st.closing),
                     mp_obj_new_bool(st.parked), MP_OBJ_NEW_SMALL_INT(st.port),
                     mp_obj_new_int_from_uint(st.requests), MP_OBJ_NEW_SMALL_INT(st.err)};
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_web_state_obj, mod_web_state);

static mp_obj_t mod_web_events(void) {
    return mp_obj_new_int_from_uint(moy_web_events());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_web_events_obj, mod_web_events);

// web_take() -> (method, target, body bytes) for the parked request, or None.
static mp_obj_t mod_web_take(void) {
    const char *m, *t, *b;
    size_t mn, tn, bn;
    if (!moy_web_take(&m, &mn, &t, &tn, &b, &bn)) {
        return mp_const_none;
    }
    mp_obj_t o[3] = {text_of(m, mn), text_of(t, tn), mp_obj_new_bytes((const byte *)b, bn)};
    return mp_obj_new_tuple(3, o);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_web_take_obj, mod_web_take);

static mp_obj_t mod_web_answer(mp_obj_t resp) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(resp, &b, MP_BUFFER_READ);
    moy_web_answer(b.buf, b.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_web_answer_obj, mod_web_answer);

static mp_obj_t out_bytes(char *p, size_t n) {
    mp_obj_t o = mp_obj_new_bytes((const byte *)(p ? p : ""), n);
    moy_net_free(p);
    return o;
}

// web_handle(method, target, body) -> the whole response, or None when the
// VM answers this request.
static mp_obj_t mod_web_handle(mp_obj_t method, mp_obj_t target, mp_obj_t body) {
    size_t mn, tn;
    const char *m = mp_obj_str_get_data(method, &mn);
    const char *t = mp_obj_str_get_data(target, &tn);
    mp_buffer_info_t b;
    mp_get_buffer_raise(body, &b, MP_BUFFER_READ);
    char *out = NULL;
    size_t n = 0;
    int done = 0;
    WEB_GUARD_BEGIN
    done = moy_web_handle(m, mn, t, tn, b.buf, b.len, &out, &n);
    WEB_GUARD_END
    if (!done) {
        moy_net_free(out);
        return mp_const_none;
    }
    return out_bytes(out, n);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_web_handle_obj, mod_web_handle);

// web_pack(root, kinds) -> the store under `root` as the pull's JSON text.
static mp_obj_t mod_web_pack(mp_obj_t root, mp_obj_t kinds_o) {
    char kinds[96];
    const char *k = names_of(kinds_o, kinds, sizeof(kinds));
    char *out = NULL;
    size_t n = 0;
    int rc = 0;
    WEB_GUARD_BEGIN
    rc = moy_web_pack(mp_obj_str_get_str(root), k, &out, &n);
    WEB_GUARD_END
    if (rc != 0) {
        moy_net_free(out);
        mp_raise_OSError(rc);
    }
    mp_obj_t s = mp_obj_new_str(out ? out : "", n);
    moy_net_free(out);
    return s;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_web_pack_obj, mod_web_pack);

static mp_obj_t mod_web_stamp(void) {
    const char *s = moy_web_stamp_text();
    return s ? mp_obj_new_str(s, strlen(s)) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_web_stamp_obj, mod_web_stamp);

// -- the updater and its client (moy_ota.h) -----------------------------------------

static mp_obj_t ota_text(const char *s) {
    return mp_obj_new_str(s, strlen(s));
}

// A failed call's reason as the exception the façade shows the kid.
static void ota_raise(void) {
    nlr_raise(mp_obj_new_exception_arg1(&mp_type_ValueError, ota_text(moy_ota_error())));
}

static mp_obj_t mod_ota_keys(void) {
    int n = moy_ota_keys();
    mp_obj_t items[4];
    for (int i = 0; i < n && i < 4; i++) {
        items[i] = mp_obj_new_bytes(moy_ota_key(i)->n, MOY_OTA_KEY_BYTES);
    }
    return mp_obj_new_tuple(n < 4 ? n : 4, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_keys_obj, mod_ota_keys);

// Keys as the Python verifier takes them, ((modulus_hex, exponent), ...), into
// `out` (up to `max`): the count, or -1 for None (the baked keys). An entry
// that is not one is skipped.
static int keys_of(mp_obj_t o, moy_ota_key_t *out, int max) {
    if (o == mp_const_none) {
        return -1;
    }
    size_t n, k = 0;
    mp_obj_t *items;
    mp_obj_get_array(o, &n, &items);
    for (size_t i = 0; i < n && (int)k < max; i++) {
        size_t m;
        mp_obj_t *pair;
        if (!mp_obj_is_type(items[i], &mp_type_tuple)) {
            continue;
        }
        mp_obj_get_array(items[i], &m, &pair);
        if (m < 1 || !mp_obj_is_str(pair[0])) {
            continue;
        }
        size_t hn;
        const char *h = mp_obj_str_get_data(pair[0], &hn);
        if (moy_ota_key_hex(h, hn, &out[k]) == 0) {
            k++;
        }
    }
    return (int)k;
}

#define KEYS_MAX 4

// ota_verify(payload, sig_hex, keys=None) -> whether a key signed it.
static mp_obj_t mod_ota_verify(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(args[0], &b, MP_BUFFER_READ);
    if (args[1] == mp_const_none || !mp_obj_is_str(args[1])) {
        return mp_const_false;
    }
    moy_ota_key_t *keys = m_new(moy_ota_key_t, KEYS_MAX);
    int nk = keys_of(n_args > 2 ? args[2] : mp_const_none, keys, KEYS_MAX);
    size_t sn;
    const char *s = mp_obj_str_get_data(args[1], &sn);
    int ok = nk == 0 ? 0 : moy_ota_verify(b.buf, b.len, s, sn, nk < 0 ? NULL : keys, nk);
    m_del(moy_ota_key_t, keys, KEYS_MAX);
    return mp_obj_new_bool(ok);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_ota_verify_obj, 2, 3, mod_ota_verify);

// ota_judge(text, board, require_sig, keys=None) -> None, or why it is refused.
static mp_obj_t mod_ota_judge(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *t = mp_obj_str_get_data(args[0], &n);
    moy_ota_key_t *keys = m_new(moy_ota_key_t, KEYS_MAX);
    int nk = keys_of(n_args > 3 ? args[3] : mp_const_none, keys, KEYS_MAX);
    char why[MOY_OTA_ERR_MAX] = "no usable key";
    int rc = nk == 0 ? MOY_OTA_ERR
             : moy_ota_judge(t, n, args[1] == mp_const_none ? NULL : mp_obj_str_get_str(args[1]),
                             mp_obj_is_true(args[2]), nk < 0 ? NULL : keys, nk, why, sizeof(why));
    m_del(moy_ota_key_t, keys, KEYS_MAX);
    return rc == MOY_OTA_OK ? mp_const_none : ota_text(why);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_ota_judge_obj, 3, 4, mod_ota_judge);

// ota_judge_c6(text, require_sig, keys=None) -> None, or why it is refused.
static mp_obj_t mod_ota_judge_c6(size_t n_args, const mp_obj_t *args) {
    size_t n;
    const char *t = mp_obj_str_get_data(args[0], &n);
    moy_ota_key_t *keys = m_new(moy_ota_key_t, KEYS_MAX);
    int nk = keys_of(n_args > 2 ? args[2] : mp_const_none, keys, KEYS_MAX);
    char why[MOY_OTA_ERR_MAX] = "no usable key";
    int rc = nk == 0 ? MOY_OTA_ERR
             : moy_ota_judge_c6(t, n, mp_obj_is_true(args[1]), nk < 0 ? NULL : keys, nk,
                                why, sizeof(why));
    m_del(moy_ota_key_t, keys, KEYS_MAX);
    return rc == MOY_OTA_OK ? mp_const_none : ota_text(why);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_ota_judge_c6_obj, 2, 3, mod_ota_judge_c6);

// ota_canonical(text) / ota_canonical_c6(text) -> the bytes a signature
// covers, or None when the manifest does not make one.
static mp_obj_t canon_of(mp_obj_t text, size_t (*fn)(const char *, size_t, char *, size_t)) {
    size_t n;
    const char *t = mp_obj_str_get_data(text, &n);
    size_t need = fn(t, n, NULL, 0);
    if (need == (size_t)-1) {
        return mp_const_none;
    }
    vstr_t v;
    vstr_init_len(&v, need);
    fn(t, n, v.buf, need);
    return mp_obj_new_bytes_from_vstr(&v);
}

static mp_obj_t mod_ota_canonical(mp_obj_t text) {
    return canon_of(text, moy_ota_canonical);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_ota_canonical_obj, mod_ota_canonical);

static mp_obj_t mod_ota_canonical_c6(mp_obj_t text) {
    return canon_of(text, moy_ota_canonical_c6);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_ota_canonical_c6_obj, mod_ota_canonical_c6);

// ota_check(url, board, require_sig) -> (0, text) | (1, None) | (-1, reason)
static mp_obj_t mod_ota_check(mp_obj_t url, mp_obj_t board, mp_obj_t req) {
    char *text = NULL;
    size_t n = 0;
    int rc = moy_ota_check(mp_obj_str_get_str(url), mp_obj_str_get_str(board),
                           mp_obj_is_true(req), &text, &n);
    mp_obj_t o[2] = {MP_OBJ_NEW_SMALL_INT(rc), mp_const_none};
    if (rc == MOY_OTA_OK) {
        o[1] = mp_obj_new_str(text, n);
        moy_net_free(text);
    } else if (rc == MOY_OTA_ERR) {
        o[1] = ota_text(moy_ota_error());
    }
    return mp_obj_new_tuple(2, o);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_ota_check_obj, mod_ota_check);



// ota_dl_begin(url, size, sha256, sink): raises ValueError(reason).
static mp_obj_t mod_ota_dl_begin(size_t n_args, const mp_obj_t *args) {
    if (moy_ota_dl_begin(mp_obj_str_get_str(args[0]), (uint32_t)mp_obj_get_int(args[1]),
                         mp_obj_str_get_str(args[2]), mp_obj_get_int(args[3])) != 0) {
        ota_raise();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_ota_dl_begin_obj, 4, 4, mod_ota_dl_begin);

static mp_obj_t mod_ota_dl_step(mp_obj_t max) {
    return MP_OBJ_NEW_SMALL_INT(moy_ota_dl_step((uint32_t)mp_obj_get_int(max)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_ota_dl_step_obj, mod_ota_dl_step);

static mp_obj_t mod_ota_dl_finish(void) {
    return mp_obj_new_bool(moy_ota_dl_finish() == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_dl_finish_obj, mod_ota_dl_finish);

static mp_obj_t mod_ota_cancel(void) {
    moy_ota_cancel();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_cancel_obj, mod_ota_cancel);

static mp_obj_t mod_ota_slot_begin(mp_obj_t size) {
    if (moy_ota_slot_begin((uint32_t)mp_obj_get_int(size)) != 0) {
        ota_raise();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_ota_slot_begin_obj, mod_ota_slot_begin);

static mp_obj_t mod_ota_slot_write(mp_obj_t data) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(data, &b, MP_BUFFER_READ);
    return mp_obj_new_bool(moy_ota_slot_write(b.buf, b.len) == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_ota_slot_write_obj, mod_ota_slot_write);

static mp_obj_t mod_ota_slot_close(void) {
    return mp_obj_new_bool(moy_ota_slot_close() == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_slot_close_obj, mod_ota_slot_close);

// ota_activate() -> the slot's label, or None (the reason in ota_state).
static mp_obj_t mod_ota_activate(void) {
    char label[24];
    if (moy_ota_activate(label, sizeof(label)) != 0) {
        return mp_const_none;
    }
    return ota_text(label);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_activate_obj, mod_ota_activate);

static mp_obj_t mod_ota_c6_commit(void) {
    return mp_obj_new_bool(moy_ota_c6_commit() == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_c6_commit_obj, mod_ota_c6_commit);

static mp_obj_t mod_ota_c6_version(void) {
    int v = moy_c6_version != NULL ? moy_c6_version() : -1;
    return v < 0 ? mp_const_none : MP_OBJ_NEW_SMALL_INT(v);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_c6_version_obj, mod_ota_c6_version);

// ota_state() -> (phase, sink, dl_done, dl_total, done, total, error)
static mp_obj_t mod_ota_state(void) {
    moy_ota_state_t st;
    moy_ota_state(&st);
    mp_obj_t o[7] = {
        MP_OBJ_NEW_SMALL_INT(st.phase), MP_OBJ_NEW_SMALL_INT(st.sink),
        mp_obj_new_int_from_uint(st.dl_done), mp_obj_new_int_from_uint(st.dl_total),
        mp_obj_new_int_from_uint(st.done), mp_obj_new_int_from_uint(st.total),
        ota_text(st.err),
    };
    return mp_obj_new_tuple(7, o);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_ota_state_obj, mod_ota_state);

// http_open(url, agent) -> (handle, status, content_length); OSError(errno).
static mp_obj_t mod_http_open(mp_obj_t url, mp_obj_t agent) {
    int status = 0;
    uint32_t clen = 0;
    int h = moy_httpc_open(mp_obj_str_get_str(url), mp_obj_str_get_str(agent), &status, &clen);
    if (h < 0) {
        mp_raise_OSError(-h);
    }
    mp_obj_t o[3] = {MP_OBJ_NEW_SMALL_INT(h), MP_OBJ_NEW_SMALL_INT(status),
                     mp_obj_new_int_from_uint(clen)};
    return mp_obj_new_tuple(3, o);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_http_open_obj, mod_http_open);

static mp_obj_t mod_http_readinto(mp_obj_t h, mp_obj_t buf) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(buf, &b, MP_BUFFER_WRITE);
    int k = moy_httpc_read(mp_obj_get_int(h), b.buf, b.len);
    if (k < 0) {
        mp_raise_OSError(-k);
    }
    return MP_OBJ_NEW_SMALL_INT(k);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_http_readinto_obj, mod_http_readinto);

static mp_obj_t mod_http_close(mp_obj_t h) {
    moy_httpc_close(mp_obj_get_int(h));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_http_close_obj, mod_http_close);

static const mp_rom_map_elem_t moy_net_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_net)},
    {MP_ROM_QSTR(MP_QSTR_parse_request), MP_ROM_PTR(&mod_parse_request_obj)},
    {MP_ROM_QSTR(MP_QSTR_query_param), MP_ROM_PTR(&mod_query_param_obj)},
    {MP_ROM_QSTR(MP_QSTR_http_response), MP_ROM_PTR(&mod_http_response_obj)},
    {MP_ROM_QSTR(MP_QSTR_encode_batch), MP_ROM_PTR(&mod_encode_batch_obj)},
    {MP_ROM_QSTR(MP_QSTR_decode_batch), MP_ROM_PTR(&mod_decode_batch_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_password), MP_ROM_PTR(&mod_wifi_password_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_remember), MP_ROM_PTR(&mod_wifi_remember_obj)},
    {MP_ROM_QSTR(MP_QSTR_sync_batch), MP_ROM_PTR(&mod_sync_batch_obj)},
    {MP_ROM_QSTR(MP_QSTR_sync_apply), MP_ROM_PTR(&mod_sync_apply_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_start), MP_ROM_PTR(&mod_web_start_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_stop), MP_ROM_PTR(&mod_web_stop_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_set_pin), MP_ROM_PTR(&mod_web_set_pin_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_poll), MP_ROM_PTR(&mod_web_poll_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_state), MP_ROM_PTR(&mod_web_state_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_events), MP_ROM_PTR(&mod_web_events_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_take), MP_ROM_PTR(&mod_web_take_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_answer), MP_ROM_PTR(&mod_web_answer_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_handle), MP_ROM_PTR(&mod_web_handle_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_pack), MP_ROM_PTR(&mod_web_pack_obj)},
    {MP_ROM_QSTR(MP_QSTR_web_stamp), MP_ROM_PTR(&mod_web_stamp_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_keys), MP_ROM_PTR(&mod_ota_keys_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_verify), MP_ROM_PTR(&mod_ota_verify_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_judge), MP_ROM_PTR(&mod_ota_judge_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_canonical), MP_ROM_PTR(&mod_ota_canonical_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_canonical_c6), MP_ROM_PTR(&mod_ota_canonical_c6_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_check), MP_ROM_PTR(&mod_ota_check_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_judge_c6), MP_ROM_PTR(&mod_ota_judge_c6_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_dl_begin), MP_ROM_PTR(&mod_ota_dl_begin_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_dl_step), MP_ROM_PTR(&mod_ota_dl_step_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_dl_finish), MP_ROM_PTR(&mod_ota_dl_finish_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_cancel), MP_ROM_PTR(&mod_ota_cancel_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_slot_begin), MP_ROM_PTR(&mod_ota_slot_begin_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_slot_write), MP_ROM_PTR(&mod_ota_slot_write_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_slot_close), MP_ROM_PTR(&mod_ota_slot_close_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_activate), MP_ROM_PTR(&mod_ota_activate_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_c6_commit), MP_ROM_PTR(&mod_ota_c6_commit_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_c6_version), MP_ROM_PTR(&mod_ota_c6_version_obj)},
    {MP_ROM_QSTR(MP_QSTR_ota_state), MP_ROM_PTR(&mod_ota_state_obj)},
    {MP_ROM_QSTR(MP_QSTR_http_open), MP_ROM_PTR(&mod_http_open_obj)},
    {MP_ROM_QSTR(MP_QSTR_http_readinto), MP_ROM_PTR(&mod_http_readinto_obj)},
    {MP_ROM_QSTR(MP_QSTR_http_close), MP_ROM_PTR(&mod_http_close_obj)},
    #if defined(MOY_NET_WIFI) && MOY_NET_WIFI
    {MP_ROM_QSTR(MP_QSTR_wifi_on), MP_ROM_PTR(&mod_wifi_on_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_off), MP_ROM_PTR(&mod_wifi_off_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_connect), MP_ROM_PTR(&mod_wifi_connect_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_disconnect), MP_ROM_PTR(&mod_wifi_disconnect_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_forget), MP_ROM_PTR(&mod_wifi_forget_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_status), MP_ROM_PTR(&mod_wifi_status_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_scan), MP_ROM_PTR(&mod_wifi_scan_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_mac), MP_ROM_PTR(&mod_wifi_mac_obj)},
    {MP_ROM_QSTR(MP_QSTR_wifi_ps), MP_ROM_PTR(&mod_wifi_ps_obj)},
    #endif
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
