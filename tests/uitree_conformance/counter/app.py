"""Conformance fixture `counter` (level 1), the Python half: a count, an
action, an action that is off while the count is 0, a toggle and the
templates that show them. Its Lua half is counter.moy/main.lua."""
import uitree as ui

s = ui.state(count=0, loud=False)


def add():
    s.count += 1


def reset():
    s.count = 0


ui.action("add", "Add one", add)
ui.action("reset", "Reset", reset, when="count", off="grey")


def build():
    with ui.page("Counter"):
        with ui.group("Count"):
            ui.info("Count", "{count}")
            ui.toggle("Loud", "loud")
            ui.link("add")
            ui.link("reset")
            ui.label("{count|plural press,presses} so far, {loud?loud:quiet}")
