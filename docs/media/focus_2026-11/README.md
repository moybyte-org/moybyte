# Focus and selection across the themes (2026-11-05)

The owner chose one grammar for focus and selection on 2026-11-05 from mock-ups
drawn in Moy Night (`docs/theme_and_shell_2026-11.md` §1.5, landed in
`320a5b60e`). Selected is a fill. A focused row wears a 4 px bar at its left,
with no fill. A focused button, tab or cell wears a 2 px ring 2 px clear of it.
Touch shows neither. On the T-Deck the owner then saw three problems:

- the control center's separators disappear beside the focused row;
- its card sits flush on the panel's top;
- the other themes look different.

This folder audits every theme on every surface that wears a mark, and
proposes the grammar the themes share. Every image comes from the real
toolkit. `tools/focus_shots.py` builds the goldens' workstation through
`tools/look_shots.py`'s `render` and draws every surface in every state.

```bash
.venv/bin/python tools/focus_shots.py docs/media/focus_2026-11   # today/, proposal/, findings/
```

## Reading the renders

The images are in three folders:

- `today/` shows what dev draws, at `810f6d445`.
- `proposal/` shows the proposed grammar.
- `findings/` holds the numbered crops this README cites.

Each run has these sheets:

| sheet | what it holds |
|---|---|
| `surface_<surface>_tdeck.png` | the state sheet on the T-Deck (320×240), shown at 2×: one row per theme-mode, one column per state |
| `surface_<surface>_desk.png` | the same sheet in a window on the desk (1024×600), shown at 2× |
| `surface_<surface>_shell.png` | the real shell's screens, at 1×, on both sizes |
| `theme_<theme>-<mode>_tdeck.png`, `theme_<theme>-<mode>_shell.png` | one theme-mode's contact sheets: every surface in that theme |

The theme-modes are Moy Night (`moy-dark`), Moy Day (`moy-light`), Blocks
(`blocks-light`, its one mode), Contrast dark and Contrast light.

**The state sheet** is a system app (`Sheet` in the tool) that draws one surface
per page with the public verbs. It covers:

- a group card (`ui.link`, `ui.toggle`, `ui.slider`);
- a plain list (`ui.list`) and a table (`ui.table`);
- a menu (`ui.menu`);
- buttons of every kind (`ui.button`);
- a dialog (`ui.dialog`);
- tabs and segments (`ui.tabs`, `ui.seg`);
- cards and tools (`ui.grid` and `ui.card`, `ui.tool`);
- fields (`ui.field`).

Each item is labelled with its own state. "Selected" is drawn with
`o.current`, "Disabled" with `o.disabled`, and the danger kind is labelled
"Danger".

A cell's caption names its frame's state:

- `rest`: focus is on the page's "Other" button, so every item shows at rest.
- `focus-<item>`: that stop holds the focus.
- `focus-selected`: the focus is on the selected item.
- `pressed`: a finger is held on the item.
- `touch-*`: touch is the input.

**The shell screens** are these:

- Settings, with Volume, Steady (a toggle) and the empty Name field focused;
- the control center: open, Brightness focused and Wi-Fi focused;
- Files, and the desk with Files and Calc;
- the system menu over the home, the pause menu, the desk's system menu and
  the desk's power popover;
- the delete question on both sizes;
- the bar's status cluster and the Moy button focused;
- the home's tiles, Calc and the desk's PROJECTS window.

## The audit: what ships today

Ranked by how much each breaks the owner's grammar.

**1. In Blocks and Contrast, focus is a fill.**
![focus is a fill](findings/01_focus-is-a-fill-in-two-themes.png)
Moy draws a focused row as the bar. Blocks fills the row in its ink, and
Contrast fills it in yellow. The cause is the themes' `look.focus`: Moy has
`edge`, while Blocks and Contrast have `invert` for every part that is not a
card (`native/moy_ui/themes/blocks.json`, `contrast.json`). The same split
holds on every surface: buttons, menus, tabs, fields, keys and tools (see
`today/surface_*_tdeck.png`).

**2. A fill means opposite things in different themes.**
![fill swaps meaning](findings/02_focus-and-selection-swap-meaning.png)
- In Moy, a fill is the selected row.
- In Blocks, a fill is focus, and the selected row is an underline under its
  label (`current.row: underline`).
- In Contrast, both marks are solid fills. Yellow is focus, white or black is
  selected, and only the colour tells them apart.

A table's current row follows its focus. So in Blocks and Contrast, a focused
table shows only the focus fill, and the selected mark is not shown at all
(`today/surface_table_tdeck.png`).

