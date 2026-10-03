// SPDX-License-Identifier: MIT
// Copyright (c) 2026 CubeCoders Limited
// Copyright (c) 2026 Nikola Jovicic
//
// ESP 88: CubeCoders' neon city film (JetExamples' esp32-neon-film, MIT),
// rendered by Jet (github.com/CubeCoders/Jet, MIT) into the cart's frame and
// handed to the console whole through blit565. The film's own code builds and
// animates every cut; this is the cart around it: the hooks, the buttons,
// config.json, the artwork read from assets.bin, the frame and the HUD.
// ports/jet/README.md in Moybyte's tree says how it is built.
//
// The film renders the way the example's ESP32 runtime (its
// components/esp32_jet) renders it: half-width field buffers, one field a
// frame, the river reflecting the previous field, and the sprites composited
// at full width onto the rows each field sends out -- here into the frame
// rather than to a panel, so the other field's rows stay as they were, as a
// panel's do. The picture is two thirds of the film's: the 480 x 296 between
// its letterbox bars becomes 320 x 198, centred in the console's 320 x 240,
// whose black above and below are the bars.
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include <algorithm>
#include <vector>

#define FILM_RENDER_WIDTH 320
#define FILM_RENDER_HEIGHT 198
#include "Film.hpp"
#include "hud_font.h"
#include "moy_cart.h"

extern "C" void __wasm_call_ctors(void);
extern "C" size_t cart_heap_peak(void);
extern "C" size_t cart_heap_size(void);
extern "C" unsigned char __stack_low, __stack_high;
extern "C" void cart_par(int n, void (*fn)(int i, void *ctx), void *ctx);
extern "C" size_t cart_item_stack_peak(void);

#define EXPORT(name) __attribute__((export_name(name)))

