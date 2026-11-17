// moy_os_net: the OS table's socket rows (moy_os.h).
#include "moy_os_int.h"

// -- the rows: sockets ----------------------------------------------------------------

int moy_os_sock_slot(moy_os_proc_t *p, int fd) {
    int k = fd - MOY_OS_SOCK_FD;
    if (k < 0 || k >= (int)MOY_OS_HANDLES || p->h[k].kind != H_SOCK || p->h[k].closing) {
        return -1;
    }
    return k;
}

static void net_body(void *arg) {
    moy_os_proc_t *p = arg;
    if (!p->net_held) {
        if (moy_os_g_net != NULL) {
            moy_os_g_net(moy_os_g_net_ctx, p->pid, 1);
        }
        p->net_held = 1;
    }
}

// The process may use the network, and holds it: 0, or an errno value.
static int net_need(thr_t *t) {
    if (t == NULL) {
        return EPERM;
    }
    moy_os_proc_t *p = t->p;
    if (!(p->flags & MOY_OS_NET) || p->s == NULL) {
        return EACCES;
    }
    if (!p->net_held && moy_os_on_session(t, net_body, p) != 0) {
        return EINTR;
    }
    return 0;
}

// The network's link up, waited for MOY_OS_LINK_US at most: 0, or an errno.
static int link_wait(thr_t *t) {
#ifdef MOY_ON_BOARD
    if (moy_net_link == NULL) {
        return 0;
    }
    int64_t end = moy_now_us() + MOY_OS_LINK_US;
    uint32_t ip;
    while (!moy_net_link(&ip)) {
        if (moy_now_us() >= end) {
            return ENETUNREACH;
        }
        if (moy_os_doze(t, MOY_OS_SLICE_US) != 0) {
            return EINTR;
        }
    }
#else
    (void)t;
#endif
    return 0;
}

// For each of `n` stack sockets lfd[i], what of POLLIN and POLLOUT in want[i]
// is ready, and POLLERR, after waiting `us` at most for any: how many are.
static int sk_ready(const int *lfd, const short *want, short *got, int n, int64_t us) {
#ifdef MOY_ON_BOARD
    fd_set rs, ws, es;
    FD_ZERO(&rs);
    FD_ZERO(&ws);
    FD_ZERO(&es);
    int top = -1;
    for (int i = 0; i < n; i++) {
        if (want[i] & POLLIN) {
            FD_SET(lfd[i], &rs);
        }
        if (want[i] & POLLOUT) {
            FD_SET(lfd[i], &ws);
        }
        FD_SET(lfd[i], &es);
        top = lfd[i] > top ? lfd[i] : top;
    }
    struct timeval tv = { (time_t)(us / 1000000), (suseconds_t)(us % 1000000) };
    int r = lwip_select(top + 1, &rs, &ws, &es, &tv);
    int ready = 0;
    for (int i = 0; i < n; i++) {
        got[i] = 0;
        if (r > 0) {
            got[i] = (short)((FD_ISSET(lfd[i], &rs) ? POLLIN : 0)
                             | (FD_ISSET(lfd[i], &ws) ? POLLOUT : 0)
                             | (FD_ISSET(lfd[i], &es) ? POLLERR : 0));
        }
        ready += got[i] != 0;
    }
    return ready;
#else
    struct pollfd fds[MOY_OS_HANDLES];
    for (int i = 0; i < n; i++) {
        fds[i].fd = lfd[i];
        fds[i].events = want[i];
        fds[i].revents = 0;
    }
    int r = poll(fds, (nfds_t)n, (int)((us + 999) / 1000));
    int ready = 0;
    for (int i = 0; i < n; i++) {
        got[i] = r > 0 ? fds[i].revents : 0;
        ready += got[i] != 0;
    }
    return ready;
#endif
}

// Wait for socket `slot` to be readable (or writable): 0, EAGAIN at `ms`
// (0: no limit), EINTR when the process ends.
static int sk_block(thr_t *t, const handle_t *h, bool wr, uint32_t ms) {
    int64_t end = ms ? moy_now_us() + (int64_t)ms * 1000 : 0;
    int lfd = h->fd;
    for (;;) {
        if (moy_os_ending(t)) {
            return EINTR;
        }
        if (h->closing) {
            return EBADF;
        }
        int64_t slice = MOY_OS_SLICE_US;
        if (end) {
            int64_t left = end - moy_now_us();
            if (left <= 0) {
                return EAGAIN;
            }
            slice = left < slice ? left : slice;
        }
        short want = wr ? POLLOUT : POLLIN, got = 0;
        if (sk_ready(&lfd, &want, &got, 1, slice) > 0) {
            return 0;
        }
    }
}

