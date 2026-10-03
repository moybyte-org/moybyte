"""The cover reader (SPEC.md 3.6): the native `moy_png` the boards and the
browser run, `runtime/cover_png.py`'s `Reference` the host runs, and moy-spec's
vectors (tests/cover_vectors/, vendored -- its UPSTREAM.md says from where).

Three things are held together here, and each pair once had a way to drift:

  * BOTH readers reach moy-spec's verdict and pixels on every vector -- the
    profile is a public spec, and a console that shows a cover another host
    ignores (or the reverse) is the bug a conformance suite exists to catch;
  * the two readers agree byte for byte on everything else too: every output
    format, every reduction, every way a stream can be damaged. The native one
    is reached through the desktop MicroPython (tests/unix_mp.py), so this is
    the real binding over the real C, not a transcription;
  * a decode stopped after any row and resumed gives the same picture, because
    the shelf time-slices it.
"""

import hashlib
import json
import os
import random
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from runtime import cover_png
from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "cover_vectors"
SIDE = cover_png.SIDE


def _expected():
    with open(VECTORS / "expected.json", encoding="utf-8") as f:
        return json.load(f)["vectors"]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


# --- files written by hand ------------------------------------------------------

def _chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))


def _ihdr(ctype=2, w=SIDE, h=SIDE):
    return _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))


def _png(head, idats, tail=b"", iend=True):
    return (b"\x89PNG\r\n\x1a\n" + head
            + b"".join(_chunk(b"IDAT", z) for z in idats) + tail
            + (_chunk(b"IEND", b"") if iend else b""))


def _rgb_rows(seed=1):
    rnd = random.Random(seed)
    out = bytearray()
    for y in range(SIDE):
        out.append(rnd.randrange(5))
        for x in range(SIDE):
            out += bytes(((x * 2 + y) & 255, (y * 3) & 255,
                          rnd.randrange(256) if (x + y) % 17 == 0 else 90))
    return bytes(out)


def _idx_rows(n=16, seed=2):
    """Filter 0 throughout: under any other filter these bytes would unfilter
    to indices past an n-entry PLTE."""
    rnd = random.Random(seed)
    out = bytearray()
    for y in range(SIDE):
        out.append(0)
        out += bytes((((x >> 3) + (y >> 2)) % n) if rnd.random() < 0.9
                     else rnd.randrange(n) for x in range(SIDE))
    return bytes(out)


def _plte(n, seed=3):
    rnd = random.Random(seed)
    return _chunk(b"PLTE", bytes(rnd.randrange(256) for _ in range(3 * n)))


def _fixed_match_at_start():
    """A fixed-Huffman block whose first symbol is a length-3 match at
    distance 1 -- a reference before the start of the stream."""
    bits = []

    def put(v, n, rev=False):
        if rev:
            for i in range(n - 1, -1, -1):
                bits.append((v >> i) & 1)
        else:
            for i in range(n):
                bits.append((v >> i) & 1)
    put(1, 1)
    put(1, 2)
    put(0b0000001, 7, rev=True)           # symbol 257: length 3
    put(0, 5, rev=True)                   # distance code 0: distance 1
    put(0, 7, rev=True)                   # end of block
    out = bytearray()
    for i in range(0, len(bits), 8):
        b = 0
        for j, bit in enumerate(bits[i:i + 8]):
            b |= bit << j
        out.append(b)
    return b"\x78\x01" + bytes(out) + b"\0\0\0\0"


