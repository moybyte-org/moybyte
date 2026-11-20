# Map (grep -n a name to jump there):
#   CEngine            the reference's Engine with its frame, focus and hints in C
#   CEngine.lower      the checked tree into the C store, one build call a node and a prop
#   CEngine.committed  the commit's writes pushed to C's slots
#   CEngine.paint      one frame: one call into C, then the events it queued
#   EngineGap          a kind, prop or form the C engine does not draw yet
#   CPage              a hosted page (uitree_app.Page) on the C engine
"""The UI tree on the C engine (native/moy_uitree): the app's build function
runs through the reference's Builder (every check the reference makes), the
checked tree is lowered into the engine's store, and each frame is one call
into C that walks it through the toolkit's verbs. Python runs for handlers
only: the commit's writes are pushed to C's slots at its end, and the
engine's own writes (a toggle, a list's cursor) are pulled back before any
handler runs. Same app file, same trace, same pixels as `uitree_app.Engine`."""

import ui as _v
import ui_draw as _D

import uitree as S
from uitree_app import Engine, Page

try:
    from moy_uitree import Engine as _New        # the board's module, or the host's face of it
except ImportError:         # pragma: no cover - a tier with no engine
    _New = None


class EngineGap(S.UIError):
    """What the C engine does not draw yet, by name: never drawn wrong."""

    def __init__(self, what):
        S.UIError.__init__(self, "E00", "the C engine does not draw %s yet" % what,
                           fix="run this app on the reference engine (uitree_app.Engine)")


# the engine's numbers (native/moy_uitree/moy_uitree.h)
BOOL, INT, NUM, TEXT, ENUM, ROWOF, MODEL = 1, 2, 3, 4, 5, 6, 7
EV_NONE, EV_ACTION, EV_WRITE, EV_CELL = 0, 1, 2, 3
F_DESK, F_ON_BAR, F_BAND, F_BARE, F_TRACE, F_UP, F_DOWN, F_GEN = 1, 2, 4, 8, 16, 32, 64, 128
F_PAGES_SHIFT = 8
OWE = 1 << 16
A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK = 1, 2, 4
INPUTS = ("keys", "tdeck", "pad", "touch")
_KINDS = {"page": 1, "group": 2, "label": 3, "info": 4, "link": 5, "button": 6, "toggle": 7,
          "rows": 8, "dialog": 9, "seg": 10, "slider": 11, "field": 12, "empty": 13,
          "progress": 14, "text_view": 15,
          "scroll": 16, "menu": 17, "item": 18, "split": 19,
          "canvas": 20, "tabs": 21, "switch": 22, "case": 23, "table": 24, "col": 25, "foot": 26}
_HEAD = {"rows": ("model",), "table": ("model",), "dialog": ("buttons",)}
_KIND_NAMES = dict((v, k) for k, v in _KINDS.items())
_KIND_NAMES[len(_KINDS) + 1] = "cycle"          # UIT_KINDS: a seg of the cycle look
_BOUND = ("toggle", "seg", "slider", "field", "text_view", "tabs")       # a two-way control: bound to the state of its key
_LIT_ONLY = {"seg": ("values", "words", "model", "labels"), "text_view": ("name",),
             "switch": ("name",), "case": ("v",), "tabs": ("labels", "values")}    # a switch's and a case's: in their keys   # what the reference keeps beside a node's props
_TYPED = "typed in field "
LOWERED = "lowered"         # an engine's root once its page is in the store: the Python tree is gone
E_CHECK = -6                # UIT_E_CHECK: a mistake the spec names, its code `error_code()`
E_GAP = -3                  # UIT_E_KIND: what this engine does not draw yet
# The build mode of the engines made after it is set. "direct": the build's
# calls keep each template's and condition's source and C compiles it at
# lowering, raising the spec's codes (T01-T05, C01-C02) from its compiler.
# "checked": the reference's Builder compiles and checks them first, as the
# host's checker does.
BUILD = "direct"
# each code C's compiler raises, with the fix the contract gives it
FIXES = {"T01": "the template's syntax: {name}, {name|fmt %d}, {cond?a:b}; '{{' for a brace, '\\:' for a colon "
                "in a branch",
         "T02": "name a declared state, a field of the rows, a system value or a label table",
         "T03": "name a value of the type the site takes",
         "T04": "a label for each value of the enum, True and False for a bool, else \"_\"",
         "T05": "{sel.field} outside a list; {.field} and @current inside ui.rows",
         "C01": 'a bare condition: shown="on", "!busy", "mode=edit", "a & !b"',
         "C02": "a condition needing & and | is a derive, named here",
         "N04": "a size class: @wide, @desk, @tall, @handheld"}


