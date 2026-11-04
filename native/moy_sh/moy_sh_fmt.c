// moy_sh_fmt: the shell's printf formatting, its integers its own.
//
// The boards' C library is newlib with the nano formatter, which has no
// 64-bit conversions: dash prints every number through intmax_t ("%jd",
// arithmetic, $?, printf's %d, the C built-ins' sizes), and nano printed "ld"
// for each. Every conversion an integer takes is done here, on every target
// alike; a floating conversion (printf's %f) is handed to the C library one
// at a time, and prints what that library can.

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "moy_sh_fmt.h"

typedef struct {
    char *buf;
    size_t cap, n;
} sink_t;

static void put(sink_t *k, char c) {
    if (k->n + 1 < k->cap) {
        k->buf[k->n] = c;
    }
    k->n++;
}

static void pad(sink_t *k, char c, int n) {
    while (n-- > 0) {
        put(k, c);
    }
}

enum { F_MINUS = 1, F_PLUS = 2, F_SPACE = 4, F_HASH = 8, F_ZERO = 16 };

// One integer conversion: `v` its magnitude, `neg` its sign.
static void integer(sink_t *k, uintmax_t v, int neg, char conv, int flags, int width,
                    int prec) {
    char digits[72];
    int nd = 0;
    unsigned base = conv == 'o' ? 8u : (conv == 'x' || conv == 'X' || conv == 'p') ? 16u : 10u;
    const char *set = conv == 'X' ? "0123456789ABCDEF" : "0123456789abcdef";
    while (v != 0) {
        digits[nd++] = set[v % base];
        v /= base;
    }
    int zeros = prec > nd ? prec - nd : 0;
    if (prec < 0 && nd == 0) {
        zeros = 1;                      // a plain 0
    }
    if (conv == 'o' && (flags & F_HASH) && zeros == 0 && (nd == 0 || digits[nd - 1] != '0')) {
        zeros = 1;
    }
    const char *prefix = "";
    if (neg) {
        prefix = "-";
    } else if (conv == 'd' || conv == 'i') {
        prefix = (flags & F_PLUS) ? "+" : (flags & F_SPACE) ? " " : "";
    } else if ((conv == 'x' || conv == 'X') && (flags & F_HASH) && nd > 0) {
        prefix = conv == 'x' ? "0x" : "0X";
    } else if (conv == 'p') {
        prefix = "0x";
    }
    int len = (int)strlen(prefix) + zeros + nd;
    int fill = width > len ? width - len : 0;
    if ((flags & F_ZERO) && !(flags & F_MINUS) && prec < 0) {
        zeros += fill;
        fill = 0;
    }
    if (!(flags & F_MINUS)) {
        pad(k, ' ', fill);
    }
    for (const char *p = prefix; *p; p++) {
        put(k, *p);
    }
    pad(k, '0', zeros);
    while (nd > 0) {
        put(k, digits[--nd]);
    }
    if (flags & F_MINUS) {
        pad(k, ' ', fill);
    }
}

