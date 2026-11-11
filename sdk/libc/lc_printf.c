// printf's family (sdk/README.md): one formatter over a sink, every
// conversion C99 names but %a (printed as %e) and positional arguments.
// Floating point is exact, rounded half to even, as glibc prints it.
#include "lc.h"

#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define F_MINUS 1
#define F_PLUS 2
#define F_SPACE 4
#define F_HASH 8
#define F_ZERO 16

typedef struct {
    lc_out_fn out;
    void *arg;
    int n;
    int bad;
} sink_t;

static void put(sink_t *s, const char *p, size_t n) {
    if (n == 0 || s->bad) {
        return;
    }
    if (s->out(s->arg, p, n) != 0) {
        s->bad = 1;
        return;
    }
    s->n += (int)n;
}

static void pad(sink_t *s, char c, int n) {
    char b[16];
    memset(b, c, sizeof b);
    while (n > 0) {
        int k = n > (int)sizeof b ? (int)sizeof b : n;
        put(s, b, (size_t)k);
        n -= k;
    }
}

// A converted body: sign or prefix, `zeros` leading zeros, the digits.
static void emit(sink_t *s, int flags, int width, const char *pre, int zeros, const char *body,
                 int blen) {
    int plen = (int)strlen(pre);
    int len = plen + zeros + blen;
    if (!(flags & F_MINUS) && !(flags & F_ZERO)) {
        pad(s, ' ', width - len);
    }
    put(s, pre, (size_t)plen);
    if (!(flags & F_MINUS) && (flags & F_ZERO)) {
        pad(s, '0', width - len);
    }
    pad(s, '0', zeros);
    put(s, body, (size_t)blen);
    if (flags & F_MINUS) {
        pad(s, ' ', width - len);
    }
}

static void integer(sink_t *s, int flags, int width, int prec, unsigned long long v, int neg,
                    int base, int upper, int conv) {
    char b[24];
    int i = sizeof b;
    const char *dig = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    while (v) {
        b[--i] = dig[v % (unsigned)base];
        v /= (unsigned)base;
    }
    int blen = (int)sizeof b - i;
    const char *pre = "";
    if (conv == 'd' || conv == 'i') {
        pre = neg ? "-" : (flags & F_PLUS) ? "+" : (flags & F_SPACE) ? " " : "";
    } else if ((flags & F_HASH) && base == 16 && blen) {
        pre = upper ? "0X" : "0x";
    } else if (conv == 'p') {
        pre = "0x";
    }
    int zeros = prec > blen ? prec - blen : 0;
    if ((flags & F_HASH) && base == 8 && zeros == 0 && (blen == 0 || b[i] != '0')) {
        zeros = 1;
    }
    if (prec < 0 && blen == 0) {
        zeros = 1;      // 0 with no precision prints "0"
    }
    if (prec >= 0) {
        flags &= ~F_ZERO;
    }
    emit(s, flags, width, pre, zeros, b + i, blen);
}

// -- floating point -------------------------------------------------------------------------
//
// Exact: a double is m * 2^e, so its integer part is a big integer printed
// by division and its fraction a big binary fraction whose digits come out
// one multiplication by ten at a time. The digits a conversion keeps are
// rounded on the exact rest, half to even.

#define WORDS 36                    // 1152 bits: a double's integer part or fraction
#define MAXD 760                    // digits a conversion keeps, and its round digit

typedef struct {
    uint32_t w[WORDS];
    int n;                          // words in use
} big_t;

typedef struct {
    char d[MAXD + 2];
    int n;                          // digits kept
    int point;                      // digits before the decimal point (may be <= 0)
    big_t frac;                     // the fraction not yet turned into digits
    int fbits;
    char ip[320];                   // the integer part's digits, most significant first
    int ipn, ipat;
} digits_t;

static int big_zero(const big_t *b) {
    for (int i = 0; i < b->n; i++) {
        if (b->w[i]) {
            return 0;
        }
    }
    return 1;
}

