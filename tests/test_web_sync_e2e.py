"""The WHOLE sync loop in a real browser, against no hardware.

Real headless Chrome loads the shipped page from serve.py's board twin
(`--carts` mode: the same pack_store + apply_ops a board runs, over a plain
directory), clicks Make -> +New through the real launcher UI, and the created
cart must land as files ON DISK -- proving the page, the worker's sync pump,
the wire, and the receiving apply in one pass. This is the browsershot run
that verified the feature on 2026-08-25, committed so it stays one command:

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_sync_e2e.py

Env-gated like the on-glass suites, not tool-gated: it costs ~40s and a
Chrome window, which does not belong in every `make test`. Prerequisites
(google-chrome or $MOY_CHROME, node, a built dist/ that carries the sync
client) SKIP with a reason rather than fail -- a missing toolchain is a bench
fact, not a regression. The same scenario runs against a real board by
pointing MOY_BASE at its webhost url (see browsershot.mjs).
"""

import json
import os
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
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~40s)")


def _skip_unless_ready():
    if shutil.which(CHROME) is None:
        pytest.skip("no %s on PATH (MOY_CHROME overrides)" % CHROME)
    if shutil.which("node") is None:
        pytest.skip("no node on PATH")
    if not (DIST / "index.html").exists():
        pytest.skip("no built dist/ -- run firmware/web_runner/build.sh")
    worker = (DIST / "worker.js").read_text()
    if "syncPump" not in worker:
        pytest.skip("dist/ predates the sync client -- rebuild web_runner")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_a_cart_made_in_chrome_lands_on_disk(tmp_path):
    _skip_unless_ready()
    store = tmp_path / "store"
    store.mkdir()
    # Two carts, deliberately: a single-cart store trips worker.js's one-cart
    # kiosk path (the game IS the page, no shell, no Make tile).
    for cart in ("star_catcher.moy", "sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)

    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "serve.py", str(port), "dist", "--carts", str(store)],
        cwd=RUNNER, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        base = "http://127.0.0.1:%d" % port
        for _ in range(50):                      # wait for the twin to answer
            try:
                urllib.request.urlopen(base + "/carts.json", timeout=1).read()
                break
            except OSError:
                if server.poll() is not None:
                    pytest.fail("serve.py died on startup")
                time.sleep(0.1)
        else:
            pytest.fail("serve.py never answered /carts.json")

        env = dict(os.environ, MOY_BASE=base)
        run = subprocess.run(
            ["node", "browsershot.mjs", "scenarios/sync_create.json",
             str(tmp_path / "shots")],
            cwd=RUNNER, env=env, capture_output=True, text=True, timeout=180)
        assert run.returncode == 0, \
            "browsershot failed:\n%s\n%s" % (run.stdout[-2000:], run.stderr[-500:])

        new = store / "new_cart.moy"
        assert new.is_dir(), \
            "the browser-created cart never reached the store:\n%s" % run.stdout[-2000:]
        got = sorted(p.name for p in new.iterdir())
        assert {"config.json", "main.py", "manifest.json"} <= set(got), got
        man = json.loads((new / "manifest.json").read_text())
        assert man.get("title"), man
        # ...and nothing that must stay home crossed: no journal, no .bak.
        assert not (new / "journal").exists() and not any(
            p.suffix in (".bak", ".tmp") for p in new.rglob("*"))
    finally:
        server.terminate()
        server.wait(timeout=10)
