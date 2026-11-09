// moy_os: a native module's process and the OS table's rows (moy_os.h).

// A board is told by its heap header: the esp32 port does not define
// ESP_PLATFORM for a usermod's sources.
#if __has_include("esp_heap_caps.h")
#define MOY_OS_BOARD 1
#endif

#ifndef MOY_OS_BOARD
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#endif

#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_app.h"
#include "moy_evq.h"
#include "moy_os.h"
#include "moy_os_rows.h"
#include "moy_proc.h"
#include "moy_sh_prog.h"
#include "moy_super.h"
#include "moy_vmq.h"

#ifdef MOY_OS_UMODE
#include "moy_umode.h"
#include <dirent.h>
#include <netdb.h>
#endif

#ifdef MOY_OS_BOARD
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
#ifdef MOY_OS_BOARD
#define SK(f) lwip_##f
int moy_net_link(uint32_t *ip) __attribute__((weak));
#else
#define SK(f) f
#endif

// The lease table's sweep, where the image has it.
uint32_t moy_lease_sweep(uint32_t owner) __attribute__((weak));

#define MAGIC 0x6d6f796fu

enum { FB_FREE = 0, FB_WRITING, FB_READY, FB_TAKEN };

// -- the platform: memory, clocks, semaphores, tasks, timers -------------------------

static void *os_alloc(size_t n) {
#ifdef MOY_OS_BOARD
    return heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return calloc(1, n);
#endif
}

static void os_free(void *p) {
#ifdef MOY_OS_BOARD
    heap_caps_free(p);
#else
    free(p);
#endif
}

static int64_t mono_us(void) {
#ifdef MOY_OS_BOARD
    return esp_timer_get_time();
#else
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
#endif
}

typedef struct {
#ifdef MOY_OS_BOARD
    SemaphoreHandle_t h;
    StaticSemaphore_t buf;
#else
    pthread_mutex_t m;
    pthread_cond_t c;
    int n;
    int mutex;                  // a lock (take = lock), not a count
#endif
} sem_t_;

static void sem_init(sem_t_ *s, bool mutex) {
#ifdef MOY_OS_BOARD
    s->h = mutex ? xSemaphoreCreateMutexStatic(&s->buf) : xSemaphoreCreateBinaryStatic(&s->buf);
#else
    pthread_mutex_init(&s->m, NULL);
    pthread_cond_init(&s->c, NULL);
    s->n = mutex ? 1 : 0;
    s->mutex = mutex;
#endif
}

static void sem_fini(sem_t_ *s) {
#ifdef MOY_OS_BOARD
    vSemaphoreDelete(s->h);
#else
    pthread_cond_destroy(&s->c);
    pthread_mutex_destroy(&s->m);
#endif
}

static void sem_give(sem_t_ *s) {
#ifdef MOY_OS_BOARD
    xSemaphoreGive(s->h);
#else
    pthread_mutex_lock(&s->m);
    s->n = 1;
    pthread_cond_signal(&s->c);
    pthread_mutex_unlock(&s->m);
#endif
}

// Taken within `us` (<0: forever): true.
static bool sem_take(sem_t_ *s, int64_t us) {
#ifdef MOY_OS_BOARD
    TickType_t t = us < 0 ? portMAX_DELAY : (TickType_t)((us + 999) / 1000 / portTICK_PERIOD_MS + 1);
    return xSemaphoreTake(s->h, t) == pdTRUE;
#else
    pthread_mutex_lock(&s->m);
    if (us < 0) {
        while (!s->n) {
            pthread_cond_wait(&s->c, &s->m);
        }
    } else {
        struct timespec at;
        clock_gettime(CLOCK_REALTIME, &at);
        int64_t ns = (int64_t)at.tv_nsec + us * 1000;
        at.tv_sec += (time_t)(ns / 1000000000);
        at.tv_nsec = (long)(ns % 1000000000);
        while (!s->n && pthread_cond_timedwait(&s->c, &s->m, &at) == 0) {
        }
    }
    bool got = s->n != 0;
    s->n = 0;
    pthread_mutex_unlock(&s->m);
    return got;
#endif
}

// The table's one lock: the processes, their threads and handles.
static sem_t_ g_lock;
static bool g_lock_made;

static void lock(void) {
    if (!g_lock_made) {
        sem_init(&g_lock, true);
        g_lock_made = true;
    }
    sem_take(&g_lock, -1);
}

static void unlock(void) {
    sem_give(&g_lock);
}

#ifdef MOY_OS_BOARD
typedef TaskHandle_t task_t;
#else
typedef pthread_t task_t;
#endif

static task_t task_self(void) {
#ifdef MOY_OS_BOARD
    return xTaskGetCurrentTaskHandle();
#else
    return pthread_self();
#endif
}

// The task watchdog fed for a task that waits on a module (a background
// command's door): a no-op where the task is not watched.
static void task_feed(void) {
#ifdef MOY_OS_BOARD
    if (esp_task_wdt_status(NULL) == ESP_OK) {
        esp_task_wdt_reset();
    }
#endif
}

static bool task_is(task_t a, task_t b) {
#ifdef MOY_OS_BOARD
    return a == b;
#else
    return pthread_equal(a, b);
#endif
}

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
#ifndef MOY_OS_BOARD
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
    // a game's (moy_os_game): its screen, and the frame moy_present hands over
    uint8_t game;
    volatile uint8_t fb_state;  // FB_FREE, FB_WRITING, FB_READY, FB_TAKEN
    uint8_t fb_posted;          // a frame message posted, the last when the game had
    uint32_t fb_mark;           // read `fb_mark` records: the next waits for another read
    int fb_w, fb_h;
    uint8_t *fb;                // fb_w * fb_h palette indices
    sem_t_ fb_room;             // the frame let go: a present may write it
    sem_t_ fb_shown;            // a present made it READY
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

static moy_os_proc_t *g_proc[MOY_OS_PROCS];
static moy_os_stats_t g_stats;
static moy_os_net_fn g_net;
static void *g_net_ctx;
static int g_err_none;              // errno for a thread no process owns

static void log_line(moy_os_proc_t *p, int level, const char *text) {
    moy_log_write(level, p != NULL ? p->pid : MOY_LOG_KERNEL, text, strlen(text));
}

// A text of the module's length, `cap` at most, read only inside the region
// of its process it starts in: another of its threads may take its NUL away
// after the row's check, and a read past the region could run off the end of
// PSRAM. `cap` or more: no NUL inside both.
static size_t text_len(thr_t *t, const char *p, size_t cap) {
    size_t bound = cap < PTRDIFF_MAX ? cap : PTRDIFF_MAX;
#ifdef MOY_OS_UMODE
    if (t != NULL && t->u != NULL) {
        size_t room = moy_u_room(t->u, p);
        if (room < bound) {
            size_t n = strnlen(p, room);
            return n < room ? n : cap;
        }
    }
#endif
    (void)t;
    size_t n = strnlen(p, bound);
    return n < bound ? n : cap;
}

// Which task is which thread: static, so a lookup never reads a record
// another task's proc_end may be freeing. A thread's entry is set before its
// first row and cleared before its record goes; a running thread's record
// outlives its entry.
static struct {
    volatile task_t task;
    thr_t *volatile thr;
} g_self[MOY_OS_PROCS * MOY_OS_THREADS];

static void self_set(thr_t *t) {
    for (uint32_t i = 0; i < MOY_OS_PROCS * MOY_OS_THREADS; i++) {
        if (g_self[i].thr == NULL) {
            g_self[i].thr = t;
            g_self[i].task = task_self();
            return;
        }
    }
}

static void self_clear(thr_t *t) {
    for (uint32_t i = 0; i < MOY_OS_PROCS * MOY_OS_THREADS; i++) {
        if (g_self[i].thr == t) {
            memset((void *)&g_self[i].task, 0, sizeof g_self[i].task);
            g_self[i].thr = NULL;
        }
    }
}

// The calling thread's record, or NULL: a thread no process owns.
static thr_t *self_thr(void) {
    task_t me = task_self();
    for (uint32_t i = 0; i < MOY_OS_PROCS * MOY_OS_THREADS; i++) {
        thr_t *t = g_self[i].thr;
        if (t != NULL && task_is(g_self[i].task, me)) {
            return t;
        }
    }
    return NULL;
}

void moy_os_errno_set(int e) {
    thr_t *t = self_thr();
    if (t != NULL) {
        *t->errp = e;
    } else {
        g_err_none = e;
    }
}

// -- a thread's life --------------------------------------------------------------

static void thr_end(thr_t *t) __attribute__((noreturn));

// A joiner and the process's waiters woken, then the record DONE: its last
// touch of the record and the process, which a reaper that sees it frees.
static void thr_done(thr_t *t) {
    lock();
    self_clear(t);
    sem_give(&t->done);
    sem_give(&t->p->wake);
    sem_give(&t->p->stopped);
    t->state = T_DONE;
    unlock();
}

// The thread stops where it stands: DONE and parked for its reaper (a board),
// or back in thr_main's frame by longjmp (a host), which ends it there.
// Never pthread_exit: its unwinding walks the module's frames, whose code
// and unwind tables the module's unload takes away.
static void thr_end(thr_t *t) {
#ifdef MOY_OS_BOARD
    thr_done(t);
    for (;;) {
        vTaskSuspend(NULL);
    }
#else
    longjmp(t->home, 1);
#endif
}

// A row's start and end. `in_row` counts the kernel code a thread is in: a
// thread inside some is never deleted where it stands (proc_end), and one
// whose process is ending leaves at the row's end instead, still counted, so
// its end is DONE and never a delete of kernel code.
static thr_t *enter(void) {
    thr_t *t = self_thr();
    if (t != NULL) {
        t->in_row++;
    }
    return t;
}

static void leave(thr_t *t) {
    if (t == NULL) {
        return;
    }
    if (t->in_row == 1 && t->p->ending) {
        thr_end(t);
    }
    t->in_row--;
}

static bool ending(thr_t *t) {
    return t != NULL && t->p->ending;
}

typedef struct {
    thr_t *t;
    int op;
} go_t;

#ifdef MOY_OS_UMODE
static u_area_t *u_area(const thr_t *t) {
    return (u_area_t *)t->u->shi - 1;
}

// The module's code at `fn` in U-mode on thread t (moy_umode.h): a0..a4 = a.
static int u_call(thr_t *t, uint32_t fn, const uint32_t a[5]) {
    return moy_u_run(t->u, fn, t->p->mod.uret, t->usp, a);
}
#endif

// The module's entry for `op` on thread t: its own code, in U-mode where the
// module runs there.
static int mod_call(thr_t *t, int op, int argc, char **argv) {
    moy_os_proc_t *p = t->p;
#ifdef MOY_OS_UMODE
    if (p->u != NULL) {
        uint32_t a[5] = { p->mod.utable, (uint32_t)op, (uint32_t)argc, (uint32_t)argv, 0 };
        return u_call(t, (uint32_t)p->mod.entry, a);
    }
#endif
    return p->mod.entry(&moy_os_table, op, argc, argv, NULL);
}

#ifdef MOY_OS_BOARD
static void thr_main(void *arg)
#else
static void *thr_main(void *arg)
#endif
{
    thr_t *t = arg;
    moy_os_proc_t *p = t->p;
    const bool service = p->service;
    t->task = task_self();
    lock();
    self_set(t);
    unlock();
#ifndef MOY_OS_BOARD
    if (setjmp(t->home) != 0) {
        thr_done(t);
        return NULL;
    }
#endif
#ifdef MOY_OS_UMODE
    // A U-mode module's constructors run on its first thread (on a board's S3
    // the loader runs them).
    if (t == &p->thr[0] && p->u != NULL) {
        for (uint32_t i = 0; i < p->mod.uinits; i++) {
            uint32_t a[5] = { 0 };
            t->in_row = 0;
            u_call(t, p->mod.uinit[i], a);
            t->in_row = 1;
        }
    }
#endif
    if (t == &p->thr[0]) {
        // A command's main, or a service's loop.
#ifdef MOY_OS_UMODE
        char **argv = p->u != NULL ? p->uargv : p->argv;
#else
        char **argv = p->argv;
#endif
        t->in_row = 0;
        int code = mod_call(t, MOY_ELF_OP_MAIN, p->argc, argv);
        t->in_row = 1;
        lock();
        if (!p->main_done) {
            p->code = code;
            p->main_done = 1;
        }
        unlock();
        if (service) {
            moy_evq_want(MOY_EVQ_WANT_POLL);    // its reap is the VM's
        }
    } else {
        t->in_row = 0;
#ifdef MOY_OS_UMODE
        if (p->u != NULL) {
            uint32_t a[5] = { (uint32_t)t->arg };
            t->out = (void *)(uintptr_t)u_call(t, (uint32_t)t->start, a);
        } else
#endif
        t->out = t->start(t->arg);
        t->in_row = 1;
    }
    thr_end(t);
#ifndef MOY_OS_BOARD
    return NULL;
#endif
}

// A task for record `t` with the process's caps: 0, or an errno value.
#ifdef MOY_OS_UMODE
static size_t u_strings(char **v, size_t *count) {
    size_t n = 0;
    *count = 0;
    for (; v != NULL && *v != NULL; v++) {
        n += strlen(*v) + 1;
        (*count)++;
    }
    return n;
}

static size_t env_len(const char *e) {
    size_t n = 1;
    for (; e != NULL && *e; e += strlen(e) + 1) {
        n += strlen(e) + 1;
    }
    return n;
}

// Memory a process could read, zeroed and freed: what a dead process held
// never reaches the next holder of the heap's block.
static void u_scrub_free(void *mem) {
    if (mem != NULL) {
        memset(mem, 0, heap_caps_get_allocated_size(mem));
        heap_caps_free(mem);
    }
}

// Thread t's U stack and windows: its u_area_t at the top and, for the first
// thread, argv and the environment below it. 0, or an errno value.
static int u_thread(thr_t *t) {
    moy_os_proc_t *p = t->p;
    bool first = t == &p->thr[0];
    size_t argc = 0;
    size_t extra = first ? u_strings(p->argv, &argc) + (argc + 1) * sizeof(char *)
                           + env_len(p->env) + 16 : 0;
    uint32_t len = MOY_U_UP(p->caps.stack + sizeof(u_area_t) + extra);
    uint8_t *mem = heap_caps_aligned_alloc(MOY_U_ALIGN, len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    moy_u_space_t *u = os_alloc(sizeof *u);
    if (mem == NULL || u == NULL || moy_u_add(p->u, (uint32_t)mem, (uint32_t)mem + len,
                                              MOY_U_R | MOY_U_W) != 0) {
        heap_caps_free(mem);
        os_free(u);
        return ENOMEM;
    }
    memset(mem, 0, len);        // nothing of the heap's last holder shows
    u->proc = p->u;
    u->id = moy_u_new_id();
    u->slo = (uint32_t)mem;
    u->shi = (uint32_t)mem + len;
    u->ending = &p->ending;
    u->thr = t;
    t->u = u;
    t->ustack = mem;
    u_area_t *area = u_area(t);
    memset(area, 0, sizeof *area);
    t->errp = &area->err;
    uint8_t *at = (uint8_t *)area;
    if (first) {
        size_t n = env_len(p->env);
        at -= n;
        p->uenv = (char *)at;
        if (p->env != NULL) {
            memcpy(at, p->env, n - 1);
        }
        at[n - 1] = 0;
        size_t sn = u_strings(p->argv, &argc);
        at -= sn;
        char *str = (char *)at;
        at = (uint8_t *)(((uint32_t)at - (argc + 1) * sizeof(char *)) & ~3u);
        p->uargv = (char **)at;
        for (size_t i = 0; i < argc; i++) {
            size_t k = strlen(p->argv[i]) + 1;
            memcpy(str, p->argv[i], k);
            p->uargv[i] = str;
            str += k;
        }
        p->uargv[argc] = NULL;
    }
    t->usp = (uint32_t)at & ~15u;
    return 0;
}

static bool u_unmap(moy_os_proc_t *p, void *addr, bool locked);

// Memory a U-mode process held, freed now when no other thread of it can be
// inside a row or running on another core, else at the process's end.
static void u_free(moy_os_proc_t *p, void *mem) {
    bool busy = p->caps.core < 0;
    thr_t *me = self_thr();
    for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
        busy |= &p->thr[k] != me && p->thr[k].state == T_LIVE && p->thr[k].in_row > 0;
    }
    retired_t *r = busy ? os_alloc(sizeof *r) : NULL;
    if (r == NULL) {
        u_scrub_free(mem);
        return;
    }
    r->mem = mem;
    r->next = __atomic_load_n(&p->retired, __ATOMIC_RELAXED);
    while (!__atomic_compare_exchange_n(&p->retired, &r->next, r, false, __ATOMIC_ACQ_REL,
                                        __ATOMIC_RELAXED)) {
    }
}

