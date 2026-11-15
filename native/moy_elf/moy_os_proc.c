// moy_os_proc: a native module's process, from its start to its reaping (moy_os.h).
#include "moy_os_int.h"

// -- processes --------------------------------------------------------------------------

// The S3s: core 1 only (tiers.md), at the VM's own priority there, so a
// module that never calls a row still shares the core with the task that
// stops it. The P4s: core 0 above the idle work, as a Lua service runs.
void moy_os_default_caps(moy_os_caps_t *caps) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    caps->core = 1;
    caps->prio = 1;
#elif defined(MOY_ON_BOARD)
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
    moy_os_sem_fini(&p->wake);
    moy_os_sem_fini(&p->hop_done);
    moy_os_sem_fini(&p->hop_lock);
    moy_os_sem_fini(&p->stopped);
    p->magic = 0;
    moy_os_free(p);
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
    moy_os_u_scrub_free(stack);
    if (u.fault != 0) {
        moy_os_log_line(p, MOY_LOG_ERR, "the native module's entry faulted");
        return 0;
    }
    return hooks;
}
#endif

static moy_os_proc_t *proc_new(const moy_os_module_t *m, const moy_os_caps_t *caps, uint32_t pid,
                               bool service, char *err, size_t errlen) {
    moy_os_lock();
    uint32_t slot = MOY_OS_PROCS;
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (moy_os_g_proc[i] == NULL) {
            slot = i;
            break;
        }
    }
    moy_os_unlock();
    if (slot == MOY_OS_PROCS) {
        snprintf(err, errlen, "too many native processes");
        return NULL;
    }
    moy_os_proc_t *p = moy_os_alloc(sizeof *p);
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
    moy_os_sem_init(&p->wake, false);
    moy_os_sem_init(&p->hop_done, false);
    moy_os_sem_init(&p->hop_lock, true);
    moy_os_sem_init(&p->stopped, false);
#ifdef MOY_OS_UMODE
    if (m->ulo != 0) {
        p->u = moy_os_alloc(sizeof *p->u);
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
    moy_os_lock();
    moy_os_g_proc[slot] = p;
    moy_os_unlock();
    moy_os_g_stats.started++;
#ifdef MOY_OS_UMODE
#endif
    return p;
}

