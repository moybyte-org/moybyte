"""Sheets app (#78): the kid spreadsheet -- workbook list, grid, formula cells,
autosave -- plus the table()/text() interop cart verbs it feeds."""

import json
from pathlib import Path

from runtime import host_app, moy_carts, formula
from runtime.sheets_app import SheetsAppLayer, SheetsLayout, MAX_SHEETS


ROOT = Path(__file__).resolve().parent.parent


def _open_sheets(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Sheets":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_kind() == "sheets"
    return ws.sheets_app


def _type(app, inp, text):
    for ch in text:
        inp.last_key = 10 if ch == "\n" else ord(ch)
        app._typed_keys(inp)
        inp.last_key = 0
        app._typed_keys(inp)


# -- identity + cart -------------------------------------------------------------

def test_sheets_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "sheets.moy"
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["version"] >= 1
    assert man["type"] == "app"
    assert man["system"] is True
    assert "sheets" in man["permissions"]
    compile((folder / "main.py").read_text(encoding="utf-8"),
            str(folder / "main.py"), "exec")


def test_sheets_identity_rejects_copies_and_impostors():
    real = {"title": "Sheets", "permissions": ["sheets"], "path": "/x/sheets.moy"}
    assert SheetsAppLayer.is_app(real)
    assert not SheetsAppLayer.is_app(dict(real, title="My Sheets"))
    assert not SheetsAppLayer.is_app(dict(real, permissions=[]))
    assert not SheetsAppLayer.is_app(dict(real, path="/x/sheets_copy.moy"))
    embedded = {"title": "Sheets", "permissions": ["sheets"], "version": 1}
    assert SheetsAppLayer.is_app(embedded)


# -- open + edit + persist -------------------------------------------------------

def test_new_sheet_edit_formula_and_reload(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)                          # + NEW SHEET
    assert app.mode == "grid" and app.sheet is not None
    # A1 = 5, B1 = =A1*2 (Enter steps the selection down, so re-place it).
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "5\n")
    app.cur_col, app.cur_row = 1, 0
    _type(app, ws.input, "=A1*2\n")
    assert app.sheet.value_at(0, 0) == 5
    assert app.sheet.value_at(1, 0) == 10
    ws.frame(1 / 30)                         # a draw pass over live data
    app.flush(force=True)
    assert moy_carts.load_sheets(str(tmp_path / "carts"))
    # Re-open the app: the workbook comes back with the formula intact.
    ws.go_home()
    app2 = _open_sheets(ws)
    assert len(app2.sheets) == 1
    app2._open_sheet(0)
    assert app2.sheet.value_at(1, 0) == 10
    assert app2.sheet.raw_at(1, 0) == "=A1*2"


def test_cell_error_values_show_in_the_grid(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "=B1\n")
    app.cur_col, app.cur_row = 1, 0
    _type(app, ws.input, "=A1\n")            # A1 <-> B1 cycle
    assert app.sheet.value_at(0, 0) == formula.LOOP
    ws.frame(1 / 30)                         # error cells draw without crashing
    assert ws.cart_error is None


def test_backspace_clears_a_cell(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "42\n")
    app.cur_col, app.cur_row = 0, 0
    ws.input.last_key = 8                     # Backspace on a not-editing cell clears
    app._typed_keys(ws.input)
    ws.input.last_key = 0
    app._typed_keys(ws.input)
    assert app.sheet.value_at(0, 0) == ""


def test_delete_sheet_and_workbook_cap(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)
    app._delete_active()
    assert app.mode == "list" and len(app.sheets) == 0
    # Fill the workbook to the cap; the next NEW is refused.
    app.sheets = [formula.Sheet("S" + str(i)) for i in range(MAX_SHEETS)]
    app._new_sheet()
    assert len(app.sheets) == MAX_SHEETS
    assert app.status == "WORKBOOK FULL"


def test_bar_x_exit_saves_the_workbook(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "99")               # an OPEN, uncommitted edit
    xb = ws.layout.context_x_btn
    app.handle_pointer(xb[0] + 2, xb[1] + 2, True)
    assert ws.wm.top_kind() == "launcher"
    data = json.loads(moy_carts.load_sheets(str(tmp_path / "carts")))
    assert data["sheets"][0]["cells"]["A1"]["v"] == 99


def test_layout_reflows():
    small = SheetsLayout(320, 240, 1)
    big = SheetsLayout(960, 600, 1)
    assert big.vis_rows > small.vis_rows
    assert SheetsLayout(480, 300, 1, windowed=True).bar_h == 0


# -- attach a sheet to a game (#78: the Sheets-to-game UI) -----------------------

def test_attach_lists_only_game_and_story_carts(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)                          # + NEW SHEET
    kinds = {c.get("type") for c in app._attach_targets()}
    assert kinds <= {"game", "story"}
    # Sheets/Writer/Storybook/... (type "app") never show up as attach targets.
    assert "Sheets" not in [c.get("title") for c in app._attach_targets()]


