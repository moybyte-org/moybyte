// moy_os_files: the OS table's file and stream rows (moy_os.h).
#include "moy_os_int.h"

// -- the rows: files and streams ----------------------------------------------------

// A socket descriptor's handle slot in `p`, or -1, and its hold for a row
// (the sockets' rows, below).
// The event descriptor's read (the rows: the event descriptor, below).



int moy_os_fd_owned(moy_os_proc_t *p, int fd) {
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        if (p->h[i].kind == H_FD && p->h[i].fd == fd) {
            return (int)i;
        }
    }
    return -1;
}

// `path` (a shell path) made absolute into out, checked against the roots: 0,
// or an errno value.
static int reach(moy_os_proc_t *p, const char *path, char *out) {
    if (path == NULL) {
        return EFAULT;
    }
    if (moy_sh_abs(p->s, path, out, MOY_SH_PROG_PATH) != 0) {
        return ENAMETOOLONG;
    }
    return moy_sh_prog_reaches(&p->prog, out) ? 0 : EACCES;
}

static int list_ent(void *arg, const char *name, int is_dir, uint32_t size) {
    dir_t *d = arg;
    (void)size;
    size_t k = strlen(name) + 2;
    char *g = moy_os_alloc(d->n + k + 1);
    if (g == NULL) {
        return ENOMEM;
    }
    if (d->names != NULL) {
        memcpy(g, d->names, d->n);
        moy_os_free(d->names);
    }
    g[d->n] = is_dir ? 'd' : 'f';
    memcpy(g + d->n + 1, name, k - 1);
    d->names = g;
    d->n += k;
    return 0;
}

static void f_body(void *arg) {
    fcall_t *c = arg;
    moy_os_proc_t *p = c->p;
    moy_sh_t *s = p->s;
    char abs[MOY_SH_PROG_PATH], abs2[MOY_SH_PROG_PATH];
    int e;
    if (!moy_sh_prog_poll(&p->prog, moy_now_us())) {
        p->ending = 1;
        c->r = -EINTR;
        return;
    }
    switch (c->op) {
    case F_OPEN: {
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
            break;
        }
        moy_sh_stat_t st;
        if (moy_sh_stat_at(s, abs, &st) == 0 && st.is_dir) {
            c->r = -EISDIR;
            break;
        }
        int acc = c->flags & O_ACCMODE;
        int f = (acc == O_RDONLY || acc == O_RDWR ? MOY_SH_O_READ : 0)
                | (acc == O_WRONLY || acc == O_RDWR ? MOY_SH_O_WRITE : 0)
                | (c->flags & O_CREAT ? MOY_SH_O_CREAT : 0)
                | (c->flags & O_TRUNC ? MOY_SH_O_TRUNC : 0)
                | (c->flags & O_EXCL ? MOY_SH_O_EXCL : 0)
                | (c->flags & O_APPEND ? MOY_SH_O_APPEND : 0);
        int fd = moy_sh_fd_open(s, abs, f);
        if (fd < 0) {
            c->r = fd;
            break;
        }
        moy_os_lock();
        int h = moy_os_handle_new(p, H_FD, NULL, fd);
        moy_os_unlock();
        if (h < 0) {
            moy_sh_fd_close(s, fd);
            c->r = -EMFILE;
            break;
        }
        c->r = fd;
        break;
    }
    case F_CLOSE: {
        int h = moy_os_fd_owned(p, c->fd);
        if (h < 0) {
            c->r = c->fd >= 0 && c->fd <= 2 ? 0 : -EBADF;
            break;
        }
        moy_os_lock();
        moy_os_handle_drop(p, (uint32_t)h);
        moy_os_unlock();
        c->r = 0;
        break;
    }
    case F_READ:
    case F_WRITE:
        if (c->fd < 0 || (c->fd > 2 && moy_os_fd_owned(p, c->fd) < 0)) {
            c->r = -EBADF;
        } else if (moy_sh_prog_bg_stdio(&p->prog, c->fd)) {
            if (c->op == F_READ) {
                c->r = c->fd == 0 ? 0 : -EBADF;
            } else if (c->fd == 0) {
                c->r = -EBADF;
            } else {
                moy_sh_prog_bg_write(&p->prog, c->fd, c->buf, c->n);
                c->r = (long)c->n;
            }
        } else {
            errno = 0;
            long got = c->op == F_READ ? moy_sh_fd_read(s, c->fd, c->buf, c->n)
                                       : moy_sh_fd_write(s, c->fd, c->buf, c->n);
            c->r = got < 0 ? -(errno ? errno : EIO) : got;
        }
        break;
    case F_SEEK:
        if (moy_os_fd_owned(p, c->fd) < 0) {
            c->r = c->fd >= 0 && c->fd <= 2 ? -ESPIPE : -EBADF;
        } else {
            int64_t r = moy_sh_fd_seek(s, c->fd, c->at, c->whence);
            c->r = (long)r;
        }
        break;
    case F_STAT:
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
        } else {
            e = moy_sh_stat_at(s, abs, &c->st);
            c->r = e ? -e : 0;
        }
        break;
    case F_FSTAT:
        if (c->fd < 0 || (c->fd > 2 && moy_os_fd_owned(p, c->fd) < 0)) {
            c->r = -EBADF;
        } else {
            c->size = 0;
            c->what = moy_sh_prog_bg_stdio(&p->prog, c->fd) ? MOY_SH_FD_PIPE
                                                             : moy_sh_fd_what(s, c->fd, &c->size);
            c->r = c->what < 0 ? c->what : 0;
        }
        break;
    case F_UNLINK:
    case F_RMDIR:
    case F_MKDIR:
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
            break;
        }
        if (c->op != F_MKDIR) {
            moy_sh_stat_t st;
            if ((e = moy_sh_stat_at(s, abs, &st)) != 0) {
                c->r = -e;
                break;
            }
            if (c->op == F_UNLINK && st.is_dir) {
                c->r = -EISDIR;
                break;
            }
            if (c->op == F_RMDIR && !st.is_dir) {
                c->r = -ENOTDIR;
                break;
            }
        }
        e = c->op == F_MKDIR ? moy_sh_mkdir_at(s, abs) : moy_sh_remove_at(s, abs);
        c->r = e ? -e : 0;
        break;
    case F_RENAME:
        if ((e = reach(p, c->path, abs)) != 0 || (e = reach(p, c->path2, abs2)) != 0) {
            c->r = -e;
        } else {
            e = moy_sh_rename_at(s, abs, abs2);
            c->r = e ? -e : 0;
        }
        break;
    case F_LIST:
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
        } else {
            e = moy_sh_list(s, abs, list_ent, c->dir);
            c->r = e ? -e : 0;
        }
        break;
    case F_CWD:
        snprintf(p->cwd, sizeof p->cwd, "%s", moy_sh_cwd(s));
        c->r = 0;
        break;
    case F_TERM: {
        const moy_sh_host_t *h = moy_sh_host(s);
        int cols = 0, rows = 0;
        if (h->size != NULL) {
            h->size(h->ctx, &cols, &rows);
        }
        c->r = (long)cols << 16 | (long)rows;
        break;
    }
    }
}

