"""Stage 7 (docs/shell_ux_technical_plan_v1.md Section 3, v1.1 cadence): the idle-typing
debounce -- the undo journal's SOFT commit trigger. A durable commit fires ~1.5s after
the last keystroke in the code editor (never mid-burst, so the SD write lands in a
typing gap), never on a keystroke count, and the autosave is INVISIBLE (spec: "Save is
invisible").
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_ws_with_cart(tmp_path, src, title="Debounced"):
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


def _entries(cart_path):
    from runtime import moy_carts
    return moy_carts._journal_load_entries(cart_path + "/journal/journal.jsonl")


def _open_code(ws, text=None):
    ws.set_menu_view("code")
    ws.screen = "menu"
    if text is not None:
        ws.editor.set_text(text)
        ws.editor.dirty = True


def test_keystroke_in_code_editor_arms_the_debounce(tmp_path):
    from runtime import host_app
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    drv = host_app.ConsoleDriver(ws)
    _open_code(ws)
    assert ws._edit_ms is None                      # no pending edit yet

    drv.type_char(ord("x"))
    drv.frame(1 / 30)                               # routes the key into the editor
    assert ws._edit_ms is not None                 # a keystroke armed the debounce
    assert ws.editor.dirty                          # and actually edited the buffer
    # One frame is NOT enough time to fire (elapsed << 1.5s): still unsaved.
    assert ws.editor.dirty


def test_debounce_does_not_fire_mid_typing(tmp_path):
    from runtime import console as C
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    _open_code(ws, "def _draw():\n    cls(2)\n")
    ws._edit_ms = C._ticks_ms()                     # "just typed" -> not idle
    ws.frame(1 / 30)
    assert ws.editor.dirty                          # NOT committed mid-burst
    assert not [e for e in _entries(path) if e["file"] == "main.py"]   # nothing journaled


def test_idle_debounce_autosaves_and_journals_invisibly(tmp_path):
    from runtime import console as C
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    new = "def _draw():\n    cls(4)  # autosaved\n"
    _open_code(ws, new)
    ws.save_status = None
    ws._edit_ms = C._ticks_ms() - 5000              # 5s of no keystroke -> past 1.5s
    ws.frame(1 / 30)                                # the idle tick fires the commit

    assert not ws.editor.dirty                      # the edit was persisted
    assert ws._edit_ms is None                      # and the debounce disarmed
    assert new in (Path(path) / "main.py").read_text()
    ents = [e for e in _entries(path) if e["file"] == "main.py"]
    assert ents, "the idle debounce must journal a durable step"
    assert (Path(path) / "journal" / ents[-1]["snap"]).read_text() == new
    # Invisible (spec Section 7): the autosave does NOT flash the SAVE status.
    assert ws.save_status != "SAVED"


def test_autosave_skips_unparseable_source_without_nagging(tmp_path):
    from runtime import console as C
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    _open_code(ws, "def _draw(:\n    cls(\n")        # mid-edit, won't parse
    ws.save_status = None
    ws._edit_ms = C._ticks_ms() - 5000
    ws.frame(1 / 30)

    assert ws.editor.dirty                           # not committed (invalid) -> still pending
    assert ws.save_status is None                    # no SYNTAX nag from the autosave
    assert not [e for e in _entries(path) if e["file"] == "main.py"]


def test_idle_autosave_does_not_award_achievement(tmp_path):
    # F2: the invisible idle autosave must NOT pop the "Code Wizard" toast -- a visible
    # side effect on a nominally-invisible save. The badge stays earnable via SAVE/PLAY.
    from runtime import console as C
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    _open_code(ws, "def _draw():\n    cls(4)\n")
    notes = []
    ws.ach.note = lambda *a, **k: notes.append(a)
    ws._edit_ms = C._ticks_ms() - 5000
    ws.frame(1 / 30)
    assert not ws.editor.dirty                       # the autosave committed
    assert not any(a and a[0] == "code_save" for a in notes)   # but awarded nothing


def test_manual_save_still_awards_achievement(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    _open_code(ws, "def _draw():\n    cls(5)\n")
    notes = []
    ws.ach.note = lambda *a, **k: notes.append(a)
    assert ws.save_code() is True                    # explicit SAVE
    assert any(a and a[0] == "code_save" for a in notes)   # awards "Code Wizard"


def test_no_pending_edit_costs_nothing(tmp_path):
    # The common path: _edit_ms is None, so the idle tick early-outs every frame.
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    assert ws._edit_ms is None
    for _ in range(5):
        ws.frame(1 / 30)                             # must not raise / must not journal
    assert not (Path(ws.cart["path"]) / "journal").exists()