// A thread's U stack and windows let go, its task gone.
static void u_thread_end(thr_t *t) {
    if (t->u == NULL) {
        return;
    }
    moy_u_drop(t->p->u, t->u->slo);
    u_free(t->p, t->ustack);
    os_free(t->u);
    t->u = NULL;
    t->ustack = NULL;
}
#endif

static int thr_spawn(thr_t *t) {
    moy_os_proc_t *p = t->p;
    t->errp = &t->err;
#ifdef MOY_OS_UMODE
    if (p->u != NULL) {
        int e = u_thread(t);
        if (e != 0) {
            t->state = T_FREE;
            return e;
        }
    }
#endif
    sem_init(&t->done, false);
    t->in_row = 1;              // thr_main's own code until the module's runs
    t->state = T_LIVE;
#ifdef MOY_OS_BOARD
    TaskHandle_t h = NULL;
    BaseType_t core = p->caps.core < 0 ? tskNO_AFFINITY : (BaseType_t)p->caps.core;
    // The record is LIVE before the task can run, so its first row finds it.
    if (xTaskCreatePinnedToCoreWithCaps(thr_main, "moy_elf", p->caps.stack, t,
                                        (UBaseType_t)p->caps.prio, &h, core,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        t->state = T_FREE;
        sem_fini(&t->done);
#ifdef MOY_OS_UMODE
        u_thread_end(t);
#endif
        return EAGAIN;
    }
    t->task = h;
    return 0;
#else
    pthread_attr_t a;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    pthread_attr_setstacksize(&a, p->caps.stack < 65536 ? 65536 : p->caps.stack);
    lock();
    int r = pthread_create(&t->task, &a, thr_main, t);
    unlock();
    pthread_attr_destroy(&a);
    if (r != 0) {
        t->state = T_FREE;
        sem_fini(&t->done);
        return r;
    }
    return 0;
#endif
}

// A DONE thread's task deleted (a board) and its record freed.
static void thr_reap(thr_t *t) {
    self_clear(t);
#ifdef MOY_OS_BOARD
    vTaskDeleteWithCaps(t->task);
#endif
#ifdef MOY_OS_UMODE
    u_thread_end(t);
#endif
    sem_fini(&t->done);
    memset(t, 0, sizeof *t);
}

// -- handles -----------------------------------------------------------------------

static int handle_new(moy_os_proc_t *p, uint8_t kind, void *ptr, int fd) {
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        if (p->h[i].kind == H_FREE) {
            p->h[i].kind = kind;
            p->h[i].gen = (uint16_t)(p->h[i].gen + 1u);
            p->h[i].users = 0;
            p->h[i].closing = 0;
            p->h[i].ptr = ptr;
            p->h[i].fd = fd;
            return (int)i;
        }
    }
    return -1;
}

// Handle `i` given back: what it holds let go.
static void handle_drop(moy_os_proc_t *p, uint32_t i) {
    handle_t *h = &p->h[i];
    switch (h->kind) {
    case H_MUTEX:
        sem_fini(h->ptr);
        os_free(h->ptr);
        break;
    case H_DIR:
        os_free(((dir_t *)h->ptr)->names);
        os_free(h->ptr);
        break;
    case H_FD:
        if (p->s != NULL) {
            moy_sh_fd_close(p->s, h->fd);
        }
        break;
    case H_SOCK:
        SK(close)(h->fd);
        break;
    case H_ADDR:
#ifdef MOY_OS_UMODE
        if (p->u != NULL) {
            u_unmap(p, h->ptr, true);
            break;
        }
#endif
        SK(freeaddrinfo)(h->ptr);
        break;
    default:
        return;
    }
    h->kind = H_FREE;
    h->ptr = NULL;
    h->users = 0;
    h->closing = 0;
}

// A module's id for handle `i`: its slot and its slot's generation, so a
// stale id never names the slot's next holder; never 0 nor all ones
// (PTHREAD_MUTEX_INITIALIZER's).
static uint32_t handle_id(const moy_os_proc_t *p, int i) {
    return (uint32_t)p->h[i].gen << 8 | (uint32_t)(i + 1);
}

static int handle_of(const moy_os_proc_t *p, uint32_t id, uint8_t kind) {
    uint32_t i = (id & 0xffu) - 1u;
    if (i >= MOY_OS_HANDLES || p->h[i].kind != kind || handle_id(p, (int)i) != id) {
        return -1;
    }
    return (int)i;
}

// -- the session's calls ------------------------------------------------------------

// fn(arg) on the task that may touch the session (the command's waiting
// task): 0, or -1 when the process has none or is ending.
static int on_session(thr_t *t, void (*fn)(void *arg), void *arg) {
    moy_os_proc_t *p = t->p;
    if (p->s == NULL) {
        return -1;
    }
    sem_take(&p->hop_lock, -1);
    if (p->ending) {
        sem_give(&p->hop_lock);
        return -1;
    }
    p->hop_arg = arg;
    p->hop_fn = fn;
    sem_give(&p->wake);
    while (!sem_take(&p->hop_done, MOY_OS_SLICE_US)) {
        if (p->ending && p->hop_fn == fn) {
            p->hop_fn = NULL;
            sem_give(&p->hop_lock);
            return -1;
        }
    }
    sem_give(&p->hop_lock);
    return 0;
}

// -- the rows the role door serves -------------------------------------------------
//
// An app call (docs/app_api_v1.md's call table) is the role table's row for the
// process's app run, its fields packed as the door takes them
// (native/moy_app/moy_app.h's moy_app_role): run on the VM's task -- a
// command's through the task that serves its session, a service's as a hop
// on the VM's queue (moy_vmq.h), which the scheduler's poll serves -- or a
// read of what the look and the clipboard write where it is called, under the
// state's seqlock. A hop's wait is not its wake's cost; a queue that is full
// answers EAGAIN.

typedef struct {
    const moy_app_run_t *run;
    uint32_t row;
    const uint8_t *arg;
    size_t n;
    void *out;
    size_t cap;
    int32_t r;
} role_call_t;

// A game's frame call, woken for a hop just queued: it serves the hop at
// once instead of at its next look.
static void hop_queued(void *arg) {
    sem_give(&((moy_os_proc_t *)arg)->fb_shown);
}

// fn(arg) on the VM's task for service `p`, the calling thread waiting until
// it has run (moy_vmq_hop): its wait is moved out of the wake that made it.
// False when the queue is full, fn not run.
static bool vm_hop(moy_os_proc_t *p, void (*fn)(void *), void *arg) {
    uint32_t waited = 0;
    lock();
    p->hopping++;
    unlock();
    int rc = moy_vmq_hop_wake(p->pid, fn, arg, &waited, p->game ? hop_queued : NULL, p);
    lock();
    p->hopping--;
    if (p->woke_us != 0) {
        p->woke_us += waited;
    } else {
        p->started_us += waited;
    }
    unlock();
    return rc == MOY_VMQ_OK;
}

static void role_body(void *arg) {
    role_call_t *c = arg;
    c->r = moy_app_role(c->run->a, c->run->g, c->row, c->arg, c->n, c->out, c->cap);
}

static void put32(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
    q[2] = (uint8_t)(v >> 16);
    q[3] = (uint8_t)(v >> 24);
}

static int role_errno(int32_t r) {
    switch (-r) {
        case MOY_APP_DENIED: case MOY_APP_STALE: return EACCES;
        case MOY_APP_FULL: return ERANGE;
        case MOY_APP_NOMEM: return ENOMEM;
        case MOY_APP_ABSENT: return ENOENT;
        case MOY_APP_NOSTORE: return ENODEV;
        case MOY_APP_IO: return EIO;
        case MOY_APP_NEEDS_VM: return EAGAIN;
        case MOY_APP_FORGED: return EBADMSG;
        case MOY_APP_BUSY: return EBUSY;
        default: return EINVAL;
    }
}

#define MOY_OS_CALL_ARGS 8u          // a row's registers bound its fields

int32_t moy_os_call(uint32_t row, int how, const moy_os_arg_t *a, int n, void *out, size_t cap) {
    thr_t *t = enter();
    moy_os_proc_t *p = t != NULL ? t->p : NULL;
    int32_t r = -MOY_APP_STALE;
    uint8_t stack[256];
    uint8_t *buf = stack;
    size_t len = 0;
    // Each length is measured once: the text is the module's memory, which
    // another of its threads may change between a measure and the copy.
    size_t lens[MOY_OS_CALL_ARGS];
    if (n < 0 || n > (int)MOY_OS_CALL_ARGS) {
        p = NULL;
    }
    if (p != NULL && moy_app_run_live(&p->run)) {
        for (int i = 0; i < n; i++) {
            size_t k = a[i].kind == MOY_OS_ARG_INT ? 4u
                       : a[i].kind == MOY_OS_ARG_BYTES ? a[i].n
                       : a[i].p != NULL ? text_len(t, a[i].p, SIZE_MAX) : 0u;
            if (k == SIZE_MAX) {
                k = 0;
                len = SIZE_MAX / 2;     // no end inside its memory: refused
            }
            lens[i] = k;
            len += 4u + k;
        }
        if (len >= SIZE_MAX / 2) {
            buf = NULL;
        } else if (len > sizeof stack) {
            buf = os_alloc(len);
        }
        if (buf == NULL) {
            r = -MOY_APP_NOMEM;
        } else {
            size_t at = 0;
            for (int i = 0; i < n; i++) {
                if (a[i].kind == MOY_OS_ARG_INT) {
                    put32(buf + at, 4u);
                    put32(buf + at + 4, (uint32_t)a[i].v);
                    at += 8u;
                    continue;
                }
                size_t k = lens[i];
                put32(buf + at, (uint32_t)k);
                if (k) {
                    memcpy(buf + at + 4, a[i].p, k);
                }
                at += 4u + k;
            }
            role_call_t c = { &p->run, row, buf, len, out, out != NULL ? cap : 0u,
                              -MOY_APP_NEEDS_VM };
            if (how == MOY_OS_SEQ) {
                for (;;) {
                    uint32_t q = moy_app_seq_begin(p->run.a);
                    if (!(q & 1u)) {
                        role_body(&c);
                        if (moy_app_seq_end(p->run.a, q)) {
                            break;
                        }
                    }
                }
            } else if (p->s == NULL) {
                vm_hop(p, role_body, &c);
            } else if (on_session(t, role_body, &c) != 0) {
                c.r = -MOY_APP_NEEDS_VM;
            }
            r = c.r;
            if (buf != stack) {
                os_free(buf);
            }
        }
    }
    leave(t);
    if (r < 0) {
        moy_os_errno_set(role_errno(r));
        return -1;
    }
    return r;
}

// -- the rows: files and streams ----------------------------------------------------

// A socket descriptor's handle slot in `p`, or -1, and its hold for a row
// (the sockets' rows, below).
static int sock_slot(moy_os_proc_t *p, int fd);
static int sock_of(thr_t *t, int fd);
static void sock_let(thr_t *t, int k);
static void sock_close(moy_os_proc_t *p, int k);
static long sock_io(thr_t *t, int slot, void *buf, size_t n, int flags, bool wr);
// The event descriptor's read (the rows: the event descriptor, below).
static long evq_read(moy_os_proc_t *p, void *buf, size_t n);

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

enum { F_OPEN = 1, F_CLOSE, F_READ, F_WRITE, F_SEEK, F_STAT, F_FSTAT, F_UNLINK, F_MKDIR,
       F_RMDIR, F_RENAME, F_LIST, F_CWD, F_TERM };

static int fd_owned(moy_os_proc_t *p, int fd) {
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        if (p->h[i].kind == H_FD && p->h[i].fd == fd) {
            return (int)i;
        }
    }
    return -1;
}

// `path` (a shell path) made absolute into out, checked against the roots: 0,
// or an errno value.
static int reach(moy_os_proc_t *p, const char *path, char *out) {
    if (path == NULL) {
        return EFAULT;
    }
    if (moy_sh_abs(p->s, path, out, MOY_SH_PROG_PATH) != 0) {
        return ENAMETOOLONG;
    }
    return moy_sh_prog_reaches(&p->prog, out) ? 0 : EACCES;
}

static int list_ent(void *arg, const char *name, int is_dir, uint32_t size) {
    dir_t *d = arg;
    (void)size;
    size_t k = strlen(name) + 2;
    char *g = os_alloc(d->n + k + 1);
    if (g == NULL) {
        return ENOMEM;
    }
    if (d->names != NULL) {
        memcpy(g, d->names, d->n);
        os_free(d->names);
    }
    g[d->n] = is_dir ? 'd' : 'f';
    memcpy(g + d->n + 1, name, k - 1);
    d->names = g;
    d->n += k;
    return 0;
}

