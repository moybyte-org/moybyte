"""The official carts: every seed cart (system_carts/) but a test fixture
published in its release's `carts-<board>.json` (tools/web_pack.py), the core set a console
never removes (moy_seed.CORE), a removal's record that the seed honours, and
the Zero's way of carrying them -- the core in its image, the rest installed
by a cable flash (tools/board_flash.py) and brought to each release by the
update's step (runtime/carts/web_addon.py)."""

import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from cart_store_fixtures import MemNet
from runtime import cart_index as ci, moy_carts, moy_catalogue, moy_seed, moy_store_base, pkg, web_addon

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "system_carts"
sys.path.insert(0, str(ROOT / "tools"))

import board_config  # noqa: E402
import board_flash  # noqa: E402
import gen_device_carts as gen  # noqa: E402
import gen_web_blob  # noqa: E402
import web_pack  # noqa: E402
from test_ota_signing import TEST_KEYS  # noqa: E402
from test_pkg import Inst, _sign  # noqa: E402

BASE = "http://mirror.invalid/firmware-beta/"
OWN = BASE + "carts-tdeck.json"


def _manifests():
    out = {}
    for d in sorted(SEEDS.iterdir()):
        if d.name.endswith(".moy"):
            out[d.name[:-4]] = json.loads((d / "manifest.json").read_text())
    return out


# The test fixtures (owner, 2026-11-18): seeded on the host for the tests,
# pushed to a board for a run, never in an image nor listed as an official cart.
FIXTURES = {"moybyte.bench", "moybyte.bench_lua", "moybyte.brick_siege_lua",
            "moybyte.sakura"}
# The official carts no store is seeded with (owner, 2026-11-18): the release
# lists them, and a console gets them from there alone.
UNSEEDED = {"moybyte.bullet_storm", "moybyte.sakura_c"}


def _official():
    return {cid: m for cid, m in _manifests().items() if not gen.is_fixture(m)}


# -- the core set -----------------------------------------------------------------


def test_the_core_set_is_one_list_every_reader_shares():
    assert board_config.core_carts() == moy_seed.CORE
    mans = _manifests()
    for cid in moy_seed.CORE:
        assert cid in mans, "%s is no seed cart" % cid
    assert moy_seed.is_core("moybyte.files.moy") and moy_seed.is_core("moybyte.files")
    assert not moy_seed.removable("moybyte.files.moy")
    assert moy_seed.removable("moybyte.coin_quest.moy")
    assert not moy_seed.removable("dm.moy"), "only a built-in is the seed's to keep away"


def test_the_c_reads_the_core_set_from_its_one_source(tmp_path):
    """The removal record is C (native/moy_store/moy_removed.c), whose core
    set is moy_core.h, generated from moy_seed.CORE: the header is what the
    generator writes, and the C records exactly the names Python calls
    removable."""
    import gen_core_carts
    assert gen_core_carts.main(["--check"]) == 0, "run tools/gen_core_carts.py"
    names = ["moybyte.x", "moybyte.", "moybyte", "moybyte.moy", "moybytes.x.moy",
             "dm.moy", "kenny.jet", "x.moybyte.moy"]
    for cid in _manifests():
        names += [cid, cid + ".moy"]
    for i, name in enumerate(names):
        root = str(tmp_path / str(i) / "carts")
        os.makedirs(root)
        assert moy_carts.note_removed(root, name) == moy_seed.removable(name), name
        assert moy_carts.load_removed(root) == ([name] if moy_seed.removable(name) else [])


def test_the_record_reads_only_optional_built_ins_once_each(tmp_path):
    root = str(tmp_path / "moybyte" / "carts")
    os.makedirs(root)
    rec = tmp_path / "moybyte" / "system" / "removed"
    rec.parent.mkdir()
    rec.write_text(" moybyte.letter_blitz.moy \r\nmoybyte.files.moy\ndm.moy\n\n"
                   "moybyte.letter_blitz.moy\nmoybyte.coin_quest.moy")
    assert moy_carts.load_removed(root) == ["moybyte.letter_blitz.moy", "moybyte.coin_quest.moy"]
    assert not moy_carts.note_removed(root, "moybyte.coin_quest.moy")
    assert moy_carts.note_removed(root, "moybyte.paint.moy")
    assert rec.read_text() == ("moybyte.letter_blitz.moy\nmoybyte.coin_quest.moy\n"
                               "moybyte.paint.moy\n"), "a write rewrites the record as read"
    assert moy_carts.unremove(root, ["moybyte.coin_quest.moy", "dm.moy"]) == [
        "moybyte.coin_quest.moy"]
    assert rec.read_text() == "moybyte.letter_blitz.moy\nmoybyte.paint.moy\n"


