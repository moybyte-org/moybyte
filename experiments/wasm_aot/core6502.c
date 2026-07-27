/* #158 spike: the SAME 6502 micro-core as spike6502.lua, in C, compiled to
 * wasm32. Same 8 opcodes, same memory model (2KB NES RAM mirrored, PRG at
 * $8000), same test program -- so instr/sec is directly comparable to the Lua
 * numbers measured on host and on the P4.
 *
 * Dispatch is a switch (-> br_table in wasm), which is the idiomatic fast shape
 * in C, exactly as a table-of-closures is the idiomatic fast shape in Lua. The
 * comparison is best-form vs best-form, not a handicap.
 *
 * build: clang --target=wasm32 -O2 -nostdlib -Wl,--no-entry -o core6502.wasm
 */

static unsigned char ram[2048];
static unsigned char rom[256];
static int A, X, PC, C, Z, N, V;
static int bad;
static int inited = 0;

static const unsigned char prog[] = {
    0xA2, 0x00,             /* LDX #$00      */
    0xBD, 0x00, 0x03,       /* LDA $0300,X   */
    0x69, 0x01,             /* ADC #$01      */
    0x9D, 0x00, 0x02,       /* STA $0200,X   */
    0xE8,                   /* INX           */
    0xE0, 0x00,             /* CPX #$00      */
    0xD0, 0xF3,             /* BNE $8002     */
    0x4C, 0x00, 0x80        /* JMP $8000     */
};

__attribute__((export_name("reset")))
void reset(void) {
    /* ram/rom are static -> already zero in the wasm data segment; explicit
     * zero loops here would lower to memset, which a freestanding module has
     * no libc to link against. */
    for (unsigned i = 0; i < sizeof(prog); i++) rom[i] = prog[i];
    A = 0; X = 0; PC = 0x8000;
    C = 0; Z = 1; N = 0; V = 0;
    inited = 1;
}

static int rd(int a) {
    if (a < 0x2000) return ram[a & 0x07FF];
    if (a >= 0x8000) return rom[a - 0x8000];
    return 0;
}

static void wr(int a, int v) {
    if (a < 0x2000) ram[a & 0x07FF] = (unsigned char)v;
}

/* Dispatch through a function-pointer table -> call_indirect in wasm, the
 * structural twin of the Lua core's ops[op]() table-of-closures. (It also
 * sidesteps a br_table arity check that WAMR's loader applies differently on
 * the esp-idf build than on the linux build, despite identical -D flags.) */
typedef int (*op_fn)(void);

static int op_ldx_imm(void) { int v = rd(PC); PC++; X = v; Z = (v == 0); N = (v >= 0x80); return 2; }
static int op_lda_absx(void) {
    int lo = rd(PC), hi = rd(PC + 1); PC += 2;
    int v = rd((hi * 256 + lo + X) & 0xFFFF);
    A = v; Z = (v == 0); N = (v >= 0x80); return 4;
}
static int op_adc_imm(void) {
    int v = rd(PC); PC++;
    int t = A + v + C; C = (t > 0xFF);
    int r = t & 0xFF;
    V = (((A ^ r) & (v ^ r) & 0x80) != 0);
    A = r; Z = (r == 0); N = (r >= 0x80); return 2;
}
static int op_sta_absx(void) {
    int lo = rd(PC), hi = rd(PC + 1); PC += 2;
    wr((hi * 256 + lo + X) & 0xFFFF, A); return 5;
}
static int op_inx(void) { X = (X + 1) & 0xFF; Z = (X == 0); N = (X >= 0x80); return 2; }
static int op_cpx_imm(void) {
    int v = rd(PC); PC++;
    int t = X - v; C = (t >= 0);
    int r = t & 0xFF; Z = (r == 0); N = (r >= 0x80); return 2;
}
static int op_bne(void) {
    int o = rd(PC); PC++;
    if (!Z) { if (o >= 0x80) o -= 256; PC = (PC + o) & 0xFFFF; return 3; }
    return 2;
}
static int op_jmp_abs(void) {
    int lo = rd(PC), hi = rd(PC + 1);
    PC = (hi * 256 + lo) & 0xFFFF; return 3;
}
static int op_bad(void) { bad++; return 2; }

static op_fn ops[256];

static void build_table(void) {
    for (int i = 0; i < 256; i++) ops[i] = op_bad;
    ops[0xA2] = op_ldx_imm;  ops[0xBD] = op_lda_absx;
    ops[0x69] = op_adc_imm;  ops[0x9D] = op_sta_absx;
    ops[0xE8] = op_inx;      ops[0xE0] = op_cpx_imm;
    ops[0xD0] = op_bne;      ops[0x4C] = op_jmp_abs;
}

__attribute__((export_name("step")))
int step(int n) {
    if (!inited) { reset(); build_table(); }
    int cyc = 0;
    for (int i = 0; i < n; i++) {
        int op = rd(PC);
        PC++;
        cyc += ops[op]();
    }
    return cyc;
}

/* Raw VM reference, masked so the optimiser can't close-form the sum. */
__attribute__((export_name("bad_count")))
int bad_count(int unused) { (void)unused; return bad; }

__attribute__((export_name("spin")))
int spin(int n) {
    int s = 0;
    for (int i = 1; i <= n; i++) s = (s + i) & 0xFFFF;
    return s;
}
