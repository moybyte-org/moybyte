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

## The marks: focus, current, primary

Three marks, each with one meaning, in every theme and on both sizes. Focus
and current are theme properties (`THEMES.md`); the engine draws them, so no
button kind styles them, and a theme changes its marks by changing two
fields (owner, 2026-10-15: "make it a property, easy to change later").

| mark | means | drawn by | colour |
|---|---|---|---|
| **focus** | where the keys act; exactly one per screen, never in a window behind | `focus.style` per size class | `accent` |
| **current** | the chosen tab, row, Places entry, file, tool, window chip | `current.style` | `value` |
| **primary** | Play, Run, "=" | the `primary` kind; on a bar, a bare glyph in `value` | `value` |

**One accent.** `value` is `accent` unless a theme sets its own: Moy sets
only `accent`, so its focus, current plates and primary fills are one
colour, the Accent setting's (owner, 2026-10-15). Blocks and Contrast keep
a `value` of their own, and picking an accent sets both roles. Contrast
offers no accent picks (`accents`, owner 2026-10-19): its one yellow focus
is the promise it keeps.

**Focus** is `focus = {style, card, small, color, ink, ring, plate, lift,
edge, underline}`. The engine picks the style by the part's size class: a
`card` (a carousel card, a desk tile: 48 px and up), a `small` part (under
28 px: bar icons, title buttons, tool cells) or any other part (rows,
buttons, fields), which takes `style`; `card` and `small` absent mean
`style`. The six styles:

| style | draws | parameters |
|---|---|---|
| `ring` | an outline outside the part; on a small part, a tinted plate and a 1.5 px ring inside its own box | `ring.w`, `ring.gap`, `ring.alpha`; `outline` |
| `plate` | the part tinted in `accent` | `plate.alpha` (light, dark) |
| `lift` | the part grown and raised over a neutral offset shadow, with a 1 px rim; on a card, its chrome grown and its picture raised at its own clean side, on the card's plate (owner call 2026-10-17: never a non-clean scale) | `lift.grow`, `lift.rise`, `lift.rim` (a colour or role, and its alpha) |
| `edge` | a bar the full height of a row on its leading edge, over a light `accent` plate across the part; under a part not three times wider than tall | `edge.w`, `plate.alpha` |
| `underline` | a bar under the glyph, inside the part's box | `underline.w` |
| `invert` | the part filled in `accent`, its ink flipped | — |

`ink` (`keep` or `accent`) is what a focused glyph or label does; `invert`
always flips it. `outline` (a width and a colour) rings a ring from outside
and an inverted part from inside, for an accent that alone would not hold
3:1 on its ground. Every style but `lift` on a card is drawn after the part's
fill and before its content.

**Current** is `current = {style, color, alpha, row, ink}`: a `plate`
(`color`, by default `value`, at `alpha` for light and dark, 0.6 of it in a
window behind; a solid plate, alpha 1, is its outline behind), a `fill`
(`sel` and `sel_ink`, `sel_back` behind) or a `bar` (the 3 px `value` bar
under a tab or beside a row). `row` says how a row wears it: the style's
own, the row's outline, or an underline under its label. On an inverted
part a current bar takes the flipped ink. Focus and current on one part
stack, and the shapes tell them apart, not the colours: in Moy they are
one colour.

