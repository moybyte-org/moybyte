# UI damage model v1 — one invalidation mechanism instead of six

**Status: REVIEWED AND DOWNGRADED. Do not build §5 phases 1-3.**
**Date:** 2026-07-26, revised the same day after an adversarial architecture + perf
review. Numbers measured on P4 glass (1024×600) unless stated.
**Tracks:** #58 (P4 port), #66 (performance ledger), #113 (kinetic scroll), #73 (windowed WM).

---

## 0. Review outcome — read this before anything else

The review killed the performance case and left the maintainability case standing.
Both halves matter, so both are recorded.

**The perf claim is NOT established.** §1.1 argued the Editor tabs' 76-92ms is
"chrome that did not change", from the observation that the cost is identical on an
empty cart and on the richest cart on the device. That experiment had **no power**:

- Every one of those draw loops iterates over the **viewport, not the content**.
  `map_editor_ui.py:886-904` issues `rect` + `rectb` for every visible cell whether
  occupied or not (only the `spr` at :903 is content-gated); the tile palette draws
  from `sheet.count`, and a blank `SpriteSheet` still reports 256 tiles;
  `code_layer.py:887` draws `len(visible_lines())` rows regardless of file length.
  So identical numbers were **structurally guaranteed** either way.
- The two maps on the device are 20×13 and 15×15 against a 26×15 viewport, so
  `map_editor_ui.py:427-428` pins the camera at (0,0) and a pan is a **no-op**. The
  "richest cart" had 620 bytes of assets. Both samples were effectively empty.
- And content-independence says nothing about whether the pixels *changed* — which
  is the question invalidation answers and the one never asked.

**A cheaper lever with better evidence was found in the same code.** `map` and
`paint` (and the scene pane, which is why `blocks` is 88ms) fill `lay.body_fill`
and then immediately fill `lay.panel` **in the same colour**:

    cv.rect(*(lay.body_fill + (th["surface"] if light else NAMES["black"],)))
    cv.rect(*(lay.panel     + (th["surface"] if light else NAMES["black"],)))

`map_editor_ui.py:861,864` and `paint_layer.py:686,690`. `panel` covers `body_fill`
except two 8px columns and a bottom strip, so **~94% of ~450K px is written twice**
— ~848KB of redundant writes per frame, and `moy_gfx_fill_run` is a cached store
loop, so on PSRAM write-allocate genuinely doubles a fill's traffic.

