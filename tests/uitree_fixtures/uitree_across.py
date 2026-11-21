"""A page on the Editor's placed kinds whose rows run the layout rule along
x, as the stack runs it down: a bar of fixed cells (a canvas `w=`, a word
button) and fills (a status line of fields, a canvas with no width), and a
row of regions that fills its stack, each region stacking its own children
in the row's whole height."""
import uitree as ui

s = ui.state(n=0, mode=ui.enum("words", "rows", init="words"))
DRAWN = []


def bump():
    s.n = s.n + 1


def flip():
    s.mode = "rows" if s.mode == "words" else "words"


def opened():
    s.n = 0
    s.mode = "words"


ui.hook("opened", opened)
ui.action("bump", "Bump", bump, hint="Bump")
ui.action("flip", "Flip", flip, hint="Flip")
ui.action("again", "Again", bump)


def _draw(cv, x, y, w, h):
    DRAWN.append((x, y, w, h))
    cv.rect(x + 2, y + 2, max(0, w - 4), max(0, h - 4) // 2, 9)


def _words():
    with ui.panel("note"):
        ui.label("Nothing here")
        ui.label("yet", tone="dim")


def _rows():
    ui.label("Rows mode")
    ui.button("again", label="Bump once")


def build():
    with ui.page(""):
        with ui.row("bar"):
            ui.canvas("logo", _draw, w=40, ground="surface")
            ui.button("flip", label="Flip")
            ui.status("Line {n} · {mode} · all fine · the last field")
            ui.canvas("meter", _draw, ground="panel")
            ui.button("bump", icon="add", alone=True)
        with ui.row("body"):
            with ui.panel("left"):
                ui.label("Left")
                ui.label("{n} bumps", tone="dim")
            ui.switch("mode", {"words": _words, "rows": _rows})
            ui.canvas("side", _draw, w=56, ground="surface")
        with ui.row("foot"):
            ui.status("{n} bumped")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("across_" + ("c" if "C" in engine.__name__ else "ref"), "Across fixture", build,
                                engine=engine)
