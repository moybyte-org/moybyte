// moy_sh: the session, its runs, the fork and the exec (moy_sh.h).
//
// Compiled as dash's sources are (`-include dash_config.h`), because it is
// the place that knows dash's state: it allocates every aggregate dash kept
// as a global (moy_sh_g.h) and gives each the value dash's initializer did.

#include <errno.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "shell.h"
#include "alias.h"
#include "arith_yacc.h"
#include "builtins.h"
#include "error.h"
#include "eval.h"
#include "exec.h"
#include "expand.h"
#include "init.h"
#include "input.h"
#include "jobs.h"
#include "main.h"
#include "memalloc.h"
#include "mystring.h"
#include "nodes.h"
#include "options.h"
#include "output.h"
#include "parser.h"
#include "redir.h"
#include "trap.h"
#include "var.h"
#include "system.h"

#include "cd.h"
#include "moy_sh_int.h"

#define main_handler (*moy_sh_g->main_handler)

_Static_assert(NOPTS == MOY_SH_G_NOPTS, "moy_sh_g.h: optlist's size is dash's NOPTS");

// The running session's state, the one writable word the shell keeps outside
// its arena (its session is moy_sh_cur, a field of it).
MOY_SH_TLS struct moy_sh_globals *moy_sh_g;

// --- memory -----------------------------------------------------------------

void *moy_sh_halloc(size_t n) {
    return moy_sh_cur->host.alloc(moy_sh_cur->host.ctx, n ? n : 1);
}

void moy_sh_hfree(void *p) {
    if (p != NULL) {
        moy_sh_cur->host.free(moy_sh_cur->host.ctx, p);
    }
}

void *moy_sh_malloc(size_t n) {
    return moy_sh_arena_alloc(moy_sh_cur->arena, n);
}

void *moy_sh_realloc(void *p, size_t n) {
    return moy_sh_arena_realloc(moy_sh_cur->arena, p, n);
}

char *moy_sh_strdup(const char *str) {
    size_t n = strlen(str) + 1;
    char *p = moy_sh_malloc(n);
    if (p != NULL) {
        memcpy(p, str, n);
    }
    return p;
}

void moy_sh_mfree(void *p) {
    moy_sh_arena_free(moy_sh_cur->arena, p);
}

static void *zalloc(size_t n) {
    void *p = moy_sh_malloc(n);
    if (p != NULL) {
        memset(p, 0, n);
    }
    return p;
}

// Make the session current for the length of a call.
static struct moy_sh_globals *enter(moy_sh_t *s) {
    struct moy_sh_globals *was = moy_sh_g;
    moy_sh_g = s->g;
    return was;
}

static void leave(struct moy_sh_globals *was) {
    moy_sh_g = was;
}

// --- the state dash's initializers gave ---------------------------------------

static int state_alloc(struct moy_sh_globals *g);

static int state_new(moy_sh_t *s) {
    struct moy_sh_globals *g = s->g;
    if (!state_alloc(g)) {
        return 0;
    }
    // dash's file-local globals are reached through the record here
    g->curdir = g->physdir = nullstr;
    savestatus = -1;
    g->tpip[0] = -1;
    g->builtinloc = -1;
    g->toppf = parsefile = g->basepf;
    stdin_istty = -1;
    g->ttyfd = -1;
    g->stackp = g->stackbase;
    stacknxt = g->stackbase->space;
    stacknleft = MINSIZE;
    sstrend = g->stackbase->space + MINSIZE;
    outputf.fd = 1;
    outputf.bufsize = BUFSIZ;
    errout.fd = 2;
    out1 = &outputf;
    out2 = &errout;
    memcpy(defifsvar, "IFS= \t\n", sizeof "IFS= \t\n");
    memcpy(g->defoptindvar, "OPTIND=1", sizeof "OPTIND=1");
    memcpy(linenovar, "LINENO=", sizeof "LINENO=");
    return 1;
}

// --- the session ----------------------------------------------------------------

static void reset_after_error(void) {
    exitreset();
    reset();
    FORCEINTON;
}

