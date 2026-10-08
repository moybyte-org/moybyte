// moy_loop_board: the loop's stages on a VM -- the ops table a console
// board's VM service hands moy_loop.c (docs/kernel_survival_2026-10.md §7.1),
// and the same stages on the desktop MicroPython, which drives a staged
// console's frames through them (tests/test_frame_alloc.py). What only a
// board has -- the stdin ring, the OTA slot, the watchdog, PSRAM, the
// webhost's state -- is behind `__has_include("esp_ota_ops.h")`.
//
// Every stage is a C call into the module that owns it: the input stage is
// moy_input's (over the pointer and drivers the console bound with
// moy_input.loop_bind), present and the fence and the light are moy_glass's
// (over the compositor bound with moy_glass.loop_bind), the dev channel reads
// the stdin ring the console's RX ISR feeds, the webhost's state is moy_net's,
// the watchdog is the kernel's. A module an image does not take resolves to
// no call (weak), and its stage is absent.
//
// The loop runs as the OUTERMOST frame of the VM service task: the console's
// boot registered its upcalls and returned, and moy_kernel.c's copy of mp_task
// calls moy_loop_board_run() where the port enters its REPL. Python is called
// back on the same task; a pending exception that surfaces outside an upcall
// (a Ctrl-C reaching the pacing sleep) is caught here.

#include <stdio.h>
#include <string.h>

#include <stdlib.h>

#include "py/mphal.h"
#include "py/runtime.h"

#if __has_include("esp_ota_ops.h")
#define MOY_LOOP_ESP 1
#include "py/ringbuf.h"
#include "esp_heap_caps.h"
#include "esp_ota_ops.h"
#include "moy_kernel.h"
#else
#define MOY_LOOP_ESP 0
#endif

#include "moy_loop.h"

// The modules' loop entries (weak: an image without the module has none).
void moy_input_loop_inputs(uint32_t now, bool *click, bool *active) __attribute__((weak));
void moy_input_loop_pointer(uint32_t now, bool click, bool swallow) __attribute__((weak));
void moy_input_loop_point(int32_t x, int32_t y, bool down, bool edge) __attribute__((weak));
void moy_input_loop_tail(void) __attribute__((weak));
void moy_glass_loop_present(void) __attribute__((weak));
void moy_glass_loop_fence(void) __attribute__((weak));
void moy_glass_loop_idle(void) __attribute__((weak));
bool moy_glass_loop_light(int level) __attribute__((weak));
bool moy_glass_loop_overlap(uint32_t v[7], uint8_t *has) __attribute__((weak));
void moy_surface_kernel_bump(void) __attribute__((weak));
extern uint32_t moybyte_gc_pause[3] __attribute__((weak));

#if MOY_LOOP_ESP && defined(MOY_NET_WIFI) && MOY_NET_WIFI
#include "moy_net.h"
#endif

static uint32_t b_ms(void) {
    return (uint32_t)mp_hal_ticks_ms();
}

static uint32_t b_us(void) {
    return (uint32_t)mp_hal_ticks_us();
}

static void b_sleep(uint32_t ms) {
    mp_hal_delay_ms(ms);
}

static void b_inputs(uint32_t now, bool *click, bool *active) {
    moy_input_loop_inputs(now, click, active);
}

#if MOY_LOOP_ESP
#if MICROPY_HW_ESP_USB_SERIAL_JTAG
#include "usb_serial_jtag.h"
#endif

// The next byte off the stdin ring the console's RX ISR feeds. The USB
// Serial/JTAG ISR stops taking bytes while the ring is full and waits for a
// reader to pull the FIFO again (what the port's stdio poll does), so an
// empty ring re-polls it before it answers none.
static int b_getc(void) {
    int c = ringbuf_get(&stdin_ringbuf);
    #if MICROPY_HW_ESP_USB_SERIAL_JTAG
    if (c < 0) {
        usb_serial_jtag_poll_rx();
        c = ringbuf_get(&stdin_ringbuf);
    }
    #endif
    return c;
}
#endif

static void b_pointer(uint32_t now, bool click, bool swallow) {
    moy_input_loop_pointer(now, click, swallow);
}

static void b_present(void) {
    moy_glass_loop_present();
}

static bool b_light(int level) {
    return moy_glass_loop_light(level);
}

