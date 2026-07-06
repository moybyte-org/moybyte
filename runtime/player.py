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
    `_update(dt)`/`_draw()`/`audio.tick`, the crash capture, then the pause/crash
    chrome. It fills the DRAWBRK perf split (ws._pf_upd/_pf_cart/_pf_audio) exactly
    as the old content-layer body did -- that contract stays on `ws`.
  * `handle_input`/`handle_pointer` -- input + the #71 pause machinery (carried
    verbatim here so Stage 2 stays pixel-identical; retired in Stage 5).
  * the crash `_draw_error_panel` + the pause `_draw_pause_dim`/`_draw_pause_buttons`.

The BUNDLE the Player reaches (spec Section 2): the open `Project` (its live data),
plus the RAW canvas / input / audio / `make_api` / wifi through its `ws` back-ref --
the SAME raw objects `Workstation._start` handed `make_api` before, so the hot draw
path stays injected-direct (the Section 5 perf guardrail: WHO calls make_api moved,
WHAT it closes over did not). It deliberately does NOT reach the store, the shell
top bar, the home grid, or the layouts -- the bar draw/tap in the pause frame is
reached through thin `ws` helpers so this file never names them (a spike-test grep
enforces the isolation). The nine cart-run fields it owns are exposed back on
Workstation as forwarding properties, so every surface file + test is unchanged.

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


# Pause screen (#71 unified key): two explicit, always-tappable buttons so quitting
# is never ambiguous or keyboard-mode-dependent -- CONTINUE resumes, QUIT pops back
# to whoever launched the cart. Centered as a pair near the bottom of the 320x240
# game viewport, well clear of the top bar (y 0..18). Moved here with the pause
# machinery (Stage 2); re-exported from console.py so console._PAUSE_* still resolves
# for tests. (The whole pause surface is retired in Stage 5.)
_PAUSE_BTN_W = 92
_PAUSE_BTN_H = 22
_PAUSE_BTN_GAP = 12
_PAUSE_BTN_Y = 240 - 40
_PAUSE_CONTINUE_BTN = ((320 - 2 * _PAUSE_BTN_W - _PAUSE_BTN_GAP) // 2, _PAUSE_BTN_Y,
                       _PAUSE_BTN_W, _PAUSE_BTN_H)
_PAUSE_QUIT_BTN = (_PAUSE_CONTINUE_BTN[0] + _PAUSE_BTN_W + _PAUSE_BTN_GAP, _PAUSE_BTN_Y,
                   _PAUSE_BTN_W, _PAUSE_BTN_H)


