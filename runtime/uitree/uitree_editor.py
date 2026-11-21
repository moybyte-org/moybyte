# Map (grep -n a name to jump there):
#   Engine                 the Editor's kinds over uitree_app.Engine: what the C engine and the reference share
#   Engine.draw_hosted     the bridge: each hosted surface drawn where the page laid it out
#   Engine.nudge           a divider moved by keys
#   Engine._r_text_editor  the text editor's options and its document's view
#   lent_place             a lent row's cells placed
#   Page                   a page on the Editor's kinds hosted by a surface on today's verbs
#   page                   that page on the engine in use
#   BarZone                the page's row lent to the shell's bar (lend="bar")
#   BarZone.draw_zone      the lent row drawn into the bar's zone
#   BarZone.zone_tap       a tap on the lent row
#   BarZone.activate       a lent cell's action run
"""The Editor's kinds (uitree.py EXT-4) as both engines share them: the
panes' sizes and their rules (`fit`, `nudge`, `reset_sizes`), the hosted
surfaces a page's regions place, the text editor's view over its document,
and the row a page lends the shell's bar (`BarZone`, `lent_place`). The C
engine draws them (`uitree_c.CEditorEngine`); the reference replays them
through today's `ui` verbs (`uitree_ref_editor.Engine`)."""
from array import array

import ui as _v
import ui_draw as _ud
import uitree as S
from uitree import _NOW
import uitree_app
from ui_host import _outside

_SYN = {"python": "python", "lua": "lua", "json": "json", "markdown": "markdown", "plain": "plain"}
_REGIONS = ("case", "switch", "panel")          # a row's cells that stack their own children
HEADER = 32
KEY_STEP = 8


_LEFT, _RIGHT, _UP, _DOWN = 0x110, 0x111, 0x112, 0x113


def _clip_in(cv, x, y, w, h, outer):
    """Clip `cv` to a canvas's rect, inside `outer` where the host gives one:
    whether any of it is left to draw (a canvas outside the host's clip,
    a clock tick's span, is not drawn at all)."""
    if outer is not None:
        x0, y0 = max(x, outer[0]), max(y, outer[1])
        w = max(0, min(x + w, outer[0] + outer[2]) - x0)
        h = max(0, min(y + h, outer[1] + outer[3]) - y0)
        x, y = x0, y0
        if w <= 0 or h <= 0:
            return False
    cv.clip(x, y, w, h)
    return True


def _clip_back(cv, outer):
    """A canvas's clip given back: the host's, else none."""
    if outer is None:
        cv.clip()
    else:
        cv.clip(outer[0], outer[1], outer[2], outer[3])


def fit(start, mins, total):
    """The children's sizes along a panes `total` long (gutters taken
    off): each held child at its size, at least its minimum, and the one
    that grows (None in `start`) the rest; where the rest falls under its
    minimum the held children give it room, last first, down to their own
    (the desk Editor's rule)."""
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
_TEXT_PAD = 8          # moy_ui_api_int.h's TEXT_PAD: the view's text inset at the scale


