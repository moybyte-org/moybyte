// moy_fold: the GAME FOLD, shared by both banded boards (#190 on the T-Deck,
// its cousin on the Guition). It is the second half of `moy_flush`'s split and
// lives beside it for the same reason: this is a protocol with a one-shot
// latch, a cross-core fence and a deferred composite, and a protocol that
// lives twice is a protocol whose next fix lands once.
//
// WHAT THE FOLD IS. A cart with a small canvas (SPEC.md 1/3.1) is composited
// into the root framebuffer -- black bezels plus one integer upscale -- and the
// banded flush then reads that same root back out of PSRAM to fill its bounce
// slots. Both passes move the whole screen. The fold deletes the first: the
// canvas ARMS the fold with a snapshot of the (view-cropped) game frame, and
// the flush SYNTHESIZES every band straight from that snapshot -- black outside
// the viewport, game pixels at integer scale inside. On a folded frame the root
// is neither written by a composite nor read by the pump.
//
// WHAT IS SHARED AND WHAT IS NOT. `moy_flush.h`'s split says the band synthesis
// stays per-board, and half of it still does: the ROOT-copy synthesis is the
// board's (moy_lcd memcpys a row run, moy_axs rotate-gathers the landscape
// buffer into the portrait panel), because it reads the board's own
// framebuffer with the board's own stride. The FOLD synthesis does not -- it
// reads a caller-supplied RGB565 rectangle and writes a bounce slot, with no
// transport in it at all -- so both shapes live here, parameterized by
// geometry: `moy_fold_band` for a straight-through panel and
// `moy_fold_band_rot` for a rotated one. That is what lets a second board take
// the lever without a second copy of the latch, the fence and the scale
// arithmetic.
//
// THE LATCH IS ONE-SHOT, and the ordering is the whole safety argument:
//
//   arm      (VM) the canvas hands over a buffer + geometry. May be REFUSED
//            (geometry the synthesis cannot express); the caller then performs
//            the composite itself, so a decline is invisible one level up.
//   consume  (VM, feeder IDLE -- every caller drains first) latches `armed`
//            into `inflight` and clears it. A frame is folded or it is not,
//            decided once, before the feeder can see either flag.
//   band     (FEEDER) reads `src` and the geometry. No MP context: nothing in
//            here may raise or allocate, and the only wait it takes is the
//            bounded one for the snapshot below.
//   end      (FEEDER, frame_end) clears `inflight`.
//   disarm   (VM) an overlay is about to paint the root: the arm is dropped
//            and the caller performs the SKIPPED composite into the back
//            buffer, so the overlay lands on a current picture.
//
// THE FENCE is what makes re-arming safe while a folded frame is still on the
// wire. `src` is read only during SYNTHESIS, which the feeder finishes before
// the last band ships, so `moy_fold_fence` waits for the feed to complete
// (`bnc_next == bnc_total`) and NOT for the transfer -- two volatile reads on
// the ordinary cadence. The caller must fence before it overwrites the
// snapshot; that is the one rule outside this file, and the whole reason the
// canvas snapshots into a flush-private scratch rather than handing over the
// live game canvas, which the next cart frame would draw over mid-band.
//
// THE SNAPSHOT IS THE DMA ENGINE'S (2026-09-08). Measured on glass, that
// snapshot was the whole of `cmp`: a 153,600 B PSRAM->PSRAM copy costs the
// VM core ~5.1 ms on either S3 through the write-allocate cache (a 32 KB p8
// canvas ~1.1 ms), the fence beside it 0.03 ms. The GDMA engine moves the same
// bytes in ~3.4 ms with the CPU free, and started at `blit_game` it has ALWAYS
// landed before the cart's next tick (residual wait 0.02 ms, every frame,
// both boards) -- the bar, the stack walk, the kick and the loop head are
// longer than the copy. So `moy_fold_arm_snap` takes the LIVE canvas and the
// scratch, starts the copy, and arms over the scratch; the copy is a plain
// memcpy when the engine declines (alignment, size, a refusal), so the arm's
// caller never sees the difference. Folding the live canvas instead, with a
// fence before the cart's first write, was measured and DECLINED: that wait is
// bounded by the FEED (8.1 ms after kick on the Guition's game window, 13.1 ms
// on the T-Deck's full frame) where the DMA's is bounded by 3.4 ms, and it
// would have cost a p8 cart on the T-Deck ~5 ms a frame against the 1.1 ms it
// paid.
//
// The copy adds two waits, both on the `copying` latch and both bounded by
// the flush deadline:
//
//   feeder   `moy_fold_band*` waits for the snapshot before its first read of
//            `src`; every later band finds the latch clear (one volatile
//            read). The transport idles for the residue, which is core-0 time
//            and wire time, never the VM's.
//   VM       `moy_fold_snap_fence` waits before the cart's next write of the
//            LIVE canvas -- the sys canvas takes it in `sync_back`, which both
//            boards run before every Player tick. That is the SECOND rule
//            outside this file, and it is what keeps "a cart's next write
//            never races a band read" true when the snapshot is asynchronous.
//
// `moy_fold_fence`, `disarm`, `composite` and `reset` all wait the copy out
// themselves, so a scratch is never overwritten, composited or freed under a
// DMA. A copy that never completes trips the deadline ONCE: it is counted,
// the latch is cleared, and the engine is retired for the session -- every
// later snapshot is a memcpy, which is the shape this file shipped with.
//
// A COMPILED CART'S FRAME folds too, without ever reaching a canvas. libmoy's
// wasm binding can hand the host a frame where the cart made it, in its own
// linear memory (moy_wasm.h's `frame`): blit's index bytes with 256 colours,
// or blit565's little-endian RGB565. `moy_fold_arm_frame` snapshots that frame
// by the same DMA engine into a scratch and latches its FORMAT, and the band
// synthesis resolves the palette or swaps the bytes as it fills each bounce
// slot -- on the feeder, off the cart's core. The copy the blit used to make
// on the VM core, frame into canvas, is gone; what is left is a DMA the VM
// never waits for, because the snapshot's two fences above are exactly the
// ones a frame needs: the feeder's first band waits for it, and the sys
// canvas's `sync_back` fences it before the cart's next hook can write its
// memory. The whole frame is copied, cropped or not, so the scratch holds a
// copy of what the cart blitted that the binding can write into the screen
// later (moy_wasm_presented's `kept`). The GDMA wants 64-aligned ends and a
// cart's frame sits wherever its allocator put it, so the copy is the
// 64-aligned span around it, and the frame starts a few bytes into the
// scratch; a palette frame's colours ride at the scratch's tail.

