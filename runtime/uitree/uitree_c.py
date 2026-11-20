# Map (grep -n a name to jump there):
#   CEngine            the reference's Engine with its frame, focus and hints in C
#   CEngine.lower      the checked tree into the C store, one build call a node and a prop
#   CEngine.committed  the commit's writes pushed to C's slots
#   CEngine.paint      one frame: one call into C, then the events it queued
#   EngineGap          a kind, prop or form the C engine does not draw yet
"""The UI tree on the C engine (native/moy_uitree): the app's build function
runs through the reference's Builder (every check the reference makes), the
checked tree is lowered into the engine's store, and each frame is one call
into C that walks it through the toolkit's verbs. Python runs for handlers
only: the commit's writes are pushed to C's slots at its end, and the
engine's own writes (a toggle, a list's cursor) are pulled back before any
handler runs. Same app file, same trace, same pixels as `uitree_app.Engine`."""

import ctypes as _c

import ui as _v
import ui_draw as _D

import uitree as S
from uitree_app import Engine

try:
    from runtime import moy_uitree_binding as _B
    from runtime.moy_ui_face import moy_ui_sink_t as _Sink
except ImportError:         # pragma: no cover - a tier with no host library
    _B = None


class EngineGap(S.UIError):
    """What the C engine does not draw yet, by name: never drawn wrong."""

    def __init__(self, what):
        S.UIError.__init__(self, "E00", "the C engine does not draw %s yet" % what,
                           fix="run this app on the reference engine (uitree_app.Engine)")


_KINDS = {"page": 1, "group": 2, "label": 3, "info": 4, "link": 5, "button": 6, "toggle": 7,
          "rows": 8, "dialog": 9}
_HEAD = {"rows": ("model",), "dialog": ("buttons",)}
_EVB = 512


def _lib():
    if _B is None:
        raise EngineGap("on a tier with no host library")
    lib = _B.lib()
    if not getattr(lib, "_uit_sink", False):
        lib.moy_ui_sink_row.argtypes = [_c.POINTER(_Sink), _c.c_uint32, _c.c_int]
        lib.moy_ui_sink_row.restype = _c.c_int
        lib._uit_sink = True
    return lib


