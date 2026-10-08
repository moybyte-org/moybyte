---
name: perf
description: Measure a Moybyte cart's frame rate or frame cost on a board, pick the meter that answers the question (PERF, VERBS, LUAPROF, PERFCNT, moy_prof), and record the result where numbers live. Use before measuring, optimizing, or proposing a performance lever -- it lists the levers already settled.
---

# Performance

Numbers live in issues: **#66** is the ledger (per-cart fps, the frame-budget
model, shipped/reverted/open levers, how to measure), **#58** the P4, and
`docs/perf_native_gap_v1.md` (**#77**) the cross-board strategy — why the boards
trail native emulators, the frame-budget taxes, the PPA verdict, the ranked
levers. When hardware numbers land, edit #66's BODY (comments are its
changelog), then `make sync-issues`. Never paste a number into a doc.

## Measure

```bash
tools/board.py tdeck perf "Brick Siege"              # median drawn fps over 8 s, cart ended after
tools/board.py tdeck perf "Brick Siege" --diag       # + draw/flush/logic/render/chrome ms
tools/board.py guition_s3 perf "Jet Teapot" --uncap  # every loop frame draws: the compiled carts' floors
python3 tools/p4_perf.py --board p4                  # the roster, one row a cart
python3 tools/p4_cart_bench.py --board tdeck         # the two Bench carts' phases
```

`perf` drops the first 4 s (caches, cold flash), takes only samples naming the
cart it started, medians the drawn fps (a GC spike lands in one sample), ends
the cart and puts diag and uncap back. It prints the conditions; keep them
equal across an A/B:

