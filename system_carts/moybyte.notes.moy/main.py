# Notes -- the console's one text app, and it is nothing but a cartridge (#181).
#
# Everything below is ordinary cart code you can open in the Editor and change.
# What makes it an APP rather than a game is its manifest.json:
#
#     "type": "app"                 -> the shell runs it WITH the exitable bar,
#                                      so it can never trap you
#     "permissions": [...]          -> the shell hands it exactly these, and
#                                      nothing else. A permission it does not
#                                      ask for has no NAME here at all: writing
#                                      `carts` in this file is a NameError, not
#                                      a locked door. That is the whole sandbox.
#
# The one that matters here is `files:docs`, which brings BOTH the storage verbs
# (`files.list()`, `files.new_name()`, `files.rename()`, `files.duplicate()`,
# `files.delete()`, `files.badge()`) and `open_editor(name)` -- a handle onto
# one note, over the console's own editor. The editor is not in this file: the
# text, the caret, wrap, undo, the clipboard, Select and the Markdown (headings,
# checkboxes, `[[links]]`, `![[drawings]]`) all live in the shell and are drawn
# through the handle, the way `make_layer` hands a cart the layer engine. This
# file is the page: the list, the tools and where the note goes, drawn with the
# public UI API (`ui`), which moves focus, keys and taps for it.
#
# The handle owns the keyboard while a note is open, so there is no typing code
# here for the NOTE. Taps on the note are the cart's to forward: only the cart
# knows where it drew it.

NAME_MAX = 24          # characters a name takes
DESK_W = 600           # a page this wide shows the vault beside the note
SIDE = 268             # the vault's column on the desk, from the window's content
FOOT = 31              # the desk window's foot
ICON = {"MD": "notes", "TXT": "doc", "JSON": "doc", "PY": "code", "LUA": "code"}
# The note's tools: Select toggles a selection that a drag or a roll grows.
TOOLS = (("select", "Select"), ("copy", "Copy\tCtrl+C"), ("cut", "Cut\tCtrl+X"),
         ("paste", "Paste\tCtrl+V"))
KEY_DEL, KEY_BS, KEY_ESC, KEY_DUP, KEY_CR, KEY_LF = 0x7F, 0x08, 0x1B, 0x04, 0x0D, 0x0A

NO_STORE = ("Notes keep to a card;", "put one in to start.")
FS = 1                 # the font scale the page lays out at (_layout)
MODES = {"md": "Markdown", "code": "Program", "json": "Data", "text": "Text"}

# The page is drawn again on every change, so what it hands the verbs is
# made once: the override tables here, the vault's rows and caption in
# _refresh, a note's shown name in _title.
TOOL_O = (({"current": False, "disabled": False}, {"current": False, "disabled": True}),
          ({"current": True, "disabled": False}, {"current": True, "disabled": True}))
UNDO_O = ({"disabled": False}, {"disabled": True})
OFF = ((False, False), (True, False), (False, True), (True, True))
STATUS_O = {"id": "status"}
MODE_O = {"id": "mode"}
VAULT_O = {"id": "vault", "lead": True}
DIM_O = {"tone": "dim"}
FIELD_O = {"id": "Name", "hint": "my note"}
WRAP_O = {"wrap": True, "tone": "dim"}
WASH_O = {"wash": True}

ed = None              # the open note's editor handle (None: the list)
names = None           # the vault, newest first (None: no storage)
rows = None            # the vault's table rows: New note, then a note each
caption = ""           # "8 notes"
cur = 0                # the list's current row: 0 is New note, then a note each
asking = None          # None | "new" | "rename": the dialog that is up
typed = ""             # what has been typed into it
menu = False           # the current row's menu is up
paging = False         # a drag that began on the note belongs to the note


def _layout(w, h, fs):
    # The page reflows: it lays out in content_rect() every frame, so the
    # surface it is given is the whole one (the window's on the desk).
    global FS
    FS = max(1, fs)


def _init():
    _refresh()
    textmode(True)
    # A note the console was already asked to open -- Files routed a `.md`, a
    # `.json` or a `.txt` here. `open_editor()` with no name is that request,
    # and None when there was none.
    asked = open_editor()
    if asked is not None:
        _show(asked)
    else:
        last = prefs.get("last")
        if names and last in names:
            _open(last)


