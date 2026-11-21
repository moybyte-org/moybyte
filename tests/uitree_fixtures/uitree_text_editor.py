"""A page on the Editor's text editor that both engines draw: a find row
shown while finding, which Back closes (close=), the document's rows (its gutter on a condition, the
find's matches marked, an error mark), a status line of what the document
shows its templates (`{code.line}`, `{code.matches}`), and a button that
turns the gutter off, and a popover hung from the caret whose list of
one link a row is a menu."""
import uitree as ui

TEXT = "".join("def f%d(x):\n    return x + %d\n\n" % (i, i) for i in range(12))

s = ui.state(
    code=ui.doc(TEXT, "python"),
    gutter_on=True, finding=False, find_q="", issue=False,
    marks=ui.model(key="line", line=0, kind=ui.enum("error", "crash", init="error"), words=""),
    picking=False, cands=ui.model(key="word", word="", kind=""), cand=ui.row_of("cands"),
)


def gutter():
    s.gutter_on = not s.gutter_on


def find():
    s.finding = not s.finding
    s.find_q = "return" if s.finding else ""


def find_close():
    s.finding = False
    s.find_q = ""


def pick_open():
    s.cands = [{"word": w, "kind": k} for w, k in (("return", "kw"), ("range", "fn"), ("repr", "fn"))]
    s.cand = "range"
    s.picking = True


def pick(row):
    s.code.insert(row.word)
    s.picking = False


def pick_close():
    s.picking = False


def opened():
    s.picking = False
    s.code.set_text(TEXT)
    s.code.go(1, 1)
    s.gutter_on = True
    s.finding = False
    s.find_q = ""


ui.hook("opened", opened)
ui.action("gutter", "Gutter", gutter, keys=["g"], hint="Gutter")
ui.action("find", "Find", find, keys=["ctrl+f"], hint="Find")
ui.action("find_close", "Close", find_close, hint="Close find")
ui.action("pick_open", "Pick", pick_open, keys=["ctrl+e"])
ui.action("pick", "Insert", pick, takes="row", hint="Insert")
ui.action("pick_close", "Close", pick_close, hint="Close")


def build():
    with ui.page(""):
        with ui.row("find_strip", shown="finding", close="find_close"):
            ui.field("", "find_q", 32, hint="Find")
            ui.status("{code.match} of {code.matches}", shown="find_q")
        ui.text_editor("code", find="find_q", find_on="finding", marks="marks", gutter="gutter_on",
                       focus=True)
        with ui.row("status_row"):
            ui.status("Ln {code.line}, Col {code.col} · {code.lines} lines · "
                      "{issue?1 issue:No issues}")
            ui.button("gutter", icon="linenums", alone=True, marked="gutter_on")
        with ui.popover("picks", anchor="code.caret", w="{@desk?300:150}", close="pick_close",
                        shown="picking"):
            with ui.rows("cands", cur="cand"):
                ui.link("pick", "{.kind}", label="{.word}")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("te_" + ("c" if "C" in engine.__name__ else "ref"), "Text editor fixture",
                                build, engine=engine)
