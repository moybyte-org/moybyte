// moy_sh_sys: the operating system dash sees (moy_sh_posix.h).
//
// A DESCRIPTOR is a slot of the running process's table (moy_sh_g.h, in the
// arena) naming an OBJECT, which lives outside the arena (host memory) with a
// count of the descriptors naming it across every process, so a fork's child
// adds a reference to each and its exit drops them (moy_sh_sys_fork/_exit):
//
//   TERM   the terminal: writes go to the host's `out`, reads the host's `in`
//          (end of file without one)
//   NUL    /dev/null
//   MEM    bytes read whole at open: a file of the tree, a /dev or /proc read
//   FILE   bytes written, put into the tree (or the virtual file) whole when
//          its last descriptor closes; created or truncated at open, so an
//          open that cannot write fails there
//   PIPE_R, PIPE_W   the two ends of a pipe buffer. Stages run one after
//          another, so a read of an empty pipe is its end; a write past
//          MOY_SH_PIPE_MAX or with no reader left fails with EPIPE
//
// PATHS are the shell's: absolute after the working folder is applied and
// `.`/`..` folded. /dev and /proc are virtual (the host's vread/vlist, with
// /dev/null and /dev/tty here); /bin lists the installed commands (the
// host's lookup); everything else is the store's, under the host's root,
// through moy_vol. `/` lists the virtual folders, the root volume's names and
// the folders mounted beside it (moy_vol_mounts), each once.

#include <errno.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "moy_sh_posix.h"
#include "moy_sh_int.h"
#include "moy_vol.h"

enum { O_TERM = 1, O_NUL, O_MEM, O_FILE, O_PIPE_R, O_PIPE_W };

typedef struct {
    char *buf;
    size_t len, cap, pos;
    int readers, writers;
} sh_pipe_t;

struct moy_sh_obj {
    int kind;
    int refs;
    int flags;                  // O_ACCMODE bits and O_APPEND
    int stream;                 // TERM: 1 or 2 (0 for input)
    char *buf;
    size_t len, cap, pos;
    char *path;                 // FILE: where its bytes go
    int vfile;                  // FILE: a virtual file (the host's vwrite)
    sh_pipe_t *pipe;
};

struct moy_sh_dir {
    struct dirent ent;
    char *names;                // NUL-separated, a type byte before each
    size_t len, cap, pos;
};

// --- objects ----------------------------------------------------------------

static struct moy_sh_obj *obj_new(int kind) {
    struct moy_sh_obj *o = moy_sh_halloc(sizeof *o);
    if (o != NULL) {
        memset(o, 0, sizeof *o);
        o->kind = kind;
        o->refs = 1;
    }
    return o;
}

static int buf_add(char **buf, size_t *len, size_t *cap, const void *p, size_t n, size_t max) {
    if (*len + n > max) {
        return -1;
    }
    if (*len + n > *cap) {
        size_t c = *cap ? *cap : 256;
        while (c < *len + n) {
            c *= 2;
        }
        char *q = moy_sh_halloc(c);
        if (q == NULL) {
            return -1;
        }
        if (*len) {
            memcpy(q, *buf, *len);
        }
        moy_sh_hfree(*buf);
        *buf = q;
        *cap = c;
    }
    memcpy(*buf + *len, p, n);
    *len += n;
    return 0;
}

static char *dupstr(const char *p) {
    size_t n = strlen(p) + 1;
    char *q = moy_sh_halloc(n);
    if (q != NULL) {
        memcpy(q, p, n);
    }
    return q;
}

static int store_write(const char *path, const char *p, size_t n, int append);

static void obj_drop(struct moy_sh_obj *o) {
    if (o == NULL || --o->refs > 0) {
        return;
    }
    moy_sh_t *s = moy_sh_cur;
    if (o->kind == O_FILE && o->path != NULL) {
        if (o->vfile) {
            if (s->host.vwrite != NULL) {
                s->host.vwrite(s->host.ctx, o->path, o->buf, o->len);
            }
        } else {
            store_write(o->path, o->buf, o->len, (o->flags & O_APPEND) != 0);
        }
    }
    if (o->pipe != NULL) {
        sh_pipe_t *p = o->pipe;
        if (o->kind == O_PIPE_R) {
            p->readers--;
        } else {
            p->writers--;
        }
        if (p->readers == 0 && p->writers == 0) {
            moy_sh_hfree(p->buf);
            moy_sh_hfree(p);
        }
    }
    moy_sh_hfree(o->buf);
    moy_sh_hfree(o->path);
    moy_sh_hfree(o);
}

static struct moy_sh_proc *proc(void) {
    return &moy_sh_g->proc;
}

static struct moy_sh_obj *fd_obj(int fd) {
    if (fd < 0 || fd >= MOY_SH_FDS) {
        return NULL;
    }
    return proc()->fd[fd];
}

