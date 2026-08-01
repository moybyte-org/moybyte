"""On-glass P4 console tests (#58): drive the REAL board over serial.

Gated: they run only when MOYBYTE_P4_PORT is set (e.g.
`MOYBYTE_P4_PORT=/dev/ttyACM0 .venv/bin/python -m pytest
tests/test_p4_on_glass.py -v`), so the normal host suite never needs
hardware. One board reset per module; tests share the session and are
ordered (file order == execution order), each leaving the console in the
state the next one expects -- an on-glass tour, not isolated units.

The device half is the serial dev-command set in
firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py (`swipe` feeds
the real pointer path, `state` answers with a JSON snapshot); the driver is
tools/p4_autotest.P4Board.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PORT = os.environ.get("MOYBYTE_P4_PORT")

pytestmark = pytest.mark.skipif(
    not PORT, reason="MOYBYTE_P4_PORT not set (needs the P4 on serial)")


@pytest.fixture(scope="module")
def board():
    from p4_autotest import P4Board
    b = P4Board(PORT)
    b.reset()
    b.cmd("diag 1")
    yield b
    # Leave the board freshly booted on the desk for a human.
    try:
        b.ser.write(b"\r\x03")
        b.drain(0.5)
        b.ser.write(b"\x04")
        b.drain(1.0)
    finally:
        b.close()


def test_boots_to_the_desk(board):
    st = board.state()
    assert st.get("desk") is True
    assert not st.get("order")


def test_wifi_status_is_readable(board):
    st = board.state()
    assert "wifi_err" not in st, st.get("wifi_err")
    assert st.get("wifi") is None or isinstance(st["wifi"], list)


def test_appearance_cart_is_claimed(board):
    """The Appearance app must claim its cart on the DEVICE store. The device
    seeds the folder from the TITLE slug (appearance.moy) while the host copies
    the source folder (theme_picker.moy); an is_app that knows only the host
    name reads False and Settings' APPEARANCE row silently does nothing (the
    on-glass 2026-07-25 report)."""
    st = board.state()
    cart = st.get("appearance_cart")
    assert cart, "no Appearance cart in the device store"
    assert cart.get("is_app") is True, cart


def test_every_system_app_claims_exactly_one_cart(board):
    """The same identity check for every registered app, not just Appearance."""
    claims = board.state().get("app_claims") or {}
    assert claims, "no system apps registered on device"
    assert all(n == 1 for n in claims.values()), claims


def test_open_settings_window(board):
    board.open("settings")
    board.drain(0.5)
    st = board.state()
    assert "settings" in st.get("order", ())


def test_settings_rows_scroll_on_swipe(board):
    st = board.state()
    cx, row_y, row_h = board.settings_geometry(st)
    board.swipe(cx, row_y(4), cx, row_y(4) - int(2.5 * row_h), frames=25)
    st = board.state()
    assert (st["settings"]["set_top"] or 0) > 0, st["settings"]


def test_scroll_survives_release(board):
    """Letting go must NOT snap the rows back to the top (the on-glass
    'thrown at the start' report)."""
    top = board.state()["settings"]["set_top"]
    board.drain(1.0)
    st = board.state()
    assert st["settings"]["set_top"] == top, st["settings"]


def test_appearance_opens(board):
    line = board.open("appearance")
    board.drain(0.5)
    st = board.state()
    assert "appearance" in st.get("order", ()), (line, st.get("order"))


def test_picker_opens(board):
    board.open("picker")
    board.drain(6.0)              # first-open cover pop-in settles
    st = board.state()
    assert "make" in st.get("order", ())


def test_window_buffers_are_single_retained_surfaces(board):
    """(#113) A window buffer holds the LAST paint, so it must advertise
    RETAINED_FRAMES = 1. The class default 2 describes the root DPI ping-pong;
    inheriting it made a picker drag shift by ~twice the real delta and ghost a
    duplicate of every card (owner-reported on glass, 2026-07-25)."""
    ret = board.state().get("win_retained") or {}
    assert ret, "no window buffers to check"
    assert all(v == 1 for v in ret.values()), ret


def test_draw_gates_are_installed(board):
    """(#155) rect/rectb/print/pix must be the NATIVE moy_gfx callables on the
    root system canvas AND on every window content buffer. Measured on glass
    2026-07-26: the Python wrapper cost 50.2us against 5.2us for the fill_rect
    kernel it ends in, so an un-gated canvas silently pays ~10x per chrome
    call."""
    assert board.pyval("str(type(ws.sys_canvas.rect))") == "<class 'draw_gate'>"
    assert board.pyval("ws.sys_canvas._gate_ctx is not None") is True
    ungated = board.pyval(
        "[k for k, w in ws.wm._wins.items() if w.buf._gate_ctx is None]")
    assert ungated == [], ungated


def test_draw_gates_take_the_traffic(board):
    """The gates must actually be drawing -- a fallback that quietly swallowed
    every call would look installed and measure fast."""
    board.pyexec("ws.sys_canvas.gate_counts_reset()\nws.mark_dirty()")
    board.drain(1.5)
    fills, texts, _fu, _tu = board.pyval("ws.sys_canvas.gate_counts()")
    assert fills > 0 and texts > 0, (fills, texts)


def test_window_chrome_freezes_during_a_content_scroll(board):
    """(#155) A window's title strip is disjoint from its content stamp, so a
    quiet frame must leave it alone once both ping-pong buffers hold it -- 8.2ms
    of a 70ms picker-scroll frame before the freeze. Probed MID-gesture: the
    freeze only engages while the desk serves its cache.

    tools/p4_chrome_freeze.py is the deeper version (it byte-compares the strip
    out of both ping-pong buffers); this pins that the freeze engages at all."""
    board.open("settings")
    board.drain(1.0)
    w = board.state()["wins"]["settings"]
    cx = w[0] + 1 + w[2] // 2
    ctop = w[1] + 1 + w[4]
    board.swipe_async(cx, ctop + (w[3] - w[4]) - 40, cx, ctop + 40, frames=200)
    board.drain(1.5)                     # let the streak reach both buffers
    quiet = board.pyval("ws.wm._chrome_quiet")
    streak = board.pyval("ws.wm._wins['settings']._chrome_streak")
    board.wait_line("swipe done", 30)
    assert quiet is True, "the scroll frame never went quiet"
    assert (streak or 0) >= 2, "chrome never froze (streak=%s)" % (streak,)


def test_perf_lines_flow(board):
    n0 = len(board.lines)
    board.drain(4.0)
    assert board.perf_lines(n0), "no PERF lines while idle"


def test_a_drag_touches_no_storage_and_rebuilds_no_cache(board):
    """The two invariants this board's UI perf now rests on, asserted on the real
    console via ws.note_cost (reported in `state` as "costs").

    Both were bugs found the slow way on 2026-07-26, each costing a day because
    neither produced any signal -- they just made two frames per gesture 5x
    slower:

      * cover blob reads (58ms each on this flash, 22ms even when the file is
        absent) landed on DRAG frames, because a cover was only loaded when its
        card first scrolled into view. Now prefetched on idle frames, so a drag
        must read nothing at all.
      * the bar's strip cache rebuilt on every switch between the WM's two draw
        destinations (root canvas via viewport / window buffer) -- 72ms of an 86ms
        frame. Now cached per destination, so a long drag must build ~once, not
        once per frame.

    Counted on the build/read side only, so this net costs nothing when healthy."""
    board.open("picker")
    board.drain(16.0)                    # let the idle prefetch finish the store
    g = board.pyval("ws.wm._wins['make'].ctx.layout.lib_grid")
    assert g is not None, "picker did not open as a window"
    w = board.state()["wins"]["make"]
    ox, oy = w[0] + 1, w[1] + 1 + w[4]
    gx, gy, gw, gh = g
    cy = oy + gy + gh // 2
    board.pyexec("ws.costs.clear()")
    f0 = board.state()["frames"]
    for _ in range(3):
        board.swipe(ox + gx + gw - 40, cy, ox + gx + 60, cy, 30)
    st = board.state()
    costs = st.get("costs") or {}
    painted = st["frames"] - f0
    assert painted > 60, "the drags painted only %d frames" % painted
    assert costs.get("cover.blob.read", 0) == 0, (
        "a drag read %d cover blobs from flash -- the idle prefetch is not "
        "covering the store (costs=%r)" % (costs.get("cover.blob.read"), costs))
    # A handful over ~135 frames is the healthy shape; one per frame is the bug.
    assert costs.get("bar.strip.render", 0) <= 4, (
        "the bar strip rebuilt %d times across %d frames (costs=%r)"
        % (costs.get("bar.strip.render"), painted, costs))


def test_idle_screen_blank_and_wake(board):
    """The #58 power save: the panel blanks after an idle timeout and any input
    wakes it, while the loop, the cart and this very serial channel stay live.

    Retuned to a few seconds for the test, then restored -- the shipped default
    is 5 minutes. Silence is the actual stimulus here: the assertion is that
    NOT talking to the board blanks it, so the wait must send nothing.
    """
    board.cmd("power 3", wait_for="REMOTE power")
    board.drain(0.3)
    board.drain(6.0)                       # say nothing; let the timer expire
    assert board.state()["psave"][0] is True, "the panel never blanked"
    # ...and that state query was serial traffic, which counts as activity, so
    # the panel is already awake again by the following frame.
    board.drain(0.5)
    assert board.state()["psave"][0] is False, "input did not wake the panel"

    # `power off` blanks immediately. It arrives ON the serial channel, which is
    # itself activity -- the explicit blank has to outrank that or it wakes in
    # the same iteration (it did, before _ps_force).
    board.cmd("power off", wait_for="REMOTE power")
    board.drain(1.0)
    assert board.state()["psave"][0] is True, "`power off` did not blank"

    # 0 disables it outright: no blank, however long the silence.
    board.cmd("power 0", wait_for="REMOTE power")
    board.drain(6.0)
    line = board.cmd("power", wait_for="REMOTE power")
    assert "asleep=False" in line, "a disabled timer still blanked: %r" % line
    board.cmd("power 300", wait_for="REMOTE power")     # restore the default
