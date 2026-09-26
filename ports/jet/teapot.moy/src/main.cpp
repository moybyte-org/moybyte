// Jet Teapot: a 3D scene rendered by Jet (github.com/CubeCoders/Jet, MIT), a
// software rasteriser that writes RGB565, handed to the console whole through
// blit565 with a HUD drawn over it by the ordinary verbs. scene.cpp is the
// scene; this is the cart: the hooks, the buttons, config.json, the buffers
// and the HUD. ports/jet/README.md in Moybyte's tree says how it is built.
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

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
enum { PM_HEAP_PEAK_KB, PM_HEAP_KB, PM_TRIS, PM_FPS10, PM_RENDER_MS10 };

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

const int HUD_BG = 0;
const int HUD_INK = 7;
const int HUD_H = 10;

void draw_hud()
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
    moy_rect(0, 0, CART_W, HUD_H, HUD_BG);
    moy_print(line, (int)(at - line), 2, 1, HUD_INK);
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
    ready = obj && jet->open(frame, color, depth, obj, shading_from_cfg(), interlaced);
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
    moy_blit565(arena);
    if (hud) draw_hud();
}
