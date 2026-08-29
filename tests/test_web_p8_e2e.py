"""Dropping a PICO-8 cart on the hosted console, in a REAL browser (#194).

The claim: a `.p8` and a `.p8.png` both convert, run, and open in the editor
from a drop, with no checkout and no CLI. Nothing else in the tree can prove
that -- `tests/test_import_p8.py` proves the conversion on CPython, and the
whole point of this feature is that the SAME conversion happens inside the wasm
VM, driven by the page.

"RUNS" IS CHECKED ON THE CANVAS (2026-08-29). It used to be checked by asking
the console which cart was playing, which a cart that loads and then does
nothing satisfies perfectly -- and for a while that was the honest ceiling,
because the drop emitted a Python stub whose whole job was to look like
something. It emits the cart's own code now, so the check counts PIXELS: the
fixture paints an 80x80 block in p8 colour 14 from its own `_draw`, and the
scenario counts that colour before the drop as well as after, so the assertion
rests on a measured baseline instead of a belief about what the shell paints.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_p8_e2e.py

Env-gated like its sync and persistence siblings, and it shares their
prerequisite ladder (`tests/web_e2e.py`): a missing toolchain SKIPS on a bench
and FAILS under CI, because a job that asks for a browser suite and then skips
every check is a green tick over nothing. ~2 Chrome boots, ~60s.

THE FIXTURE IS OURS. `tests/fixtures/tiny_dash.p8` is a cart we wrote and the
`.p8.png` is BUILT from it here (tests/p8_fixture.py) -- a real BBS-shaped
steganographic image, all five PNG scanline filters, generated fresh so it
cannot go stale. The interesting real carts are all licensed
(`ports/celeste.moy` is gitignored on both repos under CC BY-NC-SA) and a
fixture that cannot be committed is a check that only runs on one laptop.
"""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~60s)")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(port):
    p = subprocess.Popen(
        [sys.executable, "serve.py", str(port), "dist"],
        cwd=RUNNER, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/index.html", timeout=1).read(64)
            return p, base
        except OSError:
            if p.poll() is not None:
                pytest.fail("serve.py died on startup")
            time.sleep(0.1)
    p.terminate()
    pytest.fail("serve.py never answered")


def _run(base, profile, outdir, drop):
    """One browsershot run of the p8 drop scenario -> (stdout, [js results])."""
    env = dict(os.environ, MOY_BASE=base, MOY_PROFILE=str(profile),
               MOY_DROP=str(drop))
    r = subprocess.run(
        ["node", "browsershot.mjs", "scenarios/p8_import.json", str(outdir)],
        cwd=RUNNER, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, "browsershot p8_import failed:\n%s\n%s" % (
        r.stdout[-4000:], r.stderr[-500:])
    js = re.findall(r"js -> (.*)$", r.stdout, re.M)
    return r.stdout, [j.strip().strip('"') for j in js]


def _fixtures(tmp_path):
    import import_p8
    import p8_fixture
    return p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)


def _pink(step):
    """The pink-pixel count out of one scenario probe (`pink N`)."""
    assert step.startswith("pink "), "the pixel probe did not run: %r" % step
    assert step != "pink ?", step
    return int(step.split(None, 1)[1])


def _check(out, js, form):
    """The same assertions for both cart forms -- they are one feature."""
    assert len(js) >= 11, "not enough js steps:\n%s" % out[-3000:]
    (mode, pink_before, sent, imported, report, panel, playing, pink_after,
     edit_btn, _clicked, edited) = js[:11]
    assert "saved in this browser" in mode, \
        "%s: a static host must land in site mode, said %r" % (form, mode)
    assert sent.endswith(form), sent

    # 1. it converted and landed in the store
    assert "imported tiny_dash.moy" in imported, \
        "%s: the drop did not import: %r\n%s" % (form, imported, out[-3000:])

    # 2. the compatibility report is REAL prose about THIS cart (#194: report,
    #    don't crash) -- the same lines the CLI prints, written once.
    assert "tiny dash" in report, report
    assert "imported." in report, \
        "%s: the report must name the cart it imported: %r" % (form, report)
    assert "cart's own Lua" in report, \
        "%s: the report must say the code is Lua: %r" % (form, report)
    assert "CODE did NOT" not in report, \
        "%s: that headline was inverted on 2026-08-29: %r" % (form, report)
    assert "sspr" in report, \
        "%s: the report must name the verbs that differ: %r" % (form, report)
    assert panel.startswith("block|"), \
        "%s: the report card never showed: %r" % (form, panel)
    assert panel.endswith("|"), "a successful import must not paint as an error"

    # 3. IT RUNS -- the console's own assets payload names the cart playing...
    assert playing.startswith("tiny dash "), \
        "%s: the imported cart is not the one running: %r\n%s" % (
            form, playing, out[-3000:])
    # ...and, the check that a stub cart could not pass, the cart's OWN _draw
    # is painting. 80x80 p8 pixels composited at whatever integer scale this
    # viewport gives is thousands either way; the baseline says what the page
    # painted in that colour without the cart.
    before, after = _pink(pink_before), _pink(pink_after)
    assert after - before > 2000, (
        "%s: the imported cart's own _draw is not painting -- pink pixels went "
        "%d -> %d. It loaded (the console names it above) and then drew "
        "nothing, which is what a stub, a dead shim or an unwired lifecycle "
        "all look like.\n%s" % (form, before, after, out[-3000:]))

    # 4. ...and OPEN IN EDITOR lands in the editor, on the imported cart.
    assert edit_btn.endswith("|open in editor"), edit_btn
    assert edited.startswith("editing tiny dash"), \
        "%s: open-in-editor did not open: %r\n%s" % (form, edited, out[-3000:])
    assert "(menu/" in edited, \
        "%s: the editor screen is not up: %r" % (form, edited)

    # 5. ...on the imported cart's OWN assets, in the asset tabs -- #194's
    #    done-when names Sprites / Map / Music by name, and an editor that
    #    opened on the code alone would satisfy every check above.
    sprites, music = js[12], js[14]
    assert sprites == "editing tiny dash (menu/paint)", sprites
    assert music == "editing tiny dash (menu/music)", music


@pytest.mark.parametrize("form", ["p8", "p8.png"])
def test_a_dropped_pico8_cart_converts_runs_and_opens_in_the_editor(
        tmp_path, form):
    """#194's done-when, both cart forms, in a browser with no checkout."""
    web_e2e.require("store", "p8")
    p8, png = _fixtures(tmp_path)
    drop = p8 if form == "p8" else png
    port = _free_port()
    server, base = _serve(port)
    try:
        out, js = _run(base, tmp_path / "chrome", tmp_path / "s", drop)
        _check(out, js, form)
        shelf = js[-1]
        assert "tiny_dash.moy" in shelf, \
            "the imported cart is not on the shelf: %r\n%s" % (shelf, out[-2000:])
        print("\n%s -> %s" % (form, js[3]))
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_file_that_is_not_a_cart_is_reported_not_crashed(tmp_path):
    """The other half of "report, don't crash": a `.p8` that is not a cart has
    to come back as a sentence, with the console still running."""
    web_e2e.require("store", "p8")
    junk = tmp_path / "notacart.p8"
    junk.write_text("this is a readme, not a cartridge\n", encoding="utf-8")
    port = _free_port()
    server, base = _serve(port)
    try:
        out, js = _run(base, tmp_path / "chrome", tmp_path / "s", junk)
        assert len(js) >= 11, out[-3000:]
        imported, report, panel = js[3], js[4], js[5]
        assert "could not import" in imported, imported
        assert "no PICO-8 sections" in report, report
        assert panel == "block|bad", \
            "a refused import must paint as an error: %r" % panel
        # ...and the console is still alive: the shelf still answers.
        assert "star_catcher.moy" in js[-1] or js[-1], js[-1]
        assert "console crash" not in out, out[-2000:]
    finally:
        server.terminate()
        server.wait(timeout=10)
