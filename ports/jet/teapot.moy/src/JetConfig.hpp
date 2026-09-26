// Jet's compile-time configuration for this cart: JetExamples'
// esp32-lighting-teapot/main/firmware/JetConfig.hpp, less what belongs to that
// example's ESP32 runtime (its S3 raster worker, internal-RAM scratch and
// field buffers). Every switch Jet reads is set here.
//
// HALF_WIDTH_BUFFERS is the one this cart builds both ways: the module carries
// Jet twice, once full width and once in Jet's own namespace renamed with the
// half-width buffers on (tools/jet_cart.py), so config.json picks the mode at
// launch without a second cart.
#pragma once

#ifndef HALF_WIDTH_BUFFERS
#define HALF_WIDTH_BUFFERS 0
#endif

#define JET_MESH_INSTANCING 0
#define RENDER_TILE_BUFFER 0
#define TILE_WIDTH 32
#define TILE_HEIGHT 32

// Per-pixel depth for the intersecting handle, spout and body.
#define FAST_Z 0
#define JET_PERSPECTIVE_DEPTH 0
#define LAZY_Z 0
#define Z_BUFFERING 1
#define SORT_TRIANGLES 0
#define JET_SORT_DEPTH_BUCKETS 64
#define JET_DEPTH_SORT_OPAQUE_FRONT_TO_BACK 1
#define SORT_SCENE_OBJECTS 0
#define SORT_SCENE_REVERSE 0
#define JET_RUNTIME_DEPTH 0

#define SCREEN_DOOR_ALPHA 0
#define NOISE_ALPHA 0
#define SKIP_ZERO_AREA_TRIANGLES 1
#define DEPTH_ALPHA_BLEND 0

#define TEXTURE_MAPPING 0
#define PERSPECTIVE_CORRECT_TEXTURES 0
#define JET_HIGH_PRECISION_UVS 0
#define BILINEAR_FILTER 0

#define LIGHTING 1
#define Z_BRIGHTNESS 0

#define FLOAT_CAMERA_ANGLES 1
#define FLOAT_SIN_CACHE_SCALE 10
#define FLOAT_TAN_CACHE_SCALE 1

// The frame goes to the console whole through blit565, so there is no scanout
// to split into fields; interlacing is Jet's runtime switch
// (Rasterizer::interlacedMode) over the one full-height buffer.
#define FIELD_BUFFERS 0
#define SSR_FIELD_REFLECT 0
#define CHECKERBOARD_MODE 0
#define CHECKERBOARD_RECONSTRUCTION 0

#define POSTFX_CRT 0
#define POSTFX_CELLSHADING 0
#define POSTFX_ANTIALIASING 0
#define POSTFX_BLOOM 0
#define POSTFX_MOTION_BLUR 0
#define POSTFX_CHROMATIC 0
#define POSTFX_PIXELATE 0
#define CRT_SCANLINE_INTENSITY 48
#define MOTION_BLUR_STRENGTH 50
#define CHROMATIC_OFFSET 2
#define PIXELATE_SIZE 4
#define CELLSHADING_CELL_BITS 4
#define DEBUG_OVERDRAW 0

#define zBrightFar 1600
#define zBrightNear 200
#define zBrightScale 48
#define depthFogFar 8192
#define depthFogNear 6144

#define MAX_PICK_QUERIES 0
