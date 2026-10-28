// The radio link's owner (moy_link.h).

#include "moy_link.h"

// The radio half; the ring is moy_link.h's, inline, so a build that compiles
// this file twice (the port links usermods into the elf and into main) never
// sees it defined twice.

#if defined(MOY_NET_LINK) && MOY_NET_LINK

#include "esp_heap_caps.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"

// The ring and the latch over it: the WiFi task's callback writes, the frame
// reads, and neither holds the latch for more than one record's copy. The
// ring's bytes are PSRAM by the kernel's rule; the statics are the kernel's,
// so a VM stop or a soft reset leaves the callback a live ring.
static moy_link_ring_t s_ring;
static portMUX_TYPE s_latch = portMUX_INITIALIZER_UNLOCKED;
static int s_up;

static void on_recv(const esp_now_recv_info_t *info, const uint8_t *data,
                    int len) {
    if (info == NULL || data == NULL || len < 0) {
        return;
    }
    portENTER_CRITICAL(&s_latch);
    moy_link_ring_put(&s_ring, info->src_addr, data, (uint32_t)len);
    portEXIT_CRITICAL(&s_latch);
}

int moy_link_start(uint32_t rxbuf) {
    if (s_up) {
        return ESP_OK;
    }
    if (s_ring.buf == NULL || s_ring.cap != rxbuf) {
        uint8_t *buf = heap_caps_malloc(rxbuf, MALLOC_CAP_SPIRAM);
        if (buf == NULL) {
            return ESP_ERR_NO_MEM;
        }
        uint8_t *old = s_ring.buf;
        portENTER_CRITICAL(&s_latch);
        moy_link_ring_init(&s_ring, buf, rxbuf);
        portEXIT_CRITICAL(&s_latch);
        heap_caps_free(old);
    }
    esp_err_t err = esp_now_init();
    if (err != ESP_OK) {
        return err;
    }
    err = esp_now_register_recv_cb(on_recv);
    if (err != ESP_OK) {
        esp_now_deinit();
        return err;
    }
    s_up = 1;
    return ESP_OK;
}

void moy_link_stop(void) {
    if (!s_up) {
        return;
    }
    esp_now_unregister_recv_cb();
    esp_now_deinit();
    s_up = 0;
    portENTER_CRITICAL(&s_latch);
    s_ring.head = s_ring.used = 0;
    portEXIT_CRITICAL(&s_latch);
}

int moy_link_rate(int rate) {
    return esp_wifi_config_espnow_rate(WIFI_IF_STA, (wifi_phy_rate_t)rate);
}

int moy_link_add_peer(const uint8_t mac[6]) {
    if (esp_now_is_peer_exist(mac)) {
        return ESP_OK;
    }
    esp_now_peer_info_t p;
    memset(&p, 0, sizeof(p));
    memcpy(p.peer_addr, mac, 6);
    p.ifidx = WIFI_IF_STA;
    return esp_now_add_peer(&p);
}

int moy_link_send(const uint8_t mac[6], const uint8_t *data, size_t len) {
    if (!s_up) {
        return ESP_ERR_ESPNOW_NOT_INIT;
    }
    return esp_now_send(mac, data, len);
}

int moy_link_recv(uint8_t mac[6], uint8_t *data, uint32_t cap) {
    portENTER_CRITICAL(&s_latch);
    int n = moy_link_ring_get(&s_ring, mac, data, cap);
    portEXIT_CRITICAL(&s_latch);
    return n;
}

void moy_link_stats(moy_link_stats_t *s) {
    portENTER_CRITICAL(&s_latch);
    s->rx = s_ring.rx;
    s->drops = s_ring.drops;
    s->peak = s_ring.peak;
    s->cap = s_ring.cap;
    s->used = s_ring.used;
    portEXIT_CRITICAL(&s_latch);
    s->up = s_up;
}

#endif