static void f_body(void *arg) {
    fcall_t *c = arg;
    moy_os_proc_t *p = c->p;
    moy_sh_t *s = p->s;
    char abs[MOY_SH_PROG_PATH], abs2[MOY_SH_PROG_PATH];
    int e;
    if (!moy_sh_prog_poll(&p->prog, mono_us())) {
        p->ending = 1;
        c->r = -EINTR;
        return;
    }
    switch (c->op) {
    case F_OPEN: {
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
            break;
        }
        moy_sh_stat_t st;
        if (moy_sh_stat_at(s, abs, &st) == 0 && st.is_dir) {
            c->r = -EISDIR;
            break;
        }
        int acc = c->flags & O_ACCMODE;
        int f = (acc == O_RDONLY || acc == O_RDWR ? MOY_SH_O_READ : 0)
                | (acc == O_WRONLY || acc == O_RDWR ? MOY_SH_O_WRITE : 0)
                | (c->flags & O_CREAT ? MOY_SH_O_CREAT : 0)
                | (c->flags & O_TRUNC ? MOY_SH_O_TRUNC : 0)
                | (c->flags & O_EXCL ? MOY_SH_O_EXCL : 0)
                | (c->flags & O_APPEND ? MOY_SH_O_APPEND : 0);
        int fd = moy_sh_fd_open(s, abs, f);
        if (fd < 0) {
            c->r = fd;
            break;
        }
        lock();
        int h = handle_new(p, H_FD, NULL, fd);
        unlock();
        if (h < 0) {
            moy_sh_fd_close(s, fd);
            c->r = -EMFILE;
            break;
        }
        c->r = fd;
        break;
    }
    case F_CLOSE: {
        int h = fd_owned(p, c->fd);
        if (h < 0) {
            c->r = c->fd >= 0 && c->fd <= 2 ? 0 : -EBADF;
            break;
        }
        lock();
        handle_drop(p, (uint32_t)h);
        unlock();
        c->r = 0;
        break;
    }
    case F_READ:
    case F_WRITE:
        if (c->fd < 0 || (c->fd > 2 && fd_owned(p, c->fd) < 0)) {
            c->r = -EBADF;
        } else if (moy_sh_prog_bg_stdio(&p->prog, c->fd)) {
            if (c->op == F_READ) {
                c->r = c->fd == 0 ? 0 : -EBADF;
            } else if (c->fd == 0) {
                c->r = -EBADF;
            } else {
                moy_sh_prog_bg_write(&p->prog, c->fd, c->buf, c->n);
                c->r = (long)c->n;
            }
        } else {
            errno = 0;
            long got = c->op == F_READ ? moy_sh_fd_read(s, c->fd, c->buf, c->n)
                                       : moy_sh_fd_write(s, c->fd, c->buf, c->n);
            c->r = got < 0 ? -(errno ? errno : EIO) : got;
        }
        break;
    case F_SEEK:
        if (fd_owned(p, c->fd) < 0) {
            c->r = c->fd >= 0 && c->fd <= 2 ? -ESPIPE : -EBADF;
        } else {
            int64_t r = moy_sh_fd_seek(s, c->fd, c->at, c->whence);
            c->r = (long)r;
        }
        break;
    case F_STAT:
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
        } else {
            e = moy_sh_stat_at(s, abs, &c->st);
            c->r = e ? -e : 0;
        }
        break;
    case F_FSTAT:
        if (c->fd < 0 || (c->fd > 2 && fd_owned(p, c->fd) < 0)) {
            c->r = -EBADF;
        } else {
            c->size = 0;
            c->what = moy_sh_prog_bg_stdio(&p->prog, c->fd) ? MOY_SH_FD_PIPE
                                                             : moy_sh_fd_what(s, c->fd, &c->size);
            c->r = c->what < 0 ? c->what : 0;
        }
        break;
    case F_UNLINK:
    case F_RMDIR:
    case F_MKDIR:
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
            break;
        }
        if (c->op != F_MKDIR) {
            moy_sh_stat_t st;
            if ((e = moy_sh_stat_at(s, abs, &st)) != 0) {
                c->r = -e;
                break;
            }
            if (c->op == F_UNLINK && st.is_dir) {
                c->r = -EISDIR;
                break;
            }
            if (c->op == F_RMDIR && !st.is_dir) {
                c->r = -ENOTDIR;
                break;
            }
        }
        e = c->op == F_MKDIR ? moy_sh_mkdir_at(s, abs) : moy_sh_remove_at(s, abs);
        c->r = e ? -e : 0;
        break;
    case F_RENAME:
        if ((e = reach(p, c->path, abs)) != 0 || (e = reach(p, c->path2, abs2)) != 0) {
            c->r = -e;
        } else {
            e = moy_sh_rename_at(s, abs, abs2);
            c->r = e ? -e : 0;
        }
        break;
    case F_LIST:
        if ((e = reach(p, c->path, abs)) != 0) {
            c->r = -e;
        } else {
            e = moy_sh_list(s, abs, list_ent, c->dir);
            c->r = e ? -e : 0;
        }
        break;
    case F_CWD:
        snprintf(p->cwd, sizeof p->cwd, "%s", moy_sh_cwd(s));
        c->r = 0;
        break;
    case F_TERM: {
        const moy_sh_host_t *h = moy_sh_host(s);
        int cols = 0, rows = 0;
        if (h->size != NULL) {
            h->size(h->ctx, &cols, &rows);
        }
        c->r = (long)cols << 16 | (long)rows;
        break;
    }
    }
}

// *p copied into `out` and pointed there: false when it does not fit.
static bool path_in(thr_t *t, const char **p, char *out) {
    if (*p == NULL) {
        return true;
    }
    size_t n = text_len(t, *p, MOY_SH_PROG_PATH);
    if (n >= MOY_SH_PROG_PATH) {
        return false;
    }
    memcpy(out, *p, n);
    out[n] = 0;
    *p = out;
    return true;
}

// Call `c` on the session: its answer, errno set below 0.
static long fcall(thr_t *t, fcall_t *c) {
    if (t == NULL) {
        moy_os_errno_set(EPERM);
        return -1;
    }
    c->p = t->p;
    if (t->p->s == NULL && c->op != F_WRITE) {
        moy_os_errno_set(EACCES);
        return -1;
    }
    if (t->p->s == NULL) {
        // A service's stdout and stderr: the log's lines.
        if (c->fd != 1 && c->fd != 2) {
            moy_os_errno_set(EBADF);
            return -1;
        }
        lock();
        moy_sh_prog_bg_write(&t->p->prog, c->fd, c->buf, c->n);
        unlock();
        return (long)c->n;
    }
    // The paths are copied before the hop: the session's task reads them
    // while the module's other threads run on, and could rewrite them.
    char path[MOY_SH_PROG_PATH], path2[MOY_SH_PROG_PATH];
    if (!path_in(t, &c->path, path) || !path_in(t, &c->path2, path2)) {
        moy_os_errno_set(ENAMETOOLONG);
        return -1;
    }
    c->r = -EINTR;
    if (on_session(t, f_body, c) != 0) {
        moy_os_errno_set(EINTR);
        return -1;
    }
    if (c->r < 0) {
        moy_os_errno_set((int)-c->r);
        return -1;
    }
    return c->r;
}

int moy_os_open(const char *path, int flags, int mode) {
    (void)mode;
    thr_t *t = enter();
    fcall_t c = { .op = F_OPEN, .path = path, .flags = flags };
    long r = fcall(t, &c);
    leave(t);
    return (int)r;
}

int moy_os_close(int fd) {
    thr_t *t = enter();
    long r;
    int k = -1;
    if (t != NULL && fd != MOY_EVQ_FD) {
        lock();
        k = sock_slot(t->p, fd);
        if (k >= 0) {
            sock_close(t->p, k);
        }
        unlock();
    }
    if (t != NULL && fd == MOY_EVQ_FD) {
        r = 0;                  // the process's own: it stays open
    } else if (k >= 0) {
        r = 0;
    } else {
        fcall_t c = { .op = F_CLOSE, .fd = fd };
        r = fcall(t, &c);
    }
    leave(t);
    return (int)r;
}

ssize_t moy_os_read(int fd, void *buf, size_t n) {
    thr_t *t = enter();
    long r;
    int k = t != NULL && fd >= MOY_OS_SOCK_FD && fd != MOY_EVQ_FD ? sock_of(t, fd) : -1;
    if (t != NULL && fd == MOY_EVQ_FD) {
        r = evq_read(t->p, buf, n);
    } else if (k >= 0) {
        r = sock_io(t, k, buf, n, 0, false);
        sock_let(t, k);
    } else {
        fcall_t c = { .op = F_READ, .fd = fd, .buf = buf, .n = n };
        r = fcall(t, &c);
    }
    leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_write(int fd, const void *buf, size_t n) {
    thr_t *t = enter();
    long r;
    int k = t != NULL && fd >= MOY_OS_SOCK_FD && fd != MOY_EVQ_FD ? sock_of(t, fd) : -1;
    if (k >= 0) {
        r = sock_io(t, k, (void *)buf, n, 0, true);
        sock_let(t, k);
    } else {
        fcall_t c = { .op = F_WRITE, .fd = fd, .buf = (void *)buf, .n = n };
        r = fcall(t, &c);
    }
    leave(t);
    return (ssize_t)r;
}

off_t moy_os_lseek(int fd, off_t at, int whence) {
    thr_t *t = enter();
    long r;
    if (t != NULL && sock_slot(t->p, fd) >= 0) {
        moy_os_errno_set(ESPIPE);
        r = -1;
    } else {
        fcall_t c = { .op = F_SEEK, .fd = fd, .at = (int64_t)at, .whence = whence };
        r = fcall(t, &c);
    }
    leave(t);
    return (off_t)r;
}

static void fill_stat(struct stat *st, int is_dir, int what, uint32_t size, int64_t mtime) {
    memset(st, 0, sizeof *st);
    st->st_mode = is_dir ? (S_IFDIR | 0755)
                : what == MOY_SH_FD_TERM ? (S_IFCHR | 0666)
                : what == MOY_SH_FD_PIPE ? (S_IFIFO | 0666) : (S_IFREG | 0644);
    st->st_size = (off_t)size;
    st->st_nlink = 1;
    st->st_mtime = (time_t)mtime;
}

int moy_os_stat(const char *path, struct stat *st) {
    thr_t *t = enter();
    fcall_t c = { .op = F_STAT, .path = path };
    long r = fcall(t, &c);
    if (r == 0) {
        fill_stat(st, c.st.is_dir, MOY_SH_FD_FILE, c.st.size, c.st.mtime);
    }
    leave(t);
    return (int)r;
}

int moy_os_fstat(int fd, struct stat *st) {
    thr_t *t = enter();
    if (t != NULL && sock_slot(t->p, fd) >= 0) {
        memset(st, 0, sizeof *st);
        st->st_mode = S_IFSOCK | 0666;
        st->st_nlink = 1;
        leave(t);
        return 0;
    }
    fcall_t c = { .op = F_FSTAT, .fd = fd };
    long r = fcall(t, &c);
    if (r == 0) {
        fill_stat(st, 0, c.what, c.size, 0);
    }
    leave(t);
    return (int)r;
}

int moy_os_isatty(int fd) {
    thr_t *t = enter();
    if (t != NULL && sock_slot(t->p, fd) >= 0) {
        moy_os_errno_set(ENOTTY);
        leave(t);
        return 0;
    }
    fcall_t c = { .op = F_FSTAT, .fd = fd };
    long r = fcall(t, &c);
    leave(t);
    if (r == 0 && c.what != MOY_SH_FD_TERM) {
        moy_os_errno_set(ENOTTY);
    }
    return r == 0 && c.what == MOY_SH_FD_TERM;
}

int moy_os_unlink(const char *path) {
    thr_t *t = enter();
    fcall_t c = { .op = F_UNLINK, .path = path };
    long r = fcall(t, &c);
    leave(t);
    return (int)r;
}

int moy_os_mkdir(const char *path, mode_t mode) {
    (void)mode;
    thr_t *t = enter();
    fcall_t c = { .op = F_MKDIR, .path = path };
    long r = fcall(t, &c);
    leave(t);
    return (int)r;
}

int moy_os_rmdir(const char *path) {
    thr_t *t = enter();
    fcall_t c = { .op = F_RMDIR, .path = path };
    long r = fcall(t, &c);
    leave(t);
    return (int)r;
}

int moy_os_rename(const char *from, const char *to) {
    thr_t *t = enter();
    fcall_t c = { .op = F_RENAME, .path = from, .path2 = to };
    long r = fcall(t, &c);
    leave(t);
    return (int)r;
}

// A DIR the module holds is its handle's id, never a pointer to the record.
DIR *moy_os_opendir(const char *path) {
    thr_t *t = enter();
    dir_t *d = os_alloc(sizeof *d);
    DIR *out = NULL;
    if (d == NULL) {
        moy_os_errno_set(ENOMEM);
    } else {
        fcall_t c = { .op = F_LIST, .path = path, .dir = d };
        if (fcall(t, &c) == 0) {
            lock();
            int h = handle_new(t->p, H_DIR, d, -1);
            unlock();
            if (h >= 0) {
                out = (DIR *)(uintptr_t)handle_id(t->p, h);
                d = NULL;
            } else {
                moy_os_errno_set(EMFILE);
            }
        }
        if (d != NULL) {
            os_free(d->names);
            os_free(d);
        }
    }
    leave(t);
    return out;
}

struct dirent *moy_os_readdir(DIR *dir) {
    thr_t *t = enter();
    struct dirent *out = NULL;
    if (t != NULL) {
        lock();                 // a closedir by another thread frees the record
    }
    int h = t != NULL ? handle_of(t->p, (uint32_t)(uintptr_t)dir, H_DIR) : -1;
    if (h < 0) {
        moy_os_errno_set(EBADF);
    } else {
        dir_t *d = t->p->h[h].ptr;
        if (d->at < d->n) {
            const char *e = d->names + d->at;
            size_t k = strlen(e + 1);
            memset(&d->ent, 0, sizeof d->ent);
            snprintf(d->ent.d_name, sizeof d->ent.d_name, "%s", e + 1);
            d->ent.d_type = e[0] == 'd' ? DT_DIR : DT_REG;
            d->at += k + 2;
            out = &d->ent;
#ifdef MOY_OS_UMODE
            if (t->u != NULL) {
                u_area(t)->ent = d->ent;
                out = &u_area(t)->ent;
            }
#endif
        }
    }
    if (t != NULL) {
        unlock();
    }
    leave(t);
    return out;
}

int moy_os_closedir(DIR *dir) {
    thr_t *t = enter();
    int h = t != NULL ? handle_of(t->p, (uint32_t)(uintptr_t)dir, H_DIR) : -1;
    if (h >= 0) {
        lock();
        handle_drop(t->p, (uint32_t)h);
        unlock();
    } else {
        moy_os_errno_set(EBADF);
    }
    leave(t);
    return h >= 0 ? 0 : -1;
}

char *moy_os_getcwd(char *buf, size_t n) {
    thr_t *t = enter();
    char *out = NULL;
    fcall_t c = { .op = F_CWD };
    if (fcall(t, &c) == 0) {
        if (strlen(t->p->cwd) + 1 > n) {
            moy_os_errno_set(ERANGE);
        } else if (buf != NULL) {
            memcpy(buf, t->p->cwd, strlen(t->p->cwd) + 1);
            out = buf;
        } else {
            moy_os_errno_set(EINVAL);
        }
    }
    leave(t);
    return out;
}

char *moy_os_getenv(const char *name) {
    thr_t *t = enter();
    char *out = NULL;
    size_t k = name != NULL ? text_len(t, name, MOY_SH_PROG_PATH) : 0;
    k = k < MOY_SH_PROG_PATH ? k : 0;
#ifdef MOY_OS_UMODE
    char *env = t == NULL ? NULL : t->p->u != NULL ? t->p->uenv : t->p->env;
#else
    char *env = t != NULL ? t->p->env : NULL;
#endif
    for (char *e = env; e != NULL && *e && k; e += strlen(e) + 1) {
        if (strncmp(e, name, k) == 0 && e[k] == '=') {
            out = e + k + 1;
            break;
        }
    }
    leave(t);
    return out;
}

// -- the rows: the process ---------------------------------------------------------

void moy_os_exit(int code) {
    moy_os__exit(code);
}

void moy_os__exit(int code) {
    thr_t *t = enter();
    if (t == NULL) {
        abort();
    }
    moy_os_proc_t *p = t->p;
    const bool service = p->service;
    lock();
    if (!p->main_done) {
        p->code = code & 0xff;
        p->main_done = 1;
    }
    p->ending = 1;
    unlock();
    if (service) {
        moy_evq_want(MOY_EVQ_WANT_POLL);
    }
    thr_end(t);
}

int *moy_os___errno(void) {
    thr_t *t = self_thr();
    return t != NULL ? t->errp : &g_err_none;
}

// -- the rows: time ------------------------------------------------------------------

time_t moy_os_time(time_t *out) {
    time_t now = time(NULL);
    if (out != NULL) {
        *out = now;
    }
    return now;
}

int moy_os_clock_gettime(clockid_t clock, struct timespec *ts) {
    if (ts == NULL) {
        moy_os_errno_set(EFAULT);
        return -1;
    }
    if (clock_gettime(clock, ts) != 0) {
        moy_os_errno_set(errno);
        return -1;
    }
    return 0;
}

int moy_os_gettimeofday(struct timeval *tv, void *tz) {
    (void)tz;
    if (tv == NULL) {
        return 0;
    }
    if (gettimeofday(tv, NULL) != 0) {
        moy_os_errno_set(errno);
        return -1;
    }
    return 0;
}

// Sleep `us` in slices, leaving early when the process ends: 0, or EINTR.
static int doze(thr_t *t, int64_t us) {
    int64_t end = mono_us() + us;
    for (int64_t left = us; left > 0; left = end - mono_us()) {
        if (ending(t)) {
            return EINTR;
        }
        int64_t d = left < MOY_OS_SLICE_US ? left : MOY_OS_SLICE_US;
#ifdef MOY_OS_BOARD
        vTaskDelay((TickType_t)((d + 999) / 1000 / portTICK_PERIOD_MS + 1));
#else
        struct timespec ts = { (time_t)(d / 1000000), (long)(d % 1000000) * 1000 };
        nanosleep(&ts, NULL);
#endif
    }
    return 0;
}

int moy_os_nanosleep(const struct timespec *req, struct timespec *rem) {
    thr_t *t = enter();
    int r = 0;
    if (req == NULL || req->tv_nsec < 0 || req->tv_nsec >= 1000000000) {
        moy_os_errno_set(EINVAL);
        r = -1;
    } else if (doze(t, (int64_t)req->tv_sec * 1000000 + req->tv_nsec / 1000) != 0) {
        if (rem != NULL) {
            rem->tv_sec = 0;
            rem->tv_nsec = 0;
        }
        moy_os_errno_set(EINTR);
        r = -1;
    }
    leave(t);
    return r;
}

int moy_os_usleep(useconds_t us) {
    thr_t *t = enter();
    int r = doze(t, (int64_t)us);
    if (r) {
        moy_os_errno_set(r);
    }
    leave(t);
    return r ? -1 : 0;
}

unsigned moy_os_sleep(unsigned s) {
    thr_t *t = enter();
    int r = doze(t, (int64_t)s * 1000000);
    leave(t);
    return r ? s : 0;
}

// -- the rows: threads -----------------------------------------------------------------

// pthread_t is the thread's slot and the slot's generation, never 0: a stale
// one never names the slot's next thread.
static uint32_t thr_id(const moy_os_proc_t *p, const thr_t *t) {
    return (uint32_t)t->gen << 8 | (uint32_t)(t - p->thr + 1);
}

static thr_t *thr_of(moy_os_proc_t *p, uint32_t id) {
    uint32_t i = (id & 0xffu) - 1u;
    if (i >= MOY_OS_THREADS || (p->thr[i].state != T_LIVE && p->thr[i].state != T_DONE)
        || p->thr[i].gen != (id >> 8)) {
        return NULL;
    }
    return &p->thr[i];
}

int moy_os_pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                          void *(*start)(void *), void *arg) {
    (void)attr;
    thr_t *t = enter();
    int r = EPERM;
    if (t != NULL && start != NULL && !t->p->ending) {
        moy_os_proc_t *p = t->p;
        lock();
        // A finished detached thread's slot is reused.
        for (uint32_t i = 1; i < MOY_OS_THREADS; i++) {
            if (p->thr[i].state == T_DONE && p->thr[i].detached && !p->thr[i].joined) {
                uint16_t gen = p->thr[i].gen;
                thr_reap(&p->thr[i]);
                p->thr[i].gen = gen;
            }
        }
        thr_t *n = NULL;
        for (uint32_t i = 1; n == NULL && i < MOY_OS_THREADS; i++) {
            if (p->thr[i].state == T_FREE) {
                n = &p->thr[i];
            }
        }
        r = EAGAIN;
        if (n != NULL) {
            uint16_t gen = (uint16_t)(n->gen + 1u);
            memset(n, 0, sizeof *n);
            n->gen = gen ? gen : 1;
            n->p = p;
            n->start = start;
            n->arg = arg;
            n->state = T_NEW;           // no other create takes the slot while it spawns
            unlock();
            r = thr_spawn(n);
            lock();
            if (r == 0 && thread != NULL) {
                *thread = (pthread_t)thr_id(p, n);
            }
        }
        unlock();
    }
    leave(t);
    return r;
}

