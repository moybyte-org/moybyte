"""The cover-art pipeline (#155): read the RLE blob, decode it, crop it to the
card, cache what is expensive.

What is expensive was measured on P4 glass: reading the blob 46.9ms and PARSING
it (base64 + RLE) 17.1ms, against a native decode 0.89ms and a native crop
0.76ms. So the parsed RUNS are what get cached in RAM; the decode and crop are
cheap enough to redo per size, which is what makes a window resize cheap.

Two caches were tried and removed, both because a sidecar read cost more than
the work it saved on a board whose flash reads at ~470KB/s: a 77KB decoded
SOURCE (164ms per read), and #86's per-size crop sidecars (~66ms to read, the
same as rebuilding from the blob, plus a ~30ms write per cover per size)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import moy_carts  # noqa: E402


def _cover_text(w, h, value):
    return moy_carts.encode_moyimg(w, h, bytes([value]) * (w * h))


def _mk_cart_with_cover(tmp_path, value=5):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Covered", root, src="def _draw():\n    pass\n")
    moy_carts.save_image(cart, "cover", _cover_text(64, 48, value))
    return cart


# -- store level ----------------------------------------------------------------

def test_cover_sig_moves_with_content():
    a = _cover_text(64, 48, 5)
    b = _cover_text(64, 48, 9)
    assert moy_carts.cover_sig(a) != moy_carts.cover_sig(b)
    assert moy_carts.cover_sig(a) == moy_carts.cover_sig(a)


# -- console level ---------------------------------------------------------------

def _land_cover(ws, cart, w, h, frames=300):
    """Step the per-frame cover budget until the (w, h) cover lands."""
    for _ in range(frames):
        ws._cover_built = False          # frame() resets this once per frame
        img = ws._cover_for(cart, w, h)
        if img is not None:
            return img
    raise AssertionError("cover never landed")


def _clear_ram_caches(ws):
    # mirror the store re-scan clear
    ws._cover_cache = {}
    ws._cover_cache_order = []
    ws._cover_cache_pixels = 0
    ws._cover_jobs = {}
    ws._cover_runs = {}
    ws._cover_runs_order = []
    ws._cover_runs_bytes = 0


def test_edited_cover_invalidates_the_thumb(tmp_path):
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _land_cover(ws, cart, 40, 30)

    # edit the cover art -> the old sidecar is stale and must NOT be served
    moy_carts.save_image(cart, "cover", _cover_text(64, 48, 9))
    _clear_ram_caches(ws)
    img = _land_cover(ws, cart, 40, 30)
    assert img.pix[0] == 9                       # rebuilt from the NEW art
    # ...and the rebuild refreshed the sidecar for the next session
    _clear_ram_caches(ws)
    ws._cover_built = False
    img2 = ws._cover_for(cart, 40, 30)
    assert img2 is not None and img2.pix[0] == 9 and ws._cover_jobs == {}


# -- resize must not re-decode (#155) -------------------------------------------

def test_a_new_size_re_crops_instead_of_re_decoding(tmp_path):
    """Owner, on glass 2026-07-26: "covers are remade every time you resize the
    launcher."

    Both the cover cache and its thumb sidecar are keyed by (path, w, h), so any
    relayout -- a window resize, or the hop between the fullscreen Library and
    the windowed picker -- missed on every cover and re-ran the 0.5-1.7s RLE
    decode for each one. The decode is size-INDEPENDENT; only the crop after it
    depends on the size. So the SOURCE is cached and a new size adopts it,
    finishing in a single step instead of hundreds."""
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _land_cover(ws, cart, 40, 30)
    runs = ws._cover_runs_get(cart["path"])
    assert runs is not None and runs[0] == 64 and runs[1] == 48, \
        "the parsed runs were not cached"

    # A DIFFERENT size, with no sidecar for it: one step, no decode.
    _clear_ram_caches(ws)
    ws._cover_built = False
    img = ws._cover_for(cart, 24, 18)
    assert img is not None, "a new size still needed multiple frames"
    assert len(img.pix) == 24 * 18 and img.pix[0] == 5


def test_an_edited_cover_is_picked_up_after_a_rescan(tmp_path):
    """The runs cache is keyed by path and trusted for the session -- computing a
    content stamp would mean reading the blob, which is the cost it exists to
    avoid. A re-scan is what drops it, and that is the path a cover edit takes."""
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    img = _land_cover(ws, cart, 40, 30)
    assert img.pix[0] == 5
    moy_carts.save_image(cart, "cover", _cover_text(64, 48, 9))
    ws._apply_items(moy_carts.scan(str(tmp_path / "carts")))
    cart = next(c for c in ws._all_carts if c.get("path") == cart["path"])
    img = _land_cover(ws, cart, 24, 18)
    assert img.pix[0] == 9, "a re-scan did not drop the cached runs"


def test_the_runs_cache_is_bounded(tmp_path):
    """Runs are ~15KB each, so the cache must stay bounded."""
    from runtime import console, host_app
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    carts = []
    # 320x240 sources are 76.8KB each, so ten of them must overrun the cap.
    for i in range(10):
        c = moy_carts.create("C%d" % i, root, src="def _draw():\n    pass\n")
        moy_carts.save_image(c, "cover", _cover_text(320, 240, i + 1))
        carts.append(c)
    ws = host_app.build_workstation(root)
    for c in carts:
        _land_cover(ws, c, 40, 30, frames=2000)
    assert ws._cover_runs_bytes <= console._COVER_RUNS_MAX_BYTES
    assert len(ws._cover_runs) == len(ws._cover_runs_order)


def test_native_and_python_crops_are_byte_identical(tmp_path):
    """The native crop (moy_gfx.crop_index) exists so a relayout costs a
    millisecond instead of 20-40ms per card. It is only safe because it
    reproduces the Python crop exactly -- same integer floors, same source
    window. This runs the Python path directly and compares.

    (On the host there is no moy_gfx, so the fast path is absent and this
    pins the REFERENCE the device kernel was written against; the device half
    is grepped by tests/test_micropython_spike.py.)"""
    from runtime import console
    from runtime.console import _ticks_ms

    def python_crop(pix, sw, sh, w, h):
        cw_ = min(sw, sh * w // h) or 1
        ch_ = min(sh, sw * h // w) or 1
        ox = (sw - cw_) // 2
        oy = (sh - ch_) // 2
        xmap = [ox + dx * cw_ // w for dx in range(w)]
        out = bytearray(w * h)
        for dy in range(h):
            base = (oy + dy * ch_ // h) * sw
            for dx in range(w):
                out[dy * w + dx] = pix[base + xmap[dx]]
        return bytes(out)

    # A source with structure, so a wrong sample lands on a different value.
    sw, sh = 64, 48
    pix = bytearray((x * 7 + y * 3) & 63 for y in range(sh) for x in range(sw))
    for (w, h) in ((40, 30), (24, 18), (64, 48), (17, 41), (7, 5), (100, 20)):
        job = console._CoverJob((sw, sh, b""), w, h, src=pix)
        # step() is time-sliced, so drive it to completion (the native path
        # finishes in the first call; the Python loop takes several).
        for _ in range(2000):
            if job.done:
                break
            job.step(_ticks_ms())
        assert job.done and job.img is not None, (w, h)
        assert job.img.pix == python_crop(pix, sw, sh, w, h), (w, h)


# -- idle prefetch (#155, P4 glass 2026-07-26) ----------------------------------

def _mk_carts_with_covers(tmp_path, n, with_cover=3):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    out = []
    for i in range(n):
        c = moy_carts.create("C%d" % i, root, src="def _draw():\n    pass\n")
        if i < with_cover:
            moy_carts.save_image(c, "cover", _cover_text(32, 24, i + 1))
        out.append(c)
    return root, out


def test_idle_frames_prefetch_cover_runs(tmp_path):
    """A cover's blob read + parse is ~108ms on P4 flash and is SIZE-INDEPENDENT.
    Charged lazily, it lands on the frame that first needs the card -- during a
    shelf drag, that is a drag frame, which is why the picker measured a 577ms
    worst frame. Idle frames do nothing, so they pay it instead.

    The assertion is that runs for a cart NOT on screen become cached while the
    console is idle -- that is what makes the later drag free."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 4, with_cover=3)
    ws = host_app.build_workstation(root)
    covered = [c for c in ws._all_carts if c.get("path") in
               [x["path"] for x in carts[:3]]]
    assert covered, "fixture carts missing from the store"

    # Nothing has asked for a cover yet -> the prefetch must stay asleep, so a
    # device sitting in an editor never warms a cache nobody wants.
    for _ in range(50):
        ws._cover_prefetch_tick()
    assert all(ws._cover_runs_get(c["path"]) is None for c in covered)

    # A surface draws a card -> prefetch is armed and warms the REST on idle.
    _land_cover(ws, covered[0], 20, 15)
    assert ws._cover_seen
    for _ in range(200):
        ws._cover_prefetch_tick()
    for c in covered:
        assert ws._cover_runs_get(c["path"]) is not None, c["path"]


