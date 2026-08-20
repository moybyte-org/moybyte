"""Cart-declared canvas (SPEC.md 1/3.1): a manifest `"canvas": "128x128"` runs
the cart on a genuinely smaller raster -- W/H report it, every verb clips to it
-- and the console integer-scales it up at composite, exactly like a
cart-declared view. The set is CLOSED (320x240 / 160x120 / 128x128); an
out-of-set size is refused by name like an unknown runtime, never run at
dimensions the cart did not ask for.

Driven through the shared console (runtime.host_app), so this asserts the same
plumbing the boards freeze: the store normalizer, the per-run bind/release on
the Workstation (including the shared-tier promote, which is the T-Deck shape),
the wm viewport/composite, and the touch mapping through view()."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import host_app, moy_carts  # noqa: E402
from runtime.moy_carts import _canvas_str, _normalize_canvas  # noqa: E402

DT = 1.0 / 30

# Marker colors from the top of the MOY64 table (60..63): indices the system
# chrome never draws, so the asserts below can't collide with the run-start
# toast / cursor overlays that legitimately paint over a running game.
PLAIN_SRC = """
def _update(dt):
    pass

def _draw():
    cls(3)
    pix(0, 0, 60)
    pix(W - 1, H - 1, 61)
"""

VIEW_SRC = """
def _init():
    view(128, 120)

def _update(dt):
    pass

def _draw():
    cls(1)
    pix(0, 0, 63)    # row 0 is OUTSIDE the centered 128x120 view -> never shown
    pix(0, 4, 62)    # the view's top-left corner
"""

CRASH_SRC = """
def _update(dt):
    pass

def _draw():
    cls(0)
    boom()
"""


def _write_cart(carts_dir, name, src, canvas="128x128", extra=None):
    d = Path(carts_dir) / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    man = {"title": name, "type": "game", "canvas": canvas}
    if extra:
        man.update(extra)
    (d / "manifest.json").write_text(json.dumps(man))
    (d / "main.py").write_text(src)
    (d / "config.json").write_text("{}")


def _open(tmp_path, name, src, canvas="128x128"):
    carts_dir = str(tmp_path / "carts")
    _write_cart(carts_dir, name, src, canvas)
    ws = host_app.build_workstation(carts_dir)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("title") == name)
    ws.open()
    return ws, drv


# -- the store normalizer ----------------------------------------------------

def test_normalize_canvas_closed_set():
    assert _normalize_canvas(None) is None
    assert _normalize_canvas("320x240") == (320, 240)
    assert _normalize_canvas("160x120") == (160, 120)
    assert _normalize_canvas("128x128") == (128, 128)
    # Out-of-set (including well-formed sizes) is carried RAW as the refusal
    # evidence -- SPEC.md 3.1 refuses it like an unknown runtime.
    assert _normalize_canvas("256x192") == "256x192"
    assert _normalize_canvas("128x120") == "128x120"
    assert _normalize_canvas({"w": 128}) == {"w": 128}
    # The LEGACY moybyte dict form (carts already seeded on boards carry it --
    # a stale celeste on the P4 was refused without this) normalizes when its
    # size is in the set, and stays refusal evidence when it is not.
    assert _normalize_canvas({"palette": "moy64", "height": 240,
                              "width": 320}) == (320, 240)
    assert _normalize_canvas({"width": 128, "height": 128}) == (128, 128)
    assert _normalize_canvas({"width": 999, "height": 2}) == \
        {"width": 999, "height": 2}


def test_canvas_str_round_trip():
    assert _canvas_str((128, 128)) == "128x128"
    assert _canvas_str([160, 120]) == "160x120"
    assert _canvas_str("256x192") == "256x192"   # lossless out-of-set copy


def test_create_and_duplicate_carry_canvas(tmp_path):
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    cart = moy_carts.create("tiny", root, src="def _draw():\n    pass\n",
                            type="game", canvas=(128, 128))
    assert cart["canvas"] == (128, 128)
    man = json.loads((Path(cart["path"]) / "manifest.json").read_text())
    assert man["canvas"] == "128x128"
    dup = moy_carts.duplicate(cart, root)
    assert dup["canvas"] == (128, 128)


# -- the per-run bind (the shared-tier promote is the T-Deck shape) ----------

def test_small_canvas_run_binds_and_composites(tmp_path):
    ws, drv = _open(tmp_path, "tiny", PLAIN_SRC)
    assert ws.cart_error is None
    # The run plays on a REAL 128x128 canvas; the boot canvas was promoted to
    # system canvas so the composite runs (sc is no longer gc).
    assert (ws.canvas.w, ws.canvas.h) == (128, 128)
    stock = ws.sys_canvas
    assert (stock.w, stock.h) == (320, 240)
    assert stock is not ws.canvas
    # No view: 128x128 on 320x240 composites at 1x, centered (96, 56).
    assert ws.wm.viewport() == (96, 56, 1)
    ws.ach.toast = None                  # the first-play achievement toast is
    for _ in range(3):                   # wall-clock timed -- clear, don't wait
        drv.frame(DT)
    # pix() reads a palette INDEX back on every tier, so the cart's own colour
    # numbers are what the assertion names.
    assert stock.pix(96, 56) == 60                    # cart (0, 0)
    assert stock.pix(96 + 127, 56 + 127) == 61        # cart (127, 127)
    # Run death restores the boot raster and the shared-canvas degradation.
    ws.player.release_world()
    assert ws.canvas is stock
    assert ws._sys_canvas is None


def test_view_scales_and_maps_touch(tmp_path):
    ws, drv = _open(tmp_path, "zoomed", VIEW_SRC)
    assert ws.cart_error is None
    for _ in range(120):                 # outlive the run-start toast overlay
        drv.frame(DT)
    # view(128, 120) on a 128x128 canvas: centered source rows 4..123, and the
    # 320x240 glass fits it at 2x -> 256x240, flush to the top, 32px pillars.
    assert ws.wm.viewport() == (32, 0, 2)
    # Touch mapping is the inverse: the viewport's top-left is cart (0, 4).
    assert ws.wm.game_xy(32, 0) == (0, 4)
    assert ws.wm.game_xy(32 + 255, 239) == (127, 123)
    sc = ws.sys_canvas
    assert sc.pix(32, 0) == 62                        # cart (0, 4) -> dest (32, 0)
    assert sc.pix(31, 0) == 0                         # pillar stays bezel
    # cart row 0 lies outside the view: its marker must never reach the glass.
    import canvas_probe as probe
    assert probe.word_of(63, sc) not in set(probe.pixels(sc))


def test_out_of_set_canvas_is_refused_by_name(tmp_path):
    ws, drv = _open(tmp_path, "wrongsize", PLAIN_SRC, canvas="256x192")
    assert ws.cart_error is not None
    assert "256x192" in ws.cart_error
    # Refused BEFORE any bind: the boot raster never changed hands.
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)
    assert ws._sys_canvas is None
    drv.frame(DT)                                     # the error panel draws


def test_crash_on_small_canvas_survives(tmp_path):
    ws, drv = _open(tmp_path, "crashy", CRASH_SRC)
    for _ in range(3):
        drv.frame(DT)                # the crash panel must fit the 128px raster
    assert ws.cart_error is not None
    ws.player.release_world()
    assert (ws.canvas.w, ws.canvas.h) == (320, 240)


def test_rerun_reuses_the_cached_canvas(tmp_path):
    ws, drv = _open(tmp_path, "tiny", PLAIN_SRC)
    small = ws.canvas
    ws.player.release_world()
    ws.open()
    assert ws.canvas is small        # one 128x128 buffer per session, not per run
