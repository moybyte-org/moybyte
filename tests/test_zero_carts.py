"""A Zero's carts, asked for by the page it serves (`/official`).

A Zero's image carries the seed carts its board.toml's `[roster] image`
names; the rest are official carts its release publishes (tools/web_pack.py). The browser console the board serves
lists them, beside the carts of moybyte-org/carts (or the board's
system/sources), in Get Carts and asks the BOARD to install or remove one:
the board fetches and writes its own store, the one writer, the way it
installs its firmware (`/update`, tests/test_zero_update.py). What is pinned
here is the board's half over the real webhost, signed in as a page: the
POST queues and answers, the work is the poll's, the list is handed over
once, an install waits for the page's prompt, one the store has no room for
is refused with the sizes, an external file's licence is the page's to
accept, and a removal leaves the same record `pkg remove` leaves; and the
page's half, over the real carts_link and Get Carts.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "tools"))

from cart_store_fixtures import MemNet                       # noqa: E402
from runtime import cart_index as ci, moy_carts, web_addon    # noqa: E402
from test_official_carts import OWN, SEEDS, ZeroInst, _release  # noqa: E402
from test_ota_signing import TEST_KEYS                         # noqa: E402

SOME = ("moybyte.files", "moybyte.harpoon_pop", "moybyte.tap_only_red", "moybyte.calc")
HARPOON = "moybyte.harpoon_pop.moy"


@pytest.fixture
def zero(tmp_path, monkeypatch):
    """A Zero's host as zero_host.serve builds it, signed in to by a page
    (`zero.page`), its store holding Get Carts, Files and Calc, its release four carts
    (`zero.net` the network it fetches through)."""
    import seal_client
    from runtime import net_binding as nb
    monkeypatch.setitem(sys.modules, "moy_net", nb)
    monkeypatch.setattr(ci, "INDEX_KEYS", TEST_KEYS)
    seeds = tmp_path / "seeds"
    for cid in SOME:
        shutil.copytree(str(SEEDS / (cid + ".moy")), str(seeds / (cid + ".moy")))
    digest, routes = _release(tmp_path, seeds=seeds)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    carts = tmp_path / "moy" / "carts"
    carts.mkdir(parents=True)
    for cid in ("moybyte.files", "moybyte.calc"):
        shutil.copytree(str(seeds / (cid + ".moy")), str(carts / (cid + ".moy")))
    shutil.copytree(str(SEEDS / "moybyte.get_carts.moy"), str(carts / "moybyte.get_carts.moy"))
    (tmp_path / "moy" / "system").mkdir()
    from moy_webhost import WebHost
    h = WebHost(str(carts), auth=str(tmp_path / "moy" / "system"))
    h.net = MemNet(routes)
    h.inst = ZeroInst(str(carts), h.net)
    h.official = web_addon.OfficialCarts(h.inst, OWN)
    h.root = str(carts)
    h.handle_http("GET", "/sync", b"")
    h.page = seal_client.Browser(h.handle_http)
    h.page.pair_token(nb.web_token())
    return h


def _call(host, method, body=None):
    import seal_client
    try:
        code, plain = host.page.call(method, "/official",
                                     None if body is None else json.dumps(body).encode())
    except seal_client.Refused as e:
        return e.status, e.doc
    return code, json.loads(plain.decode())


def _run(host):
    n = 0
    while host.official.step(1000):
        n += 1
        assert n < 10000
    return n


def test_a_check_lists_the_release_s_official_carts_once(zero):
    code, doc = _call(zero, "POST", {"action": "check"})
    assert code == 200 and doc["ok"] is True and doc["state"] == "checking"
    assert zero.net.opened == [], "the POST answers; the fetch is the poll's"
    _run(zero)
    code, doc = _call(zero, "GET")
    assert code == 200 and doc["state"] == "listed"
    assert sorted(c["folder"] for c in doc["carts"]) == sorted(c + ".moy" for c in SOME)
    files, = [c for c in doc["carts"] if c["folder"] == "moybyte.files.moy"]
    assert files["core"] is True
    harpoon, = [c for c in doc["carts"] if c["folder"] == HARPOON]
    assert harpoon["core"] is False and harpoon["name"] == "Harpoon Pop"
    assert harpoon["licence"] and harpoon["assets"][0]["size"] > 0
    for c in doc["carts"]:
        for a in c["assets"]:
            assert all(set(m) == {"size"} for m in a["files"].values()), \
                "no file digest crosses"
    assert "url" not in json.dumps(doc["carts"])
    assert doc["free"][0] > 0 and doc["free"][1] > 0
    code, doc = _call(zero, "GET")
    assert doc["state"] == "idle" and "carts" not in doc, "the list is handed over once"


def test_an_install_fetches_from_the_release_and_lands_on_the_board(zero):
    moy_carts.note_removed(zero.root, HARPOON)
    code, doc = _call(zero, "POST", {"action": "install", "folder": HARPOON})
    assert code == 200 and doc["state"] == "installing"
    seen = []
    while zero.official.step(0):
        code, doc = _call(zero, "GET")
        seen.append((doc["state"], doc["progress"]))
    assert ("installing", None) in seen
    assert any(p and p[1] > 0 for _s, p in seen), "the download's progress is readable"
    code, doc = _call(zero, "GET")
    assert doc["state"] == "asking" and doc["manifest"]["title"] == "Harpoon Pop"
    assert not os.path.exists(zero.root + "/" + HARPOON), "the page's prompt comes first"
    assert _call(zero, "POST", {"action": "agree"})[0] == 200
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc == dict(doc, state="done", folder=HARPOON, error=None)
    assert os.path.exists(zero.root + "/" + HARPOON + "/manifest.json")
    assert moy_carts.load_removed(zero.root) == [], "its removal record is dropped"
    assert zero.inst.rescans[-1].endswith(HARPOON)
    got = [u.rsplit("/", 1)[-1] for u in zero.net.opened]
    assert got[0] == "carts-tdeck.json" and got[1].startswith("moybyte.harpoon_pop-")


def test_a_cart_the_release_does_not_publish_is_not_installed(zero):
    _call(zero, "POST", {"action": "install", "folder": "moybyte.sakura.moy"})
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "error" and "release" in doc["error"]
    assert not os.path.exists(zero.root + "/moybyte.sakura.moy")


def test_a_full_store_refuses_before_it_fetches_a_cart(zero):
    zero.official.reserve = 1 << 60
    _call(zero, "POST", {"action": "install", "folder": HARPOON})
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "error"
    assert doc["error"].startswith("Not enough room: needs ") and doc["error"].endswith(
        ", nothing free."), "the sizes, before anything is fetched"
    assert len(zero.net.opened) == 1, "only its index"


def test_a_removal_leaves_pkg_s_record_and_a_core_cart_stays(zero):
    code, doc = _call(zero, "POST", {"action": "remove", "folder": "moybyte.calc.moy"})
    assert code == 200 and doc["state"] == "removing"
    assert os.path.exists(zero.root + "/moybyte.calc.moy"), "the removal is the poll's"
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "done"
    assert not os.path.exists(zero.root + "/moybyte.calc.moy")
    assert moy_carts.load_removed(zero.root) == ["moybyte.calc.moy"]
    assert zero.net.opened == [], "a removal fetches nothing"
    code, doc = _call(zero, "POST", {"action": "remove", "folder": "moybyte.files.moy"})
    assert code == 409 and doc["ok"] is False
    assert os.path.exists(zero.root + "/moybyte.files.moy")
    code, doc = _call(zero, "POST", {"action": "remove", "folder": HARPOON})
    assert code == 409, "a cart the board does not hold"


def test_one_request_at_a_time_and_none_beside_the_release_s_own_step(zero):
    assert _call(zero, "POST", {"action": "check"})[0] == 200
    code, doc = _call(zero, "POST", {"action": "install", "folder": HARPOON})
    assert code == 409 and doc["message"].startswith("busy")
    _run(zero)
    zero.official.other = lambda: True
    code, doc = _call(zero, "POST", {"action": "check"})
    assert code == 409 and "release" in doc["message"]


def test_a_cancelled_install_leaves_the_store_as_it_was(zero):
    _call(zero, "POST", {"action": "install", "folder": HARPOON})
    zero.official.step(0)
    zero.official.step(0)
    code, doc = _call(zero, "POST", {"action": "cancel"})
    assert code == 200 and doc["ok"] is True
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "idle" and doc["error"] is None
    assert not os.path.exists(zero.root + "/" + HARPOON)


def test_both_methods_need_a_paired_page(zero):
    for method, body in (("GET", None), ("POST", b'{"action":"check"}')):
        resp = zero.handle_http(method, "/official", body)
        assert b" 401 " in resp.split(b"\r\n", 1)[0], method
    assert zero.official.state == "idle"


def test_a_host_with_no_backend_has_no_route(tmp_path):
    from moy_webhost import WebHost
    carts = tmp_path / "carts"
    carts.mkdir()
    resp = WebHost(str(carts)).handle_http("GET", "/official", b"")
    assert b" 404 " in resp.split(b"\r\n", 1)[0]


def test_the_zero_builds_the_backend_beside_its_updater():
    import zero_host

    class Ota:
        def own_index(self):
            return OWN
    o = zero_host.make_official(Ota())
    assert isinstance(o, web_addon.OfficialCarts)
    assert o.reserve == zero_host.SEED_RESERVE and o.index() == OWN
    assert zero_host.make_official(None) is None


@pytest.mark.parametrize("slots", [True, False], ids=["xiao", "c3"])
def test_a_zero_with_no_browser_console_installs_the_one_its_release_has(
        tmp_path, monkeypatch, slots):
    """A Zero the website's flasher wrote (the app image alone), or one whose
    store was wiped, serves no page: once its image has confirmed itself the
    serving loop (zero_host.tend_addon) installs the add-on that fits from its
    release, on both Zeros, and holds back while /official works. Only the
    XIAO, which updates over the air, also brings its official carts."""
    import zero_host
    from runtime import net_binding as nb
    from test_web_addon import _names
    monkeypatch.setitem(sys.modules, "moy_net", nb)
    monkeypatch.setattr(ci, "INDEX_KEYS", TEST_KEYS)
    seeds = tmp_path / "seeds"
    shutil.copytree(str(SEEDS / "moybyte.files.moy"), str(seeds / "moybyte.files.moy"))
    digest, routes = _release(tmp_path, seeds=seeds)
    carts = tmp_path / "moy" / "carts"
    carts.mkdir(parents=True)

    class Ota:
        def available(self):
            return slots

        def own_index(self):
            return OWN

    class Official:
        working = True

        def busy(self):
            return self.working

    try:
        _names(digest, monkeypatch)
        addon = zero_host.make_addon(Ota())
        assert addon.absent and addon.carts is slots
        net = MemNet(routes)
        addon.inst = ZeroInst(str(carts), net)
        official = Official()
        assert not zero_host.tend_addon(addon, official) and net.opened == []
        official.working = False
        n = 0
        while zero_host.tend_addon(addon, official):
            n += 1
            assert n < 10000
        assert addon.error is None
        assert nb.page_state() == 3 and nb.page_why() is None    # MOY_PAGE_READY
        assert [f for f in os.listdir(str(carts)) if f.startswith(web_addon.PREFIX)]
        fetched = len(net.opened)
        assert not zero_host.tend_addon(addon, official)
        assert len(net.opened) == fetched, "a store with its page asks nothing more"
    finally:
        nb._bake({})
        nb.page_at("")


def test_a_boot_clears_the_stage_an_install_a_reset_cut_left(tmp_path):
    """An install's stage (`install/` beside the carts folder) holds no room
    the seed counts after a reset cuts it short: the boot finishes it."""
    import zero_host
    carts = tmp_path / "moy" / "carts"
    carts.mkdir(parents=True)
    cut = tmp_path / "moy" / "install" / "web_console_0123456789ab.moy"
    cut.mkdir(parents=True)
    (cut / "moybyte_web.pack").write_bytes(b"\0" * 4096)
    assert zero_host.recover_installs(str(carts)) == 1
    assert os.listdir(str(tmp_path / "moy" / "install")) == []
    assert zero_host.recover_installs(str(carts)) == 0


# -- the page's half: Get Carts asking the board ------------------------------------


def _http(resp):
    """(status, body bytes) of a raw response off the webhost's router."""
    head, _, body = resp.partition(b"\r\n\r\n")
    if b"Transfer-Encoding: chunked" in head:
        out = b""
        while body:
            n, _, rest = body.partition(b"\r\n")
            n = int(n, 16)
            if not n:
                break
            out += rest[:n]
            body = rest[n + 2:]
        body = out
    return int(head.split()[1]), body


