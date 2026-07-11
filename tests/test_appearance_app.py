"""Visual Appearance app: image/cart wallpaper sources and panel themes."""

import json
from pathlib import Path

from runtime import chrome, host_app, moy_carts


ROOT = Path(__file__).resolve().parent.parent


def _open_appearance(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Appearance":
            ws.launcher.sel = i
            break
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_kind() == "appearance"
    return ws.appearance_app


def test_appearance_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "theme_picker.moy"
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["version"] >= 1
    assert man["type"] == "app"
    assert "appearance" in man["permissions"]
    compile((folder / "main.py").read_text(encoding="utf-8"),
            str(folder / "main.py"), "exec")


def test_picker_separates_image_and_cart_wallpapers(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_appearance(ws)
    assert [c["title"] for c in app._image_items()] == ["My Art"]
    cart_titles = [c["title"] for c in app._cart_items()]
    assert "My Art" not in cart_titles
    assert {"Moy Night", "Sakura", "Ocean Desktop"}.issubset(set(cart_titles))


def test_wallpaper_and_theme_choices_apply_and_persist(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_appearance(ws)

    app._set_mode("carts")
    ocean_i = [c["title"] for c in app._cart_items()].index("Ocean Desktop")
    app._apply(ocean_i)
    assert ws.wallpaper_id == "ocean"
    assert moy_carts.load_system(carts)["wallpaper"] == "ocean"
    assert ws._animating(1 / 30) is True  # actual live cart preview keeps moving

    app._set_mode("themes")
    berry_i = [item[0] for item in app._items()].index("berry")
    app._apply(berry_i)
    assert ws.theme_name == "berry"
    assert ws.theme_colors["title"] == 14
    assert moy_carts.load_system(carts)["theme"] == "berry"


def test_my_art_image_thumbnail_and_live_preview_draw(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts, sys_size=(1024, 600), font_scale=2,
                                    windowed=True)
    raw = bytearray(512 * 300)
    for y in range(300):
        raw[y * 512:(y + 1) * 512] = bytes((22 if y < 150 else 3,)) * 512
    assert ws.artwork.save(raw, 512, 300)
    assert ws.artwork.set_wallpaper()

    app = _open_appearance(ws)
    assert app.mode == "images"
    thumb = ws.artwork.thumbnail(120, 72)
    assert (thumb.w, thumb.h) == (120, 72)
    assert set(thumb.pix) == {3, 22}
    before = bytes(ws.sys_canvas.buf)
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert bytes(ws.sys_canvas.buf) == before  # static image preview is redraw-free when idle


def test_desktop_appearance_window_rebuilds_responsive_context(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"),
                                    sys_size=(1024, 600), font_scale=2,
                                    windowed=True)
    _open_appearance(ws)
    win = ws.wm._wins["appearance"]
    assert win.ctx.appearance_layout.w == win.buf.w
    old_w = win.ctx.appearance_layout.w
    ws.wm._resize_window(win, 720, 430)
    assert win.ctx.appearance_layout.w == win.buf.w
    assert win.ctx.appearance_layout.w != old_w
    assert win.w >= 620
    assert win.h >= 460


def test_small_theme_grid_stays_inside_catalog(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_appearance(ws)
    app._set_mode("themes")
    cards = app.layout.cards(len(app._items()))
    assert len(cards) == len(chrome.THEMES)     # one card per shipped theme
    bottom = app.layout.catalog[1] + app.layout.catalog[3]
    assert max(y + h for _x, y, _w, h in cards) <= bottom