# -- the vault ---------------------------------------------------------------


def _refresh(keep=None):
    global names, cur, rows, caption
    got, err = files.list()
    names = None if err is not None else list(got)
    n = len(names) if names else 0
    rows = [("new", "New note")] + [(ICON.get(files.badge(m), "doc"), shown(m))
                                    for m in names or ()]
    caption = "%d note%s" % (n, "" if n == 1 else "s")
    if keep is not None and names and keep in names:
        cur = names.index(keep) + 1
    cur = max(0, min(cur, n))


def shown(name):
    """A name as the list shows it: a note's bare and in words, any other
    file's with its extension."""
    if "." in name:
        return name
    words = name.replace("_", " ")
    return words[:1].upper() + words[1:]


_titled = [None, "", ""]   # the name _title last saw, its shown name, and "... · saved"


def _title(name, saved=False):
    """`shown(name)`, made once a name (`ed.name()` is one object a note)."""
    t = _titled
    if t[0] is not name:
        t[0] = name
        t[1] = shown(name)
        t[2] = t[1] + " · saved"
    return t[2] if saved else t[1]


def _row():
    """The note the current row names, or None (New note)."""
    if not names or cur < 1 or cur > len(names):
        return None
    return names[cur - 1]


def _find(target):
    """The note a `[[link]]` names: by its name, or as the list shows it."""
    t = target.strip().lower()
    for n in names or ():
        if n.lower() == t or shown(n).lower() == t:
            return n
    return target


def _open(name):
    got = open_editor(name)
    if got is not None:
        _show(got)


def _show(handle):
    global ed
    _close()
    ed = handle
    ed.focus()                          # the handle takes the keyboard
    prefs.set("last", ed.name())
    _refresh(ed.name())


def _close():
    global ed
    if ed is not None:
        name = ed.name()
        ed.close()                      # saves on the way out: no Save button
        ed = None
        textmode(True)
        _refresh(name)


# -- the verbs on a row: Rename, Duplicate, Move to Trash ---------------------


def _ask(what):
    global asking, typed, menu
    menu = False
    asking = what
    typed = shown(_row())[:NAME_MAX] if what == "rename" else ""


def _answer():
    """The dialog's verb: make the new note, or rename the row's."""
    global asking
    what, asking = asking, None
    name = typed.strip()
    if what == "new":
        got, err = files.new_name(name or None)
        if err is None:
            _open(got)
    elif what == "rename" and name and _row() is not None:
        got, err = files.rename(_row(), name)
        _refresh(got if err is None else None)


def _duplicate():
    name = _row()
    if name is not None:
        got, err = files.duplicate(name)
        _refresh(got if err is None else name)


def _trash():
    name = _row()
    if name is not None:
        files.delete(name)              # to the Trash, which restores: nothing to ask
        _refresh()


# -- frame ---------------------------------------------------------------------


def _update(dt):
    global asking
    k = keyp()
    if not k:
        return
    if ed is not None:
        if k == KEY_ESC or btnp("b"):
            _close()
        return
    if asking is not None:
        if k == KEY_ESC:
            asking = None
        elif k in (KEY_CR, KEY_LF):
            _answer()                   # Enter makes
        elif k == KEY_BS and not typed:
            asking = None               # backspacing off the start backs out
        return
    if menu:
        return
    if k in (KEY_DEL, KEY_BS):
        _trash()
    elif k == KEY_DUP:
        _duplicate()
    elif k in (ord("n"), ord("N")):
        _ask("new")
    elif k in (ord("m"), ord("M")) and _row() is not None:
        _open_menu()
    elif k == KEY_ESC:
        quit()


def _open_menu():
    global menu
    menu = True


def _draw():
    x, y, w, h = content_rect()
    if w >= DESK_W:
        _desk(x, y, w, h)
    elif ed is not None:
        _note_page(x, y, w, h)
    else:
        _list_page(x, y, w, h)
    _tapping()


# -- the handheld's pages -------------------------------------------------------


def _list_page(x, y, w, h):
    global cur
    ui.bar("Notes")
    ui.at(x, y, w, h)
    ui.page("")
    if names is None:
        _blank(x, y, w, h, "No storage", NO_STORE)
        return
    if not names:
        _empty(x, y, w, h)
        _dialog()
        return
    _hints()
    _list(False)
    ui.end()
    _row_menu()
    _dialog()


