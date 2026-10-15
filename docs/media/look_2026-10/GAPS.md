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
  radii, the ring, `shadow`, `edges`, `backdrop`, `win`, `bar`, `hhbar`,
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

**12. The three marks** as three parts: `focus` (the ring, after the part,
from `ring_w`, `ring_gap`, `ring_alpha`), `current` (`bar` or `fill`, with
`sel`, `sel_ink` and a colour in a window behind) and `primary` (gap 13).
Every part draws its state through them and nothing else; a test in
`tests/test_ui_style_data.py` holds that `accent` appears only in `focus`.
A part under 28 px takes the hugging ring (DESIGN, the marks): the focus
part draws a tinted plate and a thin ring inside the part's own box, before
the part's glyph. **Cost:** 3.5 days. **Frame:** none.

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
same 55 slots (`icons/pixel/slots.txt`).

- **Pixel:** a 16×16 `IconSheet` (`icons/pixel/system_icons.moygfx`, drawn
  by `icons/pixel/pixel_icons.py`), editable in the sprite editor as today.
  Its palette indices are roles, so a theme recolours it with a swap (Bench
  takes the blue ramp down a step on its white bar).
- **Smooth:** 24-unit SVG sources (`icons/smooth/<set>/`, a `.fill` layer
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

**21. Covers:** a 64 px crop per cover for the hero, a 48 px shelf crop
(`CROPS`), anti-aliased clipped corners, a title tile for a cart without a
cover. **Cost:** 1.5 days.

**22. App relayouts:**

| layout | cost |
|---|---|
| Settings: only working rows, the About facts, a caption on every group | 1 day |
| Appearance: one preview size on both boards, a fixed caption line, the current bar | 1 day |
| Calc: a tape history (new behaviour), the display inset, the 4×5 keypad in kinds | 2 days |
| Files: Places (Projects, Carts, Art, Music, Trash), file pictures, ellipsis | 3 days |
| the sprite editor: the grey checker, the ring cursor, the bar on tool and colour | 1 day |
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
  one) opens the system menu from every surface; over a fullscreen cart it
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

## Moot in this rendering

The pixel round's star field, moonlit edge and focus halo; the Galmuri
chrome fonts and their Moybyte cut; text at 2× as the big boards' only
mode (now gap 11, a theme's choice); the 14 and 17 px desk rows; dimming a
window behind by 25%; the 12×12 two-tone icons; the redrawn default
wallpaper (the default theme draws a backdrop).

**Total:** about 94–102 developer days for gaps 1–26; gaps 27–30 add
about 6.
