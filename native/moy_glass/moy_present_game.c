// The game canvas composited onto a P4's glass (moy_present.h, THE GAME
// COMPOSITE): one body for the console's canvas (moy_glass.game_composite,
// device/p4_canvas.py), the rotated compositor's paint route and the
// kernel's front (modmoy_glass.c).
//
// The choice. CRISP PIXELS takes the PPA's banded nearest
// (moy_ppa_k_crisp, an SRAM bounce byte-exact against the CPU kernel);
// otherwise the PPA's bilinear scale (moy_ppa_k_scale, or _nb when the
// caller defers the show); and the CPU kernel's nearest (mg_blit565_scale)
// wherever the PPA does not take it. The PPA cannot clip, so a scaled block
// that does not fit inside the picture takes the CPU kernel: a normal
// per-call condition, never a demotion. An error on the bilinear scale
// demotes the composite to the CPU kernel for the kernel's life; a crisp
// refusal (no SRAM bands, a submit refused) takes the CPU kernel for that
// call alone and demotes nothing. A compiled cart's direct-colour frame
// (`frame`) keeps the bilinear scale whatever CRISP PIXELS says, as its
// quiet frames, scaled straight from the snapshot, do.

#include "moy_present.h"

#if __has_include("moy_ppa.h")
#include "moy_ppa.h"
#else
int moy_ppa_k_scale(uint16_t *dst, int dw, int dh, int dx, int dy, const uint16_t *src, int sw, int sh, int scale);
int moy_ppa_k_scale_nb(uint16_t *dst, int dw, int dh, int dx, int dy, const void *src, int sw, int sh, int scale);
int moy_ppa_k_crisp(uint16_t *dst, int dw, int dh, int dx, int dy, const uint16_t *src, int sw, int sh, int scale, bool defer);
#endif
#ifndef MOY_PPA_K_OFF
#define MOY_PPA_K_OFF (-2)              // moy_ppa.h's; moot where the entries are absent
#endif
#pragma weak moy_ppa_k_scale
#pragma weak moy_ppa_k_scale_nb
#pragma weak moy_ppa_k_crisp

// moy_gfx_kernels.h's nearest-neighbour upscale (moy_gfx is in every console).
void mg_blit565_scale(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                      const uint16_t *src, size_t scap, int sw, int sh, int scale);

static bool s_crisp;
static bool s_demoted;

void moy_game_crisp(bool on) {
    s_crisp = on;
}

static bool game_fits(int dw, int dh, int dx, int dy, int sw, int sh, int scale) {
    return scale >= 1 && dx >= 0 && dy >= 0 && dx + sw * scale <= dw && dy + sh * scale <= dh;
}

bool moy_game_direct(int dw, int dh, int dx, int dy, int sw, int sh, int scale) {
    return !s_crisp && !s_demoted && moy_ppa_k_scale != NULL
           && game_fits(dw, dh, dx, dy, sw, sh, scale);
}

int moy_game_composite(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                       const uint16_t *src, size_t scap, int sw, int sh, int scale,
                       bool defer, bool frame) {
    int out = MOY_GAME_DONE;
    if (!s_demoted && game_fits(dw, dh, dx, dy, sw, sh, scale)
            && dcap >= (size_t)dw * (size_t)dh && scap >= (size_t)sw * (size_t)sh) {
        if (s_crisp && !frame) {
            if (moy_ppa_k_crisp != NULL
                    && moy_ppa_k_crisp(dst, dw, dh, dx, dy, src, sw, sh, scale, defer) == 0) {
                return defer ? MOY_GAME_QUEUED : MOY_GAME_DONE;
            }
        } else if (moy_ppa_k_scale != NULL) {
            int e = defer ? moy_ppa_k_scale_nb(dst, dw, dh, dx, dy, src, sw, sh, scale)
                          : moy_ppa_k_scale(dst, dw, dh, dx, dy, src, sw, sh, scale);
            if (e == 0) {
                return defer ? MOY_GAME_QUEUED : MOY_GAME_DONE;
            }
            if (e != MOY_PPA_K_OFF) {
                s_demoted = true;
                out = MOY_GAME_DEMOTED;
            }
        }
    }
    mg_blit565_scale(dst, dcap, dw, dh, dx, dy, src, scap, sw, sh, scale);
    return out;
}
