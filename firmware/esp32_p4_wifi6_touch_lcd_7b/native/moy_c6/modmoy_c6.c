// The P4's ESP-NOW: the C6's radio behind the esp_now_* C API (#7/#65).
//
// This file is what makes `MICROPY_PY_ESPNOW (1)` link on a board whose SoC
// has no radio: it implements the seventeen esp_now_*/espnow-rate symbols
// MicroPython's stock modespnow.c calls, as thin wrappers over ESP-Hosted's
// custom-RPC seam to the C6 (protocol: espnow_shim_proto.h, ONE body with the
// slave; plan + verdicts: docs/espnow_p4_2026-08.md). Everything above this
// file is stock: modespnow.c owns the Python API and its rxbuf ring,
// device/moy_espnow.py owns discovery/pairing/lockstep and runs unchanged.
//
// Division of labour, decided:
//   * The PEER TABLE lives HERE, mirrored to the slave fire-and-forget. Every
//     peer verb modespnow calls (add/del/mod/get/fetch/num/exist) answers
//     from the mirror without an RPC round-trip; the slave upserts on
//     ADD_PEER so mirror and radio converge. The table is only ever touched
//     from the MicroPython task (modespnow is the sole caller).
//   * INIT/DEINIT/PING are synchronous handshakes (semaphore + timeout): a
//     console with a stock C6 -- no shim on the slave -- gets a TIMEOUT, so
//     esp_now_init() fails cleanly, modespnow raises OSError, and
//     moy_espnow.start() degrades to "no radio", never a crash. That is the
//     same soft-fail rule the S3 boards live by.
//   * SEND is fire-and-forget with an error-only ACK. The real delivery
//     report is SEND_STATUS (the radio's own send callback, forwarded); an
//     error ACK synthesizes a FAILED send_cb so modespnow's sync accounting
//     (tx_responses vs tx_packets) never hangs on a slave-side error.
//
// The hosted callbacks (ACK/RECV/SEND_STATUS) run on the hosted RPC task.
// modespnow's recv_cb is safe there by upstream's own design -- on a radio
// SoC it runs on the WiFi task: single producer into the ring, consumer on
// the MP task, callbacks scheduled via mp_sched_schedule.

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "py/obj.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "py/mperrno.h"

#include "esp_err.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "esp_hosted.h"

#include "espnow_shim_proto.h"

#define MOYC6_PEERS_MAX     (8)     // broadcast + a match peer + slack
#define MOYC6_HANDSHAKE_MS  (2000)  // covers a busy SDIO; a stock slave never answers

// -- state -------------------------------------------------------------------

static esp_now_recv_cb_t s_recv_cb;
static esp_now_send_cb_t s_send_cb;
static bool s_inited;

static esp_now_peer_info_t s_peers[MOYC6_PEERS_MAX];
static bool s_peer_used[MOYC6_PEERS_MAX];
static int s_fetch_at;              // esp_now_fetch_peer iterator

static SemaphoreHandle_t s_ack_sem;
static uint8_t s_ack_verb;          // the verb the waiter wants answered
static volatile int32_t s_ack_err;

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_rx_packets, s_rx_dropped, s_tx_packets;
static uint32_t s_acks, s_ack_errors;
static int32_t s_last_err;

// -- hosted plumbing ---------------------------------------------------------

static esp_err_t moyc6_rpc(const void *msg, size_t msg_len,
    const uint8_t *payload, size_t payload_len) {
    if (payload_len == 0) {
        return esp_hosted_send_custom_data(MOYC6_H2S, (const uint8_t *)msg, msg_len);
    }
    // One contiguous message: the seam has no scatter-gather.
    uint8_t buf[sizeof(moyc6_send_t) + ESP_NOW_MAX_DATA_LEN];
    if (msg_len + payload_len > sizeof(buf)) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(buf, msg, msg_len);
    memcpy(buf + msg_len, payload, payload_len);
    return esp_hosted_send_custom_data(MOYC6_H2S, buf, msg_len + payload_len);
}

