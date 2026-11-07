// moy_tty_sftp: the SFTP server a face runs in place of its shell's command
// (moy_tty.h, MOY_TTY_SFTP): SFTP version 3 (draft-ietf-secsh-filexfer-02,
// the version OpenSSH speaks) over the face's stream, on the files tree of the
// face's moy_sh session (docs/os_2026-11/system.md section 4).
//
// PATHS are the shell's: a client's path is made absolute against the
// session's folder, its home (moy_sh_abs), and names the store's file the
// shell's would (moy_sh_resolve: /etc the store's system folder, the home's
// .ssh the same). The root, /bin, /dev and /proc are the shell's listings and
// stats (moy_sh_list, moy_sh_stat_at); a virtual file does not open.
//
// THE STORE is touched on the console's task: each request is one hop
// (moy_tty_face_hop) that opens, reads or writes and closes under the
// board's gate (moy_vol_gate_enter), so nothing is open between requests and
// no hop carries more than one request's data (IO_MAX). A write appends: its
// offset must be the file's size, as a client's upload's always is.
//
// THE SHELF follows: a change under the store's carts folder notes the cart
// folder it touched, and once the client has sent nothing for IDLE_MS, or at
// the session's end, the door's `rescan` hands the names to the console
// (runtime/shell/sh_door.py), which re-reads those folders alone.
//
// Served: open, close, read, write, stat, lstat, fstat, setstat and fsetstat
// (accepted and ignored), opendir, readdir, remove, mkdir, rmdir, realpath,
// rename, and the extensions posix-rename@openssh.com and limits@openssh.com.
// Refused as unsupported: readlink, symlink and every other extension.

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "moy_sh.h"
#include "moy_sh_board.h"
#include "moy_tty.h"
#include "moy_vol.h"

#define PKT_MAX (34u * 1024u)           // the longest request taken
#define IO_MAX (32u * 1024u)            // a read's or a write's bytes at most
#define OUT_MAX (IO_MAX + 64u)
#define HANDLES 8
#define PATH 512
#define LIST_MAX (256u * 1024u)         // a folder's listing, at most
#define NOTES 8                         // cart folders noted before a rescan of all
#define NOTE_NAME 64
#define IDLE_MS 300                     // the client's quiet before the shelf is told

enum {
    FXP_INIT = 1, FXP_VERSION = 2, FXP_OPEN = 3, FXP_CLOSE = 4, FXP_READ = 5,
    FXP_WRITE = 6, FXP_LSTAT = 7, FXP_FSTAT = 8, FXP_SETSTAT = 9, FXP_FSETSTAT = 10,
    FXP_OPENDIR = 11, FXP_READDIR = 12, FXP_REMOVE = 13, FXP_MKDIR = 14,
    FXP_RMDIR = 15, FXP_REALPATH = 16, FXP_STAT = 17, FXP_RENAME = 18,
    FXP_EXTENDED = 200,
    FXP_STATUS = 101, FXP_HANDLE = 102, FXP_DATA = 103, FXP_NAME = 104,
    FXP_ATTRS = 105, FXP_EXTENDED_REPLY = 201,
};

enum {
    FX_OK = 0, FX_EOF = 1, FX_NO_SUCH_FILE = 2, FX_PERMISSION_DENIED = 3,
    FX_FAILURE = 4, FX_BAD_MESSAGE = 5, FX_OP_UNSUPPORTED = 8,
};

#define PF_READ 0x01
#define PF_WRITE 0x02
#define PF_CREAT 0x08
#define PF_TRUNC 0x10
#define PF_EXCL 0x20

#define A_SIZE 0x01
#define A_PERMISSIONS 0x04
#define A_ACMODTIME 0x08

#define E_NOT_END 1000                  // a write not at the file's end
#define E_NOTEMPTY 39                   // the store's (Linux's) numbering

