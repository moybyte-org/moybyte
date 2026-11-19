// The C6 half of the P4's ESP-NOW (#7/#65): the real radio, driven over
// ESP-Hosted's custom-RPC seam.
//
// Compiled INTO the stock esp-hosted-mcu slave ("network_adapter") by this
// directory's build.sh, which stages the slave project out of the SAME
// managed-component checkout the P4 host builds against -- version match by
// construction, not by discipline. The host half is native/p4/moy_c6/
// modmoy_c6.c; the wire protocol is espnow_shim_proto.h, ONE body, copied
// here at stage time. docs/history/espnow_p4_2026-08.md carries the plan.
//
// Shape rules, mirrored from the host file:
//   * one hosted msg id per direction, verb byte inside (the handler table
//     defaults to 3 slots; this uses 1);
//   * INIT/DEINIT/PING always ACK -- they are the handshakes, and a missing
//     ACK is how a stock slave tells the host "no shim here";
//   * every other verb ACKs only on FAILURE (the host's peer mirror owns the
//     happy path, and the radio's own send callback is the delivery report);
//   * ADD_PEER is an UPSERT, so host mod_peer and add_peer converge here;
//   * SEND_BATCH sends its frames in order and answers once, only when the
//     radio refused one (BATCH, below);
//   * SET_RATE sets the rate on every peer, as the C6 takes it (RATE, below);
//   * nothing here waits on hosted's RPC queue except the forwarder task
//     (THE FORWARDER, below).

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "esp_idf_version.h"

#include "esp_hosted_peer_data.h"
#include "espnow_shim_proto.h"

static const char *TAG = "moyc6_espnow";
static bool s_now_inited;

// -- THE FORWARDER -----------------------------------------------------------
//
// esp_hosted_send_custom_data() ends in xQueueSend(portMAX_DELAY) on the
// slave's protocomm request queue (protocomm_pserial.c), whose one consumer,
// pserial_task, also runs every host RPC. A host RPC such as
// esp_wifi_sta_get_ap_info() or esp_wifi_get_channel() runs there and waits
// for the WiFi task (ieee80211_ioctl). A radio callback that sends directly
// parks the WiFi task on a full queue whose consumer is waiting for the WiFi
// task: a deadlock with no timeout on either side, which an inbound ESP-NOW
// flood plus one radio RPC reaches in seconds. An ACK sent directly from
// moyc6_on_h2s, which runs ON pserial_task, blocks on its own queue.
//
// So every slave->host message is copied into this ring without waiting,
// and one task, which neither the WiFi task nor pserial_task ever waits on,
// hands them to hosted. The ring holds messages at their own length, so a
// v2 frame costs its bytes and a 250-byte one no more than before. A
// received frame that would leave MOYC6_FWD_RESERVE bytes or fewer free is
// dropped and counted (the air drops frames too; the link above already
// lives with loss); an ACK or a status may take the reserve, so a flood
// cannot starve a handshake or modespnow's tx accounting.

#define MOYC6_FWD_BYTES     (12 * 1024)
#define MOYC6_FWD_RESERVE   (256)
#define MOYC6_FWD_MAX       (sizeof(moyc6_recv_t) + ESP_NOW_MAX_DATA_LEN_V2)

static RingbufHandle_t s_fwd_rb;
static volatile uint32_t s_rx_dropped, s_ctl_dropped;
static uint32_t s_batch_waits, s_batch_failed;

static void moyc6_fwd_task(void *arg) {
    (void)arg;
    uint32_t told_rx = 0, told_ctl = 0;
    TickType_t told_at = 0;
    for (;;) {
        size_t len = 0;
        uint8_t *item = xRingbufferReceive(s_fwd_rb, &len, portMAX_DELAY);
        if (item == NULL) {
            continue;
        }
        esp_hosted_send_custom_data(MOYC6_S2H, item, len);
        vRingbufferReturnItem(s_fwd_rb, item);
        // At most one line a second: the UART log would slow this task.
        if ((s_rx_dropped != told_rx || s_ctl_dropped != told_ctl)
                && xTaskGetTickCount() - told_at >= pdMS_TO_TICKS(1000)) {
            told_at = xTaskGetTickCount();
            told_rx = s_rx_dropped;
            told_ctl = s_ctl_dropped;
            ESP_LOGW(TAG, "forward ring full: %" PRIu32 " frames, %" PRIu32
                " control messages dropped", told_rx, told_ctl);
        }
    }
}

static void moyc6_forward(const void *hdr, size_t hdr_len,
    const uint8_t *data, size_t data_len, bool control) {
    const size_t len = hdr_len + data_len;
    if (s_fwd_rb == NULL || len > MOYC6_FWD_MAX) {
        return;
    }
    if (!control && xRingbufferGetCurFreeSize(s_fwd_rb) < len + MOYC6_FWD_RESERVE) {
        s_rx_dropped++;
        return;
    }
    // Acquired in place and written once, so the frame is copied one time.
    void *item = NULL;
    if (xRingbufferSendAcquire(s_fwd_rb, &item, len, 0) == pdTRUE && item != NULL) {
        memcpy(item, hdr, hdr_len);
        if (data_len > 0) {
            memcpy((uint8_t *)item + hdr_len, data, data_len);
        }
        xRingbufferSendComplete(s_fwd_rb, item);
    } else {
        if (control) {
            s_ctl_dropped++;
        } else {
            s_rx_dropped++;
        }
    }
}

