#!/usr/bin/env python3
"""Offline PICO-8 `.p8` / `.p8.png` -> Moybyte `.moy` importer (no Lua VM).

    import_p8.py <cart.p8> <out_dir.moy>

THE CONVERTER IS NOT HERE. `tools/p8_import.py` beside this file is moy-spec's
converter, VENDORED (see `tools/vendor_p8_import.py`) rather than copied: the
sheet, the SFX bank and the music tracks are its work, and this file must not
re-implement or "improve" a line of it.

THE PORTER IS NOT HERE EITHER. `tools/p8_lua_port.py`, also vendored, converts
the cart's own Lua and writes the `.moy` FOLDER -- so an imported cart RUNS
(owner call, 2026-08-29). The Python porting scaffold this tool used to emit is
gone with it: transcribing Lua into Python when the two are nearly the same text
teaches syntax rather than game-making. `docs/two_languages.md` is where the
language differences live now.

WHAT IS OURS is `tools/p8_writer.py` -- the input guards, the `os.path` shim and
the compatibility report -- and it is a separate file because the BROWSER
imports carts too: a `.p8` dropped on the wasm console goes through that same
file, staged into its frozen set by `firmware/web_runner/build.sh`. A second
copy over there would drift exactly the way the converter once did (below), one
level down. What is left HERE is the CLI and the host glue.

That split is not tidiness. This file used to carry its own copy of the whole
converter, upstream corrected the PICO-8 pitch offset in theirs, and ours never
heard about it -- every cart imported here came out TWO OCTAVES flat, for ten
days, while `make test` stayed green because the tests had pinned the wrong
model too. `tests/test_p8_import_vendor.py` is what makes that impossible now.

Why the assets convert nearly verbatim: the MOY64 palette's first 16 colors *are*
PICO-8's base 16 byte-for-byte (`runtime/palette.py` `_BASE16`), and
`sprites.moygfx` is literally PICO-8 `__gfx__` format (a 16x16 grid of 8x8 tiles,
one hex nibble per pixel). So importing PICO-8 *assets* needs no Lua runtime.
(Feasibility analysis: issue #13.)

What a run writes into the `.moy` FOLDER:

  __gfx__   -> sprites.moygfx   near-verbatim nibble copy (vendored converter).
  __sfx__   -> sounds.json      full fidelity (vendored converter): 8 waveforms,
   __music__                    the effect nibble in PICO-8's own numbering,
                                4-channel music rows, SFX loop ranges, per-row
                                pattern lengths, and SPEC.md 8.1's pitch
                                mapping (p8 pitch 33 = A4 = moy 57, offset 24).
                                Only the 8 CUSTOM instruments stay unmodelled.
  __lua__   -> main.lua         the cart's own Lua, mechanically converted to
   __gff__                      Lua 5.4 (`!=`, `+=`, one-line `if`) under a
                                generated PICO-8 compat shim, with the flag
                                table baked in beside it.
  __map__   -> map.moymap       all 64 rows -- including the 32 PICO-8 keeps in
                                the bottom half of __gfx__ -- in the console's
                                own tilemap format, so the Map editor opens it.
  header/   -> manifest.json    title (from a `__lua__` comment line, else the
   filename                     filename), "runtime": lua, the 128x128 p8 canvas
                                + the view(128, 120) zoom hint, and
                                safe_to_share false (BBS carts are CC BY-NC-SA).

DEFERRED (intentionally, noted rather than guessed):
  __label__      the cart's label image; nothing shows it.

Stdlib only (the vendored converter is stdlib only too, which is what makes the
browser import in #194 cheap).
"""

import os
import sys

# The vendored converter and the shared writer both sit beside this file.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Re-exported so callers (and tests) reach the converter through the tool they
# already import. Listed one by one on purpose: a `*` here is how a name would
# quietly stop being upstream's.
from p8_import import (  # noqa: E402
    GFX_H,
    GFX_TILES,
    GFX_W,
    PICO8_PITCH_C0,
    _IMPORT_INSTRUMENT_TO_WAVE,
    _music_line_channels,
    _music_line_row,
    _music_tracks,
    _row_secs,
    _sfx_line_to_dict,
    _sfx_meta,
    _title_from,
    gfx_to_kgfx,
    icon_tile,
    music_start_map,
    parse_p8,
    read_p8,
    sfx_music_to_sounds,
)
# The `.moy` folder writer -- ours, but shared with the wasm console (#194), so
# it lives in its own file rather than in this one. Same one-by-one listing for
# the same reason: a `*` is how a name quietly stops being the shared body's.
from p8_writer import (  # noqa: E402
    SHIM_GAPS,
    cart_title,
    looks_like_png,
    png_problem,
    report_lines,
    scan_lua_verbs,
    sections_problem,
    write_cart,
)

__all__ = [
    # upstream's, re-exported verbatim (tools/p8_import.py)
    "GFX_H", "GFX_TILES", "GFX_W", "PICO8_PITCH_C0",
    "_IMPORT_INSTRUMENT_TO_WAVE", "_music_line_channels", "_music_line_row",
    "_music_tracks", "_row_secs", "_sfx_line_to_dict", "_sfx_meta",
    "_title_from", "gfx_to_kgfx", "icon_tile", "music_start_map", "parse_p8",
    "read_p8", "sfx_music_to_sounds",
    # the shared half's, re-exported so a caller reaches it through the tool
    # it already imports (tools/p8_writer.py)
    "SHIM_GAPS", "cart_title", "looks_like_png", "png_problem", "report_lines",
    "scan_lua_verbs", "sections_problem", "write_cart",
    # ours
    "import_p8", "main",
]


def import_p8(p8_path, out_dir):
    """Parse `p8_path` and write a `.moy` folder at `out_dir`. Returns the
    writer's summary dict (see p8_writer.write_cart / report_lines).

    The one line of work that is HOST work and stays here is reading the file off
    a real filesystem; the browser already holds the dropped bytes."""
    sections = read_p8(p8_path)
    return write_cart(sections, out_dir, cart_title(sections, p8_path))


def main(argv):
    if len(argv) != 3:
        prog = os.path.basename(argv[0]) if argv else "import_p8.py"
        sys.stderr.write("usage: %s <cart.p8> <out_dir.moy>\n" % prog)
        return 2
    p8_path, out_dir = argv[1], argv[2]
    if not os.path.isfile(p8_path):
        sys.stderr.write("error: no such file: %s\n" % p8_path)
        return 1
    with open(p8_path, "rb") as f:
        blob = f.read(64)
    # The same guard the browser shows (#194): a frozen build at opt=3 strips the
    # converter's assert-based PNG validation, so the check lives in the shared
    # writer and runs on every tier -- including this one, because a guard that
    # only runs where it was needed is a guard nobody maintains.
    if looks_like_png(blob):
        with open(p8_path, "rb") as f:
            problem = png_problem(f.read())
        if problem:
            sys.stderr.write("error: %s\n" % problem)
            return 1
    summary = import_p8(p8_path, out_dir)
    print("Imported PICO-8 cart -> %s" % out_dir)
    print("  title:    %s" % summary["title"])
    for tag in ("imported", "lossy", "deferred", "empty", "differs",
                "unsupported"):
        for item in summary.get(tag, ()):
            print("  %-12s %s" % (tag + ":", item))
    print("")
    for line in report_lines(summary):
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
