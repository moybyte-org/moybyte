# Theming — one data package re-skins the whole OS (2026-09)

**Status: DRAFT rev 2, 2026-09-27.** Rev 1 went through an adversarial
architecture pass and a performance/hardware pass the same day (verdicts:
**STANDS WITH FIXES, close to REWORK** / **PERF CASE STANDS WITH FIXES**).
All of their findings were taken into this revision; **§14 records each one
and where it landed**. The owner's decisions are in §1. Nothing here is
scheduled until the owner accepts it.
**Depends on:** `docs/native_kernel_2026-09.md` (the toolkit and the window
managers move below the app verb table; §8 maps every theming piece onto its
sprints). **Relates to:** `docs/visual_identity_v1.md` (the default look),
`docs/app_api_v1.md` (`ctx.theme`, the app-cart `theme()` global),
`docs/surface_model_v1.md` (one dirty protocol), `docs/shell_ux_v1.md` (#105,
the two worlds), `runtime/skin.py` (skins as data), #177, #146, #147, #203.
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
`docs/shell_ux_v1.md` and the kernel's sprint 7 (§4.8). A theme reserves keys
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
| D7 | **The home is one layout on every board**: the cover carousel with tabs (Games / Mine / Apps / Make), themeable, with its own wallpaper slot. How it meets #105's desk ("apps are windows, games are fullscreen") is §12's first open question, settled in the shell-layout doc. |

Rejected in the same sessions, so they are not re-proposed: MOY64 grey chrome
replacing the PICO-8 base colours, cartridge-shell or notched library cards,
labels inside the desk tiles, and Pixel Operator replacing Tiny5 as the UI font.
Each was mocked and judged worse (§11).

## 2. What exists today

**SOURCE.** Theming is already data in three layers; what it lacks is reach.

- **Colour tokens:** `runtime/chrome.py`. `THEMES` holds six families (`night`,
  `indigo`, `berry`, `forest`, `slate`, `machine`), each with a light variant in
  `THEME_LIGHT`, so twelve token sets. `theme_colors(name, variant)` resolves the
  base keys (`panel`, `edge`, `title`, `title_ink`, `accent`, `hilite`, `dim`)
  plus the semantic roles of `docs/visual_identity_v1.md` §4.3 and two flags,
  `surface_light` and `bar_light`. `runtime/appearance.py`'s `set_theme`
  installs a set.
- **Widget style:** `runtime/ui.py`'s `DEFAULT_SPECS` (kind × state → a
  `(field, ink, edge)` triple of token specs) and `DEFAULT_METRICS`, with the
  state ladder `disabled > pressed > hot > on > hover > rest` and the quirks
  listed in `NON_DATA_QUIRKS`. `runtime/skin.py` installs a skin as a delta over
  both; two ship, `default` and `outline`.
- **Icons:** a themeable 16×16 `IconSheet` (`runtime/editors_sheet.py`), baked
  from `chrome._ICON_ART`, or the user's own edited sheet, `system_icons.moygfx`
  (Settings → EDIT ICONS).
- **Wallpapers** are carts with `"type": "wallpaper"`; apps reach the backdrop
  through `ctx.wallpaper`.
- **Who reads tokens:** shipped apps through `ctx.theme` (`colors()`, `light()`,
  `name()`, `variant()`, `set()`, `set_variant()`, `skin`, `set_skin`;
  `runtime/app_context.py`). App carts (`"type": "app"`) through the ungated
  `theme()` global, which returns today's token dict (`runtime/system_api.py`;
  `system_carts/notes.moy` reads `th["panel"]` and `th["ink_dim"]`). **Games
  have no `theme()`**, and giving them one would be a cart-verb change in
  moy-spec's SPEC.md.
- **Persisted settings:** `theme`, `theme_variant`, `skin` and `wallpaper` in
  `system.json`.

What stops a Platinum or Win95 theme today:

