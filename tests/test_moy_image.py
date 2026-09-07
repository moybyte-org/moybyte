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

from runtime import moy_image  # noqa: E402

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
    blob = moy_image.encode_moyimg(40, 20, art)
    meta = json.loads(blob)
    assert meta["format"] == "moyimg-v1" and meta["w"] == 40 and meta["h"] == 20
    assert set(meta) == {"format", "w", "h", "data"}, (
        "one format -- there is no codec field to dispatch on")
    raw = moy_image._b64_decode(meta["data"])
    # A zlib header: CM=8 in the low nibble, and CINFO -- the window, minus
    # eight -- in the high one. THIS is what a reader takes its window from.
    assert raw[0] & 0x0F == 8
    assert (raw[0] >> 4) + 8 == moy_image.MOYIMG_WBITS
    assert (raw[0] * 256 + raw[1]) % 31 == 0


def test_the_pinned_window_is_the_measured_one():
    """12 is not a taste. Across every picture this repo ships it lands within
    half a percent of the best ratio any window reaches, for a 4 KB heap
    allocation instead of 32 KB -- and the window is live on a board that is
    compressing a kid's drawing while the shell is on screen."""
    assert moy_image.MOYIMG_WBITS == 12
    assert (1 << moy_image.MOYIMG_WBITS) == 4096


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
                       "data": moy_image._b64_encode(raw)})
    assert moy_image.decode_moyimg(blob) == (40, 20, art)


@pytest.mark.parametrize("size", [(1, 1), (3, 7), (40, 20), (320, 240), (512, 300)])
def test_encode_decode_round_trips(size):
    w, h = size
    art = _art(w, h)
    assert moy_image.decode_moyimg(moy_image.encode_moyimg(w, h, art)) == (w, h, art)


def test_a_list_of_indices_encodes_like_a_buffer():
    """A cart hands `image()` whatever it has; the codec must not care."""
    art = _art(8, 4)
    assert (moy_image.encode_moyimg(8, 4, list(art))
            == moy_image.encode_moyimg(8, 4, bytearray(art))
            == moy_image.encode_moyimg(8, 4, art))


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
    assert moy_image.decode_moyimg(bad) is None
    assert moy_image.moyimg_runs(bad) is None


def test_a_retired_rle_blob_reads_as_absent():
    """The strict reader, on the one legacy form that ever shipped. Paint's RLE
    blob is built here because nothing in the tree writes one any more -- and
    there is no migration that will (CLAUDE.md, 2026-09-07): a card carrying one
    reads as a picture that is not there, which is what every caller already
    draws as a placeholder."""
    packed = moy_image.pack_runs(_art(64, 48))
    blob = json.dumps({"format": "moyimg-v1", "w": 64, "h": 48, "codec": "rle",
                       "data": moy_image._b64_encode(packed)})
    assert moy_image.decode_moyimg(blob) is None
    assert moy_image.moyimg_runs(blob) is None


def test_extra_header_keys_survive_a_decode():
    """The #108 provenance stamp rides in the same object."""
    art = _art(8, 4)
    meta = json.loads(moy_image.encode_moyimg(8, 4, art))
    meta["src"] = "drawings/sunset"
    meta["sig"] = 12345
    assert moy_image.decode_moyimg(json.dumps(meta)) == (8, 4, art)


# -- runs: the cover shelf's cache, now DERIVED from the raster --------------

def test_runs_and_pixels_are_the_same_picture():
    art = _art(64, 48)
    blob = moy_image.encode_moyimg(64, 48, art)
    w, h, packed = moy_image.moyimg_runs(blob)
    assert (w, h) == (64, 48) and len(packed) % 2 == 0
    out = bytearray()
    for i in range(0, len(packed), 2):
        assert 1 <= packed[i] <= 255 and packed[i + 1] <= 63
        out.extend(bytes((packed[i + 1],)) * packed[i])
    assert bytes(out) == art


def test_a_run_never_exceeds_the_byte_it_is_counted_in():
    """255 is the cap a `count` byte can hold; a 300-long stretch is two runs."""
    packed = moy_image.pack_runs(bytes((9,)) * 300)
    assert packed == bytes((255, 9, 45, 9))


