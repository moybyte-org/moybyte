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
last generic knobs. The T-Deck is near its architectural ceiling on these, and
the P4's two "obvious" remaining knobs (`-O3` on the kernel, game canvas in
internal SRAM) both **measured null** on glass (next section) — the generic
build-tuning chapter is closed on both boards.

## 6. The lever roadmap

API-preserving = the kid writes the same `.moy` cart. **Payoff** and **effort**
are estimates; anything not "shipped/reverted" needs on-glass measurement.

### Shipped 2026-07-09 (P4) — this doc's own session

| lever | payoff |
|---|---|
| quiet-frame partial repaint (`WindowedWM.draw_stack`) | 7→21fps |
| PPA game composite (`blit_game`) | 35→51fps |
| async-composite overlap (defer show past input poll) | +2–5fps (→56) |
| retained backdrop cache (`_BackdropLayer`) | app-drags 8→14fps |

### Measured NULL on the P4 (2026-07-09 A/B — don't re-explore)

Both "cheap hardware-side" levers were built, flashed, and A/B'd on glass —
**individually and combined** — against a 4-cart baseline (Brick Siege / Letter
Blitz / Hop Quest / Sky Run, 5×2s PERF samples each, fresh boot per run):

| lever | render slice | fps | verdict |
|---|---|---|---|
| **`-O3` on `moy_gfx`** (in-source `#pragma GCC optimize`) | unchanged (±0.2ms) | unchanged | the C kernel isn't compute-bound *or* isn't the slice |
| **game canvas → internal SRAM** (`moy_alloc` `MEMORY_INTERNAL`, fit confirmed: "internal (150 KB)") | unchanged | unchanged | the render target isn't bandwidth-bound *or* isn't the slice |
| **both combined** | unchanged | unchanged | kills the "balanced bottleneck" explanation |

