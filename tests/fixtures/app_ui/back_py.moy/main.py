# Back that steps out of an app's own views before it leaves the app
# (docs/app_api_v1.md section 4, "Back steps out of your views first"), as a
# Python app: back_lua.moy is the same app in Lua, and tests/test_app_ui.py
# holds both to the same steps. Its first page opens a second page, and the
# second a popover; while the second page shows, the app lends the bar its
# way back (ui.bar's `back`). Into pmem: 0 the page (1 or 2), 1 whether the
# popover shows, 2 how many times the bar's way back reached the app.

page = 1
pop = False
ups = 0


def _layout(w, h, fs):
    pass


def _draw():
    global page, pop, ups
    if ui.bar("Details" if page == 2 else "", (), (), page == 2) == ui.UP:
        page = 1
        ups += 1
    ui.page("Details" if page == 2 else "Back")
    if page == 1:
        if ui.button("Details", "primary"):
            page = 2
    else:
        if ui.button("More", "primary"):
            pop = True
        if pop:
            pop = ui.popover("more", 120)
            ui.label("In the popover")
            ui.popover()
    pmem(0, page)
    pmem(1, 1 if pop else 0)
    pmem(2, ups)
