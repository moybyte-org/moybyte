"""The two PICO-8 cart forms, as fixtures the repo may actually carry.

`tests/fixtures/tiny_dash.p8` is a cart WE wrote. That matters: the interesting
real carts are the BBS ones, `ports/celeste.moy` is gitignored on both repos
under CC BY-NC-SA, and a test fixture that cannot be committed is a test that
only runs on one laptop.

The `.p8.png` half is BUILT from it here rather than committed, because the
steganographic form is mechanical: PICO-8 packs the whole 32,800-byte cart ROM
into the low two bits of a 160x205 RGBA label image, two bits per channel.
Building it is the exact inverse of the vendored converter's `_p8png_rom` /
`_p8png_sections`, so a round trip through this encoder and back out of the
converter is a real end-to-end check of the PNG path -- not a recorded blob that
would go stale the moment either side changed.

The scanline FILTERS rotate through all five PNG types on purpose. The unfilter
loops in `_png_scanlines` are the heaviest thing the browser import runs (four
bytes per subpixel, per pixel, in interpreted Python) and the Paeth branch is
the one nobody would notice being wrong: every filter is a lossless transform,
so a bug there produces a plausible ROM full of wrong bytes rather than an
error.
"""

import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TINY_DASH_P8 = os.path.join(ROOT, "tests", "fixtures", "tiny_dash.p8")

# A PICO-8 BBS cart label: 160x205 RGBA, and its 32,800 pixels ARE the ROM.
PNG_W = 160
PNG_H = 205
ROM_LEN = PNG_W * PNG_H

GFX_AT = 0x0000
MAP_AT = 0x2000
GFF_AT = 0x3000
MUSIC_AT = 0x3100
SFX_AT = 0x3200
CODE_AT = 0x4300


def read_p8_text(path=None):
    with open(path or TINY_DASH_P8, encoding="utf-8") as f:
        return f.read()


