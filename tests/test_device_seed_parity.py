"""Host<->device seed-cart parity (#15/#19, refactored to build-time codegen).

The device does NOT ship system_carts/ -- it seeds from an embedded `CARTS` list
and falls back to it when SD is unavailable. That list used to be ~1800 lines of
cart bodies hand-copied into moy_runtime.py, and it drifted (stale src, wrong
colorkey, missing sheets). It is now GENERATED from system_carts/ at build time
(tools/gen_device_carts.py -> modules/carts_data.py), and moy_runtime does
`from carts_data import CARTS`.

So `_load_moy_runtime` here registers the same generated data and execs the real
device module, and these tests verify the generated CARTS faithfully mirror the
host source of truth (system_carts/):

  * every system_carts/<cart>/main.py    == the matching CARTS entry "src"
  * every system_carts/<cart>/sprites.moygfx == "sprites"  (or both absent)
  * every manifest "edit" / "config"     == the entry "edit" / "cfg"

Plus: the refactor invariants (moy_runtime imports carts_data, holds no inline
cart constants, and build.sh runs the generator), the generated module round-
trips as valid Python, and seed_builtins() still WRITEs sprites.moygfx + a complete
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
    "Moy Night": "moy_night",
    "Open Machine": "open_machine",
    "My Art": "my_art",
    "Sakura": "sakura",
    "Sakura Lua": "sakura_lua",
    "Ray Test": "ray_test",
    "Ray Lua": "ray_lua",
    "Layer Test": "layer_test",
    "Bullet Storm": "bullet_storm",
    "Star Catcher": "star_catcher",
    "Pixel Pet": "pet",
    "Tiny Runner": "tiny_runner",
    "Hop Quest": "platformer",
    "Sky Run": "scroll_demo",
    "Brick Siege": "brick_siege",
    "Harpoon Pop": "harpoon_pop",
    "Tap Only Red": "tap_red",
    "Tap Game": "tap_game",
    "Coin Quest": "coin_quest",
    "Beeper": "beeper",
    "Letter Blitz": "letter_blitz",
    "Paint": "paint",
    "Appearance": "theme_picker",
    "Writer": "writer",
    "Calc": "calc",
    "Storybook": "storybook",
    "Sheets": "sheets",
    "Files": "files",
    "WiFi": "wifi",
}


def _load_moy_runtime():
    # Mirror tests/test_micropython_spike.py::_load_moy_runtime: the device does
    # `from editors import ...` / `from audio import ...` / `from console import ...`,
    # frozen from runtime/ (editors + audio first -- console imports both).
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    # moy_runtime also imports the device-only leaves (device_api, device_canvas,
    # moy_lua_glue, ...). conftest's _DeviceModuleFinder resolves those from the
    # authored modules/ tree on demand, so there is deliberately no list here --
    # the one that used to live here omitted moy_lua_glue and made this file fail
    # standalone while passing in a full run.
    # moy_runtime now does `from carts_data import CARTS`; on device that module is
    # build-generated from system_carts/. Register the same generated data here so
    # exec succeeds AND this test exercises the real generator -> moy_runtime path.
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module(str(SYSTEM_CARTS))
    spec = importlib.util.spec_from_file_location(
        "moy_runtime", FW / "modules" / "moy_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _carts_by_title():
    return {c["title"]: c for c in _load_moy_runtime().CARTS}


def _manifest(folder):
    return json.loads((SYSTEM_CARTS / (folder + ".moy") / "manifest.json").read_text(encoding="utf-8"))


# -- every system cart has a faithful embedded mirror -----------------------

def test_every_system_cart_has_an_embedded_entry():
    carts = _carts_by_title()
    for folder in sorted(p.name[:-4] for p in SYSTEM_CARTS.glob("*.moy")):
        man = _manifest(folder)
        assert man["title"] in carts, "no embedded CARTS entry mirrors " + folder
        assert TITLE_TO_FOLDER[man["title"]] == folder


def test_embedded_src_matches_main_byte_for_byte():
    # The manifest's "main" names the source file (#67: main.lua for a lua cart).
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        mainf = _manifest(folder).get("main", "main.py")
        main = (SYSTEM_CARTS / (folder + ".moy") / mainf).read_text(encoding="utf-8")
        assert cart["src"] == main, "embedded src drifted from " + folder + "/" + mainf
        assert cart.get("main", "main.py") == mainf
        assert cart.get("runtime", "python") == _manifest(folder).get("runtime", "python")


def test_embedded_sprites_match_sheet_or_both_absent():
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        sheet = SYSTEM_CARTS / (folder + ".moy") / "sprites.moygfx"
        if sheet.exists():
            assert cart.get("sprites") == sheet.read_text(encoding="utf-8"), \
                "embedded sprites drifted from " + folder + "/sprites.moygfx"
            assert cart["sprites"]                              # non-empty
        else:
            assert "sprites" not in cart, folder + " has no sheet but embeds one"


def test_embedded_sounds_match_sounds_json_or_both_absent():
    # The audio bank (#16) mirrors system_carts/<cart>/sounds.json byte-for-byte
    # (parsed): the device seeds it from the embedded entry, so any drift fails here.
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        path = SYSTEM_CARTS / (folder + ".moy") / "sounds.json"
        if path.exists():
            assert cart.get("sounds") == json.loads(path.read_text(encoding="utf-8")), \
                "embedded sounds drifted from " + folder + "/sounds.json"
            assert cart["sounds"]                               # non-empty
        else:
            assert "sounds" not in cart, folder + " has no sounds.json but embeds one"


def test_embedded_map_matches_kmap_or_both_absent():
    # The tilemap (#32) mirrors system_carts/<cart>/map.moymap byte-for-byte: the
    # device seeds it from the embedded entry, so any drift fails here. Only carts
    # that ship a map.moymap carry a "map" key.
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        path = SYSTEM_CARTS / (folder + ".moy") / "map.moymap"
        if path.exists():
            assert cart.get("map") == path.read_text(encoding="utf-8"), \
                "embedded map drifted from " + folder + "/map.moymap"
            assert cart["map"]                                  # non-empty
        else:
            assert "map" not in cart, folder + " has no map.moymap but embeds one"


def test_embedded_images_match_or_both_absent():
    # Paint-image assets (#63 Fold 3) mirror system_carts/<cart>/images/*.moyimg
    # byte-for-byte: gen_device_carts reads the images/ folder into cart["images"]
    # ({name: blob}), and seed_builtins writes them back, so any drift fails here.
    # sakura ships images/bg.moyimg; carts without an images/ folder carry no key.
    carts = _carts_by_title()
    for title, cart in carts.items():
        folder = TITLE_TO_FOLDER[title]
        idir = SYSTEM_CARTS / (folder + ".moy") / "images"
        on_disk = {}
        if idir.is_dir():
            for p in sorted(idir.iterdir()):
                if p.suffix == ".moyimg":
                    on_disk[p.stem] = p.read_text(encoding="utf-8")
        if on_disk:
            assert cart.get("images") == on_disk, \
                "embedded images drifted from " + folder + "/images/"
        else:
            assert "images" not in cart, folder + " has no images/ but embeds one"


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
        path = SYSTEM_CARTS / (folder + ".moy") / "blocks.json"
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

def test_moy_runtime_imports_generated_carts_with_no_inline_duplication():
    src = (FW / "modules" / "moy_runtime.py").read_text(encoding="utf-8")
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
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    hexs = "0123456789abcdef\n" * 4
    seed = [{
        "title": "Sheety Cart", "type": "game",
        "src": "def _draw():\n    cls(0)\n", "cfg": {"x": 1},
        "edit": [{"key": "x", "type": "int", "card": "X {value}"}],
        "sprites": hexs,
        "canvas": {"width": 480, "height": 270, "palette": "moy64"},
        "permissions": ["graphics", "input"],
    }]
    moy_carts.seed_builtins(seed, root)

    d = Path(root) / "sheety_cart.moy"
    assert (d / "sprites.moygfx").read_text(encoding="utf-8") == hexs
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["canvas"] == {"width": 480, "height": 270, "palette": "moy64"}
    assert man["permissions"] == ["graphics", "input"]
    assert man["edit"] == seed[0]["edit"]

    # A reload sees the sheet (this is what the device paint editor loads).
    cart = moy_carts.load(str(d))
    assert cart["sprites"] == hexs


def test_seed_builtins_writes_map_kmap_when_present(tmp_path):
    # The tilemap (#32) is seeded the same way as sprites: a seed carrying a "map"
    # blob writes map.moymap, and a reload exposes it as cart["map"].
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    blob = "2 2\n0102\n0300\n"
    seed = [{
        "title": "Mappy Cart", "type": "game",
        "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": [],
        "map": blob,
    }]
    moy_carts.seed_builtins(seed, root)
    d = Path(root) / "mappy_cart.moy"
    assert (d / "map.moymap").read_text(encoding="utf-8") == blob
    assert moy_carts.load(str(d))["map"] == blob


def test_seed_builtins_writes_images_when_present(tmp_path):
    # Paint-image assets (#63) seed the same way as sprites/map: a seed carrying an
    # "images" dict writes images/<name>.moyimg, and a reload exposes them as
    # cart["images"] (what the make_api image() accessor decodes).
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    blob = '{"format": "moyimg-v1", "w": 2, "h": 1, "data": "eJxj5QQAABUADw=="}'
    seed = [{
        "title": "Arty Cart", "type": "game",
        "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": [],
        "images": {"bg": blob},
    }]
    moy_carts.seed_builtins(seed, root)
    d = Path(root) / "arty_cart.moy"
    assert (d / "images" / "bg.moyimg").read_text(encoding="utf-8") == blob
    assert moy_carts.load(str(d))["images"] == {"bg": blob}


def test_seed_builtins_skips_sheet_when_seed_has_none(tmp_path):
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    seed = [{"title": "Plain Cart", "type": "game",
             "src": "def _draw():\n    cls(0)\n", "cfg": {}, "edit": []}]
    moy_carts.seed_builtins(seed, root)
    d = Path(root) / "plain_cart.moy"
    assert (d / "main.py").is_file()
    assert not (d / "sprites.moygfx").exists()        # nothing written for a sheet-less seed


def test_seed_builtins_leaves_existing_cart_untouched(tmp_path):
    # A cart already on SD is left untouched when the seed is NOT newer (here both
    # default to version 0): equal versions => skip, so on-device edits survive.
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    d = Path(root) / "keep_me.moy"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Keep Me"}', encoding="utf-8")
    seed = [{"title": "Keep Me", "type": "game", "src": "X", "cfg": {}, "edit": [],
             "sprites": "ffff"}]          # no "version" -> 0, same as the on-SD cart
    moy_carts.seed_builtins(seed, root)
    assert not (d / "sprites.moygfx").exists()        # pre-existing cart untouched
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
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)

    moy_carts.seed_builtins([_seed("Hop", 1, "OLD", sprites="aaaa")], root)
    d = Path(root) / "hop.moy"
    assert (d / "main.py").read_text(encoding="utf-8") == "OLD"
    # a v2 seed with new code and NO sprite blob must overwrite + drop the stale sheet
    moy_carts.seed_builtins([_seed("Hop", 2, "NEW")], root)
    assert (d / "main.py").read_text(encoding="utf-8") == "NEW"
    assert not (d / "sprites.moygfx").exists()        # code/art wholesale replace
    assert moy_carts.load(str(d))["version"] == 2


def test_seed_builtins_preserves_saves_and_config_across_version_bump(tmp_path):
    # A re-seed replaces code/art but keeps the kid's data: pmem.json (high scores /
    # save state) and config.json (Make-it-mine tuning) survive the bump.
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    moy_carts.seed_builtins([_seed("Hop", 1, "v1")], root)
    d = Path(root) / "hop.moy"

    cart = moy_carts.load(str(d))                    # the kid plays + tunes the cart
    moy_carts.save_config({**cart, "cfg": {"speed": 9}})   # Make-it-mine edit
    moy_carts.save_pmem(cart, [4242] + [0] * 255)          # a high score

    moy_carts.seed_builtins([_seed("Hop", 2, "v2")], root)  # content update
    assert (d / "main.py").read_text(encoding="utf-8") == "v2"     # code refreshed
    assert moy_carts.load(str(d))["cfg"]["speed"] == 9            # tuning kept
    assert moy_carts.load_pmem(str(d))[0] == 4242                 # save kept


def test_seed_builtins_skips_when_version_not_newer(tmp_path):
    # An equal-or-older built-in never clobbers the on-SD cart (so a kid's edits to
    # a built-in survive every boot until you actually bump the version).
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    moy_carts.seed_builtins([_seed("Hop", 5, "MINE")], root)
    d = Path(root) / "hop.moy"
    moy_carts.seed_builtins([_seed("Hop", 5, "SAME")], root)   # equal -> skip
    assert (d / "main.py").read_text(encoding="utf-8") == "MINE"
    moy_carts.seed_builtins([_seed("Hop", 3, "OLDER")], root)  # older -> skip
    assert (d / "main.py").read_text(encoding="utf-8") == "MINE"


def test_seed_builtins_refreshes_a_preversion_cart(tmp_path):
    # The real-device case: an existing unversioned cart (version 0) is refreshed by
    # any version>=1 built-in -- this is what auto-fixes stale carts after a bump.
    from runtime import moy_carts

    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    d = Path(root) / "hop.moy"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Hop"}', encoding="utf-8")  # no version
    (d / "main.py").write_text("STALE", encoding="utf-8")
    moy_carts.seed_builtins([_seed("Hop", 1, "FRESH")], root)
    assert (d / "main.py").read_text(encoding="utf-8") == "FRESH"
    assert moy_carts.load(str(d))["version"] == 1


def test_every_system_cart_is_versioned():
    # Every built-in carries a version>=1 so it supersedes a pre-versioning on-SD copy
    # (and the generator propagates it into the embedded seed).
    for cart in _carts_by_title().values():
        assert cart.get("version", 0) >= 1, cart["title"] + " missing manifest version"


def test_every_system_app_claims_its_device_seeded_folder(tmp_path):
    """host == device for APP IDENTITY: the host store copies each cart's SOURCE
    folder name (theme_picker.moy), but the device's seed_builtins names the
    seeded folder from the TITLE slug (appearance.moy). An is_app that only
    knows the source name silently fails to claim its cart on device, and the
    app becomes unopenable -- Settings -> APPEARANCE did nothing on the P4
    (on-glass, 2026-07-25). Every registered system app must accept BOTH."""
    from runtime import host_app, moy_carts

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws._apps, "no system apps registered"
    for app, _text in ws._apps:
        claimed = [c for c in ws._all_carts if app.is_app(c)]
        assert len(claimed) == 1, \
            "%s claims %d carts on the host store" % (app.id, len(claimed))
        cart = dict(claimed[0])
        root, _, _base = cart["path"].replace("\\", "/").rpartition("/")
        # Re-path it exactly the way the device's seed_builtins would name it.
        cart["path"] = root + "/" + moy_carts.slug(cart["title"]) + ".moy"
        assert app.is_app(cart), \
            "%s does not claim its DEVICE-seeded folder %s" % (app.id,
                                                               cart["path"])
