"""The memory census: who holds a console board's memory, by owner.

docs/native_kernel_2026-09.md's sprint 0 asks who sets the boot heap's peak,
who holds what is live after it, and who holds the off-heap PSRAM. This module
answers on the board, and `tools/mem_census.py` drives it from the host.

The boot trace is ARMED by a file, `/mem_census` on the flash root, written by
the host tool and read once when this module is first imported. Unarmed,
`mark()` returns at once and nothing is installed. Armed:

  - `mark(tag)` records one row at a step of the boot (`desktop_spine` and
    the boards' `moy_runtime` call it): ticks_ms, the gc heap's areas and
    bytes held, the bytes allocated (live and garbage alike), PSRAM free and
    largest, and the `moy_alloc` registry. The file's text "live" also runs a
    collect at each mark and records what is live then, and each area's
    occupancy -- which perturbs the boot it measures, so the default ("lite")
    does not.
  - every module imported from then on is timed, inclusive of what it
    imports, by an `__import__` hook (`imports()`).

`gc.growths()` (tools/patch_gc_census.py) dates every area the heap added on
the same clock, so the boot's marks place each growth in its step.

After boot, on a running console (each is the `py` verb's expression):

  snap()            the heap figures at one moment (one collect)
  offheap(ws)       the registry's buffers by the first path that reaches
                    each from `ws` and every module, in bytes
  referrers(addr)   the heap blocks that hold an address, link by link out
                    to the object that owns it (gc.refs())
  cut(ws)           DESTRUCTIVE: drops each of ws's attributes in turn, then
                    every module's globals, collecting after each, and records
                    the bytes each drop freed and from which areas. The
                    console does not survive it; reboot after.

A figure a build cannot give is None, never 0: a host has no `esp32`, a build
without the gc patches no `gc.areas()` / `gc.area_map()` / `gc.growths()` /
`gc.refs()`.
"""

import gc
import sys

try:
    from time import ticks_ms, ticks_us, ticks_diff
except ImportError:  # CPython host
    import time as _t

    def ticks_ms():
        return int(_t.monotonic() * 1000)

    def ticks_us():
        return int(_t.monotonic() * 1000000)

    def ticks_diff(a, b):
        return a - b

try:
    import esp32 as _esp32
except ImportError:
    _esp32 = None

try:
    import moy_alloc as _moy_alloc
except ImportError:
    _moy_alloc = None

FLAG = "/mem_census"

_PSRAM, _SRAM, _DMA = 0x400, 0x800, 0x808


def _armed():
    try:
        with open(FLAG) as f:
            return f.read().strip() or "lite"
    except OSError:
        return None


MODE = _armed()
MARKS = [] if MODE else None
IMPORTS = [] if MODE else None


def caps(c):
    """(total, free, largest, low-water) over heap_caps regions with caps `c`,
    or None without esp32."""
    if _esp32 is None:
        return None
    try:
        regs = _esp32.idf_heap_info(c)
    except Exception:  # noqa: BLE001
        return None
    if not regs:
        return None
    tot = free = big = low = 0
    for r in regs:
        tot += r[0]
        free += r[1]
        low += r[3]
        if r[2] > big:
            big = r[2]
    return (tot, free, big, low)


def _areas():
    fn = getattr(gc, "areas", None)
    return fn() if fn is not None else (None, None)


def _area_map():
    fn = getattr(gc, "area_map", None)
    return fn() if fn is not None else None


def _registry():
    fn = getattr(_moy_alloc, "stats", None) if _moy_alloc is not None else None
    return fn() if fn is not None else None


def mark(tag):
    """One boot-trace row at step `tag`; nothing when unarmed."""
    if MARKS is None:
        return
    t = ticks_ms()
    n, held = _areas()
    alloc = gc.mem_alloc()
    live = amap = None
    if MODE == "live":
        gc.collect()
        live = gc.mem_alloc()
        amap = _area_map()
    ps = caps(_PSRAM)
    MARKS.append((tag, t, n, held, alloc, live,
                  ps[1] if ps else None, ps[2] if ps else None,
                  _registry(), amap))


def _hook_imports():
    try:
        import builtins
    except ImportError:
        return
    orig = builtins.__import__
    depth = [0]
    seen = set()

    def _timed(name, *args):
        # A built-in module never enters sys.modules, so the name set is what
        # keeps a function-body `import json` to one row.
        if name in sys.modules or name in seen:
            return orig(name, *args)
        seen.add(name)
        depth[0] += 1
        t0 = ticks_us()
        try:
            return orig(name, *args)
        finally:
            depth[0] -= 1
            IMPORTS.append((name, depth[0], ticks_diff(ticks_us(), t0)))

    builtins.__import__ = _timed


