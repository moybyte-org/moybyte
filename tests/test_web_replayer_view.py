"""The #175 `view` op, tested against the REAL replayer JS.

The wasm/node probes exercise the PYTHON emitter; the replayer in
runtime/web_view_page.py is browser JS that no other test executes. This runs the
draw-state + primitives + rep() dispatch slice under node with browser globals
stubbed, and asserts the view op places, clips and composes as documented.

Skipped when node is unavailable (the pytest suite must stay runnable without it).
"""

import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_JS = os.path.join(_HERE, os.pardir, "firmware", "web_runner",
                        "replayer_view_test.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_replayer_view_op():
    assert os.path.exists(_TEST_JS), _TEST_JS
    p = subprocess.run(["node", os.path.abspath(_TEST_JS)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "replayer view tests failed:\n%s\n%s" % (
        p.stdout, p.stderr)
    assert "0 failed" in p.stdout, p.stdout
