# The 2026-10 look: modern pixel

Restraint in the chrome, colour in the carts. References: Playdate,
Switch/3DS home, AnalogueOS, OP-1. The pictures are drawn by `mockups.py`
(see `index.txt`); what the engine lacks is in `GAPS.md`.

## The rule: ring, bar, fill

There are three marks, each with one meaning. They hold in every theme and on
both sizes, and nothing else uses these colours as marks.

| mark | means | colour | where |
|---|---|---|---|
| **ring**: 2 px, with a 1 px gap | **focus**: where the keys act; exactly one per screen | accent | cards, keys, previews, the sprite cell; on a list row, the ring sits around a raised row |
| **bar**: 2 px | **current or selected**: the chosen theme, swatch, tab, tool, Places entry, file or code line | value | under a tab, a swatch, a preview or a segment; left of a row or a tool |
| **fill** | **primary action**: Play, Run, "=" | value | the one primary button on a surface |

The current window's title strip is the window's bar, filled in `value`. A
toggle's track is that control's own state, not a mark. In Paper the ring and
the fill are both ink but keep their different shapes, so a focused "5" (a
ring with a gap) never looks like "=" (a filled key).

## Colour

Each theme has six neutral steps, an **accent** (focus) and a **value**
(current and primary). The values are RGB targets quantised to RGB565
(GAPS 1).

| role | Moy dark | Moy light |
|---|---|---|
| `bg` | #11122a | #efecf8 |
| `surface` | #1c1e40 | #faf9fd |
| `raised` | #2a2d55 | #ffffff |
| `group_edge` | #5c5f96, the moonlit top | #8a80c4, the shadow line below |
| `line` | #30335c | #dcd7ee |
| `dim` | #9196c2 | #5a5487 |
| `text` | #eceaf8 | #1d1747 |
| `accent` | #ffc53d moon amber | #d45f00 moon orange |
| `value` | #b3a6f2 Moy lavender | #4b3bb8 deep violet |

**Each accent has a dark and a light value.** The Accent row's swatches show
the current mode's values, so the checked swatch is the colour the ring draws
in. In light mode, Moon is #d45f00: 3.4–3.9:1 on light surfaces, orange and
not ochre.

**Contrast:** text 4.5:1 or better (measured 11–17:1, `dim` 4.7–6.8:1,
comments 5.5:1); every non-text mark 3:1 or better (rings, bars, keycap
outlines, tracks, group edges 3.0–3.2:1). `mockups.py` prints them. Bright
colour lives only in covers, sprites, syntax, Moy, the wallpaper and games.

## Moy's identity, without a wallpaper

Paper wins by committing to a material. Moy commits to the night sky:

1. **Sky:** `bg` carries a sparse star tile (96 px, 14 points) on every
   screen.
2. **Moonlight:** raised things and groups catch a 1 px lit top edge.
3. **Moon glow:** the focus ring has a dithered halo; only Moy's glows.
4. **Moy himself:** the mascot is the system button in every bar.
5. **Lavender is Moy's, amber the moon's:** one amber mark per screen.

**Moy light is dawn, not dark mode with the lights on.** Its paper is pale
lavender and its ink deep indigo. The stars stay as lavender sparkles, groups
sit on an indigo shadow line, and the moon's focus turns orange. Game art is
never veiled.

## Type

| step | font | cap | use |
|---|---|---|---|
| title | Galmuri11 Bold | 11 (×2 for the big-home title and keypad legends, ×3 for the Calc result) | names, numerals |
| body | Galmuri9 | 9 | rows, tabs, titles, buttons, files |
| caption | Galmuri7 | 7, `dim` | metadata, headers, keycap legends |
| code | GalmuriMono9, Moybyte cut | 9, 5 px advance | the Code tab: 57 columns |

All Galmuri (OFL-1.1, Lee Minseo), mixed case; petme128 stays the carts'
`print`. **The Moybyte cut** gives Mono's `l` a flag and tail and `1` a base
(`l 1 I | ( )` never alike) and Bold's `−` the width of `+`.

**Contrast** sets body in Galmuri11 and captions in Galmuri9. Its keycaps
grow to 15 px, its file names cut with an ellipsis, and its Files window
drops the Kind column. **Studio** sets every chrome label in capitals:
tabs, titles, places, kinds, hints and buttons. Content (file names and
code) keeps its case.

## Surfaces, elevation, corners

- **Lines are rare.** A list is one surface with inset hairlines, and boxes
  are never nested.
- **Elevation is a hard pixel drop:** 3 px under the current window and the
  focused card, 2 px under a window behind. Paper uses a dithered drop.
  Studio and Contrast are flat and framed.
- **Corners:** radius 3 for cards, windows and groups, 2 for keys and
  buttons. Studio uses 0.

