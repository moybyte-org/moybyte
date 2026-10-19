"""The public UI API (docs/ui_api_2026-10.md): the verbs every app and cart
draws its interface with, and the rect algebra they lay out with.

    ui.page("Settings")
    sound = ui.toggle("Sound", sound)
    vol = ui.slider("Volume", vol, 0, 10)
    cur = ui.list("Theme", themes, cur)
    if ui.button("Save", "primary"):
        save()

`group`, `info`, `link`, `cycle`, `seg`, `swatches` and `hints` are the
Settings-style rows (docs/media/look_2026-10/DESIGN.md, Components); `icon`
puts an icon on the next row or button and `progress` shows how far a task
is. `table` is a list in columns, `scroll` a region whose rows scroll in it,
`field` a line of typed text and `dialog` a modal card (Files' verbs);
`popover` a card hung from a part, holding a picker (the sprite editor's),
or centred where nothing holds focus, and `menu` its rows of verbs (the
system menu, the pause menu);
`tabs` is a row of tabs and `grid` and `card` a grid of cards whose pictures
the app draws (Appearance's).

Immediate mode: each verb draws now and answers its value, and the engine
(native/moy_ui/moy_ui_api.h) owns focus, keyboard navigation, activation and
each id's state from frame to frame. `o`, the override table, is a dict of
§3's keys and optional everywhere. A Lua cart makes the same calls
(native/moycore/moycore_ui.h) and a compiled app imports them
(native/moy_ui/moy_ui_wasm.h); all three draw the same pixels.

The verbs draw into the frame the host began (`ui_draw.ui_frame`). The rect
algebra is pure tuple math, every rect `(x, y, w, h)`; `text_size` and
`has_symbols` measure what an app prints into a part, and `face` names the
theme's face it prints in. What lies under the
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


def text_size(text, scale=1):
    """`(w, h)`: the pixels `text` covers through a canvas's `print` now, its
    font the theme's, at the whole-number `scale` -- `h` is the line."""
    f = _C.print_font()
    info = _C.font_info(f)
    return _C.text_width(str(text), scale, f), (info[2] if info else 8) * scale


_FACE = [None]                          # the print font `face` put aside


def face(name=None, text=None):
    """`face(name)`: a canvas's `print` (and `text_size`) draw in the
    theme's `name` face -- a type role: "ui", "title", "caption", or a
    display's "key", "op" and "result" -- until `face()` puts the one it
    replaced back. True where the theme installs a face for `name` that
    draws every character of `text` (when given); False, changing nothing,
    where it does not (a pixel theme, and a cart's run, draw in their print
    font)."""
    if name is None:
        if _FACE[0] is not None:
            _C.print_font(_FACE[0])
            _FACE[0] = None
        return True
    f = _C.font(name)[0]
    if f == 0 or (text is not None and not _C.text_covers(str(text), f)):
        return False
    was = _C.print_font(f)
    if was is not None and _FACE[0] is None:
        _FACE[0] = was
    return was is not None


def has_symbols():
    """Whether that font draws Latin-1 and the symbols `− × ÷ … ← → ↑ ↓ •`
    (a version 2 font, native/moy_gfx/moy_text.h); petme128 and a version 1
    font draw ASCII alone."""
    info = _C.font_info(_C.print_font())
    return info is not None and info[4] == 0


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
    hint = o.get("hint")
    tail = b"" if hint is None else _hint(str(hint).encode("utf-8"))
    return _pack("<9i", _colour(o.get("fill")), _colour(o.get("ink")),
                 _colour(o.get("border")), _ABSENT if oid is None else len(idb),
                 _flag(o, "current"), _flag(o, "disabled"), _flag(o, "nav"),
                 _flag(o, "mark"), _flag(o, "steps")) + idb + tail


