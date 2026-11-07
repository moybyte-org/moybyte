"""The packages (docs/os_2026-11/system.md §6, runtime/carts/pkg.py): the
index's signature and `depends`, `system/sources`, the checks Get Carts
shares with the shell's `pkg` (the chip, the engine's fit and THE SHARES',
the command-name refusal), the verbs over an in-memory repository, and the
`pkg` and `settings` role commands through the C shell's door on a host
console."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import app_perms, cart_index as ci, moy_carts, pkg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ota_sign  # noqa: E402
from test_ota_signing import TEST_D, TEST_KEYS, TEST_N  # noqa: E402

BASE = "https://carts.example/repo"


def _sign(data, d=TEST_D):
    """tools/ota_sign.py's signed document, with the test key's modexp."""
    body = ota_sign.index_unsigned(data)
    digest = hashlib.sha256(ota_sign.INDEX_SCHEME + body).digest()
    block = ota_sign.pkcs1_v15_block(digest, (TEST_N.bit_length() + 7) // 8)
    sig = "%x" % pow(int.from_bytes(block, "big"), d, TEST_N)
    return body[:-1] + ota_sign.INDEX_TAIL + sig.encode() + b'"}\n'


@pytest.fixture
def keys(monkeypatch):
    from runtime import net_binding
    net_binding.install()
    monkeypatch.setattr(ci, "INDEX_KEYS", TEST_KEYS)


# -- the signature ------------------------------------------------------------------


def test_a_signed_index_verifies_and_its_carts_say_so(keys):
    repo = Repo(BASE)
    repo.add("dm")
    signed = _sign(repo.index())
    assert ci.split_signed(signed)[0] == ota_sign.INDEX_SCHEME + repo.index()
    carts = ci.parse_index(signed, repo.url("index.json"))
    assert [c["id"] for c in carts] == ["dm"] and carts[0]["signed"] is True
    unsigned = ci.parse_index(repo.index(), repo.url("index.json"))
    assert unsigned[0]["signed"] is False, "an unsigned index is read"


def test_a_signature_that_does_not_verify_is_refused(keys):
    repo = Repo(BASE)
    repo.add("dm")
    signed = _sign(repo.index())
    for bad in (signed.replace(b'"version": 1', b'"version": 1 '),       # the body moved
                signed.replace(b'"dm"', b'"dx"'),
                _sign(repo.index(), d=TEST_D + 2)):                       # another key
        with pytest.raises(ci.InstallError) as exc:
            ci.parse_index(bad, repo.url("index.json"))
        assert exc.value.text == ci.BAD_SIGNATURE


def test_a_sig_anywhere_but_last_is_refused(keys):
    doc = json.dumps({"version": 1, "sig": "ab", "carts": []}).encode()
    with pytest.raises(ci.InstallError) as exc:
        ci.parse_index(doc, "https://x/index.json")
    assert exc.value.text == ci.BAD_SIGNATURE


def test_the_signer_signs_in_place_and_signs_again_over_its_own_signature():
    data = b'{"version": 1, "carts": []}\n'
    once = _sign(data)
    assert ota_sign.index_unsigned(once) == data.rstrip()
    assert _sign(once) == once


# -- the sources --------------------------------------------------------------------


def test_sources_are_one_url_a_line(tmp_path):
    root = str(tmp_path / "carts")
    os.makedirs(root)
    assert ci.load_indexes(root) == list(ci.DEFAULT_INDEXES)
    ci.save_sources(root, ["https://a.example/index.json", "http://10.0.0.2/i.json"])
    assert (tmp_path / "system" / "sources").read_text() == \
        "https://a.example/index.json\nhttp://10.0.0.2/i.json\n"
    assert ci.load_indexes(root) == ["https://a.example/index.json",
                                     "http://10.0.0.2/i.json"]
    ci.save_sources(root, [])
    assert ci.load_indexes(root) == list(ci.DEFAULT_INDEXES)


# -- depends ------------------------------------------------------------------------


def _entry(cid, depends=None):
    repo = Repo(BASE)
    e = repo.add(cid)
    if depends is not None:
        e["depends"] = depends
    return e


def test_depends_resolve_first_and_once():
    a, b, c, d = (_entry("a", ["b", "c"]), _entry("b", ["d"]), _entry("c", ["d"]),
                  _entry("d"))
    got = ci.needs([a, b, c, d], a, set())
    assert [x["id"] for x in got] == ["d", "b", "c", "a"]
    assert [x["id"] for x in ci.needs([a, b, c, d], a, {"d", "c"})] == ["b", "a"]


def test_a_missing_or_circular_dependency_is_refused():
    a, b = _entry("a", ["b"]), _entry("b", ["a"])
    with pytest.raises(ci.InstallError) as exc:
        ci.needs([a], a, set())
    assert "needs b" in exc.value.text
    with pytest.raises(ci.InstallError) as exc:
        ci.needs([a, b], a, set())
    assert exc.value.text == "It depends on itself."
    assert pkg.depends_why([a, b], a, {"b"}) is None
    assert pkg.depends_why([a, _entry("b")], a, set()) == "Get B first."


def test_an_entry_whose_depends_is_not_a_list_of_ids_is_left_out():
    for bad in ("b", [1], [""], {"b": 1}):
        e = _entry("a", bad)
        assert ci.check_cart(e) == "depends", bad
    assert ci.check_cart(_entry("a", [])) is None
    assert ci.check_cart(_entry("a")) is None


# -- the fit ------------------------------------------------------------------------


class Inst:
    """The install role's server, bound, over a host folder."""

    def __init__(self, root, net=None, chip=("esp32s3", 2), fit=None, shelf=()):
        self._root = root
        self._net = net
        self._chip = chip
        self._fit = fit
        self.shelf = list(shelf)
        self.prefs = {}
        self._consent = app_perms.Consent(self)
        self.held = 0
        self.rescans = []

    # the consent store
    def get(self, key, default=None):
        return self.prefs.get(key, default)

    def set(self, key, value):
        self.prefs[key] = value

    def net(self):
        return self._net

    def hold(self):
        self.held += 1
        return True

    def release(self):
        self.held -= 1

    def root(self):
        return self._root

    def writable(self):
        return True

    def op(self, fn):
        return fn()

    def chip(self):
        return self._chip

    def runtimes(self):
        return ("lua", "wasm")

    def fit(self, runtime, pages, module_len, interp):
        return self._fit

    def free(self):
        return None

    def memory(self):
        return None

    def keep(self):
        return None

    def consent(self):
        return self._consent

    def find(self, folder):
        return {"id": folder[:-4], "path": self._root + "/" + folder} \
            if os.path.isdir(self._root + "/" + folder) else None

    def rescan(self, folder=None):
        self.rescans.append(folder)

    def carts(self):
        return self.shelf


