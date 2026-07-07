"""Stage 7 (docs/shell_ux_technical_plan_v1.md Section 3): the durable undo/redo WALK
driven through the console -- ws.undo()/ws.redo() restore the live file, rebuild the
affected editor, and re-run the cart; plus the code-editor keyboard-shortcut UI trigger
(Ctrl+Z / Ctrl+Y). A durable step = one COMMIT (not one keystroke); finer, in-session
undo stays in the editor's RAM.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

V1 = "def _draw():\n    cls(1)  # one\n"
V2 = "def _draw():\n    cls(2)  # two\n"


def _make_ws_with_cart(tmp_path, src, title="Undoable"):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, type="app")
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()
    return ws


def _open_code_and_commit(ws, text):
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text(text)
    assert ws.save_code() is True          # persist + journal one durable step


def test_undo_redo_walk_through_the_console(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(0)\n")
    path = ws.cart["path"]
    _open_code_and_commit(ws, V1)
    _open_code_and_commit(ws, V2)
    assert (Path(path) / "main.py").read_text() == V2

    # undo: the live file + the editor buffer both revert to V1
    assert ws.undo() is True
    assert (Path(path) / "main.py").read_text() == V1
    assert ws.editor.text() == V1          # the editor was rebuilt over the restored file
    assert ws.cart["src"] == V1            # and the live cart data too

    # undo again is at the floor (V1 = the first durable commit; V0 is only in-RAM)
    assert ws.undo() is False
    assert (Path(path) / "main.py").read_text() == V1

    # redo steps forward to V2
    assert ws.redo() is True
    assert (Path(path) / "main.py").read_text() == V2
    assert ws.editor.text() == V2

    # redo at the top is a no-op
    assert ws.redo() is False


def test_new_commit_after_undo_truncates_redo(tmp_path):
    # Google-Docs rule, end to end: editing after an undo drops the redo future.
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(0)\n")
    path = ws.cart["path"]
    _open_code_and_commit(ws, V1)
    _open_code_and_commit(ws, V2)
    assert ws.undo() is True and (Path(path) / "main.py").read_text() == V1

    v3 = "def _draw():\n    cls(3)  # three\n"
    _open_code_and_commit(ws, v3)          # a NEW commit while rewound
    assert (Path(path) / "main.py").read_text() == v3
    assert ws.redo() is False              # V2 was truncated -- nothing ahead
    assert ws.undo() is True and (Path(path) / "main.py").read_text() == V1


def test_undo_redo_keyboard_shortcut_in_code_editor(tmp_path):
    # The UI trigger: Ctrl+Z (0x1A) / Ctrl+Y (0x19) typed in the code editor drive the
    # walk (these control bytes are never inserted as text).
    from runtime import host_app
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(0)\n")
    path = ws.cart["path"]
    drv = host_app.ConsoleDriver(ws)
    _open_code_and_commit(ws, V1)
    _open_code_and_commit(ws, V2)

    drv.type_char(0x1A)                     # Ctrl+Z
    drv.frame(1 / 30)
    assert (Path(path) / "main.py").read_text() == V1
    assert ws.editor.text() == V1

    drv.type_char(0x19)                     # Ctrl+Y
    drv.frame(1 / 30)
    assert (Path(path) / "main.py").read_text() == V2
    assert ws.editor.text() == V2


def test_control_bytes_never_corrupt_the_buffer(tmp_path):
    # A stray Ctrl+Z/Ctrl+Y must NOT be typed into the source (they dispatch, or no-op
    # at a floor, but never insert a character).
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(0)\n")
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text(V1)
    before = ws.editor.text()
    ws.editor.key(0x1A)                     # the editor core ignores control bytes
    ws.editor.key(0x19)
    assert ws.editor.text() == before       # unchanged -- no chr(0x1A) inserted


def test_undo_no_journal_is_a_safe_noop(tmp_path):
    # A cart never committed (no journal) -> undo/redo just return False, no crash.
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(0)\n")
    assert ws.undo() is False
    assert ws.redo() is False
