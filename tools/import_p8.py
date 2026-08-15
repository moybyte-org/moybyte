#!/usr/bin/env python3
"""Offline PICO-8 `.p8` / `.p8.png` -> Moybyte `.moy` importer (no Lua VM).

    import_p8.py <cart.p8> <out_dir.moy>

THE CONVERTER IS NOT HERE. `tools/p8_import.py` beside this file is moy-spec's
converter, VENDORED (see `tools/vendor_p8_import.py`) rather than copied: the
sheet, the SFX bank and the music tracks are its work, and this file must not
re-implement or "improve" a line of it. What lives HERE is the half moy-spec has
no opinion about -- the CLI, the `.moy` folder writer, and the guided
PICO-8 -> Python porting scaffold (#36).

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
  __lua__   -> main.py          NOT transpiled / NOT executed. The Lua is
                                imported as a commented-out reference block with
                                a working Python stub on top and inline
                                `# PORT NOTE:` guidance -- ours, below.
  header/   -> manifest.json    title (from a `__lua__` comment line, else the
   filename                     filename), type "game", standard canvas +
                                permissions, empty config/edit.

  (A Lua-runtime port -- `main.lua` plus a p8 compat shim -- is moy-spec's
  `moy port`, not this tool. This one is the Python porting exercise.)

DEFERRED (intentionally, noted rather than guessed):
  __map__        the `.moymap` writer lives in moy-spec's `p8_lua_port` (#32).
  __gff__        per-sprite flag bits -- Moybyte has no sprite-flag model yet.

Stdlib only (the vendored converter is stdlib only too, which is what makes the
browser import in #194 cheap).
"""

import json
import os
import sys

# The vendored converter sits beside this file.
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

__all__ = [
    # upstream's, re-exported verbatim (tools/p8_import.py)
    "GFX_H", "GFX_TILES", "GFX_W", "PICO8_PITCH_C0",
    "_IMPORT_INSTRUMENT_TO_WAVE", "_music_line_channels", "_music_line_row",
    "_music_tracks", "_row_secs", "_sfx_line_to_dict", "_sfx_meta",
    "_title_from", "gfx_to_kgfx", "icon_tile", "music_start_map", "parse_p8",
    "read_p8", "sfx_music_to_sounds",
    # ours
    "CHEATSHEET", "PORT_NOTES", "import_p8", "lua_to_main_py", "main",
    "make_manifest",
]


# --------------------------------------------------------------------------
# __lua__  ->  main.py   (reference comment + GUIDED porting notes; NOT executed)
# --------------------------------------------------------------------------
# This tool ports a PICO-8 cart to PYTHON, and a Python cart can't "just run" a
# Lua one -- the kid PORTS it, and that's the lesson (issue #36). We DON'T
# transpile. Instead we keep the original Lua as a reference comment and scaffold
# the port with inline `# PORT NOTE:` lines for the real PICO-8 -> Moybyte
# gotchas -- but only for the verbs THIS cart actually uses (scanned from its
# Lua), so the guidance is relevant, not boilerplate. See docs/porting_pico8.md
# for the full cheatsheet. (A cart that should stay Lua goes through moy-spec's
# `moy port` instead, which emits main.lua + the p8 compat shim -- #67.)

CHEATSHEET = "docs/porting_pico8.md"

