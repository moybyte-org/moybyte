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

typedef struct {
    int op;
    const char *p, *q;
    int r;
} vol_call_t;

static void vol_here(void *arg);

static int vol_op(int op, const char *path, const char *to) {
    char abs[MOY_SH_PATH_MAX], sp[MOY_SH_PATH_MAX], abs2[MOY_SH_PATH_MAX], sp2[MOY_SH_PATH_MAX];
    if (moy_sh_path(path, abs, sizeof abs) != 0) {
        return ENAMETOOLONG;
    }
    const char *p = moy_sh_store_path(abs, sp, sizeof sp);
    if (p == NULL) {
        return EROFS;
    }
    const char *q = NULL;
    if (op == V_RENAME && moy_sh_path(to, abs2, sizeof abs2) == 0) {
        q = moy_sh_store_path(abs2, sp2, sizeof sp2);
    }
    vol_call_t c = { op, p, q, 0 };
    moy_sh_hop(moy_sh_cur, vol_here, &c);
    return c.r;
}

// The store's change, where the store may be touched (moy_sh_hop).
static void vol_here(void *arg) {
    vol_call_t *c = arg;
    int op = c->op;
    const char *p = c->p, *q = c->q;
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
    c->r = r;
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

static int tail_bytes(int fd, const char *name, void *arg) {
    size_t want = (size_t)*(int *)arg;
    char *text;
    size_t len = slurp(fd, &text);
    (void)name;
    size_t at = len > want ? len - want : 0;
    outmem(text + at, len - at, out1);
    return 0;
}

// -c N counts bytes; -Nc is the old spelling, which ssh-copy-id still uses
// to see whether authorized_keys ends in a newline.
static int tailcmd(int argc, char **argv) {
    (void)argc;
    const char *a = argv[1];
    if (a && a[0] == '-' && a[1] >= '0' && a[1] <= '9') {
        const char *e = a + 1;
        while (*e >= '0' && *e <= '9') {
            e++;
        }
        if (e[0] == 'c' && e[1] == '\0') {
            int n = atoi(a + 1);
            return each_input(argv + 2, tail_bytes, &n);
        }
    }
    int n = 10, c, bytes = 0;
    while ((c = nextopt("n:c:")) != '\0') {
        n = number(optionarg);
        bytes = c == 'c';
    }
    return each_input(argptr, bytes ? tail_bytes : tail_one, &n);
}

// The directory part of a path, as POSIX dirname prints it.
static int dirnamecmd(int argc, char **argv) {
    if (argc != 2) {
        return moy_sh_fail("usage: dirname PATH");
    }
    const char *p = argv[1];
    size_t n = strlen(p);
    while (n > 1 && p[n - 1] == '/') {
        n--;
    }
    while (n > 0 && p[n - 1] != '/') {
        n--;
    }
    if (n == 0) {
        out1str(".\n");
        return 0;
    }
    while (n > 1 && p[n - 1] == '/') {
        n--;
    }
    outmem(p, n, out1);
    out1c('\n');
    return 0;
}

// The last part of a path, without SUFFIX when it ends in one.
static int basenamecmd(int argc, char **argv) {
    if (argc != 2 && argc != 3) {
        return moy_sh_fail("usage: basename PATH [SUFFIX]");
    }
    const char *p = argv[1];
    size_t n = strlen(p);
    while (n > 1 && p[n - 1] == '/') {
        n--;
    }
    size_t at = n;
    while (at > 0 && p[at - 1] != '/') {
        at--;
    }
    if (n == 1 && p[0] == '/') {
        at = 0;
    }
    size_t len = n - at;
    if (argc == 3) {
        size_t sl = strlen(argv[2]);
        if (sl < len && memcmp(p + n - sl, argv[2], sl) == 0) {
            len -= sl;
        }
    }
    outmem(p + at, len, out1);
    out1c('\n');
    return 0;
}

#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#endif
#ifdef CONFIG_IDF_TARGET
#define MOY_SH_MACHINE CONFIG_IDF_TARGET
#else
#define MOY_SH_MACHINE "host"
#endif

// The system's name (-s, the default), the chip (-m), or both (-a).
static int unamecmd(int argc, char **argv) {
    int sys = 0, mach = 0, c;
    (void)argc;
    (void)argv;
    while ((c = nextopt("asm")) != '\0') {
        sys |= c == 's' || c == 'a';
        mach |= c == 'm' || c == 'a';
    }
    if (!sys && !mach) {
        sys = 1;
    }
    if (sys) {
        out1str("Moybyte");
    }
    if (mach) {
        out1str(sys ? " " MOY_SH_MACHINE : MOY_SH_MACHINE);
    }
    out1c('\n');
    return 0;
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

// `edit FILE` and `top`: a text app in front of the face (moy_sh_app.h).
static int appcmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    flushall();
    int r = s->host.app != NULL ? s->host.app(s->host.ctx, s, argc, argv) : MOY_SH_NOT_FOUND;
    if (r == MOY_SH_NOT_FOUND) {
        return moy_sh_fail("%s: needs a screen", argv[0]);
    }
    return r;
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
#ifdef ESP_PLATFORM
    now += 946684800;           // a board's clock counts from 2000 (moy_wifi.c)
#endif
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
    out1str("\033[H\033[2J\033[3J");
    return 0;
}

// -- neofetch -------------------------------------------------------------------------

#if defined(ESP_PLATFORM) && __has_include("esp_chip_info.h")
#include "esp_chip_info.h"
#include "esp_timer.h"
#define MOY_SH_FETCH_CHIP 1
#endif

// The console's palette, its first sixteen the base colours (native/moy_ui's
// generated tables): weak, NULL in an image without the toolkit (the Zero),
// where neofetch draws plain.
extern const uint32_t moy_theme_moy64[64] __attribute__((weak));

#define FETCH_ROWS 16
#define FETCH_VALUE 64
#define FETCH_LOGO_W 16
#define FETCH_LOGO_ROWS 8
#define FETCH_GAP 3                     // between the logo and the rows
#define FETCH_ACCENT 14                 // the title's and the labels' base colour

// Moy, the mascot: chrome.py's "moy" sprite ('.' clear, a hex digit a base
// colour), drawn two pixels a cell with half blocks.
static const char *const fetch_moy[16] = {
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
};

// ...and his silhouette where the terminal draws no colour.
static const char *const fetch_plain[FETCH_LOGO_ROWS] = {
    "   .-------.    ",
    "  /        |    ",
    " |         '--. ",
    " |   o   o    | ",
    " |    ---     | ",
    " |            | ",
    "  '--.----.--'  ",
    "    '-'  '-'    ",
};

// A row's rank: the rows a short terminal leaves out go highest rank first.
typedef struct {
    const char *label;
    char value[FETCH_VALUE];
    int rank;
} fetch_row_t;

typedef struct {
    fetch_row_t row[FETCH_ROWS];
    int n;
    char title[FETCH_VALUE];
} fetch_t;

static char *fetch_add(fetch_t *f, const char *label, int rank) {
    if (f->n >= FETCH_ROWS) {
        return NULL;
    }
    fetch_row_t *r = &f->row[f->n++];
    r->label = label;
    r->rank = rank;
    r->value[0] = 0;
    return r->value;
}

// `key`'s value in /proc/console's `key: value` lines, into `out`; 0 none.
static int console_row(const char *text, size_t n, const char *key, char *out, size_t cap) {
    size_t kn = strlen(key);
    for (const char *p = text, *end = text + n; p < end;) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        const char *e = nl != NULL ? nl : end;
        if ((size_t)(e - p) > kn + 2 && memcmp(p, key, kn) == 0 && p[kn] == ':' && p[kn + 1] == ' ') {
            size_t vn = (size_t)(e - p) - kn - 2;
            vn = vn < cap - 1 ? vn : cap - 1;
            memcpy(out, p + kn + 2, vn);
            out[vn] = 0;
            return vn > 0;
        }
        p = e + 1;
    }
    return 0;
}