static void b_fence(void) {
    moy_glass_loop_fence();
}

static void b_repaint(void) {
    if (moy_surface_kernel_bump != NULL) {
        moy_surface_kernel_bump();
    }
}

static void b_tail(uint32_t now, bool drew) {
    (void)now;
    if (moy_input_loop_tail != NULL) {
        moy_input_loop_tail();
    }
    // The idle-band drain: the overlapped flush returns with bands queued,
    // and a frame the console's gate skipped never reaches the flush that
    // would drain them, so an idle frame leaves nothing in flight before
    // whatever comes next (the card, a sleep, a word). A banded compositor's
    // alone: a DSI one scans, and its fence would wait out the PPA.
    if (!drew && moy_glass_loop_idle != NULL) {
        moy_glass_loop_idle();
    }
}

#if MOY_LOOP_ESP
// The OTA confirm: the image proved itself (painted, and kept looping).
static void b_healthy(void) {
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
        const esp_partition_t *p = esp_ota_get_running_partition();
        printf("Moybyte OTA: marked app valid (slot %s)\n", p ? p->label : "?");
    }
}

static void b_feed(void) {
    moy_kernel_feed();
}
#endif

static void b_say(const char *line) {
    mp_hal_stdout_tx_str(line);
    mp_hal_stdout_tx_str("\r\n");
}

static bool b_overlap(moy_perf_overlap_t *out) {
    if (moy_glass_loop_overlap == NULL) {
        return false;
    }
    return moy_glass_loop_overlap(out->v, &out->has);
}

static bool b_gc(uint32_t out[3]) {
    if (&moybyte_gc_pause == NULL) {
        return false;
    }
    out[0] = moybyte_gc_pause[0];
    out[1] = moybyte_gc_pause[1];
    out[2] = moybyte_gc_pause[2];
    moybyte_gc_pause[2] = 0;            // the longest since the last read
    return true;
}

static void b_point(int32_t x, int32_t y, bool down, bool edge) {
    if (moy_input_loop_point != NULL) {
        moy_input_loop_point(x, y, down, edge);
    }
}

static void *b_alloc(size_t n) {
    #if MOY_LOOP_ESP
    void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
    return p != NULL ? p : heap_caps_malloc(n, MALLOC_CAP_8BIT);
    #else
    return malloc(n);
    #endif
}

// The webhost wants its Python half while it joins, serves or says goodbye.
static uint32_t b_services(void) {
    uint32_t bits = 0;
    #if MOY_LOOP_ESP && defined(MOY_NET_WIFI) && MOY_NET_WIFI
    moy_wc_state_t wc;
    moy_wc_state(&wc);
    moy_web_state_t web;
    moy_web_state(&web);
    if (wc.state != MOY_WC_OFF || web.listening) {
        bits |= MOY_SVC_WEB;
    }
    #endif
    return bits;
}

static moy_loop_ops_t s_ops;

// A fresh VM is up: the loop's stages are the board's again.
#if MOY_LOOP_ESP
static void window_end(void);
#endif

void moy_loop_board_vm_start(void) {
    #if MOY_LOOP_ESP
    window_end();
    #endif
    memset(&s_ops, 0, sizeof(s_ops));
    s_ops.ticks_ms = b_ms;
    s_ops.ticks_us = b_us;
    s_ops.sleep_ms = b_sleep;
    s_ops.inputs = moy_input_loop_inputs != NULL ? b_inputs : NULL;
    #if MOY_LOOP_ESP
    s_ops.getc = b_getc;
    s_ops.healthy = b_healthy;
    s_ops.feed = b_feed;
    #endif
    s_ops.pointer = moy_input_loop_pointer != NULL ? b_pointer : NULL;
    s_ops.present = moy_glass_loop_present != NULL ? b_present : NULL;
    s_ops.backlight = moy_glass_loop_light != NULL ? b_light : NULL;
    s_ops.fence = moy_glass_loop_fence != NULL ? b_fence : NULL;
    s_ops.repaint = b_repaint;
    s_ops.tail = b_tail;
    s_ops.say = b_say;
    s_ops.overlap = b_overlap;
    s_ops.gc_pauses = b_gc;
    s_ops.point = b_point;
    s_ops.alloc = b_alloc;
    s_ops.services = b_services;
    moy_loop_init(&s_ops, 60);
    moy_idle_t *d = moy_loop_idle();
    d->can_dim = false;
    moy_loop_set_vm(true);
}

