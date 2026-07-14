// P4 BLE-HID notification fast path.
//
// MicroPython's ESP32 NimBLE port dispatches Bluetooth IRQs synchronously from
// the NimBLE host task.  That task must acquire the VM GIL before it can call a
// Python handler, so a long RGB565 render can delay keyboard notifications and
// eventually back up the hosted C6 transport.  This tiny queue intercepts only
// registered GATTC notification handles before Python dispatch, copies their
// small HID reports immediately, and lets the frame loop drain them later.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "esp_timer.h"
#include "py/obj.h"
#include "py/runtime.h"

#define MOY_BLE_HID_HANDLES_MAX (4)
#define MOY_BLE_HID_REPORT_MAX (32)
#define MOY_BLE_HID_QUEUE_LEN (64)

typedef struct {
    uint16_t handle;
    uint8_t len;
    uint8_t data[MOY_BLE_HID_REPORT_MAX];
    int64_t arrived_us;
} moy_ble_hid_event_t;

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static bool s_enabled;
static uint16_t s_conn;
static uint16_t s_handles[MOY_BLE_HID_HANDLES_MAX];
static uint8_t s_handle_count;
static moy_ble_hid_event_t s_queue[MOY_BLE_HID_QUEUE_LEN];
static uint8_t s_head;
static uint8_t s_count;
static uint8_t s_max_depth;
static uint32_t s_received;
static uint32_t s_dropped;

static bool moy_ble_hid_matches(uint16_t conn, uint16_t handle) {
    if (!s_enabled || conn != s_conn) {
        return false;
    }
    for (uint8_t i = 0; i < s_handle_count; ++i) {
        if (s_handles[i] == handle) {
            return true;
        }
    }
    return false;
}

// Called on the NimBLE host task by the small P4 modbluetooth patch.  No
// MicroPython API, allocation, logging, or GIL is used here.
bool moy_ble_hid_queue_on_notify(uint16_t conn, uint16_t handle,
    const uint8_t **fragments, uint16_t *fragment_lens, size_t num) {

    size_t total = 0;
    for (size_t i = 0; i < num; ++i) {
        total += fragment_lens[i];
    }
    // Let the regular Python IRQ see an unexpected large/report-map payload;
    // the fast path is deliberately only for small keyboard input reports.
    if (total == 0 || total > MOY_BLE_HID_REPORT_MAX) {
        return false;
    }

    portENTER_CRITICAL(&s_lock);
    if (!moy_ble_hid_matches(conn, handle)) {
        portEXIT_CRITICAL(&s_lock);
        return false;
    }
    if (s_count == MOY_BLE_HID_QUEUE_LEN) {
        // Preserve the newest level state if the frame loop is ever delayed for
        // more than a whole queue.  The drop counter makes this visible.
        s_head = (uint8_t)((s_head + 1) % MOY_BLE_HID_QUEUE_LEN);
        --s_count;
        ++s_dropped;
    }
    uint8_t tail = (uint8_t)((s_head + s_count) % MOY_BLE_HID_QUEUE_LEN);
    moy_ble_hid_event_t *event = &s_queue[tail];
    event->handle = handle;
    event->len = (uint8_t)total;
    size_t copied = 0;
    for (size_t i = 0; i < num; ++i) {
        memcpy(event->data + copied, fragments[i], fragment_lens[i]);
        copied += fragment_lens[i];
    }
    event->arrived_us = esp_timer_get_time();
    ++s_count;
    ++s_received;
    if (s_count > s_max_depth) {
        s_max_depth = s_count;
    }
    portEXIT_CRITICAL(&s_lock);
    return true;
}

static mp_obj_t moy_ble_hid_configure(mp_obj_t conn_in, mp_obj_t handles_in) {
    uint16_t conn = (uint16_t)mp_obj_get_int(conn_in);
    size_t count;
    mp_obj_t *items;
    mp_obj_get_array(handles_in, &count, &items);
    if (count == 0 || count > MOY_BLE_HID_HANDLES_MAX) {
        mp_raise_ValueError(MP_ERROR_TEXT("BLE HID handle count"));
    }
    uint16_t handles[MOY_BLE_HID_HANDLES_MAX];
    for (size_t i = 0; i < count; ++i) {
        handles[i] = (uint16_t)mp_obj_get_int(items[i]);
    }

    portENTER_CRITICAL(&s_lock);
    s_enabled = false;
    s_conn = conn;
    s_handle_count = (uint8_t)count;
    memcpy(s_handles, handles, count * sizeof(uint16_t));
    s_head = 0;
    s_count = 0;
    s_max_depth = 0;
    s_received = 0;
    s_dropped = 0;
    s_enabled = true;
    portEXIT_CRITICAL(&s_lock);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_2(moy_ble_hid_configure_obj, moy_ble_hid_configure);

static mp_obj_t moy_ble_hid_disable(void) {
    portENTER_CRITICAL(&s_lock);
    s_enabled = false;
    s_handle_count = 0;
    s_head = 0;
    s_count = 0;
    portEXIT_CRITICAL(&s_lock);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ble_hid_disable_obj, moy_ble_hid_disable);

// Return (value_handle, report_bytes, queue_age_us), or None when empty.
static mp_obj_t moy_ble_hid_read(void) {
    moy_ble_hid_event_t event;
    bool have = false;
    portENTER_CRITICAL(&s_lock);
    if (s_count != 0) {
        event = s_queue[s_head];
        s_head = (uint8_t)((s_head + 1) % MOY_BLE_HID_QUEUE_LEN);
        --s_count;
        have = true;
    }
    portEXIT_CRITICAL(&s_lock);
    if (!have) {
        return mp_const_none;
    }
    int64_t age_us = esp_timer_get_time() - event.arrived_us;
    mp_obj_t tuple[3] = {
        MP_OBJ_NEW_SMALL_INT(event.handle),
        mp_obj_new_bytes(event.data, event.len),
        mp_obj_new_int_from_ll(age_us),
    };
    return mp_obj_new_tuple(3, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ble_hid_read_obj, moy_ble_hid_read);

// Return (received, dropped, queued, max_depth, enabled).
static mp_obj_t moy_ble_hid_stats(void) {
    uint32_t received;
    uint32_t dropped;
    uint8_t queued;
    uint8_t max_depth;
    bool enabled;
    portENTER_CRITICAL(&s_lock);
    received = s_received;
    dropped = s_dropped;
    queued = s_count;
    max_depth = s_max_depth;
    enabled = s_enabled;
    portEXIT_CRITICAL(&s_lock);
    mp_obj_t tuple[5] = {
        mp_obj_new_int_from_uint(received),
        mp_obj_new_int_from_uint(dropped),
        MP_OBJ_NEW_SMALL_INT(queued),
        MP_OBJ_NEW_SMALL_INT(max_depth),
        mp_obj_new_bool(enabled),
    };
    return mp_obj_new_tuple(5, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ble_hid_stats_obj, moy_ble_hid_stats);

static const mp_rom_map_elem_t moy_ble_hid_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ble_hid) },
    { MP_ROM_QSTR(MP_QSTR_configure), MP_ROM_PTR(&moy_ble_hid_configure_obj) },
    { MP_ROM_QSTR(MP_QSTR_disable), MP_ROM_PTR(&moy_ble_hid_disable_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&moy_ble_hid_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&moy_ble_hid_stats_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ble_hid_globals, moy_ble_hid_globals_table);

const mp_obj_module_t moy_ble_hid_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ble_hid_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ble_hid, moy_ble_hid_module);
