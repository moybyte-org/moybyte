// moy_png: the cover reader. moy_png.h says what it accepts and why it is
// shaped this way; this file is the inflater, the chunk walk and the rows.

#include <string.h>

#include "moy_png.h"

#define WSIZE   32768u              // the deflate window: zlib's own, always
#define WMASK   (WSIZE - 1u)
#define MAXBITS 15
#define ROWMAX  (1 + MOY_PNG_SIDE * 3)
#define MAGIC   0x6D6F7950u         // "moyP": work that moy_png_begin set up
#define NCACHE  64                  // nearest-colour memo, direct-mapped

// Inflate modes.
enum { M_HEAD, M_BLOCK, M_STORED, M_CODES, M_END, M_CHECKED, M_ERROR };

typedef struct {
    uint32_t magic;
    uint32_t len;                   // the file length moy_png_begin read
    // The IDAT walk: the payload being read is [pos, end).
    uint32_t pos;
    uint32_t end;
    // Bits not yet consumed, least significant first.
    uint32_t bitbuf;
    int32_t bitcnt;
    // Inflate.
    uint8_t mode;
    uint8_t last;                   // the block being read is the final one
    uint8_t pad0[2];
    uint32_t left;                  // stored bytes, or match bytes, still owed
    uint32_t dist;                  // the match's distance
    uint32_t produced;              // bytes inflated so far
    uint32_t adler_a;
    uint32_t adler_b;
    uint32_t wpos;
    int16_t lcount[MAXBITS + 1];
    int16_t lsym[288];
    int16_t dcount[MAXBITS + 1];
    int16_t dsym[32];
    // The picture.
    uint8_t ctype;
    uint8_t bpp;                    // bytes per pixel in the raw rows: 3 or 1
    uint8_t div;
    uint8_t fmt;
    uint16_t npal;                  // PLTE entries
    uint16_t nmap;                  // entries of the palette INDEX maps to
    uint16_t row;                   // raw rows finished
    uint16_t col;                   // bytes of the current raw row so far
    uint16_t orow;                  // output rows finished
    uint8_t cur;                    // which of rows[] is being filled
    uint8_t pad1;
    uint8_t plte[768];
    uint8_t map[768];
    uint8_t lut[256];               // PLTE entry -> map index (indexed, div 1)
    uint32_t ckey[NCACHE];
    uint8_t cval[NCACHE];
    uint32_t sums[(MOY_PNG_SIDE / 2) * 3];
    uint8_t rows[2][ROWMAX];
    uint8_t window[WSIZE];
} png_t;

_Static_assert(sizeof(png_t) <= MOY_PNG_WORK, "MOY_PNG_WORK is too small");

static const uint8_t SIG[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};

size_t moy_png_work_size(void) {
    return MOY_PNG_WORK;
}

static int fmt_bytes(int fmt) {
    switch (fmt) {
        case MOY_PNG_RGB888: return 3;
        case MOY_PNG_RGB565:
        case MOY_PNG_RGB565_SW: return 2;
        case MOY_PNG_INDEX: return 1;
    }
    return 0;
}

static int div_ok(int div) {
    return div >= 1 && div <= MOY_PNG_SIDE && (div & (div - 1)) == 0;
}

size_t moy_png_out_size(int div, int fmt) {
    if (!div_ok(div) || !fmt_bytes(fmt)) {
        return 0;
    }
    size_t side = (size_t)(MOY_PNG_SIDE / div);
    return side * side * (size_t)fmt_bytes(fmt);
}

static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
           | ((uint32_t)p[2] << 8) | p[3];
}

// --- the IDAT walk -----------------------------------------------------------
//
// Every IDAT's payload, in file order, wherever it sits before IEND -- which is
// what the reference reader concatenates. moy_png_begin proved the chunk list
// sound, but the walk still bounds every step against `len`: rows() is handed
// the bytes again on every call.

