# The apps' restyle (design, 2026-10-21)

Paint, My Art, Get Carts, Notes and Storybook in the 2026-10 look, drawn by
`rounds/round13.py` (`index.txt` lists every picture, `moy-light_*` and
`moy-dark_*`, handheld at 320×240 and @3x, desk windows at 900×536 on the
1024×600 desk). Each app becomes a page of the public verbs
(`docs/ui_api_2026-10.md` §1); a part the verbs lack is marked **GAPS n**
and specified in `GAPS.md`. The look is `DESIGN.md`'s. The design took a
visual review and, for Storybook, a functional one (2026-10-21); where
the two differed on Storybook, the functional one decided.

## The owner's decisions (2026-10-21)

- **A-01.** The apps are built as drawn, except where a decision below
  changes them; the pictures it outdates are marked **to be redrawn**
  under their app, and are not targets until they are.
- **Storybook** stays the slides app (S-01), and is an **app with its own
  story document**: a story no longer compiles to a cart. Slides are
  1024 × 600 and draw their text in the shell's fonts at any size, so
  `print` in moy-spec does not change (S-02 and GAPS 40 are void). A
  handheld shows and presents stories zoomed out; a story is edited, and
  shown at full size, on the desk boards or in the web build.
- **Drawings.** The desk boards' drawings are 1024 × 600. A handheld's own
  drawings stay 320 × 240, and it opens a 1024 × 600 drawing view-only: it
  can use it as its wallpaper or put it in a story, not draw on it.
