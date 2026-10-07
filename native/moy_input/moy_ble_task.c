// moy_input's BLE HID central on NimBLE: the kernel owns the host's lifecycle
// and drives moy_hid's machine from its host task (moy_hid.h has the machine).
//
// A board takes it with MOY_INPUT_BLE in mpconfigboard.h (and MOY_INPUT_BLE_HOSTED
// where the radio is a companion chip over ESP-Hosted, the P4s'). MicroPython's
// own `bluetooth` module is not in these images: nothing above the kernel
// touches the NimBLE host, so a soft reset leaves it, the bond and the link up.
//
// ONE TASK. Every GAP and GATT callback, the half-second tick and every verb the
// frame asks for run on NimBLE's host task (a verb is queued and an event
// posted to the host's default queue), so the machine is single-threaded; the
// only shared state is the report latch, moy_hid's lock.
//
// PERSISTENCE is NVS: the bonds are NimBLE's own store (CONFIG_BT_NIMBLE_NVS_PERSIST),
// and the machine's settings -- enabled, the picked keyboard's address and
// name -- are namespace "moy_ble".

#include "py/mpconfig.h"

#include "moy_ble.h"

#if defined(MOY_INPUT_BOARD) && defined(MOY_INPUT_BLE)

// NimBLE's headers find their configuration (esp_nimble_cfg.h) under
// ESP_PLATFORM, which a usermod's sources are not compiled with on this port.
#ifndef ESP_PLATFORM
#define ESP_PLATFORM 1
#endif

#include <string.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs.h"
#include "services/gap/ble_svc_gap.h"
#if defined(MOY_INPUT_BLE_HOSTED)
#include "esp_hosted.h"
#endif

void ble_store_config_init(void);

static moy_hid_t s_hid;
static bool s_up;                   // the host runs
static volatile bool s_synced;
static uint8_t s_own_type;
static struct ble_npl_callout s_tick;
static struct ble_npl_event s_verb_ev;
static struct ble_npl_event s_scan_done_ev;
static moy_drv_lock_t s_verb_lock = portMUX_INITIALIZER_UNLOCKED;

// -- the verbs the frame queues ---------------------------------------------------

enum { V_NONE, V_ENABLE, V_DISABLE, V_DISCOVER, V_PICK, V_FORGET, V_SCAN };
#define VERBS 4
static struct {
    uint8_t v;
    moy_hid_addr_t addr;
} s_verbs[VERBS];
static uint8_t s_nverbs;

static void verb_run(struct ble_npl_event *ev) {
    (void)ev;
    for (;;) {
        uint8_t v;
        moy_hid_addr_t addr;
        MOY_DRV_LOCK(&s_verb_lock);
        if (s_nverbs == 0) {
            MOY_DRV_UNLOCK(&s_verb_lock);
            return;
        }
        v = s_verbs[0].v;
        addr = s_verbs[0].addr;
        memmove(&s_verbs[0], &s_verbs[1], (VERBS - 1) * sizeof(s_verbs[0]));
        s_nverbs--;
        MOY_DRV_UNLOCK(&s_verb_lock);
        switch (v) {
            case V_ENABLE: moy_hid_set_enabled(&s_hid, true); break;
            case V_DISABLE: moy_hid_set_enabled(&s_hid, false); break;
            case V_DISCOVER: moy_hid_discover(&s_hid); break;
            case V_PICK: moy_hid_pick(&s_hid, &addr); break;
            case V_FORGET: moy_hid_forget(&s_hid); break;
            case V_SCAN: moy_hid_scan(&s_hid); break;
        }
    }
}

static bool post(uint8_t v, const moy_hid_addr_t *addr) {
    if (!s_up) {
        return false;
    }
    bool ok = false;
    MOY_DRV_LOCK(&s_verb_lock);
    if (s_nverbs < VERBS) {
        s_verbs[s_nverbs].v = v;
        if (addr) {
            s_verbs[s_nverbs].addr = *addr;
        }
        s_nverbs++;
        ok = true;
    }
    MOY_DRV_UNLOCK(&s_verb_lock);
    if (ok) {
        ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_verb_ev);
    }
    return ok;
}

