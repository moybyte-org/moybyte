"""The cart store (#124) in the shape the browser gives it, on the host.

A page's network is NON-BLOCKING: its bytes arrive between frames, while no
Python runs, so `runtime/cart_index.py` must give the frame back instead of
waiting, and read only what the transport vouches for. The page can read only
what CORS lets it, so it reads a release asset's mirror beside the index and
asks the player for an external file whose hosts it cannot read. Its store of
record is OPFS, not the files an install writes, so an install goes to a
KEEPER before it moves into place. And a page a board serves gets no carts of
its own at all.

Everything here runs on CPython against fakes of those three seams, plus the
Python half of the real bridge (`firmware/web_runner/carts_link.py`) over a
real spool file. The JavaScript halves are `store_test.mjs` (the OPFS commit)
and `tests/test_web_store_e2e.py` (all of it in Chrome).
"""

import json
import os
import sys
from pathlib import Path

import pytest

from cart_store_fixtures import MemNet, Repo, sha
from runtime import cart_index as ci

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://pages.example/carts"
GITHUB = "https://github.example/o/carts/releases/download"


class _Trickle:
    """One answer of a non-blocking transport. Nothing arrives until the
    transport `tick`s (a frame boundary); then the head, then `chunk` bytes a
    tick. Reading anything `ready` did not vouch for fails the test."""

    def __init__(self, body, status, chunk):
        self.body = body
        self.want_status = status
        self.chunk = chunk
        self.status = None
        self.length = None
        self.got = 0
        self.pos = 0
        self.closed = False

    def tick(self):
        if self.closed:
            return
        if self.status is None:
            self.status = self.want_status
            return
        if self.status in (200, 206):
            self.got = min(len(self.body), self.got + self.chunk)

    def _ended(self):
        return self.status is not None and (self.status not in (200, 206)
                                            or self.got == len(self.body))

    def ready(self, n):
        if self.status is None:
            return False
        return self._ended() or self.got - self.pos >= n

    def readinto(self, buf):
        have = self.got - self.pos
        if have <= 0:
            assert self._ended(), "read past what ready() vouched for"
            return 0
        n = min(len(buf), have)
        buf[:n] = self.body[self.pos:self.pos + n]
        self.pos += n
        return n

    def close(self):
        self.closed = True


class Trickle:
    """A non-blocking transport over `{url: bytes}`. `cors` makes it the
    browser's: a URL in `unreadable` answers status 0, as a host with no CORS
    header does to a page."""

    def __init__(self, routes, chunk=5000, cors=False, unreadable=()):
        self.routes = dict(routes)
        self.chunk = chunk
        self.cors = cors
        self.unreadable = set(unreadable)
        self.opened = []
        self.live = []

    def online(self):
        return True

    def open(self, url):
        self.opened.append(url)
        if url in self.unreadable:
            r = _Trickle(b"", 0, self.chunk)
        elif url not in self.routes:
            r = _Trickle(b"not found", 404, self.chunk)
        else:
            r = _Trickle(self.routes[url], 200, self.chunk)
        self.live.append(r)
        return r

    def tick(self):
        for r in self.live:
            r.tick()


class Ranged(Trickle):
    """The browser's transport, which can also ask for part of a file
    (`span`): a host answers 206 with those bytes, or -- `ignore` -- the
    whole file, or -- `refuse` -- nothing a page may read (status 0, a
    preflight a host turned down). `spans` is every range asked for."""

    ranges = True

    def __init__(self, routes, ignore=False, refuse=False, **kw):
        Trickle.__init__(self, routes, **kw)
        self.ignore = ignore
        self.refuse = refuse
        self.spans = []

    def open(self, url, span=None):
        if span is None:
            return Trickle.open(self, url)
        self.opened.append(url)
        self.spans.append((url, span[0], span[1]))
        if self.refuse or url in self.unreadable:
            r = _Trickle(b"", 0, self.chunk)
        elif url not in self.routes:
            r = _Trickle(b"not found", 404, self.chunk)
        elif self.ignore:
            r = _Trickle(self.routes[url], 200, self.chunk)
        else:
            r = _Trickle(self.routes[url][span[0]:span[0] + span[1]], 206, self.chunk)
        self.live.append(r)
        return r


def _store(tmp_path):
    root = tmp_path / "moybyte" / "carts"
    root.mkdir(parents=True)
    return str(root).replace("\\", "/")


def _session(fn):
    return fn()


def _cart(repo, cid):
    return next(c for c in ci.parse_index(repo.index(), repo.url("index.json"))
                if c["id"] == cid)


def _run(job, net=None, limit=100000):
    """Step `job` to its end, ticking a non-blocking transport between steps
    -- the frame boundary where a page's bytes land. Returns the steps taken."""
    n = 0
    while job.step():
        n += 1
        assert n < limit, "the job never finished"
        if net is not None and hasattr(net, "tick"):
            net.tick()
    return n


def _untouched(root, folder):
    base = Path(root)
    assert not (base / folder).exists(), "a failed install left a cart folder"
    stage = base.parent / ci.STAGE_DIR
    assert not stage.exists() or not os.listdir(stage), os.listdir(stage)
    assert folder not in ci.load_record(root)


# -- the mirror ---------------------------------------------------------------------

def test_a_cors_transport_reads_the_mirror_first_and_every_other_falls_back():
    asset = {"url": GITHUB + "/a.zip", "mirror": "releases/v1/a.zip"}
    assert ci.asset_urls(asset, cors=True) == ["releases/v1/a.zip", GITHUB + "/a.zip"]
    assert ci.asset_urls(asset) == [GITHUB + "/a.zip", "releases/v1/a.zip"]
    assert ci.asset_urls({"url": GITHUB + "/a.zip"}, cors=True) == [GITHUB + "/a.zip"]


