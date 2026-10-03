# Map (grep -n a name to jump there):
#   _CoverImage                a decoded cover in RGB565
#   _CoverJob                  a resumable decode of one cover
#   CoverCache                 the cover and icon caches, budgets and warmers
#   CoverCache.cover_for       a cart's cover at a reduction
#   CoverCache.sheet_icon      the icon out of a cart's sprite sheet
#   CoverCache.invalidate_all  drop everything a store rescan could change
#   CoverCache.diet_release    drop the cover pipeline before a cart runs
#   CoverCache.prefetch_tick   warm one cart's cover file
"""The shelf's COVER + ICON pipeline (#209 landing C) -- `Workstation.covers`.

A cart's cover is its `cover.png` (SPEC.md 3.6), read by runtime/cover_png.py
-- the native `moy_png` on a board and in the browser, its Python twin on the
host. What is cached is ONE decoded picture per cover, the BASE: its 128 x 128
pixels as RGB565 in the system canvas's byte order, which the shelf draws at a
whole-number scale through `DeviceCanvas.blit565` (docs/theming_2026-09.md,
P8: one cached base size per cover, an integer upscale at draw, no per-size
variants). Two other decodes of the same file exist, each for one consumer:

  * HALF, 64 x 64, its box-filtered reduction: the Library GRID's interim need,
    for a card whose art slot is under 128 and whose cart names no icon;
  * ICON, 16 x 16, a reduction in palette indices: the desktop icon of a cart
    that names no SPEC.md 3.4 icon (`icon_sheet_for`).

This is the FRAME-HOT collaborator: the grids call `cover_for` once per card
per painted shelf frame through an injected bound method, and `frame()` touches
this object exactly twice -- once at its top (`begin_frame`, the per-frame build
budget) and once at its tail (`take_deferred`, the re-arm that keeps frames
coming until a deferred build lands). Nothing here is reached through a
Workstation forward.

## `gen` has ONE author

`gen` bumps on every change to what a cover cache would draw, and the shelf's
retained-frame keys pin it (launcher_layer's home + picker bands, eight sites)
so a cover landing mid-drag forces a full band repaint rather than a torn one.
It is a plain attribute on THIS object with no `ws` mirror -- the architecture
doc's one-author rule -- so the consumers read `ws.covers.gen`. Three sites bump
it and they are the three ways the cache can change under a reader: a build
finishing or definitively missing (`_finish`), the diet release, and a store
re-scan (`invalidate_all`).

## The #186 free order is ONE body

Cover payloads live OUTSIDE the MP gc heap on device (`moybuf`), so every drop
path has to FREE them -- and an in-flight `_CoverJob` aliases both a source file
and the shared decode scratch. The order is the whole invariant: **jobs are
dropped FIRST, then the payloads are freed**. Reversed, `_free_src`'s
job-alias guard sees a live job, declines the free, and the LRU entry is then
discarded anyway -- the file leaks for the rest of the session; and the scratch
free, which has no guard at all, would hand a decoding job freed memory. Both
drop paths (`invalidate_all` on a store re-scan, `diet_release` before a cart
runs on the RAM-tight tier) go through `_drop_payloads`, which is the only place
that order exists, and `tests/test_cover_cache.py` perturbs it.

## What is read THROUGH `ws`, per call

The cart store, the storage gate (`_with_sd`), the cost meter, the roster
(`ws.carts.all`), the system canvas (whose 565 byte order a cover is decoded
into) and the two grids. None of them is knowable when this object is built:
the store is injected by `wire_workstation_core`, and on the boards `_with_sd`
is swapped for the native SD attach after that. Same rule the sibling
collaborators follow.
"""

try:
    from editors import SpriteSheet
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import SpriteSheet

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff

try:
    import cover_png
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import cover_png

# #186 moy_buf: cover payloads (source files, decoded pictures, the decode
# scratch) live OUTSIDE the MP gc heap on device, so a warm shelf stops taxing
# every GC collect. On the host this is a transparent no-op layer.
try:
    import moybuf as _moybuf
except ImportError:
    from runtime import moybuf as _moybuf

