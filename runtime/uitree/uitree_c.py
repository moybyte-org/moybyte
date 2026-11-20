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
EV_NONE, EV_ACTION, EV_WRITE = 0, 1, 2
F_DESK, F_ON_BAR, F_BAND, F_BARE, F_TRACE, F_UP, F_DOWN, F_GEN = 1, 2, 4, 8, 16, 32, 64, 128
F_PAGES_SHIFT = 8
OWE = 1 << 16
A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK = 1, 2, 4
INPUTS = ("keys", "tdeck", "pad", "touch")
_KINDS = {"page": 1, "group": 2, "label": 3, "info": 4, "link": 5, "button": 6, "toggle": 7,
          "rows": 8, "dialog": 9, "seg": 10, "slider": 11, "field": 12}
_HEAD = {"rows": ("model",), "dialog": ("buttons",)}
_BOUND = ("toggle", "seg", "slider", "field")       # a two-way control: bound to the state of its key
_LIT_ONLY = {"seg": ("values", "words", "model", "labels")}   # what the reference keeps beside a node's props
_TYPED = "typed in field "
LOWERED = "lowered"         # an engine's root once its page is in the store: the Python tree is gone


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
        self._slot, self._names, self._types, self._pushed = {}, [], [], {}
        self._declare()

    def _ck(self, r, what):
        if r < 0:
            raise EngineGap("%s (%s)" % (what, self._x.error()))
        return r

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
            if a.row is not None or a.chars or a.unit is not None:
                raise EngineGap("action %s's row=, chars= or unit" % a.name)
            flags = (A_HOOK if a.hook else 0) | (A_ROLE_CONFIRM if "role:confirm" in a.keys else 0) | (
                A_ROLE_BACK if "role:back" in a.keys else 0)
            off = {"hide": 1, "grey": 2}.get(getattr(a, "off", None), 0)
            self._aidx[a.name] = self._ck(x.action(
                a.name, a.label or "", a.hint if isinstance(a.hint, str) else "", a.when_src, off, flags,
                a.letters), "action " + a.name)
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
            elif k == EV_ACTION:
                act = self._acts[a]
                row = self.s._v[self._names[m]][r] if m >= 0 and r >= 0 else None
                src, _, fid = text.partition("\x02")
                if fid:
                    self.fired_id = fid
                self.q.append((act, self.args_for(act, row), src))

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
        a prop, in the order the build gave them."""
        x = self._x
        x.build_begin(self._page_id())

        def node(n, parent):
            kind = _KINDS.get(n.kind)
            if kind is None:
                raise EngineGap("a %s node" % n.kind)
            if n.l.get("field"):
                raise EngineGap("a %s bound to a row's field" % n.kind)
            me = self._ck(x.node(kind, n.key, parent), "node %s" % n.key)
            if n.kind in _BOUND:
                self._ck(x.prop(me, "bind", n.key, None), "%s %s" % (n.kind, n.key))
            if n.kind == "seg" and "labels" in n.l:
                self._ck(x.prop(me, "labels", n.l["labels"], None), "seg %s labels" % n.key)
            elif n.kind == "rows":
                self._ck(x.prop(me, "bind", n.l["model"], None), "rows " + n.key)
            for k, src in n.src.items():
                if k in _HEAD.get(n.kind, ()):
                    continue
                v = n.l.get(k)
                rep = repr(v) if k in n.l else None
                if hasattr(v, "fn") and hasattr(v, "takes"):        # an action named by a prop
                    src = v.name
                elif type(src) is int or type(src) is bool:        # a literal number or flag
                    src, rep = str(src), repr(src)
                elif not isinstance(src, str):
                    raise EngineGap("%s %s=%r" % (n.kind, k, src))
                self._ck(x.prop(me, k, src, rep), "%s %s=%r" % (n.kind, k, src))
            lits = _LIT_ONLY.get(n.kind, ())
            for k in n.l:
                if k not in n.src and k not in ("model", "buttons", "field") and k not in lits and \
                        n.l[k] is not None:
                    raise EngineGap("%s %s=" % (n.kind, k))
            if n.act is not None:
                x.node_action(me, self._aidx[n.act.name])
            if n.kind == "dialog":
                for a in n.l["buttons"]:
                    x.node_action(me, self._aidx[a.name])
            for k in n.kids:
                node(k, me)
        node(root, -1)
        self._ck(x.build_end(), "the build")

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

