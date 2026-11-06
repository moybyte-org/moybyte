// moy_ssh: the console's SSH server (docs/os_2026-11/system.md section 3),
// Dropbear inside the kernel's process (README.md).
//
// THE SERVER is one task: it listens, takes one connection at a time and runs
// Dropbear's session for it to the end. Dropbear ends a session by exiting
// its process; here that exit is a jump back to the accept loop
// (moy_ssh_exit_jump), and every allocation the session made goes with it
// (Dropbear's tracking allocator, the epoch the session ran in).
//
// THE SHELL is moy_sh's: a session's `shell` or `exec` request opens a pair
// of connected sockets, Dropbear's channel on one end and the shell's face on
// the other (moy_ssh_face.h), which runs on a task of its own.
//
// SIGN-IN (docs/os_2026-11/system.md section 3): a password against the
// stored hash (moy_ssh_hash) or a key in authorized_keys, any user name the
// console's one account. The store's system folder holds what the server
// reads at every connection's start, so a change is in force at the next:
//   password          the hash, one line; none (or an empty file): no SSH
//   ssh               `key = value` lines, sshd_config-like: `password_auth =
//                     no` turns passwords off (keys still sign in)
//   authorized_keys   OpenSSH's format, a key a line (ssh-copy-id writes it)
//   ssh_host_key      the host key, made at the first setup
//
// TASKS. The server's task and the face's (moy_ssh_face.h) never touch the
// store or Python: what must runs on the console's task, HOPPED there
// (moy_ssh_hop) and served at the frame's tail (moy_ssh_tail).
//
// THE RUN (moy_ssh_run.c): the console starts and stops the two tasks. They
// are made at the first start and park between a stop and the next start,
// never deleted (README.md, What runs where). A stop ends them after the
// session they serve. Asked from a session's own command (`passwd -d` over
// SSH, inside a hop) a stop cannot wait for that session, so it stands until
// the session ends; a start asked in the same session (`passwd x` next)
// withdraws it, and the tasks serve on.

#ifndef MOY_SSH_H
#define MOY_SSH_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SSH_HASH_ROUNDS 4096
#define MOY_SSH_HASH_MAX 128

typedef struct {
    uint32_t sessions;          // connections served
    uint32_t ended;             // connections ended, signed in or not
    uint32_t signed_in;         // commands run after a sign-in
    uint32_t refused;           // password attempts refused
    size_t heap_now;            // Dropbear's allocations now, bytes
    size_t heap_peak;           // their high-water since start
    size_t session_peak;        // the high-water of the last session alone
    uint32_t handshake_ms;      // accept to the shell's start, last session
    size_t arena_peak;          // the most of a shell's arena a session used
    int listening;
} moy_ssh_stats_t;

// The server's state (PSRAM), its algorithm lists, and the host key read
// from `system_dir`/ssh_host_key or made and written there, on the calling
// task, which must be the console's: 0, or -1 with the reason logged. Again
// later: nothing more.
int moy_ssh_setup(const char *system_dir);
// A listening socket on `port`, or -1.
int moy_ssh_listen(int port);
// Serve one connection from `lfd` to its end; 0 when one was served, 1 when
// none came within `wait_ms`, -1 on an error of the listening socket.
int moy_ssh_serve_one(int lfd, int wait_ms);

// The stored form of `password` with `salt` (hex, up to 32 characters):
// `$moy1$SALT$HEX`, SHA-256 iterated MOY_SSH_HASH_ROUNDS times.
int moy_ssh_hash(const char *password, const char *salt, char *out, size_t cap);

void moy_ssh_stats(moy_ssh_stats_t *out);
// A shell's arena at its end, for arena_peak.
void moy_ssh_note_arena(size_t used);

// The store's system folder setup was given.
const char *moy_ssh_system(void);

// Two connected stream sockets (the channel's end, the face's end).
int moy_ssh_pair(int fds[2]);

// The tasks: a task's id; a nap of up to `ms` (a poke ends it early); a
// poke of task `t`'s nap.
uintptr_t moy_ssh_self(void);
void moy_ssh_nap(int ms);
#define MOY_SSH_FOREVER (-1)
void moy_ssh_poke(uintptr_t t);
// The console's task's own wait, which leaves its task notifications alone.
void moy_ssh_console_nap(int ms);
// fn(arg) on a task of its own (a PSRAM stack on a board), its id or 0.
uintptr_t moy_ssh_spawn(void (*fn)(void *arg), const char *name, uint32_t stack, void *arg);
// fn(arg) on the console's task, waiting for it; in place when called there.
void moy_ssh_hop(void (*fn)(void *arg), void *arg);
// The console's task, at every frame's tail: the hop that waits, run.
void moy_ssh_tail(void);
// Whether the console's task is inside a hop now (a session's command).
int moy_ssh_serving(void);

// The run, on the console's task: serving on `port` (the face's shell set
// first, moy_ssh_face_set_shell), 0, MOY_SSH_EADDRINUSE or MOY_SSH_ENOMEM;
// serving already, 0. The stop, and whether it serves. The tasks' ids, 0
// before the first start made them.
#define MOY_SSH_EADDRINUSE (-1)
#define MOY_SSH_ENOMEM (-2)
int moy_ssh_run_start(int port);
void moy_ssh_run_stop(void);
int moy_ssh_run_listening(void);
void moy_ssh_run_tasks(uintptr_t *server, uintptr_t *face);

void *moy_ssh_calloc(size_t n, size_t size);
void moy_ssh_free(void *p);

#endif // MOY_SSH_H
