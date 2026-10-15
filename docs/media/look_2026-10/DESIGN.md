# The 2026-10 look

Anti-aliased type, smooth shapes and soft depth in the chrome; pixel art in
the content (covers, sprites, carts) and in Moy. `draw.py` draws every
picture that `index.txt` lists, through `mockups.py` and `rounds/`;
`THEMES.md` holds the theme model; `GAPS.md` is what the engine lacks;
`docs/look_2026-10.md` orders the build.

## Decisions (owner, 2026-10-15)

- **Modern rendering** replaces the third pixel round (`cb195ae9`,
  `pixel_round3/`, "better and coherent but hand-drawn, still retro"): 2 or 4 bpp anti-aliased
  text, smooth corners, soft shadows, native 1× on the big boards. That
  round's layouts, marks and components stand.
- **Moy is one theme, Day (light, the default) and Night (dark);** Blocks
  and Contrast ship beside it. Luna, Coast, Pico, Bench and Adw, the ricing
  proofs, come later from moybyte-org/carts.
- **The engine is a superset:** smooth or pixel, field by field. Fonts are
  not economised; colours are RGB (GAPS 1); dither is allowed anywhere;
  button kinds replace per-app roles; the handheld text sizes stand.
- **Icon sets** are pixel or smooth, a theme's `icons: {kind, set}`.
- **Moy is the canonical pixel Moy in every theme** and the system menu
  button, outside the focus order. Super opens the system menu, or the
  pause menu over a fullscreen game; Esc closes and steps back.
- **Play is a bare glyph in `value`. Minimize, maximize and close** are
  title buttons in every built-in theme.
- **The editors keep today's layout;** code syntax colours are theme roles
  (the plan's decisions).

The hugging ring is the orchestrator's (2026-10-15, under the owner's
"taste is the orchestrator's").

## The marks: ring, bar, fill

Three marks, each with one meaning, in every theme and on both sizes.

| mark | means | default shape | colour |
|---|---|---|---|
| **ring** | **focus**: where the keys act; exactly one per screen | 2.5–3 px, anti-aliased, a 3 px gap | `accent` |
| **bar** | **current**: the chosen tab, row, Places entry, swatch, tool, window | a 3 px rounded bar under or beside (`current: bar`), or a selection fill (`current: fill`) | `value` |
| **fill** | **primary**: Play, Run, "=" | the `primary` kind; on a bar, a bare glyph in `value` | `value` |

The ring is the engine's: drawn after the part, from the theme's ring
fields; no button kind styles it. A focused window's current mark is its
title strip filled in `value` (Night, Blocks) or a 4 px `value` bar along
its top (Day). The readability check keeps every kind's fill off `accent`.

**Small parts take the hugging ring.** Around a 16 px bar icon the ring is
27 px across: taller than the 26 px bar, over its neighbours, and heavy. On
a part under 28 px (bar icons, title buttons, tool cells) the focus is a
plate in `accent` at 14% (light) or 22% (dark) with a 1.5 px `accent` ring
on its edge, no gap, inside the part's own box, under the glyph
(`focus_marks.png`). It is still the one ring on the screen.

**The Moy button is not in the focus order;** Super reaches it. Open, it
shows the current mark as a plate (`sel`, or `value` mixed into its tile),
never a ring.

## Colour

Each theme sets a neutral ramp (`bg`, `surface`, `raised`, `sunk`, `line`,
`dim`, `text`), an **accent** (focus only), a **value** (current and
primary) with its `value_ink`, and optionally `danger`.

| role | Moy Day | Moy Night | Blocks | Contrast (dark) |
|---|---|---|---|---|
| `bg` | #eceef3 | #0f1127 | #fff3dc (home #ffd23f) | #000000 |
| `surface` | #ffffff | #181b38 | #ffffff | #000000 |
| `raised` | #f4f5f8 | #23274b | #fff8ec | #000000 |
| `line` | #e2e5ec | #2b2f57 | #eadcc0 | #ffffff |
| `dim` | #5f6478 | #a1a6cc | #5b5167 | #e6e6e6 |
| `text` | #15161d | #eef0fb | #1a1530 | #ffffff |
| `accent` | #d45f00 moon orange | #ffc23d amber | #1a1530 ink | #ffe600 |
| `value` | #4c3fd6 indigo | #b6a8f7 lavender | #6a2cff violet | #ffffff |

