"""The bars' packing and drawing from the bar page (`bar_ui`): `pack`
answers what `shell_layout.pack` answers, from the page's rects, for every
bar of a layout of the page's shape (`placed`) on a desk; any other layout,
and a handheld's (its status cluster's ring room is not the page's yet), is
`shell_layout.pack`'s. `draw` is a frame of the page onto a bar's strip."""

import bar_ui
import iconsets as _iconsets
import layout_base as LB
import shell_layout as SL
import status_items as _status
import uitree as S

s = bar_ui.s
LEFT = ("menu", "chips", "zone")
RIGHT = ("wifi", "battery", "clock")
_PAGE = [None]
SHOWN = {"wifi": "wifi_on", "clock": "clock_on"}     # a widget's shown= state, where not its name
_SHOWN = tuple((n, SHOWN.get(n, n)) for n in LEFT + RIGHT if n != "battery")
_DIRTY = [False]                # a state written since the page's store last took the writes
_LAST = [None] * 8              # the bar `_states` wrote last: names, cs, fs, clock_w, sys_len, n, glyph, wins


def _ordered(names, order):
    j = 0
    for n in names:
        while j < len(order) and order[j] != n:
            j += 1
        if j == len(order):
            return False
        j += 1
    return True


def placed(bar):
    """Whether the engine places `bar` (one of a layout's bars): a top or
    bottom bar of the themes' shape, some of `LEFT` from its start and of
    `RIGHT` from its end, in their order, its zone among them."""
    return (bar[0] in ("top", "bottom") and not bar[3] and "zone" in bar[2]
            and _ordered(bar[2], LEFT) and _ordered(bar[4], RIGHT) and not (len(bar) > 5 and bar[5]))


def _page():
    p = _PAGE[0]
    if p is None:
        import uitree_editor
        p = _PAGE[0] = uitree_editor.page(bar_ui.bar, None, None)
        _WINS[0] = _ITEMS_W[0] = _LAST[0] = None    # (a new page's store holds none of them)
    return p


def _put(nm, v):
    """The host's write of what the bar shows (the placement pushes it)."""
    if s._v[nm] != v:
        s._v[nm] = v
        s._ver[nm] += 1
        _DIRTY[0] = True


def _place(rect, names, cs, fs, clock_w, sys_len, n_items):
    page = _states(names, cs, fs, clock_w, sys_len, n_items)
    page.unit = cs
    page.place((0, 0, rect[2], rect[3]), desk=True)
    return page


_WINS = [None]                  # the `wins` rows last written (the window manager's, kept while they stand)
SEL = [-1]                      # the status item whose menu is open (the bar layer's), -1 none
PAUSED = ["ink_dim"]            # a paused item's ink as a tone: the look's content role (the bar layer's)
_ITEMS = [None, -1, -2, None, -1]   # the `items` rows, and the items' gen, SEL, PAUSED and count they show
_ITEMS_W = [None]               # the `items` rows last written
_KEYS = tuple(str(k) for k in range(_status.CAP))


def _items(n):
    """The `items` rows for `n` status items (`status_items.ITEMS` in its
    order, an item it does not hold yet as the app glyph in the accent): its
    key its index, its icon the set's slot or the app glyph where the set
    has none, its tone its state's ink, the one whose menu is open marked.
    The list is kept while all of that stands."""
    it = _status.ITEMS
    g = it.gen
    c = _ITEMS
    if c[0] is not None and c[1] == g and c[2] == SEL[0] and c[3] is PAUSED[0] and c[4] == n:
        return c[0]
    rows = []
    for k in range(n):
        x = it.at(k)
        icon = x.icon if x is not None else None
        st = x.state if x is not None else _status.ON
        rows.append({"k": _KEYS[k], "icon": icon if icon in _iconsets.SLOT else "app",
                     "on": k == SEL[0],
                     "tone": "danger" if st == _status.ERROR else PAUSED[0] if st == _status.PAUSED
                     else "accent"})
    c[0], c[1], c[2], c[3], c[4] = rows, g, SEL[0], PAUSED[0], n
    return rows


