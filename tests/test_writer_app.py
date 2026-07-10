"""Writer app: the kid notebook -- notes list, ruled text page, autosave."""

import json
from pathlib import Path

from runtime import host_app, moy_carts
from runtime.writer_app import (WriterAppLayer, WriterLayout, AUTOSAVE_KEYS,
                                MAX_NOTES, _title_of)


ROOT = Path(__file__).resolve().parent.parent


def _open_writer(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Writer":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_kind() == "writer"
    return ws.writer_app


def _type(app, inp, text):
    # One byte per physical press, released between (the last_key edge contract).
    for ch in text:
        inp.last_key = 10 if ch == "\n" else ord(ch)
        app._typed_keys(inp)
        inp.last_key = 0
        app._typed_keys(inp)


def test_writer_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "writer.moy"
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["version"] >= 1
    assert man["type"] == "app"
    assert man["system"] is True
    assert "notebook" in man["permissions"]
    compile((folder / "main.py").read_text(encoding="utf-8"),
            str(folder / "main.py"), "exec")


def test_writer_identity_rejects_copies_and_impostors():
    real = {"title": "Writer", "permissions": ["notebook"], "path": "/x/writer.moy"}
    assert WriterAppLayer.is_app(real)
    assert not WriterAppLayer.is_app(dict(real, title="My Writer"))
    assert not WriterAppLayer.is_app(dict(real, permissions=[]))
    assert not WriterAppLayer.is_app(dict(real, path="/x/writer_copy.moy"))
    embedded = {"title": "Writer", "permissions": ["notebook"], "version": 1}
    assert WriterAppLayer.is_app(embedded)


def test_writer_opens_in_text_mode_and_exit_restores(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_writer(ws)
    assert ws.input.text_mode is True     # a typing app: clean ASCII keys
    ws.go_home()
    assert ws.wm.top_kind() == "launcher"
    assert ws.input.text_mode is False    # button mode restored for games


def test_new_note_types_autotitles_and_persists(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    assert app.mode == "list"
    app._tap_row(0)                       # the "+ NEW PAGE" row
    assert app.mode == "edit" and app.editor is not None
    _type(app, ws.input, "The Dragon Fort\nOnce upon a time.")
    ws.frame(1 / 30)                      # the edit view renders
    app._back_to_list()                   # leaving the page flushes
    assert app.notes[0]["title"] == "The Dragon Fort"
    data = json.loads(moy_carts.load_notes(ws.carts_root))
    assert data["format"] == "moynotes-v1"
    assert data["notes"][0]["body"] == "The Dragon Fort\nOnce upon a time."


def test_notebook_survives_a_reboot(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_note()
    _type(app, ws.input, "remember me")
    app._back_to_list()
    # A fresh workstation over the same store loads the same notebook.
    ws2 = host_app.build_workstation(carts)
    app2 = _open_writer(ws2)
    assert [n["title"] for n in app2.notes] == ["remember me"]
    app2._open_note(0)
    assert app2.editor.text() == "remember me"


def test_autosave_flushes_after_keystroke_budget(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_note()
    _type(app, ws.input, "x" * AUTOSAVE_KEYS)
    # No back/exit tap yet -- the keystroke debounce alone must have persisted.
    data = json.loads(moy_carts.load_notes(ws.carts_root))
    assert data["notes"][0]["body"] == "x" * AUTOSAVE_KEYS


def test_tear_out_is_a_two_tap_confirm(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_note()
    _type(app, ws.input, "oops")
    lay = app.layout
    bx = lay.del_btn[0] + 2
    by = lay.del_btn[1] + 2
    app.handle_pointer(bx, by, True)      # first tap only ARMS
    assert app.del_armed and len(app.notes) == 1
    app.handle_pointer(bx, by, True)      # second tap tears the page out
    assert app.mode == "list" and app.notes == []
    assert json.loads(moy_carts.load_notes(ws.carts_root))["notes"] == []


def test_notebook_full_refuses_a_new_page(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app.notes = [{"title": str(i), "body": str(i)} for i in range(MAX_NOTES)]
    app._new_note()
    assert len(app.notes) == MAX_NOTES
    assert app.status == "NOTEBOOK FULL"


def test_read_only_store_keeps_typing_without_crashing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_note()
    ws.can_manage = False
    _type(app, ws.input, "still here")
    assert app.flush(force=True) is False
    assert app.status == "CAN'T SAVE HERE"
    assert app.editor.text() == "still here"   # in-memory editing untouched


def test_layout_reflows_and_windowed_drops_the_bar():
    small = WriterLayout(320, 240, 1)
    big = WriterLayout(960, 600, 1)
    assert big.cols > small.cols and big.rows > small.rows
    assert big.list_rows > small.list_rows
    win = WriterLayout(480, 300, 1, windowed=True)
    assert win.bar_h == 0
    assert _title_of("\n\n  Hello there  \nmore") == "Hello there"
    assert _title_of("") == "EMPTY PAGE"


def test_typing_routes_through_the_real_driver(tmp_path):
    """End-to-end through the real input model: driver-fed keys walk the memoized
    layer stack into the writer's page (not a direct _typed_keys call)."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    drv = host_app.ConsoleDriver(ws)
    r = app.layout.row_rect(0)
    drv.click(r[0] + 4, r[1] + 4)          # tap "+ NEW PAGE"
    drv.frame(1 / 30)
    assert app.mode == "edit"
    for ch in "Hi!":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
        drv.frame(1 / 30)                  # release edge so the next char registers
    assert app.editor.text() == "Hi!"


def test_bar_x_exit_saves_the_open_page(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_note()
    _type(app, ws.input, "not lost")
    xb = ws.layout.context_x_btn          # the OS bar's context-X
    app.handle_pointer(xb[0] + 2, xb[1] + 2, True)
    assert ws.wm.top_kind() == "launcher"
    data = json.loads(moy_carts.load_notes(ws.carts_root))
    assert data["notes"][0]["body"] == "not lost"
