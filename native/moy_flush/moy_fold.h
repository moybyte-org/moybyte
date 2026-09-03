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
//            here may raise, allocate or block.
//   end      (FEEDER, frame_end) clears `inflight`.
//   disarm   (VM) an overlay is about to paint the root: the arm is dropped
//            and the caller performs the SKIPPED composite into the back
//            buffer, so the overlay lands on a current picture.
//
// THE FENCE is what makes re-arming safe while a folded frame is still on the
// wire. `src` is read only during SYNTHESIS, which the feeder finishes in the
// first few ms of a flush, so `moy_fold_fence` waits for the feed to complete
// (`bnc_next == bnc_total`) and NOT for the transfer -- two volatile reads on
// the ordinary cadence. The caller must fence before it overwrites the
// snapshot; that is the one rule outside this file, and the whole reason the
// canvas snapshots into a flush-private scratch rather than handing over the
// live game canvas, which the next cart frame would draw over mid-band.

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

typedef struct {
    bool armed;                 // this frame's composite is the flush's job
    volatile bool inflight;     // the in-flight flush still reads `src`
    const uint8_t *src;         // game pixels, RGB565 wire order, vw*vh
    int vw, vh;                 // the game rectangle, in GAME pixels
    int ox, oy;                 // its origin in the LOGICAL frame
    int scale;                  // integer upscale, >= 1
    uint32_t frames;            // flushes folded since boot (the liveness meter)
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

// VM side. Register THIS frame's composite as the flush's job. `len` is the
// source buffer's byte length and `fb_w`/`fb_h` the LOGICAL frame the geometry
// must fit inside. FALSE = declined (the caller composites itself); nothing is
// latched on a decline, so a live arm from an earlier call is left alone.
bool moy_fold_arm(const uint8_t *src, size_t len, int vw, int vh,
                  int ox, int oy, int scale, int fb_w, int fb_h);

// VM side, with the FEEDER IDLE (drain first). Latch the one-shot arm into the
// frame about to be kicked and count it. True = this flush is folded.
bool moy_fold_consume(void);

// FEEDER side, from frame_end: the flush is done reading `src`.
void moy_fold_end(void);

// VM side. Drop a live arm; TRUE if there was one, which is the caller's cue
// to perform the skipped composite (`moy_fold_composite`) into its back buffer.
bool moy_fold_disarm(void);

// VM side. Block until no in-flight flush still reads `src`. Releases the GIL
// while it spins and is bounded by the flush deadline; a two-compare no-op on
// the ordinary cadence.
void moy_fold_fence(void);

// Clear the latches (soft reset / free-all). `frames` is a since-boot meter and
// deliberately survives.
void moy_fold_reset(void);

// The composite the fold SKIPPED: black, plus the game rectangle at scale,
// into a `fb_w` x `fb_h` RGB565 frame. Used by the disarm path on both boards,
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
