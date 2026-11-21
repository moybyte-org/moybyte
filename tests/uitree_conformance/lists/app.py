"""Conformance fixture `lists` (level 2), the Python half: a tab strip bound
to an enum, rows of links, and the same rows as cards
whose card fires an action with its row. Its Lua half is
lists.moy/main.lua."""
import uitree as ui

s = ui.state(tab=ui.enum("all", "few", init="all"), picked="",
             items=ui.model(key="id", id="", name="", kind=ui.enum("fruit", "bread", init="fruit")),
             sel=ui.row_of("items"))
ui.labels("TABS", {"all": "All", "few": "Few"})


def opened():
    s.items = [{"id": "apple", "name": "Apples", "kind": "fruit"}, {"id": "bread", "name": "Bread", "kind": "bread"},
               {"id": "pear", "name": "Pears", "kind": "fruit"}]


def pick(row):
    s.picked = "Picked " + row.name


def open_(row):
    s.picked = "Opened " + row.id


ui.hook("opened", opened)
ui.action("pick", "Pick", pick, takes="row")
ui.action("open", "Open", open_, takes="row")


def build():
    with ui.page("Lists"):
        ui.tabs("tab", "TABS")
        with ui.group("Things"):
            with ui.rows("items"):
                ui.link("pick", label="{.name}")
        with ui.group("Shelf"):
            with ui.cards("items", cur="sel", w=96, h=64):
                ui.card("open", label="{.name}", sub="{.kind}")
        ui.label("{picked}")
