# Theming — one data package re-skins the whole OS (2026-09)

**Status: DRAFT, 2026-09-27.** The owner's decisions from the 2026-09-27
art-direction and theming sessions are recorded in §1; the rest is the design
they imply. Nothing here is scheduled until the owner accepts it.
**Depends on:** `docs/native_kernel_2026-09.md` (the toolkit and the window
managers move below the app verb table; §8 here maps every theming piece onto
its sprints). **Relates to:** `docs/visual_identity_v1.md` (the default look),
`docs/app_api_v1.md` (`ctx.theme`, the user-app `theme()` global),
`docs/surface_model_v1.md` (one invalidation mechanism), `runtime/skin.py`
(skins as data), #177 (hover), #146/#147 (charm).
Claims are labelled as in `docs/surface_model_v1.md`: **SOURCE** (read out of
code, with the file), **ESTIMATED** (with how), **PREDICTED** (with the gate).

---

## 0. The thesis

**A theme is one small, data-only package that re-skins every layer of the
OS** — colours, sizes, how surfaces are drawn, window decorations, bar layout,
fonts, icons, cursor, sounds and wallpapers — and it is a cart, so a kid can
install, share and remix one like any game. Drawing code never names a colour
or a shape: it asks for a **role** or a **part in a state**, and the active
theme answers with MOY64 indices, sizes and a named built-in frame style.
Nothing in a theme can run.

The range it must cover is the owner's: the default Moybyte look (§2) plus
looks in the family of classic Mac OS Platinum, a riced tiling desktop,
Windows 9x and XFCE/LXQt — bevels, pinstripes, gradients, flat panels,
bottom taskbars, docks and different fonts, not recolours only.

## 1. Decisions (owner, 2026-09-27)

| # | decision |
|---|---|
| D1 | The default look is **"Open Machine, refined"**: the Workbench structure (1px black outlines, 2px hard shadows, calm light/dark chrome over the PICO-8 base colours), the **Tiny5** UI font, desk icons as tiles with the **label in a pill under the tile**, window title strips **left-aligned with the app's identity stripe and a square close on the right**, and **Moy's night** (stars, a low hill, a moon, Moy on the hill) as the default wallpaper. Light and dark are ONE drawing routine and two token tables. |
| D2 | Themes change the OS **extensively** (§0's range), not only colours. |
| D3 | A theme can carry **paintable sprites** (9-slice frames, glyphs, a desktop tile) — in the first or second version; the format reserves them from version 1. |
| D4 | **Apps follow the theme.** Settings, Calc and every shipped app restyle with it, because they draw through the one toolkit; carts read it through `theme()` (§6). |
| D5 | **Layout: both.** A theme sets the default bar layout and window mode; the user can override either in Settings, and the override survives a theme switch. |
| D6 | **Themes bring wallpapers**, assigned per **slot** — `desk` and `home` — which may differ; the user can override each slot. |
| D7 | **The home is one layout on every board**: the cover carousel with tabs (Games / Mine / Apps / Make), themeable like everything else, with its own wallpaper slot. |

Rejected in the same sessions, so they are not re-proposed (§11 has the rest):
MOY64 grey chrome replacing the PICO-8 base colours, cartridge-shell or
notched library cards, labels inside the desk tiles, and Pixel Operator
replacing Tiny5 as the UI font. Each was mocked and judged worse.

## 2. What exists today

**SOURCE.** Theming is already data in three layers; what it lacks is reach.

- **Colour tokens:** `runtime/chrome.py` — `THEMES` (the dark sets), `THEME_LIGHT`,
  the semantic roles of `docs/visual_identity_v1.md` §4.3 resolved by
  `theme_colors(name, variant)`, and `runtime/appearance.py`'s `set_theme`.
- **Widget style:** `runtime/ui.py`'s `DEFAULT_SPECS` (kind × state → a
  `(field, ink, edge)` triple of token specs) and `DEFAULT_METRICS`, with the
  state ladder `disabled > pressed > hot > on > hover > rest`; `runtime/skin.py`
  installs a skin as a delta over both (`default`, `outline`).
- **Icons:** a themeable 16×16 `IconSheet` (`runtime/editors_sheet.py`), baked
  from `chrome._ICON_ART` or loaded from `system_icons.moygfx`.
- **Wallpapers** are carts (`kind: "wallpaper"`), and apps reach the backdrop
  through `ctx.wallpaper`.
- **Apps and carts read tokens** through `ctx.theme` and the user-app `theme()`
  global (`docs/app_api_v1.md`).

