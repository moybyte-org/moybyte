"""`SystemStore` + `StoreHandle` + the achievement event push (#209 landing B).

Three mechanisms this landing introduced, each of which fails SILENTLY when it
breaks -- which is why they are pinned directly rather than left to the goldens:

  1. THE DICT IS NEVER REBOUND. `ws.system` is a plain alias of the store's own
     dict and `load()` mutates it in place. A landing that re-assigned either
     name would leave every alias in the shell (settings_layer's raw writes, the
     launcher's favorites read, `app_context.Prefs`, the crash guard) writing
     into an orphan nobody persists -- and nothing would raise.
  2. THE GUARD IS ONE OBJECT. `StoreHandle` reads the store, the root,
     `can_manage` and the SD session wrapper THROUGH `ws` per call, because none
     of them exists when the collaborator is built.
  3. THE OVERLAYS ARE PUSHED, NOT POLLED. The achievement objects write their
     deadlines into flat kernel fields at event time; the per-frame gates read
     those ints and must not call into `ach` / `ach_ui` at all.
"""

import pytest

from runtime import crash_guard, host_app, moy_carts, system_store
from ws_helpers import build_ws

DT = 1 / 30.0


def _ws(tmp_path):
    """A console with every self-animating source stilled, so `_animating` is
    answering about the overlays and nothing else."""
    ws = build_ws(tmp_path)
    ws.look.select_wallpaper("fill:dark_blue", persist=False)
    ws._toast_until = 0
    ws._egg_until = 0
    ws._confetti_until = 0
    ws._splash_until = None
    return ws


class _Tripwire:
    """Any attribute touch is a failure, and it names itself."""

    def __init__(self, what):
        self._what = what

    def __getattr__(self, name):
        raise AssertionError(
            "the per-frame gate reached %s.%s -- the overlay deadlines are "
            "pushed, not polled" % (self._what, name))


class _AngryStore:
    """A cart store whose every settings verb fails. Nothing here may raise out
    of the console: a card pulled mid-session is not a crash."""

    def load_system(self, root):
        raise OSError("card gone")

    def save_system(self, settings, root):
        raise OSError("card gone")

    def load_achievements(self, root):
        raise OSError("card gone")

    def save_achievements(self, ids, root):
        raise OSError("card gone")


# ---------------------------------------------------------------------------
# 1. the dict identity
# ---------------------------------------------------------------------------

def test_ws_system_is_the_stores_own_dict(tmp_path):
    ws = _ws(tmp_path)
    assert ws.system is ws.prefs.settings


def test_a_load_mutates_that_dict_in_place(tmp_path):
    """The mechanism the whole landing rests on: `load_system()` must not rebind
    either name, or every alias in the shell goes stale at boot."""
    ws = _ws(tmp_path)
    alias = ws.system                       # what settings_layer/Prefs/the guard hold
    moy_carts.save_system({"font_scale": 1, "marker": "from-the-card"},
                          ws.carts_root)
    ws.load_system()
    assert ws.system is alias
    assert ws.prefs.settings is alias
    assert alias["marker"] == "from-the-card"


def test_a_load_replaces_what_was_there(tmp_path):
    """Not a merge: a key the card does not carry is GONE after the load, which
    is what "the file is the state" means."""
    ws = _ws(tmp_path)
    ws.system["stale"] = 1
    moy_carts.save_system({"marker": 2}, ws.carts_root)
    ws.load_system()
    assert "stale" not in ws.system
    assert ws.system["marker"] == 2


def test_a_load_with_no_store_keeps_what_is_there(tmp_path):
    """An embedded boot has no card to read: the in-RAM settings survive rather
    than being wiped by a load that could not happen."""
    ws = _ws(tmp_path)
    ws.system["chosen"] = "here"
    ws.carts_store = None
    ws.prefs.load()
    assert ws.system["chosen"] == "here"


def test_a_store_that_raises_leaves_the_settings_empty_and_boots(tmp_path):
    ws = _ws(tmp_path)
    ws.system["stale"] = 1
    ws.carts_store = _AngryStore()
    ws.prefs.load()                         # must not raise
    assert ws.system == {}
    assert ws.system is ws.prefs.settings


# ---------------------------------------------------------------------------
# 2. the crash guard over that dict
# ---------------------------------------------------------------------------