moy_sh_t *moy_sh_new(const moy_sh_host_t *host, size_t arena, const char *home,
                     const char *cwd) {
    moy_sh_t *s = host->alloc(host->ctx, sizeof *s);
    if (s == NULL) {
        return NULL;
    }
    memset(s, 0, sizeof *s);
    s->host = *host;
    s->next_pid = MOY_SH_FIRST_CHILD;
    s->mem = host->alloc(host->ctx, arena + 16);
    void *base = s->mem != NULL ? (void *)(((uintptr_t)s->mem + 15) & ~(uintptr_t)15) : NULL;
    s->arena = moy_sh_arena_init(base, arena);
    s->g = s->arena != NULL ? moy_sh_arena_alloc(s->arena, sizeof *s->g) : NULL;
    if (s->g == NULL) {
        moy_sh_free(s);
        return NULL;
    }
    memset(s->g, 0, sizeof *s->g);
    s->g->sess = s;
    struct moy_sh_globals *was = enter(s);
    if (!state_new(s)) {
        leave(was);
        moy_sh_free(s);
        return NULL;
    }
    int nroles = 0;
    while (host->roles != NULL && host->roles[nroles] != NULL) {
        nroles++;
    }
    if (nroles > 0) {
        s->roles = host->alloc(host->ctx, nroles * sizeof *s->roles);
        if (s->roles == NULL) {
            leave(was);
            moy_sh_free(s);
            return NULL;
        }
        s->nroles = nroles;
    }
    extern int moy_sh_rolecmd(int, char **);
    for (int i = 0; i < nroles; i++) {
        s->roles[i].name = host->roles[i];
        s->roles[i].builtin = moy_sh_rolecmd;
        s->roles[i].flags = BUILTIN_REGULAR;
    }
    moy_sh_sys_start(s);
    if (setjmp(main_handler.loc)) {
        leave(was);
        moy_sh_free(s);
        return NULL;
    }
    handler = &main_handler;
    rootpid = mypid = s->g->proc.pid;
    init();
    char **argv = zalloc(3 * sizeof(char *));
    argv[0] = savestr("sh");
    argv[1] = savestr("-im");
    procargs(argv);
    home = home != NULL && strlen(home) < sizeof s->home ? home : "/";
    strcpy(s->home, home);
    setvar("HOME", home, 0);
    setvar("PS1", "${PWD##*/}$ ", 0);
    setvar("TERM", "vt100", VEXPORT);
    if (cwd != NULL && chdir(cwd) == 0) {
        setpwd(cwd, 0);
    }
    flushall();
    leave(was);
    return s;
}

int moy_sh_go_home(moy_sh_t *s, const char *const *dirs) {
    struct moy_sh_globals *was = enter(s);
    int at = -1;
    struct stat st;
    for (int i = 0; dirs[i] != NULL && at < 0; i++) {
        if (strlen(dirs[i]) < sizeof s->home && stat(dirs[i], &st) == 0 && S_ISDIR(st.st_mode)
                && chdir(dirs[i]) == 0) {
            setpwd(dirs[i], 0);
            setvar("HOME", dirs[i], 0);
            strcpy(s->home, dirs[i]);
            at = i;
        }
    }
    flushall();
    leave(was);
    return at;
}

int moy_sh_is_dir(moy_sh_t *s, const char *path) {
    struct moy_sh_globals *was = enter(s);
    struct stat st;
    int r = stat(path, &st) == 0 && S_ISDIR(st.st_mode);
    leave(was);
    return r;
}

void moy_sh_set_etc(moy_sh_t *s, const char *etc) {
    s->host.etc = etc;
}

void moy_sh_free(moy_sh_t *s) {
    if (s == NULL) {
        return;
    }
    if (s->g != NULL) {
        struct moy_sh_globals *was = enter(s);
        moy_sh_sys_end(s);
        leave(was == s->g ? NULL : was);
    }
    void (*hfree)(void *, void *) = s->host.free;
    if (s->pending != NULL) {
        hfree(s->host.ctx, s->pending);
    }
    if (s->roles != NULL) {
        hfree(s->host.ctx, s->roles);
    }
    if (s->mem != NULL) {
        hfree(s->host.ctx, s->mem);
    }
    hfree(s->host.ctx, s);
}

