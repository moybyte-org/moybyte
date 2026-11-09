// The volume seam's three backends (moy_vol.h has the contract).

// The lock's clock and recursive mutex are POSIX's, which a strict C99
// build on Linux leaves out unless asked.
#if defined(__linux__) && !defined(_GNU_SOURCE) && !defined(_XOPEN_SOURCE)
#define _XOPEN_SOURCE 700
#endif

#include <string.h>

#include "moy_vol.h"

#if MOY_VOL_FAT
#ifndef FFCONF_H
#define FFCONF_H "lib/oofatfs/ffconf.h"
#endif
#include "lib/oofatfs/ff.h"
// An ESP-IDF image links IDF's own fatfs beside oofatfs, with the same global
// names and other signatures, and a strong reference from here would pull its
// f_open in first. There the calls are WEAK references: they pull nothing, and
// bind to the f_ symbols VfsFat already brings in, oofatfs's.
#if MOY_VOL_FAT_WEAK
extern __typeof__(f_open) f_open __attribute__((weak));
extern __typeof__(f_read) f_read __attribute__((weak));
extern __typeof__(f_write) f_write __attribute__((weak));
extern __typeof__(f_close) f_close __attribute__((weak));
extern __typeof__(f_lseek) f_lseek __attribute__((weak));
extern __typeof__(f_stat) f_stat __attribute__((weak));
extern __typeof__(f_opendir) f_opendir __attribute__((weak));
extern __typeof__(f_readdir) f_readdir __attribute__((weak));
extern __typeof__(f_closedir) f_closedir __attribute__((weak));
extern __typeof__(f_mkdir) f_mkdir __attribute__((weak));
extern __typeof__(f_unlink) f_unlink __attribute__((weak));
extern __typeof__(f_rename) f_rename __attribute__((weak));
extern __typeof__(f_chdir) f_chdir __attribute__((weak));
#endif
#define FF(fn) fn
#endif
#if MOY_VOL_LFS2
#include "lib/littlefs/lfs2.h"
#endif
#if MOY_VOL_POSIX
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

// -- the store's lock (moy_vol.h, THE STORE'S LOCK) ------------------------------

#if defined(__has_include) && __has_include("esp_heap_caps.h")
#define LOCK_RTOS 1
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#elif !defined(__EMSCRIPTEN__) || defined(__EMSCRIPTEN_PTHREADS__)
#define LOCK_THREADS 1
#include <pthread.h>
#include <time.h>
#endif

// The holder and its depth: written by the holder alone, so a task reading
// the owner sees itself only while it holds the lock.
static uintptr_t s_owner;
static int s_depth;
static uint32_t s_held_at;
static moy_store_lock_stats_t s_stats;

#if LOCK_RTOS
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static StaticSemaphore_t s_mu_buf;
static SemaphoreHandle_t s_mu;

static SemaphoreHandle_t mu(void) {
    if (s_mu == NULL) {
        portENTER_CRITICAL(&s_mux);
        if (s_mu == NULL) {
            s_mu = xSemaphoreCreateMutexStatic(&s_mu_buf);
        }
        portEXIT_CRITICAL(&s_mux);
    }
    return s_mu;
}

uintptr_t moy_store_me(void) {
    return (uintptr_t)xTaskGetCurrentTaskHandle();
}

static uint32_t now_us(void) {
    return (uint32_t)esp_timer_get_time();
}

static int try_take(void) {
    return xSemaphoreTake(mu(), 0) == pdTRUE;
}

static void nap(void) {
    vTaskDelay(1);
}

static void take(void) {
    xSemaphoreTake(mu(), portMAX_DELAY);
}

static void give(void) {
    xSemaphoreGive(mu());
}
#elif LOCK_THREADS
static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;

uintptr_t moy_store_me(void) {
    return (uintptr_t)pthread_self() | 1u;
}

static uint32_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u);
}

static int try_take(void) {
    return pthread_mutex_trylock(&s_mu) == 0;
}

static void nap(void) {
    struct timespec ts = { 0, 100000 };
    nanosleep(&ts, NULL);
}

static void take(void) {
    pthread_mutex_lock(&s_mu);
}

static void give(void) {
    pthread_mutex_unlock(&s_mu);
}
#else
uintptr_t moy_store_me(void) {
    return 1u;
}

static uint32_t now_us(void) {
    return 0u;
}

static int try_take(void) {
    return 1;
}

static void nap(void) {
}

static void take(void) {
}

static void give(void) {
}
#endif

int moy_store_on_vm(void) __attribute__((weak));

// The VM's task goes first: while it waits, another task does not take the
// lock it let go, so the VM waits for one op at most, never for a task that
// takes the lock again and again (a mutex hands it to whoever asks next).
static uint32_t s_vm_wants;
// Every task blocked on the lock, the VM's among them, and the outermost takes
// so far: what moy_store_yield lets go for and waits on.
static uint32_t s_waiting;
static uint32_t s_takes;