#ifndef MOYBYTE_MOY_FOLD_H
#define MOYBYTE_MOY_FOLD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// The per-band axis map `moy_fold_band_rot` builds on its stack. Both boards
// run 32-row bands; a board that raises that past this asserts at compile time
// next to its own BAND_ROWS rather than overflowing here.
#define MOY_FOLD_MAX_BAND_ROWS 64

// A sane ceiling on the integer upscale. The console's scale comes from
// `min(sys_w // game_w, sys_h // game_h)` and is 1 or 2 on every shipped
// board; the bound exists so the geometry arithmetic below cannot overflow on
// a caller that hands over nonsense.
#define MOY_FOLD_MAX_SCALE 64

// What the GDMA engine demands of a PSRAM copy on the S3: address AND length
// a multiple of this (measured on Guition glass 2026-09-08 -- a 32-byte offset
// is refused, a 64-byte one taken; `moy_alloc` hands out 64-aligned buffers
// for exactly this reason). A snapshot that fails the rule is a memcpy, and
// the engine is never asked -- its refusal is an error log per frame.
#define MOY_FOLD_SNAP_ALIGN 64

// The layout of the fold's source. WIRE is a console canvas: RGB565 in the
// panel's byte order, what a game-canvas snapshot holds. The other two are a
// compiled cart's frame as the cart handed it over: LE565 is blit565's
// little-endian RGB565, byte-swapped into wire order as it is synthesized, and
// IDX8 is blit's index bytes, resolved through 256 wire-order colours.
#define MOY_FOLD_WIRE  0
#define MOY_FOLD_LE565 1
#define MOY_FOLD_IDX8  2

