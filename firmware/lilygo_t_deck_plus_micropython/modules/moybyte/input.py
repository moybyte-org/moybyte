import time


BUTTONS = (
    "up",
    "down",
    "left",
    "right",
    "a",
    "b",
    "x",
    "y",
    "run",
    "stop",
    "home",
    "save",
    "share",
    "select",
    "start",
)


class InputState:
    def __init__(self):
        self._held = set()
        self._last = set()
        self._pressed = set()
        self._released = set()
        self.last_key = 0

    def begin_frame(self):
        self._pressed = self._held - self._last
        self._released = self._last - self._held
        self._last = set(self._held)

    def release_all(self):
        self._held.clear()

    def set_button(self, name, held):
        if name not in BUTTONS:
            raise ValueError("unknown button: " + name)
        if held:
            self._held.add(name)
        else:
            self._held.discard(name)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed

    def released(self, name):
        return name in self._released


class TDeckKeyboard:
    KEYBOARD_ADDR = 0x55
    KEY_HOLD_MS = 260
    RAW_MODE_CMD = b"\x03"     # LILYGO_KB_MODE_RAW_CMD: stream the raw key matrix
    KEY_MODE_CMD = b"\x04"     # LILYGO_KB_MODE_KEY_CMD: back to 1-byte ASCII
    # Use the raw key matrix for true hold-to-move while a cart runs. The matrix
    # is the only way a *held* direction keeps firing -- the 1-byte ASCII path
    # reports each key once on the press edge (no autorepeat), so the hold-latch
    # below can only fake it for KEY_HOLD_MS and then movement stalls. Raw mode
    # needs T-Deck keyboard firmware >= 2025-06-12; on older firmware the 0x03
    # command is ignored, the keyboard keeps sending ASCII, and _read_raw_buttons
    # detects that and sticks the session back on the ASCII + latch path. Set this
    # False to force the ASCII path regardless of firmware.
    RAW_GAME_MODE = True
    # #69 experiment knob: the esp32 machine.I2C default clock-stretch timeout is
    # 50000us -- and I2CSTAT sized the stalls on hardware (2026-07-04): kbd max
    # 21.6-59.8ms, touch max 41ms, several >20ms per play session, maxima hugging
    # that 50ms ceiling. 5000 caps a stretch at 5ms: a stall becomes a <=5ms
    # failed read (caught -> empty buttons for ONE frame) instead of a felt
    # 60ms input freeze. The touch shares this bus/I2C object, so the knob
    # governs both. Set None to restore the driver default (the A/B revert).
    I2C_TIMEOUT_US = 5000
    # #69 per-session I2C latency stats, updated by _timed_read on every keyboard
    # transaction and read by the I2CSTAT diag line (moy_runtime): total reads,
    # worst-case us (+ which mode it happened in), and how many crossed 5ms / 20ms.
    # The HITCH kbd= column only sees stalls inside >80ms frames; this sees ALL.
    # Class-level defaults so every construction path starts zeroed; the first
    # update shadows them per-instance.
    stat_n = 0
    stat_max_us = 0
    stat_max_raw = False
    stat_over5 = 0
    stat_over20 = 0
    stat_timeouts = 0     # reads that RAISED (capped stalls) -- one stale frame each
    # A capped stall (#69) raises on ONE read; only this many consecutive
    # failures mean the keyboard is genuinely gone (see _read_error).
    ERR_RUN_LIMIT = 10
    _err_run = 0
    _raw_last = ((), 0)   # (buttons, key) held across a capped stall -- see below
    # #69 input-poller thread: when a poller owns the I2C bus, set_game_mode must
    # not write I2C from the main thread -- it queues the target here and the
    # poller applies it between reads (apply_pending_mode).
    _poller_owned = False
    _want_game = None

    def __init__(self, input_state):
        self.input = input_state
        self.available = False
        self.raw_mode = False
        self._raw_unsupported = False   # set once if 0x03 was ignored (old firmware)
        self._i2c = None
        self._held_buttons = ()
        self._held_until_ms = 0
        try:
            from machine import I2C, Pin

            if self.I2C_TIMEOUT_US is None:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
            else:
                self._i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000,
                                timeout=self.I2C_TIMEOUT_US)
            self._i2c.readfrom(self.KEYBOARD_ADDR, 1)
            # Boot in 1-byte ASCII mode (clean ASCII; verified by the keyboard
            # probe). set_game_mode(True) switches to the raw matrix (0x03) while a
            # cart runs so a *held* direction keeps firing, and set_game_mode(False)
            # restores ASCII (0x04) for the code editor -- sending 0x04 is the revert
            # an earlier attempt missed, which is why raw mode used to garble editor
            # text irreversibly. __init__ never enables raw (the editor/launcher must
            # come up in ASCII); the console toggles it per screen.
            self.available = True
        except Exception as exc:
            print("Moybyte keyboard unavailable:", exc)

    def poll(self):
        """One synchronous keyboard poll: hardware read + InputState apply. The
        two halves are split (#69) so the input-poller thread can run the I2C
        half (_read_stage) off the frame loop while the main thread applies the
        staged result (_apply) -- poll() is the unthreaded path and behaves
        exactly as it always did."""
        self._apply(self._read_stage())

    def _read_stage(self):
        """The I2C half of a poll: touch the hardware and update kbd-INTERNAL
        state only (hold latch, raw fallback detection) -- it must NEVER write
        self.input (that's _apply's job, on the main thread). Returns the staged
        (buttons_tuple, key) to apply. Runs on the poller thread when threaded,
        inline from poll() when not."""
        if self.raw_mode:
            return self._read_raw_buttons()
        now_ms = _ticks_ms()
        key = self._read_key()
        if key != 0:
            self._held_buttons = self._buttons_for_key(key)
            self._held_until_ms = now_ms + self.KEY_HOLD_MS
        elif _ticks_diff(self._held_until_ms, now_ms) <= 0:
            self._held_buttons = ()
        return (self._held_buttons, key)

    def _apply(self, staged):
        """The state half of a poll: write a staged (buttons, key) result into
        the shared InputState. Cheap (no I2C), always on the main thread, so the
        console's begin_frame edge math never races the poller's bus reads."""
        buttons, key = staged
        self.input.last_key = key
        # Text mode (a cart's textmode(True) / the code editor): report the key but do
        # NOT also fire its game-button alias (w/a/s/d/z/x -> up/left/down/right/a/b),
        # or a typed password/name would also trigger d-pad + A/B shortcut actions
        # (#38/#42). Clear any latched buttons and stop here -- key()/keyp() still work.
        # (Raw mode is never active on a text screen; the guard keeps parity with the
        # old inline poll(), which only text-gated the ASCII path.)
        if not self.raw_mode and getattr(self.input, "text_mode", False):
            self._held_buttons = ()
            self.input.release_all()
            return
        self.input.release_all()
        for button in buttons:
            self.input.set_button(button, True)

    def set_game_mode(self, on):
        """Switch the keyboard between raw-matrix (on=True: true held-state for a
        running cart) and 1-byte ASCII (on=False: the code editor / launcher). The
        console calls this on every screen change; it is idempotent and only talks
        to the keyboard on a real transition. Honoured only when RAW_GAME_MODE is
        set and the firmware actually supports raw -- otherwise it stays on ASCII
        and the hold-latch fallback applies."""
        if not self.available or self._i2c is None:
            return
        if self._poller_owned:
            # #69: the input-poller thread owns the I2C bus -- queue the target
            # and let the poller apply it between reads (a main-thread write here
            # could collide with a poller read mid-stall). Queuing the RAW target
            # (not the resolved want) keeps the RAW_GAME_MODE/_raw_unsupported
            # resolution in one place, at apply time (_resolve_raw_want).
            self._want_game = bool(on)
            return
        self._apply_raw_want(self._resolve_raw_want(on))

    def apply_pending_mode(self):
        """#69 POLLER THREAD ONLY: apply a set_game_mode target the main thread
        queued (see above). Runs between reads so mode-switch I2C writes happen
        on the one thread that owns the bus."""
        w = self._want_game
        if w is None:
            return
        self._want_game = None
        self._apply_raw_want(self._resolve_raw_want(w))

    def _resolve_raw_want(self, on):
        """Resolve a set_game_mode(on) request into the actual raw-mode target:
        honours RAW_GAME_MODE (the force-ASCII override) and _raw_unsupported
        (old-firmware fallback) in exactly one place, whichever thread calls in."""
        return bool(on) and self.RAW_GAME_MODE and not self._raw_unsupported

    def _apply_raw_want(self, want):
        """Send the mode-switch I2C command, only on a real transition."""
        if want == self.raw_mode:
            return
        if want:
            self._enable_raw_mode()      # sends 0x03; sets raw_mode True on success
        else:
            self._disable_raw_mode()     # sends 0x04; back to 1-byte ASCII

    def _disable_raw_mode(self):
        try:
            self._i2c.writeto(self.KEYBOARD_ADDR, self.KEY_MODE_CMD)
        except Exception as exc:  # noqa: BLE001
            print("Moybyte keyboard mode revert failed:", exc)
        self.raw_mode = False
        self._held_buttons = ()

    def _timed_read(self, nbytes):
        """The one place a keyboard I2C transaction happens: readfrom + #69 latency
        stats (a 5-byte read at 400kHz is ~135us nominal; anything in the ms range
        is the C3 clock-stretching or bus contention -- exactly what I2CSTAT sizes)."""
        t0 = _ticks_us()
        data = self._i2c.readfrom(self.KEYBOARD_ADDR, nbytes)
        el = _ticks_diff(_ticks_us(), t0)
        self.stat_n += 1
        if el > self.stat_max_us:
            self.stat_max_us = el
            self.stat_max_raw = self.raw_mode
        if el >= 5000:
            self.stat_over5 += 1
            if el >= 20000:
                self.stat_over20 += 1
        return data

    def _read_error(self, exc, label):
        """One failed keyboard I2C read (#69): with the I2C_TIMEOUT_US cap a C3
        clock-stretch now RAISES (ETIMEDOUT) instead of blocking 50ms -- that is
        ONE dropped input frame by design, not a dead keyboard. Only a solid run
        of consecutive failures (a genuinely absent/wedged keyboard -- the case
        the old immediate disable protected against) turns the session off."""
        self._err_run += 1
        self.stat_timeouts += 1
        if self._err_run >= self.ERR_RUN_LIMIT:
            print("Moybyte keyboard %s failed repeatedly:" % label, exc)
            self.available = False

    def _read_key(self):
        if not self.available or self._i2c is None:
            return 0
        try:
            data = self._timed_read(1)
            self._err_run = 0
            if data:
                return data[0]
        except Exception as exc:
            self._read_error(exc, "read")
        return 0

    def _enable_raw_mode(self):
        try:
            self._i2c.writeto(self.KEYBOARD_ADDR, self.RAW_MODE_CMD)
            self.raw_mode = True
        except Exception:
            self.raw_mode = False

    def _read_raw_buttons(self):
        """One raw-matrix read -> staged (buttons_tuple, key). Kbd-internal state
        only (fallback detection, held-stall memory); the caller (_apply, via
        _read_stage) writes InputState."""
        try:
            data = self._timed_read(5)
            self._err_run = 0
        except Exception as exc:
            # Transient (a capped stall): keep raw_mode -- next frame reads again --
            # and HOLD the last known matrix state for this one frame. Returning ()
            # would release-then-repress a held button across the gap, which btnp()
            # reads as a spurious second press edge (double-jump/double-shot). Held
            # state one frame stale is invisible; a phantom edge is not. Only the
            # consecutive-failure limit ends the session (see _read_error).
            self._read_error(exc, "raw read")
            return self._raw_last
        if len(data) < 5:
            self.raw_mode = False
            self._raw_last = ((), 0)
            return self._raw_last
        if data[1] == 0 and data[2] == 0 and data[3] == 0 and data[4] == 0:
            key = data[0]
            buttons = self._buttons_for_key(key) if key > 0x20 else ()
            if buttons:
                # A printable ASCII byte arrived while we asked for raw: the
                # firmware ignored 0x03 (pre-2025-06-12). Stick the session on the
                # 1-byte ASCII + latch path so set_game_mode stops retrying raw.
                self.raw_mode = False
                self._raw_unsupported = True
                self._held_buttons = buttons
                self._held_until_ms = _ticks_ms() + self.KEY_HOLD_MS
                return (buttons, key)

        d0, d1, _d2, d3, d4 = data[0], data[1], data[2], data[3], data[4]
        buttons = []
        key = 0
        if (d0 & 0x08) or (d3 & 0x02):
            buttons.append("left")
            key = ord("a")
        if (d1 & 0x04) or (d4 & 0x02):
            buttons.append("right")
            key = ord("d")
        if (d0 & 0x02) or (d4 & 0x40):
            buttons.append("up")
            key = ord("w")
        if (d1 & 0x02) or (d3 & 0x40):
            buttons.append("down")
            key = ord("s")
        if (d1 & 0x20) or (d0 & 0x20) or (d3 & 0x08):
            buttons.append("a")
            key = ord("z")
        if d1 & 0x10:
            buttons.append("b")
            key = ord("x")
        if _d2 & 0x01:
            buttons.append("run")
            key = ord("r")
        # q and e are PLAIN LETTERS now (readable via key()/keyp() like the other
        # decoded letters) -- their old home/stop chrome roles made them stolen
        # keys (#71). THE one console key in every input mode is BACKSPACE
        # (matrix [4][3] -> d4 bit 3, the byte the C3 streams per column): it maps
        # to "home" here exactly like typed 0x08 does on the ASCII path, so pause
        # is the same physical key whether a cart runs raw, ASCII or text mode.
        if d0 & 0x01:
            key = ord("q")
        if d1 & 0x01:
            key = ord("e")
        if d4 & 0x08:
            buttons.append("home")
            key = 0x08
        self._raw_last = (tuple(buttons), key)  # held across a capped stall (see above)
        return self._raw_last

    def _buttons_for_key(self, key):
        if key in (ord("a"), ord("A"), ord("h"), ord("H")):
            return ("left",)
        elif key in (ord("d"), ord("D"), ord("l"), ord("L")):
            return ("right",)
        elif key in (ord("w"), ord("W"), ord("k"), ord("K")):
            return ("up",)
        elif key in (ord("s"), ord("S"), ord("j"), ord("J")):
            return ("down",)
        elif key in (ord("z"), ord("Z"), ord(" "), 0x0D):
            return ("a",)
        elif key in (ord("x"), ord("X")):
            return ("b",)
        elif key in (ord("r"), ord("R")):
            return ("run",)
        elif key == 0x1B:
            return ("stop",)
        elif key == 0x08:
            # BACKSPACE is THE console key (#71 pause / HOME) -- the same physical
            # key in every input mode: here on the typed-ASCII path, in
            # _read_raw_buttons on the raw-matrix path (d4 bit 3), and via the
            # Workstation's last_key edge for a text-mode cart. q/Q and e/E lost
            # their old home/stop aliases: typing carts (Letter Blitz) read
            # letters via key()/keyp(), and a letter that ALSO fires console
            # chrome is a stolen letter -- pressing Q paused the game instead of
            # shooting the Q target. Text-mode screens (code editor, wifi
            # password) suppress ALL aliases, so backspace still deletes there.
            return ("home",)
        return ()


