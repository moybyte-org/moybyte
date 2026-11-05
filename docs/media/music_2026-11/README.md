# Music mockups (2026-11-04)

The Music system app (#110), and the two shell features it controls the
stream from, before either is built:
- **Music**: a radio of curated http MP3 stations, the user's own MP3s and
  playlists, and Now playing, over `moybyte.media`
  (`docs/os_2026-11/hardware.md` §3).
- **An app's status item on the desk**: an icon in the bar's right zone and
  the menu it opens.
- **The control center on a handheld**: one panel off the bar's status
  cluster, with the console's quick controls and then each app's card.

**Every file is an engine render.** `tools/music_mockups.py` draws each page
with the public UI API's verbs (`docs/ui_api_2026-10.md`) on the
shell-goldens workstation (`tests/test_shell_goldens.py`), at the board's
real size, in Moy Night (`moy-dark_*`) and, for some scenes, Moy Day
(`moy-light_*`). The media service's answers are faked. The status item and
the control center open through the shell's own popover (`toggle_popover`).
To draw them again, run `.venv/bin/python tools/music_mockups.py OUT [FILTER ...]`.
A 320×240 file also has an `@3x` copy, scaled nearest.

## One media card

What plays is one component (`MediaCard` in the tool) wherever it shows:
- **Its parts:**
  - the art, a station's or list's colour field, which carries no state;
  - the title;
  - one state line: the state glyph, then artist · station (or the list and
    "3 of 12"). A line too wide drops a part at its separator, never cut
    inside one: a station keeps its name, a list its own;
  - a chevron where the card opens the app;
  - the transport.
- **Its densities:**
  - `strip`: a band over a list's foot, with the art, two lines, the
    chevron and a filled play/pause button;
  - `compact`: a rounded card, the head over the transport, used by the
    control center and the desk menu, and laid in one row across the desk
    window's foot;
  - `full`: Now playing, with large art, the artist and the station on
    lines of their own, "◆ LIVE" in the accent (no outline) or the file's
    bar, and the transport on its grid.
- **Its states**, the same at every density, carried by the state glyph,
  the line and the buttons:
  - playing: four lively bars in the accent;
  - paused: the same four bars 2 px wide and 2 px tall on their baseline,
    in the paused ink;
  - connecting: three 2 px dots centred on the line, the middle one lit,
    then "Connecting...";
  - failed: "Not answering" in the danger ink. The main button becomes Try
    again (↵ Try again), and the bar's ♫ turns the danger ink.
- **Its transport:** previous, the main button (play, pause or retry), next,
  then stop after a double gap, all in the same ink, on a 1:2:1 + 1 grid
  across the card.

## One grammar for selection, focus and hints

The owner's grammar (2026-11-05): selection is a fill, and focus is a ring
on a cell or a bar on a row. The shell adopts it, and the build of the
status item and control center makes the toolkit draw it. In the mockups
the tool draws whatever the toolkit does not do yet.

- **Selected is a fill.** The following keep today's filled pill or fill:
  - the open tab (the launcher's, Music's);
  - the front window's task button;
  - the open sidebar place (Files' Projects, Music's Radio);
  - a list's current row while focus is elsewhere;
  - a bar item whose panel or menu is open (the status cluster while the
    control center is open, ♫ while its desk menu is).
- **A focused cell wears the ring.** Buttons, tabs, the bar's status
  cluster and other cells wear one ring: an even 2 px stroke of the accent,
  2 px out from the cell, following its corners. A focused cell that is also
  selected wears both, fill and ring.
- **The status cluster's ring** has 3 px of clearance on every side. It
  stands 3 px below the glass's top and 6 px from the clock, and the bar
  always reserves its room, so nothing moves when the keys arrive.
- **A focused row wears the bar.** It is the accent bar at the row's left,
  4 px wide on the T-Deck, with no fill. A focused row that is also
  selected wears both, fill and bar.
  - The following are rows: a table's rows, a link, a slider, a field
    (its label stays above it), a card's head and a volume meter.
  - The bar is 4 px, not 3. Alone, without a fill, 3 px reads as a hairline
    beside a row's icon at 320×240, faintest on Moy Night's dark panel and
    against Moy Day's lavender. 4 px is about a third of the icon's width
    and reads as a mark in both themes.
- **Under touch** there are no rings and no bars, only the selected fills.
- **Playing is not selection.** What plays shows through its content: the
  accent on the row's words and its state glyph. A row that only leads
  somewhere ("Add a station") is in the text ink with its glyph in the
  accent.
- **State is not an action.** The state glyph shows what plays, and ▶, ⏸ and
  ↻ appear only on buttons.
- **The paused ink** is a theme role between the text and the ground:
  MOY64 50 in Moy Night (the dim ink was too close to the accent) and
  `ink_dim` in Moy Day.
- **One chevron:** the toolkit's value-row chevron (`moy_ui_parts.c`), never
  a font's `>` or `›`.
- **Hints:**
  - The strip names the keys of the input in use, 8 px in from both edges.
    Under touch it is gone.
  - Its last hint is always Back (K, B or Esc), or Close in a popover. A
    lesser hint is dropped first.
  - Mic is the T-Deck's cancel inside a field only (`ACT_KEYS`' `cancel`;
    in a field K types a letter). Everywhere else Back is K.
  - The confirm hint's word follows the focused thing ("Play", "Pause",
    "Cancel", "Try again").
  - When the focus is on one of a row of buttons, the hint names how to
    reach the others (A D, ← →: "Button"). A change on a pad is ← →.
  - A strip names only what the focus can do. A menu opened with the
    pointer has no focus to show, so its strip is "Esc Close".
  - While a text field holds the keys, no hint names a letter key. Enter is
    "Next", and the last field's Enter does the page's verb.