// A frame scratch's tail holds a palette frame's colours, then the patches.
#define MOY_FOLD_LUT_BYTES 512

// PATCHES: what the console draws over a compiled cart's frame -- the FPS
// chip and the perf HUD line, opaque rects the painter declares -- come from
// the game canvas, the rest of the frame from the cart's. At most this many
// rects and this many bytes of them, copied into the scratch at the arm; a
// frame with more is settled into the canvas instead.
#define MOY_FOLD_MAX_PATCHES 4
#define MOY_FOLD_PATCH_BYTES 8192

typedef struct {
    int x, y, w, h;             // in the frame's own pixels
    const uint16_t *pix;        // w x h wire pixels, in the scratch
} moy_fold_patch_t;

typedef struct {
    bool armed;                 // this frame's composite is the flush's job
    volatile bool inflight;     // the in-flight flush still reads `src`
    const uint8_t *src;         // game pixels, `fmt` layout, vh rows of sstride
    int vw, vh;                 // the game rectangle, in GAME pixels
    int sx, sstride;            // its x offset and row stride inside `src`, in pixels
    int fmt;                    // MOY_FOLD_WIRE / LE565 / IDX8
    const uint16_t *lut;        // IDX8: the 256 wire colours
    int sy;                     // a frame's view top, in the frame's rows
    int npatch;                 // a frame's patches (0 on every other arm)
    moy_fold_patch_t patch[MOY_FOLD_MAX_PATCHES];
    int ox, oy;                 // its origin in the LOGICAL frame
    int scale;                  // integer upscale, >= 1
    uint32_t frames;            // flushes folded since boot (the liveness meter)
    // THE SNAPSHOT. `copying` is the DMA in flight: `src` is being written and
    // the live canvas read, and both readers above wait on it.
    volatile bool copying;
    uint32_t snaps;             // snapshots the DMA engine took
    uint32_t snaps_sync;        // ...and the memcpys taken when it declined
    uint32_t snap_timeouts;     // copies that never completed (retires the engine)
    uint32_t snap_wait_us;      // what the last VM-side snap fence waited
    uint32_t frame_arms;        // a compiled cart's frames armed (the lever's meter)
} moy_fold_t;

extern moy_fold_t moy_fold;

// The rotated panel's mapping from the LOGICAL landscape frame L[ly][lx] to
// the PHYSICAL panel P[py][px], plus the physical rect this flush ships:
//   rot 0:  P[py][px] = L[px][panel_h-1-py]
//   rot 1:  P[py][px] = L[panel_w-1-px][py]
// (moy_axs's LANDSCAPE block is where those two came from and why.)
typedef struct {
    int panel_w, panel_h;       // PHYSICAL panel size
    int rot;                    // 0 or 1
    int win_x, win_y, win_w;    // the physical rect being shipped
} moy_fold_rot_t;

// VM side. Register THIS frame's composite as the flush's job over a snapshot
// the caller already holds: `src` is `vw` x `vh` contiguous. `len` is the
// source buffer's byte length and `fb_w`/`fb_h` the LOGICAL frame the geometry
// must fit inside. FALSE = declined (the caller composites itself); nothing is
// latched on a decline, so a live arm from an earlier call is left alone.
bool moy_fold_arm(const uint8_t *src, size_t len, int vw, int vh,
                  int ox, int oy, int scale, int fb_w, int fb_h);

// VM side. Snapshot the LIVE canvas and arm over the snapshot. The rectangle
// is `vw` x `vh` at column `sx` of rows `live_off/(sstride*2)` onward, `sstride`
// pixels wide; the copy is the whole row range (`vh` x `sstride`, one
// contiguous run, the only shape a DMA takes) into `scratch`, which becomes
// `src`. `*async_out` says whether the copy is a DMA in flight (fence with
// `moy_fold_snap_fence` before `live` is written again) or landed here.
// FALSE = the geometry was refused; nothing was copied or latched. The caller
// must have fenced (`moy_fold_fence`): the previous flush is done reading
// `scratch` and the previous snapshot has landed.
bool moy_fold_arm_snap(const uint8_t *live, size_t live_len, size_t live_off,
                       uint8_t *scratch, size_t scratch_len,
                       int vw, int vh, int sx, int sstride,
                       int ox, int oy, int scale, int fb_w, int fb_h,
                       bool *async_out);