// *p copied into `out` and pointed there: false when it does not fit.
static bool path_in(thr_t *t, const char **p, char *out) {
    if (*p == NULL) {
        return true;
    }
    size_t n = moy_os_text_len(t, *p, MOY_SH_PROG_PATH);
    if (n >= MOY_SH_PROG_PATH) {
        return false;
    }
    memcpy(out, *p, n);
    out[n] = 0;
    *p = out;
    return true;
}

// Call `c` on the session: its answer, errno set below 0.
long moy_os_fcall(thr_t *t, fcall_t *c) {
    if (t == NULL) {
        moy_os_errno_set(EPERM);
        return -1;
    }
    c->p = t->p;
    if (t->p->s == NULL && c->op != F_WRITE) {
        moy_os_errno_set(EACCES);
        return -1;
    }
    if (t->p->s == NULL) {
        // A service's stdout and stderr: the log's lines.
        if (c->fd != 1 && c->fd != 2) {
            moy_os_errno_set(EBADF);
            return -1;
        }
        moy_os_lock();
        moy_sh_prog_bg_write(&t->p->prog, c->fd, c->buf, c->n);
        moy_os_unlock();
        return (long)c->n;
    }
    // The paths are copied before the hop: the session's task reads them
    // while the module's other threads run on, and could rewrite them.
    char path[MOY_SH_PROG_PATH], path2[MOY_SH_PROG_PATH];
    if (!path_in(t, &c->path, path) || !path_in(t, &c->path2, path2)) {
        moy_os_errno_set(ENAMETOOLONG);
        return -1;
    }
    c->r = -EINTR;
    if (moy_os_on_session(t, f_body, c) != 0) {
        moy_os_errno_set(EINTR);
        return -1;
    }
    if (c->r < 0) {
        moy_os_errno_set((int)-c->r);
        return -1;
    }
    return c->r;
}

int moy_os_open(const char *path, int flags, int mode) {
    (void)mode;
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_OPEN, .path = path, .flags = flags };
    long r = moy_os_fcall(t, &c);
    moy_os_leave(t);
    return (int)r;
}

