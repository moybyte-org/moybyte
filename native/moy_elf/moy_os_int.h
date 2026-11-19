// moy_os_int: what moy_os.c, its file rows (moy_os_files.c), its socket rows
// (moy_os_net.c) and its processes (moy_os_proc.c) share; nothing else
// includes it.
#ifndef MOY_OS_INT_H
#define MOY_OS_INT_H

#include "moy_platform.h"

#include "moy_kmem.h"
#include "moy_tasks.h"

#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_app.h"
#include "moy_evq.h"
#include "moy_evq_codes.h"
#include "moy_os.h"
#include "moy_os_rows.h"
#include "moy_proc.h"
#include "moy_sh_prog.h"
#include "moy_super.h"
#include "moy_screen_slot.h"
#include "moy_vmq.h"

#ifdef MOY_OS_UMODE
#include "moy_umode.h"
#include <dirent.h>
#include <netdb.h>
#endif

#ifdef MOY_ON_BOARD
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#else
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <setjmp.h>
#include <unistd.h>
#endif

// The memory rows' values where a board's C library has no sys/mman.h:
// sdk/sysroot/include/sys/mman.h's, which are Linux's.
#if __has_include(<sys/mman.h>)
#include <sys/mman.h>
#endif
#ifndef MAP_ANONYMOUS
#define PROT_READ 0x1
#define PROT_WRITE 0x2
#define PROT_EXEC 0x4
#define MAP_SHARED 0x01
#define MAP_PRIVATE 0x02
#define MAP_ANONYMOUS 0x20
#define MAP_FAILED ((void *)-1)
#endif

// The sockets' stack: lwIP's calls on a board, the host's own.
#ifdef MOY_ON_BOARD
#define SK(f) lwip_##f
#if __has_include("moy_net.h")
#include "moy_net.h"
#else
int moy_net_link(uint32_t *ip);
#endif
#pragma weak moy_net_link
#else
#define SK(f) f
#endif

// The lease table's sweep, where the image has it.
#include "moy_lease.h"
#pragma weak moy_lease_sweep

#define MAGIC 0x6d6f796fu

typedef struct {
#ifdef MOY_ON_BOARD
    SemaphoreHandle_t h;
    StaticSemaphore_t buf;
#else
    pthread_mutex_t m;
    pthread_cond_t c;
    int n;
    int mutex;                  // a moy_os_lock (take = lock), not a count
#endif
} sem_t_;

#ifdef MOY_ON_BOARD
typedef TaskHandle_t task_t;
#else
typedef pthread_t task_t;
#endif

// -- the records ------------------------------------------------------------------

enum { T_FREE = 0, T_LIVE, T_DONE, T_NEW };     // T_NEW: a slot pthread_create holds
enum { H_FREE = 0, H_MUTEX, H_DIR, H_FD, H_SOCK, H_ADDR };

typedef struct {
    struct moy_os_proc *p;
    task_t task;
    uint8_t state;
    uint16_t gen;
    uint8_t detached;
    uint8_t joined;
    volatile int in_row;
    int err;
    int *errp;                  // where errno is: err, or a U-mode thread's own slot
#ifdef MOY_OS_UMODE
    moy_u_space_t *u;           // a U-mode thread's windows (moy_umode.h)
    void *ustack;               // its U stack, the u_area_t at its top
    uint32_t usp;               // the stack pointer a hook starts on
#endif
    void *(*start)(void *);
    void *arg;
    void *out;
    sem_t_ done;
#ifndef MOY_ON_BOARD
    jmp_buf home;               // thr_main's frame, where an ending thread returns to
#endif
} thr_t;

typedef struct {
    struct dirent ent;
    char *names;                // NUL-separated, a byte before each its kind ('d', 'f')
    size_t n;
    size_t at;
} dir_t;

typedef struct {
    uint8_t kind;
    uint8_t users;              // H_SOCK, H_MUTEX: rows using it now
    uint8_t closing;            // H_SOCK: closed while used; the last user lets it go
    uint16_t gen;
    int fd;                     // H_FD: the session's descriptor; H_SOCK: the stack's descriptor
    uint32_t kinds;             // H_SOCK: SK_NONBLOCK
    void *ptr;                  // H_ADDR: the stack's lookup answer
    uint32_t rcv_ms, snd_ms;    // H_SOCK: SO_RCVTIMEO and SO_SNDTIMEO (0: none)
} handle_t;