static void seek_idat(png_t *s, const uint8_t *d, uint32_t p) {
    uint32_t len = s->len;
    while (p + 8 <= len) {
        uint32_t n = be32(d + p);
        const uint8_t *t = d + p + 4;
        if (n > len - p - 8 || len - p - 8 - n < 4) {
            break;
        }
        if (t[0] == 'I' && t[1] == 'E' && t[2] == 'N' && t[3] == 'D') {
            break;
        }
        if (t[0] == 'I' && t[1] == 'D' && t[2] == 'A' && t[3] == 'T' && n) {
            s->pos = p + 8;
            s->end = p + 8 + n;
            return;
        }
        p += 12 + n;
    }
    s->pos = s->end = 0;            // no more image data
}

static int next_byte(png_t *s, const uint8_t *d) {
    if (s->pos >= s->end) {
        if (s->end == 0) {
            return -1;
        }
        seek_idat(s, d, s->end + 4);
        if (s->pos >= s->end) {
            return -1;
        }
    }
    return d[s->pos++];
}

// --- bits ----------------------------------------------------------------------

// At least `n` (<= 16) bits in the buffer, or 0 at the end of the data. Bytes are
// loaded one at a time, so after any take fewer than 8 bits remain -- all of
// them from the last byte loaded, which is what makes byte alignment a drop.
static int need(png_t *s, const uint8_t *d, int n) {
    while (s->bitcnt < n) {
        int b = next_byte(s, d);
        if (b < 0) {
            return 0;
        }
        s->bitbuf |= (uint32_t)b << s->bitcnt;
        s->bitcnt += 8;
    }
    return 1;
}

static uint32_t take(png_t *s, int n) {
    uint32_t v = s->bitbuf & ((1u << n) - 1u);
    s->bitbuf >>= n;
    s->bitcnt -= n;
    return v;
}

// --- Huffman codes (canonical, decoded a bit at a time) ------------------------

// Build a decoding table from code lengths. 0: complete (or empty). >0:
// incomplete. <0: over-subscribed.
static int construct(int16_t *count, int16_t *symbol, const uint8_t *length,
                     int n) {
    int16_t offs[MAXBITS + 1];
    int len;
    int left;
    for (len = 0; len <= MAXBITS; len++) {
        count[len] = 0;
    }
    for (int i = 0; i < n; i++) {
        count[length[i]]++;
    }
    if (count[0] == n) {
        return 0;
    }
    left = 1;
    for (len = 1; len <= MAXBITS; len++) {
        left <<= 1;
        left -= count[len];
        if (left < 0) {
            return left;
        }
    }
    offs[1] = 0;
    for (len = 1; len < MAXBITS; len++) {
        offs[len + 1] = (int16_t)(offs[len] + count[len]);
    }
    for (int i = 0; i < n; i++) {
        if (length[i] != 0) {
            symbol[offs[length[i]]++] = (int16_t)i;
        }
    }
    return left;
}

// The next symbol, or -1 (out of data, or a bit pattern no code has).
static int decode(png_t *s, const uint8_t *d, const int16_t *count,
                  const int16_t *symbol) {
    int code = 0;
    int first = 0;
    int index = 0;
    for (int len = 1; len <= MAXBITS; len++) {
        if (s->bitcnt < 1 && !need(s, d, 1)) {
            return -1;
        }
        code |= (int)(s->bitbuf & 1u);
        s->bitbuf >>= 1;
        s->bitcnt--;
        int c = count[len];
        if (code - c < first) {
            return symbol[index + (code - first)];
        }
        index += c;
        first += c;
        first <<= 1;
        code <<= 1;
    }
    return -1;
}

static void fixed(png_t *s) {
    uint8_t lengths[288];
    int i;
    for (i = 0; i < 144; i++) {
        lengths[i] = 8;
    }
    for (; i < 256; i++) {
        lengths[i] = 9;
    }
    for (; i < 280; i++) {
        lengths[i] = 7;
    }
    for (; i < 288; i++) {
        lengths[i] = 8;
    }
    construct(s->lcount, s->lsym, lengths, 288);
    for (i = 0; i < 30; i++) {
        lengths[i] = 5;
    }
    construct(s->dcount, s->dsym, lengths, 30);
}

