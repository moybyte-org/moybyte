#!/usr/bin/env python3
"""The console's font set: TrueType outlines pre-rasterised at fixed sizes
into font files, format version 2 (native/moy_gfx/moy_text.h has the format,
docs/theming_2026-09.md §4.7 its rules, docs/look_2026-10.md step 3 the set).

FreeType through Pillow, hinted, one size of one weight a file, as
docs/media/look_2026-10/mockups.py rasterises its target pictures: a glyph's
ink box with its bearing and advance, its coverage quantised to 1, 2 or 4
bits a pixel, and the kerning pairs HarfBuzz (Pillow's raqm layout) answers
at that size between ASCII's glyphs and the byte map's symbols. A family that lacks a glyph gets an ASCII stand-in, and the
tool says so.

  fetch    the pinned TrueType files into the cache (MOY_FONT_CACHE, else
           ~/.cache/moybyte-look-fonts), each checked against its SHA-256,
           and each family's licence into native/moy_ui/fonts/FAMILY.txt.
  build    every face of the set -> native/moy_ui/fonts/*.fnt and set.json
           (the faces, the set each tier takes, the sources).
  check    rebuild in memory and compare with the committed files.
  face     TTF SIZE WEIGHT BPP OUT [CHARS]: one face from any TrueType file
           (tests/test_make_font.py builds its fixture with it).
  report   the bytes each tier's set costs.
  module   BOARD_DIR OUT: the freezable `fonts_data` of the board's tier
           (board.toml [board] `font_tier`, else `tier`), every face and family
           licence it takes, and its icon sets' files (tools/make_icons.py);
           runtime/fontset.py reads both.

The committed files are the build's output under the FreeType and raqm
versions set.json records; `check` refuses to compare under others, since a
different hinter moves pixels.

Run: python tools/make_font.py [fetch | build | check | report | face ...]
"""

import hashlib
import json
import os
import re
import struct
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "native", "moy_ui", "fonts")
HEADER = os.path.join(ROOT, "native", "moy_gfx", "moy_text.h")
CACHE = os.environ.get("MOY_FONT_CACHE", os.path.expanduser("~/.cache/moybyte-look-fonts"))

# google/fonts, pinned: every family the set takes, SIL OFL-1.1.
GF = "https://raw.githubusercontent.com/google/fonts/954e256c4277419b5e48bffa51be069b2bd01bde/ofl/"
FAMILIES = {
    # IBM Plex Sans reserves the name "Plex" (its OFL.txt), and a rasterised
    # subset is a Modified Version, so the faces built from it are named
    # Day Sans, after the theme that uses them.
    "daysans": dict(
        source="IBM Plex Sans", ttf=GF + "ibmplexsans/IBMPlexSans%5Bwdth,wght%5D.ttf",
        sha256="3b031aa4216174205bd8471f88a49b91f093169e9e87bd5262242bc5967fe2e3",
        licence=GF + "ibmplexsans/OFL.txt",
        licence_sha256="7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da"),
    "inter": dict(
        source="Inter", ttf=GF + "inter/Inter%5Bopsz,wght%5D.ttf",
        sha256="29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031",
        licence=GF + "inter/OFL.txt",
        licence_sha256="5b9321a4298cfeb6b34354164a1c3afc3db114569984c502b9b35d988fd58c57"),
    "atkinson": dict(
        source="Atkinson Hyperlegible Next",
        ttf=GF + "atkinsonhyperlegiblenext/AtkinsonHyperlegibleNext%5Bwght%5D.ttf",
        sha256="5a455d1cfa099b601ab70751bb9673e8fe1854dc4500c80e1a220d0d75e31745",
        licence=GF + "atkinsonhyperlegiblenext/OFL.txt",
        licence_sha256="aca6a428580965d2297d1b718042dd427c2a9443ece3b0d02d758e161e0c4030"),
    "jbmono": dict(
        source="JetBrains Mono", ttf=GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        sha256="48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda",
        licence=GF + "jetbrainsmono/OFL.txt",
        licence_sha256="b2fe5e8987594e9ffd1d2ca52a2f5d73eb8335243893c5d6254b5ad69269591d"),
}