# The three decodes of a cover, named by the reduction each one is (the
# `div` cover_png takes): see the module docstring for who asks for which.
BASE = 1
HALF = 2
ICON = 8
SIDE = cover_png.SIDE

# Decoded pictures, LRU-bounded by count and by bytes. A BASE is 32KB, so the
# byte cap holds 48 of them -- every cover on a full shelf, with the picker's
# beside them.
_COVER_CACHE_MAX_ENTRIES = 64
_COVER_CACHE_MAX_BYTES = 48 * SIDE * SIDE * 2
# Cover FILES, kept so a second decode of the same cover (another size, or a
# rebuild after the diet) reads no storage. A cover is at most 64KB and most are
# a few KB, so this holds every cover a shelf has.
_COVER_SRC_MAX_BYTES = 512 * 1024
# diet: how many newest LRU entries (pictures AND files) SURVIVE the release at
# cart start -- the visible shelf stays warm, the long tail leaves the heap.
_COVER_DIET_KEEP = 6

# How long one _CoverJob.step may run inside a frame, and the per-frame budget
# for cover BUILDS (cover_for). Sized for the warm case (2026-07-27): a
# transition frame arrives with the whole visible set pending, and at 20ms the
# picker's covers land on the FIRST painted frame instead of spreading over
# two or three full repaints. The first build of a frame always proceeds; the
# budget only gates the SECOND onward.
_COVER_SLICE_MS = 20
# Output rows a decode advances between clock checks: a BASE is 128 of them.
_COVER_ROWS = 16

class _CoverImage:
    """A decoded cover: `pix` is w * h RGB565 words in the system canvas's
    byte order (BASE, HALF), drawn by `DeviceCanvas.blit565` -- or w * h
    palette indices (ICON), a sprite like any icon, drawn by `spr`."""

    def __init__(self, w, h, pix):
        self.w = w
        self.h = h
        self.pix = pix
        self.transparent = -1


class _CoverJob:
    """A RESUMABLE decode of one cover at one reduction: `step(t0)` advances
    it _COVER_ROWS output rows at a time until _COVER_SLICE_MS of the frame is
    spent, and cover_for re-steps it on the following frames until `done`. A
    file that turns out not to decode just finishes with img=None -- a corrupt
    cover means no cover, never a crash.

    `src` is the cover file and `pix` the decode scratch it runs in; both are
    ALIASED here until the job is done, which is what the #186 order guards."""

    def __init__(self, src, div, swapped, work):
        self.done = False
        self.img = None
        self.src = src
        self.pix = work
        self.div = div
        if div == ICON:
            fmt = cover_png.INDEX
            pal = cover_png.moy64()
        else:
            fmt = cover_png.RGB565_SW if swapped else cover_png.RGB565
            pal = None
        self.side = SIDE // div
        self.job = cover_png.Job(src, div, fmt, pal, work)
        self.out = bytearray(cover_png.out_size(div, fmt)) if self.job.ok else None
        if not self.job.ok:
            self.done = True

    def step(self, t0):
        """Advance until ~_COVER_SLICE_MS after t0. Sets self.done (and
        self.img) when the decode finishes or the file turns out not to be
        a cover."""
        try:
            while not self.done:
                r = self.job.rows(self.out, _COVER_ROWS)
                if r < 0:
                    self.done = True
                elif r > 0:
                    # #186: the picture moves off the gc heap (take() copies
                    # into moy_buf storage on device; on the host it adopts
                    # `out` unchanged, zero copies).
                    self.img = _CoverImage(self.side, self.side,
                                           _moybuf.take(self.out))
                    self.out = None
                    self.done = True
                elif _ticks_diff(_ticks_ms(), t0) >= _COVER_SLICE_MS:
                    return
        except Exception:  # noqa: BLE001 -- corrupt cover -> no cover
            self.img = None
            self.done = True