static void moyc6_ack(uint8_t verb, esp_err_t err) {
    moyc6_ack_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_ACK } };
    msg.verb = verb;
    msg.err = (int32_t)err;
    moyc6_forward(&msg, sizeof(msg), NULL, 0, true);
}

static void moyc6_ack_if_err(uint8_t verb, esp_err_t err) {
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "verb 0x%02x -> %s", verb, esp_err_to_name(err));
        moyc6_ack(verb, err);
    }
}

// -- the radio's own callbacks, forwarded ------------------------------------

// Frames the radio holds (BATCH, below): one more for each send it takes, one
// fewer for each send callback. Counted before the send, because the
// callback (on the WiFi task, above this one) can run before it returns.
static uint32_t s_inflight_n(int d);

static esp_err_t moyc6_now_send(const uint8_t *dst, const uint8_t *data, size_t len) {
    s_inflight_n(1);
    esp_err_t err = esp_now_send(dst, data, len);
    if (err != ESP_OK) {
        s_inflight_n(-1);
    }
    return err;
}

static void moyc6_sent_one(void) {
    s_inflight_n(-1);
}

static void moyc6_now_recv_cb(const esp_now_recv_info_t *info,
    const uint8_t *data, int len) {
    if (len < 0 || len > ESP_NOW_MAX_DATA_LEN_V2) {
        return;
    }
    moyc6_recv_t hdr = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_RECV } };
    memcpy(hdr.src, info->src_addr, 6);
    memcpy(hdr.dst, info->des_addr, 6);
    hdr.rssi = (int8_t)info->rx_ctrl->rssi;
    hdr.len = (uint16_t)len;
    moyc6_forward(&hdr, sizeof(hdr), data, (size_t)len, false);
}

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 5, 0)
static void moyc6_now_send_cb(const uint8_t *mac_addr, esp_now_send_status_t status) {
    const uint8_t *mac = mac_addr;
#else
static void moyc6_now_send_cb(const esp_now_send_info_t *tx_info, esp_now_send_status_t status) {
    const uint8_t *mac = tx_info ? tx_info->des_addr : NULL;
#endif
    moyc6_send_status_t msg = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_SEND_STATUS } };
    moyc6_sent_one();
    if (mac != NULL) {
        memcpy(msg.mac, mac, 6);
    }
    msg.status = (uint8_t)status;
    moyc6_forward(&msg, sizeof(msg), NULL, 0, true);
}

// -- BATCH -------------------------------------------------------------------
//
// One SEND_BATCH is one RPC for up to MOYC6_BATCH_FRAMES frames: the P4 pays
// the seam's round trip once, and this handler (on pserial_task, inside that
// round trip) sends the frames in order.
//
// A batch's frames are held to MOYC6_INFLIGHT handed to the radio and not yet
// reported by its send callback. Each frame the radio holds is a WiFi TX
// buffer from the heap, and the radio drains slower than a batch fills it:
// unheld, a P4 sending v2 frames took the C6's heap from 136 KB free to under
// 6 KB, and an allocation that failed there lost a whole RPC, which the P4
// waits five seconds for. A frame that finds MOYC6_INFLIGHT out, or that the
// radio refuses for want of buffers (ESP_ERR_ESPNOW_NO_MEM, "delay a while
// before sending"), waits a tick and goes again, at most MOYC6_BATCH_WAITS
// ticks a batch; past that it fails, and the link above resends it. So
// pserial_task is never held longer than that, and the WiFi task, which frees
// the buffers, waits on nothing here.

#define MOYC6_INFLIGHT      (8)
#define MOYC6_BATCH_WAITS   (20)

static volatile uint32_t s_inflight;
static portMUX_TYPE s_inflight_lock = portMUX_INITIALIZER_UNLOCKED;

static uint32_t s_inflight_n(int d) {
    portENTER_CRITICAL(&s_inflight_lock);
    if (d > 0 || s_inflight > 0) {
        s_inflight += d;
    }
    uint32_t n = s_inflight;
    portEXIT_CRITICAL(&s_inflight_lock);
    return n;
}

