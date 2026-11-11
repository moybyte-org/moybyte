// wild: a native module that misbehaves on request, for the P4's U-mode
// isolation (native/moy_elf/moy_umode.h, tests/on_glass.py's elf_umode).
// Each wild mode should end the process with a fault record and leave the
// console running; the others check what U-mode must keep working. The
// sweep's rows come from native/os_calls.json (rows.h, generated).
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "rows.h"

#define KERNEL_RAM 0x4ff40000ul     // internal SRAM: the kernel's
#define EVQ_FD 31                   // native/moy_kernel/moy_evq.h's MOY_EVQ_FD

// Output gathered (`g_buf`, a listing's) or written at once.
static char *g_buf;
static size_t g_at, g_cap;

static void put(const char *s) {
    size_t n = strlen(s);
    if (g_buf != NULL && g_at + n <= g_cap) {
        memcpy(g_buf + g_at, s, n);
        g_at += n;
        return;
    }
    write(1, s, n);
}

static void num(unsigned long v) {
    char b[16];
    int i = sizeof b;
    b[--i] = 0;
    do {
        b[--i] = (char)('0' + v % 10);
        v /= 10;
    } while (v);
    put(b + i);
}

static void hex(unsigned long v) {
    char b[12];
    int i = sizeof b;
    b[--i] = 0;
    do {
        b[--i] = "0123456789abcdef"[v & 15u];
        v >>= 4;
    } while (v);
    b[--i] = 'x';
    b[--i] = '0';
    put(b + i);
}

static unsigned long parse(const char *s) {
    unsigned long v = 0;
    int base = 10;
    if (s[0] == '0' && s[1] == 'x') {
        base = 16;
        s += 2;
    }
    for (; *s; s++) {
        int d = *s >= '0' && *s <= '9' ? *s - '0' : *s >= 'a' && *s <= 'f' ? *s - 'a' + 10 : -1;
        if (d < 0 || d >= base) {
            break;
        }
        v = v * (unsigned long)base + (unsigned long)d;
    }
    return v;
}

static int deep(int n) {
    volatile char pad[256];
    pad[0] = (char)n;
    return deep(n + 1) + pad[0];
}

// -- a raw row: its ordinal in t0, its arguments in a0..a7 -----------------------

static long raw(unsigned row, const unsigned long *a) {
    register unsigned long a0 __asm__("a0") = a[0];
    register unsigned long a1 __asm__("a1") = a[1];
    register unsigned long a2 __asm__("a2") = a[2];
    register unsigned long a3 __asm__("a3") = a[3];
    register unsigned long a4 __asm__("a4") = a[4];
    register unsigned long a5 __asm__("a5") = a[5];
    register unsigned long a6 __asm__("a6") = a[6];
    register unsigned long a7 __asm__("a7") = a[7];
    register unsigned long t0 __asm__("t0") = row;
    __asm__ volatile ("ecall"
                      : "+r"(a0), "+r"(a1)
                      : "r"(a2), "r"(a3), "r"(a4), "r"(a5), "r"(a6), "r"(a7), "r"(t0)
                      : "memory");
    return (long)a0;
}

static const wild_row_t *row_named(const char *name) {
    for (unsigned i = 0; i < WILD_NROWS; i++) {
        if (strcmp(WILD_ROWS[i].name, name) == 0) {
            return &WILD_ROWS[i];
        }
    }
    return NULL;
}

// -- the argument sweep -----------------------------------------------------------

enum { B_NULL, B_KERNEL, B_PSRAM, B_FOREIGN, B_MAPEND, B_MODEND, B_N };
static const char *const BAD[B_N] = { "null", "kernel", "psram", "foreign", "mapend", "modend" };

static unsigned char g_ok[1024] __attribute__((aligned(16)));    // a benign buffer
static socklen_t g_len;
static char g_tail[64];
static unsigned long g_psram, g_foreign;
static uint8_t *g_map;                     // 128 bytes of the process's own, 'A' to its end

static void *benign_fn(void *arg) {
    return arg;
}

static int is_ptr(int k) {
    return k == A_STR || k == A_STR0 || k == A_BUF || k == A_BUF0 || k == A_CODE || k == A_LEN;
}

static int nullable(int k) {
    return k == A_STR0 || k == A_BUF0 || k == A_LEN;
}

