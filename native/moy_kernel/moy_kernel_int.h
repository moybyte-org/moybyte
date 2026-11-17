// moy_kernel_int: what the kernel's entry (moy_kernel.c), its recovery floor
// (moy_recovery_floor.c) and its task watchdog (moy_wd.c) share: the headers
// and weak symbols they are built against, the boot state in RTC memory, the
// serial line and the screens' scratch. Nothing outside native/moy_kernel
// includes it.

#ifndef MOY_KERNEL_INT_H
#define MOY_KERNEL_INT_H

#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "esp_attr.h"
#include "esp_app_desc.h"
#include "esp_cpu.h"
#if CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH
#include "esp_core_dump.h"
#endif
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "moy_kmem.h"
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
#include "moy_evq.h"
#include "moy_vmq.h"
#include "moy_kernel.h"
#include "moy_lease.h"
#include "moy_log.h"
#include "moy_loop.h"
#include "moy_recovery.h"
#include "moy_str.h"
#include "../moy_spine/moy_route.h"

// The kernel's own route tables (moy_route.h), where the image has the spine:
// the Zero, which routes nothing, has none.
#pragma weak moy_spine_kernel
#pragma weak moy_returns_run
#pragma weak moy_returns_caller
// The run a stop was for, with no VM (native/moy_play's moy_play_stop.c).
#if __has_include("moy_play.h")
#include "moy_play.h"
#else
void moy_play_stopped_run(void);
#endif
#pragma weak moy_play_stopped_run
// The app ABI's live grants (native/moy_app), where the image has it, and
// its letting go of what the ending VM's heap held. Declared here as
// moy_app.h has them: its role names (moy_app_rows.h) and moy_crash.h's share
// MOY_ROLE_WALLPAPER, so the two headers do not meet in one file.
uint32_t moy_app_kernel_grants(void);
void moy_app_vm_stop(void);
#pragma weak moy_app_kernel_grants
#pragma weak moy_app_vm_stop
#if __has_include("moy_wasi.h") && __has_include("wasm_export.h")
#include "moy_wasi.h"
#else
void moy_wasm_services_end(void);
#endif
#pragma weak moy_wasm_services_end
#if __has_include("moy_os.h")
#include "moy_os.h"
#else
uint32_t moy_os_services_end(void);
#endif
#pragma weak moy_os_services_end
// The input table's readers, which the ending VM's processes held
// (native/moy_input), where the image has it.
#if __has_include("moy_input.h")
#include "moy_input.h"
#else
void moy_input_vm_stop(void);
#endif
#pragma weak moy_input_vm_stop

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
void MOY_KERNEL_PANEL(backlight)(int level);   // moy_light.h: 0 off, 255 full
#if !MOY_KERNEL_SCR
#error "a board with a panel draws its screens through moy_gfx's moy_scr.h"
#endif
#endif

// The port's, extern without a header (the second by
// patches/esp32_native_code_free.patch, whose header half is not on a P4).
int vprintf_null(const char *format, va_list ap);
void esp_native_code_free_all(void);

// The store's volumes, where the image has them: the card the floor brings up
// and the VM's mount (moy_kvol_vm_mount).
#if __has_include("moy_vol.h")
#include "moy_vol.h"
#else
int moy_store_card_up(const char **what);
#endif
#pragma weak moy_store_card_up

// The boot state and the crash record, in RTC memory (moy_kernel.c).
extern moy_kstate_t moy_k_kst;
extern moy_crash_rec_t moy_k_krec;
// What the console's boot was doing when it ended unproven (moy_kernel.c).
extern char moy_k_fail_what[MOY_CRASH_WHAT_LEN];
// The task watchdog's reason, in the record a TWDT panic leaves (moy_wd.c).
extern char moy_k_twdt_what[MOY_CRASH_WHAT_LEN];

// A VM failure recorded as a crash (moy_kernel.c): what it was doing.
void moy_kernel_record_vm(const char *what);

// The console's serial line, written with no VM (moy_kernel.c).
void moy_k_out(const char *s);
void moy_k_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

#ifdef MOY_KERNEL_PANEL
// The kernel's screens' scratch and geometry, and the splash palette the
// console keeps (moy_kernel.c).
void moy_k_scr_reserve(void);
void moy_k_geom(moy_rgeom_t *g);
const uint16_t *moy_k_splash_pal(void);
#endif

// The recovery floor (moy_recovery_floor.c): the floor itself, and the
// restart into it when the console's boot ends unproven. Neither returns.
void moy_kernel_recovery(int why);
void moy_kernel_vm_failed(void);

#endif // MOY_KERNEL_INT_H