int moy_os_close(int fd) {
    thr_t *t = moy_os_enter();
    long r;
    int k = -1;
    if (t != NULL && fd != MOY_EVQ_FD) {
        moy_os_lock();
        k = moy_os_sock_slot(t->p, fd);
        if (k >= 0) {
            moy_os_sock_close(t->p, k);
        }
        moy_os_unlock();
    }
    if (t != NULL && fd == MOY_EVQ_FD) {
        r = 0;                  // the process's own: it stays open
    } else if (k >= 0) {
        r = 0;
    } else {
        fcall_t c = { .op = F_CLOSE, .fd = fd };
        r = moy_os_fcall(t, &c);
    }
    moy_os_leave(t);
    return (int)r;
}

ssize_t moy_os_read(int fd, void *buf, size_t n) {
    thr_t *t = moy_os_enter();
    long r;
    int k = t != NULL && fd >= MOY_OS_SOCK_FD && fd != MOY_EVQ_FD ? moy_os_sock_of(t, fd) : -1;
    if (t != NULL && fd == MOY_EVQ_FD) {
        r = moy_os_evq_read(t->p, buf, n);
    } else if (k >= 0) {
        r = moy_os_sock_io(t, k, buf, n, 0, false);
        moy_os_sock_let(t, k);
    } else {
        fcall_t c = { .op = F_READ, .fd = fd, .buf = buf, .n = n };
        r = moy_os_fcall(t, &c);
    }
    moy_os_leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_write(int fd, const void *buf, size_t n) {
    thr_t *t = moy_os_enter();
    long r;
    int k = t != NULL && fd >= MOY_OS_SOCK_FD && fd != MOY_EVQ_FD ? moy_os_sock_of(t, fd) : -1;
    if (k >= 0) {
        r = moy_os_sock_io(t, k, (void *)buf, n, 0, true);
        moy_os_sock_let(t, k);
    } else {
        fcall_t c = { .op = F_WRITE, .fd = fd, .buf = (void *)buf, .n = n };
        r = moy_os_fcall(t, &c);
    }
    moy_os_leave(t);
    return (ssize_t)r;
}

off_t moy_os_lseek(int fd, off_t at, int whence) {
    thr_t *t = moy_os_enter();
    long r;
    if (t != NULL && moy_os_sock_slot(t->p, fd) >= 0) {
        moy_os_errno_set(ESPIPE);
        r = -1;
    } else {
        fcall_t c = { .op = F_SEEK, .fd = fd, .at = (int64_t)at, .whence = whence };
        r = moy_os_fcall(t, &c);
    }
    moy_os_leave(t);
    return (off_t)r;
}

static void fill_stat(struct stat *st, int is_dir, int what, uint32_t size, int64_t mtime) {
    memset(st, 0, sizeof *st);
    st->st_mode = is_dir ? (S_IFDIR | 0755)
                : what == MOY_SH_FD_TERM ? (S_IFCHR | 0666)
                : what == MOY_SH_FD_PIPE ? (S_IFIFO | 0666) : (S_IFREG | 0644);
    st->st_size = (off_t)size;
    st->st_nlink = 1;
    st->st_mtime = (time_t)mtime;
}

int moy_os_stat(const char *path, struct stat *st) {
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_STAT, .path = path };
    long r = moy_os_fcall(t, &c);
    if (r == 0) {
        fill_stat(st, c.st.is_dir, MOY_SH_FD_FILE, c.st.size, c.st.mtime);
    }
    moy_os_leave(t);
    return (int)r;
}

int moy_os_fstat(int fd, struct stat *st) {
    thr_t *t = moy_os_enter();
    if (t != NULL && moy_os_sock_slot(t->p, fd) >= 0) {
        memset(st, 0, sizeof *st);
        st->st_mode = S_IFSOCK | 0666;
        st->st_nlink = 1;
        moy_os_leave(t);
        return 0;
    }
    fcall_t c = { .op = F_FSTAT, .fd = fd };
    long r = moy_os_fcall(t, &c);
    if (r == 0) {
        fill_stat(st, 0, c.what, c.size, 0);
    }
    moy_os_leave(t);
    return (int)r;
}

int moy_os_isatty(int fd) {
    thr_t *t = moy_os_enter();
    if (t != NULL && moy_os_sock_slot(t->p, fd) >= 0) {
        moy_os_errno_set(ENOTTY);
        moy_os_leave(t);
        return 0;
    }
    fcall_t c = { .op = F_FSTAT, .fd = fd };
    long r = moy_os_fcall(t, &c);
    moy_os_leave(t);
    if (r == 0 && c.what != MOY_SH_FD_TERM) {
        moy_os_errno_set(ENOTTY);
    }
    return r == 0 && c.what == MOY_SH_FD_TERM;
}

int moy_os_unlink(const char *path) {
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_UNLINK, .path = path };
    long r = moy_os_fcall(t, &c);
    moy_os_leave(t);
    return (int)r;
}

