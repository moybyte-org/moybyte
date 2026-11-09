"""A native app drawing with the toolkit it links (sdk/include/moybyte/ui.h,
sdk/src/moyui_link.c): its own copy of native/moy_ui, its RUN face over a
canvas of its own, its theme pulled from the console's block (moy_ui_pull,
moy_ui_font_pull), its input from the frame message, its RGB565 screen
handed over with moy_present.

  * tests/fixtures/elf/elf_page.moy (a native module built for the host) and
    tests/fixtures/ui_page/page_mini_py.moy (the same calls as a Python app)
    draw the same pixels under the same keys;
  * the native app hears the toggle's press inline and ends on the button's.
"""

import os
import shutil

import pytest

from ws_helpers import make_drv, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "tests", "fixtures", "elf", "elf_page.moy")
PY = os.path.join(ROOT, "tests", "fixtures", "ui_page", "page_mini_py.moy")
DT = 1.0 / 30.0
KEYS = ("a", "a", "down", "up")     # the toggle has the focus first


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _pixels(ws):
    """The run's canvas, which both tiers draw whole."""
    return bytes(ws.run.canvas._buf)


def _script(ws):
    """Settle, then each key of KEYS pressed and two frames drawn: the shots;
    then the button activated."""
    drv = make_drv(ws)
    shots = []
    for _ in range(3):
        _frame(drv)
    shots.append(_pixels(ws))
    for key in KEYS:
        drv.press(key)
        _frame(drv)
        _frame(drv)
        shots.append(_pixels(ws))
    drv.press("down")
    _frame(drv)
    _frame(drv)
    drv.press("a")
    for _ in range(4):
        _frame(drv)
    return shots


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Each tier's shots and how its run ended: {name: (shots, error, screen)}.
    Both carts are titled "Native page", so the run's bar draws alike."""
    from runtime import host_app
    from tools import elf_cart
    out = {}
    for name in ("python", "elf"):
        root = str(tmp_path_factory.mktemp("ui_linked_" + name) / "carts")
        host_app.moy_carts.ensure_dirs(root)
        if name == "python":
            shutil.copytree(PY, os.path.join(root, "page_mini_py.moy"))
        else:
            elf_cart.build(ELF, os.path.join(root, "elf_page.moy"), ["host"], signed=False)
        ws = host_app.build_workstation(root)
        if name == "elf" and "elf" not in ws.runtimes:
            ws.exit()
            continue
        ws.look.set_theme("moy", persist=False)
        ws.pointer.visible = False
        open_cart(ws, "Native page")
        if ws.confirm.open:             # a copied app's consent: Open
            act = ws.confirm._act
            ws.confirm.cancel()
            act()
        assert ws.cart["title"] == "Native page"
        p = ws.run.player
        assert p.cart_error is None, (name, p.cart_error)
        shots = _script(ws)
        out[name] = (shots, p.cart_error, ws.screen)
        ws.exit()
    return out


def test_the_linked_toolkit_draws_what_the_python_app_draws(runs):
    if "elf" not in runs:
        pytest.skip("no host native tier")
    want = runs["python"][0]
    got = runs["elf"][0]
    assert len(got) == len(want)
    for i, (a, b) in enumerate(zip(got, want)):
        assert a == b, "shot %d differs from the Python app's" % i


def test_the_toggle_is_heard_and_the_button_ends_it(runs):
    if "elf" not in runs:
        pytest.skip("no host native tier")
    shots, err, screen = runs["elf"]
    assert shots[1] != shots[0], "the toggle's press changed nothing"
    assert shots[2] == shots[0], "a second press did not turn it back"
    assert shots[3] != shots[2] and shots[4] == shots[2], "the focus did not move and back"
    assert err is None, err
    assert screen == "launcher", "the button's press did not end the app"
