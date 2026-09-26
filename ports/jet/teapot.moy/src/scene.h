// The teapot scene as main.cpp drives it. scene.cpp is compiled once per Jet
// build -- full width, and half width in a renamed namespace -- and each
// compilation defines one of the two tables below.
#pragma once

#include <stdint.h>

#define CART_W 320
#define CART_H 240

enum { SHADE_CYCLE, SHADE_FLAT, SHADE_GOURAUD, SHADE_PHONG };

struct CartInput {
    int left, right, up, down, a, b;
};

struct JetScene {
    // How many 16-bit words this build's colour buffer takes; the depth
    // buffer takes the same. Full width renders straight into the frame, so
    // its colour buffer is the frame and it needs none of its own.
    int color_words;
    int depth_words;
    // Build the scene over the buffers, with the model's OBJ text (loaded
    // before this returns, then no longer needed). False when it would not
    // load.
    bool (*open)(uint16_t *frame, uint16_t *color, uint16_t *depth,
                 const char *obj, int shading, bool interlaced);
    void (*update)(float dt, const CartInput *in);
    // Render; the whole frame is then in `frame`. The triangles rasterized.
    int (*render)(void);
    const char *(*shading)(void);
};

extern "C" const JetScene jet_full, jet_half;
