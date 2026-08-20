# The user-files (#108) shared visual vocabulary: the thumbnail-grid picker
# every file surface reuses -- the Files app's gallery, Paint's OPEN mode, and
# any later save/open flow (Writer/Sheets/#70/#110). One widget so "browse your
# stuff" is a single learned gesture; it is, quietly, an Open dialog in icon
# view. Kid rules baked in: thumbnails first, names ALWAYS visible under them
# (names are identity -- the desktop concept we refuse to hide), newest first,
# no paths, no extensions. MicroPython-safe; staged to both boards.

try:
    from moy_image import decode_moyimg
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_image import decode_moyimg
    from runtime import ui as _ui

_in = _ui.rect_in


class Bitmap:
    """Duck-typed indexed image accepted by host and device system canvases."""

    def __init__(self, w, h, pix):
        self.w = int(w)
        self.h = int(h)
        self.pix = pix
        self.transparent = -1
        self._paint = True


def cover_indices(src, sw, sh, dw, dh):
    """Nearest-neighbor cover crop: source cropped centered to the target
    aspect, then sampled to exactly dw x dh (the ArtworkService formula)."""
    if sw * dh > sh * dw:
        crop_h = sh
        crop_w = max(1, sh * dw // dh)
        sx0, sy0 = (sw - crop_w) // 2, 0
    else:
        crop_w = sw
        crop_h = max(1, sw * dh // dw)
        sx0, sy0 = 0, (sh - crop_h) // 2
    out = bytearray(dw * dh)
    for y in range(dh):
        sy = sy0 + y * crop_h // dh
        so = sy * sw
        do = y * dw
        for x in range(dw):
            out[do + x] = src[so + sx0 + x * crop_w // dw]
    return out


THUMB_CACHE_MAX = 24     # decoded tiles kept across draws (a few KB each)
# The name strip under the art, in fs units -- ui.cell's own default for a
# labelled cell, named here because the thumbnail cache is sized against it.
_CAPTION_H = 14


class FileGridView:
    """A pageable thumbnail grid over one user-file kind. The embedding layer
    owns chrome, verbs and store writes; this widget only lists, draws and
    hit-tests. tap() -> ("pick", name) on the already-selected tile,
    ("sel", name) on a first tap, ("page", +/-1) on the page chips, None
    outside -- an open-flow embedder treats sel and pick alike."""

    def __init__(self, ws, kind="drawings"):
        self.ws = ws
        self.kind = kind
        self.names = ()
        self.sel = -1
        self.page = 0
        self._thumbs = {}          # (name, w, h) -> Bitmap|None (None = broken blob)
        self._thumb_order = []
        # ui.py's six-state model, GRID form: interaction is two INDICES fed to
        # ui.cell_state, never one ui.Hits rect per cell (that is the measured
        # ~395-rect / ~9.3ms shape the toolkit's docstring forbids). -1 = none,
        # which is also what `sel` spells, so an empty grid cues nothing.
        self.hover = -1
        self.pressed = -1
        self._armed = -1
        self._down = False
        self.set_rect((0, 0, 0, 0), 1)   # real geometry arrives per draw

    # -- geometry --------------------------------------------------------------

    def set_rect(self, rect, fs):
        self._rect = rect
        self.fs = max(1, int(fs))
        fs = self.fs
        self.cell_w = 76 * fs
        self.cell_h = 66 * fs      # 72x48 art + the name row under it
        self.gap = 6 * fs
        x, y, w, h = rect
        self.cols = max(1, (w - self.gap) // (self.cell_w + self.gap))
        self.rows = max(1, (h - self.gap - 16 * fs) // (self.cell_h + self.gap))
        self.page_h = 14 * fs

    def _per_page(self):
        return self.cols * self.rows

    def _pages(self):
        return max(1, (len(self.names) + self._per_page() - 1) // self._per_page())

    def _cell_rect(self, i):
        x, y, _w, _h = self._rect
        c = i % self.cols
        r = i // self.cols
        return (x + self.gap + c * (self.cell_w + self.gap),
                y + self.gap + r * (self.cell_h + self.gap),
                self.cell_w, self.cell_h)

    def _page_rects(self):
        x, y, w, h = self._rect
        fs = self.fs
        py = y + h - self.page_h
        return ((x + self.gap, py, 24 * fs, self.page_h),
                (x + w - self.gap - 24 * fs, py, 24 * fs, self.page_h))

    # -- data ------------------------------------------------------------------

    def refresh(self):
        """Re-list the kind (newest first) and drop stale thumbs. Guarded like
        every store read: no store / a failed read lists nothing. The
        selection survives BY NAME -- newest-first reorders (a copy, a save)
        must never silently move the highlight to a different item."""
        keep = self.sel_name()
        ws = self.ws
        names = ()
        if ws.carts_store is not None and ws.carts_root is not None:
            try:
                names = tuple(ws._with_sd(
                    lambda: ws.carts_store.list_files(self.kind, ws.carts_root)))
            except Exception:  # noqa: BLE001 -- degrade to an empty gallery
                names = ()
        self.names = names
        live = set(names)
        for key in list(self._thumbs):
            if key[0] not in live:
                del self._thumbs[key]
                self._thumb_order.remove(key)
        self.select(keep)
        self.page = max(0, min(self.page, self._pages() - 1))
        # Newest-first reorders: an index-keyed cue would now point at a
        # different file, so the pump starts over (the selection, keyed by
        # NAME above, deliberately does not).
        self.hover = -1
        self.pressed = -1
        self._armed = -1

    def invalidate(self, name=None):
        """Forget cached thumbs (for `name`, or all) after a content write."""
        for key in list(self._thumbs):
            if name is None or key[0] == name:
                del self._thumbs[key]
                self._thumb_order.remove(key)

    def sel_name(self):
        if 0 <= self.sel < len(self.names):
            return self.names[self.sel]
        return None

    def select(self, name):
        """Point the selection at `name` (or clear it with None); the page
        follows. The embedders' one way to move the highlight from code."""
        self.sel = self.names.index(name) if name in self.names else -1
        if self.sel >= 0:
            self.page = self.sel // self._per_page()

    def _thumb(self, name, w, h):
        key = (name, w, h)
        if key in self._thumbs:
            return self._thumbs[key]
        ws = self.ws
        bmp = None
        try:
            blob = ws._with_sd(
                lambda: ws.carts_store.load_file(self.kind, name, ws.carts_root))
            data = decode_moyimg(blob) if blob else None
            if data is not None:
                bmp = Bitmap(w, h, cover_indices(data[2], data[0], data[1], w, h))
        except Exception:  # noqa: BLE001 -- a broken blob draws the placeholder
            bmp = None
        self._thumbs[key] = bmp
        self._thumb_order.append(key)
        if len(self._thumb_order) > THUMB_CACHE_MAX:
            old = self._thumb_order.pop(0)
            self._thumbs.pop(old, None)
        return bmp

    # -- draw + hit-test -------------------------------------------------------

    def draw(self, cv, th):
        fs = self.fs
        x, y, w, h = self._rect
        if not self.names:
            msg = "NOTHING HERE YET"
            cv.print(msg, x + (w - len(msg) * 8 * fs) // 2,
                     y + (h - 8 * fs) // 2, th.get("dim", 1), 1)
            return
        start = self.page * self._per_page()
        # The thumbnail cache is sized ONCE, outside the loop, off the same pure
        # geometry every cell will hand back (ui.cell_art_rect is separate from
        # ui.cell for exactly this).
        _ax, _ay, art_w, art_h = _ui.cell_art_rect(
            (0, 0, self.cell_w, self.cell_h), fs, 2 * fs, _CAPTION_H * fs)
        hover, pressed = self.hover, self.pressed
        for i in range(start, min(start + self._per_page(), len(self.names))):
            name = self.names[i]
            ax, ay, _aw, _ah = _ui.cell(
                cv, th, self._cell_rect(i - start), name, on=(i == self.sel),
                state=_ui.cell_state(i, hover, pressed), fs=fs)
            bmp = self._thumb(name, art_w, art_h) if self.kind == "drawings" else None
            if bmp is not None:
                cv.spr(bmp, ax, ay)
            else:
                cv.rect(ax, ay, art_w, art_h, th.get("edge", 13))
        if self._pages() > 1:
            prev_r, next_r = self._page_rects()
            ink = th.get("title_ink", 0)
            cv.print("<", prev_r[0] + 8 * fs, prev_r[1] + 3 * fs, ink, 1)
            cv.print(">", next_r[0] + 8 * fs, next_r[1] + 3 * fs, ink, 1)
            label = str(self.page + 1) + "/" + str(self._pages())
            cv.print(label, x + (w - len(label) * 8 * fs) // 2,
                     prev_r[1] + 3 * fs, th.get("dim", 1), 1)

    def _index_at(self, px, py):
        """Index of the tile under (px, py) on the current page, else -1 --
        the ARITHMETIC hit-test that lets this grid feed ui.cell_state without
        registering anything."""
        start = self.page * self._per_page()
        for i in range(start, min(start + self._per_page(), len(self.names))):
            if _in(px, py, self._cell_rect(i - start)):
                return i
        return -1

    def pointer_frame(self, px, py, pointer):
        """The grid's twin of ui.Hits.pointer_frame -- same contract, index
        form: pump hover AND pressed from one pointer sample, return True when
        either moved (the embedder marks its surface dirty on True).

        `pointer` is duck-typed (.down / .visible / an optional .hovering), so a
        TOUCH tier -- which places the pointer hidden -- never hovers but does
        press, which is the one cue a finger ever gets."""
        down = bool(getattr(pointer, "down", False))
        pointing = bool(getattr(pointer, "visible", False)
                        or getattr(pointer, "hovering", False))
        i = self._index_at(px, py)
        if down:
            if not self._down:
                self._armed = i            # the press EDGE picks the target
            armed = self._armed
            pressed = armed if (armed >= 0 and i == armed) else -1
            hover = -1                     # a pointer that is down never hovers
        else:
            self._armed = -1
            pressed = -1
            hover = i if pointing else -1
        self._down = down
        changed = (hover != self.hover) or (pressed != self.pressed)
        self.hover = hover
        self.pressed = pressed
        return changed

    def pointer_leave(self):
        """The embedder stopped feeding this grid samples: drop both cues, and
        the press-edge history with them (ui.Hits.pointer_leave's rule -- a
        stale cue costs one repaint, a quiet grid none)."""
        changed = self.hover >= 0 or self.pressed >= 0
        self.hover = -1
        self.pressed = -1
        self._armed = -1
        self._down = False
        return changed

    def tap(self, px, py):
        """("sel", name) on a first tap, ("pick", name) on the already-selected
        tile (the two-stage protocol: Files opens on pick, Paint's open dialog
        treats both alike), ("page", d) on the page chips, None outside."""
        if self._pages() > 1:
            prev_r, next_r = self._page_rects()
            if _in(px, py, prev_r):
                self.page = (self.page - 1) % self._pages()
                return ("page", -1)
            if _in(px, py, next_r):
                self.page = (self.page + 1) % self._pages()
                return ("page", 1)
        i = self._index_at(px, py)
        if i < 0:
            return None
        if i == self.sel:
            return ("pick", self.names[i])
        self.sel = i
        return ("sel", self.names[i])

    def nav(self, inp):
        """Trackball verbs, speaking tap()'s vocabulary: arrows move the
        selection -> ("sel", name), A -> ("pick", name), None otherwise."""
        n = len(self.names)
        if not n:
            return None
        if inp.pressed("left"):
            self.sel = (self.sel - 1) % n
        elif inp.pressed("right"):
            self.sel = (self.sel + 1) % n
        elif inp.pressed("up"):
            self.sel = (self.sel - self.cols) % n
        elif inp.pressed("down"):
            self.sel = (self.sel + self.cols) % n
        elif inp.pressed("a"):
            name = self.sel_name()
            return ("pick", name) if name else None
        else:
            return None
        self.page = self.sel // self._per_page()
        return ("sel", self.names[self.sel])
