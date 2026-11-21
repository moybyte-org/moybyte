"""The Editor's Code tab as a `uitree` page: the cart's script in a text
editor over the console's own buffer, its find strip, completion, Go to,
the scripts menu, See why's popover, a status line of fields, and on a
touch glass the tool row and the symbol keys.

The document adopts the console's `CodeEditor` (`ws.editor`) and its undo
journal (`ws.history`'s code bursts): `CodeLayer` (code_layer.py) is the
host that adopts them, routes the keys and the pointer, and writes the
console's values this page shows (`sync`). Config, the theme mode's
JSON view, is `config_page` over the same state."""
import uitree as ui

import layout_base as _lb

# What the handlers reach: the workstation and the host (a CodeLayer), set
# by the host once.
_ws = [None]
_host = [None]

POPUP_MAX = 8                 # rows a completion or the scripts menu lists
NEW_SCRIPT = "New script"

s = ui.state(
    code=ui.doc("", "python"),
    file="",
    gutter_on=True,
    finding=False, find_q="", find_ci=True,
    over=ui.enum("none", "complete", "goto", "scripts", init="none"),
    cands=ui.model(key="word", word="", kind=""),
    cand=ui.row_of("cands"),
    goto_q="",
    defs=ui.model(key="at", at="", name="", line=0),
    def_at=ui.row_of("defs"),
    scripts=ui.model(key="name", name="", size="", new=False),
    script_at=ui.row_of("scripts"),
    marks=ui.model(key="line", line=0, kind=ui.enum("error", "crash", init="error"), words=""),
    issue=False,
    stopped="",
    read_only=False, ro_words="",
    touch=False, tools_open=False, select=False,
    full=True,
    note="", noted=False,
)
ui.derive("bare", cond="!finding && over=none && !stopped")     # nothing holds the keys but the code

# where the incremental search re-runs from, and Go to's definitions before
# its filter: handler memory, not shown
_anchor = [None]
_all_defs = []


def _ed():
    return _ws[0].editor


def _lead(line):
    n = 0
    while n < len(line) and line[n] == " ":
        n += 1
    return n


# -- find -----------------------------------------------------------------------

def find():
    """Ctrl+F (the tool row's Find, the desk pane's): the strip opens on
    the caret, where its search re-runs from, or closes."""
    s.over = "none"
    s.finding = not s.finding
    ed = _ed()
    if s.finding and ed is not None:
        _anchor[0] = (ed.row, ed.col)
    ui.focus("find_q" if s.finding else "code")
    _host[0].strip_moved()


def _find_run(forward, reset=False):
    ed = _ed()
    if ed is None or not s.find_q:
        return
    if reset and _anchor[0] is not None:
        # the query changed: the search starts again where find opened, a
        # match starting there taken; the buffer may have shrunk since
        r, c = _anchor[0]
        if r > len(ed.lines) - 1:
            r = len(ed.lines) - 1
        s.code.place(r + 1, min(c, len(ed.lines[r])) + 1)
    s.code.find(s.find_q, forward, reset, not s.find_ci)     # (query, forward, here, case)


def find_live():
    _find_run(True, reset=True)


def find_next():
    """Enter: the next match from the caret."""
    _find_run(True)


def find_prev():
    _find_run(False)


def find_close():
    s.finding = False
    ui.focus("code")
    _host[0].strip_moved()


def case():
    s.find_ci = not s.find_ci
    _find_run(True, reset=True)


# -- completion, Go to, the scripts ----------------------------------------------

def _kind(word, defs):
    import code_layer as _cl
    if word in defs:
        return "def"
    if word in _cl._HL_BUILTINS:
        return "verb"
    if word in _cl._HL_KEYWORDS or word in _cl._HL_LUA_KEYWORDS:
        return "word"
    return "var"


def complete_open():
    ed = _ed()
    if ed is None:
        return
    words = ed.completions(_host[0].api_names())[:POPUP_MAX]
    defs = [d[0] for d in ed.def_symbols()]
    s.cands = [{"word": w, "kind": _kind(w, defs)} for w in words]
    s.over = "complete" if words else "none"
    s.finding = False
    if words:
        s.cand = words[0]


def complete(row):
    s.code.complete(row.word)
    s.over = "none"
    ui.focus("code")


def _goto_rows():
    q = s.goto_q.lower()
    rows = [d for d in _all_defs if q in d["name"].lower()]
    return rows[:POPUP_MAX if _lb.metric_class() == _lb.DESK else 6]


def goto_open():
    ed = _ed()
    if ed is None:
        return
    del _all_defs[:]
    for name, row in ed.def_symbols():
        _all_defs.append({"at": str(row + 1), "name": name, "line": row + 1})
    s.goto_q = ""
    s.defs = _goto_rows()
    s.finding = False
    s.def_at = ""
    s.over = "goto" if _all_defs else "none"
    if _all_defs:
        ui.focus("goto_q")


def goto_filter():
    s.defs = _goto_rows()


