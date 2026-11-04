// moy_sh_cmds: the shell's C core built-ins (docs/os_2026-11/system.md
// section 1): the file commands over the store, and ps, free, df, log/dmesg,
// reboot, date, sleep, clear and help over the host's tables. Each is one
// function taking dash's (argc, argv), its options read with dash's nextopt,
// writing through dash's output to the process's descriptors, so redirection
// and pipes reach it as they reach any built-in. They keep working while the
// VM is stopped: nothing here calls into Python.

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "shell.h"
#include "builtins.h"
#include "error.h"
#include "eval.h"
#include "memalloc.h"
#include "mystring.h"
#include "options.h"
#include "output.h"
#include "system.h"

#include "moy_sh_int.h"
#include "moy_vol.h"

#define CMD_BUF 4096

// --- helpers ----------------------------------------------------------------

static int resolve(const char *path, char *abs, size_t cap) {
    int r = moy_sh_path(path, abs, cap);
    if (r != 0) {
        errno = r;
        return -1;
    }
    return 0;
}

// The store op `op` on `path` (and `to`): 0 or an errno.
enum { V_MKDIR, V_RMDIR, V_REMOVE, V_RENAME };

static int vol_op(int op, const char *path, const char *to) {
    char abs[MOY_SH_PATH_MAX], sp[MOY_SH_PATH_MAX], abs2[MOY_SH_PATH_MAX], sp2[MOY_SH_PATH_MAX];
    if (moy_sh_path(path, abs, sizeof abs) != 0) {
        return ENAMETOOLONG;
    }
    const char *p = moy_sh_store_path(abs, sp, sizeof sp);
    if (p == NULL) {
        return EROFS;
    }
    moy_vol_t v, v2;
    const char *rest, *rest2;
    int entered = moy_vol_gate_enter(p);
    int r = moy_vol_at(p, &v, &rest);
    if (r == 0) {
        switch (op) {
            case V_MKDIR:
                r = moy_vol_mkdir(&v, rest);
                break;
            case V_RMDIR:
            case V_REMOVE:
                r = moy_vol_remove(&v, rest);
                break;
            case V_RENAME: {
                const char *q = NULL;
                if (moy_sh_path(to, abs2, sizeof abs2) == 0) {
                    q = moy_sh_store_path(abs2, sp2, sizeof sp2);
                }
                if (q == NULL) {
                    r = EROFS;
                    break;
                }
                r = moy_vol_at(q, &v2, &rest2);
                if (r == 0 && (v2.kind != v.kind || v2.fs != v.fs)) {
                    r = EXDEV;
                }
                if (r == 0) {
                    r = moy_vol_rename(&v, rest, rest2);
                }
                break;
            }
        }
    }
    moy_vol_gate_leave(entered);
    return r;
}

static int is_dir(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static const char *base_name(const char *p) {
    const char *s = strrchr(p, '/');
    return s != NULL && s[1] ? s + 1 : p;
}

// Copy what `fd` reads to stdout.
static int pump(int fd) {
    char buf[512];
    for (;;) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n < 0) {
            return -1;
        }
        if (n == 0) {
            return 0;
        }
        outmem(buf, (size_t)n, out1);
        if (moy_sh_poll()) {
            return -1;
        }
    }
}

// Read all of `fd` into the stack string; answers its length.
static size_t slurp(int fd, char **out) {
    char buf[512];
    char *p;
    size_t len = 0;
    STARTSTACKSTR(p);
    for (;;) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n <= 0) {
            break;
        }
        p = stnputs(buf, (size_t)n, p);
        len += (size_t)n;
    }
    STACKSTRNUL(p);
    *out = stackblock();
    return len;
}

// Each operand opened for reading, "-" or none being stdin: fn(fd, name).
static int each_input(char **args, int (*fn)(int fd, const char *name, void *arg), void *arg) {
    int status = 0;
    if (*args == NULL) {
        return fn(0, "-", arg);
    }
    for (; *args; args++) {
        int fd = strcmp(*args, "-") == 0 ? 0 : open(*args, O_RDONLY);
        if (fd < 0) {
            status |= moy_sh_fail("%s: %s: %s", commandname, *args, strerror(errno));
            continue;
        }
        status |= fn(fd, *args, arg);
        if (fd != 0) {
            close(fd);
        }
    }
    return status;
}

