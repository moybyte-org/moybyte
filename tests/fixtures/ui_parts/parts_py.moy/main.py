# The public UI API's parts page (docs/look_2026-10.md step 12), as a Python
# app. parts_lua.moy and tests/fixtures/wasm/app_parts.moy draw the same page
# with the same calls, and tests/test_ui_parts.py holds the three to the same
# pixels. Into pmem: 0 frames, 1 the link's activations, 2 the size (from 0),
# 3 the mode, 4 the accent, 5 the focused id's length, 7 the content's top,
# 8 the name's length, 9 the times Enter was pressed in it, 10 the level.

opens = 0
size = 1
mode = 0
accent = 0
name = "pic"
dones = 0
level = 3
frames = 0


def _draw():
    global opens, size, mode, accent, name, dones, level, frames
    x, y, w, h = ui.page("Parts")
    ui.hints(["confirm", "Open", "change", "Change"])
    ui.group("General")
    ui.icon("wifi")
    if ui.link("Wi-Fi", "Home"):
        opens += 1
    size = ui.cycle("Size", ["S", "M", "L"], size)
    ui.info("Board", "T-Deck")
    ui.progress("Update", 3, 4)
    ui.group("Look")
    mode = ui.seg("Mode", ["Day", "Night"], mode)
    accent = ui.swatches("Accent", [0xff0000, 0x00c000, 0x0000ff], accent)
    name, d = ui.field("Name", name, 8)
    if d:
        dones += 1
    level = ui.slider("Level", level, 0, 7, {"steps": True})
    ui.group()
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, opens)
    pmem(2, size)
    pmem(3, mode)
    pmem(4, accent)
    pmem(5, len(f) if f else 0)
    pmem(7, y)
    pmem(8, len(name))
    pmem(9, dones)
    pmem(10, level)
