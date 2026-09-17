"""The shared editor-input leaf every editor surface builds on: `KeyEdge`, the
typed-key edge, and `TextEntry` / `text_key`, the one on-screen text-entry
ladder every prompt on the console types through.

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
        self._guard = False

    def reset(self):
        """Forget the last byte (on entering a screen, so a stale key can't fire)."""
        self.prev = 0
        self._guard = False

    def seed(self, k, guard=False):
        """Start from the byte held right now, so a key still latched from
        the screen change is not read as a fresh press. `guard` is the modal
        discipline (widgets.arm_prompt): the next `arming` pass re-seeds and
        must not dispatch."""
        self.prev = k
        self._guard = guard

    def arming(self, k):
        """True exactly once after a guarded seed: that input pass only
        re-seeds the edge with `k`, so the tap/key that OPENED a prompt can
        never be its first keystroke, commit or cancel."""
        if not self._guard:
            return False
        self._guard = False
        self.prev = k
        return True

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


# The events one typed byte can mean to a text field.
TE_EDIT, TE_COMMIT, TE_CANCEL = 1, 2, 3


def text_key(text, ch, cap, allow=None):
    """The console's ONE on-screen text-entry key ladder: Backspace/Delete
    trim, Enter commits, Esc cancels, and printable ASCII appends while the
    buffer is under `cap` (and passes `allow(ch, text)` when a filter is
    given). Returns (text, event) -- the event is TE_EDIT / TE_COMMIT /
    TE_CANCEL, or None for a byte the field ignores. What commit and cancel
    MEAN is the caller's."""
    if ch in (8, 127):
        return text[:-1], TE_EDIT
    if ch in (13, 10):
        return text, TE_COMMIT
    if ch == 27:
        return text, TE_CANCEL
    if 32 <= ch < 127 and len(text) < cap:
        c = chr(ch)
        if allow is None or allow(c, text):
            return text + c, TE_EDIT
    return text, None


class TextEntry:
    """A typed buffer under `text_key`: the text, its cap, an optional
    per-character `allow(ch, text)` filter, and the key EDGE it reads
    `inp.last_key` through, so one press types one character however many
    frames the byte is held. `edge` may be shared between the fields of one
    prompt so a field switch does not re-fire the byte that switched it.

    `open(guard=True)` is the modal discipline (KeyEdge.seed / arming): the
    first input pass after opening only re-seeds the edge, so the tap/key that
    OPENED the prompt is never read as its first keystroke."""

    def __init__(self, cap, allow=None, edge=None):
        self.text = ""
        self.cap = cap
        self.allow = allow
        self.edge = KeyEdge() if edge is None else edge

    def open(self, text="", seed=0, guard=False):
        """Start editing `text` (cut to the cap, so what is shown is what typing
        could reproduce) with the edge seeded to the byte held right now."""
        self.text = text[:self.cap]
        self.edge.seed(seed, guard)

    def key(self, ch):
        """Apply one byte; returns its event (see text_key)."""
        self.text, ev = text_key(self.text, ch, self.cap, self.allow)
        return ev

    def feed(self, inp):
        """One input frame: the event of a fresh byte, else None (the guarded
        opening pass included)."""
        k = inp.last_key
        if self.edge.arming(k) or not self.edge.hit(k):
            return None
        return self.key(k)
