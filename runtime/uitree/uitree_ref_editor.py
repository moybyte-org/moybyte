"""The reference engine's Editor kinds (`use_engine("reference")`): the
replay of the Editor's kinds (uitree.py EXT-4) through today's `ui` verbs,
regions stacked down the page, rows, tabs, panes over `ui.split` with its
dividers, a pane's header, the text editor over `ui.text_view` and the
document's keys, a slot, a status line, popovers hung from a node's part,
and the bridge's `legacy` node.

Its `Engine` is `uitree_editor.Engine` (what the C engine's Editor kinds
share with it) over `uitree_ref.Engine`; nothing on the C engine's path
imports this module."""

from moy_input import SHIFT as _SHIFT
import ui as _v
import ui_draw as _ud
import layout_base as _lb
import uitree as S
from uitree import _NOW
import uitree_app
import uitree_editor
from uitree_editor import _clip_back, _clip_in, _DOWN, fit, HEADER, LC_ALONE, LC_CUR, LC_MARKED, LC_ON, LC_PRIMARY, _LEFT, lent_place, _REGIONS, _RIGHT, _TEXT_PAD, _UP
import uitree_ref
from ui_host import _outside


_FILL = ("switch", "panes", "pane", "text_editor", "slot", "panel", "case", "canvas", "legacy")

# what the hosted phase walks to place the legacy nodes: the regions
_PLACED = ("case", "switch", "panes", "pane", "panel", "legacy", "row")

_WHOLE = _REGIONS + ("status", "canvas", "row")     # a row's cells the row's whole height

_BARE = {"bare": True}      # a page whose bodies its host drew opens over them

# The panes' sizes in desk units (runtime/editors/editor_panes.py's, the
# desk Editor's): the gutter, a pane's header, a divider's handle drawn and
# the touch it takes, a touch drag's travel before it takes, the
# Ctrl+Alt+arrow step, a pane's least size where it names none.
GUTTER = 6

HANDLE = (6, 36)

HANDLE_HIT = (16, 48)

DRAG_SLOP = 8

FLOOR = (64, HEADER + 16)

RADIUS = 8

DOUBLE_MS = 400            # a second tap this soon on a divider resets it

# The key event (native/moy_input/moy_input.h): the code in the low 16
# bits, the modifiers above.
_MODS_AT = 16

_CTRL, _KSHIFT, _ALT = 1, 2, 4

_F6 = 0x101 + 5


_ENTERS = (10, 13)

STATUS_SEP = " \u00b7 "  # a status template's fields are split at it


