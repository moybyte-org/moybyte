"""What stays OURS around the vendored PICO-8 import -- ONE body, every tier.

TWO VENDORED FILES DO THE WORK, and both are moy-spec's, byte-for-byte and
hash-pinned (`make vendor-p8-import`, `tests/test_p8_import_vendor.py`):

  p8_import.py     the ASSET converter -- sheet, SFX bank, music tracks.
  p8_lua_port.py   the CODE, and the `.moy` FOLDER: the cart's own Lua
                   mechanically converted p8-Lua -> Lua 5.4 under a generated
                   PICO-8 compat shim, plus map.moymap, sprites.moygfx,
                   sounds.json and a `"format": "moy-1"` manifest.

So an imported cart RUNS (owner call, 2026-08-29). It used to arrive as a
commented Lua reference block with a Python stub on top and a `# PORT NOTE:`
scaffold -- the hand-port-to-Python exercise (#36), which was the only p8 route
this repo had. That scaffold is DELETED, because transcribing Lua into Python
when the two are almost the same text teaches syntax rather than game-making,
and #194's own spec says a drop "converts, RUNS, and opens in the editor". The
languages still differ, and `docs/two_languages.md` is where that lives now --
for the kid staring at an imported cart's Lua, which is the real reader.

WHAT IS LEFT HERE is the third thing, the part moy-spec has no opinion about:

  * the INPUT GUARDS. `p8_import` validates a PNG with `assert`, and the wasm
    build freezes at opt=3, which compiles asserts OUT -- so a dropped holiday
    photo would fail somewhere deep in a struct unpack with nothing naming the
    file. `png_problem`/`sections_problem` are the same checks as returned
    prose, and they run on EVERY tier including CPython, because a guard that
    only runs where it was needed is a guard nobody maintains.
  * `_ensure_os_path`, the one `os` primitive MicroPython cannot give the
    converter (see its own docstring).
  * the COMPATIBILITY REPORT (#194: report, don't crash), aimed at what the
    generated SHIM does not implement.

WHY IT IS NOT INSIDE `tools/import_p8.py`. The BROWSER imports carts too (#194):
`firmware/web_runner/build.sh` stages this file and both vendored ones into the
wasm console's frozen set, so a `.p8` dropped on the page and a `.p8` handed to
the CLI go through the SAME code. A second copy in the runner is the shape that
already cost this repo ten days of carts imported two octaves flat
(`tools/import_p8.py`'s header), one level down.

MICROPYTHON-CLEAN, and that is a CONSTRAINT rather than an accident -- it is
what lets the browser (and later a board, #194's 2026-08-28 decision) run it:
no `os.path`, no `os.makedirs`, no f-strings, no `pathlib`. The same constraint
now binds `p8_lua_port.py` upstream, where it is written down in that file's
header; `tests/test_p8_micropython.py` is the lane that proves both.

THE ZOOM HINT IS NOT OPTIONAL HERE. A p8 cart is 128x128, and the `view(128,
120)` hint is what makes a host with room composite the centred rows at its best
integer scale instead of letterboxing the square at 1x. moy-spec's porter puts
it behind `--zoom`; a regeneration on 2026-08-11 forgot the flag and shipped a
tiny Celeste to the glass, and on a drag-and-drop import that failure would fire
on EVERY cart -- so `P8_CROP` below is passed unconditionally, by every tier.
"""

# The two vendored files. Frozen beside us in the wasm console (and reachable
# with `tools/` on sys.path, which is how the CLI and the tests arrive);
# `tools.p8_import` is the same file seen as a package from the repo root.
try:
    import p8_import
    import p8_lua_port
except ImportError:  # pragma: no cover -- exercised by whichever entry runs
    from tools import p8_import
    from tools import p8_lua_port


# The cart canvas an imported p8 declares (SPEC.md 1/3.1) and the viewport hint
# its main.lua opens with. 120 is the 8-row concession that lets a 4:3 host fill
# its height exactly (2x = 256x240 on the handheld, 5x = 640x600 on the P4);
# nothing is cropped from the RASTER, only from the presentation. `P8_CROP` is
# the porter's `--zoom` spelled as data, so no tier can forget it.
P8_CANVAS = "128x128"
P8_VIEW_W = 128
P8_VIEW_H = 120
P8_CROP = (4, 4)


