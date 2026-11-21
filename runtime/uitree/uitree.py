# Map (grep -n a name to jump there):
#   UIError               every mistake: a code, the app's file and line, what was expected, the fix
#   T                     a declared type, the base of every kind of state
#   ImageT                a picture the engine decodes, caches and veils (G9)
#   Pic                   an image value: where the engine finds the picture
#   image                 the Python spelling of an image value
#   Enum                  one of named values
#   Load                  a load's state: never, loading, failed, loaded (M2)
#   Record                a fixed set of typed fields
#   Model                 rows keyed by one field
#   RowOf                 one row of a model, as a value
#   Opt                   a value that may be absent (ui.opt)
#   Doc                   a text document the engine owns (ui.doc)
#   Row                   a model's row, read by field
#   ModelView             a model read in a handler
#   RecView               a record read in a handler
#   DocView               a document read and changed in a handler
#   State                 the app's declared, typed state; a read inside a build is an error
#   Decl                  what one app module declares: state, actions, hooks, labels, work
#   AppDecl               an app's ui.app(...): its title and what it declares
#   Ctx                   what a template may name where it is compiled
#   Tmpl                  a compiled template: its type, its getter, its source
#   Action                a named action: its label, handler, condition and hint
#   Node                  one node of a built tree
#   Ev                    the event a canvas's pointer hook is handed
#   Builder               runs a page's build: typed, keyed, every name resolved
#   Ticket                a load's handle from start()
#   LoadView              a load's state in a handler
#   Work                  a named job the engine runs in slices
#   Pictures              the app's own pictures, decoded by the engine
#   -- the calls an app makes  the page verbs (ui.row, ui.list, ...)
"""The UI tree API, `import uitree as ui`: an app module declares typed
state (`ui.state`), named actions and hooks with what they take
(`ui.action`, `ui.hook`), label tables and derives; a page is a function
that describes nodes (`with ui.page(...)`) and reads no state. Templates
and conditions are checked against the declared types when the page is
built, once, at open.

It imports no engine: `tools/uitree_check.py` runs an app's page functions
with this module alone. The replay into the toolkit's verbs is
`uitree_app.py` (the Editor's kinds: `uitree_editor.py`).

Every mistake is a `UIError`: a code, the app's file and line, what was
expected, what was found and the fix. There are no warnings.
"""
import collections
import re
import sys

# The host's introspection, and the one place a board goes without it. A
# board's MicroPython has no frames, signatures or fuzzy matching: there an
# error's file:line reads ?:0, a handler's parameter count is not checked at
# its declaration, a misspelt name gets no "did you mean", and a service's
# post outside a handler goes to the app declared last. Every error code is
# raised on both; tools/uitree_check.py runs the full checks on the host.
try:
    import difflib
    import inspect
    _frame = sys._getframe
except (ImportError, AttributeError):
    difflib = inspect = _frame = None

# Whether the engine keeps a trace (`_trace`) and what feeds it: the host's
# only. A board keeps none, so its frames allocate nothing for one.
TRACING = _frame is not None

# What else a board's MicroPython lacks, met here and nowhere else: `re` has
# no fullmatch (a pattern anchored with `$` matches the same names), a
# function takes no attributes (a wrapper keeps its own __name__ there) and
# has no __module__, and an instance's __dict__ is read-only (the layer keeps
# its own attributes as plain ones, set at __init__). `collections` there has
# no Counter or OrderedDict.move_to_end; the layer uses neither.
if hasattr(re, "fullmatch"):
    _fullmatch = re.fullmatch
else:
    def _fullmatch(pattern, s):
        return re.match(pattern + "$", s)


def _wraps(call, fn):
    try:
        call.__name__, call.__doc__, call.__wrapped__ = fn.__name__, fn.__doc__, fn
    except AttributeError:
        pass
    return call


def module_of(fn):
    """The name of the module that defines `fn` (a board's functions carry
    their globals, not __module__)."""
    return fn.__globals__["__name__"]


def _word(c):
    return c.isalpha() or c.isdigit() or c == "_"


def dotted(s, lead=False):
    """A fragment's flattened key read back as its path: in each word, the
    first "__" with a character on both sides becomes "." ("panel__ok" is
    "panel.ok"). `lead` takes only words that start with a lowercase letter."""
    out, i, n = [], 0, len(s)
    while i < n:
        if not _word(s[i]):
            out.append(s[i])
            i += 1
            continue
        j = i
        while j < n and _word(s[j]):
            j += 1
        w = s[i:j]
        k = w.find("__", 1)
        if k > 0 and k + 2 < len(w) and (not lead or "a" <= w[0] <= "z"):
            w = w[:k] + "." + w[k + 2:]
        out.append(w)
        i = j
    return "".join(out)

# the layer's own files: an error's file:line is the first frame outside them
_SKIP = ("uitree.py", "uitree_app.py", "uitree_editor.py", "uitree_doc.py", "uitree_pics.py")


def _base(path):
    return path.rsplit("/", 1)[-1]


class UIError(Exception):
    def __init__(self, code, what, expected=None, found=None, fix=None, where=None):
        self.code, self.what = code, what
        self.expected, self.found, self.fix = expected, found, fix
        self.where = where or _caller()
        super().__init__(self.text())

    def text(self):
        """The spec's one line: error[CODE] WHERE: WHAT; fix: FIX (expected E, found F)."""
        out = "error[%s] %s:%s: %s" % (self.code, self.where[0], self.where[1], self.what)
        if self.fix is not None:
            out += "; fix: " + self.fix
        if self.expected is not None:
            out += " (expected %s%s)" % (self.expected, "" if self.found is None else ", found %s" % self.found)
        elif self.found is not None:
            out += " (found %s)" % self.found
        return " ".join(part.strip() for part in out.split("\n"))


def _caller():
    if _frame is None:
        return "?", 0
    f = _frame(1)
    while f is not None:
        fn = f.f_code.co_filename
        if _base(fn) not in _SKIP and "contextlib" not in fn:
            return _base(fn), f.f_lineno
        f = f.f_back
    return "?", 0


def _near(k, names):
    if difflib is None:
        return ""
    m = difflib.get_close_matches(k, list(names), 2, 0.6)
    return " or ".join(repr(x) for x in m)


def _pyt(v):
    return {bool: "a bool", int: "an int", float: "a number", str: "a text",
            type(None): "None", list: "a list", tuple: "a tuple", dict: "a dict"}.get(
                type(v), type(v).__name__)


# -- the build in progress, the app replaying, the action running -------------

class _Now:
    building = False        # a build is running
    frame = False           # a replay is running (canvas draws read, never write)
    app = None              # the app building, replaying or running a handler
    action = None           # the action whose handler runs
    load_move = False       # a LoadView moving its load's state
    collect = None          # the checker's list: a refused call is recorded and the run goes on


_NOW = _Now()


# -- types --------------------------------------------------------------------

class T:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


BOOL, INT, NUM, TEXT = T("bool"), T("int"), T("number"), T("text")


class ImageT(T):
    """G9: a picture the engine decodes, caches and veils. Its zero is no
    picture; a value is a `Pic` (`image.store(path)`, `image.own(key)`)."""
    init = None


IMAGE = ImageT("image")


class Pic:
    """An image value: where the engine finds the picture. `store` is a path
    in the console's store (KIND/NAME, a .moyimg drawing); `own` a key into
    the app's own pictures (`pictures.put`)."""
    __slots__ = ("kind", "key")

    def __init__(self, kind, key):
        if kind not in ("store", "own") or not isinstance(key, str) or not key:
            raise UIError("S02", "an image is image.store(\"KIND/NAME\") or image.own(\"KEY\")",
                          found="%r %r" % (kind, key), fix='image.store("drawings/NAME") for a file in the store, image.own("KEY") for one the app puts')
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "key", key)

    def __setattr__(self, k, v):
        raise UIError("S03", "an image value is whole; write a new one", fix='s.NAME = image.own("KEY"): write a new image value whole')

    def __eq__(self, o):
        return isinstance(o, Pic) and o.kind == self.kind and o.key == self.key

    def __hash__(self):
        return hash((self.kind, self.key))

    def __repr__(self):
        return "%s:%s" % (self.kind, self.key)


class image:
    """The Python spelling of an image value."""
    @staticmethod
    def store(path):
        if not isinstance(path, str) or path.count("/") != 1:
            raise UIError("S02", "image.store(%r): a store path is KIND/NAME" % (path,),
                          fix='image.store("drawings/Cat")')
        return Pic("store", path)

    @staticmethod
    def own(key):
        return Pic("own", key)


class Enum(T):
    def __init__(self, values, init=None):
        T.__init__(self, "enum(%s)" % ", ".join(values))
        self.values, self.init = tuple(values), values[0] if init is None else init


class Load(Enum):
    """M2: a load's state, the engine's: never, loading, failed, loaded.
    A handler moves it with start() (which hands a ticket and makes every
    earlier ticket stale), done() and fail(); a post carrying a stale
    ticket is dropped before any handler runs."""

    def __init__(self):
        Enum.__init__(self, ("never", "loading", "failed", "loaded"), "never")
        self.name = "load"


class Record(T):
    def __init__(self, fields):
        T.__init__(self, "record(%s)" % ", ".join(fields))
        self.fields = {k: _decl(v, k)[0] for k, v in fields.items()}
        self.init = {k: _decl(v, k)[1] for k, v in fields.items()}


class Model(T):
    def __init__(self, key, fields):
        if "key" in fields:
            raise UIError("S07", "a model's field is named 'key'", expected="key= names the key field",
                          fix='name the field for what it holds: ui.model(key="id", id="", ...)')
        T.__init__(self, "model(key=%s; %s)" % (key, ", ".join(fields)))
        self.key = key
        self.fields = {k: _decl(v, k)[0] for k, v in fields.items()}
        self.zero = {k: _decl(v, k)[1] for k, v in fields.items()}
        self.init = ()
        self.cells = tuple((k,) + _accept(t) for k, t in self.fields.items())
        # The fields in one order on every Python: a board's dicts (and so its
        # keyword arguments) keep another order than CPython's, and the C
        # store's field indexes, a compiled page's among them, are this order's.
        self.order = tuple(sorted(self.fields))
        # each field of `order` with how the C store takes its cell (uitree_c's push):
        # 0 a text (an enum's value too) as it is, 1 an int, 2 a bool, 3 a number, 4 str() of it
        self.wire = tuple((f, _wire(self.fields[f])) for f in self.order)


def _wire(t):
    return (0 if t is TEXT or type(t) is Enum else 1 if t is INT else 2 if t is BOOL else
            3 if t is NUM else 4)


class RowOf(T):
    def __init__(self, model):
        T.__init__(self, "row_of(%s)" % model)
        self.model, self.init = model, ""


class Opt(T):
    """EXT-1. A value that may be absent (shape 2.1's ui.opt): absent until
    written, draws as nothing, false in a condition."""

    def __init__(self, base):
        T.__init__(self, "opt(%s)" % base)
        self.base, self.init = base, None


def opt(initial):
    t, _v = _decl(initial, "opt")
    if isinstance(t, (Opt, Model, Doc)):
        raise UIError("S13", "ui.opt of %s: only a value may be absent" % t,
                      fix="ui.opt(0), ui.opt(\"\"), ui.opt(ui.record(...)); an empty model is ()")
    return Opt(t)


# EXT-2. DOC_FIELDS (beside SYS) are the fields a document shows to
# templates, all read-only: the caret (from 1), the selection's length in
# characters, the text's lines, the find's current match and count, undo and
# redo, the edit count and the word before the caret.


class Doc(T):
    """EXT-2. A text document the engine owns (ui.doc): the buffer, its
    caret and selection, its undo journal and its view. A handler reads and
    changes it through its verbs; the text editor node shows it and takes
    typed keys into it with no app code."""

    def __init__(self, text, lang):
        T.__init__(self, "doc(%s)" % lang)
        self.text, self.lang, self.init = text, lang, None


def doc(text="", lang="plain"):
    if lang not in ("plain", "python", "lua", "json", "markdown"):
        raise UIError("S12", "ui.doc lang=%r" % lang, expected="plain, python, lua, json, markdown", fix='lang="plain" (or python, lua, json, markdown)')
    return Doc(text, lang)


def enum(*values, init=None):
    if len(values) < 2 or len(set(values)) != len(values) or not all(
            isinstance(v, str) and v for v in values):
        raise UIError("E03", "an enum needs two or more distinct non-empty texts",
                      found=repr(values), fix='ui.enum("ok", "waiting", init="ok"); one value is a bool')
    if init is None:
        raise UIError("E01", "an enum names its initial value", expected="init= one of " + ", ".join(values),
                      fix='ui.enum(%s, init=%r)' % (", ".join(repr(v) for v in values), values[0]))
    if init not in values:
        raise UIError("E02", "enum init=%r is not one of its values" % init, expected=", ".join(values), fix='init=%r' % values[0])
    return Enum(values, init)


def record(**fields):
    return Record(fields)


def model(key=None, **fields):
    """Rows of `fields`, each row named by its `key` field. No field is
    called `key` (S07): the word names the key field."""
    if key not in fields:
        raise UIError("S07", "a model names its key field, one of its fields",
                      expected="key= one of %s" % ", ".join(fields), found=repr(key),
                      fix='ui.model(key="ssid", ssid="", ...)')
    m = Model(key, fields)
    if m.fields[key] not in (TEXT, INT):
        raise UIError("S07", "a model's key field is a text or an int",
                      found="%s is %s" % (key, m.fields[key]), fix='key= names a text or an int field')
    return m


def row_of(model_name):
    return RowOf(model_name)


def _decl(v, name):
    """A declaration's type and initial value."""
    if isinstance(v, T):
        return v, v.init
    t = {bool: BOOL, int: INT, float: NUM, str: TEXT}.get(type(v))
    if t is not None:
        return t, v
    fix = {list: 'a list is a model: %s=ui.model(key="id", id="", ...)' % name,
           tuple: 'a list is a model: %s=ui.model(key="id", id="", ...)' % name,
           dict: "a dict is a record: %s=ui.record(field=initial, ...)" % name,
           type(None): "None has no type: give a typed initial value (0, \"\", False) "
                       "or an enum: ui.enum(\"none\", ...)"}.get(
        type(v), "a typed initial value: 0, \"\", False, ui.enum(...), ui.record(...); "
                 "a handler is declared with ui.action or ui.hook")
    raise UIError("S13", "%r has no type the state can hold" % name,
                  expected="a bool, int, number, text, ui.enum, ui.record, ui.model or ui.row_of",
                  found=repr(v) + " (%s)" % _pyt(v), fix=fix)


# the plain values a write equal to the one held needs no check for
_SAME_OK = (bool, int, str, float, type(None))


def _check(t, v, what):
    """`v` as type `t` stores it, or an error that names the fix."""
    if isinstance(t, Opt):
        return None if v is None else _check(t.base, v, what)
    if t is IMAGE:
        if v is None or isinstance(v, Pic):
            return v
        raise UIError("S02", "write of %s to %s, an image" % (_pyt(v), what), expected="an image",
                      found="%r (%s)" % (v, _pyt(v))[:60],
                      fix='ui.image.store("KIND/NAME") for a store file, ui.image.own(KEY) for a picture '
                          'the app put (ui.pictures.put), None for no picture')
    if isinstance(t, Doc):
        raise UIError("S03", "a write of a whole document to %s" % what, found=repr(v)[:40],
                      fix="%s.set_text(text), or its verbs: insert, go, find, undo" % what)
    ok = (t is BOOL and type(v) is bool or t is INT and type(v) is int
          or t is NUM and type(v) in (int, float) or t is TEXT and type(v) is str
          or isinstance(t, RowOf) and type(v) is str
          or isinstance(t, Enum) and type(v) is str and v in t.values)
    if ok:
        return v
    if isinstance(t, Record):
        if isinstance(v, RecView):
            v = dict(v._d())
        if isinstance(v, dict) and set(v) == set(t.fields):
            return {k: _check(t.fields[k], v[k], what + "." + k) for k in t.fields}
    elif isinstance(t, Model):
        if isinstance(v, (list, tuple, ModelView)):
            rows, keys = [], set()
            for r in v:
                r = _row(t, r, what)
                k = r._f[t.key]
                if k in keys:
                    raise UIError("S04", "two rows of %s keyed %r" % (what, k),
                                  fix="a model's key field is unique: give each row its own %s" % t.key)
                keys.add(k)
                rows.append(r)
            return tuple(rows)
    fixes = {BOOL: "True or False; a test is written as one (x != \"\")",
             INT: "int(...)", NUM: "a number", TEXT: "str(...), or a template where it is drawn"}
    fix = fixes.get(t) or ("one of %s" % ", ".join(repr(x) for x in t.values)
                           if isinstance(t, Enum) else "a whole value of %r" % t)
    if isinstance(t, Enum) and type(v) is str:
        n = _near(v, t.values)
        fix = "did you mean %s?" % n if n else fix
    raise UIError("S02", "write of %s to %s" % (_pyt(v), what), expected=repr(t),
                  found="%r (%s)" % (v, _pyt(v)), fix=fix)


