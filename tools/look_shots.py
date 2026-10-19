#!/usr/bin/env python3
"""The 2026-10 look's review kit: every mockup beside the real shell's render.

usage: .venv/bin/python tools/look_shots.py OUT [FILTER ...] [--step N]
                                            [--theme NAME] [--variant MODE]
                                            [--parts] [--input PROFILE]
                                            [--kit] [--profiles] [--only-kit]
                                            [--audit]

Each scene `docs/media/look_2026-10/index.txt` names is set up on the host
from the REAL shell (the workstation `tests/test_shell_goldens.py` builds, with
its pinning), at the mockup's size, theme and mode, and written into OUT as

  SCENE.mockup.png   the target, copied from docs/media/look_2026-10/
  SCENE.render.png   the shell's frame (and SCENE.render@3x.png at 320x240)
  SCENE.pair.png     mockup | render at the same scale
  SCENE.pair@3x.png  the same, nearest 3x, for the 320x240 scenes

plus OUT/index.txt, which is what a reviewer reads: per scene, the target's
description, what the render stands in with, and the plan steps still to land.

A scene is BUILT when the shell holds its theme and every step of
`docs/look_2026-10.md` that targets it carries a "Landed YYYY-MM-DD" mark.
Until then it is rendered as the nearest current screen (a missing theme
becomes the default, `DEFAULT`) and marked "not built yet" with what is missing.

FILTER keeps the scene it names, else the scenes whose name contains it
(shell globs work too);
`--step N` keeps the scenes step N targets. `--theme`/`--variant` render
every kept scene in that theme instead of its own. `--parts` draws the
handheld Settings and Appearance scenes with the public UI API's parts sheet
(tests/fixtures/ui_parts/parts_app.py, docs/look_2026-10.md step 12) in
place of the shell's screens, which have not moved onto the verbs yet.

The handheld scenes draw the T-Deck's own keys (`HANDHELD_INPUT`), the desk
ones the desk keyboard's; `--input PROFILE` (keys, tdeck, pad, touch) draws
every scene with that input's keys, touch with no hint strip at all.

`--kit` adds the kit's own scenes, states no mockup draws, each in the five
theme-modes of `KIT_MODES`: Settings with Volume (at `VOLUME_REST`),
Brightness, a toggle On and Off and the empty Name field focused; the desk
Appearance window with a cycler, the segments and the accents focused; the
parts sheet with each of its parts focused; the Code tab with Undo enabled
and disabled and with the caret on the longest line; the sprite editor's
palette; the system menu and the pause menu over focused content.
`--profiles` adds the hint-bearing screens once per input profile, the
Name field being typed and the Wi-Fi password prompt among them. They are
written into OUT/kit/ with their own index.txt; `--only-kit` skips the
mockup pairs.

Every render is checked, and a failed check fails the run (exit 1): the
handheld hero's focus mark lies under the bar, every cover a home draws is
the whole 128 px cover at a clean scale, a menu over content leaves one focus
mark on the screen, an open tab's pill has its ink padded alike both sides,
and a theme's bar packs alike in every mode the run drew it in. `--audit`
writes SCENE.audit.csv beside each render: the state pairs round 2 measured
by hand (a focused row's parts against its plate, the caret against its line,
the current plate against the ground beside it, a disabled history icon
against the enabled ink, an Off toggle against its row), each with its
contrast ratio and a FLAG under its floor.
"""

import argparse
import fnmatch
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCKUPS = os.path.join(ROOT, "docs", "media", "look_2026-10")
PLAN = os.path.join(ROOT, "docs", "look_2026-10.md")

# The theme a scene names none of is drawn with, and a target theme the
# shell does not hold (the ricing themes, step 29) is drawn with, in the
# scene's mode.
DEFAULT = "moy"

# Steps whose target is every PNG, and each theme's own steps, by
# `theme-mode` first, then by theme (docs/look_2026-10.md).
EVERY = ("14", "27")
THEME_STEPS = {"moy-dark": ("26a",), "moy": (), "blocks": ("26b",),
               "contrast": ("26c",),
               "luna": ("29",), "coast": ("29",), "pico": ("29",),
               "bench": ("29",), "adw": ("29",)}

# The surfaces of the shell, as `tests/test_shell_goldens.py` configures them.
HANDHELD = "tdeck_320x240_fs1_dark"
DESK = "p4_1024x600_cs2_fs1_windowed"
CONSOLE = "p4_1024x600_cs2_fs1_console"

# The bar's clock, as the targets print it.
CLOCK = "14:32"

# The inputs a hint strip draws the keys of (ui_draw.INPUTS), and the one
# the handheld config draws: the T-Deck's own keys, as its board declares
# them (`Workstation.use_inputs`).
PROFILES = ("keys", "tdeck", "pad", "touch")
HANDHELD_INPUT = "tdeck"

# Volume's level in every Settings scene: mid-range, so its empty steps show.
VOLUME_REST = 3

# Calc's keys for the target's tape, then the expression on its display.
CALC_TAPE = ("48+72=", "120-36=", "84/7=", "12*7=")


