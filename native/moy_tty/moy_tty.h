// moy_tty: the shell's face over a byte stream (README.md), the one face SSH
// (native/moy_ssh) and the web terminal (moy_tty_ws.c) open, another face
// like the serial `sh` and the Terminal's screen (native/moy_sh).
//
// A TRANSPORT OPENS A FACE (moy_tty_open): a pair of connected sockets, the
// transport's end and the face's. The face RUNS on a task of its own (the
// face task, PSRAM stack on a board), so a long command holds no frame and a
// slow client holds only that task. What the shell must do on the console's
// task -- the store's files, the door's crossings into Python -- it hops
// there (moy_tty_hop, served at the frame's tail by moy_tty_tail), and what
// it writes while there is held and sent from the face's task after the hop.
//
// With a terminal (`pty`) the face is the terminal's line discipline: it
// echoes, erases, turns a newline into CR LF and reads Ctrl-C and Ctrl-D, its
// Left, Right, Home, End and Delete edit the line, Up and Down walk the lines
// run before (an arrow as ESC [ or ESC O, however the reads split it), and Tab
// completes the word left of the caret as the Terminal's face does
// (moy_sh_cands: a command's name, a path's from its folder; several names
// complete to what they share and a second Tab lists them). A text app in
// front (edit, top, python) gets the keys or the lines, a full-screen app an
// escape's bytes together (a lone ESC after PEND_MS). Without a terminal
// (`ssh host CMD`) it runs CMD, its standard input the stream's, and ends with
// its status.
//
// ONE FACE AT A TIME across the transports: an open while another
// transport's face runs answers MOY_TTY_EBUSY. Each transport holds the face
// task while it serves (moy_tty_hold); the task is made at the first hold and
// parks while nobody holds it and no face runs, never deleted (a deleted
// PSRAM-stacked task links FreeRTOS's task-state query into IRAM).

#ifndef MOY_TTY_H
#define MOY_TTY_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

typedef struct moy_tty moy_tty_t;

// The transports, each a bit of the hold word.
#define MOY_TTY_SSH 1
#define MOY_TTY_WEB 2

#define MOY_TTY_EBUSY (-2)              // another transport's face runs
#define MOY_TTY_ENOMEM (-1)

// What a shell has in front of its prompt.
#define MOY_TTY_FRONT_SHELL 0
#define MOY_TTY_FRONT_FULL 1            // a full-screen app: the keys are its
#define MOY_TTY_FRONT_LINE 2            // a line app: the lines are its

#define MOY_TTY_LINE 1024               // a typed line's bytes
#define MOY_TTY_SFTP 2                  // moy_tty_open's `pty`: the SFTP subsystem

// The shell a face runs: moy_sh on a board (moy_tty_sh.c).
typedef struct {
    void *(*open)(moy_tty_t *f);
    int (*run)(void *sh, const char *text, size_t n);   // MOY_SH_DONE/MORE/EXIT
    size_t (*prompt)(void *sh, int more, char *out, size_t cap);
    int (*status)(void *sh);
    void (*cancel)(void *sh);
    void (*close)(void *sh);
    // The app in front (may be NULL: none ever is): MOY_TTY_FRONT_*.
    int (*front)(void *sh);
    // A full-screen app's keys, a line app's line: 1 when the app ended.
    int (*keys)(void *sh, const char *p, size_t n);
    int (*line)(void *sh, const char *text);
    // Its screen drawn (whole, or what changed), its clock, its new size.
    void (*show)(void *sh, int whole);
    void (*tick)(void *sh);
    void (*resize)(void *sh, int cols, int rows);
    // Tab on `line` with the caret at `col` (may be NULL: Tab does nothing):
    // the bytes to insert at the caret into `ins`, or with several names
    // that share nothing more, a NUL-separated list of them into `list`
    // (*nlist the names); answers the inserted bytes.
    size_t (*complete)(void *sh, const char *line, size_t col, char *ins, size_t cap,
                       char *list, size_t list_cap, int *nlist);
    // The SFTP server over the session's files tree (MOY_TTY_SFTP), to the
    // stream's end: its exit status (may be NULL: no SFTP).
    int (*sftp)(void *sh);
} moy_tty_shell_t;

void moy_tty_set_shell(const moy_tty_shell_t *shell);
// The folder the shell's /etc names (the store's system folder), or NULL:
// the one the session finds (moy_sh_board_go_home).
void moy_tty_set_etc(const char *etc);
const char *moy_tty_etc(void);

// On the console's task, before a transport opens its first face: the
// state, and which task the hops wait for. 0, or MOY_TTY_ENOMEM.
int moy_tty_setup(void);

// A transport's hold on the face task (`on` 1) or its release (0): 0, or
// MOY_TTY_ENOMEM when the task could not be made.
int moy_tty_hold(int via, int on);
// Whether a face transport `via` opened is still running.
int moy_tty_busy(int via);

