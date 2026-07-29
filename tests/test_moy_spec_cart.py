"""moy-spec (SPEC.md) cart semantics on the reference console.

A `"format": "moy-1"` manifest is the brand-neutral spec cart: Lua by
definition, main.lua, the spec's 30fps default tick, a game. The store must
apply those defaults, carry the spec-only fields (`palette`, `extensions`)
through load/duplicate, the Player must refuse a cart requiring an extension
this build doesn't implement (SPEC.md 10), and a cart-supplied palette
(SPEC.md 2.2) must be live for exactly the run's lifetime.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RED64 = ["FF0000"] * 64


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("no cart titled " + title)


def _write_moy1(root, name, manifest, src="function _draw()\n  cls(1)\nend\n"):
    d = Path(root) / (name + ".moy")
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / manifest.get("main", "main.lua")).write_text(src)
    return str(d)


# -- store: moy-1 defaults + spec-field passthrough -----------------------------

def test_moy1_defaults(tmp_path):
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {"format": "moy-1", "title": "Spec"})
    cart = moy_carts.load(path)
    assert cart["runtime"] == "lua"          # spec carts are Lua by definition
    assert cart["main"] == "main.lua"
    assert cart["type"] == "game"
    assert cart["fps"] == 30                 # SPEC.md 5: 30 is the default tick
    assert cart["palette"] is None
    assert cart["extensions"] == []


def test_moybyte_defaults_unchanged(tmp_path):
    """The moy-1 branch must not move moybyte's own defaults."""
    from runtime import moy_carts
    d = Path(tmp_path) / "old.moy"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"title": "Old"}))
    (d / "main.py").write_text("def _draw():\n    pass\n")
    cart = moy_carts.load(str(d))
    assert cart["runtime"] == "python" and cart["main"] == "main.py"
    assert cart["type"] == "app" and cart["fps"] == 0


def test_spec_fields_survive_load_and_duplicate(tmp_path):
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {
        "format": "moy-1", "title": "Spec",
        "palette": RED64, "extensions": ["layers"],
    })
    cart = moy_carts.load(path)
    assert cart["palette"] == RED64
    assert cart["extensions"] == ["layers"]
    dup = moy_carts.duplicate(cart, str(tmp_path))
    assert dup["palette"] == RED64
    assert dup["extensions"] == ["layers"]


# -- Player: the SPEC.md 10 refusal + the SPEC.md 2.2 palette lifetime ----------

def test_unknown_extension_refused(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Warp", root, src="def _draw():\n    pass\n",
                     type="game", extensions=["warpdrive"])
    ws = _ws(tmp_path)                 # scans the pre-created cart with the seeds
    _open(ws, "Warp")
    assert ws.cart_error and "warpdrive" in ws.cart_error


def test_cart_palette_applied_then_restored(tmp_path):
    from runtime import moy_carts
    root = str(Path(tmp_path) / "carts")
    Path(root).mkdir(parents=True, exist_ok=True)
    moy_carts.create("Reddish", root, src="def _draw():\n    pass\n",
                     type="game", palette=RED64)
    from runtime import host_app
    ws = host_app.build_workstation(root)
    default_red = ws.canvas.palette[8]           # MOY64 idx 8 (FF004D)
    _open(ws, "Reddish")
    assert not ws.cart_error
    assert tuple(ws.canvas.palette[8]) == (255, 0, 0)   # cart table live
    ws.player.release_world()
    assert tuple(ws.canvas.palette[8]) == tuple(default_red)  # restored
