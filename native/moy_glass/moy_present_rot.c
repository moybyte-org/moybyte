// The rotated DSI compositor (moy_present.h): the Guition P4's landscape desk
// over its portrait panel, and its frame state machine.
//
// A LANDSCAPE console on a PORTRAIT DSI panel (the Guition JC8012P4A1C, owner
// call 2026-09-06: "we want it landscape").
//
// The DSI peripheral scans a portrait framebuffer (800 wide, 1280 tall) from
// PSRAM continuously; there is no per-frame flush to fold a rotation into. So
// the console paints a persistent LANDSCAPE buffer (1280x800, `framebuffer()`)
// and flush() ROTATES it into a portrait scan buffer on the PPA, then switches
// scan-out to that buffer. Two costs, and the design is about paying the small
// one as often as possible:
//
//   * a FULL frame -- anything the WM painted that it did not describe -- is a
//     whole-buffer rotate, 2MB in and 2MB out over the same PSRAM the DSI is
//     reading at ~123MB/s. Tens of milliseconds. Every painted chrome frame
//     pays it; an idle desk paints nothing and pays nothing.
//   * a QUIET game frame -- the game composite was the frame's only write
//     (the canvas's draw gates did not move; both the windowed WM's quiet
//     stack and a fullscreen play frame look like this) -- is ONE PPA op: the
//     game canvas scaled AND rotated straight into the scan buffer, plus the
//     top bar's strip rotated from the paint buffer. A few ms, not tens.
//   * a DAMAGE frame -- the WM painted, and said WHAT it painted
//     (`note_damage`: a drag's gesture union, the window a keystroke or a
//     scroll re-rendered) -- rotates those landscape rects from the paint
//     buffer, plus the bar strip and the game rect if a game drew. The paint
//     buffer is ONE persistent picture, so "what this frame changed" is
//     exactly what the WM wrote, and the WM already reasons about that set
//     for its own backdrop restore. A frame the WM cannot describe -- a
//     window opening, a theme change, a toast -- notes nothing and pays the
//     full rotate; a description that would cost more than the full rotate
//     is declined for one.
//
// The catch is ping-pong: a rect-only frame lands in a scan buffer that was
// last shown two frames ago and may lack what the frame between painted. So
// every scan buffer carries a STALE list -- the portrait rects it has missed
// since it was last fully current -- and before a rect frame is rotated into a
// buffer, the stale rects it does not cover are copied 1:1 from the buffer on
// glass (angle 0). For a game at a fixed rect that list is the same rect every
// frame, already covered, and costs nothing. A buffer whose stale list has
// grown past a handful, or that missed a full frame, is brought current by a
// full rotate instead: correctness by construction, and the bound on the
// bookkeeping. All three scan buffers, because a show takes effect at the
// panel's NEXT refresh, not when show() returns: the buffer a show left is
// still being scanned until then, and with two buffers it is the only other
// one, so every frame presented late was rotated into the buffer on glass and
// landed mid-scan. The panel's refresh count (moy_dsi.refreshes) says when a
// show has taken effect; the partner is the target whenever it has, the third
// buffer only while the partner is still on glass, so the third's stale debt
// is paid rarely rather than every frame.
//
// EVERY FRAME IS ASYNC (2026-09-08), and the paint buffer PING-PONGS to make
// it so. A rotate is PPA time the CPU used to spend waiting -- ~11ms for a
// game frame, ~17ms for a drag's rect frame, ~48ms for a full one. Instead a
// frame's ops are QUEUED and the show waits for the next present, and the
// CPU paints the NEXT frame into the OTHER paint buffer meanwhile. Two paint
// buffers, so RETAINED_FRAMES on the root is 2 -- exactly the horizon the WM
// already runs at (its `_retained_n` floors to 2), so it paints no more than
// it did with one. The present fences with wait(keep): everything older than
// the last flush's ops has landed, so the buffer about to be painted has no
// reader left. The quiet game frame keeps its tighter fence (keep=1): its
// copy of the game canvas into the scratch must land before the cart's tick
// writes the canvas, and the strip rotate before the WM re-stamps the bar.
// A flush that finds its predecessor still flying fences and shows it first.
//
// THE DRAG STAMP rides the same queue. The WM's stamp-defer
// (P4SystemCanvas.blit_strip_async) hands the moving window's content copy
// to the compositor instead of doing it on the CPU -- 17ms of a 32ms drag
// frame through the write-allocate cache -- and flush() queues it as the
// frame's FIRST op, into the paint buffer, so the rect rotates behind it
// read the stamped pixels (the SRM engine completes in submit order: one
// tail-inserted list per engine, one transaction on the 2D-DMA at a time).

