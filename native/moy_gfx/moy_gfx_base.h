// What the raster's files share: the byte order of the RGB565 words they
// write, and an integer square root.
//
// THE WIRE ORDER is one fact of the board: byte-swapped (1, the default: the
// S3 panels, the host and the browser hold a word high byte first) or
// canonical (0: the P4's DPI scan-out). Text (moy_text.c), shapes
// (moy_shape.c) and paths (moy_path.c) all blend in it, and moy_gfx_wire is
// the one call that sets it, before a frame draws: the canvas sets it from its
// wire palette (device/device_canvas.py's WIRE_SWAPPED), the kernel's floor and
// splash from their screen's `swap`, an app linked as an ELF from its frame's.
//
// It is state at file scope and needs no lock: every board sets it once, to
// its panel's order, and every caller after sets the same value.

#ifndef MOY_GFX_BASE_H
#define MOY_GFX_BASE_H

#include <stdint.h>

extern int moy_gfx_swapped;

// Set the wire order where `swapped` is 0 or 1 (a negative value only asks);
// answers the order it was.
int moy_gfx_wire(int swapped);

// A canonical RGB565 word in the wire order, and a wire word canonical (the
// swap is its own inverse).
static inline uint16_t moy_gfx_order(uint16_t w) {
    return moy_gfx_swapped ? (uint16_t)((w << 8) | (w >> 8)) : w;
}

// floor(sqrt(v)).
static inline uint32_t mg_isqrt64(uint64_t v) {
    uint64_t r = 0, b = (uint64_t)1 << 62;
    while (b > v) {
        b >>= 2;
    }
    while (b) {
        if (v >= r + b) {
            v -= r + b;
            r = (r >> 1) + b;
        } else {
            r >>= 1;
        }
        b >>= 2;
    }
    return (uint32_t)r;
}

#endif
