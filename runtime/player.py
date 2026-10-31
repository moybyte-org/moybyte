# Map (grep -n a name to jump there):
#   _Refused              a compiled cart refused before it loads
#   newer_notice          the notice for a cart built for a newer console
#   fit_notice            the notice for a cart too big to load
#   Player                runs one cart: start, tick every frame, always exit
#   Player.start          start or re-run a cart under make_api
#   Player.frame_plan     what this loop frame is for the cart
#   Player.tick           one frame of the running cart
#   Player.release_world  drop the dead run's world at exit
"""The cart PLAYER -- the run-loop black box (Stage 2 of
docs/history/shell_ux_technical_plan_v1.md).

`Player` is the one object that RUNS a cart: it starts a cart under the frozen
`make_api`, feeds it input, presents its pixels, and guarantees it exits (a crash
becomes an on-canvas error panel, never a hang). Pulling this out of `Workstation`
is the pivotal cut of the shell-UX migration: a cart now runs identically whether
it was launched from the home grid or (Stage 3) from the editor's PLAY -- the
Player has zero knowledge of who launched it.

What the Player owns (moved verbatim from Workstation):
  * `start(project)` -- reset the canvas, stamp the cart clock, gate `wifi` by the
    manifest permission, call the injected `make_api`, exec the source, capture
    `_update`/`_draw`, turn any exception into `cart_error`/`crash_line`.
  * `tick(dt)` -- the per-frame game loop: key-edge derivation (cart_key/cart_keyp),
    `_update(dt)`/`_draw()`/`audio.tick`, the crash capture, then the crash chrome.
    It fills the DRAWBRK perf split
    (ws._pf_upd/_pf_cart/_pf_audio/_pf_bg) -- that contract stays on `ws`.
  * `handle_input`/`handle_pointer` -- a running cart owns every key and tap. The
    console's key (`start`, Super) is read by the router before this layer and
    opens the pause menu (`pause`/`resume` below); a cart ends itself with `quit()`,
    and a tool/app exits through its bar's context-X.
  * a stopped run's notice (`notice`, `newer`), which the console's stop card
    shows (`console_notices.PauseMenu.stop`).

The BUNDLE the Player reaches (spec Section 2): the open `Project` (its live data),
plus the RAW canvas / input / audio / `make_api` / wifi through its `ws` back-ref --
the SAME raw objects `Workstation._start` handed `make_api` before, so the hot draw
path stays injected-direct (the Section 5 perf guardrail: WHO calls make_api moved,
WHAT it closes over did not). It deliberately does NOT reach the store, the shell
top bar, the home grid, or the layouts -- the stop card it opens is reached
through thin `ws` helpers so this file never names them (a spike-test grep enforces
the isolation). The cart-run fields it owns are exposed back on Workstation as
forwarding properties, so every surface file + test is unchanged.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree
so the device freezes it (same pattern as console.py/project.py). It stays a leaf --
the tiny `_ticks_*`/`_wrap` helpers are defined here, `_err_text`/`_in` come from the
widgets leaf and NAMES is injected -- so it imports nothing back into console (no
circular import). The one cross-module value it needs
is the code line-height for the crash panel, imported from the code editor leaf
(bare name on the device / once host_app has aliased it, `runtime.X` for a direct
test load).
"""

try:
    import chrome_ops as _ch
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import chrome_ops as _ch

# #65 Phase 2: the lockstep frame's local-input packer. The BUTTON ORDER comes
# from cart_api (its one author) and is handed to the session.
try:
    from players import mask_of as _netplay_mask
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.players import mask_of as _netplay_mask
try:
    from cart_api import CART_BUTTONS as _NET_BUTTONS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cart_api import CART_BUTTONS as _NET_BUTTONS
try:
    from moy_play import Tick as TickScheduler     # the kernel's tick model
    import moy_play as _moy_play
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_play import Tick as TickScheduler
    from runtime import moy_play as _moy_play


# Auto-native carts (#67 spike): when the runtime HAS the native code emitter
# (MicroPython on device / unix; never host CPython), every top-level def in a
# cart is compiled to machine code at load -- the kid writes ordinary Python,
# the loader applies @micropython.native. Measured on the unix build: -5..-15%
# cart logic with BYTE-IDENTICAL pixels across all seed carts (the emitter
# preserves full Python semantics); the S3 delta is expected larger (its
# per-call dispatch cost dominates kid loops -- see the CALIB line). Guarded:
# if the emitter refuses a cart (compile-time), the pristine bytecode path
# runs instead -- a cart can never break because of this flag.
try:
    import micropython as _micropython
    # The decorator is a COMPILER construct (recognized syntactically, stripped
    # from the runtime module), so probing an attribute can't detect it -- compile
    # a one-liner through the emitter instead. Raises on a build without it.
    exec(compile("@micropython.native\ndef _t():\n    pass", "<probe>", "exec"),
         {"micropython": _micropython})
    NATIVE_CARTS = True
except Exception:  # noqa: BLE001 -- host CPython / emitter-less build: bytecode only
    _micropython = None
    NATIVE_CARTS = False


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


# Deferred pmem persistence (#66): how often a DIRTY pmem persists mid-play.
# The guaranteed saves are cart exit + crash; this only bounds progress lost to
# a power pull. Each flush is a ~80-130ms SD write on the T-Deck (measured on
# glass 2026-07-14, the Letter Blitz "word-event spike"), so the cadence IS the
# hitch cadence -- keep it a minute, not seconds.
PMEM_FLUSH_MS = 60000

# The cart TYPES that run with the shell's minimal exitable bar rather than
# owning the whole raster, and the one of them with no folder. `console.py`
# imports both -- the two files decided this in parallel with two literal
# tuples until scripts made it three types, and a bar the Player draws but the
# shell does not route taps to is a cart nobody can leave.
SCRIPT_TYPE = "script"
BAR_TYPES = ("tool", "app", SCRIPT_TYPE)

# The optional features this build implements (moy SPEC.md 10). A cart whose
# manifest "extensions" lists anything else is REFUSED at start -- the clean
# §10 decline, never a mid-frame crash on a missing verb.
#
# The first two are BACK-COMPAT names, not features this gate decides. Upstream
# b9dbba1 (vendored 2026-08-19) made `make_layer`/`draw_layer`/`background` and
# `view` CORE -- SPEC.md 6, installed unconditionally, because a verb that
# degrades truthfully cannot be an extension. They stay listed here so a
# manifest written against the older spec still starts: declaring them is now a
# no-op, and REMOVING them from this tuple would refuse carts that already ship
# with the declaration. The rest are moybyte's own,
# namespaced `vendor.feature` exactly as §10 requires so they can never collide
# with a future standard name -- a cart declaring one is non-portable by
# construction, which is the honest trade its author is making. Before this
# list existed the gate refused every namespaced name, so a cart that truthfully
# declared what it used was rejected by the one console that implements it.
SUPPORTED_EXTENSIONS = (
    "layers",             # CORE now (SPEC.md 6): make_layer/draw_layer/background
    "viewport",           # CORE now (SPEC.md 6): view(w, h)
    "moybyte.scenes",     # #85/#109: scene/load_scene + the actor world
    "moybyte.images",     # #63: image(name) / Image paint-image assets
    "moybyte.net",        # #65: net.send / on_net (also permission-gated)
    "moybyte.wifi",       # #38: the injected wifi service (permission-gated)
    "moybyte.app",        # #224: a compiled app's role imports (native/moy_app/moy_app_wasm.h)
    "moybyte.ui",         # the public UI API, a Lua cart's `ui` (native/moycore/moycore_ui.h)
)
APP_EXT = "moybyte.app"


try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_us, _ticks_diff, _ticks_add
    from widgets import _err_text, _in
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_us, _ticks_diff, _ticks_add
    from runtime.widgets import _err_text, _in

# USER APPS (#181, ui_refactor_2026-08 Phase 7): the permission-keyed filter over
# AppContext, plus the responsive opt-in probe. A leaf like the rest of what this
# file imports -- it holds no Workstation, only the context FACTORY it is handed.
try:
    from ui_draw import cart_text as _cart_text      # the app-cart boundary (§4.7)
    from ui_draw import (ui_owes as _ui_owes, ui_moving as _ui_moving, UI_IN_LEN as _UI_IN_LEN, _P_MIDDLE, _PB_MIDDLE)
    from moy_input import pointer_state as _pointer_state
    import layout_base as _layout_base
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ui_draw import cart_text as _cart_text
    from runtime.ui_draw import (ui_owes as _ui_owes, ui_moving as _ui_moving, UI_IN_LEN as _UI_IN_LEN, _P_MIDDLE, _PB_MIDDLE)
    from runtime.moy_input import pointer_state as _pointer_state
    from runtime import layout_base as _layout_base
try:
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host
    from runtime import ui_draw as _ui_draw
from array import array as _array
_UI_MASKS = [0, 0]                      # reused: the frame's button masks
_P_LIVE = 1                             # moy_ui_api.h's MOY_UI_P_LIVE: a pointer over the page
_UI_POINTER = [0, 0, 0, 0]              # reused: moy_input.pointer_state's answer
try:
    from system_api import draws_in_look, make_system_api, wants_layout
except ImportError:                     # host: the runtime package
    from runtime.system_api import draws_in_look, make_system_api, wants_layout
import moy_app as _moy_app              # the grant policy (native/moy_app)
try:
    from audio_session import native as _audio_native
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio_session import native as _audio_native

# The cart-facing text editor (docs/text_editing_2026-09.md step 3). Imported
# here rather than reached through the shell for the same reason as the module
# above: it is a leaf over the Files role and the cart's canvas.
try:
    import moy_carts as _carts
except ImportError:  # pragma: no cover - host
    from runtime import moy_carts as _carts
try:
    from editor_handle import EditorHandle
except ImportError:                     # host: the runtime package
    from runtime.editor_handle import EditorHandle
# The kernel's frame loop (native/moy_kernel), on a tier whose frames are the
# kernel's: the Player tells it the cadence a paced game runs at.
try:
    import moy_loop as _loop
except ImportError:                     # host CPython: the host drives its frames
    _loop = None