#include <string.h>

#include "moy_present.h"

static int iabs_area(const moy_rect_t *r) {
    return (int)r->w * (int)r->h;
}

moy_rect_t moy_rot_rect(int x, int y, int w, int h, int angle, int lw, int lh) {
    moy_rect_t r;
    if (angle == 90) {               // landscape (x, y) -> portrait (y, lw - 1 - x)
        r.x = (int16_t)y;
        r.y = (int16_t)(lw - x - w);
    } else {                         // 270: landscape (x, y) -> portrait (lh - 1 - y, x)
        r.x = (int16_t)(lh - y - h);
        r.y = (int16_t)x;
    }
    r.w = (int16_t)h;
    r.h = (int16_t)w;
    return r;
}

moy_rect_t moy_unrot_rect(int px, int py, int pw, int ph, int angle, int lw, int lh) {
    moy_rect_t r;
    if (angle == 90) {
        r.x = (int16_t)(lw - py - ph);
        r.y = (int16_t)px;
    } else {
        r.x = (int16_t)py;
        r.y = (int16_t)(lh - px - pw);
    }
    r.w = (int16_t)ph;
    r.h = (int16_t)pw;
    return r;
}

static bool covered(const moy_rect_t *r, const moy_rect_t *rs, int n) {
    for (int i = 0; i < n; i++) {
        const moy_rect_t *q = &rs[i];
        if (q->x <= r->x && q->y <= r->y && r->x + r->w <= q->x + q->w
            && r->y + r->h <= q->y + q->h) {
            return true;
        }
    }
    return false;
}

static moy_rect_t bbox(const moy_rect_t *a, const moy_rect_t *b) {
    int x0 = a->x < b->x ? a->x : b->x;
    int y0 = a->y < b->y ? a->y : b->y;
    int x1 = a->x + a->w > b->x + b->w ? a->x + a->w : b->x + b->w;
    int y1 = a->y + a->h > b->y + b->h ? a->y + a->h : b->y + b->h;
    moy_rect_t r = { (int16_t)x0, (int16_t)y0, (int16_t)(x1 - x0), (int16_t)(y1 - y0) };
    return r;
}

static bool same(const moy_rect_t *a, const moy_rect_t *b) {
    return a->x == b->x && a->y == b->y && a->w == b->w && a->h == b->h;
}

void moy_rot_init(moy_rot_t *r, const moy_rot_ops_t *ops, int pw, int ph, int angle,
                  bool async, bool bounce) {
    memset(r, 0, sizeof(*r));
    r->ops = ops;
    r->pw = pw;
    r->ph = ph;
    r->w = ph;
    r->h = pw;
    r->angle = angle;
    r->strip_h = 18;
    r->async = async;
    r->bounce = bounce;
    r->bounce_min_px = (int32_t)MOY_ROT_BOUNCE_MIN_PX;
    r->pending = -1;
    r->seq[0] = 0;
    r->seq[1] = r->seq[2] = -1;
    r->rseq[0] = 0;
    r->rseq[1] = r->rseq[2] = -1;
    r->stale_full[1] = r->stale_full[2] = true;     // never painted: a full rotate
    ops->show(ops->ctx, 0);
    r->front = 0;
    r->back = 1;
}

void moy_rot_set_angle(moy_rot_t *r, int angle) {
    r->angle = angle;
    for (int i = 0; i < 3; i++) {
        r->stale_full[i] = true;
        r->nstale[i] = 0;
    }
}

