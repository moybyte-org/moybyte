"""Persistent cover thumbnails (#66 "decode covers once"): a finished shelf-card
crop is saved as a sidecar (<cart>/thumbs/<w>x<h>.mct) and every later session
(or re-scan / LRU refill) loads it in one small read instead of re-running the
0.5-1.7s time-sliced RLE decode. Stale (edited-cover) and corrupt sidecars are
ignored and rebuilt."""

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

def test_thumb_roundtrip_and_validation(tmp_path):
    cart = _mk_cart_with_cover(tmp_path)
    path = cart["path"]
    pix = bytes(range(40)) * 30          # 40x30 crop payload
    sig = moy_carts.cover_sig("some cover blob")
    moy_carts.save_cover_thumb(path, 40, 30, sig, pix)
    assert moy_carts.load_cover_thumb(path, 40, 30, sig) == pix
    # wrong stamp (edited cover) / wrong size / absent -> None, never garbage
    assert moy_carts.load_cover_thumb(path, 40, 30, sig ^ 1) is None
    assert moy_carts.load_cover_thumb(path, 30, 40, sig) is None
    assert moy_carts.load_cover_thumb(path, 41, 30, sig) is None
    # corrupt file -> None
    with open(path + "/thumbs/40x30.mct", "wb") as f:
        f.write(b"JUNKJUNK" + pix)
    assert moy_carts.load_cover_thumb(path, 40, 30, sig) is None


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
    # mirror the store re-scan clear: RAM caches gone, sidecars remain
    ws._cover_cache = {}
    ws._cover_cache_order = []
    ws._cover_cache_pixels = 0
    ws._cover_jobs = {}
    ws._cover_runs = {}
    ws._cover_runs_order = []
    ws._cover_runs_bytes = 0


def test_cover_persists_and_reloads_without_a_decode(tmp_path):
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    img = _land_cover(ws, cart, 40, 30)
    assert len(img.pix) == 40 * 30 and img.pix[0] == 5
    assert (Path(cart["path"]) / "thumbs" / "40x30.mct").exists()

    # "next session": RAM caches cleared -> ONE call returns the image from the
    # sidecar, with no decode job ever created.
    _clear_ram_caches(ws)
    ws._cover_built = False
    img2 = ws._cover_for(cart, 40, 30)
    assert img2 is not None and img2.pix == img.pix
    assert ws._cover_jobs == {}


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
    assert ws._cover_runs_get(cart["path"], None) is None     # sig must match
    sig = moy_carts.cover_sig(moy_carts.load_image(cart["path"], "cover"))
    runs = ws._cover_runs_get(cart["path"], sig)
    assert runs is not None and runs[0] == 64 and runs[1] == 48, \
        "the parsed runs were not cached"

    # A DIFFERENT size, with no sidecar for it: one step, no decode.
    _clear_ram_caches(ws)
    ws._cover_built = False
    img = ws._cover_for(cart, 24, 18)
    assert img is not None, "a new size still needed multiple frames"
    assert len(img.pix) == 24 * 18 and img.pix[0] == 5


def test_the_source_cache_is_stamped_against_the_cover(tmp_path):
    """An edited cover must not be re-cropped from the old source."""
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _land_cover(ws, cart, 40, 30)
    moy_carts.save_image(cart, "cover", _cover_text(64, 48, 9))
    new_sig = moy_carts.cover_sig(moy_carts.load_image(cart["path"], "cover"))
    assert ws._cover_runs_get(cart["path"], new_sig) is None
    _clear_ram_caches(ws)
    img = _land_cover(ws, cart, 24, 18)
    assert img.pix[0] == 9, "re-cropped from the stale source"


def test_the_source_cache_is_bounded(tmp_path):
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