- **Wallpapers.** A wallpaper cart may carry one image per screen size,
  so a desk and a handheld each show a picture made for it
  (`docs/theming_2026-09.md`'s Wallpapers).
- **Moybyte fields, not moy-spec.** moy-spec is the game console spec: an
  app feature such as the story document or a wallpaper's sizes is
  Moybyte's, in Moybyte-side fields (`docs/moy_cart_api.md`).
- **Q-02, order.** The apps are built first (the look plan's step 31), then
  the Editor (step 30), then the old UI is removed.

## What the five share

- **A page, the app bar, a way back.** `ui.page(title)` under the handheld
  bar (Moy, ‹, the title, then status); ‹, B and Esc step back one page
  (`docs/look_2026-10.md` step 27a). Paint, Notes and Storybook lend the
  bar their undo, redo, zoom and Present after the title, as the editors
  do (**GAPS 37**); on the desk those live in the window's tool row. The
  bar's status is true: with no link, Wi-Fi shows `wifi_off`.
- **Grids are cards.** My Art, Get Carts and Storybook take Appearance's
  `ui.grid` and `ui.card`: a picture at its card's size, the 1 px edge, the
  lift for focus, the current plate where a card is current, a name (two
  lines where the focused card's needs them) and a sub-line. The first card
  of a grid one adds to is the verb (New drawing, New story); the slide
  sorter's New slide is last, after the slide it follows. A card says a
  state with a badge on its picture's corner (**GAPS 34**). On the
  handheld every grid keeps a 16 px margin at both sides, its gaps taking
  the rest. Grids scroll in a `ui.scroll` region and end in `ui.end()`'s
  fade; nothing pages.
- **An empty page** names what is missing and offers the one verb that
  fills it (**GAPS 36**).
- **Words for status.** The upper-case status lines go. A tool, a colour,
  a size is its part's current mark; a passing message is a toast
  (**GAPS 33**); a lasting state is a sentence in sentence case.
- **Asking.** ConfirmTap's tap-twice goes from all five. A verb that
  destroys asks in a `ui.dialog` whose first stop is the safe button
  (Remove a cart, Delete a story); a verb that loses nothing asks nothing
  (a new drawing, Move to Trash: the Trash restores; a slide's Delete:
  undo brings it back).
- **Menus and washes**, by DESIGN.md's rule: a card's or a row's menu is
  modal and washes the content between the bars (My Art's card menu, the
  sorter's slide menu, Notes' row menu); a popover hung from its control
  washes nothing (Paint's ⋯, its colours and brush, Storybook's Add and
  layouts). Dialogs wash. A wash never covers a bar or the hint strip.
  A menu's rows carry their icon and key; a destroying row is set apart at
  the foot in the danger ink; a card menu names its card in a head row.
- **Destroying verbs** are the `danger` kind: the label and icon in the
  danger ink (`native/moy_ui/themes/moy.json`'s `danger_ink`) on the
  normal fill.
- **Focus on a part of its own colour.** A focused primary button in Moy
  (`value` is `accent`) wears DESIGN.md's ring for a mark on its own
  colour (**GAPS 41**, an engine rule).
- **Hints and feet.** The handheld hint strip names the focused part's
  keys (Paint leads with the tool in use) and ends 4 px or more short of
  the glass's edge, dropping its least-used hint where the face runs wide;
  Esc Close while a popover is open; touch boards show no strip. A desk window's foot is a rule, the
  status at the left and the verbs (Files: the one Enter does last and
  primary) or the hints at the right.
- **Duplicate is Ctrl+D** in every app.
- **New icon slots** (**GAPS 32**).

## Paint

`runtime/artwork.py` (`PaintAppLayer`, `ArtworkService`). The sprite tab
(`runtime/paint_layer.py`, step 24) and Paint share their vocabulary: the
tools as toggles, the colour in use two-toned, the ring cursor, the more
menu.

| today | becomes |
|---|---|
| 10 tools: pencil, brush, eraser, fill, pick, line, box, circle, spray, pan | 8 `toggle` buttons, the one in use current: Pencil, Brush, Eraser, Fill, Shape (line, rect, oval, cycling on a repeat tap, as the sprite tab's), Spray (a can and three dots), Pick (a dropper whose drip is the colour under the cursor), Move |
| N (tap twice) | More: New drawing (N), no question |
| U, R | the bar's undo and redo (Ctrl+Z, Ctrl+Y); the desk's tool row |
| OPEN | More: Open… (O): the My Art page |
| WALL | More: Use as wallpaper (W); a toast |
| GAME, then the ADD AS BG list | More: Put in a game… (G): the projects as a `ui.table` (cover, name); Enter adds, a toast says where |
| SHOW, a tap to return | More: Show (V); any key or tap returns |
| FIT, 1X, 2X, 4X | Z cycles 1×, 2×, 4×, Fit; the bar's chip shows it (a `raised` chip with the structure line); a `ui.seg` on the desk. Paint opens at 1×, centred on the last mark (a whole-scale fit of 320×240 under the handheld bar is 160×120) |
| 64 colours; PAL1–4 pages of 16 | the rail's page of 16 holds the colour in use; the colour in use opens all 64 in a `ui.popover` (`ui.swatches` in rows, **GAPS 38**), its focus the canvas's ring, its caption the focused colour's number and name; the desk shows all 64 always |
| sizes 1, 2, 4; EDGE, SOLID | the brush popover (B), hung from the size button, whose dot is the brush's own diameter: Size for Pencil, Brush, Eraser and Spray; Size, Shape and Fill (Outline, Filled) for Shape |
| GAME / DESKTOP preset (tap twice) | goes: a desk board's drawings are 1024×600, a handheld's 320×240; on a handheld a 1024×600 drawing opens view-only, its More offering Use as wallpaper and Put in a story |
| the status line | the marks, and toasts: a new drawing's first save says its name; a failure (can't save, storage off) is a toast in the danger ink |
| a show-only picture (a cart's own image) | opens as today; a mark is refused with a toast, and More offers Copy to My Art |
| autosave, the starter garden | unchanged |

Popovers hang inside the drawing's frame, their bottoms on its bottom,
their shadows clipped to it.

**T-Deck** (keys): tools 22×22 at 23 px down x 4; the drawing in a 256×184
view at (32, 31); the rail at x 292: the colour in use 24×22, the page of
16 at 12×12, the brush button 24×22, ⋯ 24×22. **Touch boards**
(`paint-touch`, a per-board touch density, **GAPS 42**): tools two by four
at 28×28 on a 32 px pitch, the colour in use 28×28 as the only colour
target, the brush button and ⋯ at 28×28, the drawing 248×206 beside them,
no hint strip; the colours popover fills the drawing's frame and shows 28
at a time, 30×30 on a 32 px pitch, seven a row, with a 36 px page seg of
three pages. **Desk:** tools 40×40 at 44 px, the 1024×600 drawing on the
window's pasteboard at the zoom (Fit, or a whole scale panned), 36 px
buttons in the tool row, 24 px swatches. **To be redrawn:**
`moy-*_desktop_paint` draws a 512×300 drawing at 1×. **Keys:** P B E
F S A I, Space held to move, C colours, B brush, Z zoom, M more, the
arrows move the cursor, Enter or `a` draws; the T-Deck's strip shows Draw,
Colours and More after the tool.

## My Art

There is no My Art app today: `system_carts/moybyte.my_art.moy` is the
wallpaper slot a published drawing is copied into, Paint's OPEN shows the
drawings as a paged grid (`FileGridView`), and Files' Art place lists
them. **My Art** becomes the drawings' page, which Paint's Open… and Files'
Art open alike; the wallpaper slot is renamed **Wallpaper** wherever it
shows (its cart's title, Appearance's Pictures tab).

| function | part |
|---|---|
| the drawings | `ui.grid` of `ui.card`s, each its drawing at 4:3, its name; New drawing first |
| which one is the wallpaper | the `wallpaper` badge, and the caption's "Wallpaper: Garden" |
| open | Enter, a tap; on the desk the foot's Open (primary) |
| use as wallpaper, put in a game | the card's menu (W, G); the desk's foot |
| rename, duplicate, move to Trash | the card's menu (R, Ctrl+D, Del), Files' own store verbs; Move to Trash set apart in the danger ink, no question |
| nothing drawn yet | the empty page, New drawing |

The card's menu washes the page; the card under it drops its lift for the
current plate, and the menu's head names it. Handheld cards 88×66, three a
row, 14 apart; desk 152×114, five a row.

## Get Carts

`runtime/getcarts_app.py`: the phases CHECKING, LIST, CART, LICENCE, YOUR
COPY, GETTING, READY / NOT INSTALLED, ON THE CONSOLE, and the offline
ones.

| today | becomes |
|---|---|
| a row per cart: cover, name, genre, licence, size, state word | a card: the whole cover (64 on the handheld, three a row on a 102 px pitch; 128 on the desk, four a row), the name, a sub-line (the size, or the state's word); badges `check` On console, `getcarts` Update ready, `warn` in the danger ink Too big, Can't play, Name taken (the cover veiled); a cart with no cover shows its genre's icon on a tile tinted from `value` |
| "24 CARTS  3.1 MB FREE  1 SHELF AWAY" | the caption row: count left, free right; a shelf away appends "· 1 shelf away" |
| CHECK chip | R and the hint on the handheld; Check (`rotate`) on the desk's caption row |
| the cart's page | handheld: cover, genre and source, the state line in `value`, the description (a wrapped `ui.label`, **GAPS 39**), `ui.info` rows (Licence, Size, From; Needs … from …; Plays slowly on this console), then Remove (danger), Play or Use, Get or Update (primary, focused); a blocker greys the verb and says why in the danger ink. Desk: a pane beside the grid, the verbs in the foot |
| REMOVE (tap twice) | a `ui.dialog`: "Remove Hop Quest? Its saves and the files it wrote go too." Cancel first, Remove (danger) |
| LICENCE: I AGREE / NO | a page: the licence's name, its text in a `ui.scroll` well, No (focused) and I agree (primary) |
| YOUR COPY (the browser) | a page of labels over the browser's file box, Cancel |
| CHECKING, ONE MOMENT, CONNECTING | one busy page: the app's icon, a title, a line, a `ui.progress` of the shelves where counted, Cancel |
| GETTING | the cover, "Getting Hop Quest", `ui.progress`, sizes and rate, Cancel (danger); while it is put on the shelf, no Cancel and "Putting it on the shelf…" |
| READY | the cart's page again with a toast "Hop Quest is on your shelf", Play primary |
| NOT INSTALLED | an empty page: the reason, "Nothing changed.", OK |
| WIFI IS OFF | an empty page: Wi-Fi (the `wifi` glyph) is the primary and focused, Try again beside it; Wi-Fi opens Settings' Wi-Fi page, and coming back with a link up retries at once |
| SHELF AWAY, MEMORY FULL, NO INTERNET | empty pages (`warn`) with Try again primary |
| ON THE CONSOLE (a served page) | an empty page with the two sentences, no verb |

## Notes

`system_carts/moybyte.notes.moy` (a cart; `runtime/editor_handle.py` draws
the note). On the verbs it leaves `ui_draw`, the exception step 11a pinned.

| today | becomes |
|---|---|
| + NEW, the cursor's first stop | New note, the table's first row: a link in `value` with `new` |
| the names, newest first, each with its badge (MD, TXT, JSON, PY, LUA) | `ui.table` rows: the kind's icon (`notes`, `doc`, `code`) and the name -- a note's bare, any other file's with its extension; no badge column |
| — | Del moves the focused note to the Trash, no question; M opens its row menu: Rename…, Duplicate (Ctrl+D), Move to Trash (danger, set apart) |
| MY NOTES 7, the status line | the caption "8 notes"; NO STORAGE is an empty page with no verb |
| NAME IT: the field, "my note", the extension hint, MAKE, CANCEL | a `ui.dialog` holding a `ui.field` (empty, its placeholder "my note"), "End the name with .py or .lua to make a program." under it, Cancel and Make (primary); Enter makes, Esc cancels, Backspace past the start cancels as today |
| the note: BACK SEL CPY CUT PST UN RE | ‹ leaves; undo and redo in the bar; Select (a toggle, current while selecting), Copy, Cut and Paste as icons with their words under them in a foot row (11 px), each at half strength when it can do nothing; Paste a clipboard holding a page, Copy the clipboard alone |
| the editor handle's Markdown | drawn in the look: mono cells, a heading in the bold mono after a `#` in the disabled ink, checkboxes as squares (done: `value`, the words `dim`), a link in `value` underlined, a `![[drawing]]` as its picture with the edge, the selection in `selection` with `selection_ink` (moy.json's) |
| the badge refusal, "SELECT: DRAG OR ROLL TO PICK", the name and mode | the foot's right: the refusal in the danger ink, else "18 letters" while selecting, else the mode |
| the last note reopens; Files routes a `.md`, `.json` or `.txt` here | unchanged |

Desk: the vault as a 280 px column beside the note (Files' split), the
note's tools over it with tooltips naming their keys, the foot's Ctrl+C,
Ctrl+X and Ctrl+V hints. **Build item:** today's Notes cart, run in a desk
window, draws a handheld bar of its own inside the window (Moy, ‹, the
clock); on the verbs its page begins under the window's title strip, as
Files' does.

## Storybook

`runtime/storybook_app.py` today: a shelf of stories, a story's pages, a
page (4 lines of 34 mono characters, BG cycling six colours, MY ART
attaching the current drawing, TEAR OUT tapped twice), 16 pages, the story
a cart whose `main.py` the deck compiles to, read-only once its code is
edited by hand. The owner widened its scope (2026-10-21): **a slides app
for kids, with objects**, as PowerPoint is, and made it an **app with its
own story document** (2026-10-21): a story is not a cart and compiles to
none. A slide is 1024 × 600. The desk boards and the web build edit and
present stories; a handheld shows and presents them zoomed out.

**To be redrawn:** every Storybook picture (`storybook*`, handheld and
desk, and `storybook_present*`) draws a 320 × 240 slide, and the handheld
ones edit it. They show the parts, the objects and the inputs, not the
slide's size or where editing happens; the sizes below that follow a
320 × 240 slide are theirs.

### Screens

| screen | picture | what it holds |
|---|---|---|
| Stories | `storybook`, `storybook-empty` | a card a story (its first slide), New story first; Enter opens, P presents, M the menu (Rename, Duplicate, Delete, which asks) |
| a new story | `storybook-new` | New story makes one Title slide and opens it in edit with the title chosen to be written; the story is named from its title |
| Slides | `storybook-deck`, `storybook-deck-menu`, `storybook-layouts` | the sorter: a card a slide, its number and transition icon; New slide last opens the layouts (Title, Picture, Two pictures, Blank; Enter takes the last used); N adds after the focused slide; [ and ] move it; Ctrl+D duplicates; Del deletes; M or a long press opens its menu: Transition, Duplicate, Move earlier, Move later, Delete |
| a slide | `storybook-keys`, `storybook-edit`, `storybook-add`, `storybook-style`, `storybook-colours`, `storybook-overview` | the slide in its window, the chosen object, the style sheet, the colours, Z's whole slide |
| Add a picture | `storybook-pictures` | `ui.tabs` My Art and Sprites, a grid of cards; Enter adds it, chosen, and the next placeholder becomes chosen |
| Present | `storybook_present`, `storybook_present_desk` | the slide alone, fitted whole to the glass; its number shows for a second after each change, in a half-black pill (the desk: in the black margin) |

### Handheld: view and present

A handheld (the T-Deck class) lists the stories, opens a story's sorter and
presents it, each slide shrunk whole to the glass; it does not edit a
story, and New story is the desk's. Present takes the same inputs as on the
desk (Inputs, below).

### Desk: sizes and placement

| part | size, place |
|---|---|
| tool row | 44: Add ▾ (Text, Shape, Picture, Background), Bring to front, Send to back, Copy, Paste, undo, redo as 36×36 icon buttons; the ‹ 2/5 › stepper; Present (primary, 124×34) |
| slides column | 112 wide: frames 80×60 at a 72 px pitch, the number at their left, the slide in edit current; a click or tap opens one, a drag moves it, `,` and `.` step |
| the slide | 1024×600, shrunk to fit its pasteboard between the slides column and the style panel; Present shows it at 1× on 1024×600 glass, and shrunk whole elsewhere |
| chosen object | the `value` outline in its dark keyline (**GAPS 35**); handles drawn 8×8, answering 16×16 to a pointer and 32×32 to touch (by the last input) |
| snap guide | a 1 px dashed `accent` line within 4 slide pixels of the slide's centre lines and edges or another object's edges and centres; Alt held, or Snap in More, turns it off |
| style panel | 236 wide, rows 28 (32 under touch): the chosen object's Shape, Fill, Line, Width, Text size, Colour, Align, Appears; a picture's Size (½, 1, 2), Clear background, Appears; with nothing chosen, the slide's Background and its Transition (None, Fade, Swipe) |
| foot | the status, Ctrl+D Duplicate, Del Delete |
| sorter | cards 160×120 (a half), five a row, 16 apart |

### Inputs

Every action has keys, a pad, touch and a pointer (a keyboard is assumed on
every board, BLE on the touch boards); no button stands for a key alone.
The touch column names the cells of the handheld tool row the pictures
draw (Add ▾, Style, Order, More, Slides); on the desk they are the tool
row's and the panel's.

| action | keys | pad | touch | pointer |
|---|---|---|---|---|
| choose an object | Tab (z order) | L, R | tap (again: the one beneath) | click |
| move it | arrows by the step (1 px; G makes it 8 and shows the grid) | d-pad | drag the body | drag |
| resize it | R, then the arrows change the width and height; + and − keep the aspect; a picture steps through its scales | A held, d-pad | the corner knob | a handle |
| add text, a shape, a picture, a background | T, S, I, B | X: More's Add rows | Add ▾ | Add ▾ |
| write its words | Enter (the keyboard in text mode; Esc ends) | A | double tap | double click |
| its style | Y | Y | Style | the panel |
| the More menu | M | X | More | the tool row |
| front, back | ], [ | More | Order | the tool row |
| copy, paste (another slide too) | Ctrl+C, Ctrl+V | More | More | the tool row |
| centre it on the slide | More | More | More | More |
| duplicate, delete | Ctrl+D, Del | More | More | the tool row, Del |
| undo, redo | Ctrl+Z, Ctrl+Y | More | the bar | the tool row |
| the whole slide | Z | More | More | — |
| the next, previous slide | `.` and `,` | More | the bar's 2/5 | the slides column |
| a new slide after this one | N | More | Slides | the sorter |
| let go of the object | Esc | B (the d-pad then moves among the parts) | a tap on the empty slide | a click on the empty slide |
| present | P (everywhere) | More | Present | Present |
| in Present: next, previous, stop | → Space Enter, ←, Esc | A or →, ←, B | tap or swipe left, swipe right, the console key's menu | click, right click, Esc |

### Objects

| object | what it holds |
|---|---|
| text box | words (plain, one style a box, 200 characters; wrapped at word breaks in its width, a long word broken at the edge; its height follows), a font of the shell's and its size (any size; the style panel offers S, M, L and XL first), colour (the 16), align, an optional strip behind |
| shape | rect, rounded rect, ellipse, line, arrow, star, speech bubble; fill (none or a colour), line (none or a colour), width 1 or 2; words inside, styled as a text box's; arrow, line and the bubble's tail point four ways (`flip`) |
| picture | a drawing from My Art, copied into the story (copy-on-use); scales ½, 1, 2, 3, 4 of its source, the ½ baked when it is placed (a pixel kept wherever the source has one off the background, so a 1 px line survives); Clear background draws its corner colour clear |
| sprite | tiles from any project's sheet, copied into the story when used |
| background | a colour or a drawing (a 1024×600 one fills the slide) |

Every object has a **step**: 0 appears with its slide, 1, 2, … appear on
taps in that order (the style panel's Appears). Every slide has a
**transition**: None, Fade (through black, about six frames each way) or
Swipe (the next slide pushes the last one off), set in the slide's menu
and in the desk panel with nothing chosen.

### One renderer

Storybook's slide renderer is the only one: edit draws through it (edit is
what Present shows), the thumbnails are its frames shrunk by the C blit,
and a drag redraws the dirty box (the old box and the new, the handles'
reach and a guide's strips) in z order. Present is Storybook's own full
screen, started at a slide and returning to it on Esc; it holds the
current and the next slide's images only.

### The document

A story is a document of its own, the JSON below and the images it uses,
not a cart. By the repo's rule (no store migrations before users,
CLAUDE.md "Settled") Storybook reads only this format, strictly: an old
story cart's `moydeck-v1` `deck.json` is not a story, and the cart stays a
cart, which Make opens. No seed cart is a story, so no seed version moves.

```json
{"format": "moystory-2", "slides": [{"bg": 15, "transition": "fade", "objects": [
 {"type": "text", "x": 26, "y": 36, "w": 972, "text": "Once upon a time...",
  "font": "ui", "size": 48, "colour": 1, "align": "centre", "strip": null, "step": 0},
 {"type": "picture", "x": 51, "y": 240, "image": "pg2", "scale": 0.5, "clear": true, "step": 0},
 {"type": "sprite", "x": 640, "y": 100, "tile": 3, "tw": 2, "th": 2, "scale": 6, "step": 0},
 {"type": "shape", "shape": "bubble", "x": 595, "y": 240, "w": 397, "h": 160, "flip": 0,
  "fill": 7, "line": 0, "width": 2, "text": "Hello!", "font": "ui", "size": 32, "colour": 0,
  "align": "centre", "step": 1}]}]}
```

- `format` is the version, read strictly. Objects carry no ids: z is the
  array's order, undo records a slide's index and its list, the clipboard
  is one object's JSON.
- Geometry by type: a picture or a sprite is a position and a scale, a text
  box a width (its height follows), a shape its whole rect.
- Coordinates are the 1024 × 600 slide's. `font` names one of the shell's
  font roles and `size` its pixel height; `step` replaces `appear`; `bg` is
  a colour index or an image name.
- Limits: 16 slides, 12 objects a slide, 200 characters a text box, 24
  images a story, an image at most 1024×600.

### Later

| feature | why later |
|---|---|
| free rotation | no part draws a rotated rect, oval, text or sprite: a rotated raster per primitive |
| pen and bezier paths | a path rasteriser (fill and stroke) in `moy_gfx` |
| grouping | a group object, nested choosing and hit-testing: editor work, a little runtime |
| gradients | the look's paints are chrome (GAPS 7); a slide's gradient needs a raster of its own |
| animation paths | a timeline per object and an editor for it |
| styled runs inside one text box | a rich-text model in the document and the renderer |
| go to a slide on a tap, a sound a slide | choose-your-own-adventure stories; a `go` field is reserved for it |
| distribute, a layers panel, speaker notes | declined for a kids' app |
