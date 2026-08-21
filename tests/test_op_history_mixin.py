"""`OpHistoryMixin` -- the undo/redo facade the five editor cores share.

Its `_undo` proxy is read across the suite; `_redo` and `redo()` were never
reached by any of the 2819 tests (verified 2026-08-22 by raising inside the
property). An asymmetric hole in a five-consumer facade is how the hand-copies
it replaced had begun to differ.
"""

from runtime.op_history import History, OpHistoryMixin, TextEditCodec


class _Doc:
    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def set_text(self, t):
        self._t = t

    def goto_row(self, *a):
        pass


class _Core(OpHistoryMixin):
    """The smallest thing a real core is: a doc, a History, and the two hooks."""

    def __init__(self, text=""):
        self.doc = _Doc(text)
        self._hist = History(self.doc, TextEditCodec())
        self.before = 0
        self.after = 0

    def _hist_before(self):
        self.before += 1

    def _hist_after_step(self):
        self.after += 1

    def type(self, pos, inserted, deleted=""):
        """record() logs an edit the surface has ALREADY applied, so apply it."""
        op = ("edit", pos, deleted, inserted)
        self._hist.codec.apply(self.doc, op)
        self._hist.record(op)


def _core():
    c = _Core()
    c.type(0, "a")
    c.type(1, "b")
    return c


def test_the_redo_proxy_is_the_historys_own_stack():
    c = _core()
    assert c._redo is c._hist._redo
    assert c._undo is c._hist._undo


def test_redo_reapplies_what_undo_reverted():
    c = _core()
    assert c.doc.text() == "ab"
    assert c.undo() is True and c.doc.text() == "a"
    assert c.can_redo() is True
    assert c.redo() is True and c.doc.text() == "ab"
    assert c.can_redo() is False


def test_redo_at_the_top_of_history_takes_no_step():
    c = _core()
    assert c.can_redo() is False
    assert c.redo() is False
    assert c.doc.text() == "ab"


def test_a_new_edit_invalidates_the_redo_stack():
    c = _core()
    c.undo()
    assert c.can_redo() is True
    c.type(1, "z")
    assert c.can_redo() is False
    assert c.redo() is False


def test_redo_runs_the_same_hooks_undo_does():
    """The hand-copies this facade replaced had begun to vary in whether redo
    closed an open gesture, which is what _hist_before exists for."""
    c = _core()
    c.undo()
    b, a = c.before, c.after
    assert c.redo() is True
    assert (c.before, c.after) == (b + 1, a + 1)


def test_a_refused_redo_still_closes_an_open_gesture_but_takes_no_step():
    c = _core()
    b, a = c.before, c.after
    assert c.redo() is False
    assert c.before == b + 1        # the gesture is sealed either way
    assert c.after == a             # ... but no step was taken


def test_undo_walks_back_to_the_session_base_and_stops():
    c = _core()
    assert c.undo() and c.undo()
    assert c.doc.text() == ""
    assert c.can_undo() is False
    assert c.undo() is False
    assert c.redo() is True and c.doc.text() == "a"