def test_a_compiled_cart_with_nothing_for_this_chip_is_refused(tmp_path):
    e = _entry("dm")
    inst = Inst(str(tmp_path))
    assert pkg.fit_why(inst, e, ci.plan(e, "esp32s3", 2)) is None
    for fn in [f for f in e["assets"][0]["files"] if f.endswith(".aot") or f == "main.wasm"]:
        del e["assets"][0]["files"][fn]
    e["assets"][0]["files"]["main.esp32p4.f2.aot"] = {"size": 9, "sha256": "0" * 64}
    assert pkg.fit_why(inst, e, ci.plan(e, "esp32s3", 2)) == \
        "It isn't built for this console's chip (esp32s3)."


def test_the_engines_fit_and_the_shares_refuse_a_cart_too_big(tmp_path, monkeypatch):
    e = _entry("dm")
    p = ci.plan(e, "esp32s3", 2)
    roomy = Inst(str(tmp_path), fit=((3 << 20, 1 << 20), (8 << 20, 8 << 20)))
    tight = Inst(str(tmp_path), fit=((3 << 20, 1 << 20), (2 << 20, 8 << 20)))
    monkeypatch.setattr(pkg, "_board_share", lambda: None)
    assert pkg.fit_why(roomy, e, p) is None
    assert pkg.fit_why(tight, e, p) is not None, "the engine's own refusal"
    monkeypatch.setattr(pkg, "_board_share", lambda: 2 << 20)
    assert pkg.fit_why(roomy, e, p) == \
        "Too big for this console: it needs 3.0 MB, the share is 2.0 MB."
    assert pkg.blocker(roomy, e, p, None, True, pkg.fit_why(roomy, e, p)) == \
        pkg.fit_why(roomy, e, p)
    assert pkg.blocker(roomy, e, p, (1024, 512), True) .startswith("Not enough room")
    assert pkg.blocker(roomy, e, p, None, False) == \
        "This console can't play this kind of cart."