def _go(line):
    ed = _ed()
    if ed is not None:
        ed.goto_row(line - 1, _lead(ed.lines[line - 1]))
    s.over = "none"
    ui.focus("code")


def goto(row):
    _go(row.line)


def goto_pick():
    """Enter in Go to's field: the definition its list has current."""
    rows = [r for r in s.defs if r.at == s.def_at] or list(s.defs)[:1]
    if rows:
        _go(rows[0].line)


def scripts_open():
    """A tap on the bar's Code tab while it is open: the cart's scripts,
    the open one current, New script last where a second one would run;
    a second tap closes it."""
    if s.over == "scripts":
        overlay_close()
        return
    ws = _ws[0]
    names = ws.text.code_sources()
    rows = []
    for n in names:
        size = ws.text.code_source_size(n)
        rows.append({"name": n, "size": ("%.1f KB" % (size / 1024.0)) if size is not None else "",
                     "new": False})
    if _host[0].can_add_script():
        rows.append({"name": NEW_SCRIPT, "size": "", "new": True})
    s.scripts = rows
    s.file = ws.text.code_file_name() or ""
    s.finding = False
    s.over = "scripts" if names else "none"
    if names:
        s.script_at = s.file if s.file in names else names[0]


def open_script(row):
    # closed before the switch: opening another file commits this buffer
    # and builds a new one
    s.over = "none"
    if row.new:
        _host[0].new_script()
    else:
        _ws[0].text.open_code_file(row.name)
    ui.focus("code")


def overlay_close():
    s.over = "none"
    ui.focus("code")


def stop_close():
    _ws[0].crash_popup = None
    s.stopped = ""


# -- the clipboard and the tools (a touch glass's row; the keys' chords) ----------

def select_all():
    s.code.select_all()


def copy():
    s.code.copy()


def cut():
    s.code.cut()


def paste():
    s.code.paste()


def tools():
    s.tools_open = not s.tools_open


def select_mode():
    s.select = not s.select
    ed = _ed()
    if ed is not None:
        ed.select_sticky = s.select
        if s.select:
            ed.begin_select()           # the anchor here: the arrows and a drag extend


def indent():
    s.code.indent()


def outdent():
    s.code.outdent()


def gutter():
    s.gutter_on = not s.gutter_on


def undo():
    _ws[0].history.undo()


def redo():
    _ws[0].history.redo()


def symbol(ev):
    """A symbol key tapped: into the find field while it is open, else
    into the code."""
    if ev.kind != "down":
        return
    syms = _host[0].symbols()
    i = _host[0].symbol_at(ev.x)
    if not 0 <= i < len(syms):
        return
    if s.finding:
        s.find_q = s.find_q + syms[i]
        find_live()
    else:
        s.code.insert(syms[i])


def console(v):
    """The console's values this page shows, handed by the host where they
    moved: (the error's row from 0 or None, its words, See why's text,
    read-only, its words, touch, the Code tab (not Config), Config's note
    and whether it is a note of its own rather than the status)."""
    row, err, stop, ro, ro_words, touch, full, (note, noted) = v
    s.marks = [] if row is None else [{"line": row + 1, "kind": "error", "words": err or ""}]
    s.issue = bool(err)
    s.stopped = stop
    s.read_only = ro
    s.ro_words = ro_words
    s.touch = touch
    s.full = full
    s.note = note
    s.noted = noted


def fresh():
    """A console's Code tab, made: the page as declared (one state a
    process, a console's own from its first frame)."""
    s.finding = False
    s.find_q = ""
    s.find_ci = True
    s.over = "none"
    s.gutter_on = True
    s.tools_open = False
    s.select = False
    s.goto_q = ""


def reset():
    """A fresh buffer: no overlay, no select mode."""
    s.finding = False
    s.over = "none"
    s.select = False


def set_find(q):
    s.find_q = q


def set_case(ci):
    s.find_ci = ci


ui.hook("console", console, takes="arg")
ui.hook("reset", reset)
ui.hook("fresh", fresh)
ui.hook("set_find", set_find, takes="arg")
ui.hook("set_case", set_case, takes="arg")
ui.action("find", "Find", find, keys=("ctrl+f", "role:search"), hint="")
ui.hook("find_live", find_live)
ui.action("find_next", "Next", find_next)
ui.action("find_prev", "Back", find_prev, keys=("shift+enter",), when="finding", off="hide")
ui.action("find_close", "Close", find_close)
ui.action("case", "Match case", case, hint="")
ui.action("complete_open", "Complete", complete_open, keys=("ctrl+e",), when="full", off="hide",
          hint="")
