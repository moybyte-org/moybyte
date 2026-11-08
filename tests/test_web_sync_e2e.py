"""The WHOLE sync loop in a real browser, against no hardware.

Real headless Chrome loads the shipped page from serve.py's board twin
(`--carts` mode: the same pack_store + apply_ops a board runs, over a plain
directory), clicks Make -> +New through the real launcher UI, and the created
cart must land as files ON DISK -- proving the page, the worker's sync pump,
the wire, and the receiving apply in one pass. This is the browsershot run
that verified the feature on 2026-08-25, committed so it stays one command:

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_sync_e2e.py

The twin is an open host (no sign-in, as a host with no system folder is);
the sign-in and its sealed routes run against the board's own C in
tests/test_web_term_e2e.py.

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


def _seeded_store(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    # Two carts, deliberately: a single-cart store trips worker.js's one-cart
    # kiosk path (the game IS the page, no shell, no Make tab).
    for cart in ("moybyte.star_catcher.moy", "moybyte.sakura.moy"):
        shutil.copytree(ROOT / "system_carts" / cart, store / cart)
    return store


@contextlib.contextmanager
def _twin(store):
    """serve.py's board twin over `store`, up and answering, then torn down."""
    web_e2e.warm_host_library()
    port = web_e2e.free_port()
    argv = [sys.executable, "serve.py", str(port), "dist", "--carts", str(store)]
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


def _browsershot(scenario, base, outdir, drop=None):
    env = dict(os.environ, MOY_BASE=base)
    if drop is not None:
        env["MOY_DROP"] = str(drop)
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

        new = store / "local.new_cart.moy"
        assert new.is_dir(), \
            "the browser-created cart never reached the store:\n%s" % run_out[-2000:]
        got = sorted(p.name for p in new.iterdir())
        assert {"config.json", "main.py", "manifest.json"} <= set(got), got
        man = json.loads((new / "manifest.json").read_text())
        assert man.get("title"), man
        # ...and nothing that must stay home CROSSED. The test is what came
        # over the WIRE, not what is on the disk afterwards, and those stopped
        # being the same thing: `moy_fs._publish` leaves a `<name>.bak` redo
        # log beside EVERY file it writes, so the receiver's own crash safety
        # now puts `main.py.bak` next to `main.py` exactly as it has always put
        # `journal/cursor.json.bak` next to the cursor. Counting either against
        # the wire counts this side's durability as a leak.
        #
        # A backup whose PRINCIPAL did not cross is still a leak, and that is
        # what this asks: every `.bak`/`.tmp` here must belong to a file that
        # is itself here. An orphan means something arrived that should not
        # have -- which is the failure the line was written to catch.
        here = {p.name for p in new.iterdir()}
        orphans = sorted(n for n in here
                         if n.endswith((".bak", ".tmp"))
                         and n.rsplit(".", 1)[0] not in here)
        assert not orphans, "a backup with no file to back: %s" % orphans

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


def _files_wasm_zip(tmp_path):
    """The Files Wasm fixture built (tools/wasm_cart.py) and zipped as an
    export is, its sources left behind: the cart a page plays from its VFS."""
    import zipfile
    sys.path.insert(0, str(ROOT))
    from tools import wasm_cart                               # noqa: E402
    built = tmp_path / "built" / "wasm_files.moy"
    wasm_cart.build(str(ROOT / "tests" / "fixtures" / "wasm" / "wasm_files.moy"),
                    str(built))
    out = tmp_path / "wasm_files.moy.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for p in sorted(built.rglob("*")):
            rel = p.relative_to(built)
            if p.is_file() and rel.parts[0] != "src":
                z.write(p, "wasm_files.moy/" + rel.as_posix())
    return out


def _played(out):
    """The console's state the scenario read after it opened the cart."""
    for m in re.findall(r"^   js -> (.*)$", out, re.M):
        v = json.loads(m)
        if isinstance(v, str) and v.startswith("{") and '"pmem"' in v:
            return json.loads(v)
    return None


