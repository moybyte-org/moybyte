# Notes -- a USER APP, and it is nothing but a cartridge (#181).
#
# Everything below is ordinary cart code you can open in the Editor and change.
# What makes it an APP rather than a game is two lines of manifest.json:
#
#     "type": "app"                            -> the shell runs it WITH the
#                                                 exitable bar (the X in the top
#                                                 right), so it can never trap you
#     "permissions": ["files:docs", "prefs"]   -> the shell hands it `files` and
#                                                 `prefs`, and NOTHING else
#
# A permission it does not ask for has no NAME here at all -- writing `carts` in
# this file is a NameError, not a locked door. That is the whole sandbox.
#
# The four extra globals every "app" cart gets, with no permission needed,
# because they are how an app draws rather than what it may reach:
#
#     screen()   the canvas -- what the ui toolkit's `cv` argument wants
#     theme()    the live panel-theme tokens -- its `th` argument
#     bar_h()    rows at the top the shell's own bar owns; draw below them
#     ui         the REAL runtime/ui.py the shipped apps use: rect algebra
#                (inset/cut_*/hsplit/vsplit), themed widgets, and Hits
#
# Kid-facing behaviour: type a note, SAVE it into your documents (the same place
# Writer keeps its notes -- open one there and it is really the same file), tap a
# saved name to read it back. NEW starts a fresh one. It remembers which note you
# had open, in its own corner of the settings (`prefs` is namespaced per app, so
# it cannot see -- or break -- anybody else's).

MAX_LINES = 9          # what fits the note panel at 320x240
MAX_COLS = 26
MAX_LIST = 6           # saved names the shelf shows

lines = [""]           # the note being edited
name = None            # the file it came from / goes to, None while unsaved
saved = []             # saved note names
status = "TYPE A NOTE"
hits = None


def _init():
    global hits
    # Clean ASCII typing (the T-Deck keyboard's text mode). Safe in an app: the
    # bar's X is the exit, so BACKSPACE stays an ordinary delete key -- which is
    # exactly why a text-mode GAME has to provide quit() and this does not.
    textmode(True)
    hits = ui.Hits()
    _refresh()
    last = prefs.get("last")
    if last:
        _open(last)


# -- storage: every verb answers (value, err) and never raises ---------------


def _refresh():
    global saved, status
    names, err = files.list()
    if err is not None:
        saved = []
        status = "NO STORAGE"
    else:
        saved = names[:MAX_LIST]


def _open(what):
    global lines, name, status
    body, err = files.load_text(what)
    if err is not None:
        status = "CAN'T OPEN"
        return
    lines = body.split("\n")[:MAX_LINES] if body else [""]
    name = what
    status = "OPEN " + what


def _save():
    global name, status
    target = name
    if target is None:
        target, err = files.new_name()      # NOTE 1, NOTE 2, ...
        if err is not None:
            status = "NO STORAGE"
            return
    target, err = files.save_text(target, "\n".join(lines))
    if err is not None:
        status = "CAN'T SAVE"
        return
    name = target
    prefs.set("last", target)
    status = "SAVED " + target
    _refresh()


def _new():
    global lines, name, status
    lines = [""]
    name = None
    status = "NEW NOTE"


# -- typing ------------------------------------------------------------------


def _update(dt):
    _typing()
    _tapping()


def _typing():
    ch = keyp()                             # one edge per press, no autorepeat
    if not ch:
        return
    if ch == 8:                             # BACKSPACE
        if lines[-1]:
            lines[-1] = lines[-1][:-1]
        elif len(lines) > 1:
            lines.pop()
    elif ch == 10 or ch == 13:              # ENTER
        if len(lines) < MAX_LINES:
            lines.append("")
    elif 32 <= ch <= 126:
        if len(lines[-1]) < MAX_COLS:
            lines[-1] = lines[-1] + chr(ch)


# -- drawing: the shell's own toolkit, at the shell's own theme ---------------


def _draw():
    cv = screen()
    th = theme()
    cls(col("black"))
    hits.clear()
    # The shell paints its bar over the top bar_h() rows and swallows taps
    # there, so lay out below it and never hardcode 18.
    body = ui.inset((0, bar_h(), W, H - bar_h()), 4)
    shelf, note = ui.cut_right(body, 96)
    _draw_note(cv, th, ui.cut_right(note, 4)[1])
    _draw_shelf(cv, th, shelf)


def _draw_note(cv, th, rect):
    bar, page = ui.cut_bottom(rect, 20)
    inner = ui.panel(cv, th, page, name or "NEW NOTE")
    y = inner[1] + 2
    for i in range(len(lines)):
        text = lines[i]
        if i == len(lines) - 1:
            text = text + "_"               # the caret, as a character
        cv.print(text[:MAX_COLS], inner[0] + 2, y, th["ink"], 1)
        y += 9
    left, right = ui.hsplit(bar, 2, 4)
    ui.button(cv, th, left, "SAVE", state=hits.state_of("save"))
    hits.add(left, "save")
    ui.button(cv, th, right, "NEW", state=hits.state_of("new"))
    hits.add(right, "new")
    cv.print(status[:24], rect[0] + 2, bar[1] - 10, th["ink_dim"], 1)


def _draw_shelf(cv, th, rect):
    inner = ui.panel(cv, th, rect, "SAVED")
    rows = ui.vsplit(inner, MAX_LIST, 2)
    for i in range(MAX_LIST):
        if i >= len(saved):
            break
        ui.row(cv, th, rows[i], saved[i], on=(saved[i] == name),
               hits=hits, verb="open", arg=i)


# -- taps: the draw pass IS the hit map (ui.Hits) -----------------------------


def _tapping():
    t = touch()
    if t is None:
        return
    hits.pointer_frame(t[0], t[1], _Ptr(t[3]))
    if not t[2]:
        return
    hit = hits.at(t[0], t[1])
    if hit is None:
        return
    verb, arg = hit
    if verb == "save":
        _save()
    elif verb == "new":
        _new()
    elif verb == "open" and arg < len(saved):
        _open(saved[arg])


class _Ptr:
    """What Hits' pump duck-types on: `.down` and `.visible`. A cart has no
    Pointer object of its own, and touch() already carries both bits."""

    def __init__(self, down):
        self.down = down
        self.visible = False        # touch never hovers -- it presses