int moy_os_live_threads(const moy_os_proc_t *p) {
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
    moy_os_lock();
    p->ending = 1;
    moy_os_unlock();
    moy_evq_kick(p->pid);
    moy_evq_unwait(p->pid);             // a task deleted below may be its waiter
    if (p->game) {
        moy_screen_slot_close(p->pid);  // a present waiting answers EINTR
    }
    moy_os_sem_give(&p->hop_done);
    int64_t end = moy_now_us() + MOY_OS_REAP_US;
    while (moy_os_live_threads(p) > 0) {
        bool inside = false;
        for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
            inside |= p->thr[k].state == T_LIVE && p->thr[k].in_row > 0;
        }
        if (!inside) {
#ifdef MOY_ON_BOARD
            // Every live thread is in the module's code: deleted there. Each
            // is held first and looked at again, held: one that has gone into
            // kernel code since is let go to leave at its row's end.
            moy_os_lock();
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
                moy_os_self_clear(t);
                vTaskDeleteWithCaps(t->task);
#ifdef MOY_OS_UMODE
                moy_os_u_thread_end(t);
#endif
                moy_os_sem_fini(&t->done);
                memset(t, 0, sizeof *t);
                moy_os_g_stats.killed++;
            }
            moy_os_unlock();
            continue;
#endif
        }
        if (moy_now_us() >= end) {
            moy_os_g_stats.leaked++;
            moy_os_log_line(p, MOY_LOG_ERR, "a native thread would not end; its process is left running");
            return false;
        }
        // A thread in a row leaves at its end; one serving the session gets its
        // answer refused, and a service's hop on the VM's queue is run (its
        // end is the VM's task's: moy_os_stop, moy_os_reap).
        if (p->hop_fn != NULL) {
            p->hop_fn = NULL;
            moy_os_sem_give(&p->hop_done);
        }
        if (p->service && p->hopping) {
            moy_vmq_serve();
        }
        moy_os_sem_take(&p->stopped, MOY_OS_SLICE_US);
    }
    moy_evq_unwait(p->pid);             // one may have waited again on its way out
    moy_os_lock();
    for (uint32_t k = 0; k < MOY_OS_THREADS; k++) {
        if (p->thr[k].state == T_DONE) {
            moy_os_thr_reap(&p->thr[k]);
        }
    }
    uint32_t swept = 0;
    for (uint32_t i = 0; i < MOY_OS_HANDLES; i++) {
        if (p->h[i].kind != H_FREE) {
            moy_os_handle_drop(p, i);
            swept++;
        }
    }
    moy_os_g_stats.swept += swept;
    map_t *maps = p->maps;
    p->maps = NULL;
    p->mapped = 0;
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (moy_os_g_proc[i] == p) {
            moy_os_g_proc[i] = NULL;
        }
    }
    moy_os_unlock();
    while (maps != NULL) {
        map_t *m = maps;
        maps = m->next;
#ifdef MOY_OS_UMODE
        moy_os_u_scrub_free(m->mem);
#elif defined(MOY_ON_BOARD)
        heap_caps_free(m->mem);
#endif
        moy_os_free(m);
    }
    if (p->net_held && moy_os_g_net != NULL) {
        moy_os_g_net(moy_os_g_net_ctx, p->pid, 0);
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
        moy_sh_prog_fini(&p->prog, moy_os_free);
    } else if (p->service) {
        moy_sh_prog_flush(&p->prog, 0);
        moy_sh_prog_flush(&p->prog, 1);
    }
    moy_os_free(p->argv);
    moy_os_free(p->env);
#ifdef MOY_OS_UMODE
    while (p->retired != NULL) {
        retired_t *r = p->retired;
        p->retired = r->next;
        moy_os_u_scrub_free(r->mem);
        moy_os_free(r);
    }
    moy_os_free(p->u);
#endif
    moy_os_sem_fini(&p->wake);
    moy_os_sem_fini(&p->hop_done);
    moy_os_sem_fini(&p->hop_lock);
    moy_os_sem_fini(&p->stopped);
    p->magic = 0;
    moy_os_free(p);
    return true;
}