This exact bug was already fixed in **Settings** during this session, and its own
comment says so (`settings_layer.py:966-968`: *"re-filling the panel rect would
paint 272k of the same pixels a second time, ~9ms of pure duplicate fill"*). So
Settings' 24ms — cited in §1 as proof the fixes worked — was bought by **deleting
an overdrawn fill**, i.e. by the cheap lever this doc proposed to replace with an
architecture. `docs/perf_native_gap_v1.md:49-54` had already named overdraw as the
structural cost. This doc failed to cite it.

**Prior art this doc should have cited and did not:** the same bet — infer what is
static, retain it, repaint the rest — was already tried and **reverted** in this
codebase as the *Fold-2 auto map cache* (`perf_native_gap_v1.md:176`), because
inferring "static" under a free-form drawing API cost more than it saved. That is
the most likely failure mode of §5 and it has precedent here.

**What survives:** §2.1's argument. Six hand-rolled partial re-derivations of
invalidation, two of which produced the same silent-key bug, is a real
maintainability defect independent of any millisecond. Consolidation is defensible
on bug-class grounds alone — which §6 already concedes promises no speed-up.

### 0.05 Where the attribution left it — the lever is a native grid kernel, not damage

Step 3 above measured the worst surface for the first time, and it redirects the
plan a second time. The map tab's 80ms is **~960 native fills and ~50% Python
dispatch**, i.e. the CELL GRID itself, not chrome drawn around content. Two
consequences:

- **Invalidation is the wrong tool here.** It pays only when the region is
  unchanged, and on a map larger than the viewport a pan changes every cell. On the
  maps currently on the device the camera is pinned (§0), so a damage experiment
  would have looked like a large win for a reason that reverses on real content —
  exactly what the review predicted of §6's phase-2 gate.
- **The right lever has precedent in this codebase.** ~1000 MicroPython→C calls per
  frame is the same shape as the sprite problem, solved twice already: `spr_batch`
  (#43) and the native `spr_gate` (#63) collapsed N per-item calls into one. A
  **native grid kernel** — one C call that fills the cell grid straight from the
  tilemap, as `blit_map` already does for cart maps — attacks both halves at once:
  it removes the dispatch (53%) and lets the kernel fill spans instead of 960
  separate rects.

That is the next thing to build. It is a kernel, not an architecture, and it is
testable the same way everything else here was: `p4_surface_sweep --only map`
before and after.

### 0.06 The rest of the attribution: paint and blocks (2026-07-27)

Paint and blocks were then attributed the same way, with **real content** —
Coin Quest (16 block rows, 23 scene actors) and Battle City (a full 15×15
tilemap) rather than the first cart on the shelf, whose outline and map are
empty. That is not a detail: the map tab costs **74.4ms on a populated map vs
65.0ms on an empty one**, because the 960 grid fills are a floor that the
per-tile `spr` calls are then added to. Every number below is per painted frame
during a drag, from `tools/p4_attrib.py`.

| phase | wall | native fills | in kernel | dispatch |
|---|---|---|---|---|
| `map._draw_map` (Battle City) | 74.4 | 960 | 31.7 (43%) | 42.4 (57%) |
| `map._draw_map` (empty map) | 65.0 | 960 | 32.4 (50%) | 32.2 (50%) |
| `block_ui._draw_blocks` | 86.9 | 409 | 20.7 (24%) | 65.2 (**75%**) |
| `paint_layer.draw` | 51.8 | 543 | 24.1 (46%) | 27.4 (53%) |

Three things fell out that the §1 table could not have shown:

- **The blocks tab is not a blocks tab.** Its dominant phase is
  `scene_ui._draw_scene` at 47.9ms — the interactive scene pane it hosts on a
  wide canvas (#93/#85). The outline itself is 13.3ms over 16 rows and the tab's
  own chrome 10.6ms. So "blocks is slow" is really "the scene renderer is slow,
  and two tabs pay for it".
- **Two of the biggest phases belonged to neither surface.** `chrome._blit_glyph`
  cost 48µs a call — ~35µs of Python walking a 12×12 mask bit by bit behind
  ~1.5µs of pixels — and Paint draws 19 glyphs a frame: 13.1ms of a 51.8ms tab.
  `block_ui._layout_workspace` ran **4.3 times per painted frame** (the draw plus
  every pointer event in the gesture), rebuilding both pane layouts each time:
  5.5ms. Both are now memoised (`c422a12`), which is what took sprites 64→52ms
  and blocks/map 80→76ms whole-frame. Neither needed damage tracking, a new
  primitive, or C.
- **The dispatch share is the whole story, and it rises as the fills shrink.**
  Blocks issues 409 native fills to map's 960 yet spends *more* absolute time in
  Python (65ms vs 42ms), because `spr` is not gate-counted and the per-call
  overhead does not care how few pixels a call moves. Across all three surfaces
  the kernel never exceeds half the wall.

That last point generalises §0.05's conclusion beyond the map tab: **every
measured editor surface is dispatch-bound, not fill-bound.** A damage model
removes *regions*; these surfaces need to remove *calls*. The two levers that
follow from the numbers, in order:

1. A **native span-batch verb** (`fill_rects` over a packed array), which is the
   generalisation of the map grid kernel — it also serves the glyph spans, the
   Paint palette/tool row and the scene tile palette.
2. The **scene renderer**, which is now the single most expensive phase on the
   device and is paid twice (Scene tab and Blocks tab).

### 0.065 "Move the GUI to C?" — no, and here is the measurement

The obvious reading of §0.06 is "50–75% is Python, so put the GUI in C". Measured
on glass 2026-07-27, that is wrong on both halves.

**The MP→C transition is not the cost.** A gated `cv.rect` call with constant
arguments costs **1.2µs** (empty MicroPython loop iteration: 1.27µs; the loop plus
the call: 2.5µs). Computing the arguments adds ~0.9µs, reading them off a layout
object ~0.8µs, an unhoisted `cv.rect` attribute lookup ~0.6µs. Nothing here is a
40µs call.

**The kernel time is memory, not code.** 960 map-cell fills (28×17 = 476px, 914KB
in total) at 1024x600:

| how the same 914KB is written | cost |
|---|---|
| one 896×510 fill | 13.4ms (= 68MB/s, the PSRAM write ceiling) |
| 510 full-width rows (896×1) | 14.3ms |
| 960 grid cells, row-major | 22.4ms |
| 960 cells scattered | 23.0ms |
| 960 cells all at the same spot (cache-hot) | 4.6ms |

So the map grid's ~22ms is **13.4ms of irreducible bytes** plus ~8ms of penalty
for writing 28px-wide strips across a 2048-byte stride, plus ~1ms of call
overhead. C cannot remove the floor; a *row-contiguous* kernel could recover most
of the 8ms. And if every microsecond of Python vanished from the map tab it would
go 70.7 → ~32ms — still 2× over budget, because the bytes remain.

**A caveat on §0.06's "dispatch" column:** it is wall minus *gated* kernel time,
and only `rect`/`rectb`/`print`/`pix` are gated. `spr`/`line`/`circ` are native
work booked as dispatch. It is therefore "everything that is not a counted fill",
not "Python".

**Tried and reverted: a lookup-free `spr` preamble.** A microbenchmark put
`cv.spr` for a cached 8×8 tile at 92µs against a 19µs C blit, apparently 73µs of
preamble (five `getattr(img, name, default)` guards ≈ 24µs, an empty
`flush_batch()` call ≈ 11µs, plus `int()`s and nine attribute reads). Replacing
the guards with direct attribute reads behind `try/except AttributeError` (plus
class-level defaults on `Image`) made the map tab **10ms SLOWER** on glass — 70.7
→ 81.4ms, repeatable to ±1ms. The editors blit image objects that are not all
`Image` instances, so the AttributeError *fires*, and a raised exception in
MicroPython costs far more than the five getattrs it replaced. Reverted.

Two lessons, both cheap to re-learn expensively: `try/except` is a fast-path guard
only when the exception never fires; and **the microbenchmark was not
reproducible** — a second run reported 43µs for the same unchanged `flush_batch`
that first measured 11.5µs, while the 62-frame `p4_attrib` numbers repeat to
±1ms. Act on in-situ frame measurements, not on a REPL loop.

**Correction (2026-07-27, same day): lever 1 below was tried on the map grid and
is WRONG.** Three arrangements of the same pixels, measured on glass:

| map grid arrangement | wall | kernel | native fills |
|---|---|---|---|
| per-cell `rect` + `spr` + `rectb` (shipping) | **70.7ms** | 31.7 | 960 |
| 2 field fills + tiles + per-cell `rectb` | 81.3ms | 48.1 | 572 |
| 2 field fills + tiles + full-length rules | 96.4ms | 65.6 | 264 |

Restructuring cut the call count by 73% and the dispatch by 8ms, and made the tab
**26ms slower**, because the kernel time DOUBLED. Two memory effects the fill
benchmark above did not model:

- **Temporal reuse.** In the per-cell loop a cell's `rectb` writes lines its own
  `rect` wrote microseconds earlier — warm. Fill the whole field first and the
  border pass re-touches every line cold. That alone is +16ms.
- **Vertical rules are pathological.** A 1px × full-height rule is ~500
  consecutive partial-cache-line read-modify-writes at a 2048-byte stride. Two
  per column is worse than the same pixels drawn cell-locally.

So the "22.4ms of strips vs 14.3ms of rows" table measures a *single pass in
isolation*; it does not license reordering passes. **The map grid is already in
the best shape this memory system will give**, and the byte floor is not the only
memory effect — locality between a write and its reuse is another. Reverted.

Verification note, since it cost more than the change: the pixel-equivalence
harness must run the oracle **inside the frame**, right after the real renderer,
and assert it changes zero bytes of the canvas. Two earlier versions drew the
oracle onto a twin canvas (or onto the real one after the frame) and compared
regions — both failed against the UNMODIFIED renderer, because the viewport
origin/clip/pal the renderer runs under do not survive the frame. A harness that
fails on known-good code cannot certify anything; check that first, always.

**What this leaves as the levers**, in order:

1. ~~**Fewer, wider spans** — the native grid kernel, for the contiguity as much
   as the call count (est. 22 → 14ms on the map grid).~~ **Disproved above.** A
   native kernel could still win by writing row-contiguously *without* losing the
   reuse (compose each output row once, cells and lattice together), but that is
   a much narrower claim than "fewer, wider spans", and the measured baseline it
   has to beat is 70.7ms, not 96.
2. **Retained widget surfaces** — Paint's tool row, the palette, the bar strip are
   static across frames; caching *pixels* removes the dispatch and the bytes. The
   bar strip cache already proves the mechanism.
3. Not redrawing at all: the redraw gate exists, the editors just don't use it at
   tab granularity.

The pattern this codebase has actually won with five times (`spr_batch` #43,
`spr_gate` #63, `blit_map`, `decode_runs`, `blit565`) is not "move code to C" — it
is **raise the verb's level so one call does what N did**, keeping the Python
description portable. Rewriting layers in C would fork `runtime/`, and host ==
device is the invariant the whole console rests on.

### 0.07 Method note: measure content, not the first cart on the shelf

Three separate runs in this campaign measured an empty screen and drew a
conclusion from it — the Writer/Sheets file-grid rows in `p4_surface_sweep`, the
blocks outline, and the Paint tab's sprite 0 (blank on most carts; Coin Quest's
art starts at index 1). `p4_attrib` now prints what is actually on the surface
(block rows / scene actors / map dims / lit pixels in the open sprite) above
every table, and picks a non-blank sprite, because a number whose content is not
stated is not reproducible. An empty editor is not a cheap editor — it is a
different screen.

### 0.1 Corrections to specific claims below

Left in place so the errors are visible rather than quietly deleted:

| claim | correction |
|---|---|
| "25× fewer pixels at 320×240" | **8.0×** (614,400 / 76,800). Against the actual editor window, 6.1×. |
| picker band "874×429, 750KB" | that is the **fullscreen Library**, not the windowed picker (764×372, 555KB). Different surface, different `RETAINED_FRAMES`. |
| `scroll_rect` "~148MB/s counting write-allocate" | the drag shifts ~26px, so src and dst share cache lines — there is no separate write-allocate read. ~99MB/s, matching `p4_scroll_ab.py:13`. The PSRAM ceiling was never measured. |
| "scroll-as-blit buys only 4.5ms" | confounded: the A/B's full-redraw arm set `RETAINED_FRAMES = 0`, which trips `_try_drag_partial`'s first gate, so it varied shift-vs-repaint **and** band-vs-whole-surface. The controlled third arm (band repaint, no shift) was never run. |
| the sweep's picker row | the grid is `ScrollRegion(horizontal=True)` (`launcher_layer.py:146`) and the sweep swipes **vertically**, so that row measured no scroll at all; its 6s drain also leaves covers cold where sibling tools use 14s. |
| Settings 24ms as proof of the fixes | confounded by window area: Settings is 407×341 vs the Editor's 894×521, a 3.36× ratio against a measured 76/24 = 3.17. Consistent with "cost ∝ filled pixels" with no chrome story needed. |
| §9 Q3 (is the PPA useful at small damage?) | already measured: a 1:1 16×16 PPA blit is **8× slower** than CPU and never crosses over (`perf_native_gap_v1.md:67-68`). Small damage makes the PPA *worse*. |

### 0.2 Architecture blockers — prerequisites, not details

§5.1 claimed the plumbing is "half-built". It is not:

1. **`clip()` has no stack and no intersect.** `clip()` with no args resets to the
   **full surface** (`canvas.py:223-242`, device twin `device_canvas.py:479-494`),
   and existing surfaces already call it that way mid-draw
   (`launcher_layer.py:519-533`, `artwork.py:806-808`). A compositor-installed
   damage clip is silently destroyed by the first Layer that clips. Fixing it means
   intersect-or-stack semantics in three implementations plus the native `DrawCtx`
   state array and the web-view wire op.
2. **`cls` ignores clip by design** ("a full-SURFACE reset", both backends) and
   *every* migrated surface opens with one. So do `blit_strip`, `scroll_rect`,
   `blit_window_from` and `blit_indices` — which are exactly the verbs the
   compositor's own restore path uses.
3. **The viewport is not half a damage clip.** `set_viewport` redefines `cv.w/cv.h`
   (`canvas.py:135-157`) and every responsive layout is built from `(cv.w, cv.h,
   fs)`, so a viewport set to a damage rect would relayout the surface for that
   rect. It also resets the clip on install.
4. **Damage must be a union over the last `RETAINED_FRAMES` painted frames.** The
   P4 is a 2-deep DPI ping-pong; every existing partial mechanism encodes the
   `>= 2` streak rule for exactly this reason. A list cleared after one paint is
   correct on the host (`RETAINED_FRAMES = 1`) and **stale on the P4** — and the
   320×240 `_base` goldens structurally cannot catch it, because that is the tier
   that keeps full repaint.
5. **On device, layer buffers cannot be freed.** `moy_alloc` has no `free()`; a
   layer returns to the pool only if minted with an `owner`, and console chrome
   passes `owner=None`. So §5.2's "it owns the buffer, the key, eviction" would
   **leak PSRAM** on every eviction (today's MRU-2 strip eviction already does).
6. **§5.2's stated rationale is already satisfied.** `ws.note_cost` is already
   wired to all three caches with budget tests. Unification would not have
   prevented the §2.1 bug — that key was wrong about the destination canvas, and a
   unified primitive still needs a per-destination key. What caught it was the
   counter plus the test, both per-cache.
7. **No damage *producer* story.** There are ~179 `_dirty = True` sites across 22
   files, some firing from input handlers that run outside `frame()`. §5.1 specifies
   only the consumer, so a surface is migrated only once *every* path that can
   dirty it produces a rect.
8. **Two damage lists, not one.** Render damage (re-run the content draw into
   `win.buf`) and composite damage (re-stamp `win.buf` into the root) are different
   questions, and every measured win of this session lives in that distinction.

### 0.3 The sequence the review argues for instead

Cheapest first, each independently valuable, none requiring an architecture.
**Steps 1 and 3 are DONE; their results are below and they change the conclusion
again.**

1. ~~**Delete the duplicate fills** on map / paint / scene~~ — **DONE** (`5dd6357`).
   `ui.fill_uncovered` paints only the part of `panel` that `body_fill` did not
   already cover in the same colour (it cannot simply be dropped: the panel
   overhangs the body by `2 * fs` at the top). On glass: **map 92 → 80ms, sprites
   76 → 64ms, blocks 88 → 80ms**, with `code` (48) and `settings` (24) unchanged as
   controls — they are exactly the two surfaces without a duplicate fill.
2. **Fix the sweep** — axis-aware swipes and a 14s warm-up — and re-derive the
   table. The picker row is currently measuring the wrong gesture.
3. ~~**Point an attribution instrument at map/paint/blocks**~~ — **DONE for map**,
   and it settles the question. Bracketing `_draw_map` with `gate_counts()`, per
   frame over 31 frames:

       wall              64.6 ms
       native fills         960 calls
       in fill kernel      30.3 ms   (47%, 31.6 us/fill)
       native texts          14 calls, 0.3 ms
       => dispatch/py      34.0 ms   (53%)

   So the map tab issues **~960 native fills per frame** — the 26×15 cell grid,
   `map_editor_ui.py:895-904`, a `rect` (+`rectb`) per visible cell whether occupied
   or not — and splits **almost exactly 50/50 between kernel time and Python
   dispatch**. (Perf capture adds ~6us/op, so kernel time is if anything
   overstated and dispatch's share larger.) Paint and blocks are still
   unattributed: the probe used the wrong object for the Paint tab's UI.
4. **Run the picker A/B's missing third arm** (band repaint without the shift) to
   learn whether scroll-as-blit should be generalised or **deleted** above a band-size
   threshold.

Only then decide whether what remains justifies a damage model — and if it does,
specify it per §0.2, with a non-vacuous phase-1 gate (phase 1 as written exercises
only the no-op path) and a `damage 0|1` A/B switch, which every comparable lever in
this codebase shipped with.

---

## 1. The problem, in numbers

Every interactive surface, measured with real content seeded on the device
(`tools/p4_surface_sweep.py`; 60fps = 16.7ms):

| surface | content | median | p90 | worst |
|---|---|---|---|---|
| editor:map | ~960 fills/frame | **76** (was 92) | 80 | 138 |
| editor:blocks | via the scene pane | **76** (was 88) | 76 | 151 |
| editor:paint | 19 glyphs/frame | **52** (was 76) | 56 | 116 |
| writer | 200-line doc | 52 | 52 | 128 |
| editor:code | 302-line cart | 52 | 60 | 130 |
| sheets | 360-cell table | 48 | 52 | 99 |
| picker | 29 carts, covers warm | 48 | 60 | 89 |
| settings | full row set | 24 | 28 | 42 |

Only Settings is close to budget, and only because it received this session's
fixes (its worst frame was 117ms before them).

### 1.1 The finding that motivated this doc — and what it turned out to be

> **Superseded by §0.05–§0.06.** Kept because the reasoning below is what the
> whole proposal was built on, and it was wrong in an instructive way.

The original claim: *the Editor tabs' cost is chrome, not content* — map 92 /
blocks 88 / paint 76 came out identical on a freshly created cart and on the
richest cart on the device, so the slowest surfaces spend ~90ms redrawing a
palette, a toolbar and a grid **that did not change**, which is by definition
what invalidation removes.

Two things are wrong with that. The experiment had **no power**: these draw loops
iterate the *viewport*, not the content, so an empty map still issues its 960 cell
fills and the comparison could not have come out any other way. And the
invariance is not even exact — a populated map costs 74.4ms against an empty
map's 65.0ms (§0.06), because the tiles are drawn *on top of* the grid.

What replaced it, from direct attribution rather than an A/B: the cost is
**per-call dispatch**, 50–75% of the wall on every editor surface. Unchanged
regions are not the dominant waste; the number of MicroPython→C calls is.

It is also why the T-Deck feels fine on the same code: at 320×240 a full redraw
moves 25× fewer pixels. The console inherited immediate-mode rendering from the
fantasy-console lineage — TIC-80 and Pico-8 redraw everything at 128×128, and
should — and that model quietly stopped fitting when the screen became 1024×600.

---

## 2. What we do today

Rendering is **immediate-mode**: each Layer redraws its whole content every frame.
Invalidation exists only at *frame* granularity — `Workstation._needs_redraw()`
decides "paint everything or nothing". There is no region concept anywhere in the
shared console.

Except there is, six times over, hand-rolled:

| mechanism | what it actually is | where |
|---|---|---|
| bar strip cache | retained sub-surface, manual key | `bar_layer._draw_top_bar_cart` |
| desk backdrop cache | retained sub-surface, manual key | `wm_windowed._ensure_backdrop` |
| chrome freeze streaks | "this region is unchanged for N frames" | `wm_windowed._chrome_streak` |
| scroll-as-blit + damage rects | region invalidation, per surface | `launcher_layer.draw_shift` |
| stamp-defer | deferred region composite | `wm_windowed._stamp_streak` |
| cover caches (runs + sized + `_cover_none`) | retained bitmap cache | `console._cover_for` |

Each was written separately, each has its own key, and each is a partial
re-derivation of the same algorithm.

### 2.1 The cost of not having one mechanism

Two of the six produced the same bug, and it cost a day to find (2026-07-26):

- **The bar strip cache** keyed on canvas *identity* while the windowed WM
  alternates destinations for the same bar (root canvas via a viewport on a quiet
  frame, window buffer otherwise). Every switch read as a canvas swap and rebuilt:
  **72ms of an 86ms Settings frame, twice per gesture.**
- **`new_layer`'s pre-collect**, meant for a cart's ~384KB world, was charged to
  the bar's 36KB strip. A collect on the P4 desk is **55ms** (mark scales with the
  live set: 488KB live → 55ms, 958KB with the picker open → 113ms). This was the
  sole source of GC pauses during a gesture.

Neither produced any signal. Nothing broke; two frames in thirty-one were five
times slower, which only shows up if you happen to measure the exact frame. Four
wrong models died before the real one (recorded in `tools/p4_alloc.py`'s header).

**A cache whose key silently stops describing reality is the recurring defect of
this architecture, not an accident.** That is the strongest argument for having
exactly one of them.

---

## 3. How established libraries solve it

LVGL, TouchGFX, emWin and Slint differ in API and target, and share one core idea:
**a retained widget tree plus region invalidation.**

- **LVGL** — an object tree with styles; each display holds an *invalid-area
  list*. `lv_obj_invalidate()` adds a rect; the refresh pass merges overlapping
  rects, then redraws only those areas, clipping every object's draw to the
  current area. A widget gets its own buffer only when it needs opacity or a
  transform.
- **TouchGFX** — the same model with dirty rectangles, aiming DMA2D (Chrom-ART) at
  the invalidated rect. Expensive subtrees cache into dynamic bitmaps.
- **emWin** — invalidation via WM_PAINT plus clipping; "memory devices" as
  offscreen caches.
- **Slint** — partial rendering with dirty regions over a line-based software
  renderer.

The invariant worth stealing: **a frame paints only what changed, and the
accelerator is aimed at that region** — not at redrawing everything faster.

Note how directly that maps onto our measurements. Our per-frame accelerator work
(the PPA composite, the async overlap) makes the *whole-surface* redraw faster. The
libraries' answer is to not do most of it.

---

## 4. Decision: why not adopt LVGL

Recorded so it is not re-litigated. **We already ship LVGL, and we already left
it.**

1. **The T-Deck build *is* `lvgl_micropython`** — but LVGL is used only to bring up
   the panel and the SPI bus (`tdeck_display.py`, plus `lcd_bus` for DMA-capable
   memory). Nothing draws the UI with it.
2. **The drawing path was LVGL once.** Replacing it with our own native blitter
   went **47 → 90 FPS**; LVGL's CPU-bound rotation was the wall. A measured
   retreat, not a taste decision.
3. **The P4 has no LVGL at all.** `firmware/esp32_p4_wifi6_touch_lcd_7b/build.sh`
   line 6: *"does NOT use lvgl_micropython (no P4/DSI support)"*. On the only board
   where these numbers hurt, "just use the library" is **first a project to port
   LVGL to P4/DSI** — precisely the work avoided by writing `moy_dsi` instead.
4. **We render the same pixels four ways**, and LVGL covers one:

   | renderer | LVGL |
   |---|---|
   | host simulator (Python/pygame) | no |
   | T-Deck (RGB565 via `moy_gfx`) | in principle |
   | P4 (DPI framebuffer) | no port exists |
   | web view (draw *commands* to a browser) | no |

   The web view works *because* we own the drawing API: it records `spr`/`map`/
   `rect` calls and ships them at ~72KB/s. With LVGL we would stream pixels —
   153KB/frame, already documented as unplayable. And "host == device, one
   codebase, identical pixels" is a stated invariant; LVGL is C with bindings and
   cannot run the host sim.
5. **The cart API is the product.** Kids draw `cls/rect/spr/print` on an indexed
   MOY64 canvas, deliberately independent of framebuf and LVGL so one `.moy` runs
   on host, both boards, and eventually a Lua VM. LVGL does not help there, and a
   *game* should be immediate-mode.

**Rejected compromise: LVGL for the P4's chrome only.** It forks the shell into two
UI implementations, doubles every future UI change, and breaks the one-codebase
invariant that makes the host simulator worth having.

**Where this decision would flip:** if the P4 were the only target, with no web
view and no host simulator, LVGL would be the right answer and building our own
would be indefensible. The four-renderer constraint and the missing P4 port are
what make it not — both concrete, neither aesthetic.

Nothing in the MicroPython ecosystem offers retained-mode invalidation in Python.
Slint needs a Rust toolchain; microui and Nuklear are immediate-mode, so they do
not address this at all.

**What we take from LVGL is the algorithm, not the dependency.** An invalid-area
list, a merge, and a clip discipline are a few hundred lines of well-understood
logic. We do not need widgets, styles, layouts, anti-aliased fonts or its input
stack — we have those, tuned to a kid-facing product with byte-identical frozen
pixels at 320×240.

---

## 5. Proposal

> **Superseded by §5.0.** The three primitives below were specified before the
> attribution and before §0.2's blocker list; §5.0 is what the numbers and the
> blockers together actually argue for, and it is roughly a tenth of the work.

### 5.0 What to build instead: surface-granularity damage (2026-07-27)

**The mechanism already exists — it is just not reached for the focused window.**
`wm_windowed.py:1352` reads `if focused and not moving:` and only then re-runs the
content draw. A background window, or one being dragged, is already stamped from
its retained `win.buf` without re-rendering. So the console already has retained
per-window surfaces, a compositor that stamps them, and a two-frame streak rule
for the ping-pong. What it does not have is a reason to skip the FOCUSED window's
content draw when that content did not change — and that is the 70.7ms.

**The arithmetic that makes it worth doing** (correcting §0.065, which compared a
retained blit against the byte floor rather than against the real cost):

| the focused editor window, per painted frame | cost |
|---|---|
| re-running the content draw (map tab) | **70.7ms** |
| stamping the retained `win.buf` 1:1 | **13.7ms** |
| stamping nothing, because nothing moved | ~0 |

So the win is up to ~57ms on any painted frame whose content is unchanged — and
those are common: the clock ticking, the cursor moving, another window dirtying,
an FPS chip updating. Today every one of them re-renders the focused surface in
full.

**Why this shape and not §5.1's damage clip.** It sidesteps four of §0.2's eight
blockers outright, because there is no damage *rect* pushed into drawing code:

- **#1 (`clip()` has no stack), #2 (`cls` ignores clip), #3 (the viewport is not a
  damage clip)** — all moot. Nothing installs a damage clip. A layer draws its
  whole surface into its own buffer exactly as it does today, `cls` and all.
- **#7 (no damage producer; ~179 `_dirty` sites)** — the producer already exists.
  At surface granularity the question is only "did this content change", which is
  precisely what `_dirty` already answers. No rects to derive, no sites to audit.
- **#8 (two damage lists, not one)** — this IS that split, and it names them:
  RENDER damage is a per-surface boolean; COMPOSITE damage is the WM's existing
  rect union and streak machinery. They stay separate.

Still live and must be designed for:

- **#4 (union over `RETAINED_FRAMES`)** — the composite half still needs the `>= 2`
  streak; the WM already implements it (`_stamp_streak`, `_full_debt`).
- **#5 (`moy_alloc` has no `free()`)** — retained buffers must be minted with an
  `owner` and reused per window, never evicted-and-reallocated.

**And the one real risk, which decides the gate:** a surface that animates without
setting `_dirty` would freeze. Candidates to check before switching anything on —
the code editor's caret blink, the music playhead, a running cart window (already
special-cased via `_PlayerWindowLayer`), and any status text on a timer. The
conservative first gate is therefore NOT "content not dirty" but "the frame is
being painted for a reason that provably is not this window" — cursor movement,
clock tick, another window's dirty flag — which is a strictly smaller set than
`not ws._dirty` and cannot freeze a self-animating surface that also dirties.

**First slice, with a measurement gate:** skip the focused window's content draw
when the painted frame was triggered by the cursor or the clock alone; stamp from
`win.buf`. Verify with `tools/p4_clicks.py` (the transitions must not regress) and
`tools/p4_surface_sweep.py` (the drag medians), and check the caret still blinks in
the Code tab on glass. If that holds, widen the trigger set one source at a time.

**On maintainability**, since that is the other reason to do it: this direction is
neutral-to-positive for Layer code and positive for the shell. Layers keep drawing
imperatively — they are not asked to declare rects, which is the version that
would have made them harder. The shell's ad-hoc mechanisms that this can absorb
are the chrome freeze and the drag content-freeze (both special cases of "this
surface did not change"); the bar strip cache, the cover caches and scroll-as-blit
are different problems and stay. So "six mechanisms become one" (§2.1) is an
overclaim — call it two of six, plus a general rule where there were special cases.

**First slice SHIPPED (2026-07-27): `wm_windowed._content_static`.** The gate that
proved implementable is equivalent to the "provably not this window" trigger set,
decomposed into checkable facts: `not ws._dirty` (readable during the draw —
`frame()` clears it after) **and** no pointer DOWN or CLICK this frame or last
**and** the content is not a self-animating window surface (music preview,
bluetooth panel, update screen — excluded by name) **and** `win.buf` is not stale
(a fresh build or a gesture's `_direct_render` bypass forces one live refill).
The down/click guard exists because the audit found the real invariant: the
drag-driven handlers (paint strokes, map pans, `ScrollRegion`) mutate content
WITHOUT marking dirty — they predate the #44 gate and rely on pointer-state
arming — so any button activity must render live; a position/visibility-only
pointer change is inert (no content surface draws hover feedback, and anything
that appears without input must already be an `_animating` source or it could
never paint under the redraw gate). The feared caret blink does not exist:
both carets (code editor, Writer) are deliberately solid, for exactly this
reason. Pinned by `tests/test_wm_content_freeze.py` (7 tests incl. a
frozen-vs-live pixel-equality check; both gate mutations verified caught).

**The measured on-glass verdict is humbler than the table above.** The
frame-by-frame classes the 57ms row promised are mostly already covered on the
P4: a game beside a focused editor goes through `draw_stack`'s quiet partial
stack, which bypasses ALL window draws (A/B measured 32.2 vs 32.4ms/frame — no
change, correctly), and an open EDITOR freezes a live wallpaper anyway
(`wm.top_kind()` is `"menu"`, not in `_animating`'s wallpaper list). Where
animation-armed full paints DO occur — Settings over a live-wallpaper desk —
the freeze measured **123.1 → 109.2ms/frame** (the settings content draw
replaced by its stamp; the rest is the wallpaper's own desk repaint).
`tools/p4_clicks.py` transitions: unchanged within noise (all dirty-armed, as
designed). The 70ms-class win applies to tap-settle frames on the P4 and to
every cursor-move frame on the HOST and WEB tiers (visible cursor defeats both
quiet paths there; on the web a skipped render also means the window's
`RecordingLayer` re-ships nothing — it blits by reference). Next widening, per
the plan: waive the down-guard when a WM drag/resize of a DIFFERENT window has
captured the pointer.

---

**The original §5, for the record:**

**Not a widget toolkit.** A widget tree would mean rewriting every Layer's draw and
would fight the indexed-canvas cart API. Three primitives instead, and Layers keep
drawing imperatively:

### 5.1 A damage list on the compositor

`invalidate(rect)` collects; the frame merges overlapping rects and runs one
clipped repaint pass per merged region. `Canvas.clip()` already exists and the
viewport work (this session) already lets a Layer draw into a sub-rect of the root
with correct origin and stride — so the plumbing is half-built.

A Layer that declares nothing gets today's behaviour (full repaint), so migration
is per-surface and reversible.

### 5.2 One retained-surface primitive

Replaces the bar strip cache, the desk backdrop cache and the cover caches. It owns
the buffer, the key, eviction, and — non-negotiable, see §2.1 — **its own build
counter via `ws.note_cost`**. A cache that cannot report its own rebuild rate is
how we got here.

### 5.3 One scroll primitive

Absorbs the two duplicated `_try_drag_partial` eligibility chains
(`launcher_layer.py:794` and `:1301`, ~10 conditions each, near-identical) plus
Settings' third model (row-snapping). The blit-vs-redraw decision then lives in
**one** place and can be made from measurement: scroll-as-blit wins decisively at
320×240 but buys only **4.5ms** at 1024×600 (26.8ms shift path vs 31.3ms plain full
redraw), because the 874×429 band is 750KB and the single `scroll_rect` is 15.2ms
of pure memory bandwidth.

### 5.4 Carts are untouched

`cls/pset/line/rect/rectfill/circ/circfill/spr/print` and the 320×240 game canvas
stay exactly as they are. A game legitimately redraws everything.

---

## 6. Staging, with a measurable gate per phase

1. **Damage list + clipped repaint in the compositor**, no Layer changes. Gate: the
   golden/parity suites stay byte-identical (nothing declares damage yet, so every
   frame is still a full repaint).
2. **One Editor tab** — Map, the worst at 92ms. Gate: a drag repaints the map
   viewport only; median drops materially. This phase decides whether the whole
   idea pays; if Map does not improve, stop here.
3. **The remaining Editor tabs** (blocks, paint, code) plus Writer/Sheets.
4. **Retained-surface primitive**, migrating the three existing caches onto it.
   Gate: the rebuild-budget tests (`test_bar_strip_rebuild_budget`,
   `test_cover_blob_read_budget`) still pass, unchanged.
5. **Scroll primitive**, absorbing the three scroll models. Gate: `#113`'s existing
   scroll tests plus the on-glass suite.

Phases 4 and 5 are consolidation with no promised speed-up; they exist to remove
the bug class. Phase 2 is where the perf claim lives, and it is deliberately first
after the plumbing so the claim is tested early and cheaply.

---

## 7. What this will NOT fix

Stated plainly, because the temptation is to expect invalidation to fix everything
that is slow:

- **The picker's 15.2ms band shift is memory bandwidth**, not redundant work —
  750KB moved at ~148MB/s of cache traffic counting the write-allocate read. Cards
  cover 420 of the band's 429 rows, so there is no empty backdrop to skip.
- **Genuinely full-screen changes still cost what they cost.** Opening a window,
  flipping worlds, changing theme.
- **The 99–220ms worst frames** on every surface are the open/first-paint
  transition — a frame that legitimately draws everything. Invalidation does not
  help a frame whose damage *is* the whole screen. (That class was removed from
  Settings by fixing a cache, not by damage tracking.)
- **Per-frame allocation churn.** ~2-4KB/frame; not currently a problem (a gesture
  takes zero collects since the `new_layer` gate), but invalidation does not touch
  it.

The honest scope: invalidation removes *unnecessary* work. On the Editor tabs the
measurements say most of the work is unnecessary. Elsewhere they say it is not.

---

## 8. Risks

- **Scope.** This touches the shared shell that both boards and the host run. The
  per-phase gates and the "declare nothing → old behaviour" fallback are the
  mitigation.
- **Correctness of clipped draws.** A Layer that draws outside its declared damage
  leaves stale pixels — a *visual* bug, which is at least loud, unlike the silent
  perf bugs of §2.1. The `_base`-verbatim golden tests at 320×240 are the net.
- **Another silent-cache family.** The counter requirement in §5.2 exists for
  exactly this; every new cache ships with a budget test.
- **The web view.** Recording layers and the per-surface command streams interact
  with clipping; the `RecordingLayer` path must either honour damage or opt out
  wholesale (it already opts out of the bar strip cache).

---

## 9. Open questions

1. Does damage merging want to be per-Layer or global? LVGL is global per display;
   our WM already has per-window surfaces, which may be the natural unit.
2. Do the Editor tabs need *sub-region* damage, or is "content viewport vs chrome"
   a coarse enough split to get most of the 90ms? Cheap to test in phase 2 and
   worth trying the coarse version first.
3. Does the P4's PPA become more useful once damage is small (fills and blits aimed
   at a rect), or does per-op submit overhead dominate at small sizes? The sprite
   batching verdict (#66) says submit overhead dominates below some size — that
   threshold should be measured, not assumed.
