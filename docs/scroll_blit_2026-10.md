# Scroll-as-blit for the verbs engine (2026-10)

**What this is.** The design for moving a UI verbs scroll region by
shifting the pixels it already drew, instead of drawing every row again. A
scroll region here is a scrolling nest of `native/moy_ui/moy_ui_api.c`: the
page, a `ui.scroll` region, or a table's rows. The apps it is for are
Settings, Files, Get Carts, Storybook, My Art and Calc. #113 is where the
model comes from (shift, then repaint the exposed band) and where it was first
built for the launcher shelf and the picker (`ScrollRegion`'s paint ring in
`runtime/ui_draw.py`). The verbs engine never got that ring. The measurements
behind every choice below are in
[#113's 2026-10-23 comment](https://github.com/moybyte-org/moybyte/issues/113#issuecomment-6359246826),
and the frame costs are in #66 and #58. It was written before any code. An
adversarial review gates the build.

## 1. The mechanism

A **shift frame** for region R is a frame on which R's surface draws and
nothing about R has changed except its offset. On a shift frame:

1. At R's open (`moy_ui_api_scroll`, or a page's or table's equivalent), the
   engine reads R's history (§2). If the target buffer holds R at a known
   offset under the same key, it issues the shift through the canvas
   (`scroll_rect`'s family, §5). The shift moves R's view by the offset delta
   Δ. The engine then sets the **band floor** on the canvas row: the |Δ| rows
   the shift exposed.
2. The app's immediate pass runs as it always does. Every verb inside R still
   runs its layout, focus, hit-testing and key hashing. A verb whose box misses
   the band does not rasterize. A verb that answers a rect to the app (a
   `ui.card`, `ui.table_rows`) answers it cut to the band, so an app that
   draws its own picture into a card that missed the band skips that draw.
3. At R's close, the engine fences the shift and lifts the floor. It then
   restores what its own overlays covered (§3) and draws them again.

When the history cannot vouch for the target buffer, R paints in full exactly
as it does today. The same happens when |Δ| leaves no rows to shift. This
full paint is the reference that the parity tests hold the shift frames to.

**The precondition and the check.** Whether a frame is a shift frame is
decided before the pass from signals that already exist. All of these must
hold:

- `ws._dirty` is false.
- No press is live, unless it is R's own drag, or a fling that R is running.
- The surface is not a Class B animating surface (`docs/surface_model_v1.md`
  §3).
- R's view rect, content extent, focus and look are the ones in its history.

The engine then verifies the decision after the pass. It computes R's
**key**, a hash of every verb drawn inside R (its kind, id, value, state and
box relative to the offset), and compares it with the key in R's history. A
mismatch means a producer changed content without saying so. In that case the
engine runs the pass again, unclipped, as a **replay**: `ui_frame` begins
with the frame's input already consumed, so no stop activates twice. The cost
of a lying precondition is therefore one extra pass on that frame, never a
wrong pixel. The replay needs an app's draw to be idempotent under consumed
input. The review should hold every verbs app to that, because a draw that
polls or counts would do it twice.

**What the check cannot see.** It cannot see a picture the app skipped
because its card answered empty. That picture's pixels sit in the shifted
part, and they are only right if the picture has not changed. So an app whose
region holds its own pictures passes their generation into the region
(`o.gen` on `ui.scroll`, hashed into the key). This applies to Get Carts' and
My Art's cover caches and to Files' thumbnails.

## 2. Where the history lives

R's history lives in R's keep slot in the engine (`moy_ui_keep_t`,
`native/moy_ui/moy_ui_api.h`). That slot is where R's offset already lives,
and every client reaches it: Python apps, Lua carts (`moycore_ui.h`) and
compiled apps (`moy_ui_wasm.h`) all draw through the same engine. The slot
grows a ring of up to `RETAINED_FRAMES` entries, each holding:

- the painted frame number, which `ui_frame` hands in (the console's
  painted-frame counter, as the launcher's ring uses);
- the offset;
- the key;
- the target identity: the canvas row, and the surface-write origin on it.

The canvas row tells the engine its ring depth: the tier's canvas sets
`RETAINED_FRAMES` on the row (`native/moy_glass/moy_canvas.h`). Two rules
from `ScrollRegion.blit_shift` carry over unchanged:

- The last N entries must be consecutive painted frames. A gap means another
  surface may own those buffers.
- Every entry must carry the same key and target. The target identity matters
  because the windowed WM alternates destinations for one window: `win.buf`
  on settled frames, the framebuffer during a gesture. A switch breaks the
  chain, and that is correct, since the two destinations hold different
  pixels. This is the destination swap that §14.2 records cost the bar's strip
  cache 72 ms. Here it is a key field, so it cannot alias.

`win._buf_stale` and `look_gen` reach the engine through the target identity
and the key. The WM sets both today for the content freeze (§14.1).

## 3. Overlays: what the engine drew over R

Edge fades (`edge_fades`) are drawn over R's content. A shift would carry
faded pixels into the body, and unfaded ones into the fade. The engine
therefore keeps what each overlay covered, at its last draw: R's width times
the fade height, per fade. On a shift frame it does two things. First, after
the fence, it writes those kept rows back where the shift moved them, cut to
outside the band. Then, before drawing the fades again, it keeps the new
rows. As a result R's pixels at close equal a full paint, and every shift
frame has ONE band (the exposed rows), in either direction.

Without this, scrolling toward the top under a foot fade needs two bands:
the exposed rows and the fade zone. Two bands mean two clipped passes, which
doubles the app's Python. The kept rows are owned off-heap and
released like a layer's pixels (`runtime/moybuf.py`). They are the
newest frame's only, because the source is always the frame before (§5).

## 4. The clip contract

**The band floor** is a rect on the canvas row, beside the clip
(`MOY_ST_CX0..CY1`). While a floor is set:

- `clip(x, y, w, h)` stores the INTERSECTION of the rect and the floor;
- `clip()` restores the floor, not the surface.

This holds in `device/device_canvas.py`'s `clip`, the one canvas class on every
tier, and in the C row the verbs draw through. Its consequence is that
app-drawn pixels obey the band with no change to the apps. My Art draws its
pictures between its own `cv.clip(...)` and `cv.clip()`. Under a floor, the
first narrows to the band and the second returns to it. `cv.cover`,
`cv.picture` and `blit565` already honour the clip.

**The band invariant**, unchanged from #113: inside the floor, every pixel is
painted from the ground up. That includes the page ground under a row, so an
anti-aliased edge never blends over itself. Outside the floor, nothing is
written until the fence (§6).

## 5. Which shift, where

| tier | ring | shift | why |
|---|---|---|---|
| P4s, gesture (direct render) | 2 (Guition paint buffers), 3 (Waveshare framebuffers) | **2D-DMA, cross-buffer, async**: the buffer that holds frame n−1 into the back buffer | fastest copy of the three engines, off the CPU, either direction |
| P4s, settled (`win.buf`) | 1 | 2D-DMA in place when the content moves up; CPU `scroll_rect` otherwise, or a full paint where that loses | the engines are forward-only in place |
| T-Deck, Guition S3 | 2 | **none**: full paint (lever `None`) | a CPU shift costs more than the fill it would save (the comment's S3 rows) |
| host | 1 | CPU `scroll_rect` (the same `moy_gfx` kernel, through `runtime/gfx_binding.py`) | the reference and the test bed |
| web | 0 | none: full paint | `docs/surface_model_v1.md` §8 (no retained root) |

**Cross-buffer** reads the previous frame (Δ is one frame's motion, not
N frames'). Nothing overlaps, so any engine works in either direction. The
source is a presented buffer, so its lines are already written back. **In
place** is the only choice on a ring of one.

The 2D-DMA is the M2M block copy on the P4's DMA2D (`esp_async_fbcpy` in
ESP-IDF's `esp_lcd`). It is not the PPA. The PPA's scale-rotate-mirror engine
copies 1:1 at about a third of the 2D-DMA's rate. The AXI GDMA moves one span
at a time, so it needs whole buffer rows, and a region inside a window never
has them. A per-row GDMA loses to the CPU. A sub-window region has to be
copied as a 2D block, and the 2D-DMA copies one at the GDMA's speed. A
lever a board lacks is `None` (a board without DMA2D takes the CPU or no
shift), declared in its board.toml like every other lever.

## 6. The WM's direct render

During a content gesture the windowed WM draws the focused window straight
into the framebuffer, through a viewport (`wm_windowed._direct_render`,
#155). That is the case this design targets: the shift's target is the back
buffer of the root's ring, and its source is the buffer the compositor last
queued. On the Waveshare, that buffer is `moy_present_dsi.c`'s front or
pending buffer. On the Guition, it is the previous landscape paint buffer,
which the rotated compositor may still be reading; reading it beside the
compositor is safe. The rules:

- **The window's content rect is in the key.** A window that moved,
  resized, or hangs off the glass falls back to a full paint, as
  `_direct_render` itself already does for the last case.
- **The fence.** Between the submit and the fence, the CPU writes nothing
  in a cache line of the shift's destination rows. The band rows are outside
  those rows by construction. Under direct render the window's chrome is
  drawn after its content (`_direct_render`'s caller), and the windows under
  it before. Anything an app draws outside R while R is open, in those rows,
  fences first: one compare in the row's draw entry. This is §4 `end_frame`'s
  ordering constraint applied mid-frame. The destination's dirty lines are
  written back before the submit, and nothing dirties them until the fence.
- **The frame's own async ops keep their order.** The shift is fenced at R's
  close. On the `win.buf` path, the deferred content stamp therefore stays the
  frame's last write.

## 7. Why this is not a second invalidation mechanism

What decides whether a surface draws is still the surface's gens plus the
§14.1 freeze. The history never causes a draw and never skips one. It only
picks HOW MUCH of R a surface that is already drawing rasterizes. It is §4's
"last-seen per physical buffer, N-deep", kept inside one surface for one
region: a ring keyed by painted frame number, of the shape §7 asks for. It is
not a streak counter of the kind §7 retires. Against the §8 graveyard:

- **Per-widget / fine-grained damage.** No widget reports damage, and nothing
  is invalidated per item. There is one region and one translation, and the
  key equality between two whole passes is what licenses it.
- **Inferred static-content caching.** Nothing is inferred to be static. The
  precondition comes from the signals that already gate drawing. The key check
  runs after every shift frame, and on a mismatch the replay repaints, so a
  wrong inference costs time, not pixels. Fold-2 had no check.
- **A retained root framebuffer across web frames.** The web tier keeps no
  root and takes no shift.
- **PPA for 1:1 copies.** That verdict stands: a blocking 1:1 copy is
  bandwidth-bound on any engine, and this design uses no PPA. What this design
  buys is the CPU's time. The 2D-DMA copy is submitted in microseconds and
  runs beside the app's Python, which it does not slow. It is also faster than
  the CPU's copy of the same block. The evidence is new (the comment), and the
  graveyard entry is about a different claim.
- **The frame-level damage architecture.** Its draw loops iterated the
  viewport. Here the cost that goes is the region's raster: the page-ground
  `mg_fill_run` and the rows' fills, which are a measured share of a scroll
  frame (the comment).

## 8. The tests that pin it

- **Parity, per app.** Settings, Files, Get Carts, Storybook, My Art and Calc
  each get a scripted drag and a fling. Every frame is rendered shift-and-band
  and forced-full, and the two are byte-identical. This runs at 320×240 and at
  a windowed size, on a ring of 1 and on rings of 2 and 3 emulated on the host
  canvas (`tests/test_scroll_ring_rotation.py`'s pattern). It covers both
  directions, |Δ| from 1 to the view less one, fades showing at either end,
  and the clamp at either end.
- **Each break forces a full pass**, asserted with a counting canvas: a cover
  landing mid-drag, a focus move, a look change, a window moved during a
  fling, the direct-render ↔ `win.buf` switch, and a painted-frame gap.
- **The check catches a liar.** An app that mutates R's content without
  dirtying must still match the full paint, through the replay. The replay
  must activate no stop: an activation counter stays at one.
- **The floor.** `cv.clip(rect)` under a floor intersects, and `cv.clip()`
  returns to the floor. `cv.cover`, `cv.picture` and `blit565` write no pixel
  outside the band (a pixel diff outside the band is zero).
- **The budget.** A shift frame's rasterized verbs stay under a count, as in
  #113's dispatch-count method. The pass's verb CALLS do not change, and the
  test says so.
- **On glass**, in each P4's suite:
  - the 2D-DMA shift is byte-exact against `moy_gfx.scroll_rect` in both
    directions and both placements;
  - a CPU write into the destination rows during the flight is fenced first
    (a test that corrupts without the fence);
  - `tools/p4_surface_sweep.py`'s drag medians move;
  - `tools/p4_clicks.py`'s transitions do not regress, which is the §14.1
    gate.

## 9. What it should buy

The shift's own cost is in the comment for each board and engine. So are the
model and the per-app expectation it gives. On both P4s the 2D-DMA copy of a
window-sized region finishes well inside the pass's own Python, so a scroll
frame keeps its Python and loses its region raster. Get Carts, Storybook and
Settings, whose scroll frames are mostly region raster, gain the most. Calc
scrolls a small region and gains little. The verdict per app is measured at
the build's gate, on both P4s, and lands in #66 and #58.