namespace {

using Renderer::Sprite2D;

const int CART_W = 320, CART_H = 240;
const int FILM_W = Film::renderWidth, FILM_H = Film::renderHeight;
const int FILM_TOP = (CART_H - FILM_H) / 2;
const int FIELD_W = FILM_W / 2, FIELD_H = FILM_H / 2;
static_assert(FILM_W == CART_W && FILM_H % 2 == 0 && FILM_H <= CART_H,
              "the film fills the frame's width in whole field pairs");
static_assert(sizeof(uint16_t) == 2 && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
              "blit565 takes little-endian words; Jet stores native ones");

// The frame blit565 takes, and the two half-width fields Jet renders into.
alignas(16) uint16_t frame[CART_W * CART_H];
alignas(16) uint16_t fields[2][FIELD_W * FIELD_H];

// pmem slots a test or a measurement reads back. Per cut, over its most
// recent play of a second or more: the mean ms Jet took (render and the
// film's effects) and the frames a second, both in tenths.
enum { PM_HEAP_PEAK_KB, PM_HEAP_KB, PM_TRIS, PM_FPS10, PM_RENDER_MS10, PM_CUT, PM_STACK_KB,
       PM_ITEM_STACK };
const int PM_CUT_RENDER_MS10 = 16, PM_CUT_FPS10 = 32;

Renderer::Scene *scene = nullptr;
bool ready = false, interlaced = true, hud = false;
int cores = 2;
float clock_s = 0;
// The film, then the second of black it holds before the example restarts
// the board; the cart plays it again instead.
const float LOOP = Film::duration + 1;
int held = 0, shown_cut = -1;
std::vector<Sprite2D *> overlays;

bool cfg_is(const char *key, const char *want)
{
    char buf[16];
    int n = moy_cfg(key, (int)strlen(key), buf, sizeof buf);
    return n >= 0 && n == (int)strlen(want) && !memcmp(buf, want, n);
}

int cfg_int(const char *key, int fallback)
{
    char buf[16];
    int n = moy_cfg(key, (int)strlen(key), buf, sizeof buf);
    if (n <= 0 || n >= (int)sizeof buf) return fallback;
    int v = 0;
    for (int i = 0; i < n; i++) {
        if (buf[i] < '0' || buf[i] > '9') return fallback;
        v = v * 10 + (buf[i] - '0');
    }
    return v;
}

float cut_start(int cut)
{
    float t = 0;
    for (int i = 0; i < cut; i++) t += Film::durations[i];
    return t;
}

// -- the artwork -----------------------------------------------------------

const char ASSET_FILE[] = "assets.bin";
const int NAME_BYTES = 20, RECORD = NAME_BYTES + 8;

struct Asset {
    const char *name;
    void *dst;
    int bytes;
};
#define ASSET(a) {#a, Assets::a, (int)sizeof(Assets::a)}
const Asset ASSETS[] = {
    ASSET(facade0), ASSET(facade1), ASSET(facade2), ASSET(facade3),
    ASSET(shop0), ASSET(shop1), ASSET(shop2), ASSET(shop3),
    ASSET(sign0), ASSET(sign1), ASSET(sign2), ASSET(sign3), ASSET(sign4), ASSET(sign5),
    ASSET(environment), ASSET(hologram), ASSET(dashboard),
    ASSET(facade0Palette), ASSET(facade1Palette), ASSET(facade2Palette),
    ASSET(facade3Palette), ASSET(shop0Palette), ASSET(shop1Palette), ASSET(shop2Palette),
    ASSET(shop3Palette), ASSET(sign0Palette), ASSET(sign1Palette), ASSET(sign2Palette),
    ASSET(sign3Palette), ASSET(sign4Palette), ASSET(sign5Palette),
    ASSET(environmentPalette), ASSET(hologramPalette), ASSET(dashboardPalette),
    ASSET(glow),
};

// The closing credits' coverage, read when the last cut loads, at the size
// CreditMask.hpp declares.
const int CREDITS_W = FILM_CREDITS_W, CREDITS_H = FILM_CREDITS_H;
// Where the film puts them, (60, 68) of its 480 x 320, in this picture.
const int CREDITS_X = 40, CREDITS_Y = 37;
int credits_at = -1;

int read_assets(int offset, void *dst, int len)
{
    return moy_read(ASSET_FILE, sizeof ASSET_FILE - 1, offset, dst, len);
}

uint32_t le32(const uint8_t *p)
{
    return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
}

// Every array Assets.hpp declares, from its record in assets.bin: a table of
// NUL-padded names with each record's offset and length, then the bytes.
bool load_assets()
{
    uint8_t head[12];
    if (read_assets(0, head, sizeof head) != (int)sizeof head || memcmp(head, "MOYJETA1", 8))
        return false;
    const int n = (int)le32(head + 8);
    if (n <= 0 || n > 256) return false;
    uint8_t *table = static_cast<uint8_t *>(malloc(n * RECORD));
    bool ok = table && read_assets(sizeof head, table, n * RECORD) == n * RECORD;
    for (const Asset &a : ASSETS) {
        bool found = false;
        for (int i = 0; ok && !found && i < n; i++) {
            const uint8_t *rec = table + i * RECORD;
            if (strncmp(reinterpret_cast<const char *>(rec), a.name, NAME_BYTES)) continue;
            const int at = (int)le32(rec + NAME_BYTES), len = (int)le32(rec + NAME_BYTES + 4);
            found = len <= a.bytes && read_assets(at, a.dst, len) == len;
        }
        ok = ok && found;
    }
    for (int i = 0; ok && i < n; i++) {
        const uint8_t *rec = table + i * RECORD;
        if (!strncmp(reinterpret_cast<const char *>(rec), "credits", NAME_BYTES)
            && le32(rec + NAME_BYTES + 4) == (uint32_t)(CREDITS_W * CREDITS_H))
            credits_at = (int)le32(rec + NAME_BYTES);
    }
    free(table);
    return ok && credits_at >= 0;
}

// The film sizes its credit texture at 360 x 168 and expands its mask into
// it when the last cut loads; the cart sizes it at this picture's credits
// (_init), so the film's pass leaves a blank texture of that size, which this
// fills: white at the coverage the file gives each pixel, 0 staying the
// texture's transparent key.
void adapt_credits()
{
    if (!Film::creditSprite || Film::creditPixels.size() != size_t(CREDITS_W * CREDITS_H))
        return;
    uint8_t *cover = static_cast<uint8_t *>(malloc(CREDITS_W * CREDITS_H));
    if (cover && read_assets(credits_at, cover, CREDITS_W * CREDITS_H) == CREDITS_W * CREDITS_H) {
        for (int i = 0; i < CREDITS_W * CREDITS_H; i++) {
            const int g = cover[i];
            Film::creditPixels[i] = uint16_t((g >> 3) << 11 | (g >> 2) << 5 | (g >> 3));
        }
    }
    free(cover);
    Film::creditSprite->x = CREDITS_X;
    Film::creditSprite->y = CREDITS_Y;
}

// -- the frame ---------------------------------------------------------------

void on_cut();

// The film at `clock_s`.
void play()
{
    Film::seek(clock_s);
    if (Film::shot != shown_cut) on_cut();
}

// On two cores the raster runs as Jet's own ESP32-S3 runtime runs it: the
// frame's setup on one core, then the field's rows in two bands at once, each
// into its own rows and its own triangle flags, merged after; a band reads
// the other field for its reflections, never the rows the other band draws.
// The scan-out's rows are shared the same way.
struct Bands {
    Renderer::Scene *scene;
    std::vector<uint8_t> flags[2];
};
Bands bands;
const int SPLIT = (FILM_H / 2) & ~1;

void band(int i, void *ctx)
{
    Bands *b = static_cast<Bands *>(ctx);
    b->scene->rasterizeBand(i ? SPLIT : 0, i ? FILM_H : SPLIT, b->flags[i].data());
}

void in_bands(Renderer::Scene &s)
{
    const int tris = s.lastFrameDrawnTriangles;
    bands.scene = &s;
    bands.flags[0].assign(tris ? tris : 1, 0);
    bands.flags[1].assign(tris ? tris : 1, 0);
    cart_par(2, band, &bands);
    int count = 0;
    for (int t = 0; t < tris; ++t) count += (bands.flags[0][t] | bands.flags[1][t]) != 0;
    s.lastFrameRasterizedTriangles = count;
}

// One field into its buffer, reflecting the other, then the film's rain and
// spray over it. The rows it drew: Jet draws the odd rows of an even frame.
int render_field()
{
    const int parity = scene->frameCounter % 2 == 0 ? 1 : 0;
    scene->setFramebuffer(fields[parity]);
    scene->getRenderer()->reflectBuffer = fields[parity ^ 1];
    if (cores == 2) scene->render(in_bands);
    else scene->render();
    scene->lastFrameRasterizedTriangles += Film::effects(*scene);
    return parity;
}

// The rows [y0, y1) of `parity` (both with -1) into the frame: each stored
// pixel twice, then the sprites over the row in their z order.
void scan_rows(int y0, int y1, int parity)
{
    const int step = parity < 0 ? 1 : 2;
    int y = y0;
    if (parity >= 0 && (y & 1) != parity) ++y;
    for (; y < y1; y += step) {
        const uint32_t *src = reinterpret_cast<const uint32_t *>(fields[y & 1] + (y >> 1) * FIELD_W);
        uint16_t *row = frame + (FILM_TOP + y) * CART_W;
        uint32_t *dst = reinterpret_cast<uint32_t *>(row);
        for (int i = 0; i < FIELD_W / 2; ++i) {
            const uint32_t two = src[i];
            dst[2 * i] = (two & 0xFFFFu) * 0x10001u;
            dst[2 * i + 1] = (two >> 16) * 0x10001u;
        }
        Renderer::compositeSprites(row, CART_W, y, overlays.data(), (int)overlays.size());
    }
}

int scan_parity;

void scan_item(int i, void *)
{
    scan_rows(i ? SPLIT : 0, i ? FILM_H : SPLIT, scan_parity);
}

void scan_out(int parity)
{
    const auto &sprites = scene->getSprites();
    overlays.assign(sprites.begin(), sprites.end());
    std::stable_sort(overlays.begin(), overlays.end(),
                     [](const Sprite2D *a, const Sprite2D *b) { return a->zOrder < b->zOrder; });
    if (cores == 2) {
        scan_parity = parity;
        cart_par(2, scan_item, nullptr);
    } else {
        scan_rows(0, FILM_H, parity);
    }
}

// -- the HUD -----------------------------------------------------------------

char *put(char *at, const char *s)
{
    while (*s) *at++ = *s++;
    return at;
}

char *put_int(char *at, int v)
{
    char digits[12];
    int n = 0;
    if (v < 0) {
        *at++ = '-';
        v = -v;
    }
    do {
        digits[n++] = char('0' + v % 10);
        v /= 10;
    } while (v);
    while (n) *at++ = digits[--n];
    return at;
}

char *put_tenths(char *at, int v10)
{
    at = put_int(at, v10 / 10);
    *at++ = '.';
    return put_int(at, v10 % 10);
}

const int HUD_H = 10;
// The strip and its ink as RGB565: SPEC.md 2.2's palette entries 0 and 7.
const uint16_t HUD_BG_565 = 0x0000;
const uint16_t HUD_INK_565 = 0xFF9D;

// `s` in the console's font into the frame at (x, y), the pixels print draws.
void frame_print(const char *s, int len, int x, int y)
{
    for (int k = 0; k < len; k++, x += 8) {
        int code = (unsigned char)s[k];
        if (code < 0x20 || code > 0x7F) continue;
        const uint8_t *g = HUD_FONT + (code - 0x20) * 8;
        for (int j = 0; j < 8; j++) {
            int px = x + j;
            if (px < 0 || px >= CART_W) continue;
            for (int b = 0; b < 8; b++) {
                int py = y + b;
                if (((g[j] >> b) & 1) && py >= 0 && py < CART_H)
                    frame[py * CART_W + px] = HUD_INK_565;
            }
        }
    }
}

int tris = 0, fps10 = 0, render_ms10 = 0;
int window_start = -1, window_frames = 0, window_render_ms = 0;

// The strip over the letterbox's top rows: the cut, the whole-frame rate and
// the mean time Jet took, each over the last second.
void draw_hud()
{
    char line[48];
    const int cut = Film::shot < 0 ? 0 : Film::shot;
    char *at = line;
    if (cut < 9) *at++ = '0';
    at = put_int(at, cut + 1);
    *at++ = ' ';
    at = put(at, Film::names[cut]);
    at = put(at, "  ");
    at = put_tenths(at, fps10);
    at = put(at, "FPS ");
    at = put_tenths(at, render_ms10);
    at = put(at, "MS");
    for (int i = 0; i < CART_W * HUD_H; i++) frame[i] = HUD_BG_565;
    frame_print(line, (int)(at - line), 2, 1);
}

void draw_failure()
{
    static const char msg[] = "assets.bin did not load";
    moy_rect(0, 0, CART_W, CART_H, 1);
    moy_print(msg, sizeof msg - 1, 8, 112, 7);
}

// -- measuring ---------------------------------------------------------------

// Painted below the stack when the cart starts; the paint left is the stack
// it never reached.
const uint8_t STACK_PAINT = 0xA5;

void paint_stack()
{
    unsigned char here;
    unsigned char *low = &__stack_low, *top = &here - 256;
    if (top > low) memset(low, STACK_PAINT, top - low);
}

int stack_used_kb()
{
    const unsigned char *p = &__stack_low;
    while (p < &__stack_high && *p == STACK_PAINT) ++p;
    return (int)((&__stack_high - p + 1023) / 1024);
}

void report_memory()
{
    moy_pmem(PM_HEAP_PEAK_KB, (int)((cart_heap_peak() + 1023) / 1024), 1);
    moy_pmem(PM_HEAP_KB, (int)(cart_heap_size() / 1024), 1);
    moy_pmem(PM_STACK_KB, stack_used_kb(), 1);
    moy_pmem(PM_ITEM_STACK, (int)cart_item_stack_peak(), 1);
}

// One cut's play: its frames and Jet's time over them, from its first drawn
// frame (after the cut has loaded) to its last.
struct CutMeter {
    int cut = -1, frames = 0, first = 0, last = 0, render_ms = 0;

