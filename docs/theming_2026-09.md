# Theming — one data package re-skins the whole OS (2026-09)

**Status: ACCEPTED by the owner, 2026-10-11.** Rev 2 (2026-09-27) took an
adversarial architecture pass and a performance/hardware pass the same day
(verdicts: **STANDS WITH FIXES, close to REWORK** / **PERF CASE STANDS WITH
FIXES**); **§14 records each finding and where it landed**. Rev 3
(2026-10-11) re-reads §2, §8 and §9 against what kernel sprint 6 built, rules
on §12's questions and plans phases 1-4 as steps. The owner's decisions are in
§1; where a phase stands is #224's.
**Depends on:** `docs/kernel_toolkit_2026-10.md` (sprint 6 of
`docs/native_kernel_2026-09.md`: the toolkit's bodies in C, with style as
data; the window managers stay Python; §8 maps every theming piece onto it).
**Relates to:** `docs/visual_identity_v1.md` (the default look),
`docs/app_api_v1.md` (`ctx.theme`, the app-cart `theme()` global),
`docs/surface_model_v1.md` (one dirty protocol), `docs/shell_ux_v1.md` (#105,
the two worlds), `docs/studio_2026-09.md` (the Studio's chrome is drawn from
these parts), `native/moy_ui/style.json` (style as data), #177, #146, #147, #203.
Claims are labelled as in `docs/surface_model_v1.md`: **SOURCE** (read out of
code, with the file), **ESTIMATED** (with how), **PREDICTED** (with the gate
that settles it).

---

## 0. The thesis

**A theme is one small, data-only package that re-skins every layer of the
OS** — colours, sizes, how surfaces are drawn, window decorations, fonts, icons,
images and, later, sounds. It installs, shares and remixes like a cart. Drawing
code never names a colour or a shape: it asks for a **role** or a **part in a
state**, and the active theme answers with MOY64 indices, sizes and a compiled
surface record. **Nothing in a theme can run**: a theme holds data and images,
and it may *name* installed carts (wallpapers) but never contain one.

The range it must cover is the owner's: the default Moybyte look (D1) plus
looks in the family of classic Mac OS Platinum, a riced tiling desktop, Windows
9x and XFCE/LXQt. That means bevels, pinstripes, gradients, flat panels and
different fonts, not recolours only.

**Scope.** This doc is theming proper: tokens, parts, the surface painter, text,
sprites, images and the package. Where the bar goes, window modes (floating or
tiled) and the home screen's structure are **shell layout**. The owner decided
them (D5, D7), and they are designed in their own doc against
`docs/shell_ux_v1.md` and the window managers, which stay Python (§4.8). A theme reserves keys
for them from format 1.

## 1. Decisions (owner, 2026-09-27)

