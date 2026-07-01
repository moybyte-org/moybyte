# Moybyte MicroPython T-Deck Spike — Final Decision Record

Date: 2026-06-22
Status: Stage 1 complete. Graphics path proven. Runtime identity still an open product decision.

## Question the spike was chartered to answer

Can a MicroPython userland on the LilyGO T-Deck Plus (ESP32-S3 + ST7789) boot,
load kid projects, draw, poll input, and recover — at an acceptable frame rate —
so that MicroPython could be a viable cartridge/userland runtime for Moybyte?

## What was proven

MicroPython boots, draws, polls the T-Deck keyboard matrix, runs kid projects
(frozen and SD-loaded), and is **vindicated on performance**: the Python/draw
side of the frame loop is lean (~5–9 ms). The one performance wall that existed
was **not in MicroPython** — it was in native C, below both scripting VMs.

## The wall (and why it is runtime-independent)

Rendering into an `lv.canvas` and flushing via `lv.timer_handler()` cost
~13 ms/frame (`pump`), CPU-bound. Verified two ways:

1. **40 → 80 MHz SPI clock did nothing** to `pump` — if it were bus-bound the
   clock would have halved it.
2. **Root cause in source:** `display.set_rotation(_270)` forces LVGL's upstream
   `lv_draw_sw_rotate` over a PSRAM-backed buffer on every flush. That per-pixel
   software rotation scales linearly with pixels, projecting to **<10 FPS at the
   320×240 production surface**.

Because the wall is native C (LVGL's rotation), a **Lua** userland calling the
same flush would hit the identical ceiling. The graphics pipeline and the
scripting language are decoupled by this evidence.

## The fix — native DMA canvas blitter (Stage 1)

`modules/moy_canvas.py` (`Blitter`) drives `lcd_bus.SPIBus`'s already-exposed
`tx_param` (CASET/RASET) and `tx_color` (RAMWR DMA) directly, blitting the
128×128 RGB565 framebuffer to the panel and **bypassing LVGL's flush entirely**.
LVGL is retained only for chrome (the title/status label); the `lv.canvas` is
never invalidated in native mode, so the two never contend for the panel.

The DMA-capable transfer buffer comes from a tiny C user module
`native/moy_alloc/` (`malloc_dma` → `MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL`).
`lcd_bus.allocate_framebuffer` could not be used: it is hard-capped at 2 slots,
both already consumed by the LVGL ST7789 driver's draw buffers. `moy_alloc` is
staged into the upstream `ext_mod/` tree by `build.sh` (cp + sed-include), the
same pattern used for the early-board-init patch.

## Result

| Metric | LVGL canvas path | Native DMA blitter |
|---|---|---|
| FPS (uncapped) | ~47 | **~90** |
| `pump` (LVGL flush) | ~13 ms | 0 (LVGL does only the title label) |
| Flush cost | CPU-bound software rotation (~13 ms) | DMA transfer, ~6 ms (now in `drw`) |
| Colors / geometry | correct | correct |

At 128×128 the flush is no longer purely bus-bound — 80 MHz SPI is a no-op here,
so the remaining ~6 ms is clock-independent overhead (the Python slice-copy into
the DMA buffer + per-call `tx_param`/`tx_color` overhead), not the SPI transfer.
The frame rate is capped at ~60 FPS for normal device use (`target_frame_ms=16`);
uncapped it sustains ~90.

## Incidental fix

A silent build bug was found and fixed: `.py` edits to frozen modules were not
re-freezing (Ninja rests custom commands on identical manifest content, and
`build.sh` rewrote an identical manifest each run). This had silently
invalidated the earlier 80 MHz and byte-swap tests. Fixed by embedding an md5
fingerprint of the frozen sources in the manifest (`build.sh`), so any `.py`
edit now reliably re-freezes.

## What is decided vs open

- **Decided (by the data):** the production graphics pipeline must be **native**
  (DMA blitter), independent of the scripting runtime. This is the shared native
  graphics kernel for v0.4.
- **Open (product call, only the user can make):** runtime identity — Lua-first
  (v0.4's strategic lock) vs MicroPython-first vs hybrid (one native kernel +
  both as first-class runtimes). Performance is no longer a differentiator.
- **Open (technical gate for MicroPython-primary):** only the `MOYBYTE_SKIP_VFS`
  image boots; it disables MicroPython's writable `/` filesystem. Whether the
  normal writable-VFS boot can show a screen is unanswered.

## Stage 2 gate result (2026-06-23): full-screen compositor — PASS

Built a full-screen 320x240 RGB565 compositor (`modules/moy_compositor.py`,
strip-blit over the `lcd_bus` DMA path) + an on-device benchmark
(`RUN_FULLSCREEN_BENCH` in `moybyte_shell.py`) to answer the one open question:
can the full screen flush fast enough for the v0.4 desktop? Measured flush time:

| surface | 40 MHz flush / fps | 80 MHz flush / fps |
|---|---|---|
| full-redraw 320x240 | 41 ms / 21 | 29 ms / 28 |
| partial band 320x64 | 11 ms / 73 | 8 ms / 91 |

- **Bus-bound and linear in rows pushed** (64/240 x 41 = 11 ms, measured 11).
  Full-screen full-redraw cannot reach 60 FPS even at 80 MHz.
- **Partial/dirty-rect updates are the answer:** a 320x64 band is 73 FPS @40 MHz,
  91 @80 MHz. A desktop (static wallpaper + small animated regions) lives here,
  so 60+ FPS is achievable on this panel.
- **80 MHz is stable** on this unit (no corruption, only tearing). Gains are
  sub-linear: the Python slice-copy (PSRAM->internal SRAM) + per-strip
  `tx_param` overhead (~13 ms/full frame) doesn't scale with clock -- a C
  compositor cuts this.
- **Tearing** (large moving color bands) is clock-independent: flush > ~16 ms
  panel refresh and there is no tearing-effect (TE) sync. Cosmetic; fixed in
  Stage 3 by TE sync / double-buffering, not a clock bump.

Decisions locked for Stage 3 (the production C compositor):
- Desktop renders via native compositor + **dirty-rect/partial updates**, never
  a full-frame redraw.
- Adopt **80 MHz** for the full-screen path (validated stable); 40 MHz stays fine
  for the 128x128 game path (CPU-bound there, 80 MHz was a no-op).
- Move the strip pack + RGB332 LUT + flush into **C**; add **TE sync /
  double-buffering** to kill tearing.

## Next milestones

- **Stage 1.5 (optional, low value at 128×128):** eliminate the per-frame
  slice-copy by drawing straight into the DMA buffer (the in-place byte-swap by
  `tx_color` is harmless because `clear()` rewrites the buffer every frame).
- **Stage 2 (the real production milestone):** full 320×240 native compositor —
  move all chrome off LVGL onto the native framebuffer; flush in horizontal
  strips (a 320×240×2 = 150 KB framebuffer does not fit internal DMA SRAM, and
  the DMA transfer alone would be ~30 ms); add callback-driven async DMA
  (`register_callback` + double-buffering) to overlap CPU draw with transfer.
  This is where the runtime-independent native kernel becomes production-ready.

## Salvage from the spike

The `from moybyte import *` API surface, the host simulator, the T-Deck pin map
+ flash tooling, the benchmark harness, and the native framebuffer-draw embryo
all carry forward into the v0.4 native kernel and the PC simulator (v0.4 Task
Group A). Nothing is discarded.
