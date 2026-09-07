# The portable moyimg codec + cover-thumb sidecars, extracted from moy_carts.py
# (which re-exports every name here, so store call sites and tests are unchanged).
#
# encode/decode_moyimg: the ``moyimg-v1`` indexed-bitmap blob -- ONE format.
# moyimg_runs: the same blob as (count, value) runs, for the cover builder.
# The wallpaper-preview sidecar cache (the Appearance monitor's computed frame)
# reads instead of re-rendering the cart -- regenerable, plain writes,
# readers validate magic + size + stamp.
#
# MicroPython-safe (json + binascii + deflate; _mkdir from the moy_fs leaf).

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


# --- the one wire form -------------------------------------------------------
#
# A ``.moyimg`` is a JSON header {format, w, h, data} where `data` is base64 of
# a ZLIB stream of w*h MOY64 palette indices, one byte per pixel. ONE
# format, since 2026-09-07: Paint used to write a second, uncompressed RLE codec
# (`codec: "rle"`) because saving needed no compressor, and measured on every
# shipped image that form is 2.5-10x BIGGER -- a 320x240 cover 72 KB against 26,
# and big flat strings are what the S3 heap fails on first. The RLE reader
# survives for exactly one generation, inside `moy_carts.migrate_images`, and
# nowhere else: no live decoder speaks two formats.
#
# The compressor is the same two-tier seam the packed seed roster reads through
# (`moy_carts._packed_stream`): MicroPython replaced `zlib` with `deflate` in
# v1.21, so a board has DeflateIO and no zlib, and CPython has zlib and no
# deflate. Unlike the roster's raw stream this one is standard ZLIB-framed,
# which is what makes the pre-2026-09 assets already in this format rather than
# a legacy of it -- nothing had to be rewritten to make them the one form.
#
# WBITS is pinned on the WRITE side only: a zlib header carries its own window
# size, so a reader that asks for none takes the stream's. That is what lets a
# 15-bit stream written by an old CPython tool and a 12-bit one written by Paint
# on a board read identically on every tier. 12 is measured, not chosen: across
# every image this repo ships it lands within 0.4% of the best ratio any window
# reaches (92,081 B total against 91,745 at 13 and 92,321 at 15) for a 4 KB
# window instead of 32 KB -- and the window is a live heap allocation on a board
# that is compressing a kid's drawing.
MOYIMG_WBITS = 12


def _deflate(data):
    """`data` -> a zlib stream at MOYIMG_WBITS. `deflate` first: it is the one
    a board has, and the browser build freezes a decompress-only `zlib` shim
    that would answer the import and then have no `compress`."""
    try:
        import deflate
    except ImportError:                  # CPython (host suites, the tools)
        import zlib
        comp = zlib.compressobj(9, zlib.DEFLATED, MOYIMG_WBITS)
        return comp.compress(data) + comp.flush()
    import io as _io
    buf = _io.BytesIO()
    stream = deflate.DeflateIO(buf, deflate.ZLIB, MOYIMG_WBITS)
    stream.write(data)
    stream.close()                       # the deflate tail; `buf` stays open
    return buf.getvalue()


def _inflate(raw):
    """A zlib stream -> its bytes, with the window the STREAM declares."""
    try:
        import deflate
    except ImportError:                  # CPython
        import zlib
        return zlib.decompress(raw)
    import io as _io
    return deflate.DeflateIO(_io.BytesIO(raw), deflate.ZLIB).read()


def encode_moyimg(width, height, indices):
    """Encode an indexed bitmap as a portable ``moyimg-v1`` blob."""
    w = int(width)
    h = int(height)
    if w <= 0 or h <= 0 or len(indices) != w * h:
        raise ValueError("bad artwork size")
    if not isinstance(indices, (bytes, bytearray, memoryview)):
        indices = bytes(bytearray(indices))   # a list of ints from a cart
    return json.dumps({
        "format": "moyimg-v1", "w": w, "h": h,
        "data": _b64_encode(_deflate(indices)),
    })


def decode_moyimg(text):
    """Decode a ``.moyimg`` into ``(w, h, bytes)``, or None when it is not one.

    None rather than a raise all the way down: a corrupt or foreign blob is
    treated as an absent picture by every caller, on every tier."""
    try:
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        if w <= 0 or h <= 0:
            return None
        pix = _inflate(_b64_decode(meta["data"]))
        if len(pix) != w * h:
            return None
        return (w, h, pix)
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None


def pack_runs(pix):
    """An indexed raster -> ``(count, value)`` byte pairs, count 1..255.

    The mirror of moy_gfx's `decode_runs`, and native for the same reason: a
    320x240 walk costs 0.5-1.7s interpreted on a board, which is the whole
    history of the time-sliced cover builder. The Python body below is the host
    path and the fallback, and produces identical bytes."""
    native = _encode_runs()
    if native is not None:
        got = native(pix)
        if got is not None:
            return got
    out = bytearray()
    pos = 0
    total = len(pix)
    while pos < total:
        value = pix[pos] & 63
        count = 1
        while pos + count < total and count < 255 \
                and (pix[pos + count] & 63) == value:
            count += 1
        out.append(count)
        out.append(value)
        pos += count
    return bytes(out)


_ENCODE_RUNS = False       # False = not looked up yet; None = this build has none


def _encode_runs():
    """moy_gfx.encode_runs when this build has one. Looked up LAZILY: moy_image
    is staged to targets with no compositor at all (the headless Zero), and on
    the host `moy_gfx` is not an importable module -- gfx_binding is."""
    global _ENCODE_RUNS
    if _ENCODE_RUNS is False:
        try:
            import moy_gfx
            _ENCODE_RUNS = getattr(moy_gfx, "encode_runs", None)
        except ImportError:
            _ENCODE_RUNS = None
    return _ENCODE_RUNS


def moyimg_runs(text):
    """A ``.moyimg`` as ``(w, h, packed_run_bytes)``, or None.

    What the Library shelf caches per cart (cover_cache._runs_load): the
    size-INDEPENDENT half of a cover build, ~15KB against the 77KB raster whose
    caching was measured and rejected. `decode_moyimg` stays the one-shot
    decoder for everything that wants pixels.

    The runs used to be READ off the file and are derived from the raster now,
    which costs a 77KB transient per cover LOAD. Accepted, not overlooked: a
    load happens once per cart per session, on an idle prefetch frame, beside a
    ~47ms flash read on the same call -- where the 116ms this size of allocation
    measured on P4 glass was a per-BUILD cost, every cover at every size, which
    is why _CoverJob reuses an off-heap scratch and this does not. If it ever
    shows up, that scratch is the lever."""
    got = decode_moyimg(text)
    if got is None:
        return None
    return (got[0], got[1], pack_runs(got[2]))


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