What stops a Platinum or Win95 theme today:

1. **Most drawing does not go through the toolkit.** ESTIMATED (grep of
   `runtime/`, 2026-09-27): about 420 raw fill/line/text calls against about 50
   toolkit widget calls, spread over ~25 modules. And at 320×240 / font scale 1
   the shell takes frozen hand-rolled branches (`if not ws.layout._base`) that
   bypass the toolkit entirely (`.claude/rules/rendering.md`).
2. **A skin can only colour and pad.** There is no frame style (raised, sunken,
   etched), no fill pattern (dither, pinstripe, gradient), no skinnable window
   decoration or bar.
3. **One font, fixed cell.** petme128 8×8 (`runtime/font.py`); the C text kernel
   takes only that glyph layout (`native/moy_gfx/modmoy_gfx.c`, `text`); layout
   arithmetic is written in multiples of the 8px cell (ESTIMATED: ~700 `fs * N`
   sites in `runtime/`).
4. **Layout is fixed per tier:** the bar, the taskbar and the window mode are
   code, not choices.

## 3. The package

A theme is a cart of `kind: "theme"`: a folder with a manifest, a `theme.json`
and optional assets. Every key is optional (§5).

```json
{
  "format": 1,
  "name": "Platinum",
  "inherits": "moy-light",
  "colors":   { "surface": 6, "surface_ink": 0, "title": 48, "title_ink": 0,
                "shade_hi": 7, "shade_lo": 50, "shade_dark": 0,
                "select": 13, "select_ink": 7, "desk": 22 },
  "metrics":  { "border": 1, "title_h": 14, "button": 11, "pad": 4, "gap": 4, "shadow": 1 },
  "surfaces": {
    "window.title:active": "pinstripe shade_lo/shade_hi, border",
    "button:rest":          "raised, fill surface, border",
    "button:pressed":       "sunken, fill shade_lo, border",
    "desk":                 "dither desk/23"
  },
  "frame":    { "style": "platinum", "title_align": "center", "buttons": "C|T|Z" },
  "layout":   { "bar": "top-menu", "windows": "float" },
  "fonts":    { "ui": "ui.fnt", "title": "title.fnt", "mono": "mono.fnt" },
  "sprites":  "parts.moygfx",
  "icons":    "icons.moygfx",
  "sounds":   { "click": 3, "open": 7 },
  "wallpapers": { "desk": "platinum_desk", "home": "platinum_home" }
}
```

The key names and the surface grammar are illustrative until §9's phase 1
freezes them; the SHAPE is the decision: roles, metrics, surfaces by part and
state, a named frame style, a layout choice, fonts, assets.

## 4. The vocabulary — the public contract

The vocabulary is an API. Renaming a role or a part is a breaking change and
bumps `format`. That is the lesson of GTK 3, whose themes styled an internal
node tree that point releases then changed (§10).

### 4.1 Colour roles

About 30 roles, each a **MOY64 index**, in **background/foreground pairs**
(`surface`/`surface_ink`, `select`/`select_ink`, `title`/`title_ink`,
`button`/`button_ink`, …) plus the bevel shades (`shade_hi`, `shade_lo`,
`shade_dark`) and the signal roles the default already has (`focus`, `play`,
`author`, `danger`). Pairing is what makes a readability check possible (§7).
Inactive and disabled colours are **derived by a rule** unless a theme sets
them, so a kid-made theme sets about a dozen roles, not thirty.

The palette stays MOY64 for every theme. A theme chooses its subset; per-theme
RGB palettes are declined for now (§11), because on the shared-canvas tier the
shell and the game are one raster.

### 4.2 Metrics

Logical pixels, multiplied by the chrome scale the tier already computes:
`border`, `title_h`, `button`, `pad`, `gap`, `shadow`, `radius` (0 or 1: a cut
corner, never a curve). Never absolute screen positions: a theme that places
things by pixel works at one resolution only, which split Rockbox's catalogue by
screen size (§10). Moybyte runs from 320×240 to 1280×800.

### 4.3 Parts × states

The parts are the toolkit's widget kinds plus the chrome's pieces: `button`
(and its verb variants), `chip`, `tab`, `row`, `cell`, `field`, `scrollbar`,
`scrollbar.thumb`, `panel`, `toolbar`, `window`, `window.title`,
`window.button.close|min|max`, `bar`, `bar.item`, `dock.item`, `desk`,
`desk.icon`, `home.card`, `home.plate`, `home.tab`. The states are the ladder
`runtime/ui.py` already resolves — `rest`, `hover`, `on`, `hot`, `pressed`,
`disabled` — plus `active`/`inactive` for windows. A missing state falls back
along a documented chain (`pressed → hot → rest`, `inactive → active`).

