# Performance: the native gap and the lever roadmap (v1)

Synthesis of the 2026-07-09 performance investigation (the ESP32-P4 bring-up plus
the "are we behind a NES?" thread). This doc is the **reasoning + the lever
roadmap**; the living per-cart NUMBERS stay in the issues — **#66** (T-Deck /
ESP32-S3 ledger) and **#58** (P4 port status). Grounding predecessors:
`docs/history/perf_60fps_architecture.md` (how consoles hit 60fps),
`docs/history/spi_flush_80mhz.md`, `docs/history/fast_by_default_drawing.md`.

## 1. The headline

A NES emulator (Anemoia-ESP32) hits **60fps on the plain ESP32** — 240MHz, **no
PSRAM at all**, 384KB used. That's a *weaker* chip than the T-Deck's S3 and a
small fraction of the P4. So the gap between our carts and 60fps is **technique,
not hardware.** Every lever below is about shedding a tax we impose on ourselves;
none of them is "draw sprites faster" (our sprite blitter is already
memory-bandwidth-fast — measured 64× 16×16 sprites in ~0.7ms native).

## 2. The frame-budget model — where a cart's frame goes

60fps = 16.7ms/frame. A cart's frame splits into, roughly:

| slice | T-Deck (S3, SPI panel) | P4 (DSI, windowed) |
|---|---|---|
| game **logic** (kid code, MicroPython) | ~a few ms | ~2ms (measured) |
| **render** (moy_gfx native blits) | ~5–10ms | ~5ms (measured) |
| **composite** (320×240 → window upscale) | none (canvas IS the screen) | ~5ms PPA / ~13ms CPU |
| **flush** (push pixels to panel) | SPI DMA (~15ms @80MHz, overlappable) | ~0 (DPI scans PSRAM continuously) |
| **overhead** (bar, loop, input, GC) | ~3–4ms | ~3–4ms |

Two consequences: (a) the **kid's own drawing is a small slice** — most of the
budget is fixed tax; (b) the two boards are bottlenecked on *different* things —
the T-Deck on the SPI flush + interpreter, the P4 on the composite + interpreter.

## 3. Why we trail native emulators (Quake, NES)

Native emulators do *far* more per-frame pixel work than our carts and still run,
because they pay none of the taxes we do:

1. **The interpreter.** The kid's logic runs on MicroPython — dynamic typing,
   heap-boxed ints/floats, per-bytecode dispatch. An emulator's "interpretation"
   (of 6502 code) is native-C-tight (~10× cheaper per op), and its game logic is
   compiled machine code. **This is our single biggest tax.**
2. **The upscale composite (P4).** We render at a fixed 320×240 (so the same cart
   runs on the T-Deck) then scale it into a desktop window. Emulators render at
   their target resolution *directly into the scan-out buffer* — no composite.
3. **The windowed desktop.** We composite the game into a window with a bar and
   wallpaper; emulators are fullscreen and skip all of it.
4. **The rendering MODEL (structural).** Our API is *imperative clear-and-redraw*:
   `cls()` writes all 150KB, `map()` overwrites most of it, sprites overwrite
   again — **overdraw**, every pixel written 2–3×/frame. The NES PPU is
   *retained*: the background lives in the nametable and only changes when the
   game changes it; each pixel is computed once via priority — **zero overdraw.**
   No blitter tuning removes overdraw; it's baked into the model.

Not all "C draw" is equal, either: the emulator's PPU is a **fused,
IRAM-resident, scanline** routine; our `moy_gfx` is a **general, flash-resident,
per-verb** blitter (batched, but not fused, and with zero `IRAM_ATTR`).

## 4. The PPA verdict (P4) — a scale engine, not a GPU

Measured on glass:

| PPA op | vs CPU |
|---|---|
| game→window **upscale** composite (320×240→640×480) | **2.6× faster** (4.98 vs 12.95ms) — small source read + hardware scale. **Shipped.** |
| single **sprite** blit (1:1, 16×16) | 8× *slower*; never crosses over (1.2× at 256×256) |
| 64 sprites queued non-blocking (batch) | 4.57ms vs 0.70ms CPU — **~10× slower than `spr_batch`** |
| full-screen 1:1 **copy** (backdrop restore) | ~identical (~26ms both) — PSRAM-bandwidth-bound vs the DSI scan-out |

So there is **no NES-PPU-style hardware sprite path** on the P4. The PPA wins only
where there's real *scale* arithmetic to offload; `spr_batch` stays the sprite
path; 1:1 copies stay CPU. Recorded so nobody re-explores it.

## 5. The build is already well-tuned (both boards)

The generic ESP-IDF speed-guide knobs are mostly already set:

