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

// A response header's name against `want` (lower case), without regard to
// ASCII case.
static int resp_name_is(const char *s, size_t n, const char *want) {
    size_t w = strlen(want);
    if (n != w) {
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (c >= 'A' && c <= 'Z') {
            c = (char)(c - 'A' + 'a');
        }
        if (c != want[i]) {
            return 0;
        }
    }
    return 1;
}

static int is_sp(char c) {
    return c == ' ' || c == '\t';
}

int moy_http_resp_parse(const char *buf, size_t n, moy_http_resp_t *r) {
    memset(r, 0, sizeof(*r));
    size_t end = 0;
    for (size_t i = 3; i < n; i++) {
        if (buf[i - 3] == '\r' && buf[i - 2] == '\n' && buf[i - 1] == '\r' && buf[i] == '\n') {
            end = i + 1u;
            break;
        }
    }
    if (end == 0) {
        return MOY_HTTP_PARTIAL;
    }
    r->head_end = end;
    const char *p = buf, *stop = buf + end - 2u;
    int first = 1;
    while (p < stop) {
        const char *eol = p;
        while (eol + 1 < stop + 2 && !(eol[0] == '\r' && eol[1] == '\n')) {
            eol++;
        }
        if (first) {
            first = 0;
            const char *sp = memchr(p, ' ', (size_t)(eol - p));
            if (sp != NULL) {
                int v = 0, digits = 0;
                for (const char *d = sp + 1; d < eol && *d >= '0' && *d <= '9' && digits < 4; d++) {
                    v = v * 10 + (*d - '0');
                    digits++;
                }
                r->status = digits == 3 ? v : 0;
            }
        } else {
            const char *colon = memchr(p, ':', (size_t)(eol - p));
            if (colon != NULL) {
                const char *v = colon + 1, *ve = eol;
                while (v < ve && is_sp(*v)) {
                    v++;
                }
                while (ve > v && is_sp(ve[-1])) {
                    ve--;
                }
                if (resp_name_is(p, (size_t)(colon - p), "content-length")) {
                    uint64_t x = 0;
                    const char *d = v;
                    for (; d < ve && *d >= '0' && *d <= '9'; d++) {
                        x = x * 10u + (uint64_t)(*d - '0');
                        if (x > 0xffffffffu) {
                            break;
                        }
                    }
                    r->clen = (d == ve && d > v && x <= 0xffffffffu) ? (uint32_t)x : 0;
                } else if (resp_name_is(p, (size_t)(colon - p), "location")) {
                    r->loc = v;
                    r->loc_n = (size_t)(ve - v);
                }
            }
        }
        p = eol + 2;
    }
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
        case 409: return "Conflict";
        case 429: return "Too Many Requests";
        case 500: return "Server Error";
        case 501: return "Not Implemented";
        case 503: return "Service Unavailable";
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
