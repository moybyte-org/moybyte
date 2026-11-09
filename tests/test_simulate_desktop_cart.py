"""`tools/simulate_desktop.py --cart PATH` runs the source folder as it is at
launch: an edit to it shows up on the next launch, over the seeded or earlier
copy the store already holds under that name."""

import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import simulate_desktop  # noqa: E402
from runtime import host_app  # noqa: E402

CART = "moybyte.tap_game.moy"


def _launch(carts, src):
    ws = host_app.build_workstation(carts)
    simulate_desktop._open_named_cart(ws, src, carts)
    return ws.cart["src"]


def test_each_launch_runs_the_source_folder_as_it_is_now(tmp_path):
    src = str(tmp_path / "src" / CART)
    shutil.copytree(os.path.join(ROOT, "system_carts", CART), src)
    carts = str(tmp_path / "carts")
    main = os.path.join(src, "main.py")
    for mark in ("# first edit", "# second edit"):
        with open(main, "a") as f:
            f.write("\n%s\n" % mark)
        assert mark in _launch(carts, src)