def _hint(b):
    """A field's placeholder as the block's tail: its length, then its text."""
    return _pack("<i", len(b)) + b


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
    new `v`. `o.steps` draws a segment per step and no number."""
    t = _T
    return _C.api_slider(t[0], t[1], t[2], label, v, lo, hi, _over(o))


def list(label, items, cur, o=None):
    """Rows of `items` under `label`, `cur` (from 0, -1 none) wearing the
    current mark; up and down move in it until its first or last row, and
    activation makes the focused row current: the new `cur`."""
    t = _T
    b = "\0".join([str(i) for i in items]).encode("utf-8")
    return _C.api_list(t[0], t[1], t[2], label, b, -1 if cur is None else cur, _over(o))


def group(caption="", o=None):
    """A group headed by `caption`: the rows stacked after it sit on its
    card until the next group, or `group()`, which ends it."""
    t = _T
    _C.api_group(t[0], t[1], t[2], caption, _over(o))


def info(label, value, o=None):
    """A row that shows `value`; no stop."""
    t = _T
    _C.api_value(t[0], t[1], t[2], 1, label, str(value), _over(o))


def link(label, value="", o=None):
    """A row that leads somewhere, `value` where it is now: True the frame it
    is activated."""
    t = _T
    return _C.api_value(t[0], t[1], t[2], 2, label, str(value), _over(o))


def _opts(items):
    return "\0".join([str(i) for i in items]).encode("utf-8")


def cycle(label, items, cur, o=None):
    """`items[cur]` between chevrons; left and right step it (wrapping) while
    focused, activation takes the next: the new `cur`."""
    t = _T
    return _C.api_choice(t[0], t[1], t[2], 3, label, _opts(items), -1 if cur is None else cur,
                         _over(o))


def seg(label, items, cur, o=None):
    """A segmented control, `items[cur]` wearing the current mark; left and
    right move it while focused, a tap picks: the new `cur`."""
    t = _T
    return _C.api_choice(t[0], t[1], t[2], 4, label, _opts(items), -1 if cur is None else cur,
                         _over(o))


def swatches(label, colours, cur, o=None):
    """A row of colour dots (each 0xRRGGBB), `colours[cur]` wearing the
    current mark; left and right move it while focused, a tap picks: the new
    `cur`."""
    t = _T
    b = b"".join([_pack("<i", int(c) & 0xffffff) for c in colours])
    return _C.api_choice(t[0], t[1], t[2], 5, label, b, -1 if cur is None else cur, _over(o))


def hints(items, o=None):
    """Keycap hints at the page's foot, its rows stacking above them (call it
    before them): `items` alternate an action -- `confirm`, `back`,
    `change`, `tabs`, `menu` or `cancel`, which draws as the keys of the input in use
    -- or keys spelled out -- space-separated; `enter`, `left`, `right`,
    `up` and `down` draw as signs -- and what it does. Under touch alone
    there are no hints and the rows take the band."""
    t = _T
    _C.api_hints(t[0], t[1], t[2], _opts(items), _over(o))


def hints_width(items):
    """The pixels `ui.hints(items)` takes across with the input in use, its
    margin at either end included, so a band placed with `ui.at` can end
    where they do; 0 under touch alone."""
    t = _T
    return _C.api_hints_w(t[0], t[1], t[2], _opts(items))


def icon(name, o=None):
    """The next row (a label, info, link, cycle, seg, swatches, toggle,
    slider or progress) draws icon `name` before its label: the theme's
    icon set's (`wifi`, `battery`, `settings`, `folder`...), else the glyph
    table's (`lock`, `key`...). True where either names it."""
    return _C.api_icon(_T[0], name)


def progress(label, v, hi, o=None):
    """A row showing `v` of `hi` done: a bar and its percent at the right;
    no stop."""
    t = _T
    _C.api_progress(t[0], t[1], t[2], label, v, hi, _over(o))


def _cells(items):
    """Text fields, each ending in a NUL (an empty last one included)."""
    return "".join([str(i) + "\0" for i in items]).encode("utf-8")


def table(label, columns, rows, cur, o=None):
    """A list in columns: `label` its caption (none where empty), `columns`
    the headers -- an empty first one makes column 0 the picture column,
    its cells icon names -- and `rows` each a sequence of cells. The current
    row `cur` (from 0, -1 none) follows the focus; the columns between the
    first text column and the last, then the last, drop where it would show
    fewer than eleven characters. `(cur, opened)`, `opened` True the frame
    the current row is opened (Enter on it, or a tap on it again)."""
    t = _T
    n = len(columns)
    cells = []
    for r in rows:
        r = [str(c) for c in r][:n]
        cells.extend(r)
        cells.extend([""] * (n - len(r)))
    return _C.api_table(t[0], t[1], t[2], label, _cells(columns), _cells(cells),
                        -1 if cur is None else cur, _over(o))