# The roles' sizes in px, weight step and bpp (docs/media/look_2026-10/DESIGN.md
# "Type"; the handheld's Calc roles are mockups.py's). "calc" faces carry only
# CALC's glyphs.
HANDHELD = {"tab": (13, "semi", 2), "status": (12, "med", 2), "title": (19, "bold", 4),
            "meta": (12, "body", 2), "key": (11, "semi", 2), "hint": (12, "med", 2),
            "hkey": (16, "med", 4, "calc"), "hop": (17, "med", 4, "calc"),
            "hres": (28, "semi", 4, "calc"), "htape": (12, "body", 2), "htitle": (14, "semi", 4)}
DESKTOP = {"chip": (15, "med", 4), "status": (15, "med", 4), "label": (13, "med", 4),
           "wtitle": (15, "semi", 4), "row": (15, "body", 4), "col": (12, "semi", 4),
           "meta": (14, "body", 4), "tape": (15, "body", 4), "result": (44, "semi", 4, "calc"),
           "keypad": (22, "med", 4, "calc"), "op": (24, "med", 4, "calc")}
STEPS = {"body": 400, "med": 500, "semi": 600, "bold": 700}
# The built-in themes' families, weight steps and size overrides
# (docs/media/look_2026-10/THEMES.md, mockups.py's theme table).
THEMES = {
    "moy-day": ("daysans", {}, {}),
    "moy-night": ("inter", {}, {}),
    "blocks": ("atkinson", {"semi": 700, "bold": 800}, {}),
    "contrast": ("atkinson", {"med": 600, "semi": 700, "bold": 800},
                 {"handheld": {"hkey": 18, "hop": 19, "hres": 30, "htape": 13, "htitle": 15},
                  "desktop": {"keypad": 26, "op": 28, "result": 50, "tape": 17, "wtitle": 17}}),
}
MONO = ("jbmono", 11, 400, 2)     # the Code tab: a 7 px cell, on every tier
TIERS = {"handheld": HANDHELD, "desktop": DESKTOP}

CALC = "0123456789.,−×÷+%=CE"
# Each stand-in a family that lacks the glyph draws instead (the byte map's
# symbols); a symbol whose stand-in is missing too is left uncovered.
STAND_INS = {"−": "-", "←": "<", "→": ">", "↑": "^", "↓": "v",
             "•": "·", "“": '"', "”": '"', "‘": "'", "’": "'",
             "–": "-", "—": "-"}
# HarfBuzz options for the kerning query: no ligature or contextual swap
# stands in for a pair's own width.
KERN_FEATURES = ["kern", "-liga", "-clig", "-calt", "-dlig"]


def byte_map():
    """The codepoints a version 2 font draws at bytes 0x80 up, from the header
    the reader is compiled with (MOY_FONT_BYTE_MAP)."""
    text = open(HEADER).read()
    m = re.search(r"#define MOY_FONT_BYTE_MAP \{([^}]*)\}", text)
    return [int(v, 16) for v in m.group(1).replace("\\", " ").split(",") if v.strip()]


def to_byte(cp, bmap):
    """The byte a codepoint draws at, or None (moy_text.c's decode)."""
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x80 + bmap.index(cp) if cp in bmap else None


def kerned(b):
    """The bytes kerning pairs are kept between: ASCII's inked glyphs and the
    byte map's symbols. Latin-1's accented letters draw unkerned, which keeps
    a handheld family's pairs a fifth of what every pair costs."""
    return 0x21 <= b <= 0x7E or 0x80 <= b <= 0x9F


def full_chars(bmap):
    return ([chr(c) for c in range(0x20, 0x7F)] + [chr(c) for c in range(0xA0, 0x100)]
            + [chr(c) for c in bmap])


# -- rasterising -------------------------------------------------------------------

def _vary(f, size, weight):
    """The variable font's instance: `weight`, the optical size at `size`."""
    try:
        axes = f.get_variation_axes()
    except OSError:
        return
    vals = []
    for ax in axes:
        n = ax["name"].decode() if isinstance(ax["name"], bytes) else ax["name"]
        if "Weight" in n:
            vals.append(max(ax["minimum"], min(ax["maximum"], weight)))
        elif "Optical" in n:
            vals.append(max(ax["minimum"], min(ax["maximum"], size)))
        else:
            vals.append(ax["default"])
    f.set_variation_by_axes(vals)


def versions():
    from PIL import features
    return {"freetype2": features.version("freetype2"), "raqm": features.version("raqm")}


