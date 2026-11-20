"""The replay of the Editor's kinds (uitree.py EXT-4) through today's `ui`
verbs: regions stacked down the page, rows, tabs, panes over `ui.split`
with its dividers, a pane's card and header, the text editor over
`ui.text_view` and the document's keys, a slot, a status line, and popovers
hung from a node's part.

What is placed here by arithmetic -- a row's widths, the caret's box, a
tab's x -- is the engine's in the shape; the replay computes it the way the
Code tab and the desk do today."""
from array import array

import ui as _v
import ui_draw as _ud
import uitree as S
from uitree import _NOW
import uitree_app

_SYN = {"python": "python", "lua": "lua", "json": "json", "markdown": "markdown", "plain": "plain"}
_FILL = ("switch", "panes", "pane", "text_editor", "slot", "panel", "case", "canvas")
_TEXT_PAD = 8          # moy_ui_api_int.h's TEXT_PAD: the view's text inset at the scale


# the find strip Back closes: the stand-in engine knows the Editor's names
FIND_STRIP = ("code__finding", "code__find_close")


class Engine(uitree_app.Engine):

    def __init__(self, *a, **k):
        uitree_app.Engine.__init__(self, *a, **k)
        self.anchors = {}      # "key.part" -> (x, y, w, h), as this frame placed it
        self.views = {}        # text editor key -> [rect, top drawn, cell, line h, x0]
        self.split_out = array("i", [0] * (4 * 16 + 4))
        self.dividers = {}     # panes node id -> the divider rects this frame drew
        self.edits_traced = None

    # -- the page: regions stacked, overlays last ---------------------------

    def _replay_page(self, n):
        w, h = self.content[2], self.content[3]
        self.classes = ("desk" if self.desk else "handheld", "wide" if w * 3 > h * 4 else "tall") + (
            ("symbols",) if _v.has_symbols() else ())
        self.lenders = {}
        S.LIVE = S.live_units(n, self.lenders)
        self.canvases = {}
        rect = _v.page(n.p["title"](None))
        prev, self.ids = self.ids, {}
        self.anchors = {}
        self.dividers = {}
        want = [(self.want_focus, None)] if self.want_focus is not None else []
        if self.fresh and prev:
            first = [i for i, (node, row) in prev.items() if "focus" in node.p and node.p["focus"](row)]
            if first and self.want_focus is None:
                self.want_focus = first[0]
                want = [(first[0], None)]
            self.fresh = False
        for key, _k in want:
            if key in prev:
                _v.focus(key)
        self.rect = rect
        self.late = []
        self._stack(n, None, rect)
        for k, row, r in self.late:
            self._draw(k, row, r)
        if self.want_focus is not None and self.want_focus in self.ids:
            _v.focus(self.want_focus)
        f = _v.focus()
        if f == self.want_focus or self.want_focus not in self.ids:
            self.want_focus = None      # it took (a popover closing hands focus back a frame late)
        self.focused = self.ids.get(f)
        self.focus_key = f or ""
        for k, lst in self.curs:
            self._cur_follow(k, lst)

    def _cur_follow(self, ck, lst):
        """A list's cur= follows focus onto its rows (uitree_app's rule)."""
        if self.focused is None:
            return
        node, row = self.focused
        if row is None or uitree_app._list_of(node) is not lst:
            return
        v = row._f[row._m.key]
        if self.s._v[ck] != v:
            old = self.s._v[ck]
            self.s._v[ck] = self._sync[ck] = v
            self.s._ver[ck] += 1
            S._trace("state", ck, "%r -> %r   (focus)" % (old, v))

    def _h_row(self):
        return 36 if self.desk else 22 * self.fs

    def _height(self, k):
        if k.kind in _FILL:
            return None
        if k.kind == "status":
            return 22 if self.desk else 14 * self.fs
        if k.kind == "tools" and (self._in_pane_head(k) or k.l.get("lend")):
            return 0
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
        for k, kh in zip(flow, hs):
            if kh == 0:
                continue
            kh = room // fills if kh is None else kh
            self._draw(k, row, (x, y, w, kh))
            y += kh
        for k in kids:
            if k.kind in ("popover", "dialog"):
                self.late.append((k, row, rect))     # over every pane, after the page

    def _draw(self, k, row, rect):
        getattr(self, "_r_" + k.kind)(k, row, rect)

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
        x, y, w, h = rect
        g = 6 if self.desk else 4
        self.cv.rect(x + g, y + g, w - 2 * g, h - 2 * g, self.th["panel"])
        ty = y + h // 2 - 4
        for k in n.kids:
            if k.kind == "text" and self._shown(k, row):
                t = self._t(k, "text", row)
                tw = _v.text_size(t)[0]
                _v.at(x + (w - tw) // 2 - 20, ty, tw + 40, 16)
                _v.label(t, {"ink": "ink_dim"})
                ty += 18

    # -- a row ----------------------------------------------------------------

    def _natural(self, k, row, h):
        if k.kind == "button":
            if "icon" in k.p and k.l.get("alone"):
                return h
            return _v.text_size(self._t(k, "label", row))[0] + 28
        if k.kind == "tabs":
            st = self.s._t[k.key]
            tab = self.decl.labels[k.l["labels"]]
            return sum(_v.text_size(tab[v])[0] + (28 if self.desk else 14 * self.fs)
                       for v in st.values)
        return None

    def _r_row(self, n, row, rect):
        x, y, w, h = rect
        kids = []
        for k in n.kids:                # a lent site: the shown lender's buttons, in its place
            if k.kind == "lent":
                t = self.lenders.get(k.key)
                kids += [b for b in t.kids if self._shown(b, row)] if t is not None else []
            elif self._shown(k, row):
                kids.append(k)
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
            r = (x, y + 2, kw, h - 4)
            if k.kind == "button":
                self._r_button(k, row, rect, at=r)
            else:
                _v.at(*r)
                self._draw(k, row, r)
            x += kw + gap

    def _r_tools(self, n, row, rect):
        if n.l.get("lend"):
            return                      # drawn at the frame's lent site
        self._r_row(n, row, rect)

    def _r_canvas(self, n, row, rect):
        _v.at(*rect)
        x, y, w, h, _st = _v.part(n.key, 0, 0, {"id": n.key})
        self.ids[n.key] = (n, None)
        self.canvases[n.key] = (n, (x, y, w, h))
        self.cv.clip(x, y, w, h)
        n.l["draw"](self.cv, x, y, w, h)
        self.cv.clip()

    def point(self, px, py, click):
        """A canvas with pointer= is handed down, drag and up in its own
        coordinates; the press that starts on it holds it until release."""
        p = self._surf.pointer()
        down = bool(p is not None and p.down)
        held = getattr(self, "held", None)
        if held is not None:
            n, r = held
            ev = S.Ev("drag" if down else "up", px - r[0], py - r[1], "touch")
            self.q.append((n.l["pointer"], (ev,), "pointer %s on canvas %r" % (ev.kind, n.key)))
            if not down:
                self.held = None
            self._damage.all()
            return True
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
        _v.at(*rect)
        _v.label(self._t(n, "text", row), {"ink": "ink_dim"})

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
                self.fire(n.l["on"], uitree_app.change_args(n.l["on"], vals[got], old), n, None)

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
                self.fire(n.l["changed"], uitree_app.change_args(n.l["changed"], text, before), n, None)
        if done and "on" in n.l:
            self.fire(n.l["on"], (), n, None)

    # -- panes and their dividers --------------------------------------------

    def _size_of(self, k):
        """A child's held size now: its state, else its start; None grows."""
        if k.l.get("grow") or "size" not in k.l:
            return None
        v = self.s._v[k.l["size"]]
        return k.l["start"] if v is None else v

    def _min_of(self, k, axis):
        if k.kind == "pane":
            return k.l.get("min", (64, 48))[axis]
        if k.kind != "panes":
            return 0
        kids = [c for c in k.kids if self._shown(c, None)]
        m = [self._min_of(c, axis) for c in kids]
        along = (k.l["dir"] if "dir" in k.l else k.p["dir"](None)) == ("row" if axis == 0 else "col")
        return sum(m) + 6 * (len(m) - 1) if along else max(m or [0])

    def _r_panes(self, n, row, rect):
        d = n.p["dir"](row)
        axis = 0 if d == "row" else 1
        kids = [k for k in n.kids if self._shown(k, row)]
        gap = 6
        span = rect[2 + axis] - gap * (len(kids) - 1)
        held = [self._size_of(k) for k in kids]
        mins = [self._min_of(k, axis) for k in kids]
        if span < sum(mins):
            # narrower than the workspace's least: the growing pane alone
            # (today's EditorDesk.alone), the rest reached by the keys
            alone = next((k for k, v in zip(kids, held) if v is None), kids[0])
            self._draw(alone, row, rect)
            return
        # a held size is the user's wish; the area wins: held sizes give way
        # down to their minimums so the growing child keeps its own
        grow_min = sum(m for m, v in zip(mins, held) if v is None)
        over = sum(v for v in held if v is not None) + grow_min - span
        for j in range(len(held)):
            if over > 0 and held[j] is not None:
                give = min(over, held[j] - mins[j])
                held[j] -= give
                over -= give
        free = span - sum(v for v in held if v is not None)
        sizes = [v if v is not None else max(mins[i], free) for i, v in enumerate(held)]
        sid = "split:" + "/".join(k.key for k in kids)
        _v.at(*rect)
        o = {"drag": True, "gap": gap, "mins": mins, "line": 2, "slop": 0,
             "handle": (6, 36), "hit": (16, 48)}
        out = self.split_out
        _v.split(sid, "x" if axis == 0 else "y", sizes, o, out)
        nk = len(kids)
        i, bits = out[4 * nk], out[4 * nk + 3]
        divs = []
        for j in range(nk - 1):
            a = out[4 * j: 4 * j + 4]
            divs.append((a[0] + a[2], a[1], gap, a[3]) if axis == 0 else (a[0], a[1] + a[3], a[2], gap))
        self.dividers[sid] = divs
        if i >= 0 and bits & _v.LET_GO and bits & _v.LIVE:
            for j in (i, i + 1):
                k = kids[j]
                if held[j] is not None and sizes[j] != held[j]:
                    nm = k.l["size"]
                    old = self.s._v[nm]
                    self.s._v[nm] = sizes[j]
                    self.s._ver[nm] += 1
                    S._trace("state", nm, "%r -> %r   (divider %d of %r let go)" % (old, sizes[j], i, sid))
            self._damage.all()
        for j, k in enumerate(kids):
            r = tuple(out[4 * j: 4 * j + 4])
            self._draw(k, row, r)

    def _r_pane(self, n, row, rect):
        x, y, w, h = rect
        cv, th = self.cv, self.th
        hh = 32
        # the header only: the body's ground is the page's, which the text
        # view's kept rows sit on (a fill under them each frame wipes them)
        cv.rect(x, y, w, hh, th["panel"])
        cv.rect(x, y + hh, w, 1, th["border"])
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
        body = [k for k in n.kids if k.kind != "tools"]
        tmp = S.Node("case", n.key + ":body", n.where)
        tmp.kids, tmp.parent = body, n
        self._stack(tmp, row, (x + 4, y + hh + 2, w - 8, h - hh - 6))

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

    def _r_text_editor(self, n, row, rect):
        key = n.key
        doc = self.s._v[key]
        ed = doc.ed
        fs = self.fs
        _fid, fw, line, _asc = _ud.font("mono")
        cell = fw * fs
        lh = (line + 3) * fs if not self.desk else line + 4
        x, y, w, h = rect
        v = self.views.get(key)
        lines = v[5] if v is not None and v[0] == rect else max(1, h // lh)
        gutter = bool(n.p["gutter"](row)) if "gutter" in n.p else False
        gcols = (len(str(len(ed.lines))) + 2) if gutter else 0
        x0 = x + _TEXT_PAD * fs + gcols * cell
        ed.set_view_size(max(1, (w - (x0 - x)) // cell), lines)
        q = ""
        if "find" in n.l and n.p.get("find_on", lambda r: True)(row):
            q = self.s._v[n.l["find"]]
        doc.find_q = q
        err = None
        if "marks" in n.l:
            for m in self.s._v[n.l["marks"]]:
                if m.kind == "error":
                    err = (m.line - 1, m.words)
                    break
        o = {"gutter": gutter, "syntax": _SYN[doc.lang], "caret": (ed.row, ed.col), "mark": True,
             "left": ed.left}
        if ed.sel is not None:
            o["sel"] = ed.selection_bounds()
        if q:
            o["find"] = (q, True)
        if err is not None:
            o["err"] = err
        if v is None or v[1] != ed.top:
            o["top"] = ed.top
        _v.at(x, y, w, h)
        got = _v.text_view(key, ed.lines, o)
        ed.top = got[0]
        if got[1] > 0:
            lh = h // got[1]
        self.views[key] = [rect, ed.top, cell, lh, x0, max(1, got[1])]
        self.ids[key] = (n, None)
        cy = y + (ed.row - ed.top) * lh + (lh // 2 if self.desk else 0)
        cx = x0 + (ed.col - ed.left) * cell
        self.anchors[key + ".caret"] = (cx, cy, cell, lh)
        p = len(ed.word_prefix())
        self.anchors[key + ".word"] = (cx - p * cell, cy, max(cell, p * cell), lh)
        self.anchors[key] = rect

    # -- popovers hang from a node's part -------------------------------------

    def _r_popover(self, n, row, rect):
        w = n.p["w"](row) if "w" in n.p else 200
        a = self.anchors.get(n.p["anchor"](row)) if "anchor" in n.p else None
        if a is not None:
            _v.at(*a)
        if not _v.popover(n.key, w) and "close" in n.l:
            self.fire(n.l["close"], self.args_for(n.l["close"]), n, None)
        for k in n.kids:
            if self._shown(k, row):
                if k.kind == "field":
                    self._r_field(k, row, self.rect)
                else:
                    self._draw(k, row, rect)
        _v.popover()

    def _r_rows(self, n, row, rect):
        uitree_app.Engine._r_rows(self, n, row, rect)

    def _r_link(self, n, row, rect):
        uitree_app.Engine._r_link(self, n, row, rect)

    # -- keys ------------------------------------------------------------------

    def shown_popovers(self):
        """The popovers in the shown tree: a switch's current case only."""
        out = []

        def walk(n):
            for k in n.kids:
                if not uitree_app._shown(k, None):
                    continue
                if k.kind == "popover":
                    out.append(k)
                elif k.kind == "switch":
                    v = self.s._v[k.l["name"]]
                    for c in k.kids:
                        if c.l["v"] in (v, "_"):
                            walk(c)
                            break
                elif k.kind not in ("rows", "cards"):
                    walk(k)
        if self.root is not None:
            walk(self.root)
        return out

    def popover_shown(self):
        return bool(self.shown_popovers())

    def editor_keys(self):
        """The text editor that takes typed keys: the shown one, unless a
        popover is open or focus is on a field or a row of its own."""
        if self.root is None or self.popover_shown():
            return None
        f = self.focused
        if f is not None and f[0].kind in ("field", "link") :
            return None
        keys = [k for k in self.views if self.views[k] is not None]
        return keys[0] if keys else None

    def keys(self, i):
        ch = getattr(i, "typed", 0) & 0xFF
        answer = [a for a in self.actions.values() if ch and any(S._chord_byte(k) == ch for k in a.keys)]
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
        ek = self.editor_keys()
        if ek is not None:
            doc = self.s._v[ek]
            moved = False
            for name, dy, dx in (("up", -1, 0), ("down", 1, 0), ("left", 0, -1), ("right", 0, 1)):
                if i.pressed(name):
                    doc.move(dy, dx, True if getattr(i, "mods", 0) & 1 else None)
                    moved = True
            if ch and (ch in (8, 9, 13, 127) or 32 <= ch < 127):
                before = doc.summary()
                if doc.key(ch):
                    self.s._ver[ek] += 1
                    tag = "(typed in text_editor %r)" % ek
                    if self.trace and self.trace[-1].endswith(tag):
                        first = self.trace.pop().split(" -> ")[0].split("state   %s " % ek)[-1].strip()
                        before = first
                    was, _NOW.app = _NOW.app, self
                    S._trace("state", ek, "%s -> %s   %s" % (before, doc.summary(), tag))
                    _NOW.app = was
                moved = True
            if moved:
                self._damage.all()
                return True
        return uitree_app.Engine.keys(self, i)

    def back(self):
        for k in self.shown_popovers():
            if "close" in k.l:
                self.q.append((k.l["close"], self.args_for(k.l["close"]), "back on popover %r" % k.key))
                self._damage.all()
                return True
        if self.s._v.get(FIND_STRIP[0]) and not self.popover_shown():
            a = self.actions.get(FIND_STRIP[1])
            if a is not None:
                self.q.append((a, (), "back on the find strip"))
                self._damage.all()
                return True
        return uitree_app.Engine.back(self)

    def has_page(self):
        return uitree_app.Engine.has_page(self) or self.popover_shown() or bool(self.s._v.get(FIND_STRIP[0]))

    # -- for the scripts --------------------------------------------------------

    def divider_at(self, nth=0, sid=None):
        """The centre of a divider this frame drew, in the app's canvas."""
        items = [(k, v) for k, v in self.dividers.items() if sid is None or k == sid]
        allv = [r for _k, v in items for r in v]
        r = allv[nth]
        return r[0] + r[2] // 2, r[1] + r[3] // 2
