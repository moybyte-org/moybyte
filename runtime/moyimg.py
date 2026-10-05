"""The moyimg codec: the ``moyimg-v1`` indexed-bitmap blob, and the content
stamp the store's sidecars are keyed on.

encode/decode_moyimg is ONE format, the one every user picture and a cart's
images/ are written in. A cart's COVER is not one of them: it is `cover.png`
(runtime/cover_png.py, SPEC.md 3.6). `text_sig` is the cheap stamp a history
sidecar and a preview sidecar both validate against.

The store's half of what was moy_image.py: this file crosses with the store
(docs/native_kernel_2026-09.md, sprint 1b); `Image` and the wallpaper-preview
sidecar stay in moy_image.py. MicroPython-safe (json + binascii + deflate).
"""

import gc
import json

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
# and big flat strings are what the S3 heap fails on first. There is no RLE
# READER anywhere: the decoders below speak one format, and a blob still in the
# retired codec reads as an absent picture (None), which every caller on every
# tier already draws as a placeholder.
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
    picture. Only a reader that genuinely needs pixels calls this."""
    try:
        import deflate
    except ImportError:                  # CPython
        import zlib
        return zlib.decompress(raw)
    import io as _io
    return deflate.DeflateIO(_io.BytesIO(raw), deflate.ZLIB).read()


def _blob_header(text):
    """A ``.moyimg``'s ``(w, h, zlib stream)``, or None when it is not one.

    The base64 STRING (as big again as the stream it carries) is dropped HERE
    rather than held alive across the inflate. MemoryError is the one exception that goes on
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
    choice to skip this one picture and try again later.

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


def text_sig(text):
    """A cheap content stamp for a text blob (NOT a hash): its length mixed
    with head+tail character sums -- an edit virtually always moves one of
    them. A collision only ever means one stale sidecar, never a crash."""
    s = 0
    for ch in text[:64]:
        s += ord(ch)
    for ch in text[-64:]:
        s = (s * 3 + ord(ch)) & 0xFFFFFF
    return (len(text) * 2654435761 + s) & 0xFFFFFFFF