class Face:
    """One size of one weight of a TrueType font, rasterised."""

    def __init__(self, ttf, size, weight, bpp):
        from PIL import ImageFont, features
        if not features.check("raqm"):
            raise SystemExit("make_font: Pillow has no raqm layout, so no kerning")
        self.size, self.bpp = size, bpp
        self.f = ImageFont.truetype(ttf, size, layout_engine=ImageFont.Layout.BASIC)
        _vary(self.f, size, weight)
        self.fk = ImageFont.truetype(ttf, size, layout_engine=ImageFont.Layout.RAQM)
        _vary(self.fk, size, weight)
        self._tofu = self._ink("￿")

    def _ink(self, ch):
        from PIL import Image, ImageDraw
        im = Image.new("L", (4 * self.size, 2 * self.size))
        ImageDraw.Draw(im).text((self.size, self.size * 3 // 2), ch, font=self.f,
                                anchor="ls", fill=255)
        return im.tobytes()

    def has(self, ch):
        return ch == " " or self._ink(ch) != self._tofu

    def glyph(self, ch):
        """(w, h, x, y, advance, levels): the ink box, its left from the pen,
        its top above the baseline, and its pixels' levels row-major."""
        from PIL import Image, ImageDraw
        adv = round(self.f.getlength(ch))
        x0, y0, x1, y1 = self.f.getbbox(ch, anchor="ls")
        if x1 <= x0 or y1 <= y0:
            return 0, 0, 0, 0, adv, []
        m = Image.new("L", (x1 - x0, y1 - y0), 0)
        d = ImageDraw.Draw(m)
        if self.bpp == 1:
            d.fontmode = "1"
        d.text((-x0, -y0), ch, font=self.f, anchor="ls", fill=255)
        top = (1 << self.bpp) - 1
        w, h = m.size
        px = m.tobytes()
        if self.bpp == 1:
            lv = [1 if a >= 112 else 0 for a in px]
        else:
            lv = [round(a * top / 255) for a in px]
        rows = [lv[r * w:(r + 1) * w] for r in range(h)]
        # Trim blank rows and columns: the box is the ink's.
        while rows and not any(rows[0]):
            rows.pop(0)
            y0 += 1
        while rows and not any(rows[-1]):
            rows.pop()
        if not rows:
            return 0, 0, 0, 0, adv, []
        left = min(next(i for i, v in enumerate(r) if v) for r in rows if any(r))
        right = max(len(r) - next(i for i, v in enumerate(reversed(r)) if v)
                    for r in rows if any(r))
        rows = [r[left:right] for r in rows]
        return right - left, len(rows), x0 + left, -y0, adv, [v for r in rows for v in r]

    def kern(self, a, b):
        fk = self.fk
        return round(fk.getlength(a + b, features=KERN_FEATURES)
                     - fk.getlength(a, features=KERN_FEATURES)
                     - fk.getlength(b, features=KERN_FEATURES))


def pack(levels, bpp):
    """Row-major levels, the first pixel in the high bits of its byte."""
    out = bytearray((len(levels) * bpp + 7) // 8)
    for i, v in enumerate(levels):
        bit = i * bpp
        out[bit >> 3] |= v << (8 - bpp - (bit & 7))
    return bytes(out)


def build_face(ttf, size, weight, bpp, chars=None, notes=None):
    """A version 2 font file: `chars` (default: every byte the set draws) of
    `ttf` at `size` px and `weight`, at `bpp`."""
    bmap = byte_map()
    face = Face(ttf, size, weight, bpp)
    chars = full_chars(bmap) if chars is None else list(dict.fromkeys(" " + chars))
    notes = [] if notes is None else notes
    index = [0] * 256
    recs, cov, kept = [], bytearray(), []
    by_char = {}
    for ch in chars:
        b = to_byte(ord(ch), bmap)
        if b is None:
            raise SystemExit("make_font: U+%04X is not a byte the format draws" % ord(ch))
        if not face.has(ch):
            sub = STAND_INS.get(ch)
            if sub is not None and face.has(sub):
                notes.append("U+%04X: stand-in %r" % (ord(ch), sub))
                kept.append((b, sub))
            else:
                notes.append("U+%04X: missing, uncovered" % ord(ch))
            continue
        w, h, x, y, adv, lv = face.glyph(ch)
        if not (0 <= w <= 255 and 0 <= h <= 255 and -128 <= x <= 127 and -128 <= y <= 127
                and 0 <= adv <= 255):
            raise SystemExit("make_font: U+%04X does not fit a record" % ord(ch))
        index[b] = len(recs)
        by_char[ch] = len(recs)
        recs.append(struct.pack("<I", len(cov))[:3] + struct.pack("<BBbbB", w, h, x, y, adv))
        cov += pack(lv, bpp)
    for b, sub in kept:
        if sub not in by_char:
            w, h, x, y, adv, lv = face.glyph(sub)
            by_char[sub] = len(recs)
            recs.append(struct.pack("<I", len(cov))[:3] + struct.pack("<BBbbB", w, h, x, y, adv))
            cov += pack(lv, bpp)
        index[b] = by_char[sub]
    if len(recs) > 255:
        raise SystemExit("make_font: %d glyphs, over a byte's 255" % len(recs))
    pairs = []
    if bpp > 1:
        inked = [c for c in chars if c in by_char and kerned(to_byte(ord(c), bmap))]
        for a in inked:
            for c in inked:
                k = face.kern(a, c)
                if k:
                    pairs.append((to_byte(ord(a), bmap), to_byte(ord(c), bmap),
                                  max(-128, min(127, k))))
    pairs.sort()
    ascent, descent = face.f.getmetrics()
    line = ascent + descent
    cell = face.glyph("0")[4] if "0" in by_char else face.glyph(" ")[4]
    head = (b"MFNT" + bytes((2, bpp, line, ascent, cell, len(recs)))
            + struct.pack("<HI", len(pairs), 0))
    body = bytes(index) + b"".join(recs) + b"".join(struct.pack("<BBb", *p) for p in pairs)
    data = head + body + bytes(cov)
    return data[:12] + struct.pack("<I", len(data)) + data[16:]


# -- the set -------------------------------------------------------------------------

def faces():
    """{name: (family, size, weight, bpp, calc)} and {tier: [names]}."""
    out, tiers = {}, {}
    for tier, roles in TIERS.items():
        names = set()
        for _theme, (fam, weights, sizes) in THEMES.items():
            for role, spec in roles.items():
                size, step, bpp = spec[:3]
                calc = len(spec) > 3
                size = sizes.get(tier, {}).get(role, size)
                weight = weights.get(step, STEPS[step])
                key = (fam, size, weight, bpp)
                name = "%s-%d-%d-%d" % key
                was = out.get(name)
                out[name] = key + ((was[4] if was else True) and calc,)
                names.add(name)
        fam, size, weight, bpp = MONO
        name = "%s-%d-%d-%d" % MONO
        out[name] = MONO + (False,)
        names.add(name)
        tiers[tier] = sorted(names)
    return dict(sorted(out.items())), tiers


def ttf_path(fam):
    return os.path.join(CACHE, "%s.ttf" % fam)


def fetch():
    """The pinned TrueType files into the cache; each licence, its line ends
    made LF (as git stores it), into native/moy_ui/fonts/."""
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    for fam, src in FAMILIES.items():
        for url, sha, path, lic in ((src["ttf"], src["sha256"], ttf_path(fam), False),
                                    (src["licence"], src["licence_sha256"],
                                     os.path.join(OUT, fam + ".txt"), True)):
            data = open(path, "rb").read() if os.path.exists(path) and not lic else b""
            if hashlib.sha256(data).hexdigest() != sha:
                data = urllib.request.urlopen(url).read()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise SystemExit("%s: not the pinned file (sha256 %s)" % (url, sha))
                if lic:
                    data = data.replace(b"\r\n", b"\n")
                with open(path, "wb") as f:
                    f.write(data)
            print("%s: %d bytes" % (path, len(data)))


def build_set(quiet=False):
    """{file name: bytes}: every face and set.json."""
    for fam, src in FAMILIES.items():
        p = ttf_path(fam)
        if not os.path.exists(p) or hashlib.sha256(open(p, "rb").read()).hexdigest() != src["sha256"]:
            raise SystemExit("%s: missing or not the pinned file; run `make_font.py fetch`" % p)
    files, table = {}, {}
    allf, tiers = faces()
    for name, (fam, size, weight, bpp, calc) in allf.items():
        notes = []
        data = build_face(ttf_path(fam), size, weight, bpp, CALC if calc else None, notes)
        files[name + ".fnt"] = data
        table[name] = {"family": fam, "size": size, "weight": weight, "bpp": bpp,
                       "chars": "calc" if calc else "full", "bytes": len(data)}
        if notes and not quiet:
            print("%s: %s" % (name, "; ".join(notes)))
    meta = {"about": "The font set tools/make_font.py builds (docs/look_2026-10.md step 3). "
                     "A board takes its tier's faces; FAMILY.txt is each family's licence.",
            "built_with": versions(),
            "families": {f: {"source": s["source"], "ttf": s["ttf"], "sha256": s["sha256"],
                             "licence": f + ".txt"} for f, s in FAMILIES.items()},
            "tiers": tiers, "faces": table}
    files["set.json"] = (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode()
    return files


def render_module(tier):
    """A freezable fonts_data.py: tier `tier`'s faces and their licences as
    bytes literals, which a frozen module keeps in flash, and its icon sets'
    files."""
    meta = json.load(open(os.path.join(OUT, "set.json")))
    names = meta["tiers"][tier]
    fams = sorted({meta["faces"][n]["family"] for n in names})
    out = ['"""GENERATED by tools/make_font.py module from native/moy_ui/fonts/: the',
           '%s tier\'s font set (runtime/fontset.py reads it)."""' % tier,
           "TIER = %r" % tier, "FACES = {"]
    for n in names:
        out.append("    %r: %r," % (n, open(os.path.join(OUT, n + ".fnt"), "rb").read()))
    out.append("}")
    out.append("LICENCES = {")
    for f in fams:
        out.append("    %r: %r," % (f, open(os.path.join(OUT, f + ".txt"), "rb").read()))
    out.append("}")
    sys.path.insert(0, HERE)
    import make_icons
    out += make_icons.module_lines(tier)
    return "\n".join(out) + "\n"


def board_fonts(board_dir):
    """The font tier a board's board.toml declares: `font_tier` where the
    board's own `tier` names no class of the set (the browser draws in the
    handheld's metrics), else its `tier`."""
    sys.path.insert(0, HERE)
    import board_config
    b = board_config.load(board_dir)["board"]
    return b.get("font_tier", b["tier"])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return
    if cmd == "module":
        tier = board_fonts(argv[2])
        with open(argv[3], "w") as f:
            f.write(render_module(tier))
        print("%s: the %s font set" % (argv[3], tier))
        return
    if cmd == "fetch":
        return fetch()
    if cmd == "face":
        ttf, size, weight, bpp, out = argv[2], int(argv[3]), int(argv[4]), int(argv[5]), argv[6]
        notes = []
        data = build_face(ttf, size, weight, bpp, argv[7] if len(argv) > 7 else None, notes)
        with open(out, "wb") as f:
            f.write(data)
        print("%s: %d bytes%s" % (out, len(data), "; " + "; ".join(notes) if notes else ""))
        return
    if cmd == "report":
        meta = json.load(open(os.path.join(OUT, "set.json")))
        for tier, names in meta["tiers"].items():
            by = {}
            for n in names:
                f = meta["faces"][n]
                by[f["family"]] = by.get(f["family"], 0) + f["bytes"]
            lic = sum(os.path.getsize(os.path.join(OUT, f + ".txt")) for f in by)
            print("%s: %d faces, %d bytes (%s), licences %d"
                  % (tier, len(names), sum(by.values()),
                     ", ".join("%s %d" % kv for kv in sorted(by.items())), lic))
        return
    if cmd == "check":
        meta = json.load(open(os.path.join(OUT, "set.json")))
        if meta["built_with"] != versions():
            raise SystemExit("make_font check: built with %s, this is %s"
                             % (meta["built_with"], versions()))
    files = build_set(quiet=cmd == "check")
    if cmd == "check":
        bad = [n for n, d in files.items()
               if not os.path.exists(os.path.join(OUT, n))
               or open(os.path.join(OUT, n), "rb").read() != d]
        if bad:
            raise SystemExit("not what make_font.py builds: %s" % ", ".join(bad))
        return
    os.makedirs(OUT, exist_ok=True)
    for n, d in files.items():
        with open(os.path.join(OUT, n), "wb") as f:
            f.write(d)
    total = sum(len(d) for n, d in files.items() if n.endswith(".fnt"))
    print("%s: %d faces, %d bytes" % (OUT, len(files) - 1, total))


if __name__ == "__main__":
    main(sys.argv)