class _Worker:
    """The worker's half of carts_link over the board's webhost: /official
    asked through its router, a refold pulled from its carts.json, the board's
    poll run between frames."""

    def __init__(self, link, host, spool):
        self.link = link
        self.host = host
        self.spool = spool
        self.asked = []

    def tick(self):
        import base64
        jobs = self.link.poll_json()
        for j in json.loads(jobs) if jobs else ():
            if j["op"] == "official":
                method = "GET" if j["body"] is None else "POST"
                self.asked.append((method, j["body"]))
                status, body = _http(self.host.handle_http(
                    method, "/official", None if j["body"] is None else j["body"].encode()))
                with open(j["path"], "wb") as f:
                    f.write(body)
                self.link.event_json(json.dumps({"id": j["id"], "status": status}))
                self.link.event_json(json.dumps({"id": j["id"], "end": 1}))
            elif j["op"] == "refold":
                status, body = _http(self.host.handle_http("GET", "/carts.json", b""))
                doc = json.loads(body)
                shutil.rmtree(j["root"] + "/" + j["folder"], ignore_errors=True)
                for rel, v in doc.items():
                    if rel.startswith(j["folder"] + "/"):
                        path = j["root"] + "/" + rel
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        data = base64.b64decode(v["b"]) if isinstance(v, dict) else v.encode()
                        with open(path, "wb") as f:
                            f.write(data)
                self.link.event_json(json.dumps({"id": j["id"], "kept": 1}))
        if self.host.official.busy():
            self.host.official.step(0)           # the board's poll: one slice


