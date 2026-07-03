# 60 FPS Architecture for Moybyte on the LilyGO T-Deck Plus (ESP32-S3)

**Status:** Research findings + recommended architecture (decision doc).
**Date:** 2026-06-27. **2026-07-03:** current measured state + the lever ledger
now live in the **#66 performance ledger** (GitHub issue, mirrored by
`make sync-issues`); this doc remains the research grounding and carries no
current numbers.
**Scope:** How fantasy consoles and ESP32 game engines hit 60 fps, and what
architecture would get the v0.4 `.moy` console to a 60 fps target on the
T-Deck Plus (ESP32-S3, dual-core Xtensa LX7 @240 MHz, 512 KB internal SRAM,
8 MB PSRAM, 320×240 ST7789 over SPI @80 MHz).

This is a research/decision doc. It changes no code. It feeds the open runtime
and engine decisions: GitHub issues **#6** (Lua vs MicroPython), **#43** (draw-call
ceiling), **#44** (extend our own indexed engine), **#11** (full TIC-80 runtime).

---

## 0. TL;DR verdict

- **The SPI flush is NOT the wall.** With true DMA double-buffering the panel
  flush runs asynchronously while the CPU renders the next frame, so the frame
  period is `max(render, flush)`, **not** `render + flush`. At 320×240 the flush
  is a **hard ceiling of ~65 fps** (15.4 ms), not a per-frame tax. **60 fps fits
  under that ceiling even at full 320×240** — it is *not* impossible. The catch is
  the margin is thin (~8%), so flush jitter / SPI gaps make full-res 60 fragile.