static int applies(const wild_row_t *r, int i, int b) {
    switch (b) {
    case B_NULL: return 1;
    case B_FOREIGN: return g_foreign != 0;
    case B_MODEND: return r->len[i] >= 0;
    default: return 1;
    }
}

// The arguments of row r with argument `bad_i` (or none, -1) the bad
// pointer `b`.
static void args_of(const wild_row_t *r, int bad_i, int b, unsigned long *a) {
    memset(g_ok, 0, sizeof g_ok);
    g_len = 16;
    memset(a, 0, 8 * sizeof *a);
    for (int j = 0; j < r->n; j++) {
        switch (r->kind[j]) {
        case A_STR: case A_STR0: a[j] = (unsigned long)"wild-none"; break;
        case A_BUF: case A_BUF0: a[j] = (unsigned long)g_ok; break;
        case A_LEN: a[j] = (unsigned long)&g_len; break;
        case A_CODE: a[j] = (unsigned long)benign_fn; break;
        case A_FD: a[j] = (unsigned long)-1; break;
        default: a[j] = 0; break;
        }
    }
    for (int j = 0; j < r->n; j++) {
        if (r->len[j] >= 0) {
            a[r->len[j]] = j == bad_i && b == B_MODEND ? 0x100000ul : 16ul;
        }
    }
    if (strcmp(r->name, "getaddrinfo") == 0) {
        ((struct addrinfo *)g_ok)->ai_flags = AI_NUMERICHOST;
    }
    if (bad_i < 0) {
        return;
    }
    int k = r->kind[bad_i];
    switch (b) {
    case B_NULL: a[bad_i] = 0; break;
    case B_KERNEL: a[bad_i] = KERNEL_RAM; break;
    case B_PSRAM: a[bad_i] = g_psram; break;
    case B_FOREIGN: a[bad_i] = g_foreign; break;
    case B_MAPEND:
        a[bad_i] = k == A_STR || k == A_STR0 ? (unsigned long)g_map : (unsigned long)g_map + 126u;
        break;
    case B_MODEND: a[bad_i] = (unsigned long)g_tail; break;
    }
}

// Every case that must end the process: (row, argument, bad pointer).
static int each_case(int want, const wild_row_t **r, int *i, int *b) {
    int c = 0;
    for (unsigned ri = 0; ri < WILD_NROWS; ri++) {
        const wild_row_t *w = &WILD_ROWS[ri];
        for (int j = 0; j < w->n; j++) {
            if (!is_ptr(w->kind[j])) {
                continue;
            }
            for (int k = 0; k < B_N; k++) {
                if ((k == B_NULL && nullable(w->kind[j])) || !applies(w, j, k)) {
                    continue;
                }
                if (c == want) {
                    *r = w;
                    *i = j;
                    *b = k;
                    return c;
                }
                c++;
            }
        }
    }
    return c;
}

