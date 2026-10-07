// Present: the compositors' frame state machines (docs/kernel_survival_2026-10.md
// §3.4, docs/surface_model_v1.md §4).
//
// The ENGINE owns the frame state -- which buffer is drawn into, which ships,
// what is still in flight -- and the BOARD owns the transport, through an ops
// table: native/moy_flush's split one tier up. Pure C over the ops, so the host
// drives every state machine here with a fake transport (moy_glass_host.c) and
// a board with its panel module's kernel entries (MOY_KERNEL_PANEL).
//
// BANDED (T-Deck, Guition S3): a ping-pong of PSRAM framebuffers a panel
// module ships band by band from its core-0 feeder. present() is three steps:
// drain the previous frame's residue, swap so the buffer just drawn becomes the
// front, kick it and return, so the next frame renders while the panel reads.
// With one buffer, or a transport with no kick, present() ships synchronously.

#ifndef MOY_PRESENT_H
#define MOY_PRESENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_PRESENT_MAX_FBS 4

typedef struct {
    // Wait out the frame in flight; false when it did not go out cleanly.
    bool (*drain)(void *ctx);
    // Hand framebuffer `n` to the feeder and return; nonzero is the PREVIOUS
    // frame's transport error, raised before this one is handed over (and then
    // nothing is kicked). NULL: the transport cannot overlap.
    int (*kick)(void *ctx, int n);
    // Ship framebuffer `n` and wait it out; nonzero on failure.
    int (*show)(void *ctx, int n);
    void *ctx;
} moy_banded_ops_t;

typedef struct {
    const moy_banded_ops_t *ops;
    uint8_t nfbs;
    uint8_t back;               // the buffer this frame draws into
    uint8_t overlap;            // kick/drain, or show
    uint32_t presents;
} moy_banded_t;

void moy_banded_init(moy_banded_t *b, const moy_banded_ops_t *ops, int nfbs,
                     bool overlap);
// The frame is drawn: ship it. Returns the transport's error, 0 when none.
int moy_banded_present(moy_banded_t *b);
// Leave no panel transfer in flight.
bool moy_banded_fence(moy_banded_t *b);

// DSI (Waveshare P4): moy_dsi SCANS a PSRAM framebuffer continuously, so a
// present is a zero-copy switch of the scanned buffer, not a transfer. With
// three buffers a frame whose async composite (the PPA's game blit, a window
// stamp) still flies is QUEUED: its show waits for the composite, drawing
// moves to the next buffer, and a buffer still read by a queued op is fenced
// before it is drawn into again. With two the deferred frame is shown at the
// loop's pre-frame hook (present_pending); with one, present is the cache
// writeback alone.

// The async-overlap meters, in order: every DSI compositor returns
// THIS field set from `overlap_stats()`, whatever its path does internally.
// `runtime/frame_loop.PerfSampler` deltas the tuple and the PERF line prints
// five of the slots as `ppa=` with `fence_us`/`game_us` as fence_ms/gfence_ms,
// so a compositor that answered a slot with some other counter would print one
// board's number under every board's label. A slot whose mechanism this path
// does not have is None -- the absence rule, because 0 is also what a broken
// meter reads as.
//
//   deferred  composites kicked async whose scan-out switch was held a loop --
//             the denominator for the rest.
//   obsolete  queued shows a newer full paint replaced: composited, unseen.
//   fences    blocking fences in flush() that free the next paint target, and
//             fence_us their cost. One per deferred frame means the extra
//             framebuffer buys nothing.
//   game_n    the blocking fence in present_pending() -- the one that must land
//             before the cart's tick overwrites the composite's source -- and
//             game_us its cost. It runs inside FrameLoop's UNTIMED present()
//             hook, so this is the only place it is visible.
//   timeouts  moy_ppa fences that gave up. Must stay 0, and a fence cannot
//             RAISE (it runs where a throw would take the desktop down), so
//             this is the only sign of a wedge.

enum { MOY_DSI_GAME = 0, MOY_DSI_STAMP = 1 };

typedef struct {
    void (*show)(void *ctx, int n);         // msync + switch scan-out to n
    void (*msync)(void *ctx);               // single buffer: the writeback alone
    void (*ppa_sync)(void *ctx);            // every PPA op landed
    bool (*ppa_done)(void *ctx);            // ...without waiting; NULL: unknown
    uint32_t (*ticks_us)(void *ctx);
    void *ctx;
} moy_dsi_ops_t;

typedef struct {
    const moy_dsi_ops_t *ops;
    uint8_t nfbs;
    uint8_t back;
    bool composite_pending;     // this frame's composite flies (the canvas sets it)
    int8_t pending;             // two buffers: the deferred show, or -1
    uint8_t npend, nbusy;
    uint8_t pend[MOY_PRESENT_MAX_FBS];      // queued shows, oldest first
    uint8_t kind[MOY_PRESENT_MAX_FBS];      // ...and what each one waits for
    uint8_t busy[MOY_PRESENT_MAX_FBS];      // buffers a queued op still reads
    uint32_t deferred, obsolete, fences, fence_us, game_n, game_us;
} moy_dsi_t;

void moy_dsi_init(moy_dsi_t *d, const moy_dsi_ops_t *ops, int nfbs);
// The frame is drawn: show it, or queue it behind its composite. `stamp`
// says the binding just kicked a deferred window stamp into this buffer.
void moy_dsi_present(moy_dsi_t *d, bool stamp);
// The loop's pre-frame hook: show what the last present deferred, fencing
// only when the cart's next draw would overwrite a composite's source.
void moy_dsi_present_pending(moy_dsi_t *d);
// No PPA op in flight and no deferred show unpresented.
void moy_dsi_fence(moy_dsi_t *d);