// VM side. Snapshot a compiled cart's frame and arm over the snapshot: the
// frame is `gw` x `gh` pixels of `fmt` (LE565 or IDX8, with `lut` its 256 wire
// colours) at `frame`, and the rectangle shown is `vw` x `vh` at (`sx`, `sy`)
// of it. The whole frame is copied -- its 64-aligned span -- into `scratch`;
// `*kept_off` is where the frame's first byte landed in it, `*async_out`
// whether the copy is a DMA in flight. `rects` holds `nrects` (x, y, w, h)
// quads in the frame's pixels whose content comes from `canvas` instead -- the
// game canvas, wire order, gw pixels a row -- copied into the scratch now. The
// scratch holds the span, MOY_FOLD_LUT_BYTES and the patches' pixels; a
// `frame` that already sits where the copy would land in it (the last frame
// shown, shown again) is not copied. FALSE = refused, and nothing was copied
// or latched. The caller has fenced
// (`moy_fold_fence`), as for `moy_fold_arm_snap`.
bool moy_fold_arm_frame(const uint8_t *frame, size_t frame_len, int fmt,
                        const uint16_t *lut, uint8_t *scratch,
                        size_t scratch_len, int gw, int gh, int sx, int sy,
                        int vw, int vh, int ox, int oy, int scale, int fb_w,
                        int fb_h, const int16_t *rects, int nrects,
                        const uint8_t *canvas, size_t canvas_len,
                        size_t *kept_off, bool *async_out);

// VM side, with the FEEDER IDLE (drain first). Latch the one-shot arm into the
// frame about to be kicked and count it. True = this flush is folded.
bool moy_fold_consume(void);

// FEEDER side, from frame_end: the flush is done reading `src`.
void moy_fold_end(void);

// VM side. Drop a live arm; TRUE if there was one, which is the caller's cue
// to perform the skipped composite (`moy_fold_composite`) into its back buffer.
// Waits the snapshot out first: the composite reads it.
bool moy_fold_disarm(void);

// VM side. Block until no in-flight flush still reads `src` and no snapshot is
// still writing it. Releases the GIL while it spins and is bounded by the
// flush deadline; a few compares on the ordinary cadence.
void moy_fold_fence(void);

// VM side. Block until the snapshot in flight has finished reading the live
// canvas -- the fence the cart's next canvas write takes. GIL released,
// bounded; one compare when nothing is in flight.
void moy_fold_snap_fence(void);

// Clear the latches (soft reset / free-all), waiting any snapshot out first
// (its buffers are about to be freed). `frames` and the snapshot counters are
// since-boot meters and deliberately survive.
void moy_fold_reset(void);

// The composite the fold SKIPPED: black, plus the game rectangle at scale,
// into a `fb_w` x `fb_h` wire-order RGB565 frame, whatever the source's layout. Used by the disarm path on both boards,
// and it is the reference the band synthesis below must reassemble into.
void moy_fold_composite(uint8_t *fb, int fb_w, int fb_h);

// FEEDER side. Synthesize logical rows [y, y+rows) of a `dst_w`-wide frame into
// a bounce slot. The straight-through form (moy_lcd): one destination row per
// logical row, contiguous.
void moy_fold_band(uint8_t *slot, int dst_w, int y, int rows);

// FEEDER side. The rotated form (moy_axs): physical rows [py0, py0+rows) of the
// window `geom` describes, gathered out of the logical game rectangle. `rows`
// must be <= MOY_FOLD_MAX_BAND_ROWS.
void moy_fold_band_rot(uint8_t *slot, const moy_fold_rot_t *geom, int py0,
                       int rows);

#endif // MOYBYTE_MOY_FOLD_H
