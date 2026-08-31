#!/usr/bin/env python3
"""PICO-8 -> moy ASSET converters (no Lua VM, stdlib only).

This is a LIBRARY, not a command. `p8_lua_port.py` drives it -- that is the tool
that writes a whole `.moy` cart (and `moy.py port` is the front end for it). What
lives here is the asset half: parse a PICO-8 cart and hand back moy-format data.

Why the assets convert nearly verbatim: moy's palette indices 0-15 *are* PICO-8's
base 16, byte-for-byte (SPEC.md 2), and `sprites.moygfx` is PICO-8 `__gfx__`
format -- one hex nibble per pixel, 8x8 tiles, sixteen per row (SPEC.md 3.2). So
the sheet is a near-verbatim nibble copy; only padding to the sheet grid differs.

  read_p8(path)               a `.p8` (text) or `.p8.png` (BBS steganographic
                              cart) -> its `__section__` lines. The PNG path
                              unpacks the ROM and re-emits it as text sections,
                              so both cart flavours converge here.
  gfx_to_kgfx(...)            __gfx__   -> sprites.moygfx
  sfx_music_to_sounds(...)    __sfx__ + __music__ -> sounds.json, full fidelity:
                              8 waveforms (renumbered, see
                              _IMPORT_INSTRUMENT_TO_WAVE), the effect nibble
                              verbatim in PICO-8's own numbering, 4-channel
                              music rows, SFX loop ranges and per-row pattern
                              lengths. Only PICO-8's 8 CUSTOM instruments stay
                              unmodelled.
  music_start_map(...)        pattern index -> track index, for the `music(n)`
                              remap the port shim needs.
  _title_from(...)            a display title from `__label__`, else the filename.

NOT handled here: `__map__` (p8_lua_port writes `map.moymap`) and `__gff__`
(per-sprite flag bits -- moy core has no sprite-flag model).
"""

import os



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
# layout (SPEC.md 3.2). We pad short/empty rows so
# the result is always the full 128x128 grid, which round-trips stably through
# from_hex/to_hex.