// --- files ------------------------------------------------------------------

static int cat_one(int fd, const char *name, void *arg) {
    (void)name;
    (void)arg;
    return pump(fd) < 0;
}

static int catcmd(int argc, char **argv) {
    (void)argc;
    while (nextopt("u") != '\0') {
    }
    return each_input(argptr, cat_one, NULL);
}

typedef struct {
    char *name;
    int is_dir;
    uint32_t size;
    time_t mtime;
} ls_ent_t;

static int ls_cmp(const void *a, const void *b) {
    return strcmp(((const ls_ent_t *)a)->name, ((const ls_ent_t *)b)->name);
}

static void ls_print(const ls_ent_t *e, int n, int lflag, int one) {
    if (lflag) {
        for (int i = 0; i < n; i++) {
            char when[20] = "";
            if (e[i].mtime > 0) {
                struct tm tm;
                time_t t = e[i].mtime;
                gmtime_r(&t, &tm);
                strftime(when, sizeof when, "%Y-%m-%d %H:%M", &tm);
            }
            out1fmt("%c %9lu %16s %s%s\n", e[i].is_dir ? 'd' : '-',
                    (unsigned long)e[i].size, when, e[i].name, e[i].is_dir ? "/" : "");
        }
        return;
    }
    for (int i = 0; i < n; i++) {
        out1fmt("%s%s%s", e[i].name, e[i].is_dir && !one ? "/" : "", one ? "\n" : (i + 1 < n ? "  " : "\n"));
    }
}

static int ls_dir(const char *path, int all, int lflag, int one) {
    DIR *d = opendir(path);
    struct dirent *de;
    if (d == NULL) {
        return moy_sh_fail("ls: %s: %s", path, strerror(errno));
    }
    int n = 0, cap = 32;
    ls_ent_t *e = stalloc(cap * sizeof *e);
    while ((de = readdir(d)) != NULL) {
        if (!all && de->d_name[0] == '.') {
            continue;
        }
        if (n == cap) {
            ls_ent_t *bigger = stalloc(cap * 2 * sizeof *e);
            memcpy(bigger, e, n * sizeof *e);
            e = bigger;
            cap *= 2;
        }
        e[n].name = sstrdup(de->d_name);
        char full[MOY_SH_PATH_MAX];
        struct stat st;
        snprintf(full, sizeof full, "%s/%s", path, de->d_name);
        if (stat(full, &st) == 0) {
            e[n].is_dir = S_ISDIR(st.st_mode);
            e[n].size = (uint32_t)st.st_size;
            e[n].mtime = st.st_mtime;
        } else {
            e[n].is_dir = 0;
            e[n].size = 0;
            e[n].mtime = 0;
        }
        n++;
    }
    closedir(d);
    qsort(e, n, sizeof *e, ls_cmp);
    ls_print(e, n, lflag, one);
    return 0;
}

static int lscmd(int argc, char **argv) {
    int all = 0, lflag = 0, one = 0, c;
    (void)argc;
    while ((c = nextopt("al1")) != '\0') {
        all |= c == 'a';
        lflag |= c == 'l';
        one |= c == '1';
    }
    one |= !isatty(1);
    char **args = argptr;
    if (*args == NULL) {
        return ls_dir(".", all, lflag, one);
    }
    int status = 0, many = args[1] != NULL;
    for (; *args; args++) {
        struct stat st;
        if (stat(*args, &st) != 0) {
            status |= moy_sh_fail("ls: %s: %s", *args, strerror(errno));
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            if (many) {
                out1fmt("%s:\n", *args);
            }
            status |= ls_dir(*args, all, lflag, one);
        } else {
            ls_ent_t e = { *args, 0, (uint32_t)st.st_size, st.st_mtime };
            ls_print(&e, 1, lflag, 1);
        }
    }
    return status;
}

