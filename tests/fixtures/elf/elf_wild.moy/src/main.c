// wild: a native module that misbehaves on request, for the P4's U-mode
// isolation (native/moy_elf/moy_umode.h, tests/on_glass.py's elf_umode).
// Each wild mode should end the process with a fault record and leave the
// console running; the others check what U-mode must keep working.
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void put(const char *s) {
    write(1, s, strlen(s));
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
    const char *mode = argc > 1 ? argv[1] : "";
    unsigned long a = argc > 2 ? parse(argv[2]) : 0;
    unsigned long b = argc > 3 ? parse(argv[3]) : 0;
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
    } else {
        put("usage: wild store|load|jump|row ADDR, ecall, csr, stack, float N, spin N SEED, "
            "spins N SEED, rows N\n");
        return 2;
    }
    put("survived\n");
    return 0;
}