def test_the_core_set_holds_what_the_shell_opens_and_names():
    """Each core cart's reason, held to the code that gives it."""
    mans = _manifests()
    core = set(moy_seed.CORE)
    # the Wallpaper slot a drawing is copied into (Files' and Paint's WALL)
    from runtime import picture_copies
    slot, = [cid for cid, m in mans.items() if m["moybyte"].get("type") == "wallpaper"
             and m["title"] == picture_copies.WALL_TITLE]
    assert slot in core
    # the process the kernel's notify rows send to
    from runtime import achievements
    assert achievements.PROC in core
    # Get Carts: the way back to every other cart
    assert "moybyte.get_carts" in core
    # every app the shell's own surfaces, and a core app, open by id
    from runtime import app_decls
    folder = {a["id"]: a["folder"] for a in app_decls.APPS}
    core_apps = [a["entry"].split(":")[0] + ".py" for a in app_decls.APPS
                 if a["folder"] in core]
    sources = sorted(p for p in board_config.shared_sources(ROOT / "runtime").values()
                     if p.parent.name in ("shell", "system", "boot", "wm", "home"))
    sources += [board_config.runtime_file(m) for m in core_apps]
    opened = set()
    for path in sources:
        text = path.read_text()
        opened |= set(re.findall(r'(?:ws\.app|open_app)\("(\w+)"\)', text))
    assert opened, "the scan found the shell's opens"
    for app in sorted(opened):
        assert folder[app] in core, "%s opens %s, whose cart is not core" % (sources, app)


def test_a_zero_installs_no_cart_its_image_carries_and_only_seed_carts():
    mans = _manifests()
    for zero in ("seeed_xiao_esp32s3_zero", "esp32c3_zero"):
        d = ROOT / "firmware" / zero
        image = board_config.roster_only(str(d))
        assert set(moy_seed.CORE) <= set(image) and set(image) <= set(mans)
        for cid in board_config.roster_install(str(d)):
            assert cid in mans and cid not in image


# -- the release publishes them ---------------------------------------------------


def _page(tmp_path):
    data, want = gen_web_blob.pack_bytes([("index.html.gz", b"\x1f\x8bpage")])
    path = tmp_path / "page.pack"
    path.write_bytes(data)
    return str(path), want.hex()


def _release(tmp_path, key=True, seeds=SEEDS):
    """A release folder for one page, its index signed with the test key."""
    rel = tmp_path / "release"
    rel.mkdir(exist_ok=True)
    page = _page(tmp_path)
    web_pack.publish_addon(page, str(rel), "tdeck", 9, seeds=str(seeds))
    if key:
        idx = rel / "carts-tdeck.json"
        idx.write_bytes(_sign(idx.read_bytes()))
    routes = {BASE + n: (rel / n).read_bytes() for n in os.listdir(rel)}
    return page[1], routes


@pytest.fixture
def keys(monkeypatch):
    from runtime import net_binding
    monkeypatch.setitem(sys.modules, "moy_net", net_binding)
    monkeypatch.setattr(ci, "INDEX_KEYS", TEST_KEYS)


# The seven carts cut from the seed set (owner, 2026-11-18): a store seeded
# before that sweeps their folders once, and keeps every fixture's.
CUT = ("hop_quest", "pixel_pet", "sky_run", "tiny_runner", "ocean_desktop",
       "open_machine", "moy_night")


def test_a_store_from_before_the_cut_sweeps_the_cut_carts_and_keeps_the_fixtures(tmp_path):
    root = str(tmp_path / "carts")
    kept = ["moybyte.%s.moy" % f for f in ("bench", "star_catcher")]
    for name in ["moybyte.%s.moy" % c for c in CUT] + kept:
        os.makedirs(os.path.join(root, name))
    moy_seed._save_store_version(root, moy_seed.STORE_FORMAT, 8)
    assert moy_seed.sweep_store(root, export=None, board="b") == len(CUT)
    assert sorted(n for n in os.listdir(root) if n.endswith(".moy")) == kept
    assert not set(SEEDS.iterdir()) & {SEEDS / ("moybyte.%s.moy" % c) for c in CUT}