void moy_store_lock(void) {
    uintptr_t me = moy_store_me();
    if (__atomic_load_n(&s_owner, __ATOMIC_RELAXED) == me) {
        s_depth++;
        return;
    }
    int vm = moy_store_on_vm != NULL && moy_store_on_vm();
    while (!vm && __atomic_load_n(&s_vm_wants, __ATOMIC_ACQUIRE) != 0u) {
        nap();
    }
    if (!try_take()) {
        uint32_t t0 = now_us();
        if (vm) {
            __atomic_add_fetch(&s_vm_wants, 1u, __ATOMIC_RELEASE);
        }
        __atomic_add_fetch(&s_waiting, 1u, __ATOMIC_RELEASE);
        take();
        __atomic_sub_fetch(&s_waiting, 1u, __ATOMIC_RELEASE);
        if (vm) {
            __atomic_sub_fetch(&s_vm_wants, 1u, __ATOMIC_RELEASE);
        }
        uint32_t w = now_us() - t0;
        s_stats.waits++;
        if (w > s_stats.wait_us) {
            s_stats.wait_us = w;
        }
        if (vm && w > s_stats.vm_wait_us) {
            s_stats.vm_wait_us = w;
        }
    }
    __atomic_store_n(&s_owner, me, __ATOMIC_RELAXED);
    s_depth = 1;
    s_held_at = now_us();
    __atomic_add_fetch(&s_takes, 1u, __ATOMIC_RELEASE);
}

void moy_store_unlock(void) {
    if (__atomic_load_n(&s_owner, __ATOMIC_RELAXED) != moy_store_me() || s_depth <= 0) {
        return;
    }
    if (--s_depth > 0) {
        return;
    }
    uint32_t h = now_us() - s_held_at;
    s_stats.holds++;
    if (h > s_stats.hold_us) {
        s_stats.hold_us = h;
    }
    __atomic_store_n(&s_owner, (uintptr_t)0, __ATOMIC_RELAXED);
    give();
}

int moy_store_lock_depth(void) {
    return __atomic_load_n(&s_owner, __ATOMIC_RELAXED) == moy_store_me() ? s_depth : 0;
}

void moy_store_unlock_to(int depth) {
    while (moy_store_lock_depth() > depth) {
        moy_store_unlock();
    }
}

void moy_store_lock_stats(moy_store_lock_stats_t *out, int reset) {
    moy_store_lock();
    *out = s_stats;
    if (reset) {
        memset(&s_stats, 0, sizeof s_stats);
    }
    moy_store_unlock();
}

// -- the volume table (moy_vol.h, THE VOLUME TABLE) --------------------------------

typedef struct {
    char at[MOY_VOL_TABLE_AT];
    uint8_t len;
    uint8_t kind;
    void *fs;
} mount_t;

static mount_t s_table[MOY_VOL_TABLE_N];

int moy_vol_table_set(const char *at, uint8_t kind, void *fs) {
    size_t n = strlen(at);
    while (n > 1u && at[n - 1u] == '/') {
        n--;
    }
    if (n == 0u || n >= MOY_VOL_TABLE_AT || at[0] != '/') {
        return MOY_EINVAL;
    }
    moy_store_lock();
    mount_t *free_slot = NULL;
    int rc = MOY_ENOSPC;
    for (size_t i = 0; i < MOY_VOL_TABLE_N; i++) {
        mount_t *m = &s_table[i];
        if (m->len == n && memcmp(m->at, at, n) == 0) {
            free_slot = m;
            break;
        }
        if (m->len == 0u && free_slot == NULL) {
            free_slot = m;
        }
    }
    if (free_slot != NULL) {
        memcpy(free_slot->at, at, n);
        free_slot->at[n] = 0;
        free_slot->len = (uint8_t)n;
        free_slot->kind = kind;
        free_slot->fs = fs;
        rc = 0;
    }
    moy_store_unlock();
    return rc;
}

void moy_vol_table_drop(void *fs) {
    moy_store_lock();
    for (size_t i = 0; i < MOY_VOL_TABLE_N; i++) {
        if (s_table[i].len != 0u && s_table[i].fs == fs) {
            memset(&s_table[i], 0, sizeof s_table[i]);
        }
    }
    moy_store_unlock();
}

void moy_vol_table_clear(void) {
    moy_store_lock();
    memset(s_table, 0, sizeof s_table);
    moy_store_unlock();
}

int moy_vol_table_at(const char *path, moy_vol_t *v, const char **rest) {
    if (path[0] != '/') {
        return MOY_ENODEV;
    }
    moy_store_lock();
    const mount_t *best = NULL;
    for (size_t i = 0; i < MOY_VOL_TABLE_N; i++) {
        const mount_t *m = &s_table[i];
        if (m->len == 0u || (best != NULL && m->len <= best->len)) {
            continue;
        }
        if (m->len == 1u || (strncmp(path, m->at, m->len) == 0
                             && (path[m->len] == '/' || path[m->len] == 0))) {
            best = m;
        }
    }
    int rc = MOY_ENODEV;
    if (best != NULL && best->kind != MOY_VOL_KIND_VM) {
        v->kind = best->kind;
        v->fs = best->fs;
        if (best->kind == MOY_VOL_KIND_POSIX || best->len == 1u) {
            *rest = path;
        } else {
            *rest = path[best->len] ? path + best->len : "/";
        }
        rc = 0;
    }
    moy_store_unlock();
    return rc;
}

