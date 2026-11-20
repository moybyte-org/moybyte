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


class Updater:
    """The firmware updater the update screen drives, a 64 KB chunk a frame:
    an image found on the card, a manifest online one version up."""
    error = ""
    boot_verdict = None
    joining = False
    CHUNK = 64 * 1024

    def __init__(self, found=True):
        self.found = found
        self.done = self.total = self.dl_done = self.dl_total = 0

    def slot(self):
        return "ota_0"

    def version(self):
        return 7

    def version_label(self):
        return "v0.7"

    def channel(self):
        return "stable"

    def available(self):
        return True

    def online_available(self):
        return True

    def find_bin(self):
        return ("/sd/update/moybyte.bin", 3000 * 1024) if self.found else None

    def begin(self, path):
        self.done, self.total = 0, 3000 * 1024

    def step(self):
        self.done = min(self.total, self.done + self.CHUNK)
        return self.done < self.total

    def check_online(self, channel):
        return {"version": 8, "size": 3000 * 1024, "channel": "stable", "label": "v0.8"}

    def offers(self, manifest, channel):
        return True

    def begin_download(self, manifest):
        self.dl_done, self.dl_total = 0, 3000 * 1024

    def download_step(self):
        self.dl_done = min(self.dl_total, self.dl_done + self.CHUNK)
        return self.dl_done < self.dl_total

    def staged_in_slot(self):
        return False

    def cancel(self):
        self.done = 0

    def download_cancel(self):
        self.dl_done = 0


class C6Updater:
    """The P4's C6 radio upgrade, offering shim 4 over shim 3."""
    installed = 3
    error = ""
    offer = {"version": 4, "hosted": "esp-hosted", "size": 1470 * 1024}

    def check(self, channel):
        return "offer"

    def cancel(self):
        pass


def _open_update(how, found=True):
    def open_(ws):
        ws.updater = Updater(found)
        ws.c6_updater = C6Updater()
        ws.open_settings()
        getattr(ws.update_ui, how)()
    return open_


def _open_tz(ws):
    ws.toggles.set_time_zone("CET-1CEST,M3.5.0,M10.5.0/3", persist=False)
    ws.open_settings()
    ws.settings_layer.open_page("tz")


class _Updater:
    def available(self):
        return True

    def online_available(self):
        return True


class _Ble:
    """A Bluetooth keyboard service as Settings reads it."""
    settings_capable = True

    def __init__(self, state):
        self.state = state

    def settings_status(self):
        return True, self.state, None, None, None


def _open_rows_full(ws):
    """Every row a board can add: the Bluetooth keyboard (scanning, so
    nothing types and the Name is read-only), the idle ladder, the web
    console and its browsers, both update flows, the channel and the C6."""
    from runtime import idle_ladder
    from runtime import moy_loop as L
    ws.use_inputs([("touch", "touch"), ("ble", "keys")])
    ws.keyboard = _Ble("scanning")
    L.trace_init(30, False, 1000)
    ws.idle_ladder = idle_ladder.IdleLadder(ws, L, 300)
    ws.webhost = Webhost(BROWSERS)
    ws.updater = _Updater()
    ws.c6_updater = object()
    ws.settings_layer._free = lambda: "84.5 GB free"     # the store's, not this machine's disk
    ws.open_settings()


# name -> what a run opens
SURFACES = {
    "settings_permissions": P.Surface(_open_settings_page("permissions"), _perms_carts),
    "settings_tz": P.Surface(_open_tz),
    "settings_paired": P.Surface(_open_paired(BROWSERS, True)),
    "settings_paired_none": P.Surface(_open_paired({}, False)),
    "update_sd": P.Surface(_open_update("open_update")),
    "update_sd_none": P.Surface(_open_update("open_update", found=False)),
    "update_online": P.Surface(_open_update("open_update_online")),
    "update_c6": P.Surface(_open_update("open_update_c6")),
    "settings_rows": P.Surface(lambda ws: ws.open_settings()),
    "settings_rows_full": P.Surface(_open_rows_full),
}

