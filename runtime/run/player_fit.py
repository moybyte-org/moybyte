# Map (grep -n a name to jump there):
#   _nativize             a cart's source with @micropython.native over every top-level def
#   _verdict              a run's cart by the kernel's VM-free rule
#   _exc_cart_line        the cart line a Python exception was raised at
#   _compiled             a compiled ("runtime": "wasm") cart
#   newer_notice          the notice for a cart built for a newer console
#   fit_notice            the notice for a cart too big to load
#   _cart_fit             what a cart's load needs against what the console has; _fits its verdict
#   _out_of_memory        a start that failed for want of memory
#   _lua_cart_where       which of a Lua cart's scripts raised, and its line
"""What the Player decides about a cart before and after it runs, as
functions of the cart and its runtime: whether it fits and whether it was
built for a newer console (the notices it is refused with), whether it is
compiled, the source the native emitter takes, and the cart line an error
names (a Python traceback's, a Lua error text's).

The Player's own module imports these names directly, so a call costs what it
did when they lived there. A leaf over chrome_ops (the panels' words),
widgets' `_err_text`, moy_carts' `cart_sources` and moy_play's census.
MicroPython-safe.
"""

try:
    import chrome_ops as _ch
    from widgets import _err_text
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import chrome_ops as _ch
    from runtime.widgets import _err_text
try:
    import moy_carts as _carts
except ImportError:  # pragma: no cover - host
    from runtime import moy_carts as _carts
try:
    import moy_play as _moy_play
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import moy_play as _moy_play


def _nativize(src):
    """Insert @micropython.native above every top-level def. Returns the new
    source plus the 1-based NEW-source line numbers of the inserted decorator
    lines, so a crash line reported against the rewritten source can be mapped
    back to the kid's original line (#24 stays exact)."""
    out = []
    ins = []
    for line in src.split("\n"):
        if line.startswith("def "):
            out.append("@micropython.native")
            ins.append(len(out))          # the decorator's own (new) line number
        out.append(line)
    return "\n".join(out), ins


def _verdict(cart):
    """(runtime, vm_free, why) of a run's cart by the kernel's VM-free rule
    (moy_play.census over its folder, native/moy_play/moy_play.h): the
    verdict `state` reports for the run in front and the census pins. None for
    a cart with no folder, or an image whose module has no rule."""
    path = cart.get("path") if cart is not None else None
    census = getattr(_moy_play, "census", None)
    if not path or census is None:
        return None
    try:
        return census(path)
    except Exception:  # noqa: BLE001 -- a meter must never block a run
        return None


def _exc_cart_line(exc, fname="<cart>"):
    """Best-effort: the 1-based source line INSIDE the cart where `exc` was
    raised, or None -- so a runtime crash can drop the kid on the offending line
    (#24), like a syntax error does. Both backends rely on the cart being
    compiled with the filename `fname` (see Player.start). Host (CPython) walks the
    traceback objects; the device (MicroPython, which exposes no tb objects)
    parses sys.print_exception's rendered output. The DEEPEST cart frame wins."""
    tb = getattr(exc, "__traceback__", None)
    line = None
    while tb is not None:
        try:
            if tb.tb_frame.f_code.co_filename == fname:
                line = tb.tb_lineno
        except AttributeError:
            pass
        tb = tb.tb_next
    if line is not None:
        return line
    try:
        import sys
        import io
        buf = io.StringIO()
        sys.print_exception(exc, buf)              # MicroPython only
        for ln in buf.getvalue().split("\n"):
            if fname in ln:
                p = ln.find("line ")
                if p >= 0:
                    num = ""
                    for ch in ln[p + 5:]:
                        if "0" <= ch <= "9":
                            num += ch
                        elif num:
                            break
                    if num:
                        line = int(num)            # keep the last (deepest) match
    except Exception:  # noqa: BLE001
        pass
    if line is not None:
        return line
    return getattr(exc, "lineno", None)            # SyntaxError caught at compile


# The cart runtimes a manifest may name besides the console's own Python, and
# how the runtime-missing panel says each. A build carries a runtime when
# `ws.runtimes` maps its name to a factory; an absent key is the panel.
RUNTIME_NAMES = {"lua": "Lua", "wasm": "wasm"}