int moy_vol_table_mounts(int (*fn)(void *arg, const char *name), void *arg) {
    mount_t t[MOY_VOL_TABLE_N];
    moy_store_lock();
    memcpy(t, s_table, sizeof t);
    moy_store_unlock();
    for (size_t i = 0; i < MOY_VOL_TABLE_N; i++) {
        if (t[i].len <= 1u) {
            continue;
        }
        char name[MOY_VOL_TABLE_AT];
        size_t n = 0;
        while (n + 1u < t[i].len && t[i].at[n + 1u] != '/') {
            name[n] = t[i].at[n + 1u];
            n++;
        }
        name[n] = 0;
        int r = n > 0 ? fn(arg, name) : 0;
        if (r != 0) {
            return r;
        }
    }
    return 0;
}

// -- FatFS's grant (oofatfs built FF_FS_REENTRANT) --------------------------------

static void *s_fat_locked;

void moy_vol_fat_lockable(void *fs) {
    s_fat_locked = fs;
}

#if MOY_VOL_FAT && FF_FS_REENTRANT
// One sync object, the store's lock, for the instance a task can reach; none
// for any other (a VfsFat over a Python block device, which only the VM's
// task works and whose sectors can raise from inside FatFS).
int ff_cre_syncobj(FATFS *fs, FF_SYNC_t *sobj) {
    *sobj = (fs == s_fat_locked) ? (FF_SYNC_t)(uintptr_t)fs : (FF_SYNC_t)0;
    return 1;
}

int ff_req_grant(FF_SYNC_t sobj) {
    if (sobj) {
        moy_store_lock();
    }
    return 1;
}

void ff_rel_grant(FF_SYNC_t sobj) {
    if (sobj) {
        moy_store_unlock();
    }
}

int ff_del_syncobj(FF_SYNC_t sobj) {
    (void)sobj;
    return 1;
}
#endif

struct moy_vol_file {
    struct moy_vol_file *next;      // the open list moy_vol_unwind walks
    uintptr_t who;                  // the task that opened it
    size_t bytes;                   // as allocated
    uint8_t kind;
    void *fs;
    union {
        int fd;
        #if MOY_VOL_FAT
        FIL fil;
        #endif
        #if MOY_VOL_LFS2
        lfs2_file_t lf;
        #endif
    } u;
    #if MOY_VOL_LFS2
    struct lfs2_file_config lcfg;   // its buffer follows the struct
    #endif
};

static moy_vol_file_t *open_files;
static moy_vol_here_t *heres;

static void unlink_open(moy_vol_file_t *f) {
    for (moy_vol_file_t **p = &open_files; *p; p = &(*p)->next) {
        if (*p == f) {
            *p = f->next;
            return;
        }
    }
}

// -- FAT ----------------------------------------------------------------------

#if MOY_VOL_FAT
// fresult_to_errno_table's values (extmod/vfs_fat.c), indexed by FRESULT.
static const uint8_t fat_errno[] = {
    0, MOY_EIO, MOY_EIO, MOY_EBUSY, 2, 2, MOY_EINVAL, MOY_EACCES, MOY_EEXIST,
    MOY_EINVAL, MOY_EROFS, MOY_ENODEV, MOY_ENODEV, MOY_ENODEV, MOY_EIO,
    MOY_EIO, MOY_EIO, MOY_ENOMEM, MOY_EMFILE, MOY_EINVAL,
};

static int fat_err(FRESULT r) {
    return (unsigned)r < sizeof fat_errno ? fat_errno[r] : MOY_EIO;
}

static int fat_root(const char *p) {
    return p[0] == 0 || (p[0] == '/' && p[1] == 0);
}
#endif

// -- littlefs -----------------------------------------------------------------

#if MOY_VOL_LFS2
static int lfs_err(int r) {
    return r >= 0 ? 0 : -r;
}
#endif

// -- POSIX ----------------------------------------------------------------------

#if MOY_VOL_POSIX
static int px_err(void) {
    return errno ? errno : MOY_EIO;
}
#endif