static bool would_block(int e) {
    return e == EAGAIN || e == EWOULDBLOCK || e == EINPROGRESS;
}

// A stack socket made non-blocking and given a handle: its descriptor, or -1.
static int sock_adopt(thr_t *t, int lfd, uint32_t flags) {
#ifdef MOY_ON_BOARD
    lwip_fcntl(lfd, F_SETFL, O_NONBLOCK);
#else
    fcntl(lfd, F_SETFL, fcntl(lfd, F_GETFL, 0) | O_NONBLOCK);
#endif
    moy_os_lock();
    int h = moy_os_handle_new(t->p, H_SOCK, NULL, lfd);
    if (h >= 0) {
        t->p->h[h].kinds = flags;
        t->p->h[h].rcv_ms = t->p->h[h].snd_ms = 0;
    }
    moy_os_unlock();
    if (h < 0) {
        SK(close)(lfd);
        moy_os_errno_set(EMFILE);
        return -1;
    }
    return MOY_OS_SOCK_FD + h;
}

// recv or send on `slot`, as its blocking mode and `flags` say.
long moy_os_sock_io(thr_t *t, int slot, void *buf, size_t n, int flags, bool wr) {
    handle_t *h = &t->p->h[slot];
    bool block = !(h->kinds & SK_NONBLOCK) && !(flags & MSG_DONTWAIT);
    for (;;) {
        long r = wr ? (long)SK(send)(h->fd, buf, n, flags | MSG_DONTWAIT)
                    : (long)SK(recv)(h->fd, buf, n, flags | MSG_DONTWAIT);
        if (r >= 0) {
            return r;
        }
        int e = errno;
        if (!block || !would_block(e)) {
            moy_os_errno_set(e);
            return -1;
        }
        if ((e = sk_block(t, h, wr, wr ? h->snd_ms : h->rcv_ms)) != 0) {
            moy_os_errno_set(e);
            return -1;
        }
    }
}

// The socket row's prologue: the slot of `fd` in the calling thread's
// process, held for the row (sock_let) so a close by another thread waits for
// it to finish; or -1 with errno set.
int moy_os_sock_of(thr_t *t, int fd) {
    int k = -1;
    if (t != NULL) {
        moy_os_lock();
        k = moy_os_sock_slot(t->p, fd);
        if (k >= 0) {
            t->p->h[k].users++;
        }
        moy_os_unlock();
    }
    if (k < 0) {
        moy_os_errno_set(t == NULL ? EPERM : EBADF);
    }
    return k;
}

// A socket row's hold let go: a socket closed meanwhile is closed now, by
// the last row that held it.
void moy_os_sock_let(thr_t *t, int k) {
    if (k < 0) {
        return;
    }
    moy_os_lock();
    handle_t *h = &t->p->h[k];
    if (--h->users == 0 && h->closing) {
        moy_os_handle_drop(t->p, (uint32_t)k);
    }
    moy_os_unlock();
}

// `fd`'s close: at once, or, while a row of another thread uses it, shut down
// (which ends that row's wait) and closed by the row's end.
void moy_os_sock_close(moy_os_proc_t *p, int k) {
    handle_t *h = &p->h[k];
    if (h->users == 0) {
        moy_os_handle_drop(p, (uint32_t)k);
    } else {
        h->closing = 1;
        SK(shutdown)(h->fd, SHUT_RDWR);
    }
}

int moy_os_socket(int domain, int type, int protocol) {
    thr_t *t = moy_os_enter();
    int r = -1, e = net_need(t);
    uint32_t flags = 0;
#ifdef SOCK_NONBLOCK
    if (type & SOCK_NONBLOCK) {
        flags |= SK_NONBLOCK;
    }
    type &= ~(SOCK_NONBLOCK | SOCK_CLOEXEC);
#endif
    if (e != 0) {
        moy_os_errno_set(e);
    } else {
        int lfd = SK(socket)(domain, type, protocol);
        if (lfd < 0) {
            moy_os_errno_set(errno);
        } else {
            r = sock_adopt(t, lfd, flags);
        }
    }
    moy_os_leave(t);
    return r;
}

