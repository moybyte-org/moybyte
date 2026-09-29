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
   - **A PICO-8 emulator on our class of hardware does not pay one piece of it,
     and the piece is ours by choice (2026-09-10).** FAKE-08 runs on
     [z8lua](https://github.com/samhocevar/z8lua), whose `luaconf.h` says
     `#define LUA_NUMBER z8::fix32` — PICO-8's 16.16 fixed-point number IS the
     VM's number type, so a cart's `x & y` is an inlined `int32` AND inside the
     arithmetic opcode. Ours is a Lua→C crossing, measured on the T-Deck at
     **1,534 ns of floor** plus 0.5–1.7 µs of body; dank tomb makes ~1,100 of
     them a frame, ≈2.5–3 ms of its 24.9 ms draw. It also makes PICO-8's
     semantics free rather than emulated (the whole of the 2026-09-10 bit-lane
     work).
     **The 3DS calibration this entry used to rest on is WITHDRAWN
     (2026-09-11).** It read: "FAKE-08 benchmarks against an old 3DS at 268 MHz
     ARM11 against our 240 MHz LX7, and plays many carts — so the gap here is
     not silicon." No fps figure for FAKE-08 exists anywhere — not its README,
     not its wiki, not its tracker — and its README says the opposite of what
     was inferred: *"Performance is not great on Old 3ds systems. Some games
     may experience slowdowns on the faster consoles as well."* The same
     paragraph explains why: *"Pico 8 lists a raspberry pi 1 with a 700 MHz
     ARM11 professor as minimum spec, and the old 3DS's CPU is 268 MHz ARM11"*
     — **38% of PICO-8's own stated minimum**. "Plays many carts" traced back
     to "Many games should be playable regardless", a hope following a
     disclaimer; this doc turned it into a benchmark. Issue #155 has users
     reporting carts at "less than quarter of the speed they're supposed to
     play at" on comparable low-end ARM. What would calibrate it is running ONE
     corpus cart on a stock FAKE-08 build and reading its rate; until someone
     does, the comparison says nothing either way.
     **And the conclusion it carried is contradicted by measurement**: the S3's
     core declares `XCHAL_HAVE_PREDICTED_BRANCHES 0` and
     `XCHAL_HAVE_SPECULATION 0`, and `perfcnt` prices that at **4.66 bubble
     cycles per taken branch, 330k taken branches a frame, 17.6% of moss moss's
     update** — while the 3DS's ARM1176 predicts branches. Silicon IS part of
     the gap, and an interpreter is the worst case for the part we lack.
     **Not a port**: z8lua is Lua 5.2 and C++
     where moycore is 5.4 as C with `LUA_32BITS`, and the idea costs a 64-bit
     intermediate for multiply/divide plus collapsing 5.4's integer/float
     duality. Its `pico8` feature branch is the clean version to read.
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
| flash mode | DIO (a board fact — the images we flash are dio) + 120MHz ✅ | QIO ✅ |
| compiler | `-O2` (PERF) ✅ | `-O2` ✅ |
| PSRAM | OCT 120MHz ✅ (the owner's bump) | HEX 200MHz ✅ |
| caches | I 32KB + D 64KB — **max** ✅ | L2 256KB ✅ (#159; 512KB does not boot) |

This is *why* the T-Deck's PSRAM bump only "helped a bit" — it was one of the
last generic knobs. The P4's two "obvious" remaining knobs (`-O3` on the
kernel, game canvas in internal SRAM) both **measured null** on glass (next
section).

**But "well-tuned" was read as "closed", and it was not.** The knobs surveyed
above are the ones the *speed guide* lists. What that framing misses is the
options MicroPython's own `ports/esp32/boards/sdkconfig.base` sets for a
generic 4MB board and every Moybyte board then inherits — a different question
from "did we tune IDF", and one nobody had asked. Swept 2026-09-20, it was not
empty: `CONFIG_SPI_MASTER_ISR_IN_IRAM` sat off (base turns it off under a
comment reading "To reduce IRAM usage"), which put the panel flush's done-ISR
in flash on both S3 boards. So: the IDF-knob chapter is closed, the
INHERITED-DEFAULT chapter was never opened, and the two are not the same
survey.

**And it split the two S3 boards, which is this doc's own rule arriving
again** — same SoC, same option, opposite answers, each board's fragment
carrying its A/B:

| board | Brick Siege | verdict |
|---|---|---|
| T-Deck | worst-frame 48 → 51 fps, render 10 → 9ms | **SHIPPED** for 7,932 B internal SRAM |
| Guition S3 | fps 47 → 47, worst 46 → 46, render 9 → 9 | **NULL — declined**, 7,908 B for nothing |

The mechanism is the one §9 already uses as its control: the Guition runs its
flush on a **core-0 feeder task**, off the VM's core, so a done-ISR in flash
costs the VM nothing there; on the T-Deck the feeder and the VM share a core
and every band completion is a flash-miss window. The win is not "ISR in
IRAM", it is "ISR in IRAM *when it contends with the VM*" — so the lever's
real precondition is core topology, and any third board needs its own A/B.

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
- Levers that reduce **how often dispatch runs** get promoted: the tick model's
  draw divisor (#217) halves or thirds the number of dispatched `_draw` calls
  per second, and the Lua/native tier (#67) cheapens each one. Everything else
  render-side is noise.
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

### Shipped 2026-07-10 — frameskip (#77, both boards); RETIRED by #217

Superseded by the tick model (#217): logic at the cart's declared rate, draw on
an adaptive integer divisor, one STEADY knob. What follows is the record of the
manual toggle it replaced. Settings → FRAMESKIP (default OFF, persisted; P4 serial `skip 0|1`): a GAME's
`_update`+input+audio tick every loop frame, `_draw`+composite+flush every
SECOND. On-glass: P4 Brick Siege logic 55→60Hz / render locked 30 / busy 17.6→9.0ms;
Letter Blitz logic 49→60Hz. Trade: 30Hz motion + doubled logic rate ⇒ ~2×
alloc churn ⇒ GC collects ~2× as often. Its default was still an open product
call when #217 retired the toggle — on the fast S3 build most carts sat near 60
skip-OFF.

### Shipped 2026-09-21 — the map-lookup cache, in two halves (#77)

`moy_prof` priced `mp_map_lookup` at 10–20% of PC samples on every board,
against one shared `uint8_t[128]` hint table serving every map in the system
— a console running a shell, a WM and a cart at once. Two things were wrong
with it, and they had to be fixed TOGETHER:

- **The index was aimed for REPR_A.** `py/map.c` picks the slot as
  `index >> 2` ("shift down by two to remove the tag bits"), which is REPR_A's
  qstr layout. REPR_C — every console board — tags a qstr `(q << 4) | 6`, so
  after `>> 2` two bits are constant for every qstr key and the 128-slot cache
  offered attribute, global and method lookups **32 slots**; a gc-pointer key
  reached the same 32. `moybyte_patch_map_cache_for_repr_c`
  (`tools/esp32_build_lib.sh`) shifts by the tag width REPR_C uses; each
  console build script calls it beside REPR_C, it refuses a tree that is not
  REPR_C, and the REPR_A Zero declines it in writing.
- **The table is too small for this workload.** `MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE`
  goes 128 → 512 in each console board's `mpconfigboard.h` (384 bytes of
  `.bss`), each with its own verdict beside it.

Brick Siege, diag on, three runs a side, the same session per board;
"share" is `mp_map_lookup`'s slice of `moy_prof`'s samples:

| arm | T-Deck fps / worst / share | Guition S3 | P4 | Guition P4 |
|---|---|---|---|---|
| stock | 51.5 / 49–52 / 20.1% | 47 / 42–47 / 13–17% | 55.5 / 54–56 / 10–13% | 56 / 48–55 / — |
| re-aimed index, 128 | 52 / 47–52 / 20.3% | 48 / 46–48 / 17.3% | 55 / 49–55 / 12.8% | — |
| re-aimed index, 512 | **55 / 52–54 / 17.5%** | **49 / 46–48 / 15.1%** | **56.5 / 55–56 / 11.7%** | **56.5 / 53–56 / 13.9%** |
| re-aimed index, 1024 | 54.5 / 48–54 / 18.2% | — | — | — |

**Neither half alone moves the frame, and 1024 buys nothing over 512.** The
re-aim alone is null on all three boards it was tried on (32 → 128 reachable
slots), and the size alone is the same 128 by arithmetic; together they are
the T-Deck's biggest single lever since `-O3`. The step shrinks across the
table because the lookup is a smaller share of each board's frame: the S3
boards spend 8–10% in `gc_alloc` where the T-Deck spends 1%, and the
Waveshare P4 is 38% idle in this cart. On both P4s the median's step is inside
the noise and the worst frame is what moves (the Guition P4's 48 → 53).

The mechanism was checked directly rather than inferred:
`tools/map_cache_probe.py` times instance-attribute lookups against the
number of distinct names in the hot set, and shows a set that fits the
reachable slots at ~0.55–0.7 µs a lookup on the S3s (0.34 on the P4) and one
that overflows them at ~1.2 µs (0.66); at 512 the 160-name set no longer
overflows. The `experiments/state_verb_cost`
README had recorded exactly this thrash as an unproven hypothesis a month
earlier.

### READ 2026-09-23 — what is left in `mp_map_lookup` is the kid idiom, not a knob

With the cache re-aimed and 512 slots, `mp_map_lookup` still holds 17.5% of
the T-Deck's Brick Siege samples (15% on the Guition S3). `moy_prof --frame 1`
attributes each sample to the function that CALLED the one the CPU was in, so
a lookup's cost lands on whoever asked for it, and the split on the T-Deck
(Guition S3 in brackets) is:

| who asks | share | what it is |
|---|---|---|
| `mp_obj_class_lookup` | 4.4% (3.6) | the class-dict probe that HITS a method, after the instance dict missed it |
| `mp_obj_instance_load_attr` | 3.3% (2.6) | the instance-dict probe: a hit for `self.x`, a MISS for `self.method` before the class is asked |
| `mp_map_lookup`'s own callees | 3.1% (2.3) | `qstr_hash` + `find_qstr`: hashing a qstr on a cache miss walks the qstr pool chain to find its hash |
| `mp_load_global` | 2.4% (2.0) | a builtin (`len`, `range`, `abs`) misses the cart's globals before it hits the builtins map |
| `mp_load_method_maybe` | 1.2% (1.0) | a method on a builtin type, `list.append` and its kin |
| `mp_setup_code_state` | 1.0% | keyword arguments at a call |
| stores, modules | ~1% | `self.x = …`, `math.sin` |

Every row but the builtins one is the idiom the kid API is built on —
`self.x`, `self.move()` — and the miss-before-hit shape is MicroPython's
attribute protocol, not a table size: the instance dict is asked first, the
class MRO second, and a subclass of an API base pays the miss twice. The fix
for that is a VM method cache keyed by (type, name), which upstream does not
have and which is not a build knob; it is written down here so the next pass
does not re-price it as a cache-size question. Two levers that ARE reachable
were priced and declined under the sub-millisecond wall: binding the builtins
a cart uses into its globals at load would remove the 2.4% miss (≈0.4 ms on
the T-Deck) and change what `globals()` shows a kid; a per-qstr pool index
would remove `find_qstr`'s walk (≈0.3 ms). The `qstr_find_strn` 1–1.6%
beside them is runtime string building (`str(score)` each frame) checking
whether the result is already interned — the cart's code, priced.

### Shipped 2026-09-23 — size-class run hints for `gc_alloc` (#66)

The same Brick Siege profiles that priced the map cache put `gc_alloc` at
2.9% of the T-Deck's samples, 8.3% of the Guition S3's, 7.0% of the P4's and
13.5% of the Guition P4's — the one symbol whose share differed by board on
one cart, on four builds of the same MicroPython with the same collector
settings. The mechanism is the allocator's scan, read in `py/gc.c`: it keeps
ONE hint per heap area, a single-block allocation advances it past itself, a
multi-block one never does, and every collect resets it to the start of the
area. So each multi-block allocation walks the allocation table from the hint
through every hole too small for it, and the next one walks the same holes
again. `moy_prof --frame 2` put the cost where that predicts: under
`mp_obj_malloc_helper` — tuples, instances, iterators, bound methods — 11.2%
on the Guition S3 against 1.2% on the T-Deck, with the spilled call frames
(`m_malloc_maybe`) the same 1.2% on both.

`tools/gc_alloc_probe.py` measured the walk with the cart up, in the heap
state the game leaves: tuples of 2, 4, 16 and 64 blocks, one hundred each,
as found and directly after `gc.collect()` (µs per allocation, the median of
three; the stock rows are the 2026-09-21 image, the patched rows the same
boards the same day after the flash):

| board | arm | 2 blocks | 4 | 16 | 64 |
|---|---|---|---|---|---|
| T-Deck | stock, post-collect | 209 | 359 | 534 | 793 |
| T-Deck | run hints, post-collect | **15** | **17** | **28** | 392 |
| Guition S3 | stock, post-collect | 230 | 374 | 601 | 736 |
| Guition S3 | run hints, post-collect | **11** | **15** | **28** | 308 |
| P4 | run hints, post-collect | 7 | 10 | 19 | 294 |
| Guition P4 | run hints, post-collect | 7 | 9 | 20 | 467 |

Both S3 boards have a ~4MB PSRAM heap, a live set just under a megabyte
after a collect, and fill it at 20–35KB/s, so a collect comes every ~100s;
the as-found cost sat at 10–14 µs on both — the walk is concentrated after
each collect and every frame in between pays a smaller one. The 64-block
column is the class ceiling and is meant to be: hints cover runs of 2–32
blocks (512 bytes), sized to the objects a frame makes; anything larger
still walks, and the cart that makes kilobyte lists per frame is the one to
re-price it for.

The patch is `tools/patch_gc_run_hints.py`, applied by
`moybyte_patch_gc_run_hints` in the shared build half and taken by every
console board (the headless Zero declines it in writing): one hint per run
length, an ATB index before which no free run that long starts. An
allocation scans from its class's hint, and the run it finds raises every
class of its length or more to it — no run that long starts earlier, or the
scan would have met it; a free lowers the classes the merged run can now
serve; a collect resets them all. Single-block allocations keep the stock
hint alone, so the common path is unchanged, and the helpers cost 0.6–0.7%
of a Bench profile. One stock line is corrected on the way: the "this area
is full" marker was written in block units to a byte index and landed a
quarter of the way in. Upstream master carries the same single hint.

Brick Siege, diag on, three runs a side, the 2026-09-21 image against the
patched one on the same boards; "share" is `gc_alloc`'s slice of `moy_prof`'s
samples, and "gone" means it fell below the profile's 0.6% floor:

| arm | T-Deck fps / worst / share | Guition S3 | P4 | Guition P4 |
|---|---|---|---|---|
| map cache, 512 (2026-09-21) | 55 / 52–54 / 2.9% | 49 / 46–48 / 8.3% | 56.5 / 55–56 / 7.0% | 56.5 / 53–56 / 13.5% |
| + run hints | 55 / 54–56 / gone | 50 / 48–49 / gone | **62.5 / 62 / gone** | 57 / 54–57 / gone |

The Waveshare P4 is the board that moves, and its idle share went with it:
`esp_cpu_wait_for_intr` fell from 37.8% to 1.3%, so the frames it was
waiting through were allocator walks on the other side of a fence. The two
S3 boards move a frame at the worst and the Guition P4 one at the worst; on
all three the cart is draw-bound now (`mg_fill_run` + `moy_spr` are 30–45%
of samples). The Bench referee is UNCHANGED on all four boards — every phase
floor equal to the 2026-09-22 run, every verb inside its spread — which
says the Bench's phases were never allocator-bound and the lever is a
game-shaped one.

What the P4 profiles show now: with `gc_alloc` gone,
`vPortClearInterruptMaskFromISR` + `vPortExitCriticalMultiCore` hold 19% of
the Waveshare's samples and 17% of the Guition P4's, FreeRTOS SMP critical
sections that the S3 profiles do not show at all. The P4 records no caller
(`mepc` is one frame deep), so who takes them is unproven; the GIL
round-robin was the candidate and is DECLINED below.

A false reading, recorded so it is not re-investigated: the Guition S3's
first two Bench runs after the flash read every compute phase 1.4–1.7x slow
(logic 26 → 38ms, table 29 → 49, `pix` 6.8 → 13.0µs) while Brick Siege and
the probe read normal; `moy_prof` on that state put 9% of samples in
`moy_fold_snap_wait` and `time.sleep_ms` spinning on the system timer. A
hard reset (`esptool --after hard_reset read_mac`) restored the 2026-09-22
floors to the microsecond on the same image, and a stock rebuild was never
needed. It recurred the same day after a second profiler pass and cleared
the same way. The suspect is the fold's snap-dead fence in `moy_fold.c` — a
copy that once times out turns every later snapshot into a CPU memcpy until
reboot — and the Guition S3's README names the counter to read while it is
slow; it is a lead, not a finding.

### DECLINED 2026-09-23 — the GIL round-robin divisor (`MICROPY_PY_THREAD_GIL_VM_DIVISOR`)

The VM gives and retakes the GIL every 32 branches so another Python thread
can run (`py/vm.c`'s `pending_exception_check`), a FreeRTOS mutex pair each
time. No Python thread exists on either P4, so the bounce buys nothing
there, and it was the named candidate for the 17–19% of P4 samples in SMP
critical-section exits. The port defines the divisor unguarded, so the A/B
took a guard patch (`#ifndef` around the port's line) plus 1024 in each
board's `mpconfigboard.h`; same boards, same day, against dev `f4180be`,
Brick Siege diag on three runs a side, and the Bench referee for the
interpreter-bound phases:

| board | fps / worst, 32 → 1024 | critical-section share | Bench float / logic / table ms |
|---|---|---|---|
| Waveshare P4 | 62.5 / 62 → 62.5–63 / 62 | 19.0% → 18.8% | 33 / 19 / 23 → 33 / 19 / 23 |
| T-Deck | 55 / 54–56 → 53.5–56.5 / 53–54 | none either side | 43 / 23 / 26 → 43 / 23 / 27 |
| Guition S3 | 50 / 48–49 → 50.5–51.5 / 50 | none either side | 45 / 26 / 29 → 45 / 26 / 29 |
| Guition P4 | not measured — its silicon twin was null on all three meters | | |

**Null on every meter on every board**, so the guard patch and the values
were removed the same day and nothing of it ships. What it settles: the P4's
critical-section share is NOT the GIL bounce. `vPortClearInterruptMaskFromISR`
is the ISR-side exit, which points at interrupt handlers — the DSI vsync,
the PPA and GDMA completions, the I2S audio DMA, and the profiler's own
GPTimer — rather than the VM, and the next step is to count interrupts per
frame by source, not to re-price a VM knob. Do not re-propose the divisor
without a caller in hand.

### DECLINED 2026-09-21 — the frame-spill threshold (`VM_MAX_STATE_ON_STACK`)

A MicroPython call whose frame exceeds `VM_MAX_STATE_ON_STACK` (stock: 11
machine words) heap-allocates that frame on EVERY call; #63 measured the
spilling case at 1,536 µs against 9 µs warm and fixed the single hottest
function by replacing it with a C callable, leaving the threshold itself
untouched. It read like the biggest lever left. Four configs on T-Deck glass,
Brick Siege (a Python cart), 3-6 runs each, and the wide-frame recursion
ceiling bisected on the board:

| threshold / VM task stack | median fps | worst | wide-frame recursion depth |
|---|---|---|---|
| **11w / 16KB (stock)** | 52 | 48–51 | 45 |
| 16w / 16KB | 52.5 | 51.5 | — |
| 32w / 16KB | **54** | 52 | **29** |
| 32w / 24KB | 52.5 | 52 | 52 (−8KB internal SRAM) |
| 64w / 16KB | 53.5 | 53 | — (indistinguishable from 32w) |

**The gain and the cost are ONE mechanism, which is why no setting wins.** What
buys the fps is frames moving from the gc heap to `alloca`; what eats the
recursion ceiling is the same frames landing on the C stack. Raising the task
stack to compensate buys the depth back (45 → 52) and gives the median gain
straight back (54 → 52.5) — which is §9's *"more internal SRAM for the VM's
DATA: slower — the drivers starve"* arriving again, since the VM task stack is
VM data. Best case is 0.7ms/frame, at this repo's own sub-millisecond wall,
against a third of the recursion headroom on a console children write code for.

What survives in every variant is a smaller one: the WORST frame improves
48–51 → 52. If this is ever re-opened it should be for tail latency, with new
arithmetic, not for the median. Deep recursion is safe either way — it raises
`RuntimeError: maximum recursion depth exceeded` from `mp_cstack_check()` and
never crashes, verified on glass at every threshold.

### Open — API-preserving (do these first)

| lever | targets | board | payoff | effort/risk |
|---|---|---|---|---|
| **dual-core: audio (+input) on core 1** | frees core 0 for logic+render | P4 (unwired), T-Deck (tried, reverted) | real parallelism | med |
| **FPS chip off by default** | overhead | both | ~1ms + cleaner kid UX | trivial |
| **P4 critical sections** — `vPortClearInterruptMaskFromISR` + `vPortExitCriticalMultiCore` are 17–19% of a Brick Siege profile on both P4s once `gc_alloc` is gone (§6, 2026-09-23), FreeRTOS SMP spinlock pairs the S3 profiles never show. Caller unproven (the P4 records one frame). The GIL round-robin divisor was the candidate and measured NULL on every meter (§6, DECLINED 2026-09-23); the `FromISR` exit names interrupt handlers (DSI vsync, PPA/GDMA completions, I2S audio DMA, the profiler's own timer), so the next step is to count interrupts per frame by source | dispatch | both P4s | up to ~3ms of a 16ms frame if a source is found | interrupt accounting first; no knob until a caller is in hand |

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
slice is dispatch). What's left is: **run the dispatch less often** (the draw
divisor, the composite overlaps) and **make each dispatch cheaper** (Lua/native tier,
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

Three rounds with the PICO-8 ports as referees (moss moss: a 30 fps cart whose
tick is pure Lua; dank tomb: 60 fps, draw-bound). The numbers, the frame anatomy
and the per-operation price list live in **#66** (rounds 3–5); the roadmap rows
above got their verdicts in **#77**. What this section keeps is what was
DECIDED:

- **The S3 pays for calls and allocations, not raster.** A C verb call floors
  at ~1.0 µs of C-side time (re-measured 2026-09-09 with the per-verb profiler
  on the real carts, against ~1.65 from the original micro-bench: `flr` reads
  1.03 µs over 680 calls a frame and `palt` 1.16 over 1204). What it costs the
  CART is more, because the profiler times the wrapper and not the crossing
  that reaches it. `tools/p8_verb_bench.py` prices that (one cart per
  operation, 2,000 calls a tick, net of an empty-loop control) — T-Deck,
  2026-09-10, ns per call:

  | the Lua being replaced | ns | | the C replacing it | ns |
  |---|---:|---|---|---:|
  | `i+1` — one VM instruction, no call | **29** | | `flr(1.5)` — the call floor | **1,533** |
  | `nop()` — a Lua→Lua call, empty | **1,125** | | `peek(0x4300)` | 1,584 |
  | a small Lua function body | 2,634 | | `band(3,5)` two ints | 2,046 |
  | | | | `rnd(8)` | 2,196 |
  | | | | `shl(1,4)` / `shr(3,1)` | 2,247 / 2,276 |
  | | | | `3>>1` / `1.5&-1` (operators) | 2,565 / 3,510 |
  | | | | `band(1.5,-1)` a fraction | 3,280 |
  | | | | `mget(1,1)` | 3,996 |

  **The number that decides a verb is 1,125 against 1,533**: a Lua→Lua call
  and a Lua→C one. Crossing into C costs only ~400 ns MORE than the call a
  shim function was already paying, so a C verb pays whenever it replaces a
  Lua FUNCTION whose body is worth more than ~400 ns — a dozen VM instructions,
  which every real shim body clears. That is why `split`, `rnd`, `srand`,
  `lut_span` and `map` all won their A/Bs.

  **And it is why the bare-operator rule earns its complexity.** Replacing
  INLINE Lua with a verb is the opposite trade: `i+1` is 29 ns against 2,046
  for a `band` call, a 70× loss. `&`, `|` and `^^` on provably-integer
  operands must stay bare VM instructions (§the porter's `_BARE_OPS`), and a
  fold that turns one into a call is a regression however tidy it looks.
  **An OPERATOR costs ~250 ns more than the verb of the same C body**
  (`3>>1` 2,562 against `shr(3,1)` 2,274; `1.5&-1` 3,511 against 3,284): the
  porter localises the verb names as upvalues and leaves the nine `__p8_*` as
  plain globals. Adding them to the localisation block is ~0.3 ms a frame on
  dank tomb — measured but NOT taken (2026-09-10), because it is a vendored
  porter change for 0.7% of a frame. A malloc through the IDF heap
  at ~9 µs (its TLSF metadata sits in PSRAM). So the levers that landed are the ones that delete calls and
  mallocs: every p8 draw verb one call into the machine, the hot shim paths in
  C, one call per native bit operator, a small-object pool under `l_alloc`
  with its free lists in internal SRAM and chunks that go back.
- **The composite was the console's biggest per-frame cost and the fold
  removes it** on both S3 boards, at any integer scale, from one shared body
  (`native/moy_flush/moy_fold.c`).
- **Instruction placement helps, data placement hurts.** The VM loop and its
  lookups in IRAM: −10 % on the tick (flash and PSRAM share the MSPI bus). More
  internal SRAM for the VM's DATA: slower — the drivers starve. `-O3` on the
  VM: still null.
- **The Python heap must be capped** (§ the S3 memory rule in
  `.claude/rules/boards.md`): it doubles on demand into the VM's PSRAM.
- **A cart that fails only on device and only sometimes is the frame cadence
  the replayer cannot reproduce**, before it is the architecture: dank tomb's
  "nil position" was the shim drawing before the first update. `run_cart --dt`
  reproduces such cadences.
- **What is left for a 30 fps moss moss on the S3**, in order: the console's
  ~10 ms around the tick (fold snapshot, router, input poll: 3–5 ms), then the
  interpreter itself (§3.1). A Xtensa JIT was gated on the perf counters: a
  template JIT only pays if retired instructions dominate a tick, and the
  evidence was read as memory. **THE COUNTERS HAVE NOW BEEN READ, and they say
  otherwise (2026-09-11, `perfcnt`, #66).** moss moss `_update`, share of
  cycles: retired instructions **51.6%**, branch bubbles **17.6%**, data
  stalls 18.1%, instruction stalls 7.1%, register-dependency bubbles 4.6% —
  ~99% accounted. Two controls say that profile is the VM's and not the
  board's: the Guition S3, whose flush runs on a core-0 feeder task rather than
  the VM's core, reads the same to three decimals; and forcing the whole Lua
  heap out of internal SRAM (`set_sram_floor` 16 → 256) moves the frame only
  36.6 → 37.2 ms. So the four null levers were never memory fixes that failed
  — there was no memory problem to fix, and `-O3` could not help because the
  cost is dispatch BRANCHES, not straight-line code. What this does NOT do is
  make a JIT a good idea; it removes the reason it was ruled out, which is a
  smaller claim. Every remaining lever has the same shape: **run fewer
  opcodes** (fusion, superinstructions, the porter's `_BARE_OPS` work), or
  take fewer branches inside the ones that run (`lvm.c` compiles to 377
  conditional branches against 3 hardware `LOOP`s, where the raster kernels
  get 44 loops and 85 branchless ops).
- **Overlapping the cart TICK with the draw on the other core is DECLINED
  (2026-09-10), and it is an arithmetic decline, not an engineering one.**
  Parallelising two things caps the win at the smaller of them, and on the
  T-Deck dank tomb's are logic **2.5 ms** against draw **25** in a 42 ms loop:
  perfect, free, race-free overlap saves ~6 %. It is also not free — `_update`
  and `_draw` are one cart's code in one VM over shared mutable globals, with
  no snapshot between them. The safe form of core parallelism is INSIDE a C
  verb, where the VM is blocked and there is nothing to race; that has nothing
  to bite on either, because the C verbs together are ~5 ms of that 25 ms draw
  and the largest single one is 0.32 ms. What is parallel already and worth
  keeping: the panel flush feeder and its done-ISR on core 0, the audio I2S
  feeder, the P4's PPA bounce worker. **Revisit only for a RASTER-bound cart**
  — full-screen effects, a big `map()`, software 3D — where one verb owns
  milliseconds.

## References

- ESP-IDF speed guides: [P4](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-guides/performance/speed.html), [S3](https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32s3/api-guides/performance/speed.html)
- Anemoia-ESP32 (NES emulator, plain ESP32, 60fps): https://github.com/Shim06/Anemoia-ESP32 — scanline PPU, line-buffer + DMA-overlapped flush (no full framebuffer, all in SRAM), `-Ofast`+flags (+14%), audio on core 1, frameskip 1.
- Issues: #66 (T-Deck perf ledger), #58 (P4 port), #67 (Lua tier), #54 (scroll engine).
