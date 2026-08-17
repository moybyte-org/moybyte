"""#186 moy_buf: cover payloads (parsed runs, card bitmaps, bakes) move OFF
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


def _cover_text(w, h, value):
    return moy_carts.encode_moyimg(w, h, bytes([value]) * (w * h))


def _mk_cart(tmp_path, name="Covered", value=5):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create(name, root, src="def _draw():\n    pass\n")
    moy_carts.save_image(cart, "cover", _cover_text(64, 48, value))
    return cart


def _tracked_ws(tmp_path, monkeypatch):
    from runtime import console, host_app
    tr = _Tracker()
    monkeypatch.setattr(console, "_moybuf", tr)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    return ws, tr


def _land(ws, cart, w, h, frames=300):
    for _ in range(frames):
        ws._cover_built = False          # frame() resets these once per frame
        ws._cover_ms = 0
        img = ws._cover_for(cart, w, h)
        if img is not None:
            return img
    raise AssertionError("cover never landed")


def test_cover_payloads_live_off_heap(tmp_path, monkeypatch):
    cart = _mk_cart(tmp_path)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    img = _land(ws, cart, 40, 30)
    assert isinstance(img.pix, memoryview)               # the card bitmap
    runs = ws._cover_runs_get(cart["path"])
    assert runs is not None and isinstance(runs[2], memoryview)  # the blob
    assert id(img.pix) in tr.live and id(runs[2]) in tr.live


def test_rescan_frees_every_payload(tmp_path, monkeypatch):
    a = _mk_cart(tmp_path, "CoverA", 5)
    b = _mk_cart(tmp_path, "CoverB", 9)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, a, 40, 30)
    _land(ws, b, 40, 30)
    assert tr.live                                       # payloads are warm
    ws._apply_items(list(ws._all_carts))                 # the store re-scan
    assert tr.live == {}                                 # ...frees ALL of it


def test_cover_lru_eviction_frees_the_old_card(tmp_path, monkeypatch):
    from runtime import console
    cart = _mk_cart(tmp_path)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    monkeypatch.setattr(console, "_COVER_CACHE_MAX_ENTRIES", 1)
    img1 = _land(ws, cart, 40, 30)
    freed_before = tr.freed
    _land(ws, cart, 20, 15)              # second size evicts the first card
    assert tr.freed > freed_before
    assert img1.pix is None              # nulled: a stale draw raises, loudly


def test_runs_eviction_frees_unless_a_job_reads_it(tmp_path, monkeypatch):
    from runtime import console
    a = _mk_cart(tmp_path, "CoverA", 5)
    b = _mk_cart(tmp_path, "CoverB", 9)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, a, 40, 30)
    blob_a = ws._cover_runs_get(a["path"])[2]
    # Shrink the byte cap so the next put evicts cart A's runs entry.
    monkeypatch.setattr(console, "_COVER_RUNS_MAX_BYTES", 1)

    class _Job:                          # an in-flight decode holding the blob
        packed = blob_a
        pix = None

    ws._cover_jobs[("fake", 1, 1)] = _Job()
    _land(ws, b, 40, 30)                 # loads + puts B -> evicts A
    assert ws._cover_runs_get(a["path"]) is None         # evicted from the LRU
    assert id(blob_a) in tr.live         # ...but NOT freed: the job reads it
    # With the job gone, the same eviction path frees.
    ws._cover_jobs = {}
    ws._cover_free_runs(blob_a)
    assert id(blob_a) not in tr.live


def test_free_cover_img_is_alias_safe(tmp_path, monkeypatch):
    from runtime.console import _CoverImage
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    img = _CoverImage(4, 3, tr.take(b"\x01" * 12))
    img._rgb_i = tr.alloc(24)
    rgb = tr.alloc(24)
    img._rgb = rgb
    img._rgb_variants = {(1, 0, 0): (rgb, 4, 3)}   # ALIASES the hot slot
    ws._free_cover_img(img)              # a double free would raise (tracker)
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
