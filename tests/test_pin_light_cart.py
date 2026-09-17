"""Pin Light on a console that HAS pins (#9).

WHY THIS EXISTS. `tests/test_seed_carts_run.py` exists because this cart
crashed on its first frame on four boards; it now opens every seed cart on a
host console, which has no pins -- so what it proves about THIS cart is the
degraded branch, the one that draws "this console has no pins" and returns
before touching a verb. The half the cart was written for (a Zero, which
answers the GPIO probe and grants `pin_write`) had no test at all.

So: the real cart through the real console, over a fake pins backend, with the
LED read back off the writes it made.
"""

from runtime import host_api
from ws_helpers import build_ws, open_cart

PIN = 21          # the cart's default `pin` config -- the XIAO's own LED
LIT, DARK = 0, 1  # ACTIVE-LOW, confirmed on a board


class _FakePins:
    """The `ws.gpio` seam: `write(n, v)` / `read(n)`, and nothing else.

    The shipping backend (`firmware/web_runner/gpio_link.py`) queues writes and
    answers reads from the last batch. Neither property is this cart's
    business -- it writes and never reads -- so the fake is the contract, not
    the transport.
    """

    def __init__(self):
        self.writes = []
        self.levels = {}

    def write(self, n, v):
        self.writes.append((n, v))
        self.levels[n] = v

    def read(self, n):
        return self.levels.get(n)


def _console(tmp_path):
    ws = build_ws(tmp_path)
    pins = _FakePins()
    ws.gpio = pins                       # set BEFORE the run: Player.start reads it
    drv = host_api.ConsoleDriver(ws)
    open_cart(ws, "Pin Light")
    assert ws.cart_error is None
    return ws, drv, pins


def _press_a(drv):
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)                    # the key-up frame, so the next press edges


def test_starting_the_cart_parks_the_led_dark(tmp_path):
    """`_init` applies the OFF state rather than assuming it: a board that was
    left lit by the previous run must not come up out of step with the label
    the cart is drawing."""
    ws, _drv, pins = _console(tmp_path)
    assert pins.writes == [(PIN, DARK)]


def test_pressing_a_toggles_the_led(tmp_path):
    ws, drv, pins = _console(tmp_path)
    _press_a(drv)
    assert ws.cart_error is None
    assert pins.writes[-1] == (PIN, LIT)
    _press_a(drv)
    assert pins.writes[-1] == (PIN, DARK)
    assert ws.cart_error is None


def test_tapping_the_button_toggles_the_led(tmp_path):
    """The touch half: the same toggle through the drawn button, which is the
    only affordance on a board with no keyboard."""
    ws, drv, pins = _console(tmp_path)
    drv.click(160, 128)                  # the cart's button rect, dead centre
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert pins.writes[-1] == (PIN, LIT)


def test_a_tap_outside_the_button_changes_nothing(tmp_path):
    """The fixture has to be able to express a difference: a tap that misses
    must leave the pin alone, or the assertions above pass on any tap at all."""
    ws, drv, pins = _console(tmp_path)
    drv.click(12, 220)
    drv.frame(1 / 30)
    assert ws.cart_error is None
    assert pins.writes == [(PIN, DARK)]


def test_a_console_without_pins_runs_the_degraded_branch(tmp_path):
    """The other side of the gate, asserted here so the two branches are
    described in one place: no `ws.gpio`, no `pin_write` NAME, no crash."""
    ws = build_ws(tmp_path)
    assert ws.gpio is None
    drv = host_api.ConsoleDriver(ws)
    open_cart(ws, "Pin Light")
    _press_a(drv)
    assert ws.cart_error is None