**3. A group row cannot be selected.**
![selected group row draws nothing](findings/03_selected-group-row-draws-nothing.png)
`moy_ui_vrow` in `native/moy_ui/moy_ui_parts.c` draws the focus mark, but it
never draws `MOY_UI_F_ON`, although `mua_mark_flags` passes it. So `ui.link`,
`ui.toggle` or `ui.slider` with `o.current` look the same as a row at rest, in
every theme. "Selected = a fill" has no drawing on the very card it was chosen
for. Table rows and list rows do draw it.

**4. The rules vanish beside the bar, and the bar sits on the card's frame.**
![rules vanish](findings/04_rules-vanish-beside-the-bar.png)
This is the owner's defect (a). In `native/moy_ui/moy_ui_api.c`, `mua_band`
treats a focus style as filling its row when it is `invert` or `edge`. A row
that fills draws no rule, and neither does the row under it. `edge` lost its
plate on 2026-11-05, so the hairlines above and below a focused row now go
missing for nothing. The cause is the same in Settings and in the control
center.

`on_card_mark` also puts the bar flush against the frame's inner edge. On a
card's first row and its last, the bar's corner meets the arc of the card's
rounded corner (the owner's second photo, and the CC crop here).

**5. The control center's card sits flush on the panel's top, and the panel's foot is empty.**
![control center](findings/05_control-center-flush-top.png)
This is the owner's defect (b). In `runtime/system/popover.py`,
`ControlCenter.column()` sizes the panel with 12 px of pad at its top and 12 px
at its foot. `ControlCenter.draw()` then places the card 1 px under the top.
The card is 1 px from the top, 7 px from the sides, and the pad it was given
sits empty under it.

**6. The box's shape picks the mark, not the part.**
![shape decides](findings/06_the-shape-decides-the-mark.png)
The `edge` case in `moy_ui_focus_in` (`native/moy_ui/moy_ui_depth.c`) draws a
bar on any box at least three times as wide as it is tall. It draws a ring only
when the caller passes `MOY_UI_MARK_CELL`. Buttons pass it, so a wide button
still rings (`proposal/surface_buttons_tdeck.png`, "focus-wide"). A choice
strip does not: `ui.tabs`, `ui.seg` and `ui.swatches` are one stop whose box
is the whole strip. So a focused tab row wears a row's bar beside the strip in
Moy, and a filled strip in Blocks and Contrast. §1.5 says a tab wears the ring.

**7. A fill hides what the part is.**
![focus hides the part](findings/07_focus-hides-the-part.png)
`invert` replaces both the part's fill and its ink:
- Contrast's focused Delete loses its danger red.
- A focused Primary and a focused Normal look the same.
- Blocks' focused selected tool covers its underline.
- On a card's row, Contrast's yellow focus hides the row's toggle state
  (`today/surface_group_tdeck.png`).

**8. A focused field wears two marks.**
![field two marks](findings/08_a-field-wears-two-marks.png)
A focused field gets the row's bar and also an accent border on its box (the
text field's ring). The Music spec (`docs/media/music_2026-11/README.md`,
"Focus marks") gives the field the bar *in place of* the accent border. A field
in error keeps its danger border either way.

**9. A pressed button looks like Primary, or like focus.**
![pressed](findings/09_pressed-looks-like-primary-or-focus.png)
In `native/moy_ui/style.json`, every button kind's pressed field is
`["pressed", "hilite", 13]`, and `hilite` is the accent in every seed theme.
So a pressed Normal button looks like Primary in Moy and Blocks, and like a
focused button in Contrast. A group row has no pressed state at all: a finger
held on it shows nothing (the "pressed" column of
`today/surface_group_tdeck.png`).

**10. A focused card lifts in Moy and rings everywhere else.**
![cards](findings/10_cards-lift-in-moy-ring-elsewhere.png)
Moy has `focus.card: lift`, and every other cell in Moy rings. In the desk's
PROJECTS window, the shelf's focused tile lifts while the buttons above it ring
(`today/surface_cells_shell.png`). That is two focus shapes for cells in one
window.

**Smaller findings:**
- A focused bar needs Contrast light's keyline too. `focus.outline` (2 px
  black) is drawn round the ring, which keeps its yellow readable on white. The
  `edge` case draws no outline, though, and a bare yellow bar on white is about
  1.3:1. This only shows once Contrast uses the bar.
- The selected mark on a pill button is drawn square. On Moy's "Toggle on"
  button, the `current` plate ignores the button's radius
  (`today/surface_buttons_tdeck.png`).
- Under touch, every theme drops both focus marks (✓). The selected fill stays
  (✓).

## The grammar (proposed)

**The same in every theme.** These rules are the semantics, and the engine
holds them:

1. **Selected is a fill:** a plate or a solid fill, with its own ink. It is the
   only mark that fills a part.
