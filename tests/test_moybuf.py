"""#186 moy_buf: cover payloads (cover files, decoded pictures, bakes) move OFF
the MP gc heap on device, and every eviction path must FREE them -- while
never freeing a payload an in-flight _CoverJob still reads (leak beats
use-after-free). The host has no moy_alloc, so these tests install a
tracking fake as runtime.console's _moybuf: its alloc/take return REAL
memoryviews (so the console's isinstance ownership checks fire) and its
free() enforces exactly the single-owner rule the C registry enforces."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import moy_carts  # noqa: E402


class _Tracker:
    def __init__(self):
        self.live = {}       # id(view) -> view
        self.freed = 0

    def alloc(self, n):
        v = memoryview(bytearray(n))
        self.live[id(v)] = v
        return v

    def take(self, payload):
        v = self.alloc(len(payload))
        v[:] = payload
        return v

    def free(self, buf):
        if not isinstance(buf, memoryview):
            return           # gc-owned fallback storage: no-op (moybuf.free)
        if id(buf) not in self.live:
            raise AssertionError("freed a foreign or already-freed buffer")
        del self.live[id(buf)]
        self.freed += 1


def _mk_cart(tmp_path, name="Covered", value=5):
    from ws_helpers import cover_bytes
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create(name, root, src="def _draw():\n    pass\n")
    moy_carts.save_cover(cart, cover_bytes(value))
    return cart


def _tracked_ws(tmp_path, monkeypatch):
    from runtime import cover_cache, host_app
    tr = _Tracker()
    monkeypatch.setattr(cover_cache, "_moybuf", tr)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    return ws, tr


def _land(ws, cart, div=1, frames=300):
    for _ in range(frames):
        ws.covers._built = False          # frame() resets these once per frame
        ws.covers._ms = 0
        img = ws.covers.cover_for(cart, div)
        if img is not None:
            return img
    raise AssertionError("cover never landed")


def test_cover_payloads_live_off_heap(tmp_path, monkeypatch):
    cart = _mk_cart(tmp_path)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    img = _land(ws, cart)
    assert isinstance(img.pix, memoryview)               # the picture
    src = ws.covers._src_get(cart["path"])
    assert src is not None and isinstance(src, memoryview)  # the file
    assert id(img.pix) in tr.live and id(src) in tr.live


def test_rescan_frees_every_payload(tmp_path, monkeypatch):
    a = _mk_cart(tmp_path, "CoverA", 5)
    b = _mk_cart(tmp_path, "CoverB", 9)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, a)
    _land(ws, b)
    assert tr.live                                       # payloads are warm
    ws.carts.apply(list(ws.carts.all))                 # the store re-scan
    assert tr.live == {}                                 # ...frees ALL of it


def test_cover_lru_eviction_frees_the_old_card(tmp_path, monkeypatch):
    from runtime import cover_cache
    cart = _mk_cart(tmp_path)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    monkeypatch.setattr(cover_cache, "_COVER_CACHE_MAX_ENTRIES", 1)
    img1 = _land(ws, cart)
    freed_before = tr.freed
    _land(ws, cart, 2)                   # the second size evicts the first
    assert tr.freed > freed_before
    assert img1.pix is None              # nulled: a stale draw raises, loudly


def test_file_eviction_frees_unless_a_job_reads_it(tmp_path, monkeypatch):
    from runtime import cover_cache
    a = _mk_cart(tmp_path, "CoverA", 5)
    b = _mk_cart(tmp_path, "CoverB", 9)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, a)
    blob_a = ws.covers._src_get(a["path"])
    # Shrink the byte cap so the next put evicts cart A's file.
    monkeypatch.setattr(cover_cache, "_COVER_SRC_MAX_BYTES", 1)

    class _Job:                          # an in-flight decode holding the file
        src = blob_a
        pix = None
        done = True
        img = None

    ws.covers._jobs[("fake", 1)] = _Job()
    # Store B's file the way a load does: the put evicts A's.
    ws.covers._src_put(b["path"], tr.take(moy_carts.load_cover(b["path"])))
    assert ws.covers._src_get(a["path"]) is None          # evicted from the LRU
    assert id(blob_a) in tr.live         # ...but NOT freed: the job reads it
    # With the job gone, the same eviction path frees.
    ws.covers._jobs = {}
    ws.covers._free_src(blob_a)
    assert id(blob_a) not in tr.live


def test_free_cover_img_is_alias_safe(tmp_path, monkeypatch):
    from runtime.cover_cache import _CoverImage
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    img = _CoverImage(4, 3, tr.take(b"\x01" * 12))
    img._rgb_i = tr.alloc(24)
    rgb = tr.alloc(24)
    img._rgb = rgb
    img._rgb_variants = {(1, 0, 0): (rgb, 4, 3)}   # ALIASES the hot slot
    ws.covers._free_img(img)              # a double free would raise (tracker)
    assert tr.live == {}
    assert img.pix is None and img._rgb is None and img._rgb_i is None


def test_moybuf_host_fallback_is_transparent():
    from runtime import moybuf
    if moybuf._ALLOC is not None:        # only meaningful without moy_alloc
        return
    b = moybuf.alloc(8)
    assert isinstance(b, bytearray) and len(b) == 8
    payload = b"\x05" * 6
    assert moybuf.take(payload) is payload   # zero copies on the host
    moybuf.free(b)                           # no-op, must not raise
    assert moybuf.stats() == (0, 0)