class CEngine(Engine):
    """`uitree_app.Engine` with the frame in C: the build, the checks and the
    handlers stay the reference's."""

    def __init__(self, *a, **k):
        Engine.__init__(self, *a, **k)
        self._L = lib = _lib()
        self._e = lib.uit_new()
        self._ev = (_c.c_int32 * 3)()
        self._buf = _c.create_string_buffer(_EVB)
        self._slot, self._names, self._types, self._pushed = {}, [], [], {}
        self._declare()

    def __del__(self):
        e, self._e = getattr(self, "_e", None), None
        if e and _B is not None:
            self._L.uit_free(e)

    def _ck(self, r, what):
        if r < 0:
            raise EngineGap("%s (%s)" % (what, self._L.uit_error(self._e).decode("utf-8", "replace")))
        return r

    # -- declarations ----------------------------------------------------------

    def _declare(self):
        L, e, st = self._L, self._e, self.s
        for name, t in st._t.items():
            if t is S.BOOL:
                ty, vals = _B.BOOL, None
            elif t is S.INT:
                ty, vals = _B.INT, None
            elif t is S.NUM:
                ty, vals = _B.NUM, None
            elif t is S.TEXT:
                ty, vals = _B.TEXT, None
            elif isinstance(t, S.Model):
                ty, vals = _B.MODEL, None
            elif isinstance(t, S.RowOf):
                ty, vals = _B.ROWOF, None
            elif type(t) is S.Enum:
                ty, vals = _B.ENUM, "\0".join(t.values).encode("utf-8")
            else:
                raise EngineGap("state %s of %r" % (name, t))
            slot = self._ck(L.uit_state(e, _B.b(name), ty, vals, len(vals) if vals else 0), "state " + name)
            self._slot[name] = slot
            self._names.append(name)
            self._types.append(t)
        for name, t in st._t.items():
            if isinstance(t, S.Model):
                fields = list(t.fields)
                types = bytes(self._ftype(t.fields[f], name) for f in fields)
                names = "\0".join(fields).encode("utf-8")
                self._ck(L.uit_model(e, self._slot[name], names, len(names), types, len(fields),
                                     fields.index(t.key)), "model " + name)
            elif isinstance(t, S.RowOf):
                L.uit_row_of(e, self._slot[name], self._slot[t.model])
        self._acts = list(self.actions.values())
        self._aidx = {}
        for a in self._acts:
            if a.row is not None or a.chars or a.unit is not None:
                raise EngineGap("action %s's row=, chars= or unit" % a.name)
            flags = (_B.A_HOOK if a.hook else 0) | (_B.A_ROLE_CONFIRM if "role:confirm" in a.keys else 0) | (
                _B.A_ROLE_BACK if "role:back" in a.keys else 0)
            hint = a.hint if isinstance(a.hint, str) else ""
            off = {"hide": 1, "grey": 2}.get(getattr(a, "off", None), 0)
            self._aidx[a.name] = self._ck(L.uit_action(
                e, _B.b(a.name), _B.b(a.label or ""), _B.b(hint), _B.b(a.when_src) if a.when_src else None,
                off, flags, ord(a.letters[0]) if a.letters else 0), "action " + a.name)
        for name in self._names:
            self._push(name)

    @staticmethod
    def _ftype(t, model):
        for k, v in ((S.BOOL, _B.BOOL), (S.INT, _B.INT), (S.NUM, _B.NUM), (S.TEXT, _B.TEXT)):
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
        L, e, st = self._L, self._e, self.s
        slot, t, v = self._slot[name], self._types[self._slot[name]], self._value(name)
        self._pushed[name] = st._ver.get(name, 0)
        if isinstance(t, S.Model):
            cells = []
            for r in v:
                for f, ft in t.fields.items():
                    x = r._f[f]
                    cells.append(("1" if x else "0") if ft is S.BOOL else repr(x) if ft is S.NUM else str(x))
            raw = ("\0".join(cells) + "\0").encode("utf-8") if cells else b""
            self._ck(L.uit_set_model(e, slot, len(v), raw, len(raw)), "model " + name)
            self._drain()
        elif t is S.BOOL or t is S.INT:
            L.uit_set_int(e, slot, int(bool(v)) if t is S.BOOL else int(v))
        elif t is S.NUM:
            L.uit_set_num(e, slot, float(v))
        else:
            raw = ("" if v is None else str(v)).encode("utf-8")
            L.uit_set_text(e, slot, raw, len(raw))

    def committed(self):
        """The end of a commit: each slot whose version moved, pushed."""
        ver, pushed = self.s._ver, self._pushed
        for name in self._names:
            if pushed.get(name) != ver.get(name, 0):
                self._push(name)

    def settle(self):
        """The removal rule runs in C, when a model is pushed (`_push`)."""

    def _pull(self, slot):
        L, e, st = self._L, self._e, self.s
        name, t = self._names[slot], self._types[slot]
        if t is S.BOOL:
            v = bool(L.uit_get_int(e, slot))
        elif t is S.INT:
            v = int(L.uit_get_int(e, slot))
        else:
            n = _c.c_size_t()
            p = L.uit_get_text(e, slot, _c.byref(n))
            v = _c.string_at(p, n.value).decode("utf-8") if p else ""
        st._v[name] = v
        st._ver[name] += 1
        self._pushed[name] = st._ver[name]

    def _drain(self):
        """The engine's trace lines, then its events: writes pulled, actions queued."""
        L, e, buf, ev = self._L, self._e, self._buf, self._ev
        while L.uit_trace_next(e, buf, _EVB) >= 0:
            kind, name, text = buf.value.decode("utf-8", "replace").split("\x01", 2)
            was, S._NOW.app = S._NOW.app, self
            try:
                S._trace(kind, name, text)
            finally:
                S._NOW.app = was
        while True:
            k = L.uit_next(e, ev, buf, _EVB)
            if k == _B.EV_NONE:
                return
            if k == _B.EV_WRITE:
                self._pull(ev[0])
            elif k == _B.EV_ACTION:
                act = self._acts[ev[0]]
                row = self.s._v[self._names[ev[1]]][ev[2]] if ev[1] >= 0 and ev[2] >= 0 else None
                src, _, fid = buf.value.decode("utf-8", "replace").partition("\x02")
                if fid:
                    self.fired_id = fid
                self.q.append((act, self.args_for(act, row), src))

    # -- the build ----------------------------------------------------------------

    def _ready(self):
        if self.root is None:
            self._build()
            self.lower(self.root)

    def lower(self, root):
        """The checked tree into the store: one node call a node, one prop call
        a prop, in the order the build gave them."""
        L, e = self._L, self._e
        L.uit_build_begin(e, _B.b(self._page_id()))

        def node(n, parent):
            kind = _KINDS.get(n.kind)
            if kind is None:
                raise EngineGap("a %s node" % n.kind)
            if n.l.get("field"):
                raise EngineGap("a %s bound to a row's field" % n.kind)
            me = self._ck(L.uit_node(e, kind, _B.b(n.key), parent), "node %s" % n.key)
            if n.kind == "toggle":
                self._ck(L.uit_prop(e, me, b"bind", _B.b(n.key), None), "toggle " + n.key)
            elif n.kind == "rows":
                self._ck(L.uit_prop(e, me, b"bind", _B.b(n.l["model"]), None), "rows " + n.key)
            for k, src in n.src.items():
                if k in _HEAD.get(n.kind, ()):
                    continue
                v = n.l.get(k)
                if hasattr(v, "fn") and hasattr(v, "takes"):        # an action named by a prop
                    src = v.name
                elif not isinstance(src, str):
                    if isinstance(src, bool):
                        src = "1" if src else "0"
                    else:
                        raise EngineGap("%s %s=%r" % (n.kind, k, src))
                self._ck(L.uit_prop(e, me, _B.b(k), _B.b(src), _B.b(repr(v)) if k in n.l else None),
                         "%s %s=%r" % (n.kind, k, src))
            for k in n.l:
                if k not in n.src and k not in ("model", "buttons", "field") and n.l[k] is not None:
                    raise EngineGap("%s %s=" % (n.kind, k))
            if n.act is not None:
                L.uit_node_action(e, me, self._aidx[n.act.name])
            if n.kind == "dialog":
                for a in n.l["buttons"]:
                    L.uit_node_action(e, me, self._aidx[a.name])
            for k in n.kids:
                node(k, me)
        node(root, -1)
        self._ck(L.uit_build_end(e), "the build")

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
        L, e = self._L, self._e
        if self.want_focus is not None:
            L.uit_want_focus(e, _B.b(self.want_focus))
            self.want_focus = None
        kind = _v.input_kind()
        self.input = kind if kind in S.INPUTS else "keys"
        b = self._buttons
        flags = ((_B.F_DESK if desk else 0) | (_B.F_ON_BAR if _D._ON_BAR[0] else 0)
                 | (_B.F_BAND if self.band else 0) | (_B.F_BARE if self.bare else 0)
                 | (_B.F_TRACE if S.TRACING else 0) | (_B.F_UP if b & 4 else 0) | (_B.F_DOWN if b & 8 else 0)
                 | (min(255, len(self.pages)) << _B.F_PAGES_SHIFT))
        t = _D.UI_TARGET
        ctx, keep = _B.ctx_ptr(L, t[0])
        h, queue = cv._ui
        if queue[0] > 4:
            cv.flush_batch()
        sink = _Sink()
        L.moy_ui_sink_row(_c.byref(sink), int(h), 0 if t[2] is None else int(t[2]))
        r = L.uit_frame(e, ctx, _c.byref(sink), int(content[0]), int(content[1]), int(content[2]),
                        int(content[3]), flags, fs, _B.INPUTS.index(self.input))
        del keep
        self._buttons = 0
        self._drain()
        if r & _B.OWE:
            self._damage.all()
        q = self.q
        if q:
            self.q = []
            for act, args, src in q:
                self.run_action(act, args, src)
        S.commit(self)
        self._works()
        if S.TRACING:
            self.frame_calls.append(self.calls)
        if q or self.timers or self.work_run or self.decl.posts:
            self._damage.all()

    # -- asked between frames ----------------------------------------------------------

    def open(self, arg=None, hook="opened"):
        self._L.uit_open(self._e)
        Engine.open(self, arg, hook)

    def dialog_shown(self):
        return self.root is not None and self._L.uit_dialog(self._e) >= 0

    def keys(self, i, damage=True):
        self._bind()
        ch = getattr(i, "typed", 0)
        if ch and not self.dialog_shown():
            c = chr(ch)
            for a in self.actions.values():
                if c.lower() in a.letters and a.can():
                    self.q.append((a, self.args_for(a), "key %r" % c))
                    break
        took = self._ui.keys(i)
        if not self.dialog_shown() and hasattr(self._ui, "inp"):
            took = self._arrow_roles(i) or took
        self._buttons = self._ui.inp[0] if hasattr(self._ui, "inp") else 0
        if self._L.uit_unfocused(self._e) and not self.dialog_shown() and (i.pressed("a") or i.pressed("run")):
            a = self.role_action("confirm")
            if a is not None and a.can():
                self.q.append((a, self.args_for(a), "role confirm"))
        if damage and (took or self.q) or self.q:
            self._damage.all()
        return True

    def dump(self):
        """The tree as the engine holds it (the reference's snapshot)."""
        n = self._L.uit_dump(self._e, None, 0)
        buf = _c.create_string_buffer(n + 1)
        self._L.uit_dump(self._e, buf, n + 1)
        return buf.value.decode("utf-8")

    def stats(self):
        out = (_c.c_int32 * 8)()
        self._L.uit_stats(self._e, out)
        return dict(zip(("nodes", "node_bytes", "tmpl_bytes", "cond_bytes", "pool_bytes", "prop_bytes",
                         "state_bytes", "engine_bytes"), out))