def test_attach_sheet_lands_the_table_in_the_target_cart(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    game_src = (
        "ROWS = table('wave')\n"
        "def _update(dt):\n    pass\n"
        "def _draw():\n    cls(0)\n"
    )
    game = ws.carts_store.create("Coin Quest 2", carts, src=game_src, type="game")
    ws._apply_items(ws.carts_store.scan(carts))
    app = _open_sheets(ws)
    app._tap_row(0)                          # + NEW SHEET
    app.sheet.name = "wave"
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "1\n")
    app.cur_col, app.cur_row = 1, 0
    _type(app, ws.input, "=A1+1\n")
    app._open_attach()
    assert app.mode == "attach"
    targets = app._attach_targets()
    titles = [c.get("title") for c in targets]
    assert "Coin Quest 2" in titles
    app._tap_row(titles.index("Coin Quest 2"))
    assert app.mode == "grid"
    assert "ATTACHED" in app.status
    table_path = Path(game["path"]) / "tables" / "wave.moysheet"
    assert table_path.exists()
    rows = moy_carts.decode_table(table_path.read_text(encoding="utf-8"))
    assert rows == [[1, 2]]
    # The sheet stays open + editable in Sheets after attaching (not consumed).
    assert app.sheet is not None and app.sheet.name == "wave"
    # And the game reads it back at runtime through table() (#78's cart verb --
    # already shipped; this proves the attach UI feeds it end to end).
    ws._apply_items(ws.carts_store.scan(carts))
    for i, c in enumerate(ws.launcher.items):
        if c.get("title") == "Coin Quest 2":
            ws.launcher.sel = i
            break
    ws.open()
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.ns["ROWS"] == [[1, 2]]


def test_attach_back_button_returns_to_the_grid_without_writing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._tap_row(0)
    app._open_attach()
    assert app.mode == "attach"
    app._close_attach()
    assert app.mode == "grid" and app.sheet is not None


# -- interop: the decode helpers -------------------------------------------------

def test_decode_table_trims_to_populated_extent():
    s = formula.Sheet("wave", 8, 8)
    s.set_cell(0, 0, "1")
    s.set_cell(1, 0, "=A1+1")
    s.set_cell(0, 1, "hello")
    blob = json.dumps(s.to_dict())
    rows = moy_carts.decode_table(blob)
    assert rows == [[1, 2], ["hello", ""]]


def test_decode_text_splits_body_into_lines():
    blob = json.dumps({"format": "moytext-v1", "body": "line one\nline two"})
    assert moy_carts.decode_text(blob) == ["line one", "line two"]


def test_decoders_degrade_on_garbage():
    for bad in ("", "not json", "{}", '{"cells": null}', "[]", None):
        assert moy_carts.decode_table(bad) == []
        assert moy_carts.decode_text(bad) == []


# -- interop: a headless cart reading table()/text() -----------------------------

def test_cart_reads_table_and_text_at_runtime(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    src = (
        "ROWS = table('wave')\n"
        "LINES = text('dialog')\n"
        "MISS_T = table('nope')\n"
        "MISS_X = text('nope')\n"
        "def _update(dt):\n    pass\n"
        "def _draw():\n    cls(0)\n"
    )
    cart = ws.carts_store.create("Reader", carts, src=src, type="app")
    s = formula.Sheet("wave", 4, 4)
    s.set_cell(0, 0, "1")
    s.set_cell(1, 0, "=A1+1")
    s.set_cell(0, 1, "hi")
    moy_carts.save_table(cart, "wave", json.dumps(s.to_dict()))
    moy_carts.save_text(cart, "dialog",
                        json.dumps({"format": "moytext-v1",
                                    "body": "Hello\nAdventurer"}))
    # Re-scan so load() picks up the freshly attached tables/ + docs/ assets.
    ws._apply_items(ws.carts_store.scan(carts))
    for i, c in enumerate(ws.launcher.items):
        if c.get("title") == "Reader":
            ws.launcher.sel = i
            break
    ws.open()
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.ns["ROWS"] == [[1, 2], ["hi", ""]]
    assert ws.ns["LINES"] == ["Hello", "Adventurer"]
    assert ws.ns["MISS_T"] == []       # missing name -> empty, never a crash
    assert ws.ns["MISS_X"] == []


def test_cart_folder_loaders_return_empty_without_assets(tmp_path):
    # A plain cart folder (no tables/ or docs/) loads to {} -- the common case.
    d = tmp_path / "plain.moy"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Plain", "main": "main.py"}')
    (d / "main.py").write_text("def _draw():\n    cls(0)\n")
    cart = moy_carts.load(str(d))
    assert cart["tables"] == {}
    assert cart["texts"] == {}
