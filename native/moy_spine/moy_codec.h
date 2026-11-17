// moy_codec: the small codecs every native module shares, each with one rule.
// Header-only, so a module takes it with the include and nothing to link.
//
//   base64  moy_b64_value      one character's six bits
//           moy_b64_decode     binascii.a2b_base64's reading
//           moy_b64_encode     padded, or base64url without padding
//   hex     moy_hex_value      one digit, either case
//           moy_hex_decode     exactly 2 * want digits
//           moy_hex_encode     lower case
//   UTF-8   moy_utf8_next      one codepoint, by the strict rule
//           moy_utf8_valid     every byte, by the strict rule
//           moy_utf8_whole     the bytes up to the last whole sequence
//           moy_utf8_put       one codepoint written
//           moy_utf8_count     the codepoints a well-formed run holds
//
// THE STRICT RULE: a lead byte is C2-DF, E0-EF or F0-F4 and each continuation
// byte 80-BF; no overlong form, no surrogate, nothing past U+10FFFF. The one
// reader with a rule of its own is the outline font's (moy_gfx/moy_path.c),
// which takes what this refuses on its side of the call.

#ifndef MOY_CODEC_H
#define MOY_CODEC_H

#include <stddef.h>
#include <stdint.h>

// The larger functions are plain statics, not `inline`: the compiler keeps one
// copy per module and inlines them only where that pays, where the hint would
// copy each body into every call site of every module.
#ifndef MOY_SPINE_OUTLINE
#define MOY_SPINE_OUTLINE __attribute__((unused))
#endif

// -- base64 ------------------------------------------------------------------------

// The six bits `c` stands for in the standard alphabet, or -1.
static inline int moy_b64_value(int c) {
    if (c >= 'A' && c <= 'Z') {
        return c - 'A';
    }
    if (c >= 'a' && c <= 'z') {
        return c - 'a' + 26;
    }
    if (c >= '0' && c <= '9') {
        return c - '0' + 52;
    }
    return c == '+' ? 62 : c == '/' ? 63 : -1;
}

// `s` decoded into `out` (room for n / 4 * 3 + 3 bytes) as binascii.a2b_base64
// reads it: a byte outside the alphabet is passed over, the first '=' that
// completes a quantum ends the data, and a quantum left short is an error.
// The decoded length, or -1.
static MOY_SPINE_OUTLINE long moy_b64_decode(const char *s, size_t n, uint8_t *out) {
    uint32_t acc = 0;
    int bits = 0, q = 0;
    long k = 0;
    for (size_t i = 0; i < n; i++) {
        int d = moy_b64_value((unsigned char)s[i]);
        if (d < 0) {
            if (s[i] == '=' && q >= 2) {
                return k;           // the padding that ends a short quantum
            }
            continue;
        }
        acc = (acc << 6) | (uint32_t)d;
        bits += 6;
        q = (q + 1) & 3;
        if (bits >= 8) {
            bits -= 8;
            out[k++] = (uint8_t)(acc >> bits);
        }
    }
    return q == 0 ? k : -1;
}

// `n` bytes as base64 into `out`: the standard alphabet padded with '=' to
// (n + 2) / 3 * 4 characters, or with `url` base64url's alphabet and no
// padding. No NUL is written. The characters written.
static MOY_SPINE_OUTLINE size_t moy_b64_encode(const uint8_t *p, size_t n, char *out, int url) {
    const char *a = url ? "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                        : "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t k = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)p[i] << 16 | (i + 1 < n ? (uint32_t)p[i + 1] << 8 : 0)
                     | (i + 2 < n ? p[i + 2] : 0);
        out[k++] = a[v >> 18 & 63];
        out[k++] = a[v >> 12 & 63];
        if (i + 1 < n) {
            out[k++] = a[v >> 6 & 63];
        } else if (!url) {
            out[k++] = '=';
        }
        if (i + 2 < n) {
            out[k++] = a[v & 63];
        } else if (!url) {
            out[k++] = '=';
        }
    }
    return k;
}

