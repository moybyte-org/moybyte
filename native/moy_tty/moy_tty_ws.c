// moy_tty_ws: the web terminal's bridge (README.md, The web terminal): the
// WebSocket the webhost upgraded (GET /term) on one side, a stream face on
// the other.
//
// The webhost answers the upgrade on the console's task and hands the socket
// here (moy_web_term); from then on the console's task has let go of it, so
// the frame never carries the session. A bridge task opens a face as SSH
// does (moy_tty_open with a terminal) and moves bytes between the
// WebSocket's messages and the face's end of the pair:
//
//   page -> board   binary: keys, as a terminal sends them
//                   text:   {"size":[COLS,ROWS]}, the terminal's new size
//   board -> page   binary: the face's bytes, what the pair holds when the
//                           first arrives (the face gathers its own output,
//                           moy_tty.c's OUT_BYTES; waiting here for more costs
//                           a scheduler tick, 10 ms on a board)
//                   close:  1000 "exit N" when the shell ends; 1013 "busy"
//                           when another transport's face runs (one face at
//                           a time), or this bridge serves another page;
//                           1008 "seal" for a frame whose seal does not
//                           open, "revoked" when the page's key was dropped
//
// A SEALED SESSION (the webhost's, moy_seal.h) carries every message as a
// binary frame of AES-GCM ciphertext and its tag: up under the session's kc,
// nonce (3, i, seq), down under kb, (4, i, seq), i counting each way from 0
// and seq the upgrade's own sequence number, so a frame dropped, replayed or
// moved ends the session. Up, the plaintext's first byte says what it is:
// 'k' keys, 's' the size's JSON. The close frames stay plain: they say why,
// and the page acts on none of them as a credential. The open host (no
// sign-in: a test, the dev server) speaks the plain messages above.
//
// The subprotocol is `moy.sh` (echoed when the page asks for it): the board
// link's channels (docs/os_2026-11/web_terminal_spike.md section 7) are a
// route and a subprotocol of their own, and this stream is the shell
// channel's payload as it will be there.

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#ifdef ESP_PLATFORM
#include "lwip/sockets.h"
#else
#include <netinet/in.h>
#include <netinet/tcp.h>
#endif

#include "moy_tty.h"
#include "moy_tty_int.h"
#include "moy_webauth.h"

#define BRIDGE_STACK (6 * 1024)
#define IN_CAP 2048                     // a page's frames waiting to be whole
#define OUT_CAP 2048                    // the face's bytes gathered into one frame
#define OUT_ALLOC (4 + OUT_CAP + MOY_SEAL_TAG)
#define SEND_MS 5000                    // a page that takes nothing for this long is gone

extern const moy_tty_shell_t moy_tty_moy_sh;

typedef struct {
    volatile int fd;                    // the upgraded socket served or waiting, or -1
    int cols, rows;
    moy_wa_seal_t seal;                 // the session's, `on` 0 for the open host
    uint32_t up, down;                  // the next frame's index each way
    volatile int cut;                   // the session's key was dropped
    uintptr_t task;
    moy_tty_ws_stats_t stats;
} ws_t;

static ws_t *W;

