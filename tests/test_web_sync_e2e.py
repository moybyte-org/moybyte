"""The WHOLE sync loop in a real browser, against no hardware.

Real headless Chrome loads the shipped page from serve.py's board twin
(`--carts` mode: the same pack_store + apply_ops a board runs, over a plain
directory), clicks Make -> +New through the real launcher UI, and the created
cart must land as files ON DISK -- proving the page, the worker's sync pump,
the wire, and the receiving apply in one pass. This is the browsershot run
that verified the feature on 2026-08-25, committed so it stays one command:

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_sync_e2e.py

Two runs now. The second one gives the twin a PIN (`--pin`, which routes
through moy_webhost's own gate predicate) and drives the pairing gesture the
2026-08-25 owner call created: a page that arrives without one is refused
everything but the boot assets and has to ask for the pin in the page. The dev
loop itself stays pinless and is meant to -- a password in the way of a rebuild
is a password people route around -- so the flag exists for exactly this.

Env-gated like the on-glass suites, not tool-gated: it costs ~40s and a
Chrome window, which does not belong in every `make test`. Prerequisites
(google-chrome or $MOY_CHROME, node, a built dist/ that carries the sync
client) SKIP with a reason on a bench -- a missing toolchain is a bench fact,
not a regression -- and FAIL under CI, where a suite that asks to run and then
skips is a green tick over nothing. `tests/web_e2e.py` owns that decision for
both browser suites. The same scenario runs against a real board by pointing
MOY_BASE at its webhost url (see browsershot.mjs).
"""

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER, ROOT

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~40s)")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seeded_store(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    # Two carts, deliberately: a single-cart store trips worker.js's one-cart
    # kiosk path (the game IS the page, no shell, no Make tile).
    for cart in ("star_catcher.moy", "sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)
    return store


@contextlib.contextmanager
def _twin(store, pin=None):
    """serve.py's board twin over `store`, up and answering, then torn down.

    The health check is GET /sync and not /carts.json, because under `--pin`
    the latter is a 403 -- which is the feature, and would read here as a twin
    that never came up."""
    port = _free_port()
    argv = [sys.executable, "serve.py", str(port), "dist", "--carts", str(store)]
    if pin:
        argv += ["--pin", pin]
    server = subprocess.Popen(argv, cwd=RUNNER, stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/sync", timeout=1).read()
                break
            except OSError:
                if server.poll() is not None:
                    pytest.fail("serve.py died on startup")
                time.sleep(0.1)
        else:
            pytest.fail("serve.py never answered /sync")
        yield base
    finally:
        server.terminate()
        server.wait(timeout=10)


def _browsershot(scenario, base, outdir):
    env = dict(os.environ, MOY_BASE=base)
    run = subprocess.run(
        ["node", "browsershot.mjs", "scenarios/" + scenario, str(outdir)],
        cwd=RUNNER, env=env, capture_output=True, text=True, timeout=240)
    assert run.returncode == 0, \
        "browsershot failed:\n%s\n%s" % (run.stdout[-3000:], run.stderr[-500:])
    return run.stdout


def test_a_cart_made_in_chrome_lands_on_disk(tmp_path):
    web_e2e.require("sync")
    store = _seeded_store(tmp_path)
    with _twin(store) as base:
        run_out = _browsershot("sync_create.json", base, tmp_path / "shots")

        new = store / "new_cart.moy"
        assert new.is_dir(), \
            "the browser-created cart never reached the store:\n%s" % run_out[-2000:]
        got = sorted(p.name for p in new.iterdir())
        assert {"config.json", "main.py", "manifest.json"} <= set(got), got
        man = json.loads((new / "manifest.json").read_text())
        assert man.get("title"), man
        # ...and nothing that must stay home CROSSED. Scoped to the cart's own
        # files: `journal/cursor.json.bak` is moy_fs's atomic-rename rotation,
        # written HERE by the receiver's own journal, and counting it as a
        # wire leak would be counting this side's crash safety against it.
        assert not any(p.suffix in (".bak", ".tmp") for p in new.iterdir())

        # THE JOURNAL IS THE RECEIVER'S OWN (2026-08-25). This used to assert
        # `journal/` did not exist, which was the right test of the wire and
        # the wrong test of the store: the browser still ships no history, but
        # the side that KEEPS the cart now records one, so a kid's undo works
        # on work done in a browser. Proven end to end here because this is the
        # only lane where a real page, the real wire and the real apply meet.
        log = new / "journal" / "journal.jsonl"
        assert log.exists(), "the receiver kept no history of a browser commit"
        entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        assert entries, log.read_text()
        for e in entries:
            assert {"seq", "file", "snap"} <= set(e), e
            assert (new / "journal" / e["snap"]).exists(), e
        assert "main.py" in {e["file"] for e in entries}, entries


def test_a_pinned_board_prompts_in_the_page_and_then_works(tmp_path):
    """THE PIN GATES EVERYTHING (2026-08-25), so a page opened by hand -- typed
    address, no QR -- can read nothing until it asks. This drives the whole
    gesture in a real browser: the refused boot, the in-page prompt, a WRONG pin
    coming back with a plain message, the right one landing, and a shelf that is
    real enough to make a cart on.

    The prompt is deliberately not `window.prompt`, and this test is half the
    reason: a native dialog cannot be filled by CDP, screenshotted, or styled.
    """
    web_e2e.require("sync", "pin")
    store = _seeded_store(tmp_path)
    with _twin(store, pin="4321") as base:
        # The gate itself, before any browser is involved.
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/carts.json", timeout=5)
        assert err.value.code == 403
        assert json.loads(err.value.read()) == {"error": "pin"}
        urllib.request.urlopen(base + "/carts.json?pin=4321", timeout=5).read()

        out = _browsershot("pin_prompt.json", base, tmp_path / "shots")
        # browsershot prints each `js` result as JSON, and the expressions here
        # return JSON themselves -- so the line is a JSON string CONTAINING one.
        # Keyed by `at` rather than by index: a scenario grows steps.
        probes = {}
        for m in re.findall(r"^   js -> (.*)$", out, re.M):
            d = json.loads(json.loads(m))
            probes[d["at"]] = d
        for at in ("prompted", "refused", "opened"):
            assert at in probes, (at, out[-3000:])
        prompted, refused, opened = (probes["prompted"], probes["refused"],
                                     probes["opened"])

        assert prompted["overlay"] == "flex", prompted
        assert prompted["msg"] == "", "nothing was tried yet"
        assert refused["overlay"] == "flex", refused
        assert "did not work" in refused["msg"], refused
        assert "pin=0000" in refused["search"], refused
        assert opened["overlay"] == "none", opened
        assert "pin=4321" in opened["search"], opened
        assert opened["stored"] == "4321", \
            "the pin was not remembered; the kid retypes it every visit"
        assert opened["status"] == "live", opened

        # ...and the shelf behind it is the real console: a cart made on it
        # reaches the store through the same pinned /sync.
        new = store / "new_cart.moy"
        assert new.is_dir(), \
            "no cart reached the store after pairing:\n%s" % out[-2000:]
