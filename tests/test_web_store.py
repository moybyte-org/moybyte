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

from tools.board_config import runtime_file

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


def test_the_browser_store_takes_its_rules_from_the_vm():
    """The page's store holds no copy of a rule: the worker hands it the
    registry, the native store's `skip`, the path and song rules over the
    receiving C (`moy_sync.local_segs`, `local_song`) and an install's name
    rule (`cart_index.plain_name`) before any store is opened (bindStore), and
    a copy could rot -- a file the sweep ships but the local store refuses
    vanishes on the next reload."""
    js = _read("firmware", "web_runner", "moy_store.mjs")
    worker = _read("firmware", "web_runner", "worker.js")
    for gone in ("SKIP_DIRS", "SITE_SKIP_DIRS", "BINARY_FILES", "export const ROOTS",
                 "safeSegments", "isSong", "const RECORD_NAME", "const STAGE_DIR"):
        assert gone not in js, gone
    bind = worker[worker.index("function bindStore()"):]
    bind = bind[:bind.index("\n}\n")]
    for rule in ("moy_sync.roots_json", 'mp.pyimport("moy_store")', "native.skip(",
                 "sync.local_segs(", "sync.local_song(", "ci.plain_name(",
                 "ci.RECORD_NAME", "ci.STAGE_DIR", "store.bind("):
        assert rule in bind, rule
    init = worker[worker.index("async function init("):]
    assert init.index("bindStore()") < init.index("initStore(fetched)")


def test_the_root_registry_the_browser_reads_is_the_receivers():
    """`moy_sync.roots_json` is what the browser's store iterates: each root's
    VFS path and endpoint from the registry, and the files root's music
    folder."""
    import json
    from runtime import moy_sync

    got = json.loads(moy_sync.roots_json("/moy/carts"))
    assert [r["id"] for r in got] == [r.id for r in moy_sync.SYNC_ROOTS]
    for entry, root in zip(got, moy_sync.SYNC_ROOTS):
        assert entry["endpoint"] == root.endpoint.lstrip("/"), entry
        assert entry["vfs"] == root.path("/moy/carts"), entry
        assert entry["top"] == (list(root.top) if root.top else None), entry
        assert entry["binary"] == root.binary and entry["site"] == root.site, entry
    assert {r["id"]: r.get("songs") for r in got}["files"] == moy_sync.MUSIC


def _dc_depths(root):
    """The whole-folder-delete depths the receiving C applies for `root`; a
    root the C does not apply (the system root, the VM's) has none."""
    import tempfile
    from runtime import moy_sync
    root_id = root.id
    with tempfile.TemporaryDirectory() as d:
        ok = []
        for n in (1, 2, 3, 4):
            path = "/".join(["Pictures"] + ["x%d" % i for i in range(n - 1)])
            if root_id == "carts":
                path = "/".join(["a.moy"] + ["x%d" % i for i in range(n - 1)])
            applied, _errs, _shelf = moy_sync.apply_ops(
                d, [{"p": path, "dc": 1}], root_id)
            if applied:
                ok.append(n)
        return ok


def test_the_local_store_keeps_what_the_receiving_c_takes():
    """`moy_sync.local_segs` is the browser store's path rule, and it is the
    receiving C's (moy_sync_path, moy_sync_apply.c's): every path the C
    applies into a root, the local store keeps, and every path it refuses as
    a path, the local store refuses -- the journal aside, which a store of
    record keeps. A whole-folder delete reaches the depths the C deletes."""
    import tempfile
    from runtime import moy_sync

    paths = ("a.moy/main.py", "a.moy/x/y.py", "a.moy", "../a.moy/x", "a.moy/../x",
             "a.moy//x", "a.moy/./x", "a.moy/x.bak", "a.moy/thumbs/t.png",
             "a\\b/x", "a.moy/x\r", "a.moy/" + "x" * 300, "a.moy/~x",
             "Pictures/a/b.json", "Music/a.mp3", "doom/doomsav0.dsg", "doom/k~part")
    for root_id in ("carts", "files", "apps"):
        for p in paths:
            with tempfile.TemporaryDirectory() as d:
                applied, _errs, _shelf = moy_sync.apply_ops(
                    d, [{"p": p, "b": "eA=="} if root_id == "apps" else {"p": p, "t": "x"}],
                    root_id)
            local = moy_sync.local_segs(root_id, p)
            if applied:
                assert local, (root_id, p)
            elif moy_sync.sync_path(p, 0) == 0:
                assert not local, (root_id, p)
    assert moy_sync.local_segs("carts", "a.moy/journal/journal.jsonl") == 3
    assert moy_sync.sync_path("a.moy/journal/journal.jsonl", 0) == 0
    for root in moy_sync.SYNC_ROOTS:
        depths = [n for n in (1, 2, 3, 4) if moy_sync.local_segs(
            root.id, "/".join(["Pictures"] + ["x%d" % i for i in range(n - 1)]), True)]
        assert depths == _dc_depths(root), root.id