1. **Most drawing does not go through the toolkit.** ESTIMATED (grep of
   `runtime/`, 2026-09-27): a few hundred raw `rect`/`print`/`text`/`line`
   calls against about 50 toolkit widget calls, over ~25 modules. At 320×240 /
   font scale 1 the shell takes frozen hand-rolled branches (`if not
   ws.layout._base`) kept for pixel parity (`docs/history/ui_refactor_2026-08.md`,
   "the frozen baseline"). And 13 `light_chrome()` sites branch on the light
   presentation in code.
2. **A skin can only colour and pad.** There is no frame style (raised, sunken,
   etched), no skinnable window decoration or bar. A 4×4 two-colour pattern fill
   does exist, `fillp` (`device/device_canvas.py`), but on a per-pixel lane (§4.4).
3. **One font, and chrome text at scale 1 is libmoy's.** `mg_text` hands scale 1
   to libmoy's `moy_print`, which uses its own compiled-in petme128 and ignores
   the font blob (`native/moy_gfx/moy_gfx_kernels.c`). The S3 boards and the
   shipped P4 draw their chrome at scale 1. Layout arithmetic is written in
   multiples of the 8px cell (ESTIMATED: 131 `8 * fs` sites in `runtime/`, and
   more in other forms; phase 4 recounts before sizing).
4. **Layout is fixed per tier:** the bar, the taskbar and the window mode are code.

## 3. The package

A theme is a folder with `"type": "theme"` in its manifest (the manifest field
is `type`, as for wallpapers and apps; `kind` is the Files document vocabulary).
It holds a `theme.json` and optional **data** assets: images, fonts, sprite and
icon sheets. It never holds a cart or a script. Every key is optional (§5).

```json
{
  "format": 1,
  "name": "Platinum",
  "inherits": "moy",
  "colors": {
    "dark":  { "panel": 1,  "title": 13, "title_ink": 7, "surface": 60 },
    "light": { "panel": 6,  "title": 48, "title_ink": 0, "surface": 6,
               "shade_hi": 7, "shade_lo": 50, "shade_dark": 0, "desk": 22 }
  },
  "metrics":  { "border": 1, "title_h": 14, "button": 11, "pad": 4, "gap": 4, "shadow": 1 },
  "surfaces": {
    "window.title": { "active": "pinstripe shade_lo/shade_hi, border" },
    "button":       { "rest": "raised, fill surface, border",
                      "pressed": "sunken, fill shade_lo, border" },
    "desk":         { "rest": "dither desk/23" }
  },
  "frame":    { "style": "platinum", "title_align": "center", "buttons": "C|T|Z" },
  "fonts":    { "ui": "ui.fnt", "title": "title.fnt" },
  "sprites":  "parts.moygfx",
  "icons":    "icons.moygfx",
  "images":   { "desk": "desk.moyimg", "home": "home.moyimg" },
  "wallpapers": { "desk": "platinum_desk" },
  "layout":   {},
  "sounds":   {}
}
```

- **Variants are an axis, not two themes.** `colors.dark` and `colors.light`
  under one package keep D1's "one routine, two tables" and today's
  `variant()`/`set_variant()`. The 13 `light_chrome()` branches become token
  differences (phase 2).
- **`images`** are static pictures the theme ships. **`wallpapers`** name
  installed wallpaper carts by id. An id that is not installed falls back to
  the image, then to the default (§4.9).
- **`layout`** and **`sounds`** are reserved in format 1 and have no defined
  keys yet. `layout` is filled by the shell-layout doc; `sounds` waits for #141.
- **Format rule:** a reader refuses a newer `format` major and ignores unknown
  keys. There is no migration pass, which is the store rule.

Key names and the surface grammar are illustrative until phase 1 freezes them;
the SHAPE is the decision.

## 4. The vocabulary — the public contract

The vocabulary is an API. Renaming a role or a part is a breaking change and
bumps `format`. That is the lesson of GTK 3, whose themes styled an internal
node tree that point releases then changed (§10).

### 4.1 Colour roles