# Each entry: pico8 token -> (note lines, checklist line). `note` may span several
# lines (each becomes a `# PORT NOTE:` line); `checklist` is a one-line tick-box
# the kid ticks off, or None to keep it out of the checklist.
#
# Grouped by the three "gotcha" rules from the #13 analysis + the not-here-yet set.
PORT_NOTES = [
    # -- inverted draw verbs (PICO-8 names are the OPPOSITE fill in Moybyte) ----
    ("rectfill", (
        ["PICO-8 rectfill() = FILLED rect. In Moybyte that verb is rect().",
         "AND the args change: PICO-8 rectfill(x,y,x1,y1,c) takes the opposite",
         "CORNER (x1,y1); Moybyte rect(x,y,w,h,c) takes WIDTH,HEIGHT. Convert:",
         "w = x1-x+1 ; h = y1-y+1."],
        "[ ] rectfill(x,y,x1,y1,c) -> rect(x,y, x1-x+1, y1-y+1, c)")),
    ("rect", (
        ["PICO-8 rect() = OUTLINE. In Moybyte the outline verb is rectb().",
         "(Moybyte rect() is FILLED -- the names are swapped vs PICO-8!)",
         "Args also change from corner (x1,y1) to extent (w,h):",
         "w = x1-x+1 ; h = y1-y+1."],
        "[ ] rect(x,y,x1,y1,c) outline -> rectb(x,y, x1-x+1, y1-y+1, c)")),
    ("circfill", (
        ["PICO-8 circfill(x,y,r,c) = FILLED circle. In Moybyte that is circ().",
         "(Same x,y,r args -- only the name changes.)"],
        "[ ] circfill -> circ")),
    ("circ", (
        ["PICO-8 circ(x,y,r,c) = OUTLINE circle. In Moybyte the outline verb is",
         "circb(). (Moybyte circ() is FILLED -- swapped vs PICO-8!)"],
        "[ ] circ outline -> circb")),
    # -- buttons (numeric -> named) -------------------------------------------
    ("btnp", (
        ["PICO-8 btnp(i) uses NUMBERS 0..5. Moybyte btnp() uses NAMES:",
         "0->'left' 1->'right' 2->'up' 3->'down' 4->'a'(O) 5->'b'(X).",
         "e.g. btnp(4) -> btnp('a')."],
        "[ ] btnp(0..5) numbers -> btnp('left'/'right'/'up'/'down'/'a'/'b')")),
    ("btn", (
        ["PICO-8 btn(i) uses NUMBERS 0..5. Moybyte btn() uses NAMES:",
         "0->'left' 1->'right' 2->'up' 3->'down' 4->'a'(O) 5->'b'(X).",
         "e.g. btn(0) -> btn('left')."],
        "[ ] btn(0..5) numbers -> btn('left'/'right'/'up'/'down'/'a'/'b')")),
    # -- renames (same idea, different name) ----------------------------------
    ("pset", (
        ["PICO-8 pset(x,y,c) sets a pixel. Moybyte uses pix(x,y,c) (3 args = set)."],
        "[ ] pset -> pix")),
    ("pget", (
        ["PICO-8 pget(x,y) reads a pixel. Moybyte uses pix(x,y) (2 args = read)."],
        "[ ] pget -> pix(x,y) (2 args)")),
    ("spr", (
        ["spr(n,x,y) is mostly the same! Moybyte spr(n,x,y) draws tile n.",
         "Moybyte spr(n,x,y, colorkey, scale, flip, w, h): flip 1/2/3 mirrors",
         "h/v/both, and w,h give a multi-tile span -- so big/flipped sprites work",
         "directly (PICO-8's flip_x/flip_y -> flip = flip_x + 2*flip_y)."],
        None)),
    ("print", (
        ["print(s,x,y,c) is mostly 1:1. Moybyte print(s,x,y,c, scale) -- but the",
         "color is a palette INDEX or col('name'), e.g. col('white')."],
        None)),
    ("cls", (
        ["cls(c) is 1:1. (cls() defaults to color 0 / black on both.)"],
        None)),
    ("line", (
        ["line(x0,y0,x1,y1,c) is 1:1 in Moybyte."],
        None)),
    # -- not here (yet) -> adapt or skip --------------------------------------
    ("map", (
        ["map()/mget()/mset() draw or read a TILEMAP -- Moybyte HAS these now (#32):",
         "map(mx,my,w,h, sx,sy, colorkey, scale), mget(x,y), mset(x,y, id)."],
        None)),
    ("mget", (
        ["mget(cx,cy) reads a map cell -- Moybyte has mget(x,y) (#32)."],
        None)),
    ("mset", (
        ["mset(cx,cy,v) writes a map cell -- Moybyte has mset(x,y, id) (#32)."],
        None)),
    ("pal", (
        ["pal(c0,c1) remaps a draw colour -- Moybyte HAS this now (#11), same name;",
         "pal() with no args resets. Per-sprite transparency is also available as",
         "the spr() colorkey arg or palt(c, on)."],
        None)),
    ("palt", (
        ["palt(c, on) sets a transparent colour -- Moybyte HAS this now (#11), same",
         "name; palt() resets. spr()'s colorkey arg also works per draw."],
        None)),
    ("camera", (
        ["camera(x,y) shifts all drawing -- Moybyte HAS this now (#11), same name and",
         "semantics; camera() with no args resets to (0,0). clip(x,y,w,h) is here too."],
        None)),
    ("sspr", (
        ["sspr() stretches part of the sheet. Moybyte has no sspr() -- use spr()",
         "with the scale arg for whole-tile scaling, or skip the stretch."],
        "[ ] sspr: use spr(..., scale=N) or skip")),
    ("fget", (
        ["fget()/fset() read/write per-sprite FLAG bits. Moybyte has no sprite",
         "flags -- track those facts in your own Python data instead."],
        "[ ] fget/fset: keep sprite flags in your own Python dict/list")),
    ("fset", (
        ["fset() sets a sprite flag bit. No sprite flags in Moybyte -- use your own",
         "Python data."],
        None)),
    ("peek", (
        ["peek()/poke() read/write raw memory. Moybyte has NO raw memory access on",
         "purpose (it's a kids' console) -- there is no equivalent; rewrite that",
         "part using normal Python variables/lists."],
        "[ ] peek/poke: REMOVE -- rewrite with normal Python variables")),
    ("poke", (
        ["poke() writes raw memory. Not available in Moybyte by design -- rewrite",
         "with normal Python variables/lists."],
        None)),
]

