// moy_sh: the terminal's shell, C at kernel level (docs/os_2026-11/system.md
// section 1), derived from dash (dash/, UPSTREAM.md).
//
// A SESSION is one shell: its state in one arena (moy_sh_arena.h), its
// terminal the host's `out`, its files the store's (moy_vol, under the host's
// `root`), its processes the host's. A face (the screen's renderer, the serial
// face) owns a session and feeds it text: moy_sh_run takes what was typed,
// runs every complete command in it and answers MOY_SH_MORE while a command
// goes on past it (an open `if`, a quote, a here-document), so the face shows
// the continuation prompt and sends the next line. Commands run to completion
// inside the call; the host's `poll` is how a run is interrupted (Ctrl-C).
//
// THE FORK. There is no fork, exec or signal in the kernel. Where dash forks,
// the shell asks the OS for a process: moy_sh_fork saves the arena, runs the
// child on the same stack and, when it exits, puts the arena back, so the
// child's variables, folder and descriptors are gone exactly as a real fork's
// would be, and answers its pid. A pipeline's stages therefore run one after
// another, each writing a pipe buffer the next reads (MOY_SH_PIPE_MAX bytes at
// most); a command substitution is a child whose output the parent reads.
// Where dash execs, the shell asks the host to spawn the program (`spawn`):
// an installed command (`type: "command"`) by its name on the PATH (/bin) or
// as `<id>/<name>`. In the background (`&`) a spawned program is a process of
// its own: its pid is the job's, `jobs`, `fg`, `wait` and `kill` reach it
// through the host. Shell code in the background runs before the prompt
// returns.
//
// BUILT-INS are dash's own (cd, echo, test, printf, read, set, export, jobs,
// kill, ...), the C core over the kernel's tables and the store
// (moy_sh_cmds.c: the file commands, ps, free, df, log, date, sleep, ...)
// and the commands of the console's services (`roles`), which the host serves
// (`role`) and refuses with "needs the console" while the VM is stopped.
//
// MEMORY. The session's record and its arena come from the host's `alloc`
// (PSRAM on a board) at moy_sh_new; a pipe's buffer, a file read whole and a
// fork's snapshot come from it while a run needs them and go back after.
// Nothing here is .bss but the running (or parked) session's pointer.

#ifndef MOY_SH_H
#define MOY_SH_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SH_DONE 0                   // every command ran; show the prompt
#define MOY_SH_MORE 1                   // a command goes on: show PS2, send more
#define MOY_SH_EXIT 2                   // the shell exited (moy_sh_status)

#define MOY_SH_NOT_FOUND (-1)           // spawn/role: no such command
#define MOY_SH_NEEDS_VM (-2)            // role: the VM is stopped

#define MOY_SH_PIPE_MAX (1024u * 1024u)  // a pipe buffer's bytes
#define MOY_SH_FILE_MAX (4u * 1024u * 1024u)  // a file read or written whole

typedef struct moy_sh moy_sh_t;

// One process for `ps`: pid, its parent's (0 none), its kind and state
// ("app", "service", "command", ...; "running", "exited", ...), its name.
typedef struct {
    int pid, ppid;
    const char *kind, *state, *name;
    uint32_t mem;                       // bytes, 0 unknown
} moy_sh_ps_t;

typedef int (*moy_sh_ps_fn)(void *arg, const moy_sh_ps_t *p);
typedef int (*moy_sh_ent_fn)(void *arg, const char *name, int is_dir, uint32_t size);