// A join claims its thread under the lock: no second join, detach or reap
// can take the record it waits on.
int moy_os_pthread_join(pthread_t thread, void **out) {
    thr_t *t = enter();
    int r = ESRCH;
    bool mine = false;
    thr_t *o = NULL;
    if (t != NULL) {
        lock();
        o = thr_of(t->p, (uint32_t)thread);
        if (o == t && o != NULL) {
            r = EDEADLK;
        } else if (o != NULL && (o == &t->p->thr[0] || o->detached || o->joined)) {
            r = EINVAL;
        } else if (o != NULL) {
            o->joined = 1;
            mine = true;
        }
        unlock();
    }
    if (mine) {
        while (o->state != T_DONE) {
            if (ending(t)) {
                break;
            }
            sem_take(&o->done, MOY_OS_SLICE_US);
        }
        if (o->state == T_DONE) {
            if (out != NULL) {
                *out = o->out;
            }
            lock();
            uint16_t gen = o->gen;
            thr_reap(o);
            o->gen = gen;
            unlock();
            r = 0;
        } else {
            r = EINTR;
        }
    }
    leave(t);
    return r;
}

int moy_os_pthread_detach(pthread_t thread) {
    thr_t *t = enter();
    int r = ESRCH;
    if (t != NULL) {
        lock();
        thr_t *o = thr_of(t->p, (uint32_t)thread);
        if (o != NULL && (o->joined || o->detached)) {
            r = EINVAL;
        } else if (o != NULL) {
            o->detached = 1;
            r = 0;
        }
        unlock();
    }
    leave(t);
    return r;
}

pthread_t moy_os_pthread_self(void) {
    thr_t *t = self_thr();
    return t != NULL ? (pthread_t)thr_id(t->p, t) : (pthread_t)0;
}

void moy_os_pthread_exit(void *out) {
    thr_t *t = enter();
    if (t == NULL) {
        abort();
    }
    if (t == &t->p->thr[0]) {
        moy_os__exit(0);
    }
    t->out = out;
    thr_end(t);
}

// A mutex the module holds is its handle's id in the type's first word, made
// at its first use when the module used the static initializer.
static uint32_t *mutex_word(pthread_mutex_t *m) {
    return (uint32_t *)(void *)m;
}

// The mutex `m` names, held for the row (mutex_let) so no destroy frees it
// under a wait: its slot in *k.
static sem_t_ *mutex_of(thr_t *t, pthread_mutex_t *m, bool make, int *k) {
    *k = -1;
    if (t == NULL || m == NULL) {
        return NULL;
    }
    lock();
    int h = handle_of(t->p, *mutex_word(m), H_MUTEX);
    if (h < 0 && make) {
        sem_t_ *s = os_alloc(sizeof *s);
        if (s != NULL) {
            sem_init(s, true);
            h = handle_new(t->p, H_MUTEX, s, -1);
            if (h < 0) {
                sem_fini(s);
                os_free(s);
            } else {
                *mutex_word(m) = handle_id(t->p, h);
            }
        }
    }
    sem_t_ *out = NULL;
    if (h >= 0) {
        out = t->p->h[h].ptr;
        t->p->h[h].users++;
        *k = h;
    }
    unlock();
    return out;
}

static void mutex_let(thr_t *t, int k) {
    if (k >= 0) {
        lock();
        t->p->h[k].users--;
        unlock();
    }
}

int moy_os_pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *attr) {
    (void)attr;
    thr_t *t = enter();
    if (m != NULL) {
        memset(m, 0, sizeof *m);
    }
    int k;
    sem_t_ *s = mutex_of(t, m, true, &k);
    mutex_let(t, k);
    leave(t);
    return s != NULL ? 0 : EAGAIN;
}

int moy_os_pthread_mutex_destroy(pthread_mutex_t *m) {
    thr_t *t = enter();
    int r = EINVAL;
    if (t != NULL && m != NULL) {
        lock();
        int h = handle_of(t->p, *mutex_word(m), H_MUTEX);
        if (h >= 0 && t->p->h[h].users != 0) {
            r = EBUSY;
        } else if (h >= 0) {
            handle_drop(t->p, (uint32_t)h);
            *mutex_word(m) = 0;
            r = 0;
        }
        unlock();
    }
    leave(t);
    return r;
}

int moy_os_pthread_mutex_lock(pthread_mutex_t *m) {
    thr_t *t = enter();
    int k;
    sem_t_ *s = mutex_of(t, m, true, &k);
    int r = s != NULL ? 0 : EINVAL;
    while (s != NULL && !sem_take(s, MOY_OS_SLICE_US)) {
        if (ending(t)) {
            r = EINTR;
            break;
        }
    }
    mutex_let(t, k);
    leave(t);
    return r;
}

int moy_os_pthread_mutex_trylock(pthread_mutex_t *m) {
    thr_t *t = enter();
    int k;
    sem_t_ *s = mutex_of(t, m, true, &k);
    int r = s == NULL ? EINVAL : sem_take(s, 0) ? 0 : EBUSY;
    mutex_let(t, k);
    leave(t);
    return r;
}

int moy_os_pthread_mutex_unlock(pthread_mutex_t *m) {
    thr_t *t = enter();
    int k;
    sem_t_ *s = mutex_of(t, m, false, &k);
    if (s != NULL) {
        sem_give(s);
    }
    mutex_let(t, k);
    leave(t);
    return s != NULL ? 0 : EINVAL;
}

// -- the rows: Moybyte's -------------------------------------------------------------

int moy_os_moy_log(int level, const char *text) {
    thr_t *t = enter();
    if (text != NULL) {
        size_t n = text_len(t, text, MOY_LOG_TEXT_MAX);
        moy_log_write(level < 0 || level >= MOY_LOG_LEVELS ? MOY_LOG_INFO : level,
                      t != NULL ? t->p->pid : MOY_LOG_KERNEL, text,
                      n < MOY_LOG_TEXT_MAX ? n : MOY_LOG_TEXT_MAX);
    }
    leave(t);
    return text != NULL ? 0 : -1;
}

int moy_os_moy_term_size(int *cols, int *rows) {
    thr_t *t = enter();
    fcall_t c = { .op = F_TERM };
    long r = fcall(t, &c);
    if (r >= 0) {
        if (cols != NULL) {
            *cols = (int)(r >> 16);
        }
        if (rows != NULL) {
            *rows = (int)(r & 0xffff);
        }
    }
    leave(t);
    return r >= 0 ? 0 : -1;
}

// -- the rows: a game's screen -------------------------------------------------------
//
// A game's frame (moy_os_game): moy_present copies the module's indices into
// the process's frame while it is FREE, making it READY; the console's frame
// call takes a READY frame (TAKEN) and lets it go (FREE) at its next call,
// once it has drawn it. A present while the frame is not FREE waits for it.

int moy_os_moy_screen(int *w, int *h) {
    thr_t *t = enter();
    int r = -1;
    if (t == NULL || !t->p->game) {
        moy_os_errno_set(ENODEV);
    } else {
        if (w != NULL) {
            *w = t->p->fb_w;
        }
        if (h != NULL) {
            *h = t->p->fb_h;
        }
        r = 0;
    }
    leave(t);
    return r;
}

int moy_os_moy_present(const void *px, size_t n) {
    thr_t *t = enter();
    if (t == NULL || !t->p->game) {
        moy_os_errno_set(ENODEV);
        leave(t);
        return -1;
    }
    moy_os_proc_t *p = t->p;
    if (px == NULL || n != (size_t)p->fb_w * (size_t)p->fb_h) {
        moy_os_errno_set(EINVAL);
        leave(t);
        return -1;
    }
    for (;;) {
        lock();
        bool free_ = p->fb_state == FB_FREE;
        if (free_) {
            p->fb_state = FB_WRITING;
        }
        unlock();
        if (free_) {
            break;
        }
        if (ending(t)) {
            moy_os_errno_set(EINTR);
            leave(t);
            return -1;
        }
        sem_take(&p->fb_room, MOY_OS_SLICE_US);
    }
    memcpy(p->fb, px, n);
    lock();
    p->fb_state = FB_READY;
    unlock();
    sem_give(&p->fb_shown);
    leave(t);
    return 0;
}

// -- the rows: the event descriptor ------------------------------------------------
//
// The process's event queue (moy_evq.h), read as descriptor MOY_EVQ_FD: by
// read (evq_read), poll and select (poll_in), on the calling thread.

// A record of `p`'s queue into buf: its bytes, 0 once its stop hung the
// descriptor up, or -1 with errno.
static long evq_read(moy_os_proc_t *p, void *buf, size_t n) {
    if (p->hup) {
        return 0;
    }
    size_t len = 0;
    switch (moy_evq_read(p->pid, buf, n, &len)) {
    case MOY_EVQ_OK:
        p->events++;
        return (long)len;
    case MOY_EVQ_EMPTY:
        moy_os_errno_set(EAGAIN);
        return -1;
    case MOY_EVQ_RANGE:
        moy_os_errno_set(EMSGSIZE);
        return -1;
    default:
        moy_os_errno_set(EBADF);
        return -1;
    }
}

static int evq_errno(int rc) {
    switch (rc) {
    case MOY_EVQ_STALE: return EBADF;
    case MOY_EVQ_FULL: return EMFILE;
    case MOY_EVQ_NOMEM: return ENOMEM;
    default: return EINVAL;
    }
}

// A send no listener took for BACKPRESSURE (moy_evq.h) is EAGAIN: try later.
int moy_os_moy_send(const char *app, const char *name, const char *args) {
    thr_t *t = enter();
    int r = -1;
    // The names are copied and the arguments measured inside their region:
    // the module's other threads may rewrite them meanwhile.
    char app_in[MOY_EVQ_APP], name_in[MOY_EVQ_NAME];
    size_t an = app != NULL ? text_len(t, app, MOY_EVQ_APP) : MOY_EVQ_APP;
    size_t nn = name != NULL ? text_len(t, name, MOY_EVQ_NAME) : MOY_EVQ_NAME;
    if (an < MOY_EVQ_APP) {
        memcpy(app_in, app, an);
        app_in[an] = 0;
    }
    if (nn < MOY_EVQ_NAME) {
        memcpy(name_in, name, nn);
        name_in[nn] = 0;
    }
    size_t room = args != NULL ? text_len(t, args, SIZE_MAX) : 0;
    const char *a = room != 0 && room != SIZE_MAX ? args : "[]";
    if (room == SIZE_MAX) {
        an = MOY_EVQ_APP;           // no end inside its memory: refused below
        room = 0;
    }
    while (room > 1 && (*a == ' ' || *a == '\t' || *a == '\n')) {
        a++;
        room--;
    }
    if (room == 0) {
        room = 2;
    }
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (an >= MOY_EVQ_APP || nn == 0 || nn >= MOY_EVQ_NAME || *a != '[') {
        moy_os_errno_set(EINVAL);
    } else {
        r = moy_evq_send(t->p->pid, app_in, name_in, a, room, NULL);
        if (r < 0) {
            moy_os_errno_set(-r == MOY_EVQ_FULL ? EAGAIN : evq_errno(-r));
            r = -1;
        }
    }
    leave(t);
    return r;
}

int moy_os_moy_on(const char *name, int on) {
    thr_t *t = enter();
    int r = -1;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (name == NULL || text_len(t, name, MOY_EVQ_NAME) - 1u >= MOY_EVQ_NAME - 1u) {
        moy_os_errno_set(EINVAL);
    } else {
        char name_in[MOY_EVQ_NAME];
        size_t n = text_len(t, name, MOY_EVQ_NAME);
        n = n < MOY_EVQ_NAME ? n : MOY_EVQ_NAME - 1u;
        memcpy(name_in, name, n);
        name_in[n] = 0;
        int rc = moy_evq_listen(t->p->pid, name_in, on != 0);
        if (rc == MOY_EVQ_OK) {
            r = 0;
        } else {
            moy_os_errno_set(evq_errno(rc));
        }
    }
    leave(t);
    return r;
}

// -- the rows: memory -----------------------------------------------------------------