static int env_add(void *arg, const char *kv) {
    moy_os_proc_t *p = arg;
    size_t have = 0;
    for (char *e = p->env; e != NULL && *e; e += strlen(e) + 1) {
        have = (size_t)(e - p->env) + strlen(e) + 1;
    }
    size_t n = strlen(kv) + 1;
    char *g = moy_os_alloc(have + n + 1);
    if (g == NULL) {
        return ENOMEM;
    }
    if (p->env != NULL) {
        memcpy(g, p->env, have);
        moy_os_free(p->env);
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
    p->argv = moy_os_alloc((size_t)(argc + 1) * sizeof(char *) + args_n);
    bool ok = p->argv != NULL
              && moy_sh_prog_init(&p->prog, s, roots, bg_pid, budget_ms, moy_now_us(), moy_os_alloc) == 0
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
        ok = moy_os_thr_spawn(&p->thr[0]) == 0;
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
    moy_os_sem_take(&p->wake, us);
    void (*fn)(void *) = p->hop_fn;
    if (fn != NULL) {
        p->hop_fn = NULL;
        fn(p->hop_arg);
        moy_os_sem_give(&p->hop_done);
    }
    moy_os_task_feed();
}

int moy_os_command_step(uint32_t pid, uint32_t max_us) {
    moy_os_proc_t *p = moy_os_proc_of(pid);
    if (p == NULL || p->service) {
        return MOY_OS_REFUSED;
    }
    if (moy_sh_prog_step(&p->prog, max_us, moy_now_us, command_done, command_serve, p)
        == MOY_SH_PROG_RUNNING) {
        return MOY_OS_RUNNING;
    }
    return command_end(p);
}

int moy_os_command_stop(uint32_t pid) {
    moy_os_proc_t *p = moy_os_proc_of(pid);
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

// A service's start, or with a screen of w x h (w > 0) pixels of `depth`
// bytes a game's: the process, its main running on its own task, or NULL
// with the reason in err.
static moy_os_proc_t *loop_start(const moy_os_module_t *m, const moy_os_caps_t *caps,
                                 uint32_t pid, const char *id, int w, int h, int depth,
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
    char **argv = moy_os_alloc(2 * sizeof(char *) + k);
    if (argv == NULL) {
        snprintf(err, errlen, "out of memory");
        proc_end(p);
        return NULL;
    }
    argv[0] = memcpy((char *)(argv + 2), name, k);
    argv[1] = NULL;
    moy_os_lock();                             // whole before moy_os_service_of reads it
    p->argv = argv;
    moy_os_unlock();
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
        int rc = moy_screen_slot_open(pid, w, h, depth);
        if (rc != 0) {
            snprintf(err, errlen, rc == -ENOMEM ? "out of memory" : "no screen for it");
            proc_end(p);
            return NULL;
        }
        p->game = 1;
        if (moy_evq_listen(pid, MOY_OS_FRAME, true) != MOY_EVQ_OK
            || (depth == 2 && moy_evq_listen(pid, MOY_OS_PASTE, true) != MOY_EVQ_OK)) {
            snprintf(err, errlen, "its event queue hears no frames");
            proc_end(p);
            return NULL;
        }
    }
    p->thr[0].p = p;
    p->thr[0].gen = 1;
    p->started_us = moy_now_us();
    if (moy_os_thr_spawn(&p->thr[0]) != 0) {
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
    return loop_start(m, caps, pid, id, 0, 0, 0, run, err, errlen);
}

moy_os_proc_t *moy_os_game(const moy_os_module_t *m, const moy_os_caps_t *caps, uint32_t pid,
                           const char *id, int w, int h, int depth, const moy_app_run_t *run,
                           char *err, size_t errlen) {
    if (w <= 0 || h <= 0 || depth < 1 || depth > 2) {
        snprintf(err, errlen, "no screen for it");
        if (m->unload != NULL) {
            m->unload(m->loaded);
        }
        return NULL;
    }
    // An app's thread runs the toolkit it links (sdk/include/moybyte/ui.h),
    // whose calls nest deeper than a game's: twice the default stack.
    moy_os_caps_t app;
    if (caps == NULL && depth == 2) {
        moy_os_default_caps(&app);
        app.stack *= 2;
        caps = &app;
    }
    return loop_start(m, caps, pid, id, w, h, depth, run, err, errlen);
}

// The record of `pid`, looked up under the lock: a command's record is freed
// by the task serving it, so only that task, or a caller under the lock,
// reads one past this.
moy_os_proc_t *moy_os_proc_of(uint32_t pid) {
    moy_os_proc_t *out = NULL;
    moy_os_lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        if (moy_os_g_proc[i] != NULL && moy_os_g_proc[i]->pid == pid) {
            out = moy_os_g_proc[i];
            break;
        }
    }
    moy_os_unlock();
    return out;
}

// The services' records, taken under the lock. A service's record is freed
// only on the VM's task (moy_os_reap, moy_os_stop, a failed start), which is
// the task that watches and reaps them, so they stay readable past it.
static uint32_t services(moy_os_proc_t **out) {
    uint32_t n = 0;
    moy_os_lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS; i++) {
        moy_os_proc_t *p = moy_os_g_proc[i];
        if (p != NULL && p->service && !p->game) {
            out[n++] = p;
        }
    }
    moy_os_unlock();
    return n;
}