def test_the_crash_guard_sees_a_reload(tmp_path):
    """The wart this landing retired: the guard used to be handed a CALLABLE
    because `load_system()` rebound the dict. It holds the dict itself now, so
    the strikes a reload brings back have to land where it is counting -- a
    guard reading its own private copy would report the boot-time count forever
    and never raise."""
    ws = _ws(tmp_path)
    assert ws.app_guard.arm("some_app") is True
    assert ws.app_guard.strikes("some_app") == 1
    # What a board that died twice holding this app comes back to.
    moy_carts.save_system(
        {crash_guard.KEY: {"strikes": {"some_app": 2}, "open": None}},
        ws.carts_root)
    ws.load_system()
    assert ws.app_guard.strikes("some_app") == 2
    assert ws.app_guard.arm("some_app") is True     # its third and last
    assert ws.app_guard.disabled("some_app") is True


def test_the_crash_guard_persists_through_the_store(tmp_path):
    """`arm` writes a strike to system.json through `prefs.persist` -- so a
    board that DIED holding one still knows on the next boot."""
    ws = _ws(tmp_path)
    ws.app_guard.arm("some_app")
    on_card = moy_carts.load_system(ws.carts_root)
    assert on_card["app_guard"]["strikes"]["some_app"] == 1
    assert on_card["app_guard"]["open"] == "some_app"


# ---------------------------------------------------------------------------
# 3. StoreHandle
# ---------------------------------------------------------------------------

def test_the_handle_reads_the_store_through_ws_per_call(tmp_path):
    """The wiring-order trap, made structurally impossible: a handle built
    before `wire_workstation_core` injected anything answers about the console
    as it is NOW, not as it was at construction."""
    ws = _ws(tmp_path)
    handle = system_store.StoreHandle(ws)   # as if built at __init__ time
    store, root = ws.carts_store, ws.carts_root
    ws.carts_store = None
    ws.carts_root = None
    assert handle.ready() is False
    assert handle.writable() is False
    ws.carts_store, ws.carts_root = store, root
    assert handle.ready() is True
    assert handle.writable() is True
    ws.can_manage = False                   # set AFTER the store, on real boards
    assert handle.ready() is True
    assert handle.writable() is False


def test_every_store_touch_runs_inside_one_sd_session(tmp_path):
    """On the T-Deck `_with_sd` mounts the card and releases it; a read or write
    that skipped it is the documented panic, so the handle is the only route."""
    ws = _ws(tmp_path)
    sessions = []
    inner = ws._with_sd

    def counting(fn):
        sessions.append(1)
        return inner(fn)

    ws._with_sd = counting
    ws.prefs.persist()
    ws.prefs.load()
    ws.prefs.load_achievements()
    ws.prefs.save_achievements(["first_open"])
    assert len(sessions) == 4


def test_a_read_only_console_attempts_no_write(tmp_path):
    """`can_manage` False is a build with nowhere to put a write -- not a
    failure to report, and not a call to make."""
    ws = _ws(tmp_path)
    attempts = []
    ws._with_sd = lambda fn: attempts.append(1)
    ws.can_manage = False
    ws.prefs.persist()
    ws.prefs.save_achievements(["first_open"])
    assert attempts == []


def test_a_failing_write_is_not_fatal(tmp_path):
    ws = _ws(tmp_path)
    ws.carts_store = _AngryStore()
    ws.system["theme"] = "berry"
    ws.prefs.persist()                      # must not raise
    assert ws.system["theme"] == "berry"    # and the choice still holds in RAM


def test_achievements_round_trip_through_the_handle(tmp_path):
    ws = _ws(tmp_path)
    ws.prefs.save_achievements(["first_open", "konami"])
    assert ws.prefs.load_achievements() == ["first_open", "konami"]
    ws.carts_store = _AngryStore()
    assert ws.prefs.load_achievements() == []      # a bad card is [], never a raise


# ---------------------------------------------------------------------------
# 4. the event push (rev 3): every arm site -> one flat kernel field
# ---------------------------------------------------------------------------

def test_an_unlock_arms_the_toast_deadline(tmp_path):
    ws = _ws(tmp_path)
    assert ws._toast_until == 0
    assert ws.ach.award("first_open") is True
    assert ws._toast_until, "the unlock hook did not arm the banner"
    assert ws.ach.toast[0] == "first_open"
    assert ws._animating(DT) is True


