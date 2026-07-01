"""Host<->device seed-cart parity (#15/#19, refactored to build-time codegen).

The device does NOT ship system_carts/ -- it seeds from an embedded `CARTS` list
and falls back to it when SD is unavailable. That list used to be ~1800 lines of
cart bodies hand-copied into kid_runtime.py, and it drifted (stale src, wrong
colorkey, missing sheets). It is now GENERATED from system_carts/ at build time
(tools/gen_device_carts.py -> modules/carts_data.py), and kid_runtime does
`from carts_data import CARTS`.

So `_load_kid_runtime` here registers the same generated data and execs the real
device module, and these tests verify the generated CARTS faithfully mirror the
host source of truth (system_carts/):

  * every system_carts/<cart>/main.py    == the matching CARTS entry "src"
  * every system_carts/<cart>/sprites.kgfx == "sprites"  (or both absent)
  * every manifest "edit" / "config"     == the entry "edit" / "cfg"

Plus: the refactor invariants (kid_runtime imports carts_data, holds no inline
cart constants, and build.sh runs the generator), the generated module round-
trips as valid Python, and seed_builtins() still WRITEs sprites.kgfx + a complete
manifest for a seed that carries them.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FW = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
SYSTEM_CARTS = ROOT / "system_carts"

# Embedded CARTS title -> the system_carts folder it mirrors.
TITLE_TO_FOLDER = {
    "Space Desktop": "wallpaper_space",
    "Ocean Desktop": "ocean",
    "Sakura": "sakura",
    "Star Catcher": "star_catcher",
    "Pixel Pet": "pet",
    "Tiny Runner": "tiny_runner",
    "Hop Quest": "platformer",
    "Sky Run": "scroll_demo",
    "Battle City": "battle_city",
    "Tap Only Red": "tap_red",
    "Tap Game": "tap_game",
    "Beeper": "beeper",
    "WiFi": "wifi",
}


def _load_kid_runtime():
    # Mirror tests/test_micropython_spike.py::_load_kid_runtime: the device does
    # `from editors import ...` / `from audio import ...` / `from console import ...`,
    # frozen from runtime/ (editors + audio first -- console imports both).
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    # kid_runtime now does `from carts_data import CARTS`; on device that module is
    # build-generated from system_carts/. Register the same generated data here so
    # exec succeeds AND this test exercises the real generator -> kid_runtime path.
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module(str(SYSTEM_CARTS))
    spec = importlib.util.spec_from_file_location(
        "kid_runtime", FW / "modules" / "kid_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _carts_by_title():
    return {c["title"]: c for c in _load_kid_runtime().CARTS}


def _manifest(folder):
    return json.loads((SYSTEM_CARTS / (folder + ".kcart") / "manifest.json").read_text(encoding="utf-8"))


# -- every system cart has a faithful embedded mirror -----------------------

def test_every_system_cart_has_an_embedded_entry():
    carts = _carts_by_title()
    for folder in sorted(p.name[:-6] for p in SYSTEM_CARTS.glob("*.kcart")):
        man = _manifest(folder)
        assert man["title"] in carts, "no embedded CARTS entry mirrors " + folder
        assert TITLE_TO_FOLDER[man["title"]] == folder


def test_embedded_src_matches_main_py_byte_for_byte():
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        main = (SYSTEM_CARTS / (folder + ".kcart") / "main.py").read_text(encoding="utf-8")
        assert cart["src"] == main, "embedded src drifted from " + folder + "/main.py"


def test_embedded_sprites_match_sheet_or_both_absent():
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        sheet = SYSTEM_CARTS / (folder + ".kcart") / "sprites.kgfx"
        if sheet.exists():
            assert cart.get("sprites") == sheet.read_text(encoding="utf-8"), \
                "embedded sprites drifted from " + folder + "/sprites.kgfx"
            assert cart["sprites"]                              # non-empty
        else:
            assert "sprites" not in cart, folder + " has no sheet but embeds one"


def test_embedded_sounds_match_sounds_json_or_both_absent():
    # The audio bank (#16) mirrors system_carts/<cart>/sounds.json byte-for-byte
    # (parsed): the device seeds it from the embedded entry, so any drift fails here.
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        path = SYSTEM_CARTS / (folder + ".kcart") / "sounds.json"
        if path.exists():
            assert cart.get("sounds") == json.loads(path.read_text(encoding="utf-8")), \
                "embedded sounds drifted from " + folder + "/sounds.json"
            assert cart["sounds"]                               # non-empty
        else:
            assert "sounds" not in cart, folder + " has no sounds.json but embeds one"


def test_embedded_map_matches_kmap_or_both_absent():
    # The tilemap (#32) mirrors system_carts/<cart>/map.kmap byte-for-byte: the
    # device seeds it from the embedded entry, so any drift fails here. Only carts
    # that ship a map.kmap carry a "map" key.
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        path = SYSTEM_CARTS / (folder + ".kcart") / "map.kmap"
        if path.exists():
            assert cart.get("map") == path.read_text(encoding="utf-8"), \
                "embedded map drifted from " + folder + "/map.kmap"
            assert cart["map"]                                  # non-empty
        else:
            assert "map" not in cart, folder + " has no map.kmap but embeds one"


def test_embedded_edit_and_cfg_match_manifest():
    carts = _carts_by_title()
    for title, cart in carts.items():
        man = _manifest(TITLE_TO_FOLDER[title])
        assert cart["edit"] == man["edit"], "edit schema drifted for " + title
        assert cart["cfg"] == man["config"], "config drifted for " + title


def test_embedded_blocks_match_blocks_json_or_both_absent():
    # A block-authored cart (#29: tap_game) carries its blocks.json into the bundle
    # (parsed) so the on-device block editor opens it as blocks; a code-only cart has
    # no "blocks" key. Drift fails here.
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        path = SYSTEM_CARTS / (folder + ".kcart") / "blocks.json"
        if path.exists():
            assert cart.get("blocks") == json.loads(path.read_text(encoding="utf-8")), \
                "embedded blocks drifted from " + folder + "/blocks.json"
            assert cart["blocks"]                               # non-empty
        else:
            assert "blocks" not in cart, folder + " has no blocks.json but embeds one"


def test_embedded_carry_canvas_and_permissions_for_seed_builtins():
    # seed_builtins (Task 1) writes these into the device manifest, so every
    # embedded entry must carry them.
    for cart in _carts_by_title().values():
        assert "canvas" in cart, cart["title"] + " missing canvas"
        assert "permissions" in cart, cart["title"] + " missing permissions"


# -- the refactor invariants: codegen, no inline duplication ----------------

def test_kid_runtime_imports_generated_carts_with_no_inline_duplication():
    src = (FW / "modules" / "kid_runtime.py").read_text(encoding="utf-8")
    assert "from carts_data import CARTS" in src
    # the ~1800 lines of hand-copied cart bodies must be GONE
    assert "_SRC = \"\"\"" not in src, "inline cart source constants are back"
    assert "_GFX = \"\"\"" not in src and "_GFX =\"\"\"" not in src
    assert "\nCARTS = [" not in src, "inline CARTS list is back"


def test_build_sh_runs_the_generator():
    build = (FW / "build.sh").read_text(encoding="utf-8")
    assert "gen_device_carts.py" in build, "build.sh must generate carts_data.py"


def test_generated_module_is_valid_python_and_round_trips():
    import gen_device_carts
    carts = gen_device_carts.build_carts(str(SYSTEM_CARTS))
    text = gen_device_carts.render_module(carts)
    ns = {}
    exec(compile(text, "carts_data.py", "exec"), ns)   # must be valid Python
    assert ns["CARTS"] == carts                         # render -> parse round-trips


# -- the device seed path actually persists the sheet -----------------------

def test_seed_builtins_writes_sprites_kgfx_when_present(tmp_path):
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    hexs = "0123456789abcdef\n" * 4
    seed = [{
        "title": "Sheety Cart", "type": "game",
        "src": "def _draw():\n    cls(0)\n", "cfg": {"x": 1},
        "edit": [{"key": "x", "type": "int", "card": "X {value}"}],
        "sprites": hexs,
        "canvas": {"width": 480, "height": 270, "palette": "kid64"},
        "permissions": ["graphics", "input"],
    }]
    kid_carts.seed_builtins(seed, root)

    d = Path(root) / "sheety_cart.kcart"
    assert (d / "sprites.kgfx").read_text(encoding="utf-8") == hexs
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["canvas"] == {"width": 480, "height": 270, "palette": "kid64"}
    assert man["permissions"] == ["graphics", "input"]
    assert man["edit"] == seed[0]["edit"]

    # A reload sees the sheet (this is what the device paint editor loads).
    cart = kid_carts.load(str(d))
    assert cart["sprites"] == hexs


def test_seed_builtins_writes_map_kmap_when_present(tmp_path):
    # The tilemap (#32) is seeded the same way as sprites: a seed carrying a "map"
    # blob writes map.kmap, and a reload exposes it as cart["map"].
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    blob = "2 2\n0102\n0300\n"
    seed = [{
        "title": "Mappy Cart", "type": "game",
        "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": [],
        "map": blob,
    }]
    kid_carts.seed_builtins(seed, root)
    d = Path(root) / "mappy_cart.kcart"
    assert (d / "map.kmap").read_text(encoding="utf-8") == blob
    assert kid_carts.load(str(d))["map"] == blob


def test_seed_builtins_skips_sheet_when_seed_has_none(tmp_path):
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    seed = [{"title": "Plain Cart", "type": "game",
             "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": []}]
    kid_carts.seed_builtins(seed, root)
    d = Path(root) / "plain_cart.kcart"
    assert (d / "main.py").is_file()
    assert not (d / "sprites.kgfx").exists()        # nothing written for a sheet-less seed


def test_seed_builtins_leaves_existing_cart_untouched(tmp_path):
    # A cart already on SD is left untouched when the seed is NOT newer (here both
    # default to version 0): equal versions => skip, so on-device edits survive.
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    d = Path(root) / "keep_me.kcart"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Keep Me"}', encoding="utf-8")
    seed = [{"title": "Keep Me", "type": "game", "src": "X", "cfg": {}, "edit": [],
             "sprites": "ffff"}]          # no "version" -> 0, same as the on-SD cart
    kid_carts.seed_builtins(seed, root)
    assert not (d / "sprites.kgfx").exists()        # pre-existing cart untouched
    assert (d / "manifest.json").read_text(encoding="utf-8") == '{"title": "Keep Me"}'


def _seed(title, version, src, **extra):
    s = {"title": title, "type": "game", "src": src, "cfg": {}, "edit": []}
    if version is not None:
        s["version"] = version
    s.update(extra)
    return s


def test_seed_builtins_overwrites_when_version_is_newer(tmp_path):
    # The #47 re-seed: a built-in whose version is newer than the on-SD copy
    # REPLACES it wholesale (destructive). Bumping the version is how a content
    # change reaches an already-seeded device without a manual carts-dir wipe.
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)

    kid_carts.seed_builtins([_seed("Hop", 1, "OLD", sprites="aaaa")], root)
    d = Path(root) / "hop.kcart"
    assert (d / "main.py").read_text(encoding="utf-8") == "OLD"
    # a v2 seed with new code and NO sprite blob must overwrite + drop the stale sheet
    kid_carts.seed_builtins([_seed("Hop", 2, "NEW")], root)
    assert (d / "main.py").read_text(encoding="utf-8") == "NEW"
    assert not (d / "sprites.kgfx").exists()        # code/art wholesale replace
    assert kid_carts.load(str(d))["version"] == 2


def test_seed_builtins_preserves_saves_and_config_across_version_bump(tmp_path):
    # A re-seed replaces code/art but keeps the kid's data: pmem.json (high scores /
    # save state) and config.json (Make-it-mine tuning) survive the bump.
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    kid_carts.seed_builtins([_seed("Hop", 1, "v1")], root)
    d = Path(root) / "hop.kcart"

    cart = kid_carts.load(str(d))                    # the kid plays + tunes the cart
    kid_carts.save_config({**cart, "cfg": {"speed": 9}})   # Make-it-mine edit
    kid_carts.save_pmem(cart, [4242] + [0] * 255)          # a high score

    kid_carts.seed_builtins([_seed("Hop", 2, "v2")], root)  # content update
    assert (d / "main.py").read_text(encoding="utf-8") == "v2"     # code refreshed
    assert kid_carts.load(str(d))["cfg"]["speed"] == 9            # tuning kept
    assert kid_carts.load_pmem(str(d))[0] == 4242                 # save kept


def test_seed_builtins_skips_when_version_not_newer(tmp_path):
    # An equal-or-older built-in never clobbers the on-SD cart (so a kid's edits to
    # a built-in survive every boot until you actually bump the version).
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    kid_carts.seed_builtins([_seed("Hop", 5, "MINE")], root)
    d = Path(root) / "hop.kcart"
    kid_carts.seed_builtins([_seed("Hop", 5, "SAME")], root)   # equal -> skip
    assert (d / "main.py").read_text(encoding="utf-8") == "MINE"
    kid_carts.seed_builtins([_seed("Hop", 3, "OLDER")], root)  # older -> skip
    assert (d / "main.py").read_text(encoding="utf-8") == "MINE"


def test_seed_builtins_refreshes_a_preversion_cart(tmp_path):
    # The real-device case: an existing unversioned cart (version 0) is refreshed by
    # any version>=1 built-in -- this is what auto-fixes stale carts after a bump.
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    d = Path(root) / "hop.kcart"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Hop"}', encoding="utf-8")  # no version
    (d / "main.py").write_text("STALE", encoding="utf-8")
    kid_carts.seed_builtins([_seed("Hop", 1, "FRESH")], root)
    assert (d / "main.py").read_text(encoding="utf-8") == "FRESH"
    assert kid_carts.load(str(d))["version"] == 1


def test_every_system_cart_is_versioned():
    # Every built-in carries a version>=1 so it supersedes a pre-versioning on-SD copy
    # (and the generator propagates it into the embedded seed).
    for cart in _carts_by_title().values():
        assert cart.get("version", 0) >= 1, cart["title"] + " missing manifest version"
