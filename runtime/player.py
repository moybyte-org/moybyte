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
    `_update(dt)`/`_draw()`/`audio.tick`, the crash capture, then the crash chrome +
    the TRANSIENT hold-to-exit toast. It fills the DRAWBRK perf split
    (ws._pf_upd/_pf_cart/_pf_audio) exactly as the old content-layer body did -- that
    contract stays on `ws`.
  * `handle_input`/`handle_pointer` -- input + the Stage-5 EXIT model: for a GAME a
    sustained hold-BACKSPACE (~700ms) pops to the run caller (`ws._exit_to_caller`);
    the #71 pause machinery it replaced is gone. (Tool/app carts run WITH a minimal
    bar and exit via its context-X instead -- see Workstation; their hold gesture is
    suppressed so BACKSPACE stays a free text key.)
  * the crash `_draw_error_panel` + the transient `_draw_hold_progress` toast.

The BUNDLE the Player reaches (spec Section 2): the open `Project` (its live data),
plus the RAW canvas / input / audio / `make_api` / wifi through its `ws` back-ref --
the SAME raw objects `Workstation._start` handed `make_api` before, so the hot draw
path stays injected-direct (the Section 5 perf guardrail: WHO calls make_api moved,
WHAT it closes over did not). It deliberately does NOT reach the store, the shell
top bar, the home grid, or the layouts -- the crash-frame bar draw/tap is reached
through thin `ws` helpers so this file never names them (a spike-test grep enforces
the isolation). The cart-run fields it owns are exposed back on Workstation as
forwarding properties, so every surface file + test is unchanged.

