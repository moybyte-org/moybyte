"""The web runner's WORKER transport (#176), driven through its real protocol.

The console moved off the browser's main thread into worker.js: it owns the VM,
self-paces the frame loop and PUSHES frames, while the page only replays + blits.
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built")
def test_dropped_frame_resync():
    """A DROPPED frame must not strand the page (owner, 2026-07-31: on a tablet,
    PLAY appeared to do nothing and a later drag showed the Library with the
    desktop still around it).

    page_tail keeps only the newest frame per rAF, but the #76 delta ships
    {"same":1} for surfaces it believes the client holds -- so the frame carrying
    a surface in full is the only chance the page gets. The recovery is the page
    reporting the drop; this drives the console through it."""
    p = subprocess.run(["node", os.path.join(_RUNNER, "resync_test.mjs")],
                       cwd=_RUNNER, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, "resync contract failed:\n%s\n%s" % (p.stdout, p.stderr)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built")
def test_assets_payload_is_never_stale():
    """The /assets payload's non-image fields must track the console (owner,
    2026-07-31: on a phone playing a buttons-only cart, the on-screen keyboard
    button blinked every couple of seconds -- asset re-fetches were answering
    with the LAUNCHER's payload, whose null input hint means 'show every
    control', and the next frame's real hint hid it again)."""
    p = subprocess.run(["node", os.path.join(_RUNNER, "assets_hint_test.mjs")],
                       cwd=_RUNNER, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, "assets hint contract failed:\n%s\n%s" % (p.stdout, p.stderr)


def test_page_reports_dropped_frames_to_the_console():
    """The wiring the node test cannot see: page drops a frame -> posts `resync`
    -> the worker calls web_boot.request_keyframe. Static, because the three
    pieces live in three languages and only ever meet in a browser."""
    page = open(os.path.join(_RUNNER, "page_tail.js"), encoding="utf-8").read()
    worker = open(os.path.join(_RUNNER, "worker.js"), encoding="utf-8").read()
    boot = open(os.path.join(_RUNNER, "web_boot.py"), encoding="utf-8").read()
    # The page notices the drop (the overwrite of an unrendered frame) and asks.
    assert "dropped++" in page and 'postMessage({t:"resync"})' in page
    # The worker routes the ask to the console...
    assert 'm.t === "resync"' in worker and "request_keyframe" in worker
    # ... which re-seeds this client: forget the cache, draw the next frame full.
    assert "def request_keyframe" in boot
    assert "delta.reset()" in boot and "arm_surface_keyframe" in boot
