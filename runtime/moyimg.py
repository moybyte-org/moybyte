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
# The compressor is native/moy_store's (`moy_ufiles.deflate`) on a board that
# carries it. Without it the seam is the two tiers the packed seed roster reads
# through (`moy_carts._packed_stream`): MicroPython replaced `zlib` with
# `deflate` in v1.21, so a board has DeflateIO and no zlib, and CPython has
# zlib and no deflate. Unlike the roster's raw stream this one is standard ZLIB-framed,
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


def _board_deflate():
    """The C compressor (`moy_ufiles.deflate`, native/moy_store) where the
    image carries it. MicroPython's own DeflateIO compares every position
    against its whole window, and on a flat picture every candidate matches to
    the end: a blank 320x240 page holds the VM for longer than the task
    watchdog allows. The C one is a bounded hash-chain matcher."""
    try:
        import moy_ufiles
    except ImportError:
        return None
    return getattr(moy_ufiles, "deflate", None)


def _deflate(data):
    """`data` -> a zlib stream at MOYIMG_WBITS: the board's C compressor, then
    `deflate` (a board without one), then CPython's zlib. `deflate` before
    `zlib`: the browser build freezes a decompress-only `zlib` shim that would
    answer the import and then have no `compress`."""
    fast = _board_deflate()
    if fast is not None:
        return fast(data)
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
    """The same stream from a raster handed over PIECE BY PIECE. The C
    compressor takes the raster whole, so on that path the pieces are joined
    first; the streaming compressors take them as they come."""
    fast = _board_deflate()
    if fast is not None:
        raster = bytearray()
        for piece in pieces:
            raster.extend(piece)
        return fast(raster)
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


def _inflate(raw, n):
    """A zlib stream -> its `n` bytes, with the window the STREAM declares, or
    None when it holds another count.

    ONE allocation the size of the whole raster -- 76,800 bytes for a 320x240
    picture -- filled a piece at a time: DeflateIO's read() of a whole stream
    regrows its result as it goes (4.2 s for a 614 KB picture on an S3, against
    0.3 s into the buffer). Only a reader that genuinely needs pixels calls
    this."""
    try:
        import deflate
    except ImportError:                  # CPython
        import zlib
        out = zlib.decompress(raw)
        return out if len(out) == n else None
    import io as _io
    s = deflate.DeflateIO(_io.BytesIO(raw), deflate.ZLIB)
    out = bytearray(n)
    mv = memoryview(out)
    got = 0
    while got < n:
        k = s.readinto(mv[got:got + 4096])
        if not k:
            return None
        got += k
    if s.read(1):
        return None
    return out


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
            pix = _inflate(raw, w * h)
        except MemoryError:
            gc.collect()
            pix = _inflate(raw, w * h)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 -- a corrupt drawing is treated as absent
        return None
    if pix is None:
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


def bytes_sig(head, tail, n):
    """`text_sig` of a blob known by its first and last 64 bytes and its
    length -- the same stamp as text_sig of the text, without reading the
    text."""
    s = 0
    for b in head[:64]:
        s += b
    for b in tail[-64:]:
        s = (s * 3 + b) & 0xFFFFFF
    return (n * 2654435761 + s) & 0xFFFFFFFF


def file_sig(f):
    """`bytes_sig` of the open binary file `f`, which is left at its start."""
    n = f.seek(0, 2)
    f.seek(0)
    head = f.read(64)
    f.seek(n - 64 if n > 64 else 0)
    tail = f.read(64)
    f.seek(0)
    return bytes_sig(head, tail, n)


