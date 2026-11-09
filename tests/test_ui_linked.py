"""A native or WASI app drawing with the toolkit it links
(sdk/include/moybyte/ui.h, sdk/src/moyui_link.c): its own copy of
native/moy_ui, its RUN face over a canvas of its own, its theme pulled from
the console's block (moy_ui_pull, moy_ui_font_pull), its input from the frame
message, its RGB565 screen handed over with moy_present.

  * tests/fixtures/elf/elf_page.moy (a native module built for the host),
    tests/fixtures/wasm/wasi_page.moy (a WASI program) and
    tests/fixtures/ui_page/page_mini_py.moy (the same calls as a Python app)
    draw the same pixels under the same keys and typed text;
  * each app hears the toggle's press and the field's typing inline and
    ends on the button's.
"""

import os
import shutil

import pytest

from ws_helpers import make_drv, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "tests", "fixtures", "elf", "elf_page.moy")
PY = os.path.join(ROOT, "tests", "fixtures", "ui_page", "page_mini_py.moy")
WASI = os.path.join(ROOT, "tests", "fixtures", "wasm", "wasi_page.moy")
DT = 1.0 / 30.0
KEYS = ("a", "a", "down", "up")     # the toggle has the focus first, the field below it
TYPED = "hi"
STEADY = 4


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _pixels(ws):
    """The run's canvas, which both tiers draw whole."""
    return bytes(ws.run.canvas._buf)


def _shot(drv):
    """The canvas once the app has caught up: a native or WASI app draws on a
    task of its own (a WASI one interpreted on the host), so a frame may show
    its last screen; frames until STEADY in a row draw the same."""
    last = _pixels(drv.ws)
    same = 0
    for _ in range(60):
        _frame(drv)
        now = _pixels(drv.ws)
        same = same + 1 if now == last else 0
        if same >= STEADY:
            return now
        last = now
    return last


def _script(ws):
    """Settle, then each key of KEYS pressed, then TYPED typed into the
    field: a shot after each; then the button activated."""
    drv = make_drv(ws)
    shots = []
    for _ in range(3):
        _frame(drv)
    shots.append(_shot(drv))
    for key in KEYS:
        drv.press(key)
        _frame(drv)
        shots.append(_shot(drv))
    drv.press("down")
    _frame(drv)
    for ch in TYPED:
        drv.type_char(ord(ch))
        _frame(drv)
        _shot(drv)
    shots.append(_shot(drv))
    drv.press("down")
    _frame(drv)
    _shot(drv)
    drv.press("a")
    for _ in range(4):
        _frame(drv)
    return shots


def _wasi_or_skip():
    """Whether the host's wasm binding and wasi-sdk are here (CI must have both)."""
    from runtime import wasm_host
    from tools import jet_cart
    if wasm_host.available() and jet_cart.wasi_sdk(fetch=False) is not None:
        return True
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding or wasi-sdk is missing")
    return False


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Each tier's shots and how its run ended: {name: (shots, error, screen)}.
    Both carts are titled "Native page", so the run's bar draws alike."""
    from runtime import host_app
    from tools import elf_cart
    out = {}
    for name in ("python", "elf", "wasi"):
        root = str(tmp_path_factory.mktemp("ui_linked_" + name) / "carts")
        host_app.moy_carts.ensure_dirs(root)
        if name == "python":
            shutil.copytree(PY, os.path.join(root, "page_mini_py.moy"))
        elif name == "elf":
            elf_cart.build(ELF, os.path.join(root, "elf_page.moy"), ["host"], signed=False)
        else:
            if not _wasi_or_skip():
                continue
            from tools import wasm_cart
            wasm_cart.build(WASI, os.path.join(root, "wasi_page.moy"))
        ws = host_app.build_workstation(root)
        tier = "wasm" if name == "wasi" else name
        if name != "python" and tier not in ws.runtimes:
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


@pytest.mark.parametrize("tier", ["elf", "wasi"])
def test_the_linked_toolkit_draws_what_the_python_app_draws(runs, tier):
    if tier not in runs:
        pytest.skip("no host %s tier" % tier)
    want = runs["python"][0]
    got = runs[tier][0]
    assert len(got) == len(want)
    for i, (a, b) in enumerate(zip(got, want)):
        assert a == b, "%s: shot %d differs from the Python app's" % (tier, i)


@pytest.mark.parametrize("tier", ["python", "elf", "wasi"])
def test_the_toggle_and_the_field_are_heard_and_the_button_ends_it(runs, tier):
    if tier not in runs:
        pytest.skip("no host %s tier" % tier)
    shots, err, screen = runs[tier]
    assert shots[1] != shots[0], "the toggle's press changed nothing"
    assert shots[2] == shots[0], "a second press did not turn it back"
    assert shots[3] != shots[2] and shots[4] == shots[2], "the focus did not move and back"
    assert shots[5] != shots[3], "the typed text did not reach the field"
    assert err is None, err
    assert screen == "launcher", "the button's press did not end the app"