# --------------------------------------------------------------------------
# what the generated shim does NOT do   (#194: report, don't crash)
# --------------------------------------------------------------------------
# The port covers the p8 verbs a cart actually leans on -- `p8_lua_port.P8_API`
# is the shim's own list, and a moy Lua cart gets the SPEC.md verb table under
# that. What is left over is this table: the PICO-8 names that reach nothing, and
# the three that reach something with different manners. A cart that calls one
# runs until it gets there, so the report has to name them BEFORE the cart does.
#
# Each entry is (kind, sentence). `kind` is "missing" (nothing answers to that
# name -- the cart stops with a Lua "attempt to call a nil value") or "differs"
# (a verb of that name exists and does not mean the same thing, which is worse,
# because it draws the wrong thing instead of saying anything).
#
# tests/test_import_p8.py checks every key against `p8_lua_port.P8_API` and the
# console's own verb table, so a shim that grows one of these cannot leave a
# stale "not supported" line behind.
MISSING = "missing"
DIFFERS = "differs"
STUBBED = "stubbed"

# Two kinds, because they mean different things to a kid staring at a cart.
#
# MISSING: nothing answers to that name and the cart STOPS -- a Lua "attempt to
# call a nil value" on the frame that line first runs.
#
# STUBBED: the verb answers, and does not mean what PICO-8 means. The cart runs
# and comes out wrong in a specific way, which is worth saying plainly. Most of
# this table used to be MISSING; the shim's compatibility layer moved it, and
# what that bought was measured -- across twelve well-known carts, fillp alone
# was called by six of them and stopped every one.
SHIM_GAPS = {
    # No cocreate/coresume/costatus/yield here: SPEC.md 4.1 admits `coroutine`,
    # libmoy opens it, and the shim aliases the four.
    "reboot": (MISSING, "reboot()/load() restart the machine or swap the cart "
                        "-- the launcher does that here; from inside a cart, "
                        "reset your own state instead"),
    "load": (MISSING, "reboot()/load() restart the machine or swap the cart "
                      "-- the launcher does that here; from inside a cart, "
                      "reset your own state instead"),
    "stop": (MISSING, "stop() drops to PICO-8's command line -- there is no "
                      "command line here; return from _update() instead"),
    "trace": (MISSING, "trace() returns a Lua stack traceback for printh() -- "
                       "no equivalent; the cart error screen shows the line"),
    "info": (MISSING, "info() prints cart stats to PICO-8's console -- no "
                      "console here; print() draws on the screen instead"),
    "serial": (MISSING, "serial() streams bytes to a p8 hardware port -- "
                        "no equivalent; there is nothing on the other end"),
}

SHIM_STUBS = {
    "peek": (STUBBED, "peek()/poke() read and write 64K of SCRATCH memory -- "
                      "it is not the console's memory, so a cart keeping its "
                      "own bookkeeping there works and one poking a hardware "
                      "register changes nothing"),
    "poke": (STUBBED, "peek()/poke() read and write 64K of SCRATCH memory -- "
                      "it is not the console's memory, so a cart keeping its "
                      "own bookkeeping there works and one poking a hardware "
                      "register changes nothing"),
    "peek2": (STUBBED, "peek2()/peek4()/poke2()/poke4() are the 2- and 4-byte "
                       "forms of the same scratch memory"),
    "peek4": (STUBBED, "peek2()/peek4()/poke2()/poke4() are the 2- and 4-byte "
                       "forms of the same scratch memory"),
    "poke2": (STUBBED, "peek2()/peek4()/poke2()/poke4() are the 2- and 4-byte "
                       "forms of the same scratch memory"),
    "poke4": (STUBBED, "peek2()/peek4()/poke2()/poke4() are the 2- and 4-byte "
                       "forms of the same scratch memory"),
    "memcpy": (STUBBED, "memcpy()/memset() move bytes inside the scratch "
                        "memory, so they cannot blit to the screen or the "
                        "sheet the way a p8 cart may expect"),
    "memset": (STUBBED, "memcpy()/memset() move bytes inside the scratch "
                        "memory, so they cannot blit to the screen or the "
                        "sheet the way a p8 cart may expect"),
    "fillp": (STUBBED, "fillp() sets a dither pattern for the fill verbs -- "
                       "the console fills solid, so the pattern is remembered "
                       "and gradients come out flat"),
    "sget": (STUBBED, "sget() reads a sheet pixel and the sheet is a FILE "
                      "here, not memory -- it reads back 0, so collision or "
                      "effects driven off sheet pixels will be wrong"),
    "sset": (STUBBED, "sset() writes a sheet pixel; the sheet is a file here, "
                      "so the write is dropped and spr() keeps drawing the "
                      "art as imported"),
    "fset": (STUBBED, "fset() writes a sprite flag; __gff__ is baked in "
                      "read-only, so fget() works and fset() is dropped"),
    "stat": (STUBBED, "stat() reads machine counters -- cpu and memory read 0 "
                      "and the mouse reads nothing, so a cart's debug HUD "
                      "shows zeroes"),
    "reload": (STUBBED, "reload()/cstore() re-read the cart ROM -- there is no "
                        "ROM here, so they do nothing; the sheet and the map "
                        "are files"),
    "cstore": (STUBBED, "reload()/cstore() re-read the cart ROM -- there is no "
                        "ROM here, so they do nothing; the sheet and the map "
                        "are files"),
    "printh": (STUBBED, "printh() prints to a terminal and there is none "
                        "behind a cart, so it is dropped; draw it with print()"),
    "extcmd": (STUBBED, "extcmd() asks the host for a screenshot or a reset -- "
                        "no host to ask, so it is dropped"),
    "flip": (STUBBED, "flip() shows the frame and waits -- the console calls "
                      "_draw() for you, so it does nothing; a cart that LOOPS "
                      "on flip() still needs that loop moved into _update()"),
    "holdframe": (STUBBED, "holdframe() pairs with flip() to hold a frame -- "
                           "nothing to hold here, so it is dropped"),
    "cartdata": (STUBBED, "cartdata()/dget()/dset() are the 64 save slots, and "
                          "these are REAL -- they persist through the "
                          "console's own save memory"),
    "dget": (STUBBED, "cartdata()/dget()/dset() are the 64 save slots, and "
                      "these are REAL -- they persist through the console's "
                      "own save memory"),
    "dset": (STUBBED, "cartdata()/dget()/dset() are the 64 save slots, and "
                      "these are REAL -- they persist through the console's "
                      "own save memory"),
}