// The fraction's next digit.
static int frac_digit(digits_t *g) {
    big_t *b = &g->frac;
    uint32_t carry = 0;
    for (int i = 0; i < b->n; i++) {
        uint64_t v = (uint64_t)b->w[i] * 10u + carry;
        b->w[i] = (uint32_t)v;
        carry = (uint32_t)(v >> 32);
    }
    if (carry && b->n < WORDS) {
        b->w[b->n++] = carry;
    }
    int word = g->fbits / 32, bit = g->fbits % 32;
    uint32_t top = 0;
    if (word < b->n) {
        top = b->w[word] >> bit;
        b->w[word] &= bit ? (1u << bit) - 1u : 0u;
    }
    if (bit && word + 1 < b->n) {
        top |= b->w[word + 1] << (32 - bit);
        b->w[word + 1] = 0;
    } else if (!bit && word + 1 < b->n) {
        b->w[word + 1] = 0;
    }
    return (int)(top & 15u);
}

// x (finite, >= 0) split: its integer part's digits and its fraction.
static void split(digits_t *g, double x) {
    uint64_t u;
    memcpy(&u, &x, sizeof u);
    int ex = (int)((u >> 52) & 0x7ff);
    uint64_t m = u & ((1ULL << 52) - 1u);
    if (ex) {
        m |= 1ULL << 52;
    }
    int e2 = (ex ? ex : 1) - 1075;
    big_t ib;
    memset(&ib, 0, sizeof ib);
    memset(&g->frac, 0, sizeof g->frac);
    g->fbits = 0;
    if (e2 >= 0) {
        int sh = e2 % 32, at = e2 / 32;
        uint64_t lo = m << sh;
        uint32_t hi = sh ? (uint32_t)(m >> (64 - sh)) : 0u;
        ib.w[at] = (uint32_t)lo;
        ib.w[at + 1] = (uint32_t)(lo >> 32);
        ib.w[at + 2] = hi;
        ib.n = at + 3;
    } else {
        int fb = -e2;
        uint64_t ipart = fb < 64 ? m >> fb : 0;
        uint64_t f = fb < 64 ? m & ((1ULL << fb) - 1u) : m;
        ib.w[0] = (uint32_t)ipart;
        ib.w[1] = (uint32_t)(ipart >> 32);
        ib.n = 2;
        g->frac.w[0] = (uint32_t)f;
        g->frac.w[1] = (uint32_t)(f >> 32);
        g->frac.n = (fb + 4) / 32 + 2 < WORDS ? (fb + 4) / 32 + 2 : WORDS;
        g->fbits = fb;
    }
    // The integer part in decimal: nine digits a division by 1e9.
    char rev[320];
    int r = 0;
    while (!big_zero(&ib)) {
        uint64_t rem = 0;
        for (int i = ib.n - 1; i >= 0; i--) {
            uint64_t v = rem << 32 | ib.w[i];
            ib.w[i] = (uint32_t)(v / 1000000000u);
            rem = v % 1000000000u;
        }
        for (int k = 0; k < 9; k++) {
            rev[r++] = (char)('0' + rem % 10u);
            rem /= 10u;
        }
    }
    while (r > 0 && rev[r - 1] == '0') {
        r--;
    }
    g->ipn = r;
    for (int i = 0; i < r; i++) {
        g->ip[i] = rev[r - 1 - i];
    }
    g->ipat = 0;
}

static int next_digit(digits_t *g) {
    return g->ipat < g->ipn ? g->ip[g->ipat++] - '0' : frac_digit(g);
}

static int rest_nonzero(digits_t *g) {
    for (int i = g->ipat; i < g->ipn; i++) {
        if (g->ip[i] != '0') {
            return 1;
        }
    }
    return !big_zero(&g->frac);
}

// Keep `keep` digits from the stream's current place, rounded on the rest.
static void take(digits_t *g, int keep) {
    if (keep > MAXD) {
        keep = MAXD;
    }
    for (int i = 0; i < keep; i++) {
        g->d[g->n++] = (char)('0' + next_digit(g));
    }
    int r = next_digit(g);
    int up = r > 5 || (r == 5 && (rest_nonzero(g) || (g->n > 0 && ((g->d[g->n - 1] - '0') & 1))));
    if (r == 5 && g->n == 0 && !rest_nonzero(g)) {
        up = 0;                     // exactly half of the first kept place: to even, 0
    }
    if (!up) {
        return;
    }
    int i = g->n - 1;
    while (i >= 0 && g->d[i] == '9') {
        g->d[i--] = '0';
    }
    if (i >= 0) {
        g->d[i]++;
    } else {
        memmove(g->d + 1, g->d, (size_t)g->n);
        g->d[0] = '1';
        g->n++;
        g->point++;
    }
}

