"""The Paint system app: drawing, shared persistence and reuse workflows."""

import json
from pathlib import Path

import pytest

from runtime import host_app, moy_carts
from ws_helpers import select_on_home  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


def _open_paint(ws):
    """Paint open. The opening's "First Steps" banner stays up, so every frame
    draws, but draws nothing: it would cover the page, whose stroke frames
    wait while it shows."""
    try:
        return _opened_paint(ws)
    finally:
        ws.ach.toast = None


def _opened_paint(ws):
    if select_on_home(ws, "Paint") is None:
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


def test_paint_my_art_and_the_wallpaper_are_well_formed_system_carts():
    paint = ROOT / "system_carts" / "moybyte.paint.moy"
    art = ROOT / "system_carts" / "moybyte.my_art.moy"
    wall = ROOT / "system_carts" / "moybyte.wallpaper.moy"
    for folder in (paint, art, wall):
        man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert man["version"] >= 1
        compile((folder / "main.py").read_text(encoding="utf-8"),
                str(folder / "main.py"), "exec")
    assert json.loads((paint / "manifest.json").read_text())["moybyte"]["type"] == "app"
    assert json.loads((art / "manifest.json").read_text())["moybyte"]["type"] == "app"
    man = json.loads((wall / "manifest.json").read_text())
    assert man["moybyte"]["type"] == "wallpaper" and man["title"] == "Wallpaper"


def test_the_paint_cart_body_is_only_the_fallback_card():
    """Paint the APP is `runtime/paint_app.py`; `moybyte.paint.moy/main.py` is the card an
    older shell shows instead, and nothing else.

    A registered app CLAIMS its cart (`console.open()` routes a claimed cart to
    `open_app`, and `_init_apps` is unconditional), so a cart body here is
    unreachable on every build that has the app -- which is every build. It
    carried a second 705-line drawing studio until 2026-09-15. The other four
    app-claimed carts ship the same 12-15 line card; this pins Paint to it.
    """
    src = (ROOT / "system_carts" / "moybyte.paint.moy" / "main.py").read_text(encoding="utf-8")
    assert "UPDATE MOYBYTE TO OPEN" in src
    assert "def _update" not in src and "def _init" not in src
    assert len(src.splitlines()) < 40, "the fallback card grew a second app"


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


