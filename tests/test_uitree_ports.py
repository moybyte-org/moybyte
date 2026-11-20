"""Every surface ported to `uitree` holds the frames its old code drew,
step by step through its script (tests/uitree_parity.py). The records in
tests/uitree_parity/ are taken from the old code at a commit before it was
replaced, and a port lands with its runs equal to them:

    .venv/bin/python tests/uitree_parity.py record NAME REF   # REF: the commit before the port
    .venv/bin/python tests/uitree_parity.py show NAME         # old | new | difference, first step that moved

A record is re-taken only for a cause named in the commit that does it.
"""

import json
import os
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


class Webhost:
    """The browsers a console's web console paired, as Settings reads them."""
    serving = True
    error = None

    def __init__(self, kids):
        self.kids = dict(kids)

    def url(self):
        return "http://192.168.1.155"

    def browsers(self):
        return [(k,) + v for k, v in self.kids.items()]

    def forget(self, kid):
        if kid == "all":
            self.kids.clear()
        else:
            self.kids.pop(kid, None)


BROWSERS = {"k1": (86400, 2 * 86400, "Laptop"), "k2": (86400, 3 * 86400, "Phone"),
            "k3": (86400, 40 * 86400, "")}


def _host_key(ws):
    """A Dropbear ed25519 key file: its fingerprint is what the page cuts into lines."""
    import ssh_service
    body = (len(b"ssh-ed25519").to_bytes(4, "big") + b"ssh-ed25519" + (64).to_bytes(4, "big")
            + bytes(range(64)))
    path = ssh_service._path(ws, ssh_service.HOST_KEY)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)


def _open_paired(kids, key):
    def open_(ws):
        ws.webhost = Webhost(kids)
        if key:
            _host_key(ws)
        ws.open_settings()
        ws.settings_layer.open_page("paired")
    return open_


def _open_tz(ws):
    ws.toggles.set_time_zone("CET-1CEST,M3.5.0,M10.5.0/3", persist=False)
    ws.open_settings()
    ws.settings_layer.open_page("tz")


# name -> what a run opens
SURFACES = {
    "settings_permissions": P.Surface(_open_settings_page("permissions"), _perms_carts),
    "settings_tz": P.Surface(_open_tz),
    "settings_paired": P.Surface(_open_paired(BROWSERS, True)),
    "settings_paired_none": P.Surface(_open_paired({}, False)),
}

# A step a port draws differently from its record on purpose: its cause,
# said once and named by each run that has it.
NO_FALSE_HINT = ("the old page named Remove in its hints with nothing to remove; the port's hints "
                 "name only what the focused node does")

# (record name, surface, golden configuration, script[, {step: cause}])
RUNS = (
    ("settings_permissions-tdeck", "settings_permissions", "tdeck_320x240_fs1_dark",
     ["k:down", "k:a", "k:down", "k:down", "k:a", "k:a", "k:down", "k:down", "k:down", "k:down", "k:down",
      "k:a", "k:up"]),
    ("settings_permissions-desk", "settings_permissions", "p4_1024x600_fs2_windowed",
     ["k:down", "k:a", "k:down", "k:down", "k:a", "k:down", "k:down", "k:down", "k:down", "k:a"]),
    # the picker opens on the zone in force; one chosen sets it and goes back
    # to Time zone's row, which opens the picker again on it
    ("settings_tz-tdeck", "settings_tz", "tdeck_320x240_fs1_dark",
     ["k:down", "k:down", "k:up", "k:a", "f:2", "k:a", "k:up", "k:up", "back"]),
    ("settings_tz-desk", "settings_tz", "p4_1024x600_fs2_windowed",
     ["k:down", "k:down", "k:down", "k:a", "f:2", "k:a", "back"]),
    # each browser by name and day, one forgotten, then Remove all; the host
    # key's fingerprint cut to the page's width
    ("settings_paired-tdeck", "settings_paired", "tdeck_320x240_fs1_dark",
     ["k:down", "k:a", "f:2", "k:down", "k:a", "f:2", "back"], {5: NO_FALSE_HINT, 6: NO_FALSE_HINT}),
    ("settings_paired-desk", "settings_paired", "p4_1024x600_fs2_windowed",
     ["k:down", "k:down", "k:a", "f:2", "k:down", "k:a", "f:2"], {6: NO_FALSE_HINT, 7: NO_FALSE_HINT}),
    ("settings_paired_none-big", "settings_paired_none", "big_800x480_fs3_dark", ["k:down", "back"],
     {0: NO_FALSE_HINT, 1: NO_FALSE_HINT}),
)


@pytest.mark.parametrize("run", RUNS, ids=[r[0] for r in RUNS])
def test_a_ported_surface_draws_its_old_frames(run, tmp_path):
    name, surface, config, script = run[:4]
    body = P.load(name)
    assert (body["surface"], body["config"], body["script"]) == (surface, config, script), (
        "the run moved from its record: re-record it with its cause")
    P.check_recorded(name, SURFACES[surface], tmp_path / "carts", run[4] if len(run) > 4 else None)
