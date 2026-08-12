/* Stage-4 spike: price the vendored libmoy kernels compiled to wasm as the
 * shell raster at desktop size (1024x600) -- option (b).
 *
 * Mirrors bench_canvas.py OP-FOR-OP (same geometry, same strings, same icon
 * pattern) so the Python and C numbers price the same frame. Indexed pixel
 * format (the default build -- the realistic shell-raster shape); build.sh
 * compiles the vendored libmoy sources verbatim.
 *
 * Additions over the Python bench (C-option-only costs):
 *   - present_rgba: indexed 1024x600 -> RGBA8888 via a 64-entry LUT, the
 *     hand-rolled loop a page canvas blit (putImageData) needs.
 *   - drag restore is memcpy (libmoy has no 1:1 layer-blit verb; memcpy IS
 *     the C idiom for it, stated in the README).
 *
 * 16x16 sprites: libmoy's spr() draws one 8x8 tile, so the 16x16 shell icon
 * is moy_sspr of a 16x16 sheet region at 1:1 -- the per-pixel colorkey path,
 * same skip pattern as the Python Image (transparent where (x*7+y*3)%5==0).
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>

#include "moy.h"

#ifdef __EMSCRIPTEN__
#include <emscripten.h>
static double now_ms(void) { return emscripten_get_now(); }
#else
#include <time.h>
static double now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}
#endif

#define BW 1024
#define BH 600
#define FRAMES 30
#define WARMUP 3

static moy_pixel fb[BW * BH];
static moy_pixel bd[BW * BH];        /* backdrop layer for the drag restore */
static uint8_t sheetpix[MOY_SHEET_W * MOY_SHEET_H];
static uint32_t rgba[BW * BH];       /* present target */

static moy_canvas cv;
static moy_canvas bdcv;
static moy_sheet sheet;

static const char TITLE16[] = "WINDOW TITLE 001";
static const char TEXT34[] = "the quick brown fox jumps over 034";
static const char LINE40[] = "for i in range(40): draw(i, x, y) #c    ";
static char S100[101];               /* built in main: (base * 3)[:100] */

/* -- fixtures ------------------------------------------------------------ */

static void make_icon(void)
{
    /* 16x16 region at sheet (0,0): 0 (the colorkey) where the Python Image is
     * transparent, else the same (x+y)%15+1 value. */
    int x, y;
    for (y = 0; y < 16; y++)
        for (x = 0; x < 16; x++)
            sheetpix[y * MOY_SHEET_W + x] =
                ((x * 7 + y * 3) % 5 == 0) ? 0 : (uint8_t)((x + y) % 15 + 1);
}

static void draw_icon(moy_canvas *c, int x, int y)
{
    moy_sspr(c, &sheet, 0, 0, 16, 16, x, y, 16, 16, /*colorkey*/0, MOY_FLIP_NONE);
}

/* -- workloads (mirror bench_canvas.py) ----------------------------------- */

static void desk_frame(moy_canvas *c)
{
    int i;
    moy_cls(c, 1);
    for (i = 0; i < 6; i++) {
        int x = (i % 3) * 330 + 8;
        int y = (i / 3) * 260 + 20;
        moy_rect(c, x, y, 420, 320, 20 + i);
        moy_rectb(c, x, y, 420, 320, 15);
        moy_rect(c, x, y, 420, 18, 8);
        moy_print(c, (const uint8_t *)TITLE16, strlen(TITLE16), x + 4, y + 5, 63);
    }
    for (i = 0; i < 6; i++)
        moy_print(c, (const uint8_t *)TEXT34, strlen(TEXT34), 16, 380 + i * 12, 60);
    for (i = 0; i < 40; i++)
        draw_icon(c, (i * 97) % (BW - 16), (i * 53) % (BH - 16));
}

static void editor_frame(moy_canvas *c)
{
    int i;
    moy_rect(c, 0, 0, BW, BH, 2);
    for (i = 0; i < 40; i++)
        moy_print(c, (const uint8_t *)LINE40, strlen(LINE40), 8, 20 + i * 14, 62);
    for (i = 0; i < 58; i++)
        moy_rect(c, 4, 18 + i * 10, BW - 24, 1, 3);
    moy_rect(c, BW - 14, 20, 10, BH - 40, 5);
    moy_rect(c, 200, 188, 2, 12, 63);
}

