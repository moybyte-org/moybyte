# The 2026-10 look: the themes as data

Every theme is a dict of fields over one base in `mockups.py`, and the
renderer reads only fields. A theme has one mode or two: Moy's are Day
(`light`) and Night (`dark`), and a mode may set any field, its family
included (Day draws Plex, Night Inter). `DESIGN.md` is the system and the shipped
themes; this file is the theme model and the ricing proofs' fields, the
input to the theme format. Gap numbers are `GAPS.md`'s.

## The shipped themes, beyond their colours

- **Moy, Day:** the desk is two pale discs (`backdrop.desk`), with no
  gradient. The focused window stays white; its current mark is a 4 px
  `value` bar across its top (`win.header: bar`). `tonal` keys are `sunk`.
- **Moy, Night:** the desk carries a flat crescent one tone above the sky.
  The focused window's title strip is filled in `value` (`win.header:
  fill`), windows have a 1 px `line` frame, and the bar is 85% `sunk`.
- **Blocks:** colour lives in blocks, not marks: the home is sunflower
  (`home_bg`), the desk cream with four flat Bauhaus shapes, the home has a
  pale halo behind the hero and mint and coral discs at its edges, each app
  owns a tile colour (Play coral, Paint sunflower, Files sky, Calc
  tangerine), the Calc display is mint and the operators sunflower. Keys
  have no outline; a focused key inverts to ink.
- **Contrast:** every size of Calc and the window titles two to six pixels
  larger, 2 px white borders on `normal`, `tonal` and `danger`, a #262626
  `tonal` fill, a black bar ruled in white.

## Ricing proof: Luna and Coast

Two themes in the style of two desktop OSes, `luna_*` (Windows XP) and
`coast_*` (macOS Sonoma/Sequoia), drawn on the same two screens by the same
renderer, glyph pipeline and RGB565 output. Everything is drawn with open
fonts; there is no Microsoft or Apple artwork, logo or font: the
start-style button carries Moy and "moy", and the menu bar carries Moy.

**Luna**

- **Font:** DejaVu Sans Condensed, regular and bold (Bitstream Vera Fonts
  licence, with public-domain DejaVu changes; a modified copy must be
  renamed). It stands in for Tahoma: a humanist sans, narrow and large on
  the pixel. The "moy" label is DejaVu Sans Bold Oblique.
- **Sizes:**
  - handheld: 12 bold tabs, 11 meta and hints, 10 key legends, 18 title;
  - desk: 14 rows, 13 meta, 16 bold window titles.
- **Theme fields it sets:**
  - the ramp, with `surface` #ece9d8;
  - accent #c86400 for focus: `invert` on parts, a 3 px ring with gap 0 on
    cards (3.2:1 on the window body);
  - value #2f63d6 for "=";
  - `current.style: fill`: selection #316ac5 with white text, and #d6d2c2 in a
    window behind;
  - `win_r` 8 with `win_corners` on the top only;
  - `win.header: grad`, five stops focused and three behind, with white
    title ink and a text shadow;
  - `win.controls: xp`, a red rounded close;
  - `win.frame` 4 px blue;
  - `bar.edge: bottom` with a `grad` taskbar, `sysbtn: start`,
    `chips: button` and a `tray` gradient segment;
  - `hhbar: grad` with `tabs: pill`;
  - per-app `tiles`, with labels in white and a 1 px shadow;
  - `sidebar` as a two-stop gradient, with a `places_panel` (white card,
    gradient head);
  - `list_bg` white;
  - `colhead` (a filled header with separators);
  - `buttons` with the XP gradient and a 1 px border on every kind, and
    `app_buttons.calc` for blue digits and red operators and C;
  - `lcd` with a 1 px border;
  - `keycap` in the button gradient;
  - `on_backdrop` white with a shadow (title on the hills 5.4:1);
  - `backdrop`: 7 shapes.
- **Gaps it uses:** 2, 3, 5 (per-corner radii), 7 (gradient paints), 9,
  3's text shadow, 12 (the selection fill), 13 (a per-app override), 15
  and 16.
  - The bottom taskbar is the layout's existing `LAYOUT_EDGES`
    (`docs/shell_layout_2026-10.md` §10), so it needs nothing new.

**Coast**

- **Font:** Inter (SIL OFL-1.1), the usual open stand-in for SF Pro. Calc's
  result is Inter Light at 52 px; the rest uses the desk sizes above.
- **Theme fields it sets:**
  - the ramp, with the window body #f6f6f8 and the list white;
  - accent #3a86ff for focus: `invert` on rows and buttons, a 3.5 px halo
    at 60% alpha with gap 0 on cards and small parts;
  - value #0a6cff;
  - `current.style: fill`, grey in a window behind;
  - `win.align: center` with no title icon;
  - `win.controls: lights`, grey when unfocused;
  - large soft shadows (blur 22, offset 14, 38%, and half that behind);
  - `bar` 30 px `glass` with `sysbtn: glyph`, `chips: none` and
    `app_title`;
  - `hhbar: glass` with `tabs: pill`;
  - `launcher: dock`, with running dots under Calc and Files;
  - `sidebar: vibrancy`;
  - `zebra` rows;
  - `app_buttons.calc`: every kind a circle, with orange `tonal` and
    `primary` and a darker `danger` (C);
  - `lcd` none, so the result stands alone;
  - `keycap` as 28% white glass;
  - `on_backdrop` white with a soft shadow (12:1);
  - `backdrop`: 4 shapes.
- **Gaps it uses:** 2, 3, 5 (the circle shape), 6, 7, 9, 12, 13, 15, 16,
  and the later 28 (frosted glass), 29 (vibrancy) and 30 (the dock).

**What the model cannot express, and what the mockups do instead:**

- **Live blur or translucency behind moving windows** (macOS): declined. On
  the P4 desk every window move would recompose the blur against the
  wallpaper. Gap 26 gives frosted glass over static pixels and 27 a tint.
- **XP's bevels and 1 px highlight lines:** expressible now (`edges:
  bevel`, gap 8). Luna keeps the gradients, which read closer to XP at 1×.
- **macOS app menus (File, Edit, …):** the shell has no app menus, so the
  bar shows the app's title only.
- **XP's minimise and maximise buttons:** drawn, blue beside the red close
  (`win.controls: xp`).

## Superset proof: Pico, Bench and Adw

Three themes in the style of existing systems, `pico_*`, `bench_*` and
`adw_*`, using only open fonts and drawing made for them here, prove the
pixel look through the same fields on the same two screens.

**Pico (in the style of Picotron)**

- **Font:** Galmuri (SIL OFL-1.1, Lee Minseo) at 1 bpp.
  - The cuts are crisp at their design sizes and whole multiples: Galmuri7
    at 8 px, Galmuri9 at 10 and Galmuri11 at 12.
  - The Calc result is Galmuri11 Bold at 24 (2×).
  - Picotron's own font is not open. Galmuri7 has its feel: proportional,
    with 5 px capitals.
  - Micro5 (OFL) was tried and dropped, because its 3 px wide C reads as
    "[".
  - Per face, 1 bpp costs 2.6–3.6 KB for the full 202-glyph set.
- **Look:**
  - `render`: crisp, stepped, scale 2;
  - the 16 PICO-8 colours and nothing else: a navy desk, paper-white
    windows, light-grey chrome, red focus, and blue for current and primary;
  - 1 px black frames, a stepped radius of 2 logical px and a hard 1 px
    shadow.
- **Backdrops, with dither:**
  - the desk is navy dithered into plum, with a dithered pink sun;
  - the home is the dither alone, so the meta line stays quiet.
- **Calc:**
  - a black display with lime digits;
  - `buttons`: `normal` grey, `tonal` orange, `danger` pink, and `primary`
    blue (the derived default).
- **Fields used:**
  - `render.*`, `fam`, `sizes` (per role, in logical px);
  - the ramp, `accent`, `value`, `value_ink`;
  - `shadow` (hard), `focus.style: invert` (`focus.ring.w` and `.gap` in
    whole logical px);
  - `win.header: fill` with `fill_back`, `win.frame`/`frame_back`,
    `win.rule`, `win.icon` off;
  - `bar` (flat black, `sysbtn: glyph`, tab chips), `hhbar: flat`;
  - `tiles` with `tile_outline` and `tile_ink`, and `label` and
    `on_backdrop` with hard text shadows;
  - `sidebar` as a colour, `list_bg`, `buttons`;
  - `lcd`, `lcd_ink`, `lcd_dim`, `keycap`;
  - `backdrop` with `dither` paints.
- **Gaps:** 2's 1 bpp faces, 5's stepped corners, 6's hard shadow, 7's
  dither and 11.

**Bench (in the style of Amiga Workbench 3.x)**

- **Font:** petme128 (MIT; `runtime/font.py`), the engine's own 8×8 print
  font. It is fixed-width, like Topaz, and is already in every image. The
  result is drawn at 3×.
- **Look:**
  - `render`: crisp, scale 2, radius 0 everywhere, no shadow;
  - `edges: bevel` in white and black;
  - grey #aaaaaa, with blue #6688bb for current, primary, and the focused
    window's frame and title strip;
  - focus as `invert`, Workbench's complement highlight.
- **Window controls:** `amiga`, a close gadget at the left and a depth
  gadget at the right.
- **Screen bar:** white with a black rule. It shows a screen title
  (`bar.app_title` as text) and the clock.
- **Backdrop:** a 16-band copper gradient (`steps:16`) from navy through blue
  and violet to rust.
- **Files:**
  - an 8 px fixed font is wide, so the Kind column drops itself when names
    would show fewer than 11 characters (the rule Contrast already had);
  - the current row is a blue fill.
- **Calc:** bevelled grey `normal`, white `tonal`, blue `primary`; the
  display is white and bevelled inward (`lcd_bevel: in`).
- **Fields used:**
  - everything Pico uses, plus `edges`, `hi`, `lo`;
  - `win.controls: amiga`, `bar.app_title`, `lcd_bevel`;
  - `keycap.bevel`, `current.style: fill`, `layout`;
  - a `steps` paint;
  - `icons.swap`, which takes the pixel set's light-blue base to dark blue
    on the white bar.
- **Gaps:**
  - the `amiga` controls (15);
  - the column-drop rule (19).

**Adw (in the style of GNOME's Adwaita)**

- **Font:** Cantarell (SIL OFL-1.1), Regular and Bold.
- **Look:**
  - a black top bar with a workspace indicator and a centred clock
    (`bar.clock: center`);
  - a dark, solid, dock-like dash (`launcher: dock`, `dock.frost` off);
  - header-bar windows: white, a centred bold title and a round close
    button (`win.controls: adw`), a soft shadow and a 1 px outline;
  - a grey sidebar, and the current row as an accent-tinted fill;
  - focus as an accent `plate` on rows and a 2 px ring at 50% alpha with
    no gap on cards and small parts (libadwaita's outline).
- **Calc:** flat grey `normal`, darker `tonal`, `danger` with red ink (the
  theme's `danger` role), blue `primary`.
- **Fields used:**
  - `bar.sysbtn: workspaces`, `bar.clock`, the `dock.*` fields,
    `win.fill`/`fill_back`;
  - `focus.ring.alpha`, `sel*`, `sidebar` as a colour;
  - `danger`, `buttons`;
  - `backdrop` (different lists for the home and the desk).
- **Gaps:**
  - the `workspaces` system button and the centred clock slot (16), the
    `adw` controls (15);
  - Cantarell lacks U+2212 (minus), so the font converter substitutes "-"
    (2).


## The theme model

Every field the nine themes use, with what the renderer does when a field
is absent. This is the input to the format spec. Colours are `#rrggbb`
(gap 1). A paint is a colour, a list of colours, or a list of
`(t, colour)` stops, with `mode` set to `smooth`, `steps:N` or `dither`.

