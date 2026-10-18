"""A fill pattern reaches the pixels in the browser's VM (2026-10-18).

`fillp_test.mjs` boots the frozen console from dist/ under node and draws
patterned `rect`/`rectb` through the canvas the page draws on. The native draw
gates the wasm installs fill solid, and the host's kernel has none, so no host
test could see the pattern vanish: Moy's night wallpaper drew its dithered
ground as one navy band in the browser while the host drew it correctly.

Needs a built dist/ (firmware/web_runner/build.sh), like its siblings
`test_web_cart_engine.py` and `test_web_worker_protocol.py`.
"""

import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.abspath(os.path.join(_HERE, os.pardir, "firmware", "web_runner"))
_TEST_JS = os.path.join(_RUNNER, "fillp_test.mjs")


def _have_dist():
    return all(os.path.isfile(os.path.join(_RUNNER, "dist", n))
               for n in ("micropython.mjs", "micropython.wasm"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built")
def test_a_fill_pattern_lands_both_colours_through_the_wasm_canvas():
    p = subprocess.run(["node", _TEST_JS], cwd=_RUNNER,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "fill pattern test failed:\n%s\n%s" % (
        p.stdout, p.stderr)