def _setup_table():
    """screen -> (config, steps, wallpaper, set up, what the render shows).
    `screen` is a scene's name after its `theme-mode_` prefix."""
    return {
        "handheld_home": (HANDHELD, ("7", "11", "15", "17"), False, _home,
                          "home, Games tab, Sky Run current"),
        "handheld_home_wallpaper": (HANDHELD, ("17", "28"), True, _home,
                                    "home over the current default wallpaper"),
        "desktop": (DESK, ("6", "7", "9", "13", "16", "21", "22", "25"),
                    False, _desk,
                    "desk: Files on Projects behind, Calc current"),
        "desktop_wallpaper": (DESK, ("25", "28"), True, _desk_wall,
                              "the desk over the current default wallpaper"),
        "desktop_home": (CONSOLE, ("18",), False, _console_home,
                         "the console-mode home on the 1024x600 board"),
        "handheld_settings": (HANDHELD, ("11", "12", "19"), False, _settings,
                              "Settings"),
        "handheld_appearance": (HANDHELD, ("20",), False, _appearance,
                                "Appearance"),
        "desktop_appearance": (DESK, ("9", "20"), False, _desk_appearance,
                               "Appearance as a window on the desk"),
        "handheld_code-editor": (HANDHELD, ("3", "4", "16", "23"), False,
                                 _code, "the Code tab on Star Catcher"),
        "handheld_sprite-editor": (HANDHELD, ("16", "23", "24"), False,
                                   _sprites,
                                   "the sprite tab on Star Catcher, sprite 0"),
        "desktop_code-editor": (DESK, ("9", "13", "16", "23"), False, _code,
                                "the Editor on the desk, Code tab"),
        "desktop_sprite-editor": (DESK, ("9", "13", "16", "23", "24"), False,
                                  _sprites,
                                  "the Editor on the desk, sprite tab"),
        "desktop_playtest": (DESK, ("9", "13", "23"), False, _playtest,
                             "the playtest window over the Editor"),
        "handheld_sysmenu": (HANDHELD, ("11", "12", "16", "25a"), False,
                             _sysmenu_home, "the system menu over the home"),
        "desktop_sysmenu": (DESK, ("11", "12", "16", "25a"), False,
                            _sysmenu_desk, "the system menu over the desk"),
        "handheld_pause": (HANDHELD, ("11", "16", "25a"), False, _pause,
                           "the pause menu over Star Catcher"),
        "desktop_pause": (CONSOLE, ("11", "16", "25a"), False, _pause,
                          "the pause menu over Star Catcher, console mode"),
        "editor_bar_all_themes": (HANDHELD, ("16", "23"), False, _code,
                                  "nearest screen: the Code tab's bar"),
        "focus_marks": (HANDHELD, ("11", "13"), False, _code,
                        "nearest screen: the Code tab's bar"),
        "moy_mark": (HANDHELD, ("16", "25a"), False, _home,
                     "nearest screen: home"),
        "icons_sheet": (HANDHELD, ("16",), False, _home,
                        "nearest screen: home"),
        "icons_pixel": (HANDHELD, ("16",), False, _home,
                        "nearest screen: home"),
        "handheld_calc": (HANDHELD, ("10", "11", "22"), False, _calc,
                          "Calc fullscreen after the target's tape"),
        "calc_all_themes": (DESK, ("5", "6", "10", "22"), False, _desk,
                            "nearest screen: Calc on the desk"),
        "type_specimen": (HANDHELD, ("3", "4"), False, _code,
                          "nearest screen: the Code tab on Star Catcher"),
    }


# --------------------------------------------------------------------------
# the index and the plan
# --------------------------------------------------------------------------

