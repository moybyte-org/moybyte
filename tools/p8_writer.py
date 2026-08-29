"""The `.moy` folder WRITER for an imported PICO-8 cart -- ONE body, every tier.

`tools/p8_import.py` beside this file is moy-spec's CONVERTER, vendored
byte-for-byte and hash-pinned. This is the other half, the one moy-spec has no
opinion about: what a `.moy` FOLDER built out of a converted cart looks like,
and the guided PICO-8 -> Python porting scaffold (#36).

WHY IT IS NOT INSIDE `tools/import_p8.py`. The BROWSER writes carts too (#194):
`firmware/web_runner/build.sh` stages this file and the vendored converter into
the wasm console's frozen set, so a `.p8` dropped on the page and a `.p8` handed
to the CLI are written by the SAME code. The alternative -- a second writer in
the runner -- is the shape that already cost this repo ten days of carts
imported two octaves flat (see `tools/import_p8.py`'s header), one level down.

MICROPYTHON-CLEAN, and that is a CONSTRAINT rather than an accident, because
that is what lets the browser (and later a board, #194's 2026-08-28 decision)
run it: no `os.path`, no `os.makedirs`, no f-strings, no `pathlib` -- `json` and
the vendored converter, nothing else. `_mkdirs` and `_write` exist because
MicroPython's `os` has `mkdir` and nothing above it.

WHAT A RUN WRITES INTO THE `.moy` FOLDER:

  __gfx__   -> sprites.moygfx   near-verbatim nibble copy (vendored converter).
  __sfx__   -> sounds.json      full fidelity (vendored converter): 8 waveforms,
   __music__                    the effect nibble in PICO-8's own numbering,
                                4-channel music rows, SFX loop ranges, per-row
                                pattern lengths, and SPEC.md 8.1's pitch mapping
                                (p8 pitch 33 = A4 = moy 57, offset 24). Only the
                                8 CUSTOM instruments stay unmodelled.
  __lua__   -> main.py          NOT transpiled / NOT executed. The Lua is kept
                                as a commented-out reference block with a working
                                Python stub on top and inline `# PORT NOTE:`
                                guidance for the verbs THIS cart uses.
  header/   -> manifest.json    title, canvas 128x128 + the view hint (below),
   filename                     the sheet's first art tile as the launcher icon.

DEFERRED (noted in the summary rather than guessed):
  __map__        the `.moymap` writer lives in moy-spec's `p8_lua_port` (#32).
  __gff__        per-sprite flag bits -- Moybyte has no sprite-flag model yet.

THE ZOOM HINT IS NOT OPTIONAL HERE. A p8 cart is 128x128 (`CANVAS_SIZES` carries
that size "to inherit the PICO-8 back catalogue at native res") and the
`view(128, 120)` hint is what makes a host with room composite the centred rows
at its best integer scale instead of letterboxing the square at 1x. moy-spec's
Lua port puts it behind `--zoom`; a regeneration on 2026-08-11 forgot the flag
and shipped a tiny Celeste to the glass, and on a drag-and-drop import that
failure would fire on EVERY cart -- so here it is unconditional, emitted by
`lua_to_main_py` at the top of every generated `main.py`.
"""

import json

# The vendored converter. Frozen beside us in the wasm console (and reachable
# with `tools/` on sys.path, which is how the CLI and the tests arrive);
# `tools.p8_import` is the same file seen as a package from the repo root.
try:
    import p8_import
except ImportError:  # pragma: no cover -- exercised by whichever entry runs
    from tools import p8_import


CHEATSHEET = "docs/porting_pico8.md"

# The cart canvas an imported p8 declares (SPEC.md 1/3.1) and the viewport hint
# its main.py opens with. 120 is the 8-row concession that lets a 4:3 host fill
# its height exactly (2x = 256x240 on the handheld, 5x = 640x600 on the P4);
# nothing is cropped from the RASTER, only from the presentation.
P8_CANVAS = "128x128"
P8_VIEW_W = 128
P8_VIEW_H = 120


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
         "map(mx,my,w,h, sx,sy, colorkey, scale), mget(x,y), mset(x,y, id).",
         "This importer does NOT bring the cart's __map__ across yet (#32) -- draw",
         "your own in the Map tab, or port the level data by hand."],
        "[ ] map: the cart's __map__ was NOT imported -- redraw it in the Map tab")),
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
_NOTE_BY_TOKEN = {}
for _i in range(len(PORT_NOTES)):
    _NOTE_BY_TOKEN[PORT_NOTES[_i][0]] = _i

