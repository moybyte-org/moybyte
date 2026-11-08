// stdlib (sdk/README.md): numbers from text, the heap over the OS table's
// mappings, the exit path, rand, and strdup.
#include "lc.h"

#include <limits.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

// -- numbers ----------------------------------------------------------------------------

static int space(int c) {
    return c == ' ' || (c >= '\t' && c <= '\r');
}

static int digit(int c, int base) {
    int v = c >= '0' && c <= '9' ? c - '0'
          : c >= 'a' && c <= 'z' ? c - 'a' + 10
          : c >= 'A' && c <= 'Z' ? c - 'A' + 10 : 99;
    return v < base ? v : -1;
}

LC_API unsigned long long lc_parse_u(const char *s, char **end, int base, int *neg, int *over) {
    const char *p = s;
    *neg = 0;
    *over = 0;
    while (space((unsigned char)*p)) {
        p++;
    }
    if (*p == '-' || *p == '+') {
        *neg = *p++ == '-';
    }
    if ((base == 0 || base == 16) && p[0] == '0' && (p[1] | 0x20) == 'x' && digit(p[2], 16) >= 0) {
        p += 2;
        base = 16;
    } else if (base == 0) {
        base = p[0] == '0' ? 8 : 10;
    }
    unsigned long long v = 0;
    const char *first = p;
    int d;
    while ((d = digit((unsigned char)*p, base)) >= 0) {
        if (v > (ULLONG_MAX - (unsigned)d) / (unsigned)base) {
            *over = 1;
        }
        v = v * (unsigned)base + (unsigned)d;
        p++;
    }
    if (end != NULL) {
        *end = (char *)(p == first ? s : p);
    }
    return *over ? ULLONG_MAX : v;
}

static long long to_signed(const char *s, char **end, int base, long long lo, long long hi) {
    int neg, over;
    unsigned long long u = lc_parse_u(s, end, base, &neg, &over);
    if (over || (!neg && u > (unsigned long long)hi)
        || (neg && u > (unsigned long long)hi + 1u)) {
        errno = ERANGE;
        return neg ? lo : hi;
    }
    return neg ? (long long)(0ULL - u) : (long long)u;
}

static unsigned long long to_unsigned(const char *s, char **end, int base,
                                      unsigned long long hi) {
    int neg, over;
    unsigned long long u = lc_parse_u(s, end, base, &neg, &over);
    if (over || u > hi) {
        errno = ERANGE;
        return hi;
    }
    return neg ? 0ULL - u : u;
}

LC_API long lc_strtol(const char *s, char **end, int base) {
    return (long)to_signed(s, end, base, LONG_MIN, LONG_MAX);
}

LC_API long long lc_strtoll(const char *s, char **end, int base) {
    return to_signed(s, end, base, LLONG_MIN, LLONG_MAX);
}

LC_API unsigned long lc_strtoul(const char *s, char **end, int base) {
    return (unsigned long)to_unsigned(s, end, base, ULONG_MAX);
}

LC_API unsigned long long lc_strtoull(const char *s, char **end, int base) {
    return to_unsigned(s, end, base, ULLONG_MAX);
}

#define PARSER(name, impl) LC_EXPORT(name, impl); LC_EXPORT(__isoc23_##name, impl)
PARSER(strtol, lc_strtol);
PARSER(strtoll, lc_strtoll);
PARSER(strtoul, lc_strtoul);
PARSER(strtoull, lc_strtoull);
PARSER(strtoimax, lc_strtoll);
PARSER(strtoumax, lc_strtoull);

LC_API int atoi(const char *s) {
    return (int)lc_strtol(s, NULL, 10);
}

LC_API long atol(const char *s) {
    return lc_strtol(s, NULL, 10);
}

LC_API long long atoll(const char *s) {
    return lc_strtoll(s, NULL, 10);
}

static const double k_tens[] = { 1e1, 1e2, 1e4, 1e8, 1e16, 1e32, 1e64, 1e128, 1e256 };

static double scale10(double v, int e) {
    int up = e > 0;
    unsigned u = (unsigned)(up ? e : -e);
    for (int i = 0; u && i < 9; i++, u >>= 1) {
        if (u & 1) {
            v = up ? v * k_tens[i] : v / k_tens[i];
        }
    }
    if (u) {
        v = up ? v * 1e256 * 1e256 : v / 1e256 / 1e256;
    }
    return v;
}

static int word(const char *p, const char *w) {
    int n = 0;
    while (w[n] && (p[n] | 0x20) == w[n]) {
        n++;
    }
    return w[n] == '\0' ? n : 0;
}