# A step a port draws differently from its record on purpose: its cause,
# said once and named by each run that has it.
NO_FALSE_HINT = ("the old page named Remove in its hints with nothing to remove; the port's hints "
                 "name only what the focused node does")

STALE_TOGGLE_HINT = ("the old rows page drew its hints before its rows, so the frame a toggle "
                     "turned still offered the turn it had just made (Turn off on a toggle now off); "
                     "the port's hints are the toggle's as it now is")

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
    # the image on the card: A installs it a chunk a frame, B stops the
    # flash and goes back to Settings
    ("update_sd-tdeck", "update_sd", "tdeck_320x240_fs1_dark", ["k:a", "f:3", "k:b"]),
    ("update_sd-desk", "update_sd", "p4_1024x600_fs2_windowed", ["k:a", "f:3", "k:b"]),
    # none on the card: the error, which A leaves as B does
    ("update_sd_none-tdeck", "update_sd_none", "tdeck_320x240_fs1_dark", ["k:a"]),
    # the check finds one: A downloads it, B cancels
    ("update_online-tdeck", "update_online", "tdeck_320x240_fs1_dark", ["k:a", "f:2", "k:b"]),
    ("update_online-big", "update_online", "big_800x480_fs3_dark", ["k:a", "k:b"]),
    # the C6's offer, which B declines
    ("update_c6-desk", "update_c6", "p4_1024x600_fs2_windowed", ["k:b"]),
    # every kind of row: a link, Text size and Home style cycled there and
    # back, Volume down to its mute and up, the Name typed and stored by
    # Enter (focus goes to the row above) and by leaving it, a toggle turned
    # on and off, the page scrolled to About and a page opened from it and
    # closed
    ("settings_rows-tdeck", "settings_rows", "tdeck_320x240_fs1_dark",
     ["k:down", "k:down", "k:right", "k:left", "k:down", "k:right", "k:down", "k:left", "k:left",
      "k:left", "k:left", "k:left", "k:left", "k:left", "k:right", "k:down", "k:down", "k:down",
      "t:Bo", "enter", "k:down", "t:x", "k:down", "k:a", "k:a", "k:down", "k:down", "k:down",
      "k:down", "k:down", "k:down", "k:a", "f:2", "back", "k:up", "k:up"],
     {24: STALE_TOGGLE_HINT, 25: STALE_TOGGLE_HINT}),
    ("settings_rows-desk", "settings_rows", "p4_1024x600_fs2_windowed",
     ["k:down", "k:down", "k:right", "k:down", "k:down", "k:left", "k:down", "k:down", "k:down",
      "t:Al", "k:down", "k:a", "k:down", "k:down", "k:down", "k:down", "k:down", "k:down", "k:down"]),
    # the Layout row a theme package adds, stepped there and back
    ("settings_rows_layout-tdeck", "settings_rows", "tdeck_320x240_fs1_homegrid",
     ["k:down", "k:down", "k:down", "k:right", "k:left", "k:down"]),
    # every board-only row, and the read-only Name with its note
    ("settings_rows_full-tdeck", "settings_rows_full", "tdeck_320x240_fs1_dark",
     ["k:down"] * 24 + ["k:right", "k:up", "k:up"]),
    ("settings_rows_full-desk", "settings_rows_full", "p4_1024x600_fs2_windowed",
     ["k:down"] * 22 + ["k:a", "k:down", "k:right"]),
)


@pytest.mark.parametrize("run", RUNS, ids=[r[0] for r in RUNS])
def test_a_ported_surface_draws_its_old_frames(run, tmp_path):
    name, surface, config, script = run[:4]
    body = P.load(name)
    assert (body["surface"], body["config"], body["script"]) == (surface, config, script), (
        "the run moved from its record: re-record it with its cause")
    P.check_recorded(name, SURFACES[surface], tmp_path / "carts", run[4] if len(run) > 4 else None)
