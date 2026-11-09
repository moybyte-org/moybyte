// math: the module's <math.h>, float and double, against what glibc's libm
// returns for the same calls (rows.h), for the native tier's tests
// (tests/test_sdk_libm.py). A line for each answer more than 2 ulp off, then
// the count; the status is 0 when none is.
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct row {
    uint8_t fn;
    double x, y;
    uint64_t want;
};

#include "rows.h"

typedef struct {
    const char *name;
    double (*d1)(double);
    double (*d2)(double, double);
    float (*f1)(float);
    float (*f2)(float, float);
} fn_t;

#define D1(n) {#n, n, 0, 0, 0},
#define D2(n) {#n, 0, n, 0, 0},
#define F1(n) {#n "f", 0, 0, n##f, 0},
#define F2(n) {#n "f", 0, 0, 0, n##f},
static const fn_t FNS[] = {FNS_D1(D1) FNS_D2(D2) FNS_D1(F1) FNS_D2(F2)};

// The distance in representable values, signed zeros one value, NaNs any NaN.
static uint64_t apart(uint64_t a, uint64_t b, int bits) {
    uint64_t sign = 1ull << (bits - 1), mag = sign - 1;
    uint64_t exp = bits == 64 ? 0x7ff0000000000000ull : 0x7f800000ull;
    int an = (a & mag) > exp, bn = (b & mag) > exp;
    if (an || bn) {
        return an && bn ? 0 : UINT64_MAX;
    }
    int64_t ka = (a & sign) ? -(int64_t)(a & mag) : (int64_t)(a & mag);
    int64_t kb = (b & sign) ? -(int64_t)(b & mag) : (int64_t)(b & mag);
    return ka > kb ? (uint64_t)(ka - kb) : (uint64_t)(kb - ka);
}

int main(void) {
    const struct row *volatile rows = ROWS;
    int n = sizeof ROWS / sizeof ROWS[0], off = 0;
    for (int i = 0; i < n; i++) {
        const struct row *r = &rows[i];
        const fn_t *f = &FNS[r->fn];
        uint64_t got;
        int bits;
        if (f->d1 || f->d2) {
            double v = f->d1 ? f->d1(r->x) : f->d2(r->x, r->y);
            memcpy(&got, &v, 8);
            bits = 64;
        } else {
            float v = f->f1 ? f->f1((float)r->x) : f->f2((float)r->x, (float)r->y);
            uint32_t w;
            memcpy(&w, &v, 4);
            got = w;
            bits = 32;
        }
        if (apart(got, r->want, bits) > 2) {
            off++;
            printf("off %s(%a, %a): %llx, glibc %llx\n", f->name, r->x, r->y,
                   (unsigned long long)got, (unsigned long long)r->want);
        }
    }
    printf("math: %d of %d within 2 ulp of glibc\n", n - off, n);
    return off != 0;
}
