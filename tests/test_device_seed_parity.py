"""Host<->device seed-cart parity (#15/#19).

The device does NOT ship system_carts/ -- it seeds from the embedded `CARTS`
list in firmware/.../modules/kid_runtime.py. That list had drifted: stale src,
old edit schemas, and no sprite sheets, so the device drew old inline art and the
paint editor showed an empty sheet. These tests pin the embedded list to the
host source of truth (system_carts/) so any future drift fails loudly here:

  * every system_carts/<cart>/main.py    == the matching CARTS entry "src"
  * every system_carts/<cart>/sprites.kgfx == "sprites"  (or both absent)
  * every manifest "edit" / "config"     == the entry "edit" / "cfg"

Plus: seed_builtins() must actually WRITE sprites.kgfx for a seed that carries
one (the device-side fix), and a complete manifest (canvas + permissions).

kid_runtime is loaded the way tests/test_micropython_spike.py::_load_kid_runtime
does (register the frozen `editors`/`console` names, then exec the device file).
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
}


def _load_kid_runtime():
    # Mirror tests/test_micropython_spike.py::_load_kid_runtime: the device does
    # `from editors import ...` / `from console import ...`, frozen from runtime/.
    for name in ("editors", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
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
