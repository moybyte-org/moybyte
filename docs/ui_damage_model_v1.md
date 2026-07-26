# UI damage model v1 — one invalidation mechanism instead of six

**Status:** proposal / direction doc. Nothing here is built.
**Date:** 2026-07-26. All numbers measured on P4 glass (1024×600) unless stated.
**Tracks:** #58 (P4 port), #66 (performance ledger), #113 (kinetic scroll), #73 (windowed WM).

---

## 1. The problem, in numbers

Every interactive surface, measured with real content seeded on the device
(`tools/p4_surface_sweep.py`; 60fps = 16.7ms):

| surface | content | median | p90 | worst |
|---|---|---|---|---|
| editor:map | *content-independent* | **92** | 96 | 220 |
| editor:blocks | *content-independent* | **88** | 92 | 167 |
| editor:paint | *content-independent* | 76 | 76 | 138 |
| writer | 200-line doc | 52 | 52 | 128 |
| editor:code | 302-line cart | 52 | 60 | 130 |
| sheets | 360-cell table | 48 | 52 | 99 |
| picker | 29 carts, covers warm | 48 | 60 | 89 |
| settings | full row set | 24 | 28 | 42 |

Only Settings is close to budget, and only because it received this session's
fixes (its worst frame was 117ms before them).

### 1.1 The finding that motivates this doc

**The Editor tabs' cost is chrome, not content.** map 92 / blocks 88 / paint 76
came out *identical* on a freshly created cart with no map, no sprites and no
blocks, and on the richest cart on the device. Code is only weakly
content-sensitive (48ms on a system-app cart, 52ms on a 302-line one).

So the slowest surfaces on the device spend ~90ms per frame redrawing a palette,
a toolbar and a grid **that did not change**. That is, by definition, what
invalidation removes.

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
