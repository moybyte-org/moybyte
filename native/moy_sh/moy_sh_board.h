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
//   ps                      text: a row a line, `pid ppid kind state mem name`
//   kill PID SIG            0, or an errno
//   status PID              text: `running` or `exited CODE`; ABSENT: none
//   lookup NAME             1 when an installed command has the name
//   spawn BG ARGV...        the exit status (a pid when BG is 1); ABSENT: none
//   role ARGV...            the exit status; ABSENT: not a command it serves
//   read PATH / list DIR    the bytes / a name a line (a folder's ends in /)
//   write PATH BYTES        0, or an errno
//   df PATH / mem           text: `total free`
// A spawned program and a role command write and read their streams through
// the session that is running (moy_sh_running: the module's read and write).
//
// THE SERIAL FACE is the dev channel's `sh` word (moy_devch.c): `sh LINE`
// runs LINE in the boot's serial session, made the first time and parked
// (moy_sh_park), and answers each line of output as `SH <line>`, then
// `SH end <status>`, `SH more` (the command goes on: send its next line) or
// `SH exit <status>` (the shell exited; the next `sh` starts a new one). A
// Ctrl-C byte on the channel interrupts the running command.

#ifndef MOY_SH_BOARD_H
#define MOY_SH_BOARD_H

#include <stdbool.h>
#include <stddef.h>

#include "moy_sh.h"

#define MOY_SH_DOOR 0x7368u             // the ROLE door's row the shell crosses
#define MOY_SH_ARENA (128u * 1024u)     // a session's arena
#define MOY_SH_SERIAL_LINE 240u         // an `SH` line's bytes, longer ones wrap

// The board's table into `h`, the face's terminal (`out`, `poll`, `ctx`) in it.
void moy_sh_board_host(moy_sh_host_t *h, void *ctx,
                       void (*out)(void *ctx, int stream, const char *p, size_t n),
                       int (*poll)(void *ctx));

// A long run's yield: the task sleeps a tick, so a task below it runs.
void moy_sh_board_yield(void);

// The serial face: run `text` (one line, no newline) in the serial session.
// False when no session could be made (said as `SH error`).
bool moy_sh_board_line(const char *text);

#endif // MOY_SH_BOARD_H
