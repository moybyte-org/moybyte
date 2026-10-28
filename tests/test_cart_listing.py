"""What a list says about a cart (moy-spec SPEC.md 3.7): `description`, `genre`
and `tags`, read as every host reads them.

  * `runtime/cart_listing.py`, `moy_carts.load` (the manifest on disk) and
    `cart_index.parse_index` (an index entry) each reach moy-spec's verdict on
    every case of `conformance/listing.json` (`tests/listing_vectors/`), the
    degrade-to-absent rules included: none of the three fields ever refuses a
    cart;
  * a copy of a cart and a seeded built-in keep the three fields;
  * the home says the genre where it has one, and the seed games name one.

The native store's reading of the same cases is held to this one's in
`tests/test_store_native.py`.
"""

import json
from pathlib import Path

import pytest

from cart_store_fixtures import Repo
from runtime import cart_index as ci
from runtime import cart_listing as cl
from runtime import moy_carts

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "listing_vectors" / "listing.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
IDS = [c["name"] for c in CASES]


def _want(case):
    return (case["description"], case["genre"], case["tags"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_reader_reaches_moy_specs_verdict(case):
    assert cl.listing(case["manifest"]) == _want(case)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_manifest_on_disk_reads_the_same_and_the_cart_stays(tmp_path, case):
    d = tmp_path / "a.moy"
    d.mkdir()
    man = {"format": "moy-1", "title": "T", "main": "main.lua"}
    man.update(case["manifest"])
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (d / "main.lua").write_text("function _draw() cls(0) end\n", encoding="utf-8")
    cart = moy_carts.load(str(d))
    assert cart is not None
    assert (cart["description"], cart["genre"], cart["tags"]) == _want(case)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_an_index_entry_reads_the_same_and_the_cart_stays(case):
    repo = Repo("http://x.test/carts")
    entry = repo.add("good")
    entry.update(case["manifest"])
    (c,) = ci.parse_index(repo.index(), repo.url("index.json"))
    got = (c.get("description"), c.get("genre"), c.get("tags", []))
    assert got == _want(case)
    for key, absent in (("description", None), ("genre", None), ("tags", [])):
        if _want(case)[("description", "genre", "tags").index(key)] == absent:
            assert key not in c            # absent is absent, never a null


def test_an_entry_without_the_fields_is_as_it_was():
    repo = Repo("http://x.test/carts")
    repo.add("good")
    (c,) = ci.parse_index(repo.index(), repo.url("index.json"))
    assert not {"description", "genre", "tags"} & set(c)


def test_a_manifest_with_none_of_them_reads_none(tmp_path):
    cart = moy_carts.create("Plain", str(tmp_path), src="def _draw():\n    cls(1)\n")
    assert (cart["description"], cart["genre"], cart["tags"]) == (None, None, [])


def test_a_copy_keeps_what_the_list_says(tmp_path):
    src = moy_carts.create("Port", str(tmp_path), src="def _draw():\n    cls(1)\n",
                           listing={"description": "A port.", "genre": "puzzle",
                                    "tags": ["pico8", "two-player"]})
    assert (src["description"], src["genre"], src["tags"]) == (
        "A port.", "puzzle", ["pico8", "two-player"])
    dup = moy_carts.duplicate(src, str(tmp_path))
    assert (dup["description"], dup["genre"], dup["tags"]) == (
        "A port.", "puzzle", ["pico8", "two-player"])
    man = json.loads((Path(dup["path"]) / "manifest.json").read_text())
    assert man["tags"] == ["pico8", "two-player"] and "ported_from" not in man


def test_a_seeded_built_in_keeps_what_the_list_says(tmp_path):
    seed = [{"title": "Seeded", "type": "game", "src": "def _draw():\n    cls(1)\n",
             "cfg": {}, "description": "Seeded for a test.", "genre": "toy",
             "tags": ["demo"]}]
    moy_carts.seed_builtins(seed, str(tmp_path))
    (cart,) = [c for c in moy_carts.scan(str(tmp_path)) if c["title"] == "Seeded"]
    assert (cart["description"], cart["genre"], cart["tags"]) == (
        "Seeded for a test.", "toy", ["demo"])


def test_the_genre_is_said_title_cased_and_rpg_in_capitals():
    assert [cl.genre_word(g) for g in cl.GENRES] == [
        "Action", "Adventure", "Arcade", "Music", "Platformer", "Puzzle",
        "Racing", "RPG", "Shooter", "Sports", "Strategy", "Toy"]
    assert cl.genre_word(None) is None and cl.genre_word("") is None


def test_the_seed_games_each_name_a_genre_and_a_line():
    """The home's metadata line and the desk's details pane have slots for
    both; a seed game that left them empty would show neither."""
    games = []
    for d in sorted((ROOT / "system_carts").glob("*.moy")):
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        if man.get("moybyte", {}).get("type") != "game":
            continue
        games.append(d.name)
        assert cl.genre(man.get("genre")), d.name
        text = man.get("description")
        assert text and cl.description(text) == text, d.name
    assert len(games) >= 15
