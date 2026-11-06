// moy_ssh_face: the shell's face over an SSH channel (moy_ssh_face.h).

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include "moy_ssh.h"
#include "moy_ssh_face.h"
#include "port/moy_ssh_port.h"

#define LINE_MAX_BYTES 1024
#define AHEAD_BYTES 256
#define HELD_MAX (256u * 1024u)         // what a hop may write before the rest is dropped
#define SH_MORE 1
#define SH_EXIT 2
#define WAIT_MS 250                     // the face's wait for input: an app's clock, a signal
#define DRAIN_MS 2000                   // the face's wait for Dropbear's half-close

uint32_t moy_ssh_mark_shell(void);

struct moy_ssh_face {
    int fd;                     // the face's end of the pair
    int pty;                    // the client has a terminal: be its line discipline
    int cols, rows;
    pid_t pid;
    char *cmd;                  // `exec`'s command; NULL: the interactive shell
    void *sh;
    int started;                // 1 the face task took it, 2 the reaper did
    int more, esc, last_cr, eof, front;
    volatile int interrupt;     // a `signal` request (INT)
    volatile int resized;       // a window-change request
    volatile int exited;        // the status is final: Dropbear may reap it
    volatile int done;          // set last by the face task: the rest is the reaper's
    int reaped;
    int status;
    size_t n, ahead_n;
    char *held;                 // written off the face's task (a hop), sent after
    size_t held_n, held_cap;
    char line[LINE_MAX_BYTES];
    char ahead[AHEAD_BYTES];    // bytes a poll read that were not Ctrl-C
};

void moy_ssh_face_set_shell(const moy_ssh_shell_t *shell) {
    moy_ssh_faces()->shell = shell;
}

static const moy_ssh_shell_t *shell(void) {
    return moy_ssh_faces()->shell;
}

static int on_face_task(void) {
    return moy_ssh_self() == moy_ssh_faces()->task;
}

static void send_all(moy_ssh_face_t *f, const char *p, size_t n) {
    while (n > 0 && f->fd >= 0) {
        ssize_t w = send(f->fd, p, n, 0);
        if (w < 0) {
            if (errno == EINTR || errno == EAGAIN) {
                continue;
            }
            return;
        }
        p += w;
        n -= (size_t)w;
    }
}

// Off the face's task the bytes wait: a slow client never holds the console.
static void hold(moy_ssh_face_t *f, const char *p, size_t n) {
    if (f->held_n + n > f->held_cap) {
        size_t cap = f->held_cap ? f->held_cap : 1024;
        while (cap < f->held_n + n && cap < HELD_MAX) {
            cap *= 2;
        }
        if (cap > HELD_MAX) {
            cap = HELD_MAX;
        }
        char *q = moy_ssh_calloc(1, cap);
        if (q == NULL) {
            return;
        }
        memcpy(q, f->held, f->held_n);
        moy_ssh_free(f->held);
        f->held = q;
        f->held_cap = cap;
    }
    if (n > f->held_cap - f->held_n) {
        n = f->held_cap - f->held_n;
    }
    memcpy(f->held + f->held_n, p, n);
    f->held_n += n;
}

static void out(moy_ssh_face_t *f, const char *p, size_t n, int raw) {
    if (!on_face_task()) {
        hold(f, p, n);
        return;
    }
    if (raw || !f->pty) {
        send_all(f, p, n);
        return;
    }
    // a terminal's newline is CR LF
    size_t start = 0;
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n' && (i == 0 || p[i - 1] != '\r')) {
            send_all(f, p + start, i - start);
            send_all(f, "\r\n", 2);
            start = i + 1;
        }
    }
    send_all(f, p + start, n - start);
}

void moy_ssh_face_out(moy_ssh_face_t *f, const char *p, size_t n) {
    out(f, p, n, 0);
}

void moy_ssh_face_raw(moy_ssh_face_t *f, const char *p, size_t n) {
    out(f, p, n, 1);
}

void moy_ssh_face_size(const moy_ssh_face_t *f, int *cols, int *rows) {
    *cols = f->cols > 0 ? f->cols : 80;
    *rows = f->rows > 0 ? f->rows : 24;
}

void moy_ssh_face_hop(moy_ssh_face_t *f, void (*fn)(void *arg), void *arg) {
    moy_ssh_hop(fn, arg);
    if (f->held_n && on_face_task()) {
        size_t n = f->held_n;
        f->held_n = 0;
        moy_ssh_face_out(f, f->held, n);
    }
}

