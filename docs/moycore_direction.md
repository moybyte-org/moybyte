# Moycore: the living direction (2026-08)

**The full record is `docs/history/moycore_plan_2026-08.md`** — 1,273 lines of
staging, arithmetic, measurements and superseded premises, archived 2026-08-27
because its ladder is walked and an agent reading it inherits a month of dead
sequencing. **The tracker is #192.** This file is what is still binding: the
contract a future crossing must honour, the decisions that outlive the stages,
the rows still open, and the gates that can still kill a claim. Numbers live in
**#66** (and **#58** for the P4); #66 wins on conflict, always.

## 1. What the engine IS now

A Lua cart's whole frame runs inside libmoy: `moycore.tick(dt)` calls `_update`
and `_draw` back to back with no return to Python. MicroPython is the shell and
is not the engine. There is exactly **one** Lua runtime on every tier — boards,
browser, host — and `import moy_lua` is meant to fail.

The ladder that got there, one line each, because none of it is direction any
more: stage 0 host-embedded audio `ff69071` (2026-08-11) · the cheap M0 levers
`87164a5` (2026-08-11) · the pal() gate-table fix `a791c33` (2026-08-12) · stage
4, the wasm head re-rasters and the streaming stack dies (2026-08-12) · the
second Lua runtime deleted, −1,907 lines (2026-08-13) · both boards flashed and
measured on glass (2026-08-13; the S3 is where it paid, celeste p50 30 → 43) ·
`spr_batch`/`rect_batch`/`spans` deleted from the cart API (2026-08-14) · lupa
deleted (2026-08-14) · the host raster binds libmoy — runtime/canvas.py deleted,
`runtime/host_canvas.py` over `runtime/gfx_binding.py` (2026-08-15) · the §3.4
sync RPC ships as `runtime/moy_sync.py` (2026-08-25).

## 2. The trace-vocabulary contract — the one rule that gates the next crossing

`tests/test_semantic_traces.py` is the pin (paid `6fd3d29`, 2026-08-11). Pixel
conformance sees the raster and cannot see semantics: btnp edges, pmem sign
wrap, the pal() reset form, the order the audio backend hears its verbs. The
harness replays one scripted trace down both cart paths — the real moycore glue
against the real `device_api.make_api` closures — under the unix dual-usermod
MicroPython, and compares per-frame canvas hashes, the observation log, the
audio command order and the final pmem image.

**The rule: a crossing extends the vocabulary BEFORE it crosses, not after.**
The harness gates only what its traces exercise, so a verb moved outside the
vocabulary is a verb moved without a pin. In practice that means: add the twin
cart lines (both languages, line-faithful), mutation-test them — change the
thing the trace claims to observe and watch the suite go red — and only then
move the implementation. A trace that observes a value which does not depend on
the thing being tested passes for the wrong reason; two of the 2026-08-12
extensions failed that test first.

**Known gaps, so nobody mistakes the pin for wider than it is.** Covered today:
cls default form, map with colorkey and scale, clip clamped both directions,
camera's return value read back, pal past index 63 and a repeated tint, palt
un-setting, the 2-arg `pix` read, btn/btnp edges, pmem wrap, audio order. NOT
covered: `key`/`keyp`, `touch`/`mouse`, `textmode`/`quit`/`view`, the
player-slot form `btn(name, p)`, scenes/tables/texts, the exit-time state
read-back, and non-draw-lane **liveness guards** — the draw lanes assert they
ran through C, and an input or audio crossing needs the analogous proof or it
can pass vacuously.

**Input MAPPING is a separate net and stays one** (`tests/test_moy_button_order.py`,
2026-08-14): the bit order is libmoy's ABI, read out of `moy.h` rather than
restated, and checked behaviourally against both InputState classes. A frame
hash cannot see a rotated d-pad — the bug that shipped to both boards for a day
was invisible to every other net in the tree, this harness included, because it
builds the host InputState.

**The pin has teeth in CI since 2026-08-17.** Every suite that drives the real
native modules resolves the binary through `tests/unix_mp.py`, which warns
locally and FAILS under `CI`/`MOYBYTE_REQUIRE_UNIX_MP`. The plan's accepted
caveat — "teeth on the dev machine and none in bare CI" — is closed; keeping
`make unix-micropython` alive is still part of the pin.

## 3. Decisions that outlive the stages

