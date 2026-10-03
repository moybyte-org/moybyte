// Moybyte moy_serial: the console's serial line, from C.
//
// readinto(buf, start, end, idle_ms) -> the index reached. Fills buf[start:end]
// straight from the stdin ring the console's RX ISRs feed, and returns early
// only when no byte has arrived for idle_ms. It is the dev channel's `recv`
// reader: a Python loop that polls once per byte tops out near 50 KB/s on an
// S3 and 11 KB/s on a P4, far under either link, and a bulk sys.stdin read has
// no timeout, so a host that died mid-window would park the frame loop.
//
// Bytes are taken as they land. While they arrive the loop spins, because a
// window's bytes come microseconds apart; once the line has been quiet for
// SPIN_US it waits a tick at a time in the port's event hook, which the RX
// ISRs cut short. The interrupt char is the caller's business: both ISRs act
// on it before a byte reaches the ring, so `recv` turns it off around this.
//
// baud([rate]) -> the REPL UART's rate, set first when one is given, after the
// TX FIFO has drained so a line printed just before the switch leaves whole at
// the old rate. Present only where the build has a UART REPL.

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/ringbuf.h"

#ifdef ESP_IDF_VERSION
#include "usb_serial_jtag.h"
#if MICROPY_HW_ENABLE_UART_REPL
#include "driver/uart.h"
#include "hal/uart_hal.h"
#include "esp_timer.h"
#include "uart.h"
#endif
#define MOY_SERIAL_RING 1
#else
#define MOY_SERIAL_RING 0
#endif

// How long after the last byte the reader keeps spinning before it sleeps.
#define SPIN_US 2000

#if MOY_SERIAL_RING && defined(MOY_SERIAL_RING_BYTES)
#include "esp_heap_caps.h"
// A board that defines MOY_SERIAL_RING_BYTES gets a stdin ring that big, in
// PSRAM, from its first `readinto` on. The port's ring is 260 bytes, so a USB
// host whose next window is already on its way stalls at 260 bytes for as
// long as the store's write of the last one takes; a ring that holds the
// whole window lets the USB ISR take it during the write. Only a board whose
// RX ISR is never needed during a flash operation may define it: an ISR that
// runs with the cache off (the Waveshare P4's UART, whose ring is in TCM)
// must not write PSRAM.
static void moy_serial_grow(void) {
    static bool done = false;
    if (done) {
        return;
    }
    done = true;
    if (stdin_ringbuf.size >= MOY_SERIAL_RING_BYTES) {
        return;
    }
    uint8_t *ring = heap_caps_malloc(MOY_SERIAL_RING_BYTES, MALLOC_CAP_SPIRAM);
    if (ring == NULL) {
        return;
    }
    // The RX ISR is installed on this core, so masking it here is enough to
    // move the ring under it, bytes already in it included.
    portDISABLE_INTERRUPTS();
    uint16_t n = 0;
    int c;
    while ((c = ringbuf_get(&stdin_ringbuf)) >= 0) {
        ring[n++] = (uint8_t)c;
    }
    stdin_ringbuf.buf = ring;
    stdin_ringbuf.size = MOY_SERIAL_RING_BYTES;
    stdin_ringbuf.iget = 0;
    stdin_ringbuf.iput = n;
    portENABLE_INTERRUPTS();
}
#endif

static mp_obj_t moy_serial_readinto(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    mp_buffer_info_t bi;
    mp_get_buffer_raise(args[0], &bi, MP_BUFFER_WRITE);
    mp_int_t i = mp_obj_get_int(args[1]);
    mp_int_t end = mp_obj_get_int(args[2]);
    mp_int_t idle_ms = mp_obj_get_int(args[3]);
    if (i < 0 || end < i || (size_t)end > bi.len || idle_ms < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("moy_serial: bad range"));
    }
#if MOY_SERIAL_RING
    #ifdef MOY_SERIAL_RING_BYTES
    moy_serial_grow();
    #endif
    uint8_t *buf = (uint8_t *)bi.buf;
    mp_uint_t last_us = mp_hal_ticks_us();
    mp_uint_t last_ms = mp_hal_ticks_ms();
    while (i < end) {
        #if MICROPY_HW_ESP_USB_SERIAL_JTAG
        usb_serial_jtag_poll_rx();
        #endif
        mp_int_t was = i;
        int c;
        while (i < end && (c = ringbuf_get(&stdin_ringbuf)) >= 0) {
            buf[i++] = (uint8_t)c;
        }
        if (i != was) {
            last_us = mp_hal_ticks_us();
            last_ms = mp_hal_ticks_ms();
            continue;
        }
        if (i >= end) {
            break;
        }
        if ((mp_uint_t)(mp_hal_ticks_ms() - last_ms) >= (mp_uint_t)idle_ms) {
            break;
        }
        if ((mp_uint_t)(mp_hal_ticks_us() - last_us) >= SPIN_US) {
            MICROPY_EVENT_POLL_HOOK
        }
    }
    return MP_OBJ_NEW_SMALL_INT(i);
#else
    (void)bi;
    (void)end;
    (void)idle_ms;
    mp_raise_NotImplementedError(MP_ERROR_TEXT("moy_serial needs the esp32 port"));
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_serial_readinto_obj, 4, 4, moy_serial_readinto);

#if MOY_SERIAL_RING && MICROPY_HW_ENABLE_UART_REPL

#if CONFIG_IDF_TARGET_ESP32P4
// The P4's baud setter writes a shared clock-reset register and must be
// expanded inside an atomic-RCC scope, as ports/esp32/uart.c does.
static uint8_t __DECLARE_RCC_ATOMIC_ENV __attribute__ ((unused));
#endif

static mp_obj_t moy_serial_baud(size_t n_args, const mp_obj_t *args) {
    uart_hal_context_t hal = { .dev = UART_LL_GET_HW(MICROPY_HW_UART_REPL) };
    soc_module_clk_t sclk;
    uint32_t sclk_freq;
    uart_hal_get_sclk(&hal, &sclk);
    if (uart_get_sclk_freq(sclk, &sclk_freq) != ESP_OK) {
        mp_raise_OSError(MP_EIO);
    }
    if (n_args == 1) {
        mp_int_t rate = mp_obj_get_int(args[0]);
        if (rate < 1200 || rate > 5000000) {
            mp_raise_ValueError(MP_ERROR_TEXT("moy_serial: rate out of range"));
        }
        int64_t give_up = esp_timer_get_time() + 200000;
        while (!uart_hal_is_tx_idle(&hal) && esp_timer_get_time() < give_up) {
        }
        uart_hal_set_baudrate(&hal, (uint32_t)rate, sclk_freq);
    }
    uint32_t now;
    uart_hal_get_baudrate(&hal, &now, sclk_freq);
    return mp_obj_new_int_from_uint(now);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_serial_baud_obj, 0, 1, moy_serial_baud);
#endif

static const mp_rom_map_elem_t moy_serial_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_OBJ_NEW_QSTR(MP_QSTR_moy_serial) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&moy_serial_readinto_obj) },
    #if MOY_SERIAL_RING && MICROPY_HW_ENABLE_UART_REPL
    { MP_ROM_QSTR(MP_QSTR_baud), MP_ROM_PTR(&moy_serial_baud_obj) },
    #endif
};
static MP_DEFINE_CONST_DICT(moy_serial_globals, moy_serial_globals_table);

const mp_obj_module_t mp_module_moy_serial = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_serial_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_serial, mp_module_moy_serial);
