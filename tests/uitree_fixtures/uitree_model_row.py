"""A row from a model (`ui.rows(dir="row")` in a placed `ui.row`): a cell per row of a
list state along x, each its button's natural width from that row's text;
`marked=` a row field. Too many cells for the row's room cut the widest
first, never under the row's height, then the last drop. Add grows the
list, Mark moves the marked row, Long lengthens a title."""
import uitree as ui

s = ui.state(wins=ui.model(key="k", k="", title="", on=False), n=2)

_TITLES = ("Settings", "Editor", "Star Catcher", "Files", "Licences and more", "Tasks", "Wi-Fi")


def _rows(n, on):
    return [{"k": "w%d" % i, "title": _TITLES[i % len(_TITLES)], "on": i == on} for i in range(n)]


def opened():
    s.n = 2
    s.wins = _rows(2, 0)


def add():
    s.n += 2
    s.wins = _rows(s.n, 1)


def mark(row):
    s.wins = [dict(k=r.k, title=r.title, on=r.k == row.k) for r in s.wins]


def long():
    s.wins = [dict(k=r.k, title=r.title + " with a long name", on=r.on) for r in s.wins]


ui.hook("opened", opened)
ui.action("add", "Add", add)
ui.action("long", "Long", long)
ui.action("chip", "Window", mark, takes="row")


def build():
    with ui.page(""):
        with ui.row("bar", pad=2, gap=4):
            ui.button("add", kind="bar", label="Add", w=28)
            with ui.rows("wins", dir="row"):
                ui.button("chip", label="{.title}", kind="bar", marked=".on")
            ui.button("long", kind="bar", label="Long", w=36)
        ui.canvas("body", lambda cv, x, y, w, h: None, ground="surface")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("model_row_" + ("c" if "C" in engine.__name__ else "ref"), "Model row fixture",
                                build, engine=engine)
