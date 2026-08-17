"""The two glyph tables agree, and nothing else was checking.

There are two copies of the petme128 font in this tree, and both are live:

  * `runtime/font.py`'s `_FONT` -- what the shell and a PYTHON cart draw with.
    `moy_gfx`'s `text`/`make_draw_ctx` take it as a blob argument from Python
    ("exactly runtime/font.py's _FONT, the SAME", modmoy_gfx.c), so every tier
    that draws through moy_gfx is reading this one.
  * libmoy's `moy_font_data[96 * 8]` (`moy_data.c`, vendored from moy-spec,
    hashed there as SPEC.md 6's `font.bin`) -- what `moy_print` reads, which
    is what a LUA cart's `print` reaches through moycore's binding.

So the language a cart is written in decides which table renders its text.

WHY THIS FILE EXISTS. `modmoy_gfx.c` claims the two are "pinned byte-identical
by the text conformance scenes". They are byte-identical; the pinning was not
real. The conformance scenes replay through `runtime/host_canvas.py` -> moy_gfx,
which is handed font.py's blob -- so both sides of that comparison read the SAME
table and a change to libmoy's copy could not move the hash. Nothing anywhere
referenced `moy_font_data`.

That is the shape of the bug `test_p8_import_vendor.py` was written about: two
copies of one constant, a comment asserting they agree, and no lane where a
disagreement turns something red. The p8 pitch offset drifted exactly this way
and cost ten days of silently-flat imports.

Re-vendoring libmoy is what would move its copy (`make vendor-libmoy` --
`tests/test_libmoy_vendor.py` guards the copy against UPSTREAM, this guards it
against font.py). If this test goes red, do not "fix" it by editing the
vendored file: work out which table upstream intends, and if it is libmoy's,
bring `runtime/font.py` to it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOY_DATA = (
    REPO
    / "native"
    / "moy_gfx"
    / "libmoy"
    / "moy_data.c"
)

_TABLE = re.compile(r"const uint8_t moy_font_data\[96 \* 8\]\s*=\s*\{(.*?)\};", re.S)
_BYTE = re.compile(r"0x[0-9a-fA-F]+|\b\d+\b")

# 96 printable ASCII glyphs, 8 column-major bytes each -- SPEC.md 6.
GLYPHS = 96
COLS = 8
FIRST = 32


def _libmoy_font() -> bytes:
    """libmoy's compiled-in glyph table, read out of the vendored C source."""
    src = MOY_DATA.read_text()
    match = _TABLE.search(src)
    assert match, "moy_font_data[96 * 8] not found in %s -- did the vendored " \
                  "table change shape? Reading it is the whole point of this test." % MOY_DATA
    data = bytes(int(tok, 0) for tok in _BYTE.findall(match.group(1)))
    assert len(data) == GLYPHS * COLS, (
        "moy_font_data parsed to %d bytes, expected %d" % (len(data), GLYPHS * COLS)
    )
    return data


def _host_font() -> bytes:
    from runtime import font

    return bytes(font._FONT)


def test_the_host_and_libmoy_glyph_tables_are_identical():
    """A python cart and a lua cart draw the same letters."""
    host = _host_font()
    lib = _libmoy_font()

    assert len(host) == GLYPHS * COLS, (
        "runtime/font.py's _FONT is %d bytes, not the %d SPEC.md 6 fixes -- if the "
        "host font grew glyphs, libmoy's table has to grow them too or lua carts "
        "lose them." % (len(host), GLYPHS * COLS)
    )

    if host == lib:
        return

    # Name the glyph, not the byte offset: "byte 419 differs" sends the reader
    # counting, and the character is what they can actually look at.
    bad = [i for i in range(len(host)) if host[i] != lib[i]]
    detail = ", ".join(
        "%r (col %d): font.py 0x%02x vs libmoy 0x%02x"
        % (chr(FIRST + i // COLS), i % COLS, host[i], lib[i])
        for i in bad[:6]
    )
    raise AssertionError(
        "%d of %d glyph bytes differ, across %d character(s): %s%s\n"
        "A python cart draws font.py's shapes and a lua cart draws libmoy's, so "
        "this is a visible difference in the same text on the same board."
        % (
            len(bad),
            len(host),
            len({i // COLS for i in bad}),
            detail,
            ", ..." if len(bad) > 6 else "",
        )
    )


def test_the_host_font_starts_at_space():
    """Both tables index from codepoint 32; an off-by-one here shifts every glyph."""
    from runtime import font

    assert getattr(font, "FIRST", FIRST) == FIRST, (
        "runtime/font.py's FIRST moved off %d. libmoy's table is indexed from "
        "space with no such constant to follow, so moving one shifts lua text by "
        "the difference and nothing else notices." % FIRST
    )