static int copy_file(const char *from, const char *to) {
    char dest[MOY_SH_PATH_MAX];
    if (is_dir(to)) {
        snprintf(dest, sizeof dest, "%s/%s", to, base_name(from));
        to = dest;
    }
    int in = open(from, O_RDONLY);
    if (in < 0) {
        return moy_sh_fail("cp: %s: %s", from, strerror(errno));
    }
    int out = open(to, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out < 0) {
        close(in);
        return moy_sh_fail("cp: %s: %s", to, strerror(errno));
    }
    char buf[512];
    ssize_t n;
    int status = 0;
    while ((n = read(in, buf, sizeof buf)) > 0) {
        if (write(out, buf, (size_t)n) != n) {
            status = moy_sh_fail("cp: %s: %s", to, strerror(errno));
            break;
        }
    }
    close(in);
    close(out);
    return status;
}

static int cpcmd(int argc, char **argv) {
    (void)argc;
    while (nextopt("") != '\0') {
    }
    char **args = argptr;
    int n = 0;
    while (args[n]) {
        n++;
    }
    if (n < 2) {
        return moy_sh_fail("usage: cp FROM... TO");
    }
    if (n > 2 && !is_dir(args[n - 1])) {
        return moy_sh_fail("cp: %s: not a folder", args[n - 1]);
    }
    int status = 0;
    for (int i = 0; i < n - 1; i++) {
        if (is_dir(args[i])) {
            status |= moy_sh_fail("cp: %s: is a folder", args[i]);
            continue;
        }
        status |= copy_file(args[i], args[n - 1]);
    }
    return status;
}

static int mvcmd(int argc, char **argv) {
    (void)argc;
    while (nextopt("") != '\0') {
    }
    char **args = argptr;
    int n = 0;
    while (args[n]) {
        n++;
    }
    if (n < 2) {
        return moy_sh_fail("usage: mv FROM... TO");
    }
    int status = 0;
    for (int i = 0; i < n - 1; i++) {
        char dest[MOY_SH_PATH_MAX];
        const char *to = args[n - 1];
        if (is_dir(to)) {
            snprintf(dest, sizeof dest, "%s/%s", to, base_name(args[i]));
            to = dest;
        }
        int r = vol_op(V_RENAME, args[i], to);
        if (r == EXDEV && !is_dir(args[i])) {
            r = copy_file(args[i], to) ? EIO : vol_op(V_REMOVE, args[i], NULL);
        }
        if (r != 0) {
            status |= moy_sh_fail("mv: %s: %s", args[i], strerror(r));
        }
    }
    return status;
}

static int remove_tree(const char *path) {
    if (is_dir(path)) {
        DIR *d = opendir(path);
        struct dirent *de;
        if (d == NULL) {
            return errno;
        }
        // names first: removing while listing would move the listing
        char *names;
        char *p;
        size_t len = 0;
        STARTSTACKSTR(p);
        while ((de = readdir(d)) != NULL) {
            if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, "..")) {
                continue;
            }
            size_t n = strlen(de->d_name) + 1;
            p = stnputs(de->d_name, n, p);
            len += n;
        }
        closedir(d);
        names = grabstackstr(p);
        for (size_t i = 0; i < len; i += strlen(names + i) + 1) {
            char full[MOY_SH_PATH_MAX];
            snprintf(full, sizeof full, "%s/%s", path, names + i);
            int r = remove_tree(full);
            if (r != 0) {
                return r;
            }
        }
        return vol_op(V_RMDIR, path, NULL);
    }
    return vol_op(V_REMOVE, path, NULL);
}

