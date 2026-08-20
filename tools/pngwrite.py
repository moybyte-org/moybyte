"""ONE hand-rolled PNG container for the repo's tools (stdlib zlib, no PIL).

Four tools each carried their own chunk/IHDR/IDAT/IEND writer (render_icons,
gen_favicon, make_sakura_bg, import_sakura_bg -- unified 2026-08-18). The
pixel PREP stays with each tool (palette resolve, scaling, RGBA mascot art);
what is shared is the container. `rows` are raw scanlines WITHOUT filter
bytes -- every scanline is written with filter 0, compressed at level 9, so
output is byte-for-byte what the four copies produced (test_favicon pins the
embedded favicon against this)."""

import struct
import zlib

RGB = 2
RGBA = 6


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(rows, w, h, color_type=RGB):
    """A complete PNG as bytes: 8-bit channels, one filter-0 scanline per row."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8,
                                          color_type, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def write_png(path, rows, w, h, color_type=RGB):
    with open(path, "wb") as f:
        f.write(png_bytes(rows, w, h, color_type))
