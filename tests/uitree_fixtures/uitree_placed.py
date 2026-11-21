"""A page on the Editor's placed kinds that both engines draw: a wrapped
label, a row of a field, a count and buttons, a status line of fields, a
handheld's tool row, a canvas of a fixed height over its ground, a panel
of words that fills, and a switch whose cases stack their own rows."""
import uitree as ui

s = ui.state(
    q="", n=0, tools_open=True, mode=ui.enum("words", "rows", init="words"),
    marked=False,
)
DRAWN = []


def bump():
    s.n = s.n + 1
    s.marked = not s.marked


def flip():
    s.mode = "rows" if s.mode == "words" else "words"


def opened():
    s.q = ""
    s.n = 0
    s.tools_open = True
    s.mode = "words"
    s.marked = False


ui.hook("opened", opened)
ui.action("bump", "Bump", bump, hint="Bump")
ui.action("flip", "Flip", flip, hint="Flip")
ui.action("look", "Look", bump)
ui.action("copy", "Copy", bump)
ui.action("paste", "Paste", flip)


def _draw(cv, x, y, w, h):
    DRAWN.append((x, y, w, h))
    cv.rect(x + 2, y + 2, max(0, w - 4), max(0, h - 4) // 2, 9)


def _words():
    with ui.panel("note"):
        ui.label("Nothing here")
        ui.label("yet", tone="dim")


def _rows():
    ui.label("Rows mode")
    ui.button("look", label="Bump once")
    ui.slot("dock")


def build():
    with ui.page(""):
        ui.label("A page placed by the engine's one rule, its words wrapped to two lines here",
                 wrap="word", lines=2)
        with ui.row("find_row"):
            ui.field("", "q", 24, hint="Find")
            ui.status("{n} found", shown="q")
            ui.button("bump", label="Aa", marked="marked")
            ui.button("flip", icon="arrow_down", alone=True)
        ui.switch("mode", {"words": _words, "rows": _rows})
        with ui.tools("tool_row", shown="tools_open"):
            ui.button("copy", icon="copy", alone=True)
            ui.button("paste", icon="paste", alone=True)
            ui.button("look", label="LOOK", shown="mode=words")
        with ui.row("status_row"):
            ui.status("Line {n} · {mode} · all fine · the last field")
        ui.canvas("pad", _draw, h=22, ground="surface")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("placed_" + ("c" if "C" in engine.__name__ else "ref"), "Placed fixture", build, engine=engine)
