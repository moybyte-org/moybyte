"""`installCartEngine`'s open(), against a fake VM that grows on its first
malloc (2026-10-03's regression: `cart_engine_test.mjs` has the full story).

Needs a built dist/ (firmware/web_runner/build.sh [--stage-only]): worker.js
resolves ./micropython.mjs relative to itself, so it only loads from dist/,
same reason test_web_worker_protocol.py gates the same way.
"""

import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.abspath(os.path.join(_HERE, os.pardir, "firmware", "web_runner"))
_TEST_JS = os.path.join(_RUNNER, "cart_engine_test.mjs")


def _have_dist():
    return all(os.path.isfile(os.path.join(_RUNNER, "dist", n))
               for n in ("worker.js", "carts.json", "micropython.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built")
def test_cart_engine_open_survives_a_heap_growth_from_its_own_boot():
    p = subprocess.run(["node", _TEST_JS], cwd=_RUNNER,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, "cart engine test failed:\n%s\n%s" % (
        p.stdout, p.stderr)
