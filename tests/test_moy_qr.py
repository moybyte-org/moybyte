"""The frozen QR encoder (runtime/moy_qr.py, #197).

A QR code fails SILENTLY: a matrix with a wrong generator polynomial, a
transposed format word or a mask the format bits do not describe still looks
exactly like a QR code, still has finder patterns in three corners, and simply
does not decode. Nobody on the team can read one by eye, and the only honest
end-to-end check -- point a phone at a board -- is the one this suite exists to
avoid needing on every change.

So the pin is a KNOWN ANSWER plus the invariants a scanner reads first:

  * `test_the_canonical_paired_url_matches_the_reference_matrix` is the real
    oracle: 29x29 modules produced by `segno` 1.6.6 (an independent, widely
    used encoder), stored here as text. Fifteen further payloads across
    versions 1-4 were compared against the same reference while this module was
    written and matched byte for byte; ONE is stored, because a stored matrix
    is only worth what a human can regenerate, and the recipe is in the
    docstring below it.
  * the format words against the PUBLISHED table -- the one part of a QR code
    whose correct values are a short list anyone can look up, and the part a
    fixed-mask encoder is most likely to get wrong.
  * the structure a decoder locates the symbol with: three finder patterns, two
    timing patterns, the alignment pattern, the always-dark module, the size.
  * a ROUND TRIP through the matrix -- unmask, walk the placement, read the
    codewords back and recover the payload -- which is the only check that
    fails when the data walk is right in the corner it starts from and wrong
    where it turns.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unix_mp import require_unix_mp                            # noqa: E402

from runtime import moy_qr                                     # noqa: E402


# What a board's connection screen encodes since the 2026-08-29 move to port 80
# (moy_webserver.DEFAULT_PORT): 30 bytes, a 25-module version 2.
PAIRED_URL = "http://192.168.1.151/?pin=4821"

# The same address on an EXPLICIT port -- 35 bytes, a 29-module version 3. It is
# what `WebHost(port=...)` still emits (the host dev twin serves on 8321), and it
# is the payload the segno reference matrix below was generated for, so it stays
# the known-answer oracle: regenerating that matrix needs segno, which is not a
# test dependency, and version 3 is the only version any third party has
# verified this encoder against.
PORTED_URL = "http://192.168.1.151:8080/?pin=4821"

# `segno` 1.6.6, byte mode, error correction L, mask 0, micro=False, with ONE
# patch: segno's `write_padding_bits` appends a whole zero codeword when the
# bit stream already ends on a codeword boundary (`8 - (length % 8)` is 8
# there), where ISO/IEC 18004 7.4.10 pads only when it does NOT. Byte mode's
# header is 12 bits and its terminator 4, so a byte-mode stream is ALWAYS
# boundary-aligned and segno always spends that extra codeword -- harmless (no
# decoder reads the padding) but it moves every pad byte and therefore every
# error-correction codeword. To regenerate:
#
#     pip install segno==1.6.6
#     python - <<'EOF'
#     import segno, segno.encoder as E
#     def fixed(buff, version, length):
#         r = length % 8
#         if r:
#             buff.extend([0] * (8 - r))
#     E.write_padding_bits = fixed
#     q = segno.make("http://192.168.1.151:8080/?pin=4821", error="l", mask=0,
#                    boost_error=False, mode="byte", micro=False)
#     for row in q.matrix:
#         print("".join("#" if v else "." for v in row))
#     EOF
REFERENCE = (
    "#######...#...#....##.#######",
    "#.....#..#...#...#.#..#.....#",
    "#.###.#.#..#...#..#.#.#.###.#",
    "#.###.#..#..##......#.#.###.#",
    "#.###.#..#..##..##.##.#.###.#",
    "#.....#...###.####.#..#.....#",
    "#######.#.#.#.#.#.#.#.#######",
    "........#..#...#.#...........",
    "###.#####...#....###.##...#..",
    ".......#.#.###...##...##.#..#",
    "##.#..##..###.#.#.#..####.###",
    "...#...#.##.###..#.#.###.#.#.",
    ".##..####.##..#..##...##.#.##",
    "#.......####..#.......##.##.#",
    ".#######.#...#..##.........##",
    "..#....#.###...#.#####..##.#.",
    "##..#.#.###.#..#.##...#....##",
    "..#.#..#.#.###....#..###..#.#",
    "#.#..##.#.###.#.##...#...#.##",
    ".#..##..###.########.###.#.#.",
    "#.#.#.###.##..#..#..#####....",
    "........#..#..#..##.#...#.###",
    "#######.#....#....###.#.##.##",
    "#.....#.#.##...###.##...##..#",
    "#.###.#.##..#..#.#..######..#",
    "#.###.#...####.....###..##.##",
    "#.###.#.##.##.#.#.##.#..###.#",
    "#.....#.###.###.######...#.#.",
    "#######.#.##..####..###..#.##",
)

# The 15-bit format words for error correction level L and masks 0..7, as
# published (they are a BCH(15,5) code XORed with 0x5412). This is the table a
# scanner's own decoder carries, so it is the right independent oracle for the
# one field a fixed-mask encoder must not get wrong.
FORMAT_L = (
    "111011111000100",
    "111001011110011",
    "111110110101010",
    "111100010011101",
    "110011000101111",
    "110001100011000",
    "110110001000001",
    "110100101110110",
)


def _text(rows):
    return tuple("".join("#" if v else "." for v in row) for row in rows)


# ---------------------------------------------------------------------------
# the known answer
# ---------------------------------------------------------------------------

def test_the_canonical_paired_url_matches_the_reference_matrix():
    """Every module of a real paired url, against a third-party encoder.

    The payload is the EXPLICIT-port form (`PORTED_URL`), which is what a
    non-default port still produces and what segno was run on. The default
    address is five characters shorter and one version smaller; the two are
    pinned together by `test_dropping_the_default_port_shrinks_the_symbol`."""
    assert _text(moy_qr.encode(PORTED_URL)) == REFERENCE


def test_dropping_the_default_port_shrinks_the_symbol():
    """The measured payoff of the 2026-08-29 move to port 80.

    The connection screen fits the code into a rect it already has, so a
    version it does not need is module size it does not get: the common LAN
    address goes 35 bytes -> 30, which is version 3 -> version 2 and 29 modules
    -> 25. Pinned rather than described, because the saving is exactly one
    version wide and a url that grew five characters back (a scheme, a longer
    pin, a port spelled out again) would spend it silently."""
    assert len(PORTED_URL) == 35 and len(moy_qr.encode(PORTED_URL)) == 29
    assert len(PAIRED_URL) == 30 and len(moy_qr.encode(PAIRED_URL)) == 25


def test_the_format_words_match_the_published_table():
    """The bits that tell a scanner WHICH mask to undo. Derived from the mask
    argument rather than written down beside it, so the two cannot drift."""
    for mask in range(8):
        assert format(moy_qr.format_bits(mask), "015b") == FORMAT_L[mask], mask


def test_the_format_bits_in_the_matrix_describe_the_mask_actually_applied():
    """The failure this whole file exists for: a code that renders perfectly
    and decodes to noise. Both copies of the word are read back OUT of the
    finished matrix, so a placement typo counts as a mismatch too."""
    m = moy_qr.encode(PORTED_URL)
    size = len(m)
    want = moy_qr.format_bits(moy_qr.MASK)
    first = 0
    for i in range(6):
        first |= m[i][8] << i
    first |= m[7][8] << 6
    first |= m[8][8] << 7
    first |= m[8][7] << 8
    for i in range(9, 15):
        first |= m[8][14 - i] << i
    second = 0
    for i in range(8):
        second |= m[8][size - 1 - i] << i
    for i in range(8, 15):
        second |= m[size - 15 + i][8] << i
    assert first == want, "copy 1 %s != %s" % (format(first, "015b"),
                                               format(want, "015b"))
    assert second == want, "copy 2 %s != %s" % (format(second, "015b"),
                                                format(want, "015b"))


# ---------------------------------------------------------------------------
# structure -- what a scanner locates the symbol with
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,version,size", [
    (1, 1, 21), (17, 1, 21), (18, 2, 25), (32, 2, 25),
    (33, 3, 29), (53, 3, 29), (54, 4, 33), (78, 4, 33)])
def test_the_version_is_the_smallest_that_holds_the_payload(n, version, size):
    """The capacity ladder at EC level L, checked on both sides of every rung:
    one byte more than a version holds must step up, never truncate."""
    assert moy_qr.capacity(version) >= n
    assert len(moy_qr.encode("x" * n)) == size


def test_a_payload_past_the_last_version_is_refused_not_truncated():
    """Silently dropping the tail of a url would produce a code that scans and
    sends a phone somewhere else."""
    with pytest.raises(ValueError):
        moy_qr.encode("x" * (moy_qr.capacity(moy_qr.MAX_VERSION) + 1))


@pytest.mark.parametrize("n", [1, 17, 18, 33, 54])
def test_the_three_finder_patterns_are_in_their_corners(n):
    """7x7 rings with a 3x3 core at top-left, top-right and bottom-left -- how
    a scanner finds the symbol and its rotation at all."""
    m = moy_qr.encode("x" * n)
    size = len(m)
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(7):
            for dc in range(7):
                edge = dr in (0, 6) or dc in (0, 6)
                core = 2 <= dr <= 4 and 2 <= dc <= 4
                assert m[r0 + dr][c0 + dc] == (1 if (edge or core) else 0), (
                    "finder at %d,%d module %d,%d" % (r0, c0, dr, dc))
        # ...and the light separator that keeps the finder from touching data.
        for i in range(8):
            if r0 == 0 and c0 == 0:
                assert m[7][i] == 0 and m[i][7] == 0


@pytest.mark.parametrize("n", [1, 18, 33, 54])
def test_the_timing_patterns_alternate_across_row_and_column_six(n):
    """The alternating run a scanner measures the module pitch with."""
    m = moy_qr.encode("x" * n)
    size = len(m)
    for i in range(8, size - 8):
        want = 1 if i % 2 == 0 else 0
        assert m[6][i] == want, "row 6 col %d" % i
        assert m[i][6] == want, "col 6 row %d" % i


@pytest.mark.parametrize("n,size", [(18, 25), (33, 29), (54, 33)])
def test_versions_above_one_carry_the_alignment_pattern(n, size):
    """Version 1 has none; 2-4 have exactly one, at (size-7, size-7) -- the
    other two grid centres of those versions fall inside finder patterns."""
    m = moy_qr.encode("x" * n)
    c = size - 7
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            far = max(abs(dr), abs(dc))
            assert m[c + dr][c + dc] == (1 if far != 1 else 0), (dr, dc)


def test_version_one_has_no_alignment_pattern():
    m = moy_qr.encode("x")
    assert len(m) == 21
    c = 21 - 7                       # where versions 2+ put theirs
    assert not all(m[c + dr][c + dc] == (1 if max(abs(dr), abs(dc)) != 1 else 0)
                   for dr in range(-2, 3) for dc in range(-2, 3))


@pytest.mark.parametrize("n", [1, 18, 33, 54])
def test_the_always_dark_module_is_dark(n):
    m = moy_qr.encode("x" * n)
    assert m[len(m) - 8][8] == 1


@pytest.mark.parametrize("n", [1, 18, 33, 54])
def test_the_matrix_carries_no_quiet_zone(n):
    """The caller draws the margin (it knows the module size and what is
    behind), so the matrix is the symbol and nothing else -- a border baked in
    here would be drawn twice."""
    m = moy_qr.encode("x" * n)
    assert m[0][0] == 1 and m[0][len(m) - 1] == 1 and m[len(m) - 1][0] == 1


# ---------------------------------------------------------------------------
# the round trip -- the data walk, read back
# ---------------------------------------------------------------------------

def _decode(m):
    """Read a payload back out of a finished matrix: rebuild the function-module
    map, walk the placement, unmask, and parse the byte-mode header.

    Deliberately NOT a call into the encoder's own walk -- it re-derives the
    zigzag from the spec so a wrong turn in `_draw_data` shows up as garbled
    text rather than cancelling out."""
    size = len(m)
    res = [bytearray(size) for _ in range(size)]
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < size and 0 <= c < size:
                    res[r][c] = 1
    for i in range(size):
        res[6][i] = 1
        res[i][6] = 1
    if size > 21:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                res[size - 7 + dr][size - 7 + dc] = 1
    for i in range(9):
        res[8][i] = 1
        res[i][8] = 1
    for i in range(8):
        res[8][size - 1 - i] = 1
        res[size - 1 - i][8] = 1
    bits = []
    col = size - 1
    up = True
    while col > 0:
        if col == 6:
            col = 5
        for k in range(size):
            row = (size - 1 - k) if up else k
            for c in (col, col - 1):
                if res[row][c]:
                    continue
                b = m[row][c]
                if (row + c) % 2 == 0:       # undo mask 0
                    b ^= 1
                bits.append(b)
        up = not up
        col -= 2
    mode = int("".join(str(b) for b in bits[0:4]), 2)
    count = int("".join(str(b) for b in bits[4:12]), 2)
    out = bytearray()
    for i in range(count):
        byte = bits[12 + i * 8:20 + i * 8]
        out.append(int("".join(str(b) for b in byte), 2))
    return mode, bytes(out)


@pytest.mark.parametrize("payload", [
    "x", "hello world", PAIRED_URL, PORTED_URL, "http://10.0.0.5/?pin=0001",
    "z" * 53, "w" * 78])
def test_the_placement_walk_round_trips(payload):
    """Unmask + walk + parse gives the payload back. The check that catches a
    walk which is right where it starts and wrong where it turns -- the first
    twenty codewords of a broken walk can still match."""
    mode, data = _decode(moy_qr.encode(payload))
    assert mode == 4, "not byte mode"
    assert data == payload.encode("utf-8")


def test_the_error_correction_codewords_are_a_real_reed_solomon_remainder():
    """The half of the payload no structural check can see. Divide the whole
    codeword stream (data + check) by the generator polynomial: a correct
    remainder is zero, in every coefficient."""
    for version in range(1, moy_qr.MAX_VERSION + 1):
        _total, ecn, dcw = moy_qr._CAP[version - 1]
        cw = moy_qr._data_codewords(b"moybyte", dcw)
        cw = cw + moy_qr._ec_codewords(cw, ecn)
        assert bytes(moy_qr._ec_codewords(cw, ecn)) == bytes(ecn), version


def test_bytes_and_str_encode_the_same():
    assert moy_qr.encode("moy") == moy_qr.encode(b"moy")


# ---------------------------------------------------------------------------
# The tier this encoder actually runs on
# ---------------------------------------------------------------------------

_MP_DRIVER = '''\
import sys
sys.path.insert(0, @STAGE@)
import moy_qr
for payload in @PAYLOADS@:
    m = moy_qr.encode(payload)
    print("|".join("".join(str(c) for c in row) for row in m))
print("OK")
'''


def test_the_encoder_runs_under_micropython_and_agrees_with_cpython(tmp_path):
    """Every test above this line runs on CPython, and the encoder does not.

    It is frozen into board images and its only caller is the connection
    screen, so CPython is the tier it is NEVER exercised on in anger. That gap
    shipped: `_ec_codewords` shifted its remainder with `del rem[0]`, which
    CPython allows on a bytearray and MicroPython raises TypeError for, so the
    encoder failed for every url on every board while all 41 checks here stayed
    green. `WebConsoleUI.matrix` catches Exception, so the entire symptom was
    the word NO ADDRESS where the QR belongs -- reported off a T-Deck, not off
    a test.

    A matrix compared BETWEEN the tiers is what closes it: a construct only one
    interpreter accepts fails here, and so does a construct both accept that
    they disagree about."""
    exe = require_unix_mp(
        why="Without it the frozen QR encoder is only ever run by CPython, "
            "which is not the interpreter any board uses -- the tier where it "
            "silently did not work at all.")
    payloads = ["moy", PAIRED_URL, PORTED_URL, "http://10.0.0.5/?pin=0001",
                "http://192.168.100.101/?pin=4821",
                "http://moybyte-zero.local/?pin=4821", "x" * 78]
    # Staged FLAT, as `moy_qr`, because that is how a board carries it: the
    # build copies runtime/*.py into modules/ and freezes them as top-level
    # names. Importing it through a `runtime` package here would test a shape
    # no board has.
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(ROOT / "runtime" / "moy_qr.py", stage / "moy_qr.py")
    src = (_MP_DRIVER
           .replace("@STAGE@", repr(str(stage)))
           .replace("@PAYLOADS@", repr(payloads)))
    script = stage / "driver.py"
    script.write_text(src)
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=120)
    assert "OK" in out.stdout, out.stdout + out.stderr
    got = [ln for ln in out.stdout.strip().splitlines() if ln != "OK"]
    assert len(got) == len(payloads), out.stdout + out.stderr
    for payload, line in zip(payloads, got):
        want = "|".join("".join(str(c) for c in row)
                        for row in moy_qr.encode(payload))
        assert line == want, "MicroPython and CPython disagree on %r" % payload