int moy_sh_vsnprintf(char *buf, size_t cap, const char *fmt, va_list ap) {
    sink_t k = { buf, cap, 0 };
    for (const char *f = fmt; *f; f++) {
        if (*f != '%') {
            put(&k, *f);
            continue;
        }
        const char *spec = f++;
        int flags = 0;
        for (;; f++) {
            if (*f == '-') {
                flags |= F_MINUS;
            } else if (*f == '+') {
                flags |= F_PLUS;
            } else if (*f == ' ') {
                flags |= F_SPACE;
            } else if (*f == '#') {
                flags |= F_HASH;
            } else if (*f == '0') {
                flags |= F_ZERO;
            } else {
                break;
            }
        }
        int width = 0, prec = -1;
        if (*f == '*') {
            width = va_arg(ap, int);
            if (width < 0) {
                flags |= F_MINUS;
                width = -width;
            }
            f++;
        } else {
            while (*f >= '0' && *f <= '9') {
                width = width * 10 + (*f++ - '0');
            }
        }
        if (*f == '.') {
            f++;
            prec = 0;
            if (*f == '*') {
                prec = va_arg(ap, int);
                f++;
            } else {
                while (*f >= '0' && *f <= '9') {
                    prec = prec * 10 + (*f++ - '0');
                }
            }
        }
        // the length: hh h l ll j z t L, as the size of what va_arg reads
        int len = 0;                    // 0 int, 1 long, 2 long long, 3 intmax, 4 size, 5 ptrdiff
        if (*f == 'h') {
            f += f[1] == 'h' ? 2 : 1;
        } else if (*f == 'l') {
            len = f[1] == 'l' ? 2 : 1;
            f += len;
        } else if (*f == 'j') {
            len = 3;
            f++;
        } else if (*f == 'z') {
            len = 4;
            f++;
        } else if (*f == 't') {
            len = 5;
            f++;
        } else if (*f == 'L') {
            f++;
        }
        char conv = *f;
        if (conv == 'd' || conv == 'i') {
            intmax_t v = len == 0 ? va_arg(ap, int) : len == 1 ? va_arg(ap, long)
                       : len == 2 ? va_arg(ap, long long) : len == 3 ? va_arg(ap, intmax_t)
                       : len == 4 ? (intmax_t)va_arg(ap, size_t) : va_arg(ap, ptrdiff_t);
            uintmax_t m = v < 0 ? (uintmax_t)0 - (uintmax_t)v : (uintmax_t)v;
            integer(&k, m, v < 0, conv, flags, width, prec);
        } else if (conv == 'u' || conv == 'o' || conv == 'x' || conv == 'X') {
            uintmax_t v = len == 0 ? va_arg(ap, unsigned) : len == 1 ? va_arg(ap, unsigned long)
                        : len == 2 ? va_arg(ap, unsigned long long)
                        : len == 3 ? va_arg(ap, uintmax_t) : len == 4 ? va_arg(ap, size_t)
                        : (uintmax_t)va_arg(ap, ptrdiff_t);
            integer(&k, v, 0, conv, flags, width, prec);
        } else if (conv == 'p') {
            integer(&k, (uintptr_t)va_arg(ap, void *), 0, 'p', flags, width, prec);
        } else if (conv == 'c' || conv == 's' || conv == '%') {
            char c = conv == 'c' ? (char)va_arg(ap, int) : '%';
            const char *s = conv == 's' ? va_arg(ap, const char *) : &c;
            size_t n = conv == 's' ? (s != NULL ? strlen(s) : 0) : 1;
            if (conv == 's' && prec >= 0 && (size_t)prec < n) {
                n = (size_t)prec;
            }
            int fill = conv != '%' && width > (int)n ? width - (int)n : 0;
            if (!(flags & F_MINUS)) {
                pad(&k, ' ', fill);
            }
            for (size_t i = 0; i < n; i++) {
                put(&k, s[i]);
            }
            if (flags & F_MINUS) {
                pad(&k, ' ', fill);
            }
        } else if (conv && strchr("eEfFgGaA", conv) != NULL) {
            char one[48], out[512];
            size_t sl = (size_t)(f - spec + 1);
            if (sl >= sizeof one) {
                break;
            }
            memcpy(one, spec, sl);
            one[sl] = 0;
            double d = va_arg(ap, double);
            int w = snprintf(out, sizeof out, one, d);
            for (int i = 0; i < w && i < (int)sizeof out - 1; i++) {
                put(&k, out[i]);
            }
        } else {
            for (const char *p = spec; p <= f && *p; p++) {
                put(&k, *p);
            }
            if (!*f) {
                break;
            }
        }
    }
    if (cap > 0) {
        buf[k.n < cap ? k.n : cap - 1] = 0;
    }
    return (int)k.n;
}

int moy_sh_snprintf(char *buf, size_t cap, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = moy_sh_vsnprintf(buf, cap, fmt, ap);
    va_end(ap);
    return n;
}
