# The public UI API's Appearance verbs (docs/look_2026-10.md step 20) as a
# Python app: a tab row and a grid of cards whose pictures the app draws.
# look_lua.moy and tests/fixtures/wasm/app_look.moy draw the same page with
# the same calls, and tests/test_ui_parts.py holds the three to the same
# pixels. Into pmem: 0 frames, 1 the tab (from 0), 2 the card picked (from
# 0), 3 the cards activated, 5 the focused id's length, 7 the first card's
# picture's left.

tab = 0
picked = 0
acts = 0
frames = 0
names = ["Red", "Green", "Blue", "Grey"]
subs = ["one", "two", "three", "four"]
colours = [8, 11, 12, 6]


def _draw():
    global tab, picked, acts, frames
    x, y, w, h = ui.page("Look")
    tab = ui.tabs("Kind", ["Pictures", "Carts"], tab)
    ui.grid("cards", 60, 40)
    left = 0
    for i in range(4):
        cx, cy, cw, ch, st = ui.card(names[i], subs[i], {"current": i == picked})
        if i == 0:
            left = cx
        if cw > 0:
            rect(cx, cy, cw, ch, colours[i])
            rect(cx + 4, cy + 4, 8, 8, 7)
        if st & 2:
            picked = i
            acts += 1
    ui.grid()
    ui.link("After", "")
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, tab)
    pmem(2, picked)
    pmem(3, acts)
    pmem(5, len(f) if f else 0)
    pmem(7, left)
