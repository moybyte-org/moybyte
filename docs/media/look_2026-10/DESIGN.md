# The 2026-10 look

Anti-aliased type, smooth shapes and soft depth in the chrome; pixel art in
the content (covers, sprites, carts). `mockups.py` draws every picture that
`index.txt` lists; `THEMES.md` holds each theme's fields and the theme model;
`GAPS.md` is what the engine lacks; `docs/look_2026-10.md` orders the build.

## Decisions (owner, 2026-10-15)

- **The rendering is modern.** The third pixel round (`cb195ae9`, kept in
  `pixel_round3/`) was "better and coherent but hand-drawn, still retro".
  Text is pre-rasterised anti-aliased coverage at 2 or 4 bpp (as LVGL's and
  the Nintendo DS's fonts are), shapes have smooth corners, depth is a soft
  shadow, and the big boards lay the chrome out at native 1×. That round's
  structure stands: its layouts, the three marks and its components.
- **Daylight is the default.** Moy Night, Blocks and Contrast ship beside it.
- **The engine is a superset.** Smooth or pixel is each theme's choice, field
  by field. "The point of the engine is to look good and be customizable."
- **Luna, Coast, Pico, Bench and Adw are the ricing proofs.** They ship
  later as installable themes in the moybyte-org/carts repo, not built in.
- **Fonts are not economised.** Three to five families are fine. Where an
  image cannot hold them, the partition grows or the image sheds weight (the
  Zero first).
- **Button kinds replace per-app roles.**
- **Theme colours are RGB**, quantised to RGB565 (GAPS 1).
- **Dither is allowed anywhere, chrome included,** where it looks good.
- **The handheld text sizes stand**; the owner checked them at real size.

## The rule: ring, bar, fill

Three marks, each with one meaning, in every theme and on both sizes.

| mark | means | default shape | colour |
|---|---|---|---|
| **ring** | **focus**: where the keys act; exactly one per screen | 2.5–3 px, anti-aliased, a 3 px gap | `accent` |
| **bar** | **current**: the chosen tab, row, Places entry, swatch, tool, window | a 3 px rounded bar under or beside (`current: bar`), or a selection fill (`current: fill`) | `value` |
| **fill** | **primary**: Play, Run, "=" | the `primary` button kind | `value` |

The ring is the engine's: it is drawn after the part, from the theme's ring
fields, and no button kind styles it. A focused window's "current" is its
title strip filled in `value` (Moy Night, Blocks) or a 4 px `value` bar
along its top edge (Daylight). The readability check keeps every kind's
fill off `accent`; where the ring is the ink colour (Blocks), the ring and
the fill share no shape.

## Colour

Each theme sets a neutral ramp (`bg`, `surface`, `raised`, `sunk`, `line`,
`dim`, `text`), an **accent** (focus only), a **value** (current and
primary) with its `value_ink`, and optionally `danger`.

| role | Daylight | Moy Night | Blocks | Contrast (dark) |
|---|---|---|---|---|
| `bg` | #eceef3 | #0f1127 | #fff3dc (home #ffd23f) | #000000 |
| `surface` | #ffffff | #181b38 | #ffffff | #000000 |
| `raised` | #f4f5f8 | #23274b | #fff8ec | #000000 |
| `line` | #e2e5ec | #2b2f57 | #eadcc0 | #ffffff |
| `dim` | #5f6478 | #a1a6cc | #5b5167 | #e6e6e6 |
| `text` | #15161d | #eef0fb | #1a1530 | #ffffff |
| `accent` | #d45f00 moon orange | #ffc23d amber | #1a1530 ink | #ffe600 |
| `value` | #4c3fd6 indigo | #b6a8f7 lavender | #6a2cff violet | #ffffff |

**Contrast:** text 12:1 or better, `dim` 5.1:1 or better, every mark 3.4:1
or better against what it sits on; `mockups.py` prints every pair. Bright
colour lives in covers, sprites, syntax, the backdrops and games. Contrast
keeps a light mode (low-vision needs split by polarity); its light ring
carries a black keyline.

## Type

Each theme brings its family, all SIL OFL-1.1: **IBM Plex Sans** (Daylight),
**Inter** (Moy Night), **Atkinson Hyperlegible Next** (Blocks and Contrast:
letters that look alike elsewhere differ, and its zero is slashed) and
**JetBrains Mono** for the Code tab. Pixel themes use 1 bpp faces (Galmuri,
petme128). Fonts are hinted at rasterisation, placed at whole pixels, and
kerned by pair.

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

**2 bpp on the handheld, not a 1 bpp bitmap** (`type_specimen.png`): at
11–13 px a hinted outline puts its stems on whole pixels, so 2 bpp matches
4 bpp and an 8-bit reference, while 1 bpp has uneven stems and reads as the
retro look the owner rejected. 11 px is the floor, for keycap legends only,
in SemiBold. Contrast sets its titles and Calc two to six pixels larger. The Code
tab's JetBrains Mono at 11 px has a 7 px advance: 45 columns on the handheld.
The desk is Android's mdpi (1 dp is 1 px); the 10.1″ shows more of it.

## Shapes and depth

| theme | radii (window, card, key) | shadow | outlines |
|---|---|---|---|
| Daylight | 12, 10, 10 | soft, indigo-tinted, 18%, blur 16, offset 8 | none; operators sit on a sunk key |
| Moy Night | 12, 10, 10 | soft, black, 55%, blur 14, offset 6 | 1 px `line` on windows |
| Blocks | 16, 14, 12 | none | 2 px ink on windows, desk tiles and the display; keys flat |
| Contrast | 4, 4, 4 | none | 2 px white on every part |

Corners are anti-aliased; a ring's coverage allows fractional widths.
Shadow ramps are tinted toward the background's hue, so their RGB565 steps
do not wobble. Moy Night dims the handheld's neighbour cards 30%; the light
themes never veil art.

## The superset

Every choice below is a theme field (`THEMES.md`), and every theme is drawn
by the same renderer.

| choice | field | values |
|---|---|---|
| text | `render.text` | `aa` (2 or 4 bpp); `crisp` (1 bpp, a pixel face at its design size or a whole multiple) |
| corners | `render.shapes`, the radii | `smooth`; `stepped` (the same coverage thresholded); square is radius 0 |
| shadows | `shadow` | `soft` (colour, alpha, blur, offset); `hard` (colour, whole-pixel offset); none |
| big-board scale | `render.scale` | 1: native layout; 2: the desk in 1024×600 metrics drawn at 512×300 and doubled. The handheld is always 1 |
| edges | `edges`, `hi`, `lo` | `flat`; `bevel` |
| paints | a paint's `mode` | `smooth` (a colour per row); `steps:N` (N flat bands); `dither` (an ordered 4×4 blend of the two nearest stops) |

## Button kinds

Five kinds, one per meaning; an app chooses a kind per button, a theme
styles each kind.

| kind | means | Calc's keys | derived default |
|---|---|---|---|
| `normal` | a plain action | digits, `.`, ⌫ | `raised` fill, `text` ink, the key radius |
| `primary` | the one primary action on a surface: the fill mark | `=` | `value` fill, `value_ink`, no border |
| `tonal` | a secondary emphasis | ÷ × − + % | `normal` with `value` ink and the operator size |
| `danger` | clears or destroys | C | `normal` with the theme's `danger` ink |
| `toggle` | a two-state part; its `on` is the current mark | Paint's tools | as `normal` |

Per kind a theme sets `fill` (a paint), `ink`, `border`, `shape` (`round`,
`circle`, `square`), `r`, `font` and `bevel`; each keeps the six states.
Resolution is the theme's `buttons[kind]`, then the optional
`app_buttons[app][kind]`; every kind derives from the resolved `normal`. An
app has at most one `primary` per surface. `calc_all_themes.png` is the
proof: one Calc body, one kind table, nine themes.

## The sizes

**The same on both:** the marks, the kinds, the type roles and the icons.

| | handheld 320×240 | desk 1024×600 and 1280×800, 1× |
|---|---|---|
| bar | tabs or ‹ and a title, then status | 40 px: the system button, window chips, status |
| rows | 20 | 28 in lists, 34 in Places; title strips 44 |
| apps | fullscreen pages | windows sized to their content; Calc keys about 75×61 |
| home | carousel: a 2× hero, 1× neighbours, no page dots | the hero, a details pane, and a shelf that bleeds off the right edge |
| exit | ‹ back | the window's ×, or the bar's × in console mode |

Pixel content scales by whole numbers, nearest-neighbour, its corners
clipped with anti-aliasing.

## Components

- **Bar:** the Moy button, then tabs or ‹ and a title; Wi-Fi, battery and
  clock at the right. The current desk chip is `raised` with a `value`
  underline.
- **Window:** a 44 px header, a line icon, the title and one ×. Minimise is
  the window's chip, maximise a title double-tap. A window behind keeps its
  frame, drops its current mark and casts a smaller shadow.
- **Keycap hints:** a cap, its legend, then a label; only keys that are not
  self-evident are named.
- **Rows:** link (value and ›), info (a value, not focusable), cycle
  (‹ value ›), toggle (with its On/Off word), slider, segmented control,
  swatches. A caption heads every group. Settings shows only settings that
  work.
- **Calc:** a tape on a display inset with the result in `value`, a 4×5
  keypad in the button kinds.
- **Files:** Places (Projects, Carts, Art, Music, Trash), each file's own
  picture, Name, Kind and Size; a name cuts with "…", and Kind drops when
  names would show fewer than 11 characters.
- **Code:** the current line takes the `sel` tint and the `value` bar; a
  line past the edge fades over 12 px.
- **Sprite editor:** transparency is a mid-grey checker (#6e6e78 / #9a9aa4)
  and swatch 0 that checker with a slash; the cursor is the ring around a
  black keyline; the current tool and colour carry the bar.
- **Icons:** 24-unit line glyphs with a 2-unit stroke, drawn at 14–16 px on
  the handheld and 18–24 on the desk. **Motion:** the carousel slides in 6
  frames, focus in 3, with no colour tweens.

## The themes

| theme | ships | identity |
|---|---|---|
| **Daylight** (light) | built in, the default | a cool grey desk with two pale discs, white surfaces, indigo current and primary, moon-orange focus, Plex |
| **Moy Night** (dark) | built in | a night-sky ramp, a flat crescent on the desk, lavender current and filled title strips, amber focus, Moy on the system button, Inter |
| **Blocks** (light) | built in | colour in blocks: a sunflower home, a cream desk with Bauhaus shapes, a colour per app tile, ink outlines and ink focus, violet current, Atkinson |
| **Contrast** (dark, light) | built in | black or white, 2 px outlines, yellow focus, larger type |
| Luna, Coast | moybyte-org/carts, later | in the style of Windows XP and macOS: gradients, a dock, frosted glass, per-app button colours |
| Pico, Bench, Adw | moybyte-org/carts, later | in the style of Picotron, Amiga Workbench and GNOME: the pixel look (1 bpp faces, stepped corners, hard shadows, dither, copper bands, bevels, 2× chrome) and a header-bar desk |

No theme uses Microsoft or Apple artwork, logos or fonts.

## Backdrops

A theme draws its home and desk as a short list of flat shapes
(`backdrop`, GAPS 9), not an image, measured in thousandths of the surface
so one list fits every glass, and drawn into the one wallpaper bake the
shell keeps. A user's wallpaper still replaces it.

## The pictures

`index.txt` names every target: the home and the desk in each of the nine
themes, `calc_all_themes.png` and `type_specimen.png`. Settings, Appearance,
the Code and sprite editors, the desk's console home and the wallpaper
scenes have no modern picture yet; their layouts are `pixel_round3/`'s, and
plan step 2 draws them in this rendering.