class Row:
    """A model's row: read by field (`row.ssid`), never written in place."""
    __slots__ = ("_m", "_f")

    def __init__(self, m, f):
        object.__setattr__(self, "_m", m)
        object.__setattr__(self, "_f", f)

    def __getattr__(self, k):
        f = self._f
        if k in f:
            return f[k]
        raise UIError("S04", "a row has no field %r" % k, expected=", ".join(self._m.fields),
                      fix=("did you mean %s?" % _near(k, self._m.fields)) or None)

    def __setattr__(self, k, v):
        raise UIError("S03", "a write into a row (row.%s = ...)" % k,
                      fix="write it through the model: s.<model>.update(%r, %s=...)"
                      % (self._f[self._m.key], k))

    def __getitem__(self, k):
        raise UIError("S04", "a row is read by field, not by index", found="row[%r]" % (k,),
                      fix="row.%s" % k if isinstance(k, str) else
                      "row." + ", row.".join(self._m.fields))

    def __iter__(self):
        raise UIError("S04", "a row is read by field, not unpacked",
                      fix="row." + ", row.".join(self._m.fields))

    def __eq__(self, o):
        return isinstance(o, Row) and o._f == self._f

    def __hash__(self):
        return hash(self._f[self._m.key])

    def __repr__(self):
        return "Row(%s)" % ", ".join("%s=%r" % kv for kv in self._f.items())


_NONE = type(None)


def _accept(t):
    """A model field's check compiled: the types `_check` takes for `t`
    without converting, compared by identity, and an enum's values (`None`
    where only `_check` decides)."""
    opt = isinstance(t, Opt)
    b = t.base if opt else t
    ts = ((bool,) if b is BOOL else (int,) if b is INT else (int, float) if b is NUM
          else (str,) if b is TEXT or isinstance(b, (RowOf, Enum)) else None)
    if ts is None:
        return None, None
    return (ts + (_NONE,) if opt else ts), (b.values if isinstance(b, Enum) else None)


def _row(m, d, what):
    if type(d) is Row:
        d = d._f
    if type(d) is dict:
        f = {}
        for k, ts, vals in m.cells:
            v = d.get(k, _NONE)
            if v is _NONE:
                break
            if ts is not None and type(v) in ts and (vals is None or v in vals or v is None):
                f[k] = v
            else:
                f[k] = _check(m.fields[k], v, "%s.%s" % (what, k))
        else:
            if len(d) == len(f):
                return Row(m, f)
    return _row_slow(m, d, what)


def _row_slow(m, d, what):
    if isinstance(d, Row):
        d = d._f
    if not isinstance(d, dict):
        raise UIError("S04", "a row of %s is a dict of its fields" % what,
                      expected=", ".join(m.fields), found="%r (%s)" % (d, _pyt(d)), fix='{%s}' % ', '.join('%r: ...' % f for f in m.fields))
    miss = [f for f in m.fields if f not in d]
    extra = [f for f in d if f not in m.fields]
    if miss or extra:
        raise UIError("S04", "a row of %s with %s" % (what, " and ".join(
            x for x in ("missing field " + ", ".join(miss) if miss else "",
                        "unknown field " + ", ".join(extra) if extra else "") if x)),
                      expected=", ".join(m.fields), found=", ".join(d),
                      fix="give every row exactly the declared fields")
    return Row(m, {k: _check(m.fields[k], d[k], "%s.%s" % (what, k)) for k in m.fields})


class ModelView:
    """A model read in a handler: iterate it, `get(key)`; write it whole or
    by key with `put`, `update`, `remove`."""

    def __init__(self, st, name):
        self._s, self._n = st, name
        self._t = st._t[name]

    def _rows(self):
        return self._s._v[self._n]

    def __iter__(self):
        return iter(self._rows())

    def __len__(self):
        return len(self._rows())

    def __getitem__(self, i):
        if isinstance(i, slice):
            return self._rows()[i]
        if not isinstance(i, int):
            raise UIError("S04", "a model is indexed by position; a row by key is get()",
                          found="[%r]" % (i,), fix="s.%s.get(%r)" % (self._n, i))
        return self._rows()[i]

    def __setitem__(self, i, v):
        raise UIError("S03", "a write into a model by index",
                      fix="s.%s.put(row) or s.%s.update(key, field=...)" % (self._n, self._n))

    def get(self, key):
        for r in self._rows():
            if r._f[self._t.key] == key:
                return r
        return None

    def put(self, row):
        r = _row(self._t, row, self._n)
        k, kf = r._f[self._t.key], self._t.key
        old = list(self._rows())
        if self.get(k) is None:
            old.append(r)
        else:
            old = [r if x._f[kf] == k else x for x in old]
        setattr(self._s, self._n, old)

    def update(self, key, **fields):
        r = self.get(key)
        if r is None:
            raise UIError("S04", "no row of %s keyed %r" % (self._n, key), fix='s.%s.get(key) is None for a key that may be gone; update only a row that is there' % self._n)
        d = dict(r._f)
        for k, v in fields.items():
            if k not in d:
                raise UIError("S04", "a row of %s has no field %r" % (self._n, k),
                              expected=", ".join(d), fix=("did you mean %s?" % _near(k, d)) or None)
            d[k] = v
        setattr(self._s, self._n, [Row(self._t, d) if x is r else x for x in self._rows()])
        _check(self._t, self._s._v[self._n], self._n)

    def remove(self, key):
        setattr(self._s, self._n, [x for x in self._rows() if x._f[self._t.key] != key])

    def order(self, keys):
        """Rows in the order of `keys`, every key once: no field is copied."""
        rows = {r._f[self._t.key]: r for r in self._rows()}
        keys = list(keys)
        if sorted(map(str, keys)) != sorted(map(str, rows)) or len(set(keys)) != len(keys):
            raise UIError("S04", "s.%s.order(keys): the keys are not the model's, each once" % self._n,
                          expected=", ".join(map(repr, rows)), found=", ".join(map(repr, keys)), fix='s.%s.order([...]): every key of the model, each once' % self._n)
        setattr(self._s, self._n, [rows[k] for k in keys])


class RecView:
    def __init__(self, st, name):
        object.__setattr__(self, "_s", st)
        object.__setattr__(self, "_n", name)

    def _d(self):
        return self._s._v[self._n]

    def __getattr__(self, k):
        d = self._d()
        if k not in d:
            raise UIError("S04", "record %s has no field %r" % (self._n, k), expected=", ".join(d), fix='s.%s.FIELD, one of %s' % (self._n, ', '.join(d)))
        return d[k]

    def __setattr__(self, k, v):
        raise UIError("S03", "a nested write into a record (s.%s.%s = ...)" % (self._n, k),
                      fix="replace it whole, or s.%s.set(%s=...)" % (self._n, k))

    def set(self, **kw):
        setattr(self._s, self._n, dict(self._d(), **kw))


class DocView:
    """EXT-2. A document read and changed in a handler. Every verb that
    changes it is one write in the trace; reads are free. The caret and the
    selection are the document's, so a verb that moves them is a write too."""

    VERBS = ("set_text", "insert", "go", "place", "select", "find", "undo", "redo", "complete",
             "indent", "outdent", "copy", "cut", "paste", "select_all")

    def __init__(self, st, name):
        object.__setattr__(self, "_s", st)
        object.__setattr__(self, "_n", name)

    def __getattr__(self, k):
        b = self._s._v[self._n]
        if k in self.VERBS:
            def verb(*a):
                if _NOW.building or _NOW.frame:
                    raise UIError("B02" if _NOW.building else "S06", "s.%s.%s() inside a %s" % (
                        self._n, k, "build" if _NOW.building else "draw"),
                        fix="call it in a handler; name the handler as an action")
                before = b.summary()
                got = getattr(b, k)(*a)
                after = b.summary()
                if after != before:
                    self._s._ver[self._n] += 1
                    _trace("state", self._n, "%s(%s): %s -> %s%s" % (
                        k, ", ".join(repr(x)[:20] for x in a), before, after,
                        "   (in %s)" % _NOW.action if _NOW.action else ""))
                return got
            return verb
        if k in ("text", "lines", "word", "defs", "completions", "line", "col", "sel",
                 "matches", "match", "can_undo", "can_redo", "edits", "count"):
            return getattr(b, k)
        raise UIError("S04", "a document has no %r" % k,
                      expected="verbs " + ", ".join(self.VERBS) +
                      "; reads text, lines, word, defs, completions, line, col, sel, edits", fix='call one of its verbs, or read one of its fields')

    def __setattr__(self, k, v):
        raise UIError("S03", "a write into a document (s.%s.%s = ...)" % (self._n, k),
                      fix="s.%s.go(line, col) moves the caret; set_text replaces the text" % self._n)


class State:
    """The app's declared, typed state. A read inside a build is an error;
    so is a write inside a build or a frame."""

    def __init__(self, decl):
        t, v = {}, {}
        for k, x in decl.items():
            t[k], v[k] = _decl(x, k)
        for k, x in t.items():
            if isinstance(x, RowOf) and not isinstance(t.get(x.model), Model):
                raise UIError("S11", "%s=ui.row_of(%r): no model named %r" % (k, x.model, x.model),
                              expected="a model name: " + (", ".join(
                                  n for n, y in t.items() if isinstance(y, Model)) or "none declared"), fix='declare the model too: %s=ui.model(key=..., ...)' % x.model)
        for k in v:
            if isinstance(t[k], Model):
                v[k] = ()
            elif isinstance(t[k], Doc):
                from uitree_doc import DocBuf
                v[k] = DocBuf(t[k].text, t[k].lang)
        object.__setattr__(self, "_t", t)
        object.__setattr__(self, "_v", v)
        object.__setattr__(self, "_init", dict(v))    # what each engine over this state starts from
        object.__setattr__(self, "_ver", dict.fromkeys(t, 0))
        object.__setattr__(self, "_derive", {})       # name -> its getter, in evaluation order
        object.__setattr__(self, "_dsrc", {})         # name -> (its expression, the names it reads)
        object.__setattr__(self, "_owner", None)      # the engine whose values these are (uitree_app)
        object.__setattr__(self, "_tickets", {})      # a load's name -> its current ticket

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        if k not in self._t:
            raise _undeclared(k, self._t)
        if _NOW.building and isinstance(self._t[k], Doc):
            raise UIError("B01", "s.%s read inside a build" % k, found="s.%s" % k,
                          fix='"{%s.line}" or another field (%s) in a text; '
                              'ui.text_editor(%r) shows it' % (k, ", ".join(DOC_FIELDS), k))
        if _NOW.building:
            e = UIError("B01", "s.%s read inside a build" % k,
                        expected="a build names state; it does not read it",
                        found="s.%s" % k,
                        fix='"{%s}" in a text, "%s" in a condition or for a two-way control; '
                            'ui.switch(%r, cases) or rows by kind if the structure depends on it'
                            % (k, k, k))
            if _NOW.collect is None:
                raise e
            _NOW.collect.append(e)      # the checker's run goes on with the declared value
            return self._v[k]
        if k in self._derive:
            return self._derive[k]()
        t = self._t[k]
        if isinstance(t, Load):
            return LoadView(self, k)
        if isinstance(t, Model):
            return ModelView(self, k)
        if isinstance(t, Record):
            return RecView(self, k)
        if isinstance(t, Doc):
            return DocView(self, k)
        return self._v[k]

    def __setattr__(self, k, val):
        if k not in self._t:
            raise _undeclared(k, self._t)
        if isinstance(self._t[k], Load) and not _NOW.load_move:
            raise UIError("S08", "s.%s = ...: a load's state is the engine's" % k,
                          fix="s.%s.start() (it hands the ticket), s.%s.done(), s.%s.fail()" % (k, k, k))
        if _NOW.action is None and not (_NOW.building or _NOW.frame) and _NOW.app is None:
            raise UIError("S06", "s.%s written outside a handler" % k,
                          expected="writes happen in handlers; a service posts",
                          fix='ui.post("HOOK", value) from the service, and the hook writes s.%s' % k)
        if k in self._derive:
            raise UIError("S08", "s.%s = ...: %s is derived; the engine computes it, nobody writes it" % (k, k),
                          fix="write the names its expression reads")
        if _NOW.building:
            e = UIError("B02", "s.%s written inside a build" % k,
                        expected="a build names state; handlers write it",
                        fix="name the handler as an action (on=\"name\"); do not call it")
            if _NOW.collect is None:
                raise e
            _NOW.collect.append(e)
            return
        if _NOW.frame:
            raise UIError("S06", "s.%s written inside a draw" % k,
                          expected="writes happen in handlers",
                          fix="write it in the handler of the event; a draw reads")
        old = self._v[k]
        if type(val) is type(old) and type(val) in _SAME_OK and val == old:
            return                      # the value it holds, checked when it was written: a poll's read, no string made
        new = _check(self._t[k], val, "s." + k)
        if new == old and not isinstance(self._t[k], Model):
            return
        self._v[k] = new
        self._ver[k] += 1
        if TRACING:                     # a board keeps no trace: no line made for it
            _trace("state", k, "%s%s" % (_change(self._t[k], old, new),
                                         "   (in %s)" % _NOW.action if _NOW.action else ""))


def derive(name, *, cond=None, text=None):
    """A value the engine computes from state: `cond=` a condition (a bool)
    or `text=` a template (a text). Handlers read it; nobody writes it."""
    where = _caller()
    _declaring("derive")
    if (cond is None) == (text is None):
        raise UIError("N01", "ui.derive(%r) takes one of cond= or text=" % name, where=where,
                      fix='ui.derive(%r, cond="count > 1") or ui.derive(%r, text="{n} left")' % (name, name))
    _derive(_st(), name, cond if cond is not None else text, "cond" if cond is not None else "tmpl", where)
    return name


def _derive(st, name, src, kind, where):
    """Compile one derive over declared state (and derives declared before
    it): typed now, read by templates, conditions and handlers."""
    global DEPS
    if name in st._t:
        raise UIError("U02", "derive %s: the name is taken" % name, where=where, fix='give the derive a name no state, action or derive has')
    DEPS = set()
    try:
        if kind == "cond":
            g = cond_full(src, Ctx(), where)
            t = BOOL
        else:
            tm = tmpl(src, Ctx(), where)
            g, t = ((tm.g if tm.t is TEXT else (lambda row, g0=tm.g: _text(g0(row)))), TEXT)
    finally:
        deps, DEPS = DEPS, None
    st._t[name] = t
    st._v[name] = None
    st._ver[name] = 0
    st._derive[name] = (lambda g=g: (bool(g(None)) if t is BOOL else g(None)))
    st._dsrc[name] = (src, sorted(deps))


def commit(app):
    """The end of a handler's commit: the engine's own moves (a list's cur
    after its row went), then every derive."""
    settle = getattr(type(app), "settle", None)     # the class's: a bound method is made at each getattr
    if settle is not None:
        settle(app)
    derive_commit(app)
    done = getattr(type(app), "committed", None)    # an engine holding state of its own takes the commit's writes
    if done is not None:
        done(app)


def derive_commit(app):
    """After a commit: each derive whose value moved is traced once, with the
    names that moved it."""
    st = app.s
    if not st._derive:
        return
    seen = getattr(app, "_dseen", None)
    if seen is None:
        _derive_seen(app, st)
        return
    total = 0
    for k in st._ver:
        total += st._ver[k]
    if total == seen["sum"] and not seen["docs"]:
        return                          # no version moved (they only grow): no derive did
    _derive_moved(app, st, seen)


# derive_commit's halves that make sets and lists: apart, because a
# comprehension naming a function's locals makes their cells at each call
# of it on a board, and derive_commit runs at every frame's commit
def _derive_seen(app, st):
    app._dseen = {"ver": dict(st._ver), "val": {k: f() for k, f in st._derive.items()},
                  "sum": -1,            # a derive over a document or an engine value: no version says
                  "docs": any(isinstance(st._t[d], Doc) for k in st._dsrc
                              for d in st._dsrc[k][1] if d in st._t)
                  or any("@" in st._dsrc[k][0] for k in st._dsrc)}


def _derive_moved(app, st, seen):
    moved = {k for k, v in st._ver.items() if seen["ver"].get(k) != v}
    was, _NOW.app = _NOW.app, app
    try:
        for k, f in st._derive.items():
            v = f()
            if v != seen["val"][k]:
                why = [d for d in st._dsrc[k][1] if d in moved]
                _trace("derive", k, "%r -> %r   (from %s)" % (seen["val"][k], v, ", ".join(why) or "?"))
                seen["val"][k] = v
                st._ver[k] += 1
                moved.add(k)
    finally:
        _NOW.app = was
    seen["ver"] = dict(st._ver)
    total = 0
    for k in st._ver:
        total += st._ver[k]
    seen["sum"] = total


