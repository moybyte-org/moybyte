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
found and why that scope was wrong. The revision was put through parallel
adversarial architecture and performance reviews on 2026-10-27; §18 is the
ledger of what each finding changed.

**The measurements.** Every number this doc reasons from lives in an issue:
the per-board shift costs in
[#113's 2026-10-23 comment](https://github.com/moybyte-org/moybyte/issues/113#issuecomment-6359246826),
the Waveshare P4's baseline of every scrolling and text surface in
[#66's P4 comment of 2026-10-27](https://github.com/moybyte-org/moybyte/issues/66#issuecomment-6441340435)
and the T-Deck's in
[#66's T-Deck comment of 2026-10-27](https://github.com/moybyte-org/moybyte/issues/66#issuecomment-6441869611)
(both at dev `06fad00a`). Where a row is named below with a number, the
number is that baseline's p90 at that commit, and the number after the arrow
is ESTIMATED by §14's arithmetic from the row's own frames; the build's
measurement step (§15) replaces every estimate with a verdict in #66, and
§15's last paragraph names the rows that stay over the target after every
phase, with the next lever for each.

**The goals (owner, 2026-10-27).** Scrolling and text editing everywhere --
Code, Play, the Editor's tabs, every app -- at most 33 ms a frame at p90,
60 fps where the board can, on the Waveshare P4 and the T-Deck; the Guitions
are measured, not targets. Every scroll is kinetic: a touch drag and fling
coast with momentum everywhere; a key, held-key or trackball step carries no
momentum (a step moves exactly one step) but glides to its target eased, and
a held key reads as one continuous movement. A fast trackball roll's
momentum is a parameter tried by hand (§2.3). The browser never looks
different from the boards: host and device are one codebase, and every
mechanism here runs on the host under test.

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
  33 ms in two thirds of the over-target rows). Key and typing frames are
  not direct render: they draw into the window buffer and stamp it
  (19.5-19.9 ms windowed, 12.2-12.7 full screen). A key step is paid two to
  three times: two whole paints right after the press, then two whole-glass
  debt copies of 12-13 ms, so presses at 20/s coalesce (of a 112-character
  line typed on Code, 43 characters never get a frame of their own). A held
  key repeats once per painted frame with no initial delay (Code, Notes,
  Music at ~11 Hz). Momentum exists on the home carousel and PROJECTS only.
  My Art builds its covers inside its draw (one 0.6-2.5 s frame per
  gesture). The home's grid and icons arrangements paint 84-115 ms a frame.
  The first frame of every gesture is the p90 of most fling rows: it stamps
  the window buffer and restores the backdrop (Settings 54 ms against 21
  steady).
- **T-Deck.** 26 of 142 rows are over; most surfaces paint 18-28 ms, the
  flush never holds a frame (it runs on core 0 behind the next frame), and
  the loop's floor is ~60 fps. All 26 are the home's grid and icons (15,
  which repaint the whole home every frame), the sprite pickers (6, the
  sheet drawn pixel by pixel), the frames a motion starts or settles on (3),
  Code's find and My Art's row step. No shell surface repeats a held key; a
  fast roll outruns the three-step queue (13 of 30 steps on the grid). A
  Settings drag frame is 41% Python VM, 32% raster, 14% memcpy; the comment's
  model for a shift that also skips the per-row work outside the band is
  the loop's floor on Settings, and the cross copy of the other buffer costs
  4.3 ms for 320×212 with the feeder running.
- **The 2026-10-23 design** reaches 28 of the P4's rows (the six apps' drags
  and flings) and none of the key, typing or text rows: it ruled a key step
  out of the shift by design ("focus is in the key"), kept the S3 boards at
  `None` from a measurement that priced the region's fill and not the rows'
  work, and did not see the stamp, the double paint, the press frame, the
  held-key repeat, the home or the pickers at all.

**Why a key step paints twice** -- three causes, each a frame. The verbs
engine answers `settled` false for any frame whose input moved anything,
including a focus move and the page's scroll to it that `api_begin` applied
before the pass (`reveal` in `native/moy_ui/moy_ui_api.c`, over the last
frame's stops) and the pass drew; the host owes a frame on it (`AppHost.owed`,
the `host.owed` checks in each layer). Some reveals are set DURING the pass
and applied at the next begin -- a list row's step, a table's, `ui.focus`
-- and that second frame is needed. And four apps mark the whole page
after the pass when an answer changed: Files and Get Carts set their
current item from `ui.focus()` read after the pass and call
`_damage.all()`; Storybook and Appearance call `damage.again()`/`all()`
the same way. The two debt copies are the windowed WM's `_full_debt`
(`RETAINED_FRAMES` − 1 on the Waveshare's ring of three) brought up by
`copy_last` of the whole glass once the desk is calm, §14.6. §7 takes each
apart.

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
  reads as one movement at the step rate. A 20-40 px step is five or six
  halvings, ~230-270 ms of frames; that is the carousel's feel today, and
  the parameter is where it is tuned. The handheld home's carousel keeps
  its slide, the glide's second ease: the target reached in `SLIDE_MS`
  (200), eased out, re-aimed from where the row is. A reveal in a dialog's
  or a popover's body (an overlay, §12) and a focus an app moves lands at
  once;
- **a drag** catches a fling or a glide, as today.

A step is `step(n, size)`: the target moves by n steps of the surface's step
size (a row, a card, a line) from the target if a glide is live, else from
the offset -- a step moves exactly one step, never more, and n steps in one
frame move the target n steps at once (the view is never more than one
glide behind its target). `glide_to(p)` aims at a content position (a focus
reveal, §7). `ScrollRegion` (`runtime/ui_draw.py`) stays the Python face
over the same buffer and gains `step`, `glide_to` and `gliding`;
`Carousel.anim_frame`'s own glide (`runtime/launcher_layer.py`) is deleted
for it, with a grep guard that no `_GLIDE_HALF_MS` or ease arithmetic
exists outside `moy_ui_interact.c`.

**The engine gets a clock word.** `api_begin` has no dt today; `ui_frame`'s
`inp` array gains `MOY_UI_IN_DT` (the loop's tick in ms, injected -- never a
clock read -- so the physics stays deterministic under test); a Lua or
compiled app's run takes the sum of its ticks' injected dt since its last
frame (`moycore_run_tick_dt`).

**The verbs engine's nests scroll on it too.** A page body, a `ui.scroll`
region and a `ui.table`'s rows are nests whose offset is a `moy_ui_keep_t`
(`a`); today a drag writes `a` from the pointer's travel directly and a
release stops dead. Each scrolling keep gains a `moy_ui_scroll_t`, driven by
the same `moy_ui_dragtap_frame` the launcher rides, so a release flings and
`api_begin` ticks every live state before the pass. The physics is therefore
ONE function on every surface, and the twin test runs it once.

**Motion is a declared Class B source.** `api_moving(ctx)` answers whether
any nest of the context glides or flings; `AppHost.keys()` and `still()`
read it beside `owed`, the frame gate reads it as animation, and the
windowed WM reads it in place of `ws._animating` for the content freeze and
direct render (§8): a surface moving on its own is the one legitimate
reason to paint with nothing dirty, and the WM must know it is the focused
window's content and nothing else.

Parameters are configuration, not measurements: `GLIDE_HALF_MS` 45 in
`moy_ui_interact.c` beside `MOY_UI_FRICTION`; `REPEAT_DELAY_MS` 400,
`REPEAT_RATE_MS` 50 and `BALL_COAST` 0 in `moy_input` (§2.2, §2.3), each
overridable per board in its board.toml.

### 2.2 The inputs: a held key repeats at the input layer

A held button repeats in `moy_input`'s merge (`moy_input_begin_frame`,
`native/moy_input/moy_input.h`), per source and per button, on a clock the
caller passes in -- `begin_frame(t, now_ms)`, 0 for none -- so
the count is against time, not the frame: after `REPEAT_DELAY_MS` of level,
one repeat every `REPEAT_RATE_MS`, and a frame that took two periods owes
two. The table exposes the count: `moy_input_repeats(button)` this frame
(the binding's `repeats(name)`), and `steps(name)` = pressed edge +
repeats. `btnp` stays an edge (a cart's `btnp` is unchanged, L9; a game that
wants repeat has `btn`), and `moy_input_clear_edges` (a modal opening on a
key) zeroes the repeats with the edges and starts each held key's delay
over. A surface that takes a frame's steps for itself (a caret) spends them
with `moy_input_spend_edges`: no layer after it sees them, and the held key
keeps its level and its schedule, so it neither presses again at the next
merge nor waits out the delay again.

Typed keys repeat from a level the table keeps for that purpose. A source's
`key` is a level a BLE keyboard holds; the T-Deck in text mode types
one-shot press edges from its matrix (`moy_kbd.c` `type_edges`) and holds no
key level, and its `text_mask` strips the aliased buttons. The driver writes
the matrix's typing key into a **typing-level slot** of its source that
carts never read, and the merge repeats from that slot and from a BLE
source's key level, delivering a count (`moy_input_key_repeats`) beside the
frame's key event. Repeat counts never enter `key()`, `keyp()` or `last_key`:
a cart in text mode is promised each typed key once
(`docs/moy_cart_api.md`), and that promise holds. The verbs engine reads two
new input words, `MOY_UI_IN_REP` (four 8-bit direction counts) and
`MOY_UI_IN_KEYREP`; a text surface takes the count rather than one key per
frame, so the one-shot queue's zero frame between equal keys does not cap
the rate.

**One repeat, from one place.** The browser's page sends a key's down
once, whatever the OS repeats (`e.repeat` dropped for every code); the
host's and the browser's keyboards both reach `ConsoleDriver.key_down` and
`key_up`, which hold the key's typing level, and the table repeats on every
tier alike, on the frames' injected clock.

Where the level is not known the lever is `None`: the T-Deck keyboard in
its ASCII fallback (an older keyboard firmware) types edges with no matrix,
so it repeats nothing.

The consumer rule, for every surface: a repeat is a step, not a press.
Nothing that opens, activates or commits runs on a repeat; only motion
(focus moves, caret moves, scroll steps) and typing do. `moy_ui`'s key
handling takes `go` and `tab` from the edge word only.

### 2.3 The trackball

A pulse is a step at the input layer (`Console.ball`, `BALL_STEP`). The
steps are delivered as a **count in the `MOY_UI_IN_REP` word** (the table's
`add_steps`, read as `steps(name)` by the Python layers), every pulse's step
in the frame it arrived: no queue and no per-frame cap, so every step a roll
makes reaches the surface (what a fast roll delivers is in #66). The glide absorbs them -- the target moves n
steps and the view eases to it -- so the lag is bounded by construction:
the view is always one glide (a few `GLIDE_HALF_MS`) behind the newest
target, never a queue's length behind. A caret takes the count as lines
(`nav`) as today.

`BALL_COAST` is the parameter the owner tries by hand after the motion
phase (a board's board.toml `[input] ball_coast`): steps a roll delivers
above `BALL_COAST_RATE` per second become a coast instead of steps,
`BALL_COAST` thousandths of a step a ms per surplus step, delivered on as
steps that decay with the fling's friction -- so every surface that takes
steps coasts, a focus-driven list as well as a page; 0 is no momentum, the
default.

### 2.4 Who takes it, from one place

| surface | scrolls through | drag/fling | key, repeat, ball steps |
|---|---|---|---|
| verbs-engine pages, `ui.scroll`, `ui.table` rows (Settings and its pages, Files, Get Carts, Storybook, My Art, Calc, Appearance, Music's steps; the Blocks outline and Config's cards once ported onto nests, §15 P5) | the nest's `moy_ui_scroll_t` | the engine's drag-tap; the fling | focus moves a stop; the reveal is `glide_to` (§7) |
| the home's carousel, grid and icons; the PROJECTS window | `ScrollRegion` (`Launcher._scroll_region`) | as today | the selection moves; `glide_to` the card |
| the Code tab, theme Config text, Notes, the fields, the text console | `TextView` (§5) over a `ScrollRegion` in visual rows | the view's region | a caret move reveals through `glide_to`; a page key is `step` |
| the Map's camera, the sprite pickers | `ScrollRegion` on two axes | as today | `step` |

The Blocks outline and Config's cards scroll in Python by whole rows today
(`block_editor_ui._outline_drag` and `blk_top`; `cards_layer`'s `mtop`),
each drawn by a Python loop whose every box moves: they take the model only
once they are `ui.scroll` nests, which §15's P5 does. Carts are out: a
cart's `btn`/`btnp` and its own scrolling are the cart's (L9); Notes is a
system cart on the verbs engine whose text is the shell's `EditorHandle`,
and §5 says what it gets.

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
- **a translation**: a scrolling region R moved by Δ along its axis, plus
  the band of |Δ| rows R exposed, plus rows (inside R: the stops the frame
  widened to, §3.3; outside R: what else the frame drew).

**Two rules keep the Δ right**, because a cross-buffer shift and an
in-place shift are different models and the 2026-10-23 text mixed them (the
bug `device/p4_canvas.py` records: a picker drag shifted by twice its
delta and ghosted every card):

- **Inside R, a shift is always n−1 → n by this frame's Δ**, out of the
  buffer frame n−1 drew, licensed only when frame n−1 was a complete paint
  of R (its entry vouched, §3.3) -- whatever the ring's depth. Translations
  never compose: the ring keeps R's offset per entry, and the one Δ that
  matters is the last frame's to this one.
- **Outside R, the buffer this frame paints lags by the rows** of every
  frame since it was last painted (the union of their records' rows), and
  those are drawn as rows. On a one-buffer canvas (the host's, the web's, a
  window buffer, a layer) the last frame IS this buffer, so the shift is
  in place by the same Δ and nothing lags outside R but this frame's rows.

The WM's shown ring (`WindowedWM._shown`) records the change as given, in
preallocated slots (no per-frame tuples: `tests/test_frame_alloc.py`'s pin
extends to shell shift and rows frames). Full-screen contents (the
handheld's home, apps and Editor; the P4's full-screen windows draw through
the WM) have no window ring and keep the same entry themselves:
`ui_draw.PaintRing`, `ScrollRegion.ring` (#113's ring) extracted --
slots of (frame number, canvas, key, offset, stamp, change, whole), `vouch(cv, frame_no,
key)` answering whether frame n−1 painted this region completely under this
key into the buffer `shift_last` reads, else None. **One voucher per
region**: under the windowed WM only `_shown` vouches (a content's own ring
never does there, or a toast drawn in n−1 would be shifted in); under the
fullscreen WM the content's `PaintRing` does. `ScrollRegion` and `TextView`
own one; the home's `_still_ring` (§14.4) is one whose change is its
parts' keys and the bar's. The one-body guard is a grep: no other list of painted frames
in `runtime/` or `native/moy_ui/`.

### 3.2 The backend verb: `shift_last`

`shift_last(x, y, w, h, dx, dy)` on a canvas, beside §14.6's `copy_last`:
the rect of the last presented frame's buffer, copied into the one this
frame draws into, moved by (dx, dy). It answers False where it cannot (no
last frame, a gap, a rect it does not have, an engine busy with something
it must not wait behind), and the caller draws. The lever is **per canvas**,
not per board: a root's ring and a one-buffer layer are different canvases
on the same board.

| canvas | the last frame's buffer | the shift | cost (#113's comment) |
|---|---|---|---|
| the Waveshare P4's root (the DSI compositor, `moy_dsi_last`) | the ring's front or pending buffer, fenced as `copy_last` fences | the 2D-DMA M2M block copy (`esp_async_fbcpy`), submitted async, fenced at the content's close; cross-buffer in either direction | 0.04 ms to submit on a clean buffer; 4.6-6.5 ms to land beside the pass's Python; the live submit's writeback and the fence's invalidate measured in P1a |
| the Guition P4's root (the rotated compositor, `moy_rot_last`) | the other of its two landscape paint buffers (`retained_frames` 2) | the same 2D-DMA; **answers False while a rotate is in flight** (the rotate shares the DMA2D; a shift queued behind it would wait 20-30 ms); the shifted R is handed to `note_damage` or the compositor never rotates it | the same engine |
| the T-Deck's and Guition S3's root (the banded compositor, `moy_banded_last`) | the other of two buffers, the one the feeder is shipping | a CPU row copy, synchronous, reading alongside the feeder (read against read) | 4.3 ms for 320×212 with the feeder running; expect ~5 on core 1 beside it |
| one persistent buffer: the host's and web runner's root, every window buffer (`win.buf`), every layer, a cart's canvas (`RETAINED_FRAMES` 1) | this buffer | `moy_gfx.scroll_rect` in place | the CPU's; the wasm's |

The S3 root lever flips from `None` (the 2026-10-23 verdict) because what
the shift buys there is not the region's fill, which that verdict priced,
but the rows' Python and raster outside the band, which §3.3 skips for an
app that reads `ui.band()` -- and §14 prices honestly what an app that does
not read it keeps. The CPU in-place shift stays out everywhere a second
buffer exists: it costs twice the cross copy on the S3s and loses on the
P4s.

**The P4's fence** keeps the four rules of the 2026-10-23 design: before
the submit, fence every op pending on the source buffer (a deferred stamp,
a rotate in flight) and write back the destination rows' dirty cache lines;
between submit and fence, no CPU write lands in a cache line of the
destination rows; at the fence, wait and invalidate the destination rows'
cache; the frame's own async ops keep their order, the shift landing before
the content's close so `end_frame`'s last-write op is still last. **The
rows the frame draws inside R are drawn after the fence** (§3.3 step 4), so
the Python pass overlaps the DMA and the CPU never waits mid-pass; the
band's raster beside a landing DMA runs ~6× slower (PSRAM contention,
#113), which §14 prices.

**A verb for the window buffer.** `copy_last_into(layer, x, y, w, h)`
copies the rect of the last presented frame's buffer into a layer (a window
buffer), with the P4's fence; `copy_last` writes only into the drawn-into
back buffer and nothing today brings a window buffer up from the root. §8
needs it.

### 3.3 A shift frame, in the verbs engine

A frame of a nest R whose change is its offset plus a known set of rows is
a **shift frame**. Everything that licenses it is known at `api_begin`,
before the pass, from the input the engine consumed and the producers'
gens; nothing is inferred from the pass:

1. **Licensed at begin** when: nothing marked the console dirty but R's own
   motion (its drag, fling or glide, through `api_moving`) and the input
   the engine applies itself (a key step's focus move and reveal, a drag
   sample, a typed key into a field); no press is live but R's claimed
   drag; nothing animates over the surface; no tooltip, toast, popover,
   menu or scrim shows; R's view rect, content extent, look, font scale and
   window content rect equal the entry's; **every stop the app draws inside
   R carries its gen** (`o.gen` on `ui.scroll`, `ui.page`, `ui.table` and
   on a card's stop for its cover, picture or thumbnail; a `ui.part` inside
   R with no gen, a nest with a placed `ui.at` stop, and a Lua or wasm app
   report `None` and never shift); and the entry is frame n−1's complete
   paint of R with nothing drawn over R after its close but the cursor
   stamp (the WM's word, `_shown_ok`, not a canvas hook).
2. **The widened set is deterministic**, built at begin from those facts:
   the old and new focus rows and the row after each (a row's rule depends
   on whether the row before it is focused, `after_mark`); the foot of each
   group either touches (drawn with the last row's marks); the fade rects
   (n−1's moved by Δ, which now hold blended pixels shifted, and n's --
   `moy_ui_fade` blends in place and captures nothing); the cursor stamp's
   rect moved by Δ; the scrollbar's rect; a typed field's row; and every
   stop whose gen moved since the entry (a cover landing is that card's
   rect, not a whole paint).
3. **The ground leaves R out**; the host moves R by Δ (`shift_last`) once
   begin licenses the frame, and the engine sets the **floor**: the band of
   |Δ| rows plus the widened set, a rect list in C, at R's open -- or from
   the pass's start where a stop drawn before R reaches into it, so that
   stop's part of R draws through the floor in a whole frame's order (what
   a stop outside R drew into R is floor where R moved or its marks
   changed: surface model §14.7). The app's pass runs: every verb runs its layout,
   focus and hit-testing and the engine RECORDS each stop's draw; a stop
   wholly outside the floor answers 0-size; **the floor only widens**: at
   the first stop whose id or order differs from frame n−1's, the floor
   grows to the rest of R below it (correct for stacked layouts, and only
   adds); a stop that vanished from the tail has the ground filled under it
   at close. There is no replay: the engine cannot re-run the host's pass
   (the layers call `host.begin` and then their verbs; `api_begin` is not
   idempotent), and a frame that found a structural change pays rows, never
   a second pass.
4. **At R's close** the engine fences the shift, then rasterizes the floor
   from its records -- the band's ground and the stops that touch the
   floor, clipped to it, each rect from the ground up -- and the fade and
   scrollbar last. Drawing after the fence is what lets the pass overlap the
   DMA (§3.2).
5. **The app's own writes inside R** (`cv.cover`, `cv.picture`, `blit565`
   for a card's cover) go through the canvas clip, which is one rect: inside
   R it is the floor's bounding rect. Such a write is legal under the
   licence because its gen holds, so it rewrites pixels the shift already
   placed with the same bytes; the parity test proves it byte for byte. An
   app that reads `ui.band()` skips what it need not draw.

**The record comparison is a test, not a mechanism.** Under test, every
shift frame's stop records (kind, id, text, value, state, box relative to
the offset, cells) are compared with frame n−1's, and a difference outside
the widened set fails the frame (L8's correctness direction). In the shipped
engine the licence and the widened set are the whole rule, so a producer
that changes a pixel without a signal is a bug the suite sees, not a hash
the frame pays.

**`ui.band()` and `ui.keep(id, h)`.** `ui.band()` answers the floor in page
coordinates (the whole page on a full paint); an app that loops over cards
skips the cards it does not touch -- Get Carts, My Art, Storybook, Files'
side. A skipped card still has a record: the app passes `ui.keep(id, h)`
for it, which carries the stop's record forward with its height, so flow
layout, focus and the structural check see it; a key step onto a kept stop
finds it. Both are Moybyte-side verbs of the public UI API
(`docs/ui_api_2026-10.md`), not moy-spec's. An app that reads neither pays
its loop's Python and is still correct.

**Ops that ignore the clip** -- `cls` and the DMA fill, the PPA fill and
cover, `blit_strip` and the async stamp, `copy_last`, `set_viewport`, the
cursor -- are each a break when they land inside R on a shift frame: the
frame paints whole. The cursor is the one allowed overdraw, as a stamp.

### 3.4 The T-Deck, and full screen

The handheld paints full screen through `FullscreenStackWM`, whose stack
repaints whole under the gate; it has no debt and no shown ring, and keeps
them so (§5.1 of the surface model: do nothing here, beyond the verb). On a
shift frame **everything outside R draws**, as every frame draws it today
(the bars, the hint strip, the chrome): only R is vouched, by the content's
`PaintRing`, and only one frame deep -- the last painted frame is frame
n−1 (a gap is harmless on this tier, an idle loop changes neither buffer,
but a frame that painted anything else between them breaks the chain), its
key equals this frame's, and the buffer the feeder is shipping is the
source. A shift frame then is the cross copy (~4.3-5 ms), the floor's
rows, and the rest of the page's chrome. On a P4 full-screen window the
WM's ring vouches, as windowed.

### 3.5 Text contents and the home

The same record, two more speakers. A `TextView` (§5) reports its change as
a translation of the text rect by k visual rows plus the rows it edited and
the caret's; a home arrangement (§9) as a translation of its band plus the
cells whose selection changed. Each draws its lag from the record: rows are
drawn, translations shift.

## 4. The verbs engine: which frames become shift frames

| frame | today | designed |
|---|---|---|
| drag, fling | whole window; a fling stops at release | shift frame; the fling coasts in shift frames |
| the press frame (a finger lands) | whole window stamped, the backdrop restored | a content frame under direct render (§8): the window's rows only |
| key step, repeat step, ball step | two or three whole paints, then N−1 whole-glass copies | one frame: focus moves (its rows) and the reveal becomes a glide; each glide frame a shift frame; then N−1 copies of R's rows (§7) |
| typing into a field | whole window | the field's row (§6) |
| a tap, a value change | whole window | the stop's rows |

The rows are the floor of §3.3 with no translation: a frame whose change is
rows only licenses the same way, the floor the rows' union, every other
stop clipped to nothing. Where a change is neither (a page switch, a look
change, a dialog opening), the page paints whole, as today, and that full
paint re-arms the ring.

## 5. Text editors: the edited line and the caret

**One body: `TextView`**, a verb of the C UI library (`native/moy_ui`) and
part of the app-facing `ui` API (`docs/ui_api_2026-10.md`), not an Editor
helper: an app that wants a text area -- a chat log, a terminal -- calls the
same verb in Python, Lua or a compiled app and gets the same rows, its look
from the theme like every other part and changed per call through the
API's overrides (§3 there). The shell's editors are its first callers. It
is the view of a text buffer
in **visual rows** -- a wrapped paragraph's rows, a code line's one row --
with the first visible row, the rows' pitch, the caret, the selection, and a
`PaintRing`. The Code tab (`CodeLayer`, which the theme Config text extends
as `ConfigLayer`), the editor handle (`EditorHandle`, which the Player makes
for Notes), the verbs' fields and the text console draw their rows through
it. The buffer stays where it is (`CodeEditor`, the handle's document, the
console's ring), and what changed is the view's to find: it keeps each
visible row's gen -- a hash of what the row draws: its text, its line's
number where a gutter shows it, and its marks (the current row, the
caret's column, the selection's span, the find matches, an error) -- and a
frame draws the rows whose gen moved (`native/moy_ui/moy_ui_text.h`, built
in P4 on 2026-10-28). By cause, those rows are:

- **an edit** marks the rows of the edited paragraph or line as laid out
  before and after; when its row count changed (a split, a join, a wrap
  that gained or lost a row), everything below is a translation by the
  difference plus those rows; a paste marks its range the same way;
- **a caret move** marks the caret's old and new rows (the caret is solid,
  §14.1's invariant, so the two rows are the whole change);
- **a scroll** by k rows is a translation of the text rect by k pitches plus
  the k exposed rows. `ui.text_view` is a scroll region of the verbs
  engine: a drag scrolls it by pixels, its release coasts, and a view that
  `follow`s its foot glides there when lines come, every frame of which is
  a translation. The Code tab still pans by whole lines
  (`CodeLayer._code_drag`), each a translation.

**What draws the whole view**, named so none is inferred: a horizontal
scroll (the Code tab's `ed.left` moves every row); a gutter width change
(the line count crossed a digit); a font, scale, wrap or look change; a
find whose match lies outside the view (a scroll of more than a view is a
page change, and its p90 is priced as one in §15); and a buffer replaced.
**Overlays over the text rect** -- the Code tab's completion popup, its
crash popup, the jump and files overlays, the tool row, the autoscroll
mark -- are rows: the rows an overlay covered last frame (its card and the
shadow round it), moved by this frame's translation, join the floor, and
the overlay draws over the frame's rows. On the desk a popover, a tooltip,
a loupe or a divider's flash over the panes draws the next frame whole.

`TextView.plan(cv, ...)` asks the voucher whether the buffer `shift_last`
reads holds R as the view drew it last -- the view's own `PaintRing` on the
full-screen tier, and in a desk pane the Editor's word from the WM
(`EditorDesk.vouched`: the window's buffer holds its last draw) -- and the
record which rows moved; R's pixels then always come from that buffer
(`shift_last` by the scroll, by 0 on a rows frame: translations never
compose; where the WM's word is that the buffer the frame paints holds R as
the frames `RETAINED_FRAMES` back left it -- a direct render's, 3 -- a rows
frame moves only the bands the rows frames since drew), and `draw` draws
the floor's rows, each whole and alone: its ground, the current mark, the
gutter number, an error's underline, the selection, the find matches, the
text in its colour runs (tokenized in C for those rows only), the fade, the
caret. An error's underline reaches the row after where the glyph box fills
the row, so that row draws with it. Rows outside the floor are not laid out
-- the T-Deck's `qstr_find_strn` share (strings interned per frame for
every visible line) goes with them. On the desk the Editor draws the Code
pane alone on such a frame (its header and the other panes as they were)
and names the pane as what it changed, unless a header's words run past it
into the pane (a font scale over the chrome's), where every frame draws
whole.

**Each editor, what it keeps and what it drops.**

- *The Code tab* draws its rows, and the desk's read-only code
  (`draw_lines`), through the view, and loses its whole-pane loop; the status band and the bar strip are outside the text
  rect and draw as today (the strip through the WM's bar word,
  `strip_moved`). Its drag pans by whole lines (`CodeLayer._code_drag`),
  each a shift frame; panning by pixels through a `ScrollRegion`, as
  `ui.text_view` scrolls, is not built for it.
- *The theme Config text* is the Code tab's class and takes all of it;
  `ThemeDoc.follow` runs before the draw as today.
- *The fields* (`ui.field`: Settings' name, Cart info's, Blocks' prompts,
  Notes' new-note name) are one row each inside the verbs engine: a typed
  key changes the field stop's record, so §3.3's floor is the field's box.
  Under a dialog the page is held (§14.5): the held page is put back each
  frame today, which would wipe the dialog's rows, so a rows frame under a
  dialog skips the put-back and draws the dialog's rows alone.
- *Notes* is a system cart on the verbs engine: its `_draw` runs its whole
  verbs page every tick (`ui.bar`, `ui.page`, the hints, the sheet), and
  the page's ground fills the cart's canvas before the handle draws, so
  rows in the handle alone save nothing. Notes takes the floor as a host's
  rows frame. Its page and its vault table carry a gen (`PAGE_O`, moved
  when the tools, the foot's word or the vault change). At begin the Player
  asks the handle the rows its next draw changes (`EditorHandle.rows_plan`:
  each visual row's laid line, caret cell and selection against the last
  draw's, the bands of rows that differ; an embed, a moved view or a new
  look is the whole document), adds what the buffer it draws into lags
  (its `PaintRing`: on a glass of two or three buffers the floors the
  frames since changed, else the last frame's page moved over with
  `shift_last`), and the engine plans the frame
  (`moy_ui_api_shift_rows`): R is the page under its gen, which may hold
  stops `ui.at` placed since nothing moves, and the floor is those rects
  and what begin's input changes, under a hover over no stop. The cart's
  pass runs under the floor, the page's ground filling it alone; the handle
  takes each band into the floor over the ground (`moy_ui_api_floor_take`),
  papers it and draws its rows, with the row above where a link's underline
  reaches into it. Where no floor is drawn and the canvas is one buffer
  only the run draws, the handle draws its rows when the canvas's gate
  counters (`MOY_ST_N_FILL`, `MOY_ST_N_TEXT`) and its clears show nothing
  else wrote it since its last draw -- otherwise whole. A `cls` under a
  floor breaks the frame (`moy_ui_api_shift_break`: the pass draws whole
  from there) and a declared `background` draws every frame whole. The
  cart verb `ed.draw(x, y, w, h)` keeps its meaning for every other cart
  (render the document into that rect, this frame): a cart that `cls()`es
  and calls it gets the whole document. What a frame changed of the canvas
  is the floor's bounding rect (`Player.change`); the Player's window on
  the desk copies it and the shown ring's lag onto the glass
  (`WindowedWM._player_rows`, `blit_strip_rect`), not the canvas. It is
  the last phase (§15 P8), priced honestly, and §15 says what it costs if
  the cart floor does not hold.
- *The text console* (`runtime/text_console.py`) draws through the view for
  its scrollback rows and its prompt line; a script that prints appends
  rows, a translation of the scrollback by the rows appended plus those
  rows. Its frame cadence is the script's (a cart's loop), and both boards
  measure it under target; it takes the view for the one body, not for a
  row it needs.

**The cost** of a keystroke becomes the row's raster (one row of glyphs,
~0.3-0.5 ms on the P4 at the mono font, a few hundred µs on the T-Deck) and
the frame's fixed part; of a caret step the same twice; of a scroll by a
row the shift plus a row.

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

**Why two paints and two copies today.** §1 names the three causes of the
second paint and the one of the copies. Each is taken apart:

- **The engine tells a move it drew from an action it owes.** `settled`
  splits in two: `moved` -- the focus and the offset changed, which
  `api_begin` applied before the pass and the pass drew, so nothing is owed
  -- and `owed` -- an activation (`act`), a typed key the app applies to its
  own text, a tap, a dismissed popover, a layout the engine found wrong at
  the close (`c->owe`), or **a reveal set during the pass** (a list row's
  step, a table's, `ui.focus`), which applies at the next begin and needs
  its frame. `api_owes` answers `owed`; `AppHost.owed` and `still()` read it,
  the layers' `host.owed` checks are unchanged. The pass-time reveals are
  moved into begin where the last frame's boxes allow it (a list's and a
  table's row step: the row's box is the last frame's at the same index),
  so the common key step owes nothing; `ui.focus` from app code keeps its
  owed frame.
- **The reveal keeps its place at `api_begin`** and sets the nest's glide
  target (`glide_to`, §2.1) instead of the offset, so the first frame after
  a press draws the focus in its rows and the glide's first step as a shift
  frame.
- **The apps stop marking the page after the pass.** Files and Get Carts
  read `ui.focus()` BEFORE drawing the pane (the reveal is applied at
  begin, so the focus is this frame's) and report the pane as rows;
  Storybook and Appearance report rows instead of `again()`/`all()`. A grep
  ratchet fails a `damage.all()` after the pass in a verbs app. Without this
  the engine's split removes nothing on those four: a dirty console fails
  the licence and the page paints whole.
- **The debt is R's rows, paid by copy, off the paint path.** A change
  leaves the ring's other buffers lagging by the change's record (§3.1):
  after a glide or coast that is all of R (the window's content rows, ~75%
  of a 13 ms whole-glass copy for a default window) plus the rows outside;
  after a pure focus move, a few rows. The windowed WM's debt frame copies
  those rects out of the last buffer (`copy_last`, whole buffer rows to the
  GDMA) instead of the whole glass, **asynchronously on the 2D-DMA where the
  board has it and preemptible by input**: a press cancels the debt, and the
  next change's paint pays it. During a glide no buffer is calm, so no debt
  is paid until the glide rests. The tool reports the debt in per-press
  latency (§14). The intermittent `_full_debt` stuck at 2 after a
  Blocks-outline fling (the P4 comment, twice in six tries) is in this
  code, and the phase that rewrites it (§15 P1d) reproduces it first.

## 8. Key and typing frames on the P4 without the stamp

A key frame draws into `win.buf` and stamps it (19.5 ms windowed) because
direct render (`wm_windowed._direct_render`, #155) is taken for content
gestures only, and because its gates refuse everything else: `_chrome_quiet`
and `_shown_ok` are false on a press frame (the desk and chrome repaint
after a calm desk: 6.5 ms of window pass against 3.7 on a steady drag, 2.1
of home against 0.4), `_frame_is_quiet` is false whenever `ws._animating`,
and `_content_flinging` knows only the launcher and PROJECTS. Three changes:

- **A speaking content direct-renders on every frame it draws**, key,
  glide, coast and press frames included, when the window is focused and on
  top with nothing over it. The window buffer lags, as it does after a
  pointer gesture today.
- **A frame whose only change is inside the focused window repaints no
  desk and no chrome.** The WM reads `api_moving` and the content's record
  in place of `ws._animating` for the freeze and for direct render: a
  content moving on its own, or a press that reached the content and
  nothing else, is a content frame. This is what reaches the press frame
  (the stamp and the 16-17 ms backdrop restore both go) and every coast
  frame of a verbs nest.
- **The window buffer is refilled from the root, not redrawn**, through
  `copy_last_into` (§3.2): asynchronously on the 2D-DMA at idle through
  the debt path, preemptible by input, and before any reader of `win.buf`
  (a focus loss, occlusion, a move, a resize, the freeze's own leg 5): the
  shown ring is the authority on what the buffer lacks and every reader
  refills first. `debt_owed` is rewritten for this case -- today it answers
  False while `win.buf` is stale, which would leave the debt unpaid for
  good. The refill is the stamp run backwards (6.5-20 ms a change at PSRAM
  rates) moved off the paint path, not removed; the tool reports it with
  the debt.

The stamp disappears from the key and typing rows; a full-screen window's
12.2-12.7 ms stamp goes the same way. The host desk takes direct render of
key frames too, so the host is the test bed; its cursor is hidden in those
tests (a visible cursor makes `_shown_ok` false, as it should). Built for
the contents that keep their own frames (`HELD`: the desk Editor, Paint) in
P4b on 2026-10-28: a frame that changes the page renders into the glass
where the shown ring vouches for the buffer it paints and the content
repaints what that buffer lags (`takes_lag`), and the Code pane's text then
moves out of the last buffer only the rows the frames since drew; a press
inside the focused Code pane reaches that pane alone, so a drag or a fling
there draws the pane's rows like a key.

## 9. The home's arrangements, PROJECTS, the sprite pickers, My Art

**The grid and icons arrangements** (`Launcher`, `IconHome`) repaint the
whole home every painted frame (97-123 ms on the P4, 80-102 on the T-Deck),
while the carousel repaints a band and shifts the rest (24-27 on the
T-Deck's same inputs, 33-57 on the P4's). The same path reaches them, with
two changes and one honest limit:

- **a selection step is two cells**: the drag fast path
  (`_try_drag_partial`) runs on a key, repeat or ball step too, its floor
  the cells whose selection changed, and on a glide the band's translation
  plus the cells exposed;
- **the band shifts only on a plain ground**: `Launcher.draw_shift` repaints
  the band whole today because the cards lie on the wallpaper, and the
  wallpaper's restorer puts a baked backdrop (`bd`) or a picture back at
  glass coordinates -- they stay put while the cards move. Only the plain
  fill (`bg`) moves with the cards, and there the band is a translation.
  On a `bd` or picture theme the band repaint stays, its cards out of their
  KEPT FACES (`launcher_layer._Faces`, 2026-10-28): a card that wears no
  focus is drawn once into a slot of one owned layer -- its picture and
  rim, keyed on the item, its title, the cover object it was drawn from and
  the look's gen -- and copied; over anything but the plain ground a
  corner's partly covered pixels blend over what lies under them, so the
  copy is `moy_gfx.face` (`mg_face`, `moy_shape.h`), which draws those
  pixels as the cover or the title tile and the edge ring draw them and
  copies the rest, one call a card. The desk shelf's cards take the same
  faces; the handheld carousel's change size with the offset and draw. A
  paint draws no face: idle frames draw them, the band's cards' and then
  the row past either end of the band (`Launcher.warm_faces`), and a paint
  copies the cards that have one and draws the rest, so a home's first
  paint costs what it did and a step copies the row it brings in. The
  backdrop goes back under the whole band in one copy and the pictures
  paint over it: its cut round the cards cost more in calls than the bytes
  it saved (a block that big is the P4s' 2D-DMA's, `FBCPY_MIN_PX`).

The desk home's still ring (§14.4) keeps the parts' keys; it is a
`PaintRing` (§3.1), the parts' keys its slots' change. The PROJECTS window is a `Carousel` in a window
(`HomeWindowLayer`) and speaks the record through the WM like any windowed
content. The carousel's settle frame (the selection changes when the row is
still: the hero and details pane repaint) is the carousel's key-row p90 on
both boards; it is a rows change (the pane's rect) under §14.4's restore,
and §15 prices it as one. The tab switch by key is a page change and paints
whole, under its own 100 ms target.

**The sprite pickers** (`paint_layer.py`'s grid, the theme editor's) read
the sheet pixel by pixel into quads (`sheet.pget`, `moy_sheet_pget` 15% of
the T-Deck's busy samples). The picker is a scaled view of an indexed
sheet; `moy_gfx.blit_indices` places an index bitmap 1:1 with no scale and
no source rect, so the picker needs a canvas-tier kernel of its own:
`blit_indices_scale(src, sx, sy, sw, sh, dx, dy, scale, pal)` in `moy_gfx`'s
compositor, nearest-neighbour, bounds-clamped, one call per visible sheet
rect, the grid lines and the selection over it; the picker scrolls as a
`ScrollRegion` on two axes with the band contract. The grid's fills and the
picker's own Python stay, so §15 prices it at a fraction of today, not at a
blit's cost.

**My Art's covers** are built inside its draw (a 0.6-2.5 s frame per
gesture on the P4). They move off the paint path: built one per idle frame
through the console's idle step, as Get Carts' cover runs are, each landing
reported as that card's gen (its rect joins the floor, §3.3 step 2 -- never
a whole paint per cover). This is a fix, not a mechanism, and it is the
first thing built (§15 P0).

## 10. Why this is not a second invalidation mechanism

Whether a surface draws is still decided by the gate, the gens, §14.1's
freeze and the one new Class B source, `api_moving`, which a nest declares
while it moves. The record only says, for a frame that IS drawing, what the
buffer lacks and how much of the content to rasterize; `shift_last` only
replaces the drawing of a lag the record names, as `copy_last` does for a
whole frame. That is L5's line: the shared layer decides whether, the
backend how. Against §8's graveyard: no widget reports damage (one region,
one translation, one record per frame, and the widened set is built from
the input the engine consumed and the producers' gens, never from the
pass); nothing is inferred (a stop whose content changes without a gen is a
bug the L8 assertion fails under test, not a hash the frame pays -- the
line L2 gains: a draw-stream comparison may assert, never license); no
root framebuffer is retained across web frames (the wasm canvas is one
persistent buffer and shifts in place, which is retention of one buffer, as
every raster tier declares); the PPA copies nothing 1:1 (the 2D-DMA is a
different engine, and what it buys is the CPU's time); and the cost that
goes is the region's raster and the rows' Python, measured on a live whole
frame.

**The surface model is amended in the same commit** as the first ring
entry (§15 P1c): a §14.7 that states the change record, the two Δ rules and
`shift_last`/`copy_last_into` (§3.1, §3.2 here, in the model's voice),
§7's scroll-as-blit row naming the two ring bodies (the windows' `_shown`,
the contents' `PaintRing`) and the one-voucher rule, `api_moving` in §3's
Class B list, and L2's added line.

## 11. What reports `None`

Per canvas, then per source:

| lever | present | `None` |
|---|---|---|
| `shift_last` across buffers | the P4 roots (2D-DMA), the S3 roots (CPU row copy) | a one-buffer canvas (shifts in place instead); the Guition's root while a rotate is in flight (answers False, the frame draws) |
| `shift_last` in place | one persistent buffer: the host's and web's root, every `win.buf`, every layer, a cart's canvas | the P4 and S3 roots (a second buffer exists; the CPU in-place shift loses) |
| `copy_last_into` a layer | roots with a `last` buffer (the P4s, the S3s), and the host's compositor, whose `last` is a test's ring as for `copy_last` | one persistent buffer with no ring: the web's canvas, every layer |
| the PPA ground fill before R opens | the P4s | the S3s, the host, the web |
| a shift frame of a nest | a nest whose app passes every gen it draws inside R | a nest with a `ui.part` and no gen, a placed `ui.at` stop, a Lua or wasm app |
| a host's rows frame (an editor handle's) | a Python app on the verbs with one handle, its page under a gen | a Lua or wasm app, two handles, a declared `background`, an embed in view |
| held-key repeat | every level source: the T-Deck matrix, BLE, the host's and browser's keyboards, a gamepad | the T-Deck keyboard's ASCII fallback |
| the trackball's steps and `BALL_COAST` | the T-Deck (and the host's arrow pan) | every board without a ball |
| direct render of key frames | the windowed WM over a root with `set_viewport` (the P4s, the host desk) | the web runner's canvas (keeps the stamp); the fullscreen WM (no window) |
| the debt copy of rows | roots with `copy_last` and a ring deeper than one | one persistent buffer (owes nothing) |

Where a lever is `None` none of its bookkeeping runs: no ring entry is
written for a shift that cannot happen, no widened set is built, no refill
is queued. The host runs every mechanism under test as an emulated tier
(§13), which is how a `None` that should have been a value is caught (L8).

## 12. Deliberately left out

- A cart's own scrolling, and every Lua and compiled cart: a cart may read
  `btn` in its draw and its `cls` writes past any floor; the Player
  composites a declared rect at most (§5, Notes, whose pass is shell code).
- An overshoot or a rubber band at the clamp: the fling hard-stops, as #113
  v1 did (owner, 2026-10-27).
- A replay of the app's pass (§3.3): no host has a re-run hook and the
  engine's begin is not idempotent; a structural change pays rows.
- A hash of R's raster bytes: replaced by the deterministic widened set and
  the L8 record assertion.
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
  one step from the target, a count of n moves n in one frame; the C against
  the twin on the desktop MicroPython in the boards' float model, dt through
  the input word.
- **The input layer** (`tests/test_tdeck_input.py` and the table's own
  suite): a held button repeats after the delay at the rate on the passed
  clock, a frame of two periods owes two, `btnp` stays an edge, a disarm
  zeroes repeats, the ASCII fallback repeats nothing (`None`), a typed key
  repeats as a count from the typing-level slot and `key()`/`keyp()`/
  `last_key` never carry it, the queue's zero frame does not cap it; every
  modal, menu and dialog activates on an edge only (a fuzz over the
  surfaces with repeats on: no activation count moves); the web page
  forwards no `e.repeat`.
- **The trackball**: 30 of 30 steps delivered at 30 steps/s on every
  stepping surface AND the view within one glide of the target at every
  presented frame (the merged bench's `ball_fast` row reports both, §14);
  `BALL_COAST` 0 adds no velocity, a value adds the surplus's.
- **The shift frame** (the verbs engine, console-level through the WM and
  full screen): for every verbs app and every text surface, a scripted drag,
  fling, key step, held key and typed line, rendered shift-and-rows and
  forced-full, every presented frame byte-identical, on 1, 2 and 3 buffers,
  with the cursor showing, with a tooltip and a toast arriving mid-gesture
  (each forces a full paint and the pixels still match), both directions,
  |Δ| from 1 to the view less one, fades at either end, the clamp at either
  end, a cover landing mid-fling (its rect joins the floor, no whole paint),
  an app that skips through `ui.band()`/`ui.keep()` while flinging; the
  fixtures carry decoded covers.
- **The L8 record assertion**: on every shift frame of those sessions the
  stop records equal frame n−1's outside the widened set; a stop whose
  neighbour's focus changed, a group foot, a fade rect, each fails the frame
  if left out of the set.
- **Two emulated DMAs** (the copy landing at submit, and at the fence) must
  produce the same frames.
- **Each break forces a full paint**, with a counting canvas: a look change,
  a window moved during a fling, a painted-frame gap, an overdraw other than
  the cursor, an app that draws in R with no gen, a placed `ui.at` stop, a
  stop that vanished at the tail (the ground filled, no second pass), an op
  that ignores the clip landing in R.
- **The floor**: `clip` inside R intersects the floor's bounding rect and
  `clip()` returns to it; the engine's own raster lands in no pixel outside
  the floor's rects; `ui.band()` equals the floor in page coordinates; a
  shift frame's rows are drawn after the fence (an ordering probe).
- **One paint per press**: a key step on every verbs page paints exactly one
  frame (the pass-time reveals moved into begin; `ui.focus` from app code
  still owes one), then N−1 copies whose bytes are bounded by R's rows;
  the `damage.all()`-after-pass ratchet; the stuck debt has a regression
  test once reproduced.
- **Direct render of key frames**: on the host desk with the cursor hidden,
  a key, glide, coast and press frame of a speaking content each engage
  direct render (an L8 engagement count per frame class) and repaint no
  desk or chrome (a counting backdrop); the window buffer's lag is the
  record's composition, and a focus loss, occlusion, move, resize and the
  freeze's leg 5 each find it refilled byte-equal to a live draw.
- **The text view**: for the Code tab, theme Config, Notes, each field and
  the console: typing, caret moves, split and join, a wrap that gains and
  loses a row, paste, find in and out of the view, a page key, a drag, a
  horizontal scroll, a gutter crossing a digit, each overlay opening and
  closing -- every frame byte-equal to a whole draw; the highlighter runs
  for the rows only (a counting highlighter); a cart that `cls()`es and
  calls `ed.draw` gets the whole document.
- **The home and the pickers**: a selection step draws two cells, a shift
  on a plain fill is a translation, a `bd` and a picture theme repaint the
  exposed cells only through one `blit_batch`, the picker's kernel matches
  the per-pixel draw byte for byte on every scale, source rect and scroll.
- **Allocation**: `tests/test_frame_alloc.py`'s pin extends to a shell shift
  frame and a rows frame (preallocated ring slots and record rects); Files'
  per-frame row list is rebuilt only when its rows change.
- **L8's silent-disable direction**: over a scripted fling, a glide and a
  typed line, the fraction of shift frames and rows frames stays above a
  floor, per surface; a mechanism that quietly never engages fails.
- **One body**: the grep guards of §2.1 and §3.1.
- **The web**: `tools/web.py shot` of a page mid-fling and mid-glide under
  injected dt equals the host's screenshot (the existing web goldens gain
  the two scenes).
- **On glass, in each board's suite**: `shift_last` byte-exact against
  `moy_gfx.scroll_rect` in both directions; on a P4 the primed-cache test
  (destination lines pulled into cache before the copy, read after the
  fence), a CPU write into the destination rows during the flight fenced
  first, and the Guition's refusal during a rotate; on an S3 the copy exact
  while the feeder ships the source; `copy_last_into` exact on each.

## 14. One measuring tool, and the arithmetic

Two tools were written for the baselines: `+tools/scroll_bench.py` (commit
`83d2ccaa`, worktree `scrollbase_p4`: the P4, the frame's split into content,
stamp, home, copy and rest, a wall-clock input source) and an earlier
`tools/input_bench.py` (commit `2cb0bc05`, worktree `scrollbase_td`: the
T-Deck, the trackball through `ws.ball`, keys and held buttons through an
input-table source, the loop's split under PERF DIAG). Both typed into real
seed carts (Star Catcher, Coin Quest), left their fixtures behind, and one
broke Star Catcher on the T-Deck. Neither landed; the one tool merged from
them did (P0, 2026-10-27):

- **`tools/input_bench.py`**, for every board: `scroll_bench`'s frame split
  and groups folded in (the P4's rows need the WM's split, the T-Deck's the
  loop's), one input vocabulary (`fling`, `drag`, `key_steps`, `key_held`,
  `key_20`, `type`, `type_fast`, `type_line`, `ball_slow`, `ball_fast`,
  `find`, `paste`), every input played inside the frame loop on wall-clock
  time through the bench's input source, so a finger moves at its speed and
  a key goes down and up on its schedule whatever the frame rate.
- **A scratch project.** The bench copies a seed cart into a project of its
  own name (`Bench Scratch`) at setup and types, pastes and finds in that; it
  makes its long note, its console script and My Art's drawings under the
  same name; a seed cart is never opened for writing.
- **Cleanup on exit**, in a `finally` that runs on a failure and on Ctrl-C:
  the scratch project, note and script deleted, the home's tab, the theme
  and the window sizes restored, PERF DIAG and `uncap` put back, the board
  left on the home.
- **The gates are presented frames, split by what moved.** A row's p90 is
  over the **presented interval** while the input or the view moves --
  the buffer switch on a P4, the feeder's completion on an S3 -- not over
  painted frames alone, because painted-frame p90 excludes the debt copies,
  the T-Deck's 3-5 ms of upcalls and the flush. Each row reports **live,
  coast and glide** p90 separately (a coast cannot dilute a press frame),
  **the max of the first three frames of every gesture** (the press frame
  is its own gate), the debt copies and the window-buffer refill in
  per-press latency, and **the fraction of shift and rows frames** (L8).
  "Keeps up with a hand" is per frame: the scheduled finger position
  against the content's offset at presentation, and the release velocity
  against the finger's. The T-Deck's loop split under PERF DIAG (+4 ms) is
  a separate run from the numbers.
- **The null-floor run.** Before any shift exists, a diag mode clips every
  content raster to an empty floor and runs each surface's rows: what
  remains is the surface's **pass cost** (its Python loop, the engine's C
  layout, the WM's window pass), which is the shift frame's lower bound and
  the one number the arithmetic below needs measured, not guessed. It is
  P0's first deliverable.
- **The arithmetic**, per frame, for a reached row:

      expected = total − content − stamp
                 + 1.1               (the direct-render stamp, measured)
                 + pass               (the null-floor run; until it exists
                                      40% of content, 20% for an app that
                                      reads ui.band)
                 + shift              (P4: ~0.04 submit + a live submit's
                                      writeback; S3: ~4.3-5 copy; host/web:
                                      scroll_rect in place)
                 + fence wait         (P4: 0 when the rows draw after the
                                      fence and the pass outlasts the DMA;
                                      else the DMA's remainder, 0-5)
                 + rows               (the band and the widened set at the
                                      content raster's rate, ×6 under DMA
                                      contention on a P4: ~1.5 ms a band)

  `total`, `content` and `stamp` are the row's own frames (the frame
  classes above, never the mean split, which averages debt copies in).
  Whole-content removal -- "97% of rows fit 33 ms without the raster" -- is
  the bound for removing the content DRAW, Python included, and no phase
  reaches it: the pass stays. The tool prints this expectation beside each
  row of a baseline run, so a phase's gate is a column, not a conversation,
  and a presented-interval floor for the T-Deck: `ws.frame` + 3-5 ms, and
  the 12.8-13.5 ms flush below it, so a painted frame under ~13 ms presents
  no faster than the feeder (~70 fps).

## 15. Build order

Phases are sized for one agent each, worst rows first within what the
dependencies allow; the ones that touch the Editor's surfaces (P3, P4, P5,
P7b) run one after another; P6 and P7a are independent of the shift work
and run beside it from the start, because they are the T-Deck's actual
over-target rows. Each phase ends with its measurement step on the boards
it names through §14's tool, verdicts to #66; the Guitions are measured
when a P4 or an S3 path changed. Estimates are §14's arithmetic at the
provisional pass cost; P0's null-floor run re-prices every one before P1a.

| phase | builds | boards | parallel with | rows reached (baseline p90 → expected, P4 / T-Deck) |
|---|---|---|---|---|
| **P0 the tool** | `tools/input_bench.py` merged (§14) with the presented-interval gates, the frame-class split, the first-three-frames max, the finger-vs-content probe and the null-floor run; My Art's covers off the paint path as rows (§9) | P4, T-Deck | -- | My Art every row (0.6-2.5 s frames gone; drag 73 → ~35-40 / 25 → ~22); every other row re-priced |
| **P1a the verb** | `shift_last` per canvas (§3.2): the P4 2D-DMA synchronous first then async with the fence, the Guition's refusal during a rotate and its `note_damage`, the S3 row copy, in place on one-buffer canvases; `copy_last_into`; on-glass tests; the live submit, writeback and fence measured | P4, T-Deck | P6, P7a | none directly (a verb) |
| **P1b the motion** | the glide and the fling in `moy_ui_scroll_t` and on every nest, the dt word, `api_moving`, the carousel's glide deleted, the pass-time reveals into begin, `settled` → `moved`/`owed` | host; a feel check on both | P1a, P6, P7a | a fling coasts on every verbs page and a key step glides (at full paints until P1c: its frames cost today's, so P1b's gate is the motion's correctness and the single paint per press on the T-Deck's lists -- Settings and Licences 16 paints for 8 presses → 8) |
| **P1c the floor on the host** | the licence, the widened set, the floor list, monotone widening, drawing after the fence, `ui.band()`/`ui.keep()`, the apps' gens for covers, `PaintRing`, the L8 record assertion, surface model §14.7 | host; T-Deck | P2 | T-Deck verbs drag and fling: Settings 25 → ~19-21, Calc 23 → ~17, Get Carts 20 → ~13, Achievements 21-22 → ~16, Licences 18 → ~13, My Art 25-30 → ~15, Storybook 12 → ~10 (all under already; the T-Deck gains little here) |
| **P1d the WM records and direct render** | `_shown` change records, direct render on every frame of a speaking content, the content-only frame (no desk, no chrome), the window-buffer refill, `debt_owed` rewritten, the debt as rows async and preemptible, the four apps' rows instead of `damage.all()`, the stuck-debt reproduction | P4 | P2 | P4 verbs drags: Settings 24 → ~14-15, Files 53 → ~18-22, Get Carts 57 → ~17, Storybook 28 → ~14, Appearance 61 → ~23, Calc 21 → ~13-14 (full screen +4-7); press frames: Settings' first frame 55 → ~20-25, so fling p90 55 → ~20-25, Files 79 → ~25, Get Carts 58 → ~22, Storybook 73 → ~20, Appearance 87 → ~28, Calc 54-65 → ~18-22; key rows: Settings 33 → ~15-18, Calc 31 → ~15-18, Storybook 48 → ~15-18, Appearance 56-64 → ~20-25, Files 56-60 → ~18-22, Get Carts 51-59 → ~18-22 |
| **P2 repeat and the ball** | `begin_frame(now)`, the repeat counts, the typing-level slot, the page's `e.repeat` dropped, the ball as counts, `BALL_COAST`; then the owner's hand trial | T-Deck, host | P1c, P1d | T-Deck `ball_fast` rows 13-27 of 30 → 30 of 30 within one glide; held keys move on every surface (none today); P4 held-key rows (Code, Notes, Music ~11 Hz with no delay) repeat at the rate |
| **P3 fields and dialogs** (Editor sequence begins) | the field's row; the held page's put-back skipped on rows frames; Blocks' prompts, Cart info, Settings' name, Notes' name field | P4, T-Deck | P6 | P4 Blocks prompts 123 → ~14-20, Cart info fields 86 → ~14-20; T-Deck fields 19-25 → ~12-15 |
| **P4 the Code tab and theme Config** | `TextView` in visual rows, the whole-draw triggers, overlays as rows, the pixel drag | P4, T-Deck | P6 | P4 Code key steps 86 → ~14-18, typing 86-92 → ~14-18, held 84 → ~14-18, drag 57-82 → ~17-22, fling 87-105 → ~17-22, find 104 → ~50-60 (a find past the view is a whole view under direct render); theme Config keys and typing 91-94 → ~14-18, fling 121 → ~17-22; T-Deck Code type 40 → ~25-40 (its p90 is two outliers P0's tool must name before P4 prices them), drag 24 → ~14-17, find 68 → ~25-45, theme Config 12-15 → ~9-11 |
| **P5 the Editor's panes** | the Blocks outline and Config's cards ported onto `ui.scroll` nests; Music's steps and the Map's camera on the nest state | P4, T-Deck | P6 | P4 Blocks outline 66-96 → ~20-30, Config 66-68 → ~20-30, Music steps 86 → ~20-30, Map 68-81 → ~22-30; T-Deck Blocks, Config, Music 22-28 → ~15-20 |
| **P6 the home and PROJECTS** | steps on `_try_drag_partial`, two cells, the `bg` translation, `blit_batch` covers, the still ring as `PaintRing`, the settle frame as the pane's rows | P4, T-Deck | everything but P1d's WM edits (coordinate the `_shown` slot shape) | P4 grid and icons 97-123 → ~25-35 on `bg`, ~30-55 on `bd` and picture themes (over); carousel key rows 54-57 → ~35-45 (over: the settle frame's pane); T-Deck grid and icons 80-102 → ~25-30 on `bg`, ~30-50 on `bd`/picture (over), carousel settle 48-51 → ~35-45 (over) |
| **P7a the picker kernel** | `blit_indices_scale` in `moy_gfx`, host and on-glass byte tests | host; one board | P1a onward | none directly (a kernel) |
| **P7b the pickers** (Editor sequence) | the picker on the kernel, the two-axis region | T-Deck, P4 | -- | T-Deck sprite pickers 40 → ~18-22; P4 93-96 → ~22-30 |
| **P8 Notes** | the handle's rows at begin, the cart's pass under the floor, the counters' licence, the Player's declared rect through the shown ring | P4, T-Deck | -- | P4 Notes 59-93 → ~22-28 with the cart floor (~35-45 without it: content 33 stays and only the composite shrinks); T-Deck 21-23 → ~15-19 |

**Dependencies.** P0 first (the tool measures every later gate and the
null-floor run prices every row). P1a → P1c (the floor needs the verb);
P1b → P1c (the floor's licence reads `api_moving`); P1c → P1d; P1b → P2
(the counts land on the glide: a glide before the shift frame would cost
the T-Deck its frame rate, which is why P1b's gate is correctness, not
frames); P1d → P3 → P4 → P5 → P7b on the Editor; P7a any time after P0;
P6 from the start beside P1a, sharing only the `_shown` slot shape with
P1d; P8 last. The surface model amendment lands with P1c.

**Rows that stay over 33 ms after every phase, and the next lever for
each.** On the P4: the Code tab's and theme Config's find past the view
(~50-60; the next lever is the Code tab's per-row raster, its highlighter
and glyph cost, a kernel question, not a scroll one); the home's grid and
icons on `bd` and picture themes (~30-55) and the carousel's key rows
(~35-45, the settle frame's hero and details pane; the lever was the
cards' kept faces, built 2026-10-28, §9, and what the carousel's step
frame keeps over the target is its hero, its details pane and its band
drawn in one frame -- #66 has the rows);
Notes if the cart floor does not hold (~35-45; the lever is then the
Player's composite of a declared rect alone, which P8 measures first); and
Files' and Get Carts' key rows if their `damage.all()` is not moved
(~38-45). On the T-Deck: the home's grid and icons on `bd` and picture
themes (~30-50) and the carousel's settle (~35-45), the same lever (the
icons' key rows stay over it: each step brings in a row of cards whose
covers decode inside that frame, on the cover cache's budget); the
Code tab's typing (~25-40) and find (~25-45) until P0's tool names the
outliers' cause. Everything else lands under the target by the
arithmetic, and the tool's column says where it did not.

## 16. moy-spec's verb table

Nothing here touches it. `btn` and `btnp` keep their meaning for a cart (a
repeat is a console-side count, never a cart's edge); `key()`, `keyp()` and
`last_key` never carry a repeat; `ed.draw(x, y, w, h)` keeps its meaning
(the whole document into that rect, this frame) for every cart but the one
whose pass is shell code under the floor; the cart canvas verbs are
unchanged (`scroll_rect`, `blit_indices`, `blit_indices_scale`, `shift_last`
and `copy_last_into` are `moy_gfx`'s compositor and a canvas's, not
SPEC.md's); `ui.band()`, `ui.keep()` and the kinetic `ui.scroll` are the
public UI API, Moybyte's (`docs/ui_api_2026-10.md`), and an app on the
verbs passes nothing new to get them. The physics' parameters are not a
cart's to set.

## 17. Decisions taken (2026-10-27)

1. `BALL_COAST` ships at 0 and is tried by hand on the T-Deck after P2.
2. `REPEAT_DELAY_MS` 400 and `REPEAT_RATE_MS` 50, in `moy_input`, overridable
   per board in its board.toml.
3. The host desk takes direct render of key frames like the P4, so the host
   is the test bed; its cursor is hidden in that bed.
4. No rubber band at the clamp.
5. The editors move onto the C UI library: the Code tab, theme Config, the
   Blocks outline and prompts, Config's cards, Cart info, Map, Music and the
   pickers lay out, focus and draw through the engine, as the apps do.
   Their look does not change: the Editor goldens stay byte-identical, and
   a detail the engine cannot draw extends the engine.
6. `TextView` is a public `ui` verb, consistent with the rest of the API,
   so any app (a chat app, a terminal) gets a text area and restyles it
   through the theme and the per-call overrides.

## 18. Review ledger (2026-10-27)

Two adversarial reviews read the first revision (commit `5262cd8e`); every
finding was checked against the code before it changed the design. What
changed, by finding:

**Architecture (verdict: do not build as written).** B1, two buffer models
and a wrong Δ: §3.1 now has the two Δ rules (inside R always n−1 → n by
this frame's Δ; outside R the rows' union; in place only on one-buffer
canvases) and translations never compose. B2, the structural replay cannot
be built: gone (§3.3 step 3, §12); the floor widens monotonically to the
rest of R and fills the ground under vanished tail stops. B3, the per-stop
comparison was inferred damage and missed pixels (the band rule after a
focused row, the group foot, the fade that blends in place, `ui.part`):
the licence is begin-known input changes plus every gen; the widened set
is deterministic and lists those cases; the record comparison is an L8
test assertion (§3.3, §13). B4, Notes broke `ed.draw` and could not gain:
the verb keeps its meaning, Notes' own pass runs under the floor with the
handle's rows known at begin and the canvas counters as the handle's
licence, the Player's rect goes through the shown ring, and it is the last
phase with both prices stated (§5, §15 P8). M1, the pass-time reveals: the
common ones move into begin and the rest count as `owed` (§7). M2, the
order: §15 is rebuilt as P1a-d. M3, the stale window buffer:
`copy_last_into`, the ring as authority, `debt_owed` rewritten (§3.2, §8).
M4, repeat: `begin_frame(now)`, the T-Deck's typing-level slot, counts
never in `key()`/`keyp()`/`last_key`, the page drops `e.repeat`,
parameters in `moy_input` (§2.2). M5, the ball: counts in the REP word, no
queue, lag bounded by the glide (§2.3). M6, the Guition: `moy_rot_last`'s
two paint buffers, refusal during a rotate, `note_damage` (§3.2). M7, no
write entry exists: the floor is a rect list the engine's own raster
honours in C, the canvas clip is its bounding rect for the app's
gen-licensed writes, and the clip-ignoring ops are listed as breaks
(§3.3). M8, `None` per canvas (§11). M9, `TextView` in visual rows with
every whole-draw trigger and the overlays as rows (§5). S1-S8: the T-Deck
draws everything outside R; one voucher per region; rows after the fence;
the picker kernel; the held page's put-back skipped; the dt word; the
host's cursor hidden; the names (`AppHost.owed`, `_try_drag_partial`, no
fade is captured, no table clock).

Where the design differs from a review's fix: M7 asked for the floor as
the clip iterated per rect; the engine draws its own raster from records
at close, so a rect list in C costs no extra pass, and the app's writes
take the bounding rect under the gen licence (§3.3 step 5), which the
parity test proves. B4 offered dropping Notes; it stays, last, because its
pass is shell code and the floor reaches it, with the no-floor price
beside it.

**Performance (verdict: mechanisms mostly sound, numbers 1.5-3×
optimistic).** 1, the arithmetic priced the whole content column as
raster: §14's formula is per frame and keeps the pass, with the null-floor
run measuring it; every estimate went through it. 2, P1's fling rows could
not move at P1 and P3 improved them by dilution: the press frame is in
scope (§4, §8), the gates split live, coast and glide and report the first
three frames' max (§14), and the reach is stated at P1d. 3, the second
paint is app-driven on Files, Get Carts, Storybook and Appearance: the
apps read `ui.focus()` before the pane and report rows, with a ratchet
(§7). 4, direct render would not engage: `api_moving` replaces
`ws._animating` for the freeze, a content-only frame repaints no desk or
chrome, with an engagement count (§8, §13). 5, the Blocks outline and
Config's cards are not nests: ported in P5, their rows priced there and
nowhere earlier (§2.4, §15). 6, `ui.band()` skipping collided with the
structural check: `ui.keep(id, h)` (§3.3). 7, the T-Deck's Python stays
for apps not reading `ui.band()` and the copy is ~4.3-5 ms: re-priced
(§15 P1c) and said to gain little there. 8, the fence mid-pass and PSRAM
contention: rows drawn after the fence, the contention priced (§3.2,
§14). 9, the baked backdrop is not translation-invariant: only `bg`
shifts, `bd` and picture themes keep the band repaint and are stated as
over the target with the next lever (§9, §15). 10, Notes needs the floor
inside the cart's pass: specified (§5), both prices in §15. 11, the text
tails: find past the view priced as a whole view, the T-Deck's typing
outliers handed to P0's tool (§15). 12, the debt copies and the refill:
async, preemptible, reported in latency (§7, §8). 13, the Guition's rotate:
refusal (§3.2). 14, glides multiply frames and covers forced whole paints:
the glide's cost is stated at its parameter, a cover landing is the card's
rect (§2.1, §9). 15, the gates: presented interval, the frame-class split,
first-three max, finger-vs-content at presentation, the L8 fraction, the
null-floor run, the diag split as a separate run (§14). 16, allocations:
preallocated slots and the `test_frame_alloc` pin (§3.1, §13). 17, the
order: P6 and P7a from the start, fields in P3 right after P1d (§15).

Where the design differs: finding 14's glide count is taken as the
parameter's trade (the carousel's feel today) and not shortened by design;
the owner's hand trial of `BALL_COAST` is the place to retune
`GLIDE_HALF_MS` if the frames read as load beside a playtest.