def test_a_store_from_before_tap_only_red_sweeps_tap_game_and_keeps_bullet_storm(tmp_path):
    """Tap Only Red, rebuilt in blocks, replaced Tap Game (owner, 2026-11-18):
    a store swept before that sweeps Tap Game's folder once, and keeps Bullet
    Storm, which left the seed set to be an official cart only."""
    root = str(tmp_path / "carts")
    kept = ["moybyte.%s.moy" % f for f in ("bullet_storm", "tap_only_red")]
    for name in ["moybyte.tap_game.moy"] + kept:
        os.makedirs(os.path.join(root, name))
    moy_seed._save_store_version(root, moy_seed.STORE_FORMAT, 9)
    assert moy_seed.sweep_store(root, export=None, board="b") == 1
    assert sorted(n for n in os.listdir(root) if n.endswith(".moy")) == kept
    assert not (SEEDS / "moybyte.tap_game.moy").exists()


def test_the_test_fixtures_are_the_ones_the_owner_named():
    assert {cid for cid, m in _manifests().items() if gen.is_fixture(m)} == FIXTURES
    assert not FIXTURES & set(moy_seed.CORE), "a core cart is listed, so no fixture"
    for d in ("seeed_xiao_esp32s3_zero", "esp32c3_zero"):
        assert not FIXTURES & set(board_config.roster_install(str(ROOT / "firmware" / d)))


def test_the_unseeded_carts_are_the_ones_the_owner_named():
    mans = _manifests()
    assert {cid for cid, m in mans.items() if gen.is_unseeded(m)} == UNSEEDED
    assert not UNSEEDED & FIXTURES and not UNSEEDED & set(moy_seed.CORE)
    for cid in UNSEEDED:
        assert not gen.in_image(mans[cid]) and not gen.is_fixture(mans[cid])


def test_an_unseeded_cart_is_seeded_nowhere(tmp_path):
    """No image, browser bundle, cable flash's new store or host store is
    seeded with an unseeded cart, and naming one for an image is an error."""
    mans = _manifests()
    titles = {mans[cid]["title"] for cid in UNSEEDED}
    for target in ("host", "device", "web"):
        assert not UNSEEDED & set(gen.roster(target))
    assert not titles & {t for t, _v, _b in gen.build_packed(str(SEEDS))}
    assert not titles & {c["title"] for c in gen.build_carts(str(SEEDS), fixtures=True)}
    with pytest.raises(ValueError, match="unseeded"):
        gen.build_packed(str(SEEDS), (sorted(UNSEEDED)[0],))
    for d in (ROOT / "firmware").iterdir():
        if (d / "board.toml").is_file():
            assert not UNSEEDED & set(board_config.roster_only(str(d)))
            assert not UNSEEDED & set(board_config.roster_install(str(d)))
    # the host's store and a store the seed served (the browser's) take none
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "host"))
    assert not titles & {c["title"] for c in ws.launcher.items}
    for cid in UNSEEDED:
        assert not os.path.exists(str(tmp_path / "host" / (cid + ".moy")))
    os.makedirs(str(tmp_path / "served"))
    got = moy_seed.seed_folders(str(SEEDS), str(tmp_path / "served"))
    assert not {cid + ".moy" for cid in UNSEEDED} & set(got["served"])
    # a store that holds one keeps it: the seed and its sweep take nothing
    root = str(tmp_path / "held")
    shutil.copytree(str(SEEDS / "moybyte.bullet_storm.moy"), root + "/moybyte.bullet_storm.moy")
    moy_seed._save_store_version(root, moy_seed.STORE_FORMAT, 0)
    moy_seed.sweep_store(root, gen.build_packed(str(SEEDS)), export=None, board="b")
    moy_catalogue.seed(gen.build_packed(str(SEEDS)), root, moy_catalogue.catalogue(root))
    assert os.path.exists(root + "/moybyte.bullet_storm.moy/manifest.json")


