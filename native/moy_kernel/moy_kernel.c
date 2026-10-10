// moy_kernel: the kernel's entry, the VM service, the crash intake and the
// recovery floor (docs/kernel_spine_2026-10.md §6-§8, #224 sprint 2).
//
// THE ENTRY. micropython.cmake defines MICROPY_ESP_IDF_ENTRY, which renames the
// port's app_main, so this file's runs instead (the embed decision,
// docs/native_kernel_2026-09.md §4.4): the port's board startup, the crash
// intake, then the VM service task with mp_task's stack, priority and core.
// app_main returns, so the IDF main task's stack is freed as it always was.
//
// THE VM SERVICE is mp_task (ports/esp32/main.c at the pinned MPY_TAG), copied:
// the same prelude, init, boot scripts, REPL loop and soft-reset list, in the
// same order, with the changes marked MOY below. It decides the boot first
// (moy_boot_decide), it lands on the recovery floor when the first heap area is
// not there, when the console's boot ends before boot_ok() it records the
// failure and restarts into the floor instead of falling to the REPL, and its
// pin sweep removes only the ISRs a Python handler holds, so the kernel's own
// (input's trackball and touch gate) outlive a soft reset, and each VM start
// clears the VM objects the kernel's modules cached in root pointers, which
// the last heap held. After mp_deinit a VM STOP (moy_kernel_vm_down,
// docs/kernel_cartpath_2026-10.md section 5) frees the first area and runs the
// kernel's frame on this task with no VM before the next start. The copy's call list is pinned: mp_task_calls.txt is what it was reviewed
// against, and tools/mp_task_calls.py fails the build when the tag's differs.
//
// THE FLOOR always draws on a boot no VM has run in: a failure inside a running
// VM restarts the board with the floor armed in RTC memory, so the panel, its
// feeder and the bus are untouched when the floor brings them up. Every choice
// on it (RETRY, SAFE, REPL) is a restart with that choice armed, for the same
// reason. The board's panel module gives it four entry points
// (MOY_KERNEL_PANEL(init|fb|present|backlight)) -- a board with no panel names
// none and its floor is serial only -- the board names its input in
// mpconfigboard.h, and serial works on every board: `retry`, `safe`, `repl`,
// `state`, with `KERNEL recovery ...` printed every few seconds.
//
// THE CRASH RECORD (moy_crash.h): __wrap_esp_panic_handler fills it in RTC
// memory and calls the real handler; the intake copies it to NVS
// (namespace moy_kernel, key crash) before the VM starts and clears the RTC
// copy. A clean boot touches no NVS.
//
// A HANG IS A RECORD TOO (#160): the console's frame loop feeds the task
// watchdog once per frame (moy_kernel_feed), which subscribes the VM task on
// its first feed. CONFIG_ESP_TASK_WDT_PANIC is on and the timeout is the
// board's (its sdkconfig.board), so a frame that never ends panics the board
// through the same wrapper and the record says TWDT, what ran and why. The loop
// leaves it with moy_kernel_rest, so the REPL is never watched.

#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <sys/time.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "esp_attr.h"
#include "esp_app_desc.h"
#include "esp_cpu.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "multi_heap.h"
#include "esp_log.h"
#include "esp_memory_utils.h"
#include "esp_system.h"
#include "esp_task.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "nvs.h"
#include "driver/gpio.h"
#include "esp_private/panic_internal.h"
#include "esp_private/cache_utils.h"
#include "soc/soc_caps.h"

#if __XTENSA__
#include "xtensa_context.h"
#include "esp_cpu_utils.h"
#include "esp_debug_helpers.h"
#else
#include "riscv/rvruntime-frames.h"
#endif

#include "py/cstack.h"
#include "py/runtime.h"
#include "py/gc.h"
#include "py/mphal.h"
#include "py/mpthread.h"
#include "py/ringbuf.h"
#include "extmod/modmachine.h"
#include "shared/readline/readline.h"
#include "shared/runtime/pyexec.h"
#include "shared/timeutils/timeutils.h"
#include "shared/tinyusb/mp_usbd.h"
#include "mbedtls/platform_time.h"

#include "uart.h"
#include "usb.h"
#include "usb_serial_jtag.h"
#include "modesp32.h"
#include "modmachine.h"
#include "modnetwork.h"

#if MICROPY_BLUETOOTH_NIMBLE
#include "extmod/modbluetooth.h"
#endif

#if MICROPY_PY_ESPNOW
#include "modespnow.h"
#endif

#include "moy_boot.h"
#include "moy_crash.h"
#include "moy_kernel.h"
#include "moy_loop.h"
#include "moy_recovery.h"
#include "../moy_spine/moy_route.h"

// The kernel's own route tables (moy_route.h), where the image has the spine:
// the Zero, which routes nothing, has none.
extern __typeof__(moy_spine_kernel) moy_spine_kernel __attribute__((weak));
extern __typeof__(moy_returns_run) moy_returns_run __attribute__((weak));
extern __typeof__(moy_returns_caller) moy_returns_caller __attribute__((weak));
extern __typeof__(moy_leases_hold) moy_leases_hold __attribute__((weak));
extern __typeof__(moy_leases_release) moy_leases_release __attribute__((weak));
extern __typeof__(moy_leases_mask) moy_leases_mask __attribute__((weak));
// The run a stop was for, with no VM (native/moy_play's moy_play_stop.c).
void moy_play_stopped_run(void) __attribute__((weak));
// The app ABI's live grants (native/moy_app), where the image has it.
uint32_t moy_app_kernel_grants(void) __attribute__((weak));

void moy_loop_board_vm_start(void);
void moy_loop_board_vm_stop(void);
int moy_loop_board_run(void);
bool moy_loop_board_ended(int r);
void moy_loop_board_window(void);
void moy_loop_board_down(void);

#if __has_include("moy_fw_label.gen.h")
#include "moy_fw_label.gen.h"
#endif
#ifndef MOY_FW_LABEL
#define MOY_FW_LABEL "unlabelled"
#endif
#ifndef MOY_FW_BOARD
#define MOY_FW_BOARD "unknown"
#endif
#ifndef MOY_FW_CHANNEL
#define MOY_FW_CHANNEL "stable"
#endif

#if defined(MOY_NET_WIFI) && MOY_NET_WIFI
#include "moy_json.h"
#include "moy_net.h"
#include "moy_ota.h"
#endif

#if !SOC_RTC_MEM_SUPPORTED
#error "moy_kernel keeps its crash record in RTC memory, which this chip lacks"
#endif

#define MP_TASK_PRIORITY (ESP_TASK_PRIO_MIN + 1)        // main.c's

#ifdef MOY_KERNEL_PANEL
// The panel module's four entry points. A board with no panel (the headless
// Zero) names none, and its floor is serial only.
int MOY_KERNEL_PANEL(init)(void);
uint16_t *MOY_KERNEL_PANEL(fb)(void);
int MOY_KERNEL_PANEL(present)(void);
void MOY_KERNEL_PANEL(backlight)(int on);
#endif

// The port's, extern without a header (the second by
// patches/esp32_native_code_free.patch, whose header half is not on a P4).
int vprintf_null(const char *format, va_list ap);
void esp_native_code_free_all(void);

RTC_NOINIT_ATTR static moy_kstate_t s_kst;
RTC_NOINIT_ATTR static moy_crash_rec_t s_krec;

// The task watchdog's reason, in the record a TWDT panic leaves: DRAM, because
// the wrapper reads it with the flash cache possibly off.
static DRAM_ATTR char s_twdt_what[MOY_CRASH_WHAT_LEN] = "task watchdog";
extern bool g_twdt_isr;                     // esp_system's: the abort came from the TWDT
static bool s_wd_armed;
static uint32_t s_wd_last_ms, s_wd_max_gap_ms, s_wd_frames;

static uint32_t s_build;                    // the build the record names
static int s_mode = MOY_BOOT_START;
static volatile bool s_proven;
static char s_fail_what[MOY_CRASH_WHAT_LEN];
static moy_crash_rec_t *s_last;             // PSRAM, read once
static bool s_last_read, s_fresh;

