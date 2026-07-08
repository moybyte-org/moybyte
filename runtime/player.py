"""The cart PLAYER -- the run-loop black box (Stage 2 of
docs/shell_ux_technical_plan_v1.md).

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

import time

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


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _err_text(exc):
    """A short, kid-readable one-liner for an exception (type: message). Robust
    on MicroPython, whose exceptions sometimes stringify oddly."""
    try:
        name = type(exc).__name__
    except Exception:  # noqa: BLE001
        name = "Error"
    try:
        msg = str(exc)
    except Exception:  # noqa: BLE001
        msg = ""
    return (name + ": " + msg) if msg else name


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


# The Player's EXIT gesture (Stage 5 of docs/shell_ux_technical_plan_v1.md, spec
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
        self._cart_key_prev = 0       # last frame's keyboard byte (key()/keyp() edge)
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
        self._restore_bg = None       # #63: the api's declared-background restore hook
        self._native_ins = None       # #67 spike: nativize's inserted-line map (crash-line fix)

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

    def start(self, project):
        """Start (or re-run) `project`'s cart under make_api. Resets the canvas draw
        state, stamps the cart clock, gates `wifi` by the manifest permission, execs
        the source, and captures _update/_draw. Any exception becomes cart_error/
        crash_line (returned False) so the caller opens to the desktop and frame()
        paints the on-canvas panel instead of hanging. Returns True iff it started."""
        ws = self.ws
        ws._dirty = True               # a (re)started cart paints its first frame (#44)
        self._reset_exit_state()       # a fresh run drops any half-done exit gesture
        # #75: cache the bar-visibility-by-type rule for this run (see __init__).
        cart = project.cart
        self._is_tool = (cart is not None
                         and cart.get("type") in ("tool", "app"))
        # #63 leak fix: the PREVIOUS cart is dead -- return its pooled layer buffers
        # (make_layer worlds, the Fold-2 map cache) for reuse before the new run
        # allocates. Probe: the host Canvas has no pool (gc reclaims its layers).
        rl = getattr(ws.canvas, "reclaim_layers", None)
        if rl is not None:
            rl("cart")
        project._build_audio()
        # Reset the canvas draw state (camera/clip/pal/palt, #11) so a fresh cart run
        # never inherits a previous cart's clip rect or palette swap.
        rs = getattr(ws.canvas, "reset_state", None)
        if rs is not None:
            rs()
        # Stamp the cart-start clock so the cart's time() reads ms since this run
        # began (re-run on apply/run_code/edit-close resets it, like TIC-80).
        self._cart_start_ms = _ticks_ms()
        ws.input.cart_start_ms = self._cart_start_ms
        # Capability-permission gate (#38): hand make_api the wifi backend ONLY
        # when this cart declares the "network" permission, so a normal kid cart
        # gets NO `wifi` name (sandbox preserved). make_api injects `wifi` into the
        # cart namespace iff the backend it receives is non-None.
        wifi = ws.wifi if ws._cart_has_perm("network") else None
        ns = ws.make_api(ws.canvas, ws.input, project.config, project.sheet,
                         ws.audio, project.tilemap, project.pmem, wifi, project.images)
        # Compile with the "<cart>" filename so a runtime traceback carries cart
        # line numbers (_exc_cart_line reads them to mark the bad line). #67 spike:
        # prefer the AUTO-NATIVE rewrite (machine code per top-level def) when the
        # emitter exists; if it refuses the cart at compile time, fall back to the
        # pristine bytecode compile -- the flag can never break a cart. The
        # inserted-line map keeps crash lines exact (#24) either way.
        src = project.cart["src"]
        self._native_ins = None
        code = None
        if NATIVE_CARTS:
            nsrc, ins = _nativize(src)
            ns["micropython"] = _micropython   # the decorator's global, no import line
            try:
                code = compile(nsrc, "<cart>", "exec")
                self._native_ins = ins
            except Exception:  # noqa: BLE001 -- emitter limitation -> bytecode path
                code = None
        try:
            if code is None:
                # The kid's own syntax error surfaces HERE -> the friendly panel.
                code = compile(src, "<cart>", "exec")
            exec(code, ns)
            if ns.get("_init"):
                ns["_init"]()
        except Exception as exc:  # noqa: BLE001
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so tick() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            self.crash_line = self._map_crash_line(_exc_cart_line(exc))
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
        return True

    def tick(self, dt):
        """The running-cart content (game domain): tick the cart _update/_draw + mixer
        (the game loop), then the crash chrome + the transient hold-to-exit toast. Fills
        the per-frame perf split (ws._pf_*) the router's DRAWBRK/CHROMEBRK accounting
        reads. Drawn on the fixed 320x240 GAME canvas, composited by the router."""
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
                # Declared background (#63): restore the cart's named backdrop BEFORE
                # its frame runs, so a naive cart draws only its actors. No-op (one
                # early-out) when the cart never called background().
                rb = self._restore_bg
                if rb is not None:
                    rb()
                _ts = _ticks_ms() if _perf else 0
                if self._update:
                    self._update(dt)
                _tm = _ticks_ms() if _perf else 0
                if self._draw:
                    self._draw()
                _td = _ticks_ms() if _perf else 0
                if ws.audio is not None:
                    ws.audio.tick(dt)      # advance/feed playback (#16)
                if _perf:
                    ws._pf_upd = _ticks_diff(_tm, _ts)    # cart _update -> game LOGIC
                    ws._pf_cart = _ticks_diff(_td, _tm)   # cart _draw -> RENDERING
                    ws._pf_audio = _ticks_diff(_ticks_ms(), _td)  # audio.tick (mixer feed)
            except Exception as exc:  # noqa: BLE001
                # A cart that raises mid-frame must NOT escape the loop (the
                # device would hang silently). Capture it, stop running the
                # broken cart, and fall through to paint the error panel; the
                # desktop buttons stay so the kid can EDIT/CODE the fix.
                self.cart_error = _err_text(exc)
                # mark the line on EDIT (#24; mapped back through the nativize insert)
                self.crash_line = self._map_crash_line(_exc_cart_line(exc))
                self._update = None
                self._draw = None
                # Print the _err_text-guarded string, never the raw `exc`: a
                # cart exception whose __str__ itself raises would otherwise
                # escape here -> the silent device hang the panel exists to prevent.
                print("Moybyte frame error:", self.cart_error)
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
        x, y, w, h = 14, 40, 292, 132
        cv.rect(x, y, w, h, NAMES["dark_purple"])
        cv.rectb(x, y, w, h, NAMES["red"])
        cv.rect(x, y, w, 14, NAMES["red"])
        cv.print("OOPS! THIS CART CRASHED", x + 6, y + 4, NAMES["white"], 1)
        cols = (w - 16) // 8                       # 8px monospace cells
        lines = _wrap(self.cart_error or "Unknown error", cols)
        max_rows = (h - 30) // _CODE_LH
        for i in range(min(len(lines), max_rows)):
            cv.print(lines[i], x + 8, y + 20 + i * _CODE_LH, NAMES["peach"], 1)
        cv.print("TAP CODE TO FIX IT", x + 8, y + h - 12, NAMES["yellow"], 1)

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
        w, h = 128, 16
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
