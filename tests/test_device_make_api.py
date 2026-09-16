"""The device make_api and the editor cores it hands a cart, EXECUTED under
CPython through the T-Deck's own module loader.

`_load_moy_runtime` registers the shared runtime modules under the names the
board freezes them as and then execs the board's `moy_runtime.py`, so
`device_api.make_api` -- the ONE make_api body every tier runs -- is driven
here with stub canvases: the spr() dispatch (sheet tile / Image / span /
flip), map/mget/mset over an injected TileMap, and the tile cache a sprite
edit must bust. The editor-core tests beside them exercise the canonical
`runtime/editors.py` the same way.
"""

import importlib.util
import sys
from pathlib import Path

from ws_helpers import StubInput


ROOT = Path("firmware/lilygo_t_deck_plus_mainline")
DEVICE = Path("device")

# The by-path `device_api` the last _load_moy_runtime executed: the make_api
# these tests drive. Kept here rather than read back from sys.modules, because
# the loaders below RESTORE sys.modules once the board module has executed --
# a bare-name `device_canvas`/`editors` left registered is what a later
# suite's `isinstance`/`type(...) is` check against the package's class trips
# over.
_DEVICE_API = None


def _swap_in(saved, name, mod):
    if name not in saved:
        saved[name] = sys.modules.get(name)
    sys.modules[name] = mod


def _swap_back(saved):
    for name, prev in saved.items():
        if prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev


def _editor_class(name):
    """An editor core, from its canonical source.

    The fork's moy_runtime re-exported these (`from editors import ...`) and the
    tests reached through it. The mainline port does not: the board gets
    `editors.py` as a STAGED shared module, so "is it wired" is a staging
    question, asked by test_editor_cores_are_staged_to_the_board below, and
    "does it work" is asked of the canonical file.
    """
    spec = importlib.util.spec_from_file_location("editors", Path("runtime") / "editors.py")
    mod = importlib.util.module_from_spec(spec)
    saved = {}
    _swap_in(saved, "editors", mod)
    try:
        spec.loader.exec_module(mod)
    finally:
        _swap_back(saved)
    return getattr(mod, name)


