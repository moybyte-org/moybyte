import sys, base64
sys.path.insert(0,'.')
from runtime import font as F
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

UPP=100; UPM=800  # 8px cell, 100 units/pixel
ASC=700; DESC=-100

def build_glyph(cp):
    pen=TTGlyphPen(None)
    g=F.glyph(chr(cp))            # 8 column-bytes, LSB=top row
    for col in range(8):
        b=g[col]
        for row in range(8):
            if (b>>row)&1:
                x0=col*UPP; x1=x0+UPP
                # baseline between row6/row7: row r -> y bottom=(6-r)*UPP
                yb=(6-row)*UPP; yt=yb+UPP
                pen.moveTo((x0,yb)); pen.lineTo((x0,yt)); pen.lineTo((x1,yt)); pen.lineTo((x1,yb)); pen.closePath()
    return pen.glyph()

codes=list(range(0x20,0x80))
order=['.notdef']+[f'g{cp:02x}' for cp in codes]
cmap={cp:f'g{cp:02x}' for cp in codes}
glyphs={'.notdef':TTGlyphPen(None).glyph()}
metrics={'.notdef':(UPM,0)}
for cp in codes:
    n=f'g{cp:02x}'; glyphs[n]=build_glyph(cp); metrics[n]=(UPM,0)

fb=FontBuilder(UPM, isTTF=True)
fb.setupGlyphOrder(order)
fb.setupCharacterMap(cmap)
fb.setupGlyf(glyphs)
fb.setupHorizontalMetrics(metrics)
fb.setupHorizontalHeader(ascent=ASC, descent=DESC)
fb.setupNameTable({"familyName":"Petme128","styleName":"Regular",
                   "fullName":"Petme128 Regular","psName":"Petme128-Regular"})
fb.setupOS2(sTypoAscender=ASC, sTypoDescender=DESC, usWinAscent=ASC, usWinDescent=abs(DESC))
fb.setupPost()
fb.font.flavor="woff2"
out="/tmp/claude-0/-home-user-moybyte/8290a6cd-1cf1-5614-bb5d-c7cd40b7881f/scratchpad/petme128.woff2"
fb.save(out)
data=open(out,"rb").read()
b64=base64.b64encode(data).decode()
open("/tmp/claude-0/-home-user-moybyte/8290a6cd-1cf1-5614-bb5d-c7cd40b7881f/scratchpad/petme128.b64","w").write(b64)
print("woff2 bytes:", len(data), "| base64 chars:", len(b64))