static void fetch_console(fetch_t *f, const char *text, size_t n, const char *key,
                          const char *label, int rank) {
    char v[FETCH_VALUE];
    if (text != NULL && console_row(text, n, key, v, sizeof v)) {
        char *out = fetch_add(f, label, rank);
        if (out != NULL) {
            memcpy(out, v, strlen(v) + 1);
        }
    }
}

static void fetch_size(char *out, uint64_t b) {
    if (b < 1024u * 1024u) {
        snprintf(out, 16, "%lu KiB", (unsigned long)(b / 1024u));
    } else if (b < 1024u * 1024u * 1024u) {
        unsigned long t = (unsigned long)(b * 10u / (1024u * 1024u));
        snprintf(out, 16, "%lu.%lu MiB", t / 10u, t % 10u);
    } else {
        unsigned long t = (unsigned long)(b * 10u / (1024u * 1024u * 1024u));
        snprintf(out, 16, "%lu.%lu GiB", t / 10u, t % 10u);
    }
}

static void fetch_used(fetch_t *f, const char *label, int rank, uint64_t total, uint64_t free) {
    char *out = total > 0 ? fetch_add(f, label, rank) : NULL;
    if (out != NULL) {
        char u[16], t[16];
        fetch_size(u, total - free);
        fetch_size(t, total);
        snprintf(out, FETCH_VALUE, "%s / %s", u, t);
    }
}

