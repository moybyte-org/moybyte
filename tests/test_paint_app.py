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


# --------------------------------------------------------------------------- #
# #186 -- the DESKTOP BACKDROP's whole-screen bake.                            #
#                                                                             #
# The third and last way a full screen of RGB565 is asked of the gc heap. The #
# first two were a cart's painted backdrop (4069334) and Paint's own document #
# (0152e2f); this one is a child's drawing published as their wallpaper, and  #
# it is the one that fails most quietly -- the desktop swallows the raise and #
# falls through to the My Art cartridge's placeholder, so the drawing simply  #
# is not there.                                                               #
# --------------------------------------------------------------------------- #

class _Loans:
    """moybuf with the C registry's single-owner rule enforced (see
    tests/test_moybuf.py): alloc hands out REAL memoryviews so the residency
    checks fire, and free refuses a foreign or already-freed buffer."""

    def __init__(self):
        self.live = {}
        self.freed = 0

    def alloc(self, n):
        v = memoryview(bytearray(n))
        self.live[id(v)] = n
        return v

    def take(self, payload):
        b = self.alloc(len(payload))
        b[:] = payload
        return b

    def free(self, buf):
        if not isinstance(buf, memoryview):
            return
        if id(buf) not in self.live:
            raise AssertionError("freed a foreign or already-freed buffer")
        del self.live[id(buf)]
        self.freed += 1

    def stats(self):
        return (len(self.live), sum(self.live.values()))


def _lending(monkeypatch, art):
    """Point the artwork service and the device canvas at one tracked
    allocator -- the boards' moy_alloc, with its rules kept.

    Both modules are resolved through the objects under test rather than by the
    `runtime.`/`device.` spelling: a board stages these as top-level names, so
    the module a test patches and the one the code runs are two different
    objects unless the live one is asked for. Returns the canvas module so the
    register a test reads is the register the bake wrote."""
    import sys as _sys

    from runtime.host_canvas import install

    install()
    dc = _sys.modules["device_canvas"]
    aw = _sys.modules[type(art).__module__]
    loans = _Loans()
    monkeypatch.setattr(dc, "_moybuf", loans)
    monkeypatch.setattr(aw, "_moybuf", loans)
    monkeypatch.setattr(dc, "_LENT_BAKES", {})
    return loans, dc


def _device_canvas(ws, w, h):
    """The offscreen canvas BOTH BOARDS build (see test_wallpaper_preview): the
    real DeviceCanvas over the real native kernel, so the bake path under test
    is the one the glass runs."""
    from runtime.host_canvas import install

    install()
    from device_canvas import DeviceCanvas, _LayerComp

    gfx = ws.canvas._gfx
    assert gfx is not None, "no native kernel: this would not test the bake"
    return DeviceCanvas(_LayerComp(int(w), int(h), gfx))