@pytest.fixture
def page(zero, tmp_path, monkeypatch):
    """A page the Zero serves: its carts a copy of the board's, Get Carts'
    board the real carts_link.BoardCarts over a worker that asks the board."""
    from runtime import host_app, moy_catalogue
    sys.path.insert(0, str(ROOT / "firmware" / "web_runner"))
    try:
        import carts_link
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(carts_link, "SPOOL", str(tmp_path / "spool"))
    monkeypatch.setattr(carts_link, "POLL_MS", 0)
    (tmp_path / "spool").mkdir()
    proot = str(tmp_path / "page" / "carts")
    shutil.copytree(zero.root, proot)
    zero.official = web_addon.OfficialCarts(zero.inst, OWN)
    zero.page = None
    from moy_webhost import WebHost
    host = WebHost(zero.root)              # open: the page's sign-in is the worker's
    host.official = zero.official
    ws = host_app.build_workstation(proot)
    for name in os.listdir(proot):           # a page's carts are the board's alone
        if name.endswith(".moy") and not os.path.exists(zero.root + "/" + name):
            shutil.rmtree(proot + "/" + name)
    ws.carts.apply(moy_catalogue.catalogue(proot))
    landed = []

    def _landed(folder):
        landed.append(folder)
        ws.carts.apply(moy_catalogue.catalogue(proot))

    link = carts_link.CartsLink(landed=_landed)
    ws.cart_home = "headless"
    ws.cart_board = carts_link.BoardCarts(link, proot)
    app = ws.app("getcarts")
    worker = _Worker(link, host, str(tmp_path / "spool"))
    return ws, app, worker, proot, landed