def read_index(path=None):
    """[(scene, description, theme, mode, (w, h))] for every target PNG,
    without its enlarged copies. theme/mode are None for a sheet."""
    out = []
    with open(path or os.path.join(MOCKUPS, "index.txt"),
              encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(\S+)\.png — (.*); (\d+)x(\d+)\s*$", line)
            if not m or m.group(1).endswith("@3x"):
                continue
            name, what = m.group(1), m.group(2)
            t = re.match(r"([a-z]+)-(dark|light)_", name)
            out.append((name, what, t and t.group(1), t and t.group(2),
                        (int(m.group(3)), int(m.group(4)))))
    return out


def screen_of(scene):
    m = re.match(r"[a-z]+-(?:dark|light)_(.*)$", scene)
    return m.group(1) if m else scene


def plan_steps(path=None):
    """{step: landed date or None}, from each `**N. ...**` paragraph."""
    with open(path or PLAN, encoding="utf-8") as f:
        text = f.read()
    steps = {}
    for para in re.split(r"\n\s*\n", text):
        m = re.match(r"\*\*(\d+[a-z]?)\. ", para.strip())
        if m:
            got = re.search(r"\bLanded (\d{4}-\d{2}-\d{2})", para)
            steps[m.group(1)] = got.group(1) if got else None
    return steps


def scene_steps(scene, theme):
    """The plan steps that target `scene`, in plan order."""
    screen = screen_of(scene)
    steps = set(_setup_table()[screen][1]) | set(EVERY)
    m = re.match(r"([a-z]+-(?:dark|light))_", scene)
    own = THEME_STEPS.get(m.group(1)) if m else None
    steps |= set(own if own is not None else THEME_STEPS.get(theme, ()))
    return sorted(steps, key=lambda s: (int(re.match(r"\d+", s).group()), s))


def select(scenes, filters, step):
    names = {sc[0] for sc in scenes}

    def hit(f, name):
        if any(c in f for c in "*?["):
            return fnmatch.fnmatch(name, f)
        return f == name if f in names else f in name

    keep = []
    for sc in scenes:
        name = sc[0]
        if filters and not any(hit(f, name) for f in filters):
            continue
        if step is not None and step not in scene_steps(name, sc[2]):
            continue
        keep.append(sc)
    return keep


# --------------------------------------------------------------------------
# the shell
# --------------------------------------------------------------------------

def _goldens():
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import test_shell_goldens as g                    # the goldens' pinning
    return g


def _settle(g, ws, frames=8, probe=None):
    """Draw until two frames are byte-identical (or `frames` run out),
    each frame under `probe` (a Probe) where one is given."""
    prev = None
    for _ in range(frames):
        g._quiesce(ws)
        ws._dirty = True
        if probe is not None:
            probe.begin()
        ws.frame(g._DT)
        if probe is not None:
            probe.end()
        cur = bytes(ws.glass_canvas._buf)
        if cur == prev:
            return True
        prev = cur
    return False


def _app(ws, app_id):
    a = ws._apps_by_id[app_id]
    return getattr(a, "_inst", a)


def _place(ws, key, x, y, w, h):
    win = ws.wm._wins[key]
    win.x, win.y = x, y
    ws.wm._resize_window(win, w, h)


def _pick_home(g, ws):
    ws.set_home_tab("games")
    titles = [it.get("title") for it in ws.launcher.items]
    if "Sky Run" in titles:
        ws.launcher.sel = titles.index("Sky Run")


def _home(g, ws):
    ws.go_home()
    _pick_home(g, ws)


def _console_home(g, ws):
    ws.go_home()
    ws.open_library()
    _pick_home(g, ws)
    # the target's records: played twelve times, the last on the pinned day
    y, m, d = g._DATE
    sel = ws.launcher.selected()
    if sel is not None and sel.get("path"):
        ws.system.set("plays", {sel["path"]: [12, y * 10000 + m * 100 + d]})


def _key_calc(g, ws):
    calc = _app(ws, "calc")
    for expr in CALC_TAPE:
        calc._key("C")
        for k in expr:
            calc._key(k)


def _desk(g, ws, calc_x=648):
    # The target's windows where it puts them, in glass pixels.
    ws.open_desk()
    ws.open_app(ws._apps_by_id["files"])
    _settle(g, ws, 2)
    _place(ws, "files", 112, 54, 548, 534)
    files = _app(ws, "files")
    files.open_place("projects", keep="harpoon_pop")
    _settle(g, ws, 2)
    ws.open_app(ws._apps_by_id["calc"])
    _settle(g, ws, 2)
    _place(ws, "calc", calc_x, 96, 344, 488)
    _key_calc(g, ws)


def _sysmenu_home(g, ws):
    _home(g, ws)
    _settle(g, ws, 2)
    ws._golden_popover = True             # the frames' quiesce keeps it open
    ws.system_key()


def _sysmenu_desk(g, ws):
    _desk(g, ws)
    _settle(g, ws, 2)
    ws._golden_popover = True
    ws.system_key()


# Frames Star Catcher plays before the pause, so its stars and HUD show.
PAUSE_FRAMES = 45


def _pause(g, ws):
    ws.go_home()
    if getattr(ws.wm, "mode", None) is not None:
        ws.open_library()                 # the windowed tier plays in console mode
    ws._open_workspace(g._cart_by_title(ws, "Star Catcher"))
    ws.run(ws.project, ws.launcher_layer)
    for _ in range(PAUSE_FRAMES):
        g._quiesce(ws)
        ws.frame(g._DT)
    ws.system_key()


def _desk_wall(g, ws):
    # The target's Calc starts at 592, over Files' Kind column (its text ends
    # at x 640 here: no Size column); 644 is the nearest place that leaves
    # the column whole, 4 px left of the plain desk scene's 648.
    _desk(g, ws, calc_x=644)


class _HomeWifi:
    """The targets' Wi-Fi, in every scene: on the network called Home."""

    def status(self):
        return (True, "Home", "192.168.1.20")

    def scan(self):
        return [("Home", 80, True)]

    def known(self):
        return ["Home"]


def _settings(g, ws):
    # The target's focus: Appearance.
    ws.open_settings()
    ws.settings_layer.set_level("volume", VOLUME_REST)
    ws.settings_layer.show_row("appearance")


def _appearance(g, ws):
    # The target's focus: Tint.
    ws.open_app(ws._apps_by_id["appearance"])
    _app(ws, "appearance").show_row("Tint")


def _desk_appearance(g, ws):
    ws.open_desk()
    ws.open_app(ws._apps_by_id["appearance"])
    _settle(g, ws, 2)
    _place(ws, "appearance", 226, 65, 680, 510)    # the target's window
    _app(ws, "appearance").show_row("Tint")


def _editor(g, ws, tab):
    ws.open_in_editor(g._cart_by_title(ws, "Star Catcher"))
    ws.set_menu_view(tab)


# The Code tab's state in the targets: the caret on line 42, column 13, with
# line 34 the first shown.
CODE_TOP, CODE_ROW, CODE_COL = 33, 41, 12


def _code(g, ws):
    desk = getattr(ws.wm, "mode", None) == "desktop"
    if desk:
        ws.open_desk()
    _editor(g, ws, "code")
    if desk:
        _settle(g, ws, 2)
        _place(ws, "menu", 112, 54, 900, 534)       # the target's window
    ed = ws.editor
    ed.row, ed.col, ed.top = CODE_ROW, CODE_COL, CODE_TOP


def _playtest(g, ws):
    _code(g, ws)
    _settle(g, ws, 2)
    ws._leave_menu()


# The Sprites tab's state in the targets: the cursor on column 3, row 2, and
# colour 11 in use.
SPRITE_CUR, SPRITE_COLOUR = (3, 2), 11


def _sprites(g, ws):
    desk = getattr(ws.wm, "mode", None) == "desktop"
    if desk:
        ws.open_desk()
    _editor(g, ws, "paint")
    if desk:
        _settle(g, ws, 2)
        _place(ws, "menu", 112, 54, 900, 534)       # the target's window, as _code's
    ws.paint_layer._cur[:] = SPRITE_CUR
    if ws.paint is not None:
        ws.paint.color = SPRITE_COLOUR


def _calc(g, ws):
    ws.open_app(ws._apps_by_id["calc"])
    _settle(g, ws, 2)
    _key_calc(g, ws)


def _image(ws):
    """The glass as an RGB image (its RGB565 is big-endian)."""
    from PIL import Image
    cv = ws.glass_canvas
    buf = bytes(cv._buf)
    sw = bytearray(len(buf))
    sw[0::2], sw[1::2] = buf[1::2], buf[0::2]
    return Image.frombytes("RGB", (cv.w, cv.h), bytes(sw), "raw", "BGR;16")


# The scenes the parts sheet stands in for (--parts): screen -> its page.
PARTS = {"handheld_settings": "settings", "handheld_appearance": "appearance"}


def _parts(page):
    def setup(g, ws):
        app = g.parts_app(ws, page)
        app.open()
        ws.wm.goto(app.id)
    return setup


def render(g, config, theme, mode, setup, wallpaper, inputs=None):
    """(image, settled, report) for one scene on a fresh workstation, its
    hint strips drawing input profile `inputs`' keys (default: the T-Deck's
    on the handheld config, the desk keyboard's elsewhere). `report` holds
    the render's `checks`, its `audit` pairs and its `bars` (clock cell and
    packing)."""
    import moy_ui
    from runtime import ui_draw
    moy_ui.theme_uninstall()
    tmp = tempfile.mkdtemp(prefix="look_shots_")
    try:
        cfg = dict(g.CONFIGS[config], variant=mode)
        if "layout" not in cfg:
            cfg["theme"] = theme
        ws = g._build(cfg, os.path.join(tmp, "carts"))
        if inputs is None and config == HANDHELD:
            inputs = HANDHELD_INPUT
        if inputs is not None:
            # the host's own source is the one the board's input becomes
            ws.use_inputs([("local", inputs)])
        # A wallpaper scene shows the console's default wallpaper cart (Moy's
        # night on a seeded store), the rest the theme's backdrop.
        ws.look.select_wallpaper(ws.look._default_wallpaper() if wallpaper
                                 else None, persist=False)
        # Every target's status: Wi-Fi on Home, the battery at 82%.
        ws.wifi = _HomeWifi()
        ws.battery_level = lambda: 82
        setup(g, ws)
        ws.bar_layer.invalidate()         # the probes see the bars drawn
        probe = Probe()
        probe.install(ws)
        try:
            settled = _settle(g, ws, probe=probe)
        finally:
            probe.remove()
        img = _image(ws)
        report = {"checks": checks(ws, probe, img), "audit": audit(ws, probe, img),
                  "bars": (ws.layout.clock_w, ws.layout.bars)}
        return img, settled, report
    finally:
        moy_ui.theme_uninstall()
        ui_draw.input_kind("keys")
        shutil.rmtree(tmp, ignore_errors=True)


def themes_held(g):
    """The theme names the shell holds: the seeds and the store's packages."""
    import moy_ui
    tmp = tempfile.mkdtemp(prefix="look_shots_")
    try:
        moy_ui.theme_uninstall()
        ws = g._build(dict(g.CONFIGS[HANDHELD]), os.path.join(tmp, "carts"))
        return set(ws.look.theme_names())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# the kit's own scenes: states no mockup draws
# --------------------------------------------------------------------------

# The five theme-modes the kit's own scenes render in (--kit).
KIT_MODES = (("moy", "dark"), ("moy", "light"), ("blocks", "light"),
             ("contrast", "dark"), ("contrast", "light"))

# What a typed field and the Wi-Fi password prompt hold in their scenes.
NAME_TYPED = "Moy"
WIFI_TYPED = "hunter2"


class _CafeWifi(_HomeWifi):
    """Home, and a locked network the console does not know: its password
    prompt."""

    def scan(self):
        return [("Home", 80, True), ("Cafe", 60, True)]


def _driver(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


def _typed(g, ws, text):
    """Type `text` a byte a frame, as a keyboard does."""
    drv = _driver(ws)
    for ch in text:
        drv.type_char(ord(ch))
        g._quiesce(ws)
        drv.frame(g._DT)
        g._quiesce(ws)
        drv.frame(g._DT)


def _settings_at(key):
    """Settings with row `key` focused."""
    def setup(g, ws):
        ws.open_settings()
        sl = ws.settings_layer
        sl.set_level("volume", VOLUME_REST)
        sl.show_row(key)
        ws._kit_box = lambda: sl._host.box(sl._focused) if sl._focused else None
    return setup


def _name_typing(g, ws):
    _settings_at("name")(g, ws)
    _settle(g, ws, 3)
    _typed(g, ws, NAME_TYPED)


def _wifi_password(g, ws):
    ws.wifi = _CafeWifi()
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_wifi()
    sl._wifi_ask("Cafe")
    _settle(g, ws, 2)
    _typed(g, ws, WIFI_TYPED)


def _desk_appearance_at(label):
    """The desk's Appearance window with row `label` focused."""
    def setup(g, ws):
        _desk_appearance(g, ws)
        app = _app(ws, "appearance")
        app.show_row(label)
        win = ws.wm._wins["appearance"]

        def box():
            b = app._ui.box(app._focused) if app._focused else None
            if b is None:
                return None
            cx, cy, _w, _h = win.content_rect()
            return (b[0] + cx, b[1] + cy, b[2], b[3])
        ws._kit_box = box
    return setup


def _parts_at(page, stop):
    """The parts sheet's `page` with part `stop` focused."""
    def setup(g, ws):
        from runtime import ui_draw
        app = g.parts_app(ws, page)
        app.open()
        ws.wm.goto(app.id)
        _settle(g, ws, 2)
        ui_draw._C.api_focus(app.ctx, stop)
        ws._kit_box = lambda: ui_draw._C.api_box(app.ctx, stop)
    return setup


def _code_edited(g, ws):
    """The Code tab after one edit: Undo enabled."""
    _code(g, ws)
    _settle(g, ws, 2)
    _typed(g, ws, " ")


def _code_long(g, ws):
    """The Code tab with the caret on the cart's longest line, so the
    current line's mark meets the overflow fade."""
    _code(g, ws)
    ed = ws.editor
    row = max(range(len(ed.lines)), key=lambda i: len(ed.lines[i]))
    ed.row, ed.col, ed.left = row, 4, 0
    ed.top = max(0, row - 4)


def _kit_table():
    """kit screen -> (config, setup, what it shows, kind): a "theme" scene
    renders in each of KIT_MODES (--kit), a "profile" one in each input
    profile (--profiles), in the run's theme (Moy Night by default)."""
    t = {
        "handheld_settings_focus-volume": (
            HANDHELD, _settings_at("volume"), "Settings, Volume focused at %d/7"
            % VOLUME_REST),
        "handheld_settings_focus-brightness": (
            HANDHELD, _settings_at("brightness"), "Settings, Brightness focused"),
        "handheld_settings_focus-toggle-on": (
            HANDHELD, _settings_at("steady"), "Settings, a toggle focused On (Steady)"),
        "handheld_settings_focus-toggle-off": (
            HANDHELD, _settings_at("show_fps"),
            "Settings, a toggle focused Off (Show FPS)"),
        "handheld_settings_focus-name": (
            HANDHELD, _settings_at("name"), "Settings, the empty Name field focused"),
        "desktop_appearance_focus-cycler": (
            DESK, _desk_appearance_at("Tint"), "Appearance window, the Tint cycler focused"),
        "desktop_appearance_focus-seg": (
            DESK, _desk_appearance_at("Mode"), "Appearance window, the Mode segments focused"),
        "desktop_appearance_focus-swatches": (
            DESK, _desk_appearance_at("Accent"), "Appearance window, the accents focused"),
        "handheld_code-editor_undo-off": (
            HANDHELD, _code, "Code tab, nothing to undo: Undo and Redo disabled"),
        "handheld_code-editor_undo-on": (
            HANDHELD, _code_edited, "Code tab after one edit: Undo enabled, Redo not"),
        "desktop_code-editor_undo-on": (
            DESK, _code_edited, "the desk Editor after one edit: Undo enabled, Redo not"),
        "handheld_code-editor_long-line": (
            HANDHELD, _code_long, "the caret on the longest line: the current "
            "line's mark meets the overflow fade"),
        "handheld_sprite-editor_palette": (
            HANDHELD, _sprites, "Sprites, colour %d current beside the focus hue"
            % SPRITE_COLOUR),
        "handheld_sysmenu": (HANDHELD, _sysmenu_home,
                             "the system menu over the focused home: one focus mark"),
        "handheld_pause": (HANDHELD, _pause,
                           "the pause menu over a running cart: one focus mark"),
        "desktop_sysmenu": (DESK, _sysmenu_desk,
                            "the system menu over a focused desk window: one focus mark"),
    }
    out = {k: v + ("theme",) for k, v in t.items()}
    for page, stops in (("settings", ("Wi-Fi", "Appearance", "Text size")),
                        ("appearance", ("Sound", "Volume", "Mode", "Accent", "Tint",
                                        "Wallpaper"))):
        for stop in stops:
            out["handheld_parts-%s_focus-%s" % (page, stop.lower().replace(" ", "-"))] = (
                HANDHELD, _parts_at(page, stop),
                "the parts sheet's %s page, %s focused" % (page, stop), "theme")
    for screen, (config, setup, what) in {
            "handheld_home": (HANDHELD, _home, "home"),
            "handheld_settings": (HANDHELD, _settings, "Settings"),
            "handheld_appearance": (HANDHELD, _appearance, "Appearance"),
            "handheld_sysmenu": (HANDHELD, _sysmenu_home, "the system menu over the home"),
            "handheld_pause": (HANDHELD, _pause, "the pause menu over Star Catcher"),
            "handheld_code-editor": (HANDHELD, _code, "the Code tab"),
            "handheld_settings_name-typing": (
                HANDHELD, _name_typing, "Settings, the Name field focused, %r typed"
                % NAME_TYPED),
            "handheld_wifi-password": (
                HANDHELD, _wifi_password, "the Wi-Fi password prompt, %d letters typed"
                % len(WIFI_TYPED)),
            "desktop_home": (CONSOLE, _console_home, "the console-mode home")}.items():
        out[screen + "_input"] = (config, setup, what, "profile")
    return out


def kit_scenes(filters=(), kit=True, profiles=False, theme=None, variant=None):
    """[(scene, screen, theme, mode, input or None)] the kit renders: its
    "theme" scenes in KIT_MODES (or in --theme/--variant) when `kit`, its
    "profile" scenes in every input profile when `profiles`."""
    table = _kit_table()
    out = []
    for screen, (_config, _setup, _what, kind) in sorted(table.items()):
        if kind == "theme" and kit:
            modes = ((theme or t, variant or m) for t, m in KIT_MODES) \
                if not (theme or variant) else ((theme or DEFAULT, variant or "dark"),)
            for t, m in modes:
                out.append(("%s-%s_%s" % (t, m, screen), screen, t, m, None))
        elif kind == "profile" and profiles:
            t, m = theme or DEFAULT, variant or "dark"
            for p in PROFILES:
                out.append(("%s-%s_%s-%s" % (t, m, screen, p), screen, t, m, p))
    if filters:
        def hit(f, name):
            return fnmatch.fnmatch(name, f) if any(c in f for c in "*?[") else f in name
        out = [s for s in out if any(hit(f, s[0]) for f in filters)]
    return out


# --------------------------------------------------------------------------
# what a render drew: the probes the checks and the audit read
# --------------------------------------------------------------------------

class Probe:
    """What a scene's frames drew, the last frame that drew each kind, in
    glass pixels: the covers' blits (x, y, w, h), the tabs (rect, active),
    the narrow rects (a caret's: x, y, w, h, colour), the icons (kind, ink)
    and the most focus marks one frame drew. A record drawn into a window's
    buffer is offset to its content rect, one drawn into a strip (the bars'
    cache) to where the frame blitted the strip; one whose canvas reaches
    the glass neither way is dropped."""

    KINDS = ("covers", "tabs", "rects", "icons")

    def __init__(self):
        self.last = {k: [] for k in self.KINDS}
        self.cur = {k: [] for k in self.KINDS}
        self.marks = 0
        self._undo = []
        self._ws = None
        self._blits = {}              # id(strip) -> (its canvas, x, y), kept across frames

    def begin(self):
        from runtime import ui_draw
        for k in self.KINDS:
            self.cur[k] = []
        ui_draw.marks(None, True)

    def _origin(self, cv, depth=0):
        ws = self._ws
        for win in getattr(ws.wm, "_wins", {}).values():
            if getattr(win, "buf", None) is cv:
                cx, cy, _w, _h = win.content_rect()
                return cx, cy
        if cv is ws.glass_canvas or cv is getattr(ws.wm, "_root_canvas", None) \
                or cv is ws.sys_canvas:
            return 0, 0
        to = self._blits.get(id(cv))
        if to is None or depth > 4 or to[0] is cv:
            return None
        o = self._origin(to[0], depth + 1)
        return None if o is None else (o[0] + to[1], o[1] + to[2])

    def end(self):
        from runtime import ui_draw
        for k in self.KINDS:
            got = []
            for cv, rec in self.cur[k]:
                o = (0, 0) if cv is None else self._origin(cv)
                if o is None:
                    continue
                if k == "tabs":
                    r, active = rec
                    got.append(((r[0] + o[0], r[1] + o[1], r[2], r[3]), active))
                elif k == "icons":
                    got.append(rec)
                else:
                    got.append((rec[0] + o[0], rec[1] + o[1]) + tuple(rec[2:]))
            if got:
                self.last[k] = got
        self.marks = max(self.marks, ui_draw.marks(None, False)[2])

    def install(self, ws):
        """Wrap the verbs the probes read; `remove` puts them back."""
        from runtime import ui_draw, iconsets
        self._ws = ws
        cur = self.cur
        blits = self._blits

        def patch(owner, name, wrap):
            real = getattr(owner, name)
            self._undo.append((owner, name, real))
            setattr(owner, name, wrap(real))

        for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
            def cover(real):
                def f(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest):
                    cur["covers"].append((cv, (dx, dy, dw, dh)))
                    return real(cv, pix, w, sx, sy, sw, sh, dx, dy, dw, dh, *rest)
                return f

            def rect(real):
                def f(cv, x, y, w, h, c, *rest):
                    if w <= 6:
                        cur["rects"].append((cv, (x, y, w, h, c)))
                    return real(cv, x, y, w, h, c, *rest)
                return f

            def blit_strip(real):
                def f(cv, strip, x, y, *rest):
                    blits[id(strip)] = (cv, x, y)
                    return real(cv, strip, x, y, *rest)
                return f
            patch(kind, "cover", cover)
            patch(kind, "rect", rect)
            patch(kind, "blit_strip", blit_strip)

        def tab(real):
            def f(cv, th, r, active, *a, **kw):
                cur["tabs"].append((cv, (tuple(r), bool(active))))
                return real(cv, th, r, active, *a, **kw)
            return f

        def icon(real):
            def f(cv, kind, rect, ink, *a, **kw):
                cur["icons"].append((None, (kind, ink)))
                return real(cv, kind, rect, ink, *a, **kw)
            return f
        for mod in {ui_draw, sys.modules.get("ui_draw", ui_draw)}:
            patch(mod, "tab", tab)
        for mod in {iconsets, sys.modules.get("iconsets", iconsets)}:
            patch(mod, "draw", icon)

    def remove(self):
        while self._undo:
            owner, name, real = self._undo.pop()
            setattr(owner, name, real)


# --------------------------------------------------------------------------
# the checks: geometry a regression fails the run on
# --------------------------------------------------------------------------

COVER = 128


def _clean(side):
    """A whole cover's side at a clean scale (owner call 2026-10-17)."""
    if side >= COVER:
        return side % COVER == 0
    return side in (COVER // 2, COVER // 4, COVER // 8)


def _diff(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def pads(img, r):
    """(left, right) pad of the ink inside pill `r` (x, y, w, h): from the
    rect's edge to the first and last column whose middle rows hold a pixel
    off the pill's own colour, its end columns (their anti-aliasing) aside."""
    x, y, w, h = r
    rows = range(y + h // 4, y + h - h // 4)
    hist = {}
    for yy in rows:
        for xx in range(x + 1, x + w - 1):
            p = img.getpixel((xx, yy))
            hist[p] = hist.get(p, 0) + 1
    if not hist:
        return None
    plate = max(hist, key=hist.get)
    ink = [xx for xx in range(x + 1, x + w - 1)
           if any(_diff(img.getpixel((xx, yy)), plate) > 48 for yy in rows)]
    if not ink:
        return None
    return ink[0] - x, x + w - 1 - ink[-1]


def checks(ws, probe, img):
    """[(check, ok, detail)] of one render: the hero's mark under the bar
    (R2-01), every cover at a clean scale (R2-07), one focus mark under a
    menu (R2-11) and each open tab's pill padded alike (R2-08)."""
    from runtime import ui_draw, layout_base
    out = []
    grid = getattr(ws, "launcher", None)
    menu = ws.sysmenu.open or ws.pause.open
    if (ws.screen == "launcher" and layout_base.metric_class() == layout_base.HANDHELD
            and hasattr(grid, "_geom") and not menu and ui_draw.marks_on()):
        geom = grid._geom()
        if geom[1] is not None and geom[0] is None:
            top = geom[7] - geom[2] - ui_draw.mark_reach("card")
            out.append(("hero rim", top >= geom[1][1],
                        "mark top y %d, bar line y %d" % (top, geom[1][1])))
    for dx, dy, dw, dh in (probe.last["covers"] if ws.screen == "launcher" else ()):
        out.append(("clean cover", dw == dh and _clean(dw),
                    "%dx%d at (%d, %d)" % (dw, dh, dx, dy)))
    if menu:
        out.append(("one focus mark", probe.marks <= 1,
                    "%d focus marks under the %s" % (
                        probe.marks, "pause menu" if ws.pause.open else "system menu")))
    for r, active in probe.last["tabs"]:
        if not active or r[2] < 8 or r[3] < 6:
            continue
        p = pads(img, r)
        if p is not None:
            out.append(("pill pads", abs(p[0] - p[1]) <= 1,
                        "tab at x %d: left %d, right %d" % (r[0], p[0], p[1])))
    return out


def bar_parity(rows):
    """[(check, ok, detail)] over the kept bars: a theme's bar packs alike in
    every mode it rendered in (R2-13). `rows` is [(theme, config, mode, bars)]."""
    by = {}
    for theme, config, mode, bars in rows:
        by.setdefault((theme, config), {})[mode] = bars
    out = []
    for (theme, config), modes in sorted(by.items()):
        if len(modes) > 1:
            vals = list(modes.values())
            out.append(("bar parity", all(v == vals[0] for v in vals),
                        "%s on %s, modes %s" % (theme, config, "/".join(sorted(modes)))))
    return out


# --------------------------------------------------------------------------
# the audit: the state pairs round 2 measured by hand
# --------------------------------------------------------------------------

def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def contrast(a, b):
    """The WCAG contrast ratio of RGB colours `a` and `b`."""
    la = 0.2126 * _lin(a[0]) + 0.7152 * _lin(a[1]) + 0.0722 * _lin(a[2])
    lb = 0.2126 * _lin(b[0]) + 0.7152 * _lin(b[1]) + 0.0722 * _lin(b[2])
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hex(c):
    return "#%02x%02x%02x" % c


def _index_rgb(ws, c):
    """Colour index `c` as the glass shows it: one pixel drawn and read."""
    cv = ws.glass_canvas
    buf = cv._buf
    keep = bytes(buf[0:2])
    cv.rect(0, 0, 1, 1, c)
    v = (buf[0] << 8) | buf[1]
    buf[0:2] = keep
    return (((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31)


def _colours(img, r, min_px=6):
    """{colour: count} inside rect `r`, clipped to the image."""
    x, y, w, h = r
    hist = {}
    for yy in range(max(0, y), min(img.height, y + h)):
        for xx in range(max(0, x), min(img.width, x + w)):
            p = img.getpixel((xx, yy))
            hist[p] = hist.get(p, 0) + 1
    return {c: n for c, n in hist.items() if n >= min_px}


# The pairs' floors: 3:1 where the pair is the only cue; a disabled ink is
# judged by how far it falls from the enabled one.
FLOOR = 3.0
DISABLED_FLOOR = 1.5


def _row_pairs(img, r, pair):
    """The pairs of a row: its plate (its commonest colour) against each
    colour that draws a part on it (a hundredth of the row or more: an edge's
    anti-aliasing is no part), the commonest four."""
    hist = _colours(img, r, max(6, r[2] * r[3] // 100))
    if not hist:
        return []
    plate = max(hist, key=hist.get)
    parts = sorted((c for c in hist if c != plate and _diff(c, plate) > 24),
                   key=lambda c: -hist[c])[:4]
    return [(pair, _hex(c), _hex(plate), contrast(c, plate), FLOOR) for c in parts]


def _focused_box(ws):
    """The focused stop's rect on the glass, where a kit scene says how to
    find it (Settings', the desk Appearance window's, the parts sheet's)."""
    f = getattr(ws, "_kit_box", None)
    return f() if f is not None else None


def audit(ws, probe, img):
    """[(pair, colour a, colour b, ratio, floor)] of one render: the focused
    row's parts against its plate, the caret against its line, the open
    tab's current plate against the ground beside it, a disabled history
    icon's ink against the enabled ink, and an Off toggle's row in Settings."""
    th = ws.theme_colors
    out = []
    box = _focused_box(ws)
    if box is not None:
        out += _row_pairs(img, box, "focused row part vs plate")
    sl = ws.settings_layer
    if ws.screen == "settings":
        for stop, kind in sl._kinds.items():
            if kind == "toggle" and "fps" in stop.lower() and stop != sl._focused:
                b = sl._host.box(stop)
                if b is not None:
                    out += _row_pairs(img, b, "Off toggle vs row ground")
    t = getattr(ws.code_layer, "_t", None)
    if ws.screen == "menu" and t is not None and "caret" in t:
        for x, y, w, h, c in probe.last["rects"]:
            if c == t["caret"] and h > 2 * w:
                cy = y + h // 2
                caret = img.getpixel((x, cy))
                ground = img.getpixel((max(0, x - 2), cy)) if x >= 2 else None
                band = _colours(img, (x - 14, y, 28, h), 1)
                band = {k: n for k, n in band.items() if k != caret}
                if band:
                    ground = max(band, key=band.get)
                if ground is not None:
                    out.append(("caret vs line ground", _hex(caret), _hex(ground),
                                contrast(caret, ground), FLOOR))
    for r, active in probe.last["tabs"]:
        if active and r[2] >= 8:
            x, y, w, h = r
            plate = _colours(img, (x + 1, y + h // 4, max(1, w - 2), max(1, h // 2)), 1)
            side = _colours(img, (x - 4, y + h // 4, 3, max(1, h // 2)), 1)
            side.update(_colours(img, (x + w + 1, y + h // 4, 3, max(1, h // 2)), 1))
            if plate and side:
                a, b = max(plate, key=plate.get), max(side, key=side.get)
                out.append(("current plate vs ground", _hex(a), _hex(b), contrast(a, b),
                            FLOOR))
    enabled = th.get("chrome_ink")
    seen = set()
    for kind, ink in probe.last["icons"]:
        if kind in ("undo", "redo") and ink != enabled and kind not in seen \
                and enabled is not None:
            seen.add(kind)
            a, b = _index_rgb(ws, ink), _index_rgb(ws, enabled)
            out.append(("disabled %s vs enabled ink" % kind, _hex(a), _hex(b),
                        contrast(a, b), DISABLED_FLOOR))
    seen, kept = set(), []
    for row in out:
        if row[:3] not in seen:
            seen.add(row[:3])
            kept.append(row)
    return kept


# --------------------------------------------------------------------------
# the pairs
# --------------------------------------------------------------------------

def pair(mockup, shot, built, scale=1):
    """mockup | render, each at `scale`, under a one-line caption strip."""
    from PIL import Image, ImageDraw
    a = mockup.convert("RGB")
    b = shot.convert("RGB")
    if scale > 1:
        a = a.resize((a.width * scale, a.height * scale), Image.NEAREST)
        b = b.resize((b.width * scale, b.height * scale), Image.NEAREST)
    gap, cap = 8, 14
    out = Image.new("RGB", (a.width + gap + b.width,
                            cap + max(a.height, b.height)), (40, 40, 40))
    out.paste(a, (0, cap))
    out.paste(b, (a.width + gap, cap))
    d = ImageDraw.Draw(out)
    d.text((2, 2), "mockup (target)", fill=(230, 230, 230))
    d.text((a.width + gap + 2, 2),
           "render" + ("" if built else " -- NOT BUILT YET"),
           fill=(230, 230, 230) if built else (255, 170, 60))
    return out


def run(out, filters=(), step=None, theme=None, variant=None, quiet=False, parts=False,
        inputs=None, kit=False, profiles=False, only_kit=False, audit_csv=False,
        failed=None):
    """Render, pair and index the kept scenes into `out`, and the kit's own
    scenes into `out`/kit when `kit` or `profiles`. Returns the rows
    written: [(scene, built, missing)]. Every failed check is appended to
    `failed` as (scene, check, detail). The cover slice and the bar's clock
    are pinned for the run and put back after it."""
    g = _goldens()
    from runtime import cover_cache
    saved = cover_cache._COVER_SLICE_MS, g._CLOCK
    cover_cache._COVER_SLICE_MS = g._COVER_SLICE_UNBOUNDED
    g._CLOCK = CLOCK
    bars = []
    fails = [] if failed is None else failed
    try:
        rows = []
        if not only_kit:
            rows = _run(g, out, filters, step, theme, variant, quiet, parts, inputs,
                        audit_csv, fails, bars)
        if kit or profiles:
            rows += _run_kit(g, os.path.join(out, "kit"), filters, theme, variant, quiet,
                             kit, profiles, inputs, audit_csv, fails, bars)
        for check, ok, detail in bar_parity(bars):
            if not ok:
                fails.append(("(run)", check, detail))
            if not quiet:
                print("%-40s %s %s" % ("check " + check, "ok" if ok else "FAIL", detail))
        return rows
    finally:
        cover_cache._COVER_SLICE_MS, g._CLOCK = saved


def _report(out, name, report, audit_csv, fails, quiet):
    """The index lines of one render's checks and audit; its CSV with
    `audit_csv`; each failed check into `fails`."""
    lines = []
    for check, ok, detail in report["checks"]:
        lines.append("  check: %s %s (%s)" % (check, "ok" if ok else "FAILED", detail))
        if not ok:
            fails.append((name, check, detail))
            if not quiet:
                print("  FAILED %s: %s" % (check, detail))
    flagged = [a for a in report["audit"] if a[3] < a[4]]
    for pair, a, b, ratio, floor in flagged:
        lines.append("  audit: FLAG %s %s on %s %.2f:1 (floor %.1f)" % (pair, a, b, ratio,
                                                                       floor))
    if audit_csv:
        with open(os.path.join(out, name + ".audit.csv"), "w", encoding="utf-8") as f:
            f.write("scene,pair,colour,against,ratio,floor,flag\n")
            for pair, a, b, ratio, floor in report["audit"]:
                f.write("%s,%s,%s,%s,%.2f,%.1f,%s\n" % (name, pair, a, b, ratio, floor,
                                                       "FLAG" if ratio < floor else ""))
    return lines


def _run_kit(g, out, filters, theme, variant, quiet, kit, profiles, inputs, audit_csv,
             fails, bars):
    """Render the kit's own scenes into `out` with their index."""
    from PIL import Image
    table = _kit_table()
    scenes = kit_scenes(filters, kit, profiles, theme, variant)
    held = themes_held(g)
    os.makedirs(out, exist_ok=True)
    rows, lines = [], []
    for name, screen, want_theme, mode, profile in scenes:
        config, setup, what, _kind = table[screen]
        drawn = want_theme if want_theme in held else DEFAULT
        shot, settled, report = render(g, config, drawn, mode, setup, False,
                                       profile or inputs)
        bars.append((drawn, config, mode, report["bars"]))
        shot.save(os.path.join(out, name + ".png"))
        files = [name + ".png"]
        if shot.size == (320, 240):
            shot.resize((960, 720), Image.NEAREST).save(os.path.join(out, name + "@3x.png"))
            files.append(name + "@3x.png")
        lines.append("%s  %s" % (name, "  ".join(files)))
        lines.append("  shows: %s; theme %s, %s; input %s; config %s%s" % (
            what, drawn, mode, profile or inputs or (HANDHELD_INPUT if config == HANDHELD
                                                      else "keys"),
            config, "" if settled else "; NOT SETTLED (still animating)"))
        lines += _report(out, name, report, audit_csv, fails, quiet)
        lines.append("")
        rows.append((name, True, []))
        if not quiet:
            print("%-40s kit" % name)
    head = ["The 2026-10 look's review kit: states no mockup draws (tools/look_shots.py "
            "--kit/--profiles)", "",
            "Each scene is the real shell rendered on the host; its checks and its",
            "audit's flags follow it.", ""]
    with open(os.path.join(out, "index.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(head + lines))
    return rows


def _run(g, out, filters, step, theme, variant, quiet, parts=False, inputs=None,
         audit_csv=False, fails=None, bars=None):
    from PIL import Image
    table = _setup_table()
    fails = [] if fails is None else fails
    bars = [] if bars is None else bars
    scenes = select(read_index(), filters, step)
    if not scenes:
        raise SystemExit("look_shots: no scene matches %s" % (
            " ".join(filters) or "--step %s" % step))
    landed = plan_steps()
    held = themes_held(g)
    os.makedirs(out, exist_ok=True)
    rows, lines = [], []
    for name, what, want_theme, want_mode, size in scenes:
        config, _steps, wallpaper, setup, shows = table[screen_of(name)]
        if parts and screen_of(name) in PARTS:
            setup = _parts(PARTS[screen_of(name)])
            shows = "the parts sheet's %s page" % PARTS[screen_of(name)]
        target_theme = theme or want_theme or DEFAULT
        mode = variant or want_mode or "dark"
        missing = []
        drawn = target_theme
        if target_theme not in held:
            drawn = theme if theme in held else DEFAULT
            missing.append("theme %s is not in the shell (drawn with %s)"
                           % (target_theme, drawn))
        todo = [s for s in scene_steps(name, target_theme)
                if not landed.get(s)]
        if todo:
            missing.append("steps %s have not landed" % ", ".join(todo))
        if want_theme is None:
            missing.append("no shell screen draws this sheet")
        shot, settled, report = render(g, config, drawn, mode, setup, wallpaper, inputs)
        bars.append((drawn, config, mode, report["bars"]))
        built = not missing
        mock = Image.open(os.path.join(MOCKUPS, name + ".png"))
        mock.save(os.path.join(out, name + ".mockup.png"))
        shot.save(os.path.join(out, name + ".render.png"))
        pair(mock, shot, built).save(os.path.join(out, name + ".pair.png"))
        files = [name + ".pair.png"]
        if size == (320, 240):
            shot.resize((960, 720), Image.NEAREST).save(
                os.path.join(out, name + ".render@3x.png"))
            pair(mock, shot, built, 3).save(
                os.path.join(out, name + ".pair@3x.png"))
            files.insert(0, name + ".pair@3x.png")
        lines.append("%s  [%s]  %s" % (name, "built" if built
                                         else "NOT BUILT YET",
                                         "  ".join(files)))
        lines.append("  target: %s" % what)
        lines.append("  render: %s; theme %s, %s; config %s; %dx%d%s" % (
            shows, drawn, mode, config, shot.width, shot.height,
            "" if settled else "; NOT SETTLED (still animating)"))
        for m in missing:
            lines.append("  missing: " + m)
        lines += _report(out, name, report, audit_csv, fails, quiet)
        lines.append("")
        rows.append((name, built, missing))
        if not quiet:
            print("%-40s %s" % (name, "built" if built else "not built yet"))
    head = [
        "The 2026-10 look: mockup | render pairs (tools/look_shots.py)",
        "",
        "Left is the target (docs/media/look_2026-10/), right is the real "
        "shell rendered on the host.",
        "Pass the scene or list every difference. A difference stands only "
        "if it is RGB565",
        "quantisation, real data (the seed carts' own code, art and titles), "
        "or a deviation",
        "the step's commit names with its reason (docs/look_2026-10.md, "
        "Review).",
        "A NOT BUILT YET scene is the nearest current screen: its 'missing' "
        "lines say what",
        "still moves its pixels.",
        "",
    ]
    with open(os.path.join(out, "index.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(head + lines))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render each 2026-10 look scene from the real shell on "
        "the host and pair it with its mockup (no board).")
    ap.add_argument("out", help="output directory")
    ap.add_argument("filters", nargs="*", metavar="FILTER",
                    help="keep scenes whose name contains this (or matches "
                    "this glob)")
    ap.add_argument("--step", help="keep the scenes plan step STEP targets "
                    "(e.g. 12, 22a)")
    ap.add_argument("--theme", help="render every scene in this theme")
    ap.add_argument("--variant", choices=("dark", "light"),
                    help="render every scene in this mode")
    ap.add_argument("--parts", action="store_true",
                    help="draw the handheld Settings and Appearance scenes "
                    "with the parts sheet")
    ap.add_argument("--input", choices=PROFILES,
                    help="draw every scene's hints with this input's keys")
    ap.add_argument("--kit", action="store_true",
                    help="add the kit's own scenes in its five theme-modes")
    ap.add_argument("--profiles", action="store_true",
                    help="add the hint-bearing screens once per input profile")
    ap.add_argument("--only-kit", action="store_true",
                    help="skip the mockup pairs (with --kit or --profiles)")
    ap.add_argument("--audit", action="store_true",
                    help="write SCENE.audit.csv, the state pairs' contrast")
    args = ap.parse_args(argv)
    failed = []
    rows = run(args.out, args.filters, args.step, args.theme, args.variant,
               parts=args.parts, inputs=args.input, kit=args.kit, profiles=args.profiles,
               only_kit=args.only_kit, audit_csv=args.audit, failed=failed)
    print("%d scenes, %d built -> %s" % (
        len(rows), sum(1 for r in rows if r[1]),
        os.path.join(args.out, "index.txt")))
    for name, check, detail in failed:
        print("FAILED %s: %s (%s)" % (name, check, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
