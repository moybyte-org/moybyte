// The kernel's JSON scanner (moy_json.h has the contract).

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_json.h"

// -- scanning ------------------------------------------------------------------

const char *moy_json_ws(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) {
        p++;
    }
    return p;
}

static int hex_val(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

// The code unit of the \uXXXX escape at `p` (which points at the 'u'), or -1.
static int32_t u_escape(const char *p, const char *end) {
    if (end - p < 5) {
        return -1;
    }
    int32_t v = 0;
    for (int i = 1; i <= 4; i++) {
        int d = hex_val(p[i]);
        if (d < 0) {
            return -1;
        }
        v = v * 16 + d;
    }
    return v;
}

static size_t utf8_put(uint32_t cp, char *b) {
    if (cp < 0x80u) {
        b[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800u) {
        b[0] = (char)(0xc0u | (cp >> 6));
        b[1] = (char)(0x80u | (cp & 0x3fu));
        return 2;
    }
    if (cp < 0x10000u) {
        b[0] = (char)(0xe0u | (cp >> 12));
        b[1] = (char)(0x80u | ((cp >> 6) & 0x3fu));
        b[2] = (char)(0x80u | (cp & 0x3fu));
        return 3;
    }
    b[0] = (char)(0xf0u | (cp >> 18));
    b[1] = (char)(0x80u | ((cp >> 12) & 0x3fu));
    b[2] = (char)(0x80u | ((cp >> 6) & 0x3fu));
    b[3] = (char)(0x80u | (cp & 0x3fu));
    return 4;
}

const char *moy_json_string(const char *p, const char *end, int decode,
                            char *out, size_t *n) {
    size_t w = 0;
    p++;
    for (;;) {
        if (p >= end) {
            return NULL;
        }
        unsigned char c = (unsigned char)*p;
        if (c == '"') {
            if (n != NULL) {
                *n = w;
            }
            return p + 1;
        }
        if (c < 0x20) {
            return NULL;
        }
        if (c != '\\') {
            if (out != NULL) {
                out[w] = (char)c;
            }
            w++;
            p++;
            continue;
        }
        if (++p >= end) {
            return NULL;
        }
        char e = *p;
        char lit = 0;
        switch (e) {
            case '"': lit = '"'; break;
            case '\\': lit = '\\'; break;
            case '/': lit = '/'; break;
            case 'b': lit = '\b'; break;
            case 'f': lit = '\f'; break;
            case 'n': lit = '\n'; break;
            case 'r': lit = '\r'; break;
            case 't': lit = '\t'; break;
            case 'u': break;
            default: return NULL;
        }
        if (e != 'u') {
            if (out != NULL) {
                out[w] = lit;
            }
            w++;
            p++;
            continue;
        }
        int32_t u = u_escape(p, end);
        if (u < 0) {
            return NULL;
        }
        p += 5;
        uint32_t cp = (uint32_t)u;
        if (decode) {
            if (cp >= 0xdc00u && cp <= 0xdfffu) {
                return NULL;
            }
            if (cp >= 0xd800u && cp <= 0xdbffu) {
                int32_t lo;
                if (end - p < 2 || p[0] != '\\' || p[1] != 'u'
                    || (lo = u_escape(p + 1, end)) < 0
                    || lo < 0xdc00 || lo > 0xdfff) {
                    return NULL;
                }
                p += 6;
                cp = 0x10000u + ((cp - 0xd800u) << 10) + ((uint32_t)lo - 0xdc00u);
            }
            char b[4];
            size_t k = utf8_put(cp, b);
            if (out != NULL) {
                memcpy(out + w, b, k);
            }
            w += k;
        }
    }
}

static int is_digit(char c) {
    return c >= '0' && c <= '9';
}

static const char *scan_number(const char *p, const char *end) {
    if (p < end && *p == '-') {
        p++;
    }
    if (p >= end || !is_digit(*p)) {
        return NULL;
    }
    if (*p == '0') {
        p++;
    } else {
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    if (p < end && *p == '.') {
        p++;
        if (p >= end || !is_digit(*p)) {
            return NULL;
        }
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    if (p < end && (*p == 'e' || *p == 'E')) {
        p++;
        if (p < end && (*p == '+' || *p == '-')) {
            p++;
        }
        if (p >= end || !is_digit(*p)) {
            return NULL;
        }
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    return p;
}

static const char *literal(const char *p, const char *end, const char *word) {
    size_t n = strlen(word);
    if ((size_t)(end - p) < n || memcmp(p, word, n) != 0) {
        return NULL;
    }
    return p + n;
}

const char *moy_json_value(const char *p, const char *end, uint32_t depth) {
    if (p >= end) {
        return NULL;
    }
    switch (*p) {
        case '"':
            return moy_json_string(p, end, 0, NULL, NULL);
        case '{':
        case '[': {
            if (depth >= MOY_JSON_DEPTH) {
                return NULL;
            }
            char close = *p == '{' ? '}' : ']';
            int obj = *p == '{';
            p = moy_json_ws(p + 1, end);
            if (p < end && *p == close) {
                return p + 1;
            }
            for (;;) {
                if (obj) {
                    if (p >= end || *p != '"') {
                        return NULL;
                    }
                    p = moy_json_string(p, end, 0, NULL, NULL);
                    if (p == NULL) {
                        return NULL;
                    }
                    p = moy_json_ws(p, end);
                    if (p >= end || *p != ':') {
                        return NULL;
                    }
                    p = moy_json_ws(p + 1, end);
                }
                p = moy_json_value(p, end, depth + 1u);
                if (p == NULL) {
                    return NULL;
                }
                p = moy_json_ws(p, end);
                if (p < end && *p == ',') {
                    p = moy_json_ws(p + 1, end);
                    continue;
                }
                if (p < end && *p == close) {
                    return p + 1;
                }
                return NULL;
            }
        }
        case 't':
            return literal(p, end, "true");
        case 'f':
            return literal(p, end, "false");
        case 'n':
            return literal(p, end, "null");
        case 'N':
            return literal(p, end, "NaN");
        case 'I':
            return literal(p, end, "Infinity");
        case '-':
            if (end - p >= 2 && p[1] == 'I') {
                return literal(p, end, "-Infinity");
            }
            return scan_number(p, end);
        default:
            return scan_number(p, end);
    }
}

int moy_json_valid(const char *text, size_t len) {
    const char *end = text + len;
    const char *p = moy_json_value(moy_json_ws(text, end), end, 1u);
    return p != NULL && moy_json_ws(p, end) == end ? MOY_JSON_OK : MOY_JSON_BAD;
}

int moy_json_object(const char *text, size_t len, moy_json_member_fn fn,
                    void *ctx) {
    const char *end = text + len;
    const char *p = moy_json_ws(text, end);
    if (p >= end || *p != '{') {
        return MOY_JSON_BAD;
    }
    p = moy_json_ws(p + 1, end);
    if (p < end && *p == '}') {
        return moy_json_ws(p + 1, end) == end ? MOY_JSON_OK : MOY_JSON_BAD;
    }
    for (;;) {
        if (p >= end || *p != '"') {
            return MOY_JSON_BAD;
        }
        const char *k = p;
        p = moy_json_string(p, end, 0, NULL, NULL);
        if (p == NULL) {
            return MOY_JSON_BAD;
        }
        const char *k_end = p;
        p = moy_json_ws(p, end);
        if (p >= end || *p != ':') {
            return MOY_JSON_BAD;
        }
        p = moy_json_ws(p + 1, end);
        const char *v = p;
        p = moy_json_value(p, end, 1u);
        if (p == NULL) {
            return MOY_JSON_BAD;
        }
        int rc = fn(ctx, k, k_end, v, p);
        if (rc != 0) {
            return rc;
        }
        p = moy_json_ws(p, end);
        if (p < end && *p == ',') {
            p = moy_json_ws(p + 1, end);
            continue;
        }
        if (p < end && *p == '}') {
            return moy_json_ws(p + 1, end) == end ? MOY_JSON_OK : MOY_JSON_BAD;
        }
        return MOY_JSON_BAD;
    }
}

// -- walking scanned values ------------------------------------------------------

void moy_json_iter(moy_json_iter_t *it, const char *v, const char *v_end) {
    it->obj = *v == '{';
    it->p = moy_json_ws(v + 1, v_end);
    it->end = v_end;
}

int moy_json_next(moy_json_iter_t *it, const char **key, const char **key_end,
                  const char **val, const char **val_end) {
    const char *p = it->p, *end = it->end;
    if (p >= end || *p == '}' || *p == ']') {
        return 0;
    }
    if (it->obj) {
        *key = p;
        p = moy_json_string(p, end, 0, NULL, NULL);
        *key_end = p;
        p = moy_json_ws(p, end);
        p = moy_json_ws(p + 1, end);    // the ':'
    } else if (key != NULL) {
        *key = *key_end = NULL;
    }
    *val = p;
    p = moy_json_value(p, end, 0u);
    *val_end = p;
    p = moy_json_ws(p, end);
    if (p < end && *p == ',') {
        p = moy_json_ws(p + 1, end);
    }
    it->p = p;
    return 1;
}

int moy_json_kind(const char *v, const char *v_end) {
    switch (*v) {
        case '"': return MOY_JSON_STR;
        case '{': return MOY_JSON_OBJ;
        case '[': return MOY_JSON_ARR;
        case 't': return MOY_JSON_TRUE;
        case 'f': return MOY_JSON_FALSE;
        case 'n': return MOY_JSON_NULL;
        case 'N': case 'I': return MOY_JSON_FLOAT;
        default:
            for (const char *p = v; p < v_end; p++) {
                if (*p == '.' || *p == 'e' || *p == 'E' || *p == 'I') {
                    return MOY_JSON_FLOAT;
                }
            }
            return MOY_JSON_INT;
    }
}

// The next code point of a string span's body (`*p` past the opening quote and
// short of the closing one): an escape, a surrogate pair, a UTF-8 sequence, or
// a byte that starts none (as itself, 0xDC80 + byte would be CPython's
// surrogateescape; the scanner's callers never hold one). A lone surrogate
// escape comes back as itself.
static uint32_t next_cp(const char **pp, const char *end) {
    const unsigned char *p = (const unsigned char *)*pp;
    uint32_t cp;
    if (*p == '\\') {
        char e = (char)p[1];
        switch (e) {
            case 'b': cp = '\b'; break;
            case 'f': cp = '\f'; break;
            case 'n': cp = '\n'; break;
            case 'r': cp = '\r'; break;
            case 't': cp = '\t'; break;
            case 'u': {
                cp = (uint32_t)u_escape((const char *)p + 1, end);
                p += 6;
                if (cp >= 0xd800u && cp <= 0xdbffu && (const char *)p + 6 <= end
                    && p[0] == '\\' && p[1] == 'u') {
                    int32_t lo = u_escape((const char *)p + 1, end);
                    if (lo >= 0xdc00 && lo <= 0xdfff) {
                        cp = 0x10000u + ((cp - 0xd800u) << 10) + ((uint32_t)lo - 0xdc00u);
                        p += 6;
                    }
                }
                *pp = (const char *)p;
                return cp;
            }
            default: cp = (uint32_t)(unsigned char)e; break;
        }
        *pp = (const char *)p + 2;
        return cp;
    }
    unsigned c = *p;
    size_t k = 0;
    if (c < 0x80u) {
        cp = c;
        k = 1;
    } else if ((c & 0xe0u) == 0xc0u) {
        cp = c & 0x1fu;
        k = 2;
    } else if ((c & 0xf0u) == 0xe0u) {
        cp = c & 0x0fu;
        k = 3;
    } else if ((c & 0xf8u) == 0xf0u) {
        cp = c & 0x07u;
        k = 4;
    } else {
        *pp = (const char *)p + 1;
        return c;
    }
    if ((const char *)p + k > end) {
        *pp = (const char *)p + 1;
        return c;
    }
    for (size_t i = 1; i < k; i++) {
        if ((p[i] & 0xc0u) != 0x80u) {
            *pp = (const char *)p + 1;
            return c;
        }
        cp = (cp << 6) | (p[i] & 0x3fu);
    }
    *pp = (const char *)p + k;
    return cp;
}

size_t moy_json_strlen(const char *s, const char *s_end) {
    const char *p = s + 1, *end = s_end - 1;
    size_t n = 0;
    char b[4];
    while (p < end) {
        if (*p != '\\') {             // bytes stand as they are
            p++;
            n++;
            continue;
        }
        uint32_t cp = next_cp(&p, end);
        if (cp >= 0xd800u && cp <= 0xdfffu) {
            cp = 0xfffdu;
        }
        n += utf8_put(cp, b);
    }
    return n;
}

size_t moy_json_str(const char *s, const char *s_end, char *out) {
    const char *p = s + 1, *end = s_end - 1;
    size_t n = 0;
    while (p < end) {
        if (*p != '\\') {
            out[n++] = *p++;
            continue;
        }
        uint32_t cp = next_cp(&p, end);
        if (cp >= 0xd800u && cp <= 0xdfffu) {
            cp = 0xfffdu;
        }
        n += utf8_put(cp, out + n);
    }
    return n;
}

int moy_json_str_is(const char *s, const char *s_end, const char *want,
                    size_t n) {
    const char *p = s + 1, *end = s_end - 1;
    const char *q = want, *qe = want + n;
    while (p < end && q < qe) {
        if (next_cp(&p, end) != next_cp(&q, qe)) {
            return 0;
        }
    }
    return p == end && q == qe;
}

int moy_json_str_eq(const char *a, const char *a_end, const char *b,
                    const char *b_end) {
    const char *p = a + 1, *pe = a_end - 1;
    const char *q = b + 1, *qe = b_end - 1;
    while (p < pe && q < qe) {
        if (next_cp(&p, pe) != next_cp(&q, qe)) {
            return 0;
        }
    }
    return p == pe && q == qe;
}

int moy_json_get(const char *obj, const char *obj_end, const char *key,
                 const char **v, const char **v_end) {
    if (*obj != '{') {
        return 0;
    }
    moy_json_iter_t it;
    const char *k, *ke, *val, *ve;
    size_t n = strlen(key);
    int found = 0;
    moy_json_iter(&it, obj, obj_end);
    while (moy_json_next(&it, &k, &ke, &val, &ve)) {
        if (moy_json_str_is(k, ke, key, n)) {
            *v = val;
            *v_end = ve;
            found = 1;
        }
    }
    return found;
}

int moy_json_truthy(const char *v, const char *v_end) {
    switch (moy_json_kind(v, v_end)) {
        case MOY_JSON_STR: return v_end - v > 2;
        case MOY_JSON_TRUE: return 1;
        case MOY_JSON_FALSE: case MOY_JSON_NULL: return 0;
        case MOY_JSON_OBJ: case MOY_JSON_ARR:
            return *moy_json_ws(v + 1, v_end) != (*v == '{' ? '}' : ']');
        case MOY_JSON_INT:
            for (const char *p = v; p < v_end; p++) {
                if (*p >= '1' && *p <= '9') {
                    return 1;
                }
            }
            return 0;
        default: {
            if (*v == 'N' || *v == 'I' || (*v == '-' && v[1] == 'I')) {
                return 1;
            }
            char buf[400];
            size_t n = (size_t)(v_end - v);
            if (n >= sizeof buf) {
                n = sizeof buf - 1u;
            }
            memcpy(buf, v, n);
            buf[n] = 0;
            return strtod(buf, NULL) != 0.0;
        }
    }
}

// Python's str.isspace() over ASCII.
static int py_space(unsigned char c) {
    return c == ' ' || (c >= 0x09 && c <= 0x0d) || (c >= 0x1c && c <= 0x1f);
}

// int() of a decimal literal: [+-]digits, single underscores between digits.
static int int_text(const char *p, const char *e, int64_t *out) {
    while (p < e && py_space((unsigned char)*p)) {
        p++;
    }
    while (e > p && py_space((unsigned char)e[-1])) {
        e--;
    }
    int neg = 0;
    if (p < e && (*p == '+' || *p == '-')) {
        neg = *p == '-';
        p++;
    }
    if (p >= e || !is_digit(*p)) {
        return 0;
    }
    uint64_t v = 0;
    int big = 0;
    for (const char *q = p; q < e; q++) {
        if (*q == '_') {
            if (q + 1 >= e || !is_digit(q[1]) || !is_digit(q[-1])) {
                return 0;
            }
            continue;
        }
        if (!is_digit(*q)) {
            return 0;
        }
        if (v > (UINT64_MAX - 9u) / 10u) {
            big = 1;
        }
        v = v * 10u + (uint64_t)(*q - '0');
    }
    if (big || v > (uint64_t)INT64_MAX) {
        return 2;
    }
    *out = neg ? -(int64_t)v : (int64_t)v;
    return 1;
}

int moy_json_int(const char *v, const char *v_end, int64_t *out) {
    switch (moy_json_kind(v, v_end)) {
        case MOY_JSON_TRUE: *out = 1; return 1;
        case MOY_JSON_FALSE: *out = 0; return 1;
        case MOY_JSON_INT: return int_text(v, v_end, out);
        case MOY_JSON_FLOAT: {
            if (*v == 'N' || *v == 'I' || (*v == '-' && v[1] == 'I')) {
                return 0;
            }
            char buf[64];
            size_t n = (size_t)(v_end - v);
            double d;
            if (n < sizeof buf) {
                memcpy(buf, v, n);
                buf[n] = 0;
                d = strtod(buf, NULL);
            } else {
                return 2;
            }
            if (!isfinite(d)) {
                return 0;
            }
            d = trunc(d);
            if (d >= 9.2e18 || d <= -9.2e18) {
                return 2;
            }
            *out = (int64_t)d;
            return 1;
        }
        case MOY_JSON_STR: {
            size_t n = moy_json_strlen(v, v_end);
            char buf[128];
            if (n >= sizeof buf) {
                // a literal this long is an int only if it is all digits
                return 0;
            }
            n = moy_json_str(v, v_end, buf);
            for (size_t i = 0; i < n; i++) {
                if ((unsigned char)buf[i] >= 0x80u) {
                    return 0;
                }
            }
            return int_text(buf, buf + n, out);
        }
        default:
            return 0;
    }
}

// -- writing as CPython's json.dumps does ------------------------------------------------

typedef struct {
    char *out;
    size_t cap, n;
} sink_t;

static void put(sink_t *k, const char *p, size_t n) {
    if (k->n < k->cap) {
        size_t room = k->cap - k->n;
        memcpy(k->out + k->n, p, n < room ? n : room);
    }
    k->n += n;
}

static void put_u(sink_t *k, uint32_t u) {
    char b[12];
    snprintf(b, sizeof b, "\\u%04x", (unsigned)u);
    put(k, b, 6);
}

static void put_cp(sink_t *k, uint32_t cp) {
    switch (cp) {
        case '"': put(k, "\\\"", 2); return;
        case '\\': put(k, "\\\\", 2); return;
        case '\n': put(k, "\\n", 2); return;
        case '\r': put(k, "\\r", 2); return;
        case '\t': put(k, "\\t", 2); return;
        case '\b': put(k, "\\b", 2); return;
        case '\f': put(k, "\\f", 2); return;
        default: break;
    }
    if (cp >= 0x20u && cp <= 0x7eu) {
        char c = (char)cp;
        put(k, &c, 1);
    } else if (cp < 0x10000u) {
        put_u(k, cp);
    } else {
        cp -= 0x10000u;
        put_u(k, 0xd800u | (cp >> 10));
        put_u(k, 0xdc00u | (cp & 0x3ffu));
    }
}

static void put_string(sink_t *k, const char *s, const char *s_end) {
    const char *p = s + 1, *end = s_end - 1;
    put(k, "\"", 1);
    while (p < end) {
        put_cp(k, next_cp(&p, end));
    }
    put(k, "\"", 1);
}

size_t moy_json_quote(const char *s, size_t n, char *out, size_t cap) {
    sink_t k = { out, cap, 0 };
    const char *p = s, *end = s + n;
    put(&k, "\"", 1);
    while (p < end) {
        put_cp(&k, next_cp(&p, end));
    }
    put(&k, "\"", 1);
    return k.n;
}

// repr() of a double, as CPython writes it: the shortest digits that read back
// the same, positional for exponents -4 to 15 (with ".0" when whole), else
// d[.ddd]e[+-]XX.
static void put_float(sink_t *k, double d) {
    if (isnan(d)) {
        put(k, "NaN", 3);
        return;
    }
    if (isinf(d)) {
        if (d < 0) {
            put(k, "-Infinity", 9);
        } else {
            put(k, "Infinity", 8);
        }
        return;
    }
    char buf[40];
    int prec;
    for (prec = 1; prec < 17; prec++) {
        snprintf(buf, sizeof buf, "%.*e", prec - 1, d);
        if (strtod(buf, NULL) == d) {
            break;
        }
    }
    snprintf(buf, sizeof buf, "%.*e", prec - 1, d);
    // buf: [-]D[.DDD]e[+-]XX
    char digits[24];
    int nd = 0, neg = 0;
    const char *p = buf;
    if (*p == '-') {
        neg = 1;
        p++;
    }
    while (*p && *p != 'e') {
        if (*p != '.') {
            digits[nd++] = *p;
        }
        p++;
    }
    int exp = atoi(p + 1);
    while (nd > 1 && digits[nd - 1] == '0') {
        nd--;
    }
    char o[64];
    int n = 0;
    if (neg) {
        o[n++] = '-';
    }
    if (exp >= -4 && exp < 16) {
        if (exp < 0) {
            o[n++] = '0';
            o[n++] = '.';
            for (int i = 0; i < -exp - 1; i++) {
                o[n++] = '0';
            }
            for (int i = 0; i < nd; i++) {
                o[n++] = digits[i];
            }
        } else {
            for (int i = 0; i <= exp; i++) {
                o[n++] = i < nd ? digits[i] : '0';
            }
            o[n++] = '.';
            if (nd > exp + 1) {
                for (int i = exp + 1; i < nd; i++) {
                    o[n++] = digits[i];
                }
            } else {
                o[n++] = '0';
            }
        }
    } else {
        o[n++] = digits[0];
        if (nd > 1) {
            o[n++] = '.';
            for (int i = 1; i < nd; i++) {
                o[n++] = digits[i];
            }
        }
        n += snprintf(o + n, sizeof o - (size_t)n, "e%c%02d", exp < 0 ? '-' : '+',
                      exp < 0 ? -exp : exp);
    }
    put(k, o, (size_t)n);
}

static void canon(sink_t *k, const char *v, const char *v_end) {
    switch (moy_json_kind(v, v_end)) {
        case MOY_JSON_STR:
            put_string(k, v, v_end);
            return;
        case MOY_JSON_INT:
            if (v_end - v == 2 && v[0] == '-' && v[1] == '0') {
                put(k, "0", 1);
            } else {
                put(k, v, (size_t)(v_end - v));
            }
            return;
        case MOY_JSON_FLOAT: {
            double d;
            if (*v == 'N') {
                d = (double)NAN;
            } else if (*v == 'I') {
                d = (double)INFINITY;
            } else if (*v == '-' && v[1] == 'I') {
                d = -(double)INFINITY;
            } else {
                char buf[400];
                size_t n = (size_t)(v_end - v);
                if (n >= sizeof buf) {
                    n = sizeof buf - 1u;
                }
                memcpy(buf, v, n);
                buf[n] = 0;
                d = strtod(buf, NULL);
            }
            put_float(k, d);
            return;
        }
        case MOY_JSON_ARR: {
            moy_json_iter_t it;
            const char *e, *ee;
            int first = 1;
            put(k, "[", 1);
            moy_json_iter(&it, v, v_end);
            while (moy_json_next(&it, NULL, NULL, &e, &ee)) {
                if (!first) {
                    put(k, ", ", 2);
                }
                first = 0;
                canon(k, e, ee);
            }
            put(k, "]", 1);
            return;
        }
        case MOY_JSON_OBJ: {
            moy_json_iter_t it, back, fwd;
            const char *key, *ke, *e, *ee;
            int first = 1;
            put(k, "{", 1);
            moy_json_iter(&it, v, v_end);
            for (;;) {
                back = it;
                if (!moy_json_next(&it, &key, &ke, &e, &ee)) {
                    break;
                }
                // a key seen before was written at its first place
                moy_json_iter_t s;
                const char *k2, *k2e, *e2, *e2e;
                int seen = 0;
                moy_json_iter(&s, v, v_end);
                while (s.p < back.p && moy_json_next(&s, &k2, &k2e, &e2, &e2e)) {
                    if (moy_json_str_eq(k2, k2e, key, ke)) {
                        seen = 1;
                        break;
                    }
                }
                if (seen) {
                    continue;
                }
                fwd = it;               // ...with its last value
                while (moy_json_next(&fwd, &k2, &k2e, &e2, &e2e)) {
                    if (moy_json_str_eq(k2, k2e, key, ke)) {
                        e = e2;
                        ee = e2e;
                    }
                }
                if (!first) {
                    put(k, ", ", 2);
                }
                first = 0;
                put_string(k, key, ke);
                put(k, ": ", 2);
                canon(k, e, ee);
            }
            put(k, "}", 1);
            return;
        }
        case MOY_JSON_TRUE:
            put(k, "true", 4);
            return;
        case MOY_JSON_FALSE:
            put(k, "false", 5);
            return;
        default:
            put(k, "null", 4);
            return;
    }
}

size_t moy_json_canon(const char *v, const char *v_end, char *out, size_t cap) {
    sink_t k = { out, cap, 0 };
    canon(&k, v, v_end);
    return k.n;
}

size_t moy_json_canon_set(const char *obj, const char *obj_end, const char *key,
                          const char *val, char *out, size_t cap) {
    sink_t k = { out, cap, 0 };
    moy_json_iter_t it, s, fwd;
    const char *mk, *mke, *e, *ee, *k2, *k2e, *e2, *e2e;
    size_t kn = strlen(key);
    int first = 1, done = 0;
    put(&k, "{", 1);
    moy_json_iter(&it, obj, obj_end);
    for (;;) {
        moy_json_iter_t back = it;
        if (!moy_json_next(&it, &mk, &mke, &e, &ee)) {
            break;
        }
        int seen = 0;
        moy_json_iter(&s, obj, obj_end);
        while (s.p < back.p && moy_json_next(&s, &k2, &k2e, &e2, &e2e)) {
            if (moy_json_str_eq(k2, k2e, mk, mke)) {
                seen = 1;
                break;
            }
        }
        if (seen) {
            continue;
        }
        int ours = moy_json_str_is(mk, mke, key, kn);
        if (ours && val == NULL) {
            continue;                   // removed
        }
        fwd = it;
        while (moy_json_next(&fwd, &k2, &k2e, &e2, &e2e)) {
            if (moy_json_str_eq(k2, k2e, mk, mke)) {
                e = e2;
                ee = e2e;
            }
        }
        if (!first) {
            put(&k, ", ", 2);
        }
        first = 0;
        put_string(&k, mk, mke);
        put(&k, ": ", 2);
        if (ours) {
            put(&k, val, strlen(val));
            done = 1;
        } else {
            canon(&k, e, ee);
        }
    }
    if (!done && val != NULL) {
        if (!first) {
            put(&k, ", ", 2);
        }
        k.n += moy_json_quote(key, kn, k.n < cap ? out + k.n : NULL,
                              k.n < cap ? cap - k.n : 0);
        put(&k, ": ", 2);
        put(&k, val, strlen(val));
    }
    put(&k, "}", 1);
    return k.n;
}
