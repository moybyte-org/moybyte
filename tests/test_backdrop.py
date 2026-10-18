"""A theme's backdrop (docs/look_2026-10.md step 7, docs/media/look_2026-10/GAPS.md
gap 9): `backdrop.home` and `backdrop.desk`, read by the loader
(native/moy_ui/moy_theme.c, moy_theme_backdrop) and rasterised by
native/moy_gfx/moy_backdrop.c into the one wallpaper bake (runtime/wallpaper.py).

* Every shape kind and paint mode against the mockups' own renderer
  (docs/media/look_2026-10/mockups.py `backdrop`) at 320x240, 1024x600 and
  1280x800: interiors exact, edges within a coverage level or two.
* The kernel's refusals (malformed records draw the fill alone), its clip and
  origin, its byte order.
* The loader's records for the moy seed, and the shell: the bake rasters once
  per theme load and mode switch, never on a frame that moves a carousel or
  a window.
"""

import importlib.util
import json
import os
import struct
import sys

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import gfx_binding as gfx
from ws_helpers import use_theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "native", "moy_ui", "style.json")) as _f:
    BD = json.load(_f)["theme"]["look"]["backdrop"]
KINDS = BD["kinds"]
SIZES = ((320, 240), (1024, 600), (1280, 800))


def _mockups():
    path = os.path.join(ROOT, "docs", "media", "look_2026-10", "mockups.py")
    spec = importlib.util.spec_from_file_location("look_mockups", path)
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, sys.argv[:1]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


M = _mockups()


def record(sh):
    """A mockup shape as the kernel's record (moy_backdrop.h)."""
    p = sh["p"]
    stops = p if isinstance(p, list) else [p]
    stops = [(round(i * 1000 / (len(stops) - 1)) if len(stops) > 1 else 0, c)
             for i, c in enumerate(stops)]
    m = sh.get("mode", "smooth")
    mode, steps = (1, int(m.split(":")[1])) if m.startswith("steps") else \
        ({"smooth": 0, "dither": 2}[m], 0)
    flat = [v for a in sh.get("at") or [] for v in (a if isinstance(a, list) else [a])]
    b = bytes([KINDS.index(sh["k"]), mode, steps, round(sh.get("a", 1) * 255),
               len(stops), len(flat)])
    for t, c in stops:
        b += struct.pack("<h", t) + bytes.fromhex(c[1:])
    return b + b"".join(struct.pack("<h", v) for v in flat)


def word(c):
    r, g, b = M.q(c)
    return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3


def rgb(buf):
    out = []
    for i in range(0, len(buf), 2):
        w = buf[i] | buf[i + 1] << 8
        out.append(M.q((((w >> 11) & 31) << 3, ((w >> 5) & 63) << 2, (w & 31) << 3)))
    return out


def draw(rec, w, h, bg=0x1234, swap=0):
    buf = bytearray(w * h * 2)
    n = gfx.backdrop(buf, w, 0, 0, w, h, rec, bg, swap, 0, 0, w, h)
    return n, buf


CASES = {
    "fill": [{"k": "fill", "p": "#3366cc"}],
    "band": [{"k": "band", "at": [200, 720], "p": ["#1b5ad3", "#3a80e2", "#86b9ef"]}],
    "circle": [{"k": "circle", "at": [801, 850, 433], "p": "#191c3e"}],
    "ellipse": [{"k": "ellipse", "at": [270, 170, 120, 48], "p": "#ffffff", "a": 0.85}],
    "rrect": [{"k": "rrect", "at": [586, 73, 234, 150, 75], "p": "#ffd23f"}],
    "pie": [{"k": "pie", "at": [996, 433, 233, 180, 270], "p": "#7cc8ff"}],
    "pie_wide": [{"k": "pie", "at": [500, 500, 300, 30, 300], "p": "#7cc8ff"}],
    "wave": [{"k": "wave", "at": [[0, 600], [260, 560], [560, 610], [820, 540], [1000, 580]],
              "p": ["#8fd04e", "#5aa12a"]}],
    "dither": M._PICO,
    "steps": M._COPPER,
    "dunes": M._DUNES,
    "bliss": M._BLISS,
    "blocks_desk": M.BD_C["desk"],
}


