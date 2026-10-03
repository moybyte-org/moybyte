// SPDX-License-Identifier: MIT
// Copyright (c) 2026 CubeCoders Limited
// Copyright (c) 2026 Nikola Jovicic
//
// The film's artwork, by the names JetExamples' esp32-neon-film/main/Assets.hpp
// gives it, which World.hpp includes. The example compiles its pixels in as
// initialised data; here the arrays are zeroed and main.cpp fills them from the
// cart's assets.bin (tools/vendor_jet.py derives it from that header) before
// the film's constructors run. Initialised data would sit in the module as well
// as in linear memory, and the module is what a board holds twice while it
// loads. A palette is declared at the most a byte index reaches.
#pragma once

#include <cstdint>

namespace Assets {
alignas(16) inline uint8_t facade0[64 * 128], facade1[64 * 128], facade2[64 * 128],
    facade3[64 * 128];
alignas(16) inline uint8_t shop0[128 * 64], shop1[128 * 64], shop2[128 * 64], shop3[128 * 64];
alignas(16) inline uint8_t sign0[128 * 64], sign1[128 * 64], sign2[128 * 64], sign3[128 * 64],
    sign4[128 * 64], sign5[128 * 64];
alignas(16) inline uint8_t environment[128 * 64], hologram[64 * 96], dashboard[256 * 128];
alignas(16) inline uint16_t facade0Palette[256], facade1Palette[256], facade2Palette[256],
    facade3Palette[256];
alignas(16) inline uint16_t shop0Palette[256], shop1Palette[256], shop2Palette[256],
    shop3Palette[256];
alignas(16) inline uint16_t sign0Palette[256], sign1Palette[256], sign2Palette[256],
    sign3Palette[256], sign4Palette[256], sign5Palette[256];
alignas(16) inline uint16_t environmentPalette[256], hologramPalette[256],
    dashboardPalette[256];
// The additive halo, 16 x 16, which the film mirrors into 32 x 32.
alignas(16) inline uint16_t glow[16 * 16];
}  // namespace Assets