class Engine(uitree_app.Engine):
    """The Editor's kinds' part of an engine: where the layout placed each
    node, the text editors' views and the panes' sizes."""

    STOPS = uitree_app.Engine.STOPS + ("legacy",)
    clip_to = None         # (x, y, w, h): the clip the host draws the page under, which a canvas's
                           # own clip meets and gives back (a bar's strip drawn again in part)

    def __init__(self, *a, **k):
        uitree_app.Engine.__init__(self, *a, **k)
        self.anchors = {}      # "key.part" -> (x, y, w, h), as this frame placed it
        self.rects = {}        # node key -> (x, y, w, h): where a pass of the layout placed it (`rect_of`)
        self.rects_at = {}     # node key -> the pass that placed it (a dict kept, never cleared: no
        self._rect_pass = 0    # allocation a frame where the same nodes are placed again)
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
        self.placed = []           # (node, row, rect) of each legacy node the phase placed
        self._hosted_no = -1       # the frame whose hosted phase drew a body
        self.legacy_gens = {}      # key -> its gen() when its body was drawn
        self.held_legacy = None    # (node, rect) a press holds until its release
        self.hosting = None        # the legacy node whose body `draw_hosted` is drawing
        # the panes: their splits as drawn, pane focus, the divider's flash
        self.du = 1                # the desk unit, this frame's
        self.unit = 0              # a host's desk unit (0: the engine's, from the font scale)
        self.mouse = None          # a host's word on its pointer (True: hovers), else the surface's
        self.splits = {}           # split id -> (panes node, kids, sizes) as this frame placed them
        self.pane_order = []       # (pane node, rect) of the panes shown, in reading order
        self.panes_root = None     # the outermost panes shown this frame
        self.pane_cur = None       # the key of the pane the keys reach
        self.flash = None          # (pane node, words): a divider held at a minimum
        self.div_held = None        # (split id, divider, at, stop, live): a divider held now
        self.arranged = ([], [])   # the host's placement: ([(pane, rect, path)], [(path, i,
                                   # "h"|"v", rect)]), the whole arrangement even under a pane alone
        self.least = (0, 0)        # the outermost panes' least size, gutters in
        self.alone_pane = None     # the pane the placement stood alone over the panes area
        self.size_gen = 0          # moves whenever a held size is written or put back
        self._path = ()            # the walk's path: child indices from the outermost panes
        self._ghost = False        # the walk lays the arrangement out under a pane alone
        self._arranging = True     # ...and records it (not while the pane alone is drawn)
        self._split_o = {"drag": True}
        self._last_div = (None, 0)  # the last tap on a divider: (split id, divider), when
        self._ev = 0               # the last frame's key event
        self._focus_seen = None    # the focused node the last pass ended on

    # -- the bridge: surfaces hosted in `legacy` nodes ------------------------

    def legacy_moved(self):
        """Whether a hosted surface's gen() moved since its body was drawn."""
        was, _NOW.app = _NOW.app, self
        try:
            for n, _row, _r in self.placed:
                if "gen" in n.l and n.l["gen"]() != self.legacy_gens.get(n.key):
                    return True
            return False
        finally:
            _NOW.app = was

    def idle_step(self):
        if self.legacy_moved():
            self._damage.all()
            return
        uitree_app.Engine.idle_step(self)

    #
    # A page that hosts a surface still drawn with today's verbs draws in
    # two phases: `hosted`, which its host runs BEFORE it begins the frame,
    # draws the page's ground round the hosted bodies and then each body in
    # its own verbs pass; the frame's pass (`paint`) then draws everything
    # else, bare, each legacy node a focus stop that draws nothing, its
    # overlays last. No verbs pass ever runs inside another: the desk
    # Editor's own order (its pane bodies, then its one verbs pass of
    # headers, dividers and menus).

    def ground_round(self, cv, th, content):
        """The page's ground where the hosted bodies leave it showing: the
        Editor's `panel`, never filled under a body (it draws its own)."""
        left = [tuple(self.ground if self.ground is not None else content)]
        for _n, _row, r in self.placed:
            out = []
            for piece in left:
                out += _outside(piece, r)
            left = out
        col = th["panel"]
        for x, y, w, h in left:
            if w > 0 and h > 0:
                cv.rect(x, y, w, h, col)

    def draw_hosted(self, cv, th, dt):
        """Each legacy body where the last placement put it, in reading
        order, each in its own verbs pass: a surface of its own, outside
        this page's frame (it may be a page of its own, which writes its
        state as its host syncs it)."""
        was = _NOW.app, _NOW.frame
        fk = self.focus_key
        try:
            for n, row, r in self.placed:
                focused = fk == n.key
                _NOW.app, _NOW.frame = self, False
                self.hosting = n
                n.l["draw"](cv, th, r[0], r[1], r[2], r[3], focused, dt)
                cv.clip()
                if "gen" in n.l:
                    _NOW.app = self
                    self.legacy_gens[n.key] = n.l["gen"]()
        finally:
            _NOW.app, _NOW.frame = was
            self.hosting = None

    def minimum(self):
        """The least area the outermost panes hold (w, h), gutters in, as
        the last placement found it."""
        return self.least

    # -- a row ----------------------------------------------------------------

    @staticmethod
    def _fit_cells(kids, rws, nat, row, room, gap, h):
        """A row of fixed cells wider than `room`: its model's cells (on a
        row other than the row's own) cut to one width, the widest first,
        never under the row's height `h`; those that still do not fit
        dropped from the end."""
        mine = [r is not row for r in rws]
        most = max([v for v, m in zip(nat, mine) if m] or [0])
        sep = gap * max(0, len(nat) - 1)
        if sum(nat) + sep <= room or not most:
            return
        lo, hi = min(h, most), most
        while lo < hi:
            cap = lo + (hi - lo + 1) // 2
            if sum(cap if m and v > cap else v for v, m in zip(nat, mine)) + sep <= room:
                lo = cap
            else:
                hi = cap - 1
        for i, m in enumerate(mine):
            if m and nat[i] > lo:
                nat[i] = lo
        total = sum(nat) + sep
        for i in range(len(nat) - 1, -1, -1):
            if total <= room:
                break
            if mine[i]:
                total -= nat[i] + (gap if len(nat) > 1 else 0)
                del kids[i], rws[i], nat[i], mine[i]

    @staticmethod
    def _share(nat, base, ws, i0, i1, room):
        """Cells i0..i1 their widths: a fixed one its own, the fills their
        basis and an even share of what `room` leaves, the last of them the
        remainder."""
        grow = sum(1 for v in nat[i0:i1] if v is None)
        rest = room - sum(v if v is not None else b for v, b in zip(nat[i0:i1], base[i0:i1]))
        each = rest // max(1, grow)
        last = max([i for i in range(i0, i1) if nat[i] is None] or [-1])
        for i in range(i0, i1):
            ws[i] = nat[i] if nat[i] is not None else \
                max(0, base[i] + each + (rest - each * grow if i == last else 0))

    # -- panes and their dividers --------------------------------------------

    def _size_of(self, k):
        """A child's held size now, in pixels: its state, else its start
        (desk units); None grows."""
        if k.l.get("grow") or "size" not in k.l:
            return None
        v = self.s._v[k.l["size"]]
        return self._start_of(k) * self.du if v is None else v

    def _start_of(self, k):
        """A child's start= in desk units: a literal, or its int state's
        value (the host writes it from the area and the cart)."""
        st = k.l["start"]
        return (self.s._v[st] or 0) if isinstance(st, str) else st

    def _mouse(self):
        if self.mouse is not None:
            return self.mouse
        p = self._surf.pointer() if self._surf is not None else None
        return bool(getattr(p, "hovers", False))

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
            self.size_gen += 1
            S._trace("state", nm, "%r -> %r   (%s)" % (old, sizes[j], why))

    def _unhold(self, kids, why):
        """Children `kids` back at their start= (their states absent)."""
        for k in kids:
            if "size" in k.l and not k.l.get("grow") and self.s._v[k.l["size"]] is not None:
                nm = k.l["size"]
                old = self.s._v[nm]
                self.s._v[nm] = None
                self.s._ver[nm] += 1
                self.size_gen += 1
                S._trace("state", nm, "%r -> None   (%s)" % (old, why))

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

    # -- pane focus -------------------------------------------------------------

    def reset_sizes(self):
        """Every divider back where its panes start (Ctrl+0)."""
        def walk(k):
            for c in k.kids:
                if c.kind in ("pane", "panes"):
                    self._unhold([c], "Ctrl+0")
                    walk(c)
        self._bind()
        if self.panes_root is not None:
            walk(self.panes_root)
        self._damage.all()

    def nudge(self, code):
        """Ctrl+Alt+arrows: the focused pane's right (left and right) or
        bottom (up and down) divider moves KEY_STEP units, neither pane
        beside it under its minimum."""
        self._bind()
        axis = 0 if code in (_LEFT, _RIGHT) else 1
        step = KEY_STEP * self.du * (-1 if code in (_LEFT, _UP) else 1)
        p = self._pane_named(self.panes_root, self.pane_cur)
        while p is not None and p.parent is not None and p.parent.kind == "panes":
            par = p.parent
            for sid, sp in self.splits.items():
                pn, kids, sizes = sp[:3]
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
        self._id(key, pair)
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

    @staticmethod
    def _a_note(p):
        """A popover of words alone (See why): nothing in it takes the keys."""
        return not any(k.kind in ("field", "rows", "link", "button", "menu") for k in p.kids)

    def pointer_down(self):
        """Whether the pointer is held: the surface's; a hosted page's host
        replaces it with its own."""
        p = self._surf.pointer() if self._surf is not None else None
        return bool(p is not None and p.down)

    # -- keys ------------------------------------------------------------------

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