def test_a_song_is_the_media_routes():
    """The browser store's songs are the board's media route's
    (moy_sync_song): a name of the music folder, no dot first, ".mp3" in any
    case."""
    from runtime import moy_sync
    keep = ("Music/a.mp3", "Music/B.MP3", "Music/x y.Mp3")
    leave = ("Music/.a.mp3", "Music/a.mp3.txt", "Music/mp3", "Music/x/a.mp3",
             "a.mp3", "Pictures/a.mp3", "Music/.mp3", "Music/../a.mp3")
    assert all(moy_sync.local_song(r) for r in keep)
    assert not any(moy_sync.local_song(r) for r in leave)


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
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _n, errs, _s = moy_sync.apply_ops(
            d, [{"p": "a.moy/journal/journal.jsonl", "t": "x"}])
        assert errs == [(0, "bad path")]


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
    sys.path.insert(0, os.path.join(_ROOT, "tools"))
    import gen_web_blob

    worker = _read("firmware", "web_runner", "worker.js")
    build = _read("firmware", "web_runner", "build.sh")
    assert 'from "./moy_store.mjs"' in worker
    assert "moy_store.mjs" in gen_web_blob.asset_names()
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
    assert body.index("web_boot.store_mode(") < body.index("web_boot.page_boot(")
    # A name the boot script imports but web_boot does not export is a
    # NameError at boot, in a worker, on somebody else's machine.
    assert "store_mode" in worker[worker.index("from web_boot import"):
                                  worker.index("from web_boot import") + 400]

    boot = _read("firmware", "web_runner", "web_boot.py")
    assert "def store_mode(" in boot
    # boot() decides site-ness from the mode and hands it to the watcher build;
    # the per-root relaxation itself lives on the Root descriptor now.
    made = boot[boot.index("def boot("):boot.index('_S["canvas"]')]
    assert '_build_watchers(carts_root' in made and '"site"' in made, made
    build = boot[boot.index("def _build_watchers("):boot.index("def _watchers(")]
    assert "root.watch_skip(site)" in build, build
    # The relaxation is site-AND-that-root-of-record; the DEFAULT is the wire's
    # own predicate (None), so a mode that fails to arrive never starts shipping
    # journals at a board.
    sync = runtime_file("moy_sync.py").read_text(encoding="utf-8")
    skip = sync[sync.index("def watch_skip("):sync.index("def watch_read(")]
    assert "skip_keep_journal" in skip and "site_keep_journal" in skip, skip
    assert "else None" in skip, skip


def test_the_mode_is_decided_before_anything_is_written():
    """Seeding the VFS is what the mode DECIDES, so probing after the write
    would seed from carts.json and then discover the local store -- the shelf
    would flash the factory carts over the kid's own."""
    worker = _read("firmware", "web_runner", "worker.js")
    init = worker[worker.index("async function initStore("):
                  worker.index("async function init(")]
    # The mode is decided (probeMode) before the seed loop writes anything --
    # seedRoot is what does the writeStore, so probeMode must precede its call.
    assert init.index("probeMode") < init.index("seedRoot(")
    # The console boots AFTER the store is seeded: web_boot rebases each watcher
    # on whatever it finds, so a late seed would ship the whole store as changes.
    body = worker[worker.index("async function init("):]
    assert body.index("initStore(fetched)") < body.index("import web_boot")