// Ctrl-C: a terminal's byte or the client's signal. Without a terminal the
// channel's bytes are the command's input, never keys.
int moy_ssh_face_poll(moy_ssh_face_t *f) {
    if (f->interrupt) {
        f->interrupt = 0;
        return 1;
    }
    if (!f->pty) {
        return 0;
    }
    char b[64];
    ssize_t n = recv(f->fd, b, sizeof b, MSG_DONTWAIT);
    if (n == 0) {
        f->eof = 1;
        return 1;
    }
    int stop = 0;
    for (ssize_t i = 0; i < n; i++) {
        if (b[i] == 3) {
            stop = 1;
        } else if (!stop && f->ahead_n < sizeof f->ahead) {
            f->ahead[f->ahead_n++] = b[i];
        }
    }
    if (stop) {
        moy_ssh_face_out(f, "^C\n", 3);
    }
    return stop;
}

// Wait up to `ms` for the channel's bytes: 1 readable, 0 not yet.
static int readable(moy_ssh_face_t *f, int ms) {
    fd_set r;
    FD_ZERO(&r);
    FD_SET(f->fd, &r);
    struct timeval tv = {ms / 1000, (ms % 1000) * 1000};
    return select(f->fd + 1, &r, NULL, NULL, &tv) > 0;
}

// The command's standard input: the channel's bytes until the client's EOF.
// Off the face's task only what has come is read, so a hop never waits on
// the client.
long moy_ssh_face_in(moy_ssh_face_t *f, void *buf, size_t n) {
    if (f->pty || n == 0) {
        return 0;
    }
    if (f->ahead_n) {
        size_t k = f->ahead_n < n ? f->ahead_n : n;
        memcpy(buf, f->ahead, k);
        memmove(f->ahead, f->ahead + k, f->ahead_n - k);
        f->ahead_n -= k;
        return (long)k;
    }
    while (!f->eof) {
        int face = on_face_task();
        if (face && !readable(f, WAIT_MS)) {
            if (f->interrupt) {
                return -1;
            }
            continue;
        }
        ssize_t r = recv(f->fd, buf, n, face ? 0 : MSG_DONTWAIT);
        if (r > 0) {
            return (long)r;
        }
        if (r == 0) {
            f->eof = 1;
        } else if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) {
            f->eof = 1;
        } else if (!face) {
            return 0;
        }
    }
    return 0;
}

static void prompt(moy_ssh_face_t *f) {
    char p[128];
    size_t n = shell()->prompt(f->sh, f->more, p, sizeof p);
    moy_ssh_face_out(f, p, n);
}

// The client's bytes the command left unread, read and dropped until
// Dropbear's half-close: it shuts its end's write side once it has reaped
// the status, and an end read to that EOF closes with nothing unread.
static void drain(moy_ssh_face_t *f) {
    uint32_t t0 = moy_ssh_ms();
    while (moy_ssh_ms() - t0 < DRAIN_MS) {
        if (!readable(f, WAIT_MS)) {
            continue;
        }
        ssize_t n = recv(f->fd, f->line, sizeof f->line, 0);
        if (n == 0 || (n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)) {
            return;
        }
    }
}

// The status is published before the output's FIN: Dropbear wakes on that
// EOF and must find the exit there, or the channel never closes. The socket
// closes after the drain: lwIP answers a close with bytes unread with a
// reset, and a reset drops the output the face has not yet sent.
static void finish(moy_ssh_face_t *f, int status) {
    f->status = status;
    if (f->sh != NULL) {
        shell()->close(f->sh);
        f->sh = NULL;
    }
    __atomic_store_n(&f->exited, 1, __ATOMIC_RELEASE);
    shutdown(f->fd, SHUT_WR);
    drain(f);
    close(f->fd);
    f->fd = -1;
    moy_ssh_free(f->held);
    f->held = NULL;
    f->held_n = f->held_cap = 0;
    __atomic_store_n(&f->done, 1, __ATOMIC_RELEASE);
}

static int front(moy_ssh_face_t *f) {
    return shell()->front != NULL ? shell()->front(f->sh) : MOY_SSH_FRONT_SHELL;
}

// After a line's run or an app's step: an app that came to the front is
// drawn; the prompt is shown when the shell (or a line app) waits.
static void settle(moy_ssh_face_t *f) {
    int now = front(f);
    if (now == MOY_SSH_FRONT_FULL && f->front != MOY_SSH_FRONT_FULL) {
        shell()->show(f->sh, 1);
    }
    f->front = now;
    if (now != MOY_SSH_FRONT_FULL && (f->pty || !f->more)) {
        prompt(f);
    }
}

// A complete line to the shell or to the line app in front; 1 when the shell exited.
static int run_line(moy_ssh_face_t *f) {
    if (f->front == MOY_SSH_FRONT_LINE) {
        f->line[f->n] = 0;
        f->n = 0;
        shell()->line(f->sh, f->line);
        settle(f);
        return 0;
    }
    f->line[f->n++] = '\n';
    int r = shell()->run(f->sh, f->line, f->n);
    f->n = 0;
    if (r == SH_EXIT) {
        finish(f, shell()->status(f->sh));
        return 1;
    }
    f->more = r == SH_MORE;
    settle(f);
    return 0;
}

