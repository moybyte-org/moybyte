# UI building blocks (2026-10-29)

What the public UI API (`docs/ui_api_2026-10.md`) needs so that every Moybyte
surface, and a new app, is a COMPOSITION of engine blocks rather than a new
element per screen, and how the boards' speed is won back once the
surfaces are on them. The plan that moves the surfaces, and its state, is
#243.

## The goal (owner, 2026-10-29)

- Every surface looks themed and scrolls the same way: one scroller, with
  fling, glide and fast frames, everywhere text or a list moves.
- An app is built from composable blocks. A screen that needs something new
  composes it, or the gap is fixed in a block. No verb is named after the
  screen that first needed it.
- The blocks are C (`native/moy_ui/`), and a Python, Lua and compiled app call
  the same ones (`docs/ui_api_2026-10.md` §1's three faces).
- **Move first, then one perf pass (owner, 2026-10-30).** Every surface
  moves onto the blocks on the host, held by its goldens, its allocation
  pins and its tests. Then one pass measures the boards with
  `tools/input_gate.py` against dev 4d0483d3, the last dev before the
  moves, and optimizes the blocks until each surface is back within the
  tolerance the owner sets. Each move changes what the next one measures, so
  a per-move board gate measured a moving target. It ran eight rounds on
  `text_view` (2026-10-29/30, #66), whose Theme Config and Code find rows
  passed while the bench's own faults kept moving the rest. The rule it
  replaced: a surface moved only when it held its p90 on both boards (owner,
  2026-10-29).
- Notes draws in the monospace face. A `.md` note's later form is a synced
  pair: the monospace source beside a proportional preview (owner,
  2026-10-29).

## 1. What the engine's pass costs

Measured on the T-Deck at the 2026-10-28 release candidate: the Settings
page is drawn through the verbs, and on most of its drag frames the engine
only moves pixels. Even so, those frames spend nearly all their time in the
app's pass, not in raster or flush. The numbers are in #66. The bespoke home
grid skips the pass and is several times cheaper.

So the cost the blocks must beat is the IMMEDIATE-MODE PASS: the app calling
every verb, and the engine laying out and checking every stop, every frame.
Two designs answer it, and the next measurement chooses between them (§5,
step 0):

- **(i) Call only what shows.** The pass calls the verbs of visible rows only.
  Rows outside the viewport cost nothing. This is what `ui.list` and
  `ui.table` already do internally, as one stop each.
- **(ii) Skip the pass.** A frame whose only change is the scroll offset
  re-runs no app code. The engine moves pixels and draws the entering band
  from records it kept. This is what `ui.keep` does for one stop.

**The measurement (2026-10-30, #66).** It used a `moy_prof` capture of a
held drag on the T-Deck, plus Music steps redrawn as "visible rows only, no
gens" (branch `s0`, an experiment). What it showed:

- The engine's C layout and stop checks are a small share of the pass.
- The app's Python is most of it: the VM's dict and attribute lookups, and
  the strings it makes each frame. The pixel move of a shift frame is most
  of the rest.
- Design (i) alone loses on key frames, because every visible row's verbs
  cost more than the copy it replaces, and wins on drag frames.

So:

- **Unchanged rows are carried, in C (design ii), with no app Python per
  carried row.** A Python carry, as Music's own rows frame was when this
  was measured (its `_plan` and `ui.keep`), roughly breaks even with (i),
  so it buys nothing.
- **The independent lever is Python per verb call.** A frame that reuses
  its option dicts, ids and strings costs measurably less, which is why every
  block allocates nothing per frame (§6).

## 2. What already composes

The verbs in `docs/ui_api_2026-10.md` §1 stay; their calls do not change.
Most shipped apps are drawn by them already. Three of them already virtualize
and are the model for anything new:

- `ui.list` and `ui.table` are one stop each (`STOP_ROWS`,
  `native/moy_ui/moy_ui_api.h`). Focus moves by row index, reveal is built
  in, and the engine plans their rows by pitch (`table_floor`, `rows_note`
  in `moy_ui_api.c`). A table of parts (§4.1) is the list of rich rows.
- `ui.grid` lays cards in columns. A card scrolled out of view answers a
  zero size, and the app skips drawing it.

## 3. The gaps

**G1. Nest depth.** `MOY_UI_API_NEST` is 2, and a scroll region, text
view, dialog or popover opened past it is an error (§4.0), never a silent
return. Deeper nests are cheap: a nest record is a few bytes per context.

**G2. Lists of rich rows.** §4.1 builds the block: a table's rows can be
parts the app draws, carried in C. The Blocks outline and Music steps are
tables of parts; the cards drawer and the home grid still plan their own
rows.

**G3. Scrolling is y-only, one region a frame.** The engine records one
shift region per frame, vertical (`sh_id`, `sh_r`, `sh_dy`). Map, Scene and
Paint pan with their own drag code. Two views cannot share a scroll, which
the Notes split needs.

**G4. Shell surfaces own private frame paths.** The frame calls (`shift_*`,
`floor*`) are not in any public face. They reach the shell through
`modmoy_ui.c`'s private table. But the Code tab, the desk's panes (a
pane's own frame, `EditorDesk._pane_rows`, and a pane's dialog's rows,
`_prompt_rows`) and the Launcher's bands each drive them with a path of
their own (#243 lists them). `AppHost.begin` (`runtime/ui_draw.py`, over
`moy_ui_api_host_begin`) is the one path that stays. The others go as
their surfaces move. Paint's pickers have none: the page under them is a
modal's page (`ui_draw.Hold`), and their cells are read from a part
(§4.4).

**G5. Budgets.**
- `MOY_UI_API_STOPS` (40): stops a frame. A stop past it is drawn and
  takes no focus or input, and the host logs the count (`moy_ui_api_lost`;
  `tests/test_ui_public.py` pins 38 buttons in a scroll region reached by
  key and the 39th said).
- `MOY_UI_API_KEEP` (16): kept ids.
- `MOY_UI_API_FLOOR` (24): the rects a shift frame's floor holds.
- `MOY_UI_TEXT_VIEWS` (4): text-view records, every context's together.

A list of rich rows on the P4 desk shows more controls than 40 stops hold.
The stop record is the cost, and it is counted in bytes before a budget is
raised. A table of parts takes one stop for its rows, plus the stops the
app draws in the parts that show.

**G6. The Python-only verbs.** `empty`, `toast`, `month`, `cursor`, `tile`,
`table_rows`, `table_pictures`, `card_clip`, `band` and `keep` have no Lua
or wasm row. They fall into three kinds:
- **Shared look.** `empty` and `toast` have many callers, so they are real
  parts. They gain their Lua and wasm rows as they are. A toast is a non-stop
  overlay, not a popover.
- **Frame-path tools.** `band` (the shift floor; it has no callers),
  `card_clip` and `keep` (`docs/scroll_and_text_2026-10.md` designs them).
  `band` is deleted. The other two go only when their callers move.
- **One-caller parts.**
  - `month` is called by `popover.py`, and `tile` by `achievements_ui.py`.
    Each moves into its caller as a composition.
  - `cursor` has several canvas callers and becomes §4.4's part option.

`grid_spread`, `group_card`, `card_mark` and `menu_danger` are not verbs.
They carry options (`grid`'s `cols`, `group`'s `card`, `card`'s badge,
`menu`'s per-item kinds), and the Lua and wasm faces need those options too.

## 4. The blocks

Every block enters all three faces (`runtime/ui.py`,
`native/moycore/moycore_ui.h`, `native/moy_ui/moy_ui_wasm.h` with
`native/moy_app/roles.json`'s `ui` rows, Moybyte's vendor extension). Every
block is held by `tests/test_ui_public.py`'s three-faces fixture and by an
allocation pin in `tests/test_frame_alloc.py`.

### 4.0 Nest overflow is an error

Every nest open past the limit raises in Python and Lua, and answers a
distinct code in wasm (-5, `-MOY_UI_DEEP`); `tests/test_ui_nest.py` holds the
three faces. `MOY_UI_API_NEST` rises to the depth the blocks below need,
when one needs it.

### 4.1 A list of rich rows: `ui.table`, extended

`ui.table` carries what §1's measurement asks for:
- it draws only the rows that show, as one stop (`STOP_ROWS`);
- it moves focus by row index, and reveals the focused row;
- it memoises its cells, so a steady frame runs no row Python;
- a placed table has the shift-frame and floor support in `moy_ui_api.c`.

The list block is `ui.table` with two additions, in every face
(`docs/ui_api_2026-10.md` §2, A table of parts):
- **a part column.** `o.part = (w, h)` makes column 0 a part the app draws
  into (a meter, a picture, a control), rows `h` tall. After the table,
  `ui.table_part(r)` answers the rows the frame draws, one a call, with
  each cell's rect, the canvas clipped to it. Ids drawn inside a part are
  scoped by its row index, so repeated labels across rows stay distinct.
- **per-row marks.** `cur` wears the current mark, `o.bar` the cursor bar,
  and `o.current = false` leaves the mark to the part.

A row's cells are its key, and its part is a function of its cells and
marks. A placed table of parts keeps a record of the rows that show (their
cells hashed, their marks), one such table a context. A carried row keeps
its stop and its pixels, with no app Python. A row whose cells or marks
change (focus, the current row, a field's caret) is drawn again. The
engine owns the extent (rows × row height) and the reveal, including a
current row the app moved with its own keys, which lands in view in the
frame that moved it (a step of the engine's own glides, planned at
begin). The table has no app plan; its `o.gen` keys its current row alone
(a cursor inside the row).

The page that holds a table of parts may be its frames' region R, placed
stops and all, where the page carries a gen keyed on what it shows beside
the table and its own scroll is still: R moves by nothing and the table's
view moves inside it, a second move the host makes (`sh_in`, `sh_in_dy`
in `moy_ui_api.c`'s `shift_plan`). The stops an app places round its table
then keep their pixels on the table's frames. With no such page the
table's view is R, and what lies outside it draws on every frame.

The Blocks outline and Music's steps are tables of parts, each inside a
page under a gen. The Blocks cursor's plate is a tool drawn in its part and
its slot is the table's gen, so `ui.rows` is not added; Music's cursor is
the table's current row with `o.bar`, its meters drawn in the parts.

A list's `o.axis = "x"` with `o.snap`, plus each row's fractional distance
from the viewport centre, makes the home carousel a composition: an app
scales each card's `part` by that distance. The carousel moves only if §5's
pass holds its numbers. Otherwise it is drawn over `scroll` and `part`.

### 4.2 `ui.split`: panes

`ui.split(id, dir, sizes, o, out)` answers one rect per pane, with focus
moving between panes as between groups. With `o.drag` it adds a divider the
engine keeps by id. A split with neither focus nor a divider stays the rect
algebra (`hsplit`/`vsplit`). `docs/ui_api_2026-10.md` §5a says each answer;
the Editor desk's dividers are splits.

### 4.3 Linked and two-axis scroll

- **Link.** `o.link = leader_id` makes a nest a follower. The leader's
  `moy_ui_scroll_t` is the one body: a fling on either pane drives the
  leader, and the follower's offset is read through a map. The map is DATA:
  a kept int array of source line to target y, in every face. There are no
  callbacks.
- **Axes.** `o.axis = "xy"` with `o.content = {w, h}`, for a `part` the cart
  pans.
- **Limit.** Fast frames shift one region a frame, in y (G3). A linked pair
  or an xy pan draws whole on its moving frames until the shift record
  holds more. That cost is measured before a surface depends on it.

### 4.4 `ui.part`, with input

- A part answers the pointer's position inside it (`ui.part_at`) and its
  pressed state. Paint's sprite picker reads its cell from it.
- `o.gen` lets an unchanged part be left as drawn.
- `o.cursor = {x, y, w, h}` draws `ui.cursor`'s mark with no extra stop.

### 4.5 `ui.text_view`

Plan step 1 makes it keyed by id, editable (selection, error line, find,
horizontal offset, current-line mark, reveal, lines gen), and hit-testable
(`ui.text_at`). Under §4.3 a text view can lead or follow, which is the
Notes split.

## 5. Order, each step gated

0. **Measure** where the pass's time goes, before any block is built (§1,
   2026-10-30).
1. §4.0: nest overflow becomes an error.
2. Plan step 1: Code, theme Config, the desk's made code and the console
   on `text_view`.
3. §4.1, `ui.table` extended: the Blocks outline and Music steps, then
   the cards drawer, then the home grid.
4. §4.2 with the Editor desk's panes.
5. §4.3 and §4.4 with the canvas editors and the Notes preview.
6. The home carousel (§4.1).
7. The perf pass: every moved surface's `input_gate` rows on both boards
   against dev 4d0483d3, and the blocks optimized until they hold.

A composition replacing a verb draws the verb's goldens byte for byte and
keeps its input semantics. A verb that leaves takes its callers with it in
the same commit, with no alias: there are no user apps to keep working.

## 6. Guards

- A composition calls only public verbs. `tests/test_ui_public.py` pins it
  by grepping the composition's file for any engine call outside the faces'
  verb table.
- Every block has an allocation pin for its moving frame.
- Every surface moved has its `input_gate` rows on both boards in #66, from
  dev 4d0483d3 and from the perf pass.