    void finish()
    {
        if (cut >= 0 && frames >= 2 && last - first >= 1000) {
            moy_pmem(PM_CUT_FPS10 + cut, (frames - 1) * 10000 / (last - first), 1);
            moy_pmem(PM_CUT_RENDER_MS10 + cut, render_ms * 10 / frames, 1);
        }
        frames = render_ms = 0;
    }
} meter;

void measure(int render_ms)
{
    const int now = moy_time();
    if (clock_s < Film::duration) {
        if (!meter.frames) meter.first = now;
        meter.last = now;
        ++meter.frames;
        meter.render_ms += render_ms;
    }
    if (window_start < 0) window_start = now;
    ++window_frames;
    window_render_ms += render_ms;
    const int span = now - window_start;
    if (span >= 1000) {
        fps10 = window_frames * 10000 / span;
        render_ms10 = window_render_ms * 10 / window_frames;
        moy_pmem(PM_TRIS, tris, 1);
        moy_pmem(PM_FPS10, fps10, 1);
        moy_pmem(PM_RENDER_MS10, render_ms10, 1);
        report_memory();
        window_start = now;
        window_frames = window_render_ms = 0;
    }
}

// A new cut has loaded: close the last one's meter, fit the credits to this
// picture when it is the last, and report the memory the load took.
void on_cut()
{
    meter.finish();
    meter.cut = shown_cut = Film::shot;
    moy_pmem(PM_CUT, shown_cut + 1, 1);
    if (shown_cut == Film::cutCount - 1) adapt_credits();
    report_memory();
}

void jump(int by)
{
    const int cut = (Film::shot + by + Film::cutCount) % Film::cutCount;
    clock_s = cut_start(cut);
}

}  // namespace