static esp_err_t moyc6_handshake(uint8_t verb) {
    if (s_ack_sem == NULL) {
        s_ack_sem = xSemaphoreCreateBinary();
        if (s_ack_sem == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }
    xSemaphoreTake(s_ack_sem, 0);   // drain a stale give
    s_ack_verb = verb;
    moyc6_hdr_t msg = { .proto = MOYC6_PROTO_VERSION, .verb = verb };
    esp_err_t err = moyc6_rpc(&msg, sizeof(msg), NULL, 0);
    if (err != ESP_OK) {
        return err;                 // transport down beats waiting on it
    }
    if (xSemaphoreTake(s_ack_sem, pdMS_TO_TICKS(MOYC6_HANDSHAKE_MS)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;     // a stock slave: no shim, no answer
    }
    return (esp_err_t)s_ack_err;
}

static void moyc6_on_ack(const uint8_t *data, size_t len) {
    if (len < sizeof(moyc6_ack_t)) {
        return;
    }
    moyc6_ack_t ack;
    memcpy(&ack, data, sizeof(ack));
    portENTER_CRITICAL(&s_lock);
    s_acks++;
    if (ack.err != ESP_OK) {
        s_ack_errors++;
        s_last_err = ack.err;
    }
    portEXIT_CRITICAL(&s_lock);
    if (ack.verb == MOYC6_V_SEND && ack.err != ESP_OK && s_send_cb != NULL) {
        // Balance modespnow's tx accounting: the radio never took this one,
        // so its real send callback will never fire.
        esp_now_send_info_t info = { 0 };
        s_send_cb(&info, ESP_NOW_SEND_FAIL);
    }
    if (s_ack_sem != NULL && ack.verb == s_ack_verb) {
        xSemaphoreGive(s_ack_sem);
    }
}

static void moyc6_on_recv(const uint8_t *data, size_t len) {
    if (len < sizeof(moyc6_recv_t)) {
        return;
    }
    moyc6_recv_t hdr;
    memcpy(&hdr, data, sizeof(hdr));
    if (len < sizeof(hdr) + hdr.len || s_recv_cb == NULL) {
        portENTER_CRITICAL(&s_lock);
        s_rx_dropped++;
        portEXIT_CRITICAL(&s_lock);
        return;
    }
    // modespnow reads src_addr and rx_ctrl->rssi; give it exactly that shape.
    wifi_pkt_rx_ctrl_t rx_ctrl = { 0 };
    rx_ctrl.rssi = hdr.rssi;
    esp_now_recv_info_t info = { 0 };
    info.src_addr = hdr.src;
    info.des_addr = hdr.dst;
    info.rx_ctrl = &rx_ctrl;
    portENTER_CRITICAL(&s_lock);
    s_rx_packets++;
    portEXIT_CRITICAL(&s_lock);
    s_recv_cb(&info, data + sizeof(hdr), hdr.len);
}

static void moyc6_on_send_status(const uint8_t *data, size_t len) {
    if (len < sizeof(moyc6_send_status_t) || s_send_cb == NULL) {
        return;
    }

    moyc6_send_status_t st;
    memcpy(&st, data, sizeof(st));
    esp_now_send_info_t info = { 0 };
    s_send_cb(&info, st.status == ESP_NOW_SEND_SUCCESS
        ? ESP_NOW_SEND_SUCCESS : ESP_NOW_SEND_FAIL);
}

// ONE hosted callback for everything slave->host: the handler table defaults
// to 3 slots per processor, so each side registers exactly one id and the
// verb byte does the fan-out (see the proto header's rationale).
static void moyc6_on_s2h(uint32_t id, const uint8_t *data, size_t len, void *ctx) {
    (void)id; (void)ctx;
    if (len < sizeof(moyc6_hdr_t) || data[0] != MOYC6_PROTO_VERSION) {
        return;
    }
    switch (data[1]) {
        case MOYC6_V_ACK:
            moyc6_on_ack(data, len);
            break;
        case MOYC6_V_RECV:
            moyc6_on_recv(data, len);
            break;
        case MOYC6_V_SEND_STATUS:
            moyc6_on_send_status(data, len);
            break;
        default:
            break;
    }
}

// -- the esp_now_* surface modespnow.c links ---------------------------------

esp_err_t esp_now_init(void) {
    if (s_inited) {
        return ESP_OK;
    }
    esp_err_t err = esp_hosted_register_custom_callback(MOYC6_S2H, moyc6_on_s2h, NULL);
    if (err != ESP_OK) {
        return err;
    }
    err = moyc6_handshake(MOYC6_V_INIT);
    if (err != ESP_OK) {
        return err;
    }
    memset(s_peer_used, 0, sizeof(s_peer_used));
    s_fetch_at = 0;
    s_inited = true;
    return ESP_OK;
}

esp_err_t esp_now_deinit(void) {
    if (!s_inited) {
        return ESP_OK;
    }
    s_inited = false;
    // Best-effort: the slave tears its side down or times out; either way
    // this host is done listening.
    moyc6_handshake(MOYC6_V_DEINIT);
    esp_hosted_register_custom_callback(MOYC6_S2H, NULL, NULL);
    return ESP_OK;
}

esp_err_t esp_now_register_recv_cb(esp_now_recv_cb_t cb) {
    s_recv_cb = cb;
    return ESP_OK;
}

esp_err_t esp_now_unregister_recv_cb(void) {
    s_recv_cb = NULL;
    return ESP_OK;
}

esp_err_t esp_now_register_send_cb(esp_now_send_cb_t cb) {
    s_send_cb = cb;
    return ESP_OK;
}

esp_err_t esp_now_unregister_send_cb(void) {
    s_send_cb = NULL;
    return ESP_OK;
}

esp_err_t esp_now_send(const uint8_t *peer_addr, const uint8_t *data, size_t len) {
    if (!s_inited) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    if (len > ESP_NOW_MAX_DATA_LEN) {
        return ESP_ERR_ESPNOW_ARG;
    }
    moyc6_send_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_SEND } };
    msg.dst_valid = peer_addr != NULL;
    if (peer_addr != NULL) {
        memcpy(msg.dst, peer_addr, 6);
    }
    msg.len = (uint16_t)len;
    esp_err_t err = moyc6_rpc(&msg, sizeof(msg), data, len);
    if (err == ESP_OK) {
        portENTER_CRITICAL(&s_lock);
        s_tx_packets++;
        portEXIT_CRITICAL(&s_lock);
    }
    return err;
}