| # | decision |
|---|---|
| D1 | The default look is **"Open Machine, refined"**: the Workbench structure (1px black outlines, 2px hard shadows, calm light/dark chrome over the PICO-8 base colours), the **Tiny5** UI font, desk icons as tiles with the **label in a pill under the tile**, window title strips **left-aligned with the app's identity stripe and a square close on the right**, and **Moy's night** (stars, a low hill, a moon, Moy on the hill) as the default wallpaper. Light and dark are ONE drawing routine and two token tables. |
| D2 | Themes change the OS **extensively** (§0's range), not only colours. |
| D3 | A theme can carry **paintable sprites** (9-slice frames, glyphs, a desktop tile), in the first or second version; the format reserves them from format 1. |
| D4 | **Apps follow the theme.** Settings, Calc and every shipped app restyle with it, because they draw through the one toolkit. App carts read it through `theme()` (§6). |
| D5 | **Layout: both.** A theme sets the default bar layout and window mode; the user can override either in Settings, and the override survives a theme switch. Designed in the shell-layout doc (§4.8). |
| D6 | **Themes bring wallpapers**, per **slot** (`desk`, `home`), which may differ; the user can override each slot. A theme ships static images and may name installed wallpaper carts (§4.9). |
| D7 | **The home is one layout on every board**: the cover carousel with tabs (Games / Mine / Apps / Make), themeable, with its own wallpaper slot. **The desk's PLAY opens this same home** (owner, 2026-09-27): the windowed tier has no separate games-only Library. |
| D8 | **Format 1 carries four more keys** (owner, 2026-10-11, before phase 1 froze it): **content colour roles** for code syntax and the block editor's block colours (§4.1); a **corner radius of 0 to 3** (§4.2); a **pressed-label offset** (§4.2); and a **text effect** per part and state, `none`, `shadow` or `emboss` with the role it paints in (§4.3). Their defaults reproduce today's pixels. The Paint, sprite, map and scene canvases stay exempt. |

The pictures for D1 and D7, with the shell as it rendered before, are in
`docs/media/theming_2026-09/` (its README says which are mockups).

Rejected in the same sessions, so they are not re-proposed: MOY64 grey chrome
replacing the PICO-8 base colours, cartridge-shell or notched library cards,
labels inside the desk tiles, and Pixel Operator replacing Tiny5 as the UI font.
Each was mocked and judged worse (§11).

## 2. What exists today

**SOURCE.** Theming is data in three layers; what it lacks is reach.

- **Theme packages:** the six families (`night`, `indigo`, `berry`, `forest`,
  `slate`, `machine`) are seed packages in format 1 (§3),
  `native/moy_ui/themes/*.json`, each with a dark and a light table, embedded
  in every image by `tools/gen_ui_tables.py`. `native/moy_ui/moy_theme.c` is
  the loader: it reads a theme.json with a strict reader of its own, walks
  `inherits` and fills every role a package leaves unset from the derivation
  rules in `native/moy_ui/style.json`'s `theme` section (§5).
  `runtime/chrome.py`'s `theme_colors(name, variant)` is its Python face;
  `runtime/appearance.py`'s `set_theme` installs a set and writes it into
  native/moy_app's live token table, whose generation moves on every write.
- **Widget style:** `native/moy_ui/style.json` (kind × state → a
  `(field, ink, edge)` triple of token specs, and each kind's metric slots),
  with the state ladder `disabled > pressed > hot > on > hover > rest` and the
  quirks it lists, resolved in C by `native/moy_ui` against the live token row
  (sprint 6). What a theme changes beyond colours is its shape (§4.2-§4.5),
  installed with the theme; the skin axis folded into it (phase 3 step 4), and
  `outline`, once the second skin, is a seed package.
- **Text:** one text call, `moy_text` (`native/moy_gfx/moy_text.h`), for every
  UI string: UTF-8, a font argument (one font today, petme128, id 0), `align`
  and `maxw`, and it returns the drawn width. Chrome text at every scale is
  `mg_text_raw` over the staged petme128 blob; a cart's `print` is libmoy's
  `moy_print`.
- **Icons:** a themeable 16×16 `IconSheet` (`runtime/editors_sheet.py`), baked
  from `chrome._ICON_ART`, or the user's own edited sheet, `system_icons.moygfx`
  (Settings → EDIT ICONS).
- **Wallpapers** are carts with `"moybyte": {"type": "wallpaper"}`; apps reach the backdrop
  through `ctx.wallpaper`.
- **Who reads tokens:** shipped apps through `ctx.theme` (`colors()`, `light()`,
  `name()`, `variant()`, `set()`, `set_variant()`;
  `runtime/app_context.py`). App carts (`"moybyte": {"type": "app"}`) through the ungated
  `theme()` global, which returns today's token dict (`runtime/system_api.py`;
  `system_carts/moybyte.notes.moy` reads `th["panel"]` and `th["ink_dim"]`).
  Compiled apps read the token row through their `theme` imports
  (`native/moy_app/roles.json`). **Games have no `theme()`**, and giving them
  one would be a cart-verb change in moy-spec's SPEC.md.
- **Persisted settings:** `theme`, `theme_variant` and `wallpaper` in
  `system.json`.

What stopped a Platinum or Win95 theme on 2026-10-11, which phases 2 to 4
answer:

1. **Most chrome is drawn by hand, in literal colours.** ESTIMATED (grep of
   `runtime/`, 2026-10-11): about 375 canvas `rect`/`print`/`line`/`circ` calls
   over 32 modules against about 80 toolkit calls over 18, and about 340
   `NAMES["..."]` colour literals over 19 modules. Most of the hand-drawn calls
   are a row's value text or a small mark inside a row a Layout class already
   places, not a widget the toolkit could draw whole. At 320×240 / font scale 1
   the shell takes frozen hand-rolled branches (`if not ws.layout._base`) kept
   for pixel parity (`docs/history/ui_refactor_2026-08.md`, "the frozen
   baseline"). Eleven `light_chrome()` sites branch on the light presentation
   in code; eight of them sit inside a `_base` branch.
2. **A skin can only colour and pad.** There is no frame style (raised, sunken,
   etched), no skinnable window decoration or bar. A 4×4 two-colour pattern fill
   does exist, `fillp` (`device/device_canvas.py`), but on a per-pixel lane (§4.4).
3. **One font.** `moy_text`'s font argument has one row, and layout
   arithmetic is written in multiples of the 8px cell (ESTIMATED: about 185
   `8 * fs` sites in `runtime/`, and more in other forms; phase 4 recounts
   before sizing).
4. **Layout is fixed per tier:** the bar, the taskbar and the window mode are code.

## 3. The package

A theme is a folder with `"moybyte": {"type": "theme"}` in its manifest (the
field is `type` in the manifest's `moybyte` object, as for wallpapers and apps;
`kind` is the Files document vocabulary).
It holds a `theme.json` and optional **data** assets: images, fonts, sprite and
icon sheets. It never holds a cart or a script.

**Format 1, as the loader reads it** (`native/moy_ui/moy_theme.h`; frozen
2026-10-11 by phase 1, `surfaces` and `frame` defined 2026-10-12 by phase 3):

```json
{
  "format": 1,
  "name": "dusk",
  "inherits": "berry",
  "colors": {
    "dark":  { "panel": 1, "title": 13, "title_ink": 7, "code_keyword": 12 },
    "light": { "panel": 6, "title": 48, "title_ink": 0, "block_events": 9 }
  },
  "metrics": { "radius": 2, "press_dx": 1, "press_dy": 1 },
  "frame": { "style": "platinum", "title_align": "center", "buttons": "C|T|ZM" },
  "text": {
    "button": { "disabled": { "effect": "emboss", "color": "ink" } }
  },
  "surfaces": {
    "button": { "rest": "bevel2, fill surface", "pressed": "sunken, fill shade_lo" },
    "window.title": { "active": "pinstripe shade_lo/shade_hi, border" }
  }
}
```

- **`format`** (an integer, required): a reader refuses a newer one. A key or
  a role a later phase defines is additive: an older reader ignores it, so it
  needs no bump. Renaming or removing a key, a role or a value's meaning bumps
  `format`. There is no migration pass, which is the store rule.
- **`name`** (required): 1 to 24 of `a-z`, `0-9`, `_`, `-`. A seed's name is
  reserved.
- **`inherits`**: one parent, by name (§5). A theme that names none inherits
  the default.
- **`colors.dark`, `colors.light`**: role → MOY64 index, or a flag role →
  `true`/`false` (§4.1). A role is a token of the live row or a content role.
  Every role is optional.
- **`metrics`**: `radius` (0-3), `press_dx` and `press_dy` (-3 to 3), each
  clamped to its range (§4.2); the default seed sets every one to 0.
- **`text`**: part → state → `{"effect": "none" | "shadow" | "emboss",
  "color": ROLE}` (§4.3); at most 32 per package. An effect other than `none`
  names its role, or it is dropped.
- **`surfaces`**: part → state → one line of the surface grammar (§4.4); at
  most 32 per package. Text the grammar does not take is dropped.
- **`frame`**: `style`, one of `classic`, `tab`, `platinum`, `win9x`, `flat`;
  `title_align`, one of `left`, `center`, `right`; `buttons`, each of `C`
  (close), `M` (minimise), `Z` (maximise) and `T` (the title) once, a `|`
  between them allowed. What a theme leaves out is its style's own (§4.5); a
  value the key does not take is dropped.
- **Reserved:** `fonts`, `sprites`, `icons`,
  `images`, `wallpapers`, `layout`, `sounds`, `editor` (style.json's
  `theme.reserved`) are accepted and not read yet. Each is defined, additively,
  by the phase that first reads it (§9).
- Any other key or role is ignored, a value of the wrong type is dropped and an
  index outside 0-63 is clamped, and each is noted (§7).

What the reserved keys will hold, as rev 2 sketched them (and the metrics
§4.2 lists beyond format 1's three, added to `metrics` additively):

```json
{
  "metrics":  { "border": 1, "title_h": 14, "button": 11, "pad": 4, "gap": 4, "shadow": 1 },
  "fonts":    { "ui": "ui.fnt", "title": "title.fnt" },
  "sprites":  "parts.moygfx",
  "icons":    "icons.moygfx",
  "images":   { "desk": "desk.moyimg", "home": "home.moyimg" },
  "wallpapers": { "desk": "platinum_desk" },
  "layout":   {},
  "sounds":   {},
  "editor":   {}
}
```

- **Variants are an axis, not two themes.** `colors.dark` and `colors.light`
  under one package keep D1's "one routine, two tables" and today's
  `variant()`/`set_variant()`. The `light_chrome()` branches become token
  differences (phases 2b and 3).
- **`images`** are static pictures the theme ships. **`wallpapers`** name
  installed wallpaper carts by id. An id that is not installed falls back to
  the image, then to the default (§4.9).
- **`layout`**, **`sounds`** and **`editor`** have no defined keys yet.
  `layout` is filled by the shell-layout doc; `sounds` waits for #141; `editor`
  is where code colour schemes go when they come
  (`docs/studio_2026-09.md` §12), since syntax colours are content (§4.3).

The sketched key names are illustrative until the phase that reads them
freezes them; the SHAPE is the decision.

## 4. The vocabulary — the public contract

The vocabulary is an API. Renaming a role or a part is a breaking change and
bumps `format`. That is the lesson of GTK 3, whose themes styled an internal
node tree that point releases then changed (§10).

### 4.1 Colour roles

**The roles are the token row's** (`native/moy_ui/style.json`'s `tokens`:
`panel`, `edge`, `title`, `title_ink`, `accent`, `hilite`, `dim` and the §4.3
semantic roles of `docs/visual_identity_v1.md`, then the roles phase 3 added
below, three of them the flags `surface_light`, `bar_light` and
`band_light`). They are the names `theme()`, `ctx.theme.colors()` and a
compiled app's `theme` imports answer; a role a phase adds is appended to the
row, so a compiled app's role ids do not move. Every value is a **MOY64
index**; per-theme RGB palettes are declined (§11).

A role a package leaves unset is **derived by a rule** (style.json's
`theme.derive`, §5): `surface` from `panel`, `focus` from `accent`, the inks
from fixed indices per variant, and so on, so a kid-made theme sets the seven
base roles or fewer. The readability check (§7) holds the **background/ink
pairs** listed in `theme.pairs`, which are pairs of today's roles.

**Content roles** (D8) are a second set in the same `colors` tables
(style.json's `theme.content`), outside the live token row, so the app ABI's
vocabulary does not move: `code_text`, `code_keyword`, `code_string`,
`code_number`, `code_comment` and `code_builtin` for the Code tab's syntax
classes, and `block_<category>` for each block category (`block_events`,
`block_control`, `block_actors`, `block_looks`, `block_draw`, `block_input`,
`block_variables`, `block_lists`, `block_operators`, `block_sound`,
`block_myblocks`). Their derivation gives today's colours: the syntax set per
variant (the dark classes' own colours, and the light set the Code tab drew on
a light surface), and the Scratch category colours in both. A seed whose dark
table has a light surface (`machine`) sets the light syntax set itself.
`runtime/code_layer.py` and `runtime/block_editor_ui.py` read them through
the look (`runtime/appearance.py`'s `content_colors`).

**Additive roles come with the phase that draws them**, under §3's rule, each
joining the token row (and native/moy_app's vocabulary, which
`tests/test_ui_style_data.py` holds to it) with a derivation rule, so every
existing theme keeps its pixels. Phase 3's painter added six (2026-10-12):

| role | what it is | derived from |
|---|---|---|
| `shade_hi`, `shade_lo`, `shade_dark` | the bevel shades a surface's edges draw in (§4.4) | `hilite` (white in the light variant), `dim`, black |
| `ink_accent` | emphasis text on the surface: the web console's pin | `accent`; `ink` in the light variant |
| `ink_note` | a note's name in the Music tab's step list | peach; brown in the light variant |
| `band_light` (a flag) | an in-window bar band is light, and its zone owners ink it dark | false; true in the light variant |

They are the three `light_chrome()` branches outside a `_base` branch turned
into tokens (`runtime/web_console_ui.py`, `runtime/music_editor_ui.py`,
`runtime/bar_layer.py`). `machine`, the seed whose dark table has a light
surface, sets the three itself, as it sets its syntax colours. An `_ink` for a
fill role that lacks one comes when a surface first paints text on it.

The six shipped families are seed packages in format 1 (§2). There is no
reader for the old settings beyond that: under the no-migrations rule a stored
name that no package provides reads as absent and resolves to the default, and
a stored `skin` is not read at all. `outline`, once the second skin, is a seed
package of surfaces over the default's colours (phase 3 step 4), and the skin
axis is gone: its rows, its verbs in the app ABI and its persisted setting.

### 4.2 Metrics

Logical pixels, multiplied by the chrome scale each tier already computes (the
#203 floor): `border`, `title_h`, `button`, `pad`, `gap`, `shadow`, `radius`
(0 to 3, D8: a corner cut from a precomputed mask in C, one per radius, so a
rounded box costs four masked corner spans and no per-pixel test), and the
pressed-label offset `press_dx`, `press_dy` (D8: the label of a part in its
`pressed` state shifts by that much, as Windows 95's buttons do). Format 1
defines `radius`, `press_dx` and `press_dy`, all 0 in the default seed.
Ruled 2026-10-12 with phase 3's painter, each the pick that builds least:
`radius` rounds the surfaces a theme paints (a part with no surface keeps
today's square frame), and is in pixels, unscaled, as the toolkit's rings are,
since a mask exists for each radius; the pressed offset is multiplied by the
primitive's font scale and moves the label and a toolkit glyph, not an icon
or glyph the caller draws itself. Never absolute screen positions: a theme that
places things by pixel works at one resolution only, which split Rockbox's
catalogue by screen size (§10). Moybyte runs from 320×240 to 1280×800.

### 4.3 Parts × states

**The part list is derived from the toolkit's tables, not invented**, and was
frozen on 2026-10-12 with the first package key that names parts
(`surfaces`). It is the kinds of `native/moy_ui/style.json`'s `kinds` and
`metrics`, in the tables' order: `row`, `cell`, `cell_band`, `chip`,
`toggle`, `row_menu`, `row_list`, `row_chrome`, `row_cta`, `button`,
`button_play`, `button_author`, `button_danger`, `tab`, `status`, `panel`,
`panel_title`, `toolbar`, `scrollbar`, `focus`, `text_field`, `game_btn`,
`game_icon_btn`, `mini_btn`, `dialog`; then the chrome the WMs and the bar
draw, style.json's `theme.parts`: `window`, `window.title`, `window.button`,
`bar`, `bar.item`, `desk`, `desk.icon`. A window's buttons are one part
(ruled 2026-10-12, the pick that builds least: the frame style tells them
apart, §4.5). `tests/test_theme_loader.py` holds this paragraph to the
tables. A part added later is additive (§3): the home's parts once the
shell-layout doc names them, and the Studio's pane parts (a pane tab with a
close, the panel list, a splitter, a drop zone, the completion popup:
`docs/studio_2026-09.md` §8), which join the style file as kinds.

**The quirks stay code** (ruled 2026-10-11, phase 1; style.json's `quirks`):
`game_btn` and `text_field` draw on the game canvas, which a theme does not
reach (§6), and `mini_btn`'s and `chip`'s unscaled label pads are revisited
by phase 4, when layout reads line metrics instead of the 8px cell.

States are the ladder native/moy_ui resolves (`rest`, `hover`, `on`, `hot`,
`pressed`, `disabled`), plus `active`/`inactive` for windows (style.json's
`theme.states`). A state a part does not name follows style.json's
`theme.chain` at draw: `pressed → hot → rest`, `inactive → active → rest`,
every other state to `rest`. In
the file, parts and states are **nested objects**, never a flattened
`part:state` string; at runtime they are the C look's per-kind, per-state
table (§5).

**What "chrome" means** (for the ratchet in phase 2a): the pixels the shell, the WMs
and the toolkit draw around content. Content is exempt: the Paint and sprite
canvases, the map and scene views, code syntax colours and the block editor's
block colours, which format 1 themes through the content roles (§4.1, D8).
The Paint and sprite canvases and the map and scene views stay exempt.

**Text effects** (D8). A part in a state may name a text effect: `none`,
`shadow` (the label drawn once more, offset one pixel down and right, in the
named role, under the label) or `emboss` (the same, offset up and left in one
role and down and right in the label's own colour: the etched disabled text of
Windows 9x and Platinum). The package key is `text`, part → state →
`{"effect", "color"}`, and the default is `none` everywhere, which is today's
drawing. Format 1 defines the key and the loader resolves it
(`moy_theme_shape`); the primitives draw it (phase 3), for the labels of the
parts that paint surfaces (§4.4). Ruled 2026-10-12: an emboss is three
draws, the effect's copy up and left, the label's colour down and right, then
the label; an effect draws only in a role of the token row, since a content
role is not in the row a look resolves against.

### 4.4 Surfaces: a one-line grammar, compiled once

A part's look is a short text: an **edge** (`flat`, `raised`, `sunken`,
`etched`, `bevel2` for Windows' two-level edge), a **fill** (`fill ROLE`,
`dither A/B`, `pinstripe A/B`, `gradient A/B` as dithered bands), an optional
`border` and `shadow`. This is how Openbox themes describe every bevel and
gradient in text, and how Windows drew all of Win95 from one `DrawEdge` routine
fed by a few shade colours (§10).

**The grammar** (frozen 2026-10-12 by phase 3; `native/moy_ui/moy_theme.c`'s
`moy_theme_surface`):

```text
surface := clause { "," clause }
clause  := EDGE | "fill" C | ("dither" | "pinstripe" | "gradient") C "/" C
         | "border" [C] | "shadow"
EDGE    := flat | raised | sunken | etched | bevel2
C       := a token of the live row, not a flag | a palette index 0-63
```

Words are lower case and split by spaces, `A/B` is one word, each clause comes
at most once, and a surface is at most `theme.surface_len` bytes. What a
surface leaves out is the part's own: no edge is `flat`, no fill is the
part's field, a bare `border` is the part's edge colour. An index over 63 is
clamped and noted; any other text the grammar does not take drops that
surface and is noted (§7). The package key is `surfaces`, part → state →
text, at most `theme.surfaces_cap` per package, resolved nearest package
first like the text effects (§5).

**How the painter draws one** (`native/moy_ui/moy_ui_paint.c`; ruled
2026-10-12, each the pick that builds least). Everything stays inside the
part's box. `shadow` takes the box's last two columns and rows in `shade_dark`
(D1's hard shadow, `MOY_UI_SHADOW`: the body offset two pixels down and
right), and the rest is the body. The fill goes under the corner mask of the theme's
`radius` (§4.2); then one-pixel outlines from the outside in: the `border`,
then the edge's levels, each with its top and left in one colour and its
bottom and right in another (square, the top-right and bottom-left corners
are the bottom-right colour's, as `DrawEdge`'s are; rounded, the two upper
arcs are the top-left colour's). `raised` is `shade_hi` over `shade_lo`,
`sunken` the reverse, `etched` sunken then raised, and `bevel2` an outer level
of the fill colour over `shade_dark` and an inner raised one, which is Win95's
button. `dither` is a checkerboard, `pinstripe` alternate rows, `gradient`
runs from A at the top to B at the bottom in bands of a 4×4 ordered dither,
one band per level. A pattern is anchored to the buffer as `fillp`'s is and
opaque. The parts that paint a surface in place of their field and edge are
the ones whose primitive draws a field: `row`, `cell` (its caption band and
label stay its own), `button` and its kinds, `chip`, `tab`, `toolbar`,
`panel` (in place of its outward border too) and `panel_title`; the
caller-coloured and game-canvas ones (the quirks above, `dialog`, `focus`,
`scrollbar`, `status`) do not. A chrome part (`window`, …) is painted through
the `ui` face, `ui.paint`, which paints nothing and answers False where the
theme gives the part no surface.

- **The text never reaches a draw.** The loader compiles every surface once
  into a small integer record (a style id, `stid`; not
  `docs/surface_model_v1.md`'s Surface `sid`) in a C table. A draw is **one C
  call through the toolkit's sink** (`docs/kernel_toolkit_2026-10.md` §3.1),
  the path every primitive already draws through: a primitive whose kind has a
  surface paints it in place of its field and edge, and the WMs' decorations
  reach it through one `ui` face. Colours stay MOY64 indices resolved through
  the canvas row's palette at draw, as every primitive's do.
- **Patterns get a fast lane.** `fillp`'s 4×4 two-colour screen-anchored
  pattern is drawn by libmoy's `moy_ds_span` (`libmoy/moy_pixel.h`). An
  **opaque** pattern (its holes take a colour) is periodic in four pixels, so
  `moy_pat_span` derives the four column-phase colours once a span and writes
  the run as 32-bit words with `moy_fill`'s head and tail handling: two
  alternating words on the 565 build, one on the index build. A pattern with
  untouched holes keeps the per-pixel walk. `rect`, `circ`, `tri` and `oval`
  all reach the lane through the span path. It lives upstream in moy-spec, is
  re-vendored, and is pinned by the `fillp` conformance scene (odd and even
  starts at widths 1-31, a circle and a triangle). The painter is built on
  `fillp`'s semantics over this lane. Its kernel cost against a solid fill, on
  the P4 and the T-Deck, is recorded in **#66** (2026-10-12).
- **Not the PPA.** Painter fills stay on the CPU: a 1:1 PSRAM fill is
  bandwidth-bound and CPU ≈ PPA (`.claude/rules/boards.md`). The PPA stays for
  upscale composites.
- **No flush pump.** One painter call can cover a large area, and the T-Deck's
  flush feeds itself (`native/moy_flush/moy_flush.c`'s feeder loop), so the
  painter pumps nothing, as the sink's primitives pump nothing.

### 4.5 Frame styles and window decoration

Window frames are a **built-in style chosen by name**: `classic` (D1's), `tab`
(a Haiku-style title tab as wide as its title), `platinum`, `win9x`, `flat`.
Parameters: title alignment and a button-order string (`C|T|MZ`: close left,
title, minimise and maximise right). The styles are our code; a theme only
chooses. SerenityOS and xfwm4 work this way (§10). On the fullscreen tiers the
"window" is the app's full-screen strip, so the same style draws that strip.

**As built** (`native/moy_ui/moy_ui_draw.c`'s `moy_ui_frame`, reached through
`ui.frame` from `runtime/wm_chrome.py`; phase 3 step 3, 2026-10-12). `classic`
is D1's (phase 3 step 4): a one-pixel ring and the strip's bottom line in
`shade_dark`, the hard black shadow, the app's identity stripe at the strip's
left with the title beside it, and a square close (its glyph boxed in the
title's ink). `flat` is the frame the WM drew in Python before D1, less the
strip's bottom line: a ring in `chrome_ink` (`dim` when inactive), no stripe,
the plain close glyph; `tab` keeps that ring and a red close. `win9x` lights the ring from the top
left, sets a title bar inside a frame of `panel`, and boxes each button as
Win95's `bevel2` with its glyph in `chrome_ink`. `platinum` centres the title,
pinstripes the active strip in `shade_lo` either side of it, and draws small
raised boxes with marks of their own (none for close, a square for maximise,
two bars for minimise). `tab` draws the strip in `panel` with a raised tab as
wide as its buttons and title. Each style has its own title alignment and
button order (classic, win9x and flat `T|MZC`, platinum `C|T|ZM`, tab
`C|T|MZ`), which `title_align` and `buttons` replace. A window's strip buttons
are laid out by the same C (`ui.frame_buttons`) the fat-finger hit-test reads,
each group resolving taps out to the window edge on its own side. The bar's
band (`ui.frame_band`) is the style's too: `win9x` lights its top row, the
others draw today's band, and a theme's `bar` surface replaces it. A
`window.title` or `window.button` surface a theme names is painted in place of
the style's own; the title takes `window.title`'s text effect; a taskbar chip
paints `bar.item` (focused `on`, minimised `disabled`).

Ruled 2026-10-12, each the pick that builds least:
- **One footprint for every style**: the window box's one-pixel ring, the
  `title_h` strip and the three-pixel drop shadow right of and below the box,
  which the WM's damage, its frozen chrome, its hit-tests and the content rect
  are built on. A style's geometry inside it is the style's code, so `metrics`
  keeps format 1's three; §4.2's `border`, `title_h`, `button`, `pad`, `gap` and
  `shadow` stay unread until a phase moves that footprint.
- **Whether the strip spans the window is the style's**, not a parameter: `tab`
  does not, and the strip beyond its tab is `panel`, the window's own, since a
  see-through strip would need the WM's damage to cover the desk under it.
- **The shadow is `shade_dark`** in every style but `classic`, which keeps its
  black. It stays the footprint's three pixels in `classic` too: D1's two-pixel
  hard shadow is the surfaces' (`shadow`, §4.4), and a window's two-pixel one
  would leave the footprint's third column to the desk under it (ruled
  2026-10-12).
- **The identity stripe's colour is the title's** (ruled 2026-10-12, the pick
  that builds least): no app declares a colour, so `classic` picks one of the
  six PICO-8 accents (blue, green, orange, pink, yellow, red) by a hash of the
  title's bytes; the stripe is `3 * fs` wide and the title moves right of it.
- **`window`'s own surface is not read**: the ring is the style's, and a
  surface under the content would be filled and then covered every chrome draw.

### 4.6 Sprites and icons (D3)

- **One sheet per theme at fixed cell sizes:** title buttons per state, checkbox
  and radio per state, a 9-slice frame per part that wants one, the cursor and a
  desktop tile. The fixed cells are what made Winamp's classic skins paintable in
  any paint program (§10). A part with a sprite draws the sprite; a part without
  one draws its surface.
- **Drawn in C, in one call each.** A 9-slice is one C call, not nine `spr`
  wrapper calls. Glyph sprites are 1-bit masks tinted by a role through a C
  mask-blit, with **no per-tint RGB bake cache**.
- **Icons:** a theme may ship an icon sheet. **The user's own edited sheet
  (`system_icons.moygfx`) is an override slot** that wins over the theme's, like
  the layout and wallpaper overrides (D5, D6).

### 4.7 Fonts

Up to three roles: `ui`, `title`, `mono`. A theme font is a bitmap font file.
The default ships Tiny5 (D1). Petme128 stays the carts' font, and the cart
`print` path is byte-identical, including the Lua C API that shares
`mg_text_raw`. The one text path for every app runtime is sprint 6's
`moy_text` (`docs/kernel_toolkit_2026-10.md` §4).

- **A theme font is a row behind `moy_text`'s font argument.** The call takes
  a `uint32` font id (petme128 is 0) at every scale; a theme font is another
  row there, never a second Python-side path. A chrome-side text lane was once
  reverted and later re-crossed on S3 A/B numbers (`moy_gfx_kernels.c`'s header
  records both), so each new lane is gated on an S3 A/B. A cart's `print` stays
  libmoy's `moy_print`.
- **Measuring is C.** `moy_text` takes `align` and `maxw` and returns the drawn
  width, and `moy_text_width` and `moy_text_wrap` measure in the same raster.
  What phase 4 adds is line metrics a layout reads in place of the 8px cell.
  Scaled text emits per-row runs through the span fill, not per-pixel stores.
- **The format is aligned.** Its header and tables are naturally aligned, and
  the parser assembles multi-byte fields from bytes or requires a `moybuf`
  (64-byte aligned) buffer. GCC on the S3 faults on an unaligned 16- or 32-bit
  load, which host and wasm do not reproduce, so phase 4 includes an on-glass
  load of a fuzzed font on an S3.
- **App carts.** `ui` is the real toolkit, handed to every `type: "app"` cart,
  and those carts lay out in 8px cells and `print` in petme128. A theme font
  therefore reaches app carts **only together with** a published `text_w()` and
  line metrics. Until then, app-cart `ui` stays on petme128.
- **Legibility.** Tiny5 at scale 1 on a 3.5" 480×320 panel is small. Themes get
  a text-size floor tied to #203's chrome floor, and phase 4 checks legibility on
  the T-Deck and the Guition S3 glass (`docs/visual_identity_v1.md` §5.4 asks
  for on-glass font tests).
- **Licence:** Tiny5 is OFL; phase 4 adds it to `THIRD_PARTY.md` and to the
  generated site webfont's notices.

### 4.8 Layout — reserved here, designed in the shell-layout doc

D5 and D7 are shell layout, not theming. What the shell-layout doc has to settle,
from rev 1's review:

- **A tier capability table.** `WindowedWM` is host/P4 only and not staged to
  the S3 (`.claude/rules/shell.md`), and there is no tiling WM. A theme value a
  tier cannot take resolves to that tier's default.
- **A content rectangle.** `bar_h()` is a public app-cart contract that assumes a
  top strip. A bottom taskbar or a dock needs `content_rect()`, with `bar_h()`
  kept as a derived shim.
- **The desk and the home (#105).** The desk's PLAY opens D7's home, the same
  one the fullscreen tiers boot to, replacing the windowed tier's games-only
  Library. What its Apps and Make tabs do on the desk, where apps are already
  desk icons and open as windows, is written out there.
- **Its own kernel mapping.** Bar presets, a dock and tiling grow the WMs, which
  stay Python (the kernel doc's §10 question 8: its sprint 7 does not run).

Format 1 reserves `layout` with no keys, so no theme written before that doc
lands is broken by it.

### 4.9 Images and wallpapers (D6)

A theme ships static **images** for the `desk` and `home` slots and may name
installed **wallpaper carts** for them. The user can override each slot.

- **Nothing executable rides in a theme.** Installing a cart is exactly what
  `system_api.py` never grants (`carts`, "the single most important entry"). A
  theme that names a cart does not install it: the cart comes from the store
  like any other, and an absent one falls back to the theme's image.
- **The default is static.** An animated wallpaper defeats the redraw gate and
  the home's retained stamp (`launcher_layer._try_stamp_retained` is skipped
  while `ws._animating(dt)`). That is the open live-wallpaper defect (#66, #73). So
  Moy's night, the default, is a still image, or animates only small dirty
  rects.
- **Readability at home.** A wallpaper with its own text or characters
  mid-screen fights the carousel (mocked 2026-09-27 with Space Desktop). Either
  the wallpaper declares itself `home_safe` (a new manifest flag, which phase 6
  lists with its side effects), or the home draws a dither scrim. A scrim is
  applied **at the wallpaper's source resolution, before the upscale blit**
  (`blit565_scale`), which is up to 16× fewer pixels than at 1280×800 (§12.1).
- **Prefer wallpapers that need no VM.** A live Python wallpaper keeps the
  Python VM up, against the kernel doc's memory goal (§4.4 there). Images and
  Lua or wasm wallpapers are preferred; a Python wallpaper stays allowed and is
  charged as such.

## 5. Fallback and inheritance, resolved once

Every key but `format` and `name` is optional. A theme resolves through
`inherits` (one parent; a theme that names none inherits the default) and ends
at the default seed, `night`, which sets every role nothing derives. The chain
is **capped at four links and a cycle is refused**, at install and at resolve
(style.json's `theme.depth`). Each role comes from the nearest package that
sets it; what is still unset is filled by `theme.derive`, the variant's own
rules first and then those of both variants, a rule giving a fixed index, a
flag or another role's value. So every role of the row is set, and a five-line
recolour is a valid theme from which a full Platinum grows, as Winamp skins
fell back to the base skin and KDE Plasma themes fall back to Breeze (§10). A
missing sprite draws the surface; a missing surface draws the default for that
part; a missing state follows §4.3's chain.

**All of it resolves at flatten, never at draw.** `moy_theme_resolve` turns a
(theme, variant) into the token row once; the look resolves the style
against that row lazily, per kind, into three palette indices per state, and
again only when the skin's or the row's generation moves
(`native/moy_ui/moy_ui.c`). A widget pays an array read. `theme_colors` caches
the row's dict per (theme, variant). The Appearance editor's previews use
colours only; they do not load other themes' assets, since the face's preview
ring holds four looks and would thrash.

## 6. Who reads it, and how a switch invalidates

- **The toolkit and the WMs** are the only drawing code that resolves parts and
  surfaces. Screens that draw chrome by hand move onto the toolkit as phase 3
  restyles them; a literal colour in chrome (§4.3's definition) is counted by
  a ratchet that only falls (phase 2a).
- **Shipped apps** read roles through `ctx.theme` and draw through the toolkit,
  so they restyle with every theme (D4).
- **App carts** read the live tokens through `theme()` (unchanged; §4.1 keeps its
  keys). **Games** do not get `theme()`: that would be a cart-verb change, and
  it goes to moy-spec as a proposal if it is ever wanted. The Player surface
  stays the cart's. That is the "don't theme our apps" lesson turned around: the
  chrome is fully themeable, game pixels are not.
- **A switch is one Class A dirty plus one integer.** A theme load, switch or
  in-place edit is an un-attributed Class A dirty (`ws._dirty`, the global
  epoch; `docs/surface_model_v1.md` §3) and bumps one monotonic integer,
  `look_gen`: native/moy_app's token-table generation, which every write of
  the live row already moves (sprint 5). Every cache that outlives a frame
  folds `look_gen` into its key:
  - the launcher statics (today keyed on `id(theme_colors)`);
  - the bar strip (today poked by `invalidate()`);
  - `wm_chrome`'s frozen chrome (today keyed on theme name and variant, so an
    in-place edit under the same name would go stale);
  - cover and `IconSheet` bakes;
  - the P4 backdrop;
  - the web's layer re-ship.

  **No cache keys on object identity.** A freed theme's `id()` can be reused by
  MicroPython, and identity keying is what `docs/surface_model_v1.md` §7 retired.
  The kernel ABI's theme handle carries the same generation (index plus
  generation, kernel doc §4.3). This is the existing dirty protocol, not a new
  mechanism, and `set_theme`'s per-cache pokes are deleted. A test edits a theme
  in place and asserts every cached surface repaints.

  **The fold lands in phase 6**, with the first in-place edit (the theme
  editor) and the first re-install under one name. Until then a (theme,
  variant) names one row for the life of the image, `theme_colors` hands out
  one dict per pair and never frees it, so today's keys cannot go stale and no
  `id()` is reused.

## 7. Safety

Kids will trade themes, so a theme is untrusted input:

- **Data only.** Parse, validate and clamp (`native/moy_ui/moy_theme.c`, a
  strict JSON reader of its own, built on every tier): a theme.json over
  `theme.bytes` (4096) is refused, as is malformed JSON, nesting past eight
  levels, a missing, non-integer or newer `format`, a missing or ill-formed
  `name`, a seed's name, or an `inherits` that names no package, cycles or
  runs past the cap. An index is clamped to 0-63, a value of the wrong type is
  dropped and an unknown key or role is ignored, each noted to the caller.
  A metric is clamped to its range, a text effect with an unknown part,
  state or effect name, or without its role, is ignored or dropped and noted,
  and a package holds at most 32 effects; a surface the grammar does not take
  (§4.4) is dropped and noted, and a package holds at most 32 surfaces. No scripts, no
  carts inside, no plug-ins (§10: GTK 2 engines, Haiku decorators, and a KDE
  store theme that deleted a user's files). `tests/test_theme_loader.py`
  fuzzes the reader and holds the boards' module to the host's answers.
- **Readability check** at load and in the editor. Every pair of `theme.pairs`
  must clear `theme.contrast` (a WCAG ratio of 3.0, the floor for large text
  and UI parts) on the MOY64 table; a failing pair sets its bit in the report
  and never refuses the theme. For a dither or pinstripe fill the ink is
  checked against **both** colours, in the look, since the check needs each
  part's ink, which the style resolves there (ruled 2026-10-12):
  `moy_ui_pattern_unreadable` (`ui.pattern_unreadable`) answers the installed
  shape's pattern surfaces whose label ink misses the floor against either
  colour, a kind's ink as the style resolves it and the chrome parts' as the
  frame and the bar draw them; a part that paints no label is not checked.
  Its reader, as the pairs' report's, is the editor, which offers the nearest
  passing index (phase 6). Every seed passes both checks in both variants
  (`tests/test_theme_loader.py`).
- **Persist only after it has rendered once.** A theme that fails to draw is never
  saved, so it cannot return after a reboot, as a malformed SerenityOS theme did
  (§10).
- **A way out that every board has.** Repeated failure is `crash_guard`'s strike
  ledger (the kernel doc's sprint 2), which falls back to the default theme. A
  per-board reset key or gesture is declared in `board.toml`, because a
  "boot-time hold" has no mechanism on the touch-only boards.
- **Two themes are firmware, not files.** The default and a high-contrast theme
  are frozen into the image under reserved ids that no user theme can take.
  Every seed is firmware already (§2); the high-contrast seed joins them in
  phase 6.

Persist-after-render, the strike ledger and the reset key arrive in phase 6
with the first theme a user can install from outside the image; until then
every theme a console can select is a seed.

## 8. Placement against the native kernel

Sprint 6 of `docs/native_kernel_2026-09.md` put the toolkit's bodies in C with
style as data (`docs/kernel_toolkit_2026-10.md`); the WMs stay Python (its §10
question 8: sprint 7 does not run); sprint 5 turned the app roles into an
import-shaped ABI with a live token table and its generation. **Theming runs
after sprint 6, written in C against the toolkit, with no Python reference
built first** (owner, 2026-10-11). The pieces:

| theming piece | built where | why |
|---|---|---|
| the package format and the loader: validate, clamp, inherit, derive, readability (§3-§5, §7) | **C**, `native/moy_ui/moy_theme.c`, with its rules and seeds as style.json data (phase 1) | the toolkit's look is C; the loader writes the token row the look resolves against, and the boards, the host and the browser run the same reader |
| the default look (D1) as seed packages and style data | data | data crosses unchanged |
| toolkit reach (phases 2 and 3) | Python surfaces calling the C toolkit | the apps and the WMs stay Python; a screen on the toolkit draws through its C primitives |
| the surface painter and pattern lane, 9-slice and mask blits | **C**, `native/moy_ui` over `moy_gfx` and upstream libmoy (phase 3) | draw kernels are C; "a hot loop lives on one side" (kernel doc §4.1). The painter is an op of the toolkit's sink |
| frame styles | **C** beside the primitives, reached from the WMs' decoration path (phase 3) | a style is our code, a theme only names it (§4.5) |
| fonts | **C**, a row behind `moy_text`'s font argument (phase 4) | the one text call exists; a font is data behind it |
| theme assets (fonts, sheets, images) | loader-owned buffers in PSRAM (`MALLOC_CAP_SPIRAM`), each a BUF row of the kernel's handle table with a role, as every off-heap buffer is (`docs/kernel_survival_2026-10.md`) | no second handle mechanism |

**Memory rule.** On the S3 boards, PSRAM outside the Python heap is the
cart-runtime reserve, and the biggest carts need most of it
(`.claude/rules/boards.md`). So: **one loaded theme at a time** (the loader
holds the seeds as flash constants and one installed package beside them),
a hard byte cap (a theme.json is capped at 4096 bytes; the cap on a package's
assets is set by the phase that first loads one), no per-tint bakes (§4.6),
and every phase takes the kernel doc's internal-SRAM gate (its §4.6). Phase 1
allocates nothing at boot: the installed slot is allocated on the first
install.

**Timing against the kernel's golden gates.** The kernel's sprint 6 gate held
the toolkit rows' goldens byte-for-byte, and every theming phase starts after
it (owner, 2026-10-11); sprint 7 does not run, so nothing waits on it. After
phase 4 a new theme is data and moves only its own goldens. The Studio
(`docs/studio_2026-09.md` §8) moves pixels too and takes the same rule; it is
not scheduled, so phase 2b re-baselines the T-Deck's dark row without it and the
Studio's T-Deck bar makes its own named re-baseline when it lands.

## 9. Phases

Each phase deletes what it replaces and passes `tools/preflight.sh`. **Pixels
move only in the phases that restyle on purpose (2b, 3 and 4)**, each
re-baselining once and naming what moved, with before/after renders for the
owner. Every other step holds the goldens byte-identical. Each step below is
one commit, gated at its end by `make test`, `tools/check_docs.py`,
`tools/preflight.sh --web` and a board pass on the T-Deck and the Waveshare P4
(`tools/board.py pass tdeck p4`), and records each board's image headroom
from the build. A step that changes no file a board image carries
(`runtime/`, `device/`, `native/`, `system_carts/`, `firmware/`) skips the
pass and says so.

**How performance is gated** (from `docs/history/ui_refactor_2026-08.md` §7):
per-cart fps is the *control*; a UI change must not move it at all. The
instrument is `tools/p4_bench.py`'s painted-frame medians and p90s per surface
(≥2 ms is signal, ≥3 ms needs an explanation), and every `* idle` scenario must
capture zero frames. Its desk scenarios are the P4's; on a fullscreen board
its `--repaint` mode times a surface painted every frame. In CI, per-surface
budgets per full draw (phase 2a): `canvas.gate_counts()` fills and texts, which
are real on the host since sprint 6. Each phase names which meter sees its
cost: DRAW2 and `gate_counts` for fill and text time, PERFCNT for instructions
versus memory, `moy_prof` for the whole image. Per-board verdicts do not
transfer, so every gate names its boards, and a gate on a board this desk does
not have (the Guition S3) is recorded as not run, never as passed.

**Phase 1 — the spec and the loader** (one step; landed 2026-10-11). Two
golden configs added first and captured on the unchanged code: cs 2 over fs 1
(the P4 as shipped) and light on the windowed tier. Format 1 frozen (§3); the
loader in C (§7); the six families as seed packages, which delete
`chrome.THEMES`, `THEME_LIGHT` and the derivation tables they replace; D8's
content roles (read by the Code and Blocks tabs, deleting `_HL_LIGHT` and
`blocks.CATEGORY_COLOR`), metrics and text effects; the quirks ruled on (§4.3).
Gate: every seed flattens to the row the Python
tables gave, in both variants (the goldens hold it: byte-identical on all
eight configs); load → flatten → dump round-trips; the reader fuzzed, and the
boards' module answers the fuzz corpus as the host's does
(`tests/test_theme_loader.py`).

**Phase 2a — the nets** (one step; goldens byte-identical). Rev 2 had this
phase move every hand-drawn chrome surface onto the toolkit on the non-`_base`
configs. Read against sprint 6's code (§2), most hand-drawn calls are a row's
value text or a mark inside a row a Layout already places, which no primitive
draws whole, so moving them byte-identically means a kind per surface for
pixels phase 3 restyles anyway. They move in phase 3 instead, under its
re-baseline. The three `light_chrome()` branches outside a `_base` branch
become roles in phase 3, with the shade roles, so the token row grows once;
the eight inside one are deleted by 2b. `look_gen` moves to phase 6 (§6).
What stays is the two nets the restyling phases are gated by:
  1. **The chrome-literal ratchet.** Per `runtime/` module, the count of
     `NAMES["..."]` colour literals and `light_chrome()` / `is_light()`
     branches, pinned (`tests/test_chrome_literals.py`); a module's count may
     fall, never rise.
  2. **`gate_counts` budgets.** Every golden surface's fills and texts in its
     settled frame, pinned per config beside the hashes
     (`tests/shell_goldens/counts.json`); a moved budget fails by name, and a
     dearer one needs a reason before it is re-pinned.

**Phase 2b — the T-Deck row's colours** (one step; one named re-baseline;
landed 2026-10-11). First `docs/surface_model_v1.md` §5.1 is amended: its
"executed path unchanged" is the presentation's contract, not a freeze on the
shell's pixels. Then the 320×240 tier's editors (Blocks, Code, Sprites, Map,
Scene, Music, Make it mine) draw in the theme's tokens in the dark variant as
they already did in the light one: the `light = (not lay._base) or …` and
`_base and not light_chrome()` branches and their frozen dark literals are
deleted, so a theme recolours the T-Deck's editors as it does every other
tier's. The dark 320×240 golden row moves (its seven editor surfaces); the
light row and every other config hold.

Rev 2 had 2b retire every `_base` branch, the geometry with the colours. It
was built, rendered and **reverted the same day** (2026-10-11): the responsive
formulas were never designed at 320×240, and running them there dropped the
Map, Scene and Music tabs off the Editor's bar (the labelled tab row needs
about 166px of a 202px zone that also holds PROJECTS and PLAY) and shrank the
Sprites grid from 144px to 96px to clear tool rows that overlap the button
column anyway. Fixing that is a T-Deck layout per surface, which is design,
not reach; it belongs to phase 3's D1 step, which redraws those surfaces
against the 320×240 mockups and re-baselines the row once. So the `_base`
geometry, the Editor's icon ladder at 320×240 and the toolkit-free Settings
scrollbar stay until then, and "the 320×240 row does not exercise the
toolkit" stays true for layout.

Gate: one re-baseline of the dark 320×240 row, named and rendered;
`tools/p4_bench.py --board tdeck --repaint` on Settings, Files and the seven
Editor tabs within the thresholds against the image before; the ratchet's
count falls.

**Phase 3 — shapes** (four steps, one re-baseline).
  1. **The pattern lane, upstream** (landed 2026-10-12). An opaque two-colour pattern fill in
     libmoy (moy-spec), re-vendored; conformance scenes for it. Gate: a pattern
     fill within a set margin of a solid fill of the same area, on the P4 and
     the T-Deck; goldens byte-identical (nothing draws it yet).
  2. **The painter and `surfaces`** (landed 2026-10-12). The surface grammar frozen and compiled by
     the loader into `stid` records (§4.4); the painter as a sink op; the
     shade roles and the roles the three `light_chrome()` branches need join
     the row with derivation rules; the part list frozen and tested against
     the style file; the corner masks for `radius` 1-3, the pressed-label
     offset and the text effects drawn by the primitives (D8). Gate: goldens
     byte-identical (no seed sets `surfaces`); painter µs per call on the P4
     and the T-Deck; the painter scenes of `tests/test_ui_painter.py` painted
     on every console board and read back equal to the host's
     (`tests/on_glass.py`'s `painter_matches_host`). Ruled 2026-10-12:
     `tools/p4_conformance.py` captures a cart's game canvas, which the
     painter, being chrome, never reaches.
  3. **Frame styles** (landed 2026-10-12). `classic`, `tab`, `platinum`,
     `win9x`, `flat` in C, reached from `runtime/wm_chrome.py`'s decoration
     path and the bar; the `frame` key defined, `metrics` kept at format 1's
     three (§4.5). Gate: goldens byte-identical (`classic` is today's frame);
     a Platinum and a Win95 seed (`platinum`, `win95`) render every golden
     surface with no code of their own, pinned as goldens of their own (the
     P4 as shipped, light); the chrome-literal ratchet falls; `gate_counts`
     budgets hold on every existing config; p4_bench on the P4 and the
     T-Deck.
  4. **D1 lands** (landed 2026-10-12). D1's tokens and shapes become the
     default seed, the hand-drawn surfaces it restyles move onto the toolkit
     as it does, the `_base` geometry retires as each surface gets its
     320×240 layout (2b's reverted half: the Editor's bar against the Studio's
     T-Deck mockup, the Sprites tab's grid and tool rows), and `outline`
     becomes a seed package (the skin axis folds into the theme). The
     readability check learns the dither and pinstripe fills (§7). The Code
     tab's `code_comment` on its 320×240 background is fixed here (2b left
     grey on blue; owner, 2026-10-12).
     Gate: **one re-baseline, named and rendered** against the mockups in
     `docs/media/theming_2026-09/`; p4_bench on the P4 and the T-Deck; the
     ratchet's count falls.
     As built: `night` carries D1 as data, one surface set over its two
     colour tables: black (`shade_dark`) outlines and the two-pixel hard
     shadow on buttons, chips and panels, the desk's icon tiles
     (`desk.icon`) and their labels in a pill the label's width (`desk`, in
     the bar's colours); calm chrome in the light table (cream panels, grey
     buttons and inactive strips, black outlines, a blue accent); the
     `classic` frame is D1's (§4.5). A theme that names none of these
     inherits them, as every seed does but `platinum` and `win95` where they
     name their own. Every `*Layout` lays the T-Deck out through the formulas
     every tier runs, and the formulas that did not fit it were fixed: the
     Sprites tab puts its tool rows under the grid and the palette, clear of
     the button column, whenever that leaves a bigger grid (144px at
     320×240), and stacks GET, PUT and FILE tighter where CLOSE is close; the
     Editor's bar takes the narrow form where its labelled row would drop a
     tab: one icon a tab, the open one framed in the tab's `on` field, then
     UNDO, REDO and PLAY. The dark `code_comment` derives to periwinkle (22).
     The skin's rows, its verbs in the app ABI (`theme.skin`,
     `theme.set_skin`) and its persisted setting are deleted; a compiled
     app's library of its own (the host's) installs the theme's shape by name
     where it installed the skin.
     Ruled 2026-10-12, each the pick that builds least:
       - **The open tab's name is not on the 320×240 bar.** The mockup fits it
         by dropping the bar's ≡, wifi and battery, an OS-zone change this
         step does not make; beside them and UNDO/REDO, 26px are left, so the
         open tab is told by its frame.
       - **`outline` draws one-pixel rings.** A surface's border is one pixel
         (§4.4), so the skin's two-pixel rings, its unfilled rows and its
         left-aligned labels are not kept; its rings are `dim` at rest and
         `focus` on hover, its panels `accent`.
       - **The Calc display stays an LCD**, black with green digits in every
         theme: a theme's darkest shade is grey in `platinum`.
       - **Tiny5 and Moy's night stay phase 4's and the wallpaper's**: the
         chrome keeps petme128, and the default wallpaper is the one already
         shipped.

**Phase 4 — type** (three steps, one re-baseline).
  1. **The font format and its lane.** The aligned bitmap format (§4.7), read
     by the loader into a buffer it owns; a font row behind `moy_text`'s font
     argument; the `fonts` key defined. Gate: goldens byte-identical (no seed
     names a font); the cart `print` path and the Lua C API byte-identical; an
     S3 A/B of the lane on the T-Deck; a fuzzed-font load on T-Deck glass.
  2. **Layout reads line metrics.** The `8 * fs` arithmetic reads the font's
     line metrics, and `mini_btn`'s and `chip`'s quirks are re-ruled (§4.3).
     Gate: goldens byte-identical (petme128's metrics are the 8px cell).
  3. **Tiny5 lands** as the UI font, with #135's glyph coverage and the OFL
     notices. Gate: **one re-baseline, named and rendered**; `moy_prof` on the
     T-Deck home and Settings; legibility on T-Deck glass (and on the Guition
     S3's when it is on a desk).

| phase | builds | gate |
|---|---|---|
| **5 — layout** | per the shell-layout doc (§4.8) | that doc's gates, plus: bar and dock strips keep their cache (idle frames zero, strip rebuilt only on a key change) |
| **6 — sprites and sharing** | the sprite sheet (D3); theme packages in the store; the Appearance app as the theme editor; a one-line text form for colour-only themes; `look_gen` in every cache key (§6); persist-after-render, the strike ledger and a reset key per board (§7); the high-contrast seed; `home_safe` (§12) and `"type": "theme"` with their side effects (the RUN grid and Editor picker exclusions, other moy hosts, the Zero's store sync) | a kid-made theme round-trips through the editor, the store and a second board; a sprite part no slower than its surface fallback; an in-place edit repaints every cached surface; idle home captures zero frames on all four console boards |

Phases 5 and 6 are planned as steps when they start.

## 10. What other systems taught (research, 2026-09-27)

About eighteen systems were studied; the lessons that shaped this doc:

- **Data themes spread; code themes die.** Winamp's classic skins (fixed sprite
  sheets, a base-skin fallback) and Kaleidoscope's bitmap schemes gathered tens
  of thousands of community themes. GTK 2 engines, Haiku decorators and Windows XP
  visual styles (signed, patched around) did not, and a KDE store "global theme"
  ran a script that deleted a user's files.
  <https://winampskins.neocities.org/base.html> ·
  <https://blog.davidedmundson.co.uk/blog/kde-store-content/>
- **Bevels are a routine, not art.** Windows' `DrawEdge` and Openbox's
  `raised bevel2 gradient vertical` draw every classic bevel from a few shades.
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawedge> ·
  <http://openbox.org/help/Themes>
- **A closed parts × states list is the stable contract.** Qt's `QStyle`, Windows'
  class/part/state and Mac OS 8's Appearance Manager brushes; GTK 3's CSS over
  internal nodes broke on point releases.
  <https://doc.qt.io/qt-6/qstyle.html> ·
  <https://blog.gtk.org/2019/01/21/theme-changes-revisited/>
- **Pair every background with its text colour** (Windows `HIGHLIGHT` /
  `HIGHLIGHTTEXT`, Material's `primary` / `onPrimary`, KDE's colour sets).
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsyscolor>
- **Fallback chains make small themes valid** (KDE to Breeze, Openbox's state
  chain, the freedesktop icon theme's `Inherits`).
  <https://develop.kde.org/docs/plasma/theme/>
- **Absolute coordinates split a catalogue by resolution** (Rockbox needed a
  rescaler). <https://github.com/TmosDHD/Rockbox-Theme-Rescaler>
- **Validate before persisting** (a malformed SerenityOS theme survived reboots).
  <https://github.com/SerenityOS/serenity/issues/4534>
- **Colours as palette indices, shared as a string** (Picotron's `.theme`, one
  base64 line on its forum). <https://www.lexaloffle.com/bbs/?pid=144631>

## 11. Declined

- **Code in themes** in any form (scripts, carts inside a theme, plug-in
  decorators, callbacks) — §7.
- **Per-theme RGB palettes**, for now: MOY64 subsets cover the target looks, and
  on the shared-canvas tier the shell and the game are one raster.
- **Absolute placement** in themes — §4.2.
- **Theming games' pixels**, and `theme()` for games without a moy-spec proposal — §6.
- **Identity-keyed theme caches** — §6.
- **Painter fills on the PPA** — §4.4.
- From the look sessions (D1): MOY64 grey chrome instead of the PICO-8 base
  colours, cartridge-shell or notched cards, labels inside the desk tiles, and
  Pixel Operator replacing Tiny5.

## 12. Questions, ruled

Rev 2 left four questions open. Each is ruled (2026-10-11) with the pick that
builds least, and the owner accepted all four on 2026-10-12, with §9's
sequencing picks: the 320×240 layout code retires in phase 3 step 4, toolkit
reach for hand-drawn rows waits for phase 3 and `look_gen` for phase 6, and
`outline` stays a skin until phase 3 step 4.

1. **Home readability: `home_safe` or a scrim (§4.9)?** Deferred to phase 6,
   where the home's wallpaper slot and `"type": "theme"` land, and decided
   there against the shell-layout doc's home. Until then the default home
   wallpaper is a still image that is readable by construction.
2. **Sprites in phase 3 or phase 6 (D3)?** Phase 6. Phase 3's parts draw
   surfaces; a sprite part then only replaces a surface, so nothing in phases
   3-5 depends on it, and format 1 already reserves `sprites`.
3. **A generator** (a theme derived from a wallpaper or one picked colour)?
   No generator. The derivation rules (§5) already make a few roles a whole
   theme, and phase 6's one-line colour-only form covers the rest.
4. **Theme sounds: with #141 or earlier?** With #141. `sounds` stays reserved.

## 13. Sentences this doc will make false

Each is corrected in place by the phase that falsifies it:

| sentence | in | fixed by |
|---|---|---|
| "Petme128 remains the canonical runtime glyph source for v1" | `docs/visual_identity_v1.md` §5.4 | phase 4 (carts keep it; the chrome moves) |
| `bar_h()` as the whole content contract | `docs/app_api_v1.md`, `runtime/system_api.py` | the shell-layout doc |
| the windowed tier's games-only Library (#105) | `docs/shell_ux_v1.md` | the shell-layout doc: the desk's PLAY opens D7's home |

## 14. Review ledger

The two passes of 2026-09-27: **A**, the architecture review (STANDS WITH
FIXES, close to REWORK), and **P**, the performance and hardware review (PERF
CASE STANDS WITH FIXES).

| # | finding | ruling | where it landed |
|---|---|---|---|
| A1 | a theme that bundles wallpaper carts carries code; installing carts is never granted | accepted | §0, §3, §4.9: static images inside; wallpaper carts named by id, installed separately |
| A2, P6 | "everything keys on the theme object" is identity keying; `id()` reuse; today's keys are three different mechanisms | accepted | §6: a Class A dirty plus a monotonic `look_gen` in every cache key; no identity keys; an edit-in-place test |
| A3, P1 | phase 2 re-baselined every golden during the largest refactor, and gated on per-cart fps, the control | accepted | §9: 2a golden-identical; 2b retires `_base` with one named re-baseline; p4_bench and `gate_counts` budgets |
| A4 | D1 needs Tiny5 before phase 4 builds fonts | accepted | D1 split: shapes and tokens in phase 3, Tiny5 in phase 4, one named re-baseline each |
| A5, P9 | chrome text at scale 1 is libmoy's compiled-in petme128; a second font on the Python path pays the wrapper cost | accepted | §4.7: a chrome text lane at every scale, the font as a draw-context handle, S3 A/B gate, OFL notice |
| A6 | `ui` is public to app carts, which lay out in 8px cells | accepted | §4.7: app carts get the theme font only with `text_w()` and line metrics |
| A7 | `theme()` is an app-cart global, not a cart verb | accepted | §2, §6: app carts only; games need a moy-spec proposal |
| A8 | the manifest field is `type`, not `kind`; a new type has side effects | accepted | §3, phase 6 |
| A9 | invented role names; `theme()` and `ctx.theme` API; persisted keys; no variant axis; `light_chrome()` branches | accepted | §3 variant axis; §4.1 today's keys are the base vocabulary; seed packages; strict reading; phase 2a folds `light_chrome()` |
| A10 | layout presets against tiers, `bar_h()`, no tiling WM | accepted | §0 scope and §4.8: layout moves to its own shell-layout doc with a tier table and `content_rect()` |
| A11 | D7 against #105 | accepted; the owner settled it (the desk's PLAY opens the same home) | §1 D7, §4.8, §13 |
| A12 | only D1 fenced against the kernel sprints; `skin.py unchanged` unlisted; assets as handles before handle tables exist | accepted | §8: no pixel-moving phase overlaps sprints 6–7; loader-owned `moybuf` buffers until sprint 2; §13 row |
| A13 | the part list did not match the tables; quirks; editor content colours | accepted | §4.3: derived from the tables, quirks ruled in phase 1, "chrome" defined |
| A14 | SOURCE counts; missing golden configs | accepted | §2 counts; phase 1 adds the cs2/fs1 and light-windowed configs |
| A15 | a theme's icons would override the user's edited sheet | accepted | §4.6: the user's sheet is an override slot |
| A16, P13 | boot-hold has no mechanism on touch boards; contrast against patterns; reserved ids; legibility | accepted | §7 and §4.7; P13's flush pump in §4.4 |
| A17 | `sounds` undefined; no format-version rule | accepted | §3: `sounds` and `layout` reserved; the reader rule |
| P2 | why `_base` exists (pixel parity) and the widget-call cost on the T-Deck | accepted | phase 2b: measure widget calls warm and fragmented on an S3; §5.1 amendment |
| P3 | dither and pinstripe exist in C (`fillp`) but per pixel; `_shape` is a Python wrapper | accepted | §4.4: the painter on `fillp` semantics, a pattern lane upstream, one gated `surface()` verb |
| P4 | surface text must never reach a draw; flattened keys allocate | accepted | §4.3 nested tables; §4.4 compiled `stid` records |
| P5 | fallback per draw; inherits cycles; preview thrash | accepted | §5: resolved at flatten, depth cap and cycle rejection, colour-only previews |
| P7 | the default home as the live-wallpaper defect; scrim cost | accepted | §4.9: a static default, the scrim at source resolution, idle-home gate |
| P8 | carousel covers on every board | accepted, deferred | the home's structure is the shell-layout doc's (§4.8); its rules carry: one cached base size per cover, an integer upscale at draw, no per-size variants, a T-Deck carousel scenario in p4_bench |
| P10 | GCC on the S3 faults on unaligned loads | accepted | §4.7: an aligned format, byte assembly or `moybuf`, a fuzzed-font load on S3 glass |
| P11 | `malloc_dma` has no free; the PSRAM reserve; bakes | accepted | §8 memory rule; §4.6 no per-tint bakes, one-call 9-slice |
| P12 | phases 3–6 had no perf gate | accepted | §9 gates per phase with named boards and meters |
| P14 | scaled text writes one pixel at a time | accepted | §4.7: per-row runs |
| P15 | flash and web bundle | accepted | §9: image headroom per board every phase, P4 first |
| P16 | not the PPA | accepted | §4.4, §11 |
| P17 | assertions without evidence; the `fs*N` count | accepted | §4.4 and §8 marked PREDICTED with gates; §2 recount (131 `8 * fs` sites) |