ui.action("complete", "Insert", complete, takes="row", row="cand", off="hide")
ui.action("goto_open", "Go to", goto_open, keys=("ctrl+g",), when="full", off="hide", hint="")
ui.hook("goto_filter", goto_filter)
ui.action("goto", "Go", goto, takes="row", row="def_at", off="hide")
ui.action("goto_pick", "Go", goto_pick)
ui.action("open_script", "Open", open_script, takes="row", row="script_at", off="hide")
ui.hook("scripts_open", scripts_open)
ui.action("overlay_close", "Close", overlay_close)
ui.action("stop_close", "Close", stop_close)
ui.action("select_all", "Select all", select_all, keys=("ctrl+a",), hint="")
ui.action("copy", "Copy", copy, keys=("ctrl+c",), hint="")
ui.action("cut", "Cut", cut, keys=("ctrl+x",), hint="")
ui.action("paste", "Paste", paste, keys=("ctrl+v",), hint="")
ui.action("tools", "Tools", tools, hint="")
ui.action("select_mode", "Select", select_mode, hint="")
ui.action("indent", "Indent", indent, hint="")
ui.action("outdent", "Outdent", outdent, hint="")
ui.action("gutter", "Line numbers", gutter, hint="")
ui.action("undo", "Undo", undo, hint="")
ui.action("redo", "Redo", redo, hint="")
ui.hook("symbol", symbol, takes="pointer")


# -- the page ---------------------------------------------------------------------

def _find_strip():
    with ui.row("find_strip", shown="finding", close="find_close"):
        ui.field("", "find_q", 64, changed="find_live", on="find_next", hint="Find")
        ui.status("{code.match} of {code.matches}", shown="find_q")
        ui.button("case", label="Aa", marked="!find_ci")
        ui.button("find_prev", icon="arrow_up", alone=True, shown="@desk")
        ui.button("find_next", icon="arrow_down", alone=True, shown="@desk")
        ui.button("find_close", icon="close", alone=True, shown="@desk")


def _stopped():
    with ui.popover("stopped", anchor="code.caret", w="{@desk?560:300}", close="stop_close",
                    shown="stopped"):
        ui.label("Stopped on this line", icon="warn", face="title")
        ui.label("{stopped}", face="mono", wrap="word", lines=4)
        ui.label("Your first edit closes this.", tone="dim", face="ui")


def code_page():
    with ui.page(""):
        ui.label("{ro_words}", wrap="word", lines=2, shown="read_only")
        _find_strip()
        ui.text_editor("code", find="find_q", find_on="finding", marks="marks",
                       gutter="gutter_on", focus=True)
        with ui.tools("tool_row", shown="touch && tools_open"):
            ui.button("select_mode", icon="select", alone=True, marked="select")
            ui.button("copy", icon="copy", alone=True)
            ui.button("cut", icon="cut", alone=True)
            ui.button("paste", icon="paste", alone=True)
            ui.button("find", icon="find", alone=True, marked="finding")
            ui.button("indent", icon="indent", alone=True)
            ui.button("outdent", icon="outdent", alone=True)
            ui.button("gutter", icon="linenums", alone=True, marked="gutter_on")
            ui.button("complete_open", label="AC")
            ui.button("goto_open", label="DEF")
            ui.button("undo", icon="undo", alone=True)
            ui.button("redo", icon="redo", alone=True)
        # a handheld's keys band stands in for the status line while the
        # find strip or an overlay holds the keys: the code keeps its rows
        with ui.row("status_row", shown="@desk || bare"):
            ui.status("Ln {code.line}, Col {code.col} · {code.lines} lines · "
                      "{issue?1 issue:No issues}")
            ui.button("tools", icon="tools", alone=True, shown="touch", marked="tools_open")
        ui.canvas("symbols", _draw_symbols, h=22, pointer="symbol", shown="touch", ground="surface")
        with ui.popover("complete", anchor="code.word", w="{@desk?300:150}", close="overlay_close",
                        shown="over=complete"):
            with ui.rows("cands", cur="cand"):
                ui.link("complete", "{.kind}", label="{.word}")
        with ui.popover("goto", anchor="{@desk?code.caret:code}", w="{@desk?420:228}", close="overlay_close",
                        shown="over=goto"):
            ui.field("", "goto_q", 32, changed="goto_filter", on="goto_pick", hint="Go to a definition")
            with ui.rows("defs", cur="def_at"):
                ui.link("goto", "line {.line}", label="{.name}")
        with ui.popover("scripts", anchor="code", w="{@desk?260:200}", close="overlay_close",
                        shown="over=scripts"):
            with ui.rows("scripts", cur="script_at", current="file"):
                ui.link("open_script", "{.size}", label="{.name}")
        _stopped()


def config_page():
    """Config: the theme's theme.json in the same editor, its status line
    the theme's (`note`)."""
    with ui.page(""):
        _find_strip()
        ui.text_editor("code", find="find_q", find_on="finding", marks="marks",
                       gutter="gutter_on", focus=True)
        with ui.row("status_row"):
            ui.status("{note}", shown="!noted")
            ui.label("{note}", icon="dup", face="caption", tone="value", shown="noted")


def _draw_symbols(cv, x, y, w, h):
    _host[0].draw_symbols(cv, x, y, w, h)
