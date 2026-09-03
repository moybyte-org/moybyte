"""The shelf's COVER + ICON pipeline (#209 landing C) -- `Workstation.covers`.

`_CoverImage`, `_CoverJob` and every verb that builds, caches, budgets, warms
and releases a Library card's art -- plus the cart DESKTOP-ICON cache, which
joins them here because it is the same lifetime and had no invalidation at all
(see `invalidate_all`). This is the FRAME-HOT collaborator: the grids call
`cover_for` once per card per painted shelf frame through an injected bound
method, and `frame()` touches this object exactly twice -- once at its top
(`begin_frame`, the per-frame build budget) and once at its tail
(`take_deferred`, the re-arm that keeps frames coming until a deferred build
lands). Nothing here is reached through a Workstation forward.

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
path has to FREE them -- and an in-flight `_CoverJob` aliases both a runs blob
and the shared decode scratch. The order is the whole invariant: **jobs are
dropped FIRST, then the payloads are freed**. Reversed, `_free_runs`'s
job-alias guard sees a live job, declines the free, and the LRU entry is then
discarded anyway -- the blob leaks for the rest of the session; and the
scratch free, which has no guard at all, would hand a decoding job freed
memory. Both drop paths (`invalidate_all` on a store re-scan, `diet_release`
before a cart runs on the RAM-tight tier) go through `_drop_payloads`, which is
the only place that order exists, and `tests/test_cover_cache.py` perturbs it.

## What is read THROUGH `ws`, per call

The cart store, the storage gate (`_with_sd`), the cost meter, the roster
(`ws.carts.all`) and
the two grids. None of them is knowable when this object is built: the store is
injected by `wire_workstation_core`, and on the boards `_with_sd` is swapped for
the native SD attach after that. Same rule the sibling collaborators follow.
"""

try:
    from editors import SpriteSheet
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import SpriteSheet

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff

# Derived Library covers are sizeable on the desktop tier and DeviceCanvas adds a
# 2-byte RGB565 bake to each cached indexed image.  Keep the cache comfortably
# bounded for the P4 heap while retaining enough variants for the root Library,
# the Make window, and selected/unselected card heights at the same time.
_COVER_CACHE_MAX_ENTRIES = 64
_COVER_CACHE_MAX_PIXELS = 768 * 1024
# Parsed cover RUNS, kept so a relayout neither re-reads nor re-parses.
# Measured per cover on P4 glass: read the blob 46.9ms + parse it 17.1ms, against
# a native decode 0.89ms + crop 0.76ms. So the expensive half is the I/O and the
# interpreted base64/RLE parse, and THAT is what must be cached -- not the
# decoded bitmap (a first attempt cached that instead: 77KB per cover, which at
# this board's ~470KB/s flash cost 164ms to reload and made things worse).
# Runs are ~15KB, so this holds far more covers in less RAM.
_COVER_RUNS_MAX_BYTES = 512 * 1024
# diet: how many newest LRU entries (covers AND run blobs) SURVIVE the
# release at cart start -- the visible shelf stays warm, the long tail leaves
# the heap. ~6 covers x (8-24KB bake + ~15KB runs) ~= 150KB retained.
_COVER_DIET_KEEP = 6


class _CoverImage:
    """Minimal blittable for a card's COVER art (visual identity v1 Section
    11.4): both canvas backends' spr() read only .w/.h/.pix/.transparent, the
    same contract as editors._SheetSprite."""

    def __init__(self, w, h, pix):
        self.w = w
        self.h = h
        self.pix = pix
        self.transparent = -1
        # Covers are opaque MOY64 bitmaps, just like Paint images.  This marker
        # selects DeviceCanvas's native blit_indices bake instead of the generic
        # per-pixel path.
        self._paint = True


