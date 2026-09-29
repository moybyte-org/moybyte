// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Nikola Jovicic
//
// Jet Teapot: a 3D scene rendered by Jet (github.com/CubeCoders/Jet, MIT), a
// software rasteriser that writes RGB565, with a HUD drawn into the same
// frame, handed to the console whole through blit565. scene.cpp is the scene;
// this is the cart: the hooks, the buttons, config.json, the buffers and the
// HUD. ports/jet/README.md in Moybyte's tree says how it is built.
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "hud_font.h"
#include "moy.h"
#include "scene.h"

extern "C" void __wasm_call_ctors(void);
extern "C" size_t cart_heap_peak(void);
extern "C" size_t cart_heap_size(void);

#define EXPORT(name) __attribute__((export_name(name)))

namespace {

// The frame blit565 takes, then -- in half width only -- Jet's half-width
// colour buffer, then the depth buffer: 307,200 bytes in either mode.
const int FRAME_WORDS = CART_W * CART_H;
alignas(16) uint16_t arena[2 * FRAME_WORDS];

static_assert(sizeof(uint16_t) == 2 && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
              "blit565 takes little-endian words; Jet stores native ones");

// pmem slots a test or a measurement reads back.
enum { PM_HEAP_PEAK_KB, PM_HEAP_KB, PM_TRIS, PM_FPS10, PM_RENDER_MS10, PM_ITEM_STACK };

const JetScene *jet = nullptr;
bool ready = false, half = false, interlaced = false, hud = true;
CartInput input;

int tris = 0, fps10 = 0, render_ms10 = 0;
int window_start = -1, window_frames = 0, window_render_ms = 0;

bool cfg_is(const char *key, const char *want)
{
    char buf[16];
    int n = moy_cfg(key, (int)strlen(key), buf, sizeof buf);
    return n >= 0 && n == (int)strlen(want) && !memcmp(buf, want, n);
}

int cfg_int(const char *key, int dflt)
{
    char buf[16];
    int n = moy_cfg(key, (int)strlen(key), buf, sizeof buf - 1);
    if (n <= 0 || n >= (int)sizeof buf) return dflt;
    int v = 0;
    for (int i = 0; i < n; i++) {
        if (buf[i] < '0' || buf[i] > '9') return dflt;
        v = v * 10 + (buf[i] - '0');
    }
    return v;
}

int shading_from_cfg()
{
    if (cfg_is("shading", "flat")) return SHADE_FLAT;
    if (cfg_is("shading", "gouraud")) return SHADE_GOURAUD;
    if (cfg_is("shading", "phong")) return SHADE_PHONG;
    return SHADE_CYCLE;
}

char *load_file(const char *name)
{
    int n = moy_read(name, (int)strlen(name), 0, nullptr, 0);
    if (n <= 0) return nullptr;
    char *text = static_cast<char *>(malloc(n + 1));
    if (!text) return nullptr;
    if (moy_read(name, (int)strlen(name), 0, text, n) != n) {
        free(text);
        return nullptr;
    }
    text[n] = 0;
    return text;
}

void report_heap()
{
    moy_pmem(PM_HEAP_PEAK_KB, (int)((cart_heap_peak() + 1023) / 1024), 1);
    moy_pmem(PM_HEAP_KB, (int)(cart_heap_size() / 1024), 1);
}

// -- the HUD --------------------------------------------------------------

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

const int HUD_INK = 7;
const int HUD_H = 10;

// The strip and its ink as RGB565: SPEC.md 2.2's palette entries 0 and 7.
const uint16_t HUD_BG_565 = 0x0000;
const uint16_t HUD_INK_565 = 0xFF9D;

// `s` in the console's font into the frame at (x, y), the pixels print draws:
// one 8px cell a byte, a byte outside 0x20-0x7F a blank cell, only the
// glyph's set bits written.
void frame_print(uint16_t *frame, const char *s, int len, int x, int y)
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

// The strip and its line, into the frame before it is handed over: the same
// pixels a rect and a print over the blit would draw, and the whole frame
// stays the cart's, which is what lets a console show it straight from here.
void draw_hud(uint16_t *frame)
{
    char line[48];
    char *at = put(line, half ? "HALF" : "FULL");
    if (interlaced) at = put(at, "/I");
    *at++ = ' ';
    at = put(at, jet->shading());
    at = put(at, "  ");
    at = put_tenths(at, fps10);
    at = put(at, "FPS ");
    at = put_tenths(at, render_ms10);
    at = put(at, "MS ");
    at = put_int(at, tris);
    *at++ = 'T';
    for (int i = 0; i < CART_W * HUD_H; i++) frame[i] = HUD_BG_565;
    frame_print(frame, line, (int)(at - line), 2, 1);
}

void draw_failure()
{
    static const char msg[] = "teapot.obj did not load";
    moy_rect(0, 0, CART_W, CART_H, 1);
    moy_print(msg, sizeof msg - 1, 8, 112, HUD_INK);
}

// One reading a second's worth of frames at a time: whole-frame rate and the
// mean time render() took, both in tenths.
void measure(int render_ms)
{
    const int now = moy_time();
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
        moy_pmem(PM_ITEM_STACK, (int)cart_item_stack_peak(), 1);
        report_heap();
        window_start = now;
        window_frames = window_render_ms = 0;
    }
}

}  // namespace

EXPORT("_init") void cart_init(void)
{
    __wasm_call_ctors();
    half = cfg_is("width", "half");
    interlaced = cfg_is("interlaced", "1");
    hud = !cfg_is("hud", "0");
    jet = half ? &jet_half : &jet_full;
    uint16_t *frame = arena;
    uint16_t *color = jet->color_words ? arena + FRAME_WORDS : nullptr;
    uint16_t *depth = arena + FRAME_WORDS + jet->color_words;
    char *obj = load_file("teapot.obj");
    ready = obj && jet->open(frame, color, depth, obj, shading_from_cfg(), interlaced,
                             cfg_int("cores", 2));
    free(obj);
    report_heap();
}

EXPORT("_update") void cart_update(float dt)
{
    input.left = moy_btn(0, 0);
    input.right = moy_btn(1, 0);
    input.up = moy_btn(2, 0);
    input.down = moy_btn(3, 0);
    input.a = moy_btn(4, 0);
    input.b = moy_btn(5, 0);
    if (ready) jet->update(dt, &input);
}

EXPORT("_draw") void cart_draw(void)
{
    if (!ready) {
        draw_failure();
        return;
    }
    const int start = moy_time();
    tris = jet->render();
    measure(moy_time() - start);
    if (hud) draw_hud(arena);
    moy_blit565(arena);
}
