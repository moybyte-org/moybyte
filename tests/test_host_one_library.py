"""The host's native modules are ONE loaded library, as a board's are one image
(runtime/host_native.py): the shell's `moy_ui`, a Lua run and a compiled run
read the same theme, depth, fonts and cart-text boundary, so a public-UI page
draws the same pixels in every runtime under a theme with depth.
"""

import ctypes
import os
import shutil

import pytest

from runtime import host_app
from ws_helpers import make_drv, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = os.path.join(ROOT, "tests", "fixtures", "ui_parts")
WASM = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_parts.moy")
_DT = 1.0 / 30.0


def _addr(lib, name):
    return ctypes.cast(getattr(lib, name), ctypes.c_void_p).value


def test_every_host_binding_loads_the_one_library():
    from runtime import (gfx_binding, glass_binding, host_native, lua_binding,
                         moy_input, moy_loop, moy_play, net_binding)
    import tools.moy_index_spike as spike
    path = host_native.build()
    assert path is not None
    libs = {"gfx": gfx_binding._lib(), "glass": glass_binding._lib(),
            "lua": lua_binding._lib(), "input": moy_input._lib(),
            "loop": moy_loop._lib(), "play": moy_play._lib(), "net": net_binding._lib()}
    for comp in (spike.SPINE, spike.FS, spike.INDEX):
        assert spike.host_library("c", False, comp) == path, comp.name
    want = _addr(libs["glass"], "moy_ui_cart_text")
    for name, lib in libs.items():
        assert lib._name == path, name
        assert _addr(lib, "moy_ui_cart_text") == want, name


def test_the_shells_moy_ui_state_is_the_lua_runs():
    """The boundary the Player sets through the shell's module is the one the
    library a Lua run draws in reads (moy_ui_cart_text answers what it was)."""
    from runtime import lua_binding
    from tools import moy_ui_binding
    ui = moy_ui_binding.binding()
    lib = lua_binding._lib()
    lib.moy_ui_cart_text.argtypes = [ctypes.c_int]
    lib.moy_ui_cart_text.restype = ctypes.c_int
    was = ui.cart_text(True)
    try:
        assert lib.moy_ui_cart_text(1) == 1, "the run's library saw the shell's boundary"
        ui.cart_text(False)
        assert lib.moy_ui_cart_text(0) == 0
    finally:
        ui.cart_text(was)


def _wasm():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return True
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s"
                    % (wasm_binding.why_unavailable() or "unknown"))
    return False


def _pixels(ws):
    ns = ws.player.ns
    cv = ns["screen"]()
    x, y, w, h = ns["content_rect"]()
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return bytes(out)


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(_DT)


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_a_page_draws_the_same_pixels_in_every_runtime_under_moy(tmp_path, variant):
    """Moy Day and Moy Night have depth (rounded, shadowed rows): a Lua and a
    compiled page draw it as the Python page does, frame by frame."""
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    for d in ("parts_py.moy", "parts_lua.moy"):
        shutil.copytree(os.path.join(PARTS, d), os.path.join(root, d))
    titles = {"python": "UI Parts Py", "lua": "UI Parts Lua"}
    if _wasm():
        from tools import wasm_cart
        wasm_cart.build(WASM, os.path.join(root, "app_parts.moy"))
        titles["wasm"] = "App Parts Wasm"
    shots = {}
    for name, title in titles.items():
        ws = host_app.build_workstation(root)
        ws.look.set_theme("moy", persist=False, variant=variant)
        ws.pointer.visible = False
        open_cart(ws, title)
        drv = make_drv(ws)
        drv.frame(_DT)
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        got = []
        for _ in range(3):
            _frame(drv)
        got.append(_pixels(ws))
        for key in ("down", "down"):
            drv.press(key)
            _frame(drv)
            _frame(drv)
            got.append(_pixels(ws))
        assert ws.player.cart_error is None, (name, ws.player.cart_error)
        ws.exit()
        shots[name] = got
    assert set(shots) == set(titles) and all(len(v) == 3 for v in shots.values())
    ref = shots["python"]
    for name, got in shots.items():
        for i, (a, b) in enumerate(zip(got, ref)):
            if a != b:
                rows = sum(1 for j in range(0, len(a), 640) if a[j:j + 640] != b[j:j + 640])
                pytest.fail("%s step %d: %d rows differ from python" % (name, i, rows))
