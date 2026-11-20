# Map (grep -n a name to jump there):
#   Engine                the replay of the Editor's kinds over uitree_app.Engine
#   -- the page: regions stacked  regions down the page, overlays last
#   -- a row              a row's widths and cells
#   Engine.hosted         the bridge's first phase: regions placed, the hosted surfaces drawn
#   Engine._r_legacy      a legacy node in the pass: one stop over its body
#   Engine.point          a pointer routed to rows, panes, dividers, the text editor
#   -- panes and their dividers  the desk Editor's rules over ui.split, a pane's header
#   Engine._pane_keys     F6, Ctrl+Shift+Enter, Ctrl+0, Ctrl+Alt+arrows
#   -- the text editor    ui.text_editor over ui.text_view and the document's keys
#   -- popovers hang      popovers hung from a node's part
#   Engine.keys           keys: popovers, the text editor, then rows
#   Engine.type_byte      a typed byte into the focused document
#   Engine.divider_at     a divider's position, for the scripts
#   Page                  a page on the Editor's kinds hosted by a surface on today's verbs
#   BarZone               the page's row lent to the shell's bar (lend="bar")
#   BarZone.draw_zone     the lent row drawn into the bar's zone
#   BarZone.zone_tap      a tap on the lent row
#   BarZone.activate      a lent cell's action run
"""The replay of the Editor's kinds (uitree.py EXT-4) through today's `ui`
verbs: regions stacked down the page, rows, tabs, panes over `ui.split`
with its dividers, a pane's header, the text editor over `ui.text_view` and
the document's keys, a slot, a status line, popovers hung from a node's
part, and the bridge's `legacy` node, a surface still on today's verbs
hosted in a region.

What is placed here by arithmetic -- a row's widths, the caret's box, a
tab's x -- is the engine's in the shape; the replay computes it the way the
Code tab and the desk do today."""
from array import array

from moy_input import SHIFT as _SHIFT
import ui as _v
import ui_draw as _ud
import layout_base as _lb
import uitree as S
from uitree import _NOW
import uitree_app
from ui_host import _outside

_SYN = {"python": "python", "lua": "lua", "json": "json", "markdown": "markdown", "plain": "plain"}
_FILL = ("switch", "panes", "pane", "text_editor", "slot", "panel", "case", "canvas", "legacy")
# what the hosted phase walks to place the legacy nodes: the regions
_PLACED = ("case", "switch", "panes", "pane", "panel", "legacy")
# The panes' sizes in desk units (runtime/editors/editor_panes.py's, the
# desk Editor's): the gutter, a pane's header, a divider's handle drawn and
# the touch it takes, a touch drag's travel before it takes, the
# Ctrl+Alt+arrow step, a pane's least size where it names none.
GUTTER = 6
HEADER = 32
HANDLE = (6, 36)
HANDLE_HIT = (16, 48)
DRAG_SLOP = 8
KEY_STEP = 8
FLOOR = (64, HEADER + 16)
RADIUS = 8
DOUBLE_MS = 400            # a second tap this soon on a divider resets it
# The key event (native/moy_input/moy_input.h): the code in the low 16
# bits, the modifiers above.
_MODS_AT = 16
_CTRL, _KSHIFT, _ALT = 1, 2, 4
_F6 = 0x101 + 5
_LEFT, _RIGHT, _UP, _DOWN = 0x110, 0x111, 0x112, 0x113
_ENTERS = (10, 13)


def fit(start, mins, total):
    """The children's sizes along a panes `total` long (gutters taken
    off): each held child at its size, at least its minimum, and the one
    that grows (None in `start`) the rest; where the rest falls under its
    minimum the held children give it room, last first, down to their own
    (editor_panes._fit, the desk's rule)."""
    sizes = [0 if v is None else max(v, m) for v, m in zip(start, mins)]
    flex = start.index(None) if None in start else len(start) - 1
    rest = total - sum(sizes[i] for i in range(len(sizes)) if i != flex)
    need = mins[flex] - rest
    for i in range(len(sizes) - 1, -1, -1):
        if need <= 0:
            break
        if i == flex:
            continue
        give = min(need, sizes[i] - mins[i])
        if give > 0:
            sizes[i] -= give
            rest += give
            need -= give
    sizes[flex] = max(0, rest)
    return sizes
STATUS_SEP = " \u00b7 "  # a status template's fields are split at it
_TEXT_PAD = 8          # moy_ui_api_int.h's TEXT_PAD: the view's text inset at the scale



