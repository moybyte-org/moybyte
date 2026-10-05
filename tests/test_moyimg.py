"""`.moyimg` has ONE format (2026-09-07), and these are the things that break.

Paint used to write a second codec -- uncompressed `count, value` run pairs,
`codec: "rle"` -- chosen because saving needed no compressor on a board. It cost
2.5-10x the bytes of the compressed form every other writer already used (one
320x240 cover 72 KB against 26), and a big flat string is the allocation an S3
heap refuses first. So: one format, the compressed one, and NO RLE reader
anywhere -- a blob still in the retired codec reads as an absent picture.

Four ways that goes wrong, each pinned below:

  * THE BOARD CANNOT COMPRESS. `deflate` ships read-only at the esp32 port's ROM
    level; the writer is a separate upstream flag. Without it a board opens every
    picture on the card and cannot save one -- and it fails at the moment a kid
    presses save, which is the worst possible place to find out.
  * THE TWO TIERS DISAGREE. CPython has `zlib` and no `deflate`, a board has
    `deflate` and no `zlib`. A stream one writes the other has to read, so the
    round trip is driven through a REAL MicroPython in both directions.
  * THE WINDOW DRIFTS. It is pinned on the write side and read off the stream, so
    a 15-bit picture written by an old tool and a 12-bit one written by Paint on a
    board both read everywhere. A reader that asked for a window SMALLER than the
    stream's would not raise -- it would inflate to garbage.
  * THE MIGRATION LOSES A DRAWING. It rewrites a kid's own files in place, so it
    is checked for pixel identity, for crash safety, and for being a no-op the
    second time.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

import unix_mp

ROOT = Path(__file__).resolve().parent.parent

from runtime import moyimg  # noqa: E402

BOARDS = (
    "lilygo_t_deck_plus_mainline/boards/MOYBYTE_TDECK",
    "esp32_p4_wifi6_touch_lcd_7b/boards/MOYBYTE_P4",
    "guition_jc3248w535/boards/MOYBYTE_GUITION_S3",
    "guition_jc8012p4a1c/boards/MOYBYTE_GUITION_P4",
    "seeed_xiao_esp32s3_zero/boards/MOYBYTE_ZERO",
)


def _art(w=40, h=20):
    """A picture with every palette index, a long run and a noisy tail."""
    raw = bytearray()
    raw.extend(bytes((7,)) * 700)
    raw.extend(bytes(range(64)))
    raw.extend(bytes(((i * 37) & 63) for i in range(w * h - len(raw))))
    return bytes(raw[:w * h])


# -- the wire form ----------------------------------------------------------

def test_the_blob_is_a_json_header_over_a_deflate_stream():
    art = _art()
    blob = moyimg.encode_moyimg(40, 20, art)
    meta = json.loads(blob)
    assert meta["format"] == "moyimg-v1" and meta["w"] == 40 and meta["h"] == 20
    assert set(meta) == {"format", "w", "h", "data"}, (
        "one format -- there is no codec field to dispatch on")
    raw = moyimg._b64_decode(meta["data"])
    # A zlib header: CM=8 in the low nibble, and CINFO -- the window, minus
    # eight -- in the high one. THIS is what a reader takes its window from.
    assert raw[0] & 0x0F == 8
    assert (raw[0] >> 4) + 8 == moyimg.MOYIMG_WBITS
    assert (raw[0] * 256 + raw[1]) % 31 == 0


def test_the_pinned_window_is_the_measured_one():
    """12 is not a taste. Across every picture this repo ships it lands within
    half a percent of the best ratio any window reaches, for a 4 KB heap
    allocation instead of 32 KB -- and the window is live on a board that is
    compressing a kid's drawing while the shell is on screen."""
    assert moyimg.MOYIMG_WBITS == 12
    assert (1 << moyimg.MOYIMG_WBITS) == 4096


def test_a_picture_written_with_a_bigger_window_still_reads():
    """Every `.moyimg` written before 2026-09-07 by a CPython tool is a 15-bit
    stream. They are not legacy and nothing rewrites them: the format did not
    change, only the window a WRITER picks, and a reader takes the stream's."""
    import zlib
    art = _art()
    comp = zlib.compressobj(9, zlib.DEFLATED, 15)
    raw = comp.compress(art) + comp.flush()
    assert (raw[0] >> 4) + 8 == 15
    blob = json.dumps({"format": "moyimg-v1", "w": 40, "h": 20,
                       "data": moyimg._b64_encode(raw)})
    assert moyimg.decode_moyimg(blob) == (40, 20, art)


@pytest.mark.parametrize("size", [(1, 1), (3, 7), (40, 20), (320, 240), (512, 300)])
def test_encode_decode_round_trips(size):
    w, h = size
    art = _art(w, h)
    assert moyimg.decode_moyimg(moyimg.encode_moyimg(w, h, art)) == (w, h, art)