def _undeclared(k, names):
    n = _near(k, names)
    return UIError("S01", "no state named %r" % k, expected="one of " + ", ".join(names),
                   found=repr(k), fix="did you mean %s?" % n if n else
                   "declare it: ui.state(..., %s=<initial value>)" % k)


def _change(t, old, new):
    """A write as the trace reads it; a model's by key."""
    if not isinstance(t, Model):
        return "%r -> %r" % (old, new)
    a = {r._f[t.key]: r._f for r in old}
    b = {r._f[t.key]: r._f for r in new}
    add = [k for k in b if k not in a]
    gone = [k for k in a if k not in b]
    chg = [k for k in b if k in a and a[k] != b[k]]
    kept = [k for k in b if k in a]
    moved = sum(1 for x, y in zip([k for k in a if k in b], kept) if x != y)
    if len(add) + len(gone) + len(chg) == 1:        # one row: say which
        if chg:
            k = chg[0]
            return "[%d rows] %r: %s" % (len(new), k, ", ".join("%s %r -> %r" % (f, a[k][f], b[k][f])
                                                                for f in b[k] if a[k][f] != b[k][f]))
        return "[%d rows] %s%r" % (len(new), "+" if add else "-", (add or gone)[0])
    return "[%d rows] +%d -%d ~%d moved %d" % (len(new), len(add), len(gone), len(chg), moved)


# -- declarations: each app module's state, actions, hooks, labels and work -----

def _command(name):
    """A command (push, pop, focus, after, post) is a handler's: never a
    build's (B02), never outside a handler (A10). Returns the app."""
    if _NOW.building:
        raise UIError("B02", "ui.%s inside a build" % name, expected="a build names; handlers act",
                      fix='name an action the node fires (on="NAME"), and call ui.%s in its handler' % name)
    if _NOW.app is None:
        raise UIError("A10", "ui.%s outside a handler" % name,
                      fix="call ui.%s from a handler or the opened hook; a service posts" % name)
    return _NOW.app


def _declaring(what):
    """A declaration stands at the module's top level, never in a build (B03)."""
    if _NOW.building:
        raise UIError("B03", "ui.%s inside a page function" % what,
                      expected="declarations at the module's top level",
                      fix="move ui.%s(...) out of the page function, above it" % what)


class Decl:
    """What one app module declares at import: its state, its actions and
    hooks in declaration order (the hints band's order), its label tables,
    its work, and the posts its services have made since the last frame."""

    def __init__(self, module, st):
        self.module = module
        self.state = st
        self.actions = {}
        self.labels = {}
        self.works = {}
        self.posts = []
        self.app = None         # its `ui.app(...)` declaration (§19.1), where it is an app


_DECLS = {}             # module name -> its Decl, made by its ui.state(...)
_DECLARING = [None]     # the Decl the last ui.state(...) made: a board's only way to the declaring module


def _caller_module():
    """The module whose code made the call (the first frame outside this
    layer); None where there are no frames (a board)."""
    if _frame is None:
        return None
    f = _frame(1)
    while f is not None:
        if _base(f.f_code.co_filename) not in _SKIP and "contextlib" not in f.f_code.co_filename:
            return f.f_globals.get("__name__")
        f = f.f_back
    return None


def decl_of(module):
    """The declaration a module made with ui.state(...): what an engine hosts.
    Found by the module's state object, which needs no frames."""
    name = module if isinstance(module, str) else module.__name__
    mod = sys.modules.get(name) if isinstance(module, str) else module
    d = _DECLS.get(name)
    if d is None and mod is not None:
        held = [v for v in mod.__dict__.values() if isinstance(v, State)]
        d = next((x for x in _DECLS.values() if any(x.state is v for v in held)), None)
    if d is None:
        raise UIError("S01", "module %s declares no state" % name, where=(name, 0),
                      expected="an app module calls s = ui.state(...) once, before its actions",
                      fix="s = ui.state(...) at the top of %s" % name)
    return d


def _d():
    """The declaration in force: the app running, else the calling module's
    (on a board, the one declared last)."""
    if _NOW.app is not None:
        return _NOW.app.decl
    if _frame is None:
        d = _DECLARING[0]
        name = "this module"
    else:
        name = _caller_module()
        d = _DECLS.get(name)
    if d is None:
        raise UIError("S01", "no state declared in %s" % name,
                      expected="s = ui.state(...) before any other ui call of the module",
                      fix="s = ui.state(...) at the top of %s" % name)
    return d


def state(**decl):
    """Declare the module's state (once): the start of an app's declaration."""
    _declaring("state")
    name = _caller_module()
    d = Decl(name, State(decl))
    _DECLS[name if name is not None else id(d)] = d
    _DECLARING[0] = d
    return d.state


def _st():
    return _d().state


class AppDecl:
    """An app's `ui.app(...)` (§19.1): its title (a template over the state),
    and the fields the console reads to place and name it."""

    def __init__(self, title, g, fields, where):
        self.title_src, self.title_g, self.where = title, g, where
        self.icon = fields.get("icon", "")
        self.size = fields.get("size")
        self.min = fields.get("min")
        self.back = fields.get("back", "pages")
        self.glass = fields.get("glass", "never")
        self.slots = tuple(fields.get("slots", ()))

    def title(self):
        return _text(self.title_g(None))


APP_FIELDS = ("icon", "size", "min", "back", "glass", "slots")
GLASS = ("never", "may", "first")


def _frac(v):
    if type(v) is int:
        return v > 0
    if not isinstance(v, str) or v.count("/") != 1:
        return False
    a, b = v.split("/")
    return a.isdigit() and b.isdigit() and 0 < int(a) <= int(b)


def app(title, **fields):
    """The app's declaration (§19.1), once, at the top level: its title (a
    template over the state: the bar's zone, the strip, the chip), `icon=`,
    `size=` (w, h: desk units or a fraction of the glass, "7/8"), `min=`
    (w, h), `back=` ("pages", or an action run where no page is left to
    pop), `glass=` (never, may, first) and `slots=`."""
    where = _caller()
    _declaring("app")
    d = _d()
    if d.app is not None:
        raise UIError("D01", "a second ui.app declaration", where=where,
                      found="the first at %s:%s" % d.app.where, fix="one ui.app(...) an app")
    for k in fields:
        if k not in APP_FIELDS:
            raise UIError("D01", "ui.app has no field %r" % k, where=where, expected=", ".join(APP_FIELDS),
                          fix="drop it: where a window stands is the console's" if k in (
                              "x", "y", "place", "z", "focus", "show") else
                          'a tools node in the tree' if k == "tools" else "drop it")
    for k in ("size", "min"):
        v = fields.get(k)
        if v is not None and not (isinstance(v, tuple) and len(v) == 2 and all(
                _frac(x) if k == "size" else type(x) is int and x > 0 for x in v)):
            raise UIError("D02", "ui.app %s=%r" % (k, v), where=where,
                          expected="(w, h), each a size in desk units%s" % (
                              ' or a fraction of the glass ("7/8")' if k == "size" else ""),
                          fix='%s=(480, 320)' % k if k == "min" else 'size=("7/8", "7/8")')
    if fields.get("glass", "never") not in GLASS:
        raise UIError("D01", "ui.app glass=%r" % (fields["glass"],), where=where, expected=", ".join(GLASS),
                      fix='glass="may"')
    b = fields.get("back", "pages")
    if not isinstance(b, str) or not _fullmatch(r"[a-z_][a-z0-9_]*", b):
        raise UIError("D01", "ui.app back=%r" % (b,), where=where,
                      expected='"pages", or the name of an action', fix='back="pages"')
    d.app = AppDecl(title, tmpl(title, Ctx(), where, "title").g, fields, where)
    return d.app


def labels(name, table):
    _declaring("labels")
    if not isinstance(table, dict) or not table:
        raise UIError("T04", "ui.labels(%r) takes a dict of value -> label" % name, fix='ui.labels(%r, {"value": "Label", ...})' % name)
    _d().labels[name] = dict(table)


# -- templates and conditions -------------------------------------------------

DOC_FIELDS = {"line": INT, "col": INT, "lines": INT, "sel": INT, "match": INT, "matches": INT,
              "can_undo": BOOL, "can_redo": BOOL, "edits": INT, "word": TEXT}

# @input: the input in use now, as the toolkit names it (spec r3 §14 leaves
# the spelling open, F-23): keys (a keyboard), tdeck (the T-Deck's), pad, touch
INPUTS = ("keys", "tdeck", "pad", "touch")
SYS = {"desk": BOOL, "handheld": BOOL, "wide": BOOL, "tall": BOOL, "current": BOOL,
       "focus": TEXT, "touch": BOOL, "input": Enum(INPUTS, "keys")}
# §7.9: retired system values, each with its replacement (N04)
SYS_RETIRED = {"symbols": "the engine's glyph fallback: write the symbol", "w": "a size class: @wide, @desk",
               "h": "a size class: @tall, @handheld", "fs": "a size class: @desk, @handheld"}


class Ctx:
    """What a template may name where it is compiled: the row's model inside
    a list, the list's `current` state."""

    def __init__(self, rowt=None, current=None):
        self.rowt, self.current = rowt, current


def _ref(name, ctx, where):
    """A name's type and its getter g(row)."""
    st = _st()
    if name.startswith("@"):
        n = name[1:]
        if n in SYS_RETIRED:
            raise UIError("N04", "%s is retired" % name, where=where, fix=SYS_RETIRED[n])
        if n not in SYS:
            raise UIError("T02", "no system value %r" % name, expected=", ".join("@" + x for x in SYS),
                          where=where, fix='one of ' + ', '.join('@' + x for x in SYS))
        if n == "current":
            if ctx.current is None:
                raise UIError("T05", "@current outside a list with current=", where=where,
                              fix='ui.rows(model, current="state name")')
            key, cur = ctx.rowt.key, ctx.current
            return BOOL, lambda row: row is not None and row._f[key] == st._v[cur]
        if n == "focus":
            return TEXT, lambda row: _NOW.app.focus_key if _NOW.app else ""
        if n == "input":
            return SYS["input"], lambda row: getattr(_NOW.app, "input", "keys") if _NOW.app else "keys"
        return BOOL, lambda row: n in (_NOW.app.classes if _NOW.app else ())
    if name.startswith("."):
        f = name[1:]
        if ctx.rowt is None:
            raise UIError("T05", "{%s}: a row's field outside a list" % name, where=where,
                          fix="use {.field} inside ui.rows/ui.cards; a state name is {name}")
        if f not in ctx.rowt.fields:
            raise UIError("T02", "rows have no field %r" % f, where=where,
                          expected=", ".join(ctx.rowt.fields),
                          fix=("did you mean %s?" % _near(f, ctx.rowt.fields)) or None)
        return ctx.rowt.fields[f], lambda row: row._f[f]
    base, dot, f = name.partition(".")
    if DEPS is not None:
        DEPS.add(base)
    if base in getattr(st, "_derive", {}):
        if dot:
            raise UIError("T03", "{%s}: derive %s is a %s; it has no fields" % (name, base, st._t[base]),
                          where=where, fix='{%s} names the derive whole' % base)
        fn = st._derive[base]
        return st._t[base], lambda row: fn()
    if base not in st._t:
        e = _undeclared(base, st._t)
        e2 = UIError("T02", "{%s}: " % name + e.what, e.expected, e.found, where=where,
                     fix=e.fix if ctx.rowt is None or base not in ctx.rowt.fields else
                     "a row's field is {.%s}" % base)
        raise e2
    t = st._t[base]
    if not dot:
        if isinstance(t, Doc):
            raise UIError("T03", "{%s} is a whole document" % name, where=where,
                          fix="a field: " + ", ".join("{%s.%s}" % (base, f) for f in DOC_FIELDS))
        return t, lambda row: st._v[base]
    if isinstance(t, Doc):
        if f not in DOC_FIELDS:
            n = _near(f, DOC_FIELDS)
            raise UIError("T02", "a document has no field %r" % f, where=where,
                          expected=", ".join(DOC_FIELDS),
                          fix="did you mean %s?" % n if n else "the caret is {%s.line}, {%s.col}" % (base, base))
        return DOC_FIELDS[f], lambda row: getattr(st._v[base], f)
    if isinstance(t, Record):
        if f not in t.fields:
            raise UIError("T02", "record %s has no field %r" % (base, f), where=where,
                          expected=", ".join(t.fields), fix='{%s.FIELD}, one of %s' % (base, ', '.join(t.fields)))
        return t.fields[f], lambda row: st._v[base][f]
    if isinstance(t, RowOf):
        m = st._t[t.model]
        if f not in m.fields:
            raise UIError("T02", "rows of %s have no field %r" % (t.model, f), where=where,
                          expected=", ".join(m.fields),
                          fix=("did you mean %s?" % _near(f, m.fields)) or None)
        mk, mn, z = m.key, t.model, m.zero[f]
        if DEPS is not None:
            DEPS.add(mn)                # a field of the current row moves with its model too

        def g(row):
            k = st._v[base]
            for r in st._v[mn]:
                if r._f[mk] == k:
                    return r._f[f]
            return z
        return m.fields[f], g
    raise UIError("T03", "{%s}: %s is %s; it has no fields" % (name, base, t), where=where,
                  fix="a field is read from a ui.record or a ui.row_of")


DEPS = None        # while a derive compiles: the names it reads


def _truth(t, name, where):
    if isinstance(t, Opt):
        base = _truth(t.base, name, where) if not isinstance(t.base, (Record, Enum)) else (lambda v: True)
        return lambda v: v is not None and base(v)
    if t is BOOL:
        return lambda v: v
    if t is IMAGE:
        return lambda v: v is not None
    if t in (TEXT,) or isinstance(t, RowOf):
        STATS["bare_nonbool"] += 1
        return lambda v: v != ""
    if t in (INT, NUM):
        STATS["bare_nonbool"] += 1
        return lambda v: v != 0
    if isinstance(t, Model):
        STATS["bare_nonbool"] += 1
        return lambda v: len(v) > 0
    raise UIError("T03", "%s is %s: it has no truth" % (name, t), where=where,
                  fix="compare it: %s=%s" % (name, t.values[0] if isinstance(t, Enum) else "..."))


STATS = {"bare_nonbool": 0}


def _joins(src, where):
    """The operators a condition joins its terms with: and is `&&`, or is
    `||`; a single `&` or `|` is C01."""
    i, ops = 0, set()
    while i < len(src):
        ch = src[i]
        if ch in "&|":
            if src[i + 1:i + 2] != ch:
                raise UIError("C01", "condition %r joins with a single %s" % (src, ch), where=where,
                              fix="and is &&, or is ||: %r" % _doubled(src))
            ops.add(ch * 2)
            i += 1
        i += 1
    return ops


def _doubled(src):
    out, i = "", 0
    while i < len(src):
        ch = src[i]
        if ch in "&|":
            out += ch * 2
            i += 2 if src[i + 1:i + 2] == ch else 1
            continue
        out += ch
        i += 1
    return out


def cond(src, ctx, where):
    """A bare condition: name, !name, name=lit, name!=lit, joined by && or
    by ||, never both and never bracketed (a derive does that)."""
    if isinstance(src, bool):
        return lambda row: src
    if not isinstance(src, str) or not src.strip():
        raise UIError("C01", "a condition is a text", found="%r (%s)" % (src, _pyt(src)),
                      fix='shown="on", "!busy", "mode=edit", "a && !b"', where=where)
    if "{" in src or "?" in src:
        raise UIError("C01", "a condition is bare, not a template", found=repr(src),
                      fix=repr(src.replace("{", "").replace("}", "")) if "{" in src
                      else "terms joined by && or by ||; a derive computes anything more",
                      where=where)
    ops = _joins(src, where)
    if "(" in src or ")" in src:
        raise UIError("C03", "condition %r has brackets" % src, where=where,
                      fix='a derive: ui.derive("NAME", cond=%r), and test NAME' % src)
    if len(ops) > 1:
        raise UIError("C02", "condition %r mixes && and ||" % src, where=where,
                      fix='a derive: ui.derive("ok", cond="a && (b || c)"), and shown="ok"')
    any_of = "||" in ops
    terms = []
    for term in src.split("||" if any_of else "&&"):
        term = term.strip()
        neg = term.startswith("!") and not term.startswith("!=")
        body = term[1:] if neg else term
        if "!=" in body:
            name, lit, eq = body.partition("!=")[0], body.partition("!=")[2], False
        elif "=" in body:
            name, lit, eq = body.partition("=")[0], body.partition("=")[2], True
        else:
            name, lit, eq = body, None, None
        name = name.strip()
        t, g = _ref(name, ctx, where)
        if lit is None:
            tr = _truth(t, name, where)
            terms.append((neg, g, tr))
            continue
        lit = lit.strip()
        want = _lit(t, lit, name, where)
        terms.append((neg, g, (lambda w, e: lambda v: (v == w) == e)(want, eq)))

    if any_of:
        def ev_any(row):
            for neg, g, tr in terms:
                if bool(tr(g(row))) != neg:
                    return True
            return False
        return ev_any

    def ev(row):
        for neg, g, tr in terms:
            if bool(tr(g(row))) == neg:
                return False
        return True
    return ev