#define SK_NONBLOCK 1u

typedef struct map {
    struct map *next;
    size_t len;
    void *mem;                  // a U-mode process's: its own 128-aligned block, the record apart
} map_t;
#define MAP_HDR ((sizeof(map_t) + 15u) & ~(size_t)15u)

// A game's channels (moy_os.h, A GAME'S CHANNELS), freed with its record.
struct moycore_files;
typedef struct {
    int32_t sound[MOY_OS_SOUND_MAX * 3];
    uint32_t sounds;            // verbs queued, under the table's lock
    int32_t pmem[256];
    uint8_t pmem_moved;
    moy_os_snd_fn snd;          // the stream's sink, NULL for none
    struct moycore_files *files;
} game_ch_t;

// native/moycore/moycore_files.h's, where the image links moycore.
struct moycore_files *moycore_files_open(const char *dir, const char *writable, size_t n);
void moycore_files_close(struct moycore_files *s);
int32_t moycore_files_read(struct moycore_files *s, const char *name, size_t n,
                           uint32_t offset, uint8_t *dst, uint32_t len);
int32_t moycore_files_write(struct moycore_files *s, const char *path, size_t n,
                            const uint8_t *data, uint32_t len);
int32_t moycore_files_erase(struct moycore_files *s, const char *path, size_t n);
int32_t moycore_files_list(struct moycore_files *s, const char *prefix, size_t n,
                           uint32_t index, uint8_t *dst, uint32_t cap);
#pragma weak moycore_files_open
#pragma weak moycore_files_close
#pragma weak moycore_files_read
#pragma weak moycore_files_write
#pragma weak moycore_files_erase
#pragma weak moycore_files_list

struct moy_os_proc {
    uint32_t magic;
    uint32_t pid;
    uint32_t row;               // its moy_proc row
    moy_os_module_t mod;
    moy_os_caps_t caps;
    uint8_t service;
    volatile uint8_t ending;
    volatile uint8_t main_done;
    int code;
    uint32_t hooks;
    // a command's
    moy_sh_t *s;
    moy_sh_prog_t prog;
    int argc;
    char **argv;
    char *env;                  // "K=V\0" each, an empty one last
    char cwd[MOY_SH_PROG_PATH];
    // the session's calls, served by the waiting task
    sem_t_ wake;
    sem_t_ hop_done;
    sem_t_ hop_lock;
    void (*hop_fn)(void *arg);
    void *hop_arg;
    // its event descriptor
    volatile uint8_t hup;       // a service's stop asked: the descriptor hangs up
    uint8_t own_q;              // its event queue opened here, closed at its end
    int64_t woke_us;            // when its last poll of the descriptor returned (0: none yet)
    int64_t started_us;         // a service's start: its first wake runs from here
    volatile uint8_t polling;   // inside a poll of the descriptor: no wake runs
    volatile uint8_t hopping;   // threads waiting on the VM's queue (moy_vmq.h)
    volatile uint8_t here;      // threads in a row answered here (the store's work)
    uint8_t struck;             // aborted strikes the watchdog charged the wake running
    uint8_t reaped;             // its end reported (moy_os_reap)
    uint32_t cpu_us;            // its wakes' cost, summed (wrapping)
    sem_t_ stopped;
    // what it holds
    thr_t thr[MOY_OS_THREADS];
    handle_t h[MOY_OS_HANDLES];
    map_t *maps;                // mmap's, newest first
    size_t mapped;
    uint32_t flags;             // MOY_OS_NET
    moy_app_run_t run;          // its app run: what its app calls are answered for
    volatile uint8_t net_held;
    uint32_t wakes, events;
    uint8_t game;               // a game's (moy_os_game): its screen is a slot, moy_screen_slot.h
    uint8_t *data;              // a game's data, handed at its start (moy_game_data)
    size_t data_n;
    game_ch_t *ch;              // a game's channels (moy_os_game)
#ifdef MOY_OS_UMODE
    moy_u_proc_t *u;            // its memory, when its module runs in U-mode
    char **uargv;               // argv and the environment, in its main thread's U area
    char *uenv;
    struct retired *retired;    // memory let go while another thread may hold it
#endif
};

