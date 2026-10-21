# The Editor's layout on the desk (2026-10-21)

The owner's brief (2026-10-21): on the desk the Editor shows several of
its parts at once, like a game-making IDE, instead of one tab at a time;
the handheld keeps its tab ladder, every tab in the look. Three concepts
were drawn and reviewed the same day by a visual designer and a game
maker; both chose **B, Workspaces**, and this file is B's design: the
pane and tool sizes at 1024 × 600 and 800 × 480, the input model, the
handheld, the new parts, and the owner's calls still open.
`rounds/round12.py` draws every picture named here (`draw.py`), with Sky
Run's code, sheet, map and scene and Coin Quest's blocks; the sound bank
past Sky Run's two effects is illustrative. It is the target of step 30
of `docs/look_2026-10.md`; `docs/studio_2026-09.md` is the earlier design
it builds on.

## The owner's calls

The pictures assume yes to each; none is decided.

1. **Live edits.** A sprite, sound or flag edit reaches a running game
   at once; a map, scene or code edit waits for the next Play, and
   Restart wears a dot while one waits (`moy-*_desktop_editor-b-code`).
   This revises `docs/studio_2026-09.md` S13, and rests on the playtest
   reading the editor's sheet and bank in RAM, which is unverified.
2. **Sound names.** An optional `name` per sound in `sounds.json` (a
   moy-spec SPEC change); without it the list shows numbers and sketches.
3. **The Editor opens maximised** on the desk (1024 × 600, 800 × 480 and
   1280 × 800) and then remembers its last state per board.
4. **A maximised window's strip carries its app's row** (every window,
   not the Editor alone). A restored window's tab row returns under a
   plain strip, which keeps 120 px bare to drag and double-tap. Two
   alternatives, the bar taking the tools and full screen, are drawn
   beside it in `MAXIMISE_VARIANTS.md`, with the panes' dividers moved by
   hand (its Resize part).

## Not chosen

- **A, Project** (a file tree, one document, a dock): still one
  document at a time, and the 176 px tree does what the strip does
  (designer); a 484 px document is about 60 columns, and sprites never
  sit beside the map (game maker). Its context-follows-the-document pane
  lives on as B's Reference. Pictures, as drawn: `moy-light_desktop_editor-a-{code,art,world,sound}.png`.
- **C, Splits** (any panel in any pane, `docs/studio_2026-09.md`): no
  visible task switch, and dragging tabs and gutters is pointer work
  (both); a later opt-in layout at most. Pictures, as drawn:
  `moy-light_desktop_editor-c-{code,art,world,sound}.png`.

## The frame (both desk sizes)

- **The strip** (y 40–84) is the maximised window's title strip. Left to
  right: the icon and the cart's name alone ("Sky Run": the chip already
  says Editor) in a fixed title slot (144 px at
  1024, 112 at 800, an ellipsis past it), so the workspaces always start
  at x 190 (158 at 800); the five workspaces, labelled, or icons with the
  current one labelled where the strip is short (800); undo and redo at a
  fixed place; a hairline; the 170 px Play slot (Play, or Restart and
  Stop); minimise, restore, close. The slot never changes width, so undo
  and redo never move. No top accent on a maximised window: the focused
  pane wears it.
- **Undo and redo** act on the focused pane's file.
- **A pane** is a `surface` card (radius 8; Night adds its 1 px `line`) on
  the Editor's `bg` ground, 6 px gutters, flat. Its 32 px header holds an
  icon and a name (with a dim status after it), or its tabs, then its
  tools in 32 × 32 cells (18 px glyphs), then grow. Tools that do not fit
  take a second 32 px row. A short pane's tabs show the current one
  labelled and the others as icons.
- **The focused pane** takes the keys and wears the 3 px `value` edge
  along its top. Moving focus between panes never commits; commits ride
  the idle debounce, Play and exit, and a workspace switch commits only
  dirty panes.
- **One cursor at full strength:** the focused pane's (the ring round a
  black keyline); a pane without the keys draws its cursor as a 1 px dim
  keyline.
- **Focus on a row or card** is Moy's: the 4 px `accent` edge over a
  0.12 (Day) or 0.14 (Night) `accent` plate.
- **The Run slot** shows the game at a whole scale under a "Game" header
  reading the scale, with pause and grow (grow takes it to 2× over the
  workspace). No game ticks while none runs: the slot holds a still
  frame, dimmed under a Play glyph. Its keys reach the game while it is
  focused; the console key there opens the pause menu in the slot.