static int rmcmd(int argc, char **argv) {
    int rec = 0, force = 0, c, status = 0;
    (void)argc;
    while ((c = nextopt("rRf")) != '\0') {
        rec |= c == 'r' || c == 'R';
        force |= c == 'f';
    }
    for (char **args = argptr; *args; args++) {
        struct stat st;
        if (stat(*args, &st) != 0) {
            if (!force) {
                status |= moy_sh_fail("rm: %s: %s", *args, strerror(errno));
            }
            continue;
        }
        if (S_ISDIR(st.st_mode) && !rec) {
            status |= moy_sh_fail("rm: %s: is a folder", *args);
            continue;
        }
        int r = rec ? remove_tree(*args) : vol_op(V_REMOVE, *args, NULL);
        if (r != 0) {
            status |= moy_sh_fail("rm: %s: %s", *args, strerror(r));
        }
    }
    return status;
}

static int mkdirs(const char *path) {
    char abs[MOY_SH_PATH_MAX];
    if (resolve(path, abs, sizeof abs) != 0) {
        return errno;
    }
    for (char *p = abs + 1; ; p++) {
        if (*p == '/' || *p == 0) {
            char was = *p;
            *p = 0;
            if (!is_dir(abs)) {
                int r = vol_op(V_MKDIR, abs, NULL);
                if (r != 0) {
                    return r;
                }
            }
            *p = was;
            if (was == 0) {
                return 0;
            }
        }
    }
}

static int mkdircmd(int argc, char **argv) {
    int parents = 0, c, status = 0;
    (void)argc;
    while ((c = nextopt("p")) != '\0') {
        parents = 1;
    }
    for (char **args = argptr; *args; args++) {
        int r;
        if (parents) {
            r = mkdirs(*args);
        } else {
            struct stat st;
            r = stat(*args, &st) == 0 ? EEXIST : vol_op(V_MKDIR, *args, NULL);
        }
        if (r != 0) {
            status |= moy_sh_fail("mkdir: %s: %s", *args, strerror(r));
        }
    }
    return status;
}

static int rmdircmd(int argc, char **argv) {
    int status = 0;
    (void)argc;
    while (nextopt("") != '\0') {
    }
    for (char **args = argptr; *args; args++) {
        int r = is_dir(*args) ? vol_op(V_RMDIR, *args, NULL) : ENOTDIR;
        if (r != 0) {
            status |= moy_sh_fail("rmdir: %s: %s", *args, strerror(r));
        }
    }
    return status;
}

static int touchcmd(int argc, char **argv) {
    int status = 0;
    (void)argc;
    while (nextopt("") != '\0') {
    }
    for (char **args = argptr; *args; args++) {
        struct stat st;
        if (stat(*args, &st) == 0) {
            continue;
        }
        int fd = open(*args, O_WRONLY | O_CREAT, 0644);
        if (fd < 0) {
            status |= moy_sh_fail("touch: %s: %s", *args, strerror(errno));
        } else {
            close(fd);
        }
    }
    return status;
}

// head and tail: -n N lines (default 10).
static int lines_opt(void) {
    int n = 10, c;
    while ((c = nextopt("n:")) != '\0') {
        n = number(optionarg);
    }
    return n;
}

static int head_one(int fd, const char *name, void *arg) {
    int left = *(int *)arg;
    char buf[512];
    (void)name;
    while (left > 0) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n <= 0) {
            break;
        }
        ssize_t i = 0;
        while (i < n && left > 0) {
            if (buf[i++] == '\n') {
                left--;
            }
        }
        outmem(buf, (size_t)i, out1);
    }
    return 0;
}

static int headcmd(int argc, char **argv) {
    (void)argc;
    (void)argv;
    int n = lines_opt();
    return each_input(argptr, head_one, &n);
}

static int tail_one(int fd, const char *name, void *arg) {
    int want = *(int *)arg;
    char *text;
    size_t len = slurp(fd, &text);
    (void)name;
    size_t at = len;
    int seen = 0;
    if (at > 0 && text[at - 1] == '\n') {
        at--;
    }
    while (at > 0) {
        if (text[at - 1] == '\n' && ++seen == want) {
            break;
        }
        at--;
    }
    outmem(text + at, len - at, out1);
    return 0;
}

static int tailcmd(int argc, char **argv) {
    (void)argc;
    (void)argv;
    int n = lines_opt();
    return each_input(argptr, tail_one, &n);
}