def _verdict(cart):
    """(runtime, vm_free, why) of a run's cart by the kernel's VM-free rule
    (moy_play.census over its folder, docs/kernel_cartpath_2026-10.md §2): the
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


def _safe_len(obj):
    try:
        return len(obj)
    except Exception:  # noqa: BLE001
        return -1


def _heap_stats():
    """Return (free, alloc) for MicroPython, (-1, -1) on host/unsupported builds."""
    try:
        import gc
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        return free, alloc
    except Exception:  # noqa: BLE001
        return -1, -1


def _moycore_sram():
    """#211: `{sram_free_min, psram_fallback, floor}` for the Lua run in the
    allocator's meters, or None where the concept does not apply.

    None is the honest answer on a tier whose Lua allocator has one region to
    choose from -- the host, the wasm head, a board with no PSRAM -- and it is
    NOT the same answer as a zero: a run that fitted with nothing to spare and a
    run that could never have been squeezed must stay distinguishable. Same
    rule as the pump/fold meters in dev_channel.

    `sram_free_min` is itself None until the run's Lua allocates something big
    enough to reach the floor test, which is where moycore samples it.
    """
    try:
        import moycore
    except ImportError:
        return None
    fn = getattr(moycore, "sram_report", None)
    if fn is None:
        return None
    try:
        r = fn()
    except Exception:  # noqa: BLE001 -- a meter never blocks an exit
        return None
    if not r:
        return None
    return {"sram_free_min": r[0], "psram_fallback": bool(r[1]), "floor": r[2]}


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


# A compiled cart bigger than this console can hold (docs/wasm_tier_plan_2026-09.md:
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
# refused for it (docs/wasm_tier_plan_2026-09.md, "A cart survives its
# firmware", 2026-09-30): device/moycore_glue.WasmRun retries it on the
# interpreter before this layer ever sees an error, so the cart plays, only
# slower, and INTERP_NOTICE is the short system NOTICE that says so
# (`ws.notice`, runtime/console_notices.py's `_draw_notice` -- the same body
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


# Compiled-code cache (#66 exec-arena fix, see Player.start): one entry per
# cart, keyed by the source's (len, hash) -- so the native emitter runs once
# per source VERSION, not once per PLAY. Module-level: survives Player runs,
# dies with the VM (exactly the arena's own lifetime).
_CODE_CACHE = {}


# A `"fps": "free"` game's longest single tick, seconds: two 30Hz periods and a
# bit. Longer gaps are stalls, and a stall slows time rather than jumping it
# (the tick model's rule for paced carts, kept for the unpaced ones).
FREE_DT_MAX = 0.1

_SIG_FPS = 2                            # the WM's overlay sig bit of the FPS chip


class Player:
    """Runs one cart: start -> tick every frame -> guarantee exit (Stage 2). Holds a
    `ws` back-ref (the shared draw toolkit + services seam every surface uses) and is
    injected NAMES like the other surfaces. The cart-run fields it owns
    (ns/_update/_draw/cart_error/crash_line/_cart_start_ms/_cart_key_prev) are exposed
    back on Workstation as forwarding properties, so every reader of ws.cart_error/
    ws._update/... is byte-for-byte unchanged; they are reset per run in start()."""

    _play = None                      # the run's frame in C: _bind_play
    stack_pre = None                  # the task's stack mark before the open: start

    def __init__(self, ws, NAMES):
        self.ws = ws
        self.NAMES = NAMES
        self.ns = None
        self._update = None
        self._draw = None
        self.cart_error = None        # last cart failure text -> on-canvas error panel
        self._notice = None           # a notice's text: the panel is a notice
                                      # while cart_error still holds it (`notice`)
        self._notice_title = None  # ...and the title it is drawn under (None: the fit's)
        self.crash_line = None        # 1-based cart line of the last runtime crash (#24)
        self.crash_file = None        # WHICH of the cart's scripts that line is in
                                      # (SPEC.md 4), or None for main/no crash
        self._cart_start_ms = 0       # _ticks_ms when the running cart last start()ed
        self.verdict = None           # the run's (runtime, vm_free, why): _verdict
        self._front = False           # the run is the kernel loop's foreground
        self._stopping = False        # the kernel stops the VM for the run
        self._paused_at = 0           # _ticks_ms the pause menu held the run at (0: not held)
        self._audio_held = 0          # the session the pause took the mix from
        self._cart_palette_canvas = None  # the canvas _cart_palette came off
        self._cart_key_prev = 0       # last frame's keyboard byte (key()/keyp() edge)
        self._cart_palette = None     # default table saved while a cart's own
                                      # manifest palette (spec 2.2) is applied
        # #75: the tool/app-vs-game decision is a per-run CONSTANT (the manifest type
        # can't change mid-run), cached at start() so the per-frame tick/input paths
        # don't re-derive it through ws._running_cart_shows_bar (a property-forward
        # chain) on the sacred play path. Combined with the live cart_error check at
        # each use site it answers exactly what _running_cart_shows_bar answers.
        self._is_tool = False
        self._script = False          # a `type: "script"` run: the shell's text console
                                      # is its screen (docs/text_editing_2026-09.md)
        self._free = False            # a `"fps": "free"` game: unpaced, real dt (SPEC 5)
        # USER APP state (#181), all per-run and all reset in start():
        self._app_layout = None       # the cart's `_layout(w, h, fs)`, when it opted into
                                      # the RESPONSIVE canvas -- None for every game and
                                      # every fixed-canvas app (the default)
        self._app_wh = None           # the (w, h, fs) _layout was last told about, so a
                                      # window resize / font-scale change re-runs it and a
                                      # steady frame costs one tuple compare
        self._app_id = None           # the crash guard's key for this run (#160), or None
        self._look = False            # a built-in app: its code draws in the look, outside
                                      # the app-cart boundary (system_api.draws_in_look)
        self._ui = None               # a Python app's frame of ui verbs: [context, input, canvas]
        self._on_change = False       # an app on the verbs: drawn on change (animates())
        self._masks = None            # ws.input.button_masks, bound for an app's run
        self._owe = 0                 # drawn frames it still owes after the last change
        self._run_handle = 0          # the kernel Player's handle on this run, or 0
        self._app_ctx = None          # a user app's or script's context: its run grant
                                      # when the run is not guarded
        self._restore_bg = None       # #63: the api's declared-background restore hook
        self._flag_put = None         # flag_edits' applier for the running cart
        self._lua = None              # #67: the running runtime cart's state -- the
                                      # handle a ws.runtimes factory returned, Lua or
                                      # wasm; _close_lua() on exit so the cart's whole
                                      # heap dies with its run
        self._play = None             # the run's frame in C (moy_play), or None: _bind_play
        self._lua_split = None        # its frame_split, bound ONCE per run: _run_ticks
                                      # asks after every tick
        self._sram_run = None         # #211: the Lua allocator's headroom report for the
                                      # run that ENDED, kept until the next run starts
        self._net = None              # #65: the running cart's net.* service, when it
                                      # has the "multiplayer" permission (else None); tick()
                                      # pumps inbound messages to its on_net handler
        self._netplay = None          # #65 Phase 2: the live two-console lockstep session
        self._drain_input = None      # pre-tick radio drain, bound by start()
                                      # (ws.netplay), when this run is a linked match. It
                                      # OWNS the tick: fixed dt, and a frame the peer's
                                      # input has not reached does not simulate at all
        self._pmem_last = 0           # #66 deferred pmem: last periodic flush (_ticks_ms)
        self._native_ins = None       # #67 spike: nativize's inserted-line map (crash-line fix)
        # Diagnostics for repeat-run regressions (#66 follow-up): one line on cart
        # start and rate-limited slow-logic lines while PERF DIAG is on. Kept here
        # instead of moy_runtime so it tracks the actual lifecycle without touching
        # the device loop.
        self._run_seq = 0
        self._start_diag = None       # (reclaim,audio,api,compile,exec,init,total,free0,free1,alloc0,alloc1)
        self._slow_logic_next = 0
        self._native_fail = None      # reason for bytecode fallback, when auto-native fails
        # The tick model (#217): one scheduler per run. `tick_ms` is the cart's
        # tick period while a GAME is paced and 0 otherwise -- a flat attribute,
        # told to the kernel's loop (moy_loop.tick) where it paces frames.
        self.sched = TickScheduler()
        self._tick_dt = 1.0 / 30
        self.tick_ms = 0
        self._n_ticks = 1             # frame_plan's answer, run by tick()
        self._keyp_latch = 0          # a keyp edge waiting for a logic tick
        self._tick_edges = None       # ws.input.tick_edges, bound while paced
        self._keep_edges = None       # ws.input.keep_edges, bound while paced
        # The editor handles this run opened (#181/#112). A per-run list, like
        # the Lua tier's layer pins: they die with the world, flushed HARD on
        # the way out so a kid who taps X never loses a note (#154).
        self._editors = []
        self._focus_editor = None     # the handle holding the keyboard, if any
        # An app on the verbs draws through its app host (`_ui_begin`):
        # whether its pass is drawing now, whether a clear broke its floor,
        # and what drew over its page after the pass (the FPS chip).
        self._uh = None
        self._pass = False
        self._broke = False
        self._over = _array("i", [0] * 4)
        # What the painted frame `change_f` changed of the run's canvas, as
        # its pass's plan said (`AppHost.change_box`): x0, y0, x1, y1, then
        # the region R it drew no more than that inside (what lies outside R
        # it drew whole), and 1 where known -- which the window's composite
        # reads.
        self.change = _array("i", [0] * 9)
        self.change_f = -1
        self._cls = None
        self._bg = None
        self._bg_on = False

    # -- the cart-facing text editor (#181/#112) ----------------------------

    def _open_cart_editor(self, files, kind, name, mode, canvas, clip=None):
        """Build one editor handle and PIN it to this run.

        Handed to `make_system_api` as its `editor` factory, so a cart reaches
        it only through the `open_editor` its manifest earned."""
        ed = EditorHandle(files, kind, name, mode, canvas, self._theme_colors,
                          clip=clip, host=self.ws, frame=self)
        self._editors.append(ed)
        return ed

    def _theme_colors(self):
        # The flat kernel token dict every shell surface reads per draw.
        return self.ws.theme_colors

    def set_editor_focus(self, handle):
        """The handle the keyboard belongs to, or None. Written by
        `Workstation.cart_editor_focus`, which also flips the T-Deck. The one
        it replaces is blurred here, so two handles can never both believe
        they are being typed into; the keys let go inside a frame are the
        page's again in that frame (`api_keys_back`)."""
        prev = self._focus_editor
        if prev is not None and prev is not handle:
            prev._blur()
        self._focus_editor = handle
        if handle is None and prev is not None and self._pass and self._ui is not None:
            # Let go inside the frame (the cart's update closed it): the page
            # this frame draws has the keys back, so its focus marks now.
            _ui_draw._C.api_keys_back(self._ui[0])

    def focused_editor(self):
        """The handle holding the keyboard, or None -- what `ws.nav` asks
        before it spends a trackball roll on the cursor."""
        return self._focus_editor

    def seed_cart_key(self, code):
        """Swallow the byte that took the keyboard, on the CART tier.

        `b2ff7de`'s rule, one rung down: handing the keyboard to a text surface
        is a screen change, and the key that caused it is still in `last_key`
        when the surface's first frame reads it. The shell's own text surfaces
        are seeded in `_set_text_mode`; this is the same call for a cart's
        editor handle, and it is a SEED and not a mute -- releasing the key and
        pressing it again still types."""
        self._cart_key_prev = code or 0
        self._keyp_latch = 0

    def _keep_grant(self, ctx):
        """Hold the context `make_system_api` built for this run: its grant
        is the run's, ended with it."""
        self._end_grant()
        self._app_ctx = ctx

    def _end_grant(self):
        """The run is over: its grant ends, and so does any store session it
        left open (a session holds no gate; runtime/shell_servers.py)."""
        ctx, self._app_ctx = self._app_ctx, None
        if ctx is not None:
            self._wasm_bind(None)
            ctx.end()
        self.ws.store.end_all()

    def _heal_front_run(self):
        """A user app whose frames the kernel drew in its front (a compiled
        app on a board: no Python tick, so no `app_guard.frame`) heals at its
        end when the kernel's run drew the guard's frames and raised nothing,
        as a Python-ticked run heals during them."""
        guard = self.ws.app_guard
        info_of = getattr(_moy_play, "info", None)
        run, self._run_handle = self._run_handle, 0
        try:
            info = info_of(run) if info_of is not None and run else None
        except Exception:  # noqa: BLE001 -- no run, no heal
            info = None
        if info is not None and info[3] >= guard.HEAL_FRAMES and info[7] is None:
            guard.heal()

    def _wasm_bind(self, cart):
        """The run a compiled app's imports serve (native/moy_app/
        moy_app_wasm.h): this run's grant and the roles its permissions name,
        for a compiled user app that declares the app extension; none
        otherwise, so its module's imports from it are refused at load."""
        bind = getattr(getattr(self.ws, "app_abi", None), "wasm_bind", None)
        if bind is None:
            return
        ctx = self._app_ctx
        if (cart is None or ctx is None or cart.get("runtime") != "wasm"
                or APP_EXT not in (cart.get("extensions") or ())):
            bind(0, ())
            return
        bind(ctx.grant, _moy_app.policy(cart.get("permissions"))[0])

    def _release_editors(self):
        """Hard-flush and drop every handle this run opened."""
        eds = self._editors
        self._focus_editor = None
        if not eds:
            return
        self._editors = []
        for ed in eds:
            try:
                ed.close()
            except Exception:  # noqa: BLE001 -- teardown must never raise
                pass

    def _layout_args(self):
        """`(w, h, fs)` for a responsive app cart's `_layout` (#181).

        `w`/`h` come off the canvas the cart actually draws on -- the system
        surface when `bind_app_canvas` took, the fixed game raster otherwise --
        so a cart whose `_layout` was found but whose bind declined (the
        shared-canvas T-Deck, or a cart that also declared a small SPEC canvas)
        is told the truth rather than a size it cannot reach. `fs` is the shell's
        effective font scale only where the app owns the system surface; on the
        fixed game raster the cart draws its own pixels at 1x and the shell's
        text size is none of its business."""
        ws = self.ws
        cv = ws.canvas
        w, h = ws.app_window_size() if ws.app_window_canvas else (cv.w, cv.h)
        return (w, h, ws.look.effective_font_scale() if ws.app_full_canvas else 1)

    def _layout_moved(self):
        """Whether `_layout_args` moved from what `_layout` was told, told
        into `_app_wh` when it did -- read field by field, so a steady frame
        builds no tuple."""
        ws = self.ws
        if ws.app_window_canvas:
            wh = ws.app_window_size()
            w = wh[0]
            h = wh[1]
        else:
            cv = ws.canvas
            w = cv.w
            h = cv.h
        fs = ws.look.effective_font_scale() if ws.app_full_canvas else 1
        a = self._app_wh
        if a is not None and a[0] == w and a[1] == h and a[2] == fs:
            return False
        self._app_wh = (w, h, fs)
        return True

    def _map_crash_line(self, line):
        """Map a crash line reported against the NATIVIZED source back to the
        kid's original line (#67 spike / #24): subtract the @micropython.native
        decorator lines inserted at-or-before it. Identity when the cart ran the
        pristine bytecode path."""
        ins = self._native_ins
        if line is None or not ins:
            return line
        n = 0
        for pos in ins:
            if pos <= line:
                n += 1
            else:
                break
        return line - n

    def _restore_palette(self):
        """Put back the default palette table a cart-supplied one (spec 2.2)
        displaced. Idempotent; called from every exit path AND at start (so a
        re-run never saves a cart table as 'the default')."""
        if self._cart_palette is not None:
            # Onto the canvas that was SWAPPED, not whatever ws.canvas is now:
            # a load failure releases the run canvas before this runs, and a
            # restore aimed at the system canvas left the native kernels' gate
            # table (owned by the run canvas) on the cart's palette -- every
            # native cart after a failed p8 load drew in PICO-8 colours.
            cv = self._cart_palette_canvas or self.ws.canvas
            try:
                cv.palette = self._cart_palette
            except Exception:  # noqa: BLE001 -- restore must never block an exit
                pass
            self._cart_palette = None
            self._cart_palette_canvas = None

    def _xor_play_flag(self, n, mask):
        """A Python run's half of `ProjectStore.flag_edits`."""
        self.ws.project.play_flags[n] ^= mask

    def _close_lua(self):
        """Tear down the previous run's Lua state (#67), if any. Idempotent and
        exception-proof: a close() failure must never block the next start or
        the exit path -- the state is unreachable either way and gc finishes it."""
        lua = self._lua
        self._lua = None
        self._lua_split = None
        self._play = None
        if lua is not None:
            try:
                lua.close()
            except Exception:  # noqa: BLE001
                pass
            # #211: the allocator's meters outlive the VM (moycore resets them
            # at run_begin, not at close), so the run's headroom is still
            # readable here -- which is what lets the exit report run AFTER the
            # heap it describes is gone.
            self._sram_run = _moycore_sram()

    def release_radio(self):
        """The run's radio leases go: the link's (its radio stopped) and the
        run's own. A run's teardown calls it, and so does a RETURN start
        (docs/kernel_cartpath_2026-10.md section 5.4), whose run ended in the
        kernel with no VM to run this teardown: the leases are the kernel's
        table, which the stop keeps."""
        # Nobody to talk to, and power save back on. UNLESS a session is already arranged for the run about to
        # start -- which is not a hypothetical: the way a match forms is that the
        # peer's invite arrives while this console is already playing, sets
        # ws.netplay, and re-runs the cart from frame zero. The dying run tears
        # down here on the way through, and stopping the radio then killed the
        # very session that triggered the restart. On glass that read as the host
        # stalling at frame 0 forever while the guest played on alone, with
        # nothing logged anywhere (#65). release_world has already
        # cleared ws.netplay if the session belonged to THIS run, so what
        # survives here belongs to the next one.
        _link = getattr(self.ws, "link", None)
        if _link is not None and self.ws.netplay is None:
            try:
                _link.stop()
            except Exception:  # noqa: BLE001 -- teardown must never block an exit
                pass
            self.ws.wifi_release("link")
        # ...and the run's own lease. Nothing else holds the WiFi at a plain
        # exit, so this is where a console goes back to radio-off.
        self.ws.wifi_release("cart")

    def release_world(self):
        """Drop the dead run's WORLD at EXIT, not at the next start (#66 the
        repeat-run fragmentation fix, glass-fingerprinted 2026-07-10): the ns
        used to linger until the NEXT cart's start() -- go_home() nulled ws.ns
        but never _update/_draw, whose function objects close over the ns dict
        and keep the whole ~300-400KB world alive at the home screen. The next
        cart then BUILT ITS WORLD AROUND the lingering one; when that finally
        freed, the new world sat in a minefield of holes, and MicroPython's gc
        never compacts -- measured: sakura logic 6.5ms fresh vs 13-14ms after
        three other carts, with an IDENTICAL live set. Clearing the ns dict IN
        PLACE breaks the globals for every retained function ref; the layer
        reclaim + collect leave a compact heap for the next build. Idempotent;
        safe on the crash path (the error panel reads cart_error, never ns)."""
        self._front = False
        self._paused_at = 0
        self._audio_held = 0
        # Deferred pmem (#66): the dying run's last save, BEFORE the world drops
        # (ws.project still points at this run's Project here -- both exit call
        # sites run release_world ahead of replacing/slimming it).
        pm = getattr(self.ws, "pmem", None)
        if pm is not None:
            try:
                pm.flush()
            except Exception:  # noqa: BLE001
                pass
        # The dead run's `view(w, h)` must not outlive it: every fullscreen
        # surface shares viewport(), and a lingering view would crop chrome.
        self.ws.input.game_view = None
        # Nor its per-run cart canvas (SPEC.md 1/3.1) -- whoever ran us draws
        # on the boot raster, so the small canvas dies with the run. (Also the
        # RESPONSIVE app-cart bind, #181: same field, same release.)
        self.ws.release_run_canvas()
        # A dirty EDITOR HANDLE is written HERE and nowhere else (#154): this
        # is the one place every exit path passes -- the tap on X,
        # quit(), a workspace swap, a crash -- so a kid's note is
        # saved by leaving, exactly as a code tab's half-typed Python is.
        self._release_editors()
        # A USER APP's crash-guard arming dies with its run. Any STRIKE it took
        # stands -- an exit before the heal is precisely the evidence kept.
        self._app_layout = None
        self._app_wh = None
        self._ui = None
        self._uh = None
        self._on_change = False
        self._end_grant()
        if self._app_id is not None:
            self._app_id = None
            self._heal_front_run()
            self.ws.app_guard.release()
        # Nor may its SOUND. The device mixer is a global the cart only ever
        # posts notes to -- libmoy keeps sequencing a looping sfx or music track
        # long after the run that started it is gone, so a demo cart's tones
        # and celeste's music went on playing over whatever the kid did next
        # (owner, T-Deck). Same category as the view and the palette above:
        # state the run set on a shared surface, cleared where the run ends
        # rather than wherever the next one happens to overwrite it.
        au = getattr(self.ws, "audio", None)
        if au is not None:
            try:
                au.music_stop()
                au.sound_stop()
            except Exception:  # noqa: BLE001 -- teardown must not raise
                pass
        # A cart-supplied palette (spec 2.2) dies with its run -- the system
        # surfaces underneath get the default table back.
        self._restore_palette()
        ns = self.ns
        # Was there a world to drop? The collect below and the exit diags are
        # for the heap a RUN leaves behind; go_home reaches here from the desk
        # and the Editor too, where nothing ran and a collect is pure pause --
        # 440ms of the PLAY tap on a Guition P4 (2026-09-09).
        had_world = bool(ns) or self._update is not None \
            or self._draw is not None or getattr(self, "_lua", None) is not None
        if ns:
            try:
                ns.clear()
            except Exception:  # noqa: BLE001
                pass
        self.ns = None
        self._update = None
        self._draw = None
        self._restore_bg = None
        self._pass = False
        # #65: drop the net handler/inbox with the dead run so a stale on_net
        # callback (closing over the cleared ns) can't fire into the next run.
        if self._net is not None:
            try:
                self._net.reset()
            except Exception:  # noqa: BLE001 -- reset must never block an exit
                pass
        self._net = None
        # The match dies with the run: drop the session's player slots so the
        # console is back to one local player the moment the cart is gone.
        if self._netplay is not None:
            try:
                self._netplay.close()
            except Exception:  # noqa: BLE001 -- teardown must never block an exit
                pass
            self._netplay = None
            self.ws.netplay = None
        try:
            self.ws.input.netplay_live = False
        except Exception:  # noqa: BLE001
            pass
        # ...and the radio goes down with it.
        self.release_radio()
        self._close_lua()          # #67: the dead run's Lua heap goes with its world
        ws = self.ws
        rl = getattr(ws.canvas, "reclaim_layers", None)
        if rl is not None:
            try:
                # pool the dead run's layer buffers now. The owner arg was
                # missing until 2026-08-04: reclaim_layers(owner) raised a
                # TypeError this except swallowed, so the release_world
                # reclaim was silently dead and only the NEXT start() pooled
                # (found by the #186 audit).
                rl("cart")
            except Exception:  # noqa: BLE001
                pass
        if had_world:
            try:
                import gc
                gc.collect()       # off the play path: the run just ended
            except Exception:  # noqa: BLE001
                pass
            self._diag_frag()      # #66: the heap the NEXT cart inherits
            self._diag_sram()      # #211: the internal SRAM the run just had
        self._reset_stage_meters()  # #210: the shell does not inherit the run's
        self._disarm_pacing()

    def _reset_stage_meters(self):
        """#210: the frame loop's per-stage deadline meters start clean at both
        ends of a run. A run's misses must not carry the shell's and the
        shell's must not carry the run's -- and these are also the two moments
        the pacing slot the budgets are cut from changes, because frame_slot_ms
        follows the open cart's tick."""
        if _loop is not None:
            _loop.meters_reset()

    def sram_report(self):
        """#211: the internal-SRAM headroom the cart RUN had, or None.

        Live while a Lua run is open, the ended run's once it is gone, and None
        for a Python cart (no such allocator) or a tier with one region. The dev
        channel's `state` is the route -- it is the one every board serves.
        """
        if self._lua is not None:
            return _moycore_sram()
        return self._sram_run

    def _diag_sram(self):
        """One line per Lua cart exit: what the run had, and whether it tipped.

        `fallback=1` is the whole point -- a cart that outgrows the floor does
        not fail and does not warn, it starts allocating from PSRAM and gets
        about twice as slow (#67), which reads as "the cart got slower" with no
        cause attached. Nothing is printed for a run with no such allocator,
        because a line of `none`s at every Python cart's exit is not a report.
        """
        if not self._diag_enabled():
            return
        try:
            r = self._sram_run
            if not r:
                return
            lo = r["sram_free_min"]
            print("Moybyte %d SRAM exit=%d min=%s fallback=%d floor=%d"
                  % (_ticks_ms(), self._run_seq,
                     "none" if lo is None else lo,
                     1 if r["psram_fallback"] else 0, r["floor"]))
        except Exception:  # noqa: BLE001 -- a diag never blocks an exit
            pass

    def _diag_frag(self, tag="MEMX"):
        """One line per cart exit: the largest allocatable block and total free
        on the heap the next cart inherits -- #66's repeat-run fragmentation as
        a number instead of a feel. MicroPython's GC does not compact, so freed
        bytes pile into holes and the largest CONTIGUOUS run -- what a layer or
        the p8 machine's 64KB must fit -- shrinks over a session even while
        total free does not. Emitted after release_world's collect, so it reads
        the compact-as-it-gets state, and carries `exit=` (the run counter) so
        the series over repeated opens IS the degradation curve.

        The block is PROBED, which #66 line 1125 prescribes for a reason:
        `mem_free()` reads high on these ports (it folds in a potential PSRAM
        split), so only a probe that actually allocates is honest. Probed by
        DOUBLING from 16KB up to the 8MB cap, stopping at the first refusal:
        the answer is a x2 bucket, which is what the line is read for (does
        the p8 machine's 64KB still fit, does a layer) -- and at most ONE
        probe fails. That matters because a failing probe IS a collect (the
        allocator collects and retries before it raises): the binary search
        this replaced failed about twelve times per exit, 5.1 seconds of MEMX
        on a Guition P4 whose collect costs 430ms (2026-09-09). Runs after
        release_world's own collect, so it takes none of its own. Off unless
        measurement mode is on, like every line here."""
        if not self._diag_enabled():
            return
        try:
            import gc
            free = gc.mem_free()
            cap = min(free if free > 0 else (1 << 20), 8 << 20)
            big = 0
            n = 16 * 1024
            while n <= cap:
                try:
                    bytearray(n)            # unnamed: collectable at once
                except MemoryError:
                    break
                big = n
                n *= 2
            # The internal-SRAM pool the p8 machine, the flush bounce and the
            # framebuffers compete in -- regions under 1MB. Device only; -1 off it.
            int_free = int_big = -1
            try:
                import esp32
                int_free = int_big = 0
                for reg in esp32.idf_heap_info(esp32.HEAP_DATA):
                    if reg[0] < 1024 * 1024:
                        int_free += reg[1]
                        if reg[2] > int_big:
                            int_big = reg[2]
            except Exception:  # noqa: BLE001 -- host / no esp32: leave -1
                pass
            print("Moybyte %d %s exit=%d free=%dk big=%dk int=%dk/%dk"
                  % (_ticks_ms(), tag, self._run_seq, free // 1024, big // 1024,
                     int_free // 1024, int_big // 1024))
        except Exception:  # noqa: BLE001 -- a diag never blocks an exit
            pass

    def _diag_enabled(self):
        """Only emit the extra lifecycle/profiling lines in measurement mode."""
        try:
            return bool(getattr(self.ws, "diag_live", False) or getattr(self.ws, "perf_hud", False))
        except Exception:  # noqa: BLE001
            return False

    def _audio_diag(self):
        """The kernel's audio as the run found it: the output's state, the
        focused session's voices, the mix's verb count."""
        try:
            from audio_session import native
        except ImportError:
            from runtime.audio_session import native
        try:
            na = native()
            if na is None:
                return "audio=none"
            st = na.stats()
            return ("audio(out=%d mask=%d verbs=%d under=%d)"
                    % (na.out()[0], na.active(), st[3], st[2]))
        except Exception:  # noqa: BLE001
            return "audio=err"

    def _cart_state_diag(self):
        """Small generic namespace snapshot. The Sakura fields are intentional:
        they prove whether the repeated-open drop is cart-state growth or runtime
        overhead while staying harmless for every other cart."""
        ns = self.ns or {}
        petals = _safe_len(ns.get("petals"))
        sin = _safe_len(ns.get("SIN"))
        lay = 1 if ns.get("lay") is not None else 0
        return "ns=%d petals=%d sin=%d lay=%d native=%d" % (
            _safe_len(ns), petals, sin, lay, 1 if self._native_ins else 0)

    def _print_run_diag(self, tag, extra=""):
        if not self._diag_enabled():
            return
        try:
            cart = self.ws.cart or {}
            name = str(cart.get("title") or cart.get("path") or "?")
            typ = str(cart.get("type") or "?")
            d = self._start_diag
            if d is None:
                phases = ""
            else:
                phases = (" phases(reclaim=%d audio=%d api=%d compile=%d exec=%d "
                          "init=%d total=%d) heap(free=%d->%d alloc=%d->%d)"
                          % (d[0], d[1], d[2], d[3], d[4], d[5], d[6],
                             d[7], d[8], d[9], d[10]))
            if self._native_fail:
                phases += " nfail=%s" % str(self._native_fail).replace(" ", "_")
            if extra:
                extra = " " + extra
            print("Moybyte %d %s cart=%s type=%s run=%d %s %s%s%s"
                  % (_ticks_ms(), tag, name.replace(" ", "_"), typ, self._run_seq,
                     self._cart_state_diag(), self._audio_diag(), phases, extra))
        except Exception:  # noqa: BLE001 -- diagnostics must never affect play
            pass

    def _maybe_diag_slow_logic(self, upd_us, render_us, audio_us):
        # Microseconds since 2026-08-14 (see the note at the call site); the gate
        # and the printed line stay in ms so SLOWLOGIC reads as it always has.
        upd_ms = upd_us // 1000
        render_ms = render_us // 1000
        audio_ms = audio_us // 1000
        # PERF DIAG's alone: a periodic line, and the HUD a kid can tap on
        # (perf_hud) is no measurement session.
        if upd_ms < 10 or not getattr(self.ws, "diag_live", False):
            return
        now = _ticks_ms()
        if self._slow_logic_next and _ticks_diff(now, self._slow_logic_next) < 0:
            return
        self._slow_logic_next = now + 2000
        # NO _heap_stats() here (2026-08-03): gc.mem_free()+gc.mem_alloc() each
        # walk the whole 8MB heap table -- ~150ms on the T-Deck, landing right
        # after the perf brackets close, i.e. charged to CHROME. With Brick
        # Siege tripping the >=10ms gate at this 2s rate limit's ceiling, the
        # diagnostic itself was the single largest stutter source in every
        # measured build (ledger included: 20 of its 31 play hitches), and it
        # arms off perf_hud too -- watching the FPS chip injected it.
        self._print_run_diag(
            "SLOWLOGIC",
            "upd=%d render=%d audio=%d" % (upd_ms, render_ms, audio_ms),
        )

    def start(self, project):
        """Start (or re-run) `project`'s cart under make_api. Resets the canvas draw
        state, stamps the cart clock, gates `wifi` by the manifest permission, execs
        the source, and captures _update/_draw. Any exception becomes cart_error/
        crash_line (returned False) so the caller opens to the desktop and frame()
        paints the on-canvas panel instead of hanging. Returns True iff it started."""
        ws = self.ws
        self._run_seq += 1
        t0 = _ticks_ms()
        # Heap stats only in measurement mode: each _heap_stats() is two full
        # heap-table walks (~150ms on device). (-1, -1) is already the host
        # sentinel, so the RUNSTART/RUNERR phases(...) format never changes.
        _hs = _heap_stats if self._diag_enabled() else (lambda: (-1, -1))
        h0 = _hs()
        cart = self._begin_run(ws, project, t0)
        self.verdict = _verdict(cart)
        err = self._gate_spec(ws, cart)
        if err is not None:
            return self._refuse(err)
        _rt = project.cart.get("runtime", "python")
        run = 0
        self._run_handle = 0
        if _rt != "python":
            # The kernel decides the stop from the catalogue entry before
            # anything of the run is built here (docs/kernel_cartpath_2026-10.md
            # section 5.2): an audio session or a buffer made with the VM up
            # would sit inside the free run the stop is about to widen.
            run = self._launch_play(cart)
            self._run_handle = run
            if run and self._stop_for(run, cart):
                return True
        t_reclaim, t_audio = self._prepare_world(ws, project, cart, t0)
        wifi, net, gpio, console = self._grant_services(ws, project, cart)
        # #65 Phase 2: a linked two-console match. THIS is the moment a solo run
        # becomes one -- before the seed is drawn and before _init runs, so both
        # consoles' rnd() start from the same place. The guest side has already
        # been handed a session by its radio (link._on_start), so it just finds
        # ws.netplay set; the host side offers here and becomes player 0.
        link = getattr(ws, "link", None)
        if net is not None and link is not None:
            # ARM the radio here and nowhere earlier. It is only up while a game
            # that could use it is running, because disabling power save (which
            # is what buys the latency tail) costs battery on a handheld, and a
            # console on a shelf has nobody to talk to. Announcing the cart is
            # what lets a peer recognise "we are both in the same game".
            try:
                ws.wifi_hold("link")   # the STA up, owned by the wifi service, BEFORE the radio
                link.start()
                link.announce(cart.get("title") or "", 1)
                # A DEAD match never survives into the next run (#65). The
                # link's own give-up paths clear it as they re-run the cart,
                # but a re-run arriving from anywhere else -- Editor PLAY, the
                # dev channel's `run`, a kid re-launching from the shelf --
                # would otherwise adopt a session whose advance() can only
                # ever stall, and the cart would never simulate again.
                _np = ws.netplay
                if _np is not None and getattr(_np, "dead", False):
                    link.end_match(ws)
                if ws.netplay is None:
                    link.offer(ws, cart.get("title") or "")
            except Exception as exc:  # noqa: BLE001 -- a radio must never block a cart
                print("Moybyte link failed:", exc)
        # Gated on the same permission: an unlinked console leaves this None and
        # the cart runs exactly as a single-player cart does.
        self._netplay = ws.netplay if net is not None else None
        # The pre-tick radio drain (see the frame loop below). Bound once: a
        # linked run drains the link it started with, the way it keeps the
        # session it started with.
        self._drain_input = None
        if self._netplay is not None and link is not None:
            self._drain_input = getattr(link, "drain_input", None)
        # The cart api reads this to refuse touch()/mouse() for the duration:
        # only buttons cross the radio, so a pointer would move one screen's
        # player and not the other's. Set on the InputState because that is what
        # make_api is handed.
        try:
            ws.input.netplay_live = self._netplay is not None
        except Exception:  # noqa: BLE001 -- a bare test stub need not carry it
            pass
        if self._netplay is not None and self._netplay.config:
            # The host's tuning wins for the duration of the match. Applied to
            # the LIVE dict rather than written to the card: it is a property of
            # this match, not a change to the kid's own copy of the cart.
            try:
                project.config.update(self._netplay.config)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte link config failed:", exc)
        t2 = _ticks_ms()
        ns = self._build_namespace(ws, project, cart, wifi, net, gpio, console)
        t_api = _ticks_diff(_ticks_ms(), t2)
        src = project.cart["src"]
        # The driving task's stack high-water mark before the runtime opens,
        # beside the Player's own readings after the open and the frames
        # (moy_play.info): what the cart path costs the S3s' stacks.
        _st = getattr(_moy_play, "stack", None)
        self.stack_pre = _st() if _st is not None else None
        if _rt != "python":
            ok = self._start_runtime(_rt, ns, src, t0, h0,
                                     (t_reclaim, t_audio, t_api))
            if ok:
                self._arm_pacing(cart)
                self._bind_play(run)
                self._front = self._take_front()
            elif run:
                _moy_play.end(run, _moy_play.END_CRASH)
            return ok
        code, t_compile_native, _ckey, _csig = self._compile_python(project, src, ns)
        try:
            t4 = _ticks_ms()
            if code is None:
                # The kid's own syntax error surfaces HERE -> the friendly panel.
                code = compile(src, "<cart>", "exec")
            # Cache the outcome (native OR bytecode-fallback) for this source
            # version -- a repeat PLAY re-execs the same code object (see above).
            _CODE_CACHE[_ckey] = (_csig, code, self._native_ins, self._native_fail)
            t_compile = t_compile_native + _ticks_diff(_ticks_ms(), t4)
            t5 = _ticks_ms()
            _was = _cart_text(not self._look)
            try:
                exec(code, ns)
                t_exec = _ticks_diff(_ticks_ms(), t5)
                t6 = _ticks_ms()
                # A RESPONSIVE app cart (#181) is told its geometry BEFORE _init, so
                # the cart's own state is built against the size it will draw at
                # rather than against a default it then has to correct.
                if self._app_id is not None:
                    self._app_layout = ns.get("_layout")
                    if self._app_layout is not None:
                        self._app_wh = self._layout_args()
                        self._app_layout(*self._app_wh)
                if ns.get("_init"):
                    ns["_init"]()
            finally:
                _cart_text(_was)
            t_init = _ticks_diff(_ticks_ms(), t6)
        except Exception as exc:  # noqa: BLE001
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so tick() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            self.crash_line = self._map_crash_line(_exc_cart_line(exc))
            self.crash_file = None      # the python tier runs main and nothing else
            h1 = _hs()
            self.ns = ns
            self._start_diag = (t_reclaim, t_audio, t_api,
                                locals().get("t_compile", t_compile_native),
                                locals().get("t_exec", -1),
                                locals().get("t_init", -1),
                                _ticks_diff(_ticks_ms(), t0),
                                h0[0], h1[0], h0[1], h1[1])
            self._print_run_diag("RUNERR", "err=%s" % self.cart_error)
            print("Moybyte cart error:", self.cart_error)
            return False
        self.cart_error = None
        self.crash_line = None
        self.crash_file = None
        self.ns = ns
        self._update = ns.get("_update")
        self._draw = ns.get("_draw")
        # Declared background (#63): the api's frame-start restore hook. Cached here so
        # tick() pays one attribute read; it early-outs when the cart declared nothing.
        self._restore_bg = ns.get("_moy_restore_bg")
        h1 = _hs()
        self._start_diag = (t_reclaim, t_audio, t_api, t_compile, t_exec, t_init,
                            _ticks_diff(_ticks_ms(), t0),
                            h0[0], h1[0], h0[1], h1[1])
        self._arm_pacing(cart)
        self._print_run_diag("RUNSTART")
        return True


    def _refuse(self, msg):
        """Refuse a run before any of the cart's code has run: the ordinary
        error panel, with no line to mark."""
        self.cart_error = msg
        self.crash_line = None
        self.crash_file = None
        return False

    def _begin_run(self, ws, project, t0):
        """Reset the previous run's world and stamp this run's per-run flags.
        Returns the cart."""
        ws._dirty = True               # a (re)started cart paints its first frame (#44)
        self._reset_stage_meters()     # #210: this run's misses are its own
        self._disarm_pacing()          # re-armed below, once the cart is running
        # USER APP per-run state (#181): cleared here rather than at the bind
        # site below, so the early SPEC refusals (extensions / canvas) cannot
        # leave a previous app's `_layout` armed against this cart.
        self._app_layout = None
        self._app_wh = None
        self._app_id = None
        self._look = False
        self._ui = None
        self._uh = None
        self._on_change = False
        self._end_grant()
        self._release_editors()        # a RE-RUN is an exit too: flush, then drop
        ws.input.game_view = None      # the `view(w, h)` verb is per-run (cart_quit
                                       # pattern): a cart re-declares it each start
        # The run's own map and scenes, fresh from the Editor's (E-02): a fresh
        # Scenes stands at its default scene with no world, so a load_scene()
        # switch never leaks across a re-run (#85's "resets on next _init").
        project.play_world(True)
        self._flag_put = self._xor_play_flag   # a Python run's flags; a Lua run's below
        self._close_lua()              # a re-run replaces the previous run's Lua state
        # #63 leak fix: the PREVIOUS cart is dead and moycore has let go of it
        # -- return everything on loan to it (its make_layer worlds, its run
        # canvas, its paint bakes) before this run binds and allocates its own.
        # Probe: a canvas with no glass has nothing to return.
        rl = getattr(ws.canvas, "reclaim_layers", None)
        if rl is not None:
            rl("cart")
        self._t_reclaim = _ticks_diff(_ticks_ms(), t0)
        self._sram_run = None          # #211: whatever it reported was the LAST
                                       # run's; a Python cart must not inherit it
        self._pmem_last = t0           # periodic pmem flush counts from this run's start
        self._slow_logic_next = 0
        # #75: cache the bar-visibility-by-type rule for this run (see __init__).
        cart = project.cart
        self._is_tool = cart is not None and cart.get("type") in BAR_TYPES
        # A SCRIPT (docs/text_editing_2026-09.md): a cart with no folder whose
        # screen is the shell's text console. Tool-shaped -- one unpaced tick
        # per loop frame, the minimal bar's X to leave. Its responsive bind is
        # below, WITH the app one: `release_run_canvas` sits between here and
        # there and would drop it.
        self._script = cart is not None and cart.get("type") == SCRIPT_TYPE
        return cart

    def _gate_spec(self, ws, cart):
        """The SPEC gates a cart must pass before its code runs -- required
        extensions, the canvas size, a user app's manifest and crash guard --
        binding the run's canvas on the way. Returns the refusal text, or None
        when the cart may start."""
        # Required-extension gate (moy SPEC.md 10): a cart listing an extension
        # this build doesn't implement is refused cleanly -- the normal error
        # panel -- instead of crashing partway into a frame on a missing verb.
        missing = [e for e in ((cart.get("extensions") or ()) if cart else ())
                   if e not in SUPPORTED_EXTENSIONS]
        if missing:
            return "needs extension: " + ", ".join(missing)
        # Cart canvas gate (SPEC.md 1/3.1): `canvas` is the other capability
        # field -- an out-of-set size was carried raw by the loader and is
        # refused here BY NAME, like an unknown runtime; an in-set size binds a
        # real small canvas for the run (released in release_world). A tier
        # with no factory refuses too: running a 128x128 cart on a 320x240
        # canvas would letterbox it into a corner and lie about W/H.
        cv = cart.get("canvas") if cart else None
        if cv is not None and (not isinstance(cv, (tuple, list)) or len(cv) != 2):
            # clip the repr: a malformed value can be an arbitrary object and
            # the panel wraps 8px cells -- the NAME matters, not the whole blob
            return 'no "%.32s" canvas (SPEC.md 3.1)' % (cv,)
        ws.release_run_canvas()        # a straggler bind never leaks into this run
        if cv is not None and not ws.bind_run_canvas(cv[0], cv[1]):
            return "no %dx%d canvas on this screen yet" % (cv[0], cv[1])
        # -- USER APPS (#181, ui_refactor_2026-08 Phases 7 + 8) ---------------
        #
        # A `type: "app"` cart no shell app claims is a USER APP: written by
        # whoever owns the console, editable on Make, and handed shell
        # capabilities by MANIFEST PERMISSION (make_system_api, below).
        if ws.is_user_app(cart):
            # A manifest this build cannot honour AS WRITTEN is refused by
            # name, like an unknown runtime or an out-of-set canvas -- and
            # BEFORE the crash guard arms, because a mis-declared permission is
            # not a crash and must not spend a strike. Today the only such rule
            # is "at most one file kind"; the alternative was the silent
            # last-one-wins grant this replaced.
            _man = _moy_app.manifest_error(cart.get("permissions"))
            if _man is not None:
                return _man
            # CRASH ISOLATION FIRST, before a single line of the cart's code has
            # been compiled, let alone run: the mark has to survive a death the
            # interpreter never observes (a hang, an OOM, a native fault). See
            # runtime/crash_guard.py. A cart that has used up its strikes is
            # refused into the ORDINARY error panel, whose top bar carries
            # EDIT/CODE -- so "this app is broken" and "here is how you fix it"
            # are the same screen.
            self._app_id = ws.app_cart_id(cart)
            self._look = draws_in_look(cart)
            if not ws.app_guard.arm(self._app_id):
                self._app_id = None
                return ("app turned off after %d crashes - EDIT it"
                                   % ws.app_guard.STRIKES)
            # THE RESPONSIVE OPT-IN. Fixed is the DEFAULT and deliberately so:
            # a kid cart hardcodes coordinates, and the manifest `canvas` (320x240
            # unless it says otherwise) is what the WM centres and integer-scales.
            # A cart that defines a top-level `_layout(w, h, fs)` is saying it can
            # reflow, and gets the whole system surface instead. Probed from the
            # SOURCE because the canvas has to be chosen before make_api closes
            # over it -- see system_api.wants_layout.
            if wants_layout(cart.get("src")):
                ws.bind_app_canvas()
        # A SCRIPT is responsive by construction -- the SHELL draws it, and text
        # is the one thing an integer upscale of the 320x240 raster serves worst
        # -- so it takes the same whole-surface bind an app opts into, with no
        # opt-in to make. (Refused in the windowed desk world for the reason the
        # bind's own docstring gives, where it stays the fixed raster in a
        # window like any cart.)
        elif self._script:
            ws.bind_app_canvas()
        return None

    def _prepare_world(self, ws, project, cart, t0):
        """Build the audio, reset the canvas state, install a declared palette
        and stamp the cart clock. Returns the (reclaim, audio) ms for the
        RUNSTART diag; the reclaim is _begin_run's."""
        t_reclaim = self._t_reclaim
        t1 = _ticks_ms()
        project._build_audio()
        t_audio = _ticks_diff(_ticks_ms(), t1)
        # Reset the canvas draw state (camera/clip/pal/palt, #11) so a fresh cart run
        # never inherits a previous cart's clip rect or palette swap.
        rs = getattr(ws.canvas, "reset_state", None)
        if rs is not None:
            rs()
        # Cart-supplied palette (moy SPEC.md 2.2): 64 "RRGGBB" strings in the
        # manifest replace the default table for this run; layers made during
        # the run inherit it (make_layer shares canvas.palette), and every exit
        # path restores it (release_world). Indexed backends without a live
        # .palette (the device compositor's baked RGB565 LUT) skip it -- a
        # recorded conformance gap there, never a crash.
        self._restore_palette()        # a re-run must never stack two swaps
        pal = cart.get("palette") if cart else None
        if pal and len(pal) == 64 and getattr(ws.canvas, "palette", None):
            try:
                table = []
                for s in pal:
                    v = int(s.lstrip("#"), 16)
                    table.append(((v >> 16) & 255, (v >> 8) & 255, v & 255))
            except (ValueError, AttributeError, TypeError):
                table = None               # malformed -> keep the default table
            if table is not None:
                self._cart_palette = ws.canvas.palette
                self._cart_palette_canvas = ws.canvas
                ws.canvas.palette = table
        # Stamp the cart-start clock so the cart's time() reads ms since this run
        # began (re-run on apply/run_code/edit-close resets it, like TIC-80).
        self._cart_start_ms = _ticks_ms()
        ws.input.cart_start_ms = self._cart_start_ms
        return t_reclaim, t_audio

    def _grant_services(self, ws, project, cart):
        """The capability services this cart's manifest permissions grant --
        (wifi, net, gpio, console) -- each None when not declared."""
        # Capability-permission gate (#38): hand make_api the wifi backend ONLY
        # when this cart declares the "network" permission, so a normal kid cart
        # gets NO `wifi` name (sandbox preserved). make_api injects `wifi` into the
        # cart namespace iff the backend it receives is non-None.
        wifi = ws.wifi if ws._cart_has_perm("network") else None
        if wifi is not None:
            ws.wifi_hold("cart")       # the radio lease for this run; release_world lets go
        # Multiplayer message service (#65): gate net.* by the "multiplayer"
        # manifest permission exactly like wifi's "network" gate. reset() drops any
        # handler/inbox from a previous run so a fresh run starts clean; make_api
        # injects `net`/`on_net` into the namespace iff the backend is non-None.
        net = ws.net if ws._cart_has_perm("multiplayer") else None
        # Physical pins (#9), gated HERE with its two siblings rather than in the
        # tier that happens to supply it. It used to be injected by the browser
        # tier's own make_api wrapper the moment the serving host answered the pin
        # probe -- which gave it the CAPABILITY half of the gate (does this
        # console have pins) and none of the CONSENT half (did this cart ask),
        # while cart_api's comment claimed it was gated "the same way as wifi and
        # net". It was not, and the difference stopped being theoretical once a
        # dropped .p8 could land in a board's own store: a cart nobody wrote
        # could move a pin nobody declared. The pin ALLOWLIST bounds which pins,
        # never which carts.
        gpio = ws.gpio if ws._cart_has_perm("pins") else None
        # The TEXT CONSOLE, gated on the "console" permission exactly as wifi is
        # on "network": a cart that never declares it keeps `print` as the DRAW
        # verb, and a script -- whose synthesized manifest DOES declare it --
        # gets the scrollback instead. The terminal (#115) declares it too.
        console = (getattr(ws, "script_console", None)
                   if ws._cart_has_perm("console") else None)
        if net is not None:
            net.reset()
        self._net = net
        return wifi, net, gpio, console

    def _ui_begin(self, ws, render=True):
        """Begin a Python app's frame of `ui` verbs (runtime/ui.py) over the
        input its btn() and touch() read, in the run snapshot's shape, before
        its update, so `ui.keys_taken()` answers there.

        The frame begins through the run's app host (`AppHost.run`), the
        path every shell app's frames take: where the run has an editor
        handle open and drawn, the engine may plan it a shift frame -- its
        text view's fling, glide or a keyed step moved out of the frame
        before, or its changed rows drawn alone
        (docs/scroll_and_text_2026-10.md §5). Any other run draws whole."""
        ctx, a, cv = self._ui
        host = self._uh
        inp = ws.input
        bm = self._masks
        if bm is not None:
            m = bm(_NET_BUTTONS, None, _UI_MASKS)
        else:
            m = _UI_MASKS
            m[0] = m[1] = 0
            for i in range(len(_NET_BUTTONS)):
                if inp.held(_NET_BUTTONS[i]):
                    m[0] |= 1 << i
                if inp.pressed(_NET_BUTTONS[i]):
                    m[1] |= 1 << i
        st = _pointer_state(inp, _UI_POINTER)
        a[0], a[1] = m[0], m[1]
        a[2], a[3], a[4] = st[0], st[1], st[2]
        a[5] = int(getattr(inp, "last_key", 0) or 0)
        a[6] = int(getattr(inp, "mods", 0) or 0)
        a[7] = getattr(inp, "wheel_x", 0)
        a[8] = getattr(inp, "wheel_y", 0)
        if getattr(inp, "pointer_buttons", 0) & _PB_MIDDLE:
            a[4] |= _P_MIDDLE
        fed = self._focus_editor
        if fed is not None:
            a[0] = a[1] = a[5] = 0      # the keys are the focused editor handle's
            if a[4] and fed.claims(a[2], a[3]):
                a[4] = _P_LIVE          # its own gesture (a mouse's, a selection's): a hover
        # an app on the whole surface (a desk's window, or its glass) lays out
        # on the desk's rows there; the fixed raster is the handheld's
        desk = ws.app_full_canvas and _layout_base.metric_class() == _layout_base.DESK
        ed = self._note() if render and not self._bg_on else None
        if ed is None or (self._over[2] and ed.meets(self._over)):
            host.whole()                # nothing vouches for the page's pixels
        else:
            ed.aim(host)                # a caret's step lands as this frame's shift
        self._over[2] = 0
        lost = _ui_draw._C.api_lost(ctx)
        if lost:
            print("ui: a frame drew %d stops past the ones it records; they take no focus or "
                  "input (a long list is one ui.list or ui.table)" % lost)
        # On a desk the run's canvas is its window's own, which only the run
        # draws (`holds`): it holds the page as the run's last frame left it.
        own = self.holds(cv)
        if own:
            _ui_draw.window_frame(2)
        try:
            settled = host.run(cv, ws.theme_colors, None, a, desk, ws.windowed_chrome,
                               fed is not None)
        finally:
            if own:
                _ui_draw.window_frame(0)
        self._pass = render
        self._broke = False
        if self._on_change:
            # A frame that moved anything owes the next, which paints the
            # state it left (a verb acts after its part drew, a tap on the
            # note moves the caret after the note drew); a frame with nothing
            # going on pays off one.
            if (not settled or self._input_active(inp) or _ui_owes(ctx)
                    or _ui_moving(ctx)):
                self._owe = 2
            elif self._owe:
                self._owe -= 1

    def _note(self):
        """The one editor handle the run's pass drew last, else None."""
        ed = None
        for e in self._editors:         # a closed one draws nothing
            if e.live:
                if ed is not None:
                    return None
                ed = e
        return ed if ed is not None and ed.drawn_in(self._uh) else None

    def _ui_close(self):
        """The app's pass is done: a frame whose pass a clear broke said so
        as it drew (`_cart_cls`); the frame's change is the engine's, and
        what it changed of the canvas (`change`) its plan's."""
        self._pass = False
        ch = self.change
        self.change_f = _ui_draw.painted()
        if self._broke:
            _ui_draw._CHANGE[0] = None  # it drew whole from the clear on
            ch[8] = 0
        else:
            ch[8] = 1 if self._uh.change_box(ch) else 0

    def ui_host(self, cv):
        """The run's app host whose pass draws on `cv` now, else None: an
        editor handle draws its text view in that pass."""
        ui = self._ui
        if not self._pass or ui is None or cv is not ui[2]:
            return None
        return self._uh

    def overdrew(self, cv, x, y, w, h):
        """Rect x, y, w, h of `cv` was drawn over the run's page after its
        pass (the FPS chip): the next frame moves none of an editor
        handle's rows where it lay."""
        ui = self._ui
        if ui is None or cv is not ui[2] or w <= 0 or h <= 0:
            return
        o = self._over
        o[0] = x
        o[1] = y
        o[2] = w
        o[3] = h
        ch = self.change
        if ch[8] and self.change_f == _ui_draw.painted():
            if ch[2] <= ch[0] or ch[3] <= ch[1]:
                ch[0] = x
                ch[1] = y
                ch[2] = x + w
                ch[3] = y + h
            else:
                ch[0] = x if x < ch[0] else ch[0]
                ch[1] = y if y < ch[1] else ch[1]
                ch[2] = x + w if x + w > ch[2] else ch[2]
                ch[3] = y + h if y + h > ch[3] else ch[3]

    def holds(self, cv):
        """Whether `cv`, the run's canvas, is one buffer only the run
        draws and the window's composite reads (not the glass): it holds
        what the run drew last, whatever frames the console painted since."""
        return cv is self.ws.canvas and cv is not self.ws.sys_canvas

    def _cart_cls(self, c=0):
        """The cart's `cls`: a clear under a floor makes the frame whole
        from there."""
        if self._pass and self._uh.shifting:
            _ui_draw._C.api_shift_break(self._uh.ctx)
            self._broke = True
        self._cls(c)

    def _cart_bg(self, *a):
        """The cart's `background`: a declared backdrop repaints the canvas
        before every pass, so the run draws whole."""
        self._bg_on = True
        return self._bg(*a)

    def animates(self):
        """Whether the running cart has a frame to draw now. A game always
        has. An app on the verbs (`_on_change`) has one while input is going
        on, while a frame it drew still owes the next, while the engine has a
        part in motion (`api_owes`: a toast, a fade; `api_moving`: a page's
        fling or glide) and while an editor
        handle it opened changed since it drew (`EditorHandle.owes`); still,
        it draws nothing, and its window keeps its last frame."""
        if not self._on_change:
            return True
        if self._owe or self._input_active(self.ws.input):
            return True
        ui = self._ui
        if ui is not None and (_ui_owes(ui[0]) or _ui_moving(ui[0])):
            return True
        for ed in self._editors:
            if ed.owes():
                return True
        return False

    def _input_active(self, inp):
        """A key, a button, the wheel or the middle button this frame: what
        an app's `_update` and its verbs read. The pointer's moves are the
        console's own redraw leg (`Workstation._ptr_changed`)."""
        if getattr(inp, "last_key", 0):
            return True
        bm = self._masks
        if bm is not None:
            m = bm(_NET_BUTTONS, None, _UI_MASKS)
            if m[0] or m[1]:
                return True
        else:
            for i in range(len(_NET_BUTTONS)):
                if inp.held(_NET_BUTTONS[i]) or inp.pressed(_NET_BUTTONS[i]):
                    return True
        if getattr(inp, "wheel_x", 0) or getattr(inp, "wheel_y", 0):
            return True
        return bool(getattr(inp, "pointer_buttons", 0) & _PB_MIDDLE)

    def _build_namespace(self, ws, project, cart, wifi, net, gpio, console):
        """The cart's namespace: make_api over the project, the artwork and
        system-api capabilities a shipped app / user app / script is granted,
        the text console's verbs, and the other scripts either side of main."""
        ns = ws.make_api(ws.canvas, ws.input, project.config, project.sheet,
                         ws.audio, project.play_map, project.pmem, wifi, project.images,
                         project.play_scenes,   # #85: scene()/load_scene(), the run's own
                         net=net,           # #65: capability-gated net.* backend
                         gpio=gpio,         # #9: capability-gated physical pins
                         flags=project.play_flags)   # SPEC.md 3.5: the run's own flags
        # Paint is a regular cartridge with one narrow shell capability. Keep it out
        # of the kid API and inject it only into the shipped app identity that asks
        # for the artwork permission; copied/renamed carts do not inherit it.
        if (getattr(ws, "artwork", None) is not None
                and ws.artwork.is_paint_app(cart)):
            ns["artwork"] = ws.artwork
        # USER APP capability injection (#181): the cart's manifest permissions,
        # intersected with `system_api`'s allowlist, become names in its
        # namespace -- and a capability it did not declare has NO NAME AT ALL,
        # exactly like `wifi` above. Same AppContext the shipped apps get,
        # through the same `ws.app_context` factory; only the source of the
        # `needs` tuple differs (a manifest here, a class constant there). The
        # ungated riders (`ui`, `theme()`, `screen()`, `content_rect()`,
        # `bar_h()`) are how an app draws, not what it may reach -- see
        # runtime/system_api.py.
        # A SCRIPT is handed the same filter by the same call: its permissions
        # come from the manifest `run_script` synthesized, so the refusing is
        # the machinery that already refuses, and `carts` is not grantable to
        # anything (no permission maps to it, native/moy_app's policy).
        if self._app_id is not None or self._script:
            ns.update(make_system_api(ws.app_context, cart, ws.canvas,
                                      editor=self._open_cart_editor,
                                      request=ws.take_text_request(),
                                      keep=self._keep_grant))
            if "ui" in ns:
                self._uh = _ui_draw.AppHost()
                self._ui = [self._uh.ctx, _array("i", [0] * _UI_IN_LEN), ws.canvas]
                # Bound once: a getattr that finds a method allocates its
                # bound method every frame.
                self._masks = getattr(ws.input, "button_masks", None)
                # An app on the verbs is drawn on change, as a shipped app is
                # (docs/ui_api_2026-10.md, "Drawn on change"), unless its
                # manifest says it animates. One that plays sound keeps every
                # frame too: the frame is where the host and the browser pull
                # its mix (`audio_out.tick`), and a still frame pulls none.
                self._on_change = (self._app_id is not None and not self._script
                                   and cart.get("animates") is not True
                                   and "audio" not in (cart.get("permissions") or ()))
                self._owe = 2
                self._bg_on = False
                if self._on_change and "cls" in ns:
                    # a clear lands under any floor: the frame draws whole;
                    # a declared background clears every frame
                    self._cls = ns["cls"]
                    ns["cls"] = self._cart_cls
                    self._bg = ns.get("background")
                    if self._bg is not None:
                        ns["background"] = self._cart_bg
        if console is not None:
            ns.update(console.api())
        # The cart's OTHER SCRIPTS, either side of main (SPEC.md 4). A ported
        # cart's generated half -- data tables + the compat shim -- lives in
        # p8.lua and runs ahead of main.lua, so main.lua is the cart's own code
        # and an error in the shim reads `p8.lua:N:`, never landing on the kid's
        # line in the crash-to-code panel. Each is its own chunk, which is what
        # lets them be separate files at all.
        #
        # They ride the namespace like `_moy_prelude`, the same mechanism the
        # text console's binding uses; both lists are empty for every cart that
        # declares no `sources`, and lua_ext.cart_chunks then builds the same
        # one-entry list the tiers always ran.
        pre = project.cart.get("src_before")
        if pre:
            ns["_moy_pre"] = pre
        post = project.cart.get("src_after")
        if post:
            ns["_moy_post"] = post
        # #67 dual-runtime seam (Phase 2): a non-python cart never touches the
        # Python compile / auto-native / code-cache path below -- it starts
        # through the injected Lua runtime instead (same ns, same error panel).
        return ns

    def _compile_python(self, project, src, ns):
        """Compile a Python cart, preferring the auto-native rewrite through
        the per-source code cache. Returns (code, native_compile_ms, cache key,
        source signature); code is None when the emitter refused and the
        pristine compile must run -- the caller stores that outcome under the
        key."""
        # Compile with the "<cart>" filename so a runtime traceback carries cart
        # line numbers (_exc_cart_line reads them to mark the bad line). #67 spike:
        # prefer the AUTO-NATIVE rewrite (machine code per top-level def) when the
        # emitter exists; if it refuses the cart at compile time, fall back to the
        # pristine bytecode compile -- the flag can never break a cart. The
        # inserted-line map keeps crash lines exact (#24) either way.
        self._native_ins = None
        self._native_fail = None
        code = None
        t3 = _ticks_ms()
        # #66 THE repeat-run cliff fix (glass-fingerprinted 2026-07-11): the esp32
        # port's exec arena (esp_native_code_commit, MALLOC_CAP_EXEC internal
        # IRAM) is a GROW-ONLY list -- every @micropython.native compile leaks its
        # machine-code blobs until soft reset. Recompiling the SAME source on
        # every PLAY (the kid's edit->PLAY->edit loop!) exhausted it in ~5 runs of
        # a heavy cart; the emitter then died on a ~300-byte MemoryError and the
        # silent bytecode fallback HALVED the cart's logic speed ("the floor",
        # logic 6.5 -> 13-17ms; nfail= in RUNSTART names it). So compile ONCE per
        # (cart, source version): the cache key is the source's (len, hash) and
        # re-PLAYs of unchanged source re-exec the SAME code object -- its blobs
        # are immortal in the arena anyway, so reuse costs nothing and saves the
        # ~110-210ms recompile per PLAY too. An EDIT legitimately recompiles (new
        # hash -> one new blob-set; the old one still leaks -- the arena has no
        # free -- so a marathon edit session can still exhaust it, but at the
        # per-edit rate instead of the per-PLAY rate; the fallback stays graceful).
        _ckey = project.cart.get("path") or id(project.cart)
        _csig = (len(src), hash(src))
        _hit = _CODE_CACHE.get(_ckey)
        if _hit is not None and _hit[0] == _csig:
            code = _hit[1]
            self._native_ins = _hit[2]
            self._native_fail = _hit[3]
            if NATIVE_CARTS and self._native_ins is not None:
                ns["micropython"] = _micropython
        elif NATIVE_CARTS:
            # Compile MISS (new cart or edited source): reclaim the exec arena
            # FIRST (#66 -- it is grow-only otherwise; the port patch exposes
            # the free through moy_gfx). Safe here: the cache is being purged
            # (its code objects' native blobs die with the arena) and no cart
            # world is live (the previous run's release_world dropped it; the
            # wallpaper compiles plain, never native). The arena then holds
            # exactly ONE cart's current-version blobs at any time -- unlimited
            # edit->PLAY cycles. On builds without the patch the call reports
            # False and behavior is the pre-patch cache-only mitigation.
            _CODE_CACHE.clear()
            try:
                import moy_gfx
                moy_gfx.native_code_free_all()
            except Exception:  # noqa: BLE001 -- host / bench builds: no reclaim
                pass
            nsrc, ins = _nativize(src)
            ns["micropython"] = _micropython   # the decorator's global, no import line
            try:
                code = compile(nsrc, "<cart>", "exec")
                self._native_ins = ins
            except Exception as exc:  # noqa: BLE001 -- emitter limitation -> bytecode path
                self._native_fail = _err_text(exc)
                code = None
        t_compile_native = _ticks_diff(_ticks_ms(), t3)
        return code, t_compile_native, _ckey, _csig


    def _arm_pacing(self, cart):
        """A GAME runs on the tick model (#217): logic at its manifest rate --
        60 opt-in, anything else the 30 the console guarantees -- and draw on
        the scheduler's divisor. A tool/app is event-driven and ticks with the
        loop it serves, so it is not paced."""
        if self._is_tool:
            return
        fps = cart.get("fps")
        if fps == "free":
            # SPEC 5's opt-out: the cart says its logic is dt-scaled and asks
            # to run with the loop -- the tool path, real dt, every loop frame
            # draws. What a board can draw is what it gets (a P4 desk ~60, the
            # Guition ~45); a frame-counting cart must never say this. The one
            # thing the tick model still owes it is stall protection: dt is
            # clamped (FREE_DT_MAX) so a radio scan slows time, never jumps it.
            self._free = True
            return
        try:
            rate = int(fps or 30)
        except (TypeError, ValueError):
            rate = 30
        ws = self.ws
        self.sched.start(rate, getattr(ws, "steady", True))
        self.sched.uncap_mode(bool(getattr(ws, "_uncap", False)))
        self.tick_ms = self.sched.tick_ms
        # The dt a cart's tick is handed, in this VM's own float: the model
        # paces in C's single precision, the cart reads the exact period.
        self._tick_dt = 1.0 / self.sched.rate
        if _loop is not None:
            _loop.tick(self.tick_ms)
        self._keyp_latch = 0
        inp = ws.input
        self._tick_edges = getattr(inp, "tick_edges", None)
        self._keep_edges = getattr(inp, "keep_edges", None)

    def _launch_play(self, cart):
        """Launch a runtime cart's run in the kernel's Player (native/moy_play)
        BEFORE its runtime loads, so the load and _init are in the run's books
        (its upcalls by class since the launch): the run's handle, or 0 where
        the image has no Player or the cart no folder. A lockstep match's
        frame is the Player's too (moy_play_lockstep)."""
        launch = getattr(_moy_play, "launch", None)
        path = cart.get("path") if cart else None
        if launch is None or not path:
            return 0
        paced = not self._is_tool and cart.get("fps") != "free"
        ws = self.ws
        # What the kernel's stop verdict reads beside the catalogue entry:
        # the run's route is HOME (the home shelf started it), and the owner's
        # Unknown sources setting, which the load after a stop applies.
        flags = 0
        if getattr(ws, "_home_launch", False) and not self._is_tool:
            flags |= getattr(_moy_play, "HOME", 0)
        if getattr(ws, "unknown_sources", False):
            flags |= getattr(_moy_play, "UNSIGNED", 0)
        try:
            return launch(path, paced, flags) if flags else launch(path, paced)
        except RuntimeError as exc:
            print("PLAY launch:", exc)
            return 0

    def _stop_for(self, run, cart):
        """True when the kernel decided at the launch to stop the VM for the
        run (docs/kernel_cartpath_2026-10.md 5.2, the `need` policy: a VM-free
        cart that does not fit with the VM up): nothing of the cart loads here.
        The shelf's place goes to the resume record, and the loop ends at
        this frame; the kernel loads and runs the cart with no VM and starts
        the next VM, which lands on the shelf where it was left."""
        info_of = getattr(_moy_play, "info", None)
        info = info_of(run) if info_of is not None else None
        if info is None or len(info) < 13 or info[11] != "stops":
            return False
        try:
            import moy_kernel
        except ImportError:
            return False
        stop = getattr(moy_kernel, "stop", None)
        if stop is None:
            return False
        ws = self.ws
        rec = getattr(ws, "resume_record", None)
        resume = getattr(moy_kernel, "resume", None)
        if rec is not None and resume is not None:
            try:
                resume(rec())
            except Exception as exc:  # noqa: BLE001 -- a lost place is not a lost run
                print("PLAY resume:", exc)
        self._stop_grant(cart)
        print("PLAY stop: %s runs with the VM down" % (cart.get("title") or "cart"))
        self._stopping = True
        stop()
        return True

    def _stop_grant(self, cart):
        """A compiled app the VM stops for (its imports all C-served, the
        verdict's rule) runs with no Python to make its grant: the grant is
        made here, before the stop, as make_system_api makes a user app's
        (the C policy and key, the ungated theme and surface beside its
        roles), and bound for its imports. It is kernel state, so the stop
        leaves it; the start after the run ends it (console._app_abi)."""
        ws = self.ws
        app = getattr(ws, "app_abi", None)
        if (app is None or getattr(app, "wasm_bind", None) is None
                or cart.get("runtime") != "wasm"
                or APP_EXT not in (cart.get("extensions") or ())
                or not ws.is_user_app(cart)):
            return
        roles, kind = _moy_app.policy(cart.get("permissions"))
        needs = roles + tuple(r for r in ("surface", "theme") if r not in roles)
        app_id = _moy_app.id_for(cart.get("id"), cart.get("title"))
        g = app.grant(app_id, needs, kind=kind, ns=app_id, run=True)
        app.wasm_bind(g, roles)

    def _bind_play(self, run):
        """Give the launched run's frame to the kernel's Player when its
        runtime offers it (`play_begin`): every tick of a frame then runs in C
        -- the press edges, the snapshot, the cart, its audio -- and
        `_run_ticks` makes one call. A runtime that offers it holds the run
        and ends it at its close (its loans go with it), taken or not; one
        that does not is ended here, and its frames tick through Python."""
        self._play = None
        if not run:
            return
        begin = getattr(self._lua, "play_begin", None)
        if begin is None:
            _moy_play.end(run)
            return
        try:
            self._play = begin(run, self.sched if self.tick_ms else None)
        except Exception as exc:  # noqa: BLE001 -- the Python frame still runs it
            print("PLAY:", exc)
            self._play = None

    def _take_front(self):
        """Hand a VM-free game's frame to the kernel (moy_play_front): from the
        next loop frame its input, ticks, chrome and present are C and the
        loop makes no console upcall until the run ends or needs the console
        (docs/kernel_cartpath_2026-10.md §4). Where the board's compositor
        gives no front (`ws.front_bind`), or this canvas is not the one it
        composes, the console's frame keeps driving the same Player."""
        if self._play is None or self._is_tool or not (self.verdict and self.verdict[1]):
            return False
        bind = getattr(self.ws, "front_bind", None)
        take = getattr(_moy_play, "front", None)
        if bind is None or take is None:
            return False
        try:
            return bool(bind()) and bool(take(_moy_play.current()))
        except Exception as exc:  # noqa: BLE001 -- the console's frame still runs it
            print("PLAY front:", exc)
            return False

    def _resume_from_front(self, ws):
        """The console's first frame after the kernel's front gave it back:
        True when the run ENDED there (quit(), `end`, a Ctrl-C),
        whose route this takes; False when it is live and needs the console
        (a view it composes, an error it reports, a cart the link asked for).
        While the kernel still has the front -- a console frame in the same
        loop frame that took it, as a link's re-run does -- the frame is not
        the console's: True, and the run stays the kernel's."""
        if _moy_play.front_live():
            return True
        self._front = False
        info = _moy_play.info()
        if info is None or not info[6]:
            return False
        ws._reset_canvas_state()
        ws._exit_to_caller()
        return True

    # -- the pause menu's hold (docs/look_2026-10.md step 25a) -----------------

    @property
    def paused(self):
        return self._paused_at != 0

    def pause(self):
        """Hold the run for the pause menu: no tick, no draw, its time() and
        its music stopped where they are (the mix loses its session, which
        keeps its state unheard), and the keys it kept dropped."""
        if self._paused_at:
            return
        self._paused_at = _ticks_ms() or 1
        na = _audio_native()
        if na is not None:
            try:
                self._audio_held = na.focused()
                na.focus(0)
            except Exception:  # noqa: BLE001 -- a pause never fails on sound
                self._audio_held = 0
        self.park()

    def resume(self):
        """Give the run back as if no time passed: its clock moves by the
        pause, the mix takes its session again, its next tick sees fresh
        edges and the kernel takes the frame again where it had it."""
        if not self._paused_at:
            return
        span = _ticks_diff(_ticks_ms(), self._paused_at)
        self._paused_at = 0
        if span > 0:
            self._cart_start_ms = _ticks_add(self._cart_start_ms, span)
            inp = self.ws.input
            inp.cart_start_ms = _ticks_add(getattr(inp, "cart_start_ms", 0), span)
            run = _moy_play.current() if self._play is not None else 0
            if run:
                _moy_play.hold(run, span)
        held, self._audio_held = self._audio_held, 0
        na = _audio_native()
        if na is not None and held:
            try:
                na.focus(held)
            except Exception:  # noqa: BLE001 -- the session may have closed
                pass
        self.park()
        self._front = self._take_front()

    def _loop_dt(self, dt):
        """The dt an UNPACED tick gets: the loop's own for a tool/app, and for
        a `"fps": "free"` game the same CLAMPED at FREE_DT_MAX -- a stall (a
        radio scan, a cart load) slows its time instead of teleporting every
        dt-scaled body across the screen."""
        if self._free and dt > FREE_DT_MAX:
            return FREE_DT_MAX
        return dt

    def _disarm_pacing(self):
        self.tick_ms = 0
        if _loop is not None:
            _loop.tick(0)
        self._free = False
        self._n_ticks = 1
        self._tick_edges = None
        self._keep_edges = None
        self.park()

    def park(self):
        """A paced game that is not the surface on top (an Editor or menu
        over it) neither ticks nor keeps edges: what it kept for its next
        tick is dropped, so the shell reads fresh edges and the game does not
        resume on a press made before it was covered."""
        self._keyp_latch = 0
        de = getattr(self.ws.input, "drop_edges", None)
        if de is not None:
            de()

    def steady_mode(self, on):
        self.sched.steady_mode(on)

    def uncap_mode(self, on):
        """The DIAG uncap (serial `uncap 0|1`): every loop frame draws, logic
        keeps its rate. Reaches a running cart here; the next run reads
        ws._uncap at start."""
        self.sched.uncap_mode(on)

    def frame_plan(self, dt):
        """What this loop frame is for the running cart (#217): schedules its
        logic ticks (tick() runs them) and returns whether the frame DRAWS. A
        tool/app or a crashed cart ticks and draws with the loop it serves. A
        lockstep match (#65) owns its own clock: its due tick decides, and the
        draw divisor stays 1 there -- composing the two is its own step."""
        if not self.tick_ms:
            self._n_ticks = 1
            return True
        np = self._netplay
        if np is not None:
            self._n_ticks = 1
            return bool(np.pending(_ticks_ms()))
        s = self.sched
        s.plan(dt)
        self._n_ticks = s.n
        if s.n == 0 and self._keep_edges is not None:
            self._keep_edges()
        return s.draw

    def _run_ticks(self, n, dt, draw):
        """`n` logic ticks of `dt`. Each takes the latched press edges, and the
        last carries this frame's draw for a runtime whose tick fuses _update
        and _draw (the Lua tier)."""
        play = self._play
        if play is not None:
            self._keyp_latch = 0
            if n:
                play(n, dt, draw)
            return
        upd = self._update
        lua = self._lua
        fs = self._lua_split
        te = self._tick_edges
        inp = self.ws.input
        sched = self.sched
        i = 0
        while i < n:
            i += 1
            if te is not None:
                te()
            inp.cart_keyp = self._keyp_latch
            self._keyp_latch = 0
            if lua is not None:
                lua.draw_next = draw and i == n
            if upd is not None:
                t0 = _ticks_us()
                upd(dt)
                cost = _ticks_diff(_ticks_us(), t0) / 1000000.0
                # THE LOGIC HALF, not the frame. A runtime that runs the whole
                # cart frame inside update() (moycore: _update and _draw back
                # to back in C) makes this clock read logic PLUS draw, and the
                # scheduler reads `tick_cost` as the logic alone in both of its
                # rules: the catch-up test for a CHEAP tick, and the pin that
                # says no divisor can help a tick already costing a period.
                # Measured on a T-Deck 2026-09-10, `dank tomb` at 60Hz: the
                # fused frame is 25.7ms against a 16.7ms period, so the pin
                # fired and held the divisor at 1 -- while the scheduler's own
                # fits(3) was True -- and the cart ran its logic at 27Hz, half
                # its declared speed, which is the exact slowdown the tick
                # model exists to refuse. The DRAWBRK split already asks this
                # runtime the same question for the same reason.
                if fs is not None:
                    _sp = fs()
                    if _sp is not None:
                        cost = _sp[0] / 1000.0        # ms -> s, the update half
                sched.note_tick(cost)

    def _start_runtime(self, runtime, ns, src, t0, h0, t_pre):
        """Start a cart on a runtime other than the console's Python: `"lua"`
        (#67) or `"wasm"` (docs/wasm_tier_plan_2026-09.md), through the factory
        `ws.runtimes` maps it to -- moycore_glue's runs on every tier, and
        runtime/wasm_host.WasmHostRun for a compiled cart on the host. The cart gets the SAME make_api namespace a Python cart got
        (a Lua factory registers those callables as the cart's globals; a wasm
        one reads its config and pmem from it), so permission gating, pmem,
        audio and quit() semantics are identical by construction. No
        auto-native, no code cache -- those are Python-compiler concerns. A
        runtime this build lacks is an ABSENT KEY, and it and a load/_init
        error land on the normal cart error panel."""
        ws = self.ws
        t_reclaim, t_audio, t_api = t_pre
        # Same measurement-mode gate as start(): no heap walks in kid mode.
        _hs = _heap_stats if self._diag_enabled() else (lambda: (-1, -1))
        self._native_ins = None        # RUNSTART diag: no auto-native on this path
        self._native_fail = None
        make = (getattr(ws, "runtimes", None) or {}).get(runtime)
        t5 = _ticks_ms()
        t_exec = -1
        t_init = -1
        lua = None
        try:
            if runtime not in RUNTIME_NAMES:
                raise ValueError("unknown cart runtime '%s'" % runtime)
            if make is None:
                # The graceful floor: a cart on a build without its runtime
                # opens the panel, never a hang.
                raise RuntimeError("needs the %s runtime (not in this build)"
                                   % RUNTIME_NAMES[runtime])
            if runtime == "wasm":
                # A compiled cart's imports against this console's table, and
                # its load footprint against what this console can give it,
                # before anything loads.
                cart = ws.cart or {}
                missing = _cart_missing(make, cart)
                if missing:
                    raise _Refused(newer_notice(missing),
                                   _ch.say_title(_ch.SAY_NEWER))
                fit = _cart_fit(make, cart)
                # A failed fit gets back what the WM holds as caches -- parked
                # window stamps oldest-first, then the home's retained frame
                # and a dialog's held page -- one at a time, until the check
                # passes or nothing is left
                # (docs/shell_layout_2026-10.md §3).
                while (fit is not None and not _fits(fit)
                       and ws.wm.release_cache()):
                    fit = _cart_fit(make, cart)
                if fit is not None and not _fits(fit):
                    raise _Refused(fit_notice(cart.get("title"), fit[0], fit[1]),
                                   _ch.say_title(_ch.SAY_FIT))
            # A Lua run binds none, which leaves the console's state the
            # one its `ui` reads the theme from (moy_app_wasm.h).
            self._wasm_bind(ws.cart)
            # The cart's code loads and inits inside the app-cart boundary, as
            # a Python cart's exec and _init do (docs/theming_2026-09.md §4.7).
            _was = _cart_text(True)
            try:
                lua = make(ns, src)
            finally:
                _cart_text(_was)
            if runtime == "wasm" and getattr(lua, "interp", False):
                # Not an error and not a panel: the cart plays, on the
                # interpreter rather than the module this console wanted.
                # The sub-line names the cause; an unrecognised or absent one
                # (a fixture, or a runtime that predates interp_cause) reads
                # as "missing", the more common case.
                notice = getattr(ws, "notice", None)
                if notice is not None:
                    sub = INTERP_NOTICE_SUB.get(getattr(lua, "interp_cause", None),
                                                INTERP_NOTICE_SUB_DEFAULT)
                    notice(INTERP_NOTICE_TITLE, sub, "warn")
            t_exec = _ticks_diff(_ticks_ms(), t5)
            t6 = _ticks_ms()
            if lua.init is not None:
                _was = _cart_text(True)
                try:
                    lua.init()
                finally:
                    _cart_text(_was)
            t_init = _ticks_diff(_ticks_ms(), t6)
        except Exception as exc:  # noqa: BLE001 -- load/_init error -> the panel
            if lua is not None:
                try:
                    lua.close()
                except Exception:  # noqa: BLE001
                    pass
            self.cart_error = _lua_err_text(exc)
            # a load/syntax or _init error carries its `cart:N:` position, so
            # EDIT drops on the line exactly like a Python SyntaxError (#24) --
            # and on a cart of several scripts, in the FILE that raised. A
            # compiled cart has no line to drop on; one built for a newer
            # console gets the newer-console notice, and one this console
            # cannot hold gets the fit notice, whether the check refused it or
            # its load ran out of memory. Every OTHER compiled-cart refusal
            # (no/stale module, unsigned with the switch off) was already
            # retried on the interpreter inside WasmRun -- what reaches here
            # is a real error (a trap, a bad signature, a malformed cart).
            if runtime == "wasm":
                self.crash_file, self.crash_line = None, None
                title = (ws.cart or {}).get("title")
                if isinstance(exc, _Refused):
                    self._notice = self.cart_error = exc.args[0]
                    self._notice_title = exc.args[1]
                elif _out_of_memory(exc):
                    print("Moybyte cart load:", self.cart_error)
                    fit = _cart_fit(make, ws.cart or {})
                    self._notice = self.cart_error = fit_notice(
                        title, fit[0] if fit else None, fit[1] if fit else None)
                    self._notice_title = _ch.say_title(_ch.SAY_FIT)
            else:
                self.crash_file, self.crash_line = _lua_cart_where(
                    self.cart_error, self.ws.cart)
            self.ns = ns
            h1 = _hs()
            self._start_diag = (t_reclaim, t_audio, t_api, 0, t_exec, t_init,
                                _ticks_diff(_ticks_ms(), t0),
                                h0[0], h1[0], h0[1], h1[1])
            self._print_run_diag("RUNERR", "err=%s" % self.cart_error)
            print("Moybyte cart %s:" % ("notice" if self.notice else "error"),
                  self.cart_error)
            return False
        self._lua = lua
        self._lua_split = getattr(lua, "frame_split", None)
        self._flag_put = getattr(lua, "flag_xor", None)
        self.cart_error = None
        self.crash_line = None
        self.crash_file = None
        self.ns = ns
        self._update = lua.update
        self._draw = lua.draw
        self._restore_bg = ns.get("_moy_restore_bg")
        h1 = _hs()
        self._start_diag = (t_reclaim, t_audio, t_api, 0, t_exec, t_init,
                            _ticks_diff(_ticks_ms(), t0),
                            h0[0], h1[0], h0[1], h1[1])
        self._print_run_diag("RUNSTART")
        return True

    def tick(self, dt, render=True):
        """The running-cart content (game domain): the logic ticks frame_plan
        scheduled for this loop frame, the cart's _draw when `render`, the mixer
        feed, then the crash chrome. Fills the
        per-frame perf split (ws._pf_*) the router's DRAWBRK accounting
        reads. Drawn on the fixed 320x240 GAME canvas, composited by the router.

        render=False is a logic-only frame (#217): the cart's ticks, audio and
        exit/textmode handling run as normal, but the backdrop restore, the cart's
        _draw and every chrome draw are skipped -- the game canvas keeps the last
        rendered frame's pixels and nothing composites or flushes this frame."""
        ws = self.ws
        if self._stopping:
            return                     # the VM stops at this frame's end
        if self._front and self._resume_from_front(ws):
            return
        if self._paused_at:
            return                     # the pause menu holds it: the frame stays
        _perf = ws.perf_hud or ws.perf_capture
        if self.cart_error is None:
            self._tick_keys(ws)
            fed = self._focus_editor
            gp = getattr(ws.input, "game_pointer", None) if fed is not None else None
            if gp is not None:
                fed.mouse_frame(ws.input, gp[0], gp[1], dt)   # its wheel and autoscroll
            # The cart's code prints and measures in petme128, whatever the
            # theme names (docs/theming_2026-09.md §4.7).
            _was = _cart_text(not self._look)
            try:
                # A RESPONSIVE app cart follows its surface (#181): a window
                # resize, a font-scale change or a mode switch re-runs `_layout`
                # BEFORE the frame that would draw against the new geometry.
                # Costs a game exactly one `is not None`, and an app three
                # compares -- there is no clock and no probe here.
                if self._app_layout is not None and self._layout_moved():
                    self._app_layout(*self._app_wh)
                # Declared background (#63): restore the cart's named backdrop BEFORE
                # its frame runs, so a naive cart draws only its actors. No-op (one
                # early-out) when the cart never called background(). Skipped on a
                # logic-only frame (nothing draws over it this frame).
                #
                # TIMED, and charged to RENDER, not chrome (#172, on-glass
                # 2026-08-02): this restore IS the cart's drawing (it stands in
                # for its first cls()), and letting it fall out of the bracket
                # once put ~4.7ms of Brick Siege's frame under CHROME and sent
                # #172 hunting the shell for a regression that was never there.
                if self._ui is not None and getattr(self, "_lua", None) is None:
                    self._ui_begin(ws, render)
                _tb = _ticks_us() if _perf else 0
                rb = self._restore_bg
                if render and rb is not None:
                    rb()
                bg = _ticks_diff(_ticks_us(), _tb) if _perf else 0
                # Multiplayer (#65): deliver any inbound net.* messages to the cart's
                # on_net handler BEFORE its _update runs (incoming shared state applied
                # first -- the lockstep-friendly order). Every frame, drawn or
                # not. No-op when the cart has no net permission.
                if self._net is not None:
                    self._net.pump()
                # The Editor's flag edits since the last frame, into the run's
                # own table (E-02: the cart's fset stays the run's).
                if self._flag_put is not None:
                    ws.project.flag_edits(self._flag_put)
                np = self._netplay
                stalled = False
                if np is not None:            # a match: its clock, its stall
                    dt, stalled, np = self._lockstep_step(ws, dt)
                # MICROSECONDS, not ms. These three brackets and the backdrop one
                # above feed DRAWBRK's split, and `chrome` is what is left after
                # subtracting them from the frame -- on a ms clock every one of
                # them truncated toward zero and the residual collected the whole
                # error. ws._pf_* are microsecond ints; _frame_perf_end divides
                # once, at the EMA, so every public number stays in ms.
                _ts = _ticks_us() if _perf else 0
                if np is not None:
                    self._run_ticks(0 if stalled else 1, dt, render)
                elif self.tick_ms:
                    self._run_ticks(self._n_ticks, self._tick_dt, render)
                else:
                    self._run_ticks(1, self._loop_dt(dt), render)
                _tm = _ticks_us() if _perf else 0
                if render and self._draw:
                    self._draw()
                    if self._ui is not None and getattr(self, "_lua", None) is None:
                        self._ui_close()
                _cart_text(_was)
                _td = _ticks_us() if _perf else 0
                if ws.audio_out is not None:
                    ws.audio_out.tick(dt)  # pull the mix where no feeder task does (#16)
                if _perf:
                    upd = _ticks_diff(_tm, _ts)           # cart _update -> game LOGIC
                    cart = _ticks_diff(_td, _tm) + bg     # cart _draw + backdrop -> RENDERING
                    # A runtime that runs the WHOLE cart frame inside update()
                    # (moycore: _update and _draw back to back in C) makes those
                    # two clocks read `logic = everything, render = 0`. It can
                    # still say where its own time went, in microseconds, so ask
                    # -- otherwise every logic/render pair this project has
                    # recorded since #67 becomes incomparable to the next one.
                    _fs = self._lua_split
                    if _fs is not None:
                        _sp = _fs()
                        if _sp is not None:
                            # frame_split keeps its ms contract (the glue's);
                            # this side is us, so convert rather than widening it.
                            # The draw half is the last tick's; the rest of the
                            # bracket is every logic tick this frame ran.
                            _dr = int(_sp[1] * 1000.0)
                            cart = _dr + bg
                            upd = upd - _dr if upd > _dr else 0
                    aud = _ticks_diff(_ticks_us(), _td)   # audio_out.tick (the pull)
                    ws._pf_upd = upd
                    ws._pf_cart = cart
                    ws._pf_audio = aud
                    ws._pf_bg = bg        # the backdrop's share of cart, for DRAWBRK
                    self._maybe_diag_slow_logic(upd, cart, aud)
                # CRASH GUARD heal (#160): a rendered frame that got here ran the
                # cart's body, its _init, its _update and its _draw without
                # raising. Outside the perf brackets above on purpose -- the
                # HEALING frame pays one small settings write, and charging that
                # to the cart's render slice would put a flash hitch in DRAWBRK
                # under the cart's name.
                if render and self._app_id is not None:
                    ws.app_guard.frame()
            except Exception as exc:  # noqa: BLE001
                _cart_text(_was)
                if self._capture_frame_error(ws, exc):
                    return
        self._tick_tail(ws, render)

    def _tick_keys(self, ws):
        """The keyboard edge for the cart's key()/keyp(), or for the focused
        editor handle that owns the keyboard instead."""
        # The keyboard edge for the cart's key()/keyp(): last_key is the
        # byte held this loop frame (0 when nothing is down); keyp fires on
        # the 0->key transition, and the edge LATCHES until a logic tick
        # takes it (#217). Done here (not in InputState) so it is
        # independent of whether the backend sets last_key before or after
        # begin_frame().
        k = ws.input.last_key
        fed = self._focus_editor
        if fed is None:
            ws.input.cart_key = k
            if k and k != self._cart_key_prev:
                self._keyp_latch = k
        else:
            # A FOCUSED editor handle owns the keyboard: the byte goes to
            # it and the cart's own key()/keyp() read nothing, so a skin
            # never has to filter the text its editor is eating.
            ws.input.cart_key = 0
            if k and k != self._cart_key_prev and not fed.key(k):
                self._keyp_latch = k    # a key it did not take (Esc) is the cart's
            # a held key types again at the input table's repeat: the
            # handle's, never the cart's key()/keyp()
            n = getattr(ws.input, "key_repeats", 0)
            if n:
                r = ws.input.repeat_key
                if r in (8, 9, 10, 13, 127) or 32 <= r < 127:
                    for _ in range(n):
                        fed.key(r)
        self._cart_key_prev = k

    def _lockstep_step(self, ws, dt):
        """The #65 lockstep gate for this frame: (dt, stalled, session). With
        no session dt is the loop's own, nothing stalls and session is None."""
        # LOCKSTEP (#65 Phase 2): the session owns the clock. `dt` becomes
        # the fixed tick -- a variable dt diverges the two sims on frame
        # one -- and a frame whose peer input has not arrived does NOT
        # simulate. It still DRAWS: the screen holds the last agreed
        # frame instead of freezing, which is what "waiting for player"
        # looks like on hardware that cannot extrapolate safely.
        stalled = False
        np = self._netplay
        if np is not None and self._play is not None:
            # A run in the kernel's Player: its lockstep is C -- the drain, the
            # due tick or the stall's retry, the advance, the resend between
            # ticks (moy_play_lockstep).
            run = _moy_play.current()
            if not run:
                # The run ended in the kernel's front (`end`):
                # nothing simulates, and this frame's Player takes the route.
                return np.dt, True, np
            n = _moy_play.lockstep(run)
            if n is not None:
                return np.dt, n == 0, np
        if np is not None:
            # Drain the radio BEFORE deciding whether this tick can
            # advance: the boards drain in the frame TAIL, so without
            # this the peer's input is up to a whole loop frame stale
            # by the time advance() looks for it. Measured on glass
            # (P4<->T-Deck, 2026-08-24): 8.0% -> 6.4% stalled ticks at
            # DELAY=2 from this call alone. Input-priority and
            # mid-frame-safe by contract -- see EspNowLink.drain_input.
            _di = self._drain_input
            if _di is not None:
                try:
                    _di()
                except Exception:  # noqa: BLE001 -- radio must not kill a frame
                    pass
            dt = np.dt
            # The session owns the CLOCK as well as the order: it ticks
            # at its fixed rate, not at whatever the board's frame loop
            # manages, or a fixed dt would silently run the game fast.
            _nowp = _ticks_ms()
            if np.due(_nowp) or np.waiting:
                # A STALLED tick retries every loop frame instead of
                # waiting out a whole tick: the missing input usually
                # lands a few ms after it was first needed, and the
                # wait-for-the-next-due schedule made a 5ms miss cost
                # 34ms (wait_med, on glass 2026-08-24). due() still
                # owns the cadence of healthy ticks.
                stalled = not np.advance(
                    _netplay_mask(ws.input, _NET_BUTTONS), _nowp)
            else:
                # Between ticks: do not simulate, but KEEP SENDING. The
                # frame loop is faster than the lockstep clock, so these
                # frames are free redundancy against a radio whose ack
                # lies -- and they serve a stalled peer sooner.
                stalled = True
                np.resend()
        return dt, stalled, np

    def _capture_frame_error(self, ws, exc):
        """A cart that raised mid-frame: capture it, stop the cart, persist
        pmem; the stop card then opens over its last frame (`_tick_tail`).
        Returns True when the screen left the run (a parked console goes
        home)."""
        # A cart that raises mid-frame must NOT escape the loop (the
        # device would hang silently). Capture it, stop running the
        # broken cart, and fall through to the stop card, whose See why
        # opens the code on the line.
        # mark the line on EDIT (#24): a Lua cart's line comes from the
        # error text's `cart:N:` position (#67 Phase 5); a Python cart's
        # from the traceback, mapped back through the nativize insert.
        if self._lua is not None and _compiled(ws.cart):
            # A trap: the run already cleared its canvas, and there is no
            # source line to mark (docs/wasm_tier_plan_2026-09.md).
            self.cart_error = _lua_err_text(exc)
            self.crash_file, self.crash_line = None, None
        elif self._lua is not None:
            self.cart_error = _lua_err_text(exc)
            self.crash_file, self.crash_line = _lua_cart_where(
                self.cart_error, ws.cart)
        else:
            self.cart_error = _err_text(exc)
            self.crash_line = self._map_crash_line(_exc_cart_line(exc))
            self.crash_file = None
        self._update = None
        self._draw = None
        self._disarm_pacing()  # the crash panel is a console screen
        # Print the _err_text-guarded string, never the raw `exc`: a
        # cart exception whose __str__ itself raises would otherwise
        # escape here -> the silent device hang the panel exists to prevent.
        print("Moybyte frame error:", self.cart_error)
        # Deferred pmem (#66): the crash must not eat saved progress --
        # the cart stops ticking now, so this is its last chance to
        # persist. Guarded: the panel must paint even if SD is gone.
        pm = getattr(ws, "pmem", None)
        if pm is not None:
            try:
                pm.flush()
            except Exception:  # noqa: BLE001
                pass
        ws._reset_canvas_state()
        if ws.web.parked:
            return ws._crash_to_code()
        return False

    def _tick_tail(self, ws, render):
        """After the cart's frame: the periodic pmem flush, a quit() exit,
        the text-mode sync, the canvas-state reset and the chrome a drawn
        frame paints over the cart."""
        # Deferred pmem periodic flush (#66, the Letter Blitz attribution): a
        # dirty pmem persists at most once per PMEM_FLUSH_MS while the cart
        # plays -- the guaranteed saves are exit (release_world) and the crash
        # capture above. Running here, after the frame's update/draw, the
        # ~100ms SD write lands at a frame boundary (one bounded hitch per
        # minute) instead of inside the logic slice on every pmem() call.
        if self.cart_error is None:
            if _ticks_diff(_ticks_ms(), self._pmem_last) >= PMEM_FLUSH_MS:
                self._pmem_last = _ticks_ms()
                pm = getattr(ws, "pmem", None)
                if pm is not None:
                    pm.flush()
        # A cart ENDS ITSELF by calling quit() (make_api), which sets input.cart_quit.
        # Honor it now that this frame's _update has run: pop to the run caller and stop
        # (screen leaves "desktop", so this cart won't tick again -- skip the text-mode
        # sync + chrome below). Any cart may call quit(); the console's own key (`start`)
        # opens the pause menu whatever the cart does. Reset the
        # cart-set camera/clip/pal first so whatever paints underneath is clean.
        if self.cart_error is None and getattr(ws.input, "cart_quit", False):
            ws.input.cart_quit = False
            ws._reset_canvas_state()
            ws._exit_to_caller()
            return
        # Cart text input (#38/#42): apply the keyboard mode the cart's _update may
        # have just requested via textmode(), so the NEXT keyboard poll yields the
        # right bytes (clean ASCII for typing, raw/game for hold-to-move). One-frame
        # latency; no-op on the host. Done every running-cart frame so a mid-cart
        # toggle (e.g. wifi entering/leaving its password screen) takes effect.
        ws._sync_cart_text_mode()
        # Clear any cart-set camera/clip/pal/palt (#11) before the console paints
        # its own UI overlays, so they're never offset/clipped/recoloured.
        ws._reset_canvas_state()
        # A logic-only frame (#217): nothing below draws pixels this frame --
        # the crash panel/tool bar all repaint on the next drawn frame
        # (a crash disarms the pacing itself, so the panel is never starved).
        if not render:
            return
        if self.cart_error is not None or self._is_tool:
            ws.settle_cart_frame()          # the chrome below draws over the frame
        # The bar auto-hides while a cart PLAYS (Stage 5): the game owns the full
        # 320x240 with NO chrome (the #71 pause frame is gone). A run that
        # stopped opens the stop card over its last frame (the pause card's
        # sibling, drawn by its layer on the system canvas). It opens inside
        # this draw, past the stack the frame is painting, so the frame owes
        # the next one, which paints it: a stopped run animates nothing else.
        if self.cart_error is not None:
            if not ws.pause.open:
                ws.pause.stop()
                ws.request_frame()
        elif self._is_tool and not ws.app_window_canvas:   # #75: cached at start()
            # Part 4: a TOOL/APP runs WITH a minimal bar (back + title + status) so
            # it's exitable -- a GAME stays fullscreen-bar-hidden. The bar-visibility-by-
            # type rule keys on the running cart's manifest type (ws._running_cart_shows_bar).
            ws._draw_tool_bar()
        # (The FPS chip + perf HUD is the game-domain _PerfLayer, drawn right after
        # this content -- still on the GAME canvas, before the composite.)

    def handle_input(self, i):
        # A running cart owns the full 320x240 and every button and key,
        # BACKSPACE included: its own btn()/key() calls in tick() read them in
        # parallel, and the Player takes none. The console's one key is `start`
        # (Super, a T-Deck's Mic), which the router reads before any layer and
        # which opens the pause menu over a game (`Workstation.system_key`). A
        # cart ends itself with quit(); a tool/app runs WITH a minimal bar and
        # exits through its context-X.
        return True

    def handle_pointer(self, px, py, click):
        # A running cart + the editors live in the 320x240 GAME viewport, so translate
        # the panel pointer into game coords (#39; identity in the degradation case).
        ws = self.ws
        sx, sy = px, py
        gx, gy = ws._game_xy(px, py)
        px, py = gx, gy
        # While a GAME plays the bar is hidden, so the game owns the full 320x240 and
        # every tap belongs to the cart (published as the game pointer by the router before
        # this runs; the pause menu and the stop card, a layer above, take them while they
        # show). One chrome case DOES hit-test a tap: a running TOOL/APP's minimal bar
        # (Part 4: its context-X / wifi / ≡), through a thin ws helper so this file never
        # reaches the bar surface directly.
        if click and self._is_tool and self.cart_error is None:
            if not ws.app_window_canvas and ws._tool_bar_tap(px, py, sx, sy):   # ‹ exits
                # The bar consumed the tap -> clear the published game pointer's tap so the
                # tool doesn't ALSO act on it this frame (mirrors the overlay-suppress rule).
                gp = ws.input.game_pointer
                ws.input.game_pointer = (gp[0], gp[1], False, False)
            # else the tap falls through to the tool (game pointer already published)
        elif click:
            if ws.show_fps and _in(px, py, ws.perf_ui._fps_tap_rect()):
                # Tapping the FPS readout toggles the frame-time breakdown HUD
                # (#43/#44 perf). Deliberate, no keyboard, doesn't fight game
                # input -- the touch lands on a small bottom-right corner box.
                ws.perf_hud = not ws.perf_hud
        return True

    # -- crash chrome (the Player's own UX) -----------------------------------

    @property
    def notice(self):
        """A notice's text while it is the panel up, else None: a compiled
        cart refused before it loaded, for want of room, of a signature or of
        an import this console has."""
        n = self._notice
        return n if n is not None and n == self.cart_error else None

    @property
    def newer(self):
        """True while the notice up is the newer-console one."""
        return self.notice is not None and self._notice_title == _ch.say_title(_ch.SAY_NEWER)
