// moy_gatt's ops on NimBLE (moy_gatt.h). A scan and a connection attempt are
// GAP master procedures, shared with the keyboard machine through moy_scan.h:
// they are handed to the host task (moy_ble_app_*), which runs the shared
// master, delivers the discovery's advertisements and starts the app's
// connection with this file's GAP callback behind the master's own. The GATT
// procedures start from the caller's task (NimBLE's host API takes its own
// lock), and their callbacks, on the host task, complete them into the
// machine. The keyboard machine's events (moy_ble_task.c) never reach this
// callback, nor the app's the keyboard's.

#include "py/mpconfig.h"

#include "moy_gatt.h"
#include "moy_input.h"

#if defined(MOY_ON_BOARD) && defined(MOY_INPUT_BLE)

#ifndef ESP_PLATFORM
#define ESP_PLATFORM 1
#endif

#include <string.h>

#include "host/ble_hs.h"
#include "moy_ble.h"

static int err_of(int rc) {
    if (rc == 0) {
        return 0;
    }
    if (rc == BLE_HS_EALREADY || rc == BLE_HS_EBUSY) {
        return -MOY_GATT_EBUSY;
    }
    if (rc == BLE_HS_ETIMEOUT) {
        return -MOY_GATT_ETIMEDOUT;
    }
    if (rc == BLE_HS_ENOTCONN) {
        return -MOY_GATT_ENOTCONN;
    }
    return -MOY_GATT_EIO;
}

static uint8_t own_type(void) {
    uint8_t t = BLE_OWN_ADDR_PUBLIC;
    ble_hs_id_infer_auto(0, &t);
    return t;
}

int moy_gatt_gap_event(struct ble_gap_event *e, void *arg) {
    (void)arg;
    switch (e->type) {
        case BLE_GAP_EVENT_CONNECT:
            moy_gatt_on_connect(err_of(e->connect.status), e->connect.conn_handle);
            return 0;
        #ifdef BLE_GAP_EVENT_LINK_ESTAB
        case BLE_GAP_EVENT_LINK_ESTAB:
            // NimBLE's second word on a connection; the first, when it came,
            // already moved the machine on.
            if (moy_gatt_state() == MOY_GATT_CONNECTING) {
                moy_gatt_on_connect(err_of(e->link_estab.status), e->link_estab.conn_handle);
            }
            return 0;
        #endif
        case BLE_GAP_EVENT_DISCONNECT:
            moy_gatt_on_disconnect(e->disconnect.conn.conn_handle);
            return 0;
        case BLE_GAP_EVENT_NOTIFY_RX: {
            uint8_t buf[MOY_GATT_VALUE];
            uint16_t n = OS_MBUF_PKTLEN(e->notify_rx.om);
            if (n > sizeof(buf)) {
                n = sizeof(buf);
            }
            os_mbuf_copydata(e->notify_rx.om, 0, n, buf);
            moy_gatt_on_notify(e->notify_rx.conn_handle, e->notify_rx.attr_handle, buf, n);
            return 0;
        }
        case BLE_GAP_EVENT_ADV_COMPLETE:
            return 0;
        default:
            return 0;
    }
}

static int op_scan(uint32_t ms) {
    moy_ble_app_scan(ms);
    return 0;
}

static int op_connect(const uint8_t addr[6], uint8_t type) {
    moy_ble_app_connect(addr, type);
    return 0;
}

static int op_disconnect(uint16_t conn) {
    if (ble_gap_terminate(conn, BLE_ERR_REM_USER_CONN_TERM) != 0) {
        moy_ble_app_cancel(conn);       // an attempt not yet a link
    }
    return 0;
}

// The first characteristic of the UUID ends the procedure (a callback that
// answers non-zero stops it); a procedure that ends with none found is ENOENT.
static int chr_cb(uint16_t conn, const struct ble_gatt_error *err, const struct ble_gatt_chr *chr,
                  void *arg) {
    (void)conn;
    (void)arg;
    if (err->status == 0 && chr != NULL) {
        moy_gatt_on_found(0, chr->val_handle);
        return 1;
    }
    moy_gatt_on_found(err->status == BLE_HS_EDONE ? -MOY_GATT_ENOENT : err_of(err->status), 0);
    return 0;
}

static int op_find(uint16_t conn, const moy_gatt_uuid_t *u) {
    ble_uuid_any_t uuid;                // NimBLE copies it into the procedure
    if (u->len == 2) {
        uuid.u16.u.type = BLE_UUID_TYPE_16;
        uuid.u16.value = (uint16_t)(u->v[0] | u->v[1] << 8);
    } else {
        uuid.u128.u.type = BLE_UUID_TYPE_128;
        memcpy(uuid.u128.value, u->v, 16);
    }
    return err_of(ble_gattc_disc_chrs_by_uuid(conn, 1, 0xffff, &uuid.u, chr_cb, NULL));
}

static int read_cb(uint16_t conn, const struct ble_gatt_error *err, struct ble_gatt_attr *attr,
                   void *arg) {
    (void)conn;
    (void)arg;
    if (err->status != 0 || attr == NULL) {
        moy_gatt_on_read(err_of(err->status), NULL, 0);
        return 0;
    }
    uint8_t buf[MOY_GATT_VALUE];
    uint16_t n = OS_MBUF_PKTLEN(attr->om);
    if (n > sizeof(buf)) {
        n = sizeof(buf);
    }
    os_mbuf_copydata(attr->om, 0, n, buf);
    moy_gatt_on_read(0, buf, n);
    return 0;
}

static int op_read(uint16_t conn, uint16_t handle) {
    return err_of(ble_gattc_read(conn, handle, read_cb, NULL));
}

static int write_cb(uint16_t conn, const struct ble_gatt_error *err, struct ble_gatt_attr *attr,
                    void *arg) {
    (void)conn;
    (void)attr;
    (void)arg;
    moy_gatt_on_write(err_of(err->status));
    return 0;
}

static int op_write(uint16_t conn, uint16_t handle, const uint8_t *d, size_t n, int rsp) {
    return err_of(rsp ? ble_gattc_write_flat(conn, handle, d, (uint16_t)n, write_cb, NULL)
                      : ble_gattc_write_no_rsp_flat(conn, handle, d, (uint16_t)n));
}

static int op_advertise(const uint8_t *ad, size_t n, uint32_t interval_ms) {
    if (ble_gap_adv_active()) {
        ble_gap_adv_stop();
    }
    int rc = ble_gap_adv_set_data(ad, (int)n);
    if (rc == 0) {
        struct ble_gap_adv_params p = {
            .conn_mode = BLE_GAP_CONN_MODE_NON,
            .disc_mode = BLE_GAP_DISC_MODE_GEN,
            .itvl_min = (uint16_t)(interval_ms * 1000u / 625u),
            .itvl_max = (uint16_t)(interval_ms * 1000u / 625u),
        };
        rc = ble_gap_adv_start(own_type(), NULL, BLE_HS_FOREVER, &p, moy_gatt_gap_event, NULL);
    }
    moy_gatt_on_advertising(err_of(rc));
    return 0;
}

static int op_stop_advertising(void) {
    return err_of(ble_gap_adv_stop());
}

static int op_up(void) {
    return moy_ble_up();
}

static const moy_gatt_ops_t s_ops = {
    op_scan, op_connect, op_disconnect, op_find, op_read, op_write, op_advertise,
    op_stop_advertising, op_up,
};

const moy_gatt_ops_t *moy_gatt_board_ops(void) {
    return &s_ops;
}

#endif
