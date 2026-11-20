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


class _Station:
    """A Wi-Fi station as Settings' Wi-Fi panel reads it: joined to its saved
    network, three found by a scan (one open, two locked), joins that answer
    at once (a typed password right only when it is "abc")."""

    def __init__(self, nets=True):
        self.on, self.saved = "MoyNet", {"MoyNet": "x"}
        self.nets = [("MoyNet", -40, True), ("Guest", -60, False), ("Cafe", -70, True)] if nets else []

    def status(self):
        return (True, self.on, "192.168.1.155") if self.on else (False, None, None)

    def known(self):
        return list(self.saved)

    def scan(self):
        return list(self.nets)

    def connect(self, ssid, pw):
        ok = ssid == "Guest" or pw == "abc" or (not pw and ssid in self.saved)
        if ok:
            self.on = ssid
            self.saved[ssid] = pw or self.saved.get(ssid, "")
        return ok

    def forget(self, ssid):
        self.saved.pop(ssid, None)


def _open_wifi(nets=True):
    def open_(ws):
        ws.wifi = _Station(nets)
        ws.open_settings()
        ws.settings_layer.open_page("wifi")
    return open_


def _open_wifi_popover(ws):
    ws.wifi = _Station()
    ws._golden_popover = True           # the goldens' settle leaves it open
    ws.widget_tap("wifi")


# name -> what a run opens
def _code_probe(ws):
    """The Code tab's state in a line: its text, caret and selection, the
    find strip, the open overlay, the error mark, the tab."""
    import hashlib
    cl, ed = ws.code_layer, ws.editor
    over = ("complete" if cl._cmp_open else "goto" if cl._jump_open else "scripts" if cl._files_open
            else "none")
    return "%s L%d:%d sel=%r find=%s/%r over=%s err=%r tab=%s" % (
        hashlib.sha1(ed.text().encode()).hexdigest()[:12], ed.row + 1, ed.col + 1,
        ed.selection_bounds() if ed.sel is not None else None, cl._find_open, cl._find_q, over,
        ws.code_err_row, ws.editor_app.tab)


def _frame_probe(ws):
    """The Editor frame's state in a line: the open tab, the text's digest,
    what undo and redo would do, whether a playtest runs, the top surface."""
    import hashlib
    ea, ed = ws.editor_app, ws.editor
    text = hashlib.sha1(ed.text().encode()).hexdigest()[:12] if ed is not None else "-"
    return "tab=%s text=%s undo=%s redo=%s playing=%s top=%s" % (
        ea.tab, text, ws.history.can_undo(), ws.history.can_redo(), ea.playing(), ws.wm.top_kind())


def _desk_probe(ws):
    """The Editor frame's state in a line, and the desk's workspace."""
    return "%s ws=%s" % (_frame_probe(ws), ws.editor_app._desk().key)


def _panes_probe(ws):
    """The desk Editor's state in a line: the frame's, the workspace, the
    focused pane and the tab it shows, the grown pane, the caret, and each
    pane on show with its size."""
    d = ws.editor_desk
    ed = ws.editor
    fi = d._focus.get(d.key, 0)
    shown = " ".join("%d:%s=%dx%d" % (n, d.tab_of(p), r[2], r[3]) for n, p, r in d._shown_panes())
    return "%s focus=%d grown=%s caret=%s panes=[%s]" % (
        _desk_probe(ws), fi, d.grown and "+".join(d.grown),
        "%d:%d" % (ed.row, ed.col) if ed is not None else "-", shown)


def _open_editor(tab):
    def open_(ws):
        import test_shell_goldens as G
        ws.open_in_editor(G._cart_by_title(ws, G._EDITOR_CART))
        ws.text.set_menu_view(tab)
    return open_