## What differs between the sizes

**The same on both:** tokens, fonts and their sizes, icons, components, the
focus language and the 2× drawing scale on big glass.

| | handheld 320×240 | 1024×600, 512×300 logical |
|---|---|---|
| list rows | 20 | 14 in windows (Files: 16 rows, Places sidebar, Name/Kind/Size), 17 in a settings window |
| apps | fullscreen pages | windows sized to their content (Calc 166×214, Files 282×268) |
| home | carousel: a 2× hero and 1× neighbours, no page dots | the same hero crop, a details pane, and a 64 px shelf that bleeds off the right edge |
| exit | ‹ back | the × in the bar (console mode) or the window's × |
| desk | none | icon column and window chips |

## Components

- **Bar:** the Moy button, then tabs or ‹ and a title; Wi-Fi, battery and
  clock on the right. On the desk it holds window chips: the current chip is
  `raised` with a value underline.
- **Keycap:** an outlined cap, its edge in `dim` with a heavier bottom, a
  Galmuri7 legend, and a caption label after it. Hints name only keys that
  are not self-evident; the trackball's arrows are not shown.
- **Row:** link (value and ›), info (a value with no chevron), cycle (‹ value
  ›), toggle (with its On/Off word), slider, segmented control or swatches. A caption heads every
  group.
- **Settings shows only settings that work.** Unbuilt settings are absent,
  not disabled, and the About group lists real facts.
- **Window:** a 20 px strip, outline icon, title and one ×. The current
  window's strip is filled in `value`; a window behind has a sunk strip, a
  `dim` title and a body dimmed 25%.
- **Calc:** a paper tape on an LCD inset, with the result at ×3 in `value`.
  The keypad is 4×5, with legends at about 50% of the key's height and gaps
  of 6. Digits, C and ⌫ are in `text`, operators in `value`, "=" is the
  primary fill, and focus is the ring.
- **Sprite editor:** transparency is a mid-grey checker (#6e6e78 / #9a9aa4,
  neither a palette colour), and swatch 0 is that checker with a slash. The
  cursor is the focus ring around a black keyline; the current tool and
  colour carry the bar.
- **Code:** the current line takes the `sel` tint and the value bar. A line
  that runs past the edge fades out over 12 px, with no glyph that could be
  read as code. The symbol keys use Galmuri11 Bold.
- **Files:** each file shows its own picture: its cover, its app icon, a
  wallpaper miniature, or a title tile. Every column has data on every row.

**Icons:** a 12×12 grid, 1 px strokes, two tones plus one value cluster (32
in `system_sheet.png`). **Motion:** the carousel slides in 6 frames, focus in
3, with no colour tweens.

## Themes: four, each the same on both sizes

| theme | identity |
|---|---|
| **Moy** (dark, light; default) | night-sky field with stars, moonlit edges, moon-glow focus, the Moy button, lavender for current |
| **Paper** (light only) | 1-bit ink on paper, cartridge plates, inverted current strips, dithered drops, a dot grid, undimmed neighbours; windows behind cast no drop |
| **Studio** (dark only) | OP-1: true black, square, flat, 1 px frames, white for current and primary, one orange for focus, every chrome label in capitals |
| **Contrast** (dark, light) | 2 px outlines, yellow focus, larger type, white or black current fills |

**Contrast keeps its light mode:** low-vision needs split by polarity, so one
polarity fails half its users. In light mode its yellow ring carries a black
keyline.

**Dropped:** Platinum (its identity is windows, which the handheld never
shows), Machine, Win95 and Outline. Indigo, Berry, Forest and Slate become
Moy accent and tint presets.

**Paper is light only:** a dark Paper would be a second Contrast.

**The picker** uses the same 64×48 preview on both sizes, drawn in the
current mode, or in the theme's only mode with "Light only" or "Dark only".
Every preview has a caption line, so the layout never shifts. The current
theme follows the rule: its name in `text` and the value bar under it. A
ring appears only when the strip has focus.

## The default wallpaper

The wallpaper is a cart, in MOY64 only, laid out per glass (GAPS 12). The
wordmark is gone.

- **The bar stays on `bg`** over any wallpaper.
- **Sky:** black, dithering to blue only at a low horizon under the title,
  so it never cuts behind the card or its text; the hints sit on the hill.
- **Stars** keep out of the bar and the shell's quiet rectangles.
- **Moy sleeps on the ridge in a corner nothing covers:**
  - **Handheld:** bottom-left, below the left neighbour card and clear of the
    metadata.
  - **Desk:** bottom-right, whole. The WM's default window placement keeps
    clear of the wallpaper's declared subject rectangle (GAPS 12).
- **The moon** sits top-right.
- **Desk labels** sit on `bg` plates.