void moy_rot_note_damage(moy_rot_t *r, int x, int y, int w, int h) {
    int x0 = x > 0 ? x : 0;
    int y0 = y > 0 ? y : 0;
    int x1 = x + w < r->w ? x + w : r->w;
    int y1 = y + h < r->h ? y + h : r->h;
    if (x1 <= x0 || y1 <= y0) {
        return;
    }
    moy_rect_t d = { (int16_t)x0, (int16_t)y0, (int16_t)(x1 - x0), (int16_t)(y1 - y0) };
    if (r->ndamage < MOY_ROT_DAMAGE) {
        r->damage[r->ndamage++] = d;
    } else {
        // Past the list: the last slot grows to cover it -- a superset.
        r->damage[MOY_ROT_DAMAGE - 1] = bbox(&r->damage[MOY_ROT_DAMAGE - 1], &d);
    }
}

// Coalesce the noted rects into `out`: drop covered ones, take the bounding
// box above MOY_ROT_DAMAGE_RECTS. -1 when the description would not beat the
// full rotate.
static int damage_rects(const moy_rot_t *r, moy_rect_t *out) {
    int n = 0;
    for (int i = 0; i < r->ndamage; i++) {
        const moy_rect_t *d = &r->damage[i];
        if (covered(d, out, n)) {
            continue;
        }
        int k = 0;
        for (int j = 0; j < n; j++) {
            if (!covered(&out[j], d, 1)) {
                out[k++] = out[j];
            }
        }
        n = k;
        out[n++] = *d;
    }
    if (n > MOY_ROT_DAMAGE_RECTS) {
        moy_rect_t b = out[0];
        for (int i = 1; i < n; i++) {
            b = bbox(&b, &out[i]);
        }
        out[0] = b;
        n = 1;
    }
    long area = 0;
    for (int i = 0; i < n; i++) {
        area += iabs_area(&out[i]);
    }
    if (area * 10L > (long)r->w * r->h * MOY_ROT_DAMAGE_TENTHS) {
        return -1;
    }
    return n;
}

static void show(moy_rot_t *r, int n) {
    const moy_rot_ops_t *o = r->ops;
    o->show(o->ctx, n);
    r->shows++;
    r->seq[n] = r->shows;
    r->rseq[n] = o->refreshes != NULL ? o->refreshes(o->ctx) : r->shows;
    r->front = n;
}

// The buffer the panel is scanning NOW: the latest show a refresh has
// followed (a show takes effect at the panel's next refresh).
int moy_rot_on_glass(const moy_rot_t *r) {
    const moy_rot_ops_t *o = r->ops;
    int32_t now = o->refreshes != NULL ? o->refreshes(o->ctx) : r->shows + 1;
    int best = -1;
    int32_t seq = -1;
    for (int i = 0; i < 3; i++) {
        if (r->rseq[i] < now && r->seq[i] > seq) {
            seq = r->seq[i];
            best = i;
        }
    }
    return best;
}

// The scan buffer the next frame may be rotated into: neither the one on
// glass nor the one whose show waits for its refresh; of the free ones the
// most recently shown.
static int pick_back(moy_rot_t *r) {
    int busy_a = moy_rot_on_glass(r);
    int busy_b = r->front;
    int best = -1;
    int32_t seq = 0;
    bool have = false;
    for (int i = 0; i < 3; i++) {
        if (i == busy_a || i == busy_b) {
            continue;
        }
        if (!have || r->seq[i] > seq) {
            seq = r->seq[i];
            best = i;
            have = true;
        }
    }
    if (best < 0) {
        r->vsync_waits++;
        best = busy_a != busy_b ? 3 - busy_a - busy_b : (busy_a + 1) % 3;
    }
    r->back = best;
    return best;
}

static void present(moy_rot_t *r, bool late) {
    int back = r->pending;
    r->pending = -1;
    show(r, back);
    if (late) {
        r->late_n++;
    } else {
        r->pres_n++;
    }
}

