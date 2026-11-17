"""The browser console add-on (runtime/carts/web_addon.py): a cart per page,
listed in the release's own signed index (tools/web_pack.py), installed like
any cart, served by the webhost from its folder (native/moy_net/moy_page.h).

What is pinned here: a console sees only the add-on that fits its image; the
step that replaces the add-on after a firmware update installs the one that
fits and takes the old one off the shelf; the pack never crosses the sync
wire, never goes into a backup and is never read as a cart's text."""

import os
import sys
from pathlib import Path

import pytest

from cart_store_fixtures import MemNet
from runtime import cart_index as ci, moy_carts, web_addon
from runtime import net_binding as nb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_web_blob  # noqa: E402
import web_pack  # noqa: E402

BASE = "http://mirror.invalid/firmware-beta/"


def _page(tmp_path, tag):
    """A pack of a page (its bytes differ by `tag`) and its digest."""
    data, want = gen_web_blob.pack_bytes([("index.html.gz", b"\x1f\x8b" + tag),
                                          ("worker.js.gz", b"\x1f\x8bw")])
    path = tmp_path / ("page-%s.pack" % tag.decode())
    path.write_bytes(data)
    return str(path), want.hex()


def _release(tmp_path, *tags):
    """The release folder for pages `tags`, oldest first, as the publisher
    leaves it: the carts and carts-tdeck.json over them all."""
    rel = tmp_path / "release"
    rel.mkdir(exist_ok=True)
    pages = [_page(tmp_path, t) for t in tags]
    for v, page in enumerate(pages, 1):
        web_pack.publish_addon(page, str(rel), "tdeck", v)
    routes = {BASE + n: (rel / n).read_bytes() for n in os.listdir(rel)}
    return pages, routes


class Inst:
    def __init__(self, root, net):
        self._root = root
        self._net = net
        self.held = 0
        self.rescans = []

    def net(self):
        return self._net

    def root(self):
        return self._root

    def op(self, fn):
        return fn()

    def chip(self):
        return None, None

    def hold(self):
        self.held += 1
        return True

    def release(self):
        self.held -= 1

    def rescan(self, folder=None):
        self.rescans.append(folder)


@pytest.fixture
def board(monkeypatch):
    """`import moy_net` is the host's face, whose webhost names a page when a
    test says so."""
    monkeypatch.setitem(sys.modules, "moy_net", nb)
    yield
    nb._bake({})
    nb.page_at("")


def _names(digest, monkeypatch):
    nb._bake({"index.html.gz": b"\x1f\x8b"})            # the library up, a stamp
    import ctypes
    nb._lib().moy_page_name(ctypes.create_string_buffer(bytes.fromhex(digest), 32))
    monkeypatch.setattr(ci, "page_want", lambda: digest)


def test_a_console_sees_only_the_add_on_that_fits_its_image(tmp_path, monkeypatch):
    pages, routes = _release(tmp_path, b"a", b"b")
    data = routes[BASE + "carts-tdeck.json"]
    monkeypatch.setattr(ci, "page_want", lambda: pages[0][1])
    got = ci.fitting(ci.parse_index(data, BASE + "carts-tdeck.json"))
    assert [c["id"] for c in got] == [web_pack.cart_id(pages[0][1])]
    monkeypatch.setattr(ci, "page_want", lambda: None)
    assert ci.fitting(ci.parse_index(data, BASE + "carts-tdeck.json")) == [], \
        "an image that names no page fits no add-on"


def test_the_add_on_runs_with_no_engine_of_this_console_s():
    assert ci.runs_on({"runtime": "web"}, ("lua",))
    assert not ci.runs_on({"runtime": "wasm"}, ("lua",))


def test_the_console_s_own_index_is_read_last(tmp_path, monkeypatch):
    root = str(tmp_path / "carts")
    os.makedirs(root)
    own = BASE + "carts-tdeck.json"
    assert ci.load_indexes(root, own) == list(ci.DEFAULT_INDEXES) + [own]
    assert ci.load_indexes(root) == list(ci.DEFAULT_INDEXES)


