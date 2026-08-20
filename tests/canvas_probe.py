"""Count PIXELS, not bytes, when a test asks "did this actually draw?".

WHY THIS EXISTS

The stock idiom for "the screen is not blank" was `len(set(cv.buf)) > 1`: one
distinct byte in the buffer means one flat colour, more than one means something
drew. That reasoning is only sound while a pixel IS a byte, which was true of
the deleted indexed host canvas (one MOY64 palette index per byte) and
stops being true the moment a canvas stores RGB565 -- because then a COMPLETELY
BLANK frame holds two distinct byte values, the high and the low half of the one
background colour, and `len(set(...)) > 1` passes on an empty screen.

That is the worst shape a test can have: green, and testing nothing. It is the
same failure as a comparison whose fixture cannot express a difference, and the
whole point of fixing it BEFORE the format changes is that afterwards there is
no red test to tell you which assertions went hollow.

So every "it drew" assertion goes through here, and here reads the buffer at the
canvas's real pixel width.

HOW THE FORMAT IS DETECTED (in order; first answer wins)

1. An explicit `bytes_per_pixel` / `BYTES_PER_PIXEL` attribute, if a canvas ever
   grows one. Nothing has it today -- it is first so that a backend can always
   settle the question itself rather than be guessed at.
2. The buffer's SIZE against the surface: on a full-surface canvas the buffer is
   exactly `w * h * bytes_per_pixel`, so an exact match on 1x, 2x or 3x is
   decisive. Deliberately exact and deliberately restricted to full surfaces: a
   VIEWPORT canvas (#155) keeps the whole parent buffer behind a smaller w/h, so
   a modulo test there can land on the wrong multiple and answer confidently
   with a lie.
3. Attribute shape: a canvas publishing an indexed buffer calls it `.buf`, while
   `device_canvas.DeviceCanvas` -- which every tier now runs, the host included
   since `runtime/canvas.py` was deleted -- keeps its RGB565 framebuffer private
   as `._buf` and has no `.buf` at all.

RGB565 words are read native-endian. Endianness cannot change any count here --
byte-pair to word is a bijection either way -- and no caller compares a pixel
value against a literal colour, so nothing depends on which half comes first.

The raw-buffer entry points (`distinct_pixels_in`, `painted_pixels_in`) take the
pixel width explicitly, for buffers that are not canvases at all: `rgb888()` /
`to_rgb888()` output is 3 bytes per pixel and always was.
"""

from collections import Counter


# --- naming a colour --------------------------------------------------------

def word_of(index, cv=None):
    """The value one pixel of MOY64 palette `index` holds in the buffer.

    The replacement for a literal palette index in an assertion:
    `cv.buf[y * w + x] == 8` only ever meant "that pixel is orange" because the
    buffer stored indices. It stores RGB565 words now, so the index has to be
    resolved the way the canvas resolves it -- through the canvas's OWN wire
    table, since a cart palette (SPEC.md 3.1) rewrites it and the stock table
    would then name a colour that is not on screen.

    Prefer `cv.pix(x, y)` where the coordinates are handy: that reads back an
    INDEX on every tier (through the same reverse LUT) and carries camera and
    viewport, so an assertion written that way says what it means with no
    conversion at all. This is for the cases that must look at the raw buffer --
    whole-surface `set(...)` checks, row slices, `Counter` histograms.
    """
    wire = getattr(cv, "_wire", None) if cv is not None else None
    if wire is None:
        from runtime.host_canvas import install
        install()                          # puts device_canvas on sys.path
        from device_canvas import PAL565_WIRE
        wire = PAL565_WIRE                 # the stock table, in this build's byte order
    return wire[int(index) & 63]


def words_of(indices, cv=None):
    """`word_of` over a sequence -- for `set(pixels(cv)) == words_of({6, 7})`."""
    return {word_of(i, cv) for i in indices}


# --- format detection ------------------------------------------------------

def buffer_of(cv):
    """The canvas's backing buffer, whichever name this backend publishes it as."""
    buf = getattr(cv, "buf", None)
    if buf is None:
        buf = getattr(cv, "_buf", None)
    if buf is None:
        raise TypeError("not a canvas: %r has neither .buf nor ._buf" % (cv,))
    return buf


