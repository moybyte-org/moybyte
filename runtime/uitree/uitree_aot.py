"""Pages compiled ahead of time: the C engine's store as a build's lowering
leaves it, made on the host (`tools/uitree_compile.py`) and loaded on a
board with no build, no checks and no lowering (`load`). The format is
native/moy_uitree/README.md's "A compiled page".

A build reads no state and asks the engine nothing (B01-B03): every size
class, input kind, desk or handheld and font scale a page answers to is a
template or condition the engine evaluates at draw. So a lowered page is a
function of its module's code alone, and the variant key is empty (`VARIANT`).

`BLOBS` maps a page ("module.function") to its blob: the image's frozen
`uitree_pages` module where one was staged, else none, and a page with no
blob builds on the device as before. The frozen module holds each blob with
its front already read (`entry`): a frozen tuple of names is ROM on a
board, where reading the blob's front would allocate a string a name.
"""

import sys

import uitree as S

VARIANT = ""

try:
    from uitree_pages import PAGES as BLOBS       # staged with the image, frozen beside it
except ImportError:
    BLOBS = {}


class Refused(Exception):
    """A blob this front end does not take: its message names why."""


def _module(fn):
    """A function's module name (MicroPython's functions carry no
    `__module__`: their globals name it)."""
    m = getattr(fn, "__module__", None)
    if m is None:
        g = getattr(fn, "__globals__", None)
        m = g.get("__name__") if g is not None else None
    return m or "?"


def key(fn):
    """A page's name: its module's (the last part: the host imports runtime
    modules under a package, an image flat) and its function's."""
    return "%s.%s" % (_module(fn).rpartition(".")[2], getattr(fn, "__name__", "?"))


def blob_for(fn):
    return BLOBS.get(key(fn)) if BLOBS else None


def _u32(b, at):
    return b[at] | b[at + 1] << 8 | b[at + 2] << 16 | b[at + 3] << 24


DOC = "#doc"        # a document's hidden model: its state's name and this (uitree_c's `_DOC`)


def entry(blob):
    """A page's entry as the frozen module holds it: (blob, state names,
    their type codes, action names, {node: draw}, {node: ground}, the rest
    of the front end's literals (`CEngine.front`), the slots' orders
    (`orders`), then `tables`' four), every part made on the host, the
    dicts once when the image imports it."""
    f = front_of(blob)
    return (bare(blob), tuple(f["S"]), f["T"], tuple(f["A"]), f["D"], f["G"], f["X"], f["O"]) + tables(f)


def _put32(b, at, v):
    b[at:at + 4] = bytes(((v >> s) & 255) for s in (0, 8, 16, 24))


def bare(blob):
    """`blob` with no front end's part: what the store reads alone, its
    length and checksum made again (the C loader stops at the front's
    offset). A frozen entry holds this, its front already read beside it."""
    at = _u32(blob, 24)
    out = bytearray(blob[:at])
    _put32(out, 20, at)
    _put32(out, 28, 0)
    h = 2166136261
    for c in out[32:]:
        h = ((h ^ c) * 16777619) & 0xffffffff
    _put32(out, 16, h)
    return bytes(out)


def tables(f):
    """What a load binds by index, from a blob's front: {state: its slot} (a
    document's model, named its state's name and DOC, has none of its own),
    {action: its index}, the documents' state names, and how many states
    the module declares."""
    names = f["S"]
    docs = tuple(n[:-len(DOC)] for n in names if n.endswith(DOC))
    slot = dict((n, i) for i, n in enumerate(names) if not n.endswith(DOC))
    return slot, dict((a, i) for i, a in enumerate(f["A"])), docs, len(names) - len(docs)


def _literal(text):
    try:
        import ast
    except ImportError:             # a board: only ever a blob with no frozen entry
        return eval(text, {}, {})
    return ast.literal_eval(text)


def front_of(blob):
    """The front end's part of a blob (the header's front offset and
    length): {"S": state names, "T": their type codes, "A": action names,
    "D": {node: draw function name}, "G": {node: ground role}, "X": the
    rest of the front end's literals, "O": the slots' orders}."""
    if len(blob) < 32 or blob[:4] != b"MUIT":
        raise Refused("not a compiled page")
    at, n = _u32(blob, 24), _u32(blob, 28)
    out = {"S": [], "T": b"", "A": [], "D": {}, "G": {}, "X": None, "O": ()}
    for line in bytes(blob[at:at + n]).decode("utf-8").split("\n"):
        if not line:
            continue
        tag, rest = line[0], line[2:]
        if tag in "SA":
            out[tag] = rest.split(",") if rest else []
        elif tag == "T":
            out["T"] = bytes(int(c) for c in rest)
        elif tag in "XO":
            out[tag] = _literal(rest)
        else:
            for item in rest.split(",") if rest else ():
                k, _, v = item.partition(":")
                out[tag][int(k)] = v
    return out


