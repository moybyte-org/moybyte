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

import gc
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


def _deflate_pieces(pieces):
    """The same stream from a raster handed over PIECE BY PIECE.

    A compressor is a stream on both tiers -- DeflateIO takes repeated writes,
    compressobj repeated compress() calls -- so a writer that can produce its
    raster incrementally never has to hold one. That is what lets the picture
    migration rewrite a 320x240 drawing without the 76,800-byte block whose
    absence, on a store-loaded S3 heap, is what took a boot down."""
    try:
        import deflate
    except ImportError:                  # CPython (host suites, the tools)
        import zlib
        comp = zlib.compressobj(9, zlib.DEFLATED, MOYIMG_WBITS)
        out = bytearray()
        for piece in pieces:
            got = comp.compress(piece)
            if got:
                out.extend(got)
        out.extend(comp.flush())
        return bytes(out)
    import io as _io
    buf = _io.BytesIO()
    stream = deflate.DeflateIO(buf, deflate.ZLIB, MOYIMG_WBITS)
    for piece in pieces:
        stream.write(piece)
    stream.close()
    return buf.getvalue()


def _inflate(raw):
    """A zlib stream -> its bytes, with the window the STREAM declares.

    ONE allocation the size of the whole raster -- 76,800 bytes for a 320x240
    picture. Only a reader that genuinely needs pixels calls this; the cover
    shelf streams instead (`_inflate_chunks`)."""
    try:
        import deflate
    except ImportError:                  # CPython
        import zlib
        return zlib.decompress(raw)
    import io as _io
    return deflate.DeflateIO(_io.BytesIO(raw), deflate.ZLIB).read()


# How much raster the streaming reader holds at once. 1 KB: the piece and the
# runs it packs into are both transient, so the whole read lives in a couple of
# KB against a 76,800-byte raster -- and the per-piece overhead is one native
# call, so a larger chunk buys nothing measurable and costs working set.
_INFLATE_CHUNK = 1024


def _inflate_chunks(raw, chunk=_INFLATE_CHUNK):
    """The same zlib stream, yielded in pieces of at most `chunk` bytes.

    The other half of the compressor seam, and it exists because of a heap. A
    320x240 cover inflates to a CONTIGUOUS 76,800-byte block, and an S3 that has
    loaded a store has hundreds of KB free with no run that size (#66) -- so the
    reader that runs once per cover per session was asking for exactly the block
    the heap refuses first, on the launcher's idle prefetch, where the
    MemoryError took the loop down rather than one thumbnail.

    Both tiers can hand their output back a piece at a time and neither
    advertises it the same way: MicroPython's DeflateIO is a stream, so `read(n)`
    is the whole story, while CPython's decompressobj needs `max_length` to bound
    its OUTPUT -- feeding it bounded INPUT bounds nothing, because a KB of
    deflate expands to a megabyte of flat colour, which is exactly the picture a
    kid draws first."""
    try:
        import deflate
    except ImportError:                  # CPython
        import zlib
        d = zlib.decompressobj()
        pos = 0
        n = len(raw)
        while True:
            if d.unconsumed_tail:
                src = d.unconsumed_tail
            elif pos < n:
                src = raw[pos:pos + chunk]
                pos += chunk
            else:
                src = b""
            out = d.decompress(src, chunk)
            if out:
                yield out
            elif not src:
                return
        return
    import io as _io
    stream = deflate.DeflateIO(_io.BytesIO(raw), deflate.ZLIB)
    while True:
        out = stream.read(chunk)
        if not out:
            return
        yield out