def test_a_second_unlock_replaces_the_banner_and_extends_it(tmp_path):
    """There is no toast QUEUE and there never was -- `award` overwrites its
    payload. A later unlock must therefore also push the deadline out, or the
    second banner would inherit the first one's remaining time."""
    ws = _ws(tmp_path)
    ws.ach.award("first_open")
    first = ws._toast_until
    ws._toast_until = first - 1000          # as if 1s of it had elapsed
    ws.ach.award("konami")
    assert ws.ach.toast[0] == "konami"
    assert ws._toast_until >= first


def test_the_three_eggs_arm_the_popup_deadline(tmp_path):
    for trigger in ("clock", "secret", "konami"):
        ws = _ws(tmp_path)
        au = ws.ach_ui
        if trigger == "clock":
            for _ in range(au._CLOCK_TAP_GOAL):
                au._tap_clock()
        elif trigger == "secret":
            for _ in range(au._SECRET_TAP_GOAL):
                au._tap_secret_door()
        else:
            for name in au._KONAMI:
                au._konami_step(name)
        assert au.egg_msg is not None, trigger
        assert ws._egg_until, trigger
        assert ws._animating(DT) is True, trigger


def test_the_konami_code_arms_the_confetti(tmp_path):
    ws = _ws(tmp_path)
    for name in ws.ach_ui._KONAMI:
        ws.ach_ui._konami_step(name)
    assert ws._confetti_until
    assert ws.ach.has("konami")


@pytest.mark.parametrize("field", ("_toast_until", "_egg_until", "_confetti_until"))
def test_each_overlay_animates_until_its_deadline_and_then_stops(tmp_path, field):
    ws = _ws(tmp_path)
    setattr(ws, field, host_app.console._ticks_ms() + 5000)
    assert ws._animating(DT) is True
    setattr(ws, field, host_app.console._ticks_ms() - 1)
    assert ws._animating(DT) is False


@pytest.mark.parametrize("field,bit", (("_confetti_until", 4),
                                       ("_egg_until", 16),
                                       ("_toast_until", 32)))
def test_the_wm_gates_each_overlay_layer_on_its_deadline(tmp_path, field, bit):
    """The other per-frame reader: the memoized stack's signature. Same field,
    same expiry -- an overlay that animates but never composites (or the
    reverse) is the failure mode of two independent predicates."""
    ws = _ws(tmp_path)
    layer = {4: ws._confetti_layer, 16: ws._egg_layer, 32: ws._toast_layer}[bit]
    assert ws.wm._overlay_sig() & bit == 0
    assert layer not in ws.wm.overlay_stack()
    setattr(ws, field, host_app.console._ticks_ms() + 5000)
    assert ws.wm._overlay_sig() & bit == bit
    assert layer in ws.wm.overlay_stack()
    setattr(ws, field, host_app.console._ticks_ms() - 1)
    assert ws.wm._overlay_sig() & bit == 0
    assert layer not in ws.wm.overlay_stack()


def test_the_frame_gates_never_call_into_the_achievement_objects(tmp_path):
    """The point of the flip. Both gates run on EVERY loop iteration of the
    console's life; before this they asked three objects, across two modules,
    whether anything was up -- and were told no."""
    ws = _ws(tmp_path)
    ws.ach = _Tripwire("ach")
    ws.ach_ui = _Tripwire("ach_ui")
    assert ws._animating(DT) is False
    assert ws.wm._overlay_sig() >= 0
    ws._toast_until = host_app.console._ticks_ms() + 5000
    ws._egg_until = ws._toast_until
    ws._confetti_until = ws._toast_until
    assert ws._animating(DT) is True
    assert ws.wm._overlay_sig() & (4 | 16 | 32) == (4 | 16 | 32)


def test_the_toast_still_draws_while_its_deadline_is_up(tmp_path):
    """The objects are cold, not gone: the payload is read at DRAW time, and
    the banner has to survive the round trip through the layer stack."""
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.pointer.visible = False
    ws.ach.award("first_open")
    assert ws._toast_layer in ws.wm.overlay_stack()
    drv.frame(DT)                           # draws it -- an unset payload raises
    ws._toast_until = host_app.console._ticks_ms() - 1
    assert ws._toast_layer not in ws.wm.overlay_stack()
