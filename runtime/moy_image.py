# `Image`, the indexed sprite every canvas and the `image` verb share, and the
# wallpaper-preview sidecar (the Appearance monitor's computed frame, read
# instead of re-rendering the cart -- regenerable, plain writes, readers
# validate magic + size + stamp). The moyimg codec is runtime/moyimg.py.
#
# MicroPython-safe (_mkdir from the moy_fs leaf).

try:
    from moy_fs import _mkdir
    from moy_store_base import THUMBS_DIR
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import _mkdir
    from runtime.moy_store_base import THUMBS_DIR


class Image:
    """A small indexed sprite. `pix` is a flat list/bytes of palette indices.

    ONE definition, for every tier. This was written twice -- canvas.py had it
    for the host and device_canvas.py had its own byte-for-byte equivalent --
    which is the same duplication the raster itself carries, in miniature: two
    copies of a plain data holder that a cart's sprite passes through on every
    platform. Merging them is the first step of collapsing the two canvases,
    because both canvases have to agree on the type before either can be the
    survivor.

    It lives HERE, and not in either canvas, because it is the one piece of that
    pair with no raster in it at all -- it holds indices and does not draw -- and
    because this module is already staged to every target (both boards, the wasm
    head, the host), so no build list changes to reach it.

    `transparent` defaults to None rather than the device copy's -1: the host has
    the larger set of callers relying on that, and the device never used its own
    default (device_api passes the index explicitly at both construction sites).
    from_ascii yields -1 either way, which is what the raster tests for.
    """

    def __init__(self, width, height, pix, transparent=None):
        self.w = width
        self.h = height
        self.pix = pix
        self.transparent = transparent

    @classmethod
    def from_ascii(cls, rows, mapping, transparent="."):
        """Build from ['..##..', ...] using {char: index}; `transparent` char skipped."""
        h = len(rows)
        w = max(len(r) for r in rows) if rows else 0
        t_index = -1
        pix = []
        for y in range(h):
            row = rows[y]
            for x in range(w):
                ch = row[x] if x < len(row) else transparent
                if ch == transparent:
                    pix.append(t_index)
                else:
                    pix.append(mapping[ch] & 63)
        return cls(w, h, pix, transparent=t_index)


# --- the wallpaper preview: a decoded-frame sidecar ----------------------------
#
# <cart>/thumbs/<prefix><w>x<h>.mct = b"MCT1" + a 4-byte LE stamp of the source it
# was rendered from (text_sig) + the w*h pix bytes. An edited source changes
# the stamp -> the stale sidecar is ignored and rebuilt; a deleted cart takes its
# thumbs with it; a re-seed wipe just regenerates. Regenerable cache, so: plain
# writes (no atomic dance), best-effort saves, and every reader validates
# magic + size + stamp before trusting a byte.


def _thumb_file(path, w, h, prefix=""):
    return (path + "/" + THUMBS_DIR + "/" + prefix
            + str(int(w)) + "x" + str(int(h)) + ".mct")


def _load_thumb(path, w, h, sig, prefix=""):
    try:
        with open(_thumb_file(path, w, h, prefix), "rb") as f:
            data = f.read()
    except OSError:
        return None
    if (len(data) != 8 + int(w) * int(h) or data[:4] != b"MCT1"
            or int.from_bytes(data[4:8], "little") != (sig & 0xFFFFFFFF)):
        return None
    return data[8:]


def _save_thumb(path, w, h, sig, pix, prefix=""):
    try:
        _mkdir(path + "/" + THUMBS_DIR)
        with open(_thumb_file(path, w, h, prefix), "wb") as f:
            f.write(b"MCT1" + (sig & 0xFFFFFFFF).to_bytes(4, "little"))
            f.write(pix)
    except Exception:  # noqa: BLE001 -- regenerable cache
        pass


def load_wallpaper_preview(path, w, h, sig):
    """The Appearance monitor's COMPUTED preview frame for the wallpaper cart
    at `path` (thumbs/wp<w>x<h>.mct) -- raw indexed pix, or None when absent,
    stale (the stamp is text_sig of the cart's SOURCE, so an edit rebuilds)
    or corrupt."""
    return _load_thumb(path, w, h, sig, "wp")


def save_wallpaper_preview(path, w, h, sig, pix):
    """Persist a rendered wallpaper preview frame. Best-effort, never raises."""
    _save_thumb(path, w, h, sig, pix, "wp")
