# Map (grep -n a name to jump there):
#   CEngine            the reference's Engine with its frame, focus and hints in C
#   CEngine.lower      the checked tree into the C store, one build call a node and a prop
#   CEngine.committed  the commit's writes pushed to C's slots
#   CEngine.paint      one frame: one call into C, then the events it queued
#   EngineGap          a kind, prop or form the C engine does not draw yet
#   CPage              a hosted page (uitree_app.Page) on the C engine
#   CEditorEngine      the Editor's kinds (uitree_editor.Engine), placed by the engine's layout
#   CEditorPage        a hosted page on them (uitree_editor.Page)
"""The UI tree on the C engine (native/moy_uitree): the app's build function
runs through the reference's Builder (every check the reference makes), the
checked tree is lowered into the engine's store, and each frame is one call
into C that walks it through the toolkit's verbs. Python runs for handlers
only: the commit's writes are pushed to C's slots at its end, and the
engine's own writes (a toggle, a list's cursor) are pulled back before any
handler runs. Same app file, same trace, same pixels as `uitree_app.Engine`."""

import sys

import ui as _v
import ui_draw as _D

import uitree as S
from uitree_app import Engine, Page
import uitree_aot as _aot

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
A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK, A_SHIFT_ENTER = 1, 2, 4, 16
INPUTS = ("keys", "tdeck", "pad", "touch")
_KINDS = {"page": 1, "group": 2, "label": 3, "info": 4, "link": 5, "button": 6, "toggle": 7,
          "rows": 8, "dialog": 9, "seg": 10, "slider": 11, "field": 12, "empty": 13,
          "progress": 14, "text_view": 15,
          "scroll": 16, "menu": 17, "item": 18, "split": 19,
          "canvas": 20, "tabs": 21, "switch": 22, "case": 23, "table": 24, "col": 25, "foot": 26,
          "cards": 27, "card": 28, "picture": 29, "popover": 30,
          "tools": 31, "row": 32, "status": 33, "text_editor": 34, "panes": 35, "pane": 36,
          "legacy": 37, "slot": 38, "panel": 39, "lent": 40}
# the placed kinds: a page the engine's layout places holds them (`CEditorPage`)
_PLACED_KINDS = ("tools", "row", "status", "text_editor", "panes", "pane", "legacy", "slot", "panel", "lent")
# what a placed page's frame does not draw yet
_PLACED_GAPS = ("slot", "lent", "tabs")
_PANE_KINDS = ("panes", "pane")
_LEGACY_FNS = ("draw", "pointer", "keys", "hints", "gen")
_HEAD = {"rows": ("model",), "table": ("model",), "cards": ("model",), "dialog": ("buttons",)}
_KIND_NAMES = dict((v, k) for k, v in _KINDS.items())
_KIND_NAMES[len(_KINDS) + 1] = "cycle"          # UIT_KINDS: a seg of the cycle look
_BOUND = ("toggle", "seg", "slider", "field", "text_view", "tabs")       # a two-way control: bound to the state of its key
_LIT_ONLY = {"seg": ("values", "words", "model", "labels"), "text_view": ("name",),
             "switch": ("name",), "case": ("v",), "tabs": ("labels", "values")}    # a switch's and a case's: in their keys   # what the reference keeps beside a node's props
_TYPED = "typed in field "
_DOC = "#doc"               # a document's model: its state's name and this
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


class _TextEditor:
    """A text editor as the front end draws it on the C engine
    (`uitree_editor.Engine._r_text_editor`): its key, its literal props and
    its conditions, which the store evaluates (`cond_of`)."""

    def __init__(self, eng, node, key, l, conds):
        self.key = key
        self.kind = "text_editor"
        self.parts = (key + ".caret", key + ".word")
        self.l = dict((k, l[k]) for k in ("find", "marks") if k in l)
        self.conds = conds      # of "gutter", "find_on": the ones its page names
        self.p = {}
        for k in conds:
            self.p[k] = self._cond(eng, node, k)

    @staticmethod
    def _cond(eng, node, k):
        def holds(row):
            return eng._x.cond_of(node, k) == 1
        return holds


class _PaneNode(S.Node):
    """A lowered panes, pane or legacy node: `aux` its node in the store."""
    __slots__ = ()


def _pane_node(n, parent):
    """A panes, pane or legacy node's stand-in once its page is lowered: its
    key, props and the panes it holds, for what the Editor's front end asks
    of a pane (its tabs on offer, its name, its held size's state); the
    layout is the engine's."""
    m = _PaneNode(n.kind, n.key, n.where)
    m.p, m.l, m.src = n.p, n.l, n.src
    if parent is not None:
        m.parent = parent
        parent.kids.append(m)
    return m