def test_a_written_file_round_trips_through_the_apps_root(tmp_path):
    """What a compiled cart writes in a board-served page lands on the board,
    and comes back to the next page whole.

    Files Wasm (its src/main.wat says what it checks) writes in its first turn
    and, in its second, finds every file as it was and erases it. The first
    turn runs in one page: its writes cross as an apps batch, every file as
    bytes, and land under the board's `apps/<id>/` -- the staged halves of a
    write stay in the page. The second runs in the next page, which pulled
    them from /apps.json: the cart's own checks are the proof they arrived
    byte for byte, and its erases cross back as deletes. The module never
    crosses the carts pull, so each page takes the cart in as a dropped zip;
    the board's copy of the cart (no module) goes between the two, so the
    second drop lands under the same name and the same app id."""
    web_e2e.require("sync", "store", "apps")
    zipped = _files_wasm_zip(tmp_path)
    store = _seeded_store(tmp_path)
    written = tmp_path / "apps" / "local.wasm_files"
    with _twin(store) as base:
        out = _browsershot("sync_apps.json", base, tmp_path / "one", drop=zipped)
        st = _played(out)
        assert st and st["cart"] == "Files Wasm" and st["err"] is None, out[-3000:]
        assert st["pmem"][:4] == [1, 0, 0, 4], st
        assert written.is_dir(), "the cart's writes never reached the board:\n%s" \
            % out[-2500:]
        assert sorted(p.name for p in written.iterdir()) == [
            "options.cfg", "saves%2f%43ase.sav", "saves%2fcase.sav",
            "saves%2fslot1.sav"]
        assert (written / "saves%2fslot1.sav").read_bytes() == \
            bytes(7 * i & 0xFF for i in range(300))

        shutil.rmtree(store / "local.wasm_files.moy")
        out = _browsershot("sync_apps.json", base, tmp_path / "two", drop=zipped)
        st = _played(out)
        assert st and st["cart"] == "Files Wasm" and st["err"] is None, out[-3000:]
        assert st["pmem"][:4] == [2, 0, 0, 4], \
            "the board's written files did not reach the page whole: %r" % st
        assert not written.exists() or not any(written.iterdir()), \
            sorted(p.name for p in written.iterdir())


def _probes(out):
    """browsershot's `js` results, keyed by their `at`. Each line is a JSON
    string CONTAINING the JSON the expression returned."""
    probes = {}
    for m in re.findall(r"^   js -> (.*)$", out, re.M):
        d = json.loads(json.loads(m))
        probes[d["at"]] = d
    return probes


def test_a_pico8_cart_dropped_on_a_board_page_lands_on_the_board(tmp_path):
    """The drop works on a BOARD-served page, and the cart reaches the board.

    This is the correction of 2026-08-29. The page used to hide the
    import/export row in board mode and return early from the drop listener,
    citing the "two modes are total" owner call -- but that call is about where
    carts LIVE (a board-served page has ONE store, the board's), not about which
    direction they may travel, and it names ".moy export/import and the p8
    converter" as ordinary parts of the picture. A board-served page writes to
    the board's store on every edit, through the same sweep an import rides.

    So this drives the PAGE's own drop listener -- a real DragEvent with a real
    File, not the worker postMessage the p8 scenario uses -- because the listener
    is where the guard was. Then it asserts the whole chain landed: convert in
    the wasm VM, into the VFS, caught by the watcher, POST /sync, applied by the
    board's own apply_ops, on disk."""
    web_e2e.require("sync", "p8")
    sys.path.insert(0, str(ROOT / "tests"))
    import p8_fixture                                        # noqa: E402
    sys.path.insert(0, str(ROOT / "tools"))
    import import_p8                                         # noqa: E402

    p8, _png = p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)
    store = _seeded_store(tmp_path)
    with _twin(store) as base:
        run_out = _browsershot("sync_p8_drop.json", base, tmp_path / "shots",
                               drop=p8)
        landed = store / "local.tiny_dash.moy"
        assert landed.is_dir(), (
            "a p8 dropped on a board-served page never reached the board:\n%s"
            % run_out[-2500:])
        got = sorted(p.name for p in landed.iterdir())
        # The LUA port, not the old Python scaffold: main.lua is the proof the
        # cart's own code crossed, and the manifest must route it to the Lua
        # runtime or the board would try to exec it as Python.
        assert "main.lua" in got, got
        man = json.loads((landed / "manifest.json").read_text())
        # The cart must RUN as Lua on the board -- but `runtime` is DERIVED, not
        # written: moy_carts.scan reads `format: moy-1` as a spec cart and
        # defaults it to lua. So assert the derived value the board will act on,
        # through the same scan the board runs, rather than a manifest key whose
        # absence is correct.
        assert man.get("format") == "moy-1", man
        assert man.get("main") == "main.lua", man
        from runtime import moy_carts                         # noqa: E402
        scanned = {c["path"].rsplit("/", 1)[-1]: c
                   for c in moy_carts.scan(str(store))}
        entry = scanned.get("local.tiny_dash.moy")
        assert entry, sorted(scanned)
        assert entry.get("runtime") == "lua", entry
        # Imported carts stay private by default (#194): a board is where a cart
        # could plausibly be published FROM, so this matters more here.
