"""A cart's cover, `cover.png` (SPEC.md 3.6): read it, and write one.

The profile is native/moy_png/moy_png.h's header: 128 x 128, bit depth 8,
colour type 3 or 2, not interlaced, no tRNS, at most 65,536 bytes. A file
outside it, or one whose data does not decode, is not a cover; whoever asked
draws as if there were none.

TWO READERS, ONE ANSWER. The boards and the browser console decode with the
native `moy_png` module; the host decodes with `Reference` below, which is the
same rules in Python over CPython's zlib. `Job` picks whichever this build
has, and tests/test_cover_png.py holds the two to each other and to moy-spec's
conformance vectors (tests/cover_vectors/). Both stream: the zlib data is
inflated a row at a time, and a decode can stop after any row and go on later.

    Job(data, div, fmt, pal, work)  a decode; .ok, then .rows(out, n) -> 1/0/-1
    decode(data, div, fmt, pal)     the whole decode at once: bytes, or None
    out_size(div, fmt)              how many bytes a decode writes
    encode_indexed(indices, pal)    128 x 128 palette indices -> a cover.png
    moy64()                         the console palette, as R, G, B bytes

The output is (128 / div) ** 2 pixels -- div 1 the picture, a larger power of
two its box-filtered reduction -- as RGB888, RGB565 (little-endian),
RGB565_SW (high byte first) or INDEX (the nearest entry of `pal`).

MicroPython-safe: the reference's inflate is imported where it runs, and a
board never reaches it.
"""

try:
    import moy_png as _native
except ImportError:
    _native = None

SIDE = 128
MAX_BYTES = 65536
WORK = 38912
RGB888 = 0
RGB565 = 1
RGB565_SW = 2
INDEX = 3
_FMT_BYTES = (3, 2, 2, 1)
_SIG = b"\x89PNG\r\n\x1a\n"


def native():
    """The native reader, or None where this build has none (the host)."""
    return _native


_PAL = []


def moy64():
    """MOY64 as R, G, B bytes -- the console palette a cover is mapped to for
    an INDEX decode and written in by `encode_indexed`: the canvas module's
    baked twin (`device_canvas.MOY64_RGB`), which every tier carries."""
    if not _PAL:
        try:
            import device_canvas as dc
        except ImportError:  # pragma: no cover - host fallback
            from device import device_canvas as dc
        _PAL.append(dc.MOY64_RGB)
    return _PAL[0]


def out_size(div, fmt):
    side = SIDE // div
    return side * side * _FMT_BYTES[fmt]


def _div_ok(div):
    return 1 <= div <= SIDE and (div & (div - 1)) == 0


def _be32(d, p):
    return (d[p] << 24) | (d[p + 1] << 16) | (d[p + 2] << 8) | d[p + 3]


def structure(data):
    """(colour type, PLTE bytes or None, [(start, end) of each IDAT payload])
    when the file's chunks are a cover's, else None. The walk the reference
    reader makes: through IEND, CRCs unread."""
    n = len(data)
    if n > MAX_BYTES or n < 8 or bytes(data[:8]) != _SIG:
        return None
    p = 8
    ctype = 0
    plte = None
    spans = []
    idat_bytes = 0
    while True:
        if p + 8 > n:
            return None                     # the file ends before IEND
        ln = _be32(data, p)
        tag = bytes(data[p + 4:p + 8])
        if p + 12 + ln > n:
            return None                     # the chunk runs past the end
        at = p + 8
        if not ctype:
            if tag != b"IHDR" or ln != 13:
                return None
            h = data[at:at + 13]
            if (_be32(h, 0) != SIDE or _be32(h, 4) != SIDE or h[8] != 8
                    or h[9] not in (2, 3) or h[10] or h[11] or h[12]):
                return None
            ctype = h[9]
        elif tag == b"IDAT":
            spans.append((at, at + ln))
            idat_bytes += ln
        elif tag == b"PLTE":
            if ctype == 3:
                if idat_bytes or ln % 3 or ln < 3 or ln > 768:
                    return None
                plte = bytes(data[at:at + ln])
        elif tag == b"tRNS":
            return None
        elif tag == b"IEND":
            break
        elif not (tag[0] & 0x20):
            return None                     # a critical chunk not a cover's
        p += 12 + ln
    if ctype == 3 and plte is None:
        return None
    return ctype, plte, spans


