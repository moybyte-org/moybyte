"""Unit tests for the shared editor keystroke edge detector (KeyEdge).

It backs the five Ctrl+Z/Y edge trackers (#89-#93); per-editor undo/redo
behavior is covered by the editor test suites over the #111 op-history core.
(The UndoStack half this file also pinned was deleted 2026-08-18 with its
class -- no editor had used it since #111.)"""

from runtime.editors import KeyEdge


# -- KeyEdge -----------------------------------------------------------------

def test_hit_fires_once_per_press():
    e = KeyEdge()
    assert e.hit(0x1A)                          # fresh press
    assert not e.hit(0x1A)                      # held -> not a fresh edge
    assert not e.hit(0)                          # release (falsy) is never a hit
    assert e.hit(0x1A)                          # a new press after release fires


def test_undo_redo_routes_and_debounces():
    log = []
    e = KeyEdge()
    e.undo_redo(0x1A, lambda: log.append("u"), lambda: log.append("r"))
    e.undo_redo(0x1A, lambda: log.append("u"), lambda: log.append("r"))  # held
    e.undo_redo(0x19, lambda: log.append("u"), lambda: log.append("r"))
    e.undo_redo(0x41, lambda: log.append("u"), lambda: log.append("r"))  # 'A' ignored
    assert log == ["u", "r"]


def test_reset_forgets_the_last_byte():
    e = KeyEdge()
    assert e.hit(0x1A)
    e.reset()
    assert e.hit(0x1A)                          # same byte fires again after reset