static void fetch_df(fetch_t *f, const char *label, int rank, const char *path) {
    moy_sh_t *s = moy_sh_cur;
    char sp[MOY_SH_PATH_MAX];
    uint64_t total = 0, free = 0;
    const char *p = moy_sh_store_path(path, sp, sizeof sp);
    if (p != NULL && s->host.df != NULL && s->host.df(s->host.ctx, p, &total, &free) == 0) {
        fetch_used(f, label, rank, total, free);
    }
}

// The columns `p` takes (a UTF-8 character one), and `*cut` the bytes that
// fit in `room` columns.
static int fetch_width(const char *p, int room, size_t *cut) {
    int w = 0;
    size_t i = 0;
    for (; p[i]; i++) {
        if (((unsigned char)p[i] & 0xC0u) != 0x80u) {
            if (w == room) {
                break;
            }
            w++;
        }
    }
    *cut = i;
    return w;
}

static void fetch_sgr(int ground, int base) {
    uint32_t c = moy_theme_moy64[base];
    out1fmt("\033[%d;2;%u;%u;%um", ground ? 48 : 38, (unsigned)(c >> 16),
            (unsigned)((c >> 8) & 0xFF), (unsigned)(c & 0xFF));
}

static int moy_pixel(int x, int y) {
    char c = fetch_moy[y][x];
    return c == '.' ? -1 : c <= '9' ? c - '0' : c - 'a' + 10;
}

// Logo row `i` (blanks past it), FETCH_LOGO_W columns, the ink reset after.
static void fetch_logo(int colour, int i) {
    if (i < 0 || i >= FETCH_LOGO_ROWS) {
        out1fmt("%*s", FETCH_LOGO_W, "");
        return;
    }
    if (!colour) {
        out1str(fetch_plain[i]);
        return;
    }
    // two pixels a cell: the top one the ink of an upper half block, the
    // bottom one its ground (a lower half block where the top is clear)
    int ink = -1, ground = -1;          // the line before ended in a reset
    for (int x = 0; x < FETCH_LOGO_W; x++) {
        int t = moy_pixel(x, 2 * i), b = moy_pixel(x, 2 * i + 1);
        const char *glyph = "\xe2\x96\x80";
        int want_ink = t, want_ground = b;
        if (t < 0 && b < 0) {
            glyph = " ";
            want_ink = ink;
        } else if (t < 0) {
            glyph = "\xe2\x96\x84";
            want_ink = b;
            want_ground = -1;
        } else if (t == b) {
            glyph = "\xe2\x96\x88";
            want_ground = ground;       // a whole block hides its ground
        }
        if (want_ground != ground) {
            if (want_ground < 0) {
                out1str("\033[49m");
            } else {
                fetch_sgr(1, want_ground);
            }
            ground = want_ground;
        }
        if (want_ink != ink && want_ink >= 0) {
            fetch_sgr(0, want_ink);
            ink = want_ink;
        }
        out1str(glyph);
    }
    out1str("\033[0m");
}