#ifdef MOY_OS_UMODE
// What a U-mode thread's rows hand back that the module reads: at the top of
// its U stack, so inside its windows.
typedef struct {
    int err;
    struct dirent ent;
} u_area_t;

typedef struct retired {
    struct retired *next;
    void *mem;
} retired_t;
#endif

// A file row's operation and its call, as its session body runs it.
enum { F_OPEN = 1, F_CLOSE, F_READ, F_WRITE, F_SEEK, F_STAT, F_FSTAT, F_UNLINK, F_MKDIR,
       F_RMDIR, F_RENAME, F_LIST, F_CWD, F_TERM };

typedef struct {
    moy_os_proc_t *p;
    int op;
    int fd;
    int flags;
    const char *path;
    const char *path2;
    void *buf;
    size_t n;
    int64_t at;
    int whence;
    long r;                     // the answer: >= 0, or minus an errno value
    moy_sh_stat_t st;
    int what;
    uint32_t size;
    dir_t *dir;
} fcall_t;

// The process table and the stats, the network's holder and the errno of a
// thread no process owns (moy_os.c).
extern moy_os_proc_t *moy_os_g_proc[MOY_OS_PROCS];
extern moy_os_stats_t moy_os_g_stats;
extern moy_os_net_fn moy_os_g_net;
extern void *moy_os_g_net_ctx;
extern int moy_os_g_err_none;              // errno for a thread no process owns

// moy_os.c's platform, records, threads and handles, the file rows' call
// (moy_os_files.c), the sockets' (moy_os_net.c) and the processes'
// (moy_os_proc.c), as the other files reach them.
int moy_os_doze(thr_t *t, int64_t us);
bool moy_os_ending(thr_t *t);
thr_t *moy_os_enter(void);
long moy_os_evq_read(moy_os_proc_t *p, void *buf, size_t n);
long moy_os_fcall(thr_t *t, fcall_t *c);
int moy_os_fd_owned(moy_os_proc_t *p, int fd);
void moy_os_handle_drop(moy_os_proc_t *p, uint32_t i);
uint32_t moy_os_handle_id(const moy_os_proc_t *p, int i);
int moy_os_handle_new(moy_os_proc_t *p, uint8_t kind, void *ptr, int fd);
int moy_os_handle_of(const moy_os_proc_t *p, uint32_t id, uint8_t kind);
void moy_os_leave(thr_t *t);
int moy_os_live_threads(const moy_os_proc_t *p);
void moy_os_lock(void);
void moy_os_log_line(moy_os_proc_t *p, int level, const char *text);
int moy_os_loop_stop(moy_os_proc_t *p);
int moy_os_on_session(thr_t *t, void (*fn)(void *arg), void *arg);
void *moy_os_alloc(size_t n);
void moy_os_free(void *p);
moy_os_proc_t *moy_os_proc_of(uint32_t pid);
void moy_os_sem_fini(sem_t_ *s);
void moy_os_sem_give(sem_t_ *s);
void moy_os_sem_init(sem_t_ *s, bool mutex);
bool moy_os_sem_take(sem_t_ *s, int64_t us);
void moy_os_sock_close(moy_os_proc_t *p, int k);
long moy_os_sock_io(thr_t *t, int slot, void *buf, size_t n, int flags, bool wr);
void moy_os_sock_let(thr_t *t, int k);
int moy_os_sock_of(thr_t *t, int fd);
int moy_os_sock_slot(moy_os_proc_t *p, int fd);
void moy_os_self_clear(thr_t *t);
void moy_os_task_feed(void);
size_t moy_os_text_len(thr_t *t, const char *p, size_t cap);
void moy_os_thr_reap(thr_t *t);
int moy_os_thr_spawn(thr_t *t);
void moy_os_unlock(void);
#ifdef MOY_OS_UMODE
u_area_t *moy_os_u_area(const thr_t *t);
void *moy_os_u_map(moy_os_proc_t *p, size_t len);
void moy_os_u_scrub_free(void *mem);
void moy_os_u_thread_end(thr_t *t);
bool moy_os_u_unmap(moy_os_proc_t *p, void *addr, bool locked);
#endif

#endif // MOY_OS_INT_H