class CEngine(Engine):
    """`uitree_app.Engine` with the frame in C: the build, the checks and the
    handlers stay the reference's."""

    PLACED = False          # the page is stacked by the engine's layout (uit_set_placed)

    def __init__(self, *a, **k):
        Engine.__init__(self, *a, **k)
        self._c_init()

    def _c_init(self):
        if _New is None:
            raise EngineGap("on a tier with no moy_uitree")
        self._x = None          # the store: made at the first open (`_made`), so a page never opened holds none
        self._shut = False      # closed (`close`): the store keeps its lowered page and no visit
        self._lowered = None    # the page function whose tree the store holds
        self._slot, self._names, self._types, self._pushed = {}, [], [], {}
        self.direct = BUILD == "direct"
        self._draws, self._drawing = {}, False
        self._grounds = {}      # a canvas's ground= role, by its node
        self._docs = {}         # a document's state -> what it was pushed at (`_push_doc`)
        self._doc_reads = {}    # ...and the fields of it the lowered page reads
        self._editors = {}      # a text editor's node -> its stand-in (`_TextEditor`)
        self._pointers = []     # (key, its pointer= action) of each canvas a pointer is routed to
        self._held = None       # (key, action, rect): the canvas a press holds until its release
        self._pop_lists = {}    # a popover's key -> (model, cur) of the list the arrows walk in it
        self._pop_fields = {}   # a popover's key -> whether a field of its own takes typed keys
        self._loaded = False    # the store came from a compiled page whose build is not yet counted

    def _made(self):
        """The store, made and declared at the first way in that needs it."""
        x = self._x
        if x is None:
            self._x = x = _New()
            if self.PLACED:
                x.set_placed(1)
            self._slot, self._names, self._types, self._pushed = {}, [], [], {}
            self._docs = {}
            self._drawing = False
            self._bind()            # the module's state holds this engine's values before any is pushed
            if not self._load(x):
                self._declare()
        return x

    def _load(self, x):
        """The first page's compiled blob (uitree_aot), where the image has
        one: the store filled with no declaration, build or lowering call. A
        blob this front end does not take is said once and the page built."""
        blob = _aot.blob_for(self.pages[0])
        if blob is None:
            return False
        try:
            _aot.load(self, x, blob)
        except _aot.Refused as e:
            print("uitree: %s: compiled page refused (%s); built" % (_aot.key(self.pages[0]), e))
            return False
        self._lowered, self._loaded = self.pages[0], True
        return True

    def _built_as_loaded(self):
        self.builds += 1
        was, S._NOW.app = S._NOW.app, self
        try:
            S._trace("build", getattr(self.pages[0], "__name__", "page"), "page (builds %d)" % self.builds)
        finally:
            S._NOW.app = was

    def close(self):
        """The visit is over (its host left the page): the store frees what
        the visit made and keeps the declarations, the lowered page and
        each slot's value, so the next open draws with no build and pushes
        only the state that moved since."""
        Engine.close(self)
        if self._x is not None:
            self._x.close()
            self._shut = True

    def forget(self):
        """The store let go whole: the next open makes and builds it again."""
        Engine.forget(self)
        x, self._x = self._x, None
        if x is not None:
            x.free()
        self._lowered, self._shut, self._draws = None, False, {}

    def _resume(self):
        """A frame or a call on a closed page with no open first: the visit
        goes on from the state as Python holds it, its focus kept, no hook run."""
        self._shut = False
        self._bind()
        self.committed()

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
        for name, decl in st._t.items():
            vals = None
            t = decl.base if type(decl) is S.Opt else decl     # absent while it holds None (`_push`)
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
            elif type(t) is S.Doc:
                # a document stays Python's: what templates read of it (DOC_FIELDS) is one row
                # of a model of its own, the state a row_of of it (`{code.line}`), pushed
                # where the document moved (`_doc_moved`)
                self._ck(x.state(name + _DOC, MODEL, None), "state " + name)
                self._names.append(name + _DOC)
                self._types.append(_DOC)
                ty = ROWOF
            else:
                raise EngineGap("state %s of %r" % (name, t))
            self._slot[name] = self._ck(x.state(name, ty, vals), "state " + name)
            self._names.append(name)
            self._types.append(decl)
        for name, t in st._t.items():
            if isinstance(t, S.Model):
                fields = t.order
                types = bytes([self._ftype(t.fields[f], name) for f in fields])
                self._ck(x.model(self._slot[name], "\0".join(fields).encode("utf-8"), types,
                                 fields.index(t.key)), "model " + name)
            elif isinstance(t, S.RowOf):
                x.row_of(self._slot[name], self._slot[t.model])
            elif type(t) is S.Doc:
                fields = ["doc"] + list(S.DOC_FIELDS)
                types = bytes([TEXT] + [self._ftype(f, name) for f in S.DOC_FIELDS.values()])
                m = self._slot[name] - 1
                self._ck(x.model(m, "\0".join(fields).encode("utf-8"), types, 0), "doc " + name)
                x.row_of(self._slot[name], m)
                self._docs[name] = [-1] * 8
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
                A_ROLE_BACK if "role:back" in a.keys else 0) | (A_SHIFT_ENTER if S.SHIFT_ENTER in a.keys else 0)
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
        if type(t) is S.Enum:
            return TEXT         # a field of an enum: its value's text
        raise EngineGap("a field of %r in model %s" % (t, model))

    # -- state across the line --------------------------------------------------

    def _value(self, name):
        st = self.s
        if name in st._derive:
            return st._derive[name]()
        return st._v[name]

    def _push(self, name):
        x, st = self._x, self.s
        slot = self._slot.get(name)
        if slot is None:            # a document's model: pushed with its state
            self._pushed[name] = 0
            return
        t, v = self._types[slot], self._value(name)
        if t is S.Doc or type(t) is S.Doc:
            self._pushed[name] = st._ver.get(name, 0)
            self._push_doc(name, v)
            return
        self._pushed[name] = st._ver.get(name, 0)
        if type(t) is S.Opt:
            if v is None:
                x.set_absent(slot)
                return
            t = t.base
        if isinstance(t, S.Model):
            cells = []
            for r in v:
                for f in t.order:
                    c, ft = r._f[f], t.fields[f]
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

    def _push_doc(self, name, d):
        """What templates read of document `name` (DOC_FIELDS), its one row."""
        x = self._x
        sig = self._docs[name]
        ed = d.ed
        sig[0], sig[1], sig[2], sig[3] = ed.edits, ed.row, ed.col, len(ed.lines)
        sig[4], sig[5], sig[6], sig[7] = d.find_q, d.find_ci, ed.sel, ed.left
        reads = self._doc_reads.get(name, ())
        cells = ["1"]
        for f, ft in S.DOC_FIELDS.items():
            if f not in reads:              # a field no template of the page reads: never asked
                cells.append("0" if ft is not S.TEXT else "")
                continue
            c = getattr(d, f)
            cells.append(("1" if c else "0") if ft is S.BOOL else str(c))
        m = self._slot[name] - 1
        self._ck(x.set_model(m, 1, ("\0".join(cells) + "\0").encode("utf-8")), "doc " + name)
        x.set_text(self._slot[name], "1")

    def _doc_moved(self):
        """Each document whose caret, text, find or selection moved since it
        was pushed, pushed (a handler's verb moves its version; the keys move
        the caret alone)."""
        st = self.s
        for name in self._docs:
            sig, d = self._docs[name], st._v[name]
            ed = d.ed
            if (sig[0] != ed.edits or sig[1] != ed.row or sig[2] != ed.col or sig[3] != len(ed.lines)
                    or sig[4] != d.find_q or sig[5] != d.find_ci or sig[6] is not ed.sel):
                self._push_doc(name, d)

    def committed(self):
        """The end of a commit: each slot whose version moved, pushed."""
        if self._x is None or self._shut:
            return              # pushed at the open (or the frame) that comes next
        ver, pushed = self.s._ver, self._pushed
        for name in self._names:
            if pushed.get(name) != ver.get(name, 0):
                self._push(name)

    def settle(self):
        """The removal rule runs in C, when a model is pushed (`_push`)."""

    def _pull(self, slot):
        x, st = self._x, self.s
        name, t = self._names[slot], self._types[slot]
        if type(t) is S.Opt:
            t = t.base
            if x.absent(slot):
                t = None
        if t is None:
            v = None
        elif t is S.BOOL:
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
        f = t.order[fi]
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
            self._made()
            if self._lowered is not None and self._lowered is self.pages[0] and len(self.pages) == 1:
                self.root = LOWERED         # the store holds the page from a compiled blob
        if self._loaded:            # the build a compiled page stands for, counted and traced as one
            self._loaded = False
            self._built_as_loaded()
        if self.root is None:
            self._build()
            self.lower(self.root)
            self.root = LOWERED
            self._lowered = self.pages[-1]
            for name in self._docs:
                self._push_doc(name, self.s._v[name])     # the fields the lowered page reads

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
        placed, placed_kinds, placed_gaps = self.PLACED, _PLACED_KINDS, _PLACED_GAPS
        grounds = self._grounds = {}
        editors = self._editors = {}
        pointers = self._pointers = []
        pop_lists = self._pop_lists = {}
        pop_fields = self._pop_fields = {}
        stand = self._pn = {}           # node -> a panes or pane's stand-in (`_pane_node`)
        legacies = self._legacy = {}    # node -> a legacy node's stand-in: its functions
        srcs = []

        def node(n, parent, lent=False):
            nk, l = n.kind, n.l
            kind = kinds.get(nk)
            if kind is None or (nk in placed_kinds and not placed) or (
                    placed and nk in placed_gaps and not (lent and nk == "tabs")):
                raise EngineGap("a %s node" % nk + ("" if placed or nk not in placed_kinds else
                                                     " outside a placed page"))
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
            if nk in _PANE_KINDS or nk == "legacy":
                m = _pane_node(n, stand.get(parent))
                (legacies if nk == "legacy" else stand)[me] = m
                m.aux = me
            if nk == "popover":
                pop_fields[n.key] = any(k.kind == "field" for k in n.kids)
                for k in n.kids:
                    if k.kind == "rows" and k.src.get("cur"):
                        pop_lists[n.key] = (k.l["model"], k.src["cur"])
                        break
            if nk == "canvas" and l and l.get("pointer") is not None:
                pointers.append((n.key, l["pointer"]))
            if nk == "canvas" and l and l.get("ground"):
                grounds[me] = l["ground"]
            elif nk == "text_editor":
                editors[me] = _TextEditor(self, me, n.key, n.l, tuple(k for k in ("gutter", "find_on") if k in n.src))
            if nk == "seg" and "labels" in l:
                r = cprop(me, "labels", l["labels"], None)
                if r < 0:
                    fail(r, "seg %s labels" % n.key, n.where)
            elif nk == "rows" or nk == "table" or nk == "cards":
                r = cprop(me, "bind", l["model"], None)
                if r < 0:
                    fail(r, "%s %s" % (nk, n.key), n.where)
            head = heads.get(nk, ())
            for k, src in n.src.items():
                if k in head:
                    continue
                if nk == "legacy" and k in _LEGACY_FNS:     # the front end's functions
                    r = cprop(me, k, getattr(src, "__name__", k), None)
                    if r < 0:
                        fail(r, "legacy %s %s=" % (n.key, k), n.where)
                    continue
                if k == "min" and nk == "pane":             # one prop a pair: "w h", its tab the repr
                    pairs = src.items() if type(src) is dict else (("", src),)
                    for v, wh in pairs:
                        r = cprop(me, k, "%d %d" % (wh[0], wh[1]), v)
                        if r < 0:
                            fail(r, "ui.pane min=%r" % (src,), n.where)
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
                    if "." in src:
                        srcs.append(src)
                elif k == "offered" and (nk == "tabs" or nk == "pane"):
                    for v, when in src.items():     # one prop a value: its condition, the value its repr
                        r = cprop(me, k, when, v) if type(when) is str else E_GAP
                        if r < 0:
                            fail(r, "ui.tabs offered=%r" % (src,), n.where)
                    continue
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
                    src, rep = str(src), repr(src)          # a literal condition (focus=True)
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
            lends = nk == "tools" and bool(l and l.get("lend"))
            for k in n.kids:
                node(k, me, lends)
        node(root, -1)
        self._ck(x.build_end(), "the build")
        # what the page's templates and conditions read of each document: the fields pushed
        reads = self._doc_reads = {}
        for name in self._docs:
            got = [f for f in S.DOC_FIELDS if any((name + "." + f) in t for t in srcs)]
            reads[name] = tuple(got)

    def front(self):
        """What the front end keeps beside the store once a page is lowered,
        as literals a compiled page carries (`uitree_aot.front`): each
        canvas's pointer action, each popover's list and whether a field of
        its own takes keys, the document fields the page reads, each text
        editor, each pane, panes and legacy stand-in. A prop's value is kept
        as itself (0), a dict as its items (1), a function as its name in
        the page's module (2); a dict of conditions is the store's alone."""
        mod = sys.modules[_aot._module(self.pages[0])]

        def lits(d):
            out = []
            for k, v in d.items():
                if type(v) is dict:
                    if not any(callable(x) for x in v.values()):
                        out.append((k, tuple(v.items()), 1))
                elif type(v) is S.Action:
                    continue
                elif callable(v):
                    name = getattr(v, "__name__", "")
                    if getattr(mod, name, None) is not v:
                        raise _aot.Refused("%s=%r is not a function of its module" % (k, v))
                    out.append((k, name, 2))
                else:
                    out.append((k, v, 0))
            return tuple(out)
        stand = list(self._pn.items()) + list(self._legacy.items())
        stand.sort(key=lambda kv: kv[0])
        return (tuple((k, a.name) for k, a in self._pointers),
                tuple((k, m, c) for k, (m, c) in self._pop_lists.items()),
                tuple(self._pop_fields.items()),
                tuple(self._doc_reads.items()),
                tuple((node, te.key, tuple(te.l.items()), te.conds) for node, te in self._editors.items()),
                tuple((node, m.kind, m.key, m.parent.aux if m.parent is not None else -1, tuple(m.where),
                       lits(m.src), lits(m.l)) for node, m in stand))

    def unfront(self, f):
        """`front`'s literals made again into what lowering leaves the front end."""
        acts, mod = self.actions, sys.modules[_aot._module(self.pages[0])]

        def made(v, d):
            return v if d == 0 else dict(v) if d == 1 else getattr(mod, v)
        self._pointers = [(k, acts[a]) for k, a in f[0]]
        self._pop_lists = dict((k, (m, c)) for k, m, c in f[1])
        self._pop_fields = dict(f[2])
        self._doc_reads = dict(f[3])
        self._editors = dict((node, _TextEditor(self, node, key, dict(l), conds)) for node, key, l, conds in f[4])
        pn, lg = self._pn, self._legacy = {}, {}
        for node, kind, key, parent, where, src, l in f[5]:
            m = _PaneNode(kind, key, where)
            m.src = dict((k, made(v, d)) for k, v, d in src)
            m.l = dict((k, made(v, d)) for k, v, d in l)
            if parent >= 0:
                m.parent = pn[parent]
                m.parent.kids.append(m)
            m.aux = node
            (lg if kind == "legacy" else pn)[node] = m

    def _draw(self, node, x, y, w, h):
        """A canvas's draw, called by the walk inside the frame at its rect."""
        now = S._NOW
        app, frame = now.app, now.frame       # two names, not a tuple: a frame allocates nothing
        now.app, now.frame = self, True
        try:
            self._draws[node](self.cv, x, y, w, h)
        finally:
            now.app, now.frame = app, frame

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
        if self._shut:
            self._resume()
        self._ready()
        x = self._x
        if self.want_focus is not None:
            x.want_focus(self.want_focus)
            self.want_focus = None
        kind = _v.input_kind()
        self.input = kind if kind in S.INPUTS else "keys"
        t = _D.UI_TARGET
        r = x.frame(t[0], cv, t[2], content[0], content[1], content[2], content[3], self._flags(desk), fs,
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

    def _flags(self, desk):
        b = self._buttons
        return ((F_DESK if desk else 0) | (F_ON_BAR if _D._ON_BAR[0] else 0)
                | (F_BAND if self.band else 0) | (F_BARE if self.bare else 0) | (F_GEN if self._gen_on else 0)
                | (F_TRACE if S.TRACING else 0) | (F_UP if b & 4 else 0) | (F_DOWN if b & 8 else 0)
                | (min(255, len(self.pages)) << F_PAGES_SHIFT))

    def still(self, content, desk):
        """Whether the frame drawn last is what a frame drawn now into
        `content` would draw (`uit_still`): no state, draft, focus, event or
        build moved, the frame's flags and rect are the same, and the
        toolkit's context, its frame begun, took no input and is at rest."""
        if not self._visiting() or self.q or self.timers or self.decl.posts or self.want_focus is not None:
            return False
        self.committed()
        kind = _v.input_kind()
        kind = kind if kind in S.INPUTS else "keys"
        fs = max(1, getattr(self.cv, "font_scale", 1)) if self.cv is not None else 1
        t = _D.UI_TARGET
        return self._x.still(t[0], content[0], content[1], content[2], content[3], self._flags(desk), fs,
                             INPUTS.index(kind))

    # -- asked between frames ----------------------------------------------------------

    def open(self, arg=None, hook="opened"):
        """A visit: the page the store holds is the first page's, kept from
        the last visit (a build reads no state, B01), else built at the first
        frame; what the hook moved pushed at the commit that ends the open."""
        x = self._made()
        x.open()
        self._shut = False
        Engine.open(self, arg, hook)
        if self._lowered is not None and self._lowered is self.pages[0]:
            self.root = LOWERED

    def _visiting(self):
        return self._x is not None and not self._shut and self.root is not None

    def dialog_shown(self):
        return self._visiting() and self._x.dialog() >= 0

    def _focus_key(self):
        """The id focus is on after the last frame ("" none): the engine's."""
        return self._x.focused() if self._visiting() else ""

    def _no_set(self, v):
        pass                    # the Builder's initial ""; the engine keeps the key

    focus_key = property(_focus_key, _no_set)

    def draft(self, key):
        return self._x.draft(key) if self._visiting() else None

    def stop_kinds(self):
        out, i = {}, 0
        if not self._visiting():
            return out
        while True:
            got = self._x.stop(i)
            if got is None:
                return out
            out[got[1]] = _KIND_NAMES[got[0]]
            i += 1

    def leave(self):
        """Every way out of the page: each drawn commit field's draft is
        written, in C; its writes pulled and its changed= run here."""
        if not self._visiting():
            return
        self._bind()
        self._x.leave()
        self._drain()
        q, self.q = self.q, []
        for act, args, src in q:
            self.run_action(act, args, src)
        S.commit(self)

    def keys(self, i, damage=True):
        if self._x is None:
            self._made()
        if self._shut:
            self._resume()
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
        return self._made().dump()

    snapshot = dump

    def _has_page(self):
        return len(self.pages) > 1 or self.dialog_shown()

    def _back(self):
        """Back: a shown dialog's first button (in C), else the page under this
        one, else the app's back role or its declared back action."""
        self._bind()
        x = self._made()
        if self._visiting():
            if x.dialog() >= 0:
                got = x.back_dialog()
                if got != -1:
                    act = self._acts[got[0]]
                    self.q.append((act, self.args_for(act), "back on dialog %r" % got[1]))
                self._damage.all()
                return True
            got = x.back_popover()
            if got != -2:                   # a popover: Back is its close=
                if got != -1:
                    act = self._acts[got[0]]
                    self.q.append((act, self.args_for(act), "back on popover %r" % got[1]))
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
        if a is not None and x.can(self._aidx[a.name], -1):
            self.q.append((a, self.args_for(a), why))
            self._damage.all()
            return True
        return False

    def stats(self):
        return dict(zip(("nodes", "node_bytes", "tmpl_bytes", "cond_bytes", "pool_bytes", "prop_bytes",
                         "state_bytes", "engine_bytes"), self._made().stats()))


class CPage(CEngine, Page):
    """`uitree_app.Page` (a page a surface on today's verbs hosts) with the
    frame in C."""

    def __init__(self, build, host, damage, files=None, close=None, gen=False, env=None):
        Page.__init__(self, build, host, damage, files, close, gen, env)
        self._c_init()



import uitree_editor as _ed


class CEditorEngine(CEngine, _ed.Engine):
    """`uitree_editor.Engine` (the Editor's kinds) with the frame and the
    layout in C: the page is placed by the engine's one layout rule
    (`uit_set_placed`), each region's rect read back with `rect`."""

    PLACED = True

    def __init__(self, *a, **k):
        _ed.Engine.__init__(self, *a, **k)
        self._c_init()
        self._te_rect = [0, 0, 0, 0]

    def paint(self, dt, cv, th, content, desk):
        g, x = self.ground, self._made()
        if g is None:
            x.ground(0, 0, 0, 0)
        else:
            x.ground(g[0], g[1], g[2], g[3])        # no star call: a frame allocates nothing
        if self._docs:
            self._doc_moved()
        CEngine.paint(self, dt, cv, th, content, desk)

    def point(self, px, py, click):
        """A canvas with pointer= is handed down, drag and up in its own
        coordinates, at the rect the last frame placed it; the press that
        starts on it holds it until release (`uitree_editor.Engine.point`)."""
        down = self.pointer_down()
        held = self._held
        if held is not None:
            key, act, r = held
            ev = S.Ev("drag" if down else "up", px - r[0], py - r[1], "touch")
            self.q.append((act, (ev,), "pointer %s on canvas %r" % (ev.kind, key)))
            if not down:
                self._held = None
            self._damage.all()
            return True
        if click and self._visiting():
            for key, act in self._pointers:
                r = self._x.rect(key)
                if r is not None and r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    ev = S.Ev("down", px - r[0], py - r[1], "touch")
                    self.q.append((act, (ev,), "pointer down on canvas %r" % key))
                    if down:
                        self._held = (key, act, r)
                    else:
                        self.q.append((act, (S.Ev("up", ev.x, ev.y, "touch"),), "pointer up on canvas %r" % key))
                    self._damage.all()
                    return True
        return CEngine.point(self, px, py, click)

    def step_rows(self, d):
        """Up or down `d` rows in the shown popover's list
        (`uitree_editor.Engine.step_rows`): its cur= moves, and focus
        follows to the row's drawn stop. False where no popover list shows."""
        if not self._visiting():
            return False
        got = self._x.back_popover()
        lst = self._pop_lists.get(got[1]) if type(got) is tuple else None
        if lst is None:
            return False
        model, ck = lst
        rows = self.s._v[model]
        if not rows:
            return True
        keyf = rows[0]._m.key
        keys = [r._f[keyf] for r in rows]
        cur = self.s._v[ck]
        i = keys.index(cur) if cur in keys else -1
        j = max(0, min(len(keys) - 1, i + d))
        if keys[j] != cur:
            self.s._v[ck] = keys[j]
            self.s._ver[ck] += 1
            was, S._NOW.app = S._NOW.app, self
            S._trace("state", ck, "%r -> %r   (step %+d)" % (cur, keys[j], d))
            S._NOW.app = was
            self.committed()
        self.want_focus = keys[j]       # a row's key: the engine focuses the stop it drew for it
        self._damage.all()
        return True

    def _has_page(self):
        """Back is the app's while a popover or a closer row (a find strip) shows."""
        return CEngine._has_page(self) or self.popover_shown() or (
            self._visiting() and self._x.back_closer() != -2)

    def _back(self):
        """Back: a shown popover's close= (in C), else the innermost shown
        closer row's (`close=`, where it is on), else the store's back."""
        self._bind()
        x = self._made()
        if self._visiting() and x.dialog() < 0 and x.back_popover() == -2:
            got = x.back_closer()
            if got != -2 and got != -1:
                act = self._acts[got[0]]
                kind = "row"
                self.q.append((act, self.args_for(act), "back on %s %r" % (kind, got[1])))
                self._damage.all()
                return True
        return CEngine._back(self)

    # -- keys: the Editor's typed bytes and arrows into its document, ahead of the store's

    def popover_shown(self):
        return self._visiting() and self._x.back_popover() != -2

    def _focused_kind(self):
        """The kind of the stop focus is on, "" none."""
        key = self._focus_key()
        if not key:
            return ""
        if key in self.views:
            return "text_editor"
        i, x = 0, self._x
        while True:
            got = x.stop(i)
            if got is None:
                return ""
            if got[1] == key:
                return _KIND_NAMES[got[0]]
            i += 1

    def editor_keys(self):
        if not self._visiting() or self.popover_shown() or self._focused_kind() in ("field", "link"):
            return None
        for k in self.views:
            if self.views[k] is not None:
                return k
        return None

    def keys(self, i, damage=True):
        if self._x is None:
            self._made()
        if self._shut:
            self._resume()
        self._bind()
        ch = getattr(i, "typed", 0) & 0xFF
        was, S._NOW.app = S._NOW.app, self
        try:
            if self._chord(ch) or self._doc_keys(i, ch):
                return True
            if ch == 13 and self._visiting() and self._x.take_focused():
                self._drain()           # Enter takes a popover's row, as A does
                self._damage.all()
                return True
        finally:
            S._NOW.app = was
        return CEngine.keys(self, i, damage)

    def _chord(self, ch):
        """A typed byte an action's chord names (`uitree_editor.Engine._keys`):
        the action, ahead of the document; a bare letter is the document's."""
        if not ch:
            return False
        for a in self._acts:
            for k in a.keys:
                if S._chord_byte(k) == ch and a.live():
                    if a.can():
                        self.q.append((a, self.args_for(a), "key %r (unit %s)" % (k, a.unit)))
                    else:
                        S._trace("off", a.name, "key %r: %s" % (k, a.when_src))
                    self._damage.all()
                    return True
        return False

    def _doc_keys(self, i, ch):
        """The arrows and the typed bytes into the shown text editor's
        document (`uitree_editor.Engine._keys`), where it takes them."""
        text = ch and (ch in (8, 9, 13, 127) or 32 <= ch < 127)
        if text and ch != 13 and self.views and self._visiting():
            # a popover with no field of its own (completion, the scripts) is left by a typed
            # key, which then edits as though it was never open
            got = self._x.back_popover()
            if type(got) is tuple and not self._pop_fields.get(got[1]):
                act = self._acts[got[0]]
                self.run_action(act, self.args_for(act), "typed %r on popover %r" % (chr(ch), got[1]))
                if not self.popover_shown():
                    return self._into_doc(next(iter(self.views)), ch, bool(getattr(i, "mods", 0) & _ed._SHIFT))
        ek = self.editor_keys()
        if ek is None:
            return False
        doc = self.s._v[ek]
        moved = False
        if not self.host_arrows:
            for name, dy, dx in (("up", -1, 0), ("down", 1, 0), ("left", 0, -1), ("right", 0, 1)):
                if i.pressed(name):
                    doc.move(dy, dx, True if getattr(i, "mods", 0) & _ed._SHIFT else None)
                    moved = True
        if text:
            self._into_doc(ek, ch, bool(getattr(i, "mods", 0) & _ed._SHIFT))
            moved = True
        if moved:
            self._damage.all()
        return moved

    def _flags(self, desk):
        return CEngine._flags(self, desk) & ~F_BARE        # bare only over hosted bodies

    def _hosts(self, n):
        return False            # a legacy node is a gap of this engine's (`lower`)

    # -- the panes: laid out by the engine's rule, read back for the host

    # what the reference's pane helpers ask of a lowered pane, the engine answers

    def _shown(self, k, row):
        if type(k) is _PaneNode and k.kind != "legacy":
            return self._x.pane_ask(k.aux, 0, 0) == 1
        return _ed.Engine._shown(self, k, row)

    def _min_of(self, k, axis):
        if type(k) is _PaneNode and k.kind != "legacy":
            return self._x.pane_ask(k.aux, 1, axis)
        return _ed.Engine._min_of(self, k, axis)

    def _offered(self, pane):
        tabs = pane.l.get("tabs")
        if tabs is None:
            return None
        vals = self.s._t[tabs].values
        if type(pane) is not _PaneNode:
            return _ed.Engine._offered(self, pane)
        ask = self._x.pane_ask
        return tuple(v for i, v in enumerate(vals) if ask(pane.aux, 3, i) == 1)

    def _axis(self, n):
        return 0 if n.src.get("dir", "row") == "row" else 1

    def _pane_name(self, pane):
        t = pane.src.get("title")
        if t is not None and "{" not in t:
            return t
        return _ed.Engine._pane_name(self, pane)

    def _pane_sync(self):
        """The desk unit, the pane the keys reach, the pointer and the clock,
        handed to the engine before it lays the panes out or draws a divider."""
        self.du = self.unit or max(1, _ed._lb.chrome_size(getattr(self, "fs", 1)) // _ed._lb.render_scale())
        self._x.panes_set(self.du, self.pane_cur, self._mouse(), self.clock)

    def _pane_read(self):
        """The engine's panes (`uit_panes`) as `uitree_editor.Engine` keeps
        them: `arranged`, `least`, `alone_pane`, `placed`, `pane_order`,
        `splits` and `panes_root`, each pane its stand-in."""
        w = self._x.panes()
        if not w:
            return
        pn, lg = self._pn, self._legacy
        self.panes_root = pn.get(w[0])
        self.alone_pane = pn.get(w[1])
        self.least = (w[2], w[3])
        at = 9

        def path(i):
            return tuple(w[i + 1:i + 1 + w[i]])

        panes = []
        for _ in range(w[4]):
            panes.append((pn[w[at]], tuple(w[at + 1:at + 5]), path(at + 5)))
            at += 10
        divs = []
        for _ in range(w[5]):
            divs.append((path(at + 4), w[at + 9], "v" if w[at + 10] else "h", tuple(w[at:at + 4])))
            at += 11
        self.arranged = (panes, divs)
        self.placed = []
        for _ in range(w[6]):
            self.placed.append((lg[w[at]], None, tuple(w[at + 1:at + 5])))
            at += 10
        self.pane_order = []
        for _ in range(w[7]):
            self.pane_order.append((pn[w[at]], tuple(w[at + 1:at + 5])))
            at += 10
        self.splits = {}
        self._sids = []
        for _ in range(w[8]):
            n, nk, axis, gap = pn[w[at]], w[at + 1], w[at + 2], w[at + 3]
            rect, base = tuple(w[at + 4:at + 8]), path(at + 8)
            at += 13
            kids, sizes, mins, held = [], [], [], []
            for _j in range(nk):
                kids.append(pn[w[at]])
                sizes.append(w[at + 1])
                mins.append(w[at + 2])
                held.append(None if w[at + 3] < 0 else w[at + 3])
                at += 4
            sid = "split:" + "/".join(k.key for k in kids)
            self._sids.append(sid)
            self.splits[sid] = (n, kids, sizes, mins, held, rect, axis, gap, base)

    def place(self, area, desk=True):
        """The page's regions laid out over `area`, nothing drawn: each
        one's rect for `rect_of`, its panes' arrangement and hosted bodies
        (`uitree_editor.Engine.place`)."""
        self._bind()
        self.content = area
        self.desk = desk
        self._ready()
        self.committed()
        self.placed = []
        self._pane_sync()
        self._x.place(area[0], area[1], area[2], area[3], self._flags(desk), max(1, getattr(self, "fs", 1)))
        self._pane_read()
        self._hosted_no = self.frame_no

    def draw_splits(self):
        """The last placement's dividers drawn into the verbs pass the host
        has open (`uitree_editor.Engine.draw_splits`), in C (`uit_splits`)."""
        self.div_held = None
        self.flash = None
        if not self.splits:
            return
        self._bind()
        x = self._made()
        self.committed()
        self._pane_sync()
        t = _D.UI_TARGET
        out = x.splits(t[0], t[1], t[2], self._flags(self.desk))
        if out[7]:
            self.size_gen += out[7]
            self._drain()
        if out[8]:
            self._damage.all()
        if out[0] >= 0:
            self.div_held = (self._sids[out[0]], out[1], out[2], out[3], bool(out[4]))
            if out[5] >= 0:
                pane = self._pn[out[5]]
                axis = self.splits[self._sids[out[0]]][6]
                self.flash = (pane, "%s stops at its minimum, %d %s" % (
                    self._pane_name(pane), out[6], "wide" if axis == 0 else "tall"))

    def rect_of(self, key):
        """Where node `key` was placed by the last frame or placement, else None."""
        return self._x.rect(key) if self._x is not None else None

    _LC_KINDS = ("tab", "button", "text")

    def lent(self, site):
        """The shown row lent to `site`, read from the store (`uit_lent`):
        the reference's cells, [kind, key, value, label, icon, flags,
        action], None where none is lent."""
        self._made()
        if self._shut:
            self._resume()
        self._ready()
        self.committed()
        got = self._x.lent(site)
        if got is None:
            return None
        if type(got) is int:
            self._fail(got, "the row lent to %s" % site, None)
        kinds, acts, out = self._LC_KINDS, self._acts, []
        for kind, _node, _vi, flags, a, key, value, label, icon in got:
            kind = kinds[kind]
            out.append([kind, key, value if kind == "tab" else None,
                        label if label or kind != "button" else None, icon or None, flags,
                        acts[a] if a >= 0 else None])
        return out

    def lent_place(self, cells, x0, w, h, fs, pad):
        """The lent row's rule in C (`uit_lent_place`)."""
        return self._made().lent_place(cells, x0, w, h, fs, pad)

    def _draw(self, node, x, y, w, h):
        """A canvas's draw in its rect, clipped to it, over its ground; a
        text editor's rows, its document's (`_r_text_editor`)."""
        te = self._editors.get(node)
        if te is not None:
            now = S._NOW
            app, frame = now.app, now.frame
            now.app, now.frame = self, True
            try:
                r = self._te_rect       # a word an assignment: an unpacking of four builds a tuple
                r[0] = x
                r[1] = y
                r[2] = w
                r[3] = h
                self._r_text_editor(te, None, r)
                if self._x.back_popover() != -2:
                    for part in te.parts:      # where a popover may hang from: its caret, its word
                        a = self._part_anchor(part)
                        if a is not None:
                            self._x.anchor(part, a[0], a[1], a[2], a[3])
            finally:
                now.app, now.frame = app, frame
            return
        cv = self.cv
        cv.clip(x, y, w, h)
        try:
            g = self._grounds.get(node)
            if g:
                cv.rect(x, y, w, h, self.th[g])
            CEngine._draw(self, node, x, y, w, h)
        finally:
            cv.clip()


class CEditorPage(CEditorEngine, _ed.Page):
    """`uitree_editor.Page` (a page on the Editor's kinds a surface hosts)
    on `CEditorEngine`."""

    def __init__(self, build, host, damage, files=None, close=None):
        _ed.Page.__init__(self, build, host, damage, files, close)
        self._c_init()
        self._te_rect = [0, 0, 0, 0]