// ---------------------------------------------------------------------------
// The record: the panic wrapper and the intake
// ---------------------------------------------------------------------------

static IRAM_ATTR void moy_kernel_copy_text(char *dst, size_t cap, const char *src) {
    bool readable = src != NULL
        && (esp_ptr_byte_accessible(src)
            || (esp_ptr_in_drom(src) && spi_flash_cache_enabled()));
    moy_crash_strcpy(dst, cap, readable ? src : NULL);
}

void __real_esp_panic_handler(panic_info_t *info);

static const DRAM_ATTR char s_abort_at[] = "abort() was called at ";

IRAM_ATTR void __wrap_esp_panic_handler(panic_info_t *info) {
    moy_crash_rec_t *r = &s_krec;
    bool cache = spi_flash_cache_enabled();
    for (size_t i = 0; i < sizeof(*r); i++) {
        ((volatile uint8_t *)r)[i] = 0;
    }
    if (g_panic_abort) {
        r->kind = g_twdt_isr ? MOY_CRASH_TWDT : MOY_CRASH_ABORT;
    } else {
        switch (info->exception) {
            case PANIC_EXCEPTION_DEBUG: r->kind = MOY_CRASH_DEBUG; break;
            case PANIC_EXCEPTION_IWDT:  r->kind = MOY_CRASH_IWDT; break;
            case PANIC_EXCEPTION_TWDT:  r->kind = MOY_CRASH_TWDT; break;
            case PANIC_EXCEPTION_ABORT: r->kind = MOY_CRASH_ABORT; break;
            default:                    r->kind = MOY_CRASH_FAULT; break;
        }
    }
    r->core = (uint8_t)info->core;
    r->pc = (uint32_t)info->addr;
    #if __XTENSA__
    const XtExcFrame *f = (const XtExcFrame *)info->frame;
    if (f != NULL) {
        r->pc = f->pc;
        r->cause = f->exccause;
        r->addr = f->excvaddr;
        esp_backtrace_frame_t bt = {
            .pc = f->pc, .sp = f->a1, .next_pc = f->a0, .exc_frame = (void *)f,
        };
        for (int i = 0; i < 4 && bt.next_pc != 0 && esp_stack_ptr_is_sane(bt.sp); i++) {
            if (!esp_backtrace_get_next_frame(&bt)) {
                break;
            }
            r->bt[i] = esp_cpu_process_stack_pc(bt.pc);
        }
    }
    #else
    const RvExcFrame *f = (const RvExcFrame *)info->frame;
    if (f != NULL) {
        r->pc = f->mepc;
        r->cause = f->mcause;
        r->addr = f->mtval;
        r->bt[0] = f->ra;
    }
    #endif
    if (s_kst.magic == MOY_KSTATE_MAGIC) {
        const char *id;
        r->boot = s_kst.boot;
        r->role = (uint8_t)moy_kstate_open_id(&s_kst, &id);
        moy_crash_strcpy(r->id, MOY_CRASH_ID_LEN, id);
    }
    r->build = s_build;
    if (cache) {
        r->uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
        TaskHandle_t t = xTaskGetCurrentTaskHandleForCore(info->core);
        moy_kernel_copy_text(r->task, MOY_CRASH_TASK_LEN, t ? pcTaskGetName(t) : NULL);
        const char *what = g_panic_abort ? g_panic_abort_details : info->reason;
        if (r->kind == MOY_CRASH_TWDT) {
            what = s_twdt_what;
        } else if (g_panic_abort && what != NULL && esp_ptr_byte_accessible(what)) {
            // "abort() was called at PC 0x... on core N": the kind says abort,
            // so the record keeps the PC.
            size_t i = 0;
            while (s_abort_at[i] != '\0' && what[i] == s_abort_at[i]) {
                i++;
            }
            if (s_abort_at[i] == '\0') {
                what += i;
            }
        }
        moy_kernel_copy_text(r->what, MOY_CRASH_WHAT_LEN, what);
    }
    moy_crash_seal(r);
    __real_esp_panic_handler(info);
}

// A VM that dies with the board up: the record the panic wrapper would have
// made, from the VM service's side, left in RTC memory for the next intake.
static void moy_kernel_record_vm(const char *what) {
    moy_crash_rec_t *r = &s_krec;
    const char *id;
    memset(r, 0, sizeof(*r));
    r->kind = MOY_CRASH_VM;
    r->core = (uint8_t)esp_cpu_get_core_id();
    r->boot = s_kst.boot;
    r->uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
    r->build = s_build;
    r->role = (uint8_t)moy_kstate_open_id(&s_kst, &id);
    moy_crash_strcpy(r->id, MOY_CRASH_ID_LEN, id);
    moy_crash_strcpy(r->task, MOY_CRASH_TASK_LEN, pcTaskGetName(NULL));
    moy_crash_strcpy(r->what, MOY_CRASH_WHAT_LEN, what);
    moy_crash_seal(r);
}

const char *moy_kernel_reset_name(int reason) {
    switch (reason) {
        case ESP_RST_POWERON: return "poweron";
        case ESP_RST_EXT: return "ext";
        case ESP_RST_SW: return "sw";
        case ESP_RST_PANIC: return "panic";
        case ESP_RST_INT_WDT: return "int_wdt";
        case ESP_RST_TASK_WDT: return "task_wdt";
        case ESP_RST_WDT: return "wdt";
        case ESP_RST_DEEPSLEEP: return "deepsleep";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_SDIO: return "sdio";
        case ESP_RST_USB: return "usb";
        case ESP_RST_JTAG: return "jtag";
        case ESP_RST_EFUSE: return "efuse";
        case ESP_RST_PWR_GLITCH: return "power_glitch";
        case ESP_RST_CPU_LOCKUP: return "cpu_lockup";
        default: return "unknown";
    }
}

// A reset that means something went wrong even when no record survived it.
static bool moy_kernel_unclean(esp_reset_reason_t rr) {
    switch (rr) {
        case ESP_RST_PANIC: case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT:
        case ESP_RST_WDT: case ESP_RST_BROWNOUT: case ESP_RST_PWR_GLITCH:
        case ESP_RST_CPU_LOCKUP:
            return true;
        default:
            return false;
    }
}

static moy_crash_rec_t *moy_kernel_rec_buf(void) {
    if (s_last == NULL) {
        s_last = heap_caps_malloc(sizeof(moy_crash_rec_t), MALLOC_CAP_SPIRAM);
    }
    return s_last;
}

static void moy_kernel_print_rec(const char *tag, const moy_crash_rec_t *r) {
    printf("KERNEL %s kind=%s task=%s %s=%s pc=%08x cause=%u addr=%08x bt=%08x,%08x"
           " reset=%s boot=%u up_ms=%u what=%s\n",
           tag, moy_crash_kind_name(r->kind), r->task, moy_crash_role_name(r->role),
           r->id[0] ? r->id : "-", (unsigned)r->pc, (unsigned)r->cause, (unsigned)r->addr,
           (unsigned)r->bt[0], (unsigned)r->bt[1], moy_kernel_reset_name(r->reset),
           (unsigned)r->boot, (unsigned)r->uptime_ms, r->what);
    fflush(stdout);
}

static void moy_kernel_intake(void) {
    const esp_app_desc_t *app = esp_app_get_description();
    s_build = (uint32_t)app->app_elf_sha256[0] << 24 | (uint32_t)app->app_elf_sha256[1] << 16
              | (uint32_t)app->app_elf_sha256[2] << 8 | app->app_elf_sha256[3];
    uint32_t prev_boot = s_kst.magic == MOY_KSTATE_MAGIC ? s_kst.boot : 0;
    moy_kstate_open(&s_kst);
    esp_reset_reason_t rr = esp_reset_reason();
    bool have = moy_crash_valid(&s_krec);
    if (!have && !moy_kernel_unclean(rr)) {
        memset(&s_krec, 0, sizeof(s_krec));
        return;
    }
    moy_crash_rec_t *r = moy_kernel_rec_buf();
    if (r == NULL) {
        return;
    }
    if (have) {
        memcpy(r, &s_krec, sizeof(*r));
    } else {
        memset(r, 0, sizeof(*r));
        r->kind = MOY_CRASH_RESET;
        r->boot = prev_boot;
        r->build = s_build;
        moy_crash_strcpy(r->what, MOY_CRASH_WHAT_LEN, moy_kernel_reset_name(rr));
    }
    memset(&s_krec, 0, sizeof(s_krec));
    r->reset = (uint8_t)rr;
    moy_crash_seal(r);
    s_last_read = true;
    s_fresh = true;
    nvs_handle_t h;
    if (nvs_open("moy_kernel", NVS_READWRITE, &h) == ESP_OK) {
        if (nvs_set_blob(h, "crash", r, sizeof(*r)) == ESP_OK) {
            nvs_commit(h);
        }
        nvs_close(h);
    }
    moy_kernel_print_rec("crash", r);
}