@pytest.mark.parametrize("bad", ["../a.zip", "/abs/a.zip", "https://x.example/a.zip",
                                 "a\\b.zip", "", 7, "a//b.zip"])
def test_a_mirror_outside_the_site_is_left_out_and_the_cart_stays(bad):
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True)
    repo.carts[0]["assets"][0]["mirror"] = bad
    carts = ci.parse_index(repo.index(), repo.url("index.json"))
    assert [c["id"] for c in carts] == ["jet"]
    assert "mirror" not in carts[0]["assets"][0]
    assert "mirror" in repo.carts[0]["assets"][0], "the reader edited its input"


def test_the_browser_installs_from_the_mirror_and_never_asks_the_release(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True)
    cart = _cart(repo, "jet")
    release = cart["assets"][0]["url"]
    net = Trickle(repo.routes(), cors=True, unreadable=[release])
    job = ci.Install(cart, ci.plan(cart), net, root, _session)
    _run(job, net)
    assert job.error is None, job.detail
    assert net.opened == [repo.url(cart["assets"][0]["mirror"])]
    assert sorted(os.listdir(job.path)) == sorted(ci.plan(cart)["files"])


def test_a_board_falls_back_to_the_mirror_when_the_release_will_not_answer(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True)
    cart = _cart(repo, "jet")
    routes = repo.routes()
    del routes[cart["assets"][0]["url"]]
    net = MemNet(routes)
    job = ci.Install(cart, ci.plan(cart, "esp32s3", 2), net, root, _session)
    _run(job)
    assert job.error is None, job.detail
    assert net.opened == [cart["assets"][0]["url"], repo.url(cart["assets"][0]["mirror"])]


def test_a_browser_that_can_read_neither_says_it_cannot_reach_the_shelf(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB)                  # no mirror at all
    cart = _cart(repo, "jet")
    net = Trickle(repo.routes(), cors=True, unreadable=[cart["assets"][0]["url"]])
    job = ci.Install(cart, ci.plan(cart), net, root, _session)
    _run(job, net)
    assert job.error == ci.UNREACHABLE and "HTTP 0" in job.detail
    _untouched(root, "jet.moy")


# -- a ranged read: only the members this console keeps ------------------------------

def _zip_members(z):
    """{name: (header start, data end)} of a stored zip's members."""
    import zipfile
    import io
    out = {}
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        for info in zf.infolist():
            h = info.header_offset
            n = int.from_bytes(z[h + 26:h + 28], "little")
            x = int.from_bytes(z[h + 28:h + 30], "little")
            out[info.filename] = (h, h + 30 + n + x + info.compress_size)
    return out


@pytest.mark.parametrize("chunk", [1, 777, 1 << 20])
def test_the_browser_reads_only_the_members_it_keeps(tmp_path, chunk):
    """A console with no compiled tier keeps main.wasm and the cart's files,
    never a chip's module: it reads the zip's directory and then the runs of
    members it keeps, and no byte of a module crosses. What lands is byte for
    byte what the whole read installs, every member checked."""
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True, cover=b"\x89PNG fake cover",
             extra={"assets.bin": os.urandom(40000)})
    cart = _cart(repo, "jet")
    asset = cart["assets"][0]
    mirror = repo.url(asset["mirror"])
    z = repo.files[asset["mirror"]]
    p = ci.plan(cart, None, None, ranges=True)
    assert not [fn for fn in p["files"] if fn.endswith(".aot")]
    assert p["download_bytes"] < asset["size"]
    root = _store(tmp_path)
    net = Ranged(repo.routes(), cors=True, unreadable=[asset["url"]], chunk=chunk)
    job = ci.Install(cart, p, net, root, _session, step_ms=1000)
    _run(job, net, limit=10 ** 7)
    assert job.error is None, job.detail
    assert set(net.opened) == {mirror}, net.opened
    members = _zip_members(z)
    tail = net.spans[0]
    assert tail[1] + tail[2] == len(z) and tail[2] <= ci._cd_bytes(cart, asset) \
        + ci.TAIL_SLACK
    for url, start, n in net.spans[1:]:
        for name, (a, b) in members.items():
            if name.endswith(".aot"):
                assert start + n <= a or start >= b, "a module's bytes were fetched"
    assert job.done == job.total == p["download_bytes"]
    whole = Path(_store(tmp_path / "w"))
    _run(ci.Install(cart, ci.plan(cart), MemNet(repo.routes()), str(whole), _session))
    assert sorted(os.listdir(job.path)) == sorted(p["files"])
    for fn in p["files"]:
        assert (Path(job.path) / fn).read_bytes() == (whole / "jet.moy" / fn).read_bytes()
    assert ci.load_record(root)["jet.moy"]["files"] == ci.record_entry(cart, p)["files"]


@pytest.mark.parametrize("how", ["ignore", "refuse"])
def test_a_host_that_gives_no_range_gets_the_asset_read_whole(tmp_path, how):
    """A server that sends the whole file for a range, or a range the page
    may not send it: the asset goes back to be read whole, from every URL,
    and installs exactly as it would have."""
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True)
    cart = _cart(repo, "jet")
    asset = cart["assets"][0]
    root = _store(tmp_path)
    net = Ranged(repo.routes(), cors=True, unreadable=[asset["url"]],
                 **{how: True})
    p = ci.plan(cart, None, None, ranges=True)
    job = ci.Install(cart, p, net, root, _session)
    _run(job, net)
    assert job.error is None, job.detail
    assert len(net.spans) == 1
    assert net.opened == [repo.url(asset["mirror"])] * 2
    assert job.done == job.total
    assert sorted(os.listdir(job.path)) == sorted(p["files"])