- **Every touch target is 32 px or more on the desk, and its hit area is
  the control as drawn** (a field, a chip, a button, the add-block pill),
  never a larger invisible box. Every zoom is a whole number.
- **The game wears a screen glyph** wherever it is a pane or a tab, and
  its tab is always named "Game", never a bare ▷.

## B at 1024 × 600

Panes from x 6–1018, y 90–594 (1012 × 504). Each workspace's picture is
`moy-{light,dark}_desktop_editor-b-<name>.png`.

| workspace | pane | x, y, w, h | holds |
|---|---|---|---|
| **Make it mine** (`config`) | Settings | 6, 90, 360, 504 | rows ≥ 36: Run at (one slider, the value at its right), Hero (sprite tiles), Autorun (a toggle); This project: Cart info, Files |
| | Game | 372, 90, 646, 504 | no header; 2× (640 × 480) while running, else the still frame |
| **Code** (`code`) | Code | 6, 90, 666, 504 | file tabs; find, go to definition, more; ~79 columns × 32 rows at 11 px; a 26 px status line (Ln, Col, lines, issues, language; F6) |
| | Run slot | 678, 90, 340, 282 | the game at 1× from (688, 127) |
| | Reference | 678, 378, 340, 216 | Sprites (one page at 2×, 256 × 128, page chips), Sounds (named rows with ▶), Map, Log (print and errors, linking to their line); a pick inserts at the caret |
| **Code**, a blocks cart (`blocks`) | Block palette | 6, 90, 200, 504 | the eleven categories as a 2-column grid of 32 px chips, then the current one's blocks as 32 px rows |
| | Blocks | 212, 90, 460, 504 | object tabs (the actor kind, Stage, the others); add, delete, up, down, more; the outline with "+ add block" under each hat |
| | Scene · Game | 678, 90, 340, 282 | the scene at 1×; picking an actor opens its script; Play switches to Game |
| | Code | 678, 378, 340, 216 | the code the blocks make, read only |
| **Art** (`art`) | Sprite | 6, 90, 400, 504 | flip, rotate; a 44 px tool row of 36 px cells: pen, fill, shape, select, stamp, picker, more; the canvas box 288 × 288 (8 × 8 at 36, 16 × 16 at 18, 24 × 24 at 12); the palette 2 × 8 at 36 at its right; ‹ n ›, the size, "64" for MOY64; the 8 flags at 36; hints |
| | Map · Game | 412, 90, 606, 336 | pen, box, fill, select, erase; brush 1×1; zoom ‹ 2× ›; the view 592 × 288 at 2× (37 × 18 tiles) with the one-screen frame; a status line; holding a cell picks its tile |
| | Sheet | 412, 432, 606, 162 | both pages at 2× side by side, captioned 1 and 2; one pick sets the sprite and the map's brush; a press on a 16 px tile shows a 3× loupe |
| **World** (`world`, `world-stamp`) | Scene | 6, 90, 666, 504 | pick, stamp, flip, front, back, delete, snap, zoom; the selection's status; the view 640 × 464 at 2× (40 × 29 tiles), the actors over the real map |
| | Run slot | 678, 90, 340, 282 | |
| | Actor · Stamps | 678, 378, 340, 216 | two tabs in that order; the tool selects one and never moves them. Actor: Tag (a 32 px field, and 32 px chips of the tags the scene and the code use), Flip, Front and Back, the tile and place, Remove (32 px each), and Edit map (Art, on this place). Stamps: a sheet page at 2× and the tag the stamp gives its actors |
| **Sound** (`sound`) | Sounds | 6, 90, 200, 504 | Song, then a 40 px row a sound: number, name, sketch, ▶; ＋ |
| | Sound | 212, 90, 460, 504 | loop, speed ‹ n ›, ▶; the wave brush (32); the pitch graph, 288 tall: two octaves at 12 px a semitone, the selected step's note named; 16 steps at 26 px, a second page past 16; each step's wave (16); the volume ladder, 8 levels at 8 px; the ops at 36: add, delete, copy, paste, duplicate, up, down, rest; Oct ‹ 5 › and the page |
| | Run slot | 678, 90, 340, 282 | |
| | Song | 678, 378, 340, 216 | 8 slots, 4 × 2 at 72 × 56; the playing one on the current plate with a ▶ |

The Run slot is in the same place in Code, World and Sound; Art's and a
blocks cart's game is a Game tab beside the Map and the Scene, which Play
selects and Stop leaves.