**Blocks' rule** (owner, 2026-10-19): a ring is the chosen value in a set
of swatches, an underline is the current label (a tab, a tool, a theme
card's name, a Places entry, a file), and a fill is focus.

| theme | focus: card / part / small | current |
|---|---|---|
| Moy Day | `lift`, an `accent` rim at 0.6 / `edge` 4 px on a 0.12 plate / `underline` 3 px | `plate`, `value` at 0.14 |
| Moy Night | `lift`, an `accent` rim at 0.6 / `edge` 4 px on a 0.14 plate / `underline` 3 px | `plate`, `value` at 0.24 |
| Blocks | `ring` 3 px / `invert` / `invert` | `bar`, a row's label underlined |
| Contrast | `ring` 3 px, gap 2 / `invert` / `invert`; the light mode's 2 px black `outline` | `plate`, solid `value` (the ink) with `value_ink` text |
| Luna | `ring` 3 px, gap 0 / `invert` / `invert` | `fill` (XP's blue) |
| Coast | `ring` 3.5 px at 0.6 / `invert` / `ring` | `fill` (macOS's selection) |
| Pico | `invert` / `invert` / `invert` | `bar` |
| Bench | `invert` / `invert` / `invert` | `fill` (Workbench's complement) |
| Adw | `ring` 2 px at 0.5, gap 0 / `plate` / `ring` | `fill` |

**Visibility.** WCAG 2.2's Focus Appearance (2.4.13) asks for a change of
3:1 over at least a 2 px perimeter's area. Moy's edge bar, underline and
lift are quieter marks than a ring, found at a glance by their shape and
colour (`accent` against the surface is 7.2:1 in Day and 8.2:1 in Night,
`focus_marks.png`); the lift's rim carries it over a dark wallpaper, its
shadow over a light ground. `invert` passes on every part: Contrast, the
accessible choice, keeps it, with a ring on cards, and in light mode a
black outline round both, as its yellow alone is 1.3:1 on white.

**The Moy button is not in the focus order;** Super reaches it. Open, it
shows the current mark as a plate (`sel`, or `value` mixed into its tile),
never a focus mark.

## Colour

Each theme sets a neutral ramp (`bg`, `surface`, `raised`, `sunk`, `line`,
`dim`, `text`), an **accent** (focus, and by default current and
primary), a **value** (current and primary; `accent` where unset) with its
`value_ink`, and optionally `danger`.

| role | Moy Day | Moy Night | Blocks | Contrast (dark) |
|---|---|---|---|---|
| `bg` | #eceef3 | #0f1127 | #fff3dc (home #ffd23f) | #000000 |
| `surface` | #ffffff | #181b38 | #ffffff | #000000 |
| `raised` | #f4f5f8 | #23274b | #fff8ec | #000000 |
| `line` | #cfd3dc | #33385f | #ddcfb8 | #ffffff |
| `dim` | #5f6478 | #a1a6cc | #5b5167 | #ffffff |
| `text` | #15161d | #eef0fb | #1a1530 | #ffffff |
| `accent` | #4c3fd6 indigo | #b6a8f7 lavender | #1a1530 ink | #ffe600 |
| `value` | `accent` | `accent` | #6a2cff violet | #ffffff |

**Contrast:** text 12:1, `dim` 5.1:1, every mark 6.3:1 or better
(`draw.py` prints every pair); syntax colours are theme roles at 4.5:1.
Bright colour lives in content. Contrast's light focus has a black outline.
**The structure ramp** (owner, 2026-10-19): a text field's outline holds
3:1 or better on its ground (`field.border`), and the rest of the
structure -- hairlines, an Off toggle's track, a slider's rest, a key's
plate and its border, a hint's keycap -- about 1.5:1, a part on a focused
row's plate held to it there too; an empty field shows its placeholder in
the `disabled` ink. Contrast's hierarchy is weight and size, not a dimmer
ink: its `dim` is its `text`. A toggle's knob is one colour in a mode (white,
the ink in a dark one), the state on the track.
`accent` against the current plate is 3:1 or better wherever a theme's
current is a `plate` (Day 5.9, Night 5.2).

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
| Moy Day | 12, 10, 10 | soft, indigo-tinted, 18%, blur 16, offset 8 | none; operators sit on a `sunk` key |
| Moy Night | 12, 10, 10 | soft, black, 55%, blur 14, offset 6 | 1 px `line` on windows; operators sit on a key one step above `raised` |
| Blocks | 16, 14, 12 | none | 2 px ink on windows, desk tiles and the display; keys flat |
| Contrast | 4, 4, 4 | none | 2 px white on every part, the desk's tiles and the Moy button framed always |

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

**Pixel sets** are 16×16 `IconSheet`s, a tile a slot
(`native/moy_ui/icons/pixel/system_icons.moygfx`), lit from the top left, outlined in
dark blue; a theme may swap their palette indices.
**Smooth sets** are 24-unit SVGs with a 2-unit stroke (`native/moy_ui/icons/smooth/`),
rasterised on the host to 2 or 4 bpp masks and tinted by roles; a package
keeps its sources for a later on-device SVG editor. Redo is undo's mirror
in both (`icons_sheet.png`, `icons_pixel.png`).

**Play** on a bar is a bare glyph in `value`, optically centred, beside undo
and redo (`editor_bar_all_themes.png`); a smooth set draws a disabled redo
at half strength. A two-layer set (Luna) outlines its green Play in the bar's ink.

## Components

- **Bar:** the Moy button, then tabs or ‹ and a title; Wi-Fi, battery and
  clock at the right. The current desk chip is `raised` and takes the
  current mark.
- **Window:** a 44 px header, a line icon, the title, then minimize,
  maximize and close; the chip and a title double-tap still minimise and
  maximise. A window behind keeps its frame, drops its current mark and
  casts a smaller shadow.
- **System menu:** a popover under Moy: Search, Settings, About, Restart.
- **Modals dim what they cover** (owner, 2026-10-19): the system menu, a
  card's menu, About and a dialog wash the content under the bars in the
  look's `scrim` (about 40%); the pause menu washes the whole frozen game
  (on the desk the content under the bar). A
  popover hung from its control -- an app's picker, a bar widget's calendar,
  Wi-Fi or power -- does not.
- **Pause menu:** a card over the veiled, frozen game: Moy, the title and
  "Paused"; Resume, Restart, Options, Quit; the game keeps its integer
  scale behind it.
- **Keycap hints** name only keys that are not self-evident, and what they
  do to the focused part (a stepper's ←→ Change, a link's Enter Open); on
  the desk the legends are in the text's 15 px face on 20 px caps, where
  the 12 px caption face would read a size down.
- **Rows:** link, info, cycle, toggle (with On/Off), slider, segmented,
  swatches; a caption heads every group; only working settings show.
  Settings ends in an About group; CHROME SIZE is Appearance's.
- **Calc:** a tape on a display inset, the result in the plain ink
  (owner, 2026-10-19), a 4×5 keypad in kinds; a disabled key's plate fades
  with its ink.
- **Files:** Places, each file's picture, Name, Kind and Size; names cut
  with "…", and Kind drops below 11 visible characters.
- **Editors,** in today's layout: one icon a tab, the current one
  labelled, then undo, redo and Play. Code's current line takes the `sel`
  tint and the `value` bar; a long line fades over 12 px. Sprite
  transparency is a grey checker (#6e6e78 / #9a9aa4); the cursor is the
  theme's ring around a black keyline. Playtest is its own window at an
  integer scale.
- **Motion:** the carousel slides in 6 frames, focus in 3 (a lift grows
  over them), with no colour tweens.

## The themes

| theme | ships | identity |
|---|---|---|
| **Moy**, Day | built in, the default | two pale discs on cool grey, an indigo accent, Plex |
| **Moy**, Night | built in | a night sky with a crescent, a lavender accent, Inter |
| **Blocks** (light) | built in | a sunflower home, a Bauhaus desk, a colour per app tile, ink outlines and focus, Atkinson |
| **Contrast** (dark, light) | built in | black or white, 2 px outlines, yellow focus, larger type |
| Luna, Coast | later | in the style of Windows XP and macOS |
| Pico, Bench, Adw | later | in the style of Picotron, Amiga Workbench and GNOME: the pixel look |

No theme uses Microsoft or Apple artwork, logos or fonts. A theme's home
and desk are a `backdrop`, a few flat shapes (GAPS 9); a user's wallpaper
replaces it. Over a dark wallpaper a light theme's on-backdrop ink turns
light and its handheld bar takes the desk bar's fill.

## The pictures

`index.txt` names every target. Settings, Appearance, the handheld Calc,
the console home and the wallpaper scenes follow `pixel_round3/`'s
layouts (`rounds/round11.py`). The Editor's desk layouts and its
handheld Config, Blocks, Map, Scene and Music tabs are
`rounds/round12.py`'s, and `EDITOR_LAYOUT.md` is their design. The three
readings of maximise and the resizable panes are `rounds/round14.py`'s
(`MAXIMISE_VARIANTS.md`).