def _compiled(cart):
    """True for a compiled ("runtime": "wasm") cart: no source to show, no
    line to mark, no EDIT action on its error panel."""
    return (cart or {}).get("runtime") == "wasm"


# A compiled cart bigger than this console can hold (native/moy_wasm/README.md:
# a cart above the floor is allowed, and a board that cannot fit it says so).
# It is refused before it loads, on the runtime-missing panel's mechanism --
# the run never starts and cart_error holds the text -- and the panel is drawn
# as a NOTICE rather than an error: nothing went wrong, the cart is bigger than
# this console. A load that still runs out of memory gets the same notice; the
# engines on every tier begin that failure's text with _OUT_OF_MEMORY.
#
# A compiled cart whose module imports a name this console's import table
# lacks was built for a newer console (moy-spec SPEC.md 16.3), and is refused
# the same way, before it loads, with a notice naming what is missing: never
# a load error, never a trap when the cart first calls the import. Every
# tier's runtime answers `missing(cart)` through one comparison,
# device/moycore_glue.missing_imports.
#
# Both notices' words, and the error panel's, are the kernel's one table
# (native/moy_play/moy_chrome.h's THE PANELS' WORDS, #143): chrome_ops's
# say_title, crash_title, fit_text and newer_text.
_OUT_OF_MEMORY = "out of memory"

# A compiled cart whose module does not run natively on this console -- none
# for this chip and compiled-code format, one with no signature while the
# owner has Unknown sources off, or one whose content is corrupt -- is never
# refused for it (native/moy_wasm/README.md, "A cart survives its
# firmware", 2026-09-30): device/moycore_glue.WasmRun retries it on the
# interpreter before this layer ever sees an error, so the cart plays, only
# slower, and INTERP_NOTICE is the short system NOTICE that says so
# (`ws.notice`, runtime/shell/console_notices.py's `_draw_notice` -- the same body
# "MOYBYTE UPDATED" uses, never `_draw_toast`'s achievement banner, and it
# expires on its own rather than needing a dismissal). A module whose
# SIGNATURE is present and does not verify keeps the ordinary error panel --
# that module was changed after it was signed, which is tamper evidence, not
# staleness, and no switch and no interpreter runs it.
#
# The sub-line says what to do, by cause (`WasmRun.interp_cause`): a cart
# with no module by this console's own name -- none built yet, or one left
# over from a format this console has moved past -- needs a fresh build;
# one whose module matched but carried no signature while Unknown sources is
# off is simply not signed; one whose module calls a helper this firmware's
# runtime does not register is waiting on the CONSOLE, not the cart. Any
# other non-tamper refusal (a corrupt or foreign-chip key despite a matching
# file name) reads the same as "missing": this console's copy is not one it
# can use, whatever the reason.
INTERP_NOTICE_TITLE = "RUNNING SLOWLY"
INTERP_NOTICE_SUB = {
    "missing": "needs an update",
    "unsigned": "isn't signed",
    "firmware": "console needs an update",
}
INTERP_NOTICE_SUB_DEFAULT = INTERP_NOTICE_SUB["missing"]


class _Refused(Exception):
    """A compiled cart refused before it loads: args are the notice and
    the title it is drawn under."""


def newer_notice(missing):
    """The notice for a cart whose module imports `missing`, names this
    console's import table lacks."""
    return _ch.newer_text(missing)


def fit_notice(title, need, have):
    """The notice for a cart whose load needs `need` -- (total, largest
    block) -- where the console has `have` -- (free, largest free block):
    the cart, what it needs and what this console has free. Either figure
    may be None when it could not be read; the notice then says what it can."""
    if need is None or have is None:
        return _ch.fit_text(title, 0, 0, 0, 0)
    return _ch.fit_text(title, need[0], need[1], have[0], have[1])


