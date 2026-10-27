# Scrolling and text editing across the UI library (2026-10)

**What this is.** The design that makes every scroll and every text editor
in the UI library as fast and as smooth as the boards allow: one scroll
model (kinetic coast, eased steps, held-key repeat, trackball steps) for every
scrolling surface, and one frame model under it, in which a scroll is a
shift of the pixels the last frame drew plus the band it exposed, a
keystroke is the edited line and the caret, and a press is paid once. It
revises the scroll-as-blit design of 2026-10-23 (this file, under the name
scroll_blit_2026-10 until 2026-10-27), which reached the touch drag and fling
of six shell apps on the P4 and nothing else: §1 says what the baselines
found and why that scope was wrong.

**The measurements.** Every number this doc reasons from lives in an issue:
the per-board shift costs in
[#113's 2026-10-23 comment](https://github.com/moybyte-org/moybyte/issues/113#issuecomment-6359246826),
the Waveshare P4's baseline of every scrolling and text surface in
[#66's P4 comment of 2026-10-27](https://github.com/moybyte-org/moybyte/issues/66#issuecomment-6441340435)
and the T-Deck's in
[#66's T-Deck comment of 2026-10-27](https://github.com/moybyte-org/moybyte/issues/66#issuecomment-6441869611)
(both at dev `06fad00a`). Where a row is named below with a number, the
number is that baseline's p90 at that commit, and the number after the arrow
is ESTIMATED from the row's own frame split, by the arithmetic §14 states
once; the build's measurement step (§15) replaces every estimate with a
verdict in #66.

**The goals (owner, 2026-10-27).** Scrolling and text editing everywhere --
Code, Play, the Editor's tabs, every app -- at most 33 ms a frame at p90,
60 fps where the board can, on the Waveshare P4 and the T-Deck; the Guitions
are measured, not targets. Every scroll is kinetic: a touch drag and fling
coast with momentum everywhere; a key, held-key or trackball step carries no
momentum (a step moves exactly one step) but glides to its target eased, and
a held key reads as one continuous movement. Whether a fast trackball roll
gets a little momentum is tried by hand, so it is a parameter (§2.3). The
browser never looks different from the boards: host and device are one
codebase, and every mechanism here runs on the host under test.

**Where it sits.** `docs/surface_model_v1.md` is the presentation contract;
this design extends its §4 and §14 (§14.3's shown ring, §14.5's held pages,
§14.6's `copy_last`) and invents no second invalidation mechanism (§10
argues it against §8's graveyard). The widget toolkit is `native/moy_ui`
with `runtime/ui_draw.py` as its face and `runtime/ui.py` the public UI API
(`docs/ui_api_2026-10.md`); the scroll physics are `native/moy_ui/moy_ui_interact.c`
(`docs/kernel_toolkit_2026-10.md` §3.4). The cart-facing verb table is
moy-spec's and this design does not touch it (§16).

## 1. What the baselines found, and what the earlier design reached

The two comments carry the tables; the causes, as the comments rank them:

- **P4.** 143 of 164 rows are over 33 ms at p90. The surface rasterizes its
  whole window or pane on every painted frame (the raster alone is over
  33 ms in two thirds of the over-target rows; without it 97% of the rows
  fit 33 ms on their mean frame). Key and typing frames are not direct
  render: they draw into the window buffer and stamp it (19.5-19.9 ms
  windowed, 12.2-12.7 full screen). A key step is paid two to three times:
  two whole paints right after the press, then two whole-glass debt copies
  of 12-13 ms, so presses at 20/s coalesce (of a 112-character line typed
  on Code, 43 characters never get a frame of their own). A held key repeats once per
  painted frame with no initial delay (Code, Notes, Music at ~11 Hz).
  Momentum exists on the home carousel and PROJECTS only. My Art builds its
  covers inside its draw (one 0.6-2.5 s frame per gesture). The home's grid
  and icons arrangements paint 84-115 ms a frame.
- **T-Deck.** 26 of 142 rows are over; most surfaces paint 18-28 ms, the
  flush never holds a frame (it runs on core 0 behind the next frame), and
  the loop's floor is ~60 fps. The home's grid and icons repaint the whole
  home every frame (58% of the over rows); the sprite pickers draw the sheet
  pixel by pixel (23%); the frame a motion starts or settles on is a whole
  repaint (12%). No shell surface repeats a held key; a fast roll outruns
  the three-step queue (13 of 30 steps on the grid). The comment's own
  model: a scroll-as-blit that also skips the per-row work outside the band
  reaches the loop's floor on Settings (~60 fps) and better on Code and
  Notes; the shift's cross-buffer copy costs 3.4 ms for 320×190.
- **The 2026-10-23 design** reaches 28 of the P4's rows (the six apps' drags
  and flings) and none of the key, typing or text rows: it ruled a key step
  out of the shift by design ("focus is in the key"), kept the S3 boards at
  `None` from a measurement that assumed the rows' Python still ran, and
  did not see causes 2 to 7 at all.

Two of the P4's causes are one mechanism seen twice. A key step paints twice
because the verbs engine answers `settled` false for any frame whose input
moved something -- a focus move and the page's scroll to it, which
`api_begin` applies before the pass (`reveal` in `native/moy_ui/moy_ui_api.c`,
over the last frame's stops) and the pass draws, as much as an activation
the app acts on after the pass -- and every host paints an owed frame on it
(`App.owed`, the `host.owed` checks in each layer). The first paint is right;
the second is a precaution the engine cannot yet tell from a needed one. The
two debt copies are the windowed WM's `_full_debt` (`RETAINED_FRAMES` − 1 on
the Waveshare's ring of three) brought up by `copy_last` of the whole glass
once the desk is calm, §14.6. §7 removes the first and shrinks the second.

## 2. One scroll model

### 2.1 The motion: one body

`moy_ui_scroll_t` (`native/moy_ui/moy_ui.h`, the rules in
`moy_ui_interact.c`) is the one scroll state: a drag, a fling's velocity and
float offset, and -- added -- a **glide target** and the glide's start. One
`tick(dt)` advances whichever is live:

- **a fling** coasts as today: `FRICTION` per ms, `MIN_FLING` at release,
  `STOP_VEL` at rest, a hard stop at the clamp, dt injected, single
  precision (the physics twin, `tests/test_ui_physics.py`);
- **a glide** eases the offset to its target in TIME, half the distance left
  every `GLIDE_HALF_MS` (the carousel's `_GLIDE_HALF_MS`, 45, moved into C),
  rounded to the pixel, and reaches the target exactly; a new target while
  gliding re-aims from the current offset, so a held key that owes steps
  reads as one movement at the step rate, not as a stutter;
- **a drag** catches a fling or a glide, as today.

A step is `step(n, size)`: the target moves by n steps of the surface's step
size (a row, a card, a line) from the target if a glide is live, else from
the offset -- a step moves exactly one step, never more. `glide_to(p)` aims
at a content position (a focus reveal, §7). `ScrollRegion` (`runtime/ui_draw.py`)
stays the Python face over the same buffer and gains `step`, `glide_to` and
`gliding`; `Carousel.anim_frame`'s own glide (`runtime/launcher_layer.py`)
is deleted for it, with a grep guard that no `_GLIDE_HALF_MS` or ease
arithmetic exists outside `moy_ui_interact.c`.

**The verbs engine's nests scroll on it too.** A page body, a `ui.scroll`
region and a `ui.table`'s rows are nests whose offset is a `moy_ui_keep_t`
(`a`); today a drag writes `a` from the pointer's travel directly and a
release stops dead. Each scrolling keep gains a `moy_ui_scroll_t`, driven by
the same `moy_ui_dragtap_frame` the launcher rides, so a release flings and
`api_begin` ticks every live state before the pass. The physics is therefore
ONE function on every surface, and the twin test runs it once.

Parameters are configuration, not measurements, and live in
`moy_ui_interact.c` beside `MOY_UI_FRICTION`: `GLIDE_HALF_MS` 45,
`REPEAT_DELAY_MS` 400, `REPEAT_RATE_MS` 50, `BALL_COAST` 0 (§2.3).

### 2.2 The inputs: a held key repeats at the input layer

A held button repeats in `moy_input`'s merge (`moy_input_begin_frame`,
`native/moy_input/moy_input.h`), per source and per button, on the table's
own ms clock: after `REPEAT_DELAY_MS` of level, one repeat every
`REPEAT_RATE_MS`, counted against the clock, not the frame -- a frame that
took two periods owes two. The table exposes the count:
`moy_input_repeats(button)` this frame (the binding's `repeats(name)`), and
`steps(name)` = pressed edge + repeats. `btnp` stays an edge: a cart's
`btnp` is unchanged (L9; a game that wants repeat has `btn`), and
`moy_input_disarm` (a modal opening on a key) zeroes repeats with the
edges. Typed keys repeat the same way: the key level a source holds re-queues
its event every period, delivered as a count (`moy_input_key_repeats`), so a
text surface takes the count rather than one key per frame -- the one-shot
queue's zero frame between equal keys does not cap the rate. The verbs engine
reads two new input words, `MOY_UI_IN_REP` (four 8-bit direction counts) and
`MOY_UI_IN_KEYREP`; `ui_frame`'s `inp` array grows by them.

Where the level is not known the lever is `None`: the T-Deck keyboard in
its ASCII fallback (an older keyboard firmware, `moy_kbd.c`) types press
edges with no level, so it repeats nothing; its matrix mode, which serves
every surface on current firmware, holds the level and repeats. A BLE
keyboard's key is a level already.

The consumer rule, for every surface: a repeat is a step, not a press.
Nothing that opens, activates or commits runs on a repeat; only motion
(focus moves, caret moves, scroll steps) and typing do. `moy_ui`'s key
handling takes `go` and `tab` from the edge word only.

### 2.3 The trackball

A pulse is a step at the input layer (`Console.ball`, `BALL_STEP`), and a
step moves the target (§2.1), so steps are never lost to a queue: the
`BALL_QUEUE` cap of 3, which dropped 17 of 30 steps on the grid, goes, and
`ball_steps` hands every step on. The glide carries the view, so a roll's
cost is the glide's frames, not one frame per step. `BALL_COAST` is the
parameter the owner tries by hand: steps a roll delivers above
`BALL_COAST_RATE` per second become velocity (px/ms per surplus step) on the
surface's scroll state instead of target steps; 0 is no momentum, the
default. A caret takes the ball as lines (`nav`) as today, from the same
steps.

### 2.4 Who takes it, from one place

| surface | scrolls through | drag/fling | key, repeat, ball steps |
|---|---|---|---|
| verbs-engine pages, `ui.scroll`, `ui.table` rows (Settings and its pages, Files, Get Carts, Storybook, My Art, Calc, Appearance, Config, the Blocks outline, Music's steps, every app on the verbs) | the nest's `moy_ui_scroll_t` | the engine's drag-tap; the fling | focus moves a stop; the reveal is `glide_to` (§7) |
| the home's carousel, grid and icons; the PROJECTS window | `ScrollRegion` (`Launcher._scroll_region`) | as today | the selection moves; `glide_to` the card |
| the Code tab, theme Config text, Notes, the fields, the text console | `TextView` (§5) over a `ScrollRegion` in lines | the view's region | a caret move reveals through `glide_to`; a page key is `step` |
| the Map's camera, the sprite pickers | `ScrollRegion` on two axes | as today | `step` |

Carts are out: a cart's `btn`/`btnp` and its own scrolling are the cart's
(L9), and the one surface that scrolls inside a cart window, Notes, scrolls
through the shell's `EditorHandle`, which is shell code (§5).

### 2.5 The browser

The motion is C compiled into the wasm head; the band contract (§3) draws
the same pixels as a full repaint on every tier by its guard; the wasm
canvas is one persistent buffer and shifts in place (§3.2). So a fling on
the page is the host's fling, frame for frame under the same injected dt,
and `tools/web.py shot` against the host's screenshot is the guard (§13).

## 3. The frame: what changed, and what the buffer lacks

### 3.1 The change record

Surface model §14.3 gives a window content three names: `HELD`,
`retained(known, x0, y0, x1, y1)` before a draw (whether the target holds
the page, and what lags) and `changed()` after it (what the draw changed).
This design makes every scrolling and text content speak them, and widens
what a change can be. A change is one of:

- **rows**: a rect list the draw repainted (a focused row and the one it
  left; an edited line and the caret's; a changed cell);
- **a translation**: a rect R moved by Δ along its axis, plus the band of
  |Δ| rows R exposed, plus rows (the fades, the scrollbar, a stop whose
  record changed under the move).

The WM's shown ring (`WindowedWM._shown`) records the change as given. A
buffer's lag, which `retained` reports, composes the changes of the frames
since that buffer was painted: translations of the same R compose by adding
their Δ, and a lag that is a translation plus rows is what the content
repaints cheaply -- `shift_last` for the translation (§3.2), the rows and
the band drawn. A lag that composes to anything else (two regions, a
resize, a look change) is reported as "unknown" and the content draws whole,
as every content did before.

Full-screen contents (the handheld's home, apps and Editor; the P4's
full-screen windows draw through the WM) have no window ring. They keep the
same entry themselves: `ui_draw.PaintRing`, the `ScrollRegion._painted` ring
of #113 extracted -- entries of (frame number, key, offset, stamp, change),
`vouch(cv, frame_no, key)` answering the entry whose buffer this frame paints
into and the translation since, else None. `ScrollRegion` and `TextView` own
one; the home's `_still_ring` (§14.4) becomes one whose change is its parts'
rects. The WM's `_shown` stays the windows'. The one-body guard is a grep:
no other list of painted frames in `runtime/` or `native/moy_ui/`.

### 3.2 The backend verb: `shift_last`

`shift_last(x, y, w, h, dx, dy)` on the root canvas, beside §14.6's
`copy_last`: the rect of the last presented frame's buffer, copied into the
one this frame draws into, moved by (dx, dy). It answers False where it
cannot (no last frame, a gap, a rect it does not have), and the caller draws.
Per tier:

| tier | the last frame's buffer | the shift | cost (#113's comment) |
|---|---|---|---|
| Waveshare and Guition P4 (DSI and rotated compositors, `moy_dsi_last`) | another buffer of the ring (the front or pending one), fenced as `copy_last` fences | the 2D-DMA M2M block copy (`esp_async_fbcpy`), submitted async, fenced at the content's close; cross-buffer in either direction | 0.04 ms to submit; 4.6-10.4 ms to land beside the pass's Python |
| T-Deck and Guition S3 (the banded compositor, `moy_banded_last`) | the other of two buffers, the one the feeder is shipping | a CPU row copy, synchronous, reading alongside the feeder (read against read) | 3.4 ms for 320×190; 2.8 for 296×160 |
| host, web runner (one persistent buffer) | this buffer | `moy_gfx.scroll_rect` in place | the host's; the wasm's |

The S3 lever flips from `None` (the 2026-10-23 verdict) because what the
shift buys there is not the region's fill, which that verdict priced, but
the rows' Python and raster outside the band, which §3.4 skips -- the
T-Deck comment's second model. The CPU in-place shift stays out everywhere
a second buffer exists: it costs twice the cross copy on the S3s and loses
on the P4s. No tier reports `None` for `shift_last` today: every root
retains at least its one buffer; a backend that retained nothing would.

**The P4's fence** keeps the four rules of the 2026-10-23 design: before
the submit, fence every op pending on the source buffer (a deferred stamp,
a rotate in flight) and write back the destination rows' dirty cache lines;
between submit and fence, no CPU write lands in a cache line of the
destination rows -- a write that would (through the choke point, §3.3)
fences first; at the fence, wait and invalidate the destination rows'
cache; the frame's own async ops keep their order, the shift landing before
the content's close so `end_frame`'s last-write op is still last. The
Guition's rotate shares the DMA2D, so a shift waits behind a rotate in
flight; the gain is measured on a live whole frame, never in isolation.

### 3.3 A shift frame, in the verbs engine

A frame of a nest R whose only change is its offset is a **shift frame**:

1. **Licensed by producer signals**: nothing marked the console dirty but
   R's own motion (its drag, fling or glide), no press is live but R's
   claimed drag, nothing animates over the surface, no tooltip, toast,
   popover, menu or scrim shows, R's view rect, content extent, look and
   window content rect equal the ring entry's, every gen R depends on
   equals the entry's (`o.gen` on `ui.scroll`, `ui.page` and `ui.table` for
   what the app draws inside R itself: covers, thumbnails, pictures), and
   the entry recorded no overdraw of R after R's close but the cursor stamp.
2. **The ground leaves R out**; at R's open the engine submits `shift_last`
   for R's rect by Δ and sets the **floor**: the band of |Δ| rows, filled
   with R's ground.
3. **The app's pass runs.** Every verb runs its layout, focus and
   hit-testing; its raster is clipped to the floor. A stop wholly outside
   the floor answers 0-size and draws nothing; a stop that touches it draws
   clipped. **The floor widens**: a stop whose record (kind, id, text,
   value, state, box relative to the offset, a table's cells) differs from
   the last frame's stop of the same id has changed under the move, so its
   box joins the floor, its ground is filled and it draws whole. The
   comparison can only ADD to the floor; it never licenses a skip (L2).
4. **At R's close** the engine fences the shift, lifts the floor, writes
   back the keeps the shift moved (the fades `moy_ui_fade` captured, the
   cursor stamp's pixels at the stamp's rect minus Δ) in drawing order, cut
   to outside the band, and takes the new keeps after the fence.
5. **A structural change** -- a stop the last frame had that this pass did
   not draw, a new stop, an order change -- is known only at the close. The
   engine then re-runs the pass unclipped as a **replay** (`ui_frame`
   begins with the input consumed, so no stop activates twice), and R takes
   no shift for a hold-off of 8 frames. A replay is the only path that
   costs a second pass; a stop whose content changes is paid as its rows.

**The choke point.** The C row's write entry (`native/moy_glass/moy_canvas.h`)
applies the floor, records overdraw and fences, for every write path: the
verbs' fills, text and masks; `rect`, `cls`, `blit565`, `cover`,
`picture`; the PPA fill and cover; `reset_state` and `set_viewport`.
`clip(...)` inside R stores the rect intersected with the floor; `clip()`
returns to it (`device/device_canvas.py` and the C row alike). The band
invariant holds: inside the floor every pixel is painted from the ground
up, so an anti-aliased edge never blends over itself.

**Skipping the per-row Python outside the band.** The engine's pass is C
per verb, but the app's loop issuing the verbs is Python, and on the T-Deck
that is 40% of a frame. `ui.band()` answers the floor in page coordinates
(the whole page on a full paint); an app that loops over cards skips the
cards it does not touch -- Get Carts, My Art, Storybook, Files' side -- and
`ui.table` skips its own rows. An app that does not read it pays the
dispatch and is still correct. This is a Moybyte-side verb of the public UI
API (`docs/ui_api_2026-10.md`), not moy-spec's.

### 3.4 The T-Deck, and full screen

The handheld paints full screen through `FullscreenStackWM`, whose stack
repaints whole under the gate; it has no debt and no shown ring, and keeps
them so (§5.1 of the surface model: do nothing here, beyond the verb). The
content's own `PaintRing` entry vouches for the buffer: the last painted
frame is frame n−1 (a gap is harmless on this tier -- an idle loop changes
neither buffer -- but a frame that painted anything else between them
breaks the chain), its key equals this frame's, and the buffer the feeder
is shipping is the source. A shift frame then is the cross copy (3.4 ms),
the band, the rows, and the bar strip. On a P4 full-screen window the WM's
ring vouches, as windowed.

### 3.5 Text contents and the home

The same record, two more speakers. A `TextView` (§5) reports its change as
a translation of the text rect by k lines plus the lines it edited and the
caret's; a home arrangement (§8) as a translation of its band plus the cells
whose selection changed. Each draws its lag from the record: rows are drawn,
translations shift.

## 4. The verbs engine: which frames become shift frames

With §2 and §3 together, a verbs page's frames are:

| frame | today | designed |
|---|---|---|
| drag, fling | whole window; a fling stops at release | shift frame; the fling coasts in shift frames |
| key step, repeat step, ball step | two whole paints, then N−1 whole-glass copies | one frame: focus moves (two rows) and the reveal becomes a glide; each glide frame a shift frame with the two rows; then N−1 copies of the window's rows (§7) |
| typing into a field | whole window | the field's row (§6) |
| a tap, a value change | whole window | the stop's rows |

The rows are the floor of §3.3 with no translation: a frame whose change is
rows only (a focus move with no scroll, a typed key) licenses the same way,
the floor the rows' union, every other stop clipped to nothing. Where a
change is neither (a page switch, a look change, a dialog opening), the page
paints whole, as today, and that full paint re-arms the ring.

## 5. Text editors: the edited line and the caret

**One body: `TextView`** (`runtime/ui_draw.py`), the view of a text buffer
in lines: the first visible line, the lines' pitch, the caret, the
selection, and a `PaintRing`. The Code tab (`CodeLayer`, which the theme
Config text extends as `ConfigLayer`), the editor handle (`EditorHandle`,
which the Player makes for Notes and which the cart's canvas draws), the
verbs' fields and the text console draw their lines through it. The buffer
stays where it is (`CodeEditor`, the handle's document, the console's ring);
`TextView` is given what changed:

- **an edit** marks the edited line(s) -- the line typed into, every line
  below it when a line was split or joined (a translation of the lines below
  by one pitch, plus the two lines), a paste's range;
- **a caret move** marks the caret's old and new lines (the caret is solid,
  §14.1's invariant, so the two rows are the whole change);
- **a scroll** by k lines is a translation of the text rect by k pitches plus
  the k exposed lines; a drag scrolls by pixels through the view's
  `ScrollRegion`, a key page by lines through `step`, a caret leaving the
  view reveals it through `glide_to`, every frame of which is a translation.

`TextView.draw(cv, ...)` takes the lag from the WM's `retained` (windowed) or
its own ring (full screen), composes it with the frame's change, shifts the
translation with `shift_last` and draws the rows: the line's ground, its
text (the Code tab's highlighter runs for those lines only), the gutter
number, the caret, the selection's spans. Lines outside the rows are not
laid out -- the T-Deck's `qstr_find_strn` share (strings interned per frame
for every visible line) goes with them. The whole view draws when the lag is
unknown, the font or wrap changed, or the find's match highlight moved
(a find's next match is a scroll plus the match's rows).

**Each editor, what it keeps and what it drops.**

- *The Code tab* keeps `draw_lines` as the row renderer and loses its
  whole-pane loop; the status band and the bar strip are outside the text
  rect and draw as today (the strip through the WM's bar word, `strip_moved`).
  Its drag pans by whole lines on any upward movement today
  (`CodeLayer._code_drag` floors the delta); the view's `ScrollRegion` in
  pixels replaces it, and a drag scrolls as a finger does.
- *The theme Config text* is the Code tab's class and takes all of it;
  `ThemeDoc.follow` runs before the draw as today.
- *Notes* runs as a cart in a window, and its handle draws into the cart's
  canvas, one persistent buffer: the translation is an in-place
  `scroll_rect` there, the rows drawn over it. The cart's frame is composited
  by the Player; the handle DECLARES its change rect through the cart's
  frame (as `patch_cart_frame` declares the FPS chip's rect, §5.1 of the
  surface model), so the composite copies the rows and not the canvas, and
  a frame with no change composites nothing. The cart still ticks at its
  rate; what it draws is the rows.
- *The fields* (`ui.field`: Settings' name, Cart info's, Blocks' prompts,
  Notes' new-note name) are one row each inside the verbs engine: a typed
  key changes the field stop's record, so §3.3's floor is the field's box.
  Under a dialog the page is held (§14.5) and the dialog's own rows are its
  floor; the Blocks prompts' 121 ms keystroke becomes the field's row.
- *The text console* (`runtime/text_console.py`) draws through the view for
  its scrollback rows and its prompt line; a script that prints appends
  rows, which are a translation of the scrollback by the lines appended
  plus those lines. Its frame cadence is the script's (a cart's loop), and
  both boards measure it under target; it takes the view for the one body,
  not for a row it needs.

**The cost** of a keystroke becomes the row's raster (one line of glyphs,
~0.3-0.5 ms on the P4 at the mono font, a few hundred µs on the T-Deck) and
the frame's fixed part; of a caret step the same twice; of a scroll by a
line the shift plus a line.

## 6. Typing and one row

A typed key is delivered as a count (§2.2), and the surface applies every
count to the buffer and marks the rows once; the frame draws them once. So
at 20 keys/s on a 33 ms frame nothing coalesces, and at a slower frame the
characters that landed between two frames are shown together, which is
what a typist sees on any editor; no key is dropped on any surface today,
and none is after.

The caret is solid everywhere (§14.1), and stays so: a blinking caret would
be a Class B source and would defeat the freeze under it. A text surface
whose caret must read as live under a held page draws it in the rows it
owns and nowhere else.

## 7. One paint per press

**Why two paints and two copies today.** §1 says it: the verbs engine
reports every frame whose input moved anything as unsettled, the host owes
a frame on it and paints the page whole again; then the windowed WM's
`_full_debt` brings the other buffers up by copying the whole glass, once
per buffer, while the desk is calm. A gamepad press pays the same; the
T-Deck's lists pay the two paints (16 painted frames for 8 presses on
Settings and Licences) and no copies.

**The engine tells a move it drew from an action it owes.** `settled`
splits in two: `moved` -- the focus and the offset changed, which
`api_begin` applied before the pass and the pass drew, so nothing is owed --
and `owed` -- an activation (`act`), a typed key the app applies to its own
text, a tap, a dismissed popover, or a geometry change found at the close
(the focused row grew under the reveal), each of which the app may act on
after the pass. Only `owed` marks the next frame; `App.owed` and `still()`
read it, and the layers' `host.owed` checks are unchanged. The reveal keeps
its place at `api_begin` and sets the nest's glide target (`glide_to`,
§2.1) instead of the offset, so the first frame after a press draws the
focus in its two rows and the glide's first step as a shift frame. One paint
per press on every tier.

**The debt is rows, and it is paid by copy.** A change leaves the ring's
other buffers lagging by the change's record (§3.1), not by the glass; the
windowed WM's debt frame copies the record's rects out of the last buffer
(`copy_last` of the window's rows for a page that scrolled -- whole buffer
rows for the GDMA, which is what a window's content rows are) instead of
the whole glass, and brings a buffer that lags by rows only up by those
rows. During a glide no buffer is calm, so no debt is paid until the glide
rests; then N−1 row copies, each a fraction of 13 ms by the rows' share of
the glass, while nothing waits. The intermittent `_full_debt` stuck at 2
after a Blocks-outline fling (the P4 comment, twice in six tries) is in
this code, and the phase that rewrites it (§15) reproduces it first.

## 8. Key and typing frames on the P4 without the stamp

A key frame draws into `win.buf` and stamps it (19.5 ms windowed) because
direct render (`wm_windowed._direct_render`, #155) is taken for content
gestures only -- a frame that paints into the root ring leaves the window
buffer behind, and only a content that speaks §14.3 can be told what lags.
Every scrolling and text content speaks it now (§3.1), so **a speaking
content direct-renders on every frame it draws**, key frames included,
when the WM's `_shown_ok` holds (the window focused and on top, nothing over
it, the chrome quiet). The window buffer then lags, as it does after a
pointer gesture today, and is refilled where it is needed: when the window
loses focus, is occluded, moved or resized, or at idle through the debt
path (a copy of the content rect out of the root, not a redraw). A frame
the ring cannot vouch for seeds it by stamping the window buffer into it,
as §14.3 does today. The stamp disappears from the key and typing rows; a
full-screen window's 12.2-12.7 ms stamp goes the same way.

## 9. The home's arrangements, PROJECTS and the sprite pickers

**The grid and icons arrangements** (`Launcher`, `IconHome`) repaint the
whole home every painted frame (84-115 ms on the P4, 60-102 on the T-Deck),
while the carousel repaints a band and shifts the rest (24-27 on the
T-Deck's same inputs, 32-40 on the P4's). The same path reaches them, with
two changes:

- **a selection step is two cells**: the drag fast path (`_draw_home_fast`)
  runs on a key, repeat or ball step too, its floor the cells whose
  selection changed, and on a glide the band's translation plus the cells
  exposed;
- **the band shifts where the ground is still**: `Launcher.draw_shift`
  repaints the band whole today because the cards lie on the wallpaper. Where
  the backdrop has a restore key that is a plain fill or the theme's baked
  backdrop (`Wallpaper.restore_key`, §14.4), the ground moves with the cards
  and the band is a translation; a picture wallpaper and a cart wallpaper
  keep the band repaint, with only the exposed cells drawn (the covers are
  one `blit_batch`, not a blit per card, which is the T-Deck's 33% memcpy
  share).

The desk home's still ring (§14.4) already keeps the parts' keys; its
entries become `PaintRing` entries (§3.1). The PROJECTS window is a
`Carousel` in a window (`HomeWindowLayer`) and speaks the record through the
WM like any windowed content. The tab switch by key (113-121 ms on the P4)
is a page change and paints whole; its rows are under their own 100 ms
target on the T-Deck and the P4's are reached by the backdrop's restore
(§14.4), not by this design.

**The sprite pickers** (`paint_layer.py`'s grid, the theme editor's)
read the sheet pixel by pixel into quads (`sheet.pget`, `moy_sheet_pget` 15%
of the T-Deck's busy samples). The picker is a scaled view of an indexed
sheet: it draws through `moy_gfx.blit_indices` (the compositor's
indexed-to-565 blit) at the cell scale in one call per visible sheet rect,
the grid lines and the selection over it, and scrolls as a `ScrollRegion`
on two axes with the band contract. 39-40 ms a frame on the T-Deck becomes
one blit of the visible cells.

**My Art's covers** are built inside its draw (a 0.6-2.5 s frame per
gesture on the P4). They move off the paint path: built one per idle frame
through the console's idle step, as Get Carts' cover runs are, with the
app's `o.gen` moving as each lands. This is a fix, not a mechanism, and it is
the first thing built (§15).

## 10. Why this is not a second invalidation mechanism

Whether a surface draws is still decided by the gate, the gens and §14.1's
freeze. The record only says, for a frame that IS drawing, what the buffer
lacks and how much of the content to rasterize; `shift_last` only replaces
the drawing of a lag the record names, as `copy_last` does for a whole
frame. That is L5's line: the shared layer decides whether, the backend
how. Against §8's graveyard: no widget reports damage (one region, one
translation, one record per frame); nothing is inferred (a shift frame is
licensed by producer signals and gens, and the per-stop comparison and the
replay can only widen or force a repaint, never license a skip -- the line
L2 gains); no root framebuffer is retained across web frames (the wasm
canvas is one persistent buffer and shifts in place, which is retention of
one buffer, as every raster tier declares); the PPA copies nothing 1:1
(the 2D-DMA is a different engine, and what it buys is the CPU's time); and
the cost that goes is the region's raster and the rows' Python, measured on
a live whole frame.

**The surface model is amended in the same commit** as the first ring
entry: a §14.7 that states the change record and `shift_last` (§3.1, §3.2
here, in the model's voice), §7's scroll-as-blit row naming the two ring
bodies (the windows' `_shown`, the contents' `PaintRing`), and L2's added
line.

## 11. What reports `None`

| lever | present | `None` |
|---|---|---|
| `shift_last` across buffers | P4s (2D-DMA), S3s (CPU row copy) | -- (a one-buffer root shifts in place) |
| `shift_last` in place | host, web runner | the P4s and S3s (a second buffer exists; the CPU in-place shift loses) |
| the PPA ground fill before R opens | P4s | S3s, host, web |
| held-key repeat | every level source: the T-Deck matrix, BLE, the host's and browser's keyboards, a gamepad | the T-Deck keyboard's ASCII fallback |
| the trackball's steps and `BALL_COAST` | the T-Deck | every board without a ball |
| direct render of key frames | the windowed WM over a root with `set_viewport` (the P4s, the host desk) | the web runner's canvas (keeps the stamp); the fullscreen WM (no window) |
| the debt copy of rows | roots with `copy_last` and a ring deeper than one | one persistent buffer (owes nothing) |

Where a lever is `None` none of its bookkeeping runs: no ring entry is
written for a shift that cannot happen, no keeps are taken, no overdraw is
recorded. The host runs every mechanism under test as an emulated tier
(§13), which is how a `None` that should have been a value is caught (L8).

## 12. Deliberately left out

- Lua and compiled carts, and a cart's own scrolling: a cart may read `btn`
  in its draw and its `cls` writes past any floor (the 2026-10-23 design's
  reason stands); the Player composites a declared rect at most (§5).
- An overshoot or a rubber band at the clamp: the fling hard-stops, as #113
  v1 did. A parameter the owner can ask for later; it changes the physics
  twin only.
- The first frame of a gesture (the press frame's stamp and backdrop
  restore, Settings' 54 ms against 21 steady): it is the §14.3 seed and
  §14.4's restore, and a shift frame does not reach it.
- A hash of R's raster bytes with a replay on mismatch: replaced by the
  per-stop comparison at draw and the structural replay (§3.3). The hash
  cost a second pass on every mismatch and could not say WHICH stop moved.
- The home's tab switch and every page switch: whole paints, under their own
  target.
- The text console's cadence (a script's loop) and the web console (no
  scrolling or text surface).
- Scrolling inside a popover or dialog body: a dialog's rows are its floor
  (§5), and a popover is overlay, which forces full paints under it; a long
  popover that scrolls takes the nest's state like a page and shifts only
  when a later measurement says it must.

## 13. The guards

Every copy and shift path is pinned byte-for-byte against a full repaint,
on one, two and three buffers presented in turn (the host compositor's
`last` ring), as `tests/test_wm_shown.py`, `test_home_still.py` and
`test_held_page.py` pin §14.3 to §14.6. Per part:

- **The motion** (`tests/test_ui_physics.py`, `test_kinetic_scroll.py`):
  the glide reaches its target exactly, re-aims without a jump, a step moves
  one step from the target, a repeat count of n moves n; the C against the
  twin on the desktop MicroPython in the boards' float model.
- **The input layer** (`tests/test_tdeck_input.py` and the table's own
  suite): a held button repeats after the delay at the rate on the table's
  clock, a frame of two periods owes two, `btnp` stays an edge, a disarm
  zeroes repeats, the ASCII fallback repeats nothing (`None`), a typed key
  repeats as a count and the queue's zero frame does not cap it; every
  modal, menu and dialog activates on an edge only (a fuzz over the
  surfaces with repeats on: no activation count moves).
- **The trackball**: 30 of 30 steps delivered at 30 steps/s on every
  stepping surface (the merged bench's `ball_fast` row, §14), `BALL_COAST`
  0 adds no velocity, a value adds the surplus's.
- **The shift frame** (the verbs engine, console-level through the WM and
  full screen): for every verbs app and every text surface, a scripted drag,
  fling, key step, held key and typed line, rendered shift-and-rows and
  forced-full, every presented frame byte-identical, on 1, 2 and 3 buffers,
  with the cursor showing, with a tooltip and a toast arriving mid-gesture
  (each forces a full paint and the pixels still match), both directions,
  |Δ| from 1 to the view less one, fades at either end, the clamp at either
  end; the fixtures carry decoded covers.
- **Two emulated DMAs** (the copy landing at submit, and at the fence) must
  produce the same frames.
- **Each break forces a full paint**, with a counting canvas: a cover
  landing mid-drag, a look change, a window moved during a fling, a
  painted-frame gap, an overdraw other than the cursor, an app that draws in
  R with no gen, a stop that vanished (the replay fires, once, then the
  hold-off).
- **The liar**: an app that mutates a stop's text without a signal still
  matches the full paint (its row joins the floor).
- **The floor and the choke point**: `clip` inside R intersects the floor
  and `clip()` returns to it; `cover`, `picture`, `blit565`, `cls`, the PPA
  fill and `set_viewport` write no pixel outside the floor inside R, or
  fence first; `ui.band()` equals the floor in page coordinates.
- **One paint per press**: a key step on every verbs page paints exactly one
  frame, then N−1 copies whose bytes are bounded by the window's rows; the
  intermittent stuck debt has a regression test once reproduced.
- **Direct render of key frames**: the window buffer's lag is the record's
  composition; a focus loss, occlusion, move and resize find it refilled
  byte-equal to a live draw.
- **The text view**: for the Code tab, theme Config, Notes, each field and
  the console: typing, caret moves, split and join, paste, find, a page key,
  a drag -- every frame byte-equal to a whole draw; the highlighter runs
  for the rows only (a counting highlighter).
- **The home and the pickers**: a selection step draws two cells, a shift
  on a plain ground is a translation, a picture wallpaper repaints the
  exposed cells only, the picker's blit matches the per-pixel draw byte for
  byte on every scale and scroll.
- **L8's silent-disable direction**: over a scripted fling, a glide and a
  typed line, the count of shift frames and row frames stays above a floor,
  per surface; a mechanism that quietly never engages fails.
- **One body**: the grep guards of §2.1 and §3.1.
- **The web**: `tools/web.py shot` of a page mid-fling and mid-glide under
  injected dt equals the host's screenshot (the existing web goldens gain
  the two scenes).
- **On glass, in each board's suite**: `shift_last` byte-exact against
  `moy_gfx.scroll_rect` in both directions; on a P4 the primed-cache test
  (destination lines pulled into cache before the copy, read after the
  fence) and a CPU write into the destination rows during the flight
  fenced first; on an S3 the copy exact while the feeder ships the source.

## 14. One measuring tool

Two tools were written for the baselines: `+tools/scroll_bench.py` (commit
`83d2ccaa`, worktree `scrollbase_p4`: the P4, the frame's split into content,
stamp, home, copy and rest, a wall-clock input source) and
`+tools/input_bench.py` (commit `2cb0bc05`, worktree `scrollbase_td`: the
T-Deck, the trackball through `ws.ball`, keys and held buttons through an
input-table source, the loop's split under PERF DIAG). Both type into real
seed carts (Star Catcher, Coin Quest), left their fixtures behind, and one
broke Star Catcher on the T-Deck. Neither is on dev; one tool lands:

- **`+tools/input_bench.py`**, for every board: `scroll_bench`'s frame split
  and groups folded in (the P4's rows need the WM's split, the T-Deck's the
  loop's), one input vocabulary (`fling`, `drag`, `key_steps`, `key_held`,
  `key_20`, `type`, `type_fast`, `type_line`, `ball_slow`, `ball_fast`,
  `find`, `paste`), every input played inside the frame loop on wall-clock
  time through the bench's input source, so a finger moves at its speed and
  a key goes down and up on its schedule whatever the frame rate.
- **A scratch project.** The bench copies a seed cart into a project of its
  own name (`Bench Scratch`) at setup and types, pastes and finds in that; it
  makes its long note and its console script under the same name; a seed
  cart is never opened for writing.
- **Cleanup on exit**, in a `finally` that runs on a failure and on Ctrl-C:
  the scratch project, note and script deleted, the home's tab, the theme
  and the window sizes restored, PERF DIAG and `uncap` put back, the board
  left on the home.
- **Its rows** carry: paints (n, median, p90, max), debt copies, the longest
  gap while the input moves, the paint rate under live input, what moved
  after the input (coast or glide frames, and for how long), per-press
  latency, presses coalesced, keys dropped, steps delivered of steps rolled,
  and the frame's split; `--against` prints a run beside an earlier one with
  regressions flagged, as `shell_bench.py --report --against` does.
- **The estimate's arithmetic**, stated once: a reached row's expected
  frame = the baseline's `rest` + `other` (the frame's fixed part and the
  layers outside the surface) + the shift's CPU cost on that tier (§3.2) +
  the rows' raster (a band of |Δ| rows, or the changed lines, at the
  baseline's content raster scaled by their share of the view). The tool
  prints this expectation beside each row of a baseline run, so a phase's
  gate is a column, not a conversation.

## 15. Build order

Phases are sized for one agent each, worst rows first; the ones that touch
the Editor's surfaces (P4, P5, P7) run one after another; P6 is
independent of them. Each phase ends with its measurement step on the
Waveshare P4 and the T-Deck through §14's tool, verdicts to #66, and the
Guitions measured when a P4 or an S3 path changed.

| phase | builds | rows reached (baseline p90 → expected, P4 / T-Deck) | guard |
|---|---|---|---|
| **P0 the tool** | `+tools/input_bench.py` merged (§14); My Art's covers off the paint path (§9) | My Art every row (0.6-2.5 s frames gone; drag 73 → ~57 / 25) | the tool's own test on the host sim; My Art's fling frames bounded |
| **P1 the shift frame** | `shift_last` on every tier with the P4 fence and the S3 row copy; the floor and the choke point; the verbs' nests' ring entries and change record; `ui.band()`; the surface model's §14.7 | every verbs app's drag and fling, windowed and full screen: P4 Settings fling 55 → ~12, Files 79 → ~15, Get Carts 58 → ~14, Storybook 73 → ~12, Appearance 87 → ~15, Calc 54-65 → ~12, Config 67 → ~15, the Blocks outline 66-92 → ~16; T-Deck Settings 25 → ~13, Get Carts 20 → ~11, Calc 23 → ~11, Config 25 → ~12, My Art 25-30 → ~13 | §13's shift-frame, floor, DMA, break and liar tests; on-glass verb tests |
| **P2 one paint per press** | the reveal at begin; the debt as rows by copy; direct render of key frames; the stuck-debt reproduction | every verbs page's key, held-key and 20/s row: P4 Settings key steps 33 → ~11, Files 56-60 → ~14, Get Carts 51-59 → ~13, Appearance 56-64 → ~14, Calc 31-63 → ~12, Config 68 → ~15, Music steps 86 → ~16, Blocks outline 96 → ~17; T-Deck's list pages lose their second paint (Settings and Licences 16 paints for 8 presses → 8) | one-paint test; the debt's bytes bounded; the direct-render lag tests |
| **P3 the motion** | the glide in `moy_ui_scroll_t`; the fling on every nest; the carousel's glide deleted; the input layer's repeat and counts; the trackball's steps and `BALL_COAST` | every row's motion: a fling coasts everywhere, a key step glides in shift frames; the T-Deck's `ball_fast` rows deliver 30 of 30 (13-27 today); held keys move on every surface (none today); the P4's held-key rows stop repeating per frame (Code, Notes, Music ~11 Hz with no delay → the rate) | physics twin, input-table suite, the ball rows, the edge-only activation fuzz |
| **P4 the Code tab and theme Config** | `TextView` over `draw_lines`; the view's `ScrollRegion`; the WM record for the text rect; the Code tab's drag by pixels | P4 Code key steps 86 → ~12, typing 86-92 → ~12, held key 84 → ~12, fling 87-105 → ~14, find 104 → ~20, drag 57-82 → ~14; theme Config text 91-121 → ~12-14; T-Deck Code type 40 → ~10, find 68 → ~18, drag 24 → ~10, theme Config 12-15 → ~8 | the text-view tests on 1, 2 and 3 buffers; the counting highlighter |
| **P5 fields, dialogs, Notes, the console** | the field's row in the engine; Notes' handle on `TextView` with the declared rect through the Player; the console's rows | P4 Blocks prompts 123 → ~15, Cart info fields 86 → ~14, Notes typing 59-93 → ~20 (the cart's tick stays), Notes key steps 93 → ~20; T-Deck Notes 21-23 → ~9, the Settings name field 25 → ~9, Blocks prompts 19 → ~8 | the field and dialog rows under the held page; Notes' declared rect byte-equal to a whole composite |
| **P6 the home and PROJECTS** | the fast path on steps; the band's translation on a still ground; `blit_batch` covers; the still ring as `PaintRing` | P4 home grid 99-123 → ~35 (the carousel's 33-57 is the evidence), home icons 97-123 → ~35; T-Deck grid 80-87 → ~25, icons 92-102 → ~25, the carousel's settle frame 48-51 → ~26 | §13's home tests; `test_home_still.py` extended |
| **P7 the pickers and the Editor's panes** | the picker's indexed blit and two-axis region; the Map's camera, Music's steps and Config's cards on the nest state (they are verbs pages or two-axis regions already by P3) | T-Deck sprite pickers 40 → ~15; P4 sprite picker 93-96 → ~20, Map 68-81 → ~18 | the picker's byte-equality on every scale and scroll |

**Order and dependencies.** P0 first (the tool measures every later gate).
P1 before P3: a glide before the shift frame would cost the T-Deck its frame
rate (seven whole paints a step, the T-Deck comment's last bullet). P2
before P3 for the same reason on the P4 (a glide of frames each paying two
paints and two copies). P4 → P5 → P7 in that order on the Editor; P6 may
run beside P4. The surface model amendment lands with P1.

## 16. moy-spec's verb table

Nothing here touches it. `btn` and `btnp` keep their meaning for a cart (a
repeat is a console-side count, never a cart's edge); the cart canvas verbs
are unchanged (`scroll_rect`, `blit_indices` and `shift_last` are
`moy_gfx`'s compositor and the root canvas's, not SPEC.md's); `ui.band()`
and the kinetic `ui.scroll` are the public UI API, Moybyte's
(`docs/ui_api_2026-10.md`), and an app on the verbs passes nothing new to
get them. The physics' parameters are not a cart's to set.

## 17. Open questions for the owner

1. **The trackball's momentum** (§2.3): `BALL_COAST` ships at 0; the value,
   if any, is tried by hand on the T-Deck once P3 lands. The design asks
   nothing else.
2. **The repeat's feel**: `REPEAT_DELAY_MS` 400 and `REPEAT_RATE_MS` 50 are
   a desktop keyboard's defaults; a child's hand on the T-Deck may want a
   longer delay. Configuration, settable per board in board.toml if the
   trial says so.
3. **Direct render of key frames on the host desk** (§8): the host sim takes
   it like the P4, which changes nothing a golden sees but makes the host
   the test bed; the alternative is to keep the host on the stamp and test
   direct render only on glass. The design takes the first.
4. **A rubber band at the clamp** (§12): left out; a yes adds a parameter
   to the twin and a frame or two to a fling's end.
