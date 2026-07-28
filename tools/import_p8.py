#!/usr/bin/env python3
"""Offline PICO-8 `.p8` -> Moybyte `.moy` ASSET importer (no Lua VM).

    import_p8.py <cart.p8> <out_dir.moy>

Why this exists / what it does (and does NOT do)
------------------------------------------------
Moybyte's v0.4 console is "PICO-8-inspired by construction" -- the MOY64 palette's
first 16 colors *are* PICO-8's base 16 byte-for-byte (`runtime/palette.py`
`_BASE16`), and `sprites.moygfx` is literally PICO-8 `__gfx__` format (a 16x16 grid
of 8x8 tiles, one hex nibble per pixel). So importing PICO-8 *assets* into a
`.moy` is cheap and needs no Lua runtime. (Feasibility analysis: issue #13.)

This tool parses the *text* `.p8` and writes a `.moy` FOLDER:

  __gfx__   -> sprites.moygfx   NEAR-VERBATIM nibble copy. The palette already
                              matches, so the only work is padding/cropping to the
                              16x16 (128x128px) SpriteSheet grid. Round-trips
                              stably through SpriteSheet.from_hex/to_hex.
  __sfx__   -> sounds.json    BEST-EFFORT, LOSSY. PICO-8 SFX = 32 notes x
   __music__                  [pitch, instrument(0-7 builtin + custom), volume,
                              effect] over 8 instruments + 4 channels + an effect
                              column. Moybyte SFX = [pitch, wave(0-3), vol(0-7)]
                              steps at one `speed`, 1-channel music. We map the 8
                              PICO-8 instruments down to the 4 Moybyte waveforms,
                              DROP the effect column entirely, and FLATTEN the
                              4-channel music pattern to Moybyte's 1-channel
                              `pattern` (taking the first non-empty channel per
                              row). See _IMPORT_INSTRUMENT_TO_WAVE below.
  __lua__   -> main.py        NOT transpiled / NOT executed. The Lua is imported as
                              a commented-out reference block, with a tiny working
                              v0.4 Python stub on top. Running real PICO-8 code is
                              gated on the Lua-runtime decision (issue #6).
  header/   -> manifest.json  title (from a `__label__`/first comment line, else
   filename                   the filename), type "game", standard canvas +
                              permissions, empty config/edit.

DEFERRED (intentionally, noted rather than guessed):
  __map__        the `.moymap`/tilemap format is not on master yet (follow-up: #32).
  __gff__        per-sprite flag bits -- Moybyte has no sprite-flag model yet.
  .p8.png        the steganographic PNG cart: the text `.p8` is enough for v1; a
                 PNG bit-unpack is an additive follow-up (~100 lines).

This file only uses the stdlib + `runtime.editors.SpriteSheet`
(`runtime.audio` is imported opportunistically, only to *validate* the emitted
bank when available -- the writer never depends on it).
"""

import json
import os
import sys

# Make `runtime` importable when run as a script from anywhere in the repo.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from runtime.editors import SpriteSheet  # noqa: E402


# --------------------------------------------------------------------------
# .p8 section parsing
# --------------------------------------------------------------------------
# A .p8 is a UTF-8 text file. After a version header line ("pico-8 cartridge..."
# + "version N") it is a sequence of sections, each introduced by a line of the
# form `__name__` (e.g. `__lua__`, `__gfx__`, `__sfx__`, `__music__`). Lines
# belong to the most recent section header seen.

_SECTION_NAMES = ("lua", "gfx", "gff", "label", "map", "sfx", "music")


def parse_p8(text):
    """Split a `.p8` file's text into {section_name: [lines]}.

    Returns a dict whose keys are the bare names ("lua", "gfx", ...). Lines before
    the first section header (the version header) are kept under "_header". Section
    bodies preserve their lines verbatim (no trailing newline)."""
    sections = {"_header": []}
    current = "_header"
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if (len(stripped) >= 5 and stripped.startswith("__")
                and stripped.endswith("__")):
            name = stripped[2:-2]
            current = name
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _title_from(sections, p8_path):
    """Best-effort cart title: the first non-empty `__lua__` comment line that
    looks like a name, else the filename stem."""
    for line in sections.get("lua", []):
        s = line.strip()
        # PICO-8 carts conventionally start with `-- title` / `-- by author`.
        if s.startswith("--"):
            cand = s[2:].strip()
            if cand and not cand.lower().startswith(("by ", "by:")):
                return cand[:40]
    stem = os.path.basename(p8_path)
    if stem.lower().endswith(".p8"):
        stem = stem[:-3]
    if stem.lower().endswith(".p8.png"):
        stem = stem[:-7]
    return (stem or "imported").replace("_", " ").replace("-", " ").strip() or "imported"


