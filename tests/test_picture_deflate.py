"""The picture compressor (native/moy_store/moy_ufiles.c, `moy_uf_deflate`).

Every picture and cover a board writes goes through it, and a board's Python
codecs (runtime/moyimg.py) reach it as `moy_ufiles.deflate`. Its work is
bounded per byte: a matcher that walks its whole window at every position
spends that window on every byte of a flat page, and on a P4 a blank 512x300
drawing held the VM past the task watchdog.
"""

import base64
import json
import random
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from unix_mp import require_unix_mp  # noqa: E402

W, H = 512, 300


def _strokes():
    r = random.Random(1)
    pix = bytearray([5]) * (W * H)
    for _ in range(40):
        x, y, c = r.randrange(W), r.randrange(H), r.randrange(64)
        for _ in range(200):
            x = max(0, min(W - 3, x + r.randrange(-2, 3)))
            y = max(0, min(H - 3, y + r.randrange(-2, 3)))
            for dy in range(3):
                pix[(y + dy) * W + x:(y + dy) * W + x + 3] = bytes((c,)) * 3
    return bytes(pix)


PAGES = {
    "blank": bytes(W * H),
    "stripes": bytes((i // 7) % 2 for i in range(W * H)),
    "dots": bytes(1 if i % 200 == 0 else 0 for i in range(W * H)),
    "noise": bytes(random.Random(2).randrange(64) for _ in range(W * H)),
    "strokes": _strokes(),
    "runs at the length cap": bytes((i // 258) % 3 for i in range(W * H)),
}


@pytest.fixture(scope="module")
def uf():
    import moy_ufiles_binding
    try:
        return moy_ufiles_binding.binding()
    except ImportError as e:
        pytest.skip(str(e))


def _stream(blob):
    meta = json.loads(blob)
    assert (meta["w"], meta["h"]) == (W, H)
    return base64.b64decode(meta["data"])


@pytest.mark.parametrize("name", sorted(PAGES))
def test_a_page_reads_back_through_any_inflater(uf, name):
    z = _stream(uf.encode_image(W, H, PAGES[name]))
    assert z[0] == 0x48, "the zlib header names a 4 KB window"
    assert zlib.decompress(z) == PAGES[name]


def test_a_blank_page_is_one_run_of_long_matches(uf):
    """A blank page is the worst case for the work and the best case for the
    ratio: it must come out as maximal matches, in no time: the process's
    CPU time, which a loaded host's preemption does not stretch."""
    uf.encode_image(2, 2, bytes(4))
    t = time.process_time()
    z = _stream(uf.encode_image(W, H, PAGES["blank"]))
    took = time.process_time() - t
    assert len(z) < 1200, len(z)
    assert took < 0.25, "a blank page took %.0f ms to encode" % (took * 1e3)


def test_the_boards_python_codecs_reach_the_c_compressor(tmp_path):
    exe = require_unix_mp("moy_ufiles", board_model=True,
                          why="runtime/moyimg.py's compressor on a board.")
    script = tmp_path / "deflate_route.py"
    script.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import io, deflate, moy_ufiles, moyimg, cover_png\n"
        "assert moyimg._board_deflate() is moy_ufiles.deflate\n"
        "page = bytes(%d)\n"
        "z = moyimg._deflate(page)\n"
        "assert deflate.DeflateIO(io.BytesIO(z), deflate.ZLIB).read() == page\n"
        "assert moyimg.decode_moyimg(moyimg.encode_moyimg(%d, %d, page))[2] == page\n"
        "png = cover_png.encode_indexed(bytes(range(64)) * 256, bytes(range(192)))\n"
        "sys.stdout.buffer.write(png)\n"
        % (str(ROOT / "runtime"), W * H, W, H))
    proc = subprocess.run([exe, str(script)], capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()[-2000:]
    png = proc.stdout
    i = png.index(b"IDAT")
    n = int.from_bytes(png[i - 4:i], "big")
    raw = zlib.decompress(png[i + 4:i + 4 + n])
    rows = [raw[y * 129:(y + 1) * 129] for y in range(128)]
    assert all(r[0] == 0 and r[1:] == bytes(range(64)) * 2 for r in rows)