LC_API double lc_strtod(const char *s, char **end) {
    const char *p = s;
    while (space((unsigned char)*p)) {
        p++;
    }
    int neg = 0;
    if (*p == '-' || *p == '+') {
        neg = *p++ == '-';
    }
    int k;
    double v;
    if ((k = word(p, "infinity")) || (k = word(p, "inf"))) {
        v = __builtin_inf();
        p += k;
    } else if ((k = word(p, "nan"))) {
        v = __builtin_nan("");
        p += k;
    } else if (p[0] == '0' && (p[1] | 0x20) == 'x' && digit(p[2], 16) >= 0) {
        int n, o;
        v = (double)lc_parse_u(p, (char **)&p, 16, &n, &o);
    } else {
        unsigned long long m = 0;
        int exp = 0, digits = 0, any = 0;
        for (; *p >= '0' && *p <= '9'; p++, any = 1) {
            if (digits < 19) {
                m = m * 10u + (unsigned)(*p - '0');
                digits += m != 0;
            } else {
                exp++;
            }
        }
        if (*p == '.') {
            p++;
            for (; *p >= '0' && *p <= '9'; p++, any = 1) {
                if (digits < 19) {
                    m = m * 10u + (unsigned)(*p - '0');
                    digits += m != 0;
                    exp--;
                }
            }
        }
        if (!any) {
            if (end != NULL) {
                *end = (char *)s;
            }
            return 0;
        }
        if ((*p | 0x20) == 'e') {
            const char *q = p + 1;
            int eneg = 0, e = 0;
            if (*q == '-' || *q == '+') {
                eneg = *q++ == '-';
            }
            if (*q >= '0' && *q <= '9') {
                for (; *q >= '0' && *q <= '9'; q++) {
                    if (e < 100000) {
                        e = e * 10 + (*q - '0');
                    }
                }
                exp += eneg ? -e : e;
                p = q;
            }
        }
        v = (double)m;
        if (m != 0) {
            if (exp < -300) {
                v = scale10(scale10(v, -300), exp + 300);
            } else {
                v = scale10(v, exp);
            }
            if (v == 0 || v > 1.7976931348623157e308) {
                errno = ERANGE;
            }
        }
    }
    if (end != NULL) {
        *end = (char *)p;
    }
    return neg ? -v : v;
}

LC_API double strtod(const char *s, char **end) {
    return lc_strtod(s, end);
}

LC_API float strtof(const char *s, char **end) {
    return (float)lc_strtod(s, end);
}

LC_API long double strtold(const char *s, char **end) {
    return lc_strtod(s, end);
}

LC_API double atof(const char *s) {
    return lc_strtod(s, NULL);
}


// -- the heap --------------------------------------------------------------------------------
//
// Blocks in arenas the OS maps (mmap, swept with the process however it
// ends): a header before each, its size and its predecessor's, first fit
// from one free list, neighbours joined on free. A request of BIG bytes
// or more is a mapping of its own.

#define ALIGN (sizeof(void *) * 2)
#define USED ((size_t)1)
#define OWN ((size_t)2)                 // a mapping of its own
#define SIZE(b) ((b)->size & ~(ALIGN - 1))
#define ARENA_MIN ((size_t)16 * 1024)
#define ARENA_MAX ((size_t)256 * 1024)
#define BIG ((size_t)64 * 1024)

typedef struct blk {
    size_t prev;                        // the previous block's size, 0 for an arena's first
    size_t size;                        // with this, USED and OWN in the low bits
    struct blk *fnext, *fprev;          // a free block's links (the payload's first bytes)
} blk_t;

#define HDR (2 * sizeof(size_t) < ALIGN ? ALIGN : 2 * sizeof(size_t))
#define MIN_BLK ((sizeof(blk_t) + ALIGN - 1) & ~(ALIGN - 1))

typedef struct arena {
    struct arena *next;
    size_t len;
} arena_t;

#define ARENA_HDR ((sizeof(arena_t) + ALIGN - 1) & ~(ALIGN - 1))

static pthread_mutex_t s_heap = PTHREAD_MUTEX_INITIALIZER;
static blk_t *s_free;
static arena_t *s_arenas;
static size_t s_next_arena = ARENA_MIN;

static blk_t *next_of(blk_t *b) {
    return (blk_t *)((char *)b + SIZE(b));
}

static void unlink_free(blk_t *b) {
    if (b->fprev != NULL) {
        b->fprev->fnext = b->fnext;
    } else {
        s_free = b->fnext;
    }
    if (b->fnext != NULL) {
        b->fnext->fprev = b->fprev;
    }
}