def _frames(ws, worker, done, n=4000):
    for _ in range(n):
        ws.mark_dirty()
        ws.frame(1 / 30)
        worker.tick()
        if done():
            return
    raise AssertionError("still waiting")


def test_get_carts_on_a_zero_s_page_lists_the_board_s_release(page, monkeypatch):
    ws, app, worker, proot, _landed = page
    ws.spine.open_app(app)
    _frames(ws, worker, lambda: app.phase == "list")
    rows = dict((r["cart"]["folder"], r["state"]) for r in app.rows)
    assert rows == {"moybyte.files.moy": "installed", "moybyte.calc.moy": "installed",
                    HARPOON: "get", "moybyte.tap_only_red.moy": "get"}
    assert worker.asked[0] == ("POST", '{"action": "check"}')
    assert app.free is not None and app.free[0] > 0, "the board's room, not the page's"
    assert "carts" not in ws.leases.holders(), "the page's radio is not the board's"
    _frames(ws, worker, lambda: not app._covers)
    files, = [r for r in app.rows if r["cart"]["folder"] == "moybyte.files.moy"]
    assert app._pic(files) is not None, "a cart the board holds shows its cover"


def test_get_on_a_zero_s_page_asks_the_board_and_brings_its_copy_over(page, zero):
    ws, app, worker, proot, landed = page
    ws.spine.open_app(app)
    _frames(ws, worker, lambda: app.phase == "list")
    i = [r["cart"]["folder"] for r in app.rows].index(HARPOON)
    app._tap_row(i)
    app._press("GET")
    seen = []
    _frames(ws, worker, lambda: (seen.append(app._progress_of()), app.phase == "done")[1])
    assert any(t > 0 for _ti, _li, _d, t in seen), "the board's progress is drawn"
    assert os.path.exists(zero.root + "/" + HARPOON + "/manifest.json"), "the board wrote it"
    assert os.path.exists(proot + "/" + HARPOON + "/manifest.json"), "and the page has its copy"
    assert landed == [HARPOON]
    assert app.cur["state"] == "installed"
    assert any(c.get("path", "").endswith(HARPOON) for c in ws.carts.all)
    posts = [json.loads(b) for m, b in worker.asked if m == "POST"]
    assert {"action": "install", "folder": HARPOON, "index": OWN, "id": "moybyte.harpoon_pop",
            "accepted": []} in posts
    assert {"action": "agree"} in posts, "a built-in's prompt asks nothing"


