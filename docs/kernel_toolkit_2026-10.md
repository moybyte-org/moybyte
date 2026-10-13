# The toolkit, data-driven — sprint 6's design (2026-10)

**What this is.** What sprint 6 of `docs/native_kernel_2026-09.md` (#224) is to
build, written before the code: the widget toolkit's bodies in C, with
style and strings held as data; one text path for every app runtime; the end
of `surface.glyph`; a compiled app drawing the same widgets; the crossing, the
pass order, the gate and the budget. Status and the sprint's measurements are
#224's; frame numbers are #66's. Revised 2026-10-11 after the owner's decisions
and an adversarial review (§12).

**The scope.** Sprint 6 runs data-driven (the plan's §10 question 8, decided
2026-10-10): widget primitives in C, style and strings as data over them.
Sprint 7 does not run: the window managers, the bar, the desk, every Layout
class and the rest of `runtime/console.py` stay Python, and nothing here
assumes they cross. Layout stays Python this sprint, and compiled apps keep
their player window (§12).

**What it stands on.** Sprint 3's glass: the canvas rows and their draw state
(`native/moy_glass/moy_canvas.h`). Sprint 4's chrome as display lists
(`native/moy_play/moy_chrome.h`). Sprint 5's app ABI: the role table
(`native/moy_app/roles.json`), the live token table and its generation, and the
`moybyte.app` import extension with its adapter (`docs/kernel_appabi_2026-10.md`
§2, §4).

**No golden moves.** The sprint has no deliberate re-baseline; a step whose
crossing moves one golden hash does not land. The one pixel difference it
accepts is outside the goldens and named in §7.

## 1. The toolkit today

SOURCE, `runtime/ui.py`. Immediate mode over the canvas verbs, no retained
tree. Style is already data: `DEFAULT_SPECS` gives each kind and state three token
specs (field, ink, edge), and `DEFAULT_METRICS` each kind a tuple of pads,
edge rings, boxes and alignment, flattened per theme into shared tuples behind a
four-entry identity ring; `runtime/skin.py` holds the skins as deltas over
both. Geometry is pure and separate from drawing. `widget_state` resolves the
six-state ladder, `Hits` is the pointer pump for bounded widget sets and
`ScrollRegion` the one scroll model.

Not data yet: the widget bodies (each a Python function issuing three to eight
canvas calls), the glyph vocabulary and its raster (twice: `chrome._blit_glyph`
and moy_chrome's C, fed by `runtime/console_notices.py`'s push of the Python
rows at start), the scaled display type that draws a glyph pixel per `rect`,
word wrap (twice: `ui.wrap_words` and moy_chrome's `panel_lines`), and every
string.

Why the bodies cross is #66's P4 editor attribution: chrome surfaces are
dispatch-bound, Python plus the MicroPython-to-C transition per canvas call. A
widget drawn by one C call in place of several Python canvas calls removes that
share. Why now is the plan's §4.5: a single implementation serves every app runtime,
and a Python app's button is a compiled app's button.

## 2. What makes it data-driven

### 2.1 The three tables

| data | its source | read by |
|---|---|---|
| **theme tokens** | the theme packages (`native/moy_ui/themes/`, flattened by `native/moy_ui/moy_theme.c`, `docs/theming_2026-09.md`), written by the look into native/moy_app's live token table (sprint 5) | the C style resolution; every app's `theme` reads |
| **style** | `native/moy_ui/style.json`: the kinds, the states, each `(field, ink, edge)` spec with its alias chains, each kind's metric tuple with its slot names, the quirks of `NON_DATA_QUIRKS` against their kinds, the skins as deltas, the glyph vocabulary | the generated `native/moy_ui/moy_ui_tables.c` |
| **strings** | `native/moy_spine/strings/en.json`: one entry per string id, English complete, each format's conversions part of its text | the generated `native/moy_spine/moy_str_table.c` (§2.3) |

**The generated table is committed.** `runtime/native_build.py` runs no
generator, and neither do the board builds, so `tools/gen_ui_tables.py` writes
`moy_ui_tables.c` and the commit carries both; a test regenerates it and fails
on any difference, as preflight's derived-artifact steps do for the web blob.
A data edit is the JSON plus the regenerated file; it touches no C body and no
Python logic, and it still needs a firmware build to reach a board. The tables
are `const`: they cost flash and no RAM or boot parse.

**The style file is the part list theming phase 1 freezes**
(`docs/theming_2026-09.md` §4.3), parts and states as nested objects. Theming
runs after this sprint, in C (§12), and theme packages arrive as deltas on the
skin's entry.

### 2.2 Which edits stay data, which need C

| a change | the edit |
|---|---|
| which token a widget state paints; a skin | data (style) |
| a theme's colours | data (a theme package) |
| a pad, an edge weight, a strip height, a label alignment | data (metrics); a size change moves goldens, a deliberate act as today |
| a new kind that reuses a primitive's shape: a toggle, the Studio's pane tab, another row look | data (style) |
| a glyph's pixels; a new glyph | data (style's glyph vocabulary) |
| a string C draws | data (strings) |
| any layout; which widget a screen draws where; a string Python passes in | Python (apps, Layouts and WMs stay Python) |
| a new primitive shape, a new state, a new metric slot, a quirk, the hover and press rules, scroll physics, the text lane, a font format, theming's painter | C |

### 2.3 Strings as data: the localization hook (#135)

A string id is a dotted name (`fit.needs`, `recovery.title`). C reads
`moy_str(id)` and formats with `moy_str_fmt` (`native/moy_spine/moy_str.h`);
the Python face reads `ui.s(id)`. The table is the spine's, not the toolkit's,
because every image links the spine and the kernel's floor draws its words on
the Zero, which denies the toolkit. Each entry keeps its conversions in order (`%s`, `%u`,
`%d`, `%08x`: the stuck, fit, ABOUT and recovery texts use all four), and a
language pack whose entry carries different conversions is refused for that
id, which then reads English: a pack is untrusted data, as a theme is
(`docs/theming_2026-09.md` §7).

**What moves this sprint: the strings C itself holds**, the literals of
`native/moy_play/moy_chrome.c` (the SAY table, the stuck and fit texts, the
ABOUT box's formats) and of `native/moy_kernel/moy_recovery.c` and
`native/moy_kernel/moy_kernel.c`. The menu rows, the panel and strip titles,
the notice banner and the toast are **Python-fed**: `runtime/console_notices.py`
and the bar pass them into moy_chrome (`native/moy_play/modmoy_chrome.c`'s
builders). The sysmenu's rows and the notices the runtime raises read the
table through `ui.s(id)` (`PY_FED_MOVED` in `tests/test_ui_strings.py` pins
their literals out); the other Python-fed words (the running-slowly notice's
constants, the bar's titles, the update and store texts built elsewhere)
still pass as `PY`, and a screen may mix the two sources.

**Every `moy_text` call carries its source** (§4): `TABLE` (a string id), `PY`
(text a runtime passed in), `DATA` (a number or a user's text, formatted into
a table entry) or `LIT` (a literal in C). The gate is that `LIT` never reaches
the raster; the `PY` call sites are the ratchet's first rows, each removed as
its strings move.

Glyph coverage, #135's other half, comes with theming's font format, not this
sprint (owner, 2026-10-11): the text call already takes UTF-8 and a font, so a
font that covers Cyrillic is data, not a toolkit edit.

### 2.4 The absorbed issues

Of the plan's §6.2 row for sprints 6–7, what lands in the toolkit: #135's string
hook (§2.3); #177's pump, which crosses whole (§3.4), while extending hover to
the desk icons, the window strip and the taskbar stays WM work; #146's press
cue, already the `pressed` state's data. The rest of the row (#140's charm
items, #132, #129, the Studio's docking) lands in apps and WMs. The Studio's
new parts are kinds in the style file; one whose shape no primitive draws adds
a primitive when the Studio lands, which by its §8 is not during this sprint.

## 3. The C primitives

### 3.1 The module

`+native/moy_ui/`: the style resolution, the primitives, the hit pump, the
scroll model and the string table. Three bindings, the moy_app pattern: a
MicroPython usermod (`native/moy_ui/modmoy_ui.c`) on the boards and in the
browser; a ctypes binding on the host (`tools/moy_ui_binding.py`); and wasm
rows in the role table (§3.5). The Zero denies it in its board.toml: it runs no
apps.

**The sink.** A primitive draws through a sink, never a canvas object:

    typedef struct {
        uint8_t kind;      // ROW: a canvas row; RUN: a compiled run's libmoy canvas
        uint8_t fs;        // the font scale: the row's FONT_SCALE, or the run's surface row
        uint8_t look;      // which flattened style table: the shell's or the run's
        void *dst;         // moy_canvas_row_t * or moy_canvas *
    } moy_ui_sink_t;

The **ROW sink** draws into a canvas row of sprint 3's glass, named by its
CANVAS handle and checked as any handle is, through the kernels the canvas
verbs end in (`native/moy_gfx/moy_gfx_kernels.h`): camera, clip, size and font
scale from the row's state, colours through the row's palette, and the row's
fill and text counters bumped per op. It pumps nothing: the T-Deck's flush
feeds itself (`native/moy_flush/moy_flush.c`'s feeder loop), and the draw
gates hold no pump. The Python face flushes a pending sprite batch before the
call, as the canvas's `print` does (#63's order rule).

**The host's rows are not written today.** `DeviceCanvas` writes the row's
state and palette only when it installs the draw gates
(`_install_draw_gates`, `_sync_gate_state`, `_sync_gate_pal`), and the host's
kernel has no `make_draw_ctx`, so a host canvas's row keeps an empty clip and
no palette, and `gate_counts()` answers zeros. Step 1 makes every canvas keep
its row's state, palette and counters in step, gates or not, and a host test
reads the row back after `camera()`, `clip()` and `pal()`. From then
`gate_counts()` is real on the host.

The **RUN sink** calls libmoy's verbs on a compiled run's console. libmoy draws
through the cart's draw state (its `pal()`, screen palette and `fillp`, SPEC.md
§3.1), so the two sinks agree only on a reset state: a `ui_*` row saves the
run's draw state, resets it, draws and restores it, and a widget looks the same
whatever the cart left set.

**The look.** The style resolved against one token row: per kind and state,
three palette indices, `-1` for "paint nothing". It is rebuilt lazily per kind
when the skin's or the token row's generation moves, as `_flatten_kind` does
today. The fallbacks are today's, not errors: an unknown kind resolves as
`row`, a state the kind does not name resolves as its REST, and a token the
theme does not hold resolves through its alias chain to its fallback or to
`-1` (the `self.theme or {}` callers paint nothing where the theme is silent).
There are two looks, the shell's (the VM's task) and a compiled run's (its
session thread, read under sprint 5's seqlock), so no flatten is shared between
threads. Both are allocated once with `heap_caps_malloc(MALLOC_CAP_SPIRAM)` at
module init, with no static BSS (the plan's §4.6). A theme dict Python passes
that is not the live theme (Appearance's per-card previews) is packed into a
token row in the four-entry ring the face keeps, as `_FLAT_RING` does.

### 3.2 Call shapes

Every primitive takes a sink, a box, a kind id and a state, then its content.
Text is a UTF-8 span; a glyph is a glyph id or `-1`; a colour override is three
`int16`, `-2` meaning "from the style". A primitive returns what its caller
needs back (a drawn width, an art box) or an error.

    typedef struct { int16_t x, y, w, h; } moy_ui_box_t;
    typedef struct { const uint8_t *p; uint16_t n; } moy_ui_span_t;

    int  moy_ui_kind(const char *name, size_t n);    // a kind's id; an unknown name is row's
    int  moy_ui_glyph_id(const char *name, size_t n); // or -1: draws nothing, the label stays
    void moy_ui_colors(const moy_ui_sink_t *s, int kind, int state, int16_t out[3]);
    const int16_t *moy_ui_metrics(int kind);

    int moy_ui_button(moy_ui_sink_t *s, moy_ui_box_t b, int kind, int state,
                      moy_ui_span_t label, int glyph, const moy_ui_icon_t *icon);
    int moy_ui_row(moy_ui_sink_t *s, moy_ui_box_t b, int kind, int state,
                   const moy_ui_row_t *r);   // label, value, glyph, pads, text_dy, scale, edge, overrides
    int moy_ui_cell(moy_ui_sink_t *s, moy_ui_box_t b, int kind, int state,
                    const moy_ui_cell_t *c, moy_ui_box_t *art);

Errors are `MOY_UI_OK`, `MOY_UI_BAD` (a span over its bound, a state outside
the six) and `MOY_UI_STALE` (a sink whose canvas row is gone). An icon image
crosses as its pixel buffer, size and palette, never as an object.

**Stack.** A primitive keeps no buffer on the stack beyond a line of text; a
formatted string is written into a buffer the caller holds. The chain face →
primitive → `moy_text` → `snprintf` runs on mp_task's stack, whose margin
#224 records, so the gate reads its high-water (§10).

### 3.3 The primitives

| primitive | C | Python face | wasm row (`moybyte.app`) |
|---|---|---|---|
| button | `moy_ui_button` | `ui.button`, signature unchanged | `ui_button(x, y, w, h, kind, flags, interact, label, len, glyph)` |
| chip, and a toggle as a chip kind | `moy_ui_chip` | `ui.chip`, unchanged | `ui_chip(...)` |
| row | `moy_ui_row` | `ui.row`, unchanged | `ui_row(x, y, w, h, kind, state, label, len, value, vlen, glyph)` |
| cell | `moy_ui_cell`, returns the art box | `ui.cell`, unchanged | `ui_cell(..., art_out)` |
| tab row | `moy_ui_tabs`, returns the tab boxes | `ui.tab_row`, `ui.tab_row_rects`, unchanged | `ui_tabs(...)` |
| status row, panel, toolbar, focus ring, dialog, text field | one function each | unchanged | one row each |
| scrollbar | `moy_ui_scrollbar` over a scroll state | `ScrollRegion.draw_bar`, unchanged | `ui_scrollbar(...)` |
| scroll list: visible rows, the selection and the bar over a scroll state | `moy_ui_list` | new: `ui.list` | `ui_list(x, y, w, h, kind, items, len, n, row_h, offset, sel)` |
| label: text in a box, aligned, clipped; returns the width | `moy_ui_label` | new: `ui.label` | `ui_label(...)` |
| glyph (§5) | `moy_ui_glyph` | new: `ui.glyph`, `ui.glyph_id` | `ui_glyph(...)` |
| the game-canvas family | `moy_ui_game_btn` and its two siblings | unchanged | none: cart verbs draw a cart's buttons |
| rect algebra | `moy_ui_inset` and its siblings | `ui.inset` and its siblings, unchanged | `ui_inset(...)` and siblings |

**How Python drives one.** `runtime/ui.py` stays the module every shipped app
and every app cart imports (`docs/app_api_v1.md`'s `ui`). Each function becomes
a thin face: it converts keywords to the C entry's positional ints, maps the
kind name to its id (interned at first use) and makes one call with the
canvas's CANVAS handle. What stays Python because it is a Python seam: a
`glyph_draw` callable, which **replaces the label** as today (`code_layer`'s
chip face): the C call draws the field and the edge, then the face calls it;
the `th` ring; the `(verb, arg)` ids of §3.4; `state_colors`, whose three
callers keep getting a shared tuple from a face cache keyed on
`(generation, kind, state)`, so the hot path still allocates nothing. Text
editing stays the app's (`runtime/editors_base.py`); the field primitive only
draws.

**How wasm drives one.** The `ui` rows are C-served and ungated, which the role
table marks as it marks the theme's reads, and the adapter registers them
beside those. They draw into the run's canvas with the RUN sink on the session
thread, with no hop, and leave the VM-free verdict unchanged. An app reads its
kind and glyph ids once (`ui_kind`, `ui_glyph_id`) and the state constants from
the author's header the table generates.

### 3.4 Interaction state: caller-owned buffers, not handles

`Hits` and `ScrollRegion` are state a surface owns for its life. Their rules
cross to C as pure functions over a state buffer **the caller owns**: a
`bytearray` inside the Python object, or a span of the compiled app's linear
memory. The kernel holds nothing between calls, so there is no table and
nothing to end at a VM stop: the plan's §4.3 rule holds with no handle.

- **Hits.** Ids are `int32`. A compiled app chooses its own. The Python face
  interns `(verb, arg)` to an id per instance, **resets the intern at every
  `clear()`**, and keeps `hover` and `pressed` as the `(verb, arg)` values
  `state_of` compares, so they persist across a clear as today. The buffer's
  rect capacity defaults to twice the most rects any surface registers in the
  goldens' frames, which step 1 measures and records as configuration. An add
  past it answers FULL, is counted and printed once per surface, and raises
  under `HITS_DEBUG`. The grid rule stands: `cell` registers nothing.
- **ScrollRegion and DragTap.** The offset, extent, drag and fling are the
  buffer's. The fling decays by `FRICTION ** dt` in single-precision float,
  which is what the boards' MicroPython computes, so the differential runs on
  the unix MicroPython build (`tests/unix_mp.py`), comparing integer offsets
  per tick. `blit_shift` and `note_painted`, which copy canvas pixels, stay in
  the Python face.

### 3.5 The role table's `ui` rows

The `ui` rows join `native/moy_app/roles.json` as a role of their own: server
`c`, grant `never`, wasm-typed, and taking no grant: the load's check admits
them for every compiled app that declares the extension. As built (step 9):
nine rows (`ui_kind`, `ui_glyph_id`, `ui_button`, `ui_label`, `ui_list`, and
the pump's `ui_hits_clear`, `ui_hits_add`, `ui_hits_pump`, `ui_dragtap` over
buffers in the app's memory), served by `native/moy_ui/moy_ui_wasm.c` and
named in `native/moy_app/moy_app_wasm.c`'s table, so every engine's adapter
registers them with the rest. No AppContext object is built for the role
(`app_context.TOOLKIT`): a Python app draws the same C through `import ui`.
The rows' contract and buffer layouts are `docs/app_api_v1.md`'s compiled-app
section; `tests/test_roles.py` holds the table, the adapter and the doc
together, and the fixture `tests/fixtures/wasm/app_ui.moy` imports every row.
A cart that declares no `moybyte.app` extension imports only from `"moy"`, as
now. A compiled app uses them in its player window, cart-shaped: a fixed
canvas, scaled.

**The RUN sink lends the run's canvas as a row.** The target is the run's
(`moycore_ui_target`, from the module that drives the run: an owed or kept
frame settled first, as libmoy's verbs do); around each row the run's draw
state is saved, reset and restored, and the reset canvas's index-to-pixel
words become the lent row's palette, so the primitives draw it exactly as
they draw a glass row.

## 4. One text path

**What it is.** `moy_text` in `native/moy_gfx`, over the rasteriser the system
canvases' print gate already uses at every scale (`mg_text_raw` with the staged
petme128 blob, `native/moy_gfx/modmoy_gfx.c`'s print gate):

    int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
                 const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
                 int align, int maxw);                                    // drawn width
    int moy_text_width(uint32_t font, const uint8_t *s, size_t n, int scale);
    int moy_text_wrap(uint32_t font, const uint8_t *s, size_t n, int cols, int mode,
                      moy_text_line_t *lines, int cap);

As built (step 3, `native/moy_gfx/moy_text.h`): the destination is a text
target (buffer, camera, clip) that a sink, the print gate and the `text`
kernel each fill from what they hold, and the wrap has two modes, the
toolkit's (words, truncation) and the panels' (hard newlines, long words
broken across lines), so the two old wraps are one body.

Every UI text reaches it: the system canvases' `print`, every primitive's
label, the scaled display type (`chrome._print_scaled`), `chrome._text_w`, word
wrap (`ui.wrap_words` and moy_chrome's `panel_lines` become one), the recovery
screen and the kernel's own frames, and a compiled app's `ui_label`. Chrome
text at scale 1 stays on `mg_text_raw`, as the print gate draws it now; it does
not move onto libmoy's lane.

**What does not.** A cart's `print` stays libmoy's `moy_print` at 8 px on every
cart runtime (moy-spec's SPEC.md §6), byte-identical, Lua's C API included.

**The font is a handle-shaped `uint32`.** Sprint 6 has one font, petme128, id
0. Theming's font format adds rows behind the same argument, each on its own
lane gated by its own S3 A/B (`docs/theming_2026-09.md` §4.7).

**Measured in what the raster draws.** Text is UTF-8 bytes; petme128 advances
per byte, so width and truncation count bytes, where today's Python counts
codepoints and `font.draw_scaled` advances per character. For ASCII the two
are equal; §7 names the difference for anything else.

## 5. `surface.glyph`'s fate

It leaves the role table: it was a Python-only row only because the glyph
raster was a Workstation method, and once the raster is a primitive it is
drawing, not a role.

- `chrome._GLYPHS`'s twelve-row bitmaps move into the style file. The raster
  derives each glyph's spans from its rows at draw time, at any scale, so no
  per-scale pack is stored; `_glyph_runs`, `_glyph_pack` and their caches go.
- One raster, `moy_ui_glyph`, serves the toolkit, moy_chrome's GLYPH op and
  `ws._glyph`. `moy_chrome_glyph_put`, `modmoy_chrome.c`'s `chrome_glyph` and
  the push loop in `runtime/console_notices.py` are deleted.
- `runtime/shell_servers.py`'s `SurfaceServer` is deleted. Callers that passed
  `glyph_draw=self._surf.glyph` pass only `glyph=`; Appearance's direct call
  becomes `ui.glyph`.
- **Every reader of `_GLYPHS` asks the table instead** (`ui.glyph_id(kind) >= 0`
  for "is it a glyph"): `chrome._gbtn` and `chrome._blit_glyph`;
  `runtime/code_layer.py`'s `_glyph_known` and its lazy import;
  `runtime/paint_layer.py`'s tool buttons; `runtime/music_editor_ui.py`'s
  button row; `runtime/console_notices.py`'s `KERNEL_GLYPHS` push (deleted);
  the comments of `runtime/artwork.py` and `runtime/widgets.py`. Step 4 greps
  the name and leaves none.
- A compiled app draws one with `ui_glyph`.

## 6. Compiled apps on the desk

A compiled app keeps today's player window (owner, 2026-10-11): a fixed canvas,
scaled by the WM, cart-shaped, with the `ui` rows drawing into it. A reflowing
window (a window's surface, size and focus for a compiled app) is deferred. One
compiled run at a time is moycore's single static run slot
(`native/moycore/moycore_run.h`'s `moycore_run_cur`, held by
`native/moycore/modmoycore.c`), shared by Lua, p8 and wasm; that is today's
behaviour, and lifting it means a multi-instance moycore, a later, P4-first
item.

## 7. The pixel goldens

The host draws with the boards' raster: `runtime/host_canvas.py` runs
`device/device_canvas.py` over the C kernels through ctypes. So a crossing can
drift pixels only through transcription, and each hazard is settled here
rather than left for a golden to find:

| hazard | settled |
|---|---|
| order | field, edge rings, icon or glyph, value, label, then the hit registration; a `glyph_draw` callable replaces the label |
| division | every `//` is a floor division (`moy_ui_fdiv`): C's `/` truncates, and the bodies divide negative differences whenever a label is taller than its box |
| floats | the face converts each coordinate as the print gate's `gate_num` does (toward zero) before the call; a body that did float arithmetic before the canvas truncated is found by the fuzz, and the goldens' call sites are asserted to pass ints |
| bytes and codepoints | C counts what the raster draws, bytes (§4). ASCII is identical; a non-ASCII label truncates by bytes, which fixes the overflow today's codepoint count lets past the box. This is the one accepted difference; the goldens hold no such label |
| fallbacks | an unknown kind is row's, a missing state is REST, a missing token paints nothing (§3.1), never BAD |
| physics | single-precision float, compared on unix MicroPython (§3.4) |

**The differential twin.** The deleted Python bodies move under `tests/` as the
oracle, as the spine's did (`tests/spine_twin.py`). A fuzz draws random boxes,
including boxes smaller than their label and negative and float coordinates,
with ASCII and UTF-8 labels, fs 1 to 3 and both skins, once through the twin and
once through the C, and compares pixel by pixel; a failure reports the
widget, the case and the differing pixels' bounding box. Hashes are taken per
widget per case, not per sheet. The twin draws its glyphs through the real C
raster, as the C does, so the widget matrix exercises it.

**The nets.** `tests/test_shell_goldens.py` on all six rows;
`tests/test_ui_states.py`'s widget matrix, extended in step 1 to every
primitive and kind; `tests/test_skin.py`; the settings pixel test; the twin's
fuzz; and on glass, a widget sheet hashed per widget on each board against the
host. No pixel-moving theming phase and no Studio step overlaps the sprint
(`docs/theming_2026-09.md` §8).

## 8. The crossing table

| what | where it goes | the Python removed |
|---|---|---|
| `runtime/ui.py`: `DEFAULT_SPECS`, `DEFAULT_METRICS`, the slot constants, `NON_DATA_QUIRKS`, `_STATE_ALIAS` | `native/moy_ui/style.json` and its generated table | the tables; the slot names stay as constants the face exports |
| `runtime/ui.py`: `_token`, `state_token`, `_flatten_kind`, `_bind_theme`, `widget_state`, `metrics`, `state_colors` | the C style resolution (§3.1) | the bodies; `state_colors` and `metrics` stay as faces |
| `runtime/ui.py`: `set_skin` and its callable seam `fn(th, kind, state)` | the skin by name in C | the seam, with `tests/test_ui_states.py`'s case that installs a callable; no runtime caller uses it |
| `runtime/ui.py`: the widget bodies (button through `fill_uncovered`), `scroll_cues` | the C primitives (§3.3) | the bodies, kept under `tests/` as the twin |
| `runtime/ui.py`: `Hits`, `ScrollRegion`, `DragTap`, `cell_state`, `row_drag` | C over caller-owned buffers (§3.4) | the rules; the objects stay as the buffers' owners, with `blit_shift` |
| `runtime/ui.py`: `inset`, `cut_*`, `hsplit`, `vsplit`, `wrap_words` | `moy_ui` and `moy_text_wrap` | the bodies |
| `runtime/skin.py` | the skins as deltas in the style file; `names`, `use`, `active` on the face | the module |
| `runtime/chrome.py`: `_GLYPHS`, `_glyph_runs`, `_glyph_pack`, `_blit_glyph` | the style file's glyphs; `moy_ui_glyph` | those (§5 lists the readers) |
| `runtime/chrome.py`: `_print_scaled`, `_text_w` | `moy_text`, `moy_text_width` | both bodies |
| `runtime/font.py`: `draw_scaled` | `mg_text_raw` at the scaled size | `draw_scaled`; the glyph bytes stay for the host's framebuf shim |
| `runtime/shell_servers.py`: `SurfaceServer`; roles.json's `surface.glyph` | `moy_ui_glyph` | both |
| `runtime/console_notices.py`: the glyph push | nothing: one raster | the loop |
| `native/moy_play/moy_chrome.c`: `panel_lines`, `moy_chrome_glyph_put`, its literals | `moy_text_wrap`, `moy_ui_glyph`, the string table | the C copies |
| `native/moy_kernel/moy_recovery.c`, `moy_kernel.c`: their literals | the string table | the literals |
| `runtime/appearance.py`: `set_skin` | writes the skin name and its generation | its install of the Python tables |
| `device/device_canvas.py`: the row's state, palette and counters | kept in step on every canvas (§3.1) | nothing |

`chrome.NAMES` stays a Python dict: it is the palette's names, read at
hundreds of sites across the runtime, not style. `runtime/layout_base.py`, `chrome.Layout`,
`CodeLayout` and every app's Layout stay Python.

## 9. The pass order

Each step lands as one whole commit. While working, host tests; before
committing, `make test`, `tools/preflight.sh --web` and `tools/board.py pass
tdeck p4`. A step fixes, where it lives, each sentence it makes false (§13),
and the traces grow before the code they pin. **A T-Deck Settings A/B** (the
chrome-frame timing from the dev channel, against step 1's A side) is taken at
steps 2, 3 and 6, not only at the end.

1. **The rows on the host, and the nets.** Every canvas keeps its row's state,
   palette and counters in step, with the host test of §3.1;
   `native/moy_ui/style.json` and the strings' `en.json` written from today's
   tables and literals, held equal by a test; the widget matrix extended; the
   twin under `tests/` with its fuzz; per-widget op budgets pinned from the
   twin's counted ops; the Hits capacity measured; a ratchet that every key a
   style spec reads, in every theme dict a widget is handed, is in the token
   vocabulary; the A side of every A/B (§10) captured into #66. No behaviour
   moves.
2. **`+native/moy_ui/` and the style in C.** The module and its three bindings,
   which the Zero denies; the generated table and its drift test; the looks in
   PSRAM, the ring, the face's tuple cache, the skin by name; `skin.py`, the
   Python resolution and the callable seam deleted.
3. **The text path.** `moy_text` with its source tag, width and wrap; the
   system canvases' `print`, `_print_scaled`, `_text_w`, `wrap_words` and
   `panel_lines` on it; `draw_scaled` deleted. The per-widget op budgets of
   text-bearing widgets are re-pinned: a scaled heading is now one text op where
   it was a rect per block.
4. **Glyphs.** The bitmaps into the style file, one raster deriving spans at
   draw time, every reader of §5 moved, the push and `SurfaceServer` deleted,
   `surface.glyph` out of the role table. Glyph-bearing budgets re-pinned.
5. **Rows, cells and lists.** `row`, `cell`, `label`, `list`, `scrollbar`,
   `scroll_cues`, `fill_uncovered` in C.
6. **Buttons and frames.** `button`, `chip` (with the toggle kind), `tab_row`,
   `status_row`, `panel`, `toolbar`, `focus_ring`, `dialog`, `text_field`, the
   game-canvas family, the rect algebra.
7. **Interaction.** `Hits`, `ScrollRegion` and `DragTap` over caller-owned
   buffers; `tests/test_ui_states.py`'s pump rules run against the C, the
   physics on unix MicroPython.
8. **Strings.** The table with English; moy_chrome's and the kernel's literals
   on it; the source tag's gate and the `PY` ratchet (§2.3).
9. **The `ui` rows for wasm.** The rows in roles.json, the RUN sink with its
   state save and reset, the adapters on the host, the boards and the browser;
   a fixture app that draws a button and a scroll list and scrolls it by drag.
10. **The sprint's end.** The A/B's B side; `docs/app_api_v1.md` and
    `runtime/README.md` checked line by line against the code; every board
    passes the gate; numbers go to #224 and #66.

## 10. The gate

Every check below fails while its bug is present; the step that adds a check
first runs it red against that bug, then green.

| gate | check | fails on |
|---|---|---|
| goldens unchanged on each row the toolkit draws | the six shell rows, the extended widget matrix, both skins | a transcription drift that survives to a whole surface |
| the C draws what the Python drew | the twin's fuzz (§7), per widget per case, with the bounding box of any difference | the hazards of §7 one by one: truncating `/`, a float truncated late, a lost fallback, a reordered op, a `glyph_draw` drawn beside the label |
| the host's rows are the canvas's | read the row's state and palette after `camera()`, `clip()` and `pal()` on a host canvas | a C widget drawing with an empty clip and no palette, which the goldens would show only as blank surfaces |
| the C on glass is the host's C | a widget sheet on the T-Deck, the Guition S3 and both P4s, hashed per widget against the host | a target-only difference: an unaligned load on the S3, a lane chosen differently |
| one crossing and the same ops per widget | the binding's entry counter in C, against the number of widget calls the test made; the row's op counters per widget against the budgets pinned from the twin | a widget drawn through per-op Python calls; a body that issues more or fewer ops than the one it replaced |
| a widget-heavy frame A/B on one S3 board and one P4 board | `tools/p4_bench.py` painted-frame medians and p90s for Settings, Files, Storybook and the editor's tabs on the Waveshare P4, and Settings and Files on the T-Deck and the Guition S3 with the dev channel's chrome-frame timing, against step 1's A; no surface 2 ms or more slower; idle scenarios capture zero frames | a flatten per widget, a by-name lookup per call, a keyword parse in C, a tuple built per `state_colors` |
| style is data | a test copies the tree's `style.json`, changes a state's token and a metric and adds a kind, runs `tools/gen_ui_tables.py`, builds the host library through `runtime/native_build.py`'s own path, and finds the change drawn where expected and nowhere else; the drift test fails a committed table that differs from its JSON | a value written into a C body instead of read from the table; a stale committed table |
| strings are data | `moy_text` refuses `LIT`, and the C-drawn screens rendered under a pseudo-locale draw no `LIT` call; a source ratchet finds no string literal passed to the raster in `native/`; a pack with a mismatched conversion reads English for that id | a literal left in C; a translation that breaks `snprintf` |
| one text path | a source ratchet over `native/` and `runtime/`: `mg_text_raw` (petme128's lane) and `mg_text_font` (a loaded font's) are called only from `moy_text`, which the print gate enters, and `draw_scaled`, `panel_lines` and `wrap_words` have no body left; `tests/spec_conformance/hashes.json` and the Lua C API unchanged | a second rasteriser left behind; a cart pixel moved |
| `surface.glyph` gone | roles.json and the role ratchet; `_GLYPHS` named nowhere in `runtime/`; no `chrome_glyph` | a pushed copy left to drift |
| one pump | the pump rules run through the Python face and, in the wasm fixture, through input the console delivers to the run's snapshot (a scripted touch on the host and on glass), with the same states out | two pumps diverging; a fixture that feeds its pump by hand |
| no tap is lost | a surface registering past the default capacity logs FULL in the test and raises under `HITS_DEBUG` | a silent drop of the widget drawn last |
| a compiled app draws a button and a scrolling list | the fixture under WAMR on the host, in the browser suite, and on the T-Deck and the P4: pixels hashed per widget against the host, a drag scrolls the list, a cart `pal()` left set does not change them, zero upcalls of every class, the VM-free verdict unchanged | a `ui` row routed through Python; a RUN sink that draws through the cart's draw state |
| the standing meters | all five boards passing; `KERNEL_SRAM`, internal SRAM free and its low-water with the radios up, within the plan's §6.1 share; mp_task's stack high-water no lower than step 1's; each image within this sprint's budget (§11) and over its floor | what every sprint's gate guards against, and a deeper C chain under the VM's stack |

## 11. The budget

**Image.** The T-Deck is the tightest console image; its headroom at sprint 5's
end is #224's, and its floor is 512 KiB (the plan's §6.1). ESTIMATED: sprint
5's steps grew the image about 10 KB each (#224), and eight of this sprint's
steps add C, about 80 KB, less the frozen bytecode the deleted Python held;
the glyphs add no per-scale packs (§5). **The sprint's budget is 80 KiB net on
the T-Deck** (configuration), leaving most of the headroom over the floor for
theming and what follows it. Each step records its image delta in #224; a step
over 20 KiB is split or explained before it lands.

**Memory.** The style table, the glyph bitmaps and the strings are `const` in
flash. The looks and the ring are PSRAM, allocated explicitly (§3.1); the hit
and scroll state is the caller's. No internal SRAM is added, and the gate's
meters check it.

## 12. Decisions

**The owner's (2026-10-11):**

1. **Reflowing (resize-aware) compiled apps are deferred.** A compiled app
   keeps today's player window on the desk (§6), a fixed canvas the WM
   scales, and nothing in this sprint removes or regresses it. The single run slot is moycore's,
   and lifting it is a later, P4-first item.
2. **Layout as data is dropped this sprint.** Style and strings are data;
   every layout stays Python.
3. **Theming runs after sprint 6, written in C directly**, with no Python
   reference first; `docs/theming_2026-09.md` §8 says so.
4. **Only the strings C holds move now.** Python-fed strings move later under a
   ratchet, and a screen mixing the two is accepted until then.
5. **#135's glyph coverage comes with theming's font format**, not this sprint.

**This design's:**

- The generated table is committed and guarded by a drift test, not generated
  by the three builds (§2.1).
- The sink draws into canvas rows, which step 1 keeps written on every tier
  (§3.1).
- Chrome text keeps `mg_text_raw` at every scale (§4).
- Text measures bytes, the one accepted pixel difference, outside the goldens
  (§7).
- Glyph spans are derived at draw time (§5); `chrome.NAMES` stays Python (§8).
- Interaction state lives in buffers the caller owns, with the intern reset
  per clear and a logged capacity (§3.4).

## 13. Claims this sprint falsifies

Each sentence is fixed where it lives, by the step given:

| sentence | file | step |
|---|---|---|
| `skin.py` stays unchanged data; `ui.py`'s core; one text path is sprint 6's | `docs/native_kernel_2026-09.md` §2.2.1 | 2, 3, 10 |
| style is data in `runtime/skin.py`; the toolkit's tables in `ui.py` | `.claude/rules/rendering.md`, `runtime/README.md`, `runtime/skin.py`'s and `runtime/ui.py`'s headers | 2 |
| the T-Deck root canvas must poke the flush pump between native ops | `native/moy_gfx/modmoy_gfx.c`'s `fill_spans` comment | 1 |
| `surface.glyph` is a Python-only row until sprint 6 | `docs/kernel_appabi_2026-10.md` §2.1, §2.4, §11; `docs/app_api_v1.md` | 4 |
| moy_chrome's glyph raster is fed from Python at start | `native/moy_play/moy_chrome.h` | 4 |
| `ui` is the real `runtime/ui.py` in Python | `docs/app_api_v1.md`, `runtime/system_api.py`'s header | 6 |
| a reflowing (resize-aware) compiled app is sprint 6's, its lifecycle exports and its input an event queue | `docs/kernel_appabi_2026-10.md` §4.1, §12 | 10 |
