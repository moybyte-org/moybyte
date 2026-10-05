"""The cover pipeline (#155, SPEC.md 3.6): read a cart's cover.png, decode it,
cache what is expensive.

What is expensive is the READ: flash, behind the storage gate, and on the S3
boards a card shared with the panel. So the file is read once per session and
kept; the decode that turns it into the shelf's 128x128 base is native and
cheap, and the base itself is cached and drawn at a whole-number scale, so a
relayout -- a window resize, the hop between the Library and the picker --
touches neither the file nor the decoder.

Two caches were tried and removed in the RLE era, both because a sidecar read
cost more than the work it saved on a board whose flash reads at ~470KB/s: a
decoded SOURCE (164ms per read) and per-size crop sidecars (~66ms to read, the
same as rebuilding, plus a write per cover per size). Nothing here writes."""

from pathlib import Path
from ws_helpers import shelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

from runtime import cover_cache, cover_png, moy_carts  # noqa: E402
from ws_helpers import cover_bytes  # noqa: E402

BASE = cover_cache.BASE
HALF = cover_cache.HALF


def _mk_cart_with_cover(tmp_path, value=5):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Covered", root, src="def _draw():\n    pass\n")
    moy_carts.save_cover(cart, cover_bytes(value))
    return cart


def _land_cover(ws, cart, div=BASE, frames=300):
    """Step the per-frame cover budget until the cover lands."""
    for _ in range(frames):
        ws.covers._built = False          # frame() resets this once per frame
        ws.covers._ms = 0
        img = ws.covers.cover_for(cart, div)
        if img is not None:
            return img
    raise AssertionError("cover never landed")


def _clear_ram_caches(ws):
    # mirror the store re-scan clear
    ws.covers._cache = {}
    ws.covers._order = []
    ws.covers._bytes = 0
    ws.covers._jobs = {}
    ws.covers._src = {}
    ws.covers._src_order = []
    ws.covers._src_bytes = 0


def _first_word(img, ws):
    """The cover's top-left pixel as a palette index, through the canvas's
    own byte order."""
    from device.device_canvas import PAL565_WIRE
    w = img.pix[0] | (img.pix[1] << 8)
    return list(PAL565_WIRE).index(w)


# -- store level ----------------------------------------------------------------

def test_the_store_reads_a_cover_as_bytes_and_refuses_an_oversized_one(tmp_path):
    cart = _mk_cart_with_cover(tmp_path)
    data = moy_carts.load_cover(cart["path"])
    assert data == cover_bytes(5)
    assert cover_png.decode(data) is not None
    moy_carts.save_cover(cart, b"\0" * (moy_carts.COVER_MAX_BYTES + 1))
    assert moy_carts.load_cover(cart["path"]) is None
    assert moy_carts.COVER_MAX_BYTES == cover_png.MAX_BYTES


