// scanf's family (sdk/README.md): one scanner over a byte source, every
// conversion C99 names but the wide ones.
#include "lc.h"

#include <ctype.h>
#include <string.h>

static int sp(int c) {
    return c == ' ' || (c >= '\t' && c <= '\r');
}

enum { L_NONE, L_HH, L_H, L_L, L_LL, L_Z, L_BIG };

static void store_int(void *p, int len, unsigned long long v) {
    switch (len) {
    case L_HH:
        *(char *)p = (char)v;
        break;
    case L_H:
        *(short *)p = (short)v;
        break;
    case L_L:
        *(long *)p = (long)v;
        break;
    case L_LL:
        *(long long *)p = (long long)v;
        break;
    case L_Z:
        *(size_t *)p = (size_t)v;
        break;
    default:
        *(int *)p = (int)v;
    }
}

// The bytes of a number, up to `width`, into buf: whether any were taken.
static int take(lc_in_t *in, char *buf, int width, int conv, int *count) {
    int n = 0, c = in->get(in->arg);
    int cap = width < 63 ? width : 63;
    int fl = conv == 'f' || conv == 'e' || conv == 'g' || conv == 'a';
    int base = conv == 'o' ? 8 : conv == 'x' || conv == 'p' ? 16 : conv == 'i' ? 0 : 10;
    int dot = 0, exp = 0;
#define TAKE() (buf[n++] = (char)c, (*count)++, c = in->get(in->arg))
    if ((c == '-' || c == '+') && n < cap) {
        TAKE();
    }
    if (fl && n < cap && (c | 0x20) == 'i') {
        const char *w = "infinity";
        for (int k = 0; w[k] && n < cap && (c | 0x20) == w[k]; k++) {
            TAKE();
        }
    } else if (fl && n < cap && (c | 0x20) == 'n') {
        const char *w = "nan";
        for (int k = 0; w[k] && n < cap && (c | 0x20) == w[k]; k++) {
            TAKE();
        }
    } else {
        if ((base == 0 || base == 16) && c == '0' && n < cap) {
            TAKE();
            if ((c | 0x20) == 'x' && n < cap) {
                TAKE();
                base = 16;
            } else if (base == 0) {
                base = 8;
            }
        } else if (base == 0) {
            base = 10;
        }
        while (n < cap && c != -1) {
            int d = c >= '0' && c <= '9' ? c - '0' : (c | 0x20) >= 'a' && (c | 0x20) <= 'f'
                    ? (c | 0x20) - 'a' + 10 : 99;
            if (fl) {
                if (d < 10) {
                    TAKE();
                } else if (c == '.' && !dot && !exp) {
                    dot = 1;
                    TAKE();
                } else if ((c | 0x20) == 'e' && !exp) {
                    exp = 1;
                    TAKE();
                    if ((c == '-' || c == '+') && n < cap) {
                        TAKE();
                    }
                } else {
                    break;
                }
            } else if (d < base) {
                TAKE();
            } else {
                break;
            }
        }
    }
#undef TAKE
    if (c != -1) {
        in->unget(in->arg, c);
    }
    buf[n] = '\0';
    return n > 0 && !(n == 1 && (buf[0] == '-' || buf[0] == '+'));
}