def cover_indices(src, sw, sh, dw, dh, out=None):
    """Nearest-neighbor cover crop: source cropped centered to the target
    aspect, then sampled to exactly dw x dh (the backdrop's and a copy's formula).

    `out` is a caller-supplied dw*dh buffer (the streamed reader's screen,
    off-heap); None makes a bytearray. The formula is native/moy_store/moy_img.h's
    streamed reader's, which samples as it reads."""
    if sw * dh > sh * dw:
        crop_h = sh
        crop_w = max(1, sh * dw // dh)
        sx0, sy0 = (sw - crop_w) // 2, 0
    else:
        crop_w = sw
        crop_h = max(1, sw * dh // dw)
        sx0, sy0 = 0, (sh - crop_h) // 2
    if out is None:
        out = bytearray(dw * dh)
    for y in range(dh):
        sy = sy0 + y * crop_h // dh
        so = sy * sw
        do = y * dw
        for x in range(dw):
            out[do + x] = src[so + sx0 + x * crop_w // dw]
    return out


# --- the streamed reader -----------------------------------------------------
#
# A picture read from its FILE and cover-sampled into a screen, the form
# native/moy_store/moy_img.h's streamed reader takes (its rules are there):
# `data` ending within the first HEAD bytes, at most HEAD bytes after its
# value, its base64 bare and padded. This is that reader's reference, rule for rule; a
# board reads through the C (`moy_ufiles.cover_image`), which holds one source
# row and the inflater's window instead of the file and the raster this does.

HEAD = 256
_WS = b" \t\n\r"
_ALPHA = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _skip_ws(buf, i):
    while i < len(buf) and buf[i] in _WS:
        i += 1
    return i


def cover_stream(f, dst, dw, dh, max_w, max_h):
    """The picture binary file `f` reads, cover-sampled into `dst` (dw*dh
    indices, the formula `file_widgets.cover_indices` is): its (w, h), or
    None when it is not a picture in the streamed form or is larger than
    max_w x max_h. ValueError for a size `dst` cannot hold."""
    dw, dh, max_w, max_h = int(dw), int(dh), int(max_w), int(max_h)
    if min(dw, dh, max_w, max_h) <= 0 or dw * dh > len(dst):
        raise ValueError("an argument the store refuses")
    buf = b""
    while len(buf) < HEAD:
        got = f.read(HEAD - len(buf))
        if not got:
            break
        buf += got
    i = buf.find(b'"data"')
    if i < 0:
        return None
    head = buf[:i].rstrip(_WS)
    if head.endswith(b","):
        head = head[:-1]
    elif not head.endswith(b"{"):
        return None
    try:
        meta = json.loads((head + b"}").decode())
        w = int(meta["w"])
        h = int(meta["h"])
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 -- a foreign head is not a picture
        return None
    if type(meta) is not dict or not (0 < w <= 0xFFFF and 0 < h <= 0xFFFF):
        return None
    if w > max_w or h > max_h:
        return None
    rest = buf[i + 6:] + f.read()
    j = _skip_ws(rest, 0)
    if rest[j:j + 1] != b":":
        return None
    j = _skip_ws(rest, j + 1)
    if rest[j:j + 1] != b'"':
        return None
    q = rest.find(b'"', j + 1)
    if q < 0:
        return None
    value = rest[j + 1:q]
    run = value.rstrip(b"=")
    pads = len(value) - len(run)
    n = len(run)
    for c in run:
        if c not in _ALPHA:
            return None
    if pads > 2 or n % 4 == 1 or pads != (4 - n % 4) % 4:
        return None
    tail = rest[q + 1:]
    if len(tail) > HEAD:
        return None
    k = _skip_ws(tail, 0)
    if tail[k:k + 1] == b"}":
        if _skip_ws(tail, k + 1) != len(tail):
            return None
    elif tail[k:k + 1] == b",":
        try:                             # a copy's provenance
            more = json.loads((b"{" + tail[k + 1:]).decode())
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 -- not an object's members
            return None
        if type(more) is not dict or not more:
            return None
    else:
        return None
    try:
        pix = _inflate(_b64_decode(value), w * h)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 -- a corrupt stream is not a picture
        return None
    if pix is None:
        return None
    cover_indices(pix, w, h, dw, dh, dst)
    return (w, h)