if MODE:
    _hook_imports()
    mark("census")


def marks():
    """The boot trace's rows, or None when the boot was not armed."""
    return MARKS


def imports():
    """(module, nesting depth, us inclusive) per module's first import since
    arming."""
    return IMPORTS


def growths():
    fn = getattr(gc, "growths", None)
    return fn() if fn is not None else None


def store():
    """The store's resident native memory (docs/kernel_store_2026-10.md
    section 9): the index's rows, and the bytes it holds now and at its high
    water; None where the index is the Python one."""
    mi = sys.modules.get("moy_index")
    mem = getattr(mi, "mem", None)
    if mem is None:
        return None
    now, high = mem()
    cat = sys.modules.get("moy_catalogue")
    return {"rows": cat.rows() if cat is not None else None,
            "bytes": now, "high": high}


def snap():
    """The heap figures now: ticks_ms, (areas, held) and allocated read BEFORE
    the collect, live and each area's occupancy after it, the area events,
    every heap_caps set, the registry, the collector's pauses and the store's
    native memory."""
    t = ticks_ms()
    n, held = _areas()
    alloc = gc.mem_alloc()
    gc.collect()
    live = gc.mem_alloc()
    pauses = getattr(gc, "pauses", None)
    return {"t": t, "areas": n, "held": held, "alloc": alloc, "live": live,
            "area_map": _area_map(), "growths": growths(),
            "psram": caps(_PSRAM), "sram": caps(_SRAM), "dma": caps(_DMA),
            "registry": _registry(),
            "pauses": pauses() if pauses is not None else None,
            "store": store()}


# -- off-heap by owner -----------------------------------------------------

_SKIP = (int, float, str, bytes, bool, type(None))


def _addr(view):
    try:
        import uctypes
        return uctypes.addressof(view)
    except Exception:  # noqa: BLE001
        return None


def _children(obj):
    """(key, child) pairs the walker follows: a dict's values, a sequence's
    items, an instance's or a module's attributes."""
    if isinstance(obj, dict):
        try:
            return [("[%s]" % (k,), v) for k, v in obj.items()]
        except Exception:  # noqa: BLE001
            return ()
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [("[%d]" % i, v) for i, v in enumerate(obj)]
    d = None
    try:
        d = obj.__dict__
    except Exception:  # noqa: BLE001
        return ()
    if not isinstance(d, dict):
        return ()
    try:
        return [("." + str(k), v) for k, v in d.items()]
    except Exception:  # noqa: BLE001
        return ()


def offheap(ws, depth=14, modules=None):
    """Every live registry buffer, named by the first path that reaches it:
    `ws` first, then each module's globals (the pool and the loan register
    are module dicts in device_canvas). Returns {"total": (count, bytes),
    "owners": [(path, bytes)], "unowned": [(address, bytes)]}."""
    live_fn = getattr(_moy_alloc, "live", None) if _moy_alloc is not None else None
    if live_fn is None:
        return None
    mods = sys.modules if modules is None else modules
    want = {}
    for a, n in live_fn():
        want[a] = n
    found = []
    seen = set()
    roots = [("device_canvas", mods.get("device_canvas")), ("ws", ws)]
    for name in sorted(mods):
        if name not in ("device_canvas", "mem_census"):
            roots.append((name, mods[name]))
    for rname, root in roots:
        if root is None or not want:
            continue
        stack = [(root, rname, 0)]
        while stack and want:
            obj, path, lvl = stack.pop()
            if isinstance(obj, _SKIP) or callable(obj) and not hasattr(obj, "__dict__"):
                continue
            k = id(obj)
            if k in seen:
                continue
            seen.add(k)
            if isinstance(obj, memoryview):
                a = _addr(obj)
                if a in want:
                    found.append((path, want.pop(a)))
                continue
            if isinstance(obj, (bytearray, type)) or lvl >= depth:
                continue
            for key, child in _children(obj):
                if not isinstance(child, _SKIP):
                    stack.append((child, path + key, lvl + 1))
    seen = None
    return {"total": _registry(), "owners": found,
            "unowned": sorted(want.items())}