LC_API int lc_scan(lc_in_t *in, const char *fmt, va_list ap) {
    int got = 0, count = 0, c;
    va_list a;
    va_copy(a, ap);
    while (*fmt) {
        if (sp((unsigned char)*fmt)) {
            while (sp((unsigned char)*fmt)) {
                fmt++;
            }
            while ((c = in->get(in->arg)) != -1 && sp(c)) {
                count++;
            }
            if (c != -1) {
                in->unget(in->arg, c);
            }
            continue;
        }
        if (*fmt != '%' || fmt[1] == '%') {
            int want = *fmt == '%' ? '%' : (unsigned char)*fmt;
            fmt += *fmt == '%' ? 2 : 1;
            if (want == '%') {
                while ((c = in->get(in->arg)) != -1 && sp(c)) {
                    count++;
                }
            } else {
                c = in->get(in->arg);
            }
            if (c != want) {
                if (c != -1) {
                    in->unget(in->arg, c);
                }
                goto out;
            }
            count++;
            continue;
        }
        fmt++;
        int skip = *fmt == '*';
        if (skip) {
            fmt++;
        }
        int width = 0;
        while (*fmt >= '0' && *fmt <= '9') {
            width = width * 10 + (*fmt++ - '0');
        }
        int len = L_NONE;
        if (*fmt == 'h') {
            len = fmt[1] == 'h' ? L_HH : L_H;
            fmt += len == L_HH ? 2 : 1;
        } else if (*fmt == 'l') {
            len = fmt[1] == 'l' ? L_LL : L_L;
            fmt += len == L_LL ? 2 : 1;
        } else if (*fmt == 'j' || *fmt == 'q') {
            len = L_LL;
            fmt++;
        } else if (*fmt == 'z' || *fmt == 't') {
            len = L_Z;
            fmt++;
        } else if (*fmt == 'L') {
            len = L_BIG;
            fmt++;
        }
        int conv = (unsigned char)*fmt++;
        if (conv == '\0') {
            break;
        }
        if (conv == 'n') {
            if (!skip) {
                store_int(va_arg(a, void *), len, (unsigned long long)count);
            }
            continue;
        }
        if (conv != 'c' && conv != '[') {
            while ((c = in->get(in->arg)) != -1 && sp(c)) {
                count++;
            }
            if (c == -1) {
                goto out;
            }
            in->unget(in->arg, c);
        }
        if (width == 0) {
            width = conv == 'c' ? 1 : 0x7fffffff;
        }
        switch (conv) {
        case 'c': {
            char *p = skip ? NULL : va_arg(a, char *);
            int k = 0;
            for (; k < width && (c = in->get(in->arg)) != -1; k++) {
                if (p != NULL) {
                    p[k] = (char)c;
                }
                count++;
            }
            if (k == 0) {
                goto out;
            }
            got += !skip;
            break;
        }
        case 's':
        case '[': {
            char set[256];
            if (conv == 's') {
                for (int i = 0; i < 256; i++) {
                    set[i] = !sp(i);
                }
            } else {
                int neg = *fmt == '^';
                if (neg) {
                    fmt++;
                }
                memset(set, 0, sizeof set);
                const char *f0 = fmt;
                while (*fmt && (*fmt != ']' || fmt == f0)) {
                    if (fmt[1] == '-' && fmt[2] && fmt[2] != ']') {
                        for (int i = (unsigned char)fmt[0]; i <= (unsigned char)fmt[2]; i++) {
                            set[i] = 1;
                        }
                        fmt += 3;
                    } else {
                        set[(unsigned char)*fmt++] = 1;
                    }
                }
                if (*fmt == ']') {
                    fmt++;
                }
                if (neg) {
                    for (int i = 0; i < 256; i++) {
                        set[i] = !set[i];
                    }
                }
            }
            char *p = skip ? NULL : va_arg(a, char *);
            int k = 0;
            while (k < width && (c = in->get(in->arg)) != -1) {
                if (!set[c & 0xff]) {
                    in->unget(in->arg, c);
                    break;
                }
                if (p != NULL) {
                    p[k] = (char)c;
                }
                k++;
                count++;
            }
            if (k == 0) {
                goto out;
            }
            if (p != NULL) {
                p[k] = '\0';
            }
            got += !skip;
            break;
        }
        case 'd':
        case 'i':
        case 'u':
        case 'o':
        case 'x':
        case 'X':
        case 'p': {
            char buf[64];
            if (!take(in, buf, width, conv == 'X' ? 'x' : conv, &count)) {
                goto out;
            }
            int neg, over;
            int base = conv == 'o' ? 8 : conv == 'x' || conv == 'X' || conv == 'p' ? 16
                     : conv == 'i' ? 0 : 10;
            unsigned long long v = lc_parse_u(buf, NULL, base, &neg, &over);
            if (neg) {
                v = 0ULL - v;
            }
            if (!skip) {
                if (conv == 'p') {
                    *va_arg(a, void **) = (void *)(uintptr_t)v;
                } else {
                    store_int(va_arg(a, void *), len, v);
                }
                got++;
            }
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
            char buf[64];
            if (!take(in, buf, width, 'f', &count)) {
                goto out;
            }
            double v = lc_strtod(buf, NULL);
            if (!skip) {
                if (len == L_L) {
                    *va_arg(a, double *) = v;
                } else if (len == L_BIG) {
                    *va_arg(a, long double *) = v;
                } else {
                    *va_arg(a, float *) = (float)v;
                }
                got++;
            }
            break;
        }
        default:
            goto out;
        }
    }
out:
    va_end(a);
    if (got == 0) {
        c = in->get(in->arg);
        if (c == -1) {
            return EOF;
        }
        in->unget(in->arg, c);
    }
    return got;
}

// -- the family -----------------------------------------------------------------------------

typedef struct {
    const char *p;
} str_in_t;

static int str_get(void *arg) {
    str_in_t *s = arg;
    return *s->p ? (unsigned char)*s->p++ : -1;
}

static void str_unget(void *arg, int c) {
    (void)c;
    ((str_in_t *)arg)->p--;
}

static int file_get(void *arg) {
    int c = lc_getc(arg);
    return c == EOF ? -1 : c;
}

static void file_unget(void *arg, int c) {
    ungetc(c, &((lc_file_t *)arg)->f);
}

LC_API int lc_vsscanf(const char *s, const char *fmt, va_list ap) {
    str_in_t st = { s };
    lc_in_t in = { str_get, str_unget, &st };
    return lc_scan(&in, fmt, ap);
}

LC_API int lc_sscanf(const char *s, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = lc_vsscanf(s, fmt, ap);
    va_end(ap);
    return r;
}

LC_API int lc_vfscanf(FILE *fp, const char *fmt, va_list ap) {
    lc_in_t in = { file_get, file_unget, LC_F(fp) };
    return lc_scan(&in, fmt, ap);
}

LC_API int lc_fscanf(FILE *fp, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = lc_vfscanf(fp, fmt, ap);
    va_end(ap);
    return r;
}

LC_API int lc_vscanf(const char *fmt, va_list ap) {
    return lc_vfscanf(stdin, fmt, ap);
}

LC_API int lc_scanf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int r = lc_vfscanf(stdin, fmt, ap);
    va_end(ap);
    return r;
}

#define SCANNER(name) \
    LC_EXPORT(name, lc_##name); \
    LC_EXPORT(__isoc99_##name, lc_##name); \
    LC_EXPORT(__isoc23_##name, lc_##name)
SCANNER(sscanf);
SCANNER(vsscanf);
SCANNER(fscanf);
SCANNER(vfscanf);
SCANNER(scanf);
SCANNER(vscanf);