// A dynamic block's code tables. 0, or -1 for anything zlib refuses.
static int dynamic(png_t *s, const uint8_t *d) {
    static const uint8_t order[19] = {
        16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15
    };
    uint8_t lengths[286 + 30];
    int nlen;
    int ndist;
    int ncode;
    int index;
    int err;
    if (!need(s, d, 14)) {
        return -1;
    }
    nlen = (int)take(s, 5) + 257;
    ndist = (int)take(s, 5) + 1;
    ncode = (int)take(s, 4) + 4;
    if (nlen > 286 || ndist > 30) {
        return -1;
    }
    for (index = 0; index < ncode; index++) {
        if (!need(s, d, 3)) {
            return -1;
        }
        lengths[order[index]] = (uint8_t)take(s, 3);
    }
    for (; index < 19; index++) {
        lengths[order[index]] = 0;
    }
    if (construct(s->lcount, s->lsym, lengths, 19) != 0) {
        return -1;                  // the code-length code must be complete
    }
    index = 0;
    while (index < nlen + ndist) {
        int sym = decode(s, d, s->lcount, s->lsym);
        if (sym < 0) {
            return -1;
        }
        if (sym < 16) {
            lengths[index++] = (uint8_t)sym;
            continue;
        }
        uint8_t len = 0;
        int rep;
        if (sym == 16) {
            if (index == 0) {
                return -1;
            }
            len = lengths[index - 1];
            if (!need(s, d, 2)) {
                return -1;
            }
            rep = 3 + (int)take(s, 2);
        } else if (sym == 17) {
            if (!need(s, d, 3)) {
                return -1;
            }
            rep = 3 + (int)take(s, 3);
        } else {
            if (!need(s, d, 7)) {
                return -1;
            }
            rep = 11 + (int)take(s, 7);
        }
        if (index + rep > nlen + ndist) {
            return -1;
        }
        while (rep--) {
            lengths[index++] = len;
        }
    }
    if (lengths[256] == 0) {
        return -1;                  // no end-of-block code
    }
    // An incomplete literal/length or distance code is allowed only when it is
    // a single code of one bit -- zlib's rule, word for word.
    err = construct(s->lcount, s->lsym, lengths, nlen);
    if (err && (err < 0 || nlen != s->lcount[0] + s->lcount[1])) {
        return -1;
    }
    err = construct(s->dcount, s->dsym, lengths + nlen, ndist);
    if (err && (err < 0 || ndist != s->dcount[0] + s->dcount[1])) {
        return -1;
    }
    return 0;
}

// --- inflate ---------------------------------------------------------------------

static const uint16_t LBASE[29] = {
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
    35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258
};
static const uint8_t LEXT[29] = {
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
    3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0
};
static const uint16_t DBASE[30] = {
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
    257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
    8193, 12289, 16385, 24577
};
static const uint8_t DEXT[30] = {
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
    7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13
};

static int fail(png_t *s) {
    s->mode = M_ERROR;
    return -1;
}

