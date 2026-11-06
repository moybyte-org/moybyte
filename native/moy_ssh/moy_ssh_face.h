// moy_ssh_face: the shell's face over an SSH channel, another face like the
// serial `sh` (native/moy_sh/moy_sh_board.h).
//
// A session's command opens the face (moy_ssh_command, from Dropbear's task):
// a pair of connected sockets, Dropbear's channel on one end, the face's on
// the other. The face is STEPPED where its shell may run -- on a board the
// VM's task, at the frame's tail (moy_ssh_face_tail), because a shell's role
// commands cross into Python there -- and a step never blocks on the
// network: it takes the bytes that have come, edits the line, and runs a
// complete line to its end. With a terminal (pty-req) the face is the
// terminal's line discipline: it echoes, erases, turns a newline into CR LF
// and reads Ctrl-C and Ctrl-D. Without one (`ssh host CMD`) it runs CMD and
// ends with its status.
//
// One face at a time, as one session at a time.

#ifndef MOY_SSH_FACE_H
#define MOY_SSH_FACE_H

#include <stddef.h>
#include <sys/types.h>

typedef struct moy_ssh_face moy_ssh_face_t;

// The shell a face runs: moy_sh on a board (moy_ssh_sh.c).
typedef struct {
    void *(*open)(moy_ssh_face_t *f);
    int (*run)(void *sh, const char *text, size_t n);   // MOY_SH_DONE/MORE/EXIT
    size_t (*prompt)(void *sh, int more, char *out, size_t cap);
    int (*status)(void *sh);
    void (*cancel)(void *sh);
    void (*close)(void *sh);
} moy_ssh_shell_t;

void moy_ssh_face_set_shell(const moy_ssh_shell_t *shell);

// The shell's output and its Ctrl-C, from inside a run.
void moy_ssh_face_out(moy_ssh_face_t *f, const char *p, size_t n);
int moy_ssh_face_poll(moy_ssh_face_t *f);

// One step of the face that is open, if any: on a board, the frame's tail.
void moy_ssh_face_tail(void);

// After a session: wait (stepping is the tail's) until its face has ended.
void moy_ssh_face_reap_all(int wait_ms);

#endif // MOY_SSH_FACE_H