## B at 800 × 480

Panes from x 6–794, y 90–474 (788 × 384); one tabbed pane at the right
(340 × 384) instead of the Run slot over a context pane, its first tab
always Game (the game at 1× at its top). Pictures: `moy-light_desktop_editor-b-<name>-800x480.png`.

| workspace | left | right |
|---|---|---|
| Make it mine | Settings, 300 wide | the game at 1×, centred |
| Code | Code 442 × 384 (~51 columns; grow gives 788, ~100) | Game, Sprites, Sounds, Map, Log |
| Art | Sprite 306 × 384: the canvas box 192 (8 × 8 at 24), the palette 2 × 8 at 24, flags at 24 | Map · Game 476 × 384: the view 464 × 204 at 2× (29 × 12¾), then one sheet page at 2× with its page chips and 8 px under it, the brush and the zoom beside it. No 1× sheet |
| World | Scene 442 × 384 at 2× (pick, stamp, snap, zoom) | Actor (or Stamps), Game |
| Sound | Sound 442 × 384, the sounds as a menu from its title; the graph at 8 px a semitone (192), 22 px steps, the ops at 36, Oct ‹ 5 › | Game, Song |

Under 660 px of panes across, or in a restored window below its
workspace's minimum, the Editor is today's: one panel, the tab ladder.
At 1280 × 800 the Run slot is 2× (660 × 512).

## Input

| | switch workspace | move focus | grow a pane | Play / Restart / Stop |
|---|---|---|---|---|
| touch | tap it | tap the pane | the grow button or a double tap on its header | the strip |
| keys | Ctrl+1 … 5, Ctrl+Tab | F6, Shift+F6, in reading order | Ctrl+Shift+Enter; Esc restores | Ctrl+Enter plays and restarts, Ctrl+. stops |
| pad | L, R | Select and the d-pad, by direction | Select+A | Select+Start plays; Start is the console key |
| pointer | click | click | grow, or a double click | click |

A pane keeps its own keys while focused (the sprite tab's F, V, R, M, Q
and W; the map's and scene's arrows; Code's text). The focused pane's foot
shows its hints and F6 where it has room.

## The handheld

One tab at a time under one bar for all seven: the Moy tile, the seven
tabs in 18 px cells with the open one on the current plate and named
(11 px); then a hairline, undo, redo and Play anchored to the battery
and the clock, so the open tab's name never moves them. No ‹
(Esc and B step back) and no Wi-Fi in the Editor. Config's glyph is
`sliders` on both sizes; its handheld name stays "Config", "Make it mine"
being too long for the bar. Pictures, Day and Night at 1× and 3×:
`moy-{light,dark}_handheld_<tab>-editor.png`, and:

- **Config:** the cards as one group, Run at focused; This project.
- **Blocks:** the object picker, the outline, "+ add block", the tools and
  See code, the door to the Code tab.
- **Code:** step 23's tab under the one bar.
- **Sprites:** step 24's arrangement under the one bar; `_more` opens the
  more menu, which gains a Flags row (the sprite's 8 flags).
- **Map:** the map, the brush palette (‹ 1 / 4 ›), the tile and cell, hold
  to pick; pen, box, fill, select, erase, the brush (a square glyph) and
  the zoom (a magnifier), resize.
- **Scene:** a coin selected, its name, its place on a second line, its
  props (Tag, Flip, Front, Back, Remove); `_stamp` shows the stamp tool's
  palette and the stamp's tag.
- **Music:** Sound and Song, ‹ 2 tune ›, the steps, the pad; the foot's
  Hear (a `normal` button, so the bar's Play stays the one Play) and Loop.

## New parts and glyphs

`GAPS.md` 31 lists every part and glyph slot these pictures need that the
toolkit lacks, with its cost; the glyphs are drawn in
`rounds/round12.py`'s `NEW_GLYPHS`.

## Open questions

1. Actors draw as their one tile, as the scene editor does today. The
   game maker's proposal: a stamp carries its sprite's size (1 × 1, 2 × 2,
   3 × 3) in the actor (w, h) and draws at that size, with no scale field.
2. The handheld Config tab's name ("Config" drawn; "Make it mine" does
   not fit the bar).
3. The 2× game outside Make it mine: the Run slot's grow (drawn), and
   2× by default at 1280 × 800; neither ticks idle. Measure the 2× pane
   on the P4 before building on it (GAPS 31).
