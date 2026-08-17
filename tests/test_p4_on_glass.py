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


# -- the OTA manifest verifier, on real MicroPython (#53) ---------------------
#
# The P4 has no moy_ota -- the updater is T-Deck-only -- but it is the board
# with a live REPL, and the thing worth proving is language-level, not
# board-level: that the SHIPPED verifier runs under MicroPython at all. The host
# suite proves the maths in CPython, where int(hex, 16) at 512 characters,
# int.to_bytes(256, 'big') and a 2048-bit 3-argument pow are all free. On the
# device each of those is a build-configuration question that was read out of
# mpconfig.h and asserted. This executes them.
#
# The code under test is EXTRACTED from moy_ota.py by ast rather than retyped,
# so a change there is picked up here instead of drifting; the only edits are
# mechanical (dedent the methods, drop `self`).

def _extract_verifier():
    import ast
    import textwrap

    path = ROOT / "device" / "moy_ota.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def const(name):
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
                return ast.get_source_segment(src, node)
        raise AssertionError("constant vanished from moy_ota: " + name)

    def method(name):
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and cls.name == "OtaUpdater":
                for f in cls.body:
                    if isinstance(f, ast.FunctionDef) and f.name == name:
                        out = textwrap.dedent(ast.get_source_segment(src, f))
                        out = out.replace("def %s(self, " % name, "def %s(" % name)
                        return out.replace("self._", "_")
        raise AssertionError("method vanished from moy_ota: " + name)

    return "\n".join([const("OTA_SCHEME"), const("_SHA256_DER"),
                      const("OTA_PUBLIC_KEYS"), method("_canonical"),
                      method("_verify_manifest")])


def _val(board, expr, timeout=30):
    """Evaluate `expr` in the PERSISTENT device namespace.

    The device's `py` handler builds a FRESH env per command, so anything
    pyexec uploaded lives in ws._g and nowhere else -- a bare pyval of a name
    defined up there comes back None (a device NameError), which reads exactly
    like a failed assertion and is not one."""
    return board.pyval("eval(%r, ws._g)" % expr, timeout=timeout)


SIGNED_MANIFEST = {
    "channel": "unstable", "version": 1785665581, "size": 4292512,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "url": "https://example/app.bin", "label": "beta",
}