# Map each "verb" token to its index in PORT_NOTES so duplicates (e.g. peek+poke
# both ticking the same box) don't double-print.
_NOTE_BY_TOKEN = {tok: i for i, (tok, _) in enumerate(PORT_NOTES)}


def _scan_lua_verbs(lua_lines):
    """Return the set of PICO-8 verb tokens (from PORT_NOTES) that appear in this
    cart's Lua as whole-word function calls (`verb` followed by optional spaces
    then `(`). Word-boundary matched so `rect` doesn't fire inside `rectfill` and
    `print` doesn't fire inside `sprint`."""
    text = "\n".join(lua_lines)
    found = set()
    for tok, _ in PORT_NOTES:
        i = 0
        n = len(tok)
        while True:
            j = text.find(tok, i)
            if j < 0:
                break
            i = j + n
            # left boundary: not part of a longer identifier
            if j > 0 and (text[j - 1].isalnum() or text[j - 1] == "_"):
                continue
            # right boundary: must be a call -> optional spaces then '('
            k = j + n
            while k < len(text) and text[k] in " \t":
                k += 1
            if k < len(text) and text[k] == "(":
                found.add(tok)
                break
    return found


def lua_to_main_py(lua_lines, title):
    """A runnable cart stub + the original PICO-8 Lua as a reference comment,
    GUIDED with `# PORT NOTE:` lines + a port checklist for ONLY the PICO-8 verbs
    this cart actually uses. We do NOT transpile or run Lua here (that is
    moy-spec's `moy port`)."""
    safe_title = title.replace('"', "'")
    used = _scan_lua_verbs(lua_lines)
    # keep PORT_NOTES order (gotchas first, not-here-yet last) for stable output
    used_idx = sorted({_NOTE_BY_TOKEN[t] for t in used})

    head = (
        '# Imported from a PICO-8 .p8 by tools/import_p8.py.\n'
        '#\n'
        '# Only the ASSETS were imported (sprites.moygfx, and sounds.json if\n'
        '# present -- full-fidelity: 8 waves, effects, 4-channel music).\n'
        '# This importer targets PYTHON, so a PICO-8 cart does not "just run": you\n'
        '# PORT it, and that is the fun part. The original PICO-8 Lua is kept below\n'
        '# as a REFERENCE COMMENT (NOT executed).\n'
        '#\n'
        '# Cheatsheet (verb-by-verb PICO-8 -> Moybyte map): ' + CHEATSHEET + '\n'
        '#\n'
        '# This stub just draws the imported sprites so you can see the art, then\n'
        '# you rewrite _update/_draw in Python using the notes below.\n'
        '\n'
        'def _draw():\n'
        '    cls(0)\n'
        '    print("imported from .p8", 8, 8, col("white"))\n'
        '    print("' + safe_title + '", 8, 20, col("yellow"))\n'
        '    # show the first row of the imported sprite sheet\n'
        '    for i in range(16):\n'
        '        spr(i, 8 + i * 18, 40)\n'
        '\n'
    )

    # Port checklist (stretch goal): only the boxes that apply to THIS cart.
    checklist_lines = []
    for idx in used_idx:
        box = PORT_NOTES[idx][1][1]
        if box:
            checklist_lines.append(box)
    if checklist_lines:
        head += (
            '\n# ===== PORT CHECKLIST (this cart) =====\n'
            '# Tick each off as you port it from the Lua reference below:\n'
        )
        for box in checklist_lines:
            head += '#   ' + box + '\n'

    # The PORT NOTEs (only for verbs this cart uses), then the Lua reference.
    head += '\n\n# ----- original PICO-8 __lua__ (reference only; not run) -----\n'
    if used_idx:
        head += (
            '# Watch out for these PICO-8 -> Moybyte differences in the code below\n'
            '# (only the verbs THIS cart uses are listed; full map: ' + CHEATSHEET + '):\n'
        )
        for idx in used_idx:
            note_lines = PORT_NOTES[idx][1][0]
            for nl in note_lines:
                head += '# PORT NOTE: ' + nl + '\n'
        head += '#\n'

    body = "\n".join("# " + ln for ln in lua_lines)
    return head + body + "\n"


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def make_manifest(title):
    return {
        "format": "moybyte-cart-v1",
        "title": title,
        "type": "game",
        "runtime": "python",
        "main": "main.py",
        "canvas": "320x240",
        "permissions": ["graphics", "input", "sound"],
        "safe_to_share": True,
        "config": {},
        "edit": [],
    }