def front(eng):
    """The front end's part of a blob, from the engine that lowered the
    page: its states and their types, its actions, its canvases' draws and
    grounds."""
    draws = []
    for node, fn in eng._draws.items():
        name = getattr(fn, "__name__", "")
        mod = sys.modules.get(_module(fn))
        if mod is None or getattr(mod, name, None) is not fn:
            raise Refused("canvas %d draw=%r is not a function of its module" % (node, fn))
        draws.append("%d:%s" % (node, name))
    lines = ("S " + ",".join(eng._names), "T " + "".join(str(ctype(t)) for t in eng._types),
             "A " + ",".join(a.name for a in eng._acts), "D " + ",".join(draws),
             "G " + ",".join("%d:%s" % kv for kv in eng._grounds.items()), "X " + repr(eng.front()),
             "O " + repr(tuple(orders(t) for t in eng._types)))
    return ("\n".join(lines) + "\n").encode("utf-8")


def orders(t):
    """What of state type `t` the store keeps by index, in the order the C
    store numbers it: a model's fields (`Model.order`), a document's row's
    (`DOC_ORDER` after its text), an enum's values; None for the rest. A
    blob holds its compile's, and a load refuses one this Python orders
    otherwise, so no dict's order on either side can trade two indexes."""
    if type(t) is S.Opt:
        t = t.base
    if t == DOC:
        return ("doc",) + S.DOC_ORDER
    if isinstance(t, S.Model):
        return t.order
    if type(t) is S.Enum:
        return t.values
    return None


def ctype(t):
    """A state's type as the store declares it (`CEngine._declare`'s, the
    header's UIT_BOOL .. UIT_MODEL): an optional state its base's, a
    document a row_of its hidden model (`DOC`), which is a model."""
    if type(t) is S.Opt:
        t = t.base
    if t == DOC:
        return 7
    if type(t) is S.Doc:
        return 6
    return (1 if t is S.BOOL else 2 if t is S.INT else 3 if t is S.NUM else 4 if t is S.TEXT else
            7 if isinstance(t, S.Model) else 6 if isinstance(t, S.RowOf) else 5 if type(t) is S.Enum else 0)


def load(eng, x, blob):
    """Fill the new store `x` from `blob` (a blob, or a frozen `entry`) for
    `eng`'s first page: its states and actions bound by name to the module's
    (a missing one, or one of another type, refused by name), its canvases'
    draws to the module's functions, every state pushed. Refused (nothing
    loaded) where the blob is not this page's, this engine's or this
    module's."""
    if type(blob) is tuple:
        f = {"S": blob[1], "T": blob[2], "A": blob[3], "D": blob[4], "G": blob[5], "X": blob[6], "O": blob[7]}
        tb = blob[8:]
        blob = blob[0]
    else:
        f = front_of(blob)
        tb = tables(f)
    _load(eng, x, blob, f, tb)


# a scalar's store type, by the type object (`ctype` for the rest)
_SCALAR = {S.BOOL: 1, S.INT: 2, S.NUM: 3, S.TEXT: 4}


def _load(eng, x, blob, f, tb):
    """The compiler proved the blob against the module it was made from; the
    load binds each name in one pass, the lookups the engine needs anyway
    refusing what this module lacks or types otherwise."""
    st, acts = eng.s, eng.actions
    slot, aidx, docs, declared = tb
    decl = st._t
    names = f["S"]                  # the store's slots in the blob's order (a board's dicts keep another)
    if len(f["O"]) != len(names):
        raise Refused("a blob with no order for each of its states")
    types = []
    for n, c, o in zip(names, f["T"], f["O"]):
        t = decl.get(n)
        if t is None:
            if n.endswith(DOC) and type(decl.get(n[:-len(DOC)])) is S.Doc:
                t = DOC
            else:
                raise Refused("no state %r: the blob names one the module does not declare" % n)
        elif _SCALAR.get(t, 0) != c and ctype(t) != c:
            raise Refused("state %s: the module's type is not the blob's" % n)
        if o is not None and orders(t) != o:
            raise Refused("state %s: its fields or values are in another order than the blob's" % n)
        types.append(t)
    if len(decl) != declared:
        raise Refused("state %s: the module declares one the blob lacks" % [n for n in decl if n not in slot][0])
    try:
        bound = [acts[a] for a in f["A"]]
    except KeyError as e:
        raise Refused("no action %r: the blob names one the module does not declare" % e.args[0])
    mod = sys.modules[_module(eng.pages[0])]
    draws = {}
    for node, name in f["D"].items():
        fn = getattr(mod, name, None)
        if fn is None:
            raise Refused("canvas %d: no draw %r in %s" % (node, name, mod.__name__))
        draws[node] = fn
    r = x.load(blob, eng._page_id(), VARIANT)
    if r < 0:
        raise Refused(x.error())
    eng._slot, eng._aidx = slot, aidx         # read only: the entry's, made once
    eng._names = list(names)
    eng._types = types
    eng._docs = dict((d, [-1] * 8) for d in docs)
    eng._acts = bound
    eng._draws, eng._grounds = draws, f["G"]
    eng.unfront(f["X"])
    x.on_draw(eng._draw)
    eng._drawing = True
    for name in names:
        eng._push(name)
