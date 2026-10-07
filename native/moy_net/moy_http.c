// The HTTP core's pure half: the request head parser and the response head
// (moy_net.h).

#include <stdio.h>
#include <string.h>

#include "moy_net.h"

static const char *find2(const char *p, const char *end, const char *pat,
                         size_t m) {
    for (; (size_t)(end - p) >= m; p++) {
        if (p[0] == pat[0] && memcmp(p, pat, m) == 0) {
            return p;
        }
    }
    return NULL;
}

static int is_ws(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v'
           || c == '\f';
}

static void trim(const char **a, const char **b) {
    while (*a < *b && is_ws(**a)) {
        (*a)++;
    }
    while (*b > *a && is_ws((*b)[-1])) {
        (*b)--;
    }
}

static int is_clen(const char *a, const char *b) {
    static const char want[] = "content-length";
    trim(&a, &b);
    if ((size_t)(b - a) != sizeof(want) - 1) {
        return 0;
    }
    for (size_t i = 0; i < sizeof(want) - 1; i++) {
        char c = a[i];
        if (c >= 'A' && c <= 'Z') {
            c = (char)(c - 'A' + 'a');
        }
        if (c != want[i]) {
            return 0;
        }
    }
    return 1;
}

static uint32_t clen_value(const char *a, const char *b) {
    trim(&a, &b);
    if (a < b && *a == '+') {
        a++;
    }
    if (a == b) {
        return 0;
    }
    uint64_t v = 0;
    for (; a < b; a++) {
        if (*a < '0' || *a > '9') {
            return 0;
        }
        v = v * 10u + (uint64_t)(*a - '0');
        if (v > MOY_HTTP_CLEN_MAX) {
            return 0;
        }
    }
    return (uint32_t)v;
}

int moy_http_parse(const char *buf, size_t n, moy_http_req_t *r) {
    memset(r, 0, sizeof(*r));
    const char *end = buf + n;
    const char *sep = find2(buf, end, "\r\n\r\n", 4);
    size_t nlen = 4;
    if (sep == NULL) {
        sep = find2(buf, end, "\n\n", 2);
        nlen = 2;
    }
    if (sep == NULL) {
        return MOY_HTTP_PARTIAL;
    }
    // The request line: up to the first '\n' (its "\r\n" counts as one break).
    const char *line_end = memchr(buf, '\n', (size_t)(sep - buf));
    if (line_end == NULL) {
        line_end = sep;
    }
    const char *le = line_end;
    if (le < sep && le > buf && le[-1] == '\r') {
        le--;
    }
    const char *sp = memchr(buf, ' ', (size_t)(le - buf));
    if (sp == NULL) {
        return MOY_HTTP_BAD;
    }
    const char *t = sp + 1;
    const char *t_end = memchr(t, ' ', (size_t)(le - t));
    if (t_end == NULL) {
        t_end = le;
    }
    uint32_t clen = 0;
    const char *p = line_end;
    while (p < sep) {
        p++;                                    // past the '\n'
        const char *e = memchr(p, '\n', (size_t)(sep - p));
        if (e == NULL) {
            e = sep;
        }
        const char *ve = e;
        if (ve < sep && ve > p && ve[-1] == '\r') {
            ve--;
        }
        const char *c = memchr(p, ':', (size_t)(ve - p));
        if (c != NULL && c > p && is_clen(p, c)) {
            clen = clen_value(c + 1, ve);
        }
        p = e;
    }
    r->method = buf;
    r->method_n = (size_t)(sp - buf);
    r->target = t;
    r->target_n = (size_t)(t_end - t);
    r->clen = clen;
    r->head_end = (size_t)(sep - buf) + nlen;
    return MOY_HTTP_OK;
}

int moy_http_query(const char *target, size_t n, const char *name,
                   size_t name_n, const char **val, size_t *val_n) {
    *val = target;
    *val_n = 0;
    const char *end = target + n;
    const char *q = target ? memchr(target, '?', n) : NULL;
    if (q == NULL) {
        return 0;
    }
    const char *p = q + 1;
    for (;;) {
        const char *amp = memchr(p, '&', (size_t)(end - p));
        const char *pe = amp ? amp : end;
        const char *eq = memchr(p, '=', (size_t)(pe - p));
        const char *ke = eq ? eq : pe;
        if ((size_t)(ke - p) == name_n && memcmp(p, name, name_n) == 0) {
            if (eq) {
                *val = eq + 1;
                *val_n = (size_t)(pe - eq - 1);
            } else {
                *val = pe;
            }
            return 1;
        }
        if (amp == NULL) {
            return 0;
        }
        p = amp + 1;
    }
}

const char *moy_http_reason(int status) {
    switch (status) {
        case 400: return "Bad Request";
        case 403: return "Forbidden";
        case 404: return "Not Found";
        case 405: return "Method Not Allowed";
        case 500: return "Server Error";
        case 501: return "Not Implemented";
        default: return "OK";
    }
}

size_t moy_http_head(char *out, size_t cap, int status, const char *ctype,
                     size_t body_n) {
    int k = snprintf(out, cap,
                     "HTTP/1.1 %d %s\r\n"
                     "Content-Type: %s\r\n"
                     "Content-Length: %lu\r\n"
                     "Cache-Control: no-store\r\n"
                     "Access-Control-Allow-Origin: *\r\n"
                     "Connection: close\r\n\r\n",
                     status, moy_http_reason(status), ctype,
                     (unsigned long)body_n);
    return k < 0 ? 0 : (size_t)k;
}

int moy_utf8_valid(const char *s, size_t n) {
    const unsigned char *p = (const unsigned char *)s;
    const unsigned char *end = p + n;
    while (p < end) {
        unsigned c = *p++;
        if (c < 0x80) {
            continue;
        }
        unsigned need, min;
        uint32_t cp;
        if (c >= 0xC2 && c <= 0xDF) {
            need = 1; min = 0x80; cp = c & 0x1F;
        } else if (c >= 0xE0 && c <= 0xEF) {
            need = 2; min = 0x800; cp = c & 0x0F;
        } else if (c >= 0xF0 && c <= 0xF4) {
            need = 3; min = 0x10000; cp = c & 0x07;
        } else {
            return 0;
        }
        if ((size_t)(end - p) < need) {
            return 0;
        }
        for (unsigned i = 0; i < need; i++) {
            if ((p[i] & 0xC0) != 0x80) {
                return 0;
            }
            cp = (cp << 6) | (p[i] & 0x3F);
        }
        p += need;
        if (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
            return 0;
        }
    }
    return 1;
}