@pytest.mark.parametrize("size", SIZES, ids=lambda s: "%dx%d" % s)
@pytest.mark.parametrize("case", sorted(CASES))
def test_each_shape_draws_as_the_mockups_draw_it(case, size):
    w, h = size
    shapes = CASES[case]
    n, buf = draw(b"".join(record(s) for s in shapes), w, h, word("#f0f0f0"))
    assert n == len(shapes)
    cv = M.Cv(w, h, "#f0f0f0")
    M.backdrop(cv, {"backdrop": {"desk": shapes}}, "desk")
    ref = list(cv.img.get_flattened_data() if hasattr(cv.img, "get_flattened_data")
               else cv.img.getdata())
    got = rgb(buf)
    d = [max(abs(a[k] - b[k]) for k in range(3)) for a, b in zip(got, ref)]
    # Interiors exact; an edge pixel's coverage within a level or two of the
    # mockups' 4x4 supersampling.
    assert sum(1 for v in d if v > 8) <= 0.01 * len(d), case
    assert sum(d) / len(d) <= 0.5, case


def test_malformed_records_draw_the_fill_alone():
    good = record({"k": "circle", "at": [500, 500, 300], "p": "#ff0000"})
    bad = [good[:-1], bytes([7]) + good[1:], good[:4] + bytes([5]) + good[5:],
           good[:5] + bytes([4]) + good[6:], good * 17,
           record({"k": "wave", "at": [[0, 0]] * 2, "p": "#000000"})[:5] + bytes([3]),
           bytes([0, 1, 1, 255, 1, 0, 0, 0, 1, 2, 3])]
    for rec in bad:
        n, buf = draw(rec, 64, 48)
        assert n == -1
        assert buf == bytes((0x34, 0x12)) * (64 * 48)
    assert draw(b"", 8, 8) == (0, bytearray(bytes((0x34, 0x12)) * 64))
    assert draw(good * 16, 8, 8)[0] == 16


def test_the_clip_the_origin_and_the_byte_order():
    rec = b"".join(record(s) for s in M._BLISS)
    _n, whole = draw(rec, 320, 240, 0x5555)
    big = bytearray(400 * 300 * 2)
    assert gfx.backdrop(big, 400, 30, 20, 320, 240, rec, 0x5555, 0, 100, 60, 250, 200) == 7
    for y in range(300):
        for x in range(400):
            o = 2 * (y * 400 + x)
            if 100 <= x < 250 and 60 <= y < 200:
                i = 2 * ((y - 20) * 320 + x - 30)
                assert big[o:o + 2] == whole[i:i + 2]
            else:
                assert big[o:o + 2] == b"\0\0"
    _n, sw = draw(rec, 320, 240, 0x5555, swap=1)
    assert sw == bytes(b for i in range(0, len(whole), 2) for b in (whole[i + 1], whole[i]))


def _decode(rec):
    out = []
    k = 0
    while k < len(rec):
        kind, mode, steps, alpha, ns, nat = rec[k:k + 6]
        k += 6
        stops = [(struct.unpack_from("<h", rec, k + 5 * i)[0],
                  "#%02x%02x%02x" % tuple(rec[k + 5 * i + 2:k + 5 * i + 5])) for i in range(ns)]
        k += 5 * ns
        at = list(struct.unpack_from("<%dh" % nat, rec, k))
        k += 2 * nat
        out.append((KINDS[kind], mode, steps, alpha, stops, at))
    return out


def test_the_moy_seed_carries_its_desks():
    with open(os.path.join(ROOT, "native", "moy_ui", "themes", "moy.json")) as f:
        look = json.load(f)["look"]
    for light, mode in ((False, "dark"), (True, "light")):
        want = [(s["k"], 0, 0, 255, [(0, s["p"])], s["at"])
                for s in look[mode]["backdrop"]["desk"]]
        assert _decode(moy_ui.theme_backdrop("moy", light, "desk")) == want
        assert moy_ui.theme_backdrop("moy", light, "home") == b""
        # a package of no look of its own takes none from the default
        assert moy_ui.theme_install(b'{"format": 2, "name": "bare"}')[0] == 0
        assert moy_ui.theme_backdrop("bare", light, "desk") == b""


def _at(drv, x, y):
    """Pixel (x, y) of the shown frame, as its RGB565 word shows it."""
    out = drv.rgb888()
    w = drv.ws.sys_canvas.w
    if isinstance(out, (bytes, bytearray)):
        px = tuple(out[3 * (y * w + x):3 * (y * w + x) + 3])
    else:
        px = tuple(out[y * w + x])
    return M.q(px)


