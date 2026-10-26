# What the 2026-10 look needs that the engine lacks

Each gap gives the capability, its shape, its cost in developer days
(excluding golden re-bakes, which come with each step) and its frame-time
risk. Frame figures here are estimates of what to measure; the measurements
go to **#66** for the S3 boards and **#58** for the P4 boards.
"Owner call" marks a choice that is the owner's.

**Frame-time risk in one line per tier.** P4 desk: bandwidth-bound, so
text and corners stay in the noise on the known-background path, and soft
shadows during a window drag, text over a backdrop and the backdrop raster
are what to measure (gap 6, 9). S3 boards: CPU-bound by calls, so a string
stays one call into C, and the hero's shadow and title never redraw on a
carousel frame.

## Foundation

**1. Theme colours in RGB, and the theme fields.**

- **Shape:** roles take `#rrggbb`; the loader quantises each to RGB565 into
  indices 64–127, which only the shell names. A cart's colour stays `& 63`,
  so cart pixels cannot change. `moy_theme.c`'s three clamps to 63 move,
  `PAL565` grows to 128 entries in every copy, `docs/theming_2026-09.md`
  §4.10's derivation and §7's readability check run on RGB, and the package
  `format` is bumped.
- **Roles:** the mockups' names are vocabulary, not new roles: `bg`,
  `text`, `line`, `dim`, `danger` and `sel` (`selection`) map onto roles
  that exist. New, each with a derivation rule: `value`, `value_ink`,
  `sunk`, `lcd`, `lcd_ink`. §7 holds every non-text role to 3:1 against
  what it marks, and warns when `primary` and `tonal` resolve alike.
- **New fields:** the theme model's groups (`THEMES.md`): `render`, the
  radii, `focus`, `shadow`, `edges`, `backdrop`, `win`, `bar`, `hhbar`,
  `current`, the launcher and list fields, `buttons`, the display and keycap
  fields. An absent field takes the model's default.
- **Cost:** 6–8 days. **Frame:** none; 128 bytes per table.

**2. A font file with 1, 2 and 4 bpp glyphs (MFNT version 2).**

- **Where version 1 stops** (`native/moy_gfx/moy_text.h`): one glyph box of
  at most 16×16 (the 22 and 44 px Calc faces and petme128 at 3× exceed it),
  1 bpp only, an 8 KB file cap, 4 slots.
- **Version 2:** a bpp field; an 8-byte record per glyph (offset, box,
  bearing, advance) with row-major packed coverage; a kern-pair table of 3
  bytes per pair. It stays one glyph per byte: Latin-1 plus a byte map into
  0x80–0x9F for `− × ÷ … ← → ↑ ↓ •`.
- **The converter,** `tools/make_font.py`: FreeType through Pillow, hinted,
  at fixed sizes, as `mockups.py` rasterises. 2 bpp at 13 px and below, 4 bpp
  at 15 and above, 1 bpp for a crisp face at any size. A family that lacks a
  glyph gets an ASCII stand-in, and the converter says so (Cantarell has no
  U+2212).
- **Cost:** 6 days: the converter 2, the reader and validator with fuzzing
  of the new fields 2.5, tests and rasters 1.5. **Frame:** none.

**3. The alpha glyph lane.**

- **Over a known solid background** (rows, keys, the bar, chips: almost all
  chrome): the lane builds a 4- or 16-entry RGB565 ramp from ink to
  background once per string; each pixel is a lookup and a store, the cost
  class of today's 1 bpp lane. The toolkit passes the surface it paints.
- **Over unknown pixels** (desk labels and the home's text over a backdrop,
  overlays on a cart): a read, a three-channel blend and a store.
- **Text shadow:** the lane runs again at an offset and an alpha, for the
  strings that ask.
- The P4's PPA blends A4/A8 masks in hardware with a fixed setup cost per
  transaction: for the 44 px result and shadows, never single glyphs.
- **Cost:** 4 days with the host, web and device twins, a
  `tools/p4_conformance.py` case and goldens. **Frame:** a full desk of text
  is about 80 KB of PSRAM traffic over unknown pixels, under a millisecond
  on the P4; on the S3 boards a string stays one call. A chrome text lane is
  gated on an S3 A/B (`docs/theming_2026-09.md` §4.7).

