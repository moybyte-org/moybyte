"""Unit tests for the shared editor history primitives (UndoStack + KeyEdge).

These back the four in-editor undo/redo stacks (#90-#93) and the five Ctrl+Z/Y
edge trackers (#89-#93); the per-editor behavior is covered by the editor test
suites -- this file pins the shared discipline itself."""

from runtime.editors import UndoStack, KeyEdge


# -- UndoStack ---------------------------------------------------------------

def test_push_bounds_and_forks_history():
    st = UndoStack(3)
    for v in ("a", "b", "c", "d"):
        st.push(v)
    assert st.undo == ["b", "c", "d"]          # oldest trimmed at the bound
    assert not st.can_redo()


def test_take_undo_with_capture_reverse():
    # Paint/Blocks shape: reverse captures the CURRENT state (here a counter).
    st = UndoStack(8)
    st.push("e0")
    now = {"v": "live"}
    popped = st.take_undo(lambda _e: now["v"])
    assert popped == "e0"
    assert st.can_redo() and st.redo == ["live"]
    assert not st.can_undo()


def test_take_undo_moves_same_entry_when_reverse_none():
    # Map shape: the SAME rec moves across (deltas replay both ways).
    st = UndoStack(8)
    st.push("rec")
    assert st.take_undo() == "rec"
    assert st.redo == ["rec"] and st.undo == []
    assert st.take_redo() == "rec"
    assert st.undo == ["rec"] and st.redo == []


def test_reverse_returning_none_skips_the_push():
    # Music shape: a stale object yields None -> nothing stashed on the far side.
    st = UndoStack(8)
    st.push("s0")
    assert st.take_undo(lambda _e: None) == "s0"
    assert st.undo == [] and st.redo == []     # popped, but no reverse stored


def test_take_on_empty_returns_none():
    st = UndoStack(4)
    assert st.take_undo() is None
    assert st.take_redo(lambda _e: "x") is None


def test_clear_drops_both_stacks():
    st = UndoStack(4)
    st.push("a")
    st.take_undo()                              # -> one on redo
    st.clear()
    assert not st.can_undo() and not st.can_redo()


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
