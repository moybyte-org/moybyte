// moy_ssh_face: the shell's face over an SSH channel (moy_ssh_face.h).

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "moy_ssh.h"
#include "moy_ssh_face.h"
#include "port/moy_ssh_port.h"

#define LINE_MAX_BYTES 1024
#define AHEAD_BYTES 256
#define SH_MORE 1
#define SH_EXIT 2

uint32_t moy_ssh_mark_shell(void);

struct moy_ssh_face {
    int fd;                     // the face's end of the pair
    int pty;                    // the client has a terminal: be its line discipline
    int cols, rows;
    pid_t pid;
    char *cmd;                  // `exec`'s command; NULL: the interactive shell
    void *sh;
    int started, more, esc, last_cr;
    volatile int interrupt;     // a `signal` request (INT) or Ctrl-C
    volatile int exited;        // the status is final: Dropbear may reap it
    volatile int done;          // set last by the stepping task: the rest is the reaper's
    int reaped;
    int status;
    size_t n, ahead_n;
    char line[LINE_MAX_BYTES];
    char ahead[AHEAD_BYTES];    // bytes a poll read that were not Ctrl-C
};

static const moy_ssh_shell_t *s_shell;
static moy_ssh_face_t *volatile s_face;
static pid_t s_next_pid;

void moy_ssh_face_set_shell(const moy_ssh_shell_t *shell) {
    s_shell = shell;
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

void moy_ssh_face_out(moy_ssh_face_t *f, const char *p, size_t n) {
    if (!f->pty) {
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

int moy_ssh_face_poll(moy_ssh_face_t *f) {
    if (f->interrupt) {
        f->interrupt = 0;
        return 1;
    }
    char b[64];
    ssize_t n = recv(f->fd, b, sizeof b, MSG_DONTWAIT);
    int stop = 0;
    for (ssize_t i = 0; i < n; i++) {
        if (b[i] == 3) {
            stop = 1;
        } else if (!stop && f->ahead_n < sizeof f->ahead) {
            f->ahead[f->ahead_n++] = b[i];
        }
    }
    return stop;
}

static void prompt(moy_ssh_face_t *f) {
    char p[128];
    size_t n = s_shell->prompt(f->sh, f->more, p, sizeof p);
    moy_ssh_face_out(f, p, n);
}

// The status is published before the socket closes: Dropbear wakes on the
// close and must find the exit there, or the channel never closes.
static void finish(moy_ssh_face_t *f, int status) {
    f->status = status;
    if (f->sh != NULL) {
        s_shell->close(f->sh);
        f->sh = NULL;
    }
    __atomic_store_n(&f->exited, 1, __ATOMIC_RELEASE);
    shutdown(f->fd, SHUT_RDWR);
    close(f->fd);
    f->fd = -1;
    __atomic_store_n(&f->done, 1, __ATOMIC_RELEASE);
}

// A complete line to the shell; 1 when the shell exited.
static int run_line(moy_ssh_face_t *f) {
    f->line[f->n++] = '\n';
    int r = s_shell->run(f->sh, f->line, f->n);
    f->n = 0;
    if (r == SH_EXIT) {
        finish(f, s_shell->status(f->sh));
        return 1;
    }
    f->more = r == SH_MORE;
    if (f->pty || !f->more) {
        prompt(f);
    }
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
        s_shell->cancel(f->sh);
        prompt(f);
        return 0;
    case 4:
        if (f->n == 0) {
            moy_ssh_face_out(f, "\n", 1);
            finish(f, s_shell->status(f->sh));
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
    for (size_t i = 0; i < n; i++) {
        if (f->pty ? key(f, p[i]) : plain(f, p[i])) {
            return 1;
        }
    }
    return 0;
}

static void step(moy_ssh_face_t *f) {
    if (!f->started) {
        f->started = 1;
        moy_ssh_mark_shell();
        f->sh = s_shell != NULL ? s_shell->open(f) : NULL;
        if (f->sh == NULL) {
            moy_ssh_face_out(f, "moybyte: no shell\n", 18);
            finish(f, 1);
            return;
        }
        if (f->cmd != NULL) {
            size_t n = strlen(f->cmd);
            int r = s_shell->run(f->sh, f->cmd, n);
            if (r == SH_MORE) {
                // an unfinished command (an open quote): ended as sh -c would
                r = s_shell->run(f->sh, "\n", 1);
            }
            finish(f, s_shell->status(f->sh));
            return;
        }
        prompt(f);
    }
    if (f->ahead_n) {
        char a[AHEAD_BYTES];
        size_t n = f->ahead_n;
        memcpy(a, f->ahead, n);
        f->ahead_n = 0;
        if (feed(f, a, n)) {
            return;
        }
    }
    if (f->interrupt) {
        f->interrupt = 0;
        if (key(f, 3)) {
            return;
        }
    }
    char b[256];
    ssize_t n = recv(f->fd, b, sizeof b, MSG_DONTWAIT);
    if (n == 0) {
        finish(f, s_shell->status(f->sh));
        return;
    }
    if (n < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
            finish(f, 255);
        }
        return;
    }
    feed(f, b, (size_t)n);
}

void moy_ssh_face_tail(void) {
    moy_ssh_face_t *f = __atomic_load_n(&s_face, __ATOMIC_ACQUIRE);
    if (f != NULL && !__atomic_load_n(&f->done, __ATOMIC_ACQUIRE)) {
        step(f);
    }
}

// -- Dropbear's side (port/moy_ssh_port.h) -------------------------------------

static void face_free(moy_ssh_face_t *f) {
    moy_ssh_free(f->cmd);
    moy_ssh_free(f);
}

int moy_ssh_command(const char *cmd, int pty, int cols, int rows, int *fd, pid_t *pid) {
    moy_ssh_face_t *old = __atomic_load_n(&s_face, __ATOMIC_ACQUIRE);
    if (old != NULL) {
        if (!old->reaped || !__atomic_load_n(&old->done, __ATOMIC_ACQUIRE)) {
            return -1;
        }
        __atomic_store_n(&s_face, NULL, __ATOMIC_RELEASE);
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
    f->pid = 100 + s_next_pid++;
    *fd = fds[0];
    *pid = f->pid;
    __atomic_store_n(&s_face, f, __ATOMIC_RELEASE);
    return 0;
}

int moy_ssh_reap(pid_t *pid, int *status) {
    moy_ssh_face_t *f = __atomic_load_n(&s_face, __ATOMIC_ACQUIRE);
    if (f == NULL || f->reaped || !__atomic_load_n(&f->exited, __ATOMIC_ACQUIRE)) {
        return 0;
    }
    // freed once the stepping task has let go of it (done): by the next
    // command or after the session (moy_ssh_face_reap_all)
    f->reaped = 1;
    *pid = f->pid;
    *status = f->status;
    return 1;
}

int moy_ssh_signal(pid_t pid, int sig) {
    moy_ssh_face_t *f = __atomic_load_n(&s_face, __ATOMIC_ACQUIRE);
    if (f == NULL || f->pid != pid || sig != SIGINT) {
        return -1;
    }
    f->interrupt = 1;
    return 0;
}

void moy_ssh_winch(pid_t pid, int cols, int rows) {
    moy_ssh_face_t *f = __atomic_load_n(&s_face, __ATOMIC_ACQUIRE);
    if (f != NULL && f->pid == pid) {
        f->cols = cols;
        f->rows = rows;
    }
}

void moy_ssh_face_reap_all(int wait_ms) {
    for (int t = 0; t < wait_ms; t += 10) {
        moy_ssh_face_t *f = __atomic_load_n(&s_face, __ATOMIC_ACQUIRE);
        if (f == NULL) {
            return;
        }
        if (__atomic_load_n(&f->done, __ATOMIC_ACQUIRE)) {
            __atomic_store_n(&s_face, NULL, __ATOMIC_RELEASE);
            face_free(f);
            return;
        }
        usleep(10000);
    }
}
