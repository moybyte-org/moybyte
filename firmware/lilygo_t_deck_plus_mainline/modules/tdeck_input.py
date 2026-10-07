"""The T-Deck's input: the C3 keyboard, the trackball and the GT911, the
kernel's drivers (native/moy_input), and the optional BLE HID keyboard beside
the physical one.

`moy_runtime.run_desktop` constructs one `TDeckInput` over the kernel's table
before the spine, hands `build` to the spine as its `inputs()` (so the GT911
comes up behind the splash) and calls `poll` as the frame loop's input hook.
The keyboard and the GT911 are passed on the kernel's input task (#69), which
the frame's tail kicks once (`moy_runtime`), so a C3 clock-stretch blocks that
task and never a frame; the trackball's pulses are counted by ISRs.
"""

import moy_input
from console import _cursor_delta
from device_util import _ticks_ms, _ticks_diff


class TDeckInput:
    """Every input source on this board, and the one pass a frame reads them
    in. `inp` is the kernel's table every source writes into."""

    def __init__(self, inp):
        self.inp = inp
        self.keyboard = moy_input.keyboard()
        self.ball = moy_input.trackball()
        self.touch = None
        # BLE HID keyboard (#26): a SECOND, optional input source on this
        # board. On the touch-only boards a paired BLE keyboard is the only
        # keyboard and becomes ws.keyboard outright; here the physical C3
        # keyboard keeps that slot and the BLE driver hangs off
        # ws.ble_keyboard. Both write into the same table, so nothing in the
        # shared console needs to know which one a keypress came from.
        #
        # Not started here, deliberately: scanning is what makes BLE
        # expensive, and a board whose keyboard already works should not pay
        # for a radio nobody asked for. Settings starts it when the kid opens
        # the panel.
        self.ble_keyboard = moy_input.ble()
        self._click_active = [False, False]   # poll's answer, reused every frame

    def build(self, w, h):
        """The GT911, behind the splash, mapped onto the w x h system canvas."""
        self.touch = moy_input.touch(w, h)
        return self.touch

    def poll(self, now, ws, pointer, t):
        """Every input source on this board: the BLE keyboard, the touch's
        sample, the merge, then the trackball (caret in the code editor,
        cursor everywhere else) and the pointer. Returns (click, active) for
        the shared loop; the dev channel and idle blank run THERE, in the one
        order that lets the waking touch be swallowed. `t` takes the
        keyboards' and the merge's ms (`kbd`, `inp`) for the diag lines."""
        # The BLE keyboard's reports arrive on a radio IRQ; poll() is what
        # turns them into held buttons + a key, and it also advances
        # scan/reconnect and flushes a new bond outside the IRQ.
        ble = self.ble_keyboard
        if ble is not None:
            try:
                ble.poll()
                ble.apply_mouse(pointer)        # a boot mouse, where one is paired
            except Exception as exc:  # noqa: BLE001 -- BLE must fail keyboard-only
                print("Moybyte BLE keyboard poll failed:", exc)
        touch = self.touch
        if touch is not None:
            touch.poll()
        t["kbd"] = _ticks_diff(_ticks_ms(), now)
        _t0 = _ticks_ms()
        inp = self.inp
        inp.begin_frame()
        click = False
        nx = ny = 0
        ball = self.ball
        if ball is not None:
            counts, click = ball.poll()
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
        f = inp.apply_pointer(pointer)
        if f & moy_input.P_CLICK:
            click = True
        t["inp"] = _ticks_diff(_ticks_ms(), _t0)
        out = self._click_active
        out[0] = click
        out[1] = bool(f & moy_input.P_HELD) or nx or ny or click or bool(inp.last_key)
        return out