def bytes_per_pixel(cv):
    """How many bytes of `buffer_of(cv)` make one pixel. See the module docstring
    for the detection ladder and why each rung is where it is."""
    for name in ("bytes_per_pixel", "BYTES_PER_PIXEL"):
        got = getattr(cv, name, None)
        if got:
            return int(got)

    buf = buffer_of(cv)
    w = int(getattr(cv, "w", 0) or 0)
    h = int(getattr(cv, "h", 0) or 0)
    full = (int(getattr(cv, "_ox", 0) or 0) == 0
            and int(getattr(cv, "_oy", 0) or 0) == 0
            and int(getattr(cv, "_stride", w) or w) == w)
    if full and w > 0 and h > 0:
        for bpp in (1, 2, 3, 4):
            if len(buf) == w * h * bpp:
                return bpp

    # A canvas that keeps its framebuffer private is the boards' DeviceCanvas,
    # which is RGB565 by construction; anything publishing `.buf` is the indexed
    # host raster.
    return 1 if getattr(cv, "buf", None) is not None else 2


# --- reading pixels --------------------------------------------------------

def pixels_in(buf, bpp):
    """A sequence of one int per pixel over a raw buffer of `bpp`-byte pixels.

    Whole buffer, not the viewport window -- same reach the `set(cv.buf)` idiom
    had, so no assertion silently changes what it looks at.
    """
    bpp = int(bpp)
    if bpp == 1:
        return buf                            # a bytearray already iterates as pixels
    if bpp == 2 and len(buf) % 2 == 0:
        return memoryview(buf).cast("H")      # native-endian; see the docstring
    mv = memoryview(buf)
    return [int.from_bytes(mv[i:i + bpp], "little")
            for i in range(0, len(mv) - bpp + 1, bpp)]


def pixels(cv):
    """One int per pixel of a canvas, at that canvas's detected pixel width."""
    return pixels_in(buffer_of(cv), bytes_per_pixel(cv))


# --- the assertions ---------------------------------------------------------

def distinct_pixels_in(buf, bpp):
    """Distinct PIXEL values in a raw buffer of `bpp`-byte pixels."""
    return len(set(pixels_in(buf, bpp)))


def distinct_pixels(cv):
    """How many distinct colours are on this canvas. 1 == a flat fill."""
    return len(set(pixels(cv)))


def drew_something(cv):
    """True when the canvas is more than one flat colour.

    The direct replacement for `len(set(cv.buf)) > 1`, and unlike it this stays
    false on a blank RGB565 frame.
    """
    return distinct_pixels(cv) > 1


def _background(px, bg):
    if bg is not None:
        return bg
    # The background is the MOST COMMON pixel, not the first one. Pixel 0 is the
    # top-left corner, and a cart that draws at the origin (the moycore route
    # test draws `rect(0, 0, ...)`) makes it FOREGROUND -- which inverts the
    # count and turns "how much did it paint" into "how much did it not".
    if not len(px):
        return None
    return Counter(px).most_common(1)[0][0]


def painted_pixels(cv, bg=None):
    """How many pixels differ from the background colour.

    Replaces `sum(1 for b in cv.buf if b)`, which asked "how many bytes are not
    palette index 0" -- true only while a pixel is a byte AND the background is
    index 0. Pass `bg` to name the background explicitly.
    """
    px = pixels(cv)
    ref = _background(px, bg)
    return sum(1 for p in px if p != ref)


def painted_pixels_rect(cv, x, y, w, h, bg=None):
    """`painted_pixels` restricted to a rectangle of the canvas.

    Row addressing uses the canvas STRIDE (a viewport canvas's rows are wider
    than its `w`), and the rect is clamped to the surface, so a caller asking
    about a panel that hangs off the edge counts what is really there.
    """
    px = pixels(cv)
    stride = int(getattr(cv, "_stride", 0) or getattr(cv, "w", 0))
    rows = len(px) // stride if stride else 0
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(stride, int(x) + int(w))
    y1 = min(rows, int(y) + int(h))
    ref = _background(px, bg)
    n = 0
    for yy in range(y0, y1):
        row = yy * stride
        for xx in range(x0, x1):
            if px[row + xx] != ref:
                n += 1
    return n


def pixel_sample(cv, step):
    """A hashable every-`step`-th-PIXEL sample of the canvas.

    For the liveliness tests, which collect these in a set to prove a cart
    animates. Sampling the BYTES at a stride instead (the old
    `bytes(cv.buf[::97])`) walks alternating halves of consecutive RGB565 words
    once a pixel is two bytes wide -- still a signature of sorts, but of
    something nobody chose, and one that can miss a moving sprite by reading
    only the low byte of the colours it passes over.
    """
    return tuple(pixels(cv)[::int(step)])
