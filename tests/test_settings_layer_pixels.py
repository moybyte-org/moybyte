"""Stored pixel goldens for the shell SUB-surfaces `test_shell_goldens.py` skips.

Why this file exists
--------------------
`test_shell_goldens.py` renders whole SCREENS through `ws.frame()`, and its
`_quiesce` deliberately closes every overlay and modal first. That leaves the
four modules of this file group almost uncovered: it hashes the Settings screen
in exactly ONE state (the first row focused, no OTA/webhost/bluetooth rows,
wifi and bluetooth panels closed), and it hashes the system menu's rows, the
Achievements page with a badge opened, the unlock banner and the notice, and
the whole firmware-update screen **not at all** -- its own docstring names the
update screen as out of scope.

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

Settings is a page of the public UI API's verbs (docs/look_2026-10.md step
19), so its shots are the PAGE (`SettingsLayer._draw_page`, the bar left out)
with focus on each row in turn -- the page scrolled to it -- and each panel's
page; the full screen with its bar is `test_shell_goldens.py`'s `settings`
surface.

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
from test_shell_goldens import CONFIGS as _ALL, REBASELINE_CMD, UPDATE_ENV, _axes, pick_layout

# The sub-surfaces are rendered on every config that renders the whole
# surface list; a `plan` config holds a few named surfaces alone, and a
# `render` config is its row's Settings doubled.
CONFIGS = {k: v for k, v in _ALL.items() if "plan" not in v and "render" not in v}

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
        return "http://192.168.1.155"


class _Keyboard(object):
    settings_capable = True

    def set_game_mode(self, on):
        pass

    def settings_status(self):
        return (True, "ready", "Keychron K3", "AA:BB:CC:DD:EE:FF", None)

    def settings_devices(self):
        return [("AA:BB:CC:DD:EE:FF", "Keychron K3", -42, True, True),
                ("11:22:33:44:55:66", "Logi MX Keys", -77, False, False)]


class _Keep(object):
    """The store's keeper: a fixed free space, so About's Storage row is
    the same on every host."""

    def free(self):
        return (1258291, 4096)


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


def blank_digest(ws):
    """The hash of the CLEARED canvas -- a shot that drew nothing at all.

    76 of the 300 stored digests were this, in every config, for three months
    of nobody noticing: `capture` pinned the rows' scroll at the top and then
    drew row `idx`, and a row below the scroll window got an off-panel rect
    that `ui.row` clipped away entirely. The hash was stable, committed and green, and it
    pinned the colour of nothing. So the blank canvas gets a name here and
    `_no_blanks` refuses it, on the capture path AND against the stored file --
    a golden that can be produced by deleting the draw call is not a golden."""
    cv = ws.sys_canvas
    cv.cls(0)
    return hashlib.sha256(bytes(cv._buf)).hexdigest()


def _no_blanks(out, blank, cfg_name=""):
    """Raise if any shot in `out` is the empty canvas."""
    dead = sorted(k for k, v in out.items() if v == blank)
    if dead:
        raise AssertionError(
            "%s%d sub-surface shot(s) rendered NOTHING (the hash is sha256 of "
            "the cleared canvas): %s\n"
            "  A blank shot pins no pixel. Scroll the surface into view in "
            "`capture` instead of storing the void."
            % (("config %r: " % cfg_name) if cfg_name else "", len(dead), ", ".join(dead)))


def _build(cfg, carts_dir):
    from runtime import host_app
    ws = host_app.build_workstation(
        str(carts_dir), sys_size=cfg["sys_size"], font_scale=cfg["font_scale"],
        windowed=cfg["windowed"], panel_diagonal_in=cfg["diagonal_in"])
    pick_layout(ws, cfg)
    if "layout" in cfg:
        ws.look.set_theme(ws.look.theme_name, persist=False)
    ws.look.set_theme_variant(cfg["variant"], persist=False)
    ws.wifi = _Wifi(True)
    ws.updater = _Updater()
    ws.webhost = _Webhost()
    ws.keyboard = _Keyboard()
    ws.cart_keep = _Keep()
    ws._updater_ok = None          # the capability answers are cached per boot
    ws._online_ok = None
    return ws


def capture(cfg, carts_dir):
    """{sub_surface: sha256} for one configuration."""
    ws = _build(cfg, carts_dir)
    if cfg.get("console"):
        ws.open_library()          # the desk's PLAY: console mode
    blank = blank_digest(ws)
    ws.open_settings()
    sl = ws.settings_layer
    out = {}

    # The page with focus on every row in turn: each kind's verb with the
    # focus mark, and the page scrolled to it, so a row below the fold is
    # drawn, never an empty canvas (`_no_blanks`).
    def page(host=None, focus=None):
        # Focus lands on the frame after it is asked for and the page scrolls
        # to it on the next: two draws settle it, the third is the shot.
        if focus is not None:
            (host or sl._host).focus(focus)
        sl._draw_page()
        if focus is not None:
            (host or sl._host).focus(focus)
        sl._draw_page()
        return _shot(ws, sl._draw_page)

    from runtime.settings_layer import say
    sl._draw_page()
    for _caption, rows in sl._grouped():
        for key, label, _kind in rows:
            out["row_" + key] = page(focus=say(label))
    out["row_achievements"] = page(focus="Achievements")
    ws.wifi = _Wifi(False)
    out["row_wifi_off"] = page(focus="Wi-Fi")
    ws.wifi = _Wifi(True)

    sl.open_wifi()
    sl.wifi_nets = [("MoyNet-5G-Long-Name", 88, True), ("Guest", 40, False),
                    ("cafe wifi", 12, True)]
    sl.wifi_known = ["MoyNet-5G-Long-Name"]
    sl.wifi_msg = "scanning..."
    for sel in range(3):
        out["wifi_list_%d" % sel] = page(sl._sub, "net%d" % sel)
    sl.wifi_pick = "MoyNet-5G-Long-Name"
    sl.wifi_pw.text = "hunter2hunter2"
    out["wifi_password"] = page()
    sl.wifi_pick = None
    ws.wifi = _Wifi(False)
    out["wifi_offline"] = page()
    ws.wifi = _Wifi(True)
    sl.close_wifi()

    sl.open_bluetooth()
    for sel in range(2):
        out["bt_%d" % sel] = page(sl._sub, "bt%d" % sel)
    sl.close_bluetooth()

    from runtime.settings_layer import KEEP_OFF, TOGGLE_CONFIRMS
    for key in sorted(TOGGLE_CONFIRMS):
        sl.open_confirm(key)
        for sel, label in enumerate((KEEP_OFF, TOGGLE_CONFIRMS[key][2])):
            out["confirm_%s_%d" % (key, sel)] = page(sl._sub, say(label))
        sl.close_confirm()
    sl._draw_page()

    ws.go_home()
    if cfg["windowed"]:
        ws.open_library()
    ws.toggle_sysmenu()
    m = ws.sysmenu
    for sel in range(len(m.items)):
        m.sel = sel
        out["sysmenu_%d" % sel] = _shot(ws, lambda: ws.menu_ui._draw_sysmenu())
    ws.sysmenu.open = False
    ws.menu_ui._menu_about()
    for _ in range(2):              # its first frame lays its body out
        ws.menu_ui._draw_about()
    out["about"] = _shot(ws, lambda: ws.menu_ui._draw_about())
    ws._about = False

    # The Achievements page (locked AND found cards) with a badge opened,
    # the unlock banner (a secret's, its confetti out whole) and the notice.
    from runtime import achievements_ui
    ws.ach.unlocked = {"first_open": 20261012, "secret_door": 0}
    ws.open_settings()
    sl.open_page("achievements")
    out["ach_page"] = page(sl._sub)
    pg = sl.achievements
    pg._open(pg._by_id["Badge first_open"])
    out["ach_popover"] = page(sl._sub)
    sl.close_page()
    sl.open_page("licences")
    out["licences"] = page(sl._sub)
    sl.close_page()
    ws.go_home()
    ws.ach.award("konami")
    ws._toast_at -= 10000
    out["banner"] = _shot(ws, lambda: achievements_ui.unlock_banner(ws))
    ws._notice = ("Moybyte updated", "Now 0.8", "ok")
    out["notice"] = _shot(ws, lambda: ws._draw_notice())
    ws._notice = None

    # The firmware-update screen: every phase it can be in.
    uu = ws.update_ui
    uu._upd_bin = ("/sd/update/moybyte.bin", 3145728)
    uu._online_manifest = {"version": 9, "size": 3145728, "channel": "unstable",
                           "label": "beta 20260819"}
    uu._upd_msg = "sha256 mismatch on chunk 42"
    for phase in _UPDATE_PHASES:
        uu._upd_phase = phase
        out["update_" + phase] = _shot(ws, lambda: uu._draw_update(_DT))

    # The capture path refuses to PRODUCE a blank, so a re-baseline cannot put
    # one back into the file either.
    _no_blanks(out, blank)
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


@pytest.mark.parametrize("config_name", sorted(CONFIGS))
def test_no_stored_golden_is_a_blank_canvas(config_name, tmp_path, request):
    """The ratchet for the defect this file shipped with.

    `capture` refuses to emit a blank shot, which stops a bad re-baseline at
    the source; this checks the FILE, so a digest pasted or merged in by hand
    -- or one recorded before the guard existed -- is caught too. The blank is
    per-config because the canvas size (and therefore the hash of all-zero
    bytes) is."""
    if _updating(request):
        pytest.skip("re-baselining: capture() enforces this on the way in")
    blank = blank_digest(_build(CONFIGS[config_name], tmp_path / "carts"))
    stored = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))[config_name]
    _no_blanks(stored, blank, config_name)


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
    group = ("settings_layer.py", "achievements_ui.py", "about_ui.py",
             "update_ui.py")
    pat = re.compile(
        r"cv\.rect\([^\n]*\bth\[[\"'](?:hilite|selection)[\"']\][^\n]*\)\s*\n"
        r"\s*(?:fg\s*=[^\n]*\n\s*)?cv\.print\(")
    for name in group:
        src = (ROOT / "runtime" / name).read_text(encoding="utf-8")
        assert not pat.search(src), (
            "runtime/%s still hand-rolls a selection row (a `hilite` rect "
            "followed by a print). Use ui.row." % name)


def test_a_settled_settings_page_paints_nothing(tmp_path):
    """The page's settled answer (docs/ui_api_2026-10.md §2), end to end: with
    no input the redraw gate draws no frame, a press that moves nothing draws
    one frame of the same pixels, and no frame asks for another after it."""
    from runtime import host_app
    ws = _build(CONFIGS["tdeck_320x240_fs1_dark"], tmp_path / "carts")
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    for _ in range(4):
        drv.frame(_DT)
    drawn = ws._frames_drawn
    for _ in range(10):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn, "an idle Settings page painted"
    before = bytes(ws.sys_canvas._buf)
    drv.press("up")                     # on the first row: nothing to move to
    for _ in range(6):
        drv.frame(_DT)
    assert ws._frames_drawn == drawn + 1, "one frame for the press, then none"
    assert bytes(ws.sys_canvas._buf) == before


@pytest.mark.parametrize("variant", ["dark", "light"])
def test_under_touch_the_name_row_reads_not_set_over_its_note(tmp_path, variant):
    """Where nothing types (touch alone) the Name row is read-only: with no
    name saved its value reads "Not set", and the note that a keyboard
    changes it stands under the General group's card on the page's ground,
    in the captions' dim ink, with no divider -- not a row of the group
    (design review R5-11)."""
    from runtime import host_app, settings_layer
    cfg = dict(CONFIGS["tdeck_320x240_fs1_dark"], variant=variant)
    ws = _build(cfg, tmp_path / "carts")
    ws.system.set("name", "")
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    ws.use_inputs([("local", "touch")])
    ws.settings_layer.show_row("name")
    infos = []
    ui = settings_layer._ui
    real = ui.info
    ui.info = lambda label, value, o=None: (infos.append((label, value)), real(label, value, o))
    try:
        for _ in range(3):
            ws._dirty = True
            drv.frame(_DT)
    finally:
        ui.info = real
    assert ("Name", settings_layer.NAME_UNSET) in infos, infos
    cv = ws.sys_canvas
    buf = bytes(cv._buf)

    def px(x, y):
        # as the canvas stores it, the form `_col` answers
        return buf[2 * (y * cv.w + x)] | buf[2 * (y * cv.w + x) + 1] << 8
    dim = cv._col(ws.theme_colors["ink_dim"])
    play = ws.settings_layer._host.box("Steady")       # the next group's first row
    assert play is not None
    mid = cv.w // 2
    card = px(mid, play[1] + 2)
    # the General card's last pixel row over the page's middle
    y = play[1] - 1
    while px(mid, y) == card:            # up out of the Play card
        y -= 1
    while px(mid, y) != card:            # over the gap its captions lie in
        y -= 1
    foot = y
    rows = [y for y in range(foot + 1, play[1])
            if any(px(x, y) == dim for x in range(0, mid))]
    assert rows, "no note under the General card"
    note = rows[0]
    line = [px(x, note) for x in range(cv.w)]
    ground = px(cv.w - 4, note)
    assert ground != card, "the note lies on a card, not the page's ground"
    run, longest = 0, 0
    for k in range(1, len(line)):
        run = run + 1 if line[k] == line[k - 1] and line[k] != ground else 0
        longest = max(longest, run)
    assert longest < cv.w // 3, "the note has a divider"