def test_every_seed_cart_but_a_fixture_is_an_official_entry_of_its_release(tmp_path, keys,
                                                                          monkeypatch):
    digest, routes = _release(tmp_path)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    got = ci.fitting(ci.parse_index(routes[OWN], OWN, OWN))
    official = {c["id"]: c for c in got if ci.is_official(c)}
    mans = _official()
    assert set(official) == set(mans) and not FIXTURES & {c["id"] for c in got}
    assert UNSEEDED <= set(official), "an unseeded cart is official all the same"
    assert not any(n[len(BASE):].startswith(tuple(f + "-" for f in FIXTURES)) for n in routes), \
        "a fixture's zip or cover was published"
    assert set(board_flash.offered_carts()) == {cid + ".moy" for cid in mans}
    for cid, c in official.items():
        assert c["moybyte"]["api"] == moy_store_base.API_LEVEL and "fits" not in c["moybyte"]
        assert c["moybyte"]["core"] == (cid in moy_seed.CORE)
        assert c["version"] == mans[cid]["version"] and c["name"] == mans[cid]["title"]
        assert c["runtime"] == mans[cid].get("runtime", "python")
        asset, = c["assets"]
        with zipfile.ZipFile(tmp_path / "release" / asset["name"]) as z:
            names = sorted(n.split("/", 1)[1] for n in z.namelist())
        assert names == sorted(asset["files"])
        assert not any(n.startswith("thumbs/") for n in names)
        assert c.get("cover") is None or (tmp_path / "release" / c["cover"]["url"]).exists()
        assert ci.runs_on(c, ("lua", "wasm"))
    assert "scenes/" in "".join(official["moybyte.coin_quest"]["assets"][0]["files"]), \
        "a cart's subfolders travel"
    # one cart is one asset: the same bytes the same name on every board
    again = tmp_path / "again"
    again.mkdir()
    web_pack.publish_addon(_page(tmp_path), str(again), "p4", 9)
    assert {a["name"] for c in official.values() for a in c["assets"]} \
        <= set(os.listdir(again))


def test_only_the_console_s_own_signed_index_is_trusted_with_built_ins(tmp_path, keys,
                                                                     monkeypatch):
    digest, routes = _release(tmp_path)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    other = [c for c in ci.parse_index(routes[OWN], OWN) if c["runtime"] != "web"]
    assert other == [], "a built-in from any index but the console's own is left out"
    (tmp_path / "u").mkdir()
    _, unsigned = _release(tmp_path / "u", key=False)
    assert [c for c in ci.parse_index(unsigned[OWN], OWN, OWN) if c["runtime"] != "web"] == []


def test_another_build_lists_its_release_s_official_carts_by_api_level(tmp_path, keys,
                                                                       monkeypatch):
    """#271: a console whose image is not the release's (a dev build, a local
    build) lists the release's official carts at its API level or below, and
    only the browser console add-on stays matched to the image's own page."""
    digest, routes = _release(tmp_path)
    monkeypatch.setattr(ci, "page_want", lambda: "f" * 64)
    got = ci.fitting(ci.parse_index(routes[OWN], OWN, OWN))
    official = [c for c in got if ci.is_official(c)]
    assert {c["id"] for c in official} == set(_official())
    assert all(ci.newer_why(c) is None for c in official)
    assert not [c for c in got if c["runtime"] == "web"], "the add-on is another page's"
    assert ci.addon_unfit(ci.parse_index(routes[OWN], OWN, OWN))
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    assert not ci.addon_unfit(ci.parse_index(routes[OWN], OWN, OWN))
    # a release a level ahead: listed, each saying it needs a newer firmware
    monkeypatch.setattr(moy_store_base, "API_LEVEL", moy_store_base.API_LEVEL - 1)
    ahead = [c for c in ci.fitting(ci.parse_index(routes[OWN], OWN, OWN)) if ci.is_official(c)]
    assert len(ahead) == len(official)
    for c in ahead:
        assert "Needs a newer firmware" in ci.newer_why(c)
        assert "API level %d" % (moy_store_base.API_LEVEL + 1) in ci.newer_why(c)