def test_a_member_whose_bytes_are_wrong_lands_nothing_from_a_ranged_read(tmp_path):
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True)
    cart = _cart(repo, "jet")
    asset = cart["assets"][0]
    z = bytearray(repo.files[asset["mirror"]])
    a, b = _zip_members(bytes(z))["jet.moy/main.wasm"]
    z[b - 1] ^= 0xFF
    routes = repo.routes()
    routes[repo.url(asset["mirror"])] = bytes(z)
    root = _store(tmp_path)
    net = Ranged(routes, cors=True, unreadable=[asset["url"]])
    job = ci.Install(cart, ci.plan(cart, None, None, True), net, root, _session)
    _run(job, net)
    assert job.error == ci.MISMATCH and "main.wasm" in job.detail, job.detail
    _untouched(root, "jet.moy")


def test_a_directory_longer_than_its_names_is_fetched_from_where_it_begins(tmp_path):
    """A zip comment longer than the slack pushes the directory out of the
    first range; the read asks again from where the end record says it
    begins, and installs."""
    repo = Repo(BASE)
    repo.add("jet", release=GITHUB, mirror=True)
    cart = _cart(repo, "jet")
    asset = cart["assets"][0]
    z = bytearray(repo.files[asset["mirror"]])
    comment = b"c" * (ci.TAIL_SLACK + 500)
    z[-2:] = len(comment).to_bytes(2, "little")
    z += comment
    z = bytes(z)
    asset["size"], asset["sha256"] = len(z), sha(z)
    routes = repo.routes()
    routes[repo.url(asset["mirror"])] = z
    root = _store(tmp_path)
    net = Ranged(routes, cors=True, unreadable=[asset["url"]])
    job = ci.Install(cart, ci.plan(cart, None, None, True), net, root, _session)
    _run(job, net)
    assert job.error is None, job.detail
    assert net.spans[0][1] + net.spans[0][2] == len(z)
    assert net.spans[1][1] + net.spans[1][2] == len(z) and net.spans[1][2] > net.spans[0][2]
    assert job.done == job.total


def test_a_cart_whose_every_member_is_kept_is_read_whole(tmp_path):
    repo = Repo(BASE)
    repo.add("tune", runtime="lua", modules=(), release=GITHUB, mirror=True)
    cart = _cart(repo, "tune")
    p = ci.plan(cart, None, None, ranges=True)
    assert p["download_bytes"] == cart["assets"][0]["size"]
    net = Ranged(repo.routes(), cors=True, unreadable=[cart["assets"][0]["url"]])
    job = ci.Install(cart, p, net, _store(tmp_path), _session)
    _run(job, net)
    assert job.error is None and net.spans == []


# -- a non-blocking transport ---------------------------------------------------------

@pytest.mark.parametrize("chunk", [1, 333, 5000, 1 << 20])
def test_an_install_over_a_non_blocking_transport_waits_between_frames(tmp_path, chunk):
    """Every byte arrives between steps, a few at a time; the pipeline reads
    only what `ready` vouched for (the fake fails the test otherwise) and the
    result is byte for byte what a blocking transport installs."""
    repo = Repo(BASE)
    wad = os.urandom(30000)
    repo.add("dm", external=[("game.wad", wad, "pkg/game.wad")],
             extra={"big.bin": os.urandom(70000)})
    cart = _cart(repo, "dm")
    p = ci.plan(cart, "esp32p4", 2)
    root = _store(tmp_path)
    net = Trickle(repo.routes(), chunk=chunk)
    job = ci.Install(cart, p, net, root, _session, ["game.wad"], step_ms=1000)
    steps = _run(job, net, limit=10 ** 7)
    assert job.error is None, job.detail
    assert steps > 2, "a non-blocking transport cannot finish in one step"
    assert (Path(job.path) / "game.wad").read_bytes() == wad
    other = Path(_store(tmp_path / "b"))
    job2 = ci.Install(cart, p, MemNet(repo.routes()), str(other), _session, ["game.wad"])
    _run(job2)
    for fn in p["files"]:
        assert (Path(job.path) / fn).read_bytes() == (other / "dm.moy" / fn).read_bytes()


def test_a_fetch_gives_the_frame_back_until_its_bytes_are_in():
    data = os.urandom(12000)
    net = Trickle({"u": data}, chunk=5000)
    job = ci.Fetch(net, "u", 20000, len(data), sha(data))
    assert job.step() is True                    # no head yet
    steps = _run(job, net)
    assert job.data == data and steps >= 3
    small = Trickle({"u": data})
    with pytest.raises(ci.InstallError) as exc:
        _run(ci.Fetch(small, "u", 100), small)
    assert exc.value.text == ci.MISMATCH
    assert small.live[0].closed


def test_reach_names_the_first_host_this_console_can_read():
    net = Trickle({"b": b"x", "c": b"y"}, unreadable=["a"])
    r = ci.Reach(net, ["a", "b", "c"])
    _run(r, net)
    assert r.url == "b" and net.opened == ["a", "b"]
    assert all(x.closed for x in net.live)
    none = ci.Reach(Trickle({}, unreadable=["a"]), ["a", "missing"])
    _run(none, none.net)
    assert none.url is None and len(none.why) == 2


# -- a file the player supplies -------------------------------------------------------

def test_a_supplied_file_is_checked_in_slices_and_named_when_wrong(tmp_path):
    data = os.urandom(300000)
    f = tmp_path / "doom1.wad"
    f.write_bytes(data)
    ok = ci.FileCheck(str(f), len(data), sha(data))
    ok.CHUNK = 65536
    while ok.step(budget_ms=0):
        pass
    assert ok.why is None and ok.done == len(data)
    short = ci.FileCheck(str(f), len(data) + 1, sha(data))
    _run(short)
    assert "is %d bytes, and the right one is %d" % (len(data), len(data) + 1) in short.why
    wrong = ci.FileCheck(str(f), len(data), "0" * 64)
    _run(wrong)
    assert "not the ones" in wrong.why
    missing = ci.FileCheck(str(tmp_path / "nope"), 1, "0" * 64)
    _run(missing)
    assert "could not be read" in missing.why