static void map_init(void) {
    g_map = mmap(NULL, 128, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (g_map != MAP_FAILED) {
        memset(g_map, 'A', 128);
    }
}

static int sweep_list(void) {
    const wild_row_t *r;
    int i, b;
    int n = each_case(-1, &r, &i, &b);
    g_cap = (size_t)n * 48u;
    g_buf = malloc(g_cap);
    g_at = 0;
    for (int c = 0; c < n; c++) {
        each_case(c, &r, &i, &b);
        num((unsigned long)c);
        put(" ");
        num(r->row);
        put(" ");
        put(r->name);
        put(" ");
        num((unsigned long)i);
        put(" ");
        put(BAD[b]);
        put("\n");
    }
    if (g_buf != NULL) {
        write(1, g_buf, g_at);
    }
    return 0;
}

static int sweep_one(int c) {
    const wild_row_t *r;
    int i, b;
    if (each_case(c, &r, &i, &b) != c) {
        put("no such case\n");
        return 2;
    }
    unsigned long a[8];
    args_of(r, i, b, a);
    long got = raw(r->row, a);
    put("survived ");
    put(r->name);
    put(" -> ");
    num((unsigned long)got);
    put("\n");
    return 0;
}

// Every nullable pointer as NULL, the row run: an answer, never a fault.
static int sweep_nulls(void) {
    int n = 0;
    for (unsigned ri = 0; ri < WILD_NROWS; ri++) {
        const wild_row_t *r = &WILD_ROWS[ri];
        for (int j = 0; j < r->n; j++) {
            if (!nullable(r->kind[j])) {
                continue;
            }
            if (strcmp(r->name, "select") == 0 && j == 4) {
                continue;           // no timeout: it waits for ever, as POSIX's does
            }
            unsigned long a[8];
            args_of(r, j, B_NULL, a);
            raw(r->row, a);
            n++;
        }
    }
    put("nulls ");
    num((unsigned long)n);
    put(" ok\n");
    return 0;
}

// Every counted buffer as a kernel pointer with a count of 0: the check
// passes it (it is no memory), and the row must touch nothing.
static int sweep_zeros(void) {
    int n = 0;
    for (unsigned ri = 0; ri < WILD_NROWS; ri++) {
        const wild_row_t *r = &WILD_ROWS[ri];
        if (strcmp(r->name, "moy_share") == 0) {
            continue;
        }
        for (int j = 0; j < r->n; j++) {
            if (r->len[j] < 0) {
                continue;
            }
            unsigned long a[8];
            args_of(r, j, B_KERNEL, a);
            a[r->len[j]] = 0;
            raw(r->row, a);
            n++;
        }
    }
    put("zeros ");
    num((unsigned long)n);
    put(" ok\n");
    return 0;
}

// -- handles: stale, foreign and reused ------------------------------------------

static int g_bad;

static void bad(const char *what, unsigned long v, long got) {
    put("BAD ");
    put(what);
    put(" ");
    hex(v);
    put(" -> ");
    num((unsigned long)got);
    put("\n");
    g_bad++;
}

static volatile int g_go;

static void *wait_fn(void *arg) {
    while (!g_go) {
        usleep(1000);
    }
    return arg;
}

static pthread_mutex_t g_m;
static volatile int g_locked;

static void *hold_fn(void *arg) {
    (void)arg;
    pthread_mutex_lock(&g_m);
    g_locked = 1;
    while (!g_go) {
        usleep(1000);
    }
    pthread_mutex_unlock(&g_m);
    return NULL;
}

static void *wait_lock_fn(void *arg) {
    (void)arg;
    pthread_mutex_lock(&g_m);
    pthread_mutex_unlock(&g_m);
    return NULL;
}

static volatile unsigned long g_dir;

static void *readdir_fn(void *arg) {
    (void)arg;
    for (int i = 0; i < 2000 && g_dir; i++) {
        readdir((DIR *)g_dir);
    }
    return NULL;
}

// PARTS picks what runs (a bit each: descriptors, threads, directories,
// mutexes, mappings; 0 for all).
static int handles(unsigned long f_thr, unsigned long f_mtx, unsigned long f_map,
                   unsigned long parts) {
    if (parts == 0) {
        parts = 0x1f;
    }
    int fd = -1;
    // descriptors: a closed one, then every number this process does not own
    if (parts & 1) {
        fd = open("wild.tmp", O_CREAT | O_WRONLY | O_TRUNC, 0644);
        if (fd < 0) {
            put("no file for the descriptors\n");
            return 2;
        }
        close(fd);
        unsigned long fds[] = { (unsigned long)fd, 3, 4, 5, 9, 30, 32, 33, 40, 63, 64, 1000,
                                (unsigned long)-2, 0x7fffffff };
        for (unsigned ri = 0; ri < WILD_NROWS; ri++) {
            const wild_row_t *r = &WILD_ROWS[ri];
            for (int j = 0; j < r->n; j++) {
                if (r->kind[j] != A_FD) {
                    continue;
                }
                for (unsigned v = 0; v < sizeof fds / sizeof fds[0]; v++) {
                    unsigned long a[8];
                    args_of(r, -1, 0, a);
                    a[j] = fds[v];
                    if (strcmp(r->name, "mmap") == 0) {
                        a[1] = 4096;
                        a[3] = MAP_PRIVATE | MAP_ANONYMOUS;
                    }
                    long got = raw(r->row, a);
                    long ok = strcmp(r->name, "isatty") == 0 ? 0 : -1;
                    if (got != ok) {
                        bad(r->name, fds[v], got);
                    }
                }
            }
        }
        int fd2 = open("wild.tmp", O_RDONLY);
        char c;
        if (fd2 >= 0 && read(fd, &c, 1) < 0 && fd2 == fd) {
            bad("reused descriptor", (unsigned long)fd, -1);
        }
        close(fd2);
        unlink("wild.tmp");
        struct pollfd pf[2] = { { 30, POLLIN, 0 }, { 1000, POLLIN, 0 } };
        if (poll(pf, 2, 0) != 2 || !(pf[0].revents & POLLNVAL) || !(pf[1].revents & POLLNVAL)) {
            bad("poll of descriptors not held", 30, pf[0].revents);
        }
    }

    // threads: a joined one's id never names its slot's next thread
    if (parts & 2) {
        pthread_t t1, t2, t3;
        g_go = 0;
        pthread_create(&t1, NULL, benign_fn, NULL);
        pthread_join(t1, NULL);
        pthread_create(&t2, NULL, wait_fn, NULL);
        unsigned long tids[] = { (unsigned long)t1, 0, 1, 0x100, 0x101, 0x1ff, 0xffffffff,
                                 0x7fffff01, f_thr };
        for (unsigned v = 0; v < sizeof tids / sizeof tids[0]; v++) {
            if (tids[v] == (unsigned long)t2 || tids[v] == (unsigned long)pthread_self()) {
                continue;
            }
            int e = pthread_join((pthread_t)tids[v], NULL);
            if (e == 0) {
                bad("pthread_join", tids[v], e);
            }
            e = pthread_detach((pthread_t)tids[v]);
            if (e == 0) {
                bad("pthread_detach", tids[v], e);
            }
        }
        pthread_create(&t3, NULL, wait_fn, NULL);
        if (pthread_detach(t2) != 0) {
            bad("pthread_detach of a live thread", (unsigned long)t2, -1);
        }
        if (pthread_join(t2, NULL) == 0) {
            bad("pthread_join of a detached thread", (unsigned long)t2, 0);
        }
        g_go = 1;
        if (pthread_join(t3, NULL) != 0) {
            bad("pthread_join", (unsigned long)t3, -1);
        }
        if (pthread_join(t3, NULL) == 0) {
            bad("pthread_join twice", (unsigned long)t3, 0);
        }
    }

    // directories: a closed one, a reused slot, junk, a close under a read
    if (parts & 4) {
        DIR *d = opendir(".");
        closedir(d);
        DIR *d2 = opendir(".");
        unsigned long dirs[] = { (unsigned long)d, 0, 1, 0x101, 0xffff, 0xffffffff, f_map };
        for (unsigned v = 0; v < sizeof dirs / sizeof dirs[0]; v++) {
            if (dirs[v] == (unsigned long)d2) {
                continue;
            }
            if (readdir((DIR *)dirs[v]) != NULL) {
                bad("readdir", dirs[v], 0);
            }
            if (closedir((DIR *)dirs[v]) == 0) {
                bad("closedir", dirs[v], 0);
            }
        }
        closedir(d2);
        for (int round = 0; round < 8; round++) {
            g_dir = (unsigned long)opendir(".");
            pthread_t r;
            pthread_create(&r, NULL, readdir_fn, NULL);
            usleep(500);
            closedir((DIR *)g_dir);
            pthread_join(r, NULL);
        }
        g_dir = 0;
    }

    // mutexes: junk ids, a destroy while a thread waits on it
    if (parts & 8) {
        unsigned long mids[] = { f_mtx, 0x101, 0x1234, 0xffffff01 };
        for (unsigned v = 0; v < sizeof mids / sizeof mids[0]; v++) {
            pthread_mutex_t m;
            memset(&m, 0, sizeof m);
            *(uint32_t *)(void *)&m = (uint32_t)mids[v];
            pthread_mutex_trylock(&m);
            pthread_mutex_unlock(&m);
            pthread_mutex_destroy(&m);
        }
        pthread_mutex_init(&g_m, NULL);
        g_go = 0;
        g_locked = 0;
        pthread_t h, w;
        pthread_create(&h, NULL, hold_fn, NULL);
        while (!g_locked) {
            usleep(1000);
        }
        pthread_create(&w, NULL, wait_lock_fn, NULL);
        usleep(20000);
        if (pthread_mutex_destroy(&g_m) == 0) {
            bad("pthread_mutex_destroy under a wait", 0, 0);
        }
        g_go = 1;
        pthread_join(h, NULL);
        pthread_join(w, NULL);
        if (pthread_mutex_destroy(&g_m) != 0) {
            bad("pthread_mutex_destroy", 0, -1);
        }
    }

    // mappings: let go twice, another's, the process's other memory
    if (parts & 16) {
        void *m1 = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (m1 == MAP_FAILED || munmap(m1, 4096) != 0) {
            bad("mmap", (unsigned long)m1, -1);
        }
        unsigned long maps[] = { (unsigned long)m1, f_map, (unsigned long)g_ok,
                                 (unsigned long)&fd, KERNEL_RAM, g_psram, 0 };
        for (unsigned v = 0; v < sizeof maps / sizeof maps[0]; v++) {
            if (maps[v] != 0 && munmap((void *)maps[v], 4096) == 0) {
                bad("munmap", maps[v], 0);
            }
            freeaddrinfo((struct addrinfo *)maps[v]);
        }
    }
    put(g_bad ? "handles bad\n" : "handles ok\n");
    return g_bad ? 1 : 0;
}

// -- a stack pivot: sp at memory not the thread's, then a trap ---------------------

static volatile unsigned long g_sib;

static void *sib_fn(void *arg) {
    volatile char here[512];
    here[0] = 1;
    g_sib = (unsigned long)&here[256];
    while (!g_go) {
        usleep(1000);
    }
    return arg;
}

static const char pivot_msg[] = "pivot ecall ok\n";

static int pivot(unsigned long sp, const char *act) {
    unsigned row = row_named("write")->row;
    if (strcmp(act, "sib") == 0) {
        pthread_t t;
        g_go = 0;
        pthread_create(&t, NULL, sib_fn, NULL);
        while (!g_sib) {
            usleep(1000);
        }
        sp = g_sib & ~15ul;
        act = "spin";
    }
    if (strcmp(act, "ecall") == 0) {
        // the row on a wild sp, then a push through it
        __asm__ volatile (
            "mv   s1, sp\n\t"
            "mv   sp, %0\n\t"
            "li   a0, 1\n\t"
            "mv   a1, %1\n\t"
            "mv   a2, %2\n\t"
            "mv   t0, %3\n\t"
            "ecall\n\t"
            "sw   zero, -4(sp)\n\t"
            "mv   sp, s1\n\t"
            :: "r"(sp), "r"(pivot_msg), "r"(sizeof pivot_msg - 1), "r"(row)
            : "s1", "a0", "a1", "a2", "t0", "memory");
    } else if (strcmp(act, "ill") == 0) {
        __asm__ volatile ("mv s1, sp\n\tmv sp, %0\n\t.word 0\n\tmv sp, s1" :: "r"(sp)
                          : "s1", "memory");
    } else if (strcmp(act, "spin") == 0) {
        // long enough for the tick and the console's interrupts to land on it
        __asm__ volatile (
            "mv   s1, sp\n\t"
            "mv   sp, %0\n\t"
            "li   t1, 40000000\n\t"
            "1:\n\t"
            "addi t1, t1, -1\n\t"
            "bnez t1, 1b\n\t"
            "sw   zero, -4(sp)\n\t"
            "mv   sp, s1\n\t"
            :: "r"(sp) : "s1", "t1", "memory");
        g_go = 1;
        put("spun\n");
        return 0;
    } else {
        put("pivot ADDR ecall|ill|spin|sib\n");
        return 2;
    }
    put("survived\n");
    return 0;
}

// -- teardown churn: threads in rows while the process ends ------------------------

static pthread_mutex_t g_cm = PTHREAD_MUTEX_INITIALIZER;
static volatile unsigned long g_rounds;

static void *quick_fn(void *arg) {
    return arg;
}

static void *churn_fn(void *arg) {
    unsigned long kind = (unsigned long)arg;
    struct timespec ts = { 0, 1000000 };
    for (unsigned long i = 0;; i++) {
        switch ((kind + i) % 6) {
        case 0: {
            struct stat st;
            fstat(1, &st);              // a hop to the session's task
            break;
        }
        case 1:
            pthread_mutex_lock(&g_cm);
            nanosleep(&ts, NULL);
            pthread_mutex_unlock(&g_cm);
            break;
        case 2: {
            void *m = mmap(NULL, 512, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (m != MAP_FAILED) {
                memset(m, (int)i, 512);
                munmap(m, 512);
            }
            break;
        }
        case 3: {
            pthread_t t;
            if (pthread_create(&t, NULL, quick_fn, NULL) == 0) {
                pthread_join(t, NULL);
            }
            break;
        }
        case 4: {
            DIR *d = opendir(".");
            if (d != NULL) {
                readdir(d);
                closedir(d);
            }
            break;
        }
        default:
            if (kind == 5 && i > 40) {
                pthread_exit(NULL);     // a thread's own end while the process's runs
            }
            nanosleep(&ts, NULL);
            break;
        }
        g_rounds++;
    }
    return NULL;
}

static void *spin_fn(void *arg) {
    for (volatile unsigned long i = 0;; i++) {
    }
    return arg;
}

static int churn(unsigned long ms) {
    pthread_t t[6];
    for (unsigned long i = 0; i < 5; i++) {
        pthread_create(&t[i], NULL, churn_fn, (void *)i);
    }
    pthread_create(&t[5], NULL, spin_fn, NULL);
    if (ms == 0) {
        put("churning\n");
        pthread_join(t[0], NULL);       // until a kill
    }
    struct timespec ts = { (time_t)(ms / 1000), (long)(ms % 1000) * 1000000 };
    nanosleep(&ts, NULL);
    _exit(g_rounds > 0 ? 3 : 4);        // with the others inside rows
}

// -- a service that holds what another process attacks -----------------------------

static int hold(void) {
    uint8_t *m = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    pthread_t t;
    g_go = 0;
    pthread_create(&t, NULL, wait_fn, NULL);
    pthread_t s;
    pthread_create(&s, NULL, spin_fn, NULL);
    pthread_mutex_init(&g_m, NULL);
    pthread_mutex_lock(&g_m);
    if (m != MAP_FAILED) {
        memset(m, 0xa5, 4096);
    }
    put("hold ");
    hex((unsigned long)m);
    put(" ");
    hex((unsigned long)t);
    put(" ");
    hex(*(uint32_t *)(void *)&g_m);
    put("\n");
    for (;;) {
        struct pollfd pf = { EVQ_FD, POLLIN, 0 };
        if (poll(&pf, 1, 100) < 0 || (pf.revents & (POLLHUP | POLLNVAL))) {
            break;
        }
    }
    g_go = 1;
    return 0;
}

// -- the U-mode basics ------------------------------------------------------------

// Buffers of their own mappings (the heap maps 64 KB and up on its own),
// more of them than the PMP has slots, checked while the core switches.
#define BUFS 7
#define BUF_LEN (70 * 1024)

static int spin(unsigned long rounds, unsigned long seed) {
    uint32_t *b[BUFS];
    for (int i = 0; i < BUFS; i++) {
        b[i] = malloc(BUF_LEN);
        if (b[i] == NULL) {
            return 2;
        }
        for (unsigned k = 0; k < BUF_LEN / 4; k++) {
            b[i][k] = (uint32_t)(seed * 2654435761u + (unsigned)i * 977u + k);
        }
    }
    for (unsigned long r = 0; r < rounds; r++) {
        for (int i = 0; i < BUFS; i++) {
            unsigned k = (unsigned)((r * 7919u + (unsigned)i * 31u) % (BUF_LEN / 4));
            if (b[i][k] != (uint32_t)(seed * 2654435761u + (unsigned)i * 977u + k)) {
                return 1;
            }
        }
    }
    for (int i = 0; i < BUFS; i++) {
        free(b[i]);
    }
    return 0;
}

typedef struct {
    unsigned long rounds, seed;
    int bad;
} job_t;

static void *spin_thread(void *arg) {
    job_t *j = arg;
    j->bad = spin(j->rounds, j->seed);
    return NULL;
}

static unsigned long long now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000000000ull + (unsigned long long)ts.tv_nsec;
}

static void rate(const char *name, unsigned long long t0, unsigned long n) {
    unsigned long long ns10 = (now_ns() - t0) * 10ull / n;
    put(name);
    put(" ");
    num((unsigned long)(ns10 / 10));
    put(".");
    num((unsigned long)(ns10 % 10));
    put(" ns\n");
}

// What a row costs from U-mode, a call at a time: __errno's body is a load,
// so its time is the crossing's.
static void rows(unsigned long n) {
    static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
    struct timespec ts;
    unsigned long long t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        __asm__ volatile ("" ::: "memory");
    }
    rate("loop", t0, n);
    t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        (void)*(volatile int *)__errno();
    }
    rate("__errno", t0, n);
    t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        (void)pthread_self();
    }
    rate("pthread_self", t0, n);
    t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        (void)time(NULL);
    }
    rate("time", t0, n);
    t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        clock_gettime(CLOCK_MONOTONIC, &ts);
    }
    rate("clock_gettime", t0, n);
    t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        pthread_mutex_lock(&m);
        pthread_mutex_unlock(&m);
    }
    rate("mutex_lock+unlock", t0, n);
    t0 = now_ns();
    for (unsigned long i = 0; i < n; i++) {
        write(1, "", 0);
    }
    rate("write(0 bytes)", t0, n);
}