def _load_moy_runtime():
    global _DEVICE_API
    saved = {}
    # moy_runtime does `from editors import ...` and `from console import ...`; the
    # device freezes build-staged copies of runtime/{editors,audio,console}.py as
    # top-level modules. Register those same canonical files so the device module
    # loads under CPython (editors [+ block_editor_ui #29 Part 2 / map_editor_ui #32
    # / music_editor_ui #50 / perf_hud #43/#44] + audio first -- console imports
    # all of them).
    for name in ("editors", "block_editor_ui", "map_editor_ui", "scene_editor_ui",
                 "music_editor_ui",
                 "perf_hud", "update_ui", "system_menu_ui", "achievements_ui",
                 "layers", "bar_layer", "cards_layer", "paint_layer", "settings_layer", "code_layer", "widgets", "audio", "wallpaper", "launcher_layer", "console"):
        spec = importlib.util.spec_from_file_location(name, Path("runtime") / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        _swap_in(saved, name, mod)
        spec.loader.exec_module(mod)

    # moy_runtime now also does `from device_util import ...` / `from device_wifi
    # import ...` -- device-only modules authored in the shared device/ tree at
    # the repo root (staged into modules/ at build; the staged copies are
    # gitignored, so a fresh checkout has none). Register them from device/ so
    # the device module execs under CPython (device_util first: device_wifi
    # imports it).
    for dname in ("device_util", "device_wifi", "device_input", "device_diag",
                  "device_audio", "device_canvas", "device_api"):
        ds = importlib.util.spec_from_file_location(
            dname, DEVICE / (dname + ".py"))
        dmod = importlib.util.module_from_spec(ds)
        _swap_in(saved, dname, dmod)
        ds.loader.exec_module(dmod)

    # moy_runtime now does `from carts_data import CARTS` (build-generated from
    # system_carts/ -- see tools/gen_device_carts.py). Register the same generated
    # data so the device module execs under CPython.
    sys.path.insert(0, "tools")
    import gen_device_carts
    _swap_in(saved, "carts_data", gen_device_carts.as_module("system_carts"))

    spec = importlib.util.spec_from_file_location(
        "moy_runtime", ROOT / "modules" / "moy_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        _DEVICE_API = sys.modules["device_api"]
    finally:
        _swap_back(saved)
    return module


def _device_make_api():
    """THE make_api the device runs: `device_api`'s, which the boot spine
    hands to the console (the board module imports nothing of it)."""
    return _DEVICE_API.make_api


def test_code_editor_edits_buffer():
    CodeEditor = _editor_class('CodeEditor')
    ed = CodeEditor("def _draw():\n    cls(1)\n")
    assert ed.lines == ["def _draw():", "    cls(1)", ""]

    # type at the end of line 1
    ed.row, ed.col = 1, len(ed.lines[1])
    for ch in " hi":
        ed.key(ord(ch))
    assert ed.lines[1] == "    cls(1) hi"
    assert ed.dirty

    # enter splits and carries the indentation (kid-friendly Python)
    ed.row, ed.col = 1, len(ed.lines[1])
    ed.key(0x0D)
    assert ed.lines[2] == "    " and len(ed.lines) == 4

    # backspace at column 0 joins with the previous line
    ed.row, ed.col = 2, 0
    ed.key(0x08)
    assert len(ed.lines) == 3 and ed.lines[1] == "    cls(1) hi    "

    # tab inserts two spaces; control bytes are ignored
    n = len(ed.lines[ed.row])
    assert ed.key(0x09) and len(ed.lines[ed.row]) == n + 2
    assert ed.key(0x01) is False

    # tap-to-place clamps into range and round-trips through text()
    ed.place(999, 999)
    assert ed.row == len(ed.lines) - 1 and ed.col == len(ed.lines[-1])
    assert ed.text() == "\n".join(ed.lines)


def test_device_sprite_sheet_and_paint_editor():
    S, P = _editor_class('SpriteSheet'), _editor_class('PaintEditor')
    # spec=False throughout this block: these are small fixtures for the sheet's
    # own tile arithmetic and the paint editor, none of which reaches libmoy. A
    # real CART sheet is 16x32 (SPEC.md 3.2) -- see editors_sheet's module note.
    sh = S(4, 4, spec=False)                # 32x32, 16 sprites
    assert sh.count == 16 and (sh.w, sh.h) == (32, 32) and sh.is_blank()
    assert sh.tile_origin(5) == (8, 8)
    sh.tset(5, 1, 2, 9)
    assert sh.tget(5, 1, 2) == 9 and sh.dirty
    sh2 = S.from_hex(sh.to_hex(), 4, 4, spec=False)   # hex round-trips, dirty resets
    assert sh2.pix == sh.pix and sh2.dirty is False
    pe = P(sh)
    pe.color = 12
    pe.paint(0, 0)
    assert sh.tget(0, 0, 0) == 12
    pe.pick(0, 0)
    assert pe.color == 12
    pe.select(-1)
    assert pe.n == sh.count - 1


def test_device_spr_is_sheet_indexed_and_accepts_image():
    _load_moy_runtime()               # registers device_api, the make_api home
    sheet = _editor_class('SpriteSheet')(4, 4, spec=False)      # small fixture, not a cart sheet
    sheet.tset(3, 0, 0, 11)
    calls = []
    tiles = []

    class StubCanvas:
        w = 320
        h = 240

        def spr(self, img, x, y, scale=1, flip=0):
            calls.append((img.w, img.h, x, y, scale, flip))

        def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
            tiles.append((tile, x, y, colorkey, scale, flip))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    api = _device_make_api()(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](3, 100, 60)                  # 1x1 sheet tile -> auto-batch via spr_tile (#63)
    assert tiles[-1] == (3, 100, 60, -1, 1, 0)
    # Image now lives in device_canvas (make_api exposes it in the cart ns).
    api["spr"](api["Image"].from_ascii(["#"], {"#": 7}), 8, 9, scale=4)  # Image -> immediate spr
    assert calls[-1] == (1, 1, 8, 9, 4, 0)
    # Multi-tile span (#30): spr(n, x, y, w=2, h=2) blits a 16x16 image from the
    # sheet immediately (the device path, so host == device for larger sprites).
    api["spr"](0, 12, 14, w=2, h=2)
    assert calls[-1] == (16, 16, 12, 14, 1, 0)
    # Flip (#11): spr(n, x, y, scale=1, flip=3) on a 1x1 tile forwards flip to spr_tile.
    api["spr"](3, 5, 6, -1, 1, 3)
    assert tiles[-1] == (3, 5, 6, -1, 1, 3)


def test_device_paint_editor_sizes_and_spans(tmp_path=None):
    # The shared PaintEditor's larger-sprite support (#30) under the device modules:
    # cycle_size steps 1->2->3, the 2x2 region writes the four constituent tiles,
    # and tile_span_image builds the contiguous block.
    _load_moy_runtime()               # registers device_api, the make_api home
    cols = 16
    sh = _editor_class('SpriteSheet')(cols, 16, spec=False)     # half-height fixture, not a cart sheet
    pe = _editor_class('PaintEditor')(sh)
    assert pe.size == 1 and pe.dim == 8
    pe.cycle_size(); assert pe.size == 2 and pe.dim == 16
    pe.color = 7
    pe.paint(10, 11)                        # bottom-right tile of the 2x2 block
    assert sh.tget(cols + 1, 2, 3) == 7
    img = sh.tile_span_image(0, 2, 2)
    assert (img.w, img.h) == (16, 16) and img.pix[11 * 16 + 10] == 7


def test_device_make_api_map_mget_mset(tmp_path=None):
    # The device cart API exposes map()/mget()/mset() bound to the injected TileMap
    # (#32): mget/mset round-trip through it, and map() forwards to canvas.map with
    # the cart's tilemap + sheet. Exercised under CPython via the frozen modules.
    _load_moy_runtime()               # registers device_api, the make_api home
    from editors import TileMap
    sheet = _editor_class('SpriteSheet')(4, 4, spec=False)      # small fixture, not a cart sheet
    tm = TileMap(3, 3)
    mapped = []

    class StubCanvas:
        w = 320
        h = 240

        def map(self, tilemap, sheet, *args):
            mapped.append((tilemap, sheet, args))

        def __getattr__(self, name):
            return lambda *a, **k: 0

    api = _device_make_api()(StubCanvas(), StubInput(), {}, sheet, None, tm)
    api["mset"](1, 2, 5)
    assert api["mget"](1, 2) == 5 and tm.mget(1, 2) == 5
    api["map"](0, 0, 3, 3, 0, 0, -1, 2)
    assert mapped and mapped[-1][0] is tm and mapped[-1][1] is sheet
    # SPEC.md 7.2's layer mask and the flag table ride along on every call; with
    # no project the table is a private zero one, so a mask draws nothing.
    assert mapped[-1][2][:8] == (0, 0, 3, 3, 0, 0, -1, 2)
    assert mapped[-1][2][8] == 0 and len(mapped[-1][2][9]) == 512
    api["map"](0, 0, 3, 3, 0, 0, -1, 2, 6)
    assert mapped[-1][2][8] == 6
    # With no tilemap injected, the API stays callable (map() no-ops, mget -> -1).
    api2 = _device_make_api()(StubCanvas(), StubInput(), {}, sheet)
    assert api2["mget"](0, 0) == -1
    api2["map"](0, 0)                       # no crash, draws nothing


def test_sprite_sheet_pset_bumps_gen():
    # pset bumps a generation counter so a running cart's tile cache can detect a
    # sprite edit and rebuild (host/device parity for live sprite edits).
    SpriteSheet = _editor_class('SpriteSheet')
    sh = SpriteSheet(4, 4, spec=False)           # small fixture, not a cart sheet
    assert sh.gen == 0
    sh.pset(0, 0, 5)
    assert sh.gen == 1
    sh.pset(1, 0, 6)
    assert sh.gen == 2
    # An out-of-bounds pset is a no-op and must not bump gen.
    sh.pset(-1, 0, 7)
    sh.pset(sh.w, 0, 7)
    assert sh.gen == 2
    # tset routes through pset, so it bumps too.
    sh.tset(0, 2, 2, 9)
    assert sh.gen == 3


def test_device_tile_cache_invalidated_on_sprite_edit():
    # The device tile cache (and each Image's RGB565 blit cache) snapshots a tile's
    # pixels. After a kid edits a sprite, the running cart must re-blit fresh art,
    # not the stale cached Image. make_api watches the sheet's gen counter and
    # clears the cache when it changes. Checked here on the MULTI-TILE span path (which
    # still resolves through make_api's tile_cache); plain 1x1 sprites auto-batch (#63)
    # straight from the sheet, so a paint edit needs no cache invalidation at all.
    _load_moy_runtime()               # registers device_api, the make_api home
    sheet = _editor_class('SpriteSheet')(4, 4, spec=False)      # small fixture, not a cart sheet
    sheet.tset(0, 0, 0, 3)
    blitted = []

    class StubCanvas:
        w = 320
        h = 240

        def spr(self, img, x, y, scale=1, flip=0):
            blitted.append(img)

        def __getattr__(self, name):
            return lambda *a, **k: 0

    api = _device_make_api()(StubCanvas(), StubInput(), {}, sheet)
    api["spr"](0, 0, 0, w=2, h=2)
    first = blitted[-1]
    # Same sheet, same id, no edit -> the cached span Image is reused (object identity).
    api["spr"](0, 0, 0, w=2, h=2)
    assert blitted[-1] is first

    # A paint edit bumps sheet.gen -> the cache is invalidated and a fresh Image
    # (rebuilt from the new pixels) is blitted next frame.
    sheet.pset(0, 0, 5)
    api["spr"](0, 0, 0, w=2, h=2)
    assert blitted[-1] is not first