class Engine(uitree_app.Engine):

    STOPS = uitree_app.Engine.STOPS + ("legacy",)

    def __init__(self, *a, **k):
        uitree_app.Engine.__init__(self, *a, **k)
        self.anchors = {}      # "key.part" -> (x, y, w, h), as this frame placed it
        self.views = {}        # text editor key -> [rect, top drawn, cell, line h, x0, rows]
        self._tv = {}          # text editor key -> (its packed options, its answer, its id pair)
        self._mono = [None, 0, 0, 0]    # look epoch, scale: the mono cell and line pitch
        self.split_out = array("i", [0] * (4 * 16 + 4))
        self.dividers = {}     # panes node id -> the divider rects this frame drew
        self.edits_traced = None
        self.host_arrows = False   # the arrows reach the document through the host (the console's nav)
        self.ground = None         # a box the page's ground covers past its content: the
                                   # canvas round the host's rect (a handheld's bar band)
        # the bridge (`legacy`): the hosted phase places and draws them
        self.under = True          # the hosted phase draws the page's ground round them
        self.placing = False       # the hosted phase's walk: regions placed, nothing drawn
        self.placed = []           # (node, row, rect) of each legacy node the phase placed
        self._hosted_no = -1       # the frame whose hosted phase ran
        self.legacies = {}         # key -> (node, rect), as this frame's pass drew it
        self.legacy_gens = {}      # key -> its gen() when its body was drawn
        self.held_legacy = None    # (node, rect) a press holds until its release
        # the panes: their splits as drawn, pane focus, the divider's flash
        self.du = 1                # the desk unit, this frame's
        self.mouse = None          # a host's word on its pointer (True: hovers), else the surface's
        self.splits = {}           # split id -> (panes node, kids, sizes) as this frame placed them
        self.pane_order = []       # (pane node, rect) of the panes shown, in reading order
        self.panes_root = None     # the outermost panes shown this frame
        self.pane_cur = None       # the key of the pane the keys reach
        self.flash = None          # (pane node, words): a divider held at a minimum
        self._last_div = (None, 0)  # the last tap on a divider: (split id, divider), when
        self._ev = 0               # the last frame's key event
        self._focus_seen = None    # the focused node the last pass ended on

    # -- the page: regions stacked, overlays last ---------------------------

    def _frame_setup(self, n):
        w, h = self.content[2], self.content[3]
        self.classes = ("desk" if self.desk else "handheld", "wide" if w * 3 > h * 4 else "tall")
        self.lenders = {}
        S.LIVE = S.live_units(n, self.lenders)
        self.du = max(1, _lb.chrome_size(self.fs) // _lb.render_scale())
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
        if hosting and self.placed:
            # the hosted phase drew the ground round the bodies: the page
            # opens over them drawing nothing
            self.cv.clip(0, 0, 0, 0)
            rect = _v.page(n.p["title"](None))
            self.cv.clip()
        else:
            rect = _v.page(n.p["title"](None))
        if g is not None:
            rect = tuple(self.content)
        hint = self._hints()
        if hint and _v.hints_width(hint) > 0:
            # the page's band: under everything it stacks
            band, rect = _v.cut_bottom(rect, self._h_row())
            _v.at(*band)
            _v.hints(hint)
        last = self.focused                     # the last frame's focus
        prev, self.ids = self.ids, {}
        self.anchors = {}
        self.dividers = {}
        moved, ahead = self._land_focus(prev)
        self.rect = rect
        self.dups = set()
        self._lists_before = getattr(self, "_lists_now", set())
        self._lists_now = set()
        self.late = []
        self._stack(n, None, rect)
        if self.flash is not None:
            self._draw_flash()
        for k, row, r in self.late:
            self._draw(k, row, r)
        self._focus_after(moved, ahead, hint, last)
        self._pane_follow()

    # -- the bridge: surfaces hosted in `legacy` nodes ------------------------
    #
    # A page that hosts a surface still drawn with today's verbs draws in
    # two phases: `hosted`, which its host runs BEFORE it begins the frame,
    # places the page's regions by arithmetic (nothing drawn), draws the
    # page's ground round them and then each hosted surface in its own
    # verbs pass; the frame's pass (`paint`) then draws everything else,
    # each legacy node a focus stop that draws nothing, its overlays last.
    # No verbs pass ever runs inside another: the desk Editor's own order
    # (its pane bodies, then its one verbs pass of headers, dividers and
    # menus), and what a C engine does as layout, then its paint in layers.

    def hosted(self, dt, cv, th, content, desk):
        self._bind()
        self._start(dt)
        self._started = True
        self._hosted_no = self.frame_no
        self.cv, self.th = cv, th
        self.content = content
        self.desk = desk
        self.fs = max(1, getattr(cv, "font_scale", 1))
        self._ready()
        placed = self.placed = []
        if not self._hosts(self.root):
            return
        was = _NOW.app, _NOW.frame
        _NOW.app, _NOW.frame = self, True
        try:
            self._frame_setup(self.root)
            rect = tuple(self.content) if self.ground is not None else tuple(content)
            hint = self._hints()
            if hint and self._band_w(hint, cv, th) > 0:
                rect = (rect[0], rect[1], rect[2], rect[3] - self._h_row())
            self.placing = True
            try:
                self._stack(self.root, None, rect)
            finally:
                self.placing = False
            if self.under and placed:
                self._ground_round(cv, th, placed)
            for n, row, r in placed:
                focused = self.focused is not None and self.focused[0] is n
                _NOW.app, _NOW.frame = self, True
                n.l["draw"](cv, th, r[0], r[1], r[2], r[3], focused, dt)
                cv.clip()
                if "gen" in n.l:
                    _NOW.app = self
                    self.legacy_gens[n.key] = n.l["gen"]()
        finally:
            _NOW.app, _NOW.frame = was

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
        # one stop over the body; the stop's well is not drawn (the body is)
        self.cv.clip(0, 0, 0, 0)
        _v.at(*rect)
        _v.part(n.key, 0, 0, {"id": n.key})
        self.cv.clip()
        self.ids[n.key] = (n, None)
        self.legacies[n.key] = (n, rect)

    def legacy_moved(self):
        """Whether a hosted surface's gen() moved since its body was drawn."""
        was, _NOW.app = _NOW.app, self
        try:
            for key, (n, _r) in self.legacies.items():
                if "gen" in n.l and n.l["gen"]() != self.legacy_gens.get(key):
                    return True
            return False
        finally:
            _NOW.app = was

    def idle_step(self):
        if self.legacy_moved():
            self._damage.all()
            return
        uitree_app.Engine.idle_step(self)

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
        if k.kind == "row" and any(c.kind == "status" for c in k.kids) and \
                not any(c.kind == "field" for c in k.kids):
            return self._height(next(c for c in k.kids if c.kind == "status"))   # a status line's
        return self._h_row()

    def _in_pane_head(self, k):
        return k.parent is not None and k.parent.kind == "pane" and self.desk

    def _shown(self, k, row):
        return uitree_app._shown(k, row) and (k.act is None or k.act.can(row)
                                              or getattr(k.act, "off", "hide") == "grey")

    def _o(self, n, row, **o):
        o = uitree_app.Engine._o(self, n, row, **o)
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
            self._draw(k, row, (x, y, w, kh))
            y += kh
        for k in kids:
            if k.kind in ("popover", "dialog") and not self.placing:
                self.late.append((k, row, rect))     # over every pane, after the page

    def _draw(self, k, row, rect):
        if self.placing and k.kind not in _PLACED:
            return
        getattr(self, uitree_app._rname(k.kind))(k, row, rect)

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
                self._r_label(k, row, (x + g, ty, w - 2 * g, 16))
                ty += 18

    # -- a row ----------------------------------------------------------------

    def _natural(self, k, row, h):
        if k.kind == "button":
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
        if k.kind == "tabs":
            st = self.s._t[k.key]
            tab = self.decl.labels[k.l["labels"]]
            return sum(_v.text_size(tab[v])[0] + (28 if self.desk else 14 * self.fs)
                       for v in st.values)
        return None

    def _r_row(self, n, row, rect):
        x, y, w, h = rect
        if n.key:
            self.anchors[n.key] = rect
        kids = []
        for k in n.kids:                # a lent site: the shown lender's buttons, in its place
            if k.kind == "lent":
                t = self.lenders.get(k.key)
                kids += [b for b in t.kids if self._shown(b, row)] if t is not None else []
            elif self._shown(k, row):
                kids.append(k)
        if any(k.kind == "status" for k in n.kids) and not any(k.kind == "status" for k in kids):
            _ud.status_row(self.cv, self.th, rect, ())     # a status row's band, its line hidden
        nat = [self._natural(k, row, h) for k in kids]
        grow = [i for i, v in enumerate(nat) if v is None]
        if not grow:
            grow = [i for i, k in enumerate(kids) if k.kind == "tabs"][:1]
            for i in grow:
                nat[i] = None
        gap = 4 if self.desk else 2
        rest = w - sum(v for v in nat if v) - gap * max(0, len(kids) - 1)
        for i, k in enumerate(kids):
            kw = nat[i] if nat[i] is not None else max(0, rest // max(1, len(grow)))
            r = (x, y, kw, h) if k.kind == "status" else (x, y + 2, kw, h - 4)   # a band: the row's height
            if k.kind == "button":
                self._r_button(k, row, rect, at=r)
            else:
                _v.at(*r)
                self._draw(k, row, r)
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
            self._r_button(k, row, rect, at=(x, y + 1, kw - 1, h - 2))
            x += kw

    def _r_canvas(self, n, row, rect):
        _v.at(*rect)
        x, y, w, h, _st = _v.part(n.key, 0, 0, {"id": n.key})
        self.ids[n.key] = (n, None)
        self.canvases[n.key] = (n, (x, y, w, h))
        self.cv.clip(x, y, w, h)
        if "ground" in n.l:
            self.cv.rect(x, y, w, h, self.th[n.l["ground"]])
        n.l["draw"](self.cv, x, y, w, h)
        self.cv.clip()

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
            uitree_app.Engine.point(self, px, py, click)
            self._damage.all()
            return True
        if click:
            for n, r in self.legacies.values():
                if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    # the press focuses the stop (the toolkit's) and the
                    # hosted surface takes it, in its own coordinates
                    uitree_app.Engine.point(self, px, py, click)
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
        return uitree_app.Engine.point(self, px, py, click)

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
        uitree_app.Engine._r_label(self, n, row, rect)

    def _r_button(self, n, row, rect, at=None):
        uitree_app.Engine._r_button(self, n, row, rect, at=at or (rect[0], rect[1], rect[2],
                                                                min(rect[3], self._h_row())))

    def _r_tabs(self, n, row, rect, key=None, labels=None):
        key = key or n.key
        st = self.s._t[key]
        tab = self.decl.labels[labels or n.l["labels"]]
        vals = st.values
        cur = vals.index(self.s._v[key])
        _v.at(*rect)
        got = _v.tabs(key, [tab[v] for v in vals], cur)
        self.ids[key] = (n, None)
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

    def _size_of(self, k):
        """A child's held size now, in pixels: its state, else its start
        (desk units); None grows."""
        if k.l.get("grow") or "size" not in k.l:
            return None
        v = self.s._v[k.l["size"]]
        return k.l["start"] * self.du if v is None else v

    def _min_of(self, k, axis):
        """A child's least size along `axis`, in pixels, its gutters in."""
        if k.kind == "pane":
            return k.l.get("min", FLOOR)[axis] * self.du
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

    def _mouse(self):
        if self.mouse is not None:
            return self.mouse
        p = self._surf.pointer() if self._surf is not None else None
        return bool(getattr(p, "hovers", False))

    def _r_panes(self, n, row, rect):
        """The desk Editor's rules (runtime/editors/editor_panes.py): sizes
        in desk units; held sizes yield to the area, last first, down to
        their minimums, so the growing child keeps its own; under the
        workspace's least size, or grown, one pane stands over the area
        alone. The dividers are `ui.split`'s: a drag's let-go writes the
        held sizes beside it, a double tap puts them back."""
        outer = n.parent is None or n.parent.kind not in ("pane", "panes")
        if outer:
            self.panes_root = n
            alone = self._alone(n, rect)
            if alone is not None:
                self._draw(alone, row, rect)
                return
        axis = self._axis(n)
        kids = [k for k in n.kids if self._shown(k, row)]
        gap = self._gap(n)
        span = rect[2 + axis] - gap * (len(kids) - 1)
        held = [self._size_of(k) for k in kids]
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
        self.splits[sid] = (n, kids, sizes)
        if not self.placing:
            self._split(n, sid, kids, sizes, mins, held, rect, axis, gap)
            self.dividers[sid] = divs
        for k, r in zip(kids, rects):
            self._draw(k, row, r)

    def _split(self, n, sid, kids, sizes, mins, held, rect, axis, gap):
        """The dividers of panes `n` on `ui.split`: drawn, a press on one
        held, its drag's let-go written, a double tap reset; held at a
        minimum, the flash."""
        u = self.du
        mouse = self._mouse()
        o = {"drag": True, "gap": gap, "mins": mins, "line": 2 * u,
             "slop": 0 if mouse else DRAG_SLOP * u, "handle": (HANDLE[0] * u, HANDLE[1] * u),
             "hit": None if mouse else (HANDLE_HIT[0] * u, HANDLE_HIT[1] * u),
             "focus": self._keys_child(kids)}
        _v.at(*rect)
        out = self.split_out
        got = list(sizes)
        _v.split(sid, "x" if axis == 0 else "y", got, o, out)
        nk = len(kids)
        i, stop, bits = out[4 * nk], out[4 * nk + 2], out[4 * nk + 3]
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
            self._damage.all()
        elif bits & _v.LIVE and stop >= 0:
            child = kids[i + stop]
            pane = self._first_pane(child)
            m = self._min_of(child, axis)
            self.flash = (pane, "%s stops at its minimum, %d %s" % (
                self._pane_name(pane), m, "wide" if axis == 0 else "tall"))

    def _hold(self, kids, sizes, js, why):
        """The held sizes of children `js` written from `sizes` (pixels);
        the child that grows holds none."""
        for j in js:
            k = kids[j]
            if "size" not in k.l or k.l.get("grow"):
                continue
            nm = k.l["size"]
            if self._size_of(k) == sizes[j] and self.s._v[nm] is not None:
                continue
            old = self.s._v[nm]
            self.s._v[nm] = sizes[j]
            self.s._ver[nm] += 1
            S._trace("state", nm, "%r -> %r   (%s)" % (old, sizes[j], why))

    def _unhold(self, kids, why):
        """Children `kids` back at their start= (their states absent)."""
        for k in kids:
            if "size" in k.l and not k.l.get("grow") and self.s._v[k.l["size"]] is not None:
                nm = k.l["size"]
                old = self.s._v[nm]
                self.s._v[nm] = None
                self.s._ver[nm] += 1
                S._trace("state", nm, "%r -> None   (%s)" % (old, why))

    @staticmethod
    def _first_pane(n):
        while n.kind == "panes":
            n = n.kids[0]
        return n

    def _pane_name(self, pane):
        if "title" in pane.p:
            return self._t(pane, "title", None)
        tabs = pane.l.get("tabs")
        if tabs:
            return self.decl.labels[pane.l["labels"]][self.s._v[tabs]]
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

    def _panes_in(self, n):
        """The shown panes under `n`, in reading order."""
        out = []

        def walk(k):
            for c in k.kids:
                if not self._shown(c, None):
                    continue
                if c.kind == "pane":
                    out.append(c)
                elif c.kind == "panes":
                    walk(c)
        walk(n)
        return out

    def _pane_named(self, n, key):
        return next((p for p in self._panes_in(n) if p.key == key), None) if key else None

    def _pane_of(self, node):
        while node is not None and node.kind != "pane":
            node = node.parent
        return node

    def _keys_child(self, kids):
        """The child of a split the keys reach (its pane, or the panes
        holding it), -1 where none does."""
        p = self._pane_named(self.panes_root, self.pane_cur) if self.panes_root is not None else None
        while p is not None:
            if p in kids:
                return kids.index(p)
            p = p.parent
        return -1

    def _r_pane(self, n, row, rect):
        x, y, w, h = rect
        u = self.du
        hh = HEADER * u
        self.pane_order.append((n, rect))
        body = [k for k in n.kids if k.kind != "tools"]
        tmp = S.Node("case", n.key + ":body", n.where)
        tmp.kids, tmp.parent = body, n
        if not self.placing:
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
            def walk(k):
                for c in k.kids:
                    if c.kind in ("pane", "panes"):
                        self._unhold([c], "Ctrl+0")
                        walk(c)
            walk(root)
            self._damage.all()
            return True
        if ctrl and mods & _ALT and code in (_LEFT, _RIGHT, _UP, _DOWN):
            return self._nudge(code)
        return False

    def _nudge(self, code):
        """Ctrl+Alt+arrows: the focused pane's right (left and right) or
        bottom (up and down) divider moves KEY_STEP units, neither pane
        beside it under its minimum."""
        axis = 0 if code in (_LEFT, _RIGHT) else 1
        step = KEY_STEP * self.du * (-1 if code in (_LEFT, _UP) else 1)
        p = self._pane_named(self.panes_root, self.pane_cur)
        while p is not None and p.parent is not None and p.parent.kind == "panes":
            par = p.parent
            for sid, (pn, kids, sizes) in self.splits.items():
                if pn is par and self._axis(par) == axis and p in kids and kids.index(p) < len(kids) - 1:
                    i = kids.index(p)
                    lo = self._min_of(kids[i], axis)
                    hi = max(lo, sizes[i] + sizes[i + 1] - self._min_of(kids[i + 1], axis))
                    pair = sizes[i] + sizes[i + 1]
                    got = list(sizes)
                    got[i] = min(hi, max(lo, sizes[i] + step))
                    got[i + 1] = pair - got[i]
                    self._hold(kids, got, (i, i + 1), "Ctrl+Alt+arrow on %r" % self.pane_cur)
                    self._damage.all()
                    return True
            p = par
        return True

    def _r_slot(self, n, row, rect):
        """The shell docks another process's window here; the replay draws a
        stand-in for the game's frame at 1x, centred."""
        x, y, w, h = rect
        cv = self.cv
        self.anchors[n.key] = rect
        gw, gh = min(w, 320), min(h, 240)
        gx, gy = x + (w - gw) // 2, y + (h - gh) // 2
        th = self.th
        cv.rect(x, y, w, h, th["ink"])
        cv.rect(gx, gy, gw, gh, th["value"])
        for i in range(12):
            cv.rect(gx + (i * 53) % max(1, gw - 4), gy + (i * 37) % max(1, gh - 4), 3, 3, th["panel"])
        cv.rect(gx + gw // 2 - 8, gy + gh - 20, 16, 8, th["panel"])
        cv.print("slot 'run': the docked game", gx + 6, gy + 6, th["panel"])

    # -- the text editor --------------------------------------------------------

    def _mono_metrics(self):
        """The mono face's (cell, line pitch) at the scale: a version 2
        face's glyph box, petme128's line and its lead. Read again when the
        look moved (`ui_draw.look_moved`, which a font change publishes)."""
        m = self._mono
        if m[0] != _ud._GEN or m[1] != self.fs:
            fid, fw, line, _asc = _ud.font("mono")
            info = _ud.font_info(fid) if fid else None
            m[0], m[1] = _ud._GEN, self.fs
            m[2] = fw * self.fs
            m[3] = (info[1] if info is not None and info[4] == 0 else line + 2) * self.fs
        return m

    def _pitch(self):
        return self._mono_metrics()[3]

    def _r_text_editor(self, n, row, rect):
        """A text editor's rows: one `ui.text_view` on its packed options,
        which a frame fills in place (the view allocates nothing a frame).
        Its parts' anchors (`caret`, `word`) are read where a popover hangs
        from one (`_part_anchor`)."""
        key = n.key
        doc = self.s._v[key]
        ed = doc.ed
        fs = self.fs
        m = self._mono_metrics()
        cell, lh = m[2], m[3]
        x, y, w, h = rect
        lines = max(1, h // lh)
        if lines * lh < h:
            self.cv.rect(x, y + lines * lh, w, h - lines * lh, self.th["surface"])
            h = lines * lh
        tv = self._tv.get(key)
        if tv is None:
            o = _v.text_options({"syntax": _SYN[doc.lang], "mark": True})
            tv = self._tv[key] = (o, array("i", [0, 0]), (n, None))
            v = self.views[key] = [[x, y, w, h], -1, cell, lh, 0, 1]
        else:
            v = self.views[key]
        o, into, pair = tv
        r = v[0]                        # one assignment a word: an unpacking of four builds a tuple
        r[0] = x
        r[1] = y
        r[2] = w
        r[3] = h
        gutter = bool(n.p["gutter"](row)) if "gutter" in n.p else False
        gcols = 0
        if gutter:
            k = len(ed.lines)
            gcols = 2
            while k >= 10:
                k //= 10
                gcols += 1
        x0 = x + _TEXT_PAD * fs + gcols * cell
        ed.set_view_size(max(1, (w - (x0 - x)) // cell), lines)
        q = ""
        if "find" in n.l and ("find_on" not in n.p or n.p["find_on"](row)):
            q = self.s._v[n.l["find"]]
        doc.find_q = q
        err = None
        o[_v.TXO_ERR] = -1
        if "marks" in n.l:
            marks = self.s._v[n.l["marks"]]
            for i in range(len(marks)):
                mk = marks[i]
                if mk.kind == "error":
                    o[_v.TXO_ERR] = mk.line - 1
                    err = mk.words
                    break
        o[_v.TXO_GUTTER] = 1 if gutter else 0
        o[_v.TXO_CARET] = ed.row
        o[_v.TXO_CARET + 1] = ed.col
        k = _v.TXO_SEL
        b = ed.selection_bounds() if ed.sel is not None else None
        if b is None:
            o[k] = o[k + 2] = -1
            o[k + 1] = o[k + 3] = 0
        else:
            o[k] = b[0]
            o[k + 1] = b[1]
            o[k + 2] = b[2]
            o[k + 3] = b[3]
        o[_v.TXO_FIND_CI] = 1 if q and doc.find_ci else 0
        o[_v.TXO_LEFT] = ed.left
        o[_v.TXO_TOP] = ed.top if v[1] != ed.top else -1
        _v.at(x, y, w, h)
        _v.text_view(key, ed.lines, o, err, q or None, into)
        ed.top = into[0]
        if into[1] > 0:
            lh = h // into[1]
        v[1] = ed.top
        v[2] = cell
        v[3] = lh
        v[4] = x0
        v[5] = max(1, into[1])
        self.ids[key] = pair
        self.anchors[key] = r

    def _part_anchor(self, name):
        """The rect a popover hangs from: a node's, or a part's a frame
        placed (a tab's, a pane's head), or a text editor's `caret` or
        `word`, read from its view as last drawn."""
        a = self.anchors.get(name)
        if a is not None or "." not in name:
            return a
        key, part = name.split(".", 1)
        v = self.views.get(key)
        if v is None or part not in ("caret", "word") or key not in self.s._v:
            return None
        ed = self.s._v[key].ed
        r, top, cell, lh, x0 = v[0], v[1], v[2], v[3], v[4]
        cy = r[1] + (ed.row - top) * lh + (lh // 2 if self.desk else 0)
        cx = x0 + (ed.col - ed.left) * cell
        if part == "caret":
            return (cx, cy, cell, lh)
        p = len(ed.word_prefix())
        return (cx - p * cell, cy, max(cell, p * cell), lh)

    # -- popovers hang from a node's part -------------------------------------

    def _r_popover(self, n, row, rect):
        w = n.p["w"](row) if "w" in n.p else 200
        name = n.p["anchor"](row) if "anchor" in n.p else None
        a = self._part_anchor(name) if name else None
        if a is not None and "." not in name and n.kids and n.kids[0].kind == "field":
            # hung from a whole node, a popover that opens on its field
            # (Go to) stands centred over that node's top
            w = min(w, a[2] - 16 * self.fs)
            a = (a[0] + (a[2] - w) // 2, a[1] + 4 * self.fs, w, 0)
        if a is not None:
            _v.at(*a)
        if not _v.popover(n.key, w) and "close" in n.l:
            self.fire(n.l["close"], self.args_for(n.l["close"]), n, None)
        for k in n.kids:
            if self._shown(k, row):
                if k.kind == "field":
                    self._r_field(k, row, self.rect)
                elif k.kind == "label":
                    uitree_app.Engine._r_label(self, k, row, rect)   # down the card, as it flows
                else:
                    self._draw(k, row, rect)
        if self._a_note(n) and "close" in n.l and n.l["close"].hint_now():
            _v.hints(("cancel", n.l["close"].hint_now()))     # a note's card names its own close
        _v.popover()

    @staticmethod
    def _a_note(p):
        """A popover of words alone (See why): nothing in it takes the keys."""
        return not any(k.kind in ("field", "rows", "link", "button", "menu") for k in p.kids)

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
            return uitree_app.Engine._row_stop_id(self, lst, rowkey)
        r = next((r for r in self.s._v[lst.l["model"]] if r._f[r._m.key] == rowkey), None)
        return self._menu_text(lst.kids[0], r) if r is not None else None

    def _r_rows(self, n, row, rect):
        if not self._menu_rows(n):
            uitree_app.Engine._r_rows(self, n, row, rect)
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
            self.ids[t] = (link, r)
        got = _v.menu("", texts)
        if got >= 0:
            self._activate(link, model[got])

    def _r_link(self, n, row, rect):
        uitree_app.Engine._r_link(self, n, row, rect)

    def _hints(self):
        """A text surface keeps its keys: no band while it (or nothing) holds
        focus. A popover or a closer row (a find strip) names what Enter
        does there and what Back closes."""
        if self.dialog_shown():
            return uitree_app.Engine._hints(self)
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
            return uitree_app.Engine._hints(self)
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

    def pointer_down(self):
        """Whether the pointer is held: the surface's; a hosted page's host
        replaces it with its own."""
        p = self._surf.pointer() if self._surf is not None else None
        return bool(p is not None and p.down)

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

    def type_byte(self, ch, shift=False):
        """One typed byte into the shown text editor, outside a frame's input
        (a held key's repeat, a symbol key): what `keys` does with it."""
        was, _NOW.app = _NOW.app, self
        try:
            ek = self.editor_keys()
            if ek is None:
                return False
            return self._into_doc(ek, ch, shift)
        finally:
            _NOW.app = was

    def _into_doc(self, ek, ch, shift):
        doc = self.s._v[ek]
        before = doc.summary()
        if ch == 9 and shift:
            got = doc.outdent()
        else:
            got = doc.key(ch)
        if got:
            self.s._ver[ek] += 1
            tag = "(typed in text_editor %r)" % ek
            if self.trace and self.trace[-1].endswith(tag):
                first = self.trace.pop().split(" -> ")[0].split("state   %s " % ek)[-1].strip()
                before = first
            S._trace("state", ek, "%s -> %s   %s" % (before, doc.summary(), tag))
        self._damage.all()
        return True

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
            if not uitree_app._shown(k, None):
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
        return uitree_app.Engine.keys(self, i)

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
        return uitree_app.Engine._back(self)

    def _has_page(self):
        """Back is the app's while a popover or a row with close= (a find
        strip) is shown."""
        return (uitree_app.Engine._has_page(self) or self.popover_shown()
                or (self.root is not None and self._closer(self.root) is not None)
                or self._grown() is not None)

    # -- for the scripts --------------------------------------------------------

    def divider_at(self, nth=0, sid=None):
        """The centre of a divider this frame drew, in the app's canvas."""
        items = [(k, v) for k, v in self.dividers.items() if sid is None or k == sid]
        allv = [r for _k, v in items for r in v]
        r = allv[nth]
        return r[0] + r[2] // 2, r[1] + r[3] // 2


class Page(Engine):
    """A page on the Editor's kinds hosted by a surface still on today's
    verbs (`uitree_app.Page`'s bridge): the host begins the frame, routes
    the keys and the pointer, and `paint` replays the page into its rect."""

    def __init__(self, build, host, damage, files=None, close=None):
        Engine.__init__(self, S.decl_of(S.module_of(build)), build, host, None, damage, files)
        self.close_root = close


class BarZone:
    """The page's row lent to the shell's bar (`ui.tools(KEY, lend="bar")`),
    replayed by the engine into the bar's zone: what the bar layer asks a
    zone's owner (`BarLayer._zone_owner`) -- `draw_zone`, `zone_tap`,
    `zone_targets`, `zone_need`, `zone_gen`, `zone_opened`, `back_title` --
    answered from the page's tree.

    The bar draws its zone into its offscreen strip, outside any frame of
    the page's, so the replay begins no toolkit frame: it lays the shown
    lender's children out and draws each cell on the canvas the bar hands
    it, through `look`, the bar's look for a lent row (`bar_layer.ZoneLook`:
    a tab, an icon, a word, a line of text, a hairline). Taps and the bar's
    key walk come back through the same layout, so a cached strip and a
    later tap agree.

    The row's rule, the engine's and no app's: its cells pack from the left
    in their order; the run of buttons at its end stands at the zone's right
    end, END short of it; a hairline closes a run of tabs and opens that end
    run. It shows its labels (a tab's name, a button's word) where the whole
    row holds them; else the tabs as icons, the current one with its name,
    and the buttons' words, where that fits at no gaps whichever tab is
    current; else the same with the buttons as icons; else icons alone,
    LEAST apart. Gaps share the room left, up to GAP, the rest before the
    end run.

    `zone_gen` moves when the host's own generation does (`host.zone_gen`:
    a tab opened, a cart opened) or when a handler moved the page's state
    since the zone's last pass; the host (`look` where none is named)
    writes the console's values into the state in `host.head_sync`, which
    the zone runs at each pass (a strip
    re-render, a tap, the bar's key walk), never per frame, and whose
    writes the host's own generation already covers -- so a re-render that
    syncs does not owe another."""

    GAP = 5          # the widest gap between two cells, at fs 1: with a tab's pad,
                     # a labelled tab's pill ends 8 px before the next one's icon (R2-08)
    LEAST = 2        # ...the least
    GROUP = 8        # ...the least about a hairline, the line at its middle
    END = 6          # the room after the end run: with the bar's gap, its ink stands
                     # 10 px clear of the status widgets that follow (R4-26)

    def __init__(self, engine, look, site="bar", host=None):
        self.engine = engine
        self.look = look
        self.host = look if host is None else host
        self.site = site
        self.gen = 0
        self.zone_opened = False
        self.back_title = None
        self._vers = -1

    @property
    def zone_gen(self):
        return self.gen + getattr(self.host, "zone_gen", 0)

    # -- the lent row -------------------------------------------------------

    def _row(self):
        """The shown lender's node, the states synced and the tree built."""
        e = self.engine
        e._bind()
        if sum(e.s._ver.values()) != self._vers and self._vers >= 0:
            self.gen += 1               # a handler moved what the row reads since its last pass
        sync = getattr(self.host, "head_sync", None)
        if sync is not None:
            sync()                      # (what it moves, the host's own zone_gen moved for)
        if e.root is None:
            e._build()
        lenders = {}
        was, _NOW.app = _NOW.app, e
        try:
            S.LIVE = S.live_units(e.root, lenders)
        finally:
            _NOW.app = was
        self._vers = sum(e.s._ver.values())
        return lenders.get(self.site)

    def _items(self, n):
        """The row's cells before layout: [kind, node, value, label, icon]
        each, kind "tab", "button", "text"; tabs and a label each a run."""
        e = self.engine
        out = []
        for k in n.kids:
            if not e._shown(k, None):
                continue
            if k.kind == "tabs":
                tab = e.decl.labels[k.l["labels"]]
                icons = e.decl.labels.get(k.l.get("icons")) or {}
                offered = k.l.get("offered") or {}
                for v in e.s._t[k.key].values:
                    when = offered.get(v)
                    if when is None or when(None):
                        out.append(["tab", k, v, tab[v], icons.get(v)])
            elif k.kind == "button":
                icon = e._t(k, "icon", None) or None
                out.append(["button", k, None, e._t(k, "label", None), icon])
            elif k.kind == "label":
                out.append(["text", k, None, e._t(k, "text", None), None])
            else:
                raise S.UIError("N03", "a %s in a row lent to the bar" % k.kind, where=k.where,
                                expected="tabs, button or label",
                                fix="lend the bar tabs, buttons and a label")
        return out

    def _layout(self, rect):
        """[(rect, kind, node, value, label, icon, labelled)], the
        hairlines' x and the density the row packed at (2 every label, 1
        the current tab's name, 0 icons), for the lent row in `rect`."""
        n = self._row()
        if n is None:
            return [], [], 0
        e = self.engine
        was, _NOW.app = _NOW.app, e
        try:
            items = self._items(n)
        finally:
            _NOW.app = was
        return self._place(items, rect)

    def _cur(self, it):
        e = self.engine
        return it[0] == "tab" and e.s._v[it[1].key] == it[2]

    def _widths(self, items, h, density, words=False):
        """Each cell's width at `density`: 2 every label, 1 icons with the
        current tab named (and the buttons' words where `words`), 0 icons
        alone; a text cell 0 (it takes the rest)."""
        look = self.look
        out = []
        for it in items:
            kind, label, icon = it[0], it[3], it[4]
            if kind == "text":
                out.append(0)
            elif self._named(it, density, words):
                out.append(look.tab_width(label, icon, h) if kind == "tab"
                           else look.word_width(label, icon, h))
            elif kind == "tab" and self._cur(it):
                out.append(h + 2 * look.tab_pad())
            else:
                out.append(h)
        return out

    def _named(self, it, density, words=False):
        """Whether cell `it` shows its label at `density` (`words`: a
        button its word at density 1)."""
        if not it[3]:
            return False
        if it[0] == "tab":
            return density == 2 or (density == 1 and self._cur(it))
        return it[0] == "button" and (density == 2 or (density == 1 and words)) \
            and not it[1].l.get("alone")

    def _widest(self, items, ws, h):
        """How much wider the row is with its widest tab named in the
        current one's place: the buttons keep their words only where every
        tab open holds them, so a tab switch never takes them away."""
        cur = [ws[i] for i, it in enumerate(items) if it[0] == "tab" and self._cur(it)]
        named = [self.look.tab_width(it[3], it[4], h) for it in items
                 if it[0] == "tab" and it[3]]
        return max(0, max(named) - cur[0]) if cur and named else 0

    def _place(self, items, rect):
        x0, y, w, h = rect
        fs = self.look.zone_scale()
        w = max(0, w - self.END * fs)
        n = len(items)
        end = n
        while end > 0 and items[end - 1][0] == "button":
            end -= 1
        lines = [i for i in range(1, n) if items[i - 1][0] == "tab" and items[i][0] != "tab"]
        if 0 < end < n and end not in lines:
            lines.append(end)
        text = any(it[0] == "text" for it in items)
        for density, words in ((2, True), (1, True), (1, False), (0, False)):
            # the current tab's name goes before the gaps do: it holds at none
            lg = 0 if density == 1 else self.LEAST * fs
            ws = self._widths(items, h, density, words)
            least = sum(ws) + lg * max(0, n - 1 - len(lines)) + self.GROUP * fs * len(lines)
            if least + (self._widest(items, ws, h) if density == 1 and words else 0) <= w \
                    or density == 0:
                break
        spare = max(0, w - least)
        if text:
            gap = lg
        else:
            gaps = max(1, n - 1 - len(lines))
            gap = lg + min(self.GAP * fs - lg, spare // gaps)
        out, hair = [], []
        x = x0
        for i, it in enumerate(items):
            if i:
                if i in lines:
                    x += self.GROUP * fs
                    if i == end and not text:
                        # the end run stands at the zone's right end
                        x = max(x, x0 + w - sum(ws[end:]) - gap * (n - end - 1))
                    hair.append(x - (self.GROUP * fs + fs) // 2)
                else:
                    x += gap
            cw = ws[i]
            if it[0] == "text":
                cw = spare           # a line of text takes the room left
                spare = 0
            if x + cw > x0 + w:
                break
            labelled = it[0] == "text" or self._named(it, density, words)
            out.append(((x, y, cw, h), it[0], it[1], it[2], it[3], it[4], labelled))
            x += cw
        return out, hair, density

    # -- what the bar asks ----------------------------------------------------

    def draw_zone(self, cv, zone):
        cells, hair, density = self._layout(zone)
        look = self.look
        e = self.engine
        was, _NOW.app = _NOW.app, e
        try:
            for x in hair:
                look.draw_line(cv, x, zone)
            for r, kind, node, v, label, icon, labelled in cells:
                if kind == "tab":
                    look.draw_tab(cv, r, e.s._v[node.key] == v, label, icon, labelled, density == 2)
                elif kind == "text":
                    look.draw_text(cv, r, label)
                else:
                    on = node.act is None or node.act.can(None)
                    primary = node.l.get("kind") == "primary"
                    marked = "marked" in node.p and bool(node.p["marked"](None))
                    if labelled:
                        look.draw_word(cv, r, label, icon, primary, marked)
                    else:
                        look.draw_icon(cv, r, icon, on, primary, marked)
        finally:
            _NOW.app = was

    def zone_targets(self, zone):
        return [c[0] for c in self._layout(zone)[0] if c[1] != "text"]

    def zone_need(self, h):
        """The narrowest zone at band height `h` whose row holds every cell
        as icons, the current tab widened by its pad, at the least gaps."""
        n = self._row()
        if n is None:
            return 0
        e = self.engine
        was, _NOW.app = _NOW.app, e
        try:
            items = self._items(n)
        finally:
            _NOW.app = was
        fs = self.look.zone_scale()
        k = len(items)
        lines = sum(1 for i in range(1, k) if items[i - 1][0] == "tab" and items[i][0] != "tab")
        return sum(self._widths(items, h, 0)) + self.LEAST * fs * max(0, k - 1 - lines) + \
            self.GROUP * fs * lines + self.END * fs

    def box(self, key, value=None, zone=None):
        """Where the cell for `key` (a tabs' `value`) stands in `zone`, or None."""
        for c in self._layout(zone)[0]:
            if c[2].key == key and (value is None or c[3] == value):
                return c[0]
        return None

    def zone_tap(self, px, py, zone):
        """A tap in the zone: the cell under it, a gap's half to either
        neighbour, activated through the engine."""
        cells = self._layout(zone)[0]
        hit = None
        for i, c in enumerate(cells):
            x, y, w, h = c[0]
            lo = x if i == 0 else (cells[i - 1][0][0] + cells[i - 1][0][2] + x + 1) // 2
            hi = x + w if i == len(cells) - 1 else (x + w + cells[i + 1][0][0] + 1) // 2
            if lo <= px < hi and y <= py < y + h:
                hit = c
                break
        if hit is None or hit[1] == "text":
            return False
        self.activate(hit[2], hit[3])
        return True

    def activate(self, node, value=None):
        """A cell taken (a tap, the bar's keys): a tab writes its value and
        runs the tabs' `on=` (the current tab again too); a button runs its
        action where it is on."""
        e = self.engine
        e._bind()
        self.zone_opened = False
        if node.kind == "tabs":
            key = node.key
            old = e.s._v[key]
            if old != value:
                e.s._v[key] = value
                e.s._ver[key] += 1
                S._trace("state", key, "%r -> %r   (from the bar)" % (old, value))
            self.zone_opened = True
            if "on" in node.l:
                e.run_action(node.l["on"], (), "tabs %r on the bar" % key)
        elif node.act is not None:
            was, _NOW.app = _NOW.app, e
            try:
                on = node.act.can(None)
            finally:
                _NOW.app = was
            if on:
                e.run_action(node.act, (), "button %r on the bar" % node.key)
        self._row()