def cond_full(src, ctx, where):
    """A derive's condition: terms as in `cond`, `!`, and brackets; `&&` and
    `||` may both appear, but one bracket level joins with one of them."""
    _joins(src, where)
    toks, run, i = [], "", 0
    while i < len(src):
        ch = src[i]
        if ch in "()&|":
            if run.strip():
                toks.append(run.strip())
            toks.append(ch if ch in "()" else ch * 2)
            run = ""
            i += 1 if ch in "()" else 2
            continue
        run += ch
        i += 1
    if run.strip():
        toks.append(run.strip())
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def group():
        t = peek()
        if t is None:
            raise UIError("C01", "derive condition %r ends early" % src, where=where, fix='finish the condition: a name, or a comparison NAME OP VALUE')
        if t == "!" or (t.startswith("!") and t.strip() == "!"):
            take()
            g = group()
            return lambda row: not g(row)
        if t == "(":
            take()
            g = level()
            if take_ok(")") is None:
                raise UIError("C01", "derive condition %r: a ( with no )" % src, where=where, fix='close the ( with a )')
            return g
        take()
        if t.endswith("!") and peek() == "(":       # "!(" split as "!" then "("
            g = group()
            return lambda row: not g(row)
        return cond(t, ctx, where)

    def take_ok(want):
        if peek() == want:
            return take()
        return None

    def level():
        parts, ops = [group()], set()
        while peek() in ("&&", "||"):
            ops.add(take())
            parts.append(group())
        if len(ops) > 1:
            raise UIError("C02", "derive condition %r mixes && and || at one level" % src, where=where,
                          fix="bracket one side: a && (b || c)")
        if not ops:
            return parts[0]
        # plain loops: a generator expression is an object a call, and a
        # derive is read every frame (a `shown=` naming it)
        if ops == {"&&"}:
            def ev_all(row):
                for p in parts:
                    if not p(row):
                        return False
                return True
            return ev_all

        def ev_any(row):
            for p in parts:
                if p(row):
                    return True
            return False
        return ev_any
    g = level()
    if peek() is not None:
        raise UIError("C01", "derive condition %r: %r after the end" % (src, peek()), where=where, fix='join two conditions with && or ||')
    return g


def _lit(t, lit, name, where):
    if isinstance(t, Opt):
        t = t.base
    if isinstance(t, Enum):
        if lit not in t.values:
            n = _near(lit, t.values)
            raise UIError("T03", "%s=%s: %s has no value %r" % (name, lit, name, lit), where=where,
                          expected=", ".join(t.values), found=repr(lit),
                          fix="did you mean %s?" % n if n else ('one of ' + ', '.join(t.values)))
        return lit
    if t is BOOL:
        raise UIError("T03", "%s=%s compares a bool" % (name, lit), where=where,
                      fix="%s or !%s" % (name, name))
    if t in (INT, NUM):
        try:
            return int(lit) if t is INT else float(lit)
        except ValueError:
            raise UIError("T03", "%s=%s: %s is %s" % (name, lit, name, t), where=where,
                          found=repr(lit), fix="compare with a number") from None
    if t is TEXT or isinstance(t, RowOf):
        return lit
    raise UIError("T03", "%s is %s: it is not compared" % (name, t), where=where, fix='name it bare as a condition, or compare an enum or a number')


class Tmpl:
    """A compiled template: its type, getter g(row) and its source."""

    def __init__(self, src, t, g):
        self.src, self.t, self.g = src, t, g


def _split_unescaped(s, ch):
    i = 0
    while True:
        i = s.find(ch, i)
        if i < 0:
            return None
        if i == 0 or s[i - 1] != "\\":
            return i
        i += 1


def tmpl(src, ctx, where, prop=None):
    if not isinstance(src, str):
        raise UIError("T01", "%sa template is a text" % ("%s=: " % prop if prop else ""),
                      found="%r (%s)" % (src, _pyt(src)),
                      fix='"{name}" to show a state name' if not callable(src) else
                      "name an action, never a function", where=where)
    key = (src, _st(), ctx.rowt, ctx.current, prop)
    if DEPS is None:                    # a derive's compile records what it reads: never cached
        hit = _TMPLS.get(key)
        if hit is not None:
            return hit
    parts, lit, i, n = [], "", 0, len(src)
    while i < n:
        a, z = src.find("{", i), src.find("}", i)
        j = n if a < 0 and z < 0 else (a if z < 0 else (z if a < 0 else min(a, z)))
        if j > i:                       # a literal run, taken in one slice
            lit += src[i:j]
            i = j
            continue
        c = src[i]
        if src[i + 1:i + 2] == c:
            lit += c
            i += 2
            continue
        if c == "}":
            raise UIError("T01", "template %r: a lone '}'" % src, fix="'}}' for one", where=where)
        if c != "{":
            lit += c
            i += 1
            continue
        k, depth = i + 1, 0             # H8: a branch may hold plain references, one level deep
        while k < len(src) and (src[k] != "}" or depth):
            depth += {"{": 1, "}": -1}.get(src[k], 0)
            k += 1
        if k >= len(src):
            k = -1
        if k < 0:
            raise UIError("T01", "template %r: '{' with no '}'" % src, fix="'{{' for one", where=where)
        if lit:
            parts.append(lit)
        lit = ""
        parts.append(_piece(src[i + 1:k], src, ctx, where))
        i = k + 1
    if lit:
        parts.append(lit)
    if len(parts) == 1 and isinstance(parts[0], tuple):
        t, g = parts[0]
        return _keep(key, Tmpl(src, t, g))
    for p in parts:
        if isinstance(p, tuple) and (p[0] is BOOL or isinstance(p[0], (Model, Record))):
            raise UIError("T03", "template %r: %s in a text" % (src, p[0]), where=where,
                          fix="{name?yes:no} for a bool" if p[0] is BOOL else
                          "a field: {name.field}")
    gs = [p if isinstance(p, str) else p[1] for p in parts]

    def g(row):
        return "".join([x if isinstance(x, str) else _text(x(row)) for x in gs])   # a list: no generator a frame
    return _keep(key, Tmpl(src, TEXT, g))


# Templates compiled once: a page's build runs at each open, and its
# templates are the same text over the same declared types every time.
_TMPLS = {}


def _keep(key, t):
    if DEPS is None:
        _TMPLS[key] = t
    return t


def _text(v):
    return v if isinstance(v, str) else "" if v is None else str(v)


def _piece(body, src, ctx, where):
    q = _split_unescaped(body, "?")
    if q is not None:
        c = cond(body[:q].strip(), ctx, where)
        rest = body[q + 1:]
        colon = _split_unescaped(rest, ":")
        if colon is None:
            raise UIError("T01", "template %r: {cond?a:b} needs both branches" % src, where=where,
                          fix="{%s?%s:} for an empty second" % (body[:q], rest))
        a, b = rest[:colon], rest[colon + 1:]
        if _split_unescaped(b, ":") is not None:
            raise UIError("T01", "template %r: a second ':' in {cond?a:b}" % src, where=where,
                          fix="write a ':' in a branch as '\\:'")
        a, b = a.replace("\\:", ":").replace("\\?", "?"), b.replace("\\:", ":").replace("\\?", "?")
        if "{" in a or "{" in b:        # H8: text with plain references, never another choice
            for br in (a, b):
                for inner in br.split("{")[1:]:
                    if "}" in inner and "?" in inner[:inner.index("}")]:
                        raise UIError("T01", "template %r: a branch holds another choice" % src, where=where,
                                      fix="a derive for the inner choice, read here by name")
            ga, gb = tmpl(a, ctx, where).g, tmpl(b, ctx, where).g
            return TEXT, lambda row: _text(ga(row)) if c(row) else _text(gb(row))
        if _isint(a) and _isint(b):
            a, b = int(a), int(b)
            return INT, lambda row: a if c(row) else b
        return TEXT, lambda row: a if c(row) else b
    head, *pipes = body.split("|")
    t, g = _ref(head.strip(), ctx, where)
    if t is IMAGE:
        raise UIError("T03", "template %r: %s is an image, not a text" % (src, head.strip()), where=where,
                      fix="show it with image= on a card or a picture node; a text says its name")
    if pipes and pipes[0].strip() == "count":
        if not isinstance(t, Model):
            raise UIError("T03", "template %r: count counts a model's rows; %s is %s" % (src, head.strip(), t),
                          where=where, fix='{MODEL|count} over a model')
        g0 = g
        t, g, pipes = INT, (lambda row: len(g0(row))), pipes[1:]
    if pipes and pipes[0].strip() == "bytes":
        if t is not INT or len(pipes) > 1:
            raise UIError("T03", "template %r: bytes formats an int, last" % src, where=where, fix='{N|bytes} over an int, as the last transform')
        g1 = g
        return TEXT, lambda row: _bytes(g1(row))
    if not pipes:
        if isinstance(t, Enum):
            return TEXT, g
        return t, g
    if len(pipes) > 1:
        raise UIError("T01", "template %r: one transform per name" % src, where=where,
                      fix="compute the value in a handler, into a state name or a row field")
    op, _, arg = pipes[0].strip().partition(" ")
    arg = arg.strip()
    if op == "fmt":
        need = {"d": (INT,), "i": (INT,), "x": (INT,), "f": (INT, NUM), "e": (INT, NUM),
                "g": (INT, NUM), "s": (TEXT, INT, NUM)}.get(arg[-1:])
        if not arg.startswith("%") or need is None:
            raise UIError("T01", "template %r: fmt takes one printf spec" % src, where=where,
                          found=repr(arg), fix="{x|fmt %.1f}")
        if t not in need and not (isinstance(t, (Enum, RowOf)) and TEXT in need):
            raise UIError("T03", "template %r: %s does not format %s" % (src, arg, t), where=where,
                          fix="%%d for an int, %%.1f for a number, %%s for a text")
        try:
            arg % ((1 if t is not TEXT else "x"),)
        except (TypeError, ValueError):
            raise UIError("T01", "template %r: bad spec %r" % (src, arg), where=where, fix='{x|fmt %%d}: one printf spec of one value') from None
        return TEXT, lambda row: arg % (g(row),)
    if op == "map":
        tab = _d().labels.get(arg)
        if tab is None:
            raise UIError("T02", "template %r: no label table %r" % (src, arg), where=where,
                          expected=", ".join(_d().labels) or "none declared",
                          fix='ui.labels(%r, {value: "label", ...})' % arg)
        if isinstance(t, Enum):
            miss = [v for v in t.values if v not in tab]
            extra = [v for v in tab if v not in t.values]
            if miss or extra:
                raise UIError("T04", "template %r: table %s against %s" % (src, arg, t), where=where,
                              expected="a label for each of " + ", ".join(t.values),
                              found=("missing " + ", ".join(miss) if miss else "") +
                              (" unknown " + ", ".join(map(str, extra)) if extra else ""), fix='ui.labels(%r, {...}) with a label for each value' % arg)
        elif t is BOOL:
            if set(tab) != {True, False}:
                raise UIError("T04", "template %r: a bool's table has True and False" % src, where=where, fix='ui.labels(NAME, {True: "...", False: "..."})')
        elif "_" not in tab:
            raise UIError("T04", "template %r: a table on %s needs a default" % (src, t), where=where,
                          fix='add "_": "label" for every other value')
        return TEXT, lambda row: tab.get(g(row), tab.get("_", ""))
    if op == "plural":
        one, comma, many = arg.partition(",")
        if t is not INT:
            raise UIError("T03", "template %r: plural of %s" % (src, t), where=where,
                          fix="plural counts an int")
        if not comma or not one or not many:
            raise UIError("T01", "template %r: plural needs both forms" % src, where=where,
                          found=repr(arg), fix="{n|plural message,messages}")
        return TEXT, lambda row: "%d %s" % (g(row), one if g(row) == 1 else many)
    raise UIError("T01", "template %r: no transform %r" % (src, op), where=where,
                  expected="fmt, map, plural", fix="compute it in a handler, into a state name")


def _bytes(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%d KB" % ((n + 512) // 1024)
    return "%.1f MB" % (n / 1048576.0)


def _isint(x):
    return x.lstrip("-").isdigit()


# -- actions ------------------------------------------------------------------

class Action:
    def __repr__(self):
        return "<action %s>" % self.name

    def __init__(self, name, label, fn, when, hint, row, where):
        self.name, self.label, self.fn = name, label, fn
        self.letters = self.icon = ""
        self.hint = label if hint is None else hint
        self.when_src, self.where = when, where
        self.when = cond(when, Ctx(), where) if when is not None else None
        self.row = row          # a row_of name: the handler gets that row
        self.arity = None       # set by the first site that names it
        self.unit = None        # the unit it belongs to: live only while a node bound to it is shown
        self.takes = "none"
        self.hook = False
        self.hint_g = None      # a hint that is a template over the state

    def hint_now(self):
        """The hints band's verb now: `hint=` evaluated when it is a template."""
        return self.hint if self.hint_g is None else _text(self.hint_g(None))

    def cur_row(self):
        st = _st()
        m = st._t[st._t[self.row].model]
        k = st._v[self.row]
        return next((r for r in st._v[st._t[self.row].model] if r._f[m.key] == k), None)

    def can(self, row=None):
        """On now: its `when` holds and it has a row to act on (a node in a
        list acts on its own row)."""
        return self.live() and (self.when is None or self.when(row)) and (
            self.row is None or row is not None or self.cur_row() is not None)

    def live(self):
        return self.unit is None or LIVE is None or self.unit in LIVE


# the units a node bound to is shown this frame (None before the first frame)
LIVE = None


def live_units(root, lenders=None):
    """Walk what is shown: a node not shown hides its subtree, a switch shows
    its one case; every bound node met is a live unit. `lenders`, when given,
    collects the shown `tools ... lend=NAME` by NAME."""
    out = set()

    def walk(n):
        if n.p.get("shown") is not None and not n.p["shown"](None):
            return
        if n.unit:
            out.add(n.unit)
        if lenders is not None and n.kind == "tools" and n.l.get("lend"):
            if n.l["lend"] in lenders:
                raise UIError("L03", "two shown units lend to %s" % n.l["lend"], where=n.where, fix='show one lending unit at a time')
            lenders[n.l["lend"]] = n
        if n.kind == "switch":
            v = _NOW.app.s._v[n.l["name"]]
            case = next((c for c in n.kids if c.l["v"] == v), None) or next(
                (c for c in n.kids if c.l["v"] == "_"), None)
            if case is not None:
                walk(case)
            return
        if n.kind in ("rows", "cards", "table"):
            return
        for k in n.kids:
            walk(k)
    if root is not None:
        walk(root)
    return out


def _params(fn):
    if inspect is None:
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    ps = list(sig.parameters.values())
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in ps) or any(
            p.default is not p.empty for p in ps):
        return -1
    return len(ps)


# EXT-3. The letters, chords and roles an action answers to (§13): a
# chord is "ctrl+<letter>" (a desk keyboard's), a role is one of ROLES,
# which the engine maps to each board's key (the T-Deck's has no Ctrl).
# `confirm` is a confirm that no focused node takes; `back` is the page's
# back once no overlay or pushed page takes it (spec §13.3, an app's back=).
ROLES = ("search", "goto", "complete", "undo", "redo", "play", "more", "tools", "confirm", "back",
         "left", "right", "up", "down")
ARROWS = ("left", "right", "up", "down")
# §7.9: an action's retired props, each with its replacement
RETIRED_ACTION = {"key": "keys=(%(v)r,)", "does": "hint=%(v)r"}


def _letter(k):
    """A single letter: an accelerator where a board has letters (§13.2)."""
    return len(k) == 1 and k.isalpha()


def _chord_byte(k):
    if k.startswith("ctrl+") and len(k) == 6 and k[5].isalpha():
        return ord(k[5].lower()) - 96
    return None


# Enter with Shift: a field's confirm the other way (a find's previous
# match), which the field's on= is not
SHIFT_ENTER = "shift+enter"


# What a handler is handed (`takes=`), and its parameter count.
TAKES = {"none": 0, "row": 1, "arg": 1, "char": 1, "pointer": 1}
_HANDED = {"none": "nothing", "row": "the row", "arg": "the site's arg=", "char": "the typed character",
           "pointer": "a pointer event (ui.Ev: kind, x, y, source)"}
OFFS = ("hide", "grey")


