"""Conformance fixture `list` (level 1), the Python half: a model filled at
open, rows with a cursor, a row action and a label it writes. Its Lua half
is list.moy/main.lua."""
import uitree as ui

s = ui.state(items=ui.model(key="id", id="", name=""), sel=ui.row_of("items"), msg="")


def opened():
    s.items = [{"id": "a", "name": "Apples"}, {"id": "b", "name": "Bread"}, {"id": "c", "name": "Cheese"}]


def pick(row):
    s.msg = "Picked " + row.name


ui.hook("opened", opened)
ui.action("pick", "Pick", pick, takes="row")


def build():
    with ui.page("Shopping"):
        with ui.group("Things"):
            with ui.rows("items", cur="sel"):
                ui.link("pick", label="{.name}")
            ui.label("{msg}")
