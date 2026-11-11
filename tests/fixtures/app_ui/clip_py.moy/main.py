# Tables of parts whose rows hold placed ui verbs (docs/ui_api_2026-10.md
# §2, "A table of parts"), as a Python app: clip_lua.moy is the same app in
# Lua, and tests/test_app_ui.py holds both to the tables' views. Each view
# is a row and a half short of whole rows; the left opens on its first row
# and the right on its last, so a row shows cut at the left's foot and at
# the right's top. Into pmem 0..7: each view's x, y, w and h.

rows = [["row %d" % (i + 1)] for i in range(10)]
cur = [0, 9]
_cell = [0, 0, 0, 0]


def _layout(w, h, fs):
    pass


def _draw():
    x, y, w, _h = ui.page("Clip")
    vw = (w - 24) // 2
    for t in range(2):
        vx, vy, vh = x + 8 + t * (vw + 8), y + 30, 60
        ui.at(vx, vy, vw, vh)
        cur[t] = ui.table("", [""], rows, cur[t], {"id": "rows%d" % (t + 1), "part": (0, 24)})[0]
        i = ui.table_part(_cell)
        while i >= 0:
            cx, cy, cw, ch = _cell
            ui.at(cx, cy, cw - 40, ch)
            ui.label(rows[i][0])
            ui.at(cx + cw - 36, cy + 2, 32, ch - 4)
            ui.button("Go", "primary")
            i = ui.table_part(_cell)
        pmem(4 * t, vx)
        pmem(4 * t + 1, vy)
        pmem(4 * t + 2, vw)
        pmem(4 * t + 3, vh)
