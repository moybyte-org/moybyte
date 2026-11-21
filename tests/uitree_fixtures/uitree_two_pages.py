"""Two pages of stops for the keys to cross between (`Engine.edge`,
`Engine.enter`): the first three buttons, the second two."""
import uitree as ui

s = ui.state(said=0)


def say():
    s.said += 1


for _name, _label in (("one", "One"), ("two", "Two"), ("three", "Three"), ("four", "Four"),
                      ("five", "Five")):
    ui.action(_name, _label, say)


def first():
    with ui.page("First"):
        ui.button("one")
        ui.button("two")
        ui.button("three")


def second():
    with ui.page("Second"):
        ui.button("four")
        ui.button("five")