def pool():
    """device_canvas's _LAYER_POOL as {nbytes: buffers} and _LENT_BAKES as
    {owner: bytes}, or None where the module is absent."""
    dc = sys.modules.get("device_canvas")
    if dc is None:
        return None
    lp = {n: len(v) for n, v in getattr(dc, "_LAYER_POOL", {}).items()}
    lb = {}
    for owner, lst in getattr(dc, "_LENT_BAKES", {}).items():
        lb[str(owner)] = sum(len(b) for _i, b in lst)
    return {"pool": lp, "lent_bakes": lb}


def _type_names(modules=None):
    """id(type) -> name, for the builtin types and every class a module
    holds: what gc.refs()'s first word is matched against."""
    mods = sys.modules if modules is None else modules
    x = 0

    def _closure():
        return x

    names = {}
    for t in (dict, list, tuple, set, memoryview, bytearray, str, bytes, type,
              type(_closure), type(_type_names), type(sys)):
        names[id(t)] = t.__name__
    try:
        names[id(type(names.get))] = "bound_method"
    except Exception:  # noqa: BLE001
        pass
    for name in mods:
        try:
            items = mods[name].__dict__.items()
        except Exception:  # noqa: BLE001
            continue
        for k, v in items:
            if isinstance(v, type) and id(v) not in names:
                names[id(v)] = "%s.%s" % (name, k)
    return names


# Containers a referrer chain walks through to reach what holds them.
_THROUGH = ("dict", "list", "tuple", "set", "memoryview", "closure", "raw")


def referrers(addr, depth=8, width=3):
    """Who holds the heap or off-heap memory at `addr`: chains of
    (block, bytes, type) from the nearest referrer outward, each ending at an
    instance of a named class, a module, or "none" where no heap block holds
    the next link -- a C static, a stack, or nothing at all. None without
    gc.refs()."""
    refs = getattr(gc, "refs", None)
    if refs is None:
        return None
    names = _type_names()
    out = []
    stack = [(addr, [])]
    seen = set()
    while stack and len(out) < 24:
        a, path = stack.pop()
        if a in seen:
            continue
        seen.add(a)
        rs = [r for r in refs(a) if r[0] not in seen]
        if not rs:
            out.append(path + ["none"])
            continue
        for blk, nb, w0 in rs[:width]:
            kind = names.get(w0, "raw")
            step = (blk, nb, kind)
            if kind in _THROUGH and len(path) < depth:
                stack.append((blk, path + [step]))
            else:
                out.append(path + [step])
    names = None
    return out


# -- live bytes by owner (destructive) ---------------------------------------

CUTS = None

# Cut last, so what the attributes before them share is charged to them.
LATE = ("covers", "launcher", "picker", "wm", "carts", "store", "carts_store",
        "prefs", "system", "look", "layout")


def cut(ws, keep=("mem_census", "gc", "sys", "builtins", "micropython",
                  "uctypes", "esp32", "moy_alloc"), modules=None):
    """Drop every attribute of `ws` (LATE ones last), then every module's
    globals, collecting after each; record (what, freed bytes, used bytes per
    area) into CUTS and return it. The console is dead afterwards.

    A drop frees only what nothing else still reaches. The frame loop's own
    locals (the spine's Desktop, the pump, the dev channel's scope) and the
    bound methods cached on them hold most of the console a second time, so
    an attribute they share is charged to no row and stays in "left"; the
    boot trace armed "live" is what names the live bytes by boot step."""
    global CUTS
    rows = []
    gc.collect()
    base = gc.mem_alloc()
    rows.append(("start", base, _used()))
    d = ws.__dict__
    names = [k for k in d if k not in LATE]
    names.sort()
    names += [k for k in LATE if k in d]
    prev = base
    for k in names:
        try:
            setattr(ws, k, None)
        except Exception:  # noqa: BLE001 -- a property without a setter
            try:
                del d[k]
            except Exception:  # noqa: BLE001
                continue
        gc.collect()
        now = gc.mem_alloc()
        rows.append(("ws." + k, prev - now, _used()))
        print("CUT %s %d" % (rows[-1][0], rows[-1][1]))
        prev = now
    mods = sys.modules if modules is None else modules
    for name in sorted(mods):
        if name in keep:
            continue
        mod = mods.get(name)
        try:
            mod.__dict__.clear()
        except Exception:  # noqa: BLE001 -- a built-in module's table is ROM
            continue
        gc.collect()
        now = gc.mem_alloc()
        rows.append(("mod:" + name, prev - now, _used()))
        print("CUT %s %d" % (rows[-1][0], rows[-1][1]))
        prev = now
    rows.append(("left", prev, _used()))
    CUTS = rows
    return rows


def _used():
    m = _area_map()
    return None if m is None else tuple(a[3] for a in m)