def _states(names, cs, fs, clock_w, sys_len, n_items, glyph=None, wins=None):
    """The page's states written for a bar of widgets `names`; `wins` the
    chips' rows where given (a list the window manager keeps while it
    stands: written again only when it is another). The bar written last
    again (`names` the same list) writes nothing."""
    page = _page()
    page._bind()
    n = min(n_items, _status.CAP)
    items = _items(n)
    L = _LAST
    if (L[0] is names and L[1] == cs and L[2] == fs and L[3] == clock_w and L[4] == sys_len
            and L[5] == n and L[6] is glyph and (wins is None or wins is _WINS[0])
            and items is _ITEMS_W[0]):
        return page
    if wins is not None and wins is not _WINS[0]:
        _WINS[0] = wins
        s._v["wins"] = S._check(s._t["wins"], wins, "s.wins")
        s._ver["wins"] += 1
        _DIRTY[0] = True
    if items is not _ITEMS_W[0]:
        _ITEMS_W[0] = items
        s._v["items"] = S._check(s._t["items"], items, "s.items")
        s._ver["items"] += 1
        _DIRTY[0] = True
    sg = max(6 * cs, 4 * fs)        # a gap between inks (an icon item is its ink wide)
    _put("sg", sg)
    _put("cw", clock_w)
    _put("sys_w", sys_len or 16 * cs)
    _put("status", bool(n) and ("wifi" in names or "battery" in names))
    tray = False
    for nm, st in _SHOWN:
        _put(st, nm in names)
    for nm in RIGHT:
        tray = tray or nm in names
    if glyph is not None:
        _put("batt_icon", "battery" in names and not glyph)
        _put("batt_glyph", "battery" in names and glyph)
    elif "battery" not in names or not (s._v["batt_icon"] or s._v["batt_glyph"]):
        _put("batt_icon", "battery" in names)
        _put("batt_glyph", False)
    _put("tray", tray)
    L[0], L[1], L[2], L[3], L[4], L[5], L[6] = names, cs, fs, clock_w, sys_len, n, glyph
    return page


