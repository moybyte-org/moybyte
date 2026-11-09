"""What a dropped cart is named by: `moy_pack.import_base`/`import_target`
and the theme rule in `import_archive`, the one implementation the browser's
drop (worker.js over web_boot) and every other import call."""

import json
import os
import zipfile

from runtime import moy_carts


def test_a_dropped_name_becomes_an_id():
    assert moy_carts.import_base("My Game!.moy.zip") == "local.my_game"
    assert moy_carts.import_base("kenny.Star Catcher.moy.zip") == "kenny.star_catcher"
    assert moy_carts.import_base("celeste.p8.png") == "local.celeste"
    assert moy_carts.import_base("Jelpi.P8") == "local.jelpi"
    assert moy_carts.import_base("local.new_cart.moy") == "local.new_cart"
    assert moy_carts.import_base("!!!.moy") == "local.cart"
    assert moy_carts.import_base("..x.moy") == "local.x"


def test_a_taken_name_takes_the_next_stem(tmp_path):
    root = str(tmp_path)
    for n in ("star.moy", "star_2.moy"):
        os.mkdir(os.path.join(root, n))
    assert moy_carts.import_target(root, "star").endswith("/star_3.moy")
    assert moy_carts.import_target(root, "moon").endswith("/moon.moy")
    long = "a" * 24
    os.mkdir(os.path.join(root, long + ".moy"))
    got = moy_carts.import_target(root, long, theme=True)
    assert got.endswith("/" + "a" * 22 + "_2.moy")


def _theme_zip(path, top, name, text=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(top + "/manifest.json", json.dumps(
            {"title": "T", "main": "theme.json", "moybyte": {"type": "theme"}}))
        z.writestr(top + "/theme.json", text or json.dumps({"name": name}))


def test_a_theme_lands_under_its_own_name(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    arc = str(tmp_path / "a.zip")
    pretty = '{\n  "name": "dusk"\n}\n'
    _theme_zip(arc, "whatever.moy", "dusk", pretty)
    got = moy_carts.import_archive(arc, root)
    assert got.endswith("/dusk.moy")
    with open(got + "/theme.json") as f:
        assert f.read() == pretty                  # kept byte for byte
    again = moy_carts.import_archive(arc, root)
    assert again.endswith("/dusk_2.moy")
    with open(again + "/theme.json") as f:
        assert json.load(f)["name"] == "dusk_2"
    _theme_zip(arc, "Night Owl.moy", "Bad Name")
    owl = moy_carts.import_archive(arc, root)
    assert owl.endswith("/night_owl.moy")
    with open(owl + "/theme.json") as f:
        assert json.load(f)["name"] == "night_owl"


def test_a_flat_cart_zip_is_named_by_its_file(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    arc = str(tmp_path / "upload")
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("manifest.json", '{"title": "Hop"}')
        z.writestr("main.py", "x = 1\n")
    got = moy_carts.import_archive(arc, root, name="Hop Quest.moy.zip")
    assert got.endswith("/local.hop_quest.moy")
    assert sorted(os.listdir(got)) == ["main.py", "manifest.json"]
