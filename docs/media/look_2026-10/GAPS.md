# What the 2026-10 look needs that the engine lacks

Each gap gives the capability, why the design needs it, its cost in developer
days (excluding golden re-bakes, which come once at the end), and its effect
on frame time and memory. "Owner call" marks a gap that reverses a ruling or
touches moy-spec.

## Foundation

**1. Theme colours in RGB: a shell-only palette block. Owner call:
`docs/theming_2026-09.md` §11 declines per-theme RGB palettes.**

- **Why:** MOY64 has no ramp for this look. Moy dark's neutrals map to
  indices 0, 55, 55, 54, 50 and 48. That maps `surface` and `raised` onto the
  same index and loses the indigo tint that is half of Moy's signature. Light
  mode's nearest white is index 7, which is cream. The lavender value colour
  (#b3a6f2) and the amber accent (#ffc53d) have no index either.
- **Shape:** roles take `#rrggbb`. The loader quantises each value to RGB565
  into indices 64–127, which only the shell can name. A cart's colour stays
  `& 63`, so cart pixels cannot change.
- **Work:**
  - `moy_theme.c` clamps colour indices to 63 in three places, and those
    clamps move.
  - `PAL565` grows to 128 entries; the host and web tables follow.
  - The §4.10 derivation moves to RGB arithmetic.
  - The §7 readability check runs on RGB.
  - The package `format` is bumped.
- **New roles, additive:** `value`, `value_ink`, `edge` (the moonlit top),
  `group_edge`, `under`, `lcd`, `lcd_ink` and `sel`, each with a derivation
  rule. The readability check (§7) holds every non-text role to 3:1 against
  the background it marks.
- **Cost:** 5–7 days.
- **Frame and memory:** no frame cost. Memory is 128 bytes per table.

**2. Fonts: Galmuri7, Galmuri9, Galmuri11 Bold and GalmuriMono9.** Contrast
also uses Galmuri11.

- **Licence and source:** SIL OFL-1.1, © Lee Minseo,
  github.com/quiple/galmuri v2.40.4. The sampled glyphs and the licence ship
  here.
- **Fit in MFNT v1:** every box is at most 12 wide and 14 tall, and the fonts
  are proportional.
- **Work:**
  - Generalise `tools/make_tiny5_font.py`. Galmuri sits on a 100-unit grid,
    so it samples cleanly.
  - Add a `THIRD_PARTY.md` notice.
  - Add the fonts to the `tests/test_font_lane.py` rasters.
  - Add a codepoint-to-byte table in `moy_text` for `− ← → ↑ ↓`, which are
    not Latin-1. They map into the unused 0x80–0x9F slots.
- **Roles:** a new `caption` role joins `ui`, `title` and `mono`. Tiny5 leaves
  the chrome.
- **Slots:** five loaded fonts with Contrast's, so `MOY_FONT_SLOTS` goes from
  4 to 6. Each slot's buffer is capped at 8 KB, and a Galmuri file is 2–4 KB.
- **The Code tab on GalmuriMono9:** a 5 px cell instead of 8, which gives 57
  columns. Checking the cursor, selection and tap hit-tests at a 5 px cell
  takes 1 day. Petme128 stays the carts' `print` font.
- **The Moybyte cut:** Mono's `l` and `1`, and Bold's `−`, are redrawn so
  `l 1 I | ( )` never read alike (`_patch_glyphs` in `mockups.py`). The OFL
  allows modified versions; Galmuri declares no Reserved Font Name, but the
  cut is named "Galmuri Mono (Moybyte cut)" in `THIRD_PARTY.md`. 0.5 day.
- **Ellipsis:** names cut to fit with "…". It builds on `moy_text_fit_px`,
  which exists, and needs `…` in the byte map. 0.5 day.
- **Cost:** about 5.5 days.
- **Frame:** no new lane. A full Code-tab redraw draws about 40% more glyphs,
  so measure a scroll on the T-Deck and record it in #66.

**3. Text at 2× on the 7–10″ boards** (`chrome.text_scale_floor` already
answers 2).

- **Work:** the bodies that still lay out in 8 px cells move to line metrics:
  Files, Get Carts, the Storybook and the editors. Default window sizes are
  re-planned for 512×300.
- **Cost:** 3–5 days.
- **Frame:** a full-desk repaint covers the same pixels. Measure a window drag
  on both P4 boards and record it in #58.

## Chrome and parts

**4. A `soft` frame style**, with `paper` and `studio` as its parameters.

- **What it draws:**
  - a 20 px strip, filled in `value` when focused and in `surface` with a
    `dim` title when not;
  - an outline title icon and one ×;
  - the moonlit top edge;
  - Studio's 1 px frame and its inverted white strip;
  - Paper's inverted strip.
- **Minimise and maximise:** move to the window chip and a title double-tap.
- **Cost:** about 3 days.

**5. Elevation and the unfocused window.**

- **A `drop N` surface clause:** a hard offset fill in `shadow_c`. 0.5 day.
  The frame cost is two thin spans, so nothing.
- **A `lit` edge** (top row only, in `edge`) for the moonlit edge. 0.5 day.
  No frame cost.
- **Dimming an unfocused window's body by 25%:** done once on the retained
  `win.buf` stamp when focus changes, never per frame. That adds a blend
  kernel (gap 6) plus an undimmed copy, or a redraw on refocus. 1.5 days.
- **Frame and memory:** one blend pass over a window at each focus change.
  The P4 desk is bandwidth-bound, so measure a focus switch and record it in
  #58. If it is too slow, the fallback is the dimmed strip and title alone.

**6. Carousel dimming: a darken kernel**, with cached dimmed covers.

- **Cost:** 1 day.
- **Frame:** zero during motion.
- **Memory:** 32–75 KB. On the T-Deck the Player's fit check must be able to
  release them (#66).
- **Light themes:** never veil art. Only dark themes darken their
  neighbours (toward black); light themes, Paper and Contrast show them
  undimmed, so they need no cache.

**7. Parts.**

- **New parts:** `slider`, `seg` (current segment = bar), an outlined
  `keycap`, a track-and-knob `toggle` with its On/Off word, and an `info` row
  (a value with no chevron, not focusable).
- **There is no inert state.** A setting that is not built has no row.
- **The ring, bar, fill rule:** `DESIGN.md`'s three marks as three parts:
  - `focus` (the 2 px accent ring, with a 1 px gap; on a list row, around the
    raised row);
  - `current` (the 2 px value bar, under or beside);
  - `primary` (the value fill).

  Every kind draws its state through them and through nothing else. A test
  in `tests/test_ui_style_data.py` can hold that the accent appears only in
  `focus` and `value` only in `current`, `primary` and window strips.
- **The moon-glow halo** is a pattern-lane ring outside the focus ring, in Moy
  only.
- **Cost:** 3.5 days.
- **Frame:** none. The halo is a single 1-pixel dithered ring, and only the
  focused item has one.

**7b. Moy's star field: a `stars` desk pattern**, beside Paper's `dots`.

- **What it is:** a 96 px tile of 14 points drawn on `bg`, laid down once into
  the cached background. It is a solid fill plus 14 single-pixel writes per
  tile.
- **Cost:** 0.5 day.
- **Frame:** carousel motion re-fills `bg` behind the moving cards, so the
  restore draws those few pixels too. That is negligible, but include it in
  the #66 carousel number.

**7c. Desk density: row metrics per tier.**

- **What changes:** in windowed apps, list rows are 14 and settings rows 17,
  against the handheld's 20. Text stays at 2×.
- **Shape:** a WM-tier entry in the layout metrics. The handheld goldens do
  not move.
- **Cost:** 1.5 days.

**8. Icons: a 12×12 two-tone set of 32**, drawn in `mockups.py`, as a role
sheet that is remapped at draw time. 2 days.

## Layouts

**9. Homes.**

- **Handheld carousel:** tabs in the bar, a 2× hero, a title and metadata,
  page dots and a keycap hint strip. 2–3 days.
- **The 1024×600 console home:**
  - a 2× hero beside a detail pane: title at ×2, metadata, a description,
    stats, and Play / Change / Copy;
  - a shelf below that bleeds off the right edge, with the ring on the
    focused shelf card;
  - the bar's × is the one way back to the desk.
  - 3 days.
- **Data the details need:**
  - **Last played and play count:** store records. 0.5 day.
  - **A description and a genre in the manifest:** moy-spec's SPEC.md, so an
    **owner call**.
  - **A best score:** carts do not report one. A `score()` verb would be
    moy-spec's, so an **owner call**.
  - Without these, the pane shows the source, the last-played date and the
    play count.

**10. Covers.**

- **Per-cart crops:** one 64 px crop per cover, used by the hero at 2× on
  both sizes, plus a 48 px shelf crop (`CROPS`, no HUD). 0.5 day.
- **A title-tile fallback** for carts without a cover. 0.5 day.
- **Drawn label art:** about 3 hours per game, as art.

**11. App relayouts:**

| layout | cost |
|---|---|
| Settings (only working rows, About facts, a caption on every group) | 1 day |
| Appearance (one 64×48 preview at both sizes in the current mode or the theme's only mode, a fixed caption line, the current bar; accent swatches in the mode's values) | 1 day |
| Calc as an object: a tape history (new behaviour), the LCD inset, 2× legends, the 4×5 keypad with focus | 2 days |
| Files with Places (Projects, Carts, Art, Music, Trash are new filters), file pictures, desk density, ellipsis, and Kind dropped at large type | 3 days |
| sprite editor: the grey transparency checker (owner allows), the ring cursor, the bar on the tool and the colour | 1 day |
| the editors' bar, and the code overflow marker | 1 day |
| desk icons (26×20 tiles, plates over the wallpaper) | 0.5 day |

**12. Wallpaper per glass.**

- **Why:** the wallpaper cart reads W and H instead of being cover-cropped
  from 320×240, and it receives the shell's **quiet rectangles** (the bar, the
  home's text zone, the icon column) so it keeps stars and detail out of
  them.
- **The bar band:** the shell lays the bar band in `bg` over any wallpaper.
- **The subject rectangle:** the wallpaper declares where its subject is (Moy
  and the moon). The WM's default window placement keeps clear of it when the
  window fits elsewhere.
- **Cost:** the cart 0.5 day, the host API 1 day, the bar band and the
  placement 1 day.
- **Frame:** none (static).

## Themes

**13. The seed set:** Moy (dark, light), Paper (light only), Studio (dark
only), and Contrast (dark, plus a new light that is kept for
polarity-dependent low vision).

- **Accents have two values each.** Moon is #ffc53d dark and #d45f00 light,
  and likewise for the other seven presets. The light value clears 3:1 on
  light surfaces, and the picker shows the current mode's.

- **Renames and deletions:**
  - `night` is renamed `moy`. Under the no-migrations rule a stored `night`
    reads as absent and resolves to the default, which is Moy.
  - Platinum, Machine, Win95, Outline and Indigo are deleted, as are Berry,
    Forest and Slate as themes.
  - The last four become Moy accent and tint presets.
- **Studio:** one accent (orange, focus only), white for current and
  primary, and every chrome label in capitals through a theme flag that the
  toolkit's label path reads.
- **Cost:** 4 days.

**Golden re-bake:** every shell golden moves, on both sizes. 2 days.

**Total:** about 57–67 developer days. Gaps 1–3 come first, then 4–8, then
9–13.