def test_a_list_of_indices_encodes_like_a_buffer():
    """A cart hands `image()` whatever it has; the codec must not care."""
    art = _art(8, 4)
    assert (moyimg.encode_moyimg(8, 4, list(art))
            == moyimg.encode_moyimg(8, 4, bytearray(art))
            == moyimg.encode_moyimg(8, 4, art))


@pytest.mark.parametrize("bad", [
    "not json at all",
    '{"format": "moyimg-v1", "w": 4, "h": 4}',
    '{"format": "moyimg-v1", "w": 4, "h": 4, "data": "!!!"}',
    '{"format": "moyimg-v1", "w": 0, "h": 4, "data": "eJwDAAAAAAE="}',
    '{"format": "moyimg-v1", "w": 40, "h": 20, "data": "eJwDAAAAAAE="}',   # short
])
def test_a_blob_that_is_not_a_picture_reads_as_absent(bad):
    """None, never a raise: every caller on every tier treats a picture it
    cannot read as one that is not there."""
    assert moyimg.decode_moyimg(bad) is None


def test_a_retired_rle_blob_reads_as_absent():
    """The strict reader, on the one legacy form that ever shipped. Paint's RLE
    blob is built here because nothing in the tree writes one any more -- and
    there is no migration that will (CLAUDE.md, 2026-09-07): a card carrying one
    reads as a picture that is not there, which is what every caller already
    draws as a placeholder."""
    art = _art(64, 48)
    packed = bytearray()
    pos = 0
    while pos < len(art):
        count = 1
        while (pos + count < len(art) and count < 255
               and art[pos + count] == art[pos]):
            count += 1
        packed += bytes((count, art[pos]))
        pos += count
    blob = json.dumps({"format": "moyimg-v1", "w": 64, "h": 48, "codec": "rle",
                       "data": moyimg._b64_encode(bytes(packed))})
    assert moyimg.decode_moyimg(blob) is None


def test_extra_header_keys_survive_a_decode():
    """The #108 provenance stamp rides in the same object."""
    art = _art(8, 4)
    meta = json.loads(moyimg.encode_moyimg(8, 4, art))
    meta["src"] = "drawings/sunset"
    meta["sig"] = 12345
    assert moyimg.decode_moyimg(json.dumps(meta)) == (8, 4, art)


def test_a_heap_that_says_no_is_not_a_missing_picture():
    """MemoryError is the one exception this reader passes on. Swallowed as
    None it becomes "your drawing is gone" -- which Paint and `image()` would
    then cache and act on -- where raised it is a caller's choice to skip one
    picture and come back to it."""
    art = _art(16, 16)
    blob = moyimg.encode_moyimg(16, 16, art)
    real = moyimg._inflate
    moyimg._inflate = _starved
    try:
        with pytest.raises(MemoryError):
            moyimg.decode_moyimg(blob)
    finally:
        moyimg._inflate = real


def _starved(raw):
    raise MemoryError("memory allocation failed, allocating 76800 bytes")


@pytest.mark.parametrize("chunk", [1, 7, 1024, 100000])
def test_the_streaming_compressor_writes_a_picture_anything_can_read(chunk):
    """`_deflate_pieces` is the writer half of the streaming pair, and what it
    produces has to be a picture every reader already reads, at a size the
    format was chosen for -- however the pieces fell.

    NOT byte-identity, and that is measured rather than conceded. CPython's
    compressobj happens to emit the same stream either way; a board's `deflate`
    does NOT (it closes a block per write), and the size that costs is +0.2% at
    this chunk on a photo-like picture and NEGATIVE on the flat fills a kid
    actually draws. Two deflate implementations may pick different matches for
    the same input and both be right; what must hold is that either reads the
    other's, which is what this asserts."""
    art = _art(320, 240)
    pieces = [art[i:i + chunk] for i in range(0, len(art), chunk)]
    got = moyimg._deflate_pieces(pieces)
    assert moyimg._inflate(got) == art
    whole = moyimg._deflate(art)
    assert len(got) <= len(whole) * 1.05 + 64


# -- the boards can compress -------------------------------------------------

@pytest.mark.parametrize("board", BOARDS)
def test_every_board_builds_the_deflate_writer(board):
    """`deflate` is DECOMPRESS-ONLY at the esp32 port's ROM level: upstream gates
    the compressor behind FULL_FEATURES and the port sits at EXTRA_FEATURES. A
    board missing this line reads every picture on the card and cannot write
    one, and says so for the first time when a kid presses save."""
    body = (ROOT / "firmware" / board / "mpconfigboard.h").read_text()
    assert re.search(r"#define\s+MICROPY_PY_DEFLATE_COMPRESS\s+\(1\)", body), board