def _hints():
    if asking is not None:
        ui.hints(("confirm", "Make" if asking == "new" else "Rename", "cancel", "Cancel"))
    elif menu:
        ui.hints(("confirm", "Choose", "cancel", "Close"))
    else:
        ui.hints(("confirm", "Open", "N", "New", "M", "Menu", "Del", "Trash"))


def _empty(x, y, w, h):
    if asking is None:
        ui.hints(("confirm", "New note", "back", "Back"))
    else:
        _hints()
    _blank(x, y, w, h, "No notes yet",
           ("A note can be a list, a story,", "a plan or a little program."), True)


def _blank(x, y, w, h, title, lines, verb=False):
    """An empty page: Notes' icon, what is missing and, with `verb`, New
    note under it -- a gap under the page's top on the handheld, a sixth of
    the way down on the desk."""
    desk = w >= DESK_W
    top = y + (h // 6 if desk else 12 * FS)
    ui.at(x, top, w, y + h - top)
    at = ui.empty("notes", title, lines)
    if verb:
        bw, bh = (180, 40) if desk else (124 * FS, 26 * FS)
        ui.at(x + (w - bw) // 2, at + (6 if desk else 3 * FS), bw, bh)
        ui.icon("new")
        if ui.button("New note", "primary"):
            _ask("new")


def _list(desk, x=0, y=0, w=0, h=0):
    """The vault as a table: New note first, then a row a note -- on the
    desk in its column (x, y, w, h), the rows on the window's ground."""
    global cur
    if desk:
        ui.at(x, y, w, h)
        cur, opened = ui.table(caption, ("", ""), rows, cur, VAULT_O)
    else:
        ui.group(caption)
        cur, opened = ui.table("", ("", ""), rows, cur, VAULT_O)
        ui.group()
    if opened or (desk and ed is not None and _row() is not None
                  and _row() != ed.name()):
        if cur == 0:
            _ask("new")
        elif _row() is not None:
            _open(_row())


def _row_menu():
    global menu
    if not menu:
        return
    got = ui.table_rows()
    if got is not None:
        y0, rh, view, _px, _side = got
        ui.at(view[0], y0 + cur * rh, view[2], rh)
    menu = ui.popover("row", 0, WASH_O)
    a = ui.menu("", ["Rename…", "Duplicate\tCtrl+D"], ["pencil", "dup"])
    b = ui.menu("", ["Move to Trash\tDel"], ["trash"], kinds=("danger",))
    ui.popover()
    if a == 0:
        _ask("rename")
    elif a == 1:
        menu = False
        _duplicate()
    elif b == 0:
        menu = False
        _trash()


def _dialog():
    global typed, asking
    if asking is None:
        return
    p = ui.dialog("New note" if asking == "new" else "Rename",
                  ("Cancel", "Make" if asking == "new" else "Rename"))
    typed, done = ui.field("", typed, NAME_MAX, FIELD_O)
    if asking == "new":
        ui.label("End the name with .py or .lua to make a program.", WRAP_O)
    ui.dialog()
    if p == 1 or done:
        _answer()
    elif p == 0:
        asking = None


def _note_page(x, y, w, h):
    """A note open: undo and redo lent to the bar, the note, the tools at the foot."""
    t = ui.bar(_title(ed.name()), ("undo", "redo"),
               OFF[(not ed.can_undo()) + 2 * (not ed.can_redo())], True)
    if t == 0:
        ed.undo()
    elif t == 1:
        ed.redo()
    elif t == -2:
        _close()
        return
    ui.at(x, y, w, h)
    ui.page("")
    foot = 34
    _sheet(x + 6, y + 3, w - 12, h - foot - 5, 8 * FS)
    fy = y + h - foot + 1
    for i in range(len(TOOLS)):
        ui.at(x + 6 + i * 46, fy, 42, 32)
        _tool(i)
    ui.at(x + w - 120, fy + 5, 114, 22)
    ui.info("", _status(), STATUS_O)


def _tool(i):
    icon, label = TOOLS[i]
    off = (icon in ("copy", "cut") and not ed.has_selection()) or \
        (icon == "paste" and not ed.can_paste())
    if ui.tool(icon, label, TOOL_O[1 if icon == "select" and ed.selecting() else 0][1 if off else 0]):
        if icon == "select":
            ed.select_mode()
        elif icon == "copy":
            ed.copy()
        elif icon == "cut":
            ed.cut()
        else:
            ed.paste()


_counted = [-1, ""]    # the selection's size _status last said, and its words


def _status():
    """The foot's word: a refusal, else what a selection holds, else the mode."""
    if ed.badge():
        return ed.badge()
    if ed.selecting():
        n = ed.selected_count()
        if n != _counted[0]:
            _counted[0] = n
            _counted[1] = "%d letter%s" % (n, "" if n == 1 else "s")
        return _counted[1]
    return _mode()


def _mode():
    m = ed.mode()
    return MODES.get(m, m)


def _sheet(x, y, w, h, r=0):
    """Where the note draws on its paper (corners `r`), and the rect a tap
    is forwarded through."""
    sheet[0] = x
    sheet[1] = y
    sheet[2] = w
    sheet[3] = h
    ed.draw(x, y, w, h, 1, r)


sheet = [0, 0, 0, 0]   # the note's rect as last drawn (w 0: never)


# -- the desk: the vault beside the note --------------------------------------


def _desk(x, y, w, h):
    """The vault as a column beside the note (Files' split), the note's tools
    over it, a foot under both: its rule, the note's state, the clipboard's
    keys."""
    ui.bar(_title(ed.name()) if ed is not None else "")
    ui.at(x, y, w, h)
    ui.page("")
    if names is None:
        _blank(x, y, w, h, "No storage", NO_STORE)
        return
    side = min(SIDE, w // 3)
    fy = y + h - FOOT
    ui.at(x + side, y, 1, h)
    ui.rule()
    ui.at(x + 4, fy, w - 8, 1)
    ui.rule()
    _list(True, x, y + 12, side - 4, fy - y - 12)
    px = x + side + 1
    if ed is not None:
        tx = px + 11
        for i, (icon, label) in enumerate((("undo", "Undo\tCtrl+Z"), ("redo", "Redo\tCtrl+Y"))):
            ui.at(tx + 40 * i, y + 8, 36, 36)
            off = not (ed.can_undo() if i == 0 else ed.can_redo())
            if ui.tool(icon, label, UNDO_O[off]):
                if i == 0:
                    ed.undo()
                else:
                    ed.redo()
        ui.at(tx + 86, y + 14, 1, 24)
        ui.rule()
        for i in range(len(TOOLS)):
            ui.at(tx + 96 + 40 * i, y + 8, 36, 36)
            _tool(i)
        ui.at(px, y + 8, x + w - px - 2, 36)
        ui.info("", ed.badge() or _mode(), MODE_O)
        ui.at(px, y + 52, x + w - px, 1)
        ui.rule()
        _sheet(px, y + 53, x + w - px, fy - y - 61)
        keys = ("Ctrl C", "Copy", "Ctrl X", "Cut", "Ctrl V", "Paste")
        hw = ui.hints_width(keys)
        ui.at(x + w - 4 - hw, fy + 5, hw, FOOT - 1)
        ui.hints(keys)
        ui.at(x + 4, fy + 9, side, FOOT - 1)
        ui.label(_title(ed.name(), True), DIM_O)
    else:
        _blank(px, y, x + w - px, fy - y, "Pick a note", ("Or make a new one.",))
    ui.end()
    _row_menu()
    _dialog()


# -- taps on the note: the draw pass is the hit map ---------------------------


def _tapping():
    global paging
    if ed is None or not sheet[2]:
        return
    t = touch()
    if t is None:
        return
    px, py, click, down = t
    sx, sy, sw, sh = sheet
    inside = sx <= px < sx + sw and sy <= py < sy + sh
    # A PRESS on the note asks the handle what the tap meant -- a tapped
    # [[link]] is another note, the one thing the page acts on; the checkbox
    # and the caret it handles itself. What follows the press is a DRAG, a
    # pan or, while selecting, a growing selection.
    if click and inside:
        got = ed.tap(px, py, True)
        paging = True
        if got is not None and got[0] == "link":
            paging = False
            _open(_find(got[1]))
    elif not down:
        if paging:
            ed.drag(px, py, False)
        paging = False
    elif paging:
        ed.drag(px, py, True)