static void drag_frame(moy_canvas *c)
{
    memcpy(fb, bd, sizeof(fb));   /* full-screen 1:1 backdrop restore */
    moy_rect(c, 300, 140, 420, 320, 22);
    moy_rectb(c, 300, 140, 420, 320, 15);
    moy_rect(c, 300, 140, 420, 18, 8);
    moy_print(c, (const uint8_t *)TITLE16, strlen(TITLE16), 304, 145, 63);
}

static uint32_t lut[64];

static void present_rgba(void)
{
    /* indexed -> RGBA8888, the loop a page canvas putImageData needs. */
    int i;
    const moy_pixel *src = fb;
    uint32_t *dst = rgba;
    for (i = 0; i < BW * BH; i++)
        dst[i] = lut[src[i] & 63];
}

/* -- harness -------------------------------------------------------------- */

static int cmp_d(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static double median_of(double *v, int n)
{
    qsort(v, n, sizeof(double), cmp_d);
    return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

static double p90_of(double *v, int n)   /* v already sorted by median_of */
{
    return v[(int)(0.9 * (n - 1))];
}

static void run_frames(const char *name, void (*fn)(moy_canvas *), moy_canvas *c)
{
    double t[FRAMES];
    int i;
    for (i = 0; i < WARMUP; i++) fn(c);
    for (i = 0; i < FRAMES; i++) {
        double t0 = now_ms();
        fn(c);
        t[i] = now_ms() - t0;
    }
    double med = median_of(t, FRAMES);
    printf("RESULT frame %s median_ms=%.3f p90_ms=%.3f n=%d\n",
           name, med, p90_of(t, FRAMES), FRAMES);
}

#define MICRO(name, stmt, iters) do {                                        \
    double batch[7]; int b, i;                                               \
    { stmt; }                                                                \
    for (b = 0; b < 7; b++) {                                                \
        double t0 = now_ms();                                                \
        for (i = 0; i < (iters); i++) { stmt; }                              \
        batch[b] = (now_ms() - t0) * 1000.0 / (iters);                       \
    }                                                                        \
    printf("RESULT micro %s us_per_op=%.2f iters=%d\n",                      \
           name, median_of(batch, 7), (iters));                              \
} while (0)

static void present_frame(moy_canvas *c) { (void)c; present_rgba(); }

int main(void)
{
    int i;
    printf("BENCH platform=libmoy-wasm pixel=%d-byte size=%dx%d\n",
           (int)MOY_PIXEL_BYTES, BW, BH);

    {   /* S100 = ("print one hundred glyphs of shell text " * 3)[:100] */
        const char *base = "print one hundred glyphs of shell text ";
        for (i = 0; i < 100; i++) S100[i] = base[i % 39];
        S100[100] = 0;
    }

    make_icon();
    moy_sheet_init(&sheet, sheetpix);
    moy_canvas_init(&cv, fb, BW, BH);
    moy_canvas_init(&bdcv, bd, BW, BH);
    for (i = 0; i < 64; i++) {
        const uint8_t *e = moy_palette_default + i * 3;
        lut[i] = 0xFF000000u | ((uint32_t)e[2] << 16) | ((uint32_t)e[1] << 8) | e[0];
    }

    desk_frame(&bdcv);               /* paint the backdrop once, like the WM */

    run_frames("desk", desk_frame, &cv);
    run_frames("editor", editor_frame, &cv);
    run_frames("drag", drag_frame, &cv);
    run_frames("present_rgba", present_frame, &cv);

    MICRO("cls", moy_cls(&cv, 5), 2000);
    MICRO("rect_420x320", moy_rect(&cv, 30, 40, 420, 320, 9), 5000);
    MICRO("rectb_420x320", moy_rectb(&cv, 30, 40, 420, 320, 9), 20000);
    MICRO("print_100gl",
          moy_print(&cv, (const uint8_t *)S100, strlen(S100), 4, 300, 61), 5000);
    MICRO("spr_16x16", draw_icon(&cv, 500, 300), 50000);
    MICRO("line_200px", moy_line(&cv, 100, 100, 300, 240, 7), 50000);

    {
        long chk = 0;
        for (i = 0; i < BW * BH; i += 80011) chk += fb[i];
        long chk2 = 0;
        for (i = 0; i < BW * BH; i += 80011) chk2 += (long)(rgba[i] & 0xFF);
        printf("CHECK buf[0]=%d buf[-1]=%d sum8=%ld rgba8=%ld\n",
               (int)fb[0], (int)fb[BW * BH - 1], chk, chk2);
    }
    return 0;
}
