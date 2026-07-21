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
    else:
        # Windowed tier (#105): system apps are desk-only, not on the shelf.
        assert ws.open_app(ws.appearance_app)
        ws.input.begin_frame()
        ws.frame(1 / 30)
        assert ws.wm.top_kind() == "appearance"
        return ws.appearance_app
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
    items = app._image_items()
    # My Art first, then the built-in solid fills (kept selectable here now that
    # the Settings WALLPAPER stepper is gone -- the app is the ONE surface).
    assert items[0]["title"] == "My Art"
    assert items[1:] == list(ws._FILL_WALLPAPERS)
    cart_titles = [c["title"] for c in app._cart_items()]
    assert "My Art" not in cart_titles
    assert {"Moy Night", "Sakura", "Ocean Desktop"}.issubset(set(cart_titles))


def test_solid_fill_selectable_from_images_tab(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_appearance(ws)
    app._set_mode("images")
    items = app._items()
    fill_i = items.index("fill:black")
    app._apply(fill_i)
    assert ws.wallpaper_id == "fill:black"
    assert moy_carts.load_system(carts)["wallpaper"] == "fill:black"
    assert app.status == "BLACK"
    # Reopening lands back on the IMAGES tab with the fill selected.
    app.open()
    assert app.mode == "images"
    assert app._items()[app.sel] == "fill:black"


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


def test_wide_monitor_shows_full_wallpaper_letterboxed(tmp_path):
    """The Display-Properties nod: on big surfaces the preview is a monitor
    whose 4:3 screen shows the WHOLE cart frame (fit, black bars) -- never the
    desktop's cover-crop."""
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600))
    app = _open_appearance(ws)
    lay = app.layout
    assert lay.wide and lay.screen is not None
    sx, sy, sw, sh = lay.screen
    assert sw * 3 == sh * 4                      # the game canvas aspect
    ws.select_wallpaper("moy_night", persist=False)
    app._set_mode("carts")
    ws.wallpaper.draw_preview(ws.sys_canvas, (10, 20, 500, 380), 1 / 30)
    buf, w = ws.sys_canvas.buf, ws.sys_canvas.w
    # 320x240 fits 500x380 best at 3/2 -> 480x360 centered: 10px bars all around.
    assert buf[22 * w + 12] == 0 and buf[200 * w + 12] == 0        # left/top bars
    assert buf[30 * w + 20] == ws.canvas.buf[0]                    # frame TL mapped
    assert any(buf[200 * w + xx] != 0 for xx in range(30, 480))    # frame content


def test_fill_and_image_previews_fill_or_fit_the_screen(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600))
    ws.select_wallpaper("fill:indigo", persist=False)
    ws.wallpaper.draw_preview(ws.sys_canvas, (0, 0, 100, 80), 0)
    buf, w = ws.sys_canvas.buf, ws.sys_canvas.w
    from runtime import console as C
    indigo = C.NAMES["indigo"]
    assert buf[0] == indigo and buf[79 * w + 99] == indigo   # a fill floods the screen


def test_theme_preview_draws_mock_windows(tmp_path):
    """The Appearance-tab nod: the THEMES preview draws how windows will look --
    an active title strip in the theme's title token over the desk field."""
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600))
    app = _open_appearance(ws)
    app._set_mode("themes")
    ws.mark_dirty()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    th = ws.theme_colors
    x, y, w, h = app.layout.field
    buf, cw = ws.sys_canvas.buf, ws.sys_canvas.w
    field_px = set()
    for yy in range(y, y + h, 7):
        field_px.update(buf[yy * cw + x: yy * cw + x + w])
    assert th["title"] in field_px               # the active window's strip
    assert th["accent"] in field_px              # the OK button
    assert th["panel"] in field_px               # the window bodies


def test_small_tier_monitor_shows_whole_my_art_image(tmp_path):
    """The phone/320x240 tier: My Art is cover-cropped as the backdrop, so the
    monitor's screen is where the WHOLE drawing is visible (aspect-fit with
    bars) -- the 'I can't see the whole image' fix."""
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    raw = bytearray(512 * 300)
    for y in range(300):                       # top half color 22, bottom 3
        raw[y * 512:(y + 1) * 512] = bytes((22 if y < 150 else 3,)) * 512
    assert ws.artwork.save(raw, 512, 300)
    assert ws.artwork.set_wallpaper()
    app = _open_appearance(ws)
    assert app.mode == "images"
    ws.ach.toast = None                        # the unlock toast would overlap
    ws.ach.toast_until = 0                     # the monitor's top rows
    ws.mark_dirty()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    sx, sy, sw, sh = app.layout.screen
    buf, cw = ws.sys_canvas.buf, ws.sys_canvas.w
    # 512x300 aspect-fit in the 4:3 screen -> full width, letterbox bars: the
    # top band (22) AND the bottom band (3) are both visible, bars are black.
    mid_x = sx + sw // 2
    col = [buf[yy * cw + mid_x] for yy in range(sy, sy + sh)]
    assert 22 in col and 3 in col              # the WHOLE image, both halves
    assert col[0] == 0 and col[-1] == 0        # letterbox bars


def test_small_tier_stays_narrow_with_compact_theme_mock(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_appearance(ws)
    assert not app.layout.wide and app.layout.screen is not None
    app._set_mode("themes")
    ws.mark_dirty()
    ws.input.begin_frame()
    ws.frame(1 / 30)                             # compact mock draws without error
    th = ws.theme_colors
    x, y, w, h = app.layout.field
    buf, cw = ws.sys_canvas.buf, ws.sys_canvas.w
    row = bytes(buf[(y + 2) * cw + x:(y + 2) * cw + x + w])
    assert th["edge"] in row or th.get("desktop", th["panel"]) in row


def test_preview_records_on_a_bufferless_canvas(tmp_path):
    """The web fullscreen tier draws through a record-only canvas (no .buf).
    The monitor screen must land there as ONE self-contained img/spr command --
    the blank-screen-on-the-phone regression: the old path needed a readable
    framebuffer, which CommandCanvas doesn't have."""
    from runtime import web_view
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.select_wallpaper("moy_night", persist=False)
    rec = web_view.CommandCanvas(320, 240)
    ws.wallpaper.draw_preview(rec, (10, 10, 152, 114), 1 / 30)
    cmds = rec.take_commands()
    imgs = [c for c in cmds if c and c[0] in ("img", "imgref", "spr")]
    assert imgs, "the cart frame must record as an image command"


def test_preview_runner_leaves_the_game_canvas_alone(tmp_path):
    """The preview compiles the cart onto an OFFSCREEN canvas -- a running
    game's frame on ws.canvas must survive a monitor redraw untouched."""
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600))
    ws.select_wallpaper("moy_night", persist=False)
    ws.canvas.cls(9)                              # stand-in for a game's frame
    before = bytes(ws.canvas.buf)
    ws.wallpaper.draw_preview(ws.sys_canvas, (0, 0, 480, 360), 1 / 30)
    assert bytes(ws.canvas.buf) == before


def test_small_theme_grid_stays_inside_catalog(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_appearance(ws)
    app._set_mode("themes")
    cards = app.layout.cards(len(app._items()))
    assert len(cards) == len(chrome.THEMES)     # one card per shipped theme
    bottom = app.layout.catalog[1] + app.layout.catalog[3]
    assert max(y + h for _x, y, _w, h in cards) <= bottom