static int send_all(int fd, const void *p, size_t n) {
    const char *c = p;
    while (n > 0) {
        ssize_t w = send(fd, c, n, 0);
        if (w < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        c += w;
        n -= (size_t)w;
    }
    return 0;
}

// One unmasked frame, header and payload in one send.
static int frame_out(int fd, int op, const char *p, size_t n, char *buf) {
    size_t hn = 2;
    buf[0] = (char)(0x80 | op);
    if (n < 126) {
        buf[1] = (char)n;
    } else {
        buf[1] = 126;
        buf[2] = (char)(n >> 8);
        buf[3] = (char)n;
        hn = 4;
    }
    if (p != buf + hn) {
        memmove(buf + hn, p, n);
    }
    W->stats.frames_out++;
    return send_all(fd, buf, hn + n);
}

static void close_out(int fd, int code, const char *why) {
    char b[64];
    size_t n = strlen(why);
    n = n < sizeof b - 8 ? n : sizeof b - 8;
    char p[64];
    p[0] = (char)(code >> 8);
    p[1] = (char)code;
    memcpy(p + 2, why, n);
    frame_out(fd, 8, p, n + 2, b);
}

static void resize(pid_t pid, const uint8_t *p, size_t n) {
    char t[48];
    if (n >= sizeof t) {
        return;
    }
    memcpy(t, p, n);
    t[n] = 0;
    const char *s = strstr(t, "\"size\"");
    int cols = 0, rows = 0;
    if (s != NULL && sscanf(s, "\"size\" : [ %d , %d ]", &cols, &rows) == 2 && cols > 0
            && rows > 0 && cols < 1000 && rows < 1000) {
        moy_tty_winch(pid, cols, rows);
    }
}

// Whole frames out of in[0..*n): keys to the face. -1 at a close or an error.
static int frames_in(int ws, int sfd, pid_t pid, uint8_t *in, size_t *n, char *scratch) {
    size_t at = 0;
    for (;;) {
        if (*n - at < 2) {
            break;
        }
        uint8_t *f = in + at;
        int op = f[0] & 0x0F;
        size_t len = f[1] & 0x7F, hn = 2;
        if (len == 126) {
            if (*n - at < 4) {
                break;
            }
            len = (size_t)f[2] << 8 | f[3];
            hn = 4;
        } else if (len == 127) {
            return -1;                  // keys never come that big
        }
        int masked = f[1] & 0x80;
        if (masked) {
            hn += 4;
        }
        if (*n - at < hn + len) {
            if (hn + len > IN_CAP) {
                return -1;
            }
            break;
        }
        uint8_t *pl = f + hn;
        if (masked) {
            for (size_t i = 0; i < len; i++) {
                pl[i] ^= f[hn - 4 + (i & 3)];
            }
        }
        if (op == 8) {
            close_out(ws, 1000, "");
            return -1;
        }
        if (op == 9) {
            frame_out(ws, 10, (const char *)pl, len < 125 ? len : 125, scratch);
        } else if (W->seal.on && (op == 0 || op == 1 || op == 2)) {
            if (op != 2 || len < MOY_SEAL_TAG + 1
                || moy_seal_ws(0, W->seal.kc, MOY_SEAL_UP, W->up++, W->seal.seq, pl,
                               len - MOY_SEAL_TAG, pl + len - MOY_SEAL_TAG) != MOY_SEAL_OK) {
                close_out(ws, 1008, "seal");
                return -1;
            }
            len -= MOY_SEAL_TAG;
            if (pl[0] == 's') {
                resize(pid, pl + 1, len - 1);
            } else if (pl[0] == 'k' && len > 1) {
                W->stats.in_bytes += (uint32_t)(len - 1);
                if (send_all(sfd, pl + 1, len - 1) < 0) {
                    return -1;
                }
            }
            len += MOY_SEAL_TAG;
        } else if (op == 1) {
            resize(pid, pl, len);
        } else if ((op == 2 || op == 0) && len > 0) {
            W->stats.in_bytes += (uint32_t)len;
            if (send_all(sfd, pl, len) < 0) {
                return -1;
            }
        }
        at += hn + len;
    }
    memmove(in, in + at, *n - at);
    *n -= at;
    return 0;
}

// The face's bytes into one frame: the first piece and what else the pair
// holds now, up to OUT_CAP. 0 at the face's end.
static ssize_t gather(int sfd, char *out) {
    size_t n = 0;
    ssize_t m = recv(sfd, out, OUT_CAP, 0);
    if (m <= 0) {
        return 0;
    }
    n = (size_t)m;
    while (n < OUT_CAP) {
        m = recv(sfd, out + n, OUT_CAP - n, MSG_DONTWAIT);
        if (m <= 0) {
            break;
        }
        n += (size_t)m;
    }
    return (ssize_t)n;
}

// One page's session, to the shell's end or the page's.
static void pump(int ws, int sfd, pid_t pid, uint8_t *in, char *out) {
    size_t n = 0;
    for (;;) {
        if (W->cut) {
            close_out(ws, 1008, "revoked");
            return;
        }
        fd_set r;
        FD_ZERO(&r);
        FD_SET(ws, &r);
        FD_SET(sfd, &r);
        struct timeval tv = {0, MOY_TTY_WAIT_MS * 1000};
        int k = select((ws > sfd ? ws : sfd) + 1, &r, NULL, NULL, &tv);
        if (k < 0 && errno != EINTR) {
            return;
        }
        if (k <= 0) {
            continue;
        }
        if (FD_ISSET(sfd, &r)) {
            // the frame's header goes in front of the bytes: 4 spare
            ssize_t m = gather(sfd, out + 4);
            if (m <= 0) {
                pid_t p;
                int status = 0;
                for (int t = 0; t < 500 && !moy_tty_reap(MOY_TTY_WEB, &p, &status); t += 10) {
                    moy_tty_nap(10);
                }
                char why[16];
                snprintf(why, sizeof why, "exit %d", status);
                close_out(ws, 1000, why);
                return;
            }
            W->stats.out_bytes += (uint32_t)m;
            if (W->seal.on) {
                uint8_t *d = (uint8_t *)out + 4;
                if (moy_seal_ws(1, W->seal.kb, MOY_SEAL_DOWN, W->down++, W->seal.seq, d,
                                (size_t)m, d + m) != MOY_SEAL_OK) {
                    close_out(ws, 1008, "seal");
                    return;
                }
                m += MOY_SEAL_TAG;
            }
            if (frame_out(ws, 2, out + 4, (size_t)m, out) < 0) {
                return;
            }
        }
        if (FD_ISSET(ws, &r)) {
            ssize_t m = recv(ws, in + n, IN_CAP - n, 0);
            if (m <= 0) {
                return;
            }
            n += (size_t)m;
            if (frames_in(ws, sfd, pid, in, &n, out) < 0) {
                return;
            }
        }
    }
}

static void serve(int ws) {
    int one = 1;
    setsockopt(ws, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    struct timeval none = {0, 0}, snd = {SEND_MS / 1000, (SEND_MS % 1000) * 1000};
    setsockopt(ws, SOL_SOCKET, SO_RCVTIMEO, &none, sizeof none);
    setsockopt(ws, SOL_SOCKET, SO_SNDTIMEO, &snd, sizeof snd);
    uint8_t *in = moy_tty_calloc(1, IN_CAP);
    char *out = moy_tty_calloc(1, OUT_ALLOC);
    if (in == NULL || out == NULL) {
        moy_tty_free(in);
        moy_tty_free(out);
        return;
    }
    int sfd;
    pid_t pid;
    int r = moy_tty_open(MOY_TTY_WEB, NULL, 1, W->cols, W->rows, NULL, &sfd, &pid);
    if (r != 0) {
        W->stats.refused++;
        close_out(ws, 1013, r == MOY_TTY_EBUSY ? "busy" : "memory");
    } else {
        pump(ws, sfd, pid, in, out);
        close(sfd);
        moy_tty_reap_all(MOY_TTY_WEB, 5000);
    }
    moy_tty_free(in);
    moy_tty_free(out);
}

static void bridge_task(void *arg) {
    (void)arg;
    for (;;) {
        int ws = __atomic_load_n(&W->fd, __ATOMIC_ACQUIRE);
        if (ws < 0) {
            moy_tty_nap(MOY_TTY_FOREVER);
            continue;
        }
        W->stats.sessions++;
        W->stats.live = 1;
        serve(ws);
        shutdown(ws, SHUT_WR);
        close(ws);
        W->stats.live = 0;
        moy_tty_hold(MOY_TTY_WEB, 0);
        moy_seal_wipe(&W->seal, sizeof W->seal);
        __atomic_store_n(&W->fd, -1, __ATOMIC_RELEASE);
    }
}

int moy_web_term(int fd, int cols, int rows, const moy_wa_seal_t *seal) {
    if (moy_tty_setup() != 0) {
        return MOY_TTY_ENOMEM;
    }
    if (W == NULL) {
        W = moy_tty_calloc(1, sizeof *W);
        if (W == NULL) {
            return MOY_TTY_ENOMEM;
        }
        W->fd = -1;
    }
    if (__atomic_load_n(&W->fd, __ATOMIC_ACQUIRE) >= 0) {
        return MOY_TTY_EBUSY;
    }
    if (moy_tty_state()->shell == NULL) {
        moy_tty_set_shell(&moy_tty_moy_sh);
    }
    if (W->task == 0) {
        W->task = moy_tty_spawn(bridge_task, "moy_tty_ws", BRIDGE_STACK, NULL);
        if (W->task == 0) {
            return MOY_TTY_ENOMEM;
        }
    }
    if (moy_tty_hold(MOY_TTY_WEB, 1) != 0) {
        return MOY_TTY_ENOMEM;
    }
    W->cols = cols;
    W->rows = rows;
    if (seal != NULL) {
        W->seal = *seal;
    } else {
        memset(&W->seal, 0, sizeof W->seal);
    }
    W->up = W->down = 0;
    W->cut = 0;
    __atomic_store_n(&W->fd, fd, __ATOMIC_RELEASE);
    moy_tty_poke(W->task);
    return 0;
}

// The webhost's: the page's key was dropped, so its session ends.
void moy_web_term_cut(void) {
    if (W != NULL && __atomic_load_n(&W->fd, __ATOMIC_ACQUIRE) >= 0) {
        W->cut = 1;
        moy_tty_poke(W->task);
    }
}

void moy_tty_ws_stats(moy_tty_ws_stats_t *out) {
    if (W == NULL) {
        memset(out, 0, sizeof *out);
    } else {
        *out = W->stats;
    }
}