def hand_cases():
    """(name, bytes, a cover?) -- the corners moy-spec's reader decides one
    way and a hand-rolled one could decide the other."""
    raw = _rgb_rows()
    z = zlib.compress(raw, 9)
    ok = _ihdr()
    co = zlib.compressobj(9)
    synced = co.compress(raw) + co.flush(zlib.Z_SYNC_FLUSH) + co.flush()
    extra = zlib.compress(raw + b"\0", 9)
    out = [
        ("plain", _png(ok, [z]), True),
        ("empty_stored_block_before_the_end", _png(ok, [synced]), True),
        ("garbage_after_the_stream", _png(ok, [z + b"junk"]), True),
        ("bytes_after_iend", _png(ok, [z]) + b"trailing", True),
        ("bad_adler", _png(ok, [z[:-1] + bytes((z[-1] ^ 1,))]), False),
        ("no_adler", _png(ok, [z[:-4]]), False),
        ("data_past_the_picture", _png(ok, [extra]), False),
        ("one_byte_short", _png(ok, [zlib.compress(raw[:-1], 9)]), False),
        ("no_iend", _png(ok, [z], iend=False), False),
        ("no_idat", _png(ok, []), False),
        ("fdict", _png(ok, [b"\x78\x20" + z[2:]]), False),
        ("window_32k_plus", _png(ok, [bytes((0x88, 0x1C)) + z[2:]]), False),
        ("bad_header_check", _png(ok, [bytes((0x78, 0x9B)) + z[2:]]), False),
        ("distance_before_the_start", _png(ok, [_fixed_match_at_start()]), False),
        ("idat_split_by_a_text_chunk",
         _png(ok, [z[:100]], _chunk(b"tEXt", b"k\0v") + _chunk(b"IDAT", z[100:])),
         True),
        ("unknown_critical_chunk",
         _png(ok + _chunk(b"ABCD", b"x"), [z]), False),
        ("second_ihdr", _png(ok + _ihdr(), [z]), False),
        ("idat_runs_past_the_end", _png(ok, [z])[:-20], False),
    ]
    iraw = _idx_rows()
    iz = zlib.compress(iraw, 9)
    head3 = _ihdr(3)
    out += [
        ("indexed", _png(head3 + _plte(16), [iz]), True),
        ("indexed_plte_after_an_empty_idat",
         _png(head3 + _chunk(b"IDAT", b"") + _plte(16), [iz]), True),
        ("indexed_plte_after_data",
         _png(head3, [iz[:10]], _plte(16) + _chunk(b"IDAT", iz[10:])), False),
        ("indexed_two_pltes_the_last_wins",
         _png(head3 + _plte(2, 9) + _plte(16), [iz]), True),
        ("indexed_plte_too_short_for_the_pixels",
         _png(head3 + _plte(15), [iz]), False),
        ("indexed_plte_257", _png(head3 + _plte(257), [iz]), False),
        ("rgb_plte_of_any_length",
         _png(ok + _chunk(b"PLTE", b"\1\2"), [z]), True),
    ]
    return out