def _blob_header(text):
    """A ``.moyimg``'s ``(w, h, zlib stream)``, or None when it is not one.

    Both readers share it, so the header is parsed once -- and the base64 STRING
    (as big again as the stream it carries) is dropped HERE rather than held
    alive across the inflate. MemoryError is the one exception that goes on
    through: "this board could not read it just now" and "this is not a picture"
    are different answers, and only the second one is permanent."""
    try:
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        if w <= 0 or h <= 0:
            return None
        return (w, h, _b64_decode(meta["data"]))
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 -- a corrupt or foreign blob is not a picture
        return None


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

    None rather than a raise: a corrupt or foreign blob is treated as an absent
    picture by every caller, on every tier. MemoryError is the exception, and
    deliberately so -- it is the one failure that says nothing about the file.
    Swallowed as None it becomes "your drawing is gone", which the Paint and
    `image()` callers would then cache and act on; raised, it is a caller's
    choice to skip this one picture and try again later, which is what the cover
    shelf does.

    The whole raster is still built here, because a caller that asks for pixels
    needs pixels -- and the retry after a collect is `moy_carts._read_main`'s
    idiom for the same reason: it is one big CONTIGUOUS allocation on a heap that
    fragments over a session, so the run that serves it routinely exists and is
    merely not free yet. Costing nothing when the heap is fine is the point of
    doing it on the failure rather than before the attempt."""
    got = _blob_header(text)
    if got is None:
        return None
    w, h, raw = got
    got = None                           # drop the tuple's hold on the stream
    try:
        try:
            pix = _inflate(raw)
        except MemoryError:
            gc.collect()
            pix = _inflate(raw)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None
    if len(pix) != w * h:
        return None
    return (w, h, pix)


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


def _run_bytes(count, value):
    """A run of `count` `value`s as 255-capped ``(count, value)`` pairs.

    Byte-identical to what a whole-raster scan produces for the same run, which
    is the property the streaming reader below exists to keep."""
    if count <= 0:
        return b""
    if count <= 255:
        return bytes((count, value))
    out = bytearray()
    while count > 255:
        out.append(255)
        out.append(value)
        count -= 255
    if count:
        out.append(count)
        out.append(value)
    return bytes(out)


def moyimg_runs(text):
    """A ``.moyimg`` as ``(w, h, packed_run_bytes)``, or None.

    What the Library shelf caches per cart (cover_cache._runs_load): the
    size-INDEPENDENT half of a cover build, ~15KB against the 77KB raster whose
    caching was measured and rejected. `decode_moyimg` stays the one-shot
    decoder for everything that wants pixels.

    STREAMED (2026-09-07), and that is the whole point of it. This runs once per
    cover per session on the launcher's idle prefetch, and the version that
    inflated the raster whole asked a fragmented S3 heap for 76,800 contiguous
    bytes to derive 15KB of runs from -- the allocation that heap refuses first,
    on the tick with the least right to raise. Now at most a KB of raster is in
    hand at a time and the runs accumulate as they are read.

    A run that spans a piece boundary is what makes this a merge rather than a
    concatenation, and the merge is why the output is byte-identical to the
    whole-raster one: the OPEN run is held as a plain count instead of being
    written down, so it is packed once, when it ends, at whatever length it
    reached. Everything between the first and last run of a piece is already
    final and goes out in one copy. The pieces are joined at the end rather than
    appended into a growing buffer, so the one big contiguous allocation this
    makes is the result itself, at exactly its size.

    A picture STILL IN THE RETIRED CODEC reads as absent here (the stream it
    carries is not a zlib one), which is what the migration below leans on: an
    unrewritten drawing draws the placeholder and never an exception."""
    got = _blob_header(text)
    if got is None:
        return None
    w, h, raw = got
    got = None
    parts = []
    value = -1                # the run left OPEN across a piece boundary
    count = 0                 # ...and its length, which may exceed 255
    seen = 0
    try:
        for piece in _inflate_chunks(raw):
            seen += len(piece)
            packed = pack_runs(piece)
            n = len(packed)
            i = 0
            # A 255-capped run arrives as several pairs, so the leading ones are
            # walked, not peeked at.
            while i < n and packed[i + 1] == value:
                count += packed[i]
                i += 2
            if i >= n:
                continue                   # this whole piece continued one run
            if count > 0:
                parts.append(_run_bytes(count, value))
            value = packed[n - 1]
            count = 0
            j = n
            while j > i and packed[j - 1] == value:
                count += packed[j - 2]
                j -= 2
            if j > i:
                parts.append(packed[i:j])
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 -- a corrupt or retired blob is not a picture
        return None
    if seen != w * h:
        return None
    if count > 0:
        parts.append(_run_bytes(count, value))
    return (w, h, b"".join(parts))


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