def _declare(name, label, fn, takes, where, what):
    _declaring(what)
    if not isinstance(name, str) or not _fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise UIError("A03", "%s name %r" % (what, name), where=where,
                      expected="a lower-case name: letters, digits and _",
                      fix='ui.%s("save", ...)' % what)
    if not callable(fn):
        raise UIError("A03", "ui.%s(%r): the handler is not a function" % (what, name), where=where,
                      found="%r (%s)" % (fn, _pyt(fn)),
                      fix="pass the function itself: %s, not its result" % getattr(fn, "__name__", "fn"))
    if takes == "change":
        raise UIError("N04", "ui.%s(%r) takes=\"change\" is retired" % (what, name), where=where,
                      expected='takes="none": a two-way control\'s handler reads the state it changed',
                      fix='drop takes=, and read s.NAME in the handler')
    if takes not in TAKES:
        raise UIError("A09", "ui.%s(%r) takes=%r" % (what, name, takes), where=where,
                      expected="one of " + ", ".join(TAKES),
                      fix='takes="none" (the default) or the one thing every site hands it')
    d = _d()
    if name in d.actions or name in d.works:
        first = d.actions[name].where if name in d.actions else d.works[name].where
        raise UIError("U02", "%r declared twice" % name, where=where,
                      found="first at %s:%s" % first, fix="one name, one action or hook")
    if name in d.state._t:
        raise UIError("U02", "%s %r has the name of a state" % (what, name), where=where,
                      expected="one name in one key space: a state, an action or a hook",
                      fix="rename one: the state for what it holds (%s_word, %s_on), the %s for what it does"
                          % (name, name, what))
    got, want = _params(fn), TAKES[takes]
    if got is not None and got != want:
        f = getattr(fn, "__name__", "handler")
        raise UIError("A03", "%s %r takes=%r: its handler is handed %s" % (what, name, takes, _HANDED[takes]),
                      where=where,
                      expected="def %s(%s)" % (f, takes if want else ""),
                      found="def %s(%s)" % (f, "*args or defaults" if got == -1 else
                                            ", ".join("p%d" % i for i in range(got))),
                      fix="give %s exactly %d parameter%s, no defaults, or declare what it takes"
                          % (f, want, "" if want == 1 else "s"))
    return d


def action(name, label, fn, *, takes="none", when=None, off=None, hint=None, row=None,
           chars=None, keys=(), icon=None, unit=None, push=None):
    """An action a person sees: its label names it on every node and in the
    hints band. `takes=` is what every site hands its handler; `when=` makes
    it unavailable, and `off=` (required with `when=` or `row=`) says how its
    nodes look then."""
    where = _caller()
    if not isinstance(label, str) or not label:
        raise UIError("A08", "action %r has no label" % (name,), where=where,
                      expected="the words every node naming it shows",
                      fix='a handler nobody sees is a hook: ui.hook(%r, handler)' % (name,))
    if row is not None:
        if takes != "row":
            raise UIError("A02", "action %r row=%r hands its handler the row" % (name, row), where=where,
                          found="takes=%r" % takes, fix='takes="row"')
    if chars:
        if takes != "char":
            raise UIError("A02", "action %r chars=%r hands each typed character" % (name, chars),
                          where=where, found="takes=%r" % takes, fix='takes="char"')
    if (when is not None or row is not None) and off is None:
        raise UIError("O01", "action %r can be unavailable (%s) and does not say how it looks then" % (
            name, "when=%r" % when if when is not None else "row=%r" % row), where=where,
            expected='off="hide" (its nodes go) or off="grey" (they stay, dimmed, not a stop)',
            fix='ui.action(%r, %r, ..., %s, off="hide")' % (
                name, label, "when=%r" % when if when is not None else "row=%r" % row))
    if off is not None and when is None and row is None:
        raise UIError("O02", "action %r off=%r: it is never unavailable" % (name, off), where=where,
                      fix="drop off=, or give the condition: when=\"COND\"")
    if off is not None and off not in OFFS:
        raise UIError("A09", "action %r: off=%r" % (name, off), where=where,
                      expected="hide (its nodes go) or grey (they stay, dimmed)", fix='off="hide"')
    if isinstance(keys, str):
        raise UIError("K03", "action %r: keys=%r is one text, not a list" % (name, keys), where=where,
                      fix="keys=(%r,)" % keys)
    for k in keys:
        if not isinstance(k, str) or not (_chord_byte(k) or _letter(k) or k == SHIFT_ENTER
                                          or k.startswith("role:") and k[5:] in ROLES):
            raise UIError("K03", "action %r: keys=%r" % (name, k), where=where,
                          expected='a letter, "ctrl+<letter>", "shift+enter" or "role:<name>", a role one of '
                          + ", ".join(ROLES),
                          fix='keys=("m", "ctrl+f", "role:search")')
    if icon is not None and (not isinstance(icon, str) or "{" in icon):
        raise UIError("I02", "action %r: icon=%r" % (name, icon), where=where,
                      expected="an icon's name; a site's icon= may be a template", fix='icon="play"')
    d = _declare(name, label, fn, takes, where, "action")
    for other in d.actions.values():
        both = set(keys) & set(getattr(other, "keys", ()))
        if both and (other.unit == unit or other.unit is None or unit is None):
            raise UIError("K02", "action %r and action %r both answer %s" % (name, other.name, both.pop()),
                          where=where, fix="one key, one action" if other.unit == unit else
                          "an app's key is live beside every unit: give the key to one of them")
    if row is not None:
        _state_name(row, (RowOf,), "ui.action(%r) row=" % name, where)
    a = Action(name, label, fn, when, hint, row, where)
    if "{" in a.hint:
        a.hint_g = tmpl(a.hint, Ctx(), where, "hint").g
    a.takes = takes
    a.unit = unit
    a.push = push
    a.chars = chars or ""
    a.keys = tuple(keys)
    a.letters = "".join(k.lower() for k in keys if _letter(k))
    a.icon = icon or ""
    a.off = off or "hide"
    if keys:
        _arity(a, "row" if row else "none", "its keys %r" % (keys,), where)
    if chars:
        _arity(a, "char", "its chars %r" % chars, where)
    d.actions[name] = a
    return name


def hook(name, fn, *, takes="none", unit=None):
    """A handler no person sees: named by on=, changed=, a timer, a post,
    and the engine's `opened`. It has no label, keys or condition."""
    where = _caller()
    d = _declare(name, "", fn, takes, where, "hook")
    a = Action(name, "", fn, None, "", None, where)
    a.takes, a.unit, a.push, a.chars, a.keys, a.letters, a.icon, a.off = takes, unit, None, "", (), "", "", "hide"
    a.hook = True
    d.actions[name] = a
    return name


def _arity(a, hands, site, where):
    """A site hands `hands`; the action declared what it takes (A02)."""
    if a.takes != hands:
        raise UIError("A02", "%s %r is reached from %s, which hands %s" % (
            "hook" if a.hook else "action", a.name, site, _HANDED[hands]), where=where,
            expected="takes=%r (%s), as declared at %s:%s" % (a.takes, _HANDED[a.takes], a.where[0], a.where[1]),
            fix='declare it takes="%s", or a second %s for this site' % (
                hands, "hook" if a.hook else "action"))


def _action(name, hands, site, where, seen=True):
    """The action a site names. `seen`: a node a person sees (a button, a
    link, an item, a card, a dialog's button), where a hook is H01."""
    if callable(name) and not isinstance(name, str):
        raise UIError("A03", "%s: a function where an action's name belongs" % site, where=where,
                      found=getattr(name, "__name__", repr(name)),
                      fix='ui.action("name", "Label", %s) once, then name it: "name"'
                      % getattr(name, "__name__", "fn"))
    if name is None:
        raise UIError("A03", "%s: None where an action's name belongs" % site, where=where,
                      fix="a handler called instead of named? name its action: \"add\", not add()")
    acts = _d().actions
    a = acts.get(name) if isinstance(name, str) else None
    if a is None:
        n = _near(str(name), acts)
        raise UIError("A01", "%s names no action %r" % (site, name), where=where,
                      expected="one of " + (", ".join(acts) or "(none declared)"),
                      fix="did you mean %s?" % n if n else
                      'declare it: ui.action(%r, "Label", handler)' % name)
    if seen and a.hook:
        raise UIError("H01", "%s names hook %r where an action belongs" % (site, name), where=where,
                      expected="an action: a person sees its label", fix='ui.action(%r, "Label", %s)' % (
                          name, getattr(a.fn, "__name__", "handler")))
    if a.row is not None:
        rowt = _NOW.app.ctx.rowt if _NOW.app is not None and _NOW.building else None
        model = _st()._t[_st()._t[a.row].model]
        if rowt is not None and rowt is not model:
            raise UIError("A07", "action %r acts on a row of %s; %s is in a list of another model"
                          % (a.name, _st()._t[a.row].model, site), where=where,
                          fix="a second action with row= over this list's model")
        hands = "row"
    _arity(a, hands, site, where)
    return a


def after(ms, name):
    a = _command("after")
    act = _action(name, "none", "ui.after", _caller(), seen=False)
    a.timers.append([a.clock + ms, act])


def cancel(name):
    """A handler cancels the timers armed for the action NAME: they run nothing."""
    a = _command("cancel")
    act = _action(name, "none", "ui.cancel", _caller(), seen=False)
    a.timers[:] = [t for t in a.timers if t[1] is not act]


def focus(key=None):
    """A handler moves focus: ui.focus(key). Nothing reads it back."""
    if key is None:
        raise UIError("N04", "ui.focus() reads nothing back",
                      fix="@focus in a template, or a list's cur= state")
    _command("focus").want_focus = key


# -- the tree -----------------------------------------------------------------

class Node:
    __slots__ = ("kind", "key", "p", "l", "src", "kids", "parent", "act", "where", "ctx", "aux", "unit")

    def __init__(self, kind, key, where):
        self.kind, self.key, self.where = kind, key, where
        self.p, self.l, self.src, self.kids = {}, {}, {}, []
        self.parent = self.act = self.ctx = self.aux = self.unit = None

    def __enter__(self):
        b = _NOW.app
        b.stack.append(self)
        if self.kind in ("rows", "cards", "table"):
            b.ctx = self.ctx
        return self

    def __exit__(self, *a):
        b = _NOW.app
        b.stack.pop()
        if self.kind in ("rows", "cards", "table"):
            b.ctx = Ctx()


# the props each kind takes: text, int, cond, choice, lit, fn, action0/1/p (none, the row, a pointer event)
_C = {"shown": "cond"}
SCHEMA = {
    "page": {"title": "text"},
    "group": dict(_C, caption="text", close="action0"),
    "label": dict(_C, text="text", tone="lit", wrap="lit", face="lit", size="lit", icon="text",
                  lines="lit", align="lit"),
    "rule": dict(_C),
    "note": dict(_C, icon="text", lines="texts"),
    "info": dict(_C, label="text", value="text", icon="text", signal="int"),
    "link": dict(_C, value="text", label="text", icon="text", signal="int", focus="cond"),
    "button": dict(_C, kind="lit", label="text", icon="text", alone="lit", span="lit", arg="lit",
                   focus="cond", grow="lit", marked="cond", w="int"),
    "toggle": dict(_C, label="text", on="action0", focus="cond", hint="text"),
    "slider": dict(_C, label="text", lo="int", hi="int", on="action0", focus="cond", look="lit",
                   icon="text"),
    "field": dict(_C, label="text", max="lit", on="action0", secret="lit", focus="cond",
                  changed="action0", hint="text", commit="lit"),
    "seg": dict(_C, label="text", on="action0", focus="cond", look="lit", labels="lit", text="text"),
    "rows": dict(_C, cur="lit", current="lit", empty="text", filter="lit", changed="action1", group="lit"),
    "table": dict(_C, cur="lit", cols="lit", changed="action1", on="action1", empty="text", hint="text", focus="cond"),
    "col": dict(_C, text="text"),
    "cards": dict(_C, cur="lit", current="lit", w="int", h="int", cols="int", new="action0",
                  changed="action1", group="lit"),
    "card": dict(_C, label="text", sub="text", badge="text", image="image", icon="text", tone="text"),
    # G9, G13: a picture of its own (an image state, its fallback icon), a
    # progress bar (max= is required), a page that says why there is nothing
    "picture": dict(_C, image="image", icon="text", side="int", tone="text"),
    "progress": dict(_C, label="text", value="int", max="int", look="lit"),
    "empty": dict(_C, icon="text", title="text", lines="texts"),
    "text_view": dict(_C, wrap="lit", follow="lit", syntax="lit", face="lit"),
    "dialog": dict(_C, title="text", buttons="lit"),
    "popover": dict(_C, anchor="text", w="int", close="action0"),
    "menu": dict(_C, caption="text"),
    "item": dict(_C, label="text", icon="text", mkind="lit"),
    "split": dict(_C, dir="choice", sizes="ints", pad="lit", gap="lit", align="lit"),
    "grid": dict(_C, cols="lit", rows="lit", gap="lit", pad="lit"),
    "panel": dict(_C),
    "canvas": dict(_C, draw="fn", h="lit", w="int", grow="lit", pointer="actionp", focus="cond", ground="lit"),
    "foot": dict(_C, look="lit"),
    "scroll": dict(_C),
    "switch": dict(_C),
    "case": {},
    # EXT-4. The Editor's kinds (see the calls below)
    "tabs": dict(_C, labels="lit", icons="lit", focus="cond", on="action0", offered="conds"),
    "panes": dict(_C, dir="choice", gap="lit", size="lit", start="lit", min="lit", grow="lit",
                  focused="lit", grown="lit"),
    "pane": dict(_C, title="text", icon="text", size="lit", start="lit", min="lit", grow="lit",
                 tabs="lit", labels="lit", offered="conds", head="lit"),
    "text_editor": dict(_C, find="lit", find_on="cond", marks="lit", gutter="cond", focus="cond"),
    "slot": dict(_C, min="lit"),
    # §7.7's bridge: a surface still on today's verbs hosted in one node
    "legacy": dict(_C, draw="fn", pointer="fn", keys="fn", hints="fn", gen="fn"),
    "row": dict(_C, close="action0", gap="int", pad="int", mid="lit", grow="lit"),
    "tools": dict(_C, lend="lit"),
    "lent": dict(_C),
    "status": dict(_C, text="text"),
}
TWO_WAY = ("toggle", "slider", "field", "tabs", "seg")
# what a function prop takes, for its N01's fix
_FN_FIX = {None: "%s=draw, where def draw(cv, x, y, w, h)",
           ("legacy", "draw"): "%s=draw, where def draw(cv, th, x, y, w, h, focused, dt)",
           ("legacy", "pointer"): "%s=pointer, where def pointer(x, y, click) returns whether it took it",
           ("legacy", "keys"): "%s=keys, where def keys(i) returns whether it took the input",
           ("legacy", "hints"): "%s=hints, where def hints() returns the band's hints",
           ("legacy", "gen"): "%s=gen, where def gen() returns an int that moves with its pixels"}
CHOICES = {"dir": ("row", "col"), "kind": ("normal", "primary", "danger", "tonal", "bar"),
           "wrap": ("cut", "word", "char"), "align": ("start", "center", "end"),
           "face": ("ui", "title", "mono", "caption", "result"), "follow": (True, False),
           "syntax": ("plain", "sgr", "python", "lua", "json", "markdown")}
# §7.9: a node's retired props, each with its replacement (N04)
RETIRED_PROPS = {"disabled": 'when= on the action, with off="grey"', "keyname": "the action's keys=",
                 "ink": "tone=", "does": "hint= on the action", "fixed": 'weights in sizes ("1*")',
                 "paint": "an image field: image=.cover", "below": "scroll new=", "lit": "marked="}


def _b():
    a = _NOW.app
    if a is None or not _NOW.building:
        raise UIError("B03", "a ui node outside a page function",
                      fix="make nodes in a page function, under `with ui.page(title):`; a handler writes state")
    return a


