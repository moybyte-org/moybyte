"""Every surface ported to `uitree` holds the frames its old code drew,
step by step through its script (tests/uitree_parity.py). The records in
tests/uitree_parity/ are taken from the old code at a commit before it was
replaced, and a port lands with its runs equal to them:

    .venv/bin/python tests/uitree_parity.py record NAME REF   # REF: the commit before the port
    .venv/bin/python tests/uitree_parity.py show NAME         # old | new | difference, first step that moved

A record is re-taken only for a cause named in the commit that does it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uitree_parity as P  # noqa: E402

PERMS_CARTS = (("Radio Tunes", ["network", "media:url", "notifications"]),
               ("Weather", ["network", "http", "autostart"]),
               ("Chat", ["multiplayer", "clipboard", "notifications"]))


def _perms_carts(carts_dir):
    for title, perms in PERMS_CARTS:
        d = Path(carts_dir) / (title.lower().replace(" ", "_") + ".moy")
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"title": title, "canvas": "320x240",
                                                     "moybyte": {"type": "app", "permissions": perms}}))
        (d / "main.py").write_text("def _draw():\n    cls(0)\n")
        (d / "config.json").write_text("{}")


def _open_settings_page(page):
    def open_(ws):
        ws.open_settings()
        ws.settings_layer.open_page(page)
    return open_


# name -> what a run opens
SURFACES = {
    "settings_permissions": P.Surface(_open_settings_page("permissions"), _perms_carts),
}

# (record name, surface, golden configuration, script)
RUNS = (
    ("settings_permissions-tdeck", "settings_permissions", "tdeck_320x240_fs1_dark",
     ["k:down", "k:a", "k:down", "k:down", "k:a", "k:a", "k:down", "k:down", "k:down", "k:down", "k:down",
      "k:a", "k:up"]),
    ("settings_permissions-desk", "settings_permissions", "p4_1024x600_fs2_windowed",
     ["k:down", "k:a", "k:down", "k:down", "k:a", "k:down", "k:down", "k:down", "k:down", "k:a"]),
)


@pytest.mark.parametrize("name,surface,config,script", RUNS, ids=[r[0] for r in RUNS])
def test_a_ported_surface_draws_its_old_frames(name, surface, config, script, tmp_path):
    body = P.load(name)
    assert body["script"] == script, "the script moved from its record: re-record it with its cause"
    P.check_recorded(name, SURFACES[surface], tmp_path / "carts")
