// The C6 half of the P4's ESP-NOW (#7/#65): the real radio, driven over
// ESP-Hosted's custom-RPC seam.
//
// Compiled INTO the stock esp-hosted-mcu slave ("network_adapter") by this
// directory's build.sh, which stages the slave project out of the SAME
// managed-component checkout the P4 host builds against -- version match by
// construction, not by discipline. The host half is native/moy_c6/
// modmoy_c6.c; the wire protocol is espnow_shim_proto.h, ONE body, copied
// here at stage time. docs/espnow_p4_2026-08.md carries the plan.
//
// Shape rules, mirrored from the host file:
//   * one hosted msg id per direction, verb byte inside (the handler table
//     defaults to 3 slots; this uses 1);
//   * INIT/DEINIT/PING always ACK -- they are the handshakes, and a missing
//     ACK is how a stock slave tells the host "no shim here";
//   * every other verb ACKs only on FAILURE (the host's peer mirror owns the
//     happy path, and the radio's own send callback is the delivery report);
//   * ADD_PEER is an UPSERT, so host mod_peer and add_peer converge here.

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "esp_idf_version.h"

#include "esp_hosted_peer_data.h"
#include "espnow_shim_proto.h"

static const char *TAG = "moyc6_espnow";
static bool s_now_inited;

static void moyc6_ack(uint8_t verb, esp_err_t err) {
    moyc6_ack_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_ACK } };
    msg.verb = verb;
    msg.err = (int32_t)err;
    esp_hosted_send_custom_data(MOYC6_S2H, (uint8_t *)&msg, sizeof(msg));
}

static void moyc6_ack_if_err(uint8_t verb, esp_err_t err) {
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "verb 0x%02x -> %s", verb, esp_err_to_name(err));
        moyc6_ack(verb, err);
    }
}

// -- the radio's own callbacks, forwarded ------------------------------------

static void moyc6_now_recv_cb(const esp_now_recv_info_t *info,
    const uint8_t *data, int len) {
    if (len < 0 || len > ESP_NOW_MAX_DATA_LEN) {
        return;
    }
    uint8_t buf[sizeof(moyc6_recv_t) + ESP_NOW_MAX_DATA_LEN];
    moyc6_recv_t hdr = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_RECV } };
    memcpy(hdr.src, info->src_addr, 6);
    memcpy(hdr.dst, info->des_addr, 6);
    hdr.rssi = (int8_t)info->rx_ctrl->rssi;
    hdr.len = (uint16_t)len;
    memcpy(buf, &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), data, len);
    esp_hosted_send_custom_data(MOYC6_S2H, buf, sizeof(hdr) + len);
}

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 5, 0)
static void moyc6_now_send_cb(const uint8_t *mac_addr, esp_now_send_status_t status) {
    const uint8_t *mac = mac_addr;
#else
static void moyc6_now_send_cb(const esp_now_send_info_t *tx_info, esp_now_send_status_t status) {
    const uint8_t *mac = tx_info ? tx_info->des_addr : NULL;
#endif
    moyc6_send_status_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_SEND_STATUS } };
    if (mac != NULL) {
        memcpy(msg.mac, mac, 6);
    }
    msg.status = (uint8_t)status;
    esp_hosted_send_custom_data(MOYC6_S2H, (uint8_t *)&msg, sizeof(msg));
}

// -- host verbs --------------------------------------------------------------

static esp_err_t moyc6_do_init(void) {
    if (s_now_inited) {
        return ESP_OK;              // a host re-init after its own reboot
    }
    esp_err_t err = esp_now_init();
    if (err == ESP_OK) {
        err = esp_now_register_recv_cb(moyc6_now_recv_cb);
    }
    if (err == ESP_OK) {
        err = esp_now_register_send_cb(moyc6_now_send_cb);
    }
    s_now_inited = err == ESP_OK;
    return err;
}