// -- the stack, as the machine's ops ---------------------------------------------

static int gap_event(struct ble_gap_event *event, void *arg);

static void addr_to_ble(const moy_hid_addr_t *a, ble_addr_t *b) {
    b->type = a->type;
    memcpy(b->val, a->a, 6);
}

static void addr_from_ble(const ble_addr_t *b, moy_hid_addr_t *a) {
    a->type = b->type;
    memcpy(a->a, b->val, 6);
}

static int op_scan(void *ctx, bool picker) {
    (void)ctx;
    // Background scans are PASSIVE at 10% duty (30 ms in every 300 ms): the
    // radio is shared with WiFi and ESP-NOW (#7). The picker's is active and
    // continuous: user-facing, brief, and it wants the names.
    struct ble_gap_disc_params p = {
        .itvl = picker ? 48 : 480,          // 0.625 ms units
        .window = 48,
        .passive = picker ? 0 : 1,
        .filter_duplicates = 0,
    };
    return ble_gap_disc(s_own_type, MOY_HID_SCAN_MS, &p, gap_event, NULL) == 0 ? 0 : -1;
}

static void scan_done_run(struct ble_npl_event *ev) {
    (void)ev;
    moy_hid_on_scan_done(&s_hid);
}

static int op_scan_stop(void *ctx) {
    (void)ctx;
    // A cancelled discovery reports no completion of its own: the machine
    // hears it as one, after this call returns.
    if (ble_gap_disc_cancel() != 0) {
        return -1;
    }
    ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_scan_done_ev);
    return 0;
}

static int op_connect(void *ctx, const moy_hid_addr_t *peer) {
    (void)ctx;
    ble_addr_t a;
    addr_to_ble(peer, &a);
    // The low-latency range BLE allows; the keyboard may settle on another.
    struct ble_gap_conn_params cp = {
        .scan_itvl = 0x30, .scan_window = 0x30,
        .itvl_min = 6, .itvl_max = 12,      // 7.5 .. 15 ms
        .latency = 0, .supervision_timeout = 400,
        .min_ce_len = 0, .max_ce_len = 0,
    };
    return ble_gap_connect(s_own_type, &a, MOY_HID_CONNECT_MS, &cp, gap_event, NULL) == 0 ? 0 : -1;
}

static int op_disconnect(void *ctx, uint16_t conn) {
    (void)ctx;
    return ble_gap_terminate(conn, BLE_ERR_REM_USER_CONN_TERM) == 0 ? 0 : -1;
}

static int op_pair(void *ctx, uint16_t conn) {
    (void)ctx;
    return ble_gap_security_initiate(conn) == 0 ? 0 : -1;
}

static int svc_cb(uint16_t conn, const struct ble_gatt_error *err, const struct ble_gatt_svc *svc,
                  void *arg) {
    (void)arg;
    if (err->status == 0 && svc != NULL) {
        moy_hid_on_svc(&s_hid, conn, svc->start_handle, svc->end_handle,
                       ble_uuid_u16(&svc->uuid.u));
    } else {
        moy_hid_on_svc_done(&s_hid, conn, err->status == BLE_HS_EDONE ? 0 : err->status);
    }
    return 0;
}

static int op_disc_svcs(void *ctx, uint16_t conn) {
    (void)ctx;
    static const ble_uuid16_t HID = BLE_UUID16_INIT(MOY_HID_UUID_SERVICE);
    return ble_gattc_disc_svc_by_uuid(conn, &HID.u, svc_cb, NULL) == 0 ? 0 : -1;
}

static int chr_cb(uint16_t conn, const struct ble_gatt_error *err, const struct ble_gatt_chr *chr,
                  void *arg) {
    (void)arg;
    if (err->status == 0 && chr != NULL) {
        moy_hid_on_chr(&s_hid, conn, chr->def_handle, chr->val_handle, chr->properties,
                       ble_uuid_u16(&chr->uuid.u));
    } else {
        moy_hid_on_chr_done(&s_hid, conn, err->status == BLE_HS_EDONE ? 0 : err->status);
    }
    return 0;
}

