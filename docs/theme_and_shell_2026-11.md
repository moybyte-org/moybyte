# Theme and shell: the theme format, the shell's layout, scrolling and text (2026-11)

The standing contract for how the OS looks and how its shell is laid out: what
a theme package holds and how the loader reads it (§2-§7), the bars, the two
modes, popovers and the home (§8), the one scroll model (§9) and the text
files the console edits (§10), with the owner's decisions behind each (§1)
and what was declined (§12). It merges what is still in force of four docs
now archived: `docs/history/theming_2026-09.md` (accepted 2026-10-11),
`docs/history/shell_layout_2026-10.md` (2026-10-13),
`docs/history/look_2026-10.md` (the 2026-10 look's build plan, 2026-10-15),
`docs/history/scroll_and_text_2026-10.md` (2026-10-27) and
`docs/history/text_editing_2026-09.md` (2026-09-07). Their phases, build steps, review
ledgers and baselines stay there; a citation of a build step ("look step 27")
points at the archived plan. Where two of them disagreed, the later one's
ruling is the one stated here.

The look itself — the marks, the type scale, each seed's fields — is
`docs/media/look_2026-10/`: `DESIGN.md` is the look, `THEMES.md` the theme
model and each theme's fields, `GAPS.md` what the engine lacked, and
`EDITOR_LAYOUT.md` the Editor's rules. The public UI verbs are
`docs/ui_api_2026-10.md` and `docs/ui_blocks_2026-10.md`; the app contract
is `docs/app_api_v1.md`; the shell's process model is `docs/shell_ux_v1.md`;
the dirty protocol every cache here obeys is `docs/surface_model_v1.md` §3,
and its §14.6-§14.7 state the copy and shift frames §9 relies on. Numbers
that measure any of this live in **#66** (frames, per board), **#58** (the
P4 port), **#113** (the shift costs) and `docs/perf_native_gap_v1.md`
(**#77**); this doc cites them and copies none.

Claims are labelled as in `docs/surface_model_v1.md` where they are not
read out of code: **ESTIMATED** (with how) or **PREDICTED** (with the gate
that settles it).

## 1. Decisions

Each ruling keeps its date. A later ruling that replaces an earlier one says
so in place.

### 1.1 Theming (owner, 2026-09-27)

| # | decision |
|---|---|
| D1 | **Superseded by the 2026-10 look (owner, 2026-10-15; §1.3).** The default look was **"Open Machine, refined"**: the Workbench structure (1px black outlines, 2px hard shadows, calm light/dark chrome over the PICO-8 base colours), the **Tiny5** UI font, desk icons as tiles with the **label in a pill under the tile**, window title strips **left-aligned with the app's identity stripe and a square close on the right**, and **Moy's night** (stars, a low hill, a moon, Moy on the hill) as the default wallpaper. Light and dark are ONE drawing routine and two token tables. The 2026-10 look replaces D1 as the default: anti-aliased type, smooth shapes and soft shadows, with `moy` in its Day mode the default theme (owner, 2026-10-15; `docs/history/look_2026-10.md`). |
| D2 | Themes change the OS **extensively** (§2's range), not only colours. |
| D3 | A theme can carry **paintable sprites** (9-slice frames, glyphs, a desktop tile), in the first or second version; the format reserves them from format 1. |
| D4 | **Apps follow the theme.** Settings, Calc and every shipped app restyle with it, because they draw through the one toolkit. App carts read it through `theme()` (§5). |
| D5 | **Layout: both.** A theme sets the default bar layout (its `layout`'s bars, §2); the user can override it in Settings, and the override survives a theme switch. A window's mode is its own button, not a theme's (§12.2, owner 2026-10-13). §8. |
| D6 | **Themes bring wallpapers**: one wallpaper for the desk and the home (owner, 2026-10-14, replacing the per-slot wallpapers of 2026-09-27: a screen-sized bake per slot is memory a board does not have, and the home already shows the desk's wallpaper behind its cards). **The wallpaper is the theme's** (owner, 2026-10-14, the modern-Windows rule, replacing 2026-09-27's separate user override): picking a theme brings its wallpaper, and the user's pick is a change to the theme like any other, landing in `custom` and travelling with it; there is no persisted `wallpaper` setting. A theme ships a static image and may name an installed wallpaper cart (§3.9). |
| D7 | **The home is one component on every board**: tabs (Games / Mine / Apps / Make) over the carts, themeable, over the one wallpaper (D6), in an arrangement a theme or the user picks (the layout's `home`: the cover carousel by default, the grid, or the icon grid; owner, 2026-10-14, §8.10). **The desk's PLAY opens this same home** (owner, 2026-09-27): the windowed tier has no separate games-only Library. |
| D8 | **Format 1 carries four more keys** (owner, 2026-10-11, before format 1 froze): **content colour roles** for code syntax and the block editor's block colours (§3.1); a **corner radius of 0 to 3** (§3.2); a **pressed-label offset** (§3.2); and a **text effect** per part and state, `none`, `shadow` or `emboss` with the role it paints in (§3.3). Their defaults reproduce the pixels from before them. The Paint, sprite, map and scene canvases stay exempt. |

The pictures for D1 and D7, with the shell as it rendered before, are in
`docs/media/theming_2026-09/` (its README says which are mockups).

Rejected in the same sessions, so they are not re-proposed: MOY64 grey chrome
replacing the PICO-8 base colours, cartridge-shell or notched library cards,
labels inside the desk tiles, and Pixel Operator replacing Tiny5 as the UI font.
Each was mocked and judged worse (§12.1).

### 1.2 Shell layout (2026-10-13)

| # | ruling (2026-10-13) | section |
|---|---|---|
| L0 | **The owner's governing principle:** the mockups are not set in stone; the UI should make sense; in doubt, strive towards a real OS, less a game console; no duplication; things are consistent. Every ruling below is read against it: one mechanism per job, one way to each thing, and a board differs only where its hardware says so (§8.8) | all |
| L1 | **Two modes.** CONSOLE mode: one app fullscreen at a time over a back-stack rooted at the animated home. DESKTOP mode: the desk, windows, bars, popovers. They are the two WMs that exist; console mode on a P4 is `FullscreenStackWM`'s presentation, the one the handheld boards run, never a third variant | §8.1 |
| L2 | **The mode is explicit WM state**, `wm.mode`, that every tier-sensitive branch consults; the desk's PLAY enters console mode, the `close` widget on the home leaves it; parked windows live outside the back-stack and survive the switch; nothing else switches and no boot setting exists | §8.1 |
| L3 | A layout is **up to four bars**, one per edge, each with a size and three slots holding **built-in widgets** from a fixed catalogue; `layout` holds `bars` and nothing else | §8.2, §8.3 |
| L4 | **The content rectangle is derived from the bars**; `content_rect()` is the app-cart contract, `bar_h()` a shim; the surface row grows additively; bars are global, every surface lays out off them; compiled apps and games are untouched | §8.5 |
| L5 | **One popover mechanism**: the system-menu `Popup` generalised, riding the quiet stack with its own rect as damage. Its contents are **components** that draw in a popover, in both modes (docs/history/look_2026-10.md step 33, 2026-10-26; console mode's fullscreen pages before it) | §8.6 |
| L6 | **One safety rule**: `menu`, `zone` and `close` placed after every drop, else the tier default; caps, the tap floor and a minimum content area checked once, deterministically | §8.7 |
| L7 | **Precedence: user, then theme, then default.** The user's override is one strict-read row, `layout_theme`; `set_theme` never touches it (D5) | §8.9 |
| L8 | **A bar is a cached strip re-rendered whole when its key moves**, as today, and blitted otherwise; a layout change is a relayout; no per-cell repaint and no new invalidation mechanism | §8.11 |
| L9 | **The home is the carousel with the animated centre card**, console mode's root; its scale is a function of position, never of time; the fullscreen home hands its band to the compositor as damage; a board degrades by measurement, never by data | §8.10 |
| L10 | Pixels move only where a step restyles on purpose: new bar, popover and console-mode rows are NEW golden configs; the home is one named re-baseline; every other step holds every golden byte-identical | `docs/history/shell_layout_2026-10.md` §13 |
| L11 | **The HOME key backs out one level, everywhere** (owner, 2026-10-13). In desktop mode it shows the desktop, `show_desk()`: every window minimised to its chip and kept, HOME again restores them, and it never closes a window (Win+D's shape). In console mode it goes home, and from the home on a P4 to the desktop | §8.1 |
| L12 | **The input model assumes a keyboard on every board** (owner, 2026-10-13): the T-Deck's own, a BLE keyboard on the Guition S3 and both P4s. Touch is supplementary -- a fullscreen game on a P4 is left by the keyboard alone -- and there is no on-screen home button: it would spend bar space on what the HOME key does | §8.2, §12.2 |
| L13 | **Every bar widget is compact** (owner, 2026-10-14: "I only want the calendar to pop out"; "that should be the default for every widget, why would we want giant bars?"): an icon or a short value. Anything bigger opens as its component -- its popover -- and no widget is ever docked large in a bar, so a bar's `size` is a slim range, 16..32. Side bars stay, as thin bars | §8.2, §8.3, §8.6 |
| L14 | **The home has arrangements** (owner, 2026-10-14: carousel, the old big grid, and later a small icon-only grid; "we don't have to do it now"): one home component, its tabs, sources, taps and bar verbs shared, its card layout the layout's `home` key -- `carousel` (default), `grid` or `icons` | §8.3, §8.10 |
| L15 | **One projects list** (owner, 2026-10-14): the project picker is deleted on every tier; the home's Make tab is the one list of projects (L0) | §8.10 |
| L16 | **The Editor's bar has no PROJECTS button** (owner, 2026-10-14), in every console-mode Editor: its X backs out one level, to wherever the Editor was opened from -- CHANGE works from any home tab -- which is the back-stack's pop | §8.10 |
| L17 | **The desk's PROJECTS opens the home in a window** (owner, 2026-10-14): on its Make tab, a normal window whose X closes it and never switches modes; a Make pick opens the Editor in its own window, a Games cart runs full screen (owner, 2026-10-31, §8.1), an Apps entry opens its app window. It draws the console home's design (owner, 2026-10-20: the hero, its details, the shelf) whatever the fullscreen home's arrangement | §8.1, §8.10 |
| L18 | **On a board with a desk the Editor is a window** (owner, 2026-10-20): a Make pick, CHANGE or NEW from the console-mode home gives console mode back to the desk and opens the Editor there in a window, and closing that window gives the glass back to the console-mode home on the tab and card it came from, unless a window the desk did not have then is open on it (R5-05); a board with no desk opens it fullscreen | §8.1, §8.10 |

**The owner's answers** (2026-10-13 on):

| question | ruling | applied in |
|---|---|---|
| the Make tab | "+ New" and every editable cart, a tap edits | §8.10 |
| the carousel's centre card | larger, and animated through the centre in motion, not only at rest | §8.10, L9 |
| the default seed's bars | today's one bar | §8.3, example 1 |
| the desk icon column | kept beside the home | §8.1 |
| `windows: maximized` | dropped, with the `windows` key | §12.2 |
| the Guition S3 | console mode only, with the T-Deck | §8.1, §8.8 |
| the battery component | Settings' level, VOLUME and BRIGHTNESS rows, drawn by Settings | §8.2, §8.6 |
| the modes | console and desktop; PLAY enters console mode on a P4 | §8.1 |
| a boot-into-console setting | not for now; PLAY is the only way in | §8.1, §12.2 |
| the way back from console mode | the existing X, now the `close` widget | §8.1, §8.2 |
| a running windowed playtest when PLAY is pressed | ended; the simplest to build | §8.1 |
| wifi: one implementation | the Settings WIFI panel stays and `moybyte.wifi.moy` is deleted (step 6). The cart was one of the first "the OS is carts" carts; where the line between system carts and built-in apps sits is not settled and can move later — today one implementation wins | §8.6, `docs/history/shell_layout_2026-10.md` §13 step 6, `docs/history/shell_layout_2026-10.md` §16 |
| the docked calendar (2026-10-14) | "I don't want bars, I only want the calendar to pop out": `calendar` leaves the catalogue; the calendar is the clock's popover and page only | L13, §8.2, §8.6, §12.2 |
| the home's arrangements (2026-10-14) | carousel (default), the old big grid, later a small icon-only grid; "we don't have to do it now": `home: carousel | grid`, then `icons` | L14, §8.3, §8.10 |
| giant bars (2026-10-14) | "that should be the default for every widget": every widget is compact, anything bigger opens; `size` capped at 32 | L13, §8.3, §12.2 |
| how far the bars reach | global: every surface, the running tool/app included, lays out off `content_rect()`, migrated in step 3b; it saves the most space | §8.5, `docs/history/shell_layout_2026-10.md` §13 step 3b, §12.2 |
| the project picker (2026-10-14) | deleted on every tier; the Make tab is the one projects list | L15, §8.10, `docs/history/shell_layout_2026-10.md` §13 step 7c |
| the Editor's PROJECTS button (2026-10-14) | removed; the X backs out one level, to wherever the Editor was opened from | L16, §8.10 |
| the desk's PROJECTS (2026-10-14, 2026-10-20) | the home in a normal window, on Make, the console home's design; picks open as windows | L17, §8.10 |
| the Editor from the console-mode home on a P4 (2026-10-20) | a window on the desk, console mode giving way | L18, §8.1, §8.10 |

Nothing is pending the owner.

### 1.3 The 2026-10 look (owner, 2026-10-15 to 2026-10-20)

- **Taste is the orchestrator's** (owner, 2026-10-15); engine and system
  changes are welcome for the look.
- **The rendering is modern, and the engine a superset** (owner,
  2026-10-15): anti-aliased type pre-rasterised at 2 or 4 bpp, smooth
  shapes, soft shadows, native 1× on the big boards; every one of those is a
  theme's choice, and the pixel look stays expressible. The pixel round
  this replaces is `cb195ae9` (`docs/media/look_2026-10/pixel_round3/`).
- **The seed set is three themes** (owner, 2026-10-15): Moy, with a Day
  mode (light, the default) and a Night mode (dark), which were the
  Daylight and Moy Night themes until the owner made them one theme the
  same day; Blocks; and Contrast (dark and light). Every other seed was
  deleted (look step 27); under the no-migrations rule a stored theme name that
  no longer exists reads as absent and resolves to the default. The theme
  keys are `moy`, `blocks` and `contrast`; a mode is the variant `light`
  (Day) or `dark` (Night).
- **The editors are restyled in their layout** (owner, 2026-10-15): the
  tab row, UNDO, REDO and PLAY, and the playtest window (look steps 23 and
  24). The Studio's panes (`docs/history/studio_2026-09.md`) were not built;
  the Editor's rules are `docs/media/look_2026-10/EDITOR_LAYOUT.md`.
- **Code syntax colours are theme roles** (decided by the orchestrator
  under the owner's delegation, 2026-10-15): theming's `code_*` content
  roles (the mockups' `syn.*`) leave the content exemption, and the
  readability check holds each to 4.5:1 on the code surface (§6). This
  reverses `docs/history/studio_2026-09.md` §5.1 and §8 and theming's
  "syntax colours are content".
- **The ricing themes are not built in** (owner, 2026-10-15): Luna, Coast,
  Pico, Bench and Adw are installable packages in the moybyte-org/carts
  repo (#247).
- **Fonts are not economised** (owner, 2026-10-15); a board whose image
  cannot hold its set grows its partition or sheds weight, the Zero first.
- **The built-in themes' fonts stay in the firmware image** as frozen flash
  bytes (owner, 2026-10-15). A board nearing its headroom floor grows its
  app partitions (a repartition is accepted: dev boards hold nothing of
  value). Moving the set to the flash store frees nothing: seeds are packed
  in the image and inflated on first boot, and the fonts deflate poorly, so
  the store copy would double the flash they use. A theme
  installed from the store brings its own font files in its package, as
  packages do.
- **Focus and current are theme properties** (owner, 2026-10-15: "make it a
  property, easy to change later"): `focus` draws by size class and
  `current` is a plate, a fill or a bar (`DESIGN.md`, the marks). Moy lifts
  its cards, edge-bars its rows on a light plate, underlines its small
  parts and marks current with a `value` plate; Blocks and Contrast keep a
  ring on cards and `invert` elsewhere, Blocks' current an underline and
  Contrast's a solid ink plate.
- **Button kinds replace per-app roles** (owner, 2026-10-15).
- **Theme colours are RGB** (2026-10-15, GAPS 1). This reverses theming's
  2026-09 decline of RGB (§12.1). A cart's colour stays `& 63`.
- **The public UI API is built as the screens need it** (owner,
  2026-10-15; `docs/ui_api_2026-10.md`'s decisions).
- **The editors' review calls** (owner, 2026-10-19): the narrow bar keeps
  its icons with a group gap between the tabs and the actions (D-02); a
  line-number gutter on by default (D-03.1); the desk's code stays 11 px
  (D-03.2); the desk's toolbar is the mockup's padded tabs, undo, redo and
  Play at the right (D-03.3); the playtest opens at 2× over the Editor
  (D-03.4); the symbol keys follow the last input (D-17); Play reads
  Restart and Stop while a playtest runs (D-23); a restored game lands on
  its pause menu (D-25); and the sprite tab is PICO-8's tool set with a
  more menu, a sprite map and compact parts (D-15, D-11, D-22). Steps 23,
  24 and 25 say how each is built.
- **Themes and chrome, the owner's calls on design review round 3**
  (2026-10-19; `DESIGN.md` and `THEMES.md` carry each as the look):
  - D-01: every system modal dims what it covers by about 40% (the look's
    `scrim`, `moy_ui_scrim`): the system menu, a card's menu and About wash
    the content under the bars, a dialog its app's surface, the pause menu
    the whole frozen game (on the desk, where a playtest pauses in its
    window, the content under the bar), on the handheld and the desk. The wash is drawn
    once a full frame over pixels that frame painted (the pause menu keeps the whole frozen frame and starts each
    frame from it; on the desk no window or desk frame is retained under
    it, a running playtest's quiet frame washes the game's own fresh pixels
    under the system menu, and the frame it comes or goes repaints the desk
    whole, but for the system menu's over a still desk and the pause
    menu's over its paused playtest, which ride with them,
    §8.6), so it never darkens twice; `tests/test_modal_scrim.py` holds
    it. A popover hung from its own control (an app's picker, a bar
    widget's calendar, Wi-Fi or power) does not dim, and keeps painting its
    rect alone over a playtest's quiet frames.
  - D-04: Moy keeps its focus edge and lays a light accent plate under the
    focused part (0.12 in Day, 0.14 in Night), which takes a card's
    corners on its first and last rows and is the line between the focused
    row and its neighbours, which draw no rule against it; Day's lifted
    hero wears an accent rim; a window behind takes `win.fill_back` on a
    `bar` header (Day `#e3e6ed`, Night `#0f1127`), a smaller shadow
    (`shadow_back` 0.3) and its controls in the `disabled` ink.
  - D-05 ("don't be too harsh"): a field's outline 3:1 or better
    (`field.border`), the rest of the structure ramp about 1.5:1 (Moy's and
    Blocks' `line`, Day's key borders and its keycaps' border), an Off
    track or a rest track held to 1.6:1 on the plate of a focused row too,
    and the empty Name field's "Your name" in the `disabled` ink (the
    override block's `hint`, `docs/ui_api_2026-10.md` §3).
  - D-12: no rim on any cover, Contrast's included; none had one.
  - D-14: Contrast's current is a solid ink plate with inverse text
    (`current.ink: value_ink`; an outline in a window behind and on the
    code editor's current line, whose code keeps its colours), its
    hierarchy weight and size (`dim` is its ink), its desk tiles and Moy
    button framed always (`tile_outline`, `bar.sys_line`), and Appearance
    offers it no accents (`accents: false`).
  - m04: a disabled key's plate fades half way to the surface with its
    ink. m07 and m17: Blocks' current is the underline -- a row's label
    underlined (`current.row: underline`), a ring the chosen swatch, a fill
    focus. m08: Calc's display ink is the plain ink (Moy's `lcd_ink`). m15:
    a toggle's knob is one colour in a mode, the state on the track. A
    current mark draws as in a window behind only where the shell says so,
    never because the input is touch.
- **The design review's calls on the shell's surfaces** (owner,
  2026-10-19) land where each surface's look step records them: the bar's
  Wi-Fi (14), the generated cover (17), the desk description's measure
  and the console's Desk tooltip (18, 27a), Settings' Brightness, field
  Back, toggle hints and foot fade (19), Day and Night and Default (20),
  Files' titles, covers and foot bar (21), the dock's glyphs (25) and the
  system menu (25a). Beside them: Copy is Duplicate wherever the shell
  says it; a hint that only means "activate the focused thing" is not
  drawn (Press, Choose); the handheld's hint strip leads in by the top
  bar's own margin (`MOY_UI_HINT_LEAD`, one side margin for both); Calc's
  tape fades its top when older sums run on above it (`ui.fade`); the
  gear means Settings and a game's Options alone and the tray a network
  fetch alone, the Editor's Config tab wearing `sliders`. Change, Carts,
  Get carts, Steady and Show FPS keep their words.
- **The owner's calls of design review round 4** (2026-10-19): the open
  cart's Restart cart is gone and a run restarts from the pause menu (D-06,
  look step 25a); the sprite tab is PICO-8's one row of tools and a gamepad's Y
  opens its more menu (look step 24). A Wi-Fi password field that masks what is
  typed (m18) is deferred to #240.
- **Deleting a cart asks first** (owner, 2026-10-20). Round 4's m23 made
  Make's X and DEL delete on one press (550e2428), on the belief that the
  Trash restores a cart; it does not -- a cart's delete removes its folder,
  and the Trash holds a kid's files alone -- so m23 is reversed: X, DEL,
  the desk PROJECTS window's Delete, a card menu's Delete and the system
  menu's "Delete cart" all open one `ui.dialog`, "Delete <title>?" over
  "This can't be undone.", Cancel (where focus opens) and Delete (the
  danger button), dimming what is behind it (D-01;
  `console_notices.DeleteConfirm`).

### 1.4 Scrolling and text editing (2026-10-27)

1. `BALL_COAST` ships at 0; a board raises it by hand in its board.toml.
2. `REPEAT_DELAY_MS` 400 and `REPEAT_RATE_MS` 50, in `moy_input`, overridable
   per board in its board.toml.
3. The host desk takes direct render of key frames like the P4, so the host
   is the test bed; its cursor is hidden in that bed.
4. No rubber band at the clamp.
5. The editors move onto the C UI library: the Code tab, theme Config, the
   Blocks outline and prompts, Config's cards, Cart info, Map, Music and the
   pickers lay out, focus and draw through the engine, as the apps do.
   Their look does not change: the Editor goldens stay byte-identical, and
   a detail the engine cannot draw extends the engine.
6. The text area is a public `ui` verb (`ui.text_view`), consistent with the rest of the API,
   so any app (a chat app, a terminal) gets a text area and restyles it
   through the theme and the per-call overrides.

## 2. The theme package

**A theme is one small, data-only package that re-skins every layer of the
OS** — colours, sizes, how surfaces are drawn, window decorations, fonts, icons,
images and, later, sounds. It installs, shares and remixes like a cart. Drawing
code never names a colour or a shape: it asks for a **role** or a **part in a
state**, and the active theme answers with colours (MOY64 indices, or RGB
placed in the shell's block of indices 64–127), sizes and a compiled
surface record. **Nothing in a theme can run**: a theme holds data and images,
and it may *name* installed carts (wallpapers) but never contain one.

The range it must cover is the owner's: the default look (the 2026-10 look,
§1.3) plus looks in the family of classic Mac OS Platinum, a riced tiling desktop, Windows
9x and XFCE/LXQt. That means bevels, pinstripes, gradients, flat panels and
different fonts, not recolours only.

§2-§7 are theming proper: tokens, parts, the surface painter, text,
sprites, images and the package. Where the bar goes, the two modes and the
home's structure are **shell layout** (D5, D7), §8, which the theme's
`layout` key carries (§3.8).

### The format

A theme is a folder with `"moybyte": {"type": "theme"}` in its manifest (the
field is `type` in the manifest's `moybyte` object, as for wallpapers and apps;
`kind` is the Files document vocabulary).
It holds a `theme.json` and optional **data** assets: images, fonts, sprite and
icon sheets. It never holds a cart or a script.

**Format 2, as the loader reads it** (`native/moy_ui/moy_theme.h`; format 1
frozen 2026-10-11, `surfaces` and `frame` defined 2026-10-12, `fonts` and
`layout` 2026-10-13; format 2, RGB colours and `look`, 2026-10-15 by
`docs/history/look_2026-10.md` step 5):

```json
{
  "format": 2,
  "name": "dusk",
  "colors": {
    "dark":  { "panel": 1, "title": 13, "title_ink": 7, "code_keyword": 12 },
    "light": { "panel": 6, "title": 48, "title_ink": 0, "block_events": 9 }
  },
  "metrics": { "radius": 2, "press_dx": 1, "press_dy": 1 },
  "frame": { "style": "platinum", "title_align": "center", "buttons": "C|T|ZM" },
  "fonts": { "ui": "ui.fnt", "title": "title.fnt" },
  "text": {
    "button": { "disabled": { "effect": "emboss", "color": "ink" } }
  },
  "surfaces": {
    "button": { "rest": "bevel2, fill surface", "pressed": "sunken, fill shade_lo" },
    "window.title": { "active": "pinstripe shade_lo/shade_hi, border" }
  }
}
```

- **`format`** (an integer, required): a reader refuses a newer one. A key or
  a role defined later is additive: an older reader ignores it, so it
  needs no bump. Renaming or removing a key, a role or a value's meaning bumps
  `format`. There is no migration pass, which is the store rule.
- **`name`** (required): 1 to 24 of `a-z`, `0-9`, `_`, `-`, starting with a
  letter or a digit (the carts repository's theme ids, so a theme made on a
  console can be published). A seed's name is reserved.
- **`modes`**: the modes the theme has, of `"dark"` and `"light"`; the
  nearest package of its chain that names them decides, and a theme silent
  on them has both. Appearance shows a Mode row only for a theme with two
  (Blocks declares `["light"]`), and `custom` carries its source's.
- **`inherits`**: one parent, by name (§4). A theme that names none inherits
  the default. A seed's key: a user package is a standalone copy and carries
  none (owner, 2026-10-14).
- **`colors.dark`, `colors.light`**: role → a MOY64 index or `"#rrggbb"`,
  or a flag role → `true`/`false` (§3.1). A role is a token of the live row
  or a content role. Every role is optional. The two tables are the theme's
  two modes (`moy`'s Night and Day).
- **`look`**: the theme model's fields
  (`docs/media/look_2026-10/THEMES.md`, style.json's `theme.look`): shared
  ones, then a `dark` and a `light` object of the mode's own, which come
  first. A field is a colour, a paint (a colour, `"vibrancy"`, a list of
  colours or `[t, colour]` stops, or `{"stops", "mode"}` with `smooth`,
  `dither` or `steps:N`), a name of its list (`focus.color` and
  `current.color` name a role), an integer or a number (in hundredths, each
  clamped to its range; `focus.plate.alpha` and `current.alpha` default
  differently per mode), a bool, a short text, or an array
  of those (`[width, colour]` for a line, `["soft", colour, alpha, blur,
  dy]` or `["hard", colour, dx, dy]` for a shadow); `null` is explicitly
  none. `tiles` maps an app to a colour and `app_buttons` an app and a
  button kind to a button's fields, each capped; `backdrop` holds the
  home's and the desk's lists of shapes (docs/history/look_2026-10.md step 7,
  `native/moy_gfx/moy_backdrop.h`: at most 16 a surface, coordinates clamped,
  a malformed list read as no backdrop); `icons.swap` is accepted and read by
  step 16. A field nothing sets takes the model's default, which for
  most colours is silent: the renderer derives it.
- **`metrics`**: `radius` (0-3), `press_dx` and `press_dy` (-3 to 3), each
  clamped to its range (§3.2); the default seed sets every one to 0.
- **`text`**: part → state → `{"effect": "none" | "shadow" | "emboss",
  "color": ROLE}` (§3.3); at most 32 per package. An effect other than `none`
  names its role, or it is dropped.
- **`surfaces`**: part → state → one line of the surface grammar (§3.4); at
  most 32 per package. Text the grammar does not take is dropped.
- **`frame`**: `style`, one of `classic`, `tab`, `platinum`, `win9x`, `flat`;
  `title_align`, one of `left`, `center`, `right`; `buttons`, each of `C`
  (close), `M` (minimise), `Z` (maximise) and `T` (the title) once, a `|`
  between them allowed. What a theme leaves out is its style's own (§3.5); a
  value the key does not take is dropped.
- **`fonts`**: role → a font file in the package, a role of
  style.json's `theme.font_roles`; a file name is 1 to 24 of
  `a-z`, `0-9`, `_`, `-`, `.`, not starting with `.`, so it never names a
  path. Each role comes from the nearest package that names one; an unknown
  role is ignored and an ill-formed name dropped. The file is §3.7's format.
- **`layout`**: `bars`, a list of up to four bars, at most one per edge, each
  a `size` and three slots of built-in widgets (style.json's `theme.layout`);
  it resolves whole from the nearest package that sets it, never merged.
  The caps, the clamps and the required-set rule are §8.3's and §8.7's.
- **`sprites`** and **`icons`**: a `.moygfx` file in the package each, a
  name of the `fonts` grammar ending `.moygfx` (another is dropped): the
  role sheet the sprite parts draw from and the theme's own icon tiles
  (§3.6).
- **`images`** and **`wallpapers`**: the theme's picture and the wallpaper
  cart it names, one slot each, `desk` (D6). The C loader passes them by
  (style.json's `theme.reserved`); the look reads them at every switch
  (§3.9).
- **Reserved:** `sounds` and `editor` are accepted and not read. A key is
  defined, additively, by the first reader that reads it.
- Any other key or role is ignored, a value of the wrong type is dropped and an
  index outside 0-63 is clamped, and each is noted (§6). A package of another
  `format` is refused: under the store rule a format-1 package reads as
  absent.

The metrics §3.2 lists beyond format 1's three (`border`, `title_h`,
`button`, `pad`, `gap`, `shadow`) would join `metrics` additively; none is
read (§3.5's footprint).

- **Variants are an axis, not two themes.** `colors.dark` and `colors.light`
  under one package keep D1's "one routine, two tables" and
  `variant()`/`set_variant()`. A light-only difference in chrome is a token
  difference, never a `light_chrome()` branch.
- A wallpaper id that is not installed falls back to the image, then to the
  default (§3.9).
- **`sounds`** and **`editor`** have no defined keys. `sounds` waits for
  #141; `editor` holds no code colours: syntax colours are theme roles, the `code_*` roles
  of §3.1 (decided 2026-10-15, `docs/history/look_2026-10.md`).

## 3. The vocabulary — the public contract

The vocabulary is an API. Renaming a role or a part is a breaking change and
bumps `format`. That is the lesson of GTK 3, whose themes styled an internal
node tree that point releases then changed (§11).

### 3.1 Colour roles

**The roles are the token row's** (`native/moy_ui/style.json`'s `tokens`:
`panel`, `edge`, `title`, `title_ink`, `accent`, `hilite`, `dim` and the §4.3
semantic roles of `docs/history/visual_identity_v1.md`, then the painter's roles
below, three of them the flags `surface_light`, `bar_light` and
`band_light`). They are the names `theme()`, `ctx.theme.colors()` and a
compiled app's `theme` imports answer; a role added later is appended to the
row, so a compiled app's role ids do not move. A value is a **MOY64 index**
or **RGB** (ruled in 2026-10-15, §12.1). The live theme's RGB values are
quantised to RGB565 and placed in the shell's block, indices 64–127, which
every canvas's colour table carries above MOY64 and no `pal()` remaps
(`chrome.live_colors`, `moy_theme_place`); the shell draws with those. Each
role also has its nearest MOY64 index, which `theme()`, a compiled app's
`theme` imports and a cart's canvas are given, and while a cart's code runs
a colour index is masked to 0–63 (`moy_ui.cart_text`), so a cart's colour
stays its index modulo 64. Another theme's preview draws its RGB as RGB565
and takes no slot.

A role a package leaves unset is **derived by a rule** (style.json's
`theme.derive`, §4): `surface` from `panel`, `focus` from `accent`, the inks
from fixed indices per variant, and so on, so a kid-made theme sets the seven
base roles or fewer. The readability check (§6) holds the **background/ink
pairs** listed in `theme.pairs`, which are pairs of the row's roles.

**Content roles** (D8) are a second set in the same `colors` tables
(style.json's `theme.content`), outside the live token row, so the app ABI's
vocabulary does not move: `code_text`, `code_keyword`, `code_string`,
`code_number`, `code_comment` and `code_builtin` for the Code tab's syntax
classes, and `block_<category>` for each block category (`block_events`,
`block_control`, `block_actors`, `block_looks`, `block_draw`, `block_input`,
`block_variables`, `block_lists`, `block_operators`, `block_sound`,
`block_myblocks`). Their derivation gives the editors' own colours: the syntax set per
variant (the dark classes' own colours, and the light set the Code tab drew on
a light surface), and the Scratch category colours in both. The six
`code_*` roles are theme roles held to a floor of their own, 4.5:1 on the
code surface (`surface`; style.json's `theme.code`, §6); every seed sets its
own (`docs/history/look_2026-10.md` steps 23 and 26).
`runtime/code_layer.py` and `runtime/block_editor_ui.py` read them through
the look (`runtime/shell/appearance.py`'s `content_colors`).

**Additive roles come with the code that draws them**, under §2's rule, each
joining the token row (and native/moy_app's vocabulary, which
`tests/test_ui_style_data.py` holds to it) with a derivation rule, so every
existing theme keeps its pixels. The surface painter's five (2026-10-12):

| role | what it is | derived from |
|---|---|---|
| `shade_hi`, `shade_lo`, `shade_dark` | the bevel shades a surface's edges draw in (§3.4) | `hilite` (white in the light variant), `dim`, black |
| `ink_accent` | emphasis text on the surface: the web console's pin | `accent`; `ink` in the light variant |
| `band_light` (a flag) | an in-window bar band is light, and its zone owners ink it dark | false; true in the light variant |

The last two are what were once `light_chrome()` branches
(`runtime/system/web_console_ui.py`, `runtime/shell/bar_layer.py`). A seed sets them
itself where its look needs them. An `_ink` for a fill role that lacks one
comes when a surface first paints text on it.

**The seeds.** The shipped themes are the three seed packages of §1.3
(`moy`, the default, `blocks` and `contrast`), `native/moy_ui/themes/*.json`
in format 2, each with its modes' tables, embedded in every image by
`tools/gen_ui_tables.py`; `native/moy_ui/moy_theme.c` loads them and
`runtime/shell/chrome.py`'s `theme_colors(name, variant)` is its Python face.
`runtime/shell/appearance.py`'s `set_theme` installs a set and writes it into
native/moy_app's live token table. The persisted settings are `theme` and
`theme_variant` in `system.json`; the wallpaper is the theme's (D6) and has
no row. There is no reader for old settings beyond that: under the no-migrations rule a stored name that no
package provides (a seed `docs/history/look_2026-10.md` step 27 deleted: `night`,
`indigo`, `berry`, `forest`, `slate`, `machine`, `platinum`, `win95`,
`outline`) reads as absent and resolves to the default in its default mode,
and a stored `skin` is not read at all. The skin axis is gone: its rows, its
verbs in the app ABI and its persisted setting.

### 3.2 Metrics

Logical pixels, multiplied by the chrome scale each tier already computes (the #203
floor; on a desk its unit of 2 at chrome size 1, `docs/history/look_2026-10.md`
step 9): `border`, `title_h`, `button`, `pad`, `gap`, `shadow`, `radius`
(0 to 3, D8: a corner cut from a precomputed mask in C, one per radius, so a
rounded box costs four masked corner spans and no per-pixel test), and the
pressed-label offset `press_dx`, `press_dy` (D8: the label of a part in its
`pressed` state shifts by that much, as Windows 95's buttons do). Format 1
defines `radius`, `press_dx` and `press_dy`, all 0 in the default seed.
Ruled 2026-10-12 with the surface painter, each the pick that builds least:
`radius` rounds the surfaces a theme paints (a part with no surface keeps
its square frame), and is in pixels, unscaled, as the toolkit's rings are,
since a mask exists for each radius; the pressed offset is multiplied by the
primitive's font scale and moves the label and a toolkit glyph, not an icon
or glyph the caller draws itself. Never absolute screen positions: a theme that
places things by pixel works at one resolution only, which split Rockbox's
catalogue by screen size (§11). Moybyte runs from 320×240 to 1280×800.

### 3.3 Parts × states

**The part list is derived from the toolkit's tables, not invented**, and was
frozen on 2026-10-12 with the first package key that names parts
(`surfaces`). It is the kinds of `native/moy_ui/style.json`'s `kinds` and
`metrics`, in the tables' order: `row`, `cell`, `cell_band`, `chip`,
`toggle`, `row_menu`, `row_list`, `row_chrome`, `row_cta`, `button`,
`button_primary`, `button_tonal`, `button_danger`, `tab`, `status`, `panel`,
`panel_title`, `toolbar`, `scrollbar`, `focus`, `text_field`, `game_btn`,
`game_icon_btn`, `mini_btn`, `dialog`, `group`, `value_row` (the public UI
API's group card and value rows, `docs/history/look_2026-10.md` step 12, whose
primitives paint no surface); then the chrome the WMs and the bar
draw, style.json's `theme.parts`: `window`, `window.title`, `window.button`,
`bar`, `bar.item`, `desk`, `desk.icon`. A window's buttons are one part
(ruled 2026-10-12, the pick that builds least: the frame style tells them
apart, §3.5). `tests/test_theme_loader.py` holds this paragraph to the
tables. A part added later is additive (§2) and joins the style file as a
kind.

**The quirks stay code** (ruled 2026-10-11; style.json's `quirks`):
`game_btn` and `text_field` draw on the game canvas, which a theme does not
reach (§5). `mini_btn`'s and `chip`'s unscaled label pads were re-ruled
2026-10-13, when layout came to read line metrics (§3.7),
and stay unscaled pixels: a chip's clip and minimum pads inset a label box
that is now the `ui` font's (its drawn width, its line advance at the
scale), and a mini button's label is placed from its rect's corner, not
centred, so no metric enters it. A pad is a pixel inset, as `radius` is
(§3.2); scaling either would move pixels at font scale 2 and up.

States are the ladder native/moy_ui resolves (`rest`, `hover`, `on`, `hot`,
`pressed`, `disabled`), plus `active`/`inactive` for windows (style.json's
`theme.states`). A state a part does not name follows style.json's
`theme.chain` at draw: `pressed → hot → rest`, `inactive → active → rest`,
every other state to `rest`. In
the file, parts and states are **nested objects**, never a flattened
`part:state` string; at runtime they are the C look's per-kind, per-state
table (§4).

**What "chrome" means** (for the literal-colour ratchet, §5): the pixels the shell, the WMs
and the toolkit draw around content. Content is exempt: the Paint and sprite
canvases, the map and scene views and the block editor's block colours,
which format 1 themes through the content roles (§3.1, D8). Code syntax
colours are content roles held to the code floor (§6).

**Text effects** (D8). A part in a state may name a text effect: `none`,
`shadow` (the label drawn once more, offset one pixel down and right, in the
named role, under the label) or `emboss` (the same, offset up and left in one
role and down and right in the label's own colour: the etched disabled text of
Windows 9x and Platinum). The package key is `text`, part → state →
`{"effect", "color"}`, and the default is `none` everywhere. Format 1
defines the key and the loader resolves it (`moy_theme_shape`); the
primitives draw it for the labels of the
parts that paint surfaces (§3.4). Ruled 2026-10-12: an emboss is three
draws, the effect's copy up and left, the label's colour down and right, then
the label; an effect draws only in a role of the token row, since a content
role is not in the row a look resolves against.

### 3.4 Surfaces: a one-line grammar, compiled once

A part's look is a short text: an **edge** (`flat`, `raised`, `sunken`,
`etched`, `bevel2` for Windows' two-level edge), a **fill** (`fill ROLE`,
`dither A/B`, `pinstripe A/B`, `gradient A/B` as dithered bands), an optional
`border` and `shadow`. This is how Openbox themes describe every bevel and
gradient in text, and how Windows drew all of Win95 from one `DrawEdge` routine
fed by a few shade colours (§11).

**The grammar** (frozen 2026-10-12; `native/moy_ui/moy_theme.c`'s
`moy_theme_surface`):

```text
surface := clause { "," clause }
clause  := EDGE | "fill" C | ("dither" | "pinstripe" | "gradient") C "/" C
         | "border" [C] | "shadow"
EDGE    := flat | raised | sunken | etched | bevel2
C       := a token of the live row, not a flag | a palette index 0-63
```

Words are lower case and split by spaces, `A/B` is one word, each clause comes
at most once, and a surface is at most `theme.surface_len` bytes. What a
surface leaves out is the part's own: no edge is `flat`, no fill is the
part's field, a bare `border` is the part's edge colour. An index over 63 is
clamped and noted; any other text the grammar does not take drops that
surface and is noted (§6). The package key is `surfaces`, part → state →
text, at most `theme.surfaces_cap` per package, resolved nearest package
first like the text effects (§4).

**How the painter draws one** (`native/moy_ui/moy_ui_paint.c`; ruled
2026-10-12, each the pick that builds least). Everything stays inside the
part's box. `shadow` takes the box's last two columns and rows in `shade_dark`
(D1's hard shadow, `MOY_UI_SHADOW`: the body offset two pixels down and
right), and the rest is the body. The fill goes under the corner mask of the theme's
`radius` (§3.2); then one-pixel outlines from the outside in: the `border`,
then the edge's levels, each with its top and left in one colour and its
bottom and right in another (square, the top-right and bottom-left corners
are the bottom-right colour's, as `DrawEdge`'s are; rounded, the two upper
arcs are the top-left colour's). `raised` is `shade_hi` over `shade_lo`,
`sunken` the reverse, `etched` sunken then raised, and `bevel2` an outer level
of the fill colour over `shade_dark` and an inner raised one, which is Win95's
button. `dither` is a checkerboard, `pinstripe` alternate rows, `gradient`
runs from A at the top to B at the bottom in bands of a 4×4 ordered dither,
one band per level. A pattern is anchored to the buffer as `fillp`'s is and
opaque. The parts that paint a surface in place of their field and edge are
the ones whose primitive draws a field: `row`, `cell` (its caption band and
label stay its own), `button` and its kinds, `chip`, `tab`, `toolbar`,
`panel` (in place of its outward border too) and `panel_title`; the
caller-coloured and game-canvas ones (the quirks above, `dialog`, `focus`,
`scrollbar`, `status`) do not. A chrome part (`window`, …) is painted through
the `ui` face, `ui.paint`, which paints nothing and answers False where the
theme gives the part no surface; its own field and edge, what a surface
that leaves out its fill or names a bare `border` paints, are the colours
its caller draws without one: `bar.item` `panel` (`accent` when on) edged
`chrome_ink` (`dim` when disabled), `desk.icon` `panel` edged `edge`, and
`desk` `bar` edged `bar_edge`. A `toolbar` surface that fills solid with
another role than `title` hands the toolbar's text that role's ink of
`theme.pairs` (`ui.toolbar` answers the ink).

- **The text never reaches a draw.** The loader compiles every surface once
  into a small integer record (a style id, `stid`; not
  `docs/surface_model_v1.md`'s Surface `sid`) in a C table. A draw is **one C
  call through the toolkit's sink** (`docs/kernel_toolkit_2026-10.md` §3.1),
  the path every primitive already draws through: a primitive whose kind has a
  surface paints it in place of its field and edge, and the WMs' decorations
  reach it through one `ui` face. Colours stay MOY64 indices resolved through
  the canvas row's palette at draw, as every primitive's do.
- **Patterns get a fast lane.** `fillp`'s 4×4 two-colour screen-anchored
  pattern is drawn by libmoy's `moy_ds_span` (`libmoy/moy_pixel.h`). An
  **opaque** pattern (its holes take a colour) is periodic in four pixels, so
  `moy_pat_span` derives the four column-phase colours once a span and writes
  the run as 32-bit words with `moy_fill`'s head and tail handling: two
  alternating words on the 565 build, one on the index build. A pattern with
  untouched holes keeps the per-pixel walk. `rect`, `circ`, `tri` and `oval`
  all reach the lane through the span path. It lives upstream in moy-spec, is
  re-vendored, and is pinned by the `fillp` conformance scene (odd and even
  starts at widths 1-31, a circle and a triangle). The painter is built on
  `fillp`'s semantics over this lane. Its kernel cost against a solid fill, on
  the P4 and the T-Deck, is recorded in **#66** (2026-10-12).
- **Not the PPA.** Painter fills stay on the CPU: a 1:1 PSRAM fill is
  bandwidth-bound and CPU ≈ PPA (`.claude/rules/boards.md`). The PPA stays for
  upscale composites.
- **No flush pump.** One painter call can cover a large area, and the T-Deck's
  flush feeds itself (`native/moy_flush/moy_flush.c`'s feeder loop), so the
  painter pumps nothing, as the sink's primitives pump nothing.

### 3.5 Frame styles and window decoration

Window frames are a **built-in style chosen by name**: `classic` (D1's), `tab`
(a Haiku-style title tab as wide as its title), `platinum`, `win9x`, `flat`.
Parameters: title alignment and a button-order string (`C|T|MZ`: close left,
title, minimise and maximise right). The styles are our code; a theme only
chooses. SerenityOS and xfwm4 work this way (§11). On the fullscreen tiers the
"window" is the app's full-screen strip, so the same style draws that strip.

**As built** (`native/moy_ui/moy_ui_draw.c`'s `moy_ui_frame`, reached through
`ui.frame` from `runtime/wm/wm_chrome.py`; 2026-10-12). `classic`
is D1's: a one-pixel ring and the strip's bottom line in
`shade_dark`, the hard black shadow, the app's identity stripe at the strip's
left with the title beside it, and a square close (its glyph boxed in the
title's ink). `flat` is the frame the WM drew in Python before D1, less the
strip's bottom line: a ring in `chrome_ink` (`dim` when inactive), no stripe,
the plain close glyph; `tab` keeps that ring and a red close. `win9x` lights the ring from the top
left, sets a title bar inside a frame of `panel`, and boxes each button as
Win95's `bevel2` with its glyph in `chrome_ink`. `platinum` centres the title,
pinstripes the active strip in `shade_lo` either side of it, and draws small
raised boxes with marks of their own (none for close, a square for maximise,
two bars for minimise). `tab` draws the strip in `panel` with a raised tab as
wide as its buttons and title. Each style has its own title alignment and
button order (classic, win9x and flat `T|MZC`, platinum `C|T|ZM`, tab
`C|T|MZ`), which `title_align` and `buttons` replace. A window's strip buttons
are laid out by the same C (`ui.frame_buttons`) the fat-finger hit-test reads,
each group resolving taps out to the window edge on its own side. The bar's
band (`ui.frame_band`) is the style's too: `win9x` lights its top row, the
others draw today's band, and a theme's `bar` surface replaces it. A
`window.title` or `window.button` surface a theme names is painted in place of
the style's own; the title takes `window.title`'s text effect; a taskbar chip
paints `bar.item` (focused `on`, minimised `disabled`).

Ruled 2026-10-12, each the pick that builds least:
- **One footprint for every style**: the window box's one-pixel ring, the
  `title_h` strip and the three-pixel drop shadow right of and below the box,
  which the WM's damage, its frozen chrome, its hit-tests and the content rect
  are built on. A style's geometry inside it is the style's code, so `metrics`
  keeps format 1's three; §3.2's `border`, `title_h`, `button`, `pad`, `gap` and
  `shadow` stay unread while the footprint is fixed. The footprint stays
  fixed (owner, 2026-10-14): a theme draws its own frame look
  within it through surfaces and sprites. **Later, if asked:** `border` (about
  1-4 px) and `title_h` as theme metrics, read by the WM on relayout; gated by
  the window-drag benches (`tools/p4_bench.py`) and the window tests.
- **Whether the strip spans the window is the style's**, not a parameter: `tab`
  does not, and the strip beyond its tab is `panel`, the window's own, since a
  see-through strip would need the WM's damage to cover the desk under it.
- **The shadow is `shade_dark`** in every style but `classic`, which keeps its
  black. It stays the footprint's three pixels in `classic` too: D1's two-pixel
  hard shadow is the surfaces' (`shadow`, §3.4), and a window's two-pixel one
  would leave the footprint's third column to the desk under it (ruled
  2026-10-12).
- **The identity stripe's colour is the title's** (ruled 2026-10-12, the pick
  that builds least): no app declares a colour, so `classic` picks one of the
  six PICO-8 accents (blue, green, orange, pink, yellow, red) by a hash of the
  title's bytes; the stripe is `3 * fs` wide and the title moves right of it.
- **`window`'s own surface is not read**: the ring is the style's, and a
  surface under the content would be filled and then covered every chrome draw.

A theme whose `look` is nearer in its chain than any frame style draws the
2026-10 look's frame instead (`docs/history/look_2026-10.md` step 6): rounded, with
the look's shadow and outline, and a footprint of its own that the WM reads
from `ui.frame_geom` (the corners' radius, a foot under the content, the
shadow's reach).

### 3.6 Sprites and icons (D3)

- **One sheet per theme at fixed cells** (style.json's `theme.sprite_cells`,
  in 8-pixel tiles of one 128-wide sheet): the title buttons (close, maximise,
  minimise) per window state, and 9-slices for `window` per window state,
  `button` per state and `desk.icon`'s tile. The fixed cells are what made
  Winamp's classic skins paintable in any paint program (§11). A part with a
  sprite draws the sprite; a part without one draws its surface — a cell left
  all slot 0 is no sprite, and a state whose cell is empty takes the one its
  chain reaches (§3.3). The toolkit draws no checkbox or radio (a toggle is
  a `button` kind), so the sheet has no cell for either; the cursor is
  declined (2026-10-14, `docs/history/theming_2026-09.md` §9 phase 6 step 9).
- **The sheet is a role sheet in the cart sheet's format** (ruled 2026-10-14,
  phase 6 step 9): `.moygfx`'s nibble names a slot — transparent, a role of
  the row, or black or white — resolved through the variant's table at the
  blit, so one sheet follows light and dark, an accent and a tint. No page
  edits it: the package carries it and `theme.json` names it (Edit theme…'s
  Config). A tinted glyph is a sheet in two slots. The slots and the cell map are style.json's.
- **Drawn in C, in one call each** (`native/moy_ui/moy_ui_draw.c`'s
  `sprite`). A 9-slice is one C call, not nine `spr` wrapper calls: its
  8-pixel corners whole, its edges and centre tiled, scaled by the chrome
  scale as a glyph is; a title button is its cell centred in the button's
  box. Neither draws outside its box, slot 0 leaves what is under it, and
  each slot resolves through the look's slot table, so a tinted glyph is a
  sheet in two slots and there is **no per-tint RGB bake cache**. The
  `window` 9-slice is the frame above the content — the ring's top row and
  the title strip, in place of the style's — under the title and the
  buttons; the ring's sides and foot and the shadow stay the style's, inside
  §3.5's footprint.
- **Icons:** a theme may ship its own icon tiles (`icons`, the pixel icon
  set's shape: eight 16-pixel tiles a row, one a slot of
  `runtime/toolkit/iconsets.py`'s `SLOTS`, colours, not roles, its clear pixel 16),
  and its look's `icons.pixel_slots` names the slots drawn from them over
  its icon set (`Appearance.pixel_draw`). The Editor's theme mode paints
  them: a smooth slot painted becomes a tile and is named there. The icons
  are the theme's alone; no sheet beside the carts overrides them
  (2026-10-26).

### 3.7 Fonts

Seven roles: `ui`, `title`, `mono`, `caption`, and the display's `key`,
`op` and `result` (a button kind's `font` leaf names `key` or `op`;
docs/history/look_2026-10.md step 22). A theme font is a bitmap font file.
Every seed's look names a family of the font set (`fam`, below); Moy's Day
draws Day Sans (IBM Plex Sans, renamed), its Night Inter. Tiny5 (D1) is a
Font preset of Appearance (style.json's `preset_fonts`) and the face of a
package that names it. Petme128 stays the carts' font, and the cart
`print` path is byte-identical, including the Lua C API that shares
`mg_text_raw`. The one text path for every app runtime is sprint 6's
`moy_text` (`docs/kernel_toolkit_2026-10.md` §4).

- **A theme font is a row behind `moy_text`'s font argument.** The call takes
  a `uint32` font id (petme128 is 0) at every scale; a theme font is another
  row there, never a second Python-side path. A chrome-side text lane was once
  reverted and later re-crossed on S3 A/B numbers (`moy_gfx_kernels.c`'s header
  records both), so each new lane is gated on an S3 A/B. A cart's `print` stays
  libmoy's `moy_print`.
- **Measuring is C.** `moy_text` takes `align` and `maxw` and returns the drawn
  width, and `moy_text_width` and `moy_text_wrap` measure in the same raster.
  A layout reads the line metrics of the font its text draws in, never an
  8px cell (below).
  Scaled text emits per-row runs through the span fill, not per-pixel stores.
- **The format is aligned.** Its header and tables are naturally aligned, and
  the parser assembles multi-byte fields from bytes or requires a `moybuf`
  (64-byte aligned) buffer. GCC on the S3 faults on an unaligned 16- or 32-bit
  load, which host and wasm do not reproduce, so a fuzzed font is loaded on
  glass at an odd address (`tests/on_glass.py`'s `fonts_match_host`).

**The font file, version 1** (`native/moy_gfx/moy_text.h`; defined
2026-10-13). Little-endian; every field is a byte but
`size`:

| offset | bytes | field |
|---|---|---|
| 0 | 4 | `MFNT` |
| 4 | 1 | version, 1 (a newer one is refused) |
| 5 | 1 | glyph box width, 1-16 |
| 6 | 1 | glyph box height, 1-16 |
| 7 | 1 | line advance, the height to 32 |
| 8 | 1 | ascent: the baseline, in rows from the box's top, 0 to the height |
| 9 | 1 | the first byte the glyphs cover |
| 10 | 1 | how many glyphs, 1 up, ending at or before byte 255 |
| 11 | 1 | reserved, 0 |
| 12 | 4 | `size`: the file's length |
| 16 | count, padded to 4 | each glyph's advance in pixels, 0-32; padding 0 |
| then | count × width × ⌈height / 8⌉ | the glyphs: a glyph's columns left to right, a column's bytes top down, bit 0 of each its top row (petme128's layout at 8×8) |

A byte the glyphs do not cover draws glyph 0.

**The font file, version 2** (defined 2026-10-15 by the 2026-10 look's step 3,
`docs/history/look_2026-10.md`): one size of one weight pre-rasterised on the host
(`tools/make_font.py`), each glyph its own box, at 1, 2 or 4 bpp, kerned by
pair. Little-endian; every field is a byte but those named:

| offset | bytes | field |
|---|---|---|
| 0 | 4 | `MFNT` |
| 4 | 1 | version, 2 |
| 5 | 1 | bpp: 1, 2 or 4 |
| 6 | 1 | line advance, 1 up |
| 7 | 1 | ascent: the baseline, in rows from the line's top, 0 to the line |
| 8 | 1 | cell: the advance a gap counted in cells takes, 1 up (the converter writes `0`'s) |
| 9 | 1 | records, 1 up |
| 10 | 2 | kerning pairs |
| 12 | 4 | `size`: the file's length |
| 16 | 256 | the index: each byte's record; a byte the font does not cover is 0, so record 0 (the converter's space) is the fallback |
| 272 | records × 8 | a record: its coverage's offset (3 bytes), box width, box height (both 0 for no ink, else both 1 up), the box's left from the pen and its top above the baseline (signed), the advance |
| then | pairs × 3 | a pair: left byte, right byte, its signed adjustment (never 0), ascending by (left, right) |
| then | the rest | each record's coverage in record order: its box's pixels row-major at `bpp` bits, the first pixel in the high bits of its byte, a glyph starting on a byte, its pad bits 0 |

A version 2 font decodes text as UTF-8: Latin-1 draws at its own byte, the
byte map's symbols (`MOY_FONT_BYTE_MAP`: − ← → ↑ ↓ … • “ ” ‘ ’ – —) at `0x80`
up, anything else and a malformed sequence at byte 0. Widths add the pairs.
`moy_text`'s fitted text cuts with "…" in a version 2 font that covers it
(`moy_text_cut_px`); petme128 and version 1 cut as they did.

The reader refuses a file over 65536 bytes, shorter than its header or than
`size` says, with another magic or a newer version, a field out of range, a
reserved or padding byte or pad bit set, an index past the records, a record
whose coverage does not follow the one before, pairs out of order or of 0,
tables that do not fill `size` exactly, or a bit set below a version 1 glyph
box, each by its own code; a refused file loads nothing. A loaded file is
copied into a buffer its row owns (PSRAM on the boards, so drawing never
shares the S3's flash cache with code); ten rows load at once beside
petme128, and an id names its slot and the slot's generation, so a freed
font's id is refused everywhere it is passed. Petme128's row keeps
`mg_text_raw`'s cell loop and a version 1 font draws through
`mg_text_font`, a lane of its own: the S3 A/B that gated this step is
recorded in `moy_gfx_kernels.c`. A version 2 font draws a glyph at a time
through `mg_glyph_cov`, which blends its coverage into the target, from a
ramp over a known background or reading what is under it
(`docs/history/look_2026-10.md` step 4).
`moy_ui` faces it: `font_load`, `font_free`, `font_info` (box or cell, height, line,
ascent, range), `text_width`'s font argument, and `theme_fonts` for the `fonts` key
(§2). `tests/test_font_lane.py` holds both rows to a reference raster, the
refusals to their codes and a fuzz to the target, and the boards' module to
the host's answers; `tests/on_glass.py`'s `fonts_match_host` loads the fuzz
corpus on glass at an odd address.

Ruled 2026-10-13, each the pick that builds least:
  - **Bytes, not a `moybuf`.** The reader assembles `size` from its bytes
    and every other field is one, so a source at any address is read alike
    and no caller aligns anything.
  - **Advances in the file, the glyph box fixed.** A per-glyph advance
    makes a proportional font (Tiny5, step 3) one format with petme128; a
    glyph keeps the box's width, so no per-glyph offsets.
  - **A wrap counts bytes, or pixels.** `moy_text_wrap`'s words mode wraps
    in `cols` bytes, the cells a body laid out in 8px cells counts; its
    pixel mode (`MOY_WRAP_WORDS_PX`, `ui.wrap_px`) wraps in a font's
    advances, for wrapped text drawn in a theme font (step 3).

Ruled 2026-10-13 by step 2 (layout reads line metrics), each the pick that
builds least:
  - **A font per role, read where its text draws.** `moy_ui` holds the font
    each role draws in (`moy_ui_font_use`; `ui.use_font` and `ui.font`, which
    answers the id, the glyph box's width, the line advance and the
    baseline), petme128's until one is installed and again once it is
    freed. The toolkit's primitives draw and measure their labels in it: the
    `ui` role for every kind, `title` for `panel_title` and `window.title`.
    A compiled run's canvas and the game canvas (`game_btn`,
    `game_icon_btn`, `text_field`) stay petme128. A width is
    `moy_text_width`'s, a fit `moy_text_fit_px`'s, a line box the line
    advance at the scale, and a gap counted in cells the glyph box's width.
  - **The shell's layouts read the same numbers when they are built.**
    `Layout.font_w` and `line_h` are the `ui` role's, `CodeLayout.cell` and
    `lh` the `mono` role's (`lh` with `_CODE_LEAD` rows); the bar's window
    chips and the desk's label pills measure with `ui.text_width`'s role.
    A theme switch installs the fonts and rebuilds the layouts (step 3,
    below). The app bodies that lay their own text out in 8px cells (the
    editors' content, Files, Get Carts, the Storybook) keep them.
  - **The measure is the draw.** `tests/test_line_metrics.py` installs the
    step-1 sample font for every role and holds each primitive's label
    inside its box, centred by the font's own width and line, the frame's
    title clear of its buttons and the game canvas unchanged.
- **App carts.** `ui` is the real toolkit, handed to every `type: "app"` cart,
  and those carts lay out in 8px cells and `print` in petme128. A theme font
  therefore reaches app carts **only together with** a published `text_w()` and
  line metrics; until then a cart's code runs inside the app-cart boundary
  (`ui.cart_text`, below), so its `ui` and its `print` draw petme128.
- **Legibility.** A theme font has a text-size floor tied to #203's chrome
  floor: `chrome.text_scale_floor` reads the same declared diagonal, with the
  same opt-in, and answers the font scale at which the `ui` font's ascent
  reaches `MIN_TEXT_MM`; the floor lifts the effective font scale and never the
  setting. `docs/history/visual_identity_v1.md` §5.4 asks for on-glass font tests;
  they were taken on the T-Deck and the Guition S3 (2026-10-13).
- **Licence:** Tiny5 is OFL-1.1; `THIRD_PARTY.md` §3.1a carries its notice,
  its pinned source and what the conversion changed.
- **The font set** (the 2026-10 look's step 3): `native/moy_ui/fonts/` holds
  the faces `tools/make_font.py` builds from four OFL-1.1 families (Day Sans,
  from IBM Plex Sans, whose name "Plex" is reserved; Inter; Atkinson
  Hyperlegible Next; JetBrains Mono), each `FAMILY-SIZE-WEIGHT-BPP.fnt` with
  its family's licence `FAMILY.txt`, and `set.json` names the faces each tier
  takes. A board image freezes its tier's (`fonts_data`, chosen by
  board.toml's `tier`); `runtime/toolkit/fontset.py` reads them, and
  `appearance.font_file` answers a face, and `X.txt` beside it, after the
  seeds' files. `THIRD_PARTY.md` §3.1b carries the notices.

Ruled 2026-10-13 by step 3 (Tiny5 lands), each the pick that builds least:
  - **The file.** `tools/make_tiny5_font.py` samples the pinned Google Fonts
    TrueType (default instance) onto its 128-unit pixel grid into
    `native/moy_ui/themes/tiny5.glyphs`, the glyphs as rows, and builds
    `tiny5.fnt` from that: a 6×7 box, ascent 6, line 8 (the font's own
    ascender and descender add up to 9; 8 is petme128's rhythm and centres
    its five-row capitals in the line). GPOS kerning is not carried. A seed
    package's font files sit beside it in `themes/`, each font `X.fnt` with
    its licence's full text `X.txt`, and the tables embed every font a seed
    names and its licence (`ui.font_file`).
  - **Coverage: ASCII.** The font covers `0x20`–`0x7E`. Format 1 indexes a
    glyph by byte and text is UTF-8, so #135's accented Latin and Cyrillic,
    which Tiny5 draws, cannot be addressed until a codepoint map does it
    (#135's work); a byte the font does not cover draws glyph 0, the space,
    as petme128's row does.
  - **Every seed names the font set's JetBrains Mono for `mono`** (the
    Code tab, `docs/history/look_2026-10.md` step 23), and its look's family for
    the rest; a role neither covers draws petme128. A face of the font set
    (`runtime/toolkit/fontset.py`) is the image's, not embedded in the tables.
  - **A look's `fam` names the type**: a family of the font set whose
    faces, sized per metric class (`fontset.ROLES`) at the weights its
    `w_med`, `w_semi` and `w_bold` move the type scale's steps to, win over
    the faces the chain's `fonts` give; `docs/history/look_2026-10.md` steps 26a
    and 26b have the roles, the sizes, the weights and the custom's FONT
    row.
  - **The print font.** A canvas's `print` draws in one process-wide font
    (`moy_text_print_font`; `ui.print_font`), the `ui` role's, through the
    print gate and the `text` op; `ui.print_width`, `ui.print_fit` and
    `ui.wrap_px` measure in it. The Code tab and its popup print in `mono`.
    The bodies laid out in 8px cells keep their cells and draw in the `ui`
    font, narrower than the cells. The kernel's chrome
    (`native/moy_play/moy_chrome.c`) rasterises in petme128 with no VM, and
    the run's crash panel and hold pill, its pictures on the game canvas,
    replay in petme128 too; the strips, menus, toasts and banners replay in
    the print font.
  - **The app-cart boundary** is `ui.cart_text`: on while a cart's code runs
    (its body, `_layout`, `_init`, `_update`, `_draw`; a wallpaper cart's
    frame), every role and the print font read petme128, and the shell's
    come back after. A compiled run's canvas is petme128 already (step 2).
    A built-in app cart (`system_carts/`, `system_api.draws_in_look`:
    Notes) runs outside it: its page is the verbs' and its note the editor
    handle's, both in the look's colours and faces; a copy of it is a
    kid's cart, inside the boundary.
  - **The floor is the smallest text the owner kept at 1x**: `MIN_TEXT_MM`
    is Tiny5's six rows on the 7" 1024×600 Waveshare P4 (0.89mm; that panel
    runs font scale 1 by the owner's call of 2026-07-12), under the 3.5"
    Guition S3's 0.92mm (#203: its 1x text "reads fine"). It lifts nothing
    in the lineup and guards denser glass, as the tap floor does; petme128
    clears it on every panel. It is measured on the GLASS (a `render.scale:
    2` surface pixel is two of its), and every face of the font set answers
    1 on both P4 desks (`tests/test_native_desk.py`).
  - **The fonts go with the VM.** The kernel's stop frees every font row
    (`moy_text_vm_stop`), so a restarted VM loads its theme's into empty
    slots; where nothing frees them (a host process), a load that finds
    every slot full frees them all (`moy_font_reset`) and loads again.
  - **The site carries no Tiny5.** Its display face stays petme128's webfont
    (`site/petme128.woff2`), the carts' font, so it has no Tiny5 notice to
    carry; the firmware images and the web runner carry the font, and
    `THIRD_PARTY.md` its notice.

### 3.8 Layout

A theme's `layout` key holds its bars and its home's arrangement; §8.3 is
its format, §8.7 what the loader checks and §8.9 how the user's override
stands over it. It was reserved with no keys in format 1 and defined
additively on 2026-10-13, so no theme written before then is broken by it.

### 3.9 Images and wallpapers (D6)

A theme ships one static **image** and may name one installed **wallpaper
cart**, for the desk and the home alike (D6, one slot: `desk`). The
wallpaper is the theme's: the user's pick (Appearance's WALLPAPER, Paint's
WALL, `ctx.wallpaper`) is a change to the theme, written into `custom`, and
there is no setting beside it (owner, 2026-10-14). A cart is
`"wallpapers": {"desk": id}`; a picture is `"images": {"desk":
"desk.moyimg"}` with the file in the package (the Wallpaper pick copies Paint's
picture, the wallpaper copy WALL wrote); NONE is `"wallpapers": {}` with no
`images`, the theme naming its wallpapers and none of them, so the desk is
`desktop`'s fill under the theme's backdrop. A theme naming neither key shows
its backdrop where its `look` has one (`moy`), and else the console's default,
the first wallpaper cart (Moy's night on a seeded store). The look reads the
keys at every switch (`Appearance.theme_wallpaper`): the named cart where it
is installed, else the picture, else NONE or the backdrop, else the default;
a SAFE start runs no cart. The backdrop is rastered into the picture's own
bake when its records or the colour its fill paints move (a theme load or
mode switch; an accent change keeps it, `Wallpaper._draw_backdrop`): the
desk draws `backdrop.desk`, the home and the handheld's every screen
`backdrop.home`.

A picture is at most 1024x600, the desk boards' drawing and wallpaper size
(owner, 2026-10-21), and is read from its file a row at a time straight into
one screen-sized buffer, cropped about its centre to the glass's aspect and
sampled nearest as it comes: no raster of the picture and no copy of its file
(`Wallpaper._read_picture`, `native/moy_store/moy_img.h`'s streamed reader).
The screen it made is kept beside it (`thumbs/pic<w>x<h>.mct`, stamped with
the file's `bytes_sig`), and the next boot reads that instead. A wallpaper
cart may be a picture: `"moybyte": {"pictures": {"320x240": "garden_small",
"1024x600": "garden_desk", "1280x800": "garden_desk"}}` names one of its
`images/` per glass size (any name; one image may serve several), and the desk
draws the one made for its glass, else the smallest larger one shrunk once
and kept, else the largest, running none of the cart's code (which another
host runs instead).

- **Nothing executable rides in a theme.** Installing a cart is exactly what
  `system_api.py` never grants (`carts`, "the single most important entry"). A
  theme that names a cart does not install it: the cart comes from the store
  like any other, and an absent one falls back to the theme's image.
- **The default is static.** An animated wallpaper defeats the redraw gate and
  the home's retained stamp (`launcher_layer._try_stamp_retained` is skipped
  while `ws._animating(dt)`). That is the open live-wallpaper defect (#66, #73). So
  Moy's night, the default, is a still image, or animates only small dirty
  rects.
- **A wallpaper per glass** (docs/history/look_2026-10.md step 28, GAPS 23). A
  wallpaper cart whose manifest says `"moybyte": {"glass": true}` draws on
  the system canvas itself, so `W` and `H` are the glass's, and lays out
  around `QUIET`, the shell's quiet rectangles for the surface it draws
  behind: the bars on every surface, the home's title, metadata and hint
  strip (the desk home's hero, details and shelf), and the desk's icon
  column. The `SUBJECT` it sets, an (x, y, w, h) on the glass, is the
  rectangle a new window's default place keeps clear of
  (`WindowedWM._clear_of_subject`). Its preview renders at the glass's size
  around the quiet rectangles it last drew with. Any other wallpaper draws
  its 320×240 frame on the game canvas, cover-cropped onto a bigger glass.
  Moy's night draws per glass; over any wallpaper the home's title,
  metadata and hint labels are white, the title and metadata over a black
  shadow, as the desk's labels are.
- **Readability at home.** A wallpaper with its own text or characters
  mid-screen fights the carousel (mocked 2026-09-27 with Space Desktop). The
  home's card band has no surface of its own: the cards lie on the
  wallpaper. A band surface (`home.band`, a dither scrim a theme could set)
  was removed on 2026-10-15 (owner): no seed used it; it
  could not reach the real case, a kid's own busy picture as the wallpaper
  in `custom`, which turns nothing on; and an opaque scrim hides the
  wallpaper the home exists to show. Where a carousel title proves hard to
  read on glass, the planned answer is a label pill under the focused
  card's title; it is not built.
- **Prefer wallpapers that need no VM.** A live Python wallpaper keeps the
  Python VM up, against the memory goal of `docs/native_kernel_2026-09.md` §4.4. Images and
  Lua or wasm wallpapers are preferred; a Python wallpaper stays allowed and is
  charged as such.

### 3.10 Accent and tint: every colour role derived

**The rule** (owner, 2026-10-14: every colour role, the code and block
colours with them, follows from a source theme, MODE, one ACCENT and one
TINT, readable by construction). It runs in C (`moy_theme_derive`),
integer only, on the loader's luminance (`moy_theme_lum_of`: an
index's from `moy_theme_lum`, an RGB colour's from its RGB565) and the
loader's own predicate: `clears(a, b)` is
`(hi + 500) * 10 >= MOY_THEME_CONTRAST * (lo + 500)` over the two
luminances, the pair check §6 already makes. Its inputs are the source's
two resolved rows (the token row and the content set, both variants), an
**accent** — one of sixteen pairs (A, deep) in style.json's
`theme.accents`, one of the four presets in `theme.presets` (Indigo, Berry,
Forest and Slate, the seeds `docs/history/look_2026-10.md` step 27 deleted, each an
A per variant that is its own deep: the dark one black clears, the light
one white clears; picked by its dark value), or the source's own — and a
**tint** — NONE or one of
eight ramps of MOY64 indices in `theme.tints`, dark to light, never black
(0) or white (7). The role classes each step names below are
`theme.recolour`. The lists are data, as `font_roles` is, and the sixteen
accents and eight tints the mockups draw are the set (owner, 2026-10-14);
the mockups' `derive()` (`docs/media/theming_2026-09/appearance/mockups.py`)
carries them. **Every accent is one black clears** (`clears(A, 0)`), because
style.json's kinds paint a literal black label on an accent fill (`chip`,
`button` and `row_cta` in `on`); that is what lets the accent fill every
accent role with no new ink role. A preset's light value clears white
instead, as Moy Day's own accent does.

Per variant, with B the source's row and R the result, R starting as B:

1. **Tint** (none on NONE). Each background role — `panel`, `surface`,
   `surface_alt`, `desktop`, `desktop_pattern`, `title_inactive`, `bar`,
   `dim`, `hilite`, `shade_hi`, `shade_lo` — whatever its B value, and each
   line role — `edge`, `border`, `bar_edge`, `shade_dark` — whose B value is
   not 0 or 7 (D1's black outlines stay), takes the ramp entry nearest B's
   value in luminance (the lower index on a tie) among the entries that
   clear, against black and against white, every floor B's value clears;
   with none, B's value. Keeping each
   background on its side is what keeps the literal inks of the kinds
   readable and the flags `surface_light`, `bar_light` and `band_light`
   true.
2. **Accent** (none when the source's own is kept). `accent`, `title`,
   `title_active`, `selection` and `value` take A, and so does a lifted
   card's rim (`look.focus.lift.rim`) where the source's is its `accent`. Each of `edge`, `border`,
   `hilite`, `shade_hi`, `bar_edge` and `desktop_pattern` whose B value is
   B's `title` or B's `accent` — a role the source tied to its identity
   colour — takes A. `focus` and `ink_accent` take the first of A, deep and
   R's `ink` that clears R's `surface`.
3. **Inks.** Each ink of `theme.pairs` but the dim inks (step 4's)
   against its backgrounds — `ink` (`surface`, `surface_alt`),
   `chrome_ink` (`panel`, `bar`), `title_ink` (`title`, `title_active`),
   `selection_ink` (`selection`) — keeps B's
   value if it clears every one, else takes black or white, whichever has
   the larger least ratio (black on a tie). Black or white clears any single MOY64 colour,
   and step 1 keeps the backgrounds a source pairs with one ink on one
   side, so this lands; `tests/test_theme_derive.py` holds it for every
   seed, accent, tint and variant.
4. **Dim inks.** `ink_dim` (`surface`) and `chrome_ink_dim` (`panel`,
   `bar`, `title_inactive`) keep B's value if it clears them, else take the dimmest that does
   (the least ratio, the lower index on a tie) of the ramp and the greys 5,
   6, 13, 49, 50 and 53, else R's `ink` or `chrome_ink`.
5. **Content.** Each `code_*` role keeps B's value if it clears R's
   `surface` at the code floor (`theme.code`, 4.5:1), else takes its value
   in style.json's derive set for a surface of that side (the `light` set
   where black beats white on R's `surface`, the `dark` set else) if that
   clears, else black or RGB white, the larger ratio: one of the two always
   reaches it, where MOY64's white does not on a mid grey.
   `block_*`, `play`, `author` and `danger` keep B's: they are meanings, not
   looks. A button kind that inks `danger` or `value` on its field holds the
   pair to the floor itself, through its floor forms (style.json's `floors`,
   docs/history/look_2026-10.md step 10).

The flags keep B's. NONE with the source's own accent answers B but for a
dim ink or a content colour B itself leaves under the floor, which steps 4
and 5 repair as they would under any tint (several seeds' light variants
carry one; their pairs of `theme.pairs` all clear). A source a hand edit
made unreadable in `theme.pairs` stays reported as any hand edit is (§6).

**Where the result lives: plain roles in `custom`'s `theme.json`.** Both
variants, every role, written by `ui.theme_custom` with the
source's `look` flattened beside them, so the loader reads it as any
package, Edit theme…'s Config shows the colours the
console draws, and a console without the step, the web console and the
carts repository read the same row. The inputs ride in the package's
manifest, `"moybyte": {"type": "theme", "look": {"from": "moy", "accent":
14, "tint": "plum"}}`, read by Appearance alone: ACCENT and TINT show them,
and each change re-derives from `from`'s resolved rows, never from the last
result, since a ramp quantises and quantising a quantised row drifts. A
colour (or any key the derivation writes) saved through Edit theme… drops
`from`, and the file as saved is the source from then on. A package whose whole rows would pass `theme.bytes` (a
source carrying a large `images` value, say: every seed's fits whole) is written without the roles whose value §4's
rule gives back, which resolves to the same row, and one that does not fit
even so is refused (`MOY_THEME_BIG`). Declined: `accent` and
`tint` keys the loader derives from, smaller on the card, because the
derivation would then run in the boot path and in every resolve, a console
without it would draw the source's colours under the kid's name, Config
would show two numbers in place of the colours, and a role set by hand
would need a precedence over the rule.

## 4. Fallback and inheritance, resolved once

Every key but `format` and `name` is optional. A theme resolves through
`inherits` (one parent; a theme that names none inherits the default) and ends
at the default seed, `moy`, which sets every role. The default lends a
package that names no `inherits` its roles, metrics, surfaces, fonts and
layout, but not its look (its fields and backdrops), which a chain takes only
by name (`docs/history/look_2026-10.md` step 27), as `blocks` and `contrast` draw
their own looks alone; a chain that sets no look at all (a colours-only
package) takes the default's geometry and marks flat, no shadow and no
backdrop, every colour the look would set derived from its own roles
(step 33). A user
theme is a standalone copy, flattened when `custom` is made, with no `inherits` (owner,
2026-10-14), and a role a copy lacks — one a later
format added — takes the default's, as any unset role does. The chain
is **capped at four links and a cycle is refused**, at install and at resolve
(style.json's `theme.depth`). Each role comes from the nearest package that
sets it; what is still unset is filled by `theme.derive`, the variant's own
rules first and then those of both variants, a rule giving a fixed index, a
flag or another role's value. So every role of the row is set, and a five-line
recolour is a valid theme from which a full Platinum grows, as Winamp skins
fell back to the base skin and KDE Plasma themes fall back to Breeze (§11). A
missing sprite draws the surface; a missing surface draws the default for that
part; a missing state follows §3.3's chain.

**All of it resolves at flatten, never at draw.** `moy_theme_resolve` turns a
(theme, variant) into the token row once; the look resolves the style
against that row lazily, per kind, into three palette indices per state, and
again only when the skin's or the row's generation moves
(`native/moy_ui/moy_ui.c`). A widget pays an array read. `theme_colors` caches
the row's dict per (theme, variant). The THEME grid's previews use
colours only, resolved from a package's bytes without installing it
(`ui.theme_resolve`); they do not load other themes' assets,
since the face's preview ring holds four looks and would thrash.

## 5. Who reads it, and how a switch invalidates

- **The toolkit and the WMs** are the only drawing code that resolves parts and
  surfaces. A screen that draws chrome by hand is moved onto the toolkit; a literal colour in chrome (§3.3's definition) is counted by
  a ratchet that only falls (`tests/test_chrome_literals.py`).
- **Shipped apps** read roles through `ctx.theme` and draw through the toolkit,
  so they restyle with every theme (D4).
- **App carts** read the live tokens through `theme()` (unchanged; §3.1 keeps its
  keys). **Games** do not get `theme()`: that would be a cart-verb change, and
  it goes to moy-spec as a proposal if it is ever wanted. The Player surface
  stays the cart's. That is the "don't theme our apps" lesson turned around: the
  chrome is fully themeable, game pixels are not.
- **A switch is one Class A dirty plus one integer.** A theme load, switch,
  install or in-place edit is an un-attributed Class A dirty (`ws._dirty`, the
  global epoch; `docs/surface_model_v1.md` §3) and moves one monotonic
  integer, `ws.look.look_gen`: a plain attribute `publish()` sets from
  native/moy_app's token-table generation (`theme_write` answers it), which
  every write of the live row moves — an icon sheet's adoption publishes too.
  Every cache whose pixels read the look folds `look_gen` into its key and
  replaces its entry when it moves:
  - the home statics and retained stamp (`home_band._statics_base`);
  - the bar strips (`bar_layer._cart_bar_key`);
  - `wm_chrome`'s frozen chrome, the focused window's content freeze
    (`_content_static`) and every window buffer behind it (each `_Win`
    carries the generation its buffer was drawn under);
  - the desk backdrop the windowed tier restores from (`wm_desk._desk_sig`);
  - the toolkit's look ring (`ui._bind_theme`, keyed on the theme dict and
    an epoch `ui.look_moved()` bumps: an entry from an older one is
    re-resolved in its own slot).

  An install or uninstall (`Appearance.install_theme`) drops every shared
  token and content dict (`chrome.forget_themes`), since every theme resolves
  along `inherits` to the default and one install can move any name's row,
  then re-applies the live theme; the layout answers are cleared by that
  re-apply. A cache that reads no theme — the covers, the bar's kind images,
  the font table (keyed on the package that provides a file and the file: a
  seed's from the image, whose bytes an install cannot move, a store
  package's from its folder, dropped at every store change) — folds nothing. The web head
  runs this same console and re-rasters its frames, so it holds no cache of
  its own.

  **No cache keys on object identity.** A freed theme's `id()` can be reused by
  MicroPython, and identity keying is what `docs/surface_model_v1.md` §7 retired.
  The kernel ABI's theme handle carries the same generation (index plus
  generation, `docs/native_kernel_2026-09.md` §4.3). This is the existing dirty protocol, not a new
  mechanism; `set_theme` pokes no cache. `tests/test_theme_look_gen.py`
  re-installs a package under its own name with one role changed and holds the
  live console's pixels to a console booted under the edited package.

## 6. Safety

Kids will trade themes, so a theme is untrusted input:

- **Data only.** Parse, validate and clamp (`native/moy_ui/moy_theme.c`, a
  strict JSON reader of its own, built on every tier): a theme.json over
  `theme.bytes` (16384) is refused, as is malformed JSON, nesting past eight
  levels, a missing, non-integer or other `format`, a missing or ill-formed
  `name`, a seed's name, or an `inherits` that names no package, cycles or
  runs past the cap. An index is clamped to 0-63, a number of `look` to its
  range, a value of the wrong type (a colour that is not `#rrggbb`) is
  dropped and an unknown key or role is ignored, each noted to the caller.
  A metric is clamped to its range, a text effect with an unknown part,
  state or effect name, or without its role, is ignored or dropped and noted,
  and a package holds at most 32 effects; a surface the grammar does not take
  (§3.4) is dropped and noted, and a package holds at most 32 surfaces. No scripts, no
  carts inside, no plug-ins (§11: GTK 2 engines, Haiku decorators, and a KDE
  store theme that deleted a user's files). `tests/test_theme_loader.py`
  fuzzes the reader and holds the boards' module to the host's answers.
- **Readability check** at load and in the editor. Every pair of `theme.pairs`
  must clear `theme.contrast` (a WCAG ratio of 3.0, the floor for large text
  and UI parts) on each colour's luminance (an RGB colour's as its RGB565
  shows it); a failing pair sets its bit in the report
  and never refuses the theme. The current plate's bit follows the pairs,
  and then one bit per code role: each of `theme.code`'s roles must reach
  its own floor, 4.5:1, on the code surface (`docs/history/look_2026-10.md`
  step 23). For a dither or pinstripe fill the ink is
  checked against **both** colours, in the look, since the check needs each
  part's ink, which the style resolves there (ruled 2026-10-12):
  `moy_ui_pattern_unreadable` (`ui.pattern_unreadable`) answers the installed
  shape's pattern surfaces whose label ink misses the floor against either
  colour, a kind's ink as the style resolves it and the chrome parts' as the
  frame and the bar draw them; a part that paints no label is not checked.
  The derivation (§3.10) never writes a pair under the floor; a hand edit
  through Edit theme… can, and its reader is Config's badge at the save
 . Every seed passes both checks in both variants
  (`tests/test_theme_loader.py`), the code floor among them.
- **The loader is the safety** (owner, 2026-10-14). A theme cannot run code,
  so the validation above, the readability report, the layout's required set
  (§8.7) and the fuzz gates on the host and on
  every board's module are the whole of it. A package that will not load
  leaves the slot as it was, and a `theme` row naming one boots the default
  seed with the loader's note shown once; every board has a reset button.
  No strike ledger, no persist-after-render, no per-board reset input (§12.1).
- **Two themes are firmware, not files.** The default and a high-contrast theme
  (`contrast`) are frozen into the image under reserved ids
  that no user theme can take; every seed is in the image (§3.1).

## 7. Placement against the native kernel

Sprint 6 of `docs/native_kernel_2026-09.md` put the toolkit's bodies in C with
style as data (`docs/kernel_toolkit_2026-10.md`); the WMs stay Python (its §10
question 8: sprint 7 does not run); sprint 5 turned the app roles into an
import-shaped ABI with a live token table and its generation. **Theming is
written in C against the toolkit, with no Python reference** (owner,
2026-10-11). The pieces:

| theming piece | built where | why |
|---|---|---|
| the package format and the loader: validate, clamp, inherit, derive, readability (§2-§4, §6) | **C**, `native/moy_ui/moy_theme.c`, with its rules and seeds as style.json data | the toolkit's look is C; the loader writes the token row the look resolves against, and the boards, the host and the browser run the same reader |
| the seeds as packages and style data | data | data crosses unchanged |
| toolkit reach | Python surfaces calling the C toolkit | the apps and the WMs stay Python; a screen on the toolkit draws through its C primitives |
| the surface painter and pattern lane, 9-slice and mask blits | **C**, `native/moy_ui` over `moy_gfx` and upstream libmoy | draw kernels are C; "a hot loop lives on one side" (`docs/native_kernel_2026-09.md` §4.1). The painter is an op of the toolkit's sink |
| frame styles | **C** beside the primitives, reached from the WMs' decoration path | a style is our code, a theme only names it (§3.5) |
| fonts | **C**, a row behind `moy_text`'s font argument | the one text call exists; a font is data behind it |
| theme assets (fonts, sheets, images) | loader-owned buffers in PSRAM (`MALLOC_CAP_SPIRAM`), each a BUF row of the kernel's handle table with a role, as every off-heap buffer is (`docs/kernel_survival_2026-10.md`) | no second handle mechanism |

**Memory rule.** On the S3 boards, PSRAM outside the Python heap is the
cart-runtime reserve, and the biggest carts need most of it
(`.claude/rules/boards.md`). So: **one loaded theme at a time** (the loader
holds the seeds as flash constants and one installed package beside them),
a hard byte cap (a theme.json is capped at 16384 bytes; owner, 2026-10-18:
a theme may be big, the cap only has to exist), no per-tint bakes (§3.6),
and the internal-SRAM gate of `docs/native_kernel_2026-09.md` §4.6. The
loader allocates nothing at boot: the installed slot is allocated on the
first install. A new theme is data and moves only its own goldens.

## 8. Shell layout

D5 (a theme sets the bar layout, the user overrides it, the override survives
a theme switch) and D7 (one tabbed home on every board, which the desk's
PLAY opens) are the owner's (2026-09-27); the owner's direction on scope
(2026-10-13) is **simple, and two modes**: a console mode for small screens
and a desktop mode for large ones; a layout is a short list of bars holding
built-in widgets; a widget opens its component as a popover; fancy desktop
things are wallpaper carts' job (§12.2). The rulings are §1.2's table (L0-L18).
The WMs stay Python (`docs/native_kernel_2026-09.md` §10 question 8, and §7
here).

### 8.1 The two modes, and the switch as explicit state

| | CONSOLE mode | DESKTOP mode |
|---|---|---|
| feels like | a console: one thing on screen, the animated home, a tap plays | a normal OS: a desk with icons, windows, bars, popovers |
| the WM | `FullscreenStackWM`'s presentation | `WindowedWM`'s |
| the root of the back-stack | the home (§8.10) | the desk |
| an app or the Editor opens | fullscreen, pushed; back pops -- but the Editor on a board with a desk goes back to the desk as a window, whose X comes back here (L18) | as a window on the desk |
| a bar widget's tap | a popover hung from the cell, the system menu as the popup it is (§8.6) | a popover anchored to the cell (§8.6) |
| boards | the T-Deck and the Guition S3 — this mode only; the P4 boards, the host and the browser when switched into it | the P4 boards, the host with `--windowed`, the browser's desktop arrangement — their default |

**The mode is state, not a stack shape.** "Fullscreen" read as "the desk is
not in the stack" cannot hold the desk and its windows beneath a fullscreen
home: `moy_back_goto` truncates, `go_home` tears the desk down,
`on_relayout` drops every window. So console mode is **`wm.mode`** (`"console"` or
`"desktop"`, on `WindowedWM`; `FullscreenStackWM` is console only), and the
tier-sensitive branches ask the mode: `_slots` (nothing is a
window in console mode), `keeps_animating` (a parked desk's live wallpaper
is not drawn and does not animate), `desk_open`/`windowed_chrome` (false in
console mode, so the bar band applies as on the T-Deck), the world flip in
`_on_nav`, focus and `_sync_windows`, `on_relayout` (a mode switch relayouts
the bars and the root context and never calls `_drop_wins`), `go_home`'s
teardown (in console mode it pops to the home and touches no parked
window), and every exit door, each tested by name.

**Parked windows live outside the stack.** Entering console mode moves the
desk's window records — kind, rect, focus, the retained `win.buf` stamp —
from `_order` to `wm._parked`, truncates the kernel back-stack to its root
(`MOY_ROOT`, the home) and sets the mode; the processes behind the records
(the Editor, Settings, an app) are not stopped, since a kind has one process
and `goto(kind)` finds it. Leaving console mode restores `_order` from
`_parked` and re-pushes the kinds beneath nothing new (the stack is
`launcher, desk, kinds…` again) — a restore op on the route, not a reopen,
so no process re-initialises. **Re-opening a parked kind in console mode**
(Settings from the bar while a Settings window is parked) uses the one
process fullscreen and leaves its parked record alone; on return the window
is where it was, showing the state the process now has. Nothing is refused
and no kind exists twice.

**The way in: PLAY.** The desk's PLAY icon is `wm.enter_console()`. A
playtest running in a window is ended first, as closing its window ends it
(owner, 2026-10-13: the simplest to build): the cart slot is one and console
mode's games need it. Steam's Big Picture on a PC is the shape, from memory.

**Games on the desk** (owner, 2026-10-31). Play is full screen, as Steam's
Big Picture is: a game run from the desk opens as a window full screen with
no row and no buttons, and the console's key opens its pause card. On every
board with a desk (`wm.has_desk`), in both modes, the card over a game full
screen offers **Play in a window**, which moves the running game, its run
untouched, into a normal desk window with its strip (minimise, maximise,
close); from console mode it goes back to the desk first, its windows as
they were, the game's on top. A handheld never offers it, and the card over
a game already in a window has no such row: that window's maximise alone
gives it the glass again. The desk has no game icons; the other way into a
window is the Projects window's **Play**, a Make card's third button, which
runs the cart in a window from the start. The Editor's playtest stands
docked in its Run slot and is the Editor's: closing the Editor ends it, as
the pause card's Quit does. The card offers Play in a window over it too,
and a game moved out so is its own window, which outlives the Editor; while
it lasts the Editor's workspaces have no Game tab, the panes left taking its
room, and when it closes the Game tab comes back
(`WindowedWM.play_in_window`, the path an orphaned docked game takes back to
its window; `editor_panes.without`).

**The way back** goes one level, wherever something is beneath the surface
(owner, 2026-10-13; its shape 2026-10-18, `docs/history/look_2026-10.md` step 27a):
the back chevron at the head of the bar's `zone`, or the `close` widget's X
where a layout places one (§8.2). In console mode on a P4 the home's depth is
above the mode's floor — the desk and its parked windows are beneath — so
the home's bar shows the way back and `wm.leave_console()` is its verb; on
the T-Deck the home is the floor and it is absent. Esc and B do the same
from a keyboard. No DESKTOP row joins the system menu: the bar's back is the
one way out of anything (L0).

**Memory.** Parked stamps are caches: when the Player's fit check for a
cart's canvas against the largest free block fails (fragmentation is the
real failure, #66), the WM releases parked stamps oldest-first until the
check passes, and a released window re-renders once on return, the path a
resize takes. There is no allocation-retry hook. The home's `_lib_cache`
lives across switches (the home is console mode's root and hot on return)
and is released by the same fit-check path after the stamps, and after it
the layers a modal keeps what lies under it in, kept across modals: a
dialog's held page and the frame under the pause card (`ui_host.Hold`). The Waveshare
P4's off-heap census is `tools/mem_census.py p4 offheap`, its numbers in #58.

**No boot setting** (owner, 2026-10-13: not for now). A P4 boots to the
desk; PLAY is the only way into console mode and the bar's back the way out (§12.2).

**Console mode is the same everywhere, but for the Editor.** On a P4 in
console mode the Apps tab opens an app fullscreen, as on the T-Deck; the
Editor is the one exception (L18): a Make pick, CHANGE or NEW gives console
mode back to the desk and opens the Editor there in a window, and its X
brings console mode back, on the tab and card it came from -- unless the kid
opened a window on the desk meanwhile, which keeps them there. No other door
flips the mode; the desk's own doors (its icons, PROJECTS) are desktop
mode's. The desk icon column stays (owner,
2026-10-13): a desk with icons and a launcher is the OS norm.

**Moving and resizing a window** (owner, 2026-10-23). A window's strip
drags it, and so does a full-screen window's row, from its head: the drag
restores it to its last normal size (the size it opened at, where it opened
full screen) under the finger, at the same point across its title, as on a
desktop OS. Every edge and corner resizes, from a zone that straddles the
frame (`WindowChrome._rz_band`): a few of the chrome's unit inside it, and
outside it what takes the zone to `chrome.MIN_TAP_MM` across on the panel;
the top's lies above the frame alone, the strip being the drag's, and no zone
reaches a bar. The pointer shows the resize a zone starts (`cursor_shape`).
A moving edge stops at the desk's edge and at the app's minimum
(`_min_size`); the WM holds the press, so the content stays frozen through
the gesture (`docs/surface_model_v1.md` §14.1) and relays out once on
release.

**Boards with one mode, and the hardware reason.** The T-Deck's one canvas IS
the 320×240 game raster (`runtime/wm/wm.py`), so there is no system canvas to
put a window on. The Guition S3 has a 480×320 system canvas, but at #203's
tap floor its chrome scale is 2: a window's title strip alone is 36 of 320
rows and a 16-logical icon 32 px, so a window smaller than the glass holds
nothing worth having; its board.toml denies the windowed halves for that
reason, and its PSRAM outside the Python heap is the cart-runtime reserve
(`.claude/rules/boards.md`). Both stay console-only (owner, 2026-10-13), and
`chips` has no value on them (§8.8).

### 8.2 The model

**A bar** has an `edge`, a `size` (in the handheld's rows: a bar `size` rows
thick is `size / 18` of its metric class's bar, `layout_base.bar_rows`, so 18
is 18 × the chrome scale on a handheld and 40 on a desk, or the class's
bar a theme's look sets, `hhbar.h` and `bar.h`: `docs/history/look_2026-10.md`
step 14) and three slots.
The 2026-10 look designs the P4 desk at chrome size 1 with per-class metrics
and a 40-pixel bar, a theme's `render.scale: 2` drawing it on a half-size
surface the glass doubles (owner, 2026-10-15; `docs/history/look_2026-10.md` step 9,
landed 2026-10-16).
Widgets in `left` pack from the bar's start, `right` from its end, `center`
around its middle (polybar's `modules-left/center/right`, waybar's the same
names; both open 2026-10-13). On a `left` or `right` bar the slots run top to
bottom under the same three names. Top and bottom bars span the glass; left
and right bars span what is left between them. There is no `order` key. One
layout serves both modes: a widget without a value in the current mode
(`chips` in console mode) is dropped at resolve, so a theme writes its bars
once.

**The catalogue** is fixed and every entry is C through the toolkit, the
painters `moy_chrome`'s and `moy_ui`'s. A widget has a value (what the
strip's key folds), one tap, and a minimum cell per orientation; a widget
that does not fit its bar is dropped and noted (§8.8). A tap does the same
thing in both modes; what differs is only the container a component opens in
(§8.6).

| widget | draws | value | tap | min cell, horizontal / vertical |
|---|---|---|---|---|
| `menu` | the ≡ glyph; under a theme's look the pixel Moy on its tile (`docs/history/look_2026-10.md` step 14), its cell the tile's | — | the system menu (the popup it is today, in both modes) | 16×16 / 16×16 |
| `close` | the X, for a layout that wants it; the shipped themes place none | the stack depth above the mode's floor | the exit model's close: pop the top; on a P4's home, `leave_console()` | 16×16 / 16×16 |
| `zone` | the surface's lent zone: its chips (PLAY, CHANGE, SEARCH on the home; the Editor's title, status), headed by the back chevron where the bar has a way back and no `close` | the surface's zone generation | the chevron: the way back; the rest the surface's `zone_tap` | 64×16, flexes / horizontal only |
| `title` | the surface's title | the title string | — | 24×8, flexes / dropped |
| `clock` | `HH:MM` | the minute | the calendar component | 40×8 / 16×16 (two lines) |
| `wifi` | the wifi glyph | `_wifi_icon_kind()` | the wifi component (§8.6): the Settings panel as a popover hung from the widget, in both modes | 16×16 / 16×16 |
| `battery` | the battery glyph | the level bucket | the power component: Settings' level, VOLUME and BRIGHTNESS rows, drawn by Settings (owner, 2026-10-13) | 16×16 / 16×16 |
| `chips` | the taskbar's window chips (desktop mode) | `_chip_sig` | focus, minimise, restore as today | one chip 24×16, flexes / icon chips 16×16 each |
| `text` | a theme string, 1 to 24 characters | constant | — | fit / fit |
| `spacer` | nothing, takes the slack | — | — | 0 |
| `separator` | a 1px line | — | — | 4×16 / 16×4 |

`menu`, `zone`, `close`, `title`, `clock`, `wifi` and `battery` are today's
bar as widgets — the X became its own widget so that a layout can place it,
instead of riding inside the right zone, and a layout without it has the
zone's back chevron (§8.7); `chips`, `text`, `spacer` and `separator` are new
cells. A zone owner that declares `zone_need` -- the Editor, whose narrow bar
is ten tap-floor squares -- takes the battery's room and then the Wi-Fi's on
its own surface's bar wherever its zone is narrower than that, and the clock
keeps its cell (`BarLayer._make_room`; owner, 2026-10-20: every Editor item on
the Guition S3). Every widget is compact (L13): the tallest cell any of them needs is
16 logical pixels, the clock's two lines on a side bar and an icon across a
top bar, which is why §8.3 caps a bar at 32. There is no `home` widget: the HOME key is the way home (L11, L12). There is no `settings` widget: Settings is a row of the system menu
(L0). The home's verbs ride in `zone` wherever a theme puts it, so D7's
mockup — a top strip and a bottom verb strip — is two bars in data (§8.4,
example 4).

### 8.3 The format

Additive to format 1 (§2: a key the loader did not
read is additive, no `format` bump). `layout` holds `bars` and resolves
whole through `inherits` from the nearest package that sets it (§4);
the default seed sets today's bar (example 1; owner, 2026-10-13: one bar is
the default look), the taskbar's chips floating over its middle in desktop
mode as they always have. A bar list is never merged by position. `home`
names the home's arrangement (§8.10, L14): `carousel`, the default, `grid` or
`icons`. There is no `windows`
key (§12.2) and no `mode` key (§8.1: PLAY is the switch).

```json
"layout": {
  "bars": [
    { "edge": "top", "size": 18,
      "left": ["zone"], "center": ["chips"], "right": ["clock", "close", "menu", "wifi", "battery"] }
  ]
}
```

| key | takes | cap or clamp |
|---|---|---|
| `bars` | a list of bar objects | at most 4, at most one per edge; a second on the same edge is dropped and noted |
| `bars[].edge` | `top`, `bottom`, `left`, `right` | required; an unknown edge drops the bar |
| `bars[].size` | an integer, the handheld's rows (§8.2) | clamped to 16..32 (L13: the compact widgets' 16-pixel cells, an icon's margins, a taskbar of chips at 22 or so; the tap floor is the chrome scale's, #203, not the size's); absent is 18 |
| `bars[].left`, `center`, `right` | lists of catalogue names | at most 6 per slot, 12 per layout; an unknown name is dropped and noted; a widget placed twice keeps its first placement, except `text`, `spacer`, `separator` |
| `bars[].text` | a string per `text` widget, by position | 1 to 24 characters of printable ASCII; longer is cut, empty is dropped |
| `home` | `carousel`, `grid` or `icons` | absent or unknown is `carousel` (unknown noted); it rides the layout, which a package sets whole with its bars |

The flattened package carries it in a few dozen bytes, well inside
`theme.bytes` (16384). `style.json`'s `theme.layout` holds the edges, the
slots, the widget names, the required and repeatable sets and the caps (data,
as `font_roles` is); `tests/test_theme_loader.py`'s fuzz corpus carries the
cases (a string for `bars`, five bars, two on one edge, a size of 0 and of
10000, an unknown widget, a 200-character text, a layout with no `menu`, one
with `zone` in a vertical bar only), and the boards' module answers it as the
host's does (`tests/on_glass.py`'s `layouts_match_host`).

**As the loader reads it** (step 4, 2026-10-13). A drop or a clamp sets the
package's existing note bits (§6): an unknown widget is
`unknown`, a size clamped or a text cut is `clamped`, every other drop
`dropped`. "First placement" is placement order: bars in the list's order,
slots left, center, right. A bar's `text` is a list of strings, the i-th going
to the bar's i-th `text` widget; a `text` widget with no string, or an empty
or non-printable one, is dropped, and a string with no widget is dropped. A
layout that then lacks a widget of the required set is refused whole:
`moy_ui.theme_layout(name)` answers the default seed's bars in its place with
the refusal flagged, as it does when the chain will not resolve. A package
that sets no `bars` (none, `{}`, or `bars` that is not a list) inherits.

**Prior art, read for the shape and nothing else.** Polybar and waybar place
named modules in `modules-left/center/right` of a bar whose `bottom`/`position`
picks the edge (both read 2026-10-13); that is the slot model here, minus the
module definitions (ours are a fixed catalogue). eww and Rainmeter compose
widgets from data with x/y geometry and scripts (read 2026-10-13); both are
declined for the reason §3.2 gives — pixel positions
split a catalogue by screen size, and scripts are code in a theme. GNOME's
date menu is the popover of §8.6, from memory. Win95's bottom taskbar and the
macOS menu bar are examples 2 and 3, from memory; today's right zone already
follows the macOS model (`runtime/shell/chrome.py`'s comment). Steam's Big Picture
is §8.1's switch, from memory.

### 8.4 Worked examples, as data

1. **Today's top strip** (the default seed): the block in §8.3.
2. **A Win95-style bottom taskbar** (desktop mode): the ≡ as the Start
   button at the left, the chips beside it, status and clock hard right.
   ```json
   "bars": [ { "edge": "bottom", "size": 22,
               "left": ["menu", "separator", "chips", "zone"], "center": [],
               "right": ["close", "wifi", "battery", "clock"] } ]
   ```
3. **A GNOME-style top bar** (desktop mode): the surface's verbs left, the
   clock centred (tap → the calendar popover), status and the ≡ right.
   ```json
   "bars": [ { "edge": "top", "size": 18,
               "left": ["zone"], "center": ["clock"],
               "right": ["wifi", "battery", "close", "menu"] } ]
   ```
4. **D7's mockup** (either mode): the OS bar on top, the home's verbs in a
   bottom strip. `zone` is the verb strip; on the Editor it holds the title
   and status there instead.
   ```json
   "bars": [ { "edge": "top", "size": 18, "left": ["title"], "center": [],
               "right": ["clock", "close", "menu", "wifi", "battery"] },
             { "edge": "bottom", "size": 18, "left": ["zone"], "center": [], "right": [] } ]
   ```
5. **A thin side bar** (any board, either mode): the ≡ at the top of a left
   bar, the clock in two lines and the battery at its foot, beside a top bar
   that keeps the zone, the X and the wifi. A side bar is as thin as a top
   bar (L13); the clock's tap opens the calendar, never docks it.
   ```json
   "bars": [ { "edge": "top", "size": 18, "left": ["zone"], "center": [],
               "right": ["close", "wifi"] },
             { "edge": "left", "size": 24, "left": ["menu", "separator"], "center": [],
               "right": ["clock", "battery"] } ]
   ```
6. **A T-Deck layout** (console mode): a bottom strip, the surface's verbs
   beside the ≡; the same data on a P4 in console mode draws the same bar.
   ```json
   "bars": [ { "edge": "bottom", "size": 18,
               "left": ["menu", "zone"], "center": [], "right": ["clock", "close", "battery"] } ]
   ```

### 8.5 The content rectangle

**The contract.** `content_rect()` returns `(x, y, w, h)` in the app's surface
coordinates: the part of the surface the host neither paints over nor swallows
taps in — the glass minus every bar, top and bottom first, then left and
right. Under today's bar it is `(0, bar, w, h - bar)`; in a window `(0, 0,
w, h)` (the title strip is the WM's, outside the content buffer, which is why
`windowed` apps use 0). Each bar's rect is the host's,
`BarLayer.bar_rect(i, where)`, the one author of where a bar is for drawing
AND hit-testing; the bar's own rects are in bar coordinates, and
`BarLayer.handle_bar_tap` translates a tap into them.

**`bar_h()` is derived**: `content_rect()[1]`, the rows ABOVE the content.
Under a top bar it is what it is today; in a window 0, as today; under a
bottom-only layout 0. An app that reads only `bar_h()` therefore draws from
row 0 and has its bottom rows covered by a bottom bar. Rejected: `bar_h()`
reporting the strip's height regardless of edge (a `bar_h`-only app would
leave the top rows empty AND lose the bottom ones), and a clipped sub-canvas
handed to the app (a new surface mechanism, which `docs/surface_model_v1.md`
§8 closes).

**Where the value comes from** (2026-10-13 and 2026-10-14). One leaf places the bars and derives what they leave:
`runtime/toolkit/layout_base.py`'s `place_bars`, `bar_rects_of` and `content_of(w, h,
rows)` (each bar `size * rows // 18` thick, `rows` the 18-row bar at the
surface's scale), imported by the bars, every layout and the app heads alike;
a window's layouts take its band instead (`in_window`: the default bar at the
window's top, whose zone alone draws, whatever the theme's layout), and
the rows its rounded frame takes under the content (`window_foot`), which a
bottom inset counts. `BarLayer.bar_rects(where)` and `content_rect(where)` answer it
per surface; `LayoutBase` carries `cx, cy, cw, ch`;
The calc/appearance/artwork heads take
`content_of` (rows 0 in a window) instead of computing `18 * cs`; and
`console.py`'s band check reads the bar rects, never the app's layout.
`moy_app_surface_t` holds `cx, cy, cw, ch` where it held `bar_h`, written by
the same two writers (`console._surface_write`, `_LayoutCtx.install`) with
`Workstation.app_content_rect()`, the rect the exitable strip leaves the
RUNNING app's screen; the C answers `bar_h` as its `cy`. `roles.json` carries
`surface.content_rect` (`server: c`, `grant: never`, `wasm: null`, as `bar_h`
is) and the role table's drift tests (`tests/test_roles.py`) move with it.
`runtime/shell/system_api.py` publishes `content_rect()` ungated beside `bar_h()` —
an app-API global, not a cart verb (the verb table and moy-spec are
untouched) — and a Lua app gets its four values as four results, the host's
binding fanning a tuple of ints out as moycore does. The shipped apps place by
`content_of` rather than reading the row because a row is one value for every
grant: it answers the running user app, whose strip can be the game canvas's
18 rows while a shipped app on the system canvas sits under `18 * cs`, and a
desk window has none. The resolver (`Appearance.apply_layout`, §8.9) places
every resolved bar through `Workstation.place_bars`, which relayouts;
`tests/test_content_rect.py` moves today's bar to the bottom through it and
checks every layout, the bar's band, the taps and the desk, and
`tests/test_bars.py` does the same under four bars at once, side bars
included: every layout's rects inside what they leave, each bar's strip at
its rect, taps routed by the bar they land on.

**Bars are global** (owner, 2026-10-13: it saves the most space, and L0):
every surface, the running tool/app bar included, lays out off the content
rect: beyond the responsive `Layout`, the game-canvas tool bar and its
rects, `editor_app.py`'s use of them, the popup's place, `WindowedWM`'s
window sizing, cascade, maximise and resize, and the desk icon column's
geometry and tap — every `bar_h` reader in `runtime/` reads the bars. The
alternative — bars for the OS surfaces only, a running app keeping a top
strip — is declined (§12.2).

| who | what |
|---|---|
| app carts (Python, `type: app`) | nothing breaks: `bar_h()` stays; `content_rect()` is the verb to move to; `docs/app_api_v1.md` says so in place |
| shipped apps | read the content rect through their layouts (step 3 is byte-identical under the top bar) |
| compiled apps (`moy_app` ABI) | untouched: a compiled app keeps the player window, a fixed canvas the WM scales (`docs/kernel_toolkit_2026-10.md` §6, owner 2026-10-11); `content_rect` joins the role `wasm: null` like `bar_h` |
| games | untouched: the Player surface is the cart's; no mode draws a bar over a game; on the desk a play opens full screen and the Editor's playtest docks (§8.1), and a game moved to a window keeps the size it opened at (#178) |
| versioning | `docs/app_api_v1.md` stays v1: one additive verb; the role table's generation moves as any row addition does |

### 8.6 Popovers and components

**One mechanism, desktop mode.** `widgets.Popup` generalised: a panel
anchored to a widget's cell, opening into the content side of its bar (down
from a top bar, up from a bottom one, inward from a side bar), its width the
larger of its content's minimum and today's popup width, clamped to the
glass; one open at a time; it closes on a tap outside, on the anchor tapped
again, on B or stop, and on Esc from a BLE keyboard, where arrows move and
Enter activates — exactly the `sysmenu` layer's rules. That layer is the
one popover layer, with the system menu as its first content; the system
menu is a popup in console mode too, as on the T-Deck.

**A popover rides the quiet stack** (`runtime/wm/wm_overlays.py`, the draw
stack in `runtime/wm/wm_windowed.py`).
A change that stays inside the panel -- it opened, a row moved, it closed --
does not mark the frame dirty: the WM owes the popover's rect to each of the
root's real buffers (`RETAINED_FRAMES`; one on a persistent root, so the
Guition P4 pays one frame), and such a frame draws the popover's layers
alone over the retained one, its rect the frame's damage. Before the
panel's first paint the pixels under it are snapshotted; a close puts them
back into each buffer. Two snapshot slots, because a popover may open while
the last one's restore is still owed. A frame that does repaint (a dirty
mark, an animation, the full-paint debt of a triple-buffered root) restores
the snapshot first, so it starts as if no popover were drawn, and takes the
snapshot again before painting it; its damage carries the popover's rect.
With a playtest the quiet stack is the popover's restore, the player window,
the popover: the game keeps ticking, and while the popover overlaps the
game's window its face -- copied once it is painted -- is put back over each
game frame, one copy rather than a paint. The focused window under a bar
widget's popover shows no focus mark while the popover holds the keys (one
a screen, ui.marks): the open keeps its buffer, draws it once without the
mark and stamps it into each buffer before the popover paints; the close
puts the kept buffer back and stamps it again (`_pop_marks`). A card that
sizes itself again -- the Wi-Fi card to the networks a scan found -- asks
for a frame that repaints whole at its new rect. The snapshots and the face are
released by the fit check (§8.1) once no popover is live. A change outside the panel (a connect, a stepped
setting, a row's action) marks the frame dirty as before. The system menu
takes the same path; it is the look's card
(docs/history/look_2026-10.md step 25a), whose rect -- the card and its shadow's
reach, known before it draws (`SystemMenuUI.panel_rect`) -- a riding paint
first restores from the snapshot, since the card's shadow blends. Its
scrim rides with it over a still desk with no playtest (`_pop_wash`): the
frame it opens on snapshots what the scrim covers, renders the focused
window as it shows once the menu holds the keys (no focus mark, ui.marks),
and washes; each other buffer takes that wash copied out of the buffer the
last frame painted (docs/surface_model_v1.md §14.6), or, where the root
cannot copy, is stamped that window and washed once, and
the close puts the snapshot back, with the window's buffer as the menu
found it. A frame that repaints whole while it is up washes what it
painted and keeps no snapshot, so the close then repaints whole too. The
pause menu over a playtest paused in its window rides the same way
(`pause_changed`): its game's held frame composited into each buffer
before the wash, its card's change -- a row, Options, back -- painted over
what its paints covered, put back from the snapshot and washed again, and
its close put back around the resumed game's first frames; a frame that
repaints whole under it takes the snapshot again before its scrim, so its
rows keep riding. A
pointer press whose only change was the popover's -- a bar widget's tap
-- marks no frame (`popover_touch` takes it). With a playtest under the system menu
the frame the scrim comes on copies the last painted frame, composites the
game's and washes the scrim's rects once (`_wash_all`), and each other
buffer is that frame copied whole with the quiet stack over it
(`_lead_pop_stack`). A triple-buffered root's
buffers behind the last change are brought to it while the desk is calm
(`debt_owed`: nothing animating, the pointer still), each a copy of the
last painted frame's buffer where the root has one, so the next popover
finds them current instead of paying them first. What each costs is in
#58 and #66.

**Four components, fixed per widget, nothing to configure.** A component is
one painter, one minimum size and one navigation handler; where it draws is
the container's business, not the component's.

| anchor | component | navigation inside |
|---|---|---|
| `menu` | the system menu's rows | rows |
| `clock` | the month calendar, today marked; left/right page months | a 7-column grid; the day cursor is display only |
| `wifi` | the Settings WIFI panel's rows, the one implementation (owner, 2026-10-13: the `moybyte.wifi.moy` tool cart goes, `docs/history/shell_layout_2026-10.md` §13 step 6) — its radio lease is released whenever the container closes, a tap outside included | rows; picking one runs the password step in the same container |
| `battery` | Settings' level, VOLUME and BRIGHTNESS rows, drawn by Settings — one implementation, the popover a container for it | rows |

**One container.** A component draws in a popover hung from its widget, in
both modes (docs/history/look_2026-10.md step 33: the calendar's, power's and
Wi-Fi's cards over the console-mode home too, the hint strip of a handheld
home saying their keys). No component is docked in a bar (L13). No
component is a registered app or a desk icon. The components are
`runtime/system/popover.py`'s, each on the public UI API's verbs in the system's
flat card: the wifi panel is Settings' Wi-Fi page
(`SettingsLayer.wifi_page`, its popover form the joined network and the
networks as a menu) over the component's own UI context, the power rows are
the battery's line over Settings' Volume and Brightness sliders, the
calendar is `ui.month`, and the battery's level reads None on every board
today (no board measures one), its line then reading Battery. An app that
asks for the Wi-Fi page (`Workstation.open_wifi_panel`) still gets Settings'
page in console mode.

**The keys reach the bar** (owner call 2026-10-26), in console mode on every
board and the browser. Up past the top of the surface under a bar (on
the home, the tab row's left and right: below) -- a page's first row (`ui_draw.edge_take`, the
engine's `api_edge`), the Editor's list, outline, step, map or sprite
grid at its top (`at_top`) -- puts focus on the bar's items
(`BarLayer.focus_items`): the OS widgets, the back chevron, a running app's
lent tools and the zone's targets (the Editor's tabs, undo, redo and Play,
`zone_targets`), the Editor's landing on its open tab. Left and right walk
them, confirm opens one as a tap on it does (a widget's popover over the
bar, focus staying on the widget under it; a tab, the keys going down into
it), down, back and stop come off
it; the focused item takes the bar's focus pill (below), and
the hint strip under it names the bar's keys (`ui_draw.BAR_KEYS`: change,
confirm and back, in the input's own keys). A caret keeps up for itself, so
the Code tab and the theme mode's Config walk no focus onto the bar: their
bar is a touch's, and the system menu's Home is their way out. The desk's
windows keep their own ways.

**The bar's focus mark is the pill** (owner call 2026-10-27), in the tabs'
shape whatever the theme's focus style (`ui_draw.focus`'s `pill`,
`MOY_UI_MARK_PILL`): a plate of the focus colour under the item and a ring
round it, the theme's `focus.outline` outside the ring, so the item keeps its
own ink -- an invert theme's mark would blot a 16-pixel icon out. On the OS
widgets it is one height, the system tile's and its ring, centred across the
bar (`BarLayer._pill`); on the zone's targets it is their rect and a ring
round it.

**The home's tabs and the bar's items are one row** (owner calls
2026-10-27). Where the bar holds the tabs (the handheld's home: the T-Deck,
the Guition S3, the browser), left and right walk the bar's items and the
tabs in their order on the screen -- on the T-Deck the system button, Games,
Mine, Apps, Make, Wi-Fi, the battery and the clock -- and stop at the row's
ends (`BarLayer.focus_beside_tabs`, `_tabs_split`); up stays, the row being
the top line, and down and back go to the cards from anywhere on it. The
walk moves a focus and opens nothing: confirm opens the tab the focus is on
and takes the keys to its cards, or opens a bar item as a tap on it does; Q
and E open the previous and the next tab at once, and a tap opens a tab at
once (`LauncherHomeLayer.focused_tab`). The focused tab wears the pill and
the open one its current mark, so the two read apart; the hint strip names
the row's keys, the bar's (`ui_draw.BAR_KEYS`). Where the tabs sit below the
bar (`LauncherHomeLayer.tabs_in_bar` empty), the row stops at the tabs'
ends and up goes on to the bar. A trackball roll steps on its stronger axis,
and mid-roll the other axis takes `Workstation.BALL_TURN` pulses, so a roll
along the row that drifts a pulse or two down stays on it; a pause frees it
(`Workstation._ball_roll`).

**The Editor's tabs and the bar's items are one row too** (owner call
2026-10-27). On a handheld the tab ladder sits in the bar: on the T-Deck the
system button, Config, Blocks, Code, Sprites, Map, Scene, Music, undo, redo,
Play, the battery and the clock, walked in that order and stopping at the
ends. The walk moves the focus pill and opens nothing; confirm on a tab opens
it and takes the keys down into it (`EditorApp.zone_opened`, the theme
mode's two tabs the same), confirm on undo or redo runs it and the keys stay
on the row, and a tap opens a tab at once. The focused tab wears the pill and
the open one its current mark. The desk Editor's workspaces are a window's
row, switched by their chords (`EditorDesk._chord`), and the keys do not walk
it.

### 8.7 The safety rule, and what the loader checks

**The rule.** A layout places `menu` and `zone` somewhere
**after every drop of §8.8** — a `zone` in a vertical bar is dropped, and a
layout that then has no `zone` has lost its verbs and its exit
— or it is replaced by the tier default and the replacement is noted in the
theme's report (§6). The rule therefore runs
twice: in the loader on the data, and in the composer on the resolved bars.
The way out of anything (and of console mode on a P4) is the back chevron
at the head of the `zone`, or the `close` widget where a layout places one
(§8.1), with Esc and B as its keyboard twins (`docs/shell_ux_v1.md` §9);
`menu` the system menu; the way to the mode's root is the HOME key (L11),
which no widget doubles. No menu row duplicates any of them (L0).

**Checked once, deterministically.** In the C loader
(`native/moy_ui/moy_theme.c`), as the theme is: the caps and clamps of §8.3, the
rule above on the data, and a widget's presence in the catalogue. In the
composer at resolve (it needs the glass, the chrome scale and the mode, so
it runs where the layout is laid out, on the host for every golden config):
a widget whose minimum cell does not fit its bar, or that has no value in
this mode or on this board, is dropped and noted; the rule again; a bar's
cells are at least 16 logical × chrome scale, so #203's tap floor holds
without a layout saying so; every bar is edge-anchored and clamped, so
nothing is off-screen by construction; and the content rectangle keeps **at
least 75% of each axis of the glass**, or the layout is replaced by the tier
default and noted. The tier default is the default seed's layout resolved
the same way, and where even that does not resolve on a glass, today's one
top bar (`runtime/toolkit/shell_layout.py`'s `FALLBACK`). The composer never raises;
both halves are fuzzed (`tests/test_shell_layout.py`) and the boards' module
answers the host's corpus (`tests/test_theme_loader.py`).

**Recovery.** The loader is the recovery (§6,
owner, 2026-10-14): a layout is part of a theme, a theme package from the
store is data the loader validates, and a `theme` or `layout_theme` row
naming a package that will not load reads as the default seed's, the
loader's note shown once. There is no per-board
reset key and no strike ledger for a theme (theming §12.1).

### 8.8 Boards: modes, what each can place, and the resolution rule

The tier is each board.toml's `[board] tier`; the WM is the only tier-specific
layer (`docs/shell_ux_v1.md` §3), so **the WM class carries** `LAYOUT_CATALOGUE`
(the widgets it has a value for, per mode) and `has_desk` already says
which modes a board has. Settings shows a row for what the WM lists and none
for what it does not (absence, never a disabled row). Beside it,
`LAYOUT_EDGES` names the edges a bar may take on this tier (all four on both
WMs), and the composer is `runtime/toolkit/shell_layout.py`'s `resolve`.

| board | glass, input | modes (default first) | WM | widgets placeable |
|---|---|---|---|---|
| T-Deck | 320×240, keyboard and trackball | console | `FullscreenStackWM` | all but `chips` |
| Guition S3 | 480×320, touch | console | `FullscreenStackWM` | all but `chips` |
| Waveshare P4 | 1024×600, touch | desktop, console | `WindowedWM` | all; `chips` in desktop mode |
| Guition P4 | 1280×800 landscape desk rotated onto 800×1280 portrait glass (`firmware/guition_jc8012p4a1c/README.md`), touch | desktop, console | `WindowedWM` | all; `chips` in desktop mode; side bars since their on-glass check (§8.11) |
| browser | the arrangement picked at boot | handheld arrangement: console; desktop arrangement: desktop, console | as the arrangement | as the arrangement |
| host | `tools/simulate_desktop.py`; `--windowed` | without the flag: console; with it: desktop, console | as the flag | as the flag |
| Zero | no glass | — | none | — |

**Every remaining difference between boards, and its hardware reason** (L0:
consistent where the hardware allows):

| difference | reason |
|---|---|
| the T-Deck and the Guition S3 have no desktop mode | §8.1's last paragraph: one canvas that is the game raster; a 3.5" glass at the tap floor; the S3's PSRAM reserve |
| `battery` draws nothing on a board without a gauge | the board reports `None` (CLAUDE.md's rule) and the widget is dropped at resolve, noted once |
| the carousel's motion may degrade on a board | measured frame cost (§8.10), recorded in #66, never theme data |
| the Guition P4 rotates a bar strip when its pixels moved, side bars included since their on-glass check | its scan-out is portrait and the desk landscape (`native/moy_glass/moy_present_rot.c`); narrow rotated blocks have wedged the PPA (§8.11) |

**The resolution rule, per element.** A widget outside the WM's catalogue
for the current mode, without a value on this board, or not fitting its bar,
is dropped from its slot and noted; the bar stays; then §8.7's rule runs on
what is left. A value the key does not take resolves up the inheritance
chain and ends at the seed. Only §8.7's rule and a structural failure replace
the whole layout. The same rule applies to the user's pick (§8.9): a
`system.json` copied from a `--windowed` host onto a T-Deck resolves its
layout the T-Deck's way. Every rule is exercised on every golden config
with one fixture layout that triggers each drop.

### 8.9 The user override (D5)

**One row.** `layout_theme` in `ws.system` holds the name of the package —
a seed or an installed theme — whose resolved
`layout` is used in place of the active theme's. Absent means "the active
theme's". Settings' row steps through the installed packages' layouts, not
the seeds': a seed's layout is its look's (Moy's bar, `docs/history/look_2026-10.md`
step 14). The reader is strict: a name that is not installed reads as absent
(`None`); there is no legacy shape, so the store rule costs nothing
(CLAUDE.md, 2026-09-07). `set_theme` writes `theme` and `theme_variant` and
nothing else, which is why the pick survives a switch: it is stored beside
the theme, not in it. A layout the user edits is the theme's own: it is
edited through Appearance's Edit theme…, the Editor's theme mode
(`docs/history/look_2026-10.md` step 33).

**Precedence, in one function.** `Appearance.layout()` (`runtime/shell/appearance.py`,
`ws.look`) resolves the picked package's `bars` if the row is set and
resolves, else the active theme's, else the default; then §8.8's per-element
rule and §8.7's rule for the current mode. It runs at boot's `load_system`
cascade, at the end of `set_theme`, at the Settings write, and at a mode
switch (the catalogue changes), and on a change marks `ws._dirty` and runs
`_relayout()` (§8.11). It is never read per frame.

**Settings.** One row after FONT SIZE, `LAYOUT: THEME / <package names>`,
stepping through the seeds and installed themes whose layout differs from
the active theme's; THEME deletes the row. The row is absent while no
package's layout differs and none is picked, so a console with only the
seeds' one layout shows no row.

Rejected: scalar rows per lever (a package name covers the list in one
strict read), and storing the pick inside a user theme that `inherits` the
active one (a switch would rewrite its `inherits`).

### 8.10 The home (D7), console mode's root

**One component, one place.** The home is console mode's root; desktop mode
has no home of its own — its root is the desk, and PLAY enters console mode
(§8.1). So there is one home component on every board, drawn by one layer, in
an **arrangement** the layout's `home` key picks (L14, owner 2026-10-14): the
`carousel` (the default, below), the `grid` -- a shelf of cover cards, each
titled under it, in the rows the glass holds, scrolling sideways -- or
`icons`, the small icon-only grid (Nintendo-style; `docs/history/theming_2026-09.md` §9
phase 6 step 11): the covers' reduction in tiles that scroll down, the
focused cart named in a caption band at the foot on a handheld and every
tile named under it on the desk. All three take the same tabs in the bar,
sources, taps, keys and hint strip, "+ New" a card like the rest
(`docs/history/look_2026-10.md` step 33 draws the grid and the icons). The carousel:
`LauncherHomeLayer` is it, the `Launcher` shelf engine under a **tab
row** (`ui.tab_row`, a toolkit kind, drawn through C) with four sources. One
class, four lists; there is no pinned Make tile, its job is the Make tab's. The
mockups (`docs/media/theming_2026-09/README.md`: the look, not finished
layouts; L0: not set in stone) are at
`docs/media/theming_2026-09/home_dark_480x320.png` and the 320×240 pair. The
2026-10 look restyles this component (`docs/history/look_2026-10.md` steps 17 and 18):
on the handheld's metrics a 2× hero between 1× neighbours, its title and
metadata under it, the tabs in the bar's `zone` and the verbs a keycap hint
strip along the foot (look step 17); on the desk's metrics the selected card as
the hero beside a details pane (its metadata, its play record, its
buttons) over a shelf that bleeds off the right edge, the tabs in the bar
and the hint strip along the foot as on the handheld, still the `carousel`
arrangement (look step 18).

| tab | source | tap (console mode, every board) | verbs (the hint strip; on the desk its buttons, and the strip naming them, add COPY beside CHANGE; Make's buttons on a real card are its own, CHANGE first, NEW kept on the + tile and the strip's N) |
|---|---|---|---|
| Games | the installed carts that are games — the seeds and the store's — and not the kid's own | run, fullscreen | PLAY, CHANGE (an editable one), SEARCH (the launcher's search row, `tests/test_launcher_search_favorites.py`) |
| Mine | the kid's own carts | run | PLAY, CHANGE |
| Apps | the registered apps (`ws._apps`, minus `HIDDEN_APPS`) | the app fullscreen, pushed | OPEN |
| Make | "+ New" first, then every editable cart (owner, 2026-10-13): the one projects list (L15) | the Editor: fullscreen, pushed, on a board with no desk; a window on the desk on one with a desk (L18) | NEW, CHANGE, COPY, DEL (DEL asks first); in the desk's Projects window PLAY too, the third button, which runs the cart in a desk window (§8.1) |

Games and Mine do not overlap: a cart the kid made or took to make their own
is Mine's, not Games'. **The selected card's verbs beyond its tap are one
list** (`LauncherHomeLayer.card_verbs`): its favourite star (#105), CHANGE
with COPY beside it, and Make's DEL. Every input reaches the whole list
(`tests/test_home_card_verbs.py`): the system menu over the home opens on it,
its first group, in an open cart's place; a long press on a card (500 ms
without travel, the phone home screens' idiom) and a gamepad's X open it
alone, as a menu hung at the card; a keyboard types F for the star and the
strip's letters for the rest. A row's Delete asks first, as the cart
group's, the strip's DEL and the X key do: one dialog, "Delete <title>?",
"This can't be undone.", Cancel and Delete (owner, 2026-10-20; a cart's
delete is permanent, no Trash holds it). Mine and Make hold the same carts under the two
personas of `docs/shell_ux_v1.md` §4 — a tap on Mine plays, a tap on Make
edits (owner, 2026-10-13) — which narrows that section's "a launcher tap
always RUNS" to the three tabs that launch (`docs/history/shell_layout_2026-10.md` §16). The windowed tier
has no separate games-only Library: the Games tab excludes apps and the
Apps tab holds them, on every board.

**One projects list** (owner, 2026-10-14; L15-L17). The project picker is
deleted on every tier: the Make tab is the list, and COPY and DEL, which
were the picker's, are Make's verbs. The Editor's bar carries no way to
another project; its X is the back-stack's pop, so it lands on the home on
the tab and card the Editor was opened from (CHANGE works from any tab). On
the narrow bar the open tab's name goes on its chip only where the whole
ladder still fits the zone with its icons the full gap apart
(`EditorApp._ladder_layout`): the name goes before the icons squeeze, so
the T-Deck's bar, beside the Moy tile and the back chevron, is icon-only.

**The home in a window.** The desk's PROJECTS opens the same component in a
desktop-mode window (`HomeWindowLayer`, kind `projects`): on its Make tab
on every open, over its own card model (`ws.projects_grid`), so the
fullscreen home's tab and card are untouched. It draws the console home's
design whatever the layout's `home` (owner, 2026-10-20): the hero, its
details pane and buttons, the shelf, the hint strip along the foot and the
tabs in the window's band. It is a normal window -- it moves, resizes,
minimises to a chip; its X and B close it alone and never switch modes. It
lays out in the window's content rect through the window's layout context
(§8.5's machinery): a narrow window shrinks the hero, or stands it over
the details where the height holds both at a hero no smaller, and a short
one drops the details' facts, then their metadata
(`Carousel._geom_desk`). Its backdrop is the panel field rather than the
wallpaper, so an idle window draws nothing, and its drag rides
the band frame (`_band_frame`) and the WM's content-gesture path. Every pick opens what it opens as a window above it: a Make project
the Editor, a Games cart the player, an Apps entry its app; the Editor's
window X closes back to it. SEARCH is the fullscreen home's verb alone.
While search is open the handheld's hint strip reads "Search: " and the
query, a bar caret after it while it is typed, then the key that closes it
(a field's cancel while typing, where the T-Deck's K types a letter, else
back); touch has no strip, so the line lies on the home's ground over the
glass's foot and a tap on it closes search (design review R5-04).

**Navigation.** Touch: tap a tab, drag or fling the carousel, tap the centre
card (the desk's hero) or a verb (a hint, a desk button). T-Deck: left/right
move the carousel by a card with the drag's glide (the handheld's 6-frame
slide), run through the same `flinging` path as a touch fling
(`_band_frame`), so a held key is one continuous motion; up moves focus to
the tabs in the bar, where left/right move the focus along them and the
bar's items beside them, enter opens the focused one and down returns (§8.6); the trackball is the same
four directions and its click is enter. On the desk a key or a
tap on a shelf card selects it at once and the shelf glides to show it two
cards in; a drag scrolls the shelf and selects nothing; up from the shelf
puts the focus on the details pane's first button, where left/right move
along the buttons, confirm presses one, up goes on to the tabs and down or
B comes back to the shelf. The hint strip names
its keys: on a keyboard C, /, N, Y and X its verbs, and the tabs' action;
on a gamepad, which has no letters, the verb its confirm does (Play,
Open, New, or Change on Make), X for the card's list and the tabs'
shoulders. Under touch there is no strip, and the home lays out in its
height too; a long press opens the card's list.

**Damage first.** The fullscreen home's band frame (`_band_frame`) hands
the rects it changed -- its kept table's floor, and a step's hint strip -- to
the compositor's `note_damage` (`FullscreenStackWM.hand_content_damage`,
nothing handed while an overlay, the cursor or an owed LOADING pill also
drew), so a moving home frame on the Guition P4 rotates the band, not the
whole buffer (`docs/history/shell_layout_2026-10.md` §13 step 2; #66's shelf-drag rows). It is the carousel's
floor: the carousel's band hands the same way.

**The carousel, animated** (owner, 2026-10-13: the centre card is larger and
grows and shrinks as cards pass through the centre, in motion as well as at
rest), on the handheld; the desk's shelf cards keep one size, and its hero
and details lie outside the band, repainted when the selection moves
(`docs/surface_model_v1.md` §14.4: each buffer gets only the parts it lacks). The rule that keeps it inside the cost model: **a card's scale is a
function of its distance from the centre and of nothing else** — `1.0` at one
card pitch or more, rising to the centre scale at distance 0 — so there is no
animation clock, a settled row is a still row, and "settled" is the kinetic
scroll's own condition (#113). The frame in motion:

- **`sel` leaves the band's key while the row moves** — `Launcher.sel` bumps
  the zone generation, so every card crossing would re-render the bar; `sel`
  and the zone update once, on settle;
- the cards lie on the home's **wallpaper**: the band has no backdrop of its
  own (owner, 2026-10-14: "why is the background white? instead of showing
  the wallpaper"; no scrim either, §3.9), so its pixels cannot
  shift as #113's scroll-as-blit does — the wallpaper would slide with the
  cards. A moving frame restores the wallpaper only around the cards: from
  the wallpaper's own still image where it has one (`Wallpaper.restore_key`:
  the picture, the theme's baked backdrop, the plain fill), else from the
  copy every full paint takes of it under the band
  (`LauncherHomeLayer._band_bg`, an owned layer the fit check releases with
  the retained frame), every card painting its whole square opaque
  but the rows its rounded corners span, which are restored too: each
  card one scaled blit of its whole RGB565 cover bake at its scale
  (`DeviceCanvas.cover`), the focus mark on the
  selected one (on the handheld its title and metadata across the band, on
  the desk the shelf's caption). Covers are RGB565
  bakes and `sspr` scales index sheets, so this is **a nearest-neighbour
  RGB565 scaled-blit kernel** in `moy_gfx`, benched on all four boards
  before the home committed (`docs/history/shell_layout_2026-10.md` §13 step 7a). The band's rect is the frame's
  damage.

What it costs per moving frame is the wallpaper restored around the cards
and their scaled blits; what it costs per settled frame is nothing. Step 7
measures it on every board (`docs/history/shell_layout_2026-10.md` §13) against #66's per-cart fps as the control.
**If a board's drag frames do not fit its budget it degrades**, by this order
and only as far as the measurement says: (1) the centre scale is reduced;
(2) every card is at scale 1.0 while moving and the centre card scales up
only once settled; (3) one card size with the frame and the plate. The
verdict is a per-board constant on the WM (`HOME_MOTION`, `HOME_SCALE`),
recorded in #66, never a theme key.

**The wallpaper slot.** The home draws the `home` slot's wallpaper
(§3.9); the desk draws `desk`; a console-only
board never reads `desk`; a parked desk's live wallpaper does not animate
(§8.1). Readability is §3.9's: the cards lie on the wallpaper, with
no scrim.

**Counts and empties.** The bar's `title` on the home is the active tab's
count. An empty Mine or Make shows one "+ New" card.

**The "+ New" card** (design review 2026-10-19) is a blank page, never a
cover: the theme's `surface` in a cover's corners with the icon set's plus in
`ink_accent` (`home_grid.draw_new_art`, `iconsets.plus`), the same at
rest and selected. A colour block with a glyph is what a coverless cart's
generated cover is (`ui_cover.initial_cover`), so New stays off that
vocabulary; a theme that casts no shadow frames the page in its `border`,
the page inside the frame keeping a cover's corners and the frame running
round them (design review R5-10, F6-04).

### 8.11 Kernel mapping, caches and cost

| piece | side | why |
|---|---|---|
| both WMs and the mode state, the bar layer (a list of bars), the popover layer, the home layer, Settings rows, the resolver, slot packing (arithmetic once per relayout, cached on the layout object) | **Python** | `docs/native_kernel_2026-09.md` §10 question 8: sprint 7 does not run; the WMs are policy, and this doc adds policy, not kernels |
| the widget painters, the band, the tab row, the chips, the pill, the frames, the calendar grid | **C, existing and extended in place** | `moy_chrome_strip_band`, `chrome_strip_right` and the `ui` kinds are C already; a widget painter is one more op of the toolkit's sink, the calendar a grid of `ui.cell`s |
| the RGB565 scaled blit | **C, a kernel** in `moy_gfx`: `blit565_resize` | one call per card, any ratio; benched on every board (#66) |
| the compositor's strip rect list | **C, `native/moy_glass`** | a list set on relayout, each strip rotated only when it moved, is the Guition P4's lever (`docs/history/shell_layout_2026-10.md` §13 step 2); step 5 feeds it one strip a bar |
| the route's restore op | **C, `native/moy_spine`** | the back-stack is the kernel's; parked kinds come back without re-opening |
| the surface row's content rect, the loader's layout bytes | **C, additive** | the row is `native/moy_app`'s and the package reader is `native/moy_ui`'s; no new module, no new handle kind |

**Caches, and what each folds.** A layout change, and a mode switch, is a
Class A dirty plus a relayout — the path `set_font_scale` takes today
(`runtime/shell/appearance.py`: relayout the eight layout heads, every registered
app, the WM's re-anchor hook) — with the mode switch relayouting bars and the
root context only. Nothing here invents an invalidation mechanism.

| cache | keyed on | on a change |
|---|---|---|
| a bar's strip (`bar_layer._bar_strips`, per bar, `where` and canvas) | the look, the bar's rect, and the value of every widget it holds (the minute, the wifi kind, the zone generation; the depth for `close` moves with a navigation's invalidate) — a bar without the clock keeps its strip on the minute | the strip is re-rendered WHOLE and blitted, as today; a per-cell repaint was reviewed and declined — the whole strip is one C paint once a minute for the clock, inside the frame-allocation rule `tests/test_frame_alloc.py` holds; idle frames paint nothing |
| the home statics and retained stamp (`_statics_key`: size, fs, theme) | plus the content rect and the active tab | one re-stamp; a tab switch is a key change; in motion §8.10's bands paint, at rest the stamp holds |
| the home's `_lib_cache` | the cart list and cover generation | lives across mode switches; released by the fit-check path after the parked stamps (§8.1) |
| the taskbar chips (`wm_chrome._chip_sig`) | the chip signature, in the `chips` cell | painted by the WM over the strip, frozen on quiet frames like the window chrome; a change re-paints them and tells the compositor the bar's rect moved |
| the desk backdrop drag cache (`wm_desk._BackdropLayer`) | captured per gesture from the live desk | nothing to key: the next gesture captures the relayouted desk |
| a parked window's content stamp (`win.buf`) | the window's content, as today | kept across console mode; released oldest-first when the Player's fit check fails, re-rendered once on return |
| `wm_chrome`'s frozen chrome and `_content_static` (`docs/surface_model_v1.md` §14.1) | theme name, variant, window rects | the re-anchor moves the rects, the freeze re-arms on the next quiet frame |
| the popover | its component's own rows | open, navigate and close are Class A dirties with the popover's rect as damage; closed, it draws nothing and holds nothing |
| the Guition P4's strip rects | one generation per bar | a bar rotates only when its generation moved; a game frame with still bars rotates the game rect alone |

Every one of these keys folds `look_gen`
(§5); the keys added here are geometry and values,
which is what those keys already hold.

**What is measured, and where it goes.** A painted frame pays one flat
strip blit per bar (up to four); a left or right bar is a column blit, a
per-row copy rather than a band (the `--repaint` deltas are in #66). On the
Guition P4 a bar strip is a rect the compositor rotates when its generation
moved; a narrow rotated block has wedged the PPA before, so a side bar was
placed on that board only after an on-glass check (2026-10-14, #58), and
`MOY_ROT_RECTS` (8) is budgeted as game + damage + four bars with
coalescing, tested. The mode switch's two spikes — entry (the world
release's collect, the strip layer's pre-collect, cover decodes, the
relayout) and exit (the desk re-render, the stamps, one full frame) — are
gated as single-frame maxima. Idle
stays zero under every layout and in both modes: `tools/p4_bench.py`'s
`desk idle`, `library idle`, `settings idle` and `app idle` capture no frame,
and `ws.note_cost`'s `bar.strip.render` count over an idle minute is one
(the clock's) (`runtime/shell/console_perf.py`). The carousel's motion cost is
§8.10's and goes to #66; parked windows' memory and the off-heap census are #58's.

## 9. Scrolling: one scroll model

In §9, L1-L10 are `docs/surface_model_v1.md`'s rules (written "surface-model
L5"), not §1.2's.

### 9.1 The motion: one body

`moy_ui_scroll_t` (`native/moy_ui/moy_ui.h`, the rules in
`moy_ui_interact.c`) is the one scroll state: a drag, a fling's velocity and
float offset, a **glide target** and the glide's start. One
`tick(dt)` advances whichever is live:

- **a fling** coasts: `FRICTION` per ms, `MIN_FLING` at release,
  `STOP_VEL` at rest, a hard stop at the clamp, dt injected, single
  precision (the physics twin, `tests/test_ui_physics.py`);
- **a glide** eases the offset to its target in TIME, half the distance left
  every `GLIDE_HALF_MS` (the carousel's `_GLIDE_HALF_MS`, 45, moved into C),
  rounded to the pixel, and reaches the target exactly; a new target while
  gliding re-aims from the current offset, so a held key that owes steps
  reads as one movement at the step rate. A 20-40 px step is five or six
  halvings, ~230-270 ms of frames, the carousel's feel; the parameter is
  where it is tuned. The handheld home's carousel keeps
  its slide, the glide's second ease: the target reached in `SLIDE_MS`
  (200), eased out, re-aimed from where the row is. A reveal in a dialog's
  or a popover's body (an overlay, `docs/history/scroll_and_text_2026-10.md` §12) and a focus an app moves lands at
  once;
- **a drag** catches a fling or a glide.

A step is `step(n, size)`: the target moves by n steps of the surface's step
size (a row, a card, a line) from the target if a glide is live, else from
the offset -- a step moves exactly one step, never more, and n steps in one
frame move the target n steps at once (the view is never more than one
glide behind its target). `glide_to(p)` aims at a content position (a focus
reveal, `docs/history/scroll_and_text_2026-10.md` §7). `ScrollRegion` (`runtime/toolkit/ui_draw.py`) stays the Python face
over the same buffer, with `step`, `glide_to` and `gliding`; a grep guard
holds that no `_GLIDE_HALF_MS` or ease arithmetic exists outside
`moy_ui_interact.c`.

**The engine has a clock word.** `ui_frame`'s `inp` array carries
`MOY_UI_IN_DT` (the loop's tick in ms, injected -- never a
clock read -- so the physics stays deterministic under test); a Lua or
compiled app's run takes the sum of its ticks' injected dt since its last
frame (`moycore_run_tick_dt`).

**The verbs engine's nests scroll on it too.** A page body, a `ui.scroll`
region and a `ui.table`'s rows are nests whose offset is a `moy_ui_keep_t`
(`a`). Each scrolling keep has a `moy_ui_scroll_t`, driven by
the same `moy_ui_dragtap_frame` the launcher rides, so a release flings and
`api_begin` ticks every live state before the pass. The physics is therefore
ONE function on every surface, and the twin test runs it once.

**Motion is a declared Class B source.** `api_moving(ctx)` answers whether
any nest of the context glides or flings; `AppHost.keys()` and `still()`
read it beside `owed`, the frame gate reads it as animation, and the
windowed WM reads it in place of `ws._animating` for the content freeze and
direct render (`docs/history/scroll_and_text_2026-10.md` §8): a surface moving on its own is the one legitimate
reason to paint with nothing dirty, and the WM must know it is the focused
window's content and nothing else.

Parameters are configuration, not measurements: `GLIDE_HALF_MS` 45 in
`moy_ui_interact.c` beside `MOY_UI_FRICTION`; `REPEAT_DELAY_MS` 400,
`REPEAT_RATE_MS` 50 and `BALL_COAST` 0 in `moy_input` (§9.2, §9.3), each
overridable per board in its board.toml.

### 9.2 The inputs: a held key repeats at the input layer

A held button repeats in `moy_input`'s merge (`moy_input_begin_frame`,
`native/moy_input/moy_input.h`), per source and per button, on a clock the
caller passes in -- `begin_frame(t, now_ms)`, 0 for none -- so
the count is against time, not the frame: after `REPEAT_DELAY_MS` of level,
one repeat every `REPEAT_RATE_MS`, and a frame that took two periods owes
two. The table exposes the count: `moy_input_repeats(button)` this frame
(the binding's `repeats(name)`), and `steps(name)` = pressed edge +
repeats. `btnp` stays an edge (a cart's `btnp` is unchanged, surface-model L9; a game that
wants repeat has `btn`), and `moy_input_clear_edges` (a modal opening on a
key) zeroes the repeats with the edges and starts each held key's delay
over. A surface that takes a frame's steps for itself (a caret) spends them
with `moy_input_spend_edges`: no layer after it sees them, and the held key
keeps its level and its schedule, so it neither presses again at the next
merge nor waits out the delay again.

Typed keys repeat from a level the table keeps for that purpose. A source's
`key` is a level a BLE keyboard holds; the T-Deck in text mode types
one-shot press edges from its matrix (`moy_kbd.c` `type_edges`) and holds no
key level, and its `text_mask` strips the aliased buttons. The driver writes
the matrix's typing key into a **typing-level slot** of its source that
carts never read, and the merge repeats from that slot and from a BLE
source's key level, delivering a count (`moy_input_key_repeats`) beside the
frame's key event. Repeat counts never enter `key()`, `keyp()` or `last_key`:
a cart in text mode is promised each typed key once
(`docs/moy_cart_api.md`), and that promise holds. The verbs engine reads two
input words, `MOY_UI_IN_REP` (four 8-bit direction counts) and
`MOY_UI_IN_KEYREP`; a text surface takes the count rather than one key per
frame, so the one-shot queue's zero frame between equal keys does not cap
the rate.

**One repeat, from one place.** The browser's page sends a key's down
once, whatever the OS repeats (`e.repeat` dropped for every code); the
host's and the browser's keyboards both reach `ConsoleDriver.key_down` and
`key_up`, which hold the key's typing level, and the table repeats on every
tier alike, on the frames' injected clock.

Where the level is not known the lever is `None`: the T-Deck keyboard in
its ASCII fallback (an older keyboard firmware) types edges with no matrix,
so it repeats nothing.

The consumer rule, for every surface: a repeat is a step, not a press.
Nothing that opens, activates or commits runs on a repeat; only motion
(focus moves, caret moves, scroll steps) and typing do. `moy_ui`'s key
handling takes `go` and `tab` from the edge word only.

### 9.3 The trackball

A pulse is a step at the input layer (`Console.ball`, `BALL_STEP`). The
steps are delivered as a **count in the `MOY_UI_IN_REP` word** (the table's
`add_steps`, read as `steps(name)` by the Python layers), every pulse's step
in the frame it arrived: no queue and no per-frame cap, so every step a roll
makes reaches the surface (what a fast roll delivers is in #66). The glide absorbs them -- the target moves n
steps and the view eases to it -- so the lag is bounded by construction:
the view is always one glide (a few `GLIDE_HALF_MS`) behind the newest
target, never a queue's length behind. A caret takes the count as lines
(`nav`).

`BALL_COAST` is a board's board.toml `[input] ball_coast` (0 by default, §1.4): steps a roll delivers
above `BALL_COAST_RATE` per second become a coast instead of steps,
`BALL_COAST` thousandths of a step a ms per surplus step, delivered on as
steps that decay with the fling's friction -- so every surface that takes
steps coasts, a focus-driven list as well as a page; 0 is no momentum, the
default.

### 9.4 Who takes it, from one place

| surface | scrolls through | drag/fling | key, repeat, ball steps |
|---|---|---|---|
| verbs-engine pages, `ui.scroll`, `ui.table` rows (Settings and its pages, Config's cards and its file list, Files, Get Carts, Storybook, My Art, Calc, Appearance) | the nest's `moy_ui_scroll_t` | the engine's drag-tap; the fling | focus moves a stop; the reveal is `glide_to` (`docs/history/scroll_and_text_2026-10.md` §7) |
| the home's carousel, grid and icons; the PROJECTS window | `ScrollRegion` (`Launcher._scroll_region`) | as today | the selection moves; `glide_to` the card |
| the Code tab, theme Config text, the desk's made code, the text console's log | `ui.text_view` (`docs/history/scroll_and_text_2026-10.md` §5): the nest's `moy_ui_scroll_t`, in visual rows | the engine's drag-tap; the fling | the editor's `top`, moved to keep its caret in view, lands the view there in that frame (`ui_draw.text_aim`); a fling and the wheel glide |
| Notes | `ui.text_view` (`docs/history/scroll_and_text_2026-10.md` §5), placed in its page with `ui.at` | the engine's drag-tap; the fling | the caret's row lands in view in that frame (`ui_draw.text_reveal`) |
| the fields | one row in the verbs engine | -- | -- |
| the Map's camera, the sprite pickers | `ScrollRegion` on two axes | as today | `step` |

The Blocks outline and Music's steps are tables of parts
(`docs/ui_blocks_2026-10.md` §4.1): the engine's region, drag, fling and
reveal, their rows carried in C, and the page holding each table the
region R its frames move the table inside. Config's cards are a verbs
page (`editor_settings.SettingsPage`) whose gen makes a key step the
engine's rows frame; its broken-manifest page (`ui.empty`) and its file
list (a placed `ui.table`) are verbs pages too. Carts are out: a
cart's `btn`/`btnp` and its own scrolling are the cart's (surface-model L9); Notes is a
system cart on the verbs engine whose note is a text view drawn by the
shell's `EditorHandle`, and `docs/history/scroll_and_text_2026-10.md` §5 says what it gets.

### 9.5 The browser

The motion is C compiled into the wasm head; the band contract (`docs/history/scroll_and_text_2026-10.md` §3) draws
the same pixels as a full repaint on every tier by its guard; the wasm
canvas is one persistent buffer and shifts in place (§9.6). So a fling on
the page is the host's fling, frame for frame under the same injected dt,
and `tools/web.py shot` against the host's screenshot is the guard (`docs/history/scroll_and_text_2026-10.md` §13).

### 9.6 The backend verb: `shift_last`

`shift_last(x, y, w, h, dx, dy)` on a canvas, beside `docs/surface_model_v1.md` §14.6's `copy_last`:
the rect of the last presented frame's buffer, copied into the one this
frame draws into, moved by (dx, dy). It answers False where it cannot (no
last frame, a gap, a rect it does not have, an engine busy with something
it must not wait behind), and the caller draws. The lever is **per canvas**,
not per board: a root's ring and a one-buffer layer are different canvases
on the same board.

| canvas | the last frame's buffer | the shift |
|---|---|---|---|
| the Waveshare P4's root (the DSI compositor, `moy_dsi_last`) | the ring's front or pending buffer, fenced as `copy_last` fences | the 2D-DMA M2M block copy (`esp_async_fbcpy`), submitted async, fenced at the content's close; cross-buffer in either direction |
| the Guition P4's root (the rotated compositor, `moy_rot_last`) | the other of its two landscape paint buffers (`retained_frames` 2) | the same 2D-DMA; **answers False while a rotate is in flight** (the rotate shares the DMA2D, and a shift queued behind it would wait for it); the shifted R is handed to `note_damage` or the compositor never rotates it |
| the T-Deck's and Guition S3's root (the banded compositor, `moy_banded_last`) | the other of two buffers, the one the feeder is shipping | a CPU row copy, synchronous, reading alongside the feeder (read against read) |
| one persistent buffer: the host's and web runner's root, every window buffer (`win.buf`), every layer, a cart's canvas (`RETAINED_FRAMES` 1) | this buffer | `moy_gfx.scroll_rect` in place |

What each shift costs per board is in #113 (2026-10-23 comment). The S3
root lever is present (2026-10-27; it was `None` on 2026-10-23) because what
the shift buys there is not the region's fill, which that verdict priced,
but the rows' Python and raster outside the band, which `docs/history/scroll_and_text_2026-10.md` §3.3 skips for an
app that reads `ui.band()` -- and `docs/history/scroll_and_text_2026-10.md` §14 prices honestly what an app that does
not read it keeps. The CPU in-place shift stays out everywhere a second
buffer exists: it costs twice the cross copy on the S3s and loses on the
P4s.

**The P4's fence** keeps the four rules of the 2026-10-23 design: before
the submit, fence every op pending on the source buffer (a deferred stamp,
a rotate in flight) and write back the destination rows' dirty cache lines;
between submit and fence, no CPU write lands in a cache line of the
destination rows; at the fence, wait and invalidate the destination rows'
cache; the frame's own async ops keep their order, the shift landing before
the content's close so `end_frame`'s last-write op is still last. **The
rows the frame draws inside R are drawn after the fence** (`docs/history/scroll_and_text_2026-10.md` §3.3 step 4), so
the Python pass overlaps the DMA and the CPU never waits mid-pass; the
band's raster beside a landing DMA runs slower (PSRAM contention,
#113), which `docs/history/scroll_and_text_2026-10.md` §14 prices.

**A verb for the window buffer.** `copy_last_into(layer, x, y, w, h)`
copies the rect of the last presented frame's buffer into a layer (a window
buffer), with the P4's fence; `copy_last` writes only into the drawn-into
back buffer and brings no window buffer up from the root, which the
windowed WM's direct render of key frames needs
(`docs/history/scroll_and_text_2026-10.md` §8).

### 9.7 Why this is not a second invalidation mechanism

Whether a surface draws is decided by the gate, the gens,
`docs/surface_model_v1.md` §14.1's freeze and one Class B source, `api_moving`, which a nest declares
while it moves. The record only says, for a frame that IS drawing, what the
buffer lacks and how much of the content to rasterize; `shift_last` only
replaces the drawing of a lag the record names, as `copy_last` does for a
whole frame. That is surface-model L5's line: the shared layer decides whether, the
backend how. Against `docs/surface_model_v1.md` §8's graveyard: no widget reports damage (one region,
one translation, one record per frame, and the widened set is built from
the input the engine consumed and the producers' gens, never from the
pass); nothing is inferred (a stop whose content changes without a gen is a
bug the surface-model L8 assertion fails under test, not a hash the frame pays -- the
line surface-model L2 gains: a draw-stream comparison may assert, never license); no
root framebuffer is retained across web frames (the wasm canvas is one
persistent buffer and shifts in place, which is retention of one buffer, as
every raster tier declares); the PPA copies nothing 1:1 (the 2D-DMA is a
different engine, and what it buys is the CPU's time); and the cost that
goes is the region's raster and the rows' Python, measured on a live whole
frame.

`docs/surface_model_v1.md` §14.7 states the change record, the two Δ
rules and `shift_last`/`copy_last_into` in the model's voice, its §7 the
two ring bodies (the windows' `_shown`, the contents' `PaintRing`) and the
one-voucher rule, and its §3 `api_moving` among the Class B sources.

### 9.8 What reports `None`

Per canvas, then per source:

| lever | present | `None` |
|---|---|---|
| `shift_last` across buffers | the P4 roots (2D-DMA), the S3 roots (CPU row copy) | a one-buffer canvas (shifts in place instead); the Guition's root while a rotate is in flight (answers False, the frame draws) |
| `shift_last` in place | one persistent buffer: the host's and web's root, every `win.buf`, every layer, a cart's canvas | the P4 and S3 roots (a second buffer exists; the CPU in-place shift loses) |
| `copy_last_into` a layer | roots with a `last` buffer (the P4s, the S3s), and the host's compositor, whose `last` is a test's ring as for `copy_last` | one persistent buffer with no ring: the web's canvas, every layer |
| the PPA ground fill before R opens | the P4s | the S3s, the host, the web |
| a shift frame of a nest | a nest whose app passes every gen it draws inside R | a nest with a `ui.part` and no gen, a placed `ui.at` stop, a Lua or wasm app |
| a host's rows frame (an editor handle's) | a Python app on the verbs with one handle, its page under a gen | a Lua or wasm app, two handles, a declared `background`, an embed in view |
| a table of parts moving inside its page | the Blocks outline and Music's steps: a drag, a fling, a key step, a changed row, the page under its gen | an edit the head or foot shows (the page's gen moves), a prompt over the page, a floor past three fifths of the page |
| held-key repeat | every level source: the T-Deck matrix, BLE, the host's and browser's keyboards, a gamepad | the T-Deck keyboard's ASCII fallback |
| the trackball's steps and `BALL_COAST` | the T-Deck (and the host's arrow pan) | every board without a ball |
| direct render of key frames | the windowed WM over a root with `set_viewport` (the P4s, the host desk) | the web runner's canvas (keeps the stamp); the fullscreen WM (no window) |
| the debt copy of rows | roots with `copy_last` and a ring deeper than one | one persistent buffer (owes nothing) |
| a kept table's move (the home's band) | a band over one colour, on a canvas with `shift_last` | a wallpaper under the band (its pixels would move); the canvas refusing (the Guition's root while a rotate is in flight): the band draws whole |

Where a lever is `None` none of its bookkeeping runs: no ring entry is
written for a shift that cannot happen, no widened set is built, no refill
is queued. The host runs every mechanism under test as an emulated tier
(`docs/history/scroll_and_text_2026-10.md` §13), which is how a `None` that should have been a value is caught (surface-model L8).

## 10. Text on the console: one editor, three doors

Owner decisions of 2026-09-07, and the Run verb of 2026-10-30.
`runtime/README.md` maps the files that build them (`editor_handle.py`,
`text_modes.py`, the Files and Notes rows).

### 10.1 The decision

Notes is the one text app. There is no Writer process: undo through the
op-history core, clipboard, wrap and the T-Deck's text-mode keyboard live IN
the shell as an editor handle a cart draws through, the way
`make_layer` exposes the layer engine. The kid edits the skin, never the engine.

Docs are plain Markdown files in one flat vault so a PC with Obsidian opens the
same folder unchanged. The file is the interface; sync is a folder moving over
the existing RPC or the card, not an app feature.

### 10.2 Three roots, three doors

Roots a person touches: PROJECTS (each cart a folder), NOTES (the vault, one
flat folder -- `.md` by default, and whatever else a person names), and the
drawings the Files app owns.

| door | path | what opens |
|---|---|---|
| make a game | home → Make → a project → the Editor's tabs | the Code tab is one of the cart's scripts — its main file unless another is picked (#89): highlight by runtime, parse gate on the debounce, hard exits always write (SYNTAX badge), PLAY, crash-to-code |
| write | Launcher → Notes | the vault as a table (New note first, then each file's kind icon and its name: a note's bare, another file's with its extension), New note → a name in a dialog, one file open with Select, Copy, Cut and Paste at its foot (and Run on a `.py` or `.lua`) and undo and redo in the bar (docs/media/look_2026-10/APPS_RESTYLE.md's Notes) |
| any file | Launcher → Files → a root | routed by what it is (below) |

**The Files router**, in order: a `.moy` folder opens the project Editor, never a
listing; any script in a cart's own load order — `main.py`/`main.lua` and every
other entry in its `sources` (#89) — opens the Editor's Code tab (PLAY, the
journal and crash-to-code live there); an image opens Paint -- including a
cart's own `images/*.moyimg`, edited in place on the project kind, and one
Paint has no editor for opens READ-ONLY rather than being refused, because a
picture always has somewhere to open; any other text file
(`.md`, `.json`, `.txt`, a script `.py`/`.lua`) opens the editor in the mode for
its extension. A project's own files as files are reached only through the Config
tab's Files, so the loader stays in the loop.

**Everything the router opens comes BACK to Files**, on the shelf it was left
on. Files is a cart app, so a note/project/drawing REPLACES it, and the console
records who it is returning to: the run's caller kind (`ws.returns`) for a run
(the Notes cart), `_project_return` for a project file (which returns through
the loader), and the app-return (`ws.returns.back()`) for a jump into another
app's own surface (Paint, a project's Editor) -- popped by
`Workstation._go_home_or_back`, which is what every exit gesture ends in. HOME is still home, and going home clears the return.

**Modes** are the only thing that differs per file. Every file draws as its
monospace source in `ui.text_view` (`runtime/editor_handle.py`): Markdown wraps
and is coloured as syntax (285b5d1e), its checkboxes and `[[note]]` links still
answering a tap; code (the per-runtime highlighter and parse gate), JSON (pretty-printed at indent 2 on
open, saved as shown; soft save refuses an invalid document; a hard exit writes
it with an INVALID badge and the loader re-validates on the next open), plain
text.

**WRAP is prose, PAN is structure.** Markdown and plain text soft-wrap, because
a sentence that runs off a 320px screen is unreadable. Code and JSON do not:
their LINES mean something, so a long line pans sideways instead -- the Code
tab's gesture, drag or trackball, one rung down.

### 10.3 What the vault holds, and what a name means

**Every file in the vault is listed, under its whole name, with its mode's
badge.** `.md` is the ONE extension a vault name may leave off, because a bare
name IS a note; `todo.txt`, `data.json`, `hi.py` and `hi.lua` keep theirs,
because the extension is what picks the mode and what stops two files shadowing
each other. The store's `VAULT_EXTS` is that set and `text_modes.badge_for_kind`
is the label.

**NEW asks for a name** (Notes' prompt; the Files rename field's pattern) and
the name decides the mode: a bare name is Markdown, an extension is honoured.
That is the only thing on the console that MAKES a `.txt` or a `.json`. The store slugs and unique-ifies
what was typed (`moy_carts.free_file_name`), so NEW never overwrites.

### 10.4 Scripts

A script is a cart with no folder: a bare `.py` or `.lua` in the vault, listed
under its whole name like every other vault file (a
separate `scripts/` root waits for the terminal's `run`, #115). RUN (from Files, from the editor handle the script is open in, later `run name.py` at the terminal prompt) wraps
it on the fly — a synthesized manifest of `type: "script"`, the same portable
subset every cart gets, a TEXT CONSOLE as its surface (`print` to scrollback,
`input` on the prompt's line editor). Default grants are `files`, `prefs` and the
console; `carts` and the network go through the capability gate (#120). The
terminal (#115) inherits all of it: its `run` is this, its REPL is the script
surface with a live prompt, its scrollback is the console.

**Run belongs to the editor, not to Notes (2026-10-30).** A script open
in the console's editor handle runs from it: `ed.can_run()` on a `.py`/`.lua`,
`ed.run()` saves it (hard, so a syntax error is the console's to show) and runs
it in the handle's kind down the path Files' RUN takes. Its exit comes back
INTO the cart that ran it -- that cart started again on the same document,
the caret and the view's first line put back, and the cart's own caller
restored, so Notes opened from Files still returns to the shelf. Notes' vault
comes back on the program's row: a placed table opens on its current row. Notes shows it as a fifth tool, Run, at the
foot and after a rule on the desk; any cart holding a handle gets the same verb.

### 10.5 The board split

The T-Deck is the writing device (keyboard, trackball as caret, the symbol
palette for the keys it lacks). **The trackball is the caret in a cart's editor
handle too, not only in the Code tab**: `Workstation.nav` is the one place that
decides, and it answers whether it spent the roll so the board loop can give the
same pulses to the cursor when nothing holds a caret.

**That keyboard has no Ctrl**, so the handle's `Ctrl+C/X/V/Z/Y/A` are a host
convenience and never the way a note is edited on a board. The device path is a
TOOLBAR -- SELECT, COPY, CUT, PASTE, UNDO, REDO -- carrying the Code tab's own
labels and its `select_sticky` gesture, because it is the row a kid's hand has
already learned. On the P4 and the Guitions long-form typing goes
through a BLE keyboard or the phone's keyboard in the web view; on glass they
read, tick checkboxes, make short edits and use the Config cards. An on-screen
keyboard is #239.

## 11. What other systems taught (research, 2026-09-27)

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

## 12. Declined

### 12.1 Theming

- **Code in themes** in any form (scripts, carts inside a theme, plug-in
  decorators, callbacks) — §6.
- **Per-theme RGB palettes** were declined here on 2026-09-27 (MOY64 subsets
  covered the target looks, and on the shared-canvas tier the shell and the
  game are one raster). The decline is reversed (2026-10-15, the owner's
  delegate for the 2026-10 look): MOY64 has no ramp for that look's tinted
  neutrals, amber or lavender, and a shell-only block at indices 64–127
  leaves a cart's colours at `& 63`, so the game's raster cannot change
  (`docs/history/look_2026-10.md` step 5).
- **Absolute placement** in themes — §3.2.
- **Theming games' pixels**, and `theme()` for games without a moy-spec proposal — §5.
- **Identity-keyed theme caches** — §5.
- **A strike ledger, persist-after-render and a per-board reset input for
  themes** (owner, 2026-10-14) — a theme is data the loader validates, it
  cannot run code, and every board has a reset button; the loader's refusal
  and the default seed are the way out (§6).
- **User themes that inherit** (owner, 2026-10-14) — a user theme is a
  flattened copy; inheritance stays among the seeds (§4).
- **A SAVE in Appearance** (owner, 2026-10-14) — the OS has no save;
  `custom` saves as it goes through Paint's mechanism.
- **The six-page theme editor** (owner, 2026-10-14) — COLOURS, FRAME,
  SHAPES, FONTS, SPRITES and LAYOUT pages, a per-role picker with
  "fails its pair" marks, a name page and a byte budget (mockups on the
  unmerged branch `editordesign`, `937863c7`): too complex for what a kid
  changes. One page of rows, every colour derived (§3.10), and Edit theme…
  for the rest.
- **SHARE and PASTE, the one-line form** (owner, 2026-10-14) — a theme
  travels as its package through the store (Files, the Zero's sync, the
  browser console's export and import, the carts repository); NAME… is how
  a kid keeps one.
- **A wallpaper setting beside the theme** (owner, 2026-10-14) — the
  wallpaper is the theme's (D6).
- **Per-slot wallpapers** (owner, 2026-10-14) — one screen-sized bake per
  slot is memory a board does not have, and the home already shows the
  desk's wallpaper behind its cards; one wallpaper, D6.
- **Painter fills on the PPA** — §3.4.
- **A surface for the home's band** (`home.band`, a scrim; owner,
  2026-10-15) — no seed used it, it cannot reach a kid's own picture in
  `custom`, and an opaque scrim hides the wallpaper the home shows (§3.9).
  Nor a `home_safe` manifest flag — the cart author's claim about every
  theme's home, which no check reads (`docs/history/theming_2026-09.md` §12.1).
- From the look sessions (D1): MOY64 grey chrome instead of the PICO-8 base
  colours, cartridge-shell or notched cards, labels inside the desk tiles, and
  Pixel Operator replacing Tiny5.

### 12.2 Shell layout

- **Composable layouts, widget carts, a widget catalogue a theme extends**:
  the owner's scope is simple (2026-10-13); anything livelier than a bar of
  built-ins is a wallpaper cart's job (§3.9).
- **`windows: maximized`, and any `windows` key** (owner, 2026-10-13): the
  per-window maximize button already exists, and a mode that pre-presses it
  is a second way to the same state (L0). D5's "window mode" is answered by
  the button (`docs/history/shell_layout_2026-10.md` §16).
- **A boot-into-console setting, or a remembered last mode** (owner,
  2026-10-13): not for now; PLAY is the only way into console mode on a P4.
- **A `mode` key, a DESKTOP row in the system menu, a `settings` widget, a
  HOME row, a calendar app or desk icon**: each is a second way to a thing
  that has one — PLAY and the bar's back, the menu's Settings row, the HOME key, the
  calendar component (L0). A second home is declined too; a second
  ARRANGEMENT of the one home is the `home` key (L14).
- **An on-screen home button, and touch buttons for what a key does**
  (owner, 2026-10-13): out of scope for now, and may be added if requested;
  a bar cell is precious space, and every board has a keyboard (L12). Input
  to come -- BLE joysticks, a USB keyboard, mouse or joystick -- maps onto the
  same key verbs (HOME, back, menu), so the key is the extension point.
- **Per-widget cell repaint inside a strip** (review, 2026-10-13): the
  whole-strip re-render is one C paint and already inside the frame-alloc
  rule; a cell path would be a second render path for the same pixels.
- **A dock**: the bottom dock of the pre-2026-07 shell was deleted on
  2026-08-21 and `tests/test_desktop_shell.py::test_the_pre_2026_07_bottom_dock_is_gone`
  is the ratchet; pinned apps are the desk's icon column, running ones the
  `chips` widget.
- **A tiling WM**: two windows side by side need a reflowing compiled app,
  which `docs/kernel_toolkit_2026-10.md` §12 defers; the Editor beside a
  playtest is #178 and floating does it.
- **Desktop mode on the T-Deck and the Guition S3**: §8.1's hardware reasons.
- **Refusing PLAY while a playtest runs**: it is ended instead (owner,
  2026-10-13).
- **Bars for the OS surfaces only, a running app keeping a top strip**
  (owner, 2026-10-13): global bars save the most space, and two bar
  positions on one console is the duplication L0 forbids.
- **The `moybyte.wifi.moy` tool cart as console mode's wifi container**
  (owner, 2026-10-13): the Settings panel is the one implementation; the
  line between system carts and built-in apps is not settled and can move
  later (§1.2).
- **Pixel-positioned widgets, scripts in a layout, more than one bar per
  edge, auto-hide, `size` under 16**: §3.2's rule,
  §6's "data only", and #203's tap floor.
- **Rotated text in a vertical bar**: `title`, `text` and `zone` need a
  horizontal bar; the clock's vertical form is two lines, not a rotation.
- **Configurable popover contents, a popover a theme adds**: fixed per
  widget, four of them.
- **Large bars and docked widgets** (owner, 2026-10-14; L13): a `calendar`
  widget docking the month grid in a side bar, and any bar thicker than the
  compact widgets need. It was built (step 6's example 5, a 120-row right
  bar) and removed the same day: a widget is an icon or a short value, and
  what is bigger opens as its popover or its page -- one way to the
  calendar, not two. `size` is capped at 32 (§8.3).