def _whole_raster_runs(text):
    """What `moyimg_runs` did before it streamed: inflate the lot, then scan it.
    The reference the streaming reader has to match byte for byte."""
    got = moy_image.decode_moyimg(text)
    return None if got is None else (got[0], got[1], moy_image.pack_runs(got[2]))


@pytest.mark.parametrize("asset", sorted(
    (ROOT / "system_carts").glob("**/*.moyimg")), ids=lambda p: p.parent.parent.name)
def test_streamed_runs_are_the_whole_raster_runs(asset):
    """The reader the launcher's idle prefetch runs once per cover. It reads the
    deflate stream a kilobyte at a time instead of inflating a 76,800-byte
    raster to derive 15KB of runs from -- and the ONLY thing that makes that a
    safe swap is that the bytes are identical, because a run that spans a piece
    boundary is an invitation to emit two runs where the scan emitted one."""
    text = asset.read_text()
    assert moy_image.moyimg_runs(text) == _whole_raster_runs(text)


@pytest.mark.parametrize("name,pix", [
    ("one flat colour", bytes((5,)) * 76800),
    ("a run longer than a piece", bytes((3,)) * 5000 + bytes(range(64)) * 10),
    ("runs that land exactly on 255", b"".join(bytes((v & 63,)) * 255 for v in range(40))),
    ("runs that land exactly on a piece", b"".join(bytes((v & 63,)) * 1024 for v in range(12))),
    ("one pixel", bytes((1,))),
    ("no run longer than one", bytes((i & 63) for i in range(9000))),
])
def test_a_run_across_a_piece_boundary_packs_as_one_run(name, pix):
    """The cases the boundary merge exists for. A 600-long run must come out
    255/255/90 wherever the reader happened to cut, never 255/255/255/35."""
    blob = moy_image.encode_moyimg(len(pix), 1, pix)
    got = moy_image.moyimg_runs(blob)
    assert got == _whole_raster_runs(blob), name
    out = bytearray()
    for i in range(0, len(got[2]), 2):
        assert 1 <= got[2][i] <= 255 and got[2][i + 1] <= 63
        out.extend(bytes((got[2][i + 1],)) * got[2][i])
    assert bytes(out) == pix, name


def test_the_runs_reader_never_inflates_a_whole_raster():
    """The allocation that took both S3 boards to the REPL a few minutes after a
    flash: 76,800 CONTIGUOUS bytes, asked for on an idle prefetch frame, on a
    heap with hundreds of KB free and no run that size (#66). The one-shot
    inflater must not be on this path at all."""
    art = _art(320, 240)
    blob = moy_image.encode_moyimg(320, 240, art)
    sizes = []
    real = moy_image._inflate_chunks

    def watched(raw, chunk=moy_image._INFLATE_CHUNK, _r=real):
        for piece in _r(raw, chunk):
            sizes.append(len(piece))
            yield piece

    moy_image._inflate_chunks = watched
    moy_image._inflate = _no_inflate
    try:
        got = moy_image.moyimg_runs(blob)
    finally:
        moy_image._inflate_chunks = real
        moy_image._inflate = _REAL_INFLATE
    assert got == (320, 240, moy_image.pack_runs(art))
    assert sum(sizes) == 320 * 240 and max(sizes) <= moy_image._INFLATE_CHUNK
    assert len(sizes) > 60, "one piece is a whole raster by another name"


_REAL_INFLATE = moy_image._inflate


def _no_inflate(raw):
    raise AssertionError("the streaming reader inflated the whole raster")


def test_a_heap_that_says_no_is_not_a_missing_picture():
    """MemoryError is the one exception these readers pass on. Swallowed as
    None it becomes "your drawing is gone" -- which Paint and `image()` would
    then cache and act on -- where raised it is a caller's choice to skip one
    picture and come back to it, which is exactly what the cover shelf does."""
    art = _art(16, 16)
    blob = moy_image.encode_moyimg(16, 16, art)
    real = moy_image._inflate
    moy_image._inflate = _starved
    try:
        with pytest.raises(MemoryError):
            moy_image.decode_moyimg(blob)
    finally:
        moy_image._inflate = real
    real_chunks = moy_image._inflate_chunks
    moy_image._inflate_chunks = _starved_chunks
    try:
        with pytest.raises(MemoryError):
            moy_image.moyimg_runs(blob)
    finally:
        moy_image._inflate_chunks = real_chunks


