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
tools/p4_autotest.P4Board. This is the only one of the three suites whose
board may be RESET -- its `[serial]` block does not declare `attach_only`,
because the CH343 is an external USB-UART that survives a chip reset. The
handful of checks it shares with the two fullscreen-tier boards live in
tests/on_glass.py; everything below that is windowed-desk or OTA is this
board's alone.
"""

import pytest

import on_glass
from on_glass import ROOT

PORT, pytestmark = on_glass.gate("MOYBYTE_P4_PORT", "the P4")


@pytest.fixture(scope="module")
def board():
    with on_glass.session(
            PORT,
            board_dir=ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b") as b:
        b.cmd("diag 1")            # this suite asserts PERF lines flow
        yield b
        # Leave the board freshly booted on the desk for a human -- the one
        # board this may be done to (see the module docstring).
        b.ser.write(b"\r\x03")
        b.drain(0.5)
        b.ser.write(b"\x04")
        b.drain(1.0)


def test_boots_to_the_desk(board):
    st = board.state()
    assert st.get("desk") is True
    assert not st.get("order")


def test_wifi_status_is_readable(board):
    on_glass.wifi_status_is_readable(board)


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
    on_glass.every_app_claims_one_cart(board)


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
    """One format on every board (#206 item 2), plus the columns only this one
    can fill: the windowed WM's pass split and the async-PPA overlap counters.
    The module fixture sends `diag 1`, so the wm columns are measured here --
    with the meters off they read `-`, which is a different answer from 0."""
    got = on_glass.perf_line_is_the_one_format(board)
    for name in ("wmr", "wmw", "wms"):
        assert got[name] is not None, (name, got)
    assert isinstance(got["ppa"], tuple) and len(got["ppa"]) == 5, got
    assert got["ppa"][4] == 0, "PPA timeouts must stay 0: %r" % (got["ppa"],)


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

    The shared body is the blank/wake/`power off` trio every board runs; what
    is this board's is the tail -- 0 disables the timer outright, which the S3
    suites do not pin."""
    on_glass.idle_blank_and_wake(board)

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
#
# NAMING THE PIECES BY HAND WAS THE DRIFT (2026-08-27). `607ba35` promoted the
# PKCS#1 block compare out of the class into a module-level `verify_sig` so the
# C6 image's second signature could share it, and this extractor -- which knew
# about class methods and module-level ASSIGNMENTS -- kept uploading a
# `_verify_manifest` whose body had left the building. The board said
# `NameError: name 'verify_sig' isn't defined` once per command; the harness
# discarded the text and every assertion downstream read `assert None is False`.
# So the closure is now DERIVED: whatever the extracted code still references,
# the extractor goes and fetches, and what it cannot fetch it names, here, in
# milliseconds, instead of on the wire as an absence.

def _free_names(snippet):
    """Names `snippet` READS that nothing in it (or in builtins) defines."""
    import ast
    import builtins

    def params(args):
        got = {a.arg for a in list(args.posonlyargs) + list(args.args)
               + list(args.kwonlyargs)}
        return got | {a.arg for a in (args.vararg, args.kwarg) if a}

    tree = ast.parse(snippet)
    top = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            top.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            top.add(node.name)

    free = set()
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        # Bindings first, reads second: ast.walk does not visit a store before
        # the load it feeds, so a one-pass check would flag ordinary locals.
        local = params(fn.args)
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                local.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                local.update((a.asname or a.name).split(".")[0]
                             for a in node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                local.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.Lambda)):
                local |= params(node.args)
        free.update(n.id for n in ast.walk(fn)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id not in local and n.id not in top)
    return free


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

    def top_level(name):
        """A module-level def or assignment the extracted code needs, or None
        (a stdlib name, or something genuinely missing -- the caller says so)."""
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(src, node)
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == name for t in node.targets):
                return ast.get_source_segment(src, node)
        return None

    parts = [const("OTA_SCHEME"), const("_SHA256_DER"), const("OTA_PUBLIC_KEYS"),
             method("_canonical"), method("_verify_manifest")]
    have = {"OTA_SCHEME", "_SHA256_DER", "OTA_PUBLIC_KEYS",
            "_canonical", "_verify_manifest"}
    for _ in range(8):                      # a chain, not a single hop
        missing = sorted(_free_names("\n".join(parts)) - have)
        if not missing:
            break
        for name in missing:
            got = top_level(name)
            assert got is not None, (
                "the extracted verifier calls %r, which moy_ota does not define "
                "at module level -- the device would answer NameError and the "
                "test would read it as a missing value" % name)
            parts.append(got)
            have.add(name)
    snippet = "\n".join(parts)
    assert not _free_names(snippet), (
        "the extracted verifier does not close over its own names: %s"
        % sorted(_free_names(snippet)))
    return snippet


def _val(board, expr, timeout=30):
    """Evaluate `expr` in the PERSISTENT device namespace.

    The device's `py` handler builds a FRESH env per command, so anything
    pyexec uploaded lives in ws._g and nowhere else -- a bare pyval of a name
    defined up there comes back None (a device NameError), which reads exactly
    like a failed assertion and is not one. strict=True is what makes that
    distinction: a device exception arrives as DeviceError carrying the board's
    own words, never as a value the caller then asserts against."""
    return board.pyval("eval(%r, ws._g)" % expr, timeout=timeout, strict=True)


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

    assert board.pyexec(_extract_verifier(), timeout=90), board.last_error
    assert board.pyexec(
        "KEYS = %r\nMANIFEST = %r\n" % (TEST_KEYS, manifest),
        timeout=90), board.last_error

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
        "VERIFY_US = time.ticks_diff(time.ticks_us(), t0) // 5\n",
        timeout=60), board.last_error
    us = board.pyval("ws._g['VERIFY_US']", strict=True)
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
    # Self-consistent like the rest of this test: the baked set must be the
    # image's OWN serve allowlist (moy_webhost.ASSETS), not a count restated
    # here -- a hand-pinned 4 went stale the day moy_store.mjs joined the set.
    declared = board.pyval("sorted(__import__('moy_webhost').ASSETS)")
    assert count == len(declared), (stamp, declared)
    assert total > 400000, "a bundle this small is not the wasm console"
    names = board.pyval("__import__('moy_web').assets()")
    assert set(names) == {n + ".gz" for n in declared}, (names, declared)
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


def test_a_cart_runs_and_exits(board):
    """The 2026-08-17 blind-spot closer, same as on_glass.cart_runs_and_exits:
    a staged regression once broke every cart start while this suite stayed
    green, because nothing here ran one. Launch, assert it started clean, exit.

    NOT the shared body, deliberately. tools/p4_perf.py's idiom, exactly:
    ws.exit() first to clear whatever the tour above left open (Settings + the
    picker windows -- a `run` from that state opens the cart under the picker's
    project arrangement, where the first draft's cart-quit exit did not pop),
    then run, then ws.exit() out. The shared body keeps the cart-quit path, so
    the kid-facing quit() flag stays pinned on the two boards that reach the
    launcher from a bare stack while this one pins the launch itself."""
    for _ in range(3):                       # close settings/picker leftovers
        board.cmd("py ws.exit()", wait_for="PY")
        board.drain(0.5)
    line = board.cmd("run star", wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.5)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the cart is not ticking"
    board.cmd("py ws.exit()", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "exit did not end the run: %r" % st