Canonical home is runtime/; build.sh stages a copy into the firmware modules/ tree
so the device freezes it (same pattern as console.py/project.py). It stays a leaf --
the tiny `_ticks_*`/`_err_text`/`_wrap` helpers are defined here (as widgets.py and
the other leaf surfaces define their own copies) and NAMES/_in are injected -- so it
imports nothing back into console (no circular import). The one cross-module value it needs
is the code line-height for the crash panel, imported from the code editor leaf
(bare name on the device / once host_app has aliased it, `runtime.X` for a direct
test load).
"""

try:
    from code_layer import _CODE_LH
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.code_layer import _CODE_LH

# #65 Phase 2: the lockstep frame's local-input packer. The BUTTON ORDER comes
# from cart_api (its one author) and is handed to the session, so netplay.py can
# stay the import-free leaf players.py is.
try:
    from netplay import mask_of as _netplay_mask
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.netplay import mask_of as _netplay_mask
try:
    from cart_api import CART_BUTTONS as _NET_BUTTONS
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cart_api import CART_BUTTONS as _NET_BUTTONS


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
    "moybyte.docs",       # #78 Desk Lab interop: table(name) / text(name)
    "moybyte.images",     # #63: image(name) / Image paint-image assets
    "moybyte.net",        # #65: net.send / on_net (also permission-gated)
    "moybyte.wifi",       # #38: the injected wifi service (permission-gated)
)


try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_us, _ticks_diff
    from widgets import _err_text
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_us, _ticks_diff
    from runtime.widgets import _err_text

# USER APPS (#181, ui_refactor_2026-08 Phase 7): the permission-keyed filter over
# AppContext, plus the responsive opt-in probe. A leaf like the rest of what this
# file imports -- it holds no Workstation, only the context FACTORY it is handed.
try:
    from system_api import make_system_api, manifest_error, wants_layout
except ImportError:                     # host: the runtime package
    from runtime.system_api import (make_system_api, manifest_error,
                                     wants_layout)


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


def _lua_err_text(exc):
    """_err_text minus lupa's appended "stack traceback:" block (#67 Phase 5).
    The device moy_lua text never has one (lua_pcall runs without a message
    handler), so trimming keeps the panel the same kid-short one-liner on both
    backends; the raise position (`cart:N:`) lives in the message head."""
    t = _err_text(exc)
    p = t.find("\nstack traceback:")
    return t if p < 0 else t[:p]


def _lua_cart_line(text, chunk="cart"):
    """Best-effort: the 1-based cart line inside a Lua error text (#67 Phase 5).
    Both backends load the cart chunk as "@cart" (lua_host loadstring /
    device_api moy_lua.exec), so a load or raise position renders `cart:12:`;
    a plain-named chunk renders `[string "cart"]:12:` -- both parsed. The FIRST
    position in the text is the raise point (any traceback frames come after
    it), the deepest-frame rule the Python parser applies. No regex: this runs
    frozen on MicroPython like its Python twin above."""
    if not text:
        return None
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
                    return int(num)
            p = s.find(pat, p + 1)
    return None


def _wrap(text, cols):
    """Word-wrap `text` into a list of lines no wider than `cols` chars. A single
    word longer than `cols` is hard-split so it still fits the panel."""
    if cols < 1:
        cols = 1
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            while len(word) > cols:                 # hard-split an over-long token
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:cols])
                word = word[cols:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= cols:
                line = line + " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


# The Player's EXIT gesture (Stage 5 of docs/history/shell_ux_technical_plan_v1.md, spec
# Section 9): the #71 pause machinery is GONE. A running GAME owns the full 320x240
# with NO chrome, and BACKSPACE is a plain key the cart reads. Exit is a single
# deliberate gesture that pops to the run caller:
#   * hold BACKSPACE ~700ms -- raw-matrix mode (keyboard fw >= 2025-06-12) streams the
#     held "home" key each frame; a small TRANSIENT progress toast fills as you hold,
#     and the pop fires at the threshold.
# A single quick BACKSPACE tap is NOT the gesture -- it just reaches the cart as a
# key/button. In TEXT mode "home" is never asserted (BACKSPACE arrives as a typed 0x08
# the cart's key() reads -- e.g. the wifi password field's DELETE), so the gesture
# never fires there: exactly the "backspace = delete, zero special-casing" the spec
# wants. (The old triple-tap fw-independent alias was DROPPED after on-device testing --
# tools/apps now run WITH a minimal bar whose context-X exits them, so a cart is never
# strandable-but-for-reboot without needing a keyboard fallback.)
_HOLD_EXIT_MS = 700         # sustained BACKSPACE hold to exit


# Compiled-code cache (#66 exec-arena fix, see Player.start): one entry per
# cart, keyed by the source's (len, hash) -- so the native emitter runs once
# per source VERSION, not once per PLAY. Module-level: survives Player runs,
# dies with the VM (exactly the arena's own lifetime).
_CODE_CACHE = {}


class Player:
    """Runs one cart: start -> tick every frame -> guarantee exit (Stage 2). Holds a
    `ws` back-ref (the shared draw toolkit + services seam every surface uses) and is
    injected NAMES + `_in` like the other surfaces. The cart-run fields it owns
    (ns/_update/_draw/cart_error/crash_line/_cart_start_ms/_cart_key_prev) are exposed
    back on Workstation as forwarding properties, so every reader of ws.cart_error/
    ws._update/... is byte-for-byte unchanged; they are reset per run in start()."""

    def __init__(self, ws, NAMES, _in):
        self.ws = ws
        self.NAMES = NAMES
        self._in = _in
        self.ns = None
        self._update = None
        self._draw = None
        self.cart_error = None        # last cart failure text -> on-canvas error panel
        self.crash_line = None        # 1-based cart line of the last runtime crash (#24)
        self._cart_start_ms = 0       # _ticks_ms when the running cart last start()ed
        self._cart_palette_canvas = None  # the canvas _cart_palette came off
        self._cart_key_prev = 0       # last frame's keyboard byte (key()/keyp() edge)
        self._cart_palette = None     # default table saved while a cart's own
                                      # manifest palette (spec 2.2) is applied
        # Stage 5 exit-gesture state (spec Section 9). Reset per run in start() so a
        # fresh cart never inherits a stale half-gesture.
        self._home_held_since = 0     # _ticks_ms when "home" (BACKSPACE) began being held
        self._home_holding = False    # True while "home" is held -> draw the TRANSIENT
                                      # hold-progress toast (ONLY while holding, Section 12)
        # #75: the tool/app-vs-game decision is a per-run CONSTANT (the manifest type
        # can't change mid-run), cached at start() so the per-frame tick/input paths
        # don't re-derive it through ws._running_cart_shows_bar (a property-forward
        # chain) on the sacred play path. Combined with the live cart_error check at
        # each use site it answers exactly what _running_cart_shows_bar answers.
        self._is_tool = False
        # USER APP state (#181), all per-run and all reset in start():
        self._app_layout = None       # the cart's `_layout(w, h, fs)`, when it opted into
                                      # the RESPONSIVE canvas -- None for every game and
                                      # every fixed-canvas app (the default)
        self._app_wh = None           # the (w, h, fs) _layout was last told about, so a
                                      # window resize / font-scale change re-runs it and a
                                      # steady frame costs one tuple compare
        self._app_id = None           # the crash guard's key for this run (#160), or None
                                      # when the run is not guarded
        self._restore_bg = None       # #63: the api's declared-background restore hook
        self._lua = None              # #67: the running "lua" cart's runtime state (a
                                      # ws.lua_runtime handle; _close_lua() on exit so a
                                      # cart's whole Lua heap dies with its run)
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
        return (cv.w, cv.h,
                ws.look.effective_font_scale() if ws.app_full_canvas else 1)

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

    def _reset_exit_state(self):
        """Clear the hold timer (a fresh run, or the moment the exit gesture completes
        -- so re-entering a cart starts from a clean slate)."""
        self._home_held_since = 0
        self._home_holding = False

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

    def _close_lua(self):
        """Tear down the previous run's Lua state (#67), if any. Idempotent and
        exception-proof: a close() failure must never block the next start or
        the exit path -- the state is unreachable either way and gc finishes it."""
        lua = self._lua
        self._lua = None
        if lua is not None:
            try:
                lua.close()
            except Exception:  # noqa: BLE001
                pass

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
        # A USER APP's crash-guard arming dies with its run. Any STRIKE it took
        # stands -- an exit before the heal is precisely the evidence kept.
        self._app_layout = None
        self._app_wh = None
        if self._app_id is not None:
            self._app_id = None
            self.ws.app_guard.release()
        # Nor may its SOUND. The device mixer is a global the cart only ever
        # posts notes to -- libmoy keeps sequencing a looping sfx or music track
        # long after the run that started it is gone, so beeper's tones and
        # celeste's music went on playing over whatever the kid did next
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
        if ns:
            try:
                ns.clear()
            except Exception:  # noqa: BLE001
                pass
        self.ns = None
        self._update = None
        self._draw = None
        self._restore_bg = None
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
        # ...and the radio goes down with it: nobody to talk to, and power save
        # back on. UNLESS a session is already arranged for the run about to
        # start -- which is not a hypothetical: the way a match forms is that the
        # peer's invite arrives while this console is already playing, sets
        # ws.netplay, and re-runs the cart from frame zero. The dying run tears
        # down here on the way through, and stopping the radio then killed the
        # very session that triggered the restart. On glass that read as the host
        # stalling at frame 0 forever while the guest played on alone, with
        # nothing logged anywhere (#65, 2026-08-22). The clause above has already
        # cleared ws.netplay if the session belonged to THIS run, so what
        # survives here belongs to the next one.
        _link = getattr(self.ws, "link", None)
        if _link is not None and self.ws.netplay is None:
            try:
                _link.stop()
            except Exception:  # noqa: BLE001 -- teardown must never block an exit
                pass
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
        try:
            import gc
            gc.collect()           # off the play path: the run just ended
        except Exception:  # noqa: BLE001
            pass
        self._diag_frag()          # #66: the heap the NEXT cart inherits

    def _diag_frag(self, tag="MEMX"):
        """One line per cart exit: the largest allocatable block and total free
        on the heap the next cart inherits -- #66's repeat-run fragmentation as
        a number instead of a feel. MicroPython's GC does not compact, so freed
        bytes pile into holes and the largest CONTIGUOUS run -- what a layer or
        the p8 machine's 64KB must fit -- shrinks over a session even while
        total free does not. Emitted after release_world's collect, so it reads
        the compact-as-it-gets state, and carries `exit=` (the run counter) so
        the series over repeated opens IS the degradation curve.

        The block is BINARY-SEARCHED, which #66 line 1125 prescribes for a
        reason: `mem_free()` reads high on these ports (it folds in a potential
        PSRAM split), so only a probe that actually allocates is honest. Each
        trial drops at once and a failing one auto-collects before it raises, so
        the search perturbs nothing -- and it runs between carts with nothing
        else live. Off unless measurement mode is on, like every line here."""
        if not self._diag_enabled():
            return
        try:
            import gc
            gc.collect()
            free = gc.mem_free()
            lo, hi = 0, min(free if free > 0 else (1 << 20), 8 << 20)
            while hi - lo > 1024:
                mid = (lo + hi) // 2
                try:
                    bytearray(mid)          # unnamed: collectable at once
                    lo = mid
                except MemoryError:
                    hi = mid
            big = lo
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
        try:
            au = getattr(self.ws, "audio", None)
            fn = getattr(au, "diag_state", None)
            if fn is None:
                return "audio=na"
            st = fn()
            if st is None:
                return "audio=na"
            return ("audio(seq=%d core1=%d reused=%d running=%d mask=%d committed=%d)"
                    % (st[0], st[1], st[2], st[3], st[4], st[5]))
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
        if upd_ms < 10 or not self._diag_enabled():
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
        ws._dirty = True               # a (re)started cart paints its first frame (#44)
        self._reset_exit_state()       # a fresh run drops any half-done exit gesture
        # USER APP per-run state (#181): cleared here rather than at the bind
        # site below, so the early SPEC refusals (extensions / canvas) cannot
        # leave a previous app's `_layout` armed against this cart.
        self._app_layout = None
        self._app_wh = None
        self._app_id = None
        ws.input.game_view = None      # the `view(w, h)` verb is per-run (cart_quit
                                       # pattern): a cart re-declares it each start
        # #85: a fresh run resets the active scene to the default, so a load_scene()
        # switch never leaks across a re-run (the "resets on next _init" semantics).
        _sc = getattr(project, "scenes", None)
        if _sc is not None:
            try:
                _sc.reset()
            except Exception:  # noqa: BLE001 -- scene reset must never block a run
                pass
        self._close_lua()              # a re-run replaces the previous run's Lua state
        self._pmem_last = t0           # periodic pmem flush counts from this run's start
        self._slow_logic_next = 0
        # #75: cache the bar-visibility-by-type rule for this run (see __init__).
        cart = project.cart
        self._is_tool = (cart is not None
                         and cart.get("type") in ("tool", "app"))
        # Required-extension gate (moy SPEC.md 10): a cart listing an extension
        # this build doesn't implement is refused cleanly -- the normal error
        # panel -- instead of crashing partway into a frame on a missing verb.
        missing = [e for e in ((cart.get("extensions") or ()) if cart else ())
                   if e not in SUPPORTED_EXTENSIONS]
        if missing:
            self.cart_error = "needs extension: " + ", ".join(missing)
            self.crash_line = None
            return False
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
            self.cart_error = 'no "%.32s" canvas (SPEC.md 3.1)' % (cv,)
            self.crash_line = None
            return False
        ws.release_run_canvas()        # a straggler bind never leaks into this run
        if cv is not None and not ws.bind_run_canvas(cv[0], cv[1]):
            self.cart_error = "no %dx%d canvas on this screen yet" % (cv[0], cv[1])
            self.crash_line = None
            return False
        # -- USER APPS (#181, ui_refactor_2026-08 Phases 7 + 8) ---------------
        #
        # A `type: "app"` cart no shell app claims is a USER APP: written by
        # whoever owns the console, editable in the picker, and handed shell
        # capabilities by MANIFEST PERMISSION (make_system_api, below).
        if ws.is_user_app(cart):
            # A manifest this build cannot honour AS WRITTEN is refused by
            # name, like an unknown runtime or an out-of-set canvas -- and
            # BEFORE the crash guard arms, because a mis-declared permission is
            # not a crash and must not spend a strike. Today the only such rule
            # is "at most one file kind"; the alternative was the silent
            # last-one-wins grant this replaced.
            _man = manifest_error(cart)
            if _man is not None:
                self.cart_error = _man
                self.crash_line = None
                return False
            # CRASH ISOLATION FIRST, before a single line of the cart's code has
            # been compiled, let alone run: the mark has to survive a death the
            # interpreter never observes (a hang, an OOM, a native fault). See
            # runtime/crash_guard.py. A cart that has used up its strikes is
            # refused into the ORDINARY error panel, whose top bar carries
            # EDIT/CODE -- so "this app is broken" and "here is how you fix it"
            # are the same screen.
            self._app_id = ws.app_cart_id(cart)
            if not ws.app_guard.arm(self._app_id):
                self.cart_error = ("app turned off after %d crashes - EDIT it"
                                   % ws.app_guard.STRIKES)
                self.crash_line = None
                self._app_id = None
                return False
            # THE RESPONSIVE OPT-IN. Fixed is the DEFAULT and deliberately so:
            # a kid cart hardcodes coordinates, and the manifest `canvas` (320x240
            # unless it says otherwise) is what the WM centres and integer-scales.
            # A cart that defines a top-level `_layout(w, h, fs)` is saying it can
            # reflow, and gets the whole system surface instead. Probed from the
            # SOURCE because the canvas has to be chosen before make_api closes
            # over it -- see system_api.wants_layout.
            if wants_layout(cart.get("src")):
                ws.bind_app_canvas()
        # #63 leak fix: the PREVIOUS cart is dead -- return its pooled layer buffers
        # (make_layer worlds, the Fold-2 map cache) for reuse before the new run
        # allocates. Probe: the host Canvas has no pool (gc reclaims its layers).
        rl = getattr(ws.canvas, "reclaim_layers", None)
        if rl is not None:
            rl("cart")
        t_reclaim = _ticks_diff(_ticks_ms(), t0)
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
        # Capability-permission gate (#38): hand make_api the wifi backend ONLY
        # when this cart declares the "network" permission, so a normal kid cart
        # gets NO `wifi` name (sandbox preserved). make_api injects `wifi` into the
        # cart namespace iff the backend it receives is non-None.
        wifi = ws.wifi if ws._cart_has_perm("network") else None
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
        if net is not None:
            net.reset()
        self._net = net
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
        ns = ws.make_api(ws.canvas, ws.input, project.config, project.sheet,
                         ws.audio, project.tilemap, project.pmem, wifi, project.images,
                         project.scenes,    # #85: scene()/load_scene() over the cart's scenes
                         tables=project.tables, texts=project.texts,  # #78 interop
                         net=net,           # #65: capability-gated net.* backend
                         gpio=gpio,         # #9: capability-gated physical pins
                         flags=project.flags)   # SPEC.md 3.5 tile flags (fget/fset)
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
        # ungated riders (`ui`, `theme()`, `screen()`, `bar_h()`) are how an app
        # draws, not what it may reach -- see runtime/system_api.py.
        if self._app_id is not None:
            ns.update(make_system_api(ws.app_context, cart, ws.canvas,
                                      ws.app_bar_h))
        t_api = _ticks_diff(_ticks_ms(), t2)
        # Compile with the "<cart>" filename so a runtime traceback carries cart
        # line numbers (_exc_cart_line reads them to mark the bad line). #67 spike:
        # prefer the AUTO-NATIVE rewrite (machine code per top-level def) when the
        # emitter exists; if it refuses the cart at compile time, fall back to the
        # pristine bytecode compile -- the flag can never break a cart. The
        # inserted-line map keeps crash lines exact (#24) either way.
        src = project.cart["src"]
        # #67 dual-runtime seam (Phase 2): a non-python cart never touches the
        # Python compile / auto-native / code-cache path below -- it starts
        # through the injected Lua runtime instead (same ns, same error panel).
        _rt = project.cart.get("runtime", "python")
        if _rt != "python":
            return self._start_lua(_rt, ns, src, t0, h0,
                                   (t_reclaim, t_audio, t_api))
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
            t_init = _ticks_diff(_ticks_ms(), t6)
        except Exception as exc:  # noqa: BLE001
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so tick() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            self.crash_line = self._map_crash_line(_exc_cart_line(exc))
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
        self._print_run_diag("RUNSTART")
        return True

    def _start_lua(self, runtime, ns, src, t0, h0, t_pre):
        """Start a "runtime": "lua" cart (#67 Phase 2) through the injected
        `ws.lua_runtime` factory -- runtime/lua_host.py (lupa) on the host, the
        moy_lua native module on the device once Phase 1 lands. The cart gets
        the SAME make_api namespace a Python cart got (the factory registers
        those callables as the cart's Lua globals), so permission gating, pmem,
        audio and quit() semantics are identical by construction. No
        auto-native, no code cache -- those are Python-compiler concerns. A
        missing runtime or a Lua load/_init error lands on the normal cart
        error panel (crash-line mapping for Lua tracebacks is Phase 5)."""
        ws = self.ws
        t_reclaim, t_audio, t_api = t_pre
        # Same measurement-mode gate as start(): no heap walks in kid mode.
        _hs = _heap_stats if self._diag_enabled() else (lambda: (-1, -1))
        self._native_ins = None        # RUNSTART diag: no auto-native on this path
        self._native_fail = None
        make_lua = getattr(ws, "lua_runtime", None)
        t5 = _ticks_ms()
        t_exec = -1
        t_init = -1
        lua = None
        try:
            if runtime != "lua":
                raise ValueError("unknown cart runtime '%s'" % runtime)
            if make_lua is None:
                # The graceful floor: a lua cart on a build without the runtime
                # (today: every device build) opens the panel, never a hang.
                raise RuntimeError("needs the Lua runtime "
                                   "(not in this build yet)")
            lua = make_lua(ns, src)
            t_exec = _ticks_diff(_ticks_ms(), t5)
            t6 = _ticks_ms()
            if lua.init is not None:
                lua.init()
            t_init = _ticks_diff(_ticks_ms(), t6)
        except Exception as exc:  # noqa: BLE001 -- load/_init error -> the panel
            if lua is not None:
                try:
                    lua.close()
                except Exception:  # noqa: BLE001
                    pass
            self.cart_error = _lua_err_text(exc)
            # a load/syntax or _init error carries its `cart:N:` position, so
            # EDIT drops on the line exactly like a Python SyntaxError (#24)
            self.crash_line = _lua_cart_line(self.cart_error)
            self.ns = ns
            h1 = _hs()
            self._start_diag = (t_reclaim, t_audio, t_api, 0, t_exec, t_init,
                                _ticks_diff(_ticks_ms(), t0),
                                h0[0], h1[0], h0[1], h1[1])
            self._print_run_diag("RUNERR", "err=%s" % self.cart_error)
            print("Moybyte cart error:", self.cart_error)
            return False
        self._lua = lua
        self.cart_error = None
        self.crash_line = None
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
        """The running-cart content (game domain): tick the cart _update/_draw + mixer
        (the game loop), then the crash chrome + the transient hold-to-exit toast. Fills
        the per-frame perf split (ws._pf_*) the router's DRAWBRK/CHROMEBRK accounting
        reads. Drawn on the fixed 320x240 GAME canvas, composited by the router.

        render=False is the #77 frameskip's logic-only tick (ws.frame's skip frames):
        the cart's _update + audio + exit/textmode handling run as normal, but the
        backdrop restore, the cart's _draw and every chrome draw are skipped -- the
        game canvas keeps the last rendered frame's pixels and nothing composites or
        flushes this frame, so input/logic hold the full loop rate while the render
        cost is halved."""
        ws = self.ws
        _perf = ws.perf_hud or ws.perf_capture
        if self.cart_error is None:
            # Resolve this frame's keyboard edge for the cart's key()/keyp():
            # last_key is the byte held this frame (0 when nothing is down);
            # keyp fires only on the 0->key transition. Done here (not in
            # InputState) so it's independent of whether the backend sets
            # last_key before or after begin_frame().
            k = ws.input.last_key
            ws.input.cart_key = k
            ws.input.cart_keyp = k if (k and k != self._cart_key_prev) else 0
            self._cart_key_prev = k
            try:
                # A RESPONSIVE app cart follows its surface (#181): a window
                # resize, a font-scale change or a world flip re-runs `_layout`
                # BEFORE the frame that would draw against the new geometry.
                # Costs a game exactly one `is not None`, and an app one tuple
                # build plus a compare -- there is no clock and no probe here.
                if self._app_layout is not None:
                    _wh = self._layout_args()
                    if _wh != self._app_wh:
                        self._app_wh = _wh
                        self._app_layout(*_wh)
                # Declared background (#63): restore the cart's named backdrop BEFORE
                # its frame runs, so a naive cart draws only its actors. No-op (one
                # early-out) when the cart never called background(). Skipped on a
                # frameskip logic-only tick (nothing draws over it this frame).
                #
                # TIMED, and charged to RENDER, not chrome (#172, on-glass
                # 2026-08-02): this restore IS the cart's drawing (it stands in
                # for its first cls()), and letting it fall out of the bracket
                # once put ~4.7ms of Brick Siege's frame under CHROME and sent
                # #172 hunting the shell for a regression that was never there.
                _tb = _ticks_us() if _perf else 0
                rb = self._restore_bg
                if render and rb is not None:
                    rb()
                bg = _ticks_diff(_ticks_us(), _tb) if _perf else 0
                # Multiplayer (#65): deliver any inbound net.* messages to the cart's
                # on_net handler BEFORE its _update runs (incoming shared state applied
                # first -- the lockstep-friendly order). Every logic tick, incl. a
                # frameskip logic-only frame. No-op when the cart has no net permission.
                if self._net is not None:
                    self._net.pump()
                # LOCKSTEP (#65 Phase 2): the session owns the clock. `dt` becomes
                # the fixed tick -- a variable dt diverges the two sims on frame
                # one -- and a frame whose peer input has not arrived does NOT
                # simulate. It still DRAWS: the screen holds the last agreed
                # frame instead of freezing, which is what "waiting for player"
                # looks like on hardware that cannot extrapolate safely.
                stalled = False
                np = self._netplay
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
                # MICROSECONDS, not ms (2026-08-14). These three brackets and the
                # backdrop one above feed DRAWBRK's split, and CHROMEBRK's `other`
                # is what is left after subtracting them from the frame -- so on a
                # ms clock every one of them truncated toward zero and the
                # remainder collected the whole error. Six quantized terms, each
                # losing up to 1ms, is up to 6ms of PURE ARTEFACT in a bucket that
                # read ~7.6ms and was being treated as a real cost to hunt.
                # ws._pf_* are microsecond ints now; _frame_perf_end divides once,
                # at the EMA, so every public number stays in ms.
                _ts = _ticks_us() if _perf else 0
                if self._update and not stalled:
                    self._update(dt)
                _tm = _ticks_us() if _perf else 0
                if render and self._draw:
                    self._draw()
                _td = _ticks_us() if _perf else 0
                if ws.audio is not None:
                    ws.audio.tick(dt)      # advance/feed playback (#16)
                if _perf:
                    upd = _ticks_diff(_tm, _ts)           # cart _update -> game LOGIC
                    cart = _ticks_diff(_td, _tm) + bg     # cart _draw + backdrop -> RENDERING
                    # A runtime that runs the WHOLE cart frame inside update()
                    # (moycore: _update and _draw back to back in C) makes those
                    # two clocks read `logic = everything, render = 0`. It can
                    # still say where its own time went, in microseconds, so ask
                    # -- otherwise every logic/render pair this project has
                    # recorded since #67 becomes incomparable to the next one.
                    _fs = getattr(self._lua, "frame_split", None)
                    if _fs is not None:
                        _sp = _fs()
                        if _sp is not None:
                            # frame_split keeps its ms contract (lua_host twins it);
                            # this side is us, so convert rather than widening it.
                            upd = int(_sp[0] * 1000.0)
                            cart = int(_sp[1] * 1000.0) + bg
                    aud = _ticks_diff(_ticks_us(), _td)   # audio.tick (mixer feed)
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
                # A cart that raises mid-frame must NOT escape the loop (the
                # device would hang silently). Capture it, stop running the
                # broken cart, and fall through to paint the error panel; the
                # desktop buttons stay so the kid can EDIT/CODE the fix.
                # mark the line on EDIT (#24): a Lua cart's line comes from the
                # error text's `cart:N:` position (#67 Phase 5); a Python cart's
                # from the traceback, mapped back through the nativize insert.
                if self._lua is not None:
                    self.cart_error = _lua_err_text(exc)
                    self.crash_line = _lua_cart_line(self.cart_error)
                else:
                    self.cart_error = _err_text(exc)
                    self.crash_line = self._map_crash_line(_exc_cart_line(exc))
                self._update = None
                self._draw = None
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
                # Owner ask 2026-07-23: no parked OOPS screen -- throw the kid
                # straight into the code editor on the crashing line (popup +
                # inline marker). The panel below survives only as the
                # fallback when there is nothing to edit.
                ws._reset_canvas_state()
                if ws._crash_to_code():
                    return
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
        # sync + chrome below). This is the exit a TEXT-mode cart MUST provide -- once it
        # calls textmode(True), hold-BACKSPACE can't reach it (BACKSPACE is a typed delete
        # there, no keyboard autorepeat) -- but ANY cart may call quit(). Reset the
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
        # Frameskip logic-only tick (#77): nothing below draws pixels this frame --
        # the crash panel/tool bar/hold toast all repaint on the next rendered frame
        # (a crash mid-skip disables the skip gate itself: ws.frame requires
        # cart_error None to skip, so the panel is never starved).
        if not render:
            return
        # The bar auto-hides while a cart PLAYS (Stage 5): the game owns the full
        # 320x240 with NO chrome (the #71 pause frame is gone). The ONLY chrome left
        # is the CRASH panel + its top bar, so EDIT/CODE stay reachable to fix the cart.
        # The top bar is the shell's (not the Player's), so its draw + _pf_bar
        # (CHROMEBRK) accounting stay on ws; the Player just asks for it here.
        if self.cart_error is not None:
            self._draw_error_panel()
            ws._draw_cart_bar()                 # unified top bar (crash tool switcher)
        elif self._is_tool:                     # #75: cached at start(); error is None here
            # Part 4: a TOOL/APP runs WITH a minimal bar (title + status + context-X) so
            # it's exitable -- a GAME stays fullscreen-bar-hidden. The bar-visibility-by-
            # type rule keys on the running cart's manifest type (ws._running_cart_shows_bar).
            ws._draw_tool_bar()
        elif self._home_holding:
            # The TRANSIENT hold-to-exit affordance (spec Section 12): drawn ONLY while
            # BACKSPACE is being held, never on a plain play frame -- so a frame with no
            # hold in flight draws exactly the cart's pixels and nothing else (no chrome
            # re-added to the sacred 50fps path).
            self._draw_hold_progress()
        # (The FPS chip + perf HUD is the game-domain _PerfLayer, drawn right after
        # this content -- still on the GAME canvas, before the composite.)

    def handle_input(self, i):
        # Stage 5 EXIT model (spec Section 9): the #71 pause machinery is GONE. A running
        # GAME owns the full 320x240 and every button/key -- BACKSPACE ("home") is a plain
        # key the cart reads via its OWN btn()/key() calls in tick() (a parallel read; the
        # Player never steals it from the cart). The Player only WATCHES "home" for the
        # deliberate exit gesture: hold ~700ms -> pop to the run caller (ws._exit_to_caller).
        # Raw-matrix mode streams the held key, so a sustained hold is a rising elapsed-
        # since-first-held; a transient progress toast fills as you hold. A single quick tap
        # is NOT the gesture -> it just reaches the cart. In TEXT mode "home" is never
        # asserted (BACKSPACE arrives as a typed 0x08 the cart's key() reads -- the wifi
        # password field's DELETE), so the gesture never fires there: the "backspace =
        # delete, zero special-casing" the spec wants.
        #
        # The hold gesture is for GAMES only. A tool/app runs WITH a minimal bar and exits
        # via its context-X (ws._running_cart_shows_bar()), so its hold is suppressed --
        # BACKSPACE stays a free text key (the wifi password field's DELETE).
        ws = self.ws
        if self._is_tool and self.cart_error is None:   # #75: cached type + live error
            if self._home_holding:              # drop any in-flight hold (no toast on a tool)
                self._home_holding = False
                self._home_held_since = 0
                ws._dirty = True
            return True
        now = _ticks_ms()
        if i.held("home"):
            if not self._home_holding:
                self._home_holding = True
                self._home_held_since = now
                ws._dirty = True               # the toast just appeared -> repaint
            elif _ticks_diff(now, self._home_held_since) >= _HOLD_EXIT_MS:
                self._reset_exit_state()
                ws._exit_to_caller()           # hold complete: pop to the caller
                return True
            else:
                ws._dirty = True               # the toast fills each held frame
        elif self._home_holding:
            self._home_holding = False         # released before the threshold -> toast gone
            self._home_held_since = 0
            ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        # A running cart + the editors live in the 320x240 GAME viewport, so translate
        # the panel pointer into game coords (#39; identity in the degradation case).
        ws = self.ws
        gx, gy = ws._game_xy(px, py)
        px, py = gx, gy
        # While a GAME plays the bar is hidden (Stage 5 retired the pause screen), so the
        # game owns the full 320x240 and every tap belongs to the cart (published as the
        # game pointer by the router before this runs). Two chrome cases DO hit-test a tap:
        # the CRASH panel's top bar (HOME / EDIT|CODE / PAINT / MAP / BLOCKS / MUSIC -- the
        # fix-it tool switcher) and -- Part 4 -- a running TOOL/APP's minimal bar (its
        # context-X / wifi / ≡). Both route through thin ws helpers so this file never
        # reaches the bar surface directly.
        if click and self.cart_error is not None:
            ws._cart_bar_tap(px, py)            # crash-bar tool switcher (EDIT/CODE reachable)
        elif click and self._is_tool:           # #75: cached type; error is None past the if
            if ws._tool_bar_tap(px, py):        # tool bar: X exits, wifi/≡ shortcuts
                # The bar consumed the tap -> clear the published game pointer's tap so the
                # tool doesn't ALSO act on it this frame (mirrors the overlay-suppress rule).
                gp = ws.input.game_pointer
                ws.input.game_pointer = (gp[0], gp[1], False, False)
            # else the tap falls through to the tool (game pointer already published)
        elif click:
            if ws.show_fps and self._in(px, py, ws.perf_ui._fps_tap_rect()):
                # Tapping the FPS readout toggles the frame-time breakdown HUD
                # (#43/#44 perf). Deliberate, no keyboard, doesn't fight game
                # input -- the touch lands on a small bottom-right corner box.
                ws.perf_hud = not ws.perf_hud
        return True

    # -- crash chrome + the transient exit toast (the Player's own UX) --------

    def _draw_error_panel(self, cv=None):
        # A friendly on-canvas crash report (the device never reaches serial, so
        # this is the ONLY error surface). Drawn with the indexed API only: a red
        # box + a short title + the exception text, word-wrapped and truncated to
        # fit. The CODE/EDIT button below it stays live so the kid can fix the cart.
        # `cv` defaults to the GAME canvas (a crashed running cart); the system-
        # domain cards tab passes ws.sys_canvas so its defensive fallback stays
        # visible on a distinct system canvas (#39 step 3).
        if cv is None:
            cv = self.ws.canvas
        NAMES = self.NAMES
        # Sized against the surface, not 320x240: a cart-declared small canvas
        # (SPEC.md 1/3.1) still gets a panel that fits (chunky once upscaled,
        # but readable and inside the raster).
        w = min(292, cv.w - 12)
        h = min(132, cv.h - 16)
        x = (cv.w - w) // 2
        y = min(40, (cv.h - h) // 2)
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["red"])
        cv.rect(x, y, w, 14, NAMES["red"])
        cv.print("Your game stopped.", x + 6, y + 4, NAMES["white"], 1)
        cols = (w - 16) // 8                       # 8px monospace cells
        lines = _wrap(self.cart_error or "Unknown error", cols)
        max_rows = (h - 30) // _CODE_LH
        for i in range(min(len(lines), max_rows)):
            cv.print(lines[i], x + 8, y + 20 + i * _CODE_LH, NAMES["peach"], 1)
        cv.print("TAP CODE TO SEE WHY", x + 8, y + h - 12, NAMES["yellow"], 1)

    def _draw_hold_progress(self):
        """The TRANSIENT hold-to-exit affordance (Stage 5, spec Section 12): a small
        pill near the top that fills as BACKSPACE is held toward _HOLD_EXIT_MS. Drawn
        ONLY while the hold is in flight -- handle_input clears _home_holding on release
        or exit, so it appears on the first held frame and vanishes on release, like a
        toast; it is NEVER a persistent per-frame overlay on the play frame. Indexed-API
        only (host == device). The label sits above a thin progress bar so neither
        obscures the other."""
        cv = self.ws.canvas
        NAMES = self.NAMES
        w, h = min(128, cv.w - 8), 16    # fits a cart-declared small canvas too
        x = (cv.w - w) // 2
        y = 6
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, NAMES["light_grey"])
        label = "HOLD TO EXIT"
        cv.print(label, x + (w - len(label) * 8) // 2, y + 2, NAMES["white"], 1)
        fill = int((w - 4) * self._hold_frac())
        if fill > 0:
            cv.rect(x + 2, y + h - 4, fill, 2, NAMES["yellow"])

    def _hold_frac(self):
        """0..1 progress of the current BACKSPACE hold toward _HOLD_EXIT_MS (0 when no
        hold is in flight, capped at 1 so the fill never overruns the pill)."""
        if not self._home_holding:
            return 0.0
        el = _ticks_diff(_ticks_ms(), self._home_held_since)
        if el <= 0:
            return 0.0
        f = el / _HOLD_EXIT_MS
        return 1.0 if f > 1.0 else f