**4. The font set.**

- **Families:** Plex (Moy Day), Inter (Moy Night), Atkinson Hyperlegible
  Next (Blocks, Contrast), JetBrains Mono (the Code tab), each with a
  `THIRD_PARTY.md` notice. A board takes only its size set: about 40 KB per
  family on the handheld boards, 85–100 KB on the desk boards, 5 KB for the
  mono. Calc's 22 and 44 px faces ship only the keypad's 20 glyphs.
- **Image:** the build prints the headroom; where a board cannot hold the
  set, its partition grows or the image sheds weight, the Zero first (owner,
  2026-10-15).
- **RAM:** each file is copied into PSRAM once, at boot or theme change.
  Drawing from memory-mapped flash would share the S3's cache with code.
- **Slots and roles:** `MOY_FONT_SLOTS` goes from 4 to 10; a `caption` role
  joins `ui`, `title` and `mono`; names cut with "…" on `moy_text_fit_px`.
- **The Code tab** on JetBrains Mono 11 px: a 7 px cell, 45 columns (10 px
  gives 6 px and 53 columns: owner call). Cursor, selection and tap
  hit-tests at the new cell: 1 day.
- **Cost:** 4 days. **Frame:** a Code-tab redraw draws fewer, larger
  glyphs; measure a scroll on the T-Deck.

## Rendering

**5. Anti-aliased shapes.**

- **Shape:** a 4 bpp quarter-circle coverage table per radius (128 bytes at
  r = 16). A ring is the outer table minus the inner, so widths can be
  fractional. Interiors are the span fill; only corner pixels blend, over a
  known background through gap 3's ramp.
- **Variants:** per-corner flags (a window rounded on top only); `stepped`
  corners, the table thresholded at theme load; `circle`, the largest circle
  in a cell, a pill past 1.5 cells.
- **Cost:** 4 days in `moy_gfx` with the canvas twins. **Frame:** negligible.

**6. Shadows.**

- **Soft:** a 9-slice 4 bpp mask per (radius, blur), baked at theme load
  and blended in a ramp tinted to the background's hue. **Hard:** an offset
  fill.
- **Cost:** 2.5 days.
- **Frame, the P4's real risk:** a soft shadow is a read-modify-write band
  about three blurs wide; for the Calc window that is about 280 KB of PSRAM
  traffic, a few milliseconds whenever the window moves. At rest it belongs
  on the cached desk layer; during a drag it may drop to a smaller blur or
  none. On the handheld the hero card's shadow lives in the card layer,
  redrawn when the carousel settles, not on its 6 frames.

