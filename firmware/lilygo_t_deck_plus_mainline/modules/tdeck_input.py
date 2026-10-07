"""The T-Deck's input: the C3 keyboard matrix, the trackball, the GT911 touch,
the #69 poller thread that owns every I2C transaction among them, and the
optional BLE HID keyboard beside the physical one.

`moy_runtime.run_desktop` constructs one `TDeckInput` before the spine, hands
`build` to the spine as its `inputs()` (so the GT911 and the poller come up
behind the splash) and calls `poll` as the frame loop's input hook. The
drivers are the shared device tier's (`moybyte.input`, `device_input`,
`ble_keyboard`); what is this board's is how they share one bus and one
thread.
"""

from console import _cursor_delta
from device_input import TrackBall, Touch
from device_util import _ticks_ms, _ticks_diff, _sleep_ms, _diag_note
from frame_loop import apply_touch

# --- #69 the input-poller thread ---------------------------------------------
#
# Every I2C0 transaction (keyboard + GT911 + mode switches) moves to a dedicated
# Python thread; the frame loop only consumes staged state. A C3 clock-stretch
# stall then blocks the poller instead of a frame. Requires the build's
# GIL-release patch to bite -- stage 3's `tdeck_smoke.keyboard()` is the on-glass
# A/B that says whether it is in this image. False (or no `_thread`, or a dead
# thread) falls back to synchronous polling with no rebuild.
MOY_INPUT_POLLER = True

# The bond store is on the INTERNAL VFS, not beside the carts the way the
# touch-only boards do it: this board's carts live on SD, whose writes have
# to go through the with_sd_live gate, and a pairing that fails because a
# card is missing would be a bad first experience for a feature whose whole
# point is "my keyboard works now".
BLE_STORE = "/ble_keyboard.json"


class TDeckInput:
    """Every input source on this board, and the one pass a frame reads them
    in. `inp` is the merged InputState every source writes into."""

    def __init__(self, inp):
        from moybyte.input import TDeckKeyboard
        self.inp = inp
        self.keyboard = TDeckKeyboard(inp)
        self.ball = TrackBall()
        self.touch = None
        self.poller = None
        # BLE HID keyboard (#26): a SECOND, optional input source on this
        # board. On the touch-only boards a paired BLE keyboard is the only
        # keyboard and becomes ws.keyboard outright; here the physical C3
        # keyboard keeps that slot and the BLE driver hangs off
        # ws.ble_keyboard. Both write into the SAME InputState, so nothing in
        # the shared console needs to know which one a keypress came from.
        #
        # auto_start=False deliberately: scanning is what makes BLE expensive,
        # and a board whose keyboard already works should not pay for a radio
        # nobody asked for. Settings starts it when the kid opens the panel.
        self.ble_keyboard = None
        try:
            from ble_keyboard import BleHidKeyboard
            self.ble_keyboard = BleHidKeyboard(inp, store_path=BLE_STORE,
                                               auto_start=False)
        except Exception as exc:  # noqa: BLE001 -- a build without the module, or no radio
            print("Moybyte: BLE keyboard unavailable:", exc)
        self._click_active = [False, False]   # poll's answer, reused every frame

    def build(self, w, h):
        """The GT911 and the #69 poller thread, behind the splash. The touch
        shares the keyboard's I2C object: one bus, one driver instance, so the
        poller owns every transaction on it."""
        from moybyte.input import InputPoller
        keyboard = self.keyboard
        touch = self.touch = Touch(w, h, i2c=getattr(keyboard, "_i2c", None))
        if MOY_INPUT_POLLER:
            try:
                _p = InputPoller(keyboard, touch)
                if _p.start():
                    self.poller = _p
                    keyboard._poller_owned = True
                    touch._source = _p.consume_touch
                    _diag_note("input", "poller thread running (#69, one pass per frame)")
            except Exception as exc:  # noqa: BLE001 -- input must never fail closed
                _diag_note("input", "poller setup failed: %s" % (exc,))
                self.poller = None
        return touch

    def poll(self, now, ws, pointer, t):
        """Every input source on this board: the #69 poller (with its death
        fallback), the keyboard, the BLE keyboard, the trackball (caret in the
        code editor, cursor everywhere else), the GT911. Returns (click,
        active) for the shared loop; the dev channel and idle blank run THERE,
        in the one order that lets the waking touch be swallowed. `t` takes
        the keyboard's and the merge's ms (`kbd`, `inp`) for the diag lines.

        The two keyboards each write their OWN InputSource and inp.begin_frame()
        merges them, so the order they poll in carries no authority -- which is
        the whole point of the multi-source model (device/moybyte/input.py)."""
        keyboard = self.keyboard
        touch = self.touch
        poller = self.poller
        # If the poller thread ever dies, detach and fall back to synchronous
        # polling -- input never goes dark.
        if poller is not None and not poller.alive:
            _diag_note("input", "poller thread died -> synchronous fallback")
            keyboard._poller_owned = False
            touch._source = None
            poller = self.poller = None
        # The poller thread makes one pass per frame, when this thread lets it:
        # kick() readies it and sleep_ms(0) (the port's GIL release + taskYIELD)
        # runs it. Without the yield a free-running cart never lets go of the
        # GIL and the thread starves (InputPoller's docstring has the numbers).
        if poller is not None:
            poller.kick()
            _sleep_ms(0)
        try:
            if poller is not None:
                poller.consume()
            else:
                keyboard.poll()
        except Exception:  # noqa: BLE001
            pass
        # The BLE keyboard's reports arrive on a radio IRQ; poll() is what
        # turns them into held buttons + a key, and it also advances
        # scan/reconnect and flushes a new bond outside the IRQ.
        ble = self.ble_keyboard
        if ble is not None:
            try:
                ble.poll()
            except Exception as exc:  # noqa: BLE001 -- BLE must fail keyboard-only
                print("Moybyte BLE keyboard poll failed:", exc)
        t["kbd"] = _ticks_diff(_ticks_ms(), now)
        _t0 = _ticks_ms()
        inp = self.inp
        inp.begin_frame()
        counts, click = self.ball.poll()
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        # The ball is this board's arrow keys, so a surface holding a CARET
        # gets them and everything else gets the cursor. ws.nav owns that
        # decision (the code editor AND a cart's focused editor handle, #181)
        # and says whether it spent them.
        if not ws.nav(nx, ny):
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)
        touched, tclick = apply_touch(touch, pointer)
        if tclick:
            click = True
        t["inp"] = _ticks_diff(_ticks_ms(), _t0)
        out = self._click_active
        out[0] = click
        out[1] = (touched or nx or ny or click
                  or bool(getattr(inp, "last_key", None)))
        return out