// One line: the logo's row `logo` beside the text (`pad` 0: no logo), then
// `label` and `text` cut to what the terminal has left.
static void fetch_line(int colour, int cols, int pad, int logo, const char *label,
                       const char *text, int title) {
    int room = cols;
    if (pad > 0) {
        fetch_logo(colour, logo);
        out1fmt("%*s", FETCH_GAP, "");
        room -= pad;
    }
    char accent[32] = "";
    if (colour) {
        uint32_t c = moy_theme_moy64[FETCH_ACCENT];
        snprintf(accent, sizeof accent, "\033[1;38;2;%u;%u;%um", (unsigned)(c >> 16),
                 (unsigned)((c >> 8) & 0xFF), (unsigned)(c & 0xFF));
    }
    const char *plain = colour ? "\033[0m" : "";
    if (label != NULL && room > 0) {
        size_t cut;
        room -= fetch_width(label, room, &cut);
        out1fmt("%s%.*s%s", accent, (int)cut, label, plain);
        if (room > 0) {
            out1c(':');
            room--;
        }
        if (room > 0) {
            out1c(' ');
            room--;
        }
    }
    if (text != NULL && room > 0) {
        size_t cut;
        fetch_width(text, room, &cut);
        const char *at = title ? memchr(text, '@', cut) : NULL;
        if (at != NULL && colour) {
            // name@moybyte: the two names in the accent, as neofetch's user@host
            out1fmt("%s%.*s%s@%s%.*s%s", accent, (int)(at - text), text, plain, accent,
                    (int)(cut - (size_t)(at - text) - 1), at + 1, plain);
        } else {
            out1fmt("%.*s", (int)cut, text);
        }
    }
    out1c('\n');
}

// The base colours in two rows of eight, as neofetch's colour blocks.
static void fetch_palette(int cols, int pad, int logo, int half) {
    if (pad > 0) {
        fetch_logo(1, logo);
        out1fmt("%*s", FETCH_GAP, "");
    }
    int room = (cols - pad) / 3;
    for (int i = 0; i < 8 && i < room; i++) {
        fetch_sgr(1, half * 8 + i);
        out1str("   ");
    }
    out1str("\033[0m\n");
}

// The console at a glance: Moy beside its rows, the rows the console's VM
// knows (/proc/console) left out while it is stopped; in colour, the base
// colours under the rows, on a terminal that draws escapes, plain on the
// serial line and into a pipe. A terminal too narrow for the logo gets the
// rows alone, one too short for them all its highest-ranked rows.
static int neofetchcmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    (void)argc;
    (void)argv;
    fetch_t *f = moy_sh_halloc(sizeof *f);
    if (f == NULL) {
        return moy_sh_fail("neofetch: no memory");
    }
    memset(f, 0, sizeof *f);
    char *text = NULL;
    size_t tn = 0;
    if (s->host.vread == NULL || s->host.vread(s->host.ctx, "/proc/console", &text, &tn) != 0) {
        text = NULL;
    }
    char name[FETCH_VALUE];
    if (text != NULL && console_row(text, tn, "name", name, sizeof name)) {
        snprintf(f->title, sizeof f->title, "%s@moybyte", name);
    }
    fetch_console(f, text, tn, "board", "Board", 1);
    fetch_console(f, text, tn, "firmware", "Firmware", 2);
    char *v = fetch_add(f, "Chip", 3);
#ifdef MOY_SH_FETCH_CHIP
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    snprintf(v, FETCH_VALUE, "%s, %d cores", CONFIG_IDF_TARGET, chip.cores);
    v = fetch_add(f, "Uptime", 7);
    unsigned long up = (unsigned long)(esp_timer_get_time() / 60000000);
    if (up >= 24u * 60u) {
        snprintf(v, FETCH_VALUE, "%lud %luh %lum", up / 1440u, up / 60u % 24u, up % 60u);
    } else if (up >= 60u) {
        snprintf(v, FETCH_VALUE, "%luh %lum", up / 60u, up % 60u);
    } else {
        snprintf(v, FETCH_VALUE, "%lum", up);
    }
#else
    snprintf(v, FETCH_VALUE, "%s", MOY_SH_MACHINE);