static int vol_open(const moy_vol_t *v, const char *path, int mode,
                    moy_vol_file_t **out) {
    size_t bytes = sizeof(moy_vol_file_t);
    #if MOY_VOL_LFS2
    if (v->kind == MOY_VOL_KIND_LFS2) {
        bytes += ((lfs2_t *)v->fs)->cfg->cache_size;
    }
    #endif
    moy_store_tick();
    int held = mode & MOY_VOL_HELD;
    mode &= ~MOY_VOL_HELD;
    moy_vol_file_t *f = held ? moy_store_keep(bytes) : moy_store_alloc(bytes);
    if (f == NULL) {
        return MOY_ENOMEM;
    }
    f->bytes = bytes;
    f->kind = v->kind;
    f->fs = v->fs;
    int rc = MOY_ENODEV;
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            rc = fat_err(FF(f_open)((FATFS *)v->fs, &f->u.fil, path,
                                mode == MOY_VOL_WRITE ? FA_WRITE | FA_CREATE_ALWAYS
                                : mode == MOY_VOL_APPEND ? FA_WRITE | FA_OPEN_APPEND
                                : FA_READ));
            break;
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            f->lcfg.buffer = (uint8_t *)f + sizeof(moy_vol_file_t);
            rc = lfs_err(lfs2_file_opencfg(
                (lfs2_t *)v->fs, &f->u.lf, path,
                mode == MOY_VOL_WRITE ? LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_TRUNC
                : mode == MOY_VOL_APPEND ? LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_APPEND
                : LFS2_O_RDONLY, &f->lcfg));
            break;
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            f->u.fd = mode == MOY_VOL_WRITE
                      ? open(path, O_WRONLY | O_CREAT | O_TRUNC, 0666)
                      : mode == MOY_VOL_APPEND
                      ? open(path, O_WRONLY | O_CREAT | O_APPEND, 0666)
                      : open(path, O_RDONLY);
            rc = f->u.fd < 0 ? px_err() : 0;
            if (rc == 0 && mode == MOY_VOL_READ) {
                struct stat st;
                if (fstat(f->u.fd, &st) == 0 && S_ISDIR(st.st_mode)) {
                    close(f->u.fd);
                    rc = MOY_EISDIR;
                }
            }
            break;
        #endif
        default:
            break;
    }
    if (rc != 0) {
        moy_store_free(f, bytes);
        return rc;
    }
    if (!held) {
        f->who = moy_store_me();
        f->next = open_files;
        open_files = f;
    }
    *out = f;
    return 0;
}

static int vol_read(moy_vol_file_t *f, void *buf, size_t n, size_t *got) {
    *got = 0;
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            UINT br = 0;
            FRESULT r = FF(f_read)(&f->u.fil, buf, (UINT)n, &br);
            *got = br;
            return fat_err(r);
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_ssize_t r = lfs2_file_read((lfs2_t *)f->fs, &f->u.lf, buf,
                                            (lfs2_size_t)n);
            if (r < 0) {
                return lfs_err((int)r);
            }
            *got = (size_t)r;
            return 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            for (;;) {
                errno = 0;
                ssize_t r = read(f->u.fd, buf, n);
                if (r >= 0) {
                    *got = (size_t)r;
                    return 0;
                }
                if (errno != EINTR) {
                    return px_err();
                }
            }
        #endif
        default:
            return MOY_EBADF;
    }
}

static int vol_write(moy_vol_file_t *f, const void *buf, size_t n) {
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            UINT bw = 0;
            FRESULT r = FF(f_write)(&f->u.fil, buf, (UINT)n, &bw);
            if (r != FR_OK) {
                return fat_err(r);
            }
            return bw == n ? 0 : MOY_ENOSPC;
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_ssize_t r = lfs2_file_write((lfs2_t *)f->fs, &f->u.lf, buf,
                                             (lfs2_size_t)n);
            if (r < 0) {
                return lfs_err((int)r);
            }
            return (size_t)r == n ? 0 : MOY_ENOSPC;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            const uint8_t *p = buf;
            while (n) {
                errno = 0;
                ssize_t r = write(f->u.fd, p, n);
                if (r < 0) {
                    if (errno == EINTR) {
                        continue;
                    }
                    return px_err();
                }
                p += r;
                n -= (size_t)r;
            }
            return 0;
        }
        #endif
        default:
            return MOY_EBADF;
    }
}

static int vol_size(moy_vol_file_t *f, uint32_t *n) {
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            *n = (uint32_t)f_size(&f->u.fil);
            return 0;
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_soff_t r = lfs2_file_size((lfs2_t *)f->fs, &f->u.lf);
            if (r < 0) {
                return lfs_err((int)r);
            }
            *n = (uint32_t)r;
            return 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            struct stat st;
            if (fstat(f->u.fd, &st) != 0) {
                return px_err();
            }
            *n = (uint32_t)st.st_size;
            return 0;
        }
        #endif
        default:
            return MOY_EBADF;
    }
}

static int vol_seek(moy_vol_file_t *f, uint32_t at) {
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_lseek)(&f->u.fil, (FSIZE_t)at));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_soff_t r = lfs2_file_seek((lfs2_t *)f->fs, &f->u.lf, (lfs2_soff_t)at,
                                           LFS2_SEEK_SET);
            return r < 0 ? lfs_err((int)r) : 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            return lseek(f->u.fd, (off_t)at, SEEK_SET) < 0 ? px_err() : 0;
        #endif
        default:
            return MOY_EBADF;
    }
}

