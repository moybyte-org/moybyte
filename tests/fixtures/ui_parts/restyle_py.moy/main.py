# The public UI API's restyle verbs (docs/look_2026-10.md step 31) as a
# Python app: a verb, three tools (one current, one disabled), a rule and
# the bar's title and tools.
# restyle_lua.moy and tests/fixtures/wasm/app_restyle.moy draw the same page
# with the same calls, and tests/test_ui_parts.py holds the three to the same
# pixels. Into pmem: 0 frames, 1 the verb's activations, 2 the tools'
# (Select 1, Cut 10), 3 the bar's answer + 1, 5 the focused id's length.

makes = 0
tools = 0
frames = 0


def _draw():
    global makes, tools, frames
    ui.page("")
    ui.at(98, 120, 124, 26)
    ui.icon("new")
    if ui.button("Make", "primary"):
        makes += 1
    ui.at(0, 196, 320, 1)
    ui.rule()
    ui.at(10, 200, 42, 32)
    if ui.tool("select", "Select", {"current": True}):
        tools += 1
    ui.at(56, 200, 42, 32)
    ui.tool("copy", "Copy\tCtrl+C", {"disabled": True})
    ui.at(102, 200, 42, 32)
    if ui.tool("cut", "Cut"):
        tools += 10
    b = ui.bar("Title", ["undo", "redo"], [False, True])
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, makes)
    pmem(2, tools)
    pmem(3, b + 1)
    pmem(5, len(f) if f else 0)
