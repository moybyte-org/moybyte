#!/usr/bin/env python3
"""The outline faces: the shell's sans, sans bold and mono as TrueType
outlines, compiled into native/moy_gfx/moy_path_faces.h, which
native/moy_gfx/moy_path.c draws at any size (a story's text, APPS_RESTYLE.md
"The document": every board draws them as outlines, the same pixels on
every board at the same size).

Each face is one instance of a pinned variable font (tools/make_font.py's
FAMILIES, fetched into its cache): Atkinson Hyperlegible Next at weight 500
(`sans`) and 700 (`sans_bold`), JetBrains Mono at 500 (`mono`). A glyph a
byte, Latin-1 (0x20-0x7E, 0xA0-0xFF; a codepoint past it draws as `?`): its
advance and its outline as a program of bytes (`_program`), and the kerning
pairs between ASCII's inked glyphs, HarfBuzz's (Pillow's raqm layout) at the
em, as make_font asks at its sizes. The program is TrueType's own glyph
encoding (a flag a point, the coordinates as deltas a byte where they fit),
because the table rides every image twice: natively, and in the browser
console's wasm that every board serves.

  build    write native/moy_gfx/moy_path_faces.h
  check    rebuild in memory and compare with the committed header

Run: python tools/make_outline_faces.py [build | check]
"""

import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import make_font  # noqa: E402

OUT = os.path.join(ROOT, "native", "moy_gfx", "moy_path_faces.h")

# (name, family, weight): moy_path.h's MOY_PATH_SANS, _SANS_BOLD, _MONO order.
FACES = (("sans", "atkinson", 500), ("sans_bold", "atkinson", 700), ("mono", "jbmono", 500))
CHARS = list(range(0x20, 0x7F)) + list(range(0xA0, 0x100))