class CoverCache:
    """The cover + icon caches, their budgets, their warmers and their frees.

    Held as `ws.covers`. Constructed in `Workstation.__init__` (it takes no
    service), and the two grids are handed `covers.cover_for` as their
    `cover_for` hook right there -- a bound method, so a card's paint costs one
    call, exactly what it cost when the method lived on the kernel."""

    def __init__(self, ws):
        self.ws = ws
        # Bumped on any cover-cache change and on every painted frame that
        # deferred a build (#113: the shelf blit path pins it so a cover
        # landing mid-drag forces a full band repaint; the home's retained
        # stamp keys on it, see take_deferred). ONE author --
        # there is no ws mirror; launcher_layer + the tests read covers.gen.
        self.gen = 0
        # RAM-tight board (T-Deck): drop the cover pipeline when a cart RUN
        # starts (see diet_release) -- set by the S3 backend only; the P4/host
        # keep covers warm (windows leave the desk visible, and RAM is not
        # scarce). The `if` that reads it is kernel policy, in Workstation._start.
        self.diet = False
        # cart path -> desktop-icon sprite Image (or None) from the cart's
        # SHEET. CLEARED by invalidate_all: written at one site and cleared at
        # none, a re-seed or a browser sync kept stale desk icons and a deleted
        # cart's Image never went away.
        self.icons = {}
        self._cache = {}         # (path, div) -> decoded cover (or None)
        self._order = []         # LRU keys (oldest first)
        self._bytes = 0          # bytes of decoded pictures held
        self._jobs = {}          # (path, div) -> the in-flight _CoverJob (at most one)
        self._built = False      # per-frame cover-build budget (see cover_for)
        self._ms = 0             # ms of it spent this frame
        self._none = {}          # paths known to carry no cover
        self._buf = None         # the decode scratch, cover_png.WORK bytes
        self._src = {}           # path -> cover file bytes, LRU-bounded
        self._src_order = []     # LRU keys (oldest first)
        self._src_bytes = 0
        self._deferred = False   # a build was pushed past the budget -> stay dirty
        self._seen = True        # idle prefetch armed (see prefetch_tick); True from
                                 # BOOT: covers must be warm BEFORE the first cover
                                 # surface opens, not after (p4_clicks measured the
                                 # cold pipeline as two ~1s clicks). Latched False once
                                 # every cart is known; re-armed by a store re-scan.
        self._pf_i = 0           # phase-1 cursor: carts warmed this arm (#200)
        self._pb_i = 0           # phase-2 cursor: prebuild specs settled this arm

    # -- the frame loop's two touches ----------------------------------------

    def begin_frame(self):
        """Reset the per-frame cover-build budget -- which is a TIME slice, not
        a count (see cover_for). Called at the TOP of every `frame()`, painted
        or not, and it is one of exactly two calls the loop makes here."""
        self._built = False
        self._ms = 0

    def take_deferred(self):
        """True (once) when a build was pushed past this frame's budget.

        Read at the frame TAIL, after the redraw gate has cleared `_dirty`, so
        the caller re-dirties and the remaining covers land on the following
        frames. Taking it -- read AND clear in one call -- is what keeps the
        flag single-author: the gate that set it is a draw, the drain is the
        loop, and neither has to know the other's ordering.

        Taking it also moves `gen`: a frame that drew a card's placeholder
        while its cover was still due is not a settled frame, and the home's
        retained stamp keys on `gen`. Left alone, the next frame would stamp
        that frame back instead of drawing the grid, no card would ask again,
        and a cover past the idle prebuild's first screenful would stay a
        placeholder for good."""
        if not self._deferred:
            return False
        self._deferred = False
        self.gen += 1
        return True

    # -- what the grids call, once per card per painted frame ----------------

    def cover_for(self, cart, div=BASE):
        """The cart's cover decoded at reduction `div` (BASE, HALF or ICON) --
        or None when the cart carries no cover, or while its decode is still
        in flight (the card draws its icon or glyph until it lands). Cached
        per (path, div); read through the store so a slimmed cart (#66) never
        rehydrates, and cleared with the icon cache on a store re-scan.

        Decodes are TIME-SLICED and BUDGETED: a miss starts a resumable
        _CoverJob and the frame advances it by at most ~_COVER_SLICE_MS of
        work, so covers pop in over frames and no frame freezes; frame()
        re-arms the redraw gate while any decode is pending."""
        ws = self.ws
        path = cart.get("path")
        if path is None or ws.carts_store is None:
            return None
        self._seen = True     # re-arm the idle prefetch (it latches off once
                              # every cart is known; a surface asking again is
                              # the cheap signal to re-check)
        self._pb_i = 0        # the prebuild set is selection-keyed, so a
                              # surface asking can have changed it. The phase-1
                              # cursor is not reset here: files are keyed by
                              # path alone, so only a cache DROP makes a warmed
                              # cart worth re-reading (#200).
        if path in self._none:       # known cover-less: never re-probe
            return None
        key = (path, div)
        cache = self._cache
        if key in cache:
            order = self._order
            try:
                order.remove(key)
            except ValueError:
                pass
            order.append(key)
            return cache[key]
        # Per-frame build budget: a TIME slice. Cheap builds all land on the
        # same frame, an expensive one still yields.
        if self._built and self._ms >= _COVER_SLICE_MS:
            self._deferred = True
            return None
        self._built = True
        t0 = _ticks_ms()
        try:
            return self._build(path, key, div, t0)
        except (MemoryError, ValueError, OSError) as exc:
            # The build's own allocations -- the decode scratch and the
            # picture -- are outside _CoverJob.step's fence, and they are the
            # ones a fragmented S3 heap refuses (#66). Same answer as an
            # unreadable file: no cover for this cart this session, placeholder
            # drawn, loop alive. `_finish` still runs so the key caches the
            # miss rather than retrying it every frame.
            print("Moybyte cover build failed:", path, exc)
            self._none[path] = True
            self._spend(t0)
            self._jobs.pop(key, None)
            return self._finish(key, None)

    def _build(self, path, key, div, t0):
        """One step of a cover build, from `cover_for`'s budget gate. Split out
        so the fence above wraps the whole of it, allocations included.

        ONE decode is in flight at a time, because there is one scratch: a
        decode that spans frames owns it until it finishes. So whoever asks
        next finishes THAT one first -- its card may have scrolled away, and
        nothing else would ever step it -- and starts its own only with budget
        left over."""
        jobs = self._jobs
        for other in list(jobs):
            if other == key:
                continue
            job = jobs[other]
            if not job.done:
                job.step(t0)
            self._spend(t0)
            if not job.done:
                self._deferred = True
                return None
            jobs.pop(other)
            self._finish(other, job.img)
            if self._ms >= _COVER_SLICE_MS:
                self._deferred = True
                return None
        job = jobs.get(key)
        if job is None:
            # The cover FILE still in RAM? Then this decode touches no storage
            # at all. Keyed by PATH alone, deliberately: validating it would
            # mean reading the file, which is the cost this cache exists to
            # avoid -- so it is good for the session and dropped wholesale on a
            # store re-scan, which is what a create/edit/delete goes through.
            src = self._src_get(path)
            if src is None:
                src = self._src_load(path)
                if src is None:
                    self._spend(t0)
                    return self._finish(key, None)
            native = cover_png.native() is not None
            if native and self._buf is None:
                self._buf = _moybuf.alloc(cover_png.WORK)
            self.ws.note_cost("cover.build")     # one decode of one size
            job = _CoverJob(src, div, self._swapped(), self._buf if native else None)
            if not job.done:
                jobs[key] = job
        if not job.done:
            job.step(t0)
        self._spend(t0)
        if not job.done:
            self._deferred = True    # keep frames coming until it lands
            return None
        jobs.pop(key, None)
        if job.img is None:
            # A file that read but does not decode is no cover, at ANY size.
            self._none[path] = True
        return self._finish(key, job.img)

    def _swapped(self):
        """The system canvas's 565 byte order, which BASE and HALF are
        decoded into so the draw is a straight copy."""
        cv = getattr(self.ws, "sys_canvas", None)
        return getattr(cv, "swapped565", True)

    def icon_sheet_for(self, cart):
        """A sprite Image for a cart's desktop icon, or None when the cart has
        no art (then the type glyph is drawn).

        A cart whose manifest names no "icon" (SPEC.md 3.4) is drawn by its
        COVER where it has one -- the ICON reduction, built through the same
        budgeted decode as every cover, so until it lands (or for a cart with
        no cover) this answers the sheet's own icon, the pre-cover look."""
        if cart.get("path") is None:                # a pinned pseudo tile (Make/New):
            return None                             # no cart art -> draw its type glyph
        if not cart.get("icon"):
            img = self.cover_for(cart, ICON)
            if img is not None:
                return img
        return self.sheet_icon(cart)

    def sheet_icon(self, cart):
        """The icon out of the cart's SPRITE SHEET, cached per cart path so the
        grid doesn't rebuild a sheet every frame -- and baked by
        `CartManager.slim` while the sheet is still in RAM.

        The tiles come from the manifest's "icon" (SPEC.md 3.4) -- [tile, w, h],
        or a bare tile id for 1x1 -- falling back to tile 0. The field has to be
        explicit rather than a plain tile-0 rule because tile 0 is BLANK by
        convention across the whole PICO-8 catalogue (it is why map cell 00 means
        empty), so tile 0 alone draws nothing for every converted cart."""
        if cart.get("path") is None:
            return None
        key = cart.get("path") or cart.get("title")
        cache = self.icons
        if key in cache:
            return cache[key]
        n, tw, th = cart.get("icon") or (0, 1, 1)
        # ONE TILE, not the whole sheet: icon_from_hex carries the blank-sheet
        # test and the SPEC 3.4 out-of-range fallback, so the picture is
        # unchanged -- the shell goldens pin it.
        try:
            img = SpriteSheet.icon_from_hex(cart.get("sprites"), n, tw, th,
                                            cols=16, rows=32)
        except Exception:  # noqa: BLE001 -- a bad sheet just gets the type glyph
            img = None
        cache[key] = img
        return img

    # -- lifecycle: the #186 free order, ONE body ----------------------------

    def invalidate_all(self):
        """Drop EVERYTHING a store re-scan could have changed under us.

        The cover half of `CartManager.apply`: a create/duplicate/delete,
        a re-seed or a browser sync can carry a new or changed cover, can
        change a cart's icon tile, and can take a cart away entirely -- so the
        pictures, the cover files, the cover-less set and the ICON cache all go.

        The icon cache is the one this used to miss: it was written by
        `sheet_icon` and cleared nowhere, so a re-scan kept drawing the icon
        a cart had before it was edited, and a deleted cart's Image stayed live
        forever (docs/history/console_architecture_2026-08.md rev-2 item 10)."""
        self._drop_payloads(0)
        self._none = {}
        self._prune_icons()
        self.gen += 1             # re-arm the idle prefetch: new/changed carts
        self._seen = True         # should warm before their surface opens
        self._pf_i = 0            # a fresh pass over the (possibly new) roster
        self._pb_i = 0

    def _prune_icons(self):
        """Drop every desktop icon that CAN be rebuilt, and every icon whose
        cart has gone away. What survives is the one case a blanket clear would
        lose for the rest of the session: a cart still on the shelf that has
        already been slimmed (#66), whose sprite art is no longer in RAM.

        The predicate is `lazy`, which is exactly the flag `CartManager.slim` sets
        after it bakes an icon and deletes the art -- so "will something re-bake
        this?" and "will this survive the prune?" are answers to the same
        question and cannot drift apart. A slimmed cart's icon also cannot have
        gone stale: a real edit arrives as a fresh FAT scan, which is the branch
        that drops."""
        keep = {}
        for cart in self.ws.carts.all:
            if not cart.get("lazy"):
                continue          # its art is in hand -- a fresh bake is possible
            key = cart.get("path") or cart.get("title")
            if key in self.icons:
                keep[key] = self.icons[key]
        self.icons = keep

    def _drop_payloads(self, keep, scratch=False):
        """THE #186 FREE ORDER, and the only copy of it.

        In-flight jobs go FIRST: a `_CoverJob` aliases the cover file it
        decodes and the shared decode scratch. Free before dropping them and
        `_free_src`'s alias guard declines the free while the LRU discards the
        entry anyway -- the file is then leaked for the session -- and the
        scratch, which has no guard, is handed to a job that is still writing
        into it.

        `keep` is how many NEWEST entries of each LRU survive (0 = everything
        goes). `scratch` frees the decode scratch as well, which is a
        RAM-release intent (diet_release) rather than an invalidation one: a
        re-scan wants the scratch kept, since nothing about it went stale."""
        self._jobs = {}                       # <- FIRST. See above.
        order = self._order
        cache = self._cache
        while len(order) > keep:
            k = order.pop(0)
            img = cache.pop(k, None)
            if img is not None:
                self._bytes -= len(img.pix)
                self._free_img(img)           # #186: pix + bakes off-heap
        if not order:
            self._bytes = 0
        sorder = self._src_order
        src = self._src
        while len(sorder) > keep:
            k = sorder.pop(0)
            gone = src.pop(k, None)
            if gone is not None:
                self._src_bytes -= len(gone)
                self._free_src(gone)
        if not sorder:
            self._src_bytes = 0
        if scratch and self._buf is not None:
            _moybuf.free(self._buf)           # the decode scratch
            self._buf = None                  # realloc'd on demand

    def diet_release(self):
        """Drop the whole cover pipeline before a cart runs (cover_diet tier).

        The 2026-08-03 census: on the T-Deck the shelf's caches were the
        live-set staircase, none of it read while a game owns the glass, yet
        every GC pause marks it (114ms at a 638KB live set vs 243ms at 1427KB,
        measured on glass). Covers are regenerable by design, so the trade is:
        halve the mid-play GC pause, pay a shelf pop-in on the way back home
        (_seen re-arms the idle prefetch). _none stays: knowing a cart HAS no
        cover is a probe saved, not RAM.

        KEEPS the newest _COVER_DIET_KEEP entries of both LRUs (owner ask
        2026-08-03, "I'd rather not have pop-in"): the covers on screen when
        PLAY was tapped are the most recently touched, so the exact view the
        kid returns to is still warm and only cards scrolled into view later
        rebuild -- their normal cold path, prefetch-warmed."""
        # The #186 order (jobs before frees) lives in _drop_payloads, which
        # invalidate_all shares -- this path just keeps a few entries and
        # hands back the decode scratch as well.
        self._drop_payloads(_COVER_DIET_KEEP, scratch=True)
        self.gen += 1             # any shelf band repaints from scratch
        self._seen = True         # re-arm the idle prefetch for the return home
        self._pf_i = 0            # the dropped tail has to be walked again
        self._pb_i = 0

    # -- the idle warmers (frame()'s quiet branch) ---------------------------

    def prefetch_tick(self):
        """Warm ONE not-yet-known cart's cover file. Called only from the idle
        branch of frame(), i.e. on a frame that would otherwise do nothing.

        Reading a cover is flash I/O, charged to whichever frame first needs
        the card -- on a shelf that scrolls, a DRAG frame. It cannot be made
        much cheaper, so it moves instead -- same reasoning as the bar strip:
        spend it where nobody is waiting.

        ARMED FROM BOOT (2026-07-27), not from the first cover draw. The old
        gate ("only while a surface is showing covers") kept the cache cold at
        exactly the moment it was needed: tools/p4_clicks.py measured
        back_to_desk at 1108ms and open_picker at 824ms, both of which were the
        cover pipeline paying its loads ON the transition's painted frames
        because nothing had armed the prefetch from the desk or Settings.
        Warming from boot moves all of it into the first few idle seconds of
        the session. The trade, accepted: an idle EDITOR warms the cache too,
        so the first input after a >2-quiet-frame pause can land behind one
        in-flight flash read, at most once per cart per session. A RUNNING game
        is never affected: it animates, so the idle branch never executes.

        ONE PASS PER ARM, both phases (#200). Both cursors advance
        monotonically and neither is rewound by a cache miss, so the arm
        terminates in at most (roster + prebuild specs) ticks whatever the
        caches do -- they both evict, and a walk that re-read whatever was
        evicted re-read a file per idle frame forever (measured: 2000 ticks,
        2000 loads, latch never cleared)."""
        ws = self.ws
        carts = ws.carts.all
        if not self._seen or ws.carts_store is None or not carts:
            return
        n = len(carts)
        i = self._pf_i
        while i < n:
            cart = carts[i]
            i += 1
            path = cart.get("path")
            if (not path or path in self._none
                    or self._src_get(path) is not None):
                continue
            self._pf_i = i
            self._src_load(path)
            return
        self._pf_i = i
        # Every cart's file is known. Phase 2 (2026-07-27): pre-DECODE the
        # covers the shelf/picker grids' next full draw will request, so the
        # first click pays a cache hit instead of a decode, on a frame nobody
        # is watching.
        if self._prebuild_tick():
            return
        # Nothing left to warm: stop until something asks for covers again (a
        # re-scan clears the caches and cover_for re-arms the flag).
        self._seen = False

    # The prebuild covers the first screenful per grid -- what a fresh session's
    # click reveals. Scroll-ins beyond it decode lazily. Deliberately NOT every
    # item: the picture cache is LRU and capped, and prebuilding two full grids
    # would evict the head cards (the ones the click shows) for the tail.
    _COVER_PREBUILD_PER_GRID = 12

    def _prebuild_tick(self):
        """Decode ONE pending cover from the grids' cover_specs (the exact
        (cart, div) set their next full draw requests). Returns True while
        there is (or may be) work left, False when the visible set is settled.

        Runs on idle frames only (the caller), so it must not re-arm the paint
        machinery: cover_for sets _deferred when a build defers, which would
        turn the NEXT painted frame into two -- save/restore it. For the same
        reason it brings its OWN build budget: `_built`/`_ms` are the PAINTED
        frame's time slice, reset by begin_frame, and inheriting a spent one
        made this loop refuse every build while still reporting work left (#200
        -- a walk that converged in 45 ticks on an idle host never converged at
        all on one 40x loaded, which is what the flake was).

        `_pb_i` is a cursor over the spec positions, not a "still uncached" test:
        the picture LRU is capped, so a set larger than the cap evicts its own
        head and an uncached test never runs out of work."""
        ws = self.ws
        grids = (ws.launcher, ws.picker)
        cap = self._COVER_PREBUILD_PER_GRID
        at = self._pb_i
        pos = 0
        for grid in grids:
            specs = getattr(grid, "cover_specs", None)
            if specs is None:
                continue
            n = 0
            for cart, div in specs():
                if n >= cap:
                    break
                n += 1
                here = pos
                pos += 1
                if here < at:
                    continue
                path = cart.get("path")
                key = (path, div)
                if not path or path in self._none or key in self._cache:
                    self._pb_i = pos
                    continue
                deferred = self._deferred
                built = self._built
                ms = self._ms
                self._built = False
                self._ms = 0
                try:
                    self.cover_for(cart, div)
                finally:
                    self._deferred = deferred
                    self._built = built
                    self._ms = ms
                # A job still in flight is re-stepped next tick (its row cursor
                # only moves forward); anything else is settled.
                self._pb_i = here if key in self._jobs else pos
                return True
        self._pb_i = pos
        return False

    # -- the file cache: the size-independent half of a build ----------------

    def _src_load(self, path):
        """Read this cart's cover file into the file cache; returns its bytes,
        or None for a cart with no cover (or one that is not in the profile's
        shape at all).

        A cover-less cart is remembered per PATH: probing for a file that is not
        there costs 22ms on the P4's flash (a listdir measured the same, so
        there is no cheaper existence test), and most carts have no cover."""
        ws = self.ws
        store = ws.carts_store
        loader = getattr(store, "load_cover", None)
        ws.note_cost("cover.blob.read")
        # Through the storage gate like every other store read here: this fires
        # from the launcher's draw and the idle prefetch, i.e. around a repaint,
        # where the T-Deck has a flush in flight over the SPI host its card
        # shares -- an sdspi transaction there is the documented hang.
        #
        # AND IT CANNOT RAISE. Reading a cover is flash I/O, so it can fail for
        # reasons that are nothing to do with this cart -- a card pulled, a heap
        # with no room left -- and it runs from the idle prefetch, where a
        # MemoryError escaping took both S3 boards to the REPL a few minutes
        # after a flash (2026-09-07). A cover that cannot be read is treated as
        # one that is not there, for this session: the card draws its icon or
        # glyph, `_none` stops it being re-probed every idle frame, and a store
        # re-scan clears that and tries again.
        try:
            data = ws._with_sd(
                lambda: loader(path)) if loader is not None else None
            if data and cover_png.structure(data) is None:
                data = None
        except (MemoryError, ValueError, OSError) as exc:
            print("Moybyte cover unreadable:", path, exc)
            self._none[path] = True
            return None
        if not data:
            self._none[path] = True
            return None
        # #186: the file moves off the gc heap; every reader (the native decode,
        # len, slicing) reads a memoryview identically; eviction frees it.
        data = _moybuf.take(data)
        self._src_put(path, data)
        return data

    def _src_get(self, path):
        """This cart's cover file bytes, or None."""
        data = self._src.get(path)
        if data is None:
            return None
        order = self._src_order
        try:
            order.remove(path)
        except ValueError:
            pass
        order.append(path)
        return data

    def _src_put(self, path, data):
        """Cache a cover file, LRU-bounded by bytes."""
        cache = self._src
        order = self._src_order
        old = cache.get(path)
        if old is not None:
            self._src_bytes -= len(old)
            self._free_src(old)          # #186: a replaced file returns
            try:
                order.remove(path)
            except ValueError:
                pass
        cache[path] = data
        order.append(path)
        self._src_bytes += len(data)
        while order and self._src_bytes > _COVER_SRC_MAX_BYTES:
            drop = order.pop(0)
            if drop == path:              # never evict the one just stored
                order.insert(0, drop)
                break
            gone = cache.pop(drop, None)
            if gone is not None:
                self._src_bytes -= len(gone)
                self._free_src(gone)      # #186 (job-alias guarded)

    # -- build bookkeeping ---------------------------------------------------

    def _spend(self, t0):
        """Charge this frame's cover budget (see cover_for)."""
        self._ms += _ticks_diff(_ticks_ms(), t0)

    def _finish(self, key, img):
        """Insert a finished cover (or a definitive miss) into the bounded
        LRU cache and return it."""
        cache = self._cache
        cache[key] = img
        order = self._order
        order.append(key)
        self.gen += 1
        if img is not None:
            self._bytes += len(img.pix)
        while (len(order) > _COVER_CACHE_MAX_ENTRIES
               or self._bytes > _COVER_CACHE_MAX_BYTES):
            old_key = order.pop(0)
            old_img = cache.pop(old_key, None)
            if old_img is not None:
                self._bytes -= len(old_img.pix)
                self._free_img(old_img)   # #186: pix + bakes off-heap
        return img

    # -- #186 frees ----------------------------------------------------------

    def _free_src(self, data):
        """#186: return an evicted cover file to off-heap storage -- unless an
        in-flight _CoverJob still decodes from it (the LRU knows nothing about
        jobs; leaking one file beats a use-after-free). No-op for gc-heap
        payloads (host / fallback)."""
        for job in self._jobs.values():
            if job.src is data:
                return
        _moybuf.free(data)

    def _free_img(self, img):
        """#186: release an evicted picture's off-heap payloads -- its pixels
        plus any RGB565 bake the device canvas stamped on an ICON drawn as a
        sprite (_rgb / the variant dict). Alias-safe: the hot _rgb slot SHARES
        a variant entry's buffer, so each distinct buffer frees once. Fields
        are nulled afterwards, so if anything ever drew an evicted cover it
        would raise loudly instead of blitting freed memory."""
        if img is None:
            return
        freed = []
        for name in ("pix", "_rgb_i", "_rgb"):
            b = getattr(img, name, None)
            if isinstance(b, memoryview):
                dup = False
                for s in freed:
                    if b is s:
                        dup = True
                        break
                if not dup:
                    freed.append(b)
                    _moybuf.free(b)
            setattr(img, name, None)
        var = getattr(img, "_rgb_variants", None)
        if var:
            for v in var.values():
                b = v[0]
                if isinstance(b, memoryview):
                    dup = False
                    for s in freed:
                        if b is s:
                            dup = True
                            break
                    if not dup:
                        freed.append(b)
                        _moybuf.free(b)
            var.clear()
