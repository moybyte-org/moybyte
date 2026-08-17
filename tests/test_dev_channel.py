"""The unified serial dev channel (runtime/dev_channel.py).

Until 2026-08-17 the channel existed three times: this module (extracted for
the fork, which was then deleted -- zero importers), a verbatim copy inside the
T-Deck's moy_runtime, and an older inline loop in the P4's. Both boards
construct `DevChannel` now, so this file is the host half of the regression
net; the on-glass halves are tests/test_p4_on_glass.py and
tests/test_tdeck_on_glass.py, which drive the same commands over real serial.

Everything here runs against stub objects on CPython -- the channel is
deliberately importable with no board and no console (its device_util import
falls back to a self-contained shim), which is what makes this testable at all.
"""

import json

from runtime.dev_channel import DevChannel, _remote_state


class FakePointer:
    def __init__(self):
        self.down = False
        self.fresh = False
        self.click = False
        self.placed = []

    def place(self, x, y):
        self.placed.append((x, y))


class FakeIdle:
    """The IdleBlank surface `power` drives (device_boot.IdleBlank's shape)."""

    def __init__(self, timeout_ms=300000):
        self.timeout_ms = timeout_ms
        self.asleep = False
        self.blanked = False
        self.woken = 0

    def blank(self):
        self.blanked = True

    def wake(self, now):
        self.woken += 1
        self.asleep = False


class FullscreenWM:
    _stack = ["home"]


class Win:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.title_h = 18
        self.kind = "settings"
        self.minimized = False
        self.buf = None
        self.ctx = None


class WindowedWM:
    def __init__(self):
        self._order = ["settings"]
        self._focus = "settings"
        self._wins = {"settings": Win(100, 80, 640, 400)}

    def desk_open(self):
        return True


class FakeWS:
    def __init__(self, wm=None):
        self.wm = wm or FullscreenWM()
        self.screen = "home"
        self.wifi = None
        self._all_carts = []
        self._apps = ()
        self._dirty = False
        self._psave_ms = 300000
        self._psave_asleep = False


def make(ws=None, **kw):
    ws = ws or FakeWS()
    return ws, DevChannel(ws, FakePointer(), **kw)


# -- the state snapshot --------------------------------------------------------


def test_state_carries_both_tiers_shapes(capsys):
    """ONE snapshot for every tier: the fullscreen back-stack when the WM has
    one, the windowed fields when it has windows -- the P4 suite's keys
    (psave/desk/order/wins) and the T-Deck's (stack) from the same function."""
    full = _remote_state(FakeWS())
    assert full["stack"] == ["home"]
    assert full["psave"] == [False, 300]
    assert "wins" not in full

    windowed = _remote_state(FakeWS(wm=WindowedWM()))
    assert windowed["desk"] is True
    assert windowed["order"] == ["settings"]
    assert windowed["wins"]["settings"][:4] == [100, 80, 640, 400]
    assert "stack" not in windowed


def test_state_is_one_line_json(capsys):
    ws, ch = make()
    ch.run(ws, "state")
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith("STATE ")][0]
    st = json.loads(line.split("STATE ", 1)[1])
    assert st["screen"] == "home"


# -- gesture scripts -----------------------------------------------------------


def test_swipe_is_press_hold_release(capsys):
    """i==0 press edge, held interpolation, i==n a real RELEASE sample at the
    end point (down=False) -- the shape the fling estimators need."""
    ws, ch = make()
    ch.run(ws, "swipe 0 0 100 0 5")
    samples = []
    while ch._swipe is not None:
        ch.click = False
        ch._scripts()
        if ch._swipe is not None or samples[-1:] != []:
            pass
        samples.append((ch.pointer.placed[-1] if ch.pointer.placed else None,
                        ch.pointer.down, ch.click))
    out = capsys.readouterr().out
    assert "REMOTE swipe 0,0 -> 100,0 frames=5" in out
    assert "REMOTE swipe done" in out
    # 6 pointer frames for n=5 (0..5), then the done frame cleared the script.
    xs = [p[0][0] for p in samples if p[0] is not None]
    assert xs[0] == 0 and xs[-1] == 100
    press = samples[0]
    assert press[1] is True and press[2] is True          # press edge clicks
    release = samples[5]
    assert release[1] is False                            # real release sample
    assert all(s[1] is True for s in samples[1:5])        # held in between
    assert all(s[2] is False for s in samples[1:])        # click frame 0 only
    assert all(s[0] is not None for s in samples[:6])
    assert ch.pointer.fresh is True                       # scripted = fresh


def test_drag_declines_without_windows_and_runs_with(capsys):
    ws, ch = make()                                        # fullscreen tier
    ch.run(ws, "drag")
    assert "REMOTE drag: no window open" in capsys.readouterr().out
    assert ch._drag is None

    ws2, ch2 = make(ws=FakeWS(wm=WindowedWM()))
    ch2.run(ws2, "drag 12 3")
    out = capsys.readouterr().out
    assert "REMOTE drag win=settings" in out and "frames=12 step=3" in out
    n = 0
    while ch2._drag is not None:
        ch2._scripts()
        n += 1
    assert "REMOTE drag done" in capsys.readouterr().out
    assert n == 13                                        # 12 frames + done
    assert ch2.pointer.down is False                      # released at the end


# -- board extras and the py env -----------------------------------------------


def test_extras_dispatch_after_builtins_and_cannot_shadow(capsys):
    calls = []
    ws, ch = make(extra={"bt": lambda ws, p, l: calls.append(l),
                         "state": lambda ws, p, l: calls.append("SHADOW")})
    ch.run(ws, "bt status")
    assert calls == ["bt status"]
    ch.run(ws, "state")                                   # built-in wins
    assert calls == ["bt status"]
    assert "STATE " in capsys.readouterr().out


def test_unknown_command_echoes(capsys):
    ws, ch = make()
    ch.run(ws, "frobnicate 1")
    assert "REMOTE ? frobnicate 1" in capsys.readouterr().out


def test_py_env_reaches_injected_names(capsys):
    ws, ch = make(env={"marker": 41})
    ch.run(ws, "py marker + 1")
    assert "PY 42" in capsys.readouterr().out


# -- power over the injected IdleBlank ------------------------------------------


def test_power_retune_off_and_disable(capsys):
    idle = FakeIdle()
    ws, ch = make(idle=idle)
    ch.run(ws, "power 3")
    assert idle.timeout_ms == 3000
    assert ws._psave_ms == 3000                # `state`'s psave stays live
    ch.run(ws, "power off")
    assert idle.blanked is True                # explicit blank is a REQUEST...
    ch.run(ws, "power 0")
    assert idle.timeout_ms == 0
    out = capsys.readouterr().out
    assert "REMOTE power timeout=3s asleep=False" in out
    assert "REMOTE power off" in out
    assert "REMOTE power timeout=0s asleep=False" in out


def test_power_without_idle_declines(capsys):
    ws, ch = make()
    ch.run(ws, "power 3")
    assert "no idle blank" in capsys.readouterr().out


def test_bl_without_backlight_declines_and_with_it_drives(capsys):
    ws, ch = make()
    ch.run(ws, "bl 0")
    assert "no backlight control" in capsys.readouterr().out
    lit = []
    idle = FakeIdle()
    ws2, ch2 = make(set_backlight=lit.append, idle=idle)
    ch2.run(ws2, "bl 0")
    ch2.run(ws2, "bl 1")
    assert lit == [False, True]
    assert idle.asleep is False and idle.woken == 1