| knob | T-Deck (S3) | P4 |
|---|---|---|
| flash mode | QIO ✅ + 120MHz ✅ | QIO ✅ |
| compiler | `-O2` (PERF) ✅ | `-O2` ✅ |
| PSRAM | OCT 120MHz ✅ (the owner's bump) | HEX 200MHz ✅ |
| caches | I 32KB + D 64KB — **max** ✅ | L2 128KB (not max) |

This is *why* the T-Deck's PSRAM bump only "helped a bit" — it was one of the
last generic knobs. The T-Deck is near its architectural ceiling on these; the
P4 has more headroom (L2 cache, and the working-set-in-SRAM idea below).

## 6. The lever roadmap

API-preserving = the kid writes the same `.moy` cart. **Payoff** and **effort**
are estimates; anything not "shipped/reverted" needs on-glass measurement.

### Shipped this session (P4)

| lever | payoff |
|---|---|
| quiet-frame partial repaint (`WindowedWM.draw_stack`) | 7→21fps |
| PPA game composite (`blit_game`) | 35→51fps |
| async-composite overlap (defer show past input poll) | +2–5fps (→56) |
| retained backdrop cache (`_BackdropLayer`) | app-drags 8→14fps |

### Open — API-preserving (do these first)

| lever | targets | board | payoff | effort/risk |
|---|---|---|---|---|
| **`-Ofast`/`-O3` on `moy_gfx`** | render | both | Anemoia saw +14% from `-Ofast`+flags; ours modest | low — one cmake line, A/B |
| **`moy_gfx` in IRAM** | render (flash-cache-miss stalls) | both | modest (loop is partly cache-resident) | low |
| **game canvas → internal SRAM** | render + composite-read (PSRAM/DSI contention) | P4 (768KB internal) | plausibly significant — the emulator's whole trick | low-med (change one alloc; verify fit) |
| **frameskip / decouple logic(60) from render(30)** | render + flush (halved) | both | large for heavy carts — the emulator's actual trick; keeps input/motion at 60 | med (loop restructure; a "feel" call) |
| **dual-core: audio (+input) on core 1** | frees core 0 for logic+render | P4 (unwired), T-Deck (tried, reverted) | real parallelism | med |
| **FPS chip off by default** | overhead | both | ~1ms + cleaner kid UX | trivial |
| **render-overlap (composite ∥ next render)** | composite fully hidden | P4 | ~5ms (lock 60 on heaviest) | high — double game canvas + triple framebuffer + async cadence; tearing needs eyes-on-glass |

### Open — model / strategic (bigger, not API-flag-flip)

| lever | note |
|---|---|
| **Lua tier (#67)** | the interpreter is our biggest tax; a lighter VM (or viper-typed hot paths / native cart compilation beyond the shipped `@micropython.native`) is the closest to the emulator's cheap interpreter. Necessary-but-not-sufficient — pair with IRAM + render fusion. More upside on the P4 (no SPI-flush floor in front of it). |
| **fullscreen game path (P4)** | skip the upscale composite + desktop entirely (the "Quake path"); trades the windowed look + the same-cart-everywhere portability |
| **retained / scanline render (PPU model)** | zero overdraw, the real structural win — but NOT API-preserving. The automatic version (**Fold-2 auto map cache**) was **tried and reverted** (inferring "static background" under a free-form API cost more than it saved). The kid-cooperative version exists today: the **scroll-layer engine (#54)** — big where used (Sky Run 45–49 vs old full-redraw ~24–29). Lever = make that idiom the default kids reach for. |

## 7. Honest framing for "fast by default"

Kids write ordinary code and shouldn't have to know the expert idioms. The path
there is NOT hardware acceleration (the PPA dead-ended for everything but the
composite) and NOT "faster sprites" (already fast). It is: **give kids more
budget by shedding the fixed taxes** (frameskip, SRAM working set, compiler/IRAM,
the composite overlaps) and **lower the interpreter cost** (Lua/native tier). The
plain-ESP32 NES emulator is the proof the hardware has the grunt — the ceiling is
the layers we put on top of it.

## References

- ESP-IDF speed guides: [P4](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-guides/performance/speed.html), [S3](https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32s3/api-guides/performance/speed.html)
- Anemoia-ESP32 (NES emulator, plain ESP32, 60fps): https://github.com/Shim06/Anemoia-ESP32 — scanline PPU, line-buffer + DMA-overlapped flush (no full framebuffer, all in SRAM), `-Ofast`+flags (+14%), audio on core 1, frameskip 1.
- Issues: #66 (T-Deck perf ledger), #58 (P4 port), #67 (Lua tier), #54 (scroll engine).