def test_a_python_built_in_from_anywhere_else_is_still_refused():
    entry = {"id": "moybyte.coin_quest", "name": "Quest", "version": 1,
             "folder": "moybyte.coin_quest.moy", "runtime": "python",
             "licence": {"url": "L", "size": 1, "sha256": "0" * 64},
             "assets": [{"url": "a", "size": 1, "sha256": "0" * 64,
                         "files": {"main.py": {"size": 1, "sha256": "0" * 64}}}]}
    assert ci.check_cart(entry) is not None
    assert ci.check_cart(entry, official=True) is None
    deep = dict(entry, assets=[dict(entry["assets"][0], files={
        "a/b/c/d.json": {"size": 1, "sha256": "0" * 64}})])
    assert ci.check_cart(deep, official=True) is not None
    dots = dict(entry, assets=[dict(entry["assets"][0], files={
        "scenes/../x": {"size": 1, "sha256": "0" * 64}})])
    assert ci.check_cart(dots, official=True) is not None


# -- a console removes an optional one, and the seed honours it -------------------


def _seeded(tmp_path):
    root = str(tmp_path / "moybyte" / "carts")
    os.makedirs(root)
    seed = gen.build_packed(str(SEEDS))
    shelf = moy_catalogue.seed(seed, root, moy_catalogue.catalogue(root))
    return root, seed, shelf


class OwnInst(Inst):
    def __init__(self, root, net=None, own=OWN):
        Inst.__init__(self, root, net, chip=(None, None))
        self._own = own

    def own(self):
        return self._own

    def runtimes(self):
        return ("lua", "wasm")


def _drain(gen_):
    for _ in pkg.through(gen_):
        pass


def test_a_removed_optional_cart_stays_removed_and_a_get_brings_it_back(
        tmp_path, keys, monkeypatch):
    root, seed, _shelf = _seeded(tmp_path)
    inst = OwnInst(root)
    quest = "moybyte.coin_quest.moy"
    _drain(pkg.removed(inst, quest))
    assert not os.path.exists(root + "/" + quest)
    assert moy_carts.load_removed(root) == [quest]
    # the next boot's seed writes it no more, its newer version included
    shelf = moy_catalogue.seed(seed, root, moy_catalogue.catalogue(root))
    assert not os.path.exists(root + "/" + quest)
    assert quest not in [e["path"].rsplit("/", 1)[-1] for e in shelf]
    newer = [(t, v + 1 if t == "Coin Quest" else v, b) for t, v, b in seed]
    moy_catalogue.seed(newer, root, moy_catalogue.catalogue(root))
    assert not os.path.exists(root + "/" + quest)
    # a core cart is never recorded
    assert not moy_carts.note_removed(root, "moybyte.files.moy")
    # Get Carts lists it to get, and installs it from the release
    digest, routes = _release(tmp_path)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    inst._net = MemNet(routes)
    carts = ci.fitting(ci.parse_index(routes[OWN], OWN, OWN))
    c, = [c for c in carts if c["folder"] == quest]
    rows = _rows(root, carts)
    assert rows[quest] == "get"
    assert rows["moybyte.files.moy"] == "installed", "a seed stands by its version"
    job = ci.Install(c, ci.plan(c), inst.net(), root, inst.op)
    while job.step():
        pass
    assert job.path, job.error
    _drain(pkg.landed(inst, job.path, None))
    assert moy_carts.load_removed(root) == []
    assert os.path.exists(root + "/" + quest + "/scenes")
    assert _rows(root, carts)[quest] == "installed"


def _rows(root, carts):
    rec = ci.load_record(root)
    out = {}
    for c in carts:
        if not ci.is_official(c):
            continue
        folder = root + "/" + c["folder"]
        man = None
        if os.path.exists(folder + "/manifest.json"):
            man = json.loads(open(folder + "/manifest.json").read())
        out[c["folder"]] = ci.cart_state(c, ci.plan(c), rec.get(c["folder"]),
                                         os.path.exists(folder), man)
    return out


def test_pkg_remove_takes_an_optional_built_in_and_keeps_a_core_one(tmp_path):
    root, _seed, _shelf = _seeded(tmp_path)
    out, err = [], []
    p = pkg.Pkg(OwnInst(root), out.append, err.append)
    assert _done(p.remove(["moybyte.letter_blitz"])) == 0
    assert moy_carts.load_removed(root) == ["moybyte.letter_blitz.moy"]
    assert _done(p.remove(["moybyte.files"])) == 1
    assert "core" in err[-1] and os.path.exists(root + "/moybyte.files.moy")


