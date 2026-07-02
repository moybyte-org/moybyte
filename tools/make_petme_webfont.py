#!/usr/bin/env python3
"""Build a tiny woff2 webfont from the console's own petme128 8x8 glyphs.

The teaser site inlines the result (base64 data URI) as its heading/display face,
so the site's titles render in the exact font the device draws. Two tweaks over a
literal bitmap dump make it read well at display sizes:
  - PROPORTIONAL advances: each glyph advances by its real ink width + 1px, so
    narrow letters (i, l, s) don't leave the big monospace gaps ("si zes").
  - a redrawn apostrophe/quote: petme128's default ' is a slanted tick that looks
    odd large; we substitute clean vertical marks.

Run: python tools/make_petme_webfont.py  (prints the base64 to embed).
"""
import sys, base64
sys.path.insert(0, '.')
from runtime import font as F
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

UPP = 100; UPM = 800          # 100 units/pixel, 8px cell
ASC = 700; DESC = -100
GAP = 2                       # trailing spacing in pixels
SPACE_ADV = 5                 # advance for U+0020 (pixels)

# Custom column bitmaps (col-major, LSB = top row) overriding petme's originals.
OVERRIDES = {
    0x27: [0, 0x07, 0, 0, 0, 0, 0, 0],          # ' apostrophe: clean 3px vertical
    0x22: [0x07, 0, 0x07, 0, 0, 0, 0, 0],       # " quote: two clean verticals
}


def cols_for(cp):
    if cp in OVERRIDES:
        return OVERRIDES[cp]
    return list(F.glyph(chr(cp)))               # 8 column-bytes


def build_glyph(cols):
    pen = TTGlyphPen(None)
    for col in range(8):
        b = cols[col]
        for row in range(8):
            if (b >> row) & 1:
                x0 = col * UPP; x1 = x0 + UPP
                yb = (6 - row) * UPP; yt = yb + UPP   # baseline between row6/row7
                pen.moveTo((x0, yb)); pen.lineTo((x0, yt))
                pen.lineTo((x1, yt)); pen.lineTo((x1, yb)); pen.closePath()
    return pen.glyph()


def advance_for(cols):
    maxcol = -1
    for col in range(8):
        if cols[col]:
            maxcol = col
    if maxcol < 0:
        return SPACE_ADV * UPP                   # blank glyph (space)
    return (maxcol + 1 + GAP) * UPP              # ink width + 1px gap


codes = list(range(0x20, 0x80))
order = ['.notdef'] + [f'g{cp:02x}' for cp in codes]
cmap = {cp: f'g{cp:02x}' for cp in codes}
glyphs = {'.notdef': TTGlyphPen(None).glyph()}
metrics = {'.notdef': (SPACE_ADV * UPP, 0)}
for cp in codes:
    cols = cols_for(cp)
    n = f'g{cp:02x}'
    glyphs[n] = build_glyph(cols)
    metrics[n] = (advance_for(cols), 0)

fb = FontBuilder(UPM, isTTF=True)
fb.setupGlyphOrder(order)
fb.setupCharacterMap(cmap)
fb.setupGlyf(glyphs)
fb.setupHorizontalMetrics(metrics)
fb.setupHorizontalHeader(ascent=ASC, descent=DESC)
fb.setupNameTable({"familyName": "Petme128", "styleName": "Regular",
                   "fullName": "Petme128 Regular", "psName": "Petme128-Regular"})
fb.setupOS2(sTypoAscender=ASC, sTypoDescender=DESC, usWinAscent=ASC, usWinDescent=abs(DESC))
fb.setupPost()
fb.font.flavor = "woff2"
out = sys.argv[1] if len(sys.argv) > 1 else "petme128.woff2"
fb.save(out)
data = open(out, "rb").read()
b64 = base64.b64encode(data).decode()
open(out + ".b64", "w").write(b64)
print("woff2 bytes:", len(data), "| base64 chars:", len(b64))