def _node(kind, key, props, where, act=None):
    b = _b()
    if kind != "page" and not b.stack:
        raise UIError("B03", "ui.%s outside a page" % kind, where=where,
                      fix="put it inside `with ui.page(title):`")
    schema = SCHEMA[kind]
    n = Node(kind, key, where)
    n.act = act
    n.ctx = b.ctx
    direct = b.direct
    for k, v in props.items():
        pt = schema.get(k)
        if v is None and (pt is None or not pt.startswith("action")):
            continue                        # an optional prop given None is not given
        if pt is None and k in RETIRED_PROPS:
            raise UIError("N04", "ui.%s %s= is retired" % (kind, k), where=where, fix=RETIRED_PROPS[k])
        if pt is None:
            near = _near(k, schema)
            raise UIError("N01", "ui.%s has no prop %r" % (kind, k), where=where,
                          expected=", ".join(schema) or "(none)",
                          fix="did you mean %s?" % near if near else ('drop it; %s takes %s' % (kind, ', '.join(schema) or 'none')))
        n.src[k] = v
        if pt == "lit":
            if k in CHOICES and v not in CHOICES[k]:
                raise UIError("N01", "ui.%s %s=%r" % (kind, k, v), where=where,
                              expected=", ".join(CHOICES[k]), fix='%s=%r' % (k, CHOICES[k][0]))
            n.l[k] = v
        elif pt == "fn":
            if not callable(v):
                raise UIError("N01", "ui.%s %s= takes a draw function (the escape hatch)" % (kind, k),
                              where=where, found=repr(v), fix=_FN_FIX.get((kind, k), _FN_FIX[None]) % k)
            n.l[k] = v
        elif direct and pt in _DIRECT:
            _direct_src(kind, k, v, pt, where)    # the engine compiles the source at lowering
        elif pt == "cond":
            n.p[k] = cond(v, b.ctx, where)
        elif pt == "conds":
            # a condition a value (a tabs' offered=): the value shows where it holds
            if not isinstance(v, dict):
                raise UIError("N01", "ui.%s %s= is a dict of a condition a value" % (kind, k),
                              where=where, found=repr(v), fix='%s={"blocks": "has_blocks"}' % k)
            n.l[k] = {x: cond(c, b.ctx, where) for x, c in v.items()}
        elif pt in ("action0", "action1", "actionp"):
            want = "row" if pt == "action1" else "pointer" if pt == "actionp" else "none"
            n.l[k] = _action(v, want, "ui.%s %s=" % (kind, k), where, seen=False)
        elif pt == "texts":
            if isinstance(v, str) or not isinstance(v, (list, tuple)):
                raise UIError("N01", "ui.%s %s= is a list of texts" % (kind, k), where=where,
                              found=repr(v), fix="%s=[%r]" % (k, v))
            n.p[k] = _texts([_prop("text", k, x, b.ctx, where, kind) for x in v])
        else:
            n.p[k] = _prop(pt, k, v, b.ctx, where, kind)
    if kind == "page":
        b.root = n
        return n
    parent = b.stack[-1]
    if n.key is None:
        n.key = "%s#%d" % (kind, len(parent.kids))
    for k in parent.kids:
        if k.key == n.key:
            raise UIError("N02", "two nodes keyed %r under %r" % (n.key, parent.key), where=where,
                          found="the first at line %s" % k.where[1],
                          fix="a node's identity is its action or state name: one per parent; "
                              "give a button arg= to tell two apart")
    n.parent = parent
    parent.kids.append(n)
    return n


# what a direct build (`Builder.direct`) leaves to the engine's compiler
_DIRECT = ("cond", "text", "int", "choice", "ints", "image", "texts")


def _direct_src(kind, k, v, pt, where):
    """A direct build's check of a source it does not compile: its Python
    type (C01, T01), what only Python can see."""
    if pt == "cond":
        if not isinstance(v, (str, bool)) or isinstance(v, str) and not v.strip():
            raise UIError("C01", "a condition is a text", found="%r (%s)" % (v, _pyt(v)),
                          fix='shown="on", "!busy", "mode=edit", "a && !b"', where=where)
    elif pt == "texts":
        if isinstance(v, str) or not isinstance(v, (list, tuple)):
            raise UIError("N01", "ui.%s %s= is a list of texts" % (kind, k), where=where,
                          found=repr(v), fix="%s=[%r]" % (k, v))
        for x in v:
            _direct_src(kind, k, x, "text", where)
    elif pt == "int" and type(v) is int or pt == "ints" and isinstance(v, tuple):
        pass
    elif not isinstance(v, str):
        raise UIError("T01", "%s=: a template is a text" % k, found="%r (%s)" % (v, _pyt(v)),
                      fix='"{name}" to show a state name' if not callable(v) else
                      "name an action, never a function", where=where)


def _texts(gs):
    def texts(row):             # one list a call, no generator
        out = []
        for g in gs:
            x = g(row)
            if x:
                out.append(x)
        return out
    return texts


def _prop(pt, k, v, ctx, where, kind):
    if pt == "image":
        if not isinstance(v, str) or not _fullmatch(r"[.@]?[A-Za-z_][\w.]*", v):
            raise UIError("T03", "ui.%s %s=%r: an image names an image state or a row's image field" % (
                kind, k, v), where=where, fix="%s=.cover (a row's field) or %s=NAME (an image state)" % (k, k))
        t, g = _ref(v, ctx, where)
        if t is not IMAGE:
            raise UIError("T03", "ui.%s %s=%s is %s, not an image" % (kind, k, v, t), where=where,
                          fix="declare the field as `image NAME` and write ui.image.store(...) or "
                              "ui.image.own(...) to it")
        return g
    if pt == "int":
        if type(v) is int:
            return lambda row: v
        t = tmpl(v, ctx, where)
        if t.t is not INT:
            raise UIError("T03", "ui.%s %s=%r is %s" % (kind, k, v, t.t), where=where,
                          expected="an int, \"{name}\" of an int, or \"{cond?a:b}\" of two ints", fix='%s=24, or a template of an int' % k)
        return t.g
    if pt == "ints":
        if isinstance(v, tuple) and all(type(x) is int or _isweight(x) for x in v):
            return lambda row: v        # ints; with a weight ("2*"), the ints are desk units
        t = tmpl(v, ctx, where)
        for s in _branches(v):
            if not all(_isint(x) for x in s.split()):
                raise UIError("T03", "ui.%s %s=%r: %r is not ints" % (kind, k, v, s), where=where,
                              fix='a tuple (1, 3), or "{@wide?112 184:1 3}"')
        return lambda row: tuple(int(x) for x in str(t.g(row)).split())
    if pt == "choice":
        tmpl(v, ctx, where)
        for s in _branches(v):
            if s not in CHOICES[k]:
                raise UIError("T03", "ui.%s %s=%r: %r" % (kind, k, v, s), where=where,
                              expected=", ".join(CHOICES[k]), fix='%s=%r, or {cond?a:b} of two of them' % (k, CHOICES[k][0]))
        t = tmpl(v, ctx, where)
        return t.g
    t = tmpl(v, ctx, where, k)
    if t.t is BOOL or isinstance(t.t, (Model, Record)):
        raise UIError("T03", "ui.%s %s=%r is %s, not a text" % (kind, k, v, t.t), where=where,
                      fix="{%s?yes:no}" % v.strip("{}") if t.t is BOOL else ('a text: {%s|map NAME}' % v.strip('{}')))
    g = t.g
    return lambda row: _text(g(row))


def _branches(v):
    """The literal values a choice template can take."""
    if "{" not in v:
        return [v]
    body = v.strip()[1:-1]
    q = body.find("?")
    if not (v.strip().startswith("{") and v.strip().endswith("}")) or q < 0:
        raise UIError("T03", "%r: a choice is a literal or {cond?a:b}" % v, fix='"row", or "{wide?row:col}"')
    a, _, b = body[q + 1:].partition(":")
    return [a, b]


def _state_name(name, want, site, where):
    st = _st()
    if not isinstance(name, str) or name not in st._t:
        if not isinstance(name, str):
            n = [k for k, x in st._v.items() if x == name and st._t[k] in want] if name is not None else []
            raise UIError("N01", "%s takes a state name, not its value" % site, where=where,
                          found="%r (%s)" % (name, _pyt(name)),
                          fix=("\"%s\"" % n[0]) if len(n) == 1 else 'the name in quotes: "wifi"')
        raise _undeclared(name, st._t)
    t = st._t[name]
    if not any(t is w or isinstance(w, type) and isinstance(t, w) for w in want):
        spell = {"Opt": "ui.opt(0)", "Doc": "ui.doc(text, lang)", "Model": "ui.model(...)",
                 "Enum": "ui.enum(...)", "RowOf": "ui.row_of(model)", "Record": "ui.record(...)"}
        exp = " or ".join(spell.get(getattr(w, "__name__", ""), repr(w)) for w in want)
        raise UIError("T03", "%s names %s, which is %s" % (site, name, t), where=where,
                      expected=exp, fix="declare %s=%s, or name another" % (name, exp.split(" or ")[0]))
    return name


# -- the calls an app makes ---------------------------------------------------

def page(title, **p):
    b = _b()
    if b.stack:
        raise UIError("N03", "ui.page inside a page", fix="a page is the root; ui.push opens another")
    return _node("page", "page", dict(p, title=title), _caller())


def group(caption="", **p):
    return _node("group", None, dict(p, caption=caption), _caller())


def label(text, **p):
    """A row of text (§7.2): `face=` a type role, `size="fit"` shrinks it to
    its width, `wrap="word"` breaks it at word breaks (at most `lines=`
    lines, then an ellipsis), `align=`, `tone=`, `icon=` before it."""
    where = _caller()
    if p.get("size", "fit") != "fit":
        raise UIError("N01", "ui.label size=%r" % (p["size"],), where=where, expected="fit",
                      fix='size="fit", or drop it')
    if "lines" in p and (type(p["lines"]) is not int or p["lines"] < 1):
        raise UIError("N01", "ui.label lines=%r" % (p["lines"],), where=where,
                      expected="an int, at least 1", fix="lines=2")
    return _node("label", None, dict(p, text=text), where)


def rule(**p):
    """A divider across the page (§7.2)."""
    return _node("rule", None, p, _caller())


def note(lines, icon="", **p):
    """A note on the page, no stop: `icon` and `lines`' paragraphs (§7.2)."""
    return _node("note", None, dict(p, lines=lines, icon=icon), _caller())


def text_view(name, **p):
    """A read-only lines view (§7.2) of a text state (its lines) or a model
    of lines (each row's first text field that is not its key): `wrap=`
    "cut" or "word", `follow=True` keeps a view at its foot there as lines
    arrive, `syntax=` a terminal's colours ("sgr") or a source's; `face="ui"`
    sets each line as a paragraph of prose in the ui face, half a line
    between paragraphs (no syntax). A view whose text runs past it is a
    stop: the arrows scroll it a line, PgUp and PgDn a page (L7)."""
    where = _caller()
    if p.get("face", "mono") not in ("mono", "ui"):
        raise UIError("N01", "ui.text_view face=%r" % (p["face"],), where=where,
                      expected="mono, ui", fix='face="ui"')
    if p.get("face") == "ui" and "syntax" in p:
        raise UIError("N01", "ui.text_view face=\"ui\" with syntax=: prose takes no syntax",
                      where=where, fix="drop syntax=, or face=")
    _state_name(name, (TEXT, Model), "ui.text_view", where)
    t = _st()._t[name]
    n = _node("text_view", name, p, where)
    n.l["name"] = name
    if isinstance(t, Model):
        f = [k for k, v in t.fields.items() if k != t.key and v is TEXT]
        if not f:
            raise UIError("T03", "ui.text_view %s: its model has no text field" % name, where=where,
                          fix='ui.model(key="n", n=0, line="")')
        n.l["field"] = f[0]
    return n


def info(label, value="", **p):
    return _node("info", None, dict(p, label=label, value=value), _caller())


class _Site:
    """A stop's site as an error names it, formatted only when one does."""

    def __init__(self, kind, name, in_row):
        self.kind, self.name, self.in_row = kind, name, in_row

    def __str__(self):
        return "ui.%s(%s)%s" % (self.kind, getattr(self.name, "__name__", repr(self.name)),
                                " in a row" if self.in_row else "")


def _stop(kind, name, p, where, n=None):
    in_row = _NOW.app.ctx.rowt is not None
    act = _action(name, ("row" if in_row else "none") if n is None else n, _Site(kind, name, in_row), where)
    key = name if "arg" not in p else "%s/%s" % (name, p["arg"])
    return _node(kind, key, p, where, act)


def link(action, value="", **p):
    return _stop("link", action, dict(p, value=value), _caller())


def button(action, kind="normal", **p):
    where = _caller()
    return _stop("button", action, dict(p, kind=kind), where, "arg" if "arg" in p else None)


def item(action, **p):
    return _stop("item", action, p, _caller())


def card(action, **p):
    return _stop("card", action, p, _caller())


def _row_field(kind, name, want, where):
    """A two-way control bound to `.field` of its row: only inside a list,
    a field of the right type. The engine writes that field of that row."""
    rowt = _b().ctx.rowt
    f = name[1:]
    if rowt is None:
        raise UIError("T05", "%s %s: a row's field outside a list" % (kind, name), where=where,
                      fix="put it inside rows, or bind a state name")
    if f not in rowt.fields:
        raise UIError("T02", "%s %s: rows have no field %r" % (kind, name, f), where=where,
                      expected=", ".join(rowt.fields), fix='.FIELD, one of ' + ', '.join(rowt.fields))
    if rowt.fields[f] is not want:
        raise UIError("T03", "%s %s is %s; a %s binds %s" % (kind, name, rowt.fields[f], kind, want), where=where, fix='bind a %s field of the row' % want)
    if f == rowt.key:
        raise UIError("T03", "%s %s: the key of a row is never written" % (kind, name), where=where, fix='bind a field that is not the key %r' % rowt.key)
    return f


def toggle(label, name, **p):
    where = _caller()
    if isinstance(name, str) and name.startswith("."):
        f = _row_field("toggle", name, BOOL, where)
        on = p.pop("on", None)
        n = _node("toggle", f, dict(p, label=label), where)
        n.l["field"] = f
        if on is not None:          # it hands its row (G10)
            n.l["on"] = _action(on, "row", "ui.toggle %s on=" % name, where, seen=False)
        return n
    return _node("toggle", _state_name(name, (BOOL,), "ui.toggle", where), dict(p, label=label), where)


def _look(kind, look, looks, where):
    if look not in looks:
        raise UIError("N01", "ui.%s look=%r" % (kind, look), where=where,
                      expected=", ".join(repr(x) for x in looks), fix="look=%r" % looks[0])


def slider(label, name, lo, hi, look="bar", **p):
    """A value from lo to hi, left and right its own. `look="steps"` draws
    a segment a step and no number; `icon=` (a template; empty draws none)
    stands before its label."""
    where = _caller()
    _look("slider", look, ("bar", "steps"), where)
    p = dict(p, look=look) if look != "bar" else p
    if isinstance(name, str) and name.startswith("."):
        f = _row_field("slider", name, INT, where)
        on = p.pop("on", None)
        n = _node("slider", f, dict(p, label=label, lo=lo, hi=hi), where)
        n.l["field"] = f
        if on is not None:          # it hands its row (G10)
            n.l["on"] = _action(on, "row", "ui.slider %s on=" % name, where, seen=False)
        return n
    return _node("slider", _state_name(name, (INT,), "ui.slider", where),
                 dict(p, label=label, lo=lo, hi=hi), where)


def field(label, name, max=32, commit=False, **p):
    """A line of text bound to a text state. `hint=` is what it shows empty.
    `commit=True` holds the edit as a draft: the state is written when Enter
    is pressed in it or focus leaves it (`changed=` runs then), and its hints
    are Done and Cancel; without it every edit writes the state."""
    where = _caller()
    if commit not in (True, False):
        raise UIError("N01", "ui.field(%r) commit=%r" % (name, commit), where=where,
                      expected="True or False", fix="commit=True")
    n = _node("field", _state_name(name, (TEXT,), "ui.field", where),
              dict(p, label=label, max=max, commit=commit or None), where)
    return n


def seg(label, name, labels=None, text=None, look="seg", **p):
    """One of several values, left and right its own while focused: over an
    enum, each value's word from label table `labels=` (every value named);
    over a `row_of`, its model's rows in order, each word the row template
    `text=`. `look="cycle"` shows the one value between chevrons and
    activation steps it; `look="seg"` shows every value."""
    where = _caller()
    _look("seg", look, ("seg", "cycle"), where)
    _state_name(name, (Enum, RowOf), "ui.seg", where)
    t = _st()._t[name]
    if isinstance(t, Enum):
        if text is not None:
            raise UIError("N01", "ui.seg(%r): text= words a row_of's rows; an enum's words are a label table"
                          % name, where=where, fix="labels=\"TABLE\", declared with ui.labels")
        tab = _d().labels.get(labels) if isinstance(labels, str) else None
        if tab is None:
            raise UIError("T02", "ui.seg(%r): no label table %r" % (name, labels), where=where,
                          fix='labels="NAME" after ui.labels("NAME", {value: "Word", ...})')
        miss = [v for v in t.values if v not in tab]
        if miss:
            raise UIError("T04", "ui.seg(%r): table %s has no word for %s" % (name, labels, ", ".join(miss)),
                          where=where, expected="a word for each of " + ", ".join(t.values),
                          fix="add them to ui.labels(%r, ...)" % labels)
        n = _node("seg", name, dict(p, label=label, look=look), where)
        n.l["values"], n.l["words"] = t.values, tuple(tab[v] for v in t.values)
        n.l["labels"] = labels          # the table's name: what an engine that reads tables names
        return n
    if labels is not None or text is None:
        raise UIError("N01", "ui.seg(%r) over a row_of words each row with text=" % name, where=where,
                      fix='text="{.FIELD}", a template over a row of %s' % t.model)
    n = _node("seg", name, dict(p, label=label, look=look), where)
    n.l["model"] = t.model
    if _NOW.app.direct:
        _direct_src("seg", "text", text, "text", where)
    else:
        n.l["text"] = tmpl(text, Ctx(_st()._t[t.model]), where, "text").g
    n.src["text"] = text
    return n