GFX_W = 128
GFX_H = 128
GFX_TILES = (GFX_W // 8) * (GFX_H // 8)   # 256, a p8 sheet's tile count


def icon_tile(kgfx_text):
    """The first sheet tile carrying any art -- the cart's icon (SPEC.md 3.4), or
    None for a blank sheet.

    PICO-8 has no icon field, so this is a heuristic, but a well-founded one: the
    p8 convention leaves sprite 0 EMPTY (it is why an empty map cell reads 0), and
    authors fill the sheet from the top, so the first non-blank tile is the game's
    own first piece of art -- a player, a ship, a logo. Picking a 1x1 tile rather
    than guessing a 2x2 block is deliberate: p8 sprites are 8x8, so a wider span
    would drag in whatever unrelated sprite happens to sit beside it."""
    if not kgfx_text:
        return None
    rows = kgfx_text.split("\n")
    for n in range(GFX_TILES):
        ox, oy = (n % 16) * 8, (n // 16) * 8
        for y in range(oy, min(oy + 8, len(rows))):
            if any(c != "0" for c in rows[y][ox:ox + 8]):
                return n
    return None


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
    # The built grid is already the exact 128x128 .moygfx layout -- the
    # reference editor's from_hex/to_hex round-trip is an identity on it
    # (verified against the reference implementation), so no
    # normalization pass is needed.
    return hex_text


# --------------------------------------------------------------------------
# __sfx__ / __music__  ->  sounds.json
# --------------------------------------------------------------------------
# PICO-8 __sfx__: one line per SFX (up to 64). Each line is hex:
#     [editor mode:2][note duration:2][loop start:2][loop end:2] then 32 notes.
# Each note is 5 hex nibbles:  P P W V E
#     pitch    = first 2 nibbles, 0..63 (C0..D#5; PICO-8 pitch 0 == note C0)
#     waveform = next 1 nibble, 0..15 (0..7 builtin instruments, 8..F custom)
#     volume   = next 1 nibble, 0..7
#     effect   = last 1 nibble, 0..7
#
# moy SFX (SPEC.md 8.1): steps of [pitch, wave, vol(, eff)] at one
# `speed` (steps/sec), where pitch is a semitone index 0..95 (A4=57), wave is
# 0..7, vol 0..7, eff 0..7. The model covers PICO-8's 1:1:
#   * PITCH: PICO-8's tracker LABELS its pitch 0 as C0, but its synth tunes
#     pitch 33 to 440 Hz -- key_to_freq(k) = 440 * 2^((k-33)/12), verbatim in
#     zepto8 and fake-08 -- so PICO-8's octave labels sit two octaves below
#     concert naming. moy pitch is a true semitone index (57 == A4 == 440 Hz),
#     so the import TRANSPOSES: moy = p8 + 24. The 1:1 map this used to be
#     played every ported cart two octaves deep. A volume-0 note keeps its
#     key ([pitch, wave, 0]) -- PICO-8 rests do, and a following slide
#     (eff 1) glides from that key.
#   * WAVE: all 8 builtin instruments map 1:1 (table below -- the two consoles
#     number them differently). The 8 CUSTOM instruments (waveform 8..15) are
#     an SFX slot 0..7 used AS an instrument, which the moy model has no way to
#     say. They still fold onto one builtin wave -- but onto the wave that
#     slot actually plays, not onto `w & 7`. The low-bits fold was arbitrary
#     and it was audibly wrong: a cart using custom instrument 6 got `6 & 7`
#     = p8 waveform 6 = NOISE, so 32 notes of a real cart's music played as
#     static. The report names the substitution now instead of doing it
#     silently.
#   * SPEED: PICO-8 "note duration" D is ticks-per-row at 120 ticks/sec, so
#     speed = 120/D steps/sec exactly (SPEC.md 8.1's speed is not
#     integer-only; rounding it drifts the row clock). D==0 plays flat out.
#   * EFFECTS: the nibble carries over VERBATIM -- moy uses PICO-8's
#     numbering (1 slide, 2 vibrato, 3 drop, 4/5 fades, 6/7 arpeggio).
#   * Trailing all-rest notes are trimmed so a mostly-empty SFX stays short.

PICO8_PITCH_C0 = 24  # PICO-8 pitch 0 sounds at moy semitone 24 (its A "2" is A4)

# 8 PICO-8 builtin instruments -> the same 8 moy waveforms, renumbered.
# p8: 0 triangle, 1 tilted saw, 2 saw, 3 square, 4 pulse, 5 organ, 6 noise,
#     7 phaser
# moy: 0 square, 1 triangle, 2 saw, 3 noise, 4 pulse, 5 organ, 6 tilted saw,
#      7 phaser (audio.WAVE_*)
_IMPORT_INSTRUMENT_TO_WAVE = {
    0: 1,  # triangle   -> WAVE_TRIANGLE
    1: 6,  # tilted saw -> WAVE_TILTED
    2: 2,  # saw        -> WAVE_SAW
    3: 0,  # square     -> WAVE_SQUARE
    4: 4,  # pulse      -> WAVE_PULSE
    5: 5,  # organ      -> WAVE_ORGAN
    6: 3,  # noise      -> WAVE_NOISE
    7: 7,  # phaser     -> WAVE_PHASER
}


def _hx(s, lo, hi):
    """int(s[lo:hi], 16) or 0 on any bad/short slice."""
    try:
        return int(s[lo:hi], 16)
    except (ValueError, IndexError):
        return 0


def custom_instrument_waves(sfx_lines):
    """The builtin wave each p8 CUSTOM instrument (8..15) folds onto.

    A custom instrument is __sfx__ slot 0..7 played as an instrument. The moy
    model has one wave per note and no way to name a slot, so the fold has to
    pick a builtin -- and the honest pick is the wave that slot mostly plays,
    read off the slot itself. Falls back to the low-bits fold for a slot that
    is empty or all rests.
    """
    waves = {}
    for k in range(8):
        if k >= len(sfx_lines):
            break
        s = "".join(sfx_lines[k].strip().lower().split())
        seen = {}
        notes = s[8:]
        for i in range(32):
            chunk = notes[i * 5:i * 5 + 5]
            if len(chunk) < 5 or chunk[3] not in "1234567":
                continue                 # a rest says nothing about the timbre
            instr = int(chunk[2], 16) if chunk[2] in "0123456789abcdef" else 0
            if instr >= 8:
                continue                 # a custom instrument defined by one
            seen[instr] = seen.get(instr, 0) + 1
        if seen:
            top = max(seen.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            waves[8 + k] = _IMPORT_INSTRUMENT_TO_WAVE.get(top, 0)
    return waves


def _sfx_line_to_dict(line, custom=None):
    """One PICO-8 __sfx__ hex line -> a moy SFX dict (or None if all-rest).

    The header's LOOP RANGE (bytes 4..8: loop start / loop end) carries over
    (ignoring it was the "half the music" bug: p8 songs pair a
    long melody with short accompaniment riffs that loop until the row ends;
    played once they die mid-row). A loop range keeps its exact step count --
    the silent steps inside it are part of the riff's rhythm, so no rest-trim."""
    s = line.strip().lower()
    if len(s) < 8:
        return None
    # ticks-per-note at 120 ticks/sec -> steps/sec, kept exact: a D=32 sfx is
    # 3.75 steps/s, and rounding that to 4 drifts it against the row clock.
    # SPEC.md 8.1's speed is not integer-only.
    duration = _hx(s, 2, 4)
    speed = round(120.0 / duration, 4) if duration else 120.0
    loop_s = _hx(s, 4, 6)
    loop_e = _hx(s, 6, 8)
    notes = s[8:]                        # 32 notes x 5 nibbles
    steps = []
    for i in range(32):
        chunk = notes[i * 5:i * 5 + 5]
        if len(chunk) < 5:
            break
        pitch = _hx(chunk, 0, 2)
        instrument = int(chunk[2], 16) if chunk[2] in "0123456789abcdef" else 0
        vol = int(chunk[3], 16) if chunk[3] in "01234567" else 0
        eff = int(chunk[4], 16) if chunk[4] in "01234567" else 0
        if instrument >= 8 and custom and instrument in custom:
            wave = custom[instrument]
        else:
            wave = _IMPORT_INSTRUMENT_TO_WAVE.get(instrument & 7, 0)
        if vol <= 0:
            # keep the key: a PICO-8 rest is silent but still the origin a
            # following slide (eff 1) glides from
            steps.append([PICO8_PITCH_C0 + pitch, wave, 0])
        elif eff:
            # the effect nibble carries over verbatim (p8 numbering)
            steps.append([PICO8_PITCH_C0 + pitch, wave, vol, eff])
        else:
            steps.append([PICO8_PITCH_C0 + pitch, wave, vol])
    if 0 < loop_e <= len(steps) and loop_s < loop_e:
        # p8 loop range: play 0..loop_e once, then repeat loop_s..loop_e
        steps = steps[:loop_e]
        if not any(st[2] for st in steps):
            return None
        d = {"speed": speed, "loop": True, "steps": steps}
        if loop_s:
            d["loop_start"] = int(loop_s)
        return d
    if loop_e == 0 and 0 < loop_s < len(steps):
        # p8's length trick: loop start with end 0 = "play this many notes"
        steps = steps[:loop_s]
    # trim trailing rests so a near-empty SFX doesn't carry 32 silent steps
    while steps and steps[-1][2] == 0:
        steps.pop()
    if not steps:
        return None
    return {"speed": speed, "loop": False, "steps": steps}


def _music_line_channels(line):
    """All ENABLED channel SFX ids of one __music__ line (in channel order).

    PICO-8 music line on disk is `<flags:2> <space> <ch0:2><ch1:2><ch2:2><ch3:2>`
    -- a flag byte, a single space, then the four channel bytes packed together.
    Each channel byte is an SFX id 0..63; bit 6 (0x40) set means the channel is
    OFF for this pattern (so 0x40+ == silent). We tolerate either the real spaced
    form or a fully-packed 10-hex line by stripping internal whitespace."""
    s = "".join(line.strip().lower().split())   # drop the inter-group space(s)
    if len(s) < 10:
        return []
    chans = s[2:10]                              # the 4 channel bytes after flags
    out = []
    for ci in range(4):
        b = _hx(chans, ci * 2, ci * 2 + 2)
        if b & 0x40:                     # channel disabled in this pattern
            continue
        out.append(b & 0x3F)
    return out


def _music_line_row(line):
    """One PICO-8 __music__ line -> a moy multi-channel pattern row:
    a fixed-POSITION list [ch0, ch1, ch2, ch3] with -1 for a disabled channel
    (positions matter -- the engine keeps channel j on the same voice across
    rows, which is what lets slides carry over), trailing -1s trimmed. A row
    with only channel 0 collapses to a plain int (the 1-channel form); a fully
    silent row is None."""
    s = "".join(line.strip().lower().split())
    if len(s) < 10:
        return None
    chans = s[2:10]
    row = []
    for ci in range(4):
        b = _hx(chans, ci * 2, ci * 2 + 2)
        row.append(-1 if (b & 0x40) else (b & 0x3F))
    while row and row[-1] < 0:
        row.pop()
    if not row:
        return None
    if len(row) == 1:
        return row[0]
    return row


def _music_tracks(music_lines, sfx=None, sfx_lines=None):
    """PICO-8 __music__ -> moy tracks + the pattern-start map.

    A p8 SONG is a run of patterns: `music(n)` starts at pattern n and plays
    until a stop flag (0x4) or a loop-end (0x2, which loops the run). The old
    importer flattened EVERYTHING into one track, so a cart's music(40) pointed
    at nothing. Split on the flag bits instead: one moy track per run, and
    return {p8_pattern_start: track_index} so the Lua shim can remap music(n)
    (nearest-lower fallback for a mid-song start index).

    Each pattern row imports as a MULTI-CHANNEL row (all four p8
    channels, fixed positions) -- the old 4->1 melody-pick flatten is gone."""
    metas = [_sfx_meta(l) for l in sfx_lines] if sfx_lines else None
    rows = []
    for line in music_lines:
        s = "".join(line.strip().lower().split())
        flags = _hx(s, 0, 2) if len(s) >= 10 else 0
        rows.append((flags, _music_line_row(line)))
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
        # PER-ROW durations, by p8's actual rule (see _row_secs
        # -- following channel 0 blindly cut melodies 4x early when ch0 was a
        # fast looping arp). row_secs carries this per row; `speed` stays the
        # first finite row's tempo for display/back-compat.
        rsecs = [_row_secs(row, metas) for row in pattern]
        finite = [v for v in rsecs if v > 0]
        spd = (1.0 / finite[0]) if finite else 4.0
        track = {"speed": spd, "loop": loop, "pattern": pattern}
        if any(v != rsecs[0] for v in rsecs) or not finite:
            track["row_secs"] = rsecs
        tracks.append(track)
    return tracks, starts


def _sfx_meta(line):
    """(duration_ticks, loop_start, loop_end) straight off a raw __sfx__ line
    -- the duration math needs the EXACT tick value (32*dur/120 s), not the
    rounded steps/sec the converted SFX carries."""
    s = line.strip().lower()
    if len(s) < 8:
        return (1, 0, 0)
    return (_hx(s, 2, 4) or 1, _hx(s, 4, 6), _hx(s, 6, 8))


def _row_secs(row, metas):
    """One pattern row's duration in seconds, by p8's REAL rule (verified
    against zepto8's implementation -- the wiki's "all-looping loops forever"
    is wrong, celeste's title would stall on row 0):
      * the FIRST enabled non-looping channel's note count (32, or loop_start
        when the loop-end-0 length trick is in play) at its own tick rate;
      * if EVERY channel loops: the SLOWEST looping channel's 32 notes;
      * 0 (hold forever) only when no channel resolves at all."""
    ids = row if isinstance(row, list) else [row]
    longest_loop = 0.0
    for sid in ids:
        if sid is None or sid < 0 or not metas or sid >= len(metas):
            continue
        dur, ls, le = metas[sid]
        if le > 0 and le > ls:
            longest_loop = max(longest_loop, 32.0 * dur / 120.0)
        else:
            notes = min(32, ls) if (le == 0 and ls > 0) else 32
            return notes * dur / 120.0
    return longest_loop


def music_start_map(music_lines):
    """Just the {p8_pattern_start: moy_track_index} map (the porter bakes it
    into the compat shim's music() wrapper)."""
    return _music_tracks(music_lines)[1]


def sfx_music_to_sounds(sfx_lines, music_lines, max_sfx=64):
    """Build the AudioBank-shaped sounds dict from __sfx__/__music__.

    Returns (sounds_dict_or_None, n_sfx, n_music). The SFX list keeps positional
    ids (empty/all-rest SFX become an empty placeholder so music ids still line
    up). Music patterns map to moy 1-channel `pattern` lists."""
    sfx = []
    n_real = 0
    custom = custom_instrument_waves(sfx_lines)
    for i, line in enumerate(sfx_lines):
        if i >= max_sfx:
            break
        d = _sfx_line_to_dict(line, custom)
        if d is None:
            # keep the id slot so __music__ references stay aligned
            sfx.append({"speed": 8, "loop": False, "steps": []})
        else:
            sfx.append(d)
            n_real += 1
    # trim trailing empty SFX slots
    while sfx and not sfx[-1]["steps"]:
        sfx.pop()

    music, _starts = _music_tracks(music_lines, sfx, sfx_lines)

    if not sfx and not music:
        return None, 0, 0
    return {"sfx": sfx, "music": music}, n_real, len(music)



# --------------------------------------------------------------------------
# .p8.png -> sections   (the BBS cart format: one ROM byte per pixel, hidden in
# the 2 low bits of each A,R,G,B channel; 160x205 = 0x8000 ROM + trailer)
# --------------------------------------------------------------------------
# Everything below is stdlib-only: hand-rolled PNG chunk walk + unfilter (RGBA8
# non-interlaced -- every BBS cart is), then the fixed ROM layout re-emitted as
# the TEXT .p8 section lines the existing converters already consume:
#   0x0000 gfx (2px/byte, low nibble = left)   0x2000 map rows 0-31
#   0x3000 gff        0x3100 music (4B/pattern, flag bits ride bit7 of ch0-2)
#   0x3200 sfx (64x68B: 32 2-byte notes + editor/speed/loop)   0x4300 code
# Code is raw ASCII, the OLD ":c:" compression (pre-0.2.0 carts -- the BBS
# classics), or the "\x00pxa" scheme every PICO-8 >= 0.2.0 writes.

_OLD_LOOKUP = b"#\n 0123456789abcdefghijklmnopqrstuvwxyz!#%(){}[]<>+=/*:;.,~_"

# --- the "\x00pxa" code compression (PICO-8 >= 0.2.0) -----------------------
#
# What every modern BBS cart uses, so without it a dropped .p8.png is refused
# with "save it as text .p8 first" -- which is a fair instruction and a bad
# answer, since the whole point of dropping a cart is not having PICO-8 open.
#
# Implemented from Lexaloffle's own reference (dansanderson/lexaloffle,
# pxa_compress_snippets.c: `pxa_decompress`), which is the only description
# precise enough to be worth following -- the wiki gives the header and the
# three chunk kinds and none of the bit widths.
#
#   header   8 bytes, read through the SAME bit reader as the body:
#            "\x00pxa", then raw_len and comp_len, each 2 bytes MSB-first.
#   bits     LSB-FIRST within each byte, which is the one thing a reader
#            written from the prose gets wrong half the time.
#   CHR      a literal, as an index into a move-to-front table of all 256
#            bytes: a unary run of 1-bits chooses a widening window, then the
#            index within it. The byte moves to the front, so a cart's own
#            alphabet drifts into the cheap end of the table.
#   REF      a back-reference: a distance, then a chained length. The copy is
#            byte-at-a-time and MAY overlap itself -- that is how a run is
#            spelled, so memcpy or a slice is wrong here.
#   RAW      the 0.2.0j escape: a distance whose encoding is the reserved
#            value means "plain bytes follow, until a NUL or the end".
_PXA_TINY_LITERAL_BITS = 4
_PXA_BLOCK_LEN_CHAIN_BITS = 3
_PXA_MIN_BLOCK_LEN = 3
_PXA_BLOCK_DIST_BITS = 5


class _PxaBits:
    """LSB-first bit reader over a bytes-like, with the byte cursor exposed --
    the decompress loop stops on `comp_len` bytes consumed, not on bits."""

    def __init__(self, buf, pos=0):
        self.buf = buf
        self.pos = pos
        self.bit = 1

    def bit1(self):
        if self.pos >= len(self.buf):
            return 0
        ret = 1 if (self.buf[self.pos] & self.bit) else 0
        self.bit <<= 1
        if self.bit == 256:
            self.bit = 1
            self.pos += 1
        return ret

    def val(self, bits):
        v = 0
        for i in range(bits):
            if self.bit1():
                v |= 1 << i
        return v

    def chain(self, link_bits, max_bits):
        """Sum of `link_bits`-wide links, ending at the first non-maximal one.
        A full link means 'and more follows', so a value is spelled in as many
        links as it needs -- cheap for the small values that dominate."""
        top = (1 << link_bits) - 1
        val = 0
        read = 0
        vv = top
        while vv == top:
            vv = self.val(link_bits)
            read += link_bits
            val += vv
            if read >= max_bits:
                break
        return val

    def num(self):
        """A back-reference distance. The width is chosen first, in 5-bit steps
        (15 bits is commonest so it is the 1-bit prefix), then the value. The
        one reserved combination -- zero in a 10-bit field -- is the RAW-block
        marker, and is returned as -1."""
        bits = (3 - self.chain(1, 2)) * _PXA_BLOCK_DIST_BITS
        val = self.val(bits)
        if val == 0 and bits == 10:
            return -1
        return val


def _pxa_decompress(rom, pos=0x4300, max_len=0x10000):
    """The code section at `pos` -> its bytes. Raises ValueError on a stream
    that decodes to nonsense rather than returning half a program."""
    bits = _PxaBits(rom, pos)
    header = [bits.val(8) for _ in range(8)]
    raw_len = header[4] * 256 + header[5]
    comp_len = header[6] * 256 + header[7]
    # The table starts as identity; every emitted byte moves to the front.
    table = list(range(256))
    out = bytearray()
    end = pos + comp_len
    while bits.pos < end and len(out) < raw_len and len(out) < max_len:
        if bits.bit1() == 0:
            offset = bits.num() + 1
            if offset == 0:                       # RAW: plain bytes to a NUL
                while len(out) < raw_len:
                    b = bits.val(8)
                    if b == 0:
                        break
                    out.append(b)
                continue
            if offset > len(out):
                raise ValueError("pxa: back-reference past the start")
            length = bits.chain(_PXA_BLOCK_LEN_CHAIN_BITS, 100000) \
                + _PXA_MIN_BLOCK_LEN
            # ONE BYTE AT A TIME, and not a slice: a reference may reach into
            # what this very loop is writing, which is how a repeated run is
            # spelled. A slice would copy the pre-loop bytes and repeat garbage.
            start = len(out) - offset
            for i in range(length):
                if len(out) >= max_len:
                    break
                out.append(out[start + i])
        else:
            lpos = 0
            nbits = 0
            safety = 0
            while bits.bit1() == 1 and safety < 16:
                lpos += 1 << (_PXA_TINY_LITERAL_BITS + nbits)
                nbits += 1
                safety += 1
            nbits += _PXA_TINY_LITERAL_BITS
            lpos += bits.val(nbits)
            if lpos > 255:
                raise ValueError("pxa: literal index %d out of range" % lpos)
            c = table[lpos]
            out.append(c)
            # move-to-front
            for i in range(lpos, 0, -1):
                table[i] = table[i - 1]
            table[0] = c
    return bytes(out)


def _png_scanlines(data):
    import struct
    import zlib
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos = 8
    w = h = None
    idat = b""
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            assert depth == 8 and ctype == 6 and interlace == 0, \
                "expected 8-bit RGBA non-interlaced (all BBS carts are)"
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    p = 0
    for _y in range(h):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if f == 1:                                   # Sub
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif f == 2:                                 # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:                                 # Average
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:                                 # Paeth
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out += line
        prev = line
    return w, h, bytes(out)


def _p8png_rom(data):
    w, h, px = _png_scanlines(data)
    rom = bytearray(w * h)
    for i in range(w * h):
        r, g, b, a = px[i * 4:i * 4 + 4]
        rom[i] = ((a & 3) << 6) | ((r & 3) << 4) | ((g & 3) << 2) | (b & 3)
    return bytes(rom)


def _old_decompress(rom, start):
    ln = (rom[start + 4] << 8) | rom[start + 5]
    out = bytearray()
    i = start + 8
    while len(out) < ln and i < len(rom):
        b = rom[i]
        i += 1
        if b == 0x00:
            out.append(rom[i])
            i += 1
        elif b <= 0x3B:
            out.append(_OLD_LOOKUP[b])
        else:
            second = rom[i]
            i += 1
            off = (b - 0x3C) * 16 + (second & 0xF)
            cnt = (second >> 4) + 2
            for _ in range(cnt):
                out.append(out[-off])
    return out.decode("latin-1")


def _p8png_sections(rom):
    sections = {}
    sections["gfx"] = ["".join("%x%x" % (rom[y * 64 + xb] & 0xF, rom[y * 64 + xb] >> 4)
                               for xb in range(64)) for y in range(128)]
    sections["map"] = ["".join("%02x" % rom[0x2000 + y * 128 + x] for x in range(128))
                       for y in range(32)]
    sections["gff"] = ["".join("%02x" % rom[0x3000 + y * 128 + x] for x in range(128))
                       for y in range(2)]
    mus = []
    for p in range(64):
        ch = rom[0x3100 + p * 4:0x3100 + p * 4 + 4]
        flags = (ch[0] >> 7) | ((ch[1] >> 7) << 1) | ((ch[2] >> 7) << 2)
        mus.append("%02x %02x%02x%02x%02x"
                   % (flags, ch[0] & 0x7F, ch[1] & 0x7F, ch[2] & 0x7F, ch[3] & 0x7F))
    sections["music"] = mus
    sfx = []
    for s in range(64):
        base = 0x3200 + s * 68
        head = rom[base + 64:base + 68]           # editor, speed, loop start/end
        line = "%02x%02x%02x%02x" % (head[0], head[1], head[2], head[3])
        for n in range(32):
            v = rom[base + n * 2] | (rom[base + n * 2 + 1] << 8)
            pitch = v & 0x3F
            wave = (v >> 6) & 0x7
            vol = (v >> 9) & 0x7
            eff = (v >> 12) & 0x7
            custom = (v >> 15) & 1
            line += "%02x%x%x%x" % (pitch, wave | (custom << 3), vol, eff)
        sfx.append(line)
    sections["sfx"] = sfx
    code = rom[0x4300:]
    if code[:4] == b"\x00pxa":
        lua = _pxa_decompress(rom, 0x4300).decode("latin-1")
    elif code[:4] == b":c:\x00":
        lua = _old_decompress(rom, 0x4300)
    else:
        end = code.find(b"\x00")
        lua = code[:end if end >= 0 else len(code)].decode("latin-1")
    sections["lua"] = lua.split("\n")
    return sections


# The cart's code comes out of the ROM as P8SCII, and every byte >= 0x80 is a
# GLYPH -- the six button symbols among them. `ascii/replace` turned all of
# them into one U+FFFD, so `btn(<left>)` and `btn(<x>)` decoded to the same
# text: not "unreadable", WRONG, and identically wrong. latin-1 is the decode
# that cannot lose a byte; `p8_lua_port` maps the glyphs that mean something.


def read_p8(path):
    """Parse a cart from either the text .p8 or the BBS .p8.png form into the
    same sections dict parse_p8 yields."""
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return _p8png_sections(_p8png_rom(blob))
    return parse_p8(blob.decode("utf-8", "replace"))
