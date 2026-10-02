"""The cart store (#124): `runtime/cart_index.py` (the console's `moy install`)
and `runtime/getcarts_app.py` (the Get Carts app), on the host.

Five families:

  * READING -- the live indexes' snapshots parse, and the plan for a console
    takes main.wasm plus ONLY that console's module.
  * INSTALLING -- every byte checked, and every way an install can fail
    leaves the shelf exactly as it was: no folder, no staging, no record.
  * UPDATING AND REMOVING -- an update keeps the kid's saves and an edited
    config, rebuilds the module set, and a crash at any point of the swap is
    put right by `recover`.
  * THE TRANSPORTS -- the host's urllib one against a local server, the
    board's over scripted sockets (the TLS itself is glass-only).
  * THE APP -- the screens a kid walks through on a real Workstation, the
    radio lease held only while something is fetched, and draw == tap.

The MicroPython lane at the bottom runs the installer under the desktop
MicroPython, which is where `deflate.DeflateIO` is (CPython's path is zlib).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cart_store_fixtures import (GPL_URL, MIT_URL, MemNet, Repo, Server, Truncated,
                                 _Resp, sha, snapshot, stored_zip)
from runtime import cart_index as ci

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://carts.example/repo"


def _store(tmp_path):
    root = tmp_path / "moybyte" / "carts"
    root.mkdir(parents=True)
    return str(root).replace("\\", "/")


def _session(fn):
    return fn()


def _install(cart, root, net, chip="esp32s3", fmt=2, accepted=None, session=_session,
             step_ms=50):
    p = ci.plan(cart, chip, fmt)
    if accepted is None:
        accepted = [e["path"] for e in p["external"]]
    job = ci.Install(cart, p, net, root, session, accepted, step_ms=step_ms)
    n = 0
    while job.step():
        n += 1
        assert n < 10000
    return job


def _parsed(repo):
    return ci.parse_index(repo.index(), repo.url("index.json"))


def _cart(repo, cid):
    return next(c for c in _parsed(repo) if c["id"] == cid)


def _untouched(root, folder):
    """The shelf is as it was before an install that failed."""
    base = Path(root)
    assert not (base / folder).exists(), "a failed install left a cart folder"
    stage = base.parent / ci.STAGE_DIR
    assert not stage.exists() or not os.listdir(stage), \
        "a failed install left %s" % os.listdir(stage)
    assert folder not in ci.load_record(root)


# -- READING -------------------------------------------------------------------

def test_the_live_indexes_parse():
    gpl = ci.parse_index(snapshot("gpl-index.json"), GPL_URL)
    mit = ci.parse_index(snapshot("mit-index.json"), MIT_URL)
    assert [c["id"] for c in gpl] == ["doom"]
    assert sorted(c["id"] for c in mit) == ["esp88", "teapot"]
    doom = gpl[0]
    assert doom["index"] == GPL_URL and doom["shelf"] == "Moybyte GPL carts"
    assert doom["external"][0]["path"] == "doom1.wad"


def test_the_plan_takes_main_wasm_and_only_this_consoles_module():
    doom = ci.parse_index(snapshot("gpl-index.json"), GPL_URL)[0]
    p = ci.plan(doom, "esp32s3", 2)
    assert p["module"] == "main.esp32s3.f2.aot"
    assert "main.esp32p4.f2.aot" not in p["files"]
    assert {"main.wasm", "manifest.json", "config.json", "doom1.wad"} <= set(p["files"])
    assert not p["slow"]
    files = doom["assets"][0]["files"]
    assert p["store_bytes"] == sum(m["size"] for fn, m in files.items()
                                   if fn != "main.esp32p4.f2.aot") \
        + doom["external"][0]["size"]
    assert p["download_bytes"] == doom["assets"][0]["size"] \
        + doom["external"][0]["archive"]["size"]


@pytest.mark.parametrize("chip,fmt", [("esp32s3", 3), ("esp32c6", 2), (None, None)])
def test_a_console_with_no_module_of_its_own_takes_none(chip, fmt):
    """A board with a compiled tier and no module of its own plays the cart on
    the interpreter (slow); the host has no compiled tier to be slow against."""
    tea = next(c for c in ci.parse_index(snapshot("mit-index.json"), MIT_URL)
               if c["id"] == "teapot")
    p = ci.plan(tea, chip, fmt)
    assert p["module"] is None
    assert p["slow"] is bool(chip)
    assert not [fn for fn in p["files"] if fn.endswith(".aot")]


def test_module_names_are_read_by_the_wasm_cart_rule():
    sys.path.insert(0, str(ROOT))
    from tools.wasm_cart import aot_name
    name = aot_name("main.wasm", "esp32p4", 7)
    assert ci.module_parts(name) == ("main", "esp32p4", "7")
    assert ci.module_parts("main.wasm") is None
    assert ci.module_parts("teapot.obj") is None


def test_references_resolve_against_the_index():
    idx = "https://moybyte-org.github.io/gpl-carts/index.json"
    assert ci.resolve(idx, "carts/doom/NOTICE") == \
        "https://moybyte-org.github.io/gpl-carts/carts/doom/NOTICE"
    assert ci.resolve(idx, "/x/y") == "https://moybyte-org.github.io/x/y"
    assert ci.resolve(idx, "https://deb.debian.org/a") == "https://deb.debian.org/a"
    assert ci.resolve("http://192.168.1.5:8000/index.json", "./carts/a") == \
        "http://192.168.1.5:8000/carts/a"


@pytest.mark.parametrize("bad", ["../escape", ".hidden", "a/b", ""])
def test_an_entry_naming_a_path_outside_its_folder_is_left_out(bad):
    repo = Repo(BASE)
    entry = repo.add("good")
    entry["assets"][0]["files"][bad] = {"size": 1, "sha256": "0" * 64}
    assert _parsed(repo) == []


def test_a_folder_that_is_not_a_cart_folder_is_left_out():
    repo = Repo(BASE)
    repo.add("good")["folder"] = "good"
    assert _parsed(repo) == []


@pytest.mark.parametrize("doc", [b"<html>", b'{"version": 2, "carts": []}', b"[]"])
def test_a_document_that_is_not_an_index_is_refused(doc):
    with pytest.raises(ci.InstallError) as exc:
        ci.parse_index(doc, "https://x/index.json")
    assert exc.value.text == ci.UNREACHABLE


def test_an_indexes_file_beside_the_carts_folder_replaces_the_defaults(tmp_path):
    root = _store(tmp_path)
    assert ci.load_indexes(root) == list(ci.DEFAULT_INDEXES)
    (Path(root).parent / ci.INDEXES_NAME).write_text(json.dumps(
        {"indexes": ["http://192.168.1.5:8000/index.json", "not a url"]}))
    assert ci.load_indexes(root) == ["http://192.168.1.5:8000/index.json"]


_VECTORS = ROOT / "tests" / "cover_vectors"


def test_an_entry_keeps_the_cover_it_names():
    repo = Repo(BASE)
    data = (_VECTORS / "rgb_filters_mixed.png").read_bytes()
    entry = repo.add("good", cover=data)
    assert list(entry).index("cover") == list(entry).index("licence") + 1
    (c,) = _parsed(repo)
    assert ci.cover_ref(c) == {"url": "carts/good/cover.png", "size": len(data),
                               "sha256": sha(data), "w": 128, "h": 128}


@pytest.mark.parametrize("field, value", [
    ("w", 64), ("h", 512), ("size", 65537), ("size", 0), ("sha256", "x" * 64),
    ("url", 7), (None, "not an object")])
def test_a_cover_the_console_cannot_read_leaves_the_cart_without_one(field, value):
    """A cover is the row's picture and nothing else: an entry whose cover is
    out of SPEC.md 3.6's profile or malformed keeps its cart."""
    repo = Repo(BASE)
    entry = repo.add("good", cover=(_VECTORS / "rgb_filters_mixed.png").read_bytes())
    if field is None:
        entry["cover"] = value
    else:
        entry["cover"][field] = value
    (c,) = _parsed(repo)
    assert "cover" not in c and ci.cover_ref(c) is None