// A rotate, queued when `nb`; a full queue refuses a queued submit, and then
// everything is fenced and the op resubmitted blocking.
static void rot(moy_rot_t *r, bool nb, bool wb, int dst, int dw, int dh, int dx, int dy,
                int src, int sw, int sh, int sx, int sy, int w, int h, int angle) {
    const moy_rot_ops_t *o = r->ops;
    if (o->rotate(o->ctx, nb, wb, dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h,
                  angle) < 0) {
        o->ppa_sync(o->ctx);
        r->refused++;
        o->rotate(o->ctx, false, wb, dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h,
                  angle);
    }
}

static void rot_scale(moy_rot_t *r, bool nb, bool wb, int dst, int dw, int dh, int dx,
                      int dy, int src, int sw, int sh, int scale, int angle,
                      const int16_t *blk) {
    const moy_rot_ops_t *o = r->ops;
    if (o->rotate_scale(o->ctx, nb, wb, dst, dw, dh, dx, dy, src, sw, sh, scale,
                        angle, blk) < 0) {
        o->ppa_sync(o->ctx);
        r->refused++;
        o->rotate_scale(o->ctx, false, wb, dst, dw, dh, dx, dy, src, sw, sh, scale,
                        angle, blk);
    }
}

// The paint buffer's landscape block onto scan buffer `fb`: the engine
// transactions it submitted (the frame's op count is what the present fences by).
static int rotate_block(moy_rot_t *r, int fb, int paint, int x, int y, int w, int h,
                        bool nb) {
    moy_rect_t p = moy_rot_rect(x, y, w, h, r->angle, r->w, r->h);
    const moy_rot_ops_t *o = r->ops;
    if (r->bounce && o->bounce != NULL && (long)w * h >= r->bounce_min_px) {
        moy_rect_t f = moy_rot_rect(0, y, r->w, h, r->angle, r->w, r->h);
        int n = o->bounce(o->ctx, fb, r->pw, r->ph, f.x, f.y, paint, r->w, r->h, 0, y,
                          r->w, h, r->angle, nb);
        if (n > 0) {
            r->bounced += (uint32_t)n;
            return n;
        }
        if (n < 0) {
            r->bounce = false;
        } else {
            r->refused++;
        }
    }
    rot(r, nb, false, fb, r->pw, r->ph, p.x, p.y, paint, r->w, r->h, x, y, w, h,
        r->angle);
    return 1;
}

// A compiled cart's frame straight to scan buffer `fb` at portrait (px, py):
// its view block scaled and rotated from the snapshot, then its patches.
static int frame_ops(moy_rot_t *r, bool nb, int fb, int px, int py) {
    const moy_rot_ops_t *o = r->ops;
    const moy_rot_game_t *g = &r->game;
    o->snap_wait(o->ctx);
    int16_t blk[4] = { g->bx, g->by, g->bw, g->bh };
    rot_scale(r, nb, false, fb, r->pw, r->ph, px, py, MOY_ROT_GAME, g->sw, g->sh,
              g->scale, r->angle, blk);
    for (int k = 0; k < g->npatch; k++) {
        const int16_t *pr = &g->patches[6 * k];
        moy_rect_t q = moy_rot_rect(pr[4], pr[5], pr[2] * g->scale, pr[3] * g->scale,
                                    r->angle, r->w, r->h);
        int16_t pb[4] = { pr[0], pr[1], pr[2], pr[3] };
        rot_scale(r, nb, false, fb, r->pw, r->ph, q.x, q.y, MOY_ROT_PIC, g->sw,
                  g->rows, g->scale, r->angle, pb);
    }
    return 1 + g->npatch;
}