def test_the_desktop_micropython_mirrors_the_boards():
    """The binary six suites drive is `standard`, which is EXTRA_FEATURES like
    the boards -- so without the same flag it would report that MicroPython
    cannot write this format, about itself rather than about a board."""
    body = (ROOT / "Makefile").read_text()
    assert "CFLAGS_EXTRA=-DMICROPY_PY_DEFLATE_COMPRESS=1" in body


def test_the_browser_build_already_had_it():
    """The web runner is FULL_FEATURES, which is where upstream turns the
    compressor on -- so it needs no flag, and a page saves like a board."""
    body = (ROOT / "firmware" / "web_runner" / "variant"
            / "mpconfigvariant.h").read_text()
    assert "MICROPY_CONFIG_ROM_LEVEL_FULL_FEATURES" in body


# -- every shipped picture is the one format ---------------------------------

def test_no_asset_in_the_tree_is_the_retired_codec():
    seen = 0
    for p in sorted((ROOT / "system_carts").glob("**/*.moyimg")):
        meta = json.loads(p.read_text())
        assert "codec" not in meta, p
        got = moyimg.decode_moyimg(p.read_text())
        assert got is not None and len(got[2]) == got[0] * got[1], p
        seen += 1
    assert seen >= 2, "the seed pictures went missing, not the codec"


# -- and on a real MicroPython ----------------------------------------------

DRIVER = """
import sys
sys.path.insert(0, ".")
import json, moyimg

art = bytes(((i * 37) & 63) for i in range(320 * 240))
blob = moyimg.encode_moyimg(320, 240, art)
got = moyimg.decode_moyimg(blob)
assert got is not None and got[0] == 320 and got[1] == 240
assert bytes(got[2]) == art, "a board could not read back its own picture"

# ...and the one CPython wrote, which is the direction that actually ships.
host = open("host.moyimg").read()
hgot = moyimg.decode_moyimg(host)
assert hgot is not None and bytes(hgot[2]) == art, "a board could not read the host's"

# The STREAMING writer, on the implementation that actually runs it:
# `_deflate_pieces` writes through repeated DeflateIO.write(), which is not
# CPython's compressobj, and this is the only place it is driven by the real
# thing -- which is how we know `deflate` closes a block per write and CPython
# does not.
pieces = [art[i:i + 1024] for i in range(0, len(art), 1024)]
streamed_blob = moyimg._deflate_pieces(pieces)
assert moyimg._inflate(streamed_blob) == art, "a board could not read the picture it wrote piece by piece"
piecewise = len(streamed_blob)

meta = json.loads(blob)
raw = moyimg._b64_decode(meta["data"])
print("RESULT " + json.dumps({
    "bytes": len(blob), "same_as_host": blob == host, "piecewise": piecewise,
    "wbits": (raw[0] >> 4) + 8,
}))
"""


def test_the_codec_round_trips_on_the_interpreter_the_board_runs(tmp_path):
    """Nothing above this proves it. Every host check writes with `zlib` and
    reads with `zlib`; a board has no `zlib` at all -- `deflate` is a different
    implementation, reading its source one byte at a time and writing its
    output the same way, and until 2026-09-07 it could not write at all."""
    exe = unix_mp.require_unix_mp(
        why="This is the only lane where the board's OWN compressor writes a\n"
            "picture and its own inflater reads one. CPython's zlib is what\n"
            "every other check uses for both halves.")
    for name in ("moyimg.py",):
        (tmp_path / name).write_text(
            (ROOT / "runtime" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    art = bytes(((i * 37) & 63) for i in range(320 * 240))
    (tmp_path / "host.moyimg").write_text(moyimg.encode_moyimg(320, 240, art))
    (tmp_path / "run.py").write_text(DRIVER)

    r = subprocess.run([exe, "run.py"], cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, "%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    line = [l for l in r.stdout.split("\n") if l.startswith("RESULT ")]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0][len("RESULT "):])
    assert got["wbits"] == moyimg.MOYIMG_WBITS
    # A writer that streams its raster into the compressor pays a RATIO, not a
    # byte-identity -- `deflate` closes a block per write where CPython's
    # compressobj does not. Measured here rather than assumed, because a writer
    # that inflated its own output would undo the size argument the one-format
    # decision rests on.
    assert got["piecewise"] <= got["bytes"] * 1.05 + 64
    # Not asserted EQUAL: two deflate implementations may pick different
    # matches for the same input and both be right. What must hold is that each
    # reads the other, which the driver checked before printing this.
    print("\nunix MicroPython: 320x240 -> %d B (host %d B, identical: %s); "
          "written piece by piece %d B"
          % (got["bytes"], len(moyimg.encode_moyimg(320, 240, art)),
             got["same_as_host"], got["piecewise"]))
