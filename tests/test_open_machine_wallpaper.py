from pathlib import Path

from runtime import host_canvas
from runtime.host_app import build_workstation
from tools.gen_device_carts import build_carts


ROOT = Path(__file__).resolve().parents[1]


def test_open_machine_wallpaper_is_static_moy64_cart(tmp_path):
    ws = build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                           font_scale=2, windowed=True)

    assert "open_machine" in ws.wallpaper_options()
    ws.select_wallpaper("open_machine", persist=False)
    assert ws.wallpaper._wp_draw is not None
    assert ws.wallpaper._wp_update is None
    assert not ws.wallpaper.is_animating(1 / 60)

    ws.wallpaper.draw(0)
    first = bytes(ws.canvas._buf)
    # "MOY64 cart": every pixel is a palette entry, and these four are in it.
    # to_indices is EXACT and strict -- it raises on a word no index produces,
    # which is the "used <= set(range(64))" half of the old assertion.
    used = set(host_canvas.indices_of(ws.canvas))
    assert {0, 1, 10, 13}.issubset(used)

    # No RNG or time state: repeated draws produce the same quiet construction field.
    ws.wallpaper.draw(1 / 60)
    assert bytes(ws.canvas._buf) == first


def test_open_machine_wallpaper_is_in_device_seed_order():
    carts = build_carts(str(ROOT / "system_carts"))
    cart = next(c for c in carts if c["title"] == "Open Machine")
    assert cart["type"] == "wallpaper"
    assert cart["version"] >= 1                # bumped per #47 on content changes
    assert cart["cfg"]["field"] == "black"
    assert "def _update" not in cart["src"]
