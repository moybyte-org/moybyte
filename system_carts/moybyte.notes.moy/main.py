# Notes -- the console's one text app, and it is nothing but a cartridge (#181).
#
# Everything below is ordinary cart code you can open in the Editor and change.
# What makes it an APP rather than a game is its manifest.json:
#
#     "type": "app"                 -> the shell runs it WITH the exitable bar
#                                      (the X in the top right), so it can never
#                                      trap you
#     "permissions": [...]          -> the shell hands it exactly these, and
#                                      nothing else. A permission it does not
#                                      ask for has no NAME here at all: writing
#                                      `carts` in this file is a NameError, not
#                                      a locked door. That is the whole sandbox.
#
# The one that matters here is `files:docs`, which brings BOTH the storage verbs
# (`files.list()`, `files.new_name()`, `files.badge()`) and `open_editor(name)`
# -- a handle onto one note, over the console's own editor. The editor is not in
# this file: the text, the caret, wrap, the sideways pan, undo, the clipboard,
# SELECT mode and the Markdown rendering (headings, checkboxes, `[[links]]`,
# `![[drawings]]`) all live in the shell and are drawn through the handle, the
# way `make_layer` hands a cart the layer engine. This file is the SKIN: a list,
# a row of buttons and where the page goes.
#
# The handle also owns the keyboard while it is focused, so there is no typing
# code here for the NOTE -- taking the keyboard is a thing only the shell can do
# correctly on the T-Deck. The NAME PROMPT is the exception and says so: no
# handle is open there, so it asks for the text keyboard itself with
# `textmode()` and reads clean bytes off `keyp()`.
#
# Two input paths, always both, because the three boards are not the same
# machine: everything is TAPPABLE (touch glass), and everything on the shelf is
# also reachable with the d-pad + A/ENTER the launcher uses. Inside a note the
# trackball is the caret -- the shell routes it to the focused handle (ws.nav),
# the same roll that moves the caret in the Editor's Code tab.

ROWS = 6               # names one screen of the shelf shows
STRIP = 18             # a button strip: the shelf's header, a note's toolbar
LINE = 10              # the status line under either one
NAME_MAX = 24          # characters the name prompt takes

# The note toolbar. Short labels and this order are the Code tab's tool palette
# (`code_layer._TOOLS`), because it is the row a kid's hand has already learned
# -- and because on the T-Deck it is the ONLY path: that keyboard has no Ctrl,
# so the Ctrl+C/X/V/Z/Y the handle also answers are a host convenience, never
# the way a note is edited on the board.
TOOLS = ("back", "sel", "copy", "cut", "paste", "undo", "redo")
TOOL_LABEL = {"back": "BACK", "sel": "SEL", "copy": "CPY", "cut": "CUT",
              "paste": "PST", "undo": "UN", "redo": "RE"}

ed = None              # the open note's editor handle (None = the shelf)
names = []             # the vault, newest first
sel = 0                # the shelf cursor: 0 is NEW, 1..n are names
top = 0                # first name the list shows
naming = False         # the NEW name prompt is up
typed = ""             # what has been typed into it
paging = False         # a drag that started on the page belongs to the page
status = "MY NOTES"
hits = None


def _init():
    global hits
    hits = ui.Hits()
    _refresh()
    # A note the console was already asked to open -- Files routed a `.md`, a
    # `.json` or a `.txt` here. `open_editor()` with no name is that request,
    # and None when there was none.
    asked = open_editor()
    if asked is not None:
        _show(asked)
    else:
        last = prefs.get("last")
        if last in names:
            _open(last)


# -- the vault ---------------------------------------------------------------


def _refresh():
    global names, status, sel, top
    got, err = files.list()
    names = [] if err is not None else got
    if err is not None:
        status = "NO STORAGE"
    if sel > len(names):
        sel = len(names)
    top = min(top, max(0, len(names) - ROWS))


def _open(name):
    got = open_editor(name)
    if got is None:
        return
    _show(got)


def _show(handle):
    global ed, status
    _close()
    ed = handle
    ed.focus()                          # the handle takes the keyboard
    prefs.set("last", ed.name())
    status = ed.name()