def test_going_home_with_paint_never_opened_writes_nothing(tmp_path):
    """go_home's close sweep reaches Paint on every return home. With its
    document never built there is nothing to save: no blank drawing lands in
    the gallery, no first-save achievement, and the document stays unbuilt."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = ws.artwork_app
    ws.go_home()
    ws.go_home()
    assert app._doc is None
    assert not list(tmp_path.rglob("drawing_*"))
    assert not ws.ach.has("first_paint")       # the paint_save event's award


def test_touch_stroke_undo_redo_and_shared_save(tmp_path):
    carts = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts)
    app = _open_paint(ws)
    driver = host_app.ConsoleDriver(ws)

    before = bytes(app.doc.pix)
    # A short continuous stroke through the same pointer path as a host mouse
    # and a device touch, where the view shows the drawing at 1x.
    x, y, w, h, sx, sy, sw, sh, q = app._spec()
    assert q == 4, "Paint opens at 1x on a handheld"
    x0, y0 = x + 10, y + 8
    driver.touch(x0, y0)
    driver.frame(1 / 30)
    driver.touch_drag(x0 + 16, y0)
    driver.frame(1 / 30)
    driver.touch_up()
    driver.frame(1 / 30)
    changed = bytes(app.doc.pix)
    assert changed != before
    assert changed[(sy + 8) * app.doc.W + sx + 10] == app.color

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
    wall = next(c for c in moy_carts.scan(carts) if c["title"] == "Wallpaper")
    assert "bg" not in wall["images"]
    assert ws.artwork.set_wallpaper()
    wall = next(c for c in moy_carts.scan(carts) if c["title"] == "Wallpaper")
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
    app._mark_changed()
    assert app._save()

    assert ws.artwork.set_wallpaper()
    ws.look.tick(0.0)                          # the change shows at the next frame
    assert ws.look.wallpaper_id == "moybyte.wallpaper" == ws.look.PICTURE
    ws.look.commit()
    # The picture is the theme's: copied into `custom` (docs/theming_2026-09.md D6).
    assert moy_carts.load_system(carts)["theme"] == "custom"
    pic = Path(carts) / "custom.moy" / "desk.moyimg"
    assert pic.read_bytes() == moy_carts.load_artwork(carts).encode()

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
    assert paint_app.service is ws.artwork          # Paint's own model
    assert not hasattr(paint_app.ctx, "artwork")    # not a role it holds

    other = next(c for c in ws.carts.all if c["title"] == "Star Catcher")
    other.setdefault("permissions", []).append("artwork")
    ws._open_workspace(other)
    ws.run(ws.project, ws.launcher_layer)
    assert "artwork" not in ws.ns


def test_a_desks_drawing_is_1024x600_and_a_window_resize_never_resamples_it(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"),
                                    sys_size=(1024, 600), font_scale=2,
                                    windowed=True, panel_diagonal_in=7.0)
    ws.open_desk()
    app = _open_paint(ws)
    ws.frame(1 / 30)
    win = ws.wm._wins["artwork"]
    lay = win.ctx.artwork_layout
    assert (app.doc.W, app.doc.H) == (1024, 600)
    assert lay.desk
    assert app._quarters() == 2, "Fit shows the desk's drawing at a half in a window"
    assert app.sizes() == (2, 4, 8, 16)

    before = bytes(app.doc.pix)
    ws.wm._resize_window(win, 720, 430)
    assert win.ctx.artwork_layout.w == win.buf.w
    assert bytes(app.doc.pix) == before

    assert app._save()
    assert ws.artwork.set_wallpaper()
    ws.look.tick(0.0)
    ws.wallpaper.draw(0)
    src = app.doc.pix
    out = ws.sys_canvas
    # 1024x600 -> the 1024x600 desk, one to one. pix() reads a palette INDEX.
    for x, y in ((0, 0), (10, 10), (511, 299), (1023, 599)):
        assert out.pix(x, y) == src[y * 1024 + x]


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
    app._mark_changed()
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
    # The picture is over the full-surface bar: one loan, which a stroke
    # mends a rect of (`rebake`) rather than taking again.
    assert _bake_bytes(doc.image) >= device_canvas._OFFHEAP_BAKE_BYTES
    assert 1 <= device_canvas._MAX_LENT_BAKES

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


def _lending(monkeypatch, wall):
    """Point the wallpaper component and the device canvas at one tracked
    allocator -- the boards' moy_alloc, with its rules kept.

    Both modules are resolved through the objects under test rather than by the
    `runtime.`/`device.` spelling: a board stages these as top-level names, so
    the module a test patches and the one the code runs are two different
    objects unless the live one is asked for. Returns the canvas module so the
    register a test reads is the register the bake wrote."""
    import sys as _sys

    from runtime.host_canvas import install

    install()
    import importlib
    # The live module when one is loaded; otherwise this import makes it so.
    dc = importlib.import_module("device_canvas")
    aw = _sys.modules[type(wall).__module__]
    dc._owner_done("wallpaper_bg")     # an earlier test's backdrop: its own lifetime
    loans = _Loans()
    monkeypatch.setattr(dc, "_moybuf", loans)
    monkeypatch.setattr(aw, "_moybuf", loans)
    return _Ledger(loans, dc), dc


class _Ledger:
    """Every off-heap buffer the picture holds: the wallpaper component's
    indices through moybuf's tracked allocator, and the canvas's bake, which
    is the glass's loan to the picture's owner."""

    def __init__(self, loans, dc):
        self.loans = loans
        self.dc = dc

    def bakes(self):
        g = self.dc._glass_mod
        h = self.dc._OWNERS.get("wallpaper_bg")
        if h is None:
            return []
        return [g.row(r)[0] for r in g.rows(h) if g.row(r)[1] == g.ROLE_BAKE]

    def stats(self):
        n, total = self.loans.stats()
        b = self.bakes()
        return (n + len(b), total + sum(b))


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
    """A workstation whose desktop backdrop is a saved drawing (the WALL verb),
    copied into the theme as its picture; answers (ws, ws.wallpaper)."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    art = ws.artwork
    pix = bytearray(dw * dh)
    for y in range(dh):
        o = y * dw
        for x in range(dw):
            pix[o + x] = (x // 7 + y // 5) % 63
    assert art.save(pix, dw, dh), art.last_error
    assert art.set_wallpaper(), art.last_error
    ws.look.tick(0.0)
    assert ws.look.wallpaper_id == ws.look.PICTURE
    return ws, ws.wallpaper


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
    ws, wall = _published(tmp_path)
    loans, device_canvas = _lending(monkeypatch, wall)
    from runtime.artwork import PaintDocument

    cv = _device_canvas(ws, 320, 240)
    placed = []
    real_spr = cv.spr

    def _spy(img, x, y, *a, **k):
        placed.append((x, y) + a)
        return real_spr(img, x, y, *a, **k)

    cv.spr = _spy

    assert wall.draw_picture(cv) is True
    assert placed == [(0, 0)], "the backdrop is placed 1:1 at the origin"

    m = wall._pic_bitmap
    assert (m.w, m.h) == (320, 240), "the bitmap is the SCREEN, not the source"
    assert m._owner == type(wall).PIC_OWNER == "wallpaper_bg"
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
    assert loans.bakes() == [153600]

    # Redrawing reuses both -- the desktop draws this every frame it paints.
    for _ in range(5):
        wall.draw_picture(cv)
    assert loans.stats() == (2, 320 * 240 * 3)
    assert wall._pic_bitmap is m


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
    ws, wall = _published(tmp_path)
    loans, device_canvas = _lending(monkeypatch, wall)

    cv = _device_canvas(ws, 320, 240)
    wall.draw_picture(cv)
    on_picture = loans.stats()
    indices = wall._pic_bitmap.pix
    assert on_picture == (2, 320 * 240 * 3)

    for _ in range(6):
        ws.look.set_theme_variant("light")       # MODE keeps the picture shown
        assert wall._pic_bitmap.pix is indices and loans.stats() == on_picture
        ws.look.set_theme_variant("dark")
        ws.look.select_wallpaper(None, persist=False)
        # The bake is back; the indices, and the bitmap, are still cached.
        assert loans.stats() == (1, 320 * 240), "the bake outlived its backdrop"
        assert loans.bakes() == []
        assert wall._pic_bitmap is not None and wall._pic_bitmap.pix is indices
        assert wall._pic_bitmap._rgb_i is None, "a stale draw must re-bake"

        ws.look.select_wallpaper(ws.look.PICTURE, persist=False)
        wall.draw_picture(cv)
        assert loans.stats() == on_picture, "the ledger drifted across a switch"
    assert loans.loans.freed == 0, "the bake came back each change, and nothing else"


def test_publishing_a_new_drawing_returns_both_of_the_backdrops_loans(
        tmp_path, monkeypatch):
    """#186: WALL replaces the picture, so the cached screen is genuinely dead
    -- indices and bake both. set_wallpaper releases them itself rather than
    leaning on the select it ends with, because it can still answer False after
    clearing the cache and never reach one."""
    ws, wall = _published(tmp_path)
    loans, _dc = _lending(monkeypatch, wall)
    art = ws.artwork

    cv = _device_canvas(ws, 320, 240)
    wall.draw_picture(cv)
    assert loans.stats() == (2, 320 * 240 * 3)
    indices = wall._pic_bitmap.pix

    art.new_doc(512, 300)
    assert art.save(bytearray(512 * 300), 512, 300), art.last_error
    assert art.set_wallpaper(), art.last_error
    ws.look.tick(0.0)
    assert wall._picture(320, 240) is not None
    assert loans.stats() == (1, 320 * 240), "the replaced screen kept its buffers"
    assert wall._pic_bitmap.pix is not indices

    wall.draw_picture(cv)
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


def test_a_backdrop_that_already_fits_the_screen_is_read_straight_in(tmp_path,
                                                                    monkeypatch):
    """A drawing the size of the desk is read into the screen's own buffer,
    the same one loan as any other size (its indices, then the bake): no
    decoded copy of it is kept anywhere."""
    ws, wall = _published(tmp_path, 320, 240)
    loans, _dc = _lending(monkeypatch, wall)

    cv = _device_canvas(ws, 320, 240)
    wall.draw_picture(cv)
    assert wall._pic_pix is wall._pic_bitmap.pix
    assert loans.stats() == (2, 320 * 240 * 3)

    wall.drop_picture()
    assert loans.stats() == (0, 0)


# --------------------------------------------------------------------------- #
# The 2026-10 look's Paint (docs/media/look_2026-10/APPS_RESTYLE.md, Paint).  #
# --------------------------------------------------------------------------- #

def _type(ws, *keys):
    drv = host_app.ConsoleDriver(ws)
    for k in keys:
        drv.type_char(ord(k) if isinstance(k, str) else k)
        for _ in range(2):
            drv.frame(1 / 30)


def _wide(ws, name="garden", c=12):
    pix = bytearray([c]) * (1024 * 600)
    pix[600 * 1024 - 1] = 8
    moy_carts.save_file("drawings", name, moy_carts.encode_moyimg(1024, 600, pix),
                        ws.carts_root)


def test_an_undo_step_holds_the_tiles_a_mark_reached_not_the_drawing():
    from runtime.artwork import PaintDocument
    d = PaintDocument(1024, 600)
    before = bytes(d.pix)
    d.snapshot()
    d.line(10, 10, 600, 400, 8, 16, True)
    d.finish()
    held = sum(len(t[1]) for t in d.history[0])
    assert held < len(d.pix) // 8, "a diagonal stroke keeps the strip it crossed"
    after = bytes(d.pix)
    assert d.undo() and bytes(d.pix) == before
    assert d.redo() and bytes(d.pix) == after
    # what changed is the rect the canvas mends, not a whole re-bake
    d.take_dirty()
    d.snapshot()
    d.stamp(100, 100, 3, 4)
    d.finish()
    assert d.take_dirty() == (98, 98, 4, 4)


def test_the_tool_keys_pick_tools_and_a_repeat_on_shape_moves_its_shape_on(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    _type(ws, "b")
    assert app.tool == 1
    _type(ws, "s")
    assert app.tool == 4 and app.shape == 1
    _type(ws, "s")
    assert app.shape == 2, "Shape: rect -> oval on a repeat"
    _type(ws, "e", "e")
    assert app.tool == 2 and app.pop == "brush", "a repeat key opens the brush"
    assert app.back() and app.pop is None


def test_z_cycles_1x_2x_4x_and_fit_and_fit_is_the_largest_that_shows_it_whole(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    seen = [app._quarters()]
    for _ in range(4):
        _type(ws, "z")
        seen.append(app._quarters())
    assert seen == [4, 8, 16, 2, 4], "a 320x240 drawing in the T-Deck's view fits at a half"


def test_the_arrows_move_the_cursor_and_a_draws_there(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    c0 = list(app.cur)
    drv = host_app.ConsoleDriver(ws)
    drv.press("right")
    drv.frame(1 / 30)
    assert app.cur[0] == c0[0] + 1
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    x, y = app.cur
    assert app.doc.get(x, y) == app.color


def test_more_offers_put_in_a_game_only_for_a_game_size_drawing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    verbs = [r[0] for r in app._more_rows() if r != "-"]
    assert verbs == ["new", "open", "wallpaper", "game", "show"]
    app.doc.put(1, 1, 8)
    app._mark_changed()
    app._verb("game")
    assert app.page == "game" and app.game.carts
    title = app.game.carts[0].get("title")
    assert app._art.copies.attach(0, app._art.doc_name()) == title


def test_a_handheld_views_a_desks_drawing_and_never_decodes_it_whole(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.pointer.visible = False
    _wide(ws)
    ws.artwork.open_named("garden", "drawings")
    app = ws.artwork_app
    app._starter_pending = False
    app = _open_paint(ws)
    v = app.viewer
    assert v is not None and (v.W, v.H) == (1024, 600)
    assert app._doc is None, "no drawing decoded to draw on"
    assert v.fit_q == 1 and (v.fit_bitmap().w, v.fit_bitmap().h) == (256, 150)
    assert (v.mini().w, v.mini().h) == (64, 38)
    _type(ws, "p")
    assert app.toast[0] == "From a desk: you can look, not draw"
    zooms = []
    for _ in range(4):
        _type(ws, "z")
        zooms.append(v.quarters())
    assert zooms == [2, 4, 8, 1], "Fit, then a half, 1x, 2x"
    _type(ws, "z", "z")
    bmp, k = v.window()
    assert k == 1 and bmp.w <= 320 and v._src[1:3] == (1024, 600)
    # its More keeps the verbs a viewer has
    verbs = [r[0] for r in app._more_rows() if r != "-"]
    assert verbs == ["open", "wallpaper", "story", "show"]
    assert app.doc_title() == "Garden"


def test_the_first_save_of_a_new_drawing_says_its_name(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    app.doc.put(2, 2, 8)
    app._mark_changed()
    assert app._save()
    assert app.toast is not None and app.toast[0].startswith("Saved ")


def test_put_in_a_story_adds_a_last_page_and_its_undo_takes_it_out(tmp_path):
    from runtime import storybook_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    sb = ws._apps_by_id["storybook"]
    sb._new_story()
    app = _open_paint(ws)
    app.doc.put(2, 2, 8)
    app._mark_changed()
    assert app._save()
    files = ws.artwork.ctx.files
    stories = storybook_app.story_list(ws.artwork.ctx.carts, files)
    assert stories
    n = stories[0]["slides"]
    title, slide, undo = storybook_app.add_picture_slide(
        ws.artwork.ctx.carts, ws.artwork.ctx.files, stories[0], ws.artwork.doc_name())
    assert slide == n + 1
    assert storybook_app.story_list(ws.artwork.ctx.carts, files)[0]["slides"] == n + 1
    undo()
    assert storybook_app.story_list(ws.artwork.ctx.carts, files)[0]["slides"] == n


def test_the_desk_window_is_titled_with_the_drawing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True, panel_diagonal_in=7.0)
    ws.open_desk()
    app = _open_paint(ws)
    for _ in range(2):
        ws._dirty = True
        ws.frame(1 / 30)
    win = ws.wm._wins["artwork"]
    assert ws.wm._win_title(win).startswith("Paint")
    assert app.doc_title() in ws.wm._win_title(win)
    assert ws.wm._chip_title(win, True) == "Paint"


def _idle_paints(ws):
    from ws_helpers import quiesce
    drv = host_app.ConsoleDriver(ws)
    quiesce(ws)
    for _ in range(4):
        drv.frame(1 / 30)
    quiesce(ws)
    ws._dirty = True
    for _ in range(4):
        drv.frame(1 / 30)
    drawn = ws._frames_drawn
    for _ in range(10):
        drv.frame(1 / 30)
    return ws._frames_drawn - drawn


def test_a_settled_canvas_and_a_settled_view_paint_nothing(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.pointer.visible = False
    _open_paint(ws)
    assert _idle_paints(ws) == 0, "an idle canvas painted"
    _wide(ws)
    ws.artwork.open_named("garden", "drawings")
    ws.artwork_app.open()
    assert ws.artwork_app.viewer is not None
    assert _idle_paints(ws) == 0, "an idle view painted"


def test_a_tap_on_the_handheld_bars_zoom_chip_steps_the_zoom(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    for _ in range(3):
        ws._dirty = True
        ws.frame(1 / 30)
    bl = ws.bar_layer
    edge, rect, _dy, _cells = bl._packed("app")[0]
    box = (0, 0, rect[2], rect[3])
    end = bl._title_end("app", bl.lent_zone("app", bl._packed("app")[0]), box, False)
    chip = [h for h, _i, _off, name, _r in bl._lent_rects("app", end, box, False)
            if name == bl._lent("app")[4]]
    assert chip
    was = app.zoom
    x, y, w, h = chip[0]
    assert bl.handle_bar_tap("app", rect[0] + x + w // 2, rect[1] + y + h // 2)
    ws._dirty = True
    ws.frame(1 / 30)
    assert app.zoom != was


def _stroke_glass(tmp_path, monkeypatch, desk, stroke_frames, tool, shape=1, bufs=1,
                  keys=False, chosen=False, start=(16, 14)):
    """The glass after every frame of one scripted stroke, how many of them
    were stroke frames, the widest part of the view one repainted, and which
    frames from the press on were stroke frames. `bufs` > 1 presents from
    that many buffers in turn, as a board's ping-pong does: each frame draws
    over the one `bufs` frames before it. `chosen`: the tool was chosen before
    the page was painted, so the press is the stroke's first change; `start`
    is where in the view the pointer's stroke begins."""
    from runtime import paint_app
    if not stroke_frames:
        monkeypatch.setattr(paint_app, "_loop", None)
    if desk:
        ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                        font_scale=2, windowed=True, panel_diagonal_in=7.0)
        ws.open_desk()
    else:
        ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    root = getattr(ws.wm, "_root_canvas", None) or ws.sys_canvas
    root.RETAINED_FRAMES = bufs
    held = []
    if chosen:
        app.tool = tool
        app.shape = shape

    def frame():
        n = ws._frames_drawn
        drv.frame(1 / 30)
        if bufs > 1 and ws._frames_drawn != n:
            held.append(bytes(root._buf))
            root._buf[:] = held[-bufs] if len(held) >= bufs else held[0]
    # chosen: frames enough that every buffer holds the page
    for _ in range(3 + (bufs if chosen else 0)):
        ws._dirty = True
        frame()
    app.tool = tool
    app.shape = shape
    win = ws.wm._wins.get("artwork") if hasattr(ws.wm, "_wins") else None
    ox = oy = 0
    if win is not None:
        was = app.layout
        app.layout = win.ctx.artwork_layout
        x, y, w, h = app._spec()[:4]
        app.layout = was
        ox, oy = win.content_rect()[:2]
    else:
        x, y, w, h = app._spec()[:4]
    # The page below the bar, whose clock reads the wall's minute: the two
    # runs this compares are made at two moments.
    top = (oy if win is not None else app.layout.cy) * root.w * 2
    made = [0, 0, 0, []]
    real = app._stroke_frame

    def counted(cv, th):
        got = real(cv, th)
        made[3].append(got)
        if got:
            made[0] += 1
            c = app._chg
            made[1] = max(made[1], c[2] - c[0])
            made[2] = max(made[2], c[3] - c[1])
        return got
    app._stroke_frame = counted
    shots = []
    if keys:
        # the keys draw: `a` held, the arrows moving the ring cursor
        app.cur[0] = app.doc.W // 4
        app.cur[1] = app.doc.H // 4
        drv.hold("a", True)
        drv.hold("right", True)
        for i in range(40):
            drv.hold("down", i % 3 == 0)
            n = ws._frames_drawn
            frame()
            shots.append(bytes((held[-1] if bufs > 1 and ws._frames_drawn != n
                                else root._buf)[top:]))
        drv.hold("down", False)
        drv.hold("right", False)
        drv.hold("a", False)
        frame()
        shots.append(bytes((held[-1] if bufs > 1 else root._buf)[top:]))
        return shots, made, bytes(app.doc.pix)
    pts = [(x + start[0] + i * 5, y + start[1] + (i * 3) % (h - 30)) for i in range(30)]
    drv.touch(ox + pts[0][0], oy + pts[0][1])
    frame()
    for px, py in pts[1:]:
        drv.touch_drag(ox + px, oy + py)
        n = ws._frames_drawn
        frame()
        shots.append(bytes((held[-1] if bufs > 1 and ws._frames_drawn != n else root._buf)[top:]))
    drv.touch_up()
    frame()
    shots.append(bytes((held[-1] if bufs > 1 else root._buf)[top:]))
    return shots, made, bytes(app.doc.pix)


@pytest.mark.parametrize("desk, bufs", [(False, 1), (False, 2), (False, 3), (True, 1)])
@pytest.mark.parametrize("tool", ["brush", "eraser", "spray", "shape", "keys"])
def test_a_stroke_frame_paints_what_a_whole_frame_paints(tmp_path, monkeypatch, desk, bufs,
                                                         tool):
    """A frame whose only change is the stroke's repaints the part of the view
    it and the frames since its buffer was painted changed, and the glass is
    the glass a whole frame leaves, every frame -- on one buffer, and on two
    or three presented in turn; drawn with the pointer, and with the keys,
    whose ring cursor moves with the stroke."""
    from runtime import paint_app
    t = {"brush": paint_app.BRUSH, "eraser": paint_app.ERASER, "spray": paint_app.SPRAY,
         "shape": paint_app.SHAPE, "keys": paint_app.BRUSH}[tool]
    k = tool == "keys"
    whole, none, pix0 = _stroke_glass(tmp_path / "a", monkeypatch, desk, False, t, bufs=bufs,
                                      keys=k)
    monkeypatch.undo()
    part, made, pix1 = _stroke_glass(tmp_path / "b", monkeypatch, desk, True, t, bufs=bufs,
                                     keys=k)
    assert none[0] == 0
    assert made[0] >= 20, "the stroke was painted whole every frame"
    assert pix0 == pix1
    for i, (a, b) in enumerate(zip(whole, part)):
        assert a == b, "frame %d of the stroke differs from a whole frame" % i
    if tool != "shape":
        # a short step a frame: what a stroke frame repaints is the last few
        # steps' dots, never the stroke so far
        assert made[1] <= 48 and made[2] <= 48, made


def test_a_banner_over_the_page_makes_every_frame_whole(tmp_path):
    """The unlock banner's soft edges blend over what is under them, so a
    stroke frame, which paints a part of the page, waits while it is up."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    ws.ach.award("first_run")
    drv.frame(1 / 30)
    assert ws._toast_until and ws.sys_canvas.covered
    assert not app._may(ws.sys_canvas)
    ws._toast_until = 0
    drv.frame(1 / 30)
    assert not ws.sys_canvas.covered


@pytest.mark.parametrize("desk, bufs", [(False, 1), (False, 2), (False, 3), (True, 1)])
@pytest.mark.parametrize("tool", ["brush", "keys"])
def test_a_stroke_is_stroke_frames_from_its_press(tmp_path, monkeypatch, desk, bufs, tool):
    """A press on a page every buffer holds changes nothing but a mark: the
    stroke's first frames are stroke frames, never whole ones (a fast stroke
    begun on whole frames took its first samples far apart and drew them as
    straight lines), and the glass is a whole frame's."""
    from runtime import paint_app
    k = tool == "keys"
    # begun clear of the view's rounded corners, which take a whole frame
    at = (48, 40)
    whole, _, pix0 = _stroke_glass(tmp_path / "a", monkeypatch, desk, False, paint_app.BRUSH,
                                   bufs=bufs, keys=k, chosen=True, start=at)
    monkeypatch.undo()
    part, made, pix1 = _stroke_glass(tmp_path / "b", monkeypatch, desk, True, paint_app.BRUSH,
                                     bufs=bufs, keys=k, chosen=True, start=at)
    stroke = made[3]
    assert stroke[0], "the press was drawn whole"
    if not (desk and k):
        # (a desk draws frames of its own between a held key's steps, which
        # Paint does not paint and so cannot count as its buffer's)
        assert all(stroke[:-1]), "frames from the press drawn whole: %s" % stroke[:12]
    assert pix0 == pix1
    for i, (a, b) in enumerate(zip(whole, part)):
        assert a == b, "frame %d of the stroke differs from a whole frame" % i


@pytest.mark.parametrize("bufs", [1, 2, 3])
@pytest.mark.parametrize("change", ["tool", "open"])
def test_a_page_change_asks_for_the_frames_that_paint_it_into_every_buffer(tmp_path, bufs,
                                                                           change):
    """A board draws Paint only on a frame something asked for. So a whole
    frame that moved the page -- a tool picked, the drawing opened -- either
    asks for one more or leaves every buffer of the canvas holding the page,
    RETAINED_FRAMES paints of it in a row; an idle board has then painted it
    by the next press, which is a stroke frame from its first."""
    from runtime import paint_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    app = _open_paint(ws)
    drv = host_app.ConsoleDriver(ws)
    (getattr(ws.wm, "_root_canvas", None) or ws.sys_canvas).RETAINED_FRAMES = bufs
    for _ in range(4):
        drv.frame(1 / 30)
    asked = [0]
    dmg = app._damage
    for verb in ("again", "settle"):
        real = getattr(dmg, verb)
        setattr(dmg, verb, lambda _real=real: asked.__setitem__(0, asked[0] + 1) or _real())
    whole = []
    stroke = app._stroke_frame

    def counted(cv, th):
        got = stroke(cv, th)
        if not got:
            whole.append(paint_app._loop.drawn())
        return got
    app._stroke_frame = counted
    draw = app.draw

    def drawn(dt):
        n, a = len(whole), asked[0]
        draw(dt)
        if len(whole) > n:
            f = whole[-1]
            assert asked[0] > a or app._kept(f + 1, app._epoch, None), (
                "frame %d moved the page, asked for no frame after it, and left "
                "a buffer without the page" % f)
    app.draw = drawn
    if change == "tool":
        app.tool = paint_app.ERASER
        app._damage.all()
    else:
        app.open()
    for _ in range(6):
        drv.frame(1 / 30)
    assert whole and len(whole) <= bufs + 1, whole
