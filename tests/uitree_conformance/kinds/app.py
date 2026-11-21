"""Conformance fixture `kinds` (level 2), the Python half: the kinds the C
engine draws beyond level 1 -- a seg over an enum worded by a label table, a
progress bar, a menu and its item, a table with its columns, a lines view --
and an action a table's row fires. Its Lua half is kinds.moy/main.lua."""
import uitree as ui

s = ui.state(mode=ui.enum("a", "b", "c", init="a"), done=2, log="one\ntwo",
             items=ui.model(key="id", id="", name="", size=0), sel=ui.row_of("items"), picked="")
ui.labels("MODES", {"a": "Apple", "b": "Bean", "c": "Corn"})


def opened():
    s.items = [{"id": "a", "name": "Apples", "size": 3}, {"id": "b", "name": "Bread", "size": 1}]


def pick(row):
    s.picked = "Picked " + row.name


def bump():
    s.done += 1


ui.hook("opened", opened)
ui.action("pick", "Pick", pick, takes="row")
ui.action("bump", "More", bump)


def build():
    with ui.page("Kinds"):
        ui.seg("Mode", "mode", labels="MODES")
        ui.progress("done", max=5, label="Done")
        with ui.menu("Do"):
            ui.item("bump")
        with ui.table("items", cur="sel", cols=["Name", "Size"], on="pick"):
            ui.col("{.name}")
            ui.col("{.size}")
        ui.text_view("log")
        ui.label("{picked}")