**Contrast:** text 12:1, `dim` 5.1:1, every mark 3.4:1 or better
(`draw.py` prints every pair); syntax colours are theme roles at 4.5:1.
Bright colour lives in content. Contrast's light ring has a black keyline.

## Type

All SIL OFL-1.1: **IBM Plex Sans** (Moy Day), **Inter** (Moy Night),
**Atkinson Hyperlegible Next** (Blocks and Contrast: look-alike letters
differ, the zero is slashed) and **JetBrains Mono** for the Code tab.
Pixel themes use 1 bpp faces (Galmuri, petme128). Glyphs are hinted, placed
at whole pixels and kerned by pair.

| handheld 320×240 (143 ppi) | size | weight | bpp |
|---|---|---|---|
| title | 19 | Bold | 4 |
| tabs | 13 | SemiBold | 2 |
| status, hints, meta | 12 | Medium, Regular | 2 |
| keycap legends | 11 | SemiBold | 2 |

| desk 1024×600 at 1× (170 ppi) | size | weight | bpp |
|---|---|---|---|
| bar chips and clock, window titles, rows, Calc tape | 15 | Medium, SemiBold, Regular | 4 |
| Kind and Size columns | 14 | Regular | 4 |
| desk labels | 13 | Medium | 4 |
| column heads | 12 | SemiBold | 4 |
| Calc keys, operators | 22, 24 | Medium | 4 |
| Calc result | 44 | SemiBold | 4 |

**2 bpp on the handheld** (`type_specimen.png`) matches 4 bpp at 11–13 px;
1 bpp reads retro. 11 px is the floor, for keycaps. Contrast sets titles
and Calc larger. JetBrains Mono 11 gives 45 columns on the handheld.

## Shapes and depth

| theme | radii (window, card, key) | shadow | outlines |
|---|---|---|---|
| Moy Day | 12, 10, 10 | soft, indigo-tinted, 18%, blur 16, offset 8 | none; operators sit on a sunk key |
| Moy Night | 12, 10, 10 | soft, black, 55%, blur 14, offset 6 | 1 px `line` on windows |
| Blocks | 16, 14, 12 | none | 2 px ink on windows, desk tiles and the display; keys flat |
| Contrast | 4, 4, 4 | none | 2 px white on every part |

Shadow ramps are tinted toward the background's hue. Night dims the
handheld's neighbour cards 30%; light themes never veil art.

## The superset

Every choice below is a theme field (`THEMES.md`), drawn by one renderer.

| choice | field | values |
|---|---|---|
| text | `render.text` | `aa` (2 or 4 bpp); `crisp` (1 bpp pixel faces) |
| corners | `render.shapes` | `smooth`; `stepped` |
| shadows | `shadow` | `soft`; `hard`; none |
| big-board scale | `render.scale` | 1 native; 2 drawn at 512×300 and doubled |
| edges | `edges` | `flat`; `bevel` |
| paints | a paint's `mode` | `smooth`; `steps:N`; `dither` |
| icons | `icons` | `pixel` or `smooth`, a set, a palette swap |

## Button kinds

| kind | means | Calc's keys | derived default |
|---|---|---|---|
| `normal` | a plain action | digits, `.`, ⌫ | `raised` fill, `text` ink, the key radius |
| `primary` | the one primary action on a surface | `=` | `value` fill, `value_ink`, no border |
| `tonal` | a secondary emphasis | ÷ × − + % | `normal` with `value` ink and the operator size |
| `danger` | clears or destroys | C | `normal` with the `danger` ink |
| `toggle` | a two-state part; `on` is the current mark | Paint's tools | as `normal` |

A theme styles each kind (`THEMES.md`), per app if it wants; every kind
derives from `normal`; one `primary` per surface (`calc_all_themes.png`).

## The sizes

The marks, kinds, type roles and icons are the same on both.