// A call on the stack's socket that answers at once: its answer, errno set.
#define SOCK_CALL(fd, call)                         \
    thr_t *t = moy_os_enter();                             \
    int k = moy_os_sock_of(t, fd), r = -1;                 \
    if (k >= 0) {                                   \
        int lfd = t->p->h[k].fd;                    \
        (void)lfd;                                  \
        r = (call);                                 \
        if (r < 0) {                                \
            moy_os_errno_set(errno);                \
        }                                           \
    }                                               \
    moy_os_sock_let(t, k);                                 \
    moy_os_leave(t);                                       \
    return r

int moy_os_bind(int fd, const struct sockaddr *addr, socklen_t len) {
    SOCK_CALL(fd, SK(bind)(lfd, addr, len));
}

int moy_os_listen(int fd, int backlog) {
    SOCK_CALL(fd, SK(listen)(lfd, backlog));
}

int moy_os_shutdown(int fd, int how) {
    SOCK_CALL(fd, SK(shutdown)(lfd, how));
}

// A length a row reads through a pointer, read ONCE: the row's check passed
// the buffer by it, and another thread may change it after. The buffer is
// checked again by the value read (u_fits), and the stack is handed a copy.
static socklen_t len_in(const socklen_t *len) {
    return len != NULL ? *(const volatile socklen_t *)len : 0;
}

// [p, p+n) writable by the calling thread's process (always, off U-mode).
static bool u_fits(thr_t *t, void *ptr, size_t n) {
#ifdef MOY_OS_UMODE
    if (t != NULL && t->u != NULL && ptr != NULL) {
        return moy_u_ok(t->u, ptr, n, MOY_U_W) != 0;
    }
#endif
    (void)t;
    (void)ptr;
    (void)n;
    return true;
}

// getsockname, getpeername and getsockopt over a copy of the length.
static int sock_out(int fd, int op, int level, int name, void *out, socklen_t *len) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd), r = -1;
    if (k >= 0) {
        socklen_t n = len_in(len);
        if (len == NULL || out == NULL || !u_fits(t, out, n)) {
            errno = EFAULT;
        } else {
            int lfd = t->p->h[k].fd;
            r = op == 0 ? SK(getsockname)(lfd, out, &n)
                : op == 1 ? SK(getpeername)(lfd, out, &n)
                : SK(getsockopt)(lfd, level, name, out, &n);
            if (r == 0) {
                *len = n;
            }
        }
        if (r < 0) {
            moy_os_errno_set(errno);
        }
    }
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return r;
}

int moy_os_getsockname(int fd, struct sockaddr *addr, socklen_t *len) {
    return sock_out(fd, 0, 0, 0, addr, len);
}

int moy_os_getpeername(int fd, struct sockaddr *addr, socklen_t *len) {
    return sock_out(fd, 1, 0, 0, addr, len);
}

int moy_os_getsockopt(int fd, int level, int name, void *val, socklen_t *len) {
    return sock_out(fd, 2, level, name, val, len);
}

int moy_os_setsockopt(int fd, int level, int name, const void *val, socklen_t len) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd), r = -1;
    if (k >= 0) {
        if (level == SOL_SOCKET && (name == SO_RCVTIMEO || name == SO_SNDTIMEO)) {
            // The OS's wait keeps them: underneath, the socket never blocks.
            if (val == NULL || len < (socklen_t)sizeof(struct timeval)) {
                moy_os_errno_set(EINVAL);
            } else {
                const struct timeval *tv = val;
                uint32_t ms = (uint32_t)(tv->tv_sec * 1000 + (tv->tv_usec + 999) / 1000);
                *(name == SO_RCVTIMEO ? &t->p->h[k].rcv_ms : &t->p->h[k].snd_ms) = ms;
                r = 0;
            }
        } else {
            r = SK(setsockopt)(t->p->h[k].fd, level, name, val, len);
            if (r < 0) {
                moy_os_errno_set(errno);
            }
        }
    }
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return r;
}