# --------------------------------------------------------------------------
# __gfx__  ->  sprites.moygfx   (near-verbatim; palette already matches)
# --------------------------------------------------------------------------
# PICO-8 __gfx__ is up to 128 rows of up to 128 hex nibbles (one per pixel,
# value 0-15). That is *exactly* the SpriteSheet 16x16-tile (128x128px) hex
# layout (`runtime/editors.py` SpriteSheet.to_hex). We pad short/empty rows so
# the result is always the full 128x128 grid, which round-trips stably through
# from_hex/to_hex.

GFX_W = 128
GFX_H = 128


def gfx_to_kgfx(gfx_lines):
    """Turn PICO-8 `__gfx__` lines into a 128x128 `sprites.moygfx` hex string, or
    None if there is no graphics data at all. Non-hex chars are coerced to '0'."""
    rows = []
    any_pixel = False
    for line in gfx_lines:
        s = line.strip().lower()
        if not s:
            continue
        clean = []
        for ch in s[:GFX_W]:
            if ch in "0123456789abcdef":
                clean.append(ch)
                if ch != "0":
                    any_pixel = True
            else:
                clean.append("0")
        # pad short rows out to the full width
        if len(clean) < GFX_W:
            clean.extend("0" * (GFX_W - len(clean)))
        rows.append("".join(clean))
        if len(rows) >= GFX_H:
            break
    if not rows or not any_pixel:
        return None
    # pad to the full 128 rows so the sheet is a complete 16x16 tile grid
    while len(rows) < GFX_H:
        rows.append("0" * GFX_W)
    hex_text = "\n".join(rows)
    # Normalize through the real SpriteSheet so we emit *exactly* what the editor
    # would (and prove the round-trip is stable for the caller's tests).
    sheet = SpriteSheet.from_hex(hex_text, cols=16, rows=16)
    return sheet.to_hex()


# --------------------------------------------------------------------------
# __sfx__ / __music__  ->  sounds.json   (best-effort, LOSSY)
# --------------------------------------------------------------------------
# PICO-8 __sfx__: one line per SFX (up to 64). Each line is hex:
#     [editor mode:2][note duration:2][loop start:2][loop end:2] then 32 notes.
# Each note is 5 hex nibbles:  P P W V E
#     pitch    = first 2 nibbles, 0..63 (C0..D#5; PICO-8 pitch 0 == note C0)
#     waveform = next 1 nibble, 0..15 (0..7 builtin instruments, 8..F custom)
#     volume   = next 1 nibble, 0..7
#     effect   = last 1 nibble, 0..7  (we DROP this -- Moybyte has no effects)
#
# Moybyte SFX (runtime/audio.py): steps of [pitch, wave, vol] at one `speed`
# (steps/sec), where pitch is a semitone index 0..95 (A4=57), wave is 0..3
# (square/triangle/saw/noise), vol 0..7. Mapping decisions, all lossy:
#   * PITCH: PICO-8 pitch 0 == C0. Moybyte pitch is a raw semitone index where
#     C0 == 0 too (name_to_pitch('C0') -> 0), so PICO-8 pitch maps 1:1 as a
#     semitone index. A volume-0 note becomes a REST (-1), like PICO-8.
#   * WAVE: 8 PICO-8 instruments -> 4 Moybyte waveforms (table below). The 8
#     CUSTOM instruments (waveform 8..15, defined in __sfx__ slots 0..7) are not
#     modelled; we fold them onto the builtin in the low 3 bits (w & 7).
#   * SPEED: PICO-8 "note duration" D is ticks-per-row at 120 ticks/sec, so the
#     row rate is 120/D rows/sec. Moybyte `speed` is steps/sec, so speed = round(
#     120/D), clamped to >=1. D==0 is treated as 1.
#   * EFFECTS (slide/vibrato/drop/arp/fades): DROPPED entirely.
#   * Trailing all-rest notes are trimmed so a mostly-empty SFX stays short.

PICO8_PITCH_C0 = 0  # PICO-8 pitch index 0 is the note C0
REST = -1

