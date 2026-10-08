// The WiFi driver (moy_net.h). Its credential rules are moy_net.h's, inline:
// the port compiles a usermod into the elf and into main, and a file whose
// content is a board define must define nothing else.

#include <string.h>

#include "moy_net.h"

#if defined(MOY_NET_WIFI) && MOY_NET_WIFI

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "nvs.h"
#if __has_include("mdns.h") && defined(MOY_NET_MDNS) && MOY_NET_MDNS
#include "mdns.h"
#define HAVE_MDNS 1
#else
#define HAVE_MDNS 0
#endif

// The driver's life (docs/kernel_survival_2026-10.md section 6.1): one
// station, brought up the first time a holder of the spine's lease asks and
// stopped when the last lets go. Its state is written by the event task's
// handlers and read by anyone, a word at a time under the latch.
static portMUX_TYPE s_latch = portMUX_INITIALIZER_UNLOCKED;
static moy_wifi_state_t s_st;
static esp_netif_t *s_netif;
static volatile int s_want;             // a connect is asked for: retry on loss
static esp_timer_handle_t s_retry;      // the next attempt after a loss
static volatile uint8_t s_backoff;      // retries since the last address
static volatile uint8_t s_keep;         // an address came: keep the network

// A lost or failed association is retried after a backoff (0.5 s doubling to
// 8 s), never from the event itself: an immediate esp_wifi_connect() lands on
// the attempt a fresh connect has just started -- on the P4 the event crosses
// to the C6 after that connect -- and the two leave the station associating
// with nothing (reason 8, ASSOC_LEAVE, and no further event). Through the C6
// the leave a connect's own disconnect causes can also end the attempt that
// follows it, so a leave is retried like any loss; an attempt that has
// already got its address is left alone.
static void retry(void *arg) {
    (void)arg;
    if (s_want && !s_st.connected) {
        esp_wifi_connect();
    }
}

static void schedule_retry(void) {
    if (s_retry == NULL) {
        return;
    }
    uint8_t k = s_backoff < 4 ? s_backoff : 4;
    s_backoff++;
    esp_timer_stop(s_retry);
    esp_timer_start_once(s_retry, (uint64_t)500000u << k);
}

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        s_st.on = 1;
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_STOP) {
        s_st.on = 0;
        s_st.connected = 0;
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        portENTER_CRITICAL(&s_latch);
        s_st.connected = 0;
        s_st.ip = 0;
        s_st.reason = d ? d->reason : 0;
        portEXIT_CRITICAL(&s_latch);
        if (s_want) {
            schedule_retry();
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *g = data;
        portENTER_CRITICAL(&s_latch);
        s_st.ip = g ? g->ip_info.ip.addr : 0;
        s_st.connected = 1;
        s_st.reason = 0;
        portEXIT_CRITICAL(&s_latch);
        s_backoff = 0;
        s_keep = 1;
    }
}

static int driver(void) {
    if (s_st.driver) {
        return ESP_OK;
    }
    esp_err_t e = esp_netif_init();
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) {
        return e;
    }
    e = esp_event_loop_create_default();
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) {
        return e;
    }
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi, NULL, NULL);
    s_netif = esp_netif_create_default_wifi_sta();
    const esp_timer_create_args_t ta = {.callback = retry, .name = "moy_wifi"};
    esp_timer_create(&ta, &s_retry);
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    e = esp_wifi_init(&cfg);
    if (e != ESP_OK) {
        return e;
    }
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    e = esp_wifi_set_mode(WIFI_MODE_STA);
    if (e == ESP_OK) {
        s_st.driver = 1;
    }
    return e;
}

int moy_wifi_on(void) {
    int e = driver();
    if (e != ESP_OK) {
        return e;
    }
    e = esp_wifi_start();
    if (e == ESP_OK) {
        s_st.on = 1;
    }
    return e;
}

void moy_wifi_off(void) {
    if (!s_st.driver) {
        return;
    }
    s_want = 0;
    if (s_retry != NULL) {
        esp_timer_stop(s_retry);
    }
    esp_wifi_disconnect();
    esp_wifi_stop();
    portENTER_CRITICAL(&s_latch);
    s_st.on = 0;
    s_st.connected = 0;
    s_st.ip = 0;
    s_st.ssid[0] = '\0';
    portEXIT_CRITICAL(&s_latch);
}

