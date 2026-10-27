# Map (grep -n a name to jump there):
#   -- rect algebra          inset, cut_top, hsplit, vsplit: the rects the verbs take
#   _over                    the override table as the engine reads it
#   page                     a page and its rows; label, button, toggle, slider, list after it
#   table                    a list in columns; scroll, field, dialog, popover, menu after it
#   grid                     a grid of cards the app draws into; card
#   band                     the rows a shift frame draws; card_clip, keep beside it
#   focus                    focus, moved or read; keys_taken, at
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
the app draws (Appearance's); `empty` an empty page, `tool` an icon button
in an app's tool row, `bar` the title and tools an app lends its bar and
`rule` a divider (the apps' restyle's).

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
verbs -- the rect-taking widgets, the marks, `ScrollRegion` -- is
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
    return (_C.text_width(text if type(text) is str else str(text), scale, f),
            (info[2] if info else 8) * scale)


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
    if f == 0 or (text is not None and not _C.text_covers(
            text if type(text) is str else str(text), f)):
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


# The board's and the browser's module read the table itself (modmoy_ui.c's
# over_dict) and a verb's options as a list or tuple (opts_seq), so a verb
# given either builds nothing; the host's binding takes the bytes below.
_OVER_DICT = getattr(_C, "over_dict", False)
_SEQ = getattr(_C, "opts_seq", False)
_TUPLE = type(())
_LIST = type([])            # `list` is the verb below


def _over(o):
    """The override table as the engine reads it: the dict where the module
    takes one, else its block (moy_ui_api.h); None for none."""
    if not o:
        return None
    if _OVER_DICT:
        return o
    oid = o.get("id")
    idb = b"" if oid is None else str(oid).encode("utf-8")
    hint = o.get("hint")
    wash = o.get("wash")
    lead = o.get("lead")
    gen = o.get("gen")
    flags = None
    if wash is not None or lead is not None or gen is not None:
        flags = (1 if wash else 0) | (2 if lead else 0) | (4 if gen is not None else 0)
    tail = b"" if hint is None and flags is None else _hint(
        None if hint is None else str(hint).encode("utf-8"))
    if flags is not None:
        tail += _pack("<i", flags)
    if gen is not None:
        g = int(gen) & 0xffffffff
        tail += _pack("<i", g - 0x100000000 if g & 0x80000000 else g)
    return _pack("<9i", _colour(o.get("fill")), _colour(o.get("ink")),
                 _colour(o.get("border")), _ABSENT if oid is None else len(idb),
                 _flag(o, "current"), _flag(o, "disabled"), _flag(o, "nav"),
                 _flag(o, "mark"), _flag(o, "steps")) + idb + tail


def _hint(b):
    """A field's placeholder as the block's tail: its length, then its text
    (-1 for none, where a flags word follows)."""
    if b is None:
        return _pack("<i", -1)
    return _pack("<i", len(b)) + b


def page(title, o=None):
    """A page filling the app's surface (or `at`'s rect) under `title`, its
    rows stacking from the top: the content rect `(x, y, w, h)`."""
    t = _T
    return _C.api_page(t[0], t[1], t[2], title, _over(o))


_TONES = ("ink", "dim", "value", "danger")
_FACES = ("ui", "title", "mono", "caption", "key", "op", "result")


def _tone(o):
    v = o.get("tone") if o else None
    return _TONES.index(v) if v in _TONES else 0


def label(text, o=None):
    """A row of text; no stop. `o`'s `wrap` breaks it at word breaks in the
    row's width, at most `lines` lines (16 where it names none) with an
    ellipsis after the last; `align` "center" centres each line; `tone`
    ("dim", "value", "danger") inks it; `face` ("title", "caption", "mono",
    "result" -- Calc's display, the largest) sets it in another type role; `inset` stands it in from the page's edges as a
    group's caption is; `sunk` sets it on a `sunk` inset across the row (a
    stopped run's error, in the "mono" face). Such a label is as tall as its
    lines. (The board's
    and the browser's module read these from the dict itself: modmoy_ui.c's
    label_text.)"""
    t = _T
    if o and not _OVER_DICT and ("wrap" in o or "align" in o or "tone" in o or "face" in o
                                 or "inset" in o or "sunk" in o):
        lines = (o.get("lines") or 16) if o.get("wrap") else 0
        f = o.get("face")
        _C.api_text(t[0], lines, 1 if o.get("align") == "center" else 3 if o.get("sunk")
                    else 2 if o.get("inset") else 0,
                    _tone(o), _FACES.index(f) if f in _FACES else -1)
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
    k = type(items)
    b = items if _SEQ and (k is _TUPLE or k is _LIST) else \
        "\0".join([str(i) for i in items]).encode("utf-8")
    return _C.api_list(t[0], t[1], t[2], label, b, -1 if cur is None else cur, _over(o))


def group(caption="", o=None):
    """A group headed by `caption`: the rows stacked after it sit on its
    card until the next group, or `group()`, which ends it. `o`'s `card`
    opens a group with no caption."""
    t = _T
    if not caption and o and o.get("card"):
        _C.api_group_card(t[0], t[1], t[2])
        return
    _C.api_group(t[0], t[1], t[2], caption, _over(o))


def info(label, value, o=None):
    """A row that shows `value`; no stop."""
    t = _T
    _C.api_value(t[0], t[1], t[2], 1, label, value if type(value) is str else str(value),
                 _over(o))


def link(label, value="", o=None):
    """A row that leads somewhere, `value` where it is now: True the frame it
    is activated."""
    t = _T
    return _C.api_value(t[0], t[1], t[2], 2, label,
                        value if type(value) is str else str(value), _over(o))


# The encodings verbs made lately, so a frame that hands a verb the same
# options draws without building them again: by identity, and for a mutable
# sequence also by a copy an edit no longer equals (a tuple is its own).
_MEMO = 24
_memo_k = [None] * _MEMO
_memo_c = [None] * _MEMO
_memo_v = [None] * _MEMO
_memo_at = [0]


def _memo_get(items, kind):
    k = _memo_k
    for i in range(_MEMO):
        if k[i] is items:
            c = _memo_c[i]
            if c is None or c == items:
                v = _memo_v[i]
                if v[0] == kind:
                    return v[1]
    return None


def _memo_put(items, kind, b, copy):
    i = _memo_at[0]
    _memo_at[0] = (i + 1) % _MEMO
    _memo_k[i] = items
    _memo_c[i] = copy
    _memo_v[i] = (kind, b)
    return b


def _opts(items):
    t = type(items)
    if _SEQ and (t is _TUPLE or t is _LIST) and (not items or type(items[0]) is str):
        return items                    # the module joins its words (opts_seq)
    b = _memo_get(items, 0)
    if b is None:
        b = _memo_put(items, 0, "\0".join([str(i) for i in items]).encode("utf-8"),
                      None if type(items) is tuple else [i for i in items])
    return b


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
    b = _memo_get(colours, -2)
    if b is None:
        b = _memo_put(colours, -2, b"".join([_pack("<i", int(c) & 0xffffff) for c in colours]),
                      None if type(colours) is tuple else [c for c in colours])
    return _C.api_choice(t[0], t[1], t[2], 5, label, b, -1 if cur is None else cur, _over(o))


def hints(items, o=None):
    """Keycap hints at the page's foot, its rows stacking above them (call it
    before them): `items` alternate an action -- `confirm`, `back`,
    `change`, `tabs`, `menu` or `cancel`, which draws as the keys of the input in use
    -- or keys spelled out -- space-separated; `enter`, `left`, `right`,
    `up` and `down` draw as signs -- and what it does. Under touch alone
    there are no hints and the rows take the band. While the keys are on
    the shell's bar, the strip names its keys."""
    t = _T
    _C.api_hints(t[0], t[1], t[2], _opts(_D.bar_or(items)), _over(o))


def fade(rect, up=False):
    """A fade over `rect` in a part, toward the part's well: nothing at one
    edge to nearly the well at the other -- its bottom, or its top with
    `up` -- what says the part's content goes on past that edge (Calc's
    tape, its older sums above)."""
    t = _T
    _D.fade(t[1], rect, _D.FADE_WELL, up, t[2])


def end():
    """The page's end, after its last row: where its rows run on below what
    shows, a fade at its foot (above the hint band) says there is more."""
    t = _T
    _C.api_end(t[0], t[1], t[2])


def hints_width(items):
    """The pixels `ui.hints(items)` takes across with the input in use, its
    margin at either end included, so a band placed with `ui.at` can end
    where they do; 0 under touch alone."""
    t = _T
    return _C.api_hints_w(t[0], t[1], t[2], _opts(_D.bar_or(items)))


def icon(name, o=None):
    """The next row (a label, info, link, cycle, seg, swatches, toggle,
    slider or progress) draws icon `name` before its label: the theme's
    icon set's (`wifi`, `battery`, `settings`, `folder`...), else the glyph
    table's (`lock`, `key`...). True where either names it."""
    return _C.api_icon(_T[0], name)


_COUNT = {"steps": True}


def progress(label, v, hi, o=None):
    """A row showing `v` of `hi` done: a bar and its percent at the right;
    no stop. Placed (`at`) a row and its bar tall, the label and percent
    stand on a line over the bar, which spans the rect. With `o.count` it
    is a count: its label alone says it, no percent."""
    t = _T
    _C.api_progress(t[0], t[1], t[2], label, v, hi, _over(_COUNT if o and o.get("count") else o))


def _cells(items):
    """Text fields, each ending in a NUL (an empty last one included)."""
    return "".join([str(i) + "\0" for i in items]).encode("utf-8")


def _columns(columns):
    b = _memo_get(columns, -1)
    if b is None:
        b = _memo_put(columns, -1, _cells(columns),
                      None if type(columns) is tuple else [c for c in columns])
    return b


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
    b = _memo_get(rows, n)
    if b is None:
        cells = []
        for r in rows:
            r = [str(c) for c in r][:n]
            cells.extend(r)
            cells.extend([""] * (n - len(r)))
        copy = [tuple(r) for r in rows]
        b = _memo_put(rows, n, _cells(cells), tuple(copy) if type(rows) is tuple else copy)
    return _C.api_table(t[0], t[1], t[2], label, _columns(columns), b,
                        -1 if cur is None else cur, _over(o))


def initial(rect, title, r=0):
    """A generated cover over `rect` (corners `r`) for a cart that has none:
    `title`'s initial on the colour block that stays the cart's, as the
    home's cards draw it (`ui_draw.initial_cover`)."""
    _D.initial_cover(_T[1], rect, r, title)


def table_rows():
    """Where the table drawn last put its rows, for an app that draws a
    row's own picture over its picture column's cell (Files' covers):
    `(row0_y, row_h, view, pic_x, pic_side)` -- row i's cell is at
    (pic_x, row0_y + i * row_h + (row_h - pic_side) // 2), shown where it
    lies in `view` -- or None where it had no picture column."""
    return _C.api_table_rows(_T[0])


def table_pictures():
    """After the app drew its pictures over the cells `table_rows` names:
    the table's fades at the edges its rows run on past, again over the
    picture column, so a cut row's picture fades with its row."""
    t = _T
    _C.api_table_pics(t[0], t[1], t[2])


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
    below, else on the side with more room, its body cut to fit and faded
    where it runs on; never over the rect, and 4 pixels at the scale off
    the surface's edges. An `at` rect 0 tall is a point (a finger's): the
    card starts there and slides to fit whole where the surface has the
    room. `w` wide (0: the rect's width), its body the rows
    stacked until
    `popover()` ends it; while it is drawn nothing outside it takes input.
    True, or False the frame a press outside the card or Esc dismissed it
    (the app then stops drawing it). With `o.wash` it washes what it
    covers, as a dialog does: a row's or a card's menu."""
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
    label and icon in the theme's menu danger ink (its look's `danger_ink`,
    else the danger buttons' ink). The item activated this frame
    (from 0), -1 none; an item's id is its text, and its text after a tab
    is the key it answers to, drawn at the row's right (a key `hints` draws
    as a sign -- left, right, up, down, enter -- as that sign)."""
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
    on the page's ground between the caption and the group's rows. `o`'s
    `cols` spreads that many columns across the rect, the gaps taking the
    rest, `margin` keeps that many pixels at either side, `foot` sets a
    card's foot under its picture, `top` the room over its first row and
    `wrap` breaks a title wider than its card onto a second line where the
    foot holds it."""
    t = _T
    if o and o.get("cols"):
        _C.api_grid_spread(t[0], o["cols"], o.get("foot") or 0, o.get("top") or 0,
                           o.get("margin") or 0, 1 if o.get("wrap") else 0)
    _C.api_grid(t[0], t[1], t[2], "" if id is None else id, w, h, _over(o))


def card(title, sub="", o=None):
    """A card in the open grid, `title` (its id) and `sub` under its
    picture, the current mark where `o` says `current`: `(x, y, w, h,
    state)`, the picture's rect -- the app draws its picture there before
    its next ui call, which cuts its corners and rings it -- and the state
    bits `part`'s are. `w` and `h` are 0 for a card scrolled out of view,
    and on a shift frame for one its floor does not reach (`band`).
    `o`'s `badge` names the icon on the picture's corner, and `tone`
    ("value", "danger") inks the sub line; "danger" also veils the picture
    and inks the badge. `icon` is a card with no picture of its own: that
    icon on a tile tinted from `value`, and the app draws nothing there;
    with `locked` the tile is veiled, the icon in the disabled ink, and a
    lock badge on its corner."""
    t = _T
    if o and ("badge" in o or "tone" in o or "icon" in o or "locked" in o):
        _C.api_card_mark(t[0], o.get("badge") or "", 4 if o.get("locked") else _tone(o),
                         o.get("icon") or "")
    return _C.api_card(t[0], t[1], t[2], title, sub, _over(o))


def empty(icon, title, lines=()):
    """An empty page's head, centred in `at`'s rect (else the page's next
    row): `icon` on a disc of `value`, `title`, then `lines` in the dim ink,
    what is missing; the page's one verb that fills it follows as a button.
    The y under it."""
    t = _T
    return _C.api_empty(t[0], t[1], t[2], icon, title, _opts(lines))


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
_MDAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_DOW = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)


def weekday(y, m, d):
    """0 for Monday .. 6 for Sunday (Sakamoto's rule: no datetime on a board)."""
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + _DOW[m - 1] + d + 6) % 7


def month_days(y, m):
    """The days month `m` of year `y` has."""
    if m == 2 and y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
        return 29
    return _MDAYS[m - 1]


def month_h(year, mon, fs=1, desk=False):
    """The height `month(year, mon)` takes at font scale `fs` in the desk's
    class or the handheld's."""
    return _C.month_h((weekday(year, mon, 1) + month_days(year, mon) + 6) // 7, fs,
                      1 if desk else 0)


def month(year, mon, today=0):
    """A month's calendar in `at`'s rect, else stacked on the page: its
    name and year in the title face, the weekdays' initials, its days in
    seven columns from Monday, the weekend's dim and day `today` (0 none)
    bold on a disc of `value`. On the desk and under touch its head holds
    the previous and next month's chevrons: -1 or 1 the frame one is
    pressed, else 0."""
    t = _T
    return _C.api_month(t[0], t[1], t[2], "%s %d" % (MONTHS[mon - 1], year), "MTWTFSS",
                        weekday(year, mon, 1), month_days(year, mon), today)


def toast(text, kind="check", o=None):
    """A toast over the foot of `at`'s rect (else the page's): a pill with
    icon `kind` and `text`, a passing message. The app draws it each frame
    for as long as it shows (two seconds, or until the next key). `o`'s
    `flat` draws it with no soft shadow, as a pill painted once or over a
    running game is. The pill's rect (x, y, w, h)."""
    t = _T
    return _C.api_toast(t[0], t[1], t[2], text, kind, bool(o and o.get("flat")))


def tile(x, y, w, h, icon, o=None):
    """A badge's tile over the rect (x, y, w, h) on a card's surface (a
    popover's): `icon` on a tile tinted from `value`, its corners cut to the
    card radius; with `o.locked` veiled, the icon in the disabled ink and a
    lock badge on its corner, as a locked card's picture."""
    t = _T
    desk = t[3] & 1
    _C.tile(t[1], t[2], (x, y, w, h), icon, 4 if o and o.get("locked") else 0,
            10 if desk else 8, _D._fs(t[1]), desk)


def cursor(x, y, w, h):
    """A canvas's cursor around the rect (x, y, w, h) the app drew: the
    theme's ring round a dark keyline, so it reads on a pixel of any colour
    (Paint's brush, a swatch the keys are on). It is no stop and no focus."""
    _D.cursor(_T[1], (x, y, w, h))


def tool(icon, label, o=None):
    """A tool: icon `icon` as a flat button in `at`'s rect (else a row of
    the page), wearing only its marks: current where `o` says `current`, at
    half strength where it says `disabled`. `label` is its word, and its
    text after a tab the key it answers to ("Paste\tCtrl+V"): on the
    handheld the word stands under the icon; on the desk a resting pointer
    shows the word and the key as a tooltip under it, over the page at
    `end()`. True the frame it is activated."""
    t = _T
    return _C.api_tool(t[0], t[1], t[2], icon, label, _over(o))


def bar(title, tools=(), off=(), back=False):
    """Lend the host's bar this frame's `title` and `tools` (icon names, at
    most six), drawn after the title in the bar's ink, a tool whose place in
    `off` is true at half strength: on the handheld its bar after the way
    back; on the desk the window's title after the app's ("Notes · Story
    ideas"), and the tools are the app's own row there. `back`: the app has
    a page to step back from, so the bar's way back is the app's. The tool
    the bar activated since the last frame (from 0), UP its way back, -1
    none."""
    t = _T
    bits = 0
    for i in range(min(len(off), 6)):
        if off[i]:
            bits |= 1 << i
    return _C.api_bar(t[0], t[1], t[2], title, _opts(tools), bits, back)


def rule(o=None):
    """A rule: a line in the structure colour across `at`'s rect (down it
    where the rect is taller than wide: a split's divider), else a row of
    the page with the line across it."""
    t = _T
    _C.api_rule(t[0], t[1], t[2])


def part(id, w=0, h=0, o=None):
    """A stop for content only the app can draw, `w` and `h` 0 being the
    row's: `(x, y, w, h, state)`, state's bits FOCUS 1, ACTIVATED 2,
    PRESSED 4, HOVER 8. The app draws into the rect after."""
    t = _T
    return _C.api_part(t[0], t[1], t[2], id, w, h, _over(o))


FOCUS, ACTIVATED, PRESSED, HOVER = 1, 2, 4, 8
UP = -2                                 # ui.bar's answer: the bar's way back


def band(r=None):
    """The rows of the page being laid out that this frame draws, as
    `(x, y, w, h)` in the page's coordinates (y from its rows' top): on a
    shift frame (docs/scroll_and_text_2026-10.md §3.3) only those whose
    pixels the frame before left wrong, else what shows of the page. An app
    that loops over many rows or cards skips the ones it lies outside of,
    and passes `keep` for each it skips; None where no page is open. Given
    `r`, an `array("i")` of four, written there and `r` answered."""
    if r is None:
        return _C.api_band(_T[0])
    return _C.api_band(_T[0], r)


def card_clip(r):
    """`r`, an `array("i")` of x, y, w, h, cut in place to where the picture
    of the card (or part) just answered draws this frame -- on a shift frame
    the rect of its floor it lies in, else as it was -- and answered. An app
    whose pictures carry a `gen` clips its picture's draw to it, so what the
    frame moved stays; nothing is made."""
    return _C.api_card_clip(_T[0], r)


def keep(id, h=0):
    """A stop the app does not draw this frame -- it lies outside `band` --
    carried forward as the last frame drew it: a card of the open grid (`id`
    its title or its `id` override), else a row `h` high, so layout, focus
    and the frames after see it. Its state bits, as `part`'s."""
    t = _T
    return _C.api_keep(t[0], t[1], t[2], id, h)


def focus(id=None):
    """Move focus to `id`; the focused id (None with none)."""
    return _C.api_focus(_T[0], id)


def keys_taken():
    """Whether the ui used this frame's keys (a cart's own input then skips
    them)."""
    return _C.api_keys_taken(_T[0])


def input_kind():
    """The input in use (the last one used): "keys", "tdeck", "pad" or
    "touch" -- what a part sized for a finger or a pointer reads."""
    return _D.input_kind()


def at(x, y, w, h):
    """Place the next call exactly, leaving the row cursor alone; before a
    `page`, it places the page."""
    _C.api_at(_T[0], x, y, w, h)