moy_sh_t *moy_sh_running(void) {
    return moy_sh_g != NULL ? moy_sh_g->sess : NULL;
}

void *moy_sh_ctx(const moy_sh_t *s) {
    return s->host.ctx;
}

const moy_sh_host_t *moy_sh_host(const moy_sh_t *s) {
    return &s->host;
}

int moy_sh_resolve(moy_sh_t *s, const char *path, char *out, size_t cap) {
    struct moy_sh_globals *was = enter(s);
    char abs[MOY_SH_PATH_MAX];
    int r = moy_sh_path(path, abs, sizeof abs);
    if (r == 0 && moy_sh_store_path(abs, out, cap) == NULL) {
        r = ENOENT;
    }
    leave(was);
    return r;
}

int moy_sh_abs(moy_sh_t *s, const char *path, char *out, size_t cap) {
    struct moy_sh_globals *was = enter(s);
    int r = moy_sh_path(path, out, cap);
    leave(was);
    return r;
}

int moy_sh_stat_at(moy_sh_t *s, const char *path, moy_sh_stat_t *out) {
    struct moy_sh_globals *was = enter(s);
    struct stat st;
    int r = stat(path, &st) == 0 ? 0 : (errno ? errno : ENOENT);
    if (r == 0) {
        out->is_dir = S_ISDIR(st.st_mode);
        out->size = (uint32_t)st.st_size;
        out->mtime = (int64_t)st.st_mtime;
    }
    leave(was);
    return r;
}

typedef struct {
    moy_sh_t *s;
    void (*fn)(void *);
    void *arg;
} sh_hop_t;

static void hop_in(void *p) {
    sh_hop_t *h = p;
    struct moy_sh_globals *was = enter(h->s);
    h->fn(h->arg);
    leave(was);
}

void moy_sh_hop(moy_sh_t *s, void (*fn)(void *arg), void *arg) {
    if (s == NULL || s->host.hop == NULL) {
        fn(arg);
        return;
    }
    sh_hop_t h = { s, fn, arg };
    s->host.hop(s->host.ctx, hop_in, &h);
}

void moy_sh_park(moy_sh_t *s) {
    moy_sh_g = s != NULL ? s->g : NULL;
}

int moy_sh_status(const moy_sh_t *s) {
    return s->status;
}

size_t moy_sh_arena_in_use(const moy_sh_t *s) {
    return moy_sh_arena_used(s->arena);
}

const char *moy_sh_cwd(moy_sh_t *s) {
    return s->g->proc.cwd != NULL ? s->g->proc.cwd : "/";
}

void moy_sh_cancel(moy_sh_t *s) {
    s->pending_n = 0;
}

static int hold(moy_sh_t *s, const char *p, size_t n) {
    if (s->pending_n + n + 2 > s->pending_cap) {
        size_t cap = (s->pending_n + n + 2) * 2;
        char *q = moy_sh_halloc(cap);
        if (q == NULL) {
            return 0;
        }
        if (s->pending_n) {
            memcpy(q, s->pending, s->pending_n);
        }
        moy_sh_hfree(s->pending);
        s->pending = q;
        s->pending_cap = cap;
    }
    memcpy(s->pending + s->pending_n, p, n);
    s->pending_n += n;
    s->pending[s->pending_n] = 0;
    return 1;
}

// A line ending in an unquoted backslash goes on: count the backslashes
// before its newline.
static int continues(const char *p, size_t n) {
    if (n == 0 || p[n - 1] != '\n') {
        return 0;
    }
    size_t k = 0;
    while (k + 1 < n && p[n - 2 - k] == '\\') {
        k++;
    }
    return k & 1;
}

