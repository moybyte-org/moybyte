"""The Paint system app: drawing, shared persistence and reuse workflows."""

import json
from pathlib import Path

from runtime import host_app, moy_carts


ROOT = Path(__file__).resolve().parent.parent


def _open_paint(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Paint":
            ws.launcher.sel = i
            break
    else:
        # Windowed tier (#105): system apps are desk-only, not on the shelf.
        assert ws.open_app(ws.artwork_app)
        ws.input.begin_frame()
        ws.frame(1 / 30)
        assert ws.cart_error is None
        assert ws.wm.top_kind() == "artwork"
        return ws.artwork_app
    ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.wm.top_kind() == "artwork"
    return ws.artwork_app


def test_paint_and_my_art_are_well_formed_system_carts():
    paint = ROOT / "system_carts" / "paint.moy"
    wall = ROOT / "system_carts" / "my_art.moy"
    for folder in (paint, wall):
        man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert man["version"] >= 1
        compile((folder / "main.py").read_text(encoding="utf-8"),
                str(folder / "main.py"), "exec")
    assert json.loads((paint / "manifest.json").read_text())["type"] == "app"
    assert json.loads((wall / "manifest.json").read_text())["type"] == "wallpaper"


def test_rle_moyimg_roundtrip_handles_long_runs_and_all_palette_indices():
    raw = bytearray()
    raw.extend(bytes((7,)) * 700)
    raw.extend(bytes(range(64)))
    raw.extend(bytes((0,)) * 36)
    blob = moy_carts.encode_moyimg(40, 20, raw)
    meta = json.loads(blob)
    assert meta["format"] == "moyimg-v1"
    assert meta["codec"] == "rle"
    assert moy_carts.decode_moyimg(blob) == (40, 20, bytes(raw))
    assert host_app._decode_moyimg(blob) == (40, 20, bytes(raw))


def test_touch_stroke_undo_redo_and_shared_save(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_paint(ws)
    driver = host_app.ConsoleDriver(ws)

    before = bytes(app.doc.pix)
    # FIT canvas maps each screen pixel to a 2x2 document block. Draw a short
    # continuous stroke through the same pointer path as host mouse/device touch.
    _img, dx, dy, scale, factor = app.display
    x0, y0 = dx + 10 * scale, dy + 8 * scale
    driver.touch(x0, y0)
    driver.frame(1 / 30)
    driver.touch_drag(x0 + 16 * scale, y0)
    driver.frame(1 / 30)
    driver.touch_up()
    driver.frame(1 / 30)
    changed = bytes(app.doc.pix)
    assert changed != before
    assert changed[(8 * factor) * app.doc.W + 10 * factor] == app.color

    assert app.doc.undo()
    assert bytes(app.doc.pix) == before
    assert app.doc.redo()
    assert bytes(app.doc.pix) == changed

    assert app._save() is True
    # #108: the drawing persists as a NAMED user file, auto-named on first
    # save; saving alone never touches the wallpaper copy (copy-on-set).
    name = ws.artwork.doc_name()
    assert name
    blob = moy_carts.load_file("drawings", name, carts)
    assert moy_carts.decode_moyimg(blob) == (app.doc.W, app.doc.H, changed)
    wall = next(c for c in moy_carts.scan(carts) if c["title"] == "My Art")
    assert "bg" not in wall["images"]
    assert ws.artwork.set_wallpaper()
    wall = next(c for c in moy_carts.scan(carts) if c["title"] == "My Art")
    # The wallpaper copy carries the SAME pixels (copy-on-set) plus a #108
    # phase-2 provenance stamp (src/sig) so a later edit can offer UPDATE.
    assert moy_carts.decode_moyimg(wall["images"]["bg"]) == \
        moy_carts.decode_moyimg(blob)
    assert moy_carts.decode_moyimg(moy_carts.load_artwork(carts)) == \
        moy_carts.decode_moyimg(blob)
    assert moy_carts.read_provenance(moy_carts.load_artwork(carts))[0] == \
        "drawings/" + name


def test_publish_wallpaper_and_attach_as_game_bg(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_paint(ws)
    app.doc.put(10, 10, 14)
    assert app._save()

    assert ws.artwork.set_wallpaper()
    assert ws.wallpaper_id == "my_art"
    assert moy_carts.load_system(carts)["wallpaper"] == "my_art"

    titles = ws.artwork.targets()
    target_i = titles.index("Star Catcher")
    assert ws.artwork.attach(target_i) == "Star Catcher"
    target = next(c for c in moy_carts.scan(carts) if c["title"] == "Star Catcher")
    assert "bg" in target["images"]
    bg = moy_carts.decode_moyimg(target["images"]["bg"])
    assert bg[:2] == (320, 240)  # a desktop original derives a game-sized copy
    assert len(bg[2]) == 320 * 240


def test_artwork_capability_is_not_in_the_regular_cart_api(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    base = host_app.make_api(ws.canvas, ws.input, {})
    assert "artwork" not in base

    paint_app = _open_paint(ws)
    assert paint_app.ws.artwork is ws.artwork

    other = next(c for c in ws._all_carts if c["title"] == "Star Catcher")
    other.setdefault("permissions", []).append("artwork")
    ws._open_workspace(other)
    ws.run(ws.project, ws.launcher_layer)
    assert "artwork" not in ws.ns


def test_desktop_paint_reflows_and_wallpaper_maps_512x300_exactly_2x(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"),
                                    sys_size=(1024, 600), font_scale=2,
                                    windowed=True)
    app = _open_paint(ws)
    win = ws.wm._wins["artwork"]
    lay = win.ctx.artwork_layout
    assert (app.doc.W, app.doc.H) == (512, 300)
    assert not lay.compact
    assert lay.view[2] >= 512 and lay.view[3] >= 300

    # Resize release rebuilds the buffer + its Paint layout context; document bytes
    # and resolution are not resampled by presentation changes.
    before = bytes(app.doc.pix)
    ws.wm._resize_window(win, 720, 430)
    assert win.ctx.artwork_layout.w == win.buf.w
    assert bytes(app.doc.pix) == before
    assert win.w >= 620
    assert win.h >= 460

    assert app._save()
    assert ws.artwork.set_wallpaper()
    ws.wallpaper.draw(0)
    src = app.doc.pix
    out = ws.sys_canvas.buf
    # 512x300 -> exact nearest-neighbor 2x on the 1024x600 host system canvas.
    for x, y in ((0, 0), (10, 10), (255, 149), (511, 299)):
        c = src[y * 512 + x]
        dx, dy = x * 2, y * 2
        assert out[dy * 1024 + dx] == c
        assert out[dy * 1024 + dx + 1] == c
        assert out[(dy + 1) * 1024 + dx] == c