**7. Paints.** A paint is a colour or up to four stops, in `smooth` (one
RGB565 colour per row), `steps:N` or `dither` (the ordered pattern lane,
libmoy's `fillp`, with two colours per row) mode, on title strips, bars,
buttons, keys, tiles and backdrops. A gradient spans at least one 565 step
per 8 rows or reads as stepped. **Cost:** 2.5 days. **Frame:** a span fill;
cached with its strip.

**8. Bevel edges:** `hi`/`lo` lines on windows, buttons, tiles, keycaps and
the display, from `style.json`'s `shade_hi` and `shade_lo`. **Cost:** 0.5
day. **Frame:** none.

**9. Backdrops: a `backdrop` theme field.**

- **Shape:** `backdrop.home` and `backdrop.desk` are lists of up to 16
  shapes (`fill`, `band`, `circle`, `ellipse`, `rrect`, `pie`, `wave`), in
  thousandths of the surface, each with a paint and an alpha. A record is a
  few bytes; the seed themes' lists are 22–57 bytes.
- **No new buffer:** the shapes draw into the one wallpaper bake
  (`docs/theming_2026-09.md` D6), and the home's band copy is filled from
  it as now. A malformed list reads as no backdrop; the loader caps shapes
  and colours and clamps coordinates, fuzzed with `tests/test_theme_loader.py`'s
  corpus.
- **Cost:** 3 days with the host and web twins. **Frame:** none at rest; a
  full raster on theme load and mode switch (about 1.2 MB of PSRAM writes on
  the P4); where a board keeps no full bake, the damage under the carousel's
  cards is redrawn clipped.

**10. Native 1× layout on the big boards.**

- **Shape:** the P4 boards lay the chrome out at 1× with per-class metrics
  (handheld, desk) instead of one scale factor: the bar 40, rows 28, Places
  34, title strips 44. `chrome.text_scale_floor` answers 1 there. Files, Get
  Carts, the Storybook and the editors move from 8 px cells to line metrics,
  and default window sizes are re-planned. Pixel content keeps whole-number
  scaling; carts keep their canvas.
- **Cost:** 6–9 days. **Frame:** the same framebuffer and bandwidth, with
  more, smaller glyphs: a call-count question for gap 3.

**11. Whole-chrome 2× per theme** (`render.scale: 2`): the desk keeps its
1024×600 metrics and draws into a 512×300 system surface the compositor
doubles. **Cost:** 2–3 days. **Frame:** a quarter of the fill bandwidth and
one PPA doubling pass on the P4.

## Parts

**12. The three marks** (DESIGN, the marks): `focus` and `current` as theme
properties, and `primary` (gap 13). The loader takes `focus = {style, card,
small, color, ink, ring, plate, lift, edge, underline}` and `current =
{style, color, alpha}` with the model's defaults. One focus part draws the
mark by size class (`card` 48 px and up, `small` under 28 px, any other
part) with six renderers: `ring` (today's, with the in-box ring on a small
part), and five new ones, `plate`, `lift`, `edge`, `underline` and `invert`;
each draws after the part's fill and before its content and returns the
content's ink. One current part draws `plate`, `fill` or `bar`. Every part
draws its state through them and nothing else; a test in
`tests/test_ui_style_data.py` holds that `accent` reaches a part only
through `focus` and through `value`, which defaults to it, and the
readability check holds `accent` 3:1 or better on a `plate`
current. **Cost:** 5 days (the property and its loader 1, the five new
renderers 2, the current plate 0.5, the callers that drew a ring or a bar
1.5). **Frame:** none, but `lift` on the carousel's hero grows the card and
its shadow over the 3 focus frames, so the card layer redraws them:
`--home` on tdeck.

**13. Button kinds** (DESIGN, "Button kinds"): `play` becomes `primary` and
`author` `tonal` in `style.json` and the ten callers; `cell` is the `toggle`
kind; `fill`, `ink`, `border`, `shape`, `r`, `font` and `bevel` per kind in
`moy_ui`'s draw; the `app_buttons` lookup by app id; at most one `primary`
per surface, held on the goldens. **Cost:** 2.5 days. **Frame:** none.

**14. New parts:** `slider`, `seg`, `keycap`, a `toggle` with its On/Off
word, an `info` row and a group caption. A setting that is not built has no
row. **Cost:** 3.5 days. **Frame:** none.

**15. Window parts:** header styles `plain`, `fill`, `bar`, `grad`; control
sets `three`, `close`, `lights`, `xp`, `amiga`, `adw`; frame width; title alignment
and icon. Every built-in theme's set is minimize, maximize and close as
title buttons with hit targets of 28 px or more; a ricing theme's set may
drop the first two, and the window chip and a title double-tap minimise and
maximise either way. **Cost:** 5 days. **Frame:** cached with the window
strip.

**16. Bar parts:** the tile (36×34 on the desk, 22×20 on the handheld,
the pixel Moy on it, an open plate, no focus stop), start-style, glyph and
workspaces system buttons; `tab`, `button` and no chips; a tray segment; an `app_title`
widget; a centred clock slot; the handheld bar styles and pill tabs.
**Cost:** 3 days. **Frame:** none.

**17. Neighbour dimming:** a darken kernel with cached dimmed covers, for a
theme with `dim_neighbours` (Moy Night). **Cost:** 1 day. **Frame:** zero
during motion; 32–75 KB of covers, which the Player's fit check must be
able to release on the T-Deck.

**18. Icon sets.** A theme's `icons` is `{kind, set}`; every set fills the
same slots (`native/moy_ui/icons/pixel/slots.txt`).

- **Pixel:** a 16×16 `IconSheet` (`native/moy_ui/icons/pixel/system_icons.moygfx`, drawn
  by `native/moy_ui/icons/pixel/pixel_icons.py`), editable in the sprite editor as today.
  Its palette indices are roles, so a theme recolours it with a swap (Bench
  takes the blue ramp down a step on its white bar).
- **Smooth:** 24-unit SVG sources (`native/moy_ui/icons/smooth/<set>/`, a `.fill` layer
  where a set has two) rasterised on the host at the sizes in use into 2 bpp
  (16 px and under) and 4 bpp masks in an MFNT version 2 file (about 20 KB a
  set), tinted by roles at draw time. A theme package carries its sources,
  for an on-device SVG icon editor later.
- **Both:** redo is generated as undo's mirror; Play is a bare glyph in
  `value`; a slot a set lacks falls back to the default set.
- **Cost:** 4 days (the field and loader, the host rasteriser in the
  build, the swap, the mirror). **Frame:** a mask blend per icon, as text.

**19. Adaptive columns:** a list drops its optional column (Kind) when
names would show fewer than 11 characters, on every theme. **Cost:** 0.5
day.

## Layouts

**20. Homes.** The handheld carousel: tabs in the bar, a 2× hero, title and
metadata, a keycap hint strip, no page dots (2–3 days). The desk's console
home: the hero beside a details pane (source, last played, play count, and
Play, Change, Copy) over a shelf that bleeds off the right edge, the bar's
× back to the desk (3 days). Last played and play count are new store
records (0.5 day). A manifest description and genre, and a best score, are
moy-spec's: owner call.

**21. Covers:** the whole cover on every card at a clean scale (owner call
2026-10-17, after a side-by-side render; the mockups' per-cover crops,
`CROPS`, are declined), anti-aliased clipped corners, a generated cover
(the title's initial on a colour block) for a cart without one. **Cost:** 1.5 days.

**22. App relayouts:**

| layout | cost |
|---|---|
| Settings: only working rows, the About facts, a caption on every group | 1 day |
| Appearance: one preview size on both boards, a fixed caption line, the current mark | 1 day |
| Calc: a tape history (new behaviour), the display inset, the 4×5 keypad in kinds | 2 days |
| Files: Places (Projects, Carts, Art, Music, Trash), file pictures, ellipsis | 3 days |
| the sprite editor: the grey checker, the ring cursor, the current mark on tool and colour | 1 day |
| the editors in today's layout: the tab row with a bare Play, the playtest window, the code overflow fade | 1.5 days |
| the `code_*` roles as theme roles: out of the content exemption, held to 4.5:1 on the code surface | 1 day |
| desk icons as tiles with labels | 0.5 day |

**23. Wallpaper per glass:** a wallpaper cart reads W and H, receives the
shell's quiet rectangles (the bar, the home's text, the icon column) and
declares a subject rectangle the WM's default placement keeps clear of.
**Cost:** 2.5 days. **Frame:** none.

## Themes

**24. The seed set:** Moy (Day, the default, and Night), Blocks, and
Contrast (dark and light). `night`, `platinum`, `win95`, `machine`, `outline`,
`indigo`, `berry`, `forest` and `slate` are deleted; a stored name of a
deleted seed falls back to the default, and nothing migrates. Indigo, Berry, Forest and Slate return as accent presets with
a dark and a light value each. **Cost:** 4 days, plus the goldens.

## The system button and Moy

**25. Moy's outline.** The canonical Moy (`docs/visual_identity_v1.md`
§6.5) is outlined in index 0, and an `IconSheet` keys index 0 clear, so the
sheet loses the outline (`moy_mark.png` draws it). The fix is a per-sheet
clear index in the `.moygfx` header (16 for the system sheet, 0 by default,
so every existing sheet reads as now). **Cost:** 1 day with the sprite
editor's swatch. **Frame:** none.

