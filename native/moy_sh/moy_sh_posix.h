// moy_sh_posix.h: the operating system dash sees.
//
// dash was written against POSIX: file descriptors, fork and exec, wait,
// signals, a terminal. The kernel has none of them, so every call dash makes
// to the OS is a macro here naming moy_sh_sys.c's version, over the session's
// own descriptor table (moy_sh_g.h, `struct moy_sh_proc`) and the files tree
// (the store's moy_vol) -- never the C library's, which on the host would
// reach the real processes and files. The patch includes this file after each
// dash source's system headers (and at the top of system.h), so a system
// header's prototype is never renamed.
//
// What each call means here:
//   descriptors   open/read/write/close/dup/dup2/fcntl/pipe/lseek over
//                 objects: the terminal, a pipe buffer, a file read whole at
//                 open and written whole at its last close, /dev and /proc
//   files         stat/lstat/fstat, opendir/readdir/closedir, access, chdir
//                 and getcwd over the session's working folder
//   processes     fork, execve and wait are not here: the shell asks the OS
//                 (moy_sh.h, "fork"); kill reaches the host's processes;
//                 _exit ends the running child or the session's run
//   signals       recorded and never delivered; an interrupt is the host's
//                 poll (moy_sh.h), which dotrap raises as SIGINT
//   terminal      isatty is 1 for the terminal object; there is no tty
//                 control, job control is the shell's own

#ifndef MOY_SH_POSIX_H
#define MOY_SH_POSIX_H

// Every system header a dash source includes, before the macros below exist,
// so no later inclusion of one renames its prototypes.
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <locale.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/param.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#include <wchar.h>
#if __has_include(<pwd.h>)
#include <pwd.h>
#endif
#if __has_include(<sys/wait.h>)
#include <sys/wait.h>
#endif
#if __has_include(<sys/resource.h>)
#include <sys/resource.h>
#endif
#if __has_include(<paths.h>)
#include <paths.h>
#endif

typedef struct moy_sh_dir moy_sh_DIR;

int moy_sh_open(const char *path, int flags, ...);
ssize_t moy_sh_read(int fd, void *buf, size_t n);
ssize_t moy_sh_write(int fd, const void *buf, size_t n);
int moy_sh_close(int fd);
int moy_sh_dup(int fd);
int moy_sh_dup2(int fd, int to);
int moy_sh_fcntl(int fd, int cmd, ...);
int moy_sh_pipe(int fds[2]);
off_t moy_sh_lseek(int fd, off_t at, int whence);
int moy_sh_stat(const char *path, struct stat *st);
int moy_sh_fstat(int fd, struct stat *st);
moy_sh_DIR *moy_sh_opendir(const char *path);
struct dirent *moy_sh_readdir(moy_sh_DIR *d);
int moy_sh_closedir(moy_sh_DIR *d);
int moy_sh_access(const char *path, int mode);
int moy_sh_faccessat(int dirfd, const char *path, int mode, int flags);
int moy_sh_chdir(const char *path);
char *moy_sh_getcwd(char *buf, size_t n);
int moy_sh_isatty(int fd);
mode_t moy_sh_umask(mode_t m);
pid_t moy_sh_getpid(void);
int moy_sh_kill(pid_t pid, int sig);
int moy_sh_raise(int sig);
void moy_sh__exit(int status) __attribute__((__noreturn__));
int moy_sh_sigaction(int sig, const struct sigaction *act, struct sigaction *old);
int moy_sh_sigprocmask(int how, const sigset_t *set, sigset_t *old);
int moy_sh_tcgetattr(int fd, struct termios *t);
const char *moy_sh_strsignal(int sig);
long moy_sh_sysconf(int name);
int moy_sh_unsupported(const char *what) __attribute__((__noreturn__));

// dash's memory: the session's arena (moy_sh_arena.h).
void *moy_sh_malloc(size_t n);
void *moy_sh_realloc(void *p, size_t n);
char *moy_sh_strdup(const char *s);
void moy_sh_mfree(void *p);