int moy_sh_run(moy_sh_t *s, const char *text, size_t n) {
    if (s->exited) {
        return MOY_SH_EXIT;
    }
    struct moy_sh_globals *was = enter(s);
    if (!hold(s, text, n) || (s->pending_n && s->pending[s->pending_n - 1] != '\n' && !hold(s, "\n", 1))) {
        leave(was);
        return MOY_SH_DONE;
    }
    if (continues(s->pending, s->pending_n)) {
        leave(was);
        return MOY_SH_MORE;
    }
    volatile int ret = MOY_SH_DONE;
    volatile size_t start = 0;
    volatile int pushed = 0;
    struct stackmark smark;
    s->interrupt = 0;
    if (setjmp(s->run_jb)) {
        // the shell exited: moy_sh__exit with no child running
        ret = MOY_SH_EXIT;
        s->exited = 1;
        s->pending_n = 0;
        goto out;
    }
    if (setjmp(main_handler.loc)) {
        exitreset();
        int e = exception;
        moy_sh_g->input_more = 0;
        if (e == EXEND || e == EXEXIT) {
            exitshell();
        }
        if (e == EXMORE) {
            // keep what the command that goes on started with
            memmove(s->pending, s->pending + start, s->pending_n - start);
            s->pending_n -= start;
            s->pending[s->pending_n] = 0;
            ret = MOY_SH_MORE;
        } else {
            if (e == EXINT) {
                out2c('\n');
            }
            s->pending_n = 0;
        }
        reset_after_error();
        flushall();
        goto out;
    }
    handler = &main_handler;
    setinputstring(s->pending);
    pushed = 1;
    for (;;) {
        union node *node;
        setstackmark(&smark);
        if (parsefile->strpush == NULL) {
            start = parsefile->nextc - s->pending;
        }
        moy_sh_g->input_more = 1;
        moy_sh_g->input_eof = 0;
        node = parsecmd(0);
        moy_sh_g->input_more = 0;
        if (node == NEOF) {
            break;
        }
        if (moy_sh_g->input_eof) {
            // a here-document still open at the end of what was sent
            exraise(EXMORE);
        }
        job_warning = (job_warning == 2) ? 1 : 0;
        evaltree(node, 0);
        popstackmark(&smark);
        if (evalskip) {
            evalskip &= ~(SKIPFUNC | SKIPFUNCDEF);
            evalskip = 0;
        }
    }
    popfile();
    pushed = 0;
    s->pending_n = 0;
    if (jobctl) {
        showjobs(out2, SHOW_CHANGED);
    }
    flushall();
out:
    (void)pushed;
    s->status = s->exited ? s->status : exitstatus;
    leave(was);
    return ret;
}

size_t moy_sh_prompt(moy_sh_t *s, int more, char *out, size_t cap) {
    struct moy_sh_globals *was = enter(s);
    const char *p = more ? ps2val() : ps1val();
    struct stackmark smark;
    size_t n = 0;
    struct jmploc jl;
    struct jmploc *saved = handler;
    setstackmark(&smark);
    if (!setjmp(jl.loc)) {
        handler = &jl;
        p = expandstr(p);
    } else {
        reset_after_error();
    }
    handler = saved;
    n = strlen(p);
    if (n >= cap) {
        n = cap ? cap - 1 : 0;
    }
    if (cap) {
        memcpy(out, p, n);
        out[n] = 0;
    }
    popstackmark(&smark);
    flushall();
    leave(was);
    return n;
}

const char *moy_sh_getvar(moy_sh_t *s, const char *name) {
    struct moy_sh_globals *was = enter(s);
    const char *v = lookupvar(name);
    leave(was);
    return v;
}

void moy_sh_setvar(moy_sh_t *s, const char *name, const char *value) {
    struct moy_sh_globals *was = enter(s);
    struct jmploc jl;
    struct jmploc *saved = handler;
    if (!setjmp(jl.loc)) {
        handler = &jl;
        setvar(name, value, 0);
    } else {
        reset_after_error();
    }
    handler = saved;
    flushall();
    leave(was);
}

