"""Conformance fixture `gates` (level 1), the Python half: an action hidden
while a toggle is off (off="hide"), one greyed while a count is 0
(off="grey"), and a node shown by a condition. Its Lua half is
gates.moy/main.lua."""
import uitree as ui

s = ui.state(open=False, n=0)


def add():
    s.n += 1


def take():
    s.n -= 1


ui.action("add", "Add one", add, when="open", off="hide")
ui.action("take", "Take one", take, when="n", off="grey")


def build():
    with ui.page("Gates"):
        with ui.group("Gate"):
            ui.toggle("Open", "open")
            ui.link("add")
            ui.link("take")
            ui.label("{n} inside", shown="open")
