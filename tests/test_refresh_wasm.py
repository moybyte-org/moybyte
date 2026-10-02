"""tools/refresh_wasm.py: it discovers a board's compiled carts from the
board's OWN store (never a local folder walk -- ports/jet's carry no
main.wasm at all, so walking their source used to refuse both of them),
rebuilds a cart's module only when it is actually stale, prefers a KNOWN
local recipe over anything else, prunes what the board can no longer use,
and keeps going past one cart it could not refresh
(docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware")."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import refresh_wasm  # noqa: E402
import push_cart  # noqa: E402
import wasm_cart  # noqa: E402

CHIP = "esp32s3"


def _cart(root, name, runtime="wasm", title=None, main="main.wasm"):
    d = os.path.join(root, name)
    os.makedirs(d)
    man = {"title": title or name, "runtime": runtime, "main": main}
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f)
    return d


def _built_cart(root, name, title):
    """A local source folder that already carries a built main.wasm -- what
    `known_sources` should pick up with NO build step."""
    d = _cart(root, name, title=title)
    with open(os.path.join(d, "main.wasm"), "wb") as f:
        f.write(b"\0asm fake")
    return d


def wanted(main="main.wasm", chip=CHIP):
    return wasm_cart.aot_name(main, chip)


def cart_entry(title, path, main="main.wasm", runtime="wasm"):
    """One `ws.carts.all` row, shaped as the real store's -- a dict, not the
    (title, path, main) triple the discovery EXPRESSION extracts from it, so
    a test that gets the expression's shape wrong fails here rather than
    passing against a stand-in that was never faithful."""
    return {"title": title, "path": path, "main": main, "runtime": runtime}


class FakeBoard:
    """A stand-in for the P4Board `push_cart.connect` hands back: `pyval`
    evaluates the SAME expression text refresh_wasm sends, against a fake
    `ws.carts.all` and a fake `os` over an in-memory {dir: {names}} store --
    so a test failure that misreads the real expression shape fails here
    too, rather than a hand-parsed stand-in silently accepting anything."""

    def __init__(self, carts_all, files):
        self.files = files              # {dir path: set(filenames)}

        class _Ws:
            carts = type("C", (), {"all": carts_all})()

        class _FakeOs:
            @staticmethod
            def listdir(path):
                return sorted(files.get(path, ()))

            @staticmethod
            def remove(path):
                d, name = path.rsplit("/", 1)
                files[d].discard(name)

        def _import(name, *a, **kw):
            if name == "os":
                return _FakeOs
            return __import__(name, *a, **kw)

        self._env = {"ws": _Ws(), "__import__": _import}
        self.closed = False

    def pyval(self, expr, timeout=None, strict=False):
        del timeout, strict
        return eval(expr, self._env)                 # noqa: S307 (our own text)

    def close(self):
        self.closed = True


def _wire(monkeypatch, carts_all, files, chip=CHIP, pushed=None):
    """Point refresh_wasm at a FakeBoard reporting `carts_all`/`files`, a
    fixed chip, and a `push_cart.main` that -- unless a test overrides it --
    simulates a clean push: the modules the push does not carry leave the
    destination by push_cart's own rule (`stale_modules`), and the wanted
    module and main.wasm land in it. Returns the list of argv lists every
    push_cart.main call recorded."""
    boards = []

    def fake_connect(board, port, verbose=False):
        del board, port, verbose
        b = FakeBoard(carts_all, files)
        boards.append(b)
        return b
    monkeypatch.setattr(push_cart, "connect", fake_connect)
    monkeypatch.setattr(push_cart, "board_chip", lambda board: chip)
    calls = []

    def fake_push_main(argv):
        calls.append(argv)
        if pushed is not None:
            return pushed(argv)
        src, dest = argv[0], argv[argv.index("--dest") + 1]
        man = json.load(open(os.path.join(src, "manifest.json"), encoding="utf-8"))
        main = man.get("main", "main.wasm")
        carried = set(os.listdir(src)) | {main, wanted(main, chip)}
        there = files.setdefault(dest, set())
        there.difference_update(push_cart.stale_modules(main, carried,
                                                        sorted(there)))
        there.update((main, wanted(main, chip)))
        return 0
    monkeypatch.setattr(push_cart, "main", fake_push_main)
    return calls, boards


def test_a_board_with_no_chip_is_a_no_op(monkeypatch):
    monkeypatch.setattr(push_cart, "board_chip", lambda board: None)
    called = []
    monkeypatch.setattr(push_cart, "connect",
                        lambda *a, **k: called.append(1) or 1 / 0)
    done, total = refresh_wasm.refresh("xiao_zero", "auto", log=lambda s: None)
    assert (done, total) == (0, 0)
    assert not called                   # never even asks the board for its carts


def test_a_board_with_no_compiled_carts_is_a_no_op(monkeypatch, tmp_path):
    _wire(monkeypatch, [], {})
    done, total = refresh_wasm.refresh("tdeck", "auto", log=lambda s: None)
    assert (done, total) == (0, 0)


def test_an_already_current_cart_is_not_repushed(monkeypatch):
    path = "/sd/carts/jet_teapot.moy"
    files = {path: {"main.wasm", wanted()}}
    carts = [cart_entry("Jet Teapot", path)]
    calls, _ = _wire(monkeypatch, carts, files)
    log = []
    done, total = refresh_wasm.refresh("tdeck", "auto", log=log.append)
    assert (done, total) == (1, 1)
    assert calls == []                  # nothing pushed -- it was never stale
    assert any("already current" in line for line in log)


def test_a_stale_cart_matched_to_a_known_source_is_rebuilt_and_pushed(
        monkeypatch, tmp_path):
    path = "/sd/carts/jet_teapot.moy"
    files = {path: {"main.wasm"}}       # no module yet for this chip
    carts = [cart_entry("Jet Teapot", path)]
    seen = {}

    def fake_push(argv):
        # Captured DURING the call: refresh()'s `finally` deletes its work
        # dir (materialize()'s "folder" copy lives there) the moment it
        # returns, so a check after the fact would read a path already gone.
        seen["had_main_wasm"] = os.path.isfile(os.path.join(argv[0], "main.wasm"))
        files.setdefault(path, set()).update(("main.wasm", wanted()))
        return 0
    calls, _ = _wire(monkeypatch, carts, files, pushed=fake_push)
    src = _built_cart(tmp_path, "teapot.moy", "Jet Teapot")
    monkeypatch.setattr(refresh_wasm, "known_sources",
                        lambda paths=(): {"Jet Teapot": ("folder", src)})
    done, total = refresh_wasm.refresh("tdeck", "auto", log=lambda s: None)
    assert (done, total) == (1, 1)
    assert len(calls) == 1
    argv = calls[0]
    # materialize() copies a "folder" source into refresh's OWN work dir
    # rather than handing push_cart the checkout's copy directly, so only
    # the basename and content travel, not the path.
    assert os.path.basename(argv[0]) == os.path.basename(src)
    assert seen["had_main_wasm"]
    assert argv[argv.index("--board") + 1] == "tdeck"
    assert argv[argv.index("--dest") + 1] == path
    assert wanted() in files[path]


def test_a_cart_with_no_known_source_is_refused(monkeypatch):
    path = "/sd/carts/doom.moy"
    files = {path: {"main.wasm"}}
    carts = [cart_entry("Doom", path)]
    calls, _ = _wire(monkeypatch, carts, files)
    monkeypatch.setattr(refresh_wasm, "known_sources", lambda paths=(): {})
    log = []
    done, total = refresh_wasm.refresh("tdeck", "auto", log=log.append)
    assert (done, total) == (0, 1)
    assert calls == []
    assert any("no known source" in line for line in log)


def test_stale_modules_are_removed_after_a_rebuild(monkeypatch, tmp_path):
    path = "/sd/carts/jet_teapot.moy"
    stale = wasm_cart.aot_name("main.wasm", CHIP, "0")
    other_chip = wasm_cart.aot_name("main.wasm", "esp32p4")
    files = {path: {"main.wasm", stale, other_chip}}
    carts = [cart_entry("Jet Teapot", path)]
    calls, _ = _wire(monkeypatch, carts, files)
    src = _built_cart(tmp_path, "teapot.moy", "Jet Teapot")
    monkeypatch.setattr(refresh_wasm, "known_sources",
                        lambda paths=(): {"Jet Teapot": ("folder", src)})
    done, _total = refresh_wasm.refresh("tdeck", "auto", log=lambda s: None)
    assert done == 1
    assert len(calls) == 1
    assert files[path] == {"main.wasm", wanted()}    # both leftovers gone


def test_stale_modules_are_removed_even_when_already_current(monkeypatch):
    path = "/sd/carts/jet_teapot.moy"
    stale = wasm_cart.aot_name("main.wasm", CHIP, "0")
    files = {path: {"main.wasm", wanted(), stale}}
    carts = [cart_entry("Jet Teapot", path)]
    calls, _ = _wire(monkeypatch, carts, files)
    log = []
    done, _total = refresh_wasm.refresh("tdeck", "auto", log=log.append)
    assert done == 1
    assert calls == []                  # still never repushed
    assert files[path] == {"main.wasm", wanted()}
    assert any("removed" in line for line in log)


def test_a_refused_cart_does_not_stop_the_run(monkeypatch, tmp_path):
    a_path, b_path = "/sd/carts/a.moy", "/sd/carts/b.moy"
    files = {a_path: {"main.wasm"}, b_path: {"main.wasm"}}
    carts = [cart_entry("A", a_path), cart_entry("B", b_path)]

    def fake_push(argv):
        if argv[0].endswith("a.moy"):
            raise SystemExit("STORE FULL: no room")
        files[b_path].add(wanted())
        return 0
    calls, _ = _wire(monkeypatch, carts, files, pushed=fake_push)
    src_a = _built_cart(tmp_path, "a.moy", "A")
    src_b = _built_cart(tmp_path, "b.moy", "B")
    monkeypatch.setattr(refresh_wasm, "known_sources",
                        lambda paths=(): {"A": ("folder", src_a),
                                          "B": ("folder", src_b)})
    log = []
    done, total = refresh_wasm.refresh("tdeck", "auto", log=log.append)
    assert (done, total) == (1, 2)      # b still got refreshed
    assert len(calls) == 2
    assert any("refused: STORE FULL" in line for line in log)


def test_a_listdir_failure_refuses_just_that_cart(monkeypatch):
    path = "/sd/carts/ghost.moy"
    carts = [cart_entry("Ghost", path)]
    calls, _ = _wire(monkeypatch, carts, {})   # listdir(path) -> KeyError inside eval

    def bad_listdir(board, port, cart_path, verbose=False):
        raise RuntimeError("no answer")
    monkeypatch.setattr(refresh_wasm, "_listdir", bad_listdir)
    log = []
    done, total = refresh_wasm.refresh("tdeck", "auto", log=log.append)
    assert (done, total) == (0, 1)
    assert calls == []
    assert any("could not read" in line for line in log)


def test_known_sources_finds_the_real_jet_titles():
    """No build, no toolchain: `known_sources` only reads manifests."""
    known = refresh_wasm.known_sources()
    assert known.get("Jet Teapot") == ("jet", "teapot")
    assert known.get("ESP 88") == ("jet", "esp88")


def test_known_sources_prefers_jet_over_a_sourceless_folder(tmp_path):
    """A ports/jet/*.moy folder carries no main.wasm, so the plain folder
    scan must never shadow the jet-builder entry with an unusable one."""
    known = refresh_wasm.known_sources(("ports",))
    assert known["Jet Teapot"][0] == "jet"


def test_known_sources_picks_up_a_folder_with_its_own_main_wasm(tmp_path):
    _built_cart(tmp_path, "demo.moy", "Demo Wasm")
    known = refresh_wasm.known_sources((str(tmp_path),))
    assert known["Demo Wasm"][0] == "folder"


def test_a_cli_run_exits_non_zero_on_a_partial_refresh(monkeypatch):
    monkeypatch.setattr(refresh_wasm, "refresh", lambda *a, **k: (1, 2))
    assert refresh_wasm.main(["--board", "tdeck"]) == 1


def test_a_cli_run_exits_clean_when_everything_refreshed(monkeypatch):
    monkeypatch.setattr(refresh_wasm, "refresh", lambda *a, **k: (2, 2))
    assert refresh_wasm.main(["--board", "tdeck"]) == 0