typedef struct {
    int used, dir, write, changed;
    uint32_t gen;
    char abs[PATH];                     // the shell's path
    char sp[PATH];                      // the store's, for a file
    uint8_t *list;                      // a folder's names: [is_dir][size, 4][name NUL]...
    size_t list_n, list_cap, list_at;
} handle_t;

typedef struct {
    moy_tty_t *f;
    moy_sh_t *s;
    uint8_t *in;
    uint32_t in_n, at;                  // the request's bytes, the parse's place
    int bad;                            // the parse ran past its end
    uint8_t *out;
    size_t on;
    uint32_t gen;
    handle_t h[HANDLES];
    char carts[PATH];                   // the shelf's folder, a shell path
    size_t carts_n;
    char notes[NOTES][NOTE_NAME];
    int nnotes, all;
} sftp_t;

// -- the request -------------------------------------------------------------------

static uint32_t get_u32(sftp_t *x) {
    if (x->at + 4 > x->in_n) {
        x->bad = 1;
        return 0;
    }
    const uint8_t *p = x->in + x->at;
    x->at += 4;
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static uint64_t get_u64(sftp_t *x) {
    uint64_t hi = get_u32(x);
    return hi << 32 | get_u32(x);
}

static const uint8_t *get_str(sftp_t *x, uint32_t *n) {
    *n = get_u32(x);
    if (x->bad || *n > x->in_n - x->at) {
        x->bad = 1;
        *n = 0;
        return (const uint8_t *)"";
    }
    const uint8_t *p = x->in + x->at;
    x->at += *n;
    return p;
}

// A string as a C string into `out`: 0, or -1 when it holds a NUL or is too long.
static int get_cstr(sftp_t *x, char *out, size_t cap) {
    uint32_t n;
    const uint8_t *p = get_str(x, &n);
    if (x->bad || n >= cap || memchr(p, 0, n) != NULL) {
        return -1;
    }
    memcpy(out, p, n);
    out[n] = 0;
    return 0;
}

// -- the answer ----------------------------------------------------------------------

static void put_u32(sftp_t *x, uint32_t v) {
    if (x->on + 4 <= OUT_MAX) {
        uint8_t *p = x->out + x->on;
        p[0] = (uint8_t)(v >> 24);
        p[1] = (uint8_t)(v >> 16);
        p[2] = (uint8_t)(v >> 8);
        p[3] = (uint8_t)v;
        x->on += 4;
    }
}

static void put_u64(sftp_t *x, uint64_t v) {
    put_u32(x, (uint32_t)(v >> 32));
    put_u32(x, (uint32_t)v);
}

static void put_str(sftp_t *x, const void *p, size_t n) {
    if (x->on + 4 + n <= OUT_MAX) {
        put_u32(x, (uint32_t)n);
        memcpy(x->out + x->on, p, n);
        x->on += n;
    }
}

static void begin(sftp_t *x, int type, uint32_t id) {
    x->on = 4;
    x->out[x->on++] = (uint8_t)type;
    put_u32(x, id);
}

static void send_out(sftp_t *x) {
    uint32_t n = (uint32_t)x->on - 4;
    x->out[0] = (uint8_t)(n >> 24);
    x->out[1] = (uint8_t)(n >> 16);
    x->out[2] = (uint8_t)(n >> 8);
    x->out[3] = (uint8_t)n;
    moy_tty_raw(x->f, (const char *)x->out, x->on);
    x->on = 0;
}

static void status(sftp_t *x, uint32_t id, int code, const char *msg) {
    begin(x, FXP_STATUS, id);
    put_u32(x, (uint32_t)code);
    put_str(x, msg, strlen(msg));
    put_str(x, "en", 2);
    send_out(x);
}

static void status_err(sftp_t *x, uint32_t id, int err) {
    switch (err) {
        case 0:
            status(x, id, FX_OK, "Success");
            return;
        case ENOENT:
            status(x, id, FX_NO_SUCH_FILE, "No such file");
            return;
        case MOY_EACCES:
        case EPERM:
        case MOY_EROFS:
            status(x, id, FX_PERMISSION_DENIED, "Permission denied");
            return;
        case MOY_EEXIST:
            status(x, id, FX_FAILURE, "File exists");
            return;
        case MOY_EISDIR:
            status(x, id, FX_FAILURE, "Is a folder");
            return;
        case ENOTDIR:
            status(x, id, FX_FAILURE, "Not a folder");
            return;
        case E_NOTEMPTY:
            status(x, id, FX_FAILURE, "Folder not empty");
            return;
        case MOY_EXDEV:
            status(x, id, FX_FAILURE, "Another volume");
            return;
        case MOY_ENOSPC:
            status(x, id, FX_FAILURE, "No space left");
            return;
        case E_NOT_END:
            status(x, id, FX_OP_UNSUPPORTED, "A write appends at the file's end");
            return;
        default:
            status(x, id, FX_FAILURE, "Failure");
            return;
    }
}

static void put_attrs(sftp_t *x, const moy_sh_stat_t *st) {
    put_u32(x, A_SIZE | A_PERMISSIONS | (st->mtime ? A_ACMODTIME : 0));
    put_u64(x, st->size);
    put_u32(x, st->is_dir ? 040755 : 0100644);
    if (st->mtime) {
        put_u32(x, (uint32_t)st->mtime);
        put_u32(x, (uint32_t)st->mtime);
    }
}

// `ls -l`'s line, which OpenSSH's client shows as it is; no date, a listing
// has none.
static void put_entry(sftp_t *x, const char *name, const moy_sh_stat_t *st) {
    char line[PATH + 64];
    int n = snprintf(line, sizeof line, "%s    1 moy      moy      %8lu %s",
                     st->is_dir ? "drwxr-xr-x" : "-rw-r--r--", (unsigned long)st->size, name);
    size_t k = strlen(name);
    put_str(x, name, k);
    put_str(x, line, n > 0 && (size_t)n < sizeof line ? (size_t)n : 0);
    put_attrs(x, st);
}

// -- paths ---------------------------------------------------------------------------

// The request's path made the shell's absolute one: 0, or an errno value.
static int get_path(sftp_t *x, char *abs) {
    char raw[PATH];
    if (get_cstr(x, raw, sizeof raw) != 0) {
        return MOY_EINVAL;
    }
    return moy_sh_abs(x->s, raw[0] ? raw : ".", abs, PATH);
}

// The store's path of shell path `abs`: 0, or ENOENT for a virtual one.
static int store_of(sftp_t *x, const char *abs, char *sp) {
    return moy_sh_resolve(x->s, abs, sp, PATH);
}

// -- the shelf -----------------------------------------------------------------------

static void note(sftp_t *x, const char *abs) {
    if (x->carts_n == 0 || strncmp(abs, x->carts, x->carts_n) != 0 || abs[x->carts_n] != '/') {
        return;
    }
    const char *name = abs + x->carts_n + 1;
    size_t n = strcspn(name, "/");
    if (n == 0) {
        return;
    }
    if (n >= NOTE_NAME) {
        x->all = 1;
        return;
    }
    for (int i = 0; i < x->nnotes; i++) {
        if (strlen(x->notes[i]) == n && !memcmp(x->notes[i], name, n)) {
            return;
        }
    }
    if (x->nnotes == NOTES) {
        x->all = 1;
        return;
    }
    memcpy(x->notes[x->nnotes], name, n);
    x->notes[x->nnotes][n] = 0;
    x->nnotes++;
}

static void tell_shelf(sftp_t *x) {
    if (x->nnotes == 0 && !x->all) {
        return;
    }
    char *names[NOTES];
    int n = x->all ? 0 : x->nnotes;
    for (int i = 0; i < n; i++) {
        names[i] = x->notes[i];
    }
    moy_sh_board_door(x->s, "rescan", n, names);
    x->nnotes = 0;
    x->all = 0;
}

// -- the store, on the console's task --------------------------------------------------

enum { IO_CREATE, IO_READ, IO_WRITE, IO_LIST, IO_MKDIR, IO_REMOVE, IO_RMDIR, IO_RENAME,
       IO_REPLACE };

typedef struct {
    int op;
    const char *path, *path2;
    uint32_t pflags;
    uint64_t off;
    uint8_t *buf;
    size_t n, got;
    handle_t *h;
    int r;
} io_t;

static int list_ent(void *ctx, const char *name, size_t len, int is_dir, uint32_t size) {
    handle_t *h = ctx;
    size_t need = h->list_n + 6 + len;
    if (need > LIST_MAX) {
        return 0;
    }
    if (need > h->list_cap) {
        size_t cap = h->list_cap ? h->list_cap * 2 : 1024;
        while (cap < need) {
            cap *= 2;
        }
        uint8_t *p = moy_tty_calloc(1, cap);
        if (p == NULL) {
            return MOY_ENOMEM;
        }
        if (h->list_n) {
            memcpy(p, h->list, h->list_n);
        }
        moy_tty_free(h->list);
        h->list = p;
        h->list_cap = cap;
    }
    uint8_t *p = h->list + h->list_n;
    p[0] = (uint8_t)is_dir;
    memcpy(p + 1, &size, 4);
    memcpy(p + 5, name, len);
    p[5 + len] = 0;
    h->list_n = need;
    return 0;
}

static int io_create(const moy_vol_t *v, const char *rest, uint32_t pf) {
    moy_vol_stat_t st;
    int there = moy_vol_stat(v, rest, &st) == 0;
    if (there && st.is_dir) {
        return MOY_EISDIR;
    }
    if (there && (pf & PF_EXCL) && (pf & PF_CREAT)) {
        return MOY_EEXIST;
    }
    if (!there && !(pf & PF_CREAT)) {
        return ENOENT;
    }
    if (there && !(pf & PF_TRUNC)) {
        return 0;
    }
    moy_vol_file_t *f;
    int r = moy_vol_open(v, rest, MOY_VOL_WRITE, &f);
    return r != 0 ? r : moy_vol_close(f);
}

static int io_read(const moy_vol_t *v, const char *rest, io_t *c) {
    moy_vol_file_t *f;
    uint32_t size = 0;
    int r = moy_vol_open(v, rest, MOY_VOL_READ, &f);
    if (r != 0) {
        return r;
    }
    r = moy_vol_size(f, &size);
    if (r == 0 && c->off < size) {
        r = moy_vol_seek(f, (uint32_t)c->off);
        while (r == 0 && c->got < c->n) {
            size_t k = 0;
            r = moy_vol_read(f, c->buf + c->got, c->n - c->got, &k);
            if (k == 0) {
                break;
            }
            c->got += k;
        }
    }
    int e = moy_vol_close(f);
    return r != 0 ? r : e;
}

static int io_write(const moy_vol_t *v, const char *rest, io_t *c) {
    moy_vol_file_t *f;
    uint32_t size = 0;
    int r = moy_vol_open(v, rest, MOY_VOL_APPEND, &f);
    if (r != 0) {
        return r;
    }
    r = moy_vol_size(f, &size);
    if (r == 0 && c->off != size) {
        r = E_NOT_END;
    }
    if (r == 0 && c->n) {
        r = moy_vol_write(f, c->buf, c->n);
    }
    int e = moy_vol_close(f);
    return r != 0 ? r : e;
}

static void io_here(void *arg) {
    io_t *c = arg;
    moy_vol_t v, v2;
    const char *rest, *rest2;
    moy_vol_stat_t st;
    int entered = moy_vol_gate_enter(c->path);
    int r = moy_vol_at(c->path, &v, &rest);
    if (r == 0) {
        switch (c->op) {
            case IO_CREATE:
                r = io_create(&v, rest, c->pflags);
                break;
            case IO_READ:
                r = io_read(&v, rest, c);
                break;
            case IO_WRITE:
                r = io_write(&v, rest, c);
                break;
            case IO_LIST:
                r = moy_vol_list(&v, rest, list_ent, c->h);
                break;
            case IO_MKDIR:
                r = moy_vol_mkdir(&v, rest);
                break;
            case IO_REMOVE:
            case IO_RMDIR:
                r = moy_vol_stat(&v, rest, &st);
                if (r == 0 && st.is_dir != (c->op == IO_RMDIR)) {
                    r = st.is_dir ? MOY_EISDIR : ENOTDIR;
                }
                if (r == 0) {
                    r = moy_vol_remove(&v, rest);
                }
                break;
            default:
                r = moy_vol_at(c->path2, &v2, &rest2);
                if (r == 0 && (v2.kind != v.kind || v2.fs != v.fs)) {
                    r = MOY_EXDEV;
                }
                if (r == 0) {
                    r = c->op == IO_RENAME ? moy_vol_rename(&v, rest, rest2)
                                           : moy_vol_replace(&v, rest, rest2);
                }
                break;
        }
    }
    moy_vol_gate_leave(entered);
    c->r = r;
}

static int io(sftp_t *x, io_t *c) {
    moy_tty_face_hop(x->f, io_here, c);
    return c->r;
}

// -- handles ---------------------------------------------------------------------------

static handle_t *new_handle(sftp_t *x, uint32_t *idx) {
    for (int i = 0; i < HANDLES; i++) {
        if (!x->h[i].used) {
            handle_t *h = &x->h[i];
            memset(h, 0, sizeof *h);
            h->used = 1;
            h->gen = ++x->gen;
            *idx = (uint32_t)i;
            return h;
        }
    }
    return NULL;
}

static void put_handle(sftp_t *x, uint32_t id, uint32_t idx) {
    uint8_t b[8];
    uint32_t g = x->h[idx].gen;
    memcpy(b, &idx, 4);
    memcpy(b + 4, &g, 4);
    begin(x, FXP_HANDLE, id);
    put_str(x, b, sizeof b);
    send_out(x);
}

static handle_t *get_handle(sftp_t *x) {
    uint32_t n, idx, g;
    const uint8_t *p = get_str(x, &n);
    if (n != 8) {
        return NULL;
    }
    memcpy(&idx, p, 4);
    memcpy(&g, p + 4, 4);
    if (idx >= HANDLES || !x->h[idx].used || x->h[idx].gen != g) {
        return NULL;
    }
    return &x->h[idx];
}

static void drop_handle(sftp_t *x, handle_t *h) {
    if (h->changed) {
        note(x, h->abs);
    }
    moy_tty_free(h->list);
    h->list = NULL;
    h->used = 0;
}

// -- the requests ----------------------------------------------------------------------

static void do_open(sftp_t *x, uint32_t id) {
    char abs[PATH], sp[PATH];
    int r = get_path(x, abs);
    uint32_t pf = get_u32(x);
    if (r == 0) {
        r = store_of(x, abs, sp);
        r = r == ENOENT ? MOY_EACCES : r;
    }
    if (r == 0 && !(pf & PF_WRITE)) {
        moy_sh_stat_t st;
        r = moy_sh_stat_at(x->s, abs, &st);
        if (r == 0 && st.is_dir) {
            r = MOY_EISDIR;
        }
    }
    if (r == 0 && (pf & PF_WRITE)) {
        io_t c = { IO_CREATE, sp, NULL, pf, 0, NULL, 0, 0, NULL, 0 };
        r = io(x, &c);
    }
    uint32_t idx = 0;
    handle_t *h = r == 0 ? new_handle(x, &idx) : NULL;
    if (r == 0 && h == NULL) {
        status(x, id, FX_FAILURE, "Too many open files");
        return;
    }
    if (r != 0) {
        status_err(x, id, r);
        return;
    }
    strcpy(h->abs, abs);
    strcpy(h->sp, sp);
    h->write = (pf & PF_WRITE) != 0;
    h->changed = h->write && (pf & (PF_CREAT | PF_TRUNC));
    put_handle(x, id, idx);
}

static void do_read(sftp_t *x, uint32_t id) {
    handle_t *h = get_handle(x);
    uint64_t off = get_u64(x);
    uint32_t len = get_u32(x);
    if (h == NULL || h->dir) {
        status(x, id, FX_FAILURE, "Bad handle");
        return;
    }
    len = len < IO_MAX ? len : IO_MAX;
    // the data goes straight into the answer, past its header
    begin(x, FXP_DATA, id);
    io_t c = { IO_READ, h->sp, NULL, 0, off, x->out + x->on + 4, len, 0, NULL, 0 };
    int r = io(x, &c);
    if (r != 0) {
        status_err(x, id, r);
        return;
    }
    if (c.got == 0) {
        status(x, id, FX_EOF, "End of file");
        return;
    }
    put_u32(x, (uint32_t)c.got);
    x->on += c.got;
    send_out(x);
}

static void do_write(sftp_t *x, uint32_t id) {
    handle_t *h = get_handle(x);
    uint64_t off = get_u64(x);
    uint32_t n;
    const uint8_t *p = get_str(x, &n);
    if (h == NULL || h->dir || !h->write) {
        status(x, id, FX_FAILURE, "Bad handle");
        return;
    }
    io_t c = { IO_WRITE, h->sp, NULL, 0, off, (uint8_t *)p, n, 0, NULL, 0 };
    int r = io(x, &c);
    if (r == 0) {
        h->changed = 1;
    }
    status_err(x, id, r);
}

static void do_stat(sftp_t *x, uint32_t id, const char *abs) {
    moy_sh_stat_t st;
    int r = moy_sh_stat_at(x->s, abs, &st);
    if (r != 0) {
        status_err(x, id, r);
        return;
    }
    begin(x, FXP_ATTRS, id);
    put_attrs(x, &st);
    send_out(x);
}

static int root_ent(void *arg, const char *name, int is_dir, uint32_t size) {
    return list_ent(arg, name, strlen(name), is_dir, size);
}

static void do_opendir(sftp_t *x, uint32_t id) {
    char abs[PATH], sp[PATH];
    moy_sh_stat_t st;
    int r = get_path(x, abs);
    if (r == 0) {
        r = moy_sh_stat_at(x->s, abs, &st);
    }
    if (r == 0 && !st.is_dir) {
        r = ENOTDIR;
    }
    uint32_t idx = 0;
    handle_t *h = r == 0 ? new_handle(x, &idx) : NULL;
    if (r == 0 && h == NULL) {
        status(x, id, FX_FAILURE, "Too many open files");
        return;
    }
    if (r == 0) {
        h->dir = 1;
        strcpy(h->abs, abs);
        // the root and the virtual folders are the shell's listing; a store
        // folder is listed with its files' sizes
        if (strcmp(abs, "/") == 0 || store_of(x, abs, sp) != 0) {
            r = moy_sh_list(x->s, abs, root_ent, h);
        } else {
            io_t c = { IO_LIST, sp, NULL, 0, 0, NULL, 0, 0, h, 0 };
            r = io(x, &c);
        }
        if (r != 0) {
            drop_handle(x, h);
        }
    }
    if (r != 0) {
        status_err(x, id, r);
        return;
    }
    put_handle(x, id, idx);
}

static void do_readdir(sftp_t *x, uint32_t id) {
    handle_t *h = get_handle(x);
    if (h == NULL || !h->dir) {
        status(x, id, FX_FAILURE, "Bad handle");
        return;
    }
    if (h->list_at >= h->list_n) {
        status(x, id, FX_EOF, "End of folder");
        return;
    }
    begin(x, FXP_NAME, id);
    size_t count_at = x->on;
    put_u32(x, 0);
    uint32_t count = 0;
    while (h->list_at < h->list_n && x->on + 2 * PATH + 128 < OUT_MAX && count < 128) {
        const uint8_t *p = h->list + h->list_at;
        moy_sh_stat_t st = { p[0], 0, 0 };
        memcpy(&st.size, p + 1, 4);
        const char *name = (const char *)p + 5;
        h->list_at += 6 + strlen(name);
        put_entry(x, name, &st);
        count++;
    }
    size_t end = x->on;
    x->on = count_at;
    put_u32(x, count);
    x->on = end;
    send_out(x);
}

static void do_realpath(sftp_t *x, uint32_t id) {
    char abs[PATH];
    int r = get_path(x, abs);
    if (r != 0) {
        status_err(x, id, r);
        return;
    }
    begin(x, FXP_NAME, id);
    put_u32(x, 1);
    put_str(x, abs, strlen(abs));
    put_str(x, abs, strlen(abs));
    put_u32(x, 0);
    send_out(x);
}

// One path's change (mkdir, remove, rmdir).
static void do_change(sftp_t *x, uint32_t id, int op) {
    char abs[PATH], sp[PATH];
    int r = get_path(x, abs);
    if (r == 0) {
        r = store_of(x, abs, sp);
        r = r == ENOENT ? MOY_EACCES : r;
    }
    if (r == 0) {
        io_t c = { op, sp, NULL, 0, 0, NULL, 0, 0, NULL, 0 };
        r = io(x, &c);
    }
    if (r == 0) {
        note(x, abs);
    }
    status_err(x, id, r);
}

static void do_rename(sftp_t *x, uint32_t id, int op) {
    char abs[PATH], abs2[PATH], sp[PATH], sp2[PATH];
    int r = get_path(x, abs);
    if (r == 0) {
        r = get_path(x, abs2);
    }
    if (r == 0) {
        r = store_of(x, abs, sp);
    }
    if (r == 0) {
        r = store_of(x, abs2, sp2);
    }
    r = r == ENOENT ? MOY_EACCES : r;
    if (r == 0) {
        io_t c = { op, sp, sp2, 0, 0, NULL, 0, 0, NULL, 0 };
        r = io(x, &c);
    }
    if (r == 0) {
        note(x, abs);
        note(x, abs2);
    }
    status_err(x, id, r);
}

static void do_extended(sftp_t *x, uint32_t id) {
    char name[64];
    if (get_cstr(x, name, sizeof name) != 0) {
        status(x, id, FX_BAD_MESSAGE, "Bad message");
    } else if (strcmp(name, "posix-rename@openssh.com") == 0) {
        do_rename(x, id, IO_REPLACE);
    } else if (strcmp(name, "limits@openssh.com") == 0) {
        begin(x, FXP_EXTENDED_REPLY, id);
        put_u64(x, PKT_MAX);
        put_u64(x, IO_MAX);
        put_u64(x, IO_MAX);
        put_u64(x, HANDLES);
        send_out(x);
    } else {
        status(x, id, FX_OP_UNSUPPORTED, "Unsupported");
    }
}

static void request(sftp_t *x) {
    int type = x->in[0];
    x->at = 1;
    x->bad = 0;
    if (type == FXP_INIT) {
        x->on = 4;
        x->out[x->on++] = FXP_VERSION;
        put_u32(x, 3);
        put_str(x, "posix-rename@openssh.com", 24);
        put_str(x, "1", 1);
        put_str(x, "limits@openssh.com", 18);
        put_str(x, "1", 1);
        send_out(x);
        return;
    }
    uint32_t id = get_u32(x);
    char abs[PATH];
    handle_t *h;
    int r;
    switch (type) {
        case FXP_OPEN:
            do_open(x, id);
            break;
        case FXP_CLOSE:
            h = get_handle(x);
            if (h == NULL) {
                status(x, id, FX_FAILURE, "Bad handle");
            } else {
                drop_handle(x, h);
                status(x, id, FX_OK, "Success");
            }
            break;
        case FXP_READ:
            do_read(x, id);
            break;
        case FXP_WRITE:
            do_write(x, id);
            break;
        case FXP_STAT:
        case FXP_LSTAT:
            r = get_path(x, abs);
            if (r != 0) {
                status_err(x, id, r);
            } else {
                do_stat(x, id, abs);
            }
            break;
        case FXP_FSTAT:
            h = get_handle(x);
            if (h == NULL) {
                status(x, id, FX_FAILURE, "Bad handle");
            } else {
                do_stat(x, id, h->abs);
            }
            break;
        case FXP_SETSTAT:
        case FXP_FSETSTAT:
            // the store keeps no modes, owners or times to set
            status(x, id, FX_OK, "Success");
            break;
        case FXP_OPENDIR:
            do_opendir(x, id);
            break;
        case FXP_READDIR:
            do_readdir(x, id);
            break;
        case FXP_REMOVE:
            do_change(x, id, IO_REMOVE);
            break;
        case FXP_MKDIR:
            do_change(x, id, IO_MKDIR);
            break;
        case FXP_RMDIR:
            do_change(x, id, IO_RMDIR);
            break;
        case FXP_REALPATH:
            do_realpath(x, id);
            break;
        case FXP_RENAME:
            do_rename(x, id, IO_RENAME);
            break;
        case FXP_EXTENDED:
            do_extended(x, id);
            break;
        default:
            status(x, id, FX_OP_UNSUPPORTED, "Unsupported");
            break;
    }
}

// -- the session -----------------------------------------------------------------------

// `n` bytes of the stream: 0, or -1 at its end.
static int take(sftp_t *x, uint8_t *p, size_t n) {
    while (n > 0) {
        long r = moy_tty_in(x->f, p, n);
        if (r == 0) {
            return -1;
        }
        if (r > 0) {
            p += r;
            n -= (size_t)r;
        }
    }
    return 0;
}

int moy_tty_sftp(moy_tty_t *f, moy_sh_t *s) {
    sftp_t *x = moy_tty_calloc(1, sizeof *x);
    uint8_t *in = moy_tty_calloc(1, PKT_MAX);
    uint8_t *out = moy_tty_calloc(1, OUT_MAX);
    int status = 0;
    if (x == NULL || in == NULL || out == NULL) {
        status = 1;
        goto done;
    }
    x->f = f;
    x->s = s;
    x->in = in;
    x->out = out;
    int n = snprintf(x->carts, sizeof x->carts, "%s/carts", moy_sh_board_store(s));
    x->carts_n = n > 0 && (size_t)n < sizeof x->carts ? (size_t)n : 0;
    for (;;) {
        if ((x->nnotes || x->all) && !moy_tty_ready(f, IDLE_MS)) {
            tell_shelf(x);
        }
        uint8_t len[4];
        if (take(x, len, 4) != 0) {
            break;
        }
        uint32_t k = (uint32_t)len[0] << 24 | (uint32_t)len[1] << 16 | (uint32_t)len[2] << 8 | len[3];
        if (k == 0 || k > PKT_MAX) {
            status = 1;             // no request is this long: the stream is lost
            break;
        }
        if (take(x, in, k) != 0) {
            break;
        }
        x->in_n = k;
        request(x);
    }
    for (int i = 0; i < HANDLES; i++) {
        if (x->h[i].used) {
            drop_handle(x, &x->h[i]);
        }
    }
    tell_shelf(x);
done:
    moy_tty_free(out);
    moy_tty_free(in);
    moy_tty_free(x);
    return status;
}