static int fd_lowest(int from) {
    for (int i = from < 0 ? 0 : from; i < MOY_SH_FDS; i++) {
        if (proc()->fd[i] == NULL) {
            return i;
        }
    }
    return -1;
}

static int fd_put(struct moy_sh_obj *o, int from) {
    int fd = fd_lowest(from);
    if (fd < 0) {
        obj_drop(o);
        errno = EMFILE;
        return -1;
    }
    proc()->fd[fd] = o;
    proc()->cloexec[fd] = 0;
    return fd;
}

void moy_sh_sys_start(moy_sh_t *s) {
    (void)s;
    for (int i = 0; i < 3; i++) {
        struct moy_sh_obj *o = obj_new(O_TERM);
        if (o != NULL) {
            o->stream = i;
            o->flags = i == 0 ? O_RDONLY : O_WRONLY;
        }
        proc()->fd[i] = o;
    }
    proc()->cwd = moy_sh_strdup("/");
    proc()->pid = 100;
}

void moy_sh_sys_fork(moy_sh_t *s) {
    (void)s;
    for (int i = 0; i < MOY_SH_FDS; i++) {
        if (proc()->fd[i] != NULL) {
            proc()->fd[i]->refs++;
        }
    }
}

void moy_sh_sys_exit(moy_sh_t *s) {
    (void)s;
    for (int i = 0; i < MOY_SH_FDS; i++) {
        struct moy_sh_obj *o = proc()->fd[i];
        proc()->fd[i] = NULL;
        obj_drop(o);
    }
}

void moy_sh_sys_end(moy_sh_t *s) {
    moy_sh_sys_exit(s);
}

// --- paths ------------------------------------------------------------------

int moy_sh_path(const char *path, char *out, size_t cap) {
    char tmp[MOY_SH_PATH_MAX];
    const char *cwd = proc()->cwd != NULL ? proc()->cwd : "/";
    size_t n;
    if (path[0] == '/') {
        n = strlen(path);
        if (n >= sizeof tmp) {
            return ENAMETOOLONG;
        }
        memcpy(tmp, path, n + 1);
    } else {
        n = (size_t)snprintf(tmp, sizeof tmp, "%s/%s", cwd, path);
        if (n >= sizeof tmp) {
            return ENAMETOOLONG;
        }
    }
    // fold: out is built component by component
    size_t o = 0;
    const char *p = tmp;
    if (cap < 2) {
        return ENAMETOOLONG;
    }
    while (*p) {
        while (*p == '/') {
            p++;
        }
        const char *e = p;
        while (*e && *e != '/') {
            e++;
        }
        size_t len = (size_t)(e - p);
        if (len == 0 || (len == 1 && p[0] == '.')) {
            // nothing
        } else if (len == 2 && p[0] == '.' && p[1] == '.') {
            while (o > 0 && out[o - 1] != '/') {
                o--;
            }
            if (o > 0) {
                o--;
            }
        } else {
            if (o + 1 + len + 1 > cap) {
                return ENAMETOOLONG;
            }
            out[o++] = '/';
            memcpy(out + o, p, len);
            o += len;
        }
        p = e;
    }
    if (o == 0) {
        out[o++] = '/';
    }
    out[o] = 0;
    return 0;
}

static int under(const char *abs, const char *dir) {
    size_t n = strlen(dir);
    return strncmp(abs, dir, n) == 0 && (abs[n] == 0 || abs[n] == '/');
}

static int is_virtual(const char *abs) {
    return under(abs, "/dev") || under(abs, "/proc") || under(abs, "/bin");
}

const char *moy_sh_store_path(const char *abs, char *out, size_t cap) {
    const char *root = moy_sh_cur->host.root != NULL ? moy_sh_cur->host.root : "";
    if (is_virtual(abs)) {
        return NULL;
    }
    const char *etc = moy_sh_cur->host.etc;
    const char *home = moy_sh_cur->home;
    size_t hn = strcmp(home, "/") == 0 ? 0 : strlen(home);
    if (etc != NULL && (under(abs, "/etc") || under(abs, "/.ssh")
                        || (hn > 0 && strncmp(abs, home, hn) == 0 && under(abs + hn, "/.ssh")))) {
        const char *rest = abs + 4;
        if (under(abs, "/.ssh")) {
            rest = abs + 5;
        } else if (!under(abs, "/etc")) {
            rest = abs + hn + 5;            // the home's .ssh
        } else if (under(rest, "/dropbear")) {
            rest += 9;
        }
        size_t n = (size_t)snprintf(out, cap, "%s%s%s", root, etc, rest);
        return n < cap ? out : NULL;
    }
    size_t n = (size_t)snprintf(out, cap, "%s%s", root, strcmp(abs, "/") == 0 && *root ? "" : abs);
    if (n >= cap) {
        return NULL;
    }
    if (n == 0) {
        out[0] = '/';
        out[1] = 0;
    }
    return out;
}

// --- the store --------------------------------------------------------------

