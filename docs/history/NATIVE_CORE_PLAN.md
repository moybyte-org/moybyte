# Moybyte Native Graphics Core — Plan

Date: 2026-06-22
Status: Stage 1 complete (native DMA canvas blitter, 47→90 FPS at 128×128).
Stage 2 (this plan) is reordered: the **full-screen native compositor benchmark**
is now the next step and the gate, because the v0.4 desktop needs the full screen
and the full-screen frame rate is an open, bus-bound question.

**Update 2026-06-23:** Stage 2 gate **PASSED** (full-redraw 41/29 ms @40/80 MHz,
dirty band 11/8 ms → 73/91 FPS; see SPIKE_FINAL.md). Stage 3 is now drafted and
implemented in v1 — see **STAGE3_PLAN.md** (native `moy_gfx` C kernel +
dirty-rect `moy_compositor`).

## Context

The MicroPython T-Deck spike proved the architecture and hit one hard wall: the
LVGL `lv.canvas` flush was CPU-bound (~13 ms/frame) because LVGL software-rotates
every flush. Stage 1 bypassed that for the 128×128 game canvas with a native DMA
blitter (`modules/moy_canvas.py` + `native/moy_alloc/`), reaching ~90 FPS.

**The wall was native C, below both scripting VMs** — so the production graphics
path must be a **native core**, and it's needed for *any* userland (Lua or
MicroPython). This plan builds that core as **language-neutral KidKernel APIs**,
keeping MicroPython as the userland for now. The Lua-vs-MicroPython userland
decision is decoupled (perf-neutral, a product call) and stays open.

Two things to keep distinct (see SPIKE_FINAL.md / project memory):
- **lvgl_micropython** = the MicroPython *platform* we're built on (MicroPython +
  the `lcd_bus` panel driver + `framebuf` + the LVGL library). We keep this.
- **LVGL-the-GUI** (`lv.*` widgets, `lv.timer_handler`, the rotated flush) = the
  part that costs us. We drop this.

## Why the priority changed (was 2b, now first)

The v0.4 product (`moybyte_Console_Plan_v0_4.md`) is a **Living Desktop**, not a
128×128 game. A desktop needs the **full screen** (320×240 on the T-Deck panel;
480×270 logical later). That makes the old plan's order wrong:

- **Former Stage 2a** (drop LVGL chrome on the *128×128* layout) is mostly polish
  for the **game path** — and v0.4 demotes Tiny Runner to project #5. It is now
  **deferred** (see bottom). Its two genuinely-reusable pieces (generalize the
  blitter to arbitrary regions; kill the LVGL TaskHandler) are absorbed into the
  compositor work below.
- **Former Stage 2b** (full-screen native compositor) is the real **desktop
  foundation** and contains the one **unresolved unknown** that gates the whole
  v0.4-on-device vision: *can we flush the full screen fast enough?*

### The unknown, quantified

The full-frame SPI transfer is **bus-bound**, not CPU-bound, at full screen:

```
320 × 240 × 16 bit ÷ 40 MHz SPI ≈ 30.7 ms  →  ~32 FPS ceiling, full-frame redraw
```