def _instance(fam, weight):
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    path = make_font.ttf_path(fam)
    want = make_font.FAMILIES[fam]["sha256"]
    with open(path, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != want:
            raise SystemExit("make_outline_faces: %s is not the pinned file "
                             "(tools/make_font.py fetch)" % path)
    return instantiateVariableFont(TTFont(path), {"wght": weight})


def _kerns(fam, weight, upm, inked):
    from PIL import ImageFont, features
    if not features.check("raqm"):
        raise SystemExit("make_outline_faces: Pillow has no raqm layout, so no kerning")
    fk = ImageFont.truetype(make_font.ttf_path(fam), upm,
                            layout_engine=ImageFont.Layout.RAQM)
    make_font._vary(fk, upm, weight)
    feats = make_font.KERN_FEATURES
    width = {c: fk.getlength(chr(c), features=feats) for c in inked}
    out = []
    for a in inked:
        for b in inked:
            k = round(fk.getlength(chr(a) + chr(b), features=feats) - width[a] - width[b])
            if k:
                out.append((a, b, k))
    return out


def _program(pts, contours):
    """A glyph's outline as bytes: its contour count, each contour's point
    count, a flag a point (the on-curve bit, then for x and for y: a one-byte
    magnitude with its sign, or a repeat, or a little-endian int16), then the x
    deltas, then the y deltas, each from the point before (the first from 0)."""
    out = bytearray([len(contours)] + contours)
    flags, xs, ys = bytearray(), bytearray(), bytearray()
    px = py = 0
    for x, y, on in pts:
        f = on
        for d, short, same, buf in ((x - px, 0x02, 0x10, xs), (y - py, 0x04, 0x20, ys)):
            if d == 0:
                f |= same
            elif -255 <= d <= 255:
                f |= short | (same if d > 0 else 0)
                buf.append(abs(d))
            else:
                buf += struct.pack("<h", d)
        flags.append(f)
        px, py = x, y
    return bytes(out + flags + xs + ys)


def face(name, fam, weight):
    """(upm, ascent, descent, glyph records, program bytes, kerns, largest
    glyph's points, most contours) of one face. A record is (advance, the
    offset of its program); a byte the face lacks takes `?`'s."""
    tt = _instance(fam, weight)
    upm = tt["head"].unitsPerEm
    asc, desc = tt["hhea"].ascent, tt["hhea"].descent
    cmap = tt.getBestCmap()
    glyf, hmtx = tt["glyf"], tt["hmtx"]
    glyphs, data = [], bytearray()
    inked = []
    most_pts = most_ends = 0
    for c in range(0x20, 0x100):
        gname = cmap.get(c) if c in CHARS else None
        if gname is None:
            glyphs.append(None)
            continue
        coords, endpts, flags = glyf[gname].getCoordinates(glyf)
        pts = []
        for (x, y), fl in zip(coords, flags):
            x, y = int(round(x)), int(round(y))
            if not -16384 <= x < 16384 or not -32768 <= y < 32768:
                raise SystemExit("make_outline_faces: %s U+%04X out of range" % (name, c))
            pts.append((x, y, fl & 1))
        contours = [e - s for s, e in zip([-1] + list(endpts), endpts)]
        if any(n > 255 for n in contours) or len(contours) > 255:
            raise SystemExit("make_outline_faces: %s U+%04X has a contour past 255 points"
                             % (name, c))
        if len(data) > 0xFFFF:
            raise SystemExit("make_outline_faces: %s's programs pass 64 KB" % name)
        glyphs.append((hmtx[gname][0], len(data)))
        data += _program(pts, contours)
        most_pts, most_ends = max(most_pts, len(pts)), max(most_ends, len(contours))
        if len(endpts) and c <= 0x7E:
            inked.append(c)
    q = glyphs[ord("?") - 0x20]
    glyphs = [q if g is None else g for g in glyphs]
    return (upm, asc, desc, glyphs, bytes(data), _kerns(fam, weight, upm, inked),
            most_pts, most_ends)


def _rows(vals, per=12):
    vals = [str(v) for v in vals]
    return "\n".join("    " + ", ".join(vals[i:i + per]) + "," for i in range(0, len(vals), per))


def render():
    out = ["// Generated by tools/make_outline_faces.py from the pinned TrueType files",
           "// (tools/make_font.py FAMILIES); `check` holds it. Do not edit.",
           "// Atkinson Hyperlegible Next and JetBrains Mono, SIL Open Font License 1.1:",
           "// native/moy_ui/fonts/atkinson.txt and native/moy_ui/fonts/jbmono.txt.",
           "// Each face: its glyphs' programs (make_outline_faces._program; font units,",
           "// y up), a record a byte from 0x20 (advance, its program's offset) and its",
           "// kerning pairs: the first pair of each left byte from 0x20 (and the end),",
           "// each pair's right byte, sorted, and its adjustment.",
           "#ifndef MOY_PATH_FACES_H", "#define MOY_PATH_FACES_H", ""]
    table = []
    most_pts = most_ends = 0
    for name, fam, weight in FACES:
        upm, asc, desc, glyphs, data, kerns, npts, nends = face(name, fam, weight)
        most_pts, most_ends = max(most_pts, npts), max(most_ends, nends)
        u = name.upper()
        out.append("static const uint8_t PF_%s_DATA[] = {" % u)
        out.append(_rows(data, 16))
        out.append("};")
        out.append("static const moy_path_glyph_t PF_%s_RECS[%d] = {" % (u, len(glyphs)))
        out.append("\n".join("    {%d, %d}," % g for g in glyphs))
        out.append("};")
        kern = "NULL, NULL, NULL"
        if kerns:
            kerns = sorted(kerns)
            at = [sum(1 for k in kerns if k[0] < a) for a in range(0x20, 0x80)]
            out.append("static const uint16_t PF_%s_KERN_AT[%d] = {" % (u, len(at)))
            out.append(_rows(at))
            out.append("};")
            out.append("static const uint8_t PF_%s_KERN_B[] = {" % u)
            out.append(_rows([k[1] for k in kerns], 16))
            out.append("};")
            out.append("static const int16_t PF_%s_KERN_DX[] = {" % u)
            out.append(_rows([k[2] for k in kerns]))
            out.append("};")
            kern = "PF_%s_KERN_AT, PF_%s_KERN_B, PF_%s_KERN_DX" % (u, u, u)
        out.append("")
        table.append("    {%d, %d, %d, PF_%s_RECS, PF_%s_DATA, %s},"
                     % (upm, asc, desc, u, u, kern))
    out.append("// The most points and contours one glyph has, across the faces.")
    out.append("#define PF_MOST_POINTS %d" % most_pts)
    out.append("#define PF_MOST_CONTOURS %d" % most_ends)
    out.append("")
    out.append("static const moy_path_face_t PF_FACES[MOY_PATH_FACES] = {")
    out.extend(table)
    out.append("};")
    out.append("")
    out.append("#endif")
    return "\n".join(out) + "\n"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd not in ("build", "check"):
        print(__doc__)
        return 2
    text = render()
    if cmd == "build":
        with open(OUT, "w") as f:
            f.write(text)
        print("make_outline_faces: wrote %s (%d bytes)" % (os.path.relpath(OUT, ROOT), len(text)))
        return 0
    with open(OUT) as f:
        same = f.read() == text
    print("make_outline_faces: %s" % ("up to date" if same else "STALE: run build"))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