class InputPoller:
    """#69 THE INPUT POLLER THREAD -- the keyboard/touch stall fix.

    THE PROBLEM (root-caused on hardware, #69): the T-Deck keyboard is a
    bit-banged I2C slave (an ESP32-C3) that CLOCK-STRETCHES its way through a
    read -- I2CSTAT sized real stalls at 21-60ms on the kbd and up to 41ms on
    the GT911 sharing the bus. The legacy esp32 machine.I2C `timeout=` only
    caps a SINGLE stretch EVENT (an exponential HW register) inside a
    hardcoded 100ms*(1+len) transaction wait, so many sub-cap stretches add up
    to a 40-60ms "successful" read that used to land INSIDE the frame loop = a
    felt input/render freeze. (The per-transaction-timeout new i2c_master
    driver broke the whole bus at boot -- parked DO-NOT-USE in build.sh.)

    THE FIX: this thread OWNS every I2C0 transaction (keyboard reads, GT911
    reads, deferred set_game_mode writes) and stages results; the frame loop
    only consumes staged state (consume()/consume_touch -- cheap, I2C-free).
    A stall then blocks only this thread while the VM keeps rendering -- WHICH
    REQUIRES the build's I2C GIL-release patch (esp32_i2c_gil_release.patch:
    machine_i2c.c frees the GIL across the blocking i2c_master_cmd_begin;
    without it a stall holds the GIL and freezes the loop no matter which
    thread reads, since MicroPython threads share one GIL on MP_TASK_COREID).

    Staging semantics, per input kind:
      * raw-matrix buttons are LEVEL state -> latest snapshot wins (tuple swap,
        atomic under the GIL).
      * ASCII key bytes are one-shot EVENTS (the C3 reports each press once) ->
        a small queue delivers each byte for exactly one main frame, inserting
        a 0-frame between identical bytes so keyp()'s edge detector sees both.
      * GT911 samples: freshest point this frame, a pending finger-up the frame
        after, so a sub-frame tap still lands as a clean down->up pair.

    The I2CSTAT counters keep updating from this thread, so stalls stay
    measurable -- smooth frames + nonzero I2CSTAT maxima is exactly the
    signature that the isolation works. _poll_once is the whole per-pass body,
    factored out so host tests drive it without a thread."""

    POLL_MS = 12       # cadence (~80Hz; each pass = 1 kbd read + 1 touch read)

    def __init__(self, keyboard, touch, period_ms=None):
        self.kbd = keyboard
        self.touch = touch
        self.period = self.POLL_MS if period_ms is None else period_ms
        self.alive = False
        self._stop = False
        # Keyboard staging: which mode the LAST pass saw, plus the two state
        # shapes that mode needs (only one is ever live at a time, but keeping
        # both named -- instead of one mixed 3-tuple -- makes consume() read
        # as "raw: use the snapshot; ascii: use the latch + dequeue a byte").
        self._kbd_is_raw = False
        self._raw_stage = ((), 0)      # raw-matrix (buttons, key) -- LEVEL state
        self._ascii_buttons = ()       # ASCII fallback hold-latch -- LEVEL state
        self._keyq = []                # one-shot ASCII bytes awaiting delivery
        self._last_key = 0             # last byte consume() applied (edge gapping)
        self._tpoint = None            # freshest GT911 point since last consume
        self._tup = False              # a confirmed finger-up since last consume

    def start(self):
        """Spawn the thread; True on success. On ANY failure the caller keeps
        the synchronous path (this must never take input down)."""
        try:
            import _thread
            _thread.start_new_thread(self._run, ())
            self.alive = True
            return True
        except Exception as exc:   # noqa: BLE001 -- no _thread / no RAM -> fallback
            print("Moybyte input poller unavailable:", exc)
            return False

    # -- poller thread side ---------------------------------------------
    def _run(self):
        self.alive = True
        while not self._stop:
            try:
                self._poll_once()
            except Exception:   # noqa: BLE001 -- one bad pass must not kill input
                pass
            _sleep_ms(self.period)
        self.alive = False

    def _poll_once(self):
        """One full bus pass: pending kbd mode switch, one keyboard read, one
        touch read. ALL I2C lives here (the GIL-release patch makes a stall
        block only this thread)."""
        kbd = self.kbd
        if kbd is not None and kbd.available:
            kbd.apply_pending_mode()
            buttons, key = kbd._read_stage()
            self._kbd_is_raw = kbd.raw_mode
            if self._kbd_is_raw:
                self._raw_stage = (buttons, key)
            else:
                self._ascii_buttons = buttons
                # ASCII bytes are one-shot events: queue each one (bounded).
                if key and len(self._keyq) < 16:
                    self._keyq.append(key)
        t = self.touch
        if t is not None and t.available:
            r = t.read_raw()
            if r is False:
                self._tup = True
            elif r is not None:
                self._tpoint = r

    # -- main thread side -------------------------------------------------
    def consume(self):
        """Apply the staged keyboard state to InputState -- the frame loop's
        replacement for keyboard.poll(). Cheap and I2C-free."""
        if self._kbd_is_raw:
            buttons, key = self._raw_stage
        else:
            buttons, key = self._ascii_buttons, self._dequeue_key()
        self.kbd._apply((buttons, key))

    def _dequeue_key(self):
        """Pop the next queued ASCII byte for delivery this frame -- unless
        it's an exact repeat of the byte consume() JUST delivered, in which
        case deliver a 0 (release) frame instead and leave it queued. Without
        this gap, two rapid same-letter presses ("aa") would read as one
        continuously-held key to keyp()'s edge detector, silently losing the
        second press."""
        if not self._keyq:
            self._last_key = 0
            return 0
        if self._keyq[0] == self._last_key:
            self._last_key = 0
            return 0
        key = self._keyq.pop(0)
        self._last_key = key
        return key

    def consume_touch(self):
        """One raw GT911 sample per frame (wired as Touch._source): the
        freshest point first, a pending finger-up the frame after -- a
        sub-frame tap becomes a clean two-frame down->up."""
        p = self._tpoint
        if p is not None:
            self._tpoint = None
            return p
        if self._tup:
            self._tup = False
            return False
        return None

    def stop(self):
        self._stop = True


def _sleep_ms(ms):
    try:
        time.sleep_ms(ms)
    except AttributeError:
        time.sleep(ms / 1000.0)


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(end_ms, start_ms):
    try:
        return time.ticks_diff(end_ms, start_ms)
    except AttributeError:
        return end_ms - start_ms


def _ticks_us():
    try:
        return time.ticks_us()
    except AttributeError:
        return int(time.time() * 1000000)