# -- the command name ------------------------------------------------------------------


def _stage(tmp_path, man):
    stage = tmp_path / "stage"
    stage.mkdir(exist_ok=True)
    (stage / "manifest.json").write_text(json.dumps(man))
    return str(stage)


def test_a_command_whose_name_is_taken_is_refused_at_install(tmp_path):
    cmd = {"format": "moy-1", "id": "moybyte.hello", "title": "Hello",
           "moybyte": {"type": "command"}}
    inst = Inst(str(tmp_path), shelf=[{"id": "moybyte.hello", "type": "command"}])
    aid, perms, _lines = pkg.install_check(inst, _stage(tmp_path, cmd), "hello.moy",
                                           inst.carts())
    assert perms == [], "an update of the command that holds the name"
    inst.shelf = [{"id": "local.hello", "type": "command"}]
    with pytest.raises(ci.InstallError) as exc:
        pkg.install_check(inst, _stage(tmp_path, cmd), "hello.moy", inst.carts())
    assert exc.value.text == "Its command name is taken: hello is local.hello's command"
    for name in ("ls", "pkg", "cd"):
        with pytest.raises(ci.InstallError) as exc:
            pkg.install_check(inst, _stage(tmp_path, dict(cmd, id="moybyte." + name)),
                              name + ".moy", inst.carts())
        assert exc.value.text == "Its command name is taken: the shell's own " + name
    game = dict(cmd, moybyte={"type": "game"})
    pkg.install_check(inst, _stage(tmp_path, dict(game, id="moybyte.ls")), "ls.moy", ())


def test_the_shells_own_names_are_the_c_tables():
    sh = ROOT / "native" / "moy_sh"
    dash = re.findall(r'\{\s*"([^"]+)",', (sh / "dash" / "builtins.c").read_text())
    core = (sh / "moy_sh_cmds.c").read_text()
    core = re.findall(r'\{ "([a-z]+)", \w+, BUILTIN_REGULAR \}',
                      core[core.index("core[] = {"):])
    board = (sh / "moy_sh_board.c").read_text()
    table = board[board.index("ROLES[] = {"):]
    roles = re.findall(r'"([a-z]+)"', table[:table.index("NULL")])
    assert dash and core and "pkg" in roles and "settings" in roles
    assert sorted(set(pkg.SHELL_NAMES)) == sorted(set(dash + core + roles))


# -- the verbs ----------------------------------------------------------------------


def _pkg(tmp_path, repo, **kw):
    root = str(tmp_path / "carts")
    os.makedirs(root, exist_ok=True)
    ci.save_sources(root, [repo.url("index.json")])
    inst = Inst(root, MemNet(repo.routes()), **kw)
    out, err = [], []
    asked = []

    def ask(q):
        asked.append(q)
        return kw.get("yes", False)
    return inst, pkg.Pkg(inst, out.append, err.append, ask=ask), out, err


