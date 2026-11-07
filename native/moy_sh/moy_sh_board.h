// moy_sh_board: the OS a shell session sees on a board, and the serial face.
//
// THE HOST TABLE. What the kernel knows is served in C, so it answers with
// the VM stopped: the log (moy_log_read), memory (heap_caps), reboot, the
// kernel's row and the run in front (the Player's: `kill` ends it), and
// /proc's status and log files. What the Python console knows crosses the
// ROLE door (moy_loop_role) on row MOY_SH_DOOR: the console's processes and
// its /proc and /dev (runtime/system/devfs.py), the installed commands, the
// role commands, df and the Python heap. While no VM runs the door answers
// NEEDS_VM: a role command or an installed one says "needs the console", and
// `ps` shows the console stopped.
//
// THE DOOR'S ROW carries `verb NUL arg NUL arg ...` and answers an int, or
// text into the answer buffer (runtime/shell/sh_door.py is its other half):
//   ps                      text: a row a line, `pid ppid kind state mem cpu name`
//   kill PID SIG            0, or an errno
//   status PID              text: `running` or `exited CODE`; ABSENT: none
//   lookup NAME             1 when an installed command has the name
//   spawn BG ARGV...        the exit status (a pid when BG is 1); ABSENT: none
//   role JOB ARGV...        start the role command and step it: the exit
//                           status, RUNNING (-100) while it goes on;
//                           ABSENT: not a command it serves
//   step JOB / stop JOB     its next step, the same answers / end it (130)
//   read PATH / list DIR    the bytes / a name a line (a folder's ends in /)
//   write PATH BYTES        0, or an errno
//   df PATH / mem           text: `total free`
//   rescan NAME...          0: the shelf re-reads those cart folders (SFTP's
//                           changes, native/moy_tty/moy_tty_sftp.c)
// A spawned program and a role command write and read their streams through
// the session that is running (moy_sh_running: the module's read and write).
//
// WITH NO VM the store's commands are served in C (moy_sh_store.c), over the
// same C the console calls: `settings` (list, get, set, unset) reads and
// writes system/settings through the settings rows' file reader and writer
// (native/moy_spine/moy_settings.h) and the store's crash-safe write; `pkg
// list` and `pkg remove` read and change the shelf's record through
// native/moy_store/moy_shelf.h, the removal forgetting the cart's consent in
// the settings rows. Every other role command, `settings reload` and the
// other `pkg` verbs need the console.
//
// THE SERIAL FACE is the dev channel's `sh` word (moy_devch.c): `sh LINE`
// runs LINE in the boot's serial session, made the first time and parked
// (moy_sh_park), and answers each line of output as `SH <line>`, then
// `SH end <status>`, `SH more` (the command goes on: send its next line) or
// `SH exit <status>` (the shell exited; the next `sh` starts a new one). A
// Ctrl-C byte on the channel, or `sh ^C` while a run is parked, interrupts
// the running command; any other `sh` line while one is parked answers
// `SH busy`. A text app a
// command opened (moy_sh_app.h) answers `SH app
// NAME` instead of `SH end`, a full-screen one after its screen, a row an
// `SH` line on a grid of MOY_SH_SERIAL_COLS by MOY_SH_SERIAL_ROWS; while it
// is in front each `sh LINE` is its: a full-screen app's keys in caret
// notation (`^S`, `^X`, `^[[A` the up arrow, `^?` Delete, `^^` a caret), a
// line app's line (`^D` alone ends it), answered the same way, and `SH end
// 0` once the app has ended.

#ifndef MOY_SH_BOARD_H
#define MOY_SH_BOARD_H

#include <stdbool.h>
#include <stddef.h>

#include "moy_sh.h"

#define MOY_SH_DOOR 0x7368u             // the ROLE door's row the shell crosses
#define MOY_SH_ARENA (128u * 1024u)     // a session's arena
#define MOY_SH_SERIAL_LINE 240u         // an `SH` line's bytes, longer ones wrap
#define MOY_SH_SERIAL_COLS 80           // a text app's screen on the serial face
#define MOY_SH_SERIAL_ROWS 24
#define MOY_SH_STORE_PATH 320u          // a store path's bytes, the root's included