int moy_os_accept(int fd, struct sockaddr *addr, socklen_t *len) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd), r = -1;
    socklen_t n = len_in(len);
    bool named = addr != NULL && len != NULL;
    if (k >= 0 && named && !u_fits(t, addr, n)) {
        moy_os_errno_set(EFAULT);
        moy_os_sock_let(t, k);
        k = -1;
    }
    int held = k;
    while (k >= 0) {
        handle_t *h = &t->p->h[k];
        int lfd = SK(accept)(h->fd, named ? addr : NULL, named ? &n : NULL);
        if (lfd >= 0) {
            if (named) {
                *len = n;
            }
            r = sock_adopt(t, lfd, 0);
            break;
        }
        int e = errno;
        if ((h->kinds & SK_NONBLOCK) || !would_block(e)
            || (e = sk_block(t, h, false, h->rcv_ms)) != 0) {
            moy_os_errno_set(e);
            break;
        }
    }
    moy_os_sock_let(t, held);
    moy_os_leave(t);
    return r;
}

int moy_os_connect(int fd, const struct sockaddr *addr, socklen_t len) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd), r = -1, e;
    if (k >= 0 && (e = link_wait(t)) != 0) {
        moy_os_errno_set(e);
    } else if (k >= 0) {
        handle_t *h = &t->p->h[k];
        r = SK(connect)(h->fd, addr, len);
        e = r < 0 ? errno : 0;
        if (r < 0 && would_block(e) && !(h->kinds & SK_NONBLOCK)) {
            e = sk_block(t, h, true, h->snd_ms);
            if (e == 0) {
                int err = 0;
                socklen_t n = sizeof err;
                SK(getsockopt)(h->fd, SOL_SOCKET, SO_ERROR, &err, &n);
                e = err;
            } else if (e == EAGAIN) {
                e = ETIMEDOUT;
            }
            r = e == 0 ? 0 : -1;
        } else if (r < 0 && e == EAGAIN) {
            e = EINPROGRESS;
        }
        if (r < 0) {
            moy_os_errno_set(e);
        }
    }
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return r;
}

ssize_t moy_os_send(int fd, const void *buf, size_t n, int flags) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd);
    long r = k >= 0 ? moy_os_sock_io(t, k, (void *)buf, n, flags, true) : -1;
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_recv(int fd, void *buf, size_t n, int flags) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd);
    long r = k >= 0 ? moy_os_sock_io(t, k, buf, n, flags, false) : -1;
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_sendto(int fd, const void *buf, size_t n, int flags, const struct sockaddr *to,
                      socklen_t len) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd), e;
    long r = -1;
    if (k >= 0 && to != NULL && (e = link_wait(t)) != 0) {
        moy_os_errno_set(e);
    } else if (k >= 0) {
        handle_t *h = &t->p->h[k];
        bool block = !(h->kinds & SK_NONBLOCK) && !(flags & MSG_DONTWAIT);
        for (;;) {
            r = (long)SK(sendto)(h->fd, buf, n, flags | MSG_DONTWAIT, to, len);
            if (r >= 0) {
                break;
            }
            e = errno;
            if (!block || !would_block(e) || (e = sk_block(t, h, true, h->snd_ms)) != 0) {
                moy_os_errno_set(e);
                break;
            }
        }
    }
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_recvfrom(int fd, void *buf, size_t n, int flags, struct sockaddr *from,
                        socklen_t *len) {
    thr_t *t = moy_os_enter();
    int k = moy_os_sock_of(t, fd), e;
    long r = -1;
    socklen_t fl = len_in(len);
    bool named = from != NULL && len != NULL;
    if (k >= 0 && named && !u_fits(t, from, fl)) {
        moy_os_errno_set(EFAULT);
    } else if (k >= 0) {
        handle_t *h = &t->p->h[k];
        bool block = !(h->kinds & SK_NONBLOCK) && !(flags & MSG_DONTWAIT);
        for (;;) {
            r = (long)SK(recvfrom)(h->fd, buf, n, flags | MSG_DONTWAIT, named ? from : NULL,
                                   named ? &fl : NULL);
            if (r >= 0) {
                if (named) {
                    *len = fl;
                }
                break;
            }
            e = errno;
            if (!block || !would_block(e) || (e = sk_block(t, h, false, h->rcv_ms)) != 0) {
                moy_os_errno_set(e);
                break;
            }
        }
    }
    moy_os_sock_let(t, k);
    moy_os_leave(t);
    return (ssize_t)r;
}