def test_a_supplied_external_file_installs_without_its_archive(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    wad = os.urandom(50000)
    repo.add("dm", external=[("game.wad", wad, "pkg/game.wad")])
    cart = _cart(repo, "dm")
    arc_url = cart["external"][0]["archive"]["urls"][0]
    net = Trickle(repo.routes(), cors=True, unreadable=[arc_url])
    mine = tmp_path / "picked"
    mine.write_bytes(wad)
    p = ci.plan(cart)
    job = ci.Install(cart, p, net, root, _session, ["game.wad"],
                     supplied={"game.wad": str(mine)})
    assert job.total == cart["assets"][0]["size"] + len(wad)
    _run(job, net)
    assert job.error is None, job.detail
    assert arc_url not in net.opened
    assert (Path(job.path) / "game.wad").read_bytes() == wad
    assert job.done == job.total


@pytest.mark.parametrize("how", ["wrong bytes", "short", "long"])
def test_a_wrong_supplied_file_leaves_the_shelf_as_it_was(tmp_path, how):
    root = _store(tmp_path)
    repo = Repo(BASE)
    wad = os.urandom(50000)
    repo.add("dm", external=[("game.wad", wad, "pkg/game.wad")])
    cart = _cart(repo, "dm")
    bad = {"wrong bytes": os.urandom(50000), "short": wad[:-1], "long": wad + b"!"}[how]
    mine = tmp_path / "picked"
    mine.write_bytes(bad)
    job = ci.Install(cart, ci.plan(cart), MemNet(repo.routes()), root, _session,
                     ["game.wad"], supplied={"game.wad": str(mine)})
    _run(job)
    assert job.error == ci.MISMATCH, job.detail
    _untouched(root, "dm.moy")


def test_a_supplied_file_still_needs_its_licence_accepted(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("dm", external=[("game.wad", b"w" * 100, "pkg/game.wad")])
    cart = _cart(repo, "dm")
    (tmp_path / "picked").write_bytes(b"w" * 100)
    job = ci.Install(cart, ci.plan(cart), MemNet(repo.routes()), root, _session, (),
                     supplied={"game.wad": str(tmp_path / "picked")})
    _run(job)
    assert job.error == ci.NO_LICENCE
    _untouched(root, "dm.moy")


# -- the keeper -------------------------------------------------------------------------

class Keeper:
    """A store of record that is not the files an install writes. `answer`
    None holds the commit until the test calls `finish`; otherwise the commit
    is answered at once with it."""

    def __init__(self, answer=("", False)):
        self.answer = answer
        self.commits = []
        self.landed_folders = []
        self.records = []
        self.pending = None

    def commit(self, folder, stage, record, done):
        files = dict((n, Path(stage, n).read_bytes()) for n in sorted(os.listdir(stage)))
        self.commits.append((folder, files, json.loads(record)))
        if self.answer is None:
            self.pending = done
        else:
            done(*self.answer)

    def finish(self, why="", full=False):
        done, self.pending = self.pending, None
        done(why, full)

    def landed(self, folder):
        self.landed_folders.append(folder)

    def record(self, text):
        self.records.append(json.loads(text))

    def free(self):
        return 10 ** 9, 1


def test_an_install_moves_into_place_only_once_the_keeper_has_it(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    cart = _cart(repo, "jet")
    p = ci.plan(cart)
    keep = Keeper(answer=None)
    job = ci.Install(cart, p, MemNet(repo.routes()), root, _session, keep=keep)
    for _ in range(5):
        assert job.step() is True
    assert job.keeping and not (Path(root) / "jet.moy").exists()
    folder, files, record = keep.commits[0]
    assert folder == "jet.moy" and sorted(files) == sorted(p["files"])
    for fn, meta in p["files"].items():
        assert sha(files[fn]) == meta["sha256"]
    assert record["carts"]["jet.moy"]["version"] == cart["version"]
    assert job.cancel() is False and job.error is None, "a kept build cannot be cancelled"
    keep.finish()
    assert job.step() is False and job.error is None
    assert job.path and (Path(root) / "jet.moy" / "main.wasm").exists()
    assert keep.landed_folders == ["jet.moy"]
    assert ci.load_record(root)["jet.moy"] == record["carts"]["jet.moy"]


def test_a_keeper_that_answers_at_once_finishes_in_the_same_step(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    cart = _cart(repo, "jet")
    keep = Keeper()
    job = ci.Install(cart, ci.plan(cart), MemNet(repo.routes()), root, _session, keep=keep)
    _run(job)
    assert job.path and not job.keeping and keep.landed_folders == ["jet.moy"]


@pytest.mark.parametrize("full", [True, False])
def test_a_keeper_that_cannot_keep_it_leaves_the_shelf_as_it_was(tmp_path, full):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    cart = _cart(repo, "jet")
    keep = Keeper(answer=("QuotaExceededError: no room", full))
    job = ci.Install(cart, ci.plan(cart), MemNet(repo.routes()), root, _session, keep=keep)
    _run(job)
    assert job.error == (ci.FULL if full else ci.NO_WRITE)
    assert keep.landed_folders == []
    _untouched(root, "jet.moy")


def test_an_update_hands_the_keeper_the_saves_it_carries(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet", version=1)
    ci_job = ci.Install(_cart(repo, "jet"), ci.plan(_cart(repo, "jet")),
                        MemNet(repo.routes()), root, _session, keep=Keeper())
    _run(ci_job)
    (Path(root) / "jet.moy" / "pmem.json").write_text('{"0": 7}')
    repo.add("jet", version=2)
    keep = Keeper(answer=None)
    job = ci.Install(_cart(repo, "jet"), ci.plan(_cart(repo, "jet")),
                     MemNet(repo.routes()), root, _session, keep=keep)
    for _ in range(50):
        if job.keeping:
            break
        job.step()
    folder, files, record = keep.commits[0]
    assert files["pmem.json"] == b'{"0": 7}'
    assert (Path(root) / "jet.moy" / "main.wasm").read_bytes().endswith(b"1"), \
        "the old copy moved before the keeper had the new one"
    keep.finish()
    job.step()
    assert (Path(root) / "jet.moy" / "main.wasm").read_bytes().endswith(b"2")
    assert (Path(root) / "jet.moy" / "pmem.json").read_text() == '{"0": 7}'


def test_a_removal_tells_the_keeper_the_record_before_the_folder_goes(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    repo.add("jet")
    repo.add("pot")
    for cid in ("jet", "pot"):
        _run(ci.Install(_cart(repo, cid), ci.plan(_cart(repo, cid)),
                        MemNet(repo.routes()), root, _session))
    seen = []

    class K(Keeper):
        def record(self, text):
            seen.append((json.loads(text), (Path(root) / "jet.moy").exists()))
    ci.remove(root, "jet.moy", K())
    (rec, present), = seen
    assert sorted(rec["carts"]) == ["pot.moy"] and present is True
    assert not (Path(root) / "jet.moy").exists()
    assert sorted(ci.load_record(root)) == ["pot.moy"]


def test_the_sweep_adopts_a_kept_folder_instead_of_shipping_it(tmp_path):
    from runtime import moy_sync
    root = Path(tmp_path / "carts")
    (root / "mine.moy").mkdir(parents=True)
    (root / "mine.moy" / "main.lua").write_text("x = 1")
    w = moy_sync.StoreWatcher(str(root))
    (root / "mine.moy" / "main.lua").write_text("x = 22")       # the kid's edit
    (root / "jet.moy").mkdir()
    for n, body in (("manifest.json", "{}"), ("config.json", "{}")):
        (root / "jet.moy" / n).write_text(body)
    (root / "jet.moy" / "main.wasm").write_bytes(b"\0asm\x01\0\0\0")
    w.sweep()
    w.adopt("jet.moy")
    ops = w.take()
    assert [o["p"] for o in ops] == ["mine.moy/main.lua"], ops
    w.ack(True)
    w.sweep()
    assert w.take() is None


# -- the app, in the browser's shapes ----------------------------------------------------

class _In:
    def __init__(self, *names):
        self.names = set(names)
        self.last_key = 0

    def pressed(self, name):
        return name in self.names


class Picker:
    """The page's file question. `give` answers it the way the worker does."""

    def __init__(self):
        self.asked = []
        self.open = None

    def __call__(self, name, size, host):
        p = _Pick(self)
        self.asked.append((name, size, host))
        self.open = p
        return p


class _Pick:
    def __init__(self, owner):
        self.owner = owner
        self.answer = None
        self.closed = False

    def poll(self):
        return self.answer

    def close(self):
        """The worker deletes the file a closed question was answered with."""
        self.closed = True
        if self.answer and self.answer[0] == "file" and os.path.exists(self.answer[1]):
            os.remove(self.answer[1])
        if self.owner.open is self:
            self.owner.open = None


def _app_ws(tmp_path, net, indexes, keep=None, pick=None, home=None):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    (tmp_path / ci.INDEXES_NAME).write_text(json.dumps({"indexes": indexes}))
    ws.cart_net = net
    ws.cart_keep = keep
    ws.cart_pick = pick
    ws.cart_home = home
    return ws, ws._apps_by_id["getcarts"]


def _frame(ws, net=None):
    ws._dirty = True
    ws.frame(1 / 30)
    if net is not None and hasattr(net, "tick"):
        net.tick()


def _until(ws, app, net, done, n=5000):
    for _ in range(n):
        if done():
            return
        _frame(ws, net)
    raise AssertionError("still %s" % app.phase)


def _shelf(tmp_path):
    repo = Repo(BASE, "Browser carts")
    wad = os.urandom(40000)
    repo.add("dm", name="Dungeon", runtime="lua", modules=(), release=GITHUB,
             mirror=True, external=[("game.wad", wad, "pkg/game.wad")])
    repo.add("rock", name="Rock Run", runtime="lua", modules=(), release=GITHUB,
             mirror=True)
    unreadable = list(repo.releases) + [c["external"][0]["archive"]["urls"][0]
                                        for c in repo.carts if c["external"]]
    net = Trickle(repo.routes(), chunk=4096, cors=True, unreadable=unreadable)
    return repo, wad, net


def test_a_page_a_board_serves_says_where_carts_come_from(tmp_path):
    for home, words in (("board", "turn WEB CONSOLE off"), ("headless", "no screen")):
        ws, app = _app_ws(tmp_path / home, None, [], home=home)
        ws.open_app(app)
        _frame(ws)
        assert app.phase == "board"
        title, lines = app._message()
        assert title == "ON THE CONSOLE" and words in " ".join(lines)
        assert app._current_verbs() == () and "carts" not in ws._wifi_holders


def test_the_browser_lists_and_installs_through_a_non_blocking_net(tmp_path):
    repo, wad, net = _shelf(tmp_path)
    keep = Keeper()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=keep,
                      pick=Picker())
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    assert [r["cart"]["name"] for r in app.rows] == ["Dungeon", "Rock Run"]
    app._tap_row(1)
    _frame(ws, net)
    app._press("GET")
    _until(ws, app, net, lambda: app.phase == "done")
    assert keep.landed_folders == ["rock.moy"]
    assert (tmp_path / "carts" / "rock.moy" / "manifest.json").exists()
    assert not [u for u in net.opened if u.startswith(GITHUB)]


def test_a_file_the_page_cannot_fetch_is_the_players_own_copy(tmp_path):
    repo, wad, net = _shelf(tmp_path)
    pick = Picker()
    keep = Keeper()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=keep, pick=pick)
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    app._tap_row(0)                                   # Dungeon
    _frame(ws, net)
    app._press("GET")
    _until(ws, app, net, lambda: app.phase == "licence")
    app._press("I AGREE")
    _until(ws, app, net, lambda: app.phase == "pick")
    assert pick.asked == [("game.wad", len(wad), "pages.example")]
    title, lines = app._message()
    assert title == "YOUR COPY" and "can't fetch game.wad" in " ".join(lines)
    assert app._current_verbs() == ("CANCEL",)
    # A file that is not the one: the console says so and asks again.
    wrong = tmp_path / "wrong.wad"
    wrong.write_bytes(os.urandom(100))
    first = pick.open
    first.answer = ("file", str(wrong))
    _until(ws, app, net, lambda: app.phase == "pick" and pick.open is not first)
    assert first.closed and "That isn't game.wad" in app.pick_why
    right = tmp_path / "doom1.wad"
    right.write_bytes(wad)
    pick.open.answer = ("file", str(right))
    _until(ws, app, net, lambda: app.phase == "done")
    assert (tmp_path / "carts" / "dm.moy" / "game.wad").read_bytes() == wad
    assert pick.open is None, "the question stayed up after the install"
    arc = repo.carts[0]["external"][0]["archive"]["urls"][0]
    assert net.opened.count(arc) == 1, "the archive was asked for more than the probe"


def test_every_file_the_page_cannot_fetch_is_asked_for_and_kept_until_read(tmp_path):
    repo = Repo(BASE)
    a, b = os.urandom(3000), os.urandom(5000)
    repo.add("two", name="Two Files", runtime="lua", modules=(), release=GITHUB,
             mirror=True, external=[("a.dat", a, "pkg/a.dat"), ("b.dat", b, "pkg/b.dat")])
    unreadable = list(repo.releases) + [e["archive"]["urls"][0]
                                        for e in repo.carts[0]["external"]]
    net = Trickle(repo.routes(), cors=True, unreadable=unreadable)
    pick = Picker()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=Keeper(), pick=pick)
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    app._tap_row(0)
    _frame(ws, net)
    app._press("GET")
    for _ in range(2):
        _until(ws, app, net, lambda: app.phase == "licence")
        app._press("I AGREE")
    for name, body in (("a.dat", a), ("b.dat", b)):
        _until(ws, app, net, lambda: app.phase == "pick" and pick.open is not None
               and pick.asked[-1][0] == name)
        f = tmp_path / name
        f.write_bytes(body)
        pick.open.answer = ("file", str(f))
        q = pick.open
        _until(ws, app, net, lambda: q.closed or app.phase not in ("pick", "picked"))
        assert not q.closed, "an answered question closed before the install read it"
    _until(ws, app, net, lambda: app.phase in ("done", "failed"))
    assert app.phase == "done", app.why
    assert (tmp_path / "carts" / "two.moy" / "a.dat").read_bytes() == a
    assert (tmp_path / "carts" / "two.moy" / "b.dat").read_bytes() == b


def test_cancelling_the_question_fetches_nothing(tmp_path):
    repo, wad, net = _shelf(tmp_path)
    pick = Picker()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=Keeper(), pick=pick)
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    app._tap_row(0)
    _frame(ws, net)
    app._press("GET")
    _until(ws, app, net, lambda: app.phase == "licence")
    app._press("I AGREE")
    _until(ws, app, net, lambda: app.phase == "pick")
    q = pick.open
    app.handle_input(_In("b"))
    assert app.phase == "cart" and q.closed
    assert not [u for u in net.opened if u.endswith(".zip")]
    # ...and the page's own cancel is the same answer.
    app._press("GET")
    _until(ws, app, net, lambda: app.phase == "licence")
    app._press("I AGREE")
    _until(ws, app, net, lambda: app.phase == "pick")
    pick.open.answer = ("cancel",)
    _until(ws, app, net, lambda: app.phase == "cart")
    assert app.status == "NOT FETCHED"
    assert not (tmp_path / "carts" / "dm.moy").exists()


def test_while_the_keeper_has_it_there_is_no_cancel(tmp_path):
    repo, wad, net = _shelf(tmp_path)
    keep = Keeper(answer=None)
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=keep, pick=Picker())
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    app._tap_row(1)
    _frame(ws, net)
    app._press("GET")
    _until(ws, app, net, lambda: app.job is not None and app.job.keeping)
    assert app._current_verbs() == ()
    assert "Putting it on the shelf..." in app._message()[1]
    app.handle_input(_In("b"))
    assert app.phase == "getting" and app.job.keeping
    app.close()                                    # leaving does not undo it
    keep.finish()
    assert (tmp_path / "carts" / "rock.moy").exists()
    assert keep.landed_folders == ["rock.moy"]


def test_remove_in_the_browser_sends_the_record_to_the_keeper(tmp_path):
    repo, wad, net = _shelf(tmp_path)
    keep = Keeper()
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=keep, pick=Picker())
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    app._tap_row(1)
    _frame(ws, net)
    app._press("GET")
    _until(ws, app, net, lambda: app.phase == "done")
    app._press("OK")
    app._tap_row(1)
    _frame(ws, net)
    app._press("REMOVE")
    app._press("REMOVE")
    assert keep.records and keep.records[-1]["carts"] == {}
    assert not (tmp_path / "carts" / "rock.moy").exists()