// The VM is going: no upcall from here on, and none of its objects held.
void moy_loop_vm_clear(void);
void moy_glass_loop_clear(void) __attribute__((weak));

void moy_loop_board_vm_stop(void) {
    moy_loop_set_vm(false);
    moy_loop_vm_clear();
    if (moy_glass_loop_clear != NULL) {
        moy_glass_loop_clear();
    }
}

// The console registered its upcalls: run its frames until the dev channel
// asks for the REPL, a Ctrl-C arrives, or the upcalls go.
int moy_loop_board_run(void) {
    for (;;) {
        nlr_buf_t nlr;
        int r;
        if (nlr_push(&nlr) == 0) {
            r = moy_loop_run();
            nlr_pop();
        } else {
            mp_obj_t exc = MP_OBJ_FROM_PTR(nlr.ret_val);
            if (mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(mp_obj_get_type(exc)),
                                        MP_OBJ_FROM_PTR(&mp_type_KeyboardInterrupt))) {
                r = MOY_LOOP_INTERRUPT;
            } else {
                mp_printf(&mp_plat_print, "Moybyte loop error: ");
                mp_obj_print_exception(&mp_plat_print, exc);
                continue;
            }
        }
        #if MOY_LOOP_ESP
        moy_kernel_rest();      // a loop that ended is not a hang
        #endif
        return r;
    }
}

#if MOY_LOOP_ESP
// THE LOOP TASK (docs/kernel_survival_2026-10.md §7.1): while no VM runs --
// the soft reset's window today, sprint 4's stops tomorrow -- a small kernel
// task drives the kernel's own frame, pinned to the VM's core at the VM's
// priority. It is created when the window opens (after the teardown has
// fenced the glass) and deletes itself when the next VM is up, so its stack
// is internal SRAM only while no VM runs, in place of the VM task's. Its
// frame makes no upcall: the kernel's plain web-console screen while the web
// console serves, and nothing else yet.
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "esp_task.h"

#define MOY_LOOP_TASK_STACK 4096
#define MOY_LOOP_TASK_PERIOD_MS 16

bool moy_kernel_plain_web(void);

static volatile bool s_window;      // no VM runs: the task drives the frame
static volatile bool s_task_up;     // the task exists
static uint32_t s_window_frames;

static void loop_task(void *arg) {
    (void)arg;
    bool drawn = false;
    while (s_window) {
        if (!drawn) {
            drawn = moy_kernel_plain_web();
        }
        s_window_frames++;
        vTaskDelay(pdMS_TO_TICKS(MOY_LOOP_TASK_PERIOD_MS));
    }
    s_task_up = false;
    vTaskDelete(NULL);
}

void moy_loop_board_window(void) {
    if (s_task_up) {
        return;
    }
    s_window = true;
    s_task_up = true;
    if (xTaskCreatePinnedToCore(loop_task, "moy_loop", MOY_LOOP_TASK_STACK / sizeof(StackType_t),
                                NULL, ESP_TASK_PRIO_MIN + 1, NULL,
                                xPortGetCoreID()) != pdPASS) {
        s_task_up = false;
        s_window = false;
    }
}

// The next VM is up: the task ends before anything of the VM's draws.
static void window_end(void) {
    s_window = false;
    for (int i = 0; s_task_up && i < 100; i++) {
        vTaskDelay(1);
    }
}

uint32_t moy_loop_board_window_frames(void) {
    return s_window_frames;
}

// The loop ended: say why, and answer whether the VM resets now (a
// SystemExit reached an upcall: `kstop`'s cycle). A Ctrl-C is a developer
// asking for the REPL, which proves this start as the first frame would have.
bool moy_loop_board_ended(int r) {
    if (r == MOY_LOOP_EXIT) {
        return true;
    }
    if (r == MOY_LOOP_QUIT) {
        printf("Moybyte desktop: serial quit -> REPL\n");
    } else if (r == MOY_LOOP_INTERRUPT) {
        printf("Moybyte desktop interrupted -> REPL\n");
        moy_kernel_boot_ok();
    }
    return false;
}
#endif
