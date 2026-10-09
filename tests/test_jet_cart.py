"""The compiled tier's Jet carts, Jet Teapot and ESP 88, on the host
(ports/jet/README.md; docs/wasm_tier_plan_2026-09.md, "The showcase cart").

What is pinned here:

  * each module tools/jet_cart.py builds imports only the console's table --
    nothing from WASI -- and a sibling moy-spec's `moy check` passes each cart
    with no finding at all; the two carts' imports header (moy-spec's
    moy_cart.h), runtime and font are one body;
  * the frame at two fixed camera poses, full width, half width and half
    width interlaced, as PIXEL GOLDENS: RGB565 frames through the same
    binding and golden mechanism the wasm fixtures use
    (tests/test_wasm_cart.py), with the HUD off, because the HUD's figures
    are the host clock's;
  * Jet's colour byte order against blit565's little-endian rule, with a pixel
    whose colour the test computes on its own: the sky gradient's bottom row;
  * the HUD is drawn INTO the frame the cart blits -- so the whole frame is
    the cart's and a console can show it straight from the cart's memory --
    and it is pixel for pixel what `rect` and `print` would draw over the
    blit: read back off the strip glyph by glyph, drawn again by the console's
    own verbs on the same frame without it, the two frames are byte-identical;
    its glyphs are the console's font;
  * the heap Jet uses stays inside what the declared memory leaves it, and
    flying through the model does not trap;
  * ESP 88: three frames of the film as pixel goldens (the river, the
    boulevard, the credits), its buttons (left and right step the cuts and
    wrap, A shows the HUD, read back glyph by glyph), and its heap's worst
    known case -- the boulevard loaded after the pursuit's queues -- inside
    the heap the build requires;
  * both carts draw on two cores (`par`) as they do on one: the frame their
    bands make, clearing and widening each its own rows, is byte for byte
    the one Jet makes in a single pass, and no item comes near the end of its
    stack. The host runs par's items one after another; the boards' suites
    run them across the cores.

The build needs wasi-sdk 24 (tools/jet_cart.py fetches it by sha256 when it is
absent) and the host wasm binding. Without either the tests SKIP on a bench
and FAIL under `CI` or `MOYBYTE_REQUIRE_HOST_WASM`, where a skip would hide
the tier.
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from runtime import host_app
from ws_helpers import open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_FILE = os.path.join(ROOT, "tests", "shell_goldens", "jet_teapot.json")
FILM_GOLDEN_FILE = os.path.join(ROOT, "tests", "shell_goldens", "jet_esp88.json")
UPDATE_ENV = "MOYBYTE_UPDATE_GOLDENS"
TITLE = "Jet Teapot"
FILM_TITLE = "ESP 88"
CARTS = ("teapot", "esp88")
W, H = 320, 240
DT = 1.0 / 60.0            # the cart's declared rate: one logic tick a frame
HUD_H = 10
CART_BUTTONS = ("left", "right", "up", "down", "a", "b")
PM_HEAP_PEAK_KB, PM_HEAP_KB = 0, 1
PM_ITEM_STACK = 5
# The bytes of stack each par item has (runtime.cpp's ITEM_STACK); the most
# an item may use of it, leaving the rest for a deeper frame than any seen.
ITEM_STACK = 2048


def _required():
    return bool(os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"))


def _skip_or_fail(why):
    if _required():
        pytest.fail(why)
    pytest.skip(why)


@pytest.fixture(scope="module")
def jet():
    """tools.jet_cart with its module built (and cached), or a skip."""
    from runtime import wasm_binding, wasm_host
    from tools import jet_cart
    if not wasm_host.available():
        _skip_or_fail("no host wasm binding: %s" % (wasm_binding.why_unavailable() or "?"))
    try:
        sdk = jet_cart.wasi_sdk(fetch=True)
        jet_cart.compile_wasm(sdk)
    except Exception as exc:  # noqa: BLE001
        _skip_or_fail("the showcase cart did not build: %s" % exc)
    return jet_cart


def _ws(tmp_path, jet, cart="teapot", title=TITLE, **config):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    jet.build(root, config=config, cart=cart)
    ws = host_app.build_workstation(root)
    ws.look.set_theme_variant("dark", persist=False)
    open_cart(ws, title)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert type(ws.player._lua).__name__ == "WasmHostRun"
    ws.player.uncap_mode(True)           # every frame draws: one tick, one frame
    return ws


def _frames(ws, n, hold=(), dt=DT):
    for _ in range(n):
        ws.pointer.visible = False
        ws._toast_until = 0
        ws.show_fps = ws.perf_hud = ws.perf_capture = False
        for b in CART_BUTTONS:
            ws.input.set_held(b, b in hold)
        ws.input.begin_frame()
        ws._dirty = True
        ws.frame(dt)
    assert ws.player.cart_error is None, ws.player.cart_error


def _rgb565(ws, x, y):
    """The canvas pixel as RGB565, whatever order the canvas stores words in."""
    from runtime import wasm_host
    cv = ws.sys_canvas
    i = 2 * (y * cv.w + x)
    word = cv._buf[i] | cv._buf[i + 1] << 8
    if wasm_host._wire_swapped():
        word = ((word >> 8) | (word << 8)) & 0xFFFF
    return word


def _pmem(ws):
    return ws.player._lua._run.pmem()[1]


# -- the module ------------------------------------------------------------------


@pytest.mark.parametrize("cart", CARTS)
def test_the_module_imports_only_the_console(jet, cart):
    wasm = jet.compile_wasm(cart=cart)
    got = jet.imports(wasm)
    assert got and all(m == "moy" for m, _n in got), got
    assert {n for _m, n in got} <= jet.console_imports()
    assert "blit565" in {n for _m, n in got}


@pytest.mark.parametrize("cart", CARTS)
def test_the_module_matches_the_manifest(jet, cart):
    """One memory, the manifest's, minimum equal to maximum, and no absolute
    path in the binary (the build maps them out)."""
    from tools import wasm_module
    wasm = jet.compile_wasm(cart=cart)
    pages = jet.manifest(cart)["memory"]
    mems = [body for sid, _n, body in wasm_module.sections(wasm) if sid == 5]
    assert mems, "no memory section"
    # count 1, flags 1 (has max), min, max
    n, i = wasm_module._read_leb(mems[0], 0)
    assert n == 1 and mems[0][i] == 1
    lo, i = wasm_module._read_leb(mems[0], i + 1)
    hi, _ = wasm_module._read_leb(mems[0], i)
    assert lo == hi == pages
    assert ROOT.encode() not in wasm and b"/home/" not in wasm


@pytest.mark.parametrize("cart", CARTS)
def test_moy_check_passes_the_built_cart(jet, tmp_path, cart):
    """moy-spec's own check, when a checkout sits beside this one: no error
    and no warning -- the binding is SPEC.md 16, so a well-formed compiled
    cart draws none."""
    from vendor_check import spec_checkout
    spec = spec_checkout("moy.py")
    if spec is None:
        pytest.skip("no moy-spec checkout")
    cart = jet.build(str(tmp_path), cart=cart)
    r = subprocess.run([sys.executable, os.path.join(spec, "moy.py"), "check", cart],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    findings = [ln.split()[:2] for ln in r.stdout.splitlines()
                if ln.strip().startswith(("error", "warn"))]
    assert findings == [], r.stdout


def test_the_carts_share_one_runtime():
    """The imports header (moy-spec's moy_cart.h), the heap and C library
    edges, and the HUD's font are one body in both carts' sources: each
    cart's src/ is complete on its own, so the copies are pinned equal rather
    than shared."""
    for name in ("moy_cart.h", "runtime.cpp", "hud_font.h"):
        bodies = set()
        for cart in CARTS:
            with open(os.path.join(ROOT, "ports", "jet", cart + ".moy", "src", name), "rb") as f:
                bodies.add(f.read())
        assert len(bodies) == 1, "%s differs between the Jet carts" % name


def test_the_toolchain_is_the_tier_s(jet):
    """One wasi-sdk for the tier's compiled carts: the pin the other C cart's
    recipe builds with."""
    import importlib.util
    path = os.path.join(ROOT, "experiments", "wasm_aot", "doom", "build_cart.py")
    spec = importlib.util.spec_from_file_location("doom_build_cart", path)
    doom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doom)
    assert (jet.WASI_SDK_URL, jet.WASI_SDK_SHA256) == (doom.WASI_SDK_URL,
                                                       doom.WASI_SDK_SHA256)


# -- the frame --------------------------------------------------------------------

# (name, config, [(frames, buttons held), ...]): the start pose -- the
# example's camera and the Flat third of its shading cycle -- and a flown one
# in Phong: backed off and climbed, then turned a little right.
POSES = (
    ("start", {}, ((8, ()),)),
    ("flown", {"shading": "phong"}, ((24, ("down", "a")), (6, ("right",)))),
)


# The frame's make, by the golden key's first part: an interlaced frame is
# the field it rendered over the one it rendered the frame before.
WIDTHS = {"full": {"width": "full"}, "half": {"width": "half"},
          "half_i": {"width": "half", "interlaced": True}}


def _golden_frame(tmp_path, jet, width, pose):
    _name, config, legs = pose
    ws = _ws(tmp_path, jet, hud=False, **WIDTHS[width], **config)
    for frames, hold in legs:
        _frames(ws, frames, hold)
    assert (ws.sys_canvas.w, ws.sys_canvas.h) == (W, H)
    return hashlib.sha256(bytes(ws.sys_canvas._buf)).hexdigest()


def _check_golden(request, path, key, got, test):
    """`got` against the committed table at `path`. Re-baseline deliberately,
    as the shell goldens are: `MOYBYTE_UPDATE_GOLDENS=1` or
    `--update-goldens`."""
    update = (os.environ.get(UPDATE_ENV)
              or request.config.getoption("--update-goldens", default=False))
    table = {}
    if os.path.isfile(path):
        with open(path) as f:
            table = json.load(f)
    if update:
        table[key] = got
        with open(path, "w") as f:
            json.dump(table, f, indent=2, sort_keys=True)
            f.write("\n")
        return
    assert key in table, "no golden for %s; record it with %s=1" % (key, UPDATE_ENV)
    assert got == table[key], (
        "the frame %s moved (%s != %s). Re-baseline only if you can say "
        "which pixel moved and why: %s=1 .venv/bin/python -m pytest "
        "tests/test_jet_cart.py -k %s" % (key, got, table[key], UPDATE_ENV, test))


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("pose", POSES, ids=[p[0] for p in POSES])
def test_the_frame_golden(tmp_path, request, jet, width, pose):
    """The frame the cart presents at a fixed pose, hashed against committed
    bytes."""
    key = "%s_%s" % (width, pose[0])
    _check_golden(request, GOLDEN_FILE, key,
                  _golden_frame(tmp_path, jet, width, pose), "golden")


def _sky(y):
    """The scene's background gradient at row y, as RGB565 -- the example's
    formula, restated here so the colour is known independently of Jet."""
    r = 12 + y * 24 // H
    g = 28 + y * 56 // H
    b = 54 + y * 72 // H
    return (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)


@pytest.mark.parametrize("width", ("full", "half"))
def test_jet_s_byte_order_is_blit565_s(tmp_path, jet, width):
    """Jet stores RGB565 in the machine's order, and wasm's is little-endian,
    which is blit565's: no swap in the cart. The bottom row's corners are sky
    at the start pose, a colour whose two byte orders differ."""
    ws = _ws(tmp_path, jet, width=width, hud=False)
    _frames(ws, 2)
    want = _sky(H - 1)
    swapped = ((want >> 8) | (want << 8)) & 0xFFFF
    assert want != swapped
    for x in (0, 1, W - 2, W - 1):
        assert _rgb565(ws, x, H - 1) == want, (x, hex(_rgb565(ws, x, H - 1)), hex(want))


def _hud_line(ws):
    """The HUD's line, read back off the strip one 8px cell at a time against
    the console's font: a cell that is not exactly a glyph is a failure."""
    from runtime import font
    glyphs = {}
    for i in range(95, -1, -1):          # the first of equal bitmaps wins: ' '
        glyphs[font._FONT[i * 8:(i + 1) * 8]] = chr(0x20 + i)
    out = []
    for k in range((W - 2) // 8):
        cols = bytes(sum(1 << b for b in range(8)
                         if _rgb565(ws, 2 + 8 * k + j, 1 + b) != 0)
                     for j in range(8))
        assert cols in glyphs, "cell %d is not a glyph: %s" % (k, cols.hex())
        out.append(glyphs[cols])
    return "".join(out).rstrip()


def test_the_hud_is_the_frames_and_what_the_verbs_would_draw(tmp_path, jet):
    """The same frame with and without the HUD differs in the HUD's strip and
    nowhere else, and the strip holds the HUD's two palette colours. Drawn
    again by the console's own rect and print over the frame without it, the
    line read back off the strip makes the two frames byte-identical: the
    HUD the cart draws into its frame is the one the verbs used to draw."""
    # One run at a time, as on a board: each console plays its frames before
    # the next launches.
    on = _ws(tmp_path / "on", jet, hud=True, shading="phong")
    _frames(on, 4)
    off = _ws(tmp_path / "off", jet, hud=False, shading="phong")
    _frames(off, 4)
    a, b = bytes(on.sys_canvas._buf), bytes(off.sys_canvas._buf)
    row = 2 * W
    assert a[HUD_H * row:] == b[HUD_H * row:]
    assert a[:HUD_H * row] != b[:HUD_H * row]
    ink = {_rgb565(on, x, y) for y in range(HUD_H) for x in range(W)}
    assert len(ink) == 2, sorted(map(hex, ink))
    assert _rgb565(off, W // 2, 0) == _sky(0)
    assert _rgb565(on, W - 1, 0) != _sky(0)
    line = _hud_line(on)
    assert line.startswith("FULL PHONG  ") and line.endswith("T"), line
    cv = off.sys_canvas
    cv.reset_state()
    cv.rect(0, 0, W, HUD_H, 0)
    cv.print(line, 2, 1, 7)
    assert bytes(cv._buf) == a


def test_the_hud_font_is_the_consoles():
    """src/hud_font.h is runtime/font.py's bytes, glyph for glyph."""
    import re
    from runtime import font
    path = os.path.join(ROOT, "ports", "jet", "teapot.moy", "src", "hud_font.h")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    body = text[text.index("HUD_FONT[96 * 8] = {"):]
    body = body[:body.index("};")]
    body = re.sub(r"//[^\n]*", "", body)
    got = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-f]{2})", body))
    assert got == font._FONT


# -- memory and flight --------------------------------------------------------------


def test_the_heap_fits_the_declared_memory(tmp_path, jet):
    """The declared memory leaves the heap room for the model's load and
    Jet's queues, with a quarter to spare; runtime.cpp reports its peak."""
    ws = _ws(tmp_path, jet)
    _frames(ws, 30, ("right",))
    pm = _pmem(ws)
    peak, size = pm[PM_HEAP_PEAK_KB], pm[PM_HEAP_KB]
    assert 0 < peak and 0 < size
    assert peak * 4 <= size * 3, (peak, size)


@pytest.mark.parametrize("width", WIDTHS)
def test_two_cores_draw_the_frame_one_core_draws(tmp_path, jet, width):
    """The teapot's bands -- each clearing, rasterizing and widening its own
    rows -- make the frame Jet makes in one pass, at every width, over a
    flight that crosses the near plane."""
    frames = []
    for cores in (1, 2):
        ws = _ws(tmp_path / str(cores), jet, hud=False, cores=cores, shading="phong",
                 **WIDTHS[width])
        _frames(ws, 20, ("up", "right"))
        _frames(ws, 10, ("down", "a"))
        frames.append(bytes(ws.sys_canvas._buf))
        pm = _pmem(ws)
        assert pm[PM_ITEM_STACK] <= ITEM_STACK // 2, pm[PM_ITEM_STACK]
    assert frames[0] == frames[1]


@pytest.mark.parametrize("width", ("full", "half"))
def test_flying_through_the_teapot_does_not_trap(tmp_path, jet, width):
    """Forward through the model and out the other side, climbing, then
    turning back: near-plane clipping, the camera inside the mesh, and the
    interlaced fields, with no trap."""
    ws = _ws(tmp_path, jet, width=width, interlaced=True)
    _frames(ws, 150, ("up",))
    _frames(ws, 60, ("up", "a", "left"))
    _frames(ws, 60, ("down", "b"))


# -- ESP 88 ---------------------------------------------------------------------

FILM_DT = 1.0 / 30.0
FILM_TOP = 21                # (240 - 198) / 2: the picture between the bars
FILM_H = 198
PM_CUT = 5

# (name, config, frames at FILM_DT): the river two seconds in, fading up
# with its reflection; the boulevard three seconds in, the car turning out
# of the side street; the credits held, four and a half seconds into the
# last cut.
FILM_POSES = (
    ("river", {"cut": 1}, 60),
    ("boulevard", {"cut": 7}, 90),
    ("credits", {"cut": 12}, 135),
)


def _film(tmp_path, jet, **config):
    return _ws(tmp_path, jet, cart="esp88", title=FILM_TITLE, **config)


@pytest.mark.parametrize("pose", FILM_POSES, ids=[p[0] for p in FILM_POSES])
def test_the_film_frame_golden(tmp_path, request, jet, pose):
    """The frame ESP 88 presents at a fixed time in a cut, hashed against
    committed bytes, with the letterbox above and below it black."""
    name, config, frames = pose
    ws = _film(tmp_path, jet, hud=False, **config)
    _frames(ws, frames, dt=FILM_DT)
    buf = bytes(ws.sys_canvas._buf)
    row = 2 * W
    assert not any(buf[:FILM_TOP * row]) and not any(buf[(FILM_TOP + FILM_H) * row:])
    assert any(buf[FILM_TOP * row:(FILM_TOP + FILM_H) * row])
    _check_golden(request, FILM_GOLDEN_FILE, name, hashlib.sha256(buf).hexdigest(),
                  "film_frame")


def test_the_film_s_buttons_step_the_cuts_and_show_the_hud(tmp_path, jet):
    """A shows the HUD in the top bar: the cut's number and name, then the
    rates. Right steps to the next cut, left to the one before, and both
    wrap round the film."""
    ws = _film(tmp_path, jet)
    _frames(ws, 2, dt=FILM_DT)
    assert not any(bytes(ws.sys_canvas._buf)[:FILM_TOP * 2 * W])
    _frames(ws, 1, ("a",), dt=FILM_DT)
    _frames(ws, 1, dt=FILM_DT)
    assert _hud_line(ws).startswith("01 THE RIVER  "), _hud_line(ws)
    assert _hud_line(ws).endswith("MS"), _hud_line(ws)
    for hold, want in (("left", "12 ESP 88  "), ("right", "01 THE RIVER  "),
                       ("right", "02 RAIN DISTRICT  ")):
        _frames(ws, 1, (hold,), dt=FILM_DT)
        _frames(ws, 1, dt=FILM_DT)
        assert _hud_line(ws).startswith(want), _hud_line(ws)
        assert _pmem(ws)[PM_CUT] == int(want[:2])
    _frames(ws, 1, ("a",), dt=FILM_DT)
    _frames(ws, 1, dt=FILM_DT)
    assert not any(bytes(ws.sys_canvas._buf)[:FILM_TOP * 2 * W])


def test_the_film_s_heap_holds_its_worst_known_case(tmp_path, jet):
    """Jet's per-frame queues keep the capacity of the busiest frame drawn,
    and the pursuit's is the largest: the boulevard's city loaded after it is
    the heap's high-water mark. Played that way, the peak stays inside the
    heap the build requires of the manifest's memory, and the film never
    traps for want of it."""
    ws = _film(tmp_path, jet, cut=7)
    _frames(ws, 30 * 26, dt=FILM_DT)          # the boulevard, then the pursuit
    assert _pmem(ws)[PM_CUT] == 9
    for _ in range(2):                         # back to the boulevard
        _frames(ws, 1, ("left",), dt=FILM_DT)
        _frames(ws, 20, dt=FILM_DT)
    pm = _pmem(ws)
    assert pm[PM_CUT] == 7
    peak, size = pm[PM_HEAP_PEAK_KB], pm[PM_HEAP_KB]
    need = jet.cart_spec("esp88").heap_min // 1024
    assert 0 < peak <= need <= size, (peak, need, size)


FILM_PM_ITEM_STACK = 7


@pytest.mark.parametrize("interlaced", (True, False))
def test_the_film_on_two_cores_is_the_film_on_one(tmp_path, jet, interlaced):
    """The film's raster and scan-out in two bands each make the frames the
    single pass makes: the pursuit, with its reflections and sprites, both
    fields."""
    frames = []
    for cores in (1, 2):
        ws = _film(tmp_path / str(cores), jet, hud=False, cut=8, cores=cores,
                   interlaced=interlaced)
        _frames(ws, 45, dt=FILM_DT)
        frames.append(bytes(ws.sys_canvas._buf))
        assert _pmem(ws)[FILM_PM_ITEM_STACK] <= ITEM_STACK // 2
    assert frames[0] == frames[1]


@pytest.mark.parametrize("interlaced", (True, False))
def test_the_film_draws_one_field_a_frame_or_both(tmp_path, jet, interlaced):
    """Interlaced, as the example's runtime plays it, a frame draws one
    field's rows and leaves the other's as they were -- black, the first
    time; with `"interlaced": false` it draws both. The courier's street
    opens with no fade, so every drawn row has colour in it."""
    ws = _film(tmp_path, jet, cut=3, interlaced=interlaced)
    _frames(ws, 1, dt=FILM_DT)
    buf = bytes(ws.sys_canvas._buf)
    row = 2 * W
    lit = [any(buf[(FILM_TOP + y) * row:(FILM_TOP + y + 1) * row]) for y in range(FILM_H)]
    if interlaced:
        assert lit[1::2] == [True] * (FILM_H // 2) and not any(lit[0::2]), lit
    else:
        assert all(lit), lit
