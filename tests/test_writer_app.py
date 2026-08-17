"""Writer app (#108): the kid notebook on named files/docs/*.moytext user
files -- the shared FileGridView picker, autosave on the idle debounce, rename,
trash, and the one-shot notes.json -> named-docs migration."""

import json
from pathlib import Path

from runtime import host_app, moy_carts
from runtime.writer_app import WriterAppLayer, WriterLayout, _body_of


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


def _press(app, inp, key):
    # One control-key press/release cycle (Ctrl+Z=0x1A, Ctrl+Y=0x19) -- same
    # press-then-release edge contract as _type.
    inp.last_key = key
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


def test_new_doc_persists_as_a_named_moytext_file(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    assert app.mode == "list"
    app._new_doc()                        # + NEW
    assert app.mode == "edit" and app.editor is not None
    name = app.doc_name
    assert name and name.startswith("doc")
    _type(app, ws.input, "The Dragon Fort\nOnce upon a time.")
    app._back_to_list()                   # leaving the page flushes
    assert app.mode == "list"
    assert name in app.grid.names         # the doc shows in the picker
    blob = moy_carts.load_file("docs", name, carts)
    data = json.loads(blob)
    assert data["format"] == "moytext-v1"
    assert data["body"] == "The Dragon Fort\nOnce upon a time."
    # It reads back through the #78 text() cart verb decoder too.
    assert moy_carts.decode_text(blob) == ["The Dragon Fort", "Once upon a time."]


def test_open_doc_roundtrips_the_body(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    name = app.doc_name
    _type(app, ws.input, "remember me")
    app._back_to_list()
    # A fresh workstation over the same store re-opens the same doc.
    ws2 = host_app.build_workstation(carts)
    app2 = _open_writer(ws2)
    assert name in app2.grid.names
    app2._open_doc(name)
    assert app2.editor.text() == "remember me"


def test_autosave_flushes_on_the_idle_debounce(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    name = app.doc_name
    _type(app, ws.input, "no save tap")
    assert app._unsaved is True
    app.draw(app.AUTOSAVE_S + 0.1)        # the debounce alone persists it
    assert app._unsaved is False
    assert _body_of(moy_carts.load_file("docs", name, carts)) == "no save tap"


def test_new_doc_is_unwritten_until_touched(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    app._back_to_list()                   # never typed -> no litter in the gallery
    assert moy_carts.list_files("docs", carts) == []


def test_rename_moves_the_file(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    old = app.doc_name
    _type(app, ws.input, "hi")
    app.flush(force=True)
    app._begin_rename()
    assert app.mode == "rename"
    app.rename_text = "my_story"
    app._typed_rename(type("K", (), {"last_key": 0x0D})())
    assert app.mode == "edit"
    assert app.doc_name == "my_story"
    assert "my_story" in moy_carts.list_files("docs", carts)
    assert old not in moy_carts.list_files("docs", carts)


def test_delete_moves_to_restorable_trash(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    name = app.doc_name
    _type(app, ws.input, "oops")
    app.flush(force=True)
    app._delete_doc()
    assert app.mode == "list"
    assert name not in moy_carts.list_files("docs", carts)
    assert ("docs", name) in moy_carts.trash_list(carts)


def test_migration_turns_notes_json_into_named_docs(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    moy_carts.save_notes(json.dumps({"format": "moynotes-v1", "notes": [
        {"title": "One", "body": "First note"},
        {"title": "Two", "body": "Second note"},
    ]}), carts)
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    names = moy_carts.list_files("docs", carts)
    assert len(names) == 2
    bodies = {_body_of(moy_carts.load_file("docs", n, carts)) for n in names}
    assert bodies == {"First note", "Second note"}
    assert set(app.grid.names) == set(names)


def test_files_app_open_routes_a_doc_to_writer(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    moy_carts.save_file("docs", "letter",
                        json.dumps({"format": "moytext-v1", "body": "dear you"}),
                        carts)
    files = ws.files_app
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Files":
            ws.launcher.sel = i
            break
    ws.open()
    ws.frame(1 / 30)
    files._enter_kind("docs")
    files._act("OPEN", "letter")
    assert ws.wm.top_kind() == "writer"
    assert ws.writer_app.doc_name == "letter"
    assert ws.writer_app.editor.text() == "dear you"


def test_read_only_store_keeps_typing_without_crashing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_doc()
    ws.can_manage = False
    _type(app, ws.input, "still here")
    assert app.flush(force=True) is False
    assert app.status == "CAN'T SAVE HERE"
    assert app.editor.text() == "still here"   # in-memory editing untouched


def test_bar_x_exit_saves_the_open_page(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    name = app.doc_name
    _type(app, ws.input, "not lost")
    xb = ws.layout.context_x_btn          # the OS bar's context-X
    app.handle_pointer(xb[0] + 2, xb[1] + 2, True)
    assert ws.wm.top_kind() == "launcher"
    assert _body_of(moy_carts.load_file("docs", name, carts)) == "not lost"


def test_layout_reflows_and_windowed_drops_the_bar():
    small = WriterLayout(320, 240, 1)
    big = WriterLayout(960, 600, 1)
    assert big.cols > small.cols and big.rows > small.rows
    win = WriterLayout(480, 300, 1, windowed=True)
    assert win.bar_h == 0
    assert _body_of('{"format": "moytext-v1", "body": "hi"}') == "hi"
    assert _body_of("garbage") == ""


# -- #111 phase 3: op-history undo/redo --------------------------------------


def test_typing_a_burst_then_undo_removes_it_and_redo_restores_it(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_doc()
    assert app.can_undo() is False and app.can_redo() is False
    _type(app, ws.input, "hello")         # one burst -- no pause, no punctuation
    assert app.editor.text() == "hello"
    assert app.can_undo() is True
    _press(app, ws.input, 0x1A)           # Ctrl+Z
    assert app.editor.text() == ""
    assert app.can_undo() is False and app.can_redo() is True
    _press(app, ws.input, 0x19)           # Ctrl+Y
    assert app.editor.text() == "hello"
    assert app.can_redo() is False


def test_punctuation_closes_a_burst_so_undo_walks_word_by_word(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_doc()
    _type(app, ws.input, "Hi. Bye.")      # two bursts: "Hi." then " Bye."
    assert app.editor.text() == "Hi. Bye."
    _press(app, ws.input, 0x1A)
    assert app.editor.text() == "Hi."
    _press(app, ws.input, 0x1A)
    assert app.editor.text() == ""
    assert app.can_undo() is False


def test_undo_reaches_across_the_autosave_flush(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    _type(app, ws.input, "first")
    app.draw(app.AUTOSAVE_S + 0.1)        # the debounce flushes + commits the burst
    assert app._unsaved is False
    _type(app, ws.input, " second")       # a fresh burst after the flush boundary
    assert app.editor.text() == "first second"
    _press(app, ws.input, 0x1A)           # undo the still-open post-flush burst
    assert app.editor.text() == "first"
    _press(app, ws.input, 0x1A)           # undo reaches the ALREADY-FLUSHED burst too
    assert app.editor.text() == ""
    assert app.can_undo() is False


def test_history_commit_writes_a_sidecar_segment(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    name = app.doc_name
    _type(app, ws.input, "note.")
    app.flush(force=True)
    recs = moy_carts.load_history("docs", name, carts)
    segs = [r for r in recs if r["t"] == "seg"]
    assert segs and segs[0]["ops"]
    op = segs[0]["ops"][0]
    assert op[0] == "edit" and op[2] == "" and op[3] == "note."


def test_reopened_doc_keeps_undo_depth(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_writer(ws)
    app._new_doc()
    name = app.doc_name
    _type(app, ws.input, "keep me")
    app.flush(force=True)                 # sidecar segment lands
    app._back_to_list()
    # A fresh workstation re-opening the same doc rebuilds undo depth from the
    # #111 sidecar, not just the saved text.
    ws2 = host_app.build_workstation(carts)
    app2 = _open_writer(ws2)
    app2._open_doc(name)
    assert app2.editor.text() == "keep me"
    assert app2.can_undo() is True
    assert app2.can_redo() is False
    _press(app2, ws2.input, 0x1A)
    assert app2.editor.text() == ""


def test_undo_redo_dim_with_can_undo_can_redo(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_doc()
    assert app.can_undo() is False and app.can_redo() is False
    app.draw(0)                           # exercise the dimmed toolbar paint path
    _type(app, ws.input, "x")
    assert app.can_undo() is True and app.can_redo() is False
    app.draw(0)                           # exercise the enabled toolbar paint path
    _press(app, ws.input, 0x1A)
    assert app.can_undo() is False and app.can_redo() is True


def test_undo_redo_toolbar_taps(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_writer(ws)
    app._new_doc()
    _type(app, ws.input, "tap me")
    ub = app.layout.undo_btn
    assert app.handle_pointer(ub[0] + 2, ub[1] + 2, True) is True
    assert app.editor.text() == ""
    rb = app.layout.redo_btn
    assert app.handle_pointer(rb[0] + 2, rb[1] + 2, True) is True
    assert app.editor.text() == "tap me"
