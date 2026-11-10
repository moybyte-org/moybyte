"""A responsive app on every tier (docs/app_api_v1.md section 3): one fixture
app -- tests/fixtures/layout/layout_py.moy, layout_lua.moy, layout_wasm.moy
(a compiled app), layout_wasi.moy (a WASI app) and layout_elf.moy (a native
app) -- defines `_layout(w, h, fs)` and pages how many calls it heard and the
last one's arguments. In a window on the desk, each tier's run hears the
same calls with the same arguments and draws the same pixels as its window
is resized, the font scale moves and the window is maximised; and a tier
with `_init` hears `_layout` before it.
"""

import os
import shutil

import pytest

from ws_helpers import build_desktop_ws, make_drv, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "layout")
DT = 1.0 / 30.0
STEADY = 4
TIERS = ("python", "lua", "wasm", "wasi", "elf")
TITLES = {"python": "Layout Py", "lua": "Layout Lua", "wasm": "Layout Wasm",
          "wasi": "Layout Wasi", "elf": "Layout Elf"}


def _frame(drv):
    quiesce(drv.ws)
    drv.ws._dirty = True
    drv.frame(DT)


def _pixels(ws):
    """The run's canvas in its content rect, where every tier draws its page."""
    cv = ws.run.canvas
    x, y, w, h = ws.run.player.ns["content_rect"]()
    buf = cv._buf
    out = bytearray()
    for j in range(y, y + h):
        out += buf[2 * (j * cv.w + x):2 * (j * cv.w + x + w)]
    return (w, h), bytes(out)


def _shot(drv):
    """The page once the app has caught up: a native or WASI app draws on a
    task of its own, so frames run until STEADY presented in a row draw the
    same (test_ui_linked's rule)."""
    last = _pixels(drv.ws)
    same = 0
    for _ in range(600):
        app = drv.ws.run.player._lua
        before = getattr(app, "presented", None)
        _frame(drv)
        now = _pixels(drv.ws)
        if before is not None and app.presented == before:
            continue
        same = same + 1 if now == last else 0
        if same >= STEADY:
            return now
        last = now
    return last


def _surface(ws):
    """The (w, h, fs) a window-bound app lays out for: its window's content
    and the shell's font scale (Player._layout_args)."""
    w, h = ws.app_window_size()
    return (w, h, ws.look.effective_font_scale())


def _script(ws):
    """Settle, resize the window, move the font scale, maximise: a shot and
    the surface after each."""
    drv = make_drv(ws)
    out = []
    for _ in range(3):
        _frame(drv)
    out.append((_surface(ws), _shot(drv)))
    win = ws.wm._wins["desktop"]
    ws.wm._resize_window(win, win.w - 120, win.h - 80)
    out.append((_surface(ws), _shot(drv)))
    ws.look.set_font_scale(3, persist=False)
    out.append((_surface(ws), _shot(drv)))
    ws.wm._toggle_max(ws.wm._wins["desktop"])
    out.append((_surface(ws), _shot(drv)))
    return out


def _host_wasm(wasi):
    from runtime import wasm_host
    from tools import jet_cart
    if wasm_host.available() and jet_cart.wasi_sdk(fetch=False) is not None:
        return True
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding or wasi-sdk is missing")
    return False


def _install(tier, root):
    src = os.path.join(FIX, "layout_%s.moy" % ("py" if tier == "python" else tier))
    dst = os.path.join(root, os.path.basename(src))
    if tier in ("python", "lua"):
        shutil.copytree(src, dst)
    elif tier == "elf":
        from tools import elf_cart
        elf_cart.build(src, dst, ["host"], signed=False)
    else:
        if not _host_wasm(tier == "wasi"):
            return False
        from tools import wasm_cart
        wasm_cart.build(src, dst)
    return True


