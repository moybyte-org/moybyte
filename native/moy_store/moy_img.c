// The moyimg-v1 decoder (moy_img.h has the contract).

#include <stdbool.h>
#include <string.h>

#include "moy_img.h"
#include "moy_json.h"

#if defined(__has_include)
#if __has_include("lib/uzlib/uzlib.h")
#include "lib/uzlib/uzlib.h"
#else
#include "uzlib.h"
#endif
#else
#include "lib/uzlib/uzlib.h"
#endif

// The inflater's state with the base64 reader its byte callback is.
typedef struct {
    uzlib_uncomp_t z;
    const char *p, *end;    // the base64 still to read
    uint32_t acc;           // bits read and not yet handed out
    int bits;
} img_work_t;

size_t moy_img_work_size(void) {
    return sizeof(img_work_t);
}

static int b64_value(char c) {
    if (c >= 'A' && c <= 'Z') {
        return c - 'A';
    }
    if (c >= 'a' && c <= 'z') {
        return c - 'a' + 26;
    }
    if (c >= '0' && c <= '9') {
        return c - '0' + 52;
    }
    if (c == '+') {
        return 62;
    }
    if (c == '/') {
        return 63;
    }
    return -1;
}

// The next character of the JSON string's DECODED text at *p, or -1 at its
// end: an escape decodes as json.loads decodes it; a character past ASCII,
// escaped or not, reads as 0x80.
static int next_char(const char **pp, const char *end) {
    const char *p = *pp;
    if (p >= end) {
        return -1;
    }
    int c = (unsigned char)*p++;
    if (c >= 0x80) {
        c = 0x80;
    } else if (c == '\\' && p < end) {
        int e = (unsigned char)*p++;
        switch (e) {
            case 'n': c = '\n'; break;
            case 'r': c = '\r'; break;
            case 't': c = '\t'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case 'u': {
                int v = 0;
                for (int i = 0; i < 4 && p < end; i++, p++) {
                    int d = *p;
                    v = v * 16 + (d <= '9' ? d - '0' : (d | 0x20) - 'a' + 10);
                }
                c = v < 0x80 ? v : 0x80;
                break;
            }
            default: c = e; break;      // \" \\ \/
        }
    }
    *pp = p;
    return c;
}

// binascii.a2b_base64's leniency, checked whole before anything decodes: a
// character outside the alphabet is skipped; the alphabet run ends at the
// first '='; a run of 4k+1 characters is refused, and one of 4k+2 or 4k+3 is
// refused unless the padding it needs follows.
static bool b64_ok(const char *p, const char *end) {
    size_t n = 0;
    int c;
    for (const char *q = p; (c = next_char(&q, end)) >= 0;) {
        if (c >= 0x80) {
            return false;           // a2b_base64 takes ASCII only
        }
    }
    const char *at = p;
    while ((c = next_char(&p, end)) >= 0 && c != '=') {
        if (b64_value((char)c) >= 0) {
            n++;
        }
        at = p;
    }
    p = at;
    int need = (int)(n % 4u);
    if (need == 1) {
        return false;
    }
    if (need == 0) {
        return true;
    }
    int pads = 0, want = 4 - need;
    while (pads < want && (c = next_char(&p, end)) >= 0) {
        if (c == '=') {
            pads++;
        } else if (b64_value((char)c) >= 0) {
            return false;
        }
    }
    return pads >= want;
}

// The next decoded byte for the inflater, or -1 at the end of the alphabet run.
static int b64_byte(void *data) {
    img_work_t *w = data;
    while (w->bits < 8) {
        int c = next_char(&w->p, w->end);
        if (c < 0 || c == '=') {
            return -1;
        }
        int v = b64_value((char)c);
        if (v >= 0) {
            w->acc = (w->acc << 6) | (uint32_t)v;
            w->bits += 6;
        }
    }
    w->bits -= 8;
    return (int)((w->acc >> w->bits) & 0xffu);
}

// The header: w, h and the base64 span of `data`.
static int head(const char *text, size_t n, uint32_t *w, uint32_t *h, const char **b,
                const char **be) {
    const char *end = text + n;
    const char *s = moy_json_ws(text, end);
    if (s >= end || *s != '{') {
        return MOY_IMG_NOT;
    }
    const char *e = moy_json_value(s, end, 0);
    if (e == NULL || moy_json_ws(e, end) != end) {
        return MOY_IMG_NOT;
    }
    const char *v, *ve;
    int64_t wi, hi;
    if (!moy_json_get(s, e, "w", &v, &ve) || moy_json_int(v, ve, &wi) != 1
        || !moy_json_get(s, e, "h", &v, &ve) || moy_json_int(v, ve, &hi) != 1
        || wi <= 0 || hi <= 0 || wi > 0xffff || hi > 0xffff) {
        return MOY_IMG_NOT;
    }
    if (!moy_json_get(s, e, "data", &v, &ve) || *v != '"') {
        return MOY_IMG_NOT;
    }
    *w = (uint32_t)wi;
    *h = (uint32_t)hi;
    *b = v + 1;             // the string's text, its escapes decoded as it is read
    *be = ve - 1;
    return MOY_IMG_OK;
}

int moy_img_head(const char *text, size_t n, uint32_t *w, uint32_t *h) {
    const char *b, *be;
    return head(text, n, w, h, &b, &be);
}

int moy_img_decode(const char *text, size_t n, uint8_t *pix, size_t cap,
                   uint32_t *w_out, uint32_t *h_out, void *work) {
    uint32_t w, h;
    const char *b, *be;
    if (head(text, n, &w, &h, &b, &be) != MOY_IMG_OK) {
        return MOY_IMG_NOT;
    }
    if (!b64_ok(b, be)) {
        return MOY_IMG_NOT;
    }
    size_t want = (size_t)w * h;
    if (want >= cap) {
        return MOY_IMG_ROOM;
    }
    img_work_t *iw = work;
    memset(iw, 0, sizeof(*iw));
    iw->p = b;
    iw->end = be;
    uzlib_uncompress_init(&iw->z, NULL, 0);
    iw->z.source = NULL;
    iw->z.source_limit = NULL;
    iw->z.source_read_data = iw;
    iw->z.source_read_cb = b64_byte;
    int wbits;
    if (uzlib_parse_zlib_gzip_header(&iw->z, &wbits) != UZLIB_HEADER_ZLIB) {
        return MOY_IMG_NOT;
    }
    // The buffer is the window too. Its byte past the picture is where a
    // stream longer than the picture shows itself.
    iw->z.dest_start = iw->z.dest = pix;
    iw->z.dest_limit = pix + want + 1;
    int st;
    do {
        st = uzlib_uncompress_chksum(&iw->z);
    } while (st == UZLIB_OK && iw->z.dest < iw->z.dest_limit);
    if (st != UZLIB_DONE || iw->z.dest != pix + want) {
        return MOY_IMG_NOT;
    }
    *w_out = w;
    *h_out = h;
    return MOY_IMG_OK;
}