def _list(kind, model_name, p, where):
    b = _b()
    _state_name(model_name, (Model,), "ui.%s" % kind, where)
    mt = _st()._t[model_name]
    if p.get("cur") is not None:
        c = _state_name(p["cur"], (RowOf,), "ui.%s cur=" % kind, where)
        if _st()._t[c].model != model_name:
            raise UIError("T03", "cur=%r is a row of %s, not of %s" % (c, _st()._t[c].model, model_name),
                          where=where, fix='cur= names a row_of(%r)' % model_name)
    if p.get("current") is not None:
        _state_name(p["current"], (TEXT, RowOf, INT), "ui.%s current=" % kind, where)
    if p.get("filter") is not None:
        _state_name(p["filter"], (TEXT,), "ui.%s filter=" % kind, where)
        if mt.fields[mt.key] is not TEXT:
            raise UIError("T03", "ui.%s filter=%r: the rows' key %s is not a text" % (kind, p["filter"], mt.key),
                          where=where, fix="filter matches the model's key field, a text")
    group = p.pop("group", None)
    n = _node(kind, model_name, p, where)
    n.ctx = Ctx(mt, p.get("current"))
    n.l["model"] = model_name
    if group is not None:           # a caption drawn where its value changes from the row before
        if b.direct:
            _direct_src(kind, "group", group, "text", where)
        else:
            n.l["group"] = tmpl(group, n.ctx, where, "group").g
        n.src["group"] = group
    b.ctx = n.ctx
    return n


def table(model_name, **p):
    """Rows as a table: a header of cols=, one `col` child per column."""
    return _list("table", model_name, p, _caller())


def col(text, **p):
    return _node("col", None, dict(p, text=text), _caller())


def rows(model_name, **p):
    return _list("rows", model_name, p, _caller())


def cards(model_name, **p):
    if "paint" in p:
        raise UIError("N04", "cards paint= is retired (G9)", where=_caller(),
                      fix="give the rows an image field (image pic) and the card image=.pic")
    return _list("cards", model_name, p, _caller())


def picture(image=None, **p):
    """One picture at its own place: image state `image`'s, its `icon` on a
    tinted tile while it has none; with no image, the icon's tile alone
    (`tone="locked"` veils it, a lock on its corner)."""
    if image is not None:
        p["image"] = image
    return _node("picture", None, p, _caller())


def progress(value, **p):
    where = _caller()
    if "max" not in p:
        raise UIError("N01", "progress %s has no max=" % value, where=where,
                      fix="max=\"{NAME}\" (an int), or max=0 for work whose size is not known")
    if p.get("look", "count") != "count":
        raise UIError("N01", "progress look=%r" % (p["look"],), where=where, expected="count",
                      fix='look="count" ("3 of 5", or the label saying it: "{done} of {total} found"), or drop it (a bar)')
    return _node("progress", None, dict(p, value="{%s}" % value if not str(value).startswith("{") else value),
                 where)


def empty(title, **p):
    return _node("empty", None, dict(p, title=title), _caller())


def dialog(title, buttons, **p):
    where = _caller()
    acts = [_action(x, "none", "ui.dialog button", where) for x in buttons]
    if len(acts) < 1:
        raise UIError("N01", "a dialog has at least one button", where=where, fix='ui.dialog(title, ["cancel", "ok"]): Back takes the first')
    n = _node("dialog", None, dict(p, title=title, buttons=buttons), where)
    n.l["buttons"] = acts
    return n


def popover(key, **p):
    return _node("popover", key, p, _caller())


def menu(caption="", **p):
    return _node("menu", None, dict(p, caption=caption), _caller())


def _isweight(x):
    """A split's weight, "N*" ("*" alone is 1*)."""
    return type(x) is str and x.endswith("*") and (x[:-1] == "" or x[:-1].isdigit())


def split(dir, sizes, **p):
    return _node("split", None, dict(p, dir=dir, sizes=sizes), _caller())


def grid(cols, rows, **p):
    return _node("grid", None, dict(p, cols=cols, rows=rows), _caller())


def panel(key, **p):
    return _node("panel", key, p, _caller())


# the theme's roles a canvas's ground= may name: what the engine paints its
# rect with before its draw
GROUNDS = ("surface", "surface_alt", "surface_light", "panel", "lcd", "desktop")


def canvas(key, draw, **p):
    where = _caller()
    g = p.get("ground")
    if g is not None and g not in GROUNDS:
        raise UIError("N01", "canvas %r: ground=%r" % (key, g), where=where,
                      expected="one of the theme's grounds: " + ", ".join(GROUNDS),
                      fix='ground="surface"')
    return _node("canvas", key, dict(p, draw=draw), where)


def foot(look="end", **p):
    """The page's docked foot: its buttons at their natural widths at its
    end, a label before them; `look="share"` (F-13): its buttons share its
    width, a label a line of its own over them."""
    where = _caller()
    _look("foot", look, ("end", "share"), where)
    if look != "end":
        p["look"] = look
    return _node("foot", "foot", p, where)


def scroll(key, **p):
    return _node("scroll", key, p, _caller())


def switch(name, cases, **p):
    """One of several bodies by an enum's value; every value or "_"."""
    where = _caller()
    _state_name(name, (Enum, BOOL), "ui.switch", where)
    t = _st()._t[name]
    vals = t.values if isinstance(t, Enum) else (True, False)
    miss = [v for v in vals if v not in cases]
    extra = [v for v in cases if v not in vals and v != "_"]
    if (miss and "_" not in cases) or extra:
        raise UIError("T04", "ui.switch(%r) over %s" % (name, t), where=where,
                      expected="a case for each of %s, or \"_\"" % ", ".join(map(str, vals)),
                      found="missing %s" % ", ".join(map(str, miss)) if miss else
                      "unknown %s" % ", ".join(map(str, extra)), fix='add the missing cases, or "_" for the rest')
    n = _node("switch", "switch:" + name, p, where)     # beside the tabs that drive it
    n.l["name"] = name
    b = _NOW.app
    b.stack.append(n)
    try:
        for v, body in cases.items():
            unit = None
            if isinstance(body, tuple):
                body, unit = body
            c = _node("case", "case:%s" % v, {}, where)
            c.l["v"] = v
            c.unit = unit
            b.stack.append(c)
            try:
                body()
            finally:
                b.stack.pop()
    finally:
        b.stack.pop()
    return n


# -- EXT-4. the Editor's vocabulary --------------------------------------------

ANCHORS = {"text_editor": ("caret", "word"), "tabs": None, "pane": ("head",), "rows": ("cur",), "cards": ("cur",)}


def tabs(name, labels, **p):
    """A tab strip bound two-way to an enum: one tab per value, labelled by a
    label table that must cover every value. The body is a ui.switch on the
    same name, whose cases are built once and kept."""
    where = _caller()
    _state_name(name, (Enum,), "ui.tabs", where)
    t = _st()._t[name]
    tab = _d().labels.get(labels)
    if tab is None:
        raise UIError("T02", "ui.tabs(%r): no label table %r" % (name, labels), where=where,
                      fix='ui.labels(%r, {value: "Label", ...})' % labels)
    extra = [v for v in p.get("offered") or () if v not in t.values]
    if extra:
        raise UIError("N01", "ui.tabs(%r) offered= names %s, not a value" % (name, ", ".join(extra)),
                      where=where, expected=", ".join(t.values), fix="a condition for a value of %r" % name)
    miss = [v for v in t.values if v not in tab]
    if miss:
        raise UIError("T04", "ui.tabs(%r): table %s has no label for %s" % (name, labels, ", ".join(miss)),
                      where=where, expected="a label for each of " + ", ".join(t.values), fix='add the labels to ui.labels(%r, ...)' % labels)
    n = _node("tabs", name, dict(p, labels=labels), where)
    n.l["values"] = t.values
    return n


def _sized(kind, key, p, where):
    """A child of ui.panes holds its size in state or grows (EXT-4). Its
    start= is desk units, or names an int state its host writes (a start
    that depends on the area or the cart: a build reads no state)."""
    st = p.get("start")
    if isinstance(st, str):
        nm = _state_name(st, (INT, Opt), "ui.%s start=" % kind, where)
        t = _st()._t[nm]
        if t is not INT and t.base is not INT:
            raise UIError("T03", "ui.%s start=%r is %s" % (kind, nm, t), where=where,
                          fix="%s=340 in the state, which the host writes from the area" % nm)
    elif st is not None and type(st) is not int:
        raise UIError("N01", "ui.%s(%r) start=%r" % (kind, key, st), where=where,
                      fix="start=340 (desk units), or the name of an int state")
    if p.get("size") is not None:
        nm = _state_name(p["size"], (Opt,), "ui.%s size=" % kind, where)
        if _st()._t[nm].base is not INT:
            raise UIError("T03", "ui.%s size=%r is %s" % (kind, nm, _st()._t[nm]), where=where,
                          fix="%s=ui.opt(0) in the state: absent until a divider moves it" % nm)
        if "start" not in p:
            raise UIError("N01", "ui.%s(%r) size=%r has no start=" % (kind, key, nm), where=where,
                          fix="start=340: where it is until the user drags it")


def panes(dir, key=None, **p):
    """Panes side by side ("row") or stacked ("col"): each child is a
    ui.pane or a ui.panes, which holds its size (size= an opt(int) state,
    start= until the user drags it) or grows (grow=True); exactly one grows.
    Sizes, starts and minimums are desk units. A divider between two
    children is dragged by the user, and its let-go writes the sizes beside
    it into their states. The outermost panes may name `focused=`, an
    opt(text) state the engine writes with the key of the pane the keys
    reach (F6, Shift+F6, a press), and `grown=`, an opt(text) state holding
    the key of the pane grown over the others (Ctrl+Shift+Enter; Back puts
    it back), absent while none is."""
    where = _caller()
    _sized("panes", key, p, where)
    for prop in ("focused", "grown"):
        if p.get(prop) is not None:
            nm = _state_name(p[prop], (Opt,), "ui.panes %s=" % prop, where)
            if _st()._t[nm].base is not TEXT:
                raise UIError("T03", "ui.panes %s=%r is %s" % (prop, nm, _st()._t[nm]), where=where,
                              fix='%s=ui.opt("") in the state: the engine writes a pane\'s key' % nm)
    n = _node("panes", key, dict(p, dir=dir), where)
    if n.parent is not None and n.parent.kind in ("pane", "panes") and (
            p.get("focused") is not None or p.get("grown") is not None):
        raise UIError("N03", "ui.panes(%r) focused=/grown= on panes inside panes" % key, where=where,
                      fix="name them on the outermost ui.panes: pane focus and grow are the workspace's")
    return n


def pane(key, **p):
    """One pane of ui.panes: size= names an opt(int) state the engine
    writes when a divider beside it is let go (absent: start=), or grow=True
    takes what is left. min= is its least (w, h), or by tab ({value: (w,
    h), "_": (w, h)}: the largest of its offered tabs'). tabs= names an enum
    whose ui.switch is its body, labels= that enum's table; its header
    shows them, offered= a condition a value (a value whose condition fails
    has no tab; a pane left with none is not shown). head=False: no header,
    the body is the pane's whole rect."""
    where = _caller()
    _sized("pane", key, p, where)
    if p.get("size") is None and not p.get("grow"):
        raise UIError("N01", "ui.pane(%r) neither holds a size nor grows" % key, where=where,
                      fix='size="name", start=N; or grow=True on the one that takes the rest')
    tabs = p.get("tabs")
    if tabs is not None:
        _state_name(tabs, (Enum,), "ui.pane tabs=", where)
    values = _st()._t[tabs].values if tabs is not None else ()
    for prop in ("offered", "min"):
        v = p.get(prop)
        if not isinstance(v, dict):
            continue
        if tabs is None:
            raise UIError("N01", "ui.pane(%r) %s= by tab with no tabs=" % (key, prop), where=where,
                          fix="%s on a tabbed pane, else %s" % (prop, "min=(64, 48)" if prop == "min" else "shown="))
        extra = [x for x in v if x not in values and not (prop == "min" and x == "_")]
        if extra:
            raise UIError("N01", "ui.pane(%r) %s= names %s, not a tab" % (key, prop, ", ".join(extra)),
                          where=where, expected=", ".join(values), fix="a key for a value of %r" % tabs)
    if p.get("head") not in (None, True, False):
        raise UIError("N01", "ui.pane(%r) head=%r" % (key, p["head"]), where=where,
                      fix="head=False: a pane with no header, its body its whole rect")
    return _node("pane", key, p, where)


def text_editor(name, **p):
    """The text editor node over the document `name` (a ui.doc): typed keys go
    into it with no app code; find= names the query's text state, marks= a
    model of (line, kind, words) it marks in the gutter and on the line."""
    where = _caller()
    _state_name(name, (Doc,), "ui.text_editor", where)
    if p.get("find") is not None:
        _state_name(p["find"], (TEXT,), "ui.text_editor find=", where)
    if p.get("marks") is not None:
        m = _state_name(p["marks"], (Model,), "ui.text_editor marks=", where)
        mt = _st()._t[m]
        need = {"line": INT, "words": TEXT}
        bad = [f for f, t in need.items() if mt.fields.get(f) is not t] + (
            [] if isinstance(mt.fields.get("kind"), Enum) else ["kind"])
        if bad:
            raise UIError("T03", "ui.text_editor marks=%r: its rows need %s" % (m, ", ".join(bad)),
                          where=where, fix='ui.model(key="line", line=0, kind=ui.enum("error", ...), words="")')
    return _node("text_editor", name, p, where)


LEGACY_IN = ("page", "panel", "case", "pane")


def legacy(key, draw, **p):
    """A surface still drawn with today's verbs, hosted in one node (§7.7,
    the bridge): one focus stop over the rect the engine lays out for it.
    `draw(cv, th, x, y, w, h, focused, dt)` draws the surface into that rect,
    under the page's theme `th`,
    BEFORE the page's own verbs pass (a hosted surface begins its own pass;
    the page's overlays lie over it); `pointer(x, y, click)` is handed a
    press in its own coordinates and what follows until the release, True
    where it took it; `keys(i)` the frame's input while it holds focus,
    True where it took it (else the page moves focus as from any stop);
    `hints()` the hints the page's band shows while it holds focus; `gen()`
    an int that moves whenever its pixels may, which an idle frame polls
    (it asks for its own frames otherwise). Stands where a region stands:
    in a page, a panel, a case or a pane. It goes with the old engine."""
    where = _caller()
    n = _node("legacy", key, dict(p, draw=draw), where)
    if n.parent is not None and n.parent.kind not in LEGACY_IN:
        raise UIError("N03", "ui.legacy(%r) in a %s" % (key, n.parent.kind), where=where,
                      expected=", ".join(LEGACY_IN),
                      fix="a hosted surface is a region: put it in a pane, a case or a panel")
    return n


def slot(name, **p):
    """A hole in the app's layout another process's window may be docked in
    (the desk handoff's ui.slot): the shell resolves its rect."""
    return _node("slot", name, p, _caller())


def row(key, **p):
    """Children side by side; a field grows, the rest take their size.
    close= is what Back does while the row is shown (a find strip's close).
    On a placed page: gap= between cells and pad= at each end (a literal in
    the page's unit, an int state in pixels the host measured); a row in a
    row is a region of its own (an empty one a spacer), and mid=True
    centres it on its parent, each side's fills sharing that side's room."""
    return _node("row", key, p, _caller())


def tools(key, **p):
    """The buttons the shown tab lends: a pane's header on the desk, the
    bar (here: the head row) on a handheld."""
    return _node("tools", key, p, _caller())


def lent(key, **p):
    """Where the frame draws the buttons a shown unit lends (`tools KEY lend=NAME`)."""
    return _node("lent", key, p, _caller())


class Ev:
    """An event a canvas's pointer hook is handed. Read-only."""
    __slots__ = ("kind", "x", "y", "source")

    def __init__(self, kind, x=0, y=0, source=""):
        for k, v in (("kind", kind), ("x", x), ("y", y), ("source", source)):
            object.__setattr__(self, k, v)

    def __setattr__(self, k, v):
        raise UIError("S03", "an event is read-only (ev.%s = ...)" % k, fix='a canvas handler reads the event; it writes state')

    def __repr__(self):
        return "Ev(%s %d,%d)" % (self.kind, self.x, self.y)


def status(text, **p):
    """A status line: its template's fields, split at " · ", the first
    always shown, the rest while they fit."""
    return _node("status", None, dict(p, text=text), _caller())