def test_an_update_installs_the_add_on_that_fits_and_takes_the_old_one_away(
        tmp_path, monkeypatch, board):
    """After a firmware update the add-on on the shelf fits the old image:
    the step installs the one the new image fits through the ordinary
    install, takes the old one off, and the webhost serves the new one."""
    pages, routes = _release(tmp_path, b"old", b"new")
    root = str(tmp_path / "carts")
    os.makedirs(root)
    old = web_pack.cart_id(pages[0][1]) + ".moy"
    import zipfile
    with zipfile.ZipFile(tmp_path / "release" / (web_pack.CART_NAME % pages[0][1])) as z:
        z.extractall(root)
    assert os.listdir(root) == [old]
    _names(pages[1][1], monkeypatch)
    nb.page_at(root)
    assert nb.page_state() == web_addon.STATE_STALE
    inst = Inst(root, MemNet(routes))
    step = web_addon.AddonStep(inst, BASE + "carts-tdeck.json")
    assert step.start()
    n = 0
    while step.tick(1000):
        n += 1
        assert n < 1000
    assert step.error is None
    assert os.listdir(root) == [web_pack.cart_id(pages[1][1]) + ".moy"]
    assert nb.page_state() == 3 and nb.page_why() is None
    assert inst.held == 0 and inst.rescans == [None]
    assert not step.start(), "an add-on that fits wants nothing"


class Console(Inst):
    """A console's server: a network saved or not, a game playing or not."""

    def __init__(self, root, net, configured=True):
        Inst.__init__(self, root, net)
        self.saved = configured
        self.game = False

    def configured(self):
        return self.saved

    def playing(self):
        return self.game


def _absent(tmp_path, monkeypatch, *tags):
    """A store with no add-on, its image naming the last of `tags`' pages."""
    pages, routes = _release(tmp_path, *tags)
    root = str(tmp_path / "carts")
    os.makedirs(root)
    _names(pages[-1][1], monkeypatch)
    nb.page_at(root)
    assert nb.page_state() == web_addon.STATE_ABSENT
    return pages, routes, root


def _run(step):
    n = 0
    while step.tick(1000):
        n += 1
        assert n < 1000


def test_every_board_with_no_add_on_installs_the_one_that_fits(tmp_path, monkeypatch, board):
    """Every board's step has `absent` (a console's since 2026-11-17, a
    Zero's whose page is its whole use): a store with none -- a wiped one, a
    console that never had it -- gets the one its image fits from its
    release."""
    pages, routes, root = _absent(tmp_path, monkeypatch, b"old", b"new")
    inst = Console(root, MemNet(routes))
    step = web_addon.AddonStep(inst, BASE + "carts-tdeck.json", absent=True)
    assert step.start()
    _run(step)
    assert step.error is None and step.due is None
    assert os.listdir(root) == [web_pack.cart_id(pages[1][1]) + ".moy"]
    assert nb.page_state() == 3 and inst.held == 0
    assert not step.start(), "an add-on that fits wants nothing"


def test_a_step_without_absent_brings_no_first_add_on(tmp_path, monkeypatch, board):
    _pages, routes, root = _absent(tmp_path, monkeypatch, b"new")
    step = web_addon.AddonStep(Inst(root, MemNet(routes)), BASE + "carts-tdeck.json")
    assert not step.start() and os.listdir(root) == []


def test_a_removed_console_is_never_fetched_and_its_install_brings_it_back(
        tmp_path, monkeypatch, board):
    """A removal records the console (moy_seed.ADDON_LINE): the step never
    fetches it, whichever page's folder was removed, until an install drops
    the line (pkg.landed) or a Zero's page asks for it again (`restore`)."""
    pages, routes, root = _absent(tmp_path, monkeypatch, b"new")
    assert moy_carts.note_removed(root, web_pack.cart_id("ab" * 32) + ".moy")
    assert moy_carts.load_removed(root) == [moy_carts.ADDON_LINE]
    net = MemNet(routes)
    step = web_addon.AddonStep(Console(root, net), BASE + "carts-tdeck.json", absent=True)
    assert step.removed() and not step.wanted()
    assert not step.start() and not step.start(retry=True)
    assert net.opened == [] and os.listdir(root) == []
    assert step.restore()
    _run(step)
    assert step.error is None and moy_carts.load_removed(root) == []
    assert os.listdir(root) == [web_pack.cart_id(pages[0][1]) + ".moy"]


