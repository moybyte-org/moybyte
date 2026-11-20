"""A small editor on `uitree`'s Editor kinds for the hosting tests: tabs
over a text editor and a notes tab, a find strip, completion hung from the
caret's word, a status line, and on the desk two panes with a divider."""
import uitree as ui

TEXT = ("def init():\n"
        "    global x\n"
        "    x = 60\n\n"
        "def update():\n"
        "    x = x + 1\n\n"
        "def draw():\n"
        "    cls(1)\n"
        "    spr(1, x, 110)\n")
API = ("btn", "cls", "spr", "print", "rect")

s = ui.state(
    tab=ui.enum("code", "notes", init="code"),
    side_w=ui.opt(0),
    code=ui.doc(TEXT, "python"),
    finding=False, find_q="",
    completing=False,
    cands=ui.model(key="word", word=""),
    cand=ui.row_of("cands"),
    marks=ui.model(key="line", line=0, kind=ui.enum("error", "warn", init="error"), words=""),
    played=0,
)

ui.labels("TABS", {"code": "Code", "notes": "Notes"})


def opened():
    s.tab = "code"
    s.code.set_text(TEXT)
    s.finding = False
    s.find_q = ""
    s.completing = False
    s.marks = []
    s.played = 0


def play():
    s.played += 1
    try:
        compile(s.code.text(), "main.py", "exec")
        s.marks = []
    except SyntaxError as e:
        s.marks = [{"line": e.lineno or 1, "kind": "error", "words": e.msg}]


def undo():
    s.code.undo()


def find():
    s.finding = True
    ui.focus("find_q")


def find_live():
    s.code.find(s.find_q, True, True)


def find_next():
    s.code.find(s.find_q, True)


def find_close():
    s.finding = False
    ui.focus("code")


def complete_open():
    s.cands = [{"word": w} for w in s.code.completions(API)[:6]]
    s.completing = bool(len(s.cands))


def complete(row):
    s.code.complete(row.word)
    s.completing = False
    ui.focus("code")


def complete_close():
    s.completing = False
    ui.focus("code")


ui.hook("opened", opened)
ui.hook("find_live", find_live)
ui.action("play", "Play", play, keys=("ctrl+r", "role:play"))
ui.action("undo", "Undo", undo, keys=("ctrl+z",), when="code.can_undo", off="grey")
ui.action("find", "Find", find, keys=("ctrl+f",))
ui.action("find_next", "Next", find_next)
ui.action("find_close", "Close", find_close)
ui.action("complete_open", "Complete", complete_open, keys=("ctrl+e",), when="code.word", off="hide")
ui.action("complete", "Use", complete, takes="row", row="cand", off="hide")
ui.action("complete_close", "Close", complete_close)


def code_body():
    with ui.row("find_strip", shown="finding", close="find_close"):
        ui.field("", "find_q", 32, changed="find_live", on="find_next", hint="Find")
        ui.status("{code.match} of {code.matches}", shown="find_q")
        ui.button("find_close", icon="close", alone=True)
    ui.text_editor("code", find="find_q", find_on="finding", marks="marks", gutter=True, focus=True)
    ui.status("Ln {code.line}, Col {code.col}  {code.lines} lines")
    with ui.popover("completion", anchor="code.word", w="{@desk?220:150}", close="complete_close",
                    shown="completing"):
        with ui.rows("cands", cur="cand"):
            ui.link("complete", label="{.word}")


def notes_body():
    with ui.panel("notes_area"):
        ui.text("Played {played}", tone="dim")


def build():
    with ui.page("Editor"):
        with ui.panel("hand", shown="@handheld"):
            with ui.row("head"):
                ui.tabs("tab", "TABS")
                ui.button("undo", icon="undo", alone=True)
                ui.button("play", "primary", icon="play", alone=True)
            ui.switch("tab", {"code": code_body, "notes": notes_body})
        with ui.panel("desk", shown="@desk"):
            with ui.row("desk_head"):
                ui.button("undo", icon="undo", alone=True)
                ui.button("play", "primary", icon="play", label="Play")
            with ui.panes("row"):
                with ui.pane("code_pane", title="main.py", icon="code", grow=True, min=(64, 48)):
                    with ui.tools("code_head"):
                        ui.button("find", icon="find", alone=True)
                    code_body()
                with ui.pane("side", title="Notes", size="side_w", start=260, min=(120, 48)):
                    notes_body()


def layer():
    import uitree_app
    import uitree_editor
    return uitree_app.app_layer("uitree_editor", "Editor fixture", build,
                                engine=uitree_editor.Engine)