// Inflate up to `want` bytes into `dst` (and the window). Returns how many --
// fewer only when the final block has ended (mode M_END) -- or -1.
static int inflate(png_t *s, const uint8_t *d, uint8_t *dst, int want) {
    int n = 0;
    uint8_t *win = s->window;
    while (n < want) {
        switch (s->mode) {
            case M_HEAD: {
                int cmf = next_byte(s, d);
                int flg = next_byte(s, d);
                if (cmf < 0 || flg < 0 || (cmf & 15) != 8 || (cmf >> 4) > 7
                    || ((cmf << 8) | flg) % 31 != 0 || (flg & 0x20)) {
                    return fail(s);
                }
                s->mode = M_BLOCK;
                break;
            }
            case M_BLOCK: {
                if (s->last) {
                    s->mode = M_END;
                    return n;
                }
                if (!need(s, d, 3)) {
                    return fail(s);
                }
                s->last = (uint8_t)take(s, 1);
                uint32_t type = take(s, 2);
                if (type == 0) {
                    s->bitbuf = 0;          // to the byte boundary
                    s->bitcnt = 0;
                    int b0 = next_byte(s, d);
                    int b1 = next_byte(s, d);
                    int b2 = next_byte(s, d);
                    int b3 = next_byte(s, d);
                    if (b3 < 0 || b0 < 0 || b1 < 0 || b2 < 0
                        || (b0 | (b1 << 8)) != ((~(b2 | (b3 << 8))) & 0xFFFF)) {
                        return fail(s);
                    }
                    s->left = (uint32_t)(b0 | (b1 << 8));
                    s->mode = M_STORED;
                } else if (type == 1) {
                    fixed(s);
                    s->left = 0;
                    s->mode = M_CODES;
                } else if (type == 2) {
                    if (dynamic(s, d) < 0) {
                        return fail(s);
                    }
                    s->left = 0;
                    s->mode = M_CODES;
                } else {
                    return fail(s);
                }
                break;
            }
            case M_STORED: {
                if (s->left == 0) {
                    s->mode = M_BLOCK;
                    break;
                }
                while (s->left && n < want) {
                    int b = next_byte(s, d);
                    if (b < 0) {
                        return fail(s);
                    }
                    dst[n++] = (uint8_t)b;
                    win[s->wpos++ & WMASK] = (uint8_t)b;
                    s->produced++;
                    s->left--;
                }
                break;
            }
            case M_CODES: {
                if (s->left) {
                    uint32_t from = s->wpos - s->dist;
                    while (s->left && n < want) {
                        uint8_t b = win[from++ & WMASK];
                        dst[n++] = b;
                        win[s->wpos++ & WMASK] = b;
                        s->produced++;
                        s->left--;
                    }
                    break;
                }
                int sym = decode(s, d, s->lcount, s->lsym);
                if (sym < 0) {
                    return fail(s);
                }
                if (sym < 256) {
                    dst[n++] = (uint8_t)sym;
                    win[s->wpos++ & WMASK] = (uint8_t)sym;
                    s->produced++;
                    break;
                }
                if (sym == 256) {
                    s->mode = M_BLOCK;
                    break;
                }
                sym -= 257;
                if (sym >= 29) {
                    return fail(s);
                }
                if (LEXT[sym] && !need(s, d, LEXT[sym])) {
                    return fail(s);
                }
                uint32_t len = LBASE[sym] + take(s, LEXT[sym]);
                int ds = decode(s, d, s->dcount, s->dsym);
                if (ds < 0 || ds >= 30) {
                    return fail(s);
                }
                if (DEXT[ds] && !need(s, d, DEXT[ds])) {
                    return fail(s);
                }
                uint32_t dist = DBASE[ds] + take(s, DEXT[ds]);
                if (dist > s->produced) {
                    return fail(s);         // before the start of the stream
                }
                s->left = len;
                s->dist = dist;
                break;
            }
            case M_END:
                return n;
            default:
                return fail(s);
        }
    }
    return n;
}

static void adler(png_t *s, const uint8_t *p, int n) {
    uint32_t a = s->adler_a;
    uint32_t b = s->adler_b;
    while (n > 0) {
        int k = n < 5552 ? n : 5552;
        n -= k;
        while (k--) {
            a += *p++;
            b += a;
        }
        a %= 65521u;
        b %= 65521u;
    }
    s->adler_a = a;
    s->adler_b = b;
}

// The picture is all in: the stream must now END -- any further byte is data
// past the picture -- and carry the Adler-32 of what it produced.
static int finish(png_t *s, const uint8_t *d) {
    uint8_t extra;
    while (s->mode != M_END) {
        int got = inflate(s, d, &extra, 1);
        if (got != 0) {
            return fail(s);
        }
        if (s->mode == M_ERROR) {
            return -1;
        }
    }
    s->bitbuf = 0;                  // the check is byte-aligned
    s->bitcnt = 0;
    uint32_t want = (s->adler_b << 16) | s->adler_a;
    uint32_t got = 0;
    for (int i = 0; i < 4; i++) {
        int b = next_byte(s, d);
        if (b < 0) {
            return fail(s);
        }
        got = (got << 8) | (uint32_t)b;
    }
    if (got != want) {
        return fail(s);
    }
    s->mode = M_CHECKED;
    return 1;
}

