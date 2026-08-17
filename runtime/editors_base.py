"""The shared editor-input leaf (KeyEdge) every editor surface builds on.

This module also carried the pre-#111 history pair -- `UndoStack` and
`UndoRedoMixin` -- until 2026-08-18: every editor core had moved onto the
op-history `History` (and its `OpHistoryMixin` facade, runtime/op_history.py),
leaving the pair with no importer but its own unit tests. Git history has it.
editors.py re-exports this, so `from editors import KeyEdge` is unchanged.
Dependency-free (freezes cleanly on CPython + MicroPython)."""


class KeyEdge:
    """Single-fire keystroke edge detector shared by the editor surfaces (#89-#93).
    The keyboard streams last_key every frame with NO autorepeat, so a shortcut
    must act only on the 0->key (or key->new-key) EDGE -- a held Ctrl+Z would
    otherwise drain the whole undo stack, one step per frame. Tracks the previous
    byte. `undo_redo(k, on_undo, on_redo)` is the common Ctrl+Z/Y (0x1A/0x19) case
    the one-shot surfaces share; `hit(k)` exposes the raw edge for a richer
    dispatcher (the code editor folds undo/redo in with copy/cut/paste/find)."""

    def __init__(self):
        self.prev = 0

    def reset(self):
        """Forget the last byte (on entering a screen, so a stale key can't fire)."""
        self.prev = 0

    def hit(self, k):
        """True iff k is a fresh press (truthy and != the last byte). Records k as
        the new previous either way, so a caller reads the flag then dispatches."""
        fresh = bool(k) and k != self.prev
        self.prev = k
        return fresh

    def undo_redo(self, k, on_undo, on_redo):
        """The Ctrl+Z/Y shortcut: on a fresh edge fire on_undo (0x1A) / on_redo
        (0x19). Records the edge like hit(), so a held key fires once."""
        if self.hit(k):
            if k == 0x1A:
                on_undo()
            elif k == 0x19:
                on_redo()