typedef struct {
    int l, w, c, many;
    unsigned long tl, tw, tc;
} wc_t;

static int wc_one(int fd, const char *name, void *arg) {
    wc_t *o = arg;
    char buf[512];
    unsigned long l = 0, w = 0, c = 0;
    int inword = 0;
    ssize_t n;
    while ((n = read(fd, buf, sizeof buf)) > 0) {
        for (ssize_t i = 0; i < n; i++) {
            unsigned char ch = (unsigned char)buf[i];
            c++;
            l += ch == '\n';
            if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') {
                inword = 0;
            } else if (!inword) {
                inword = 1;
                w++;
            }
        }
    }
    o->tl += l;
    o->tw += w;
    o->tc += c;
    // one count stands alone; several line up in columns
    int all = !o->l && !o->w && !o->c;
    int cols = all ? 3 : o->l + o->w + o->c;
    const char *fmt = cols == 1 ? "%lu" : "%7lu";
    unsigned long v[3] = { l, w, c };
    int want[3] = { all || o->l, all || o->w, all || o->c };
    int first = 1;
    for (int i = 0; i < 3; i++) {
        if (want[i]) {
            if (!first) {
                out1c(' ');
            }
            out1fmt(fmt, v[i]);
            first = 0;
        }
    }
    out1fmt(strcmp(name, "-") ? " %s\n" : "\n", name);
    return 0;
}

static int wccmd(int argc, char **argv) {
    wc_t o = { 0 };
    int c;
    (void)argc;
    (void)argv;
    while ((c = nextopt("lwc")) != '\0') {
        o.l |= c == 'l';
        o.w |= c == 'w';
        o.c |= c == 'c';
    }
    return each_input(argptr, wc_one, &o);
}

typedef struct {
    const char *pat;
    int ign, inv, num, count, quiet, many;
    int matched;
} grep_t;

static int grep_has(const grep_t *g, const char *line, size_t n) {
    size_t m = strlen(g->pat);
    if (m == 0) {
        return 1;
    }
    for (size_t i = 0; i + m <= n; i++) {
        size_t j = 0;
        while (j < m) {
            unsigned char a = (unsigned char)line[i + j], b = (unsigned char)g->pat[j];
            if (g->ign) {
                a = (unsigned char)tolower(a);
                b = (unsigned char)tolower(b);
            }
            if (a != b) {
                break;
            }
            j++;
        }
        if (j == m) {
            return 1;
        }
    }
    return 0;
}

static int grep_one(int fd, const char *name, void *arg) {
    grep_t *g = arg;
    char *text;
    size_t len = slurp(fd, &text);
    unsigned long count = 0, lineno = 0;
    for (size_t at = 0; at < len;) {
        size_t end = at;
        while (end < len && text[end] != '\n') {
            end++;
        }
        lineno++;
        if (grep_has(g, text + at, end - at) != g->inv) {
            count++;
            g->matched = 1;
            if (!g->count && !g->quiet) {
                if (g->many) {
                    out1fmt("%s:", name);
                }
                if (g->num) {
                    out1fmt("%lu:", lineno);
                }
                outmem(text + at, end - at, out1);
                outcslow('\n', out1);
            }
        }
        at = end + 1;
    }
    if (g->count) {
        if (g->many) {
            out1fmt("%s:", name);
        }
        out1fmt("%lu\n", count);
    }
    return 0;
}

static int grepcmd(int argc, char **argv) {
    grep_t g = { 0 };
    int c;
    (void)argc;
    (void)argv;
    while ((c = nextopt("ivncqF")) != '\0') {
        g.ign |= c == 'i';
        g.inv |= c == 'v';
        g.num |= c == 'n';
        g.count |= c == 'c';
        g.quiet |= c == 'q';
    }
    if (*argptr == NULL) {
        moy_sh_fail("usage: grep [-ivncq] TEXT [FILE...]");
        return 2;
    }
    g.pat = *argptr++;
    g.many = argptr[0] != NULL && argptr[1] != NULL;
    int err = each_input(argptr, grep_one, &g);
    return err ? 2 : !g.matched;
}

