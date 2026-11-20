"""A table over a model, its current row named by cur=, and a foot whose
buttons share its width under a line of its own (`foot look="share"`);
the table focused by its key when the page opens."""
import uitree as ui

s = ui.state(log=ui.model(key="id", id="", line=""), at=ui.row_of("log"), said="")


def opened():
    s.log = [{"id": "r%d" % i, "line": "line %d" % i} for i in range(3)]
    s.said = "a line of its own"
    ui.focus("log")


def mark(row):
    s.said = "marked " + row.id


def drop(row):
    s.log = [r for r in s.log if r.id != row.id]


ui.app("Foot", icon="check", min=(160, 120))
ui.hook("opened", opened)
ui.action("mark", "Mark", mark, takes="row", row="at", off="hide")
ui.action("drop", "Remove", drop, takes="row", row="at", off="hide")


def foot_page():
    with ui.page("Foot"):
        with ui.table("log", cols=("Line",), cur="at"):
            ui.col("{.line}")
        with ui.foot(look="share"):
            ui.label("{said}")
            ui.button("mark")
            ui.button("drop", "danger")