void moy_rot_flush(moy_rot_t *r) {
    const moy_rot_ops_t *o = r->ops;
    int back = pick_back(r);
    bool game = r->has_game;
    r->has_game = false;
    moy_rect_t damage[MOY_ROT_DAMAGE];
    int ndamage = r->ndamage;
    memcpy(damage, r->damage, sizeof(damage));
    r->ndamage = 0;
    bool stamp = r->has_stamp;
    r->has_stamp = false;
    int paint_buf = MOY_ROT_PAINT0 + r->pi;
    bool nb = r->async;
    if (r->pending >= 0) {
        uint32_t tf = o->ticks_us(o->ctx);
        o->ppa_sync(o->ctx);
        present(r, true);
        r->fences++;
        r->fence_us += o->ticks_us(o->ctx) - tf;
        back = pick_back(r);
    }
    int fb = back;
    uint32_t t0 = o->ticks_us(o->ctx);
    int ops = 0;
    if (stamp) {
        const moy_rot_stamp_t *s = &r->stamp;
        rot(r, nb, true, MOY_ROT_STAMP_DST, s->dw, s->dh, s->x, s->y, MOY_ROT_STAMP_SRC,
            s->sw, s->sh, 0, 0, s->sw, s->sh, 0);
        ops++;
        r->stamp_n++;
    }
    moy_rect_t rects[MOY_ROT_RECTS];
    int nrects = -1;            // -1: no description, a full rotate
    bool direct_game = false;
    int tail = 1;
    bool painted = false;
    moy_rect_t grect = { 0, 0, 0, 0 };
    const moy_rot_game_t *g = &r->game;
    if (game) {
        grect.x = (int16_t)g->ox;
        grect.y = (int16_t)g->oy;
        grect.w = (int16_t)((g->frame ? g->bw : g->sw) * g->scale);
        grect.h = (int16_t)((g->frame ? g->bh : g->sh) * g->scale);
        if (ndamage == 0 && !stamp && o->quiet(o->ctx)) {
            rects[0] = grect;
            nrects = 1;
            direct_game = g->direct;
        } else {
            o->paint(o->ctx);           // the paint buffer needs it too
            painted = true;
        }
    }
    if (nrects < 0 && ndamage > 0) {
        moy_rot_t tmp;
        tmp.w = r->w;
        tmp.h = r->h;
        tmp.ndamage = ndamage;
        memcpy(tmp.damage, damage, sizeof(damage));
        moy_rect_t dr[MOY_ROT_DAMAGE];
        int n = damage_rects(&tmp, dr);
        if (n < 0) {
            r->dmg_declined++;
        } else {
            nrects = 0;
            if (game) {
                rects[nrects++] = grect;
            }
            for (int i = 0; i < n; i++) {
                rects[nrects++] = dr[i];
            }
            r->dmg_n++;
            r->dmg_rects += (uint32_t)n;
        }
    }
    if (nrects >= 0 && r->strip_h > 0) {
        moy_rect_t s = { 0, 0, (int16_t)r->w,
                         (int16_t)(r->strip_h < r->h ? r->strip_h : r->h) };
        rects[nrects++] = s;
    }
    moy_rect_t changed[MOY_ROT_RECTS];
    for (int i = 0; i < nrects; i++) {
        changed[i] = moy_rot_rect(rects[i].x, rects[i].y, rects[i].w, rects[i].h,
                                  r->angle, r->w, r->h);
    }
    if (nrects < 0 || r->stale_full[back] || r->nstale[back] > MOY_ROT_STALE_LIMIT) {
        if (game && !painted) {
            o->paint(o->ctx);           // a full rotate reads the paint buffer
        }
        ops += rotate_block(r, fb, paint_buf, 0, 0, r->w, r->h, nb);
        r->full_n++;
        r->full_us += o->ticks_us(o->ctx) - t0;
    } else {
        int front = r->front;
        moy_rect_t cover[MOY_ROT_RECTS];
        memcpy(cover, changed, sizeof(cover));
        for (int k = 0; k < r->nstale[back]; k++) {
            const moy_rect_t *s = &r->stale[back][k];
            if (covered(s, cover, nrects)) {
                continue;
            }
            int best = -1;
            int best_cost = 0;
            moy_rect_t best_grown = { 0, 0, 0, 0 };
            moy_rect_t us = moy_unrot_rect(s->x, s->y, s->w, s->h, r->angle, r->w, r->h);
            for (int i = direct_game ? 1 : 0; i < nrects; i++) {
                moy_rect_t grown = bbox(&rects[i], &us);
                int cost = iabs_area(&grown) - iabs_area(&rects[i]);
                if (cost < iabs_area(s) && (best < 0 || cost < best_cost)) {
                    best = i;
                    best_cost = cost;
                    best_grown = grown;
                }
            }
            if (best >= 0) {
                rects[best] = best_grown;
                cover[best] = moy_rot_rect(best_grown.x, best_grown.y, best_grown.w,
                                           best_grown.h, r->angle, r->w, r->h);
                r->grown++;
                continue;
            }
            rot(r, nb, false, fb, r->pw, r->ph, s->x, s->y, front, r->pw, r->ph, s->x,
                s->y, s->w, s->h, 0);
            ops++;
            r->copies++;
        }
        if (direct_game) {
            for (int i = 1; i < nrects; i++) {
                ops += rotate_block(r, fb, paint_buf, rects[i].x, rects[i].y, rects[i].w,
                                    rects[i].h, nb);
            }
            int px = changed[0].x, py = changed[0].y;
            if (g->frame) {
                tail = frame_ops(r, nb, fb, px, py);
                ops += tail;
            } else if (nb) {
                o->scratch(o->ctx, (size_t)g->sw * g->sh * 2u);
                rot(r, true, false, MOY_ROT_SCRATCH, g->sw, g->sh, 0, 0, MOY_ROT_GAME, g->sw,
                    g->sh, 0, 0, g->sw, g->sh, 0);
                rot_scale(r, true, false, fb, r->pw, r->ph, px, py, MOY_ROT_SCRATCH, g->sw,
                          g->sh, g->scale, r->angle, NULL);
                ops += 2;
            } else {
                rot_scale(r, false, false, fb, r->pw, r->ph, px, py, MOY_ROT_GAME, g->sw,
                          g->sh, g->scale, r->angle, NULL);
            }
        } else {
            if (game && !painted) {
                o->paint(o->ctx);       // crisp quiet: composite, then rotate
            }
            // Below the bounce size first: a big block's bands fill the
            // queue, and the small ones queued behind them would wait.
            for (int pass = 0; pass < 2; pass++) {
                for (int i = 0; i < nrects; i++) {
                    bool big = r->bounce && o->bounce != NULL
                               && iabs_area(&rects[i]) >= r->bounce_min_px;
                    if (big == (pass == 1)) {
                        ops += rotate_block(r, fb, paint_buf, rects[i].x, rects[i].y,
                                            rects[i].w, rects[i].h, nb);
                    }
                }
            }
        }
        r->rect_n++;
        r->rect_us += o->ticks_us(o->ctx) - t0;
    }
    r->stale_full[back] = false;
    r->nstale[back] = 0;
    for (int other = 0; other < 3; other++) {
        if (other == back) {
            continue;
        }
        if (nrects < 0) {
            r->stale_full[other] = true;
            r->nstale[other] = 0;
        } else if (!r->stale_full[other]) {
            for (int i = 0; i < nrects; i++) {
                bool have = false;
                for (int k = 0; k < r->nstale[other]; k++) {
                    if (same(&r->stale[other][k], &changed[i])) {
                        have = true;
                        break;
                    }
                }
                if (have) {
                    continue;
                }
                if (r->nstale[other] < MOY_ROT_STALE_CAP) {
                    r->stale[other][r->nstale[other]++] = changed[i];
                } else {
                    r->stale_full[other] = true;    // past the limit: a full rotate
                    r->nstale[other] = 0;
                    break;
                }
            }
        }
    }
    r->pi = 1 - r->pi;
    if (nb) {
        // Everything older than this frame's ops lands before the buffer
        // painted next is touched; a direct game frame's copy of the game
        // canvas lands before the cart's tick.
        r->keep = direct_game ? tail : ops;
        r->pending = back;
        r->def_n++;
        return;
    }
    show(r, back);
}

void moy_rot_present_pending(moy_rot_t *r) {
    const moy_rot_ops_t *o = r->ops;
    if (r->pending < 0) {
        return;
    }
    uint32_t t0 = o->ticks_us(o->ctx);
    o->ppa_wait(o->ctx, r->keep);
    r->wait_n++;
    r->wait_us += o->ticks_us(o->ctx) - t0;
    if (o->ppa_done(o->ctx)) {
        present(r, false);
    }
}

void moy_rot_fence(moy_rot_t *r) {
    if (r->pending >= 0) {
        r->ops->ppa_sync(r->ops->ctx);
        present(r, true);
    }
}