# The Code tab: the caret by the arrows, a typed word and Enter; Ctrl+F's
# find strip searching as it is typed, Enter for the next match, Back
# closing it; Ctrl+G's Go to filtered and taken; Ctrl+E's completion and
# Back; Ctrl+Z taking the typing back
CODE_SCRIPT = ["k:down", "k:down", "k:right", "k:right", "t:x", "enter", "t:\x06", "t:spr", "enter",
               "back", "t:\x07", "t:dr", "enter", "k:down", "t:sp", "t:\x05", "back", "t:\x1a",
               "t:\x1a", "k:up"]
# The desk's Esc is the window's: Ctrl+F closes the strip it opened, and
# Enter takes a completion
CODE_DESK_SCRIPT = ["k:down", "k:down", "k:right", "k:right", "t:x", "enter", "t:\x06", "t:spr",
                    "enter", "t:\x06", "t:\x07", "t:dr", "enter", "k:down", "t:sp", "t:\x05", "enter",
                    "t:\x1a", "t:\x1a", "k:up"]

# The Editor's frame on a handheld, through its bar zone's targets (left to
# right: Cards, Blocks, Code, Paint, Map, Scene, Music, undo, redo, Play):
# each tab opened by a tap, a typed edit taken back and given again by the
# zone's undo and redo, then Play
FRAME_SCRIPT = ["zone:3", "zone:4", "zone:5", "zone:6", "zone:0", "zone:1", "zone:2", "t:x",
                "zone:7", "zone:8", "zone:7", "zone:9"]

# The desk Editor's row: each workspace, a typed letter, undo, redo, undo
# again, Play, then Restart and Stop beside the playtest
FRAME_DESK_SCRIPT = ["cell:ws:art", "cell:ws:world", "cell:ws:sound", "cell:ws:settings",
                     "cell:ws:code", "k:down", "t:x", "cell:undo", "cell:redo", "cell:undo", "cell:play",
                     "f:4", "cell:restart", "f:4", "cell:stop", "f:4"]

# The desk Editor's panes: the Code pane takes a typed letter; F6 and
# Shift+F6 move the keys to the next pane and back; the Code pane's divider
# dragged 100 px left by touch, a tap in the Code pane focusing it and
# moving its caret, Ctrl+Alt+Right moving the divider 8 px back and Ctrl+0
# resetting it; Ctrl+3 to Art, a tap in its second pane focusing it,
# Ctrl+Shift+Enter growing it and Esc giving the workspace back; Ctrl+Tab to
# World, a tap on its first pane, the row back to Code, whose pane keeps the
# keys and takes a second letter
FRAME_PANES_SCRIPT = ["t:x", "key:F6", "key:F6", "key:S-F6", "div:0:-100", "pane:0",
                      "key:C-A-Right",
                      "key:C-0", "key:C-3", "pane:1", "key:C-S-Enter", "back", "key:C-Tab",
                      "pane:0", "cell:ws:code", "t:y"]

# The System log's ring: (t_us, level, process, text) on a pinned clock, more
# lines than a handheld's page holds, a traceback's lines under its record.
LOG_RING = tuple((1000000 + 250000 * n, (6, 4, 3, 7)[n % 4], ("shell", "kernel", "game")[n % 3],
                  "record %d of the ring" % n) for n in range(24)) + (
    (9103300, 2, "game", "Traceback (most recent call last):\n  File \"<cart>\", line 88, in _update\n"
     "TypeError: unsupported types for +: 'int', 'NoneType'"),)


def _open_log(ws):
    import moy_log
    moy_log.init(16384)
    pids = {"kernel": moy_log.KERNEL, "shell": moy_log.SHELL}
    for t, level, who, text in LOG_RING:
        if who not in pids:
            pids[who] = moy_log.spawn(who)
        moy_log.pin_clock(t)
        moy_log.write(level, pids[who], text)
    moy_log.pin_clock(0)
    ws.open_settings()
    ws.settings_layer.open_page("log")


