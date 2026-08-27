"""`CoverCache` direct (#209 landing C, docs/console_architecture_2026-08.md).

The cover pipeline is the FRAME-HOT collaborator, so what has to be pinned here
is not "does a cover render" -- `tests/test_cover_pipeline.py` and the goldens
already do that -- but the four things the extraction put at risk and the one
bug it fixes:

  * **the #186 free order.** Payloads live off the gc heap on device, an
    in-flight `_CoverJob` aliases both a runs blob and the shared decode
    scratch, and the invariant is that jobs are dropped BEFORE anything is
    freed. It used to be two hand-copies (`_apply_items` and the diet release);
    it is one body now, so it gets a perturbation test rather than a comment --
    including the reversed order EXECUTED, so the leak it causes is on the
    record rather than asserted about.
  * **the icon cache is invalidated.** It was written at one site and cleared
    at none, so a re-seed or a browser sync kept stale desk icons and a deleted
    cart's Image leaked. And the clear has an ORDER of its own: `slim_carts`
    bakes each icon and then deletes the sprite art, so a clear that ran after
    it would leave a slimmed cart with no icon and nothing to rebuild from.
  * **`gen` has one author and three bump sites**, because eight launcher keys
    and the picker's retained bands pin it.
  * **the frame loop's two touches**, whose semantics (a TIME budget reset, a
    take-once drain) are what keep a deferred cover landing on the next frame
    instead of never or forever.

The tracker is the same single-owner fake `tests/test_moybuf.py` installs: its
`alloc`/`take` hand back REAL memoryviews so the ownership checks fire, and its
`free` refuses a foreign or double free the way the C registry does. Here it
also records, at every free, how many jobs were still in flight -- which is the
order assertion itself, not a proxy for one.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import cover_cache, moy_carts  # noqa: E402
from ws_helpers import build_ws  # noqa: E402


class _Tracker:
    """Single-owner off-heap storage, plus a free-time job census."""

    def __init__(self):
        self.live = {}
        self.freed = 0
        self.jobs_at_free = []
        self.covers = None      # set once the console exists

    def alloc(self, n):
        v = memoryview(bytearray(n))
        self.live[id(v)] = v
        return v

    def take(self, payload):
        v = self.alloc(len(payload))
        v[:] = payload
        return v

    def free(self, buf):
        if self.covers is not None:
            self.jobs_at_free.append(len(self.covers._jobs))
        if not isinstance(buf, memoryview):
            return              # gc-owned fallback storage: no-op
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
    tr = _Tracker()
    monkeypatch.setattr(cover_cache, "_moybuf", tr)
    ws = build_ws(tmp_path)
    tr.covers = ws.covers
    return ws, tr


def _land(ws, cart, w, h, frames=300):
    """Step the per-frame budget until this (cart, w, h) cover lands."""
    for _ in range(frames):
        ws.covers.begin_frame()
        img = ws.covers.cover_for(cart, w, h)
        if img is not None:
            return img
    raise AssertionError("cover never landed")


class _FakeJob:
    """An in-flight decode holding a payload -- what a mid-scroll rescan sees."""

    def __init__(self, packed=None, pix=None):
        self.packed = packed
        self.pix = pix


# -- the #186 free order --------------------------------------------------------

def test_a_rescan_frees_a_blob_an_in_flight_job_was_reading(tmp_path, monkeypatch):
    """The invariant, stated as its outcome: the drop paths clear the jobs
    first, so the alias guard has nothing to decline and the blob really goes.

    The guard exists for the LRU, which evicts under a live job and must leak
    rather than free (test_moybuf pins that half). A wholesale drop is the
    other case: the job is going away in the same breath, so the payload must
    not survive it."""
    cart = _mk_cart(tmp_path)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, cart, 40, 30)
    blob = ws.covers._runs_get(cart["path"])[2]
    assert id(blob) in tr.live
    ws.covers._jobs[("half-built", 1, 1)] = _FakeJob(packed=blob)

    ws.covers.invalidate_all()

    assert id(blob) not in tr.live, (
        "the blob outlived the cart -- the frees ran before the jobs were dropped")
    assert ws.covers._jobs == {}


def test_the_reversed_order_leaks_the_blob(tmp_path, monkeypatch):
    """The perturbation, EXECUTED. This is the body `_drop_payloads` must never
    become, run against the same state as the test above: free first, drop the
    jobs after, and `_free_runs`'s alias guard declines every free while the
    LRU discards the entries anyway. Nothing raises; the memory is simply gone
    for the session, which is why the order needed a test and not a comment."""
    cart = _mk_cart(tmp_path)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, cart, 40, 30)
    covers = ws.covers
    blob = covers._runs_get(cart["path"])[2]
    covers._jobs[("half-built", 1, 1)] = _FakeJob(packed=blob)

    for entry in list(covers._runs.values()):      # <- the mutant: frees FIRST
        covers._free_runs(entry[1][2])
    covers._runs = {}
    covers._runs_order = []
    covers._jobs = {}                              # ...jobs after

    assert id(blob) in tr.live      # leaked, silently -- exactly the #186 defect


def test_no_payload_is_freed_while_a_job_is_still_in_flight(tmp_path, monkeypatch):
    """The order asserted directly rather than through its effect: the tracker
    counts the jobs still registered at every single free. Both drop paths, and
    the scratch buffer as well -- which has NO alias guard, so freeing it under
    a job that is decoding into it is a use-after-free rather than a leak."""
    a = _mk_cart(tmp_path, "CoverA", 5)
    b = _mk_cart(tmp_path, "CoverB", 9)
    ws, tr = _tracked_ws(tmp_path, monkeypatch)
    _land(ws, a, 40, 30)
    _land(ws, b, 40, 30)
    covers = ws.covers
    scratch = covers._buf

    covers._jobs[("half-built", 1, 1)] = _FakeJob(packed=None, pix=scratch)
    covers.diet_release()
    covers._jobs[("half-built", 2, 2)] = _FakeJob(packed=None)
    covers.invalidate_all()

    assert tr.jobs_at_free, "nothing was freed -- the test proves nothing"
    assert set(tr.jobs_at_free) == {0}, (
        "a payload was freed while %d job(s) could still alias it"
        % max(tr.jobs_at_free))


def test_both_drop_paths_are_the_same_body():
    """One mechanism, one implementation: the two callers differ only in how
    much they keep and whether the decode scratch goes with it. Pinned as
    "who assigns `self._jobs`", because a second body would have to."""
    import ast
    src = (ROOT / "runtime" / "cover_cache.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "CoverCache")
    writers = set()
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            targets = getattr(node, "targets", [])
            for tgt in targets:
                if (isinstance(tgt, ast.Attribute) and tgt.attr == "_jobs"
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"):
                    writers.add(fn.name)
    assert writers == {"__init__", "_drop_payloads"}, sorted(writers)
    assert "self._drop_payloads(0)" in src                      # invalidate_all
    assert "self._drop_payloads(_COVER_DIET_KEEP, scratch=True)" in src


# -- the icon cache: the bug this landing fixes ---------------------------------

def _icon_pixels(img):
    return None if img is None else bytes(img.pix)


def _tile0(nibble):
    """A .moygfx blob whose tile 0 -- the icon's SPEC 3.4 default -- is one
    flat colour. A short blob lands in the TOP rows, so eight lines is a tile."""
    return "\n".join([nibble * 8] * 8)


def test_a_rescan_rebuilds_the_icon_from_the_new_art(tmp_path):
    """The rev-2 item 10 bug: `_icon_cache` was written by `icon_sheet_for` and
    cleared by nothing, so an edited cart kept the icon it had before the edit
    for the rest of the session -- on the shelf card and the desk column both.

    Driven end to end through the real re-scan path, because the fix is as much
    about ORDER as about clearing: `_apply_items` clears BEFORE `slim_carts`,
    which is what bakes the icon and then deletes the sprite art."""
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    made = moy_carts.create("Iconic", root, src="def _draw():\n    pass\n")
    moy_carts.save_sprites(made, _tile0("5"))
    ws = build_ws(tmp_path)
    cart = next(c for c in ws._all_carts if c["title"] == "Iconic")
    before = _icon_pixels(ws.covers.icon_sheet_for(cart))
    assert before is not None, "seed cart drew no icon -- the test proves nothing"

    stored = next(c for c in moy_carts.scan(root) if c["title"] == "Iconic")
    moy_carts.save_sprites(stored, _tile0("e"))      # repaint the icon's tile
    ws._apply_items(moy_carts.scan(root))

    fresh = next(c for c in ws._all_carts if c["title"] == "Iconic")
    after = _icon_pixels(ws.covers.icon_sheet_for(fresh))
    assert after is not None, (
        "the icon was cleared with nothing left to rebuild it from -- the clear "
        "ran AFTER slim_carts deleted the art")
    assert after != before, "the stale icon survived the re-scan"


def test_a_deleted_carts_icon_does_not_outlive_it(tmp_path):
    """The leak half of the same bug: the cache is keyed by path, so a cart
    that goes away used to leave its Image live forever."""
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Doomed", root, src="def _draw():\n    pass\n")
    ws = build_ws(tmp_path)
    live = next(c for c in ws._all_carts if c["title"] == "Doomed")
    ws.covers.icon_sheet_for(live)
    key = live.get("path") or live.get("title")
    assert key in ws.covers.icons

    moy_carts.delete(cart)
    ws._apply_items(moy_carts.scan(root))

    assert key not in ws.covers.icons


def test_the_kernel_holds_no_cover_state(tmp_path):
    """One owner. A `ws._cover_*` attribute coming back is a second author for
    something this object already has."""
    ws = build_ws(tmp_path)
    strays = sorted(n for n in vars(ws)
                    if n.startswith("_cover") or n == "_icon_cache"
                    or n == "cover_diet" or n == "_covers_deferred")
    assert strays == []


# -- gen: one author, three bump sites ------------------------------------------

def test_gen_bumps_on_a_build_a_diet_release_and_a_rescan(tmp_path):
    """The three ways the cache can change under a reader that has already
    keyed a retained band on it (launcher_layer's home + picker, eight sites).
    A bump nobody makes is a torn shelf; a bump on something that changed
    nothing is a full band repaint for free."""
    cart = _mk_cart(tmp_path)
    ws = build_ws(tmp_path)
    covers = ws.covers

    at_boot = covers.gen
    _land(ws, cart, 40, 30)                    # _finish
    after_build = covers.gen
    assert after_build > at_boot

    covers.diet_release()
    after_diet = covers.gen
    assert after_diet > after_build

    ws._apply_items(moy_carts.scan(str(tmp_path / "carts")))   # invalidate_all
    assert covers.gen > after_diet


def test_a_definitive_miss_bumps_gen_too(tmp_path):
    """A cart that turns out to have no cover art changes what the card draws
    (glyph, not a pending cover), so the band it sits in has to repaint."""
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    bare = moy_carts.create("Bare", root, src="def _draw():\n    pass\n")
    ws = build_ws(tmp_path)
    cart = next(c for c in ws._all_carts if c["path"] == bare["path"])
    before = ws.covers.gen
    ws.covers.begin_frame()
    assert ws.covers.cover_for(cart, 40, 30) is None
    assert ws.covers.gen > before


def test_gen_has_no_ws_mirror(tmp_path):
    """One author per derived value (architecture doc 9). The consumers read
    `ws.covers.gen`; a mirror on the console would be a second one."""
    ws = build_ws(tmp_path)
    assert not hasattr(ws, "_cover_gen")
    launcher = (ROOT / "runtime" / "launcher_layer.py").read_text(encoding="utf-8")
    windowed = (ROOT / "runtime" / "wm_windowed.py").read_text(encoding="utf-8")
    assert "_cover_gen" not in launcher and "_cover_gen" not in windowed
    assert launcher.count("ws.covers.gen") == 8


# -- the grids reach covers through injected bound methods ----------------------

def test_both_grids_are_wired_to_the_collaborators_bound_method(tmp_path):
    """A card's paint costs ONE call, exactly what it cost with the method on
    the kernel -- no forward, no lookup chain (architecture doc 3e)."""
    ws = build_ws(tmp_path)
    for grid in (ws.launcher, ws.picker):
        assert grid.cover_for.__self__ is ws.covers
        assert grid.cover_for.__func__ is type(ws.covers).cover_for


# -- the frame loop's two touches -----------------------------------------------

def test_begin_frame_resets_a_time_budget_not_a_count(tmp_path, monkeypatch):
    """The budget is milliseconds spent, not builds done (2026-07-27): cheap
    builds all land on the same frame and an expensive one still yields. A
    reset that only cleared the flag would re-admit an unbounded frame."""
    ws = build_ws(tmp_path)
    covers = ws.covers
    covers._built = True
    covers._ms = 999
    covers.begin_frame()
    assert covers._built is False and covers._ms == 0


def test_a_second_build_past_the_budget_defers_and_re_arms_the_gate(tmp_path):
    """Over budget, `cover_for` returns None (the card keeps its glyph) and
    marks the frame deferred, which is what keeps frames coming until the
    remaining covers land."""
    a = _mk_cart(tmp_path, "CoverA", 5)
    b = _mk_cart(tmp_path, "CoverB", 9)
    ws = build_ws(tmp_path)
    covers = ws.covers
    _land(ws, a, 40, 30)                  # warm the runs so a build is cheap
    _land(ws, b, 40, 30)
    covers._cache = {}
    covers._order = []
    covers._pixels = 0

    covers.begin_frame()
    covers._ms = cover_cache._COVER_SLICE_MS + 1    # this frame is spent
    covers._built = True
    assert covers.cover_for(a, 41, 31) is None
    assert covers.take_deferred() is True


def test_take_deferred_is_take_once(tmp_path):
    """The frame tail reads AND clears in one call, so the flag has a single
    author: a draw sets it, the loop takes it, and a second frame does not
    inherit a re-arm nobody asked for."""
    ws = build_ws(tmp_path)
    covers = ws.covers
    assert covers.take_deferred() is False
    covers._deferred = True
    assert covers.take_deferred() is True
    assert covers.take_deferred() is False


def test_the_idle_prebuild_never_re_arms_the_paint_machinery(tmp_path):
    """The prebuild runs on frames that would otherwise do nothing, so a build
    it defers must not turn the NEXT painted frame into two."""
    _mk_cart(tmp_path)
    ws = build_ws(tmp_path)
    covers = ws.covers
    for _ in range(200):
        covers.begin_frame()
        covers.prefetch_tick()
        assert covers.take_deferred() is False, (
            "an idle prefetch frame re-armed the redraw gate")
        if not covers._seen:
            break


def test_the_frame_loop_touches_covers_exactly_twice(tmp_path):
    """The hot rule, pinned in the source: `frame()` binds the collaborator
    once and calls it at its top and its tail. Anything else that grew a
    per-frame reach into covers would show up here."""
    text = (ROOT / "runtime" / "console.py").read_text(encoding="utf-8")
    body = text[text.index("\n    def frame(self, dt):"):]
    body = body[:body.index("\n    def _frame_perf_end(")]
    assert body.count("covers = self.covers") == 1
    assert body.count("covers.begin_frame()") == 1
    assert body.count("covers.take_deferred()") == 1
    assert "self.covers" not in body.replace("covers = self.covers", "")


# -- the diet release (the RAM-tight tier) --------------------------------------

def test_the_diet_release_keeps_the_newest_entries_of_both_lrus(tmp_path):
    """Owner ask 2026-08-03, "I'd rather not have pop-in": the covers on screen
    when PLAY was tapped are the most recently touched, so the exact view the
    kid comes back to is still warm and only cards scrolled in later rebuild."""
    ws = build_ws(tmp_path)
    covers = ws.covers
    keep = cover_cache._COVER_DIET_KEEP
    carts = [_mk_cart(tmp_path, "Cover%d" % i, i + 1) for i in range(keep + 3)]
    ws._apply_items(moy_carts.scan(str(tmp_path / "carts")))
    live = {c["path"]: c for c in ws._all_carts}
    order = [live[c["path"]] for c in carts]
    for cart in order:
        _land(ws, cart, 40, 30)
    assert len(covers._cache) == len(order)

    covers.diet_release()

    assert len(covers._cache) == keep
    assert len(covers._runs) == keep
    newest = [(c["path"], 40, 30) for c in order[-keep:]]
    assert sorted(covers._cache) == sorted(newest)
    assert sorted(covers._runs) == sorted(c["path"] for c in order[-keep:])


def test_the_diet_release_hands_back_the_decode_scratch_and_re_arms(tmp_path):
    """The scratch is 76.8KB of the live set and nothing reads it while a game
    owns the glass; `_seen` re-arms so the walk home warms the shelf again."""
    cart = _mk_cart(tmp_path)
    ws = build_ws(tmp_path)
    covers = ws.covers
    _land(ws, cart, 40, 30)
    covers._seen = False
    covers.diet_release()
    assert covers._buf is None
    assert covers._seen is True


def test_a_run_releases_the_caches_only_on_the_diet_tier(tmp_path):
    """The flag is the board's, the `if` is the shell's: `_start` reads
    `covers.diet` and the P4/host keep their covers warm (windows leave the
    desk visible, and RAM is not scarce there)."""
    cart = _mk_cart(tmp_path)
    ws = build_ws(tmp_path)
    _land(ws, cart, 40, 30)
    live = next(c for c in ws._all_carts if c["path"] == cart["path"])
    ws._open_workspace(live)
    assert ws.covers.diet is False
    warm = ws.covers.gen
    ws._start()
    assert ws.covers.gen == warm            # nothing dropped

    ws.covers.diet = True
    ws._start()
    assert ws.covers.gen > warm             # ...and now it did


# -- invalidate_all's own contract ----------------------------------------------

def test_a_rescan_forgets_that_a_cart_had_no_cover(tmp_path):
    """The cover-less set is a probe saved (22ms of flash per miss), but a
    re-scan is exactly when a cart can have GAINED art -- so it goes with the
    rest. (The diet release keeps it, deliberately: that is RAM pressure, not
    staleness.)"""
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    bare = moy_carts.create("Bare", root, src="def _draw():\n    pass\n")
    ws = build_ws(tmp_path)
    cart = next(c for c in ws._all_carts if c["path"] == bare["path"])
    ws.covers.begin_frame()
    assert ws.covers.cover_for(cart, 40, 30) is None
    assert cart["path"] in ws.covers._none

    ws.covers.diet_release()
    assert cart["path"] in ws.covers._none          # RAM pressure keeps it

    stored = next(c for c in moy_carts.scan(root) if c["path"] == bare["path"])
    moy_carts.save_image(stored, "cover", _cover_text(64, 48, 7))
    ws._apply_items(moy_carts.scan(root))
    assert ws.covers._none == {}

    fresh = next(c for c in ws._all_carts if c["path"] == bare["path"])
    assert _land(ws, fresh, 40, 30) is not None