static int op_disc_chrs(void *ctx, uint16_t conn, uint16_t start, uint16_t end) {
    (void)ctx;
    return ble_gattc_disc_all_chrs(conn, start, end, chr_cb, NULL) == 0 ? 0 : -1;
}

static int dsc_cb(uint16_t conn, const struct ble_gatt_error *err, uint16_t chr_val_handle,
                  const struct ble_gatt_dsc *dsc, void *arg) {
    (void)arg;
    (void)chr_val_handle;
    if (err->status == 0 && dsc != NULL) {
        moy_hid_on_dsc(&s_hid, conn, dsc->handle, ble_uuid_u16(&dsc->uuid.u));
    } else {
        moy_hid_on_dsc_done(&s_hid, conn, err->status == BLE_HS_EDONE ? 0 : err->status);
    }
    return 0;
}

static int op_disc_dscs(void *ctx, uint16_t conn, uint16_t start, uint16_t end) {
    (void)ctx;
    return ble_gattc_disc_all_dscs(conn, start, end, dsc_cb, NULL) == 0 ? 0 : -1;
}

static int write_cb(uint16_t conn, const struct ble_gatt_error *err, struct ble_gatt_attr *attr,
                    void *arg) {
    (void)arg;
    moy_hid_on_write_done(&s_hid, conn, attr ? attr->handle : 0, err->status);
    return 0;
}

static int op_write(void *ctx, uint16_t conn, uint16_t handle, const uint8_t *src, size_t n,
                    bool response) {
    (void)ctx;
    int rc = response ? ble_gattc_write_flat(conn, handle, src, (uint16_t)n, write_cb, NULL)
                      : ble_gattc_write_no_rsp_flat(conn, handle, src, (uint16_t)n);
    return rc == 0 ? 0 : -1;
}

static void op_forget(void *ctx) {
    (void)ctx;
    ble_store_clear();
}

static void op_save(void *ctx, const moy_hid_t *h) {
    (void)ctx;
    nvs_handle_t nv;
    if (nvs_open("moy_ble", NVS_READWRITE, &nv) != ESP_OK) {
        return;
    }
    nvs_set_u8(nv, "on", h->enabled);
    if (h->has_pref) {
        uint8_t blob[7] = {h->pref.type};
        memcpy(&blob[1], h->pref.a, 6);
        nvs_set_blob(nv, "pref", blob, sizeof(blob));
        nvs_set_str(nv, "name", h->name);
    } else {
        nvs_erase_key(nv, "pref");
        nvs_erase_key(nv, "name");
    }
    nvs_commit(nv);
    nvs_close(nv);
}

static void load(moy_hid_t *h) {
    nvs_handle_t nv;
    if (nvs_open("moy_ble", NVS_READONLY, &nv) != ESP_OK) {
        return;
    }
    uint8_t on = 1;
    nvs_get_u8(nv, "on", &on);
    uint8_t blob[7];
    size_t n = sizeof(blob);
    char name[MOY_HID_NAME] = "";
    size_t len = sizeof(name);
    moy_hid_addr_t pref;
    bool has = nvs_get_blob(nv, "pref", blob, &n) == ESP_OK && n == sizeof(blob);
    if (has) {
        pref.type = blob[0];
        memcpy(pref.a, &blob[1], 6);
        nvs_get_str(nv, "name", name, &len);
    }
    nvs_close(nv);
    moy_hid_load(h, on != 0, has ? &pref : NULL, name);
}

static uint32_t op_ms(void *ctx) {
    (void)ctx;
    return (uint32_t)(esp_timer_get_time() / 1000) & (MOY_INPUT_TICKS_PERIOD - 1u);
}

static const moy_hid_ops_t s_ops = {
    op_scan, op_scan_stop, op_connect, op_disconnect, op_pair, op_disc_svcs, op_disc_chrs,
    op_disc_dscs, op_write, op_forget, op_save, op_ms, NULL,
};

