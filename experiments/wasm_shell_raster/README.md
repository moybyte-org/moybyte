# wasm shell raster spike (moycore stage 4, docs/moycore_plan_2026-08.md §3.2)

Prices the two candidate substrates for a wasm-side SHELL raster at desktop
size (1024×600), with real frame numbers, against the plan's gate: *"both
options price out unplayable → the sunset has no completion path."*

- **Option (a)**: the host sim's pure-Python indexed canvas (runtime/canvas.py
  at the time; deleted 2026-08-15) interpreted by the REAL dist wasm MicroPython
  (`firmware/web_runner/dist/`).
- **Option (b)**: the vendored libmoy C kernels
  (`native/moy_gfx/libmoy/`, indexed
  8-bit pixel build — the default) compiled to wasm with the web runner's own
  emsdk.

## Methodology

One shared workload definition, implemented op-for-op twice
(`bench_canvas.py` / `bench.c` — same geometry, same strings, same 16×16 icon
transparency pattern). Cross-check: all three runs print the same sampled
framebuffer checksum (`sum8=52`), so they drew the same pixels.

- **desk**: full-canvas `cls` wallpaper + 6 windows (420×320 fill + `rectb`
  frame + 18px title strip) + 300 glyphs of 8×8 `print` + 40 16×16 sprites.
- **editor**: full-canvas fill + 1,600 glyphs (40 lines × 40 chars) + 60 thin
  rects (row rules / scrollbar / cursor).
- **drag**: full-screen 1:1 backdrop restore + one window restamped.
  Python uses `blit_strip(layer)` — the WM's actual full-restore idiom.
  **C uses `memcpy`** (libmoy has no 1:1 layer-blit verb; memcpy is the C
  idiom for it).
- **present** (C option): indexed 1024×600 → RGBA8888 via a hand-rolled
  64-entry-LUT loop (what a page `putImageData` needs).
- **micro**: per-verb µs (cls / rect 420×320 / rectb / print 100 glyphs /
  spr 16×16 / 200px diagonal line).

3 warmup + 30 timed frames, median + p90. Micro: median of 7 batches.
Sprite note: libmoy's `moy_spr` draws one 8×8 tile, so the C 16×16 icon is
`moy_sspr` of a 16×16 sheet region at 1:1 (per-pixel colorkey path, same skip
pattern as the Python `Image`).

Runs (all on: AMD Ryzen 5 5600X, Node v22.22.3, emcc 6.0.4, CPython 3.10.12,
dist wasm MicroPython v1.28.0 built 2026-08-06):

```sh
PYTHONPATH=../.. ../../.venv/bin/python bench_canvas.py   # CPython baseline
node bench_wasm.mjs                                       # option (a): real dist VM
./build.sh                                                # option (b): emcc -O2/-O3 + node
```

`bench_wasm.mjs` stages the pure-Python indexed canvas into `/modules`
(shadowing the frozen copy, like a `--stage-only` dev dist); its
font/palette/editors imports resolve to the frozen twins. **It no longer runs
as written**: option (a)'s subject was deleted with the host raster, so this
directory is a record of the measurement, not a live bench.

## Numbers (2026-08-12)

Full frames, median ms (p90):

| workload            | CPython (baseline) | wasm-MP — option (a) | wasm-C -O2 — option (b) |
|---------------------|-------------------:|---------------------:|------------------------:|
| desk repaint        |        6.45 (7.91) |          37.0 (39.0) |            0.06 (0.10)  |
| editor repaint      |        8.42 (8.69) |          49.0 (57.0) |            0.04 (0.05)  |
| drag frame          |        0.44 (0.50) |            2.4 (2.5) |            0.05 (0.06)  |
| present idx→RGBA    |                  — |         0.89 (proxy¹)|            0.42 (0.42)  |

¹ Option (a) still needs a present. A Python loop over 614k pixels would be
seconds, so the realistic shape is page-side JS over the wasm heap; the 0.89ms
is that identical JS LUT loop over a same-sized Uint8Array. **The
buffer-export plumbing is NOT built — proxy number.**

Per-verb micro table, µs/op:

| verb                | CPython | wasm-MP |  wasm-C -O2 |
|---------------------|--------:|--------:|------------:|
| cls (614k px)       |     834 |   4,420 |         5.9 |
| rect 420×320        |      47 |     153 |         2.9 |
| rectb 420×320       |      92 |     303 |         3.0 |
| print 100 glyphs    |     680 |   3,740 |         2.5 |
| spr 16×16           |      66 |     457 |        0.36 |
| line 200px          |      58 |     387 |        0.47 |

`-O3` is a wash vs `-O2` (frame medians 0.04–0.09ms both ways, differences at
noise level — consistent with the boards' O2 verdict).

### Honesty / limitations

- The wasm port's `time.ticks_us` has ~1ms granularity; micro batches were
  sized ≥20ms and the drag frame timed in reps of 10, so quantization error is
  a few % at worst.
- This prices the RASTER only. Under option (b) the shell's Python widget
  logic still issues each verb across the MP→C binding (~100–150 calls for
  these frames); binding dispatch is NOT included in the C numbers. Even at a
  pessimistic 5µs/call that adds <1ms/frame — it does not change the verdict,
  but per-verb C numbers this small mean dispatch, not pixels, would be the
  real cost of option (b), exactly like the P4.
- Neither option's number includes the shell's own Python layout/logic cost
  (identical for both options, and today's recording canvas pays it too).
- Ryzen 5600X + V8 is a fast desktop. A weak laptop/tablet could plausibly be
  2–4× slower; that margin matters for option (a) (37→100ms+) and is
  irrelevant for option (b) (0.1→0.4ms).
- The C drag restore is `memcpy`, not a library verb (said above); the C
  bench runs no allocator and no GC, the MP bench pays MicroPython's.

## Verdict

**Option (a) — canvas.py under wasm MicroPython — is marginal.** A full desk
repaint is 37ms (27fps-equivalent) and a full editor repaint 49ms — at the
edge of the ≲50ms typing/scroll bar and over the 33ms drag budget, on a fast
desktop. Drags survive only because the WM's retained-backdrop restore frame
is 2.4ms — i.e. option (a) is playable *only if* the shell keeps its whole
partial-repaint discipline (dirty gate, scroll-as-blit, backdrop cache), and
it has no headroom on slower client machines.

**Option (b) — libmoy compiled to wasm — is trivially playable.** Full
repaints are 0.04–0.1ms plus a 0.42ms present: ~500–1,000× faster than option
(a) on full frames, three orders of magnitude inside the 33ms budget. Even
with MP→C dispatch overhead added it stays sub-2ms/frame, and a 10× slower
client is still at 60fps with room to spare.

**Gate answer: the sunset HAS a completion path.** Neither option prices out
unplayable, and option (b) is not close — a `moy_gfx`-style usermod (the same
vendored sources the boards compile, minus the exclusion in the wasm build)
gives the browser head a shell raster with desktop-class headroom for the cost
of a present loop. Option (a) is a workable stopgap but a dead end at scale
(bigger canvases, weaker clients); if the streaming stack is sunset, compile
the kernels.