#endif
    uint32_t m[6] = { 0 };
    if (s->host.mem != NULL && s->host.mem(s->host.ctx, m) == 0) {
        fetch_used(f, "Memory", 4, m[0], m[1]);
        fetch_used(f, "PSRAM", 5, m[2], m[3]);
    }
    fetch_df(f, "Card", 6, "/sd");
    fetch_df(f, "Internal", 9, "/");
    fetch_console(f, text, tn, "display", "Display", 10);
    fetch_console(f, text, tn, "theme", "Theme", 11);
    v = fetch_add(f, "Shell", 12);
    snprintf(v, FETCH_VALUE, "moy_sh (dash)");
    fetch_console(f, text, tn, "wifi", "WiFi", 8);
    moy_sh_hfree(text);

    const char *term = moy_sh_getvar(s, "TERM");
    int colour = isatty(1) && (term == NULL || strcmp(term, "dumb") != 0)
                 && moy_theme_moy64 != NULL;
    int cols = 80, rows = 0;
    if (s->host.size != NULL) {
        s->host.size(s->host.ctx, &cols, &rows);
    }
    int beside = cols >= FETCH_LOGO_W + FETCH_GAP + 26;
    int pad = beside ? FETCH_LOGO_W + FETCH_GAP : 0;
    int palette = colour;
    // what a short terminal shows: the title always, the palette while it
    // fits, the rows highest rank first (the prompt takes the last line)
    if (rows > 0) {
        int head = f->title[0] ? 2 : 0;
        if (palette && head + f->n + 3 > rows - 1) {
            palette = 0;
        }
        while (f->n > 0 && head + f->n > rows - 1) {
            int worst = 0;
            for (int i = 1; i < f->n; i++) {
                worst = f->row[i].rank > f->row[worst].rank ? i : worst;
            }
            memmove(&f->row[worst], &f->row[worst + 1], (size_t)(f->n - worst - 1) * sizeof f->row[0]);
            f->n--;
        }
    }
    int line = 0;
    if (f->title[0]) {
        char rule[FETCH_VALUE];
        size_t cut;
        int w = fetch_width(f->title, FETCH_VALUE - 1, &cut);
        memset(rule, '-', (size_t)w);
        rule[w] = 0;
        fetch_line(colour, cols, pad, line++, NULL, f->title, 1);
        fetch_line(colour, cols, pad, line++, NULL, rule, 0);
    }
    for (int i = 0; i < f->n; i++) {
        fetch_line(colour, cols, pad, line++, f->row[i].label, f->row[i].value, 0);
    }
    if (palette) {
        fetch_line(colour, cols, pad, line++, NULL, NULL, 0);
        fetch_palette(cols, pad, line++, 0);
        fetch_palette(cols, pad, line++, 1);
    }
    for (; pad > 0 && line < FETCH_LOGO_ROWS; line++) {
        fetch_logo(colour, line);
        out1c('\n');
    }
    moy_sh_hfree(f);
    return 0;
}

static int helpcmd(int argc, char **argv);

// Sorted by name (moy_sh_core_builtin searches it).
static const struct builtincmd core[] = {
    { "basename", basenamecmd, BUILTIN_REGULAR },
    { "cat", catcmd, BUILTIN_REGULAR },
    { "clear", clearcmd, BUILTIN_REGULAR },
    { "cp", cpcmd, BUILTIN_REGULAR },
    { "date", datecmd, BUILTIN_REGULAR },
    { "df", dfcmd, BUILTIN_REGULAR },
    { "dirname", dirnamecmd, BUILTIN_REGULAR },
    { "dmesg", logcmd, BUILTIN_REGULAR },
    { "edit", appcmd, BUILTIN_REGULAR },
    { "free", freecmd, BUILTIN_REGULAR },
    { "grep", grepcmd, BUILTIN_REGULAR },
    { "head", headcmd, BUILTIN_REGULAR },
    { "help", helpcmd, BUILTIN_REGULAR },
    { "log", logcmd, BUILTIN_REGULAR },
    { "ls", lscmd, BUILTIN_REGULAR },
    { "mkdir", mkdircmd, BUILTIN_REGULAR },
    { "mv", mvcmd, BUILTIN_REGULAR },
    { "neofetch", neofetchcmd, BUILTIN_REGULAR },
    { "ps", pscmd, BUILTIN_REGULAR },
    { "reboot", rebootcmd, BUILTIN_REGULAR },
    { "rm", rmcmd, BUILTIN_REGULAR },
    { "rmdir", rmdircmd, BUILTIN_REGULAR },
    { "sh", moy_sh_shcmd, BUILTIN_REGULAR },
    { "sleep", sleepcmd, BUILTIN_REGULAR },
    { "tail", tailcmd, BUILTIN_REGULAR },
    { "top", appcmd, BUILTIN_REGULAR },
    { "touch", touchcmd, BUILTIN_REGULAR },
    { "uname", unamecmd, BUILTIN_REGULAR },
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