def test_a_page_s_delete_over_the_sync_wire_is_the_console_s_removal(tmp_path):
    """A board-served page deletes a cart with a `dc` op
    (native/moy_net/moy_sync_apply.c): an optional built-in's removal is
    recorded as pkg.removed records it, a core cart stays, and a cart folder
    the wire brings back drops the line as an install does. The C shell's
    `pkg remove` is tests/moy_sh/serial/pkg.sh's transcript."""
    from runtime.moy_sync import apply_ops
    root, seed, _shelf = _seeded(tmp_path)
    blitz = "moybyte.letter_blitz.moy"
    applied, errors, _ = apply_ops(root, [{"p": blitz, "dc": 1}])
    assert (applied, errors) == (1, [])
    assert not os.path.exists(root + "/" + blitz)
    assert moy_carts.load_removed(root) == [blitz]
    moy_catalogue.seed(seed, root, moy_catalogue.catalogue(root))
    assert not os.path.exists(root + "/" + blitz), "the next boot's seed keeps it away"
    _, errors, _ = apply_ops(root, [{"p": "moybyte.files.moy", "dc": 1}])
    assert [why for _i, why in errors] == ["a core cart"]
    assert os.path.exists(root + "/moybyte.files.moy/manifest.json")
    applied, errors, _ = apply_ops(root, [{"p": "dm.moy/main.py", "t": "x = 1"},
                                          {"p": "dm.moy", "dc": 1}])
    assert (applied, errors) == (2, []) and moy_carts.load_removed(root) == [blitz]
    applied, errors, _ = apply_ops(root, [{"p": blitz + "/main.py", "t": "x = 1"}])
    assert (applied, errors) == (1, []) and moy_carts.load_removed(root) == []


def _addon(root, digest):
    folder = "web_console_%s.moy" % digest[:12]
    os.makedirs(root + "/" + folder)
    with open(root + "/" + folder + "/manifest.json", "w") as f:
        json.dump({"format": "moy-1", "title": "Browser console", "runtime": "web",
                   "main": "moybyte_web.pack", "moybyte": {"type": "web", "fits": digest}}, f)
    return folder


def test_removing_the_browser_console_is_one_line_and_an_install_drops_it(tmp_path):
    """Every removal of a browser console add-on -- the shelf's and Get
    Carts' (pkg.removed), `pkg remove` of one the board installed itself, a
    page's delete over the sync wire -- records the console once
    (moy_seed.ADDON_LINE), whichever page's folder it was, so no board fetches
    it again (web_addon.AddonStep); an install of one drops the line."""
    from runtime.moy_sync import apply_ops
    root, _seed, _shelf = _seeded(tmp_path)
    inst = OwnInst(root)
    a = _addon(root, "a" * 64)
    _drain(pkg.removed(inst, a))
    assert not os.path.exists(root + "/" + a)
    assert moy_carts.load_removed(root) == [moy_carts.ADDON_LINE]
    assert moy_carts.addon_removed(root)
    _drain(pkg.landed(inst, root + "/" + _addon(root, "b" * 64), None))
    assert moy_carts.load_removed(root) == []
    out, err = [], []
    p = pkg.Pkg(inst, out.append, err.append)
    assert _done(p.remove(["web_console_" + "b" * 12])) == 0, err
    assert moy_carts.load_removed(root) == [moy_carts.ADDON_LINE]
    assert moy_carts.unremove(root, [moy_carts.ADDON_LINE]) == [moy_carts.ADDON_LINE]
    c = _addon(root, "c" * 64)
    applied, errors, _ = apply_ops(root, [{"p": c, "dc": 1}])
    assert (applied, errors) == (1, []) and not os.path.exists(root + "/" + c)
    assert moy_carts.load_removed(root) == [moy_carts.ADDON_LINE]
    applied, errors, _ = apply_ops(root, [{"p": c + "/manifest.json", "t": "{}"}])
    assert (applied, errors) == (1, []) and moy_carts.load_removed(root) == []