def _close():
    global ed, status
    if ed is not None:
        ed.close()                      # saves on the way out -- no SAVE button
        ed = None
    status = "MY NOTES"
    _refresh()


# -- NEW: the name is asked for, and an extension in it is kept --------------
#
# A bare name is a note (`.md`). A name that carries an extension keeps it, and
# the extension is what picks the editing mode: `todo.txt` is plain text,
# `data.json` is JSON, `hi.py` and `hi.lua` are scripts. The store slugs and
# unique-ifies whatever is typed, so NEW never overwrites a file that is there.


def _start_naming():
    global naming, typed, status
    naming = True
    typed = ""
    textmode(True)          # no handle is open: this prompt wants the keyboard
    status = "TYPE A NAME"


def _stop_naming():
    global naming, status
    naming = False
    status = "MY NOTES"
    textmode(False)


def _make():
    global status
    name, err = files.new_name(typed.strip() or None)
    _stop_naming()
    if err is not None:
        status = "NO STORAGE"
        return
    _open(name)


def _naming_key():
    global typed
    k = keyp()
    if not k:
        return
    if k == 0x0D or k == 0x0A:
        _make()
    elif k == 0x08 or k == 0x7F:
        if typed:
            typed = typed[:-1]
        else:
            _stop_naming()      # backspacing off the end backs out of the prompt
    elif 0x20 <= k <= 0x7E and len(typed) < NAME_MAX:
        typed += chr(k)


# -- the shelf's cursor ------------------------------------------------------


def _move(step):
    global sel, top
    sel = max(0, min(len(names), sel + step))
    if sel > 0:
        if sel - 1 < top:
            top = sel - 1
        elif sel - 1 >= top + ROWS:
            top = sel - ROWS


def _activate():
    if sel == 0:
        _start_naming()
    elif sel <= len(names):
        _open(names[sel - 1])


# -- frame -------------------------------------------------------------------


def _update(dt):
    _keys()
    _tapping()


def _keys():
    # A focused handle owns the keyboard, so the note view reads nothing here
    # -- key()/keyp() are already quiet while it holds focus.
    if ed is not None:
        return
    if naming:
        _naming_key()
        return
    if btnp("down"):
        _move(1)
    elif btnp("up"):
        _move(-1)
    elif btnp("a") or btnp("run"):
        _activate()          # A / ENTER, the launcher's own activate keys


def _draw():
    cv = screen()
    th = theme()
    cls(th["panel"])
    hits.clear()
    # The shell paints its bar over the top bar_h() rows and swallows taps
    # there, so lay out below it and never hardcode 18.
    body = ui.inset((0, bar_h(), W, H - bar_h()), 4)
    if ed is None:
        _draw_shelf(cv, th, body)
        if naming:
            # The prompt is MODAL: drop the shelf's own tap rects so a finger
            # landing beside the box cannot open a note out from under it.
            hits.clear()
            _draw_naming(cv, th, body)
    else:
        _draw_note(cv, th, body)


def _draw_shelf(cv, th, rect):
    head, rest = ui.cut_top(rect, STRIP)
    line, list_rect = ui.cut_bottom(rest, LINE)
    # NEW lives in a header strip and not at the bottom of the list, because
    # that is where the eye lands and because it is the cursor's FIRST stop:
    # one control, reachable by a finger and by the same A/ENTER that opens a
    # cart on the launcher shelf.
    new_btn, title = ui.cut_left(head, 84)
    ui.button(cv, th, new_btn, "+ NEW", on=(sel == 0),
              state=hits.state_of("new"))
    hits.add(new_btn, "new")
    cv.print(("MY NOTES  " + str(len(names)))[:26], title[0] + 6,
             title[1] + 5, th["ink_dim"], 1)
    inner = ui.panel(cv, th, list_rect)
    rows = ui.vsplit(inner, ROWS, 2)
    for i in range(ROWS):
        n = top + i
        if n >= len(names):
            break
        name = names[n]
        # The whole name, and the mode's badge beside it: the vault holds
        # notes, plain text, data and scripts side by side now, and a row that
        # did not say which is which would open a surprise.
        ui.row(cv, th, rows[i], name, on=(sel == n + 1), value=files.badge(name),
               hits=hits, verb="open", arg=n)
    cv.print(status[:38], line[0], line[1] + 1, th["ink_dim"], 1)