def test_the_free_room_is_the_keepers(tmp_path):
    repo, wad, net = _shelf(tmp_path)

    class Tight(Keeper):
        def free(self):
            return 10, 1
    ws, app = _app_ws(tmp_path, net, [repo.url("index.json")], keep=Tight(), pick=Picker())
    ws.open_app(app)
    _until(ws, app, net, lambda: app.phase == "list")
    assert app.free == (10, 1)
    assert app.blocker(app.rows[1]).startswith("Not enough room")


# -- the bridge's Python half ------------------------------------------------------------

@pytest.fixture
def link(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "firmware" / "web_runner"))
    try:
        import carts_link
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(carts_link, "SPOOL", str(tmp_path / "net"))
    (tmp_path / "net").mkdir()
    woke = []
    landed = []
    lk = carts_link.CartsLink(wake=lambda: woke.append(1), landed=landed.append)
    return carts_link, lk, tmp_path / "net", woke, landed


def test_the_bridge_reads_a_spool_only_as_far_as_it_has_grown(link):
    mod, lk, spool, woke, _landed = link
    net = mod.WebCartNet(lk)
    assert net.cors is True and net.online() is True
    a = net.open("https://pages.example/carts/index.json")
    jobs = json.loads(lk.poll_json())
    assert jobs == [{"op": "get", "id": a.rid,
                     "url": "https://pages.example/carts/index.json"}]
    assert lk.poll_json() == ""
    (spool / str(a.rid)).write_bytes(b"")
    assert a.status is None and not a.ready(1)
    lk.event_json(json.dumps({"id": a.rid, "status": 200}))
    assert a.status == 200 and woke
    with open(spool / str(a.rid), "ab") as f:
        f.write(b"hello ")
    buf = bytearray(4)
    assert a.ready(6) and not a.ready(7)
    assert a.readinto(buf) == 4 and bytes(buf) == b"hell"
    assert a.readinto(buf) == 2 and bytes(buf[:2]) == b"o "
    assert a.readinto(buf) is None, "nothing there yet must not read as the end"
    with open(spool / str(a.rid), "ab") as f:
        f.write(b"world")
    lk.event_json(json.dumps({"id": a.rid, "end": 1}))
    assert a.ready(100)
    out = bytearray(16)
    n = a.readinto(out)
    assert out[:n] == b"world" and a.readinto(out) == 0
    a.close()
    assert json.loads(lk.poll_json()) == [{"op": "drop", "id": a.rid}]
    lk.event_json(json.dumps({"id": a.rid, "end": 1}))   # a late answer: ignored