def scroll(id=None, o=None):
    """`scroll(id)` begins a scroll region in `at`'s rect (else the page's
    room below its rows): the rows stacked after it scroll in it, by the keys
    that move focus and by a drag, until `scroll()` ends it."""
    t = _T
    _C.api_scroll(t[0], t[1], t[2], "" if id is None else id, _over(o))


def field(label, text, max=32, o=None):
    """A line of text the keyboard types into while it is focused, at most
    `max` bytes: `(text, done)`, `done` True the frame Enter is pressed in
    it. An empty field shows `o`'s `hint`, dim."""
    t = _T
    raw = str(text).encode("utf-8")[:max]
    b = bytearray(max)
    b[:len(raw)] = raw
    r = _C.api_field(t[0], t[1], t[2], label, b, len(raw), _over(o))
    return str(bytes(b[:r & 0xffff]), "utf-8"), bool(r & 0x10000)


def dialog(title=None, buttons=(), kind="primary", o=None):
    """`dialog(title, buttons)` begins a modal card centred on the surface,
    its `buttons` at its foot (the last of `kind`, the others normal) and its
    body the rows stacked until `dialog()` ends it; while it is drawn nothing
    under it takes input, and focus opens on its body's first stop, else its
    first button. The button activated this frame (from 0), -1 none."""
    t = _T
    if title is None:
        return _C.api_dialog(t[0], t[1], t[2], "", b"", 0, None)
    k = _KINDS.index(kind) if kind in _KINDS else 1
    return _C.api_dialog(t[0], t[1], t[2], title, _opts(buttons), k, _over(o))


def popover(id=None, w=0, o=None):
    """`popover(id, w)` begins a card hung under `at`'s rect (else under the
    stop that holds focus as it opens), above it where there is no room
    below, `w` wide (0: the rect's width), its body the rows stacked until
    `popover()` ends it; while it is drawn nothing outside it takes input.
    True, or False the frame a press outside the card or Esc dismissed it
    (the app then stops drawing it)."""
    t = _T
    if id is None:
        _C.api_popover(t[0], t[1], t[2], "", 0, None)
        return False
    return bool(_C.api_popover(t[0], t[1], t[2], id, w, _over(o)))


def menu(label, items, icons=(), o=None, kinds=()):
    """A menu: its caption `label` ("" none, and then a rule where rows sit
    above it), then a row per item, each a stop with the icon named at its
    place in `icons` ("" none) before it. Stacked on the page (in a popover,
    its card), or from `at`'s rect's top left. `kinds` names a row's kind at
    its place ("" plain): a "danger" row, a verb that destroys, draws its
    label and icon in the danger buttons' ink. The item activated this frame
    (from 0), -1 none; an item's id is its text, and its text after a tab
    is the key it answers to, drawn at the row's right."""
    t = _T
    danger = 0
    for i in range(min(len(kinds), 32)):
        if kinds[i] == "danger":
            danger |= 1 << i
    if danger:
        _C.api_menu_danger(t[0], danger)
    return _C.api_menu(t[0], t[1], t[2], label, _opts(items), _opts(icons), _over(o))


def tabs(label, items, cur, o=None):
    """A row of tabs from the left, `items[cur]` wearing the current mark,
    `label` its id (it draws none); left and right move it while focused, a
    tap picks: the new `cur`."""
    t = _T
    return _C.api_choice(t[0], t[1], t[2], 9, label, _opts(items), -1 if cur is None else cur,
                         _over(o))


def grid(id=None, w=0, h=0, o=None):
    """`grid(id, w, h)` begins a grid of cards whose pictures are `w` x `h`,
    as many columns as the row holds, centred, its rows stacking on the page
    (or from `at`'s rect's top left); `grid()` ends it. In a group it lies
    on the page's ground between the caption and the group's rows."""
    t = _T
    _C.api_grid(t[0], t[1], t[2], "" if id is None else id, w, h, _over(o))


def card(title, sub="", o=None):
    """A card in the open grid, `title` (its id) and `sub` under its
    picture, the current mark where `o` says `current`: `(x, y, w, h,
    state)`, the picture's rect -- the app draws its picture there before
    its next ui call, which cuts its corners and rings it -- and the state
    bits `part`'s are. `w` and `h` are 0 for a card scrolled out of view."""
    t = _T
    return _C.api_card(t[0], t[1], t[2], title, sub, _over(o))


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
