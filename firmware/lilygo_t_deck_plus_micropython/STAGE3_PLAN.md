# KidCode Native Graphics Core — Stage 3 Plan (production compositor)

Date: 2026-06-23
Status: Stage 2 gate PASSED (see SPIKE_FINAL.md "Stage 2 gate result"). This plan
covers Stage 3: the production native compositor that the v0.4 Living Desktop
runs on. **Stage 3 v1 is implemented and validated on device** (see "Device
validation" below).

## Device validation (2026-06-23)

`modules/kc_compositor.py` runs on the T-Deck via the `RUN_COMPOSITOR_SMOKE`
path. Dirty-rect flush: ~400 FPS, `flush_ms` 0–1 ms — confirmed clean. Full-screen
flush took several iterations:

- **Bug:** every MicroPython-level `tx_color` call glitches a few rows at its
  command→data boundary, so a full flush built from 6 strip transfers banded
  (stale rows of the previous frame at each strip seam, non-deterministic). The
  Stage 2 bench masked it by repainting every frame; the static red→blue→hold
  test exposed it (frozen bands on a static screen).
- **What didn't fully fix it:** one CASET/RASET window + RAMWR-continue (`0x3C`)
  removed the stale bottom strip and shrank the seams, but each transfer boundary
  still dropped a few rows.
- **Fix (validated):** flush the whole screen in **one** `tx_color` from a
  full-frame **PSRAM** DMA buffer (the S3 can DMA from PSRAM). esp_lcd splits the
  data internally with no repeated command byte, so it's seamless — the same
  reason the 128×128 `kc_canvas` blit was always clean. Small dirty rects still
  use the internal-SRAM strip (1–2 transfers, no visible seam). The static blue
  hold is now dead solid.

Takeaway for the production compositor: **never paint a region with many small
back-to-back `tx_color` calls** — use one transfer from a DMA buffer sized to the
region (PSRAM for full-frame, internal SRAM for small rects).

## Connect-the-ends (2026-06-23): a .kcart runs on device

`modules/kid_runtime.py` runs an actual v0.4 cartridge on the T-Deck: a
`framebuf`-backed `DeviceCanvas` over the compositor buffer + a palette→RGB565
LUT implements the same kid API (`cls/pset/rect/rectfill/circ/spr/text/...`), so
the **same** `_init/_update/_draw` cartridge source that runs in the host
`runtime/` simulator runs on the panel (v1 embeds the Space Desktop source;
loading real `.kcart` files from SD is the follow-on). Behind `RUN_KIDCART`.

Measured (Space Desktop, full-screen repaint every frame, 40 MHz):
**fps=23, draw_ms=11, flush_ms=33** — clean background, no banding. A static
wallpaper + small dirty-rect widgets would hit 60+; 80 MHz roughly halves the
flush. The host and device now share one cartridge + one drawing API; only the
canvas backend differs (`runtime/canvas.py` ↔ `kid_runtime.DeviceCanvas`).

## What Stage 2 settled (the inputs to this plan)

| surface | 40 MHz flush / fps | 80 MHz flush / fps |
|---|---|---|
| full-redraw 320×240 | 41 ms / 21 | 29 ms / 28 |
| partial band 320×64 | 11 ms / 73 | 8 ms / 91 |

1. **Flush is bus-bound and linear in rows pushed.** Full-screen full-redraw can
   never hit 60 FPS on this panel → **the desktop must render via dirty-rect /
   partial updates**, never a full-frame repaint.
2. **80 MHz is stable** and used for the full-screen path; gains are sub-linear
   because ~13 ms of per-frame Python copy/`tx_param` overhead doesn't scale with
   clock. → **Move the per-pixel + pack work into C.**
3. **No tearing-effect (TE) pin on the T-Deck.** TulipCC drives the same ST7789
   with only the `on_color_trans_done` async-DMA callback, no TE GPIO. → We
   **cannot** hardware-sync to vblank. Tearing is controlled by keeping each flush
   **smaller than one refresh (~16 ms)** — which dirty-rect already achieves
   (8–11 ms for a 320×64 band) — plus **double-buffering** so we never DMA a
   half-drawn frame. Full-screen animation would still tear; the desktop avoids it
   by construction (static wallpaper + small animated regions).

## Architecture

```
ESP32-S3 / FreeRTOS
  └─ Native KidKernel (C)
       ├─ kc_gfx       : VM-neutral pixel kernel (fill / fill_rect / blit / pack)
       │                 operates on RGB565 buffers; no MicroPython/LVGL/Lua deps
       └─ lcd_bus      : existing SPI DMA panel path (tx_param / tx_color)
            └─ kc_compositor (Python today): owns the framebuffer + dirty regions,
               draws via kc_gfx, flushes only dirty bands over lcd_bus DMA
                 └─ userland VM (MicroPython now; Lua later) calls the same
                    language-neutral surface: kid.clear/rect/spr/text → kc_gfx
```

The strategic point (v0.4): **the pixel kernel is C and VM-neutral.** `kc_gfx` has
zero dependency on `framebuf`, LVGL, or the MicroPython object model in its hot
path — it takes a buffer + ints. The same C is callable from a future Lua binding.
That is the "one native kernel, interchangeable VM" the v0.4 plan requires.

## Scope: Stage 3 v1 (this repo) vs Stage 3.1 (later)

**v1 (implemented here, compiles, needs device validation):**
- `native/kc_gfx/` C module: `fill`, `fill_rect`, `blit565` (with transparent
  color key), `pack_strip` (full-width fast path + arbitrary-x per-row packing in
  C — this replaces the pathologically slow Stage 2 Python per-row loop).
- `kc_compositor.py` rewritten: owns an RGB565 framebuffer + a **dirty-region
  tracker**, draws through `kc_gfx` (falls back to `framebuf`/pure-Python when the
  C module isn't present), and **flushes only the dirty region** over the proven
  `lcd_bus` DMA path. Optional **double-buffer** (front/back) to avoid
  drawing-while-flushing.
- Python still orchestrates the flush loop (issues `tx_param`/`tx_color`).

**3.1 (follow-on, after v1 is validated on device):**
- Move the whole flush loop into C (or a FreeRTOS display task) so userland never
  blocks on DMA; use the async `on_color_trans_done` callback for double-buffered
  overlap (draw frame N+1 while DMA-ing N).
- Native font blitter in `kc_gfx` (8×12 / 6×8 / 12×16, per TulipCC) to replace
  `framebuf.text`'s 8×8 chrome.
- RGB332 + 256-entry LUT framebuffer **only if** PSRAM footprint becomes the
  pressure point (halves framebuffer RAM, not the SPI transfer — deferred).
- Lua bindings to `kc_gfx` once the userland decision lands.

## kc_gfx C API (v1)

All ops take a writable RGB565 buffer (`bytearray`/`memoryview`) + ints, are fully
bounds-clamped (a bad arg clips, never overruns), and return `None`.

```
kc_gfx.fill(buf, npix, color565)
kc_gfx.fill_rect(buf, stride_px, x, y, w, h, color565)
kc_gfx.blit565(dst, dst_w, dst_h, dx, dy, src, src_w, src_h, key)   # key=-1: opaque
kc_gfx.pack_strip(fb, fb_w, x, y, w, rows, dst)                      # fb window -> dst (contiguous)
```

`pack_strip` is the flush packer: it copies a `w×rows` window of the framebuffer
into the contiguous DMA strip buffer. Full-width (`x==0, w==fb_w`) is one memcpy;
cropped rects are packed row-by-row **in C** (the Stage 2 Python version of this
was ~78 ms for a 64×64 region — the bug behind the "weird squares"; in C it's
microseconds).

## Dirty-region model (v1)

A **single union bounding box** of everything drawn since the last flush
(`DirtyTracker`: pure-Python, host-testable). Drawing ops grow the box;
`flush()` packs+DMAs only that box (clamped to the screen), then clears it. This is
the simplest model that gets the desktop win: a static wallpaper + a moving 32×32
pet flushes ~a 40×40 box at >60 FPS instead of the whole 320×240 frame.

Limitation: two far-apart small changes union into one big box. v1 accepts this;
3.1 can keep a short list of disjoint rects if it matters. The box is also clipped
to whole DMA strips for the transfer.

## Flush path (v1)

```
for each (y, rows) band of the dirty box (<= strip height):
    kc_gfx.pack_strip(fb, fb_w, x, y, w, rows, strip_dma)   # C
    bus.tx_param(CASET, ...); bus.tx_param(RASET, ...)
    bus.tx_color(RAMWR, strip_dma[:n], x, y, x+w-1, y+rows-1, 0, True)  # proven DMA
```

Same `lcd_bus` calls Stage 1/2 proved; only the *packing* moved to C and the
*region* shrank to the dirty box. Double-buffer mode draws into the back buffer
and packs from it, so a flush never reads a half-updated frame.

## Build integration

`native/kc_gfx/` mirrors `native/kc_alloc/` (`modkc_gfx.c` + `micropython.cmake`).
`build.sh` stages it into `ext_mod/kc_gfx` and injects
`include(.../kc_gfx/micropython.cmake)` after the `kc_alloc` include — same
re-stage-every-build pattern (the upstream `ext_mod` is wiped on re-clone).

## Migration from the Stage 2 spike

- `kc_compositor.py` (Stage 2 strip blitter + benchmark helper) is rewritten into
  the dirty-rect compositor; `plan_strips()` and the host `make_compositor(None)`
  guard are kept. The fullscreen benchmark in `kidcode_shell.py`
  (`RUN_FULLSCREEN_BENCH`, default off) stays as a regression/A-B tool.
- `kc_canvas.py` (Stage 1 128×128 blitter) stays for the game path.
- The renderer/desktop will target `kc_compositor` at full screen instead of the
  128×128 canvas once the desktop shell is built (separate task).

## Device-test checklist (when validating v1 on hardware)

1. Build with `KIDCODE_SKIP_VFS_BOOT=1`; confirm `kc_gfx` links (no build error).
2. A smoke path that: allocates a `Compositor(320,240)`, `clear(blue)`, draws a
   moving block via `fill_rect`/`blit565`, `flush()` each frame.
3. Expect: only the dirty box transfers → ≥60 FPS for a small moving region; no
   "weird squares" (the C `pack_strip` fixes the cropped-rect path); colors
   correct; static areas stable (no stale content).
4. Compare A/B against the `RUN_FULLSCREEN_BENCH` numbers — dirty-box flush should
   match the band timing (~8 ms @80 MHz for a 320×64-equivalent area).
5. Double-buffer on: confirm no shearing of the moving block.

Acceptance: a small animated region sustains ≥60 FPS, cropped rects render
correctly, host tests green, the C module compiles in the ESP-IDF toolchain.

## Risks / unknowns (flagged for device validation)

- **kc_gfx compiles but is untested on hardware** (no device access at authoring
  time). The ops are simple, bounds-clamped, and mirror the proven kc_alloc module
  shape, but pixel output must be eyeballed on the panel.
- **Double-buffer without TE** reduces but cannot fully eliminate tearing for
  large/fast updates; the desktop must stay dirty-rect to keep flushes < refresh.
- **PSRAM framebuffer + double-buffer = 2×150 KB.** Fine on the 8 MB PSRAM, but
  confirm headroom alongside the MicroPython heap.
- **Union-bbox dirty model** can over-flush when changes are far apart; revisit in
  3.1 if a real desktop layout shows it.