static int vol_close(moy_vol_file_t *f) {
    int rc = MOY_EBADF;
    unlink_open(f);
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            rc = fat_err(FF(f_close)(&f->u.fil));
            break;
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            rc = lfs_err(lfs2_file_close((lfs2_t *)f->fs, &f->u.lf));
            break;
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            rc = close(f->u.fd) == 0 ? 0 : px_err();
            break;
        #endif
        default:
            break;
    }
    moy_store_free(f, f->bytes);
    return rc;
}

static moy_vol_gate_fn gate;

void moy_vol_set_gate(moy_vol_gate_fn fn) {
    gate = fn;
}

// What moy_vol_gate_enter took: the lock, and the bus gate.
enum { GATE_BUS = 1, GATE_LOCK = 2 };

// The lock holder's bus gates (under the lock).
static int s_bus;

void moy_store_yield(void) {
    if ((__atomic_load_n(&s_waiting, __ATOMIC_ACQUIRE) == 0u
         && __atomic_load_n(&s_vm_wants, __ATOMIC_ACQUIRE) == 0u)
        || (moy_store_on_vm != NULL && moy_store_on_vm())) {
        return;
    }
    int depth = moy_store_lock_depth();
    if (depth <= 0) {
        return;
    }
    uintptr_t me = moy_store_me();
    for (moy_vol_here_t *h = heres; h != NULL; h = h->next) {
        if (h->who == me) {
            return;                     // its working folder holds the volume
        }
    }
    int bus = s_bus;
    for (int i = 0; i < bus && gate != NULL; i++) {
        gate(0);
    }
    s_bus = 0;
    uint32_t took = __atomic_load_n(&s_takes, __ATOMIC_ACQUIRE);
    moy_store_unlock_to(0);
    // A mutex hands the lock to whoever asks next, this task included: it
    // waits until a waiter has taken it, or none waits any more.
    while (__atomic_load_n(&s_waiting, __ATOMIC_ACQUIRE) != 0u
           && __atomic_load_n(&s_takes, __ATOMIC_ACQUIRE) == took) {
        nap();
    }
    for (int i = 0; i < depth; i++) {
        moy_store_lock();               // the VM's op first (its want)
    }
    for (int i = 0; i < bus && gate != NULL; i++) {
        gate(1);
    }
    s_bus = bus;
}

int moy_vol_gate_enter(const char *path) {
    moy_vol_t v;
    const char *r;
    int rc = moy_vol_at(path, &v, &r);      // first: on the VM's task it can raise
    if (rc == MOY_ENODEV && moy_store_on_vm != NULL && !moy_store_on_vm()) {
        return -1;
    }
    moy_store_lock();
    if (gate == NULL || rc != 0 || v.kind != MOY_VOL_KIND_FAT) {
        return GATE_LOCK;
    }
    gate(1);
    s_bus++;
    return GATE_LOCK | GATE_BUS;
}

void moy_vol_gate_leave(int entered) {
    if (entered < 0) {
        return;
    }
    if ((entered & GATE_BUS) && gate != NULL) {
        gate(0);
        s_bus--;
    }
    if (entered & GATE_LOCK) {
        moy_store_unlock();
    }
}

// The calling task's files and working folders: another task's are its own.
void moy_vol_unwind(void) {
    uintptr_t me = moy_store_me();
    moy_store_lock();
    for (int again = 1; again;) {
        again = 0;
        for (moy_vol_file_t *f = open_files; f != NULL; f = f->next) {
            if (f->who == me) {
                moy_vol_close(f);
                again = 1;
                break;
            }
        }
    }
    for (int again = 1; again;) {
        again = 0;
        for (moy_vol_here_t *h = heres; h != NULL; h = h->next) {
            if (h->who == me) {
                moy_vol_leave(h);
                again = 1;
                break;
            }
        }
    }
    moy_store_unlock();
}

int moy_vol_enter(const moy_vol_t *v, const char *dir, moy_vol_here_t *h) {
    memset(h, 0, sizeof *h);
    h->v = *v;
    h->dir_n = strlen(dir);
    while (h->dir_n > 1u && dir[h->dir_n - 1u] == '/') {
        h->dir_n--;
    }
    h->dir = moy_store_alloc(h->dir_n + 1u);
    if (h->dir == NULL) {
        return MOY_ENOMEM;
    }
    moy_store_lock();
    h->who = moy_store_me();
    memcpy(h->dir, dir, h->dir_n);
    #if MOY_VOL_FAT
    if (v->kind == MOY_VOL_KIND_FAT) {
        FATFS *fs = v->fs;
        h->cdir = fs->cdir;
        #if FF_FS_EXFAT
        h->cdc_scl = fs->cdc_scl;
        h->cdc_size = fs->cdc_size;
        h->cdc_ofs = fs->cdc_ofs;
        #endif
        int rc = fat_err(FF(f_chdir)(fs, h->dir));
        if (rc != 0) {
            moy_store_unlock();
            moy_store_free(h->dir, h->dir_n + 1u);
            h->dir = NULL;
            return rc;
        }
    }
    #endif
    h->next = heres;
    heres = h;
    return 0;
}