The elimination is the finding: with C compute *and* framebuffer bandwidth both
accelerated simultaneously and the render slice not moving 1ms, what's left of
the slice is the **MicroPython per-draw-call dispatch** (arg unboxing, api
wrapper bodies, call overhead between the cart's verbs and the kernel entry) —
the same verdict #43/#63 reached on the T-Deck by counting calls, now re-proven
on the P4 with hardware levers. Consequences:

- **`moy_gfx` in IRAM is predicted null on the P4** (it accelerates the same C
  that just measured as a minor fraction of the slice) — deprioritized, not worth
  a build unless the T-Deck (SPI flush profile, different cache) wants it.
- Levers that reduce **how often dispatch runs** get promoted: frameskip halves
  the number of dispatched `_draw` calls per second, and the Lua/native tier
  (#67) cheapens each one. Everything else render-side is noise.
- Two build-cycle gotchas recorded: cmake `set_source_files_properties` does
  NOT reach `moy_gfx` (directory-scoped; the linked object compiles in the
  `micropython.elf` target's dir — verified via build.ninja; use an in-source
  pragma), and the A/B was only trustworthy because the boot log printed which
  memory region the canvas landed in.

### The S3 counterpoint (2026-07-10) — the same levers land differently

The P4 elimination does NOT transfer wholesale to the T-Deck. **Confirmed by
the 2026-07-10 two-flash A/B** (same session, identical build minus the
pragma): Brick Siege **without** `-O3` = 33–36fps / render 10.7–15.3ms (the
old ledger band — master drift ≈ 0); **with** it = 51–54fps / render
6.6–7.4ms. One pragma line = −40% render / +50% fps, chrome (also moy_gfx
blits) halved too. Brick Siege's render is pure `moy_gfx` C (fill+map per DRAW2), so
**the S3 render slice is compute-bound where the P4's is dispatch-bound**
(slower PSRAM wait-states inside per-pixel loops + Xtensa vs RISC-V codegen).
`-O3` ships in the kernel (in-source pragma; harmless-null on P4). The
takeaway that generalizes: **per-board lever verdicts don't transfer — A/B on
each.** The **fb-in-internal-SRAM** lever measured
**cannot engage** on the T-Deck: `fb=psram free-int=164KB need=420KB` (both
ping-pong buffers + WiFi reserve); the guard + boot line stay self-documenting.

### Shipped 2026-07-10 — frameskip (#77, both boards)

Settings → FRAMESKIP (default OFF, persisted; P4 serial `skip 0|1`): a GAME's
`_update`+input+audio tick every loop frame, `_draw`+composite+flush every
SECOND. On-glass: P4 Brick Siege logic 55→60Hz / render locked 30 / busy 17.6→9.0ms;
Letter Blitz logic 49→60Hz. Trade: 30Hz motion + doubled logic rate ⇒ ~2×
alloc churn ⇒ GC collects ~2× as often. Default ON/OFF is an open product
call — on the fast S3 build most carts sit near 60 skip-OFF.

### Open — API-preserving (do these first)

| lever | targets | board | payoff | effort/risk |
|---|---|---|---|---|
| **dual-core: audio (+input) on core 1** | frees core 0 for logic+render | P4 (unwired), T-Deck (tried, reverted) | real parallelism | med |
| **FPS chip off by default** | overhead | both | ~1ms + cleaner kid UX | trivial |

**Render-overlap is CLOSED, not open** (it sat in the table above until
2026-08-15, which is how a 2026-08-09 perf hunt came to spend its last lead
re-proposing it). The estimate was ~5ms and "lock 60 on the heaviest"; what
happened on glass, in three steps:

| step | date | result |
|---|---|---|
| **triple framebuffer** (`efcf5d1`) | 2026-07-27 | SHIPPED — the DMA fence leaves the drag path, drags 30→42.8fps |
| **double game canvas** (copy-on-swap, so the deferred composite's fence could go fence-free; `26e1f9f` records the verdict at the defer site) | 2026-07-27 | BUILT, MEASURED, **REVERTED** — windowed Battle City 56→41fps. The fence it retires is ~FREE at this composite size (the DMA finishes inside the input poll), while the swap pays a ~150KB retention memcpy (4–5ms) EVERY quiet frame plus `_drain_pending` collision stalls once the fence-free show backlogs. 56–59 restored on revert |
| **L2 cache 128→256KB** (#159, `1665425`) | 2026-07-27 | closed the chapter outright: Brick Siege busy 15.5→8.0ms, the whole cart roster at the 60 cap. 512KB does not boot (internal/DMA pool 0x101) |

So the target the lever existed to reach was reached by a cache config line.
Re-open it only with NEW arithmetic — a materially bigger composite would be
one; the reverted design is in git history.

### Open — model / strategic (bigger, not API-flag-flip)

| lever | note |
|---|---|
| **Lua tier (#67)** | the interpreter is our biggest tax; a lighter VM (or viper-typed hot paths / native cart compilation beyond the shipped `@micropython.native`) is the closest to the emulator's cheap interpreter. Necessary-but-not-sufficient — pair with IRAM + render fusion. More upside on the P4 (no SPI-flush floor in front of it). |
| **fullscreen game path (P4)** | skip the upscale composite + desktop entirely (the "Quake path"); trades the windowed look + the same-cart-everywhere portability |
| **retained / scanline render (PPU model)** | zero overdraw, the real structural win — but NOT API-preserving. The automatic version (**Fold-2 auto map cache**) was **tried and reverted** (inferring "static background" under a free-form API cost more than it saved). The kid-cooperative version exists today: the **scroll-layer engine (#54)** — big where used (Sky Run 45–49 vs old full-redraw ~24–29). Lever = make that idiom the default kids reach for. |

## 7. Honest framing for "fast by default"

Kids write ordinary code and shouldn't have to know the expert idioms. The path
there is NOT hardware acceleration (the PPA dead-ended for everything but the
composite), NOT "faster sprites" (already fast), and — as of the 2026-07-09 A/B —
NOT compiler flags or SRAM placement either (both measured null; the render
slice is dispatch). What's left is: **run the dispatch less often** (frameskip,
the composite overlaps) and **make each dispatch cheaper** (Lua/native tier,
#67). The plain-ESP32 NES emulator is the proof the hardware has the grunt — the
ceiling is the layers we put on top of it.

## 8. The indexed-canvas A/B (2026-08-05) — the measurement behind RGB565-at-draw

Moved here from CLAUDE.md 2026-08-28, because that file is direction and this is
the evidence. The DECISION is settled and lives there in one line; what follows
is what settled it, so nobody re-runs the bench to re-derive it.

libmoy draws into a framebuffer of palette INDICES and resolves colour once,
later. Moybyte's device canvas resolves at DRAW time and stores RGB565 straight
into the compositor buffer. "An indexed canvas doesn't fit here" was wrong and is
recorded as wrong: libmoy renders the CART canvas, which SPEC.md 1 fixes at
320×240, so the P4's 1024×600 scan-out buffer was never the thing proposed. An
indexed cart canvas is 76,800 B instead of 153,600 B, every draw writes one byte
per pixel instead of two, and the conversion kernel already exists
(`moy_gfx.blit_indices`, #63).

Bench: standalone ESP-IDF at 360MHz / 200MHz PSRAM / `-O2`, four deterministic
scenes × 20 frames, canvas in SRAM and again in PSRAM. **A** = 8-bit index canvas
drawn by libmoy's kernels unmodified + one resolve at the stamp. **B** =
565-at-draw, geometry copied line-for-line from libmoy so only the write differs.
Ratios are A over B; below 1 = indexed wins.

| ui (100 rects) | ray (320 sspr cols) | mode7 (120 tline rows) | tri (60 tris) | stamp |
|---|---|---|---|---|
| **0.73×** | **1.13×** | **1.20×** | **0.85×** | A 2.0ms resolve vs B 0.42ms memcpy (SRAM) / 1.76ms (PSRAM) |

Indexed wins where the kernel is **write-bandwidth-bound** (fills) and loses
where it is **per-pixel-sample-bound** (sspr, tline) — those loops are dominated
by sheet addressing and Bresenham, so a narrower store buys nothing while the
resolve is added on top.

**Two things not to misquote.** The "tline is a wash" reading is from the run
*before* the reduce-once tline fix (25ms → 8ms), when the soft-modulos hid the
format entirely. And the stamp comparison flatters A: on the P4 today B's stamp
is not a memcpy at all but `moy_ppa.blit_async` — hardware, ~free to the CPU, and
the PPA does not consume indices. The T-Deck is the untested opposite shape; it
already pays an SRAM bounce copy the resolve could ride.

**Every scene hashed A==B in both placements** — an indexed canvas loses no
colour, proven on silicon. That question is closed; the format choice is settled
on performance, and B is what ships.

## 9. The S3 program (2026-09-02) — verdicts, not numbers

One day, three Opus-driven rounds, the PICO-8 ports as referees (moss moss: a
30 fps cart whose tick is pure Lua; dank tomb: 60 fps, draw-bound). The
numbers, the frame anatomy and the per-operation price list live in **#66**
(rounds 3–5); the roadmap rows above got their verdicts in **#77**. What this
section keeps is what was DECIDED:

- **The S3 pays for calls and allocations, not raster.** A C verb call floors
  at ~1.65 µs, a malloc through the IDF heap at ~9 µs (its TLSF metadata sits
  in PSRAM). So the levers that landed are the ones that delete calls and
  mallocs: every p8 draw verb one call into the machine, the hot shim paths in
  C, one call per native bit operator, a small-object pool under `l_alloc`
  with its free lists in internal SRAM and chunks that go back.
- **The composite was the console's biggest per-frame cost and the fold
  removes it** on both S3 boards, at any integer scale, from one shared body
  (`native/moy_flush/moy_fold.c`). #190's decline was wrong about the shape.
- **Instruction placement helps, data placement hurts.** The VM loop and its
  lookups in IRAM: −10 % on the tick (flash and PSRAM share the MSPI bus). More
  internal SRAM for the VM's DATA: slower — the drivers starve. `-O3` on the
  VM: still null.
- **The Python heap must be capped** (§ the S3 memory rule in
  `.claude/rules/boards.md`): it doubles on demand into the VM's PSRAM.
- **A cart that fails only on device and only sometimes is the frame cadence
  the replayer cannot reproduce**, before it is the architecture: dank tomb's
  "nil position" was the shim drawing before the first update. `run_cart --dt`
  now reproduces such cadences.
- **What is left for a 30 fps moss moss on the S3**, in order: the console's
  ~10 ms around the tick (fold snapshot, router, input poll: 3–5 ms), then the
  structural one — running the cart tick on core 0 overlapped with the
  console's frame (frame ≈ max, not sum; A/B against the shared PSRAM bus
  before keeping). A Xtensa JIT is gated on the perf counters: a template JIT
  only pays if retired instructions dominate a tick, and the evidence says
  memory does.

## References

- ESP-IDF speed guides: [P4](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-guides/performance/speed.html), [S3](https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32s3/api-guides/performance/speed.html)
- Anemoia-ESP32 (NES emulator, plain ESP32, 60fps): https://github.com/Shim06/Anemoia-ESP32 — scanline PPU, line-buffer + DMA-overlapped flush (no full framebuffer, all in SRAM), `-Ofast`+flags (+14%), audio on core 1, frameskip 1.
- Issues: #66 (T-Deck perf ledger), #58 (P4 port), #67 (Lua tier), #54 (scroll engine).
