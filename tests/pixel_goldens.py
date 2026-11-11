"""What a hash-pinned pixel test does with a frame that moved.

Every test that pins a frame by its hash (the shell goldens, the sub-surfaces,
the cart raster, the tier frame, the slide, the non-ASCII heading) checks it
through `check`. A frame whose hash moved is written as a PNG under
`.build/goldens/<test file>/<test>/<name>.png`, beside a `node.txt` naming the
test, and the failure's message ends with `message`: where the frames are and
the one command that shows them against a ref,

    tools/golden_diff.py REF

which renders the same tests at REF in a temporary worktree and writes
before, after and a marked diff of each frame with an index page.

A test's folder is emptied when the test starts (tests/conftest.py), so what
is there is what its last run moved. With `MOYBYTE_GOLDEN_DUMP=DIR` every
checked frame is written under DIR instead, moved or not: that is how
`golden_diff.py` takes the "before" of a test at REF.
"""

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".build" / "goldens"
DUMP_ENV = "MOYBYTE_GOLDEN_DUMP"
COMMAND = "tools/golden_diff.py HEAD"
NODE_FILE = "node.txt"


def rgb565_rows(buf, w, h, big_endian=True):
    """RGB888 rows of an RGB565 frame (a host canvas's `_buf` is big-endian)."""
    b = bytes(buf)
    hi, lo = (0, 1) if big_endian else (1, 0)
    rows = []
    for y in range(h):
        row = bytearray(w * 3)
        o = y * w * 2
        for x in range(w):
            v = b[o + hi] << 8 | b[o + lo]
            o += 2
            j = x * 3
            row[j] = (v >> 11 & 31) * 255 // 31
            row[j + 1] = (v >> 5 & 63) * 255 // 63
            row[j + 2] = (v & 31) * 255 // 31
        rows.append(bytes(row))
    return rows


def index_rows(buf, w, h):
    """RGB888 rows of a frame of MOY64 palette indices, one byte a pixel."""
    from device_canvas import MOY64_RGB
    lut = [MOY64_RGB[3 * i:3 * i + 3] for i in range(64)]
    b = bytes(buf)
    return [b"".join(lut[i & 63] for i in b[y * w:(y + 1) * w]) for y in range(h)]


def node_dir(node=None):
    """`<test file stem>/<test name and params>` of the running test."""
    node = node or os.environ.get("PYTEST_CURRENT_TEST", "unknown::unknown")
    node = node.rsplit(" (", 1)[0]
    path, _, name = node.partition("::")
    name = re.sub(r"[^\w.,\[\]=-]+", "_", name)
    if len(name) > 120:                 # a long parameter id: cut, kept apart by its hash
        name = name[:100] + "-" + hashlib.sha256(name.encode()).hexdigest()[:12]
    return Path(Path(path).stem) / name


def node_id():
    return os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]


def clear(node):
    """Empty a test's folder: its frames are its last run's."""
    d = OUT / node_dir(node)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def _write(base, name, rows, w, h):
    sys.path.insert(0, str(ROOT / "tools"))
    import pngwrite
    d = base / node_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / NODE_FILE).write_text(node_id() + "\n", encoding="utf-8")
    path = d / ("%s.png" % re.sub(r"[^\w.,=-]+", "_", name))
    pngwrite.write_png(str(path), rows(), w, h)
    return path


def dumping():
    """Whether every checked frame is written (`MOYBYTE_GOLDEN_DUMP`)."""
    return bool(os.environ.get(DUMP_ENV))


def dump(name, rows, w, h):
    if dumping():
        _write(Path(os.environ[DUMP_ENV]), name, rows, w, h)


def check(name, got, want, rows, w, h):
    """Compare a frame's hash with its golden. `rows` is a callable giving the
    frame's RGB888 rows, called only when a PNG is written. A moved frame is
    written under `OUT` and its path returned; a held one returns None."""
    dump(name, rows, w, h)
    if got == want:
        return None
    return _write(OUT, name, rows, w, h)


def digest(frame):
    return hashlib.sha256(bytes(frame)).hexdigest()


def message(paths=()):
    """The lines a failure ends with: where the moved frames are and the
    command that diffs them against a ref."""
    paths = [p for p in paths if p]
    where = OUT / node_dir()
    try:
        where = where.relative_to(ROOT)
    except ValueError:
        pass
    return ("\n  %d moved frame(s) written to %s/\n"
            "  before, after and a marked diff of each (REF: where they held):\n"
            "    %s" % (len(paths), where, COMMAND))
