#!/usr/bin/env python3
"""Tiny5, the default theme's UI font, as a font file (docs/theming_2026-09.md
§4.7 has the format).

Two steps, so the font in the tree rebuilds from the tree alone:

  extract  Tiny5's TrueType file (Google Fonts, pinned below by commit and
           SHA-256) -> native/moy_ui/themes/tiny5.txt, its glyphs as rows of
           `#` and `.`. Samples each pixel's centre against the outline of the
           default instance (wght 400, wdth 100, BLED/JITT/ROND 0), whose
           points sit on a 128-unit grid: one unit square is one pixel. Needs
           fontTools (install on demand; in no extra).
  build    tiny5.txt -> native/moy_ui/themes/tiny5.fnt. The default; Python
           alone. tests/test_tiny5_font.py holds the committed file to it.

The font covers ASCII 0x20-0x7E, the bytes the format can name that UTF-8
draws as themselves; a byte outside draws glyph 0, the space, as petme128's
row does.

Run: python tools/make_tiny5_font.py [build | extract TTF | check]
"""

import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TXT = os.path.join(ROOT, "native", "moy_ui", "themes", "tiny5.txt")
FNT = os.path.join(ROOT, "native", "moy_ui", "themes", "tiny5.fnt")

SOURCE = ("https://github.com/google/fonts/blob/"
          "8dc5b4fcbbf3303402a6f3b587976c73cf4e212e/ofl/tiny5/"
          "Tiny5%5BBLED,JITT,ROND,wdth,wght%5D.ttf")
SHA256 = "588626f2c1b164f55927c0d8123f4bc134651aad7d257da68fc19b0824b0d8fa"
UNIT = 128              # font units a pixel
FIRST, LAST = 0x20, 0x7E
TOP = 5                 # the highest pixel row, from the baseline up (i, j, `)
BOTTOM = -1             # the lowest (g, j, p, q, y, the comma)
LINE = 8                # the line advance; the font's own ascender and
                        # descender add up to 9 (§4.7 records the ruling)


def extract(ttf):
    from fontTools.pens.pointInsidePen import PointInsidePen
    from fontTools.ttLib import TTFont
    data = open(ttf, "rb").read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("%s is not the pinned Tiny5 (sha256 %s)" % (ttf, SHA256))
    font = TTFont(ttf)
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    out = ["# Tiny5 (OFL-1.1, THIRD_PARTY.md), extracted by tools/make_tiny5_font.py",
           "# from " + SOURCE,
           "# sha256 " + SHA256,
           "# a glyph: its byte, its advance, then its rows top down; # is ink",
           "box %d %d" % (max(font["hmtx"][cmap[c]][0] // UNIT for c in range(FIRST, LAST + 1)),
                          TOP - BOTTOM + 1),
           "line %d" % LINE,
           "ascent %d" % (TOP + 1)]
    w = int(out[4].split()[1])
    for c in range(FIRST, LAST + 1):
        name = cmap[c]
        adv = font["hmtx"][name][0]
        if adv % UNIT:
            raise SystemExit("U+%04X: advance %d is off the pixel grid" % (c, adv))
        out.append("")
        out.append("0x%02X %d" % (c, adv // UNIT))
        for y in range(TOP, BOTTOM - 1, -1):
            row = ""
            for x in range(w):
                pen = PointInsidePen(glyphs, ((x + 0.5) * UNIT, (y + 0.5) * UNIT))
                glyphs[name].draw(pen)
                row += "#" if pen.getResult() else "."
            out.append(row)
    with open(TXT, "w") as f:
        f.write("\n".join(out) + "\n")


def parse(text):
    """(w, h, line, ascent, [(byte, advance, rows)]) from tiny5.txt's text."""
    head = {}
    glyphs = []
    cur = None
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("# "):
            continue
        if ln[0] in ".#":
            cur[2].append(ln)
            continue
        parts = ln.split()
        if parts[0].startswith("0x"):
            cur = (int(parts[0], 16), int(parts[1]), [])
            glyphs.append(cur)
        else:
            head[parts[0]] = [int(v) for v in parts[1:]]
    w, h = head["box"]
    return w, h, head["line"][0], head["ascent"][0], glyphs


def build(text):
    """The font file tiny5.txt's text describes."""
    w, h, line, ascent, glyphs = parse(text)
    first = glyphs[0][0]
    count = len(glyphs)
    for i, (byte, _adv, rows) in enumerate(glyphs):
        if byte != first + i or len(rows) != h or any(len(r) != w for r in rows):
            raise SystemExit("glyph 0x%02X: not the next byte, or not a %dx%d box"
                             % (byte, w, h))
    adv = bytes(a for _b, a, _r in glyphs)
    adv += bytes(-len(adv) % 4)
    cols = bytearray()
    per = (h + 7) // 8
    for _b, _a, rows in glyphs:
        for x in range(w):
            col = 0
            for y in range(h):
                if rows[y][x] == "#":
                    col |= 1 << y
            cols += col.to_bytes(per, "little")
    size = 16 + len(adv) + len(cols)
    head = b"MFNT" + bytes((1, w, h, line, ascent, first, count, 0)) + struct.pack("<I", size)
    return head + adv + bytes(cols)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd == "extract":
        extract(argv[2])
        cmd = "build"
    data = build(open(TXT).read())
    if cmd == "check":
        if open(FNT, "rb").read() != data:
            raise SystemExit("%s is not what %s builds" % (FNT, TXT))
        return
    with open(FNT, "wb") as f:
        f.write(data)
    print("%s: %d bytes" % (FNT, len(data)))


if __name__ == "__main__":
    main(sys.argv)