// The terminal's keys; 1 when the face ended.
static int key(moy_ssh_face_t *f, char c) {
    if (f->esc) {
        // an escape sequence (an arrow, a function key) is dropped whole
        if (f->esc == 1 && (c == '[' || c == 'O')) {
            f->esc = 2;
        } else if (f->esc == 1 || (c >= 0x40 && c <= 0x7e)) {
            f->esc = 0;
        }
        return 0;
    }
    if (c == '\n' && f->last_cr) {
        f->last_cr = 0;
        return 0;
    }
    f->last_cr = c == '\r';
    switch (c) {
    case 0x1b:
        f->esc = 1;
        return 0;
    case 3:
        moy_ssh_face_out(f, "^C\n", 3);
        f->n = 0;
        f->more = 0;
        if (f->front == MOY_SSH_FRONT_SHELL) {
            shell()->cancel(f->sh);
        }
        prompt(f);
        return 0;
    case 4:
        if (f->n == 0) {
            moy_ssh_face_out(f, "\n", 1);
            if (f->front == MOY_SSH_FRONT_LINE) {
                shell()->keys(f->sh, "\x04", 1);
                settle(f);
                return 0;
            }
            finish(f, shell()->status(f->sh));
            return 1;
        }
        return 0;
    case 0x7f:
    case 8:
        if (f->n > 0) {
            f->n--;
            send_all(f, "\b \b", 3);
        }
        return 0;
    case '\r':
    case '\n':
        moy_ssh_face_out(f, "\n", 1);
        return run_line(f);
    default:
        if ((unsigned char)c >= 0x20 && f->n < sizeof f->line - 1) {
            f->line[f->n++] = c;
            send_all(f, &c, 1);
        }
        return 0;
    }
}

// The bytes of a session with no terminal (`ssh -T host`): lines, no echo.
static int plain(moy_ssh_face_t *f, char c) {
    if (c == '\n') {
        return run_line(f);
    }
    if (c != '\r' && f->n < sizeof f->line - 1) {
        f->line[f->n++] = c;
    }
    return 0;
}

static int feed(moy_ssh_face_t *f, const char *p, size_t n) {
    if (f->front == MOY_SSH_FRONT_FULL) {
        if (shell()->keys(f->sh, p, n)) {
            f->front = MOY_SSH_FRONT_SHELL;
            settle(f);
        } else {
            shell()->show(f->sh, 0);
        }
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        if (f->pty ? key(f, p[i]) : plain(f, p[i])) {
            return 1;
        }
        if (f->front == MOY_SSH_FRONT_FULL && i + 1 < n) {
            return feed(f, p + i + 1, n - i - 1);
        }
    }
    return 0;
}

// A face from its open to its end, on the face task.
static void face_run(moy_ssh_face_t *f) {
    moy_ssh_mark_shell();
    f->sh = shell() != NULL ? shell()->open(f) : NULL;
    if (f->sh == NULL) {
        moy_ssh_face_out(f, "moybyte: no shell\n", 18);
        finish(f, 1);
        return;
    }
    if (f->cmd != NULL) {
        int r = shell()->run(f->sh, f->cmd, strlen(f->cmd));
        if (r == SH_MORE) {
            // an unfinished command (an open quote): ended as sh -c would
            shell()->run(f->sh, "\n", 1);
        }
        finish(f, shell()->status(f->sh));
        return;
    }
    settle(f);
    for (;;) {
        if (f->ahead_n) {
            char a[AHEAD_BYTES];
            size_t n = f->ahead_n;
            memcpy(a, f->ahead, n);
            f->ahead_n = 0;
            if (feed(f, a, n)) {
                return;
            }
        }
        if (f->eof) {
            finish(f, shell()->status(f->sh));
            return;
        }
        if (f->interrupt) {
            f->interrupt = 0;
            if (f->front == MOY_SSH_FRONT_FULL ? feed(f, "\x03", 1) : key(f, 3)) {
                return;
            }
        }
        if (f->resized) {
            f->resized = 0;
            if (shell()->resize != NULL) {
                shell()->resize(f->sh, f->cols, f->rows);
            }
        }
        if (f->front == MOY_SSH_FRONT_FULL) {
            shell()->tick(f->sh);
            if (front(f) != MOY_SSH_FRONT_FULL) {
                f->front = MOY_SSH_FRONT_SHELL;
                settle(f);
            } else {
                shell()->show(f->sh, 0);
            }
        }
        if (!readable(f, WAIT_MS)) {
            continue;
        }
        char b[256];
        ssize_t n = recv(f->fd, b, sizeof b, 0);
        if (n == 0) {
            finish(f, shell()->status(f->sh));
            return;
        }
        if (n < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                finish(f, 255);
                return;
            }
            continue;
        }
        if (feed(f, b, (size_t)n)) {
            return;
        }
    }
}