// ROTATED DSI (Guition P4): a LANDSCAPE desk on PORTRAIT glass. The console
// paints one of two persistent landscape paint buffers and present ROTATES it
// onto a portrait scan buffer on the PPA -- the whole buffer for an
// undescribed frame, the rects the WM noted for a damage frame, ONE scale+
// rotate op straight from the game canvas for a quiet game frame -- with a
// stale list per scan buffer so a rect frame never lands on a buffer missing
// what the frame before painted. Every frame's ops are queued; its show waits
// for the loop's pre-frame hook. moy_present_rot.c's header is the why of
// each rule.
//
// Buffers are named, not pointed at: the binding maps each id to its own.

typedef struct {
    int16_t x, y, w, h;
} moy_rect_t;

enum {
    MOY_ROT_FB0 = 0,            // the scan buffers 0..2
    MOY_ROT_PAINT0 = 3,         // the paint buffers 3..4
    MOY_ROT_SCRATCH = 5,        // the game canvas's copy a queued rotate reads
    MOY_ROT_GAME = 6,           // the game canvas, or a compiled cart's snapshot
    MOY_ROT_PIC = 7,            // a compiled cart's patch picture
    MOY_ROT_STAMP_DST = 8,      // the deferred window stamp
    MOY_ROT_STAMP_SRC = 9,
};

#define MOY_ROT_STALE_LIMIT 6           // more distinct stale rects: a full rotate
#define MOY_ROT_STALE_CAP 8
#define MOY_ROT_DAMAGE 32               // noted rects a frame keeps
#define MOY_ROT_DAMAGE_RECTS 3          // above this many, their bounding box
#define MOY_ROT_DAMAGE_TENTHS 6         // a description over 6/10 of the frame: full
#define MOY_ROT_RECTS 8                 // game + damage + strip, after coalescing
#define MOY_ROT_BOUNCE_MIN_PX (384L * 1024L)

typedef struct {
    void (*show)(void *ctx, int n);
    int32_t (*refreshes)(void *ctx);    // NULL: the panel has no refresh count
    void (*ppa_sync)(void *ctx);
    bool (*ppa_done)(void *ctx);
    void (*ppa_wait)(void *ctx, int keep);
    void (*snap_wait)(void *ctx);
    // A rotate; queued when nb. -1: the full queue refused a queued submit (a
    // blocking one that fails raises in the binding).
    int (*rotate)(void *ctx, bool nb, bool wb, int dst, int dw, int dh, int dx,
                  int dy, int src, int sw, int sh, int sx, int sy, int w, int h,
                  int angle);
    int (*rotate_scale)(void *ctx, bool nb, bool wb, int dst, int dw, int dh, int dx,
                        int dy, int src, int sw, int sh, int scale, int angle,
                        const int16_t *blk);  // blk: x, y, w, h of src, or NULL
    // The SRAM-bounce rotate: its transactions, 0 for a refused queue, <0 when
    // the pipeline is not to be had. NULL: none.
    int (*bounce)(void *ctx, int fb, int pw, int ph, int fx, int fy, int paint,
                  int w, int h, int sx, int sy, int bw, int bh, int angle, bool nb);
    void (*paint)(void *ctx);           // composite the game into the paint buffer
    bool (*quiet)(void *ctx);           // nothing but the game drew this frame
    void (*scratch)(void *ctx, size_t n);   // MOY_ROT_SCRATCH holds n bytes
    uint32_t (*ticks_us)(void *ctx);
    void *ctx;
} moy_rot_ops_t;

typedef struct {
    int sw, sh, ox, oy, scale;
    bool direct, frame;
    int16_t bx, by, bw, bh;     // a compiled cart's view block of the snapshot
    int rows;                   // ...its patch picture's rows
    int npatch;
    int16_t patches[6 * 4];     // (bx, by, bw, bh, landing x, landing y) each
} moy_rot_game_t;

typedef struct {
    int dw, dh, x, y, sw, sh;
} moy_rot_stamp_t;

typedef struct {
    const moy_rot_ops_t *ops;
    int pw, ph, w, h, angle, strip_h;
    int32_t seq[3], rseq[3], shows;
    int front, back, pi, pending, keep;
    bool async, bounce, has_game, has_stamp;
    int32_t bounce_min_px;      // a block this big goes through the bounce
    bool stale_full[3];
    uint8_t nstale[3];
    moy_rect_t stale[3][MOY_ROT_STALE_CAP];
    int ndamage;
    moy_rect_t damage[MOY_ROT_DAMAGE];
    moy_rot_game_t game;
    moy_rot_stamp_t stamp;
    uint32_t vsync_waits, full_n, full_us, rect_n, rect_us, copies, grown;
    uint32_t dmg_n, dmg_rects, dmg_declined, bounced, stamp_n, refused;
    uint32_t def_n, pres_n, late_n, fences, fence_us, wait_n, wait_us;
} moy_rot_t;

moy_rect_t moy_rot_rect(int x, int y, int w, int h, int angle, int lw, int lh);
moy_rect_t moy_unrot_rect(int px, int py, int pw, int ph, int angle, int lw, int lh);
void moy_rot_init(moy_rot_t *r, const moy_rot_ops_t *ops, int pw, int ph, int angle,
                  bool async, bool bounce);
void moy_rot_set_angle(moy_rot_t *r, int angle);
void moy_rot_note_damage(moy_rot_t *r, int x, int y, int w, int h);
int moy_rot_on_glass(const moy_rot_t *r);
void moy_rot_flush(moy_rot_t *r);
void moy_rot_present_pending(moy_rot_t *r);
void moy_rot_fence(moy_rot_t *r);

#endif // MOY_PRESENT_H