static int store_read_here(const char *path, char **buf, size_t *len) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    uint32_t size = 0;
    int entered = moy_vol_gate_enter(path);
    int r = moy_vol_at(path, &v, &rest);
    if (r == 0) {
        r = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    if (r == 0) {
        r = moy_vol_size(f, &size);
        if (r == 0 && size > MOY_SH_FILE_MAX) {
            r = EFBIG;
        }
        char *p = r == 0 ? moy_sh_halloc(size + 1) : NULL;
        if (r == 0 && p == NULL) {
            r = ENOMEM;
        }
        size_t got = 0;
        while (r == 0 && got < size) {
            size_t n = 0;
            r = moy_vol_read(f, p + got, size - got, &n);
            if (n == 0) {
                break;
            }
            got += n;
        }
        int c = moy_vol_close(f);
        if (r == 0) {
            r = c;
        }
        if (r == 0) {
            p[got] = 0;
            *buf = p;
            *len = got;
        } else {
            moy_sh_hfree(p);
        }
    }
    moy_vol_gate_leave(entered);
    return r;
}

static int store_write_here(const char *path, const char *p, size_t n, int append) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    int entered = moy_vol_gate_enter(path);
    int r = moy_vol_at(path, &v, &rest);
    if (r == 0) {
        r = moy_vol_open(&v, rest, append ? MOY_VOL_APPEND : MOY_VOL_WRITE, &f);
    }
    if (r == 0) {
        if (n) {
            r = moy_vol_write(f, p, n);
        }
        int c = moy_vol_close(f);
        if (r == 0) {
            r = c;
        }
    }
    moy_vol_gate_leave(entered);
    return r;
}

static int store_stat_here(const char *path, moy_vol_stat_t *st) {
    moy_vol_t v;
    const char *rest;
    int entered = moy_vol_gate_enter(path);
    int r = moy_vol_at(path, &v, &rest);
    if (r == 0) {
        r = moy_vol_stat(&v, rest, st);
    }
    moy_vol_gate_leave(entered);
    return r;
}

// Each store call runs where the store may be touched: through the host's
// hop (moy_sh_hop), the session current there.
typedef struct {
    int op;
    const char *path;
    char **buf;
    size_t *len;
    const char *p;
    size_t n;
    int append;
    moy_vol_stat_t *st;
    int r;
} store_call_t;

enum { S_READ, S_WRITE, S_STAT };

static void store_here(void *arg) {
    store_call_t *c = arg;
    switch (c->op) {
        case S_READ:
            c->r = store_read_here(c->path, c->buf, c->len);
            break;
        case S_WRITE:
            c->r = store_write_here(c->path, c->p, c->n, c->append);
            break;
        default:
            c->r = store_stat_here(c->path, c->st);
            break;
    }
}

static int store_call(store_call_t *c) {
    moy_sh_hop(moy_sh_cur, store_here, c);
    return c->r;
}

static int store_read(const char *path, char **buf, size_t *len) {
    store_call_t c = { .op = S_READ, .path = path, .buf = buf, .len = len };
    return store_call(&c);
}

static int store_write(const char *path, const char *p, size_t n, int append) {
    store_call_t c = { .op = S_WRITE, .path = path, .p = p, .n = n, .append = append };
    return store_call(&c);
}

static int store_stat(const char *path, moy_vol_stat_t *st) {
    store_call_t c = { .op = S_STAT, .path = path, .st = st };
    return store_call(&c);
}

// --- virtual files ----------------------------------------------------------

typedef struct {
    const char *name;
    int found, is_dir;
    uint32_t size;
} find_t;

static int find_ent(void *arg, const char *name, int is_dir, uint32_t size) {
    find_t *f = arg;
    if (strcmp(name, f->name) == 0) {
        f->found = 1;
        f->is_dir = is_dir;
        f->size = size;
        return 1;
    }
    return 0;
}

static int vlist(const char *dir, moy_sh_ent_fn fn, void *arg);

// Whether the virtual `abs` exists: 0 none, 1 a file, 2 a folder.
static int vexists(const char *abs) {
    if (!strcmp(abs, "/dev") || !strcmp(abs, "/proc") || !strcmp(abs, "/bin")) {
        return 2;
    }
    if (!strcmp(abs, "/dev/null") || !strcmp(abs, "/dev/tty")) {
        return 1;
    }
    if (under(abs, "/bin")) {
        const char *name = abs + 5;
        return strchr(name, '/') == NULL && moy_sh_cur->host.lookup != NULL &&
               moy_sh_cur->host.lookup(moy_sh_cur->host.ctx, name) ? 1 : 0;
    }
    char dir[MOY_SH_PATH_MAX];
    const char *slash = strrchr(abs, '/');
    size_t n = (size_t)(slash - abs);
    if (n >= sizeof dir) {
        return 0;
    }
    memcpy(dir, abs, n);
    dir[n] = 0;
    find_t f = { slash + 1, 0, 0, 0 };
    vlist(dir, find_ent, &f);
    return f.found ? (f.is_dir ? 2 : 1) : 0;
}

