#!/usr/bin/env python3
"""Build the web page's favicon FROM the console's own mascot art.

    python tools/gen_favicon.py            # print the data: URI
    python tools/gen_favicon.py --write    # patch it into page_core.html

Moy is 16x16 nibble art in `runtime/chrome.py` (`_ICON_ART["moy"]`), resolved
through the MOY64 palette -- the same source the boot splash and the launcher
header draw from. Generating the favicon from it rather than checking in a
hand-drawn PNG means the browser tab and the console cannot disagree about what
the mascot looks like, and `tests/test_favicon.py` fails if they drift.

The PNG is written by hand (zlib + four chunks) rather than with pillow: this is
~200 bytes of a format whose 16x16 RGBA case is a dozen lines, and the
alternative is making a build artifact depend on an optional dev dependency.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runtime"))

PAGE = os.path.join(ROOT, "firmware", "web_runner", "page_core.html")
MARK_A = '<link rel=icon href="'
MARK_B = '">'


def mascot_rgba(scale=2):
    """The mascot as raw RGBA rows. `.` in the art is TRANSPARENT, which is why
    this cannot just reuse an icon-sheet tile: on a sheet a blank pixel is index
    0, which is also Moy's outline colour, so the sheet blit boxes him in
    (the same reason console.splash_image builds its own Image)."""
    from runtime import chrome
    from runtime.palette import MOY64

    art = chrome._ICON_ART["moy"]
    rows = []
    for y in range(16):
        line = art[y] if y < len(art) else ""
        row = bytearray()
        for x in range(16):
            idx = chrome._nibble(line[x]) if x < len(line) else -1
            if idx is None or idx < 0:
                row += b"\x00\x00\x00\x00"          # transparent
            else:
                r, g, b = MOY64[idx & 63]
                row += bytes((r, g, b, 255))
        for _ in range(scale):                       # nearest-neighbour up
            rows.append(bytes(_stretch(row, scale)))
    return rows, 16 * scale


def _stretch(row, scale):
    out = bytearray()
    for i in range(0, len(row), 4):
        out += row[i:i + 4] * scale
    return out


def png(rows, size):
    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", binascii.crc32(c) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + r for r in rows)        # filter 0 per scanline
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def data_uri(scale=2):
    rows, size = mascot_rgba(scale)
    return "data:image/png;base64," + base64.b64encode(png(rows, size)).decode()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="patch page_core.html in place")
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args(argv)

    uri = data_uri(args.scale)
    if not args.write:
        print(uri)
        return 0

    with open(PAGE, encoding="utf-8") as f:
        page = f.read()
    if MARK_A in page:
        i = page.index(MARK_A) + len(MARK_A)
        j = page.index(MARK_B, i)
        page = page[:i] + uri + page[j:]
    else:
        anchor = "<title>Moybyte</title>"
        page = page.replace(anchor, '%s%s%s%s' % (MARK_A, uri, MARK_B, anchor), 1)
    with open(PAGE, "w", encoding="utf-8") as f:
        f.write(page)
    print("patched %s (%d bytes of data uri)" % (PAGE, len(uri)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
