"""The UI tree's replay: a built tree drawn each frame through the
toolkit's `ui` verbs, call for call as a hand-written page makes them, so a
page of standard rows draws today's pixels. No app code runs here but a
canvas's draw (the escape hatch); handlers are queued and run after the
frame, as one commit.

`Engine` replays into a frame its host began. `App` is a registered app
whose screen is a build (`app_layer` makes the class a manifest names);
`Page` is a build hosted by a surface still drawn with today's verbs."""
import re

import ui as _v
from layers import AppLayer
from layout_base import bar_rows, content_of, metric_class, DESK

import uitree as S
from uitree import _NOW

ID_MAX = 31            # bytes of a node id the host engine keeps (seen: a longer focus id comes back cut)
_GAP = {"s": (6, 10), "m": (8, 16), "l": (16, 24), "0": (0, 0)}


def _shown(n, row):
    g = n.p.get("shown")
    return g is None or g(row)


def _list_of(node):
    while node is not None and node.kind not in ("rows", "cards", "table"):
        node = node.parent
    return node


def change_args(act, new, old):
    """What a two-way control's hook is handed: the new value and the old
    when it takes=change."""
    return (new, old) if act.takes == "change" else ()


class Engine(S.Builder):
    """One app's built tree replayed into today's verbs, inside a frame its
    host has begun (`host.begin`), and the commits of its handlers."""

    close_root = None       # what ui.pop() on the first page does: a hosted page's host closes it

    def __init__(self, decl, build, host, surface, damage, files=None):
        self.decl = decl
        self._ui, self._surf, self._damage = host, surface, damage
        self.init_builder(decl.state, build)
        self.q = []
        self.ids = {}
        self.dups = set()
        self._sync = {}
        self.curs = []
        self.fresh = False
        self.focused = None
        self.drawn = {}
        self.actions = decl.actions
        self.files = files
        self.pics = None                # the picture engine, made at the first frame over its canvas
        self.calls = 0                  # handler runs this frame: actions, hooks, timers, posts, work steps
        self.frame_calls = []
        self.work_run = {}              # work name -> [first frame, frames, steps, ms, over, wrote]
        self.frame_no = 0
        self.pic_wh = {}

    # -- the engine's counted runs (G12) ------------------------------------

    def run(self, fn, *args, action=None):
        if action != "setup":
            self.calls += 1
        return S.Builder.run(self, fn, *args, action=action)

    @property
    def idle_owed(self):
        return self.pics is not None and self.pics.idle_owed

    def idle_step(self):
        """A frame with nothing to paint: one owed picture decoded, the card
        showing it from the next frame."""
        if self.pics is not None and self.pics.idle_step():
            self._damage.all()

    def _posts(self):
        q, self.decl.posts[:] = list(self.decl.posts), []
        tk = self.s.__dict__.get("_tickets", {})
        for act, arg, ticket in q:
            if ticket is not None and tk.get(ticket.load) != ticket.n:
                was, S._NOW.app = S._NOW.app, self
                S._trace("post", act.name, "dropped: %r is stale (%s is at #%d)" % (
                    ticket, ticket.load, tk.get(ticket.load, 0)))
                S._NOW.app = was
                continue
            self.run_action(act, (arg,) if act.takes == "arg" else (), "post")

    def _works(self):
        """Each work whose due= holds: one step a frame, after the frame's
        input and paint; a step past its budget is counted. One trace line
        per run of frames, not per step."""
        import time
        for w in self.decl.works.values():
            if w.unit is not None and S.LIVE is not None and w.unit not in S.LIVE:
                continue
            S._NOW.app = self
            try:
                due = w.due(None)
            finally:
                S._NOW.app = None
            run = self.work_run.get(w.name)
            if not due:
                if run is not None:
                    was, S._NOW.app = S._NOW.app, self
                    S._trace("work", w.name, "ended: %d steps over %d frames, %.1f ms, %d over budget; "
                             "wrote %s" % (run[2], run[1], run[3], run[4], ", ".join(sorted(run[5])) or "nothing"))
                    S._NOW.app = was
                    del self.work_run[w.name]
                continue
            if run is None:
                run = self.work_run[w.name] = [self.frame_no, 0, 0, 0.0, 0, set()]
                was, S._NOW.app = S._NOW.app, self
                S._trace("work", w.name, "due (%s), budget %d ms a frame" % (w.due_src, w.budget))
                S._NOW.app = was
            run[1] += 1
            t0 = time.perf_counter()
            vers = dict(self.s._ver)
            S._TRACE_OFF[0] = True          # a step's writes are counted in its run's line
            try:
                self.run(w.fn, action="work " + w.name)
                S.commit(self)
            finally:
                S._TRACE_OFF[0] = False
            run[2] += 1
            ms = (time.perf_counter() - t0) * 1000
            if ms > w.budget:
                run[4] += 1
            run[3] += ms
            run[5] |= {k for k, v in self.s._ver.items() if vers.get(k) != v}

    def query(self, name, *a):
        if name == "colors":
            return self.th
        return getattr(_v, name)(*a)

    def open(self, arg=None, hook="opened"):
        """The app opens: its first page, and its `opened` hook (or the hook
        a hosted page names) run once, handed the launch argument when it
        takes=\"arg\"."""
        self.pages = self.pages[:1]
        self.openers = []
        self._ui.reset()
        self.root = None
        self.want_focus = None          # a fresh visit: no focus, cur or row order carried over
        self._sync, self.ids, self.focused, self.drawn = {}, {}, None, {}
        self.__dict__.pop("_orders", None)
        self._lists_now = self._lists_before = set()
        hook = self.actions.get(hook)
        if hook is not None and hook.hook:
            self.run(hook.fn, *((arg,) if hook.takes == "arg" else ()), action="opened")
        S.commit(self)

    def fire(self, act, args, n, row):
        self.fired_id = n.key if row is None else "%s/%s" % (row._f[row._m.key], n.key)
        src = "%s %r" % (n.kind, n.key if row is None else "%s/%s" % (row._f[row._m.key], n.key))
        title = self.root.p["title"](None) if self.root else ""
        self.q.append((act, args, "%s on %r" % (src, title)))

    # -- the frame --------------------------------------------------------

    def paint(self, dt, cv, th, content, desk):
        """One frame: posts, timers, the page replayed into `content` of a
        frame the host began, the queued handlers as one commit, work."""
        self.frame_no += 1
        self.calls = 0
        self._posts()
        self.clock += int(dt * 1000)
        for t in [t for t in self.timers if t[0] <= self.clock]:
            self.timers.remove(t)
            self.run_action(t[1], (), "timer")
        self.cv, self.th = cv, th
        if self.pics is None:
            import uitree_pics
            self.pics = uitree_pics.PicEngine(self, self.files, getattr(cv, "swapped565", False),
                                              uitree_pics.budget_for(cv.w, cv.h))
        fs = max(1, getattr(cv, "font_scale", 1))
        self.content = content
        if self.root is None:
            self._build()
            self.curs = []
            self._walk(self.root)
            self.fresh = True
        self.pics.begin_frame()
        _NOW.app, _NOW.frame = self, True
        try:
            self.desk = desk
            self.fs = fs
            self._replay_page(self.root)
        finally:
            _NOW.app, _NOW.frame = None, False
        q, self.q = self.q, []
        for act, args, src in q:
            self.run_action(act, args, src)
        S.commit(self)              # a two-way control's write with no hook moves a derive too
        self._works()
        self.frame_calls.append(self.calls)
        if q or self.timers or self.work_run or self.decl.posts:
            self._damage.all()

    def calls_text(self):
        """The handler runs of each frame since the last ask, run-length."""
        fc, self.frame_calls = self.frame_calls, []
        out, last, n = [], None, 0
        for c in fc + [None]:
            if c == last:
                n += 1
                continue
            if last is not None:
                out.append("%d x%d" % (last, n))
            last, n = c, 1
        return "calls a frame over %d frames: %s" % (len(fc), ", ".join(out) or "-")

    def _walk(self, n):
        if n.kind in ("rows", "cards", "table") and n.src.get("cur"):
            self.curs.append((n.src["cur"], n))
        for k in n.kids:
            self._walk(k)

    def _gap(self, name):
        g = _GAP.get(str(name), (0, 0))
        return g[1] if self.desk else g[0] * self.fs

    def _replay_page(self, n):
        w, h = self.content[2], self.content[3]
        self.classes = ("desk" if self.desk else "handheld", "wide" if w * 3 > h * 4 else "tall") + (
            ("symbols",) if _v.has_symbols() else ())
        S.LIVE = S.live_units(n)
        shown = [k for k in n.kids if _shown(k, None)]
        if any(k.kind == "foot" for k in shown) or self._foot_in_case(n):
            self.foot_r, body = _v.cut_bottom(self.content, 44 if self.desk else 22 * self.fs)
            _v.at(*body)
        rect = _v.page(n.p["title"](None))
        hint = self._hints()
        placed = any(k.kind in ("split", "grid", "foot") for k in shown)
        if not placed:
            _v.hints(hint)
        elif not self.desk and _v.hints_width(hint) > 0:
            band, rect = _v.cut_bottom(rect, 20 * self.fs)
            _v.at(*band)
            _v.hints(hint)
        prev, self.ids = self.ids, {}
        moved = False
        want = [(self.want_focus, None)] if self.want_focus is not None else []
        if self.fresh and prev:
            want += [(i, None) for i, (node, row) in prev.items()
                     if "focus" in node.p and node.p["focus"](row)][:1]
            self.fresh = False
        back = getattr(self, "_lists_now", set()) - getattr(self, "_lists_before", set())
        lists = {}
        for k, lst in self.curs:
            if self._sync.get(k, self) != self.s._v[k] or (lst.key in back and self.s._v[k]):
                want.append((self.s._v[k], k))      # a list shown again: focus on its cur
                lists[k] = lst
        for key, k in want:
            if k is None and key == self.want_focus and key in self.dups:
                self._focus_named(prev)
            fid = key if k is None and key in prev else self._id_in(prev, key)
            if not fid and k is not None and key != "":
                fid = self._row_stop_id(lists[k], key)  # a row not drawn yet: its id, so focus lands this frame
            if fid:
                _v.focus(fid)
                moved = True
                if k is not None:
                    self._sync[k] = key
                elif key == self.want_focus:
                    self.want_focus = None
        self.rect = rect
        self.dups = set()
        self._lists_before = getattr(self, "_lists_now", set())
        self._lists_now = set()
        self._kids(n, None, rect)
        f = _v.focus()
        self.focused = self.ids.get(f)
        self.focus_key = f or ""
        if self.focused is not None and not moved:
            node, row = self.focused
            lst = _list_of(node)
            ck = lst.src.get("cur") if lst is not None else None
            if ck:
                v = "" if row is None else row._f[row._m.key]
                if self.s._v[ck] != v:
                    self.cur_moved(lst, v, "focus")

    def _row_stop_id(self, lst, rowkey):
        """The id the first focus stop of row `rowkey` of `lst` will draw under."""
        if not any(r._f[r._m.key] == rowkey for r in self.s._v[lst.l["model"]]):
            return None
        for k in lst.kids:
            if k.kind in ("link", "button", "toggle", "slider", "field", "item", "card"):
                return ("%s/%s" % (rowkey, k.key))[:ID_MAX]
        return None

    def _focus_named(self, prev):
        """G15: a handler's ui.focus(KEY) names a node; through a fragment used
        twice, it resolves among the shown instances, and two shown is F01."""
        w = self.want_focus
        if w is None:
            return
        if w in self.dups:
            self.want_focus = None
            raise S.UIError("F01", "ui.focus(%r): two shown nodes are keyed %r" % (
                re.sub(r"\b(\w+?)__(\w+)", r"\1.\2", w), w), where=("(run)", 0),
                fix="give the two uses of the fragment different parents' keys and name the parent: "
                    "ui.focus(\"panel/key\"), or show one at a time")

    def settle(self):
        for _ck, lst in self.curs:
            self._after_removal(lst, [r._f[r._m.key] for r in self.s._v[lst.l["model"]]])

    def _after_removal(self, lst, keys):
        """G15's removal rule: the current row gone, the next row in the old
        order that is still there, else the one before it, else none."""
        ck = lst.src.get("cur")
        orders = self.__dict__.setdefault("_orders", {})
        old = orders.get(lst.key)
        orders[lst.key] = keys
        if not ck or old is None:
            return None
        cur = self.s._v[ck]
        if cur in keys or cur not in old:
            return None
        i = old.index(cur)
        nxt = next((k for k in old[i + 1:] if k in keys), None)
        if nxt is None:
            nxt = next((k for k in reversed(old[:i]) if k in keys), "")
        self.cur_moved(lst, nxt, "removal of %r" % cur)
        self._sync[ck] = None           # focus follows cur onto that row
        return nxt

    def _foot_in_case(self, n):
        """A foot in the shown case of a switch at the page's root."""
        for k in n.kids:
            if k.kind == "switch" and _shown(k, None):
                v = self.s._v[k.l["name"]]
                case = next((c for c in k.kids if c.l["v"] in (v, "_")), None)
                if case is not None and any(c.kind == "foot" and _shown(c, None) for c in case.kids):
                    return True
        return False

    def _r_tabs(self, n, row, rect):
        """An enum's tab strip across the page's top: a band cut from the page."""
        import ui_draw as _ud
        st = self.s._t[n.key]
        tab = self.decl.labels[n.l["labels"]]
        vals = list(st.values)
        bh = _ud.metrics("value_row")[17 if self.desk else 16] * self.fs
        pad = 8 if self.desk else 0
        band, self.rect = _v.cut_top(self.rect, bh + 2 * pad)
        if pad:
            band = _v.inset(band, 2 * pad, pad)
        _v.at(*band)
        self.ids[n.key] = (n, None)
        got = _v.tabs("", [tab[v] for v in vals], vals.index(self.s._v[n.key]), {"id": n.key})
        _v.at(*self.rect)
        if vals[got] != self.s._v[n.key]:
            old = self.s._v[n.key]
            self.s._v[n.key] = vals[got]
            self.s._ver[n.key] += 1
            S._trace("state", n.key, "%r -> %r   (from tabs %r)" % (old, vals[got], n.key))

    def cur_moved(self, lst, v, why):
        """The engine moves a list's cur= (focus, a removal): traced, and the
        list's changed= hook is handed the row now current."""
        ck = lst.src["cur"]
        old = self.s._v[ck]
        self.s._v[ck] = self._sync[ck] = v
        self.s._ver[ck] += 1
        was, _NOW.app = _NOW.app, self
        S._trace("state", ck, "%r -> %r   (%s)" % (old, v, why))
        _NOW.app = was
        if "changed" in lst.l:
            r = next((r for r in self.s._v[lst.l["model"]] if r._f[r._m.key] == v), None)
            if r is not None:
                self.q.append((lst.l["changed"], (r,), "%s %r cur moved" % (lst.kind, lst.key)))

    def _id_in(self, ids, want):
        for i, (node, row) in ids.items():
            if (row is not None and row._f[row._m.key] == want) or (row is None and node.key == want):
                return i
        return None

    def _hints(self):
        f = self.focused
        d = self.dialog_shown()
        if d:
            b = d.l["buttons"]
            return ("confirm", b[-1].hint, "back", b[0].hint)
        out = []
        if f is not None:
            node, row = f
            act = node.act if node.kind != "cards" else node.l.get("new")
            h = act.hint if act is not None and act.can() else None
            if node.kind in ("toggle", "cycle", "slider"):
                h = self._t(node, "hint", row) if "hint" in node.p else "Change"
            if node.kind == "table" and "hint" in node.p:
                h = self._t(node, "hint", None)
            if h:
                out += ["confirm", h]
        for a in self.actions.values():
            if a.key and a.key.isalpha() and a.can():
                out += [a.key.upper(), a.hint]
        return tuple(out + ["back", "Back"])

    def dialog_shown(self):
        for k in self.root.kids if self.root else ():
            if k.kind == "dialog" and _shown(k, None):
                return k
        return None

    def _kids(self, n, row, rect):
        for k in n.kids:
            if _shown(k, row) and (k.act is None or k.act.can(row) or k.act.off == "grey"):
                getattr(self, "_r_" + k.kind)(k, row, rect)

    def _t(self, n, k, row, default=""):
        """A text prop's value now; a change after the first frame is traced."""
        g = n.p.get(k)
        if g is None:
            return n.act.label if (k == "label" and n.act is not None) else default
        v = g(row)
        src = n.src[k]
        if "{" in src:
            nid = (id(n), None if row is None else row._f[row._m.key], k)
            old = self.drawn.get(nid)
            if old is not None and old != v:
                S._trace("drew", n.kind, "%s%r -> %r" % (
                    "" if row is None else "'%s/%s' " % (nid[1], n.key), src, v))
            self.drawn[nid] = v
        return v

    def _o(self, n, row, **o):
        nid = n.key if row is None else "%s/%s" % (row._f[row._m.key], n.key)
        nid = nid[:ID_MAX]              # the engine keeps this much of an id: past it, two ids may meet
        if nid in self.ids and self.ids[nid][0] is not n:
            self.dups.add(nid)          # two shown nodes, one id: a fragment used twice
        self.ids[nid] = (n, row)
        o["id"] = nid
        if "disabled" in n.p:
            o["disabled"] = bool(n.p["disabled"](row))
        if n.act is not None and n.act.off == "grey" and not n.act.can(row):
            o["disabled"] = True
        lst = _list_of(n)
        if row is not None and lst is not None and lst.kind == "rows" and lst.src.get("current"):
            o["current"] = row._f[row._m.key] == self.s._v[lst.src["current"]]
        if "signal" in n.p:
            o["signal"] = n.p["signal"](row)
        if "icon" in n.p:
            _v.icon(n.p["icon"](row))
        return o

    def _r_scope(self, n, row, rect):
        if any(self.s._ver[d] != v for d, v in n.aux.items()):
            S._trace("rebuilt", n.key, "scope (%s changed)" % ", ".join(
                d for d, v in n.aux.items() if self.s._ver[d] != v))
            _NOW.frame = False
            self.stack, self.ctx = [], S.Ctx()
            try:
                self.build_scope(n)
            finally:
                _NOW.frame, _NOW.app = True, self
            self.curs = []
            self._walk(self.root)
        self._kids(n, row, rect)

    def _r_switch(self, n, row, rect):
        v = self.s._v[n.l["name"]]
        case = next((c for c in n.kids if c.l["v"] == v), None) or next(
            (c for c in n.kids if c.l["v"] == "_"), None)
        if case is not None:
            self._kids(case, row, rect)

    def _r_group(self, n, row, rect):
        _v.group(self._t(n, "caption", row))
        self._kids(n, row, rect)
        _v.group()

    def _r_label(self, n, row, rect):
        tone = n.l.get("tone")
        text = self._t(n, "text", row)
        o = {"ink": tone} if tone else None
        if n.l.get("wrap") == "char":   # lines of what fits a group's card, cut anywhere
            import ui_draw as _ud
            per = max(8, (self.rect[2] - 32) // max(1, _ud.font("ui")[1] * self.fs))
            for at in range(0, len(text), per):
                _v.label(text[at:at + per], o)
            return
        _v.label(text, o)

    def _r_info(self, n, row, rect):
        _v.info(self._t(n, "label", row), self._t(n, "value", row), self._o(n, row))

    def _r_link(self, n, row, rect):
        if _v.link(self._t(n, "label", row), self._t(n, "value", row), self._o(n, row)):
            self._activate(n, row)

    def _activate(self, n, row):
        if n.act.can(row):
            self.fire(n.act, self.args_for(n.act, row, n.l.get("arg")), n, row)

    def _r_button(self, n, row, rect, at=None):
        if at:
            _v.at(*at)
        text = self._t(n, "label", row)
        o = self._o(n, row)
        if "icon" in n.p and n.l.get("alone"):
            text = ""
        if _v.button(text, n.l.get("kind", "normal"), o):
            self._activate(n, row)

    def _two_way(self, n, new, cur):
        if new != cur:
            setattr_ok = S._check(self.s._t[n.key], new, "s." + n.key)
            old = self.s._v[n.key]
            self.s._v[n.key] = setattr_ok
            self.s._ver[n.key] += 1
            S._trace("state", n.key, "%r -> %r   (from %s %r)" % (old, new, n.kind, n.key))
            if "on" in n.l:
                self.fire(n.l["on"], change_args(n.l["on"], new, old), n, None)

    def _row_write(self, n, row, new):
        """A two-way control over `.field`: the engine writes that field of
        that row, in place, and hands the row as it now is to on=."""
        f = n.l["field"]
        old = row._f[f]
        if new == old:
            return
        m = n.parent
        while m.kind not in ("rows", "cards", "table"):
            m = m.parent
        name = m.l["model"]
        key = row._f[row._m.key]
        d = dict(row._f)
        d[f] = S._check(row._m.fields[f], new, "%s.%s" % (name, f))
        nr = S.Row(row._m, d)
        self.s._v[name] = tuple(nr if r is row else r for r in self.s._v[name])
        self.s._ver[name] += 1
        for i, (node, r) in list(self.ids.items()):
            if r is row:                # the frame's nodes on that row see it as it now is
                self.ids[i] = (node, nr)
        was, _NOW.app = _NOW.app, self
        S._trace("state", name, "%r: %s %r -> %r   (from %s .%s)" % (key, f, old, new, n.kind, f))
        _NOW.app = was
        if "on" in n.l:
            self.fire(n.l["on"], (nr,), n, nr)

    def _r_toggle(self, n, row, rect):
        if "field" in n.l:
            cur = row._f[n.l["field"]]
            self._row_write(n, row, _v.toggle(self._t(n, "label", row), cur, self._o(n, row)))
            return
        cur = self.s._v[n.key]
        self._two_way(n, _v.toggle(self._t(n, "label", row), cur, self._o(n, row)), cur)

    def _r_slider(self, n, row, rect):
        lo, hi = n.p["lo"](row), n.p["hi"](row)
        if "field" in n.l:
            cur = row._f[n.l["field"]]
            self._row_write(n, row, _v.slider(self._t(n, "label", row), cur, lo, hi, self._o(n, row)))
            return
        cur = self.s._v[n.key]
        self._two_way(n, _v.slider(self._t(n, "label", row), cur, lo, hi, self._o(n, row)), cur)

    def _r_field(self, n, row, rect):
        k = n.key
        o = self._o(n, row)
        if n.l.get("secret"):
            o["secret"] = True
        text, done = _v.field(self._t(n, "label", row), self.s._v[k], n.l.get("max", 32), o)
        if text != self.s._v[k]:
            old = self.s._v[k]
            self.s._v[k] = text
            self.s._ver[k] += 1
            if n.l.get("secret"):
                old, text = "*" * len(old), "*" * len(text)
            tag = "(typed in field %r)" % k
            if self.trace and self.trace[-1].endswith(tag):     # one line per run of typing
                old = self.trace.pop().split("  state   ")[1].split(" -> ")[0][9:]
                S._trace("state", k, "%s -> %r   %s" % (old, text, tag))
            else:
                S._trace("state", k, "%r -> %r   %s" % (old, text, tag))
            if "changed" in n.l:
                self.fire(n.l["changed"], change_args(n.l["changed"], text, old), n, None)
        if done and "on" in n.l:
            self.fire(n.l["on"], (), n, None)

    def _r_scroll(self, n, row, rect):
        _v.scroll(n.key)
        self._kids(n, row, rect)
        _v.scroll()

    def _r_rows(self, n, row, rect):
        model = self.s._v[n.l["model"]]
        self._after_removal(n, [r._f[r._m.key] for r in model])
        if n.l.get("filter"):
            q = self.s._v[n.l["filter"]].lower()
            key = self.s._t[n.l["model"]].key
            model = [r for r in model if q in r._f[key].lower()]
        empty = self._t(n, "empty", None) if not model else ""
        if empty:
            _v.label(empty)
        cap = None
        for r in model:
            if "group" in n.l:
                c = n.l["group"](r)
                if c != cap:
                    if cap is not None:
                        _v.group()
                    _v.group(c)
                    cap = c
            self._kids(n, r, rect)
        if cap is not None:
            _v.group()

    def _r_table(self, n, row, rect):
        """`table MODEL cols=[...]`: one `col` child per column, each a
        template over the row; cur= follows the table's current row."""
        model = list(self.s._v[n.l["model"]])
        cells = [tuple(self._t(c, "text", r) for c in n.kids if c.kind == "col") for r in model]
        ck = n.src.get("cur")
        keys = [r._f[r._m.key] for r in model]
        self._after_removal(n, keys)
        at = keys.index(self.s._v[ck]) if ck and self.s._v[ck] in keys else 0
        o = {"id": n.key}
        _v.at(*self.rect)
        self.ids[n.key] = (n, model[at] if model else None)
        if not model:
            _v.label(self._t(n, "empty", None))
            return
        got, opened = _v.table("", tuple(n.l["cols"]), cells, at, o)
        if ck and 0 <= got < len(model) and keys[got] != self.s._v[ck]:
            self.cur_moved(n, keys[got], "table")
        if 0 <= got < len(model):
            self.ids[n.key] = (n, model[got])
        if opened and "on" in n.l and 0 <= got < len(model):
            self.fire(n.l["on"], (model[got],), n, model[got])

    def _r_dialog(self, n, row, rect):
        btns = n.l["buttons"]
        pick = _v.dialog(self._t(n, "title", row), tuple(a.label for a in btns))
        self._kids(n, row, rect)
        _v.dialog()
        if pick >= 0:
            self.q.append((btns[pick], self.args_for(btns[pick]), "button %r of dialog %r"
                           % (btns[pick].name, n.p["title"](None))))

    def _r_popover(self, n, row, rect):
        w = n.p["w"](row) if "w" in n.p else 200
        if "anchor" in n.p:
            want = n.p["anchor"](row)
            if want.endswith(".cur"):         # a list's current row: its node
                lst = next((l for _k, l in self.curs if l.l["model"] == want[:-4]), None)
                want = self.s._v[lst.src["cur"]] if lst is not None else want
            box = self._ui.box(self._id_in(self.ids, want) or "")
            if box is not None:
                _v.at(box[0], box[1] - 8, w, 0)
        if not _v.popover(n.key, w, {"wash": True}) and "close" in n.l:
            self.fire(n.l["close"], self.args_for(n.l["close"]), n, None)
        self._kids(n, row, rect)
        _v.popover()

    def _r_menu(self, n, row, rect):
        items = [k for k in n.kids if _shown(k, row) and k.act.can(row)]
        labels = []
        for k in items:
            hot = k.l.get("keyname") or (k.act.key or "").upper()[:1]
            labels.append(self._t(k, "label", row) + ("\t" + hot if hot and self._keys_shown() else ""))
        got = _v.menu(self._t(n, "caption", row), labels,
                      [k.p["icon"](row) if "icon" in k.p else "" for k in items],
                      kinds=[k.l.get("mkind", "") for k in items])
        if got >= 0:
            self._activate(items[got], None)

    def _keys_shown(self):
        return _v.hints_width(("back", "Back")) > 0

    # -- placed layout ------------------------------------------------------

    def _r_split(self, n, row, rect):
        d = n.p["dir"](row)
        sizes = n.p["sizes"](row)
        pad = self._gap(n.l.get("pad", "m"))
        gap = self._gap(n.l.get("gap", "m"))
        x, y, w, h = _v.inset(rect, pad)
        span = (w if d == "row" else h) - gap * (len(sizes) - 1)
        tot = sum(sizes)
        kids = [k for k in n.kids if _shown(k, row)]
        at = 0
        for i, k in enumerate(kids):
            s = span * sizes[i] // tot if i < len(kids) - 1 else span - at
            r = (x + at, y, s, h) if d == "row" else (x, y + at, w, s)
            at += s + gap
            if k.kind == "group":       # a region of the split: its own scrolled column
                _v.at(*r)
                _v.scroll("split %s %d" % (n.key, i))
                self._kids(k, row, r)
                _v.end()
                _v.scroll()
                _v.at(*self.rect)
                continue
            getattr(self, "_r_" + k.kind)(k, row, r)

    def _r_grid(self, n, row, rect):
        cols, rows_ = n.l["cols"], n.l["rows"]
        gap = self._gap(n.l.get("gap", "s"))
        cells = [_v.hsplit(r, cols, gap=gap) for r in _v.vsplit(rect, rows_, gap=gap)]
        i = 0
        for k in n.kids:
            span = k.l.get("span", 1)
            r0, c0 = divmod(i, cols)
            a, b = cells[r0][c0], cells[r0][c0 + span - 1]
            self._r_button(k, row, rect, at=(a[0], a[1], b[0] + b[2] - a[0], a[3]))
            i += span

    def _r_panel(self, n, row, rect):
        cv, th = self.cv, self.th
        _v.at(*rect)
        x, y, w, h, _st = _v.part(n.key, 0, 0, {"id": n.key, "disabled": True})
        pad = 14 if self.desk else 10 * self.fs
        right, room, bottom = x + w - pad, w - 2 * pad, y + h - pad
        for k in reversed([k for k in n.kids if _shown(k, row)]):
            if k.kind == "canvas":
                _NOW.frame = True
                k.l["draw"](cv, x + pad, y + 4 * self.fs, room, bottom - y - 4 * self.fs)
                continue
            t = self._t(k, "text", row)
            tone = th["lcd_ink"] if k.l.get("tone") == "value" else th["ink_dim"]
            big = 1
            if k.l.get("size") == "fit":
                if k.l.get("face") and _v.face(k.l["face"], t):
                    big = self.fs
                else:
                    big = 5 if self.desk else 3 * self.fs
                    while big > 1 and _v.text_size(t, big)[0] > room:
                        big -= 1
            while len(t) > 1 and _v.text_size(t, big)[0] > room:
                t = t[1:]
            tw, th_ = _v.text_size(t, big)
            ty = bottom - th_ + (th_ // 5 if big > 1 else 0)
            cv.print_scaled(t, right - tw, ty, tone, big) if big > 1 else cv.print(t, right - tw, ty, tone)
            _v.face()
            bottom = ty - (10 if self.desk else 8 * self.fs)

    def _r_cards(self, n, row, rect):
        _v.scroll(n.key)
        subs = any("sub" in k.p for k in n.kids)
        _v.grid(n.key, n.p["w"](None), n.p["h"](None), {"cols": n.p["cols"](None) if "cols" in n.p else 0,
                "margin": 16 if self.desk else 8, "foot": (30 if self.desk else 26) + (6 if subs else 0),
                "top": 4})
        new = n.l.get("new")
        if new is not None:
            self.ids["new"] = (n, None)
            if _v.card(new.label, "", {"id": "new", "icon": "new"})[4] & _v.ACTIVATED:
                self.fire(new, self.args_for(new), n, None)
        lst = n
        self._lists_now.add(n.key)
        model = self.s._v[n.l["model"]]
        self._after_removal(n, [r._f[r._m.key] for r in model])
        for r in model:
            for k in n.kids:
                o = self._o(k, r)
                if lst.src.get("current") and self.s._v[lst.src["current"]] == r._f[r._m.key]:
                    o["current"] = True
                badge = self._t(k, "badge", r)
                if badge:
                    o["badge"] = badge
                tone = self._t(k, "tone", r) if "tone" in k.p else ""
                if tone:
                    o["tone"] = tone
                pic = k.p["image"](r) if "image" in k.p else None
                got = None
                if pic is not None:
                    w, h = self.pic_wh.get(n.key, (0, 0))
                    got = self.pics.cache.get((pic, w, h))
                if got is None and "icon" in k.p:
                    o["icon"] = k.p["icon"](r)
                r_ = _v.card(self._t(k, "label", r), self._t(k, "sub", r), o)
                if r_[2] and pic is not None:          # in view: drawn, or owed
                    if not r_[4] & _v.FOCUS:
                        self.pic_wh[n.key] = (r_[2], r_[3])     # an unfocused card's picture rect
                    w, h = (r_[2], r_[3]) if not r_[4] & _v.FOCUS else self.pic_wh.get(n.key, (r_[2], r_[3]))
                    got = self.pics.peek(pic, w, h) if got is not None or w else None
                    if got is not None:
                        from array import array
                        c = array("i", self.rect)
                        _v.card_clip(c)
                        self.cv.clip(*c)
                        self.pics.draw(self.cv, got, r_[0], r_[1], r_[2], r_[3])
                        self.cv.clip()
                if r_[4] & _v.ACTIVATED:
                    self._activate(k, r)
        _v.grid()
        _v.end()
        _v.scroll()

    def _r_picture(self, n, row, rect):
        """One image at its own place: a card with no words, its picture the
        image's or, while it has none, the icon on a tinted tile."""
        side = n.p["side"](row) if "side" in n.p else 64
        pic = n.p["image"](row)
        got = self.pics.peek(pic, side, side) if pic is not None else None
        o = {"disabled": True}
        if got is None and "icon" in n.p:
            o["icon"] = n.p["icon"](row)
        tone = self._t(n, "tone", row) if "tone" in n.p else ""
        if tone:
            o["tone"] = tone
        _v.grid("pic " + str(n.src["image"]), side, side, {"cols": 1, "foot": 0, "top": 4,
                                                             "margin": 16 if self.desk else 8})
        r = _v.card("", "", o)
        if r[2] and got is not None:
            self.pics.draw(self.cv, got, r[0], r[1], r[2], r[3])
        _v.grid()

    def _r_progress(self, n, row, rect):
        _v.progress(self._t(n, "label", row), n.p["value"](row), n.p["max"](row))

    def _r_empty(self, n, row, rect):
        lines = [self._t(n, "line", row)] if "line" in n.p else []
        _v.empty(self._t(n, "icon", row) or "warn", self._t(n, "title", row), [x for x in lines if x])

    def _r_foot(self, n, row, rect):
        x, y, w, h = self.foot_r
        fh = 30
        by = y + h - fh - 7
        cv = self.cv
        cv.rect(x, y, w, h, self.th["panel"])
        cv.rect(x + 16, by - 7, w - 32, 1, self.th["border"])
        bx = x + w - 16
        for k in reversed([k for k in n.kids if k.kind == "button" and _shown(k, row)
                           and k.act.can(row)]):
            _v.face("caption")
            bw = _v.text_size(self._t(k, "label", row))[0] + 48
            _v.face()
            bx -= bw
            self._r_button(k, row, rect, at=(bx, by, bw, fh))
            bx -= 8
        for k in n.kids:
            if k.kind == "label" and _shown(k, row):
                _v.at(x + 26, by, max(0, bx - x - 26), fh)
                self._r_label(k, row, rect)

    def _r_canvas(self, n, row, rect):
        x, y, w, h, _st = _v.part(n.key, 0, n.l.get("h", 0), {"id": n.key})
        n.l["draw"](self.cv, x, y, w, h)

    # -- input --------------------------------------------------------------

    def keys(self, i):
        """A frame's input: an action's letter or typed characters, then the
        host's keys (focus, the arrows, Enter)."""
        ch = getattr(i, "typed", 0)
        if ch and not self.dialog_shown():
            c = chr(ch)
            for a in self.actions.values():
                if a.key == c and a.can():
                    self.q.append((a, self.args_for(a), "key %r" % c))
                    break
                if c in a.chars and a.can():
                    self.q.append((a, (c,), "typed %r" % c))
                    break
        if self._ui.keys(i) or self.q:
            self._damage.all()
        return True

    def point(self, px, py, click):
        if self._ui.point(px, py, click):
            self._damage.all()
        return True

    def has_page(self):
        return len(self.pages) > 1 or self.dialog_shown()

    def _closer(self, n):
        """G6: the innermost shown node that says what Back does there."""
        if not _shown(n, None):
            return None
        if n.kind == "switch":
            v = self.s._v[n.l["name"]]
            kids = [c for c in n.kids if c.l["v"] in (v, "_")][:1]
        else:
            kids = n.kids
        for k in kids:
            if k.kind in ("rows", "cards", "table"):
                continue
            got = self._closer(k)
            if got is not None:
                return got
        return n if n.kind == "group" and "close" in n.l else None

    def back(self):
        g = self._closer(self.root) if self.root is not None and not self.dialog_shown() else None
        if g is not None:
            act = g.l["close"]
            if act.can():
                self.q.append((act, self.args_for(act), "back on group %r" % g.key))
                self._damage.all()
                return True
        for k in self.root.kids if self.root else ():
            if k.kind in ("dialog", "popover") and _shown(k, None):
                act = k.l["buttons"][0] if k.kind == "dialog" else k.l.get("close")
                if act is not None:
                    self.q.append((act, self.args_for(act), "back on %s %r" % (k.kind, k.key)))
                self._damage.all()
                return True
        if len(self.pages) > 1:
            self.pages.pop()
            self.root = None
            back_to = self.openers.pop() if getattr(self, "openers", None) else None
            self.want_focus = back_to
            was, _NOW.app = _NOW.app, self
            S._trace("page", "back", "page %d, focus back to %r" % (len(self.pages), back_to))
            _NOW.app = was
            return True
        return False

    def trace_text(self):
        import re
        return re.sub(r"\b([a-z]\w*?)__(\w+)", r"\1.\2", "\n".join(self.trace))

    def snapshot(self):
        return S.snapshot(self)


class App(AppLayer):
    """A registered app whose screen is a `uitree` build: the app protocol
    (docs/app_api_v1.md) over an `Engine`. `app_layer` makes the class the
    app's manifest entry names."""

    NEEDS = ("surface", "theme", "damage")
    UI = True
    domain = "system"
    BUILD = None            # (build,): the app module's root page
    IS_APP = None           # (is_app,): claims the app's identity cart

    def __init__(self, ctx, names=None):
        self._surf, self._theme, self._damage = ctx.surface, ctx.theme, ctx.damage
        build = self.BUILD[0]
        self.engine = Engine(S.decl_of(build.__module__), build, ctx.ui, ctx.surface, ctx.damage,
                             getattr(ctx, "files", None))

    def is_app(self, cart):
        return bool(self.IS_APP and self.IS_APP[0](cart))

    def open(self):
        self.engine.open()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.engine.root = None

    def draw(self, dt):
        cv = self._surf.canvas()
        th = self._theme.colors()
        desk = metric_class() == DESK
        win = self._surf.windowed()
        fs = max(1, getattr(cv, "font_scale", 1))
        cs = max(fs, self._surf.chrome_scale() or fs)
        content = content_of(cv.w, cv.h, 0 if win else bar_rows(cs))
        self.engine._ui.begin(cv, th, content, desk, win)
        self.engine.paint(dt, cv, th, content, desk)

    def handle_input(self, i):
        return self.engine.keys(i)

    def handle_pointer(self, px, py, click):
        return self.engine.point(px, py, click)

    def has_page(self):
        return bool(self.engine.has_page())

    def back(self):
        return self.engine.back()

    @property
    def idle_owed(self):
        return self.engine.idle_owed

    def idle_step(self):
        self.engine.idle_step()


def app_layer(id, title, build, is_app=None, needs=()):
    """The AppLayer class of an app whose screen is `build` (the module's
    root page): what its manifest's `entry` names."""
    if not callable(build):
        raise S.UIError("N03", "app_layer(%r): build is not a function" % id,
                        fix="pass the module's page function itself: app_layer(%r, %r, build)" % (id, title))
    return type(title.replace(" ", "") + "App", (App,), {
        "id": id, "TITLE": title, "BUILD": (build,), "IS_APP": (is_app,) if is_app else None,
        "NEEDS": App.NEEDS + tuple(needs)})


class Page(Engine):
    """A `uitree` page hosted by a surface still drawn with today's verbs
    (the bridge): the surface begins the frame on its own host and routes
    the keys to it; `paint` replays the page into its content rect. It goes
    with the old engine."""

    def __init__(self, build, host, damage, files=None, close=None):
        Engine.__init__(self, S.decl_of(build.__module__), build, host, None, damage, files)
        self.close_root = close