static int vlist(const char *dir, moy_sh_ent_fn fn, void *arg) {
    moy_sh_t *s = moy_sh_cur;
    if (!strcmp(dir, "/dev")) {
        int r = fn(arg, "null", 0, 0);
        if (!r) {
            r = fn(arg, "tty", 0, 0);
        }
        if (!r && s->host.vlist != NULL) {
            r = s->host.vlist(s->host.ctx, dir, fn, arg) == ENOENT ? 0 : r;
        }
        return r;
    }
    if (s->host.vlist == NULL) {
        return ENOENT;
    }
    return s->host.vlist(s->host.ctx, dir, fn, arg);
}

// --- descriptors ------------------------------------------------------------

int moy_sh_open(const char *path, int flags, ...) {
    char abs[MOY_SH_PATH_MAX], sp[MOY_SH_PATH_MAX];
    int r = moy_sh_path(path, abs, sizeof abs);
    if (r != 0) {
        errno = r;
        return -1;
    }
    int acc = flags & O_ACCMODE;
    struct moy_sh_obj *o;
    if (!strcmp(abs, "/dev/null")) {
        o = obj_new(O_NUL);
    } else if (!strcmp(abs, "/dev/tty")) {
        o = obj_new(O_TERM);
        if (o != NULL) {
            o->stream = acc == O_RDONLY ? 0 : 1;
        }
    } else if (is_virtual(abs)) {
        int kind = vexists(abs);
        if (kind == 2) {
            errno = EISDIR;
            return -1;
        }
        if (under(abs, "/bin") || (kind == 0 && acc != O_RDONLY)) {
            errno = under(abs, "/bin") && kind == 1 ? EACCES : EROFS;
            return -1;
        }
        if (kind == 0) {
            errno = ENOENT;
            return -1;
        }
        if (acc == O_RDONLY) {
            char *buf = NULL;
            size_t len = 0;
            moy_sh_t *s = moy_sh_cur;
            r = s->host.vread != NULL ? s->host.vread(s->host.ctx, abs, &buf, &len) : ENOENT;
            if (r != 0) {
                errno = r;
                return -1;
            }
            o = obj_new(O_MEM);
            if (o == NULL) {
                moy_sh_hfree(buf);
            } else {
                o->buf = buf;
                o->len = len;
            }
        } else {
            o = obj_new(O_FILE);
            if (o != NULL) {
                o->vfile = 1;
                o->path = dupstr(abs);
            }
        }
    } else {
        const char *p = moy_sh_store_path(abs, sp, sizeof sp);
        moy_vol_stat_t st;
        if (p == NULL) {
            errno = ENAMETOOLONG;
            return -1;
        }
        int exists = store_stat(p, &st) == 0;
        if (exists && st.is_dir) {
            errno = EISDIR;
            return -1;
        }
        if (acc == O_RDONLY) {
            char *buf = NULL;
            size_t len = 0;
            if (!exists) {
                errno = ENOENT;
                return -1;
            }
            r = store_read(p, &buf, &len);
            if (r != 0) {
                errno = r;
                return -1;
            }
            o = obj_new(O_MEM);
            if (o == NULL) {
                moy_sh_hfree(buf);
            } else {
                o->buf = buf;
                o->len = len;
            }
        } else {
            if (exists && (flags & O_CREAT) && (flags & O_EXCL)) {
                errno = EEXIST;
                return -1;
            }
            if (!exists && !(flags & O_CREAT)) {
                errno = ENOENT;
                return -1;
            }
            o = obj_new(O_FILE);
            if (o == NULL) {
                errno = ENOMEM;
                return -1;
            }
            o->path = dupstr(p);
            o->flags = flags & (O_ACCMODE | O_APPEND);
            if (acc == O_RDWR && exists && !(flags & O_TRUNC)) {
                // read and written whole: what was there is kept
                r = store_read(p, &o->buf, &o->len);
                o->cap = o->len;
                o->flags &= ~O_APPEND;
            } else if (!(flags & O_APPEND) || !exists) {
                // created or truncated now, so a refusal is the open's
                r = store_write(p, "", 0, 0);
            }
            if (r != 0 || o->path == NULL) {
                o->path = (moy_sh_hfree(o->path), NULL);
                obj_drop(o);
                errno = r ? r : ENOMEM;
                return -1;
            }
        }
    }
    if (o == NULL) {
        errno = ENOMEM;
        return -1;
    }
    o->flags = (o->flags & ~O_ACCMODE) | acc;
    return fd_put(o, 0);
}

