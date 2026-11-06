// moy_ssh_port: what Dropbear asks of a POSIX system, answered for the
// console (moy_ssh_port.c). Included by localoptions.h, so every Dropbear
// file sees these names; nothing outside Dropbear does.
//
// One user: any name signs in as the console's one account, its password
// the hash in the store (moy_ssh.h). No fork, no pty, no signals: a
// session's command is the shell's (moy_ssh_command), and the server's exit
// is a jump back to the accept loop (moy_ssh_exit_jump).

#ifndef MOY_SSH_PORT_H
#define MOY_SSH_PORT_H

#include <stddef.h>
#include <stdarg.h>
#include <sys/types.h>

struct passwd;
struct Channel;
struct ChanSess;

struct passwd *moy_ssh_getpwnam(const char *name);
char *moy_ssh_crypt(const char *key, const char *salt);
int moy_ssh_getgroups(int n, gid_t *list);
void moy_ssh_entropy(void *buf, size_t n);
void moy_ssh_log(int priority, const char *msg);
void moy_ssh_exit_jump(int code);

// The session channel's command: `cmd` (NULL: the interactive shell), with a
// terminal (`pty`) or not. Answers 0 and the channel's descriptor, or -1.
int moy_ssh_command(const char *cmd, int pty, int cols, int rows, int *fd, pid_t *pid);
// A command that has finished since the last call: 1 with its id and exit
// status, 0 when none.
int moy_ssh_reap(pid_t *pid, int *status);
// A signal the client sent to the command (`signal` request).
int moy_ssh_signal(pid_t pid, int sig);
void moy_ssh_winch(pid_t pid, int cols, int rows);

// Memory for every Dropbear allocation (PSRAM on a board), counted.
void *moy_ssh_calloc(size_t n, size_t size);
void *moy_ssh_realloc(void *p, size_t n);
void moy_ssh_free(void *p);

#define getpwnam moy_ssh_getpwnam
#define crypt moy_ssh_crypt
#define getgroups moy_ssh_getgroups
#define DB_CALLOC moy_ssh_calloc
#define DB_REALLOC moy_ssh_realloc
#define DB_FREE moy_ssh_free

// MicroPython has its own m_malloc family: Dropbear's are renamed.
#define m_malloc moy_db_malloc
#define m_calloc moy_db_calloc
#define m_strdup moy_db_strdup
#define m_realloc moy_db_realloc
#define m_free_direct moy_db_free

// Dropbear's and libtomcrypt's big globals are PSRAM, taken at the server's
// setup (moy_ssh_globals): each name is the object a pointer points at, so
// the definition `struct sshsession ses;` defines the pointer and every use
// reads through it. What stays internal is the algorithm lists and a few words.
#define ses (*moy_ssh_g_ses)
#define svr_ses (*moy_ssh_g_svr_ses)
#define opts (*moy_ssh_g_opts)
#define svr_opts (*moy_ssh_g_svr_opts)
#define cipher_descriptor (*moy_ssh_g_ciphers)
#define hash_descriptor (*moy_ssh_g_hashes)
int moy_ssh_globals(void);

#ifdef ESP_PLATFORM
// lwIP has no reverse lookup; Dropbear asks only for numeric hosts.
#define gethostbyaddr(addr, len, type) ((struct hostent *)NULL)
#endif

#endif // MOY_SSH_PORT_H