def mutated_cases(n=120, seed=7):
    """Valid covers with their zlib streams damaged, a seeded few at a time."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        ctype = rnd.choice((2, 3))
        raw = _rgb_rows(i) if ctype == 2 else _idx_rows(16, i)
        co = zlib.compressobj(rnd.choice((1, 6, 9)), zlib.DEFLATED,
                              rnd.choice((9, 12, 15)), 8,
                              rnd.choice((zlib.Z_DEFAULT_STRATEGY, zlib.Z_FIXED,
                                          zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE)))
        z = bytearray(co.compress(raw) + co.flush())
        kind = rnd.random()
        if kind < 0.6:
            for _ in range(rnd.randint(1, 3)):
                j = rnd.randrange(2, len(z))
                z[j] ^= 1 << rnd.randrange(8)
        elif kind < 0.8:
            z = z[:rnd.randrange(len(z))]
        cut = rnd.randrange(1, max(2, len(z)))
        head = _ihdr(ctype) + (_plte(16, i) if ctype == 3 else b"")
        out.append(("mutated_%03d" % i, _png(head, [bytes(z[:cut]), bytes(z[cut:])]),
                    None))
    return out


# --- the reference reader -------------------------------------------------------

@pytest.mark.parametrize("vec", _expected(), ids=lambda v: v["file"])
def test_the_reference_reaches_moy_specs_verdict(vec):
    data = (VECTORS / vec["file"]).read_bytes()
    assert len(data) == vec["bytes"]
    got = cover_png.decode(data)
    if vec["verdict"] == "cover":
        assert got is not None, vec["note"]
        assert _sha(got) == vec["rgb888_sha256"], vec["note"]
    else:
        assert got is None, vec["note"]


def test_the_vectors_are_the_ones_named_in_upstream():
    names = {v["file"] for v in _expected()}
    on_disk = {p.name for p in VECTORS.glob("*.png")}
    assert names == on_disk
    assert "7cf0434" in (VECTORS / "UPSTREAM.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name,data,cover", hand_cases(), ids=lambda c: c if isinstance(c, str) else "")
def test_the_reference_on_the_hand_written_corners(name, data, cover):
    assert (cover_png.decode(data) is not None) is cover, name


def test_nothing_out_of_profile_raises():
    rnd = random.Random(3)
    for _ in range(200):
        blob = b"\x89PNG\r\n\x1a\n" + bytes(rnd.randrange(256)
                                             for _ in range(rnd.randrange(64)))
        assert cover_png.decode(blob) is None
    assert cover_png.decode(b"") is None
    assert cover_png.decode(b"\x89PNG\r\n\x1a\n") is None


def test_a_resumed_decode_is_the_whole_decode():
    data = (VECTORS / "rgb_filters_mixed.png").read_bytes()
    whole = cover_png.decode(data, 1, cover_png.RGB565)
    for step in (1, 3, 5, 64):
        job = cover_png.Job(data, 1, cover_png.RGB565)
        out = bytearray(cover_png.out_size(1, cover_png.RGB565))
        calls = 0
        while True:
            r = job.rows(out, step)
            calls += 1
            if r != 0:
                break
        assert r == 1 and bytes(out) == whole
        assert calls == (SIDE + step - 1) // step


def test_a_reduction_is_the_rounded_box_mean():
    data = (VECTORS / "rgb_filter1.png").read_bytes()
    full = cover_png.decode(data)
    half = cover_png.decode(data, 2)
    want = bytearray()
    for oy in range(SIDE // 2):
        for ox in range(SIDE // 2):
            for c in range(3):
                s = sum(full[((2 * oy + dy) * SIDE + 2 * ox + dx) * 3 + c]
                        for dy in (0, 1) for dx in (0, 1))
                want.append((s + 2) // 4)
    assert half == bytes(want)


def test_565_is_the_canvas_word_in_either_byte_order():
    data = (VECTORS / "indexed_filter3.png").read_bytes()
    rgb = cover_png.decode(data)
    le = cover_png.decode(data, 1, cover_png.RGB565)
    sw = cover_png.decode(data, 1, cover_png.RGB565_SW)
    for i in (0, 77, SIDE * SIDE - 1):
        r, g, b = rgb[3 * i:3 * i + 3]
        w = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        assert le[2 * i:2 * i + 2] == bytes((w & 255, w >> 8))
        assert sw[2 * i:2 * i + 2] == bytes((w >> 8, w & 255))


def test_index_is_the_nearest_palette_entry_lowest_first():
    from device.device_canvas import MOY64_RGB
    data = (VECTORS / "rgb_filter2.png").read_bytes()
    rgb = cover_png.decode(data)
    idx = cover_png.decode(data, 1, cover_png.INDEX, MOY64_RGB)
    pal = MOY64_RGB
    for i in range(0, SIDE * SIDE, 997):
        r, g, b = rgb[3 * i:3 * i + 3]
        d = [(r - pal[3 * k]) ** 2 + (g - pal[3 * k + 1]) ** 2
             + (b - pal[3 * k + 2]) ** 2 for k in range(64)]
        assert idx[i] == d.index(min(d))


def test_encode_indexed_writes_a_cover_that_reads_back():
    from device.device_canvas import MOY64_RGB
    rnd = random.Random(5)
    indices = bytes(rnd.randrange(64) if rnd.random() < 0.1 else (x // 9) % 64
                    for x in range(SIDE * SIDE))
    data = cover_png.encode_indexed(indices, MOY64_RGB)
    assert len(data) <= cover_png.MAX_BYTES
    assert cover_png.decode(data, 1, cover_png.INDEX, MOY64_RGB) == indices
    rgb = cover_png.decode(data)
    assert rgb == b"".join(MOY64_RGB[3 * v:3 * v + 3] for v in indices)
    # The CRCs are real, so a strict reader elsewhere takes it too.
    p = 8
    while p < len(data):
        n = struct.unpack(">I", data[p:p + 4])[0]
        tag = data[p + 4:p + 8]
        crc = struct.unpack(">I", data[p + 8 + n:p + 12 + n])[0]
        assert crc == zlib.crc32(tag + data[p + 8:p + 8 + n]) & 0xFFFFFFFF
        p += 12 + n


# --- the native reader, and the two side by side ---------------------------------

# Every file is read as the picture, and once more a few rows at a time into
# the panel's byte order; the files named full_* go through every format and
# reduction as well. The Python side of each line is the slow half, which is
# what keeps the full matrix to a handful of files.
SHORT = ((1, 0, 0), (1, 2, 7))
FULL = ((1, 0, 0), (1, 0, 5), (1, 1, 0), (1, 2, 7), (1, 3, 0), (2, 0, 0),
        (2, 2, 3), (4, 3, 0), (8, 1, 0), (128, 0, 0))
_FULL_FROM = ("rgb_filters_mixed.png", "indexed_filters_mixed.png",
              "palette_256.png", "ancillary_and_split_idat.png")

_DRIVER = "SHORT = %r\nFULL = %r\n" % (SHORT, FULL) + r"""
import sys, os, hashlib, moy_png
work = bytearray(moy_png.WORK)
pal = open(sys.argv[2], "rb").read()
for name in sorted(os.listdir(sys.argv[1])):
    data = open(sys.argv[1] + "/" + name, "rb").read()
    for div, fmt, step in (FULL if name.startswith("full_") else SHORT):
        size = (128 // div) * (128 // div) * (3, 2, 2, 1)[fmt]
        out = bytearray(size)
        r = -1
        if moy_png.begin(work, data, div, fmt, pal if fmt == 3 else None):
            while True:
                r = moy_png.rows(work, data, out, step)
                if r:
                    break
        h = hashlib.sha256(out).digest().hex() if r == 1 else "-"
        print(name, div, fmt, step, h)
"""


def _reference_line(name, data, div, fmt, step, pal):
    job = cover_png.Job(data, div, fmt, pal if fmt == 3 else None)
    r = -1
    out = bytearray(cover_png.out_size(div, fmt))
    if job.ok:
        while True:
            r = job.rows(out, step)
            if r:
                break
    return "%s %d %d %d %s" % (name, div, fmt, step, _sha(out) if r == 1 else "-")


def test_the_native_reader_agrees_with_the_reference_on_everything(tmp_path):
    exe = require_unix_mp("moy_png", why="the boards' cover reader is C; this is "
                          "the one lane that runs it beside the host's")
    from device.device_canvas import MOY64_RGB
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    cases = [(p.name, p.read_bytes()) for p in sorted(VECTORS.glob("*.png"))]
    cases += [("full_" + n, (VECTORS / n).read_bytes()) for n in _FULL_FROM]
    cases += [(n + ".png", d) for n, d, _c in hand_cases()]
    cases += [(n + ".png", d) for n, d, _c in mutated_cases()]
    for name, data in cases:
        (corpus / name).write_bytes(data)
    pal = tmp_path / "pal.bin"
    pal.write_bytes(MOY64_RGB)
    script = tmp_path / "drive.py"
    script.write_text(_DRIVER, encoding="utf-8")
    run = subprocess.run([exe, str(script), str(corpus), str(pal)],
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr
    native = run.stdout.split("\n")
    want = []
    for name in sorted(os.listdir(corpus)):
        data = (corpus / name).read_bytes()
        for div, fmt, step in (FULL if name.startswith("full_") else SHORT):
            want.append(_reference_line(name, data, div, fmt, step, MOY64_RGB))
    assert native[:-1] == want
    # ...and the vectors' verdicts, read off the native lines directly.
    for vec in _expected():
        line = next(l for l in native if l.startswith(vec["file"] + " 1 0 0 "))
        h = line.rsplit(" ", 1)[1]
        assert (h != "-") is (vec["verdict"] == "cover"), vec["file"]
        if vec["verdict"] == "cover":
            assert h == vec["rgb888_sha256"], vec["file"]
    covers = sum(1 for l in native
                 if " 1 0 0 " in l and not l.endswith(" -"))
    assert covers >= 30, "the corpus barely exercises a whole decode"


_MP_REFERENCE = r"""
import sys, os, hashlib
sys.path.insert(0, sys.argv[2])
import cover_png
for name in sorted(os.listdir(sys.argv[1])):
    data = open(sys.argv[1] + "/" + name, "rb").read()
    ref = cover_png.Reference()
    out = bytearray(128 * 128 * 3)
    r = ref.rows(out, 0) if ref.begin(data) else -1
    print(name, hashlib.sha256(out).digest().hex() if r == 1 else "-")
"""


def test_the_reference_reads_every_cover_on_micropython_too(tmp_path):
    """A build without the native reader still draws covers: the reference
    runs there over MicroPython's `deflate`. It is held to every cover the
    vectors accept (not to zlib's verdict on a damaged stream -- the native
    reader is what a board has for that)."""
    exe = require_unix_mp(why="the reference's other inflater is MicroPython's")
    script = tmp_path / "ref.py"
    script.write_text(_MP_REFERENCE, encoding="utf-8")
    run = subprocess.run([exe, str(script), str(VECTORS), str(ROOT / "runtime")],
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    got = dict(l.split(" ", 1) for l in run.stdout.split("\n") if l)
    for vec in _expected():
        if vec["verdict"] == "cover":
            assert got[vec["file"]] == vec["rgb888_sha256"], vec["file"]


def test_the_two_readers_agree_on_their_constants():
    """cover_png restates the native module's numbers so the host, which has
    no native module, can size and name the same things. Pinned to the C."""
    exe = require_unix_mp("moy_png")
    names = ("WORK", "SIDE", "MAX_BYTES", "RGB888", "RGB565", "RGB565_SW", "INDEX")
    run = subprocess.run(
        [exe, "-c", "import moy_png; print(%s)" % ", ".join(
            "moy_png." + n for n in names)],
        capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    native = [int(v) for v in run.stdout.split()]
    assert native == [getattr(cover_png, n) for n in names]