ssize_t moy_sh_read(int fd, void *buf, size_t n) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL || (o->flags & O_ACCMODE) == O_WRONLY) {
        errno = EBADF;
        return -1;
    }
    switch (o->kind) {
        case O_MEM:
        case O_FILE: {
            size_t left = o->len > o->pos ? o->len - o->pos : 0;
            if (n > left) {
                n = left;
            }
            if (n) {
                memcpy(buf, o->buf + o->pos, n);
            }
            o->pos += n;
            return (ssize_t)n;
        }
        case O_PIPE_R: {
            sh_pipe_t *p = o->pipe;
            size_t left = p->len - p->pos;
            if (n > left) {
                n = left;
            }
            if (n) {
                memcpy(buf, p->buf + p->pos, n);
            }
            p->pos += n;
            return (ssize_t)n;
        }
        case O_PIPE_W:
            errno = EBADF;
            return -1;
        case O_TERM: {
            moy_sh_t *s = moy_sh_cur;
            long r = s->host.in != NULL ? s->host.in(s->host.ctx, buf, n) : 0;
            if (r < 0) {
                errno = EIO;
                return -1;
            }
            return (ssize_t)r;
        }
        default:
            return 0;
    }
}

ssize_t moy_sh_write(int fd, const void *buf, size_t n) {
    struct moy_sh_obj *o = fd_obj(fd);
    moy_sh_t *s = moy_sh_cur;
    if (o == NULL || (o->flags & O_ACCMODE) == O_RDONLY) {
        errno = EBADF;
        return -1;
    }
    switch (o->kind) {
        case O_TERM:
            s->host.out(s->host.ctx, o->stream ? o->stream : 1, buf, n);
            return (ssize_t)n;
        case O_NUL:
            return (ssize_t)n;
        case O_FILE:
            if (buf_add(&o->buf, &o->len, &o->cap, buf, n, MOY_SH_FILE_MAX) != 0) {
                errno = EFBIG;
                return -1;
            }
            return (ssize_t)n;
        case O_PIPE_W: {
            sh_pipe_t *p = o->pipe;
            if (p->readers == 0) {
                errno = EPIPE;
                return -1;
            }
            // a drained pipe starts over at its front
            if (p->pos == p->len) {
                p->pos = p->len = 0;
            }
            if (buf_add(&p->buf, &p->len, &p->cap, buf, n, MOY_SH_PIPE_MAX) != 0) {
                errno = EPIPE;
                return -1;
            }
            return (ssize_t)n;
        }
        default:
            errno = EBADF;
            return -1;
    }
}

int moy_sh_close(int fd) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL) {
        errno = EBADF;
        return -1;
    }
    proc()->fd[fd] = NULL;
    obj_drop(o);
    return 0;
}

int moy_sh_dup2(int fd, int to) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL || to < 0 || to >= MOY_SH_FDS) {
        errno = EBADF;
        return -1;
    }
    if (fd == to) {
        return to;
    }
    o->refs++;
    struct moy_sh_obj *old = proc()->fd[to];
    proc()->fd[to] = o;
    proc()->cloexec[to] = 0;
    obj_drop(old);
    return to;
}

int moy_sh_dup(int fd) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL) {
        errno = EBADF;
        return -1;
    }
    o->refs++;
    return fd_put(o, 0);
}

int moy_sh_fcntl(int fd, int cmd, ...) {
    va_list ap;
    va_start(ap, cmd);
    long arg = va_arg(ap, long);
    va_end(ap);
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL) {
        errno = EBADF;
        return -1;
    }
    switch (cmd) {
        case F_DUPFD:
        case F_DUPFD_CLOEXEC: {
            o->refs++;
            int to = fd_put(o, (int)arg);
            if (to >= 0 && cmd == F_DUPFD_CLOEXEC) {
                proc()->cloexec[to] = 1;
            }
            return to;
        }
        case F_GETFD:
            return proc()->cloexec[fd] ? FD_CLOEXEC : 0;
        case F_SETFD:
            proc()->cloexec[fd] = (arg & FD_CLOEXEC) != 0;
            return 0;
        case F_GETFL:
            return o->flags;
        case F_SETFL:
            return 0;
        default:
            errno = EINVAL;
            return -1;
    }
}

int moy_sh_pipe(int fds[2]) {
    sh_pipe_t *p = moy_sh_halloc(sizeof *p);
    struct moy_sh_obj *r = obj_new(O_PIPE_R), *w = obj_new(O_PIPE_W);
    if (p == NULL || r == NULL || w == NULL) {
        moy_sh_hfree(p);
        moy_sh_hfree(r);
        moy_sh_hfree(w);
        errno = ENOMEM;
        return -1;
    }
    memset(p, 0, sizeof *p);
    p->readers = p->writers = 1;
    r->pipe = w->pipe = p;
    r->flags = O_RDONLY;
    w->flags = O_WRONLY;
    fds[0] = fd_put(r, 0);
    if (fds[0] < 0) {
        p->readers = 0;
        obj_drop(w);
        return -1;
    }
    fds[1] = fd_put(w, 0);
    if (fds[1] < 0) {
        moy_sh_close(fds[0]);
        return -1;
    }
    return 0;
}