const moy_crash_rec_t *moy_kernel_last_crash(void) {
    if (!s_last_read) {
        s_last_read = true;
        moy_crash_rec_t *r = moy_kernel_rec_buf();
        nvs_handle_t h;
        size_t n = sizeof(*r);
        bool ok = false;
        if (r != NULL && nvs_open("moy_kernel", NVS_READONLY, &h) == ESP_OK) {
            ok = nvs_get_blob(h, "crash", r, &n) == ESP_OK && n == sizeof(*r)
                 && moy_crash_valid(r);
            nvs_close(h);
        }
        if (!ok && r != NULL) {
            r->magic = 0;
        }
    }
    return (s_last != NULL && moy_crash_valid(s_last)) ? s_last : NULL;
}

const moy_crash_rec_t *moy_kernel_take_crash(void) {
    if (!s_fresh) {
        return NULL;
    }
    s_fresh = false;
    return moy_kernel_last_crash();
}

// ---------------------------------------------------------------------------
// What the binding reaches
// ---------------------------------------------------------------------------

int moy_kernel_mode(void) {
    return s_mode;
}

void moy_kernel_boot_ok(void) {
    moy_kernel_stamp(MOY_STAMP_FRAME);
    s_proven = true;
    moy_boot_proven(&s_kst);
}

void moy_kernel_boot_failed(const char *what) {
    moy_crash_strcpy(s_fail_what, sizeof(s_fail_what), what);
}

void moy_kernel_arm(int role, const char *id) {
    moy_kstate_arm(&s_kst, role, id);
}

void moy_kernel_test_restart(int test) {
    s_kst.test = (uint8_t)test;
    printf("KERNEL test %d armed, restarting\n", test);
    fflush(stdout);
    esp_restart();
}

void moy_kernel_test_crash(bool abort_not_fault) {
    printf("KERNEL test crash (%s)\n", abort_not_fault ? "abort" : "fault");
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(50));
    if (abort_not_fault) {
        abort();
    }
    *(volatile uint32_t *)0 = 0xDEADu;
}

// ---------------------------------------------------------------------------
// The task watchdog (#160)
// ---------------------------------------------------------------------------

// One call per console frame. The first subscribes the VM task (it runs on it)
// and the record's reason; every call resets the watchdog and keeps the longest
// gap between two (the worst frame the console took), which the gate reads.
void moy_kernel_feed(void) {
    uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
    if (!s_wd_armed) {
        if (esp_task_wdt_add(NULL) != ESP_OK) {
            return;
        }
        snprintf(s_twdt_what, sizeof(s_twdt_what), "console hung: no frame in %us",
                 (unsigned)CONFIG_ESP_TASK_WDT_TIMEOUT_S);
        s_wd_armed = true;
        s_wd_max_gap_ms = 0;
        s_wd_frames = 0;
    } else if (now - s_wd_last_ms > s_wd_max_gap_ms) {
        s_wd_max_gap_ms = now - s_wd_last_ms;
    }
    s_wd_last_ms = now;
    s_wd_frames++;
    esp_task_wdt_reset();
}

// The console's loop ended (a quit, a Ctrl-C): nothing feeds the watchdog now.
void moy_kernel_rest(void) {
    if (s_wd_armed) {
        esp_task_wdt_delete(NULL);
        s_wd_armed = false;
    }
}

bool moy_kernel_watchdog(uint32_t *timeout_ms, uint32_t *max_gap_ms, uint32_t *frames, bool reset) {
    bool armed = s_wd_armed;
    *timeout_ms = (uint32_t)CONFIG_ESP_TASK_WDT_TIMEOUT_S * 1000u;
    *max_gap_ms = s_wd_max_gap_ms;
    *frames = s_wd_frames;
    if (reset) {
        s_wd_max_gap_ms = 0;
        s_wd_frames = 0;
    }
    return armed;
}

// The port's weak one prints and restarts; this one records first.
void nlr_jump_fail(void *val) {
    printf("NLR jump failed, val=%p\n", val);
    moy_kernel_record_vm("nlr_jump_fail");
    esp_restart();
}

// ---------------------------------------------------------------------------
// The recovery floor
// ---------------------------------------------------------------------------

#define K_POLL_MS        20
#define K_REPORT_MS      5000
#define K_BUTTON_HOLD_MS 1000

static void k_out(const char *s) {
    size_t n = strlen(s);
    #if MICROPY_HW_ESP_USB_SERIAL_JTAG
    usb_serial_jtag_tx_strn(s, n);
    #endif
    #if MICROPY_HW_ENABLE_UART_REPL
    uart_stdout_tx_strn(s, n);
    #endif
}

static void k_printf(const char *fmt, ...) {
    char b[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof(b), fmt, ap);
    va_end(ap);
    k_out(b);
}

// The console's RX ring, which nothing else reads while the VM is down. The
// ISR takes a packet only into free ring space, so the FIFO is polled again
// whenever the ring has room.
static int k_getc(void) {
    int c = ringbuf_get(&stdin_ringbuf);
    #if MICROPY_HW_ESP_USB_SERIAL_JTAG
    if (c < 0) {
        usb_serial_jtag_poll_rx();
        c = ringbuf_get(&stdin_ringbuf);
    }
    #endif
    return c;
}

#if defined(MOY_KERNEL_TOUCH_AXS15231)
#include "driver/i2c.h"
// The AXS15231's touch, the same read as input's driver
// (native/moy_input/moy_touchdev.c), on the
// port's legacy I2C driver (the one this MicroPython links; the new driver
// beside it aborts the boot). The VM never runs in a floor's boot, so the
// floor owns I2C0.
static bool s_touch_up;
static void k_touch_init(void) {
    i2c_config_t c = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = MOY_KERNEL_TOUCH_SDA,
        .scl_io_num = MOY_KERNEL_TOUCH_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 400000,
    };
    s_touch_up = i2c_param_config(I2C_NUM_0, &c) == ESP_OK
                 && i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0) == ESP_OK;
}

// A press as a logical (x, y); returns whether a finger is down.
static bool k_touch(int *x, int *y) {
    static const uint8_t cmd[11] = {
        0xb5, 0xab, 0xa5, 0x5a, 0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00,
    };
    uint8_t d[8];
    if (!s_touch_up
            || i2c_master_write_to_device(I2C_NUM_0, MOY_KERNEL_TOUCH_ADDR, cmd, sizeof(cmd),
                                          pdMS_TO_TICKS(20)) != ESP_OK
            || i2c_master_read_from_device(I2C_NUM_0, MOY_KERNEL_TOUCH_ADDR, d, sizeof(d),
                                           pdMS_TO_TICKS(20)) != ESP_OK
            || d[0] != 0 || d[1] == 0) {
        return false;
    }
    int rx = ((d[2] & 0x0F) << 8) | d[3];
    int ry = ((d[4] & 0x0F) << 8) | d[5];
    *x = (MOY_KERNEL_PANEL_W - 1) - ry;      // axs_touch's rot-0 map: SWAP_XY + FLIP_X
    *y = rx;
    return true;
}
#endif

typedef struct {
    moy_rgeom_t g;
    moy_rview_t v;
    uint16_t *fb;
    int why;
    uint32_t crc;
} k_floor_t;