class CEngine(Engine):
    """`uitree_app.Engine` with the frame in C: the build, the checks and the
    handlers stay the reference's."""

    def __init__(self, *a, **k):
        Engine.__init__(self, *a, **k)
        self._c_init()

    def _c_init(self):
        if _New is None:
            raise EngineGap("on a tier with no moy_uitree")
        self._x = _New()
        self.direct = BUILD == "direct"
        self._slot, self._names, self._types, self._pushed = {}, [], [], {}
        self._draws, self._drawing = {}, False
        self._declare()

    def _ck(self, r, what, where=None):
        if r < 0:
            self._fail(r, what, where)
        return r

    def _fail(self, r, what, where):
        """A refusal of C's: a mistake the spec names (UIError, its code), or
        what this engine does not draw yet (EngineGap)."""
        if r == E_CHECK:
            code = self._x.error_code()
            raise S.UIError(code, "%s: %s" % (what, self._x.error()), where=where or ("?", 0), fix=FIXES[code])
        raise EngineGap("%s (%s)" % (what, self._x.error()))

    # -- declarations ----------------------------------------------------------

    def _declare(self):
        x, st = self._x, self.s
        for name, t in st._t.items():
            vals = None
            if t is S.BOOL:
                ty = BOOL
            elif t is S.INT:
                ty = INT
            elif t is S.NUM:
                ty = NUM
            elif t is S.TEXT:
                ty = TEXT
            elif isinstance(t, S.Model):
                ty = MODEL
            elif isinstance(t, S.RowOf):
                ty = ROWOF
            elif type(t) is S.Enum:
                ty, vals = ENUM, "\0".join(t.values).encode("utf-8")
            else:
                raise EngineGap("state %s of %r" % (name, t))
            self._slot[name] = self._ck(x.state(name, ty, vals), "state " + name)
            self._names.append(name)
            self._types.append(t)
        for name, t in st._t.items():
            if isinstance(t, S.Model):
                fields = list(t.fields)
                types = bytes([self._ftype(t.fields[f], name) for f in fields])
                self._ck(x.model(self._slot[name], "\0".join(fields).encode("utf-8"), types,
                                 fields.index(t.key)), "model " + name)
            elif isinstance(t, S.RowOf):
                x.row_of(self._slot[name], self._slot[t.model])
        for name, tab in self.decl.labels.items():
            keys = "\0".join([str(k) for k in tab]).encode("utf-8")
            words = "\0".join([str(tab[k]) for k in tab]).encode("utf-8")
            self._ck(x.labels(name, keys, words), "labels " + name)
        self._acts = list(self.actions.values())
        self._aidx = {}
        for a in self._acts:
            if a.chars or a.unit is not None:
                raise EngineGap("action %s's chars= or unit" % a.name)
            flags = (A_HOOK if a.hook else 0) | (A_ROLE_CONFIRM if "role:confirm" in a.keys else 0) | (
                A_ROLE_BACK if "role:back" in a.keys else 0)
            off = {"hide": 1, "grey": 2}.get(getattr(a, "off", None), 0)
            self._aidx[a.name] = self._ck(x.action(
                a.name, a.label or "", a.hint if isinstance(a.hint, str) else "", a.when_src, off, flags,
                a.letters, a.icon or ""), "action " + a.name)
            if a.row is not None:
                self._ck(x.action_row(self._aidx[a.name], self._slot[a.row]), "action %s row=" % a.name)
        for name in self._names:
            self._push(name)

    @staticmethod
    def _ftype(t, model):
        for k, v in ((S.BOOL, BOOL), (S.INT, INT), (S.NUM, NUM), (S.TEXT, TEXT)):
            if t is k:
                return v
        raise EngineGap("a field of %r in model %s" % (t, model))

    # -- state across the line --------------------------------------------------

    def _value(self, name):
        st = self.s
        if name in st._derive:
            return st._derive[name]()
        return st._v[name]

    def _push(self, name):
        x, st = self._x, self.s
        slot = self._slot[name]
        t, v = self._types[slot], self._value(name)
        self._pushed[name] = st._ver.get(name, 0)
        if isinstance(t, S.Model):
            cells = []
            for r in v:
                for f, ft in t.fields.items():
                    c = r._f[f]
                    cells.append(("1" if c else "0") if ft is S.BOOL else repr(c) if ft is S.NUM else str(c))
            raw = ("\0".join(cells) + "\0").encode("utf-8") if cells else b""
            self._ck(x.set_model(slot, len(v), raw), "model " + name)
            self._drain()
        elif t is S.BOOL or t is S.INT:
            x.set_int(slot, int(bool(v)) if t is S.BOOL else int(v))
        elif t is S.NUM:
            x.set_num(slot, float(v))
        else:
            x.set_text(slot, "" if v is None else str(v))

    def committed(self):
        """The end of a commit: each slot whose version moved, pushed."""
        ver, pushed = self.s._ver, self._pushed
        for name in self._names:
            if pushed.get(name) != ver.get(name, 0):
                self._push(name)

    def settle(self):
        """The removal rule runs in C, when a model is pushed (`_push`)."""

    def _pull(self, slot):
        x, st = self._x, self.s
        name, t = self._names[slot], self._types[slot]
        if t is S.BOOL:
            v = bool(x.get_int(slot))
        elif t is S.INT:
            v = x.get_int(slot)
        else:
            v = x.get_text(slot)
        st._v[name] = v
        st._ver[name] += 1
        self._pushed[name] = st._ver[name]

    def _pull_cell(self, slot, ri, fi):
        """A row's field the engine wrote (a toggle bound to `.field`): the
        row made again with it, as the reference's `_row_write` makes it."""
        st = self.s
        name, t = self._names[slot], self._types[slot]
        f = list(t.fields)[fi]
        ft, raw = t.fields[f], self._x.get_cell(slot, ri, fi)
        v = raw == "1" if ft is S.BOOL else int(raw) if ft is S.INT else float(raw) if ft is S.NUM else raw
        rows = st._v[name]
        old = rows[ri]
        d = dict(old._f)
        d[f] = v
        new = S.Row(old._m, d)
        st._v[name] = tuple(new if r is old else r for r in rows)
        st._ver[name] += 1
        self._pushed[name] = st._ver[name]

    def _drain(self):
        """The engine's trace lines, then its events: writes pulled, actions queued."""
        x = self._x
        while True:
            line = x.trace_next()
            if line is None:
                break
            kind, name, text = line.split("\x01", 2)
            tag = text.rfind("(" + _TYPED)
            if kind == "state" and tag > 0 and self.trace and self.trace[-1].endswith(text[tag:]):
                # one line per run of typing in a field: the run's first value -> its last
                old = self.trace.pop().split("  state   ")[1][max(8, len(name)) + 1:].split(" -> ")[0]
                text = old + text[text.index(" -> "):]
            was, S._NOW.app = S._NOW.app, self
            try:
                S._trace(kind, name, text)
            finally:
                S._NOW.app = was
        while True:
            ev = x.next()
            if ev is None:
                return
            k, a, m, r, text = ev
            if k == EV_WRITE:
                self._pull(a)
            elif k == EV_CELL:
                self._pull_cell(a, m, r)
            elif k == EV_ACTION:
                act = self._acts[a]
                row = self.s._v[self._names[m]][r] if m >= 0 and r >= 0 else None
                src, _, fid = text.partition("\x02")
                if fid:
                    self.fired_id = fid
                if row is None and act.row is not None:     # row=: the row its row_of names (the app's state)
                    was, S._NOW.app = S._NOW.app, self
                    try:
                        args = self.args_for(act)
                    finally:
                        S._NOW.app = was
                else:
                    args = self.args_for(act, row)
                self.q.append((act, args, src))

    # -- the build ----------------------------------------------------------------

    def _ready(self):
        """The page built and checked by the reference's Builder, lowered into
        the store, and the Python tree let go: `root` is then `LOWERED`."""
        if self.root is None:
            self._build()
            self.lower(self.root)
            self.root = LOWERED

    def lower(self, root):
        """The checked tree into the store: one node call a node, one prop call
        a prop, in the order the build gave them. Nothing is formatted but
        for a refusal."""
        x = self._x
        x.build_begin(self._page_id())
        draws = self._draws = {}
        if not self._drawing:
            x.on_draw(self._draw)
            self._drawing = True
        cnode, cprop, cact, aidx, fail = x.node, x.prop, x.node_action, self._aidx, self._fail
        kinds, bound, heads, lits_only, action = _KINDS, _BOUND, _HEAD, _LIT_ONLY, S.Action

        def node(n, parent):
            nk, l = n.kind, n.l
            kind = kinds.get(nk)
            if kind is None:
                raise EngineGap("a %s node" % nk)
            field = l.get("field") if l and nk != "text_view" else None
            if field and nk != "toggle":
                raise EngineGap("a %s bound to a row's field" % nk)
            me = cnode(kind, n.key, parent)
            if me < 0:
                fail(me, "node %s" % n.key, n.where)
            if field:
                r = cprop(me, "field", field, None)
                if r >= 0 and "on" in l:
                    r = cprop(me, "on", l["on"].name, repr(l["on"]))
                if r < 0:
                    fail(r, "%s .%s" % (nk, field), n.where)
            elif nk in bound:
                r = cprop(me, "bind", n.key, None)
                if r >= 0 and nk == "text_view" and "field" in l:
                    r = cprop(me, "field", l["field"], None)       # a model's lines: this text field
                if r < 0:
                    fail(r, "%s %s" % (nk, n.key), n.where)
            if nk == "seg" and "labels" in l:
                r = cprop(me, "labels", l["labels"], None)
                if r < 0:
                    fail(r, "seg %s labels" % n.key, n.where)
            elif nk == "rows" or nk == "table":
                r = cprop(me, "bind", l["model"], None)
                if r < 0:
                    fail(r, "%s %s" % (nk, n.key), n.where)
            head = heads.get(nk, ())
            for k, src in n.src.items():
                if k in head:
                    continue
                if k == "draw" and nk == "canvas":         # the front end's function: called inside the frame
                    draws[me] = src
                    r = cprop(me, "draw", getattr(src, "__name__", "draw"), None)
                    if r < 0:
                        fail(r, "canvas %s draw=" % n.key, n.where)
                    continue
                rep = None
                if k in l:
                    v = l[k]
                    if type(v) is action:              # an action named by a prop
                        src = v.name
                    if type(v) is action or not callable(v):     # a compiled template keeps no literal
                        rep = repr(v)
                if type(src) is str:
                    pass
                elif k == "cols" and nk == "table":
                    for h in src:           # one prop a column's header
                        r = cprop(me, k, str(h), None)
                        if r < 0:
                            fail(r, "ui.table cols=%r" % (src,), n.where)
                    continue
                elif (type(src) is list or type(src) is tuple) and S.SCHEMA[nk].get(k) == "texts":
                    for line in src:        # one prop a line
                        r = cprop(me, k, line, None) if type(line) is str else E_GAP
                        if r < 0:
                            fail(r, "ui.%s %s=%r" % (nk, k, line), n.where)
                    continue
                elif type(src) is bool and S.SCHEMA[nk].get(k) == "cond":
                    raise EngineGap("%s %s=%r: a literal condition" % (nk, k, src))
                elif type(src) is tuple and S.SCHEMA[nk].get(k) == "ints":     # sizes: ints and weights
                    src, rep = " ".join([str(v) for v in src]), repr(src)
                elif type(src) is int or type(src) is bool:        # a literal number or flag
                    src, rep = str(src), repr(src)
                elif not isinstance(src, str):
                    raise EngineGap("%s %s=%r" % (nk, k, src))
                r = cprop(me, k, src, rep)
                if r < 0:
                    fail(r, "ui.%s %s=%r" % (nk, k, src), n.where)
            if l:
                lits = lits_only.get(nk, ())
                for k in l:
                    if k not in n.src and k not in ("model", "buttons", "field", "on") and k not in lits and \
                            l[k] is not None:
                        raise EngineGap("%s %s=" % (nk, k))
            if n.act is not None:
                cact(me, aidx[n.act.name])
            if nk == "dialog":
                for a in l["buttons"]:
                    cact(me, aidx[a.name])
            for k in n.kids:
                node(k, me)
        node(root, -1)
        self._ck(x.build_end(), "the build")

    def _draw(self, node, x, y, w, h):
        """A canvas's draw, called by the walk inside the frame at its rect."""
        now = S._NOW
        was = now.app, now.frame
        now.app, now.frame = self, True
        try:
            self._draws[node](self.cv, x, y, w, h)
        finally:
            now.app, now.frame = was

    # -- the frame -------------------------------------------------------------------

    def paint(self, dt, cv, th, content, desk):
        self._bind()
        if not self._started:
            self._start(dt)
        self._started = False
        self.cv, self.th = cv, th
        self.content = content
        self.desk = desk
        self.fs = fs = max(1, getattr(cv, "font_scale", 1))
        self._ready()
        x = self._x
        if self.want_focus is not None:
            x.want_focus(self.want_focus)
            self.want_focus = None
        kind = _v.input_kind()
        self.input = kind if kind in S.INPUTS else "keys"
        b = self._buttons
        flags = ((F_DESK if desk else 0) | (F_ON_BAR if _D._ON_BAR[0] else 0)
                 | (F_BAND if self.band else 0) | (F_BARE if self.bare else 0) | (F_GEN if self._gen_on else 0)
                 | (F_TRACE if S.TRACING else 0) | (F_UP if b & 4 else 0) | (F_DOWN if b & 8 else 0)
                 | (min(255, len(self.pages)) << F_PAGES_SHIFT))
        t = _D.UI_TARGET
        r = x.frame(t[0], cv, t[2], content[0], content[1], content[2], content[3], flags, fs,
                    INPUTS.index(self.input))
        self._buttons = 0
        if r & 0xffff or S.TRACING:
            self._drain()
        if r & OWE:
            self._damage.all()
        q = self.q
        if q:
            self.q = []
            for act, args, src in q:
                self.run_action(act, args, src)
        if q or r & 0xffff:
            S.commit(self)          # a two-way control's write with no hook moves a derive too
        if self.decl.works:
            self._works()
        if S.TRACING:
            self.frame_calls.append(self.calls)
        if q or self.timers or self.work_run or self.decl.posts:
            self._damage.all()

    # -- asked between frames ----------------------------------------------------------

    def open(self, arg=None, hook="opened"):
        self._x.open()
        Engine.open(self, arg, hook)

    def dialog_shown(self):
        return self.root is not None and self._x.dialog() >= 0

    def _focus_key(self):
        """The id focus is on after the last frame ("" none): the engine's."""
        x = getattr(self, "_x", None)
        return x.focused() if x is not None else ""

    def _no_set(self, v):
        pass                    # the Builder's initial ""; the engine keeps the key

    focus_key = property(_focus_key, _no_set)

    def draft(self, key):
        return self._x.draft(key)

    def stop_kinds(self):
        out, i = {}, 0
        while True:
            got = self._x.stop(i)
            if got is None:
                return out
            out[got[1]] = _KIND_NAMES[got[0]]
            i += 1

    def leave(self):
        """Every way out of the page: each drawn commit field's draft is
        written, in C; its writes pulled and its changed= run here."""
        self._bind()
        self._x.leave()
        self._drain()
        q, self.q = self.q, []
        for act, args, src in q:
            self.run_action(act, args, src)
        S.commit(self)

    def keys(self, i, damage=True):
        self._bind()
        ch = getattr(i, "typed", 0)
        a = self._x.letter(ch) if ch else -1
        if a >= 0:
            act = self._acts[a]
            self.q.append((act, self.args_for(act), "key %r" % chr(ch)))
        took = self._ui.keys(i)
        if not self.dialog_shown() and hasattr(self._ui, "inp"):
            took = self._arrow_roles(i) or took
        self._buttons = self._ui.inp[0] if hasattr(self._ui, "inp") else 0
        if self._x.unfocused() and not self.dialog_shown() and (i.pressed("a") or i.pressed("run")):
            a = self.role_action("confirm")
            if a is not None and a.can():
                self.q.append((a, self.args_for(a), "role confirm"))
        if damage and (took or self.q) or self.q:
            self._damage.all()
        return True

    def dump(self):
        """The tree as the engine holds it (the reference's snapshot)."""
        return self._x.dump()

    snapshot = dump

    def _has_page(self):
        return len(self.pages) > 1 or self.dialog_shown()

    def _back(self):
        """Back: a shown dialog's first button (in C), else the page under this
        one, else the app's back role or its declared back action."""
        self._bind()
        if self.root is not None and self._x.dialog() >= 0:
            got = self._x.back_dialog()
            if got != -1:
                act = self._acts[got[0]]
                self.q.append((act, self.args_for(act), "back on dialog %r" % got[1]))
            self._damage.all()
            return True
        if len(self.pages) > 1:
            self.pages.pop()
            self.root = None
            back_to = self.openers.pop() if getattr(self, "openers", None) else None
            self.want_focus = back_to
            was, S._NOW.app = S._NOW.app, self
            S._trace("page", "back", "page %d, focus back to %r" % (len(self.pages), back_to))
            S._NOW.app = was
            return True
        a, why = self.role_action("back"), "role back"
        ad = self.decl.app
        if a is None and ad is not None and ad.back != "pages":
            a, why = self.actions[ad.back], "the app's back"
        if a is not None and self._x.can(self._aidx[a.name], -1):
            self.q.append((a, self.args_for(a), why))
            self._damage.all()
            return True
        return False

    def stats(self):
        return dict(zip(("nodes", "node_bytes", "tmpl_bytes", "cond_bytes", "pool_bytes", "prop_bytes",
                         "state_bytes", "engine_bytes"), self._x.stats()))


class CPage(CEngine, Page):
    """`uitree_app.Page` (a page a surface on today's verbs hosts) with the
    frame in C."""

    def __init__(self, build, host, damage, files=None, close=None, gen=False, env=None):
        Page.__init__(self, build, host, damage, files, close, gen, env)
        self._c_init()

