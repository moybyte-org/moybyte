/* Misaligned loads and stores at every scalar width: the guard for what the
 * AOT compilers assume of the boards' cores -- that a load or a store of any
 * width takes a misaligned address in hardware, so an access whose alignment
 * the compiler cannot see is emitted at its own width rather than as bytes
 * (native/moy_wasm/README.md, "Misaligned access").
 *
 * check() returns how many accesses read or wrote bytes other than byte-wise
 * composition says: 0 is the only right answer, and a core that trapped
 * instead would take the board down with it. The sweep walks every offset of
 * a buffer longer than 64 KB, so it crosses every cache line in it and at
 * least one MMU page, wherever the runtime placed linear memory.
 *
 * Built by tools/wasm_module.py's misaligned_wasm().
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

#define SPAN (72 * 1024)
#define NI __attribute__((noinline))

static u8 buf[SPAN + 16];
static u8 twin[16] __attribute__((aligned(8)));

static u64 bytes_le(const u8 *p, int n)
{
    u64 v = 0;
    for (int i = n - 1; i >= 0; i--) v = (v << 8) | ((volatile const u8 *)p)[i];
    return v;
}

static void put_le(u8 *p, u64 v, int n)
{
    for (int i = 0; i < n; i++) ((volatile u8 *)p)[i] = (u8)(v >> (8 * i));
}

/* Each access is its own function taking the address, so no caller's
   constant reaches it and the compiler cannot see its alignment. */
NI static u32 ld16u(const u8 *p) { return *(const u16 *)p; }
NI static int ld16s(const u8 *p) { return *(const short *)p; }
NI static u32 ld32(const u8 *p) { return *(const u32 *)p; }
NI static u64 ld64(const u8 *p) { return *(const u64 *)p; }
NI static int eq32f(const u8 *p, const u8 *q) { return *(const float *)p == *(const float *)q; }
NI static int eq64f(const u8 *p, const u8 *q) { return *(const double *)p == *(const double *)q; }
NI static void st16(u8 *p, u32 v) { *(u16 *)p = (u16)v; }
NI static void st32(u8 *p, u32 v) { *(u32 *)p = v; }
NI static void st64(u8 *p, u64 v) { *(u64 *)p = v; }
NI static void st32f(u8 *p, int i) { *(float *)p = (float)i * 0.5f; }
NI static void st64f(u8 *p, int i) { *(double *)p = (double)i * 0.25; }

static u8 pattern(u32 i) { return (u8)((i * 2654435761u) >> 24); }

__attribute__((export_name("check"))) int check(void)
{
    int bad = 0;
    for (u32 i = 0; i < SPAN + 16; i++) buf[i] = pattern(i);
    for (u32 o = 0; o < SPAN; o++) {
        u8 *p = buf + o;
        if (ld16u(p) != (u32)bytes_le(p, 2)) bad++;
        if (ld16s(p) != (int)(short)bytes_le(p, 2)) bad++;
        if (ld32(p) != (u32)bytes_le(p, 4)) bad++;
        if (ld64(p) != bytes_le(p, 8)) bad++;
    }
    /* Floating point: a value whose bits are known -- 1.2345f and pi, never
       a NaN -- written byte-wise here and at an aligned twin, then compared
       as floats, which takes a floating-point load of each. */
    for (u32 o = 0; o < SPAN; o += 7) {
        u8 *p = buf + o;
        put_le(twin, 0x3F9E0419u, 4);
        put_le(p, 0x3F9E0419u, 4);
        if (!eq32f(p, twin)) bad++;
        put_le(twin, 0x400921FB54442D18ull, 8);
        put_le(p, 0x400921FB54442D18ull, 8);
        if (!eq64f(p, twin)) bad++;
    }
    for (u32 o = 0; o < SPAN; o++) {
        u8 *p = buf + o;
        st16(p, 0x5E6Fu ^ o);
        if (bytes_le(p, 2) != ((0x5E6Fu ^ o) & 0xFFFFu)) bad++;
        st32(p, 0xA1B2C3D4u ^ o);
        if (bytes_le(p, 4) != (0xA1B2C3D4u ^ o)) bad++;
        st64(p, 0x0102030405060708ull ^ o);
        if (bytes_le(p, 8) != (0x0102030405060708ull ^ o)) bad++;
    }
    for (u32 o = 0; o < SPAN; o += 7) {
        u8 *p = buf + o;
        int k = (int)(o % 64u);
        float f = (float)k * 0.5f;
        double d = (double)k * 0.25;
        u32 fb;
        u64 db;
        __builtin_memcpy(&fb, &f, 4);
        __builtin_memcpy(&db, &d, 8);
        st32f(p, k);
        if (bytes_le(p, 4) != fb) bad++;
        st64f(p, k);
        if (bytes_le(p, 8) != db) bad++;
    }
    return bad;
}
