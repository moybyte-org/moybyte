# State-verb cost attribution (2026-08-12)

M0(a) priced Lua state-verb trampolines at ~0.55ms/call on the S3 **by
difference** (#66's attribution table) — a lumped number that couldn't say
whether the cost was the Lua→MP crossing or the verb body. The question
mattered because the moycore stage-1 "state-verb ownership flip" (plan §6)
was justified by that slice. `bench.py` splits it on the unix dual-usermod
build (the same VM `tests/test_semantic_traces.py` trusts).

## Result: the crossing is ~1%, the body is ~99%

unix MP, µs/call, medians (x86 µs — the RATIOS are the deliverable):

| measure | before fix | after fix |
|---|---:|---:|
| empty registered verb from Lua (pure crossing) | 0.07 | 0.07 |
| `camera(4,2)` body | 1.12 | 1.12 |
| `clip(...)` body | 1.73 | 1.85 |
| `pal()` reset body (identity steady state) | **5.03** | **0.63** |
| `pal(9,3)`+`pal()` sandwich, per call | 5.43 | ~5.0* |
| `reset_state` after a pal touch | 11.25 | 2.59 |

A moycore ownership flip for camera/clip/pal would delete the 0.07µs
crossing and would have to re-implement the 5µs body in C — the in-place fix
(incremental gate-pal sync, `device_canvas.py`) deletes the body cost with no
new seam. `pal()` was expensive because **every call rebuilt the draw gates'
64-entry RGB565 palette table in an interpreted loop** (`_sync_gate_pal`),
including the celeste shim's once-per-frame reset of an already-identity map.
The fix pokes one entry per remap and slice-copies a prebuilt identity table
(`_PAL565_WIRE_BUF`, a C memcpy) per real reset; `_sync_gate_pal` survives as
the cold seed-time rebuild. Pinned by `tests/test_gate_pal_sync.py` (gate
table == full rebuild across a 4000-op random walk).

## *The lab hazard found on the way: the big-function cliff (unix MP)

The post-fix sandwich number refuses to improve on unix even though every
piece of it measures <1µs — and a bisection showed why in an unexpected
place: the SAME body costs **~1µs/call as two small functions** and
**~5µs/call as one large function**, no matter WHICH piece is removed to
make it small (removing the delta bookkeeping, the slice copies, the
flush_batch call or the state-id call each collapse 5µs → <1µs; defaults and
locals count are innocent; no hidden allocation — 32B/pair, just the slice
objects). The shape is consistent with `MICROPY_OPT_MAP_LOOKUP_CACHE`
(128-entry direct-mapped, keyed on (map, qstr)) thrashing when a hot
function's attr/global working set collides — enabled at
ROM_LEVEL_EXTRA_FEATURES, i.e. on the unix build AND both boards, but
collision patterns depend on per-build qstr numbering, so nothing here
transfers across builds. NOT proven (that would need a cache-off rebuild);
recorded as a hypothesis with the evidence above.

Two consequences, both recorded so they don't get re-derived:

1. **Interpreting unix-MP microbenches of LARGE functions is hazardous** —
   a 4µs/call artifact swamps the effect being measured. Bench small
   functions, or cross-check any large-function number on glass.
2. A dispatcher split of `pal()` (thin default-arg wrapper calling
   `_pal_set`/`_pal_reset` helpers) measured 5× faster on unix — but an
   extra Python call layer costs ~5µs on-glass (#63's empty-method number)
   and the artifact may not exist there, so the split is queued as an
   on-glass A/B, not shipped. Per-board verdicts don't transfer (#66/#77's
   own -O3 lesson).

## S3 expectation (PREDICTED, needs glass)

celeste's shim resets pal once per frame and sandwiches around tinted draws;
m0_state's mix (3 camera + 2 pal + 1 time per frame) measured the state
slice at ~1.5–3ms. The 64-loop was the dominant share of each pal call, so
the fix should roughly halve that slice — re-run the M0 state-vs-input
variant delta on the T-Deck to confirm (the staged carts in the M0 kit).