def pack(w, h, rows, cs, fs, clock_w, text_w=None, of=None, sys_len=0, cluster=None, items=None):
    """`shell_layout.pack`'s answer, the engine placing every bar of `of`
    where it places them all (`placed`)."""
    if of is None:
        of = LB.bars()
    n_items = SL.ITEMS[0] if items is None else items
    if cluster is None:
        cluster = LB.metric_class() != LB.DESK
    if cluster or not all(placed(b) for b in of):
        return SL.pack(w, h, rows, cs, fs, clock_w, text_w, of, sys_len, cluster, items)
    rects = LB.bar_rects_of(w, h, rows, of)
    ic = 16 * cs
    out = []
    for bar, rect in zip(of, rects):
        names = bar[2] + bar[4]
        page = _place(rect, names, cs, fs, clock_w, sys_len, n_items)
        thick = rect[3]
        iy = 1 + (thick - LB.BAR_ROWS * cs) // 2
        cells = []
        for name in LEFT + ("status",) + RIGHT:
            if name == "status":
                r = _items_span(page, n_items) if s._v["status"] else None
            else:
                r = page.rect_of("battery_st" if name == "battery" and s._v["batt_glyph"] else name)
            if r is None or (name != "status" and name not in names):
                continue
            x, _y, cw, _h = r
            if name in ("menu", "zone", "status", "wifi", "battery"):
                r = (x, iy, cw, ic)
            else:
                r = (x, 0, cw, thick)
            slot = 0 if name in LEFT else 2
            cells.append((name, r, n_items if name == "status" else None, slot))
        out.append((bar[0], rect, (thick - LB.BAR_ROWS * fs) // 2, tuple(cells)))
    return tuple(out)


def _items_span(page, n):
    """The status items' cell: the span of the page's `n` item cells."""
    x0 = x1 = None
    for k in range(min(n, _status.CAP)):
        r = page.rect_of(_KEYS[k] + "/item")
        if r is not None:
            x0 = r[0] if x0 is None else min(x0, r[0])
            x1 = r[0] + r[2] if x1 is None else max(x1, r[0] + r[2])
            y, h = r[1], r[3]
    return None if x0 is None else (x0, y, x1 - x0, h)


def item_rects(rect, names, cs, fs, clock_w, sys_len, n_items, out):
    """The status items of a bar at `rect` (a packed bar of the page's
    shape): their rects in its bar's coordinates into `out` (cleared), in
    the items' order."""
    page = _place(rect, names, cs, fs, clock_w, sys_len, n_items)
    del out[:]
    for k in range(min(n_items, _status.CAP)):
        r = page.rect_of(_KEYS[k] + "/item")
        if r is not None:
            out.append(r)
    return out


def chip_rects(rect, names, cs, fs, clock_w, sys_len, n_items, wins, out):
    """The chips of a bar at `rect` (a packed bar of the page's shape) for
    the window manager's rows `wins`: [(kind, rect)] into `out` (cleared)
    on the canvas the bar lies on, a chip the row's fit rule dropped left
    out."""
    page = _states(names, cs, fs, clock_w, sys_len, n_items, wins=wins)
    page.unit = cs
    page.place((0, 0, rect[2], rect[3]), desk=True)
    del out[:]
    for row in wins:
        k = row["k"]
        r = page.rect_of(k + "/chip")
        if r is not None:
            out.append((k, (rect[0] + r[0], rect[1] + r[1], r[2], r[3])))
    return out


class _NoDamage:
    """The page's damage: a bar's strip is drawn again when its key moves
    (`BarLayer._draw_bar`), never because the page asked."""

    def all(self):
        pass

    def add(self, *a):
        pass


_CTX = [None]                   # the bar's own toolkit context (its stops, its kept state)
_D = [None]


def _ui_draw():
    D = _D[0]
    if D is None:
        import ui_draw as D
        _D[0] = D
    return D
_ORIGIN = [0, 0]                # where the page is drawn on its canvas: the bar's origin there


def draw(cv, th, rect, names, cs, fs, clock_w, sys_len, n_items, wicon, time, glyph, clip=None,
         wins=None):
    """A frame of the bar page drawn onto `cv` at `rect` (a bar's, on its
    strip at 0, 0 or on a canvas the bar lies on), placed as `pack` placed
    it, the canvases drawn by `bar_ui.PAINT` in the bar's own coordinates.
    `clip` (on `cv`, which the caller has clipped to it): the part drawn
    again, every draw inside it, and `cv` left clipped to it. The
    toolkit's target the host's frame set is given back."""
    page = _states(names, cs, fs, clock_w, sys_len, n_items, glyph, wins)
    _put("wicon", wicon)
    _put("time", time)
    if _DIRTY[0]:
        _DIRTY[0] = False
        pushed = getattr(page, "committed", None)
        if pushed is not None:
            pushed()            # the C engine's store takes the writes (the reference reads them)
    if page._damage is None:
        page._damage = _NoDamage()
    D = _ui_draw()
    ctx = _CTX[0]
    if ctx is None:
        ctx = _CTX[0] = D.ui_context()
    t = D.UI_TARGET
    a, b, c, d = t[0], t[1], t[2], t[3]
    _ORIGIN[0] = rect[0]
    _ORIGIN[1] = rect[1]
    page.clip_to = clip
    try:
        D.ui_frame(ctx, cv, th, rect=rect, desk=True, elsewhere=True)
        page.unit = cs
        page.bare = True
        page.paint(0, cv, th, rect, True)
    finally:
        page.clip_to = None
        t[0], t[1], t[2], t[3] = a, b, c, d
        if clip is not None:
            cv.clip(clip[0], clip[1], clip[2], clip[3])
    return page
