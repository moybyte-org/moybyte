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

Env-gated like its sync sibling, and it shares that sibling's prerequisite
ladder (`tests/web_e2e.py`): a missing toolchain SKIPS on a bench and FAILS
under CI, where a suite that asks to run and then skips proves nothing.
~4 Chrome boots, ~90s.
"""

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER, ROOT

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~90s)")


def _serve(port, extra=()):
    """serve.py on `port`, waited until it answers."""
    if "--carts" in extra:
        web_e2e.warm_host_library()
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
    web_e2e.require("store")
    port = web_e2e.free_port()             # fixed: OPFS is scoped to the ORIGIN
    profile = tmp_path / "chrome"          # fixed: OPFS lives in the profile
    server, base = _serve(port)
    try:
        out, js = _run("persist_create", base, profile, tmp_path / "s1")
        assert len(js) >= 5, "no js steps reported:\n%s" % out[-2000:]
        mode_said, _asked, shelf, batches, detail = js[:5]
        assert "saved in this browser" in mode_said, \
            "a static host must land in site mode, said: %r\n%s" % (mode_said, out[-2000:])
        assert "local.new_cart.moy" in shelf, "the cart was never created:\n%s" % out[-3000:]
        assert int(batches) >= 1, \
            "nothing was ever written to the local store (%s ops batches):\n%s" % (
                batches, out[-3000:])
        print("\npersist create: %s batches, last %s" % (batches, detail))

        # THE CLAIM: a second load, no server-side state, same browser.
        out2, js2 = _run("persist_reload", base, profile, tmp_path / "s2")
        assert "saved in this browser" in js2[0], js2[0]
        assert "local.new_cart.moy" in js2[1], \
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
    web_e2e.require("store")
    port = web_e2e.free_port()
    profile = tmp_path / "chrome"
    server, base = _serve(port)
    try:
        _run("persist_create", base, profile, tmp_path / "s1")
        out, js = _run("persist_zip", base, profile, tmp_path / "s2")
        assert len(js) >= 7, out[-3000:]
        zipline, imported, shelf = js[2], js[4], js[6]
        assert zipline.startswith("local.new_cart.moy.zip "), \
            "export produced nothing: %r\n%s" % (zipline, out[-3000:])
        assert int(zipline.split()[1]) > 200, "suspiciously small zip: %s" % zipline
        assert "imported local.new_cart_2.moy" in imported, \
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


def _tagged(js, tag):
    """The js step result that starts with `tag`, past the tag (its quotes
    unescaped: `_run` keeps the step's JSON text)."""
    for j in js:
        if j.startswith(tag + " "):
            return j[len(tag) + 1:].replace('\\"', '"')
    raise AssertionError("no %s step in %r" % (tag, js))


def _host_theme(tmp_path):
    """A theme package named on the host through NAME… (`sunset`, from the
    `sprig` fixture, whose sheets it carries, with one accent), zipped as the
    store archives a cart: (zip path, its resolved rows and sheets as
    `Appearance.package_rows` digests them)."""
    sys.path.insert(0, str(ROOT / "tests"))
    from ws_helpers import build_ws
    from runtime import moy_carts, ui_draw
    ws = build_ws(tmp_path / "host")
    shutil.copytree(ROOT / "tests" / "fixtures" / "themes" / "sprig.moy",
                    os.path.join(ws.carts_root, "sprig.moy"))
    ws.carts.rescan()
    ws.look.set_theme("sprig")
    assert ws.look.change("accent", ui_draw.theme_accents()[4][1]) == 0
    ws.look.tick(0.0)
    assert ws.look.name_custom("sunset") is None
    folder = os.path.join(ws.carts_root, "sunset.moy")
    assert os.path.isfile(os.path.join(folder, "parts.moygfx"))
    arc = str(tmp_path / "sunset.moy.zip")
    moy_carts.pack(folder, arc)
    return arc, ws.look.package_rows()["sunset"]


def test_a_theme_exports_and_imports_as_a_zip(tmp_path, monkeypatch):
    """A theme package travels as a cart does (docs/theming_2026-09.md §9
    phase 6 step 8), but lands under its own name: the loader takes a theme
    only from `<name>.moy`. Into a fresh profile it keeps its name and
    resolves to the rows it had, its sheets byte for byte; into the same
    profile it takes the `_2` stem with theme.json's `name` rewritten,
    installed and selectable."""
    import base64
    import json
    web_e2e.require("store")
    arc, rows = _host_theme(tmp_path)
    port = web_e2e.free_port()
    server, base = _serve(port)
    try:
        monkeypatch.setenv("MOY_THEME_ZIP", arc)
        out, js = _run("persist_theme_zip", base, tmp_path / "chrome", tmp_path / "s1")
        assert "imported sunset.moy" in _tagged(js, "IMPORTED"), out[-3000:]
        st = json.loads(_tagged(js, "STATE"))
        assert st["packages"]["sunset"] == rows, (st, rows)
        assert "imported sunset_2.moy" in _tagged(js, "IMPORTED2"), out[-3000:]
        st2 = json.loads(_tagged(js, "STATE2"))
        assert st2["packages"]["sunset"] == rows
        assert st2["packages"]["sunset_2"] == rows, \
            "the _2 copy did not resolve as the theme it is: %r" % st2
        name, b64 = _tagged(js, "ZIP").split(" ", 1)
        assert name == "sunset.moy.zip", name
        exported = tmp_path / name
        exported.write_bytes(base64.b64decode(b64))

        monkeypatch.setenv("MOY_THEME_ZIP", str(exported))
        out, js = _run("persist_theme_fresh", base, tmp_path / "chrome2", tmp_path / "s2")
        assert "imported sunset.moy" in _tagged(js, "IMPORTED"), out[-3000:]
        st = json.loads(_tagged(js, "STATE"))
        assert st["packages"] == {"sunset": rows}, (st, rows)
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_theme_imports_under_its_own_name_byte_for_byte(tmp_path, monkeypatch):
    """A theme that lands under the name its theme.json already says keeps
    that file as it came (moy_carts.import_archive's rule): the `dusk`
    fixture's theme.json, trailing newline and all, exported back out of
    the store identical."""
    import base64
    import io
    import zipfile
    web_e2e.require("store")
    sys.path.insert(0, str(ROOT))
    from runtime import moy_carts
    src = ROOT / "tests" / "fixtures" / "themes" / "dusk.moy"
    arc = str(tmp_path / "dusk.moy.zip")
    moy_carts.pack(str(src), arc)
    port = web_e2e.free_port()
    server, base = _serve(port)
    try:
        monkeypatch.setenv("MOY_THEME_ZIP", arc)
        out, js = _run("persist_theme_fresh", base, tmp_path / "chrome", tmp_path / "s1")
        assert "imported dusk.moy" in _tagged(js, "IMPORTED"), out[-3000:]
        name, b64 = _tagged(js, "ZIP").split(" ", 1)
        assert name == "dusk.moy.zip", name
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))) as z:
            got = z.read("dusk.moy/theme.json")
        assert got == (src / "theme.json").read_bytes(), \
            "theme.json was rewritten on the way in (%d bytes, was %d)" % (
                len(got), (src / "theme.json").stat().st_size)
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_name_on_the_desk_survives_a_phone_keyboards_enter(tmp_path):
    """Appearance → NAME… on the desk, the name typed and ENTERed the way a
    phone's on-screen keyboard delivers them (#kbin's input event, then its
    keydown): `custom` becomes the theme and the console keeps running. The
    ENTER is the last event before the next draw, which then drew a focus
    on NAME…'s row after the row had gone (an IndexError, the page's
    "console crash")."""
    import json
    web_e2e.require("store")
    port = web_e2e.free_port()
    server, base = _serve(port)
    try:
        out, js = _run("appearance_name", base, tmp_path / "chrome", tmp_path / "s1")
        assert "Traceback" not in out, out[-3000:]
        assert js[-2] == "live", "the console stopped: %r\n%s" % (js[-2], out[-3000:])
        assert json.loads(_tagged(js, "THEME"))["name"] == "nikola", out[-3000:]
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_board_served_page_keeps_nothing_locally(tmp_path):
    """The other half of a total split: serve.py --carts answers GET /sync, so
    the page must stay in BOARD mode and open no local store.

    What this does NOT assert any more is that the import/export row is hidden.
    It used to, and that conflated two different things: the owner call is that
    a board-served page has ONE store -- the board's -- not that carts may not
    travel. The row is shown in every mode since 2026-08-29 (a board-served
    import rides the same sync push every edit already takes). The invariant
    that matters is the one below: OPFS is never opened here."""
    web_e2e.require("store")
    store = tmp_path / "store"
    store.mkdir()
    for cart in ("moybyte.star_catcher.moy", "moybyte.sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)
    port = web_e2e.free_port()
    server, base = _serve(port, ("--carts", str(store)))
    try:
        out, js = _run("persist_board_mode", base, tmp_path / "chrome", tmp_path / "s1")
        assert len(js) >= 2, out[-2000:]
        display, _, said = js[0].partition("|")
        assert display.strip() == "flex", \
            "the import/export row must be offered on a board too: %r" % display
        assert "kept on the console" in said, said
        # No local store was opened, seeded or read: the detail is empty because
        # board mode never touches OPFS at all.
        assert js[1] == "board|", \
            "a board-served page touched the browser store: %r\n%s" % (js[1], out[-2000:])
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_crash_is_readable_on_a_phone(tmp_path):
    """A phone has no devtools, so "console crash (see devtools)" alone is a
    report nobody can act on (the owner's iPhone, 2026-10-16). The worker's
    error -- a Python traceback -- is printed on the page itself, under the
    title, in a phone-shaped handheld page where a person can read and copy
    it. The error is posted the way worker.js posts it ({t:"error", s, msg}).

    The second post is the iPhone's own report: an abort while instantiating
    the wasm, whose JSC stack is the frames alone. Its message -- the abort's
    reason -- is the line that has to be on the screen, above the frames."""
    import json
    web_e2e.require("store")
    port = web_e2e.free_port()
    server, base = _serve(port)
    tb = ("Traceback (most recent call last):\n"
          '  File "launcher_layer.py", line 1, in draw\n'
          "IndexError: list index out of range")
    abort = ("RuntimeError: Aborted(RangeError: Out of memory). "
             "Build with -sASSERTIONS for more info.")
    frames = ("abort@http://192.168.1.208:8321/micropython.mjs:1:3440\n"
              "instantiateArrayBuffer@http://192.168.1.208:8321/"
              "micropython.mjs:1:4172")
    read = {"js": "(function(){var c=document.getElementById('crash');"
                  "var r=c.getBoundingClientRect();return JSON.stringify("
                  "{shown:getComputedStyle(c).display,text:c.textContent,"
                  "top:r.top,h:r.height,vh:innerHeight,crash:window.__moyCrash,"
                  "status:document.getElementById('s').textContent});})()"}
    try:
        out, js = web_e2e.run(
            tmp_path, "crash_readable", "?handheld=1",
            [{"js": "(onWorker({t:'error',s:%s,msg:%s}),1)"
                    % (json.dumps(tb), json.dumps(tb))}, read,
             {"js": "(onWorker({t:'error',s:%s,msg:%s}),1)"
                    % (json.dumps(abort + "\n" + frames), json.dumps(abort))},
             read],
            base, tmp_path / "chrome", boot_ms=30000,
            device={"width": 390, "height": 844, "dpr": 3, "mobile": True})
        got = json.loads(js[-3])
        assert got["status"] == "console crash (see devtools)", got
        assert got["shown"] == "block", got
        assert got["text"].endswith("IndexError: list index out of range"), got
        assert got["h"] > 0 and 0 <= got["top"] < got["vh"], \
            "the crash text is off the phone's first screen: %r" % got
        got = json.loads(js[-1])
        assert got["text"].split("\n") == [abort] + frames.split("\n"), got
        assert got["crash"] == abort + "\n" + frames, got
        assert got["h"] > 0 and 0 <= got["top"] < got["vh"], got
    finally:
        server.terminate()
        server.wait(timeout=10)
