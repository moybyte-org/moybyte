// moy_os: a native module's process and the OS table's rows
// (docs/os_2026-11/tiers.md section 1; native/moy_elf/README.md).
//
// A MODULE is a loaded ELF (moy_elf.h's loader on a board, the host's dlopen):
// one entry the OS calls with the table and what to run (sdk/src/moyos_crt.c).
// The module imports nothing by name; its calls into the OS are the table's
// rows, by ordinal (moy_os_table.h, generated from native/os_calls.json).
//
// A PROCESS is a module run: its pid, its row of the process table
// (moy_proc.h, tier ELF), its threads and its registrations. The OS creates
// every thread of it, a task with the process's caps (a priority, a core,
// a stack in PSRAM); the table exports no task, timer or ISR primitive.
// pthread_create is the one thread verb, and it makes another such task.
// Every registration (a mutex, an open folder, a descriptor, a socket) is a
// handle in the process's record, swept at its end with
// the leases its pid holds (moy_lease_sweep), however it ends.
//
// A COMMAND is main(argc, argv) in a shell session (moy_sh.h's spawn), as a
// WASI command is its _start: it runs on its own task, and every row that
// touches the session (its files, its streams, its folder, its terminal)
// runs on the task that serves it -- moy_os_command's caller, which waits out
// the whole run, or the shell door's, which serves it a step at a time
// (moy_os_command_step) so the console's frame goes on between the steps. Its files are the folders its permissions grant
// and its streams in the background the OS log's (moy_sh_prog.h, WASI's
// rules). Ctrl-C ends it with 130 and its budget with 137, at the next row it
// calls; a board also ends one that calls none.
//
// A SERVICE is main(argc, argv) on its task, argv its cart's id: a loop that
// blocks in poll on its EVENT DESCRIPTOR, MOY_EVQ_FD, with its nearest
// deadline as the timeout, and reads each message it is woken for
// (docs/os_2026-11/processes.md section 1, the service model). The
// descriptor is the process's event queue (moy_evq.h): readable while an
// event is queued, each read one record (moy_evq_read), never blocking
// (EAGAIN), MOY_EVQ_BATCH reads a wake (the poll is the queue's step). It
// listens with moy_on and sends with moy_send, both the kernel's, under the
// queue's rule; a send BACKPRESSURE refuses is EAGAIN. Its stop makes the descriptor hang up
// (poll's POLLHUP, a read's end of file), and main returns within
// MOY_OS_STOP_US or the process is killed. A command whose process has a
// queue reads the same descriptor. Each wake, from the poll's return to its
// next poll of the descriptor, is charged to the one supervisor
// (moy_super.h); a suspended process's poll waits, unserved, until it is
// resumed. Native code is not aborted where it stands: a wake that never
// polls again is struck by the watchdog (moy_os_watch) and killed once it
// strikes out. A service has no session, so its file and stdin rows answer
// EACCES and its stdout and stderr are the OS log's lines; its app calls
// are hops on the VM's queue (moy_vmq.h), answered at the scheduler's poll.
//
// MEMORY. mmap's anonymous mappings (the SDK's malloc takes its arenas so)
// are PSRAM on a board, each in its process's record and freed at its end.
//
// SOCKETS (the `network` permission, MOY_OS_NET). The rows are BSD sockets
// over the console's stack: lwIP's on a board, the host's own on the host,
// each under its own headers' layout. A socket is a handle, its descriptor
// MOY_OS_SOCK_FD + the handle's slot (below the C library's FD_SETSIZE, so
// select takes it), read, written and closed by the file rows too, swept at
// the end. Underneath every socket is non-blocking: a blocking call waits in
// slices of MOY_OS_SLICE_US, so Ctrl-C and the budget end it there. The
// first socket a process opens holds the network for it, on the task that
// serves its session (the net hook, moy_os_set_net), and the process's end
// lets it go. A row that needs the link (connect, sendto, a name lookup)
// waits for it MOY_OS_LINK_US at most. A service has no session to hold the
// network from, and its socket rows answer EACCES.
//
// ENDING. A row's wait is sliced, so a thread inside a row notices the end
// within MOY_OS_SLICE_US and leaves; a thread in the module's own code is
// deleted (a board) where it stands. A host cannot delete a thread: one that
// will not reach a row in MOY_OS_REAP_US is left running and its process
// record with it (moy_os_stats' `leaked`).