#ifdef MOY_OS_UMODE
// A mapping of a U-mode process: zeroed, 128-aligned, a window of its own.
static void *u_map(moy_os_proc_t *p, size_t len) {
    if (len > 0x7fffff80u) {
        return NULL;
    }
    uint32_t n = MOY_U_UP(len);
    map_t *m = os_alloc(sizeof *m);
    void *mem = heap_caps_aligned_alloc(MOY_U_ALIGN, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (m == NULL || mem == NULL
        || moy_u_add(p->u, (uint32_t)mem, (uint32_t)mem + n, MOY_U_R | MOY_U_W) != 0) {
        os_free(m);
        heap_caps_free(mem);
        return NULL;
    }
    memset(mem, 0, n);
    m->len = len;
    m->mem = mem;
    lock();
    m->next = p->maps;
    p->maps = m;
    p->mapped += len;
    unlock();
    return mem;
}

// Mapping `addr` let go; `locked` when the caller holds the table's lock.
static bool u_unmap(moy_os_proc_t *p, void *addr, bool locked) {
    map_t *m = NULL;
    if (!locked) {
        lock();
    }
    for (map_t **at = &p->maps; *at != NULL; at = &(*at)->next) {
        if ((*at)->mem == addr) {
            m = *at;
            *at = m->next;
            p->mapped -= m->len;
            break;
        }
    }
    if (!locked) {
        unlock();
    }
    if (m == NULL) {
        return false;
    }
    moy_u_drop(p->u, (uint32_t)m->mem);
    u_free(p, m->mem);
    os_free(m);
    return true;
}
#endif

void *moy_os_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off) {
    (void)addr;
    (void)prot;
    (void)off;
    thr_t *t = enter();
    void *out = MAP_FAILED;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (len == 0 || fd != -1 || !(flags & MAP_ANONYMOUS) || (flags & MAP_SHARED)) {
        moy_os_errno_set(len == 0 ? EINVAL : ENODEV);
#ifdef MOY_OS_UMODE
    } else if (t->p->u != NULL) {
        out = u_map(t->p, len);
        if (out == NULL) {
            out = MAP_FAILED;
            moy_os_errno_set(ENOMEM);
        }
#endif
    } else {
        map_t *m = len < (size_t)-1 - MAP_HDR ? os_alloc(MAP_HDR + len) : NULL;
        if (m == NULL) {
            moy_os_errno_set(ENOMEM);
        } else {
            m->len = len;
            lock();
            m->next = t->p->maps;
            t->p->maps = m;
            t->p->mapped += len;
            unlock();
            out = (char *)m + MAP_HDR;
        }
    }
    leave(t);
    return out;
}

int moy_os_munmap(void *addr, size_t len) {
    (void)len;
    thr_t *t = enter();
    map_t *m = NULL;
#ifdef MOY_OS_UMODE
    if (t != NULL && t->p->u != NULL) {
        int r = u_unmap(t->p, addr, false) ? 0 : -1;
        if (r != 0) {
            moy_os_errno_set(EINVAL);
        }
        leave(t);
        return r;
    }
#endif
    if (t != NULL) {
        lock();
        for (map_t **at = &t->p->maps; *at != NULL; at = &(*at)->next) {
            if ((char *)*at + MAP_HDR == (char *)addr) {
                m = *at;
                *at = m->next;
                t->p->mapped -= m->len;
                break;
            }
        }
        unlock();
    }
    if (m != NULL) {
        os_free(m);
    } else {
        moy_os_errno_set(EINVAL);
    }
    leave(t);
    return m != NULL ? 0 : -1;
}

// -- the rows: sockets ----------------------------------------------------------------

static int sock_slot(moy_os_proc_t *p, int fd) {
    int k = fd - MOY_OS_SOCK_FD;
    if (k < 0 || k >= (int)MOY_OS_HANDLES || p->h[k].kind != H_SOCK || p->h[k].closing) {
        return -1;
    }
    return k;
}

static void net_body(void *arg) {
    moy_os_proc_t *p = arg;
    if (!p->net_held) {
        if (g_net != NULL) {
            g_net(g_net_ctx, p->pid, 1);
        }
        p->net_held = 1;
    }
}

// The process may use the network, and holds it: 0, or an errno value.
static int net_need(thr_t *t) {
    if (t == NULL) {
        return EPERM;
    }
    moy_os_proc_t *p = t->p;
    if (!(p->flags & MOY_OS_NET) || p->s == NULL) {
        return EACCES;
    }
    if (!p->net_held && on_session(t, net_body, p) != 0) {
        return EINTR;
    }
    return 0;
}

// The network's link up, waited for MOY_OS_LINK_US at most: 0, or an errno.
static int link_wait(thr_t *t) {
#ifdef MOY_OS_BOARD
    if (moy_net_link == NULL) {
        return 0;
    }
    int64_t end = mono_us() + MOY_OS_LINK_US;
    uint32_t ip;
    while (!moy_net_link(&ip)) {
        if (mono_us() >= end) {
            return ENETUNREACH;
        }
        if (doze(t, MOY_OS_SLICE_US) != 0) {
            return EINTR;
        }
    }
#else
    (void)t;
#endif
    return 0;
}

// For each of `n` stack sockets lfd[i], what of POLLIN and POLLOUT in want[i]
// is ready, and POLLERR, after waiting `us` at most for any: how many are.
static int sk_ready(const int *lfd, const short *want, short *got, int n, int64_t us) {
#ifdef MOY_OS_BOARD
    fd_set rs, ws, es;
    FD_ZERO(&rs);
    FD_ZERO(&ws);
    FD_ZERO(&es);
    int top = -1;
    for (int i = 0; i < n; i++) {
        if (want[i] & POLLIN) {
            FD_SET(lfd[i], &rs);
        }
        if (want[i] & POLLOUT) {
            FD_SET(lfd[i], &ws);
        }
        FD_SET(lfd[i], &es);
        top = lfd[i] > top ? lfd[i] : top;
    }
    struct timeval tv = { (time_t)(us / 1000000), (suseconds_t)(us % 1000000) };
    int r = lwip_select(top + 1, &rs, &ws, &es, &tv);
    int ready = 0;
    for (int i = 0; i < n; i++) {
        got[i] = 0;
        if (r > 0) {
            got[i] = (short)((FD_ISSET(lfd[i], &rs) ? POLLIN : 0)
                             | (FD_ISSET(lfd[i], &ws) ? POLLOUT : 0)
                             | (FD_ISSET(lfd[i], &es) ? POLLERR : 0));
        }
        ready += got[i] != 0;
    }
    return ready;
#else
    struct pollfd fds[MOY_OS_HANDLES];
    for (int i = 0; i < n; i++) {
        fds[i].fd = lfd[i];
        fds[i].events = want[i];
        fds[i].revents = 0;
    }
    int r = poll(fds, (nfds_t)n, (int)((us + 999) / 1000));
    int ready = 0;
    for (int i = 0; i < n; i++) {
        got[i] = r > 0 ? fds[i].revents : 0;
        ready += got[i] != 0;
    }
    return ready;
#endif
}

// Wait for socket `slot` to be readable (or writable): 0, EAGAIN at `ms`
// (0: no limit), EINTR when the process ends.
static int sk_block(thr_t *t, const handle_t *h, bool wr, uint32_t ms) {
    int64_t end = ms ? mono_us() + (int64_t)ms * 1000 : 0;
    int lfd = h->fd;
    for (;;) {
        if (ending(t)) {
            return EINTR;
        }
        if (h->closing) {
            return EBADF;
        }
        int64_t slice = MOY_OS_SLICE_US;
        if (end) {
            int64_t left = end - mono_us();
            if (left <= 0) {
                return EAGAIN;
            }
            slice = left < slice ? left : slice;
        }
        short want = wr ? POLLOUT : POLLIN, got = 0;
        if (sk_ready(&lfd, &want, &got, 1, slice) > 0) {
            return 0;
        }
    }
}

static bool would_block(int e) {
    return e == EAGAIN || e == EWOULDBLOCK || e == EINPROGRESS;
}

// A stack socket made non-blocking and given a handle: its descriptor, or -1.
static int sock_adopt(thr_t *t, int lfd, uint32_t flags) {
#ifdef MOY_OS_BOARD
    lwip_fcntl(lfd, F_SETFL, O_NONBLOCK);
#else
    fcntl(lfd, F_SETFL, fcntl(lfd, F_GETFL, 0) | O_NONBLOCK);
#endif
    lock();
    int h = handle_new(t->p, H_SOCK, NULL, lfd);
    if (h >= 0) {
        t->p->h[h].kinds = flags;
        t->p->h[h].rcv_ms = t->p->h[h].snd_ms = 0;
    }
    unlock();
    if (h < 0) {
        SK(close)(lfd);
        moy_os_errno_set(EMFILE);
        return -1;
    }
    return MOY_OS_SOCK_FD + h;
}

// recv or send on `slot`, as its blocking mode and `flags` say.
static long sock_io(thr_t *t, int slot, void *buf, size_t n, int flags, bool wr) {
    handle_t *h = &t->p->h[slot];
    bool block = !(h->kinds & SK_NONBLOCK) && !(flags & MSG_DONTWAIT);
    for (;;) {
        long r = wr ? (long)SK(send)(h->fd, buf, n, flags | MSG_DONTWAIT)
                    : (long)SK(recv)(h->fd, buf, n, flags | MSG_DONTWAIT);
        if (r >= 0) {
            return r;
        }
        int e = errno;
        if (!block || !would_block(e)) {
            moy_os_errno_set(e);
            return -1;
        }
        if ((e = sk_block(t, h, wr, wr ? h->snd_ms : h->rcv_ms)) != 0) {
            moy_os_errno_set(e);
            return -1;
        }
    }
}

// The socket row's prologue: the slot of `fd` in the calling thread's
// process, held for the row (sock_let) so a close by another thread waits for
// it to finish; or -1 with errno set.
static int sock_of(thr_t *t, int fd) {
    int k = -1;
    if (t != NULL) {
        lock();
        k = sock_slot(t->p, fd);
        if (k >= 0) {
            t->p->h[k].users++;
        }
        unlock();
    }
    if (k < 0) {
        moy_os_errno_set(t == NULL ? EPERM : EBADF);
    }
    return k;
}

// A socket row's hold let go: a socket closed meanwhile is closed now, by
// the last row that held it.
static void sock_let(thr_t *t, int k) {
    if (k < 0) {
        return;
    }
    lock();
    handle_t *h = &t->p->h[k];
    if (--h->users == 0 && h->closing) {
        handle_drop(t->p, (uint32_t)k);
    }
    unlock();
}

// `fd`'s close: at once, or, while a row of another thread uses it, shut down
// (which ends that row's wait) and closed by the row's end.
static void sock_close(moy_os_proc_t *p, int k) {
    handle_t *h = &p->h[k];
    if (h->users == 0) {
        handle_drop(p, (uint32_t)k);
    } else {
        h->closing = 1;
        SK(shutdown)(h->fd, SHUT_RDWR);
    }
}

int moy_os_socket(int domain, int type, int protocol) {
    thr_t *t = enter();
    int r = -1, e = net_need(t);
    uint32_t flags = 0;
#ifdef SOCK_NONBLOCK
    if (type & SOCK_NONBLOCK) {
        flags |= SK_NONBLOCK;
    }
    type &= ~(SOCK_NONBLOCK | SOCK_CLOEXEC);
#endif
    if (e != 0) {
        moy_os_errno_set(e);
    } else {
        int lfd = SK(socket)(domain, type, protocol);
        if (lfd < 0) {
            moy_os_errno_set(errno);
        } else {
            r = sock_adopt(t, lfd, flags);
        }
    }
    leave(t);
    return r;
}

// A call on the stack's socket that answers at once: its answer, errno set.
#define SOCK_CALL(fd, call)                         \
    thr_t *t = enter();                             \
    int k = sock_of(t, fd), r = -1;                 \
    if (k >= 0) {                                   \
        int lfd = t->p->h[k].fd;                    \
        (void)lfd;                                  \
        r = (call);                                 \
        if (r < 0) {                                \
            moy_os_errno_set(errno);                \
        }                                           \
    }                                               \
    sock_let(t, k);                                 \
    leave(t);                                       \
    return r

int moy_os_bind(int fd, const struct sockaddr *addr, socklen_t len) {
    SOCK_CALL(fd, SK(bind)(lfd, addr, len));
}

int moy_os_listen(int fd, int backlog) {
    SOCK_CALL(fd, SK(listen)(lfd, backlog));
}

int moy_os_shutdown(int fd, int how) {
    SOCK_CALL(fd, SK(shutdown)(lfd, how));
}

// A length a row reads through a pointer, read ONCE: the row's check passed
// the buffer by it, and another thread may change it after. The buffer is
// checked again by the value read (u_fits), and the stack is handed a copy.
static socklen_t len_in(const socklen_t *len) {
    return len != NULL ? *(const volatile socklen_t *)len : 0;
}

// [p, p+n) writable by the calling thread's process (always, off U-mode).
static bool u_fits(thr_t *t, void *ptr, size_t n) {
#ifdef MOY_OS_UMODE
    if (t != NULL && t->u != NULL && ptr != NULL) {
        return moy_u_ok(t->u, ptr, n, MOY_U_W) != 0;
    }
#endif
    (void)t;
    (void)ptr;
    (void)n;
    return true;
}

// getsockname, getpeername and getsockopt over a copy of the length.
static int sock_out(int fd, int op, int level, int name, void *out, socklen_t *len) {
    thr_t *t = enter();
    int k = sock_of(t, fd), r = -1;
    if (k >= 0) {
        socklen_t n = len_in(len);
        if (len == NULL || out == NULL || !u_fits(t, out, n)) {
            errno = EFAULT;
        } else {
            int lfd = t->p->h[k].fd;
            r = op == 0 ? SK(getsockname)(lfd, out, &n)
                : op == 1 ? SK(getpeername)(lfd, out, &n)
                : SK(getsockopt)(lfd, level, name, out, &n);
            if (r == 0) {
                *len = n;
            }
        }
        if (r < 0) {
            moy_os_errno_set(errno);
        }
    }
    sock_let(t, k);
    leave(t);
    return r;
}

int moy_os_getsockname(int fd, struct sockaddr *addr, socklen_t *len) {
    return sock_out(fd, 0, 0, 0, addr, len);
}

int moy_os_getpeername(int fd, struct sockaddr *addr, socklen_t *len) {
    return sock_out(fd, 1, 0, 0, addr, len);
}

int moy_os_getsockopt(int fd, int level, int name, void *val, socklen_t *len) {
    return sock_out(fd, 2, level, name, val, len);
}

int moy_os_setsockopt(int fd, int level, int name, const void *val, socklen_t len) {
    thr_t *t = enter();
    int k = sock_of(t, fd), r = -1;
    if (k >= 0) {
        if (level == SOL_SOCKET && (name == SO_RCVTIMEO || name == SO_SNDTIMEO)) {
            // The OS's wait keeps them: underneath, the socket never blocks.
            if (val == NULL || len < (socklen_t)sizeof(struct timeval)) {
                moy_os_errno_set(EINVAL);
            } else {
                const struct timeval *tv = val;
                uint32_t ms = (uint32_t)(tv->tv_sec * 1000 + (tv->tv_usec + 999) / 1000);
                *(name == SO_RCVTIMEO ? &t->p->h[k].rcv_ms : &t->p->h[k].snd_ms) = ms;
                r = 0;
            }
        } else {
            r = SK(setsockopt)(t->p->h[k].fd, level, name, val, len);
            if (r < 0) {
                moy_os_errno_set(errno);
            }
        }
    }
    sock_let(t, k);
    leave(t);
    return r;
}

