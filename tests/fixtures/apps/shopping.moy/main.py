# A Python app from docs/app_api_v1.md alone (sections 1, 3, 5): a list
# longer than the screen, which the ui engine scrolls, and the row last
# chosen kept in prefs across runs. tests/test_app_examples.py drives it.

ITEMS = ["Item %d" % n for n in range(1, 41)]
cur = 0


def _init():
    global cur
    cur = prefs.get("cur") or 0


def _draw():
    global cur
    ui.page("Shopping")
    new = ui.list("List", ITEMS, cur)
    if new != cur:
        cur = new
        prefs.set("cur", cur)