- **Zero duplication** (owner, 2026-08-12 late): everything that can be moycore
  is moycore. The one accepted exception is Python carts' `make_api` verb
  surface, kept because blocks graduate to Python (owner, 2026-08-12, the
  MakeCode model, #111) — and kept honest by the semantic traces, not by
  discipline. Blocks→Lua is declined, not open.
- **A de-duplication is only safe when the two things are the same thing**
  (2026-08-14, learned from the button-order bug). Folding two lists into one
  because they look alike is how a protocol property becomes silently wrong.
  The cure shape: make the differing thing a *required* argument, so forgetting
  is a `TypeError` — `lua_ext.button_masks(order)` is the model.
- **Whatever routes work to moycore, assert that something ARRIVES** — per cart,
  by name. Two gate bugs cost this lesson twice: a substring scan that
  disqualified every cart in the tree, and a regex lookbehind CPython accepts
  and MicroPython's `re` raises on, which shipped green and made moycore
  unreachable on glass while everything looked healthy. An untaken path is the
  failure mode this class of change is most likely to have, and a fallback is a
  *successful* start — so assert on the run's TYPE.
- **The superset rides moycore; there is no split gate.** The 2026-08-13
  layers/images call ("they stay Python-side") was correct about the
  implementation and wrong about the consequence: routing superset carts to a
  second runtime left two engines implementing the spec verbs. Every Lua cart
  runs `MoycoreRun` now; moybyte's non-spec verbs are REGISTERED on top of
  libmoy's table as trampolines to the same closures, and the object-valued
  three (`make_layer`/`draw_layer`/`image`) ride int handles plus a Lua prelude
  because a trampoline marshals scalars and tuples. A cart needing a
  Python-backed verb does not need a second engine.
  **What would reopen C layers:** a Lua cart that makes layers per-frame-HOT —
  `draw_layer` inside a sprite loop, or many layers composited per frame. Today
  the census is one cart at one blit per frame, and the cost of C layers is a
  second console in C (each moybyte layer is a full canvas with the whole verb
  table bound to it).
- **Registration is a DENY list, not an allow list** — what is stable and
  enumerable is what libmoy OWNS. One definition, `runtime/lua_ext.py`, imported
  by every runtime including the host's; an allow list silently drops any verb
  nobody remembered to add, and did.
- **The spec-surface core grows UPSTREAM.** Raster, audio, VM and loop are
  libmoy's, in the moy-spec repo; `native/moycore` vendors it and owns only what
  is NOT spec — scenes/tables/texts glue, pmem, net, textmode/quit/view. That is
  audio's shipped shape. Fixes belong upstream and come back through the vendor
  target; editing a vendored file in place is a red test.
- **Presentation stays per-board and outside moycore.** Moycore renders the cart
  canvas and is, in `docs/surface_model_v1.md` terms, the game surface's
  *producer* — Class B while running, composited by each backend's own shipped
  strategy (fold on the S3, PPA on the P4, framebuffer blit in the browser,
  plain blit on the host). It owns no glass and adds no invalidation mechanism.
  Live-edit beside a windowed run is snapshot-shaped: moycore watches
  `sheet.gen` at the tick boundary and re-pulls the blob on change.
- **Perf guardrails.** The boards build libmoy `MOY_PIXEL_RGB565`; the
  indexed-vs-565 A/B was settled on P4 glass 2026-08-05 and moycore does not
  reopen it. The `lua_Alloc` internal-SRAM-first policy and the `-O2` pins on
  the VM carry.

## 4. Open rows

- **The exit-time state read-back is unpinned.** camera/clip/pal ownership moves
  to moycore for the duration of a run and the shell reads back at exit; the
  traces reset state per frame and never observe that read. Extend before
  anything touches it.
- **How a two-sided sync collision PRESENTS to a kid is undesigned.** The rule
  is settled — per-file last-writer-wins plus the journal, and the journal lives
  with the store of record (owner, 2026-08-25), so an overwritten version is one
  UNDO away on the glass it was made on. That turns the worst case from silent
  loss into a recoverable surprise, which is not the same as designing the
  surprise away. The wasm-mode switch shrinks the window further — a parked
  console does not author — but PLAY ON DEVICE still writes pmem under a live
  browser copy. Pinned for convergence, not for UX, by
  `tests/test_sync_convergence.py`.
- **The S3's biggest remaining lever is not the engine.** Chrome and internal
  SRAM are, and both want a re-measure under the microsecond brackets that
  landed 2026-08-14 — the figure the plan quoted was taken with the old
  millisecond ones. Numbers and the re-measure belong in #66.

## 5. What can still kill a claim

- **The pin surface not keeping up.** If a crossing lands outside the trace
  vocabulary, moycore recreates the parallel-implementation disease at the
  semantic layer while deleting it at the mechanical one — a net loss the whole
  staging rule exists to forbid. No crossing lands without its pin. That is the
  line, and it is the only one the walked ladder did not retire.
- **A measured regression.** Every crossing to date measured fps-null on the S3
  and held the P4 cap. A change that measures worse stops there until explained;
  per-board verdicts do not transfer, and a unix microbench is not a board.
- **Closed gates, recorded so they are not re-run:** M0's attribution and
  celeste re-read (2026-08-12) — the deletable-by-moycore share measured
  ~2–4ms against a predicted 4–8, so the fps case shrank to its floor and §4.2
  maintainability carried the build alone; stage 4's feasibility spike
  (2026-08-12) — libmoy via emcc priced ~500–1,000× the interpreted host raster,
  closing the gate in the sunset's favour; the screenshot verb on the sync RPC
  (DROPPED, owner, 2026-08-25) — the wasm head covers the show-and-tell jobs
  because the browser IS the console, and the capture job dies with the mirror
  as the sunset priced in.