#ifdef MOY_OS_UMODE
// The stack's answer copied into a mapping of the U-mode process's own.
static struct addrinfo *u_addrinfo(moy_os_proc_t *p, const struct addrinfo *ai) {
    size_t n = 0;
    for (const struct addrinfo *a = ai; a != NULL; a = a->ai_next) {
        n += sizeof *a + ((a->ai_addrlen + 3u) & ~3u)
             + (a->ai_canonname != NULL ? strlen(a->ai_canonname) + 1 : 0);
    }
    uint8_t *at = n != 0 ? moy_os_u_map(p, n) : NULL;
    struct addrinfo *out = (struct addrinfo *)at, *prev = NULL;
    for (const struct addrinfo *a = ai; at != NULL && a != NULL; a = a->ai_next) {
        struct addrinfo *c = (struct addrinfo *)at;
        *c = *a;
        c->ai_next = NULL;
        at += sizeof *c;
        if (a->ai_addr != NULL) {
            memcpy(at, a->ai_addr, a->ai_addrlen);
            c->ai_addr = (struct sockaddr *)at;
            at += (a->ai_addrlen + 3u) & ~3u;
        }
        if (a->ai_canonname != NULL) {
            strcpy((char *)at, a->ai_canonname);
            c->ai_canonname = (char *)at;
            at += strlen(a->ai_canonname) + 1;
        }
        if (prev != NULL) {
            prev->ai_next = c;
        }
        prev = c;
    }
    return out;
}
#endif

int moy_os_getaddrinfo(const char *node, const char *service, const struct addrinfo *hints,
                       struct addrinfo **res) {
    thr_t *t = moy_os_enter();
    int r = EAI_FAIL, e = net_need(t);
    bool numeric = node == NULL || (hints != NULL && (hints->ai_flags & AI_NUMERICHOST));
    if (e == 0 && !numeric) {
        e = link_wait(t);
    }
    if (e != 0) {
        moy_os_errno_set(e);
    } else {
        // The answer stays in the kernel until it is the process's own: *res
        // is the module's memory, which another of its threads may write.
        struct addrinfo *got = NULL;
        bool own = true;
        char node_in[256], service_in[32];
        size_t nn = node != NULL ? moy_os_text_len(t, node, sizeof node_in) : 0;
        size_t sn = service != NULL ? moy_os_text_len(t, service, sizeof service_in) : 0;
        if (nn >= sizeof node_in || sn >= sizeof service_in) {
            r = EAI_NONAME;
        } else {
            if (node != NULL) {
                memcpy(node_in, node, nn);
                node_in[nn] = 0;
            }
            if (service != NULL) {
                memcpy(service_in, service, sn);
                service_in[sn] = 0;
            }
            r = SK(getaddrinfo)(node != NULL ? node_in : NULL, service != NULL ? service_in : NULL,
                                hints, &got);
        }
#ifdef MOY_OS_UMODE
        if (r == 0 && t->p->u != NULL) {
            struct addrinfo *u = u_addrinfo(t->p, got);
            SK(freeaddrinfo)(got);
            got = u;
            own = false;
            r = u != NULL ? 0 : EAI_MEMORY;
        }
#endif
        if (r == 0) {
            moy_os_lock();
            int h = moy_os_handle_new(t->p, H_ADDR, got, -1);
            moy_os_unlock();
            if (h < 0) {
#ifdef MOY_OS_UMODE
                if (!own) {
                    moy_os_u_unmap(t->p, got, false);
                } else
#endif
                SK(freeaddrinfo)(got);
                got = NULL;
                r = EAI_MEMORY;
            }
        }
        (void)own;
        *res = got;
    }
    moy_os_leave(t);
    return r;
}

void moy_os_freeaddrinfo(struct addrinfo *ai) {
    thr_t *t = moy_os_enter();
    if (t != NULL && ai != NULL) {
        moy_os_lock();
        for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
            if (t->p->h[i].kind == H_ADDR && t->p->h[i].ptr == ai) {
                moy_os_handle_drop(t->p, i);
                break;
            }
        }
        moy_os_unlock();
    }
    moy_os_leave(t);
}