// Where dash forked, waited and exec'd (moy_sh.h, "THE FORK").
struct builtincmd;
int moy_sh_fork(void (*child)(void *), void *arg, int bg);
int moy_sh_wait(int *wstatus, int flags);
int moy_sh_children(void);
int moy_sh_exec(char **argv, const char *path, int idx);
int moy_sh_poll(void);
struct job;
void moy_sh_foreground(struct job *jp);
const struct builtincmd *moy_sh_find_builtin(const char *name);

#define stat64 stat
#define fstat64 fstat
#define lstat64 lstat
#define open64 open
#define dirent64 dirent
#define DIR moy_sh_DIR

#define open(...) moy_sh_open(__VA_ARGS__)
#define read(fd, b, n) moy_sh_read(fd, b, n)
#define write(fd, b, n) moy_sh_write(fd, b, n)
#define close(fd) moy_sh_close(fd)
#define dup(fd) moy_sh_dup(fd)
#define dup2(fd, to) moy_sh_dup2(fd, to)
#define fcntl(...) moy_sh_fcntl(__VA_ARGS__)
#define pipe(fds) moy_sh_pipe(fds)
#define lseek(fd, at, w) moy_sh_lseek(fd, at, w)
#define stat(p, st) moy_sh_stat(p, st)
#define lstat(p, st) moy_sh_stat(p, st)
#define fstat(fd, st) moy_sh_fstat(fd, st)
#define opendir(p) moy_sh_opendir(p)
#define readdir64(d) moy_sh_readdir(d)
#define readdir(d) moy_sh_readdir(d)
#define closedir(d) moy_sh_closedir(d)
#define access(p, m) moy_sh_access(p, m)
#define faccessat(d, p, m, f) moy_sh_faccessat(d, p, m, f)
#define chdir(p) moy_sh_chdir(p)
#define getcwd(b, n) moy_sh_getcwd(b, n)
#define isatty(fd) moy_sh_isatty(fd)
#define umask(m) moy_sh_umask(m)
#define getpid() moy_sh_getpid()
#define getppid() 0
#define getuid() 0
#define geteuid() 0
#define getgid() 0
#define getegid() 0
#define getpgrp() moy_sh_getpid()
#define setpgid(pid, pgrp) 0
#define tcgetpgrp(fd) (-1)
#define tcsetpgrp(fd, pgrp) 0
#define tcgetattr(fd, t) moy_sh_tcgetattr(fd, t)
#define kill(pid, sig) moy_sh_kill(pid, sig)
#define killpg(pgrp, sig) moy_sh_kill(-(pgrp), sig)
// printf's \u escape stores UTF-8 big-endian; both chips are little-endian
#undef htonl
#define htonl(v) __builtin_bswap32(v)
_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "htonl above assumes a little-endian chip");

// dash installs its handlers with sigaction; signal() only restores defaults
typedef void (*moy_sh_sig_t)(int);
static inline moy_sh_sig_t moy_sh_signal(int sig, moy_sh_sig_t fn) {
    (void)sig;
    (void)fn;
    return SIG_DFL;
}

#define raise(sig) moy_sh_raise(sig)
#define _exit(status) moy_sh__exit(status)
#define signal(sig, fn) moy_sh_signal(sig, fn)
#define sigaction(sig, act, old) moy_sh_sigaction(sig, act, old)
#define sigprocmask(how, set, old) moy_sh_sigprocmask(how, set, old)
#define sigsuspend(set) moy_sh_unsupported("sigsuspend")
#define strsignal(sig) moy_sh_strsignal(sig)
#define sysconf(name) moy_sh_sysconf(name)
#define tee(a, b, n, f) (-1)
#define memfd_create(name, f) (-1)
#define fork() moy_sh_unsupported("fork")
#define vfork() moy_sh_unsupported("vfork")
#define execve(p, a, e) moy_sh_unsupported("execve")
#define wait3(st, f, r) moy_sh_unsupported("wait3")
#define waitpid(p, st, f) moy_sh_unsupported("waitpid")
#define getpwnam(name) ((struct passwd *)0)

#endif // MOY_SH_POSIX_H