int moy_sh_complete(moy_sh_t *s, const char *prefix, moy_sh_ent_fn fn, void *arg) {
    struct moy_sh_globals *was = enter(s);
    size_t n = strlen(prefix);
    int r = 0;
    for (int i = 0; i < NUMBUILTINS && !r; i++) {
        if (strncmp(builtincmd[i].name, prefix, n) == 0) {
            r = fn(arg, builtincmd[i].name, 0, 0);
        }
    }
    if (!r) {
        r = moy_sh_core_names(fn, arg, prefix);
    }
    for (int i = 0; i < s->nroles && !r; i++) {
        if (strncmp(s->roles[i].name, prefix, n) == 0) {
            r = fn(arg, s->roles[i].name, 0, 0);
        }
    }
    leave(was);
    return r;
}

int moy_sh_list(moy_sh_t *s, const char *path, moy_sh_ent_fn fn, void *arg) {
    struct moy_sh_globals *was = enter(s);
    int r = 0;
    moy_sh_DIR *d = moy_sh_opendir(path);
    if (d == NULL) {
        r = errno ? errno : ENOENT;
    } else {
        struct dirent *e;
        while (!r && (e = moy_sh_readdir(d)) != NULL) {
            r = fn(arg, e->d_name, e->d_type == DT_DIR, 0);
        }
        moy_sh_closedir(d);
    }
    leave(was);
    return r;
}

// --- the fork -------------------------------------------------------------------

int moy_sh_fork(void (*child)(void *), void *arg, int bg) {
    moy_sh_t *s = moy_sh_cur;
    struct moy_sh_fork_frame f;
    memset(&f, 0, sizeof f);
    f.up = s->forks;
    f.bg = bg;
    f.pid = s->next_pid++;
    f.snap_n = moy_sh_arena_used(s->arena);
    f.snap = moy_sh_halloc(f.snap_n);
    if (f.snap == NULL) {
        errno = ENOMEM;
        return -1;
    }
    moy_sh_arena_save(s->arena, f.snap);
    moy_sh_sys_fork(s);
    s->forks = &f;
    if (setjmp(f.done) == 0) {
        moy_sh_g->proc.pid = f.pid;
        // the child's errors end the child, as they end a forked dash
        if (setjmp(main_handler.loc)) {
            exitreset();
            exitshell();
        }
        handler = &main_handler;
        child(arg);
        exitshell();
    }
    // the child has exited (moy_sh__exit) and its descriptors are closed
    // read back through a volatile view: the child wrote these after setjmp
    volatile struct moy_sh_fork_frame *vf = &f;
    int wstatus = vf->wstatus, bound = vf->bound;
    s->forks = f.up;
    moy_sh_arena_restore(s->arena, f.snap, f.snap_n);
    moy_sh_hfree(f.snap);
    int pid = bound > 0 ? bound : f.pid;
    if (s->nkids < MOY_SH_KIDS_MAX) {
        struct moy_sh_kid *k = &s->kids[s->nkids++];
        k->pid = pid;
        k->ppid = moy_sh_g->proc.pid;
        k->wstatus = wstatus;
        k->host = bound > 0;
    }
    // an interrupted child: the foreground's interrupt reaches the parent too
    if (!f.bg && (wstatus & 0x7f) == SIGINT) {
        s->interrupt = 1;
    }
    gotsigchld = 1;
    return pid;
}

static void kid_drop(moy_sh_t *s, int i) {
    memmove(&s->kids[i], &s->kids[i + 1], (s->nkids - i - 1) * sizeof s->kids[0]);
    s->nkids--;
}

// The running process ends with wait status `wstatus`.
static void __attribute__((__noreturn__)) end_process(int wstatus) {
    moy_sh_t *s = moy_sh_cur;
    struct moy_sh_fork_frame *f = s->forks;
    if (f != NULL) {
        int me = moy_sh_g->proc.pid;
        for (int i = s->nkids - 1; i >= 0; i--) {
            if (s->kids[i].ppid == me) {
                kid_drop(s, i);
            }
        }
        moy_sh_sys_exit(s);
        f->wstatus = wstatus;
        longjmp(f->done, 1);
    }
    s->status = (wstatus & 0x7f) ? 128 + (wstatus & 0x7f) : (wstatus >> 8) & 0xff;
    longjmp(s->run_jb, 1);
}

