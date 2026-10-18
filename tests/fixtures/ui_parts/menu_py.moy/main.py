# The public UI API's menu (docs/look_2026-10.md step 25a) as a Python app:
# a card centred on the page (a popover with nothing to hang from) holding a
# header row, a menu of four and its hints, then a captioned menu stacked on
# the page. menu_lua.moy and tests/fixtures/wasm/app_menu.moy draw the same
# page with the same calls, and tests/test_ui_parts.py holds the three to the
# same pixels. Into pmem: 0 frames, 1 whether the card is open, 2 the card's
# item activated last (from 1), 3 its activations, 5 the focused id's length,
# 7 the page menu's item activated last (from 1).

shown = True
last = 0
picks = 0
sys_last = 0
frames = 0


def _draw():
    global shown, last, picks, sys_last, frames
    ui.page("Menu")
    if shown:
        shown = ui.popover("pause", 200)
        ui.icon("moy")
        ui.info("Star", "Paused")
        i = ui.menu("", ["Resume", "Restart", "Options", "Quit"],
                    ["play", "rotate", "settings", "close"])
        ui.hints(["enter", "Choose", "Esc", "Resume"])
        ui.popover()
        if i >= 0:
            last = i + 1
            picks += 1
            if i == 3:
                shown = False
    j = ui.menu("System", ["Search", "Settings"], ["search", "settings"])
    if j >= 0:
        sys_last = j + 1
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, 1 if shown else 0)
    pmem(2, last)
    pmem(3, picks)
    pmem(5, len(f) if f else 0)
    pmem(7, sys_last)