int moy_os_accept(int fd, struct sockaddr *addr, socklen_t *len) {
    thr_t *t = enter();
    int k = sock_of(t, fd), r = -1;
    socklen_t n = len_in(len);
    bool named = addr != NULL && len != NULL;
    if (k >= 0 && named && !u_fits(t, addr, n)) {
        moy_os_errno_set(EFAULT);
        sock_let(t, k);
        k = -1;
    }
    int held = k;
    while (k >= 0) {
        handle_t *h = &t->p->h[k];
        int lfd = SK(accept)(h->fd, named ? addr : NULL, named ? &n : NULL);
        if (lfd >= 0) {
            if (named) {
                *len = n;
            }
            r = sock_adopt(t, lfd, 0);
            break;
        }
        int e = errno;
        if ((h->kinds & SK_NONBLOCK) || !would_block(e)
            || (e = sk_block(t, h, false, h->rcv_ms)) != 0) {
            moy_os_errno_set(e);
            break;
        }
    }
    sock_let(t, held);
    leave(t);
    return r;
}

int moy_os_connect(int fd, const struct sockaddr *addr, socklen_t len) {
    thr_t *t = enter();
    int k = sock_of(t, fd), r = -1, e;
    if (k >= 0 && (e = link_wait(t)) != 0) {
        moy_os_errno_set(e);
    } else if (k >= 0) {
        handle_t *h = &t->p->h[k];
        r = SK(connect)(h->fd, addr, len);
        e = r < 0 ? errno : 0;
        if (r < 0 && would_block(e) && !(h->kinds & SK_NONBLOCK)) {
            e = sk_block(t, h, true, h->snd_ms);
            if (e == 0) {
                int err = 0;
                socklen_t n = sizeof err;
                SK(getsockopt)(h->fd, SOL_SOCKET, SO_ERROR, &err, &n);
                e = err;
            } else if (e == EAGAIN) {
                e = ETIMEDOUT;
            }
            r = e == 0 ? 0 : -1;
        } else if (r < 0 && e == EAGAIN) {
            e = EINPROGRESS;
        }
        if (r < 0) {
            moy_os_errno_set(e);
        }
    }
    sock_let(t, k);
    leave(t);
    return r;
}

ssize_t moy_os_send(int fd, const void *buf, size_t n, int flags) {
    thr_t *t = enter();
    int k = sock_of(t, fd);
    long r = k >= 0 ? sock_io(t, k, (void *)buf, n, flags, true) : -1;
    sock_let(t, k);
    leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_recv(int fd, void *buf, size_t n, int flags) {
    thr_t *t = enter();
    int k = sock_of(t, fd);
    long r = k >= 0 ? sock_io(t, k, buf, n, flags, false) : -1;
    sock_let(t, k);
    leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_sendto(int fd, const void *buf, size_t n, int flags, const struct sockaddr *to,
                      socklen_t len) {
    thr_t *t = enter();
    int k = sock_of(t, fd), e;
    long r = -1;
    if (k >= 0 && to != NULL && (e = link_wait(t)) != 0) {
        moy_os_errno_set(e);
    } else if (k >= 0) {
        handle_t *h = &t->p->h[k];
        bool block = !(h->kinds & SK_NONBLOCK) && !(flags & MSG_DONTWAIT);
        for (;;) {
            r = (long)SK(sendto)(h->fd, buf, n, flags | MSG_DONTWAIT, to, len);
            if (r >= 0) {
                break;
            }
            e = errno;
            if (!block || !would_block(e) || (e = sk_block(t, h, true, h->snd_ms)) != 0) {
                moy_os_errno_set(e);
                break;
            }
        }
    }
    sock_let(t, k);
    leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_recvfrom(int fd, void *buf, size_t n, int flags, struct sockaddr *from,
                        socklen_t *len) {
    thr_t *t = enter();
    int k = sock_of(t, fd), e;
    long r = -1;
    socklen_t fl = len_in(len);
    bool named = from != NULL && len != NULL;
    if (k >= 0 && named && !u_fits(t, from, fl)) {
        moy_os_errno_set(EFAULT);
    } else if (k >= 0) {
        handle_t *h = &t->p->h[k];
        bool block = !(h->kinds & SK_NONBLOCK) && !(flags & MSG_DONTWAIT);
        for (;;) {
            r = (long)SK(recvfrom)(h->fd, buf, n, flags | MSG_DONTWAIT, named ? from : NULL,
                                   named ? &fl : NULL);
            if (r >= 0) {
                if (named) {
                    *len = fl;
                }
                break;
            }
            e = errno;
            if (!block || !would_block(e) || (e = sk_block(t, h, false, h->rcv_ms)) != 0) {
                moy_os_errno_set(e);
                break;
            }
        }
    }
    sock_let(t, k);
    leave(t);
    return (ssize_t)r;
}

#ifdef MOY_OS_UMODE
// The stack's answer copied into a mapping of the U-mode process's own.
static struct addrinfo *u_addrinfo(moy_os_proc_t *p, const struct addrinfo *ai) {
    size_t n = 0;
    for (const struct addrinfo *a = ai; a != NULL; a = a->ai_next) {
        n += sizeof *a + ((a->ai_addrlen + 3u) & ~3u)
             + (a->ai_canonname != NULL ? strlen(a->ai_canonname) + 1 : 0);
    }
    uint8_t *at = n != 0 ? u_map(p, n) : NULL;
    struct addrinfo *out = (struct addrinfo *)at, *prev = NULL;
    for (const struct addrinfo *a = ai; at != NULL && a != NULL; a = a->ai_next) {
        struct addrinfo *c = (struct addrinfo *)at;
        *c = *a;
        c->ai_next = NULL;
        at += sizeof *c;
        if (a->ai_addr != NULL) {
            memcpy(at, a->ai_addr, a->ai_addrlen);
            c->ai_addr = (struct sockaddr *)at;
            at += (a->ai_addrlen + 3u) & ~3u;
        }
        if (a->ai_canonname != NULL) {
            strcpy((char *)at, a->ai_canonname);
            c->ai_canonname = (char *)at;
            at += strlen(a->ai_canonname) + 1;
        }
        if (prev != NULL) {
            prev->ai_next = c;
        }
        prev = c;
    }
    return out;
}
#endif

int moy_os_getaddrinfo(const char *node, const char *service, const struct addrinfo *hints,
                       struct addrinfo **res) {
    thr_t *t = enter();
    int r = EAI_FAIL, e = net_need(t);
    bool numeric = node == NULL || (hints != NULL && (hints->ai_flags & AI_NUMERICHOST));
    if (e == 0 && !numeric) {
        e = link_wait(t);
    }
    if (e != 0) {
        moy_os_errno_set(e);
    } else {
        // The answer stays in the kernel until it is the process's own: *res
        // is the module's memory, which another of its threads may write.
        struct addrinfo *got = NULL;
        bool own = true;
        char node_in[256], service_in[32];
        size_t nn = node != NULL ? text_len(t, node, sizeof node_in) : 0;
        size_t sn = service != NULL ? text_len(t, service, sizeof service_in) : 0;
        if (nn >= sizeof node_in || sn >= sizeof service_in) {
            r = EAI_NONAME;
        } else {
            if (node != NULL) {
                memcpy(node_in, node, nn);
                node_in[nn] = 0;
            }
            if (service != NULL) {
                memcpy(service_in, service, sn);
                service_in[sn] = 0;
            }
            r = SK(getaddrinfo)(node != NULL ? node_in : NULL, service != NULL ? service_in : NULL,
                                hints, &got);
        }
#ifdef MOY_OS_UMODE
        if (r == 0 && t->p->u != NULL) {
            struct addrinfo *u = u_addrinfo(t->p, got);
            SK(freeaddrinfo)(got);
            got = u;
            own = false;
            r = u != NULL ? 0 : EAI_MEMORY;
        }
#endif
        if (r == 0) {
            lock();
            int h = handle_new(t->p, H_ADDR, got, -1);
            unlock();
            if (h < 0) {
#ifdef MOY_OS_UMODE
                if (!own) {
                    u_unmap(t->p, got, false);
                } else
#endif
                SK(freeaddrinfo)(got);
                got = NULL;
                r = EAI_MEMORY;
            }
        }
        (void)own;
        *res = got;
    }
    leave(t);
    return r;
}

void moy_os_freeaddrinfo(struct addrinfo *ai) {
    thr_t *t = enter();
    if (t != NULL && ai != NULL) {
        lock();
        for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
            if (t->p->h[i].kind == H_ADDR && t->p->h[i].ptr == ai) {
                handle_drop(t->p, i);
                break;
            }
        }
        unlock();
    }
    leave(t);
}

// poll over any of the process's descriptors: a socket as the stack says, a
// file or stream of the session at once (its reads and writes never wait).
static int poll_in(thr_t *t, struct pollfd *fds, nfds_t n, int timeout_ms) {
    moy_os_proc_t *p = t->p;
    int64_t end = timeout_ms < 0 ? 0 : mono_us() + (int64_t)timeout_ms * 1000;
    int lfd[MOY_OS_HANDLES];
    short want[MOY_OS_HANDLES], got[MOY_OS_HANDLES];
    nfds_t at[MOY_OS_HANDLES];
    int held[MOY_OS_HANDLES];
    for (;;) {
        int ready = 0, m = 0;
        bool evq = false;
        for (nfds_t i = 0; i < n; i++) {
            struct pollfd *f = &fds[i];
            f->revents = 0;
            if (f->fd < 0) {
                continue;
            }
            int k = f->fd >= MOY_OS_SOCK_FD && m < (int)MOY_OS_HANDLES ? sock_of(t, f->fd) : -1;
            if (f->fd == MOY_EVQ_FD) {
                if (p->hup) {
                    f->revents = (short)(POLLHUP | (f->events & POLLIN));
                } else if (!moy_evq_has(p->pid)) {
                    f->revents = POLLNVAL;
                } else if (moy_evq_queued(p->pid) > 0) {
                    f->revents = (short)(f->events & POLLIN);
                }
                evq |= f->revents == 0;
            } else if (k >= 0) {
                held[m] = k;
                lfd[m] = p->h[k].fd;
                want[m] = (short)(f->events & (POLLIN | POLLOUT));
                at[m++] = i;
            } else if (f->fd >= MOY_OS_SOCK_FD && sock_slot(p, f->fd) >= 0) {
                f->revents = 0;         // past the sockets one poll takes: not looked at
            } else if ((f->fd >= 0 && f->fd <= 2) || fd_owned(p, f->fd) >= 0) {
                f->revents = (short)(f->events & (POLLIN | POLLOUT));
            } else {
                f->revents = POLLNVAL;
            }
            ready += f->revents != 0;
        }
        int64_t slice = 0;
        if (ready == 0) {
            slice = MOY_OS_SLICE_US;
            if (end) {
                int64_t left = end - mono_us();
                slice = left <= 0 ? 0 : left < slice ? left : slice;
            }
        }
        bool any = m > 0 && sk_ready(lfd, want, got, m, slice) > 0;
        for (int j = 0; j < m; j++) {
            if (any) {
                fds[at[j]].revents = (short)(got[j] & (fds[at[j]].events | POLLERR | POLLHUP));
                ready += fds[at[j]].revents != 0;
            }
            sock_let(t, held[j]);
        }
        if (m == 0 && slice > 0 && evq) {
            // The one wait: woken by an event, a stop or the slice's end.
            moy_evq_wait(p->pid, (uint32_t)((slice + 999) / 1000));
        } else if (m == 0 && slice > 0 && doze(t, slice) != 0) {
            return -EINTR;
        }
        if (ready > 0 || (end && mono_us() >= end) || timeout_ms == 0) {
            return ready;
        }
        if (ending(t)) {
            return -EINTR;
        }
    }
}

// A poll of `p`'s event descriptor: the wake that ends here charged to the
// supervisor (unless the watchdog struck it while it ran, moy_os_reap), and
// while the supervisor holds the process suspended, no wake served. The
// next wake is the queue's STEP (moy_evq.h, IN TURN).
static int wake_end(thr_t *t) {
    moy_os_proc_t *p = t->p;
    lock();
    p->polling = 1;
    int64_t now = mono_us();
    int64_t cost = p->woke_us != 0 ? now - p->woke_us : 0;
    bool struck = p->struck != 0;
    p->struck = 0;
    if (cost > 0) {
        p->cpu_us += (uint32_t)cost;
    }
    unlock();
    if (!moy_evq_has(p->pid)) {
        return 0;
    }
    if (p->woke_us != 0 && !struck) {
        moy_super_charge(p->pid, (uint32_t)(cost > 0 ? cost : 0), false);
    }
    while (moy_super_held(p->pid) && !p->hup) {
        if (doze(t, MOY_OS_SLICE_US) != 0) {
            return EINTR;
        }
    }
    moy_evq_step(p->pid);               // the next wake reads MOY_EVQ_BATCH at most
    return 0;
}

static bool polls_evq(const struct pollfd *fds, nfds_t n) {
    for (nfds_t i = 0; i < n; i++) {
        if (fds[i].fd == MOY_EVQ_FD) {
            return true;
        }
    }
    return false;
}

int moy_os_poll(struct pollfd *fds, nfds_t n, int timeout) {
    thr_t *t = enter();
    int r = -1;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else {
        bool evq = polls_evq(fds, n);
        r = evq ? -wake_end(t) : 0;
        if (r == 0) {
            r = poll_in(t, fds, n, timeout);
        }
        if (evq) {
            lock();
            t->p->wakes += r >= 0;
            t->p->woke_us = mono_us();
            t->p->polling = 0;
            unlock();
        }
        if (r < 0) {
            moy_os_errno_set(-r);
            r = -1;
        }
    }
    leave(t);
    return r;
}

int moy_os_select(int n, fd_set *rs, fd_set *ws, fd_set *es, struct timeval *tv) {
    thr_t *t = enter();
    int r = -1;
    struct pollfd fds[FD_SETSIZE];
    nfds_t m = 0;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (n < 0 || n > FD_SETSIZE) {
        moy_os_errno_set(EINVAL);
    } else {
        for (int fd = 0; fd < n; fd++) {
            short ev = (short)((rs != NULL && FD_ISSET(fd, rs) ? POLLIN : 0)
                               | (ws != NULL && FD_ISSET(fd, ws) ? POLLOUT : 0));
            if (ev || (es != NULL && FD_ISSET(fd, es))) {
                fds[m].fd = fd;
                fds[m].events = ev;
                m++;
            }
        }
        int ms = tv == NULL ? -1 : (int)(tv->tv_sec * 1000 + (tv->tv_usec + 999) / 1000);
        r = poll_in(t, fds, m, ms);
        if (r < 0) {
            moy_os_errno_set(-r);
            r = -1;
        } else {
            r = 0;
            for (nfds_t i = 0; i < m; i++) {
                int fd = fds[i].fd;
                short ev = fds[i].revents;
                if (ev & POLLNVAL) {
                    moy_os_errno_set(EBADF);
                    r = -1;
                    break;
                }
                if (rs != NULL && FD_ISSET(fd, rs) && !(ev & (POLLIN | POLLERR | POLLHUP))) {
                    FD_CLR(fd, rs);
                }
                if (ws != NULL && FD_ISSET(fd, ws) && !(ev & (POLLOUT | POLLERR))) {
                    FD_CLR(fd, ws);
                }
                if (es != NULL && FD_ISSET(fd, es)) {
                    FD_CLR(fd, es);
                }
                r += (rs != NULL && FD_ISSET(fd, rs)) + (ws != NULL && FD_ISSET(fd, ws));
            }
        }
    }
    leave(t);
    return r;
}

int moy_os_fcntl(int fd, int cmd, int arg) {
    thr_t *t = enter();
    int r = -1;
    int k = t != NULL ? sock_slot(t->p, fd) : -1;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (k < 0 && !(fd >= 0 && fd <= 2) && fd_owned(t->p, fd) < 0) {
        moy_os_errno_set(EBADF);
    } else if (cmd == F_GETFD || cmd == F_SETFD) {
        r = 0;
    } else if (cmd == F_GETFL) {
        r = O_RDWR | (k >= 0 && (t->p->h[k].kinds & SK_NONBLOCK) ? O_NONBLOCK : 0);
    } else if (cmd == F_SETFL) {
        if (k >= 0) {
            t->p->h[k].kinds = (arg & O_NONBLOCK) ? (t->p->h[k].kinds | SK_NONBLOCK)
                                                  : (t->p->h[k].kinds & ~SK_NONBLOCK);
        }
        r = 0;
    } else {
        moy_os_errno_set(EINVAL);
    }
    leave(t);
    return r;
}

void moy_os_set_net(moy_os_net_fn net, void *ctx) {
    g_net_ctx = ctx;
    g_net = net;
}

// -- processes --------------------------------------------------------------------------

// The S3s: core 1 only (tiers.md), at the VM's own priority there, so a
// module that never calls a row still shares the core with the task that
// stops it. The P4s: core 0 above the idle work, as a Lua service runs.
void moy_os_default_caps(moy_os_caps_t *caps) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    caps->core = 1;
    caps->prio = 1;
#elif defined(MOY_OS_BOARD)
    caps->core = 0;
    caps->prio = 2;
#else
    caps->core = -1;
    caps->prio = 0;
#endif
    caps->stack = 16 * 1024;
}

// A record proc_new made and nothing else holds.
static void proc_free(moy_os_proc_t *p) {
    sem_fini(&p->wake);
    sem_fini(&p->hop_done);
    sem_fini(&p->hop_lock);
    sem_fini(&p->stopped);
    sem_fini(&p->fb_room);
    sem_fini(&p->fb_shown);
    p->magic = 0;
    os_free(p);
}

#ifdef MOY_OS_UMODE
static volatile uint8_t g_never;

// The module's hooks (its entry's BIND), asked in U-mode on the calling task
// with a stack of its own: 0 when it faults.
static uint32_t u_bind(moy_os_proc_t *p) {
    uint32_t len = 1024;
    uint8_t *stack = heap_caps_aligned_alloc(MOY_U_ALIGN, len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (stack == NULL) {
        return 0;
    }
    memset(stack, 0, len);
    moy_u_space_t u = { .proc = p->u, .id = moy_u_new_id(), .slo = (uint32_t)stack,
                        .shi = (uint32_t)stack + len, .ending = &g_never };
    uint32_t a[5] = { p->mod.utable, MOY_ELF_OP_BIND };
    uint32_t hooks = (uint32_t)moy_u_run(&u, (uint32_t)p->mod.entry, p->mod.uret, u.shi, a);
    u_scrub_free(stack);
    if (u.fault != 0) {
        log_line(p, MOY_LOG_ERR, "the native module's entry faulted");
        return 0;
    }
    return hooks;
}
#endif

static moy_os_proc_t *proc_new(const moy_os_module_t *m, const moy_os_caps_t *caps, uint32_t pid,
                               bool service, char *err, size_t errlen) {
    lock();
    uint32_t slot = MOY_OS_PROCS;
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (g_proc[i] == NULL) {
            slot = i;
            break;
        }
    }
    unlock();
    if (slot == MOY_OS_PROCS) {
        snprintf(err, errlen, "too many native processes");
        return NULL;
    }
    moy_os_proc_t *p = os_alloc(sizeof *p);
    if (p == NULL) {
        snprintf(err, errlen, "out of memory");
        return NULL;
    }
    p->magic = MAGIC;
    p->pid = pid;
    p->mod = *m;
    if (caps != NULL) {
        p->caps = *caps;
    } else {
        moy_os_default_caps(&p->caps);
    }
    p->service = service;
    p->prog.roots = NULL;
    sem_init(&p->wake, false);
    sem_init(&p->hop_done, false);
    sem_init(&p->hop_lock, true);
    sem_init(&p->stopped, false);
    sem_init(&p->fb_room, false);
    sem_init(&p->fb_shown, false);
#ifdef MOY_OS_UMODE
    if (m->ulo != 0) {
        p->u = os_alloc(sizeof *p->u);
        if (p->u == NULL) {
            snprintf(err, errlen, "out of memory");
            proc_free(p);
            return NULL;
        }
        p->u->lo = m->ulo;
        p->u->rx = m->urx;
        p->u->hi = m->uhi;
        p->u->vbase = m->uvbase;
        p->hooks = u_bind(p);
    } else
#endif
    p->hooks = (uint32_t)p->mod.entry(&moy_os_table, MOY_ELF_OP_BIND, 0, NULL, NULL);
    lock();
    g_proc[slot] = p;
    unlock();
    g_stats.started++;
#ifdef MOY_OS_UMODE
#endif
    return p;
}

static int live_threads(const moy_os_proc_t *p) {
    int n = 0;
    for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
        n += p->thr[k].state == T_LIVE;
    }
    return n;
}