static esp_err_t moyc6_do_deinit(void) {
    if (!s_now_inited) {
        return ESP_OK;
    }
    s_now_inited = false;
    esp_now_unregister_recv_cb();
    esp_now_unregister_send_cb();
    return esp_now_deinit();
}

static void moyc6_on_h2s(uint32_t id, const uint8_t *data, size_t len, void *ctx) {
    (void)id; (void)ctx;
    if (len < sizeof(moyc6_hdr_t) || data[0] != MOYC6_PROTO_VERSION) {
        return;
    }
    const uint8_t verb = data[1];
    switch (verb) {
        case MOYC6_V_INIT:
            moyc6_ack(verb, moyc6_do_init());
            break;
        case MOYC6_V_DEINIT:
            moyc6_ack(verb, moyc6_do_deinit());
            break;
        case MOYC6_V_PING:
            // "Is the shim here?" -- answered by existing, not by radio state.
            moyc6_ack(verb, ESP_OK);
            break;
        case MOYC6_V_VERSION:
            // "WHICH shim?" -- the ACK's err field carries the build identity
            // (positive, so the host's error accounting must except this
            // verb). A slave that predates the verb ignores it and the host
            // reads the timeout as "older than everything".
            moyc6_ack(verb, MOYC6_SHIM_VERSION);
            break;
        case MOYC6_V_SEND: {
            if (len < sizeof(moyc6_send_t)) {
                break;
            }
            moyc6_send_t msg;
            memcpy(&msg, data, sizeof(msg));
            if (len < sizeof(msg) + msg.len) {
                break;
            }
            moyc6_ack_if_err(verb, esp_now_send(
                msg.dst_valid ? msg.dst : NULL, data + sizeof(msg), msg.len));
            break;
        }
        case MOYC6_V_ADD_PEER: {
            if (len < sizeof(moyc6_peer_t)) {
                break;
            }
            moyc6_peer_t msg;
            memcpy(&msg, data, sizeof(msg));
            esp_now_peer_info_t peer = { 0 };
            memcpy(peer.peer_addr, msg.mac, 6);
            peer.channel = msg.channel;
            peer.ifidx = (wifi_interface_t)msg.ifidx;
            peer.encrypt = msg.encrypt;
            memcpy(peer.lmk, msg.lmk, sizeof(peer.lmk));
            // Upsert: the host's mirror is the authority on which one it is.
            esp_err_t err = esp_now_is_peer_exist(peer.peer_addr)
                ? esp_now_mod_peer(&peer) : esp_now_add_peer(&peer);
            moyc6_ack_if_err(verb, err);
            break;
        }
        case MOYC6_V_DEL_PEER: {
            if (len < sizeof(moyc6_mac_t)) {
                break;
            }
            moyc6_mac_t msg;
            memcpy(&msg, data, sizeof(msg));
            moyc6_ack_if_err(verb, esp_now_del_peer(msg.mac));
            break;
        }
        case MOYC6_V_SET_RATE: {
            if (len < sizeof(moyc6_rate_t)) {
                break;
            }
            moyc6_rate_t msg;
            memcpy(&msg, data, sizeof(msg));
            moyc6_ack_if_err(verb, esp_wifi_config_espnow_rate(
                (wifi_interface_t)msg.ifidx, (wifi_phy_rate_t)msg.rate));
            break;
        }
        case MOYC6_V_SET_PMK: {
            if (len < sizeof(moyc6_pmk_t)) {
                break;
            }
            moyc6_pmk_t msg;
            memcpy(&msg, data, sizeof(msg));
            moyc6_ack_if_err(verb, esp_now_set_pmk(msg.pmk));
            break;
        }
        default:
            break;
    }
}

esp_err_t moyc6_espnow_shim_init(void) {
    esp_err_t err = esp_hosted_register_custom_callback(MOYC6_H2S, moyc6_on_h2s, NULL);
    ESP_LOGI(TAG, "espnow shim %s", err == ESP_OK ? "armed" : "FAILED to register");
    return err;
}