int moy_os_mkdir(const char *path, mode_t mode) {
    (void)mode;
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_MKDIR, .path = path };
    long r = moy_os_fcall(t, &c);
    moy_os_leave(t);
    return (int)r;
}

int moy_os_rmdir(const char *path) {
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_RMDIR, .path = path };
    long r = moy_os_fcall(t, &c);
    moy_os_leave(t);
    return (int)r;
}

int moy_os_rename(const char *from, const char *to) {
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_RENAME, .path = from, .path2 = to };
    long r = moy_os_fcall(t, &c);
    moy_os_leave(t);
    return (int)r;
}

// A DIR the module holds is its handle's id, never a pointer to the record.
DIR *moy_os_opendir(const char *path) {
    thr_t *t = moy_os_enter();
    dir_t *d = moy_os_alloc(sizeof *d);
    DIR *out = NULL;
    if (d == NULL) {
        moy_os_errno_set(ENOMEM);
    } else {
        fcall_t c = { .op = F_LIST, .path = path, .dir = d };
        if (moy_os_fcall(t, &c) == 0) {
            moy_os_lock();
            int h = moy_os_handle_new(t->p, H_DIR, d, -1);
            moy_os_unlock();
            if (h >= 0) {
                out = (DIR *)(uintptr_t)moy_os_handle_id(t->p, h);
                d = NULL;
            } else {
                moy_os_errno_set(EMFILE);
            }
        }
        if (d != NULL) {
            moy_os_free(d->names);
            moy_os_free(d);
        }
    }
    moy_os_leave(t);
    return out;
}

struct dirent *moy_os_readdir(DIR *dir) {
    thr_t *t = moy_os_enter();
    struct dirent *out = NULL;
    if (t != NULL) {
        moy_os_lock();                 // a closedir by another thread frees the record
    }
    int h = t != NULL ? moy_os_handle_of(t->p, (uint32_t)(uintptr_t)dir, H_DIR) : -1;
    if (h < 0) {
        moy_os_errno_set(EBADF);
    } else {
        dir_t *d = t->p->h[h].ptr;
        if (d->at < d->n) {
            const char *e = d->names + d->at;
            size_t k = strlen(e + 1);
            memset(&d->ent, 0, sizeof d->ent);
            snprintf(d->ent.d_name, sizeof d->ent.d_name, "%s", e + 1);
            d->ent.d_type = e[0] == 'd' ? DT_DIR : DT_REG;
            d->at += k + 2;
            out = &d->ent;
#ifdef MOY_OS_UMODE
            if (t->u != NULL) {
                moy_os_u_area(t)->ent = d->ent;
                out = &moy_os_u_area(t)->ent;
            }
#endif
        }
    }
    if (t != NULL) {
        moy_os_unlock();
    }
    moy_os_leave(t);
    return out;
}

int moy_os_closedir(DIR *dir) {
    thr_t *t = moy_os_enter();
    int h = t != NULL ? moy_os_handle_of(t->p, (uint32_t)(uintptr_t)dir, H_DIR) : -1;
    if (h >= 0) {
        moy_os_lock();
        moy_os_handle_drop(t->p, (uint32_t)h);
        moy_os_unlock();
    } else {
        moy_os_errno_set(EBADF);
    }
    moy_os_leave(t);
    return h >= 0 ? 0 : -1;
}

char *moy_os_getcwd(char *buf, size_t n) {
    thr_t *t = moy_os_enter();
    char *out = NULL;
    fcall_t c = { .op = F_CWD };
    if (moy_os_fcall(t, &c) == 0) {
        if (strlen(t->p->cwd) + 1 > n) {
            moy_os_errno_set(ERANGE);
        } else if (buf != NULL) {
            memcpy(buf, t->p->cwd, strlen(t->p->cwd) + 1);
            out = buf;
        } else {
            moy_os_errno_set(EINVAL);
        }
    }
    moy_os_leave(t);
    return out;
}

char *moy_os_getenv(const char *name) {
    thr_t *t = moy_os_enter();
    char *out = NULL;
    size_t k = name != NULL ? moy_os_text_len(t, name, MOY_SH_PROG_PATH) : 0;
    k = k < MOY_SH_PROG_PATH ? k : 0;
#ifdef MOY_OS_UMODE
    char *env = t == NULL ? NULL : t->p->u != NULL ? t->p->uenv : t->p->env;
#else
    char *env = t != NULL ? t->p->env : NULL;
#endif
    for (char *e = env; e != NULL && *e && k; e += strlen(e) + 1) {
        if (strncmp(e, name, k) == 0 && e[k] == '=') {
            out = e + k + 1;
            break;
        }
    }
    moy_os_leave(t);
    return out;
}