def test_remove_on_a_zero_s_page_is_the_board_s_and_a_core_cart_offers_none(page, zero):
    ws, app, worker, proot, landed = page
    ws.spine.open_app(app)
    _frames(ws, worker, lambda: app.phase == "list")
    folders = [r["cart"]["folder"] for r in app.rows]
    app._tap_row(folders.index("moybyte.files.moy"))
    assert "REMOVE" not in app._verbs()
    app._tap_row(folders.index("moybyte.calc.moy"))
    assert "REMOVE" in app._verbs()
    app._remove()
    _frames(ws, worker, lambda: app.phase == "list" and app.cur is None)
    assert not os.path.exists(zero.root + "/moybyte.calc.moy")
    assert moy_carts.load_removed(zero.root) == ["moybyte.calc.moy"]
    assert not os.path.exists(proot + "/moybyte.calc.moy")
    assert landed == ["moybyte.calc.moy"]
    rows = dict((r["cart"]["folder"], r["state"]) for r in app.rows)
    assert rows["moybyte.calc.moy"] == "get"


def test_a_board_that_will_not_install_says_why_and_changes_nothing(page, zero):
    ws, app, worker, proot, _landed = page
    ws.spine.open_app(app)
    _frames(ws, worker, lambda: app.phase == "list")
    zero.official.reserve = 1 << 60
    app._tap_row([r["cart"]["folder"] for r in app.rows].index(HARPOON))
    app.free = None
    app._press("GET")
    _frames(ws, worker, lambda: app.phase == "failed")
    assert app.why.startswith("Not enough room: needs ")
    assert not os.path.exists(proot + "/" + HARPOON)


# -- the shelves beyond the release: moybyte-org/carts and system/sources ------

SHELF = "https://moybyte-org.github.io/carts"


def _shelf(zero, **kw):
    """moybyte-org/carts (cart_index.DEFAULT_INDEXES) as the board's network
    serves it: a Lua cart, a compiled one, a Python one and one with an
    external file the store has no room for."""
    from cart_store_fixtures import Repo
    repo = Repo(SHELF, "Moybyte carts")
    repo.add("moymesh", name="Moymesh", runtime="lua", modules=(),
             moybyte=kw.get("perms", {"permissions": ["graphics", "input"]}))
    repo.add("teapot", name="Jet Teapot")
    repo.add("snake_py", name="Snake Py", runtime="python", modules=())
    repo.add("doom", name="Doom",
             external=[("doom1.wad", b"WAD!" * 4000, "pkg/doom1.wad", True)])
    zero.net.routes.update(repo.routes())
    return repo


def _post_run(zero, body):
    code, doc = _call(zero, "POST", body)
    assert code == 200, doc
    _run(zero)
    return _call(zero, "GET")[1]