# 8 PICO-8 builtin instruments -> Moybyte waveform (0 square,1 tri,2 saw,3 noise)
#   0 triangle, 1 tilted saw, 2 saw, 3 square, 4 pulse, 5 organ, 6 noise, 7 phaser
_IMPORT_INSTRUMENT_TO_WAVE = {
    0: 1,  # triangle  -> triangle
    1: 2,  # tilted saw -> saw
    2: 2,  # saw       -> saw
    3: 0,  # square    -> square
    4: 0,  # pulse     -> square (closest of the 4)
    5: 1,  # organ     -> triangle (mellow)
    6: 3,  # noise     -> noise
    7: 2,  # phaser    -> saw (buzzy)
}


def _hx(s, lo, hi):
    """int(s[lo:hi], 16) or 0 on any bad/short slice."""
    try:
        return int(s[lo:hi], 16)
    except (ValueError, IndexError):
        return 0


def _sfx_line_to_dict(line):
    """One PICO-8 __sfx__ hex line -> a Moybyte SFX dict (or None if all-rest)."""
    s = line.strip().lower()
    if len(s) < 8:
        return None
    duration = _hx(s, 2, 4)              # ticks-per-row
    speed = max(1, round(120.0 / duration)) if duration else 1
    notes = s[8:]                        # 32 notes x 5 nibbles
    steps = []
    for i in range(32):
        chunk = notes[i * 5:i * 5 + 5]
        if len(chunk) < 5:
            break
        pitch = _hx(chunk, 0, 2)
        instrument = int(chunk[2], 16) if chunk[2] in "0123456789abcdef" else 0
        vol = int(chunk[3], 16) if chunk[3] in "01234567" else 0
        # effect = chunk[4]  -- intentionally dropped
        wave = _IMPORT_INSTRUMENT_TO_WAVE.get(instrument & 7, 0)
        if vol <= 0:
            steps.append([REST, wave, 0])
        else:
            steps.append([PICO8_PITCH_C0 + pitch, wave, vol])
    # trim trailing rests so a near-empty SFX doesn't carry 32 silent steps
    while steps and steps[-1][0] == REST:
        steps.pop()
    if not steps:
        return None
    return {"speed": int(speed), "loop": False, "steps": steps}


def _music_line_to_pattern_entry(line):
    """One PICO-8 __music__ line -> a single SFX id for Moybyte's 1-channel
    pattern, or None if the row is empty.

    PICO-8 music line on disk is `<flags:2> <space> <ch0:2><ch1:2><ch2:2><ch3:2>`
    -- a flag byte, a single space, then the four channel bytes packed together.
    Each channel byte is an SFX id 0..63; bit 6 (0x40) set means the channel is
    OFF for this pattern (so 0x40+ == silent). We tolerate either the real spaced
    form or a fully-packed 10-hex line by stripping internal whitespace. We
    FLATTEN 4 channels to 1 by taking the first channel that is enabled and points
    at a real SFX."""
    s = "".join(line.strip().lower().split())   # drop the inter-group space(s)
    if len(s) < 10:
        return None
    chans = s[2:10]                              # the 4 channel bytes after flags
    for ci in range(4):
        b = _hx(chans, ci * 2, ci * 2 + 2)
        if b & 0x40:                     # channel disabled in this pattern
            continue
        sfx_id = b & 0x3F
        return sfx_id
    return None


def _music_tracks(music_lines):
    """PICO-8 __music__ -> Moybyte tracks + the pattern-start map.

    A p8 SONG is a run of patterns: `music(n)` starts at pattern n and plays
    until a stop flag (0x4) or a loop-end (0x2, which loops the run). The old
    importer flattened EVERYTHING into one track, so a cart's music(40) pointed
    at nothing. Split on the flag bits instead: one Moybyte track per run, and
    return {p8_pattern_start: track_index} so the Lua shim can remap music(n)
    (nearest-lower fallback for a mid-song start index)."""
    rows = []
    for line in music_lines:
        s = "".join(line.strip().lower().split())
        flags = _hx(s, 0, 2) if len(s) >= 10 else 0
        rows.append((flags, _music_line_to_pattern_entry(line)))
    tracks = []
    starts = {}
    i = 0
    while i < len(rows):
        if rows[i][1] is None:
            i += 1
            continue
        start = i
        pattern = []
        loop = True
        while i < len(rows) and rows[i][1] is not None:
            flags, entry = rows[i]
            pattern.append(entry)
            i += 1
            if flags & 0x2:            # loop-end: the run loops from its start
                break
            if flags & 0x4:            # stop: the song ends here, no loop
                loop = False
                break
        starts[start] = len(tracks)
        tracks.append({"speed": 4, "loop": loop, "pattern": pattern})
    return tracks, starts


