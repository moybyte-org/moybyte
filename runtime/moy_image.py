# The portable moyimg codec + cover-thumb sidecars, extracted from moy_carts.py
# (which re-exports every name here, so store call sites and tests are unchanged).
#
# encode/decode_moyimg: the ``moyimg-v1`` indexed-bitmap blob (Paint's MicroPython-
# safe RLE codec; legacy zlib assets stay valid -- decoders dispatch on ``codec``).
# moyimg_runs: the header+runs parse for the time-sliced cover builder.
# The wallpaper-preview sidecar cache (the Appearance monitor's computed frame)
# reads instead of re-running the 0.5-1.7s RLE decode -- regenerable, plain writes,
# readers validate magic + size + stamp.
#
# MicroPython-safe (json + binascii only; _mkdir from the moy_fs leaf).

import json

try:
    from moy_fs import _mkdir
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import _mkdir


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


def _b64_encode(data):
    """MicroPython/CPython-compatible base64 text without a trailing newline."""
    try:
        import ubinascii as _binascii
    except ImportError:  # pragma: no cover - CPython
        import binascii as _binascii
    out = _binascii.b2a_base64(data)
    if not isinstance(out, str):
        out = out.decode("ascii")
    return out.strip()


def _b64_decode(text):
    try:
        import ubinascii as _binascii
    except ImportError:  # pragma: no cover - CPython
        import binascii as _binascii
    return _binascii.a2b_base64(text)


def encode_moyimg(width, height, indices):
    """Encode an indexed bitmap as a portable ``moyimg-v1`` blob.

    Paint uses a tiny RLE codec instead of zlib so saving works in the shared
    runtime without depending on a board-specific compressor. Existing zlib
    assets remain valid; decoders dispatch on the optional ``codec`` field.
    Runs are stored as ``count, palette_index`` byte pairs.
    """
    w = int(width)
    h = int(height)
    if w <= 0 or h <= 0 or len(indices) != w * h:
        raise ValueError("bad artwork size")
    packed = bytearray()
    pos = 0
    total = len(indices)
    while pos < total:
        value = int(indices[pos]) & 63
        count = 1
        while pos + count < total and count < 255 \
                and (int(indices[pos + count]) & 63) == value:
            count += 1
        packed.append(count)
        packed.append(value)
        pos += count
    return json.dumps({
        "format": "moyimg-v1", "w": w, "h": h,
        "codec": "rle", "data": _b64_encode(packed),
    })


def moyimg_runs(text):
    """Parse a ``.moyimg`` into ``(w, h, packed_rle_bytes)`` WITHOUT decoding
    the pixels -- the JSON header + base64 only. The Library shelf's
    time-sliced cover builder (console._CoverJob) walks the returned
    (count, value) run pairs incrementally across frames; ``decode_moyimg``
    below stays the one-shot decoder. None on any malformed input."""
    try:
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        if w <= 0 or h <= 0 or meta.get("codec") != "rle":
            return None
        packed = _b64_decode(meta["data"])
        if len(packed) & 1:
            return None
        return (w, h, packed)
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None


def decode_moyimg(text):
    """Decode Paint's RLE ``.moyimg`` form into ``(w, h, bytes)``.

    The host/device drawing backends retain their legacy-zlib fallback. Keeping
    the shared-store decoder focused on RLE avoids importing compression support
    merely to load Paint's own persisted artwork.
    """
    try:
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        if w <= 0 or h <= 0 or meta.get("codec") != "rle":
            return None
        packed = _b64_decode(meta["data"])
        out = bytearray()
        if len(packed) & 1:
            return None
        for i in range(0, len(packed), 2):
            count = packed[i]
            value = packed[i + 1]
            if count < 1 or value > 63 or len(out) + count > w * h:
                return None
            out.extend(bytes((value,)) * count)
        if len(out) != w * h:
            return None
        return (w, h, bytes(out))
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None


# --- cover thumbnails (#66 launcher shelf): decoded-crop sidecars -------------
#
# Decoding a 320x240 RLE cover costs 0.5-1.7s interpreted on the T-Deck, so the
# console (CoverCache.cover_for) builds each card-sized crop ONCE and persists it
# Sidecars hold raw indexed pixels: <cart>/thumbs/<prefix><w>x<h>.mct = b"MCT1" + a 4-byte LE
# stamp of the cover blob it was built from (cover_sig) + the w*h pix bytes.
# An edited cover changes the stamp -> the stale thumb is ignored and rebuilt;
# a deleted cart takes its thumbs with it; a re-seed wipe just regenerates.
# Regenerable cache, so: plain writes (no atomic dance), best-effort saves, and
# every reader validates magic + size + stamp before trusting a byte.

THUMBS_DIR = "thumbs"


def cover_sig(text):
    """A cheap content stamp for a cover blob (NOT a hash): its length mixed
    with head+tail character sums -- a paint edit virtually always moves one of
    them. A collision only ever means one stale thumbnail, never a crash."""
    s = 0
    for ch in text[:64]:
        s += ord(ch)
    for ch in text[-64:]:
        s = (s * 3 + ord(ch)) & 0xFFFFFF
    return (len(text) * 2654435761 + s) & 0xFFFFFFFF


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
    stale (the stamp is cover_sig of the cart's SOURCE, so an edit rebuilds)
    or corrupt. Same regenerable-sidecar contract as the cover thumbs."""
    return _load_thumb(path, w, h, sig, "wp")


def save_wallpaper_preview(path, w, h, sig, pix):
    """Persist a rendered wallpaper preview frame. Best-effort, never raises."""
    _save_thumb(path, w, h, sig, pix, "wp")