def test_the_bridge_asks_the_page_for_a_range_and_reads_its_206(link):
    """A ranged read's piece: the page's fetch is asked for `bytes=a-b` (the
    worker's cartsGet sends it as one Range), and a 206 is read like a 200."""
    mod, lk, spool, _woke, _landed = link
    net = mod.WebCartNet(lk)
    assert net.ranges is True
    a = net.open("https://pages.example/carts/a.zip", span=(100, 50))
    assert json.loads(lk.poll_json()) == [{"op": "get", "id": a.rid, "range": [100, 149],
                                           "url": "https://pages.example/carts/a.zip"}]
    (spool / str(a.rid)).write_bytes(b"x" * 50)
    lk.event_json(json.dumps({"id": a.rid, "status": 206}))
    lk.event_json(json.dumps({"id": a.rid, "end": 1}))
    buf = bytearray(64)
    assert a.status == 206 and a.ready(51) and a.readinto(buf) == 50


def test_the_bridge_reports_a_host_it_may_not_read_as_status_zero(link):
    mod, lk, spool, _woke, _landed = link
    a = mod.WebCartNet(lk).open("https://github.example/x.zip")
    lk.event_json(json.dumps({"id": a.rid, "status": 0, "error": "Failed to fetch"}))
    assert a.status == 0 and a.ready(1)
    with pytest.raises(OSError):
        a.readinto(bytearray(4))