| | handheld 320×240 | desk 1024×600 and 1280×800, 1× |
|---|---|---|
| bar | Moy, tabs or ‹ and a title, then status | 40 px: Moy's 36×34 button, window chips, status |
| rows | 20 | 28 in lists, 34 in Places; title strips 44 |
| apps | fullscreen pages | windows sized to their content; Calc keys about 75×61 |
| home | carousel: a 2× hero, 1× neighbours, no page dots | the hero, a details pane, a shelf bleeding off the right edge |
| exit | ‹ back, Esc | the window's ×, or the bar's × in console mode |

Pixel content scales by whole numbers, nearest-neighbour, corners clipped
with anti-aliasing.

## Moy and the icons

**Moy** is the website's 16×16 (`docs/visual_identity_v1.md` §6.5), drawn
as pixel art in every theme at whole scales: 16 px on the handheld's 22×20
tile, 32 px on the desk's 36×34 button (`moy_mark.png`). Its outline is
index 0, which needs GAPS 25.

**Pixel sets** are 16×16 `IconSheet`s of 55 slots
(`icons/pixel/system_icons.moygfx`), lit from the top left, outlined in
dark blue; a theme may swap their palette indices.
**Smooth sets** are 24-unit SVGs with a 2-unit stroke (`icons/smooth/`),
rasterised on the host to 2 or 4 bpp masks and tinted by roles; a package
keeps its sources for a later on-device SVG editor. Redo is undo's mirror
in both (`icons_sheet.png`, `icons_pixel.png`).

**Play** on a bar is a bare glyph in `value`, optically centred, beside undo
and redo (`editor_bar_all_themes.png`); a smooth set draws a disabled redo
at half strength. A two-layer set (Luna) outlines its green Play in the bar's ink.

## Components

- **Bar:** the Moy button, then tabs or ‹ and a title; Wi-Fi, battery and
  clock at the right. The current desk chip is `raised` with a `value`
  underline.
- **Window:** a 44 px header, a line icon, the title, then minimize,
  maximize and close; the chip and a title double-tap still minimise and
  maximise. A window behind keeps its frame, drops its current mark and
  casts a smaller shadow.
- **System menu:** a popover under Moy: Search, Settings, About, Restart.
- **Pause menu:** a card over the veiled, frozen game: Moy, the title and
  "Paused"; Resume, Restart, Options, Quit; the game keeps its integer
  scale behind it.
- **Keycap hints** name only keys that are not self-evident.
- **Rows:** link, info, cycle, toggle (with On/Off), slider, segmented,
  swatches; a caption heads every group; only working settings show.
- **Calc:** a tape on a display inset, the result in `value`, a 4×5 keypad
  in kinds.
- **Files:** Places, each file's picture, Name, Kind and Size; names cut
  with "…", and Kind drops below 11 visible characters.
- **Editors,** in today's layout: one icon a tab, the current one
  labelled, then undo, redo and Play. Code's current line takes the `sel`
  tint and the `value` bar; a long line fades over 12 px. Sprite
  transparency is a grey checker (#6e6e78 / #9a9aa4); the cursor is the
  ring around a black keyline. Playtest is its own window at an integer
  scale.
- **Motion:** the carousel slides in 6 frames, focus in 3, with no colour
  tweens.

## The themes

| theme | ships | identity |
|---|---|---|
| **Moy**, Day | built in, the default | two pale discs on cool grey, indigo current, moon-orange focus, Plex |
| **Moy**, Night | built in | a night sky with a crescent, lavender current and title strips, amber focus, Inter |
| **Blocks** (light) | built in | a sunflower home, a Bauhaus desk, a colour per app tile, ink outlines and focus, Atkinson |
| **Contrast** (dark, light) | built in | black or white, 2 px outlines, yellow focus, larger type |
| Luna, Coast | later | in the style of Windows XP and macOS |
| Pico, Bench, Adw | later | in the style of Picotron, Amiga Workbench and GNOME: the pixel look |

No theme uses Microsoft or Apple artwork, logos or fonts. A theme's home
and desk are a `backdrop`, a few flat shapes (GAPS 9); a user's wallpaper
replaces it.

## The pictures

`index.txt` names every target. Settings, Appearance, the handheld Calc,
the console home and the wallpaper scenes have only `pixel_round3/`
layouts until plan step 2.
