# Shell layout — two modes, bars, built-in widgets, popovers and the home (2026-10)

The doc `docs/theming_2026-09.md` §4.8 reserved: what its D5 (a theme sets the
bar layout, the user can override it, the override survives a theme switch)
and D7 (one tabbed cover-carousel home on every board, which the desk's PLAY
opens) need from the shell. D5 and D7 are the owner's (2026-09-27). The
owner's direction on scope (2026-10-13) is **simple, and two modes**: a
console mode for small screens and a desktop mode for large ones; a layout is
a short list of bars holding built-in widgets; a widget may open a popover in
desktop mode; fancy desktop things are wallpaper carts' job (§15). Everything
else here is this doc's, dated 2026-10-13, revised the same day after two
adversarial reviews (§18), and theming phase 5 builds it (§13).

Where it sits: the theme format is `docs/theming_2026-09.md` §3 and §5; the
shell's process model is `docs/shell_ux_v1.md`; the dirty protocol every cache
here obeys is `docs/surface_model_v1.md` §3; the app contract it extends is
`docs/app_api_v1.md` and `docs/kernel_appabi_2026-10.md` §2.5; the WMs stay
Python per `docs/native_kernel_2026-09.md` §10 question 8 and
`docs/theming_2026-09.md` §8. Numbers that measure the shell live in **#66**
(frames, idle floors, the shelf drag per board) and **#58** (the P4 port, its
memory and compositors); this doc cites, it does not copy.

## 1. What this doc rules

