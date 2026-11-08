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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_os.h"
#include "moy_os_rows.h"
#include "moy_proc.h"
#include "moy_sh_prog.h"

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
#include <pthread.h>
#include <setjmp.h>
#endif

// The input table's readers and the lease table's sweep, where the image has them.
#include "moy_input.h"
uint32_t moy_lease_sweep(uint32_t owner) __attribute__((weak));
moy_input_t *moy_input_kernel(void) __attribute__((weak));
int moy_input_reader(moy_input_t *t, uint32_t kinds, int *r) __attribute__((weak));
int moy_input_reader_free(moy_input_t *t, int r) __attribute__((weak));
size_t moy_input_events(moy_input_t *t, int r, moy_event_t *out, size_t n) __attribute__((weak));

#define MAGIC 0x6d6f796fu

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

enum { T_FREE = 0, T_LIVE, T_DONE };
enum { H_FREE = 0, H_TIMER, H_MUTEX, H_DIR, H_READER, H_FD };
enum { M_START = 1, M_TIMER, M_ON, M_STOP };

typedef struct {
    struct moy_os_proc *p;
    task_t task;
    uint8_t state;
    uint8_t gen;
    uint8_t detached;
    uint8_t joined;
    volatile int in_row;
    int err;
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
    uint8_t gen;
    int fd;                     // H_FD: the session's descriptor; H_READER: the reader, -1 before the poll makes it
    uint32_t kinds;             // H_READER: the event kinds asked for
    void *ptr;
} handle_t;

typedef struct {
    uint8_t kind;
    uint32_t a;
    moy_event_t e;
} mail_t;

typedef struct {
    struct moy_os_proc *p;
    int h;
    uint32_t period_us;
#ifdef MOY_OS_BOARD
    esp_timer_handle_t t;
#else
    pthread_t t;
    volatile int stop;
#endif
    volatile int pending;
} timer_t_;

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
    // a service's calls
    sem_t_ mail_lock;
    mail_t mail[MOY_OS_MAIL];
    uint8_t mail_at, mail_n;
    sem_t_ mail_go;
    sem_t_ stopped;
    // what it holds
    thr_t thr[MOY_OS_THREADS];
    handle_t h[MOY_OS_HANDLES];
    uint32_t calls, timers, events;
};

static moy_os_proc_t *g_proc[MOY_OS_PROCS];
static moy_os_stats_t g_stats;
static int g_err_none;              // errno for a thread no process owns

static void log_line(moy_os_proc_t *p, int level, const char *text) {
    moy_log_write(level, p != NULL ? p->pid : MOY_LOG_KERNEL, text, strlen(text));
}

// The calling thread's record, or NULL: a thread no process owns.
static thr_t *self_thr(void) {
    task_t me = task_self();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = g_proc[i];
        if (p == NULL) {
            continue;
        }
        for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
            if (p->thr[k].state == T_LIVE && task_is(p->thr[k].task, me)) {
                return &p->thr[k];
            }
        }
    }
    return NULL;
}