int moy_wifi_connect(const char *ssid, const char *password) {
    int e = moy_wifi_on();
    if (e != ESP_OK) {
        return e;
    }
    wifi_config_t c;
    memset(&c, 0, sizeof(c));
    size_t n = strlen(ssid), p = strlen(password);
    if (n == 0 || n > sizeof(c.sta.ssid) || p >= sizeof(c.sta.password)) {
        return ESP_ERR_INVALID_ARG;
    }
    memcpy(c.sta.ssid, ssid, n);
    memcpy(c.sta.password, password, p);
    s_want = 0;
    s_backoff = 0;
    if (s_retry != NULL) {
        esp_timer_stop(s_retry);
    }
    esp_wifi_disconnect();
    e = esp_wifi_set_config(WIFI_IF_STA, &c);
    if (e != ESP_OK) {
        return e;
    }
    portENTER_CRITICAL(&s_latch);
    memcpy(s_st.ssid, ssid, n);
    s_st.ssid[n] = '\0';
    portEXIT_CRITICAL(&s_latch);
    s_want = 1;
    // A connect asked while the radio is still starting (through the C6 on a
    // P4, right after the lease brought it up) is refused; the backoff asks
    // again, as it does after a loss.
    if (esp_wifi_connect() != ESP_OK) {
        schedule_retry();
    }
    return ESP_OK;
}

void moy_wifi_disconnect(void) {
    s_want = 0;
    if (s_retry != NULL) {
        esp_timer_stop(s_retry);
    }
    if (s_st.driver) {
        esp_wifi_disconnect();
    }
}

// The network that last gave an address, kept in NVS (namespace "moy_wifi")
// so the recovery floor can reach the network with no VM and no store. It is
// written from the caller's task the first time the state is read after an
// address came, and only when it changed.
#define KEEP_NS "moy_wifi"
#define KEEP_KEY "net"

typedef struct {
    char ssid[33];
    char pass[65];
} kept_t;