def test_update_caches_list_reads_the_cache_and_install_lands(tmp_path):
    repo = Repo(BASE)
    repo.add("dm", name="Dungeon")
    repo.add("hop")
    inst, p, out, err = _pkg(tmp_path, repo)
    assert done(p.update()) == 0 and out == [repo.url("index.json") + ": 2 carts\n"]
    assert len(os.listdir(tmp_path / "pkg")) == 1
    inst._net = None                                   # list reads the cache alone
    del out[:]
    assert done(p.list()) == 0
    assert out == ["dm 1 - Dungeon\n", "hop 1 - Hop\n"]
    inst._net = MemNet(repo.routes())
    del out[:]
    assert done(p.install(["dm"], yes=True)) == 0, err
    assert (tmp_path / "carts" / "dm.moy" / "manifest.json").exists()
    assert out[0].startswith("Getting dm 1 (") and "dm 1 installed" in out[-1]
    assert [f.replace("\\", "/").rsplit("/", 1)[-1] for f in inst.rescans] == ["dm.moy"], \
        "an install re-reads the one folder it wrote, never the whole store"
    assert inst.held == 0, "the radio is let go"
    del out[:]
    assert done(p.list(["dun"])) == 0
    assert out == ["dm 1 installed Dungeon\n"]
    repo.add("dm", version=2, name="Dungeon")
    inst._net = MemNet(repo.routes())
    assert done(p.update()) == 0
    del out[:]
    assert done(p.upgrade(yes=True)) == 0 and "dm 2 installed" in out[-1]
    del out[:]
    del inst.rescans[:]
    assert done(p.remove(["dm"])) == 0 and out == ["dm removed\n"]
    assert not (tmp_path / "carts" / "dm.moy").exists()
    assert inst.rescans == ["dm.moy"], "a removal re-reads the folder it took away"


class _WorkerReq:
    """The http worker's request (runtime/system/dev_http.py's Request and
    Stream): no answer for its first polls, then its body, a stream's a few
    bytes a poll."""

    def __init__(self, body, cap=None, trickle=None):
        self.body = body[:cap] if body is not None and cap else body
        self.polls = 0
        self.trickle = trickle
        self.at = 0

    def _in(self):
        if self.trickle is None:
            return len(self.body)
        return min(len(self.body), max(0, self.polls - 2) * self.trickle)

    def state(self):
        self.polls += 1
        if self.polls < 3:
            return None
        if self.body is None:
            return (404, 0)
        return (200, None if self.trickle else len(self.body))

    def available(self):
        return self._in() - self.at

    def ended(self):
        return self._in() == len(self.body)

    def readinto(self, mv, off=None):
        if off is None:
            off = self.at
        got = self.body[off:min(off + len(mv), self._in())]
        mv[:len(got)] = got
        self.at = off + len(got)
        return len(got)

    def close(self):
        pass


class _HttpWorker:
    CAP_MAX = 256 * 1024

    def __init__(self, routes):
        self.routes = routes
        self.got = []
        self.streamed = []

    def get(self, url, cap):
        self.got.append(url)
        return _WorkerReq(self.routes.get(url), cap)

    def stream(self, url, window):
        # the window holds a reader's whole gate, or a large archive's
        # member never opens (a board's install hung on exactly that)
        assert ci.GATE < window <= self.CAP_MAX
        self.streamed.append(url)
        return _WorkerReq(self.routes.get(url), trickle=97)


def test_an_install_s_downloads_are_the_http_worker_s_and_never_block(tmp_path):
    """Where the transport has the http worker (cart_index.Worker: a board's
    install role, Get Carts' and pkg's alike) the index is fetched whole and
    a download of any size streams through the worker's window, a few bytes
    a poll, off the VM's thread: the transport's blocking `open` (a TLS
    handshake and reads on the frame) is never called."""
    repo = Repo(BASE)
    repo.add("dm", name="Dungeon")
    root = str(tmp_path / "carts")
    os.makedirs(root, exist_ok=True)
    ci.save_sources(root, [repo.url("index.json")])
    mem, http = MemNet(repo.routes()), _HttpWorker(repo.routes())
    inst = Inst(root, ci.Worker(mem, http))
    out, err = [], []
    p = pkg.Pkg(inst, out.append, err.append, ask=lambda q: True)
    assert done(p.update()) == 0, err
    assert done(p.install(["dm"], yes=True)) == 0, err
    assert (tmp_path / "carts" / "dm.moy" / "manifest.json").exists()
    assert mem.opened == [], "nothing went through the blocking open"
    assert http.streamed and http.got, (http.got, http.streamed)