_ALL_GAPS = {}


def _rebuild_gap_index():
    _ALL_GAPS.clear()
    _ALL_GAPS.update(SHIM_GAPS)
    _ALL_GAPS.update(SHIM_STUBS)


_rebuild_gap_index()


def scan_lua_verbs(lua_lines):
    """The `SHIM_GAPS` tokens this cart calls, as whole-word calls (`verb`,
    optional spaces, `(`).

    Word-boundary matched at both ends, so `t` does not fire inside `time` or
    `set`, and `color` does not fire inside `colorkey`."""
    text = "\n".join(lua_lines)
    n_text = len(text)
    found = {}
    for tok in _ALL_GAPS:
        i = 0
        n = len(tok)
        while True:
            j = text.find(tok, i)
            if j < 0:
                break
            i = j + n
            prev = text[j - 1] if j > 0 else " "
            if prev.isalpha() or prev.isdigit() or prev in "._:":
                continue                    # inside a longer name / a method
            k = j + n
            if k < n_text and (text[k].isalpha() or text[k].isdigit()
                               or text[k] == "_"):
                continue                    # a longer name that starts with it
            while k < n_text and (text[k] == " " or text[k] == "\t"):
                k += 1
            if k < n_text and text[k] == "(":
                found[tok] = True
                break
    return set(found)


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

    `p8_lua_port` needs no such shim: it reaches `os` only for `mkdir`, which
    every tier has -- that one WAS an upstream fix, because its regex and
    `str.isalnum` gaps could not be shimmed from out here at all.

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
# top-level: converted sections -> a .moy folder
# --------------------------------------------------------------------------