static void keep_now(void) {
    s_keep = 0;
    wifi_config_t c;
    if (esp_wifi_get_config(WIFI_IF_STA, &c) != ESP_OK) {
        return;
    }
    kept_t k, old;
    memset(&k, 0, sizeof(k));
    memcpy(k.ssid, c.sta.ssid, sizeof(k.ssid) - 1u);
    memcpy(k.pass, c.sta.password, sizeof(k.pass) - 1u);
    nvs_handle_t h;
    if (nvs_open(KEEP_NS, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    size_t n = sizeof(old);
    if (nvs_get_blob(h, KEEP_KEY, &old, &n) != ESP_OK || n != sizeof(old)
        || memcmp(&old, &k, sizeof(k)) != 0) {
        nvs_set_blob(h, KEEP_KEY, &k, sizeof(k));
        nvs_commit(h);
    }
    nvs_close(h);
}

// The provisioning access point (the Zero's first run): open, beside the
// station, which stays up for the setup form's scans. Its address in `*ip`
// (network order); 0, or an ESP error.
static esp_netif_t *s_ap;

int moy_wifi_ap(const char *ssid, uint32_t *ip) {
    int e = driver();
    if (e != ESP_OK) {
        return e;
    }
    if (s_ap == NULL) {
        s_ap = esp_netif_create_default_wifi_ap();
    }
    e = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (e != ESP_OK) {
        return e;
    }
    wifi_config_t c;
    memset(&c, 0, sizeof(c));
    size_t n = strlen(ssid);
    if (n == 0 || n > sizeof(c.ap.ssid)) {
        return ESP_ERR_INVALID_ARG;
    }
    memcpy(c.ap.ssid, ssid, n);
    c.ap.ssid_len = (uint8_t)n;
    c.ap.authmode = WIFI_AUTH_OPEN;
    c.ap.max_connection = 4;
    c.ap.channel = 1;
    e = esp_wifi_set_config(WIFI_IF_AP, &c);
    if (e == ESP_OK) {
        e = moy_wifi_on();
    }
    esp_netif_ip_info_t info;
    *ip = 0;
    if (e == ESP_OK && esp_netif_get_ip_info(s_ap, &info) == ESP_OK) {
        *ip = info.ip.addr;
    }
    return e;
}

int moy_wifi_ap_mac(uint8_t mac[6]) {
    int e = driver();
    return e != ESP_OK ? e : esp_wifi_get_mac(WIFI_IF_AP, mac);
}

// The name the board answers to on the network (`<host>.local`), with its
// web console as an http service, where the board takes the responder
// (MOY_NET_MDNS: the headless Zero, found by name). 0, an ESP error, or
// ESP_ERR_NOT_SUPPORTED in an image without it.
int moy_wifi_mdns(const char *host) {
    #if HAVE_MDNS
    static uint8_t up;
    if (!up) {
        esp_err_t e = mdns_init();
        if (e != ESP_OK) {
            return e;
        }
        mdns_service_add(NULL, "_http", "_tcp", 80, NULL, 0);
        up = 1;
    }
    return mdns_hostname_set(host);
    #else
    (void)host;
    return ESP_ERR_NOT_SUPPORTED;
    #endif
}

int moy_net_link(uint32_t *ip) {
    portENTER_CRITICAL(&s_latch);
    int up = s_st.connected && s_st.ip != 0;
    *ip = s_st.ip;
    portEXIT_CRITICAL(&s_latch);
    return up;
}

int moy_wifi_connect_kept(void) {
    kept_t k;
    nvs_handle_t h;
    size_t n = sizeof(k);
    if (nvs_open(KEEP_NS, NVS_READONLY, &h) != ESP_OK) {
        return ESP_ERR_NOT_FOUND;
    }
    esp_err_t e = nvs_get_blob(h, KEEP_KEY, &k, &n);
    nvs_close(h);
    if (e != ESP_OK || n != sizeof(k) || k.ssid[0] == '\0') {
        return ESP_ERR_NOT_FOUND;
    }
    k.ssid[sizeof(k.ssid) - 1u] = '\0';
    k.pass[sizeof(k.pass) - 1u] = '\0';
    e = moy_wifi_connect(k.ssid, k.pass);
    memset(&k, 0, sizeof(k));
    return e;
}

void moy_wifi_forget_kept(const char *ssid) {
    kept_t k;
    nvs_handle_t h;
    size_t n = sizeof(k);
    if (nvs_open(KEEP_NS, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    if (nvs_get_blob(h, KEEP_KEY, &k, &n) == ESP_OK && n == sizeof(k)
        && strncmp(k.ssid, ssid, sizeof(k.ssid)) == 0) {
        nvs_erase_key(h, KEEP_KEY);
        nvs_commit(h);
    }
    memset(&k, 0, sizeof(k));
    nvs_close(h);
}

void moy_wifi_state(moy_wifi_state_t *out) {
    if (s_keep) {
        keep_now();
    }
    portENTER_CRITICAL(&s_latch);
    *out = s_st;
    portEXIT_CRITICAL(&s_latch);
}

int moy_wifi_scan(moy_wifi_ap_t *out, int max) {
    if (!s_st.on) {
        return -1;
    }
    if (esp_wifi_scan_start(NULL, true) != ESP_OK) {
        return -1;
    }
    uint16_t n = (uint16_t)max;
    wifi_ap_record_t *recs = heap_caps_malloc(sizeof(*recs) * (size_t)max,
                                              MALLOC_CAP_SPIRAM);
    if (recs == NULL) {
        esp_wifi_clear_ap_list();
        return -1;
    }
    if (esp_wifi_scan_get_ap_records(&n, recs) != ESP_OK) {
        heap_caps_free(recs);
        return -1;
    }
    for (int i = 0; i < n; i++) {
        memcpy(out[i].ssid, recs[i].ssid, sizeof(out[i].ssid));
        out[i].ssid[sizeof(out[i].ssid) - 1] = '\0';
        out[i].rssi = recs[i].rssi;
        out[i].auth = (uint8_t)recs[i].authmode;
    }
    heap_caps_free(recs);
    return n;
}

int moy_wifi_mac(uint8_t mac[6]) {
    int e = driver();
    return e != ESP_OK ? e : esp_wifi_get_mac(WIFI_IF_STA, mac);
}

int moy_wifi_ps(int set) {
    if (!s_st.driver) {
        return -1;
    }
    if (set >= 0) {
        esp_wifi_set_ps((wifi_ps_type_t)set);
    }
    wifi_ps_type_t ps;
    return esp_wifi_get_ps(&ps) == ESP_OK ? (int)ps : -1;
}

#endif
