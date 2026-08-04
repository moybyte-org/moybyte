"""petme128 8x8 font, shared so the host renders text identically to the device.

This is MicroPython framebuf's built-in `font_petme128_8x8` (the glyphs the device
draws via `framebuf.text`), extracted byte-for-byte so the host `Canvas.print`
produces pixel-identical text. 8x8 glyphs for ASCII 0x20..0x7f; each glyph is 8
bytes = 8 columns, one byte per column with the LSB at the top row (exactly how
framebuf scans it). Both backends advance 8px per character.

THIRD-PARTY NOTICE -- the glyph data below is not Moybyte's work:

    MicroPython (extmod/font_petme128_8x8.h) -- The MIT License (MIT)
    Copyright (c) 2013, 2014 Damien P. George
    https://github.com/micropython/micropython

The full permission notice is reproduced in THIRD_PARTY.md at the repository
root; it travels with these bytes wherever they go (the frozen device font
blob, the generated site webfont, any copy of this file).
"""

FIRST = 0x20
WIDTH = 8
HEIGHT = 8

# 8 bytes per glyph, column-major, LSB = top row.
_FONT = bytes.fromhex(
    "00000000000000000000004f4f000000"
    "0007070000070700147f7f14147f7f14"
    "00242e6b6b3a1200006333180c666300"
    "00327f4d4d7772500000000406030100"
    "00001c3e63410000000041633e1c0000"
    "082a3e1c1c3e2a080008083e3e080800"
    "000080e0600000000008080808080800"
    "000000606000000000406030180c0602"
    "003e7f49457f3e000040447f7f404000"
    "00627351494f460000226349497f3600"
    "00181814167f7f1000276745457d3900"
    "003e7f49497b3200000303797d070300"
    "00367f49497f360000266f49497f3e00"
    "0000002424000000000080e464000000"
    "00081c36634141000014141414141400"
    "00414163361c080000020351590f0600"
    "003e7f414d4f2e00007c7e0b0b7e7c00"
    "007f7f49497f3600003e7f4141632200"
    "007f7f41633e1c00007f7f4949414100"
    "007f7f0909010100003e7f41497b3a00"
    "007f7f08087f7f000000417f7f410000"
    "002060417f3f0100007f7f1c36634100"
    "007f7f4040404000007f7f060c067f7f"
    "007f7f0e1c7f7f00003e7f41417f3e00"
    "007f7f09090f0600001e3f21617f5e00"
    "007f7f19396f460000266f49497b3200"
    "0001017f7f010100003f7f40407f3f00"
    "001f3f60603f1f00007f7f3018307f7f"
    "0063771c1c77630000070f78780f0700"
    "006171594d47430000007f7f41410000"
    "0002060c18306040000041417f7f0000"
    "00080c06060c0800c0c0c0c0c0c0c0c0"
    "000001030604000000207454547c7800"
    "007f7f44447c380000387c44446c2800"
    "00387c44447f7f0000387c54545c5800"
    "00087e7f090302000098bca4a4fc7c00"
    "007f7f04047c78000000007d7d000000"
    "0040c08080fd7d00007f7f30386c4400"
    "0000417f7f400000007c7c1830187c7c"
    "007c7c04047c780000387c44447c3800"
    "00fcfc24243c180000183c2424fcfc00"
    "007c7c04040c080000485c5454742000"
    "04043f7f44642000003c7c40407c3c00"
    "001c3c60603c1c00001c7c3018307c1c"
    "00446c38386c4400009cbca0a0fc7c00"
    "004464745c4c44000008083e77414100"
    "000000ffff000000004141773e080800"
    "0002030103020301aa55aa55aa55aa55"
)


# Public alias for the raw glyph blob: the device passes these bytes to the native
# moy_gfx.text kernel (#62), so host and device rasterize from the SAME source.
DATA = _FONT


def glyph(ch):
    """The 8 column-bytes for character `ch` (space for anything out of range)."""
    n = ord(ch) - FIRST
    if 0 <= n < len(_FONT) // WIDTH:
        return _FONT[n * WIDTH:(n + 1) * WIDTH]
    return _FONT[0:WIDTH]


def as_bytes(s):
    """A print() argument as the BYTE sequence it is.

    bytes/bytearray pass through; a str is taken one byte per character rather
    than UTF-8-encoded, because a str reaching here stands in for a Lua byte
    string. A character above U+00FF is not one byte, so it degrades to '?'
    rather than raising -- this is a draw path, and losing a glyph beats losing
    the frame."""
    if isinstance(s, (bytes, bytearray)):
        return s
    s = str(s)
    out = bytearray(len(s))
    for i in range(len(s)):
        c = ord(s[i])
        out[i] = c if c <= 0xFF else 0x3F
    return out


def draw(put, s, x, y):
    """Render `s` at (x, y) by calling put(px, py) for each set pixel. Matches
    framebuf.text exactly: column j, row bit from LSB(top), 8px advance per BYTE.

    Per byte, not per character (moy SPEC.md 6). framebuf.text walks bytes and
    so does the device's native text kernel (moy_gfx: "the string is walked as
    BYTES"), so decoding here made the HOST disagree with the device on any
    non-ASCII string -- one 8px cell where the device advances two. Both tiers
    render nothing for those bytes either way; it was purely the cursor that
    drifted, which is worse, because the text after it lands somewhere else."""
    cx = int(x)
    y = int(y)
    for code in as_bytes(s):
        col = glyph(chr(code))
        for j in range(WIDTH):
            bits = col[j]
            py = y
            while bits:
                if bits & 1:
                    put(cx + j, py)
                bits >>= 1
                py += 1
        cx += WIDTH


def draw_scaled(block, s, x, y, scale):
    """Render `s` at nearest-neighbor `scale`: each set glyph pixel becomes a
    scale x scale block, emitted via block(bx, by, scale) so the caller fills a
    square. scale=1 plots one pixel per call -- identical coverage to draw() but
    through the block sink. Used for the resizable SYSTEM-UI font (the game canvas
    keeps plain 8x8 via draw()); cell advance is WIDTH*scale per char."""
    scale = int(scale)
    if scale < 1:
        scale = 1
    cx = int(x)
    y = int(y)
    for ch in str(s):
        col = glyph(ch)
        for j in range(WIDTH):
            bits = col[j]
            row = 0
            while bits:
                if bits & 1:
                    block(cx + j * scale, y + row * scale, scale)
                bits >>= 1
                row += 1
        cx += WIDTH * scale
