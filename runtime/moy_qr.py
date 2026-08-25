"""A QR encoder small enough to freeze into every board image (#197).

WHY THIS EXISTS AT ALL. The web console's address is `http://<ip>:8080/?pin=NNNN`
-- around 35 characters of IP, port and pairing pin, which a kid has to get from
a 320x240 panel into a phone. Typed, it is a transcription error waiting to
happen; scanned, it is a camera gesture. So the connection screen draws the url
as a QR code, and drawing one means encoding one here: there is no library on a
board, and the whole point of the pin is that it is not a constant something
could be baked with.

THE SUBSET, and why each bound is where it is:

  * BYTE mode only. The payload is a url, which alphanumeric mode cannot spell
    (it has no lowercase and no `?`), so the denser modes would never fire.
  * ERROR CORRECTION LEVEL L. The code is read off a lit panel at arm's length
    with no print damage and no smudges -- the failure mode EC exists for does
    not occur here -- and L buys the most payload per module, which is what
    keeps the code small enough to draw at a legible module size on a 320x240
    canvas.
  * VERSIONS 1..4 (21x21 to 33x33). A paired url is ~35 bytes; version 3 at L
    holds 53 and version 4 holds 78, so the range covers every address this
    will ever carry, including IPv4 with a long hostname. Above version 4 an EC
    level's codewords split into multiple BLOCKS that must be interleaved, and
    that machinery would be code no console reaches. `encode` raises rather
    than silently truncating.
  * ONE MASK, pattern 0 (`(row + col) % 2 == 0`), and the format bits say so.
    The spec lets an encoder pick any of the eight; picking the BEST one means
    rendering all eight and scoring each against four penalty rules, which is
    ~30ms of MicroPython on a board and buys nothing measurable for a payload
    of this shape. Mask 0 is the checkerboard: it cannot produce the long
    same-colour runs penalty rule 1 punishes, nor the solid blocks rule 2
    punishes. What is NOT optional is that the 15 format bits describe the mask
    actually applied -- a scanner unmasks with what they say, so a mismatch
    yields a code that looks right and decodes to noise. `test_moy_qr.py` pins
    the format words against the published table for exactly that reason.

MicroPython shape: bytearrays and small ints throughout, no f-strings, no
comprehension over the module grid, and the GF(256) tables are built once at
import (512 + 256 bytes) rather than stored as source literals.

    rows = moy_qr.encode("http://10.0.0.5:8080/?pin=4821")
    size = len(rows)            # rows[r][c] is 1 for a DARK module

The returned matrix carries NO quiet zone -- the caller knows what it is
drawing on and how many pixels a module is worth, and the four-module margin
the spec asks for is a drawing decision, not an encoding one.
"""

# Per version (index = version - 1), error correction level L, ONE block:
# (total codewords, error-correction codewords, data codewords). The byte-mode
# payload capacity is data - 2: the header is a 4-bit mode indicator plus an
# 8-bit character count, and the 4-bit terminator then lands exactly on the
# byte boundary (see _data_codewords).
_CAP = ((26, 7, 19), (44, 10, 34), (70, 15, 55), (100, 20, 80))

MAX_VERSION = len(_CAP)
MASK = 0                       # the one mask pattern, see the module docstring
QUIET = 4                      # modules of margin the SPEC asks a drawer for

# GF(256) with the QR primitive polynomial x^8+x^4+x^3+x^2+1 and generator 2.
# _EXP is doubled to 512 entries so a log sum never needs a modulo.
_EXP = bytearray(512)
_LOG = bytearray(256)


def _init_gf():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _gen_poly(n):
    """The degree-`n` generator polynomial, coefficients highest power first.

    Built rather than tabulated: the four we need (7/10/15/20) would be 56
    bytes of source constants that nothing could check, where this is the
    definition -- the product of (x - a^i) for i in 0..n-1."""
    g = bytearray(1)
    g[0] = 1
    for i in range(n):
        nxt = bytearray(len(g) + 1)
        root = _EXP[i]
        for j in range(len(g)):
            c = g[j]
            nxt[j] ^= c                              # the x term: shift up
            if c and root:
                nxt[j + 1] ^= _EXP[_LOG[c] + _LOG[root]]
        g = nxt
    return g


def _ec_codewords(data, n):
    """The `n` Reed-Solomon check codewords for `data` -- polynomial long
    division in GF(256), remainder only."""
    g = _gen_poly(n)
    rem = bytearray(n)
    for b in data:
        factor = b ^ rem[0]
        del rem[0]
        rem.append(0)
        if factor:
            lf = _LOG[factor]
            for i in range(n):
                c = g[i + 1]
                if c:
                    rem[i] ^= _EXP[_LOG[c] + lf]
    return rem


def capacity(version):
    """Byte-mode payload capacity of `version` at EC level L."""
    return _CAP[version - 1][2] - 2


def _pick_version(n):
    for v in range(1, MAX_VERSION + 1):
        if n <= capacity(v):
            return v
    raise ValueError("qr: %d bytes exceeds version %d (%d)"
                     % (n, MAX_VERSION, capacity(MAX_VERSION)))


