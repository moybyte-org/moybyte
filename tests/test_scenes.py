"""Scene as a fourth asset type (#85 Stage 1: format + runtime).

Covers the `.moyscene` store layer (moy_carts scan/load/save + atomic write +
manifest assets.scenes through create/duplicate/seed), the durable undo-journal
commit via Project.commit_scene, and the cart-facing scene()/scene(name)/
load_scene() semantics driven from a real running cart headless (host_app +
Workstation -- the same shared code the device runs). Stage 2 (the placement
editor) is out of scope here.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# A tiny two-scene payload used across the store + runtime tests.
MAIN_SCENE = json.dumps([
    {"tag": "player", "tile": 0, "x": 5, "y": 6, "flip": 1, "flags": {"hp": 3}},
    {"tag": "coin", "tile": 3, "x": 10, "y": 20},
    {"tag": "coin", "tile": 3, "x": 30, "y": 40},
])
LEVEL2_SCENE = json.dumps([
    {"tag": "coin", "tile": 3, "x": 1, "y": 2},
])


# -- widgets.Scenes: the shared data model (#85) -----------------------------

def test_scenes_actor_rows_expose_fields_and_default_active():
    from runtime.widgets import Scenes
    sc = Scenes({"main": MAIN_SCENE, "level2": LEVEL2_SCENE}, ["main", "level2"])
    assert sc.active == "main"                  # element 0 = default active
    rows = sc.scene()                           # bare scene() -> the active scene
    assert [a.tag for a in rows] == ["player", "coin", "coin"]  # order preserved
    a0 = rows[0]
    assert (a0.tag, a0.tile, a0.x, a0.y, a0.flip) == ("player", 0, 5, 6, 1)
    assert a0.flags == {"hp": 3}
    assert rows[1].flags == {}                  # absent flags -> empty dict


def test_scene_named_does_not_switch_active():
    from runtime.widgets import Scenes
    sc = Scenes({"main": MAIN_SCENE, "level2": LEVEL2_SCENE}, ["main", "level2"])
    named = sc.scene("level2")                  # peek WITHOUT switching
    assert len(named) == 1 and named[0].x == 1
    assert sc.active == "main"                  # scene(name) never switches


def test_load_scene_switches_and_reset_restores_default():
    from runtime.widgets import Scenes
    sc = Scenes({"main": MAIN_SCENE, "level2": LEVEL2_SCENE}, ["main", "level2"])
    got = sc.load_scene("level2")
    assert sc.active == "level2" and len(got) == 1
    assert len(sc.scene()) == 1                  # bare scene() now follows the switch
    sc.reset()                                  # a fresh run's _init (#85)
    assert sc.active == "main" and len(sc.scene()) == 3


def test_missing_scene_paths_are_graceful():
    from runtime.widgets import Scenes
    empty = Scenes()                            # a scene-less cart
    assert empty.active is None
    assert empty.scene() == [] and empty.scene("nope") == []
    assert empty.load_scene("nope") == [] and empty.active is None
    bad = Scenes({"main": "not json at all {"}, ["main"])
    assert bad.scene() == []                    # malformed blob -> empty, never raises


def test_scene_returns_fresh_list_each_call():
    from runtime.widgets import Scenes
    sc = Scenes({"main": MAIN_SCENE}, ["main"])
    a = sc.scene()
    a.clear()                                   # mutating the returned list...
    assert len(sc.scene()) == 3                 # ...does not corrupt the cache


def test_reset_drops_in_place_row_mutations():
    # scene() shares the cached Actor rows between calls, so a cart that mutates
    # them in-place (a.x += 1 in _init -- kids do) must get pristine rows on its
    # NEXT run: reset() re-parses, dropping the drift (#85).
    from runtime.widgets import Scenes
    sc = Scenes({"main": MAIN_SCENE}, ["main"])
    sc.scene()[0].x += 99                       # in-place row drift during a run
    assert sc.scene()[0].x == 5 + 99            # visible within the same run
    sc.reset()                                  # the next run's start (Player.start)
    assert sc.scene()[0].x == 5                 # fresh parse, drift gone


# -- moy_carts store: save / load / manifest assets --------------------------

def _cart(tmp_path, **kw):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Scene Cart", root, src="def _draw():\n    cls(0)\n",
                         type="game", **kw)
    return moy_carts, root, c


def test_save_scene_roundtrip_and_manifest_assets(tmp_path):
    mc, root, c = _cart(tmp_path)
    assert c["scenes"] == {} and c["scene_names"] == []   # a fresh cart has none

    mc.save_scene(c, "main", MAIN_SCENE)
    # live cart dict + manifest updated in place
    assert c["scenes"]["main"] == MAIN_SCENE
    assert c["scene_names"] == ["main"]
    man = json.loads((Path(c["path"]) / "manifest.json").read_text())
    assert man["assets"]["scenes"] == ["main"]
    # a second named scene appends to the manifest order
    mc.save_scene(c, "level2", LEVEL2_SCENE)
    assert c["scene_names"] == ["main", "level2"]

    reloaded = mc.load(c["path"])
    assert reloaded["scenes"]["main"] == MAIN_SCENE
    assert reloaded["scenes"]["level2"] == LEVEL2_SCENE
    assert reloaded["scene_names"] == ["main", "level2"]   # manifest order preserved


def test_save_scene_is_atomic(tmp_path):
    mc, root, c = _cart(tmp_path)
    mc.save_scene(c, "main", MAIN_SCENE)
    scene_dir = Path(c["path"]) / "scenes"
    f = scene_dir / "main.moyscene"
    assert f.read_text() == MAIN_SCENE
    assert not (scene_dir / "main.moyscene.tmp").exists()   # no orphan temp file
    # a re-save rotates the previous good copy aside (crash-safe atomic swap)
    mc.save_scene(c, "main", LEVEL2_SCENE)
    assert f.read_text() == LEVEL2_SCENE
    assert (scene_dir / "main.moyscene.bak").read_text() == MAIN_SCENE


def test_scene_names_orders_manifest_first_then_stray_files(tmp_path):
    # A hand-added file the manifest never listed still loads (appended sorted after
    # the manifest order), so the loader is authoritative but robust.
    mc, root, c = _cart(tmp_path)
    mc.save_scene(c, "main", MAIN_SCENE)                    # manifest: ["main"]
    (Path(c["path"]) / "scenes" / "aaa.moyscene").write_text("[]")
    reloaded = mc.load(c["path"])
    assert reloaded["scene_names"] == ["main", "aaa"]       # manifest, then stray


def test_duplicate_copies_scenes_and_order(tmp_path):
    mc, root, c = _cart(tmp_path)
    mc.save_scene(c, "main", MAIN_SCENE)
    mc.save_scene(c, "level2", LEVEL2_SCENE)
    dup = mc.duplicate(c, root, new_title="Copy")
    assert dup["path"] != c["path"]
    assert dup["scenes"] == {"main": MAIN_SCENE, "level2": LEVEL2_SCENE}
    assert dup["scene_names"] == ["main", "level2"]
    man = json.loads((Path(dup["path"]) / "manifest.json").read_text())
    assert man["assets"]["scenes"] == ["main", "level2"]


def test_seed_builtins_writes_scenes_and_manifest_assets(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    seed = {
        "title": "Seeded Scenes", "type": "game", "version": 1,
        "src": "def _draw():\n    cls(0)\n", "cfg": {},
        "scenes": {"main": MAIN_SCENE, "level2": LEVEL2_SCENE},
        "scene_order": ["main", "level2"],
    }
    moy_carts.seed_builtins([seed], root)
    path = root + "/seeded_scenes.moy"
    man = json.loads((Path(path) / "manifest.json").read_text())
    assert man["assets"]["scenes"] == ["main", "level2"]
    loaded = moy_carts.load(path)
    assert loaded["scenes"]["main"] == MAIN_SCENE
    assert loaded["scene_names"] == ["main", "level2"]


def test_seed_preserves_scenes_are_replaced_but_pmem_kept(tmp_path):
    # #47: a version bump re-seeds code + assets (scenes included) but preserves the
    # kid's pmem/config. Prove the scene refresh + the pmem preservation together.
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    v1 = {"title": "Reseed", "type": "game", "version": 1,
          "src": "def _draw():\n    cls(0)\n", "cfg": {},
          "scenes": {"main": MAIN_SCENE}, "scene_order": ["main"]}
    moy_carts.seed_builtins([v1], root)
    path = root + "/reseed.moy"
    (Path(path) / "pmem.json").write_text(json.dumps([42] + [0] * 255))  # a "save"

    v2 = dict(v1, version=2, scenes={"main": LEVEL2_SCENE})              # scene changed
    moy_carts.seed_builtins([v2], root)
    loaded = moy_carts.load(path)
    assert loaded["scenes"]["main"] == LEVEL2_SCENE                      # refreshed
    assert moy_carts.load_pmem(path)[0] == 42                            # save kept


# -- Project.commit_scene: durable undo journal (#85 via #7) ------------------

def _open_scene_cart(tmp_path, scenes=None, src="def _draw():\n    cls(0)\n"):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create("Journaled Scene", carts_dir, src=src, type="game",
                              scenes=scenes, scene_order=list(scenes) if scenes else None)
    ws = host_app.build_workstation(carts_dir)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Journaled Scene":
            ws.launcher.sel = i
            break
    ws.open()                                    # runs _init -> ws.ns is populated
    return ws


def _entries(cart_path):
    from runtime import moy_carts
    return moy_carts._journal_load_entries(cart_path + "/journal/journal.jsonl")


def test_commit_scene_journals_and_undo_restores(tmp_path):
    from runtime import moy_carts
    ws = _open_scene_cart(tmp_path)
    path = ws.cart["path"]

    ws.project.commit_scene("main", MAIN_SCENE)
    ws.project.commit_scene("main", LEVEL2_SCENE)

    ents = [e for e in _entries(path) if e["file"] == "scenes/main.moyscene"]
    assert len(ents) == 2                        # two durable steps
    # the subfolder path flattened into a valid flat snapshot filename (no "/" dir)
    snap = ents[-1]["snap"]
    assert "/" not in snap[len("s/"):]
    assert (Path(path) / "journal" / snap).read_text() == LEVEL2_SCENE

    live = Path(path) / "scenes" / "main.moyscene"
    assert live.read_text() == LEVEL2_SCENE
    assert moy_carts.journal_undo(path) == "scenes/main.moyscene"
    assert live.read_text() == MAIN_SCENE         # undo restored the previous scene


# -- running cart: scene() / scene(name) / load_scene() (#85 Section 6) -------

RUN_SRC = (
    "def _init():\n"
    "    global tags, coins, ppos, named2, switched, active_at_init\n"
    "    tags = [a.tag for a in scene()]\n"
    "    coins = sum(1 for a in scene() if a.tag == 'coin')\n"
    "    ppos = None\n"
    "    for a in scene():\n"
    "        if a.tag == 'player':\n"
    "            ppos = (a.x, a.y, a.flip, a.flags.get('hp'))\n"
    "    named2 = len(scene('level2'))\n"      # peek without switching
    "    switched = len(load_scene('level2'))\n"  # switch the active scene
    "def _draw():\n"
    "    cls(0)\n"
)


def test_running_cart_reads_scene_into_init(tmp_path):
    ws = _open_scene_cart(tmp_path, src=RUN_SRC,
                          scenes={"main": MAIN_SCENE, "level2": LEVEL2_SCENE})
    ns = ws.ns
    assert ns["tags"] == ["player", "coin", "coin"]      # order = draw order
    assert ns["coins"] == 2
    assert ns["ppos"] == (5, 6, 1, 3)                    # x/y/flip/flags read through
    assert ns["named2"] == 1                             # scene('level2') peeked
    assert ns["switched"] == 1                           # load_scene switched + returned
    assert ws.scenes.active == "level2"                  # the switch stuck for this run


# A cart that does NOT switch -- it just records how many actors the ACTIVE scene has.
COUNT_SRC = (
    "def _init():\n"
    "    global n\n"
    "    n = len(scene())\n"
    "def _draw():\n    cls(0)\n"
)


def test_active_scene_resets_on_rerun(tmp_path):
    # load_scene() must not leak across a RE-RUN of the same project: Player.start
    # resets the active scene to the default before _init runs ("resets on next
    # _init", #85 Section 6). _start() re-runs without rebuilding the Scenes object,
    # so this exercises the Player reset (not the fresh-workspace rebuild).
    ws = _open_scene_cart(tmp_path, src=COUNT_SRC,
                          scenes={"main": MAIN_SCENE, "level2": LEVEL2_SCENE})
    assert ws.ns["n"] == 3                               # active == main (3 actors)
    ws.scenes.load_scene("level2")                       # a leftover switch (1 actor)
    assert ws.scenes.active == "level2"
    ws._start()                                          # re-run the SAME project
    assert ws.scenes.active == "main"                    # reset to default first...
    assert ws.ns["n"] == 3                               # ...so _init saw main again


def test_scene_less_cart_still_has_working_accessors(tmp_path):
    # A cart with no scenes must still get callable scene()/load_scene() (empty),
    # never a NameError -- the base key-set is identical either way.
    ws = _open_scene_cart(tmp_path, src=(
        "def _init():\n"
        "    global n, sw\n"
        "    n = len(scene())\n"
        "    sw = len(load_scene('nope'))\n"
        "def _draw():\n    cls(0)\n"))
    assert ws.ns["n"] == 0 and ws.ns["sw"] == 0