# The verbs with NO Moybyte equivalent, for the compatibility report (#194's
# "report, don't crash"): a cart that leans on these needs a human decision, not
# a mechanical rename. Everything else in PORT_NOTES is a rename or an arg shape.
UNSUPPORTED = {
    "peek": "peek()/poke() raw memory -- no equivalent (rewrite with variables)",
    "poke": "peek()/poke() raw memory -- no equivalent (rewrite with variables)",
    "sspr": "sspr() stretch blits -- use spr(..., scale=N) or skip the stretch",
    "fget": "fget()/fset() sprite flags -- no sprite-flag model (keep your own)",
    "fset": "fget()/fset() sprite flags -- no sprite-flag model (keep your own)",
}


def scan_lua_verbs(lua_lines):
    """Return the set of PICO-8 verb tokens (from PORT_NOTES) that appear in this
    cart's Lua as whole-word function calls (`verb` followed by optional spaces
    then `(`). Word-boundary matched so `rect` doesn't fire inside `rectfill` and
    `print` doesn't fire inside `sprint`."""
    text = "\n".join(lua_lines)
    found = set()
    for tok, _note in PORT_NOTES:
        i = 0
        n = len(tok)
        while True:
            j = text.find(tok, i)
            if j < 0:
                break
            i = j + n
            # left boundary: not part of a longer identifier
            if j > 0 and (text[j - 1].isalpha() or text[j - 1].isdigit()
                          or text[j - 1] == "_"):
                continue
            # right boundary: must be a call -> optional spaces then '('
            k = j + n
            while k < len(text) and (text[k] == " " or text[k] == "\t"):
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
    safe_title = title.replace('"', "'")[:16]
    used = scan_lua_verbs(lua_lines)
    # keep PORT_NOTES order (gotchas first, not-here-yet last) for stable output
    idx = {}
    for t in used:
        idx[_NOTE_BY_TOKEN[t]] = True
    used_idx = sorted(idx.keys())

    head = (
        '# Imported from a PICO-8 cart by Moybyte (tools/p8_writer.py).\n'
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
        '# The cart is 128x128 (PICO-8 native). view() is the ZOOM HINT: a screen\n'
        '# with room composites the centred 128x' + str(P8_VIEW_H) + ' at its best\n'
        '# integer scale instead of showing a tiny square in a corner. Keep it.\n'
        'view(' + str(P8_VIEW_W) + ', ' + str(P8_VIEW_H) + ')\n'
        '\n'
        'def _draw():\n'
        '    cls(0)\n'
        '    print("imported .p8", 2, 4, col("white"))\n'
        '    print("' + safe_title + '", 2, 14, col("yellow"))\n'
        '    # the first two rows of the imported sprite sheet\n'
        '    for n in range(32):\n'
        '        spr(n, (n % 16) * 8, 34 + (n // 16) * 8)\n'
        '    print("edit me!", 2, 62, col("white"))\n'
        '\n'
    )

    # Port checklist (stretch goal): only the boxes that apply to THIS cart.
    checklist_lines = []
    for i in used_idx:
        box = PORT_NOTES[i][1][1]
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
        for i in used_idx:
            for nl in PORT_NOTES[i][1][0]:
                head += '# PORT NOTE: ' + nl + '\n'
        head += '#\n'

    body = "\n".join("# " + ln for ln in lua_lines)
    return head + body + "\n"


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def make_manifest(title, icon=None):
    """The imported cart's manifest.

    `safe_to_share` is FALSE on purpose (#194): importing somebody else's cart to
    play and study is fine, republishing it is not, so an imported cart must not
    look like an authored one to any future share path (#122 family)."""
    man = {
        "format": "moybyte-cart-v1",
        "title": title,
        "type": "game",
        "runtime": "python",
        "main": "main.py",
        "canvas": P8_CANVAS,
        "permissions": ["graphics", "input", "sound"],
        "safe_to_share": False,
        "config": {},
        "edit": [],
    }
    if icon is not None:
        man["icon"] = icon
    return man


# --------------------------------------------------------------------------
# the cart's TITLE, on a tier whose `os` stops at mkdir
# --------------------------------------------------------------------------

class _ShimPath:
    @staticmethod
    def basename(p):
        return p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


class _ShimOs:
    path = _ShimPath


