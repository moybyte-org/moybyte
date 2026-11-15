"""The one on-screen text-entry ladder every prompt on the console types
through: `text_key`, and `TextEntry`, a buffer under it. A typed byte is the
input table's press (`typed`, native/moy_input/moy_input.h), one per physical
press. Dependency-free (freezes cleanly on CPython + MicroPython)."""


# The events one typed byte can mean to a text field.
TE_EDIT, TE_COMMIT, TE_CANCEL = 1, 2, 3


def text_key(text, ch, cap, allow=None):
    """The console's ONE on-screen text-entry key ladder: Backspace trims
    (the caret is the text's end, so Delete has nothing after it), Enter commits, Esc cancels, and printable ASCII appends while the
    buffer is under `cap` (and passes `allow(ch, text)` when a filter is
    given). Returns (text, event) -- the event is TE_EDIT / TE_COMMIT /
    TE_CANCEL, or None for a byte the field ignores. What commit and cancel
    MEAN is the caller's."""
    if ch == 8:
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
    """A typed buffer under `text_key`: the text, its cap and an optional
    per-character `allow(ch, text)` filter."""

    def __init__(self, cap, allow=None):
        self.text = ""
        self.cap = cap
        self.allow = allow

    def open(self, text=""):
        """Start editing `text`, cut to the cap, so what is shown is what
        typing could reproduce."""
        self.text = text[:self.cap]

    def key(self, ch):
        """Apply one byte; returns its event (see text_key)."""
        self.text, ev = text_key(self.text, ch, self.cap, self.allow)
        return ev
