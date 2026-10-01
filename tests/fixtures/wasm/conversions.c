/* Saturating float-to-int conversions at every limit: the guard for what the
 * AOT compiler assumes of a board's core -- on the ESP32-S3, that TRUNC.S
 * clamps a value past either end of the int32 range to that end and gives
 * INT32_MAX for a NaN, so the saturating conversion is TRUNC.S and a NaN
 * check (native/moy_wasm/README.md, "Float-to-int conversions").
 *
 * check() returns how many conversions gave something other than wasm's
 * answer: 0 is the only right one. The inputs come from memory, so the
 * compiler cannot fold a single conversion.
 *
 * Built by tools/wasm_module.py's conversions_wasm().
 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef int i32;

#define SAT __attribute__((noinline, target("nontrapping-fptoint")))

SAT static i32 s32(float f) { return __builtin_wasm_trunc_saturate_s_i32_f32(f); }
SAT static u32 u32f(float f) { return __builtin_wasm_trunc_saturate_u_i32_f32(f); }
SAT static i32 s64(double d) { return __builtin_wasm_trunc_saturate_s_i32_f64(d); }
SAT static u32 u64f(double d) { return __builtin_wasm_trunc_saturate_u_i32_f64(d); }

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
    return wrong;
}