class Engine(uitree_editor.Engine, uitree_ref.Engine):
    """The replay of the Editor's kinds over `uitree_ref.Engine`: regions
    stacked down the page, rows, panes and their dividers, the text editor,
    popovers hung from a node's part."""

    def _place_hosted(self, dt, cv, th, content):
        """`hosted`'s pass where the page hosts a surface: the regions
        placed, the page's ground rounded under them, their bodies drawn."""
        placed = self.placed
        was = _NOW.app, _NOW.frame
        _NOW.app, _NOW.frame = self, True
        try:
            self._frame_setup(self.root)
            rect = tuple(self.content) if self.ground is not None else tuple(content)
            hint = self._hints() if self.band else None
            if hint and self._band_w(hint, cv, th) > 0:
                rect = (rect[0], rect[1], rect[2], rect[3] - self._h_row())
            self._place_walk(rect)
            if self.under and placed:
                self._ground_round(cv, th, placed)
            self.draw_hosted(cv, th, dt)
        finally:
            _NOW.app, _NOW.frame = was

    # -- the page: regions stacked, overlays last ---------------------------

    def _frame_setup(self, n):
        w, h = self.content[2], self.content[3]
        self.classes = ("desk" if self.desk else "handheld", "wide" if w * 3 > h * 4 else "tall")
        self.lenders = {}
        S.LIVE = S.live_units(n, self.lenders)
        self.du = self.unit or max(1, _lb.chrome_size(self.fs) // _lb.render_scale())
        self.pane_order = []
        self.panes_root = None
        self.splits = {}

    def _replay_page(self, n):
        self._frame_setup(n)
        self.canvases = {}
        self.legacies = {}
        self.flash = None
        hosting = self._hosted_no == self.frame_no
        g = self.ground
        if g is not None:
            _v.at(*g)
        # the hosted phase drew the ground round the bodies: the page opens
        # over them bare
        rect = _v.page(n.p["title"](None), _BARE if (hosting and self.placed) or self.bare else None)
        if g is not None:
            rect = tuple(self.content)
        hint = self._hints()
        if hint and self.band and _v.hints_width(hint) > 0:
            # the page's band: under everything it stacks
            band, rect = _v.cut_bottom(rect, self._h_row())
            _v.at(*band)
            _v.hints(hint)
        last = self.focused                     # the last frame's focus
        prev, self.ids = self.ids, {}
        self.anchors = {}
        self._rect_pass += 1
        self.dividers = {}
        moved, ahead = self._land_focus(prev)
        self.rect = rect
        self.dups = set()
        self.late = []
        self._stack(n, None, rect)
        if self.flash is not None:
            self._draw_flash()
        for k, row, r in self.late:
            self._draw(k, row, r)
        self._focus_after(moved, ahead, hint, last)
        self._pane_follow()

    # -- the bridge: surfaces hosted in `legacy` nodes ------------------------

    def rect_of(self, key):
        """Where node `key` was placed by the last frame or placement, else None."""
        return self.rects.get(key) if self.rects_at.get(key) == self._rect_pass else None

    def _put_rect(self, key, r):
        self.rects[key] = r
        self.rects_at[key] = self._rect_pass

    def _place_walk(self, rect):
        self._rect_pass += 1
        self.placing = True
        try:
            self._stack(self.root, None, rect)
        finally:
            self.placing = False

    def place(self, area, desk=True):
        """A host's placement, with no frame begun and nothing drawn: the
        page's regions laid out over `area` (a panes host hands its panes
        area), each legacy node's rect in `placed`, the panes in
        `arranged` and their splits for `draw_splits`. The bodies are drawn
        by `draw_hosted`, as often as the host draws them."""
        self._bind()
        self.content = area
        self.desk = desk
        self._ready()
        self.placed = []
        was = _NOW.app, _NOW.frame
        _NOW.app, _NOW.frame = self, True
        try:
            self._frame_setup(self.root)
            self._place_walk(tuple(area))
        finally:
            _NOW.app, _NOW.frame = was
        self._hosted_no = self.frame_no

    def draw_splits(self):
        """The last placement's dividers drawn into the verbs pass the host
        has open (its own: its focus, its split frame and its pad's moves
        read them there), each split's focus its child pane the keys reach
        (`pane_cur`). What a divider answers is the engine's: a let-go
        writes the held sizes beside it, a double tap puts them back, one
        held at a minimum sets `flash`; `div_held` says which is held."""
        self.div_held = None
        self.flash = None
        if not self.splits:
            return
        self._bind()
        was = _NOW.app
        _NOW.app = self
        try:
            for sid, sp in self.splits.items():
                n, kids, sizes, mins, held, rect, axis, gap = sp[:8]
                self._split(n, sid, kids, sizes, mins, held, rect, axis, gap)
        finally:
            _NOW.app = was

    def _hosts(self, n):
        """Whether the page holds a legacy node (asked once a build)."""
        got = getattr(self, "_hosts_of", (None, False))
        if got[0] is not n:
            def walk(k):
                return k.kind == "legacy" or any(walk(c) for c in k.kids)
            got = self._hosts_of = (n, n is not None and walk(n))
        return got[1]

    def _band_w(self, hint, cv, th):
        """The page's band width for `hint`, asked of this page's context
        with no frame begun (the hosted phase places above it)."""
        t = _ud.UI_TARGET
        was = (t[0], t[1], t[2], t[3])
        t[0], t[1], t[2] = self._ui.ctx, cv, _ud._look(th)
        try:
            return _v.hints_width(hint)
        finally:
            t[0], t[1], t[2], t[3] = was

    def _ground_round(self, cv, th, placed):
        """The page's ground where the hosted bodies leave it showing: the
        Editor's `panel`, never filled under a body (it draws its own)."""
        left = [tuple(self.ground if self.ground is not None else self.content)]
        for _n, _row, r in placed:
            out = []
            for piece in left:
                out += _outside(piece, r)
            left = out
        col = th["panel"]
        for x, y, w, h in left:
            if w > 0 and h > 0:
                cv.rect(x, y, w, h, col)

    def _r_legacy(self, n, row, rect):
        if self.placing:
            self.placed.append((n, row, rect))
            return
        if self._hosted_no != self.frame_no:
            raise S.UIError("N03", "ui.legacy(%r) in a page whose host ran no hosted phase" % n.key,
                            where=n.where, fix="the host calls engine.hosted(dt, cv, th, content, desk) "
                            "before host.begin, then engine.paint")
        if not any(p[0] is n and p[2] == rect for p in self.placed):
            # placed elsewhere than the pass lays it out this frame (a band
            # came or went between the phases): the next frame draws it
            # where it lies now
            S._trace("legacy", n.key, "placed at %r, laid out at %r: drawn again" % (
                next((p[2] for p in self.placed if p[0] is n), None), rect))
            self._damage.all()
        # one stop over the body, no well: the body is drawn
        _v.at(*rect)
        _v.part(n.key, 0, 0, {"id": n.key, "bare": True})
        self._id(n.key, self._slot(n, None).pair)
        self.legacies[n.key] = (n, rect)

    def _h_row(self):
        return 48 if self.desk else 22 * self.fs

    def _height(self, k):
        if k.kind == "canvas" and k.l.get("h"):
            return k.l["h"] * (1 if self.desk else self.fs)
        if k.kind in _FILL:
            return None
        if k.kind == "status":
            # one band height in every theme under a version 2 `ui` face,
            # 12 under petme128, never under the face's line and a pixel
            # above and below
            fid, _w, line, _a = _ud.font("ui")
            return max(20 if _ud.font_v2(fid) else 12, line + 2) * self.fs
        if k.kind == "tools" and (self._in_pane_head(k) or k.l.get("lend")):
            return 0
        if k.kind == "label" and ("lines" in k.l or k.l.get("wrap") == "word"):
            # a wrapped label: its lines of the ui face, a pad above and below
            line = _ud.font("ui")[2]
            return (k.l.get("lines", 2) * (line + 2) + 6) * self.fs
        if k.kind == "row" and (k.l.get("grow") or any(c.kind in _REGIONS for c in k.kids)):
            return None         # a growing row, a row of regions: a fill of its stack
        if k.kind == "row" and any(c.kind == "status" for c in k.kids) and \
                not any(c.kind == "field" for c in k.kids):
            return self._height(next(c for c in k.kids if c.kind == "status"))   # a status line's
        return self._h_row()

    def _in_pane_head(self, k):
        return k.parent is not None and k.parent.kind == "pane" and self.desk

    def _shown(self, k, row):
        if not (uitree_ref._shown(k, row) and (k.act is None or k.act.can(row)
                                               or getattr(k.act, "off", "hide") == "grey")):
            return False
        if k.kind == "pane" and "offered" in k.l:
            return bool(self._offered(k))       # a pane left with no tab goes
        if k.kind == "panes":
            return any(self._shown(c, row) for c in k.kids)    # ...and a split left empty
        return True

    def _o(self, n, row, **o):
        o = uitree_ref.Engine._o(self, n, row, **o)
        if n.act is not None and not n.act.can(row):
            o["disabled"] = True
        if "marked" in n.p:
            o["current"] = bool(n.p["marked"](row))
        return o

    def _stack(self, n, row, rect):
        """The shown children down `rect`: fixed rows take their height, the
        fills share the rest; popovers and dialogs draw last, over it."""
        kids = [k for k in n.kids if self._shown(k, row)]
        flow = [k for k in kids if k.kind not in ("popover", "dialog")]
        hs = [self._height(k) for k in flow]
        fixed = sum(h for h in hs if h)
        fills = sum(1 for h in hs if h is None) or 1
        x, y, w, h = rect
        room = max(0, h - fixed)
        spare = 0
        for i, (k, kh) in enumerate(zip(flow, hs)):
            if kh == 0:
                continue
            kh = (room // fills if kh is None else kh) + spare
            spare = 0
            if k.kind == "text_editor" and any(flow[i + 1:]):
                # a text editor takes whole lines; the node under it the rest
                spare = kh % self._pitch()
                kh -= spare
            r = (x, y, w, kh)
            self._put_rect(k.key, r)
            self._draw(k, row, r)
            y += kh
        for k in kids:
            if k.kind in ("popover", "dialog") and not self.placing:
                self.late.append((k, row, rect))     # over every pane, after the page

    def _draw(self, k, row, rect):
        if self.placing and k.kind not in _PLACED:
            return
        getattr(self, uitree_ref._rname(k.kind))(k, row, rect)

    def _r_case(self, n, row, rect):
        self._stack(n, row, rect)

    def _r_switch(self, n, row, rect):
        v = self.s._v[n.l["name"]]
        case = next((c for c in n.kids if c.l["v"] == v), None) or next(
            (c for c in n.kids if c.l["v"] == "_"), None)
        if case is not None:
            self._stack(case, row, rect)

    def _r_panel(self, n, row, rect):
        if n.key in ("hand", "desk"):
            self._stack(n, row, rect)
            return
        if self.placing:
            return
        x, y, w, h = rect
        g = 6 if self.desk else 4
        self.cv.rect(x + g, y + g, w - 2 * g, h - 2 * g, self.th["panel"])
        ty = y + h // 2 - 4
        for k in n.kids:
            if k.kind == "label" and self._shown(k, row):
                # a panel's words: its labels down its middle
                r = (x + g, ty, w - 2 * g, 16)
                self._put_rect(k.key, r)
                self._r_label(k, row, r)
                ty += 18

    # -- a row ----------------------------------------------------------------

    def _natural(self, k, row, h):
        if k.kind == "button":
            if k.l.get("grow"):
                return None                         # a growing button: a fill
            if k.src.get("w") is not None:
                return self._len(k, "w", row, 0)    # a bar item: its own width
            if k.l.get("kind") == "bar":
                # a bar item: its icon's ink and its label (moy_ui_bar_item_w)
                icon = self._t(k, "icon", row) if "icon" in k.p else (k.act.icon if k.act else "") or ""
                label = self._t(k, "label", row) if "label" in k.p else ""
                if k.l.get("alone") and ("icon" in k.p or (k.act and k.act.icon)):
                    label = ""
                return _v._C.bar_item_w(icon, label, h, max(1, getattr(self, "fs", 1)))
            if "icon" in k.p and k.l.get("alone"):
                return h
            return _v.text_size(self._t(k, "label", row))[0] + 28
        if k.kind == "status" and STATUS_SEP not in self._t(k, "text", row):
            # a one-field status beside a control ("2 of 5" by a find
            # field) takes its words' width; a line of fields grows
            pad = _ud.metrics("status")[_ud.ST_PAD] * self.fs
            was = _ud.print_font(_ud.font("ui")[0])
            try:
                return _ud.print_width(self._t(k, "text", row), self.fs) + 2 * pad + 2
            finally:
                _ud.print_font(was)
        if k.kind == "canvas" and k.src.get("w") and not k.l.get("grow"):
            return self._len(k, "w", row, 0)        # a fixed cell along x, as h= down
        if k.kind == "row":
            # a row in a row: its cells' widths and gaps where none grows;
            # one with nothing shown a spacer; a grow= row a fill
            if k.l.get("grow"):
                return None
            rws = []
            kids = self._row_kids(k, row, rws)
            nat = [self._natural(c, r, h) for c, r in zip(kids, rws)]
            if not kids or any(v is None for v in nat):
                return None
            return sum(nat) + self._len(k, "gap", row, self._row_gap()) * max(0, len(kids) - 1) + \
                2 * self._len(k, "pad", row, 0)
        if k.kind == "tabs":
            st = self.s._t[k.key]
            tab = self.decl.labels[k.l["labels"]]
            return sum(_v.text_size(tab[v])[0] + (28 if self.desk else 14 * self.fs)
                       for v in st.values)
        return None

    def _unit(self):
        """A placed row's unit: the host's (`unit`), else 1 on a desk and
        the font scale on a handheld."""
        return self.unit or (1 if self.desk else self.fs)

    def _row_gap(self):
        return 4 if self.desk else 2

    def _len(self, n, k, row, dflt):
        """Length prop `k` of `n` in pixels: a literal in the page's unit,
        an int state as the host measured it; `dflt` (pixels) where unset."""
        v = n.src.get(k)
        if v is None:
            return dflt
        if type(v) is int:
            return v * self._unit()
        return n.p[k](row)

    def _row_kids(self, n, row, rws):
        """A row's shown cells in order, the list row each draws on in
        `rws`: a lent site the shown lender's buttons, in its place; a
        `rows` a cell per row of its model, its kids shown on that row (a
        row from a model)."""
        kids = []
        for k in n.kids:
            if k.kind == "lent":
                t = self.lenders.get(k.key)
                for b in t.kids if t is not None else ():
                    if self._shown(b, row):
                        kids.append(b)
                        rws.append(row)
            elif k.kind == "rows":
                if not self._shown(k, row):
                    continue
                for r in self.s._v[k.l["model"]]:
                    for b in k.kids:
                        if self._shown(b, r):
                            kids.append(b)
                            rws.append(r)
            elif self._shown(k, row):
                kids.append(k)
                rws.append(row)
        return kids

    def _r_row(self, n, row, rect):
        x, y, w, h = rect
        if n.key:
            self.anchors[n.key] = rect
        rws = []
        kids = self._row_kids(n, row, rws)
        if any(k.kind == "status" for k in n.kids) and not any(k.kind == "status" for k in kids) and \
                not self.placing:
            _ud.status_row(self.cv, self.th, rect, ())     # a status row's band, its line hidden
        nat = [self._natural(k, r, h) for k, r in zip(kids, rws)]
        if not any(v is None for v in nat):
            for i in [i for i, k in enumerate(kids) if k.kind == "tabs"][:1]:
                nat[i] = None
        # a fill's basis: a growing canvas's w=, which its share is over
        base = [self._len(k, "w", row, 0) if v is None and k.kind == "canvas" else 0
                for k, v in zip(kids, nat)]
        gap = self._len(n, "gap", row, self._row_gap())
        pad = self._len(n, "pad", row, 0)
        x0, x1 = x + pad, x + w - pad
        mid = next((i for i, k in enumerate(kids) if k.kind == "row" and k.l.get("mid")), None)
        if mid is None and not any(v is None for v in nat):
            self._fit_cells(kids, rws, nat, row, x1 - x0, gap, h)
        ws = [0] * len(kids)
        if mid is None:
            self._share(nat, base, ws, 0, len(kids), x1 - x0 - gap * max(0, len(kids) - 1))
        else:
            # the mid centred on the row: each side's fills share that side's room
            a = x0 + sum(v or b for v, b in zip(nat[:mid], base)) + gap * mid
            b = x1 - sum(v or b for v, b in zip(nat[mid + 1:], base[mid + 1:])) - gap * (len(kids) - 1 - mid)
            c = x + w // 2
            if nat[mid] is None:        # a mid of fills: the widest span centred that clears both
                half = max(0, min(c - a, b - c))
                ms, ws[mid] = c - half, 2 * half
            else:
                ms, ws[mid] = x + (w - nat[mid]) // 2, nat[mid]
            self._share(nat, base, ws, 0, mid, ms - x0 - gap * mid)
            self._share(nat, base, ws, mid + 1, len(kids), x1 - (ms + ws[mid]) - gap * (len(kids) - 1 - mid))
        for i, k in enumerate(kids):
            if i == 0:
                x = x0
            if mid is not None and i == mid:
                x = ms
            elif mid is not None and i == mid + 1:
                x = x1 - sum(ws[mid + 1:]) - gap * (len(kids) - 2 - mid)
            kw = ws[i]
            r = (x, y, kw, h) if k.kind in _WHOLE or (k.kind == "button" and k.l.get("kind") == "bar") \
                else (x, y + 2, kw, h - 4)      # a band, a region, a bar item: the row's height
            self._put_rect(k.key, r)
            kr = rws[i]
            if kr is not row:           # a model row's cell: ROWKEY/KEY
                self._put_rect(kr._f[kr._m.key] + "/" + k.key, r)
            if k.kind == "row" or self.placing:
                self._draw(k, kr, r)        # a placement records the cells, drawing none
            elif k.kind == "button":
                self._r_button(k, kr, rect, at=r)
            else:
                _v.at(*r)
                self._draw(k, kr, r)
            x += kw + gap

    def _r_tools(self, n, row, rect):
        if n.l.get("lend"):
            return                      # drawn at the frame's lent site
        if self.desk:
            self._r_row(n, row, rect)
            return
        # a handheld's tool row: a cell each across the width, a word's
        # its width (two cells at most), the icons sharing the rest
        x, y, w, h = rect
        kids = [k for k in n.kids if self._shown(k, row)]
        n_k = max(1, len(kids))
        words = {i: min(self._natural(k, row, h), 2 * w // n_k)
                 for i, k in enumerate(kids) if not k.l.get("alone")}
        icons = n_k - len(words)
        rest = w - sum(words.values())
        j = 0
        for i, k in enumerate(kids):
            if i in words:
                kw = words[i]
            else:
                kw = rest * (j + 1) // max(1, icons) - rest * j // max(1, icons)
                j += 1
            r = (x, y + 1, kw - 1, h - 2)
            self._put_rect(k.key, r)
            self._r_button(k, row, rect, at=r)
            x += kw

    def _r_canvas(self, n, row, rect):
        _v.at(*rect)
        x, y, w, h, _st = _v.part(n.key, 0, 0, {"id": n.key, "bare": True} if self.bare else {"id": n.key})
        self._id(n.key, self._slot(n, None).pair)    # (a bare page's canvases sink no well)
        self.canvases[n.key] = (n, (x, y, w, h))
        if not _clip_in(self.cv, x, y, w, h, self.clip_to):
            return
        if "ground" in n.l:
            self.cv.rect(x, y, w, h, self.th[n.l["ground"]])
        n.l["draw"](self.cv, x, y, w, h)
        _clip_back(self.cv, self.clip_to)

    def point(self, px, py, click):
        """A canvas with pointer= is handed down, drag and up in its own
        coordinates; the press that starts on it holds it until release."""
        down = self.pointer_down()
        held = getattr(self, "held", None)
        if held is not None:
            n, r = held
            ev = S.Ev("drag" if down else "up", px - r[0], py - r[1], "touch")
            self.q.append((n.l["pointer"], (ev,), "pointer %s on canvas %r" % (ev.kind, n.key)))
            if not down:
                self.held = None
            self._damage.all()
            return True
        held = self.held_legacy
        if held is not None:
            n, r = held
            _NOW.app = self
            try:
                n.l["pointer"](px - r[0], py - r[1], click)
            finally:
                _NOW.app = None
            if not down:
                self.held_legacy = None
            uitree_ref.Engine.point(self, px, py, click)
            self._damage.all()
            return True
        if click:
            for n, r in self.legacies.values():
                if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    # the press focuses the stop (the toolkit's) and the
                    # hosted surface takes it, in its own coordinates
                    uitree_ref.Engine.point(self, px, py, click)
                    if "pointer" in n.l:
                        _NOW.app = self
                        try:
                            n.l["pointer"](px - r[0], py - r[1], click)
                        finally:
                            _NOW.app = None
                        if down:
                            self.held_legacy = (n, r)
                    self._damage.all()
                    return True
            for p, r in self.pane_order:
                if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3] and p.key != self.pane_cur:
                    self.pane_cur = p.key       # a press in a pane gives it the keys
                    self._damage.all()
                    break
        if click:
            for n, r in getattr(self, "canvases", {}).values():
                if "pointer" in n.l and r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    ev = S.Ev("down", px - r[0], py - r[1], "touch")
                    self.q.append((n.l["pointer"], (ev,), "pointer down on canvas %r" % n.key))
                    if down:
                        self.held = (n, r)
                    else:
                        self.q.append((n.l["pointer"], (S.Ev("up", ev.x, ev.y, "touch"),),
                                       "pointer up on canvas %r" % n.key))
                    self._damage.all()
                    return True
        return uitree_ref.Engine.point(self, px, py, click)

    def _r_status(self, n, row, rect):
        """A status line: its first field always, the others while they fit,
        joined by the `ui` face's separator, on the status band's look."""
        fields = self._t(n, "text", row).split(STATUS_SEP)
        line = fields[0]
        if len(fields) > 1:
            fid = _ud.font("ui")[0]
            info = _ud.font_info(fid) if fid else None
            sep = "  \u00b7  " if info is not None and info[4] == 0 else " - "
            room = rect[2] - 2 * _ud.metrics("status")[_ud.ST_PAD] * self.fs
            was = _ud.print_font(fid)
            try:
                for f in fields[1:]:
                    more = line + sep + f
                    if _ud.print_width(more, self.fs) > room:
                        break
                    line = more
            finally:
                _ud.print_font(was)
        # its words on the page's text column (the text view's inset), the
        # band's ground before them
        x, y, w, h = rect
        tx = x + _TEXT_PAD * self.fs - _ud.metrics("status")[_ud.ST_PAD] * self.fs
        if tx > x:
            _ud.status_row(self.cv, self.th, (x, y, tx - x, h), ())
            rect = (tx, y, w - (tx - x), h)
        _ud.status_row(self.cv, self.th, rect, (line,))

    def _r_label(self, n, row, rect):
        _v.at(*rect)
        uitree_ref.Engine._r_label(self, n, row, rect)

    def _r_button(self, n, row, rect, at=None):
        uitree_ref.Engine._r_button(self, n, row, rect, at=at or (rect[0], rect[1], rect[2],
                                                                min(rect[3], self._h_row())))

    def _r_tabs(self, n, row, rect, key=None, labels=None):
        key = key or n.key
        st = self.s._t[key]
        tab = self.decl.labels[labels or n.l["labels"]]
        vals = st.values
        cur = vals.index(self.s._v[key])
        _v.at(*rect)
        got = _v.tabs(key, [tab[v] for v in vals], cur)
        self._id(key, self._slot(n, None).pair)
        x = rect[0]
        for v in vals:
            tw = _v.text_size(tab[v])[0] + (28 if self.desk else 14 * self.fs)
            self.anchors["%s.%s" % (key, v)] = (x, rect[1], tw, rect[3])
            x += tw
        if got is not None and got >= 0 and got != cur:
            old = self.s._v[key]
            self.s._v[key] = vals[got]
            self.s._ver[key] += 1
            S._trace("state", key, "%r -> %r   (from tabs %r)" % (old, vals[got], key))
            if "on" in n.l:
                self.fire(n.l["on"], (), n, None)

    def _r_field(self, n, row, rect):
        k = n.key
        before = self.s._v[k]
        o = self._o(n, row)
        if "hint" in n.p:
            o["hint"] = self._t(n, "hint", row)
        if rect is not self.rect:
            _v.at(*rect)
        text, done = _v.field(self._t(n, "label", row), before, n.l.get("max", 32), o)
        if text != before:
            self.s._v[k] = text
            self.s._ver[k] += 1
            S._trace("state", k, "%r -> %r   (typed in field %r)" % (before, text, k))
            if "changed" in n.l:
                self.fire(n.l["changed"], (), n, None)
        if done and "on" in n.l:
            back = self._shift_enter() if getattr(self, "shift", False) else None
            if back is not None and back.can():
                self.fire(back, self.args_for(back), n, None)
            else:
                self.fire(n.l["on"], (), n, None)

    def _shift_enter(self):
        """The action Shift+Enter answers in a field, or None."""
        return next((a for a in self.actions.values() if S.SHIFT_ENTER in a.keys), None)

    # -- panes and their dividers --------------------------------------------

    def _offered(self, pane):
        """A tabbed pane's tabs on offer, in their enum's order (offered=
        conditions each), else None."""
        tabs = pane.l.get("tabs")
        if tabs is None:
            return None
        off = pane.l.get("offered")
        vals = self.s._t[tabs].values
        if not off:
            return vals
        return tuple(v for v in vals if v not in off or off[v](None))

    def _min_of(self, k, axis):
        """A child's least size along `axis`, in pixels, its gutters in."""
        if k.kind == "pane":
            m = k.l.get("min", FLOOR)
            if isinstance(m, dict):
                # by tab: the largest of the offered tabs'
                d = m.get("_", FLOOR)
                most = 0
                for v in self._offered(k):
                    most = max(most, m.get(v, d)[axis])
                return most * self.du
            return m[axis] * self.du
        if k.kind != "panes":
            return 0
        kids = [c for c in k.kids if self._shown(c, None)]
        m = [self._min_of(c, axis) for c in kids]
        along = self._axis(k) == axis
        return sum(m) + self._gap(k) * (len(m) - 1) if along else max(m or [0])

    def _axis(self, n):
        return 0 if (n.l["dir"] if "dir" in n.l else n.p["dir"](None)) == "row" else 1

    def _gap(self, n):
        return n.l.get("gap", GUTTER) * self.du

    def _r_panes(self, n, row, rect):
        """The desk Editor's rules (runtime/editors/editor_desk.py): sizes
        in desk units; held sizes yield to the area, last first, down to
        their minimums, so the growing child keeps its own; under the
        workspace's least size, or grown, one pane stands over the area
        alone. A split left one child shown is that child; where the child
        that grows is not shown, the last one shown takes the rest. The
        dividers are `ui.split`'s: a drag's let-go writes the held sizes
        beside it, a double tap puts them back."""
        outer = n.parent is None or n.parent.kind not in ("pane", "panes")
        if outer:
            self.panes_root = n
            self._path = ()
            if self._arranging and self.placing:
                self.arranged = ([], [])
                self.least = (self._min_of(n, 0), self._min_of(n, 1))
            alone = self._alone(n, rect)
            if self._arranging and self.placing:
                self.alone_pane = alone
            if alone is not None:
                if self.placing:
                    # the arrangement under the pane alone, as a host's
                    # placement answers it; nothing of it placed
                    self._ghost = True
                    try:
                        self._lay_panes(n, row, rect)
                    finally:
                        self._ghost = False
                self._arranging = False
                try:
                    self._draw(alone, row, rect)
                finally:
                    self._arranging = True
                return
        self._lay_panes(n, row, rect)

    def _lay_panes(self, n, row, rect):
        kids = [k for k in n.kids if self._shown(k, row)]
        if len(kids) == 1:
            self._draw(kids[0], row, rect)
            return
        axis = self._axis(n)
        gap = self._gap(n)
        span = rect[2 + axis] - gap * (len(kids) - 1)
        held = [self._size_of(k) for k in kids]
        if held.count(None) > 1:
            raise S.UIError("N07", "ui.panes(%r) shows %d children that grow" % (n.key, held.count(None)),
                            where=n.where, found=", ".join(k.key for k, v in zip(kids, held) if v is None),
                            fix="their shown= conditions never hold together")
        mins = [self._min_of(k, axis) for k in kids]
        sizes = fit(held, mins, span)
        rects, divs = [], []
        x, y, w, h = rect
        for j in range(len(kids)):
            sz = sizes[j] if j < len(kids) - 1 else (w if axis == 0 else h)
            r = (x, y, sz, h) if axis == 0 else (x, y, w, sz)
            rects.append(r)
            if j < len(kids) - 1:
                if axis == 0:
                    divs.append((x + sz, y, gap, h))
                    x += sz + gap
                    w -= sz + gap
                else:
                    divs.append((x, y + sz, w, gap))
                    y += sz + gap
                    h -= sz + gap
        sid = "split:" + "/".join(k.key for k in kids)
        base = self._path
        if not self._ghost:
            self.splits[sid] = (n, kids, sizes, mins, held, rect, axis, gap, base)
        if not self.placing:
            self._split(n, sid, kids, sizes, mins, held, rect, axis, gap)
            self.dividers[sid] = divs
        record = self._arranging and self.placing
        for j, (k, r) in enumerate(zip(kids, rects)):
            self._path = base + (j,)
            self._draw(k, row, r)
            if record and j < len(divs):
                self.arranged[1].append((base, j, "h" if axis == 0 else "v", divs[j]))
        self._path = base

    def _split(self, n, sid, kids, sizes, mins, held, rect, axis, gap):
        """The dividers of panes `n` on `ui.split`: drawn, a press on one
        held, its drag's let-go written, a double tap reset; held at a
        minimum, the flash."""
        u = self.du
        mouse = self._mouse()
        o = self._split_o
        o["gap"] = gap
        o["mins"] = mins
        o["line"] = 2 * u
        o["slop"] = 0 if mouse else DRAG_SLOP * u
        o["handle"] = (HANDLE[0] * u, HANDLE[1] * u)
        o["hit"] = None if mouse else (HANDLE_HIT[0] * u, HANDLE_HIT[1] * u)
        o["focus"] = self._keys_child(kids)
        _v.at(*rect)
        out = self.split_out
        got = list(sizes)
        _v.split(sid, "x" if axis == 0 else "y", got, o, out)
        nk = len(kids)
        i, at, stop, bits = out[4 * nk], out[4 * nk + 1], out[4 * nk + 2], out[4 * nk + 3]
        if i < 0:
            return
        if bits & _v.LET_GO:
            if bits & _v.LIVE:
                self._hold(kids, got, (i, i + 1), "divider %d of %r let go" % (i, sid))
            else:
                last, at = self._last_div
                self._last_div = ((sid, i), self.clock)
                if last == (sid, i) and self.clock - at < DOUBLE_MS:
                    self._last_div = (None, 0)
                    self._unhold(kids[i:i + 2], "divider %d of %r tapped twice" % (i, sid))
                else:
                    return
            self._damage.all()
            return
        self.div_held = (sid, i, at, stop, bool(bits & _v.LIVE))
        if bits & _v.LIVE and stop >= 0:
            pane = self._first_pane(kids[i + stop])
            m = self._min_of(pane, axis)
            self.flash = (pane, "%s stops at its minimum, %d %s" % (
                self._pane_name(pane), m, "wide" if axis == 0 else "tall"))

    def _first_pane(self, n):
        while n.kind == "panes":
            n = next(k for k in n.kids if self._shown(k, None))
        return n

    def _pane_name(self, pane):
        """A pane's name: its title, else its first tab's."""
        if "title" in pane.p:
            return self._t(pane, "title", None)
        tabs = pane.l.get("tabs")
        if tabs:
            return self.decl.labels[pane.l["labels"]][self._offered(pane)[0]]
        return pane.key

    def _alone(self, n, rect):
        """The pane shown over the whole panes area, or None: the grown
        one, else the focused one where the area is under the workspace's
        least size (one pane at a time; the keys reach the rest)."""
        g = n.l.get("grown")
        if g is not None and self.s._v[g] is not None:
            p = self._pane_named(n, self.s._v[g])
            if p is not None:
                return p
        if rect[2] < self._min_of(n, 0) or rect[3] < self._min_of(n, 1):
            return self._pane_named(n, self.pane_cur) or self._panes_in(n)[0]
        return None

    def _pane_of(self, node):
        while node is not None and node.kind != "pane":
            node = node.parent
        return node

    def _keys_child(self, kids):
        """The child of a split that is the pane the keys reach, -1 where
        none is (the split's focus: its split frame and the pad's moves
        start there)."""
        p = self._pane_named(self.panes_root, self.pane_cur) if self.panes_root is not None else None
        return kids.index(p) if p is not None and p in kids else -1

    def _r_pane(self, n, row, rect):
        if self._arranging and self.placing:
            self.arranged[0].append((n, rect, self._path))
        if self._ghost:
            return
        x, y, w, h = rect
        u = self.du
        hh = 0 if n.l.get("head") is False else HEADER * u
        self.pane_order.append((n, rect))
        body = [k for k in n.kids if k.kind != "tools"]
        tmp = S.Node("case", n.key + ":body", n.where)
        tmp.kids, tmp.parent = body, n
        if not self.placing and hh:
            self._pane_head(n, row, rect, hh)
        self._stack(tmp, row, (x, y + hh, w, max(0, h - hh)))

    def _pane_head(self, n, row, rect, hh):
        """The header row: its title (and icon) or its tabs, its tools at
        the right. (The card look and the desk's header rules are not yet
        the engine's.)"""
        x, y, w, _h = rect
        cv, th = self.cv, self.th
        # the header only: the body's ground is the page's, which the text
        # view's kept rows sit on (a fill under them each frame wipes them)
        cv.rect(x, y, w, hh - 1, th["panel"])
        cv.rect(x, y + hh - 1, w, 1, th["border"])
        self.anchors[n.key + ".head"] = (x, y, w, hh)
        tools = [k for k in n.kids if k.kind == "tools" and self._shown(k, row)]
        tw = 0
        if tools:
            t = tools[0]
            kids = [k for k in t.kids if self._shown(k, row)]
            tw = sum(self._natural(k, row, hh - 4) or 0 for k in kids) + 4 * len(kids)
            self._r_row(t, row, (x + w - tw - 4, y + 2, tw, hh - 4))
        if n.l.get("tabs"):
            self._r_tabs(n, row, (x + 6, y + 2, w - tw - 16, hh - 4), n.l["tabs"], n.l["labels"])
        else:
            _v.at(x + 10, y + 2, w - tw - 20, hh - 4)
            if "icon" in n.p:
                _v.icon(n.p["icon"](row))
            _v.label(self._t(n, "title", row))

    def _draw_flash(self):
        """A divider held at a pane's minimum: the pane ringed, its words
        along its foot (the desk's)."""
        pane, why = self.flash
        u = self.du
        for p, r in self.pane_order:
            if p is pane:
                self.cv.rrect(r[0] - 2 * u, r[1] - 2 * u, r[2] + 4 * u, r[3] + 4 * u,
                              (RADIUS + 2) * u, None, self.th["value"])
                _v.at(r[0] + 8 * u, r[1] + r[3] - 28 * u, r[2] - 16 * u, 24 * u)
                _v.label(why, {"tone": "value"})

    # -- pane focus -------------------------------------------------------------

    def _pane_follow(self):
        """After the pass: the pane the keys reach follows focus where it
        moved into another pane (a press, a step), and the panes'
        focused= state says which it is."""
        root = self.panes_root
        if root is None:
            return
        f = self.focused[0] if self.focused is not None else None
        if f is not self._focus_seen:
            self._focus_seen = f
            p = self._pane_of(f)
            if p is not None:
                self.pane_cur = p.key
        if self._pane_named(root, self.pane_cur) is None:
            order = self._panes_in(root)
            self.pane_cur = order[0].key if order else None
        nm = root.l.get("focused")
        if nm is not None and self.s._v[nm] != self.pane_cur:
            old = self.s._v[nm]
            self.s._v[nm] = self.pane_cur
            self.s._ver[nm] += 1
            S._trace("state", nm, "%r -> %r   (pane focus)" % (old, self.pane_cur))

    def focus_pane(self, key):
        """The keys to pane `key`: its first stop takes focus."""
        root = self.panes_root
        p = self._pane_named(root, key) if root is not None else None
        if p is None:
            return False
        self.pane_cur = key
        first = next(iter(self._stops(p)), None)
        if first is not None:
            self.want_focus = first[0].key
        self._damage.all()
        return True

    def _pane_keys(self, i):
        """The workspace's keys (the desk Editor's): F6 and Shift+F6 step
        the pane the keys reach, Ctrl+Shift+Enter grows it or puts it back,
        Ctrl+0 puts every divider back, Ctrl+Alt+arrows move its right or
        bottom divider. True where one acted."""
        root = self.panes_root
        ev = getattr(i, "key_event", 0) or 0
        fresh = ev != 0 and ev != self._ev
        self._ev = ev
        if root is None or not fresh:
            return False
        code, mods = ev & 0xFFFF, ev >> _MODS_AT
        ctrl = mods & _CTRL
        if code == _F6 and not ctrl:
            order = [p.key for p in self._panes_in(root)]
            if not order:
                return False
            j = order.index(self.pane_cur) if self.pane_cur in order else 0
            return self.focus_pane(order[(j + (-1 if mods & _KSHIFT else 1)) % len(order)])
        if ctrl and mods & _KSHIFT and code in _ENTERS and root.l.get("grown") is not None:
            nm = root.l["grown"]
            old = self.s._v[nm]
            new = None if old == self.pane_cur else self.pane_cur
            self.s._v[nm] = new
            self.s._ver[nm] += 1
            S._trace("state", nm, "%r -> %r   (Ctrl+Shift+Enter)" % (old, new))
            self._damage.all()
            return True
        if ctrl and not mods & (_ALT | _KSHIFT) and code == ord("0"):
            self.reset_sizes()
            return True
        if ctrl and mods & _ALT and code in (_LEFT, _RIGHT, _UP, _DOWN):
            return self.nudge(code)
        return False

    def _r_slot(self, n, row, rect):
        """A rect another process's window or page is docked in: recorded
        (`rect_of`, `anchor`), drawn by its owner, nothing drawn here."""
        self.anchors[n.key] = rect

    # -- the text editor --------------------------------------------------------

    def _pitch(self):
        return self._mono_metrics()[3]

    # -- popovers hang from a node's part -------------------------------------

    def _r_popover(self, n, row, rect):
        w = n.p["w"](row) if "w" in n.p else 200
        name = n.p["anchor"](row) if "anchor" in n.p else None
        a = self._part_anchor(name) if name else None
        if a is not None and "." not in name:
            # hung from a whole node, a popover stands over that node's top:
            # one that opens on its field (Go to) centred, a list (the
            # scripts) at its start, under the bar's tab that opened it
            w = min(w, a[2] - 16 * self.fs)
            if n.kids and n.kids[0].kind == "field":
                a = (a[0] + (a[2] - w) // 2, a[1] + 4 * self.fs, w, 0)
            else:
                a = (a[0], a[1], w, 0)
        if a is not None:
            _v.at(*a)
        if not _v.popover(n.key, w) and "close" in n.l:
            self.fire(n.l["close"], self.args_for(n.l["close"]), n, None)
        for k in n.kids:
            if self._shown(k, row):
                if k.kind == "field":
                    self._r_field(k, row, self.rect)
                elif k.kind == "label":
                    uitree_ref.Engine._r_label(self, k, row, rect)   # down the card, as it flows
                else:
                    self._draw(k, row, rect)
        if self._a_note(n) and "close" in n.l and n.l["close"].hint_now():
            _v.hints(("cancel", n.l["close"].hint_now()))     # a note's card names its own close
        _v.popover()

    def _menu_rows(self, n):
        """A popover's list of one link a row is a menu: a choice row each,
        its value at the row's right (no link's mark)."""
        return (n.parent is not None and n.parent.kind == "popover" and len(n.kids) == 1
                and n.kids[0].kind == "link")

    def _menu_text(self, link, r):
        t = self._t(link, "label", r)
        v = self._t(link, "value", r) if "value" in link.p else ""
        return (t + "\t" + v if v else t)[:uitree_app.ID_MAX]

    def _row_stop_id(self, lst, rowkey):
        if not self._menu_rows(lst):
            return uitree_ref.Engine._row_stop_id(self, lst, rowkey)
        r = next((r for r in self.s._v[lst.l["model"]] if r._f[r._m.key] == rowkey), None)
        return self._menu_text(lst.kids[0], r) if r is not None else None

    def _r_rows(self, n, row, rect):
        if not self._menu_rows(n):
            uitree_ref.Engine._r_rows(self, n, row, rect)
            return
        link = n.kids[0]
        model = list(self.s._v[n.l["model"]])
        if not model:
            empty = self._t(n, "empty", None)
            if empty:
                _v.label(empty, {"tone": "dim"})
            return
        texts = []
        for r in model:
            t = self._menu_text(link, r)
            texts.append(t)
            self._id(t, (link, r))
        got = _v.menu("", texts)
        if got >= 0:
            self._activate(link, model[got])

    def _r_link(self, n, row, rect):
        uitree_ref.Engine._r_link(self, n, row, rect)

    def _hints(self):
        """A text surface keeps its keys: no band while it (or nothing) holds
        focus. A popover or a closer row (a find strip) names what Enter
        does there and what Back closes."""
        if self.dialog_shown():
            return uitree_ref.Engine._hints(self)
        pops = self.shown_popovers()
        if pops and self._a_note(pops[0]):
            return ()                   # the card names its close itself
        closer = pops[0] if pops else (self._closer(self.root) if self.root is not None else None)
        f = self.focused
        if closer is None and f is not None and f[0].kind == "legacy":
            # the hosted surface's own hints, passed through (none: it keeps its keys)
            return tuple(f[0].l["hints"]()) if "hints" in f[0].l else ()
        if closer is None:
            if f is None or f[0].kind == "text_editor":
                return tuple(getattr(self, "text_keys", ()))   # the host's keys, if it names any
            return uitree_ref.Engine._hints(self)
        out = []
        if self._shown_list() is not None:
            out += ["scroll", "Pick"]           # the arrows walk the popover's list
        if f is not None:
            node, row = f
            act = node.l.get("on") if node.kind == "field" else node.act
            if act is not None and act.can(row) and act.hint_now():
                out += ["confirm", act.hint_now()]
            back = self._shift_enter() if node.kind == "field" else None
            if back is not None and back.can() and back.hint_now():
                out += ["Shift+enter", back.hint_now()]
        close = closer.l.get("close")
        if close is not None and close.hint_now():
            out += ["back", close.hint_now()]
        return tuple(out)

    # -- keys ------------------------------------------------------------------

    def _shown_list(self):
        """The list (rows with cur=) of the popover shown, or None."""
        for p in self.shown_popovers():
            for k in p.kids:
                if k.kind == "rows" and k.src.get("cur"):
                    return k
        return None

    def step_rows(self, d):
        """Up or down `d` rows in the shown popover's list (the arrows and a
        trackball, which the console routes past the page): its cur= moves
        and focus follows. False where no popover list is shown."""
        was, _NOW.app = _NOW.app, self
        try:
            lst = self._shown_list()
            if lst is None:
                return False
            rows = list(self.s._v[lst.l["model"]] if "model" in lst.l else ())
            if not rows:
                return True
            ck = lst.src["cur"]
            keyf = rows[0]._m.key
            keys = [r._f[keyf] for r in rows]
            cur = self.s._v[ck]
            i = keys.index(cur) if cur in keys else -1
            j = max(0, min(len(keys) - 1, i + d))
            if keys[j] != cur:
                self.s._v[ck] = keys[j]
                self.s._ver[ck] += 1
                S._trace("state", ck, "%r -> %r   (step %+d)" % (cur, keys[j], d))
            self.want_focus = self._row_stop_id(lst, keys[j])
            self._damage.all()
            return True
        finally:
            _NOW.app = was

    def shown_popovers(self):
        """The popovers in the shown tree: a switch's current case only."""
        out = []
        if self.root is not None:
            self._pops(self.root, out)
        return out

    def _pops(self, n, out):
        """The shown popovers under `n` into `out`, or, `out` None, the
        first of them (a frame's question, which allocates nothing)."""
        for k in n.kids:
            if not uitree_ref._shown(k, None):
                continue
            got = None
            if k.kind == "popover":
                if out is None:
                    return k
                out.append(k)
            elif k.kind == "switch":
                v = self.s._v[k.l["name"]]
                for c in k.kids:
                    if c.l["v"] == v or c.l["v"] == "_":
                        got = self._pops(c, out)
                        break
            elif k.kind != "rows" and k.kind != "cards":
                got = self._pops(k, out)
            if got is not None:
                return got
        return None

    def popover_key(self):
        """The key of the popover shown (the first, in the shown tree), or None."""
        p = self._pops(self.root, None) if self.root is not None else None
        return p.key if p is not None else None

    def popover_shown(self):
        return self.root is not None and self._pops(self.root, None) is not None

    def editor_keys(self):
        """The text editor that takes typed keys: the shown one, unless a
        popover is open or focus is on a field or a row of its own."""
        if self.root is None or self.popover_shown():
            return None
        f = self.focused
        if f is not None and f[0].kind in ("field", "link") :
            return None
        for k in self.views:
            if self.views[k] is not None:
                return k
        return None

    def keys(self, i):
        was, _NOW.app = _NOW.app, self      # a size class (@desk) reads the app's
        try:
            return self._keys(i)
        finally:
            _NOW.app = was

    def _keys(self, i):
        if self._pane_keys(i):
            return True
        ch = getattr(i, "typed", 0) & 0xFF
        self.shift = bool(getattr(i, "mods", 0) & _SHIFT)
        live = ()
        if ch:
            answer = [a for a in self.actions.values() if any(S._chord_byte(k) == ch for k in a.keys)]
            live = [a for a in answer if a.live()]
        if len(live) > 1:               # the checker proves this cannot happen; the engine says so if it does
            raise S.UIError("U08", "%s answer one key while all are shown" % " and ".join(a.name for a in live),
                            fix="never show two units that answer one key together")
        if live:
            a = live[0]
            k = [k for k in a.keys if S._chord_byte(k) == ch][0]
            if a.can():
                self.q.append((a, self.args_for(a), "key %r (unit %s)" % (k, a.unit)))
            else:
                was, _NOW.app = _NOW.app, self
                S._trace("off", a.name, "key %r: %s" % (k, a.when_src))
                _NOW.app = was
            self._damage.all()
            return True
        text = ch and (ch in (8, 9, 13, 127) or 32 <= ch < 127)
        f = self.focused
        if f is not None and f[0].kind == "legacy" and not self.popover_shown():
            # the hosted surface takes the keys it answers; the rest move
            # focus as from any stop
            if "keys" in f[0].l and f[0].l["keys"](i):
                self._damage.all()
                return True
        if ch == 13 and f is not None and f[0].kind == "link" and self.popover_shown():
            self._activate(f[0], f[1])      # Enter takes a popover's row, as A does
            self._damage.all()
            return True
        if text and ch != 13 and self.views:
            # a popover with no field of its own (completion, the scripts) is
            # left by a typed key, which then edits as though it was never open
            for p in self.shown_popovers():
                if "close" in p.l and not any(k.kind == "field" for k in p.kids):
                    self.run_action(p.l["close"], self.args_for(p.l["close"]),
                                    "typed %r on popover %r" % (chr(ch), p.key))
                    if not self.popover_shown():
                        self.focused = None
                        return self._into_doc(next(iter(self.views)), ch,
                                              bool(getattr(i, "mods", 0) & _SHIFT))
                    break
        ek = self.editor_keys()
        if ek is not None:
            doc = self.s._v[ek]
            moved = False
            if not self.host_arrows:
                for name, dy, dx in (("up", -1, 0), ("down", 1, 0), ("left", 0, -1), ("right", 0, 1)):
                    if i.pressed(name):
                        doc.move(dy, dx, True if getattr(i, "mods", 0) & _SHIFT else None)
                        moved = True
            if text:
                self._into_doc(ek, ch, bool(getattr(i, "mods", 0) & _SHIFT))
                moved = True
            if moved:
                self._damage.all()
                return True
        return uitree_ref.Engine.keys(self, i)

    def _grown(self):
        root = self.panes_root
        g = root.l.get("grown") if root is not None else None
        return g if g is not None and self.s._v[g] is not None else None

    def _back(self):
        g = self._grown()
        if g is not None and not self.popover_shown():
            old = self.s._v[g]
            self.s._v[g] = None
            self.s._ver[g] += 1
            S._trace("state", g, "%r -> None   (back)" % old)
            self._damage.all()
            return True
        for k in self.shown_popovers():
            if "close" in k.l:
                self.q.append((k.l["close"], self.args_for(k.l["close"]), "back on popover %r" % k.key))
                self._damage.all()
                return True
        return uitree_ref.Engine._back(self)

    def _has_page(self):
        """Back is the app's while a popover or a row with close= (a find
        strip) is shown."""
        return (uitree_ref.Engine._has_page(self) or self.popover_shown()
                or (self.root is not None and self._closer(self.root) is not None)
                or self._grown() is not None)

    # -- a row lent to a site ------------------------------------------------------

    def lent(self, site):
        """The shown row lent to `site` (`ui.tools(KEY, lend=SITE)`), its
        cells before layout: [kind, key, value, label, icon, flags, action]
        each, kind "tab", "button" or "text" (a label), flags LC_*; a tabs'
        values each a cell, those its offered= holds. None where no row is
        lent there."""
        if self.root is None:
            self._build()
        lenders = {}
        was, _NOW.app = _NOW.app, self
        try:
            S.LIVE = S.live_units(self.root, lenders)
            n = lenders.get(site)
            return None if n is None else self._lent_cells(n)
        finally:
            _NOW.app = was

    def _lent_cells(self, n):
        out = []
        for k in n.kids:
            if not self._shown(k, None):
                continue
            if k.kind == "tabs":
                tab = self.decl.labels[k.l["labels"]]
                icons = self.decl.labels.get(k.l.get("icons")) or {}
                offered = k.l.get("offered") or {}
                now = self.s._v[k.key]
                for v in self.s._t[k.key].values:
                    when = offered.get(v)
                    if when is None or when(None):
                        out.append(["tab", k.key, v, tab[v], icons.get(v),
                                    LC_CUR if now == v else 0, k.l.get("on")])
            elif k.kind == "button":
                a = k.act
                flags = (LC_ALONE if k.l.get("alone") else 0) | (
                    LC_PRIMARY if k.l.get("kind") == "primary" else 0) | (
                    LC_ON if a is None or a.can(None) else 0) | (
                    LC_MARKED if "marked" in k.p and k.p["marked"](None) else 0)
                out.append(["button", k.key, None, self._t(k, "label", None),
                            self._t(k, "icon", None) or None, flags, a])
            elif k.kind == "label":
                out.append(["text", k.key, None, self._t(k, "text", None), None, 0, None])
            else:
                raise S.UIError("N03", "a %s in a row lent to the bar" % k.kind, where=k.where,
                                expected="tabs, button or label",
                                fix="lend the bar tabs, buttons and a label")
        return out

    def lent_place(self, cells, x0, w, h, fs, pad):
        """The lent row's layout (`lent_place`): the rule `uit_lent_place`
        runs on the C engine."""
        return lent_place(cells, x0, w, h, fs, pad)

    # -- for the scripts --------------------------------------------------------

    def divider_at(self, nth=0, sid=None):
        """The centre of a divider this frame drew, in the app's canvas."""
        items = [(k, v) for k, v in self.dividers.items() if sid is None or k == sid]
        allv = [r for _k, v in items for r in v]
        r = allv[nth]
        return r[0] + r[2] // 2, r[1] + r[3] // 2


class Page(Engine):
    """`uitree_editor.Page` replayed: a page on the Editor's kinds hosted by
    a surface on today's verbs (its own `__init__`, as `uitree_ref.Page`)."""

    def __init__(self, build, host, damage, files=None, close=None):
        Engine.__init__(self, S.decl_of(S.module_of(build)), build, host, None, damage, files)
        self.close_root = close
