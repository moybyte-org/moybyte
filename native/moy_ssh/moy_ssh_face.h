// moy_ssh_face: the shell's face over an SSH channel, another face like the
// serial `sh` (native/moy_sh/moy_sh_board.h).
//
// A session's command opens the face (moy_ssh_command, from Dropbear's task):
// a pair of connected sockets, Dropbear's channel on one end, the face's on
// the other. The face RUNS on a task of its own (moy_ssh_face_task: on a
// board the server's second task, PSRAM stack), so a long command holds no
// frame and a slow client holds only that task. What the shell must do on
// the console's task -- the store's files, the door's crossings into Python
// -- it hops there (moy_ssh_hop, served at the frame's tail), and what it
// writes while there is held and sent from the face's task after the hop.
//
// With a terminal (pty-req) the face is the terminal's line discipline: it
// echoes, erases, turns a newline into CR LF and reads Ctrl-C and Ctrl-D, and
// a text app in front (edit, top, python) gets the keys or the lines. Without
// one (`ssh host CMD`) it runs CMD, its standard input the channel's, and
// ends with its status.
//
// One face at a time, as one session at a time.

#ifndef MOY_SSH_FACE_H
#define MOY_SSH_FACE_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

typedef struct moy_ssh_face moy_ssh_face_t;

// What a shell has in front of its prompt.
#define MOY_SSH_FRONT_SHELL 0
#define MOY_SSH_FRONT_FULL 1            // a full-screen app: the keys are its
#define MOY_SSH_FRONT_LINE 2            // a line app: the lines are its

// The shell a face runs: moy_sh on a board (moy_ssh_sh.c).
typedef struct {
    void *(*open)(moy_ssh_face_t *f);
    int (*run)(void *sh, const char *text, size_t n);   // MOY_SH_DONE/MORE/EXIT
    size_t (*prompt)(void *sh, int more, char *out, size_t cap);
    int (*status)(void *sh);
    void (*cancel)(void *sh);
    void (*close)(void *sh);
    // The app in front (may be NULL: none ever is): MOY_SSH_FRONT_*.
    int (*front)(void *sh);
    // A full-screen app's keys, a line app's line: 1 when the app ended.
    int (*keys)(void *sh, const char *p, size_t n);
    int (*line)(void *sh, const char *text);
    // Its screen drawn (whole, or what changed), its clock, its new size.
    void (*show)(void *sh, int whole);
    void (*tick)(void *sh);
    void (*resize)(void *sh, int cols, int rows);
} moy_ssh_shell_t;

// The server's state the faces keep (PSRAM, moy_ssh.c's).
typedef struct {
    const moy_ssh_shell_t *shell;
    moy_ssh_face_t *volatile face;      // the open face, or NULL
    pid_t next_pid;
    volatile uintptr_t task;            // the face task's
} moy_ssh_faces_t;

moy_ssh_faces_t *moy_ssh_faces(void);

void moy_ssh_face_set_shell(const moy_ssh_shell_t *shell);

// The shell's output (CR LF on a terminal), its raw bytes (a full-screen
// app's escapes), its Ctrl-C, its standard input, and its size.
void moy_ssh_face_out(moy_ssh_face_t *f, const char *p, size_t n);
void moy_ssh_face_raw(moy_ssh_face_t *f, const char *p, size_t n);
int moy_ssh_face_poll(moy_ssh_face_t *f);
long moy_ssh_face_in(moy_ssh_face_t *f, void *buf, size_t n);
void moy_ssh_face_size(const moy_ssh_face_t *f, int *cols, int *rows);
// fn(arg) on the console's task (moy_ssh_hop), what it wrote sent after.
void moy_ssh_face_hop(moy_ssh_face_t *f, void (*fn)(void *arg), void *arg);

// The face task's body: run each face that opens until `*stop`.
void moy_ssh_face_task(volatile int *stop);

// After a session: wait until its face has ended, then free it.
void moy_ssh_face_reap_all(int wait_ms);

#endif // MOY_SSH_FACE_H
