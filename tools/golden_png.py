#!/usr/bin/env python3
"""Render the shell goldens of one config to PNGs, to look at what a hash pins.

    tools/golden_png.py p4_1024x600_cs2_fs1_procs
    tools/golden_png.py tdeck_320x240_fs1_dark --surface settings --out /tmp/g

The config names are `CONFIGS` in `tests/test_shell_goldens.py`; `--surface`
keeps the surfaces whose name contains it. It runs that config's golden test
with `_render` wrapped, so each settled frame lands as `<out>/<surface>.png`
exactly as the hash saw it (the glass canvas, RGB565 big-endian). The test's
verdict is printed as usual: a moved golden still fails here.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def rgb565be_rows(buf, w, h):
    """RGB888 rows from a big-endian RGB565 buffer."""
    rows = []
    for y in range(h):
        row = bytearray(w * 3)
        o = y * w * 2
        for x in range(w):
            v = buf[o] << 8 | buf[o + 1]
            o += 2
            j = x * 3
            row[j] = (v >> 11 & 31) * 255 // 31
            row[j + 1] = (v >> 5 & 63) * 255 // 63
            row[j + 2] = (v & 31) * 255 // 31
        rows.append(bytes(row))
    return rows


class _Dump:
    def __init__(self, out, surface):
        self.out, self.surface, self.written = out, surface, []

    def pytest_collection_modifyitems(self, session, config, items):
        import pngwrite
        import test_shell_goldens as t
        orig = t._render

        def render(ws, surface, config_name):
            digest = orig(ws, surface, config_name)
            if self.surface is None or self.surface in surface:
                cv = ws.glass_canvas
                path = self.out / ("%s.png" % surface)
                pngwrite.write_png(str(path),
                                   rgb565be_rows(bytes(cv._buf), cv.w, cv.h),
                                   cv.w, cv.h)
                self.written.append(path)
            return digest
        t._render = render


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("config", help="a CONFIGS name in tests/test_shell_goldens.py")
    ap.add_argument("--surface", help="only surfaces whose name contains this")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(),
                                                  "moybyte-goldens"),
                    help="output folder (default <tmp>/moybyte-goldens)")
    a = ap.parse_args(argv)
    import pytest
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    dump = _Dump(out, a.surface)
    os.chdir(ROOT / "tests")
    rc = pytest.main(["-q", "-p", "no:xdist",
                      "test_shell_goldens.py::test_shell_surfaces_match_goldens"
                      "[%s]" % a.config], plugins=[dump])
    for p in dump.written:
        print(p)
    if not dump.written:
        print("no surface rendered: check the config name", file=sys.stderr)
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