// Every thread of `p` ended (a board deletes one in the module's own code
// where it stands), every registration swept, the module unloaded and the
// record freed. False when a thread would not end (a host): the record is
// then left, `leaked`.
static bool proc_end(moy_os_proc_t *p) {
    lock();
    p->ending = 1;
    unlock();
    moy_evq_kick(p->pid);
    moy_evq_unwait(p->pid);             // a task deleted below may be its waiter
    sem_give(&p->hop_done);
    int64_t end = mono_us() + MOY_OS_REAP_US;
    while (live_threads(p) > 0) {
        bool inside = false;
        for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
            inside |= p->thr[k].state == T_LIVE && p->thr[k].in_row > 0;
        }
        if (!inside) {
#ifdef MOY_OS_BOARD
            // Every live thread is in the module's code: deleted there. Each
            // is held first and looked at again, held: one that has gone into
            // kernel code since is let go to leave at its row's end.
            lock();
            for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
                thr_t *t = &p->thr[k];
                if (t->state != T_LIVE || t->in_row != 0) {
                    continue;
                }
                vTaskSuspend(t->task);
                while (eTaskGetState(t->task) == eRunning) {
                    taskYIELD();
                }
                if (t->state != T_LIVE || t->in_row != 0) {
                    vTaskResume(t->task);
                    continue;
                }
                self_clear(t);
                vTaskDeleteWithCaps(t->task);
#ifdef MOY_OS_UMODE
                u_thread_end(t);
#endif
                sem_fini(&t->done);
                memset(t, 0, sizeof *t);
                g_stats.killed++;
            }
            unlock();
            continue;
#endif
        }
        if (mono_us() >= end) {
            g_stats.leaked++;
            log_line(p, MOY_LOG_ERR, "a native thread would not end; its process is left running");
            return false;
        }
        // A thread in a row leaves at its end; one serving the session gets its
        // answer refused, and a service's hop on the VM's queue is run (its
        // end is the VM's task's: moy_os_stop, moy_os_reap).
        if (p->hop_fn != NULL) {
            p->hop_fn = NULL;
            sem_give(&p->hop_done);
        }
        if (p->service && p->hopping) {
            moy_vmq_serve();
        }
        sem_take(&p->stopped, MOY_OS_SLICE_US);
    }
    moy_evq_unwait(p->pid);             // one may have waited again on its way out
    lock();
    for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
        if (p->thr[k].state == T_DONE) {
            thr_reap(&p->thr[k]);
        }
    }
    uint32_t swept = 0;
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        if (p->h[i].kind != H_FREE) {
            handle_drop(p, i);
            swept++;
        }
    }
    g_stats.swept += swept;
    map_t *maps = p->maps;
    p->maps = NULL;
    p->mapped = 0;
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (g_proc[i] == p) {
            g_proc[i] = NULL;
        }
    }
    unlock();
    while (maps != NULL) {
        map_t *m = maps;
        maps = m->next;
#ifdef MOY_OS_UMODE
        u_scrub_free(m->mem);
#elif defined(MOY_OS_BOARD)
        heap_caps_free(m->mem);
#endif
        os_free(m);
    }
    if (p->net_held && g_net != NULL) {
        g_net(g_net_ctx, p->pid, 0);
    }
    if (moy_lease_sweep != NULL) {
        moy_lease_sweep(p->pid);
    }
    if (p->own_q) {
        moy_evq_close(p->pid);
    }
    if (p->woke_us != 0) {
        moy_super_forget(p->pid);
    }
    if (p->mod.unload != NULL) {
        p->mod.unload(p->mod.loaded);
    }
    if (p->prog.roots != NULL) {
        moy_sh_prog_fini(&p->prog, os_free);
    } else if (p->service) {
        moy_sh_prog_flush(&p->prog, 0);
        moy_sh_prog_flush(&p->prog, 1);
    }
    os_free(p->argv);
    os_free(p->env);
    os_free(p->fb);
#ifdef MOY_OS_UMODE
    while (p->retired != NULL) {
        retired_t *r = p->retired;
        p->retired = r->next;
        u_scrub_free(r->mem);
        os_free(r);
    }
    os_free(p->u);
#endif
    sem_fini(&p->wake);
    sem_fini(&p->hop_done);
    sem_fini(&p->hop_lock);
    sem_fini(&p->stopped);
    sem_fini(&p->fb_room);
    sem_fini(&p->fb_shown);
    p->magic = 0;
    os_free(p);
    return true;
}

static int env_add(void *arg, const char *kv) {
    moy_os_proc_t *p = arg;
    size_t have = 0;
    for (char *e = p->env; e != NULL && *e; e += strlen(e) + 1) {
        have = (size_t)(e - p->env) + strlen(e) + 1;
    }
    size_t n = strlen(kv) + 1;
    char *g = os_alloc(have + n + 1);
    if (g == NULL) {
        return ENOMEM;
    }
    if (p->env != NULL) {
        memcpy(g, p->env, have);
        os_free(p->env);
    }
    memcpy(g + have, kv, n);
    p->env = g;
    return 0;
}

static uint32_t row_add(uint32_t pid, uint8_t kind, const char *id, void *engine) {
    uint32_t h = 0;
    if (moy_proc_add(pid, MOY_PROC_ELF, kind, id != NULL ? id : "", 0, 0, &h) != MOY_PROC_OK) {
        return 0;
    }
    moy_proc_bind(h, engine, NULL);
    return h;
}

static moy_os_proc_t *proc_of(uint32_t pid);

int moy_os_command_start(moy_sh_t *s, const moy_os_module_t *m, const moy_os_caps_t *caps,
                         int argc, const char *const *argv, const char *roots, uint32_t pid,
                         const char *id, uint32_t bg_pid, uint32_t budget_ms, uint32_t flags,
                         const moy_app_run_t *run, char *err, size_t errlen) {
    moy_os_proc_t *p = proc_new(m, caps, pid, false, err, errlen);
    if (p == NULL) {
        if (m->unload != NULL) {
            m->unload(m->loaded);
        }
        return MOY_OS_REFUSED;
    }
    p->s = s;
    p->flags = flags;
    if (run != NULL) {
        p->run = *run;
    }
    size_t args_n = 0;
    for (int i = 0; i < argc; i++) {
        args_n += strlen(argv[i]) + 1;
    }
    p->argv = os_alloc((size_t)(argc + 1) * sizeof(char *) + args_n);
    bool ok = p->argv != NULL
              && moy_sh_prog_init(&p->prog, s, roots, bg_pid, budget_ms, mono_us(), os_alloc) == 0
              && moy_sh_environ(s, env_add, p) == 0;
    if (ok && !(p->hooks & MOY_ELF_H_MAIN)) {
        snprintf(err, errlen, "not a command: it defines no main");
        ok = false;
    } else if (!ok) {
        snprintf(err, errlen, "out of memory");
    }
    if (ok) {
        char *at = (char *)(p->argv + argc + 1);
        for (int i = 0; i < argc; i++) {
            size_t n = strlen(argv[i]) + 1;
            memcpy(at, argv[i], n);
            p->argv[i] = at;
            at += n;
        }
        p->argv[argc] = NULL;
        p->argc = argc;
        p->thr[0].p = p;
        p->thr[0].gen = 1;
        ok = thr_spawn(&p->thr[0]) == 0;
        if (!ok) {
            snprintf(err, errlen, "no task for it");
        }
    }
    if (!ok) {
        proc_end(p);
        return MOY_OS_REFUSED;
    }
    p->row = row_add(pid, MOY_PROC_COMMAND, id, p);
    return 0;
}

// The command's end: its row's, its process's, and its exit status.
static int command_end(moy_os_proc_t *p) {
    int code = p->prog.exited ? p->prog.code : p->code;
    if (p->row) {
        moy_proc_end(p->row, code);
    }
    proc_end(p);
    return code;
}

static bool command_done(void *job) {
    return ((moy_os_proc_t *)job)->main_done;
}

// One of the command's session calls served, waiting `us` at most for it.
static void command_serve(void *job, int64_t us) {
    moy_os_proc_t *p = job;
    sem_take(&p->wake, us);
    void (*fn)(void *) = p->hop_fn;
    if (fn != NULL) {
        p->hop_fn = NULL;
        fn(p->hop_arg);
        sem_give(&p->hop_done);
    }
    task_feed();
}

int moy_os_command_step(uint32_t pid, uint32_t max_us) {
    moy_os_proc_t *p = proc_of(pid);
    if (p == NULL || p->service) {
        return MOY_OS_REFUSED;
    }
    if (moy_sh_prog_step(&p->prog, max_us, mono_us, command_done, command_serve, p)
        == MOY_SH_PROG_RUNNING) {
        return MOY_OS_RUNNING;
    }
    return command_end(p);
}

int moy_os_command_stop(uint32_t pid) {
    moy_os_proc_t *p = proc_of(pid);
    if (p == NULL || p->service) {
        return MOY_OS_REFUSED;
    }
    moy_sh_prog_stop(&p->prog, MOY_PROC_INTERRUPTED);
    return command_end(p);
}

int moy_os_command(moy_sh_t *s, const moy_os_module_t *m, const moy_os_caps_t *caps,
                   int argc, const char *const *argv, const char *roots, uint32_t pid,
                   const char *id, uint32_t bg_pid, uint32_t budget_ms, uint32_t flags,
                   const moy_app_run_t *run, char *err, size_t errlen) {
    int r = moy_os_command_start(s, m, caps, argc, argv, roots, pid, id, bg_pid, budget_ms,
                                 flags, run, err, errlen);
    if (r != 0) {
        return r;
    }
    do {
        r = moy_os_command_step(pid, 1000000u);
    } while (r == MOY_OS_RUNNING);
    return r;
}

// A service's start, or with a screen of w x h (w > 0) a game's: the
// process, its main running on its own task, or NULL with the reason in err.
static moy_os_proc_t *loop_start(const moy_os_module_t *m, const moy_os_caps_t *caps,
                                 uint32_t pid, const char *id, int w, int h,
                                 const moy_app_run_t *run, char *err, size_t errlen) {
    moy_os_proc_t *p = proc_new(m, caps, pid, true, err, errlen);
    if (p == NULL) {
        if (m->unload != NULL) {
            m->unload(m->loaded);
        }
        return NULL;
    }
    p->prog.bg_pid = pid;
    if (run != NULL) {
        p->run = *run;
    }
    const char *name = id != NULL && *id ? id : w > 0 ? "game" : "service";
    size_t k = strlen(name) + 1;
    p->argv = os_alloc(2 * sizeof(char *) + k);
    if (p->argv == NULL) {
        snprintf(err, errlen, "out of memory");
        proc_end(p);
        return NULL;
    }
    p->argv[0] = memcpy((char *)(p->argv + 2), name, k);
    p->argv[1] = NULL;
    p->argc = 1;
    if (!(p->hooks & MOY_ELF_H_MAIN)) {
        snprintf(err, errlen, "not a %s: it defines no main", w > 0 ? "game" : "service");
        proc_end(p);
        return NULL;
    }
    if (!moy_evq_has(pid)) {
        if (moy_evq_open(pid, name, NULL, 0u) != MOY_EVQ_OK) {
            snprintf(err, errlen, "no event queue for it");
            proc_end(p);
            return NULL;
        }
        p->own_q = 1;
    }
    if (w > 0) {
        p->game = 1;
        p->fb_w = w;
        p->fb_h = h;
        p->fb = os_alloc((size_t)w * (size_t)h);
        if (p->fb == NULL) {
            snprintf(err, errlen, "out of memory");
            proc_end(p);
            return NULL;
        }
        if (moy_evq_listen(pid, MOY_OS_FRAME, true) != MOY_EVQ_OK) {
            snprintf(err, errlen, "its event queue hears no frames");
            proc_end(p);
            return NULL;
        }
    }
    p->thr[0].p = p;
    p->thr[0].gen = 1;
    p->started_us = mono_us();
    if (thr_spawn(&p->thr[0]) != 0) {
        snprintf(err, errlen, "no task for it");
        proc_end(p);
        return NULL;
    }
    if (w <= 0) {
        p->row = row_add(pid, MOY_PROC_SERVICE, id, p);
    }
    return p;
}

