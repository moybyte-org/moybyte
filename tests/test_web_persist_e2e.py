"""Mode-1 browser persistence in a REAL browser, against no hardware (#193).

The claim this suite exists to prove is the one nothing else can: a cart made
in a browser is still there after the tab is closed. That needs two page loads
sharing one browser profile, because the store lives in OPFS and OPFS lives in
the profile -- so these tests pin the Chrome profile (MOY_PROFILE) AND the port
(OPFS is per-ORIGIN, and browsershot's default port moves every run).

It also pins the half that is easy to get wrong in the other direction: a page
served by a BOARD TWIN (serve.py --carts, which answers GET /sync) must stay in
board mode and keep nothing locally. The two modes are total (owner call
2026-08-25) and a regression either way is silent -- carts written to a store
nobody reads.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_persist_e2e.py

Env-gated like its sync sibling: ~4 Chrome boots, ~90s.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "firmware" / "web_runner"
DIST = RUNNER / "dist"
CHROME = os.environ.get("MOY_CHROME", "google-chrome")

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~90s)")


def _skip_unless_ready():
    if shutil.which(CHROME) is None:
        pytest.skip("no %s on PATH (MOY_CHROME overrides)" % CHROME)
    if shutil.which("node") is None:
        pytest.skip("no node on PATH")
    if not (DIST / "index.html").exists():
        pytest.skip("no built dist/ -- run firmware/web_runner/build.sh")
    if not (DIST / "moy_store.mjs").exists():
        pytest.skip("dist/ predates the browser store -- rebuild web_runner")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(port, extra=()):
    """serve.py on `port`, waited until it answers."""
    p = subprocess.Popen(
        [sys.executable, "serve.py", str(port), "dist", *extra],
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


def _run(scenario, base, profile, outdir):
    """One browsershot run -> (stdout, [js step results])."""
    env = dict(os.environ, MOY_BASE=base, MOY_PROFILE=str(profile))
    r = subprocess.run(
        ["node", "browsershot.mjs", "scenarios/%s.json" % scenario, str(outdir)],
        cwd=RUNNER, env=env, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, "browsershot %s failed:\n%s\n%s" % (
        scenario, r.stdout[-3000:], r.stderr[-500:])
    js = re.findall(r"js -> (.*)$", r.stdout, re.M)
    return r.stdout, [j.strip().strip('"') for j in js]


def test_a_cart_made_in_the_browser_survives_a_reload(tmp_path):
    """#193's done-when, in one run: make a cart on a STATIC host, close the
    page, open it again in the same browser, and it is still on the shelf."""
    _skip_unless_ready()
    port = _free_port()                    # fixed: OPFS is scoped to the ORIGIN
    profile = tmp_path / "chrome"          # fixed: OPFS lives in the profile
    server, base = _serve(port)
    try:
        out, js = _run("persist_create", base, profile, tmp_path / "s1")
        assert len(js) >= 5, "no js steps reported:\n%s" % out[-2000:]
        mode_said, _asked, shelf, batches, detail = js[:5]
        assert "saved in this browser" in mode_said, \
            "a static host must land in site mode, said: %r\n%s" % (mode_said, out[-2000:])
        assert "new_cart.moy" in shelf, "the cart was never created:\n%s" % out[-3000:]
        assert int(batches) >= 1, \
            "nothing was ever written to the local store (%s ops batches):\n%s" % (
                batches, out[-3000:])
        print("\npersist create: %s batches, last %s" % (batches, detail))

        # THE CLAIM: a second load, no server-side state, same browser.
        out2, js2 = _run("persist_reload", base, profile, tmp_path / "s2")
        assert "saved in this browser" in js2[0], js2[0]
        assert "new_cart.moy" in js2[1], \
            "the cart did NOT survive the reload:\n%s" % out2[-3000:]
        # ...and it came from the local store, not from the served carts.json.
        assert js2[2].startswith("loaded "), \
            "the second load did not read the local store: %r\n%s" % (js2[2], out2[-3000:])
        print("persist reload: %s" % js2[2])
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_cart_exports_and_imports_as_a_zip(tmp_path):
    """The no-account escape hatch: zip a cart out of the live VFS, feed the
    same bytes back, and it lands under the store's own duplicate name."""
    _skip_unless_ready()
    port = _free_port()
    profile = tmp_path / "chrome"
    server, base = _serve(port)
    try:
        _run("persist_create", base, profile, tmp_path / "s1")
        out, js = _run("persist_zip", base, profile, tmp_path / "s2")
        assert len(js) >= 7, out[-3000:]
        zipline, imported, shelf = js[2], js[4], js[6]
        assert zipline.startswith("new_cart.moy.zip "), \
            "export produced nothing: %r\n%s" % (zipline, out[-3000:])
        assert int(zipline.split()[1]) > 200, "suspiciously small zip: %s" % zipline
        assert "imported new_cart_2.moy" in imported, \
            "the re-import did not take the duplicate name: %r\n%s" % (imported, out[-3000:])
        assert "new_cart_2.moy" in shelf, out[-2000:]
        print("\nzip round trip: %s -> %s" % (zipline, imported))

        # The imported cart is a COMMIT like any other: it must persist too.
        out2, js2 = _run("persist_reload", base, profile, tmp_path / "s3")
        assert "new_cart_2.moy" in js2[1], \
            "the imported cart did not survive a reload:\n%s" % out2[-3000:]
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_board_served_page_keeps_nothing_locally(tmp_path):
    """The other half of a total split: serve.py --carts answers GET /sync, so
    the page must stay in BOARD mode -- no local store, and no export row that
    would invite one."""
    _skip_unless_ready()
    store = tmp_path / "store"
    store.mkdir()
    for cart in ("star_catcher.moy", "sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)
    port = _free_port()
    server, base = _serve(port, ("--carts", str(store)))
    try:
        out, js = _run("persist_board_mode", base, tmp_path / "chrome", tmp_path / "s1")
        assert len(js) >= 2, out[-2000:]
        display, _, said = js[0].partition("|")
        assert display.strip() == "", \
            "the browser-store row must stay hidden on a board: %r" % display
        assert "kept on the console" in said, said
        # No local store was opened, seeded or read: the detail is empty because
        # board mode never touches OPFS at all.
        assert js[1] == "board|", \
            "a board-served page touched the browser store: %r\n%s" % (js[1], out[-2000:])
    finally:
        server.terminate()
        server.wait(timeout=10)