const char *moy_vol_here_name(moy_vol_here_t *h, const char *name) {
    if (h->v.kind == MOY_VOL_KIND_FAT) {
        return name;
    }
    if (name[0] == 0) {
        return h->dir;
    }
    size_t nn = strlen(name), need = h->dir_n + nn + 2u;
    if (need > h->name_cap) {
        if (h->name != NULL) {
            moy_store_free(h->name, h->name_cap);
        }
        h->name = moy_store_alloc(need);
        h->name_cap = h->name != NULL ? need : 0u;
        if (h->name == NULL) {
            return NULL;
        }
    }
    memcpy(h->name, h->dir, h->dir_n);
    size_t k = h->dir_n;
    if (k == 0u || h->name[k - 1u] != '/') {
        h->name[k++] = '/';
    }
    memcpy(h->name + k, name, nn + 1u);
    return h->name;
}

void moy_vol_leave(moy_vol_here_t *h) {
    for (moy_vol_here_t **p = &heres; *p; p = &(*p)->next) {
        if (*p == h) {
            *p = h->next;
            break;
        }
    }
    #if MOY_VOL_FAT
    if (h->v.kind == MOY_VOL_KIND_FAT && h->dir != NULL) {
        FATFS *fs = h->v.fs;
        fs->cdir = h->cdir;
        #if FF_FS_EXFAT
        fs->cdc_scl = h->cdc_scl;
        fs->cdc_size = h->cdc_size;
        fs->cdc_ofs = h->cdc_ofs;
        #endif
    }
    #endif
    if (h->name != NULL) {
        moy_store_free(h->name, h->name_cap);
    }
    if (h->dir != NULL) {
        moy_store_free(h->dir, h->dir_n + 1u);
        moy_store_unlock();
    }
    h->name = h->dir = NULL;
    h->name_cap = 0;
}

#if MOY_VOL_FAT
// Days from 1970-01-01 to a civil date (H. Hinnant's days_from_civil).
static int64_t days_from_civil(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    int era = (y >= 0 ? y : y - 399) / 400;
    unsigned yoe = (unsigned)(y - era * 400);
    unsigned doy = (153u * (m > 2 ? m - 3 : m + 9) + 2u) / 5u + d - 1u;
    unsigned doe = yoe * 365u + yoe / 4u - yoe / 100u + doy;
    return (int64_t)era * 146097 + (int64_t)doe - 719468;
}
#endif

#if MOY_VOL_FAT
// What a FAT directory entry says of its file: folder, size, stamp.
static void fat_info(const FILINFO *fno, moy_vol_stat_t *st) {
    st->is_dir = (fno->fattrib & AM_DIR) != 0;
    st->size = (uint32_t)fno->fsize;
    st->mtime = 0;
    unsigned mo = (fno->fdate >> 5) & 0x0f, dd = fno->fdate & 0x1f;
    if (mo >= 1 && mo <= 12 && dd >= 1) {
        st->mtime = days_from_civil(1980 + ((fno->fdate >> 9) & 0x7f), mo, dd) * 86400
                    + ((fno->ftime >> 11) & 0x1f) * 3600 + ((fno->ftime >> 5) & 0x3f) * 60
                    + 2 * (fno->ftime & 0x1f);
    }
}
#endif

#if MOY_VOL_LFS2
// littlefs's mtime: attribute 1, nanoseconds, little-endian; 0 where unset.
static int64_t lfs_mtime(lfs2_t *fs, const char *path) {
    uint8_t b[8];
    if (lfs2_getattr(fs, path, 1, b, sizeof b) != (lfs2_ssize_t)sizeof b) {
        return 0;
    }
    uint64_t ns = 0;
    for (size_t i = sizeof b; i > 0; --i) {
        ns = ns << 8 | b[i - 1];
    }
    return (int64_t)(ns / 1000000000u);
}
#endif

static int vol_stat(const moy_vol_t *v, const char *path, moy_vol_stat_t *st) {
    st->is_dir = 0;
    st->size = 0;
    st->mtime = 0;
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            if (fat_root(path)) {
                st->is_dir = 1;
                return 0;
            }
            FILINFO fno;
            FRESULT r = FF(f_stat)((FATFS *)v->fs, path, &fno);
            if (r != FR_OK) {
                return fat_err(r);
            }
            fat_info(&fno, st);
            return 0;
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            struct lfs2_info info;
            int r = lfs2_stat((lfs2_t *)v->fs, path, &info);
            if (r < 0) {
                return lfs_err(r);
            }
            st->is_dir = info.type == LFS2_TYPE_DIR;
            st->size = info.size;
            st->mtime = lfs_mtime((lfs2_t *)v->fs, path);
            return 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            struct stat s;
            errno = 0;
            if (stat(path, &s) != 0) {
                return px_err();
            }
            st->is_dir = S_ISDIR(s.st_mode) != 0;
            st->size = (uint32_t)s.st_size;
            st->mtime = (int64_t)s.st_mtime;
            return 0;
        }
        #endif
        default:
            return MOY_ENODEV;
    }
}