def _published(tmp_path, dw=512, dh=300):
    """A workstation whose desktop backdrop is a saved drawing (the WALL verb)."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    art = ws.artwork
    pix = bytearray(dw * dh)
    for y in range(dh):
        o = y * dw
        for x in range(dw):
            pix[o + x] = (x // 7 + y // 5) % 63
    assert art.save(pix, dw, dh), art.last_error
    assert art.set_wallpaper(), art.last_error
    assert art.owns_wallpaper(ws.look.wallpaper_id)
    return ws, art


def test_the_backdrop_is_one_screen_sized_bake_the_register_lends(tmp_path,
                                                                  monkeypatch):
    """#186: a published drawing is the console's biggest allocation drawn on
    the most ordinary screen there is.

    Measured on the two S3 boards at 0152e2f, both at an untouched launcher:
    the T-Deck's 320x240 desk asked the gc heap for 153,600 contiguous bytes
    against a largest run of 130,559 once combed, and the Guition's 480x320
    desk asked for 614,400 -- four times the screen it was filling -- against a
    largest run of 107,584, with nothing fragmented by hand. Both raised, and
    the desktop swallowed it.

    So the backdrop is ONE screen-sized bitmap drawn 1:1. 1:1 is the only
    placement `spr` bakes through the owner-lent path (any other scale bakes a
    pre-scaled scale^2 copy through _cache_rgb, which the register does not
    reach), and screen-sized is the smallest bake that can fill the screen."""
    ws, art = _published(tmp_path)
    loans, device_canvas = _lending(monkeypatch, art)
    from runtime.artwork import ArtworkService, PaintDocument

    cv = _device_canvas(ws, 320, 240)
    placed = []
    real_spr = cv.spr

    def _spy(img, x, y, *a, **k):
        placed.append((x, y) + a)
        return real_spr(img, x, y, *a, **k)

    cv.spr = _spy

    assert art.draw_wallpaper(cv) is True
    assert placed == [(0, 0)], "the backdrop is placed 1:1 at the origin"

    m = art._wall_bitmap
    assert (m.w, m.h) == (320, 240), "the bitmap is the SCREEN, not the source"
    assert m._owner == ArtworkService.WALL_OWNER == "wallpaper_bg"
    # Its own key. release_bakes(owner) frees everything an owner holds, so a
    # shared one would make Paint's leaving hook drop the desktop's backdrop.
    assert m._owner != PaintDocument.OWNER
    assert m._owner not in ("wallpaper", "wallpaper_pv", "cart")

    # Both whole-screen buffers are loans: the resampled indices (320*240) and
    # the RGB565 bake (320*240*2). Neither is a run this heap can promise.
    assert isinstance(m.pix, memoryview) and len(m.pix) == 320 * 240
    assert isinstance(m._rgb_i, memoryview) and len(m._rgb_i) == 153600
    assert len(m._rgb_i) >= device_canvas._OFFHEAP_BAKE_BYTES
    assert loans.stats() == (2, 320 * 240 * 3)
    assert [len(b) for _i, b in device_canvas._LENT_BAKES["wallpaper_bg"]] == [153600]

    # Redrawing reuses both -- the desktop draws this every frame it paints.
    for _ in range(5):
        art.draw_wallpaper(cv)
    assert loans.stats() == (2, 320 * 240 * 3)
    assert art._wall_bitmap is m


def test_the_backdrops_loan_comes_back_on_every_wallpaper_change(tmp_path,
                                                                 monkeypatch):
    """#186: off-heap memory has no collector, so the loan needs a seam.

    The backdrop is neither a cart RUN nor a document -- it has neither of the
    deaths the other two loans hang off. What it has is a SELECTION, and the
    wallpaper component's clear() is the one seam every wallpaper change
    funnels through. The BAKE goes back there and the resampled indices do not:
    the resample is a Python loop over a whole screen, so paying it per
    selection would be a visible hitch, while the re-bake is one native call.

    The ledger is the assertion. Repeated switches must come back to the same
    numbers -- a loan taken per change and never given back is the leak this
    mechanism is one wrong line away from being."""
    ws, art = _published(tmp_path)
    loans, device_canvas = _lending(monkeypatch, art)

    cv = _device_canvas(ws, 320, 240)
    art.draw_wallpaper(cv)
    on_my_art = loans.stats()
    indices = art._wall_bitmap.pix
    assert on_my_art == (2, 320 * 240 * 3)

    for _ in range(6):
        ws.look.select_wallpaper("fill:black", persist=False)
        # The bake is back; the indices, and the bitmap, are still cached.
        assert loans.stats() == (1, 320 * 240), "the bake outlived its backdrop"
        assert device_canvas._LENT_BAKES.get("wallpaper_bg") in (None, [])
        assert art._wall_bitmap is not None and art._wall_bitmap.pix is indices
        assert art._wall_bitmap._rgb_i is None, "a stale draw must re-bake"

        ws.look.select_wallpaper("my_art", persist=False)
        art.draw_wallpaper(cv)
        assert loans.stats() == on_my_art, "the ledger drifted across a switch"
        assert art._wall_bitmap.pix is indices, "the resample was paid twice"
    assert loans.freed == 6, "one bake returned per change, and nothing else"


def test_publishing_a_new_drawing_returns_both_of_the_backdrops_loans(
        tmp_path, monkeypatch):
    """#186: WALL replaces the picture, so the cached screen is genuinely dead
    -- indices and bake both. set_wallpaper releases them itself rather than
    leaning on the select it ends with, because it can still answer False after
    clearing the cache and never reach one."""
    ws, art = _published(tmp_path)
    loans, _dc = _lending(monkeypatch, art)

    cv = _device_canvas(ws, 320, 240)
    art.draw_wallpaper(cv)
    assert loans.stats() == (2, 320 * 240 * 3)

    art.new_doc(512, 300)
    assert art.save(bytearray(512 * 300), 512, 300), art.last_error
    assert art.set_wallpaper(), art.last_error
    assert loans.stats() == (0, 0), "the replaced screen kept its buffers"
    assert art._wall_bitmap is None and art._wall_pix is None

    art.draw_wallpaper(cv)
    assert loans.stats() == (2, 320 * 240 * 3), "the new backdrop took no loan"


def test_an_exact_integer_cover_is_the_replication_it_replaced():
    """#186: the branch this collapsed existed for the P4 -- 512x300 doubled
    onto 1024x600 with no resample. Going through cover_indices instead must
    not move a pixel there, and it does not: at an exact multiple the formula
    crops nothing and samples x*sw//dw, which IS nearest-neighbour
    replication. Asserted rather than argued, because the P4 is the one tier
    this change could not be measured on."""
    from runtime.file_widgets import cover_indices

    for sw, sh, k in ((512, 300, 2), (320, 240, 3), (160, 120, 4)):
        src = bytearray((x * 5 + y * 3) % 63 for y in range(sh) for x in range(sw))
        dw, dh = sw * k, sh * k
        out = cover_indices(src, sw, sh, dw, dh)
        assert len(out) == dw * dh
        for y in range(0, dh, 7):
            row = y * dw
            srow = (y // k) * sw
            assert bytes(out[row:row + dw]) == bytes(
                src[srow + x // k] for x in range(dw)), "%dx moved a pixel" % k


def test_a_backdrop_that_already_fits_the_screen_takes_no_resample(tmp_path,
                                                                   monkeypatch):
    """A drawing the size of the desk is drawn from its own decoded indices --
    one loan (the bake), not two. The release path has to cope with the
    half-set state that leaves."""
    ws, art = _published(tmp_path, 320, 240)
    loans, _dc = _lending(monkeypatch, art)

    cv = _device_canvas(ws, 320, 240)
    art.draw_wallpaper(cv)
    assert art._wall_pix is None, "a screen-sized source needs no copy"
    assert loans.stats() == (1, 153600)

    art._drop_wall_bitmap()
    assert loans.stats() == (0, 0)