- **PERF DIAG** is off by default (kid mode, #68) and its meters are frame
  eaters, so `perf` and `p4_perf.py` measure the SHIPPING fps with it off.
  With it off a board writes no periodic line at all -- no PERF, no diag tick,
  no AUDIORATE, no SD trace (owner call 2026-09-30: every line is garbage the
  collector stops the frame for) -- so that fps is read off the drawn-frame
  counter (`ws._frames_drawn`, the one PERF's `fps=` is taken from) across 2 s
  windows. `--diag` turns the diag on and reads the PERF lines for the phase
  ms; the on-glass helpers (`tests/on_glass.py`'s `perf_diag`) arm it too,
  because the suites' floors were set with it on. Say which. DIAG SD LOG stays
  off for serial measurement (it stutters).
- **WiFi off** — the radio is a lease; `wifi_held` in `state` names holders.
- **LINKED** means a second console in the same two-player cart made it a real
  ESP-NOW match on the shared tick (#65): move the peer and re-measure.
- **The tick model (#217)** runs a game's logic at its manifest rate and draws
  on a divisor; PERF's `tick=rate/div miss=n` says whether it held its tick.
  `uncap 1` makes every loop frame draw; Settings → STEADY (`steady 0|1`) is how
  long the divisor remembers.

Measure the one claim the change is about, before and after, on the boards it
touches — not a ritual baseline. Account for the WHOLE frame before choosing a
lever: partial instrumentation has produced confident wrong answers. Stop when
the best remaining lever is a few hundred µs a frame.

## Which meter

| question | meter |
|---|---|
| fps and the frame split | `PERF` (`runtime/perf_line.py`, every board, ~2 s, under PERF DIAG) |
| the collector's stops | `PERF`'s `gc=n/us/max`: collections this sample, their pause and the longest, in µs (`gc.pauses()`, `tools/patch_gc_meters.py`; `-` where a build lacks it) |
| what each heap holds | `heapcaps`: PSRAM, internal and internal-DMA total/free/largest/low-water, and the gc heap's held/live/areas (`heapcaps_line` in `runtime/dev_channel.py`) |
| WHO holds it | `tools/mem_census.py BOARD fresh\|after\|offheap\|cut\|...`: the boot trace (which step grew each gc area, and for what request), the `moy_alloc` registry by owner, each area's occupancy, live bytes by boot step (`arm live`), a compiled cart's PSRAM at the Player's fit check (`device/mem_census.py`'s header) |
| the S3 panel pump, draw batches, loop hitches | `HITCH` (a frame's work past 80 ms, by stage) and `LOOP` (the PERF period's average frame by stage) are the kernel's on every console under PERF DIAG (`native/moy_kernel/moy_loop.c`); `device_diag`'s `DRAWBRK`/`BATCH`/`DRAW2`/`PUMP`/`I2CSTAT`/`WEBHOST` are staged on the T-Deck alone (each board.toml says why); elsewhere `state`'s `pump`/`fold`/`ppa`/`stages` read the same C meters |
| instructions or memory? | `PERFCNT`: `perfcnt on [event]`, then `perfcnt` — retired instructions per cycle over update and draw (`runtime/dev_channel.py`; the strategy doc argues its readings) |
| a Lua/p8 cart's draw | `VERBS`: `verbs on`, then `verbs` — per-frame calls and ms per verb. The canvas meters read zero on a Lua cart: it draws through libmoy's C verbs (`native/moycore/README.md`) |
| a Lua cart's own code vs the p8 shim | `LUAPROF`: `luaprof on`, then `luaprof` — samples on a VM instruction count, because a call hook would inflate the shim's small functions; it perturbs the frame and cannot see the collector (`luagc` for that) |
| the whole image, across tiers | `moy_prof`: `python3 tools/prof_sample.py --board tdeck --cart "..."` (`--frame 1` for the caller). PC-sampled from a GPTimer ISR, free at its default 1000 Hz, symbolized on the host; the module header says how each arch takes the PC. The only meter that sees the collector and costs living between tiers |

Each switch is its own word (`tools/board.py X tail 5 --send "verbs on"`), armed
at install so an unarmed frame carries no wrapper.

## Settled — do not re-attempt

- **Per-board verdicts do not transfer.** The `-O3` `moy_gfx` pragma is
  A/B-confirmed on the compute-bound S3 and NULL on the dispatch-bound P4.
- **The FEED/DISPATCH/GC chain was worked through**: shipped — auto-native carts
  (#67), the live-set diet, the pal-state variant cache (#72), the layer pool,
  `background()`; reverted with verdicts — Fold-2's auto map cache, the third
  bounce slot (which retired the core-1 feeder unbuilt). That is where people
  LOOKED, not a wall: the tick model's own pin found a free lever on 2026-09-10
  (a tick-only frame must beat a DRAWING frame, not the period — dank tomb ran
  its logic at 23 Hz of 60, #217), and on 2026-09-20 the cart VM's collector
  turned out never to have left Lua's default schedule, spending a whole cycle
  inside one frame (#107).
- **Fixed engine-side, kid API untouched**: per-draw-call dispatch (#43/#63),
  call-frame heap spill (#63), float boxing (the REPR_C build patch; the
  REPR_A heap-wrap collect was the old micro-stutter), banding (the SRAM-bounce
  flush: panel DMA reads only internal SRAM).
- **The map-lookup cache (2026-09-21)**: its index re-aimed for REPR_C in the
  shared build half, and 512 slots per console board, A/B'd on three boards.
  Neither half moves a frame alone and 1024 buys nothing over 512 — do not
  re-propose either on its own (`docs/perf_native_gap_v1.md` §6).
- **Size-class run hints for `gc_alloc` (2026-09-23,
  `tools/patch_gc_run_hints.py`, every console board)** took `gc_alloc` out of
  all four profiles. Allocations over 32 blocks still walk, by design. What is
  left in `mp_map_lookup` is the kid idiom's miss-before-hit, a VM question —
  do not re-price it as a cache size.
- **A play frame allocates nothing on the Python heap (2026-09-30).** The
  collector's cost is per COLLECTION -- a whole-heap mark and sweep that stops
  the frame and a compiled cart's sound with it -- so the lever is how often
  one comes, which is the console's own garbage rate: the loop, inputs, dev
  channel, Player, glue and compositor hand-off allocate zero bytes a frame,
  and the periodic diag text is written only under PERF DIAG.
  `tests/test_frame_alloc.py` pins it on the desktop MicroPython
  built in the boards' model (32-bit, REPR_C, threads under one GIL). What allocates on a
  board and reads as free: set arithmetic, a `getattr` that finds a method (a
  bound method), a tuple returned to be unpacked, `str()` of a str (a copy), a
  function holding a generator or closure (its cells are made on EVERY call,
  early return or not), `select.poll()`'s list, I2C `readfrom`, `"%d" %`, and
  `id()` of a pointer above the 30-bit small int (a P4's PSRAM). On glass,
  `gc.mem_alloc()` walks the whole heap -- tens of ms a call on a T-Deck --
  so take it over a window of seconds, never per frame.
- **FRAMESKIP and `FPS_GOVERNOR` are deleted**; the tick model (#217,
  `native/moy_play/moy_tick.c`) is the one scheduler.
- The launcher's live wallpaper defeats the redraw-on-change gate, so the tile
  grid re-renders at wallpaper rate: #66 sizes it, #73's per-surface
  compositing is the architectural fix.
- Board-specific verdicts (the PPA scale-only verdict, the double game canvas
  reverted in `26e1f9f`, sprite batching on the PPA) are in
  `.claude/rules/boards.md` and §6 of the strategy doc.
