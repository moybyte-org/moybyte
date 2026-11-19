// moy_os: a native module's process and the OS table's rows (moy_os.h).
#include "moy_os_int.h"

// -- the platform: memory, clocks, semaphores, tasks, timers -------------------------

void *moy_os_alloc(size_t n) {
    return moy_kalloc(n, MOY_KMEM_PSRAM | MOY_KMEM_ZERO);
}

void moy_os_free(void *p) {
    moy_kfree(p);
}



void moy_os_sem_init(sem_t_ *s, bool mutex) {
#ifdef MOY_ON_BOARD
    s->h = mutex ? xSemaphoreCreateMutexStatic(&s->buf) : xSemaphoreCreateBinaryStatic(&s->buf);
#else
    pthread_mutex_init(&s->m, NULL);
    pthread_cond_init(&s->c, NULL);
    s->n = mutex ? 1 : 0;
    s->mutex = mutex;
#endif
}

void moy_os_sem_fini(sem_t_ *s) {
#ifdef MOY_ON_BOARD
    vSemaphoreDelete(s->h);
#else
    pthread_cond_destroy(&s->c);
    pthread_mutex_destroy(&s->m);
#endif
}

void moy_os_sem_give(sem_t_ *s) {
#ifdef MOY_ON_BOARD
    xSemaphoreGive(s->h);
#else
    pthread_mutex_lock(&s->m);
    s->n = 1;
    pthread_cond_signal(&s->c);
    pthread_mutex_unlock(&s->m);
#endif
}