void moy_os_errno_set(int e) {
    thr_t *t = self_thr();
    if (t != NULL) {
        t->err = e;
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

// A row's start and end: a thread inside one is never deleted where it stands;
// one whose process is ending leaves at the row's end instead.
static thr_t *enter(void) {
    thr_t *t = self_thr();
    if (t != NULL) {
        t->in_row++;
    }
    return t;
}

static void leave(thr_t *t) {
    if (t != NULL && --t->in_row == 0 && t->p->ending) {
        thr_end(t);
    }
}

static bool ending(thr_t *t) {
    return t != NULL && t->p->ending;
}

typedef struct {
    thr_t *t;
    int op;
} go_t;

#ifdef MOY_OS_BOARD
static void thr_main(void *arg)
#else
static void *thr_main(void *arg)
#endif
{
    thr_t *t = arg;
    moy_os_proc_t *p = t->p;
    t->task = task_self();
#ifndef MOY_OS_BOARD
    if (setjmp(t->home) != 0) {
        thr_done(t);
        return NULL;
    }
#endif
    if (t == &p->thr[0]) {
        if (!p->service) {
            int code = p->mod.entry(&moy_os_table, MOY_ELF_OP_MAIN, p->argc, p->argv, NULL);
            lock();
            if (!p->main_done) {
                p->code = code;
                p->main_done = 1;
            }
            unlock();
        } else {
            // A service's dispatch: its start, then its calls, in order.
            p->calls++;
            int code = p->mod.entry(&moy_os_table, MOY_ELF_OP_START, 0, NULL, NULL);
            bool stop = code != 0;
            if (stop) {
                p->code = code;
            }
            while (!stop && !p->ending) {
                if (!sem_take(&p->mail_go, MOY_OS_SLICE_US)) {
                    continue;
                }
                for (;;) {
                    mail_t m;
                    sem_take(&p->mail_lock, -1);
                    bool got = p->mail_n > 0;
                    if (got) {
                        m = p->mail[p->mail_at];
                        p->mail_at = (uint8_t)((p->mail_at + 1u) % MOY_OS_MAIL);
                        p->mail_n--;
                    }
                    sem_give(&p->mail_lock);
                    if (!got || p->ending) {
                        break;
                    }
                    p->calls++;
                    if (m.kind == M_TIMER) {
                        timer_t_ *tm = p->h[m.a].kind == H_TIMER ? p->h[m.a].ptr : NULL;
                        if (tm != NULL) {
                            tm->pending = 0;
                        }
                        p->timers++;
                        p->mod.entry(&moy_os_table, MOY_ELF_OP_TIMER, (int)(uint32_t)mono_us(),
                                     NULL, NULL);
                    } else if (m.kind == M_ON) {
                        p->events++;
                        p->mod.entry(&moy_os_table, MOY_ELF_OP_ON, 0, NULL, &m.e);
                    } else if (m.kind == M_STOP) {
                        p->mod.entry(&moy_os_table, MOY_ELF_OP_STOP, 0, NULL, NULL);
                        stop = true;
                        break;
                    }
                }
            }
            lock();
            p->main_done = 1;
            unlock();
        }
    } else {
        t->out = t->start(t->arg);
    }
    thr_end(t);
#ifndef MOY_OS_BOARD
    return NULL;
#endif
}

// A task for record `t` with the process's caps: 0, or an errno value.
static int thr_spawn(thr_t *t) {
    moy_os_proc_t *p = t->p;
    sem_init(&t->done, false);
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
#ifdef MOY_OS_BOARD
    vTaskDeleteWithCaps(t->task);
#endif
    sem_fini(&t->done);
    memset(t, 0, sizeof *t);
}

// -- handles -----------------------------------------------------------------------

static int handle_new(moy_os_proc_t *p, uint8_t kind, void *ptr, int fd) {
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        if (p->h[i].kind == H_FREE) {
            p->h[i].kind = kind;
            p->h[i].gen = (uint8_t)(p->h[i].gen + 1u);
            p->h[i].ptr = ptr;
            p->h[i].fd = fd;
            return (int)i;
        }
    }
    return -1;
}

static void timer_stop(timer_t_ *tm);

// Handle `i` given back: what it holds let go.
static void handle_drop(moy_os_proc_t *p, uint32_t i) {
    handle_t *h = &p->h[i];
    switch (h->kind) {
    case H_TIMER:
        timer_stop(h->ptr);
        break;
    case H_MUTEX:
        sem_fini(h->ptr);
        os_free(h->ptr);
        break;
    case H_DIR:
        os_free(((dir_t *)h->ptr)->names);
        os_free(h->ptr);
        break;
    case H_READER:
        if (h->fd >= 0 && moy_input_kernel != NULL && moy_input_kernel() != NULL) {
            moy_input_reader_free(moy_input_kernel(), h->fd);
        }
        break;
    case H_FD:
        if (p->s != NULL) {
            moy_sh_fd_close(p->s, h->fd);
        }
        break;
    default:
        return;
    }
    h->kind = H_FREE;
    h->ptr = NULL;
}

// A module's id for handle `i`: never 0 nor all ones (PTHREAD_MUTEX_INITIALIZER's).
static uint32_t handle_id(const moy_os_proc_t *p, int i) {
    return ((uint32_t)p->h[i].gen << 8 | (uint32_t)(i + 1)) & 0xffffu;
}

static int handle_of(const moy_os_proc_t *p, uint32_t id, uint8_t kind) {
    uint32_t i = (id & 0xffu) - 1u;
    if (i >= MOY_OS_HANDLES || p->h[i].kind != kind || handle_id(p, (int)i) != (id & 0xffffu)) {
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

// -- the rows: files and streams ----------------------------------------------------

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
        if (c->fd > 2 && fd_owned(p, c->fd) < 0) {
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
        if (c->fd > 2 && fd_owned(p, c->fd) < 0) {
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
    fcall_t c = { .op = F_CLOSE, .fd = fd };
    long r = fcall(t, &c);
    leave(t);
    return (int)r;
}

ssize_t moy_os_read(int fd, void *buf, size_t n) {
    thr_t *t = enter();
    fcall_t c = { .op = F_READ, .fd = fd, .buf = buf, .n = n };
    long r = fcall(t, &c);
    leave(t);
    return (ssize_t)r;
}

ssize_t moy_os_write(int fd, const void *buf, size_t n) {
    thr_t *t = enter();
    fcall_t c = { .op = F_WRITE, .fd = fd, .buf = (void *)buf, .n = n };
    long r = fcall(t, &c);
    leave(t);
    return (ssize_t)r;
}

off_t moy_os_lseek(int fd, off_t at, int whence) {
    thr_t *t = enter();
    fcall_t c = { .op = F_SEEK, .fd = fd, .at = (int64_t)at, .whence = whence };
    long r = fcall(t, &c);
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
        }
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
    size_t k = name != NULL ? strlen(name) : 0;
    for (char *e = t != NULL ? t->p->env : NULL; e != NULL && *e && k; e += strlen(e) + 1) {
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
    thr_t *t = self_thr();
    if (t == NULL) {
        abort();
    }
    moy_os_proc_t *p = t->p;
    lock();
    if (!p->main_done) {
        p->code = code & 0xff;
        p->main_done = 1;
    }
    p->ending = 1;
    unlock();
    thr_end(t);
}

int *moy_os___errno(void) {
    thr_t *t = self_thr();
    return t != NULL ? &t->err : &g_err_none;
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
    if (clock_gettime(clock, ts) != 0) {
        moy_os_errno_set(errno);
        return -1;
    }
    return 0;
}

int moy_os_gettimeofday(struct timeval *tv, void *tz) {
    (void)tz;
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

// pthread_t is the thread's slot and generation, never 0.
static uint32_t thr_id(const moy_os_proc_t *p, const thr_t *t) {
    return (uint32_t)t->gen << 8 | (uint32_t)(t - p->thr + 1);
}

static thr_t *thr_of(moy_os_proc_t *p, uint32_t id) {
    uint32_t i = (id & 0xffu) - 1u;
    if (i >= MOY_OS_THREADS || p->thr[i].state == T_FREE || p->thr[i].gen != (uint8_t)(id >> 8)) {
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
            if (p->thr[i].state == T_DONE && p->thr[i].detached) {
                uint8_t gen = p->thr[i].gen;
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
            uint8_t gen = (uint8_t)(n->gen + 1u);
            memset(n, 0, sizeof *n);
            n->gen = gen ? gen : 1;
            n->p = p;
            n->start = start;
            n->arg = arg;
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

int moy_os_pthread_join(pthread_t thread, void **out) {
    thr_t *t = enter();
    int r = ESRCH;
    thr_t *o = t != NULL ? thr_of(t->p, (uint32_t)thread) : NULL;
    if (o == t && o != NULL) {
        r = EDEADLK;
    } else if (o != NULL && o != &t->p->thr[0] && !o->detached && !o->joined) {
        o->joined = 1;
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
            uint8_t gen = o->gen;
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
    thr_t *o = t != NULL ? thr_of(t->p, (uint32_t)thread) : NULL;
    if (o != NULL) {
        o->detached = 1;
    }
    leave(t);
    return o != NULL ? 0 : ESRCH;
}

pthread_t moy_os_pthread_self(void) {
    thr_t *t = self_thr();
    return t != NULL ? (pthread_t)thr_id(t->p, t) : (pthread_t)0;
}

void moy_os_pthread_exit(void *out) {
    thr_t *t = self_thr();
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

static sem_t_ *mutex_of(thr_t *t, pthread_mutex_t *m, bool make) {
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
    sem_t_ *out = h >= 0 ? t->p->h[h].ptr : NULL;
    unlock();
    return out;
}

int moy_os_pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *attr) {
    (void)attr;
    thr_t *t = enter();
    if (m != NULL) {
        memset(m, 0, sizeof *m);
    }
    sem_t_ *s = mutex_of(t, m, true);
    leave(t);
    return s != NULL ? 0 : EAGAIN;
}

int moy_os_pthread_mutex_destroy(pthread_mutex_t *m) {
    thr_t *t = enter();
    int r = EINVAL;
    if (t != NULL && m != NULL) {
        lock();
        int h = handle_of(t->p, *mutex_word(m), H_MUTEX);
        if (h >= 0) {
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
    sem_t_ *s = mutex_of(t, m, true);
    int r = s != NULL ? 0 : EINVAL;
    while (s != NULL && !sem_take(s, MOY_OS_SLICE_US)) {
        if (ending(t)) {
            r = EINTR;
            break;
        }
    }
    leave(t);
    return r;
}

int moy_os_pthread_mutex_trylock(pthread_mutex_t *m) {
    thr_t *t = enter();
    sem_t_ *s = mutex_of(t, m, true);
    int r = s == NULL ? EINVAL : sem_take(s, 0) ? 0 : EBUSY;
    leave(t);
    return r;
}

int moy_os_pthread_mutex_unlock(pthread_mutex_t *m) {
    thr_t *t = enter();
    sem_t_ *s = mutex_of(t, m, false);
    if (s != NULL) {
        sem_give(s);
    }
    leave(t);
    return s != NULL ? 0 : EINVAL;
}

// -- the rows: Moybyte's -------------------------------------------------------------

int moy_os_moy_log(int level, const char *text) {
    thr_t *t = enter();
    if (text != NULL) {
        log_line(t != NULL ? t->p : NULL, level < 0 || level >= MOY_LOG_LEVELS ? MOY_LOG_INFO : level,
                 text);
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

static void mail_post(moy_os_proc_t *p, const mail_t *m) {
    sem_take(&p->mail_lock, -1);
    bool ok = p->mail_n < MOY_OS_MAIL;
    if (ok) {
        p->mail[(p->mail_at + p->mail_n) % MOY_OS_MAIL] = *m;
        p->mail_n++;
    }
    sem_give(&p->mail_lock);
    if (ok) {
        sem_give(&p->mail_go);
    }
}

static void timer_tick(timer_t_ *tm) {
    if (tm->pending || tm->p->ending) {
        return;                 // a tick still waiting is the one the module gets
    }
    tm->pending = 1;
    mail_t m = { .kind = M_TIMER, .a = (uint32_t)tm->h };
    mail_post(tm->p, &m);
}

#ifdef MOY_OS_BOARD
static void timer_cb(void *arg) {
    timer_tick(arg);
}
#else
static void *timer_thread(void *arg) {
    timer_t_ *tm = arg;
    int64_t next = mono_us() + tm->period_us;
    while (!tm->stop) {
        int64_t d = next - mono_us();
        if (d > 0) {
            struct timespec ts = { 0, (long)(d < 5000 ? d : 5000) * 1000 };
            nanosleep(&ts, NULL);
            continue;
        }
        next += tm->period_us;
        timer_tick(tm);
    }
    return NULL;
}
#endif

static void timer_stop(timer_t_ *tm) {
#ifdef MOY_OS_BOARD
    esp_timer_stop(tm->t);
    esp_timer_delete(tm->t);
#else
    tm->stop = 1;
    pthread_join(tm->t, NULL);
#endif
    os_free(tm);
}

int moy_os_moy_timer_every(uint32_t period_us) {
    thr_t *t = enter();
    int r = -1;
    if (t == NULL || !t->p->service) {
        moy_os_errno_set(ENOTSUP);
    } else if (period_us < 1000) {
        moy_os_errno_set(EINVAL);
    } else {
        timer_t_ *tm = os_alloc(sizeof *tm);
        lock();
        int h = tm != NULL ? handle_new(t->p, H_TIMER, tm, -1) : -1;
        unlock();
        if (h < 0) {
            os_free(tm);
            moy_os_errno_set(tm != NULL ? EMFILE : ENOMEM);
        } else {
            tm->p = t->p;
            tm->h = h;
            tm->period_us = period_us;
#ifdef MOY_OS_BOARD
            esp_timer_create_args_t a = { .callback = timer_cb, .arg = tm, .name = "moy_elf" };
            bool ok = esp_timer_create(&a, &tm->t) == ESP_OK;
            if (ok && esp_timer_start_periodic(tm->t, period_us) != ESP_OK) {
                esp_timer_delete(tm->t);
                ok = false;
            }
#else
            bool ok = pthread_create(&tm->t, NULL, timer_thread, tm) == 0;
#endif
            if (!ok) {
                lock();
                t->p->h[h].kind = H_FREE;
                unlock();
                os_free(tm);
                moy_os_errno_set(EAGAIN);
            } else {
                r = (int)handle_id(t->p, h);
            }
        }
    }
    leave(t);
    return r;
}

int moy_os_moy_timer_cancel(int timer) {
    thr_t *t = enter();
    int h = t != NULL ? handle_of(t->p, (uint32_t)timer, H_TIMER) : -1;
    if (h >= 0) {
        lock();
        handle_drop(t->p, (uint32_t)h);
        unlock();
    } else {
        moy_os_errno_set(EINVAL);
    }
    leave(t);
    return h >= 0 ? 0 : -1;
}

int moy_os_moy_events(uint32_t kinds) {
    thr_t *t = enter();
    int r = -1;
    if (t == NULL || !t->p->service) {
        moy_os_errno_set(ENOTSUP);
    } else if ((kinds & MOY_EV_ALL) == 0) {
        moy_os_errno_set(EINVAL);
    } else {
        // The reader itself is the poll's to make, on the console's task.
        lock();
        int h = handle_new(t->p, H_READER, NULL, -1);
        if (h >= 0) {
            t->p->h[h].kinds = kinds & MOY_EV_ALL;
            r = (int)handle_id(t->p, h);
        }
        unlock();
        if (r < 0) {
            moy_os_errno_set(EMFILE);
        }
    }
    leave(t);
    return r;
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
    sem_init(&p->mail_lock, true);
    sem_init(&p->mail_go, false);
    sem_init(&p->stopped, false);
    p->hooks = (uint32_t)p->mod.entry(&moy_os_table, MOY_ELF_OP_BIND, 0, NULL, NULL);
    lock();
    g_proc[slot] = p;
    unlock();
    g_stats.started++;
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
    sem_give(&p->mail_go);
    sem_give(&p->hop_done);
    int64_t end = mono_us() + MOY_OS_REAP_US;
    while (live_threads(p) > 0) {
        bool inside = false;
        for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
            inside |= p->thr[k].state == T_LIVE && p->thr[k].in_row > 0;
        }
        if (!inside) {
#ifdef MOY_OS_BOARD
            // Every live thread is in the module's code: deleted there.
            lock();
            for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
                thr_t *t = &p->thr[k];
                if (t->state == T_LIVE && t->in_row == 0) {
                    vTaskDeleteWithCaps(t->task);
                    sem_fini(&t->done);
                    memset(t, 0, sizeof *t);
                    g_stats.killed++;
                }
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
        // answer refused.
        if (p->hop_fn != NULL) {
            p->hop_fn = NULL;
            sem_give(&p->hop_done);
        }
        sem_take(&p->stopped, MOY_OS_SLICE_US);
    }
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
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (g_proc[i] == p) {
            g_proc[i] = NULL;
        }
    }
    unlock();
    if (moy_lease_sweep != NULL) {
        moy_lease_sweep(p->pid);
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
    sem_fini(&p->wake);
    sem_fini(&p->hop_done);
    sem_fini(&p->hop_lock);
    sem_fini(&p->mail_lock);
    sem_fini(&p->mail_go);
    sem_fini(&p->stopped);
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
                         const char *id, uint32_t bg_pid, uint32_t budget_ms, char *err,
                         size_t errlen) {
    moy_os_proc_t *p = proc_new(m, caps, pid, false, err, errlen);
    if (p == NULL) {
        if (m->unload != NULL) {
            m->unload(m->loaded);
        }
        return MOY_OS_REFUSED;
    }
    p->s = s;
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

int moy_os_command_step(uint32_t pid, uint32_t max_us) {
    moy_os_proc_t *p = proc_of(pid);
    if (p == NULL || p->service) {
        return MOY_OS_REFUSED;
    }
    // Serve the session's calls until main ends, its run is stopped, it
    // calls exit, or the step's time is up.
    int64_t end = mono_us() + max_us;
    while (!p->main_done) {
        int64_t now = mono_us();
        if (!moy_sh_prog_poll(&p->prog, now)) {
            break;
        }
        if (now >= end) {
            return MOY_OS_RUNNING;
        }
        int64_t wait = end - now < MOY_SH_PROG_POLL_US ? end - now : MOY_SH_PROG_POLL_US;
        sem_take(&p->wake, wait);
        void (*fn)(void *) = p->hop_fn;
        if (fn != NULL) {
            p->hop_fn = NULL;
            fn(p->hop_arg);
            sem_give(&p->hop_done);
        }
        task_feed();
    }
    return command_end(p);
}

int moy_os_command_stop(uint32_t pid) {
    moy_os_proc_t *p = proc_of(pid);
    if (p == NULL || p->service) {
        return MOY_OS_REFUSED;
    }
    moy_sh_prog_stop(&p->prog, MOY_SH_PROG_INTERRUPTED);
    return command_end(p);
}

int moy_os_command(moy_sh_t *s, const moy_os_module_t *m, const moy_os_caps_t *caps,
                   int argc, const char *const *argv, const char *roots, uint32_t pid,
                   const char *id, uint32_t bg_pid, uint32_t budget_ms, char *err,
                   size_t errlen) {
    int r = moy_os_command_start(s, m, caps, argc, argv, roots, pid, id, bg_pid, budget_ms,
                                 err, errlen);
    if (r != 0) {
        return r;
    }
    do {
        r = moy_os_command_step(pid, 1000000u);
    } while (r == MOY_OS_RUNNING);
    return r;
}

moy_os_proc_t *moy_os_service(const moy_os_module_t *m, const moy_os_caps_t *caps,
                              uint32_t pid, const char *id, char *err, size_t errlen) {
    moy_os_proc_t *p = proc_new(m, caps, pid, true, err, errlen);
    if (p == NULL) {
        if (m->unload != NULL) {
            m->unload(m->loaded);
        }
        return NULL;
    }
    p->prog.bg_pid = pid;
    p->thr[0].p = p;
    p->thr[0].gen = 1;
    if (thr_spawn(&p->thr[0]) != 0) {
        snprintf(err, errlen, "no task for it");
        proc_end(p);
        return NULL;
    }
    p->row = row_add(pid, MOY_PROC_SERVICE, id, p);
    return p;
}

static moy_os_proc_t *proc_of(uint32_t pid) {
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (g_proc[i] != NULL && g_proc[i]->pid == pid) {
            return g_proc[i];
        }
    }
    return NULL;
}

uint32_t moy_os_poll(uint32_t *ended, int32_t *codes, uint32_t n) {
    uint32_t got = 0;
    moy_input_t *in = moy_input_kernel != NULL ? moy_input_kernel() : NULL;
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = g_proc[i];
        if (p == NULL || !p->service) {
            continue;
        }
        if (p->main_done) {
            int code = p->code;
            uint32_t pid = p->pid;
            if (p->row) {
                moy_proc_end(p->row, code);
            }
            if (proc_end(p) && got < n) {
                ended[got] = pid;
                codes[got] = code;
                got++;
            }
            continue;
        }
        for (uint32_t k = 0; in != NULL && k < MOY_OS_HANDLES; k++) {
            if (p->h[k].kind != H_READER) {
                continue;
            }
            if (p->h[k].fd < 0) {
                int rd = -1;
                if (moy_input_reader(in, p->h[k].kinds, &rd) != 0) {
                    continue;
                }
                p->h[k].fd = rd;
            }
            moy_event_t ev[8];
            size_t c;
            while ((c = moy_input_events(in, p->h[k].fd, ev, 8)) > 0) {
                for (size_t j = 0; j < c; j++) {
                    mail_t mm = { .kind = M_ON, .e = ev[j] };
                    mail_post(p, &mm);
                }
            }
        }
    }
    return got;
}

void moy_os_post_event(const moy_event_t *e) {
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = g_proc[i];
        if (p == NULL || !p->service || !(p->hooks & MOY_ELF_H_ON)) {
            continue;
        }
        bool wants = false;
        for (uint32_t k = 0; k < MOY_OS_HANDLES; k++) {
            wants |= p->h[k].kind == H_READER && (p->h[k].kinds & MOY_EV_BIT(e->kind));
        }
        if (wants) {
            mail_t m = { .kind = M_ON, .e = *e };
            mail_post(p, &m);
        }
    }
}

int moy_os_stop(uint32_t pid) {
    moy_os_proc_t *p = proc_of(pid);
    if (p == NULL || !p->service) {
        return -1;
    }
    mail_t m = { .kind = M_STOP };
    mail_post(p, &m);
    int64_t end = mono_us() + MOY_OS_STOP_US;
    while (!p->main_done && mono_us() < end) {
        sem_take(&p->stopped, MOY_OS_SLICE_US);
    }
    int code = p->main_done ? p->code : MOY_SH_PROG_KILLED;
    if (p->row) {
        moy_proc_end(p->row, code);
    }
    proc_end(p);
    return code;
}

int moy_os_info(uint32_t pid, moy_os_info_t *out) {
    moy_os_proc_t *p = proc_of(pid);
    if (p == NULL) {
        return -1;
    }
    memset(out, 0, sizeof *out);
    out->pid = p->pid;
    out->threads = (uint32_t)live_threads(p);
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        out->handles += p->h[i].kind != H_FREE;
    }
    out->calls = p->calls;
    out->timers = p->timers;
    out->events = p->events;
    out->service = p->service;
    out->ending = p->ending;
    return 0;
}

void moy_os_stats(moy_os_stats_t *out) {
    *out = g_stats;
}