def _theme(name, backdrop, **kw):
    d = {"format": 2, "name": name, "inherits": "moy", "look": {"backdrop": backdrop}}
    d.update(kw)
    return json.dumps(d).encode()


HOME = [{"k": "fill", "p": ["#1d2b53", "#7e2553"], "mode": "dither"},
        {"k": "circle", "at": [500, 470, 520], "p": "#ffe066", "a": 0.5}]


def test_the_backdrop_rasters_once_per_theme_load_on_the_desk(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True, panel_diagonal_in=7.0)
    use_theme(ws, "night", "dark")                 # a wallpaper cart, no backdrop
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    ws.look.set_theme("moy", variant="light")
    for _ in range(6):
        drv.frame(1 / 30)
    assert ws.screen == "desk" and ws.look.wallpaper_id is None
    assert ws.wallpaper.rasters == 1
    assert _at(drv, 200, 595) == M.q("#e5e3f1")       # Day's left disc
    ws.open_app(ws._apps_by_id["calc"])
    drv.frame(1 / 30)
    win = ws.wm._wins["calc"]
    for i in range(12):                                # a window moving over it
        win.x += 9
        ws.wm.dirty = True
        drv.frame(1 / 30)
    assert ws.wallpaper.rasters == 1
    ws.look.set_theme_variant("dark")
    for _ in range(6):
        drv.frame(1 / 30)
    assert ws.wallpaper.rasters == 2
    assert _at(drv, 600, 595) == M.q("#191c3e")       # Night's crescent


def test_a_home_backdrop_rasters_once_under_the_carousel(tmp_path):
    from ws_helpers import build_ws_with_shelf
    from runtime import host_app
    ws = build_ws_with_shelf(tmp_path, 8)
    drv = host_app.ConsoleDriver(ws)
    assert ws.look.install_theme(_theme("bdhome", {"home": HOME}))[0] == 0
    ws.look.set_theme("bdhome", variant="dark")
    drv.frame(1 / 30)
    assert ws.screen == "launcher" and ws.look.wallpaper_id is None
    assert ws.wallpaper.rasters == 1
    for _ in range(4):
        drv.press("right")
        for _ in range(8):
            drv.frame(1 / 30)
    assert ws.wallpaper.rasters == 1
    assert _at(drv, 1, 239) == M.q("#7e2553")         # the dither's last row
    ws.look.uninstall_theme()


def test_a_malformed_list_reads_as_no_backdrop():
    bad = [{"k": "circle", "at": [1, 2], "p": "#000000"}]
    assert moy_ui.theme_install(_theme("bdbad", {"desk": bad, "home": HOME}))[0] == 0
    assert moy_ui.theme_backdrop("bdbad", False, "desk") == b""
    assert len(_decode(moy_ui.theme_backdrop("bdbad", False, "home"))) == 2
    moy_ui.theme_uninstall()


# -- the loader's caps, clamps and refusals --------------------------------------

N_CLAMPED, N_UNKNOWN, N_DROPPED = 1, 2, 4
DISC = {"k": "circle", "at": [500, 500, 100], "p": "#ff0000"}


def _read(backdrop, surface="desk", light=False, **look):
    rc, notes = moy_ui.theme_install(json.dumps(
        {"format": 2, "name": "bdt", "look": dict(look, backdrop=backdrop)}).encode())
    assert rc == 0
    try:
        return notes, _decode(moy_ui.theme_backdrop("bdt", light, surface))
    finally:
        moy_ui.theme_uninstall()


@pytest.mark.parametrize("shape", [
    {"k": "circle", "at": [1, 2], "p": "#000000"},            # a count short
    {"k": "fill", "at": [1], "p": "#000000"},                  # a count over
    {"k": "teapot", "p": "#000000"},                           # no such kind
    {"at": [1, 2, 3], "p": "#000000"},                         # no kind
    {"k": "circle", "at": [1, 2, 3]},                          # no paint
    {"k": "circle", "at": [1, 2, 3], "p": "red"},              # no colour
    {"k": "circle", "at": [1, 2, 3], "p": ["#000000"] * 5},    # five stops
    {"k": "circle", "at": [1, 2, 3], "p": "#000000", "mode": "steps:1"},
    {"k": "circle", "at": [1, 2, 3], "p": "#000000", "a": "half"},
    {"k": "circle", "at": [[1, 2], [3, 4]], "p": "#000000"},   # points, not numbers
    {"k": "wave", "at": [[0, 1]], "p": "#000000"},             # one point
    {"k": "wave", "at": [[0, 1]] * 9, "p": "#000000"},         # nine points
    {"k": "wave", "at": [0, 1, 2, 3], "p": "#000000"},         # numbers, not points
    {"k": "wave", "at": [[0, 1], [2]], "p": "#000000"},        # half a point
    "circle",
])
def test_a_malformed_shape_makes_its_list_no_backdrop(shape):
    notes, got = _read({"desk": [DISC, shape], "home": [DISC]})
    assert got == [] and notes & N_DROPPED
    assert _read({"desk": [DISC, shape], "home": [DISC]}, "home")[1] != []


