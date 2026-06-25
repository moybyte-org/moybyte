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
    "Star Catcher": "star_catcher",
    "Pixel Pet": "pet",
    "Tiny Runner": "tiny_runner",
    "Hop Quest": "platformer",
    "Tap Only Red": "tap_red",
    "Beeper": "beeper",
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


def test_embedded_edit_and_cfg_match_manifest():
    carts = _carts_by_title()
    for title, cart in carts.items():
        man = _manifest(TITLE_TO_FOLDER[title])
        assert cart["edit"] == man["edit"], "edit schema drifted for " + title
        assert cart["cfg"] == man["config"], "config drifted for " + title


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
    # The already-flashed-device caveat: a cart already on SD is not overwritten,
    # so new seeds-with-sprites only land after the user clears the carts dir.
    from runtime import kid_carts

    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    d = Path(root) / "keep_me.kcart"
    d.mkdir()
    (d / "manifest.json").write_text('{"title": "Keep Me"}', encoding="utf-8")
    seed = [{"title": "Keep Me", "type": "game", "src": "X", "cfg": {}, "edit": [],
             "sprites": "ffff"}]
    kid_carts.seed_builtins(seed, root)
    assert not (d / "sprites.kgfx").exists()        # pre-existing cart untouched
    assert (d / "manifest.json").read_text(encoding="utf-8") == '{"title": "Keep Me"}'
