#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   -- the index and the plan      read_index, plan_steps, scene_steps, select
#   -- the shell                   each scene's setup over the goldens' workstation
#   render                         one scene on a fresh workstation: image, checks, audit
#   -- the kit's own scenes        _kit_table, kit_scenes: states no mockup draws
#   Probe                          what a render's frames drew, in glass pixels
#   -- the checks: geometry        checks, pads, bar_parity: what fails the run
#   -- the audit                   audit, contrast: the state pairs and their ratios
#   -- the pairs                   pair, run, _run, _run_kit, main
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
palette; the system menu and the pause menu over focused content; the
desk with Calc; a long press on a Games and a Make card and a pad's X (the
card's verbs as a menu) and up from the desk home's shelf, each under the
input it is made with (`KIT_INPUT`) and each checked to have opened what it
opens. `--profiles` adds the hint-bearing screens once per input profile,
the Make tab, the Name field being typed and the Wi-Fi password prompt among
them; a scene drawn with the T-Deck's keys notes that the host cannot prove
its keyboard's keys. They are written into OUT/kit/ with their own
index.txt; `--only-kit` skips the mockup pairs.

Every render is checked, and a failed check fails the run (exit 1): the
handheld hero's focus mark lies under the bar, every cover a home draws is
the whole 128 px cover at a clean scale, a menu over content leaves one focus
mark on the screen, an open tab's pill has its ink padded alike both sides,
and a theme's bar packs alike in every mode the run drew it in. `--audit`
writes SCENE.audit.csv beside each render: the state pairs the review rounds
measured by hand (a focused row's parts against its plate and its mark
against the row, the caret against its line, the open tab's current mark
against the ground beside it, the focused desk chip's label, a disabled
history icon against the enabled ink, an Off toggle against its row, a lifted
hero's rim against its cover, a Settings caption cut at the page top, the
Code tab's left edges against its inset and its caret against the next stem,
the sprite tools' ink and columns, the desk Appearance foot, the desk Calc's
backspace against a digit),
each with its ratio and a FLAG under its floor. A box is measured as it
shows -- cut to its window or the content under the bar, less the hint band
-- inside its plate's own run; anti-aliasing is no colour of its own; text
is held to 4.5:1 and a part to 3:1; a current mark by the look's
`current.style`, a faint plate passing on its label's ink; a swatch only has
to show, and a preview card is measured by its ring. A pair an open design
question owns (`D05_TOKENS`, `D08_KINDS`), or a measure with a known, filed
gap (`KNOWN`), names it in the CSV's owner column and is no FLAG; the run
prints the FLAG count.
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


def _sprites_more(g, ws):
    """The sprite tab with its more menu open, a selection held (the hint
    strip's operations first)."""
    _sprites(g, ws)
    if ws.paint is not None:
        ws.paint.set_selection(1, 1, 4, 4)
        ws.paint_layer._open_more()
    _settle(g, ws, 3)


def _playtest_minimised(g, ws):
    """The playtest minimised to its chip: the run held, the chip's pause
    badge (D-25)."""
    _playtest(g, ws)
    _settle(g, ws, 2)
    win = ws.wm._wins.get("desktop")
    if win is not None:
        ws.wm._minimize(win, True)
        ws.wm._move_focus("menu")
    _settle(g, ws, 3)