#ifndef MOY_OS_H
#define MOY_OS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "moy_app_run.h"
#include "moy_os_table.h"
#include "moy_sh.h"

// What the OS asks a module's entry (sdk/src/moyos_crt.c). Ops 2 to 5 were
// a service's dispatched hooks, retired 2026-11-08, and are never reused.
enum { MOY_ELF_OP_BIND = 0, MOY_ELF_OP_MAIN };
// OP_BIND's answer: a bit per hook the module defines.
enum { MOY_ELF_H_MAIN = 1 };

typedef int (*moy_elf_entry_fn)(const moy_os_table_t *os, int op, int argc, char **argv,
                                const void *arg);

// A loaded module: its entry, and how its loader lets it go once its last
// thread has (NULL: nothing to do).
// A module the P4s run in U-mode (moy_umode.h) also carries its block (0
// elsewhere): [ulo, urx) RX from its gate on, [urx, uhi) RW, uvbase the address
// of its vaddr 0, its gate's table and return stub, and its constructors,
// which its first thread runs.
#define MOY_OS_INITS 16u
typedef struct {
    moy_elf_entry_fn entry;
    void *loaded;
    void (*unload)(void *loaded);
    uint32_t ulo, urx, uhi, uvbase, utable, uret;
    uint32_t uinit[MOY_OS_INITS];
    uint32_t uinits;
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
#define MOY_OS_SLICE_US 20000       // a row's wait between looks at the process's end
#define MOY_OS_REAP_US 2000000      // a stop's wait for the process's threads
#define MOY_OS_STOP_US 2000000      // moy_stop's budget
#define MOY_OS_LINK_US 20000000     // a socket row's wait for the network's link
#define MOY_OS_SOCK_FD 32           // a socket's descriptor: this + its handle's slot

// moy_os_command's answers below 0.
#define MOY_OS_REFUSED (-3)         // it did not run: the reason in err
#define MOY_OS_RUNNING (-100)       // moy_os_command_step: main goes on

typedef struct moy_os_proc moy_os_proc_t;

// The caps a process gets on this console.
void moy_os_default_caps(moy_os_caps_t *caps);

// Command module `m` run as `argv` in session `s` (the session the calling
// task may touch), reaching the `roots` its permissions grant (NUL-separated,
// ending in an empty one, relative to the store; NULL: none), process `pid`,
// in the background when `bg_pid` is non-zero, ending at `budget_ms` (0: no
// limit), with what `flags` grants (MOY_OS_NET: the socket rows). `id` is
// the cart's id for its process row. Its exit status, or
// MOY_OS_REFUSED with the reason in `err`. `run` is the process's app run
// (moy_app_run.h), the grant its app calls are answered for (NULL: none,
// each answering EACCES). The module is unloaded (m->unload) before it
// answers, whatever it answers.
#define MOY_OS_NET 1u
// A loader's flag, read before the module is handed here: the cart's consent
// admits a module with no signature (moy_ota_admit_module's `consented`,
// app_perms.admits_unsigned: its `full_access` granted).
#define MOY_OS_UNSIGNED 2u
int moy_os_command(moy_sh_t *s, const moy_os_module_t *m, const moy_os_caps_t *caps,
                   int argc, const char *const *argv, const char *roots, uint32_t pid,
                   const char *id, uint32_t bg_pid, uint32_t budget_ms, uint32_t flags,
                   const moy_app_run_t *run, char *err, size_t errlen);

// The same run in steps, so the task that serves it can go on between them
// (the shell door's step, runtime/shell/sh_door.py): start answers 0 with
// the process running, or MOY_OS_REFUSED as moy_os_command does; each step
// serves its session's calls for `max_us` at most and answers MOY_OS_RUNNING
// while main goes on, else its exit status, the process gone; stop ends it
// as Ctrl-C does (130). Every step and the stop run on the task that may
// touch the session.
int moy_os_command_start(moy_sh_t *s, const moy_os_module_t *m, const moy_os_caps_t *caps,
                         int argc, const char *const *argv, const char *roots, uint32_t pid,
                         const char *id, uint32_t bg_pid, uint32_t budget_ms, uint32_t flags,
                         const moy_app_run_t *run, char *err, size_t errlen);
int moy_os_command_step(uint32_t pid, uint32_t max_us);
int moy_os_command_stop(uint32_t pid);

// The network's holder: net(ctx, pid, 1) when process `pid` opens its first
// socket, net(ctx, pid, 0) at its end, each on the task that serves its
// session (the console's, which holds the radio: runtime/run/elf_run.py).
// NULL: nothing holds it.
typedef void (*moy_os_net_fn)(void *ctx, uint32_t pid, int hold);
void moy_os_set_net(moy_os_net_fn net, void *ctx);

// Service module `m` started as process `pid` (`id` its cart's id): the
// process, its main running on its own task, or NULL with the reason in
// `err` (the module unloaded). Its event queue is the one the OS opened for
// `pid`, or one opened here with `id` as its app, closed at its end. `run` is
// its app run (NULL: none), its app calls hopping to the VM's task.
moy_os_proc_t *moy_os_service(const moy_os_module_t *m, const moy_os_caps_t *caps,
                              uint32_t pid, const char *id, const moy_app_run_t *run,
                              char *err, size_t errlen);

// A GAME is main(argc, argv) on its task, argv its cart's id, as a service's
// is: a loop over its event descriptor, hearing the system's message
// MOY_OS_FRAME once a frame while it has read since the last, its arguments
// [held, pressed, ms]: the buttons held and those pressed since the last
// frame message, a bit each in libmoy's moy_button order, and the frame's
// milliseconds, then the frame's input words where the console gives them
// (an app's: MOY_OS_FRAME_IN_*). Its screen is w x h pixels of `depth`
// bytes, row by row: palette indices (1, a game's) or RGB565 (2, an app's,
// the surface a copy of the toolkit it links draws on). moy_screen answers
// its size, and moy_present copies a whole screen from the module into the
// process's frame, which the console draws at its frame. Its stop
// is a service's, the descriptor hung up. It has no process row of its own:
// `pid` is its Player run's, and the scheduler never reaps or stops it.
#define MOY_OS_FRAME "frame"
moy_os_proc_t *moy_os_game(const moy_os_module_t *m, const moy_os_caps_t *caps, uint32_t pid,
                           const char *id, int w, int h, int depth, const moy_app_run_t *run,
                           char *err, size_t errlen);
// On the console's task, once a frame: its screen slot's frame call
// (native/moy_kernel/moy_screen_slot.h): the frame the last call took let
// go, the frame message posted unless one waits unread (its arguments
// followed by the `n_in` words at `in`, MOY_OS_FRAME_IN at most), then up to
// `max_us` waiting for a present, the game's hops served meanwhile. A mask, MOY_OS_FRAME_SENT where the message was posted and
// MOY_OS_FRAME_SHOWN where a frame was presented, taken until the next call;
// MOY_OS_ENDED once main has ended (moy_os_game_stop answers its status); -1
// for no such game.
#define MOY_OS_FRAME_SENT 1
#define MOY_OS_FRAME_SHOWN 2
#define MOY_OS_ENDED (-4)
// An app's input words: the pointer's x and y on the screen, its state
// (lua_ext.snap_shared's flags, 0 none), the key typed this frame (0 none),
// how its ui frame draws (native/moy_ui/moy_ui_wasm.h's MOYCORE_UI_FACE_*:
// a window's body, the desk's rows, the shell's look, the font scale),
// whether the console's RGB565 words
// are byte-swapped (its canvas's swapped565), the rect its ui frame lies
// in, its content_rect() (x, y, w, h: the screen less the host's bars), and
// the surface a responsive app lays out for, its `_layout`'s (w, h, fs): the
// window's or the screen's size and the font scale (0, 0, 0 for a game).
#define MOY_OS_FRAME_IN 13
#define MOY_OS_FRAME_IN_X 0
#define MOY_OS_FRAME_IN_Y 1
#define MOY_OS_FRAME_IN_PTR 2
#define MOY_OS_FRAME_IN_KEY 3
#define MOY_OS_FRAME_IN_FACE 4
#define MOY_OS_FRAME_IN_SWAP 5
#define MOY_OS_FRAME_IN_ROOM_X 6
#define MOY_OS_FRAME_IN_ROOM_Y 7
#define MOY_OS_FRAME_IN_ROOM_W 8
#define MOY_OS_FRAME_IN_ROOM_H 9
#define MOY_OS_FRAME_IN_LAYOUT_W 10
#define MOY_OS_FRAME_IN_LAYOUT_H 11
#define MOY_OS_FRAME_IN_LAYOUT_FS 12
int moy_os_game_frame(uint32_t pid, uint32_t held, uint32_t pressed, uint32_t ms,
                      uint32_t max_us, const int32_t *in, int n_in);
// Game `pid`'s frame, w x h pixels of `depth` bytes, for the game's life (a
// frame call's SHOWN frame is what it holds until the next call); NULL for
// no such game.
const uint8_t *moy_os_game_screen(uint32_t pid, int *w, int *h, int *depth);
// Game `pid` stopped as a service is (moy_os_stop): its exit status, or -1.
int moy_os_game_stop(uint32_t pid);

// The pid of the live service started as `id` (n bytes, its cart's id): one
// neither stopping nor ended, else 0. Any task may ask (under the table's
// lock): moy_app's service_running row (native/moy_app/moy_app.c, svc_row).
uint32_t moy_os_service_of(const char *id, size_t n);
// The same service's event descriptor hung up (moy_os_stop's TERM, with no
// wait) from any task: its pid, 0 for none (moy_app's service_stop row).
uint32_t moy_os_service_term(const char *id, size_t n);

// On the console's task, at the scheduler's poll (a board's runs when an end
// or a kill wants the VM, moy_os_watch): every service that has ended swept -- main returned or exit called, or killed by the watchdog
// (moy_os.c's THE WATCHDOG: a wake struck out without a poll, or a start
// past MOY_OS_STOP_US, MOY_PROC_KILLED). The pids swept go to `ended` (up to
// `n`), with their exit statuses in `codes`; answers how many.
uint32_t moy_os_reap(uint32_t *ended, int32_t *codes, uint32_t n);
// On the console's task, once a frame while it answers true (a board's loop
// job, modmoy_elf.c): the watchdog run over the live services, and the VM
// wanted (moy_evq.h, THE VM'S WAKE) while one has ended and waits for its
// reap. Whether a service lives.
bool moy_os_watch(void);

// Stop service `pid`: its event descriptor hung up, main given
// MOY_OS_STOP_US to return, then every thread ended and every registration
// swept. Its exit status (137 when main ran out of time), or -1 for no such
// service.
int moy_os_stop(uint32_t pid);
// Every service stopped as moy_os_stop stops one, its exit logged: a soft
// reset, whose next VM has no record to take them back by (a VM stop keeps
// them, docs/os_2026-11/processes.md section 5). How many it stopped.
uint32_t moy_os_services_end(void);

// What the OS knows of process `pid`: 0, or -1 for none.
typedef struct {
    uint32_t pid;
    uint32_t threads;           // live threads
    uint32_t handles;           // registrations held
    uint32_t wakes;             // polls of its event descriptor that returned
    uint32_t events;            // records it read from it
    uint8_t service;            // 1 a service, 0 a command
    uint8_t ending;
    uint8_t net;                // 1 while it holds the network
    uint32_t sockets;           // sockets open
    uint32_t mapped;            // bytes of its mappings
    uint32_t cpu_us;            // its wakes' cost, summed (wrapping)
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

// The least free stack, in bytes, of any live thread's kernel stack (its
// FreeRTOS task's high-water mark), or UINT32_MAX with none: a board's.
uint32_t moy_os_stack_low(void);

// The last native faults since boot, newest first, n at most: a P4's U-mode
// module killed where it stood (moy_umode.h). cause is the core's mcause code,
// or 0x100 (no such row) or 0x200 | the row (a row given memory outside the
// process); pc is the module's own address (its ELF's vaddr) when inside it.
#define MOY_OS_FAULTS 4u
typedef struct {
    uint32_t pid;
    uint32_t cause;
    uint32_t pc;
    uint32_t addr;
    char name[24];
} moy_os_fault_t;
uint32_t moy_os_faults(moy_os_fault_t *out, uint32_t n);

#endif // MOY_OS_H