def _cart_fit(make, cart):
    """(need, have) from a runtime that can say what a cart's load takes
    (`footprint`) and what it can give (`memory`), or None when it cannot:
    a runtime with no such report, a cart with nothing to measure, or a
    report that failed -- the load then answers for itself."""
    fp = getattr(make, "footprint", None)
    mem = getattr(make, "memory", None)
    if fp is None or mem is None:
        return None
    try:
        need = fp(cart)
        if need is None:
            return None
        return need, mem()
    except Exception:  # noqa: BLE001 -- a report is advisory; the load decides
        return None


def _fits(fit):
    """A `_cart_fit` answer's verdict: the total and the largest block each
    within what the console has free."""
    return fit[0][0] <= fit[1][0] and fit[0][1] <= fit[1][1]


def _cart_missing(make, cart):
    """What the cart imports that the runtime's table lacks, from a runtime
    that can say (`missing`), or None: a runtime with no such report, a cart
    that imports nothing missing, or a report that failed -- the load then
    answers for itself."""
    fn = getattr(make, "missing", None)
    if fn is None:
        return None
    try:
        return fn(cart) or None
    except Exception:  # noqa: BLE001 -- a report is advisory; the load decides
        return None


def _out_of_memory(exc):
    """True for a start that failed for want of memory."""
    return isinstance(exc, MemoryError) or _OUT_OF_MEMORY in _err_text(exc)


def _lua_err_text(exc):
    """_err_text minus any appended "stack traceback:" block (#67 Phase 5): the
    panel is the same kid-short one-liner on every backend, and the raise
    position (`cart:N:`) lives in the message head."""
    t = _err_text(exc)
    p = t.find("\nstack traceback:")
    return t if p < 0 else t[:p]


def _lua_cart_at(text, chunk="cart"):
    """`(offset, line)` of the FIRST `chunk:N:` position in a Lua error text,
    or `(None, None)`.

    The OFFSET is what lets a caller holding several chunk names pick the
    earliest rather than whichever name it happened to ask about first --
    see `_lua_cart_where`. No regex: this runs frozen on MicroPython like its
    Python twin above."""
    if not text:
        return None, None
    s = str(text)
    for pat in ('[string "%s"]:' % chunk, chunk + ":"):
        p = s.find(pat)
        while p >= 0:
            prev = s[p - 1] if p > 0 else " "
            # a real position, not a word ending in the chunk name ("restart:")
            if not (prev.isalpha() or prev.isdigit() or prev == "_"):
                num = ""
                for ch in s[p + len(pat):]:
                    if "0" <= ch <= "9":
                        num += ch
                    else:
                        break
                if num:
                    return p, int(num)
            p = s.find(pat, p + 1)
    return None, None


def _lua_cart_line(text, chunk="cart"):
    """Best-effort: the 1-based cart line inside a Lua error text (#67 Phase 5).
    Every tier loads the cart chunk as "@cart" (moycore's load), so a load or raise position renders `cart:12:`;
    a plain-named chunk renders `[string "cart"]:12:` -- both parsed. The FIRST
    position in the text is the raise point (any traceback frames come after
    it), the deepest-frame rule the Python parser applies.

    `_lua_cart_where` is the several-scripts form, and is what the crash paths
    use: this one cannot say WHICH file it read."""
    return _lua_cart_at(text, chunk)[1]


def _lua_cart_where(text, cart):
    """`(file, line)` -- which of the cart's scripts raised, and where in it.

    `lua_ext.cart_chunks` names main's chunk "@cart" and every OTHER script
    after its own file, so a port's error reads `p8.lua:412:` where the cart's
    own code reads `cart:12:`. Asking only about main was right while a cart was
    one file; on a ported cart it finds NOTHING -- no line, no marker, no
    crash-to-code -- for the two thirds of the code that is not main.lua, which
    is exactly where a generated shim raises.

    Earliest position wins whichever file it names: that is the raise point, and
    traceback frames come after it."""
    mainf = (cart or {}).get("main", "main.py")
    at = None
    where = (None, None)
    for name in _carts.cart_sources(cart) or (mainf,):
        pos, line = _lua_cart_at(text, "cart" if name == mainf else name)
        if pos is not None and (at is None or pos < at):
            at, where = pos, (name, line)
    return where
