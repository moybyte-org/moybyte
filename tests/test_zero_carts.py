"""A Zero's official carts, asked for by the page it serves (`/official`).

The Zero's image carries only the core carts; the rest are official carts its
release publishes (tools/web_pack.py). The browser console the board serves
lists them in Get Carts and asks the BOARD to install or remove one: the board
fetches from its own release and writes its own store, the one writer, the
way it installs its firmware (`/update`, tests/test_zero_update.py). What is
pinned here is the board's half over the real webhost, signed in as a page:
the POST queues and answers, the work is the poll's, the list is handed over
once, and a removal leaves the same record `pkg remove` leaves.
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

SOME = ("moybyte.files", "moybyte.hop_quest", "moybyte.tap_game", "moybyte.calc")
HOP = "moybyte.hop_quest.moy"


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
    hop, = [c for c in doc["carts"] if c["folder"] == HOP]
    assert hop["core"] is False and hop["name"] == "Hop Quest"
    assert hop["licence"] and hop["assets"][0]["size"] > 0
    for c in doc["carts"]:
        for a in c["assets"]:
            assert all(set(m) == {"size"} for m in a["files"].values()), \
                "no file digest crosses"
    assert "url" not in json.dumps(doc["carts"])
    assert doc["free"][0] > 0 and doc["free"][1] > 0
    code, doc = _call(zero, "GET")
    assert doc["state"] == "idle" and "carts" not in doc, "the list is handed over once"


def test_an_install_fetches_from_the_release_and_lands_on_the_board(zero):
    moy_carts.note_removed(zero.root, HOP)
    code, doc = _call(zero, "POST", {"action": "install", "folder": HOP})
    assert code == 200 and doc["state"] == "installing"
    seen = []
    while zero.official.step(0):
        code, doc = _call(zero, "GET")
        seen.append((doc["state"], doc["progress"]))
    assert ("installing", None) in seen
    assert any(p and p[1] > 0 for _s, p in seen), "the download's progress is readable"
    code, doc = _call(zero, "GET")
    assert doc == dict(doc, state="done", folder=HOP, error=None)
    assert os.path.exists(zero.root + "/" + HOP + "/manifest.json")
    assert moy_carts.load_removed(zero.root) == [], "its removal record is dropped"
    assert zero.inst.rescans[-1].endswith(HOP)
    got = [u.rsplit("/", 1)[-1] for u in zero.net.opened]
    assert got[0] == "carts-tdeck.json" and got[1].startswith("moybyte.hop_quest-")


def test_a_cart_the_release_does_not_publish_is_not_installed(zero):
    _call(zero, "POST", {"action": "install", "folder": "moybyte.sakura.moy"})
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "error" and "release" in doc["error"]
    assert not os.path.exists(zero.root + "/moybyte.sakura.moy")


def test_a_full_store_refuses_before_it_fetches_a_cart(zero):
    zero.official.reserve = 1 << 60
    _call(zero, "POST", {"action": "install", "folder": HOP})
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "error" and doc["error"] == ci.FULL
    assert len(zero.net.opened) == 1, "only the index"


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
    code, doc = _call(zero, "POST", {"action": "remove", "folder": HOP})
    assert code == 409, "a cart the board does not hold"


def test_one_request_at_a_time_and_none_beside_the_release_s_own_step(zero):
    assert _call(zero, "POST", {"action": "check"})[0] == 200
    code, doc = _call(zero, "POST", {"action": "install", "folder": HOP})
    assert code == 409 and doc["message"].startswith("busy")
    _run(zero)
    zero.official.other = lambda: True
    code, doc = _call(zero, "POST", {"action": "check"})
    assert code == 409 and "release" in doc["message"]


def test_a_cancelled_install_leaves_the_store_as_it_was(zero):
    _call(zero, "POST", {"action": "install", "folder": HOP})
    zero.official.step(0)
    zero.official.step(0)
    code, doc = _call(zero, "POST", {"action": "cancel"})
    assert code == 200 and doc["ok"] is True
    _run(zero)
    code, doc = _call(zero, "GET")
    assert doc["state"] == "idle" and doc["error"] is None
    assert not os.path.exists(zero.root + "/" + HOP)


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
                    HOP: "get", "moybyte.tap_game.moy": "get"}
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
    i = [r["cart"]["folder"] for r in app.rows].index(HOP)
    app._tap_row(i)
    app._press("GET")
    seen = []
    _frames(ws, worker, lambda: (seen.append(app._progress_of()), app.phase == "done")[1])
    assert any(t > 0 for _ti, _li, _d, t in seen), "the board's progress is drawn"
    assert os.path.exists(zero.root + "/" + HOP + "/manifest.json"), "the board wrote it"
    assert os.path.exists(proot + "/" + HOP + "/manifest.json"), "and the page has its copy"
    assert landed == [HOP]
    assert app.cur["state"] == "installed"
    assert any(c.get("path", "").endswith(HOP) for c in ws.carts.all)
    assert ("POST", json.dumps({"action": "install", "folder": HOP})) in worker.asked


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
    app._tap_row([r["cart"]["folder"] for r in app.rows].index(HOP))
    app.free = None
    app._press("GET")
    _frames(ws, worker, lambda: app.phase == "failed")
    assert app.why == ci.FULL
    assert not os.path.exists(proot + "/" + HOP)
