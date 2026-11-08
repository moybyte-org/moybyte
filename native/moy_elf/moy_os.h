// moy_os: a native module's process and the OS table's rows
// (docs/os_2026-11/tiers.md section 1; native/moy_elf/README.md).
//
// A MODULE is a loaded ELF (moy_elf.h's loader on a board, the host's dlopen):
// one entry the OS calls with the table and what to run (sdk/src/moyos_crt.c).
// The module imports nothing by name; its calls into the OS are the table's
// rows, by ordinal (moy_os_table.h, generated from os_table.json).
//
// A PROCESS is a module run: its pid, its row of the process table
// (moy_proc.h, tier ELF), its threads and its registrations. The OS creates
// every thread of it, a task with the process's caps (a priority, a core,
// a stack in PSRAM); the table exports no task, timer or ISR primitive.
// pthread_create is the one thread verb, and it makes another such task.
// Every registration (a timer, a mutex, an open folder, an event reader, a
// descriptor) is a handle in the process's record, swept at its end with
// the leases its pid holds (moy_lease_sweep), however it ends.
//
// A COMMAND is main(argc, argv) in a shell session (moy_sh.h's spawn), as a
// WASI command is its _start: moy_os_command runs it on its own task while
// the calling task waits, and every row that touches the session (its files,
// its streams, its folder, its terminal) runs on the calling task, which
// serves them while it waits. Its files are the folders its permissions grant
// and its streams in the background the OS log's (moy_sh_prog.h, WASI's
// rules). Ctrl-C ends it with 130 and its budget with 137, at the next row it
// calls; a board also ends one that calls none.
//
// A SERVICE is moy_start on its task, then moy_timer for each tick of a timer
// it started and moy_on for each event of the kinds it asked for, in order,
// then moy_stop: all OS-owned dispatch on its one task. A service has no
// session, so its file and stdin rows answer EACCES and its stdout and stderr
// are the OS log's lines.
//
// ENDING. A row's wait is sliced, so a thread inside a row notices the end
// within MOY_OS_SLICE_US and leaves; a thread in the module's own code is
// deleted (a board) where it stands. A host cannot delete a thread: one that
// will not reach a row in MOY_OS_REAP_US is left running and its process
// record with it (moy_os_stats' `leaked`).

#ifndef MOY_OS_H
#define MOY_OS_H

#include <stddef.h>
#include <stdint.h>

#include "moy_event.h"
#include "moy_os_table.h"
#include "moy_sh.h"

// What the OS asks a module's entry (sdk/src/moyos_crt.c).
enum { MOY_ELF_OP_BIND = 0, MOY_ELF_OP_MAIN, MOY_ELF_OP_START, MOY_ELF_OP_STOP,
       MOY_ELF_OP_TIMER, MOY_ELF_OP_ON };
// OP_BIND's answer: a bit per hook the module defines.
enum { MOY_ELF_H_MAIN = 1, MOY_ELF_H_START = 2, MOY_ELF_H_STOP = 4, MOY_ELF_H_TIMER = 8,
       MOY_ELF_H_ON = 16 };

typedef int (*moy_elf_entry_fn)(const moy_os_table_t *os, int op, int argc, char **argv,
                                const void *arg);

// A loaded module: its entry, and how its loader lets it go once its last
// thread has (NULL: nothing to do).
typedef struct {
    moy_elf_entry_fn entry;
    void *loaded;
    void (*unload)(void *loaded);
} moy_os_module_t;

// A process's caps: every thread of it is a task at `prio` on `core` (-1:
// either) with a stack of `stack` bytes.
typedef struct {
    int prio;
    int core;
    uint32_t stack;
} moy_os_caps_t;

#define MOY_OS_PROCS 4u             // processes at once
#define MOY_OS_THREADS 8u           // threads a process has at once, its first included
#define MOY_OS_HANDLES 32u          // registrations a process holds at once
#define MOY_OS_MAIL 16u             // a service's waiting calls
#define MOY_OS_SLICE_US 20000       // a row's wait between looks at the process's end
#define MOY_OS_REAP_US 2000000      // a stop's wait for the process's threads
#define MOY_OS_STOP_US 2000000      // moy_stop's budget

// moy_os_command's answers below 0.
#define MOY_OS_REFUSED (-3)         // it did not run: the reason in err

typedef struct moy_os_proc moy_os_proc_t;

// The caps a process gets on this console.
void moy_os_default_caps(moy_os_caps_t *caps);

// Command module `m` run as `argv` in session `s` (the session the calling
// task may touch), reaching the `roots` its permissions grant (NUL-separated,
// ending in an empty one, relative to the store; NULL: none), process `pid`,
// in the background when `bg_pid` is non-zero, ending at `budget_ms` (0: no
// limit). `id` is the cart's id for its process row. Its exit status, or
// MOY_OS_REFUSED with the reason in `err`. The module is unloaded (m->unload)
// before it answers, whatever it answers.
int moy_os_command(moy_sh_t *s, const moy_os_module_t *m, const moy_os_caps_t *caps,
                   int argc, const char *const *argv, const char *roots, uint32_t pid,
                   const char *id, uint32_t bg_pid, uint32_t budget_ms, char *err,
                   size_t errlen);

// Service module `m` started as process `pid` (`id` its cart's id): the
// process, its moy_start running on its own task, or NULL with the reason in
// `err` (the module unloaded).
moy_os_proc_t *moy_os_service(const moy_os_module_t *m, const moy_os_caps_t *caps,
                              uint32_t pid, const char *id, char *err, size_t errlen);

// On the console's task, once a frame: each service's event readers drained
// into its waiting calls, and a service that ended by itself (exit, its
// moy_start failing) swept. The pids swept go to `ended` (up to `n`), with
// their exit statuses in `codes`; answers how many.
uint32_t moy_os_poll(uint32_t *ended, int32_t *codes, uint32_t n);

// Stop service `pid`: moy_stop on its task within MOY_OS_STOP_US, then every
// thread ended and every registration swept. Its exit status (137 when
// moy_stop ran out of time), or -1 for no such service.
int moy_os_stop(uint32_t pid);

// What the OS knows of process `pid`: 0, or -1 for none.
typedef struct {
    uint32_t pid;
    uint32_t threads;           // live threads
    uint32_t handles;           // registrations held
    uint32_t calls;             // hook calls made (start, timer, on, stop)
    uint32_t timers;            // moy_timer calls made
    uint32_t events;            // moy_on calls made
    uint8_t service;            // 1 a service, 0 a command
    uint8_t ending;
} moy_os_info_t;
int moy_os_info(uint32_t pid, moy_os_info_t *out);

// Counts since boot.
typedef struct {
    uint32_t started;           // processes
    uint32_t swept;             // registrations swept at a process's end
    uint32_t killed;            // threads deleted where they stood
    uint32_t leaked;            // processes left running (a host thread that would not end)
} moy_os_stats_t;
void moy_os_stats(moy_os_stats_t *out);

// An event for every service whose reader takes `e`'s kind, as the poll
// would hand it (the host's tests, a source with no ring).
void moy_os_post_event(const moy_event_t *e);

#endif // MOY_OS_H