def _ensure_os_path():
    """Give the vendored converter an `os.path.basename`, if its tier lacks one.

    `p8_import._title_from` names a cart from its FILENAME when the Lua carries
    no title comment, and reaches `os.path.basename` to do it. MicroPython has no
    `os.path` and no way to grow one -- a built-in module's globals are a fixed
    dict (assignment raises), and a frozen `os.py` would shadow the real `os` for
    the whole console. So the ONE missing primitive is injected into the
    converter's own module globals: a platform shim in exactly the sense
    `shims/zlib.py` is, leaving the hash-pinned file untouched and upstream the
    only thing that knows the title RULE.

    `tests/test_p8_import_vendor.py` pins that `os.path.basename` stays the
    converter's only use of `os`, so a re-vendor that reached for more of the
    module cannot pass this shim off as sufficient."""
    import os
    if hasattr(os, "path"):
        return                                  # CPython: nothing to shim
    if getattr(p8_import.os, "path", None) is None:
        p8_import.os = _ShimOs


def cart_title(sections, filename):
    """The imported cart's display title: upstream's rule, every tier."""
    _ensure_os_path()
    return p8_import._title_from(sections, filename)


# --------------------------------------------------------------------------
# portable filesystem bits (MicroPython's `os` stops at mkdir)
# --------------------------------------------------------------------------

def _mkdirs(path):
    """`os.makedirs(path, exist_ok=True)` for a tier that has only `os.mkdir`."""
    import os
    at = "/" if path.startswith("/") else ""
    for seg in path.split("/"):
        if not seg:
            continue
        at = at + seg if at in ("", "/") else at + "/" + seg
        try:
            os.mkdir(at)
        except OSError:
            pass                     # exists, or a parent we cannot make -- the
                                     # write below is what actually reports


def _write(out_dir, name, text):
    # `encoding=` is load-bearing on CPython (a non-UTF-8 locale would mangle an
    # accented title) and simply ignored by MicroPython's `open`.
    f = open(out_dir + "/" + name, "w", encoding="utf-8")
    try:
        f.write(text)
    finally:
        f.close()


# --------------------------------------------------------------------------
# top-level: converted sections -> a .moy folder
# --------------------------------------------------------------------------

def write_cart(sections, out_dir, title):
    """Write a `.moy` folder at `out_dir` from already-parsed `sections`
    (`p8_import.read_p8`'s output) and `title`. Returns a summary dict describing
    what was imported / deferred / not supported.

    Takes SECTIONS rather than a path so the caller owns the read: the browser
    already holds the dropped bytes, and the CLI already has the file."""
    _mkdirs(out_dir)

    lua_lines = sections.get("lua", [])
    used_verbs = sorted(scan_lua_verbs(lua_lines))

    summary = {
        "title": title,
        "imported": [],
        "lossy": [],
        "deferred": [],
        "empty": [],
        "unsupported": [],
        "verbs": used_verbs,
        "sfx": 0,
        "music": 0,
    }

    # sprites.moygfx (near-verbatim from __gfx__) -- FIRST, because the manifest
    # takes the cart's launcher icon out of the sheet it produces (SPEC.md 3.4).
    kgfx = p8_import.gfx_to_kgfx(sections.get("gfx", []))
    icon = None
    if kgfx is not None:
        _write(out_dir, "sprites.moygfx", kgfx)
        icon = p8_import.icon_tile(kgfx)
        summary["imported"].append("sprites.moygfx (from __gfx__, palette is identical)")
    else:
        summary["empty"].append("sprites.moygfx (no __gfx__ pixels)")

    # manifest.json
    _write(out_dir, "manifest.json", json.dumps(make_manifest(title, icon)))
    summary["imported"].append(
        "manifest.json (canvas %s + the view(%d, %d) zoom hint)"
        % (P8_CANVAS, P8_VIEW_W, P8_VIEW_H))

    # main.py (Lua reference + GUIDED port notes for the verbs this cart uses)
    _write(out_dir, "main.py", lua_to_main_py(lua_lines, title))
    if used_verbs:
        summary["imported"].append(
            "main.py (Lua reference + %d PORT NOTE verbs: %s; cheatsheet: %s)"
            % (len(used_verbs), ", ".join(used_verbs), CHEATSHEET))
    else:
        summary["imported"].append(
            "main.py (Lua reference, no known PICO-8 verbs to annotate)")

    # config.json (empty -- nothing to edit yet)
    _write(out_dir, "config.json", "{}")
    summary["imported"].append("config.json (empty)")

    # sounds.json (from __sfx__/__music__)
    sounds, n_sfx, n_music = p8_import.sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds is not None:
        _write(out_dir, "sounds.json", json.dumps(sounds))
        summary["sfx"] = n_sfx
        summary["music"] = n_music
        summary["imported"].append(
            "sounds.json (from __sfx__/__music__: %d sfx, %d music; "
            "8 waves 1:1, effects verbatim, 4-channel rows; only custom "
            "instruments unmodelled)" % (n_sfx, n_music))
    else:
        summary["empty"].append("sounds.json (no __sfx__/__music__)")

    # deferred sections (note, don't import)
    for line in sections.get("map", []):
        if line.strip():
            summary["deferred"].append(
                "__map__ (tilemap import lives in `moy port`; #32)")
            break
    for line in sections.get("gff", []):
        if line.strip():
            summary["deferred"].append("__gff__ (no sprite-flag model)")
            break
    for line in sections.get("label", []):
        if line.strip():
            summary["deferred"].append("__label__ (cart label image not imported)")
            break

    # verbs with no Moybyte equivalent at all
    seen = {}
    for v in used_verbs:
        text = UNSUPPORTED.get(v)
        if text is not None and text not in seen:
            seen[text] = True
            summary["unsupported"].append(text)

    return summary


