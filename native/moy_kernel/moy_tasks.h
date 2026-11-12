// moy_tasks: every task the native tier starts, in one table -- its name,
// stack, priority, core and where its stack lives -- and the one call that
// starts one (moy_tasks.c). The threading model, what each may touch and
// through which lock, is native/moy_kernel/README.md's.
//
// THE TABLE (MOY_TASKS, board only): X(id, name, stack bytes, priority, core,
// stack in PSRAM). A value the table leaves to the spawner is MOY_TASK_GIVEN
// (moy_task_spawn_as passes it: a name, a stack, a priority or a core);
// MOY_TASK_CALLER takes the spawning task's priority or core. A row whose
// stack the spawner hands in whole is moy_task_spawn_static's.
//
// Outside the table: MicroPython's own mp_task (moy_kernel.c's pinned copy of
// the port's), the panel flush's feeder (native/moy_flush, whose harness builds
// it with no board) and the P4 silicon tier's (native/p4).

#ifndef MOY_TASKS_H
#define MOY_TASKS_H

#include "moy_platform.h"

#if defined(MOY_ON_BOARD) && __has_include("py/mpconfig.h")
#include "py/mpconfig.h"        // the board's own values (mpconfigboard.h)
#endif

// -- the sizes and priorities the modules share with the table ----------------------

#define MOY_LOOP_TASK_STACK 4096
#ifndef MOY_INPUT_TASK_STACK
#define MOY_INPUT_TASK_STACK 3072          // a board names its own (mpconfigboard.h)
#endif
#define MOY_MEDIA_STACK (20u * 1024u)       // a frame's decode takes 16.7 KB of it
#define MOY_MEDIA_PRIO 3
#define MOYCORE_SVC_STACK 12288u
#define MOYCORE_SVC_PRIO 2
#define MOY_SH_RUN_STACK (24u * 1024u)      // a shell runner's (the SSH face's task's)
#define MOY_PAKE_STACK 8192
#ifndef MOY_AUDIO_CORE
#define MOY_AUDIO_CORE 1                    // the VM's, unless the board names one
#endif

#ifdef MOY_ON_BOARD
#include "esp_task.h"
#include "sdkconfig.h"

#define MOY_TASK_GIVEN (-2)
#define MOY_TASK_CALLER (-1)

// The audio feeder: near the top, but under the esp_timer task where the two
// share a core, since at its priority a block's render held every timer
// callback for its length.
#if (MOY_AUDIO_CORE == 0 && defined(CONFIG_ESP_TIMER_TASK_AFFINITY_CPU0)) \
    || (MOY_AUDIO_CORE == 1 && defined(CONFIG_ESP_TIMER_TASK_AFFINITY_CPU1))
#define MOY_AUDIO_PRIO (ESP_TASK_TIMER_PRIO - 1)
#else
#define MOY_AUDIO_PRIO (configMAX_PRIORITIES - 3)
#endif

// A Lua service: core 0 on the P4s; core 1 on the S3s, the VM's (core 0 has
// the flush feed and fold, input and the radios), with its stack in PSRAM so
// a service holds no internal SRAM past its control block (the shares'
// internal cap, moy_play.h). Its task never touches flash itself: its file
// rows hop to the VM's task.
#if CONFIG_IDF_TARGET_ESP32P4
#define MOY_TASK_SVC_CORE 0
#define MOY_TASK_SVC_PSRAM 0
#else
#define MOY_TASK_SVC_CORE 1
#define MOY_TASK_SVC_PSRAM 1
#endif

#define MOY_TASKS(X) \
    X(LOOP, "moy_loop", MOY_LOOP_TASK_STACK, ESP_TASK_PRIO_MIN + 1, MOY_TASK_CALLER, 0) \
    X(INPUT, "moy_input", MOY_INPUT_TASK_STACK, ESP_TASK_PRIO_MIN + 2, 0, 0) \
    X(AUDIO, "moy_audio", MOY_TASK_GIVEN, MOY_AUDIO_PRIO, MOY_AUDIO_CORE, 0) \
    X(MIC, "moy_mic", 3072, 6, 0, 0) \
    X(MIDI, "midi", 2560, 12, 0, 0) \
    X(MEDIA, "moy_media", MOY_MEDIA_STACK, MOY_MEDIA_PRIO, 0, 0) \
    X(FETCH, "moy_fetch", 6144, 5, 0, 0) \
    X(LORA, "moy_lora", 4096, 10, 0, 0) \
    X(USBDISK, "usbdisk", 4096, 5, 0, 0) \
    X(SVC, "moy_svc", MOYCORE_SVC_STACK, MOYCORE_SVC_PRIO, MOY_TASK_SVC_CORE, MOY_TASK_SVC_PSRAM) \
    X(SH, "moy_sh", MOY_SH_RUN_STACK, MOY_TASK_CALLER, MOY_TASK_CALLER, 1) \
    X(TTY, NULL, MOY_TASK_GIVEN, 1, 0, 1) \
    X(PAKE, "moy_pake", MOY_PAKE_STACK, 1, 0, 1) \
    X(ELF, "moy_elf", MOY_TASK_GIVEN, MOY_TASK_GIVEN, MOY_TASK_GIVEN, 1)

typedef enum {
    #define MOY_TASK_ID(id, name, stack, prio, core, psram) MOY_TASK_##id,
    MOY_TASKS(MOY_TASK_ID)
    #undef MOY_TASK_ID
    MOY_TASK_COUNT
} moy_task_id_t;

typedef struct {
    const char *name;
    int32_t stack;
    int prio;
    int core;
    bool psram;
} moy_task_row_t;

const moy_task_row_t *moy_task_row(moy_task_id_t id);

// The row's task, its handle into *out (out may be NULL): whether it started.
bool moy_task_spawn(moy_task_id_t id, TaskFunction_t fn, void *arg, TaskHandle_t *out);
// The same with the values the row leaves to the spawner (MOY_TASK_GIVEN);
// each argument is read only where the row gives it.
bool moy_task_spawn_as(moy_task_id_t id, const char *name, uint32_t stack, int prio, int core,
                       TaskFunction_t fn, void *arg, TaskHandle_t *out);
// The row's task over the spawner's stack (the row's size) and control block.
TaskHandle_t moy_task_spawn_static(moy_task_id_t id, TaskFunction_t fn, void *arg,
                                   StackType_t *stack, StaticTask_t *tcb);
#endif

#endif // MOY_TASKS_H