def _install(zero, repo, cid, **more):
    folder = cid + ".moy"
    body = dict({"action": "install", "folder": folder, "index": repo.url("index.json"),
                 "id": cid}, **more)
    return _post_run(zero, body)


def test_a_check_lists_moybyte_org_carts_beside_the_release(zero):
    _shelf(zero)
    doc = _post_run(zero, {"action": "check"})
    assert doc["state"] == "listed" and doc["unreached"] == 0
    by = dict((c["folder"], c) for c in doc["carts"])
    assert set(c + ".moy" for c in SOME) < set(by)
    assert by["moymesh.moy"]["official"] is False and by["moymesh.moy"]["state"] == "get"
    assert by["moybyte.files.moy"]["official"] is True
    assert by["moybyte.files.moy"]["state"] == "installed"
    assert by["moymesh.moy"]["index"] == SHELF + "/index.json"
    wad, = by["doom.moy"]["external"]
    assert wad["path"] == "doom1.wad" and wad["size"] == 16000
    assert by["snake_py.moy"]["runtime"] == "python", "listed; the page says it can't play it"


def test_system_sources_replace_moybyte_org_and_an_unreached_shelf_is_counted(zero):
    from cart_store_fixtures import Repo
    other = Repo("https://friend.example/carts", "A friend's carts")
    other.add("kite", name="Kite", runtime="lua", modules=())
    zero.net.routes.update(other.routes())
    (Path(zero.root).parent / "system" / "sources").write_text(
        other.url("index.json") + "\nhttps://gone.example/index.json\n")
    _shelf(zero)
    doc = _post_run(zero, {"action": "check"})
    folders = [c["folder"] for c in doc["carts"]]
    assert "kite.moy" in folders and "moymesh.moy" not in folders
    assert HARPOON in folders, "its own release is always read"
    assert doc["unreached"] == 1


def test_a_cart_from_moybyte_org_installs_once_the_page_agrees(zero):
    repo = _shelf(zero, perms={"permissions": ["pins"]})
    doc = _install(zero, repo, "moymesh")
    assert doc["state"] == "asking" and doc["manifest"]["moybyte"] == {"permissions": ["pins"]}
    assert not os.path.exists(zero.root + "/moymesh.moy"), "nothing lands before the prompt"
    doc = _post_run(zero, {"action": "agree"})
    assert doc == dict(doc, state="done", folder="moymesh.moy", error=None)
    assert os.path.exists(zero.root + "/moymesh.moy/manifest.json")
    assert ci.load_record(zero.root)["moymesh.moy"]["index"] == repo.url("index.json")
    doc = _post_run(zero, {"action": "check"})
    by = dict((c["folder"], c) for c in doc["carts"])
    assert by["moymesh.moy"]["state"] == "installed" and by["moymesh.moy"]["had"] == 1


def test_a_page_that_says_no_leaves_the_store_as_it_was(zero):
    repo = _shelf(zero)
    assert _install(zero, repo, "moymesh")["state"] == "asking"
    doc = _post_run(zero, {"action": "cancel"})
    assert doc["state"] == "idle"
    assert not os.path.exists(zero.root + "/moymesh.moy")
    assert not os.listdir(ci.stage_root(zero.root))


def test_a_cart_the_store_has_no_room_for_is_refused_with_the_sizes(zero):
    repo = _shelf(zero)
    free = web_addon._room(zero.root, 0)
    zero.official.reserve = free[0] - free[1]
    doc = _install(zero, repo, "doom", accepted=["doom1.wad"])
    assert doc["state"] == "error"
    assert doc["error"].startswith("Not enough room: needs ") and doc["error"].endswith(" free.")
    assert zero.net.opened == [repo.url("index.json")], "refused before any download"
    assert not os.path.exists(zero.root + "/doom.moy")
    assert not os.listdir(ci.stage_root(zero.root)) if os.path.exists(
        ci.stage_root(zero.root)) else True