### 4.4 Surfaces: a one-line grammar

A part's look is a short text: an **edge** (`flat`, `raised`, `sunken`,
`etched`, `bevel2` for Windows' two-level edge), a **fill** (`fill ROLE`,
`dither A/B`, `pinstripe A/B`, `gradient A/B` as dithered bands), an optional
`border` and `shadow`. This is how Openbox themes describe every bevel and
gradient in text, and how Windows drew all of Win95 from one `DrawEdge` routine
fed by four shade colours (§10). One built-in painter draws every combination at
every scale, with no artwork.

### 4.5 Frame styles and window decoration

Window frames are a **built-in style chosen by name** — `classic` (D1's), `tab`
(a Haiku-style title tab as wide as its title), `platinum`, `win9x`, `flat` —
with parameters: title alignment, a button-order string (`C|T|MZ`: close left,
title, minimise and maximise right) and whether the title strip spans the
window. The styles are our code; a theme only chooses one. SerenityOS and xfwm4
work this way (§10).

### 4.6 Sprites (D3)

One sheet per theme at fixed cell sizes: title buttons per state, checkbox and
radio per state, a 9-slice frame per part that wants one, the cursor and a
desktop tile. The fixed cells are what made Winamp's classic skins paintable in
any paint program (§10). A part with a sprite draws the sprite; a part without
one draws its surface (§4.4). Glyph-style sprites may be 1-bit masks tinted by a
role, so one sheet follows the colours.

### 4.7 Fonts

Up to three roles: `ui`, `title`, `mono`. A theme font is a bitmap font file;
the default ships Tiny5 (D1) and petme128 stays the carts' font (the cart API's
`print` is unchanged). This is the same work as the kernel doc's "one text path
for every app runtime" (`docs/native_kernel_2026-09.md` §10.6) and is built once
(§8).

### 4.8 Layout (D5)

A small validated choice, never coordinates: `bar` (`top`, `top-menu`,
`bottom-taskbar`, `top+dock`), `windows` (`float`, `tile`) and the dock's
presence. The theme's value is the default; the user's Settings override is
stored beside it and wins until reset. Tiling is a window-manager mode the
theme can default to, not a thing a theme implements.

### 4.9 Wallpapers (D6)

A theme bundles its wallpaper carts and assigns them to the `desk` and `home`
slots; the user can override each slot. Two rules:

- **Readability at home.** The home draws its carousel over the wallpaper, so a
  wallpaper whose own text or characters sit mid-screen fights it (mocked
  2026-09-27 with Space Desktop). A wallpaper either declares itself
  `home_safe`, or the home draws a dither scrim over it. Which one is §12's
  first open question.
- **Prefer wallpapers that need no VM.** A live Python wallpaper keeps the
  Python VM running, which undoes the kernel doc's memory goal on the launcher
  (`docs/native_kernel_2026-09.md` §4.4). A theme's wallpapers should be static
  images or Lua/wasm carts; a Python wallpaper stays allowed and is charged as
  such.

## 5. Fallback and inheritance

Every key is optional. A theme resolves through `inherits` (one parent) and ends
at the built-in default, which is always complete. A missing sprite draws the
surface; a missing surface draws the default for that part; a missing state
follows §4.3's chain. So a five-line recolour is a valid theme and a full
Platinum grows from it, as Winamp skins fell back to the base skin and KDE
Plasma themes fall back to Breeze (§10).

## 6. Who reads it

- **The toolkit and the window managers** are the only drawing code that
  resolves parts and surfaces. Screens that draw by hand today move onto the
  toolkit (§9 phase 2); a screen that still names a literal colour in chrome is
  a failing test, not a style nit.
- **Shipped apps** read roles through `ctx.theme` and draw widgets through the
  toolkit, so they restyle with every theme (D4).
- **User apps and carts** read the live tokens through `theme()` — already
  granted without permission (`docs/app_api_v1.md`). A cart may follow the
  theme; a game is never themed against its will, and the Player surface stays
  the cart's. That is the "don't theme our apps" lesson turned around: the
  chrome is fully themeable, game pixels are not.
- **A theme switch is one signal.** `appearance.set_theme` already rebinds the
  token dict, and its identity keys the launcher's statics (SOURCE:
  `runtime/appearance.py`). The full theme resolves into ONE immutable object
  that is rebound on switch, and everything that caches pixels keys on it. That
  feeds `docs/surface_model_v1.md`'s one dirty protocol; it is not a second
  invalidation mechanism, and the per-cache pokes `set_theme` does today go
  away.

