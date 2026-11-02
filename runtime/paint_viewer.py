# Map (grep -n a name to jump there):
#   Viewer                a desk's drawing on a handheld: read by rows, never drawn on
#   Viewer.window         the drawing's part the view shows, read at the zoom
#   Viewer.step           the next zoom
"""A drawing larger than this handheld's own, to look at in Paint
(paint_app.PaintAppLayer): never decoded whole, each zoom reads the file a
row at a time into the bitmap the view shows.

It takes paint_app's FIT at its FOOT: paint_app imports this module after
binding its constants, so either may load first. MicroPython-safe.
"""

try:
    import moybuf as _moybuf
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    try:
        from runtime import moybuf as _moybuf
    except ImportError:
        _moybuf = None
try:
    from artwork import read_cover
    from file_widgets import Bitmap
except ImportError:  # pragma: no cover - direct host import
    from runtime.artwork import read_cover
    from runtime.file_widgets import Bitmap


class Viewer:
    """A drawing larger than this handheld's own, to look at (owner,
    2026-10-21): never decoded whole. Each zoom reads the drawing's file a row
    at a time into the screen it shows (`read_cover`): Fit a screen of its
    own size at a quarter, a half and up a source of that scale (the half,
    else the whole), off the gc heap; what the view shows of it is cut into a
    window the canvas bakes. A sixteenth of the whole is the overview."""

    OWNER = "artwork"

    def __init__(self, name, w, h, blob, view):
        self.name = name
        self.W = w
        self.H = h
        self.blob = blob
        self.view = view
        self.zoom = FIT
        self.fit_q = self._fit()
        self.pan = [0, 0]                 # the view's top left in the source's pixels
        self._fit_bmp = None
        self._mini = None
        self._src = None                  # (scale in quarters, w, h, pixels)
        self._win = None                  # (key, Bitmap) the view's window of it
        self._show = None

    def _fit(self):
        vw, vh = self.view[2], self.view[3]
        for q in (4, 2, 1):
            if self.W * q // 4 <= vw and self.H * q // 4 <= vh:
                return q
        return 1

    def quarters(self):
        return self.fit_q if self.zoom == FIT else self.zoom

    def _alloc(self, n):
        if _moybuf is not None:
            return _moybuf.alloc(n)
        return bytearray(n)

    def _free(self, pix):
        if pix is not None and _moybuf is not None and isinstance(pix, memoryview):
            try:
                _moybuf.free(pix)
            except Exception:  # noqa: BLE001 -- already returned
                pass

    def fit_bitmap(self):
        if self._fit_bmp is None:
            q = self.fit_q
            w, h = self.W * q // 4, self.H * q // 4
            pix = bytearray(w * h)
            if read_cover(self.blob, pix, w, h) is None:
                return None
            self._fit_bmp = Bitmap(w, h, pix, self.OWNER)
        return self._fit_bmp

    def mini(self):
        """The whole at a sixteenth: the overview a zoomed view shows."""
        if self._mini is None:
            w, h = max(1, self.W // 16), max(1, (self.H + 8) // 16)
            pix = bytearray(w * h)
            if read_cover(self.blob, pix, w, h) is None:
                return None
            self._mini = Bitmap(w, h, pix)
        return self._mini

    def show_bitmap(self, w, h):
        if self._show is None or self._show.w != w or self._show.h != h:
            pix = bytearray(w * h)
            if read_cover(self.blob, pix, w, h) is None:
                return None
            self._show = Bitmap(w, h, pix, self.OWNER)
        return self._show

    def _source(self):
        """The raster the zoom reads from: the drawing at a half for a half,
        whole for 1x and up."""
        q = min(self.quarters(), 4)
        src = self._src
        if src is not None and src[0] == q:
            return src
        self.drop_source()
        w, h = self.W * q // 4, self.H * q // 4
        pix = self._alloc(w * h)
        if read_cover(self.blob, pix, w, h) is None:
            self._free(pix)
            return None
        self._src = (q, w, h, pix)
        return self._src

    def drop_source(self):
        if self._src is not None:
            self._free(self._src[3])
        self._src = None
        self._win = None

    def window(self):
        """(Bitmap, k): the view's window of the zoom's source and the
        whole pixels each of its pixels takes on the glass."""
        src = self._source()
        if src is None:
            return None, 1
        q, sw, sh, pix = src
        k = max(1, self.quarters() // q)
        vw, vh = self.view[2], self.view[3]
        ww, wh = min(sw, vw // k), min(sh, vh // k)
        self.pan[0] = max(0, min(self.pan[0], sw - ww))
        self.pan[1] = max(0, min(self.pan[1], sh - wh))
        key = (q, self.pan[0], self.pan[1], ww, wh)
        if self._win is not None and self._win[0] == key:
            return self._win[1], k
        win = bytearray(ww * wh)
        x0, y0 = self.pan
        for r in range(wh):
            o = (y0 + r) * sw + x0
            win[r * ww:(r + 1) * ww] = pix[o:o + ww]
        bmp = Bitmap(ww, wh, win, self.OWNER)
        self._win = (key, bmp)
        return bmp, k

    def step(self, zooms):
        """The next zoom of `zooms`, the view kept about its middle."""
        q0 = self.quarters()
        i = zooms.index(self.zoom) if self.zoom in zooms else 0
        self.zoom = zooms[(i + 1) % len(zooms)]
        q1 = self.quarters()
        s0 = min(q0, 4)
        s1 = min(q1, 4)
        vw, vh = self.view[2], self.view[3]
        k0 = max(1, q0 // s0)
        mx = (self.pan[0] + vw // (2 * k0)) * 4 // s0 if q0 != self.fit_q else self.W // 2
        my = (self.pan[1] + vh // (2 * k0)) * 4 // s0 if q0 != self.fit_q else self.H // 2
        k1 = max(1, q1 // s1)
        self.pan[0] = mx * s1 // 4 - vw // (2 * k1)
        self.pan[1] = my * s1 // 4 - vh // (2 * k1)
        if self.zoom == FIT:
            self.drop_source()

    def nudge(self, dx, dy):
        k = max(1, self.quarters() // min(self.quarters(), 4))
        step = 16 // k
        self.pan[0] += dx * step
        self.pan[1] += dy * step

    def frame(self):
        """Where the view stands in the whole, in the whole's pixels:
        (x, y, w, h), what the overview frames."""
        q = self.quarters()
        s = min(q, 4)
        vw, vh = self.view[2], self.view[3]
        return (self.pan[0] * 4 // s, self.pan[1] * 4 // s,
                min(self.W, vw * 4 // q), min(self.H, vh * 4 // q))

    def close(self):
        self.drop_source()
        self._fit_bmp = None
        self._mini = None
        self._show = None
        self.blob = None


# paint_app's zoom word, imported last (see above).
try:
    from paint_app import FIT
except ImportError:  # pragma: no cover - direct host import
    from runtime.paint_app import FIT
