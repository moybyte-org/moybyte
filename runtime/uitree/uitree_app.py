"""The UI tree's replay: a built tree drawn each frame through the
toolkit's `ui` verbs, call for call as a hand-written page makes them, so a
page of standard rows draws today's pixels. No app code runs here but a
canvas's draw (the escape hatch); handlers are queued and run after the
frame, as one commit.

`Engine` replays into a frame its host began. `App` is a registered app
whose screen is a build (`app_layer` makes the class a manifest names);
`Page` is a build hosted by a surface still drawn with today's verbs."""

import ui as _v
from layers import AppLayer
from layout_base import bar_rows, content_of, metric_class, DESK

import uitree as S
from uitree import _NOW

_UP, _DOWN = 1 << 2, 1 << 3     # the up and down bits of a host's frame buttons (ui_draw._HOST_BUTTONS)
_IN_REP = 10                    # the frame input's repeat word, a byte a direction (ui_draw._IN_REP)
ID_MAX = 31            # bytes of a node id the host engine keeps (seen: a longer focus id comes back cut)
_GAP = {"s": (6, 10), "m": (8, 16), "l": (16, 24), "0": (0, 0)}


_RNAMES = {}


def _rname(kind):
    """A node kind's replay method's name, made once: a name built each
    frame is a string a kid a frame."""
    r = _RNAMES.get(kind)
    if r is None:
        r = _RNAMES[kind] = "_r_" + kind
    return r


def _shown(n, row):
    g = n.p.get("shown")
    return g is None or g(row)


def _list_of(node):
    while node is not None and node.kind not in ("rows", "cards", "table"):
        node = node.parent
    return node