static void push_free(blk_t *b) {
    b->size &= ~USED;
    b->fprev = NULL;
    b->fnext = s_free;
    if (s_free != NULL) {
        s_free->fprev = b;
    }
    s_free = b;
}

static void *map(size_t n) {
    void *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return p == MAP_FAILED ? NULL : p;
}

static int grow(size_t need) {
    size_t len = s_next_arena;
    while (len < need + ARENA_HDR + HDR) {
        len *= 2;
    }
    arena_t *a = map(len);
    if (a == NULL) {
        return -1;
    }
    if (s_next_arena < ARENA_MAX) {
        s_next_arena *= 2;
    }
    a->len = len;
    a->next = s_arenas;
    s_arenas = a;
    blk_t *b = (blk_t *)((char *)a + ARENA_HDR);
    size_t body = (len - ARENA_HDR - HDR) & ~(ALIGN - 1);
    b->prev = 0;
    b->size = body;
    blk_t *end = next_of(b);
    end->prev = body;
    end->size = USED;                   // the arena's end: a used block of size 0
    push_free(b);
    return 0;
}

// Split `b` (unlinked, in use) to `n` bytes when the rest makes a block.
static void trim(blk_t *b, size_t n) {
    size_t have = SIZE(b);
    if (have - n < MIN_BLK) {
        return;
    }
    b->size = n | (b->size & (ALIGN - 1));
    blk_t *r = next_of(b);
    r->prev = n;
    r->size = have - n;
    next_of(r)->prev = have - n;
    blk_t *nx = next_of(r);
    if (!(nx->size & USED)) {
        unlink_free(nx);
        r->size += SIZE(nx);
        next_of(r)->prev = SIZE(r);
    }
    push_free(r);
}

static size_t need_of(size_t n) {
    if (n > ((size_t)-1) / 2) {
        return 0;
    }
    size_t k = (n + HDR + ALIGN - 1) & ~(ALIGN - 1);
    return k < MIN_BLK ? MIN_BLK : k;
}

static void *alloc_locked(size_t n) {
    size_t k = need_of(n);
    if (k == 0) {
        return NULL;
    }
    if (k >= BIG) {
        blk_t *b = map(k);
        if (b == NULL) {
            return NULL;
        }
        b->prev = 0;
        b->size = k | USED | OWN;
        return (char *)b + HDR;
    }
    for (int tries = 0; tries < 2; tries++) {
        for (blk_t *b = s_free; b != NULL; b = b->fnext) {
            if (SIZE(b) >= k) {
                unlink_free(b);
                b->size |= USED;
                trim(b, k);
                return (char *)b + HDR;
            }
        }
        if (tries == 0 && grow(k) != 0) {
            break;
        }
    }
    return NULL;
}

// The arena `b` is the whole of, handed back to the OS: never the last one.
static void release_arena(blk_t *b) {
    arena_t *a = (arena_t *)((char *)b - ARENA_HDR);
    if (s_arenas == a && a->next == NULL) {
        return;
    }
    unlink_free(b);
    for (arena_t **at = &s_arenas; *at != NULL; at = &(*at)->next) {
        if (*at == a) {
            *at = a->next;
            break;
        }
    }
    munmap(a, a->len);
}

static void free_locked(void *p) {
    blk_t *b = (blk_t *)((char *)p - HDR);
    if (b->size & OWN) {
        munmap(b, SIZE(b));
        return;
    }
    blk_t *nx = next_of(b);
    if (!(nx->size & USED)) {
        unlink_free(nx);
        b->size = (b->size & (ALIGN - 1)) | (SIZE(b) + SIZE(nx));
        next_of(b)->prev = SIZE(b);
    }
    if (b->prev != 0) {
        blk_t *pv = (blk_t *)((char *)b - b->prev);
        if (!(pv->size & USED)) {
            unlink_free(pv);
            pv->size = SIZE(pv) + SIZE(b);
            next_of(pv)->prev = SIZE(pv);
            b = pv;
        }
    }
    push_free(b);
    if (b->prev == 0 && next_of(b)->size == USED) {
        release_arena(b);
    }
}

LC_API void *malloc(size_t n) {
    pthread_mutex_lock(&s_heap);
    void *p = alloc_locked(n);
    pthread_mutex_unlock(&s_heap);
    if (p == NULL) {
        errno = ENOMEM;
    }
    return p;
}