def test_a_cart_with_no_cover_reads_none(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Bare", root, src="def _draw():\n    pass\n")
    assert moy_carts.load_cover(cart["path"]) is None


def test_a_copy_of_a_cart_keeps_its_cover(tmp_path):
    """The picker's COPY copies the folder byte for byte -- a copy that read
    every file as text would lose the one that is not."""
    cart = _mk_cart_with_cover(tmp_path, value=12)
    root = str(tmp_path / "carts")
    loaded = moy_carts.load(cart["path"])
    dup = moy_carts.duplicate(loaded, root)
    assert moy_carts.load_cover(dup["path"]) == cover_bytes(12)


# -- console level ---------------------------------------------------------------

def test_an_edited_cover_is_read_again_once_the_caches_drop(tmp_path):
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    img = _land_cover(ws, cart)
    assert _first_word(img, ws) == 5

    moy_carts.save_cover(cart, cover_bytes(9))
    _clear_ram_caches(ws)
    img = _land_cover(ws, cart)
    assert _first_word(img, ws) == 9                # rebuilt from the NEW file


def test_a_relayout_needs_no_storage_and_no_decode(tmp_path):
    """Owner, on glass 2026-07-26: "covers are remade every time you resize the
    launcher." The base is ONE size, drawn at whatever whole-number scale a
    card takes, so a new card size is a cache hit."""
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    img = _land_cover(ws, cart)
    ws.costs.clear()
    ws.covers._built = False
    assert ws.covers.cover_for(cart, BASE) is img
    assert ws.costs.get("cover.build", 0) == 0
    assert ws.costs.get("cover.blob.read", 0) == 0


def test_the_second_size_of_a_cover_reads_no_storage(tmp_path):
    """HALF is a second decode of the SAME file, which is still in RAM."""
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _land_cover(ws, cart)
    ws.costs.clear()
    half = _land_cover(ws, cart, HALF)
    assert (half.w, half.h) == (64, 64)
    assert ws.costs.get("cover.blob.read", 0) == 0


def test_an_edited_cover_is_picked_up_after_a_rescan(tmp_path):
    """The file cache is keyed by path and trusted for the session -- a content
    stamp would mean reading the file, which is the cost it exists to avoid. A
    re-scan is what drops it, and that is the path a cover edit takes."""
    from runtime import host_app
    cart = _mk_cart_with_cover(tmp_path, value=5)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    img = _land_cover(ws, cart)
    assert _first_word(img, ws) == 5
    moy_carts.save_cover(cart, cover_bytes(9))
    ws.carts.apply(shelf(str(tmp_path / "carts")))
    cart = next(c for c in ws.carts.all if c.get("path") == cart["path"])
    img = _land_cover(ws, cart)
    assert _first_word(img, ws) == 9, "a re-scan did not drop the cached file"


def test_the_file_cache_is_bounded(tmp_path, monkeypatch):
    from runtime import host_app
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    carts = []
    for i in range(10):
        c = moy_carts.create("C%d" % i, root, src="def _draw():\n    pass\n")
        moy_carts.save_cover(c, cover_bytes(i + 1, stripes=i + 20))
        carts.append(c)
    cap = 2 * len(cover_bytes(1, stripes=20))
    monkeypatch.setattr(cover_cache, "_COVER_SRC_MAX_BYTES", cap)
    ws = host_app.build_workstation(root)
    for c in carts:
        _land_cover(ws, c, frames=2000)
    assert ws.covers._src_bytes <= cap
    assert len(ws.covers._src) == len(ws.covers._src_order)


def test_the_picture_cache_is_bounded(tmp_path, monkeypatch):
    from runtime import host_app
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    carts = []
    for i in range(6):
        c = moy_carts.create("C%d" % i, root, src="def _draw():\n    pass\n")
        moy_carts.save_cover(c, cover_bytes(i + 1))
        carts.append(c)
    monkeypatch.setattr(cover_cache, "_COVER_CACHE_MAX_BYTES", 3 * 128 * 128 * 2)
    ws = host_app.build_workstation(root)
    for c in carts:
        _land_cover(ws, c)
    assert ws.covers._bytes <= 3 * 128 * 128 * 2
    assert len(ws.covers._cache) == len(ws.covers._order) <= 3


# -- idle prefetch (#155, P4 glass 2026-07-26) ----------------------------------

def _mk_carts_with_covers(tmp_path, n, with_cover=3):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    out = []
    for i in range(n):
        c = moy_carts.create("C%d" % i, root, src="def _draw():\n    pass\n")
        if i < with_cover:
            moy_carts.save_cover(c, cover_bytes(i + 1))
        out.append(c)
    return root, out


def test_idle_frames_prefetch_cover_files(tmp_path):
    """A cover's read is flash and SIZE-INDEPENDENT. Charged lazily, it lands
    on the frame that first needs the card -- during a shelf drag, a drag
    frame, which is why the picker measured a 577ms worst frame. Idle frames do
    nothing, so they pay it instead.

    The assertion is that files become cached while the console is idle WITHOUT
    any surface having drawn a cover first -- armed from boot (2026-07-27).
    The old first-draw arming kept the cache cold at exactly the moment it was
    needed: p4_clicks measured back_to_desk at 1108ms / open_picker at 824ms,
    both the cover pipeline paying its per-cart loads on the transition's
    painted frames because nothing on the desk or in Settings had armed it."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 4, with_cover=3)
    ws = host_app.build_workstation(root)
    covered = [c for c in ws.carts.all if c.get("path") in
               [x["path"] for x in carts[:3]]]
    assert covered, "fixture carts missing from the store"

    # Armed from boot: idle ticks warm every cart with NO cover draw first.
    assert ws.covers._seen
    for _ in range(200):
        ws.covers.prefetch_tick()
    for c in covered:
        assert ws.covers._src_get(c["path"]) is not None, c["path"]


def test_rescan_rearms_the_prefetch(tmp_path):
    """A store re-scan clears the cover caches (new/changed art), so it must
    also re-arm the idle prefetch -- otherwise every cover goes cold again
    until a surface happens to draw one (the exact failure the boot arming
    exists to prevent)."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 3, with_cover=2)
    ws = host_app.build_workstation(root)
    for _ in range(200):
        ws.covers.prefetch_tick()
    assert ws.covers._seen is False          # exhausted: every cart known
    ws.carts.apply(list(ws.carts.all))    # the re-scan path (create/dup/delete)
    assert ws.covers._seen                   # re-armed...
    want = carts[0]["path"]
    assert ws.covers._src_get(want) is None  # ...and the cache really was cleared
    for _ in range(200):
        ws.covers.prefetch_tick()
    assert ws.covers._src_get(want) is not None   # idle re-warms with no draw


def test_prefetch_prebuilds_visible_covers(tmp_path):
    """Phase 2 (2026-07-27): once every cart's file is warm, idle ticks also
    DECODE the covers the shelf/picker grids' next full draw requests
    (cover_specs' first screenful), so the first click pays a cache hit. The
    idle builds must not leak a dirty re-arm (covers._deferred)."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 4, with_cover=3)
    ws = host_app.build_workstation(root, sys_size=(1024, 600))
    for _ in range(400):
        ws.covers.prefetch_tick()
    assert ws.covers._seen is False          # files AND prebuild fully exhausted
    assert ws.covers._deferred is False     # no repaint re-arm from idle work
    specs = ws.launcher.cover_specs()[:ws.covers._COVER_PREBUILD_PER_GRID]
    want = [(c.get("path"), div) for c, div in specs
            if c.get("path") not in ws.covers._none]
    assert want, "no cover-bearing specs in the fixture"
    for key in want:
        assert key in ws.covers._cache, key


def test_prefetch_makes_a_later_build_touch_no_storage(tmp_path):
    """The point of warming files: the build that follows must not read flash."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 3, with_cover=3)
    ws = host_app.build_workstation(root)
    # By PATH: carts.all also holds the seeded built-ins.
    want = carts[-1]["path"]
    target = next(c for c in ws.carts.all if c.get("path") == want)
    first = next(c for c in ws.carts.all if c.get("path") == carts[0]["path"])
    _land_cover(ws, first)                         # arm
    for _ in range(200):
        ws.covers.prefetch_tick()

    reads = []
    orig = ws.carts_store.load_cover

    def spy(path, _orig=orig):
        reads.append(path)
        return _orig(path)
    ws.carts_store.load_cover = spy
    img = _land_cover(ws, target, HALF)
    assert img is not None
    assert reads == [], "the build re-read the file the prefetch already holds"


def test_prefetch_stops_once_every_cart_is_known(tmp_path):
    """It must not spin: once every cart is either warmed or known cover-less it
    disarms, so an idle console is not walking the cart list forever."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 3, with_cover=1)
    ws = host_app.build_workstation(root)
    covered = next(c for c in ws.carts.all if c.get("path") == carts[0]["path"])
    _land_cover(ws, covered)
    for _ in range(200):
        ws.covers.prefetch_tick()
    assert ws.covers._seen is False


def _tick_to_convergence(ws, limit=400):
    """Tick the idle prefetch until it disarms; returns the tick count."""
    for i in range(1, limit + 1):
        ws.covers.prefetch_tick()
        if not ws.covers._seen:
            return i
    raise AssertionError(
        "prefetch still armed after %d ticks -- an idle console is walking the "
        "cart list forever (#200)" % limit)


def test_prefetch_stops_with_a_file_cache_too_small_for_every_cover(
        tmp_path, monkeypatch):
    """The walk must converge under FILE-cache pressure (#200).

    Its convergence test used to be "is this cart's file cached?", which is a
    different question from "have I warmed this cart", because the cache is
    LRU and byte-capped: warming the tail evicts the head, the head reads as
    unknown again, and the round-robin re-reads a file per idle frame forever.
    Measured before the fix: 2000 ticks, 2000 loads, still armed."""
    from runtime import host_app
    monkeypatch.setattr(cover_cache, "_COVER_SRC_MAX_BYTES", 1024)
    root, carts = _mk_carts_with_covers(tmp_path, 24, with_cover=24)
    ws = host_app.build_workstation(root, sys_size=(1024, 600))
    loads = []
    inner = ws.covers._src_load
    monkeypatch.setattr(ws.covers, "_src_load",
                        lambda p: (loads.append(p), inner(p))[1])

    ticks = _tick_to_convergence(ws)

    # The cap is far below what the covers need, so the head IS evicted...
    assert ws.covers._src_bytes <= 1024 or len(ws.covers._src) == 1
    assert len(ws.covers._src) < len(loads)
    # ...and the arm still costs one pass over the roster plus, at worst, one
    # re-read per prebuilt cover (its file having been evicted behind it).
    budget = len(ws.carts.all) + 2 * cover_cache.CoverCache._COVER_PREBUILD_PER_GRID
    assert len(loads) <= budget, (
        "%d file loads for %d carts in %d ticks" % (
            len(loads), len(ws.carts.all), ticks))


def test_prefetch_stops_with_a_picture_cache_too_small_for_the_visible_set(
        tmp_path, monkeypatch):
    """Same shape one phase later (#200): the PREBUILD walked the grids' specs
    until none was uncached, and the picture cache is capped and LRU, so a
    visible set larger than the cap evicts its own head and that test never
    runs out of work. Every one of those idle ticks also bumped `gen`, which is
    the shelf's band-repaint key."""
    from runtime import host_app
    monkeypatch.setattr(cover_cache, "_COVER_CACHE_MAX_BYTES", 0)
    root, carts = _mk_carts_with_covers(tmp_path, 12, with_cover=12)
    ws = host_app.build_workstation(root, sys_size=(1024, 600))

    _tick_to_convergence(ws)

    assert ws.covers._cache == {}, "the cap should have evicted every build"


def test_prefetch_converges_on_a_machine_too_slow_for_the_build_budget(
        tmp_path):
    """The prebuild must bring its OWN build budget (#200).

    `_built`/`_ms` are the PAINTED frame's time slice; the idle prefetch paints
    nothing, so inheriting a spent one made every prebuild refuse to build while
    still reporting work left -- an unbounded walk whose trigger is wall-clock,
    which is exactly the shape of the one 2026-08-15 failure (it raced a
    concurrent firmware build). A 40x-slower host reproduced it: 45 ticks to
    converge unloaded, never within 2000 loaded."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 3, with_cover=1)
    ws = host_app.build_workstation(root)
    ws.covers._built = True          # a painted frame spent the whole slice
    ws.covers._ms = 10 ** 6

    _tick_to_convergence(ws)

    assert ws.covers._built is True, "the painted frame's budget was clobbered"
    assert ws.covers._ms == 10 ** 6


def test_cover_file_read_budget(tmp_path):
    """Each cart's cover file must be read from storage AT MOST ONCE per session.

    A read is ~58ms on P4 flash (22ms even when the file is absent), so a
    repeat read is a stall the owner feels. Two separate bugs re-read files in
    the RLE era -- a cache keyed on a stamp stashed on a cart dict that did not
    survive a relayout, and per-size keying that missed on every resize -- and
    neither announced itself. This is the budget that would have."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 4, with_cover=4)
    ws = host_app.build_workstation(root)
    mine = [c for c in ws.carts.all
            if c.get("path") in [x["path"] for x in carts]]
    ws.costs.clear()
    for div in (BASE, HALF, BASE):                   # includes a second size
        for c in mine:
            _land_cover(ws, c, div)
    reads = ws.costs.get("cover.blob.read", 0)
    assert reads >= 1, "no file read counted -- is ws.note_cost still wired?"
    assert reads <= len(mine), (
        "read %d files for %d carts across three asks -- the file cache is not "
        "holding" % (reads, len(mine)))


def test_the_cover_file_read_takes_the_storage_gate(tmp_path):
    """A cover file is a STORE read, so it goes through `ws._with_sd`.

    On the T-Deck the gate drains the panel and brackets the session because
    the card shares the panel's SPI host, and an sdspi transaction overlapping
    band queueing from the core-0 feeder is the documented Cache/MMU panic.
    This read is the one most likely to hit that: it is reached from the
    launcher's DRAW and from the idle prefetch, i.e. on frames where the
    previous flush is still in flight.
    """
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 2, with_cover=2)
    ws = host_app.build_workstation(root)

    depth = [0]
    reads = []

    def gate(fn):
        depth[0] += 1
        try:
            return fn()
        finally:
            depth[0] -= 1

    class _Spy:
        """The real store, watching only load_cover's gate depth."""

        def __init__(self, store):
            self._store = store

        def __getattr__(self, name):
            return getattr(self._store, name)

        def load_cover(self, *a, **kw):
            reads.append(depth[0])
            return self._store.load_cover(*a, **kw)

    ws._with_sd = gate
    ws.carts_store = _Spy(ws.carts_store)
    for c in [c for c in ws.carts.all
              if c.get("path") in [x["path"] for x in carts]]:
        _land_cover(ws, c)

    assert reads, "no cover file was read -- retarget this test"
    assert 0 not in reads, "a cover file was read outside the storage gate"


# -- what is NOT a cover, and the idle tick never ends the session ---------------
#
# Both S3 boards once reached the desk on the first boot after a flash, passed a
# few checks and dropped to the REPL within minutes: the idle prefetch asked a
# store-loaded heap for a block it did not have, and the MemoryError came out of
# the frame with nobody waiting on it and the least right of any frame to end a
# session (2026-09-07).

def _starve_the_read(ws, exc):
    """Make the store's cover read fail the way a loaded heap does."""
    def boom(_path):
        raise exc
    ws.carts_store.load_cover = boom


def test_a_cover_the_heap_refuses_does_not_kill_the_prefetch(tmp_path):
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 4, with_cover=3)
    ws = host_app.build_workstation(root)
    real = ws.carts_store.load_cover
    _starve_the_read(ws, MemoryError("memory allocation failed, allocating 16384 bytes"))
    try:
        for _ in range(400):
            ws.covers.prefetch_tick()      # must not raise
    finally:
        ws.carts_store.load_cover = real
    assert ws.covers._seen is False, "the walk must still converge and disarm"
    for c in carts[:3]:
        assert c["path"] in ws.covers._none, "a cover it cannot read is skipped"


def test_a_cover_the_heap_refuses_draws_the_placeholder(tmp_path):
    """`cover_for` returning None is what makes the card fall back to its
    icon/glyph -- the deterministic pre-cover look -- so the shelf still
    paints, it just paints without that one picture."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 2, with_cover=1)
    ws = host_app.build_workstation(root)
    cart = next(c for c in ws.carts.all if c.get("path") == carts[0]["path"])
    real = ws.carts_store.load_cover
    _starve_the_read(ws, MemoryError("memory allocation failed"))
    try:
        for _ in range(8):
            ws.covers._built = False
            assert ws.covers.cover_for(cart) is None
    finally:
        ws.carts_store.load_cover = real
    assert cart["path"] in ws.covers._none


def test_an_old_images_cover_moyimg_is_not_a_cover(tmp_path):
    """The strict reader reaches the shelf (CLAUDE.md, "No store migrations
    until there are users"): a cart still carrying the retired
    images/cover.moyimg has no cover, on a prefetch that keeps walking."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 2, with_cover=0)
    cart = carts[0]
    moy_carts.save_image(cart, "cover", moy_carts.encode_moyimg(
        64, 48, bytes(64 * 48)))
    ws = host_app.build_workstation(root)
    live = next(c for c in ws.carts.all if c.get("path") == cart["path"])
    ws.covers._built = False
    assert ws.covers.cover_for(live) is None
    for _ in range(200):
        ws.covers.prefetch_tick()          # must not raise
    assert live["path"] in ws.covers._none


def test_a_cover_outside_the_profile_draws_the_placeholder(tmp_path):
    """A PNG of the wrong size is ignored -- the cart is never refused for it
    (SPEC.md 3.6) -- and is read once, then known to be no cover."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 1, with_cover=0)
    cart = carts[0]
    wrong = (ROOT / "tests" / "cover_vectors" / "size_127x128.png").read_bytes()
    moy_carts.save_cover(cart, wrong)
    ws = host_app.build_workstation(root)
    live = next(c for c in ws.carts.all if c.get("path") == cart["path"])
    ws.covers._built = False
    assert ws.covers.cover_for(live) is None
    assert live["path"] in ws.covers._none
    assert ws.covers._src_get(live["path"]) is None


def test_a_build_that_cannot_allocate_is_one_missing_cover(tmp_path):
    """The file read fine; it is the build's OWN allocations -- the decode
    scratch and the picture -- that a fragmented heap refuses, and they sit
    outside _CoverJob.step's fence."""
    from runtime import host_app
    root, carts = _mk_carts_with_covers(tmp_path, 2, with_cover=1)
    ws = host_app.build_workstation(root)
    cart = next(c for c in ws.carts.all if c.get("path") == carts[0]["path"])
    real = cover_cache._CoverJob

    def boom(*a, **k):
        raise MemoryError("memory allocation failed, allocating 38912 bytes")

    cover_cache._CoverJob = boom
    try:
        ws.covers._built = False
        assert ws.covers.cover_for(cart) is None
        for _ in range(200):
            ws.covers.prefetch_tick()      # the prebuild walk goes here too
    finally:
        cover_cache._CoverJob = real
    assert cart["path"] in ws.covers._none
    assert ws.covers._jobs == {}, "a failed build must not leave a job behind"


def test_the_frame_loops_idle_branch_swallows_a_surprise(tmp_path):
    """The backstop. Each warmer fences what it expects; this catches what it
    does not, says so ONCE, and hands the frame back."""
    from runtime import host_app
    root, _carts = _mk_carts_with_covers(tmp_path, 2, with_cover=1)
    ws = host_app.build_workstation(root)

    def boom():
        raise RuntimeError("something nobody thought of")

    ws.covers.prefetch_tick = boom
    ws._quiet_frames = 20
    ws._dirty = False
    for _ in range(5):
        ws.frame(0.05)                     # must not raise
    assert ws._idle_warned