class Engine(S.Builder):
    """One app's built tree replayed into today's verbs, inside a frame its
    host has begun (`host.begin`), and the commits of its handlers."""

    close_root = None       # what ui.pop() on the first page does: a hosted page's host closes it

    def __init__(self, decl, build, host, surface, damage, files=None, gen=False, env=None):
        self.decl = decl
        self.env = env                  # what the host hands its handlers (`env()`)
        self._gen_on = gen              # the page's gen: off where a golden pins today's draw counts
        self._ui, self._surf, self._damage = host, surface, damage
        self.init_builder(decl.state, build)
        self.q = []
        self.ids = {}
        self.dups = set()
        self._sync = {}
        self._orders = {}               # a list's key -> the order of its rows (_after_removal)
        self._pid = (None, 0, "")       # (build, level, the id _page_id made for them)
        self.curs = []
        self.tv_stop = False
        self.fresh = False
        self.focused = None
        self.drawn = {}
        self.actions = decl.actions
        self._in_pop = False            # a popover's children replaying
        self._roles = None              # role -> its action, made again when an action is declared
        self._roles_n = -1
        self.files = files
        self.pics = None                # the picture engine, made at the first frame over its canvas
        self.calls = 0                  # handler runs this frame: actions, hooks, timers, posts, work steps
        self.frame_calls = []
        self.work_run = {}              # work name -> [first frame, frames, steps, ms, over, wrote]
        self.frame_no = 0
        self.pic_wh = {}
        self.drafts = {}                # a commit field's key -> its text until it is written
        self._page_o = {"gen": 0}       # the page's gen: moves when what its rows show may have
        self._gen_ok = False
        self._page_names = ()           # the states the page's gen counts: all but its cells' own
        self._cells = {}                # a split's region (its group) -> [its override block, the states it reads]
        self._edits = 0                 # a commit field's draft edits, which move the gen too
        self._buttons = 0               # the frame's buttons, as keys() handed them to the host
        st = decl.state                 # this engine's own values: two consoles in one process each hold theirs
        self._vals = dict((k, st._init.get(k)) for k in st._t)
        self._vers = dict.fromkeys(st._t, 0)

    def _bind(self):
        """The module's state holds this engine's values: at every way in (a
        frame, a handler, input, an opening, a way out) an engine that is not
        the last one in puts the other's values by and takes its own. The
        first engine in takes the module's as they are. The dicts stay the
        objects templates and derives read."""
        st = self.s
        was = st._owner
        if was is self:
            return
        if was is not None:
            was._vals, was._vers = dict(st._v), dict(st._ver)
            st._v.clear()
            st._v.update(self._vals)
            st._ver.clear()
            st._ver.update(self._vers)
        object.__setattr__(st, "_owner", self)

    # -- the engine's counted runs (G12) ------------------------------------

    def run(self, fn, *args, action=None):
        self._bind()
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
        if not self.decl.posts:
            return
        q, self.decl.posts[:] = list(self.decl.posts), []
        tk = self.s._tickets
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
        self._bind()
        self.pages = self.pages[:1]
        self.openers = []
        self._ui.reset()
        self.root = None
        self.want_focus = None          # a fresh visit: no focus, cur or row order carried over
        self._sync, self.ids, self.focused, self.drawn = {}, {}, None, {}
        self.drafts = {}
        self._orders = {}
        self._lists_now = self._lists_before = set()
        self._lists_ever = set()
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
        self._bind()
        self.frame_no += 1
        self.calls = 0
        self._posts()
        self.clock += int(dt * 1000)
        if self.timers:
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
            self._gen_ok = self._gen_on and self._gen_safe(self.root)
            if self._gen_ok:
                self._cell_gens(self.root)
        self.pics.begin_frame()
        _NOW.app, _NOW.frame = self, True
        try:
            self.desk = desk
            self.fs = fs
            self._replay_page(self.root)
        finally:
            _NOW.app, _NOW.frame = None, False
        self._buttons = 0
        q = self.q
        if q:
            self.q = []
            for act, args, src in q:
                self.run_action(act, args, src)
        S.commit(self)              # a two-way control's write with no hook moves a derive too
        self._works()
        if S.TRACING:
            self.frame_calls.append(self.calls)
        if q or self.timers or self.work_run or self.decl.posts:
            self._damage.all()

    GEN_UNSAFE = ("canvas", "picture", "cards", "text_editor")

    def _gen_safe(self, n):
        """Whether every pixel the page's rows draw comes from state, so the
        page's gen (the sum of the versions of the states it reads) moves
        whenever one may: no canvas, picture or text editor, and no template
        naming @focus, @input or @touch. A text view's lines are its state's
        and its scroll the toolkit's own region; cards or a picture with no
        image draw only their state (an icon on a tile)."""
        if n.kind in ("cards", "picture") and not self._images(n):
            pass
        elif n.kind in self.GEN_UNSAFE:
            return False
        for v in n.src.values():
            if isinstance(v, str) and ("@focus" in v or "@input" in v or "@touch" in v):
                return False
        return all(self._gen_safe(k) for k in n.kids)

    @staticmethod
    def _images(n):
        """Whether `n` (cards or a picture) draws an image, decoded late."""
        return "image" in n.p or any("image" in k.p for k in n.kids)

    def _names_in(self, v, out):
        """Add to `out` the state names the prop value `v` reads: each word
        not led by `.` (a row's field) or `@` (an engine value)."""
        if isinstance(v, (tuple, list)):
            for x in v:
                self._names_in(x, out)
            return
        if not isinstance(v, str):
            return
        ver = self.s._ver
        word, lead, prev = "", "", ""
        for ch in v + " ":
            if ch.isalpha() or ch.isdigit() or ch == "_":
                if not word:
                    lead = prev
                word += ch
            else:
                if word and lead != "." and lead != "@" and word in ver:
                    out.add(word)
                word = ""
            prev = ch

    def _reads(self, n, out, cur):
        """The state names node `n` and its kids read; `cur` False leaves
        out a list's cur= (focus the toolkit draws, not a row's pixels)."""
        for props in (n.src, n.l):
            for k, v in props.items():
                if cur or k != "cur":
                    self._names_in(v, out)
        if n.key in self.s._ver:
            out.add(n.key)
        for k in n.kids:
            self._reads(k, out, cur)

    def _cell_gens(self, root):
        """Each region of a split (a group in it) and each cards' region
        carries a gen of its own, over the states only its rows read; the
        page's gen counts the rest,
        so a cell whose state moved leaves the page and its other cells held.
        A state any action, any node outside a cell or a hint reads stays the
        page's; one only a list's cur= names is focus, which no gen counts."""
        cells, rest = [], set()

        def walk(n):
            if n.kind == "cards":
                names = set()
                self._reads(n, names, False)
                cells.append((n, names))
                return
            if n.kind == "split":
                for k in n.kids:
                    if k.kind == "group":
                        names = set()
                        self._reads(k, names, False)
                        cells.append((k, names))
                    else:
                        walk(k)
                self._names_in(list(n.src.values()), rest)
                return
            for props in (n.src, n.l):
                for k, v in props.items():
                    if k != "cur":
                        self._names_in(v, rest)
            if n.key in self.s._ver:
                rest.add(n.key)
            for k in n.kids:
                walk(k)

        def hints(n):
            for k, v in n.src.items():
                if k == "hint":
                    self._names_in(v, rest)
            for k in n.kids:
                hints(k)
        walk(root)
        hints(root)
        for a in self.actions.values():
            for v in (a.label, a.hint, a.when_src):
                self._names_in(v, rest)
        mine, seen, focus = set(), set(), set()
        for _k, names in cells:
            mine |= names
        self._reads(root, seen, False)
        self._reads(root, focus, True)
        own = (mine - rest) | (focus - seen - rest)    # a cur= alone is focus: the toolkit's to draw
        self._page_names = tuple(k for k in self.s._ver if k not in own)
        self._cells = {}
        for k, names in cells:
            self._cells[k] = [{"gen": 0}, tuple(names)]

    def _gen_of(self, names):
        ver, g = self.s._ver, self.builds + self._edits
        for k in names:
            g += ver[k]
        return g & 0x7fffffff

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

    def _page_id(self):
        """The toolkit's id of the page on top: its build's name and its
        level in the stack, so a title that moves keeps the page's focus and
        scroll, and two levels sharing a title keep their own."""
        b, n = self.pages[-1], len(self.pages)
        m = self._pid
        if m[0] is not b or m[1] != n:
            m = self._pid = (b, n, ("%d %s" % (n, getattr(b, "__name__", "page")))[:ID_MAX])
        return m[2]

    def _gap(self, name):
        g = _GAP.get(str(name), (0, 0))
        return g[1] if self.desk else g[0] * self.fs

    def _replay_page(self, n):
        w, h = self.content[2], self.content[3]
        kind = _v.input_kind()
        self.input = kind if kind in S.INPUTS else "keys"
        self.classes = ("desk" if self.desk else "handheld", "wide" if w * 3 > h * 4 else "tall") + (
            ("touch",) if self.input == "touch" else ())
        S.LIVE = S.live_units(n)
        shown = [k for k in n.kids if _shown(k, None)]
        foot = self._foot_of(n)
        band = None
        if foot is not None and not self.desk:
            rest = self.content
            if self._keys_shown():              # a handheld's band is the content's last line
                band, rest = _v.cut_bottom(rest, 20 * self.fs)
            self.foot_r, body = _v.cut_bottom(rest, 22 * self.fs + self._foot_line(foot))
            _v.at(*body)
        o = self._page_o
        o["id"] = self._page_id()
        if self._gen_ok:
            o["gen"] = self._gen_of(self._page_names)
        else:
            o.pop("gen", None)
        rect = _v.page(n.p["title"](None), o)
        f = _v.focus()
        last = self.focused                     # the last frame's focus
        now = self.ids.get(f)
        if now is not None:
            self.focused = now                  # a pointer's move lands at the frame's begin
        elif f == "" and self.focused is not None:
            self.focused = self._stepped(n)     # a key's step lands as the stops draw: where it will
        if self.focused is None and self.want_focus is None:
            self.focused = next(self._stops(n), None)   # where focus starts: the first stop
        hint = self._hints()
        placed = foot is not None or any(k.kind in ("split", "grid") for k in shown)
        if not hint:
            pass                        # nothing on offer: no band, as a page with no keys draws none
        elif not placed:
            _v.hints(hint)
        elif band is not None:          # under the foot, outside the page's ground
            self.cv.rect(band[0], band[1], band[2], band[3], self.th["panel"])
            _v.at(*band)
            _v.hints(hint)
            _v.at(*rect)
        elif not self.desk and _v.hints_width(hint) > 0:
            band, rect = _v.cut_bottom(rect, 20 * self.fs)
            _v.at(*band)
            _v.hints(hint)
        elif _v.hints_width(hint) > 0:  # the desk's band, at the page's foot, its room cut from the page
            import ui_draw as _ud
            _v.hints(hint)
            rect = _v.cut_bottom(rect, _ud.metrics("value_row")[17] * self.fs)[1]
        if foot is not None and self.desk:  # the desk's foot: docked above the band, on the page's ground
            got = _v.dock(44 + self._foot_line(foot))
            if got is not None:
                self.foot_r = got
                rect = (rect[0], rect[1], rect[2], max(0, got[1] - rect[1]))
        prev, self.ids = self.ids, {}
        moved = False
        want = [(self.want_focus, None)] if self.want_focus is not None else []
        if self.fresh and prev:
            want += [(i, None) for i, (node, row) in prev.items()
                     if "focus" in node.p and node.p["focus"](row)][:1]
            self.fresh = False
        # a list shown again: drawn last frame, not the frame before, and
        # drawn some frame before that (its first showing is no return)
        before = getattr(self, "_lists_before", set())
        back = (getattr(self, "_lists_now", set()) - before) & getattr(self, "_lists_ever", set())
        lists = {}
        for k, lst in self.curs:
            if self._sync.get(k, self) != self.s._v[k] or (lst.key in back and self.s._v[k]):
                want.append((self.s._v[k], k))      # a list shown again: focus on its cur
                lists[k] = lst
        ahead = None
        for key, k in want:
            if k is None and key == self.want_focus and key in self.dups:
                self._focus_named(prev)
            fid = key if k is None and key in prev else self._id_in(prev, key)
            if not fid and k is not None and key != "":
                fid = self._row_stop_id(lists[k], key)  # a row not drawn yet: its id, so focus lands this frame
            if not fid and k is None:
                fid = ahead = self._drawn_id(key)       # a node this frame draws first: focus lands on it now
            if fid:
                _v.focus(fid)
                moved = True
                if k is not None:
                    self._sync[k] = key
                elif key == self.want_focus:
                    self.want_focus = None
        self.rect = rect
        self.dups = set()
        self._lists_ever = getattr(self, "_lists_ever", set()) | before
        self._lists_before = getattr(self, "_lists_now", set())
        self._lists_now = set()
        self.tv_stop = False            # a text view that is a stop draws this frame (the band's next)
        self._kids(n, None, rect)
        if ahead is not None and ahead not in self.ids:
            self.want_focus = ahead                     # foreseen and not drawn after all: next frame
        f = _v.focus()
        hinted = self.focused
        self.focused = self.ids.get(f)
        self.focus_key = f or ""
        if last is not None and last[0].kind == "field" and last[0].key in self.drafts and (
                self.focused is None or self.focused[0] is not last[0]):
            self._commit_field(last[0])         # focus left a commit field: its draft is written
        if (self.focused and self.focused[0]) is not (hinted and hinted[0]) and self._hints() != hint:
            self._damage.all()                  # the band named another node's verbs: draw it again
        if self.focused is not None and not moved:
            node, row = self.focused
            lst = _list_of(node)
            ck = lst.src.get("cur") if lst is not None else None
            if ck:
                v = "" if row is None else row._f[row._m.key]
                if self.s._v[ck] != v:
                    self.cur_moved(lst, v, "focus")

    def _drawn_id(self, key):
        """The id node `key` (or `ROWKEY/NODEKEY`) draws under this frame, as
        the replay will walk the tree: shown, its action on, its switch case
        the current one, its row in the model. None when it will not draw."""
        rowkey, _, nodekey = key.rpartition("/")

        def walk(n):
            for k in n.kids:
                if not _shown(k, None):
                    continue
                if k.kind == "switch":
                    v = self.s._v[k.l["name"]]
                    case = next((c for c in k.kids if c.l["v"] == v), None) or next(
                        (c for c in k.kids if c.l["v"] == "_"), None)
                    got = walk(case) if case is not None else None
                elif k.kind == "table" and not rowkey and k.key == nodekey:
                    return k.key[:ID_MAX]       # a table is one stop, its key the id
                elif k.kind in ("rows", "cards", "table"):
                    got = rowkey and self._row_node_id(k, rowkey, nodekey)
                elif k.act is not None and not k.act.can(None):
                    continue
                elif not rowkey and k.key == nodekey:
                    return k.key[:ID_MAX]
                else:
                    got = walk(k)
                if got:
                    return got
            return None
        return walk(self.root) if self.root is not None else None

    STOPS = ("link", "button", "toggle", "slider", "field", "seg", "item", "card", "tabs")

    def _stops(self, n, row=None):
        """The (node, row) of each focus stop this frame draws, in order:
        shown, its action on (a greyed one is no stop), in the current case
        of a switch, in each row of a list; none in a dialog or popover."""
        for k in n.kids:
            if not _shown(k, row):
                continue
            if k.kind == "switch":
                v = self.s._v[k.l["name"]]
                case = next((c for c in k.kids if c.l["v"] == v), None) or next(
                    (c for c in k.kids if c.l["v"] == "_"), None)
                if case is not None:
                    yield from self._stops(case, row)
            elif k.kind in ("rows", "cards", "table"):
                for r in self.s._v[k.l["model"]]:
                    if k.kind == "rows":
                        yield from self._stops(k, r)
                    elif k.kind == "cards":
                        yield from self._stops(k, r)    # its cards: one a row
                    else:
                        yield (k, r)
                        break
            elif k.kind in ("dialog", "popover"):
                continue
            elif k.kind in self.STOPS:
                if k.act is None or k.act.can(row):
                    yield (k, row)
            else:
                yield from self._stops(k, row)

    def _stepped(self, n):
        """Where this frame's up or down step takes focus from the focused
        stop: the stop before or after it in the page's order (None where the
        frame steps no way the order says)."""
        b = self._buttons
        d = 1 if b & _DOWN else -1 if b & _UP else 0
        if not d:
            return None
        node, row = self.focused
        stops = list(self._stops(n))
        for i in range(len(stops)):
            if stops[i][0] is node and stops[i][1] is row:
                j = i + d
                return stops[j] if 0 <= j < len(stops) else stops[i]
        return None

    def _row_node_id(self, lst, rowkey, nodekey):
        row = next((r for r in self.s._v[lst.l["model"]] if str(r._f[r._m.key]) == rowkey), None)
        if row is None:
            return None
        for k in lst.kids:
            if k.key == nodekey and _shown(k, row) and (k.act is None or k.act.can(row)):
                return ("%s/%s" % (rowkey, nodekey))[:ID_MAX]
        return None

    def _row_stop_id(self, lst, rowkey):
        """The id the first focus stop of row `rowkey` of `lst` will draw under."""
        if not any(r._f[r._m.key] == rowkey for r in self.s._v[lst.l["model"]]):
            return None
        if lst.kind == "table":
            return lst.key[:ID_MAX]         # a table is one stop: its current row is drawn at= it
        for k in lst.kids:
            if k.kind in ("link", "button", "toggle", "slider", "field", "item", "card"):
                return ("%s/%s" % (rowkey, k.key))[:ID_MAX]
        return None

    def _focus_named(self, prev):
        """G15: a handler's ui.focus(KEY) names a node; through a fragment used
        twice, it resolves among the shown instances, and two shown is N08."""
        w = self.want_focus
        if w is None:
            return
        if w in self.dups:
            self.want_focus = None
            raise S.UIError("N08", "ui.focus(%r): two shown nodes are keyed %r" % (
                S.dotted(w), w), where=("(run)", 0),
                fix="give the two uses of the fragment different parents' keys and name the parent: "
                    "ui.focus(\"panel/key\"), or show one at a time")

    def settle(self):
        for _ck, lst in self.curs:
            self._after_removal(lst, [r._f[r._m.key] for r in self.s._v[lst.l["model"]]])

    def _after_removal(self, lst, keys):
        """G15's removal rule: the current row gone, the next row in the old
        order that is still there, else the one before it, else none."""
        ck = lst.src.get("cur")
        orders = self._orders
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

    def _foot_of(self, n):
        """The page's shown foot: at its root, or in the shown case of a
        switch at its root."""
        for k in n.kids:
            if not _shown(k, None):
                continue
            if k.kind == "foot":
                return k
            if k.kind == "switch":
                v = self.s._v[k.l["name"]]
                case = next((c for c in k.kids if c.l["v"] == v), None) or next(
                    (c for c in k.kids if c.l["v"] == "_"), None)
                for c in case.kids if case is not None else ():
                    if c.kind == "foot" and _shown(c, None):
                        return c
        return None

    def _foot_label(self, n):
        """A shared foot's label, shown and not empty: its line over the
        buttons (None where it has none)."""
        if n.l.get("look") != "share":
            return None
        for k in n.kids:
            if k.kind == "label" and _shown(k, None) and self._t(k, "text", None):
                return k
        return None

    def _foot_line(self, n):
        k = self._foot_label(n)
        if k is None:
            return 0
        return _v.text_size("Ag")[1] + (8 if self.desk else 4 * self.fs)

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

    def role_action(self, role):
        """The declared action answering `role:ROLE` (one per key, K02).
        Asked every frame for the arrows: a lookup, no string made."""
        roles = self._roles
        if roles is None or self._roles_n != len(self.actions):
            roles = {}
            for a in self.actions.values():
                for k in a.keys:
                    if k.startswith("role:") and k[5:] not in roles:
                        roles[k[5:]] = a
            self._roles, self._roles_n = roles, len(self.actions)
        return roles.get(role)

    def role(self, role):
        """A host that routes its own keys (kernel chrome) presses ROLE: the
        action answering it runs now, as one commit, when it is on offer.
        True when one ran."""
        a = self.role_action(role)
        if a is None or not a.can():
            return False
        self.run_action(a, self.args_for(a), "role %s" % role)
        self._damage.all()
        return True

    def _hints(self):
        f = self.focused
        d = self.dialog_shown()
        if d:
            b = d.l["buttons"]
            return ("confirm", b[-1].hint_now(), "back", b[0].hint_now())
        out = []
        pop = self._popover_shown()
        if pop is not None:
            # a popover: Back closes it; what lies under it takes no keys
            if f is not None and self._holds(pop, f[0]) and f[0].act is not None and f[0].act.can():
                out += ["confirm", f[0].act.hint_now()]
            close = pop.l.get("close")
            return tuple(out + ["back", close.hint_now() if close is not None else "Close"])
        if f is not None:
            node, row = f
            act = node.act if node.kind != "cards" else node.l.get("new")
            h = act.hint_now() if act is not None and act.can() else None
            if node.kind == "field" and node.l.get("commit"):
                return ("confirm", "Done", "cancel", "Cancel")
            if node.kind in ("seg", "slider"):
                out += ["change", "Change"]
            if node.kind == "toggle":
                h = self._t(node, "hint", row) if "hint" in node.p else "Change"
            if node.kind == "table" and "hint" in node.p:
                h = self._t(node, "hint", None)
            if h:
                out += ["confirm", h]
            if node.kind == "text_view":
                out += ["scroll", "Scroll"]      # a text that runs on: the arrows scroll it (L7)
        else:
            a = self.role_action("confirm")
            h = a.hint_now() if a is not None and a.can() else ""
            if h:
                out += ["confirm", h]
        if self.desk and self.tv_stop and (f is None or f[0].kind != "text_view"):
            out += ["PgUp PgDn", "Scroll"]       # the page's text runs on: PgUp and PgDn page it
        for a in self.actions.values():
            if a.letters and a.can():
                out += [a.letters[0].upper(), a.hint_now()]
        a = self.role_action("back") if len(self.pages) == 1 else None
        if a is None:
            return tuple(out + ["back", "Back"])
        h = a.hint_now() if a.can() else ""
        return tuple(out + ["back", h] if h else out)

    def _popover_shown(self):
        for k in self.root.kids if self.root else ():
            if k.kind == "popover" and _shown(k, None):
                return k
        return None

    @staticmethod
    def _holds(n, node):
        return n is node or any(Engine._holds(k, node) for k in n.kids)

    def dialog_shown(self):
        for k in self.root.kids if self.root else ():
            if k.kind == "dialog" and _shown(k, None):
                return k
        return None

    def _kids(self, n, row, rect):
        for k in n.kids:
            if _shown(k, row) and (k.act is None or k.act.can(row) or k.act.off == "grey"):
                getattr(self, _rname(k.kind))(k, row, rect)

    def _t(self, n, k, row, default=""):
        """A text prop's value now; a change after the first frame is traced."""
        g = n.p.get(k)
        if g is None:
            return n.act.label if (k == "label" and n.act is not None) else default
        v = g(row)
        src = n.src[k]
        if S.TRACING and "{" in src:            # what the trace's "drew" line compares
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
        if n.act is not None and n.act.off == "grey" and not n.act.can(row):
            o["disabled"] = True
        lst = _list_of(n)
        if row is not None and lst is not None and lst.kind == "rows" and lst.src.get("current"):
            o["current"] = row._f[row._m.key] == self.s._v[lst.src["current"]]
        if "signal" in n.p:
            o["signal"] = n.p["signal"](row)
        name = n.p["icon"](row) if "icon" in n.p else n.act.icon if n.act is not None else ""
        if name:
            _v.icon(name)
        if n.l.get("look") == "steps":
            o["steps"] = True
        return o

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
        if "icon" in n.p:
            name = n.p["icon"](row)
            if name:
                _v.icon(name)
        for text, o in self._label_rows(n, row):
            _v.label(text, o)

    def _label_rows(self, n, row):
        """A label's rows as the toolkit's labels: (text, options) each."""
        tone = n.l.get("tone")
        text = self._t(n, "text", row)
        o = None if not tone else {"tone": tone} if tone in ("dim", "value", "danger") else {"ink": tone}
        face, wrap, align = n.l.get("face"), n.l.get("wrap"), n.l.get("align")
        if face or wrap == "word" or align in ("center", "end") or "lines" in n.l:
            o = {"tone": tone} if tone in ("dim", "value", "danger") else dict(o or {})
            if face and not (n.l.get("size") == "fit" and not self._fits(face, text)):
                o["face"] = face
            if wrap == "word" or "lines" in n.l:
                o["wrap"] = True
                o["lines"] = n.l.get("lines", 16)
            if align in ("center", "end"):
                o["align"] = align
            return ((text, o),)
        if wrap == "char":   # lines of what fits a group's card, cut anywhere
            import ui_draw as _ud
            per = max(8, (self.rect[2] - 32) // max(1, _ud.font("ui")[1] * self.fs))
            return tuple((text[at:at + per], o) for at in range(0, len(text), per))
        return ((text, o),)

    def _fits(self, face, text):
        """A label's `size="fit"`: its face where the text fits the page's
        width in it, else the page's own face (the engine's ellipsis after)."""
        if not _v.face(face, text):
            return False
        w = _v.text_size(text)[0]
        _v.face()
        return w <= self.rect[2] - 32

    def _r_rule(self, n, row, rect):
        _v.rule()

    def _r_note(self, n, row, rect):
        _v.note(self._t(n, "icon", row) or "info", n.p["lines"](row))

    def _r_text_view(self, n, row, rect):
        v = self.s._v[n.l["name"]]
        if "field" in n.l:
            f = n.l["field"]
            lines = [r._f[f] for r in v]
        else:
            lines = v.split("\n") if v else []
        o = self._o(n, None, syntax=n.l.get("syntax", "plain"), keys=True)   # a stop where it runs on
        if n.l.get("face") == "ui":
            o["face"] = "ui"
        if n.l.get("wrap") == "word":
            o["wrap"] = True
        if n.l.get("follow"):
            o["follow"] = True
        _v.text_view(n.key, lines, o)
        self.tv_stop = self.tv_stop or self._ui.box(n.key) is not None

    def _r_info(self, n, row, rect):
        _v.info(self._t(n, "label", row), self._t(n, "value", row), self._o(n, row))

    def _r_link(self, n, row, rect):
        if _v.link(self._t(n, "label", row), self._t(n, "value", row), self._o(n, row)):
            self._activate(n, row)

    def _activate(self, n, row):
        if n.act.can(row):
            self.fire(n.act, self.args_for(n.act, row, n.l.get("arg")), n, row)

    def _fit(self, n, at):
        """A button in a split's column takes its natural width (the
        toolkit's `fit`) unless it grows."""
        return at is None and getattr(self, "in_cell", False) and not n.l.get("grow")

    def _r_button(self, n, row, rect, at=None):
        text = self._t(n, "label", row)
        if at:
            _v.at(*at)
        o = self._o(n, row)
        if self._fit(n, at):
            o["fit"] = True
        if n.l.get("alone") and ("icon" in n.p or n.act.icon):
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
                self.fire(n.l["on"], (), n, None)

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

    def _r_seg(self, n, row, rect):
        k = n.key
        cur = self.s._v[k]
        if "model" in n.l:
            rows = self.s._v[n.l["model"]]
            keys = [r._f[r._m.key] for r in rows]
            words = [S._text(n.l["text"](r)) for r in rows]
        else:
            keys, words = n.l["values"], n.l["words"]
        at = keys.index(cur) if cur in keys else (-1 if "model" in n.l else 0)
        verb = _v.cycle if n.l.get("look") == "cycle" else _v.seg
        got = verb(self._t(n, "label", row), words, None if at < 0 else at, self._o(n, row))
        if got is not None and 0 <= got < len(keys) and got != at:
            self._two_way(n, keys[got], cur)

    def _commit_field(self, n):
        """A commit field's draft, written to its state: Enter, or focus
        leaving it. `changed=` runs when the state moves."""
        k = n.key
        text = self.drafts.pop(k, None)
        if text is None or text == self.s._v[k]:
            return
        old = self.s._v[k]
        self.s._v[k] = text
        self.s._ver[k] += 1
        if n.l.get("secret"):
            old, text = "*" * len(old), "*" * len(text)
        was, _NOW.app = _NOW.app, self
        S._trace("state", k, "%r -> %r   (field %r committed)" % (old, text, k))
        _NOW.app = was
        if "changed" in n.l:
            self.fire(n.l["changed"], (), n, None)

    def leave(self):
        """Every way out of the page: a commit field's draft is written."""
        self._bind()
        for n, _row in list(self.ids.values()):
            if n.kind == "field" and n.key in self.drafts:
                self._commit_field(n)
        q, self.q = self.q, []
        for act, args, src in q:
            self.run_action(act, args, src)
        S.commit(self)

    def _r_field(self, n, row, rect):
        k = n.key
        o = self._o(n, row)
        if n.l.get("secret"):
            o["secret"] = True
        if "hint" in n.p:
            o["hint"] = self._t(n, "hint", row)
        if n.l.get("commit"):
            cur = self.drafts.get(k, self.s._v[k])
            text, done = _v.field(self._t(n, "label", row), cur, n.l.get("max", 32), o)
            if text != cur:
                self.drafts[k] = text
                self._edits += 1
            if done:
                self._commit_field(n)
                if "on" in n.l:
                    self.fire(n.l["on"], (), n, None)
            return
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
                self.fire(n.l["changed"], (), n, None)
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
        w = self._size(n.p["w"](row) if "w" in n.p else 200)
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
        self._in_pop = True
        try:
            self._kids(n, row, rect)
        finally:
            self._in_pop = False
        _v.popover()

    def _pop_split(self, n, row):
        """A split in a popover's body (dir "row"): its cells side by side on
        the body's rows, in by its pad, as tall as its tallest cell, each a fixed size in desk units
        or a weight of what is left ("N*"); a picture cell the picture at
        its top, a group cell its children one under another."""
        kids = [k for k in n.kids if _shown(k, row)]
        sizes = n.p["sizes"](row)
        hs = [self._pop_cell_h(k, row) for k in kids]
        x, y, w, _h, _step = _v.measure("progress", "")    # where the body's stack stands
        pad = self._gap(n.l.get("pad", "m"))
        _v.space((max(hs) if hs else 0) + 2 * pad)
        if not w:
            return
        x, y, w = x + pad, y + pad, max(0, w - 2 * pad)
        gap = self._gap(n.l.get("gap", "m"))
        at = x
        for k, cw, h_ in zip(kids, self._spans(sizes, w - gap * (len(kids) - 1), len(kids)), hs):
            if k.kind == "group":
                cy = y
                for kk in k.kids:
                    if not _shown(kk, row):
                        continue
                    kh = self._pop_kid_h(kk, row)
                    _v.at(at, cy, cw, kh)
                    getattr(self, _rname(kk.kind))(kk, row, (at, cy, cw, kh))
                    cy += kh
            else:
                _v.at(at, y, cw, h_)
                getattr(self, _rname(k.kind))(k, row, (at, y, cw, h_))
            at += cw + gap

    def _spans(self, sizes, span, n):
        """A split's first `n` cells across `span` pixels: with a weight
        ("N*") among them, each int a fixed size in desk units and the
        weights sharing the rest; else every int a weight. The last cell
        takes what rounding leaves."""
        sizes = sizes[:n]
        if any(isinstance(v, str) for v in sizes):
            ws = [int(v[:-1] or 1) if isinstance(v, str) else 0 for v in sizes]
            rest = max(0, span - sum(self._size(v) for v in sizes if not isinstance(v, str)))
            out = [rest * wt // max(1, sum(ws)) if isinstance(v, str) else self._size(v)
                   for v, wt in zip(sizes, ws)]
        else:
            tot = max(1, sum(sizes))
            out = [span * v // tot for v in sizes]
        if out:
            out[-1] += span - sum(out)
        return out

    def _pop_cell_h(self, k, row):
        if k.kind == "picture":
            return self._size(k.p["side"](row) if "side" in k.p else 64) + self._size(8)
        if k.kind == "group":
            return sum(self._pop_kid_h(kk, row) for kk in k.kids if _shown(kk, row))
        return self._pop_kid_h(k, row)

    def _pop_kid_h(self, k, row):
        if k.kind == "label":
            return sum(_v.measure("label", text, o)[3] for text, o in self._label_rows(k, row))
        if k.kind == "progress":
            return _v.measure("progress", "")[3]
        raise S.UIError("N01", "a %s in a popover's split" % k.kind, where=k.where,
                         fix="a picture, a label or a progress; or the %s outside the split" % k.kind)

    def _r_menu(self, n, row, rect):
        items = [k for k in n.kids if _shown(k, row) and k.act.can(row)]
        labels = []
        for k in items:
            hot = k.act.letters.upper()[:1]
            labels.append(self._t(k, "label", row) + ("\t" + hot if hot and self._keys_shown() else ""))
        got = _v.menu(self._t(n, "caption", row), labels,
                      [k.p["icon"](row) if "icon" in k.p else k.act.icon for k in items],
                      kinds=[k.l.get("mkind", "") for k in items])
        if got >= 0:
            self._activate(items[got], None)

    def _keys_shown(self):
        return _v.hints_width(("back", "Back")) > 0

    # -- placed layout ------------------------------------------------------

    def _r_split(self, n, row, rect):
        if self._in_pop:
            return self._pop_split(n, row)
        d = n.p["dir"](row)
        sizes = n.p["sizes"](row)
        pad = self._gap(n.l.get("pad", "m"))
        gap = self._gap(n.l.get("gap", "m"))
        x, y, w, h = _v.inset(rect, pad)
        span = (w if d == "row" else h) - gap * (len(sizes) - 1)
        kids = [k for k in n.kids if _shown(k, row)]
        spans = self._spans(sizes, span, len(sizes))
        at = 0
        for i, k in enumerate(kids):
            s = spans[i] if i < len(kids) - 1 else span - at
            r = (x + at, y, s, h) if d == "row" else (x, y + at, w, s)
            at += s + gap
            if k.kind == "group":       # a region of the split: its own scrolled column
                sid = "split %s %d" % (n.key, i)
                _v.at(*r)
                cg = self._cells.get(k) if self._gen_ok else None
                if cg is not None:
                    cg[0]["gen"] = self._gen_of(cg[1])
                    _v.scroll(sid, cg[0])
                else:
                    _v.scroll(sid)
                was, self.in_cell = getattr(self, "in_cell", False), True
                if d == "row" and n.l.get("align", "center") == "center":
                    used = self._col_h(k, row)
                    if used is not None:
                        _v.space((r[3] - used) // 2)
                self._kids(k, row, r)
                self.in_cell = was
                _v.end()
                _v.scroll()
                _v.at(*self.rect)
                continue
            if k.kind == "canvas" and self._draws_only(k):
                _NOW.frame = True       # a cell of its own that only draws: drawn into the cell
                k.l["draw"](self.cv, *r)
                continue
            if k.kind == "canvas":      # a cell of its own: the canvas's part fills it
                _v.at(*r)
                self._r_canvas(k, row, r)
                _v.at(*self.rect)
                continue
            getattr(self, _rname(k.kind))(k, row, r)

    def _col_h(self, n, row):
        """How tall a split's column draws, measured before it draws (the
        toolkit's `measure`): None where it holds a kind with no measure,
        which lays it out from its cell's top."""
        h = 0
        for k in n.kids:
            if not (_shown(k, row) and (k.act is None or k.act.can(row) or k.act.off == "grey")):
                continue
            if k.kind == "label":
                for text, o in self._label_rows(k, row):
                    h += _v.measure("label", text, o)[4]
            elif k.kind == "button":
                h += _v.measure("button", self._t(k, "label", row),
                                {"fit": True} if self._fit(k, None) else None)[4]
            else:
                return None
        return h

    def _r_grid(self, n, row, rect):
        cols, rows_ = n.l["cols"], n.l["rows"]
        gap = self._gap(n.l.get("gap", "s"))
        if "pad" in n.l:
            p = self._gap(n.l["pad"])
            rect = (rect[0] + p, rect[1] + p, max(0, rect[2] - 2 * p), max(0, rect[3] - 2 * p))
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

    def _size(self, v):
        """A size a build gives (w=, h=, side=): the handheld's at its font
        scale, the desk's as given."""
        return v if self.desk else v * self.fs

    def _r_cards(self, n, row, rect):
        cg = self._cells.get(n) if self._gen_ok else None
        if cg is not None:
            cg[0]["gen"] = self._gen_of(cg[1])
            _v.scroll(n.key, cg[0])
        else:
            _v.scroll(n.key)
        subs = any("sub" in k.p for k in n.kids)
        go = {"cols": n.p["cols"](None) if "cols" in n.p else 0, "margin": 16 if self.desk else 8,
              "foot": ((52 if subs else 30) if self.desk else 26 + (6 if subs else 0)), "top": 4,
              "wrap": "fit"}
        w, h = self._size(n.p["w"](None)), self._size(n.p["h"](None))
        new = n.l.get("new")
        grid = "group" not in n.l or new is not None
        if grid:
            _v.grid(n.key, w, h, go)
        if new is not None:
            self.ids["new"] = (n, None)
            if _v.card(new.label, "", {"id": "new", "icon": "new"})[4] & _v.ACTIVATED:
                self.fire(new, self.args_for(new), n, None)
        lst = n
        self._lists_now.add(n.key)
        model = self.s._v[n.l["model"]]
        self._after_removal(n, [r._f[r._m.key] for r in model])
        cap = None
        for r in model:
            if "group" in n.l:          # a caption where it changes, a grid of its own under it
                c = n.l["group"](r)
                if c != cap:
                    if grid:
                        _v.grid()
                    if cap is not None:
                        _v.group()
                    _v.group(c)
                    _v.grid(n.key + " " + c, w, h, go)
                    grid, cap = True, c
            for k in n.kids:
                o = self._o(k, r)
                if lst.src.get("current") and self.s._v[lst.src["current"]] == r._f[r._m.key]:
                    o["current"] = True
                badge = self._t(k, "badge", r)
                if badge:
                    o["badge"] = badge
                tone = self._t(k, "tone", r) if "tone" in k.p else ""
                if tone == "locked":
                    o["locked"] = True
                elif tone:
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
        if grid:
            _v.grid()
        if cap is not None:
            _v.group()
        _v.end()
        _v.scroll()

    def _r_picture(self, n, row, rect):
        """One image at its own place: a card with no words, its picture the
        image's or, while it has none, the icon on a tinted tile."""
        side = self._size(n.p["side"](row) if "side" in n.p else 64)
        pic = n.p["image"](row) if "image" in n.p else None
        got = self.pics.peek(pic, side, side) if pic is not None else None
        o = {"disabled": True}
        if got is None and "icon" in n.p:
            o["icon"] = n.p["icon"](row)
        tone = self._t(n, "tone", row) if "tone" in n.p else ""
        if tone == "locked":
            o["locked"] = True
        elif tone:
            o["tone"] = tone
        _v.grid("pic " + str(n.src.get("image", "")), side, side, {"cols": 1, "foot": -1, "top": 4,
                                                             "margin": 16 if self.desk else 8})
        r = _v.card("", "", o)
        if r[2] and got is not None:
            self.pics.draw(self.cv, got, r[0], r[1], r[2], r[3])
        _v.grid()

    def _r_progress(self, n, row, rect):
        v, hi = n.p["value"](row), n.p["max"](row)
        if n.l.get("look") == "count":
            # the label, where there is one, is the whole line: it says the
            # count in its own words ("{found} of {total} found")
            label = self._t(n, "label", row)
            _v.progress(label or ("%d of %d" % (v, hi) if hi else ""), v, hi, {"count": True})
            return
        _v.progress(self._t(n, "label", row), v, hi)

    def _r_empty(self, n, row, rect):
        _v.empty(self._t(n, "icon", row) or "warn", self._t(n, "title", row),
                 n.p["lines"](row) if "lines" in n.p else [])

    def _r_foot(self, n, row, rect):
        """The page's docked foot: its buttons at their natural widths at
        its end and a label before them; `look="share"`: its buttons share
        its width, its label a line of its own over them. On a handheld it
        lies outside the page, on its own ground under a rule along its top;
        on the desk it is the page's (`ui.dock`), on the page's ground."""
        x, y, w, h = self.foot_r
        desk, fs = self.desk, self.fs
        fh, pad, m = (30, 7, 16) if desk else (20 * fs, fs, 4 * fs)
        by = y + h - fh - pad
        if not desk:
            cv = self.cv
            cv.rect(x, y, w, h, self.th["panel"])
            cv.rect(x + m, y, w - 2 * m, 1, self.th["border"])
        btns = [k for k in n.kids if k.kind == "button" and _shown(k, row) and k.act.can(row)]
        if n.l.get("look") == "share":
            lab = self._foot_label(n)
            if lab is not None:
                _v.at(x + m, y + pad, w - 2 * m, by - y - pad)
                self._r_label(lab, row, rect)
            cells = _v.hsplit((x + m, by, w - 2 * m, fh), len(btns), 8 if desk else 4 * fs) if btns else ()
            for k, c in zip(btns, cells):
                self._r_button(k, row, rect, at=c)
            return
        bx = x + w - m
        for k in reversed(btns):
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

    @staticmethod
    def _draws_only(n):
        """A canvas with no pointer=, keys= or on=: it draws, and is no stop."""
        return not any(p in n.l or p in n.p for p in ("pointer", "keys", "on"))

    def _r_canvas(self, n, row, rect):
        o = {"id": n.key}
        if self._draws_only(n):
            o["disabled"] = True        # a canvas that only draws: no stop, no ring
        x, y, w, h, _st = _v.part(n.key, 0, n.l.get("h", 0), o)
        n.l["draw"](self.cv, x, y, w, h)

    # -- input --------------------------------------------------------------

    def keys(self, i, damage=True):
        """A frame's input: an action's letter or typed characters, then the
        host's keys (focus, the arrows, Enter). A host that draws its own
        frame for its keys (Settings) passes damage=False."""
        self._bind()
        ch = getattr(i, "typed", 0)
        if ch and not self.dialog_shown():
            c = chr(ch)
            for a in self.actions.values():
                if c.lower() in a.letters and a.can():
                    self.q.append((a, self.args_for(a), "key %r" % c))
                    break
                if c in a.chars and a.can():
                    self.q.append((a, (c,), "typed %r" % c))
                    break
        took = self._ui.keys(i)
        if not self.dialog_shown() and hasattr(self._ui, "inp"):
            took = self._arrow_roles(i) or took
        self._buttons = self._ui.inp[0] if hasattr(self._ui, "inp") else 0     # the host's begin consumes them
        if self.focused is None and not self.dialog_shown() and (i.pressed("a") or i.pressed("run")):
            a = self.role_action("confirm")
            if a is not None and a.can():
                self.q.append((a, self.args_for(a), "role confirm"))
        if damage and (took or self.q) or self.q:
            self._damage.all()
        return True

    def _arrow_roles(self, i):
        """An arrow whose role an action answers (§13.1: left, right, up,
        down) fires that action on its press and on each repeat, and the
        toolkit never sees it: the app owns the arrow, focus stays."""
        inp, fired = self._ui.inp, False
        for bit in range(4):
            role = S.ARROWS[bit]
            a = self.role_action(role)
            if a is None:
                continue
            down = inp[0] & (1 << bit)
            reps = (inp[_IN_REP] >> (8 * bit)) & 0xFF
            if (down or reps) and a.can():
                self.q.append((a, self.args_for(a), "role %s" % role))
                fired = True
            if down or reps:
                inp[0] &= ~(1 << bit)
                inp[1] &= ~(1 << bit)
                u = inp[_IN_REP] & 0xFFFFFFFF & ~(0xFF << (8 * bit))
                inp[_IN_REP] = u - 0x100000000 if u >> 31 else u     # the word is signed: no bigint
        return fired

    def point(self, px, py, click):
        self._bind()
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
        self._bind()
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
        a, why = self.role_action("back"), "role back"
        ad = self.decl.app
        if a is None and ad is not None and ad.back != "pages":
            a, why = self.actions[ad.back], "the app's back"
        if a is not None and a.can():
            self.q.append((a, self.args_for(a), why))
            self._damage.all()
            return True
        return False

    def trace_text(self):
        return S.dotted("\n".join(self.trace), lead=True)

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
        self.engine = Engine(S.decl_of(S.module_of(build)), build, ctx.ui, ctx.surface, ctx.damage,
                             getattr(ctx, "files", None), env=ctx)

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


def env():
    """What the host handed the engine running this handler: an app's
    AppContext, a bridged page's console or surface. A module's handlers
    reach the console's services through it, so two consoles in one process
    each reach their own."""
    a = _NOW.app
    if not isinstance(a, Engine) or _NOW.building:
        raise S.UIError("A10", "uitree_app.env() outside a handler",
                        fix="ask it in a handler or a hook; a build names state, it reads no service")
    return a.env


def app_layer(id, title, build, is_app=None, needs=()):
    """The AppLayer class of an app whose screen is `build` (the module's
    root page): what its manifest's `entry` names. Its title is the
    module's `ui.app(...)` declaration's (§19.1), else `title`."""
    if not callable(build):
        raise S.UIError("N01", "app_layer(%r): build is not a function" % id,
                        fix="pass the module's page function itself: app_layer(%r, %r, build)" % (id, title))
    ad = S.decl_of(S.module_of(build)).app
    if ad is None and title is None:
        raise S.UIError("D04", "app %r has no ui.app declaration" % id, where=(S.module_of(build), 0),
                        fix='ui.app("Title", icon="...") at the top of %s' % S.module_of(build))
    if ad is not None:
        if ad.back != "pages" and ad.back not in S.decl_of(S.module_of(build)).actions:
            raise S.UIError("A01", "ui.app back=%r names no action" % ad.back, where=ad.where,
                            fix='declare ui.action(%r, ...), or back="pages"' % ad.back)
        title = ad.title()
    return type(title.replace(" ", "") + "App", (App,), {
        "id": id, "TITLE": title, "BUILD": (build,), "IS_APP": (is_app,) if is_app else None,
        "NEEDS": App.NEEDS + tuple(needs)})


class Page(Engine):
    """A `uitree` page hosted by a surface still drawn with today's verbs
    (the bridge): the surface begins the frame on its own host and routes
    the keys to it; `paint` replays the page into its content rect. It goes
    with the old engine."""

    def __init__(self, build, host, damage, files=None, close=None, gen=False, env=None):
        Engine.__init__(self, S.decl_of(S.module_of(build)), build, host, None, damage, files, gen, env)
        self.close_root = close
