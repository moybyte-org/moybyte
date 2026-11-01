# The public UI API's fixture page (docs/ui_api_2026-10.md), as a Python app.
# page_lua.moy and tests/fixtures/wasm/app_ui.moy draw the same page with the
# same calls, and tests/test_ui_public.py holds the three to the same pixels.
# Into pmem: 0 frames, 1 sound, 2 volume, 3 the theme row (from 0), 4 saves,
# 5 keys taken, 6 the swatch's state, 7 the focused id's length, 10 the
# focused pane (from 0), 11 the first pane's size, 12 where a tap activated the
# second pane's part (x * 256 + y + 1; 0 none), 13 where the pointer held on
# it last was while pressed (the same; a part with nav off keeps a held
# press), 14 the row of parts' current row. A table at the right draws its
# rows as parts (ui.table_part); a row of parts above the Save button lies
# side by side (o.axis "x"), its view resting on a part's centre (o.snap),
# each part shorter by its distance from the centre (ui.table_dist); two
# panes at the foot (ui.split) have a divider the script drags and a part in
# the second it taps and drags across (ui.part_at).

sound = True
vol = 5
cur = 1
saves = 0
frames = 0
_cell = [0, 0, 0, 0]    # a part's cell (ui.table_part)
_sizes = [80, 0]        # the panes' sizes (ui.split)
_spl = [0] * 12         # their rects and the divider's words
_pane = 0
_tap = 0
_held = 0
_xcur = 0
_ROW = [["a"], ["b"], ["c"], ["d"], ["e"]]
_XO = {"id": "row", "part": (30, 26), "axis": "x", "snap": True}


def _draw():
    global sound, vol, cur, saves, frames, _pane, _tap, _held, _xcur
    ui.page("UI page")
    ui.label("Hello")
    sound = ui.toggle("Sound", sound)
    vol = ui.slider("Volume", vol, 0, 10)
    cur = ui.list("Theme", ["Moy", "Night", "Berry"], cur)
    x, y, w, h, st = ui.part("swatch", 0, 16)
    rect(x + 2, y + 2, w - 4, h - 4, 8)
    ui.at(200, 214, 100, 20)
    if ui.button("Save", "primary"):
        saves += 1
    ui.at(230, 2, 80, 34)
    ui.table("", ("",), [["a"], ["b"], ["c"]], 1, {"id": "parts", "part": (0, 17), "bar": True})
    i = ui.table_part(_cell)
    while i >= 0:
        rect(_cell[0] + 4, _cell[1] + 3, 20, _cell[3] - 6, 9 + i)
        i = ui.table_part(_cell)
    ui.at(200, 182, 110, 28)
    _xcur = ui.table("", ("",), _ROW, _xcur, _XO)[0]
    i = ui.table_part(_cell)
    while i >= 0:
        d = ui.table_dist()
        rect(_cell[0] + 3, _cell[1] + 2, _cell[2] - 6, _cell[3] - 4 - ((d * d) >> 16), 9 + i)
        i = ui.table_part(_cell)
    ui.at(10, 214, 180, 20)
    _pane = ui.split("panes", "x", _sizes,
                     {"drag": True, "focus": _pane, "gap": 6, "mins": (40, 40)}, _spl)
    ui.at(_spl[4], _spl[5], _spl[6], _spl[7])
    px, py, pw, ph, pst = ui.part("cell", 0, 0, {"nav": False})
    if pst & ui.ACTIVATED:
        got = ui.part_at()
        if got is not None:
            _tap = got[0] * 256 + got[1] + 1
    if pst & ui.PRESSED:
        got = ui.part_at()
        if got is not None:
            _held = got[0] * 256 + got[1] + 1
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, 1 if sound else 0)
    pmem(2, vol)
    pmem(3, cur)
    pmem(4, saves)
    pmem(5, 1 if ui.keys_taken() else 0)
    pmem(6, st)
    pmem(7, len(f) if f else 0)
    pmem(10, _pane)
    pmem(11, _sizes[0])
    pmem(12, _tap)
    pmem(13, _held)
    pmem(14, _xcur)