static void k_draw(k_floor_t *k) {
    if (k->fb == NULL) {
        return;
    }
    #ifdef MOY_KERNEL_PANEL
    moy_recovery_render(k->fb, &k->g, &k->v);
    k->crc = moy_crash_crc32(0, k->fb, (size_t)k->g.fb_w * k->g.fb_h * 2);
    int e = MOY_KERNEL_PANEL(present)();
    if (e != 0) {
        k_printf("KERNEL recovery present err=0x%x\r\n", e);
    }
    #endif
}

static void k_report(const k_floor_t *k) {
    k_printf("KERNEL recovery reason=%s sel=%s crc=%08x fw=%s\r\n",
             moy_crash_why_name(k->why), moy_recovery_choice_name(k->v.sel),
             (unsigned)(k->fb ? k->crc : 0), MOY_FW_LABEL);
    for (int i = 0; i < k->v.nlines; i++) {
        k_printf("KERNEL line %d %s\r\n", i, k->v.line[i]);
    }
    k_printf("KERNEL hint %s\r\n", k->v.hint);
}

static void k_state(const k_floor_t *k) {
    const moy_crash_rec_t *r = moy_kernel_last_crash();
    k_printf("STATE {\"screen\": \"recovery\", \"recovery\": \"%s\", \"sel\": \"%s\", "
             "\"crash\": ", moy_crash_why_name(k->why), moy_recovery_choice_name(k->v.sel));
    if (r == NULL) {
        k_out("null}\r\n");
    } else {
        k_printf("{\"kind\": \"%s\", \"task\": \"%s\", \"id\": \"%s\", \"role\": \"%s\", "
                 "\"pc\": %u, \"reset\": \"%s\"}}\r\n", moy_crash_kind_name(r->kind), r->task,
                 r->id, moy_crash_role_name(r->role), (unsigned)r->pc,
                 moy_kernel_reset_name(r->reset));
    }
}

#if defined(MOY_NET_WIFI) && MOY_NET_WIFI
// The floor's `update` word (docs/kernel_survival_2026-10.md section 6.3): the
// kernel's updater with no VM. `update` checks this build's channel for this
// board and says what it offers; `update install` installs it and restarts
// into it. A URL after either names another manifest -- typed at the serial,
// the owner's choice as a card's ota.json is, so a signature is checked when
// present and required only of the baked channel. The network is the one the
// WiFi driver keeps (moy_wifi_connect_kept).
#define K_UPDATE_WAIT_MS 20000
#define K_RELEASES "https://github.com/moybyte-org/moybyte/releases/download/"