**26. The system menu and the pause menu.**

- **Keys:** Super (the GUI key of a BLE keyboard, the Windows key of a USB
  one) opens the system menu from every surface, on its release so it can
  lead chords (owner, 2026-10-21); over a fullscreen cart it
  opens the pause menu instead; Esc closes either and steps back. The
  T-Deck's own keyboard has no Super key; its chord is an owner call.
- **The Moy button** leaves the focus order (Super reaches it); tapping it
  opens the menu, and its open state is the plate of gap 16.
- **The pause menu** (Resume, Restart, Options, Quit): the Player holds the
  cart's clock, input and audio while it shows, and Resume gives the cart
  its next frame as if no time passed. Options holds volume and controls; a
  cart's own entries need a moy-spec verb (owner call).
- **Cost:** 4–5 days. **Frame:** the card over a frozen frame, drawn once;
  the cart's first frame after Resume is measured on tdeck and p4.

## Later: the ricing themes (moybyte-org/carts)

Luna, Coast, Pico, Bench and Adw ship as installable theme packages, each
bringing its own font family, so they cost the image nothing. They need, on
top of gaps 1–26:

**27. Theme packages with fonts** from the store: the package carries its
MFNT files, the loader validates them as it validates a seed's. **Cost:**
1.5 days.

**28. Frosted glass over a static backdrop:** the backdrop under a bar's
rect blurred once at load, in place in the bake, then tinted. No window
passes under a bar. **Cost:** 1.5 days. **Frame:** none.