def _anchors(root):
    """Every popover's anchor names a node of the tree: 'key' or 'key.part',
    or one of two by a condition ({@desk?a:b})."""
    keys = {}

    def walk(n):
        keys.setdefault(n.key, n)
        for k in n.kids:
            walk(k)
    walk(root)

    def check(n):
        if n.kind == "panes":
            # exactly one grows among the children shown together: one, or
            # several each shown by a condition (the layout holds the rest)
            grow = [k.key for k in n.kids if k.l.get("grow") or (
                k.kind in ("pane", "panes") and "size" not in k.l)]
            always = [k for k in n.kids if k.key in grow and "shown" not in k.src]
            if not grow or len(always) > 1 or (always and len(grow) > 1):
                raise UIError("N07", "ui.panes has %d children that grow" % len(grow), where=n.where,
                              found=", ".join(map(str, grow)) or "none",
                              fix='exactly one grows; the others hold size="name", start=N')
        anc = n.src.get("anchor") if n.kind == "popover" else None
        for a in (_branches(anc) if anc is not None else ()):
            key, _, part = a.partition(".")
            tgt = keys.get(key)
            if tgt is None:
                raise UIError("N06", "popover %r anchor=%r names no node" % (n.key, a), where=n.where,
                              fix=("did you mean %s?" % _near(key, keys)) or
                              "an anchor is a node's key: a state name, an action, a pane's key")
            parts = ANCHORS.get(tgt.kind, ())
            if part and parts is not None and part not in parts:
                raise UIError("N06", "popover %r anchor=%r: a %s has no part %r" % (
                    n.key, a, tgt.kind, part), where=n.where,
                    expected=", ".join(parts) or "(none)", fix='anchor="KEY.PART" with a part the node has')
            if part and parts is None and part not in tgt.l.get("values", ()):
                raise UIError("N06", "popover %r anchor=%r: no tab %r" % (n.key, a, part), where=n.where,
                              expected=", ".join(tgt.l.get("values", ())), fix='anchor="KEY.TAB" with one of the tabs')
        for k in n.kids:
            check(k)
    check(root)


def push(build):
    a = _command("push")
    a.pages.append(build)
    a.openers = getattr(a, "openers", []) + [getattr(a, "fired_id", None)]
    _trace("page", "push", "page %d, opened from %r" % (len(a.pages), a.openers[-1]))
    a.root = None


def pop():
    """From a handler: the page on top closes, and focus returns to the node
    that pushed it. On the first page of a page its host shows (a Settings
    page), the host closes it."""
    a = _command("pop")
    if len(a.pages) > 1:
        a.pages.pop()
        a.root = None
        a.want_focus = a.openers.pop() if getattr(a, "openers", None) else None
        _trace("page", "pop", "page %d, focus back to %r" % (len(a.pages), a.want_focus))
        return
    if a.close_root is None:
        raise UIError("P05", "ui.pop() on the app's first page", fix="Back leaves the app; pop() closes a "
                      "page ui.push() opened")
    _trace("page", "pop", "the host closes the page")
    a.close_root()


# the theme and font, asked of the engine (the checker answers for one look)
def text_size(t, scale=1):
    return _q("text_size", t, scale)


def colors():
    return _q("colors")


def fade(rect, top):
    return _q("fade", rect, top)


def _q(name, *a):
    app = _NOW.app
    if _NOW.building:
        raise UIError("B01", "ui.%s inside a build: a build asks the engine nothing" % name,
                      fix="a text or a condition the engine evaluates; a canvas asks in its draw")
    if app is None:
        raise UIError("A10", "ui.%s outside a handler or a draw" % name, fix='ask it in a handler or a canvas draw')
    return app.query(name, *a)


def _removed(name, fix):
    def f(*a, **k):
        raise UIError("N04", "ui.%s is not in the UI tree API" % name, fix=fix)
    return f


cell = _removed("cell", "declare it in ui.state(...) and name it")
per = _removed("per", '"{@desk?a:b}" in a template, shown="@desk" for structure')
fmt = _removed("fmt", '"{x|fmt %.1f}" in a template')
ref = _removed("ref", 'declare name=ui.row_of("model") in the state')
key = _removed("key", 'ui.action(name, label, handler, keys=("m",))')
rebuild = _removed("rebuild", "rows by kind, or ui.switch(name, cases)")
at = _removed("at", "placement is ui.split, ui.grid, ui.foot")
scope = _removed("scope", "rows by kind, or ui.switch(name, cases)")
text = _removed("text", 'ui.label(text, face="result", size="fit")')
has_symbols = _removed("has_symbols", "the engine's glyph fallback: write the symbol")


# -- the build, shared by the app and the checker -----------------------------

class _AnyColour(dict):
    """The checker's theme: every colour asked is 0."""

    def __missing__(self, k):
        return 0


class Builder:
    """Runs a page's build: typed, keyed, every name resolved."""

    # a direct build keeps each template's and condition's source for the
    # engine that compiles it (the C engine's board build mode)
    direct = False
    QUERIES = {"text_size": lambda t, sc=1: (6 * sc * len(t), 8 * sc),
               "colors": lambda: _AnyColour(), "fade": lambda r, top: None}

    def query(self, name, *a):
        return self.QUERIES[name](*a)

    def init_builder(self, s, build):
        self.s = s
        self.pages = [build]
        self.root = None
        self.builds = 0
        self.clock = 0
        self.timers = []
        self.classes = ()
        self.input = "keys"             # @input: the engine's replay sets it each frame
        self.focus_key = ""
        self.want_focus = None
        self.trace = collections.deque((), 200)

    def _build(self):
        was = _NOW.app
        _NOW.app = self
        self.stack, self.ctx, self.root = [], Ctx(), None
        _NOW.building = True
        try:
            r = self.pages[-1]()
            if r is not None:
                raise UIError("N01", "a page function returned a value", fix="a page function describes; it returns nothing")
        finally:
            _NOW.building = False
            _NOW.app = was
        if self.root is None:
            raise UIError("B03", "a page function made no page", fix="start it with `with ui.page(title):`",
                          where=(getattr(self.pages[-1], "__code__", None) and (
                              _base(self.pages[-1].__code__.co_filename),
                              self.pages[-1].__code__.co_firstlineno)) or ("?", 0))
        _anchors(self.root)
        self.builds += 1
        _NOW.app = self
        try:
            _trace("build", getattr(self.pages[-1], "__name__", "page"), "page (builds %d)" % self.builds)
        finally:
            _NOW.app = was

    def run(self, fn, *args, action=None):
        was, was_act = _NOW.app, _NOW.action
        _NOW.app, _NOW.action = self, action
        try:
            return fn(*args)
        finally:
            _NOW.app, _NOW.action = was, was_act

    def run_action(self, act, args, source):
        if TRACING:                     # a board keeps no trace: no line made for it
            was, _NOW.app = _NOW.app, self
            try:
                _trace("action", act.name, "from %s" % source)
            finally:
                _NOW.app = was
        self.run(act.fn, *args, action=act.name)
        commit(self)

    def fire_named(self, name, *args):
        """Tests by name: fire an action as its key would; an action of a
        unit not shown is off, as its key is."""
        a = _d().actions[name]
        if not a.live():
            was, _NOW.app = _NOW.app, self
            try:
                _trace("off", name, "unit %s is not shown" % a.unit)
            finally:
                _NOW.app = was
            return
        self.run_action(a, args or self.args_for(a), "test")

    def args_for(self, act, row=None, arg=None):
        if row is not None:
            return (row,)
        if act.row is not None:
            return (act.cur_row(),)
        return () if arg is None else (arg,)


_TRACE_OFF = [False]


def _trace(kind, name, text):
    """A line of the engine's trace: its last 200 lines (a deque), on the
    host only (a board, which has no frames, keeps none: a line a write is
    an allocation a frame)."""
    a = _NOW.app
    if a is None or not TRACING or (_TRACE_OFF[0] and kind in ("state", "derive")):
        return
    ms = a.clock
    a.trace.append("%02d:%02d:%02d.%03d  %-7s %-8s %s" % (
        ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000, kind, name, text))


def snapshot(app):
    """The tree as text: kind, key, each prop's source and its value now."""
    out = []

    def val(n, k, row):
        if k in n.p:
            return n.p[k](row)
        return n.l.get(k)

    def line(n, depth, row):
        bits = ["%s%s %r" % ("  " * depth, n.kind, n.key)]
        if n.act is not None:
            bits.append("action=%s%s" % (n.act.name, "" if n.act.can() else "(off: %s)" % n.act.when_src))
        if n.kind in ("toggle", "slider", "field"):
            v = app.s._v[n.key]
            bits.append("bind=%s:%r = %r" % (n.key, app.s._t[n.key],
                                             "*" * len(v) if n.l.get("secret") else v))
        for k, src in n.src.items():
            if k in ("draw", "paint", "buttons", "kind") and k in n.l:
                continue
            v = val(n, k, row)
            if isinstance(v, Action):
                bits.append("%s=%s" % (k, v.name))
            elif k in n.p and isinstance(src, str) and ("{" in src or SCHEMA[n.kind][k] == "cond"):
                bits.append("%s=%r = %r" % (k, src, v))
            else:
                bits.append("%s=%r" % (k, v))
        if n.kind == "dialog":
            bits.append("buttons=%s" % ",".join(a.name for a in n.l["buttons"]))
        out.append("  ".join(bits))
        if n.kind in ("rows", "cards"):
            m = app.s._v[n.l["model"]]
            out.append("%s  [%d rows of %s]" % ("  " * depth, len(m), n.l["model"]))
            for r in m:
                out.append("%s  row %r" % ("  " * depth, r._f[r._m.key]))
                for k in n.kids:
                    line(k, depth + 2, r)
            return
        for k in n.kids:
            line(k, depth + 1, row)
    if app.root is not None:
        line(app.root, 0, None)
    return "\n".join(out)


# -- load states, posts, work and pictures (G9, G12, M2) ------------------------

class Ticket:
    __slots__ = ("load", "n")

    def __init__(self, load, n):
        self.load, self.n = load, n

    def __repr__(self):
        return "ticket %s#%d" % (self.load, self.n)


class LoadView:
    """A load's state in a handler: start() hands a ticket and moves it to
    loading (every earlier ticket is stale from then); done() and fail()
    end it."""

    def __init__(self, st, name):
        self._s, self._n = st, name

    def _move(self, v):
        _NOW.load_move = True
        try:
            setattr(self._s, self._n, v)
        finally:
            _NOW.load_move = False

    def start(self):
        tk = self._s._tickets
        tk[self._n] = tk.get(self._n, 0) + 1
        self._move("loading")
        return Ticket(self._n, tk[self._n])

    def done(self):
        self._move("loaded")

    def fail(self):
        self._move("failed")

    def __eq__(self, o):
        return self._s._v[self._n] == o

    def __repr__(self):
        return repr(self._s._v[self._n])



def post(name, arg=None, ticket=None):
    """G12: a service hands the app a value from outside any handler: the
    hook `name` runs before the next frame. A post whose ticket a later
    start() made stale is dropped there, and traced."""
    if _NOW.building:
        raise UIError("B02", "ui.post inside a build", fix="post from a service's callback; a build names")
    d = _d()
    a = d.actions.get(name)
    if a is None or not a.hook:
        raise UIError("A01", "post(%r): no hook %r" % (name, name),
                      expected=", ".join(k for k, x in d.actions.items() if x.hook) or "(none)",
                      fix="declare it: ui.hook(%r, handler, takes=\"arg\")" % name)
    if ticket is not None and not isinstance(ticket, Ticket):
        raise UIError("S02", "post(%r, ticket=%r): a ticket is what start() handed" % (name, ticket),
                      fix="t = s.LOAD.start() in the handler that asks; the service posts with ticket=t")
    if (arg is not None) != (a.takes == "arg"):
        raise UIError("A02", "post(%r%s): hook %r takes=%r" % (name, ", arg" if arg is not None else "",
                                                              name, a.takes),
                      fix='hand an argument only to a hook declared takes="arg"')
    d.posts.append((a, arg, ticket))


class Work:
    __slots__ = ("name", "fn", "due", "budget", "where", "slices", "frames", "ms", "over", "unit", "due_src")

    def __init__(self, name, fn, due, budget, where):
        self.name, self.fn, self.due, self.budget, self.where = name, fn, due, budget, where
        self.slices = self.frames = self.over = 0
        self.ms = 0.0


def work(name, fn, due, budget=4, unit=None):
    """G12's work: a step the engine runs while `due` holds, as many times
    as fit `budget` ms of a frame's time left after its input and paint,
    never on a frame whose input is late."""
    where = _caller()
    _declaring("work")
    if name in _d().works or name in _d().actions:
        raise UIError("U02", "work %r: the name is taken" % name, where=where, fix='give the work a name no action, hook or work has')
    if _params(fn) not in (0, None):
        raise UIError("A02", "work %s: a step takes nothing; it reads state" % name, where=where,
                      fix="def %s():" % getattr(fn, "__name__", name))
    if type(budget) is not int or not 1 <= budget <= 16:
        raise UIError("W02", "work %s budget=%r" % (name, budget), where=where,
                      expected="an int of milliseconds, 1 to 16", fix='budget=4 (milliseconds, 1 to 16)')
    w = Work(name, fn, cond(due, Ctx(), where), budget, where)
    w.unit = unit
    w.due_src = due
    _d().works[name] = w
    return name


class Pictures:
    """The app's own pictures: bytes the engine decodes (a PNG, a .moyimg),
    or an indexed drawing in the console's 16 colours that is no file. The
    engine holds them; `image.own(key)` names one."""

    def __init__(self):
        self.src = {}
        self.gen = {}

    def put(self, key, data=None, w=None, h=None, pixels=None):
        if (data is None) == (pixels is None):
            raise UIError("S02", "pictures.put(%r): bytes (data=) or an indexed drawing (w=, h=, pixels=)" % key, fix='pictures.put(KEY, data=PNG_BYTES) or pictures.put(KEY, w=W, h=H, pixels=INDICES)')
        if pixels is not None:
            if type(w) is not int or type(h) is not int or len(pixels) != w * h:
                raise UIError("S02", "pictures.put(%r): pixels are w * h palette indices" % key,
                              found="w=%r h=%r %d bytes" % (w, h, len(pixels)), fix='pixels= holds w * h bytes')
            self.src[key] = ("idx", w, h, bytes(pixels))
        else:
            self.src[key] = ("bytes", bytes(data))
        self.gen[key] = self.gen.get(key, 0) + 1
        a = _NOW.app
        if a is not None and getattr(a, "pics", None) is not None:
            a.pics.forget_own(key)

    def drop(self, key):
        self.src.pop(key, None)
        self.gen[key] = self.gen.get(key, 0) + 1


pictures = Pictures()


# -- the checker's run: a refused call is recorded, and the run goes on ---------

class _Refused(Node):
    """What a refused node call hands back in the checker's run: its children
    are checked under it, and it joins no tree."""

    def __init__(self, where):
        Node.__init__(self, "panel", "(refused)", where)

    def __enter__(self):
        b = _NOW.app
        if b is not None and getattr(b, "stack", None) is not None:
            b.stack.append(self)
        return self

    def __exit__(self, *a):
        b = _NOW.app
        if b is not None and getattr(b, "stack", None) and b.stack[-1] is self:
            b.stack.pop()


# the props a declaration takes, checked before the call (§5.1); a node
# kind's are its SCHEMA's
DECL_PROPS = {"action": ("takes", "when", "off", "hint", "row", "chars", "keys", "icon", "unit", "push")}


def _strict(fn, k, props):
    """A prop the call does not take: N04 with its replacement where §7.9
    retired it (an action's `key=`), else N01 naming the ones it takes."""
    for x in k:
        if x not in props:
            if fn.__name__ == "action" and x in RETIRED_ACTION:
                raise UIError("N04", "ui.action %s= is retired" % x, where=_caller(),
                              fix=RETIRED_ACTION[x] % {"v": k[x]})
            near = _near(x, props)
            raise UIError("N01", "ui.%s has no prop %r" % (fn.__name__, x), where=_caller(),
                          expected=", ".join(props), fix="did you mean %s?" % near if near else "drop it")


def _collecting(fn, returns_node):
    props = DECL_PROPS.get(fn.__name__)

    def call(*a, **k):
        if _NOW.collect is None:
            if props is not None:
                _strict(fn, k, props)
            return fn(*a, **k)
        try:
            if props is not None:
                _strict(fn, k, props)
            return fn(*a, **k)
        except UIError as e:
            _NOW.collect.append(e)
            return _Refused(e.where) if returns_node else (a[0] if a else None)
    return _wraps(call, fn)


NODE_CALLS = ("page", "group", "label", "info", "link", "button", "item", "card", "toggle", "slider",
              "field", "seg", "table", "col", "rows", "cards", "picture", "progress", "empty", "dialog",
              "popover", "menu", "split", "grid", "panel", "canvas", "foot", "scroll", "rule", "note",
              "text_view", "switch", "tabs", "panes", "pane", "text_editor", "slot", "row", "tools", "lent", "status",
              "legacy")
DECL_CALLS = ("action", "hook", "labels", "work", "derive", "app")
COMMAND_CALLS = ("push", "pop", "focus", "after", "post")
for _n in NODE_CALLS:
    globals()[_n] = _collecting(globals()[_n], True)
for _n in DECL_CALLS + COMMAND_CALLS:
    globals()[_n] = _collecting(globals()[_n], False)
del _n
