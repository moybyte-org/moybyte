"""The web runner's WORKER transport (#176), driven through its real protocol.

The console moved off the browser's main thread into worker.js: it owns the VM,
self-paces the frame loop and PUSHES frames, while the page only blits them.
Nothing else in the suite executes that JS, so this fakes the Web Worker globals
(`self` + `fetch`) in node and drives boot -> assets -> run -> frames -> input ->
reload, for both presentation tiers plus the idle case.

Needs a built dist/ (firmware/web_runner/build.sh [--stage-only]); skipped when
that or node is absent, so a fresh checkout still runs the suite green.
"""

import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.abspath(os.path.join(_HERE, os.pardir, "firmware", "web_runner"))
_TEST_JS = os.path.join(_RUNNER, "worker_protocol_test.mjs")


def _have_dist():
    return all(os.path.isfile(os.path.join(_RUNNER, "dist", n))
               for n in ("worker.js", "carts.json", "micropython.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built")
@pytest.mark.parametrize("search", [
    "?desktop=1&cart=brick_siege.moy",     # windowed desktop, cart running
    "?cart=star_catcher.moy",              # handheld tier, cart running
    "?desktop=1",                          # idle desk: the redraw gate must hold
])
def test_worker_protocol(search):
    p = subprocess.run(["node", _TEST_JS, search], cwd=_RUNNER,
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, "worker protocol failed for %s:\n%s\n%s" % (
        search, p.stdout, p.stderr)


def test_the_framebuffer_transport_is_wired_end_to_end():
    """The wiring no single-language test can see: the console publishes its
    framebuffer's address, the worker copies it out of the wasm heap and
    TRANSFERS it, and the page hands the buffer back to be refilled. Three
    languages that only meet in a browser.

    This replaces the dropped-frame resync contract, which is gone with the
    delta protocol it protected: a frame carries all its own pixels now, so
    losing one costs exactly one stale frame instead of stranding a surface
    until the next keyframe.
    """
    page = open(os.path.join(_RUNNER, "page_tail.js"), encoding="utf-8").read()
    worker = open(os.path.join(_RUNNER, "worker.js"), encoding="utf-8").read()
    boot = open(os.path.join(_RUNNER, "web_boot.py"), encoding="utf-8").read()
    build = open(os.path.join(_RUNNER, "build.sh"), encoding="utf-8").read()
    # The console publishes where the pixels are...
    assert "def fb_addr" in boot and "uctypes.addressof" in boot
    assert "def fb_len" in boot
    # ...the worker reads them from the heap and transfers the copy...
    assert "mp._module.HEAPU8" in worker and "fbAddr()" in worker
    assert 'self.postMessage({ t: "frame", s: s, fb: fb }, [fb])' in worker
    # ...which only works because HEAPU8 is an exported runtime method.
    assert "HEAPU8" in build
    # ...and the page returns the buffer so the ping-pong keeps its two halves.
    assert 't:"fbret"' in page
    assert 'm.t === "fbret"' in worker


def test_the_resync_protocol_is_gone():
    """The delta protocol's recovery path must not linger: a self-contained
    frame has nothing to re-seed, and a leftover request_keyframe would be a
    caller into a console verb that no longer exists."""
    page = open(os.path.join(_RUNNER, "page_tail.js"), encoding="utf-8").read()
    worker = open(os.path.join(_RUNNER, "worker.js"), encoding="utf-8").read()
    boot = open(os.path.join(_RUNNER, "web_boot.py"), encoding="utf-8").read()
    for name, text in (("page", page), ("worker", worker), ("web_boot", boot)):
        assert "request_keyframe" not in text, name
        assert '"resync"' not in text, name
