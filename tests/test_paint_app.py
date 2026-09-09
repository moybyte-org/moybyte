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


def test_moyimg_roundtrip_handles_long_runs_and_all_palette_indices():
    raw = bytearray()
    raw.extend(bytes((7,)) * 700)
    raw.extend(bytes(range(64)))
    raw.extend(bytes((0,)) * 36)
    blob = moy_carts.encode_moyimg(40, 20, raw)
    meta = json.loads(blob)
    assert meta["format"] == "moyimg-v1"
    assert "codec" not in meta, "one format -- there is nothing to dispatch on"
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
    assert ws.look.wallpaper_id == "my_art"
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
    assert paint_app.ctx.artwork is ws.artwork      # the AppContext handle

    other = next(c for c in ws.carts.all if c["title"] == "Star Catcher")
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
    out = ws.sys_canvas
    # 512x300 -> exact nearest-neighbor 2x on the 1024x600 host system canvas.
    # pix() reads a palette INDEX back, which is what the document stores.
    for x, y in ((0, 0), (10, 10), (255, 149), (511, 299)):
        c = src[y * 512 + x]
        dx, dy = x * 2, y * 2
        assert out.pix(dx, dy) == c
        assert out.pix(dx + 1, dy) == c
        assert out.pix(dx, dy + 1) == c


def test_a_saved_drawing_reopens_as_the_same_pixels(tmp_path):
    """Paint's own round trip, through the store and back into the editor --
    the pair of verbs the one-format change moved. Anything that survives
    `encode_moyimg` but not the decoder on the way back reads as a blank canvas
    with a kid's drawing gone, which is not a failure anything else would catch.
    """
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_paint(ws)
    doc = app.doc
    for i in range(0, len(doc.pix), 7):
        doc.pix[i] = (i // 7) & 63
    painted = bytes(doc.pix)
    assert app._save() is True
    name = ws.artwork.doc_name()

    fresh = host_app.build_workstation(carts)
    fresh.artwork.open_named(name, "drawings")
    reopened = _open_paint(fresh)
    reopened.open()
    assert bytes(reopened.doc.pix) == painted
    assert (reopened.doc.W, reopened.doc.H) == (doc.W, doc.H)
    assert fresh.artwork.editable(), fresh.artwork.why_read_only()


def test_the_seed_background_opens_in_paint_and_is_editable(tmp_path):
    """Sakura's `images/bg.moyimg` is a 320x240 picture inside a cart, and it
    is the one every kid meets: the wallpaper they can take apart. It used to
    open READ-ONLY saying "CAN'T READ THIS PICTURE" -- not because of its size
    but because the store's decoder spoke a different codec than the tool that
    wrote it, which is the whole argument for there being one format."""
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    cart = next(c for c in ws.carts.all if c.get("title") == "Sakura")
    assert ws.open_image("images/bg.moyimg", cart=cart)

    art = ws.artwork
    got = art.load()
    assert got is not None, art.why_read_only()
    assert (got[0], got[1]) == (320, 240) and len(got[2]) == 320 * 240
    assert art.editable() and art.why_read_only() == ""
    assert (320, 240) <= (art.MAX_W, art.MAX_H)

    app = _open_paint(ws)
    app.open()
    assert (app.doc.W, app.doc.H) == (320, 240)
    assert bytes(app.doc.pix) == got[2]


def _bake_bytes(img):
    return img.w * img.h * 2


def test_paints_full_screen_bake_is_borrowed_and_given_back(tmp_path):
    """#186: Paint is the one app that rebuilds a WHOLE screen of RGB565 over
    and over -- every stroke invalidates the bake and the next frame rebuilds
    all 153,600 bytes of it -- and that is the allocation a fragmented gc heap
    refuses first (measured on both S3 boards: megabytes free, no run past
    ~147KB). So the document's bitmap names an owner and the device canvas
    lends it off-heap memory instead.

    Off-heap memory has no collector, so the loan needs a seam to come back at,
    and an app has no death to hang one off -- `_init_apps` builds every app
    once and they live as long as the console. The document's own seams are the
    answer: it is LEFT (close) or REPLACED (a new drawing)."""
    from device import device_canvas
    from runtime.artwork import PaintDocument

    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_paint(ws)

    doc = app.doc
    assert doc.image._owner == PaintDocument.OWNER == "artwork"
    assert doc.thumb_image._owner == PaintDocument.OWNER
    # The picture is over the full-surface bar and the half-scale thumb is
    # under it, so a stroke in FIT view -- the default -- costs no loan at all.
    assert _bake_bytes(doc.image) >= device_canvas._OFFHEAP_BAKE_BYTES
    assert _bake_bytes(doc.thumb_image) < device_canvas._OFFHEAP_BAKE_BYTES
    # A whole document plus its thumb is two loans at the desktop size, which is
    # why the per-owner cap is not one.
    big = PaintDocument(512, 300)
    assert _bake_bytes(big.thumb_image) >= device_canvas._OFFHEAP_BAKE_BYTES
    assert 2 <= device_canvas._MAX_LENT_BAKES

    # The seams. A gc-heap canvas has no release_bakes at all (the host's), so
    # the probe is a getattr -- record what a device canvas would be told.
    cv = app._surf.canvas()
    assert app._surf.canvas() is cv, "the app draws on one canvas"
    given_back = []
    cv.release_bakes = given_back.append

    app.close()                       # leaving, by any route (go_home sweeps it)
    assert given_back == ["artwork"]
    app.open()                        # ...and a re-open, whose load() may re-mint
    assert given_back == ["artwork"] * 2
    app._fresh_doc(app.doc.W, app.doc.H)          # NEW: this document is gone
    assert given_back == ["artwork"] * 3
    assert app.doc is not doc