# --------------------------------------------------------------------------
# the compatibility REPORT (#194: report, don't crash)
# --------------------------------------------------------------------------

def report_lines(summary):
    """A short, human compatibility summary of one import.

    The headline is the thing an importer is most likely to be wrong about: the
    Lua is NOT executed. A kid who drops a cart and sees the sprite sheet needs
    to be told that in a sentence, not to discover it as a black screen."""
    out = ['"%s" imported.' % summary.get("title", "cart")]
    out.append("Art and sound came across. The PICO-8 CODE did NOT: it is kept "
               "in main.py as a comment for you to port to Python.")
    n_sfx = summary.get("sfx") or 0
    n_music = summary.get("music") or 0
    if n_sfx or n_music:
        out.append("sound: %d sfx, %d music track%s"
                   % (n_sfx, n_music, "" if n_music == 1 else "s"))
    for item in summary.get("unsupported", ()):
        out.append("not supported: " + item)
    for item in summary.get("deferred", ()):
        out.append("not imported: " + item)
    for item in summary.get("empty", ()):
        out.append("empty: " + item)
    verbs = summary.get("verbs") or ()
    if verbs:
        out.append("this cart uses: " + ", ".join(verbs))
    return out


# --------------------------------------------------------------------------
# input guards (#194: opt=3 freezing strips the converter's own asserts)
# --------------------------------------------------------------------------
# `p8_import._png_scanlines` validates the PNG with `assert`, and a frozen build
# at opt=3 compiles asserts OUT -- so on the browser (and on a board) malformed
# input would fail somewhere deep in a struct unpack instead of saying what is
# wrong. These are the same checks, stated as returned prose, and they run
# BEFORE the converter is handed the bytes on every tier including CPython (a
# guard that only runs where it was needed is a guard nobody maintains).

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# A PICO-8 BBS cart label is exactly this, and its 32,800 pixels ARE the ROM.
P8PNG_W = 160
P8PNG_H = 205


def _u32(b, off):
    return (b[off] << 24) | (b[off + 1] << 16) | (b[off + 2] << 8) | b[off + 3]


def looks_like_png(blob):
    return len(blob) >= 8 and bytes(blob[:8]) == PNG_MAGIC


def png_problem(blob):
    """None when `blob` is a PICO-8 cart PNG this converter can read, else a
    sentence saying what it is instead."""
    if not looks_like_png(blob):
        return "that file is not a PNG"
    if len(blob) < 33:
        return "that PNG is truncated (no header chunk)"
    if bytes(blob[12:16]) != b"IHDR":
        return "that PNG does not start with an IHDR chunk"
    w = _u32(blob, 16)
    h = _u32(blob, 20)
    depth, ctype, interlace = blob[24], blob[25], blob[28]
    if depth != 8 or ctype != 6 or interlace != 0:
        return ("that PNG is not 8-bit RGBA non-interlaced (depth %d, colour "
                "type %d%s) -- a PICO-8 BBS cart always is, so this is a "
                "picture rather than a cart" % (depth, ctype,
                                                ", interlaced" if interlace else ""))
    if (w, h) != (P8PNG_W, P8PNG_H):
        return ("that PNG is %dx%d -- a PICO-8 cart image is %dx%d, so this is "
                "a picture rather than a cart" % (w, h, P8PNG_W, P8PNG_H))
    return None


def sections_problem(sections):
    """None when parsed `sections` look like a cart, else what is missing.

    A text `.p8` has no magic worth checking (it is UTF-8 prose), so the honest
    test is whether the parse found any PICO-8 section at all -- a dropped
    README parses into `{"_header": [...]}` and nothing else."""
    for name in ("lua", "gfx", "sfx", "music", "map", "gff"):
        if sections.get(name):
            return None
    return ("that file has no PICO-8 sections in it (no __lua__, __gfx__, "
            "__sfx__ ...) -- is it really a .p8 cart?")
