// moy_touch: the mapping and the no-news contract. moy_touch.h has the contract.

#include "moy_touch.h"

#include <string.h>

#include "moy_input.h"

void moy_touch_map(const moy_touch_map_t *m, int32_t rx, int32_t ry, int32_t *x, int32_t *y) {
    int32_t px = m->swap ? ry : rx;
    int32_t py = m->swap ? rx : ry;
    if (m->raw_w) {
        int64_t v = (int64_t)(px - m->raw_x0) * m->w;
        px = (int32_t)(v >= 0 ? v / m->raw_w : -((-v + m->raw_w - 1) / m->raw_w));
    }
    if (m->raw_h) {
        int64_t v = (int64_t)(py - m->raw_y0) * m->h;
        py = (int32_t)(v >= 0 ? v / m->raw_h : -((-v + m->raw_h - 1) / m->raw_h));
    }
    if (m->flip_x) {
        px = m->w - 1 - px;
    }
    if (m->flip_y) {
        py = m->h - 1 - py;
    }
    px = px < 0 ? 0 : (px >= m->w ? m->w - 1 : px);
    py = py < 0 ? 0 : (py >= m->h ? m->h - 1 : py);
    *x = px;
    *y = py;
}

void moy_touch_held_init(moy_touch_held_t *hp, bool extrapolate, int32_t w, int32_t h, float damp,
                         int32_t hold_ms) {
    memset(hp, 0, sizeof(*hp));
    hp->hold_ms = hold_ms;
    hp->fresh = true;
    hp->extrapolate = extrapolate;
    hp->w = w;
    hp->h = h;
    hp->damp = damp;
}

#define VEL_EMA 0.5f

void moy_touch_sample(moy_touch_held_t *hp, int32_t x, int32_t y, uint32_t now, moy_touch_pt_t *out) {
    bool edge = !hp->down;
    int32_t ox = x;
    int32_t oy = y;
    if (hp->extrapolate) {
        if (edge || !hp->has_last) {
            hp->vx = 0.0f;
            hp->vy = 0.0f;
        } else {
            int32_t dt = moy_input_ticks_diff(now, hp->ms);
            if (dt > 0 && dt <= 100) {
                hp->vx += ((float)(x - hp->lx) / (float)dt - hp->vx) * VEL_EMA;
                hp->vy += ((float)(y - hp->ly) / (float)dt - hp->vy) * VEL_EMA;
            }
        }
        if (hp->gliding && !edge) {
            int32_t bx = hp->gx - x;
            int32_t by = hp->gy - y;
            float behind = (float)bx * hp->vx + (float)by * hp->vy;
            if (behind > 0.0f && bx * bx + by * by <= 1024) {
                ox = hp->gx;
                oy = hp->gy;
            }
        }
        hp->gliding = false;
    }
    hp->down = true;
    hp->has_last = true;
    hp->lx = x;
    hp->ly = y;
    hp->ms = now;
    hp->fresh = true;
    out->down = true;
    out->edge = edge;
    out->x = ox;
    out->y = oy;
}

void moy_touch_release(moy_touch_held_t *hp, moy_touch_pt_t *out) {
    hp->down = false;
    hp->has_last = false;
    hp->gliding = false;
    hp->fresh = true;
    memset(out, 0, sizeof(*out));
}

static int32_t clamp_to(int32_t v, int32_t n) {
    return v < 0 ? 0 : (v >= n ? n - 1 : v);
}

void moy_touch_hold(moy_touch_held_t *hp, uint32_t now, moy_touch_pt_t *out) {
    memset(out, 0, sizeof(*out));
    if (hp->down && hp->has_last) {
        int32_t dt = moy_input_ticks_diff(now, hp->ms);
        if (dt < hp->hold_ms) {
            hp->fresh = false;
            int32_t x = hp->lx;
            int32_t y = hp->ly;
            if (hp->extrapolate && dt > 0) {
                x = (int32_t)((float)x + hp->vx * hp->damp * (float)dt);
                y = (int32_t)((float)y + hp->vy * hp->damp * (float)dt);
                if (hp->w) {
                    x = clamp_to(x, hp->w);
                }
                if (hp->h) {
                    y = clamp_to(y, hp->h);
                }
                hp->gx = x;
                hp->gy = y;
                hp->gliding = true;
            }
            out->down = true;
            out->x = x;
            out->y = y;
            return;
        }
        hp->down = false;
        hp->has_last = false;
    }
    hp->fresh = true;
}