off_t moy_sh_lseek(int fd, off_t at, int whence) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL) {
        errno = EBADF;
        return -1;
    }
    if (o->kind != O_MEM && o->kind != O_FILE) {
        errno = ESPIPE;
        return -1;
    }
    off_t base = whence == SEEK_SET ? 0 : whence == SEEK_CUR ? (off_t)o->pos : (off_t)o->len;
    if (base + at < 0) {
        errno = EINVAL;
        return -1;
    }
    o->pos = (size_t)(base + at);
    return (off_t)o->pos;
}

int moy_sh_isatty(int fd) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL || o->kind != O_TERM) {
        errno = o == NULL ? EBADF : ENOTTY;
        return 0;
    }
    return 1;
}

// --- files ------------------------------------------------------------------

static void fill_stat(struct stat *st, const char *abs, int is_dir, uint32_t size, int64_t mtime) {
    memset(st, 0, sizeof *st);
    st->st_mode = is_dir ? (S_IFDIR | 0755) : (S_IFREG | 0755);
    st->st_size = size;
    st->st_mtime = (time_t)mtime;
    st->st_nlink = 1;
    st->st_dev = 1;
    uint32_t h = 2166136261u;
    for (const char *p = abs; *p; p++) {
        h = (h ^ (unsigned char)*p) * 16777619u;
    }
    st->st_ino = h;
}

int moy_sh_stat(const char *path, struct stat *st) {
    char abs[MOY_SH_PATH_MAX], sp[MOY_SH_PATH_MAX];
    int r = moy_sh_path(path, abs, sizeof abs);
    if (r != 0) {
        errno = r;
        return -1;
    }
    if (!strcmp(abs, "/")) {
        fill_stat(st, abs, 1, 0, 0);
        return 0;
    }
    if (is_virtual(abs)) {
        int kind = vexists(abs);
        if (kind == 0) {
            errno = ENOENT;
            return -1;
        }
        fill_stat(st, abs, kind == 2, 0, 0);
        if (kind == 1 && under(abs, "/dev")) {
            st->st_mode = S_IFCHR | 0666;
        }
        return 0;
    }
    const char *p = moy_sh_store_path(abs, sp, sizeof sp);
    moy_vol_stat_t vs;
    r = p != NULL ? store_stat(p, &vs) : ENAMETOOLONG;
    if (r != 0) {
        // a folder the host's root listing names (a mount point on a board)
        char dir[2] = "/";
        find_t f = { abs + 1, 0, 0, 0 };
        if (strchr(abs + 1, '/') == NULL && moy_sh_cur->host.vlist != NULL) {
            moy_sh_cur->host.vlist(moy_sh_cur->host.ctx, dir, find_ent, &f);
        }
        if (f.found) {
            fill_stat(st, abs, f.is_dir, f.size, 0);
            return 0;
        }
        errno = r;
        return -1;
    }
    fill_stat(st, abs, vs.is_dir, vs.size, vs.mtime);
    return 0;
}

int moy_sh_fstat(int fd, struct stat *st) {
    struct moy_sh_obj *o = fd_obj(fd);
    if (o == NULL) {
        errno = EBADF;
        return -1;
    }
    memset(st, 0, sizeof *st);
    st->st_mode = o->kind == O_TERM || o->kind == O_NUL ? S_IFCHR | 0666 :
                  o->kind == O_PIPE_R || o->kind == O_PIPE_W ? S_IFIFO | 0600 : S_IFREG | 0644;
    st->st_size = o->kind == O_MEM || o->kind == O_FILE ? (off_t)o->len : 0;
    return 0;
}

int moy_sh_access(const char *path, int mode) {
    struct stat st;
    (void)mode;
    return moy_sh_stat(path, &st);
}

int moy_sh_faccessat(int dirfd, const char *path, int mode, int flags) {
    (void)dirfd;
    (void)flags;
    return moy_sh_access(path, mode);
}

int moy_sh_chdir(const char *path) {
    char abs[MOY_SH_PATH_MAX];
    struct stat st;
    int r = moy_sh_path(path, abs, sizeof abs);
    if (r != 0) {
        errno = r;
        return -1;
    }
    if (moy_sh_stat(abs, &st) != 0) {
        return -1;
    }
    if (!S_ISDIR(st.st_mode)) {
        errno = ENOTDIR;
        return -1;
    }
    char *cwd = moy_sh_strdup(abs);
    if (cwd == NULL) {
        errno = ENOMEM;
        return -1;
    }
    moy_sh_mfree(proc()->cwd);
    proc()->cwd = cwd;
    return 0;
}

char *moy_sh_getcwd(char *buf, size_t n) {
    const char *cwd = proc()->cwd != NULL ? proc()->cwd : "/";
    size_t len = strlen(cwd) + 1;
    if (buf == NULL) {
        buf = moy_sh_malloc(len);
        n = len;
        if (buf == NULL) {
            errno = ENOMEM;
            return NULL;
        }
    }
    if (len > n) {
        errno = ERANGE;
        return NULL;
    }
    memcpy(buf, cwd, len);
    return buf;
}

