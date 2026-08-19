"""Stored pixel goldens for the shell SUB-surfaces `test_shell_goldens.py` skips.

Why this file exists
--------------------
`test_shell_goldens.py` renders whole SCREENS through `ws.frame()`, and its
`_quiesce` deliberately closes every overlay and modal first. That leaves the
four modules of this file group almost uncovered: it hashes the Settings screen
in exactly ONE state (row 0 selected, no OTA/webhost/bluetooth rows, wifi and
bluetooth panels closed), and it hashes the system menu, the About modal, the
achievements view, the Easter-egg banner and the whole firmware-update screen
**not at all** -- its own docstring names the update screen as out of scope.

Those surfaces are precisely where Phase 3b transcribed hand-rolled row/dialog
drawing onto `runtime/ui.py`'s kinds, so without this file the transcription had
no oracle at all on four of the five modules it touched. This is the same
mechanism as the shell goldens -- a hash of bytes COMMITTED to the repo, not a
live-vs-live A/B -- applied one level down: each sub-surface is drawn straight
onto a cleared system canvas and hashed on its own, so a red line names the
surface, the configuration, and therefore the widget.

The configuration matrix is IMPORTED from `test_shell_goldens` rather than
copied, so the two files cannot drift onto different tiers. The axis that
matters most here is the same one that file discovered: at 320x240 / font
scale 1 the shell takes frozen branches, so a widget change is only visible on
the other rows.

Deliberately NOT the whole Settings screen: `SettingsLayer.draw` composites the
live wallpaper, which is a cart render and is not stable across processes here.
The full screen is `test_shell_goldens.py`'s `settings` surface; this file takes
its ROWS.

Re-baselining is a deliberate act, same switch as the shell goldens:

    MOYBYTE_UPDATE_GOLDENS=1 .venv/bin/python -m pytest \
        tests/test_settings_layer_pixels.py -p no:xdist
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

# The matrix lives in ONE place. pytest puts tests/ on sys.path, so this is a
# plain module import; an ImportError here is a loud lockstep failure rather
# than the silent divergence a copied dict would give.
from test_shell_goldens import CONFIGS, REBASELINE_CMD, UPDATE_ENV, _axes

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_FILE = Path(__file__).resolve().parent / "shell_goldens" / "subsurfaces.json"

_DT = 1.0 / 30.0


# ---------------------------------------------------------------------------
# injected services -- every capability-gated row/panel has to be REACHABLE, or
# the matrix silently covers the host's degraded subset (no OTA rows at all).
# ---------------------------------------------------------------------------

class _Wifi(object):
    def __init__(self, connected=True):
        self._c = connected

    def status(self):
        if self._c:
            return (True, "MoyNet-5G-Long-Name", "192.168.1.155")
        return (False, None, None)

    def known(self):
        return ["MoyNet-5G-Long-Name", "Guest"]

    def scan(self):
        return [("MoyNet-5G-Long-Name", 88, True), ("Guest", 40, False),
                ("cafe wifi", 12, True)]


class _Webhost(object):
    serving = True
    error = None

    def url(self):
        return "http://192.168.1.155:8080"


class _Keyboard(object):
    settings_capable = True

    def set_game_mode(self, on):
        pass

    def settings_status(self):
        return (True, "ready", "Keychron K3", "AA:BB:CC:DD:EE:FF", None)

    def settings_devices(self):
        return [("AA:BB:CC:DD:EE:FF", "Keychron K3", -42, True, True),
                ("11:22:33:44:55:66", "Logi MX Keys", -77, False, False)]


class _Updater(object):
    error = ""
    dl_done = 512 * 1024
    dl_total = 3000 * 1024
    done = 900 * 1024
    total = 3000 * 1024
    boot_verdict = None

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


_UPDATE_PHASES = ("checking", "nopublish", "uptodate", "confirm_online",
                  "downloading", "confirm", "install", "done", "updated",
                  "rolledback", "error")


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def _shot(ws, fn):
    """Draw ONE sub-surface onto a cleared canvas and hash it."""
    cv = ws.sys_canvas
    cv.cls(0)
    fn()
    return hashlib.sha256(bytes(cv._buf)).hexdigest()


def _build(cfg, carts_dir):
    from runtime import host_app
    ws = host_app.build_workstation(
        str(carts_dir), sys_size=cfg["sys_size"], font_scale=cfg["font_scale"],
        windowed=cfg["windowed"])
    ws.set_theme_variant(cfg["variant"], persist=False)
    ws.wifi = _Wifi(True)
    ws.updater = _Updater()
    ws.webhost = _Webhost()
    ws.keyboard = _Keyboard()
    ws._updater_ok = None          # the capability answers are cached per boot
    ws._online_ok = None
    return ws


def capture(cfg, carts_dir):
    """{sub_surface: sha256} for one configuration."""
    ws = _build(cfg, carts_dir)
    ws.open_settings()
    sl = ws.settings_layer
    out = {}

    # Every Settings row KIND, selected and unselected: the selection fill and
    # the label ink are what `ui.row` took over, and each kind then draws its
    # own trailing content (value column / icon / OPEN glyph / stepper).
    rows = sl._settings_rows()
    for idx in range(len(rows)):
        key = rows[idx][0]
        sl.set_top = 0
        sl.set_msel = idx
        out["row_sel_" + key] = _shot(ws, lambda i=idx: sl._draw_settings_row(i))
        sl.set_msel = -1
        out["row_off_" + key] = _shot(ws, lambda i=idx: sl._draw_settings_row(i))
    sl.set_msel = 0

    keys = [r[0] for r in rows]
    ws.wifi = _Wifi(False)
    wi = keys.index("wifi")
    out["row_wifi_off"] = _shot(ws, lambda: sl._draw_settings_row(wi))
    ws.wifi = _Wifi(True)

    # The WIFI panel: the scan list (a selection row per network), the password
    # prompt, and the offline status line.
    sl.wifi_view = True
    sl.wifi_nets = [("MoyNet-5G-Long-Name", 88, True), ("Guest", 40, False),
                    ("cafe wifi", 12, True)]
    sl.wifi_known = ["MoyNet-5G-Long-Name"]
    sl.wifi_msg = "scanning..."
    for sel in range(3):
        sl.wifi_sel = sel
        out["wifi_list_%d" % sel] = _shot(ws, lambda: sl._draw_wifi())
    sl.wifi_pick = "MoyNet-5G-Long-Name"
    sl.wifi_pw = "hunter2hunter2"
    out["wifi_password"] = _shot(ws, lambda: sl._draw_wifi())
    sl.wifi_pick = None
    ws.wifi = _Wifi(False)
    out["wifi_offline"] = _shot(ws, lambda: sl._draw_wifi())
    ws.wifi = _Wifi(True)
    sl.wifi_view = False

    # The BLUETOOTH panel (P4-capability gated; the injected keyboard above is
    # what makes it reachable on the host at all).
    sl.open_bluetooth()
    for sel in range(2):
        sl.bt_sel = sel
        out["bt_%d" % sel] = _shot(ws, lambda: sl._draw_bluetooth())
    sl.bt_view = False

    # The = dropdown, one shot per highlighted row (headers + separators move
    # with it), and the ABOUT modal.
    ws.go_home()
    ws.toggle_sysmenu()
    m = ws.sysmenu
    for sel in range(len(m.items)):
        m.sel = sel
        out["sysmenu_%d" % sel] = _shot(ws, lambda: ws.menu_ui._draw_sysmenu())
    ws.sysmenu.open = False
    out["about"] = _shot(ws, lambda: ws.menu_ui._draw_about())

    # The achievements view (locked AND unlocked rows) + the egg banner.
    out["ach_none"] = _shot(ws, lambda: ws.ach_ui._draw_achievements())
    ws.ach.award("konami")
    ws.ach.award("secret_door")
    out["ach_some"] = _shot(ws, lambda: ws.ach_ui._draw_achievements())
    ws.ach_ui.egg_msg = ("KNOCK KNOCK... OH! YOU FOUND ME!", "key")
    out["egg"] = _shot(ws, lambda: ws.ach_ui._draw_egg())

    # The firmware-update screen: every phase it can be in.
    uu = ws.update_ui
    uu._upd_bin = ("/sd/update/moybyte.bin", 3145728)
    uu._online_manifest = {"version": 9, "size": 3145728, "channel": "unstable",
                           "label": "beta 20260819"}
    uu._upd_msg = "sha256 mismatch on chunk 42"
    for phase in _UPDATE_PHASES:
        uu._upd_phase = phase
        out["update_" + phase] = _shot(ws, lambda: uu._draw_update(_DT))
    return out


# ---------------------------------------------------------------------------
# the goldens file + the explicit re-baseline
# ---------------------------------------------------------------------------

def _updating(request):
    if os.environ.get(UPDATE_ENV):
        return True
    return bool(request.config.getoption("--update-goldens", default=False))


_CAPTURED = {}


@pytest.fixture(scope="module", autouse=True)
def _write_goldens_on_teardown(request):
    yield
    if not _CAPTURED:
        return
    GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    stored = {}
    if GOLDEN_FILE.exists():
        stored = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    stored.update(_CAPTURED)
    if set(_CAPTURED) == set(CONFIGS):
        stored = {k: v for k, v in stored.items() if k in CONFIGS}
    GOLDEN_FILE.write_text(
        json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize("config_name", sorted(CONFIGS))
def test_sub_surfaces_match_goldens(config_name, tmp_path, request):
    got = capture(CONFIGS[config_name], tmp_path / "carts")

    if _updating(request):
        assert not hasattr(request.config, "workerinput"), (
            "re-baselining under xdist would have every worker rewrite the "
            "goldens file. Run it serially:\n    " + REBASELINE_CMD)
        _CAPTURED[config_name] = got
        pytest.skip("re-baselining %s (%d sub-surfaces)" % (config_name, len(got)))

    assert GOLDEN_FILE.exists(), (
        "%s is missing. Baseline it deliberately:\n    %s"
        % (GOLDEN_FILE, REBASELINE_CMD))
    golden = json.loads(GOLDEN_FILE.read_text(encoding="utf-8")).get(config_name)
    assert golden is not None, (
        "no sub-surface goldens stored for config %r (axes: %s)"
        % (config_name, _axes(CONFIGS[config_name])))

    moved = []
    for name in sorted(set(got) | set(golden)):
        if golden.get(name) != got.get(name):
            moved.append((name, golden.get(name), got.get(name)))
    if moved:
        lines = ["SHELL SUB-SURFACE PIXELS MOVED -- config %r" % config_name,
                 "  axes: %s" % _axes(CONFIGS[config_name]),
                 "  %d sub-surface(s) changed:" % len(moved)]
        for name, want, have in moved:
            lines.append("    %-22s golden=%s  rendered=%s"
                         % (name, (want[:16] + "..") if want else "<absent>",
                            (have[:16] + "..") if have else "<not rendered>"))
        lines.append("")
        lines.append("  Each line is one Settings/menu/achievements/update "
                     "sub-surface drawn onto a cleared system canvas.")
        lines.append("  If the change is INTENDED and you can say which pixel "
                     "moved and why, re-baseline deliberately:")
        lines.append("    " + REBASELINE_CMD)
        raise AssertionError("\n".join(lines))


def test_goldens_file_covers_exactly_the_matrix(request):
    if _updating(request):
        pytest.skip("re-baselining: the file is rewritten at module teardown")
    stored = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    assert sorted(stored) == sorted(CONFIGS)
    for config_name in CONFIGS:
        for name, digest in stored[config_name].items():
            assert isinstance(digest, str) and len(digest) == 64, (
                "%s/%s: %r is not a sha256 hex digest"
                % (config_name, name, digest))


def test_every_capability_row_is_reachable(tmp_path):
    """The injected services must actually OPEN the gated rows.

    Without them the host builds a Settings list with no OTA, webhost or
    bluetooth rows at all, and this whole file would pin a subset while
    reporting full coverage."""
    ws = _build(CONFIGS["tdeck_320x240_fs1_dark"], tmp_path / "carts")
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    for key in ("wifi", "bluetooth", "webhost", "update", "ota_channel",
                "update_online"):
        assert key in keys, "%s row is not reachable in this fixture" % key


def test_the_hand_rolled_row_idiom_is_gone(tmp_path):
    """Phase 3 ratchet, scoped to this file group: no module here may still
    paint a list row as a bare selection `rect` followed by a `print`.

    Deliberately a SOURCE check and deliberately narrow -- it is the one thing a
    pixel hash cannot see, because a hand-rolled copy that happens to be
    byte-identical to `ui.row` is exactly what this phase exists to delete."""
    import re
    group = ("settings_layer.py", "system_menu_ui.py", "achievements_ui.py",
             "update_ui.py")
    pat = re.compile(
        r"cv\.rect\([^\n]*\bth\[[\"'](?:hilite|selection)[\"']\][^\n]*\)\s*\n"
        r"\s*(?:fg\s*=[^\n]*\n\s*)?cv\.print\(")
    for name in group:
        src = (ROOT / "runtime" / name).read_text(encoding="utf-8")
        assert not pat.search(src), (
            "runtime/%s still hand-rolls a selection row (a `hilite` rect "
            "followed by a print). Use ui.row." % name)