// poll over any of the process's descriptors: a socket as the stack says, a
// file or stream of the session at once (its reads and writes never wait).
static int poll_in(thr_t *t, struct pollfd *fds, nfds_t n, int timeout_ms) {
    moy_os_proc_t *p = t->p;
    int64_t end = timeout_ms < 0 ? 0 : moy_now_us() + (int64_t)timeout_ms * 1000;
    int lfd[MOY_OS_HANDLES];
    short want[MOY_OS_HANDLES], got[MOY_OS_HANDLES];
    nfds_t at[MOY_OS_HANDLES];
    int held[MOY_OS_HANDLES];
    for (;;) {
        int ready = 0, m = 0;
        bool evq = false;
        for (nfds_t i = 0; i < n; i++) {
            struct pollfd *f = &fds[i];
            f->revents = 0;
            if (f->fd < 0) {
                continue;
            }
            int k = f->fd >= MOY_OS_SOCK_FD && m < (int)MOY_OS_HANDLES ? moy_os_sock_of(t, f->fd) : -1;
            if (f->fd == MOY_EVQ_FD) {
                if (p->hup) {
                    f->revents = (short)(POLLHUP | (f->events & POLLIN));
                } else if (!moy_evq_has(p->pid)) {
                    f->revents = POLLNVAL;
                } else if (moy_evq_queued(p->pid) > 0) {
                    f->revents = (short)(f->events & POLLIN);
                }
                evq |= f->revents == 0;
            } else if (k >= 0) {
                held[m] = k;
                lfd[m] = p->h[k].fd;
                want[m] = (short)(f->events & (POLLIN | POLLOUT));
                at[m++] = i;
            } else if (f->fd >= MOY_OS_SOCK_FD && moy_os_sock_slot(p, f->fd) >= 0) {
                f->revents = 0;         // past the sockets one poll takes: not looked at
            } else if ((f->fd >= 0 && f->fd <= 2) || moy_os_fd_owned(p, f->fd) >= 0) {
                f->revents = (short)(f->events & (POLLIN | POLLOUT));
            } else {
                f->revents = POLLNVAL;
            }
            ready += f->revents != 0;
        }
        int64_t slice = 0;
        if (ready == 0) {
            slice = MOY_OS_SLICE_US;
            if (end) {
                int64_t left = end - moy_now_us();
                slice = left <= 0 ? 0 : left < slice ? left : slice;
            }
        }
        bool any = m > 0 && sk_ready(lfd, want, got, m, slice) > 0;
        for (int j = 0; j < m; j++) {
            if (any) {
                fds[at[j]].revents = (short)(got[j] & (fds[at[j]].events | POLLERR | POLLHUP));
                ready += fds[at[j]].revents != 0;
            }
            moy_os_sock_let(t, held[j]);
        }
        if (m == 0 && slice > 0 && evq) {
            // The one wait: woken by an event, a stop or the slice's end.
            moy_evq_wait(p->pid, (uint32_t)((slice + 999) / 1000));
        } else if (m == 0 && slice > 0 && moy_os_doze(t, slice) != 0) {
            return -EINTR;
        }
        if (ready > 0 || (end && moy_now_us() >= end) || timeout_ms == 0) {
            return ready;
        }
        if (moy_os_ending(t)) {
            return -EINTR;
        }
    }
}

// A poll of `p`'s event descriptor: the wake that ends here charged to the
// supervisor (unless the watchdog struck it while it ran, moy_os_reap), and
// while the supervisor holds the process suspended, no wake served. The
// next wake is the queue's STEP (moy_evq.h, IN TURN).
static int wake_end(thr_t *t) {
    moy_os_proc_t *p = t->p;
    moy_os_lock();
    p->polling = 1;
    int64_t now = moy_now_us();
    int64_t cost = p->woke_us != 0 ? now - p->woke_us : 0;
    bool struck = p->struck != 0;
    p->struck = 0;
    if (cost > 0) {
        p->cpu_us += (uint32_t)cost;
    }
    moy_os_unlock();
    if (!moy_evq_has(p->pid)) {
        return 0;
    }
    if (p->woke_us != 0 && !struck) {
        moy_super_charge(p->pid, (uint32_t)(cost > 0 ? cost : 0), false);
    }
    while (moy_super_held(p->pid) && !p->hup) {
        if (moy_os_doze(t, MOY_OS_SLICE_US) != 0) {
            return EINTR;
        }
    }
    moy_evq_step(p->pid);               // the next wake reads MOY_EVQ_BATCH at most
    return 0;
}