int main(int argc, char **argv) {
    if (argc == 1 && strcmp(argv[0], "elf.wild") == 0) {
        return hold();                  // started as a service
    }
    const char *mode = argc > 1 ? argv[1] : "";
    unsigned long a = argc > 2 ? parse(argv[2]) : 0;
    unsigned long b = argc > 3 ? parse(argv[3]) : 0;
    unsigned long c = argc > 4 ? parse(argv[4]) : 0;
    if (strcmp(mode, "store") == 0) {
        *(volatile uint32_t *)a = 0x5a5a5a5au;
    } else if (strcmp(mode, "load") == 0) {
        num(*(volatile uint32_t *)a);
        put("\n");
    } else if (strcmp(mode, "jump") == 0) {
        ((void (*)(void))a)();
    } else if (strcmp(mode, "row") == 0) {
        write(1, (const void *)a, 16);
    } else if (strcmp(mode, "ecall") == 0) {
        __asm__ volatile ("li t0, 999\n\tecall" ::: "t0", "a0", "memory");
    } else if (strcmp(mode, "csr") == 0) {
        unsigned long v;
        __asm__ volatile ("csrr %0, mstatus" : "=r"(v));
        num(v);
    } else if (strcmp(mode, "stack") == 0) {
        num((unsigned long)deep(0));
    } else if (strcmp(mode, "float") == 0) {
        volatile float x = 0.5f;
        float s = 0;
        for (unsigned long i = 0; i < a; i++) {
            s += x * 2.0f;
        }
        num((unsigned long)s);
        put("\n");
        return 0;
    } else if (strcmp(mode, "spin") == 0) {
        int bad = spin(a, b);
        put(bad ? "bad " : "ok ");
        num(b);
        put("\n");
        return bad;
    } else if (strcmp(mode, "rows") == 0) {
        rows(a);
        return 0;
    } else if (strcmp(mode, "spins") == 0) {
        pthread_t t[3];
        job_t j[3];
        for (int i = 0; i < 3; i++) {
            j[i].rounds = a;
            j[i].seed = b + (unsigned long)i;
            j[i].bad = 9;
            pthread_create(&t[i], NULL, spin_thread, &j[i]);
        }
        int bad = 0;
        for (int i = 0; i < 3; i++) {
            pthread_join(t[i], NULL);
            bad |= j[i].bad;
        }
        put(bad ? "bad\n" : "ok\n");
        return bad;
    } else if (strcmp(mode, "cases") == 0) {
        g_foreign = a;
        return sweep_list();
    } else if (strcmp(mode, "sweep") == 0) {
        // sweep CASE PSRAM FOREIGN: one case, which must end the process
        g_psram = b;
        g_foreign = c;
        map_init();
        return sweep_one((int)a);
    } else if (strcmp(mode, "nulls") == 0) {
        return sweep_nulls();
    } else if (strcmp(mode, "zeros") == 0) {
        return sweep_zeros();
    } else if (strcmp(mode, "handles") == 0) {
        // handles THREAD MUTEX MAP: another process's, for the foreign cases
        g_psram = KERNEL_RAM;
        return handles(a, b, c, argc > 5 ? parse(argv[5]) : 0);
    } else if (strcmp(mode, "unmapped") == 0) {
        volatile uint32_t *m = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        m[0] = 7;
        munmap((void *)m, 4096);
        num(m[0]);
    } else if (strcmp(mode, "pivot") == 0) {
        return pivot(a, argc > 3 ? argv[3] : "");
    } else if (strcmp(mode, "churn") == 0) {
        return churn(a);
    } else {
        put("usage: wild store|load|jump|row ADDR, ecall, csr, stack, float N, spin N SEED, "
            "spins N SEED, rows N, cases [FOREIGN], sweep CASE PSRAM FOREIGN, nulls, zeros, "
            "handles THREAD MUTEX MAP [PARTS], unmapped, pivot ADDR ecall|ill|spin|sib, churn MS\n");
        return 2;
    }
    put("survived\n");
    return 0;
}