static int moyc6_peer_find(const uint8_t *mac) {
    for (int i = 0; i < MOYC6_PEERS_MAX; i++) {
        if (s_peer_used[i] && memcmp(s_peers[i].peer_addr, mac, 6) == 0) {
            return i;
        }
    }
    return -1;
}

static esp_err_t moyc6_peer_push(const esp_now_peer_info_t *peer) {
    moyc6_peer_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_ADD_PEER } };
    memcpy(msg.mac, peer->peer_addr, 6);
    msg.channel = peer->channel;
    msg.ifidx = (uint8_t)peer->ifidx;
    msg.encrypt = peer->encrypt;
    memcpy(msg.lmk, peer->lmk, sizeof(msg.lmk));
    return moyc6_rpc(&msg, sizeof(msg), NULL, 0);
}

esp_err_t esp_now_add_peer(const esp_now_peer_info_t *peer) {
    if (!s_inited) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    if (peer == NULL) {
        return ESP_ERR_ESPNOW_ARG;
    }
    if (moyc6_peer_find(peer->peer_addr) >= 0) {
        return ESP_ERR_ESPNOW_EXIST;
    }
    for (int i = 0; i < MOYC6_PEERS_MAX; i++) {
        if (!s_peer_used[i]) {
            s_peers[i] = *peer;
            s_peer_used[i] = true;
            return moyc6_peer_push(peer);
        }
    }
    return ESP_ERR_ESPNOW_FULL;
}

esp_err_t esp_now_mod_peer(const esp_now_peer_info_t *peer) {
    if (!s_inited) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    int i = peer ? moyc6_peer_find(peer->peer_addr) : -1;
    if (i < 0) {
        return ESP_ERR_ESPNOW_NOT_FOUND;
    }
    s_peers[i] = *peer;
    return moyc6_peer_push(peer);    // the slave handler upserts
}

esp_err_t esp_now_del_peer(const uint8_t *peer_addr) {
    if (!s_inited) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    int i = peer_addr ? moyc6_peer_find(peer_addr) : -1;
    if (i < 0) {
        return ESP_ERR_ESPNOW_NOT_FOUND;
    }
    s_peer_used[i] = false;
    moyc6_mac_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_DEL_PEER } };
    memcpy(msg.mac, peer_addr, 6);
    return moyc6_rpc(&msg, sizeof(msg), NULL, 0);
}

esp_err_t esp_now_get_peer(const uint8_t *peer_addr, esp_now_peer_info_t *peer) {
    int i = peer_addr ? moyc6_peer_find(peer_addr) : -1;
    if (i < 0) {
        return ESP_ERR_ESPNOW_NOT_FOUND;
    }
    *peer = s_peers[i];
    return ESP_OK;
}

bool esp_now_is_peer_exist(const uint8_t *peer_addr) {
    return peer_addr != NULL && moyc6_peer_find(peer_addr) >= 0;
}

esp_err_t esp_now_fetch_peer(bool from_head, esp_now_peer_info_t *peer) {
    if (from_head) {
        s_fetch_at = 0;
    }
    while (s_fetch_at < MOYC6_PEERS_MAX) {
        int i = s_fetch_at++;
        if (s_peer_used[i]) {
            *peer = s_peers[i];
            return ESP_OK;
        }
    }
    return ESP_ERR_ESPNOW_NOT_FOUND;
}

esp_err_t esp_now_get_peer_num(esp_now_peer_num_t *num) {
    int total = 0, enc = 0;
    for (int i = 0; i < MOYC6_PEERS_MAX; i++) {
        if (s_peer_used[i]) {
            total++;
            if (s_peers[i].encrypt) {
                enc++;
            }
        }
    }
    num->total_num = total;
    num->encrypt_num = enc;
    return ESP_OK;
}

