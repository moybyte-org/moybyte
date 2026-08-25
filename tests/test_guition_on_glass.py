"""On-glass Guition JC3248W535 console tests (#202): drive the real board.

Gated: they run only when MOYBYTE_GUITION_PORT is set (e.g.
`MOYBYTE_GUITION_PORT=/dev/ttyACM1 .venv/bin/python -m pytest
tests/test_guition_on_glass.py -v`) -- the T-Deck suite's shape pointed at the
third board, per the port checklist's stage-6 exit criterion
(docs/board_ports_2026-08.md).

DELIBERATELY NO RESET, the T-Deck's reason verbatim: this board's
USB-Serial/JTAG is ON the SoC, so a reset tears the USB device down under the
open handle and the reader looks exactly like a dead board. The suite ATTACHES
to the running desktop, asserts, and leaves the console on the launcher.

Tests share the session in file order.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PORT = os.environ.get("MOYBYTE_GUITION_PORT")

pytestmark = pytest.mark.skipif(
    not PORT,
    reason="MOYBYTE_GUITION_PORT not set (needs the Guition S3 on serial)")


@pytest.fixture(scope="module")
def board():
    # The shared line-pump driver, pointed at this board's [serial] block:
    # dtr/rts HIGH at open (an open with both LOW chip-resets a USB-Serial/JTAG
    # board), attach_only, 768-byte chunk. The same declaration push_cart.py
    # and the flash targets read.
    from p4_autotest import P4Board
    b = P4Board(PORT, board_dir=ROOT / "firmware" / "guition_jc3248w535")
    b.drain(0.8)
    line = b.cmd("state", wait_for="STATE ", timeout=10.0)
    if line is None:
        raise RuntimeError(
            "the Guition did not answer `state` on %s -- is the desktop "
            "running? (this suite attaches, it does not reset)" % PORT)
    # Identity, not just liveness: the other S3 shares this usb id and answers
    # `state` just as happily (MOYBYTE_GUITION_PORT=auto resolves the port).
    b.verify_board()
    yield b
    b.close()


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    st = board.state()
    assert isinstance(st.get("frames"), int) and st["frames"] > 0
    assert st.get("stack"), st
    assert st["stack"][-1] == st["screen"]
    asleep, secs = st["psave"]
    assert asleep is False
    assert secs > 0


def test_the_system_canvas_is_the_landscape_glass(board):
    """The board's one structural novelty (#202): the first FULLSCREEN-tier
    console whose system canvas (480x320 landscape, rotated in moy_axs's band
    copy) is not its game canvas (320x240). Assert both sizes and the
    viewport seam through the live console."""
    line = board.cmd("py (ws.sys_canvas.w, ws.sys_canvas.h, ws.canvas.w, ws.canvas.h)",
                     wait_for="PY ")
    assert line == "PY (480, 320, 320, 240)", line
    # composite_game's placement: 1:1, centred both ways.
    line = board.cmd("py ws.wm.viewport()", wait_for="PY ")
    assert line == "PY (80, 40, 1)", line


def test_every_system_app_claims_exactly_one_cart(board):
    claims = board.state().get("app_claims") or {}
    assert claims, "no system apps registered?"
    wrong = {k: v for k, v in claims.items() if v != 1}
    assert not wrong, "app cart claims off (seed/title drift?): %r" % wrong


def test_py_probe_reaches_the_live_console(board):
    line = board.cmd("py ws._frames_drawn", wait_for="PY ")
    assert line is not None and line.startswith("PY "), line
    assert int(line.split("PY ", 1)[1]) > 0
    line = board.cmd("py boot.done", wait_for="PY ")
    assert line == "PY True", line


def test_diag_toggle_roundtrips(board):
    board.cmd("diag 1", wait_for="REMOTE diag on")
    assert board.state()["diag"] is True
    board.cmd("diag 0", wait_for="REMOTE diag off")
    assert board.state()["diag"] is False


def test_swipe_rides_the_real_pointer_feed(board):
    """A horizontal fling over the home shelf, through the same pointer the
    AXS15231 feeds -- and the console is still on home afterwards."""
    frames0 = board.state()["frames"]
    board.swipe(400, 160, 80, 160, frames=20)
    st = board.state()
    assert st["stack"][-1] == "launcher", st["stack"]
    assert st["frames"] > frames0, "the gesture drew no frames"
    board.swipe(80, 160, 400, 160, frames=20)
    board.drain(0.8)


def test_a_cart_runs_and_exits(board):
    """The _GATE_SEQ blind-spot closer, on this board's glass from day one:
    launch through the real launcher path, assert the cart is ticking, exit
    through the cart-quit flag."""
    line = board.cmd("run star", wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.0)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the cart is not ticking"
    board.cmd("py ws.input.cart_quit = True", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "quit did not pop to the caller: %r" % st
    assert st["stack"][-1] == "launcher"


def test_a_lua_cart_runs_and_exits(board):
    """moycore on the third board: the Lua tier is supposed to reach every
    board by default (the whole point of the shared native staging), so pin it
    with a real run, not just an import."""
    line = board.cmd("run sakura lua", wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.0)
    st = board.state()
    assert st.get("cart") == "Sakura Lua", st.get("cart")
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the Lua cart is not ticking"
    board.cmd("py ws.input.cart_quit = True", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "quit did not pop to the caller: %r" % st
    assert st["stack"][-1] == "launcher"


def test_idle_screen_blank_and_wake(board):
    board.cmd("power 3", wait_for="REMOTE power")
    board.drain(0.3)
    board.drain(6.0)                       # say nothing; let the timer expire
    assert board.state()["psave"][0] is True, "the panel never blanked"
    board.drain(0.5)                       # the state query was activity
    assert board.state()["psave"][0] is False, "input did not wake the panel"

    board.cmd("power off", wait_for="REMOTE power")
    board.drain(1.0)
    assert board.state()["psave"][0] is True, "`power off` did not blank"

    board.cmd("power 300", wait_for="REMOTE power")   # restore the default
    board.drain(0.5)
    assert board.state()["psave"][0] is False
    assert board.state()["psave"][1] == 300


def test_mem_reports_the_heap(board):
    line = board.cmd("mem", wait_for="REMOTE mem")
    assert line is not None and "live=" in line and "free=" in line, line


def test_sync_push_writes_the_store_and_the_shelf_follows(board):
    """The 3.4 sync RPC against the REAL board: bring the webhost up, POST a
    batch from this machine over the LAN, and read the result back over
    serial -- the file on the TF card, the cart on the LIVE launcher (the
    shelf rescan, with no reboot anywhere), then a dc batch that removes it
    again. Skips rather than fails when the bench has no shared network:
    everything up to the HTTP hop is the other tests' job.

    Runs LAST-ish on purpose: it flips WiFi + the webhost on, and puts both
    back the way it found them."""
    import json as _json
    import urllib.request

    line = board.cmd("web", wait_for="WEB ", timeout=30.0)
    if line is None or "http://" not in line:
        pytest.skip("webhost did not come up (no wifi on this bench): %r" % line)
    # Since #197 the `web` line is the PAIRED url -- the pin rides ?pin= and
    # every write batch must carry it (a bare batch is the 403 the pin exists
    # to give). The glass is parked on the connection screen while this runs.
    paired = "http://" + line.split("http://", 1)[1].split()[0].rstrip("/")
    pin = paired.split("pin=", 1)[1].split("&")[0] if "pin=" in paired else None
    url = paired.split("?", 1)[0].rstrip("/")
    try:
        batch = _json.dumps({"v": 1, "pin": pin, "ops": [
            {"p": "pytest_sync.moy/manifest.json",
             "t": '{"title": "Pytest Sync", "type": "game", "main": "main.py"}'},
            {"p": "pytest_sync.moy/main.py",
             "t": "def _draw():\n    cls(11)\n"},
        ]}).encode()
        r = urllib.request.urlopen(urllib.request.Request(
            url + "/sync", data=batch,
            headers={"Content-Type": "application/json"}), timeout=15)
        doc = _json.loads(r.read())
    except OSError as exc:
        pytest.skip("board url unreachable from this machine: %s" % exc)
    try:
        assert doc == {"ok": 2, "err": []}, doc
        if pin:
            # The gate itself, on glass: the same batch without the pin.
            import urllib.error
            try:
                urllib.request.urlopen(urllib.request.Request(
                    url + "/sync",
                    data=_json.dumps({"v": 1, "ops": []}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=15)
                assert False, "a pinless batch was accepted"
            except urllib.error.HTTPError as exc:
                assert exc.code == 403, exc.code
        board.drain(0.5)
        line = board.cmd(
            "py print('SYNCED=' + repr(any((c.get('path') or '')"
            ".endswith('pytest_sync.moy') for c in ws.launcher.items)))",
            wait_for="SYNCED=", timeout=8.0)
        assert line is not None and "SYNCED=True" in line, line
        # ...and the dc op takes it back off the card AND the shelf.
        batch = _json.dumps({"v": 1, "pin": pin,
                             "ops": [{"p": "pytest_sync.moy",
                                      "dc": 1}]}).encode()
        r = urllib.request.urlopen(urllib.request.Request(
            url + "/sync", data=batch,
            headers={"Content-Type": "application/json"}), timeout=15)
        assert _json.loads(r.read())["ok"] == 1
        board.drain(0.5)
        line = board.cmd(
            "py import os; print('GONE=' + repr("
            "any((c.get('path') or '').endswith('pytest_sync.moy') "
            "for c in ws.launcher.items) is False "
            "and 'pytest_sync.moy' not in os.listdir(ws.carts_root)))",
            wait_for="GONE=", timeout=8.0)
        assert line is not None and "GONE=True" in line, line
    finally:
        # Leave the board as found: unpark the glass and stop the host.
        # stop_web_console, not toggle -- a toggle would START a host that
        # died under the parked screen (the reason the verb exists).
        board.cmd("py ws.stop_web_console(); print('WEBOFF')",
                  wait_for="WEBOFF", timeout=8.0)
