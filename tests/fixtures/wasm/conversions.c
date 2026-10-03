/* Float-to-int conversions at every limit: the guard for what the AOT
 * compiler assumes of a board's core -- on the ESP32-S3, that TRUNC.S clamps
 * a value past either end of the int32 range to that end and gives INT32_MAX
 * for a NaN, so the saturating conversion is TRUNC.S and a NaN check
 * (native/moy_wasm/README.md, "Float-to-int conversions") -- and for the
 * 64-bit ones, which neither core has an instruction for: the compiler calls
 * a helper (__fixsfdi and its kin) the board's runtime must resolve, or the
 * module does not load at all (#229).
 *
 * check() returns how many conversions gave something other than wasm's
 * answer: 0 is the only right one. The inputs come from memory, so the
 * compiler cannot fold a single conversion. The trapping 64-bit conversions
 * are given only inputs in their range.
 *
 * Built by tools/wasm_module.py's conversions_wasm().
 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef int i32;
typedef long long i64;

#define SAT __attribute__((noinline, target("nontrapping-fptoint")))
#define TRAP __attribute__((noinline))

SAT static i32 s32(float f) { return __builtin_wasm_trunc_saturate_s_i32_f32(f); }
SAT static u32 u32f(float f) { return __builtin_wasm_trunc_saturate_u_i32_f32(f); }
SAT static i32 s64(double d) { return __builtin_wasm_trunc_saturate_s_i32_f64(d); }
SAT static u32 u64f(double d) { return __builtin_wasm_trunc_saturate_u_i32_f64(d); }

/* to 64 bits: saturating, then trapping */
SAT static i64 sat_s64_f32(float f) { return __builtin_wasm_trunc_saturate_s_i64_f32(f); }
SAT static u64 sat_u64_f32(float f) { return __builtin_wasm_trunc_saturate_u_i64_f32(f); }
SAT static i64 sat_s64_f64(double d) { return __builtin_wasm_trunc_saturate_s_i64_f64(d); }
SAT static u64 sat_u64_f64(double d) { return __builtin_wasm_trunc_saturate_u_i64_f64(d); }
TRAP static i64 trap_s64_f32(float f) { return __builtin_wasm_trunc_s_i64_f32(f); }
TRAP static u64 trap_u64_f32(float f) { return __builtin_wasm_trunc_u_i64_f32(f); }
TRAP static i64 trap_s64_f64(double d) { return __builtin_wasm_trunc_s_i64_f64(d); }
TRAP static u64 trap_u64_f64(double d) { return __builtin_wasm_trunc_u_i64_f64(d); }

/* bits, signed result, unsigned result */
static volatile const u32 F32[][3] = {
    {0x7fc00000u, 0, 0},                       /* NaN */
    {0xffc00000u, 0, 0},                       /* -NaN */
    {0x7f800001u, 0, 0},                       /* signalling NaN */
    {0xff800001u, 0, 0},
    {0x7f800000u, 0x7fffffffu, 0xffffffffu},   /* +inf */
    {0xff800000u, 0x80000000u, 0},             /* -inf */
    {0x4f000000u, 0x7fffffffu, 0x80000000u},   /* 2^31 */
    {0x4effffffu, 0x7fffff80u, 0x7fffff80u},   /* the largest float below it */
    {0xcf000000u, 0x80000000u, 0},             /* -2^31 */
    {0xcf000001u, 0x80000000u, 0},             /* just below */
    {0x4f800000u, 0x7fffffffu, 0xffffffffu},   /* 2^32 */
    {0x4f7fffffu, 0x7fffffffu, 0xffffff00u},   /* the largest float below it */
    {0xbf800000u, 0xffffffffu, 0},             /* -1 */
    {0xbf7fffffu, 0, 0},                       /* just above -1 */
    {0xbf000000u, 0, 0},                       /* -0.5 */
    {0x80000000u, 0, 0},                       /* -0 */
    {0x3fc00000u, 1, 1},                       /* 1.5 */
    {0xbfc00000u, 0xffffffffu, 0},             /* -1.5 */
    {0x4e800000u, 0x40000000u, 0x40000000u},   /* 2^30 */
    {0xd0000000u, 0x80000000u, 0},             /* -2^33 */
    {0x00000001u, 0, 0},                       /* the smallest denormal */
    {0x7f7fffffu, 0x7fffffffu, 0xffffffffu},   /* the largest float */
};

static volatile const u64 F64[][3] = {
    {0x7ff8000000000000ull, 0, 0},
    {0x7ff0000000000000ull, 0x7fffffffu, 0xffffffffu},
    {0xfff0000000000000ull, 0x80000000u, 0},
    {0x41dfffffffffffffull, 0x7fffffffu, 0x7fffffffu},   /* 2^31 - 2^-22 */
    {0x41e0000000000000ull, 0x7fffffffu, 0x80000000u},   /* 2^31 */
    {0xc1e0000000200000ull, 0x80000000u, 0},             /* -2^31 - 1 */
    {0xc1dfffffffffffffull, 0x80000001u, 0},             /* just above -2^31 */
    {0x41efffffffe00000ull, 0x7fffffffu, 0xffffffffu},   /* 2^32 - 1 */
    {0x41f0000000000000ull, 0x7fffffffu, 0xffffffffu},   /* 2^32 */
    {0xbfefffffffffffffull, 0, 0},                       /* just above -1 */
};

