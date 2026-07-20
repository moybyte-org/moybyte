"""Sheets app (#78/#108): the kid spreadsheet on named files/tables/*.moysheet
user files -- the shared FileGridView picker, per-sheet autosave, rename, trash,
the sheets.json -> named-tables migration -- plus the table()/text() interop
cart verbs it feeds (unchanged moy_carts decoders)."""

import json
from pathlib import Path

from runtime import host_app, moy_carts, formula
from runtime.sheets_app import SheetsAppLayer, SheetsLayout


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
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_sheets(ws)
    assert app.mode == "list"
    app._new_sheet()                         # + NEW
    assert app.mode == "grid" and app.sheet is not None
    name = app.sheet_name
    # A1 = 5, B1 = =A1*2 (Enter steps the selection down, so re-place it).
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "5\n")
    app.cur_col, app.cur_row = 1, 0
    _type(app, ws.input, "=A1*2\n")
    assert app.sheet.value_at(0, 0) == 5
    assert app.sheet.value_at(1, 0) == 10
    app.flush(force=True)
    # It persisted as a named .moysheet user file (moysheet-v1).
    blob = moy_carts.load_file("tables", name, carts)
    assert json.loads(blob)["format"] == "moysheet-v1"
    # Re-open it: the formula comes back intact.
    ws.go_home()
    app2 = _open_sheets(ws)
    assert name in app2.grid.names
    app2._open_file(name)
    assert app2.sheet.value_at(1, 0) == 10
    assert app2.sheet.raw_at(1, 0) == "=A1*2"


def test_saved_sheet_reads_back_through_the_table_verb(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_sheets(ws)
    app._new_sheet()
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "1\n")
    app.cur_col, app.cur_row = 1, 0
    _type(app, ws.input, "=A1+1\n")
    app.flush(force=True)
    # The same blob the table() cart verb consumes (#78 decode_table).
    blob = moy_carts.load_file("tables", app.sheet_name, carts)
    assert moy_carts.decode_table(blob) == [[1, 2]]


def test_cell_error_values_show_in_the_grid(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_sheets(ws)
    app._new_sheet()
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
    app._new_sheet()
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "42\n")
    app.cur_col, app.cur_row = 0, 0
    ws.input.last_key = 8                     # Backspace on a not-editing cell clears
    app._typed_keys(ws.input)
    ws.input.last_key = 0
    app._typed_keys(ws.input)
    assert app.sheet.value_at(0, 0) == ""


def test_delete_sheet_moves_to_trash(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_sheets(ws)
    app._new_sheet()
    name = app.sheet_name
    assert name in moy_carts.list_files("tables", carts)
    app._delete_current()
    assert app.mode == "list"
    assert name not in moy_carts.list_files("tables", carts)
    assert ("tables", name) in moy_carts.trash_list(carts)


def test_rename_moves_the_file(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_sheets(ws)
    app._new_sheet()
    old = app.sheet_name
    app._begin_rename()
    app.rename_text = "budget"
    app._typed_name(type("K", (), {"last_key": 0x0D})())
    assert app.sheet_name == "budget"
    assert "budget" in moy_carts.list_files("tables", carts)
    assert old not in moy_carts.list_files("tables", carts)


def test_bar_x_exit_saves_the_open_sheet(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_sheets(ws)
    app._new_sheet()
    name = app.sheet_name
    app.cur_col, app.cur_row = 0, 0
    _type(app, ws.input, "99")               # an OPEN, uncommitted edit
    xb = ws.layout.context_x_btn
    app.handle_pointer(xb[0] + 2, xb[1] + 2, True)
    assert ws.wm.top_kind() == "launcher"
    data = json.loads(moy_carts.load_file("tables", name, carts))
    assert data["cells"]["A1"]["v"] == 99


def test_migration_turns_sheets_json_into_named_tables(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    s1 = formula.Sheet("Alpha", 6, 6)
    s1.set_cell(0, 0, "7")
    s2 = formula.Sheet("Beta", 6, 6)
    s2.set_cell(0, 0, "hi")
    moy_carts.save_sheets(json.dumps({"format": "moysheets-v1",
                                      "sheets": [s1.to_dict(), s2.to_dict()]}), carts)
    ws = host_app.build_workstation(carts)
    app = _open_sheets(ws)
    names = moy_carts.list_files("tables", carts)
    assert len(names) == 2
    assert set(app.grid.names) == set(names)
    # Each migrated file is a valid moysheet the table() verb can read.
    got = {tuple(tuple(r) for r in moy_carts.decode_table(
        moy_carts.load_file("tables", n, carts))) for n in names}
    assert ((7,),) in got and (("hi",),) in got


def test_files_app_open_routes_a_table_to_sheets(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    s = formula.Sheet("nums", 4, 4)
    s.set_cell(0, 0, "3")
    moy_carts.save_file("tables", "nums", json.dumps(s.to_dict()), carts)
    files = ws.files_app
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Files":
            ws.launcher.sel = i
            break
    ws.open()
    ws.frame(1 / 30)
    files._enter_kind("tables")
    files._act("OPEN", "nums")
    assert ws.wm.top_kind() == "sheets"
    assert ws.sheets_app.sheet_name == "nums"
    assert ws.sheets_app.sheet.value_at(0, 0) == 3


def test_layout_reflows():
    small = SheetsLayout(320, 240, 1)
    big = SheetsLayout(960, 600, 1)
    assert big.vis_rows > small.vis_rows
    assert SheetsLayout(480, 300, 1, windowed=True).bar_h == 0


# -- interop: the decode helpers (unchanged moy_carts) ---------------------------

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
    assert ws.ns["MISS_T"] == []
    assert ws.ns["MISS_X"] == []


def test_cart_folder_loaders_return_empty_without_assets(tmp_path):
    d = tmp_path / "plain.moy"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Plain", "main": "main.py"}')
    (d / "main.py").write_text("def _draw():\n    cls(0)\n")
    cart = moy_carts.load(str(d))
    assert cart["tables"] == {}
    assert cart["texts"] == {}