# name -> what a run opens
SURFACES = {
    "editor_code": P.Surface(_open_editor("code"), probe=_code_probe),
    "editor_frame": P.Surface(_open_editor("code"), probe=_frame_probe),
    "editor_desk_row": P.Surface(_open_editor("code"), probe=_desk_probe),
    "editor_desk_panes": P.Surface(_open_editor("code"), probe=_panes_probe),
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
    "settings_log": P.Surface(_open_log),
    "settings_wifi": P.Surface(_open_wifi()),
    "settings_wifi_none": P.Surface(_open_wifi(False)),
    "wifi_popover": P.Surface(_open_wifi_popover),
}

# A step a port draws differently from its record on purpose: its cause,
# said once and named by each run that has it.
NO_FALSE_HINT = ("the old page named Remove in its hints with nothing to remove; the port's hints "
                 "name only what the focused node does")

STALE_TOGGLE_HINT = ("the old rows page drew its hints before its rows, so the frame a toggle "
                     "turned still offered the turn it had just made (Turn off on a toggle now off); "
                     "the port's hints are the toggle's as it now is")

LICENCES_K5 = ("Licences is a uitree page that takes the engine's layout (owner ruling K5): its "
               "frames are the port's, reviewed as side-by-sides and re-baked in its goldens")

LOG_BACK_BAND = ("the old System log filled its page by hand (`ui.at`) and drew no hints; the port "
                 "takes the engine's layout (K5), whose band names Back on every page, a line "
                 "shorter on a handheld")

WIFI_OWN_HINT = ("the old Wi-Fi panel's band said Connect (Join in the popover) whatever was focused; "
                 "the port's names what the focused row does: Scan again, Forget")

WIFI_DIALOG_BAND = ("under the password dialog the old popover's band still named the list's keys "
                    "(Join, Close); the port's names the dialog's (Connect, Cancel), as Settings' "
                    "page always did")

WIFI_RING_IN_VIEW = ("the old popover drew its menu with no page, so focus on its last row, Scan "
                     "again, left the ring's reach under the card's foot; the port's page keeps "
                     "it in view, the rows 2 px higher. " + WIFI_OWN_HINT)

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
     {24: STALE_TOGGLE_HINT, 25: STALE_TOGGLE_HINT, 32: LICENCES_K5, 33: LICENCES_K5}),
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
     ["k:down"] * 22 + ["k:a", "k:down", "k:right"], dict.fromkeys((23, 24, 25), LICENCES_K5)),
    # the ring at its foot, scrolled up and down again, and closed
    ("settings_log-tdeck", "settings_log", "tdeck_320x240_fs1_dark",
     ["f:2", "k:up", "k:up", "k:up", "k:down", "back"], dict.fromkeys(range(6), LOG_BACK_BAND)),
    ("settings_log-desk", "settings_log", "p4_1024x600_fs2_windowed", ["f:2", "k:up", "k:down"],
     dict.fromkeys(range(4), LOG_BACK_BAND)),
    # the networks: an open one joined, a locked one's password typed wrong
    # then right, the prompt cancelled by back, Scan again, the network
    # picked forgotten, and back to the rows
    ("settings_wifi-tdeck", "settings_wifi", "tdeck_320x240_fs1_dark",
     ["k:down", "k:a", "f:2", "k:down", "k:a", "t:abd", "enter", "f:2", "k:a", "back", "k:a",
      "t:abc", "enter", "f:2", "k:down", "k:a", "f:2", "k:down", "k:a", "f:2", "back"],
     dict.fromkeys(range(15, 21), WIFI_OWN_HINT)),
    ("settings_wifi-desk", "settings_wifi", "p4_1024x600_fs2_windowed",
     ["k:down", "k:a", "f:2", "k:down", "k:a", "t:abc", "enter", "f:2", "k:down", "k:down",
      "k:a", "f:2", "back"], dict.fromkeys(range(9, 13), WIFI_OWN_HINT)),
    ("settings_wifi_none-big", "settings_wifi_none", "big_800x480_fs3_dark", ["k:down", "k:a", "f:2"],
     dict.fromkeys(range(4), WIFI_OWN_HINT)),
    # the bar's Wi-Fi popover: an open network joined, a locked one's
    # password typed, then closed
    ("wifi_popover-desk", "wifi_popover", "p4_1024x600_fs2_windowed",
     ["k:down", "k:a", "f:2", "k:down", "k:a", "t:abc", "enter", "f:2", "k:down", "k:a", "f:2", "back"],
     {5: WIFI_DIALOG_BAND, 6: WIFI_DIALOG_BAND, 9: WIFI_RING_IN_VIEW, 10: WIFI_RING_IN_VIEW,
      11: WIFI_RING_IN_VIEW}),
    ("wifi_popover-tdeck", "wifi_popover", "tdeck_320x240_fs1_dark",
     ["k:down", "k:a", "f:2", "k:down", "k:a", "t:abc", "enter", "f:2", "back"]),
    ("editor_code-tdeck", "editor_code", "tdeck_320x240_fs1_dark", CODE_SCRIPT, P.BEHAVIOUR),
    ("editor_code-desk", "editor_code", "p4_1024x600_fs2_windowed", CODE_DESK_SCRIPT, P.BEHAVIOUR),
    ("editor_code-big", "editor_code", "big_800x480_fs3_dark", CODE_SCRIPT, P.BEHAVIOUR),
    ("editor_frame-tdeck", "editor_frame", "tdeck_320x240_fs1_dark", FRAME_SCRIPT, P.BEHAVIOUR),
    ("editor_frame-big", "editor_frame", "big_800x480_fs3_dark", FRAME_SCRIPT, P.BEHAVIOUR),
    ("editor_desk_row", "editor_desk_row", "p4_1024x600_cs2_fs1_windowed", FRAME_DESK_SCRIPT,
     P.BEHAVIOUR),
    ("editor_desk_panes", "editor_desk_panes", "p4_1024x600_cs2_fs1_windowed", FRAME_PANES_SCRIPT,
     P.BEHAVIOUR),
)


