"""The page's AudioWorklet sink (page_core.html, THE SINK) under node: a dry
ring plays silence after a short fade, never what it held; underruns, gaps,
repeats and a full ring's refusals are counted; a ring that wraps plays
every sample once, in order. `sink_test.mjs` reads the processor's source out
of the page, so it needs no built dist/."""

import os
import shutil
import subprocess

import pytest

_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                       "firmware", "web_runner")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_worklet_sink_underruns_into_silence_and_counts():
    p = subprocess.run(["node", "sink_test.mjs"], cwd=_RUNNER,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, "sink test failed:\n%s\n%s" % (p.stdout, p.stderr)