LC_API void free(void *p) {
    if (p == NULL) {
        return;
    }
    pthread_mutex_lock(&s_heap);
    free_locked(p);
    pthread_mutex_unlock(&s_heap);
}

LC_API void *calloc(size_t n, size_t size) {
    if (size != 0 && n > ((size_t)-1) / size) {
        errno = ENOMEM;
        return NULL;
    }
    void *p = malloc(n * size);
    if (p != NULL) {
        memset(p, 0, n * size);
    }
    return p;
}

LC_API void *realloc(void *p, size_t n) {
    if (p == NULL) {
        return malloc(n);
    }
    if (n == 0) {
        free(p);
        return NULL;
    }
    size_t k = need_of(n);
    if (k == 0) {
        errno = ENOMEM;
        return NULL;
    }
    blk_t *b = (blk_t *)((char *)p - HDR);
    pthread_mutex_lock(&s_heap);
    if (!(b->size & OWN)) {
        if (SIZE(b) < k) {
            blk_t *nx = next_of(b);
            if (!(nx->size & USED) && SIZE(b) + SIZE(nx) >= k) {
                unlink_free(nx);
                b->size = (b->size & (ALIGN - 1)) | (SIZE(b) + SIZE(nx));
                next_of(b)->prev = SIZE(b);
            }
        }
        if (SIZE(b) >= k) {
            trim(b, k);
            pthread_mutex_unlock(&s_heap);
            return p;
        }
    } else if (SIZE(b) >= k && SIZE(b) - k < BIG) {
        pthread_mutex_unlock(&s_heap);
        return p;
    }
    size_t had = SIZE(b) - HDR;
    void *q = alloc_locked(n);
    if (q != NULL) {
        memcpy(q, p, had < n ? had : n);
        free_locked(p);
    }
    pthread_mutex_unlock(&s_heap);
    if (q == NULL) {
        errno = ENOMEM;
    }
    return q;
}

LC_API void *reallocarray(void *p, size_t n, size_t size) {
    if (size != 0 && n > ((size_t)-1) / size) {
        errno = ENOMEM;
        return NULL;
    }
    return realloc(p, n * size);
}

LC_API int posix_memalign(void **out, size_t align, size_t n) {
    if (align < sizeof(void *) || (align & (align - 1)) != 0) {
        return EINVAL;
    }
    if (align > ALIGN) {
        return ENOMEM;
    }
    *out = malloc(n);
    return *out != NULL ? 0 : ENOMEM;
}

// -- strings the heap makes ------------------------------------------------------------------

LC_API char *strdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *p = malloc(n);
    if (p != NULL) {
        memcpy(p, s, n);
    }
    return p;
}

LC_API char *strndup(const char *s, size_t n) {
    size_t k = strnlen(s, n);
    char *p = malloc(k + 1);
    if (p != NULL) {
        memcpy(p, s, k);
        p[k] = '\0';
    }
    return p;
}

// -- the exit path ----------------------------------------------------------------------------

#define ATEXIT 32

static void (*s_atexit[ATEXIT])(void);
static int s_atexit_n;

LC_API int atexit(void (*fn)(void)) {
    if (s_atexit_n >= ATEXIT) {
        return -1;
    }
    s_atexit[s_atexit_n++] = fn;
    return 0;
}

LC_API void __moy_exit(void) {
    while (s_atexit_n > 0) {
        s_atexit[--s_atexit_n]();
    }
    lc_flush_all();
}

LC_API void abort(void) {
    lc_flush_all();
    _exit(134);
}

LC_API void _Exit(int code) {
    _exit(code);
}

#if defined(LC_NEWLIB)
LC_API void __assert_func(const char *file, int line, const char *fn, const char *expr) {
    fprintf(stderr, "assertion \"%s\" failed: file \"%s\", line %d%s%s\n", expr, file, line,
            fn != NULL ? ", function: " : "", fn != NULL ? fn : "");
    abort();
}
#else
LC_API void __assert_fail(const char *expr, const char *file, unsigned line, const char *fn) {
    fprintf(stderr, "%s:%u: %s: Assertion `%s' failed.\n", file, line, fn != NULL ? fn : "?",
            expr);
    abort();
}
#endif

// -- rand ---------------------------------------------------------------------------------

static unsigned long long s_rand = 1;

LC_API void srand(unsigned seed) {
    s_rand = seed;
}

LC_API int rand(void) {
    s_rand = s_rand * 6364136223846793005ULL + 1u;
    return (int)((s_rand >> 33) & RAND_MAX);
}

LC_API long random(void) {
    return rand();
}

LC_API void srandom(unsigned seed) {
    srand(seed);
}