class Player:
    """Runs one cart: start -> tick every frame -> guarantee exit (Stage 2). Holds a
    `ws` back-ref (the shared draw toolkit + services seam every surface uses) and is
    injected NAMES + `_in` like the other surfaces. The nine cart-run fields it owns
    (ns/_update/_draw/cart_error/crash_line/cart_paused/_bks_prev/_cart_start_ms/
    _cart_key_prev) are exposed back on Workstation as forwarding properties, so every
    reader of ws.cart_error/ws._update/... is byte-for-byte unchanged; they are reset
    per run in start()."""

    def __init__(self, ws, NAMES, _in):
        self.ws = ws
        self.NAMES = NAMES
        self._in = _in
        self.ns = None
        self._update = None
        self._draw = None
        self.cart_error = None        # last cart failure text -> on-canvas error panel
        self.crash_line = None        # 1-based cart line of the last runtime crash (#24)
        self.cart_paused = False      # pause menu (#71): a running cart owns the FULL
                                      # 320x240 (no bar); BACKSPACE -- THE one console
                                      # key in every input mode -- TOGGLES this. Quit
                                      # is the pause screen's own explicit QUIT button
        self._bks_prev = 0            # last_key edge tracker for the BACKSPACE pause
                                      # (a text-mode cart types letters, so button
                                      # aliases are suppressed -- the Player edge-
                                      # detects the console key itself)
        self._cart_start_ms = 0       # _ticks_ms when the running cart last start()ed
        self._cart_key_prev = 0       # last frame's keyboard byte (key()/keyp() edge)

    def start(self, project):
        """Start (or re-run) `project`'s cart under make_api. Resets the canvas draw
        state, stamps the cart clock, gates `wifi` by the manifest permission, execs
        the source, and captures _update/_draw. Any exception becomes cart_error/
        crash_line (returned False) so the caller opens to the desktop and frame()
        paints the on-canvas panel instead of hanging. Returns True iff it started."""
        ws = self.ws
        ws._dirty = True               # a (re)started cart paints its first frame (#44)
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
        try:
            # Compile with the "<cart>" filename so a runtime traceback carries
            # cart line numbers (_exc_cart_line reads them to mark the bad line).
            exec(compile(project.cart["src"], "<cart>", "exec"), ns)
            if ns.get("_init"):
                ns["_init"]()
        except Exception as exc:  # noqa: BLE001
            # The device's native run loop starves USB, so a print() never reaches
            # serial -- stash the failure so tick() can paint an on-canvas panel.
            # Print only the _err_text-guarded string, never the raw `exc`: a cart
            # exception whose __str__ itself raises would otherwise escape here and
            # become the exact silent device hang the panel exists to prevent.
            self.cart_error = _err_text(exc)
            self.crash_line = _exc_cart_line(exc)
            print("Moybyte cart error:", self.cart_error)
            return False
        self.cart_error = None
        self.crash_line = None
        self.ns = ns
        self._update = ns.get("_update")
        self._draw = ns.get("_draw")
        return True

    def tick(self, dt):
        """The running-cart content (game domain): tick the cart _update/_draw + mixer
        (the game loop), then the pause/crash chrome. Fills the per-frame perf split
        (ws._pf_*) the router's DRAWBRK/CHROMEBRK accounting reads. Drawn on the fixed
        320x240 GAME canvas, composited by the router."""
        ws = self.ws
        _perf = ws.perf_hud or ws.perf_capture
        if self.cart_paused and self.cart_error is None:
            # Paused (#71): the cart is frozen -- no _update, no _draw; the
            # canvas retains its last frame as the backdrop. Keep the mixer
            # fed so a mid-flight note decays instead of sticking.
            if ws.audio is not None:
                ws.audio.tick(dt)
        elif self.cart_error is None:
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
                self.crash_line = _exc_cart_line(exc)   # mark the line on EDIT (#24)
                self._update = None
                self._draw = None
                # Print the _err_text-guarded string, never the raw `exc`: a
                # cart exception whose __str__ itself raises would otherwise
                # escape here -> the silent device hang the panel exists to prevent.
                print("Moybyte frame error:", self.cart_error)
        # Cart text input (#38/#42): apply the keyboard mode the cart's _update may
        # have just requested via textmode(), so the NEXT keyboard poll yields the
        # right bytes (clean ASCII for typing, raw/game for hold-to-move). One-frame
        # latency; no-op on the host. Done every running-cart frame so a mid-cart
        # toggle (e.g. wifi entering/leaving its password screen) takes effect.
        ws._sync_cart_text_mode()
        # Clear any cart-set camera/clip/pal/palt (#11) before the console paints
        # its own UI overlays, so they're never offset/clipped/recoloured.
        ws._reset_canvas_state()
        if self.cart_error is not None:
            self._draw_error_panel()
        # The bar auto-hides while a cart PLAYS (#71): the game owns the full
        # 320x240. Chrome appears only in the pause menu (BACKSPACE, or the
        # web page's menu button) -- and on a crash, so EDIT/CODE stay reachable.
        # The top bar is the shell's (not the Player's), so its draw + _pf_bar
        # (CHROMEBRK) accounting stay on ws; the Player just asks for it here.
        if self.cart_paused or self.cart_error is not None:
            if self.cart_paused:
                self._draw_pause_dim()          # scanline shade UNDER the chrome
            ws._draw_cart_bar()                 # unified top bar (tool switcher)
            if self.cart_paused:
                self._draw_pause_buttons()
        # (The FPS chip + perf HUD is the game-domain _PerfLayer, drawn right after
        # this content -- still on the GAME canvas, before the composite.)

    def handle_input(self, i):
        # THE ONE CONSOLE KEY (#71): BACKSPACE/HOME does exactly ONE thing
        # in every input mode, every cart type, paused or not: TOGGLE the
        # pause screen. No special case -- it never means "exit" (that's a
        # separate, explicit, ALWAYS-TAPPABLE action: the CONTINUE/QUIT
        # buttons drawn on the pause screen itself, see _draw_pause_buttons
        # + handle_pointer). Raw-matrix and typed-ASCII carts deliver it as
        # the "home" button (input backends map it); a TEXT-MODE cart
        # suppresses ALL button aliases (so a typed letter is never
        # mistaken for a shortcut), so here we edge-detect last_key
        # ourselves to catch backspace specifically -- but the RESULT is
        # identical either way: flip cart_paused.
        #
        # An earlier version tried to make the SAME key also distinguish
        # "exit" from "resume" (a second press from pause = quit), and
        # separately tried treating typed Z/space/Enter/R as "resume" --
        # both were special cases that fell over in practice: Z and R are
        # live GAMEPLAY LETTERS in a typing game, and a keyboard-only
        # "press again to quit" is a different rule per cart type. One
        # button, one job, plus explicit on-screen buttons for the
        # deliberate action (quitting) is simpler and cannot be confused.
        ws = self.ws
        _bks = False
        if getattr(ws.input, "text_mode", False) and ws.cart is not None:
            k = ws.input.last_key
            _bks = (k == 0x08 and k != self._bks_prev
                    and (self.cart_paused or ws.cart.get("type") == "game"))
            self._bks_prev = k
        else:
            self._bks_prev = 0
        if i.pressed("home") or i.pressed("stop") or _bks:
            self.cart_paused = not self.cart_paused
            ws._dirty = True
        elif self.cart_paused:
            if i.pressed("a") or i.pressed("run"):
                self.cart_paused = False   # CONTINUE accelerator (button only)
                ws._dirty = True
            elif i.pressed("b"):
                ws._open_menu()
        # NOTE: no unpaused B handler -- while a cart PLAYS every button
        # belongs to the game (Star Catcher moves with B; the old
        # B->editor shortcut hijacked it). The editor is reachable from
        # the pause menu (B / bar icons) exactly like the other tools.
        return True

    def handle_pointer(self, px, py, click):
        # A running cart + the editors live in the 320x240 GAME viewport, so translate
        # the panel pointer into game coords (#39; identity in the degradation case).
        ws = self.ws
        gx, gy = ws._game_xy(px, py)
        px, py = gx, gy
        # While a cart PLAYS the bar is hidden (#71) -- the game owns the full
        # 320x240 and every tap belongs to the cart. The unified TOP BAR (HOME,
        # EDIT/CODE, PAINT, MAP, BLOCKS icons -- the TIC-80 one-tap tool
        # switcher) hit-tests only in the PAUSE menu (BACKSPACE, or the web
        # page's menu button) and on the crash panel, where it is drawn.
        chrome = self.cart_paused or self.cart_error is not None
        if click and chrome:
            # The top-bar tool switcher (drawn by the shell) consumes the tap iff a
            # tool icon was hit; the Player routes it through a thin ws helper (so
            # this file never reaches the bar surface directly), otherwise the pause
            # QUIT/CONTINUE handling runs.
            if ws._cart_bar_tap(px, py):
                pass
            elif self.cart_paused and self._in(px, py, _PAUSE_QUIT_BTN):
                # The pause screen's explicit QUIT button (#71): the ONE
                # deliberate way to exit, identical for every cart type --
                # never inferred from a keyboard key, so it's never
                # ambiguous with a typing game's own letters. Pops back to
                # whoever launched this cart (Stage 2: the home root).
                ws._exit_to_caller()
            elif self.cart_paused:
                # CONTINUE: the QUIT button's rect is excluded above, so a
                # tap ANYWHERE else (including the CONTINUE button itself)
                # resumes play -- and must NOT leak into the cart as a
                # game tap on the same frame.
                self.cart_paused = False
                ws._dirty = True
                ws.input.game_pointer = (gx, gy, False, False)
        elif click:
            if ws.show_fps and self._in(px, py, ws.perf_ui._fps_tap_rect()):
                # Tapping the FPS readout toggles the frame-time breakdown HUD
                # (#43/#44 perf). Deliberate, no keyboard, doesn't fight game
                # input -- the touch lands on a small bottom-right corner box.
                ws.perf_hud = not ws.perf_hud
        return True

    # -- crash + pause chrome (the Player's own UX -- it guarantees the exit) --

    def _draw_error_panel(self):
        # A friendly on-canvas crash report (the device never reaches serial, so
        # this is the ONLY error surface). Drawn with the indexed API only: a red
        # box + a short title + the exception text, word-wrapped and truncated to
        # fit. The CODE/EDIT button below it stays live so the kid can fix the cart.
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

    def _draw_pause_dim(self):
        """Darken the frozen cart frame. The canvas is indexed (no alpha), so the
        shade is 50% scanlines -- every other row black, the classic CRT-era pause
        look. Idempotent across repaints (black rows stay black), so no entry-once
        latch; the bar and the pill draw AFTER it and stay full-bright."""
        cv = self.ws.canvas
        NAMES = self.NAMES
        for y in range(0, cv.h, 2):
            cv.rect(0, y, cv.w, 1, NAMES["black"])

    def _draw_pause_buttons(self):
        """The pause screen's two EXPLICIT actions (#71), over the dimmed frame:
        CONTINUE (resume; also any tap outside QUIT, or A/RUN) and QUIT (pop to the
        home root; ONLY this button does that -- never inferred from a keyboard key,
        so a typing game's own letters can never be mistaken for it)."""
        cv = self.ws.canvas
        NAMES = self.NAMES
        title = "PAUSED"
        cv.print(title, (cv.w - len(title) * 8) // 2, _PAUSE_BTN_Y - 14,
                  NAMES["white"], 1)
        for rect, label in ((_PAUSE_CONTINUE_BTN, "CONTINUE"), (_PAUSE_QUIT_BTN, "QUIT")):
            x, y, w, h = rect
            cv.rect(x, y, w, h, NAMES["black"])
            cv.rectb(x, y, w, h, NAMES["light_grey"])
            cv.print(label, x + (w - len(label) * 8) // 2, y + (h - 8) // 2,
                      NAMES["white"], 1)
