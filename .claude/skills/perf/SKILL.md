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
  eaters. `fps=` is valid either way; the phase ms need `--diag`. Say which.
  DIAG SD LOG stays off for serial measurement (it stutters).
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
| fps and the frame split | `PERF` (`runtime/perf_line.py`, every board, ~2 s) |
| the S3 panel pump, draw batches, loop hitches | `device_diag`'s `DRAWBRK`/`BATCH`/`DRAW2`/`LOOP`/`PUMP`/`I2CSTAT`/`WEBHOST`/`HITCH` — staged on the T-Deck alone (each board.toml says why); elsewhere `state`'s `pump`/`fold`/`ppa`/`stages` read the same C meters |
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
- **FRAMESKIP and `FPS_GOVERNOR` are deleted**; the tick model (#217,
  `runtime/tick_model.py`) is the one scheduler.
- The launcher's live wallpaper defeats the redraw-on-change gate, so the tile
  grid re-renders at wallpaper rate: #66 sizes it, #73's per-surface
  compositing is the architectural fix.
- Board-specific verdicts (the PPA scale-only verdict, the double game canvas
  reverted in `26e1f9f`, sprite batching on the PPA) are in
  `.claude/rules/boards.md` and §6 of the strategy doc.