- **The wall is render time.** Once the flush is hidden, the *sole* remaining
  constraint is `render ≤ 16.6 ms`. Today a full 320×240 redraw takes **~50 ms of
  draw** (issue #43) → we need roughly a **3× render speedup** to reach 60.
- **MicroPython per-frame cost is a real, measured part of that 50 ms** — not just
  draw-call count. MicroPython is **~300–1900× slower than C** for per-element
  work (MDPI ESP32 paper) and the native `map()` primitive collapsing 381 tile
  draws → 1 did **not** raise Hop's fps (~12), which is direct local evidence that
  the per-frame *interpreter* cost, not the draw-call count alone, dominates.
- **Every fantasy console solves this the same way:** the script issues a
  *bounded* number of high-level draw calls per frame; the **per-pixel and
  per-primitive inner loops live in native compiled code** (C in PICO-8/TIC-80,
  Rust in Pyxel). The script is never in the pixel loop, and ideally not in the
  per-entity loop either.
- **Honest verdict:**
  - **Simple games** (a few dozen moving sprites, a tilemap, modest logic):
    **60 fps is reachable at 320×240** with (a) DMA double-buffer to hide the
    flush, (b) a native sprite-batch + tilemap draw path so the per-frame native
    work is a handful of MP→C calls, and (c) keeping the Python cart's per-frame
    logic light. This is essentially the issue #44 plan taken to completion.
  - **Heavy action games** (hundreds of entities, per-pixel effects, full-screen
    redraw every frame, lots of Python entity logic): **60 fps at 320×240 is not
    realistic in MicroPython.** The fallback is **30 fps** (the PICO-8 default), or
    a **lower internal resolution** (160×120 → 2× upscale) to buy render budget,
    or moving the hot loop out of Python (native data-driven engine, or a Lua VM).
  - **Recommended target:** ship **60 fps as the ceiling and 30 fps as the
    contract floor** (PICO-8's `_update`/`_update60` model), at **320×240**, with a
    **160×120 "turbo" mode** available to carts that need the render headroom.

---

## 1. Framebuffer size / resolution strategy

### 1.1 What the reference consoles use

| Console | Resolution | Pixels | Notes |
|---|---|---|---|
| **PICO-8** | 128×128 | 16,384 | 16-color fixed palette. |
| **TIC-80** | 240×136 | 32,640 | 16-color, ~2× PICO-8's pixel count. 16 KB VRAM of 272 KB total RAM. |
| **Pyxel** | configurable, retro-small (commonly 128–256 wide) | — | 16 colors; default 60 fps. |
| **ESP Little Game Engine** (ESP32 PICO-8-like) | 128×128 | 16,384 | 16 colors, 32 sprites, ~20 fps, retained-mode. |
| **Moybyte v0.4 (today)** | **320×240** | **76,800** | RGB565 indexed via MOY64. **~4.7× PICO-8, ~2.4× TIC-80.** |

The first thing to notice: **Moybyte's canvas is far bigger than the consoles it
draws inspiration from.** PICO-8 hits its CPU budget at 128×128 (16 K pixels);
Moybyte is asking MicroPython to fill 76.8 K pixels per frame — 4.7× the area —
which is a large part of why a full redraw is slow.

Sources: PICO-8 128×128 / 16-color
([manual](https://www.lexaloffle.com/dl/docs/pico-8_manual.html),
[Wikipedia](https://en.wikipedia.org/wiki/PICO-8)); TIC-80 240×136 / 16 KB VRAM
([architecture](https://deepwiki.com/nesbox/TIC-80/2-architecture)); Pyxel 60 fps
([GitHub](https://github.com/kitao/pyxel)); ESP Little Game Engine
([Hackaday](https://hackaday.io/project/164205-esp-little-game-engine)).

### 1.2 The flush budget — verified math

A frame is `W × H × 2 bytes` (RGB565). At an SPI clock of `f` Hz the *minimum*
flush time (bus 100% busy, no command/dummy overhead) is `W·H·16 / f` seconds.

| Internal res | Bytes/frame | Bits/frame | Flush @80 MHz | Flush-only ceiling |
|---|---|---|---|---|
| **320×240** (native) | 153,600 | 1,228,800 | **15.36 ms** | **~65 fps** |
| **240×136** (TIC-80-like) | 65,280 | 522,240 | 6.53 ms | ~153 fps |
| **160×120** (2× upscale → 320×240) | 38,400 | 307,200 | **3.84 ms** | **~260 fps** |
| **128×128** (PICO-8-like) | 32,768 | 262,144 | 3.28 ms | ~305 fps |

So the brief's numbers check out: **320×240 @80 MHz ≈ 15.4 ms/frame ≈ 65 fps
flush ceiling**, and **160×120 ≈ 3.8 ms ≈ 260 fps ceiling**. A 160×120 internal
buffer point-doubled to 320×240 on flush keeps the screen filling the panel while
cutting the per-pixel render *and* flush cost by **4×**.

This is corroborated from the other direction: Mario Zechner/community report a
240×320×16bpp ST7789 flushes in "approximately 16 ms with frame buffer in SRAM
and DMA transfer" (≈60 fps), and a LovyanGFX/TFT_eSPI ST7789/ILI9341 320×240
benchmark measured **~32 fps at 40 MHz and ~63 fps at 80 MHz** for a full-frame
bouncing-circles test — right on the 65 fps ceiling.

Sources: [esp32.com throughput thread](https://esp32.com/viewtopic.php?t=26793);
[mboehmerm 320×240 benchmark](https://github.com/mboehmerm/Touch-Display-ili9341-320x240);
[LVGL ESP32 guide](https://lvgl.io/blog/tutorial-esp32-getting-started).

### 1.3 The 80 MHz caveat (don't silently drop to 40 MHz)

The full 80 MHz only happens when **all** SPI signals are routed through the
dedicated **IOMUX** pins. If even one SPI pin goes through the **GPIO matrix**,
the driver silently caps at **~40 MHz** (26 MHz full-duplex), which **doubles the
flush to ~30 ms → ~33 fps ceiling**. There is also a panel caveat: **ST7789 is
reliable at 80 MHz; ILI9341 is not and must drop to 40 MHz** (the T-Deck Plus is
ST7789, so 80 MHz is on the table — confirm the wiring uses IOMUX pins). Issue #33
already moved the bus 40→80 MHz; this section is the reason that mattered.

Sources: [ESP-IDF SPI Master driver](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/spi_master.html);
[Anemoia-ESP32 (80 MHz ST7789 vs ILI9341)](https://github.com/Shim06/Anemoia-ESP32).

### 1.4 What comparable ESP32 60 fps projects actually use

- **Anemoia-ESP32** (NES, ~60 fps on 320×240 ST7789 @80 MHz): renders the NES's
  **256×240** and uses **line-based DMA** — it does *not* keep a full 320×240
  framebuffer (256×240×2 = 120 KB, ~⅓ of SRAM); it buffers a few scanlines and
  pushes them via DMA. ([Anemoia](https://github.com/Shim06/Anemoia-ESP32))
- **ESP32 Game Boy** (160×144 native): went from **8 fps → 60 fps** by replacing
  per-scanline SPI with **one ~46 KB `pushColors` DMA burst** plus `IRAM_ATTR` on
  the hot functions. ([writeup](https://flavortown.hackclub.com/projects/17607))
- **ESP Little Game Engine**: 128×128 internal, ~20 fps (its own slow VM is the
  limiter, not the flush).

The pattern: **smaller internal resolution + one big DMA flush.** Nobody hitting
60 fps on an S3 is doing a per-scanline blocking blit of a full 320×240 buffer.

### 1.5 Resolution recommendation

Frame the choice as **render budget + flush margin**, not possible-vs-impossible:

- **320×240 (sharp):** native panel res, crisp UI/editors/pixel art, but a *tight*
  render budget (76.8 K px to fill) and a *thin* flush margin (15.4 ms vs the
  16.6 ms frame). Best for the **system UI, editors, and simple games**.
- **160×120 → 2× upscale (chunky):** 4× less render and flush work, a *huge* flush
  margin (3.8 ms), and a *roomy* render budget — the only realistic path to 60 fps
  for **action/effect-heavy carts**. The look is chunkier (PICO-8-grade), which is
  on-brand for a fantasy console.

**Recommendation:** keep **320×240 as the default** (UI must be sharp), and add a
**per-cart `pixel_scale` / "turbo" 160×120 internal mode** (manifest/`config.json`
flag) that renders at 160×120 and **point-doubles on flush**. This is the single
cheapest 4× lever and it is purely additive to the existing indexed pipeline.

---

## 2. The scripting VM vs native split (the central lesson)

**Every fantasy console keeps the per-pixel and per-primitive inner loops in
native compiled code; the script only issues a bounded number of high-level draw
CALLS per frame.** The script is never in the pixel loop.

- **PICO-8:** Lua game logic; **native C** drawing primitives
  (`cls/pset/line/rect/rectfill/circ/circfill/spr/sspr/map/print`) operate on a
  native 128×128 framebuffer. The CPU cost model proves the split: graphics are
  charged **~1 cycle per pixel drawn** — *"transparent pixels in a sprite still
  cost a cycle each; offscreen pixels still cost a cycle as well"* — an accounting
  that only makes sense because a C loop touches every pixel while the Lua call is
  a single statement. Per-frame contract: **`_update()`/`_draw()` at 30 fps**, or
  **`_update60()`/`_draw()` at 60 fps** (half the CPU budget per frame). Under
  load, the *update rate stays constant* and the *draw* is dropped (→15 fps). The
  budget is `2^23 = 8,388,608` cycles/sec (≈139,810/frame @60 fps); `cls()` ≈ 2052
  cycles; `stat(1)` = fraction of frame CPU used (1.0 = 100%).
  ([manual](https://www.lexaloffle.com/dl/docs/pico-8_manual.html),
  [cycles thread](https://www.lexaloffle.com/bbs/?pid=44725))
- **TIC-80:** pure-**C** core; **10+ scripting languages** (Lua, JS, Wren,
  Fennel, Python-subset, …) are *only* the script layer — each has a binding layer
  onto the one native C API. Per-frame entry **`TIC()` called 60×/sec**; the tick
  flow is *run script → Blit (native pixel stage) → Sound* — pixels happen in a
  separate native stage after the script returns. There is even an open request
  (#1788) to *add* PICO-8-style per-pixel cost accounting, which confirms TIC-80
  doesn't charge the script per pixel **because the script isn't in the pixel
  loop**. ([architecture](https://deepwiki.com/nesbox/TIC-80/2-architecture),
  [TIC() wiki](https://github.com/nesbox/TIC-80/wiki/TIC),
  [#1788](https://github.com/nesbox/TIC-80/issues/1788))
- **Pyxel:** the entire engine core is **Rust** (`pyxel-engine` crate; PyPI
  classifier "Programming Language :: Rust"); the Python script only schedules
  `update`/`draw` (default **60 fps**) and issues `blt`/`bltm`/`rect`/… calls into
  Rust. The maintainer's Pyxel2 goal is explicit: *"the core engine is eventually
  implemented in Rust"* and *"it will work without Python installed."*
  ([#306](https://github.com/kitao/pyxel/issues/306),
  [crate](https://crates.io/crates/pyxel-engine))

**The principle for Moybyte:** the per-frame script work must be `O(draw calls)`,
not `O(pixels)` and ideally not `O(entities)`. Minimize script→native crossings
per frame, and never cross into the script inside a pixel loop. Moybyte's draw API
(`cls/rect/circ/spr/print/map`) already has the right *shape*; the gap is (a)
their per-call MP→C overhead and (b) the Python that runs *between* the calls.

---

## 3. MicroPython vs Lua vs native, per-frame cost on ESP32-S3

### 3.1 Relative speed (the hard numbers)

| Source / benchmark | C | Lua | MicroPython | Takeaway |
|---|---|---|---|---|
| **MDPI ESP32 paper** (M5Stack ESP32 @160 MHz, 1024 B): CRC-32 / SHA-256 / FFT / FIR / IIR | 1× | — | **~302–1900× slower** | MP is *thousands of times* slower than C for per-element compute. |
| **ESP8266 GPIO frequency** (Lua vs MP vs Arduino-C++) | 125 kHz | 6.6 kHz | ~13 kHz | **C++ ≈20× Lua, ≈10× MP; MP ≈2× faster than Lua.** |
| **OLED game-loop FPS** (identical draw+clear loop, ESP8266) | C++ **35**, C 27 | **4** | **7–8** | MP ≈ ⅕ of C++; **Lua worse than MP** here. |
| **Official MP "Performance" wiki** (tight loop, Teensy 96 MHz ARM) | 95.8 M | — | 1.1 M | MP tight loop **~87× slower than C++** (~9 µs/iter). |
| **luvsheth ESP32** (primality, bytecode baseline) | C module **108.8×** | — | 1× (487 ms) | A C module is ~109× a Python loop; `native` 2.4×, `viper` 16.2×. |

Two crucial, counter-intuitive findings from the head-to-head data:

1. **MicroPython is *faster* than plain (PUC/eLua) Lua on ESP-class hardware**, by
   ~2× on the GPIO test and ~2× on the OLED FPS test. The common assumption "Lua
   would be faster" is **not supported** for the interpreters that actually run on
   Xtensa. **LuaJIT (which *is* native-class) does not run on Xtensa ESP32** — only
   the plain Lua interpreter, which is the slow one measured here.
2. **NodeMCU Lua can't even run a free-running game loop** — it's
   event/timer-driven with a hardware watchdog: *"any try to create an infinity
   loop will cause a panic error and the board will be restarted by the
   watchdog."* The OLED paper had to drive frames from a timer and capped at ~4
   fps. Lua-RTOS-ESP32 has real threads (so a loop is possible) but no published
   fantasy-console FPS, and the same slow interpreter.

Sources: [MDPI 12(1):143](https://www.mdpi.com/2079-9292/12/1/143);
[ESP8266 freq compare](http://esp8266freq.blogspot.com/2016/12/esp-speed-compare-in-lua-micropython.html);
[ESP OLED FPS paper (Rak & Wiora)](https://iaesprime.com/index.php/csit/article/download/128/46);
[MP Performance wiki](https://github.com/micropython/micropython/wiki/Performance);
[luvsheth native/viper/C](https://luvsheth.com/p/making-micropython-computations-run);
[NodeMCU FAQ](https://nodemcu.readthedocs.io/en/dev/lua-developer-faq/).

### 3.2 Is MicroPython per-frame overhead a known 60 fps blocker? Yes.

- **Identical-loop OLED FPS:** MicroPython **7–8 fps** vs C++ **35 fps** — MP gets
  ⅕ the frame rate on the *same* display loop.
  ([paper](https://iaesprime.com/index.php/csit/article/download/128/46))
- **CircuitPython µGame** (close cousin): **~30.6 fps non-DMA, 6.7 fps for a
  full-screen update**, "a dozen fps in a relatively simple game," explicitly
  blamed on *"interpreter overhead"* + software float; a later VM rewrite gave a
  **5× VM speedup**. ([µGame](https://hackaday.io/project/27629-game))
- **Framebuffer flush alone** for a 128×128×8 buffer ≈ **41 ms** (~24 fps) on a
  Pyboard before any logic — the MP docs warn framebuffer-to-display latency "may
  be too high for game applications."
  ([wiki](https://github.com/micropython/micropython/wiki/Performance))
- **The local evidence is the strongest:** Moybyte's native `map()` collapsed 381
  tile draws → 1 MP→C call and Hop's fps **stayed ~12** (issue #32/#43). If
  draw-call count were the only cost, that should have jumped. It didn't, which
  means the **per-frame MicroPython interpreter cost** (the cart's entity/AI/HUD
  loops, attribute lookups, the per-`spr` Python around each call) is a *major*
  part of the ~50 ms draw. This matches the MDPI 300–1900× factor: any per-entity
  Python math is hundreds of times slower than the C equivalent.

### 3.3 MicroPython's own escape hatches (and their ceiling)

The Xtensa ESP32-S3 supports the native/viper/asm emitters (the RISC-V ESP32-C3
does not — relevant: our target *is* Xtensa, so these are available):

| Lever | Speedup vs bytecode | Constraints |
|---|---|---|
| `@micropython.native` | **~2.4×** (official "roughly twice") | larger code; scheduler/GIL paused during it. |
| `@micropython.viper` | **~16×** (luvsheth) — "almost as fast as assembler" for int/bit | machine-word int semantics, **no float**, type hints required. |
| `@micropython.asm_xtensa` / C module | **~109×** (luvsheth C module) | hand assembly / C; effectively native-class. |

So a *pure* MicroPython hot loop can be sped ~2–16× with decorators, but a real C
module is ~100×. **Viper is the sweet spot for fixed-point integer hot loops**
(blitters, fills, fixed-point physics) if you want to stay in the MP toolchain
without writing a C module — but it loses floats, which most kid game math uses.

Sources: [MP speed docs](https://docs.micropython.org/en/latest/reference/speed_python.html);
[luvsheth](https://luvsheth.com/p/making-micropython-computations-run).

### 3.4 Conclusion for the runtime question (#6)

- **Switching the cart VM from MicroPython to Lua would *not* help** — plain Lua on
  Xtensa is ~2× *slower* than MicroPython, has no JIT, and (NodeMCU) can't run a
  free game loop. A Lua *VM* buys authoring familiarity / TIC-80 compatibility
  (#11), **not** fps. Drop "switch to Lua for performance" as a justification.
- The real fps levers are **native** (move the hot per-frame work into C), and
  **structural** (fewer/larger draw calls, less per-entity Python, lower internal
  res). The runtime language is close to a wash for speed; the *architecture* is
  where the 3× lives.

---

## 4. The update loop in C / "avoid Python loops entirely"

### 4.1 Two architectures

- **Immediate-mode (script draws every frame):** the script calls
  `cls; for each sprite: spr(...); print(...)` each frame. Cheap to *author*,
  but every entity is a script→native crossing **and** the per-entity bookkeeping
  (positions, animation, AI) runs in the interpreter. This is Moybyte today, and
  it's why effect-heavy frames (more `spr` calls) dip the fps (#43).
- **Retained-mode / data-driven (script configures, C draws + updates):** the
  script *registers* sprites/tilemap/entities into a native system once (or on
  change), and the **C engine owns the per-frame loop** — it iterates entities,
  applies declared motion, culls, sorts, and blits, all in C. The script's
  per-frame work drops toward zero. This is what the constrained-MCU engines do:
  - **FabGL** (ESP32): a retained Scene/Sprite engine — unlimited sprites (64
    colors + transparency), multi-frame animation, **built-in collision-detection
    callbacks**; you configure sprites and the C engine draws/updates them.
    ([FabGL](https://github.com/fdivitto/FabGL))
  - **ESP Little Game Engine**: configure a background layer + 32 soft sprites
    (collision + rotation); the C engine renders them — its own VM runs game logic
    at ~900 k ops/s, but the *rendering* is native and retained.
    ([Hackaday](https://hackaday.io/project/164205-esp-little-game-engine))
  - **Anemoia / Retro-Go / Game Boy**: the entire per-frame loop is native C/C++
    (`-Ofast`, `IRAM_ATTR` hot paths); the only "script" is the ROM. This is the
    extreme of "no interpreter in the hot loop" → real 60 fps.
    ([Anemoia](https://github.com/Shim06/Anemoia-ESP32),
    [Retro-Go](https://github.com/ducalex/retro-go))

### 4.2 Which gets to 60 fps on a constrained MCU?

**Data-driven / native-loop wins, decisively.** The immediate-mode-in-interpreter
projects (µGame ~6–30 fps, MP OLED 7–8 fps, ESP-LGE ~20 fps) sit well below 60;
the native-loop projects (Anemoia ~60, Game Boy 60) hit it. The dividing line is
exactly **whether the per-frame loop runs in compiled code or in the interpreter.**

### 4.3 What this means for Moybyte

The honest implication of the `map()` result (§3.2): a native **draw** batch alone
is not enough, because the cart's per-frame *Python* (entity updates, AI, HUD) is
also in the ~50 ms. To get a 3× render speedup you need to shrink **both**:

1. **Native sprite-batch / draw-list** (issue #43 lever 1, the `map()` trick for
   sprites): submit N sprites in **one** MP→C call; the C kernel loops + blits all.
   Kills the effect-dip and removes N−1 crossings. **Necessary but not sufficient.**
2. **A native, data-driven entity/animation layer** (optional, the bigger lever):
   move *position/velocity/animation/culling* into C so the cart declares "here are
   my sprites and how they move" and the C engine runs the per-frame loop. This is
   what removes the *interpreter* cost the `map()` result exposed. It is more work
   and constrains the API toward retained-mode, but it is the only thing that takes
   per-entity Python off the critical path for heavy games.

A middle path: keep immediate-mode authoring but provide **native helpers for the
hot per-entity math** (a viper/C "update N particles", "move N entities by
velocity", "collide AABB list") so the cart's `_update` issues a few native calls
instead of Python loops. This preserves the kid-friendly imperative style while
moving the `O(entities)` work into C.

---

## 5. DMA double-buffering + dual-core

### 5.1 Double-buffering is foundational — it takes the flush off the critical path

The esp_lcd SPI driver's color transfer is **non-blocking DMA**: per the IDF docs,
`esp_lcd_panel_io_tx_color()` *"will package the command and RGB data into a
transaction, and push into a queue. The real transmission is performed in the
background (DMA+interrupt)."* The overlap mechanism is the **`on_color_trans_done`
callback**: with **two framebuffers** you call `draw_bitmap(bufA)` (returns
immediately; DMA flushes A in the background), render frame N+1 into **bufB** on the
CPU, then wait on the done-callback before reusing bufA.

**Therefore the frame period is `max(render, flush)`, not `render + flush`.** At
320×240 the 15.4 ms flush becomes a **hard ceiling (~65 fps)**, not a per-frame
tax — and **60 fps fits under it**. The catch is the ~8% margin: real SPI gaps and
flush jitter make full-res 60 thin. Lower internal res (§1) widens the margin
(160×120 → 3.8 ms flush → ~260 fps ceiling) *and* shrinks render — but the point
of low-res is **margin + render budget, not making 60 possible.** With the flush
hidden, the **sole remaining wall is `render ≤ 16.6 ms`** (§3–4).

**Moybyte's current flush serializes** (issue #40 path): `flush()` does
`self._frame[:] = self._fb` (a 153 KB PSRAM→PSRAM copy, ~3 ms) and then bands the
copy out, blocking the loop. True overlap needs a **ping-pong of two PSRAM
buffers**: render into buffer B while DMA flushes buffer A, swap, and recycle each
buffer in `on_color_trans_done`. This is the fragile #40 DMA path done properly,
and it is the **first foundational change** — it removes both the ~3 ms copy *and*
the flush from the critical path.

Sources: [esp_lcd LCD docs](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/lcd/index.html);
local `firmware/.../modules/moy_compositor.py` `flush()`.

### 5.2 PSRAM contention caveat

The win only materializes if CPU rendering doesn't stall on the memory DMA is
reading. **PSRAM is the trap:** GDMA reading a PSRAM framebuffer contends with CPU
PSRAM access (~50/50 bandwidth split), and PSRAM can't be written while GDMA reads
it. The IDF mitigation is **bounce buffers** in internal DRAM ("DMA fetching from
DRAM bounce buffer is much faster than PSRAM frame buffer"). Moybyte already stages
DMA through an **internal-SRAM strip/band buffer** — keep that pattern; ideally the
actively-flushed band lives in internal SRAM even with double-buffering.

Source: [esp32.com RGB LCD throughput](https://esp32.com/viewtopic.php?t=26793).

### 5.3 Dual-core — and the MicroPython single-core caveat

The canonical ESP32-S3 move is to **pin the flush/audio to one core** via
`xTaskCreatePinnedToCore` while game logic + rendering run on the other:
- **T-HMI-C64**: core 0 copies the bitmap to the LCD; core 1 runs the emulation.
  ([T-HMI-C64](https://github.com/retroelec/T-HMI-C64))
- **Anemoia**: the **APU/audio lives entirely on the second core** ("it doesn't
  need to run in perfect sync"). ([Anemoia](https://github.com/Shim06/Anemoia-ESP32))

**But MicroPython's VM is effectively single-core on ESP32.** Per the maintainers,
*"MicroPython does not support dual-core on the ESP32; it will always run all its
threads on the same core"* — `_thread` only time-slices on one core; the second
core is reserved for the ESP-IDF runtime. **Implication:** the second LX7 core
**cannot run the cart's Python in parallel.** It is only useful via **native C
FreeRTOS tasks**. So the dual-core plan for Moybyte is:
- **Core 0:** the MicroPython VM (cart `_update`/`_draw`, the native draw kernels
  it calls).
- **Core 1 (native C only):** the **audio (I2S) task** (must never starve — issue
  #16 confirmed audio isn't the *current* bottleneck, but a steady I2S task wants
  its own core), and optionally the **DMA flush ISR / bounce-copy**.

Since the flush is already non-blocking DMA, the *biggest* concrete second-core
value is **audio** + the DMA-done bookkeeping — not parallelizing game logic.

Sources: [micropython #4611](https://github.com/micropython/micropython/issues/4611),
[#8197](https://github.com/micropython/micropython/issues/8197).

---

## 6. 2D acceleration on the ESP32-S3

**The ESP32-S3 has no dedicated hardware 2D graphics engine — no blit, no blend,
no fill, no scale/rotate accelerator.** What it has:

- **GDMA / `esp_async_memcpy`:** **memory-to-memory copy only** — "almost the same
  as the standard libc memcpy." It can overlap copies with compute, but it cannot
  fill a rect or blend. Useful for moving framebuffer bytes (e.g. the ping-pong
  copy), nothing 2D.
  ([async_memcpy docs](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/async_memcpy.html))
- **LCD_CAM peripheral:** the parallel-LCD/camera DMA path, not a 2D engine; it can
  hardware byte-swap RGB565 at zero CPU cost, but no blend/fill/scale.
- **PIE / SIMD (LX7 "Extended Instructions"):** the S3 *does* have a small SIMD
  unit — **8×128-bit vector registers, 160-bit accumulators, 8/16/32-bit lanes**
  (e.g. 8× int16/op), `EE.*` instructions. It **can** accelerate fills / blits /
  color conversion: bitbank measured **~40% speedup** (14→10 ms) on JPEG
  YCbCr→RGB565. **But** it requires 16-byte alignment, has no logical right shift,
  saturating add/sub only, no horizontal ops, and sparse/partly-closed docs — it's
  a hand-written-assembly DSP tool, not a 2D API.
  ([bitbank: S3 has SIMD](https://bitbanksoftware.blogspot.com/2024/01/surprise-esp32-s3-has-few-simd.html),
  [Espressif PIE intro](https://developer.espressif.com/blog/2024/12/pie-introduction/))

**Contrast — the ESP32-P4 has what the S3 lacks: a real PPA (Pixel Processing
Accelerator) + 2D-DMA** doing hardware **fill / blend / scale (bilinear) / rotate /
mirror**. LVGL's P4 integration measured **~30% saving on fill/copy, up to ~9× on
pure fill, ~40% on rotate** — *but blend showed "no significant gains… due to
DMA-2D memory bandwidth."* (Relevant to issue #12, the P4 prototype board.)
([P4 PPA docs](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/peripherals/ppa.html),
[LVGL PPA](https://lvgl.io/docs/open/integration/chip_vendors/espressif/hardware_accelerator_ppa))

**Takeaway for the S3:** all fill/blit/blend/scale is CPU work. The only hardware
help is GDMA (copies) and optionally hand-tuned PIE SIMD for the innermost
fill/blit loop (~40% on the hot kernel, at high implementation cost). There is no
compositor to lean on — the levers are the software ones: minimize MP→C draw-call
count, keep the flush on DMA, fit the 80 MHz IOMUX path, and (if pursued) PIE-tune
the `moy_gfx` fill/blit kernels. **If hardware 2D acceleration is a hard
requirement for 60 fps heavy games, that's an argument for the P4 (#12), not the
S3.**

---

## 7. Recommended 60 FPS architecture for Moybyte on the S3

A concrete, ranked plan. This extends the existing indexed `moy_gfx` /
`moy_compositor` engine (consistent with the issue #44 decision to keep our own
engine) rather than adopting a foreign framework.

### 7.0 The frame-budget model to design against

```
frame_period = max( render_time , flush_time )      # with true DMA double-buffer
target: frame_period <= 16.6 ms   (60 fps)
flush_time(320x240 @80MHz)  = 15.4 ms   -> hard ceiling ~65 fps (thin margin)
flush_time(160x120 @80MHz)  =  3.8 ms   -> ceiling ~260 fps (huge margin)
render_time today (320x240 full redraw) ~= 50 ms    -> need ~3x to hit 60
```

The whole plan is: **(A) hide the flush** so the ceiling is 65 fps, then **(B)
attack the ~50 ms render** down to ≤16.6 ms.

### 7.1 Ranked plan

**Rank 1 — DMA double-buffer the flush (foundational).** Ping-pong two PSRAM
framebuffers; render into B while DMA flushes A; recycle in `on_color_trans_done`.
Removes the ~3 ms `_frame[:]=_fb` copy *and* takes the 15.4 ms flush off the
critical path → frame period becomes `max(render, flush)`. **This is the change
that makes everything else "just" a render-budget problem.** (Builds on #40.)

**Rank 2 — Native sprite-batch / draw-list.** One MP→C call submits N sprites; the
C kernel loops + blits all (the `map()` #32 trick for sprites; issue #43 lever 1).
Removes N−1 script→native crossings and the per-`spr` Python wrapper. Eliminates
the effect-dip. **Necessary but, per the `map()` evidence, not sufficient alone.**

**Rank 3 — Cut per-frame Python (`O(entities)` interpreter cost).** This is the
part the `map()` result exposed. Two options, pick per how far you want to push:
- *Lighter:* native/viper helpers for the hot per-entity math (move/collide/animate
  N entities in one call), keeping immediate-mode authoring.
- *Heavier:* a **data-driven native entity/sprite layer** — the cart declares
  sprites + motion once; the C engine owns the per-frame update+draw loop (FabGL /
  ESP-LGE model). This is what fully removes per-entity Python from the hot path
  and is the only route to 60 fps for **heavy** games.

**Rank 4 — Internal-resolution "turbo" mode.** Per-cart **160×120 → 2× upscale**
flag. 4× less render + flush. The cheapest way to buy render budget for
action/effect-heavy carts; default stays 320×240 for sharp UI.

**Rank 5 — Dirty-rect for static screens.** The compositor already has
`DirtyTracker`/`flush_dirty` (issue #44 lever 1). Redraw-on-change + flush only the
dirty region → idle UI/editors/turn-based games approach ~0 cost. **No help for
full-action full-redraw games**, but a large win for the *computer* feeling snappy.

**Rank 6 — Second core for audio (+ DMA bookkeeping).** Native C FreeRTOS task on
core 1 for steady I2S. The MP VM stays single-core on core 0 (it cannot be
parallelized). Modest direct fps effect; protects audio from stutter as carts grow.

**Rank 7 — PIE/SIMD-tune the `moy_gfx` fill/blit kernels.** ~40% on the innermost
kernel, high effort, do last and only if profiling says the C kernel (not the
Python around it) is the remaining cost.

### 7.2 The explicit decisions the brief asks for

**(a) Internal resolution.** Keep **320×240 as the default** (sharp UI/editors/pixel
art); add a **per-cart 160×120 turbo mode** (point-doubled on flush) for carts that
need the 4× render+flush headroom. Resolution is a render-budget/margin knob, not a
possible-vs-impossible switch.

**(b) Keep the Python cart API behind a richer C engine, vs Lua, vs stay
MicroPython.** **Stay MicroPython for the cart language, and invest in a richer C
engine behind the existing draw API.** Rationale: plain Lua on Xtensa is ~2×
*slower* than MicroPython and has no JIT (§3.1) — switching VMs buys *no* fps. The
3× lives in native code, not the interpreter language. (A Lua VM remains worth
doing for *authoring/TIC-80 compatibility* under #11, but **not** as a perf play —
remove that justification.)

**(c) Is a native update loop / data-driven engine needed to avoid per-frame
Python?** **For simple games: no** — Rank 1+2 (double-buffer + sprite-batch) plus
light Python `_update` gets simple carts to 60 at 320×240. **For heavy games: yes**
— the `map()` evidence shows draw-batching alone won't move fps when per-entity
Python dominates; a data-driven native entity/update layer (Rank 3 heavy) is
required to take `O(entities)` off the interpreter.

**(d) Double-buffer + dual-core plan.** Double-buffer is **Rank 1, foundational**
(ping-pong PSRAM, async DMA, recycle in `on_color_trans_done`) → frame period
`max(render, flush)`. Dual-core: **core 0 = MP VM + native draw kernels; core 1 =
native C audio (I2S) task + DMA-done bookkeeping.** The MP VM is single-core, so
core 1 is for native tasks only, and its biggest concrete win is audio.

### 7.3 Honest verdict on 60 fps reachability

- **Simple games (a few dozen sprites, a tilemap, light logic) at 320×240:**
  **60 fps is reachable.** Cost: Rank 1 (double-buffer) + Rank 2 (sprite-batch) +
  disciplined light Python `_update`. The flush ceiling (65 fps) leaves a thin but
  workable margin; the render budget is the constraint and these games fit it.
- **Heavy action games (hundreds of entities, per-pixel effects, full redraw,
  heavy Python logic) at 320×240:** **60 fps is not realistic in MicroPython.**
  The honest options are **30 fps** (PICO-8's default, and a perfectly good
  contract floor), **160×120 turbo** (Rank 4, the 4× lever), or **moving the
  per-frame loop into native C** (Rank 3 heavy / a data-driven engine). At full
  res with per-entity Python, expect today's **~15–25 fps** to improve to the
  **20s–low 30s** with Rank 1–4, not to 60.
- **Cost summary:** Rank 1–2 are bounded, contained engine work (the highest
  value-per-effort, and they make the *flush* a non-issue and *simple* games hit
  60). Rank 3-heavy (native data-driven loop) is the expensive part and the only
  thing that unlocks *heavy* 60 fps — it constrains the API toward retained-mode
  and is a real architectural commitment. Hardware 2D acceleration for heavy 60 fps
  is an argument for the **ESP32-P4** (#12), not the S3.

**Recommended contract (matching PICO-8):** expose **`_update`/`_draw` at 30 fps as
the floor and `_update60` at 60 fps as the ceiling**; keep the *update* rate stable
and *drop draws* under load (PICO-8's degradation model). Ship **320×240 default +
160×120 turbo**. Land Rank 1+2 first; treat Rank 3-heavy (native data-driven
engine) as the gated investment for heavy-action 60 fps, decided alongside the
runtime question (#6) and the P4 evaluation (#12).

---

## 8. Source index

**Resolution / framebuffer / flush math**
- PICO-8 manual (128×128, 16 colors, CPU model, `_update`/`_update60`, `stat(1)`): https://www.lexaloffle.com/dl/docs/pico-8_manual.html
- PICO-8 cycle/pixel cost thread: https://www.lexaloffle.com/bbs/?pid=44725
- TIC-80 architecture (pure-C core, 10+ script langs, 240×136, 16 KB VRAM, TIC() 60×/s, blit stage): https://deepwiki.com/nesbox/TIC-80/2-architecture
- TIC-80 TIC() wiki: https://github.com/nesbox/TIC-80/wiki/TIC
- TIC-80 #1788 (no per-pixel cost = script not in pixel loop): https://github.com/nesbox/TIC-80/issues/1788
- Pyxel (Rust core, 60 fps): https://github.com/kitao/pyxel , https://github.com/kitao/pyxel/issues/306 , https://crates.io/crates/pyxel-engine
- ST7789 ~16 ms full flush w/ SRAM+DMA; PSRAM bounce-buffer: https://esp32.com/viewtopic.php?t=26793
- 320×240 ST7789/ILI9341 ~63 fps@80 MHz / ~32 fps@40 MHz benchmark: https://github.com/mboehmerm/Touch-Display-ili9341-320x240
- LVGL ESP32 guide (240×240 clear ~3 ms): https://lvgl.io/blog/tutorial-esp32-getting-started
- ESP-IDF SPI Master (IOMUX 80 MHz vs GPIO-matrix 40 MHz): https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/spi_master.html

**Language speed / game-loop FPS**
- MDPI Electronics 12(1):143 — C/MicroPython/Rust/TinyGo on ESP32 (MP ~302–1900× slower): https://www.mdpi.com/2079-9292/12/1/143
- ESP8266 freq compare — Lua vs MP vs Arduino (C++ 20× Lua, 10× MP): http://esp8266freq.blogspot.com/2016/12/esp-speed-compare-in-lua-micropython.html
- Rak & Wiora OLED FPS (C++ 35 / C 27 / MP 7–8 / Lua 4; Lua no free loop): https://iaesprime.com/index.php/csit/article/download/128/46
- MicroPython Performance wiki (tight-loop ~87× C++; 41 ms framebuffer flush): https://github.com/micropython/micropython/wiki/Performance
- luvsheth — native 2.4× / viper 16.2× / C module 108.8×: https://luvsheth.com/p/making-micropython-computations-run
- MicroPython speed docs (native/viper/asm): https://docs.micropython.org/en/latest/reference/speed_python.html
- miguelgrinberg MP benchmarks (ESP32-S3 vs CPython 169–553×): https://blog.miguelgrinberg.com/post/benchmarking-micropython
- CircuitPython µGame (~6–30 fps, interpreter-bound, 5× VM rewrite): https://hackaday.io/project/27629-game
- NodeMCU FAQ (no free game loop): https://nodemcu.readthedocs.io/en/dev/lua-developer-faq/

**ESP32 engines / DMA / dual-core / 2D accel**
- Anemoia-ESP32 (NES ~60 fps, line-DMA, APU on 2nd core, ST7789 80 MHz): https://github.com/Shim06/Anemoia-ESP32
- ESP32 Game Boy 8→60 fps via one DMA burst: https://flavortown.hackclub.com/projects/17607
- Retro-Go (native C cores): https://github.com/ducalex/retro-go
- FabGL retained sprite/scene engine: https://github.com/fdivitto/FabGL
- ESP Little Game Engine (128×128, 32 sprites, ~20 fps, retained): https://hackaday.io/project/164205-esp-little-game-engine
- esp_lcd LCD docs (non-blocking DMA, on_color_trans_done): https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/lcd/index.html
- T-HMI-C64 (LCD copy core 0 / emulation core 1): https://github.com/retroelec/T-HMI-C64
- MicroPython single-core on ESP32: https://github.com/micropython/micropython/issues/4611 , https://github.com/micropython/micropython/issues/8197
- ESP32-S3 async_memcpy (copy only): https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/async_memcpy.html
- ESP32-S3 PIE/SIMD (bitbank ~40% color-conv): https://bitbanksoftware.blogspot.com/2024/01/surprise-esp32-s3-has-few-simd.html , https://developer.espressif.com/blog/2024/12/pie-introduction/
- ESP32-P4 PPA (hardware 2D, contrast): https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/peripherals/ppa.html , https://lvgl.io/docs/open/integration/chip_vendors/espressif/hardware_accelerator_ppa