# How long one _CoverJob.step may run inside a frame, and the per-frame budget
# for cover BUILDS (cover_for). Sized for the warm case (2026-07-27): with the
# runs prefetched a native build is ~2ms, and a transition frame arrives with
# the whole visible set pending -- at 8ms the picker's ~9 covers spread over 2-3
# painted frames (each a full ~190ms repaint via the _deferred re-arm),
# at 20ms they land on the FIRST frame (p4_clicks: open_picker 376 -> ~190ms).
# The cold path is unshaped by this constant: the first build of a frame always
# proceeds (one ~50ms blob load), and the budget only gates the SECOND onward,
# which a 20ms ceiling still refuses after any load. Python-fallback jobs (host
# without moy_gfx) just step in chunkier slices -- the host is fast.
_COVER_SLICE_MS = 20
# Per-palette-index run templates for the RLE fill (built lazily, 64 x 255B):
# a run decodes as ONE C-level slice copy instead of a per-pixel loop.
_COVER_RUNS = None

# The native indexed crop, when this build has one (device only; the host keeps
# the Python loop and both produce identical bytes).
try:
    import moy_gfx as _moy_gfx
    _CROP_INDEX = getattr(_moy_gfx, "crop_index", None)
    _DECODE_RUNS = getattr(_moy_gfx, "decode_runs", None)
except ImportError:
    _CROP_INDEX = None
    _DECODE_RUNS = None

# #186 moy_buf: cover payloads (parsed runs, cover bitmaps, the decode
# scratch) live OUTSIDE the MP gc heap on device, so a warm shelf stops
# taxing every GC collect. On the host this is a transparent no-op layer.
try:
    import moybuf as _moybuf
except ImportError:
    from runtime import moybuf as _moybuf