def test_a_console_with_no_network_saved_does_nothing_at_all(tmp_path, monkeypatch, board):
    _pages, routes, root = _absent(tmp_path, monkeypatch, b"new")
    net = MemNet(routes)
    inst = Console(root, net, configured=False)
    step = web_addon.AddonStep(inst, BASE + "carts-tdeck.json", absent=True)
    assert not step.start() and not step.nudge()
    assert inst.held == 0 and step.error is None and step.due is None
    assert net.opened == []
    inst.saved = True                  # a network joined: Settings nudges it
    assert step.nudge()
    _run(step)
    assert step.error is None and len(os.listdir(root)) == 1


def test_a_game_puts_the_step_off_and_stops_a_running_one(tmp_path, monkeypatch, board):
    """Never during play: a start while a game runs is put off (`due`, which
    the console's frame polls), and a game that starts stops a running step,
    the shelf as it was and the radio let go, to start again after it."""
    _pages, routes, root = _absent(tmp_path, monkeypatch, b"new")
    clock = [0]
    monkeypatch.setattr(web_addon, "_now", lambda: clock[0])
    inst = Console(root, MemNet(routes))
    step = web_addon.AddonStep(inst, BASE + "carts-tdeck.json", absent=True)
    inst.game = True
    assert not step.start() and step.due is not None and inst.held == 0
    clock[0] += web_addon.RETRY_MS - 1
    assert not step.poll(), "not before its wait is over"
    inst.game = False
    clock[0] += 1
    assert step.poll() and step.due is None and inst.held == 1
    inst.game = True
    assert not step.tick(1000)
    assert step.phase is None and inst.held == 0 and os.listdir(root) == []
    assert step.due is not None and step.error is None
    inst.game = False
    clock[0] += web_addon.RETRY_MS
    assert step.poll()
    _run(step)
    assert step.error is None and len(os.listdir(root)) == 1


def test_a_failure_backs_off_doubling_to_its_ceiling(tmp_path, monkeypatch, board):
    _pages, _routes, root = _absent(tmp_path, monkeypatch, b"new")
    clock = [0]
    monkeypatch.setattr(web_addon, "_now", lambda: clock[0])
    step = web_addon.AddonStep(Console(root, MemNet({})), BASE + "carts-tdeck.json",
                               absent=True)
    waits = []
    for _ in range(9):
        assert step.start(retry=True)
        _run(step)
        assert step.error and step.due is not None
        waits.append(step._due_ms)
        clock[0] += step._due_ms - 1
        assert not step.poll(), "a failure waits its back-off out"
        clock[0] += 1
    assert waits[:3] == [web_addon.RETRY_MS, 2 * web_addon.RETRY_MS, 4 * web_addon.RETRY_MS]
    assert waits[-1] == web_addon.RETRY_MAX_MS
    step.nudge()                       # a join forgets the back-off
    assert step.phase is not None
    step.stop()


def test_a_release_with_no_add_on_for_the_image_is_not_asked_again_by_the_loop(
        tmp_path, monkeypatch, board):
    """An image its release publishes no add-on for (a build of this tree)
    is told so once; the serving loop's retries fetch the index no more, and
    a boot's or a person's start still asks."""
    pages, routes = _release(tmp_path, b"old")
    root = str(tmp_path / "carts")
    os.makedirs(root)
    _names(_page(tmp_path, b"unpublished")[1], monkeypatch)
    nb.page_at(root)
    net = MemNet(routes)
    step = web_addon.AddonStep(Inst(root, net), BASE + "carts-tdeck.json", absent=True)
    assert step.start()
    while step.tick(1000):
        pass
    assert step.error == "no browser console for this firmware"
    monkeypatch.setattr(web_addon, "RETRY_MS", 0)
    assert not step.start(retry=True)
    assert len(net.opened) == 1
    assert step.start()
    step.stop()


