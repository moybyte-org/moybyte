"""Play's tiles wear covers: every seed cart on the home's Games and Apps tabs
draws its own cover.png (SPEC.md 3.6) through the shelf's cover cache, except
the carts named in GENERATED, which wear the generated cover
(`ui_cover.initial_cover`, the design for a cart with no art) for the reason
given beside each.

A seed game's cover is a capture of its scene and a seed app's is its icon on
its title's block (tools/gen_covers.py); a new seed game or app ships one, or
names here why it does not."""

import json
from pathlib import Path

import pytest

from runtime import cover_cache, cover_png
from ws_helpers import build_ws

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "system_carts"

# Seed carts on Play that draw the generated cover, and why.
GENERATED = {
    "moybyte.bench": "the perf ledger's fixture: its frame is noise by design",
    "moybyte.bench_lua": "the perf ledger's fixture: its frame is noise by design",
    "moybyte.coin_quest": "the blocks example: its scene is a few coins on a plain field",
    "moybyte.pin_light": "a pin cart whose screen is text only",
}


def _land(ws, cart, frames=300):
    """Step the per-frame cover budget until `cart`'s BASE lands, or None
    once the cache knows it has no cover."""
    for _ in range(frames):
        ws.covers.begin_frame()
        img = ws.covers.cover_for(cart, cover_cache.BASE)
        if img is not None or ws.covers.none_for(cart):
            return img
    raise AssertionError("%s: its cover never landed" % cart.get("title"))


def _seed_ids():
    return {d.name[:-4] for d in SEEDS.glob("*.moy")}


@pytest.mark.parametrize("tab", ["games", "apps"])
def test_every_seed_tile_on_play_draws_its_cover(tmp_path, tab):
    ws = build_ws(tmp_path)
    ws.set_home_tab(tab)
    seeds = _seed_ids()
    tiles = [it for it in ws.launcher.items if it.get("id") in seeds]
    assert tiles, "no seed cart on the %s tab" % tab
    bare, extra = [], []
    for it in tiles:
        img = _land(ws, it)
        if it["id"] in GENERATED:
            if img is not None:
                extra.append(it["id"])
        elif img is None or (img.w, img.h) != (cover_png.SIDE, cover_png.SIDE):
            bare.append(it["id"])
    assert not bare, "seed carts on %s with no cover: %s" % (tab, bare)
    assert not extra, "GENERATED names carts that have a cover now: %s" % extra


def test_every_seed_app_ships_a_cover():
    """Apps are not all on the Apps tab (Settings reaches Appearance and My
    Art), and each still shows its cover in Files and the Projects window."""
    for d in sorted(SEEDS.glob("*.moy")):
        with open(d / "manifest.json") as f:
            if json.load(f)["moybyte"].get("type") != "app":
                continue
        data = (d / "cover.png").read_bytes()
        assert cover_png.decode(data) is not None, d.name
