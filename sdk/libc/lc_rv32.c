// The compiler's helpers a RISC-V module cannot take from the toolchain's
// libgcc (sdk/README.md): those objects address a table or a jump table by
// absolute address, which no module can relocate. Each here is plain C that
// calls no helper of its own: bit counts, 64-bit division, double division
// and the 64-bit float conversions. The rest of libgcc links as it is.
#include <stdint.h>

#if defined(__riscv) || defined(LC_RV32_TEST)

#if defined(LC_RV32_TEST)
#define H(name) lc_test_##name
#define LC_API
#else
#define H(name) name
#define LC_API __attribute__((visibility("hidden")))
#endif

LC_API int H(__clzsi2)(uint32_t x) {
    if (x == 0) {
        return 32;
    }
    int n = 0;
    if (!(x & 0xffff0000u)) {
        n += 16;
        x <<= 16;
    }
    if (!(x & 0xff000000u)) {
        n += 8;
        x <<= 8;
    }
    if (!(x & 0xf0000000u)) {
        n += 4;
        x <<= 4;
    }
    if (!(x & 0xc0000000u)) {
        n += 2;
        x <<= 2;
    }
    if (!(x & 0x80000000u)) {
        n += 1;
    }
    return n;
}

LC_API int H(__clzdi2)(uint64_t x) {
    uint32_t hi = (uint32_t)(x >> 32);
    return hi ? H(__clzsi2)(hi) : 32 + H(__clzsi2)((uint32_t)x);
}

LC_API int H(__ctzsi2)(uint32_t x) {
    if (x == 0) {
        return 32;
    }
    int n = 0;
    while (!(x & 1u)) {
        x >>= 1;
        n++;
    }
    return n;
}

LC_API int H(__ctzdi2)(uint64_t x) {
    uint32_t lo = (uint32_t)x;
    return lo ? H(__ctzsi2)(lo) : 32 + H(__ctzsi2)((uint32_t)(x >> 32));
}

LC_API int H(__ffssi2)(uint32_t x) {
    return x ? H(__ctzsi2)(x) + 1 : 0;
}

LC_API int H(__ffsdi2)(uint64_t x) {
    return x ? H(__ctzdi2)(x) + 1 : 0;
}

LC_API uint64_t H(__udivmoddi4)(uint64_t n, uint64_t d, uint64_t *rem) {
    if (d == 0) {
        if (rem != 0) {
            *rem = n;
        }
        return ~0ULL;
    }
    uint64_t q = 0, r = 0;
    int bits = 64 - H(__clzdi2)(n | 1u);
    for (int i = bits - 1; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1u);
        if (r >= d) {
            r -= d;
            q |= 1ULL << i;
        }
    }
    if (rem != 0) {
        *rem = r;
    }
    return q;
}

LC_API uint64_t H(__udivdi3)(uint64_t n, uint64_t d) {
    return H(__udivmoddi4)(n, d, 0);
}

LC_API uint64_t H(__umoddi3)(uint64_t n, uint64_t d) {
    uint64_t r;
    H(__udivmoddi4)(n, d, &r);
    return r;
}

LC_API int64_t H(__divmoddi4)(int64_t a, int64_t b, int64_t *rem) {
    uint64_t ua = a < 0 ? 0ULL - (uint64_t)a : (uint64_t)a;
    uint64_t ub = b < 0 ? 0ULL - (uint64_t)b : (uint64_t)b;
    uint64_t r;
    uint64_t q = H(__udivmoddi4)(ua, ub, &r);
    if (rem != 0) {
        *rem = a < 0 ? (int64_t)(0ULL - r) : (int64_t)r;
    }
    return (a < 0) != (b < 0) ? (int64_t)(0ULL - q) : (int64_t)q;
}

LC_API int64_t H(__divdi3)(int64_t a, int64_t b) {
    return H(__divmoddi4)(a, b, 0);
}

LC_API int64_t H(__moddi3)(int64_t a, int64_t b) {
    int64_t r;
    H(__divmoddi4)(a, b, &r);
    return r;
}

// -- double division, IEEE 754, round to nearest even ---------------------------------------

#define SIGN (1ULL << 63)
#define FRAC ((1ULL << 52) - 1u)
#define QNAN 0x7ff8000000000000ULL

typedef union {
    double d;
    uint64_t u;
} du_t;