def test_the_install_role_s_transport_is_the_worker_s_where_https_verifies(tmp_path,
                                                                          monkeypatch):
    """One transport for Get Carts and `pkg`: the install role's `net()`
    (runtime/shell/shell_servers.py) wraps the console's in the http worker
    where its https verifies, and hands the console's own where it does not."""
    from runtime import devices
    from runtime.shell_servers import InstallServer
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    http = _HttpWorker({})
    http.tls = lambda: True
    monkeypatch.setattr(devices, "api", lambda name: http if name == "http" else None)
    ws.cart_net = MemNet({})
    net = InstallServer(ws).net(None)
    assert isinstance(net, ci.Worker) and net.online() is True
    net.get("u", 10)
    net.stream("v", ci.WINDOW)
    assert http.got == ["u"] and http.streamed == ["v"]
    http.tls = lambda: False
    ws.cart_net = MemNet({})
    assert InstallServer(ws).net(None) is ws.cart_net


def test_install_brings_its_depends_first_and_refuses_what_it_cannot(tmp_path):
    repo = Repo(BASE)
    repo.add("base")
    e = repo.add("game")
    e["depends"] = ["base"]
    repo.files["index.json"] = repo.index()
    inst, p, out, err = _pkg(tmp_path, repo)
    assert done(p.install(["game"], yes=True)) == 0, err
    assert [ln.split()[1] for ln in out if ln.startswith("Getting")] == ["base", "game"]
    e["depends"] = ["nowhere"]
    repo.files["index.json"] = repo.index()
    inst2, p2, _out, err2 = _pkg(tmp_path / "b", repo)
    assert done(p2.install(["game"], yes=True)) == 1
    assert err2 == ["game: It needs nowhere, which no cart shelf has."]
    assert done(p2.install(["nothing"])) == 1 and err2[-1] == "nothing: no cart shelf lists it"


def test_a_no_at_the_prompt_installs_nothing(tmp_path):
    repo = Repo(BASE)
    repo.add("cam", moybyte={"permissions": ["camera"]})
    inst, p, out, err = _pkg(tmp_path, repo)
    inst._consent.check = lambda cart, install=False: ([], [], None)
    assert done(p.install(["cam"])) == 1
    assert not (tmp_path / "carts" / "cam.moy").exists()
    assert inst.prefs == {}, "no consent recorded"


class _Units:
    """What the store does that holds a frame -- a folder made, renamed or
    deleted, a file deleted, the record or a consent written, the shelf
    re-read; the record's backup and its publish each count -- between the
    verb's frames (its bare yields)."""

    SLOW = (("os", ("mkdir", "rename", "remove", "rmdir")),
            ("shelf", ("shelf_take",)), ("fs", ("_publish_bak", "_publish_mark")),
            ("ci", ("save_record", "_write_atomic")), ("carts", ("adopt",)))

    def __init__(self, monkeypatch, inst):
        self.now = 0
        self.frames = []
        self.depth = 0
        from runtime import moy_fs
        mods = {"os": os, "shelf": ci._shelf, "ci": ci, "carts": ci._moy_carts(),
                "fs": moy_fs}
        for mod, names in self.SLOW:
            for name in names:
                monkeypatch.setattr(mods[mod], name, self._unit(getattr(mods[mod], name)))
        for name in ("set", "rescan"):
            setattr(inst, name, self._unit(getattr(inst, name)))

    def _unit(self, fn):
        def counted(*a, **kw):
            self.depth += 1
            try:
                return fn(*a, **kw)
            finally:
                self.depth -= 1
                if self.depth == 0:
                    self.now += 1
        return counted

    def run(self, verb):
        """`verb` stepped to its end, a frame a bare yield (the door's
        crossing ends there): its exit code."""
        self.now = 0
        while True:
            try:
                got = next(verb)
            except StopIteration as e:
                self.frames.append(self.now)
                return e.value
            if not got:
                self.frames.append(self.now)
                self.now = 0