def _done(g):
    try:
        while True:
            next(g)
    except StopIteration as stop:
        return stop.value


def test_a_pass_puts_back_a_fixture_a_removal_took(tmp_path):
    root, seed, _shelf = _seeded(tmp_path)
    moy_carts.note_removed(root, "moybyte.brick_siege.moy")
    import shutil
    shutil.rmtree(root + "/moybyte.brick_siege.moy")
    assert moy_carts.put_back(root, seed, ["Brick Siege", "Coin Quest"]) == ["Brick Siege"]
    assert os.path.exists(root + "/moybyte.brick_siege.moy/manifest.json")
    assert moy_carts.load_removed(root) == []


def test_the_shelf_deletes_an_optional_built_in_only_where_its_release_brings_it_back():
    from runtime import cart_manager

    class WS:
        cart_own = None

    m = cart_manager.CartManager.__new__(cart_manager.CartManager)
    m.ws = WS()
    quest = {"path": "/sd/moybyte/carts/moybyte.coin_quest.moy"}
    files = {"path": "/sd/moybyte/carts/moybyte.files.moy"}
    mine = {"path": "/sd/moybyte/carts/mine.moy"}
    assert m.deletable(mine) and not m.deletable(quest) and not m.deletable(files)
    m.ws.cart_own = OWN
    assert m.deletable(quest) and not m.deletable(files)


# -- the Zero: the update's step brings the official carts it holds ---------------


class ZeroInst:
    def __init__(self, root, net):
        self._root, self._net = root, net
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
        return True

    def release(self):
        pass

    def rescan(self, folder=None):
        self.rescans.append(folder)


def test_after_an_update_a_zero_brings_the_official_carts_it_holds_to_the_release(
        tmp_path, keys, monkeypatch):
    root = str(tmp_path / "moy" / "carts")
    os.makedirs(root)
    seeds = tmp_path / "seeds"
    import shutil
    for cid in ("moybyte.files", "moybyte.coin_quest", "moybyte.tap_only_red"):
        shutil.copytree(str(SEEDS / (cid + ".moy")), str(seeds / (cid + ".moy")))
    digest, routes = _release(tmp_path, seeds=seeds)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    monkeypatch.setattr(web_addon, "page_state", lambda: None)
    # the store: an older Coin Quest with a save and a tuning, an up to date Files
    for cid in ("moybyte.files", "moybyte.coin_quest"):
        shutil.copytree(str(seeds / (cid + ".moy")), root + "/" + cid + ".moy")
    quest = root + "/moybyte.coin_quest.moy"
    man = json.loads(open(quest + "/manifest.json").read())
    man["version"] -= 1
    open(quest + "/manifest.json", "w").write(json.dumps(man))
    open(quest + "/pmem.bin", "w").write("[1, 2]")
    open(quest + "/config.json", "w").write('{"speed": 120}')
    net = MemNet(routes)
    step = web_addon.AddonStep(ZeroInst(root, net), OWN, carts=True)
    assert step.wanted()
    assert step.start()
    while step.tick(1000):
        pass
    assert step.error is None
    got = [u.rsplit("/", 1)[-1] for u in net.opened]
    assert got[0] == "carts-tdeck.json"
    assert len(got) == 2 and got[1].startswith("moybyte.coin_quest-"), \
        "only the newer cart is fetched"
    assert json.loads(open(quest + "/manifest.json").read())["version"] == man["version"] + 1
    assert open(quest + "/pmem.bin").read() == "[1, 2]"
    assert open(quest + "/config.json").read() == '{"speed": 120}'
    assert not os.path.exists(root + "/moybyte.tap_only_red.moy"), \
        "a cart it holds, never a new one"
    assert web_addon.official_stamp(root) == digest
    assert not step.wanted(), "a store at its release asks nothing"


def test_a_console_board_s_step_is_the_add_on_s_alone(tmp_path, monkeypatch):
    root = str(tmp_path / "carts")
    os.makedirs(root)
    monkeypatch.setattr(ci, "page_want", lambda: "a" * 64)
    monkeypatch.setattr(web_addon, "page_state", lambda: None)
    assert not web_addon.AddonStep(ZeroInst(root, None), OWN).wanted()