def test_the_shipped_verifier_runs_on_micropython(board):
    """Signed here rather than pasted from a release: the test has to keep
    working when the key is rotated, and the private half of the real one is a
    GitHub secret that is deliberately not on this machine."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests"))
    from test_ota_signing import TEST_KEYS, sign_with_test_key

    manifest = dict(SIGNED_MANIFEST)
    manifest["sig"] = sign_with_test_key(manifest)

    assert board.pyexec(_extract_verifier(), timeout=90), "verifier upload failed"
    assert board.pyexec(
        "KEYS = %r\nMANIFEST = %r\n" % (TEST_KEYS, manifest), timeout=90)

    # The MicroPython API questions, asked one at a time so a failure names itself.
    assert _val(board, "int(KEYS[0][0], 16).__class__.__name__") == "int"
    assert _val(board, "len(int(KEYS[0][0], 16).to_bytes(256, 'big'))") == 256
    assert _val(board,
        "__import__('hashlib').sha256(b'moybyte').digest()[:4]") == b"\xbd\xd3\xd1\xb4"

    assert _val(board, "_verify_manifest(MANIFEST, KEYS)") is True


def test_verification_is_fast_enough_to_not_think_about(board):
    """Measured 2026-08-02: 35ms modexp, 41ms whole verify. The bound is loose
    on purpose -- it exists to catch an ORDER-of-magnitude regression (a
    software-int fallback, a bigger key), not to police jitter. It runs once per
    update check, behind a screen already waiting on the network."""
    assert board.pyexec(
        "import time\n"
        "t0 = time.ticks_us()\n"
        "for _ in range(5):\n"
        "    _verify_manifest(MANIFEST, KEYS)\n"
        "VERIFY_US = time.ticks_diff(time.ticks_us(), t0) // 5\n", timeout=60)
    us = board.pyval("ws._g['VERIFY_US']")
    print("\nverify_manifest: %dus (%.1fms)" % (us, us / 1000.0))
    assert 0 < us < 500_000, "verify took %dus -- something got much slower" % us


def test_a_tampered_manifest_is_refused_on_the_device(board):
    """Every field the signature covers, refused on real hardware -- and junk in
    the signature refused without raising, because whatever arrives off the wire
    lands straight in int(sig, 16) and pow()."""
    for field, value in (("sha256", "0" * 64), ("version", 999),
                         ("size", 1), ("channel", "stable")):
        got = _val(board, "_verify_manifest(dict(MANIFEST, **{%r: %r}), KEYS)"
                          % (field, value))
        assert got is False, "tampering with %s was accepted" % field

    assert _val(
        board, "_verify_manifest({k: v for k, v in MANIFEST.items() if k != 'sig'}, KEYS)"
    ) is False, "an unsigned manifest verified"

    for junk in ("", "zz", "00", "ff" * 256):
        assert _val(board, "_verify_manifest(dict(MANIFEST, sig=%r), KEYS)"
                           % junk) is False, "junk signature %r accepted" % junk


def test_the_ota_updater_is_live_on_this_board(board):
    """#53 on the P4. The partition table has been OTA-shaped since bring-up and
    update_ui frozen in all along; what was missing was moy_ota itself, the
    staging directory (this board has no SD), the identity stamp and the
    mark_valid call. Verified on glass 2026-08-02 by installing the board's OWN
    running image into the inactive slot: 3,085,216 bytes in 11 steps, reboot
    came up on ota_1 and marked itself valid there.

    Asserted here rather than re-run: a full install is ~90s and leaves the
    board on the other slot, which every test after this one would inherit."""
    assert board.pyval("ws.updater is not None") is True
    assert board.pyval("ws.updater.available()") is True, "not an OTA build"
    assert board.pyval("__import__('moy_ota').BOARD") == "p4"
    assert board.pyval("ws.updater.update_dir") == "/moy/update"
    assert board.pyval("ws.updater.slot()") in ("ota_0", "ota_1")
    # The manifest it would fetch is this board's, not the T-Deck's -- an OTA
    # payload is an app-partition image, so the wrong one cannot boot.
    url = board.pyval("ws.updater.manifest_url('unstable')")
    assert url.endswith("/latest-p4.json"), url
    # mark_valid ran; without it the bootloader reverts every OTA.
    assert any("marked app valid" in ln for ln in board.lines), \
        "no mark_valid at boot -- rollback would undo every update"


def test_the_rollback_confirm_comes_from_the_frame_loop(board):
    """The confirm has to be worth something, and where it is made decides that.

    Made on the boot path it certifies an image that has never drawn a pixel --
    a board that boots to a black screen (this project shipped one, #56) would
    confirm itself and lose the safety net. So it waits for a painted frame AND
    for the loop to keep running afterwards, and this asserts the loop is what
    actually got there on a live board.

    The full pass -- install, reboot into the new slot, and a reset inside the
    confirm window to force the bootloader to revert -- was run on glass
    2026-08-02: 15/15, both directions, banner and verdict correct each time.
    Not re-run here because one install is ~2min and leaves the board on the
    other slot, which every later test would inherit."""
    ota = "__import__('moy_ota')"
    assert board.pyval("ws.updater.confirmed") is True
    loops = board.pyval("ws.updater._loops")
    assert loops >= board.pyval("%s.HEALTHY_LOOPS" % ota), \
        "confirmed after %s loop iterations -- not from the frame loop" % loops
    # A paint threshold above 1 would be unreachable: the console repaints only
    # when something changes, so an untouched desktop paints once and stops.
    assert board.pyval("%s.HEALTHY_PAINTS" % ota) == 1
    assert board.pyval("ws._frames_drawn") >= 1


def test_a_pending_marker_becomes_a_verdict_on_this_board(board):
    """The marker round trip against the real filesystem, without a 3MB install.

    What is device-specific here is exactly what a host test cannot reach: this
    board has no SD, so `with_sd` is a plain call-through and the marker lands on
    the internal VFS -- the same path that has to hold a rollback's evidence
    across a reboot.

    The mkdir is not scaffolding, it is the firmware's own step: `update_dir`
    does NOT exist on a board whose VFS has never taken an OTA, and `finish()`
    and the download opener each create it before their first write. This test
    writes the marker directly, so it has to do the same -- without it the test
    passes only on a board that happens to have downloaded an update earlier,
    and fails on any freshly-flashed one (a full cable flash wipes the VFS).
    That is exactly how it failed on 2026-08-15 after the canvas-flip reflash.

    AND IT HAS TO BE ITS OWN SHORT COMMAND, not a line of the snippet below.
    `pyexec` uploads a multi-line snippet in chunks; `cmd` sends one line. A
    real `os.mkdir` is a flash erase+write that stalls the frame loop for long
    enough that, with PERF diag streaming (the module fixture sends `diag 1`),
    a diag line interleaves into the chunk exchange and the reader parses the
    fragment as a serial COMMAND instead of Python -- surfacing as the baffling
    `PY ERR SyntaxError: invalid syntax for integer with base 10`. It reproduces
    only when the mkdir actually creates something: with the directory already
    present mkdir raises EEXIST immediately, never stalls, and the upload is
    clean. Any device call that blocks on flash belongs outside a chunked
    upload for the same reason."""
    board.cmd("py (lambda: (__import__('os').mkdir(ws.updater.update_dir), 1)[1])()",
              wait_for="PY", timeout=20)     # EEXIST if it is already there
    assert board.pyexec(
        "import json\n"
        "f = open(ws.updater._pending_path(), 'w')\n"
        "f.write(json.dumps({'slot': 'nowhere', 'label': 'v99'}))\n"
        "f.close()\n"
        "V = ws.updater.boot_check()\n"), \
        "the device raised while writing the pending marker"
    verdict = board.pyval("eval('V', ws._g)")
    assert verdict[0] == "rolled_back", verdict
    # Reading it must NOT consume it: an image that reports and then dies has to
    # still have its marker on the boot after the rollback.
    listing = board.pyval("__import__('os').listdir(ws.updater.update_dir)")
    assert "pending.json" in listing, listing
    board.pyexec("import os\n"
                 "os.remove(ws.updater._pending_path())\n"
                 "ws.updater.boot_verdict = None\n"
                 "ws._notice = None\n")
    assert "pending.json" not in board.pyval(
        "__import__('os').listdir(ws.updater.update_dir)")


# -- the browser console baked into the image (moy_web) -----------------------


def test_the_web_console_is_baked_into_this_image(board):
    """The bundle is `.incbin`'d into flash rodata and handed out as a
    memoryview at it, which is the one part of this that no host test can
    reach: whether the linker put the blob where the table says it is.

    Deliberately SELF-CONSISTENT rather than compared against this checkout's
    `dist/` -- the board may legitimately be running an older build, and the
    question here is "does the image's own console read back correctly", not
    "is it today's". The stamp is what answers the second question, by eye.
    """
    stamp = board.pyval("__import__('moy_web').stamp()")
    assert stamp and stamp != "0 0 none", (
        "this image has NO baked web console (stamp %r) -- it was built with "
        "no firmware/web_runner/dist" % (stamp,))
    count, total = int(stamp.split()[0]), int(stamp.split()[1])
    assert count == 4, stamp
    assert total > 400000, "a bundle this small is not the wasm console"
    names = board.pyval("__import__('moy_web').assets()")
    assert set(names) == {"index.html.gz", "worker.js.gz",
                          "micropython.mjs.gz", "micropython.wasm.gz"}, names
    # Each blob read back at its recorded length, starting with the gzip magic.
    # A misplaced symbol still gives plausible lengths -- it is the first bytes
    # that say the pointer landed on the right thing.
    got = board.pyval(
        "[(n, len(__import__('moy_web').asset(n)), "
        "bytes(__import__('moy_web').asset(n)[:3])) "
        "for n in __import__('moy_web').assets()]")
    assert sum(g[1] for g in got) == total, got
    for name, size, magic in got:
        assert magic == b"\x1f\x8b\x08", (name, magic)
        assert size > 0, name


def test_the_console_serves_the_image_when_storage_has_none(board):
    """Storage WINS on purpose (a pushed copy is a human's explicit override),
    so this asserts the fallback the way the handler sees it -- pointed at a
    directory that cannot exist. If the real /moy/web has a pushed copy, that
    is the correct answer for the live host and the reason `start()` prints
    which source it is using."""
    assert board.pyexec(
        "import moy_webhost\n"
        "H = moy_webhost.WebHost('/moy/carts', '/moy/no_such_web')\n"
        "R = H.handle_http('GET', '/micropython.wasm', b'')\n")
    kind = board.pyval("eval('type(R).__name__', ws._g)")
    assert kind == "BlobResponse", kind
    head = board.pyval("eval('R.head()', ws._g)")
    assert b"Content-Encoding: gzip" in head, head
    assert b"200 OK" in head and b"no-store" in head
    note = board.pyval("eval('H.source_note()', ws._g)")
    assert "baked into this firmware" in note, note