// Taken within `us` (<0: forever): true.
bool moy_os_sem_take(sem_t_ *s, int64_t us) {
#ifdef MOY_ON_BOARD
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

void moy_os_lock(void) {
    if (!g_lock_made) {
        moy_os_sem_init(&g_lock, true);
        g_lock_made = true;
    }
    moy_os_sem_take(&g_lock, -1);
}

void moy_os_unlock(void) {
    moy_os_sem_give(&g_lock);
}


static task_t task_self(void) {
#ifdef MOY_ON_BOARD
    return xTaskGetCurrentTaskHandle();
#else
    return pthread_self();
#endif
}

// The task watchdog fed for a task that waits on a module (a background
// command's door): a no-op where the task is not watched.
void moy_os_task_feed(void) {
#ifdef MOY_ON_BOARD
    if (esp_task_wdt_status(NULL) == ESP_OK) {
        esp_task_wdt_reset();
    }
#endif
}

static bool task_is(task_t a, task_t b) {
#ifdef MOY_ON_BOARD
    return a == b;
#else
    return pthread_equal(a, b);
#endif
}

moy_os_proc_t *moy_os_g_proc[MOY_OS_PROCS];
moy_os_stats_t moy_os_g_stats;
moy_os_net_fn moy_os_g_net;
void *moy_os_g_net_ctx;
int moy_os_g_err_none;              // errno for a thread no process owns

void moy_os_log_line(moy_os_proc_t *p, int level, const char *text) {
    moy_log_write(level, p != NULL ? p->pid : MOY_LOG_KERNEL, text, strlen(text));
}

// A text of the module's length, `cap` at most, read only inside the region
// of its process it starts in: another of its threads may take its NUL away
// after the row's check, and a read past the region could run off the end of
// PSRAM. `cap` or more: no NUL inside both.
size_t moy_os_text_len(thr_t *t, const char *p, size_t cap) {
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

void moy_os_self_clear(thr_t *t) {
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
        moy_os_g_err_none = e;
    }
}

// -- a thread's life --------------------------------------------------------------

static void thr_end(thr_t *t) __attribute__((noreturn));

// A joiner and the process's waiters woken, then the record DONE: its last
// touch of the record and the process, which a reaper that sees it frees.
static void thr_done(thr_t *t) {
    moy_os_lock();
    moy_os_self_clear(t);
    moy_os_sem_give(&t->done);
    moy_os_sem_give(&t->p->wake);
    moy_os_sem_give(&t->p->stopped);
    t->state = T_DONE;
    moy_os_unlock();
}

// The thread stops where it stands: DONE and parked for its reaper (a board),
// or back in thr_main's frame by longjmp (a host), which ends it there.
// Never pthread_exit: its unwinding walks the module's frames, whose code
// and unwind tables the module's unload takes away.
static void thr_end(thr_t *t) {
#ifdef MOY_ON_BOARD
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
thr_t *moy_os_enter(void) {
    thr_t *t = self_thr();
    if (t != NULL) {
        t->in_row++;
    }
    return t;
}

void moy_os_leave(thr_t *t) {
    if (t == NULL) {
        return;
    }
    if (t->in_row == 1 && t->p->ending) {
        thr_end(t);
    }
    t->in_row--;
}

bool moy_os_ending(thr_t *t) {
    return t != NULL && t->p->ending;
}

typedef struct {
    thr_t *t;
    int op;
} go_t;

#ifdef MOY_OS_UMODE
u_area_t *moy_os_u_area(const thr_t *t) {
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

#ifdef MOY_ON_BOARD
static void thr_main(void *arg)
#else
static void *thr_main(void *arg)
#endif
{
    thr_t *t = arg;
    moy_os_proc_t *p = t->p;
    const bool service = p->service;
    t->task = task_self();
    moy_os_lock();
    self_set(t);
    moy_os_unlock();
#ifndef MOY_ON_BOARD
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
        moy_os_lock();
        if (!p->main_done) {
            p->code = code;
            p->main_done = 1;
        }
        moy_os_unlock();
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
#ifndef MOY_ON_BOARD
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
void moy_os_u_scrub_free(void *mem) {
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
    moy_u_space_t *u = moy_os_alloc(sizeof *u);
    if (mem == NULL || u == NULL || moy_u_add(p->u, (uint32_t)mem, (uint32_t)mem + len,
                                              MOY_U_R | MOY_U_W) != 0) {
        heap_caps_free(mem);
        moy_os_free(u);
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
    u_area_t *area = moy_os_u_area(t);
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


// Memory a U-mode process held, freed now when no other thread of it can be
// inside a row or running on another core, else at the process's end.
static void u_free(moy_os_proc_t *p, void *mem) {
    bool busy = p->caps.core < 0;
    thr_t *me = self_thr();
    for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
        busy |= &p->thr[k] != me && p->thr[k].state == T_LIVE && p->thr[k].in_row > 0;
    }
    retired_t *r = busy ? moy_os_alloc(sizeof *r) : NULL;
    if (r == NULL) {
        moy_os_u_scrub_free(mem);
        return;
    }
    r->mem = mem;
    r->next = __atomic_load_n(&p->retired, __ATOMIC_RELAXED);
    while (!__atomic_compare_exchange_n(&p->retired, &r->next, r, false, __ATOMIC_ACQ_REL,
                                        __ATOMIC_RELAXED)) {
    }
}

// A thread's U stack and windows let go, its task gone.
void moy_os_u_thread_end(thr_t *t) {
    if (t->u == NULL) {
        return;
    }
    moy_u_drop(t->p->u, t->u->slo);
    u_free(t->p, t->ustack);
    moy_os_free(t->u);
    t->u = NULL;
    t->ustack = NULL;
}
#endif

int moy_os_thr_spawn(thr_t *t) {
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
    moy_os_sem_init(&t->done, false);
    t->in_row = 1;              // thr_main's own code until the module's runs
    t->state = T_LIVE;
#ifdef MOY_ON_BOARD
    TaskHandle_t h = NULL;
    BaseType_t core = p->caps.core < 0 ? tskNO_AFFINITY : (BaseType_t)p->caps.core;
    // The record is LIVE before the task can run, so its first row finds it.
    if (!moy_task_spawn_as(MOY_TASK_ELF, NULL, p->caps.stack, (int)p->caps.prio, (int)core,
                           thr_main, t, &h)) {
        t->state = T_FREE;
        moy_os_sem_fini(&t->done);
#ifdef MOY_OS_UMODE
        moy_os_u_thread_end(t);
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
    moy_os_lock();
    int r = pthread_create(&t->task, &a, thr_main, t);
    moy_os_unlock();
    pthread_attr_destroy(&a);
    if (r != 0) {
        t->state = T_FREE;
        moy_os_sem_fini(&t->done);
        return r;
    }
    return 0;
#endif
}

// A DONE thread's task deleted (a board) and its record freed.
void moy_os_thr_reap(thr_t *t) {
    moy_os_self_clear(t);
#ifdef MOY_ON_BOARD
    vTaskDeleteWithCaps(t->task);
#endif
#ifdef MOY_OS_UMODE
    moy_os_u_thread_end(t);
#endif
    moy_os_sem_fini(&t->done);
    memset(t, 0, sizeof *t);
}

// -- handles -----------------------------------------------------------------------

int moy_os_handle_new(moy_os_proc_t *p, uint8_t kind, void *ptr, int fd) {
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
void moy_os_handle_drop(moy_os_proc_t *p, uint32_t i) {
    handle_t *h = &p->h[i];
    switch (h->kind) {
    case H_MUTEX:
        moy_os_sem_fini(h->ptr);
        moy_os_free(h->ptr);
        break;
    case H_DIR:
        moy_os_free(((dir_t *)h->ptr)->names);
        moy_os_free(h->ptr);
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
            moy_os_u_unmap(p, h->ptr, true);
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
uint32_t moy_os_handle_id(const moy_os_proc_t *p, int i) {
    return (uint32_t)p->h[i].gen << 8 | (uint32_t)(i + 1);
}

int moy_os_handle_of(const moy_os_proc_t *p, uint32_t id, uint8_t kind) {
    uint32_t i = (id & 0xffu) - 1u;
    if (i >= MOY_OS_HANDLES || p->h[i].kind != kind || moy_os_handle_id(p, (int)i) != id) {
        return -1;
    }
    return (int)i;
}

// -- the session's calls ------------------------------------------------------------

// fn(arg) on the task that may touch the session (the command's waiting
// task): 0, or -1 when the process has none or is ending.
int moy_os_on_session(thr_t *t, void (*fn)(void *arg), void *arg) {
    moy_os_proc_t *p = t->p;
    if (p->s == NULL) {
        return -1;
    }
    moy_os_sem_take(&p->hop_lock, -1);
    if (p->ending) {
        moy_os_sem_give(&p->hop_lock);
        return -1;
    }
    p->hop_arg = arg;
    p->hop_fn = fn;
    moy_os_sem_give(&p->wake);
    while (!moy_os_sem_take(&p->hop_done, MOY_OS_SLICE_US)) {
        if (p->ending && p->hop_fn == fn) {
            p->hop_fn = NULL;
            moy_os_sem_give(&p->hop_lock);
            return -1;
        }
    }
    moy_os_sem_give(&p->hop_lock);
    return 0;
}

// -- the rows the role door serves -------------------------------------------------
//
// An app call (docs/app_api_v1.md's call table) is the role table's row for the
// process's app run, its fields packed as the door takes them
// (native/moy_app/moy_app.h's moy_app_role): run on the VM's task -- a
// command's through the task that serves its session, a service's as a hop
// on the VM's queue (moy_vmq.h), which the scheduler's poll serves -- a
// read of what the look and the clipboard write where it is called, under the
// state's seqlock, or a row over the kernel's own locked state (its queues,
// timers and log, the store and the settings: the how "here") run where it is
// called, and hopped only where it answers that it needs the VM. Neither a
// hop's wait nor a here row's time is its wake's cost; a queue that is full
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

// fn(arg) on the VM's task for service `p`, the calling thread waiting until
// it has run (moy_vmq_hop): its wait is moved out of the wake that made it.
// False when the queue is full, fn not run.
static bool vm_hop(moy_os_proc_t *p, void (*fn)(void *), void *arg) {
    uint32_t waited = 0;
    moy_os_lock();
    p->hopping++;
    moy_os_unlock();
    int rc = moy_vmq_hop(p->pid, fn, arg, &waited);
    moy_os_lock();
    p->hopping--;
    if (p->woke_us != 0) {
        p->woke_us += waited;
    } else {
        p->started_us += waited;
    }
    moy_os_unlock();
    return rc == MOY_VMQ_OK;
}

// A row answered on the module's own thread: its time is the console's (the
// store's lock and its medium), moved out of the wake that made it as a
// hop's wait is.
static void here_role(moy_os_proc_t *p, role_call_t *c) {
    moy_os_lock();
    p->here++;
    moy_os_unlock();
    int64_t t0 = moy_now_us();
    c->r = moy_app_role_here(c->run->a, c->run->g, c->row, c->arg, c->n, c->out, c->cap);
    int64_t took = moy_now_us() - t0;
    moy_os_lock();
    p->here--;
    if (p->woke_us != 0) {
        p->woke_us += took;
    } else {
        p->started_us += took;
    }
    moy_os_unlock();
}

// A row the door serves, hopped while no VM runs, waits for the VM.
static void role_body(void *arg) {
    role_call_t *c = arg;
    c->r = moy_app_role(c->run->a, c->run->g, c->row, c->arg, c->n, c->out, c->cap);
    if (c->r == -MOY_APP_NEEDS_VM) {
        moy_vmq_defer();
    }
}

static void put32(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
    q[2] = (uint8_t)(v >> 16);
    q[3] = (uint8_t)(v >> 24);
}

// The errno of a door's negative answer (native/os_calls.json's errors).
static int role_errno(int32_t r) {
    switch (-r) {
#define X(code, eno) case MOY_APP_##code: return E##eno;
        MOY_APP_ERRNO(X)
#undef X
        default:
            return role_errno(-MOY_APP_FALLBACK);
    }
}

// The errno of an event queue's code (native/os_calls.json's evq_errors).
static int evq_errno(int rc) {
    switch (rc) {
#define X(code, eno, app) case MOY_EVQ_##code: return E##eno;
        MOY_EVQ_CODES(X)
#undef X
        default:
            return EINVAL;
    }
}

#define MOY_OS_CALL_ARGS 8u          // a row's registers bound its fields

int32_t moy_os_call(uint32_t row, int how, const moy_os_arg_t *a, int n, void *out, size_t cap) {
    thr_t *t = moy_os_enter();
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
                       : a[i].p != NULL ? moy_os_text_len(t, a[i].p, SIZE_MAX) : 0u;
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
            buf = moy_os_alloc(len);
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
            if (how == MOY_OS_HERE) {
                here_role(p, &c);
            }
            if (how == MOY_OS_HERE && c.r != -MOY_APP_NEEDS_VM) {
                // answered on the module's own thread
            } else if (how == MOY_OS_SEQ) {
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
            } else if (moy_os_on_session(t, role_body, &c) != 0) {
                c.r = -MOY_APP_NEEDS_VM;
            }
            r = c.r;
            if (buf != stack) {
                moy_os_free(buf);
            }
        }
    }
    moy_os_leave(t);
    if (r < 0) {
        moy_os_errno_set(role_errno(r));
        return -1;
    }
    return r;
}

// -- the rows: the process ---------------------------------------------------------

void moy_os_exit(int code) {
    moy_os__exit(code);
}

void moy_os__exit(int code) {
    thr_t *t = moy_os_enter();
    if (t == NULL) {
        abort();
    }
    moy_os_proc_t *p = t->p;
    const bool service = p->service;
    moy_os_lock();
    if (!p->main_done) {
        p->code = code & 0xff;
        p->main_done = 1;
    }
    p->ending = 1;
    moy_os_unlock();
    if (service) {
        moy_evq_want(MOY_EVQ_WANT_POLL);
    }
    thr_end(t);
}

int *moy_os___errno(void) {
    thr_t *t = self_thr();
    return t != NULL ? t->errp : &moy_os_g_err_none;
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
int moy_os_doze(thr_t *t, int64_t us) {
    int64_t end = moy_now_us() + us;
    for (int64_t left = us; left > 0; left = end - moy_now_us()) {
        if (moy_os_ending(t)) {
            return EINTR;
        }
        int64_t d = left < MOY_OS_SLICE_US ? left : MOY_OS_SLICE_US;
#ifdef MOY_ON_BOARD
        vTaskDelay((TickType_t)((d + 999) / 1000 / portTICK_PERIOD_MS + 1));
#else
        struct timespec ts = { (time_t)(d / 1000000), (long)(d % 1000000) * 1000 };
        nanosleep(&ts, NULL);
#endif
    }
    return 0;
}

int moy_os_nanosleep(const struct timespec *req, struct timespec *rem) {
    thr_t *t = moy_os_enter();
    int r = 0;
    if (req == NULL || req->tv_nsec < 0 || req->tv_nsec >= 1000000000) {
        moy_os_errno_set(EINVAL);
        r = -1;
    } else if (moy_os_doze(t, (int64_t)req->tv_sec * 1000000 + req->tv_nsec / 1000) != 0) {
        if (rem != NULL) {
            rem->tv_sec = 0;
            rem->tv_nsec = 0;
        }
        moy_os_errno_set(EINTR);
        r = -1;
    }
    moy_os_leave(t);
    return r;
}

int moy_os_usleep(useconds_t us) {
    thr_t *t = moy_os_enter();
    int r = moy_os_doze(t, (int64_t)us);
    if (r) {
        moy_os_errno_set(r);
    }
    moy_os_leave(t);
    return r ? -1 : 0;
}

unsigned moy_os_sleep(unsigned s) {
    thr_t *t = moy_os_enter();
    int r = moy_os_doze(t, (int64_t)s * 1000000);
    moy_os_leave(t);
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
    thr_t *t = moy_os_enter();
    int r = EPERM;
    if (t != NULL && start != NULL && !t->p->ending) {
        moy_os_proc_t *p = t->p;
        moy_os_lock();
        // A finished detached thread's slot is reused.
        for (uint32_t i = 1; i < MOY_OS_THREADS; i++) {
            if (p->thr[i].state == T_DONE && p->thr[i].detached && !p->thr[i].joined) {
                uint16_t gen = p->thr[i].gen;
                moy_os_thr_reap(&p->thr[i]);
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
            moy_os_unlock();
            r = moy_os_thr_spawn(n);
            moy_os_lock();
            if (r == 0 && thread != NULL) {
                *thread = (pthread_t)thr_id(p, n);
            }
        }
        moy_os_unlock();
    }
    moy_os_leave(t);
    return r;
}

// A join claims its thread under the lock: no second join, detach or reap
// can take the record it waits on.
int moy_os_pthread_join(pthread_t thread, void **out) {
    thr_t *t = moy_os_enter();
    int r = ESRCH;
    bool mine = false;
    thr_t *o = NULL;
    if (t != NULL) {
        moy_os_lock();
        o = thr_of(t->p, (uint32_t)thread);
        if (o == t && o != NULL) {
            r = EDEADLK;
        } else if (o != NULL && (o == &t->p->thr[0] || o->detached || o->joined)) {
            r = EINVAL;
        } else if (o != NULL) {
            o->joined = 1;
            mine = true;
        }
        moy_os_unlock();
    }
    if (mine) {
        while (o->state != T_DONE) {
            if (moy_os_ending(t)) {
                break;
            }
            moy_os_sem_take(&o->done, MOY_OS_SLICE_US);
        }
        if (o->state == T_DONE) {
            if (out != NULL) {
                *out = o->out;
            }
            moy_os_lock();
            uint16_t gen = o->gen;
            moy_os_thr_reap(o);
            o->gen = gen;
            moy_os_unlock();
            r = 0;
        } else {
            r = EINTR;
        }
    }
    moy_os_leave(t);
    return r;
}

int moy_os_pthread_detach(pthread_t thread) {
    thr_t *t = moy_os_enter();
    int r = ESRCH;
    if (t != NULL) {
        moy_os_lock();
        thr_t *o = thr_of(t->p, (uint32_t)thread);
        if (o != NULL && (o->joined || o->detached)) {
            r = EINVAL;
        } else if (o != NULL) {
            o->detached = 1;
            r = 0;
        }
        moy_os_unlock();
    }
    moy_os_leave(t);
    return r;
}

pthread_t moy_os_pthread_self(void) {
    thr_t *t = self_thr();
    return t != NULL ? (pthread_t)thr_id(t->p, t) : (pthread_t)0;
}

void moy_os_pthread_exit(void *out) {
    thr_t *t = moy_os_enter();
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
    moy_os_lock();
    int h = moy_os_handle_of(t->p, *mutex_word(m), H_MUTEX);
    if (h < 0 && make) {
        sem_t_ *s = moy_os_alloc(sizeof *s);
        if (s != NULL) {
            moy_os_sem_init(s, true);
            h = moy_os_handle_new(t->p, H_MUTEX, s, -1);
            if (h < 0) {
                moy_os_sem_fini(s);
                moy_os_free(s);
            } else {
                *mutex_word(m) = moy_os_handle_id(t->p, h);
            }
        }
    }
    sem_t_ *out = NULL;
    if (h >= 0) {
        out = t->p->h[h].ptr;
        t->p->h[h].users++;
        *k = h;
    }
    moy_os_unlock();
    return out;
}

static void mutex_let(thr_t *t, int k) {
    if (k >= 0) {
        moy_os_lock();
        t->p->h[k].users--;
        moy_os_unlock();
    }
}

int moy_os_pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *attr) {
    (void)attr;
    thr_t *t = moy_os_enter();
    if (m != NULL) {
        memset(m, 0, sizeof *m);
    }
    int k;
    sem_t_ *s = mutex_of(t, m, true, &k);
    mutex_let(t, k);
    moy_os_leave(t);
    return s != NULL ? 0 : EAGAIN;
}

int moy_os_pthread_mutex_destroy(pthread_mutex_t *m) {
    thr_t *t = moy_os_enter();
    int r = EINVAL;
    if (t != NULL && m != NULL) {
        moy_os_lock();
        int h = moy_os_handle_of(t->p, *mutex_word(m), H_MUTEX);
        if (h >= 0 && t->p->h[h].users != 0) {
            r = EBUSY;
        } else if (h >= 0) {
            moy_os_handle_drop(t->p, (uint32_t)h);
            *mutex_word(m) = 0;
            r = 0;
        }
        moy_os_unlock();
    }
    moy_os_leave(t);
    return r;
}

int moy_os_pthread_mutex_lock(pthread_mutex_t *m) {
    thr_t *t = moy_os_enter();
    int k;
    sem_t_ *s = mutex_of(t, m, true, &k);
    int r = s != NULL ? 0 : EINVAL;
    while (s != NULL && !moy_os_sem_take(s, MOY_OS_SLICE_US)) {
        if (moy_os_ending(t)) {
            r = EINTR;
            break;
        }
    }
    mutex_let(t, k);
    moy_os_leave(t);
    return r;
}

int moy_os_pthread_mutex_trylock(pthread_mutex_t *m) {
    thr_t *t = moy_os_enter();
    int k;
    sem_t_ *s = mutex_of(t, m, true, &k);
    int r = s == NULL ? EINVAL : moy_os_sem_take(s, 0) ? 0 : EBUSY;
    mutex_let(t, k);
    moy_os_leave(t);
    return r;
}

int moy_os_pthread_mutex_unlock(pthread_mutex_t *m) {
    thr_t *t = moy_os_enter();
    int k;
    sem_t_ *s = mutex_of(t, m, false, &k);
    if (s != NULL) {
        moy_os_sem_give(s);
    }
    mutex_let(t, k);
    moy_os_leave(t);
    return s != NULL ? 0 : EINVAL;
}

// -- the rows: Moybyte's -------------------------------------------------------------

int moy_os_moy_log(int level, const char *text) {
    thr_t *t = moy_os_enter();
    if (text != NULL) {
        size_t n = moy_os_text_len(t, text, MOY_LOG_TEXT_MAX);
        moy_log_write(level < 0 || level >= MOY_LOG_LEVELS ? MOY_LOG_INFO : level,
                      t != NULL ? t->p->pid : MOY_LOG_KERNEL, text,
                      n < MOY_LOG_TEXT_MAX ? n : MOY_LOG_TEXT_MAX);
    }
    moy_os_leave(t);
    return text != NULL ? 0 : -1;
}

int moy_os_moy_term_size(int *cols, int *rows) {
    thr_t *t = moy_os_enter();
    fcall_t c = { .op = F_TERM };
    long r = moy_os_fcall(t, &c);
    if (r >= 0) {
        if (cols != NULL) {
            *cols = (int)(r >> 16);
        }
        if (rows != NULL) {
            *rows = (int)(r & 0xffff);
        }
    }
    moy_os_leave(t);
    return r >= 0 ? 0 : -1;
}

// -- the rows: a game's screen -------------------------------------------------------
//
// A game's screen is its pid's slot (native/moy_kernel/moy_screen_slot.h),
// opened with the game and closed at its stop: moy_present copies the
// module's pixels in, waiting while the console holds the last frame, and
// moy_ui_tell leaves the toolkit's answers beside them.

int moy_os_moy_screen(int *w, int *h) {
    thr_t *t = moy_os_enter();
    int sw = 0, sh = 0, depth = 0;
    int r = -1;
    if (t == NULL || !t->p->game || moy_screen_slot_size(t->p->pid, &sw, &sh, &depth) != 0) {
        moy_os_errno_set(ENODEV);
    } else {
        if (w != NULL) {
            *w = sw;
        }
        if (h != NULL) {
            *h = sh;
        }
        r = 0;
    }
    moy_os_leave(t);
    return r;
}

int moy_os_moy_present(const void *px, size_t n) {
    thr_t *t = moy_os_enter();
    if (t == NULL || !t->p->game) {
        moy_os_errno_set(ENODEV);
        moy_os_leave(t);
        return -1;
    }
    int r = moy_screen_slot_present(t->p->pid, px, n);
    if (r != 0) {
        // a game's slot gone is its stop
        moy_os_errno_set(r == -ENODEV ? EINTR : -r);
        r = -1;
    }
    moy_os_leave(t);
    return r;
}

// Up to n bytes of the data the console handed the game at its start
// (moy_os_game), from offset off into dst: how many, 0 past its end.
int moy_os_moy_game_data(size_t off, void *dst, size_t n) {
    thr_t *t = moy_os_enter();
    int r = -1;
    if (t == NULL || !t->p->game) {
        moy_os_errno_set(ENODEV);
    } else if (dst == NULL && n > 0) {
        moy_os_errno_set(EFAULT);
    } else {
        size_t have = t->p->data_n;
        size_t k = off < have ? have - off : 0;
        if (k > n) {
            k = n;
        }
        if (k > 0) {
            memcpy(dst, t->p->data + off, k);
        }
        r = (int)k;
    }
    moy_os_leave(t);
    return r;
}

// The toolkit's answers after a frame (moy_screen_slot.h, THE TOOLKIT'S
// ANSWERS), into the game's slot for the console's input owner to read.
int moy_os_moy_ui_tell(uint32_t answers) {
    thr_t *t = moy_os_enter();
    int r = t != NULL && t->p->game ? moy_screen_slot_tell(t->p->pid, answers) : -ENODEV;
    if (r != 0) {
        moy_os_errno_set(ENODEV);
        r = -1;
    }
    moy_os_leave(t);
    return r;
}

// The toolkit's theme as a block (moy_ui.h's moy_ui_theme_pull), which a copy
// of the toolkit a module links installs; weak, so an image without the
// toolkit answers none.
#if __has_include("moy_ui.h")
#include "moy_ui.h"
#else
uint32_t moy_ui_theme_pull(uint32_t have, void *buf, size_t cap);
#endif
#pragma weak moy_ui_theme_pull

uint32_t moy_os_moy_ui_pull(uint32_t have, void *buf, size_t cap) {
    thr_t *t = moy_os_enter();
    uint32_t g = moy_ui_theme_pull != NULL ? moy_ui_theme_pull(have, buf, cap) : 0;
    moy_os_leave(t);
    return g;
}

// A font the toolkit's theme names (moy_ui.h's moy_ui_theme_font): the bytes
// a copy loads as its own.
#if __has_include("moy_ui.h")
#include "moy_ui.h"
#else
uint32_t moy_ui_theme_font(uint32_t gen, uint32_t font, void *buf, size_t cap);
#endif
#pragma weak moy_ui_theme_font

uint32_t moy_os_moy_ui_font_pull(uint32_t gen, uint32_t font, void *buf, size_t cap) {
    thr_t *t = moy_os_enter();
    uint32_t n = moy_ui_theme_font != NULL ? moy_ui_theme_font(gen, font, buf, cap) : 0;
    moy_os_leave(t);
    return n;
}

// -- the rows: the event descriptor ------------------------------------------------
//
// The process's event queue (moy_evq.h), read as descriptor MOY_EVQ_FD: by
// read (evq_read), poll and select (poll_in), on the calling thread.

// A record of `p`'s queue into buf: its bytes, 0 once its stop hung the
// descriptor up, or -1 with errno.
long moy_os_evq_read(moy_os_proc_t *p, void *buf, size_t n) {
    if (p->hup) {
        return 0;
    }
    size_t len = 0;
    int rc = moy_evq_read(p->pid, buf, n, &len);
    if (rc != MOY_EVQ_OK) {
        moy_os_errno_set(evq_errno(rc));
        return -1;
    }
    p->events++;
    return (long)len;
}

int moy_os_moy_send(const char *app, const char *name, const char *args) {
    thr_t *t = moy_os_enter();
    int r = -1;
    // The texts are measured inside the module's region and the kernel copies
    // them: the module's other threads may rewrite them meanwhile.
    size_t an = app != NULL ? moy_os_text_len(t, app, MOY_EVQ_APP) : MOY_EVQ_APP;
    size_t nn = name != NULL ? moy_os_text_len(t, name, MOY_EVQ_NAME) : MOY_EVQ_NAME;
    size_t n = args != NULL ? moy_os_text_len(t, args, SIZE_MAX) : 0;
    if (n == SIZE_MAX) {
        an = MOY_EVQ_APP;           // no end inside its memory: refused
        n = 0;
    }
    uint32_t took = 0;
    int rc = t == NULL ? MOY_EVQ_OK
             : moy_evq_send_text(t->p->pid, app, an, name, nn, args, n, &took);
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (rc != MOY_EVQ_OK) {
        moy_os_errno_set(evq_errno(rc));
    } else {
        r = (int)took;
    }
    moy_os_leave(t);
    return r;
}

int moy_os_moy_on(const char *name, int on) {
    thr_t *t = moy_os_enter();
    int r = -1;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (name == NULL || moy_os_text_len(t, name, MOY_EVQ_NAME) - 1u >= MOY_EVQ_NAME - 1u) {
        moy_os_errno_set(EINVAL);
    } else {
        char name_in[MOY_EVQ_NAME];
        size_t n = moy_os_text_len(t, name, MOY_EVQ_NAME);
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
    moy_os_leave(t);
    return r;
}

// -- the rows: memory -----------------------------------------------------------------

#ifdef MOY_OS_UMODE
// A mapping of a U-mode process: zeroed, 128-aligned, a window of its own.
void *moy_os_u_map(moy_os_proc_t *p, size_t len) {
    if (len > 0x7fffff80u) {
        return NULL;
    }
    uint32_t n = MOY_U_UP(len);
    map_t *m = moy_os_alloc(sizeof *m);
    void *mem = heap_caps_aligned_alloc(MOY_U_ALIGN, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (m == NULL || mem == NULL
        || moy_u_add(p->u, (uint32_t)mem, (uint32_t)mem + n, MOY_U_R | MOY_U_W) != 0) {
        moy_os_free(m);
        heap_caps_free(mem);
        return NULL;
    }
    memset(mem, 0, n);
    m->len = len;
    m->mem = mem;
    moy_os_lock();
    m->next = p->maps;
    p->maps = m;
    p->mapped += len;
    moy_os_unlock();
    return mem;
}

// Mapping `addr` let go; `locked` when the caller holds the table's lock.
bool moy_os_u_unmap(moy_os_proc_t *p, void *addr, bool locked) {
    map_t *m = NULL;
    if (!locked) {
        moy_os_lock();
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
        moy_os_unlock();
    }
    if (m == NULL) {
        return false;
    }
    moy_u_drop(p->u, (uint32_t)m->mem);
    u_free(p, m->mem);
    moy_os_free(m);
    return true;
}
#endif

void *moy_os_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off) {
    (void)addr;
    (void)prot;
    (void)off;
    thr_t *t = moy_os_enter();
    void *out = MAP_FAILED;
    if (t == NULL) {
        moy_os_errno_set(EPERM);
    } else if (len == 0 || fd != -1 || !(flags & MAP_ANONYMOUS) || (flags & MAP_SHARED)) {
        moy_os_errno_set(len == 0 ? EINVAL : ENODEV);
#ifdef MOY_OS_UMODE
    } else if (t->p->u != NULL) {
        out = moy_os_u_map(t->p, len);
        if (out == NULL) {
            out = MAP_FAILED;
            moy_os_errno_set(ENOMEM);
        }
#endif
    } else {
        map_t *m = len < (size_t)-1 - MAP_HDR ? moy_os_alloc(MAP_HDR + len) : NULL;
        if (m == NULL) {
            moy_os_errno_set(ENOMEM);
        } else {
            m->len = len;
            moy_os_lock();
            m->next = t->p->maps;
            t->p->maps = m;
            t->p->mapped += len;
            moy_os_unlock();
            out = (char *)m + MAP_HDR;
        }
    }
    moy_os_leave(t);
    return out;
}

int moy_os_munmap(void *addr, size_t len) {
    (void)len;
    thr_t *t = moy_os_enter();
    map_t *m = NULL;
#ifdef MOY_OS_UMODE
    if (t != NULL && t->p->u != NULL) {
        int r = moy_os_u_unmap(t->p, addr, false) ? 0 : -1;
        if (r != 0) {
            moy_os_errno_set(EINVAL);
        }
        moy_os_leave(t);
        return r;
    }
#endif
    if (t != NULL) {
        moy_os_lock();
        for (map_t **at = &t->p->maps; *at != NULL; at = &(*at)->next) {
            if ((char *)*at + MAP_HDR == (char *)addr) {
                m = *at;
                *at = m->next;
                t->p->mapped -= m->len;
                break;
            }
        }
        moy_os_unlock();
    }
    if (m != NULL) {
        moy_os_free(m);
    } else {
        moy_os_errno_set(EINVAL);
    }
    moy_os_leave(t);
    return m != NULL ? 0 : -1;
}

// -- a game -----------------------------------------------------------------------------
//
// A game's record is freed only on the console's task (moy_os_game_stop, a
// failed start), the task every call below runs on, so it stays readable
// past the lock there.

static moy_os_proc_t *game_of(uint32_t pid) {
    moy_os_proc_t *p = moy_os_proc_of(pid);
    return p != NULL && p->game ? p : NULL;
}

int moy_os_game_frame(uint32_t pid, uint32_t held, uint32_t pressed, uint32_t ms,
                      uint32_t max_us, const int32_t *in, int n_in) {
    moy_os_proc_t *p = game_of(pid);
    if (p == NULL) {
        return -1;
    }
    bool done = p->main_done;
    int r = moy_screen_slot_frame(pid, held, pressed, ms, in, n_in,
                                  done ? 0u : (max_us + 999u) / 1000u);
    if (r < 0) {
        return -1;
    }
    if (r & MOY_SCREEN_SLOT_SHOWN) {
        return r;
    }
    return p->main_done ? MOY_OS_ENDED : r;
}

const uint8_t *moy_os_game_screen(uint32_t pid, int *w, int *h, int *depth) {
    return game_of(pid) != NULL ? moy_screen_slot_pixels(pid, w, h, depth) : NULL;
}

int moy_os_game_stop(uint32_t pid) {
    moy_os_proc_t *p = game_of(pid);
    return p != NULL ? moy_os_loop_stop(p) : -1;
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
    moy_os_lock();
    moy_os_fault_t *r = &g_faults[g_nfaults++ % MOY_OS_FAULTS];
    r->pid = p->pid;
    r->cause = cause;
    r->pc = at;
    r->addr = addr;
    snprintf(r->name, sizeof r->name, "%s", name);
    moy_os_unlock();
    moy_os_log_line(p, MOY_LOG_ERR, text);
    if (!p->service) {
        moy_os_write(2, text, strlen(text));
    }
    moy_os_lock();
    if (!p->main_done) {
        p->code = 128 + sig;
        p->main_done = 1;
    }
    p->ending = 1;
    moy_os_unlock();
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
    moy_os_lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (moy_os_g_proc[i] != NULL && moy_os_g_proc[i]->pid == pid) {
            p = moy_os_g_proc[i];
        }
    }
    if (p == NULL) {
        moy_os_unlock();
        return -1;
    }
    memset(out, 0, sizeof *out);
    out->pid = p->pid;
    out->threads = (uint32_t)moy_os_live_threads(p);
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
    moy_os_unlock();
    return 0;
}

void moy_os_stats(moy_os_stats_t *out) {
    *out = moy_os_g_stats;
}

uint32_t moy_os_stack_low(void) {
    uint32_t low = UINT32_MAX;
#ifdef MOY_ON_BOARD
    moy_os_lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = moy_os_g_proc[i];
        for (uint32_t k = 0; p != NULL && k < MOY_OS_THREADS; k++) {
            if (p->thr[k].state == T_LIVE && p->thr[k].task != NULL) {
                uint32_t w = (uint32_t)uxTaskGetStackHighWaterMark(p->thr[k].task);
                low = w < low ? w : low;
            }
        }
    }
    moy_os_unlock();
#endif
    return low;
}
