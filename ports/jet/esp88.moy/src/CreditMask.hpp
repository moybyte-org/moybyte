// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Nikola Jovicic
//
// Stands in for JetExamples' esp32-neon-film/main/CreditMask.hpp, the closing
// credits as a one-bit mask 360 x 168 at the film's own size, which Film.hpp
// expands into its credit texture at whatever size that texture has. The
// cart's frame is two thirds of the film's, so main.cpp sizes the texture at
// 240 x 112 and fills it with the credits' coverage from assets.bin,
// box-filtered from the example's finest mask (tools/vendor_jet.py); the mask
// here is blank at that size.
#pragma once

#include <cstdint>

#define FILM_CREDITS_W 240
#define FILM_CREDITS_H 112

namespace Film {
inline uint8_t creditMask[FILM_CREDITS_W * FILM_CREDITS_H / 8];
}