moy_os_proc_t *moy_os_service(const moy_os_module_t *m, const moy_os_caps_t *caps,
                              uint32_t pid, const char *id, const moy_app_run_t *run,
                              char *err, size_t errlen) {
    return loop_start(m, caps, pid, id, 0, 0, run, err, errlen);
}

moy_os_proc_t *moy_os_game(const moy_os_module_t *m, const moy_os_caps_t *caps, uint32_t pid,
                           const char *id, int w, int h, const moy_app_run_t *run, char *err,
                           size_t errlen) {
    if (w <= 0 || h <= 0) {
        snprintf(err, errlen, "no screen for it");
        if (m->unload != NULL) {
            m->unload(m->loaded);
        }
        return NULL;
    }
    return loop_start(m, caps, pid, id, w, h, run, err, errlen);
}

// The record of `pid`, looked up under the lock: a command's record is freed
// by the task serving it, so only that task, or a caller under the lock,
// reads one past this.
static moy_os_proc_t *proc_of(uint32_t pid) {
    moy_os_proc_t *out = NULL;
    lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (g_proc[i] != NULL && g_proc[i]->pid == pid) {
            out = g_proc[i];
            break;
        }
    }
    unlock();
    return out;
}

// The services' records, taken under the lock. A service's record is freed
// only on the VM's task (moy_os_reap, moy_os_stop, a failed start), which is
// the task that watches and reaps them, so they stay readable past it.
static uint32_t services(moy_os_proc_t **out) {
    uint32_t n = 0;
    lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = g_proc[i];
        if (p != NULL && p->service && !p->game) {
            out[n++] = p;
        }
    }
    unlock();
    return n;
}

// THE WATCHDOG. Native code is not aborted where it stands by a timer: a
// wake that runs on is struck here, on the console's task, once for each
// MOY_SUPER_ABORT_US it runs without polling its descriptor again (the
// strikes its tier's abort would have charged), and its own poll charges it
// nothing more. A wake waiting on the VM's queue is not running. Where the
// strikes suspend it while its wake still runs, suspension cannot hold it,
// so it is killed (MOY_PROC_KILLED), as a stop past its time is; a service
// whose start runs MOY_OS_STOP_US without a first poll is killed too, as a
// Python service's start past the same limit is. Answers the reason it is
// killed, or NULL.
static const char *watch(moy_os_proc_t *p, int64_t now) {
    lock();
    if (p->main_done || p->ending || p->polling || p->hopping) {
        unlock();
        return NULL;
    }
    if (p->woke_us == 0) {
        bool late = now - p->started_us >= MOY_OS_STOP_US;
        unlock();
        return late ? "its start ran past its time without a poll" : NULL;
    }
    int64_t ran = now - p->woke_us;
    bool due = ran >= (int64_t)MOY_SUPER_ABORT_US * (p->struck + 1);
    if (due) {
        p->struck++;
    }
    unlock();
    if (!due) {
        return NULL;
    }
    moy_super_charge(p->pid, (uint32_t)ran, true);
    lock();
    bool out = moy_super_held(p->pid) && !p->polling && !p->main_done;
    unlock();
    return out ? "a wake ran past its strikes without a poll" : NULL;
}

// Service `p` watched (THE WATCHDOG), a kill marking its main done: whether
// its main is done, its reap due.
static bool watched(moy_os_proc_t *p, int64_t now) {
    if (p->main_done) {
        return true;
    }
    const char *why = watch(p, now);
    if (why == NULL) {
        return false;
    }
    char text[96];
    snprintf(text, sizeof text, "killed: %s", why);
    log_line(p, MOY_LOG_ERR, text);
    lock();
    if (!p->main_done) {
        p->code = MOY_PROC_KILLED;
        p->main_done = 1;
    }
    unlock();
    return true;
}

bool moy_os_watch(void) {
    bool live = false;
    int64_t now = mono_us();
    moy_os_proc_t *svc[MOY_OS_PROCS];
    uint32_t n = services(svc);
    for (uint32_t i = 0; i < n; i++) {
        moy_os_proc_t *p = svc[i];
        if (p->reaped) {
            continue;
        }
        live = true;
        if (watched(p, now)) {
            moy_evq_want(MOY_EVQ_WANT_POLL);
        }
    }
    return live;
}

uint32_t moy_os_reap(uint32_t *ended, int32_t *codes, uint32_t n) {
    uint32_t got = 0;
    int64_t now = mono_us();
    moy_os_proc_t *svc[MOY_OS_PROCS];
    uint32_t m = services(svc);
    for (uint32_t i = 0; i < m; i++) {
        moy_os_proc_t *p = svc[i];
        if (!watched(p, now)) {
            continue;
        }
        if (p->reaped) {
            continue;                   // left running (a host's thread): reported once
        }
        int code = p->code;
        uint32_t pid = p->pid;
        if (p->row) {
            moy_proc_end(p->row, code);
            p->row = 0;
        }
        p->reaped = 1;
        proc_end(p);
        if (got < n) {
            ended[got] = pid;
            codes[got] = code;
            got++;
        }
    }
    return got;
}

// Service or game `p` stopped on the console's task (moy_os_stop).
static int loop_stop(moy_os_proc_t *p) {
    uint32_t pid = p->pid;
    p->hup = 1;
    moy_evq_kick(pid);
    int64_t end = mono_us() + MOY_OS_STOP_US;
    while (!p->main_done && mono_us() < end) {
        if (p->hopping) {
            moy_vmq_serve();            // its hop is this task's to run
        }
        sem_take(&p->stopped, p->hopping ? 1000 : MOY_OS_SLICE_US);
    }
    int code = p->main_done ? p->code : MOY_PROC_KILLED;
    if (p->row) {
        moy_proc_end(p->row, code);
    }
    proc_end(p);
    return code;
}

int moy_os_stop(uint32_t pid) {
    moy_os_proc_t *svc[MOY_OS_PROCS], *p = NULL;
    uint32_t n = services(svc);
    for (uint32_t i = 0; i < n; i++) {
        if (svc[i]->pid == pid) {
            p = svc[i];
        }
    }
    if (p == NULL || p->reaped) {
        return -1;
    }
    return loop_stop(p);
}

// -- a game -----------------------------------------------------------------------------
//
// A game's record is freed only on the console's task (moy_os_game_stop, a
// failed start), the task every call below runs on, so it stays readable
// past the lock there.

static moy_os_proc_t *game_of(uint32_t pid) {
    moy_os_proc_t *p = proc_of(pid);
    return p != NULL && p->game ? p : NULL;
}

int moy_os_game_frame(uint32_t pid, uint32_t held, uint32_t pressed, uint32_t ms,
                      uint32_t max_us) {
    moy_os_proc_t *p = game_of(pid);
    if (p == NULL) {
        return -1;
    }
    int out = 0;
    lock();
    if (p->fb_state == FB_TAKEN) {
        p->fb_state = FB_FREE;
        sem_give(&p->fb_room);
    }
    bool send = !p->main_done && (!p->fb_posted || p->events != p->fb_mark);
    uint32_t mark = p->events;
    unlock();
    if (send) {
        char args[40];
        int n = snprintf(args, sizeof args, "[%u,%u,%u]", (unsigned)held, (unsigned)pressed,
                         (unsigned)ms);
        if (moy_evq_post(pid, 0, MOY_OS_FRAME, args, (size_t)n) == MOY_EVQ_OK) {
            lock();
            p->fb_posted = 1;
            p->fb_mark = mark;
            unlock();
            out |= MOY_OS_FRAME_SENT;
        }
    }
    int64_t end = mono_us() + max_us;
    for (;;) {
        lock();
        bool ready = p->fb_state == FB_READY;
        if (ready) {
            p->fb_state = FB_TAKEN;
        }
        bool done = p->main_done;
        bool hop = p->hopping != 0;
        unlock();
        if (ready) {
            return out | MOY_OS_FRAME_SHOWN;
        }
        if (done) {
            return MOY_OS_ENDED;
        }
        int64_t left = end - mono_us();
        if (left <= 0) {
            return out;
        }
        if (hop) {
            moy_vmq_serve();            // its app call is this task's to run
        }
        sem_take(&p->fb_shown, hop ? 1000 : left);
    }
}

const uint8_t *moy_os_game_screen(uint32_t pid, int *w, int *h) {
    moy_os_proc_t *p = game_of(pid);
    if (p == NULL) {
        return NULL;
    }
    *w = p->fb_w;
    *h = p->fb_h;
    return p->fb;
}

int moy_os_game_stop(uint32_t pid) {
    moy_os_proc_t *p = game_of(pid);
    return p != NULL ? loop_stop(p) : -1;
}

// -- faults ---------------------------------------------------------------------------

static moy_os_fault_t g_faults[MOY_OS_FAULTS];
static uint32_t g_nfaults;

uint32_t moy_os_faults(moy_os_fault_t *out, uint32_t n) {
    uint32_t have = g_nfaults < MOY_OS_FAULTS ? g_nfaults : MOY_OS_FAULTS;
    uint32_t k = 0;
    for (; k < n && k < have; k++) {
        out[k] = g_faults[(g_nfaults - 1u - k) % MOY_OS_FAULTS];
    }
    return k;
}

#ifdef MOY_OS_UMODE
void moy_u_bail(uint32_t *frame) __attribute__((noreturn));

static const char *u_why(uint32_t cause, int *sig) {
    *sig = 11;
    switch (cause) {
    case 1: return "jumped outside its memory";
    case 5: return "read outside its memory";
    case 7: return "wrote outside its memory";
    case 0: case 4: case 6: *sig = 7; return "made a misaligned access";
    case 2: *sig = 4; return "ran an illegal instruction";
    case 3: *sig = 5; return "hit a breakpoint";
    case MOY_U_BAD_ROW: *sig = 31; return "called no such row";
    default: break;
    }
    if (cause & MOY_U_BAD_ARG) {
        return "passed a row memory outside it";
    }
    *sig = 4;
    return "trapped";
}

// The thread's process ends where it stands: the fault recorded and logged,
// told on the command's stderr, its code 128 + the signal a POSIX shell would
// name, the thread parked for proc_end. A fault in a call no thread owns
// (BIND, on the loading task) returns to that call instead.
void moy_os_u_fault(moy_u_space_t *s, uint32_t *f, uint32_t cause, uint32_t pc, uint32_t addr) {
    if (s->thr == NULL) {
        s->fault = cause + 1u;
        moy_u_bail(f);
    }
    thr_t *t = s->thr;
    t->in_row++;
    moy_os_proc_t *p = t->p;
    const moy_u_proc_t *u = p->u;
    uint32_t at = pc >= u->lo && pc < u->hi ? pc - u->vbase : pc;
    int sig;
    const char *why = u_why(cause, &sig);
    const char *name = (!p->service || p->game) && p->argv != NULL && p->argv[0] != NULL
                            ? p->argv[0] : "service";
    char text[160];
    if (cause & MOY_U_BAD_ARG) {
        snprintf(text, sizeof text, "%s (pid %u) killed: %s (%s) at pc 0x%x\n", name,
                 (unsigned)p->pid, why, moy_os_row_names[cause & 0xffu], (unsigned)at);
    } else {
        snprintf(text, sizeof text, "%s (pid %u) killed: %s at pc 0x%x, address 0x%08x\n", name,
                 (unsigned)p->pid, why, (unsigned)at, (unsigned)addr);
    }
    lock();
    moy_os_fault_t *r = &g_faults[g_nfaults++ % MOY_OS_FAULTS];
    r->pid = p->pid;
    r->cause = cause;
    r->pc = at;
    r->addr = addr;
    snprintf(r->name, sizeof r->name, "%s", name);
    unlock();
    log_line(p, MOY_LOG_ERR, text);
    if (!p->service) {
        moy_os_write(2, text, strlen(text));
    }
    lock();
    if (!p->main_done) {
        p->code = 128 + sig;
        p->main_done = 1;
    }
    p->ending = 1;
    unlock();
    thr_end(t);
}

void moy_os_u_park(moy_u_space_t *s) {
    thr_t *t = s->thr;
    t->in_row++;
    thr_end(t);
}

// A row from a U-mode module's ecall: its ordinal in t0, its arguments in
// a0..a7, each pointer checked by the row's rule (moy_os_check.c), and the
// row called as any of its prototypes is, through the integer registers.
typedef uint64_t u_row_fn(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t,
                          uint32_t);

// The thread counts as in a row from before the check, so memory the check
// passed is never freed under the row by another thread (u_free), and the
// thread is never deleted in kernel code (proc_end). A call no thread owns
// (BIND) has no rows: its ecall is its fault.
void moy_os_u_row(moy_u_space_t *s, uint32_t *f) {
    uint32_t row = f[5];
    const uint32_t *a = &f[10];
    thr_t *t = s->thr;
    if (t == NULL) {
        moy_os_u_fault(s, f, MOY_U_BAD_ROW, f[0], row);
    }
    t->in_row++;
    if (row >= MOY_OS_ROWS) {
        moy_os_u_fault(s, f, MOY_U_BAD_ROW, f[0], row);
    }
    if (!moy_os_check(s, row, a)) {
        moy_os_u_fault(s, f, MOY_U_BAD_ARG | row, f[0], 0);
    }
    uint64_t r = ((u_row_fn *)(void *)moy_os_table.fn[row])(a[0], a[1], a[2], a[3], a[4], a[5],
                                                             a[6], a[7]);
    f[10] = (uint32_t)r;
    f[11] = (uint32_t)(r >> 32);
    f[0] += 4;
    t->in_row--;
}
#endif

int moy_os_info(uint32_t pid, moy_os_info_t *out) {
    moy_os_proc_t *p = NULL;
    lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (g_proc[i] != NULL && g_proc[i]->pid == pid) {
            p = g_proc[i];
        }
    }
    if (p == NULL) {
        unlock();
        return -1;
    }
    memset(out, 0, sizeof *out);
    out->pid = p->pid;
    out->threads = (uint32_t)live_threads(p);
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        out->handles += p->h[i].kind != H_FREE;
    }
    out->wakes = p->wakes;
    out->events = p->events;
    out->service = p->service;
    out->ending = p->ending;
    out->net = p->net_held;
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        out->sockets += p->h[i].kind == H_SOCK;
    }
    out->mapped = (uint32_t)p->mapped;
    out->cpu_us = p->cpu_us;
    unlock();
    return 0;
}

void moy_os_stats(moy_os_stats_t *out) {
    *out = g_stats;
}

uint32_t moy_os_stack_low(void) {
    uint32_t low = UINT32_MAX;
#ifdef MOY_OS_BOARD
    lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = g_proc[i];
        for (uint32_t k = 0; p != NULL && k < MOY_OS_THREADS; k++) {
            if (p->thr[k].state == T_LIVE && p->thr[k].task != NULL) {
                uint32_t w = (uint32_t)uxTaskGetStackHighWaterMark(p->thr[k].task);
                low = w < low ? w : low;
            }
        }
    }
    unlock();
#endif
    return low;
}