// The listing: each entry with what its folder's entry says of it; littlefs's
// mtime, an attribute of its own, read only where `mtime` asks.
static int vol_scan(const moy_vol_t *v, const char *dir, moy_vol_scan_fn fn,
                    void *ctx, int mtime) {
    int rc = 0;
    moy_vol_stat_t st;
    (void)mtime;
    moy_store_tick();
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            FF_DIR *d = moy_store_alloc(sizeof(FF_DIR));
            FILINFO *fno = moy_store_alloc(sizeof(FILINFO));
            if (d == NULL || fno == NULL) {
                rc = MOY_ENOMEM;
            } else {
                rc = fat_err(FF(f_opendir)((FATFS *)v->fs, d, dir));
                if (rc == 0) {
                    for (;;) {
                        FRESULT r = FF(f_readdir)(d, fno);
                        if (r != FR_OK) {
                            rc = fat_err(r);
                            break;
                        }
                        if (fno->fname[0] == 0) {
                            break;
                        }
                        fat_info(fno, &st);
                        rc = fn(ctx, fno->fname, strlen(fno->fname), &st);
                        if (rc) {
                            break;
                        }
                    }
                    FF(f_closedir)(d);
                }
            }
            if (fno != NULL) {
                moy_store_free(fno, sizeof(FILINFO));
            }
            if (d != NULL) {
                moy_store_free(d, sizeof(FF_DIR));
            }
            return rc;
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_dir_t *d = moy_store_alloc(sizeof(lfs2_dir_t));
            struct lfs2_info *info = moy_store_alloc(sizeof(struct lfs2_info));
            if (d == NULL || info == NULL) {
                rc = MOY_ENOMEM;
            } else {
                rc = lfs_err(lfs2_dir_open((lfs2_t *)v->fs, d, dir));
                if (rc == 0) {
                    for (;;) {
                        int r = lfs2_dir_read((lfs2_t *)v->fs, d, info);
                        if (r <= 0) {
                            rc = lfs_err(r);
                            break;
                        }
                        if (info->name[0] == '.' && (info->name[1] == 0
                            || (info->name[1] == '.' && info->name[2] == 0))) {
                            continue;
                        }
                        st.is_dir = info->type == LFS2_TYPE_DIR;
                        st.size = info->size;
                        st.mtime = 0;
                        if (mtime) {
                            size_t dl = strlen(dir), nl = strlen(info->name);
                            char *full = moy_store_alloc(dl + nl + 2);
                            if (full == NULL) {
                                rc = MOY_ENOMEM;
                                break;
                            }
                            memcpy(full, dir, dl);
                            full[dl] = '/';
                            memcpy(full + dl + 1, info->name, nl + 1);
                            st.mtime = lfs_mtime((lfs2_t *)v->fs, full);
                            moy_store_free(full, dl + nl + 2);
                        }
                        rc = fn(ctx, info->name, strlen(info->name), &st);
                        if (rc) {
                            break;
                        }
                    }
                    lfs2_dir_close((lfs2_t *)v->fs, d);
                }
            }
            if (info != NULL) {
                moy_store_free(info, sizeof(struct lfs2_info));
            }
            if (d != NULL) {
                moy_store_free(d, sizeof(lfs2_dir_t));
            }
            return rc;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            errno = 0;
            DIR *d = opendir(dir);
            if (d == NULL) {
                return px_err();
            }
            size_t dl = strlen(dir);
            for (;;) {
                errno = 0;
                struct dirent *e = readdir(d);
                if (e == NULL) {
                    rc = errno ? px_err() : 0;
                    break;
                }
                const char *nm = e->d_name;
                if (nm[0] == '.' && (nm[1] == 0 || (nm[1] == '.' && nm[2] == 0))) {
                    continue;
                }
                size_t nl = strlen(nm), full_n = dl + nl + 2;
                char *full = moy_store_alloc(full_n);
                if (full == NULL) {
                    rc = MOY_ENOMEM;
                    break;
                }
                memcpy(full, dir, dl);
                full[dl] = '/';
                memcpy(full + dl + 1, nm, nl);
                struct stat s;
                int ok = stat(full, &s) == 0;
                moy_store_free(full, full_n);
                st.is_dir = ok && S_ISDIR(s.st_mode);
                st.size = ok ? (uint32_t)s.st_size : 0u;
                st.mtime = ok ? (int64_t)s.st_mtime : 0;
                rc = fn(ctx, nm, nl, &st);
                if (rc) {
                    break;
                }
            }
            closedir(d);
            return rc;
        }
        #endif
        default:
            return MOY_ENODEV;
    }
}