# --------------------------------------------------------------------------
# top-level: parse a .p8 and write a .moy folder
# --------------------------------------------------------------------------

def import_p8(p8_path, out_dir):
    """Parse `p8_path` and write a `.moy` folder at `out_dir`. Returns a small
    summary dict describing what was imported / deferred."""
    sections = read_p8(p8_path)
    title = _title_from(sections, p8_path)

    os.makedirs(out_dir, exist_ok=True)

    summary = {
        "title": title,
        "imported": [],
        "lossy": [],
        "deferred": [],
        "empty": [],
    }

    # manifest.json
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(make_manifest(title), f, indent=2)
    summary["imported"].append("manifest.json")

    # main.py (Lua reference + GUIDED port notes for the verbs this cart uses)
    lua_lines = sections.get("lua", [])
    main_py = lua_to_main_py(lua_lines, title)
    with open(os.path.join(out_dir, "main.py"), "w", encoding="utf-8") as f:
        f.write(main_py)
    used_verbs = sorted(_scan_lua_verbs(lua_lines))
    if used_verbs:
        summary["imported"].append(
            "main.py (Lua reference + %d PORT NOTE verbs: %s; cheatsheet: %s)"
            % (len(used_verbs), ", ".join(used_verbs), CHEATSHEET))
    else:
        summary["imported"].append(
            "main.py (Lua reference, no known PICO-8 verbs to annotate)")

    # config.json (empty -- nothing to edit yet)
    with open(os.path.join(out_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    summary["imported"].append("config.json (empty)")

    # sprites.moygfx (near-verbatim from __gfx__)
    kgfx = gfx_to_kgfx(sections.get("gfx", []))
    if kgfx is not None:
        with open(os.path.join(out_dir, "sprites.moygfx"), "w", encoding="utf-8") as f:
            f.write(kgfx)
        summary["imported"].append("sprites.moygfx (from __gfx__, palette is identical)")
    else:
        summary["empty"].append("sprites.moygfx (no __gfx__ pixels)")

    # sounds.json (from __sfx__/__music__)
    sounds, n_sfx, n_music = sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds is not None:
        with open(os.path.join(out_dir, "sounds.json"), "w", encoding="utf-8") as f:
            json.dump(sounds, f, indent=2)
        summary["imported"].append(
            "sounds.json (from __sfx__/__music__: %d sfx, %d music; "
            "8 waves 1:1, effects verbatim, 4-channel rows; only custom "
            "instruments unmodelled)" % (n_sfx, n_music))
    else:
        summary["empty"].append("sounds.json (no __sfx__/__music__)")

    # deferred sections (note, don't import)
    if any(l.strip() for l in sections.get("map", [])):
        summary["deferred"].append("__map__ (tilemap import lives in `moy port`; #32)")
    if any(l.strip() for l in sections.get("gff", [])):
        summary["deferred"].append("__gff__ (no sprite-flag model)")
    if any(l.strip() for l in sections.get("label", [])):
        summary["deferred"].append("__label__ (cart label image not imported)")

    return summary


def main(argv):
    if len(argv) != 3:
        prog = os.path.basename(argv[0]) if argv else "import_p8.py"
        sys.stderr.write("usage: %s <cart.p8> <out_dir.moy>\n" % prog)
        return 2
    p8_path, out_dir = argv[1], argv[2]
    if not os.path.isfile(p8_path):
        sys.stderr.write("error: no such file: %s\n" % p8_path)
        return 1
    summary = import_p8(p8_path, out_dir)
    print("Imported PICO-8 cart -> %s" % out_dir)
    print("  title:    %s" % summary["title"])
    for tag in ("imported", "lossy", "deferred", "empty"):
        for item in summary[tag]:
            print("  %-8s %s" % (tag + ":", item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
