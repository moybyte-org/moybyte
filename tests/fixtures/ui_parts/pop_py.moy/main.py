# The public UI API's popover (docs/look_2026-10.md step 24) as a Python
# app: a button opens a popover hung under it, holding a seg and a button
# that closes it. pop_lua.moy and tests/fixtures/wasm/app_pop.moy draw the
# same page with the same calls, and tests/test_ui_parts.py holds the three
# to the same pixels. Into pmem: 0 frames, 1 whether the popover is open, 2
# the size (from 0), 3 the closes, 5 the focused id's length, 7 the link
# under it activated.

shown = False
size = 0
closes = 0
under = 0
frames = 0


def _draw():
    global shown, size, closes, under, frames
    ui.page("Pop")
    if ui.button("Pick"):
        shown = True
    if ui.link("Under", ""):
        under += 1
    if shown:
        shown = ui.popover("pick", 200)
        size = ui.seg("Size", ["1x1", "2x2", "3x3"], size)
        if ui.button("Done"):
            shown = False
            closes += 1
        ui.popover()
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, 1 if shown else 0)
    pmem(2, size)
    pmem(3, closes)
    pmem(5, len(f) if f else 0)
    pmem(7, under)