#define MAX64 0x7fffffffffffffffull
#define MIN64 0x8000000000000000ull
#define ALL64 0xffffffffffffffffull
#define S 1   /* in the signed trapping conversion's range */
#define U 2   /* in the unsigned one's */

/* bits, signed result, unsigned result, which trapping conversions take it */
static volatile const struct { u32 bits; u64 s, u; u32 trap; } F32_64[] = {
    {0x7fc00000u, 0, 0, 0},                          /* NaN */
    {0xffc00000u, 0, 0, 0},                          /* -NaN */
    {0x7f800000u, MAX64, ALL64, 0},                  /* +inf */
    {0xff800000u, MIN64, 0, 0},                      /* -inf */
    {0x5f000000u, MAX64, MIN64, U},                  /* 2^63 */
    {0x5effffffu, 0x7fffff8000000000ull, 0x7fffff8000000000ull, S | U},
    {0xdf000000u, MIN64, 0, S},                      /* -2^63 */
    {0xdf000001u, MIN64, 0, 0},                      /* just below */
    {0x5f800000u, MAX64, ALL64, 0},                  /* 2^64 */
    {0x5f7fffffu, MAX64, 0xffffff0000000000ull, U},  /* the largest float below it */
    {0x4f800000u, 0x100000000ull, 0x100000000ull, S | U},   /* 2^32 */
    {0xd0000000u, 0xfffffffe00000000ull, 0, S},      /* -2^33 */
    {0xbf800000u, ALL64, 0, S},                      /* -1 */
    {0xbf7fffffu, 0, 0, S | U},                      /* just above -1 */
    {0xbfc00000u, ALL64, 0, S},                      /* -1.5 */
    {0x3fc00000u, 1, 1, S | U},                      /* 1.5 */
    {0x80000000u, 0, 0, S | U},                      /* -0 */
    {0x00000001u, 0, 0, S | U},                      /* the smallest denormal */
};

static volatile const struct { u64 bits; u64 s, u; u32 trap; } F64_64[] = {
    {0x7ff8000000000000ull, 0, 0, 0},                /* NaN */
    {0x7ff0000000000000ull, MAX64, ALL64, 0},        /* +inf */
    {0xfff0000000000000ull, MIN64, 0, 0},            /* -inf */
    {0x43e0000000000000ull, MAX64, MIN64, U},        /* 2^63 */
    {0x43dfffffffffffffull, 0x7ffffffffffffc00ull, 0x7ffffffffffffc00ull, S | U},
    {0xc3e0000000000000ull, MIN64, 0, S},            /* -2^63 */
    {0xc3e0000000000001ull, MIN64, 0, 0},            /* just below */
    {0x43f0000000000000ull, MAX64, ALL64, 0},        /* 2^64 */
    {0x43efffffffffffffull, MAX64, 0xfffffffffffff800ull, U},
    {0x4330000000000001ull, 0x10000000000001ull, 0x10000000000001ull, S | U},
    {0xbff0000000000000ull, ALL64, 0, S},            /* -1 */
    {0xbfefffffffffffffull, 0, 0, S | U},            /* just above -1 */
};

__attribute__((export_name("check"))) int check(void)
{
    int wrong = 0;
    for (unsigned i = 0; i < sizeof F32 / sizeof F32[0]; i++) {
        union { u32 u; float f; } c = { F32[i][0] };
        wrong += (u32)s32(c.f) != F32[i][1];
        wrong += u32f(c.f) != F32[i][2];
    }
    for (unsigned i = 0; i < sizeof F64 / sizeof F64[0]; i++) {
        union { u64 u; double d; } c = { F64[i][0] };
        wrong += (u32)s64(c.d) != (u32)F64[i][1];
        wrong += u64f(c.d) != (u32)F64[i][2];
    }
    for (unsigned i = 0; i < sizeof F32_64 / sizeof F32_64[0]; i++) {
        union { u32 u; float f; } c = { F32_64[i].bits };
        wrong += (u64)sat_s64_f32(c.f) != F32_64[i].s;
        wrong += sat_u64_f32(c.f) != F32_64[i].u;
        if (F32_64[i].trap & S)
            wrong += (u64)trap_s64_f32(c.f) != F32_64[i].s;
        if (F32_64[i].trap & U)
            wrong += trap_u64_f32(c.f) != F32_64[i].u;
    }
    for (unsigned i = 0; i < sizeof F64_64 / sizeof F64_64[0]; i++) {
        union { u64 u; double d; } c = { F64_64[i].bits };
        wrong += (u64)sat_s64_f64(c.d) != F64_64[i].s;
        wrong += sat_u64_f64(c.d) != F64_64[i].u;
        if (F64_64[i].trap & S)
            wrong += (u64)trap_s64_f64(c.d) != F64_64[i].s;
        if (F64_64[i].trap & U)
            wrong += trap_u64_f64(c.d) != F64_64[i].u;
    }
    return wrong;
}