# -- INSTALLING -------------------------------------------------------------------

def test_an_install_writes_exactly_the_plan_and_records_it(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", modules=(("esp32s3", 2), ("esp32p4", 2)))
    cart = _cart(repo, "jet")
    job = _install(cart, root, MemNet(repo.routes()), chip="esp32p4")
    assert job.error is None and job.path == root + "/jet.moy"
    files = sorted(os.listdir(job.path))
    assert files == sorted(ci.plan(cart, "esp32p4", 2)["files"])
    assert "main.esp32p4.f2.aot" in files and "main.esp32s3.f2.aot" not in files
    for fn, meta in cart["assets"][0]["files"].items():
        if fn in files:
            assert sha((Path(job.path) / fn).read_bytes()) == meta["sha256"]
    rec = ci.load_record(root)["jet.moy"]
    assert rec["id"] == "jet" and rec["version"] == 1
    assert rec["module"] == "main.esp32p4.f2.aot"
    assert rec["index"] == repo.url("index.json")
    assert job.done == job.total == cart["assets"][0]["size"]
    assert not os.listdir(Path(root).parent / ci.STAGE_DIR)


def test_an_external_file_is_never_fetched_without_its_licence(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("dm", external=[("game.wad", b"WAD!" * 3000, "pkg/game.wad")])
    net = MemNet(repo.routes())
    job = _install(_cart(repo, "dm"), root, net, accepted=[])
    assert job.error == ci.NO_LICENCE
    assert net.opened == [], "something was fetched before the licence was accepted"
    _untouched(root, "dm.moy")


def test_an_accepted_external_file_comes_out_of_its_archive(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    wad = os.urandom(70000)
    repo.add("dm", external=[("game.wad", wad, "pkg/game.wad")])
    cart = _cart(repo, "dm")
    job = _install(cart, root, MemNet(repo.routes()))
    assert job.error is None, job.detail
    assert (Path(job.path) / "game.wad").read_bytes() == wad
    assert not (Path(job.path) / "README").exists()
    assert job.done == job.total


def test_the_licence_text_is_checked_too():
    repo = Repo(BASE)
    repo.add("dm", external=[("game.wad", b"x" * 100, "pkg/game.wad")])
    cart = _cart(repo, "dm")
    ref = cart["external"][0]["licence"]
    routes = repo.routes()
    assert "fun" in ci.licence_text(MemNet(routes), cart, ref)
    routes[ci.resolve(cart["index"], ref["url"])] = b"x" * ref["size"]
    with pytest.raises(ci.InstallError) as exc:
        ci.licence_text(MemNet(routes), cart, ref)
    assert exc.value.text == ci.MISMATCH


def _flip(data, at):
    b = bytearray(data)
    b[at] ^= 0xFF
    return bytes(b)


def _faults():
    """(name, how to break the repo) -- each must leave the shelf untouched."""
    def asset_byte(repo, cart):
        rel = "releases/jet-v1/jet.moy.zip"
        z = repo.files[rel]
        repo.files[rel] = _flip(z, z.index(b"main.wasm") + 40)      # inside a member

    def central_directory(repo, cart):
        rel = "releases/jet-v1/jet.moy.zip"
        repo.files[rel] = _flip(repo.files[rel], len(repo.files[rel]) - 30)

    def index_file_hash(repo, cart):
        cart["assets"][0]["files"]["main.wasm"]["sha256"] = "0" * 64

    def extra_file(repo, cart):
        files = {"manifest.json": b"{}", "config.json": b"{}", "main.wasm": b"w",
                 "LICENSES.txt": b"l", "evil.py": b"import os"}
        z = stored_zip("jet.moy", files)
        repo.files["releases/jet-v1/jet.moy.zip"] = z
        a = cart["assets"][0]
        a["size"], a["sha256"] = len(z), sha(z)
        a["files"] = dict((fn, {"size": len(b), "sha256": sha(b)})
                          for fn, b in files.items() if fn != "evil.py")

    def missing(repo, cart):
        del repo.files["releases/jet-v1/jet.moy.zip"]

    def archive_hash(repo, cart):
        rel = "mirror/game.wad.tar.gz"
        repo.files[rel] = _flip(repo.files[rel], len(repo.files[rel]) // 2)

    def member_hash(repo, cart):
        cart["external"][0]["sha256"] = "1" * 64

    return [("asset_byte", asset_byte, ci.MISMATCH),
            ("central_directory", central_directory, ci.MISMATCH),
            ("index_file_hash", index_file_hash, ci.MISMATCH),
            ("extra_file", extra_file, ci.MISMATCH),
            ("missing", missing, ci.UNREACHABLE),
            ("archive_hash", archive_hash, ci.MISMATCH),
            ("member_hash", member_hash, ci.MISMATCH)]


@pytest.mark.parametrize("name,fault,text", _faults(), ids=[f[0] for f in _faults()])
def test_every_failure_leaves_the_shelf_as_it_was(tmp_path, name, fault, text):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", external=[("game.wad", os.urandom(30000), "pkg/game.wad")])
    cart = _cart(repo, "jet")
    fault(repo, cart)
    job = _install(cart, root, MemNet(repo.routes()))
    assert job.error == text, job.detail
    assert job.path is None
    _untouched(root, "jet.moy")


@pytest.mark.parametrize("archive", ["ram", "store"])
def test_an_external_file_installs_from_ram_or_from_a_file(tmp_path, archive):
    root = _store(tmp_path)
    repo = Repo(BASE)
    wad = os.urandom(90000)
    repo.add("dm", external=[("game.wad", wad, "pkg/game.wad")])
    cart = _cart(repo, "dm")
    p = ci.plan(cart, "esp32s3", 2)
    depth = [0, 0]

    def session(fn):
        depth[0] += 1
        depth[1] = max(depth[1], depth[0])
        try:
            return fn()
        finally:
            depth[0] -= 1
    job = ci.Install(cart, p, MemNet(repo.routes()), root, session, ["game.wad"],
                     step_ms=5, archive=archive)
    while job.step():
        pass
    assert job.error is None, job.detail
    assert (Path(job.path) / "game.wad").read_bytes() == wad
    assert depth[1] == 1, "a store session was opened inside another"
    assert os.listdir(Path(root).parent / ci.STAGE_DIR) == []


@pytest.mark.parametrize("archive", ["ram", "store"])
def test_a_bad_member_leaves_no_archive_behind(tmp_path, archive):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("dm", external=[("game.wad", os.urandom(30000), "pkg/game.wad")])
    cart = _cart(repo, "dm")
    cart["external"][0]["sha256"] = "2" * 64
    job = ci.Install(cart, ci.plan(cart, "esp32s3", 2), MemNet(repo.routes()), root,
                     _session, ["game.wad"], archive=archive)
    while job.step():
        pass
    assert job.error == ci.MISMATCH
    _untouched(root, "dm.moy")


def test_a_dropped_connection_stops_cleanly(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    cart = _cart(repo, "jet")
    routes = repo.routes()
    url = cart["assets"][0]["url"]
    data = routes[url]
    routes[url] = lambda: Truncated(data, len(data) // 2)
    job = _install(cart, root, MemNet(routes))
    assert job.error == ci.STOPPED
    _untouched(root, "jet.moy")


def test_more_bytes_than_the_index_says_is_refused(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    cart = _cart(repo, "jet")
    routes = repo.routes()
    url = cart["assets"][0]["url"]
    data = routes[url]
    routes[url] = lambda: _Resp(data + b"MORE", 200, len(data))     # a lying length
    job = _install(cart, root, MemNet(routes))
    assert job.error == ci.MISMATCH
    _untouched(root, "jet.moy")


def test_a_compressed_member_is_refused_plainly(tmp_path):
    import zipfile
    import io
    root = _store(tmp_path)
    repo = Repo(BASE)
    entry = repo.add("jet")
    buf = io.BytesIO()
    files = {"manifest.json": b"{}" * 50}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("jet.moy/manifest.json", files["manifest.json"])
    data = buf.getvalue()
    repo.files["releases/jet-v1/jet.moy.zip"] = data
    a = entry["assets"][0]
    a["size"], a["sha256"] = len(data), sha(data)
    a["files"] = {"manifest.json": {"size": 100, "sha256": sha(files["manifest.json"])}}
    job = _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    assert job.error == ci.PACKING
    _untouched(root, "jet.moy")


def test_a_full_store_says_so_and_keeps_nothing(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", extra={"big.bin": os.urandom(200000)})
    writes = [0]

    def session(fn):
        writes[0] += 1
        if writes[0] == 4:
            raise OSError(28, "ENOSPC")
        return fn()
    job = _install(_cart(repo, "jet"), root, MemNet(repo.routes()), session=session)
    assert job.error == ci.FULL
    _untouched(root, "jet.moy")


def test_a_cancelled_install_keeps_nothing(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", extra={"big.bin": os.urandom(300000)})
    cart = _cart(repo, "jet")
    p = ci.plan(cart, "esp32s3", 2)
    job = ci.Install(cart, p, MemNet(repo.routes()), root, _session, step_ms=0)
    assert job.step() and job.step()
    job.cancel()
    assert job.finished and job.error is None and job.path is None
    assert not job.step()
    _untouched(root, "jet.moy")


def test_the_pipeline_writes_in_whole_buffers(tmp_path):
    """A store session per WRITE_CHUNK and per file end, not per socket read:
    the session is the SD gate on the T-Deck, a panel sync in front of it."""
    root = _store(tmp_path)
    repo = Repo(BASE)
    big = os.urandom(5 * ci.WRITE_CHUNK + 123)
    repo.add("jet", extra={"big.bin": big})
    cart = _cart(repo, "jet")
    calls = [0]

    def session(fn):
        calls[0] += 1
        return fn()
    job = _install(cart, root, MemNet(repo.routes()), session=session)
    assert job.error is None
    n_files = len(ci.plan(cart, "esp32s3", 2)["files"])
    # one per full buffer of big.bin, one per file's end, the staging and the swap
    assert calls[0] <= n_files + len(big) // ci.WRITE_CHUNK + 3


# -- UPDATING AND REMOVING ----------------------------------------------------------

def _state(repo, root, cid, chip="esp32s3", fmt=2):
    cart = _cart(repo, cid)
    p = ci.plan(cart, chip, fmt)
    present = (Path(root) / cart["folder"]).exists()
    entry = ci.load_record(root).get(cart["folder"])
    man = None
    if present and entry is None:
        man = json.loads((Path(root) / cart["folder"] / "manifest.json").read_text())
    return ci.cart_state(cart, p, entry, present, man)


def test_an_update_keeps_the_saves_and_an_edited_config(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", version=1, config=b'{"speed": 1}')
    assert _state(repo, root, "jet") == "get"
    assert _install(_cart(repo, "jet"), root, MemNet(repo.routes())).path
    assert _state(repo, root, "jet") == "installed"
    folder = Path(root) / "jet.moy"
    (folder / "pmem.json").write_text('{"0": 900}')
    (folder / "config.json").write_text('{"speed": 9}')         # the kid's own
    repo.add("jet", version=2, config=b'{"speed": 2}')
    assert _state(repo, root, "jet") == "update"
    job = _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    assert job.error is None
    assert (folder / "pmem.json").read_text() == '{"0": 900}'
    assert (folder / "config.json").read_text() == '{"speed": 9}'
    assert (folder / "main.wasm").read_bytes().endswith(b"2")
    assert ci.load_record(root)["jet.moy"]["version"] == 2
    assert _state(repo, root, "jet") == "installed"


def test_an_update_replaces_a_config_the_kid_never_touched(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", version=1, config=b'{"speed": 1}')
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    repo.add("jet", version=2, config=b'{"speed": 2}')
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    assert (Path(root) / "jet.moy" / "config.json").read_bytes() == b'{"speed": 2}'


def test_an_update_rebuilds_the_module_set_for_this_console(tmp_path):
    """Same version, a module for this console's newer format: an update, and
    the folder carries the new module and not the old."""
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", modules=(("esp32s3", 2),))
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()), fmt=3)
    assert ci.load_record(root)["jet.moy"]["module"] is None
    assert _state(repo, root, "jet", fmt=3) == "installed"
    repo.add("jet", modules=(("esp32s3", 2), ("esp32s3", 3)))
    assert _state(repo, root, "jet", fmt=3) == "update"
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()), fmt=3)
    names = os.listdir(Path(root) / "jet.moy")
    assert "main.esp32s3.f3.aot" in names and "main.esp32s3.f2.aot" not in names


def test_a_folder_another_cart_holds_is_taken(tmp_path):
    root = _store(tmp_path)
    mine = Path(root) / "jet.moy"
    mine.mkdir()
    (mine / "manifest.json").write_text(json.dumps({"title": "My Jet",
                                                    "runtime": "python"}))
    repo = Repo(BASE)
    repo.add("jet")
    assert _state(repo, root, "jet") == "taken"


def test_a_copy_put_there_another_way_offers_an_update(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    (Path(root).parent / ci.RECORD_NAME).unlink()
    (Path(root).parent / (ci.RECORD_NAME + ".bak")).unlink()
    assert _state(repo, root, "jet") == "update"


@pytest.mark.parametrize("left", ["staging", "aside", "both", "gone"])
def test_a_crash_at_any_point_of_the_swap_is_put_right(tmp_path, left):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    target = Path(root) / "jet.moy"
    stage = Path(root).parent / ci.STAGE_DIR
    (target / "pmem.json").write_text("saves")
    if left in ("aside", "both"):             # old moved out, new never moved in
        target.rename(stage / "jet.moy.old")
    if left in ("staging", "both"):           # an unfinished build
        (stage / "jet.moy").mkdir()
        (stage / "jet.moy" / "half").write_text("x")
    if left == "gone":                        # a removal that crashed mid-delete
        (stage / "other.moy.gone").mkdir()
        (stage / "jet.moy.archive").write_bytes(b"half an archive")
    assert ci.recover(root) >= 1
    assert (target / "pmem.json").read_text() == "saves"
    assert os.listdir(stage) == []


def test_remove_takes_the_folder_and_its_record(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    _install(_cart(repo, "jet"), root, MemNet(repo.routes()))
    ci.remove(root, "jet.moy")
    assert not (Path(root) / "jet.moy").exists()
    assert "jet.moy" not in ci.load_record(root)
    assert _state(repo, root, "jet") == "get"


def test_room_is_counted_in_the_stores_blocks():
    repo = Repo(BASE)
    repo.add("jet")
    p = ci.plan(_cart(repo, "jet"), "esp32s3", 2)
    n = len(p["files"])
    assert ci.need_bytes(p, 32768) >= n * 32768
    assert ci.need_bytes(p, 1) == p["store_bytes"] + 2


# -- THE TRANSPORTS ---------------------------------------------------------------

@pytest.fixture
def server():
    s = Server()
    yield s
    s.stop()


def test_the_host_transport_follows_a_redirect_to_the_release(tmp_path, server):
    from runtime.host_app import HostCartNet
    root = _store(tmp_path)
    repo = Repo(server.base + "/repo")
    repo.add("jet")
    server.serve(repo, "/repo")
    cart = _cart(repo, "jet")
    real = cart["assets"][0]["url"][len(server.base):]
    server.routes["/dl/jet.zip"] = ("redirect", real)
    cart["assets"][0]["url"] = server.base + "/dl/jet.zip"
    job = _install(cart, root, HostCartNet(timeout=5))
    assert job.error is None, job.detail


def test_the_host_transport_reports_a_missing_file_and_a_cut_body(tmp_path, server):
    from runtime.host_app import HostCartNet
    root = _store(tmp_path)
    repo = Repo(server.base + "/repo")
    repo.add("jet")
    server.serve(repo, "/repo")
    cart = _cart(repo, "jet")
    path = cart["assets"][0]["url"][len(server.base):]
    data = server.routes.pop(path)
    assert _install(cart, root, HostCartNet(timeout=5)).error == ci.UNREACHABLE
    server.routes[path] = ("cut", data, len(data) // 3)
    assert _install(cart, root, HostCartNet(timeout=5)).error == ci.STOPPED
    _untouched(root, "jet.moy")


class _Sock:
    def __init__(self, data):
        self.data, self.pos, self.closed, self.sent = data, 0, False, b""

    def settimeout(self, _):
        pass

    def connect(self, _):
        pass

    def write(self, b):
        self.sent += b

    def read(self, n=1):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def readinto(self, buf):
        chunk = self.read(len(buf))
        buf[:len(chunk)] = chunk
        return len(chunk)

    def close(self):
        self.closed = True


class _SockNet:
    AF_INET, SOCK_STREAM, IPPROTO_TCP = 2, 1, 6

    def __init__(self, *responses):
        self.responses = list(responses)
        self.made = []

    def getaddrinfo(self, host, port):
        return [(2, 1, 6, "", (host, port))]

    def socket(self, *a):
        s = _Sock(self.responses.pop(0))
        self.made.append(s)
        return s

    def wrap_socket(self, sock, server_hostname=None):
        return sock


def test_the_board_transport_rides_the_ota_client(monkeypatch):
    import moy_ota
    sys.path.insert(0, str(ROOT / "device"))
    import cart_net
    body = b"x" * 5000
    net = _SockNet(b"HTTP/1.1 302 Found\r\nLocation: https://cdn.example/a\r\n\r\n",
                   b"HTTP/1.1 200 OK\r\nContent-Length: 5000\r\n\r\n" + body)
    monkeypatch.setitem(sys.modules, "socket", net)
    monkeypatch.setitem(sys.modules, "ssl", net)
    resp = cart_net.CartNet(wifi=None).open("https://github.com/o/r/releases/a")
    assert (resp.status, resp.length) == (200, 5000)
    got = bytearray()
    buf = bytearray(1024)
    while True:
        n = resp.readinto(memoryview(buf))
        if not n:
            break
        got += buf[:n]
    assert bytes(got) == body
    assert b"User-Agent: moybyte-carts" in net.made[1].sent
    resp.close()
    assert net.made[0].closed and net.made[1].closed
    assert moy_ota.http_open is not None


def test_the_board_transport_waits_for_a_late_link(monkeypatch):
    sys.path.insert(0, str(ROOT / "device"))
    import cart_net
    import moy_ota
    state = {"up": False, "dials": 0}

    class Wifi:
        def status(self):
            return (state["up"], None, None)

    def dial(w):
        state["dials"] += 1
        return False

    def fake_wait(online, autoconnect=None, **kw):
        autoconnect()
        state["up"] = True
        return online()
    monkeypatch.setattr(moy_ota, "wait_online", fake_wait)
    assert cart_net.CartNet(Wifi(), dial).online() is True
    assert state["dials"] == 1
    assert cart_net.CartNet(None).online() is False


# -- THE APP ---------------------------------------------------------------------

class _In:
    def __init__(self, *names):
        self.names = set(names)
        self.last_key = 0

    def pressed(self, name):
        return name in self.names


def _shelves(tmp_path):
    """Two repositories behind an in-memory transport, named by indexes.json."""
    gpl = Repo("https://gpl.example/carts", "Test GPL carts")
    gpl.add("dm", name="Dungeon", spdx="GPL-2.0-or-later",
            external=[("game.wad", os.urandom(40000), "pkg/game.wad")])
    mit = Repo("https://mit.example/carts", "Test MIT carts")
    mit.add("jet", name="Jet Pot")
    mit.add("rock", name="Rock Run", runtime="lua", modules=())
    routes = gpl.routes()
    routes.update(mit.routes())
    return gpl, mit, MemNet(routes)


def _app_ws(tmp_path, net, indexes=None):
    from runtime import host_app
    carts = tmp_path / "carts"
    ws = host_app.build_workstation(str(carts))
    if indexes is not None:
        (tmp_path / ci.INDEXES_NAME).write_text(json.dumps({"indexes": indexes}))
    ws.cart_net = net
    return ws, ws._apps_by_id["getcarts"]


def _frames(ws, app, n=40):
    for _ in range(n):
        ws._dirty = True
        ws.frame(1 / 30)
        if app.phase not in ("checking", "licence_fetch", "connecting", "getting"):
            ws._dirty = True
            ws.frame(1 / 30)
            return
    raise AssertionError("the app is still busy: %s" % app.phase)


def _tap(ws, app, verb, arg=None):
    """Tap the rect the last draw registered for (verb, arg): draw == tap."""
    for rect, v, a in app.hits._items:
        if v == verb and a == arg:
            app.handle_pointer(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2, True)
            ws._dirty = True
            ws.frame(1 / 30)
            return
    raise AssertionError("nothing drawn for %r %r: %r"
                         % (verb, arg, [(v, a) for _r, v, a in app.hits._items]))


def _open(ws, app):
    ws.open_app(app)
    _frames(ws, app)


def test_with_no_way_to_fetch_the_app_says_so(tmp_path):
    ws, app = _app_ws(tmp_path, None)
    _open(ws, app)
    assert app.phase == "nonet"
    assert "carts" not in ws._wifi_holders


def test_the_app_lists_every_shelf_and_lets_the_radio_go(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json"), mit.url("index.json")])
    _open(ws, app)
    assert app.phase == "list"
    assert [r["cart"]["name"] for r in app.rows] == ["Dungeon", "Jet Pot", "Rock Run"]
    assert [r["state"] for r in app.rows] == ["get", "get", "get"]
    assert "carts" not in ws._wifi_holders and ws.wifi.radio is False
    assert net.opened == [gpl.url("index.json"), mit.url("index.json")]


def test_get_installs_a_cart_onto_the_shelf(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json"), mit.url("index.json")])
    _open(ws, app)
    app._tap_row(1)                               # Jet Pot
    ws._dirty = True
    ws.frame(1 / 30)
    assert app.phase == "cart"
    _tap(ws, app, "btn", "GET")
    held = []
    for _ in range(40):
        if app.phase == "getting":
            held.append("carts" in ws._wifi_holders)
        ws._dirty = True
        ws.frame(1 / 30)
        if app.phase == "done":
            break
    assert app.phase == "done", app.why
    assert held and all(held), "the radio was not held for the download"
    assert "carts" not in ws._wifi_holders and ws.wifi.radio is False
    assert any(str(c.get("path", "")).endswith("/jet.moy") for c in ws.carts.all)
    assert app.cur["state"] == "installed"
    _tap(ws, app, "btn", "PLAY")
    assert ws.cart is not None and str(ws.cart.get("path")).endswith("/jet.moy")


def test_a_cart_with_an_external_file_asks_first_and_no_is_no(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json"), mit.url("index.json")])
    _open(ws, app)
    app._tap_row(0)                               # Dungeon
    ws.frame(1 / 30)
    _tap(ws, app, "btn", "GET")
    _frames(ws, app)
    assert app.phase == "licence"
    assert app.focus == 1, "NO must have the focus on a licence"
    assert any("fun" in ln for ln in app.lic[1])
    assert "carts" in ws._wifi_holders            # held through the reading
    app.handle_input(_In("a"))                    # A on the focused NO
    assert app.phase == "cart" and app.status == "NOT FETCHED"
    assert "carts" not in ws._wifi_holders
    assert not [u for u in net.opened if u.endswith(".zip") or u.endswith(".tar.gz")]
    assert not (tmp_path / "carts" / "dm.moy").exists()


def test_i_agree_fetches_the_external_file_with_the_cart(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json"), mit.url("index.json")])
    _open(ws, app)
    app._tap_row(0)
    ws.frame(1 / 30)
    _tap(ws, app, "btn", "GET")
    _frames(ws, app)
    _tap(ws, app, "btn", "I AGREE")
    _frames(ws, app)
    assert app.phase == "done", app.why
    assert (tmp_path / "carts" / "dm.moy" / "game.wad").exists()


def test_a_cart_that_will_not_fit_offers_no_get(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json"), mit.url("index.json")])
    app._inst.free = lambda: (1000, 4096)
    _open(ws, app)
    app._tap_row(1)
    ws._dirty = True
    ws.frame(1 / 30)
    assert "Not enough room" in app.blocker(app.cur)
    assert ("btn", "GET") not in [(v, a) for _r, v, a in app.hits._items]
    app.handle_input(_In("a"))
    assert app.phase == "cart" and app.job is None


def test_closing_mid_download_cancels_it_and_lets_the_radio_go(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    mit.add("jet", name="Jet Pot", extra={"big.bin": os.urandom(400000)})
    net.routes.update(mit.routes())
    ws, app = _app_ws(tmp_path, net, [mit.url("index.json")])
    _open(ws, app)
    app._tap_row(0)
    ws.frame(1 / 30)
    app.job = None
    app._press("GET")
    ws._dirty = True
    ws.frame(1 / 30)                              # CONNECTING shows
    ws._dirty = True
    ws.frame(1 / 30)                              # the job starts
    assert app.phase == "getting" and "carts" in ws._wifi_holders
    app.job.step_ms = 0
    app.close()
    assert app.job.finished and app.job.path is None
    assert "carts" not in ws._wifi_holders
    assert not (tmp_path / "carts" / "jet.moy").exists()


def test_remove_needs_two_taps(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [mit.url("index.json")])
    _open(ws, app)
    app._tap_row(0)
    ws.frame(1 / 30)
    app._press("GET")
    _frames(ws, app)
    app._press("OK")
    app._tap_row(0)
    ws._dirty = True
    ws.frame(1 / 30)
    _tap(ws, app, "btn", "REMOVE")
    assert app.arm_remove and (tmp_path / "carts" / "jet.moy").exists()
    _tap(ws, app, "btn", "REMOVE")
    assert app.phase == "list" and not (tmp_path / "carts" / "jet.moy").exists()
    assert app.rows[0]["state"] == "get"
    assert not any(str(c.get("path", "")).endswith("/jet.moy") for c in ws.carts.all)


def test_a_cart_too_big_to_run_is_refused_before_its_download(tmp_path):
    """The Player's own check -- the engine's footprint for the index's
    `memory` and this console's module, against what the engine reports free
    -- asked on the CART screen, so a console never downloads a cart it would
    refuse to load. On the host the limit is wasm_host.MEMORY_LIMIT."""
    from runtime import wasm_host
    gpl, mit, net = _shelves(tmp_path)
    big = wasm_host.MEMORY_LIMIT // 65536 + 1
    mit.add("huge", name="Huge Game")
    mit.carts[-1]["memory"] = big
    mit.files["index.json"] = mit.index()
    net.routes.update(mit.routes())
    ws, app = _app_ws(tmp_path, net, [mit.url("index.json")])
    if "wasm" not in ws.runtimes:
        pytest.skip("no host wasm runtime")
    _open(ws, app)
    row = next(r for r in app.rows if r["cart"]["id"] == "huge")
    assert row["fit"] and row["fit"].startswith("Huge Game needs")
    small = next(r for r in app.rows if r["cart"]["id"] == "jet")
    assert small["fit"] is None
    app._tap_row(app.rows.index(row))
    ws._dirty = True
    ws.frame(1 / 30)
    assert app.blocker(app.cur) == row["fit"]
    assert ("btn", "GET") not in [(v, a) for _r, v, a in app.hits._items]
    app.handle_input(_In("a"))
    assert app.phase == "cart" and app.job is None
    assert not [u for u in net.opened if u.endswith(".zip")]


def test_doom_is_refused_where_the_engine_has_too_little_free(tmp_path):
    """Doom from the live index against a console reporting the S3s' 3 MB
    cart-runtime reserve free, by the engine's own AOT arithmetic: refused
    with the notice the Player gives, before anything is fetched."""
    from runtime import wasm_binding
    if not wasm_binding.HostWasmRun.available():
        pytest.skip("no host wasm binding")

    class S3Engine:
        def footprint_of(self, pages, module_len, interp):
            of = wasm_binding.interp_footprint if interp else wasm_binding.footprint
            return of(int(pages) * 65536, int(module_len))

        def memory(self):
            return 3 * 1048576, 3 * 1048576
    net = MemNet({GPL_URL: snapshot("gpl-index.json")})
    ws, app = _app_ws(tmp_path, net, [GPL_URL])
    ws.runtimes["wasm"] = S3Engine()
    app._inst.chip = lambda: ("esp32s3", "2")
    _open(ws, app)
    doom = app.rows[0]
    assert doom["cart"]["id"] == "doom" and doom["plan"]["module"] == "main.esp32s3.f2.aot"
    assert doom["fit"] and "Doom needs" in doom["fit"] and "MB" in doom["fit"]
    assert net.opened == [GPL_URL]


def test_the_archive_goes_to_a_file_where_memory_is_short(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json")])
    _open(ws, app)
    app._inst.memory = lambda: (65536, 65536)
    app._tap_row(0)
    ws.frame(1 / 30)
    app._press("GET")
    _frames(ws, app)
    app._press("I AGREE")
    ws._dirty = True
    ws.frame(1 / 30)
    ws._dirty = True
    ws.frame(1 / 30)
    assert app.job is not None and app.job.archive == "store"
    _frames(ws, app)
    assert app.phase == "done", app.why
    assert (tmp_path / "carts" / "dm.moy" / "game.wad").exists()
    assert os.listdir(tmp_path / ci.STAGE_DIR) == []


def test_buttons_walk_the_screens_without_a_finger(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [mit.url("index.json")])
    _open(ws, app)
    app.handle_input(_In("down"))
    assert app.sel == 1
    app.handle_input(_In("a"))
    assert app.phase == "cart" and app.cur["cart"]["name"] == "Rock Run"
    app.handle_input(_In("b"))
    assert app.phase == "list"
    app.handle_input(_In("up"))
    app.handle_input(_In("a"))                    # Jet Pot
    app.handle_input(_In("a"))                    # GET (focus 0)
    ws._dirty = True
    ws.frame(1 / 30)
    app.handle_input(_In("a"))                    # A never cancels a download
    assert app.phase in ("connecting", "getting")
    _frames(ws, app)
    assert app.phase == "done"
    app.handle_input(_In("right"))
    app.handle_input(_In("a"))                    # OK
    assert app.phase == "list"


def test_b_stops_a_check_and_lets_the_radio_go(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, [gpl.url("index.json"), mit.url("index.json")])
    ws.open_app(app)
    for _ in range(3):
        ws._dirty = True
        ws.frame(1 / 30)
    assert app.phase == "checking" and "carts" in ws._wifi_holders
    app.handle_input(_In("b"))
    assert app.phase == "list" and app.status == "STOPPED"
    assert "carts" not in ws._wifi_holders


def test_an_unreachable_shelf_offers_to_try_again(tmp_path):
    gpl, mit, net = _shelves(tmp_path)
    ws, app = _app_ws(tmp_path, net, ["https://nowhere.example/index.json"])
    _open(ws, app)
    assert app.phase == "unreached"
    assert "carts" not in ws._wifi_holders
    net.up = False
    _tap(ws, app, "btn", "TRY AGAIN")
    _frames(ws, app)
    assert app.phase == "nowifi"
    assert "carts" not in ws._wifi_holders


def test_the_app_draws_at_every_shell_size(tmp_path):
    """The handheld, the Guition S3's landscape glass, and the windowed desk:
    every screen draws with nothing escaping its rect."""
    from runtime import host_app
    for kw in ({}, {"sys_size": (480, 320), "panel_diagonal_in": 3.5},
               {"sys_size": (1024, 600), "font_scale": 2, "windowed": True}):
        gpl, mit, net = _shelves(tmp_path)
        d = tmp_path / ("s%d" % len(kw))
        d.mkdir()
        ws = host_app.build_workstation(str(d / "carts"), **kw)
        (d / ci.INDEXES_NAME).write_text(json.dumps(
            {"indexes": [gpl.url("index.json"), mit.url("index.json")]}))
        ws.cart_net = net
        app = ws._apps_by_id["getcarts"]
        _open(ws, app)
        app._tap_row(0)
        ws.frame(1 / 30)
        _tap(ws, app, "btn", "GET")
        _frames(ws, app)
        assert app.phase == "licence"
        for rect, _v, _a in app.hits._items:
            assert rect[0] >= 0 and rect[1] >= 0
            assert rect[0] + rect[2] <= ws.sys_canvas.w
            assert rect[1] + rect[3] <= ws.sys_canvas.h


def _covered_shelf(rock_cover=None, unlisted=None):
    """One repository: Jet Pot with a real RGB cover, Rock Run with
    `rock_cover` as (index's bytes, served bytes or None for a missing file),
    Dungeon with none. `unlisted` is a cover.png Rock Run's release carries
    and its index does not name."""
    jet = (_VECTORS / "rgb_filters_mixed.png").read_bytes()
    repo = Repo("https://mit.example/carts", "Test MIT carts")
    repo.add("jet", name="Jet Pot", cover=jet)
    repo.add("dm", name="Dungeon")
    if rock_cover is None:
        repo.add("rock", name="Rock Run", runtime="lua", modules=(),
                 extra=None if unlisted is None else {"cover.png": unlisted})
    else:
        listed, served = rock_cover
        repo.add("rock", name="Rock Run", runtime="lua", modules=(), cover=listed)
        if served is None:
            del repo.files["carts/rock/cover.png"]
        else:
            repo.files["carts/rock/cover.png"] = served
    return repo, jet, MemNet(repo.routes())


def _canvas_rows(cv, rect):
    """The canvas's bytes under `rect` (surface-local), row by row."""
    x, y, w, h = rect
    out = []
    for yy in range(y, y + h):
        o = 2 * ((yy + cv._oy) * cv._stride + x + cv._ox)
        out.append(bytes(cv._buf[o:o + 2 * w]))
    return out


def test_the_rows_show_the_covers_the_indexes_name(tmp_path):
    """CHECKING fetches each cover an index names while it holds the radio,
    checks it, and keeps it decoded at the row's size -- the reduction
    nearest the row's height, in the canvas's own byte order -- and the
    row draws it at its left, centred and cropped to the row."""
    from runtime import cover_png
    repo, jet, net = _covered_shelf()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    assert app.phase == "list"
    assert repo.url("carts/jet/cover.png") in net.opened
    assert "carts" not in ws._wifi_holders and ws.wifi.radio is False
    cv = ws.sys_canvas
    side = app.layout.thumb
    rh = app.layout.row_h - 2 * app.layout.fs
    assert side in (16, 32, 64, 128) and abs(side - rh) <= min(
        abs(s - rh) for s in (16, 32, 64, 128))
    fmt = cover_png.RGB565_SW if cv.swapped565 else cover_png.RGB565
    want = cover_png.decode(jet, 128 // side, fmt)
    assert app.thumbs[sha(jet)] == (side, want)
    r = [row["cart"]["name"] for row in app.rows].index("Jet Pot") - app.top
    x, y, w, h = app.layout.row_rect(r)
    shown = min(side, h)
    top = (side - shown) // 2
    got = _canvas_rows(cv, (x, y + (h - shown) // 2, side, shown))
    assert got == [want[2 * side * (top + i):2 * side * (top + i + 1)]
                   for i in range(shown)]


@pytest.mark.parametrize("fault", ["wrong bytes", "missing", "not a cover"])
def test_a_cover_that_does_not_come_leaves_the_row_as_it_is(tmp_path, fault):
    """A cover whose file is missing, comes back other than its index says,
    or is not a cover (SPEC.md 3.6) draws the row exactly as a cart whose
    index names no cover (the same release, so the same size on the row)."""
    good = (_VECTORS / "rgb_filter2.png").read_bytes()
    big = (_VECTORS / "size_512x512.png").read_bytes()
    rock = {"wrong bytes": (good, good[:-1] + b"\0"), "missing": (good, None),
            "not a cover": (big, big)}[fault]
    views = []
    for name, cover in (("plain", None), (fault, rock)):
        repo, _jet, net = _covered_shelf(cover, unlisted=rock[0])
        d = tmp_path / name.replace(" ", "_")
        d.mkdir()
        ws, app = _app_ws(d, net, [repo.url("index.json")])
        _open(ws, app)
        assert app.phase == "list"
        assert "carts" not in ws._wifi_holders
        i = [row["cart"]["name"] for row in app.rows].index("Rock Run")
        assert ("cover" in app.rows[i]["cart"]) == (cover is not None)
        views.append(_canvas_rows(ws.sys_canvas, app.layout.row_rect(i - app.top)))
    assert views[0] == views[1]


def test_a_second_check_fetches_only_the_covers_it_has_not_drawn(tmp_path):
    repo, jet, net = _covered_shelf()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    _tap(ws, app, "check")
    _frames(ws, app)
    assert app.phase == "list"
    assert net.opened.count(repo.url("carts/jet/cover.png")) == 1
    assert net.opened.count(repo.url("index.json")) == 2


def test_an_installed_cart_brings_its_cover_to_the_shelf(tmp_path):
    from runtime import moy_carts
    repo, jet, net = _covered_shelf()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")])
    _open(ws, app)
    app._tap_row([row["cart"]["name"] for row in app.rows].index("Jet Pot"))
    ws._dirty = True
    ws.frame(1 / 30)
    _tap(ws, app, "btn", "GET")
    _frames(ws, app)
    assert app.phase == "done", app.why
    (cart,) = [c for c in ws.carts.all if str(c.get("path", "")).endswith("/jet.moy")]
    assert moy_carts.load_cover(cart["path"]) == jet


# -- the MicroPython lane ------------------------------------------------------------

_MP_SCRIPT = r'''
import sys, os, json
sys.path.insert(0, %(runtime)r)
import cart_index as ci
files = json.load(open(%(map)r))
class R:
    def __init__(s, path):
        s.f = open(path, "rb"); s.status = 200; s.length = os.stat(path)[6]
    def readinto(s, mv): return s.f.readinto(mv)
    def close(s): s.f.close()
class Net:
    def open(s, url): return R(files[url])
idx = open(%(index)r, "rb").read()
carts = ci.parse_index(idx, %(index_url)r)
root = %(root)r
for c in carts:
    p = ci.plan(c, "esp32p4", "2")
    job = ci.Install(c, p, Net(), root, lambda fn: fn(),
                     [e["path"] for e in p["external"]], step_ms=20, archive=%(archive)r)
    while job.step():
        pass
    print(c["id"], job.error, job.detail if job.error else "",
          sorted(os.listdir(root + "/" + c["folder"])) if job.path else "-")
rec = ci.load_record(root)
print("record", sorted(rec))
bad = dict(carts[0])
bad["folder"] = "bad.moy"
bad["external"] = []
asset = dict(bad["assets"][0])
asset["sha256"] = "0" * 64
bad["assets"] = [asset]
job = ci.Install(bad, ci.plan(bad, "esp32p4", "2"), Net(), root, lambda fn: fn(), (),
                 step_ms=20, archive=%(archive)r)
while job.step():
    pass
print("refused", job.error == ci.MISMATCH, "bad.moy" in os.listdir(root))
'''


@pytest.mark.parametrize("archive", ["ram", "store"])
def test_the_installer_runs_under_micropython(tmp_path, archive):
    from unix_mp import require_unix_mp
    exe = require_unix_mp(why="the deflate half of cart_index only exists there")
    repo = Repo("https://mp.example/carts")
    wad = os.urandom(90000)
    repo.add("dm", external=[("game.wad", wad, "pkg/game.wad")])
    repo.add("jet", extra={"big.bin": os.urandom(150000)})
    data = tmp_path / "data"
    data.mkdir()
    url_map = {}
    for i, (url, blob) in enumerate(repo.routes().items()):
        p = data / ("f%d" % i)
        p.write_bytes(blob)
        url_map[url] = str(p)
    (tmp_path / "map.json").write_text(json.dumps(url_map))
    root = _store(tmp_path)
    script = tmp_path / "run.py"
    script.write_text(_MP_SCRIPT % {
        "runtime": str(ROOT / "runtime"), "map": str(tmp_path / "map.json"),
        "index": url_map[repo.url("index.json")], "index_url": repo.url("index.json"),
        "root": root, "archive": archive})
    out = subprocess.run([exe, "-X", "heapsize=4M", str(script)], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    lines = [ln for ln in out.stdout.splitlines() if not ln.startswith("Moybyte")]
    assert "dm None" in lines[0] and "game.wad" in lines[0], out.stdout
    assert "main.esp32p4.f2.aot" in lines[1] and "esp32s3" not in lines[1], out.stdout
    assert lines[2] == "record ['dm.moy', 'jet.moy']"
    # an InstallError raised and caught on MicroPython, which has no
    # Exception.__init__ for a subclass to call
    assert lines[3] == "refused True False", out.stdout
    assert (Path(root) / "dm.moy" / "game.wad").read_bytes() == wad
    assert os.listdir(Path(root).parent / ci.STAGE_DIR) == []
