"""The bars' packing from the bar page (`bar_ui`): `pack` answers what
`shell_layout.pack` answers, from the page's rects, for every bar of a
layout of the page's shape (`placed`) on a desk; any other layout, and a
handheld's (its status cluster's ring room is not the page's yet), is
`shell_layout.pack`'s."""

import bar_ui
import layout_base as LB
import shell_layout as SL

s = bar_ui.s
LEFT = ("menu", "chips", "zone")
RIGHT = ("wifi", "battery", "clock")
_PAGE = [None]


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
    return p


def _put(nm, v):
    """The host's write of what the bar shows (the placement pushes it)."""
    if s._v[nm] != v:
        s._v[nm] = v
        s._ver[nm] += 1


def _place(rect, names, cs, fs, clock_w, sys_len, n_items):
    page = _page()
    page._bind()
    g = 2 * cs
    sg = max(g, 4 * fs)
    ic = 16 * cs
    _put("sg", sg)
    _put("cw", clock_w)
    _put("sys_w", sys_len or ic)
    _put("status", bool(n_items) and ("wifi" in names or "battery" in names))
    _put("status_w", n_items * ic + max(0, n_items - 1) * sg)
    for n in LEFT + RIGHT:
        _put(n, n in names)
    _put("tray", any(n in names for n in RIGHT))
    page.unit = cs
    page.place((0, 0, rect[2], rect[3]), desk=True)
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
            r = page.rect_of(name)
            if r is None or (name != "status" and name not in names) or (name == "status" and not s._v["status"]):
                continue
            x, _y, cw, _h = r
            if name in ("wifi", "battery"):
                r = (x, iy, ic, ic)
            elif name in ("menu", "zone", "status"):
                r = (x, iy, cw, ic)
            else:
                r = (x, 0, cw, thick)
            slot = 0 if name in LEFT else 2
            cells.append((name, r, n_items if name == "status" else None, slot))
        out.append((bar[0], rect, (thick - LB.BAR_ROWS * fs) // 2, tuple(cells)))
    return tuple(out)
