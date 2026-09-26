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
uint16_t *frame = nullptr, *color = nullptr;
float elapsed = 0;
int fixed_mode = SHADE_CYCLE;
int active = -1;

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
          int shading, bool interlaced)
{
    frame = frame_;
    color = color_ ? color_ : frame_;
    scene = new Scene(color, depth, CART_W, CART_H);
    scene->getRenderer()->interlacedMode = interlaced;
    camera.setPosition(0, 0, -1250);
    camera.setFOV(60.0f, CART_W);
    camera.nearPlane = 32;
    camera.farPlane = 3000;
    scene->setCamera(&camera);
    scene->setDirectionalLight(&key);
    scene->setAmbientLight(&ambient);
    scene->setClearBuffer(true);
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

#if HALF_WIDTH_BUFFERS
// Jet's half-width buffer holds one word per two output columns; the console
// takes a whole 320-wide frame, so each word is written twice.
void widen()
{
    const uint32_t *src = reinterpret_cast<const uint32_t *>(color);
    uint32_t *dst = reinterpret_cast<uint32_t *>(frame);
    for (int i = 0; i < CART_W / 2 * CART_H / 2; ++i) {
        const uint32_t two = src[i];
        dst[2 * i] = (two & 0xFFFFu) * 0x10001u;
        dst[2 * i + 1] = (two >> 16) * 0x10001u;
    }
}
#endif

int render()
{
    scene->render();
#if HALF_WIDTH_BUFFERS
    widen();
#endif
    return scene->lastFrameRasterizedTriangles;
}

const char *shading() { return NAMES[active < 0 ? 0 : active]; }

}  // namespace

extern "C" const JetScene JET_SCENE_TABLE = {
    HALF_WIDTH_BUFFERS ? CART_W / 2 * CART_H : 0,
    ZBUFFER_STRIDE(CART_W) * CART_H,
    open, update, render, shading,
};