class _CoverJob:
    """A RESUMABLE cover build. Decoding a 320x240 RLE cover + cover-cropping
    it to the card in one go measured 0.5-1.7s per cover on the T-Deck (#66)
    -- one frozen frame per cover even under the one-build-per-frame budget.
    So the build is a little state machine instead: step(t0) advances the
    decode (RLE runs -> the full indexed bitmap, slice-assign fills) and then
    the crop (nearest-sample rows via a precomputed column map) until
    _COVER_SLICE_MS of the frame is spent, and cover_for re-steps it on the
    following frames until `done`. Any malformed input just finishes with
    img=None -- a corrupt cover means no cover, never a crash."""

    def __init__(self, runs, w, h, src=None, buf=None):
        global _COVER_RUNS
        self.done = False
        self.img = None
        self.w = int(w)
        self.h = int(h)
        self.sw, self.sh, self.packed = runs
        # `src` is an already-decoded source bitmap, letting a caller skip
        # straight to the crop. Unused by the console now that the decode itself
        # is native (~0.9ms) and the expensive part turned out to be reading and
        # PARSING the blob -- which CoverCache caches as runs instead -- but
        # kept because it is the natural seam and the crop tests drive it.
        total = self.sw * self.sh
        if src is not None and len(src) == total:
            self.pix = src
            self.pos = total                    # decode phase already complete
            self.i = len(self.packed)
        else:
            # `buf` is a REUSED scratch buffer. A source bitmap is ~77KB, and
            # allocating one per build cost 116ms on P4 glass -- a big
            # MicroPython allocation whose gc collect dwarfed the 0.9ms decode it
            # was for. Only safe with the native decode, which finishes in a
            # single step: the interpreted fallback keeps partial state in `pix`
            # across frames, so it must own its buffer.
            if buf is not None and len(buf) >= total:
                self.pix = buf
            else:
                self.pix = bytearray(total)
            self.pos = 0              # decode write cursor (pixels)
            self.i = 0                # decode read cursor (packed bytes)
        self.out = None               # crop dest (created when decode ends)
        self.dy = 0                   # crop row cursor
        self.xmap = None
        if _COVER_RUNS is None:
            _COVER_RUNS = tuple(bytes((v,)) * 255 for v in range(64))

    def step(self, t0):
        """Advance until ~_COVER_SLICE_MS after t0. Sets self.done (and
        self.img) when the build finishes or the input turns out corrupt."""
        try:
            self._step(t0)
        except Exception:  # noqa: BLE001 -- corrupt cover -> no cover
            self.img = None
            self.done = True

    def _step(self, t0):
        packed = self.packed
        n = len(packed)
        pix = self.pix
        total = self.sw * self.sh
        # NATIVE decode (#155): the entire RLE stream in ONE C call. This is what
        # the whole time-slicing machinery was for -- interpreted, a 320x240 cover
        # cost 0.5-1.7s. The slow Python walk below stays as the host path and the
        # fallback, and produces identical bytes.
        if self.i < n and _DECODE_RUNS is not None:
            got = _DECODE_RUNS(pix, total, packed)
            if got != total:
                self.img = None
                self.done = True
                return
            self.i = n
            self.pos = total
        while self.i < n:
            i = self.i
            pos = self.pos
            for _ in range(128):      # a batch of runs between clock checks
                if i >= n:
                    break
                count = packed[i]
                value = packed[i + 1]
                if count < 1 or value > 63 or pos + count > total:
                    self.img = None
                    self.done = True
                    return
                if count == 1:
                    pix[pos] = value
                else:
                    pix[pos:pos + count] = _COVER_RUNS[value][:count]
                pos += count
                i += 2
            self.i = i
            self.pos = pos
            if _ticks_diff(_ticks_ms(), t0) >= _COVER_SLICE_MS:
                return
        if self.pos != total:         # short stream: corrupt -> no cover
            self.img = None
            self.done = True
            return
        # -- crop phase: match the card's aspect with a centered source
        # window, then nearest-sample it to exactly (w, h), a row per check.
        w, h, sw, sh = self.w, self.h, self.sw, self.sh
        if self.xmap is None:
            cw_ = min(sw, sh * w // h) or 1
            ch_ = min(sh, sw * h // w) or 1
            ox = (sw - cw_) // 2
            self._ox = ox
            self._oy = (sh - ch_) // 2
            self._cw = cw_
            self._ch = ch_
            self.xmap = [ox + dx * cw_ // w for dx in range(w)]
            self.out = bytearray(w * h)
            # NATIVE crop (#155): the whole window in ONE C call. With the decode
            # now cached, the crop is what a relayout pays -- ~20k nearest
            # samples per card, which is 20-40ms of interpreted loop but well
            # under a millisecond in C. Byte-identical by construction (same
            # integer floors, same source window); pinned by test_cover_pipeline.
            if _CROP_INDEX is not None:
                try:
                    if _CROP_INDEX(self.out, w, h, pix, sw, sh,
                                   ox, self._oy, cw_, ch_):
                        self.dy = h
                        # #186: the finished card bitmap moves off the gc heap
                        # (take() copies into moy_buf storage on device; on the
                        # host it adopts `out` unchanged, zero copies).
                        self.img = _CoverImage(w, h, _moybuf.take(self.out))
                        self.done = True
                        return
                except Exception:  # noqa: BLE001 -- any surprise -> Python loop
                    pass
        xmap = self.xmap
        out = self.out
        while self.dy < h:
            dy = self.dy
            base = (self._oy + dy * self._ch // h) * sw
            di = dy * w
            for dx in range(w):
                out[di] = pix[base + xmap[dx]]
                di += 1
            self.dy = dy + 1
            if _ticks_diff(_ticks_ms(), t0) >= _COVER_SLICE_MS:
                return
        self.img = _CoverImage(w, h, _moybuf.take(out))   # #186: off the gc heap
        self.done = True



class CoverCache:
    """The cover + icon caches, their budgets, their warmers and their frees.

    Held as `ws.covers`. Constructed in `Workstation.__init__` (it takes no
    service), and the two grids are handed `covers.cover_for` as their
    `cover_for` hook right there -- a bound method, so a card's paint costs one
    call, exactly what it cost when the method lived on the kernel."""

    def __init__(self, ws):
        self.ws = ws
        # Bumped on any cover-cache change (#113: the shelf blit path pins it so
        # a cover landing mid-drag forces a full band repaint). ONE author --
        # there is no ws mirror; launcher_layer + the tests read covers.gen.
        self.gen = 0
        # RAM-tight board (T-Deck): drop the cover pipeline when a cart RUN
        # starts (see diet_release) -- set by the S3 backend only; the P4/host
        # keep covers warm (windows leave the desk visible, and RAM is not
        # scarce). The `if` that reads it is kernel policy, in Workstation._start.
        self.diet = False
        # cart path -> desktop-icon sprite Image (or None). CLEARED by
        # invalidate_all since #209 landing C: before that it was written at one
        # site and cleared at none, so a re-seed or a browser sync kept stale
        # desk icons and a deleted cart's Image never went away.
        self.icons = {}
        self._cache = {}         # (path, w, h) -> shelf-card cover blittable (or None)
        self._order = []         # LRU keys (oldest first); bounds resize variants
        self._pixels = 0         # indexed pixels; device RGB bakes add 2B each
        self._jobs = {}          # (path, w, h) -> in-flight _CoverJob (time-sliced)
        self._built = False      # per-frame cover-build budget (see cover_for)
        self._ms = 0             # ms of it spent this frame
        self._none = {}          # paths known to carry no cover art
        self._buf = None         # reused decode scratch (see _CoverJob)
        self._runs = {}          # path -> (sig, runs) parsed RLE, LRU-bounded
        self._runs_order = []    # LRU keys (oldest first)
        self._runs_bytes = 0
        self._deferred = False   # a build was pushed past the budget -> stay dirty
        self._seen = True        # idle prefetch armed (see prefetch_tick); True from
                                 # BOOT: covers must be warm BEFORE the first cover
                                 # surface opens, not after (p4_clicks measured the
                                 # cold pipeline as two ~1s clicks). Latched False once
                                 # every cart is known; re-armed by a store re-scan.
        self._pf_i = 0           # round-robin cursor over ws.carts.all

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
        loop, and neither has to know the other's ordering."""
        if not self._deferred:
            return False
        self._deferred = False
        return True

    # -- what the grids call, once per card per painted frame ----------------

    def cover_for(self, cart, w, h):
        """The cart's COVER ART (visual identity v1 Section 11.4) as a blittable
        sized exactly (w, h) -- images/cover.moyimg cover-cropped (fill + center
        crop, nearest sample) -- or None when the cart carries none (the shelf
        card falls back to sprite/glyph, the deterministic pre-cover look) OR
        while its build is still in flight. Cached per (path, w, h); read
        through the store so a slimmed cart (#66) never rehydrates, and cleared
        with the icon cache on a store re-scan.

        Builds are TIME-SLICED and BUDGETED (#66, hardware-measured): decoding
        one 320x240 RLE cover in interpreted code costs 0.5-1.7s on the T-Deck,
        so a miss starts a resumable _CoverJob and each frame advances at most
        ONE job by ~_COVER_SLICE_MS. Cards draw their sprite/glyph fallback
        until their cover lands (covers pop in over frames, no frozen frames);
        frame() re-arms the redraw gate while any build is pending."""
        ws = self.ws
        path = cart.get("path")
        if path is None or ws.carts_store is None or w <= 0 or h <= 0:
            return None
        self._seen = True     # re-arm the idle prefetch (it latches off once
                              # every cart is known; a surface asking again is
                              # the cheap signal to re-check)
        if path in self._none:       # known cover-less: never re-probe
            return None
        key = (path, w, h)
        cache = self._cache
        if key in cache:
            order = self._order
            try:
                order.remove(key)
            except ValueError:
                pass
            order.append(key)
            return cache[key]
        # Per-frame build budget. This used to be ONE build per frame, because a
        # build was a 0.5-1.7s interpreted decode and even one had to be sliced.
        # With the decode and crop both native, a build off cached runs is ~2ms,
        # so a count of one just spread N cheap covers over N frames -- which is
        # exactly the stutter after a resize the owner reported. Spend a TIME
        # slice instead: cheap builds all land on the same frame, an expensive
        # one still yields.
        if self._built and self._ms >= _COVER_SLICE_MS:
            self._deferred = True
            return None
        self._built = True
        t0 = _ticks_ms()
        jobs = self._jobs
        job = jobs.get(key)
        if job is None:
            # Parsed runs still in RAM? Then this size costs a native decode +
            # crop (~1.7ms) and touches no storage at all (#155). That is what
            # makes a relayout cheap: reading the blob is 46.9ms and parsing it
            # 17.1ms on P4 glass, against 0.89 + 0.76ms for the two native steps.
            # Keyed by PATH alone, deliberately. Validating against the cover's
            # content stamp would mean READING the blob to compute it, which is
            # the 46.9ms this cache exists to avoid -- so it uses the same trust
            # model as the crop cache beside it: good for the session, dropped
            # wholesale on a store re-scan (which is what a create/edit/delete
            # goes through). Keying it on a stamp stashed on the cart dict was
            # measured to never hit at all: the picker's dicts do not survive a
            # relayout, so every build re-read and re-parsed the blob (53ms) and
            # the cache was dead code.
            runs = self._runs_get(path)
            sig = None
            if runs is None:
                runs, sig = self._runs_load(path)
                if runs is None:
                    self._spend(t0)
                    return self._finish(key, None)
            need = runs[0] * runs[1]
            # The shared scratch is only safe when the build cannot span frames,
            # i.e. BOTH steps are native. With a Python crop the job keeps
            # partial state in pix across frames and another cart's decode would
            # overwrite it.
            if _DECODE_RUNS is not None and _CROP_INDEX is not None:
                if self._buf is None or len(self._buf) < need:
                    # #186: the scratch lives off the gc heap too. Growing it
                    # frees the old one -- unless a job still decodes into it
                    # (the native-crop exception fallback can span frames);
                    # then the old scratch LEAKS, bounded, never freed live.
                    old = self._buf
                    if old is not None:
                        for _j in jobs.values():
                            if _j.pix is old:
                                old = None
                                break
                    if old is not None:
                        _moybuf.free(old)
                    self._buf = _moybuf.alloc(need)
                job = _CoverJob(runs, w, h, buf=self._buf)
            else:
                job = _CoverJob(runs, w, h)
            ws.note_cost("cover.build")   # decode + crop for one (path, w, h)
            job.sig = sig                   # stamps the sidecar when it lands
            jobs[key] = job
            # Bound the half-built set: a card scrolled out of view stops
            # being stepped -- drop some OTHER job (it just rebuilds if it
            # ever scrolls back into view).
            while len(jobs) > 8:
                for old in jobs:
                    if old != key:
                        jobs.pop(old)
                        break
                else:
                    break
        if not job.done:
            job.step(t0)
        self._spend(t0)
        if not job.done:
            self._deferred = True    # keep frames coming until it lands
            return None
        jobs.pop(key, None)
        return self._finish(key, job.img)

    def icon_sheet_for(self, cart):
        """A cached sprite Image for a cart's desktop icon, or None when the cart
        has no art (then the type glyph is drawn). Cached per cart path so the
        grid doesn't rebuild a sheet every frame.

        The tiles come from the manifest's "icon" (SPEC.md 3.4) -- [tile, w, h],
        or a bare tile id for 1x1 -- falling back to tile 0. The field has to be
        explicit rather than a plain tile-0 rule because tile 0 is BLANK by
        convention across the whole PICO-8 catalogue (it is why map cell 00 means
        empty), so tile 0 alone draws nothing for every converted cart."""
        if cart.get("path") is None:                # a pinned pseudo tile (Make/New):
            return None                             # no cart art -> draw its type glyph
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
        a re-seed or a browser sync can carry new or changed cover art, can
        change a cart's icon tile, and can take a cart away entirely -- so the
        card bitmaps, the parsed sources (77KB apiece; holding a departed
        cart's would be a leak), the cover-less set and the ICON cache all go.

        The icon cache is the one this used to miss: it was written by
        `icon_sheet_for` and cleared nowhere, so a re-scan kept drawing the icon
        a cart had before it was edited, and a deleted cart's Image stayed live
        forever (docs/history/console_architecture_2026-08.md rev-2 item 10)."""
        self._drop_payloads(0)
        self._none = {}
        self._prune_icons()
        self.gen += 1             # re-arm the idle prefetch: new/changed carts
        self._seen = True         # should warm before their surface opens

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

        In-flight jobs go FIRST: a `_CoverJob` aliases the runs blob it decodes
        from and (when both native steps are present) the shared decode scratch.
        Free before dropping them and `_free_runs`'s alias guard declines the
        free while the LRU discards the entry anyway -- the blob is then leaked
        for the session -- and the scratch, which has no guard, is handed to a
        job that is still writing into it.

        `keep` is how many NEWEST entries of each LRU survive (0 = everything
        goes). `scratch` frees the reusable decode buffer as well, which is a
        RAM-release intent (diet_release) rather than an invalidation one: a
        re-scan wants the scratch kept, since nothing about it went stale."""
        self._jobs = {}                       # <- FIRST. See above.
        order = self._order
        cache = self._cache
        while len(order) > keep:
            k = order.pop(0)
            img = cache.pop(k, None)
            if img is not None:
                self._pixels -= len(img.pix)
                self._free_img(img)           # #186: pix + bakes off-heap
        if not order:
            self._pixels = 0
        rorder = self._runs_order
        runs = self._runs
        while len(rorder) > keep:
            k = rorder.pop(0)
            gone = runs.pop(k, None)
            if gone is not None:
                self._runs_bytes -= len(gone[1][2])
                self._free_runs(gone[1][2])
        if not rorder:
            self._runs_bytes = 0
        if scratch and self._buf is not None:
            _moybuf.free(self._buf)           # the 76.8KB decode scratch
            self._buf = None                  # realloc'd on demand

    def diet_release(self):
        """Drop the whole cover pipeline before a cart runs (cover_diet tier).

        The 2026-08-03 census: on the T-Deck the shelf redesign's caches are the
        live-set staircase -- parsed runs (~15KB x every cart, 512KB cap), the
        cover blittables (768KB-pixel cap + the device RGB565 bakes), the 76.8KB
        decode scratch -- all sized for the P4 and none of it read while a game
        owns the glass, yet every GC pause marks it (114ms at the old 638KB live
        set vs 243ms at 1427KB, measured on glass). Covers are regenerable by
        design, so the trade is: halve the mid-play GC pause, pay a shelf
        pop-in on the way back home (_seen re-arms the idle prefetch).
        _none stays: knowing a cart HAS no art is a probe saved, not RAM.

        KEEPS the newest _COVER_DIET_KEEP entries of both LRUs (owner ask
        2026-08-03, "I'd rather not have pop-in"): the covers on screen when
        PLAY was tapped are the most recently touched, so the exact view the
        kid returns to is still warm (~150KB retained vs ~800KB dropped) and
        only cards scrolled into view later rebuild -- their normal cold path,
        prefetch-warmed. The full fix (cover payloads in moy_alloc storage the
        collector never scans, warm AND GC-invisible) is the standing follow-up."""
        # The #186 order (jobs before frees) lives in _drop_payloads, which
        # invalidate_all shares -- this path just keeps a few entries and
        # hands back the decode scratch as well.
        self._drop_payloads(_COVER_DIET_KEEP, scratch=True)
        self.gen += 1             # any shelf band repaints from scratch
        self._seen = True         # re-arm the idle prefetch for the return home

    # -- the idle warmers (frame()'s quiet branch) ---------------------------

    def prefetch_tick(self):
        """Warm ONE not-yet-known cart's cover runs. Called only from the idle
        branch of frame(), i.e. on a frame that would otherwise do nothing.

        A cover's blob read + parse is ~108ms and is charged to whichever frame
        first needs the card. On a shelf that scrolls, that is a DRAG frame: the
        picker measured a 577ms worst frame and a 48ms median against a 31ms
        warm one, which is the "it takes a while for all the covers to load and
        for it to stop stuttering" the owner reported. The work cannot be made
        much cheaper (it is flash-bound), so it moves instead -- same reasoning as
        the bar strip: spend it where nobody is waiting.

        ARMED FROM BOOT (2026-07-27), not from the first cover draw. The old
        gate ("only while a surface is showing covers") kept the cache cold at
        exactly the moment it was needed: tools/p4_clicks.py measured
        back_to_desk at 1108ms and open_picker at 824ms, both of which were the
        cover pipeline paying its ~49ms-per-cart loads ON the transition's
        painted frames because nothing had armed the prefetch from the desk or
        Settings. Warming from boot moves all of it into the first few idle
        seconds of the session. The trade, accepted: an idle EDITOR now warms
        the cache too, so the first input after a >2-quiet-frame pause can land
        behind one in-flight flash read (~50-108ms extra latency, at most once
        per cart per session, then never again -- the exhaustion latch below).
        A RUNNING game is never affected: it animates, so the idle branch that
        calls this never executes. Runs only after a couple of quiet frames so
        the gap between two gestures is not spent on flash."""
        ws = self.ws
        carts = ws.carts.all
        if not self._seen or ws.carts_store is None or not carts:
            return
        n = len(carts)
        i = self._pf_i
        for _ in range(n):
            cart = carts[i % n]
            i += 1
            path = cart.get("path")
            if (not path or path in self._none
                    or self._runs_get(path) is not None):
                continue
            self._pf_i = i
            self._runs_load(path)
            return
        self._pf_i = i
        # Every cart's RUNS are known. Phase 2 (2026-07-27): pre-BUILD the cover
        # IMAGES the shelf/picker grids' next full draw will request, so the
        # first click pays a cache hit instead of a build. Attributed on glass:
        # with runs warm, the first draw at a new card size still cost ~10ms
        # per card (native decode+crop at card size) x 12 cards = ~120ms of the
        # remaining 2x~200ms transition -- charged to the exact frames a kid is
        # watching. Same doctrine as phase 1: pay it where nobody waits.
        if self._prebuild_tick():
            return
        # Nothing left to warm: stop until something asks for covers again (a
        # re-scan clears the caches and cover_for re-arms the flag).
        self._seen = False

    # The prebuild covers the first screenful per grid -- what a fresh session's
    # click reveals. Scroll-ins beyond it build lazily as before (~10ms once per
    # card, amortized over drag frames). Deliberately NOT every item: the cover
    # cache is pixel-capped (_COVER_CACHE_MAX_PIXELS) and LRU -- prebuilding two
    # full 29-cart grids would evict the head cards (the ones the click shows)
    # to make room for the tail.
    _COVER_PREBUILD_PER_GRID = 12

    def _prebuild_tick(self):
        """Build ONE pending cover image from the grids' cover_specs (the exact
        (cart, w, h) set their next full draw requests). Returns True while
        there is (or may be) work left, False when the visible set is fully
        built.

        Runs on idle frames only (the caller), so it must not re-arm the paint
        machinery: cover_for sets _deferred when a build defers, which would
        turn the NEXT painted frame into two -- save/restore it."""
        ws = self.ws
        grids = (ws.launcher, ws.picker)
        cap = self._COVER_PREBUILD_PER_GRID
        for grid in grids:
            specs = getattr(grid, "cover_specs", None)
            if specs is None:
                continue
            n = 0
            for cart, w, h in specs():
                if n >= cap:
                    break
                n += 1
                path = cart.get("path")
                if (not path or path in self._none
                        or (path, w, h) in self._cache):
                    continue
                deferred = self._deferred
                try:
                    self.cover_for(cart, w, h)
                finally:
                    self._deferred = deferred
                return True
        return False

    # -- the runs cache: the size-independent half of a build ----------------

    def _runs_load(self, path):
        """Read + parse this cart's cover blob into the runs cache; returns
        (runs, sig), or (None, None) for a cart with no cover art.

        This is the SIZE-INDEPENDENT half of a cover build, and the expensive one:
        58ms to read the blob and 50ms to parse it on P4 glass, against ~2ms for
        the decode+crop that turns runs into a card of a given size. Split out so
        the idle prefetch can pay it while nothing is happening (see
        prefetch_tick) instead of mid-drag, when a card scrolls into view.

        A cover-less cart is remembered per PATH: probing for a file that is not
        there costs 22ms on this board's flash (a listdir of images/ measured the
        same 23.5ms, so there is no cheaper existence test), and 17 of 29 carts had
        no cover -- 380ms of pure waste per session before this was cached."""
        ws = self.ws
        store = ws.carts_store
        loader = getattr(store, "load_image", None)
        cover_name = getattr(store, "COVER_IMAGE", "cover")
        sig_fn = getattr(store, "cover_sig", None)
        ws.note_cost("cover.blob.read")      # 58ms hit / 22ms miss on P4 flash
        # Through the storage gate like every other store read here: this fires
        # from the launcher's draw and the idle prefetch, i.e. around a repaint,
        # where the T-Deck has a flush in flight over the SPI host its card
        # shares -- an sdspi transaction there is the documented hang.
        blob = ws._with_sd(
            lambda: loader(path, cover_name)) if loader is not None else None
        runs = None
        sig = None
        if blob:
            parse = getattr(store, "moyimg_runs", None)
            runs = parse(blob) if parse is not None else None
            sig = sig_fn(blob) if sig_fn is not None else None
        if runs is None:
            self._none[path] = True
            return None, None
        # #186: the packed RLE blob (~15KB x every cart, 512KB cap) is the
        # biggest slice of the warm shelf -- move it off the gc heap. Every
        # consumer (len, int indexing, the native decode_runs) reads a
        # memoryview identically; eviction frees it (_free_runs).
        runs = (runs[0], runs[1], _moybuf.take(runs[2]))
        self._runs_put(path, sig, runs)
        return runs, sig

    def _runs_get(self, path):
        """The parsed (sw, sh, packed) runs for this cart's cover, or None."""
        e = self._runs.get(path)
        if e is None:
            return None
        order = self._runs_order
        try:
            order.remove(path)
        except ValueError:
            pass
        order.append(path)
        return e[1]

    def _runs_put(self, path, sig, runs):
        """Cache parsed runs, LRU-bounded by packed bytes."""
        cache = self._runs
        order = self._runs_order
        old = cache.get(path)
        if old is not None:
            self._runs_bytes -= len(old[1][2])
            self._free_runs(old[1][2])   # #186: replaced blob returns
            try:
                order.remove(path)
            except ValueError:
                pass
        cache[path] = (sig, runs)
        order.append(path)
        self._runs_bytes += len(runs[2])
        while order and self._runs_bytes > _COVER_RUNS_MAX_BYTES:
            drop = order.pop(0)
            if drop == path:              # never evict the one just stored
                order.insert(0, drop)
                break
            gone = cache.pop(drop, None)
            if gone is not None:
                self._runs_bytes -= len(gone[1][2])
                self._free_runs(gone[1][2])   # #186 (job-alias guarded)

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
            self._pixels += len(img.pix)
        while (len(order) > _COVER_CACHE_MAX_ENTRIES
               or self._pixels > _COVER_CACHE_MAX_PIXELS):
            old_key = order.pop(0)
            old_img = cache.pop(old_key, None)
            if old_img is not None:
                self._pixels -= len(old_img.pix)
                self._free_img(old_img)   # #186: pix + bakes off-heap
        return img

    # -- #186 frees ----------------------------------------------------------

    def _free_runs(self, packed):
        """#186: return an evicted runs blob to off-heap storage -- unless an
        in-flight _CoverJob still decodes from it (the LRU knows nothing
        about jobs; leaking one blob beats a use-after-free). No-op for
        gc-heap payloads (host / fallback)."""
        for job in self._jobs.values():
            if job.packed is packed:
                return
        _moybuf.free(packed)

    def _free_img(self, img):
        """#186: release an evicted cover blittable's off-heap payloads --
        the indexed pixels plus any RGB565 bake the device canvas stamped on
        it (_rgb_i / _rgb / the variant dict). Alias-safe: the hot _rgb slot
        SHARES a variant entry's buffer, so each distinct buffer frees once.
        Fields are nulled afterwards, so if anything ever drew an evicted
        cover it would raise loudly instead of blitting freed memory
        (nothing does -- pinned by the #186 audit)."""
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