// The digits for %f with `prec` places: point = the integer digits.
static void fixed(digits_t *g, double x, int prec) {
    split(g, x);
    g->n = 0;
    g->point = g->ipn;
    take(g, g->ipn + prec);
}

// The digits for %e with `sig` significant digits: point = the exponent + 1.
static void sci(digits_t *g, double x, int sig) {
    split(g, x);
    g->n = 0;
    if (x == 0) {
        memset(g->d, '0', (size_t)sig);
        g->n = sig;
        g->point = 1;
        return;
    }
    int point = g->ipn;
    if (g->ipn == 0) {
        int c;
        while ((c = frac_digit(g)) == 0) {
            point--;
        }
        g->d[g->n++] = (char)('0' + c);
    }
    g->point = point;
    take(g, sig - g->n);
    if (g->n > sig) {
        g->n = sig;                 // a carry out of the first digit made one more
    }
}

static void floating(sink_t *s, int flags, int width, int prec, double x, int conv) {
    int upper = conv == 'F' || conv == 'E' || conv == 'G' || conv == 'A';
    int neg = __builtin_signbit(x) != 0;
    const char *sign = neg ? "-" : (flags & F_PLUS) ? "+" : (flags & F_SPACE) ? " " : "";
    if (neg) {
        x = -x;
    }
    if (x != x || x > 1.7976931348623157e308) {
        const char *w = x != x ? (upper ? "NAN" : "nan") : (upper ? "INF" : "inf");
        emit(s, flags & ~F_ZERO, width, sign, 0, w, 3);
        return;
    }
    if (prec < 0) {
        prec = 6;
    }
    if (prec > 340) {
        prec = 340;
    }
    digits_t g;
    int lower = conv | 0x20;
    int strip = 0;
    if (lower == 'a') {
        lower = 'e';
    }
    if (lower == 'g') {
        int p = prec == 0 ? 1 : prec;
        sci(&g, x, p);
        int X = x == 0 ? 0 : g.point - 1;
        if (p > X && X >= -4) {
            lower = 'f';
            prec = p - 1 - X;
        } else {
            lower = 'e';
            prec = p - 1;
        }
        strip = !(flags & F_HASH);
    }
    char body[MAXD + 16];
    int n = 0;
    if (lower == 'e') {
        sci(&g, x, prec + 1);
        int e10 = x == 0 ? 0 : g.point - 1;
        body[n++] = g.d[0];
        int frac = prec;
        if (strip) {
            while (frac > 0 && g.d[frac] == '0') {
                frac--;
            }
        }
        if (frac > 0 || (flags & F_HASH)) {
            body[n++] = '.';
        }
        for (int i = 1; i <= frac; i++) {
            body[n++] = g.d[i];
        }
        body[n++] = upper ? 'E' : 'e';
        body[n++] = e10 < 0 ? '-' : '+';
        unsigned ue = (unsigned)(e10 < 0 ? -e10 : e10);
        char eb[4];
        int k = 0;
        do {
            eb[k++] = (char)('0' + ue % 10);
            ue /= 10;
        } while (ue);
        if (k < 2) {
            eb[k++] = '0';
        }
        while (k) {
            body[n++] = eb[--k];
        }
    } else {
        fixed(&g, x, prec);
        int ip = g.point;
        if (ip <= 0) {
            body[n++] = '0';
        }
        for (int i = 0; i < ip; i++) {
            body[n++] = g.d[i];
        }
        int frac = prec;
        if (strip) {
            while (frac > 0 && g.d[ip + frac - 1] == '0') {
                frac--;
            }
        }
        if (frac > 0 || (flags & F_HASH)) {
            body[n++] = '.';
        }
        for (int k = 0; k < frac; k++) {
            body[n++] = g.d[ip + k];
        }
    }
    emit(s, flags, width, sign, 0, body, n);
}

// -- the formatter ----------------------------------------------------------------------

enum { L_NONE, L_HH, L_H, L_L, L_LL, L_J, L_Z, L_T, L_BIG };

