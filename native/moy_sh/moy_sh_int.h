// moy_sh_int.h: what the session (moy_sh.c), the OS layer (moy_sh_sys.c) and
// the built-ins (moy_sh_cmds.c) share. Not a public interface.

#ifndef MOY_SH_INT_H
#define MOY_SH_INT_H

#include <setjmp.h>
#include <stddef.h>

#include "moy_sh.h"
#include "moy_sh_arena.h"
#include "moy_sh_g.h"

#define MOY_SH_KIDS_MAX 48              // children not yet waited for
#define MOY_SH_PATH_MAX 512
#define MOY_SH_FIRST_CHILD 100000       // a child's pid: never a real process's

// A child the session is running: its frame sits on the C stack in
// moy_sh_fork while the child runs below it.
struct moy_sh_fork_frame {
    struct moy_sh_fork_frame *up;
    jmp_buf done;
    void *snap;
    size_t snap_n;
    int pid;
    int wstatus;                        // its wait status, set at its exit
    int bound;                          // a background program it spawned
    int bg;
};

struct moy_sh {
    moy_sh_host_t host;
    void *mem;                          // the arena's bytes, from host.alloc
    moy_sh_arena_t *arena;
    struct moy_sh_globals *g;

    char *pending;                      // what MOY_SH_MORE holds
    size_t pending_n, pending_cap;

    jmp_buf run_jb;                     // the session's own exit
    int status;
    int exited;
    int interrupt;                      // an interrupt not yet delivered

    struct moy_sh_fork_frame *forks;
    int next_pid;
    // children not yet waited for, each its parent's (a subshell's own are
    // dropped when it exits): `host` marks a background program the host
    // runs, whose status is asked for; the others have run to their end
    struct moy_sh_kid { int pid, ppid, wstatus, host; } kids[MOY_SH_KIDS_MAX];
    int nkids;
    struct job *fg;                     // the job a blocking wait is for

    struct builtincmd *roles;           // the host's service commands
    int nroles;
};

// The session running now (set for the length of a call into the shell).
#define moy_sh_cur (moy_sh_g->sess)

int moy_sh_shcmd(int argc, char **argv);

// The index in kids of the running process's child `pid`, or -1.
int moy_sh_kid(int pid);

void *moy_sh_halloc(size_t n);
void moy_sh_hfree(void *p);

// The OS layer: the terminal descriptors a session starts with, a fork's
// references and a process's exit.
void moy_sh_sys_start(moy_sh_t *s);
void moy_sh_sys_fork(moy_sh_t *s);
void moy_sh_sys_exit(moy_sh_t *s);
void moy_sh_sys_end(moy_sh_t *s);
// An absolute, normalised shell path for `path` against the working folder.
int moy_sh_path(const char *path, char *out, size_t cap);
// The store path a shell path names, or NULL for a virtual one.
const char *moy_sh_store_path(const char *abs, char *out, size_t cap);

// The C core built-ins (moy_sh_cmds.c) and their lookup.
const struct builtincmd *moy_sh_core_builtin(const char *name);
int moy_sh_core_names(moy_sh_ent_fn fn, void *arg, const char *prefix);

// Fail the running built-in: its message (naming the command) and a newline
// on stderr; answers 1.
int moy_sh_fail(const char *fmt, ...);

#endif // MOY_SH_INT_H