def _hexbytes(line, n):
    """`n` bytes from a 2n-char hex row, short rows zero-padded."""
    out = bytearray(n)
    for i in range(min(n, len(line) // 2)):
        try:
            out[i] = int(line[i * 2:i * 2 + 2], 16)
        except ValueError:
            out[i] = 0
    return out


def sections_to_rom(sections):
    """A parsed `.p8` -> the 32,800-byte PICO-8 cart ROM.

    The inverse of the vendored `_p8png_sections`, field for field: the gfx
    nibble order (low nibble is the LEFT pixel), the music channel flags living
    in bit 7 of the first three channel bytes, and the 16-bit little-endian sfx
    note word."""
    rom = bytearray(ROM_LEN)

    # An UNUSED music pattern is not four zero bytes -- zero is "channel plays
    # sfx 0". PICO-8 marks a channel off with bit 6, so an empty slot reads
    # 41 42 43 44, which is what makes a run of them a pattern-run break rather
    # than 63 rows of silence-that-is-really-sfx-0. Getting this wrong is
    # invisible in the ROM and loud in the imported sounds.json.
    for p in range(64):
        rom[MUSIC_AT + p * 4:MUSIC_AT + p * 4 + 4] = b"\x41\x42\x43\x44"

    for y, line in enumerate(sections.get("gfx", [])[:128]):
        for xb in range(64):
            lo = line[xb * 2:xb * 2 + 1]
            hi = line[xb * 2 + 1:xb * 2 + 2]
            v = (int(hi, 16) << 4) | int(lo, 16) if (lo and hi) else 0
            rom[GFX_AT + y * 64 + xb] = v

    for y, line in enumerate(sections.get("map", [])[:32]):
        rom[MAP_AT + y * 128:MAP_AT + y * 128 + 128] = _hexbytes(line, 128)

    for y, line in enumerate(sections.get("gff", [])[:2]):
        rom[GFF_AT + y * 128:GFF_AT + y * 128 + 128] = _hexbytes(line, 128)

    for p, line in enumerate(sections.get("music", [])[:64]):
        bits = line.split()
        if len(bits) < 2 or len(bits[1]) < 8:
            continue
        flags = int(bits[0], 16)
        ch = [int(bits[1][i * 2:i * 2 + 2], 16) & 0x7F for i in range(4)]
        for i in range(3):
            ch[i] |= ((flags >> i) & 1) << 7
        rom[MUSIC_AT + p * 4:MUSIC_AT + p * 4 + 4] = bytes(ch)

    for s, line in enumerate(sections.get("sfx", [])[:64]):
        if len(line) < 8:
            continue
        base = SFX_AT + s * 68
        head = _hexbytes(line[:8], 4)
        rom[base + 64:base + 68] = head
        for n in range(32):
            cell = line[8 + n * 5:8 + n * 5 + 5]
            if len(cell) < 5:
                break
            pitch = int(cell[0:2], 16)
            inst = int(cell[2], 16)
            vol = int(cell[3], 16)
            eff = int(cell[4], 16)
            v = ((pitch & 0x3F) | ((inst & 7) << 6) | ((vol & 7) << 9)
                 | ((eff & 7) << 12) | (((inst >> 3) & 1) << 15))
            rom[base + n * 2] = v & 0xFF
            rom[base + n * 2 + 1] = v >> 8

    code = "\n".join(sections.get("lua", [])).encode("ascii", "replace")
    end = CODE_AT + len(code)
    assert end < ROM_LEN, "that cart's Lua does not fit the ROM's code region"
    rom[CODE_AT:end] = code
    rom[end] = 0                      # the converter reads up to the first NUL
    return bytes(rom)


def _paeth(a, b, c):
    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def rom_to_png(rom):
    """The cart ROM -> a PICO-8-shaped `.p8.png`, low two bits per channel.

    The high six bits are the visible label; a gradient rather than zeros, so a
    reader that took the WRONG bits would produce garbage instead of quietly
    reading a black picture as an empty cart."""
    assert len(rom) == ROM_LEN, len(rom)
    raw = bytearray()
    prev = bytearray(PNG_W * 4)
    for y in range(PNG_H):
        line = bytearray(PNG_W * 4)
        for x in range(PNG_W):
            v = rom[y * PNG_W + x]
            # ARGB order, 2 bits each -- the converter reads
            # ((a&3)<<6)|((r&3)<<4)|((g&3)<<2)|(b&3).
            line[x * 4 + 0] = ((x & 0x3F) << 2) | ((v >> 4) & 3)      # R
            line[x * 4 + 1] = ((y & 0x3F) << 2) | ((v >> 2) & 3)      # G
            line[x * 4 + 2] = (((x ^ y) & 0x3F) << 2) | (v & 3)       # B
            line[x * 4 + 3] = (0x3F << 2) | ((v >> 6) & 3)            # A
        ftype = y % 5
        enc = bytearray(len(line))
        for i in range(len(line)):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if ftype == 0:
                enc[i] = line[i]
            elif ftype == 1:
                enc[i] = (line[i] - a) & 0xFF
            elif ftype == 2:
                enc[i] = (line[i] - b) & 0xFF
            elif ftype == 3:
                enc[i] = (line[i] - ((a + b) >> 1)) & 0xFF
            else:
                enc[i] = (line[i] - _paeth(a, b, c)) & 0xFF
        raw.append(ftype)
        raw += enc
        prev = line

    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body
                + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", PNG_W, PNG_H, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


_PNG_MEMO = []


def tiny_dash_png(parse_p8):
    """`tests/fixtures/tiny_dash.p8` as its steganographic `.p8.png` twin.

    Takes `parse_p8` rather than importing it, so the caller decides which
    converter is doing the parsing (the host's, or a MicroPython one). Memoized
    because the encode is 131k interpreted filter steps and several tests want
    the same bytes."""
    if not _PNG_MEMO:
        _PNG_MEMO.append(rom_to_png(sections_to_rom(parse_p8(read_p8_text()))))
    return _PNG_MEMO[0]


def write_pair(out_dir, parse_p8):
    """Both forms on disk under `out_dir`; returns (p8_path, png_path)."""
    p8 = os.path.join(out_dir, "tiny_dash.p8")
    png = os.path.join(out_dir, "tiny_dash.p8.png")
    with open(p8, "w", encoding="utf-8") as f:
        f.write(read_p8_text())
    with open(png, "wb") as f:
        f.write(tiny_dash_png(parse_p8))
    return p8, png