def test_a_failed_attempt_waits_before_a_browser_s_retry(tmp_path, monkeypatch, board):
    pages, routes = _release(tmp_path, b"old", b"new")
    root = str(tmp_path / "carts")
    os.makedirs(os.path.join(root, web_pack.cart_id(pages[0][1]) + ".moy"))
    _names(pages[1][1], monkeypatch)
    nb.page_at(root)
    inst = Inst(root, MemNet({}))
    step = web_addon.AddonStep(inst, BASE + "carts-tdeck.json")
    assert step.start()
    while step.tick(1000):
        pass
    assert step.error and inst.held == 0
    assert not step.start(retry=True), "a browser asking again waits RETRY_MS"
    assert step.start(), "Update on the screen does not"
    step.stop()


def test_the_pack_never_travels_and_is_never_backed_up():
    """One rule, the store's: the sync wire's pull and the backup archive
    both skip by it, and the pack is fetched again by its digest."""
    from runtime import moy_store
    for history in (False, True):
        assert moy_store.skip(web_pack.PACK_FILE, history)
        assert not moy_store.skip("manifest.json", history)


def test_the_pull_carries_the_add_on_s_manifest_and_not_its_pack(tmp_path, board):
    pages, _ = _release(tmp_path, b"a")
    root = tmp_path / "carts"
    import zipfile
    with zipfile.ZipFile(tmp_path / "release" / (web_pack.CART_NAME % pages[0][1])) as z:
        z.extractall(root)
    from test_moy_webhost import _pull
    pulled = _pull(root)
    folder = web_pack.cart_id(pages[0][1]) + ".moy"
    assert folder + "/manifest.json" in pulled
    assert folder + "/" + web_pack.PACK_FILE not in pulled


def test_opening_the_add_on_never_reads_its_pack(tmp_path):
    """Its main is the pack: bytes the webhost serves a piece at a time,
    which a whole load would read into RAM and refuse as text."""
    folder = tmp_path / "carts" / "web_console_0123456789ab.moy"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        '{"format": "moy-1", "title": "Browser console", "runtime": "web", '
        '"main": "moybyte_web.pack", "moybyte": {"type": "web", "fits": "00"}}')
    (folder / "moybyte_web.pack").write_bytes(b"\xff\xfe" * 300000)
    cart = moy_carts.load(str(folder))
    assert cart is not None and cart["src"] == ""
    assert [e["path"] for e in moy_carts.catalogue(str(tmp_path / "carts"))] \
        == [str(folder)]


def test_a_zero_s_root_undoes_a_removal_when_its_link_is_followed(
        tmp_path, monkeypatch, board):
    """A Zero has no face but its page, so where its owner removed the
    console the root says so and links the way back (device/zero_host.
    way_back over moy_page_say); the link followed restores the record and
    the next turn installs the console. Unfollowed, a removal holds."""
    sys.path.insert(0, str(ROOT / "device"))
    try:
        import zero_host
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(zero_host, "_PAGE", [None, nb.page_asked()])
    pages, routes, root = _absent(tmp_path, monkeypatch, b"new")
    moy_carts.note_removed(root, web_pack.cart_id(pages[0][1]) + ".moy")
    net = MemNet(routes)
    step = web_addon.AddonStep(Inst(root, net), BASE + "carts-tdeck.json", absent=True)
    try:
        assert not zero_host.tend_addon(step, None)
        assert nb.page_why() == zero_host.PAGE_REMOVED and net.opened == []
        assert not zero_host.tend_addon(step, None), "a removal holds"
        nb.page_at(root)
        nb.page_state()
        nb._lib().moy_page_ask()               # the root's link, followed
        assert zero_host.tend_addon(step, None)
        while zero_host.tend_addon(step, None):
            pass
        assert step.error is None and moy_carts.load_removed(root) == []
        assert nb.page_state() == 3
    finally:
        nb.page_say("", 0)
