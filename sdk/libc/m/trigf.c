// sinf, cosf and tanf: the float kernels of libm/ over an argument reduced by
// libm/'s float reduction up to 2^7*pi/2, and beyond by libm_dbl/'s double
// one, which keeps every bit of the reduced argument at any magnitude.
#include "fdlibm.h"

int __rem_pio2(double, double *);

static __int32_t reduce(float x, float *y) {
    __int32_t ix;
    GET_FLOAT_WORD(ix, x);
    if ((ix & 0x7fffffff) <= 0x43490f80) {
        return __ieee754_rem_pio2f(x, y);
    }
    double d[2];
    __int32_t n = __rem_pio2(x, d);
    y[0] = (float)d[0];
    y[1] = (float)((d[0] - y[0]) + d[1]);
    return n;
}

float sinf(float x) {
    __int32_t ix;
    GET_FLOAT_WORD(ix, x);
    ix &= 0x7fffffff;
    if (ix <= 0x3f490fd8) {
        return __kernel_sinf(x, 0.0f, 0);
    }
    if (!FLT_UWORD_IS_FINITE(ix)) {
        return x - x;
    }
    float y[2];
    switch (reduce(x, y) & 3) {
    case 0:
        return __kernel_sinf(y[0], y[1], 1);
    case 1:
        return __kernel_cosf(y[0], y[1]);
    case 2:
        return -__kernel_sinf(y[0], y[1], 1);
    default:
        return -__kernel_cosf(y[0], y[1]);
    }
}

float cosf(float x) {
    __int32_t ix;
    GET_FLOAT_WORD(ix, x);
    ix &= 0x7fffffff;
    if (ix <= 0x3f490fd8) {
        return __kernel_cosf(x, 0.0f);
    }
    if (!FLT_UWORD_IS_FINITE(ix)) {
        return x - x;
    }
    float y[2];
    switch (reduce(x, y) & 3) {
    case 0:
        return __kernel_cosf(y[0], y[1]);
    case 1:
        return -__kernel_sinf(y[0], y[1], 1);
    case 2:
        return -__kernel_cosf(y[0], y[1]);
    default:
        return __kernel_sinf(y[0], y[1], 1);
    }
}

float tanf(float x) {
    __int32_t ix;
    GET_FLOAT_WORD(ix, x);
    ix &= 0x7fffffff;
    if (ix <= 0x3f490fda) {
        return __kernel_tanf(x, 0.0f, 1);
    }
    if (!FLT_UWORD_IS_FINITE(ix)) {
        return x - x;
    }
    float y[2];
    __int32_t n = reduce(x, y);
    return __kernel_tanf(y[0], y[1], 1 - ((n & 1) << 1));
}