**Today's keys are the base vocabulary** (`panel`, `edge`, `title`,
`title_ink`, `accent`, `hilite`, `dim` and the §4.3 semantic roles of
`docs/visual_identity_v1.md`). They stay valid names, so `theme()` and
`ctx.theme.colors()` keep answering what app carts already read. Format 1 adds
the **background/foreground pairs** that are missing (a `_ink` for every fill
role) and the bevel shades (`shade_hi`, `shade_lo`, `shade_dark`). Pairing is
what makes the readability check possible (§7). Inactive and disabled colours
are **derived by a rule** unless a theme sets them, so a kid-made theme sets
about a dozen roles. Every value is a **MOY64 index**; per-theme RGB palettes are
declined (§11).

The six shipped families and the `outline` skin become seed theme packages in
phase 1, re-expressed in this format. There is no reader for the old settings
beyond that: under the no-migrations rule a stored name that no package
provides reads as absent and resolves to the default.

### 4.2 Metrics

Logical pixels, multiplied by the chrome scale each tier already computes (the
#203 floor): `border`, `title_h`, `button`, `pad`, `gap`, `shadow`, `radius` (0
or 1: a cut corner, never a curve). Never absolute screen positions: a theme that
places things by pixel works at one resolution only, which split Rockbox's
catalogue by screen size (§10). Moybyte runs from 320×240 to 1280×800.

### 4.3 Parts × states

**The part list is derived from the toolkit's tables, not invented.** It is the
kinds in `ui.DEFAULT_SPECS` and `ui.DEFAULT_METRICS` (`button`,
`button_play`/`_author`/`_danger`, `chip`, `tab`, `row` and its
`row_menu`/`row_list`/`row_chrome`/`row_cta`, `cell`, `cell_band`, `panel`,
`panel_title`, `toolbar`, `scrollbar`, `status`, `focus`, `dialog`,
`text_field`, `game_btn`, `mini_btn`, …), plus the chrome the WMs and the bar
draw (`window`, `window.title`, `window.button.*`, `bar`, `bar.item`, `desk`,
`desk.icon`) and the home's parts once the shell-layout doc names them. A test
asserts the spec lists every kind the toolkit resolves. Each entry of
`NON_DATA_QUIRKS` is ruled on in phase 1: either it becomes expressible, or it
stays code and is recorded as such.

States are the ladder `runtime/ui.py` already resolves (`rest`, `hover`, `on`,
`hot`, `pressed`, `disabled`), plus `active`/`inactive` for windows. A missing
state follows a documented chain (`pressed → hot → rest`, `inactive → active`).
In the file, parts and states are **nested objects**, never a flattened
`part:state` string. At runtime they are the nested tables `ui.py` already
insists on, because a flattened key allocates a string per widget per frame
(SOURCE: `runtime/ui.py`'s header).

**What "chrome" means** (for the lint in phase 2): the pixels the shell, the WMs
and the toolkit draw around content. Content is exempt: the Paint and sprite
canvases, the map and scene views, code syntax colours and the block editor's
block colours. Those are not themed in format 1.

### 4.4 Surfaces: a one-line grammar, compiled once

A part's look is a short text: an **edge** (`flat`, `raised`, `sunken`,
`etched`, `bevel2` for Windows' two-level edge), a **fill** (`fill ROLE`,
`dither A/B`, `pinstripe A/B`, `gradient A/B` as dithered bands), an optional
`border` and `shadow`. This is how Openbox themes describe every bevel and
gradient in text, and how Windows drew all of Win95 from one `DrawEdge` routine
fed by a few shade colours (§10).