def test_install_and_remove_hold_no_frame_for_more_than_one_store_unit(tmp_path, monkeypatch):
    """`pkg install` and `pkg remove` step their slow store work a unit a
    frame -- the staging folder made, the build moved in, the old copy set
    aside and deleted, the record, the consent, the shelf's re-read -- where
    a frame used to hold all of it (380 ms on the T-Deck); Get Carts steps
    the same generators (test_cart_store)."""
    repo = Repo(BASE)
    repo.add("dm", name="Dungeon", moybyte={"permissions": ["internet"]})
    inst, p, out, err = _pkg(tmp_path, repo)
    units = _Units(monkeypatch, inst)
    assert units.run(p.install(["dm"], yes=True)) == 0, err
    assert max(units.frames) <= 1, units.frames
    assert sum(units.frames) >= 5, "the staging (2), the move, the record, the consent, " \
        "the re-read: %r" % units.frames
    assert inst.prefs, "the consent is recorded"
    repo.add("dm", version=2, name="Dungeon", moybyte={"permissions": ["internet"]})
    inst._net = MemNet(repo.routes())
    assert done(p.update()) == 0
    (tmp_path / "carts" / "dm.moy" / "pmem.json").write_text("{}")
    units.frames = []
    assert units.run(p.upgrade(yes=True)) == 0, err
    assert max(units.frames) <= 1, units.frames
    assert (tmp_path / "carts" / "dm.moy" / "pmem.json").exists(), "the saves are carried"
    assert not os.listdir(tmp_path / "install"), "the old copy is deleted"
    units.frames = []
    assert units.run(p.remove(["dm"])) == 0
    assert max(units.frames) <= 1, units.frames
    assert not (tmp_path / "carts" / "dm.moy").exists() and not inst.prefs["consent"]
    assert not os.listdir(tmp_path / "install")
    assert ci.load_record(inst.root()) == {}


def test_a_landing_or_a_removal_stopped_partway_finishes(tmp_path):
    """Ctrl-C (the door closes the verb) never leaves a cart half moved or
    half removed: what was left of the steps is done at once."""
    repo = Repo(BASE)
    repo.add("dm", name="Dungeon")
    inst, p, out, err = _pkg(tmp_path, repo)
    assert done(p.install(["dm"], yes=True)) == 0, err
    verb = p.remove(["dm"])
    for _ in range(3):
        next(verb)
    assert (tmp_path / "install").exists() and os.listdir(tmp_path / "install")
    verb.close()
    assert not (tmp_path / "carts" / "dm.moy").exists()
    assert not os.listdir(tmp_path / "install") and inst.rescans[-1] == "dm.moy"
    assert ci.load_record(inst.root()) == {}


def test_ctrl_c_mid_landing_finishes_the_landing_and_what_follows_it(tmp_path, monkeypatch):
    """Ctrl-C while the build lands: the cart lands (Install.finish) and so do
    the consent it was installed with and the shelf's re-read (`land_now`)."""
    repo = Repo(BASE)
    repo.add("dm", name="Dungeon", moybyte={"permissions": ["internet"]})
    inst, p, out, err = _pkg(tmp_path, repo)
    jobs = []
    orig = ci.Install.step

    def step(self, *a, **kw):
        jobs.append(self)
        return orig(self, *a, **kw)
    monkeypatch.setattr(ci.Install, "step", step)
    verb = p.install(["dm"], yes=True)
    for _ in range(500):
        next(verb)
        if jobs and jobs[-1].landing:
            break
    assert jobs[-1].landing and not jobs[-1].finished
    verb.close()                                       # Ctrl-C
    assert "dm.moy" in ci.load_record(inst.root())
    assert inst.prefs.get("consent"), "the consent is recorded"
    assert [f.replace("\\", "/").rsplit("/", 1)[-1] for f in inst.rescans] == ["dm.moy"], \
        "the shelf re-read it"


def test_a_save_made_while_an_upgrade_lands_goes_with_the_saves(tmp_path, monkeypatch):
    """The saves are carried in the op that sets the old copy aside, so one
    the running cart makes a frame into the landing is not left behind."""
    repo = Repo(BASE)
    repo.add("dm", name="Dungeon")
    inst, p, out, err = _pkg(tmp_path, repo)
    assert done(p.install(["dm"], yes=True)) == 0, err
    saves = tmp_path / "carts" / "dm.moy" / ci.SAVES
    saves.write_text('{"level": 1}')
    repo.add("dm", version=2, name="Dungeon")
    inst._net = MemNet(repo.routes())
    assert done(p.update()) == 0
    orig = ci.Install._carry_config

    def carry_config(self, old):
        saves.write_text('{"level": 2}')               # the running cart saves
        return orig(self, old)
    monkeypatch.setattr(ci.Install, "_carry_config", carry_config)
    assert done(p.upgrade(yes=True)) == 0, err
    assert saves.read_text() == '{"level": 2}'