// -- the GAP events ----------------------------------------------------------------

static int gap_event(struct ble_gap_event *event, void *arg) {
    (void)arg;
    struct ble_gap_conn_desc desc;
    switch (event->type) {
        case BLE_GAP_EVENT_DISC: {
            moy_hid_addr_t a;
            addr_from_ble(&event->disc.addr, &a);
            moy_hid_on_scan_result(&s_hid, &a, event->disc.rssi, event->disc.data,
                                   event->disc.length_data);
            return 0;
        }
        case BLE_GAP_EVENT_DISC_COMPLETE:
            moy_hid_on_scan_done(&s_hid);
            return 0;
        case BLE_GAP_EVENT_CONNECT:
            if (event->connect.status == 0
                && ble_gap_conn_find(event->connect.conn_handle, &desc) == 0) {
                moy_hid_addr_t a;
                addr_from_ble(&desc.peer_id_addr, &a);
                moy_hid_on_connect(&s_hid, event->connect.conn_handle, &a);
            } else {
                moy_hid_on_connect_failed(&s_hid);
            }
            return 0;
        case BLE_GAP_EVENT_DISCONNECT:
            moy_hid_on_disconnect(&s_hid, event->disconnect.conn.conn_handle);
            return 0;
        case BLE_GAP_EVENT_NOTIFY_RX: {
            uint8_t buf[16];
            uint16_t n = OS_MBUF_PKTLEN(event->notify_rx.om);
            if (n <= sizeof(buf) && ble_hs_mbuf_to_flat(event->notify_rx.om, buf, n, &n) == 0) {
                moy_hid_on_notify(&s_hid, event->notify_rx.conn_handle, event->notify_rx.attr_handle,
                                  buf, n);
            }
            return 0;
        }
        case BLE_GAP_EVENT_CONN_UPDATE:
            if (ble_gap_conn_find(event->conn_update.conn_handle, &desc) == 0) {
                moy_hid_on_conn_update(&s_hid, event->conn_update.conn_handle, desc.conn_itvl,
                                       event->conn_update.status);
            }
            return 0;
        case BLE_GAP_EVENT_ENC_CHANGE:
            if (ble_gap_conn_find(event->enc_change.conn_handle, &desc) == 0) {
                moy_hid_on_enc_change(&s_hid, event->enc_change.conn_handle,
                                      desc.sec_state.encrypted, desc.sec_state.bonded);
            }
            return 0;
        case BLE_GAP_EVENT_PASSKEY_ACTION: {
            // Just Works is the policy; a keyboard that insists is answered.
            struct ble_sm_io io = {.action = event->passkey.params.action};
            if (io.action == BLE_SM_IOACT_NUMCMP) {
                io.numcmp_accept = 1;
            } else if (io.action == BLE_SM_IOACT_DISP) {
                io.passkey = (uint32_t)(esp_timer_get_time() % 1000000);
            } else {
                return 0;
            }
            ble_sm_inject_io(event->passkey.conn_handle, &io);
            return 0;
        }
        case BLE_GAP_EVENT_REPEAT_PAIRING:
            // The keyboard lost its bond: forget ours and pair again.
            if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &desc) == 0) {
                ble_store_util_delete_peer(&desc.peer_id_addr);
            }
            return BLE_GAP_REPEAT_PAIRING_RETRY;
        default:
            return 0;
    }
}

// -- the host's lifecycle -------------------------------------------------------------

static void tick_run(struct ble_npl_event *ev) {
    (void)ev;
    moy_hid_tick(&s_hid);
    ble_npl_callout_reset(&s_tick, ble_npl_time_ms_to_ticks32(500));
}

static void on_sync(void) {
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &s_own_type);
    s_synced = true;
    moy_hid_started(&s_hid, true, NULL);
    ble_npl_callout_reset(&s_tick, ble_npl_time_ms_to_ticks32(500));
}

static void on_reset(int reason) {
    (void)reason;
    s_synced = false;
}

