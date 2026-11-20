"""Planted mistakes, one a line, for the checker's test: each line that
holds one ends with its code, and the checker reports every one in a
single run."""
import uitree as ui

s = ui.state(count=0, name="", items=ui.model(key="id", id="", label=""), sel=ui.row_of("items"))


def add():
    s.count += 1


def pick(row):
    row.label = "x"  # S03


def nothing():
    pass


ui.action("add", "Add one", add)
ui.action("clear", "Clear", nothing, when="count")  # O01
ui.action("pick", "Pick", pick, takes="row")
ui.action("bad", "", nothing)  # A08
ui.hook("tick", nothing)


def build():
    with ui.page("Mistakes"):
        ui.label("{count}")
        ui.link("ad")  # A01
        ui.link("tick")  # H01
        ui.toggle("On", "count")  # T03
        ui.info("Name", "{nam}")  # T02
        ui.label(s.name)  # S05
        with ui.rows("items", cur="sel"):
            ui.link("add", label="{.label}")  # A02
            ui.link("pick", label="{.label}")
        ui.label("{count}", colour="red")  # N01
