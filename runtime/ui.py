"""The public UI API (docs/ui_api_2026-10.md): the verbs every app and cart
draws its interface with, and the rect algebra they lay out with.

    ui.page("Settings")
    sound = ui.toggle("Sound", sound)
    vol = ui.slider("Volume", vol, 0, 10)
    cur = ui.list("Theme", themes, cur)
    if ui.button("Save", "primary"):
        save()

Immediate mode: each verb draws now and answers its value, and the engine
(native/moy_ui/moy_ui_api.h) owns focus, keyboard navigation, activation and
each id's state from frame to frame. `o`, the override table, is a dict of
§3's keys and optional everywhere. A Lua cart makes the same calls
(native/moycore/moycore_ui.h) and a compiled app imports them
(native/moy_ui/moy_ui_wasm.h); all three draw the same pixels.

The verbs draw into the frame the host began (`ui_draw.ui_frame`). The rect
algebra is pure tuple math, every rect `(x, y, w, h)`. What lies under the
verbs -- the rect-taking widgets, the marks, `Hits`, `ScrollRegion` -- is
`runtime/ui_draw.py`, the chrome's.
"""

import moy_ui as _C

try:
    from struct import pack as _pack
except ImportError:  # pragma: no cover - a port with only ustruct
    from ustruct import pack as _pack

try:
    import ui_draw as _D
    from widgets import _in as rect_in
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui_draw as _D
    from runtime.widgets import _in as rect_in


# --- rect algebra -------------------------------------------------------------

def inset(rect, dx, dy=None):
    """Shrink `rect` by dx horizontally and dy (default dx) vertically, clamped
    so the result never has negative size."""
    return _C.inset(rect, dx, dx if dy is None else dy)


def cut_top(rect, h):
    """Split off an h-tall band from the top: returns (band, rest)."""
    return _C.cut(rect, 0, h)


def cut_bottom(rect, h):
    """Split off an h-tall band from the bottom: returns (band, rest)."""
    return _C.cut(rect, 1, h)


def cut_left(rect, w):
    """Split off a w-wide band from the left: returns (band, rest)."""
    return _C.cut(rect, 2, w)


def cut_right(rect, w):
    """Split off a w-wide band from the right: returns (band, rest)."""
    return _C.cut(rect, 3, w)


def hsplit(rect, n, gap=0):
    """`n` equal-width columns across `rect` (integer slack goes to the last)."""
    if n <= 1:
        return [rect]
    return _C.split(rect, n, gap, True)


def vsplit(rect, n, gap=0):
    """`n` equal-height rows down `rect` (integer slack goes to the last)."""
    if n <= 1:
        return [rect]
    return _C.split(rect, n, gap, False)


# --- the verbs ----------------------------------------------------------------

_T = _D.UI_TARGET                       # [context, canvas, look]: the host's frame
_KINDS = ("normal", "primary", "tonal", "danger", "toggle")
_ROLES = _C.tokens()                    # a role's index is its place here
_ROLE = 0x1000000
_ABSENT = -1


def _colour(v):
    if v is None:
        return _ABSENT
    if isinstance(v, str):
        try:
            return _ROLE + _ROLES.index(v)
        except ValueError:
            return _ABSENT
    return int(v) & 0xffffff


def _flag(o, key):
    v = o.get(key)
    return _ABSENT if v is None else (1 if v else 0)


def _over(o):
    """The override table as the engine's block (moy_ui_api.h), or None."""
    if not o:
        return None
    oid = o.get("id")
    idb = b"" if oid is None else str(oid).encode("utf-8")
    return _pack("<8i", _colour(o.get("fill")), _colour(o.get("ink")),
                 _colour(o.get("border")), _ABSENT if oid is None else len(idb),
                 _flag(o, "current"), _flag(o, "disabled"), _flag(o, "nav"),
                 _flag(o, "mark")) + idb


def page(title, o=None):
    """A page filling the app's surface (or `at`'s rect) under `title`, its
    rows stacking from the top: the content rect `(x, y, w, h)`."""
    t = _T
    return _C.api_page(t[0], t[1], t[2], title, _over(o))


def label(text, o=None):
    """A row of text; no stop."""
    t = _T
    _C.api_label(t[0], t[1], t[2], text, _over(o))


def button(label, kind="normal", o=None):
    """A button of `kind` (normal, primary, tonal, danger, toggle; an unknown
    one is normal): True the frame it is activated."""
    t = _T
    k = _KINDS.index(kind) if kind in _KINDS else 0
    return _C.api_button(t[0], t[1], t[2], label, k, _over(o))


def toggle(label, on, o=None):
    """A two-state row, `on` its current mark: the new `on`."""
    t = _T
    return _C.api_toggle(t[0], t[1], t[2], label, on, _over(o))


def slider(label, v, lo, hi, o=None):
    """A value from `lo` to `hi`, left and right its own while focused: the
    new `v`."""
    t = _T
    return _C.api_slider(t[0], t[1], t[2], label, v, lo, hi, _over(o))


def list(label, items, cur, o=None):
    """Rows of `items` under `label`, `cur` (from 0, -1 none) wearing the
    current mark; up and down move in it until its first or last row, and
    activation makes the focused row current: the new `cur`."""
    t = _T
    b = "\0".join([str(i) for i in items]).encode("utf-8")
    return _C.api_list(t[0], t[1], t[2], label, b, -1 if cur is None else cur, _over(o))


def part(id, w=0, h=0, o=None):
    """A stop for content only the app can draw, `w` and `h` 0 being the
    row's: `(x, y, w, h, state)`, state's bits FOCUS 1, ACTIVATED 2,
    PRESSED 4, HOVER 8. The app draws into the rect after."""
    t = _T
    return _C.api_part(t[0], t[1], t[2], id, w, h, _over(o))


FOCUS, ACTIVATED, PRESSED, HOVER = 1, 2, 4, 8


def focus(id=None):
    """Move focus to `id`; the focused id (None with none)."""
    return _C.api_focus(_T[0], id)


def keys_taken():
    """Whether the ui used this frame's keys (a cart's own input then skips
    them)."""
    return _C.api_keys_taken(_T[0])


def at(x, y, w, h):
    """Place the next call exactly, leaving the row cursor alone; before a
    `page`, it places the page."""
    _C.api_at(_T[0], x, y, w, h)