def _playtest_restored(g, ws):
    """The minimised playtest restored from its chip: the pause menu over it
    (D-25)."""
    _playtest_minimised(g, ws)
    ws.wm._chip_tap("desktop")
    _settle(g, ws, 3)


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
        _FOCUSED[0] = _FOCUSED_KIND[0] = _EXPECT[0] = None
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
        expect = _EXPECT[0](ws) if _EXPECT[0] is not None else []
        report = {"checks": checks(ws, probe, img) + expect, "audit": audit(ws, probe, img),
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

# How the scene being rendered finds its focused stop on the glass, and
# that stop's part kind (a kit setup sets them; `render` clears them).
_FOCUSED = [None]
_FOCUSED_KIND = [None]
# A kit scene's own checks, ws -> [(check, ok, detail)], read after it
# settles (a setup sets it; `render` clears it).
_EXPECT = [None]

# The kit screens that draw one input's keys whatever the run's profile:
# a long press is a finger's, X and up a gamepad's.
KIT_INPUT = {"handheld_home_long-press": "touch", "handheld_make_long-press": "touch",
             "handheld_home_pad-x": "pad", "desktop_home_pad-up": "pad"}

# The parts sheet's and the desk Appearance's stops by the part they draw,
# where the audit measures a part its own way (`_row_pairs`).
PART_KINDS = {"Mode": "seg", "Accent": "swatches", "Sound": "toggle", "Volume": "slider",
              "Tint": "cycle"}

# A note on every scene drawn with the T-Deck's keys: the host types every
# key, so what a hint names arriving from its keyboard is the board's to show.
TDECK_NOTE = ("the host types every key; that the T-Deck's keyboard delivers "
              "the keys these hints name is proven on glass "
              "(tests/test_tdeck_on_glass.py) and in tests/test_tdeck_keymap.py")

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
        _FOCUSED[0] = lambda: sl._host.box(sl._focused) if sl._focused else None
        _FOCUSED_KIND[0] = lambda: sl._kinds.get(sl._focused)
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
        _FOCUSED[0] = box
        _FOCUSED_KIND[0] = lambda: (PART_KINDS.get(app._focused)
                                    or app._kinds.get(app._focused))
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
        _FOCUSED[0] = lambda: ui_draw._C.api_box(app.ctx, stop)
        _FOCUSED_KIND[0] = lambda: PART_KINDS.get(stop)
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


class _Pressed:
    """One frame's input with button `name` pressed, for a layer's
    handle_input (a gamepad's X and up, which the host's keyboard has not)."""

    def __init__(self, name):
        self.name = name
        self.last_key = 0

    def pressed(self, name):
        return name == self.name

    def held(self, name):
        return name == self.name


def _first_card(ws):
    """Select the home's first real card (the Make tab's leads with + New)."""
    for i, it in enumerate(ws.launcher.items):
        if it.get("path"):
            ws.launcher.sel = i
            return


def _make(g, ws):
    """The Make tab with a project selected: New, Change, Copy and Delete."""
    _home(g, ws)
    ws.set_home_tab("make")
    _first_card(ws)


# How long the kit's finger stays down: past the launcher's LONG_PRESS_MS.
PRESS_MS = 650


def _long_press(base):
    """`base`, then a long press on the selected card through the real
    pointer feed -- a finger held still past LONG_PRESS_MS, then lifted --
    which opens the card's verbs over the home (design review R3-01). The
    scene's check fails unless the menu opened."""
    def setup(g, ws):
        base(g, ws)
        ws._golden_popover = True          # the frames' quiesce keeps it open
        drv = _driver(ws)
        for _ in range(3):
            g._quiesce(ws)
            drv.frame(g._DT)
        r = ws.launcher.tile_rect(ws.launcher.sel)
        drv.touch(r[0] + r[2] // 2, r[1] + r[3] // 2)
        for _ in range(int(PRESS_MS / 1000.0 / g._DT)):
            drv.frame(g._DT)
        drv.touch_up()
        drv.frame(g._DT)
        _EXPECT[0] = _card_menu_open("a long press on the card")
    return setup


def _card_menu_open(how):
    def check(ws):
        got = ws.sysmenu.open and ws.sysmenu.widget == "card"
        rows = [r[4] for r in ws.sysmenu.items if r[0] == "item"] if got else []
        return [("card menu", bool(got), "%s opens its verbs: %s" % (
            how, ", ".join(rows) if got else "no menu"))]
    return check


def _pad_x(g, ws):
    """A gamepad's X on the home's card: its verbs, as a long press opens
    them (design review R3-16)."""
    _home(g, ws)
    _settle(g, ws, 2)
    ws._golden_popover = True
    ws.launcher_layer.handle_input(_Pressed("x"))
    _EXPECT[0] = _card_menu_open("a pad's X")


def _pad_up(g, ws):
    """Up from the desk home's shelf: the keys move to the details pane's
    buttons, the shelf's card keeping its place (design review R3-16)."""
    _console_home(g, ws)
    _settle(g, ws, 2)
    ws.launcher_layer.handle_input(_Pressed("up"))

    def check(ws):
        k = ws.launcher_layer._button_focus
        return [("shelf up", k is not None, "up from the shelf focuses button %r" % (k,))]
    _EXPECT[0] = check


def _kit_table():
    """kit screen -> (config, setup, what it shows, kind): a "theme" scene
    renders in each of KIT_MODES (--kit), a "profile" one in each input
    profile (--profiles), in the run's theme (Moy Night by default). A
    screen in KIT_INPUT draws that input's keys in every theme-mode."""
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
        "desktop_sprite-editor": (DESK, _sprites, "the desk Sprites tab: its tool row "
                                  "and the sprite map"),
        "desktop_sprite-editor_more": (DESK, _sprites_more,
                                       "the desk Sprites tab's more menu, a selection held"),
        "handheld_sprite-editor_more": (HANDHELD, _sprites_more,
                                        "the Sprites tab's more menu, a selection held"),
        "handheld_sprite-editor_palette": (
            HANDHELD, _sprites, "Sprites, colour %d current beside the focus hue"
            % SPRITE_COLOUR),
        "desktop_playtest_minimised": (DESK, _playtest_minimised,
                                       "the playtest minimised: its chip's pause badge"),
        "desktop_playtest_restored": (DESK, _playtest_restored,
                                      "the minimised playtest restored: the pause menu"),
        "handheld_sysmenu": (HANDHELD, _sysmenu_home,
                             "the system menu over the focused home: one focus mark"),
        "handheld_pause": (HANDHELD, _pause,
                           "the pause menu over a running cart: one focus mark"),
        "desktop_sysmenu": (DESK, _sysmenu_desk,
                            "the system menu over a focused desk window: one focus mark"),
        "desktop_calc": (DESK, _desk, "the desk: Files and Calc, the tape's sums"),
        "handheld_home_long-press": (HANDHELD, _long_press(_home),
                                     "a long press on a Games card: its verbs"),
        "handheld_make_long-press": (HANDHELD, _long_press(_make),
                                     "a long press on a Make card: its verbs"),
        "handheld_home_pad-x": (HANDHELD, _pad_x, "a pad's X on a Games card: its verbs"),
        "desktop_home_pad-up": (CONSOLE, _pad_up,
                                "up from the desk home's shelf: the details pane's buttons"),
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
            "handheld_make": (HANDHELD, _make, "the Make tab, a project selected"),
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
    the narrow rects (a caret's: x, y, w, h, colour), the icons (kind, ink),
    the desk bar's chips (rect, focused), the pages that drew hints (`ui.hints`
    calls) and the most focus marks one frame drew. A record drawn into a window's
    buffer is offset to its content rect, one drawn into a strip (the bars'
    cache) to where the frame blitted the strip; one whose canvas reaches
    the glass neither way is dropped."""

    KINDS = ("covers", "tabs", "rects", "icons", "chips", "hints")

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
                if k in ("tabs", "chips"):
                    r, active = rec
                    got.append(((r[0] + o[0], r[1] + o[1], r[2], r[3]), active))
                elif k in ("icons", "hints"):
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
        def bar_part(real):
            def f(cv, th, part, rect, on=0, *a, **kw):
                if part == ui_draw.BAR_CHIP:
                    cur["chips"].append((cv, (tuple(rect), bool(on))))
                return real(cv, th, part, rect, on, *a, **kw)
            return f

        def hints(real):
            def f(items, *a, **kw):
                cur["hints"].append((None, tuple(items)))
                return real(items, *a, **kw)
            return f
        for mod in {ui_draw, sys.modules.get("ui_draw", ui_draw)}:
            patch(mod, "tab", tab)
            patch(mod, "bar_part", bar_part)
        from runtime import ui as ui_api
        for mod in {ui_api, sys.modules.get("ui", ui_api)}:
            patch(mod, "hints", hints)
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


def _covered(ws, r):
    """Whether a window above the Editor's covers rect `r`'s middle on the
    glass (a playtest over its tab strip): its pill shows no pads there."""
    order = getattr(ws.wm, "_order", None) or []
    wins = getattr(ws.wm, "_wins", {})
    if "menu" not in order:
        return False
    mx, my = r[0] + r[2] // 2, r[1] + r[3] // 2
    for key in order[order.index("menu") + 1:]:
        w = wins.get(key)
        if w is not None and not getattr(w, "minimized", False) \
                and w.x <= mx < w.x + w.w and w.y <= my < w.y + w.h:
            return True
    return False


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
        if not active or r[2] < 8 or r[3] < 6 or _covered(ws, r):
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


# The pairs' floors: a part (a fill, a line) against what it sits on 3:1,
# text against its ground 4.5:1 (WCAG's); a disabled ink, and the second cue
# beside a faint current plate, by how far it falls from the plain ink; a
# colour sample (a swatch) only by whether it shows at all.
FLOOR = 3.0
TEXT_FLOOR = 4.5
DISABLED_FLOOR = 1.5
SAMPLE_FLOOR = 1.5

# The open design questions that own a pair the audit measures (judged
# round 3): in Moy and Blocks the structure ramp -- a hairline, a field's
# outline, the theme's `border`/`edge` -- is D-05's, and the stepped bar's
# rest steps and the slider's rest track are D-08's. Such a row carries its
# owner and is no FLAG.
D05_TOKENS = ("border", "edge")
OWNED_THEMES = ("moy", "blocks")
D08_KINDS = ("slider", "steps", "volume")
# The measures with a known gap the round filed: the desk Calc's backspace
# waits on the engine sizing a button's icon by its face (R3-09,
# tests/test_editor_look.py holds it as a strict xfail).
KNOWN = {"desk backspace ink height vs digit cap": "R3-09"}


def _clip(r, v):
    """Rect `r` cut to rect `v`, or None where nothing is left."""
    x0, y0 = max(r[0], v[0]), max(r[1], v[1])
    x1, y1 = min(r[0] + r[2], v[0] + v[2]), min(r[1] + r[3], v[1] + v[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _viewport(ws, r, hinted):
    """The glass a measured box shows on: the desk window holding it (its
    content), else the content rect under the bar; less the hint band at
    its foot where the page drew hints with keys (the engine's band, the
    value row's `hints` metric)."""
    from runtime import ui_draw, layout_base
    lay = ws.layout
    view = (lay.cx, lay.cy, lay.cw, lay.ch)
    mx, my = r[0] + r[2] // 2, r[1] + r[3] // 2
    for win in getattr(ws.wm, "_wins", {}).values():
        if getattr(win, "minimized", False):
            continue
        c = win.content_rect()
        if c[0] <= mx < c[0] + c[2] and c[1] <= my < c[1] + c[3]:
            view = c
    if hinted and ui_draw.input_kind() != "touch":
        desk = layout_base.metric_class() != layout_base.HANDHELD
        band = ui_draw.metrics("value_row")[17 if desk else 16] * lay.fs
        view = (view[0], view[1], view[2], max(0, view[3] - band))
    return view


def _run_of(px, r, c):
    """The bounding box of colour `c`'s pixels in `r` (`px` the image's
    pixel access): a plate's own run, so a frame or a ring drawn just
    outside a plate is not measured as sitting on it."""
    x, y, w, h = r
    xs, ys = [], []
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if px[xx, yy] == c:
                xs.append(xx)
                ys.append(yy)
    if not xs:
        return r
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def _shape(px, r, c, fs=1):
    """How colour `c` lies in rect `r`: "solid" where it fills a 3x3 block,
    "line" where it runs straight longer than a glyph's stroke (16 pixels
    across a font scale, or three quarters of the rect down), else "text"
    (a glyph's strokes)."""
    x, y, w, h = r
    for yy in range(y, y + h - 2):
        for xx in range(x, x + w - 2):
            if px[xx, yy] == c and all(px[xx + i, yy + j] == c
                                       for i in range(3) for j in range(3)):
                return "solid"
    for yy in range(y, y + h):
        run = 0
        for xx in range(x, x + w):
            run = run + 1 if px[xx, yy] == c else 0
            if run >= 16 * fs:
                return "line"
    for xx in range(x, x + w):
        run = 0
        for yy in range(y, y + h):
            run = run + 1 if px[xx, yy] == c else 0
            if run >= max(12 * fs, h * 3 // 4):
                return "line"
    return "text"


def _neighbours(px, r, c):
    """The colours touching colour `c`'s pixels in `r` (8-neighbours)."""
    x, y, w, h = r
    out = set()
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if px[xx, yy] != c:
                continue
            for j in (-1, 0, 1):
                for i in (-1, 0, 1):
                    if x <= xx + i < x + w and y <= yy + j < y + h:
                        out.add(px[xx + i, yy + j])
    out.discard(c)
    return out


def _between(c, a, b):
    """Is `c` on the line from `a` to `b`, short of both ends, within a
    tenth of their distance: what an edge's anti-aliasing mixes?"""
    ab = [b[k] - a[k] for k in range(3)]
    n = sum(v * v for v in ab)
    if n == 0:
        return False
    t = sum((c[k] - a[k]) * ab[k] for k in range(3)) / float(n)
    if not 0.04 < t < 0.96:
        return False
    d = sum((c[k] - a[k] - t * ab[k]) ** 2 for k in range(3))
    return d <= 0.01 * n


def _blend(px, r, c):
    """Is `c` anti-aliasing: on the line between two colours it touches?"""
    near = list(_neighbours(px, r, c))
    for i, a in enumerate(near):
        for b in near[i + 1:]:
            if _between(c, a, b):
                return True
    return False


def _inks(img, r, plate, fs=1):
    """{shape: [colour, ...]} of the solid colours drawn on `plate` in `r`,
    commonest first: anti-aliasing and colours under a 400th of the rect (a
    stray edge) left out."""
    px = img.load()
    hist = _colours(img, r, max(6, r[2] * r[3] // 400))
    out = {"solid": [], "line": [], "text": []}
    for c in sorted(hist, key=lambda c: -hist[c]):
        if c == plate or _diff(c, plate) <= 24:
            continue
        shape = _shape(px, r, c, fs)
        if shape != "solid" and _blend(px, r, c):
            continue
        out[shape].append(c)
    return out


def _distinct(a, b):
    """Do two inks tell apart as a second cue: by lightness (1.5:1, as a
    disabled ink is judged) or by hue (an accent beside a grey of its
    lightness, 96 apart in RGB)?"""
    return contrast(a, b) >= DISABLED_FLOOR or _diff(a, b) >= 96


def _owner(ws, kind, c, ratio, floor):
    """The open question a failing pair is owned by ("" for none)."""
    if ratio >= floor or ws.look.theme_name not in OWNED_THEMES or c is None:
        return ""
    if c in _token_rgbs(ws, D05_TOKENS):
        return "D-05"
    if kind in D08_KINDS:
        return "D-08"
    return ""


def _token_rgbs(ws, names):
    th = ws.theme_colors
    return {_index_rgb(ws, th[n]) for n in names if th.get(n) is not None}


def _row_pairs(ws, img, r, pair, kind=None, view=None):
    """The pairs of a row: its plate -- the commonest colour of the row as
    it shows (cut to `view`), measured inside the plate's own run -- against
    each solid colour drawn on it, the commonest four: a fill or a line at
    3:1, text at 4.5:1. A part kind measures its own way: a swatch's fill
    is a sample, held only to show at all, and a card's preview art and a
    field's own ground are not measured, their rings are; a segment's
    selected pill may lean on its label's ink, as a current plate does."""
    if view is not None:
        r = _clip(r, view)
    if r is None or r[2] < 3 or r[3] < 3:
        return []
    hist = _colours(img, r, 1)
    if not hist:
        return []
    plate = max(hist, key=hist.get)
    r = _run_of(img.load(), r, plate)
    if r[2] < 3 or r[3] < 3:
        return []
    inks = _inks(img, r, plate, ws.layout.fs)
    out = []

    def add(name, c, floor):
        ratio = contrast(c, plate)
        out.append((name, _hex(c), _hex(plate), ratio, floor,
                    _owner(ws, kind, c, ratio, floor)))
    for c in inks["text"][:2]:
        add(pair + " (text)", c, TEXT_FLOOR)
    for c in inks["line"][:2]:
        add(pair + (" (ring)" if kind in ("swatches", "card") else ""), c, FLOOR)
    solids = inks["solid"][:4]
    if kind == "card":
        solids = []
    elif kind == "swatches":
        keys = [c for c in inks["text"] + inks["line"] if contrast(c, plate) >= FLOOR]
        if keys:            # the dots wear a keyline: it is what shows them
            add("swatch keyline vs plate", keys[0], FLOOR)
        else:
            for c in solids:
                add("swatch vs plate (shows)", c, SAMPLE_FLOOR)
        solids = []
    elif kind == "field":
        solids = []         # the field's own ground, which its ring bounds
    elif kind == "seg" and solids and contrast(solids[0], plate) < FLOOR:
        # a faint selected pill: its label's ink against a plain label's
        pill = solids[0]
        on = _inks(img, _run_of(img.load(), r, pill), pill, ws.layout.fs)
        mine = [c for c in on["text"] + on["solid"] + on["line"] if c != plate]
        plain = [c for c in inks["text"] if c not in mine]
        if mine and plain and _distinct(mine[0], plain[0]):
            out.append(("selected label ink vs plain label ink", _hex(mine[0]),
                        _hex(plain[0]), contrast(mine[0], plain[0]),
                        min(DISABLED_FLOOR, contrast(mine[0], plain[0])), ""))
            solids = solids[1:]
    for c in solids:
        add(pair, c, FLOOR)
    return out


def _focused_box(ws):
    """The focused stop's rect on the glass, where a kit scene says how to
    find it (Settings', the desk Appearance window's, the parts sheet's)."""
    f = _FOCUSED[0]
    return f() if f is not None else None


def _theme_look(ws):
    """The live theme's `look` block, from its file (a theme the shell
    seeds); {} for one it does not."""
    import json
    path = os.path.join(ROOT, "native", "moy_ui", "themes", ws.look.theme_name + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("look", {})
    except (OSError, ValueError):
        return {}


def _tab_ink(img, r, plate):
    """The label ink of tab `r` on `plate`: its commonest solid text ink."""
    inner = (r[0] + 1, r[1] + 1, max(1, r[2] - 2), max(1, r[3] - 2))
    inks = _inks(img, inner, plate)
    got = inks["text"] or inks["line"]
    return got[0] if got else None


def _current_pairs(ws, probe, img):
    """The open tab's current mark against the ground beside it, by the
    look's `current.style`: a bar under the tab, else a plate -- and where
    a plate is faint, its second cue, the label's ink against a plain
    tab's, which is flagged only when both miss."""
    style = (_theme_look(ws).get("current") or {}).get("style", "bar")
    tabs = probe.last["tabs"]
    out = []
    for r, active in tabs:
        if not active or r[2] < 8 or _covered(ws, r):
            continue
        x, y, w, h = r
        side = _colours(img, (x - 4, y + h // 4, 3, max(1, h // 2)), 1)
        side.update(_colours(img, (x + w + 1, y + h // 4, 3, max(1, h // 2)), 1))
        if not side:
            continue
        ground = max(side, key=side.get)
        if style == "bar":
            fs = ws.layout.fs
            bar = _colours(img, (x + 2 * fs, y + h - 3, max(1, w - 4 * fs), 3), 1)
            bar = {c: n for c, n in bar.items() if c != ground}
            if bar:
                c = max(bar, key=bar.get)
                out.append(("current bar vs ground", _hex(c), _hex(ground),
                            contrast(c, ground), FLOOR, ""))
            continue
        plate = _colours(img, (x + 1, y + h // 4, max(1, w - 2), max(1, h // 2)), 1)
        if not plate:
            continue
        p = max(plate, key=plate.get)
        row = ("current plate vs ground", _hex(p), _hex(ground), contrast(p, ground), FLOOR,
               "")
        if row[3] < FLOOR:
            mine = _tab_ink(img, r, p)
            plain = [_tab_ink(img, q, ground) for q, act in tabs if not act and q[2] >= 8]
            plain = [c for c in plain if c is not None]
            if mine is not None and plain and _distinct(mine, plain[0]):
                ratio = contrast(mine, plain[0])
                row = ("current label ink vs plain label ink", _hex(mine), _hex(plain[0]),
                       ratio, min(DISABLED_FLOOR, ratio), "")
        out.append(row)
    return out


def _chip_pairs(ws, probe, img):
    """The focused window's chip on the desk bar: its label's ink against
    its plate, as text (design review R3-06)."""
    out = []
    for r, focused in probe.last["chips"]:
        if not focused or r[2] < 40:
            continue
        x, y, w, h = r
        label = (x + 38, y + 2, max(1, w - 40), max(1, h - 4))
        hist = _colours(img, label, 1)
        if not hist:
            continue
        plate = max(hist, key=hist.get)
        inks = _inks(img, label, plate)
        got = inks["text"] or inks["line"]
        if got:
            out.append(("bar chip label vs plate", _hex(got[0]), _hex(plate),
                        contrast(got[0], plate), TEXT_FLOOR, ""))
    return out


def _measure(pair, got, want, ok):
    """A geometry pair as an audit row: what was measured against what it
    must reach, in pixels (the ratio column 1.0 where it holds). A pair
    with a known, filed gap carries it in the owner column (KNOWN)."""
    return (pair, "%dpx" % got, "%dpx" % want, 1.0 if ok else 0.0, 1.0,
            "" if ok else KNOWN.get(pair, ""))


def _mark_pairs(ws, img, box, view, kind=None):
    """The focused row's mark covers the row (design review R3-03, R3-14):
    the focus colour's run at the row's leading edge spans the row's height
    as it shows, a field's row included."""
    from runtime import ui_draw
    if not ui_draw.marks_on() or ui_draw.input_kind() == "touch" or kind == "card":
        return []           # touch shows no focus; a card wears a card's mark
    r = _clip(box, view) if view is not None else box
    if r is None:
        return []
    th = ws.theme_colors
    focus = _token_rgbs(ws, ("focus",))
    px = img.load()
    reach = max(2, ui_draw.mark_reach("part"))
    # the row's leading edge: where an edge, a bar beside a row, a ring and
    # a plate all lie, and a field's own ring inside the row does not
    x0, x1 = max(0, r[0] - reach), min(img.width, r[0] + reach + 4)
    y0, y1 = max(0, r[1] - reach), min(img.height, r[1] + r[3] + reach)
    ys = [yy for yy in range(y0, y1) if any(px[xx, yy] in focus for xx in range(x0, x1))]
    if th.get("focus") is None or not focus:
        return []
    want = r[3] - 4             # a ring inset inside a ring (Contrast Light) the slack
    got = (max(ys) - min(ys) + 1) if ys else 0
    return [_measure("focus mark height vs row", got, want, got >= want)]


def _editor_origin(ws, attr, own):
    """(the Editor's `attr` layout, the glass x and y of its content): its
    desk window's where one is open, else `own`, the fullscreen one."""
    win = getattr(ws.wm, "_wins", {}).get("menu")
    if win is not None and not getattr(win, "minimized", False) and ws.wm.desk_open():
        cx, cy, _w, _h = win.content_rect()
        return getattr(win.ctx, attr), cx, cy
    return own, 0, 0


def _ink_left(px, x0, x1, y0, y1, ground=None):
    """The first column in [x0, x1) where a row of [y0, y1) leaves its
    ground (the row's pixel at x0, or `ground`), or None."""
    for x in range(x0, x1):
        for y in range(y0, y1):
            if px[x, y] != (px[x0, y] if ground is None else ground):
                return x
    return None


def _code_pairs(ws, img):
    """The Code tab's one inset (design review R3-10): the status line and
    the symbol keys start on the layout's `x0`, a line's number right-aligned
    in the gutter that starts there and its code on the text column after
    it (D-03.1), a bearing's pixel aside; the worst of them is the measure."""
    if ws.screen != "menu" or getattr(ws, "menu_view", None) != "code":
        return []
    lay, ox, oy = _editor_origin(ws, "code_layout", ws.code_layout)
    ed = ws.editor
    fs, x0 = lay.fs, lay.x0
    cl = ws.code_layer
    gc = cl._gutter_cols(ed)
    tx0 = cl._text_x0(lay, ed)
    px = img.load()
    edges = []
    for i in range(lay.rows):
        n = ed.top + i
        if n != ed.row and n < len(ed.lines) and ed.lines[n][:1].isalpha():
            y = oy + lay.y0 + i * lay.lh
            if gc:
                at = x0 + (gc - 1 - len(str(n + 1))) * lay.cell
                e = _ink_left(px, ox + lay.cx + 1, ox + lay.cx + lay.cw, y, y + lay.lh)
                edges.append((e, at))
            e = _ink_left(px, ox + tx0 - lay.cell // 2, ox + lay.cx + lay.cw, y, y + lay.lh)
            edges.append((e, tx0))
            break
    sx, sy, _sw, sh = lay.status_band
    edges.append((_ink_left(px, ox + sx + 1, ox + sx + 200, oy + sy + 1, oy + sy + sh), x0))
    if getattr(lay, "sym_h", 0):
        ky = oy + lay.sym_y + lay.sym_h // 2
        edges.append((_ink_left(px, ox + lay.cx + 1, ox + lay.cx + lay.cw, ky, ky + 1), x0))
    off = [abs(e - ox - want) if not (want <= e - ox <= want + fs) else 0
           for e, want in edges if e is not None]
    worst = max(off) if off else 0
    return [_measure("code left edges off the inset", worst, 0, worst == 0)]


def _caret_pairs(ws, probe, img):
    """A ground column stands between the code caret and the next stem
    (design review R3-24), across the caret's middle third: the caret is no
    bold stroke (a glyph's foot, an underscore, may meet it)."""
    t = getattr(ws.code_layer, "_t", None)
    if ws.screen != "menu" or t is None or "caret" not in t:
        return []
    px = img.load()
    lay, ox, _oy = _editor_origin(ws, "code_layout", ws.code_layout)
    for x, y, w, h, c in probe.last["rects"]:
        # the caret: in the code's columns (a current-line mark in its colour
        # stands in the inset)
        if c == t["caret"] and h > 2 * w and ox + lay.x0 <= x and x + w < img.width:
            col = x + w
            ys = range(y + h // 3, y + h - h // 3)    # a stem's, not an underscore's
            ground = px[col, y + h // 2]
            gap = 1 if all(px[col, yy] == ground for yy in ys) and                 all(px[col, yy] != px[x, yy] for yy in ys) else 0
            return [_measure("caret to next stem gap", gap, 1, gap >= 1)]
    return []


def _sprite_pairs(ws, img):
    """The sprite tools (design review R3-11; the one row of 2026-10-19):
    every enabled tool shows the chrome's ink and none of the dim or danger
    inks (the tool in use wears the current mark, an idle stamp the disabled
    ink), and the row keeps the mockup's spacing, one pitch from button to
    button."""
    if ws.screen != "menu" or getattr(ws, "menu_view", None) != "paint" or ws.paint is None:
        return []
    from runtime import paint_layer, ui_draw
    lay, ox, oy = _editor_origin(ws, "paint_layout", ws.paint_layer.layout)
    th = ws.theme_colors
    px = img.load()
    ink = _index_rgb(ws, th["chrome_ink"])
    others = {_index_rgb(ws, th[r]) for r in ("ink_dim", "chrome_ink_dim")}
    others.add(_index_rgb(ws, ui_draw.button_ink(th, "danger")))
    others.discard(ink)
    bad = 0
    for tid, (x, y, w, h) in zip(paint_layer._TOOLS, lay.tool_btns):
        if tid == "stamp" or ws.paint_layer._tool_on(ws.paint, tid):
            continue
        seen = {px[ox + xx, oy + yy] for yy in range(y, y + h) for xx in range(x, x + w)
                if 0 <= ox + xx < img.width and 0 <= oy + yy < img.height}
        if ink not in seen or seen & others:
            bad += 1
    row = lay.tool_btns
    pitch = 48 if lay.desk else 24 * lay.fs
    off = sum(1 for a, b in zip(row, row[1:]) if b[0] - a[0] != pitch)
    return [_measure("sprite tools off the chrome ink", bad, 0, bad == 0),
            _measure("sprite tool row off the mockup's pitch", off, 0, off == 0)]


def _foot_pairs(ws, img):
    """The desk Appearance window's foot (design review R3-27): its status
    starts on the rows' label column and stands as far below the foot's
    rule as above the frame's bottom (within the face's 3 px), no more than
    17 px up."""
    win = getattr(ws.wm, "_wins", {}).get("appearance")
    if win is None or getattr(win, "minimized", False) or not ws.wm.desk_open():
        return []
    from runtime import ui_draw
    lay = win.ctx.app_layouts.get("appearance")
    if lay is None or not hasattr(lay, "foot"):
        return []
    cx, cy, _cw, _ch = win.content_rect()
    px = img.load()
    bx, _by, bw, _bh = lay.body
    _fx, fy, _fw, _fh = lay.foot
    ground = px[cx + bx + bw // 2, cy + fy + 4]
    rule = cy + fy
    bottom = win.y + win.h - 1 - win.edge
    cols = range(cx + bx, cx + bx + bw // 2)
    rows = [y for y in range(rule + 1, bottom) if any(px[x, y] != ground for x in cols)]
    if not rows:
        return []
    above, below = rows[0] - rule - 1, bottom - rows[-1] - 1
    pad = ui_draw.metrics("group")[1] * lay.fs
    x0 = _ink_left(px, cx + bx, cx + bx + bw // 2, rows[0], rows[-1] + 1, ground)
    dx = (x0 - (cx + bx + pad)) if x0 is not None else bw
    return [_measure("desk Appearance foot off the rows' column", abs(dx), 0,
                     0 <= dx <= lay.fs),
            _measure("desk Appearance foot gaps apart", abs(above - below), 3,
                     abs(above - below) <= 3 and below <= 17)]


def _caption_pairs(ws, img):
    """No section caption is cut at the top of Settings' page (design
    review R3-14): a header is wholly in or wholly out, so no caption ink
    (`chrome_ink_dim`/`ink_dim`) lies on the page's first two rows, in the
    left third where captions start."""
    if ws.screen != "settings" or ws.settings_layer.has_page():
        return []
    lay = ws.layout
    inks = _token_rgbs(ws, ("chrome_ink_dim", "ink_dim"))
    px = img.load()
    got = sum(1 for y in (lay.cy, lay.cy + 1) for x in range(lay.cx, lay.cx + lay.cw // 3)
              if px[x, y] in inks)
    return [_measure("caption cut at the page top", got, 0, got == 0)]


def _rim_pairs(ws, probe, img):
    """A lifted card's rim hugs its cover (design review R3-05): between
    the hero's cover and its rim no run of the card's plate (Moy's lift)."""
    look = _theme_look(ws)
    if (look.get("focus") or {}).get("card") != "lift" or ws.screen != "launcher" \
            or ws.sysmenu.open or ws.pause.open:
        return []           # a menu over the home takes the hero's lift
    covers = probe.last["covers"]
    if not covers:
        return []
    dx, dy, dw, dh = max(covers, key=lambda c: c[2] * c[3])
    px = img.load()
    plates = _token_rgbs(ws, ("surface", "tile_fill"))
    y = dy + dh // 2
    gap = 0
    while dx - 1 - gap >= 0 and px[dx - 1 - gap, y] in plates and gap < 16:
        gap += 1
    return [_measure("hero rim-to-cover gap", gap, 0, gap == 0)]


def _calc_pairs(ws, img):
    """The desk Calc's backspace key draws its sign as tall as a digit's
    cap less a pixel (design review R3-09)."""
    win = getattr(ws.wm, "_wins", {}).get("calc")
    app = ws._apps_by_id.get("calc") if hasattr(ws, "_apps_by_id") else None
    app = getattr(app, "_inst", app)
    host = getattr(app, "_ui", None)
    if win is None or getattr(win, "minimized", False) or host is None:
        return []
    cx, cy, _w, _h = win.content_rect()

    def ink_h(stop):
        k = host.box(stop)          # the key's stop, in the window's content
        if k is None:
            return None
        r = (k[0] + cx + 3, k[1] + cy + 3, k[2] - 6, k[3] - 6)
        hist = _colours(img, r, 1)
        if not hist:
            return None
        plate = max(hist, key=hist.get)
        px = img.load()
        ys = [yy for yy in range(r[1], r[1] + r[3])
              if any(_diff(px[xx, yy], plate) > 96 for xx in range(r[0], r[0] + r[2]))]
        return (max(ys) - min(ys) + 1) if ys else None
    back, digit = ink_h("<"), ink_h("7")
    if back is None or digit is None:
        return []
    return [_measure("desk backspace ink height vs digit cap", back, digit - 1,
                     back >= digit - 1)]


def audit(ws, probe, img):
    """[(pair, a, b, ratio, floor, owner)] of one render: the focused row's
    parts against its plate and its mark against the row, the caret against
    its line, the open tab's current mark against the ground beside it, the
    focused desk chip's label, a disabled history icon's ink against the
    enabled ink, an Off toggle's row in Settings, a lifted hero's rim and
    the desk Calc's backspace. A row an open design question owns names it
    in `owner`; the rest under their floor are the FLAGs."""
    th = ws.theme_colors
    out = []
    hinted = bool(probe.last["hints"])
    box = _focused_box(ws)
    if box is not None:
        view = _viewport(ws, box, hinted)
        kind = _FOCUSED_KIND[0]() if _FOCUSED_KIND[0] is not None else None
        out += _row_pairs(ws, img, box, "focused row part vs plate", kind, view)
        out += _mark_pairs(ws, img, box, view, kind)
    sl = ws.settings_layer
    if ws.screen == "settings":
        for stop, kind in sl._kinds.items():
            if kind == "toggle" and "fps" in stop.lower() and stop != sl._focused:
                b = sl._host.box(stop)
                if b is not None:
                    out += _row_pairs(ws, img, b, "Off toggle vs row ground", kind,
                                      _viewport(ws, b, hinted))
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
                                contrast(caret, ground), FLOOR, ""))
    out += _current_pairs(ws, probe, img)
    out += _chip_pairs(ws, probe, img)
    out += _rim_pairs(ws, probe, img)
    out += _caption_pairs(ws, img)
    out += _code_pairs(ws, img)
    out += _caret_pairs(ws, probe, img)
    out += _sprite_pairs(ws, img)
    out += _foot_pairs(ws, img)
    out += _calc_pairs(ws, img)
    enabled = th.get("chrome_ink")
    seen = set()
    for kind, ink in probe.last["icons"]:
        if kind in ("undo", "redo") and ink != enabled and kind not in seen \
                and enabled is not None:
            seen.add(kind)
            a, b = _index_rgb(ws, ink), _index_rgb(ws, enabled)
            out.append(("disabled %s vs enabled ink" % kind, _hex(a), _hex(b),
                        contrast(a, b), DISABLED_FLOOR, ""))
    seen, kept = set(), []
    for row in out:
        if row[:3] not in seen:
            seen.add(row[:3])
            kept.append(row)
    return kept


def flagged(rows):
    """The audit rows under their floor that no open question owns."""
    return [r for r in rows if r[3] < r[4] and not r[5]]


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
    _AUDIT_COUNT[0] = (0, 0)
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
        from runtime import iconsets
        iconsets.reset()            # the sets' masks back out of the font rows


# The run's (FLAGs, owned pairs), summed as each render reports.
_AUDIT_COUNT = [(0, 0)]


def _report(out, name, report, audit_csv, fails, quiet):
    """The index lines of one render's checks and audit; its CSV with
    `audit_csv`; each failed check into `fails`."""
    lines = []
    flags = len(flagged(report["audit"]))
    owned = sum(1 for r in report["audit"] if r[3] < r[4] and r[5])
    _AUDIT_COUNT[0] = (_AUDIT_COUNT[0][0] + flags, _AUDIT_COUNT[0][1] + owned)
    for check, ok, detail in report["checks"]:
        lines.append("  check: %s %s (%s)" % (check, "ok" if ok else "FAILED", detail))
        if not ok:
            fails.append((name, check, detail))
            if not quiet:
                print("  FAILED %s: %s" % (check, detail))
    for pair, a, b, ratio, floor, owner in report["audit"]:
        if ratio < floor:
            lines.append("  audit: %s %s %s on %s %.2f:1 (floor %.1f)" % (
                "owned by " + owner if owner else "FLAG", pair, a, b, ratio, floor))
    if audit_csv:
        with open(os.path.join(out, name + ".audit.csv"), "w", encoding="utf-8") as f:
            f.write("scene,pair,colour,against,ratio,floor,flag,owner\n")
            for pair, a, b, ratio, floor, owner in report["audit"]:
                f.write("%s,%s,%s,%s,%.2f,%.1f,%s,%s\n" % (
                    name, pair, a, b, ratio, floor,
                    "FLAG" if ratio < floor and not owner else "", owner))
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
        profile = profile or KIT_INPUT.get(screen)
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
        if (profile or inputs or (HANDHELD_INPUT if config == HANDHELD else None)) == "tdeck":
            lines.append("  note: " + TDECK_NOTE)
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
    if args.audit:
        print("%d audit flags, %d pairs owned by open design questions or known gaps"
              % _AUDIT_COUNT[0])
    for name, check, detail in failed:
        print("FAILED %s: %s (%s)" % (name, check, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