// --- the system ---------------------------------------------------------------

static int ps_row(void *arg, const moy_sh_ps_t *p) {
    (void)arg;
    out1fmt("%5d %5d %-8s %-10s %s\n", p->pid, p->ppid, p->kind ? p->kind : "",
            p->state ? p->state : "", p->name ? p->name : "");
    return 0;
}

static int pscmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    (void)argc;
    (void)argv;
    out1fmt("%5s %5s %-8s %-10s %s\n", "PID", "PPID", "KIND", "STATE", "NAME");
    out1fmt("%5d %5d %-8s %-10s %s\n", (int)getpid(), 0, "shell", "running", "sh");
    if (s->host.procs != NULL) {
        s->host.procs(s->host.ctx, ps_row, NULL);
    }
    return 0;
}

static int freecmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    uint32_t m[6] = { 0 };
    (void)argc;
    (void)argv;
    if (s->host.mem == NULL || s->host.mem(s->host.ctx, m) != 0) {
        return moy_sh_fail("free: no figures here");
    }
    static const char *const rows[3] = { "internal", "psram", "python" };
    out1fmt("%-9s %10s %10s %10s\n", "", "total", "used", "free");
    for (int i = 0; i < 3; i++) {
        if (m[2 * i] == 0) {
            continue;
        }
        out1fmt("%-9s %10lu %10lu %10lu\n", rows[i], (unsigned long)m[2 * i],
                (unsigned long)(m[2 * i] - m[2 * i + 1]), (unsigned long)m[2 * i + 1]);
    }
    return 0;
}

static int dfcmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    (void)argc;
    (void)argv;
    while (nextopt("h") != '\0') {
    }
    char **args = argptr;
    const char *one[2] = { ".", NULL };
    if (*args == NULL) {
        args = (char **)one;
    }
    out1fmt("%-24s %12s %12s %12s\n", "Folder", "Size", "Used", "Free");
    int status = 0;
    for (; *args; args++) {
        char abs[MOY_SH_PATH_MAX], sp[MOY_SH_PATH_MAX];
        uint64_t total = 0, free = 0;
        const char *p = resolve(*args, abs, sizeof abs) == 0 ? moy_sh_store_path(abs, sp, sizeof sp) : NULL;
        if (p == NULL || s->host.df == NULL || s->host.df(s->host.ctx, p, &total, &free) != 0) {
            status |= moy_sh_fail("df: %s: no figures here", *args);
            continue;
        }
        out1fmt("%-24s %12llu %12llu %12llu\n", abs, (unsigned long long)total,
                (unsigned long long)(total - free), (unsigned long long)free);
    }
    return status;
}

static int logcmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    int follow = 0, c;
    (void)argc;
    (void)argv;
    while ((c = nextopt("f")) != '\0') {
        follow = 1;
    }
    if (s->host.log == NULL) {
        return moy_sh_fail("log: no log here");
    }
    flushall();
    return s->host.log(s->host.ctx, s, 1, *argptr, follow);
}

static int rebootcmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    (void)argc;
    (void)argv;
    if (s->host.reboot == NULL) {
        return moy_sh_fail("reboot: not here");
    }
    flushall();
    s->host.reboot(s->host.ctx);
    return 0;
}

static int datecmd(int argc, char **argv) {
    int utc = 0, c;
    (void)argc;
    (void)argv;
    while ((c = nextopt("u")) != '\0') {
        utc = 1;
    }
    (void)utc;
    const char *fmt = "%a %b %e %H:%M:%S UTC %Y";
    if (*argptr != NULL && argptr[0][0] == '+') {
        fmt = argptr[0] + 1;
    }
    time_t now = time(NULL);
    struct tm tm;
    char buf[128];
    gmtime_r(&now, &tm);
    size_t n = strftime(buf, sizeof buf, fmt, &tm);
    outmem(buf, n, out1);
    outcslow('\n', out1);
    return 0;
}

