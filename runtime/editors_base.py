"""The shared editor-history base (UndoStack / KeyEdge / UndoRedoMixin) --
the leaf every editor core module builds on. Split out of editors.py so the
per-editor modules (editors_code / editors_sheet / editors_paint_map /
editors_block / editors_music) can import it without a cycle; editors.py
re-exports everything, so `from editors import X` is unchanged everywhere.
Dependency-free (freezes cleanly on CPython + MicroPython)."""


class UndoStack:
    """The bounded undo/redo stack discipline shared by every in-editor history
    (Paint #90, Map #91, Music #92, Blocks #93). It owns ONLY the bookkeeping --
    a bounded undo list + a redo list, the fresh-edit push (append, trim to
    `depth`, drop the redo branch), the undo/redo exchange, and clear. Each
    editor keeps its OWN snapshot/capture/restore semantics: it hands in the
    pushed entries and, on undo/redo, a `reverse(entry)` callable that builds the
    counterpart to stash on the opposite stack, so the three exchange shapes all
    fit --
      * Paint/Blocks push a freshly-captured CURRENT snapshot as the reverse;
      * Map moves the SAME popped rec across (reverse=None -- deltas replay both
        ways);
      * Music pushes _snapshot_of(popped) and skips the push when that is None.
    Plain Python (no deps) so it freezes onto the device."""

    def __init__(self, depth):
        self.depth = depth
        self.undo = []            # committed edits, oldest first
        self.redo = []

    def can_undo(self):
        return bool(self.undo)

    def can_redo(self):
        return bool(self.redo)

    def push(self, entry):
        """Record a pre-edit entry (a fresh edit): bounded-append it and drop the
        redo branch -- the classic fork."""
        self.undo.append(entry)
        if len(self.undo) > self.depth:
            del self.undo[0]
        self.redo = []

    def clear(self):
        """Drop both stacks (a structural change makes recorded steps meaningless)."""
        self.undo = []
        self.redo = []

    def _exchange(self, src, dst, reverse):
        """Pop the newest entry off `src`, push its counterpart (bounded) onto
        `dst`, and return the popped entry for the caller to apply. `reverse(entry)`
        builds what to stash -- None from the callable SKIPS the push (Music's
        stale object); reverse=None moves the SAME entry across (Map's replayable
        deltas). Returns None when `src` is empty (real entries are never None)."""
        if not src:
            return None
        entry = src.pop()
        rev = entry if reverse is None else reverse(entry)
        if rev is not None:
            dst.append(rev)
            if len(dst) > self.depth:
                del dst[0]
        return entry

    def take_undo(self, reverse=None):
        """Pop an undo step (stashing its reverse on redo); None if nothing to do."""
        return self._exchange(self.undo, self.redo, reverse)

    def take_redo(self, reverse=None):
        """Pop a redo step (stashing its reverse on undo); None if nothing to do."""
        return self._exchange(self.redo, self.undo, reverse)


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


class UndoRedoMixin:
    """The undo/redo METHOD surface shared by the four history-keeping editors
    (Paint #90, Map #91, Music #92, Blocks #93) over the UndoStack above: the
    can_undo/can_redo delegates, the read-only `_undo`/`_redo` list proxies
    tests inspect, and the ONE undo()/redo() exchange skeleton. Each editor
    keeps its own snapshot semantics via three hooks --
      * _hist_before()              -- runs first (Map closes an open batch);
      * _hist_reverse(entry)        -- builds the counterpart stashed on the
                                       opposite stack; the default returns the
                                       SAME entry (Map's replayable deltas), a
                                       None return skips the stash (Music's
                                       stale object);
      * _hist_apply(entry, is_redo) -- restores the popped entry.
    undo()/redo() return True iff a step was taken."""

    @property
    def _undo(self):
        return self._hist.undo

    @property
    def _redo(self):
        return self._hist.redo

    def can_undo(self):
        return self._hist.can_undo()

    def can_redo(self):
        return self._hist.can_redo()

    def _hist_before(self):
        pass

    def _hist_reverse(self, entry):
        return entry

    def _hist_apply(self, entry, is_redo):
        raise NotImplementedError

    def _hist_step(self, is_redo):
        self._hist_before()
        take = self._hist.take_redo if is_redo else self._hist.take_undo
        entry = take(self._hist_reverse)
        if entry is None:
            return False
        self._hist_apply(entry, is_redo)
        return True

    def undo(self):
        """Revert the last recorded edit; True iff a step was taken."""
        return self._hist_step(False)

    def redo(self):
        """Re-apply the last undone edit; True iff a step was taken."""
        return self._hist_step(True)