static int vol_mkdir(const moy_vol_t *v, const char *path) {
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_mkdir)((FATFS *)v->fs, path));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            return lfs_err(lfs2_mkdir((lfs2_t *)v->fs, path));
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            return mkdir(path, 0777) == 0 ? 0 : px_err();
        #endif
        default:
            return MOY_ENODEV;
    }
}

static int vol_remove(const moy_vol_t *v, const char *path) {
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_unlink)((FATFS *)v->fs, path));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            return lfs_err(lfs2_remove((lfs2_t *)v->fs, path));
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            struct stat s;
            errno = 0;
            if (stat(path, &s) == 0 && S_ISDIR(s.st_mode)) {
                return rmdir(path) == 0 ? 0 : px_err();
            }
            return unlink(path) == 0 ? 0 : px_err();
        }
        #endif
        default:
            return MOY_ENODEV;
    }
}

static int native_rename(const moy_vol_t *v, const char *src, const char *dst) {
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_rename)((FATFS *)v->fs, src, dst));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            return lfs_err(lfs2_rename((lfs2_t *)v->fs, src, dst));
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            return rename(src, dst) == 0 ? 0 : px_err();
        #endif
        default:
            return MOY_ENODEV;
    }
}

static int vol_rename(const moy_vol_t *v, const char *src, const char *dst) {
    if (v->kind != MOY_VOL_KIND_FAT) {
        moy_vol_stat_t st;
        if (moy_vol_stat(v, dst, &st) == 0) {
            return MOY_EEXIST;
        }
    }
    return native_rename(v, src, dst);
}

static int vol_replace(const moy_vol_t *v, const char *src, const char *dst) {
    int rc = native_rename(v, src, dst);
    if (rc != 0 && v->kind == MOY_VOL_KIND_FAT) {
        moy_vol_remove(v, dst);
        rc = native_rename(v, src, dst);
    }
    return rc;
}

// -- every call under the store's lock ------------------------------------------

int moy_vol_open(const moy_vol_t *v, const char *path, int mode, moy_vol_file_t **out) {
    moy_store_lock();
    int rc = vol_open(v, path, mode, out);
    moy_store_unlock();
    return rc;
}

int moy_vol_read(moy_vol_file_t *f, void *buf, size_t n, size_t *got) {
    moy_store_lock();
    int rc = vol_read(f, buf, n, got);
    moy_store_unlock();
    return rc;
}

int moy_vol_write(moy_vol_file_t *f, const void *buf, size_t n) {
    moy_store_lock();
    int rc = vol_write(f, buf, n);
    moy_store_unlock();
    return rc;
}

int moy_vol_size(moy_vol_file_t *f, uint32_t *n) {
    moy_store_lock();
    int rc = vol_size(f, n);
    moy_store_unlock();
    return rc;
}

int moy_vol_seek(moy_vol_file_t *f, uint32_t at) {
    moy_store_lock();
    int rc = vol_seek(f, at);
    moy_store_unlock();
    return rc;
}

int moy_vol_close(moy_vol_file_t *f) {
    moy_store_lock();
    int rc = vol_close(f);
    moy_store_unlock();
    return rc;
}

int moy_vol_stat(const moy_vol_t *v, const char *path, moy_vol_stat_t *st) {
    moy_store_lock();
    int rc = vol_stat(v, path, st);
    moy_store_unlock();
    return rc;
}

typedef struct {
    moy_vol_ent_fn fn;
    void *ctx;
} ent_of_t;

static int ent_of(void *c, const char *name, size_t len, const moy_vol_stat_t *st) {
    ent_of_t *e = c;
    return e->fn(e->ctx, name, len, st->is_dir, st->size);
}

int moy_vol_list(const moy_vol_t *v, const char *dir, moy_vol_ent_fn fn, void *ctx) {
    ent_of_t e = { fn, ctx };
    moy_store_lock();
    int rc = vol_scan(v, dir, ent_of, &e, 0);
    moy_store_unlock();
    return rc;
}

int moy_vol_scan(const moy_vol_t *v, const char *dir, moy_vol_scan_fn fn, void *ctx) {
    moy_store_lock();
    int rc = vol_scan(v, dir, fn, ctx, 1);
    moy_store_unlock();
    return rc;
}

int moy_vol_mkdir(const moy_vol_t *v, const char *path) {
    moy_store_lock();
    int rc = vol_mkdir(v, path);
    moy_store_unlock();
    return rc;
}

int moy_vol_remove(const moy_vol_t *v, const char *path) {
    moy_store_lock();
    int rc = vol_remove(v, path);
    moy_store_unlock();
    return rc;
}

int moy_vol_rename(const moy_vol_t *v, const char *src, const char *dst) {
    moy_store_lock();
    int rc = vol_rename(v, src, dst);
    moy_store_unlock();
    return rc;
}

int moy_vol_replace(const moy_vol_t *v, const char *src, const char *dst) {
    moy_store_lock();
    int rc = vol_replace(v, src, dst);
    moy_store_unlock();
    return rc;
}