| # | ruling (2026-10-13) | section |
|---|---|---|
| L0 | **The owner's governing principle:** the mockups are not set in stone; the UI should make sense; in doubt, strive towards a real OS, less a game console; no duplication; things are consistent. Every ruling below is read against it: one mechanism per job, one way to each thing, and a board differs only where its hardware says so (§10) | all |
| L1 | **Two modes.** CONSOLE mode: one app fullscreen at a time over a back-stack rooted at the animated home. DESKTOP mode: the desk, windows, bars, popovers. They are the two WMs that exist; console mode on a P4 is `FullscreenStackWM`'s presentation, the one the handheld boards run, never a third variant | §3 |
| L2 | **The mode is explicit WM state**, `wm.mode`, that every tier-sensitive branch consults; the desk's PLAY enters console mode, the `close` widget on the home leaves it; parked windows live outside the back-stack and survive the switch; nothing else switches and no boot setting exists | §3 |
| L3 | A layout is **up to four bars**, one per edge, each with a size and three slots holding **built-in widgets** from a fixed catalogue; `layout` holds `bars` and nothing else | §4, §5 |
| L4 | **The content rectangle is derived from the bars**; `content_rect()` is the app-cart contract, `bar_h()` a shim; the surface row grows additively; bars are global, every surface lays out off them; compiled apps and games are untouched | §7 |
| L5 | **One popover mechanism**, desktop mode only: the system-menu `Popup` generalised, riding the quiet stack with its own rect as damage. Its contents are **components** that draw in a popover, or fullscreen in console mode | §8 |
| L6 | **One safety rule**: `menu`, `zone` and `close` placed after every drop, else the tier default; caps, the tap floor and a minimum content area checked once, deterministically | §9 |
| L7 | **Precedence: user, then theme, then default.** The user's override is one strict-read row, `layout_theme`; `set_theme` never touches it (D5) | §11 |
| L8 | **A bar is a cached strip re-rendered whole when its key moves**, as today, and blitted otherwise; a layout change is a relayout; no per-cell repaint and no new invalidation mechanism | §14 |
| L9 | **The home is the carousel with the animated centre card**, console mode's root; its scale is a function of position, never of time; the fullscreen home hands its band to the compositor as damage; a board degrades by measurement, never by data | §12 |
| L10 | Pixels move only where a step restyles on purpose: new bar, popover and console-mode rows are NEW golden configs; the home is one named re-baseline; every other step holds every golden byte-identical | §13 |
| L11 | **The HOME key backs out one level, everywhere** (owner, 2026-10-13). In desktop mode it shows the desktop, `show_desk()`: every window minimised to its chip and kept, HOME again restores them, and it never closes a window (Win+D's shape). In console mode it goes home, and from the home on a P4 to the desktop | §3 |
| L12 | **The input model assumes a keyboard on every board** (owner, 2026-10-13): the T-Deck's own, a BLE keyboard on the Guition S3 and both P4s. Touch is supplementary -- a fullscreen game on a P4 is left by the keyboard alone -- and there is no on-screen home button: it would spend bar space on what the HOME key does | §4, §15 |
| L13 | **Every bar widget is compact** (owner, 2026-10-14: "I only want the calendar to pop out"; "that should be the default for every widget, why would we want giant bars?"): an icon or a short value. Anything bigger opens as its component -- the popover in desktop mode, the fullscreen page in console mode -- and no widget is ever docked large in a bar, so a bar's `size` is a slim range, 16..32. Side bars stay, as thin bars | §4, §5, §8 |
| L14 | **The home has arrangements** (owner, 2026-10-14: carousel, the old big grid, and later a small icon-only grid; "we don't have to do it now"): one home component, its tabs, sources, taps and bar verbs shared, its card layout the layout's `home` key -- `carousel` (default) or `grid`, `icons` planned | §5, §12 |

## 2. What exists, read against this doc

- **Two WMs, one a subclass of the other.** `runtime/wm.py`'s
  `FullscreenStackWM` runs on the S3 boards and the host: the top of the
  process back-stack owns the whole glass. `runtime/wm_windowed.py`'s
  `WindowedWM` (over `runtime/wm_desk.py`, the desk root layer, and
  `runtime/wm_chrome.py`, the frames and taskbar chips) subclasses it, runs
  on the P4 boards, the host with `--windowed` (`runtime/host_app.py`) and the
  browser's desktop arrangement (`firmware/web_runner/web_boot.py`), and
  presents fullscreen whenever the desk is not in the stack: `_slots` makes
  everything above the `desk` kind a window, and its fullscreen branches test
  `not self._order` or `not stack.has("desk")`; `windowed_chrome` is
  `wm.desk_open()` (`runtime/console.py`), which zeroes the apps' bar band;
  `keeps_animating` forces full frames while the desk's wallpaper is live;
  the world flip in `_on_nav` keys on `desk` in the stack; `on_relayout`
  calls `_drop_wins()`, which drops every window. The S3 boards deny the
  windowed halves by name (`firmware/guition_jc3248w535/board.toml`); the
  Zero stages nothing that draws.
- **The back-stack is a kernel set rooted at `launcher`**
  (`native/moy_spine/moy_route.h`, `MOY_ROOT`); `moy_back_goto`
  (`native/moy_spine/moy_route.c`) re-opening a kind below the top truncates
  everything above it. `go_home` (`runtime/console.py`) pops the desk and
  resets the Editor and project; the desk's PLAY is `ws.open_library()`,
  which is `go_home()`, so today it closes the windows and shows the
  Library fullscreen (`docs/shell_ux_v1.md` §3, #105). The exit model is
  context-dependent: the right zone draws the X when `where` is not `home`
  or `desk` (`bar_layer._render_right_zone`, `show_x`), and `docs/shell_ux_v1.md`
  §9 calls that the right zone's.
- **One bar, at the top, already zoned.** `runtime/bar_layer.py` draws every
  surface's strip (`where` in home, desk, menu, settings, tool, app) through
  one cached strip per `where` and canvas, re-rendered whole when
  `_cart_bar_key` moves (it folds the clock minute, the wifi kind and the
  surface's zone generation) and blitted otherwise; `redraw_clock` repaints
  the desk backdrop's clock, not the strip. The band is C
  (`native/moy_play/moy_chrome.c`). The responsive geometry is `chrome.Layout`
  (`runtime/chrome.py`): a LEFT zone lent to the surface and a RIGHT zone the
  OS owns — the clock, the ≡ toggle, wifi, the battery glyph hard right, the
  X. Beside it is FIXED geometry on the game canvas: the running tool/app bar
  (`_ZONE_LEFT_GAME`, the `_BAR_*` rects, `_zone_is_game`), `editor_app.py`'s
  use of it, `widgets._POPUP_Y`, `WindowedWM._bar_h()` (top-only, in window
  sizing, cascade, maximise and resize) and the desk icon column's
  `bar_h`-relative geometry in `wm_desk.py`. A running game gets no strip.
- **The system menu is a popup already**: `widgets.Popup`, drawn by the
  `sysmenu` layer (`runtime/layers.py`): up/down move, A or Enter activates
  and closes, B or stop dismisses, a tap outside dismisses. Its rows
  (`runtime/console_notices.py`, `_sysmenu_items`) are the cart's restart and
  delete, search, Settings, About and Reboot. Other overlays (the toast,
  the notice, About) disable the quiet stack and the damage description
  (`wm_windowed.py`: the quiet-frame path and `_hand_damage`); the popover
  rides them since step 6 (§8).
- **Wifi is one implementation**: the WIFI panel in Settings
  (`runtime/settings_layer.py`, `open_wifi`, which holds a radio lease while
  open); the `moybyte.wifi.moy` tool cart the fullscreen bar tap launched
  was deleted in step 6. Settings draws the VOLUME and BRIGHTNESS rows.
- **The Guition P4's compositor** (`native/moy_glass/moy_present_rot.c`)
  rotates, per described frame, the game rect, the damage rects and the bar
  strips that moved: the strips are a rect list (`MOY_ROT_STRIPS`, 4 after
  coalescing) that `BarLayer.publish_strips` sets at boot and on every
  relayout, one strip today, the tallest top bar; a bar re-render, a chip
  change and a clock tick mark theirs moved (`strip_moved`). `MOY_ROT_RECTS`
  (`native/moy_glass/moy_present.h`) is 8: the game, three coalesced damage
  rects and four strips. Narrow rotated blocks have wedged the PPA
  (`firmware/guition_jc8012p4a1c/README.md`). Two callers describe a frame:
  `WindowedWM._hand_damage` (desktop mode's windows) and the fullscreen
  home's shelf drag, which hands its band through
  `FullscreenStackWM.hand_content_damage` (§12).
- **The surface row** (`native/moy_app/moy_app.h`, `moy_app_surface_t`) holds
  the canvas, `w`, `h`, the origin, `bar_h` and the two scales; the shell
  writes it in `console._surface_write` and per window in
  `_LayoutCtx.install`; a user app reads `bar_h()` ungated
  (`runtime/system_api.py`); `native/moy_app/roles.json` lists `surface.bar_h`
  with `wasm: null`. The shipped apps compute their own band
  (`ListShellLayout._init_frame`, `runtime/app_shell.py`), and `console.py`'s
  band check reads that layout field back.
- **The home today** is the Library shelf (`runtime/launcher_layer.py`:
  `Launcher`, kinetic scroll, #113's scroll-as-blit whose shift key includes
  `grid.sel`, the retained stamp of `tests/test_home_retained_frame.py`,
  the `_lib_cache` of decoded covers, the pinned Make tile; covers are
  RGB565 bakes blitted at an integer scale, `_draw_cover`/`_cover_scale`).
  `Launcher.sel` bumps the zone generation, so a selection change re-renders
  the bar. `ws.picker` is a second `Launcher` over the editable carts.
  `sspr` (`device/device_canvas.py`) scales INDEX sheets; the
  arbitrary-ratio RGB565 blit is `moy_gfx.blit565_resize` (step 7a).
- **One cart runs at a time** (#73): moycore's single run slot
  (`docs/kernel_toolkit_2026-10.md` §6). **Per-frame allocation is gated**
  (`tests/test_frame_alloc.py`).
- **Persisted choices** are rows of `ws.system` (`runtime/system_store.py`);
  a Settings row per choice in `runtime/settings_layer.py`. **Format 1**
  reads `layout` (§5, step 4): `native/moy_ui/moy_theme.c` checks it and
  `runtime/shell_layout.py` resolves it.

## 3. The two modes, and the switch as explicit state

| | CONSOLE mode | DESKTOP mode |
|---|---|---|
| feels like | a console: one thing on screen, the animated home, a tap plays | a normal OS: a desk with icons, windows, bars, popovers |
| the WM | `FullscreenStackWM`'s presentation | `WindowedWM`'s |
| the root of the back-stack | the home (§12) | the desk |
| an app or the Editor opens | fullscreen, pushed; back pops | as a window on the desk |
| a bar widget's tap | its component fullscreen, pushed; the system menu as the popup it is | a popover anchored to the cell (§8) |
| boards | the T-Deck and the Guition S3 — this mode only; the P4 boards, the host and the browser when switched into it | the P4 boards, the host with `--windowed`, the browser's desktop arrangement — their default |

**The mode is state, not a stack shape.** Today "fullscreen" on a P4 means
"the desk is not in the stack", read in a dozen branches (§2), and the
back-stack cannot hold the desk and its windows beneath a fullscreen home:
`moy_back_goto` truncates, `go_home` tears the desk down, `on_relayout`
drops every window. So console mode is **`wm.mode`** (`"console"` or
`"desktop"`, on `WindowedWM`; `FullscreenStackWM` is console only), and the
branches that today ask the stack ask the mode: `_slots` (nothing is a
window in console mode), `keeps_animating` (a parked desk's live wallpaper
is not drawn and does not animate), `desk_open`/`windowed_chrome` (false in
console mode, so the bar band applies as on the T-Deck), the world flip in
`_on_nav`, focus and `_sync_windows`, `on_relayout` (a mode switch relayouts
the bars and the root context and never calls `_drop_wins`), `go_home`'s
teardown (in console mode it pops to the home and touches no parked
window), and every exit door. Step 1's gate tests each branch by name.

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

**The way back: the `close` widget** (§4), the X the exit model already
draws wherever something is beneath the surface (owner, 2026-10-13: the
existing X). In console mode on a P4 the home's depth is above the mode's
floor — the desk and its parked windows are beneath — so the X shows on the
home and `wm.leave_console()` is its verb; on the T-Deck the home is the
floor and the X is absent, as today. Hold-BACKSPACE and B do the same from a
keyboard. No DESKTOP row joins the system menu: the X is the one way out of
anything (L0).

**Memory.** Parked stamps are caches: when the Player's fit check for a
cart's canvas against the largest free block fails (fragmentation is the
real failure, #66), the WM releases parked stamps oldest-first until the
check passes, and a released window re-renders once on return, the path a
resize takes. There is no allocation-retry hook. The home's `_lib_cache`
lives across switches (the home is console mode's root and hot on return)
and is released by the same fit-check path after the stamps. The Waveshare
P4's off-heap PSRAM is unmeasured: `tools/mem_census.py p4 offheap` runs
before step 1 commits and its numbers go to #58.

**No boot setting** (owner, 2026-10-13: not for now). A P4 boots to the
desk; PLAY is the only way into console mode and the X the way out (§15).

**Console mode is the same everywhere.** On a P4 in console mode the Apps
tab opens an app fullscreen and the Make tab the Editor fullscreen, as on
the T-Deck; no tab flips the mode; the desk's own doors (its icons,
PROJECTS) are desktop mode's. The desk icon column stays (owner,
2026-10-13): a desk with icons and a launcher is the OS norm.

**Boards with one mode, and the hardware reason.** The T-Deck's one canvas IS
the 320×240 game raster (`runtime/wm.py`), so there is no system canvas to
put a window on. The Guition S3 has a 480×320 system canvas, but at #203's
tap floor its chrome scale is 2: a window's title strip alone is 36 of 320
rows and a 16-logical icon 32 px, so a window smaller than the glass holds
nothing worth having; its board.toml denies the windowed halves for that
reason, and its PSRAM outside the Python heap is the cart-runtime reserve
(`.claude/rules/boards.md`). Both stay console-only (owner, 2026-10-13), and
`chips` has no value on them (§10).

## 4. The model

**A bar** has an `edge`, a `size` (logical pixels, multiplied by the chrome
scale as every metric is, `docs/theming_2026-09.md` §4.2) and three slots.
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
that does not fit its bar is dropped and noted (§10). A tap does the same
thing in both modes; what differs is only the container a component opens in
(§8).

| widget | draws | value | tap | min cell, horizontal / vertical |
|---|---|---|---|---|
| `menu` | the ≡ glyph | — | the system menu (the popup it is today, in both modes) | 16×16 / 16×16 |
| `close` | the context-X | the stack depth above the mode's floor | the exit model's close: pop the top; on a P4's home, `leave_console()` | 16×16 / 16×16 |
| `zone` | the surface's lent zone: its chips (PLAY, CHANGE, SEARCH on the home; the Editor's title, status) | the surface's zone generation | the surface's `zone_tap` | 64×16, flexes / horizontal only |
| `title` | the surface's title | the title string | — | 24×8, flexes / dropped |
| `clock` | `HH:MM` | the minute | the calendar component | 40×8 / 16×16 (two lines) |
| `wifi` | the wifi glyph | `_wifi_icon_kind()` | the wifi component (§8): the Settings panel as a popover in desktop mode, the Settings page scrolled to it in console mode | 16×16 / 16×16 |
| `battery` | the battery glyph | the level bucket | the power component: Settings' level, VOLUME and BRIGHTNESS rows, drawn by Settings (owner, 2026-10-13) | 16×16 / 16×16 |
| `chips` | the taskbar's window chips (desktop mode) | `_chip_sig` | focus, minimise, restore as today | one chip 24×16, flexes / icon chips 16×16 each |
| `text` | a theme string, 1 to 24 characters | constant | — | fit / fit |
| `spacer` | nothing, takes the slack | — | — | 0 |
| `separator` | a 1px line | — | — | 4×16 / 16×4 |

`menu`, `zone`, `close`, `title`, `clock`, `wifi` and `battery` are today's
bar as widgets — the X becomes its own widget so that a layout can place it
and the safety rule can require it (§9), instead of riding inside the right
zone; `chips`, `text`, `spacer` and `separator` are new
cells. Every widget is compact (L13): the tallest cell any of them needs is
16 logical pixels, the clock's two lines on a side bar and an icon across a
top bar, which is why §5 caps a bar at 32. There is no `home` widget: the HOME key is the way home (L11, L12). There is no `settings` widget: Settings is a row of the system menu
(L0). The home's verbs ride in `zone` wherever a theme puts it, so D7's
mockup — a top strip and a bottom verb strip — is two bars in data (§6,
example 4).

## 5. The format

Additive to format 1 (`docs/theming_2026-09.md` §3: a key the loader did not
read is additive, no `format` bump). `layout` holds `bars` and resolves
whole through `inherits` from the nearest package that sets it (§5 there);
the default seed sets today's bar (example 1; owner, 2026-10-13: one bar is
the default look), the taskbar's chips floating over its middle in desktop
mode as they always have. A bar list is never merged by position. `home`
names the home's arrangement (§12, L14): `carousel`, the default, or `grid`;
`icons` is planned, not built, and reads as unknown. There is no `windows`
key (§15) and no `mode` key (§3: PLAY is the switch).

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
| `bars[].size` | an integer, logical pixels | clamped to 16..32 (L13: the compact widgets' 16-pixel cells, an icon's margins, a taskbar of chips at 22 or so; the tap floor is the chrome scale's, #203, not the size's); absent is 18 |
| `bars[].left`, `center`, `right` | lists of catalogue names | at most 6 per slot, 12 per layout; an unknown name is dropped and noted; a widget placed twice keeps its first placement, except `text`, `spacer`, `separator` |
| `bars[].text` | a string per `text` widget, by position | 1 to 24 characters of printable ASCII; longer is cut, empty is dropped |
| `home` | `carousel` or `grid` | absent or unknown is `carousel` (unknown noted); it rides the layout, which a package sets whole with its bars |

The flattened package carries it in a few dozen bytes, well inside
`theme.bytes` (4096). `style.json`'s `theme.layout` holds the edges, the
slots, the widget names, the required and repeatable sets and the caps (data,
as `font_roles` is); `tests/test_theme_loader.py`'s fuzz corpus carries the
cases (a string for `bars`, five bars, two on one edge, a size of 0 and of
10000, an unknown widget, a 200-character text, a layout with no `menu`, one
with `zone` in a vertical bar only), and the boards' module answers it as the
host's does (`tests/on_glass.py`'s `layouts_match_host`).

**As the loader reads it** (step 4, 2026-10-13). A drop or a clamp sets the
package's existing note bits (§7 of the theming doc): an unknown widget is
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
declined for the reason `docs/theming_2026-09.md` §4.2 gives — pixel positions
split a catalogue by screen size, and scripts are code in a theme. GNOME's
date menu is the popover of §8, from memory. Win95's bottom taskbar and the
macOS menu bar are examples 2 and 3, from memory; today's right zone already
follows the macOS model (`runtime/chrome.py`'s comment). Steam's Big Picture
is §3's switch, from memory.

## 6. Worked examples, as data

1. **Today's top strip** (the default seed): the block in §5.
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

## 7. The content rectangle

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

**Where the value comes from** (steps 3 and 5, landed 2026-10-13 and
2026-10-14). One leaf places the bars and derives what they leave:
`runtime/layout_base.py`'s `place_bars`, `bar_rects_of` and `content_of(w, h,
rows)` (each bar `size * rows // 18` thick, `rows` the 18-row bar at the
surface's scale), imported by the bars, every layout and the app heads alike;
a window's layouts take its band instead (`in_window`: today's bar at the
window's top, whose zone alone draws, whatever the theme's layout). `BarLayer.bar_rects(where)` and `content_rect(where)` answer it
per surface; `LayoutBase` carries `cx, cy, cw, ch`;
`ListShellLayout._init_frame` and the calc/appearance/artwork heads take
`content_of` (rows 0 in a window) instead of computing `18 * cs`; and
`console.py`'s band check reads the bar rects, never the app's layout.
`moy_app_surface_t` holds `cx, cy, cw, ch` where it held `bar_h`, written by
the same two writers (`console._surface_write`, `_LayoutCtx.install`) with
`Workstation.app_content_rect()`, the rect the exitable strip leaves the
RUNNING app's screen; the C answers `bar_h` as its `cy`. `roles.json` carries
`surface.content_rect` (`server: c`, `grant: never`, `wasm: null`, as `bar_h`
is) and the role table's drift tests (`tests/test_roles.py`) move with it.
`runtime/system_api.py` publishes `content_rect()` ungated beside `bar_h()` —
an app-API global, not a cart verb (the verb table and moy-spec are
untouched) — and a Lua app gets its four values as four results, the host's
binding fanning a tuple of ints out as moycore does. The shipped apps place by
`content_of` rather than reading the row because a row is one value for every
grant: it answers the running user app, whose strip can be the game canvas's
18 rows while a shipped app on the system canvas sits under `18 * cs`, and a
desk window has none. The resolver (`Appearance.apply_layout`, §11) places
every resolved bar through `Workstation.place_bars`, which relayouts;
`tests/test_content_rect.py` moves today's bar to the bottom through it and
checks every layout, the bar's band, the taps and the desk, and
`tests/test_bars.py` does the same under four bars at once, side bars
included: every layout's rects inside what they leave, each bar's strip at
its rect, taps routed by the bar they land on.

