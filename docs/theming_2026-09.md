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
| D5 | **Layout: both.** A theme sets the default bar layout (its `layout`'s bars, §3); the user can override it in Settings, and the override survives a theme switch. A window's mode is its own button, not a theme's (`docs/shell_layout_2026-10.md` §15, owner 2026-10-13). Designed in the shell-layout doc (§4.8). |
| D6 | **Themes bring wallpapers**: one wallpaper for the desk and the home (owner, 2026-10-14, replacing the per-slot wallpapers of 2026-09-27: a screen-sized bake per slot is memory a board does not have, and the home already shows the desk's wallpaper behind its cards). **The wallpaper is the theme's** (owner, 2026-10-14, the modern-Windows rule, replacing 2026-09-27's separate user override): picking a theme brings its wallpaper, and the user's pick is a change to the theme like any other, landing in `custom` and travelling with it; there is no persisted `wallpaper` setting (phase 6 step 5). A theme ships a static image and may name an installed wallpaper cart (§4.9). |
| D7 | **The home is one component on every board**: tabs (Games / Mine / Apps / Make) over the carts, themeable, over the one wallpaper (D6), in an arrangement a theme or the user picks (the layout's `home`: the cover carousel by default, the grid, or the icon grid; owner, 2026-10-14, `docs/shell_layout_2026-10.md` §12). **The desk's PLAY opens this same home** (owner, 2026-09-27): the windowed tier has no separate games-only Library. |
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
  UI string: UTF-8, a font argument (a row: petme128 is id 0, a loaded font
  file another, §4.7), `align` and `maxw`, and it returns the drawn width.
  Chrome text at every scale is `mg_text_raw` drawing petme128's row; a cart's
  `print` is libmoy's `moy_print`.
- **Icons:** a themeable 16×16 `IconSheet` (`runtime/editors_sheet.py`), baked
  from `chrome._ICON_ART`, or the theme's own (`icons`, §4.6), or the user's
  own edited sheet, `system_icons.moygfx` (Settings → EDIT ICONS), which wins.
- **Wallpapers** are carts with `"moybyte": {"type": "wallpaper"}`, or the
  theme's picture; which one the desk shows is the theme's (D6, §4.9), and
  apps reach the backdrop through `ctx.wallpaper`.
- **Who reads tokens:** shipped apps through `ctx.theme` (`colors()`, `light()`,
  `name()`, `variant()`, `set()`, `set_variant()`;
  `runtime/app_context.py`). App carts (`"moybyte": {"type": "app"}`) through the ungated
  `theme()` global, which returns today's token dict (`runtime/system_api.py`;
  `system_carts/moybyte.notes.moy` reads `th["panel"]` and `th["ink_dim"]`).
  Compiled apps read the token row through their `theme` imports
  (`native/moy_app/roles.json`). **Games have no `theme()`**, and giving them
  one would be a cart-verb change in moy-spec's SPEC.md.
- **Persisted settings:** `theme` and `theme_variant` in `system.json`; the
  wallpaper is the theme's (D6), with no row of its own since phase 6 step 5.

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
2026-10-11 by phase 1, `surfaces` and `frame` defined 2026-10-12 by phase 3,
`fonts` 2026-10-13 by phase 4, `layout` 2026-10-13 by phase 5's step 4):

```json
{
  "format": 1,
  "name": "dusk",
  "colors": {
    "dark":  { "panel": 1, "title": 13, "title_ink": 7, "code_keyword": 12 },
    "light": { "panel": 6, "title": 48, "title_ink": 0, "block_events": 9 }
  },
  "metrics": { "radius": 2, "press_dx": 1, "press_dy": 1 },
  "frame": { "style": "platinum", "title_align": "center", "buttons": "C|T|ZM" },
  "fonts": { "ui": "ui.fnt", "title": "title.fnt" },
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
- **`name`** (required): 1 to 24 of `a-z`, `0-9`, `_`, `-`, starting with a
  letter or a digit (the carts repository's theme ids, so a theme made on a
  console can be published). A seed's name is reserved.
- **`inherits`**: one parent, by name (§5). A theme that names none inherits
  the default. A seed's key: a user package is a standalone copy and carries
  none (owner, 2026-10-14).
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
- **`fonts`**: role → a font file in the package, the roles `ui`, `title`
  and `mono` (style.json's `theme.font_roles`); a file name is 1 to 24 of
  `a-z`, `0-9`, `_`, `-`, `.`, not starting with `.`, so it never names a
  path. Each role comes from the nearest package that names one; an unknown
  role is ignored and an ill-formed name dropped. The file is §4.7's format.
- **`layout`**: `bars`, a list of up to four bars, at most one per edge, each
  a `size` and three slots of built-in widgets (style.json's `theme.layout`);
  it resolves whole from the nearest package that sets it, never merged.
  The caps, the clamps and the required-set rule are
  `docs/shell_layout_2026-10.md` §5 and §9, which owns it.
- **`sprites`** and **`icons`**: a `.moygfx` file in the package each, a
  name of the `fonts` grammar ending `.moygfx` (another is dropped): the
  role sheet the sprite parts draw from and the bar's icon sheet (§4.6).
- **Reserved:** `images`, `wallpapers`, `sounds`, `editor` (style.json's
  `theme.reserved`) are accepted and not read yet. Each is defined, additively,
  by the phase that first reads it (§9).
- Any other key or role is ignored, a value of the wrong type is dropped and an
  index outside 0-63 is clamped, and each is noted (§7).

What the reserved keys will hold, as rev 2 sketched them (and the metrics
§4.2 lists beyond format 1's three, added to `metrics` additively):

```json
{
  "metrics":  { "border": 1, "title_h": 14, "button": 11, "pad": 4, "gap": 4, "shadow": 1 },
  "sprites":  "parts.moygfx",
  "icons":    "icons.moygfx",
  "images":   { "desk": "desk.moyimg" },
  "wallpapers": { "desk": "platinum_desk" },
  "sounds":   {},
  "editor":   {}
}
```

- **Variants are an axis, not two themes.** `colors.dark` and `colors.light`
  under one package keep D1's "one routine, two tables" and today's
  `variant()`/`set_variant()`. The `light_chrome()` branches become token
  differences (phases 2b and 3).
- **`images`** is the static picture the theme ships, **`wallpapers`** the
  installed wallpaper cart it names by id, one slot each (`desk`, the desk's
  and the home's; D6). An id that is not installed falls back to the image,
  then to the default (§4.9).
- **`sounds`** and **`editor`** have no defined keys yet. `sounds` waits for
  #141; `editor`
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
reach (§6). `mini_btn`'s and `chip`'s unscaled label pads were re-ruled
2026-10-13 by phase 4 step 2, when layout came to read line metrics (§4.7),
and stay unscaled pixels: a chip's clip and minimum pads inset a label box
that is now the `ui` font's (its drawn width, its line advance at the
scale), and a mini button's label is placed from its rect's corner, not
centred, so no metric enters it. A pad is a pixel inset, as `radius` is
(§4.2); scaling either would move pixels at font scale 2 and up.

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
theme gives the part no surface; its own field and edge, what a surface
that leaves out its fill or names a bare `border` paints, are the colours
its caller draws without one: `bar.item` `panel` (`accent` when on) edged
`chrome_ink` (`dim` when disabled), `desk.icon` `panel` edged `edge`, and
`desk` `bar` edged `bar_edge`. A `toolbar` surface that fills solid with
another role than `title` hands the toolbar's text that role's ink of
`theme.pairs` (`ui.toolbar` answers the ink).

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
  `shadow` stay unread until a phase moves that footprint. The footprint stays
  fixed through phase 6 (owner, 2026-10-14): a theme draws its own frame look
  within it through surfaces and sprites. **Later, if asked:** `border` (about
  1-4 px) and `title_h` as theme metrics, read by the WM on relayout; gated by
  the window-drag benches (`tools/p4_bench.py`) and the window tests.
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

- **One sheet per theme at fixed cells** (style.json's `theme.sprite_cells`,
  in 8-pixel tiles of one 128-wide sheet): the title buttons (close, maximise,
  minimise) per window state, and 9-slices for `window` per window state,
  `button` per state and `desk.icon`'s tile. The fixed cells are what made
  Winamp's classic skins paintable in any paint program (§10). A part with a
  sprite draws the sprite; a part without one draws its surface — a cell left
  all slot 0 is no sprite, and a state whose cell is empty takes the one its
  chain reaches (§4.3). The toolkit draws no checkbox or radio (a toggle is
  a `button` kind), so the sheet has no cell for either; the cursor is
  declined (§9 phase 6 step 9).
- **The sheet is a role sheet in the cart sheet's format** (ruled 2026-10-14,
  phase 6 step 9): `.moygfx`'s nibble names a slot — transparent, a role of
  the row, or black or white — resolved through the variant's table at the
  blit, so one sheet follows light and dark, an accent and a tint. No page
  edits it: the package carries it and `theme.json` names it (EDIT FILE,
  phase 6). A tinted glyph is a sheet in two slots. The slots and the cell map are style.json's.
- **Drawn in C, in one call each** (`native/moy_ui/moy_ui_draw.c`'s
  `sprite`). A 9-slice is one C call, not nine `spr` wrapper calls: its
  8-pixel corners whole, its edges and centre tiled, scaled by the chrome
  scale as a glyph is; a title button is its cell centred in the button's
  box. Neither draws outside its box, slot 0 leaves what is under it, and
  each slot resolves through the look's slot table, so a tinted glyph is a
  sheet in two slots and there is **no per-tint RGB bake cache**. The
  `window` 9-slice is the frame above the content — the ring's top row and
  the title strip, in place of the style's — under the title and the
  buttons; the ring's sides and foot and the shadow stay the style's, inside
  §4.5's footprint.
- **Icons:** a theme may ship an icon sheet (`icons`, the `IconSheet` shape,
  colours, not roles). **The user's own edited sheet (`system_icons.moygfx`)
  is an override slot** that wins over the theme's, like the layout override
  (D5); the theme's becomes the user's only when EDIT ICONS changes it.

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
  A layout reads the line metrics of the font its text draws in, never an
  8px cell (phase 4 step 2, below).
  Scaled text emits per-row runs through the span fill, not per-pixel stores.
- **The format is aligned.** Its header and tables are naturally aligned, and
  the parser assembles multi-byte fields from bytes or requires a `moybuf`
  (64-byte aligned) buffer. GCC on the S3 faults on an unaligned 16- or 32-bit
  load, which host and wasm do not reproduce, so phase 4 includes an on-glass
  load of a fuzzed font on an S3.

**The font file, version 1** (`native/moy_gfx/moy_text.h`; defined
2026-10-13 by phase 4 step 1). Little-endian; every field is a byte but
`size`:

| offset | bytes | field |
|---|---|---|
| 0 | 4 | `MFNT` |
| 4 | 1 | version, 1 (a newer one is refused) |
| 5 | 1 | glyph box width, 1-16 |
| 6 | 1 | glyph box height, 1-16 |
| 7 | 1 | line advance, the height to 32 |
| 8 | 1 | ascent: the baseline, in rows from the box's top, 0 to the height |
| 9 | 1 | the first byte the glyphs cover |
| 10 | 1 | how many glyphs, 1 up, ending at or before byte 255 |
| 11 | 1 | reserved, 0 |
| 12 | 4 | `size`: the file's length |
| 16 | count, padded to 4 | each glyph's advance in pixels, 0-32; padding 0 |
| then | count × width × ⌈height / 8⌉ | the glyphs: a glyph's columns left to right, a column's bytes top down, bit 0 of each its top row (petme128's layout at 8×8) |

A byte the glyphs do not cover draws glyph 0. The reader refuses a file over
8192 bytes, shorter than its header or than `size` says, with another magic or
a newer version, a field out of range, a reserved or padding byte set, tables
that do not fill `size` exactly, or a bit set below the glyph box, each by its
own code; a refused file loads nothing. A loaded file is copied into a buffer
its row owns; four rows load at once beside petme128, and an id names its slot
and the slot's generation, so a freed font's id is refused everywhere it is
passed. Petme128's row keeps `mg_text_raw`'s cell loop and a loaded font
draws through `mg_text_font`, a lane of its own: the S3 A/B that gated this
step is recorded in `moy_gfx_kernels.c`.
`moy_ui` faces it: `font_load`, `font_free`, `font_info` (box, line, ascent,
range), `text_width`'s font argument, and `theme_fonts` for the `fonts` key
(§3). `tests/test_font_lane.py` holds both rows to a reference raster, the
refusals to their codes and a fuzz to the target, and the boards' module to
the host's answers; `tests/on_glass.py`'s `fonts_match_host` loads the fuzz
corpus on glass at an odd address.

Ruled 2026-10-13, each the pick that builds least:
  - **Bytes, not a `moybuf`.** The reader assembles `size` from its bytes
    and every other field is one, so a source at any address is read alike
    and no caller aligns anything.
  - **Advances in the file, the glyph box fixed.** A per-glyph advance
    makes a proportional font (Tiny5, step 3) one format with petme128; a
    glyph keeps the box's width, so no per-glyph offsets.
  - **A wrap counts bytes, or pixels.** `moy_text_wrap`'s words mode wraps
    in `cols` bytes, the cells a body laid out in 8px cells counts; its
    pixel mode (`MOY_WRAP_WORDS_PX`, `ui.wrap_px`) wraps in a font's
    advances, for wrapped text drawn in a theme font (step 3).

Ruled 2026-10-13 by step 2 (layout reads line metrics), each the pick that
builds least:
  - **A font per role, read where its text draws.** `moy_ui` holds the font
    each role draws in (`moy_ui_font_use`; `ui.use_font` and `ui.font`, which
    answers the id, the glyph box's width, the line advance and the
    baseline), petme128's until one is installed and again once it is
    freed. The toolkit's primitives draw and measure their labels in it: the
    `ui` role for every kind, `title` for `panel_title` and `window.title`.
    A compiled run's canvas and the game canvas (`game_btn`,
    `game_icon_btn`, `text_field`) stay petme128. A width is
    `moy_text_width`'s, a fit `moy_text_fit_px`'s, a line box the line
    advance at the scale, and a gap counted in cells the glyph box's width.
  - **The shell's layouts read the same numbers when they are built.**
    `Layout.font_w` and `line_h` are the `ui` role's, `CodeLayout.cell` and
    `lh` the `mono` role's (`lh` with `_CODE_LEAD` rows); the bar's window
    chips and the desk's label pills measure with `ui.text_width`'s role.
    A theme switch installs the fonts and rebuilds the layouts (step 3,
    below). The app bodies that lay their own text out in 8px cells (the
    editors' content, Files, Get Carts, the Storybook) keep them.
  - **The measure is the draw.** `tests/test_line_metrics.py` installs the
    step-1 sample font for every role and holds each primitive's label
    inside its box, centred by the font's own width and line, the frame's
    title clear of its buttons and the game canvas unchanged.
- **App carts.** `ui` is the real toolkit, handed to every `type: "app"` cart,
  and those carts lay out in 8px cells and `print` in petme128. A theme font
  therefore reaches app carts **only together with** a published `text_w()` and
  line metrics; until then a cart's code runs inside the app-cart boundary
  (`ui.cart_text`, below), so its `ui` and its `print` draw petme128.
- **Legibility.** A theme font has a text-size floor tied to #203's chrome
  floor: `chrome.text_scale_floor` reads the same declared diagonal, with the
  same opt-in, and answers the font scale at which the `ui` font's ascent
  reaches `MIN_TEXT_MM`; the floor lifts the effective font scale and never the
  setting. `docs/visual_identity_v1.md` §5.4 asks for on-glass font tests;
  phase 4 step 3 took them on the T-Deck and the Guition S3.
- **Licence:** Tiny5 is OFL-1.1; `THIRD_PARTY.md` §3.1a carries its notice,
  its pinned source and what the conversion changed.

Ruled 2026-10-13 by step 3 (Tiny5 lands), each the pick that builds least:
  - **The file.** `tools/make_tiny5_font.py` samples the pinned Google Fonts
    TrueType (default instance) onto its 128-unit pixel grid into
    `native/moy_ui/themes/tiny5.glyphs`, the glyphs as rows, and builds
    `tiny5.fnt` from that: a 6×7 box, ascent 6, line 8 (the font's own
    ascender and descender add up to 9; 8 is petme128's rhythm and centres
    its five-row capitals in the line). GPOS kerning is not carried. A seed
    package's font files sit beside it in `themes/`, each font `X.fnt` with
    its licence's full text `X.txt`, and the tables embed every font a seed
    names and its licence (`ui.font_file`).
  - **Coverage: ASCII.** The font covers `0x20`–`0x7E`. Format 1 indexes a
    glyph by byte and text is UTF-8, so #135's accented Latin and Cyrillic,
    which Tiny5 draws, cannot be addressed until a codepoint map does it
    (#135's work); a byte the font does not cover draws glyph 0, the space,
    as petme128's row does.
  - **`night` names Tiny5 for `ui` and `title`**, as D1's pictures draw it,
    and every seed inherits it; `mono` stays petme128.
  - **The print font.** A canvas's `print` draws in one process-wide font
    (`moy_text_print_font`; `ui.print_font`), the `ui` role's, through the
    print gate and the `text` op; `ui.print_width`, `ui.print_fit` and
    `ui.wrap_px` measure in it. The Code tab and its popup print in `mono`.
    The bodies laid out in 8px cells keep their cells and draw in the `ui`
    font, narrower than the cells. The kernel's chrome
    (`native/moy_play/moy_chrome.c`) rasterises in petme128 with no VM, and
    the run's crash panel and hold pill, its pictures on the game canvas,
    replay in petme128 too; the strips, menus, toasts and banners replay in
    the print font.
  - **The app-cart boundary** is `ui.cart_text`: on while a cart's code runs
    (its body, `_layout`, `_init`, `_update`, `_draw`; a wallpaper cart's
    frame), every role and the print font read petme128, and the shell's
    come back after. A compiled run's canvas is petme128 already (step 2).
  - **The floor is the smallest text the owner kept at 1x**: `MIN_TEXT_MM`
    is Tiny5's six rows on the 7" 1024×600 Waveshare P4 (0.89mm; that panel
    runs font scale 1 by the owner's call of 2026-07-12), under the 3.5"
    Guition S3's 0.92mm (#203: its 1x text "reads fine"). It lifts nothing
    in the lineup and guards denser glass, as the tap floor does; petme128
    clears it on every panel.
  - **The fonts go with the VM.** The kernel's stop frees every font row
    (`moy_text_vm_stop`), so a restarted VM loads its theme's into empty
    slots; where nothing frees them (a host process), a load that finds
    every slot full frees them all (`moy_font_reset`) and loads again.
  - **The site carries no Tiny5.** Its display face stays petme128's webfont
    (`site/petme128.woff2`), the carts' font, so it has no Tiny5 notice to
    carry; the firmware images and the web runner carry the font, and
    `THIRD_PARTY.md` its notice.

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

Format 1 reserved `layout` with no keys until the shell-layout doc's step 4
defined it additively (2026-10-13, §3), so no theme written before then is
broken by it.

### 4.9 Images and wallpapers (D6)

A theme ships one static **image** and may name one installed **wallpaper
cart**, for the desk and the home alike (D6, one slot: `desk`). The
wallpaper is the theme's: the user's pick (Appearance's WALLPAPER, Paint's
WALL, `ctx.wallpaper`) is a change to the theme, written into `custom`, and
there is no setting beside it (owner, 2026-10-14; phase 6 step 5). A cart is
`"wallpapers": {"desk": id}`; a picture is `"images": {"desk":
"desk.moyimg"}` with the file in the package (a My Art pick copies Paint's
picture, the wallpaper copy WALL wrote); NONE is `"wallpapers": {}` with no
`images`, the theme naming its wallpapers and none of them, so the desk is
`desktop`'s fill. A theme naming neither key -- every seed -- shows the
console's default, the first wallpaper cart (Moy's night on a seeded store).
The look reads the keys at every switch (`Appearance.theme_wallpaper`): the
named cart where it is installed, else the picture, else NONE or the
default; a SAFE start runs no cart.

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
  mid-screen fights the carousel (mocked 2026-09-27 with Space Desktop). The
  home's card band has no surface of its own: the cards lie on the
  wallpaper. A band surface (`home.band`, a dither scrim a theme could set,
  phase 6 step 10) was removed on 2026-10-15 (owner): no seed used it; it
  could not reach the real case, a kid's own busy picture as the wallpaper
  in `custom`, which turns nothing on; and an opaque scrim hides the
  wallpaper the home exists to show. Where a carousel title proves hard to
  read on glass, the planned answer is the desk's label pill
  (`ui.icon_label`) under the focused card's title; it is not built.
- **Prefer wallpapers that need no VM.** A live Python wallpaper keeps the
  Python VM up, against the kernel doc's memory goal (§4.4 there). Images and
  Lua or wasm wallpapers are preferred; a Python wallpaper stays allowed and is
  charged as such.

### 4.10 Accent and tint: every colour role derived

**The rule** (owner, 2026-10-14: every colour role, the code and block
colours with them, follows from a source theme, MODE, one ACCENT and one
TINT, readable by construction). It runs in C (`moy_theme_derive`, phase 6
step 4), integer only, on the loader's luminance table (`moy_theme_lum`)
and the loader's own predicate: `clears(a, b)` is
`(hi + 500) * 10 >= MOY_THEME_CONTRAST * (lo + 500)` over the two
luminances, the pair check §7 already makes. Its inputs are the source's
two resolved rows (the token row and the content set, both variants), an
**accent** — one of sixteen pairs (A, deep) in style.json's
`theme.accents`, or the source's own — and a **tint** — NONE or one of
eight ramps of MOY64 indices in `theme.tints`, dark to light, never black
(0) or white (7). The role classes each step names below are
`theme.recolour`. The lists are data, as `font_roles` is, and the sixteen
accents and eight tints the mockups draw are the set (owner, 2026-10-14);
the mockups' `derive()` (`docs/media/theming_2026-09/appearance/mockups.py`)
carries them. **Every accent is one black clears** (`clears(A, 0)`), because
style.json's kinds paint a literal black label on an accent fill (`chip`,
`button` and `row_cta` in `on`); that is what lets the accent fill every
accent role with no new ink role.

Per variant, with B the source's row and R the result, R starting as B:

1. **Tint** (none on NONE). Each background role — `panel`, `surface`,
   `surface_alt`, `desktop`, `desktop_pattern`, `title_inactive`, `bar`,
   `dim`, `hilite`, `shade_hi`, `shade_lo` — whatever its B value, and each
   line role — `edge`, `border`, `bar_edge`, `shade_dark` — whose B value is
   not 0 or 7 (D1's black outlines stay), takes the ramp entry nearest B's
   value in luminance (the lower index on a tie) among the entries that
   clear, against black and against white, every floor B's value clears;
   with none, B's value. Keeping each
   background on its side is what keeps the literal inks of the kinds
   readable and the flags `surface_light`, `bar_light` and `band_light`
   true.
2. **Accent** (none when the source's own is kept). `accent`, `title`,
   `title_active` and `selection` take A. Each of `edge`, `border`,
   `hilite`, `shade_hi`, `bar_edge` and `desktop_pattern` whose B value is
   B's `title` or B's `accent` — a role the source tied to its identity
   colour — takes A. `focus` and `ink_accent` take the first of A, deep and
   R's `ink` that clears R's `surface`.
3. **Inks.** Each ink of `theme.pairs` but the dim inks (step 4's)
   against its backgrounds — `ink` (`surface`, `surface_alt`),
   `chrome_ink` (`panel`, `bar`), `title_ink` (`title`, `title_active`),
   `selection_ink` (`selection`) — keeps B's
   value if it clears every one, else takes black or white, whichever has
   the larger least ratio (black on a tie). Black or white clears any single MOY64 colour,
   and step 1 keeps the backgrounds a source pairs with one ink on one
   side, so this lands; `tests/test_theme_derive.py` holds it for every
   seed, accent, tint and variant.
4. **Dim inks.** `ink_dim` (`surface`) and `chrome_ink_dim` (`panel`,
   `bar`, `title_inactive`) keep B's value if it clears them, else take the dimmest that does
   (the least ratio, the lower index on a tie) of the ramp and the greys 5,
   6, 13, 49, 50 and 53, else R's `ink` or `chrome_ink`.
5. **Content.** Each `code_*` role and `ink_note` keeps B's value if it
   clears R's `surface`, else takes its value in style.json's derive set
   for a surface of that side (the `light` set where black beats white on
   R's `surface`, the `dark` set else) if that clears, else R's `ink`.
   `block_*`, `play`, `author` and `danger` keep B's: they are meanings, not
   looks, and the kinds paint their labels in literal inks they already
   clear.

The flags keep B's. NONE with the source's own accent answers B but for a
dim ink or a content colour B itself leaves under the floor, which steps 4
and 5 repair as they would under any tint (several seeds' light variants
carry one; their pairs of `theme.pairs` all clear). A source a hand edit
made unreadable in `theme.pairs` stays reported as any hand edit is (§7).

**Where the result lives: plain roles in `custom`'s `theme.json`.** Both
variants, every role, written by `ui.theme_custom` (phase 6 step 4), so the
loader stays format 1 and unchanged, EDIT FILE shows the colours the
console draws, and a console without the step, the web console and the
carts repository read the same row. The inputs ride in the package's
manifest, `"moybyte": {"type": "theme", "look": {"from": "night", "accent":
14, "tint": "plum"}}`, read by Appearance alone: ACCENT and TINT show them,
and each change re-derives from `from`'s resolved rows, never from the last
result, since a ramp quantises and quantising a quantised row drifts. A
colour (or any key the derivation writes) saved through EDIT FILE drops
`from`, and the file as saved is the source from then on. A package whose whole rows would pass `theme.bytes` (a
source with many surfaces) is written without the roles whose value §5's
rule gives back, which resolves to the same row, and one that does not fit
even so is refused (`MOY_THEME_BIG`). Declined: `accent` and
`tint` keys the loader derives from, smaller on the card, because the
derivation would then run in the boot path and in every resolve, a console
without it would draw the source's colours under the kid's name, EDIT FILE
would show two numbers in place of the colours, and a role set by hand
would need a precedence over the rule.

## 5. Fallback and inheritance, resolved once

Every key but `format` and `name` is optional. A theme resolves through
`inherits` (one parent; a theme that names none inherits the default) and ends
at the default seed, `night`, which sets every role nothing derives.
**Inheritance is the seeds'** (`platinum` and `win95` on `night`); a user
theme is a standalone copy, flattened when `custom` is made, with no `inherits` (owner,
2026-10-14; phase 6 steps 2 and 5), and a role a copy lacks — one a later
format added — derives as any unset role does. The chain
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
the row's dict per (theme, variant). The THEME grid's previews use
colours only, resolved from a package's bytes without installing it
(`ui.theme_resolve`, phase 6 step 2); they do not load other themes' assets,
since the face's preview ring holds four looks and would thrash.

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
- **A switch is one Class A dirty plus one integer.** A theme load, switch,
  install or in-place edit is an un-attributed Class A dirty (`ws._dirty`, the
  global epoch; `docs/surface_model_v1.md` §3) and moves one monotonic
  integer, `ws.look.look_gen`: a plain attribute `publish()` sets from
  native/moy_app's token-table generation (`theme_write` answers it), which
  every write of the live row moves — an icon sheet's adoption publishes too.
  Every cache whose pixels read the look folds `look_gen` into its key and
  replaces its entry when it moves:
  - the home statics and retained stamp (`launcher_layer._statics_base`);
  - the bar strips (`bar_layer._cart_bar_key`);
  - `wm_chrome`'s frozen chrome, the focused window's content freeze
    (`_content_static`) and every window buffer behind it (each `_Win`
    carries the generation its buffer was drawn under);
  - the desk backdrop the windowed tier restores from (`wm_desk._desk_sig`);
  - the toolkit's look ring (`ui._bind_theme`, keyed on the theme dict and
    an epoch `ui.look_moved()` bumps: an entry from an older one is
    re-resolved in its own slot).

  An install or uninstall (`Appearance.install_theme`) drops every shared
  token and content dict (`chrome.forget_themes`), since every theme resolves
  along `inherits` to the default and one install can move any name's row,
  then re-applies the live theme; the layout answers are cleared by that
  re-apply. A cache that reads no theme — the covers, the bar's kind images,
  the font table (keyed on the package that provides a file and the file: a
  seed's from the image, whose bytes an install cannot move, a store
  package's from its folder, dropped at every store change) — folds nothing. The web head
  runs this same console and re-rasters its frames, so it holds no cache of
  its own.

  **No cache keys on object identity.** A freed theme's `id()` can be reused by
  MicroPython, and identity keying is what `docs/surface_model_v1.md` §7 retired.
  The kernel ABI's theme handle carries the same generation (index plus
  generation, kernel doc §4.3). This is the existing dirty protocol, not a new
  mechanism; `set_theme` pokes no cache. `tests/test_theme_look_gen.py`
  re-installs a package under its own name with one role changed and holds the
  live console's pixels to a console booted under the edited package.

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
  The derivation (§4.10) never writes a pair under the floor; a hand edit
  through EDIT FILE can, and its reader is the editor's badge at the save
  (phase 6 step 6). Every seed passes both checks in both variants
  (`tests/test_theme_loader.py`).
- **The loader is the safety** (owner, 2026-10-14). A theme cannot run code,
  so the validation above, the readability report, the layout's required set
  (`docs/shell_layout_2026-10.md` §9) and the fuzz gates on the host and on
  every board's module are the whole of it. A package that will not load
  leaves the slot as it was, and a `theme` row naming one boots the default
  seed with the loader's note shown once; every board has a reset button.
  No strike ledger, no persist-after-render, no per-board reset input (§11).
- **Two themes are firmware, not files.** The default and a high-contrast theme
  (`contrast`, phase 6 step 2) are frozen into the image under reserved ids
  that no user theme can take. Every seed is firmware already (§2).

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
  1. **The font format and its lane** (landed 2026-10-13). The aligned bitmap format (§4.7), read
     by the loader into a buffer it owns; a font row behind `moy_text`'s font
     argument; the `fonts` key defined. Gate: goldens byte-identical (no seed
     names a font); the cart `print` path and the Lua C API byte-identical; an
     S3 A/B of the lane on the T-Deck; a fuzzed-font load on T-Deck glass.
  2. **Layout reads line metrics** (landed 2026-10-13). The `8 * fs` arithmetic reads the font's
     line metrics, and `mini_btn`'s and `chip`'s quirks are re-ruled (§4.3).
     Gate: goldens byte-identical (petme128's metrics are the 8px cell).
  3. **Tiny5 lands** (landed 2026-10-13) as the UI font, with the glyph
     coverage format 1 can address and the OFL notices. Gate: **one
     re-baseline, named and rendered**; `moy_prof` on the T-Deck home and
     Settings; legibility on T-Deck glass (and on the Guition S3's when it is
     on a desk).
     As built: `night` names Tiny5 for `ui` and `title`; a theme switch
     loads the files, installs the roles and the print font, frees the rest
     and rebuilds the layouts; the app-cart boundary, the pixel wrap and the
     text-size floor are §4.7's, with this step's rulings there.

| phase | builds | gate |
|---|---|---|
| **5 — layout** | per the shell-layout doc (§4.8) | that doc's gates, plus: bar and dock strips keep their cache (idle frames zero, strip rebuilt only on a key change) |

Phase 5 is planned as steps in the shell-layout doc (§13 there). Phase 6 is
planned below (2026-10-14).

**Phase 6 — sprites and sharing** (planned 2026-10-14; the owner's rulings
of that day and two adversarial reviews — architecture and performance —
folded in the same day; re-planned the same day around the Appearance page:
twelve steps, the icon home droppable and EDIT TASKBAR, the last, deferrable;
no re-baseline beyond the Appearance page's own). What the phase makes true:
a kid changes the look from one page of rows on any board, the change lands
in one theme, `custom`, that saves as they go, NAME… keeps it as a theme of
its own, and a theme is a package that moves between consoles and comes from
the store; a part may be a painted sprite. **The safety is the loader's** (owner,
2026-10-14): a theme is data the C loader validates and clamps (§7), the
readability check reports, the layout's required set holds, and the fuzz
gates run on the host and on every board — a theme cannot run code, every
board has a reset button, and the shell boots the default when the row's
package will not load. No ledger, no persist-after-render, no reset input
(§11). **A user theme is a standalone copy** (owner, 2026-10-14): `custom`
is the picked theme flattened into a self-contained package with no
`inherits`; inheritance stays among the seeds (§5). **Six more picks were
rulings** (owner, 2026-10-14; §12): `runtime: "theme"` as the refusal, no
`home_safe` flag (the band's surface that replaced it was removed on
2026-10-15, §4.9), fonts only from the package's folder and the icon home
droppable stand; the layout page and the one-line form were
replaced the same day by the Appearance rulings below.

**The Appearance rulings** (owner, 2026-10-14). The six-page theme editor
designed that morning (COLOURS FRAME SHAPES FONTS SPRITES LAYOUT, mockups on
the unmerged branch `editordesign`, `937863c7`) was rejected as too complex
for what a kid wants to change; the design is Windows' Personalization
instead:

- **Settings → APPEARANCE is one page of rows**, a window on the desk and a
  fullscreen page in console mode: THEME (a grid of previews: the seeds, the
  installed packages, `custom`), MODE (dark or light), ACCENT (one swatch of
  sixteen), TINT (one background tint), WALLPAPER (the wallpaper picker, out
  of the old tabs), WINDOW STYLE (the frame presets), FONT (the font
  presets, Tiny5 and petme128 only, owner 2026-10-14), CHROME SIZE (out of Settings, where the board has more than one
  scale), EDIT FILE… and, on `custom` only, NAME…. A picker opens as a
  popover in desktop mode and as a page in console mode
  (`docs/shell_layout_2026-10.md` L5); ←→ on a row steps it in place.
- **The first change on a seed or an installed theme copies it to one theme
  named `custom` and switches to it**; a change on `custom` edits it. It
  saves as it goes: no SAVE and no name page. A change started from another
  theme replaces `custom`. A theme the kid has NAMED is an installed theme
  like any other (owner, 2026-10-14): a change on it copies to `custom`,
  and NAME… → REPLACE keeps the change under the name. MODE, CHROME SIZE and THEME are not changes to a
  theme; ACCENT, TINT, WALLPAPER, WINDOW STYLE, FONT, a save through EDIT
  FILE and EDIT TASKBAR are.
- **Every colour role is derived** from the source theme, MODE, ACCENT and
  TINT, readable by construction (§4.10): no per-role picker and no "fails
  its pair" mark.
- **ADVANCED is EDIT FILE**: the theme's `theme.json` in the console's text
  editor, a save reloading the look live. Every role, the surfaces, the
  sprites and the layout are reachable there and only there; no page edits a
  role, a shape or a sprite visually.
- **NAME… renames `custom`** to a name of the kid's, so it is a theme of its
  own in the grid (a package like any in the store) and `custom` is free.
  There is no SHARE and no PASTE (owner, the same day): moving a theme to
  another console or publishing it is the store's, because the folder is a
  package (Files, the Zero's sync, the browser console's export and import,
  the carts repository).
- **The wallpaper is the theme's** (owner, the same day, the modern-Windows
  rule): WALLPAPER is a change like any other, picking a theme brings its
  wallpaper, and there is no separate persisted `wallpaper` setting (D6).
- **EDIT TASKBAR** edits the live bar and writes the theme's `layout`; it is
  the last step and deferrable, and until it lands a layout is edited
  through EDIT FILE.

The order puts the invalidation first (every later step edits a theme in
place), then the package (every later step installs one), then the design,
the derivation, `custom` and EDIT FILE as mechanisms the host tests drive
with no page, then the page in one re-baseline, NAME…, and the steps that
draw new things: sprites, the home's band, the icon home and EDIT TASKBAR.
Each step names the alternative it declined and the `tools/p4_bench.py`
scenario it adds; a scenario's numbers go to #66 (frames) or #58 (the P4
port), never here.

  1. **`look_gen` in the caches that read the look** (§6; landed
     2026-10-14). `look_gen` is a plain small integer on `ws.look`, set in
     `publish()` from `native/moy_app`'s token-table generation, which
     `theme_write` answers, never a property that calls the ABI per key
     build. A generation-keyed cache REPLACES its entry on a change, never
     accumulates; §6 lists the keys that fold it. `_statics_base` dropped
     its `id(ws.theme_colors)`, `_cart_bar_key` and the desk's signature
     their `id(ws.look.icon_sheet)` (adopting a sheet publishes), and the
     window chrome and the desk their theme name and variant. Three of the
     plan's entries were wrong and went: the font table stays keyed on the
     file, since a file comes only from the seeds' folder until step 2 keys
     it on the package that provides it (keyed on the theme instead, every
     switch would reload Tiny5 and relayout the shell); the token dicts are
     dropped whole on an install, not by name, since a name's row moves
     with any package along its `inherits`; and the web's layer re-ship has
     not existed since the recording stack went (the wasm head re-rasters).
     The windows behind the focused one were added: their buffers kept the
     old look until focused. `Appearance.install_theme` /
     `uninstall_theme` are the install path, and the installed package's
     name is a theme `set_theme` takes (`chrome.theme_known`), the least of
     step 2 an in-place edit needs to show. `set_theme`'s per-cache pokes
     are gone (`ws.bar_layer.invalidate()`, the rebinding of
     `ws.theme_colors` as the invalidation). Declined: a generation of the
     editor's own (the row's is bumped by every install). No pixel moves.
     Gate: goldens byte-identical; `tests/test_theme_look_gen.py`
     re-installs a package under its own name with one role changed, holds
     the live console's pixels to a console booted under the edit on the
     console and desk tiers, checks each key moved, and re-installs a dozen
     times with every cache's size unchanged; a bench scenario,
     `p4 idle edit` (`tools/p4_bench.py --edit`, and in the default run),
     installs in place on an idle screen and counts the repaint, then the
     idle frames.

  2. **Theme packages in the store, and the `contrast` seed** (landed
     2026-10-14). A theme is a
     cart folder, `<id>.moy`, whose manifest says `"moybyte": {"type":
     "theme"}` and `"runtime": "theme"`, its `main` naming `theme.json`. The
     `runtime` is the refusal every host already has: SPEC.md's rule that a
     host which does not implement a `runtime` refuses the cart cleanly keeps
     a theme off every other moy host with no spec change, and off a console
     older than this step (`runtime/cart_index.py`'s `RUNTIMES` has no
     `theme`, so Get Carts says CAN'T PLAY before it downloads). Its header
     sentence, "it installs only what a sandbox runs", is made true again by
     naming the third thing: data the loader validates (§13). Declined: a
     package kind of its own in the index (a second schema in moy-spec's
     cart index tool and a second list in Get Carts) and a `format` of its
     own (the format names are the `.moy` format's and `v04`'s, CLAUDE.md).
     **Where a theme publishes** is moybyte-org/carts (#230): its
     `scripts/validate.py` carries the same `RUNTIMES` refusal and its
     `build.py` bundles a theme folder as it bundles a cart; moy-spec's
     `write_index` reads `cart.json` and `build.json`, never `main.lua`, so
     `moy index` takes a theme as it is. Nothing in the folder runs.
     **Identity is the folder stem**: the loader requires `name` to equal
     it; a stored package is standalone and carries no `inherits` (one that
     does is refused with the loader's note, §5), and a role it leaves unset
     derives per §5, so a copy made before a later format adds a role still
     resolves. **Resolve from bytes**: `ui.theme_resolve` (+ in
     `native/moy_ui/modmoy_ui.c`) parses and derives a package against the
     default without installing it, cached per package and store generation;
     it serves the theme cards (`appearance_app.py`'s theme card draws a
     package's colours; step 7's THEME grid) and `layout_theme` naming a
     package other than the active one (`appearance.py`'s `_theme_layout`),
     so the one install slot (`ui.theme_install`, the
     memory rule's, `native/moy_ui/moy_theme.h`) holds the active theme
     alone. The store reads the type where it reads `wallpaper`: the home's
     tab sources (`runtime/console.py`) skip it, the Make tab too, and
     `nav.projects()` (`runtime/shell_servers.py`) leaves it out, so the
     Files app never opens it in the Editor: Files lists it as a kind of its
     own, THEMES, whose door is the Appearance app (its EDIT FILE… row from step 7). `Appearance.set_theme` takes a
     package's name: it reads the folder's `theme.json` into the slot, its
     fonts from the folder (`font_load` takes bytes, each at most
     `MOY_FONT_BYTES`; `_font_id` keyed on the package and the file) and, from
     step 7, its sheets. A package that will not load leaves the slot as it
     was and the row falls back to the default seed with the loader's note
     shown once, which is the whole of boot-time recovery. `THEME_NAMES`
     (`runtime/chrome.py`, imported by value) stays the seeds';
     `ws.look.theme_names()` (+) is the seeds plus the store's packages,
     read once per store change, and every list reads it. Get Carts lists
     and installs a theme as a cart (GET, ON CONSOLE, UPDATE; an installed
     row's verbs are USE and two-tap REMOVE, and removing the active theme
     falls back to the default seed; the size is the folder's, no chip, no
     memory plan). The Zero's sync carries the folder as it carries a cart —
     `theme.json` and the manifest as text, a font or a sheet as bytes —
     which means the wire's binary predicate learns the `.fnt` and
     `.moygfx` rule in its four copies (`runtime/moy_sync.py`'s
     `BINARY_FILES`, `native/moy_net/moy_sync_apply.c`,
     `native/moy_net/moy_webhost.c`, `firmware/web_runner/moy_store.mjs`),
     held by `tests/test_moy_sync.py`, `tests/test_moy_webhost.py` and
     `tests/test_web_store.py`. The high-contrast seed `contrast` joins
     `native/moy_ui/themes/` here, an accessibility theme listed with the
     seeds: black panels and white ink in both variants, a yellow accent,
     `radius` 0, no dither or pinstripe fill, `border` on every part, Tiny5
     — firmware under a reserved name (§7). Scenario: `switch from store`
     (the switch's frame, and the boot delta of a store package over a
     seed). Gate: a package from a LAN index (`indexes.json`) installs and
     renders on the T-Deck and the P4 and is listed in Appearance; a fixture
     package with a font under `tests/` installs on the host and on the
     boards' module (`tests/test_theme_loader.py`) and crosses the sync's
     tests byte-equal in all four copies; `ui.theme_resolve` of a package
     equals its row after install; a package the fuzz corpus refuses never
     reaches the slot and a row naming one boots the default; `contrast`
     passes both readability checks in both variants and is pinned as a
     golden of its own on the P4-as-shipped light config, as `platinum` and
     `win95` are; goldens byte-identical otherwise.
     As built: the folder's stem is the name, so a theme folder is
     `<name>.moy` with no author part (the name grammar has no dot), as the
     carts repository's folders are. An installed package holds
     `manifest.json`, `theme.json`, its fonts and sheets, licence texts
     (`*.txt`: `X.txt` beside a font `X.fnt` is that font's licence, as the
     carts repository's validator holds it) and optionally `cover.png`;
     the loader reads only the files `theme.json` names. The seeds keep the
     same rule: `native/moy_ui/themes/tiny5.txt` is Tiny5's OFL text, and
     the tables embed it beside `tiny5.fnt` (`ui.font_file("tiny5.txt")`).
     `ui.theme_resolve(src)` answers (rc,
     notes, name, dark, light, fonts, layout) -- the package's own fonts, the
     rest as `theme`, `theme_layout` and `theme_home` would after an install
     -- by parsing the bytes into the caller's buffer and resolving them as
     the loader's PROBE for that one call, ahead of the slot, so the slot is
     never borrowed and never doubled. The look reads the roster's packages
     when its generation (`CartManager.gen`, moved by every `apply`) moves
     and hands them to `chrome.adopt_packages`, which resolves each on first
     ask (`chrome.package`) and lists those that resolve
     (`chrome.theme_names`, which an app imports as it imports the seeds'
     list); a package whose bytes a re-scan moved is re-installed, and one
     removed falls back to the default seed, persisted. `contrast` keeps
     `ink_dim` and `chrome_ink_dim` light grey (6), so a disabled part still
     reads as disabled. Files renames no theme row, as it renames no
     project row: a theme's name is NAME…'s (step 8).

  3. **The Appearance design** (owner, 2026-10-14). Before a row is built:
     hand-drawn mockups — MOY64, the D1 look, bitmap fonts — of the page at
     320×240 and 1024×600 in `night` dark and a light theme, the THEME grid,
     the ACCENT and TINT pickers, an accent change before and after across
     the shell with its derived roles as a table, EDIT TASKBAR at both sizes,
     and the flow built-in → ACCENT → `custom` appears → NAME… → it is in the
     grid, in `docs/media/theming_2026-09/appearance/` (its README lists
     them; `mockups.py` there draws them and carries §4.10's rule in Python,
     checking every seed × accent × tint × variant on each run). The steps
     below build against them, as phase 3 step 4 built against D1's. The
     six-page editor's mockups that came first were rejected (the Appearance
     rulings above) and never merged. Changes no file a board image carries.
     Gate: the owner's review; `tools/check_docs.py`.

  4. **The derivation, in C** (§4.10; landed 2026-10-14). `moy_theme_derive` in
     `native/moy_ui/moy_theme.c`: a source's two resolved rows, an accent
     and a tint in, two rows out, integer only on `moy_theme_lum` and the
     loader's own predicate. style.json gains `theme.accents` (sixteen
     pairs), `theme.tints` (eight ramps) and the role classes §4.10 names
     (`theme.recolour`),
     generated into `moy_ui_tables.c` by `tools/gen_ui_tables.py` as the
     derive table is. `ui.theme_custom(source, accent, tint)` (in
     `native/moy_ui/modmoy_ui.c`, over `moy_theme_custom`; `source` a
     seed's name or a stored package's bytes, `accent` an accent's A or
     `None` for the source's own, `tint` a tint's name or `None`) answers
     the bytes of `custom`'s `theme.json`: the source's chain flattened —
     metrics, text effects, surfaces, frame, fonts and layout, each from
     the nearest package that sets it and the default seed's own only where
     it is the source (every package resolves through it anyway), and the
     source's own wallpaper keys — with both derived variants written
     whole, `name` `custom`, no `inherits`, minified, under `theme.bytes`
     (§4.10's fallback when not); `ui.theme_accents()` and
     `ui.theme_tints()` answer the two lists. Declined: the derivation in
     Python (the T-Deck pays it per held key) and in the loader (§4.10).
     Test net: `tests/test_theme_derive.py` — every seed × accent × tint
     × variant: no pair of `theme.pairs` under `theme.contrast`, `clears(A,
     0)` for every accent, the bytes parse with no note and resolve to the
     rows the derivation answered; tint NONE with the theme's own accent
     answers the source's rows but for the dim inks and content colours
     the source leaves under the floor (steps 4 and 5 repair them); the
     seeds' derived rows equal the mockups' `derive()` role for role; a maximal fixture (every role,
     32 surfaces, 32 effects, a layout) stays under `theme.bytes`; and the
     boards' module answers the host's bytes (the desktop MicroPython's
     `moy_ui` there, and `theme_custom_matches_host` in
     `tests/on_glass.py` on every console board). No golden moves (nothing draws it yet).

  5. **`custom`, saved as it goes, and the wallpaper in the theme** (landed
     2026-10-14). `Appearance.change(kind, value)` (`runtime/appearance.py`,
     `ws.look`; `kind` one of `accent`, `tint`, `wallpaper`, `frame`, `font`,
     the package's data in `runtime/theme_custom.py`) is the one
     write path every row of step 7 calls. On a seed or an installed package
     it writes `ui.theme_custom` of the active theme into `custom.moy`
     (manifest type `theme`, `runtime` `theme`, `look` per §4.10; any
     `custom` before it is replaced), installs it and sets the `theme` row
     to `custom`; on `custom` it re-derives from `look.from` and installs in
     place. **Live**: a change sets one look-dirty flag, applied at most
     once per frame by an install from bytes (`ui.theme_install`; the
     folder is never re-read), the latest change winning; `_install_fonts`
     runs only on a FONT change and a relayout only on FONT. **Saved as it
     goes** (owner, 2026-10-14: "it breaks the idea of not having
     old-school save for everything"): through the one mechanism Paint uses
     (`runtime/artwork.py`, #108 — a mark sets `_unsaved`, the write lands
     once the idle debounce `AUTOSAVE_S` has passed, and every exit path, a
     tab or app switch or leaving the page, commits, #111); a held key never
     writes per press, and a card write on the T-Deck costs tens of
     milliseconds (#66). Re-picking the source in the grid is the undo; the
     source is never written. **The wallpaper moves into the theme** (D6):
     the `wallpapers` and `images` keys are read (one slot, `desk`, §4.9);
     `select_wallpaper` — the Appearance picker, Paint's WALL, the cycle
     verb, `ctx.wallpaper` — becomes a change: a wallpaper cart is named by
     id, a My Art picture is copied into the package as `desk.moyimg`, and
     NONE names neither, so the desk is `desktop`'s fill (the built-in
     `fill:` options go). The `wallpaper` row of `system.json` and its
     reader are deleted: under the store rule (CLAUDE.md, 2026-09-07) a
     stored `wallpaper` reads as nothing and the seed version is bumped; no
     pass rewrites it. `set_theme` brings the theme's wallpaper; a named
     cart that is not installed falls back to the image, then to the
     default. The look's per-frame `tick` applies a change and runs the
     debounce, `commit` writes (the debounce, going home, an app switch, a
     theme switch); the `theme` row is written with the package, once, and a
     custom's fonts and picture are copied into its folder from the image or
     the source package. The seed version bump is the Appearance cart's
     (`moybyte.appearance.moy` 6), whose picker's fills became NONE.
     Scenario: `edit thrash` (+, `tools/p4_bench.py --edit`) — a held key on
     ACCENT through the verb, the painted-frame ms per change and the frames
     after release, on all four console boards, asserting zero store writes
     while held and exactly one after it settles. Test net: `tests/test_appearance.py`
     grows the first change on a seed (the seed untouched, `custom` written
     and active), a second change in place, a change started from another
     theme replacing `custom`, ten changes landing where one would, MODE
     and CHROME SIZE making no `custom`; `tests/test_theme_look_gen.py`'s
     in-place install holds through the verb; the wallpaper tests
     (`tests/test_appearance.py`, `tests/test_wallpaper_guard.py`,
     `tests/test_open_machine_wallpaper.py`) move to the theme's keys. No
     golden moves: no seed names a wallpaper, and an unnamed one resolves to
     the default the setting's absence gives today; the configs set none.

  6. **EDIT FILE** (landed 2026-10-14). `Appearance.edit_file()`
     (`runtime/appearance.py`, `ws.look`) opens the active theme's
     `theme.json` in the console's text app (`Workstation.open_text_cart`
     on the package's project kind, in the `json` mode of
     `runtime/text_modes.py`, whose soft save refuses a file that does not
     parse and badges it). It is not `nav.edit_file`: that door opens a
     cart project's Editor first, and a theme package is not a project. On
     a seed or an installed package `custom` is made from it and switched
     to first, since a seed is firmware; on `custom` its unwritten change
     is written first, so the file opened is the look shown. The text
     app's editor handle reports its saves to the look
     (`Appearance.file_watch`, handed to `EditorHandle` by
     `Player._open_cart_editor`): `file_check` is a second gate after the
     mode's — the text as typed, minified when only that fits
     `theme.bytes`, refused with the loader's note
     (`notice.theme_refused`'s `theme.why_*`) when the loader refuses it or
     its `name` is not `custom` (NAME) — and `file_saved` makes the file
     as saved `custom` and installs it at the next frame, the full switch
     (fonts, layout, wallpaper). A refused file leaves the look, the
     running `custom` and the manifest as they were; the hard exit writes
     it anyway, as for any document (#154), and the next change in
     Appearance writes over it. A file the loader takes with a pair of
     `theme.pairs` under the floor is written and badged
     `SOME COLOURS ARE HARD TO READ` (§7). A saved colour drops
     `look.from`, with the accent and the tint (§4.10), and so does any
     other key the derivation writes (a metric, a text effect, a surface,
     the layout), so a later ACCENT or TINT derives from the file as saved;
     a save that moves only `frame`, `fonts`, `wallpapers` or `images`
     keeps them, those keys set over the source's as a change sets them. The
     write is the look's (`Appearance.file_write`, step 7), into the
     package's folder as a change's commit writes it, never the project-file
     save, whose `journal/` a package must not carry: a theme package is flat
     data (the carts repository refuses any other file, and the browser's
     export zips the folder). **The door** is the Appearance page's EDIT
     FILE… row (step 7), through `ctx.theme.edit_file`. Declined: a JSON
     form of rows (it is the six-page editor again). Test net:
     `tests/test_appearance.py` — EDIT FILE on a seed makes `custom` and
     opens its file in the text app, not as a project; on `custom` writes
     the change first; a saved role moves the look and `look_gen` and drops
     `from`, and so does a metric; a `frame` alone keeps it; a file over the cap as
     typed is written minified; a refused save (bad JSON, over 4096 bytes,
     `inherits`, a name not `custom`'s) badges the note and moves nothing,
     its hard exit too; an unreadable pair saves and badges; idle frames
     write nothing. `tests/test_theme_look_gen.py` — a save draws, byte for
     byte, what a boot of the file as saved draws, every cache moving once.
     No golden moves (no door yet).

  7. **The Appearance page** (landed 2026-10-15). `runtime/appearance_app.py`'s IMAGES / CARTS
     / THEMES tabs are replaced by the one page of rows (the Appearance
     rulings above), built to step 3's mockups through the toolkit: the
     THEME grid of cards (`ui.theme_resolve`'s colours for a package, the
     row for a seed; selected, focus, a STORE tag for an installed package),
     MODE, ACCENT and TINT (each a row whose ←→ steps it and whose ENTER
     opens its picker, the sixteen swatches and the eight ramps, live behind
     it, ESC restoring what was there), WALLPAPER (the existing catalogue as
     its picker), WINDOW STYLE (the frame presets: CLASSIC, TABS, PLATINUM,
     WIN9X, FLAT, each a whole `frame`), FONT (the two presets, Tiny5 and
     the built-in petme128 — `petme128`, a reserved name in `fonts` the
     loader answers with font row 0, additive; owner, 2026-10-14: these two
     only, a package's own `.fnt` set through EDIT FILE), CHROME SIZE (moved
     from Settings, present where `ws.chrome_scales` has more than one) and
     EDIT FILE… (step 6's `edit_file`, through a `theme.edit_file` role
     row). Every row but THEME, MODE, CHROME SIZE and EDIT FILE… calls step
     5's change. Settings keeps APPEARANCE as the page's door. Scenario: `edit
     thrash` on the ACCENT row by keys. Test net:
     `tests/test_appearance_app.py` rewritten — every row reached and
     changed by keys alone and by a tap on each; a picker's ESC restores;
     #203's tap floor at 320×240. Goldens: ONE named re-baseline — the
     `app_appearance` row on every config; the `settings` row on the configs
     whose glass takes more than one chrome scale (CHROME SIZE leaves it);
     NEW `app_appearance_accent` rows (the picker open) on the P4-as-shipped
     and 320×240 configs. `app idle` zero with the page open on the P4 and
     the T-Deck.
     As built: the page is wide — THEME's grid of cards above the rows —
     where it is a window with room for four cards a row and two rows of
     them above the rows (`AppearanceLayout.wide`); otherwise THEME is the
     first row and its picker is the grid. Rows a page cannot fit scroll to
     the focus. A picker is drawn by the page over its own rows, under its
     row, in desktop mode, and as a page of its own in console mode: it never
     leaves the window, so the window's damage carries it and §8's popover
     layer, anchored to a bar's widget, is not used. The cursor of ACCENT,
     TINT and WALLPAPER tries its value live; THEME's grid only moves its
     cursor until ENTER. B undoes a try: a picker opened on another theme
     sets that theme back (the try's `custom` stays, as a change started
     from another theme replaces it), one opened on `custom` changes the
     value back; a tap outside a popover closes it and keeps the try. The
     source theme's own picture is the catalogue's THEME'S PICTURE,
     never My Art, and the change that puts it back is `change("wallpaper",
     THEIRS)` (`Appearance.THEIRS`, the source's own wallpaper). The look
     answers the rows in one read, `Appearance.choices()`, once per token
     generation; four `theme` role rows are the page's
     (`native/moy_app/roles.json`): `change` (a WINDOW STYLE or FONT preset
     by name, `Appearance.FRAMES` and `FONTS`), `choices`,
     `set_chrome_scale` and `edit_file` (a compiled app imports it,
     `theme_edit_file`). `petme128` in `fonts` is font row 0 in the look's
     font loader (`Appearance._font_id`), and names no file a package copies
     (`theme_custom.font_files`). The Settings CHROME SIZE row and
     `cycle_chrome_scale` went. Closing the page's window writes a change
     still settling, as an app switch does. **EDIT FILE beside the rows**
     (ruled 2026-10-15, the simpler of the two rules offered): a change made
     in the page while custom's `theme.json` is open moves the look's
     `file_gen`, and the editor reloads a buffer with no edits of its own
     (with a new undo history); a buffer with edits keeps them, and its save
     is the last write. The editor's saves go through the look
     (`Appearance.file_write`), never the project-file save, so the package
     carries no `journal/` (step 6). `edit thrash` drives the ACCENT row's
     key handler once a frame (`tools/p4_bench.py --edit`). Goldens moved:
     `app_appearance` on every config; `settings` on `big_800x480_fs3_dark`
     and `guition_480x320_fs1_dark` and their Settings sub-surfaces
     (`tests/shell_goldens/subsurfaces.json`); and on the nine desktop
     configs `app_calc`, `app_files`, `app_storybook`, `app_getcarts` and
     `app_notes`, whose frames show the Appearance window's left edge from
     under the later windows; new `app_appearance_accent` on
     `tdeck_320x240_fs1_dark`, `tdeck_320x240_fs1_light` and
     `p4_1024x600_cs2_fs1_windowed`. The seed version stays: the cart's
     body is the shell's, and its manifest did not move.

  8. **NAME…, and moving a theme** (landed 2026-10-15). NAME… is the
     page's last row on `custom` only: a name in `ui.text_field` on the
     keyboard every board has, the page holding the keyboard in text mode
     (`ctx.nav.text_mode`, as Storybook's page editor does) while it is
     open, and a character outside the name grammar (§3) never typed. ENTER
     asks `Appearance.name_check`: a seed's name, `custom` itself, a stem a
     cart folder holds and a name outside the grammar are refused with the
     reason under the field; a name an installed theme holds arms REPLACE?
     and a second ENTER on the same name replaces it (the Make tab's DEL
     arms SURE? the same way). `Appearance.name_custom` writes a change
     still settling, renames `custom.moy` to `<name>.moy` by one rename
     (`moy_carts.rename_package`; `adopt`'s aside-and-remove when it
     replaces), rewrites `theme.json`'s `name`, keeps the manifest's `look`
     with the name as its `title`, moves the roster entry
     (`CartManager.moved`) and switches to the named theme, persisted;
     `custom` no longer exists until the next change, and a change on the
     named theme copies to `custom` as on any installed one. ESC or a tap
     outside backs out. The THEME grid lists `custom`, then the store's
     packages (a named theme among them), then the seeds. Two `theme` role
     rows serve it, `name_custom` and `name_check`. A Files rename of a
     theme was not built: Files lists the store's themes and opens the page
     on one, and a theme's name is NAME…'s. **Moving a theme between
     consoles** is the store's: the folder is a package, carried by the
     card, the Zero's sync (step 2) and the browser console's cart export
     and import (owner, 2026-10-14: `firmware/web_runner/page_tail.js`'s
     `pzDownload`, the `export` and `import` messages of
     `firmware/web_runner/worker.js`, which zip a folder of the carts root).
     The import reads the manifest's type (`themeOf` in
     `firmware/web_runner/moy_store.mjs`): a theme lands as `<name>.moy`,
     never through `cartBase`'s author part, and a taken name as the next
     `_2` stem (cut so the name stays 24) with `theme.json`'s `name`
     rewritten to match; the store's own archive import,
     `moy_carts.import_archive`, lands a theme the same way. The dev
     channel's `state` reports the theme in force and each store package as
     the loader resolves it (`Appearance.package_rows`: its refusal code and
     a digest of its dark and light rows), which is what a carried theme is
     compared by. Test net: `tests/test_appearance.py` (NAME… renames, the
     grid lists the name, `custom` is gone, a reboot keeps it, the next
     change on the named theme makes a new `custom`, each refusal, REPLACE,
     the fonts travelling with the folder, the archive import's name and
     `_2`), `tests/test_appearance_app.py` (the row on `custom` only, typed
     by keys, the refusal and the REPLACE? arm, ESC and a tap outside),
     `firmware/web_runner/store_test.mjs`, and
     `tests/test_web_persist_e2e.py`'s `test_a_theme_exports_and_imports_as_a_zip`
     beside `test_a_cart_exports_and_imports_as_a_zip`: a theme named on
     the host, imported, exported and imported into a fresh profile (the
     same name, the same resolved rows) and into the same one (the `_2`
     stem, `name` rewritten, resolving). Gate: **the round trip** — a theme
     made on the T-Deck through the page, named, its folder carried to the
     P4's store, selected there, resolves to the same rows on both (the
     `state` digests equal) and to the same golden hash on the host.
     Goldens: NEW `app_appearance_custom` (the page on a `custom` made from
     `night` with ACCENT stepped once, NAME… showing and focused) on
     `tdeck_320x240_fs1_dark`, `tdeck_320x240_fs1_light` and
     `p4_1024x600_cs2_fs1_windowed`; every other row identical.
  9. **Sprites (D3)** (landed 2026-10-15). `"sprites": "<file>.moygfx"`, the cart sheet's hex
     format (`runtime/editors_sheet.py`: one nibble a pixel) read as a **role
     sheet**: a nibble is a slot of style.json's `theme.sprite_slots` — slot 0
     transparent, then roles of the row (`ink`, `surface`, `shade_hi`,
     `shade_lo`, `shade_dark`, `edge`, `accent`, `focus`, `title`,
     `title_ink`, `panel`, `dim`, `hilite`) and two fixed indices, black and
     white — so one sheet follows both tables, an accent and a tint, and
     §4.6's tinted glyph is a sheet in two slots. The sheet is user data:
     its reader joins the loader's validation (`native/moy_ui/moy_theme.c`)
     with a size cap of its own, the fuzz corpus grows sheets, and the
     decoded sheet lives in a buffer the loader owns. The empty-cell flags
     are computed when the sheet loads and each look's slot-to-colour table
     when its row is set (`moy_ui_look_set`, once per `look_gen`); the blit
     resolves a slot in C through that table, never per pixel in Python.
     The cells are fixed: `theme.sprite_cells` maps each
     spritable part and state (the title buttons, the
     `window` and `button` 9-slices, `desk.icon`'s tile) to a rect in 8px
     tiles of one 128-wide sheet; a cell that is all slot 0 is no sprite,
     and the part draws its surface (§5). In C
     (`native/moy_ui/moy_ui_draw.c`): one sink op per sprite and one per
     9-slice, scaled by the chrome scale as `moy_ui_glyph.c` scales a glyph
     (integer, per run), corners whole and edges tiled, inside §4.5's
     footprint; nine calls never. `"icons": "<file>.moygfx"` is read the
     same step: a theme's bar icons in the `IconSheet` shape, under the
     user's `system_icons.moygfx` (§4.6). No page edits a sheet (the
     Appearance rulings): a package carries its sheets, `theme.json` names
     them (EDIT FILE), and EDIT ICONS stays the user's icon sheet's.
     Declined: a MOY64 `.moyimg` sheet (one sheet per variant, and the Paint
     canvas has no cells), per-part sprite keys in `theme.json` (the
     empty-cell rule needs none), a sprite page, and **the cursor** (its
     footprint is hard-coded in the launcher's damage stamps as `8 * fs`
     boxes; a themed cursor waits for one shared constant there, a later
     item). Scenarios: `sprite chrome drag` — chrome painted every frame
     with a worst-case fixture sheet (every cell set): a window dragged over
     Settings, a Settings scroll drag, cursor motion. Gate: **a sprite part
     no slower than its surface fallback** at frame level — the scenarios'
     painted-frame medians within §9's thresholds against the image before,
     on the Waveshare P4, the Guition P4 and the T-Deck (the Guition S3
     recorded as not run when it is not on the desk), and
     `tools/p4_bench.py --painter` (a committed mode: the painter's and the
     sprite's µs per call on the same part, the 9-slice's against the
     frame's); goldens byte-identical (no seed ships a sheet); a fixture
     sheet painted on every console board on the desk reads back equal to
     the host (`painter_matches_host`'s pattern); `gate_counts` budgets
     hold.
     **As built.** The reader is `moy_theme_sheet_check` and
     `moy_theme_sheet_load` (`native/moy_ui/moy_theme.h`): a line a row of
     up to 128 hex digits, blank lines and a line's surrounding spaces and
     CR ignored, anything else refused (`SYNTAX`), at most
     `theme.sheet_rows` rows and that many 130-byte lines (`BIG`); the
     loader keeps the rows the cells reach. The toolkit has no checkbox or
     radio, so the sheet has no cell for either. The `window` 9-slice is the
     frame's top (§4.6); its slot 0 shows what is under the window, so the
     style's ring is not drawn beneath it. `custom`'s flattening carries
     both keys and `_gather` copies both files, so a sheet travels through
     `custom`, NAME… and the browser's export and import (the e2e test makes
     its theme from the `sprig` fixture), and `package_rows`'s digest covers
     the sheets' bytes. Nets: `tests/test_theme_sprites.py` (the reader,
     the blit against a Python model of the rule at three scales and under
     a clip, the frame, the recolour, the look, the sheet corpus on the
     boards' module) and `on_glass.sprites_match_host` on every console
     board's suite; the carts repository's `scripts/validate.py` checks a
     theme's `.moygfx` as the loader reads it. `tools/p4_bench.py --sprites` runs each scenario with
     `tests/fixtures/themes/sprig.moy`'s sheet and again with the sheet
     cleared on the same image and theme, and `--painter` is the per-call
     mode; the numbers are #66's.

  10. **The home's readability** (D7, §4.9) — removed 2026-10-15: the band
     surface `home.band` landed that day (`b1f4a91c`) and the owner removed
     it, because no seed used it, it could not reach a kid's own picture in
     `custom`, and an opaque scrim hides the wallpaper (§4.9).

  11. **The icon home** (`home: icons`; the shell-layout doc §12, the owner's
     "we don't have to do it now", 2026-10-14; landed 2026-10-15). The `grid` arrangement with
     one-tile cards: each cart's icon (the cover's reduction,
     `SpriteSheet.icon_from_hex`) in the theme's `desk.icon` tile and
     sprite, no title under it, the name in the pill for the focused card
     only, the tab row and the verbs the grid's. After step 9, because the
     tile sprite is that step's, and the owner may drop it. Scenarios: `icon
     home drag`, `fling` and `tab` (the moving frames, as the carousel's).
     Gate: an `icons` golden pinned on the P4-as-shipped and 320×240
     configs; `home idle` zero; the moving scenarios within §9's thresholds
     on every console board on the desk; the carousel's and the grid's
     goldens hold.
     **As built.** `IconHome` (`runtime/launcher_layer.py`) is the grid's
     `Launcher` over its own cells (`_IconCells`), which stand in as its
     layout: square tiles with a label band under each, two or three rows
     sized from the content width, the columns spread across the grid's
     viewport -- so the grid's selection, keys, taps, scroll, fling and
     drag partial run unchanged, on its panel and band. A cell's icon is the cover's ICON reduction
     where the cart names no icon, else its sheet's tile, else its type
     glyph, at a whole-number scale; the focused cell wears the ring, the
     favourite star and the label pill, which reaches half a column past
     its tile each side (the drag partial redraws the selected card over
     that reach). The tile and the pill are the desk's own bodies,
     `ui.icon_tile` and `ui.icon_label`, which the desk now draws through.
     The theme loader reads `icons` (style.json's `layout.homes`); a
     theme's layout reaches it, or EDIT FILE on one. Goldens:
     `tdeck_320x240_fs1_homeicons` and `p4_1024x600_cs2_fs1_homeicons`,
     the home's four tabs each; `tools/p4_bench.py --icons` runs the
     scenarios beside the grid's; the numbers are #66's. Net:
     `tests/test_icon_home.py`.

  12. **EDIT TASKBAR** (owner, 2026-10-14; deferrable: nothing before it
     depends on it, and until it lands the page has no taskbar row and a
     layout is edited through EDIT FILE). Its doors are a row of the bar's
     menu and an Appearance row, EDIT TASKBAR…. It is a mode of the LIVE
     bar, not a page: the items draw boxed, and ←→ (↑↓ on a side bar)
     select an item, the bar's own edge first; SPACE grabs and drops the
     selection — a grabbed item moves one place per arrow along the bar and
     across its slots, the perpendicular arrow sends it to the other end,
     and the grabbed bar moves to the edge the arrow names; ⌫ removes the
     selected item; TAB opens the tray, the catalogue's widgets the bar
     lacks (the repeatable `text`, `spacer` and `separator` always), and
     ENTER on one adds it beside the selection; `menu`, `zone` and `close`
     move and are never removed, a refused ⌫ saying why; ENTER or ESC
     leaves. Touch is ENTER on the tapped thing: a tap selects, a second
     grabs, a tap on the bar drops. Every change runs the composer's rule
     (`docs/shell_layout_2026-10.md` §9) before it shows, so the bar never
     shows a layout the rule would replace, and a move that would leave
     `zone` on a side bar (it is horizontal only, §4 there) keeps a slim
     top bar holding `zone` alone — four bars at most, one per edge, sizes
     16 to 32 (§5 there). It writes the theme's `layout` whole, making
     `custom` first on a seed or an installed theme (step 5), and retires
     the Settings LAYOUT row and its `layout_theme` override (§11 there;
     owner, 2026-10-14: the row stays where it is until this step), since
     the bars are the theme's from then on: under the store rule a stored
     `layout_theme` reads as nothing, and the seed version is bumped. Console mode (the T-Deck, the Guition S3, a
     P4 in console mode) runs the same mode on its bar, with the tray and
     the edges as the page under it, limited to its catalogue (no `chips`)
     and `LAYOUT_EDGES`. Declined: a LAYOUT page of rows (the six-page
     editor's) and a drag-only mode (every board has a keyboard, L12).
     Scenario: `bar edit thrash` (+) — a held arrow moving a grabbed item:
     the frame per move and the store writes, zero while held and one after
     it settles. Test net: `tests/test_shell_layout.py`'s fuzz grows key
     streams through the mode (none yields a layout the rule replaces, none
     removes a required widget); `tests/test_appearance.py` holds the write
     into `custom` and that no `layout_theme` is read. Goldens: byte-identical
     outside the mode; NEW rows of the mode on the P4-as-shipped and
     320×240 configs; `app idle` zero after leaving it.

  Ruled 2026-10-14 and folded in: standalone copies over inheritance, the
  safety as the loader's, the fixed frame footprint, the design step, the
  picks above, no SAVE (`custom` saves as it goes), one wallpaper and then
  the wallpaper as the theme's, the Appearance rulings, and the four answers
  of the design's review (a NAMED theme copies to `custom` like any other;
  FONT's presets are Tiny5 and petme128; the Settings LAYOUT row stays until
  EDIT TASKBAR retires it; the sixteen accents and eight tints as drawn).

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
- **A strike ledger, persist-after-render and a per-board reset input for
  themes** (owner, 2026-10-14) — a theme is data the loader validates, it
  cannot run code, and every board has a reset button; the loader's refusal
  and the default seed are the way out (§7).
- **User themes that inherit** (owner, 2026-10-14) — a user theme is a
  flattened copy; inheritance stays among the seeds (§5).
- **A SAVE in Appearance** (owner, 2026-10-14) — the OS has no save;
  `custom` saves as it goes through Paint's mechanism (phase 6 step 5).
- **The six-page theme editor** (owner, 2026-10-14) — COLOURS, FRAME,
  SHAPES, FONTS, SPRITES and LAYOUT pages, a per-role picker with
  "fails its pair" marks, a name page and a byte budget (mockups on the
  unmerged branch `editordesign`, `937863c7`): too complex for what a kid
  changes. One page of rows, every colour derived (§4.10), and EDIT FILE
  for the rest.
- **SHARE and PASTE, the one-line form** (owner, 2026-10-14) — a theme
  travels as its package through the store (Files, the Zero's sync, the
  browser console's export and import, the carts repository); NAME… is how
  a kid keeps one.
- **A wallpaper setting beside the theme** (owner, 2026-10-14) — the
  wallpaper is the theme's (D6).
- **Per-slot wallpapers** (owner, 2026-10-14) — one screen-sized bake per
  slot is memory a board does not have, and the home already shows the
  desk's wallpaper behind its cards; one wallpaper, D6.
- **Painter fills on the PPA** — §4.4.
- **A surface for the home's band** (`home.band`, a scrim; owner,
  2026-10-15) — no seed used it, it cannot reach a kid's own picture in
  `custom`, and an opaque scrim hides the wallpaper the home shows (§4.9).
  Nor a `home_safe` manifest flag — the cart author's claim about every
  theme's home, which no check reads (§12.1).
- From the look sessions (D1): MOY64 grey chrome instead of the PICO-8 base
  colours, cartridge-shell or notched cards, labels inside the desk tiles, and
  Pixel Operator replacing Tiny5.

## 12. Questions, ruled

Rev 2 left four questions open. Each is ruled (2026-10-11) with the pick that
builds least, and the owner accepted all four on 2026-10-12, with §9's
sequencing picks: the 320×240 layout code retires in phase 3 step 4, toolkit
reach for hand-drawn rows waits for phase 3 and `look_gen` for phase 6, and
`outline` stays a skin until phase 3 step 4.

1. **Home readability: `home_safe` or a scrim (§4.9)?** Neither. A scrim,
   as the surface of the home's band part, was ruled 2026-10-14 and built
   in phase 6 step 10; the owner removed it on 2026-10-15 (§4.9). No
   manifest flag: it would be the cart author's claim about every theme's
   home, and nothing reads it. The default home wallpaper is a still image
   that is readable by construction; a hard-to-read title's planned answer
   is the label pill (§4.9).
2. **Sprites in phase 3 or phase 6 (D3)?** Phase 6. Phase 3's parts draw
   surfaces; a sprite part then only replaces a surface, so nothing in phases
   3-5 depends on it, and format 1 already reserves `sprites`.
3. **A generator** (a theme derived from a wallpaper or one picked colour)?
   Ruled no generator on 2026-10-11; the owner's Appearance ruling of
   2026-10-14 made one picked colour the way a kid recolours: ACCENT and
   TINT derive every role of a source theme (§4.10), and the result is
   plain roles in a package, so there is still no second grammar. A
   wallpaper's colours derive nothing.
4. **Theme sounds: with #141 or earlier?** With #141. `sounds` stays reserved.

Phase 6's picks, accepted by the owner on 2026-10-14 (§9): `runtime:
"theme"` is the refusal that keeps a theme off other hosts; the home's band
surface over a `home_safe` flag (the surface removed 2026-10-15, §4.9); a theme's fonts come only from the
package's folder; the icon home (step 11) is droppable. Two more were
replaced later that day by the Appearance rulings (§9): the layout page
(now EDIT TASKBAR, step 12, deferrable) and the one-line form (no SHARE or
PASTE; a theme travels as its package). The same day: no SAVE, `custom`
saving as Paint does (step 5); one wallpaper for the desk and the home, and
then the wallpaper as the theme's (D6, step 5).

## 13. Sentences this doc will make false

Each is corrected in place by the phase that falsifies it:

| sentence | in | fixed by |
|---|---|---|
| `bar_h()` as the whole content contract | `docs/app_api_v1.md`, `runtime/system_api.py` | the shell-layout doc |
| the windowed tier's games-only Library (#105) | `docs/shell_ux_v1.md` | the shell-layout doc: the desk's PLAY opens D7's home |
| "it installs only what a sandbox runs (RUNTIMES)" | `runtime/cart_index.py` | phase 6 step 2: or data the loader validates |
| `icons` "planned, not built" | `docs/shell_layout_2026-10.md` §5, §12 | phase 6 step 11 |
| the per-board reset key, the strike ledger as "theming phase 6's" | `docs/shell_layout_2026-10.md` §9 | phase 6 step 2: the loader's refusal is the recovery |
| the wallpaper as a persisted choice of its own (`prefs` persist, the fills, the look's choice) | `runtime/README.md` (`appearance.py`), `docs/app_api_v1.md` (`ctx.wallpaper`), `runtime/appearance.py` | phase 6 step 5: the wallpaper is the theme's |
| the Appearance app's IMAGES / CARTS / THEMES tabs and its monitor preview; CHROME SIZE as a Settings row | `runtime/README.md` (`appearance_app.py`), `runtime/appearance_app.py`, `runtime/settings_layer.py` | phase 6 step 7: one page of rows |
| the `layout_theme` override and the Settings LAYOUT row (D5) | `docs/shell_layout_2026-10.md` L7, §11 | phase 6 step 12: the bars are the theme's |
| the browser import landing every folder as an `author.name` cart | `firmware/web_runner/worker.js`, `firmware/web_runner/moy_store.mjs` | phase 6 step 8: a theme lands under its own name |

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
| P7 | the default home as the live-wallpaper defect; scrim cost | accepted | §4.9: a static default and the idle-home gate (the scrim, removed 2026-10-15) |
| P8 | carousel covers on every board | accepted, deferred | the home's structure is the shell-layout doc's (§4.8); its rules carry: one cached base size per cover, an integer upscale at draw, no per-size variants, a T-Deck carousel scenario in p4_bench |
| P10 | GCC on the S3 faults on unaligned loads | accepted | §4.7: an aligned format, byte assembly or `moybuf`, a fuzzed-font load on S3 glass |
| P11 | `malloc_dma` has no free; the PSRAM reserve; bakes | accepted | §8 memory rule; §4.6 no per-tint bakes, one-call 9-slice |
| P12 | phases 3–6 had no perf gate | accepted | §9 gates per phase with named boards and meters |
| P14 | scaled text writes one pixel at a time | accepted | §4.7: per-row runs |
| P15 | flash and web bundle | accepted | §9: image headroom per board every phase, P4 first |
| P16 | not the PPA | accepted | §4.4, §11 |
| P17 | assertions without evidence; the `fs*N` count | accepted | §4.4 and §8 marked PREDICTED with gates; §2 recount (131 `8 * fs` sites) |
