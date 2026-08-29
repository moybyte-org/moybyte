"""Updating the serving board, from a REAL browser, against no hardware.

`tests/test_web_update.py` pins the board half -- the shared route, the gate,
the one status document, the console backend's hand-off. What it cannot reach
is the surface a person actually uses, which is a strip in the page and the one
panel it hands off to, and neither of those exists anywhere the host suite
looks. So this drives the shipped page in headless Chrome against serve.py's
board twin (`--update`), which builds its documents through
`moy_webhost.update_status` -- the same body a board builds them with -- so the
twin cannot drift from a board about what the page reads.

TWO RUNS, because the whole design is that two boards answer differently:

  headless   the Zero. It has no other display, so this page IS its update
             screen: it looks, it offers, and the two taps here are the two
             acts of consent every other board takes on its glass. Then the
             install ends in a reset, and the board GOING AWAY for a reason
             somebody chose has to read as that and not as a loss.
  glass      a console. One tap hands its own screen back, and this page stops
             being the console -- so what it must not do is sit there looking
             broken, and what it must not do EITHER is warn about unsynced
             work, because nothing here is at risk.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_update_e2e.py

Env-gated like the other two browser suites, and prerequisites (chrome, node, a
dist/ carrying the strip) SKIP with a reason on a bench and FAIL under CI --
tests/web_e2e.py owns that decision, because a suite that asks to run and then
skips is a green tick over nothing.
"""

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER, ROOT

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _twin(tmp_path, mode):
    """serve.py's board twin with a faked /update of the given shape."""
    store = tmp_path / "store"
    store.mkdir()
    # Two carts: a single-cart store trips worker.js's kiosk path (the game IS
    # the page), and there would then be no page chrome to drive.
    for cart in ("star_catcher.moy", "sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)
    port = _free_port()
    argv = [sys.executable, "serve.py", str(port), "dist",
            "--carts", str(store), "--update", mode]
    server = subprocess.Popen(argv, cwd=RUNNER, stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/sync", timeout=1).read()
                break
            except OSError:
                if server.poll() is not None:
                    pytest.fail("serve.py died on startup")
                time.sleep(0.1)
        else:
            pytest.fail("serve.py never answered /sync")
        yield base
    finally:
        server.terminate()
        server.wait(timeout=10)


def _probes(scenario, base, outdir):
    run = subprocess.run(
        ["node", "browsershot.mjs", "scenarios/" + scenario, str(outdir)],
        cwd=RUNNER, env=dict(os.environ, MOY_BASE=base),
        capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, \
        "browsershot failed:\n%s\n%s" % (run.stdout[-3000:], run.stderr[-500:])
    # browsershot prints each `js` result; the expressions return JSON, so the
    # line is a JSON string CONTAINING one. Keyed by `at`, never by index -- a
    # scenario grows steps.
    out = {}
    for m in re.findall(r"^   js -> (.*)$", run.stdout, re.M):
        d = json.loads(json.loads(m))
        out[d["at"]] = d
    return out, run.stdout


def test_a_headless_board_binds_the_update_bridge(tmp_path):
    """The console gets an UPDATER, in a real browser, from a real probe.

    This is the browser's whole job here. The update FLOW -- check, offer, the
    second consent, the polled progress, the reboot -- is driven in
    tests/test_remote_update.py against a real Workstation and the real
    UpdateUI, where the phase machine is cheap to exercise and a failure names
    the phase. Clicking a Settings row through headless Chrome would pin the
    row's COORDINATES; what cannot be tested anywhere else is that the probe,
    web_boot.update_enable and the worker's pump actually meet."""
    web_e2e.require("update")
    with _twin(tmp_path, "headless") as base:
        p, out = _probes("update_headless.json", base, tmp_path / "shots")
        # The console must have STARTED and DRAWN. `#s` is not the probe for
        # that -- page_core sets it to "live" off the frame path too, so it
        # stays green over a page whose entry point is gone. These two cannot:
        # the loader calls window.__moyStart by name, and a black canvas has no
        # lit pixels.
        assert p["live"]["fn"] == "function", \
            "window.__moyStart is missing -- the page cannot boot: %r" % (p["live"],)
        assert p["live"]["px"] > 0, \
            "the console drew nothing: %r" % (p["live"],)
        assert "bound" in p, out[-3000:]
        u = p["bound"]["u"]
        assert u, "the bridge never bound -- ws.updater is None, so Settings " \
                  "has no update row at all"
        assert u.get("running"), u
        # The hardware claim the whole design branches on. False here means
        # THIS page is the only progress report that exists.
        assert u.get("screen") is False, u
        assert p["bound"]["noStrip"] is True, \
            "the page still draws an update UI of its own"


def test_a_console_with_glass_binds_and_says_it_has_a_screen(tmp_path):
    """Same bridge, opposite hardware claim -- and that claim is the whole
    branch: `screen` true means triggering an update ENDS this page's job as the
    console, because the board's own update screen is where the frames that
    advance the flash are painted."""
    web_e2e.require("update")
    with _twin(tmp_path, "glass") as base:
        p, out = _probes("update_glass.json", base, tmp_path / "shots")
        # The console must have STARTED and DRAWN. `#s` is not the probe for
        # that -- page_core sets it to "live" off the frame path too, so it
        # stays green over a page whose entry point is gone. These two cannot:
        # the loader calls window.__moyStart by name, and a black canvas has no
        # lit pixels.
        assert p["live"]["fn"] == "function", \
            "window.__moyStart is missing -- the page cannot boot: %r" % (p["live"],)
        assert p["live"]["px"] > 0, \
            "the console drew nothing: %r" % (p["live"],)
        assert "bound" in p, out[-3000:]
        u = p["bound"]["u"]
        assert u, "the bridge never bound against a board with glass"
        assert u.get("screen") is True, u
        assert p["bound"]["noStrip"] is True, \
            "the page still draws an update UI of its own"