static int sleepcmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    if (argc != 2) {
        return moy_sh_fail("usage: sleep SECONDS");
    }
    char *end;
    double secs = strtod(argv[1], &end);
    if (end == argv[1] || *end || secs < 0) {
        return moy_sh_fail("sleep: %s: not a number", argv[1]);
    }
    long ms = (long)(secs * 1000.0);
    flushall();
    while (ms > 0) {
        int step = ms > 50 ? 50 : (int)ms;
        if (s->host.sleep_ms != NULL) {
            s->host.sleep_ms(s->host.ctx, step);
        }
        ms -= step;
        if (moy_sh_poll()) {
            moy_sh_cur->interrupt = 1;
            return 130;
        }
    }
    return 0;
}

static int clearcmd(int argc, char **argv) {
    (void)argc;
    (void)argv;
    out1str("\033[2J\033[H");
    return 0;
}

static int helpcmd(int argc, char **argv);

// Sorted by name (moy_sh_core_builtin searches it).
static const struct builtincmd core[] = {
    { "cat", catcmd, BUILTIN_REGULAR },
    { "clear", clearcmd, BUILTIN_REGULAR },
    { "cp", cpcmd, BUILTIN_REGULAR },
    { "date", datecmd, BUILTIN_REGULAR },
    { "df", dfcmd, BUILTIN_REGULAR },
    { "dmesg", logcmd, BUILTIN_REGULAR },
    { "free", freecmd, BUILTIN_REGULAR },
    { "grep", grepcmd, BUILTIN_REGULAR },
    { "head", headcmd, BUILTIN_REGULAR },
    { "help", helpcmd, BUILTIN_REGULAR },
    { "log", logcmd, BUILTIN_REGULAR },
    { "ls", lscmd, BUILTIN_REGULAR },
    { "mkdir", mkdircmd, BUILTIN_REGULAR },
    { "mv", mvcmd, BUILTIN_REGULAR },
    { "ps", pscmd, BUILTIN_REGULAR },
    { "reboot", rebootcmd, BUILTIN_REGULAR },
    { "rm", rmcmd, BUILTIN_REGULAR },
    { "rmdir", rmdircmd, BUILTIN_REGULAR },
    { "sh", moy_sh_shcmd, BUILTIN_REGULAR },
    { "sleep", sleepcmd, BUILTIN_REGULAR },
    { "tail", tailcmd, BUILTIN_REGULAR },
    { "touch", touchcmd, BUILTIN_REGULAR },
    { "wc", wccmd, BUILTIN_REGULAR },
};

#define NCORE ((int)(sizeof core / sizeof core[0]))

const struct builtincmd *moy_sh_core_builtin(const char *name) {
    int lo = 0, hi = NCORE - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        int c = strcmp(name, core[mid].name);
        if (c == 0) {
            return &core[mid];
        }
        if (c < 0) {
            hi = mid - 1;
        } else {
            lo = mid + 1;
        }
    }
    return NULL;
}

int moy_sh_core_names(moy_sh_ent_fn fn, void *arg, const char *prefix) {
    size_t n = strlen(prefix);
    for (int i = 0; i < NCORE; i++) {
        if (strncmp(core[i].name, prefix, n) == 0) {
            int r = fn(arg, core[i].name, 0, 0);
            if (r) {
                return r;
            }
        }
    }
    return 0;
}

static int help_name(void *arg, const char *name, int is_dir, uint32_t size) {
    int *col = arg;
    (void)is_dir;
    (void)size;
    out1fmt("%s%s", *col ? " " : "", name);
    *col += (int)strlen(name) + 1;
    if (*col > 60) {
        out1c('\n');
        *col = 0;
    }
    return 0;
}

static int helpcmd(int argc, char **argv) {
    int col = 0;
    (void)argc;
    (void)argv;
    out1str("The shell's commands (installed commands are in /bin):\n");
    moy_sh_complete(moy_sh_cur, "", help_name, &col);
    if (col) {
        out1c('\n');
    }
    return 0;
}