LC_API int lc_format(lc_out_fn out, void *arg, const char *fmt, va_list ap) {
    sink_t s = { out, arg, 0, 0 };
    va_list a;
    va_copy(a, ap);
    while (*fmt) {
        const char *p = fmt;
        while (*p && *p != '%') {
            p++;
        }
        put(&s, fmt, (size_t)(p - fmt));
        if (*p == '\0') {
            break;
        }
        fmt = p + 1;
        int flags = 0;
        for (;; fmt++) {
            if (*fmt == '-') {
                flags |= F_MINUS;
            } else if (*fmt == '+') {
                flags |= F_PLUS;
            } else if (*fmt == ' ') {
                flags |= F_SPACE;
            } else if (*fmt == '#') {
                flags |= F_HASH;
            } else if (*fmt == '0') {
                flags |= F_ZERO;
            } else {
                break;
            }
        }
        int width = 0;
        if (*fmt == '*') {
            width = va_arg(a, int);
            if (width < 0) {
                flags |= F_MINUS;
                width = -width;
            }
            fmt++;
        } else {
            while (*fmt >= '0' && *fmt <= '9') {
                width = width * 10 + (*fmt++ - '0');
            }
        }
        int prec = -1;
        if (*fmt == '.') {
            fmt++;
            prec = 0;
            if (*fmt == '*') {
                prec = va_arg(a, int);
                fmt++;
            } else {
                while (*fmt >= '0' && *fmt <= '9') {
                    prec = prec * 10 + (*fmt++ - '0');
                }
            }
        }
        if (flags & F_MINUS) {
            flags &= ~F_ZERO;
        }
        int len = L_NONE;
        switch (*fmt) {
        case 'h':
            len = fmt[1] == 'h' ? L_HH : L_H;
            fmt += len == L_HH ? 2 : 1;
            break;
        case 'l':
            len = fmt[1] == 'l' ? L_LL : L_L;
            fmt += len == L_LL ? 2 : 1;
            break;
        case 'q':
            len = L_LL;
            fmt++;
            break;
        case 'j':
            len = L_J;
            fmt++;
            break;
        case 'z':
            len = L_Z;
            fmt++;
            break;
        case 't':
            len = L_T;
            fmt++;
            break;
        case 'L':
            len = L_BIG;
            fmt++;
            break;
        }
        int conv = *fmt;
        if (conv == '\0') {
            break;
        }
        fmt++;
        switch (conv) {
        case 'd':
        case 'i': {
            long long v;
            switch (len) {
            case L_LL:
            case L_J:
                v = va_arg(a, long long);
                break;
            case L_L:
                v = va_arg(a, long);
                break;
            case L_Z:
            case L_T:
                v = (long long)va_arg(a, ptrdiff_t);
                break;
            default:
                v = va_arg(a, int);
                if (len == L_HH) {
                    v = (signed char)v;
                } else if (len == L_H) {
                    v = (short)v;
                }
            }
            unsigned long long u = v < 0 ? 0ULL - (unsigned long long)v : (unsigned long long)v;
            integer(&s, flags, width, prec, u, v < 0, 10, 0, conv);
            break;
        }
        case 'u':
        case 'o':
        case 'x':
        case 'X': {
            unsigned long long v;
            switch (len) {
            case L_LL:
            case L_J:
                v = va_arg(a, unsigned long long);
                break;
            case L_L:
                v = va_arg(a, unsigned long);
                break;
            case L_Z:
            case L_T:
                v = va_arg(a, size_t);
                break;
            default:
                v = va_arg(a, unsigned int);
                if (len == L_HH) {
                    v = (unsigned char)v;
                } else if (len == L_H) {
                    v = (unsigned short)v;
                }
            }
            integer(&s, flags, width, prec, v, 0, conv == 'o' ? 8 : conv == 'u' ? 10 : 16,
                    conv == 'X', conv);
            break;
        }
        case 'p': {
            void *v = va_arg(a, void *);
            if (v == NULL) {
                emit(&s, flags & ~F_ZERO, width, "", 0, "(nil)", 5);
            } else {
                integer(&s, flags, width, prec, (unsigned long long)(uintptr_t)v, 0, 16, 0, 'p');
            }
            break;
        }
        case 'c': {
            char c = (char)va_arg(a, int);
            emit(&s, flags & ~F_ZERO, width, "", 0, &c, 1);
            break;
        }
        case 's': {
            const char *v = va_arg(a, const char *);
            if (v == NULL) {
                v = "(null)";
            }
            int n = 0;
            while ((prec < 0 || n < prec) && v[n]) {
                n++;
            }
            emit(&s, flags & ~F_ZERO, width, "", 0, v, n);
            break;
        }
        case 'f':
        case 'F':
        case 'e':
        case 'E':
        case 'g':
        case 'G':
        case 'a':
        case 'A': {
            double v = len == L_BIG ? (double)va_arg(a, long double) : va_arg(a, double);
            floating(&s, flags, width, prec, v, conv);
            break;
        }
        case 'n': {
            void *v = va_arg(a, void *);
            switch (len) {
            case L_HH:
                *(signed char *)v = (signed char)s.n;
                break;
            case L_H:
                *(short *)v = (short)s.n;
                break;
            case L_L:
                *(long *)v = s.n;
                break;
            case L_LL:
            case L_J:
                *(long long *)v = s.n;
                break;
            case L_Z:
            case L_T:
                *(size_t *)v = (size_t)s.n;
                break;
            default:
                *(int *)v = s.n;
            }
            break;
        }
        case '%':
            put(&s, "%", 1);
            break;
        default:
            put(&s, fmt - 1, 1);
        }
    }
    va_end(a);
    return s.bad ? -1 : s.n;
}