@pytest.mark.parametrize("run", RUNS, ids=[r[0] for r in RUNS])
def test_a_ported_surface_draws_its_old_frames(run, tmp_path):
    name, surface, config, script = run[:4]
    body = P.load(name)
    assert (body["surface"], body["config"], body["script"]) == (surface, config, script), (
        "the run moved from its record: re-record it with its cause")
    P.check_recorded(name, SURFACES[surface], tmp_path / "carts", run[4] if len(run) > 4 else None)


# The runs whose surfaces run on the C engine (native/moy_uitree) as they
# run on the reference: each is held to the same record on both. A page the
# C engine does not draw yet raises its EngineGap, which a window's draw would
# swallow: any one made fails the run.
C_RUNS = ("settings_rows-tdeck", "settings_rows-desk", "settings_rows_layout-tdeck",
          "settings_rows_full-tdeck",
          "settings_wifi-tdeck", "settings_wifi-desk", "settings_wifi_none-big",
          "settings_tz-tdeck", "settings_tz-desk",
          "settings_paired-tdeck", "settings_paired-desk", "settings_paired_none-big",
          "settings_permissions-tdeck", "settings_permissions-desk")


@pytest.mark.parametrize("name", C_RUNS)
def test_a_ported_surface_draws_its_old_frames_on_the_c_engine(name, tmp_path, monkeypatch):
    import uitree_app
    import uitree_c
    run = [r for r in RUNS if r[0] == name][0]
    gaps = []
    init = uitree_c.EngineGap.__init__

    def gap(self, what):
        gaps.append(what)
        init(self, what)
    monkeypatch.setattr(uitree_c.EngineGap, "__init__", gap)
    lowered = []
    lower = uitree_c.CEngine.lower

    def counted(self, root):
        lowered.append(root.kind)
        return lower(self, root)
    monkeypatch.setattr(uitree_c.CEngine, "lower", counted)
    uitree_app.use_engine("c")
    try:
        P.check_recorded(name, SURFACES[run[1]], tmp_path / "carts", run[4] if len(run) > 4 else None)
    finally:
        uitree_app.use_engine("reference")
    assert not gaps, gaps
    assert lowered, "no page of the run was built on the C engine"