// --- folders ----------------------------------------------------------------

static int dir_add(void *arg, const char *name, int is_dir, uint32_t size) {
    struct moy_sh_dir *d = arg;
    char t = is_dir ? 'd' : 'f';
    (void)size;
    if (buf_add(&d->names, &d->len, &d->cap, &t, 1, MOY_SH_FILE_MAX) != 0 ||
        buf_add(&d->names, &d->len, &d->cap, name, strlen(name) + 1, MOY_SH_FILE_MAX) != 0) {
        return ENOMEM;
    }
    return 0;
}

// The root's names come from several places (the virtual folders, the
// host's, the root volume's, the mount points): each is listed once.
static int root_add(void *arg, const char *name, int is_dir, uint32_t size) {
    struct moy_sh_dir *d = arg;
    for (size_t i = 0; i < d->len; i += strlen(d->names + i + 1) + 2) {
        if (strcmp(d->names + i + 1, name) == 0) {
            return 0;
        }
    }
    return dir_add(d, name, is_dir, size);
}

static int vol_ent(void *ctx, const char *name, size_t len, int is_dir, uint32_t size) {
    (void)len;
    return dir_add(ctx, name, is_dir, size);
}

static int vol_root_ent(void *ctx, const char *name, size_t len, int is_dir, uint32_t size) {
    (void)len;
    return root_add(ctx, name, is_dir, size);
}

static int mount_ent(void *arg, const char *name) {
    return root_add(arg, name, 1, 0);
}

// Taken weak: a host whose one volume holds the whole tree has no mounts.
int moy_vol_mounts(int (*fn)(void *arg, const char *name), void *arg) __attribute__((weak));

typedef struct {
    const char *path;
    struct moy_sh_dir *d;
    int r;
    int root;                           // the tree's root: its volume's names and the mounts
} list_call_t;

// A folder of the store listed, where the store may be touched (store_call).
static void list_here(void *arg) {
    list_call_t *c = arg;
    moy_vol_t v;
    const char *rest;
    int entered = moy_vol_gate_enter(c->path);
    c->r = c->path != NULL ? moy_vol_at(c->path, &v, &rest) : ENAMETOOLONG;
    if (c->r == 0) {
        c->r = moy_vol_list(&v, rest, c->root ? vol_root_ent : vol_ent, c->d);
    }
    moy_vol_gate_leave(entered);
    if (c->root && moy_vol_mounts != NULL) {
        moy_vol_mounts(mount_ent, c->d);
    }
}

moy_sh_DIR *moy_sh_opendir(const char *path) {
    char abs[MOY_SH_PATH_MAX], sp[MOY_SH_PATH_MAX];
    struct stat st;
    int r = moy_sh_path(path, abs, sizeof abs);
    if (r != 0) {
        errno = r;
        return NULL;
    }
    if (moy_sh_stat(abs, &st) != 0) {
        return NULL;
    }
    if (!S_ISDIR(st.st_mode)) {
        errno = ENOTDIR;
        return NULL;
    }
    struct moy_sh_dir *d = moy_sh_halloc(sizeof *d);
    if (d == NULL) {
        errno = ENOMEM;
        return NULL;
    }
    memset(d, 0, sizeof *d);
    moy_sh_t *s = moy_sh_cur;
    if (!strcmp(abs, "/bin")) {
        r = s->host.vlist != NULL ? s->host.vlist(s->host.ctx, abs, dir_add, d) : 0;
        r = r == ENOENT ? 0 : r;
    } else if (is_virtual(abs)) {
        r = vlist(abs, dir_add, d);
        r = r == ENOENT && !strcmp(abs, "/proc") ? 0 : r;
    } else {
        int root = !strcmp(abs, "/");
        if (root) {
            dir_add(d, "bin", 1, 0);
            dir_add(d, "dev", 1, 0);
            if (s->host.etc != NULL) {
                dir_add(d, "etc", 1, 0);
            }
            dir_add(d, "proc", 1, 0);
            if (s->host.vlist != NULL) {
                s->host.vlist(s->host.ctx, "/", root_add, d);
            }
        }
        list_call_t c = { moy_sh_store_path(abs, sp, sizeof sp), d, 0, root };
        moy_sh_hop(s, list_here, &c);
        r = !strcmp(abs, "/") ? 0 : c.r;
    }
    if (r != 0) {
        moy_sh_hfree(d->names);
        moy_sh_hfree(d);
        errno = r;
        return NULL;
    }
    return d;
}

