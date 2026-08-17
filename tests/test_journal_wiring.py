"""Stage 7 wiring (docs/history/shell_ux_technical_plan_v1.md Section 3): a Project commit
PERSISTS and JOURNALS. Drives the real console (host_app + Workstation) so the
Project.commit_* verbs run, and checks each one leaves a durable undo-journal entry
whose snapshot is byte-identical to what landed on disk.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_ws_with_cart(tmp_path, src, title="Journaled", type="app", edit=None):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, type=type, edit=edit or [])
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


def _snap(cart_path, entry):
    return (Path(cart_path) / "journal" / entry["snap"]).read_text()


def test_commit_code_journals_the_saved_source(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    ws.set_menu_view("code")
    ws.screen = "menu"
    new = "def _draw():\n    cls(2)  # edited\n"
    ws.editor.set_text(new)
    assert ws.save_code() is True                      # persists AND journals

    ents = _entries(path)
    assert [e["file"] for e in ents] == ["main.py"]
    assert _snap(path, ents[-1]) == new                # snapshot == the on-disk source
    assert (Path(path) / "main.py").read_text() == new


def test_commit_sprites_journals(tmp_path):
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    ws.sheet.pset(0, 0, 5)                              # dirty the sheet
    ws.save_sprites()

    ents = [e for e in _entries(path) if e["file"] == "sprites.moygfx"]
    assert ents, "a sprite commit must journal"
    assert _snap(path, ents[-1]) == (Path(path) / "sprites.moygfx").read_text()


def test_commit_config_journals(tmp_path):
    ws = _make_ws_with_cart(
        tmp_path, "def _draw():\n    cls(cfg('bg', 1))\n",
        edit=[{"key": "bg", "type": "int", "min": 0, "max": 15}])
    path = ws.cart["path"]
    ws.config["bg"] = 7
    ws._save_config()

    ents = [e for e in _entries(path) if e["file"] == "config.json"]
    assert ents, "a config commit must journal"
    assert json.loads(_snap(path, ents[-1]))["bg"] == 7


def test_commit_dedup_no_duplicate_journal_entry(tmp_path):
    # Saving the SAME source twice journals ONCE (the snapshot ceiling): the second
    # commit is content-identical, so nothing is written.
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    ws.set_menu_view("code")
    ws.screen = "menu"
    same = "def _draw():\n    cls(9)\n"
    ws.editor.set_text(same)
    ws.save_code()
    ws.editor.dirty = True                             # force the store write to re-run
    ws.save_code()                                     # identical content

    ents = [e for e in _entries(path) if e["file"] == "main.py"]
    assert len(ents) == 1                              # deduped -- one durable step


def test_journal_failure_never_breaks_the_save(tmp_path, monkeypatch):
    # If journaling raises, the save it shadows still succeeds (best-effort undo).
    from runtime import moy_carts
    ws = _make_ws_with_cart(tmp_path, "def _draw():\n    cls(1)\n")
    path = ws.cart["path"]
    monkeypatch.setattr(moy_carts, "journal_append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("journal disk full")))
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text("def _draw():\n    cls(3)\n")
    assert ws.save_code() is True                      # save still reports success
    assert "cls(3)" in (Path(path) / "main.py").read_text()
