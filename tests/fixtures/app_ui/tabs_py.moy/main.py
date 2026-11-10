# Tabs that switch with Q and E (docs/ui_api_2026-10.md §2, ui.tabs), as a
# Python app: tabs_lua.moy is the same app in Lua, and tests/test_app_ui.py
# holds both to the same tabs and pixels. The app reads no key of its own:
# the tab row takes Q and E wherever the keys do not type into the field.
# Into pmem: 0 the tab (from 1), 1 the field's length, 2 whether the ui took
# the frame's keys.

tab = 0
name = ""


def _layout(w, h, fs):
    pass


def _draw():
    global tab, name
    ui.page("Tabs")
    ui.hints(("tabs", "Switch"))
    tab = ui.tabs("Tabs", ("One", "Two", "Three"), tab)
    ui.label("Page %d" % (tab + 1))
    ui.button("Go", "primary")
    name = ui.field("Name", name, 8)[0]
    pmem(0, tab + 1)
    pmem(1, len(name))
    pmem(2, 1 if ui.keys_taken() else 0)
