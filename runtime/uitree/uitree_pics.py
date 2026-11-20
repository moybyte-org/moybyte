"""G9's engine half on the host layer: an `image` value found, decoded,
cached under a byte budget and drawn, the app running no code for it.

A card or picture asks `peek(pic, w, h)` while it draws: a decoded picture,
or None, and then the pair is owed. The console's idle branch (a frame with
nothing to paint) calls the layer's `idle_step`, which decodes one owed
picture. A picture that does not decode is failed for the session, traced
once. The cache keeps decoded pixels under `budget` bytes, the least
recently drawn let go first ("redraw on exposure": it is owed again the
next time a card shows it).

What this host layer cannot show: a decode off the frame's thread (here it
is the idle frame's own time), a cache shared across apps, and a budget the
engine sets per board.
"""
import collections

import uitree as S

def budget_for(w, h):
    """The app's picture budget: one glass of RGB565. A view's pictures fit
    it on every glass the runs used (320x240: 6 cards of 17 KB in 150 KB;
    the desk: 8 of 52 KB in 1.2 MB)."""
    return w * h * 2


def _tr(app, text):
    was, S._NOW.app = S._NOW.app, app
    try:
        S._trace("image", "", text)
    finally:
        S._NOW.app = was


class PicEngine:
    def __init__(self, app, files, swapped, budget):
        self.app, self.files, self.swapped, self.budget = app, files, swapped, budget
        self.cache = collections.OrderedDict()     # (pic, w, h) -> (draw kind, pixels, bytes)
        self.used = 0
        self.failed = {}                           # (pic, gen) -> why
        self.owed = []
        self.stats = dict.fromkeys(("decoded", "failed", "refused", "evicted"), 0)

    # -- the frame's side ------------------------------------------------------------

    def begin_frame(self):
        del self.owed[:]
        self.seen = set()

    def peek(self, pic, w, h):
        k = (pic, w, h)
        self.seen.add(k)
        got = self.cache.get(k)
        if got is not None:
            self.cache[k] = self.cache.pop(k)       # the most recent last
            return got
        if (pic, self._gen(pic)) not in self.failed and k not in self.owed:
            self.owed.append(k)
        return None

    def state(self, pic, w, h):
        """none, loading, failed or shown: what the card shows."""
        if pic is None:
            return "none"
        if (pic, w, h) in self.cache:
            return "shown"
        if (pic, self._gen(pic)) in self.failed:
            return "failed"
        return "loading"

    def draw(self, cv, got, x, y, w, h):
        kind, pix, _n = got
        if kind == "cover":
            side = pix[0]
            cv.cover(pix[1], side, 0, 0, side, side, x, y, w, h, 0, False)
        else:
            cv.picture(pix, x, y, w, h)

    # -- the idle side -----------------------------------------------------------------

    @property
    def idle_owed(self):
        return any(k not in getattr(self, "refused", ()) for k in self.owed)

    def idle_step(self):
        view = frozenset(getattr(self, "seen", ()))
        if view != getattr(self, "_refused_view", None):
            self.refused, self._refused_view = set(), view     # the view moved: ask again
        owed = [k for k in self.owed if k not in self.refused]
        if not owed:
            return False
        pic, w, h = k = owed[0]
        self.owed.remove(k)
        try:
            got = self._decode(pic, w, h)
        except MemoryError:
            got = None
        if isinstance(got, str):
            self.failed[(pic, self._gen(pic))] = got
            self.stats["failed"] += 1
            _tr(self.app, "%r %dx%d failed: %s" % (pic, w, h, got))
            return True
        if not self._room(got[2]):
            self.refused = getattr(self, "refused", set())
            if k not in self.refused:
                self.refused.add(k)
                self.stats["refused"] += 1
                _tr(self.app, "%r %dx%d not kept: the pictures in view fill the budget (%d of %d bytes)" % (
                    pic, w, h, self.used, self.budget))
            return False
        self.cache[k] = got
        self.used += got[2]
        self.stats["decoded"] += 1
        _tr(self.app, "%r %dx%d decoded, %d bytes (cache %d, %d of %d bytes)" % (
            pic, w, h, got[2], len(self.cache), self.used, self.budget))
        return True

    def _room(self, n):
        """Room for n bytes: the least recently drawn picture out of view let
        go first; a picture in view is never let go, so a budget the view
        overfills keeps the rest veiled (loading) rather than thrash."""
        seen = getattr(self, "seen", set())
        while self.used + n > self.budget:
            k = next((k for k in self.cache if k not in seen), None)
            if k is None:
                return False
            old = self.cache.pop(k)
            self.used -= old[2]
            self.stats["evicted"] += 1
            _tr(self.app, "%r %dx%d evicted (least recently drawn, out of view), %d bytes back" % (
                k[0], k[1], k[2], old[2]))
        return True

    def forget_own(self, key):
        for k in [k for k in self.cache if k[0].kind == "own" and k[0].key == key]:
            self.used -= self.cache.pop(k)[2]

    def _gen(self, pic):
        return S.pictures.gen.get(pic.key, 0) if pic.kind == "own" else 0

    # -- decoders ----------------------------------------------------------------------

    def _bytes(self, pic):
        if pic.kind == "store":
            kind, name = pic.key.split("/")
            blob, err = self.files.load(kind, name)
            if err is not None or not blob:
                return None, "no file %s" % pic.key
            return blob, None
        src = S.pictures.src.get(pic.key)
        if src is None:
            return None, "no picture %r put" % pic.key
        if src[0] == "idx":
            import moy_carts
            return moy_carts.encode_moyimg(src[1], src[2], src[3]), None
        return src[1], None

    def _decode(self, pic, w, h):
        blob, why = self._bytes(pic)
        if blob is None:
            return why
        raw = blob.encode() if isinstance(blob, str) else bytes(blob)
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            import cover_png as _cp
            side = _cp.SIDE
            while side // 2 >= max(w, h) and _cp.SIDE // (side // 2) in (1, 2, 4, 8):
                side //= 2
            pix = _cp.decode(raw, _cp.SIDE // side, _cp.RGB565_SW if self.swapped else _cp.RGB565, None, None)
            if pix is None:
                return "not a cover PNG"
            return ("cover", (side, pix), side * side * 2)
        from artwork import read_cover
        from file_widgets import Bitmap
        pix = bytearray(w * h)
        if read_cover(raw, pix, w, h) is None:
            return "not a picture"
        return ("bmp", Bitmap(w, h, pix), w * h * 3)        # indices, and the RGB565 bake the canvas keeps
