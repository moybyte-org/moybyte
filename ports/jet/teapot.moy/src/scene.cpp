// SPDX-License-Identifier: MIT
// Copyright (c) 2026 CubeCoders Limited
// Copyright (c) 2026 Nikola Jovicic
//
// The Utah teapot under a flying camera: JetExamples' esp32-lighting-teapot
// scene (main/Teapot.hpp, MIT, CubeCoders Limited) on the console. The light,
// the glaze, the gradient, the rocking and the Flat / Gouraud / Phong cycle
// are the example's; the camera is the player's (main.cpp reads the buttons),
// the mesh is read from the cart's teapot.obj through Jet's own OBJ loader,
// and the frame goes to the console through blit565.
//
// This file is compiled once per Jet build. JET_SCENE_TABLE names the table it
// defines, and in the half-width build Jet's namespaces are renamed on the
// command line, so both builds link into one module.
#include <cmath>
#include <cstring>
#include <vector>

#include "ObjLoader.h"
#include "Scene.hpp"
#include "scene.h"

namespace {

using namespace Renderer;

Scene *scene = nullptr;
Camera camera;
DirectionalLight key({235, 35, 0}, {255, 244, 224}, 255);
AmbientLight ambient({30, 40, 56});
Material glaze(0xD8C3, nullptr, nullptr, false, 255, 210, 255);
Object *mesh = nullptr;
uint16_t background[CART_H];
uint16_t *frame = nullptr, *color = nullptr, *depth_buf = nullptr;
float elapsed = 0;
int fixed_mode = SHADE_CYCLE;
int active = -1;
int cores = 2;

const ShadingMode MODES[] = {ShadingMode::FLAT, ShadingMode::GOURAUD, ShadingMode::PHONG};
const char *const NAMES[] = {"FLAT", "GOURAUD", "PHONG"};

void select_mode(int mode)
{
    if (active == mode) return;
    active = mode;
    glaze.shadingMode = MODES[mode];
}

// World units a second, and degrees a second.
const float MOVE = 600.0f;
const float CLIMB = 400.0f;
const float TURN = 90.0f;

bool open(uint16_t *frame_, uint16_t *color_, uint16_t *depth, const char *obj,
          int shading, bool interlaced, int cores_)
{
    frame = frame_;
    color = color_ ? color_ : frame_;
    depth_buf = depth;
    cores = cores_ > 1 ? 2 : 1;
    scene = new Scene(color, depth, CART_W, CART_H);
    scene->getRenderer()->interlacedMode = interlaced;
    camera.setPosition(0, 0, -1250);
    camera.setFOV(60.0f, CART_W);
    camera.nearPlane = 32;
    camera.farPlane = 3000;
    scene->setCamera(&camera);
    scene->setDirectionalLight(&key);
    scene->setAmbientLight(&ambient);
    // On two cores each band clears its own rows (clear_rows).
    scene->setClearBuffer(cores == 1);
    for (int y = 0; y < CART_H; ++y) {
        const int r = 12 + y * 24 / CART_H;
        const int g = 28 + y * 56 / CART_H;
        const int b = 54 + y * 72 / CART_H;
        background[y] = uint16_t((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3));
    }
    scene->backgroundGradientColors = background;
    glaze.shadingMode = ShadingMode::PHONG;
    glaze.specularExponent = 32;
    mesh = Loader::LoadFromObjData(obj, &glaze);
    if (!mesh || mesh->triangles.empty()) return false;
    mesh->cachePositions();
    mesh->setRotation(-14, 25, -4);
    scene->addObject(mesh);
    fixed_mode = shading;
    select_mode(shading == SHADE_CYCLE ? 0 : shading - SHADE_FLAT);
    return true;
}

void update(float dt, const CartInput *in)
{
    elapsed = std::fmod(elapsed + dt, 3600.0f);
    if (fixed_mode == SHADE_CYCLE) select_mode(int(elapsed / 3.0f) % 3);
    const float pitch = -14.0f + 20.0f * std::sin(elapsed * 1.8f);
    const float roll = 10.0f * std::sin(elapsed * 1.4f);
    mesh->setRotation(int(pitch), int(std::fmod(25.0f + elapsed * 25.0f, 360.0f)), int(roll));

    const float turn = float(in->right - in->left) * TURN * dt;
    if (turn != 0.0f) camera.rotate(0.0f, turn, 0.0f);
    const int forward = int(std::lround(float(in->up - in->down) * MOVE * dt));
    const int climb = int(std::lround(float(in->a - in->b) * CLIMB * dt));
    if (forward || climb) camera.translateLocalY(0, climb, forward);
}

// Jet's half-width buffer holds one word per two output columns; the console
// takes a whole 320-wide frame, so each word is written twice. Rows [y0, y1),
// every `step`-th from one of the parity `odd` gives: an interlaced frame
// renders one field, and the other field's rows in the frame are already the
// last frame's. At full width Jet renders into the frame itself.
void widen(int y0, int y1, int step, bool odd)
{
#if HALF_WIDTH_BUFFERS
    int y = y0 + ((step == 2 && (y0 & 1) != int(odd)) ? 1 : 0);
    for (; y < y1; y += step) {
        const uint32_t *src = reinterpret_cast<const uint32_t *>(color + y * (CART_W / 2));
        uint32_t *dst = reinterpret_cast<uint32_t *>(frame + y * CART_W);
        for (int i = 0; i < CART_W / 4; ++i) {
            const uint32_t two = src[i];
            dst[2 * i] = (two & 0xFFFFu) * 0x10001u;
            dst[2 * i + 1] = (two >> 16) * 0x10001u;
        }
    }
#else
    (void)y0, (void)y1, (void)step, (void)odd;
#endif
}

// What Jet's clearBuffers() does, over rows [y0, y1) alone: the gradient into
// the colour rows and the far plane into the depth rows, the rendered field's
// rows only when `step` is 2.
void clear_rows(int y0, int y1, int step, bool odd)
{
    const int words = HALF_WIDTH_BUFFERS ? CART_W / 2 : CART_W;
    const int zwords = ZBUFFER_STRIDE(CART_W);
    int y = y0 + ((step == 2 && (y0 & 1) != int(odd)) ? 1 : 0);
    for (; y < y1; y += step) {
        const uint32_t c = background[y];
        uint32_t *row = reinterpret_cast<uint32_t *>(color + y * words);
        for (int i = 0; i < words / 2; ++i) row[i] = c << 16 | c;
        memset(depth_buf + y * zwords, 0xFF, zwords * sizeof(uint16_t));
    }
}

// The raster across the console's cores, as Jet's own ESP32-S3 runtime splits
// it: the frame's setup -- the transform, the culling, the sort -- stays on
// one core, and the frame's rows are cut into bands that clear, rasterize and
// widen at once, each into its own rows of the colour, depth and output
// buffers and its own triangle flags, merged after. An interlaced frame, half
// the rows, takes two bands; a whole frame four, so a core the console keeps
// busier takes fewer.
const int MAX_BANDS = 4;

struct Bands {
    Scene *scene;
    int n, step;
    bool odd;
    int edge[MAX_BANDS + 1];
    std::vector<uint8_t> flags[MAX_BANDS];
};
Bands bands;

void band(int i, void *ctx)
{
    Bands *b = static_cast<Bands *>(ctx);
    const int y0 = b->edge[i], y1 = b->edge[i + 1];
    clear_rows(y0, y1, b->step, b->odd);
    b->scene->rasterizeBand(y0, y1, b->flags[i].data());
    widen(y0, y1, b->step, b->odd);
}

void in_bands(Scene &s)
{
    const int tris = s.lastFrameDrawnTriangles;
    const bool interlaced = s.getRenderer()->interlacedMode;
    bands.scene = &s;
    bands.n = interlaced ? 2 : MAX_BANDS;
    bands.step = interlaced ? 2 : 1;
    // Jet draws the odd rows of an even frame.
    bands.odd = interlaced && s.frameCounter % 2 == 0;
    for (int i = 0; i <= bands.n; ++i) bands.edge[i] = (CART_H * i / bands.n) & ~1;
    for (int i = 0; i < bands.n; ++i) bands.flags[i].assign(tris ? tris : 1, 0);
    cart_par(bands.n, band, &bands);
    int count = 0;
    for (int t = 0; t < tris; ++t) {
        uint8_t any = 0;
        for (int i = 0; i < bands.n; ++i) any |= bands.flags[i][t];
        count += any != 0;
    }
    s.lastFrameRasterizedTriangles = count;
}

int render()
{
    if (cores == 2) {
        scene->render(in_bands);
    } else {
        scene->render();
        // The field render() just drew, now that it has counted the frame.
        const bool interlaced = scene->getRenderer()->interlacedMode;
        widen(0, CART_H, interlaced ? 2 : 1,
              interlaced && (scene->frameCounter - 1) % 2 == 0);
    }
    return scene->lastFrameRasterizedTriangles;
}

const char *shading() { return NAMES[active < 0 ? 0 : active]; }

}  // namespace

extern "C" const JetScene JET_SCENE_TABLE = {
    HALF_WIDTH_BUFFERS ? CART_W / 2 * CART_H : 0,
    ZBUFFER_STRIDE(CART_W) * CART_H,
    open, update, render, shading,
};