static uint32_t service_find(const char *id, size_t n, bool term) {
    uint32_t pid = 0u;
    moy_os_lock();
    for (uint32_t i = 0; i < MOY_OS_PROCS && pid == 0u; i++) {
        moy_os_proc_t *p = moy_os_g_proc[i];
        if (p != NULL && p->service && !p->game && !p->hup && !p->ending && !p->reaped
            && p->argv != NULL && strncmp(p->argv[0], id, n) == 0 && p->argv[0][n] == 0) {
            pid = p->pid;
            if (term) {
                p->hup = 1;
            }
        }
    }
    moy_os_unlock();
    if (term && pid != 0u) {
        moy_evq_kick(pid);
    }
    return pid;
}

uint32_t moy_os_service_of(const char *id, size_t n) {
    return service_find(id, n, false);
}

uint32_t moy_os_service_term(const char *id, size_t n) {
    return service_find(id, n, true);
}

// THE WATCHDOG. Native code is not aborted where it stands by a timer: a
// wake that runs on is struck here, on the console's task, once for each
// MOY_SUPER_ABORT_US it runs without polling its descriptor again (the
// strikes its tier's abort would have charged), and its own poll charges it
// nothing more. A wake waiting on the VM's queue, or inside a row answered
// here, is not running. Where the
// strikes suspend it while its wake still runs, suspension cannot hold it,
// so it is killed (MOY_PROC_KILLED), as a stop past its time is; a service
// whose start runs MOY_OS_STOP_US without a first poll is killed too, as a
// Python service's start past the same limit is. Answers the reason it is
// killed, or NULL.
static const char *watch(moy_os_proc_t *p, int64_t now) {
    moy_os_lock();
    if (p->main_done || p->ending || p->polling || p->hopping || p->here) {
        moy_os_unlock();
        return NULL;
    }
    if (p->woke_us == 0) {
        bool late = now - p->started_us >= MOY_OS_STOP_US;
        moy_os_unlock();
        return late ? "its start ran past its time without a poll" : NULL;
    }
    int64_t ran = now - p->woke_us;
    bool due = ran >= (int64_t)MOY_SUPER_ABORT_US * (p->struck + 1);
    if (due) {
        p->struck++;
    }
    moy_os_unlock();
    if (!due) {
        return NULL;
    }
    moy_super_charge(p->pid, (uint32_t)ran, true);
    moy_os_lock();
    bool out = moy_super_held(p->pid) && !p->polling && !p->main_done;
    moy_os_unlock();
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
    moy_os_log_line(p, MOY_LOG_ERR, text);
    moy_os_lock();
    if (!p->main_done) {
        p->code = MOY_PROC_KILLED;
        p->main_done = 1;
    }
    moy_os_unlock();
    return true;
}

bool moy_os_watch(void) {
    bool live = false;
    int64_t now = moy_now_us();
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
    int64_t now = moy_now_us();
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

uint32_t moy_os_services_end(void) {
    moy_os_proc_t *svc[MOY_OS_PROCS];
    uint32_t pids[MOY_OS_PROCS];
    uint32_t n = services(svc), got = 0;
    for (uint32_t i = 0; i < n; i++) {
        pids[i] = svc[i]->reaped ? 0u : svc[i]->pid;
    }
    for (uint32_t i = 0; i < n; i++) {
        int code = pids[i] != 0u ? moy_os_stop(pids[i]) : -1;
        if (code >= 0) {
            char line[40];
            int k = snprintf(line, sizeof line, "exit %d (a soft reset)", code);
            moy_log_write(MOY_LOG_INFO, pids[i], line, (size_t)k);
            got++;
        }
    }
    return got;
}

// Service or game `p` stopped on the console's task (moy_os_stop).
int moy_os_loop_stop(moy_os_proc_t *p) {
    uint32_t pid = p->pid;
    p->hup = 1;
    if (p->game) {
        moy_screen_slot_close(pid);     // a present waiting answers EINTR
    }
    moy_evq_kick(pid);
    int64_t end = moy_now_us() + MOY_OS_STOP_US;
    while (!p->main_done && moy_now_us() < end) {
        if (p->hopping) {
            moy_vmq_serve();            // its hop is this task's to run
        }
        moy_os_sem_take(&p->stopped, p->hopping ? 1000 : MOY_OS_SLICE_US);
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
    return moy_os_loop_stop(p);
}