LC_API double H(__divdf3)(double a, double b) {
    du_t x = { a }, y = { b }, out;
    uint64_t sign = (x.u ^ y.u) & SIGN;
    int ea = (int)((x.u >> 52) & 0x7ff), eb = (int)((y.u >> 52) & 0x7ff);
    uint64_t ma = x.u & FRAC, mb = y.u & FRAC;
    if (ea == 0x7ff) {
        if (ma) {
            out.u = x.u | (1ULL << 51);
        } else if (eb == 0x7ff) {
            out.u = mb ? y.u | (1ULL << 51) : QNAN;
        } else {
            out.u = sign | (0x7ffULL << 52);
        }
        return out.d;
    }
    if (eb == 0x7ff) {
        out.u = mb ? y.u | (1ULL << 51) : sign;
        return out.d;
    }
    if (eb == 0 && mb == 0) {
        out.u = (ea == 0 && ma == 0) ? QNAN : sign | (0x7ffULL << 52);
        return out.d;
    }
    if (ea == 0 && ma == 0) {
        out.u = sign;
        return out.d;
    }
    if (ea == 0) {
        int sh = H(__clzdi2)(ma) - 11;
        ma <<= sh;
        ea = 1 - sh;
    } else {
        ma |= 1ULL << 52;
    }
    if (eb == 0) {
        int sh = H(__clzdi2)(mb) - 11;
        mb <<= sh;
        eb = 1 - sh;
    } else {
        mb |= 1ULL << 52;
    }
    int e = ea - eb + 1023;
    if (ma < mb) {
        ma <<= 1;
        e--;
    }
    // 56 quotient bits: 53 of the result, a guard bit and two below it.
    uint64_t q = 0, r = ma;
    for (int i = 0; i < 56; i++) {
        q <<= 1;
        if (r >= mb) {
            r -= mb;
            q |= 1u;
        }
        r <<= 1;
    }
    q |= r != 0;
    if (e >= 0x7ff) {
        out.u = sign | (0x7ffULL << 52);
        return out.d;
    }
    if (e <= 0) {
        int sh = 1 - e;
        if (sh > 60) {
            q = q != 0;
        } else {
            uint64_t lost = q & ((1ULL << sh) - 1u);
            q = (q >> sh) | (lost != 0);
        }
        e = 0;
    }
    uint64_t m = q >> 3;
    unsigned low = (unsigned)(q & 7u);
    if (low > 4 || (low == 4 && (m & 1u))) {
        m++;
    }
    out.u = sign | ((e > 0 ? (uint64_t)(e - 1) << 52 : 0) + m);
    return out.d;
}

// -- 64-bit integers and floats ------------------------------------------------------------

typedef union {
    float f;
    uint32_t u;
} fu_t;

static float from_u64(uint64_t u) {
    if (u == 0) {
        return 0.0f;
    }
    int n = 64 - H(__clzdi2)(u);
    if (n <= 24) {
        return (float)(uint32_t)u;
    }
    int sh = n - 24;
    uint64_t m = u >> sh, rest = u & ((1ULL << sh) - 1u), half = 1ULL << (sh - 1);
    if (rest > half || (rest == half && (m & 1u))) {
        m++;
    }
    fu_t p = { .u = (uint32_t)(127 + sh) << 23 };
    return (float)(uint32_t)m * p.f;
}

LC_API float H(__floatundisf)(uint64_t u) {
    return from_u64(u);
}

LC_API float H(__floatdisf)(int64_t v) {
    return v < 0 ? -from_u64(0ULL - (uint64_t)v) : from_u64((uint64_t)v);
}

LC_API uint64_t H(__fixunssfdi)(float f) {
    fu_t x = { f };
    if (x.u & 0x80000000u) {
        return 0;
    }
    int e = (int)((x.u >> 23) & 0xff) - 127;
    if (e < 0) {
        return 0;
    }
    if (e >= 64) {
        return ~0ULL;
    }
    uint64_t m = (x.u & 0x7fffffu) | 0x800000u;
    return e < 23 ? m >> (23 - e) : m << (e - 23);
}

LC_API double H(__powidf2)(double x, int n) {
    unsigned k = n < 0 ? 0u - (unsigned)n : (unsigned)n;
    double r = 1.0;
    while (k) {
        if (k & 1u) {
            r *= x;
        }
        x *= x;
        k >>= 1;
    }
    return n < 0 ? H(__divdf3)(1.0, r) : r;
}

LC_API float H(__powisf2)(float x, int n) {
    unsigned k = n < 0 ? 0u - (unsigned)n : (unsigned)n;
    float r = 1.0f;
    while (k) {
        if (k & 1u) {
            r *= x;
        }
        x *= x;
        k >>= 1;
    }
    return n < 0 ? 1.0f / r : r;
}

#endif