def test_prefetch_makes_a_later_build_touch_no_storage(tmp_path):
    """The point of warming runs: the build that follows must not read flash."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 3, with_cover=3)
    ws = host_app.build_workstation(root)
    # By PATH: _all_carts also holds the seeded built-ins, most of which have no
    # cover at all, so an index into it is not necessarily a fixture cart.
    want = carts[-1]["path"]
    target = next(c for c in ws._all_carts if c.get("path") == want)
    first = next(c for c in ws._all_carts if c.get("path") == carts[0]["path"])
    _land_cover(ws, first, 20, 15)                 # arm
    for _ in range(200):
        ws._cover_prefetch_tick()

    reads = []
    orig = ws.carts_store.load_image

    def spy(path, name, _orig=orig):
        reads.append(path)
        return _orig(path, name)
    ws.carts_store.load_image = spy
    img = _land_cover(ws, target, 20, 15)
    assert img is not None
    assert reads == [], "the build re-read the blob the prefetch already parsed"


def test_prefetch_stops_once_every_cart_is_known(tmp_path):
    """It must not spin: once every cart is either warmed or known cover-less it
    disarms, so an idle console is not walking the cart list forever."""
    from runtime import host_app
    root, _carts = _mk_carts_with_covers(tmp_path, 3, with_cover=1)
    ws = host_app.build_workstation(root)
    _land_cover(ws, ws._all_carts[0], 20, 15)
    for _ in range(200):
        ws._cover_prefetch_tick()
    assert ws._cover_seen is False


def test_cover_blob_read_budget(tmp_path):
    """Each cart's cover blob must be read from storage AT MOST ONCE per session.

    A read is 58ms on P4 flash (22ms even when the file is absent), so a repeat
    read is a stall the owner feels. Two separate bugs here re-read blobs -- a
    cache keyed on a stamp stashed on a cart dict that did not survive a relayout,
    and per-size keying that missed on every resize -- and neither announced
    itself. This is the budget that would have."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 4, with_cover=4)
    ws = host_app.build_workstation(root)
    mine = [c for c in ws._all_carts
            if c.get("path") in [x["path"] for x in carts]]
    ws.costs.clear()
    for size in ((40, 30), (24, 18), (40, 30)):      # includes a RELAYOUT
        for c in mine:
            _land_cover(ws, c, *size)
    reads = ws.costs.get("cover.blob.read", 0)
    assert reads >= 1, "no blob read counted -- is ws.note_cost still wired?"
    assert reads <= len(mine), (
        "read %d blobs for %d carts across three layouts -- the runs cache is not "
        "holding" % (reads, len(mine)))
