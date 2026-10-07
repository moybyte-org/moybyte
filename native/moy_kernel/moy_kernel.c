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
// same order, with three changes marked MOY below. It decides the boot first
// (moy_boot_decide), it lands on the recovery floor when the first heap area is
// not there, and when the console's boot ends before boot_ok() it records the
// failure and restarts into the floor instead of falling to the REPL. The
// copy's call list is pinned: mp_task_calls.txt is what it was reviewed
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

#include "moy_crash.h"
#include "moy_kernel.h"
#include "moy_recovery.h"

#if __has_include("moy_fw_label.gen.h")
#include "moy_fw_label.gen.h"
#endif
#ifndef MOY_FW_LABEL
#define MOY_FW_LABEL "unlabelled"
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
// The AXS15231's touch, the C twin of device/axs_touch.py's read, on the
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

    char line[24];
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

// kstop N (docs/kernel_survival_2026-10.md §7.5): the VM service's soft reset,
// N times with the kernel's drivers alive, each one's heaps printed before the
// teardown and after it. Test-only: the dev channel's word, never a kid's.
static int s_kstop_n;
static int s_kstop_left;

void moy_kernel_kstop(int n) {
    s_kstop_n = n > 0 ? n : 0;
    s_kstop_left = s_kstop_n > 0 ? s_kstop_n - 1 : 0;
}

bool moy_kernel_kstop_next(void) {
    if (s_kstop_left <= 0) {
        return false;
    }
    s_kstop_left--;
    return true;
}

static void moy_kernel_kstop_line(const char *when) {
    if (s_kstop_n <= 0) {
        return;
    }
    printf("KSTOP %d/%d %s psram=%u/%u int=%u/%u/%u dma=%u/%u/%u\n",
           s_kstop_n - s_kstop_left, s_kstop_n, when,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
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

    // MOY: a missing first area lands on the floor instead of restarting.
    void *mp_task_heap = d.test == MOY_TEST_HEAP ? NULL
                         : MP_PLAT_ALLOC_HEAP(MICROPY_GC_INITIAL_HEAP_SIZE);
    if (mp_task_heap == NULL) {
        printf("mp_task_heap allocation failed!\n");
        moy_kernel_recovery(MOY_WHY_HEAP);
    }

soft_reset:
    // initialise the stack pointer for the main thread
    mp_cstack_init_with_top((void *)sp, MICROPY_TASK_STACK_SIZE);
    gc_init(mp_task_heap, mp_task_heap + MICROPY_GC_INITIAL_HEAP_SIZE);
    mp_init();
    mp_obj_list_append(mp_sys_path, MP_OBJ_NEW_QSTR(MP_QSTR__slash_lib));
    readline_init0();

    // initialise peripherals
    machine_pins_init();
    #if MICROPY_PY_MACHINE_I2S
    machine_i2s_init0();
    #endif

    // run boot-up scripts
    pyexec_frozen_module("_boot.py", false);
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

    moy_kernel_rest();
    moy_kernel_kstop_line("before");
    moy_glass_vm_stop();

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

    // Free any native code pointers that point to iRAM.
    esp_native_code_free_all();

    mp_hal_stdout_tx_str("MPY: soft reboot\r\n");

    // deinitialise peripherals
    #if MICROPY_PY_MACHINE_PWM
    machine_pwm_deinit_all();
    #endif
    // TODO: machine_rmt_deinit_all();
    machine_pins_deinit();
    #if MICROPY_PY_MACHINE_I2C_TARGET
    mp_machine_i2c_target_deinit_all();
    #endif
    machine_deinit();

    #if MICROPY_PY_SOCKET_EVENTS
    socket_events_deinit();
    #endif

    mp_deinit();
    moy_kernel_kstop_line("after");
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
