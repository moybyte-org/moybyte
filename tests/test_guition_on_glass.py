"""On-glass Guition JC3248W535 console tests (#202): drive the real board.

Gated: they run only when MOYBYTE_GUITION_PORT is set (e.g.
`MOYBYTE_GUITION_PORT=/dev/ttyACM1 .venv/bin/python -m pytest
tests/test_guition_on_glass.py -v`) -- the T-Deck suite's shape pointed at the
third board, per the port checklist's stage-6 exit criterion
(docs/board_ports_2026-08.md).

DELIBERATELY NO RESET, for the T-Deck's reason and by the same route: this
board's `[serial]` block declares `attach_only` because its USB-Serial/JTAG is
ON the SoC, so a reset tears the USB device down under the open handle and the
reader looks exactly like a dead board. The suite ATTACHES to the running
desktop, asserts, and leaves the console on the launcher.

Tests share the session in file order. The bodies every fullscreen-tier board
shares live in tests/on_glass.py; what is here is what is this board's.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_GUITION_PORT", "the Guition S3")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT,
            board_dir=ROOT / "firmware" / "guition_jc3248w535") as b:
        yield b


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    on_glass.fullscreen_tier_state(board)


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
    on_glass.every_app_claims_one_cart(board)


def test_py_probe_reaches_the_live_console(board):
    on_glass.py_probe_reaches_the_console(board)


def test_diag_toggle_roundtrips(board):
    on_glass.diag_toggle_roundtrips(board)


def test_swipe_rides_the_real_pointer_feed(board):
    on_glass.home_shelf_fling(board, 400, 80, 160)


def test_a_cart_runs_and_exits(board):
    on_glass.cart_runs_and_exits(board, "star")


def test_a_lua_cart_runs_and_exits(board):
    """moycore on the third board: the Lua tier is supposed to reach every
    board by default (the whole point of the shared native staging), so pin it
    with a real run, not just an import."""
    on_glass.cart_runs_and_exits(board, "sakura lua", title="Sakura Lua")


def test_idle_screen_blank_and_wake(board):
    on_glass.idle_blank_and_wake(board)
    on_glass.idle_timeout_restored(board)


def test_mem_reports_the_heap(board):
    on_glass.mem_reports_the_heap(board)


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
            # ...and the READ half is gated too since 2026-08-25: this board's
            # store is a child's work, and it used to be there for the asking
            # to anything on the same WiFi. The boot assets stay open, because
            # the page is what asks for the pin.
            for path in ("/carts.json", "/files.json"):
                try:
                    urllib.request.urlopen(url + path, timeout=20)
                    assert False, "%s answered without a pin" % path
                except urllib.error.HTTPError as exc:
                    assert exc.code == 403, (path, exc.code)
            r = urllib.request.urlopen(url + "/carts.json?pin=" + pin,
                                       timeout=60)
            assert "pytest_sync.moy/main.py" in _json.loads(r.read())
            r = urllib.request.urlopen(url + "/sync", timeout=15)
            assert _json.loads(r.read()) == {"sync": 1}, \
                "the capability marker must stay open, or no page finds a board"
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


def test_perf_line_is_the_one_format(board):
    """#206 item 2. This board has no windowed WM and no PPA, so those columns
    must read `-`: absence, never a 0 that a dead meter would also print."""
    got = on_glass.perf_line_is_the_one_format(board)
    for name in ("wmr", "wmw", "wms", "ppa", "fence_ms", "gfence_ms"):
        assert got[name] is None, (name, got[name])
