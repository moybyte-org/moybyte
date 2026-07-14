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