static void host_task(void *param) {
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static bool s_inited;

moy_hid_t *moy_ble_hid(void) {
    if (!s_inited) {
        moy_input_t *t = moy_input_kernel();
        uint32_t h;
        if (t == NULL || moy_input_source(t, "ble", 0, &h) != MOY_INPUT_OK) {
            return NULL;
        }
        moy_hid_init(&s_hid, &s_ops, t, h);
        load(&s_hid);
        s_inited = true;
    }
    return &s_hid;
}

bool moy_ble_start(void) {
    if (moy_ble_hid() == NULL) {
        return false;
    }
    if (s_up) {
        if (s_hid.enabled && s_hid.state == MOY_HID_DISABLED) {
            post(V_ENABLE, NULL);
        }
        return true;
    }
    #if defined(MOY_INPUT_BLE_HOSTED)
    // Since hosted ~2.8 the companion's BT controller is not initialised or
    // enabled by default: the host does both, with the transport up first,
    // before NimBLE's first HCI command (esp-hosted-mcu#212).
    esp_hosted_connect_to_slave();
    if (esp_hosted_bt_controller_init() != ESP_OK || esp_hosted_bt_controller_enable() != ESP_OK) {
        moy_hid_started(&s_hid, false, "the companion's BT controller did not start");
        return false;
    }
    #endif
    if (nimble_port_init() != ESP_OK) {
        moy_hid_started(&s_hid, false, "NimBLE did not start");
        return false;
    }
    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 0;
    ble_hs_cfg.sm_sc = 0;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_svc_gap_init();
    ble_svc_gap_device_name_set("Moybyte");
    ble_store_config_init();
    ble_npl_callout_init(&s_tick, nimble_port_get_dflt_eventq(), tick_run, NULL);
    ble_npl_event_init(&s_verb_ev, verb_run, NULL);
    ble_npl_event_init(&s_scan_done_ev, scan_done_run, NULL);
    nimble_port_freertos_init(host_task);
    s_up = true;
    return true;
}

void moy_ble_stop(void) {
    if (!s_up) {
        return;
    }
    s_up = false;
    ble_npl_callout_stop(&s_tick);
    nimble_port_stop();
    nimble_port_deinit();
    #if defined(MOY_INPUT_BLE_HOSTED)
    esp_hosted_bt_controller_disable();
    esp_hosted_bt_controller_deinit(false);
    #endif
    s_synced = false;
    moy_hid_stopped(&s_hid);
}

bool moy_ble_up(void) {
    return s_up;
}

bool moy_ble_verb(uint8_t verb, const moy_hid_addr_t *addr) {
    static const uint8_t MAP[] = {
        [MOY_BLE_ENABLE] = V_ENABLE, [MOY_BLE_DISABLE] = V_DISABLE,
        [MOY_BLE_DISCOVER] = V_DISCOVER, [MOY_BLE_PICK] = V_PICK,
        [MOY_BLE_FORGET] = V_FORGET, [MOY_BLE_SCAN] = V_SCAN,
    };
    if (verb >= sizeof(MAP)) {
        return false;
    }
    if (!s_up) {
        // Before the radio runs, the settings are the machine's to change here.
        moy_hid_t *h = moy_ble_hid();
        if (h == NULL) {
            return false;
        }
        if (verb == MOY_BLE_ENABLE || verb == MOY_BLE_DISABLE) {
            h->enabled = verb == MOY_BLE_ENABLE;
            op_save(NULL, h);
            return true;
        }
        if (verb == MOY_BLE_FORGET) {
            h->has_pref = false;
            h->ndev = 0;
            h->name[0] = 0;
            op_save(NULL, h);
            return true;
        }
        return false;
    }
    return post(MAP[verb], addr);
}

#else

moy_hid_t *moy_ble_hid(void) {
    return NULL;
}

bool moy_ble_start(void) {
    return false;
}

void moy_ble_stop(void) {
}

bool moy_ble_up(void) {
    return false;
}

bool moy_ble_verb(uint8_t verb, const moy_hid_addr_t *addr) {
    (void)verb;
    (void)addr;
    return false;
}

#endif