esp_err_t esp_now_set_pmk(const uint8_t *pmk) {
    if (!s_inited) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    moyc6_pmk_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_SET_PMK } };
    memcpy(msg.pmk, pmk, sizeof(msg.pmk));
    return moyc6_rpc(&msg, sizeof(msg), NULL, 0);
}

esp_err_t esp_wifi_config_espnow_rate(wifi_interface_t ifx, wifi_phy_rate_t rate) {
    if (!s_inited) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    moyc6_rate_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_SET_RATE } };
    msg.ifidx = (uint8_t)ifx;
    msg.rate = (uint32_t)rate;
    return moyc6_rpc(&msg, sizeof(msg), NULL, 0);
}

// -- the moy_c6 Python module: C6 plumbing that is NOT espnow ----------------
//
// fwversion/ping are the Phase D preflight (what does the C6 run, does it
// carry the shim); the ota_* verbs are the streamed slave updater -- the
// moy_ota shape, fed from MicroPython so the image rides the ordinary
// push/serial machinery and is sha-checked in Python before activate.

static mp_obj_t moy_c6_fwversion(void) {
    esp_hosted_coprocessor_fwver_t ver = { 0 };
    if (esp_hosted_get_coprocessor_fwversion(&ver) != 0) {
        return mp_const_none;      // a factory slave may not answer this RPC
    }
    char buf[48];
    int n = snprintf(buf, sizeof(buf), "%u.%u.%u",
        (unsigned)ver.major1, (unsigned)ver.minor1, (unsigned)ver.patch1);
    return mp_obj_new_str(buf, n);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_c6_fwversion_obj, moy_c6_fwversion);

static mp_obj_t moy_c6_ping(void) {
    // TIMEOUT means "no shim on the slave" -- the honest capability probe.
    return mp_obj_new_bool(moyc6_handshake(MOYC6_V_PING) == ESP_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_c6_ping_obj, moy_c6_ping);

static mp_obj_t moy_c6_stats(void) {
    mp_obj_t items[6];
    portENTER_CRITICAL(&s_lock);
    items[0] = mp_obj_new_int(s_rx_packets);
    items[1] = mp_obj_new_int(s_rx_dropped);
    items[2] = mp_obj_new_int(s_tx_packets);
    items[3] = mp_obj_new_int(s_acks);
    items[4] = mp_obj_new_int(s_ack_errors);
    items[5] = mp_obj_new_int(s_last_err);
    portEXIT_CRITICAL(&s_lock);
    return mp_obj_new_tuple(6, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_c6_stats_obj, moy_c6_stats);

static mp_obj_t moy_c6_ota_begin(void) {
    int err = esp_hosted_slave_ota_begin();
    if (err != 0) {
        mp_raise_OSError(MP_EIO);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_c6_ota_begin_obj, moy_c6_ota_begin);

static mp_obj_t moy_c6_ota_write(mp_obj_t data) {
    mp_buffer_info_t buf;
    mp_get_buffer_raise(data, &buf, MP_BUFFER_READ);
    int err = esp_hosted_slave_ota_write((uint8_t *)buf.buf, buf.len);
    if (err != 0) {
        mp_raise_OSError(MP_EIO);
    }
    return mp_obj_new_int(buf.len);
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_c6_ota_write_obj, moy_c6_ota_write);

static mp_obj_t moy_c6_ota_end(void) {
    int err = esp_hosted_slave_ota_end();
    if (err != 0) {
        mp_raise_OSError(MP_EIO);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_c6_ota_end_obj, moy_c6_ota_end);

static mp_obj_t moy_c6_ota_activate(void) {
    // Reboots the C6. The caller owns the ceremony around this (sha check
    // first, then re-verify wifi/BLE after -- docs/espnow_p4_2026-08.md
    // Phase D); this verb only pulls the lever.
    int err = esp_hosted_slave_ota_activate();
    if (err != 0) {
        mp_raise_OSError(MP_EIO);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_c6_ota_activate_obj, moy_c6_ota_activate);

static const mp_rom_map_elem_t moy_c6_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_c6) },
    { MP_ROM_QSTR(MP_QSTR_fwversion), MP_ROM_PTR(&moy_c6_fwversion_obj) },
    { MP_ROM_QSTR(MP_QSTR_ping), MP_ROM_PTR(&moy_c6_ping_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&moy_c6_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_ota_begin), MP_ROM_PTR(&moy_c6_ota_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_ota_write), MP_ROM_PTR(&moy_c6_ota_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_ota_end), MP_ROM_PTR(&moy_c6_ota_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_ota_activate), MP_ROM_PTR(&moy_c6_ota_activate_obj) },
};
static MP_DEFINE_CONST_DICT(moy_c6_module_globals, moy_c6_module_globals_table);

const mp_obj_module_t moy_c6_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_c6_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_c6, moy_c6_module);
