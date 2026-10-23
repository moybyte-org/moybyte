#!/usr/bin/env python3
"""The licences the console carries (docs/look_2026-10.md step 32, Licences):
`runtime/licences.py`, built from the texts THIRD_PARTY.md names.

Each licence BODY is stored once, raw-deflated, a paragraph a line: the
MIT text, Apache-2.0, the LLVM exception, the SIL Open Font Licence, the
4.4BSD text Berkeley DB carries and Moybyte's FSL-1.1-MIT. An ENTRY is a
part the console is made with: its name, its licence's short name, its own
notice (its copyright lines, uncompressed) and the bodies that follow it,
and the boards (`[board] ota` ids) whose image holds it, None for every
board. The sources are the tracked files below; a body two sources share
(the fonts' OFL, the MIT text) is checked equal, not assumed.

  build    write runtime/licences.py
  check    rebuild in memory and compare (tests/test_licences.py)
  report   the bytes the bodies and the notices take, raw and deflated

Run: python tools/make_licences.py [build | check | report]
"""

import os
import re
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "runtime", "licences.py")

LEAD = "Free to use and change; each release becomes MIT after two years."
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def paragraphs(text):
    """A plain or Markdown text as paragraphs: blank lines part them, a
    heading or a list item starts one, rules and Markdown marks drop."""
    out, cur = [], []

    def flush():
        if cur:
            out.append(" ".join(cur))
            del cur[:]

    for raw in text.split("\n"):
        line = raw.strip()
        if not line or re.fullmatch(r"[-=*_]{3,}", line):
            flush()
            continue
        head = re.match(r"#+\s+(.*)", line)
        if head:
            flush()
            out.append(head.group(1))
            continue
        if re.match(r"(\d+\.|\([a-z0-9]+\)|[a-z]\))\s", line) or line.startswith("- "):
            flush()
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        cur.append(line)
    flush()
    return out


def _mit():
    """The MIT text after its copyright line (LICENSES/MIT.md's, which Lua's
    COPYRIGHT and every MIT notice here repeat word for word)."""
    p = paragraphs(_read("LICENSES/MIT.md"))
    assert p[0] == "MIT License" and p[1].startswith("Copyright"), p[:2]
    body = p[2:]
    lua = paragraphs(_read("native/moy_lua/lua/COPYRIGHT"))
    i = next(k for k, s in enumerate(lua) if s.startswith("Copyright (C) 1994"))
    assert lua[i + 1:i + 1 + len(body)] == body, "Lua's MIT text is not LICENSES/MIT.md's"
    return body, lua[i]


def _apache():
    p = paragraphs(_read("native/p4/moy_dsi/vendor/license.txt"))
    w = paragraphs(_read("native/moy_wasm/wamr/LICENSE"))
    k = next(i for i, s in enumerate(w) if s.startswith("--- LLVM Exceptions"))
    assert w[:k] == p, "WAMR's Apache text is not the panel driver's"
    return p, w[k:]


_FONTS = (("Inter", "native/moy_ui/fonts/inter.txt"),
          ("Day Sans", "native/moy_ui/fonts/daysans.txt"),
          ("Atkinson Hyperlegible", "native/moy_ui/fonts/atkinson.txt"),
          ("JetBrains Mono", "native/moy_ui/fonts/jbmono.txt"),
          ("Tiny5", "native/moy_ui/themes/tiny5.txt"))


def _ofl():
    """(the OFL body, {font: its notice})."""
    body, heads = None, {}
    for name, rel in _FONTS:
        p = paragraphs(_read(rel))
        k = next(i for i, s in enumerate(p) if s.startswith("SIL OPEN FONT LICENSE"))
        if body is None:
            body = p[k:]
        assert p[k:] == body, "%s's OFL text differs" % rel
        heads[name] = [s for s in p[:k] if s.startswith("Copyright")]
    return body, heads