void moy_sh__exit(int status) {
    end_process((status & 0xff) << 8);
}

// A signal to the running process: what its sigmode says (trap.c's S_*), a
// handler being onsig, as dash's sigaction installed it.
int moy_sh_raise(int sig) {
    if (sig <= 0 || sig >= NSIG) {
        errno = EINVAL;
        return -1;
    }
    switch (sigmode[sig - 1]) {
        case 2:                         // S_CATCH
            onsig(sig);
            return 0;
        case 3:                         // S_IGN
        case 4:                         // S_HARD_IGN
            return 0;
        default:
            end_process(sig);
    }
}

int moy_sh_kid(int pid) {
    moy_sh_t *s = moy_sh_cur;
    for (int i = 0; i < s->nkids; i++) {
        if (s->kids[i].pid == pid && s->kids[i].ppid == moy_sh_g->proc.pid) {
            return i;
        }
    }
    return -1;
}

int moy_sh_children(void) {
    moy_sh_t *s = moy_sh_cur;
    for (int i = 0; i < s->nkids; i++) {
        if (s->kids[i].ppid == moy_sh_g->proc.pid) {
            return 1;
        }
    }
    return 0;
}

void moy_sh_foreground(struct job *jp) {
    moy_sh_cur->fg = jp;
}

// Ctrl-C while a foreground job's programs run: SIGINT to each, and the wait
// goes on for them. 0 when none took it.
static int interrupt_fg(moy_sh_t *s, int me) {
    struct job *jp = s->fg;
    int sent = 0;
    if (jp == NULL || s->host.kill == NULL) {
        return 0;
    }
    for (int i = 0; i < jp->nprocs; i++) {
        int pid = jp->ps[i].pid;
        for (int k = 0; k < s->nkids; k++) {
            if (s->kids[k].pid == pid && s->kids[k].ppid == me && s->kids[k].host &&
                (*s->host.kill)(s->host.ctx, pid, SIGINT) == 0) {
                sent = 1;
            }
        }
    }
    return sent;
}

int moy_sh_wait(int *wstatus, int flags) {
    moy_sh_t *s = moy_sh_cur;
    int me = moy_sh_g->proc.pid;
    for (;;) {
        int mine = 0;
        for (int i = 0; i < s->nkids; i++) {
            struct moy_sh_kid *k = &s->kids[i];
            if (k->ppid != me) {
                continue;
            }
            mine = 1;
            if (k->host) {
                int code = 0;
                int st = s->host.status != NULL ? (*s->host.status)(s->host.ctx, k->pid, &code) : -1;
                if (st == 0) {
                    continue;
                }
                k->wstatus = st > 0 ? code : 0;
            }
            int pid = k->pid;
            *wstatus = k->wstatus;
            kid_drop(s, i);
            return pid;
        }
        if (!mine) {
            errno = ECHILD;
            return -1;
        }
        if (flags & WNOHANG) {
            return 0;
        }
        if (moy_sh_poll()) {
            if (interrupt_fg(s, me)) {
                continue;
            }
            s->interrupt = 1;
            errno = EINTR;
            return -1;
        }
        if (s->host.sleep_ms != NULL) {
            s->host.sleep_ms(s->host.ctx, 10);
        }
    }
}

int moy_sh_interrupted(moy_sh_t *s) {
    if (s->interrupt) {
        return 1;
    }
    if (s->host.poll != NULL && s->host.poll(s->host.ctx)) {
        s->interrupt = 1;
        return 1;
    }
    return 0;
}

int moy_sh_poll(void) {
    moy_sh_t *s = moy_sh_cur;
    if (s->interrupt) {
        s->interrupt = 0;
        return 1;
    }
    return s->host.poll != NULL && s->host.poll(s->host.ctx);
}

// --- the exec -------------------------------------------------------------------

struct script {
    char *path;
    char **argv;
};

static void script_child(void *arg) {
    struct script *sc = arg;
    arg0 = strchr(sc->argv[0], '/') != NULL ? sc->argv[0] : sc->path;
    setparam(sc->argv + 1);
    readcmdfile(sc->path);
    exitshell();
}

