# Maximise and resize: three readings of maximise, and the panes sized by hand (2026-10-21)

The owner asked (2026-10-21) for two alternatives to today's maximise, drawn
so a reviewer can set them beside it. **E-05, tools in the title strip**, is
`EDITOR_LAYOUT.md`'s frame and its owner call 4; **V1** moves the tools into
the shell bar; **V2** makes a maximised app full screen. Nothing here is
decided, and the keys in the tables are proposals, the same in all three.
The last part, **Resize**, is the owner's chosen way to size the panes, and
the places it meets `docs/studio_2026-09.md`. `rounds/round14.py` draws
every picture (`draw.py`), and `editor_maximise_compare.png` stacks the
three Art workspaces.

| | top of the glass | panes, 1024 × 600 | panes, 800 × 480 | gained |
|---|---|---|---|---|
| E-05 | the bar (40) and the window's strip (44) | y 90–594, 504 tall | y 90–474, 384 | — |
| V1 | the bar (40), carrying the app's tools | y 46–594, 548 | y 46–474, 428 | 44 px |
| V2 | the app's row (40); the bar on demand | y 46–594, 548 | y 46–474, 428 | 44 px, and the bar's status and chips leave the glass |

Pictures, per variant `<v>` (`e05`, `v1`, `v2`):
`moy-light_desktop_editor-max-<v>-{art,code,art-800x480}.png`,
`moy-dark_desktop_editor-max-<v>-code.png` and
`moy-light_desktop_files-max-<v>.png`; V1 adds `-max-v1-windows`, V2
`-max-v2-revealed` and `-max-v2-touch`. The E-05 pictures are round 12's,
re-rendered under these names.

## Where the 44 px go

The pane widths, gutters and tool sizes stay `EDITOR_LAYOUT.md`'s; every
zoom stays whole. V1 and V2 give the same height, so their panes are
identical.

- **Art, 1024.** The Sprite pane gains a preview row under its flags: the
  sprite at 1×, 2× and 4×, as the game draws it. The canvas stays 288 (8 × 8
  at 36): the next box whole for 8, 16 and 24 is 336, 48 px more than there
  is, and with its palette 444 px wide, which would take the Sheet's two
  pages off the right column. The Map · Game pane goes 336 → 380, its view
  288 → 332 at 2× (18 → 20¾ rows), the camera clamped so the map fills it.
  The Sheet stays 162: both pages at 2× are 128.
- **Art, 800.** The Sprite pane widens 306 → 330 and its canvas goes a whole
  step up, 8 × 8 at 24 → 30 (192 → 240; 16 × 16 at 15, 24 × 24 at 10), its
  palette and flags with it. The Map · Game pane narrows 476 → 452: its view
  464 × 204 → 440 × 248 (29 × 12¾ → 27½ × 15½ tiles).
- **Code, 1024.** Code shows 34 lines, not 31. The Run slot stays 282 (the
  game at 1×; 2× needs 650 px across). The Reference goes 216 → 260: under
  the sheet page, the pick line, what Enter puts at the caret
  (`spr(6, x, y)`).
- **Files.** The list shows 18 rows, not 17 (28 px rows; 16 px stay as its
  foot). In all three, the current row's verbs (Open, the primary, and Play)
  and search ride in the app's row, so the verbs' band at the foot of a
  restored Files is not drawn.

## E-05: tools in the title strip

As `EDITOR_LAYOUT.md` draws it: the bar keeps Moy, the window chips and the
status; the window's 44 px strip carries the title slot, the app's row,
undo, redo, the 170 px Play slot (labelled), and minimise, restore and
close.

| | restore | close | switch window |
|---|---|---|---|
| pointer | the strip's restore; double-click its bare 120 px; drag it down | the strip's × | the bar's chips |
| touch | the same, by tap, double tap and drag | the strip's × | the bar's chips |
| keys | Super+↓ | Super+Q | Super+Tab |

## V1: the bar takes the tools

A maximised window has no strip. The bar, left to right:

- **Moy** (36 × 34), the system menu, as everywhere.
- **The app's chip** at x 52, in the fixed title slot (144 at 1024 with
  the app's icon; 112 at 800, the name alone), 32 tall, on the current
  plate, with ▾. It opens the **window menu**
  (`moy-light_desktop_editor-max-v1-windows`): Restore, Minimise, Close
  with their keys; the other windows; Show the desk. A double tap on it
  restores, as a title's double tap does.
- **The app's tools** from the chip: the workspaces (labelled at 1024 on
  6 px pill pads; icons with the current one labelled at 800), undo and redo
  in 32 px cells, a hairline, and a 64 px Play slot of bare glyphs in
  `value` (Play; Restart and Stop), the bar's Play rule (`DESIGN.md`). The
  slot ends 120 px before the status, so undo never moves.
- **A bare stretch** of at least 120 px: a double tap restores, a drag down
  restores and moves the window, as on a title strip.
- **The status**: Wi-Fi, battery, clock.

**The window list** leaves the bar while a maximised window is focused: the
chip's menu lists the other windows, and Super+Tab cycles them. When a
restored window takes focus, the bar is the chips row again, and the
maximised window behind shows no strip (its panes still start at y 46).