def model():
    """(bodies {id: [paragraph]}, groups)."""
    mit, lua = _mit()
    apache, llvm = _apache()
    ofl, fonts = _ofl()
    fsl = paragraphs(_read("LICENSES/FSL-1.1-MIT.md"))
    bsd = paragraphs(_read("LICENSES/berkeley-db.txt"))[1:]
    bodies = {"mit": mit, "apache": apache, "llvm": llvm, "ofl": ofl, "fsl": fsl, "bsd": bsd}
    moybyte = (
        ("The console", "FSL-1.1-MIT", "Functional Source License 1.1, MIT Future License",
         ("The system, firmware, tools and built-in carts.",), ("fsl",), None),
        ("Cart format", "MIT", "MIT License",
         ("The .moy cart format and cart API (moy-spec).",
          paragraphs(_read("LICENSES/MIT.md"))[1]), ("mit",), None),
    )
    made = [
        ("MicroPython", "MIT", "MIT License",
         ("Copyright (c) 2013-2026 Damien P. George",), ("mit",), None),
        ("ESP-IDF", "Apache-2.0", "Apache License 2.0",
         ("Copyright 2015-2026 Espressif Systems (Shanghai) CO LTD",), ("apache",), None),
        ("Lua 5.4", "MIT", "MIT License", (lua,), ("mit",), None),
        ("WAMR", "Apache-2.0", "Apache License 2.0 with LLVM Exceptions",
         ("WebAssembly Micro Runtime, the Bytecode Alliance.",), ("apache", "llvm"), None),
        ("Berkeley DB", "BSD", "BSD License (4.4BSD)",
         ("Berkeley DB 1.85, as MicroPython's btree module carries it.",), ("bsd",),
         ("tdeck", "guition_s3")),
    ]
    for name, _rel in _FONTS:
        made.append((name, "OFL-1.1", "SIL Open Font License 1.1", tuple(fonts[name]), ("ofl",),
                     None))
    made += [
        ("Pixelarticons", "MIT", "MIT License", ("Copyright (c) 2019 Gerrit Halfmann",),
         ("mit",), None),
        ("PICO-8 palette", "CC0", "CC0 1.0 Universal",
         ("The first sixteen colours and their names are PICO-8's, by Lexaloffle Games, "
          "who release the palette under CC0 1.0: no rights reserved.", CC0_URL), (), None),
        ("ST7789 panel setup", "MIT", "MIT License",
         ("The T-Deck panel's register values, from lvgl_micropython.",
          "Copyright (c) 2024-2025 Kevin G. Schlosser"), ("mit",), ("tdeck",)),
        ("AXS15231B panel setup", "MIT", "MIT License",
         ("The Guition panel's register values, from ESPHome.", "Copyright (c) 2019 ESPHome"),
         ("mit",), ("guition_s3",)),
        ("EK79007 panel driver", "Apache-2.0", "Apache License 2.0",
         ("Copyright 2023-2025 Espressif Systems (Shanghai) CO LTD",), ("apache",), ("p4",)),
        ("JD9365 panel driver", "Apache-2.0", "Apache License 2.0",
         ("Copyright 2024 Espressif Systems (Shanghai) CO LTD",), ("apache",), ("guition_p4",)),
        ("GSL3680 touch firmware", "Silead", "Silead's firmware",
         ("The touch controller's firmware, by Silead, redistributed as Guition ships it; "
          "Silead ships no licence with it.",), (), ("guition_p4",)),
    ]
    return bodies, (("Moybyte", moybyte), ("Made with", tuple(made)))


def _z(text):
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(text.encode("utf-8")) + c.flush()


def render():
    bodies, groups = model()
    out = ["# Map (grep -n a name to jump there):",
           "#   _inflate   a body's text out of its raw deflate",
           "#   text       an entry's paragraphs: its notice, then its bodies'",
           "#   shown      whether a board's image holds an entry",
           '"""The licences the console carries (docs/look_2026-10.md step 32), as',
           "Settings' Licences page shows them: GENERATED by tools/make_licences.py from",
           "the texts THIRD_PARTY.md names; change the sources and rebuild",
           "(tests/test_licences.py holds this file to them).",
           "",
           "  GROUPS     (caption, ((name, licence, its title, notice, bodies, boards), ...))",
           "  text(e)    an entry's paragraphs: its notice, then its bodies'",
           "  shown(e, board)  whether the image `board` names holds the entry",
           '"""', "", "LEAD = %r" % LEAD, "",
           "# Each body raw-deflated, a paragraph a line.", "_BODIES = {"]
    for k in sorted(bodies):
        out.append("    %r: %r," % (k, _z("\n".join(bodies[k]))))
    out += ["}", "", "GROUPS = ("]
    for cap, entries in groups:
        out.append("    (%r, (" % cap)
        for e in entries:
            out.append("        %r," % (e,))
        out.append("    )),")
    out += [")", "", "",
            "def _inflate(b):",
            "    try:",
            "        import deflate",
            "        import io",
            "        return deflate.DeflateIO(io.BytesIO(b), deflate.RAW).read()",
            "    except ImportError:",
            "        import zlib",
            "        return zlib.decompress(b, -15)",
            "", "",
            "def text(entry):",
            '    """`entry`\'s paragraphs: its notice, then each body it names."""',
            "    out = list(entry[3])",
            "    for k in entry[4]:",
            '        out.extend(str(_inflate(_BODIES[k]), "utf-8").split("\\n"))',
            "    return out",
            "", "",
            "def shown(entry, board):",
            '    """Whether the image `board` (its `[board] ota` id, None for the host and',
            '    the browser) holds `entry`."""',
            "    return entry[5] is None or board is None or board in entry[5]",
            ""]
    return "\n".join(out)


def report():
    bodies, groups = model()
    raw = z = 0
    for k in sorted(bodies):
        t = "\n".join(bodies[k])
        print("  body %-7s raw %6d  deflated %6d" % (k, len(t.encode()), len(_z(t))))
        raw += len(t.encode())
        z += len(_z(t))
    notes = sum(len(" ".join(e[3]).encode()) for _c, es in groups for e in es)
    print("  notices      raw %6d (stored as they are)" % notes)
    print("  total        raw %6d  as stored %6d" % (raw + notes, z + notes))
    full = 0
    for _c, es in groups:
        for e in es:
            t = "\n".join(list(e[3]) + [p for k in e[4] for p in bodies[k]])
            full += len(_z(t))
    print("  each entry's whole text deflated alone, summed: %d" % full)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd == "report":
        report()
        return 0
    text = render()
    if cmd == "check":
        with open(OUT, encoding="utf-8") as f:
            ok = f.read() == text
        print("make_licences check: %s" % ("matches its sources" if ok else "STALE"))
        return 0 if ok else 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("make_licences: wrote %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
