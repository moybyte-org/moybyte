"""The rule along x's pieces a bar is made of: a row's pad= and gap= (a
literal in the page's unit, an int state in pixels), a canvas's w= from an
int state, a row in a row (its own gap; an empty one a spacer pushing what
follows to the end), and a mid= row centred on its row, of fills (the
widest span centred that clears both sides) or of fixed cells (each side's
fills sharing that side's room alone)."""
import uitree as ui

s = ui.state(n=0, cw=40, sg=6)
DRAWN = []


def bump():
    s.n = s.n + 1
    s.cw = s.cw + 12
    s.sg = s.sg + 3


def opened():
    s.n = 0
    s.cw = 40
    s.sg = 6


ui.hook("opened", opened)
ui.action("bump", "Bump", bump, hint="Bump")
ui.action("flip", "Flip", bump, hint="Flip")


def _draw(cv, x, y, w, h):
    DRAWN.append((x, y, w, h))
    cv.rect(x + 1, y + 1, max(0, w - 2), max(0, h - 2) // 2, 9)


def build():
    with ui.page(""):
        with ui.row("bar", pad=2, gap=2):
            ui.button("bump", icon="add", alone=True)
            ui.canvas("zone", _draw, ground="surface")
            with ui.row("chips", mid=True):
                ui.canvas("chip", _draw, ground="panel")
            ui.canvas("clock", _draw, w="{cw}", ground="surface")
            with ui.row("tray", gap="{sg}"):
                ui.canvas("wifi", _draw, w=16, ground="panel")
                ui.canvas("batt", _draw, w=16, ground="panel")
        with ui.row("mid_bar", pad=4):
            ui.canvas("lfill", _draw, ground="surface")
            with ui.row("centre", mid=True, gap=3):
                ui.canvas("title", _draw, w=60, ground="panel")
                ui.button("flip", label="Flip")
            ui.canvas("rfill", _draw, ground="surface")
            ui.canvas("rfix", _draw, w=24, ground="panel")
        with ui.row("ends"):
            ui.canvas("lead", _draw, w=30, ground="panel")
            with ui.row("space"):
                pass
            ui.canvas("tail", _draw, w="{cw}", ground="panel")
        ui.canvas("body", _draw, ground="surface")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("across_bar_" + ("c" if "C" in engine.__name__ else "ref"), "Across bar fixture",
                                build, engine=engine)