def test_the_bridge_runs_a_whole_install_against_a_spool(link, tmp_path):
    """The real Python half of the browser transport and keeper, with the
    worker played by this test: the bytes go into spool files between steps,
    exactly where worker.js writes them."""
    mod, lk, spool, _woke, landed = link
    repo = Repo(BASE)
    repo.add("rock", runtime="lua", modules=(), release=GITHUB, mirror=True)
    cart = _cart(repo, "rock")
    routes = repo.routes()
    root = _store(tmp_path)
    net = mod.WebCartNet(lk)
    keep = mod.WebCartKeep(lk)
    job = ci.Install(cart, ci.plan(cart), net, root, _session, keep=keep, step_ms=1000)
    served = {}                     # id -> [body, bytes written so far]
    kept = []
    for _ in range(200):
        if not job.step():
            break
        for j in json.loads(lk.poll_json() or "[]"):
            if j["op"] == "get":
                body = routes.get(j["url"])
                (spool / str(j["id"])).write_bytes(b"")
                lk.event_json(json.dumps({"id": j["id"], "status": 200 if body else 404}))
                if body:
                    served[j["id"]] = [body, 0]
            elif j["op"] == "drop":
                served.pop(j["id"], None)
            elif j["op"] == "keep":
                kept.append(j)
                files = sorted(os.listdir(j["stage"]))
                assert files == sorted(ci.plan(cart)["files"])
                lk.event_json(json.dumps({"id": j["id"], "kept": 1}))
        for rid, fed in list(served.items()):         # 100 bytes a frame
            body, at = fed
            if at < len(body):
                with open(spool / str(rid), "ab") as f:
                    f.write(body[at:at + 100])
                fed[1] = min(len(body), at + 100)
                if fed[1] == len(body):
                    lk.event_json(json.dumps({"id": rid, "end": 1}))
    assert job.error is None, job.detail
    assert job.path and landed == ["rock.moy"] and len(kept) == 1
    assert json.loads(kept[0]["record"])["carts"]["rock.moy"]["id"] == "rock"