def test_get_carts_layout_agrees_across_languages():
    """Get Carts in the browser (#124) splits one layout between Python and
    the worker: the record and the staging folder beside the carts store
    (cart_index's names, which the worker hands the store and OPFS keeps
    under the same ones), and the spool directory the worker streams a fetch
    into and carts_link reads. A drift is an install the next boot cannot
    find, or a response nobody reads."""
    import sys
    from runtime import cart_index, moy_sync
    from runtime.moy_store_base import _sibling_path

    worker = _read("firmware", "web_runner", "worker.js")

    def const(src, name):
        m = re.search(r'const %s = "([^"]*)"' % name, src)
        assert m, "no %s" % name
        return m.group(1)

    # The worker writes the record into the system root's directory, the
    # carts folder's sibling as moy_store_base._sibling_path places it.
    assert 'store.rootById("system").vfs' in worker and "installRecord" in worker
    assert _sibling_path("/moy/carts", cart_index.RECORD_NAME) == (
        moy_sync.system_root("/moy/carts") + "/" + cart_index.RECORD_NAME)
    sys.path.insert(0, _RUNNER)
    try:
        import carts_link
    finally:
        sys.path.remove(_RUNNER)
    assert const(worker, "NET_DIR") == carts_link.SPOOL


def _web_boot():
    """web_boot imported for its verbs only -- no VM, no canvas."""
    import sys
    for p in ("firmware/web_runner", "device", "runtime", "tools"):
        d = os.path.join(_ROOT, p)
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    return web_boot


def test_which_roots_a_page_seeds_is_the_registrys():
    """A board-served page seeds the roots its board served and the carts
    always; a site-mode page every root it keeps itself (the apps' folders
    are their keeper's there)."""
    from runtime import moy_sync
    assert [r.id for r in moy_sync.SYNC_ROOTS
            if moy_sync.seeds(r.id, False, False)] == ["carts"]
    assert all(moy_sync.seeds(r.id, False, True) for r in moy_sync.SYNC_ROOTS)
    for served in (False, True):
        assert [r.id for r in moy_sync.SYNC_ROOTS if moy_sync.seeds(r.id, True, served)] \
            == ["carts", "files", "system"]


def test_a_dropped_file_is_named_by_the_vm():
    """What the page does with a dropped file is web_boot.drop_kind's call: a
    song Music keeps, a PICO-8 cart by its name or a PNG's signature, else a
    `.moy` archive."""
    import json
    wb = _web_boot()
    png = json.dumps(list(b"\x89PNG\r\n\x1a\n"))
    zip_ = json.dumps(list(b"PK\x03\x04\x14\x00\x00\x00"))
    assert wb.drop_kind("Song.MP3", zip_) == "song"
    assert wb.drop_kind("x/y/tune.mp3", "[]") == "song"
    assert wb.drop_kind("celeste.p8", "[]") == "p8"
    assert wb.drop_kind("Celeste.P8.PNG", "[]") == "p8"
    assert wb.drop_kind("download (3)", png) == "p8"
    assert wb.drop_kind("hop.moy", zip_) == "archive"
    assert wb.drop_kind("hop.zip", "[]") == "archive"


def test_a_site_pages_shelves_are_its_sources(tmp_path):
    """indexes.json becomes a site-mode console's system/sources through
    cart_index's writer; a page a board serves browses the board's own."""
    import json
    from runtime import cart_index
    wb = _web_boot()
    carts = tmp_path / "carts"
    carts.mkdir()
    doc = json.dumps({"indexes": ["https://a.example/index.json", 3,
                                  "https://b.example/index.json"]})
    was = wb._S.get("store_mode")
    try:
        wb._S["store_mode"] = "board"
        wb.site_sources(str(carts), doc)
        assert not (tmp_path / "system" / "sources").exists()
        wb._S["store_mode"] = "site"
        wb.site_sources(str(carts), doc)
        assert cart_index.load_indexes(str(carts)) == [
            "https://a.example/index.json", "https://b.example/index.json"]
    finally:
        wb._S["store_mode"] = was


def test_the_export_picker_lists_the_folders_the_wire_carries(tmp_path):
    import json
    from runtime import moy_sync
    for name in ("b.moy", "a.moy", "thumbs", "__pycache__"):
        (tmp_path / name).mkdir()
    (tmp_path / "wifi.json").write_text("{}")
    assert json.loads(moy_sync.folders_json(str(tmp_path))) == ["a.moy", "b.moy"]