static int run_script(char *path, char **argv) {
    struct script sc = { path, argv };
    struct job *jp = makejob(1);
    forkshell(jp, NULL, FORK_FG, script_child, &sc);
    return waitforjob(jp);
}

struct shc {
    char *cmd;
    char **argv;
};

static void shc_child(void *arg) {
    struct shc *c = arg;
    if (c->argv[0] != NULL) {
        arg0 = c->argv[0];
        setparam(c->argv + 1);
    }
    evalstring(c->cmd, 0);
    exitshell();
}

// sh FILE [ARG...] runs a script in a subshell; sh -c TEXT [NAME [ARG...]]
// runs TEXT there.
int moy_sh_shcmd(int argc, char **argv) {
    char abs[MOY_SH_PATH_MAX];
    if (argc >= 3 && strcmp(argv[1], "-c") == 0) {
        struct shc c = { argv[2], argv + 3 };
        struct job *jp = makejob(1);
        forkshell(jp, NULL, FORK_FG, shc_child, &c);
        return waitforjob(jp);
    }
    if (argc < 2 || argv[1][0] == '-') {
        return moy_sh_fail("usage: sh FILE [ARG...] | sh -c TEXT [NAME [ARG...]]");
    }
    struct stat st;
    if (moy_sh_path(argv[1], abs, sizeof abs) != 0 || stat(abs, &st) != 0 || !S_ISREG(st.st_mode)) {
        sh_warnx("%s: cannot open", argv[1]);
        return 127;
    }
    return run_script(abs, argv + 1);
}

static int spawn(moy_sh_t *s, int argc, char **argv) {
    struct moy_sh_fork_frame *f = s->forks;
    if (s->host.spawn == NULL) {
        return MOY_SH_NOT_FOUND;
    }
    flushall();
    if (f != NULL && f->bg) {
        int pid = 0;
        int r = s->host.spawn(s->host.ctx, s, argc, argv, 1, &pid);
        if (r == 0 && pid > 0) {
            f->bound = pid;
        }
        return r;
    }
    return s->host.spawn(s->host.ctx, s, argc, argv, 0, NULL);
}

static int exec_named(moy_sh_t *s, char **argv, const char *name) {
    int argc = 0;
    while (argv[argc] != NULL) {
        argc++;
    }
    char *was = argv[0];
    argv[0] = (char *)name;
    int r = spawn(s, argc, argv);
    argv[0] = was;
    if (r == MOY_SH_NEEDS_VM) {
        sh_warnx("%s: needs the console", was);
        return 126;
    }
    if (r == MOY_SH_NOT_FOUND) {
        return -1;
    }
    return r;
}

int moy_sh_exec(char **argv, const char *path, int idx) {
    moy_sh_t *s = moy_sh_cur;
    char abs[MOY_SH_PATH_MAX];
    struct stat st;
    // `exec sh ...` (ssh-copy-id's, a script's) is this shell: /bin/sh
    if (!strcmp(argv[0], "sh") || !strcmp(argv[0], "/bin/sh")) {
        int argc = 0;
        while (argv[argc] != NULL) {
            argc++;
        }
        return moy_sh_shcmd(argc, argv);
    }
    if (strchr(argv[0], '/') != NULL) {
        if (moy_sh_path(argv[0], abs, sizeof abs) == 0 && stat(abs, &st) == 0 &&
            S_ISREG(st.st_mode)) {
            if (strncmp(abs, "/bin/", 5) == 0) {
                int r = exec_named(s, argv, abs + 5);
                if (r >= 0) {
                    return r;
                }
            } else {
                return run_script(abs, argv);
            }
        }
        // `<id>/<name>`: one installed command of a cart that has several
        const char *slash = strchr(argv[0], '/');
        if (argv[0][0] != '/' && argv[0][0] != '.' && strchr(slash + 1, '/') == NULL) {
            int r = exec_named(s, argv, argv[0]);
            if (r >= 0) {
                return r;
            }
        }
    } else {
        while (padvance(&path, argv[0]) >= 0) {
            char *cand = stackblock();
            if (--idx >= 0 || pathopt != NULL) {
                continue;
            }
            if (moy_sh_path(cand, abs, sizeof abs) != 0 || stat(abs, &st) != 0 ||
                !S_ISREG(st.st_mode)) {
                continue;
            }
            if (strncmp(abs, "/bin/", 5) == 0) {
                int r = exec_named(s, argv, abs + 5);
                if (r >= 0) {
                    return r;
                }
                continue;
            }
            return run_script(abs, argv);
        }
    }
    sh_warnx("%s: not found", argv[0]);
    return 127;
}

