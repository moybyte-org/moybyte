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


def entry(blob):
    """A page's entry as the frozen module holds it: (blob, state names,
    their type codes, action names, {node: draw}, {node: ground})."""
    f = front_of(blob)
    return (blob, tuple(f["S"]), f["T"], tuple(f["A"]), f["D"], f["G"])


def front_of(blob):
    """The front end's part of a blob (the header's front offset and
    length): {"S": state names, "T": their type codes, "A": action names,
    "D": {node: draw function name}, "G": {node: ground role}}."""
    if len(blob) < 32 or blob[:4] != b"MUIT":
        raise Refused("not a compiled page")
    at, n = _u32(blob, 24), _u32(blob, 28)
    out = {"S": [], "T": b"", "A": [], "D": {}, "G": {}}
    for line in bytes(blob[at:at + n]).decode("utf-8").split("\n"):
        if not line:
            continue
        tag, rest = line[0], line[2:]
        if tag in "SA":
            out[tag] = rest.split(",") if rest else []
        elif tag == "T":
            out["T"] = bytes(int(c) for c in rest)
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
    lines = ("S " + ",".join(eng._names), "T " + "".join(str(ctype(eng.s._t[n])) for n in eng._names),
             "A " + ",".join(a.name for a in eng._acts), "D " + ",".join(draws),
             "G " + ",".join("%d:%s" % kv for kv in eng._grounds.items()))
    return ("\n".join(lines) + "\n").encode("utf-8")


def ctype(t):
    """A state's type as the store declares it (`CEngine._declare`'s, the
    header's UIT_BOOL .. UIT_MODEL)."""
    return (1 if t is S.BOOL else 2 if t is S.INT else 3 if t is S.NUM else 4 if t is S.TEXT else
            7 if isinstance(t, S.Model) else 6 if isinstance(t, S.RowOf) else 5 if type(t) is S.Enum else 0)


def load(eng, x, blob):
    """Fill the new store `x` from `blob` (a blob, or a frozen `entry`) for `eng`'s first page: its states
    and actions bound by name to the module's (a missing one refused by name,
    as a build would), its canvases' draws to the module's functions, every
    state pushed. Refused (nothing loaded) where the blob is not this page's,
    this engine's or this module's."""
    if type(blob) is tuple:
        f = {"S": blob[1], "T": blob[2], "A": blob[3], "D": blob[4], "G": blob[5]}
        blob = blob[0]
    else:
        f = front_of(blob)
    _load(eng, x, blob, f)


def _load(eng, x, blob, f):
    st, acts = eng.s, eng.actions
    names = f["S"]                  # the store's slots in the blob's order (a board's dicts keep another)
    for n in names:
        if n not in st._t:
            raise Refused("no state %r: the blob names one the module does not declare" % n)
    if len(names) != len(st._t):
        raise Refused("state %s: the module declares one the blob lacks" % [n for n in st._t if n not in names][0])
    for n, t in zip(names, f["T"]):
        if ctype(st._t[n]) != t:
            raise Refused("state %s: the module's type is not the blob's" % n)
    for a in f["A"]:
        if a not in acts:
            raise Refused("no action %r: the blob names one the module does not declare" % a)
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
    eng._slot = dict((n, i) for i, n in enumerate(names))
    eng._names = list(names)
    eng._types = [st._t[n] for n in names]
    eng._acts = [acts[a] for a in f["A"]]
    eng._aidx = dict((a, i) for i, a in enumerate(f["A"]))
    eng._draws, eng._grounds = draws, f["G"]
    x.on_draw(eng._draw)
    eng._drawing = True
    for name in names:
        eng._push(name)