def _draw_naming(cv, th, rect):
    box = (rect[0] + 10, rect[1] + 44, rect[2] - 20, 78)
    inner = ui.inset(ui.panel(cv, th, box, "NAME IT"), 4)
    field, below = ui.cut_top(inner, 16)
    ui.text_field(cv, field, typed, "my note")
    hint, btns = ui.cut_top(below, 10)
    cv.print(".txt .json .py -- or just a name", hint[0], hint[1] + 1,
             th["ink_dim"], 1)
    a, b = ui.hsplit(btns, 2, 4)
    ui.button(cv, th, a, "MAKE", state=hits.state_of("make"))
    hits.add(a, "make")
    ui.button(cv, th, b, "CANCEL", state=hits.state_of("cancel"))
    hits.add(b, "cancel")


def _draw_note(cv, th, rect):
    strip, page = ui.cut_top(rect, STRIP)
    rects = ui.hsplit(strip, len(TOOLS), 2)
    for i in range(len(TOOLS)):
        verb = TOOLS[i]
        off = _tool_off(verb)
        ui.button(cv, th, rects[i], TOOL_LABEL[verb], disabled=off,
                  on=(verb == "sel" and ed.selecting()),
                  state=hits.state_of(verb))
        if not off:
            hits.add(rects[i], verb)
    line, sheet = ui.cut_bottom(page, LINE)
    ed.draw(sheet[0], sheet[1], sheet[2], sheet[3])
    cv.print((ed.badge() or _note_status())[:38], line[0], line[1] + 1,
             th["ink_dim"], 1)


def _tool_off(verb):
    if verb == "copy" or verb == "cut":
        return not ed.has_selection()
    if verb == "paste":
        return not ed.can_paste()
    if verb == "undo":
        return not ed.can_undo()
    if verb == "redo":
        return not ed.can_redo()
    return False


def _note_status():
    if ed.selecting():
        return "SELECT: DRAG OR ROLL TO PICK"
    return ed.name() + "  " + ed.mode().upper()


# -- taps: the draw pass IS the hit map (ui.Hits) -----------------------------


def _tapping():
    global paging
    t = touch()
    if t is None:
        return
    x, y, click, down = t
    hits.pointer_frame(x, y, _Ptr(down))
    hit = hits.at(x, y)
    if ed is not None:
        _page_pointer(x, y, click, down, hit)
    if hit is None or not click:
        return
    verb, arg = hit
    if verb == "new":
        _start_naming()
    elif verb == "make":
        _make()
    elif verb == "cancel":
        _stop_naming()
    elif verb == "open" and arg < len(names):
        _open(names[arg])
    elif verb == "back":
        _close()
    elif verb == "sel":
        ed.select_mode()
    elif verb == "copy":
        ed.copy()
    elif verb == "cut":
        ed.cut()
    elif verb == "paste":
        ed.paste()
    elif verb == "undo":
        ed.undo()
    elif verb == "redo":
        ed.redo()


def _page_pointer(x, y, click, down, hit):
    # Anything the buttons did not claim belongs to the editor. A PRESS asks it
    # what the tap meant -- a tapped [[link]] is another note, which is the one
    # thing the skin has to act on; the checkbox and the caret it handles
    # itself. What follows the press is a DRAG, which the handle turns into a
    # pan (sideways too, in a mode that does not wrap) or, in SELECT mode, into
    # a growing selection. `paging` is what keeps a drag that began on a button
    # from scrolling the page under it.
    global paging
    if click and hit is None:
        got = ed.tap(x, y, True)
        paging = True
        if got is not None and got[0] == "link":
            paging = False
            _open(got[1])
    elif not down:
        if paging:
            ed.drag(x, y, False)
        paging = False
    elif paging:
        ed.drag(x, y, True)


class _Ptr:
    """What Hits' pump duck-types on: `.down` and `.visible`. A cart has no
    Pointer object of its own, and touch() already carries both bits."""

    def __init__(self, down):
        self.down = down
        self.visible = False        # touch never hovers -- it presses