2. **Focus never fills.** A focused row wears the bar at its leading edge. A
   focused cell wears the ring outside its box: a button, a tab, a segment, a
   key, a tool, a chip, a bar item or a card. The part keeps its own fill, ink
   and border, danger and primary included.
3. **The part's kind sets its mark class, not its box's shape.** The row kinds
   are value rows, list rows, table rows, menu rows and a field's row. The cell
   kinds are everything a key acts on as one item. A choice strip rings its
   current item.
4. **A focused, selected item wears both marks, stacked.** The shapes tell them
   apart, so a theme may draw both in one colour (Moy does).
5. **Focus shows under keys and the pad only.** It never shows under touch, and
   it marks one stop per screen (as today).
6. **Focus never removes structure.** Rules, card frames, a field's own border
   and a danger ink all stay where they are.
7. **Focus can always be seen.** The mark holds 3:1 against what it lands on.
   Where it does not, the theme names a keyline (`focus.outline`), and the
   engine draws the keyline round the bar and the ring alike.
8. **Pressed is brief feedback.** It moves the part's own fill one step (`sunk`
   or the theme's), and never to the accent, because the accent already means
   primary or focus.

**What a theme may vary:**

| what | the range | today's seeds (proposal) |
|---|---|---|
| focus colour | any role (`focus.color`, default `accent`) | Moy lavender/indigo, Blocks ink `#1a1530`, Contrast yellow |
| bar width | 3–5 px at the font scale | 4 everywhere |
| ring width and gap | width 2–3, gap 1–3 | Moy 2/2, Blocks 3/2, Contrast 3/2 |
| keyline | none, or a width and a colour | Contrast light: 2 px black |
| selected | a tinted `plate` (a role and its alpha) or a solid `fill` (`selection` and `selection_ink`) | Moy a `value` plate at 0.24/0.14, Blocks the `selection` fill, Contrast a solid `value` plate with `value_ink` |
| radius | follows the part's own radius | — |

**What a theme may not do:** focus as `invert`, `plate` or `underline`, or
selection as a bar, an underline or an outline. Each of these breaks rules 1–2
in at least one of the renders above.

The owner's four choices hold in every theme once the engine changes below
land (`findings/11_the-proposal-in-every-theme.png`,
`findings/12_the-proposal-on-cells.png`). None of the four fails in any theme.
What fails is the themes and engine paths that do not follow them yet.

![proposal rows](findings/11_the-proposal-in-every-theme.png)
![proposal cells](findings/12_the-proposal-on-cells.png)

## The package entries

**Step A, today's keys** (the data the proposal renders install; `PROPOSAL` in
the tool). Each seed's `look` changes as follows:

```json
"moy":      {"focus": {"style": "edge", "card": "ring", "small": "edge",
                       "ring": {"w": 2, "gap": 2}, "edge": {"w": 4}}}
"blocks":   {"focus": {"style": "edge", "card": "ring", "small": "edge",
                       "ring": {"w": 3, "gap": 2}, "edge": {"w": 4}},
             "current": {"style": "fill", "row": "plate"}}
"contrast": {"focus": {"style": "edge", "card": "ring", "small": "edge",
                       "ring": {"w": 3, "gap": 2}, "edge": {"w": 4}}}
```

These changes do three things:
- Blocks loses its white `focus.outline`, which only served `invert`.
- Contrast keeps its light mode's black outline and its solid `value` plate.
- Moy's lift values stay in the file, unused while `card` is `ring`.