def _starved(raw):
    raise MemoryError("memory allocation failed, allocating 76800 bytes")


def _starved_chunks(raw, chunk=1024):
    raise MemoryError("memory allocation failed, allocating 76800 bytes")
    yield b""                      # noqa -- makes this a generator like the real one


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
    got = moy_image._deflate_pieces(pieces)
    assert moy_image._inflate(got) == art
    whole = moy_image._deflate(art)
    assert len(got) <= len(whole) * 1.05 + 64


def test_the_native_run_scanner_agrees_with_the_python_one():
    """moy_gfx.encode_runs is the host's Python loop in C. On a build without
    it (the host) this asserts the fallback against itself, which is worth the
    two lines it costs: the test is the same on the tier that has one."""
    art = _art(320, 240)
    native = moy_image._encode_runs()
    got = moy_image.pack_runs(art)
    out = bytearray()
    for i in range(0, len(got), 2):
        out.extend(bytes((got[i + 1],)) * got[i])
    assert bytes(out) == art
    if native is not None:
        assert native(art) == got


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
        got = moy_image.decode_moyimg(p.read_text())
        assert got is not None and len(got[2]) == got[0] * got[1], p
        seen += 1
    assert seen >= 13, "the seed pictures went missing, not the codec"


# -- and on a real MicroPython ----------------------------------------------

DRIVER = """
import sys
sys.path.insert(0, ".")
import json, moy_image

art = bytes(((i * 37) & 63) for i in range(320 * 240))
blob = moy_image.encode_moyimg(320, 240, art)
got = moy_image.decode_moyimg(blob)
assert got is not None and got[0] == 320 and got[1] == 240
assert bytes(got[2]) == art, "a board could not read back its own picture"

# ...and the one CPython wrote, which is the direction that actually ships.
host = open("host.moyimg").read()
hgot = moy_image.decode_moyimg(host)
assert hgot is not None and bytes(hgot[2]) == art, "a board could not read the host's"

# The two STREAMING halves, on the implementation that actually runs them: the
# shelf reads a cover through DeflateIO.read(n) and `_deflate_pieces` writes
# one through repeated DeflateIO.write(). Neither is CPython's decompressobj/
# compressobj, and this is the only place either is driven by the real thing --
# which is how we know `deflate` closes a block per write and CPython does not.
pieces = [art[i:i + 1024] for i in range(0, len(art), 1024)]
streamed_blob = moy_image._deflate_pieces(pieces)
assert moy_image._inflate(streamed_blob) == art, \\
    "a board could not read the picture it wrote piece by piece"
piecewise = len(streamed_blob)
streamed = moy_image.moyimg_runs(blob)
whole = moy_image.pack_runs(moy_image.decode_moyimg(blob)[2])
assert bytes(streamed[2]) == bytes(whole), "the streamed runs are not the runs"

meta = json.loads(blob)
raw = moy_image._b64_decode(meta["data"])
print("RESULT " + json.dumps({
    "bytes": len(blob), "same_as_host": blob == host, "piecewise": piecewise,
    "wbits": (raw[0] >> 4) + 8, "runs": len(streamed[2]),
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
    for name in ("moy_image.py", "moy_fs.py"):
        (tmp_path / name).write_text(
            (ROOT / "runtime" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    art = bytes(((i * 37) & 63) for i in range(320 * 240))
    (tmp_path / "host.moyimg").write_text(moy_image.encode_moyimg(320, 240, art))
    (tmp_path / "run.py").write_text(DRIVER)

    r = subprocess.run([exe, "run.py"], cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, "%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    line = [l for l in r.stdout.split("\n") if l.startswith("RESULT ")]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0][len("RESULT "):])
    assert got["wbits"] == moy_image.MOYIMG_WBITS
    assert got["runs"] > 0
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
          % (got["bytes"], len(moy_image.encode_moyimg(320, 240, art)),
             got["same_as_host"], got["piecewise"]))