This matches SPIKE_FINAL.md ("a 320×240×2 = 150 KB framebuffer … the DMA transfer
alone would be ~30 ms"). Two consequences that shape this stage:

1. **RGB332 does NOT lift this ceiling.** The ST7789 receives RGB565 on the wire
   regardless; TulipCC's RGB332→RGB565 LUT runs *before* the DMA, so it halves
   framebuffer RAM and the PSRAM read, **not** the SPI transfer. So RGB332 is a
   *memory* optimization, deferred. We benchmark **RGB565 first** to isolate the
   real variable (transfer time) with zero per-pixel conversion cost.
2. **The conversion/draw must stay in C.** A Python LUT or pixel loop over 76 800
   pixels would dwarf the 30 ms transfer. We draw with `framebuf` (C) into the
   RGB565 buffer and `memoryview`-slice the strips (C memcpy). No per-pixel Python.

The escapes from the 32 FPS ceiling — **80 MHz SPI** (bus-bound here, so it should
roughly halve transfer, unlike the 128×128 case where 80 MHz was a CPU-bound
no-op) and **dirty-rectangle partial updates** (a desktop is mostly static
wallpaper + small animated regions) — are exactly what the benchmark measures.

## Architecture target

```
ESP32-S3 / FreeRTOS
  └─ Native KidKernel (C): framebuffer, compositor, DMA blitter, font, input, audio…
       └─ Language-neutral APIs: moy_canvas_clear / moy_canvas_sprite / moy_canvas_text / …
            └─ Userland VM (MicroPython today; Lua pluggable later) calls the same APIs
```

MicroPython (Lua) `kid.spr()` → native `moy_canvas_sprite()`. The kernel is the
asset; the VM is interchangeable.

## Stage 2 — Full-screen native compositor benchmark (DO FIRST; this is the gate)

**Goal:** stand up a full-screen 320×240 native compositor and **measure the
flush frame rate**. The number decides whether the v0.4 desktop is viable on this
panel as-wired and which rendering model (full-redraw vs dirty-rect, 40 vs 80 MHz)
the desktop must use. This is a **spike/benchmark first**, not a polished build.

### 2.1 Prerequisite — native takeover (kill the LVGL GUI + TaskHandler)

`tdeck_display.init_display()` creates `task_handler.TaskHandler(duration=5)`,
which arms a `machine.Timer(PERIODIC, 5 ms)` that schedules `lv.task_handler()` in
the background (`task_handler.py:54-65,166-176,136`) — **independent of any
`lv.timer_handler()` in our loop**. It must be stopped, because:
- it burns CPU we're trying to measure, and
- once the compositor goes **async** (`register_callback`, §2.3), a background
  LVGL flush on the **same `lcd_bus`** is a real bus-contention/corruption risk.

So the compositor path calls `handler.deinit()` (`task_handler.py:90`) to stop the
timer and own the bus. The shell currently discards this handle as `_task_handler`
(`moybyte_shell.py:29`) — keep it and stop it. (This is the one piece of the old
2a that we still need.)

### 2.2 The benchmark (acceptance gate)

Build `_run_fullscreen_bench()` in `moybyte_shell.py`, run when a build flag is
set, that after native takeover:

1. **Full-redraw bench (~3 s):** animate a cheap full-screen scene (e.g. a moving
   gradient via `framebuf.fill_rect`), `compositor.flush()` each frame, print
   `f=… fps=… flush_ms=…`.
2. **Dirty-rect bench (~3 s):** static background + a small moving region,
   `compositor.flush_rect(x,y,w,h)` each frame, print `fps=…`.

Run the build at **40 MHz and 80 MHz** SPI (`tdeck_display.freq`) and record both.

**Acceptance (go/no-go):**
- Full-redraw FPS is measured at 40 and 80 MHz and matches the bus model
  (~30 ms / ~15 ms flush respectively, ± overhead). If it doesn't, the model is
  wrong and we learn something more important than the build.
- Dirty-rect FPS for a desktop-sized animated region (e.g. ≤ 64×64) is **≥ 30**,
  ideally ≥ 60 — i.e. a desktop with small live widgets is smooth.
- 80 MHz runs without visual corruption (panel/wiring stable at clock).
- `handler.deinit()` confirmed: no background flush contends with the compositor.

**The decision the gate produces:** the desktop's rendering model —
(a) full-redraw at 60 FPS is viable (only if 80 MHz gets us there), or
(b) desktop must be dirty-rect / partial-update (most likely), or
(c) drop logical resolution / revisit the panel. Everything in Stage 3 depends on
this answer, so it is cheap to get it first.

### 2.3 Compositor design (`modules/moy_compositor.py`)

- **Framebuffer:** `bytearray(320*240*2)` RGB565. On the `SPIRAM_OCT` build the GC
  heap is in PSRAM, so 150 KB is fine; this is the CPU draw target, wrapped by a
  `framebuf.FrameBuffer(..., RGB565)` for C-speed drawing.
- **Strip buffer(s):** DMA-capable **internal SRAM** via `moy_alloc.malloc_dma`
  (PSRAM is not DMA-safe on this bus — no bounce buffer). Default strip = 40 rows
  → 320×40×2 = 25.6 KB (less than Stage 1's proven 32 KB). `tx_color` byte-swaps
  in place, so we always blit from these scratch buffers, never the live fb
  (mirrors `moy_canvas.py:50-59`).
- **`flush()` (full):** for each strip, `memoryview`-slice the framebuffer rows
  into the strip buffer, set CASET/RASET for that row band (full width),
  `tx_color(RAMWR, strip, 0, y, w-1, y+rows-1, 0, True)` — the exact call shape
  Stage 1 already runs successfully, just per strip.
- **`flush_rect(x,y,w,h)`:** dirty-rect. Full-width rects are contiguous slices;
  arbitrary-x rects copy per-row sub-slices into the strip buffer, chunked by
  strip height. This is the desktop's real path.
- **Async (opt-in, `double_buffer=True`):** `register_callback` makes `tx_color`
  return immediately (vs busy-wait when no callback — confirmed in
  `modlcd_bus.c:191-194`); ping-pong two strip buffers, overlapping the next
  strip's memcpy with the current strip's DMA. **Marked experimental — must be
  validated on device** (ISR/sched dispatch of the SPI completion callback is not
  host-testable). The benchmark defaults to the **synchronous** path.
- **Host guard:** `make_compositor(bus)` returns `None` when `bus is None`, so the
  simulator/tests never touch `moy_alloc`/`framebuf` (mirrors `moy_canvas`).

### 2.4 Persistence spike (parallel, small)

The v0.4 MVP needs save/load of user cartridges, but only the
`MOYBYTE_SKIP_VFS` image boots (writable `/` unanswered — SPIKE_FINAL "Open").
**Workaround to validate first:** write+reload a file under `/sd/...` (the spike
already mounts and reads SD). A tiny "write a file to SD, reboot, read it back"
probe retires the persistence unknown cheaply, without solving the writable-VFS
boot. Independent of the compositor; can run in either order.

### 2.5 RGB332 (after the gate, only if RAM demands it)

Once the RGB565 numbers are in: if full-frame PSRAM footprint (150 KB) or the
PSRAM→strip read is the pressure point, add the 8-bit RGB332 framebuffer + 256-LUT
in C (TulipCC `tulip/shared/display.c:85-118`, `tulip/esp32s3/tdeck_display.c:159`).
This halves framebuffer RAM, **not** the SPI transfer (§"The unknown"). Defer
until measured.

## Stage 3 — production compositor → see STAGE3_PLAN.md

The gate passed, so Stage 3 is now its own document: **STAGE3_PLAN.md**.
Implemented in v1 (compiles in the ESP-IDF toolchain; device validation pending):
- `native/moy_gfx/` — VM-neutral C pixel kernel (`fill`, `fill_rect`, `blit565`,
  `pack_strip`); the C `pack_strip` also fixes the slow/garbled Stage 2 cropped-rect path.
- `modules/moy_compositor.py` — rewritten as a dirty-rect compositor (union-bbox
  tracker, draws via `moy_gfx` with `framebuf`/Python fallback, flushes only the
  dirty region over the proven `lcd_bus` DMA path).

Still to do (Stage 3.1 + desktop): native font blitter, full C / FreeRTOS flush
loop with async double-buffer, optional RGB332+LUT, Lua bindings, and the actual
Living Desktop shell on top. Anti-tearing strategy is **double-buffer + dirty-rect
< refresh** (the T-Deck has no TE pin — confirmed against TulipCC's driver).

## Deferred (not this plan)

- **Former Stage 2a — drop LVGL chrome on the 128×128 game layout.** Game-path
  polish; v0.4 demotes Tiny Runner to project #5. The bits worth keeping (kill the
  TaskHandler; generalize the blit to arbitrary regions) are pulled into Stage 2.
  Do the rest only if/when the 128×128 game surface needs it.
- **Userland VM choice (Lua vs MicroPython vs both):** perf-neutral product call.
  Language-neutral core keeps it reversible. Resolve once Stage 3's API surface is
  concrete (see `moybyte-strategy-and-open-decisions` memory).
- **Going bare-ESP-IDF (dropping lvgl_micropython entirely):** not needed to drop
  the LVGL *GUI*. TulipCC keeps MicroPython as a component on a native core —
  that's the model.

## Verification

- **Host:** `make test` (compositor is import-guarded; add a test for
  `plan_strips()` + the `None` guard), `make firmware-sim-lilygo-micropython`.
- **Device (the point of this stage):** build with the bench flag
  (`MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython`), flash, read
  serial: `Moybyte fullscreen bench fps=… flush_ms=…` for full-redraw and
  dirty-rect, at 40 and 80 MHz. Record both in SPIKE_FINAL.md as the go/no-go.
- **Regression:** the LVGL/fake-LVGL path still works on host (import-guarded);
  the normal (non-bench) app path is unchanged.