static void k_update(const char *args) {
    while (*args == ' ') {
        args++;
    }
    int install = strncmp(args, "install", 7) == 0 && (args[7] == '\0' || args[7] == ' ');
    if (install) {
        args += 7;
        while (*args == ' ') {
            args++;
        }
    }
    char url[256];
    int baked = *args == '\0';
    if (baked) {
        snprintf(url, sizeof(url), K_RELEASES "%s/latest-%s.json",
                 strcmp(MOY_FW_CHANNEL, "unstable") == 0 ? "firmware-beta" : "firmware-latest",
                 MOY_FW_BOARD);
    } else {
        snprintf(url, sizeof(url), "%s", args);
    }
    moy_wifi_state_t st;
    moy_wifi_state(&st);
    if (!st.connected) {
        int e = moy_wifi_connect_kept();
        if (e != 0) {
            k_printf("KERNEL update wifi err=0x%x (no kept network)\r\n", e);
            return;
        }
        int64_t t0 = esp_timer_get_time();
        do {
            vTaskDelay(pdMS_TO_TICKS(250));
            moy_wifi_state(&st);
        } while (!st.connected && esp_timer_get_time() - t0 < K_UPDATE_WAIT_MS * 1000LL);
        if (!st.connected) {
            k_printf("KERNEL update wifi offline reason=%u\r\n", (unsigned)st.reason);
            return;
        }
    }
    k_printf("KERNEL update check %s\r\n", url);
    char *text = NULL;
    size_t n = 0;
    int rc = moy_ota_check(url, MOY_FW_BOARD, baked, &text, &n);
    if (rc == MOY_OTA_ABSENT) {
        k_out("KERNEL update none published\r\n");
        return;
    }
    if (rc != MOY_OTA_OK) {
        k_printf("KERNEL update refused: %s\r\n", moy_ota_error());
        return;
    }
    const char *o = moy_json_ws(text, text + n), *oe = moy_json_value(o, text + n, 0);
    const char *v, *ve;
    char murl[512] = "", sha[65] = "", chan[16] = "";
    int64_t ver = 0, size = 0;
    if (moy_json_get(o, oe, "url", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof(murl)) {
        murl[moy_json_str(v, ve, murl)] = '\0';
    }
    if (moy_json_get(o, oe, "sha256", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof(sha)) {
        sha[moy_json_str(v, ve, sha)] = '\0';
    }
    if (moy_json_get(o, oe, "channel", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof(chan)) {
        chan[moy_json_str(v, ve, chan)] = '\0';
    }
    if (moy_json_get(o, oe, "version", &v, &ve)) {
        moy_json_int(v, ve, &ver);
    }
    if (moy_json_get(o, oe, "size", &v, &ve)) {
        moy_json_int(v, ve, &size);
    }
    moy_net_free(text);
    k_printf("KERNEL update offers version=%ld channel=%s size=%ld running=%s\r\n",
             (long)ver, chan, (long)size, MOY_FW_LABEL);
    if (!install) {
        return;
    }
    if (moy_ota_dl_begin(murl, (uint32_t)size, sha, MOY_OTA_SINK_SLOT) != 0) {
        k_printf("KERNEL update refused: %s\r\n", moy_ota_error());
        return;
    }
    uint32_t shown = 0;
    int more;
    while ((more = moy_ota_dl_step(0)) > 0) {
        moy_ota_state_t os;
        moy_ota_state(&os);
        if (os.dl_done - shown >= 262144u) {
            shown = os.dl_done;
            k_printf("KERNEL update %u/%u\r\n", (unsigned)os.dl_done, (unsigned)os.dl_total);
        }
    }
    char label[16];
    if (more < 0 || moy_ota_dl_finish() != 0 || moy_ota_activate(label, sizeof(label)) != 0) {
        k_printf("KERNEL update failed: %s\r\n", moy_ota_error());
        return;
    }
    k_printf("KERNEL update installed into %s; restarting\r\n", label);
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
}
#endif

static void k_choose(int choice) {
    s_kst.next = (uint8_t)(MOY_BOOT_START + choice);
    k_printf("KERNEL recovery choice=%s\r\n", moy_recovery_choice_name(choice));
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
}

// The floor: never returns. Runs on the VM task's stack, with no VM.
static void moy_kernel_recovery(int why) {
    static k_floor_t k;
    const char *hint =
        #if defined(MOY_KERNEL_TOUCH_AXS15231)
        "TAP A CHOICE, OR SERIAL";
        #elif defined(MOY_KERNEL_BUTTON_GPIO)
        MOY_KERNEL_BUTTON_NAME ": NEXT  HOLD: PICK";
        #elif defined(MOY_KERNEL_IDLE_SAFE_MS)
        "SERIAL: retry / safe / repl";
        #else
        "SERIAL: retry / safe / repl";
        #endif
    k.why = why;
    #ifdef MOY_KERNEL_PANEL
    moy_rgeom_init(&k.g, MOY_KERNEL_PANEL_W, MOY_KERNEL_PANEL_H,
                   MOY_KERNEL_PANEL_ROT, MOY_KERNEL_PANEL_SWAP);
    #endif
    moy_recovery_view(&k.v, why, moy_kernel_last_crash(), MOY_FW_LABEL, hint);
    #if defined(MOY_KERNEL_IDLE_SAFE_MS)
    k.v.sel = 1;
    #endif
    #ifdef MOY_KERNEL_PANEL
    int e = MOY_KERNEL_PANEL(init)();
    if (e == 0) {
        k.fb = MOY_KERNEL_PANEL(fb)();
    } else {
        k_printf("KERNEL recovery panel err=0x%x\r\n", e);
    }
    k_draw(&k);
    MOY_KERNEL_PANEL(backlight)(1);
    #endif
    k_report(&k);

    #if defined(MOY_KERNEL_BUTTON_GPIO)
    gpio_config_t bc = {
        .pin_bit_mask = 1ULL << MOY_KERNEL_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&bc);
    int64_t pressed_at = -1;
    bool held = false;
    #endif
    #if defined(MOY_KERNEL_TOUCH_AXS15231)
    k_touch_init();
    int touch_choice = -1;
    #endif

    char line[208];
    size_t n = 0;
    int64_t t0 = esp_timer_get_time(), last_report = t0, last_input = t0;
    #if defined(MOY_KERNEL_IDLE_SAFE_MS)
    int shown_left = -1;
    #endif
    for (;;) {
        int64_t now = esp_timer_get_time();
        int c;
        while ((c = k_getc()) >= 0) {
            last_input = now;
            if (c == '\r' || c == '\n') {
                line[n] = '\0';
                if (n > 0) {
                    if (strcmp(line, "retry") == 0) {
                        k_choose(0);
                    } else if (strcmp(line, "safe") == 0) {
                        k_choose(1);
                    } else if (strcmp(line, "repl") == 0) {
                        k_choose(2);
                    } else if (strcmp(line, "state") == 0) {
                        k_state(&k);
                    #if defined(MOY_NET_WIFI) && MOY_NET_WIFI
                    } else if (strncmp(line, "update", 6) == 0
                               && (line[6] == '\0' || line[6] == ' ')) {
                        k_update(line + 6);
                        last_input = esp_timer_get_time();
                    #endif
                    } else {
                        k_report(&k);
                    }
                }
                n = 0;
            } else if (c >= 0x20 && c < 0x7F && n + 1 < sizeof(line)) {
                line[n++] = (char)c;
            }
        }
        #if defined(MOY_KERNEL_BUTTON_GPIO)
        bool down = gpio_get_level(MOY_KERNEL_BUTTON_GPIO) == 0;
        if (down && pressed_at < 0) {
            pressed_at = now;
            held = false;
        } else if (down && !held && now - pressed_at >= K_BUTTON_HOLD_MS * 1000LL) {
            held = true;
            k_choose(k.v.sel);
        } else if (!down && pressed_at >= 0) {
            if (!held && now - pressed_at >= 30000) {   // a press, not a bounce
                k.v.sel = (k.v.sel + 1) % MOY_RV_CHOICES;
                k_draw(&k);
                k_report(&k);
            }
            pressed_at = -1;
            last_input = now;
        }
        #endif
        #if defined(MOY_KERNEL_TOUCH_AXS15231)
        int tx, ty;
        if (k_touch(&tx, &ty)) {
            int hit = moy_recovery_hit(&k.g, tx, ty);
            if (hit >= 0 && hit != k.v.sel) {
                k.v.sel = hit;
                k_draw(&k);
            }
            touch_choice = hit;
            last_input = now;
        } else if (touch_choice >= 0) {
            k_choose(touch_choice);
        }
        #endif
        #if defined(MOY_KERNEL_IDLE_SAFE_MS)
        int left = (int)((MOY_KERNEL_IDLE_SAFE_MS * 1000LL - (now - last_input) + 999999) / 1000000);
        if (left <= 0) {
            k_printf("KERNEL recovery idle %ds\r\n", MOY_KERNEL_IDLE_SAFE_MS / 1000);
            k_choose(1);
        }
        if (left != shown_left) {
            shown_left = left;
            snprintf(k.v.hint, sizeof(k.v.hint), "SERIAL ONLY: SAFE IN %ds", left);
            k_draw(&k);
        }
        #endif
        if (now - last_report >= K_REPORT_MS * 1000LL) {
            last_report = now;
            k_report(&k);
        }
        vTaskDelay(pdMS_TO_TICKS(K_POLL_MS));
    }
}

// The console's boot ended before it proved itself: record it and restart into
// the floor. Never returns.
static void moy_kernel_vm_failed(void) {
    moy_kernel_record_vm(s_fail_what[0] ? s_fail_what : "the console's boot ended");
    s_kst.next = MOY_BOOT_RECOVERY;
    s_kst.reason = MOY_WHY_VM_START;
    printf("KERNEL vm_start failed: %s\n", s_krec.what);
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
}

// ---------------------------------------------------------------------------
// The entry and the VM service: mp_task, copied (see the header)
// ---------------------------------------------------------------------------

// This boot's decision. RECOVERY never returns: the floor runs here, on the VM
// task's stack.
static moy_boot_decision_t moy_kernel_decide(void) {
    moy_boot_decision_t d = moy_boot_decide(&s_kst);
    if (d.action == MOY_BOOT_RECOVERY) {
        moy_kernel_recovery(d.reason);
    }
    s_mode = d.action;
    if (s_mode != MOY_BOOT_START) {
        printf("KERNEL start mode=%s\n", s_mode == MOY_BOOT_SAFE ? "safe" : "repl");
    }
    return d;
}

#if MICROPY_SSL_MBEDTLS
static time_t platform_mbedtls_time(time_t *timer) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + TIMEUTILS_SECONDS_1970_TO_2000;
}
#endif

// The glass's teardown (native/moy_glass, docs/native_kernel_2026-09.md §4.4's
// glass rows): before the sweep, nothing the kernel's present started still
// reads a buffer the VM is about to free; after it, the lifetimes the VM held
// are ended. Weak, so an image without the glass links.
__attribute__((weak)) void moy_glass_vm_stop(void) {
}

__attribute__((weak)) void moy_glass_vm_swept(void) {
}

// The cart path's teardown (native/moy_play, native/moycore): a run the
// console never closed -- a Ctrl-C that reached the REPL mid-run, a
// SystemExit -- ends with the VM, or the next VM's first run is refused as
// already open. Weak, so an image without the Player links.
__attribute__((weak)) void moy_play_vm_stop(void) {
}

// The Player's own row (native/moy_play), made before the VM's first area:
// `alloc` is the kernel's PSRAM allocator. Weak, so an image without the
// Player links.
__attribute__((weak)) void moy_play_reserve(void *(*alloc)(size_t n)) {
    (void)alloc;
}

// The compiled cart's audio stream ring (native/moy_audio). Weak, so an image
// without the audio module links.
__attribute__((weak)) void moy_aud_reserve(void) {
}

// The links' teardown (native/moy_net/moy_ota.h): the client's connections and
// an update still streaming are closed with the VM that opened them. Weak, so
// an image without moy_net links.
__attribute__((weak)) void moy_net_vm_stop(void) {
}

// The kernel's internal volume, where the image has it (native/moy_store).
// The kernel's PLAIN web-console screen (docs/kernel_survival_2026-10.md §13
// answer 8): while no VM runs and the web console serves, the address to open,
// drawn through the floor's raster into the panel's framebuffer 0. The themed
// screen is the console's while its VM runs. Answers whether it drew.
bool moy_kernel_plain_web(void) {
    #if defined(MOY_KERNEL_PANEL) && defined(MOY_NET_WIFI) && MOY_NET_WIFI
    moy_wc_state_t wc;
    moy_wc_state(&wc);
    if (wc.state != MOY_WC_SERVING) {
        return false;
    }
    uint16_t *fb = MOY_KERNEL_PANEL(fb)();
    if (fb == NULL) {
        return false;
    }
    moy_rgeom_t g;              // on the loop task's stack: no internal statics
    moy_rview_t v;
    char url[96];
    size_t n = moy_wc_url(url, sizeof(url), 1);
    url[n < sizeof(url) ? n : sizeof(url) - 1] = 0;
    moy_rgeom_init(&g, MOY_KERNEL_PANEL_W, MOY_KERNEL_PANEL_H,
                   MOY_KERNEL_PANEL_ROT, MOY_KERNEL_PANEL_SWAP);
    moy_web_screen_view(&v, url, NULL, MOY_FW_LABEL);
    moy_recovery_render_plain(fb, &g, "MOYBYTE WEB CONSOLE", &v);
    return MOY_KERNEL_PANEL(present)() == 0;
    #else
    return false;
    #endif
}

// First light (moy_boot.h): before the first VM, the panel up, the logo in
// framebuffer 0 and the glass lit. A board with no panel has none.
static bool s_lit;
static uint32_t s_lit_ms;

bool moy_boot_lit(void) {
    return s_lit;
}

uint32_t moy_kernel_lit_ms(void) {
    return s_lit ? s_lit_ms : 0;
}

static void moy_kernel_first_light(void) {
    #ifdef MOY_KERNEL_PANEL
    int e = MOY_KERNEL_PANEL(init)();
    uint16_t *fb = e == 0 ? MOY_KERNEL_PANEL(fb)() : NULL;
    if (fb == NULL) {
        k_printf("KERNEL first light: panel err=0x%x\r\n", e);
        return;
    }
    moy_rgeom_t g;
    moy_rgeom_init(&g, MOY_KERNEL_PANEL_W, MOY_KERNEL_PANEL_H,
                   MOY_KERNEL_PANEL_ROT, MOY_KERNEL_PANEL_SWAP);
    moy_boot_logo_render(fb, &g);
    e = MOY_KERNEL_PANEL(present)();
    if (e != 0) {
        k_printf("KERNEL first light: present err=0x%x\r\n", e);
        return;
    }
    MOY_KERNEL_PANEL(backlight)(1);
    s_lit = true;
    s_lit_ms = (uint32_t)(esp_timer_get_time() / 1000);
    k_printf("KERNEL first light at %u ms\r\n", (unsigned)s_lit_ms);
    #endif
}

extern bool moy_kvol_vm_mount(void) __attribute__((weak));
// moy_alloc's registry: the buffers the swept VM's views named.
extern void moy_alloc_vm_swept(void) __attribute__((weak));

// kstop N (docs/kernel_survival_2026-10.md §7.5): the VM service's soft reset,
// N times with the kernel's drivers alive, each one's heaps printed before the
// teardown and after it. Test-only: the dev channel's word, never a kid's.
static int s_kstop_n;
static int s_kstop_left;
static bool s_kstop_stop;           // `kstop N stop`: each cycle a real stop and start

// -- the VM stop (docs/kernel_cartpath_2026-10.md section 5) --------------------
//
// A stop is the soft reset's teardown and then more: after mp_deinit the root
// section is zeroed (no stale root marks the reused first area), the first
// heap area is freed, the stdin ring and the interrupt character are the
// kernel's, and this task -- not deleted, its watchdog subscription as it was
// -- drives the kernel's frame until the stop's run ends (moy_play_stopped_run).
// Then the first area is allocated again and the next VM starts where the
// soft reset's does, as a RETURN start: the console's tables the kernel holds
// (the back-stack, the return records, the leases) and the resume record are
// as the stopped VM left them.
static volatile int s_stop;
static int s_start = MOY_START_BOOT;

// The resume record (section 5.4): a fixed-size kernel struct in PSRAM, kept
// until the next reboot and never written to flash.
typedef struct {
    uint16_t n;
    char text[MOY_KERNEL_RESUME_MAX];
} resume_t;
static resume_t *s_resume;

void moy_kernel_stop(int why) {
    s_stop = why;
    moy_loop_end(MOY_LOOP_STOP);
}

int moy_kernel_stop_pending(void) {
    return s_stop;
}

int moy_kernel_start(void) {
    return s_start;
}

// The start's stamps (moy_kernel.h): internal RAM, a reboot's to clear.
static uint32_t s_stamps[MOY_STAMPS];

void moy_kernel_stamp(int part) {
    if (part < 0 || part >= MOY_STAMPS) {
        return;
    }
    uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
    if (part == MOY_STAMP_EXIT) {
        memset(s_stamps, 0, sizeof(s_stamps));
    } else if (part == MOY_STAMP_FRAME && s_stamps[MOY_STAMP_FRAME] != 0u) {
        return;
    }
    s_stamps[part] = now ? now : 1u;
}

const uint32_t *moy_kernel_stamps(void) {
    return s_stamps;
}

static void *kernel_psram(size_t n) {
    return heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

// The small-block pool (moy_kernel.h).
static uint8_t *s_kpool;
static multi_heap_handle_t s_kpool_heap;
static portMUX_TYPE s_kpool_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_kpool_misses;

void *moy_kpool_alloc(size_t n) {
    if (s_kpool_heap == NULL || n > MOY_KPOOL_MAX) {
        return NULL;
    }
    void *p = multi_heap_malloc(s_kpool_heap, n ? n : 1u);
    if (p == NULL) {
        s_kpool_misses++;
        return NULL;
    }
    memset(p, 0, n);
    return p;
}

bool moy_kpool_free(void *p) {
    if (s_kpool == NULL || (uint8_t *)p < s_kpool || (uint8_t *)p >= s_kpool + MOY_KPOOL_BYTES) {
        return false;
    }
    multi_heap_free(s_kpool_heap, p);
    return true;
}

void moy_kpool_stats(size_t *high, uint32_t *misses) {
    *high = 0;
    *misses = s_kpool_misses;
    if (s_kpool_heap != NULL) {
        multi_heap_info_t i;
        multi_heap_get_info(s_kpool_heap, &i);
        *high = MOY_KPOOL_BYTES - i.minimum_free_bytes;
    }
}

static void moy_kernel_reserve(void) {
    if (s_resume == NULL) {
        s_resume = kernel_psram(sizeof(resume_t));
    }
    if (s_kpool == NULL) {
        s_kpool = heap_caps_malloc(MOY_KPOOL_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        s_kpool_heap = s_kpool != NULL ? multi_heap_register(s_kpool, MOY_KPOOL_BYTES) : NULL;
        if (s_kpool_heap != NULL) {
            multi_heap_set_lock(s_kpool_heap, &s_kpool_mux);
        }
    }
    moy_play_reserve(kernel_psram);
    moy_aud_reserve();
}

void moy_kernel_resume_set(const char *text, size_t n) {
    if (s_resume == NULL) {
        return;
    }
    if (n > MOY_KERNEL_RESUME_MAX) {
        n = MOY_KERNEL_RESUME_MAX;
    }
    memcpy(s_resume->text, text, n);
    s_resume->n = (uint16_t)n;
}

size_t moy_kernel_resume(const char **text) {
    if (s_resume == NULL || s_resume->n == 0) {
        *text = NULL;
        return 0;
    }
    *text = s_resume->text;
    return s_resume->n;
}

// A line out through the kernel's serial path, with no VM's stream.
void moy_kernel_say(const char *line) {
    k_out(line);
    k_out("\r\n");
}

// kstop's mark: a route and a lease on the kernel's tables before the stop,
// read back after it (section 5.6's row).
static void moy_kernel_kstop_mark(void) {
    const moy_spine_kernel_t *k = moy_spine_kernel != NULL ? moy_spine_kernel(NULL) : NULL;
    if (k == NULL) {
        return;
    }
    uint32_t mask;
    moy_returns_run(k->returns, "kstop", 5);
    moy_leases_hold(k->leases, "dev", 3, &mask);
}

// What the mark reads after the stop, cleared: "route=kstop lease=dev" when
// both survived it, then the app ABI's live grants ("grants=N", "-" where the
// image has none), which a return start's re-registration keeps flat.
static void moy_kernel_kstop_readback(char *out, size_t n) {
    const moy_spine_kernel_t *k = moy_spine_kernel != NULL ? moy_spine_kernel(NULL) : NULL;
    if (k == NULL) {
        snprintf(out, n, "route=- lease=-");
        return;
    }
    const moy_kind_t *c = moy_returns_caller(k->returns);
    bool route = c != NULL && moy_kind_is(c, "kstop", 5);
    // "dev" is the lease table's seventh tag (moy_route.c).
    bool lease = (moy_leases_mask(k->leases) & (1u << 6)) != 0;
    if (moy_app_kernel_grants != NULL) {
        snprintf(out, n, "route=%s lease=%s grants=%u", route ? "kstop" : "lost",
                 lease ? "dev" : "lost", (unsigned)moy_app_kernel_grants());
    } else {
        snprintf(out, n, "route=%s lease=%s grants=-", route ? "kstop" : "lost",
                 lease ? "dev" : "lost");
    }
    uint32_t mask;
    moy_returns_run(k->returns, NULL, 0);
    moy_leases_release(k->leases, "dev", 3, &mask);
}

void moy_kernel_kstop(int n) {
    s_kstop_n = n > 0 ? n : 0;
    s_kstop_left = s_kstop_n > 0 ? s_kstop_n - 1 : 0;
    s_kstop_stop = false;
}

void moy_kernel_kstop_stop(int n) {
    moy_kernel_kstop(n);
    if (s_kstop_n > 0) {
        s_kstop_stop = true;
        moy_kernel_kstop_mark();
        s_stop = MOY_STOP_KSTOP;
    }
}

bool moy_kernel_kstop_next(void) {
    if (s_kstop_left <= 0) {
        return false;
    }
    s_kstop_left--;
    if (s_kstop_stop) {
        moy_kernel_kstop_mark();
        s_stop = MOY_STOP_KSTOP;
    }
    return true;
}

static void moy_kernel_heaps_line(const char *tag, int i, int n, const char *when,
                                  const char *extra);

void moy_kernel_heaps(const char *tag, const char *when, const char *extra) {
    moy_kernel_heaps_line(tag, 1, 1, when, extra);
}

static void moy_kernel_kstop_line(const char *when) {
    if (s_kstop_n <= 0) {
        return;
    }
    moy_kernel_heaps_line("KSTOP", s_kstop_n - s_kstop_left, s_kstop_n, when, "");
}

// The heaps, one line: `tag i/n when psram=FREE/LARGEST int=... dma=...` and
// `extra` after them.
// THE PSRAM WALK (`heapwalk`, a dev word): every used block of at least
// WALK_USED bytes (every used block at all, `all`) and every free one of at
// least WALK_FREE, by address, so what splits the free run is named by where
// it sits. Gathered under the heap's lock into a static table, printed after
// it; past WALK_MAX rows the rest are counted as small.
#define WALK_MAX 96
#define WALK_USED 2048u
#define WALK_FREE (128u * 1024u)
typedef struct {
    uint32_t n, skipped_used, skipped_bytes;
    struct { uintptr_t at; uint32_t size; bool used; } b[WALK_MAX];
} walk_t;
static walk_t *s_walk;
static bool s_walk_on;
static uint32_t s_walk_used = WALK_USED;

static bool walk_block(walker_heap_into_t heap, walker_block_info_t blk, void *user) {
    (void)heap;
    walk_t *w = user;
    if ((blk.used && blk.size < s_walk_used) || (!blk.used && blk.size < WALK_FREE)
        || w->n >= WALK_MAX) {
        if (blk.used) {
            w->skipped_used++;
            w->skipped_bytes += (uint32_t)blk.size;
        }
        return true;
    }
    w->b[w->n].at = (uintptr_t)blk.ptr;
    w->b[w->n].size = (uint32_t)blk.size;
    w->b[w->n].used = blk.used;
    w->n++;
    return true;
}

void moy_kernel_heapwalk(const char *tag) {
    if (s_walk == NULL) {
        s_walk = heap_caps_malloc(sizeof(walk_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (s_walk == NULL) {
            printf("HEAPWALK %s: no memory\n", tag);
            return;
        }
    }
    memset(s_walk, 0, sizeof(walk_t));
    heap_caps_walk(MALLOC_CAP_SPIRAM, walk_block, s_walk);
    for (uint32_t k = 0; k < s_walk->n; k++) {
        printf("HEAPWALK %s %s 0x%08x %u\n", tag, s_walk->b[k].used ? "used" : "free",
               (unsigned)s_walk->b[k].at, (unsigned)s_walk->b[k].size);
    }
    printf("HEAPWALK %s small used=%u bytes=%u\n", tag, (unsigned)s_walk->skipped_used,
           (unsigned)s_walk->skipped_bytes);
    fflush(stdout);
}

void moy_kernel_heapwalk_at_stop(bool on, bool all) {
    s_walk_on = on;
    s_walk_used = all ? 1u : WALK_USED;
}

static void moy_kernel_heaps_line(const char *tag, int i, int n, const char *when,
                                  const char *extra) {
    if (s_walk_on && (strcmp(when, "down") == 0 || strcmp(when, "ended") == 0)) {
        moy_kernel_heapwalk(tag);
    }
    // The kernel's own serial path, as `say`'s: a printf straight after the
    // VM's teardown was lost on the Guition S3's USB serial now and then,
    // and this line is the stop's reading.
    k_printf("%s %d/%d %s psram=%u/%u int=%u/%u/%u dma=%u/%u/%u%s%s\r\n",
           tag, i, n, when,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
           *extra ? " " : "", extra);
    fflush(stdout);
}

// A kernel module that caches a VM object in a root pointer forgets it at
// every VM start; one an image does not take resolves to no call.
void moy_input_vm_fresh(void) __attribute__((weak));

static void moy_kernel_vm_fresh(void) {
    if (moy_input_vm_fresh != NULL) {
        moy_input_vm_fresh();
    }
}

// The soft reset's pin sweep: the ISR of every pin a Python handler holds, and
// no other. The port's machine_pins_deinit removes every pin's, the kernel's
// included.
static void moy_kernel_pins_deinit(void) {
    for (int i = 0; i < GPIO_PIN_COUNT; i++) {
        if (MP_STATE_PORT(machine_pin_irq_handler)[i] != MP_OBJ_NULL && GPIO_IS_VALID_GPIO(i)) {
            gpio_isr_handler_remove(i);
        }
    }
}

// The VM is gone (after mp_deinit). A soft reset starts the next one as a
// BOOT start. A stop (s_stop) is the rest of section 5.2's step 4 and the
// window: the root section zeroed, the first area freed, the kernel's frame
// with no VM for the stop's run, then the first area again and a RETURN
// start. Never returns without a first area: one that will not come back is
// the recovery floor's, as at power-on.
// The audio sessions the VM opened (moy_aud.h's moy_aud_close_vm): weak, for
// an image without moy_audio.
int moy_aud_close_vm(void) __attribute__((weak));

static void moy_kernel_vm_down(void **heap) {
    // Whatever the VM opened in the mixer ends with it, before any walk of
    // what the stop gives back.
    if (moy_aud_close_vm != NULL) {
        moy_aud_close_vm();
    }
    int why = s_stop;
    if (why == MOY_STOP_NONE) {
        s_start = MOY_START_BOOT;
        return;
    }
    size_t lo = offsetof(mp_state_ctx_t, thread.dict_locals);
    size_t hi = offsetof(mp_state_ctx_t, vm.qstr_last_chunk);
    memset((uint8_t *)&mp_state_ctx + lo, 0, hi - lo);
    MP_PLAT_FREE_HEAP(*heap);
    *heap = NULL;
    #if MICROPY_KBD_EXCEPTION
    mp_hal_set_interrupt_char(-1);
    #endif
    moy_loop_board_down();
    // The GIL as a VM's task holds it: what the kernel's frame calls releases
    // and retakes it around its waits (the flush's drain, a cart session's
    // calls) as it does with a VM.
    MP_THREAD_GIL_ENTER();
    if (why == MOY_STOP_KSTOP) {
        char rb[48];
        moy_kernel_kstop_readback(rb, sizeof(rb));
        // A few of the kernel's frames with no VM: the input stage, the dev
        // channel and the tail run; the console's upcalls are refused.
        for (int i = 0; i < 8; i++) {
            moy_loop_step();
        }
        moy_kernel_heaps_line("KSTOP", s_kstop_n - s_kstop_left, s_kstop_n, "down", rb);
    } else if (moy_play_stopped_run != NULL) {
        moy_play_stopped_run();
    }
    MP_THREAD_GIL_EXIT();
    s_stop = MOY_STOP_NONE;
    *heap = MP_PLAT_ALLOC_HEAP(MICROPY_GC_INITIAL_HEAP_SIZE);
    if (*heap == NULL) {
        printf("mp_task_heap allocation failed!\n");
        moy_kernel_recovery(MOY_WHY_HEAP);
    }
    s_start = MOY_START_RETURN;
}

static void moy_vm_task(void *pvParameter) {
    volatile uint32_t sp = (uint32_t)esp_cpu_get_sp();
    #if MICROPY_PY_THREAD
    mp_thread_init(pxTaskGetStackStart(NULL), MICROPY_TASK_STACK_SIZE / sizeof(uintptr_t));
    #endif
    #if MICROPY_HW_ESP_USB_SERIAL_JTAG
    usb_serial_jtag_init();
    #endif
    #if MICROPY_HW_ENABLE_USBDEV
    usb_phy_init();
    #endif
    #if MICROPY_HW_ENABLE_UART_REPL
    uart_stdout_init();
    #endif
    machine_init();

    #if MICROPY_SSL_MBEDTLS
    mbedtls_platform_set_time(platform_mbedtls_time);
    #endif

    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK) {
        ESP_LOGE("esp_init", "can't create event loop: 0x%x\n", err);
    }

    // MOY: the boot decision; the floor in place of a start.
    moy_boot_decision_t d = moy_kernel_decide();

    // MOY: what the kernel keeps for a run is made before the VM's first area,
    // below every area a VM will hold: a table made at a launch, with the VM's
    // areas in place, would sit inside the free run the VM's stop leaves and
    // split it (docs/kernel_cartpath_2026-10.md section 5.2).
    moy_kernel_reserve();

    // MOY: a missing first area lands on the floor instead of restarting.
    void *mp_task_heap = d.test == MOY_TEST_HEAP ? NULL
                         : MP_PLAT_ALLOC_HEAP(MICROPY_GC_INITIAL_HEAP_SIZE);
    if (mp_task_heap == NULL) {
        printf("mp_task_heap allocation failed!\n");
        moy_kernel_recovery(MOY_WHY_HEAP);
    }
    // MOY: the glass lit with the logo before any VM.
    moy_kernel_first_light();

soft_reset:
    // initialise the stack pointer for the main thread
    mp_cstack_init_with_top((void *)sp, MICROPY_TASK_STACK_SIZE);
    gc_init(mp_task_heap, mp_task_heap + MICROPY_GC_INITIAL_HEAP_SIZE);
    mp_init();
    moy_kernel_stamp(MOY_STAMP_VM);
    // MOY: the kernel modules' cached VM objects were the last heap's.
    moy_kernel_vm_fresh();
    // MOY: the frame's stages are the board's, and upcalls may register.
    moy_loop_board_vm_start();
    mp_obj_list_append(mp_sys_path, MP_OBJ_NEW_QSTR(MP_QSTR__slash_lib));
    readline_init0();

    // initialise peripherals
    machine_pins_init();
    #if MICROPY_PY_MACHINE_I2S
    machine_i2s_init0();
    #endif

    // run boot-up scripts. MOY: the internal flash volume is the kernel's
    // (native/moy_store/moy_kvfs.c), mounted at "/" here; the port's _boot.py,
    // which would mount a VfsLfs2 of its own over it, runs only where it is not.
    if (moy_kvol_vm_mount == NULL || !moy_kvol_vm_mount()) {
        pyexec_frozen_module("_boot.py", false);
    }
    int ret = pyexec_file_if_exists("boot.py");

    #if MICROPY_HW_ENABLE_USBDEV
    mp_usbd_init();
    #endif

    if (ret & PYEXEC_FORCED_EXIT) {
        goto soft_reset_exit;
    }
    // MOY: main.py is the console, which proves itself with boot_ok(); a
    // console boot that ends without it restarts into the floor. A REPL start
    // skips it.
    if (pyexec_mode_kind == PYEXEC_MODE_FRIENDLY_REPL && s_mode != MOY_BOOT_REPL) {
        s_proven = false;
        if (ret != 0 && d.test != MOY_TEST_VM_START) {
            int ret = pyexec_file_if_exists("main.py");
            // MOY: the console's boot registered its upcalls and returned;
            // the kernel's loop is this task's outermost frame from here, and
            // a SystemExit reaching an upcall is the soft reset main.py's own
            // would have been.
            if (!(ret & PYEXEC_FORCED_EXIT) && (moy_loop_registered() & 7u) == 7u
                && moy_loop_board_ended(moy_loop_board_run())) {
                ret |= PYEXEC_FORCED_EXIT;
            }
            if (ret & PYEXEC_FORCED_EXIT) {
                goto soft_reset_exit;
            }
        }
        d.test = MOY_TEST_NONE;
        if (!s_proven) {
            moy_kernel_vm_failed();
        }
    }

    for (;;) {
        if (pyexec_mode_kind == PYEXEC_MODE_RAW_REPL) {
            vprintf_like_t vprintf_log = esp_log_set_vprintf(vprintf_null);
            if (pyexec_raw_repl() != 0) {
                break;
            }
            esp_log_set_vprintf(vprintf_log);
        } else {
            if (pyexec_friendly_repl() != 0) {
                break;
            }
        }
    }

soft_reset_exit:

    // MOY: the last VM's end (a stop's run stamps its own end again).
    moy_kernel_stamp(MOY_STAMP_EXIT);
    // MOY: no upcall from here on; the loop holds none of this VM's objects.
    moy_loop_board_vm_stop();
    moy_kernel_rest();
    moy_kernel_kstop_line("before");
    moy_play_vm_stop();
    moy_glass_vm_stop();
    moy_net_vm_stop();
    // MOY: the glass is fenced; the kernel's loop task drives the frames
    // until the next VM is up.
    moy_loop_board_window();

    #if MICROPY_BLUETOOTH_NIMBLE
    mp_bluetooth_deinit();
    #endif

    #if MICROPY_PY_ESPNOW
    espnow_deinit(mp_const_none);
    MP_STATE_PORT(espnow_singleton) = NULL;
    #endif

    // Deinit uart before timers, as esp32 uart
    // depends on a timer instance
    #if MICROPY_PY_MACHINE_UART
    machine_uart_deinit_all();
    #endif
    machine_timer_deinit_all();

    #if MICROPY_PY_ESP32_PCNT
    esp32_pcnt_deinit_all();
    #endif

    #if MICROPY_PY_THREAD
    mp_thread_deinit();
    #endif

    #if MICROPY_HW_ENABLE_USBDEV
    mp_usbd_deinit();
    #endif

    gc_sweep_all();
    moy_glass_vm_swept();
    if (moy_alloc_vm_swept != NULL) {
        moy_alloc_vm_swept();
    }

    // Free any native code pointers that point to iRAM.
    esp_native_code_free_all();

    mp_hal_stdout_tx_str("MPY: soft reboot\r\n");

    // deinitialise peripherals
    #if MICROPY_PY_MACHINE_PWM
    machine_pwm_deinit_all();
    #endif
    // TODO: machine_rmt_deinit_all();
    // MOY: the sweep spares the kernel's ISRs.
    moy_kernel_pins_deinit();
    #if MICROPY_PY_MACHINE_I2C_TARGET
    mp_machine_i2c_target_deinit_all();
    #endif
    machine_deinit();

    #if MICROPY_PY_SOCKET_EVENTS
    socket_events_deinit();
    #endif

    mp_deinit();
    moy_kernel_kstop_line("after");
    // MOY: a stop runs the kernel with no VM before the next start.
    moy_kernel_vm_down(&mp_task_heap);
    if (s_kstop_left <= 0) {
        s_kstop_n = 0;
    }
    fflush(stdout);

    goto soft_reset;
}

void app_main(void) {
    // Hook for a board to run code at start up.
    // This defaults to initialising NVS and detecting the flash size.
    MICROPY_BOARD_STARTUP();

    // MOY: the crash intake, before anything can overwrite the evidence.
    moy_kernel_intake();

    // Create and transfer control to the MicroPython task.
    xTaskCreatePinnedToCore(moy_vm_task, "mp_task", MICROPY_TASK_STACK_SIZE / sizeof(StackType_t), NULL, MP_TASK_PRIORITY, &mp_main_task_handle, MP_TASK_COREID);
}