def _nearest(pal, r, g, b):
    best = 0
    bd = 1 << 30
    for i in range(len(pal) // 3):
        dr = r - pal[3 * i]
        dg = g - pal[3 * i + 1]
        db = b - pal[3 * i + 2]
        dd = dr * dr + dg * dg + db * db
        if dd < bd:
            bd = dd
            best = i
            if not dd:
                break
    return best


class _Bad(Exception):
    pass


class _Zlib:
    """CPython's zlib, fed the IDAT payloads as it asks for them: the host's
    inflater, and zlib's verdict on every stream is the one the native reader
    is held to (moy_png.h)."""

    def __init__(self, zlib, data, spans):
        self._d = zlib.decompressobj()
        self._data = data
        self._spans = spans
        self._si = 0
        self._tail = b""

    def _next(self):
        if self._tail:
            src = self._tail
            self._tail = b""
            return src
        while self._si < len(self._spans):
            a, b = self._spans[self._si]
            self._si += 1
            if b > a:
                return self._data[a:b]
        return None

    def read(self, want):
        d = self._d
        parts = []
        have = 0
        while have < want and not d.eof:
            src = self._next()
            if src is None:
                break
            got = d.decompress(src, want - have)
            self._tail = d.unconsumed_tail
            if got:
                parts.append(got)
                have += len(got)
        return b"".join(parts)

    def finish(self):
        """The picture is in: the stream must end here, with nothing more."""
        d = self._d
        while not d.eof:
            src = self._next()
            if src is None:
                break
            if d.decompress(src, 1):
                raise _Bad("data past the picture")
            self._tail = d.unconsumed_tail
        if d.flush() or not d.eof:
            raise _Bad("not one complete zlib stream")


class _Deflate:
    """MicroPython's `deflate` over the IDAT payloads joined: what a build
    without the native reader decodes with. It reads every cover the profile
    allows; it is not held to zlib's verdict on a DAMAGED stream, which only
    the native reader and `_Zlib` are."""

    def __init__(self, deflate, data, spans):
        import io
        idat = b"".join(bytes(data[a:b]) for a, b in spans)
        self._s = deflate.DeflateIO(io.BytesIO(idat), deflate.ZLIB)

    def read(self, want):
        parts = []
        have = 0
        while have < want:
            got = self._s.read(want - have)
            if not got:
                break
            parts.append(got)
            have += len(got)
        return b"".join(parts)

    def finish(self):
        if self._s.read(1):
            raise _Bad("data past the picture")


def _inflater(data, spans):
    try:
        import zlib
        if hasattr(zlib, "decompressobj"):
            return _Zlib(zlib, data, spans)
    except ImportError:
        pass
    import deflate
    return _Deflate(deflate, data, spans)


class Reference:
    """The cover reader in Python: `begin` then `rows`, the native module's
    two calls, rule for rule. The host's reader, and the C's referee."""

    def begin(self, data, div=1, fmt=RGB888, pal=None):
        if not _div_ok(div) or not 0 <= fmt <= 3:
            raise ValueError("cover_png: bad div or fmt")
        if fmt == INDEX and (pal is None or not 3 <= len(pal) <= 768):
            raise ValueError("cover_png: INDEX needs a palette of 1-256 entries")
        self._ok = False
        st = structure(data)
        if st is None:
            return False
        self._ctype, plte, self._spans = st
        self._data = data
        self._div = div
        self._fmt = fmt
        self._plte = plte or b""
        self._npal = len(self._plte) // 3
        self._bpp = 3 if self._ctype == 2 else 1
        self._map = bytes(pal) if fmt == INDEX else b""
        self._memo = {}
        self._z = _inflater(data, self._spans)
        self._prev = bytearray(SIDE * self._bpp)
        self._row = 0
        self._orow = 0
        self._sums = [0] * ((SIDE // div) * 3)
        self._done = False
        self._ok = True
        self._lut = None
        if self._ctype == 3:
            self._lut = [self._plte[3 * v:3 * v + 3] for v in range(self._npal)]
        return True

    def rows(self, out, n):
        if not self._ok:
            return -1
        if self._done:
            return 1
        oside = SIDE // self._div
        if n <= 0 or n > oside:
            n = oside
        target = min(self._orow + n, oside)
        try:
            while self._orow < target:
                self._one_row(out)
            if self._orow < oside:
                return 0
            self._z.finish()
        except Exception:  # noqa: BLE001 -- zlib's error, or ours: not a cover
            self._ok = False
            return -1
        self._done = True
        return 1

    def _one_row(self, out):
        bpp = self._bpp
        stride = SIDE * bpp
        raw = self._z.read(stride + 1)
        if len(raw) != stride + 1:
            raise _Bad("short")
        ft = raw[0]
        line = bytearray(raw[1:])
        prev = self._prev
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                if i >= bpp:
                    a = line[i - bpp]
                    c = prev[i - bpp]
                else:
                    a = c = 0
                b = prev[i]
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif ft != 0:
            raise _Bad("filter type %d" % ft)
        self._prev = line
        self._emit(line, out)
        self._row += 1
        if self._row % self._div == 0:
            self._orow += 1

    def _rgb(self, line):
        """The row as R, G, B bytes."""
        if self._ctype == 2:
            return line
        npal = self._npal
        if max(line) >= npal:
            raise _Bad("an index past the PLTE")
        lut = self._lut
        return b"".join(lut[v] for v in line)

    def _put(self, out, o, r, g, b):
        fmt = self._fmt
        if fmt == RGB888:
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
        elif fmt == INDEX:
            key = (r << 16) | (g << 8) | b
            v = self._memo.get(key)
            if v is None:
                v = _nearest(self._map, r, g, b)
                self._memo[key] = v
            out[o] = v
        else:
            w = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            if fmt == RGB565:
                out[o] = w & 255
                out[o + 1] = w >> 8
            else:
                out[o] = w >> 8
                out[o + 1] = w & 255

    def _emit(self, line, out):
        div = self._div
        fmt = self._fmt
        ob = _FMT_BYTES[fmt]
        rgb = self._rgb(line)
        if div == 1:
            o = self._row * SIDE * ob
            if fmt == RGB888:
                out[o:o + SIDE * 3] = rgb
                return
            if fmt == INDEX and self._ctype == 3:
                memo = {}
                for x in range(SIDE):
                    v = line[x]
                    m = memo.get(v)
                    if m is None:
                        m = _nearest(self._map, rgb[3 * x], rgb[3 * x + 1],
                                     rgb[3 * x + 2])
                        memo[v] = m
                    out[o + x] = m
                return
            for x in range(SIDE):
                self._put(out, o + x * ob, rgb[3 * x], rgb[3 * x + 1],
                          rgb[3 * x + 2])
            return
        sums = self._sums
        for x in range(SIDE):
            t = 3 * (x // div)
            sums[t] += rgb[3 * x]
            sums[t + 1] += rgb[3 * x + 1]
            sums[t + 2] += rgb[3 * x + 2]
        if (self._row + 1) % div:
            return
        ow = SIDE // div
        area = div * div
        half = area // 2
        o = (self._row // div) * ow * ob
        for x in range(ow):
            t = 3 * x
            self._put(out, o + x * ob, (sums[t] + half) // area,
                      (sums[t + 1] + half) // area, (sums[t + 2] + half) // area)
            sums[t] = sums[t + 1] = sums[t + 2] = 0


class Job:
    """One decode, on the native reader where the build has one.

    `work` is the caller's WORK-byte buffer, kept and reused across covers;
    the native reader needs it and the reference ignores it. `ok` is False
    for a file that is not a cover; `rows(out, n)` then steps the decode, and
    `data` and `work` must stay alive and unchanged until it answers 1 or -1."""

    def __init__(self, data, div=1, fmt=RGB888, pal=None, work=None):
        self.data = data
        self.work = work
        if _native is not None and work is not None:
            self._ref = None
            self.ok = _native.begin(work, data, div, fmt, pal)
        else:
            self._ref = Reference()
            self.ok = self._ref.begin(data, div, fmt, pal)

    def rows(self, out, n):
        if not self.ok:
            return -1
        if self._ref is None:
            return _native.rows(self.work, self.data, out, n)
        return self._ref.rows(out, n)


def decode(data, div=1, fmt=RGB888, pal=None, work=None):
    """The whole decode at once: out_size(div, fmt) bytes, or None when `data`
    is not a cover."""
    if _native is not None and work is None:
        work = bytearray(WORK)
    job = Job(data, div, fmt, pal, work)
    if not job.ok:
        return None
    out = bytearray(out_size(div, fmt))
    return bytes(out) if job.rows(out, 0) == 1 else None


# --- writing one ---------------------------------------------------------------


def _chunk(tag, body):
    try:
        from binascii import crc32
    except ImportError:  # pragma: no cover -- every target ships binascii
        crc32 = None
    crc = crc32(body, crc32(tag)) & 0xFFFFFFFF if crc32 is not None else 0
    n = len(body)
    return (bytes(((n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255, n & 255))
            + tag + body
            + bytes(((crc >> 24) & 255, (crc >> 16) & 255, (crc >> 8) & 255,
                     crc & 255)))


def encode_indexed(indices, palette):
    """128 x 128 palette indices (row-major) and their palette (R, G, B bytes,
    1-256 entries) -> a cover.png in the profile: colour type 3, every row
    filter 0, the zlib stream moyimg writes pictures with."""
    if len(indices) != SIDE * SIDE:
        raise ValueError("a cover is %dx%d" % (SIDE, SIDE))
    if len(palette) % 3 or not 3 <= len(palette) <= 768:
        raise ValueError("a cover's palette has 1-256 entries")
    try:
        from moyimg import _deflate_pieces
    except ImportError:  # pragma: no cover - host fallback when not yet aliased
        from runtime.moyimg import _deflate_pieces
    mv = memoryview(bytes(indices))
    zero = b"\0"

    def pieces():
        for y in range(SIDE):
            yield zero
            yield mv[y * SIDE:(y + 1) * SIDE]

    ihdr = bytes((0, 0, 0, SIDE, 0, 0, 0, SIDE, 8, 3, 0, 0, 0))
    return (_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"PLTE", bytes(palette))
            + _chunk(b"IDAT", _deflate_pieces(pieces()))
            + _chunk(b"IEND", b""))