// The console's store folder, as it boots on one: the card's (/sd/moybyte)
// where its carts are there, else the internal store's (/moy), else a host
// tree's root (""), which is a store's own folder; the card's where none has
// its carts yet. Its system/ is /etc, its carts/ the shelf, its files/ home.
const char *moy_sh_board_store(moy_sh_t *s);

// A new session settled in the store: /etc its system folder (unless
// `keep_etc`: the SSH face's is the server's own), its home and working
// folder the user's files folder, else `/` as it began: the store's index, or
// -1 when its files folder is not there.
int moy_sh_board_go_home(moy_sh_t *s, int keep_etc);
// A face's session made before the store was there (a card that mounts after
// the session began) goes home at the first line that finds it, while it is
// still at `/`. `*homed`: the face's word, set once it has.
void moy_sh_board_home_late(moy_sh_t *s, int *homed);

// The board's table into `h`, the face's terminal (`out`, `poll`, `ctx`) in it.
void moy_sh_board_host(moy_sh_host_t *h, void *ctx,
                       void (*out)(void *ctx, int stream, const char *p, size_t n),
                       int (*poll)(void *ctx));

// One crossing of the door (above) with `verb` and `argv`, through the
// session's hop: its int answer, or MOY_SH_DOOR_NEEDS_VM while no VM runs.
#define MOY_SH_DOOR_NEEDS_VM (-9)
int32_t moy_sh_board_door(moy_sh_t *s, const char *verb, int argc, char *const *argv);

// The store's commands with no VM (above): an exit status, or
// MOY_SH_NOT_FOUND when `argv` names none of them. `root` is the store folder
// as the C library names it (the files tree's root, then moy_sh_board_store).
int moy_sh_store_role(moy_sh_t *s, const char *root, int argc, char **argv);

// A long run's yield: the task sleeps a tick, so a task below it runs.
void moy_sh_board_yield(void);

// THE RUNNER. A face whose session runs on the console's task (the screen
// face, the serial face) gives it a runner: each line runs on a task of the
// session's own, in strict turn with the caller, so it shares nothing with
// the console but what the caller's waiting leaves alone. What crosses the
// door or touches the store hops back to the caller's task (the table's
// `hop`). A wait parks the run (`sleep`, a role command between its steps:
// the board's `sleep_ms`) and the caller's call answers MOY_SH_BUSY; the
// loop's job (moy_loop_job) hands the run its turn again each frame once the
// wait is up, steps the role command once more across the door, and calls
// the face's `done` with the run's answer when it ends. So a command that
// waits on the console blocks its face alone, and the frame goes on. With no
// VM (the recovery floor, an S3 running a cart without one) or no task to
// be had, a line runs on the caller's task as before.
typedef struct moy_sh_runner moy_sh_runner_t;
// A runner for the table `h` (its ctx and poll set: the runner wraps the
// poll, and sets `hop` and `runner`), or NULL where the loop has no jobs.
moy_sh_runner_t *moy_sh_board_runner(moy_sh_host_t *h, void (*done)(void *ctx, int r));
// moy_sh_run on the session's runner: its answer, or MOY_SH_BUSY while the
// run is parked (`done` gets the answer). MOY_SH_BUSY too while one is.
int moy_sh_board_run(moy_sh_t *s, const char *text, size_t n);
// Whether a run of the session is parked: the session is the run's until it
// ends, so nothing else may enter it (a prompt, a completion).
int moy_sh_board_busy(const moy_sh_t *s);
// Every run of the runner's on the caller's task, never parked.
void moy_sh_board_runner_inline(moy_sh_runner_t *r);
// Before the session is freed: a parked run is made to end (its waits end,
// its poll answers Ctrl-C), then the task and the record go.
void moy_sh_board_runner_end(moy_sh_runner_t *r);
// A Ctrl-C byte on the dev channel while the serial session's run is parked:
// true when one was (it is interrupted).
bool moy_sh_board_break(void);

// The serial face: run `text` (one line, no newline) in the serial session.
// False when no session could be made (said as `SH error`).
bool moy_sh_board_line(const char *text);
// The serial session given back, with the text app in front of it.
void moy_sh_board_end(void);

#endif // MOY_SH_BOARD_H