typedef struct {
    void *ctx;
    // memory for the session and its run (PSRAM on a board)
    void *(*alloc)(void *ctx, size_t n);
    void (*free)(void *ctx, void *p);
    // the terminal: stream 1 is stdout, 2 stderr
    void (*out)(void *ctx, int stream, const char *p, size_t n);
    // nonzero stops the running command, as Ctrl-C (may be NULL)
    int (*poll)(void *ctx);
    // the files tree: absolute shell paths resolve under `root` ("" on a board)
    const char *root;
    // virtual files: /dev/<name>, /proc/... and the root's own names. read
    // answers 0 with the bytes (from `alloc`), an errno otherwise (ENOENT: not
    // virtual); list answers ENOENT for a folder it does not serve.
    int (*vread)(void *ctx, const char *path, char **p, size_t *n);
    int (*vwrite)(void *ctx, const char *path, const char *p, size_t n);
    int (*vlist)(void *ctx, const char *dir, moy_sh_ent_fn fn, void *arg);
    // installed commands: 1 when one is named `name` (the PATH's /bin)
    int (*lookup)(void *ctx, const char *name);
    // run one; foreground answers its exit status, MOY_SH_NOT_FOUND or
    // MOY_SH_NEEDS_VM; background (`bg`) answers 0 and its pid
    int (*spawn)(void *ctx, moy_sh_t *s, int argc, char **argv, int bg, int *pid);
    // a process's state: 0 running, 1 exited (*code its wait status), -1 none
    int (*status)(void *ctx, int pid, int *code);
    int (*kill)(void *ctx, int pid, int sig);
    int (*procs)(void *ctx, moy_sh_ps_fn fn, void *arg);
    // the console's service commands, NULL-terminated, and their server
    const char *const *roles;
    int (*role)(void *ctx, moy_sh_t *s, int argc, char **argv);
    // memory and storage figures for free and df; reboot (never returns)
    int (*mem)(void *ctx, uint32_t out[6]);  // internal total/free, PSRAM total/free, heap total/free
    int (*df)(void *ctx, const char *path, uint64_t *total, uint64_t *free);
    void (*reboot)(void *ctx);
    // block for `ms` milliseconds (sleep); the poll is called between
    void (*sleep_ms)(void *ctx, int ms);
    // the log, for `log`/`dmesg`: write its lines (or one process's) to `fd`
    int (*log)(void *ctx, moy_sh_t *s, int fd, const char *name, int follow);
} moy_sh_host_t;

// A session over `arena` bytes; NULL when the host's alloc refuses. `host` is
// copied. The shell starts in `cwd` with $HOME `home`, interactive.
moy_sh_t *moy_sh_new(const moy_sh_host_t *host, size_t arena, const char *home,
                     const char *cwd);
void moy_sh_free(moy_sh_t *s);

// Feed `n` bytes the face read (a line, its newline included). Answers
// MOY_SH_DONE, MOY_SH_MORE or MOY_SH_EXIT.
int moy_sh_run(moy_sh_t *s, const char *text, size_t n);
// Ctrl-C at the prompt: drop what MOY_SH_MORE was holding.
void moy_sh_cancel(moy_sh_t *s);
// $? after the last command, or the exit status after MOY_SH_EXIT.
int moy_sh_status(const moy_sh_t *s);

// The prompt to show now (PS1, or PS2 after MOY_SH_MORE), expanded, into out.
size_t moy_sh_prompt(moy_sh_t *s, int more, char *out, size_t cap);

// A spawned program's or a role command's standard streams, while `spawn` or
// `role` runs: fd 0 reads, 1 and 2 write, through the session's descriptors.
long moy_sh_fd_read(moy_sh_t *s, int fd, void *buf, size_t n);
long moy_sh_fd_write(moy_sh_t *s, int fd, const void *buf, size_t n);

// A variable's value (NULL unset) and setting one.
const char *moy_sh_getvar(moy_sh_t *s, const char *name);
void moy_sh_setvar(moy_sh_t *s, const char *name, const char *value);
const char *moy_sh_cwd(moy_sh_t *s);

// Names for completion: the built-ins (dash's, the C core's, the roles')
// starting with `prefix`, until fn answers nonzero; the face completes paths.
int moy_sh_complete(moy_sh_t *s, const char *prefix, moy_sh_ent_fn fn, void *arg);

// The arena's bytes below its top, for a face's diagnostics.
size_t moy_sh_arena_in_use(const moy_sh_t *s);

// The session a host callback was called from (spawn, role: a program's
// streams are its), or between runs the parked one, or NULL.
moy_sh_t *moy_sh_running(void);
// The host table's ctx the session was made with.
void *moy_sh_ctx(const moy_sh_t *s);
// Park `s` (NULL: none): the session a face keeps between its runs, held in
// the one writable word, so a face on a board keeps no root of its own. A
// session's own runs leave the parked one parked; freeing it unparks it.
void moy_sh_park(moy_sh_t *s);

#endif // MOY_SH_H