# A lent row's cells (`Engine.lent`): their kinds as the rule numbers them,
# and their flags (native/moy_uitree/moy_uitree.h's UIT_LC_*).
LC_KINDS = {"tab": 0, "button": 1, "text": 2}
LC_ALONE, LC_CUR, LC_ON, LC_MARKED, LC_PRIMARY = 1, 2, 4, 8, 16


def lent_place(cells, x0, w, h, fs, pad):
    """The lent row's rule (`BarZone`'s): `cells` (kind, flags, the width
    named: the look's with its label, 0 where it has none) each, in a zone
    `x0` wide `w` at cell height `h`. (density, [(x, w, labelled)] a cell, w
    -1 past the room, the hairlines' x, the narrowest zone holding every cell
    as an icon). The densities, in turn: every label (2); icons with the
    current tab named and the buttons' words where every tab named in its
    place holds them (1); the same, the buttons icons; icons alone (0)."""
    Z = BarZone
    n = len(cells)
    w = max(0, w - Z.END * fs)
    end = n
    while end > 0 and cells[end - 1][0] == 1:
        end -= 1
    tablines = [i for i in range(1, n) if cells[i - 1][0] == 0 and cells[i][0] != 0]
    lines = tablines + ([end] if 0 < end < n and end not in tablines else [])
    text = any(c[0] == 2 for c in cells)

    def named(c, density, words):
        if not c[2]:
            return False
        if c[0] == 0:
            return density == 2 or (density == 1 and bool(c[1] & LC_CUR))
        return c[0] == 1 and (density == 2 or (density == 1 and words)) and not c[1] & LC_ALONE

    def width(c, density, words):
        if c[0] == 2:
            return 0
        if named(c, density, words):
            return c[2]
        return h + 2 * pad if c[0] == 0 and c[1] & LC_CUR else h

    for density, words in ((2, True), (1, True), (1, False), (0, False)):
        # the current tab's name goes before the gaps do: it holds at none
        lg = 0 if density == 1 else Z.LEAST * fs
        ws = [width(c, density, words) for c in cells]
        least = sum(ws) + lg * max(0, n - 1 - len(lines)) + Z.GROUP * fs * len(lines)
        more = 0
        if density == 1 and words:
            # the buttons keep their words only where every tab open holds them
            cur = [ws[i] for i, c in enumerate(cells) if c[0] == 0 and c[1] & LC_CUR]
            tabs = [c[2] for c in cells if c[0] == 0 and c[2]]
            more = max(0, max(tabs) - cur[0]) if cur and tabs else 0
        if least + more <= w or density == 0:
            break
    spare = max(0, w - least)
    if text:
        gap = lg
    else:
        gap = lg + min(Z.GAP * fs - lg, spare // max(1, n - 1 - len(lines)))
    out, hair = [], []
    x = x0
    for i, c in enumerate(cells):
        if i:
            if i in lines:
                x += Z.GROUP * fs
                if i == end and not text:
                    # the end run stands at the zone's right end
                    x = max(x, x0 + w - sum(ws[end:]) - gap * (n - end - 1))
                hair.append(x - (Z.GROUP * fs + fs) // 2)
            else:
                x += gap
        cw = ws[i]
        if c[0] == 2:
            cw = spare           # a line of text takes the room left
            spare = 0
        if x + cw > x0 + w:
            out.extend([(0, -1, 0)] * (n - i))
            break
        out.append((x, cw, 1 if c[0] == 2 or named(c, density, words) else 0))
        x += cw
    k = len(tablines)
    need = sum(width(c, 0, False) for c in cells) + Z.LEAST * fs * max(0, n - 1 - k) + \
        Z.GROUP * fs * k + Z.END * fs
    return density, out, hair, need


class Page(Engine):
    """A page on the Editor's kinds hosted by a surface still on today's
    verbs (`uitree_app.Page`'s bridge): the host begins the frame, routes
    the keys and the pointer, and `paint` draws the page into its rect."""

    def __init__(self, build, host, damage, files=None, close=None):
        Engine.__init__(self, S.decl_of(S.module_of(build)), build, host, None, damage, files)
        self.close_root = close


def page(build, host, damage, files=None, close=None):
    """A page on the Editor's kinds on the engine in use
    (`uitree_app.use_engine`): the C engine's `CEditorPage`, or the
    reference's `uitree_ref_editor.Page`."""
    return uitree_app._engine_class(True, True)(build, host, damage, files, close)


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

    def _row(self):
        """The shown lender's cells, the states synced first: [kind, key,
        value, label, icon, flags, action] a cell (`Engine.lent`), else None."""
        e = self.engine
        e._bind()
        if sum(e.s._ver.values()) != self._vers and self._vers >= 0:
            self.gen += 1               # a handler moved what the row reads since its last pass
        sync = getattr(self.host, "head_sync", None)
        if sync is not None:
            sync()                      # (what it moves, the host's own zone_gen moved for)
        cells = e.lent(self.site)
        self._vers = sum(e.s._ver.values())
        return cells

    def _spec(self, cells, h):
        """Each cell as the rule takes it: (kind, flags, its width named:
        the look's for a tab or a button with its label, else 0)."""
        look = self.look
        out = []
        for kind, _k, _v, label, icon, flags, _a in cells:
            named = 0
            if label and kind == "tab":
                named = look.tab_width(label, icon, h)
            elif label and kind == "button":
                named = look.word_width(label, icon, h)
            out.append((LC_KINDS[kind], flags, named))
        return out

    def _layout(self, rect):
        """[(rect, kind, key, value, label, icon, labelled, flags, action)],
        the hairlines' x and the density the row packed at (2 every label,
        1 the current tab's name, 0 icons), for the lent row in `rect`:
        the rule the engine's (`Engine.lent_place`), the widths the look's."""
        cells = self._row()
        if not cells:
            return [], [], 0
        x0, y, w, h = rect
        density, places, hair, _need = self.engine.lent_place(
            self._spec(cells, h), x0, w, h, self.look.zone_scale(), self.look.tab_pad())
        out = []
        for c, (x, cw, labelled) in zip(cells, places):
            if cw < 0:
                break
            out.append(((x, y, cw, h), c[0], c[1], c[2], c[3], c[4], bool(labelled), c[5], c[6]))
        return out, hair, density

    def draw_zone(self, cv, zone):
        cells, hair, density = self._layout(zone)
        look = self.look
        for x in hair:
            look.draw_line(cv, x, zone)
        for r, kind, _key, _v, label, icon, labelled, flags, _a in cells:
            if kind == "tab":
                look.draw_tab(cv, r, bool(flags & LC_CUR), label, icon, labelled, density == 2)
            elif kind == "text":
                look.draw_text(cv, r, label)
            elif labelled:
                look.draw_word(cv, r, label, icon, bool(flags & LC_PRIMARY), bool(flags & LC_MARKED))
            else:
                look.draw_icon(cv, r, icon, bool(flags & LC_ON), bool(flags & LC_PRIMARY),
                               bool(flags & LC_MARKED))

    def zone_targets(self, zone):
        return [c[0] for c in self._layout(zone)[0] if c[1] != "text"]

    def zone_need(self, h):
        """The narrowest zone at band height `h` whose row holds every cell
        as icons, the current tab widened by its pad, at the least gaps."""
        cells = self._row()
        if not cells:
            return 0
        return self.engine.lent_place(self._spec(cells, h), 0, 0, h, self.look.zone_scale(),
                                      self.look.tab_pad())[3]

    def box(self, key, value=None, zone=None):
        """Where the cell for `key` (a tabs' `value`) stands in `zone`, or None."""
        for c in self._layout(zone)[0]:
            if c[2] == key and (value is None or c[3] == value):
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
        self.activate(hit[1], hit[2], hit[3], hit[8])
        return True

    def activate(self, kind, key, value, act):
        """A cell taken (a tap, the bar's keys): a tab writes its value and
        runs the tabs' `on=` (the current tab again too); a button runs its
        action where it is on."""
        e = self.engine
        e._bind()
        self.zone_opened = False
        if kind == "tab":
            old = e.s._v[key]
            if old != value:
                e.s._v[key] = value
                e.s._ver[key] += 1
                S._trace("state", key, "%r -> %r   (from the bar)" % (old, value))
            self.zone_opened = True
            if act is not None:
                e.run_action(act, (), "tabs %r on the bar" % key)
        elif act is not None:
            was, _NOW.app = _NOW.app, e
            try:
                on = act.can(None)
            finally:
                _NOW.app = was
            if on:
                e.run_action(act, (), "button %r on the bar" % key)
        self._row()