// A face for transport `via`: `cmd` (NULL: the interactive shell), with a
// terminal (`pty` 1) or not (0), or with `pty` MOY_TTY_SFTP the session's SFTP
// server in place of a command (moy_tty_sftp.c), `started` called on the
// face's task as its shell opens (may be NULL). 0 with the transport's end in
// *fd and the face's id in *pid, MOY_TTY_EBUSY or MOY_TTY_ENOMEM. An SFTP
// server is the face while it runs: one at a time with a shell, across the
// transports.
int moy_tty_open(int via, const char *cmd, int pty, int cols, int rows, void (*started)(void),
                 int *fd, pid_t *pid);
// A face of `via` whose status is final, since the last call: 1 with its id
// and exit status, 0 when none.
int moy_tty_reap(int via, pid_t *pid, int *status);
// Ctrl-C from the client (SIGINT), and its terminal's new size.
int moy_tty_signal(pid_t pid, int sig);
void moy_tty_winch(pid_t pid, int cols, int rows);
// After a transport's session: wait up to `wait_ms` until its face has
// ended, then free it.
void moy_tty_reap_all(int via, int wait_ms);

// The shell's output (CR LF on a terminal), its raw bytes (a full-screen
// app's escapes), its Ctrl-C, its standard input, and its size.
void moy_tty_out(moy_tty_t *f, const char *p, size_t n);
void moy_tty_raw(moy_tty_t *f, const char *p, size_t n);
int moy_tty_poll(moy_tty_t *f);
long moy_tty_in(moy_tty_t *f, void *buf, size_t n);
// On the face's task, the output sent first: whether the stream has bytes to
// read (or has ended) within `ms`.
int moy_tty_ready(moy_tty_t *f, int ms);
void moy_tty_size(const moy_tty_t *f, int *cols, int *rows);
// Whether the client asked for a terminal.
int moy_tty_pty(const moy_tty_t *f);
// fn(arg) on the console's task (moy_tty_hop), what it wrote sent after.
void moy_tty_face_hop(moy_tty_t *f, void (*fn)(void *arg), void *arg);
// The SFTP server (moy_tty_sftp.c) over the face's stream and the files tree
// of moy_sh session `s`, to the stream's end: its exit status.
struct moy_sh;
int moy_tty_sftp(moy_tty_t *f, struct moy_sh *s);

typedef struct {
    uint32_t faces;                     // faces opened, by any transport
    uint32_t busy;                      // opens refused: another transport's face ran
    size_t arena_peak;                  // the most of a shell's arena a face used
    uint32_t sends;                     // the face's sends on its end
    uintptr_t task;                     // the face task, 0 before the first hold
} moy_tty_stats_t;

void moy_tty_stats(moy_tty_stats_t *out);
// A shell's arena at its end, for arena_peak.
void moy_tty_note_arena(size_t used);

// -- the tasks (moy_tty_sys.c) -------------------------------------------------------

// A task's id; a nap of up to `ms` (a poke ends it early); a poke of task
// `t`'s nap; a millisecond clock.
uintptr_t moy_tty_self(void);
void moy_tty_nap(int ms);
#define MOY_TTY_FOREVER (-1)
void moy_tty_poke(uintptr_t t);
uint32_t moy_tty_ms(void);
// The console's task's own wait, which leaves its task notifications alone.
void moy_tty_console_nap(int ms);
// fn(arg) on a task of its own (a PSRAM stack on a board), its id or 0.
uintptr_t moy_tty_spawn(void (*fn)(void *arg), const char *name, uint32_t stack, void *arg);
// Two connected stream sockets (the transport's end, the face's end).
int moy_tty_pair(int fds[2]);
// fn(arg) on the console's task, waiting for it; in place when called there
// or before moy_tty_setup.
void moy_tty_hop(void (*fn)(void *arg), void *arg);
// The console's task, at every frame's tail: the hop that waits, run.
void moy_tty_tail(void);
// Whether the console's task is inside a hop now (a face's command).
int moy_tty_serving(void);
// PSRAM on a board.
void *moy_tty_calloc(size_t n, size_t size);
void moy_tty_free(void *p);

// -- the web terminal (moy_tty_ws.c) ----------------------------------------------------

// The webhost's upgraded socket (GET /term), on the console's task, and the
// session its frames are sealed under (moy_webauth.h; NULL on the open host):
// 0 when the bridge took it, MOY_TTY_EBUSY while it serves one, MOY_TTY_ENOMEM.
struct moy_wa_seal;
int moy_web_term(int fd, int cols, int rows, const struct moy_wa_seal *seal);

typedef struct {
    uint32_t sessions;                  // sockets the bridge served
    uint32_t refused;                   // ...that found the face another's
    uint32_t in_bytes, out_bytes;       // the keys in, the face's bytes out
    uint32_t frames_out;                // the frames that carried them
    int live;                           // a socket served now
} moy_tty_ws_stats_t;

void moy_tty_ws_stats(moy_tty_ws_stats_t *out);

// The web terminal's link (moy_tty_ws.c): cut, and its quiet time in ms.
void moy_web_term_cut(void);
uint32_t moy_web_term_idle_ms(void);

#endif // MOY_TTY_H