def test_a_bad_signature_is_refused_by_update(tmp_path, keys):
    repo = Repo(BASE)
    repo.add("dm")
    repo.files["index.json"] = _sign(repo.index()).replace(b'"dm"', b'"dx"')
    inst, p, out, err = _pkg(tmp_path, repo)
    assert done(p.update()) == 1 and err == [repo.url("index.json") + ": " + ci.BAD_SIGNATURE]
    assert not (tmp_path / "pkg").exists(), "nothing cached"
    repo.files["index.json"] = _sign(repo.index())
    inst._net = MemNet(repo.routes())
    assert done(p.update()) == 0 and out[-1].endswith(": 1 carts, signed\n")


# -- the commands on a host console -------------------------------------------------------


class IO:
    def __init__(self, stdin=b""):
        self.stdin = stdin
        self.fds = {1: b"", 2: b""}

    def write(self, fd, data):
        self.fds[fd] += bytes(data)
        return len(data)

    def read(self, fd, n):
        got, self.stdin = self.stdin[:n], self.stdin[n:]
        return got


def done(verb):
    """A verb (a generator: runtime/carts/pkg.py) stepped to its end."""
    try:
        while True:
            next(verb)
    except StopIteration as e:
        return e.value


def _role(ws, *argv, stdin=b""):
    from runtime.sh_door import RUNNING, ShellDoor
    io = IO(stdin)
    d = ShellDoor(ws, io)
    code = d.v_role("t", *argv)
    while code == RUNNING:
        code = d.v_step("t")
    return code, io.fds[1].decode(), io.fds[2].decode()


def test_the_settings_command_reads_and_writes_the_file(tmp_path):
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    code, out, _err = _role(ws, "settings", "set", "name", "Ada", "Lovelace")
    assert code == 0 and ws.system.get("name") == "Ada Lovelace"
    assert _role(ws, "settings", "set", "show_fps", "true")[0] == 0
    assert ws.show_fps is True, "applied at once"
    assert _role(ws, "settings", "get", "name") == (0, '"Ada Lovelace"\n', "")
    path = moy_carts.system_store_path(ws.carts_root)
    with open(path) as f:
        text = f.read()
    assert 'name = "Ada Lovelace"\n' in text and "show_fps = true\n" in text
    assert _role(ws, "settings")[1] == text
    assert _role(ws, "settings", "unset", "name")[0] == 0
    assert _role(ws, "settings", "get", "name")[:2] == (1, "")
    with open(path, "a") as f:
        f.write('name = "Grace"\n')
    assert _role(ws, "settings", "reload")[0] == 0
    assert ws.system.get("name") == "Grace"
    assert _role(ws, "settings", "frob")[0] == 2


def test_pkg_install_through_the_shells_door(tmp_path):
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    repo = Repo(BASE)
    repo.add("dm", runtime="lua", modules=())
    ci.save_sources(ws.carts_root, [repo.url("index.json")])
    ws.cart_net = MemNet(repo.routes())
    code, out, err = _role(ws, "pkg", "sources")
    assert (code, out) == (0, repo.url("index.json") + "\n")
    code, out, err = _role(ws, "pkg", "install", "dm", stdin=b"y\n")
    assert code == 0, err
    assert "dm 1 installed" in out
    assert any(c.get("id") == "dm" or str(c.get("path", "")).endswith("/dm.moy")
               for c in ws.carts.all), "the shelf was scanned again"
    code, out, err = _role(ws, "pkg", "list")
    assert out == "dm 1 installed Dm\n"
    assert _role(ws, "pkg", "remove", "dm")[0] == 0
    assert _role(ws, "pkg", "frob")[0] == 2