**Bars are global** (owner, 2026-10-13: it saves the most space, and L0):
every surface, the running tool/app bar included, lays out off the content
rect. The reviews found what that must move beyond the responsive `Layout`:
the fixed game-canvas tool bar and its rects, `editor_app.py`'s use of them,
`widgets._POPUP_Y`, `WindowedWM._bar_h()` in window sizing, cascade, maximise
and resize, and the desk icon column's geometry and tap (§2) — every `bar_h`
reader in `runtime/`, which §13's step 3b greps and moves onto the bars,
re-baselining the tool/app rows by name. The alternative — bars for the OS
surfaces only, a running app keeping today's top strip — is declined (§15).

| who | what |
|---|---|
| app carts (Python, `type: app`) | nothing breaks: `bar_h()` stays; `content_rect()` is the verb to move to; `docs/app_api_v1.md` says so in place |
| shipped apps | read the content rect through their layouts (step 3 is byte-identical under the top bar) |
| compiled apps (`moy_app` ABI) | untouched: a compiled app keeps the player window, a fixed canvas the WM scales (`docs/kernel_toolkit_2026-10.md` §6, owner 2026-10-11); `content_rect` joins the role `wasm: null` like `bar_h` |
| games | untouched: the Player surface is the cart's; no mode draws a bar over a game; the windowed player window keeps #178's two sizes |
| versioning | `docs/app_api_v1.md` stays v1: one additive verb; the role table's generation moves as any row addition does |

