# The indexed bitmap a picture is drawn from, and `cover_indices` (moyimg's,
# re-exported) that the user-files (#108) surfaces read a cover through.
# MicroPython-safe; staged to every board.

try:
    from moyimg import cover_indices  # noqa: F401 -- re-exported
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moyimg import cover_indices  # noqa: F401


class Bitmap:
    """Duck-typed indexed image accepted by host and device system canvases.

    `owner` names whoever will hand back the off-heap buffer the device canvas
    may take for a FULL-SURFACE RGB565 bake of this bitmap (#186,
    device_canvas._paint_bake_buf) -- the one allocation a 320x240 picture makes
    that the gc heap cannot promise. Left None (every small bitmap, and every
    console-lifetime one) the bake stays a gc bytearray, which is right: an
    off-heap buffer nobody returns is a leak."""

    def __init__(self, w, h, pix, owner=None):
        self.w = int(w)
        self.h = int(h)
        self.pix = pix
        self.transparent = -1
        self._paint = True
        if owner is not None:
            self._owner = owner