// -- hex ---------------------------------------------------------------------------

// The value of hex digit `c`, either case, or -1.
static inline int moy_hex_value(int c) {
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

// `want` bytes from exactly 2 * want hex digits: 1, or 0 for any other length
// or a character that is not a digit.
static MOY_SPINE_OUTLINE int moy_hex_decode(const char *s, size_t n, uint8_t *out, size_t want) {
    if (s == NULL || n != want * 2u) {
        return 0;
    }
    for (size_t i = 0; i < want; i++) {
        int hi = moy_hex_value((unsigned char)s[2 * i]);
        int lo = moy_hex_value((unsigned char)s[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            return 0;
        }
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return 1;
}

// `n` bytes as 2n lower-case digits and a NUL.
static MOY_SPINE_OUTLINE void moy_hex_encode(const uint8_t *p, size_t n, char *out) {
    static const char D[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = D[p[i] >> 4];
        out[2 * i + 1] = D[p[i] & 15];
    }
    out[2 * n] = '\0';
}

// -- UTF-8 -------------------------------------------------------------------------

// The codepoint at s[*i] (*i < n), *i moved past it; -1 for a sequence the
// strict rule refuses, *i moved past its first byte alone.
static MOY_SPINE_OUTLINE int32_t moy_utf8_next(const void *s_, size_t n, size_t *i) {
    const uint8_t *s = s_;
    uint8_t b = s[(*i)++];
    if (b < 0x80) {
        return b;
    }
    size_t len = b >= 0xC2 && b <= 0xDF ? 2u : b >= 0xE0 && b <= 0xEF ? 3u
                 : b >= 0xF0 && b <= 0xF4 ? 4u : 0u;
    if (len == 0 || n - *i < len - 1u) {
        return -1;
    }
    uint32_t cp = b & (0x7Fu >> len);
    for (size_t k = 0; k + 1u < len; k++) {
        uint8_t c = s[*i + k];
        if ((c & 0xC0) != 0x80) {
            return -1;
        }
        cp = cp << 6 | (c & 0x3Fu);
    }
    if ((len == 3 && cp < 0x800) || (len == 4 && (cp < 0x10000 || cp > 0x10FFFF))
        || (cp >= 0xD800 && cp <= 0xDFFF)) {
        return -1;
    }
    *i += len - 1u;
    return (int32_t)cp;
}

// 1 when every byte of `s` is UTF-8 by the strict rule, else 0.
static MOY_SPINE_OUTLINE int moy_utf8_valid(const void *s, size_t n) {
    for (size_t i = 0; i < n;) {
        if (moy_utf8_next(s, n, &i) < 0) {
            return 0;
        }
    }
    return 1;
}

// The length of `s` up to its last whole sequence: a lead byte among the last
// three whose sequence the bytes do not finish is cut, with what follows it.
static MOY_SPINE_OUTLINE size_t moy_utf8_whole(const void *s_, size_t n) {
    const uint8_t *s = s_;
    for (size_t back = 1; back <= 3 && back <= n; back++) {
        uint8_t c = s[n - back];
        if ((c & 0xC0) == 0x80) {
            continue;
        }
        size_t need = c >= 0xF0 ? 4u : c >= 0xE0 ? 3u : c >= 0xC0 ? 2u : 1u;
        return need > back ? n - back : n;
    }
    return n;
}

// Codepoint `cp` (at most U+10FFFF) as UTF-8 into `out`: the bytes written, 1 to 4.
static MOY_SPINE_OUTLINE size_t moy_utf8_put(uint32_t cp, char *out) {
    if (cp < 0x80) {
        out[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

// The codepoints in `n` bytes of well-formed UTF-8: the bytes that are not
// continuation bytes.
static MOY_SPINE_OUTLINE size_t moy_utf8_count(const void *s_, size_t n) {
    const uint8_t *s = s_;
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        k += (s[i] & 0xC0) != 0x80;
    }
    return k;
}

#endif // MOY_CODEC_H