def test_a_list_is_capped_and_its_coordinates_clamped():
    notes, got = _read({"desk": [DISC] * 20})
    assert len(got) == 16 and notes == N_DROPPED
    notes, got = _read({"desk": [{"k": "rrect", "at": [-5000, 9999, 10.6, 0, -1],
                                  "p": "#000000", "a": 3}]})
    assert notes == N_CLAMPED
    assert got[0][5] == [-1000, 2000, 11, 0, -1] and got[0][3] == 255


def test_what_the_reader_gets_past():
    notes, got = _read({"desk": [dict(DISC, teapot=1)], "porch": [DISC]})
    assert notes == N_UNKNOWN and len(got) == 1
    notes, got = _read({"desk": "circles"})
    assert notes == N_DROPPED and got == []
    notes, got = _read({"desk": []})
    assert notes == 0 and got == []


def test_the_pool_holds_what_fits():
    full = [DISC] * 16
    dark = {"backdrop": {"desk": [DISC]}}
    notes, got = _read({"desk": full, "home": full}, dark=dark)
    assert notes == N_DROPPED and len(got) == 1      # dark's list, read first
    assert len(_read({"desk": full, "home": full}, light=True, dark=dark)[1]) == 16
    assert _read({"desk": full, "home": full}, "home", dark=dark)[1] == []   # no room
    assert len(_read({"desk": full, "home": full}, "home")[1]) == 16


def test_a_variant_list_comes_before_the_shared_one_and_inherits():
    rc, _n = moy_ui.theme_install(json.dumps(
        {"format": 2, "name": "bdt", "inherits": "moy",
         "look": {"backdrop": {"home": [DISC]}}}).encode())
    assert rc == 0
    try:
        assert len(_decode(moy_ui.theme_backdrop("bdt", False, "home"))) == 1
        assert moy_ui.theme_backdrop("bdt", True, "desk") == \
            moy_ui.theme_backdrop("moy", True, "desk")
    finally:
        moy_ui.theme_uninstall()


def test_a_stop_list_reads_its_positions_and_its_colours():
    _n, got = _read({"desk": [{"k": "fill", "p": [[0, "#000000"], [0.25, 7], [1, "#ffffff"]],
                               "mode": "steps:8"}]})
    (_k, mode, steps, _a, stops, _at), = got
    assert (mode, steps) == (1, 8)
    assert stops == [(0, "#000000"), (250, "#fff1e8"), (1000, "#ffffff")] or \
        [s[0] for s in stops] == [0, 250, 1000]


def test_the_benchs_backdrop_snippets_run_on_the_host(tmp_path):
    """tools/p4_bench.py --backdrop's on-board code, against the host's
    console: the switch makes one raster, the timed raster answers."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import p4_bench
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True, panel_diagonal_in=7.0)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    import time as _t
    from types import SimpleNamespace
    ns = {"ws": ws, "time": SimpleNamespace(ticks_us=lambda: int(_t.perf_counter() * 1e6),
                                            ticks_diff=lambda a, b: a - b)}
    exec(p4_bench.BACKDROP_SETUP.replace("import time, moy_ui", "import moy_ui"), ns)
    assert ws._bd_rc[0] == 0
    ns["_bd_switch"]("bench-bd", "light")
    drv.frame(1 / 30)
    assert ws.wallpaper.rasters - ws._bd_r0 == 1
    nbytes, us = ns["_bd_raster"]("desk")
    assert nbytes == len(ws.look.backdrop("desk")) and us >= 0
    ws.open_library()
    drv.frame(1 / 30)
    assert ns["_bd_raster"]("home")[0] == len(ws.look.backdrop("home"))
    ws.look.uninstall_theme()