static bool polls_evq(const struct pollfd *fds, nfds_t n) {
    for (nfds_t i = 0; i < n; i++) {
        if (fds[i].fd == MOY_EVQ_FD) {
            return true;
        }
    }
    return false;
}

int moy_os_poll(struct pollfd *fds, nfds_t n, int timeout) {
    thr_t *t = moy_os_enter();
    int r = -1;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else {
        bool evq = polls_evq(fds, n);
        r = evq ? -wake_end(t) : 0;
        if (r == 0) {
            r = poll_in(t, fds, n, timeout);
        }
        if (evq) {
            moy_os_lock();
            t->p->wakes += r >= 0;
            t->p->woke_us = moy_now_us();
            t->p->polling = 0;
            moy_os_unlock();
        }
        if (r < 0) {
            moy_os_errno_set(-r);
            r = -1;
        }
    }
    moy_os_leave(t);
    return r;
}

int moy_os_select(int n, fd_set *rs, fd_set *ws, fd_set *es, struct timeval *tv) {
    thr_t *t = moy_os_enter();
    int r = -1;
    struct pollfd fds[FD_SETSIZE];
    nfds_t m = 0;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (n < 0 || n > FD_SETSIZE) {
        moy_os_errno_set(EINVAL);
    } else {
        for (int fd = 0; fd < n; fd++) {
            short ev = (short)((rs != NULL && FD_ISSET(fd, rs) ? POLLIN : 0)
                               | (ws != NULL && FD_ISSET(fd, ws) ? POLLOUT : 0));
            if (ev || (es != NULL && FD_ISSET(fd, es))) {
                fds[m].fd = fd;
                fds[m].events = ev;
                m++;
            }
        }
        int ms = tv == NULL ? -1 : (int)(tv->tv_sec * 1000 + (tv->tv_usec + 999) / 1000);
        r = poll_in(t, fds, m, ms);
        if (r < 0) {
            moy_os_errno_set(-r);
            r = -1;
        } else {
            r = 0;
            for (nfds_t i = 0; i < m; i++) {
                int fd = fds[i].fd;
                short ev = fds[i].revents;
                if (ev & POLLNVAL) {
                    moy_os_errno_set(EBADF);
                    r = -1;
                    break;
                }
                if (rs != NULL && FD_ISSET(fd, rs) && !(ev & (POLLIN | POLLERR | POLLHUP))) {
                    FD_CLR(fd, rs);
                }
                if (ws != NULL && FD_ISSET(fd, ws) && !(ev & (POLLOUT | POLLERR))) {
                    FD_CLR(fd, ws);
                }
                if (es != NULL && FD_ISSET(fd, es)) {
                    FD_CLR(fd, es);
                }
                r += (rs != NULL && FD_ISSET(fd, rs)) + (ws != NULL && FD_ISSET(fd, ws));
            }
        }
    }
    moy_os_leave(t);
    return r;
}

int moy_os_fcntl(int fd, int cmd, int arg) {
    thr_t *t = moy_os_enter();
    int r = -1;
    int k = t != NULL ? moy_os_sock_slot(t->p, fd) : -1;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (k < 0 && !(fd >= 0 && fd <= 2) && moy_os_fd_owned(t->p, fd) < 0) {
        moy_os_errno_set(EBADF);
    } else if (cmd == F_GETFD || cmd == F_SETFD) {
        r = 0;
    } else if (cmd == F_GETFL) {
        r = O_RDWR | (k >= 0 && (t->p->h[k].kinds & SK_NONBLOCK) ? O_NONBLOCK : 0);
    } else if (cmd == F_SETFL) {
        if (k >= 0) {
            t->p->h[k].kinds = (arg & O_NONBLOCK) ? (t->p->h[k].kinds | SK_NONBLOCK)
                                                  : (t->p->h[k].kinds & ~SK_NONBLOCK);
        }
        r = 0;
    } else {
        moy_os_errno_set(EINVAL);
    }
    moy_os_leave(t);
    return r;
}

void moy_os_set_net(moy_os_net_fn net, void *ctx) {
    moy_os_g_net_ctx = ctx;
    moy_os_g_net = net;
}