- **The text never reaches a draw.** The loader compiles every surface once
  into a small integer record (a style id, `stid`; not `docs/surface_model_v1.md`'s Surface `sid`) in a C table. A draw calls
  **one gated verb**, `surface(x, y, w, h, stid)`, in the draw context beside
  `rect`, `rectb`, `print` and `pix`. Colours stay MOY64 indices resolved through
  the context's palette at draw, as every gate does.
- **Patterns get a fast lane.** `fillp` already draws a 4×4 two-colour
  screen-anchored pattern natively, but per pixel (a bit test and a 16-bit store,
  `libmoy/moy_pixel.h`). The solid fill path pairs 32-bit stores because the
  naive loop was measured far slower on P4 glass (`moy_gfx_kernels.h`). The
  painter is built on `fillp`'s semantics with an **opaque two-colour pattern
  lane** (four precomputed row words, stored like a solid fill). A pattern then
  costs about what a solid fill does. That lane is libmoy's, so it lands
  **upstream in moy-spec** and is re-vendored. PREDICTED; the gate is phase 3's
  bench.
- **Not the PPA.** Painter fills stay on the CPU: a 1:1 PSRAM fill is
  bandwidth-bound and CPU ≈ PPA (`.claude/rules/boards.md`). The PPA stays for
  upscale composites.
- **The T-Deck's flush pump.** One painter call can cover a large area but
  counts as one gated op, so it calls `gate_pump` in proportion to the rows it
  writes (`device/device_canvas.py`, `GATE_PUMP_EVERY`).

### 4.5 Frame styles and window decoration

Window frames are a **built-in style chosen by name**: `classic` (D1's), `tab`
(a Haiku-style title tab as wide as its title), `platinum`, `win9x`, `flat`.
Parameters: title alignment, a button-order string (`C|T|MZ`: close left,
title, minimise and maximise right), and whether the title strip spans the
window. The styles are our code; a theme only chooses. SerenityOS and xfwm4 work
this way (§10). On the fullscreen tiers the "window" is the app's full-screen
strip, so the same style draws that strip.

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
`mg_text_raw`. This is the kernel doc's "one text path for every app runtime"
(`docs/native_kernel_2026-09.md` §10.6), built once.

- **Chrome text gets its own lane at every scale.** Today scale 1 is libmoy's
  `moy_print` with its compiled-in petme128. The theme font is a separate
  `moy_gfx` text lane that the chrome uses at every scale; libmoy stays the
  carts'. A chrome-side text lane was once reverted and later re-crossed on S3
  A/B numbers (`moy_gfx_kernels.c`'s header records both), so this lane is gated
  on an S3 A/B.
- **The font is a handle in the draw context.** It enters the native print gate
  the way petme128 does today, bound when the context is created, never a second
  Python-side path.
- **Measuring is C too.** The text call takes `align` and `maxw` and returns the
  drawn width, fitting and truncating in the same call, instead of per-frame
  `text_w()` round trips from Python. The Python fallback that draws one `rect`
  per glyph run (`_print_scaled`) routes to C. Scaled text emits per-row runs
  through the span fill, not per-pixel stores.
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
- **The desk and the home (#105).** Where D7's tabbed home sits against "apps
  are windows, games are fullscreen" on the windowed tier (§12.1).
- **Its own kernel mapping.** Bar presets, a dock and tiling grow the WMs the
  kernel's sprint 7 ports, so that doc records its scope in sprint 7.

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
  while `ws._animating(dt)`). That is CLAUDE.md's open live-wallpaper defect. So
  Moy's night, the default, is a still image, or animates only small dirty
  rects.
- **Readability at home.** A wallpaper with its own text or characters
  mid-screen fights the carousel (mocked 2026-09-27 with Space Desktop). Either
  the wallpaper declares itself `home_safe` (a new manifest flag, which phase 6
  lists with its side effects), or the home draws a dither scrim. A scrim is
  applied **at the wallpaper's source resolution, before the upscale blit**
  (`blit565_scale`), which is up to 16× fewer pixels than at 1280×800 (§12.2).
- **Prefer wallpapers that need no VM.** A live Python wallpaper keeps the
  Python VM up, against the kernel doc's memory goal (§4.4 there). Images and
  Lua or wasm wallpapers are preferred; a Python wallpaper stays allowed and is
  charged as such.

## 5. Fallback and inheritance, resolved once

Every key is optional. A theme resolves through `inherits` (one parent, **depth
capped, cycles rejected** at load) and ends at the built-in default, which is
always complete. A missing sprite draws the surface; a missing surface draws the
default for that part; a missing state follows §4.3's chain. So a five-line
recolour is a valid theme and a full Platinum grows from it, as Winamp skins
fell back to the base skin and KDE Plasma themes fall back to Breeze (§10).

**All of it resolves at flatten, never at draw.** Loading a theme flattens it
once per (theme, kind): inheritance, state chains and derived roles collapse
into the per-kind tables the toolkit reads today, where a widget pays one
identity compare and two interned dict lookups (SOURCE: `runtime/ui.py`,
`_FLAT_RING`). The Appearance editor's previews use colours only; they do not
load other themes' assets, since its preview ring holds four entries and would
thrash.

## 6. Who reads it, and how a switch invalidates

- **The toolkit and the WMs** are the only drawing code that resolves parts and
  surfaces. Screens that draw chrome by hand move onto the toolkit (phase 2); a
  literal colour index in chrome (§4.3's definition) fails a lint.
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
  `look_gen`. Every cache that outlives a frame folds `look_gen` into its key:
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

## 7. Safety

Kids will trade themes, so a theme is untrusted input:

- **Data only.** Parse, validate and clamp: indices to 0–63, metrics to ranges;
  an unknown style, part or key falls back or is ignored; a file over the size cap
  is refused; `inherits` depth is capped and cycles are rejected. No scripts, no
  carts inside, no plug-ins (§10: GTK 2 engines, Haiku decorators, and a KDE store
  theme that deleted a user's files).
- **Readability check** at load and in the editor. Every background/foreground
  pair must clear a contrast floor on the MOY64 table, and for a dither or
  pinstripe fill the ink is checked against **both** colours. A failing pair is
  reported and the editor offers the nearest passing index.
- **Persist only after it has rendered once.** A theme that fails to draw is never
  saved, so it cannot return after a reboot, as a malformed SerenityOS theme did
  (§10).
- **A way out that every board has.** Repeated failure is `crash_guard`'s strike
  ledger (the kernel doc's sprint 2), which falls back to the default theme. A
  per-board reset key or gesture is declared in `board.toml`, because a
  "boot-time hold" has no mechanism on the touch-only boards.
- **Two themes are firmware, not files.** The default and a high-contrast theme
  are frozen into the image under reserved ids that no user theme can take.

## 8. Placement against the native kernel

`docs/native_kernel_2026-09.md` moves the toolkit core native in its sprint 6
("one implementation for every app runtime", with style as data) and the WMs in
sprint 7, and turns the app roles into an import-shaped ABI in sprint 5 ("style
tables → a theme handle and token reads"). Theming is shaped to that line so that
nothing built now is thrown away:

| theming piece | built where | why it survives the kernel |
|---|---|---|
| the vocabulary and the package format (§3–§5) | a spec, now | it IS the theme half of the sprint 5 ABI |
| the default look (D1) as token tables and skin data | data | data crosses unchanged |
| toolkit reach (phase 2) | Python, now | the apps stay Python on the kernel's table; every screen on the toolkit is what sprint 6 then speeds up at once |
| the surface painter and pattern lane, 9-slice and mask blits, the chrome text lane | **C**, `moy_gfx` and upstream libmoy | draw kernels are C under either kernel language; "a hot loop lives on one side" (kernel doc §4.1). PREDICTED reuse by sprint 6; its gate is sprint 6 calling these entry points unchanged |
| theme policy: flatten, frame styles, the loader | Python, now, as the reference | how every kernel crossing works: Python pins behaviour, goldens hold it, the crossing ports it and deletes it (kernel doc §4.2) |
| theme assets (fonts, sheets, images) | plain buffers owned by the loader, allocated with `moybuf` alloc and free (`malloc_dma` has no free), in PSRAM (`MALLOC_CAP_SPIRAM`) | they become kernel handles when sprint 2 builds the handle tables; no interim handle mechanism is built ahead (kernel doc §4.4) |

**Memory rule.** On the S3 boards, PSRAM outside the Python heap is the
cart-runtime reserve, and the biggest carts need most of it
(`.claude/rules/boards.md`). So: **one loaded theme at a time**, a hard
per-theme byte cap set in phase 1, no per-tint bakes (§4.6), and every phase
takes the kernel doc's internal-SRAM gate (its §4.6).

**Timing against the kernel's golden gates.** The kernel's sprint 6 gate holds
the toolkit rows' goldens byte-for-byte, and sprint 7's holds the P4 desk's
on-glass suites unchanged. **No pixel-moving theming phase overlaps sprint 6
or 7.** The restyle phases (3 and 4) land before sprint 6
starts, or after 7 closes. After that a new theme is data and moves only its own
goldens.

## 9. Phases

Each phase deletes what it replaces and passes `tools/preflight.sh`. **Pixels
move only in the phases that restyle on purpose (3 and 4)**, each re-baselining
once and naming what moved. Every other phase holds the goldens byte-identical.

**How performance is gated** (from `docs/history/ui_refactor_2026-08.md` §7):
per-cart fps is the *control*; a UI change must not move it at all. The
instrument is `tools/p4_bench.py`'s painted-frame medians and p90s per surface
(≥2 ms is signal, ≥3 ms needs an explanation), and every `* idle` scenario must
capture zero frames. It runs on the boards named in each phase with `--board`;
its scenarios were written for the P4 desk, so phase 2 first adds the
fullscreen tier's surfaces. In CI, per-surface budgets per full draw in
`tests/test_top_bar.py`'s shape: `canvas.gate_counts()` fills and texts,
allocations, `hits.add` calls and idle paint count. Each phase names which meter
sees its cost: DRAW2 and `gate_counts` for fill and text time, PERFCNT for
instructions versus memory, `moy_prof` for the whole image. Per-board verdicts do
not transfer, so every gate names its boards. Every phase also records each
board's image headroom from the build (P4 first) and takes the kernel doc's
internal-SRAM gate (its §4.6).

| phase | builds | gate |
|---|---|---|
| **1 — the spec** | §3–§5 frozen as `format: 1`: roles, parts (derived from the tables), the surface grammar, frame-style names, reserved `layout`/`sounds`; the loader (validate, clamp, flatten, readability check, size cap); the six families and `outline` as seed packages; a ruling per `NON_DATA_QUIRKS` entry. Two golden configs added first: cs 2 over fs 1 (the P4 as shipped) and light on the windowed tier | load → flatten → dump round-trip; a fuzzed loader; the spec lists every kind the toolkit resolves; **goldens byte-identical** |
| **2a — reach** | every chrome surface onto the toolkit or the WM's decoration path, **non-`_base` configs only**; the 13 `light_chrome()` branches become tokens; `look_gen` replaces the identity and name keys (§6); the chrome lint | **goldens byte-identical on every config**; `gate_counts` budgets in CI; p4_bench medians unchanged on the P4 and the Guition S3 |
| **2b — the T-Deck row** | the `_base` branches retired. This reverses the recorded frozen-baseline decision, and `docs/surface_model_v1.md` §5.1 ("executed path provably unchanged") is amended first. The widget call cost is measured warm and on a fragmented heap on an S3 first: a toolkit widget is a wide Python frame that heap-allocates per call (`docs/perf_native_gap_v1.md`, the #63 declines) | one deliberate re-baseline of the 320×240 rows, the moved pixels named; p4_bench on the **T-Deck** within thresholds, with a chrome-frame dev-channel timing; `Hits` stays barred from grids |
| **3 — shapes** | the painter (§4.4) with the pattern lane upstream in moy-spec; `surface()` as a gated verb; frame styles `classic`, `tab`, `platinum`, `win9x`, `flat`; 9-slice and mask blits; **D1's shapes and tokens land** | one deliberate re-baseline, named; a Platinum and a Win95 theme render the golden surfaces with no code of their own; painter µs per call on the P4 and an S3; `tools/p4_conformance.py` painter scenes on the P4 **and** an S3; a pattern fill within a set margin of a solid fill of the same area |
| **4 — type** | the aligned font format, the chrome text lane at every scale, `align`/`maxw` returning width, layout reading line metrics instead of the 8px cell; **Tiny5 lands** as the UI font; licence notices | one deliberate re-baseline, named; the cart `print` path and the Lua C API byte-identical; an S3 A/B of the chrome text lane; `moy_prof` on the T-Deck home and Settings; a fuzzed-font load on S3 glass; legibility on T-Deck and Guition S3 glass |
| **5 — layout** | per the shell-layout doc (§4.8) | that doc's gates, plus: bar and dock strips keep their cache (idle frames zero, strip rebuilt only on a key change) |
| **6 — sprites and sharing** | the sprite sheet (D3); theme packages in the store; the Appearance app as the theme editor; a one-line text form for colour-only themes; `home_safe` and `"type": "theme"` with their side effects (the RUN grid and Editor picker exclusions, other moy hosts, the Zero's store sync) | a kid-made theme round-trips through the editor, the store and a second board; a sprite part no slower than its surface fallback; idle home captures zero frames on all four console boards |

Phases 1 and 2a can start now. Phase 3's painter and phase 4's text lane are the
C pieces the kernel's sprint 6 is predicted to reuse (§8).

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

## 12. Open questions

1. **D7 against #105.** On the windowed tier the desk holds the apps and PLAY
   opens a games-only Library. Does D7's tabbed home replace that Library there
   (with Apps and Make tabs duplicating the desk), or does the tabbed home apply
   to the fullscreen tiers, with the desk's PLAY opening its Games tab? The
   shell-layout doc settles it.
2. **Home readability:** a `home_safe` wallpaper flag, or a scrim the home always
   draws at source resolution (§4.9)?
3. **Sprites in phase 3 or phase 6** (D3 allows either)?
4. **A generator:** a theme derived from a wallpaper or one picked colour, the way
   Material You derives one (§10)?
5. **Theme sounds:** defined with #141, or earlier?

## 13. Sentences this doc will make false

Each is corrected in place by the phase that falsifies it:

| sentence | in | fixed by |
|---|---|---|
| a theme is a `chrome.THEMES` token set and a skin is colours and pads | `runtime/chrome.py`, `runtime/skin.py` headers | phases 1 and 3 |
| "`runtime/skin.py` unchanged" | `docs/native_kernel_2026-09.md` §4.5 | phase 1 |
| the 320×240 row is frozen `_base`-verbatim and does not exercise the toolkit | `.claude/rules/shell.md`, `.claude/rules/rendering.md`, `CLAUDE.md`, `tests/test_shell_goldens.py`, `docs/history/ui_refactor_2026-08.md` | phase 2b |
| the T-Deck tier's "executed path provably unchanged" | `docs/surface_model_v1.md` §5.1 | amended before phase 2b |
| the default look is the "night" token set; "Avoid: bevels and fake plastic controls" as a rule for every look | `docs/visual_identity_v1.md`, `docs/shell_ux_v1.md` | phase 3, scoped to the default theme |
| "Petme128 remains the canonical runtime glyph source for v1" | `docs/visual_identity_v1.md` §5.4 | phase 4 (carts keep it; the chrome moves) |
| `bar_h()` as the whole content contract | `docs/app_api_v1.md`, `runtime/system_api.py` | the shell-layout doc |
| the windowed tier's Library and desk split (#105) | `docs/shell_ux_v1.md` | the shell-layout doc, per §12.1 |

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
| A11 | D7 against #105 | accepted as open | §1 D7, §12.1, §13 |
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
