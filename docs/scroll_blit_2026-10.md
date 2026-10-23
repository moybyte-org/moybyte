# Scroll-as-blit for the verbs engine (2026-10)

**What this is.** This is the design for moving a scroll region of the UI
verbs engine by shifting the pixels it already drew, instead of redrawing
every row. A region R is a scrolling nest of `native/moy_ui/moy_ui_api.c`: the
page body, a `ui.scroll` region, or a `ui.table`'s rows. #113 is where the
model comes from (shift, then repaint the exposed band) and where it was first
built for the launcher shelf and the picker (`ScrollRegion` in
`runtime/ui_draw.py`). The measurements behind every choice below are in
[#113's 2026-10-23 comment](https://github.com/moybyte-org/moybyte/issues/113#issuecomment-6359246826).
Frame costs are in #66 and #58. The doc was written before any code, and was
revised the same day after an adversarial review (§11).

**Prerequisite, built first.** The verbs' page and group grounds fill on the
PPA (`P4SystemCanvas.ppa_fill`, above `PPA_FILL_MIN_PX`). That is a separate
step, and it changes what a scroll frame costs before any shift exists. This
design's gain is measured on top of it (§10), not against today's frame.

## 1. Scope of v1

Each of these limits is a lever the other cases report as `None` (§9):

- **Where:** P4 direct render only: a content gesture drawing the focused
  window straight into the framebuffer ring (§7).
- **How:** cross-buffer only. The source is the buffer that holds frame
  n−1, and the destination is the back buffer. There is no in-place shift and
  no `win.buf` path. A single-buffer degrade (`RETAINED_FRAMES` 1) gets no
  shift.
- **Who:** shell Python apps in app windows only: Settings, Files, Get Carts,
  Storybook, My Art and Calc. Lua carts are out, because a cart may read `btn`
  in its draw and its `cls()` writes past any floor. Compiled apps are out
  too, as is any draw that the idempotence harness (§10) has not cleared.
- **When:** touch drag and fling only. A keyboard scroll moves focus, and
  focus is in the key, so a key step is never a shift frame. Keyboard
  scrolling gains nothing, by design.

## 2. The mechanism

A **shift frame** for R is a frame on which R's surface draws and nothing
about R has changed except its offset (§3 says how that is known). On a shift
frame:

1. **The page ground leaves R out.** `moy_ui_page_ground` fills the page
   ground before R opens: Get Carts and My Art open `ui.scroll` after
   `ui.page`. On a shift frame it fills the page minus R's rect.
2. **At R's open,** the engine submits the shift. The 2D-DMA copies R's
   view from the n−1 buffer into the back buffer, offset by Δ (§6). The engine
   then sets the **band floor** (§5): the |Δ| rows the shift exposed. Next it
   fills R's ground inside the band.
3. **The app's pass runs as always.** Every verb in R still runs its layout,
   focus and hit-testing, and hashes its raster into the key. Rasterizing
   inside R is clipped to the floor. The only answer that changes is that a
   card wholly outside the band answers 0-size, as a card scrolled out of view
   already does. A card that touches the band answers its full rect, and the
   floor clips what is drawn from it.
4. **At R's close,** the engine fences the shift (§6) and lifts the floor.
   It then writes back the keeps that the shift moved (§4), in their drawing
   order and cut to outside the band. Last, it takes the new keeps and draws
   the overlays again.

When anything in §3 fails, R paints in full, as it does today. That full paint
is the reference the parity tests hold every shift frame to.

## 3. When a frame may shift

**Licensed by producer signals.** These must all hold before the pass:

- `ws._dirty` is false.
- No press is live except R's own claimed drag, or the fling R is running.
- The surface is not a Class B animating surface (`docs/surface_model_v1.md`
  §3). A progress bar is an animating source, and it excludes its surface.
- No tooltip, toast, popover, menu or dialog scrim is showing.
- R's view rect, content extent, focus, look and window content rect equal
  the n−1 entry's (§4).
- Entry n−1 recorded **no overdraw** of R after R's close, other than the
  cursor stamp (§4).
- Every gen R depends on equals the entry's.

That last condition covers what the app draws itself. When the app rasterized
inside R during frame n−1 (the canvas row's gate counters, `MOY_ST_N_FILL` and
`MOY_ST_N_TEXT`, moved between the engine's own verbs while R was open), R
must carry a gen for that content: `o.gen` on `ui.scroll`, `ui.page` or
`ui.table`. Get Carts' and My Art's cover caches and Files' thumbnails pass
one. An app that draws in R and passes no gen never shifts.

**Verified after the pass.** The key is a hash of every raster byte R's verbs
produced: their kinds, ids, values and states; their boxes relative to the
offset; their text, override-id text included; their table cells and sub
lines. A key that differs from entry n−1's means a producer changed content
without a signal. The engine then re-runs the pass unclipped, as a
**replay**: `ui_frame` begins with the frame's input already consumed, so no
stop activates twice. The hash can only force a repaint. It never licenses a
skip, which is what keeps it on the right side of L2 (§9).

**No storms.** A replay costs a second pass, so a source that changes R on
every frame of a gesture would double the frame. R's drag claim suppresses
the two such sources in the verbs:

- the press highlight on the row under the finger, once R claims the drag;
- hover, which moves under a still pointer as the content scrolls.

After a replay, R takes no shift for a hold-off of frames (configuration,
starting at 8). A repeat offender therefore costs one replay per hold-off,
not one per frame.

## 4. The history: entry n−1, one body

R keeps ONE entry: the painted frame number (handed in by `ui_frame`), the
offset, the key, the gens, the window content rect, and the **address of the
buffer** frame n−1 was drawn into. R is the only part of the window that
reads the buffer's past. Everything outside R repaints every frame under
direct render, so no deeper history is needed. The frame number must be
exactly n−1, because a gap means the buffer was painted by something else.

**One body with `ScrollRegion`.** The entry lives in the native scroll state
(`moy_ui_scroll_t`, which already holds a `ScrollRegion`'s drag and fling).
`ScrollRegion`'s paint ring (`note_painted` / `blit_shift`, `_painted`) moves
into that same body, as an N-entry use of it. The kernel surface table
(§15 of the surface model) is the alternative home, if it lands first. The
build adds a one-body guard: a grep test that no second paint ring exists in
`runtime/` or `native/moy_ui/`.

**Overdraw after close.** In frame n−1, the canvas row's write choke point
(§5) unions every write that lands in R's rect after R's close into the
entry's **overdraw rect**. Several paths write there:

- the cursor (`console._draw_cursor`);
- the desk tooltip, toasts, popovers and menus, and the dialog scrim;
- `wm_windowed._row_over`.

Only the cursor is allowed. Its write is tagged as a **stamp**: before the
cursor draws, the row keeps the pixels under it, exactly as `ScrollRegion`'s
recorded stamp lets the launcher repaint the stale cursor. On a shift frame,
those kept pixels go back at the stamp's rect minus Δ, inside R and outside
the band. Any other overdraw breaks the chain, and R paints in full.

**The keeps.** `moy_ui_fade` captures the rows under every fade box that
touches R before drawing it. That covers the page and scroll foot fades,
`ui.table`'s fades, `table_pictures`, `ui.fade` (Calc's line fades, Get
Carts' card fades) and a popover's head fade. These keeps and the cursor
stamp are what step 4 of §2 writes back, in drawing order. The new keeps are
taken **after the fence**, so they hold the shifted pixels. They are owned
off-heap and released like a layer's pixels (`runtime/moybuf.py`).

## 5. The floor and the one choke point

**The floor applies to R only.** It is a rect on the canvas row, beside the
clip (`native/moy_glass/moy_canvas.h`). While it is set:

- a write whose box lies inside R's rect is clipped to the floor;
- a write outside R is not clipped by it.

`clip(x, y, w, h)` inside R stores the rect intersected with the floor, and
`clip()` returns to the floor. This holds in `device/device_canvas.py`'s
`clip` and in the C row alike. So the apps' own `cv.clip(...)`, `cv.cover`,
`cv.picture` and `blit565` inside R obey the band unchanged. My Art's picture
draw between its own `clip` calls is the case to test.

**The band invariant**, unchanged from #113: inside the floor, every pixel is
painted from the ground up, so an anti-aliased edge never blends over itself.

**The choke point.** The C row's write entry is the one place that applies
the floor, records overdraw (§4) and fences (§6). Every write reaches the
glass through the C row or through `P4SystemCanvas`'s PPA calls, and the PPA
wrapper takes the same check, so each write path passes through it:

- the verbs' fills, text and masks;
- `rect`, `cls` and `blit565`;
- `cover` and `picture`;
- the PPA fill and the PPA cover;
- `reset_state` and `set_viewport`, which change the row's clip and origin
  under the floor.

## 6. The shift engine, and the fence

**The engine.** On both P4s the shift is the DMA2D M2M block copy
(`esp_async_fbcpy`, ESP-IDF's `esp_lcd`). It is submitted asynchronously
and fenced at R's close. The PPA's SRM engine copies 1:1 at about a third of
that rate, and the AXI GDMA needs whole buffer rows, which a region inside a
window never has (the comment). The new verb is a `moy_gfx` or `moy_ppa`
verb with a fence. It is not the throwaway probe the measurements used.

**The fence** does four things:

1. **Before the submit,** it fences every op still pending on the source
   buffer (the frame n−1 compositor's deferred stamp, a rotate still writing
   it). It also writes back the destination rows' dirty cache lines.
2. **Between the submit and the fence,** the CPU writes nothing in a cache
   line of the destination rows. The band rows lie outside them by
   construction. A write that would land there fences first, through the
   choke point.
3. **At the fence,** it waits for the copy to land and then **invalidates
   the destination rows' cache.** A line the CPU pulled in before or during
   the copy must not survive it. Without this, a CPU read after the fence
   (the keeps, a blend) would see the old pixels.
4. **The frame's own async ops keep their order.** The shift lands before
   R's close, so §4 `end_frame`'s last-write op is still last.

**The Guition shares the engine.** Its rotated compositor rotates paint
buffers onto the scan buffers through the PPA, which runs on the same DMA2D,
so a shift waits behind a rotate in flight. The isolated numbers in the
comment do not see that, which is why §10's gate is a whole-frame probe.

## 7. The WM's direct render

During a content gesture the windowed WM draws the focused window straight
into the root's framebuffer ring through a viewport
(`wm_windowed._direct_render`, #155). That is v1's only case:

- **The target** is the back buffer.
- **The source** is the buffer the compositor last queued:
  `moy_present_dsi.c`'s front or pending buffer on the Waveshare, and the
  previous landscape paint buffer on the Guition.
- **The window's content rect is in the entry.** A window that moved,
  resized, or hangs off the glass paints in full. `_direct_render` already
  refuses the last case.
- **The window's chrome** is drawn after its content by `_direct_render`'s
  caller, and so after the fence. The windows under it are drawn before R
  opens.

The first frame after a release goes back into `win.buf`, so it is not a
direct-render frame, and it paints in full. The surface model's §14.3 (the
WM's shown ring) is where a window content's per-buffer history lives on a
desk; a region whose app speaks that protocol is told what its target
holds instead.

## 8. Why this is not a second invalidation mechanism

Whether a surface draws is still decided by its gens and the §14.1 freeze.
The entry never causes a draw and never skips one. It only picks how much of
R a surface that is already drawing rasterizes. That is L5's line: the
shared layer decides whether, and the backend decides how. Against the §8
graveyard:

- **Per-widget / fine-grained damage.** No widget reports damage. There is one
  region, one translation, and one entry.
- **Inferred static-content caching.** Nothing is inferred. The shift is
  licensed by producer signals and gens (§3). The key can only force a
  repaint, and on a mismatch the replay repaints, so a wrong signal costs
  time, not pixels. Fold-2 skipped on inference and had no check.
- **A retained root framebuffer across web frames.** The web tier keeps no
  root and takes no shift.
- **PPA for 1:1 copies.** That verdict stands, and this design uses no PPA.
  What it buys is the CPU's time: a DMA2D copy that is submitted in
  microseconds and runs beside the pass's Python without slowing it.
- **The frame-level damage architecture.** The cost that goes is R's raster
  only, and it is measured on a live whole frame (§10).

## 9. The surface model changes in the same change

The build amends `docs/surface_model_v1.md` in the same commit that lands the
entry:

- **§7's scroll-as-blit row** (#113) names the one body (§4) and its two
  uses: the launcher's N-entry ring, and the verbs' n−1 entry.
- **L2** gains the line this design depends on: a draw-stream hash may force
  a repaint but never license a skip.

**A lever a tier lacks is `None`.** This covers the S3 boards (a CPU shift
costs more than the fill it would save; the comment's S3 rows), the web and
host tiers, a single-buffer P4, Lua carts and compiled apps. Each declares
`None`, and where it does, none of the bookkeeping runs: no key hashing, no
keeps and no overdraw recording. The host still runs the whole mechanism
under test, as an emulated tier (§10).

## 10. The tests that pin it

- **Console-level parity through `_direct_render`.**
  - For Settings, Files, Get Carts, Storybook, My Art and Calc: a scripted
    drag and a fling, rendered shift-and-band and forced-full, with every
    frame byte-identical. This runs on an emulated ring of 2 and of 3.
  - Each runs with a cursor showing, then with a tooltip and a toast arriving
    mid-gesture (each must force a full paint, and the pixels still match).
  - Both directions, |Δ| from 1 to the view less one, fades showing at either
    end, and the clamp at either end.
  - The fixtures carry real decoded covers, not placeholders.
- **Two emulated DMAs.** One lands the copy at submit and one at the fence.
  Both must produce the same frames, which catches a write ordered wrongly
  against the flight.
- **Each break forces a full paint**, asserted with a counting canvas: a cover
  landing mid-drag, a focus move, a look change, a window moved during a
  fling, a painted-frame gap, an overdraw other than the cursor, and an app
  that draws in R with no gen.
- **The idempotence harness.** Every v1 app's draw runs twice per frame
  across its scripted sessions, the second time as a replay. Pixels, the app's
  state digest and the activation count must not change. An app enters v1
  only once this passes.
- **The check catches a liar.** An app that mutates R without a signal still
  matches the full paint, through one replay.
- **The floor and the choke point.**
  - `clip` inside R intersects the floor, and `clip()` returns to it.
  - `cover`, `picture`, `blit565`, `cls`, the PPA fill and `set_viewport`
    either write no pixel outside the band inside R, or fence first.
- **L8, the silent-disable direction.** On a scripted fling, shift frames
  DO happen: the count of shift frames over the fling stays above a floor. A
  mechanism that quietly never engages fails this test.
- **One body.** The grep guard of §4.
- **On glass, in each P4's suite:**
  - the shift verb is byte-exact against `moy_gfx.scroll_rect` in both
    directions;
  - a primed-cache test: the destination lines are pulled into cache before
    the copy and read after the fence;
  - a CPU write into the destination rows during the flight is fenced first.

## 11. What it should buy, and how that is measured

R's ground and R's rows' raster go away. A 2D-DMA submit and the band's
raster come in. Per app:

- **Get Carts** (`ui.scroll` over the cards) and **My Art** (`ui.scroll` over
  the drawings) lose R's ground and the cards' raster. Their covers are the
  larger share, and they move only with their gen.
- **Storybook** (`ui.scroll` over the stories) and **Settings** (the page
  body) lose their rows' text and grounds.
- **Files** scrolls `ui.table` rows and a `ui.scroll` side, both small rows.
  The table's fades and `table_pictures` are kept and written back (§4).
- **Calc** scrolls a small body with line fades. It gains little, and it is
  in v1 as the fade-keep case.

The model in the comment predates the PPA ground step, and it assumed a
whole-window fill that the page ground pays before R opens. The gain is
therefore not taken from it. It is measured at the build's gate, per app, on
both P4s:

- with a whole-frame probe, the compositor live;
- on top of the PPA ground step;
- shift against full, under the same scripted fling;
- with `tools/p4_surface_sweep.py`'s drag medians moving and
  `tools/p4_clicks.py`'s transitions not regressing.

The verdicts land in #66 and #58.

**Review (2026-10-23).** The adversarial review's blockers are folded in
above:

- what the cross-buffer copy carries from frame n−1 (§3, §4);
- the page ground paid before R opens (§2, §11);
- the replay's safety for Lua carts and compiled apps (§1, §3).

Its must-fixes are folded in too: the answers, the fade capture, the
R-only floor and its choke point, the cache invalidate, entry n−1 only,
every raster byte hashed, one body, the whole-frame gain probe, and no
in-place path.