**29. Vibrancy as a tint:** a sidebar takes the backdrop's mean colour
mixed toward white, computed at load. **Cost:** 0.5 day.

**30. A dock:** a floating, inset, centred, rounded bottom bar with launcher
tiles and a running dot per open app; the inset, centring and radius are
new layout fields (`docs/shell_layout_2026-10.md`). **Cost:** 2–3 days.

Live blur behind moving windows is declined: every window move on the P4
desk would recompose it.

## The Editor's layout (step 30)

**31. Panes and their parts** (`EDITOR_LAYOUT.md`, concept B): the pane
(a card on the Editor's ground, a 32 px header of tabs or a name, 32 px
tool cells, the grow button, a second tool row when the first is full,
compact tabs, the focused pane's `value` edge, a dim cursor in a pane
without the keys); the app's row in fixed title and Play slots, at the
top edge of a maximised (full-screen) window with the WM's minimise,
restore and close at its right (E-05, every app); the workspaces and their
movable dividers (R-01, `MAXIMISE_VARIANTS.md`'s Resize); the Game tab beside Art's map
and a blocks cart's scene; the signature popover; the sound panels (the
two-octave pitch graph with its note readout, each step's wave, the
volume ladder, the song's slots, a sound's sketch); the block palette and
"+ add block"; the sprite's flag toggles and the picker tool; the World
pane that follows the tool, tagged stamps and tag chips; the Log tab
(after moy-spec's `log()`); sixteen glyph slots in every set (`stop`,
`pause`, `grow`, `loop`, `grid`, `paste`, `pointer`, `lock`, `resize`,
`volume`, `picker`, `jump`, `brush`, `flag`, `log`, `screen`). Each tab's `*Layout`
lays out in a pane's rect instead of the content rect
(`docs/studio_2026-09.md` §10's step 1), and a focus move between panes
never commits (a tab switch commits synchronously today). **Cost:** about
19–22 days (panes and the row 3, the dividers 2, the tabs at a pane's size 4, the
workspaces and the Game tabs 2, the sound panels 3, the block palette 1,
the World pane and tagged stamps 1.5, flags and the picker 1.5, the
popover 2, the commit change 1, the glyphs 0.5; the Log tab waits for
moy-spec). **Frame:** a game running in the Run slot beside a pane being
typed in is two damage rects a frame on the P4 desk, and Settings'
2× game is 640 × 480 a frame; measure both on p4 and guition_p4 before
building on them.
## The apps' restyle (step 31)

Paint, My Art, Get Carts, Notes and Storybook (`APPS_RESTYLE.md`,
`rounds/round13.py`) on the public verbs need, beyond gaps 1–26:

**32. Icon slots:** `text`, `shapes`, `to_front`, `to_back`, `spray` (a
can and three dots), `picker` (a dropper; Paint draws its drip in the
colour under the cursor), `pan`, `cut`, `paste` (a clipboard holding a
page), `layout`, `slides`, `background`, `check`, `bubble`, `warn`, `down`,
`align_l`, `align_c`, `align_r` and the transitions `trans_none`,
`trans_fade`, `trans_swipe`, in every set (the default set's sources are
in `rounds/round13.py`'s `NEW_ICONS`). **Cost:** 1 day.

**33. A toast:** `ui.toast(text, kind)`, a pill in `text` with the
ground's ink and a leading icon over the content's foot, gone after two
seconds or the next key; one at a time, a new one replacing it. **Cost:**
0.5 day. **Frame:** the band under it repaints as it goes.

**34. A card's badge:** `ui.card`'s `o.badge`, an icon slot on a
`surface` disc at the picture's top right, its ink `value`. **Cost:** 0.5
day.

**35. Chosen objects:** `ui.handles(id, x, y, w, h, o)` over a part: its
outline (`value`, 1.5 px, with a dark 1 px keyline round it, so it reads on
any picture),
and by the last input either eight handles (drawn 6×6 on the handheld, 8×8
on the desk, white with a dark border, answering 16×16 to a pointer) or,
under touch, the body's drag and one corner knob (drawn 10×10, answering
32×32); a line has two; it answers the drag and the handle. With it,
`ui.guide(x0, y0, x1, y1)`, the dashed `accent` snap line. **Cost:** 2.5
days.

**36. An empty page:** `ui.empty(icon, title, lines)`: the icon at 26 px
(36 on the desk) on a disc of `value` at 0.1 (`raised` in a dark mode), the
title in the `title` role, the lines in `dim`, centred; the page's verbs
follow as buttons. **Cost:** 0.5 day.

**37. An app's bar tools:** `ui.page`'s `o.bar`, icons the app lends the
handheld bar after its title, in the chrome ink (Play bare in `value`, a
disabled one at half strength), and a compact cycle (Paint's zoom),
answered as buttons; on the desk they are the window's tool row, which
the app draws. The editors' row is chrome (step 23); this is the same
zone through the verbs. **Cost:** 1.5 days.

**38. Options on existing verbs:** `ui.swatches`'s `o.cols` lays the
swatches out in rows and `o.page` shows a page of them (Paint's 64, 32 a
page under touch), its focus the canvas cursor's ring -- a swatch grid's
focus is a ring in every theme, the one exception to a theme's small-part
mark -- and its chosen swatch a 2 px `value` ring at a 1 px gap;
`ui.seg`'s `o.icons` draws icon names, alone or before words (Storybook's
Align and Transition). **Cost:** 1 day.

**39. Wrapped labels:** `ui.label`'s `o.wrap`, the text broken at word
breaks in the content width, `o.lines` its most lines with "…" after.
**Cost:** 0.5 day.

**40. Void (2026-10-21).** Storybook is an app with its own document and
draws a story's text in the shell's fonts at any size, so `print`'s scale
in moy-spec stays as it is.

**41. Focus on a part of the mark's own colour:** the engine measures the
focus mark against the part's fill and draws DESIGN.md's ring for it
(The marks) where the pair falls under 3:1. **Cost:** 0.5 day.

**42. Touch density:** a layout's class takes the board's input as well as
its glass, so a touch board's handheld parts meet a 28–32 px target (Paint's
tools two by four at 28, one colour target, popovers of 32 swatches at
26×30) and its hint
strip gives way to the app's tool row. **Cost:** 1.5 days.