| | restore | close | switch window |
|---|---|---|---|
| pointer | the chip's menu; double-click the chip or the bare stretch; drag the stretch down | the chip's menu | the chip's menu |
| touch | the same, by tap, double tap and drag | the chip's menu | the chip's menu |
| keys | Super+↓ | Super+Q | Super+Tab |

**At 800**, the chip drops the app's icon, the workspaces are icons with the
current one labelled, and everything else is as at 1024.

## V2: full screen

No bar and no window chrome. The app's own **row** is the top 40 px: the
icon and the name in the title slot, the workspaces, undo, redo, the 170 px
Play slot (labelled, as on the strip), then **leave full screen** (four
corners pulled in) and close, no minimise. The row is the bar's height, so
the bar, brought down, covers it exactly and never a pane.

**The bar comes back on demand**, over the row, with a shadow, and carries
Moy, the window chips (the full-screen app's current), **Exit full screen**
and ×, and the status:

- **pointer**: pushed against the top edge and held (a dwell, 400 ms, so
  a pass to a tool does not reveal it; the row's tools start at y 4)
  (`-max-v2-revealed`); it hides when the pointer leaves it.
- **touch**: a swipe down that starts in the top 8 px; the bar follows the
  finger and settles past half (`-max-v2-touch`); a tap below it hides it.
  A tap on the row is always a tool.
- **keys**: Super brings the bar with the system menu open under Moy (the
  menu hangs from Moy); Super+Tab brings it with the next window's chip
  focused. Esc closes the menu and the bar with it, and nothing else.
- **pad**: Start, the console key, as Super.

| | leave full screen | close | switch window |
|---|---|---|---|
| pointer | the row's control; the revealed bar's Exit full screen | the row's ×; the bar's × | the revealed bar's chips |
| touch | the same, by tap | the same | the same |
| keys | F11 (toggles); Super+↓ | Super+Q | Super+Tab |

**Esc never leaves full screen**: games and the Run slot take it, and Code
closes its popovers with it. Leaving full screen restores the window to its
place on the desk.

**At 800** the row is the same: icons with the current workspace labelled.

## Resize: free dividers, whole-zoom content

The owner asked (2026-10-21) for panes the user can size, to suit a screen
and a taste, and chose this one of three ways (snapping dividers and size
presets were not drawn). It follows `docs/studio_2026-09.md` §4.2–4.4 and
S5–S7. Pictures, on E-05's frame, Moy Day:
`moy-light_desktop_editor-resize-{art,code,code-drag,art-min,art-800x480}.png`.

- **The five workspaces are the starting layouts.** Each opens as
  `EDITOR_LAYOUT.md` draws it; the user then drags its dividers. Panels do
  not move between panes and panes do not split.
- **A divider moves to any pixel.** Each panel draws at the largest whole
  zoom its pane allows, and what is left is the pane's own ground, the
  content centred in it: it reads as a card with room, not a gap. The
  Sprite pane names its zoom at its foot ("8×8 at 38, the largest whole
  zoom here", `-resize-art`), the Run pane in its header ("1×",
  `-resize-code`). A bigger pane only gives more canvas, more columns, more
  map (§4.2).
- **Minimums are the T-Deck's** (S5, §4.2): Run, the game at 1× (320 × 240)
  under its header; Sprites, Sound, Map and Scene, 320 × 240, the T-Deck's
  page under the pane's 26 px tab bar. Code, Make it mine, Files, Blocks,
  and the panes these pictures add (the Sheet, the Reference, Actor and
  Stamps, the sounds list, the Song, the block palette) scroll and have
  none.
- **A divider stops at its neighbour's minimum**, and the pane it stopped
  at wears a `value` ring while the drag holds, with a tag that says why
  (`-resize-code-drag`: "Run stops at its minimum: the game at 1×, 320
  wide").
- **Chrome scale** (S6, §4.3): a panel draws at the board's chrome scale
  once its desk layout fits its pane; below that it draws exactly as on the
  T-Deck, at chrome scale 1 (`-resize-art-min`: the Map at 320 × 240, its
  own sheet strip and brush page). Its targets are then below the desk's
  32 px floor, which S6 accepts.
- **Touch**: a divider's handle is drawn 6 × 36 at its middle and takes a
  32 × 48 touch; the rest of the gutter answers a pointer only (the ↔
  cursor). A drag starts on the handle and moves 8 px before it takes, so a
  tap near a pane's edge never resizes; nothing inside a pane starts one.
- **Keys**: with a pane focused (F6), Ctrl+Alt+← → ↑ ↓ move its right and
  bottom dividers 8 px; a pad does not resize.
- **Reset**: a double tap or double click on a handle puts that divider
  back where the workspace starts it; Ctrl+0 resets the whole workspace.
- **Remembered** on the board, per project, per workspace and per screen
  size (S7, §4.4); never in the cart.
- **At 800 × 480** the mechanism is the same. The starting Art layout
  widens Sprite 306 → 320, its minimum, and the Map · Game pane takes 462
  (`-resize-art-800x480`, the divider dragged left and stopped there, the
  canvas 8 × 8 at 25).
- **A restored window** resizes by the grip dev already has: the
  bottom-right corner (`runtime/wm_chrome.py`'s `_grip_rect` and
  `_grip_hit_rect`), drawn 12 px, hit 24 px plus a 4 px overhang at font
  scale 1, applied on release under a rubber-band outline or live
  (`_live_resize_ok`). The Editor's window stops shrinking at its
  workspace's combined minimum (S7). As it changes size, the scrolling pane
  (Code, the Reference, Art's Map) takes or gives the difference, so the
  canvas and the game keep their zoom.
- **Desk windows** already work this way: a window sizes freely and a
  running cart composites at a whole scale, centred in it. The panes add no
  second mechanism. The grip's 28 px hit is under the desk's 32 px floor,
  and should grow to it.

### Where `docs/studio_2026-09.md` and the Workspaces design disagree

| | the studio doc | the Workspaces design | proposed |
|---|---|---|---|
| S1, S4, §4.1 | any panel in any pane; panes split; tabs dragged or held between panes; **+** opens a panel | five fixed arrangements | the workspaces are the starting layouts and their dividers move; moving panels and splitting stay out of the first version (Concept C, a later opt-in at most) |
| S2, §3 | one bar per pane; the Studio's controls at the ends of the panes' top row: the project menu left, undo, redo and Play right | the window's strip carries the workspaces, undo, redo and Play | the strip is that top row (E-05), the bar (V1) or the app's row (V2); the cart's name in the title slot opens S2's project menu; each pane keeps one header |
| S3 | the WM draws the Studio's title strip as for every window | the maximised strip carries the app's row (owner call 4) | the WM draws it in every reading; which reading is the owner's call (this file) |
| S5, §4.2 | no panel below the T-Deck's 320 wide | 800's Sprite pane is 306 | 800's Art starts at Sprite 320, Map · Game 462 |
| S6, §4.3 | a pane at its minimum is below the tap floor, accepted | every desk target ≥ 32 px | 32 px holds in every starting layout; a pane dragged to its minimum may go below it, as S6 accepts |
| S7, §4.4 | the arrangement saved per project and per screen size | the Editor remembers its last state per board (owner call 3) | both: the window's state per board; the workspace and its dividers per project and per screen size |
| S7 | the window stops shrinking at the layout's combined minimum | under 660 px of panes, or a restored window below its workspace's minimum, the Editor is the tab ladder | the window stops at the minimum; the tab ladder is for glass smaller than any workspace's minimum |
| S8 | the T-Deck's bar lists every file | the handheld bar is the seven-tab ladder | the ladder stands; how a tab picks among a cart's several files is open |
| S9, §5.5 | Blocks and Code side by side; graduation greys Blocks out | a blocks cart's Code is read only ("made by your blocks") | the read-only Code pane offers the edit that graduates, and Blocks greys out as S9 says |
| S10 | no Debug panel in the first version | the Reference has a Log tab (print and errors) | the Log is S10's console; whether it ships first is the owner's call |
| S11, §5.3 | the Map keeps its own sheet strip, never another panel's | Art's Map shares the Sheet pane | the Map's minimum counts its own strip and draws it at the T-Deck size (`-resize-art-min`); at the desk scale, with the Sheet beside it, the strip folds into the Sheet |
| S12, §6 | Play opens Run as a tab or splits a pane for it | the Run slot or a Game tab is always there | Play selects the workspace's Game tab or Run slot; nothing splits |
| S13, §6 | edits reach the game at the next Play; closing the Run tab stops it, no separate Restart and Stop | sprite, sound and flag edits reach a running game at once; Restart and Stop (owner call 1) | owner call 1, the later decision, if the owner keeps it; Stop stays because the Run slot cannot be closed |
| §4.4 | a file is open in one place | Art's Sprite pane and Sheet, and the Map, show the sheet at once | the Sheet and the Reference are pickers that never edit, so an edit still has one place |

## The handheld

The T-Deck class (320 × 240) is unchanged under all three: its apps are
full pages under one bar, and nothing maximises.

## Open questions

1. **V1, a maximised window behind a focused one** draws no strip, so its
   top edge carries no name or tools until it is focused again. A dim
   one-line band would cost back the 44 px.
2. **V1's labels at 1024** need the 6 px pill pads and the 64 px Play slot;
   a wider status (a second tray icon, a date beside the clock) drops the
   workspaces to icons.
3. **Super's chords** (Super+↓, Super+Q, Super+Tab, Super+M, Super+D):
   Super is the console's `start` button, one key with no chords
   (`runtime/console.py`); chords need Super alone to act on its release.
4. **V2, switching**: what Super+Tab or a chip does to the full-screen app
   (stay full screen behind the other window, or leave it). Drawn: the bar
   is shown, the choice is not.
5. **V2's dwell and swipe** (400 ms, top 8 px) want a measurement on glass,
   and touch has no visible hint that a bar is there to pull.
6. **F11** sits behind Fn on compact BLE keyboards; Super+↓ is the key that
   always exists.