// --- the built-ins the shell adds -------------------------------------------------

const struct builtincmd *moy_sh_find_builtin(const char *name) {
    moy_sh_t *s = moy_sh_cur;
    const struct builtincmd *b = moy_sh_core_builtin(name);
    if (b != NULL) {
        return b;
    }
    for (int i = 0; i < s->nroles; i++) {
        if (strcmp(s->roles[i].name, name) == 0) {
            return &s->roles[i];
        }
    }
    return NULL;
}

int moy_sh_rolecmd(int argc, char **argv) {
    moy_sh_t *s = moy_sh_cur;
    flushall();
    if (argc == 1 && strcmp(argv[0], "python") == 0 && isatty(0) && s->host.app != NULL) {
        int a = s->host.app(s->host.ctx, s, argc, argv);   // the REPL in front of the face
        if (a != MOY_SH_NOT_FOUND) {
            return a;
        }
    }
    int r = s->host.role != NULL ? s->host.role(s->host.ctx, s, argc, argv) : MOY_SH_NOT_FOUND;
    if (r == MOY_SH_NEEDS_VM) {
        return moy_sh_fail("%s: needs the console", argv[0]);
    }
    if (r == MOY_SH_NOT_FOUND) {
        return moy_sh_fail("%s: not served here", argv[0]);
    }
    return r;
}

int moy_sh_fail(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    doformat(out2, fmt, ap);
    outcslow('\n', out2);
    va_end(ap);
    flushall();
    return 1;
}

long moy_sh_fd_read(moy_sh_t *s, int fd, void *buf, size_t n) {
    struct moy_sh_globals *was = enter(s);
    long r = read(fd, buf, n);
    leave(was);
    return r;
}

long moy_sh_fd_write(moy_sh_t *s, int fd, const void *buf, size_t n) {
    struct moy_sh_globals *was = enter(s);
    flushall();
    long r = write(fd, buf, n);
    leave(was);
    return r;
}

// The aggregates dash defined as globals, allocated in the arena. Last in the
// file: their names are dash's macros everywhere above.
#undef yylval
#undef ifsfirst
#undef exparg
#undef basepf
#undef basebuf
#undef stdin_state
#undef main_handler
#undef stackbase
#undef shellparam
#undef outputf
#undef errout
#undef preverrout
#undef varinit

static int state_alloc(struct moy_sh_globals *g) {
    g->yylval = zalloc(sizeof(union yystype));
    g->ifsfirst = zalloc(sizeof(struct ifsregion));
    g->exparg = zalloc(sizeof(struct arglist));
    g->basepf = zalloc(sizeof(struct parsefile_s));
    g->basebuf = zalloc(IBUFSIZ);
    g->stdin_state = zalloc(sizeof(struct stdin_state_s));
    g->main_handler = zalloc(sizeof(struct jmploc));
    g->stackbase = zalloc(sizeof(struct stack_block));
    g->shellparam = zalloc(sizeof(struct shparam));
    g->outputf = zalloc(sizeof(struct output));
    g->errout = zalloc(sizeof(struct output));
    g->preverrout = zalloc(sizeof(struct output));
    g->varinit = zalloc(varinit_n * sizeof(struct var));
    if (!g->yylval || !g->ifsfirst || !g->exparg || !g->basepf || !g->basebuf ||
        !g->stdin_state || !g->main_handler || !g->stackbase || !g->shellparam ||
        !g->outputf || !g->errout || !g->preverrout || !g->varinit) {
        return 0;
    }
    return 1;
}