**44. Tooltips:** `o.tip` on a button, shown under a resting pointer on the
desk (the bar's Desk tooltip, `BACK_TIP`, as a verb). **Cost:** 0.5 day.

**45. Icon slots for Achievements:** `trophy`, `lock`, `heart`, `spark`,
`smile`, `key` and `secret` (a question mark), in every set (the default
set's sources are in `rounds/round16.py`). **Cost:** 0.5 day.

**46. A locked card:** `ui.card`'s `o.locked` with its `icon`: the icon in
the disabled ink on `sunk` (a dark mode's `raised` half way to the ground),
and a `lock` badge in `value` (Get Carts' badge ink) on a disc of `surface`,
`raised` with a ring in a dark mode, so the badge reads on the veiled tile;
still a stop. **Cost:** 0.5 day.

**47. The unlock banner:** the kernel's toast body (`moy_chrome_toast`)
redrawn as a card centred at the top: under the bar where there is one,
at the glass's top over a fullscreen game (between the corners a
game's HUD keeps), a 32 px tile (44 on the desk) tinted from `value` with
the badge's icon, a line in `value` and the name in the semibold face, in
the theme's fonts and RGB, over any surface; a secret's with confetti
bursting from its edges. **Cost:** 1 day. **Frame:** its rect repaints as
it comes and goes.

**48. A count's progress:** `ui.progress`'s `o.count`: the label alone, no
percent; the track in its own role, `track` (Contrast's an outline the fill
keeps 3:1 or more against); no fill at nothing. **Cost:** 0.5 day.

**49. The stop card:** `moy_chrome_panel` redrawn as the system modal card
the pause card is, on the system canvas at the glass's metrics over the
run's last frame washed: Moy, the title and its state, the sentence, the
error in the code face on a `sunk` inset (a notice's words in the body
face), the rows (See why, Restart, Home; no See why for a compiled cart)
and its keys in its foot; `moy_chrome_strip_crash` deleted. **Cost:** 1.5 days. **Frame:** one
full frame as it comes, nothing after.

**50. The compact bar:** the tool cart's 18-row strip (`moy_chrome_strip_*`)
in the look's roles and faces, drawn by the shell at the glass's resolution
and metrics over the rows the cart lays out under (2 × on the desk's console
mode, and into the letterbox above them on the S3, a 36 px band).
**Cost:** 1 day.

**51. The loading pill:** `ui.toast` with `o.icon` (the cart's kind) and
`o.flat` (no shadow, no alpha), painted once on the frame an open is asked
for, over the cover being opened on a handheld and in the desk bar's
middle. **Cost:** 0.5 day.

**52. A dialog's fields:** `ui.dialog` holding captioned `ui.field`s, Tab
between them, the first focused, Enter the dialog's last button. **Cost:**
1 day.

**53. The month:** `ui.month(year, month, today)` in a popover: the
weekdays in the caption, the days in seven columns, today on the current
mark, the weekend in `dim`. **Cost:** 0.5 day.

**54. The perf chip:** the HUD's pill and its breakdown card in flat fills
and the code face's digits, repainting its own rect a frame; the parts in
three hues (Contrast: solid, hatched, outline). **Cost:** 0.5 day.

**55. The kernel's screens in the look:** recovery and the kernel's web
console (`recovery.*`, `web.*`) drawn in the outline faces compiled into C
(`moy_path_faces.h`, through `moy_scr.h`) and the look's roles (recovery in
Moy Night), rows a touch glass taps, the strings in sentence case. **Cost:** 1 day.

**56. The Editor's theme mode:** `EditorApp` opened on a theme package with
two tabs, Config (`theme.json` in the json mode, its saves the look's
`Appearance.file_write`) and Sprites (the package's icon sheet). The sprite
tab needs a 16 × 16 cell size (today's cells are 8 × 8) and the slot names;
a smooth slot is shown from its 2-bit mask and painting it writes a pixel
tile with the slot in `icons.pixel_slots`, a key the loader learns
(additive: an older reader ignores it); undo in the session's memory, no
`journal/`. Appearance's Edit theme… row opens it (a seed copied to
`custom` first, as EDIT FILE does); `ThemeLayer`, EDIT ICONS and the user's
icon sheet are deleted. **Cost:** 4 days.

## Moot in this rendering

The pixel round's star field, moonlit edge and focus halo; the Galmuri
chrome fonts and their Moybyte cut; text at 2× as the big boards' only
mode (now gap 11, a theme's choice); the 14 and 17 px desk rows; dimming a
window behind by 25%; the 12×12 two-tone icons; the redrawn default
wallpaper (the default theme draws a backdrop); gap 43's sheet, a card
docked to the content's foot (the apps as built take popovers and dialogs,
2026-10-22).

**Total:** about 96–104 developer days for gaps 1–26; gaps 27–30 add
about 6, gap 31 about 19–22, gaps 32–44 about 10, gaps 45–48 about 2.5, and gaps 49–56 about 10.
