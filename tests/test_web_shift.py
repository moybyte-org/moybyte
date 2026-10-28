"""shift_last in the browser's VM (docs/scroll_and_text_2026-10.md §3.2).

`shift_test.mjs` boots the frozen console from dist/ under node and shifts
rects of the canvas the page draws on, in place, against the per-pixel
reference `tests/test_shift_last.py` holds every other canvas to.

Needs a built dist/ (firmware/web_runner/build.sh), like `test_web_fillp.py`.
"""

import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.abspath(os.path.join(_HERE, os.pardir, "firmware", "web_runner"))
_TEST_JS = os.path.join(_RUNNER, "shift_test.mjs")


def _have_dist():
    return all(os.path.isfile(os.path.join(_RUNNER, "dist", n))
               for n in ("micropython.mjs", "micropython.wasm"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built")
def test_the_browsers_canvas_shifts_in_place_like_every_tier():
    p = subprocess.run(["node", _TEST_JS], cwd=_RUNNER,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "shift test failed:\n%s\n%s" % (p.stdout, p.stderr)
