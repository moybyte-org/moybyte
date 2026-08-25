"""The browser-local cart store (#193 mode 1): its JS unit suite, and the
seams that only exist BETWEEN languages.

The store itself is JavaScript, so `store_test.mjs` is where its behaviour is
pinned (mode decision, the op apply against a fake OPFS, the .moy zip both
ways) and this drives it. What is left here is what no single-language test can
see: that the two skip predicates agree, that an import stays PENDING while a
reload rebases, and that a board can actually serve the module its own worker
imports.
"""

import os
import re
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))
_RUNNER = os.path.join(_ROOT, "firmware", "web_runner")


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_store_passes_its_own_suite():
    p = subprocess.run(["node", "store_test.mjs"], cwd=_RUNNER,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_the_two_skip_predicates_agree():
    """`_skip` is the ONE rule for what never leaves a cart folder, and mode 1
    added a JS copy of it -- so it can now rot. A file the sweep ships but the
    local store refuses would vanish on the next reload."""
    from runtime import moy_sync

    js = _read("firmware", "web_runner", "moy_store.mjs")

    def arr(name):
        m = re.search(r"const %s = \[([^\]]*)\]" % name, js)
        assert m, "no %s in moy_store.mjs" % name
        return tuple(re.findall(r'"([^"]*)"', m.group(1)))

    assert arr("SKIP_DIRS") == moy_sync.SKIP_DIRS
    assert arr("SKIP_FILES") == moy_sync.SKIP_FILES
    assert arr("SKIP_SUFFIXES") == moy_sync.SKIP_SUFFIXES
    # ...and the SITE-mode pair, which is the same rule minus the journal
    # (2026-08-25: the journal lives with the store of record, and in mode 1
    # that is the browser). Two languages, two lists, same drift risk.
    assert arr("SITE_SKIP_DIRS") == moy_sync.SITE_SKIP_DIRS
    assert arr("SITE_SKIP_FILES") == moy_sync.SITE_SKIP_FILES


def test_the_journal_crosses_only_into_the_local_store():
    """The distinction the whole doctrine rests on, in both languages: a
    journal path is refused by the WIRE predicate and accepted by the LOCAL
    one. Collapse them and either a board is handed somebody else's history, or
    the kid's undo dies at the next reload -- neither of which fails loudly."""
    from runtime import moy_sync

    for name in ("journal", "journal.jsonl"):
        assert moy_sync._skip(name), name
        assert not moy_sync.skip_keep_journal(name), name
    for name in ("thumbs", "__pycache__", "main.py.bak", "main.py.tmp"):
        assert moy_sync._skip(name) and moy_sync.skip_keep_journal(name), name
    # The wire's own validator never softens: a batch aimed at a BOARD carries
    # no journal path no matter which watcher built it.
    assert moy_sync.safe_segments("a.moy/journal/journal.jsonl") is None


def test_an_import_stays_pending_but_a_reload_rebases():
    """The distinction the whole import path rests on. `rebase()` adopts the
    store as-is with nothing pending: right after a reload (the files came FROM
    the far end) and WRONG after an import (the files are new work that has to
    reach the store). Getting this backwards loses an imported cart silently at
    the next reload, which no unit test would notice."""
    boot = _read("firmware", "web_runner", "web_boot.py")
    body = boot[boot.index("def rescan_store("):boot.index("def reload_cart(")]
    assert "rebase()" not in body, "rescan_store must NOT rebase the watcher"
    assert "_rescan()" in body
    reload_body = boot[boot.index("def reload_cart("):boot.index("def open_cart(")]
    assert "rebase()" in reload_body, "reload_cart must still rebase"
    assert "_rescan()" in reload_body, "both paths share one shelf re-scan"


def test_the_store_module_reaches_every_host_that_serves_the_console():
    """worker.js STATICALLY imports moy_store.mjs, so any host that serves the
    console must serve it too -- a board included, where the asset list is a
    fixed allowlist and a miss is a console that cannot boot at all."""
    import sys
    sys.path.insert(0, os.path.join(_ROOT, "device"))
    import moy_webhost

    worker = _read("firmware", "web_runner", "worker.js")
    build = _read("firmware", "web_runner", "build.sh")
    assert 'from "./moy_store.mjs"' in worker
    assert "moy_store.mjs" in moy_webhost.ASSETS
    # ...built into dist/, and pre-gzipped like every other served asset.
    assert 'cp "${SCRIPT_DIR}/moy_store.mjs"' in build
    assert re.search(r"for f in .*moy_store\.mjs.*; do", build)
    # ...and cache-busted WITH the worker: a static import specifier is a url a
    # browser caches exactly as hard as the worker's own.
    assert "moy_store.mjs?v=" in build
    assert "moy_store.mjs" in build[build.index("MOY_BUILD=\""):
                                    build.index("MOY_BUILD=\"") + 400]


def test_the_mode_reaches_web_boot_before_the_watcher_is_built():
    """Which store is of record decides whether the carts watcher sweeps the
    journal, and `boot()` is where that watcher is constructed -- so the mode
    has to arrive FIRST. Arriving late would be a setting with no effect, which
    is exactly the shape of the bug that hid in `PUMP fold=`: a lever wired to
    nothing, reading as a decision that had been made."""
    worker = _read("firmware", "web_runner", "worker.js")
    body = worker[worker.index("async function init("):]
    assert body.index("web_boot.store_mode(") < body.index("web_boot.boot(")
    # A name the boot script imports but web_boot does not export is a
    # NameError at boot, in a worker, on somebody else's machine.
    assert "store_mode" in worker[worker.index("from web_boot import"):
                                  worker.index("from web_boot import") + 400]

    boot = _read("firmware", "web_runner", "web_boot.py")
    assert "def store_mode(" in boot
    made = boot[boot.index("import moy_sync"):boot.index('_S["canvas"]')]
    assert "skip_keep_journal" in made and '"site"' in made, made
    # The DEFAULT is the wire's own predicate: a mode that fails to arrive must
    # not start shipping journals at a board.
    assert "else None" in made, made


def test_the_mode_is_decided_before_anything_is_written():
    """Seeding the VFS is what the mode DECIDES, so probing after the write
    would seed from carts.json and then discover the local store -- the shelf
    would flash the factory carts over the kid's own."""
    worker = _read("firmware", "web_runner", "worker.js")
    init = worker[worker.index("async function initStore("):
                  worker.index("async function init(")]
    assert init.index("probeMode") < init.index("writeStore")
    # The console boots AFTER the store is seeded: web_boot rebases the watcher
    # on whatever it finds, so a late seed would ship the whole store as changes.
    body = worker[worker.index("async function init("):]
    assert body.index("initStore(carts)") < body.index("mp.runPython")