- **A page whose keys are its own** (Now playing) shows no focus mark. Its
  hints are the whole contract:
  - Enter plays, pauses or tries again.
  - Q E (L R on a pad) step the station or the song.
  - W S (up and down) set the volume.
  - Back leaves.
  - M (Y) is "Player" on the list and "List" on Now playing.

  Stop is on the screen only: it is a tap or a pointer away.

## Toolkit and shell changes the build must make

These are build step 1's spec, beside the status-item API.

**What changes from today's shell, everywhere.** The pills and fills stay
as they are. Three things change:
- **A focused row loses its fill.** Today a focused row is fill plus bar;
  it becomes the bar alone, 4 px on the T-Deck. A row that is focused and
  selected keeps both.
- **A focused button, tab or cell wears the ring.** Today a button shows
  focus with an underline (Calc's keys) and a bar cell with a fill; both
  become the 2 px ring.
- **Two levels move:** the modal wash darkens to 70 %, and the hint strip
  sits 8 px from the edges.

1. **Focus marks.**
   - Rows: the toolkit's focus mark on a row (`MOY_UI_MARK_PART`) drops its
     plate and keeps its bar, 4 px on the T-Deck.
   - Buttons, tabs and cells: the ring, the running set's dialog included
     (today it draws a row's bar on Cancel).
   - A field takes the row's bar in place of an accent border.
   - Under touch, neither.
2. **Selection** keeps today's fills: tabs, task buttons, a sidebar's open
   place, a list's current row without focus, and a bar item that is open.
3. **A scroll thumb** wherever a column runs on: a placed table's and the
   control center's. It is 2 px wide, in the dim ink on a structure track.
   A table ends on a whole row.
4. **A scroll region's fades.** 8 px over the region's background at
   whichever edge it runs on past, never a solid band.
5. **The control center.** The `battery` widget's component grows into a
   column:
   - one 12 px padding;
   - the console's card first (Volume, Brightness, Wi-Fi), then each app's
     card under its caption;
   - every card rounded with four borders;
   - its first view ends 6 px under the first app card, and its thumb says
     the rest is below;
   - a 50 % scrim over the page beside it in both themes, its card and only
     its card punched out (the card's shadow takes the scrim).

   The desk's menus take no scrim.
6. **The bar.**
   - Status icons, Wi-Fi and battery are one stop on the handheld, and it
     opens the control center.
   - The layout reserves the cluster's ring room: 6 px from the clock, the
     tabs' gaps evened and shrunk until the last tab stands 8 px clear.
   - While the control center is open, the cluster wears the tabs' pill.
   - The battery glyph draws below 15 % all in the danger ink, and charging
     with a bolt cut out of an accent charge. Its exact percentage lives in
     Settings, since the control center has no battery row.
7. **Status items.** The desk bar gets a status cell per item. Its icon
   shows its state in the accent, the paused ink or the danger ink, and
   wears the task buttons' pill while its menu is open.
8. **Dialogs:**
   - A dialog's body wraps and the card grows to fit. Today the running
     set's `ask_close` line is one line, cut.
   - The card's foot pads the buttons' focus ring as its top pads the title.
   - A dialog button can be disabled.
   - The modal wash is 70 %.
9. **Fields:**
   - The box fills its rect (today it stands 4 px inside), so a form's
     labels, fields, help and buttons share one gutter.
   - `border: "danger"` on `ui.field` replaces the field's border.
   - 8 px of text padding, and a long value clipped inside the field.

   A form lays its fields on a 12 px gutter, x 12 to 308 on the T-Deck,
   each label above its field and its help or error line under it.
10. **Buttons:** a disabled button is the surface with its border and its
    label in the dim ink (at least 3:1). The toolkit's washed-out disabled
    primary falls under that.
11. **Hints:**
    - The strip is 8 px from both edges and drops its lesser hints before
      Back.
    - A field never shows a letter key's hint.
    - Mic shows only in a field.
    - A pad's change is ← →.
12. **Tabs:** Music's tabs are the toolkit's (`ui.tabs`), the open one
    filled. They sit 6 px above and below the row with 4 px under the rule
    before the content, and are no focus stop (Q E, L R or a tap switch
    them).
13. **Theme roles:** the paused ink (above).
14. **Icons:**
    - new slots `pause`, `prev`, `next`, `stop`, `retry`, `minus`, `plus`,
      `playlist`, the equalizer and the connecting pulse;
    - a station's art as a two-colour field (`moy_gfx`'s gradient `rrect`,
      MOY64 colours);
    - the drawn minus and plus replace the font's dash, which sits 2 px low;
    - the speaker in a volume row centres on its meter.
15. **The media face.** `now()` gains a file's place and length (seconds) for
    the bar and its "1:12 / 3:41". Today the C counts frames since the
    service opened, across the whole list, and knows no length.
16. **The system menu's Stop music row goes.** It lives in
    `runtime/shell/console_notices.py` (the `STOP MUSIC` row and
    `_menu_stop_media`), with its test in `tests/test_dev_media.py`. The
    sentence in `docs/os_2026-11/hardware.md` §3 goes with it.

## Where the build differs

Build step 2 (`runtime/apps/music_app.py`, cart
`system_carts/moybyte.music.moy`) differs from the renders in these places:
- Its process kind is `jukebox`: `music` is the Editor's Music tab.
- A list's current row draws no fill once the focus leaves the table.
- The volume meter changes by a tap or by its − and + buttons. It takes no
  ← → of its own.
- On a pad, Y opens Now playing. L and R do not switch tabs: the input
  table has no shoulder keys.
- Every seed names the `paused` role in both modes; a theme that does not
  falls back to `ink_dim`.
- The Radio tab shows its no-Wi-Fi page when the console has a Wi-Fi radio
  that is not up and knows no network.
- Its prefs keep only the stations added (`music_stations`); there is no
  settings page.

## The files

| file | what it shows |
|---|---|
| `*_handheld_radio`, `-pad`, `-touch`, `moy-dark_handheld_radio-buffering` | the Radio tab with the T-Deck's keys, a gamepad's and touch, and a station connecting. The playing station wears the accent and a lively equalizer. The list ends on a whole row and has a thumb. The strip is the card's strip density |
| `*_handheld_radio-nowifi` | Radio with Wi-Fi off: the bar's Wi-Fi drawn off, and the Wi-Fi settings button. Every empty state stands at one top |
| `*_handheld_files`, `-empty`, `moy-dark_handheld_files-nosd` | My music: songs and playlists, the paused playlist in the still equalizer. Then the empty folder, and a console with no SD card |
| `*_handheld_now-radio`, `-pad`, `-touch`, `*_handheld_now-playlist` | Now playing, the card's full density on a 12 px grid from x 12 to 308: the artist and the station, ◆ LIVE or the file's bar and time, the transport, the volume meter between − and + |
| `moy-dark_handheld_now-buffering`, `moy-dark_handheld_now-error` | the card's other states: connecting, and a station that does not answer |
| `*_handheld_add`, `moy-dark_handheld_add-https` | Add a station: Enter steps Address, then Name, then Add, and Mic cancels. An https address on a console that cannot verify one (the host the goldens run on) shows the danger border in place of the field's, the line under it, and Add disabled |
| `moy-dark_guition-s3_noaudio` | the Guition S3, which shows Music with its no-audio screen (owner's decision) |
| `*_desktop_music`, `*_desktop_music-files` | the P4 desk window: Library with its open place filled, the table with its focused row's bar, and the compact card in one row across the foot |
| `*_status_desk-bar`, `*_status_desk-menu`, `moy-dark_status_desk-menu-keys`, `-error` | the desk's status item: ♫ beside the Wi-Fi while a stream exists, then its menu opened with the pointer (the compact card, the volume, "Esc Close"), the menu with the keys on Pause, and a station that failed. With Files in front, its focused row wears the bar alone and Projects, the open place, its fill; with the menu open, Harpoon Pop is the selected row (fill) and ♫ wears the task buttons' pill |
| `moy-dark_cc_bar`, `*_cc_bar-music`, `moy-dark_cc_bar-paused`, `*_cc_bar-focus`, `moy-dark_cc_bar-low`, `moy-dark_cc_bar-charging` | the T-Deck's bar: without music, with ♫ playing (accent) and paused (the paused ink), with the keys on the status cluster, and the battery glyph low and charging |
| `*_cc_open`, `moy-dark_cc_open-keys`, `-scrolled`, `-pad`, `-touch`, `-buffering` | the control center: its first view, the keys on Music's Pause, scrolled a card (Downloads standing in for any other app), with a pad, under touch, and with a station connecting |
| `moy-dark_vmstop_dialog` | the question before a compiled game the VM must stop for while the music holds the Wi-Fi: Cancel focused, Stop & play |