static void moyc6_do_batch(const uint8_t *data, size_t len) {
    if (len < sizeof(moyc6_batch_t)) {
        return;
    }
    moyc6_batch_status_t st = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_BATCH_STATUS } };
    st.count = data[2];
    uint32_t at = sizeof(moyc6_batch_t);
    int waits = MOYC6_BATCH_WAITS;
    for (uint8_t i = 0; i < st.count; i++) {
        moyc6_batch_frame_t f;
        int32_t off = moyc6_batch_next(data, (uint32_t)len, &at, &f);
        if (off < 0) {
            st.failed += (uint8_t)(st.count - i);
            st.err = ESP_ERR_INVALID_SIZE;
            break;
        }
        esp_err_t err;
        for (;;) {
            err = s_inflight >= MOYC6_INFLIGHT ? ESP_ERR_ESPNOW_NO_MEM
                : moyc6_now_send(f.dst_valid ? f.dst : NULL, data + off, f.len);
            if (err != ESP_ERR_ESPNOW_NO_MEM || waits <= 0) {
                break;
            }
            waits--;
            s_batch_waits++;
            vTaskDelay(1);
        }
        if (err != ESP_OK) {
            st.failed++;
            st.err = err;
        }
    }
    s_batch_failed += st.failed;
    if (st.failed > 0) {
        moyc6_forward(&st, sizeof(st), NULL, 0, true);
    }
}

// -- RATE --------------------------------------------------------------------
//
// esp_wifi_config_espnow_rate(), the call the host's SET_RATE names, "only
// works when not use Wi-Fi 6": on the C6 it answers ESP_FAIL and every frame
// leaves at the 1M default (a 1,470-byte frame is then 12 ms of air, about
// 85 a second). The rate is a PEER's here (esp_now_set_peer_rate_config), so
// SET_RATE keeps it and sets it on every peer, the broadcast address among
// them, and ADD_PEER sets it on each peer it adds.

static bool s_rate_set;
static esp_now_rate_config_t s_rate;

static esp_err_t moyc6_rate_peer(const uint8_t *mac) {
    return s_rate_set ? esp_now_set_peer_rate_config(mac, &s_rate) : ESP_OK;
}

static esp_err_t moyc6_set_rate(wifi_phy_rate_t rate) {
    memset(&s_rate, 0, sizeof(s_rate));
    s_rate.phymode = rate <= WIFI_PHY_RATE_11M_S ? WIFI_PHY_MODE_11B
        : rate <= WIFI_PHY_RATE_9M ? WIFI_PHY_MODE_11G : WIFI_PHY_MODE_HT20;
    s_rate.rate = rate;
    s_rate_set = true;
    static const uint8_t all[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
    esp_err_t err = esp_now_is_peer_exist(all) ? moyc6_rate_peer(all) : ESP_OK;
    esp_now_peer_info_t peer;
    for (esp_err_t at = esp_now_fetch_peer(true, &peer); at == ESP_OK;
            at = esp_now_fetch_peer(false, &peer)) {
        esp_err_t e = moyc6_rate_peer(peer.peer_addr);
        if (e != ESP_OK) {
            err = e;
        }
    }
    return err;
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
            moyc6_ack_if_err(verb, moyc6_now_send(
                msg.dst_valid ? msg.dst : NULL, data + sizeof(msg), msg.len));
            break;
        }
        case MOYC6_V_SEND_BATCH:
            moyc6_do_batch(data, len);
            break;
        case MOYC6_V_DIAG: {
            moyc6_diag_t d = { .hdr = { MOYC6_PROTO_VERSION, MOYC6_V_DIAG_REPLY } };
            d.heap_free = esp_get_free_heap_size();
            d.heap_min = esp_get_minimum_free_heap_size();
            d.heap_largest = (uint32_t)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
            d.rx_dropped = s_rx_dropped;
            d.ctl_dropped = s_ctl_dropped;
            d.batch_waits = s_batch_waits;
            d.batch_failed = s_batch_failed;
            moyc6_forward(&d, sizeof(d), NULL, 0, true);
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
            if (err == ESP_OK) {
                err = moyc6_rate_peer(peer.peer_addr);
            }
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
            moyc6_ack_if_err(verb, moyc6_set_rate((wifi_phy_rate_t)msg.rate));
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
    // No forwarder, no shim: the host then reads a stock slave and its
    // ESP-NOW fails inert.
    s_fwd_rb = xRingbufferCreate(MOYC6_FWD_BYTES, RINGBUF_TYPE_NOSPLIT);
    if (s_fwd_rb == NULL || xTaskCreate(moyc6_fwd_task, "moyc6_fwd",
            CONFIG_ESP_HOSTED_DEFAULT_TASK_STACK_SIZE, NULL,
            CONFIG_ESP_HOSTED_DEFAULT_TASK_PRIORITY, NULL) != pdPASS) {
        if (s_fwd_rb != NULL) {
            vRingbufferDelete(s_fwd_rb);
            s_fwd_rb = NULL;
        }
        ESP_LOGE(TAG, "espnow shim: no forwarder, not armed");
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_hosted_register_custom_callback(MOYC6_H2S, moyc6_on_h2s, NULL);
    ESP_LOGI(TAG, "espnow shim %s", err == ESP_OK ? "armed" : "FAILED to register");
    return err;
}
