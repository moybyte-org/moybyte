"""A board-served page's Settings and badges live on the BOARD, in a real
browser, against a board twin (serve.py --carts).

The page seeds its console from the board's GET /console.json and sends its
changes back as system batches; the twin merges them through the same bodies a
board's VM runs (runtime/moy_sync.py's SystemReceiver). So a theme named
through Appearance and a badge earned by opening a cart land in the twin's
system.json and achievements.json beside its carts, and the next page load --
a fresh profile, since board mode keeps nothing in the browser -- comes back
on them. The keys the board keeps for itself never reach the page and are
never moved. A twin flashed before the root (--no-system) is sent no system
batch at all.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_board_system_e2e.py

~3 Chrome boots a test.
"""

import json
import os
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
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

# What the board keeps for itself before the page ever opens.
BOARD_ROWS = {"web_pin": "4821", "unknown_sources": False, "volume": 2,
              "ota_channel": "stable"}


def _twin(tmp_path, port, *extra):
    """serve.py --carts over a fresh board store; (process, base, log path)."""
    web_e2e.warm_host_library()
    top = tmp_path / "board"
    store = top / "carts"
    store.mkdir(parents=True)
    # A board's whole seed set: the apps (Appearance among them) ride it.
    for cart in sorted((ROOT / "system_carts").glob("*.moy")):
        shutil.copytree(cart, store / cart.name)
    (top / "system.json").write_text(json.dumps(BOARD_ROWS))
    log = tmp_path / "twin.log"
    p = subprocess.Popen(
        [sys.executable, "serve.py", str(port), "dist", "--carts", str(store),
         *extra],
        cwd=RUNNER, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/index.html", timeout=1).read(64)
            return p, base, top, log
        except OSError:
            if p.poll() is not None:
                pytest.fail("serve.py died on startup:\n" + log.read_text())
            time.sleep(0.1)
    p.terminate()
    pytest.fail("serve.py never answered")


def _scenario(name):
    return json.loads((RUNNER / "scenarios" / ("%s.json" % name)).read_text())


def _play(tmp_path, name, base, steps=None, query=None):
    sc = _scenario(name) if steps is None else {"query": query, "steps": steps}
    return web_e2e.run(tmp_path, name, sc["query"], sc["steps"], base,
                       tmp_path / ("chrome_" + name), boot_ms=30000)


def _tagged(js, tag):
    for j in js:
        if isinstance(j, str) and j.startswith(tag + " "):
            return json.loads(j[len(tag) + 1:])
    raise AssertionError("no %s step in %r" % (tag, js))


RELOAD = [
    {"note": "a fresh browser: everything comes from the board", "wait": 3000},
    {"js": "window.__moyState().then(s => 'RELOADED ' + JSON.stringify("
           "{theme: s.theme.name, ach: s.ach, keys: Object.keys(s.system),"
           " mru: s.system.desk_mru || []}))"},
]


def test_the_theme_and_badges_a_page_picks_live_on_the_board(tmp_path):
    web_e2e.require("store")
    server, base, top, log = _twin(tmp_path, web_e2e.free_port())
    try:
        out, js = _play(tmp_path, "appearance_name", base)
        assert _tagged(js, "THEME")["name"] == "nikola", out[-3000:]
        out, js = _play(tmp_path, "persist_open", base)
        opened = _tagged(js, "OPENED")
        assert opened["theme"] == "nikola" and "first_open" in opened["ach"], \
            "%r\n%s" % (opened, out[-3000:])

        # THE BOARD has them, beside its carts, as a board keeps them...
        rows = json.loads((top / "system.json").read_text())
        assert rows["theme"] == "nikola", (rows, log.read_text()[-2000:])
        assert "first_open" in json.loads(
            (top / "achievements.json").read_text())["found"]
        # ...the run under the board's own carts root, not the page's...
        star = str(top / "carts" / "moybyte.star_catcher.moy")
        assert star in rows.get("desk_mru", []), rows
        # ...and what it keeps for itself unmoved.
        for key, value in BOARD_ROWS.items():
            assert rows[key] == value, (key, rows)

        # A fresh browser comes back on them: the board was the store.
        out, js = _play(tmp_path, "board_reload", base, RELOAD, "?dev=1")
        back = _tagged(js, "RELOADED")
        assert back["theme"] == "nikola", "%r\n%s" % (back, out[-3000:])
        assert "first_open" in back["ach"], back
        assert "/moy/carts/moybyte.star_catcher.moy" in back["mru"], back
        for key in BOARD_ROWS:
            assert key not in back["keys"], \
                "the page was sent the board's %s" % key
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_board_without_the_system_root_is_sent_no_system_batch(tmp_path):
    """Firmware from before the root 404s /console.json: the page seeds no
    system root, watches none, and its Settings and badges stay in the tab --
    nothing is aimed at a board that could only refuse it, and its carts
    still sync."""
    web_e2e.require("store")
    server, base, top, log = _twin(tmp_path, web_e2e.free_port(), "--no-system")
    try:
        out, js = _play(tmp_path, "persist_open", base)
        opened = _tagged(js, "OPENED")
        assert "first_open" in opened["ach"], out[-3000:]
    finally:
        server.terminate()
        server.wait(timeout=10)
    said = log.read_text()
    assert "system batch refused" not in said, said[-2000:]
    assert json.loads((top / "system.json").read_text()) == BOARD_ROWS
    assert not (top / "achievements.json").exists()