def music_start_map(music_lines):
    """Just the {p8_pattern_start: moy_track_index} map (the porter bakes it
    into the compat shim's music() wrapper)."""
    return _music_tracks(music_lines)[1]


def sfx_music_to_sounds(sfx_lines, music_lines, max_sfx=64):
    """Build the AudioBank-shaped sounds dict (lossy) from __sfx__/__music__.

    Returns (sounds_dict_or_None, n_sfx, n_music). The SFX list keeps positional
    ids (empty/all-rest SFX become an empty placeholder so music ids still line
    up). Music patterns map to Moybyte 1-channel `pattern` lists."""
    sfx = []
    n_real = 0
    for i, line in enumerate(sfx_lines):
        if i >= max_sfx:
            break
        d = _sfx_line_to_dict(line)
        if d is None:
            # keep the id slot so __music__ references stay aligned
            sfx.append({"speed": 8, "loop": False, "steps": []})
        else:
            sfx.append(d)
            n_real += 1
    # trim trailing empty SFX slots
    while sfx and not sfx[-1]["steps"]:
        sfx.pop()

    music, _starts = _music_tracks(music_lines)

    if not sfx and not music:
        return None, 0, 0
    return {"sfx": sfx, "music": music}, n_real, len(music)


# --------------------------------------------------------------------------
# __lua__  ->  main.py   (reference comment + GUIDED porting notes; NOT executed)
# --------------------------------------------------------------------------
# Moybyte is Python, not Lua (issue #6), so a PICO-8 cart can't "just run" -- the
# kid PORTS it, and that's the lesson (issue #36). We DON'T transpile. Instead we
# keep the original Lua as a reference comment and scaffold the port with inline
# `# PORT NOTE:` lines for the real PICO-8 -> Moybyte gotchas -- but only for the
# verbs THIS cart actually uses (scanned from its Lua), so the guidance is
# relevant, not boilerplate. See docs/porting_pico8.md for the full cheatsheet.

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
    """A runnable v0.4 cart stub + the original PICO-8 Lua as a reference comment,
    GUIDED with `# PORT NOTE:` lines + a port checklist for ONLY the PICO-8 verbs
    this cart actually uses. We do NOT transpile or run Lua (gated on #6)."""
    safe_title = title.replace('"', "'")
    used = _scan_lua_verbs(lua_lines)
    # keep PORT_NOTES order (gotchas first, not-here-yet last) for stable output
    used_idx = sorted({_NOTE_BY_TOKEN[t] for t in used})

    head = (
        '# Imported from a PICO-8 .p8 by tools/import_p8.py.\n'
        '#\n'
        '# Only the ASSETS were imported (sprites.moygfx, and sounds.json if present;\n'
        '# audio is a lossy fold and __map__/.p8.png are not imported yet).\n'
        '# Moybyte is PYTHON, not Lua -- so a PICO-8 cart does not "just run": you\n'
        '# PORT it, and that is the fun part. The original PICO-8 Lua is kept below\n'
        '# as a REFERENCE COMMENT (NOT executed -- running Lua is gated on #6).\n'
        '#\n'
        '# Cheatsheet (verb-by-verb PICO-8 -> Moybyte map): ' + CHEATSHEET + '\n'
        '#\n'
        '# This stub just draws the imported sprites so you can see the art, then\n'
        '# you rewrite _update/_draw in v0.4 Python using the notes below.\n'
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
        "canvas": {"width": 320, "height": 240, "palette": "moy64"},
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
    with open(p8_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    sections = parse_p8(text)
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

    # sounds.json (lossy from __sfx__/__music__)
    sounds, n_sfx, n_music = sfx_music_to_sounds(
        sections.get("sfx", []), sections.get("music", []))
    if sounds is not None:
        with open(os.path.join(out_dir, "sounds.json"), "w", encoding="utf-8") as f:
            json.dump(sounds, f, indent=2)
        summary["lossy"].append(
            "sounds.json (from __sfx__/__music__: %d sfx, %d music; "
            "instruments folded to 4 waves, effects dropped, "
            "music flattened to 1 channel)" % (n_sfx, n_music))
    else:
        summary["empty"].append("sounds.json (no __sfx__/__music__)")

    # deferred sections (note, don't import)
    if any(l.strip() for l in sections.get("map", [])):
        summary["deferred"].append("__map__ (tilemap format not on master yet; #32)")
    if any(l.strip() for l in sections.get("gff", [])):
        summary["deferred"].append("__gff__ (no sprite-flag model in v0.4)")
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
