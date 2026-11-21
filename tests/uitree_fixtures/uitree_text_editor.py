"""A page on the Editor's text editor that both engines draw: a find row
shown while finding, the document's rows (its gutter on a condition, the
find's matches marked, an error mark), a status line of what the document
shows its templates (`{code.line}`, `{code.matches}`), and a button that
turns the gutter off."""
import uitree as ui

TEXT = "".join("def f%d(x):\n    return x + %d\n\n" % (i, i) for i in range(12))

s = ui.state(
    code=ui.doc(TEXT, "python"),
    gutter_on=True, finding=False, find_q="", issue=False,
    marks=ui.model(key="line", line=0, kind=ui.enum("error", "crash", init="error"), words=""),
)


def gutter():
    s.gutter_on = not s.gutter_on


def find():
    s.finding = not s.finding
    s.find_q = "return" if s.finding else ""


def opened():
    s.code.set_text(TEXT)
    s.code.go(1, 1)
    s.gutter_on = True
    s.finding = False
    s.find_q = ""


ui.hook("opened", opened)
ui.action("gutter", "Gutter", gutter, keys=["g"], hint="Gutter")
ui.action("find", "Find", find, keys=["ctrl+f"], hint="Find")


def build():
    with ui.page(""):
        with ui.row("find_strip", shown="finding"):
            ui.field("", "find_q", 32, hint="Find")
            ui.status("{code.match} of {code.matches}", shown="find_q")
        ui.text_editor("code", find="find_q", find_on="finding", marks="marks", gutter="gutter_on",
                       focus=True)
        with ui.row("status_row"):
            ui.status("Ln {code.line}, Col {code.col} · {code.lines} lines · "
                      "{issue?1 issue:No issues}")
            ui.button("gutter", icon="linenums", alone=True, marked="gutter_on")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("te_" + ("c" if "C" in engine.__name__ else "ref"), "Text editor fixture",
                                build, engine=engine)
