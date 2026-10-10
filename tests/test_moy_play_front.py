"""A VM-free game is the kernel loop's foreground (docs/kernel_cartpath_2026-10.md
§4): once the Player hands it over (`moy_play_front`), each loop frame is
`moy_play_front_frame` -- the run's input, its ticks, the chrome over it and
the board's present, all C -- and the console's frame does not run until the
run ends or needs it. A board's compositor gives the front its canvas and its
present; here a buffer stands in for one (runtime/moy_play.py's `front_ops`),
and the run is the real host Player's over the real seed cart. What each check
watches for:

  * the front is taken by the Player's own start, only where the board gives
    one, and a canvas the run does not draw at is refused;
  * every front frame ticks the cart, draws into the board's canvas and
    presents it, with no upcall of any class in the run's books;
  * `state` while the run is in front is the kernel's answer, not the console's;
  * the hold-to-exit gesture is the kernel's: home held short of the hold
    keeps the run, held through it ends the run with why HOLD, and the
    console's next frame takes the route back to the launcher;
  * the dev channel's `end` ends the run the same way, with why MENU.
"""

import ctypes
import json
import os
import shutil

import pytest

from ws_helpers import build_ws, open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "system_carts", "moybyte.bullet_storm.moy")

mp = pytest.importorskip("runtime.moy_play")


@pytest.fixture
def front(tmp_path):
    try:
        mp._play()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    root = tmp_path / "carts"
    root.mkdir(parents=True)
    shutil.copytree(SEED, str(root / "moybyte.bullet_storm.moy"))
    ws = build_ws(tmp_path)
    w, h = 320, 240
    buf = (ctypes.c_uint16 * (w * h))()
    presents = []
    ws.front_bind = lambda: mp.front_ops(buf, w, h, presents)
    try:
        yield ws, buf, presents
    finally:
        if mp.front_live():
            mp.front_end(mp.END_MENU)
        mp.front_ops(None, 0, 0)


def test_the_player_hands_a_vm_free_game_to_the_front(front):
    ws, buf, presents = front
    open_cart(ws, "Bullet Storm")
    assert ws.player.cart_error is None
    assert ws.player._front is True, "the Player did not take the front"
    assert mp.front_live()
    st = json.loads(mp.state_json())
    assert st["front"] is True and st["cart"] == "Bullet Storm"
    # The host has no VM stop: the verdict is the lever's absence.
    assert st["run"] == {"runtime": "lua", "vm_free": True, "why": "free",
                         "stop": "lever", "vm_down": False}
    # A console frame while the kernel has the front (one in the same loop
    # frame that took it, as a link's re-run does) leaves the run alone.
    ws.frame(1 / 30)
    assert ws.player._front is True and mp.front_live()
    assert json.loads(mp.state_json())["play"]["frames"] == st["play"]["frames"]
    f0, t0 = st["play"]["frames"], st["play"]["ticks"]
    for i in range(1, 11):
        assert mp.front_frame(1000 + 33 * i, 33333) == 1
    assert presents == [True] * 10, "every front frame presents what it drew"
    assert any(buf), "the run drew nothing into the board's canvas"
    st = json.loads(mp.state_json())
    assert st["play"]["frames"] == f0 + 10
    # Bullet Storm is unpaced ("fps": "free"): one tick a loop frame, never none.
    assert st["play"]["ticks"] == t0 + 10, "the run in front drew but never ticked"
    assert st["play"]["upcalls"] == [0, 0, 0, 0, 0, 0], st["play"]
    # (`upcalls` is the host loop's last frame, which other suites drive.)
    assert st["link"] is None


def test_a_front_ends_on_the_hold_and_the_console_takes_the_route(front):
    ws, buf, presents = front
    open_cart(ws, "Bullet Storm")
    assert ws.player._front is True
    inp = mp._BOUND[0]
    now = 5000
    assert mp.front_frame(now, 33333) == 1
    inp.set_button("home", True)
    inp.begin_frame()
    # Short of the hold: the run plays on, the pill over it.
    for _ in range(10):
        now += 33
        assert mp.front_frame(now, 33333) == 1
    assert mp.front_live()
    # Through the hold: the run ends where it stands.
    now += 700
    assert mp.front_frame(now, 33333) == -1
    assert not mp.front_live()
    info = mp.info()
    assert info[6] is True and info[10] == mp.END_HOLD, info
    assert list(info[5]) == [0, 0, 0, 0, 0, 0], info
    assert mp.state_json() == "null", "no run in front: the console answers `state`"
    inp.set_button("home", False)
    inp.begin_frame()
    ws.frame(1 / 30)
    assert ws.player._front is False
    assert ws.screen == "launcher", ws.screen


def test_the_dev_channels_end_closes_a_run_in_front(front):
    ws, buf, presents = front
    open_cart(ws, "Bullet Storm")
    assert mp.front_frame(100, 33333) == 1
    assert mp.front_end(mp.END_MENU) is True
    assert mp.front_end(mp.END_MENU) is False, "nothing in front any more"
    info = mp.info()
    assert info[6] is True and info[10] == mp.END_MENU, info
    ws.frame(1 / 30)
    assert ws.screen == "launcher", ws.screen


def test_a_canvas_the_run_does_not_draw_at_keeps_the_console_front(tmp_path):
    try:
        mp._play()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    root = tmp_path / "carts"
    root.mkdir(parents=True)
    shutil.copytree(SEED, str(root / "moybyte.bullet_storm.moy"))
    ws = build_ws(tmp_path)
    buf = (ctypes.c_uint16 * (160 * 120))()
    ws.front_bind = lambda: mp.front_ops(buf, 160, 120)
    try:
        open_cart(ws, "Bullet Storm")
        assert ws.player.cart_error is None
        assert ws.player._front is False
        assert not mp.front_live()
        assert mp.front_frame(0, 33333) == -1
    finally:
        mp.front_ops(None, 0, 0)