// -- the family ---------------------------------------------------------------------------

static int to_file(void *arg, const char *p, size_t n) {
    return lc_write(arg, p, n) == n ? 0 : -1;
}

typedef struct {
    char *p;
    size_t cap;
    size_t n;
} mem_t;

static int to_mem(void *arg, const char *p, size_t n) {
    mem_t *m = arg;
    if (m->n + 1 < m->cap) {
        size_t k = m->cap - 1 - m->n;
        memcpy(m->p + m->n, p, n < k ? n : k);
    }
    m->n += n;
    return 0;
}

static int to_grow(void *arg, const char *p, size_t n) {
    mem_t *m = arg;
    if (m->n + n + 1 > m->cap) {
        size_t want = m->cap ? m->cap : 64;
        while (want < m->n + n + 1) {
            want *= 2;
        }
        char *g = realloc(m->p, want);
        if (g == NULL) {
            return -1;
        }
        m->p = g;
        m->cap = want;
    }
    memcpy(m->p + m->n, p, n);
    m->n += n;
    return 0;
}

LC_API int vfprintf(FILE *fp, const char *fmt, va_list ap) {
    return lc_format(to_file, LC_F(fp), fmt, ap);
}

LC_API int fprintf(FILE *fp, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = vfprintf(fp, fmt, ap);
    va_end(ap);
    return r;
}

LC_API int vprintf(const char *fmt, va_list ap) {
    return vfprintf(stdout, fmt, ap);
}

LC_API int printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = vfprintf(stdout, fmt, ap);
    va_end(ap);
    return r;
}

LC_API int vsnprintf(char *buf, size_t cap, const char *fmt, va_list ap) {
    mem_t m = { buf, cap, 0 };
    int r = lc_format(to_mem, &m, fmt, ap);
    if (cap) {
        buf[m.n < cap ? m.n : cap - 1] = '\0';
    }
    return r;
}

LC_API int snprintf(char *buf, size_t cap, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = vsnprintf(buf, cap, fmt, ap);
    va_end(ap);
    return r;
}

LC_API int vsprintf(char *buf, const char *fmt, va_list ap) {
    return vsnprintf(buf, (size_t)-1 / 2, fmt, ap);
}

LC_API int sprintf(char *buf, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = vsprintf(buf, fmt, ap);
    va_end(ap);
    return r;
}

LC_API int vasprintf(char **out, const char *fmt, va_list ap) {
    mem_t m = { NULL, 0, 0 };
    int r = lc_format(to_grow, &m, fmt, ap);
    if (r < 0 || to_grow(&m, "", 0) != 0) {
        free(m.p);
        *out = NULL;
        return -1;
    }
    m.p[m.n] = '\0';
    *out = m.p;
    return r;
}

LC_API int asprintf(char **out, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = vasprintf(out, fmt, ap);
    va_end(ap);
    return r;
}

static int to_fd(void *arg, const char *p, size_t n) {
    int fd = *(int *)arg;
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w <= 0) {
            return -1;
        }
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

LC_API int vdprintf(int fd, const char *fmt, va_list ap) {
    return lc_format(to_fd, &fd, fmt, ap);
}

LC_API int dprintf(int fd, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = vdprintf(fd, fmt, ap);
    va_end(ap);
    return r;
}