def test_the_bridge_keeps_and_says_so_or_why_not(link):
    mod, lk, _spool, _woke, landed = link
    keep = mod.WebCartKeep(lk)
    said = []
    keep.commit("rock.moy", "/moy/install/rock.moy", "{}", lambda w, f: said.append((w, f)))
    job, = json.loads(lk.poll_json())
    assert job["op"] == "keep" and job["folder"] == "rock.moy"
    lk.event_json(json.dumps({"id": job["id"], "error": "QuotaExceededError", "full": 1}))
    assert said == [("QuotaExceededError", True)]
    keep.landed("rock.moy")
    assert landed == ["rock.moy"]
    keep.record('{"version": 1, "carts": {}}')
    assert json.loads(lk.poll_json()) == [{"op": "record",
                                           "record": '{"version": 1, "carts": {}}'}]
    assert keep.free() is None
    lk.event_json(json.dumps({"room": [100, 1100]}))
    assert keep.free() == (1000, 1)


def test_the_bridge_asks_the_page_for_a_file_and_takes_the_question_away(link):
    mod, lk, _spool, _woke, _landed = link
    p = lk.pick("doom1.wad", 4196020, "deb.debian.org")
    job, = json.loads(lk.poll_json())
    assert job == {"op": "pick", "id": p.rid, "name": "doom1.wad", "size": 4196020,
                   "host": "deb.debian.org"}
    assert p.poll() is None
    lk.event_json(json.dumps({"id": p.rid, "picked": "/moy/net/pick-%d" % p.rid}))
    assert p.poll() == ("file", "/moy/net/pick-%d" % p.rid)
    p.close()
    assert json.loads(lk.poll_json()) == [{"op": "unpick", "id": p.rid}]
    q = lk.pick("doom1.wad", 1, "h")
    lk.poll_json()
    lk.event_json(json.dumps({"id": q.rid, "cancel": 1}))
    assert q.poll() == ("cancel",)


# -- the MicroPython lane -------------------------------------------------------------

_MP_SCRIPT = r'''
import sys, os, json
sys.path.insert(0, %(runtime)r)
sys.path.insert(0, %(web)r)
import cart_index as ci
import carts_link
carts_link.SPOOL = %(spool)r
files = json.load(open(%(map)r))
landed = []
lk = carts_link.CartsLink(landed=landed.append)
net = carts_link.WebCartNet(lk)
keep = carts_link.WebCartKeep(lk)
carts = ci.parse_index(open(%(index)r, "rb").read(), %(index_url)r)
root = %(root)r
served = {}
for c in carts:
    p = ci.plan(c)
    sup = {}
    for e in p["external"]:
        sup[e["path"]] = %(picked)r
    job = ci.Install(c, p, net, root, lambda fn: fn(), [e["path"] for e in p["external"]],
                     step_ms=20, supplied=sup, keep=keep)
    n = 0
    while job.step():
        n += 1
        for j in json.loads(lk.poll_json() or "[]"):
            if j["op"] == "get":
                src = files.get(j["url"])
                open(carts_link.SPOOL + "/%%d" %% j["id"], "wb").close()
                lk.event_json(json.dumps({"id": j["id"], "status": 200 if src else 0}))
                if src:
                    served[j["id"]] = [open(src, "rb").read(), 0]
            elif j["op"] == "drop":
                served.pop(j["id"], None)
            elif j["op"] == "keep":
                lk.event_json(json.dumps({"id": j["id"], "kept": 1}))
        for rid in list(served):
            body, at = served[rid]
            if at < len(body):
                f = open(carts_link.SPOOL + "/%%d" %% rid, "ab")
                f.write(body[at:at + 3000])
                f.close()
                served[rid][1] = min(len(body), at + 3000)
                if served[rid][1] == len(body):
                    lk.event_json(json.dumps({"id": rid, "end": 1}))
    print(c["id"], job.error, job.detail if job.error else "",
          sorted(os.listdir(root + "/" + c["folder"])) if job.path else "-", n > 1)
print("landed", sorted(landed))
'''


def test_the_browser_path_runs_under_micropython(tmp_path):
    """The page runs this Python in the wasm MicroPython: the non-blocking
    install, the mirror, a supplied file and the keeper, through the real
    carts_link over spool files, under the desktop MicroPython."""
    from unix_mp import require_unix_mp
    exe = require_unix_mp(why="the browser runs cart_index and carts_link on MicroPython")
    repo = Repo(BASE)
    wad = os.urandom(70000)
    repo.add("dm", runtime="lua", modules=(), release=GITHUB, mirror=True,
             external=[("game.wad", wad, "pkg/game.wad")])
    repo.add("jet", runtime="lua", modules=(), release=GITHUB, mirror=True,
             extra={"big.bin": os.urandom(120000)})
    data = tmp_path / "data"
    data.mkdir()
    url_map = {}
    for i, (url, blob) in enumerate(repo.routes().items()):
        if url in repo.releases:
            continue                          # a page cannot read the release
        p = data / ("f%d" % i)
        p.write_bytes(blob)
        url_map[url] = str(p)
    (tmp_path / "map.json").write_text(json.dumps(url_map))
    (tmp_path / "picked").write_bytes(wad)
    (tmp_path / "spool").mkdir()
    root = _store(tmp_path)
    script = tmp_path / "run.py"
    script.write_text(_MP_SCRIPT % {
        "runtime": str(ROOT / "runtime"), "web": str(ROOT / "firmware" / "web_runner"),
        "spool": str(tmp_path / "spool"), "map": str(tmp_path / "map.json"),
        "index": url_map[repo.url("index.json")], "index_url": repo.url("index.json"),
        "root": root, "picked": str(tmp_path / "picked")})
    import subprocess
    out = subprocess.run([exe, "-X", "heapsize=4M", str(script)], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    lines = [ln for ln in out.stdout.splitlines() if not ln.startswith("Moybyte")]
    assert lines[0].startswith("dm None") and "game.wad" in lines[0] \
        and lines[0].endswith("True"), out.stdout
    assert lines[1].startswith("jet None") and "big.bin" in lines[1], out.stdout
    assert lines[2] == "landed ['dm.moy', 'jet.moy']", out.stdout
    assert (Path(root) / "dm.moy" / "game.wad").read_bytes() == wad