| group | fields | absent means |
|---|---|---|
| identity | `key`, `name`, `mode` (`dark`/`light`) | — |
| ramp | `bg`, `surface`, `raised`, `sunk`, `line`, `dim`, `text` | — (required) |
| marks | `accent` (focus), `value` (current, primary), `value_ink`, `danger` | `danger`: the `normal` ink |
| edges | `edges` (`flat`/`bevel`), `hi`, `lo` | flat |
| type | `fam`; `w_body`, `w_med`, `w_semi`, `w_bold` and a per-role `w_<role>`; `sizes.hh` and `sizes.dk` per role | the size tables in the script (HH, DK) |
| rendering | `render.text` (`aa`/`crisp`), `render.shapes` (`smooth`/`stepped`), `render.scale` (1/2) | aa, smooth, 1 |
| focus | `focus.style` (`ring`/`plate`/`lift`/`edge`/`underline`/`invert`), `focus.card`, `focus.small` (a style per size class), `focus.color` (a role), `focus.ink` (`keep`/`accent`), `focus.ring.w`, `.gap`, `.alpha`, `.keyline`, `focus.plate.alpha` (light, dark), `focus.lift.grow`, `.rise`, `.shadow` (`ink`/`accent`), `focus.edge.w`, `focus.underline.w` (DESIGN, the marks) | ring; `card`, `small`: `style`; `accent`, keep; 2.5, 3, 1, no keyline; (0.14, 0.22); 5, 3, ink; 4; 3 |
| radii | `win_r`, `card_r`, `key_r`, `win_corners` (four flags) | 12, 10, 10, all |
| elevation | `shadow` (`soft`, colour, alpha, blur, dy; or `hard`, colour, dx, dy), `shadow_back` | no shadow |
| backdrop | `backdrop.home`, `backdrop.desk`: lists of shapes (gap 9); `home_bg` | `bg` |
| home | `dim_neighbours`; `on_backdrop.ink`, `.dim`, `.shadow` | no veil; `text`, `dim` |
| window | `win.header` (`plain`/`fill`/`bar`/`grad`), `win.h`, `win.align`, `win.ink`, `win.ink_back`, `win.tshadow`, `win.controls` (`three`/`close`/`lights`/`xp`/`amiga`/`adw`), `win.grad`, `win.grad_back`, `win.fill`, `win.fill_back`, `win.frame`, `win.frame_back`, `win.body`, `win.rule`, `win.icon` | plain, 44, left, three (minimize, maximize, close) |
| current | `current.style` (`plate`/`fill`/`bar`), `current.color` (a role), `current.alpha` (light, dark); `sel`, `sel_ink`, `sel_back` for `fill` | bar, `value`, (0.14, 0.24) |
| desk bar | `bar.edge`, `bar.h`, `bar.style` (`flat`/`grad`/`glass`), `bar.fill`, `bar.alpha`, `bar.line`, `bar.grad`, `bar.sysbtn` (`tile`/`start`/`glyph`/`workspaces`), `bar.sys`, `bar.chips` (`tab`/`button`/`none`), `bar.chip_cur`, `bar.app_title`, `bar.ink`, `bar.dim`, `bar.tray`, `bar.clock` (`right`/`center`) | top, 40, flat |
| handheld bar | `hhbar.style` (`none`/`flat`/`grad`/`glass`), `hhbar.h`, `hhbar.fill`, `hhbar.line`, `hhbar.ink`, `hhbar.dim`, `hhbar.sys`; `tabs.style` (`bar`/`pill`), `tabs.pill`, `tabs.pill_ink` | none, bar |
| launcher | `launcher` (`column`/`dock`); `dock.fill`, `dock.alpha`, `dock.frost`, `dock.ring`, `dock.r`; `tiles` (per app), `tile_r`, `tile_fill`, `tile_outline`, `tile_ink`, `tile_grad`; `label.ink`, `label.shadow` | column, `raised` tiles |
| lists | `sidebar` (paint or `vibrancy`), `places_panel`, `list_bg`, `zebra`, `colhead` | the window body |
| icons | `icons.kind` (`pixel`/`smooth`), `icons.set`, `icons.swap` (a pixel set's palette swap, index to index) | smooth, the default set |
| code | the `code_*` roles of `docs/theming_2026-09.md` §4.1 (the mockups' `syn.*`), each 4.5:1 or better on the code surface | derived per mode |
| buttons | `buttons[kind]`, `app_buttons[app][kind]`: `fill`, `ink`, `border`, `shape`, `r`, `font`, `bevel` | derived (Button kinds) |
| display | `lcd`, `lcd_border`, `lcd_bevel`, `lcd_ink`, `lcd_dim` | no box; `value`, `dim` |
| key hints | `keycap.fill`, `.alpha`, `.border`, `.grad`, `.ink`, `.r`, `.bevel` | a tint of the background |
| placement | `layout.files`, `layout.calc` (default window rects) | the WM's placement |

**Not theme fields:** `layout.*` here stands in for the WM's default
placement, which the layout format already owns
(`docs/shell_layout_2026-10.md`). `tiles` per app is a theme field, but
which app gets which glyph is the app's own icon.

## Real size

The PNGs are 1:1 with the panel's pixels; the @3x copies are three times
that. To show a PNG at the panel's physical size, scale it to the width
below. The pixel densities are computed from each panel's diagonal.

| PNG | panel | ppi | physical size |
|---|---|---|---|
| `*_handheld_home.png` (320×240) | T-Deck, 2.8″ | 142.9 | 56.9 × 42.7 mm |
| `*_desktop.png` (1024×600) | Waveshare P4, 7″ | 169.5 | 153.4 × 89.9 mm |
| (the desk at 1280×800) | Guition P4, 10.1″ | 149.4 | 217.5 × 136.0 mm |

The 10.1″ shows the desk at 1280×800 with the same 1× metrics. The desk
mockups are drawn at 1024×600, so on the 10.1″ the same parts are about
13% larger.