## 7. Safety

Kids will trade themes, so a theme is treated as untrusted input:

- **Data only.** Parse, validate, clamp: indices to 0–63, metrics to ranges, an
  unknown style or part falls back, a file over the size cap is refused. No
  scripts, no code, no plug-ins (§10: GTK 2 engines, Haiku decorators and a KDE
  store theme that deleted a user's files).
- **Readability check** at load and in the editor: every background/foreground
  pair must clear a contrast floor on the MOY64 table; a failing pair is
  reported and the editor offers the nearest passing index.
- **Persist only after it has rendered once.** A theme that fails to draw is
  never saved, so it cannot return after a reboot, as a malformed SerenityOS
  theme did (§10).
- **A way out:** a boot-time hold resets to the default theme, and a
  high-contrast theme ships that no user theme can replace.

## 8. Placement against the native kernel

`docs/native_kernel_2026-09.md` moves the toolkit core native in its sprint 6
("one implementation for every app runtime", with style as data and
`runtime/skin.py` unchanged) and the window managers in sprint 7, and turns the
app roles into an import-shaped ABI in sprint 5 ("style tables → a theme handle
and token reads"). Theming is shaped to that line so that nothing built now is
thrown away:

| theming piece | built where | why it survives the kernel |
|---|---|---|
| the vocabulary and the package format (§3–§5) | a spec, now | it IS the theme half of the sprint 5 ABI; the kernel binds it, it does not redesign it |
| the new look (D1) as token tables and skin data | data, now | data crosses unchanged (§4.5 of the kernel doc) |
| toolkit coverage (§9 phase 2) | Python, now | the apps stay Python on the kernel's table; every screen on the toolkit is what sprint 6 then speeds up and restyles at once |
| the surface painter, 9-slice and tinted glyph blits, variable-width text | **C, in `moy_gfx`**, now | draw kernels are C already and stay C under either kernel language; "a hot loop lives on one side" (kernel doc §4.1); sprint 6's native toolkit calls the same primitives |
| theme policy: part → surface resolution, frame styles, bar layouts, the loader | Python, now, as the reference | how every kernel crossing works: the Python version pins behaviour, goldens hold it, the crossing ports it and deletes it with a parity test (kernel doc §4.2) |
| theme assets (fonts, sprite sheets) | C buffers behind handles, in PSRAM | handles, not objects (kernel doc §4.3); a switch frees them; they never pin a Python heap area |
| wallpapers | carts, preferring VM-free forms | §4.9 |

**Timing against the goldens.** The kernel's sprint 6 gate is "pixel goldens
identical on every row that exercises the toolkit". The default look's restyle
(D1) moves those goldens on purpose, so it lands **before sprint 6 starts or
after it closes, never during**. After it lands, a new theme is data and moves
only its own goldens.

## 9. Phases

Each phase deletes what it replaces, passes `tools/preflight.sh`, and
re-baselines goldens only deliberately (`tests/test_shell_goldens.py`), naming
which pixels moved and why.

| phase | builds | gate |
|---|---|---|
| **1 — the spec** | §3–§5 frozen as `format: 1`: the role list, the part list, the surface grammar, the frame styles, the layout values; the loader with validation, fallback and the readability check; the default look expressed as a theme | a round-trip test (load → resolve → dump) and a fuzzed loader; every role and part the toolkit asks for is in the spec, asserted |
| **2 — reach** | every chrome surface draws through the toolkit or the WM's decoration path; the frozen `_base` branches retired; a lint that fails on literal colour indices in chrome | goldens on all six configs re-baselined once, with the diff explained; the T-Deck row now exercises the toolkit; on-glass perf on the T-Deck unchanged within #66's noise |
| **3 — shapes** | the surface painter in `moy_gfx` (edges, dither, pinstripe, dithered gradient); frame styles `classic`, `tab`, `platinum`, `win9x`, `flat`; D1 lands as the default | a Platinum and a Win95 theme render the golden surfaces with no code of their own; host == device parity on the painter |
| **4 — type** | the bitmap font format (per-glyph widths, height, baseline), the C renderer, a `text_w()` measure, and layout reading line metrics instead of the 8px cell; Tiny5 as the default UI font | a theme changes the UI font with no layout breakage on every config; the cart `print` path byte-identical |
| **5 — layout** | bar presets (`top`, `top-menu`, `bottom-taskbar`, `top+dock`), window mode as a theme default with the Settings override (D5), the home (D7) and its wallpaper slot (D6) | a theme switches the bar and window mode; the user override survives a switch |
| **6 — sprites and sharing** | the sprite sheet (D3), theme carts in the store, the Appearance app as the theme editor, a one-line text form for colour-only themes | a kid-made theme round-trips through the editor, the store and a second board |

Phases 1–2 can start now. Phase 3's painter and phase 4's renderer are the C
pieces the kernel's sprint 6 reuses (§8).

## 10. What other systems taught (research, 2026-09-27)

About eighteen systems were studied for this doc; the lessons that shaped it:

- **Data themes spread; code themes die.** Winamp's classic skins (fixed sprite
  sheets, a base-skin fallback) and Kaleidoscope's bitmap schemes gathered tens
  of thousands of community themes. GTK 2 engines, Haiku decorators and
  Windows XP visual styles (signed, patched around) did not, and a KDE store
  "global theme" ran a script that deleted a user's files.
  <https://winampskins.neocities.org/base.html> ·
  <https://blog.davidedmundson.co.uk/blog/kde-store-content/>
- **Bevels are a routine, not art.** Windows' `DrawEdge` and Openbox's
  `raised bevel2 gradient vertical` draw every classic bevel from a few shades.
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawedge> ·
  <http://openbox.org/help/Themes>
- **A closed parts × states list is the stable contract.** Qt's `QStyle`,
  Windows' class/part/state and Mac OS 8's Appearance Manager brushes; GTK 3's
  CSS over internal nodes broke on point releases.
  <https://doc.qt.io/qt-6/qstyle.html> ·
  <https://blog.gtk.org/2019/01/21/theme-changes-revisited/>
- **Pair every background with its text colour** (Windows `HIGHLIGHT` /
  `HIGHLIGHTTEXT`, Material's `primary` / `onPrimary`, KDE's colour sets), which
  is what makes a readability check possible.
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsyscolor>
- **Fallback chains make small themes valid** (KDE to Breeze, Openbox's state
  chain, the freedesktop icon theme's `Inherits`).
  <https://develop.kde.org/docs/plasma/theme/>
- **Absolute coordinates split a catalogue by resolution** (Rockbox needed a
  rescaler). <https://github.com/TmosDHD/Rockbox-Theme-Rescaler>
- **Validate before persisting** (a malformed SerenityOS theme survived
  reboots). <https://github.com/SerenityOS/serenity/issues/4534>
- **Colours as palette indices, shared as a string** (Picotron's `.theme`, one
  base64 line on its forum). <https://www.lexaloffle.com/bbs/?pid=144631>

## 11. Declined

- **Code in themes** in any form (scripts, plug-in decorators, callbacks) — §7.
- **Per-theme RGB palettes** — for now; MOY64 subsets cover the target looks,
  and the shared-canvas tier is one raster.
- **Absolute placement** in themes — §4.2.
- **Theming carts' pixels** — §6.
- From the look sessions (D1): MOY64 grey chrome instead of the PICO-8 base
  colours, cartridge-shell or notched cards, labels inside the desk tiles, and
  Pixel Operator replacing Tiny5.

## 12. Open questions

1. **Home readability:** a `home_safe` wallpaper flag, or a dither scrim the home
   always draws (§4.9)?
2. **Sprites in phase 3 or phase 6** (D3 allows either)?
3. **A generator:** a theme made from a wallpaper or one picked colour, the way
   Material You derives one (§10), as a later addition to the editor?
4. **Theme sounds:** a slot per event now, or when the sound identity work
   (#141) lands?

## 13. Sentences this doc will make false

Corrected in place by the phase that makes them false, not annotated:

| sentence | where | corrected by |
|---|---|---|
| the default look is the "night" token set and the Library shelf | `docs/visual_identity_v1.md`, `docs/shell_ux_v1.md` | phase 3 (D1) and phase 5 (D7) |
| "Petme128 remains the canonical runtime glyph source for v1" | `docs/visual_identity_v1.md` §5.4 | phase 4 |
| "Avoid: bevels and fake plastic controls" (as a rule for every look) | `docs/visual_identity_v1.md` §3 | phase 3, scoped to the default theme |
| the 320×240 / 1× row does not exercise the toolkit | `CLAUDE.md`, `.claude/rules/rendering.md`, `tests/test_shell_goldens.py` | phase 2 |
| a theme is a `chrome.THEMES` token set and a skin is colours and pads | `runtime/chrome.py`, `runtime/skin.py` headers | phases 1 and 3 |
