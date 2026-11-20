"""Conformance fixture `pages` (level 1), the Python half: a dialog shown by
a state, its two buttons, and a pushed page Back pops. Its Lua half is
pages.moy/main.lua."""
import uitree as ui

s = ui.state(count=0, asking=False)


def add():
    s.count += 1


def ask():
    s.asking = True


def keep():
    s.asking = False


def reset():
    s.count = 0
    s.asking = False


def more():
    ui.push(details)


ui.action("add", "Add one", add)
ui.action("ask", "Reset", ask)
ui.action("keep", "Keep", keep)
ui.action("reset", "Reset", reset)
ui.action("more", "Details", more)


def build():
    with ui.page("Pages"):
        with ui.group("Count"):
            ui.info("Count", "{count}")
            ui.link("add")
            ui.link("ask")
            ui.link("more")
        with ui.dialog("Reset the count?", ["keep", "reset"], shown="asking"):
            ui.label("It goes back to 0.")


def details():
    with ui.page("Details"):
        with ui.group("Now"):
            ui.info("Count", "{count}")