def _data_codewords(data, dcw):
    """Mode + count + payload + terminator + padding, as `dcw` codewords.

    The bit layout is fixed enough to write without a bit accumulator, and
    saying so is cheaper than one: the header is 4 bits of mode indicator
    (0100 = byte) and 8 bits of character count, so every payload byte lands
    straddling two codewords by exactly one nibble, and the 4-bit terminator
    fills the last half-byte. Then the spec's alternating 11101100 / 00010001
    pad bytes to the end of the data capacity."""
    n = len(data)
    out = bytearray(dcw)
    out[0] = 0x40 | (n >> 4)           # 0100 | the count's high nibble
    low = n & 0x0F                     # the count's low nibble, carried down
    i = 1
    for b in data:
        out[i] = (low << 4) | (b >> 4)
        low = b & 0x0F
        i += 1
    out[i] = low << 4                  # the last nibble + the 0000 terminator
    i += 1
    pad = 0xEC
    while i < dcw:
        out[i] = pad
        pad = 0x11 if pad == 0xEC else 0xEC
        i += 1
    return out


def format_bits(mask, ec=1):
    """The 15-bit format word for EC level `ec` (L = 1) and `mask`.

    A BCH(15,5) code over the 5 data bits, then XORed with 0x5412 so an
    all-zero format cannot render as an all-light region. The scanner reads
    THIS to learn which mask to undo, which is why it is derived from the mask
    argument rather than written down beside it."""
    data = (ec << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _draw_function(m, res, size, version):
    """The finder/separator/timing/alignment patterns, the always-dark module,
    and the reserved format strip. `res` marks every module the data walk must
    step over."""
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            r = r0 + dr
            if r < 0 or r >= size:
                continue
            for dc in range(-1, 8):
                c = c0 + dc
                if c < 0 or c >= size:
                    continue
                res[r][c] = 1
                # The finder is a dark 7x7 ring around a light ring around a
                # dark 3x3 core; dr/dc of -1 or 7 is the light separator.
                dark = (0 <= dr <= 6 and 0 <= dc <= 6
                        and (dr == 0 or dr == 6 or dc == 0 or dc == 6
                             or (2 <= dr <= 4 and 2 <= dc <= 4)))
                m[r][c] = 1 if dark else 0
    for i in range(size):                      # timing: row 6 and column 6
        if not res[6][i]:
            res[6][i] = 1
            m[6][i] = 1 if (i % 2 == 0) else 0
        if not res[i][6]:
            res[i][6] = 1
            m[i][6] = 1 if (i % 2 == 0) else 0
    if version >= 2:
        # Versions 2..6 carry exactly ONE alignment pattern, and it sits at
        # (size-7, size-7): the other two grid centres of those versions fall
        # inside the finder patterns and are omitted.
        cr = size - 7
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                res[cr + dr][cr + dc] = 1
                far = dr if dr >= 0 else -dr
                d2 = dc if dc >= 0 else -dc
                if d2 > far:
                    far = d2
                m[cr + dr][cr + dc] = 1 if far != 1 else 0
    for i in range(9):                         # the reserved format strip
        if not res[8][i]:
            res[8][i] = 1
            m[8][i] = 0
        if not res[i][8]:
            res[i][8] = 1
            m[i][8] = 0
    for i in range(8):
        res[8][size - 1 - i] = 1
        m[8][size - 1 - i] = 0
        res[size - 1 - i][8] = 1
        m[size - 1 - i][8] = 0
    m[size - 8][8] = 1                         # the module that is always dark
    res[size - 8][8] = 1


def _draw_format(m, size, mask):
    """Both copies of the format word (the spec stores it twice so a damaged
    finder corner does not cost the reader the mask)."""
    bits = format_bits(mask)
    for i in range(6):
        m[i][8] = (bits >> i) & 1
    m[7][8] = (bits >> 6) & 1
    m[8][8] = (bits >> 7) & 1
    m[8][7] = (bits >> 8) & 1
    for i in range(9, 15):
        m[8][14 - i] = (bits >> i) & 1
    for i in range(8):
        m[8][size - 1 - i] = (bits >> i) & 1
    for i in range(8, 15):
        m[size - 15 + i][8] = (bits >> i) & 1


def _draw_data(m, res, size, cw, mask):
    """The codeword stream, walked up-and-down two-module columns from the
    bottom-right corner, masked as it lands."""
    total = len(cw) * 8
    i = 0
    col = size - 1
    up = True
    while col > 0:
        if col == 6:
            col = 5                    # step over the vertical timing column
        for k in range(size):
            row = (size - 1 - k) if up else k
            for c in (col, col - 1):
                if res[row][c]:
                    continue
                bit = 0
                if i < total:
                    bit = (cw[i >> 3] >> (7 - (i & 7))) & 1
                    i += 1
                if mask == 0 and ((row + c) % 2) == 0:
                    bit ^= 1
                m[row][c] = bit
        up = not up
        col -= 2


def encode(text, version=None):
    """`text` as a QR matrix: a list of `size` bytearrays, 1 = dark module.

    `text` may be str (encoded UTF-8) or bytes. `version` forces a size;
    omitted, the smallest version that holds the payload is chosen."""
    if not isinstance(text, (bytes, bytearray)):
        text = text.encode("utf-8")
    n = len(text)
    if version is None:
        version = _pick_version(n)
    elif n > capacity(version):
        raise ValueError("qr: %d bytes exceeds version %d (%d)"
                         % (n, version, capacity(version)))
    _total, ecn, dcw = _CAP[version - 1]
    cw = _data_codewords(text, dcw)
    cw += _ec_codewords(cw, ecn)
    size = 17 + 4 * version
    m = []
    res = []
    for _ in range(size):
        m.append(bytearray(size))
        res.append(bytearray(size))
    _draw_function(m, res, size, version)
    _draw_data(m, res, size, cw, MASK)
    _draw_format(m, size, MASK)
    return m