**Step B, the format** (§3.3's parts × states). The engine's size classes
become mark classes. In `native/moy_ui/style.json`, each kind names its class:
`row`, `cell` or `card`. A new package key, `marks`, gives each class its marks
for two states: `focus`, and `on` (§3.3's selected state). Each entry is one
line of grammar, as `surfaces` are:

```json
"marks": {
  "row":  {"focus": "bar 4",               "on": "plate value 24/14"},
  "cell": {"focus": "ring 2 gap 2",        "on": "plate value 24/14"},
  "card": {"focus": "ring 2 gap 2",        "on": "plate value 24/14"},
  "focus": {"color": "accent", "outline": "2 #000000"}
}
```

```text
focus := "bar" W | "ring" W "gap" G | "ring" W "gap" G "lift"
on    := "plate" ROLE A[/A] | "fill" ROLE ["ink" ROLE]
```

The loader takes nothing else. A `focus` that names `fill`, `plate`, `invert`
or `underline` is dropped and noted, as a bad surface is (§6). Rules 1 and 2
then hold by construction.

Under the no-migrations rule, the format change ships as strict readers plus a
seed version bump: `look.focus` and `look.current` then read as absent. The
installable ricing themes in moybyte-org/carts (Luna, Coast, Pico, Bench, Adw)
use `invert` and `plate` focus (the marks table in
`docs/media/look_2026-10/DESIGN.md`), so they are rewritten in that repo in
the same step.

## What the proposal renders draw by hand

The look data cannot express the engine changes. `_overlay` and `_cc_inset` in
the tool draw them over the real render, the way the build is to draw them:

- the selected fill on a group row (`_selected_fill`, item 3);
- the rules beside a row's bar, the bar 2 px clear of a card's frame, and the
  keyline round the bar (`_overlay`, items 2, 4 and 6);
- a field's own border under the bar (item 7);
- the ring round the current tab in place of a bar beside the strip
  (`_tab_ring`, item 5);
- the control center's card inset (`_cc_inset`, item 8).

Pressed (item 9) and Step B are not mocked, so `proposal/` still shows today's
pressed look.

## Change list for the build

Each item lands with its golden re-bake.

1. **Seeds.** Apply Step A to `native/moy_ui/themes/moy.json`,
   `native/moy_ui/themes/blocks.json` and `native/moy_ui/themes/contrast.json`.
   Then rewrite every claim it falsifies: §1.3's "Focus and current are theme
   properties" bullet, D-14 and m07/m17 in §1.3, the marks section and table
   of `docs/media/look_2026-10/DESIGN.md`, and `THEMES.md` there.
2. **Rules stay beside the bar.** In `native/moy_ui/moy_ui_api.c`, make
   `mua_band`'s `fills` mean `invert` only, so `edge` no longer drops the rule
   above a focused row or the one under it.
3. **Value rows draw selection.** In `native/moy_ui/moy_ui_parts.c`, have
   `moy_ui_vrow` draw `MOY_UI_F_ON` as `moy_ui_table_row` does: the `current`
   mark beside the row (`MOY_UI_MARK_SIDE`) under the focus mark, with its ink
   for the label.
4. **The bar clears the card.** In `native/moy_ui/moy_ui_parts.c`, have
   `on_card_mark` for `edge` stand the bar 2 px clear of the frame (`f + 2 *
   fs`). The bar keeps its 2 px vertical inset, which then clears the corner
   arc on the first and last rows.
5. **A choice strip rings its current item.** `ui.tabs`, `ui.seg` and
   `ui.swatches` draw their focus as the ring (`MOY_UI_MARK_CELL`) round the
   current item's box, not as a mark on the strip. The marks' class comes from
   the kind, not from `w >= 3 * h` (Step B makes this the rule).
6. **The keyline goes round the bar.** In `native/moy_ui/moy_ui_depth.c`, the
   `edge` case of `moy_ui_focus_in` draws `focus.outline` round the bar, as
   `focus_ring_of` does round the ring.
7. **A field's focus is the bar.** A focused field in a row keeps its box's
   rest border (`field.border`, or the caller's `o.border`). Where a field
   stands alone (outside a row), it takes the ring.
8. **Control center inset.** In `runtime/system/popover.py`, have
   `ControlCenter.draw()` place the scroll region's top by the group's inset
   (`metrics("group")[0]`, 6 px). Have `column()` count that inset at the top
   and foot in place of 12 px, so the card stands 6 px clear on all four sides
   and the panel ends 6 px under it.
9. **Pressed is the part's own step.** In `native/moy_ui/style.json`, the
   button kinds' pressed field becomes a step of their own fill (`sunk`, which
   a theme may restyle through `surfaces.<part>.pressed`), never `hilite`. A
   row gets a pressed tint under touch, which it has none of today.
10. **The current plate follows the part's radius.** A selected pill button's
    plate is a pill (Moy's "Toggle on").
11. **The guard.** Add a test over every seed theme-mode, on the state sheet's
    group, buttons and tabs pages:
    - a focused row's interior pixel equals its rest pixel;
    - the rules above and below it are present;
    - a selected row differs from a rest row;
    - a focused button's fill and ink equal its rest ones;
    - no focus mark draws under touch.

    Then add `tests/test_theme_loader.py` cases for Step B's grammar refusing a
    fill as focus.
12. **Step B** (`marks` and the mark classes), after 1–11, as its own change.
    It includes the ricing themes in moybyte-org/carts.

## Open questions for the owner

1. **Do the semantics hold across themes?** Blocks' rule (2026-10-19: "a fill
   is focus", underline is current) and Contrast's `invert` (D-14, "the
   accessible choice") both make focus a fill. The proposal reverses both, so
   that a fill means *selected* in every theme (findings 1, 2 and 7). The
   alternative is to let a theme family keep "focus = fill". That leaves a fill
   meaning opposite things across a theme switch, and a focused danger button
   with no red.
2. **Should Moy's cards ring or lift?** The proposal rings them, as §1.5's
   "cell" says, and it renders that way. The other choice is the lift *with*
   the ring (`ring … lift` in Step B's grammar), so the home's hero cover keeps
   its depth.
