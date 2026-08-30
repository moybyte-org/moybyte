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
import threading
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
def _twin(tmp_path, mode, close_after=None, pin=None):
    """serve.py's board twin with a faked /update of the given shape.

    Yields `(base_url, process)`. The process is handed out because one test
    has to UNPLUG the console mid-session, and there is no way to test a
    vanished board without being able to make it vanish.
    """
    store = tmp_path / "store"
    store.mkdir()
    # Two carts: a single-cart store trips worker.js's kiosk path (the game IS
    # the page), and there would then be no page chrome to drive.
    for cart in ("star_catcher.moy", "sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)
    port = _free_port()
    argv = [sys.executable, "serve.py", str(port), "dist",
            "--carts", str(store), "--update", mode]
    if close_after is not None:
        argv += ["--close-after", str(close_after)]
    if pin:
        argv += ["--pin", pin]
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
        yield base, server
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
    with _twin(tmp_path, "headless") as (base, _server):
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
        # ASK THE CONSOLE, do not infer from the message. This assertion read
        # `u.get("running")` alone until 2026-08-30 and passed for the whole
        # life of a build in which `update_link.py` was never staged into the
        # bundle: web_boot.update_enable raised ImportError, ws.updater stayed
        # None, a headless Zero had no update row -- and this test was green,
        # because the message it reads is sent off the back of the PROBE
        # answering. `bound` comes from web_boot.services_json.
        assert u.get("bound") is True, (
            "the probe answered but the console has no updater: %r" % (u,))
        assert (u.get("services") or {}).get("updater") is True, u
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
    with _twin(tmp_path, "glass") as (base, _server):
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
        assert u.get("bound") is True, (
            "the probe answered but the console has no updater: %r" % (u,))
        assert u.get("screen") is True, u
        assert p["bound"]["noStrip"] is True, \
            "the page still draws an update UI of its own"


# -- the board went away ------------------------------------------------------
#
# Two disconnects, one panel, and the whole value is that they read differently.
# The DETECTION lives with the pump that sees the failures (worker.js's
# SYNC_GIVE_UP, gpio_link's MAX_FAILS, the persist give-up) and is pinned in the
# host suites. What no host suite can reach is the surface: whether a person
# staring at a stalled page is actually TOLD, and whether the sentence they get
# is the true one. Before these two tests the entire net under that was
# `"__moyLinkLost" in index.html` -- a symbol check, which is the exact shape of
# check that let a black screen ship on 2026-08-29.


def test_a_vanished_board_warns_that_this_tab_is_the_only_copy(tmp_path):
    web_e2e.require("update")
    with _twin(tmp_path, "headless") as (base, _server):
        p, out = _probes("link_lost.json", base, tmp_path / "shots")
        assert p["live"]["fn"] == "function" and p["live"]["px"] > 0, \
            "the console never booted, so nothing below means anything: %r" % (p["live"],)
        # Nothing is wrong yet. A panel that shows before a failure would pass
        # every assertion after this one while being useless.
        assert p["quiet"]["shown"] == "none", p["quiet"]
        assert p["quiet"]["link"] is None, p["quiet"]

        lost = p["lost"]
        assert lost["shown"] != "none", \
            "the board vanished and the page said nothing"
        assert lost["cls"] == "bad", \
            "a loss must be styled as a loss, not as an ordinary notice: %r" % (lost,)
        assert lost["link"] == "lost", lost
        assert "stopped answering" in lost["head"], lost
        # THE sentence. Board mode keeps no local store, so unshipped work is in
        # this tab only -- and the one action that destroys it is the reload a
        # person reaches for when a page looks stuck.
        assert "keeps no copy of its own" in lost["body"], lost
        assert "do not reload" in lost["body"], lost
        # The caller passed only the count; the warning is appended by the
        # surface, so a caller cannot forget it.
        assert "15 seconds" in lost["body"], lost

        # First reason wins: the silence after a loss must not get relabelled.
        assert p["after"]["cls"] == "bad", p["after"]
        assert "stopped answering" in p["after"]["head"], \
            "a later 'expected' overwrote the loss -- the warning would vanish " \
            "at the moment it is true: %r" % (p["after"],)


def test_an_update_says_so_and_does_not_cry_wolf(tmp_path):
    web_e2e.require("update")
    with _twin(tmp_path, "glass") as (base, _server):
        p, _ = _probes("link_expected.json", base, tmp_path / "shots")
        assert p["live"]["fn"] == "function" and p["live"]["px"] > 0, p["live"]
        e = p["expected"]
        assert e["shown"] != "none", \
            "a board restarting on purpose still owes the page an explanation"
        assert e["link"] == "expected" and e["cls"] != "bad", e
        assert "restarting" in e["body"], e
        # The half that makes the other test worth anything: nothing is at risk
        # here, so the data-loss sentence must be ABSENT.
        assert "keeps no copy of its own" not in e["body"], \
            "warning about unsynced work on an ordinary update is how the " \
            "warning stops being read: %r" % (e,)
        assert "do not reload" not in e["body"], e


def _probes_while(scenario, base, outdir, kill_after, victim):
    """`_probes`, but `victim` is terminated `kill_after` seconds in.

    The scenario has to keep running while the server dies, so browsershot goes
    through Popen rather than subprocess.run -- there is no other way to unplug
    a console in the middle of a browser session.
    """
    run = subprocess.Popen(
        ["node", "browsershot.mjs", "scenarios/" + scenario, str(outdir)],
        cwd=RUNNER, env=dict(os.environ, MOY_BASE=base),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    killed = [False]

    def _kill():
        time.sleep(kill_after)
        if run.poll() is None:
            victim.terminate()
            killed[0] = True

    t = threading.Thread(target=_kill, daemon=True)
    t.start()
    try:
        out, err = run.communicate(timeout=300)
    finally:
        t.join(timeout=5)
    assert killed[0], "the board outlived the scenario -- nothing was unplugged"
    assert run.returncode == 0, \
        "browsershot failed:\n%s\n%s" % (out[-3000:], err[-500:])
    probes = {}
    for m in re.findall(r"^   js -> (.*)$", out, re.M):
        d = json.loads(json.loads(m))
        probes[d["at"]] = d
    return probes, out


def test_the_page_notices_a_board_that_actually_vanished(tmp_path):
    """The chain the other two link tests assume.

    They call `__moyLinkLost` and prove the SURFACE -- which is worth proving,
    and is not the same as proving anything fires. Here the console is genuinely
    unplugged mid-session and nothing in the scenario touches the link API: the
    sweep's POST has to fail, worker.js has to count to SYNC_GIVE_UP, and the
    panel has to arrive on its own. Without that, "we tell the kid when the
    board is gone" rests on a function nobody calls in anger.
    """
    web_e2e.require("update")
    with _twin(tmp_path, "headless") as (base, _server):
        p, out = _probes_while("link_vanish.json", base, tmp_path / "shots",
                               kill_after=12.0, victim=_server)
        assert p["live"]["fn"] == "function", p["live"]
        assert p["live"]["link"] is None, \
            "the panel was up before the board was unplugged: %r" % (p["live"],)

        gone = p["gone"]
        assert gone["link"] == "lost", (
            "the board was unplugged and the page never noticed -- the "
            "detection never fired, whatever the surface can do: %r" % (gone,))
        assert gone["shown"] != "none" and gone["cls"] == "bad", gone
        assert "keeps no copy of its own" in gone["body"], gone


def test_an_idle_page_still_notices_and_does_not_cry_wolf(tmp_path):
    """The reported failure, both halves (2026-08-30).

    A reader sat on a console, switched it off themselves, changed nothing --
    and got no notice for over a minute, then got one warning them their work
    was only in that tab.

    Both came from the same place. The give-up counted failed PUSHES, and a page
    with nothing to push never posts, so the counter never advanced: the notice
    arrived whenever they next happened to change something. And the data-loss
    sentence was attached to the KIND rather than to whether anything was
    actually at risk.

    So this scenario touches nothing at all. Only the heartbeat can see the
    board go, and the wording has to reflect an empty outbox.
    """
    web_e2e.require("update")
    with _twin(tmp_path, "headless") as (base, _server):
        p, out = _probes_while("link_vanish_idle.json", base, tmp_path / "shots",
                               kill_after=10.0, victim=_server)
        assert p["live"]["fn"] == "function", p["live"]
        assert p["live"]["link"] is None, p["live"]

        gone = p["gone"]
        assert gone["link"] == "lost", (
            "an IDLE page never noticed the board was gone -- which is the "
            "'after a minute or more' half of the report: %r" % (gone,))
        assert gone["shown"] != "none" and gone["cls"] == "bad", gone
        # Nothing was typed, so nothing is at risk, so the alarming sentence
        # must NOT be there -- and something calmer must be.
        assert "keeps no copy of its own" not in gone["body"], (
            "told a reader who changed nothing that their work is at risk: %r"
            % (gone,))
        assert "do not reload" not in gone["body"], gone
        assert "Nothing of yours is waiting to be saved" in gone["body"], gone


def test_a_board_that_says_goodbye_is_not_reported_as_lost(tmp_path):
    """The other half of the report: it should never have said "lost" at all.

    Turning WEB CONSOLE off is deliberate, the console is fine, and nothing is
    at risk -- but a closed socket looks exactly like an unplugged board, so the
    page had no way to know. The board removes the guess by answering
    `{"error":"closing"}` for a few seconds before it goes.

    The page carried an "expected" kind for this from the day the surface was
    written, with NOTHING in the system able to produce it. This is the test
    that makes it a real path rather than a branch only scenarios reach.
    """
    web_e2e.require("update")
    with _twin(tmp_path, "headless", close_after=30.0) as (base, _server):
        p, out = _probes("link_goodbye.json", base, tmp_path / "shots")
        assert p["live"]["fn"] == "function", p["live"]
        assert p["live"]["link"] is None, p["live"]

        bye = p["bye"]
        assert bye["link"] == "expected", (
            "a deliberate switch-off still reads as a vanished board: %r" % (bye,))
        assert bye["shown"] != "none", "the reader was told nothing at all"
        assert bye["cls"] != "bad", "styled as a failure; nothing failed"
        assert "took its screen back" in bye["head"], bye
        assert "keeps no copy of its own" not in bye["body"], (
            "warned about unsynced work over a console that is fine and took "
            "everything it was given: %r" % (bye,))