def test_an_external_file_installs_only_with_its_licence_accepted(zero):
    repo = _shelf(zero)
    doc = _post_run(zero, {"action": "licence", "folder": "doom.moy",
                           "index": repo.url("index.json"), "id": "doom",
                           "path": "doom1.wad"})
    assert doc["state"] == "listed" and doc["text"].startswith("The licence for doom1.wad")
    assert _call(zero, "GET")[1]["state"] == "idle"
    doc = _install(zero, repo, "doom")
    assert doc["state"] == "error" and doc["error"] == ci.NO_LICENCE
    doc = _install(zero, repo, "doom", accepted=["doom1.wad"])
    assert doc["state"] == "asking"
    doc = _post_run(zero, {"action": "agree"})
    assert doc["state"] == "done"
    assert os.path.getsize(zero.root + "/doom.moy/doom1.wad") == 16000


def test_a_python_cart_installs_only_from_the_release(zero):
    repo = _shelf(zero)
    doc = _install(zero, repo, "snake_py")
    assert doc["state"] == "error" and "can't play" in doc["error"]
    assert not os.path.exists(zero.root + "/snake_py.moy")


def test_an_index_the_board_does_not_read_is_refused(zero):
    _shelf(zero)
    code, doc = _call(zero, "POST", {"action": "install", "folder": "moymesh.moy",
                                     "index": "https://evil.example/index.json"})
    assert code == 409 and "shelf" in doc["message"]
    assert zero.official.state == "idle"


def test_a_shelf_cart_is_removed_and_a_kid_s_own_cart_is_not(zero):
    repo = _shelf(zero)
    _install(zero, repo, "moymesh")
    _post_run(zero, {"action": "agree"})
    doc = _post_run(zero, {"action": "remove", "folder": "moymesh.moy"})
    assert doc["state"] == "done"
    assert not os.path.exists(zero.root + "/moymesh.moy")
    assert "moymesh.moy" not in ci.load_record(zero.root)
    assert moy_carts.load_removed(zero.root) == [], "only a built-in's removal is recorded"
    os.makedirs(zero.root + "/my_game.moy")
    code, doc = _call(zero, "POST", {"action": "remove", "folder": "my_game.moy"})
    assert code == 409
    assert os.path.exists(zero.root + "/my_game.moy")


def test_get_carts_on_a_zero_s_page_installs_from_moybyte_org(page, zero):
    """The page lists moybyte-org/carts beside the release, asks the board
    for a cart there, shows the install prompt the board's checked manifest
    asks, and records the consent; a cart the board has no room for says so
    with its sizes before anything is asked."""
    ws, app, worker, proot, landed = page
    _shelf(zero, perms={"permissions": ["pins"]})
    ws.spine.open_app(app)
    _frames(ws, worker, lambda: app.phase == "list")
    folders = [r["cart"]["folder"] for r in app.rows]
    assert "moymesh.moy" in folders and HARPOON in folders
    board_free, app.free = app.free, (16384, 4096)
    app._tap_row(folders.index("doom.moy"))
    why = app.blocker(app.cur)
    assert why == "Not enough room: needs 40 KB, 16 KB free."
    app.free = board_free
    app._tap_row(folders.index("moymesh.moy"))
    app._press("GET")
    _frames(ws, worker, lambda: app.phase in ("consent", "failed"))
    assert app.phase == "consent" and app.question == ["Switch the console's pins"]
    assert not os.path.exists(zero.root + "/moymesh.moy")
    app._answer(True)
    _frames(ws, worker, lambda: app.phase in ("done", "failed"))
    assert app.phase == "done", app.why
    assert os.path.exists(zero.root + "/moymesh.moy/manifest.json")
    assert os.path.exists(proot + "/moymesh.moy/manifest.json")
    assert landed == ["moymesh.moy"]
    assert app.cur["state"] == "installed"
    assert ws.consent.get("moymesh")["perms"] == ["pins"]


def test_a_licence_on_a_zero_s_page_is_the_board_s_fetch(page, zero):
    ws, app, worker, proot, _landed = page
    _shelf(zero)
    ws.spine.open_app(app)
    _frames(ws, worker, lambda: app.phase == "list")
    app._tap_row([r["cart"]["folder"] for r in app.rows].index("doom.moy"))
    app._start()
    _frames(ws, worker, lambda: app.phase in ("licence", "failed"))
    assert app.phase == "licence" and app._lic_text.startswith("The licence for doom1.wad")
    assert any('"licence"' in (b or "") for _m, b in worker.asked)