# -- the cable install ------------------------------------------------------------


def test_a_cable_flash_installs_a_new_store_s_set_and_updates_what_a_store_holds():
    offered = {"moybyte.files.moy": 6, "moybyte.coin_quest.moy": 17,
               "moybyte.tap_only_red.moy": 11, "moybyte.letter_blitz.moy": 14}
    image = ("moybyte.files",)
    install = ("moybyte.coin_quest", "moybyte.tap_only_red")
    new = {"moybyte.files.moy": 6, "web_console_0123456789ab.moy": 0}
    assert board_flash.carts_plan(new, offered, install, image) == [
        "moybyte.coin_quest.moy", "moybyte.tap_only_red.moy"]
    used = {"moybyte.files.moy": 5, "moybyte.coin_quest.moy": 16, "kids.moy": 1}
    assert board_flash.carts_plan(used, offered, install, image) == ["moybyte.coin_quest.moy"], \
        "a store in use gets the newer versions of what it holds and nothing it lacks"
    assert board_flash.carts_plan(used, offered, (), image + install) == [], \
        "the image's own seed keeps the carts it carries"
    held = dict(used, **{"moybyte.letter_blitz.moy": 13})
    assert board_flash.carts_plan(held, offered, (), image + install) == [
        "moybyte.letter_blitz.moy"], "an official cart the page installed is brought to the tree"


def test_pkg_installs_a_removed_built_in_from_its_release_asking_nothing(
        tmp_path, keys, monkeypatch):
    root, _seed, _shelf = _seeded(tmp_path)
    digest, routes = _release(tmp_path)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    inst = OwnInst(root, MemNet(routes))
    out, err, asked = [], [], []
    p = pkg.Pkg(inst, out.append, err.append, ask=lambda q: asked.append(q) or True)
    assert _done(p.remove(["moybyte.letter_blitz"])) == 0
    assert _done(p.install(["moybyte.letter_blitz"])) == 0, err
    assert asked == [] and not any("trusted" in line.lower() for line in out)
    assert moy_carts.load_removed(root) == []
    assert os.path.exists(root + "/moybyte.letter_blitz.moy/manifest.json")


def test_a_perf_pass_puts_back_its_fixtures_and_says_so():
    import perf_pass

    class B:
        asked = []

        def pyval(self, expr, timeout=30.0, strict=False):
            self.asked.append(expr)
            return ["Brick Siege"]

    said = []
    stage = perf_pass.Stage(B(), "tdeck", "x", log=said.append)
    stage.conditions = {}
    stage.put_back(["Brick Siege", "Coin Quest"])
    assert B.asked == ["ws.carts.put_back(['Brick Siege', 'Coin Quest'])"]
    assert said and "Brick Siege" in said[0] and "removal" in said[0]
    assert stage.conditions["put_back"] == ["Brick Siege"]


def test_an_unseeded_cart_installs_from_its_release_on_the_host(tmp_path, keys, monkeypatch):
    from runtime import host_app
    root = str(tmp_path / "carts")
    host_app.build_workstation(root)
    digest, routes = _release(tmp_path)
    monkeypatch.setattr(ci, "page_want", lambda: digest)
    carts = ci.fitting(ci.parse_index(routes[OWN], OWN, OWN))
    rows = _rows(root, carts)
    for cid in UNSEEDED:
        assert rows[cid + ".moy"] == "get"
    inst = OwnInst(root, MemNet(routes))
    out, err = [], []
    p = pkg.Pkg(inst, out.append, err.append, ask=lambda q: True)
    assert _done(p.install(sorted(UNSEEDED))) == 0, err
    rows = _rows(root, carts)
    for cid in UNSEEDED:
        assert rows[cid + ".moy"] == "installed"
        assert os.path.exists(_main(root, cid))
    # the host's next seed keeps it, and seeds it no more
    host_app.build_workstation(root)
    assert all(os.path.exists(_main(root, cid)) for cid in UNSEEDED)


def _main(root, cid):
    """The main file of the installed cart `cid` (a compiled one's built by
    its release: tools/web_pack.py's built_seed)."""
    with open(os.path.join(root, cid + ".moy", "manifest.json")) as f:
        return os.path.join(root, cid + ".moy", json.load(f).get("main", "main.py"))