def write_cart(sections, out_dir, title):
    """Write a `.moy` folder at `out_dir` from already-parsed `sections`
    (`p8_import.read_p8`'s output) and `title`. Returns a summary dict for
    `report_lines`.

    The BYTES are `p8_lua_port.port_sections`'s -- one writer, three tiers, and
    the only place a cart's shape is decided. What this adds is the two things
    that are ours: the unconditional zoom crop, and the compatibility summary.

    Takes SECTIONS rather than a path so the caller owns the read: the browser
    already holds the dropped bytes, and the CLI already has the file."""
    lua_lines = sections.get("lua", [])
    gaps = sorted(scan_lua_verbs(lua_lines))

    wrote = p8_lua_port.port_sections(sections, out_dir, title, P8_CROP)
    files = wrote["files"]

    summary = {
        "title": title,
        "imported": [],
        "lossy": [],
        "deferred": [],
        "empty": [],
        "unsupported": [],
        "differs": [],
        "verbs": gaps,
        "files": files,
        "sfx": wrote["sfx"],
        "music": wrote["music"],
        # The importer's VERDICT (moy-spec PICO8.md): "runs", "gaps" or
        # "refused", with the reasons the porter read off the cart's code.
        # The upstream verdict is decided before the write; here the write
        # has happened, so a refused cart is the caller's to remove.
        "verdict": wrote["verdict"],
    }

    summary["imported"].append(
        "main.lua (the cart's own code, converted to Lua 5.4 under a "
        "generated PICO-8 shim -- it RUNS)")
    summary["imported"].append(
        "manifest.json (canvas %s + the view(%d, %d) zoom hint)"
        % (P8_CANVAS, P8_VIEW_W, P8_VIEW_H))
    # P8SCII characters the port can neither DRAW nor transliterate. The shim
    # carries PICO-8's own 3x5 font plus our 7x5 picture glyphs for 128..153,
    # and maps the six button symbols to this console's A/B and arrows -- but
    # P8SCII also holds two full Japanese kana alphabets (154..253) and those
    # have no glyph here. They draw as BLANK, which is a cart's own text
    # quietly missing rather than anything that looks like an error.
    drawn = set(p8_lua_port._P8_WIDE_ART)
    left = set()
    for line in lua_lines:
        for ch in line:
            if ord(ch) > 0x7F and ch not in p8_lua_port._GLYPH_TEXT \
                    and ord(ch) not in drawn:
                left.add(ch)
    if left:
        summary["lossy"].append(
            "%d PICO-8 character%s outside the shim's font (its Japanese "
            "kana, most likely) -- they draw as blank space"
            % (len(left), "" if len(left) == 1 else "s"))

    custom = p8_import.custom_instrument_waves(sections.get("sfx", []))
    used = set()
    for line in sections.get("sfx", []):
        body = "".join(line.strip().lower().split())[8:]
        for i in range(32):
            chunk = body[i * 5:i * 5 + 5]
            if len(chunk) == 5 and chunk[3] in "1234567" \
                    and chunk[2] in "89abcdef":
                used.add(int(chunk[2], 16))
    if used & set(custom):
        # Not a verb, so the gap table cannot carry it -- and it is a
        # SUBSTITUTION, which is the kind of gap that says nothing and just
        # sounds wrong. One cart played 32 notes of its music as static.
        summary["lossy"].append(
            "%d custom instrument%s (a p8 sound slot used AS an instrument) "
            "-- the port has one wave per note, so each folds onto the "
            "builtin wave that slot mostly plays"
            % (len(used & set(custom)), "" if len(used & set(custom)) == 1 else "s"))
    if "sprites.moygfx" in files:
        summary["imported"].append(
            "sprites.moygfx (from __gfx__, palette is identical)")
    else:
        summary["empty"].append("sprites.moygfx (no __gfx__ pixels)")
    if "map.moymap" in files:
        summary["imported"].append(
            "map.moymap (all 64 rows, including the ones PICO-8 keeps in the "
            "bottom half of __gfx__)")
    else:
        summary["empty"].append("map.moymap (no __map__ cells)")
    if "sounds.json" in files:
        summary["imported"].append(
            "sounds.json (from __sfx__/__music__: %d sfx, %d music; 8 waves "
            "1:1, effects verbatim, 4-channel rows; only custom instruments "
            "unmodelled)" % (wrote["sfx"], wrote["music"]))
    else:
        summary["empty"].append("sounds.json (no __sfx__/__music__)")

    for line in sections.get("label", []):
        if line.strip():
            summary["deferred"].append("__label__ (cart label image not imported)")
            break

    # The kinds are reported apart because they mean different things: MISSING
    # stops the cart, STUBBED lets it run and come out wrong in a stated way.
    seen = {}
    for v in gaps:
        kind, text = _ALL_GAPS[v]
        if text in seen:
            continue
        seen[text] = True
        if kind == MISSING:
            summary["unsupported"].append(text)
        elif kind == STUBBED:
            summary["lossy"].append(text)
        else:
            summary["differs"].append(text)

    return summary


# --------------------------------------------------------------------------
# the compatibility REPORT (#194: report, don't crash)
# --------------------------------------------------------------------------

def report_lines(summary):
    """A short, human compatibility summary of one import.

    Kept plain (owner call 2026-08-29): the cart is already running on screen
    behind this panel, so the report does not need to announce that it worked.
    What it is FOR is the edges below -- where the cart will stop agreeing with
    PICO-8 -- and one line saying what language the code turned out to be,
    because a kid who opens it finds Lua and deserves to know why."""
    out = ['"%s" imported.' % summary.get("title", "cart")]
    out.append("Its code is the cart's own Lua, under a PICO-8 shim.")
    verdict = summary.get("verdict")
    if verdict:
        out.extend(p8_lua_port.verdict_lines(verdict))
    n_sfx = summary.get("sfx") or 0
    n_music = summary.get("music") or 0
    if n_sfx or n_music:
        out.append("sound: %d sfx, %d music track%s"
                   % (n_sfx, n_music, "" if n_music == 1 else "s"))
    for item in summary.get("lossy", ()):
        out.append("approximated: " + item)
    for item in summary.get("differs", ()):
        out.append("works differently: " + item)
    for item in summary.get("unsupported", ()):
        out.append("not supported: " + item)
    for item in summary.get("deferred", ()):
        out.append("not imported: " + item)
    for item in summary.get("empty", ()):
        out.append("empty: " + item)
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