// --- the picture ---------------------------------------------------------------------

static void unfilter(uint8_t *line, const uint8_t *prev, int stride, int bpp,
                     int ft) {
    int i;
    switch (ft) {
        case 1:
            for (i = bpp; i < stride; i++) {
                line[i] = (uint8_t)(line[i] + line[i - bpp]);
            }
            break;
        case 2:
            for (i = 0; i < stride; i++) {
                line[i] = (uint8_t)(line[i] + prev[i]);
            }
            break;
        case 3:
            for (i = 0; i < bpp; i++) {
                line[i] = (uint8_t)(line[i] + (prev[i] >> 1));
            }
            for (; i < stride; i++) {
                line[i] = (uint8_t)(line[i] + ((line[i - bpp] + prev[i]) >> 1));
            }
            break;
        case 4:
            for (i = 0; i < bpp; i++) {
                line[i] = (uint8_t)(line[i] + prev[i]);    // a = c = 0: Paeth is b
            }
            for (; i < stride; i++) {
                int a = line[i - bpp];
                int b = prev[i];
                int c = prev[i - bpp];
                int p = a + b - c;
                int pa = p > a ? p - a : a - p;
                int pb = p > b ? p - b : b - p;
                int pc = p > c ? p - c : c - p;
                int pr = (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
                line[i] = (uint8_t)(line[i] + pr);
            }
            break;
        default:
            break;
    }
}

static int nearest(const uint8_t *map, int nmap, int r, int g, int b) {
    int best = 0;
    int32_t bd = 0x7FFFFFFF;
    for (int i = 0; i < nmap; i++) {
        int dr = r - map[3 * i];
        int dg = g - map[3 * i + 1];
        int db = b - map[3 * i + 2];
        int32_t dd = dr * dr + dg * dg + db * db;
        if (dd < bd) {
            bd = dd;
            best = i;
            if (dd == 0) {
                break;
            }
        }
    }
    return best;
}

static int nearest_memo(png_t *s, int r, int g, int b) {
    uint32_t key = 0x01000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b;
    uint32_t slot = ((uint32_t)r * 7u + (uint32_t)g * 13u + (uint32_t)b * 29u) & (NCACHE - 1);
    if (s->ckey[slot] != key) {
        s->ckey[slot] = key;
        s->cval[slot] = (uint8_t)nearest(s->map, s->nmap, r, g, b);
    }
    return s->cval[slot];
}

static void put(png_t *s, uint8_t *out, int fmt, int r, int g, int b) {
    switch (fmt) {
        case MOY_PNG_RGB888:
            out[0] = (uint8_t)r;
            out[1] = (uint8_t)g;
            out[2] = (uint8_t)b;
            break;
        case MOY_PNG_RGB565:
        case MOY_PNG_RGB565_SW: {
            uint16_t w = (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
            if (fmt == MOY_PNG_RGB565) {
                out[0] = (uint8_t)(w & 0xFF);
                out[1] = (uint8_t)(w >> 8);
            } else {
                out[0] = (uint8_t)(w >> 8);
                out[1] = (uint8_t)(w & 0xFF);
            }
            break;
        }
        default:
            out[0] = (uint8_t)nearest_memo(s, r, g, b);
            break;
    }
}

// One unfiltered raw row into the output: written straight through at div 1,
// summed into the box at a larger div and written when the box's last row is in.
static int emit(png_t *s, const uint8_t *line, uint8_t *out) {
    const int side = MOY_PNG_SIDE;
    const int div = s->div;
    const int fmt = s->fmt;
    const int ob = fmt_bytes(fmt);
    const int ow = side / div;
    const uint8_t *plte = s->plte;
    const int indexed = s->ctype == 3;
    int r;
    int g;
    int b;
    if (div == 1) {
        uint8_t *o = out + (size_t)s->row * (size_t)side * (size_t)ob;
        for (int x = 0; x < side; x++, o += ob) {
            if (indexed) {
                int v = line[x];
                if (v >= s->npal) {
                    return -1;
                }
                if (fmt == MOY_PNG_INDEX) {
                    o[0] = s->lut[v];
                    continue;
                }
                r = plte[3 * v];
                g = plte[3 * v + 1];
                b = plte[3 * v + 2];
            } else {
                r = line[3 * x];
                g = line[3 * x + 1];
                b = line[3 * x + 2];
            }
            put(s, o, fmt, r, g, b);
        }
        return 0;
    }
    uint32_t *sums = s->sums;
    for (int x = 0; x < side; x++) {
        if (indexed) {
            int v = line[x];
            if (v >= s->npal) {
                return -1;
            }
            r = plte[3 * v];
            g = plte[3 * v + 1];
            b = plte[3 * v + 2];
        } else {
            r = line[3 * x];
            g = line[3 * x + 1];
            b = line[3 * x + 2];
        }
        uint32_t *t = sums + 3 * (x / div);
        t[0] += (uint32_t)r;
        t[1] += (uint32_t)g;
        t[2] += (uint32_t)b;
    }
    if ((s->row + 1) % div == 0) {
        uint32_t area = (uint32_t)div * (uint32_t)div;
        uint32_t half = area / 2;
        uint8_t *o = out + (size_t)(s->row / div) * (size_t)ow * (size_t)ob;
        for (int x = 0; x < ow; x++, o += ob) {
            uint32_t *t = sums + 3 * x;
            put(s, o, fmt, (int)((t[0] + half) / area), (int)((t[1] + half) / area),
                (int)((t[2] + half) / area));
            t[0] = t[1] = t[2] = 0;
        }
    }
    return 0;
}

int moy_png_begin(void *work, size_t work_len, const uint8_t *data, size_t len,
                  int div, int fmt, const uint8_t *pal, int npal) {
    png_t *s = (png_t *)work;
    if (work == NULL || work_len < MOY_PNG_WORK || ((uintptr_t)work & 3u)
        || !div_ok(div) || !fmt_bytes(fmt)) {
        return -1;
    }
    if (fmt == MOY_PNG_INDEX && (pal == NULL || npal < 1 || npal > 256)) {
        return -1;
    }
    s->magic = 0;
    if (data == NULL || len > MOY_PNG_MAX_BYTES || len < 8 || memcmp(data, SIG, 8) != 0) {
        return 0;
    }
    // The chunk list, through IEND, exactly as the reference walks it.
    uint32_t n32 = (uint32_t)len;
    uint32_t p = 8;
    int first = 1;
    int seen_idat = 0;
    uint32_t first_idat = 0;
    uint32_t idat_bytes = 0;
    uint32_t plte_at = 0;
    uint32_t plte_len = 0;
    int ctype = 0;
    for (;;) {
        if (p + 8 > n32) {
            return 0;                       // the file ends before IEND
        }
        uint32_t n = be32(data + p);
        const uint8_t *t = data + p + 4;
        if (n > n32 - p - 8 || n32 - p - 8 - n < 4) {
            return 0;                       // the chunk runs past the end
        }
        if (first) {
            if (memcmp(t, "IHDR", 4) != 0 || n != 13) {
                return 0;
            }
            const uint8_t *h = data + p + 8;
            if (be32(h) != MOY_PNG_SIDE || be32(h + 4) != MOY_PNG_SIDE || h[8] != 8
                || (h[9] != 2 && h[9] != 3) || h[10] || h[11] || h[12]) {
                return 0;
            }
            ctype = h[9];
            first = 0;
        } else if (memcmp(t, "IDAT", 4) == 0) {
            if (!seen_idat) {
                seen_idat = 1;
                first_idat = p;
            }
            idat_bytes += n;
        } else if (memcmp(t, "PLTE", 4) == 0) {
            if (ctype == 3) {
                // After image DATA, as the reference counts it: an empty IDAT
                // ahead of the PLTE does not count.
                if (idat_bytes || n % 3 || n < 3 || n > 768) {
                    return 0;
                }
                plte_at = p + 8;
                plte_len = n;
            }
        } else if (memcmp(t, "tRNS", 4) == 0) {
            return 0;
        } else if (memcmp(t, "IEND", 4) == 0) {
            break;
        } else if (!(t[0] & 0x20)) {
            return 0;                       // a critical chunk that is not a cover's
        }
        p += 12 + n;
    }
    if (ctype == 3 && plte_len == 0) {
        return 0;
    }
    s->len = n32;
    s->ctype = (uint8_t)ctype;
    s->bpp = ctype == 2 ? 3 : 1;
    s->div = (uint8_t)div;
    s->fmt = (uint8_t)fmt;
    s->npal = (uint16_t)(plte_len / 3);
    if (plte_len) {
        memcpy(s->plte, data + plte_at, plte_len);
    }
    if (seen_idat) {
        s->pos = s->end = 0;
        seek_idat(s, data, first_idat);
    } else {
        s->pos = s->end = 0;
    }
    s->bitbuf = 0;
    s->bitcnt = 0;
    s->mode = M_HEAD;
    s->last = 0;
    s->left = 0;
    s->dist = 0;
    s->produced = 0;
    s->adler_a = 1;
    s->adler_b = 0;
    s->wpos = 0;
    s->row = 0;
    s->col = 0;
    s->orow = 0;
    s->cur = 0;
    memset(s->rows[1], 0, ROWMAX);          // the row above the first is zeros
    memset(s->sums, 0, sizeof(s->sums));
    s->nmap = 0;
    if (fmt == MOY_PNG_INDEX) {
        memcpy(s->map, pal, (size_t)npal * 3);
        s->nmap = (uint16_t)npal;
        for (int i = 0; i < NCACHE; i++) {
            s->ckey[i] = 0;
        }
        for (int v = 0; v < s->npal; v++) {
            s->lut[v] = (uint8_t)nearest(s->map, s->nmap, s->plte[3 * v],
                                         s->plte[3 * v + 1], s->plte[3 * v + 2]);
        }
    }
    s->magic = MAGIC;
    return 1;
}

int moy_png_rows(void *work, const uint8_t *data, size_t len, uint8_t *out,
                 size_t out_len, int n) {
    png_t *s = (png_t *)work;
    if (work == NULL || ((uintptr_t)work & 3u) || s->magic != MAGIC
        || data == NULL || (uint32_t)len != s->len || len > MOY_PNG_MAX_BYTES
        || out == NULL || out_len < moy_png_out_size(s->div, s->fmt)) {
        return -1;
    }
    if (s->mode == M_ERROR) {
        return -1;
    }
    const int side = MOY_PNG_SIDE;
    const int oside = side / s->div;
    if (s->mode == M_CHECKED) {
        return 1;
    }
    const int stride = side * s->bpp;
    const int rowlen = stride + 1;
    if (n <= 0 || n > oside) {
        n = oside;
    }
    int target = s->orow + n;
    if (target > oside) {
        target = oside;
    }
    while (s->orow < target) {
        uint8_t *cur = s->rows[s->cur];
        int got = inflate(s, data, cur + s->col, rowlen - s->col);
        if (got < 0) {
            return -1;
        }
        adler(s, cur + s->col, got);
        s->col = (uint16_t)(s->col + got);
        if (s->col < rowlen) {
            return fail(s);                 // the stream ended inside the picture
        }
        int ft = cur[0];
        if (ft > 4) {
            return fail(s);
        }
        unfilter(cur + 1, s->rows[s->cur ^ 1] + 1, stride, s->bpp, ft);
        if (emit(s, cur + 1, out) < 0) {
            return fail(s);
        }
        s->cur ^= 1;
        s->col = 0;
        s->row++;
        if (s->row % s->div == 0) {
            s->orow++;
        }
    }
    if (s->orow < oside) {
        return 0;
    }
    return finish(s, data) < 0 ? -1 : 1;
}