EXPORT("_init") void cart_init(void)
{
    paint_stack();
    const bool assets = load_assets();
    __wasm_call_ctors();
    interlaced = !cfg_is("interlaced", "0");
    hud = cfg_is("hud", "1");
    cores = cfg_int("cores", 2) > 1 ? 2 : 1;
    if (assets) {
        Film::creditTexture.width = CREDITS_W;
        Film::creditTexture.height = CREDITS_H;
        scene = new Renderer::Scene(fields[0], nullptr, FILM_W, FILM_H);
        scene->getRenderer()->interlacedMode = true;
        Film::init(*scene);
        // The console's letterbox is the film's bars.
        Film::topBar.enabled = Film::bottomBar.enabled = false;
        on_cut();
        clock_s = cut_start(std::clamp(cfg_int("cut", 1), 1, Film::cutCount) - 1);
        play();
        ready = true;
    }
    report_memory();
}

// left / right: the cut before or after; A: the HUD.
EXPORT("_update") void cart_update(float dt)
{
    if (!ready) return;
    int now = 0;
    for (int b = 0; b < 6; b++)
        if (moy_btn(b, 0)) now |= 1 << b;
    const int pressed = now & ~held;
    held = now;
    if (pressed & 1) jump(-1);
    if (pressed & 2) jump(1);
    if (pressed & 16) {
        hud = !hud;
        if (!hud)
            for (int i = 0; i < CART_W * HUD_H; i++) frame[i] = HUD_BG_565;
    }
    if (!(pressed & 3)) clock_s += dt;
    while (clock_s >= LOOP) clock_s -= LOOP;
    play();
}

EXPORT("_draw") void cart_draw(void)
{
    if (!ready) {
        draw_failure();
        return;
    }
    const int start = moy_time();
    int parity = render_field();
    if (!interlaced) {
        const int first = scene->lastFrameRasterizedTriangles;
        render_field();
        scene->lastFrameRasterizedTriangles += first;
        parity = -1;
    }
    const int render_ms = moy_time() - start;
    tris = scene->lastFrameRasterizedTriangles;
    scan_out(parity);
    measure(render_ms);
    if (hud) draw_hud();
    moy_blit565(frame);
}
