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
# (`files.list()`, `files.new_name()`) and `open_editor(name)` -- a handle onto
# one note, over the console's own editor. The editor is not in this file: the
# text, the caret, wrap, undo, the Markdown rendering (headings, checkboxes,
# `[[links]]`, `![[drawings]]`) and the save all live in the shell and are drawn
# through the handle, the way `make_layer` hands a cart the layer engine. This
# file is the SKIN: a list, two buttons and where the page goes.
#
# The handle also owns the keyboard while it is focused, so there is no typing
# code here and no `textmode()` call -- taking the keyboard is a thing only the
# shell can do correctly on the T-Deck.

ROWS = 7               # names the shelf shows
BTN_H = 18             # the button strip over an open note

ed = None              # the open note's editor handle (None = the shelf)
names = []             # the vault, newest first
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
    global names, status
    got, err = files.list()
    names = [] if err is not None else got[:ROWS]
    if err is not None:
        status = "NO STORAGE"


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


def _new():
    name, err = files.new_name()
    if err is not None:
        return
    _open(name)


# -- frame -------------------------------------------------------------------


def _update(dt):
    _tapping()


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
    else:
        _draw_note(cv, th, body)


def _draw_shelf(cv, th, rect):
    strip, list_rect = ui.cut_bottom(rect, BTN_H)
    inner = ui.panel(cv, th, list_rect, "MY NOTES")
    rows = ui.vsplit(inner, ROWS, 2)
    for i in range(ROWS):
        if i >= len(names):
            break
        ui.row(cv, th, rows[i], names[i], hits=hits, verb="open", arg=i)
    new_btn = ui.hsplit(strip, 3, 4)[0]
    ui.button(cv, th, new_btn, "+ NEW", state=hits.state_of("new"))
    hits.add(new_btn, "new")


def _draw_note(cv, th, rect):
    strip, page = ui.cut_top(rect, BTN_H)
    a, b, c = ui.hsplit(strip, 3, 4)
    ui.button(cv, th, a, "NOTES", state=hits.state_of("back"))
    hits.add(a, "back")
    ui.button(cv, th, b, "UNDO", state=hits.state_of("undo"),
              disabled=not ed.can_undo())
    hits.add(b, "undo")
    ui.button(cv, th, c, "REDO", state=hits.state_of("redo"),
              disabled=not ed.can_redo())
    hits.add(c, "redo")
    line, sheet = ui.cut_bottom(page, 10)
    ed.draw(sheet[0], sheet[1], sheet[2], sheet[3])
    cv.print((ed.badge() or status)[:38], line[0], line[1] + 1, th["ink_dim"], 1)


# -- taps: the draw pass IS the hit map (ui.Hits) -----------------------------


def _tapping():
    t = touch()
    if t is None:
        return
    hits.pointer_frame(t[0], t[1], _Ptr(t[3]))
    hit = hits.at(t[0], t[1])
    if hit is None:
        if ed is not None:
            _in_page(t[0], t[1], t[2])
        return
    if not t[2]:
        return
    verb, arg = hit
    if verb == "new":
        _new()
    elif verb == "back":
        _close()
    elif verb == "undo":
        ed.undo()
    elif verb == "redo":
        ed.redo()
    elif verb == "open" and arg < len(names):
        _open(names[arg])


def _in_page(x, y, click):
    # Anything the buttons did not claim belongs to the editor. It answers what
    # the tap MEANT: a tapped [[link]] is another note, which is the one thing
    # the skin has to act on -- the checkbox and the caret it handles itself.
    got = ed.tap(x, y, click)
    if got is not None and got[0] == "link":
        _open(got[1])


class _Ptr:
    """What Hits' pump duck-types on: `.down` and `.visible`. A cart has no
    Pointer object of its own, and touch() already carries both bits."""

    def __init__(self, down):
        self.down = down
        self.visible = False        # touch never hovers -- it presses