def _pmem0(ws):
    p = ws.run.player
    if p.ns.get("pmem") is not None and getattr(p, "_lua", None) is None:
        return p.ns["pmem"](0)
    run = getattr(p._lua, "_run", None)
    if run is not None and hasattr(run, "pmem"):
        return list(run.pmem()[1])[0]
    import sys
    from array import array
    img = array("i", bytearray(4 * 256))
    sys.modules[type(p._lua).__module__]._moycore.pmem_image(img)
    return img[0]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Each tier's [(surface, (size, pixels)), ...], whether the run was
    window-bound, and pmem 0 where the tier has `_init`."""
    from runtime import host_app
    out = {}
    for tier in TIERS:
        base = tmp_path_factory.mktemp("layout_" + tier)
        root = str(base / "carts")
        host_app.moy_carts.ensure_dirs(root)
        if not _install(tier, root):
            continue
        ws = build_desktop_ws(base)
        rt = {"wasi": "wasm"}.get(tier, tier)
        if tier != "python" and rt not in ws.runtimes:
            ws.exit()
            continue
        ws.look.set_theme("moy", persist=False)
        ws.pointer.visible = False
        ws.bar_layer._clock_text = lambda: "10:30"
        ws.open_desk()
        cart = next(c for c in ws.carts.all if c["title"] == TITLES[tier])
        ws._open_workspace(cart)
        ws.play(ws.launcher_layer)
        if ws.confirm.open:             # a copied app's consent: Open
            act = ws.confirm._act
            ws.confirm.cancel()
            act()
        p = ws.run.player
        assert p.cart_error is None, (tier, p.cart_error)
        bound = ws.run.app_window_canvas
        shots = _script(ws)
        assert p.cart_error is None, (tier, p.cart_error)
        init = _pmem0(ws) if tier in ("python", "lua", "wasm") else None
        out[tier] = (shots, bound, init)
        ws.exit()
    return out


def test_every_tier_opts_in_with_its_own_layout(runs):
    """The probe each tier answers before the canvas is chosen took: every
    run drew on its window's surface."""
    assert "python" in runs and "lua" in runs
    for tier, (_shots, bound, _init) in runs.items():
        assert bound, "%s ran on the fixed raster" % tier


def test_the_python_app_hears_each_surface_once(runs):
    from runtime import host_app  # noqa: F401 -- the package the runs imported
    shots = runs["python"][0]
    surfaces = [s for s, _ in shots]
    assert len(set(surfaces)) == len(surfaces), "a step did not move the surface"
    assert surfaces[2][2] == 3


@pytest.mark.parametrize("tier", TIERS[1:])
def test_every_tier_draws_the_python_apps_page(runs, tier):
    """The page names the calls and the last call's arguments, so the same
    pixels are the same calls with the same arguments."""
    if tier not in runs:
        pytest.skip("no host %s tier" % tier)
    want = runs["python"][0]
    got = runs[tier][0]
    assert len(got) == len(want)
    for i, ((s, (size, px)), (rs, (rsize, rpx))) in enumerate(zip(got, want)):
        assert s == rs and size == rsize, (tier, i, s, rs)
        assert px == rpx, "%s: step %d drew other pixels than python" % (tier, i)


@pytest.mark.parametrize("tier", ["python", "lua", "wasm"])
def test_layout_comes_before_init(runs, tier):
    if tier not in runs:
        pytest.skip("no host %s tier" % tier)
    assert runs[tier][2] == 1, "_init ran before _layout"


def test_the_page_follows_the_surface(runs):
    """Each step's page differs from the last: the app reflowed."""
    shots = runs["python"][0]
    for a, b in zip(shots, shots[1:]):
        assert a[1] != b[1]


# -- each tier's probe, answered before the canvas is chosen -------------------


def test_the_lua_probe_reads_every_script_at_column_zero():
    from runtime.lua_ext import wants_layout
    assert wants_layout({"src": "function _layout(w, h, fs)\nend\n"})
    assert wants_layout({"src": "x = 1\n_layout = function(w, h, fs) end\n"})
    assert wants_layout({"src": "", "src_after": [("b.lua", "function _layout(w)\nend")]})
    assert not wants_layout({"src": "-- function _layout(w, h, fs)\n"})
    assert not wants_layout({"src": "local function _layout(w, h, fs)\nend\n"})
    assert not wants_layout({"src": "function f()\n  function _layout() end\nend\n"})
    assert not wants_layout({})


def test_the_compiled_probes_read_the_modules_exports(tmp_path):
    from device.moycore_glue import wasm_wants_layout
    from runtime.elf_run import elf_defines
    from tools import elf_cart
    for name, has in (("layout_elf.moy", True), ("../elf/elf_page.moy", False)):
        dst = str(tmp_path / os.path.basename(name))
        elf_cart.build(os.path.join(FIX, name), dst, ["host"], signed=False)
        assert elf_defines(os.path.join(dst, "main.host.elf"), "_layout") is has, name
    if not _host_wasm(False):
        return
    from tools import wasm_cart
    for name, has in (("layout_wasm.moy", True), ("layout_wasi.moy", True),
                      ("../wasm/wasi_page.moy", False), ("../wasm/app_ui.moy", False)):
        dst = str(tmp_path / os.path.basename(name))
        wasm_cart.build(os.path.join(FIX, name), dst)
        assert wasm_wants_layout({"path": dst}) is has, name