struct dirent *moy_sh_readdir(moy_sh_DIR *d) {
    if (d->pos >= d->len) {
        return NULL;
    }
    char t = d->names[d->pos];
    const char *name = d->names + d->pos + 1;
    size_t n = strlen(name);
    d->pos += n + 2;
    if (n >= sizeof d->ent.d_name) {
        n = sizeof d->ent.d_name - 1;
    }
    memcpy(d->ent.d_name, name, n);
    d->ent.d_name[n] = 0;
#ifdef DT_DIR
    d->ent.d_type = t == 'd' ? DT_DIR : DT_REG;
#else
    (void)t;
#endif
    return &d->ent;
}

int moy_sh_closedir(moy_sh_DIR *d) {
    if (d != NULL) {
        moy_sh_hfree(d->names);
        moy_sh_hfree(d);
    }
    return 0;
}

// --- processes and signals ---------------------------------------------------

pid_t moy_sh_getpid(void) {
    return proc()->pid;
}

int moy_sh_kill(pid_t pid, int sig) {
    moy_sh_t *s = moy_sh_cur;
    if (pid < 0) {
        pid = -pid;
    }
    if (pid == 0 || pid == proc()->pid) {
        if (sig == 0) {
            return 0;
        }
        return moy_sh_raise(sig);
    }
    if (pid >= MOY_SH_FIRST_CHILD) {
        // a child the shell ran has already run to its end
        if ((sig == 0 || sig == SIGCONT) && moy_sh_kid(pid) >= 0) {
            return 0;
        }
        errno = ESRCH;
        return -1;
    }
    if (sig == 0) {
        int code;
        if (s->host.status != NULL && s->host.status(s->host.ctx, pid, &code) >= 0) {
            return 0;
        }
        errno = ESRCH;
        return -1;
    }
    int r = s->host.kill != NULL ? (*s->host.kill)(s->host.ctx, pid, sig) : ESRCH;
    if (r != 0) {
        errno = r;
        return -1;
    }
    return 0;
}

int moy_sh_sigaction(int sig, const struct sigaction *act, struct sigaction *old) {
    (void)sig;
    (void)act;
    if (old != NULL) {
        memset(old, 0, sizeof *old);
        old->sa_handler = SIG_DFL;
    }
    return 0;
}

int moy_sh_sigprocmask(int how, const sigset_t *set, sigset_t *old) {
    (void)how;
    (void)set;
    if (old != NULL) {
        sigemptyset(old);
    }
    return 0;
}

int moy_sh_tcgetattr(int fd, struct termios *t) {
    (void)fd;
    (void)t;
    errno = ENOTTY;
    return -1;
}

mode_t moy_sh_umask(mode_t m) {
    (void)m;
    return 022;
}

long moy_sh_sysconf(int name) {
    (void)name;
    return 100;
}

// The signal names, by number (kill -l, trap); 0 is EXIT, a number not named
// here is NULL and dash skips it.
const char *const signal_names[NSIG + 1] = {
    [0] = "EXIT",
    [SIGHUP] = "HUP", [SIGINT] = "INT", [SIGQUIT] = "QUIT", [SIGILL] = "ILL",
    [SIGTRAP] = "TRAP", [SIGABRT] = "ABRT", [SIGFPE] = "FPE", [SIGKILL] = "KILL",
    [SIGSEGV] = "SEGV", [SIGPIPE] = "PIPE", [SIGALRM] = "ALRM", [SIGTERM] = "TERM",
    [SIGUSR1] = "USR1", [SIGUSR2] = "USR2", [SIGCHLD] = "CHLD", [SIGCONT] = "CONT",
    [SIGSTOP] = "STOP", [SIGTSTP] = "TSTP", [SIGTTIN] = "TTIN", [SIGTTOU] = "TTOU",
    [SIGWINCH] = "WINCH",
};

const char *moy_sh_strsignal(int sig) {
    static const struct { int sig; const char *text; } texts[] = {
        { SIGHUP, "Hangup" }, { SIGINT, "Interrupt" }, { SIGQUIT, "Quit" },
        { SIGABRT, "Aborted" }, { SIGKILL, "Killed" }, { SIGSEGV, "Segmentation fault" },
        { SIGPIPE, "Broken pipe" }, { SIGALRM, "Alarm clock" }, { SIGTERM, "Terminated" },
        { SIGSTOP, "Stopped (signal)" }, { SIGTSTP, "Stopped" },
    };
    for (size_t i = 0; i < sizeof texts / sizeof texts[0]; i++) {
        if (texts[i].sig == sig) {
            return texts[i].text;
        }
    }
    if (sig > 0 && sig < NSIG && signal_names[sig] != NULL) {
        return signal_names[sig];
    }
    return "Unknown signal";
}

int moy_sh_unsupported(const char *what) {
    moy_sh_t *s = moy_sh_cur;
    static const char msg[] = "sh: the kernel has no ";
    s->host.out(s->host.ctx, 2, msg, sizeof msg - 1);
    s->host.out(s->host.ctx, 2, what, strlen(what));
    s->host.out(s->host.ctx, 2, "\n", 1);
    abort();
}