## 8. Popovers and components

**One mechanism, desktop mode.** `widgets.Popup` generalised: a panel
anchored to a widget's cell, opening into the content side of its bar (down
from a top bar, up from a bottom one, inward from a side bar), its width the
larger of its content's minimum and today's popup width, clamped to the
glass; one open at a time; it closes on a tap outside, on the anchor tapped
again, on B or stop, and on Esc from a BLE keyboard, where arrows move and
Enter activates — exactly the `sysmenu` layer's rules. That layer becomes
the one popover layer, with the system menu as its first content; the system
menu keeps being a popup in console mode too, as it is today on the T-Deck.

**A popover rides the quiet stack** (step 6, `runtime/wm_windowed.py`).
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
game frame, one copy rather than a paint. The snapshots and the face are
released by the fit check (§3) once no popover is live. A change outside the panel (a connect, a stepped
setting, a row's action) marks the frame dirty as before. The system menu
takes the same path. The bench scenarios of §13 step 6 say what each costs
(#58, #66).

**Four components, fixed per widget, nothing to configure.** A component is
one painter, one minimum size and one navigation handler; where it draws is
the container's business, not the component's.

| anchor | component | navigation inside |
|---|---|---|
| `menu` | the system menu's rows, as today | rows |
| `clock` | the month calendar, today marked; left/right page months | a 7-column grid; the day cursor is display only |
| `wifi` | the Settings WIFI panel's rows, the one implementation (owner, 2026-10-13: the `moybyte.wifi.moy` tool cart goes, §13 step 6) — its radio lease is released whenever the container closes, a tap outside included | rows; picking one runs the password step in the same container |
| `battery` | Settings' level, VOLUME and BRIGHTNESS rows, drawn by Settings — one implementation, the popover a container for it | rows |

**Two containers, one rule for which.** A component draws (a) in a popover
in desktop mode; (b) fullscreen in console mode, pushed on the back-stack as
a kind so back pops it: the calendar as its own kind, the wifi and power rows
as the Settings page scrolled to them. (a) versus (b) is the mode's, not the
board's. No component is docked in a bar (L13). No component is a registered app or a desk icon.
The components are `runtime/popover.py`'s; the wifi panel and the rows draw
through Settings' own painters over the popover's rect (`PanelGeom`), the
calendar's one painter is `draw_month`, and the battery's level reads None on
every board today (no board measures one), drawn as `--`.

## 9. The safety rule, and what the loader checks

**The rule.** A layout places `menu`, `zone` and `close` somewhere
**after every drop of §10** — a `zone` in a vertical bar is dropped, and a
layout that then has no `zone` or no `close` has lost its verbs or its exit
— or it is replaced by the tier default and the replacement is noted in the
theme's report (`docs/theming_2026-09.md` §7). The rule therefore runs
twice: in the loader on the data, and in the composer on the resolved bars.
`close` is the way out of anything (and of console mode on a P4), with
hold-BACKSPACE and B as its keyboard twins (`docs/shell_ux_v1.md` §9);
`menu` the system menu; the way to the mode's root is the HOME key (L11),
which no widget doubles. No menu row duplicates any of them (L0).

**Checked once, deterministically.** In the C loader
(`native/moy_ui/moy_theme.c`), as the theme is: the caps and clamps of §5, the
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
top bar (`runtime/shell_layout.py`'s `FALLBACK`). The composer never raises;
both halves are fuzzed (`tests/test_shell_layout.py`) and the boards' module
answers the host's corpus (`tests/test_theme_loader.py`).

**Recovery, and what waits on theming phase 6.** The per-board reset key,
the strike ledger and installed theme packages are theming phase 6's
(`docs/theming_2026-09.md` §7, §9). Until it lands every layout a console
can select is a seed's, firmware, and `layout_theme` (§11) picks among the
seeds; the reset key then clears `layout_theme` with the theme rows, and
the ledger's fallback to the default theme covers a layout because a layout
is part of a theme. With seed-only layouts the risk phase 6 guards against
is small, and this phase does not wait for it.

## 10. Boards: modes, what each can place, and the resolution rule

The tier is each board.toml's `[board] tier`; the WM is the only tier-specific
layer (`docs/shell_ux_v1.md` §3), so **the WM class carries** `LAYOUT_CATALOGUE`
(the widgets it has a value for, per mode) and `has_desk` already says
which modes a board has. Settings shows a row for what the WM lists and none
for what it does not (absence, never a disabled row). Beside it,
`LAYOUT_EDGES` names the edges a bar may take on this tier (all four on both
WMs), and the composer is `runtime/shell_layout.py`'s `resolve`.

| board | glass, input | modes (default first) | WM | widgets placeable |
|---|---|---|---|---|
| T-Deck | 320×240, keyboard and trackball | console | `FullscreenStackWM` | all but `chips` |
| Guition S3 | 480×320, touch | console | `FullscreenStackWM` | all but `chips` |
| Waveshare P4 | 1024×600, touch | desktop, console | `WindowedWM` | all; `chips` in desktop mode |
| Guition P4 | 1280×800 landscape desk rotated onto 800×1280 portrait glass (`firmware/guition_jc8012p4a1c/README.md`), touch | desktop, console | `WindowedWM` | all; `chips` in desktop mode; side bars since their on-glass check (§14) |
| browser | the arrangement picked at boot | handheld arrangement: console; desktop arrangement: desktop, console | as the arrangement | as the arrangement |
| host | `tools/simulate_desktop.py`; `--windowed` | without the flag: console; with it: desktop, console | as the flag | as the flag |
| Zero | no glass | — | none | — |

**Every remaining difference between boards, and its hardware reason** (L0:
consistent where the hardware allows):

| difference | reason |
|---|---|
| the T-Deck and the Guition S3 have no desktop mode | §3's last paragraph: one canvas that is the game raster; a 3.5" glass at the tap floor; the S3's PSRAM reserve |
| `battery` draws nothing on a board without a gauge | the board reports `None` (CLAUDE.md's rule) and the widget is dropped at resolve, noted once |
| the carousel's motion may degrade on a board | measured frame cost (§12), recorded in #66, never theme data |
| the Guition P4 rotates a bar strip when its pixels moved, side bars included since their on-glass check | its scan-out is portrait and the desk landscape (`native/moy_glass/moy_present_rot.c`); narrow rotated blocks have wedged the PPA (§14) |

**The resolution rule, per element.** A widget outside the WM's catalogue
for the current mode, without a value on this board, or not fitting its bar,
is dropped from its slot and noted; the bar stays; then §9's rule runs on
what is left. A value the key does not take resolves up the inheritance
chain and ends at the seed. Only §9's rule and a structural failure replace
the whole layout. The same rule applies to the user's pick (§11): a
`system.json` copied from a `--windowed` host onto a T-Deck resolves its
layout the T-Deck's way. Every rule is exercised on every golden config
with one fixture layout that triggers each drop.

## 11. The user override (D5)

**One row.** `layout_theme` in `ws.system` holds the name of the package —
a seed, or an installed theme once phase 6 brings installs — whose resolved
`layout` is used in place of the active theme's. Absent means "the active
theme's". The reader is strict: a name that is not installed reads as absent
(`None`); there is no legacy shape, so the store rule costs nothing
(CLAUDE.md, 2026-09-07). `set_theme` writes `theme` and `theme_variant` and
nothing else, which is why the pick survives a switch: it is stored beside
the theme, not in it. A layout the user edits in phase 6's Appearance editor
is saved as a user package and picked the same way, so "keep my own" and
"pick one" are one row.

**Precedence, in one function.** `Appearance.layout()` (`runtime/appearance.py`,
`ws.look`) resolves the picked package's `bars` if the row is set and
resolves, else the active theme's, else the default; then §10's per-element
rule and §9's rule for the current mode. It runs at boot's `load_system`
cascade, at the end of `set_theme`, at the Settings write, and at a mode
switch (the catalogue changes), and on a change marks `ws._dirty` and runs
`_relayout()` (§14). It is never read per frame.

**Settings.** One row after FONT SIZE, `LAYOUT: THEME / <package names>`,
stepping through the seeds and installed themes whose layout differs from
the active theme's; THEME deletes the row. The row is absent while no
package's layout differs and none is picked, so a console with only the
seeds' one layout shows no row.

Rejected: scalar rows per lever (a package name covers the list in one
strict read), and storing the pick inside a user theme that `inherits` the
active one (a switch would rewrite its `inherits`).

## 12. The home (D7), console mode's root

**One component, one place.** The home is console mode's root; desktop mode
has no home of its own — its root is the desk, and PLAY enters console mode
(§3). So there is one home component on every board, drawn by one layer, in
an **arrangement** the layout's `home` key picks (L14, owner 2026-10-14): the
`carousel` (the default, below) or the `grid` -- the shelf the picker draws,
its old two-row card grid on a panel, with the same tab row (in the panel's
header), the same sources, taps and bar-zone verbs, and no Make tile (a tall
"+ New" heads the Make tab, as on the picker). A small icon-only grid
(`icons`, Nintendo-style) is planned, not built. The carousel:
`LauncherHomeLayer` becomes it, the `Launcher` shelf engine under a **tab
row** (`ui.tab_row`, a toolkit kind, drawn through C) with four sources. One
class, four lists; the pinned Make tile goes, its job is the Make tab. The
mockups (`docs/media/theming_2026-09/README.md`: the look, not finished
layouts; L0: not set in stone) are at
`docs/media/theming_2026-09/home_dark_480x320.png` and the 320×240 pair.

| tab | source | tap (console mode, every board) | verbs in `zone` |
|---|---|---|---|
| Games | the installed carts that are games — the seeds and the store's — and not the kid's own | run, fullscreen | PLAY, CHANGE (an editable one), SEARCH (the launcher's search row, `tests/test_launcher_search_favorites.py`) |
| Mine | the kid's own carts — `ws.picker`'s list | run | PLAY, CHANGE |
| Apps | the registered apps (`ws._apps`, minus `HIDDEN_APPS`) | the app fullscreen, pushed | OPEN |
| Make | `ws.picker`'s list with its "+ New" first (owner, 2026-10-13) | the Editor fullscreen, pushed | NEW, CHANGE |

Games and Mine do not overlap: a cart the kid made or took to make their own
is Mine's, not Games'. Mine and Make hold the same carts under the two
personas of `docs/shell_ux_v1.md` §4 — a tap on Mine plays, a tap on Make
edits (owner, 2026-10-13) — which narrows that section's "a launcher tap
always RUNS" to the three tabs that launch (§16). The picker instance stays
as the desk's Make window content in desktop mode. The windowed tier's
separate games-only Library is gone: the Games tab excludes apps and the
Apps tab holds them, on every board.

**Navigation.** Touch: tap a tab, drag or fling the carousel, tap the centre
card or a `zone` chip. T-Deck: left/right move the carousel by a card with
the drag's glide, run through the same `flinging` path as a touch fling
(`_drag_partial`), so a held key is one continuous motion; up moves focus to
the tab row, where left/right switch tabs and down or enter returns; the
trackball is the same four directions.

**Damage first.** The fullscreen home's drag partial hands its band rect to
the compositor's `note_damage` (`FullscreenStackWM.hand_content_damage`,
nothing handed while an overlay, the cursor or an owed LOADING pill also
drew), so a moving home frame on the Guition P4 rotates the band, not the
whole buffer (§13 step 2; #66's shelf-drag rows). It is the carousel's
floor: the carousel's band hands the same way.

**The carousel, animated** (owner, 2026-10-13: the centre card is larger and
grows and shrinks as cards pass through the centre, in motion as well as at
rest). The rule that keeps it inside the cost model: **a card's scale is a
function of its distance from the centre and of nothing else** — `1.0` at one
card pitch or more, rising to the centre scale at distance 0 — so there is no
animation clock, a settled row is a still row, and "settled" is the kinetic
scroll's own condition (#113). The frame in motion:

- **`sel` leaves the band's key while the row moves** — `Launcher.sel` bumps
  the zone generation, so every card crossing would re-render the bar; `sel`
  and the zone update once, on settle;
- the cards lie on the home's **wallpaper**: the band has no backdrop of its
  own (owner, 2026-10-14: "why is the background white? instead of showing
  the wallpaper"), so its pixels cannot shift as #113's scroll-as-blit does
  — the wallpaper would slide with the cards. Every full paint copies the
  wallpaper under the band (`LauncherHomeLayer._band_bg`, an owned layer the
  fit check releases with the retained frame), and a moving frame restores
  it only around the cards, every card painting its whole square opaque:
  each card one scaled blit of its RGB565 cover bake at its scale, the
  outline frame and the name plate on the centre one. Covers are RGB565
  bakes and `sspr` scales index sheets, so this is **a nearest-neighbour
  RGB565 scaled-blit kernel** in `moy_gfx`, benched on all four boards
  before the home committed (§13 step 7a). The band's rect is the frame's
  damage.

What it costs per moving frame is the wallpaper restored around the cards
and their scaled blits; what it costs per settled frame is nothing. Step 7
measures it on every board (§13) against #66's per-cart fps as the control.
**If a board's drag frames do not fit its budget it degrades**, by this order
and only as far as the measurement says: (1) the centre scale is reduced;
(2) every card is at scale 1.0 while moving and the centre card scales up
only once settled; (3) one card size with the frame and the plate. The
verdict is a per-board constant on the WM (`HOME_MOTION`, `HOME_SCALE`),
recorded in #66, never a theme key.

**The wallpaper slot.** The home draws the `home` slot's wallpaper
(`docs/theming_2026-09.md` §4.9); the desk draws `desk`; a console-only
board never reads `desk`; a parked desk's live wallpaper does not animate
(§3). Readability — `home_safe` or a scrim — is theming phase 6's.

**Counts and empties.** The bar's `title` on the home is the active tab's
count. An empty Mine or Make shows one "+ New" card.

## 13. Phases, as steps

Each step is one commit under the common gate of `docs/theming_2026-09.md`
§9: its test, docs and preflight commands, `tools/board.py pass tdeck p4`
with the headroom each build prints, and zero captured frames in every idle
scenario — plus the gate in its row. **The performance bar, per scenario**:
`tools/p4_bench.py`'s median and p90 as a DELTA against the number the step
started from (its two thresholds apply to the delta), and an ABSOLUTE p90 the
row names where a scenario is new or changes meaning; every number goes to
#66 (frames) or #58 (the P4 port). Scenarios this phase adds to the bench:
a fling and the frames until idle; several short flicks; the T-Deck's
held-key glide; a tab switch; PLAY and the X as single-frame maxima with
three windows parked and with a released stamp; a popover open, navigate
and close over windows and over a playtest; roster play fps (`tools/p4_perf.py`)
under a multi-bar layout on the Guition P4; a live home or desk wallpaper;
a frame on a minute boundary. Every step corrects, in place, the sentences
it falsifies (§16). Goldens are byte-identical on every existing config in
every step; the steps that ADD a config say which.

| step | lands | gate beyond the common one |
|---|---|---|
| 1 — console mode as state | `wm.mode`, `_parked`, `enter_console`/`leave_console`, the route's restore op; every branch of §3 reads the mode; PLAY ends a playtest and parks the rest; the fit-check stamp release; `go_home` split by mode; the HOME key and `show_desk()` (L11) | `tools/mem_census.py p4 offheap` before the commit, to #58; NEW config `p4_1024x600_cs2_fs1_console` (a P4 in console mode, equal to the T-Deck row's layout at the P4's size); `tests/test_wm_windowed.py` covers enter, exit with windows restored unrendered, exit with a released stamp re-rendered once, a parked kind reopened fullscreen and found in place on return, the playtest ended, and each branch of §3 by name; `tests/test_wm_stack_memo.py` holds; `library drag-scroll` re-pinned here (its meaning moves: the home is now above a parked desk) with the before number beside it; PLAY and X single-frame maxima recorded; the P4's on-glass suite drives PLAY and the X; the T-Deck row unchanged |
| 2 — the Guition P4's strips and the home's damage (landed 2026-10-13) | the compositor's one boot-time strip becomes a rect LIST in `native/moy_glass` set on relayout, each rotated only when its strip's generation moved; the fullscreen home hands its band to `note_damage` | the Guition P4's on-glass suite; its shelf-drag and play rows re-measured, to #66 and #58 (a cut from today, since an unchanged strip no longer rotates per play frame); `MOY_ROT_RECTS` budgeted: game, damage, up to four bars, coalesced, with a test that five rects never exceed it |
| 3 — the content rectangle (landed 2026-10-13) | `cx, cy, cw, ch` in the surface row; `content_rect()` published (Python and the role table); `bar_h()` derived; `LayoutBase` and `ListShellLayout` read it; `bar_rect(i)` the one author for the responsive surfaces' draw and tap tests | all golden configs identical; `tests/test_app_context.py` covers the verb on both writers; `tests/test_top_bar.py` holds the tap slices |
| 3b — the fixed geometry (landed 2026-10-13) | the game-canvas tool bar, `editor_app.py`, `_POPUP_Y`, `WindowedWM._bar_h()`'s four readers (now `_desk_rect()`) and the desk icon column move onto `bar_rect`; every remaining `bar_h` reader in `runtime/` grepped and moved | every golden row identical (the fixed geometry moved without a pixel); the P4 and T-Deck suites |
| 4 — the format, the loader, the resolver (landed 2026-10-13) | the loader reads `layout.bars` (C, additive, fuzzed; §5's caps, §9's rule twice); `LAYOUT_CATALOGUE` on both WMs; the composer and its drops per mode; the seeds carry example 1 with `close` as a widget; `layout_theme` and `look.layout()`; the Settings row | goldens identical; a test picks a layout, switches themes twice and reads it back; a test reads an uninstalled name as absent; the fixture layouts of §10 drop as specified on every config and the rule's second run catches a vertical-only `zone` |
| 5 — bars on any edge, slots, cells (landed 2026-10-14) | strips per bar at their rects, whole-strip re-render on a key change (§14); slot packing; the new widgets (`chips`, `text`, `spacer`, `separator`); the drag clamp to the content rect; the strip rect list fed from the bars on the Guition P4 | NEW golden configs from examples 2, 3, 4 and 6 (`p4_1024x600_cs2_fs1_win95bar`, `..._gnomebar`, `..._twobars`, `tdeck_320x240_fs1_bottombar`), with renders for the owner; existing configs identical; idle zero under each; the T-Deck `--repaint settings,files` delta; the Guition P4's suite under example 2 and under a side bar on glass (the wedge check), its roster play fps under a two-bar layout; `tests/test_frame_alloc.py` holds |
| 6 — the popover layer and the components (landed 2026-10-14) | the `sysmenu` layer becomes the popover layer on the quiet stack with its rect as damage; the calendar, wifi and power components in their two containers; the wifi lease released on every close; **`system_carts/moybyte.wifi.moy` deleted** and `console.launch_wifi_tool` re-routed — console mode opens the Settings page scrolled to the WIFI panel, desktop mode the panel as a popover — so the bar's wifi tap, the launcher store and the Player no longer know the cart | NEW configs: `p4_1024x600_cs2_fs1_popovers` (each widget's popover over the desk), `tdeck_320x240_fs1_widget_pages` (console mode's page for each); `tests/test_wm_windowed.py` covers open, close-outside, close-on-B, one-at-a-time and the lease release; `tests/test_desktop_shell.py` covers the fullscreen container's push and pop; `tests/test_top_bar.py`'s wifi-tap test asserts the Settings route on both tiers; the popover scenarios over windows and over a playtest, with the system menu's before numbers beside them; `app idle` zero with a popover open; the T-Deck's on-glass suite reaches the WIFI panel from the bar |
| 7a — the scaled-blit kernel (landed 2026-10-14) | the nearest-neighbour RGB565 scaled blit in `moy_gfx`, host and boards | a micro-bench of the focus band's three blits on all four boards, to #66, before 7b starts; the board module answers the host's pixels |
| 7b — the home | the tab row and four sources; the carousel with the animated centre card; `sel` out of the moving shift key; the key glide as a fling; the Make tile and the Library exclusion go; the per-board motion verdict | ONE named re-baseline of the home rows on every config, with before/after renders; `library idle` zero; the fling, flick, glide and tab-switch scenarios on every board with absolute p90s named per board; the Guition P4 with the band damage of step 2; `tests/test_launcher_scroll.py`, `test_home_retained_frame.py`, `test_launcher_search_favorites.py` hold; `tests/test_desktop_shell.py` gains PLAY reaching the tabbed home in console mode |
| phase end | — | a board pass on every attached board (`tools/board.py pass tdeck guition_s3 p4 guition_p4`), the web through `tools/preflight.sh --web`; a board this desk lacks is recorded as not run |

The order: 1 makes the mode real before anything lays out against it; 2 is
the Guition P4's biggest lever and the carousel's floor; 3 and 4 move no
pixel; 5 and 6 add rows; 7 is the one restyle, with its kernel benched
first, taken last so the new home is captured under the bar layouts and in
both modes in one re-baseline.

## 14. Kernel mapping, caches and cost

| piece | side | why |
|---|---|---|
| both WMs and the mode state, the bar layer (a list of bars), the popover layer, the home layer, Settings rows, the resolver, slot packing (arithmetic once per relayout, cached on the layout object) | **Python** | `docs/native_kernel_2026-09.md` §10 question 8: sprint 7 does not run; the WMs are policy, and this doc adds policy, not kernels |
| the widget painters, the band, the tab row, the chips, the pill, the frames, the calendar grid | **C, existing and extended in place** | `moy_chrome_strip_band`, `chrome_strip_right` and the `ui` kinds are C already; a widget painter is one more op of the toolkit's sink, the calendar a grid of `ui.cell`s |
| the RGB565 scaled blit | **C, a kernel** in `moy_gfx`: `blit565_resize` | one call per card, any ratio; benched on all four boards before the home uses it (step 7a, #66) |
| the compositor's strip rect list | **C, `native/moy_glass`** | a list set on relayout, each strip rotated only when it moved, is the Guition P4's lever (§13 step 2); step 5 feeds it one strip a bar |
| the route's restore op | **C, `native/moy_spine`** | the back-stack is the kernel's; parked kinds come back without re-opening |
| the surface row's content rect, the loader's layout bytes | **C, additive** | the row is `native/moy_app`'s and the package reader is `native/moy_ui`'s; no new module, no new handle kind |

**Caches, and what each folds.** A layout change, and a mode switch, is a
Class A dirty plus a relayout — the path `set_font_scale` takes today
(`runtime/appearance.py`: relayout the eight layout heads, every registered
app, the WM's re-anchor hook) — with the mode switch relayouting bars and the
root context only. Nothing here invents an invalidation mechanism.

| cache | keyed on | on a change |
|---|---|---|
| a bar's strip (`bar_layer._bar_strips`, per bar, `where` and canvas) | the look, the bar's rect, and the value of every widget it holds (the minute, the wifi kind, the zone generation; the depth for `close` moves with a navigation's invalidate) — a bar without the clock keeps its strip on the minute | the strip is re-rendered WHOLE and blitted, as today; a per-cell repaint was reviewed and declined — the whole strip is one C paint once a minute for the clock, inside the frame-allocation rule `tests/test_frame_alloc.py` holds; idle frames paint nothing |
| the home statics and retained stamp (`_statics_key`: size, fs, theme) | plus the content rect and the active tab | one re-stamp; a tab switch is a key change; in motion §12's bands paint, at rest the stamp holds |
| the home's `_lib_cache` | the cart list and cover generation | lives across mode switches; released by the fit-check path after the parked stamps (§3) |
| the taskbar chips (`wm_chrome._chip_sig`) | the chip signature, in the `chips` cell | painted by the WM over the strip, frozen on quiet frames like the window chrome; a change re-paints them and tells the compositor the bar's rect moved |
| the desk backdrop drag cache (`wm_desk._BackdropLayer`) | captured per gesture from the live desk | nothing to key: the next gesture captures the relayouted desk |
| a parked window's content stamp (`win.buf`) | the window's content, as today | kept across console mode; released oldest-first when the Player's fit check fails, re-rendered once on return |
| `wm_chrome`'s frozen chrome and `_content_static` (`docs/surface_model_v1.md` §14.1) | theme name, variant, window rects | the re-anchor moves the rects, the freeze re-arms on the next quiet frame |
| the popover | its component's own rows | open, navigate and close are Class A dirties with the popover's rect as damage; closed, it draws nothing and holds nothing |
| the Guition P4's strip rects | one generation per bar | a bar rotates only when its generation moved; a game frame with still bars rotates the game rect alone |

Theming phase 6 folds `look_gen` into every one of these keys
(`docs/theming_2026-09.md` §6); the keys added here are geometry and values,
which is what those keys already hold.

**What is measured, per step, and where it goes.** A painted frame pays one
flat strip blit per bar (up to four) instead of one; a left or right bar is a
column blit, a per-row copy rather than a band, and the T-Deck's and the
Waveshare's `--repaint` deltas in step 5 say what that costs. On the Guition
P4 a bar strip is a rect the compositor rotates when its generation moved;
a narrow rotated block has wedged the PPA before, so a side bar was placed on
that board after step 5's on-glass check (2026-10-14: a 40-column left strip
handed over as moved on every play frame for some nine hundred frames, no
wedge; #58 has the run), and `MOY_ROT_RECTS` (8) is
budgeted as game + damage + four bars with coalescing, tested. The mode
switch's two spikes — entry (the world release's collect, the strip layer's
pre-collect, cover decodes, the relayout) and exit (the desk re-render, the
stamps, one full frame) — are gated as single-frame maxima in step 1. Idle
stays zero under every layout and in both modes: `tools/p4_bench.py`'s
`desk idle`, `library idle`, `settings idle` and `app idle` capture no frame,
and `ws.note_cost`'s `bar.strip.render` count over an idle minute is one
(the clock's) (`runtime/console_perf.py`). The carousel's motion cost is
§12's and goes to #66; parked windows' memory and the off-heap census are
#58's.

## 15. Declined

- **Composable layouts, widget carts, a widget catalogue a theme extends**:
  the owner's scope is simple (2026-10-13); anything livelier than a bar of
  built-ins is a wallpaper cart's job (`docs/theming_2026-09.md` §4.9).
- **`windows: maximized`, and any `windows` key** (owner, 2026-10-13): the
  per-window maximize button already exists, and a mode that pre-presses it
  is a second way to the same state (L0). D5's "window mode" is answered by
  the button (§16).
- **A boot-into-console setting, or a remembered last mode** (owner,
  2026-10-13): not for now; PLAY is the only way into console mode on a P4.
- **A `mode` key, a DESKTOP row in the system menu, a `settings` widget, a
  HOME row, a calendar app or desk icon**: each is a second way to a thing
  that has one — PLAY and the X, the menu's Settings row, the HOME key, the
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
- **Desktop mode on the T-Deck and the Guition S3**: §3's hardware reasons.
- **Popovers in console mode** (owner, 2026-10-13): components open
  fullscreen there, on every board in that mode.
- **Refusing PLAY while a playtest runs**: it is ended instead (owner,
  2026-10-13).
- **Bars for the OS surfaces only, a running app keeping a top strip**
  (owner, 2026-10-13): global bars save the most space, and two bar
  positions on one console is the duplication L0 forbids.
- **The `moybyte.wifi.moy` tool cart as console mode's wifi container**
  (owner, 2026-10-13): the Settings panel is the one implementation; the
  line between system carts and built-in apps is not settled and can move
  later (§17).
- **Pixel-positioned widgets, scripts in a layout, more than one bar per
  edge, auto-hide, `size` under 16**: `docs/theming_2026-09.md` §4.2's rule,
  §7's "data only", and #203's tap floor.
- **Rotated text in a vertical bar**: `title`, `text` and `zone` need a
  horizontal bar; the clock's vertical form is two lines, not a rotation.
- **Configurable popover contents, a popover a theme adds**: fixed per
  widget, four of them.
- **Large bars and docked widgets** (owner, 2026-10-14; L13): a `calendar`
  widget docking the month grid in a side bar, and any bar thicker than the
  compact widgets need. It was built (step 6's example 5, a 120-row right
  bar) and removed the same day: a widget is an icon or a short value, and
  what is bigger opens as its popover or its page -- one way to the
  calendar, not two. `size` is capped at 32 (§5).

## 16. Sentences this doc will make false

The step named in the last column corrects the claim where it stands:

| claim | where | step |
|---|---|---|
| "HOME-goes-all-the-way-home" | `runtime/console.py`'s `_leave_or_home` docstring | step 1 (L11) |
| "Leaving the desk closes its windows"; "the PLAY icon drops to the fullscreen Library"; the fullscreen branches as "the desk is not in the stack"; the desk bar "draws no X (nothing is beneath it)" as the home's rule on a P4 | `runtime/console.py`'s `open_library`, `open_desk` and `windowed_chrome` docstrings, `runtime/wm.py`'s header, `runtime/wm_windowed.py`'s header, `runtime/wm_desk.py`, `.claude/rules/shell.md`'s "windows exist ONLY above the desk kind", `docs/shell_ux_v1.md` §3's two-worlds paragraph and §9's desk row | step 1 |
| the compositor rotates "the top bar's strip" set once at boot | `firmware/guition_jc8012p4a1c/README.md`, `device/desktop_spine.py`, `native/moy_glass/moy_present_rot.c`'s comments | step 2 |
| `bar_h()` as the whole bar contract; "the top `layout.bar_h` rows" | `docs/app_api_v1.md`, `runtime/system_api.py` | step 3 |
| `BarLayer` "the unified 18px top bar + bottom dock" (the dock half is already false) | `runtime/README.md`'s `bar_layer.py` row | step 3 (the dock words), step 5 (one top bar) |
| the band check reads the app layout's `bar_h` | `runtime/console.py`'s band docstring | step 3 |
| `layout` "has no defined keys yet"; `layout` in `theme.reserved` | `docs/theming_2026-09.md` §3, `native/moy_ui/style.json` | step 4 |
| the X as part of the right zone (`show_x` in `_render_right_zone`; "the right zone" of the exit model) | `runtime/bar_layer.py`, `docs/shell_ux_v1.md` §9 | step 4 (the widget), step 5 (the drawing) |
| "the unified 18px top bar"; "every OS control lives on ONE side" | `runtime/bar_layer.py`'s header and map, `runtime/chrome.py`'s right-zone comment | step 5 |
| "a minimal left-anchored dropdown … the primitive the top-bar system menu is built on"; an overlay disables the quiet stack | `runtime/widgets.py`'s `Popup` comment, `runtime/layers.py`'s `sysmenu` layer, `runtime/wm_windowed.py`'s quiet-frame comments | step 6 |
| "the standalone moybyte.wifi.moy tool cart remains the launcher-grid path"; "on the fullscreen tiers it launches the moybyte.wifi.moy tool"; `launch_wifi_tool`'s "find the moybyte.wifi.moy tool in the launcher store, SELECT it, and RUN it"; the cart in the seed set and the lists that name it | `docs/shell_ux_v1.md` §10, `runtime/bar_layer.py`'s wifi-tap comment, `runtime/console.py`'s `launch_wifi_tool` docstring, `runtime/moy_carts.py`, `runtime/player.py`, `tests/test_top_bar.py`, `tests/test_wm_windowed.py`, `system_carts/` | step 6 |
| "the LIBRARY is the full-screen launch surface"; the games-only Library and "system-app carts leave its shelf"; "the pinned Make tile"; covers at an integer scale; "a launcher tap always RUNS" (true of three tabs, not Make) | `runtime/wm_windowed.py`, `runtime/README.md` rows for `wm_windowed.py` and `launcher_layer.py`, `runtime/launcher_layer.py`, `docs/shell_ux_v1.md` §4 | step 7b |
| D5's "a theme sets the default … window mode" | `docs/theming_2026-09.md` §1 | this doc: the window mode is the per-window button, and D5's layout half is the bars (corrected there by step 4) |

## 17. The owner's answers (2026-10-13)

| question | ruling | applied in |
|---|---|---|
| the Make tab | the picker's list, a tap edits | §12 |
| the carousel's centre card | larger, and animated through the centre in motion, not only at rest | §12, L9 |
| the default seed's bars | today's one bar | §5, example 1 |
| the desk icon column | kept beside the home | §3 |
| `windows: maximized` | dropped, with the `windows` key | §15 |
| the Guition S3 | console mode only, with the T-Deck | §3, §10 |
| the battery component | Settings' level, VOLUME and BRIGHTNESS rows, drawn by Settings | §4, §8 |
| the modes | console and desktop; PLAY enters console mode on a P4 | §3 |
| a boot-into-console setting | not for now; PLAY is the only way in | §3, §15 |
| the way back from console mode | the existing X, now the `close` widget | §3, §4 |
| a running windowed playtest when PLAY is pressed | ended; the simplest to build | §3 |
| wifi: one implementation | the Settings WIFI panel stays and `moybyte.wifi.moy` is deleted (step 6). The cart was one of the first "the OS is carts" carts; where the line between system carts and built-in apps sits is not settled and can move later — today one implementation wins | §8, §13 step 6, §16 |
| the docked calendar (2026-10-14) | "I don't want bars, I only want the calendar to pop out": `calendar` leaves the catalogue; the calendar is the clock's popover and page only | L13, §4, §8, §15 |
| the home's arrangements (2026-10-14) | carousel (default), the old big grid, later a small icon-only grid; "we don't have to do it now": `home: carousel | grid` built, `icons` planned | L14, §5, §12 |
| giant bars (2026-10-14) | "that should be the default for every widget": every widget is compact, anything bigger opens; `size` capped at 32 | L13, §5, §15 |
| how far the bars reach | global: every surface, the running tool/app included, lays out off `content_rect()`, migrated in step 3b; it saves the most space | §7, §13 step 3b, §15 |

Nothing is pending the owner.

## 18. Review ledger (2026-10-13)

The two passes on the previous revision: **A**, the architecture review, and
**P**, the performance and hardware review. Each finding, its ruling, and
where it landed; a finding both made is marked.

| # | finding | ruling | where |
|---|---|---|---|
| A, P | console mode as "push the home above the desk" contradicts the kernel route's truncation and a dozen stack-shape branches | accepted: the mode is explicit WM state, parked records outside the stack, a route restore op, every branch named and tested | §3, §13 step 1 |
| A, P | the Guition P4 rotates one boot-time strip; bars need a rect list, rotated per generation, within `MOY_ROT_RECTS`, with a wedge check for narrow blocks | accepted | §2, §13 step 2 and 5, §14 |
| A, P | per-widget cell repaint: the strip is re-rendered whole today and `redraw_clock` paints the backdrop | accepted: whole-strip re-render stays, cell repaint declined | L8, §14, §15 |
| A, P | overlays disable the quiet stack and damage | accepted: the popover rides the quiet stack with its rect as damage; the menu takes the same path | §8, §13 step 6 |
| A, P | `--repaint home` does not exist; `library drag-scroll` changes meaning; gates need deltas plus absolutes and the missing scenarios | accepted | §13 |
| A | the X must be its own widget in the required set; the rule must re-run after drops | accepted: `close`; the rule runs twice | §4, §9 |
| A | the desktop `home` widget needs a show-desktop verb; `goto("desk")` truncates | accepted: `show_desk()`; the owner then dropped the widget and gave the verb to the HOME key (2026-10-13) | L11, L12, §15 |
| A | wifi exists twice; the lease must release on an outside tap; the battery rows are Settings' | accepted; the owner ruled the Settings panel the one implementation | §8, §13 step 6, §17 |
| A | `content_rect` reaches fixed geometry the doc missed; scope is a product choice | accepted: the owner ruled bars global; step 3b moves the fixed geometry | §7, §13, §17 |
| A | Make's "tap edits" contradicts shell_ux §4 and D5's window mode; Games ⊇ Mine | accepted: both listed in §16; the tabs defined without overlap | §12, §16 |
| A | phase 5 leans on theming phase 6; stamp release needs a real trigger | accepted: seed-only until phase 6; the Player's fit check against the largest free block, no retry hook | §3, §9 |
| P | the fullscreen home hands no damage on the Guition P4 — the biggest lever | accepted, as a step before the carousel | §12, §13 step 2 |
| P | `sspr` scales index sheets, covers are RGB565: a new kernel, benched first; `sel` in the shift key; the key glide as a fling; the focus band is most of a small row | accepted | §12, §13 step 7a |
| P | the mode switch's spikes; the Waveshare's off-heap unmeasured; `_lib_cache` lifetime | accepted: single-frame maxima gated; `mem_census` before step 1; the cache's lifetime stated | §3, §13 step 1, §14 |