void moy_ssh_face_task(volatile int *stop) {
    moy_ssh_faces_t *fs = moy_ssh_faces();
    fs->task = moy_ssh_self();
    while (!*stop) {
        moy_ssh_face_t *f = __atomic_load_n(&fs->face, __ATOMIC_ACQUIRE);
        int idle = 0;
        if (f != NULL && __atomic_compare_exchange_n(&f->started, &idle, 1, 0,
                                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            face_run(f);
            continue;
        }
        moy_ssh_nap(WAIT_MS);
    }
}

// -- Dropbear's side (port/moy_ssh_port.h) -------------------------------------

static void face_free(moy_ssh_face_t *f) {
    moy_ssh_free(f->cmd);
    moy_ssh_free(f->held);
    moy_ssh_free(f);
}

int moy_ssh_command(const char *cmd, int pty, int cols, int rows, int *fd, pid_t *pid) {
    moy_ssh_faces_t *fs = moy_ssh_faces();
    moy_ssh_face_t *old = __atomic_load_n(&fs->face, __ATOMIC_ACQUIRE);
    if (old != NULL) {
        if (!old->reaped || !__atomic_load_n(&old->done, __ATOMIC_ACQUIRE)) {
            return -1;
        }
        __atomic_store_n(&fs->face, NULL, __ATOMIC_RELEASE);
        face_free(old);
    }
    moy_ssh_face_t *f = moy_ssh_calloc(1, sizeof *f);
    if (f == NULL) {
        return -1;
    }
    int fds[2];
    if (moy_ssh_pair(fds) != 0) {
        moy_ssh_free(f);
        return -1;
    }
    if (cmd != NULL) {
        size_t n = strlen(cmd);
        f->cmd = moy_ssh_calloc(1, n + 2);
        if (f->cmd == NULL) {
            close(fds[0]);
            close(fds[1]);
            moy_ssh_free(f);
            return -1;
        }
        memcpy(f->cmd, cmd, n);
        f->cmd[n] = '\n';
    }
    f->fd = fds[1];
    f->pty = pty;
    f->cols = cols;
    f->rows = rows;
    f->pid = 100 + fs->next_pid++;
    *fd = fds[0];
    *pid = f->pid;
    __atomic_store_n(&fs->face, f, __ATOMIC_RELEASE);
    moy_ssh_poke(fs->task);
    return 0;
}

int moy_ssh_reap(pid_t *pid, int *status) {
    moy_ssh_face_t *f = __atomic_load_n(&moy_ssh_faces()->face, __ATOMIC_ACQUIRE);
    if (f == NULL || f->reaped || !__atomic_load_n(&f->exited, __ATOMIC_ACQUIRE)) {
        return 0;
    }
    // freed once the face task has let go of it (done): by the next command
    // or after the session (moy_ssh_face_reap_all)
    f->reaped = 1;
    *pid = f->pid;
    *status = f->status;
    return 1;
}

int moy_ssh_signal(pid_t pid, int sig) {
    moy_ssh_face_t *f = __atomic_load_n(&moy_ssh_faces()->face, __ATOMIC_ACQUIRE);
    if (f == NULL || f->pid != pid || sig != SIGINT) {
        return -1;
    }
    f->interrupt = 1;
    return 0;
}

void moy_ssh_winch(pid_t pid, int cols, int rows) {
    moy_ssh_face_t *f = __atomic_load_n(&moy_ssh_faces()->face, __ATOMIC_ACQUIRE);
    if (f != NULL && f->pid == pid) {
        f->cols = cols;
        f->rows = rows;
        f->resized = 1;
    }
}

void moy_ssh_face_reap_all(int wait_ms) {
    moy_ssh_faces_t *fs = moy_ssh_faces();
    for (int t = 0; t < wait_ms; t += 10) {
        moy_ssh_face_t *f = __atomic_load_n(&fs->face, __ATOMIC_ACQUIRE);
        if (f == NULL) {
            return;
        }
        if (__atomic_load_n(&f->done, __ATOMIC_ACQUIRE)) {
            __atomic_store_n(&fs->face, NULL, __ATOMIC_RELEASE);
            face_free(f);
            return;
        }
        int idle = 0;
        if (__atomic_compare_exchange_n(&f->started, &idle, 2, 0, __ATOMIC_ACQ_REL,
                                        __ATOMIC_ACQUIRE)) {
            // the session ended before its face ran: it never will
            __atomic_store_n(&fs->face, NULL, __ATOMIC_RELEASE);
            close(f->fd);
            face_free(f);
            return;
        }
        usleep(10000);
    }
}
