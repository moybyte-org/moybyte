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
Until then it is rendered as the nearest current screen and marked "not
built yet" with what is missing; a scene whose theme the shell does not hold
(the ricing themes, step 29) is listed and skipped, unless `--unheld` draws
it in the default theme (`DEFAULT`).

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
palette; the system menu and the pause menu over focused content, the
system menu over Make, over an open cart of the kid's own and with Sleep
offered (a stub idle ladder), each checked to open on a row that destroys
nothing; the home and the console's home with Wi-Fi off; the desk with
Calc, and Files alone; the delete's question over Make and over the desk's
PROJECTS window, checked to open on Cancel; a long press on a Games and a Make card and a pad's X (the
card's verbs as a menu) and up from the desk home's shelf, each under the
input it is made with (`KIT_INPUT`) and each checked to have opened what it
opens. `--profiles` adds the hint-bearing screens once per input profile,
the Make tab, the Name field being typed, the Wi-Fi password prompt and the
home's search field among them; a scene drawn with the T-Deck's keys notes that the host cannot prove
its keyboard's keys. They are written into OUT/kit/ with their own
index.txt; `--only-kit` skips the mockup pairs.

Every render is checked, and a failed check fails the run (exit 1): the
handheld hero's focus mark lies under the bar, every cover a home draws is
the whole 128 px cover at a clean scale, a menu over content leaves one focus
mark on the screen, an open tab's pill has its ink padded alike both sides,
and a theme's bar packs alike in every mode the run drew it in. A kit
scene draws the keys of the run's `--input`, else the T-Deck's on the
handheld and the keyboard's on the desk, and its index says which. `--audit`
writes SCENE.audit.csv beside each render: the state pairs the review rounds
measured by hand (a focused row's parts against its plate and its mark
against the row, the caret against its line, the open tab's current mark
against the ground beside it, the focused desk chip's label, a disabled
history icon against the enabled ink, an Off toggle against its row, a lifted
hero's rim against its cover, a Settings caption cut at the page top, the
Code tab's left edges against its inset and its caret against the next stem,
the sprite tools' ink and columns, the desk Appearance foot, the desk Calc's
backspace against a digit, a menu's card against its anchor and the edge of
the glass it takes, its danger rows' words and the hint strip's ink under
it and under the PROJECTS window's, the sprite selection's ring against the
grid's lines, and rows showing under every fade where a list runs on, the
row a fade lies over reading where the fade begins),
each with its ratio and a FLAG under its floor. A box is measured as it
shows -- cut to its window or the content under the bar, less the hint band
-- inside its plate's own run; anti-aliasing is no colour of its own; text
is held to 4.5:1, a part to 3:1, the structure ramp's hairlines
(`STRUCTURE_TOKENS`) to 1.5:1 and an Off or a rest track to 1.6:1 (D-05);
a toggle's knob is measured on its track; a current
mark by the look's `current.style`, a faint plate passing on its label's
ink; a swatch only has to show, and a preview card is measured by its ring.
A measure with a known, filed gap (`KNOWN`) names it in the CSV's owner
column and is no FLAG; the run and each index.txt count the FLAGs and the
known gaps.
"""

import argparse
import fnmatch
import os
import re
import json
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
    editor = {"handheld_%s" % (k if "_" in k else k + "-editor"):
              (HANDHELD, ("16", "23", "30"), False, _tab(t), "nearest screen: the Editor's %s tab" % t)
              for k, t in EDITOR_TABS.items()}
    editor["handheld_sprite-editor_more"] = (HANDHELD, ("16", "23", "24", "30"), False, _sprites_more,
                                             "the sprite tab's more menu, which has no flags row")
    editor.update({"desktop_editor-%s" % k: (DESK, ("9", "23", "30"), False, _tab(t),
                                             "nearest screen: the Editor window, one tab "
                                             "(%s)" % t)
                   for k, t in EDITOR_LAYOUTS.items()})
    editor.update({"desktop_editor-%s" % k: (DESK, ("9", "23", "30"), False, _workspace(*w),
                                             "the desk Editor on Sky Run, workspace %s%s"
                                             % (w[0], ", full screen" if w[1] else ""))
                   for k, w in EDITOR_WORKSPACES.items()})
    editor.update({"desktop_files-max-%s" % v: (DESK, ("21", "25", "30"), False, _desk,
                                               "nearest screen: the desk, Files behind Calc "
                                               "(no maximised Files)")
                   for v in ("e05", "v1", "v2")})
    editor["editor_maximise_compare"] = (DESK, ("25", "30"), False, _tab("paint"),
                                         "nearest screen: the Editor window, one tab (paint)")
    table = {
        **editor,
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
        "handheld_code-editor": (HANDHELD, ("3", "4", "16", "23", "30"), False,
                                 _code, "the Code tab on Star Catcher"),
        "handheld_sprite-editor": (HANDHELD, ("16", "23", "24", "30"), False,
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
    table.update(_apps_table())
    table.update(_about_table())
    table.update(_old_look_table())
    return table


# Achievements and About (plan step 32, rounds/round16.py): each scene's
# screen -> (config, set up). The badges found are round 16's sets, the days
# its two; About's version, board and storage are the targets' (a stub
# updater and the board's `moy_ota.BOARD`).
S3 = "guition_tapfloor_dark"
_FOUND = {
    "empty": (),
    "few": ("first_open", "first_run", "first_paint"),
    "many": ("first_open", "first_run", "first_paint", "first_map", "first_code", "play_five",
             "toolbox", "konami", "clock_tinker"),
    "all": ("first_open", "first_run", "first_paint", "first_map", "first_code", "play_five",
            "toolbox", "decorator", "konami", "clock_tinker", "secret_door"),
}
_FOUND_ON = {"first_map": 20261012, "play_five": 20261009}
_BOARD_OF = {HANDHELD: "tdeck", S3: "guition_s3", DESK: "p4", CONSOLE: "p4"}
_FAKE_OTA = [False]


class _Updater:
    """The targets' firmware: 0.8 on Stable, nothing to install from a card."""

    def __init__(self, offered=None):
        self.offered = offered

    def version_label(self):
        return "0.8"

    def channel(self):
        return "stable"

    def available(self):
        return False


class _Keep:
    def free(self):
        return (1300000,)                 # 1.2 MB free


def _badges(ws, state):
    ws.ach.unlocked = {k: _FOUND_ON.get(k, 20261001) for k in _FOUND[state]}
    ws.ach.toast = None


def _board(ws, config):
    import types
    sys.modules["moy_ota"] = types.SimpleNamespace(BOARD=_BOARD_OF[config])
    _FAKE_OTA[0] = True
    ws.updater = _Updater()
    ws.cart_keep = _Keep()
    ws.system.set("ota_channel", "stable")
    if config == S3:
        ws.use_inputs([("local", "touch")])     # the Guition's glass: no hint strip


def _achievements(config, state, focus, pop=False, scroll=False):
    def setup(g, ws):
        _badges(ws, state)
        _board(ws, config)
        ws.open_settings()
        if config in (DESK,):
            ws.open_desk()
            ws.open_settings()
            _settle(g, ws, 2)
            _place(ws, "settings", 226, 52, 680, 536)
        sl = ws.settings_layer
        sl.open_page("achievements")
        page = sl.achievements
        page._focus = "Badge " + focus
        _settle(g, ws, 3)
        if pop:
            page._open(page._by_id["Badge " + focus])
    return setup


def _unlock(config, badge, game=False, secret=False):
    def setup(g, ws):
        _badges(ws, "many" if not secret else "few")
        ws.ach.unlocked.pop(badge, None)
        if game:
            ws.go_home()
            if getattr(ws.wm, "mode", None) is not None:
                ws.open_library()
            ws._open_workspace(g._cart_by_title(ws, "Star Catcher"))
            ws.run(ws.project, ws.launcher_layer)
            for _ in range(PAUSE_FRAMES):
                g._quiesce(ws)
                ws.frame(g._DT)
        elif config == HANDHELD:
            _home(g, ws)
        elif config == S3:
            _board(ws, config)
            _settings_about(g, ws)
        else:
            _desk(g, ws)
        _settle(g, ws, 2)
        ws._golden_toast = True
        ws.ach.award(badge)
        ws._toast_at -= 10000             # the confetti burst out whole
        ws._toast_until += 10000
    return setup


def _settings_about(g, ws):
    ws.open_settings()
    ws.settings_layer._want = "Licences"     # the page scrolled to its end
    _settle(g, ws, 3)
    ws.settings_layer._want = "Achievements"


def _settings_about_at(config):
    def setup(g, ws):
        _badges(ws, "many")
        _board(ws, config)
        _settings_about(g, ws)
    return setup


def _about(config, update=False):
    def setup(g, ws):
        _badges(ws, "many")
        _board(ws, config)
        if update:
            ws.update_ui.offered = "0.9"
        if config == HANDHELD:
            _home(g, ws)
        elif config == S3:
            _settings_about(g, ws)
        else:
            _desk(g, ws)
        _settle(g, ws, 2)
        ws._golden_about = True
        ws.menu_ui._menu_about()
    return setup


def _licences(config, entry=None, focus="The console"):
    def setup(g, ws):
        _board(ws, config)
        if config == DESK:
            ws.open_desk()
            ws.open_settings()
            _settle(g, ws, 2)
            _place(ws, "settings", 226, 52, 680, 536)
        else:
            ws.open_settings()
        sl = ws.settings_layer
        sl.open_page("licences")
        sl.licences._focus = focus
        if entry is not None:
            sl.licences.entry = sl.licences._parts() and sl.licences._ids[entry]
    return setup


ABOUT = {
    "handheld_achievements-empty": (HANDHELD, _achievements(HANDHELD, "empty", "first_open")),
    "handheld_achievements-few": (HANDHELD, _achievements(HANDHELD, "few", "first_paint")),
    "handheld_achievements": (HANDHELD, _achievements(HANDHELD, "many", "first_run")),
    "handheld_achievements-scrolled": (HANDHELD, _achievements(HANDHELD, "many", "decorator")),
    "handheld_achievements-all": (HANDHELD, _achievements(HANDHELD, "all", "first_open")),
    "handheld_achievement-popover": (HANDHELD, _achievements(HANDHELD, "many", "first_map",
                                                             pop=True)),
    "handheld_achievement-unlocked": (HANDHELD, _unlock(HANDHELD, "first_map")),
    "handheld_achievement-unlocked-game": (HANDHELD, _unlock(HANDHELD, "first_run", game=True)),
    "handheld_achievement-secret": (HANDHELD, _unlock(HANDHELD, "konami", secret=True)),
    "handheld_settings-about": (HANDHELD, _settings_about_at(HANDHELD)),
    "handheld_about": (HANDHELD, _about(HANDHELD)),
    "handheld_about-update": (HANDHELD, _about(HANDHELD, True)),
    "handheld_licences": (HANDHELD, _licences(HANDHELD)),
    "handheld_licence": (HANDHELD, _licences(HANDHELD, "MicroPython")),
    "handheld_achievements-s3": (S3, _achievements(S3, "many", "first_run")),
    "handheld_achievement-unlocked-s3": (S3, _unlock(S3, "first_map")),
    "handheld_settings-about-s3": (S3, _settings_about_at(S3)),
    "handheld_about-s3": (S3, _about(S3)),
    "desktop_achievements": (DESK, _achievements(DESK, "many", "play_five")),
    "desktop_achievement-popover": (DESK, _achievements(DESK, "many", "decorator", pop=True)),
    "desktop_achievement-unlocked": (DESK, _unlock(DESK, "first_map")),
    "desktop_achievement-unlocked-game": (CONSOLE, _unlock(CONSOLE, "first_run", game=True)),
    "desktop_about": (DESK, _about(DESK)),
    "desktop_about-update": (DESK, _about(DESK, True)),
    "desktop_licences": (DESK, _licences(DESK, focus="MicroPython")),
}


def _about_table():
    return {screen: (config, ("32",), False, setup,
                     "Achievements and About as built (step 32)")
            for screen, (config, setup) in ABOUT.items()}



# The old look's last surfaces (plan step 33, rounds/round17.py): each
# scene's screen -> (config, the screen it is drawn nearest today). The splash,
# the loading pill, the popovers, a stopped run's card and the prompts are
# frames the goldens' quiesce never holds, so each pairs with the screen it
# opens from or over; the kernel's recovery and web screens draw before the
# shell exists, so theirs is the screen the console starts on.
_HH = ("splash", "splash-boot", "loading", "calendar", "power", "wifi", "notice", "text-console",
       "text-console-done", "home-grid", "home-icons", "home-icons-pop", "home-icons-labels",
       "saver", "home-fallback", "recovery", "kernel-web", "kernel-web-restarting")
_HH_CODE = ("crash-code", "code-find", "code-complete", "code-goto", "code-files")
_HH_RUN = ("crash", "crash-compiled", "perf-hud", "perf-hud-detail")
_HH_CARDS = ("cart-info", "new-script", "project-files")
_HH_SET = ("web-console", "settings-fallback", "theme-door", "theme-config", "theme-sprites",
           "theme-sprites-edited")
_S3 = ("splash-s3", "calendar-s3", "power-s3", "crash-s3", "blocks-number-s3", "web-console-s3",
       "home-grid-s3", "tool-bar-s3", "saver-s3", "recovery-s3", "home-icons-s3",
       "home-icons-pop-s3", "home-icons-labels-s3", "theme-config-s3", "theme-sprites-s3")
_DK = ("splash", "loading", "calendar", "power", "wifi", "web-console", "text-console",
       "theme-editor", "fallback", "recovery", "kernel-web")
_DK_CODE = ("crash-code", "code-find", "code-complete", "code-goto")
_DK_CONSOLE = ("crash", "perf-hud", "home-grid", "tool-bar", "home-icons", "home-icons-pop",
               "home-icons-caption")


def _popover(widget, under=None):
    def setup(g, ws):
        (under or _home)(g, ws)
        ws.widget_tap(widget)
        ws._golden_popover = True
    return setup


_STOP_ERROR = "TypeError: unsupported types for __mul__: 'NoneType', 'float'"


def _loading(g, ws):
    """The frame an open is asked for, held: a queued open that queues
    itself again, so every settled frame is the pill's."""
    _home(g, ws)

    def again():
        ws.defer(again)
    ws.defer(again)


def _stopped(compiled=False):
    """Star Catcher stopped mid-frame on line 42 (the targets' error)."""
    def setup(g, ws):
        ws.go_home()
        if getattr(ws.wm, "mode", None) is not None:
            ws.open_library()
        ws._open_workspace(g._cart_by_title(ws, "Star Catcher"))
        ws.run(ws.project, ws.launcher_layer)
        for _ in range(PAUSE_FRAMES):
            g._quiesce(ws)
            ws.frame(g._DT)
        if compiled:
            ws.cart["runtime"] = "wasm"
        ws.player._capture_frame_error(ws, TypeError(_STOP_ERROR[11:]))
        ws.player.crash_line = 42
    return setup


def _notice(g, ws):
    """Sky Run refused for a name this console lacks, over the home."""
    from runtime import chrome_ops
    _home(g, ws)
    ws._open_workspace(g._cart_by_title(ws, "Sky Run"))
    p = ws.player
    p._notice = p.cart_error = chrome_ops.newer_text(["tilemap2"])
    p._notice_title = chrome_ops.say_title(chrome_ops.SAY_NEWER)
    ws.run(ws.project, ws.launcher_layer)


def _see_why(g, ws):
    _stopped()(g, ws)
    _settle(g, ws, 2)
    ws.pause.choose_row(ws.pause.SEE_WHY)
    ed = ws.editor
    if ed is not None:
        ed.top = 37


def _blocks_prompt(kind):
    def setup(g, ws):
        _tab("blocks")(g, ws)
        _settle(g, ws, 2)
        ui = ws.block_ui
        if kind == "num":
            ui._blk_open_kbd("num", cur=10, block={"t": "move", "p": {}}, slot="move_by",
                             allow_block=True)
        else:
            ui._blk_open_kbd("var", var="var1")
            ui.blk_kbd["entry"].text = "score"
    return setup


def _cards_prompt(which):
    def setup(g, ws):
        _tab("cards")(g, ws)
        _settle(g, ws, 2)
        cl = ws.cards_layer
        if which == "meta":
            cl._open_meta()
        elif which == "newf":
            cl._open_newf()
        else:
            cl._open_files()
    return setup


def _saver(g, ws):
    _home(g, ws)
    g._quiesce(ws)
    ws.saver_state(True)
    games = [c for c in ws.carts.all if c.get("type") == "game"] or ws.carts.all
    titles = [c.get("title") for c in games]
    ws._saver._i = titles.index("Star Catcher") - 1 if "Star Catcher" in titles else -1
    ws._saver.frame(0)


def _web(g, ws):
    _settings(g, ws)
    ws.system.set("web_pin", "4821")
    if ws.webhost is None:
        ws.webhost = _LookWebHost()
    if not ws.webhost_serving():
        ws.toggle_webhost()


class _LookWebHost:
    """The web console's server as the targets show it, on 192.168.1.42."""

    serving = False
    error = None

    def start(self):
        self.serving = True

    def stop(self):
        self.serving = False

    def url(self):
        return "http://192.168.1.42/"

    def paired_url(self):
        return "http://192.168.1.42/?pin=4821"


_SCRIPT = ("print(\"Hello! I'm Ada's first script.\")\n"
           "def _update(dt):\n"
           "    name = input('What is your name? ')\n")


def _script(done=False):
    def setup(g, ws):
        _home(g, ws)
        ws.carts_store.save_file("docs", "first.py", "print('Hi')\n" if done else _SCRIPT,
                                 ws.carts_root)
        ws.run_script("docs", "first.py")
    return setup


def _perf(detail):
    def setup(g, ws):
        _pause(g, ws)
        ws.pause.close()
        frame = ws.frame

        def held(dt):
            # the HUD's numbers, set after the goldens' quiesce turns it off
            ws.show_fps = True
            ws.perf_hud = detail
            ws._fps, ws._flush_ms, ws._draw_ms, ws._upd_ms = 30.0, 9.6, 19.3, 5.1
            return frame(dt)
        ws.frame = held
    return setup


def _code_overlay(which):
    """The Code tab on Star Catcher with one of its overlays open."""
    def setup(g, ws):
        _code(g, ws)
        _settle(g, ws, 2)
        cl, ed = ws.code_layer, ws.editor
        if which == "find":
            ed.row, ed.col = 34, 31
            cl._toggle_find()
            cl._find_q = "speed"
        elif which == "complete":
            ed.lines[41] = "    s[2] = _ba"
            ed.row, ed.col = 41, len(ed.lines[41])
            cl._open_completion(ed)
        elif which == "goto":
            cl._open_jump(ed)
        else:
            cl.open_scripts()
    return setup


# A tool cart on the game canvas: the targets' egg timer, its picture
# under the bar's 18 rows.
EGG_TIMER = """def _draw():
    cls(col("dark_blue"))
    circ(160, 60, 16, col("white"))
    print("03:20", 160 - 40, 110, col("yellow"), 4)
    print("A START   B RESET", 160 - 68, 164, col("light_grey"))
"""


def _tool_cart(g, ws):
    """Egg Timer, a tool cart, running."""
    from runtime import moy_catalogue
    if getattr(ws.wm, "mode", None) == "console" or ws.wm.desk_open():
        _console_home(g, ws)
    else:
        _home(g, ws)
    made = moy_catalogue.create("Egg Timer", ws.carts_root, src=EGG_TIMER, type="tool")
    ws.carts.rescan()
    ws._open_workspace(next(c for c in ws.carts.all if c["path"] == made["path"]))
    ws.run(ws.project, ws.launcher_layer)


# The Editor's theme mode (GAPS 56, rounds/round17.py's theme_*): the door,
# Config on the copy just made (its caret on line 4, column 24) or on the
# file once saved, Sprites on Home with Play already in pixels and the
# cursor on column 6, row 3, and Home painted there in red.
THEME_CUR = (6, 3)


def _theme_door(g, ws):
    ws.open_app(ws._apps_by_id["appearance"])
    _app(ws, "appearance").show_row("Edit theme\u2026")


def _theme(tab, copied=True, edited=False):
    def setup(g, ws):
        desk = getattr(ws.wm, "mode", None) == "desktop"
        if desk:
            ws.open_desk()
        ws.open_app(ws._apps_by_id["appearance"])
        _settle(g, ws, 2)
        assert ws.look.edit_file()
        doc = ws.editor_app.theme
        if not copied:
            doc.note = None
        if desk:
            _settle(g, ws, 2)
            win = ws.wm._wins["menu"]
            if win.full():
                ws.wm._toggle_max(win)
            dx, dy, dw, dh = ws.wm._desk_rect()
            _place(ws, "menu", dx, dy, dw, dh)
        play = _iconsets_slot("play")
        doc._set_tile(play, doc.seed(play))
        doc.flags[play] = 1
        ws.look.preview_pixels(doc.sheet, doc.flags)
        doc.cur[:] = THEME_CUR
        ed = doc.ed
        ed.row, ed.col = (11, 31) if desk else (3, 24)
        if edited:
            layer = ws.theme_sprites_layer
            layer._press(doc, THEME_CUR[0], THEME_CUR[1])
            layer._release(doc)
        ws.editor_app.theme_tab("theme_sprites" if tab == "sprites" else "theme_config")
        if desk:
            _settle(g, ws, 2)
    return setup


def _iconsets_slot(name):
    from runtime import iconsets
    return iconsets.SLOT[name]


# The scenes of step 33 the shell draws: screen -> (config, set up).
OLD_LOOK = {
    "handheld_theme-door": (HANDHELD, _theme_door),
    "handheld_theme-config": (HANDHELD, _theme("config")),
    "handheld_theme-sprites": (HANDHELD, _theme("sprites")),
    "handheld_theme-sprites-edited": (HANDHELD, _theme("sprites", edited=True)),
    "handheld_theme-config-s3": (S3, _theme("config", copied=False)),
    "handheld_theme-sprites-s3": (S3, _theme("sprites")),
    "desktop_theme-editor": (DESK, _theme("sprites", copied=False, edited=True)),
    "handheld_tool-bar-s3": (S3, _tool_cart),
    "desktop_tool-bar": (CONSOLE, _tool_cart),
    "handheld_code-find": (HANDHELD, _code_overlay("find")),
    "handheld_code-complete": (HANDHELD, _code_overlay("complete")),
    "handheld_code-goto": (HANDHELD, _code_overlay("goto")),
    "handheld_code-files": (HANDHELD, _code_overlay("files")),
    "handheld_loading": (HANDHELD, _loading),
    "desktop_loading": (DESK, _loading),
    "handheld_crash": (HANDHELD, _stopped()),
    "handheld_crash-compiled": (HANDHELD, _stopped(True)),
    "handheld_crash-s3": (S3, _stopped()),
    "desktop_crash": (CONSOLE, _stopped()),
    "handheld_notice": (HANDHELD, _notice),
    "handheld_crash-code": (HANDHELD, _see_why),
    "desktop_crash-code": (DESK, _see_why),
    "handheld_blocks-name": (HANDHELD, _blocks_prompt("var")),
    "handheld_blocks-number": (HANDHELD, _blocks_prompt("num")),
    "handheld_blocks-number-s3": (S3, _blocks_prompt("num")),
    "desktop_blocks-name": (DESK, _blocks_prompt("var")),
    "handheld_cart-info": (HANDHELD, _cards_prompt("meta")),
    "handheld_new-script": (HANDHELD, _cards_prompt("newf")),
    "handheld_project-files": (HANDHELD, _cards_prompt("files")),
    "desktop_cart-info": (DESK, _cards_prompt("meta")),
    "handheld_saver": (HANDHELD, _saver),
    "handheld_saver-s3": (S3, _saver),
    "handheld_web-console": (HANDHELD, _web),
    "handheld_web-console-s3": (S3, _web),
    "desktop_web-console": (DESK, _web),
    "handheld_text-console": (HANDHELD, _script()),
    "handheld_text-console-done": (HANDHELD, _script(True)),
    "desktop_text-console": (DESK, _script()),
    "handheld_perf-hud": (HANDHELD, _perf(False)),
    "handheld_perf-hud-detail": (HANDHELD, _perf(True)),
    "desktop_perf-hud": (CONSOLE, _perf(True)),
    "handheld_calendar": (HANDHELD, _popover("clock")),
    "handheld_power": (HANDHELD, _popover("battery")),
    "handheld_wifi": (HANDHELD, _popover("wifi")),
    "handheld_calendar-s3": (S3, _popover("clock", lambda g, ws: _settings(g, ws))),
    "handheld_power-s3": (S3, _popover("battery")),
    "desktop_calendar": (DESK, _popover("clock", lambda g, ws: _desk(g, ws))),
    "desktop_power": (DESK, _popover("battery", lambda g, ws: _desk(g, ws))),
    "desktop_wifi": (DESK, _popover("wifi", lambda g, ws: _desk(g, ws))),
}


def _old_look_table():
    near = {"home": (_home, "nearest screen: home"),
            "code": (_code, "nearest screen: the Code tab"),
            "run": (_pause, "nearest screen: the pause menu over Star Catcher"),
            "cards": (_tab("cards"), "nearest screen: the Editor's Make it mine tab"),
            "blocks": (_tab("blocks"), "nearest screen: the Editor's Blocks tab"),
            "settings": (_settings, "nearest screen: Settings"),
            "desk": (_desk, "nearest screen: the desk"),
            "console": (_console_home, "nearest screen: the console-mode home")}
    rows = [("handheld_%s" % k, HANDHELD, "home") for k in _HH]
    rows += [("handheld_%s" % k, HANDHELD, "code") for k in _HH_CODE]
    rows += [("handheld_%s" % k, HANDHELD, "run") for k in _HH_RUN]
    rows += [("handheld_%s" % k, HANDHELD, "cards") for k in _HH_CARDS]
    rows += [("handheld_%s" % k, HANDHELD, "blocks") for k in ("blocks-name", "blocks-number")]
    rows += [("handheld_%s" % k, HANDHELD, "settings") for k in _HH_SET]
    rows += [("handheld_%s" % k, S3, "settings") for k in _S3]
    rows += [("desktop_%s" % k, DESK, "desk") for k in _DK]
    rows += [("desktop_%s" % k, DESK, "code") for k in _DK_CODE]
    rows += [("desktop_%s" % k, CONSOLE, "console") for k in _DK_CONSOLE]
    rows += [("desktop_blocks-name", DESK, "blocks"), ("desktop_cart-info", DESK, "cards")]
    out = {}
    for screen, config, kind in rows:
        setup, what = near[kind]
        if screen in OLD_LOOK:
            config, setup = OLD_LOOK[screen]
            what = "the old look's last surfaces as built (step 33)"
        out[screen] = (config, ("33",), False, setup, what)
    return out

# The apps' restyle (docs/media/look_2026-10/APPS_RESTYLE.md, plan step 31):
# each scene's screen -> the app it is drawn from today, and the page of it.
APPS = {"paint": ("artwork", "main"), "paint-colours": ("artwork", "colours"),
        "paint-brush": ("artwork", "brush"), "paint-more": ("artwork", "more"),
        "paint-touch": ("artwork", "touch"), "paint-touch-colours": ("artwork", "touch-colours"),
        "paint-game": ("artwork", "game"), "my-art": ("myart", "grid"),
        "my-art-menu": ("myart", "menu"), "my-art-empty": ("myart", "empty"),
        "getcarts": ("getcarts", "grid"), "getcarts-cart": ("getcarts", "cart"),
        "getcarts-getting": ("getcarts", "getting"), "getcarts-offline": ("getcarts", "offline"),
        "notes": ("notes", "list"), "notes-edit": ("notes", "edit"), "notes-new": ("notes", "new"),
        "notes-empty": ("notes", "empty"), "paint-max": ("artwork", "main"),
        "paint-view": ("artwork", "view"), "paint-view-zoom": ("artwork", "view-zoom"),
        "paint-view-more": ("artwork", "view-more"), "storybook": ("storybook", "slide"),
        "storybook-empty": ("storybook", "empty"), "storybook-view": ("storybook", "view"),
        "storybook-deck": ("storybook", "deck"), "storybook-picture": ("storybook", "picture"),
        "storybook-slide": ("storybook", "nothing"), "storybook-max": ("storybook", "max"),
        "storybook-stories": ("storybook", "stories"), "storybook-add": ("storybook", "add"),
        "storybook-pictures": ("storybook", "pictures")}
DESK_APPS = ("paint", "paint-max", "my-art", "getcarts", "getcarts-getting", "notes", "storybook",
             "storybook-deck", "storybook-picture", "storybook-slide", "storybook-max",
             "storybook-stories", "storybook-add", "storybook-pictures")
DESK_ONLY = ("paint-max", "storybook-deck", "storybook-picture", "storybook-slide", "storybook-max",
             "storybook-stories", "storybook-add", "storybook-pictures")


def _apps_table():
    what = "nearest screen: %s as it draws today"
    out = {"storybook_present": (HANDHELD, ("31",), False,
                                 _app_open("storybook", "present"), what % "storybook"),
           "storybook_present_desk": (CONSOLE, ("31",), False,
                                      _app_open("storybook", "present"), what % "storybook")}
    for screen, (app, page) in APPS.items():
        if screen not in DESK_ONLY:
            out["handheld_" + screen] = (HANDHELD, ("31",), False, _app_open(app, page),
                                         what % app)
        if screen in DESK_APPS:
            out["desktop_" + screen] = (DESK, ("31",), False, _app_open(app, page, True),
                                        what % app)
    return out


def _app_open(app, page=None, window=False):
    """An app the restyle redraws, opened as it is today (Notes is a cart),
    on `page` (one of Paint's modes), in a window where the target has it."""
    def setup(g, ws):
        if window:
            ws.open_desk()
        if app == "notes":
            _notes(g, ws, "edit" if window else page, window)
            return
        if app == "getcarts":
            _getcarts(g, ws, page, window)
            return
        if app == "artwork":
            _paint(g, ws, page, window)
            return
        if app == "myart":
            _myart(g, ws, page, window)
            return
        if app == "storybook":
            _storybook(g, ws, page, window)
            return
        ws.open_app(ws._apps_by_id[app])
        _settle(g, ws, 2)
        if page is not None:
            _app(ws, app).mode = page
        if window:
            _place(ws, app, 112, 52, 900, 536)
    return setup


# The targets' shelf (rounds/round13.py's STORE): name, the seed cart whose
# cover it shows (None: none), its genre, where it stands, its size in KB.
_STORE = (("Pixel Pet", "pixel_pet", "toy", "installed", 40),
          ("Hop Quest", "hop_quest", "platformer", "update", 18),
          ("Bullet Storm", "bullet_storm", "shooter", "get", 96),
          ("Doom", None, "shooter", "too_big", 4100),
          ("Letter Blitz", "letter_blitz", "puzzle", "get", 22),
          ("Tiny Runner", "tiny_runner", "platformer", "noplay", 14),
          ("Star Catcher", "star_catcher", "arcade", "installed", 12),
          ("Harpoon Pop", "harpoon_pop", "arcade", "get", 31))


class _NoLink:
    """A radio with no network joined."""

    def status(self):
        return (False, None, None)

    def scan(self):
        return []

    def known(self):
        return []


class _Job:
    """A download caught at 62%: 11 KB of 18 KB at 140 KB/s."""
    finished = False
    keeping = False
    path = None
    error = None
    done = 11 * 1024
    total = 18 * 1024

    def step(self):
        return True

    def rate(self):
        return 140 * 1024

    def cancel(self):
        return True


def _getcarts(g, ws, state, window):
    """Get Carts over the targets' shelf, on `state`'s page."""
    from cart_store_fixtures import INDEX_URL, MemNet, Repo
    repo = Repo(INDEX_URL.rsplit("/", 1)[0], name="moybyte-org")
    for name, key, genre, _st, kb in _STORE:
        cover = None
        if key is not None:
            with open(os.path.join(ROOT, "system_carts", "moybyte.%s.moy" % key,
                                   "cover.png"), "rb") as f:
                cover = f.read()
        e = repo.add(name.lower().replace(" ", "_"), version=3, name=name, cover=cover,
                     main=b"\0" * (kb * 1024 - 400))
        e["genre"] = genre
        if name == "Hop Quest":
            e["description"] = ("Hop across the hills, grab every coin before the clouds "
                                "roll in, and find the flag at the end of the day.")
    repo.files["index.json"] = repo.index()
    net = MemNet(repo.routes())
    ws.cart_net = net
    app = ws._apps_by_id["getcarts"]
    app._inst.free = lambda: (3250585, 4096)
    if state == "offline":
        net.up = False
        ws.wifi = _NoLink()
    if window:
        ws.open_desk()
    ws.open_app(app)
    for _ in range(12):
        ws._dirty = True
        ws.frame(g._DT)
        if app.phase not in ("checking",):
            break
    for row in app.rows:
        st = [s for s in _STORE if s[0] == row["cart"]["name"]][0][3]
        if st == "too_big":
            row["fit"] = "Doom needs more memory than this console has."
        elif st == "noplay":
            row["runs"] = False
        elif st in ("installed", "update"):
            row["state"] = st
            row["had"] = 2 if st == "update" else None
    have = dict((r["cart"]["folder"], {"title": r["cart"]["name"]}) for r in app.rows
                if r["state"] in ("installed", "update"))
    app._inst.find = have.get
    order = [s[0] for s in _STORE]
    app.rows.sort(key=lambda r: order.index(r["cart"]["name"]))
    hop = [r for r in app.rows if r["cart"]["name"] == "Hop Quest"]
    hop = hop[0] if hop else None
    if hop is None:
        pass
    else:
        app._ui.reset()               # the shelf from its top, as a fresh open shows it
        app._focus = app._card_id(hop)
    if hop is None:
        pass
    elif state == "cart" and not window:
        app.cur = hop
        app._go("cart")
        app._focus_go()
    elif state == "getting":
        app.cur = hop
        app.job = _Job()
        app._go("getting")
        app.armed = True
    if window:
        _place(ws, "getcarts", 112, 52, 900, 536)
    _settle(g, ws, 4)


# The targets' stories (rounds/round15.py STORY, STORIES, PICTURES): The
# Brave Dragon's five slides, Space trip and Our holiday, and My Art's seven
# drawings, their art round15's own (rounds/round13.py's ART, round15's WIDE).
SB_STORIES = (("brave_dragon", "The Brave Dragon", None), ("space_trip", "Space trip", 2),
              ("our_holiday", "Our holiday", 3))
SB_DRAWINGS = (("dragon", "dragon", True), ("garden", "garden", True),
               ("my_house", "house", True), ("rocket", "rocket", False),
               ("ginger", "cat", False), ("fish_tank", "fish", False),
               ("night_sky", "night", False))


_ART = {}


def _round15_art():
    """rounds/round15.py's art as PIL images: {("wide", kind): 1024x600,
    ("art", kind): 320x240}, drawn once in a process of its own (the
    rounds' drawing modules take over sys.argv and the import system)."""
    if _ART:
        return _ART
    import subprocess
    from PIL import Image
    tmp = tempfile.mkdtemp(prefix="look_r15_")
    rounds = os.path.join(ROOT, "docs", "media", "look_2026-10", "rounds")
    code = ("import os, sys\nsys.path.insert(0, %r)\nout = %r\nimport round15 as R\n"
            "for k, im in R.WIDE.items():\n    im.save(os.path.join(out, 'wide_' + k + '.png'))\n"
            "for k, im in R.ART.items():\n    im.save(os.path.join(out, 'art_' + k + '.png'))\n"
            % (rounds, tmp))
    subprocess.run([sys.executable, "-c", code], check=True, cwd=rounds,
                   capture_output=True)
    try:
        for f in os.listdir(tmp):
            kind, name = f[:-4].split("_", 1)
            with Image.open(os.path.join(tmp, f)) as im:
                _ART[(kind, name)] = im.convert("RGB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _ART


def _indices(im):
    """A picture as MOY64's first sixteen, nearest each pixel."""
    from runtime import story_doc
    pal = [((c >> 16) & 255, (c >> 8) & 255, c & 255) for c in story_doc.COLOURS]
    im = im.convert("RGB")
    w, h = im.size
    px = im.load()
    out = bytearray(w * h)
    near = {}
    for y in range(h):
        for x in range(w):
            c = px[x, y]
            k = near.get(c)
            if k is None:
                k = min(range(16), key=lambda i: sum((a - b) ** 2 for a, b in zip(pal[i], c)))
                near[c] = k
            out[y * w + x] = k
    return w, h, bytes(out)


def _sb_story():
    """rounds/round15.py's STORY and TRANS: The Brave Dragon's slides, as
    the document's values (its makers' defaults filled in)."""
    import subprocess
    rounds = os.path.join(ROOT, "docs", "media", "look_2026-10", "rounds")
    code = ("import json, sys\nsys.path.insert(0, %r)\nimport round15 as R\n"
            "sys.stdout.write(json.dumps([list(R.STORY), list(R.TRANS)]))\n" % rounds)
    got = subprocess.run([sys.executable, "-c", code], check=True, cwd=rounds,
                         capture_output=True, text=True).stdout
    return json.loads(got[got.index("[["):])


def _sb_doc(slides, title):
    from runtime import story_doc
    out = []
    for (bg, objs), tr in slides:
        conv = []
        for o in objs:
            o = dict(o)
            if o["type"] == "shape":
                for k, v in (("flip", 0), ("text", ""), ("font", "sans_bold"), ("size", 48),
                             ("colour", 0), ("align", "centre")):
                    o.setdefault(k, v)
            if o["type"] == "picture":
                o["scale"] = 0.5 if o["scale"] == 0.5 else int(o["scale"])
            conv.append(o)
        out.append({"bg": bg, "transition": tr, "objects": conv})
    return {"format": story_doc.FORMAT, "title": title, "slides": out}


def _storybook(g, ws, state, window):
    """Storybook as the targets draw it: My Art's drawings and the three
    stories seeded (none for `empty`), on `state`'s page."""
    from runtime import moy_carts, story_doc, story_render
    root = ws.carts_root
    if state != "empty":
        r15 = _round15_art()
        art = {}
        t = 1700000000
        for i, (name, kind, wide) in enumerate(SB_DRAWINGS):
            w, h, pix = _indices(r15[("wide" if wide else "art", kind)])
            moy_carts.save_file("drawings", name, moy_carts.encode_moyimg(w, h, pix), root)
            path = moy_carts.file_path("drawings", name, root)
            os.utime(path, (t - 60 * i, t - 60 * i))
        for kind, name in r15:
            if kind == "wide":
                art[name] = _indices(r15[(kind, name)])
        slides, trans = _sb_story()
        story = list(zip(slides, trans))
        docs = {"brave_dragon": _sb_doc(story, "The Brave Dragon"),
                "space_trip": _sb_doc([story[2], story[2], story[0]], "Space trip"),
                "our_holiday": _sb_doc([story[3]] * 8, "Our holiday")}
        for i, (item, _title, _first) in enumerate(SB_STORIES):
            doc = docs[item]
            for img in story_doc.images_of(doc):
                w, h, pix = art[img]
                moy_carts.save_file("stories", item + "/" + img + ".moyimg",
                                    moy_carts.encode_moyimg(w, h, pix), root)
                moy_carts.save_file("stories", item + "/" + img + "_h.moyimg",
                                    moy_carts.encode_moyimg(w // 2, h // 2,
                                                            bytes(story_render.half(pix, w, h))),
                                    root)
            moy_carts.save_file("stories", item + "/story.json", story_doc.write(doc), root)
            d = moy_carts.file_path("stories", item, root)
            os.utime(d, (t - 60 * i, t - 60 * i))
    if not window:
        ws.pointer.visible = False
    app = ws._apps_by_id["storybook"]
    ws.open_app(app)
    _settle(g, ws, 2)
    if window:
        size = (1024, 600) if state == "max" else (900, 536)
        at = (0, 0) if state == "max" else (112, 52)
        _place(ws, "storybook", at[0], at[1], size[0], size[1])
        _settle(g, ws, 2)
    first = app.stories[0] if app.stories else None
    if not app.layout.desk and state not in ("view", "present", "empty"):
        state = "stories"                 # the handheld's Storybook: its stories
    if state in ("stories", None) or state == "empty" or first is None:
        if first is not None:
            app._focus = app._card_id(first["name"])
        _settle(g, ws, 3)
        return
    if state == "present":
        app._open_story(first, "deck" if app.layout.desk else "view")
        app._start(1)
        app.show["step"] = 1              # the bubble: its tap taken
        _settle(g, ws, 3)
        return
    if state == "view":
        app._open_story(first, "view")
        app.cur = 1
        _settle(g, ws, 3)
        return
    app._open_story(first, "deck")
    ed = app.ed
    ed.opened()
    if state == "deck":
        app.cur = 2
        app._focus = "Slide 2"
        _settle(g, ws, 3)
        return
    app.page = "slide"
    app.cur = 1
    if state == "pictures":
        ed._open_pictures("add")
        _settle(g, ws, 2)
        app._focus = "Pic dragon"
        _settle(g, ws, 3)
        return
    if state == "add":
        ed.menu = "add"
    elif state == "picture":
        ed.chosen = 0
        app.story.set(1, 0, "clear", True)
    elif state == "slide" or state is None:
        ed.chosen = 1
        ed.guides = (("h", 64),)
        ed.say = "moving: lined up with the dragon"
    elif state == "max":
        ed.chosen = 2
        ed.writing = True
    _settle(g, ws, 3)


# The targets' vault (rounds/round13.py NOTES), newest first, and the note
# the edit scenes open: a heading, checkboxes, a link and a drawing.
NOTES = ("Shopping list", "Story ideas", "Dragon facts", "todo.txt", "scores.json", "hello.py",
         "Party plan", "maze.lua")
STORY = ("# Story ideas\n\n- [x] A dragon who is scared of the dark\n"
         "- [ ] Moy finds a map in the sand\n- [ ] A race to the moon\n\n"
         "Read [[Dragon facts]] first.\n![[dragon]]\n")


def _dragon():
    """The targets' dragon (round13's thumb), as a drawing of MOY64 indices
    at 160x120: a navy sky, a green ground, a green dragon with a dark wing,
    yellow horns and an orange beak."""
    w, h = 160, 120
    px = bytearray([1]) * (w * h)

    def tri(a, b, c, col):
        def side(p, q, x, y):
            return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])
        for y in range(h):
            for x in range(w):
                s1, s2, s3 = side(a, b, x, y), side(b, c, x, y), side(c, a, x, y)
                if (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0):
                    px[y * w + x] = col

    def oval(cx, cy, rx, ry, col):
        for y in range(h):
            for x in range(w):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    px[y * w + x] = col
    for y in range(96, h):
        for x in range(w):
            px[y * w + x] = 3
    tri((50, 22), (60, 66), (84, 62), 3)
    tri((10, 92), (40, 80), (44, 88), 11)
    oval(70, 76, 32, 21, 11)
    oval(104, 46, 16, 15, 11)
    tri((94, 32), (98, 20), (101, 32), 10)
    tri((104, 32), (108, 20), (111, 32), 10)
    tri((116, 42), (138, 46), (118, 52), 9)
    oval(108, 42, 2.5, 2.5, 7)
    px[42 * w + 108] = 0
    return w, h, bytes(px)


def _notes(g, ws, state, window):
    """Notes as the targets draw it: the vault seeded (none for `empty`),
    newest first, and `state`'s page -- the list with Story ideas focused,
    Story ideas open with "scared of the dark" selected (and on the desk a
    pointer resting on Paste), or New note's dialog."""
    from runtime import moy_carts
    root = ws.carts_root
    if state != "empty":
        w, h, pix = _dragon()
        moy_carts.save_file("drawings", "dragon", moy_carts.encode_moyimg(w, h, pix), root)
        t = 1700000000
        for i, title in enumerate(NOTES):
            body = STORY if title == "Story ideas" else ""
            name = moy_carts.save_file("docs", title, body, root)
            path = moy_carts.file_path("docs", name, root) if hasattr(moy_carts, "file_path") \
                else None
            if path:
                os.utime(path, (t - 60 * i, t - 60 * i))
    if not window:
        ws.pointer.visible = False        # the T-Deck's targets: no pointer rests
    ws.go_home()
    ws._open_workspace(g._cart_by_title(ws, "Notes"))
    ws.run(ws.project, ws.launcher_layer)
    _settle(g, ws, 3)
    if window:
        _place(ws, "desktop", 112, 52, 900, 536)
        _settle(g, ws, 3)
    ns = ws.player.ns
    if state == "list":
        ns["cur"] = 2
    elif state == "new":
        ns["_ask"]("new")
    elif state == "edit":
        ns["_open"]("story_ideas")
        _settle(g, ws, 2)
        h = ns["ed"]
        # what the targets show: an edit to undo (a box ticked and unticked)
        # and a clipboard to paste
        laid = h._row(h.ed.lines[3], h._view[0])
        h._toggle_check(3, laid)
        h._toggle_check(3, h._row(h.ed.lines[3], h._view[0]))
        h.ed.clipboard = "map"
        ed = h.ed
        ed.row, ed.col = 2, len("- [x] A dragon who is ")
        ed.sel = (2, len("- [x] A dragon who is scared of the dark"))
        ed.select_sticky = True
        if window:
            _settle(g, ws, 3)
            x, y = _tool_xy(ws, ns, "Paste")
            # a mouse rests on Paste: its tooltip (the console publishes the
            # pointer in the game's space; the kit's frames run no input pass)
            ws.pointer.place(x, y)
            gx, gy = ws.wm.game_xy(x, y)
            ws.input.game_pointer = (gx, gy, False, False)
    _settle(g, ws, 3)


# The targets' drawings (rounds/round13.py ART and DRAWINGS, round15.py WIDE):
# file name -> (round13 kind, newest first); the desk's Garden is 1024 x 600.
_DRAWINGS = (("garden", "garden"), ("rocket", "rocket"), ("ginger", "cat"),
             ("fish_tank", "fish"), ("my_house", "house"), ("night_sky", "night"),
             ("dragon", "dragon"))


_ART = {}


def _art():
    """The rounds' drawings as MOY64 indices, {kind: (w, h, bytes)}, the
    desk's wide ones under "wide_<kind>": made in a process of their own
    (the rounds put the runtime on the import path as top-level modules)."""
    if _ART:
        return _ART
    import pickle
    import subprocess
    here = os.path.join(ROOT, "docs", "media", "look_2026-10", "rounds")
    out = subprocess.run([sys.executable, "-c", _ART_SRC, here], check=True,
                         stdout=subprocess.PIPE).stdout
    _ART.update(pickle.loads(out))
    return _ART


_ART_SRC = """
import pickle, sys
sys.path.insert(0, sys.argv[1])
import round15
r13 = round15.R13
lut = {}
for i, c in enumerate(r13.P64):
    lut.setdefault(c, i)
def idx(im):
    im = im.convert("RGB")
    px = im.load()
    out = bytearray(im.width * im.height)
    for y in range(im.height):
        for x in range(im.width):
            p = px[x, y]
            i = lut.get(p)
            if i is None:
                i = min(range(64), key=lambda k: sum((a - b) ** 2 for a, b in zip(r13.P64[k], p)))
                lut[p] = i
            out[y * im.width + x] = i
    return (im.width, im.height, bytes(out))
art = {k: idx(v) for k, v in r13.ART.items()}
art.update({"wide_" + k: idx(v) for k, v in round15.WIDE.items()})
sys.stdout.buffer.write(pickle.dumps(art))
"""


def _seed_drawings(ws, wide_garden=False, only=None):
    from runtime import moy_carts
    art = _art()
    root = ws.carts_root
    t = 1700000000
    for i, (name, kind) in enumerate(_DRAWINGS):
        if only is not None and name not in only:
            continue
        w, h, pix = art["wide_" + kind if wide_garden and name == "garden" else kind]
        moy_carts.save_file("drawings", name, moy_carts.encode_moyimg(w, h, pix), root)
        path = os.path.join(os.path.dirname(root), "files", "drawings", name + ".moyimg")
        if os.path.exists(path):
            os.utime(path, (t - 60 * i, t - 60 * i))


def _paint(g, ws, state, window):
    """Paint as the targets draw it: the Garden open (the desk's at 1024 x
    600, a handheld's view of it for the view scenes), on `state`'s page."""
    view = state.startswith("view")
    _seed_drawings(ws, wide_garden=window or view, only=("garden",))
    if state.startswith("touch"):
        ws.use_inputs([("local", "touch")])
    if not window:
        ws.pointer.visible = False
    else:
        ws.open_desk()
    ws.artwork.open_named("garden", "drawings")
    app = _app(ws, "artwork")
    app._starter_pending = False
    ws.open_app(ws._apps_by_id["artwork"])
    if window:
        _settle(g, ws, 1)
        _place(ws, "artwork", 112, 52, 900, 536)
    _settle(g, ws, 2)
    if view:
        v = app.viewer
        if state == "view-zoom":
            v.zoom = 4
            v.pan[0], v.pan[1] = 520, 300
        elif state == "view-more":
            app.pop = "more"
            app._focus = "Use as wallpaper\tW"
        else:
            app._say("From a desk: you can look, not draw")
        _settle(g, ws, 3)
        return
    d = app.doc
    d.history.append(((0, d._tile(0)),))     # an edit to undo
    if window:
        app.cur = [600, 240]
    else:
        app.pan = [20, 34]
        app.cur = [140, 107]
    if state == "main" and not window:
        app._say("Saved Garden")
    elif state in ("colours", "touch-colours"):
        app.pop = "colours"
        app.swatch = 14
    elif state == "brush":
        app.tool = 4
        app.size_i = 2
        app.pop = "brush"
    elif state == "more":
        app.pop = "more"
        app._focus = "Use as wallpaper\tW"
    elif state == "game":
        app._verb("game")
    _settle(g, ws, 3)
    if state == "game" and app.game is not None:
        titles = [c.get("title") for c in app.game.carts]
        if "Sky Run" in titles:
            app.game.cur = titles.index("Sky Run")
        _settle(g, ws, 3)


def _myart(g, ws, state, window):
    """My Art over the targets' drawings, Garden the wallpaper, focus on
    Rocket (its menu open for `menu`); none for `empty`."""
    if state != "empty":
        _seed_drawings(ws)
        ws.artwork.set_wallpaper("garden")
        ws.look.tick(0.0)
    if not window:
        ws.pointer.visible = False
    else:
        ws.open_desk()
    app = _app(ws, "myart")
    ws.open_app(ws._apps_by_id["myart"])
    if window:
        _settle(g, ws, 1)
        _place(ws, "myart", 112, 52, 900, 536)
    _settle(g, ws, 2)
    if state != "empty":
        app._focus = app._card_id("rocket")
        _settle(g, ws, 2)
        if state == "menu":
            app._verb("menu")
    _settle(g, ws, 3)


def _tool_xy(ws, ns, name):
    """The middle of tool `name`'s box as the Notes cart drew it last, in the
    desk's pixels."""
    from runtime import ui_draw
    b = ui_draw._C.api_box(ws.player._ui[0], name)
    gx, gy, _s = ws.wm.viewport()
    return gx + b[0] + b[2] // 2, gy + b[1] + b[3] // 2


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
    files.open_place("projects", keep="Harpoon Pop")
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
    """The Editor on Star Catcher at `tab`: on a desk, the workspace that
    holds it."""
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


def _tab(tab):
    """The Editor on Star Catcher at `tab`, as a window on the desk where
    the surface has one: the nearest screen to step 30's layouts and tabs."""
    def setup(g, ws):
        desk = getattr(ws.wm, "mode", None) == "desktop"
        if desk:
            ws.open_desk()
        _editor(g, ws, tab)
        if desk:
            _settle(g, ws, 2)
            _place(ws, "menu", 112, 54, 900, 534)
    return setup


# Step 30's targets (docs/media/look_2026-10/EDITOR_LAYOUT.md): the handheld
# tabs, and the desk Editor's layouts by concept, each to the Editor's tab
# nearest it, and the three readings of maximise and the resizable panes
# (MAXIMISE_VARIANTS.md).
EDITOR_TABS = {"config": "cards", "blocks": "blocks", "map": "map", "scene": "scene",
               "music": "music", "scene-editor_stamp": "scene"}
EDITOR_LAYOUTS = {"a-code": "code", "a-art": "map", "a-world": "scene", "a-sound": "music",
                  "b-config": "cards", "b-code": "code", "b-blocks": "blocks", "b-art": "paint",
                  "b-world": "scene", "b-world-stamp": "scene", "b-sound": "music",
                  "b-config-800x480": "cards", "b-code-800x480": "code", "b-art-800x480": "paint",
                  "b-world-800x480": "scene", "b-sound-800x480": "music",
                  "c-code": "code", "c-art": "paint",
                  "c-world": "scene", "c-sound": "music",
                  **{"max-%s-%s" % (v, k): t for v in ("e05", "v1", "v2")
                     for k, t in (("art", "paint"), ("code", "code"), ("art-800x480", "paint"))},
                  "max-v1-windows": "paint", "max-v2-revealed": "paint", "max-v2-touch": "paint",
                  "resize-art": "paint", "resize-code": "code", "resize-code-drag": "code",
                  "resize-art-min": "paint", "resize-art-800x480": "paint"}


# The workspace concept's pictures (concept B, EDITOR_LAYOUT.md; full screen
# and the dividers, MAXIMISE_VARIANTS.md): the workspace each draws, and
# whether the window is full screen. A title-strip picture (round 12's, and
# Resize's) is the restored window over the whole desk.
def _ws_scenes():
    out = {}
    for k in ("config", "code", "blocks", "art", "world", "world-stamp", "sound"):
        key = {"config": "settings", "blocks": "code", "world-stamp": "world"}.get(k, k)
        out["b-" + k] = (key, False, "Coin Quest" if k == "blocks" else "Sky Run")
        if k in ("config", "code", "art", "world", "sound"):
            out["b-%s-800x480" % k] = (key, False)
    for v in ("e05", "v1", "v2"):
        for k in ("art", "code", "art-800x480"):
            out["max-%s-%s" % (v, k)] = ("art" if k.startswith("art") else "code", v == "v2")
    for k in ("art", "code", "code-drag", "art-min", "art-800x480"):
        out["resize-" + k] = ("art" if k.startswith("art") else "code", False)
    return out


EDITOR_WORKSPACES = _ws_scenes()


def _workspace(key, full, cart="Sky Run"):
    """The Editor on `cart` (Sky Run; Coin Quest, a blocks cart, for the
    Blocks workspace) on the desk, showing workspace `key`: full screen, or
    restored over the whole desk under the bar."""
    def setup(g, ws):
        ws.open_desk()
        ws.open_in_editor(g._cart_by_title(ws, cart))
        _settle(g, ws, 2)
        win = ws.wm._wins["menu"]
        if win.full() != full:
            ws.wm._toggle_max(win)
        if not full:
            dx, dy, dw, dh = ws.wm._desk_rect()
            _place(ws, "menu", dx, dy, dw, dh)
        ws.editor_desk.switch(key)
        _settle(g, ws, 2)
    return setup


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
    glyph (D-25)."""
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
        if _FAKE_OTA[0]:
            sys.modules.pop("moy_ota", None)
            _FAKE_OTA[0] = False
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
             "handheld_make_own_long-press": "touch",
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


class _OffWifi(_HomeWifi):
    """Wi-Fi off: no network, none found (D-19's bar)."""

    def status(self):
        return (False, None, None)

    def scan(self):
        return []


def _wifi_off(base):
    """`base` with Wi-Fi off."""
    def setup(g, ws):
        ws.wifi = _OffWifi()
        base(g, ws)
    return setup


class _Ladder:
    """A board's idle ladder, as far as the system menu asks: it offers
    Sleep (the boards' spine sets one; the host's console has none)."""

    def sleep(self):
        pass


def _sleep(base):
    """`base` on a console whose idle ladder offers Sleep."""
    def setup(g, ws):
        ws.idle_ladder = _Ladder()
        base(g, ws)
    return setup


def _own_cart(g, ws):
    """A cart of the kid's own: a copy of Star Catcher (a built-in is
    copied, never deleted)."""
    from runtime.moy_store_base import builtin_name
    ws.carts.dup(g._cart_by_title(ws, "Star Catcher"))
    for cart in ws.carts.all:
        if cart.get("path") and builtin_name(cart["path"]) is None:
            return cart
    raise AssertionError("the copy of Star Catcher is not on the shelf")


def _menu_opens_safe(how):
    """The check that the system menu opened on a row that destroys
    nothing (design review R4-01), with its rows."""
    def check(ws):
        from runtime import console_notices
        m = ws.sysmenu
        got = m.open and m.widget == "menu"
        row = m.items[m.sel] if got and 0 <= m.sel < len(m.items) else None
        ok = bool(got) and row is not None and row[3] != console_notices._DANGER_ICON
        return [("menu opens safe", ok, "the menu %s opens on %s" % (
            how, row[4] if row is not None else "nothing"))]
    return check


def _sysmenu_over(base, how):
    """`base`, then the system key: the system menu over it, opened on a
    row that destroys nothing."""
    def setup(g, ws):
        base(g, ws)
        _settle(g, ws, 2)
        ws._golden_popover = True
        ws.system_key()
        _EXPECT[0] = _menu_opens_safe(how)
    return setup


def _make_own(g, ws):
    """The Make tab with a project of the kid's own selected: New, Change,
    Duplicate and, set apart, Delete."""
    own = _own_cart(g, ws)
    _home(g, ws)
    ws.set_home_tab("make")
    for i, it in enumerate(ws.launcher.items):
        if it.get("path") == own["path"]:
            ws.launcher.sel = i


def _cart_open(g, ws):
    """A cart of the kid's own open on its Code tab."""
    ws.open_in_editor(_own_cart(g, ws))
    ws.set_menu_view("code")


def _desk_files(g, ws):
    """Files alone on the desk, on Projects, its window shorter than its
    rows: covers, and the cut row's fade."""
    ws.open_desk()
    ws.open_app(ws._apps_by_id["files"])
    _settle(g, ws, 2)
    _place(ws, "files", 112, 54, 548, 380)
    _app(ws, "files").open_place("projects", keep="Harpoon Pop")


# What the home's search field holds in its scene, where something types.
SEARCH_TYPED = "st"


def _projects(size=None):
    """The desk's PROJECTS window alone on the desk, on Make with its first
    project selected: the home's hero, details and shelf in a window, at its
    default size or placed at `size` (x, y, w, h)."""
    def setup(g, ws):
        ws.open_desk()
        ws.open_projects()
        _settle(g, ws, 2)
        if size is not None:
            _place(ws, "projects", *size)
        grid = ws.projects_grid
        for i, it in enumerate(grid.items):
            if it.get("path"):
                grid.sel = i
                break
    return setup


def _projects_own(size=None, menu=None, editor=False):
    """The desk's PROJECTS window on Make with a cart of the kid's own
    selected (its Change, Duplicate and Delete), at its default size or
    placed at `size`; with `menu` its card's menu open over it, focus on the
    row it opens on ("first") or on Delete ("delete"); with `editor` that
    cart opened from it, the Editor's window above it (design review
    R5-07)."""
    def setup(g, ws):
        own = _own_cart(g, ws)
        ws.open_desk()
        ws.open_projects()
        _settle(g, ws, 2)
        if size is not None:
            _place(ws, "projects", *size)
        grid = ws.projects_grid
        for i, it in enumerate(grid.items):
            if it.get("path") == own["path"]:
                grid.sel = i
        _settle(g, ws, 2)
        if menu is not None:
            from runtime import console_notices
            ws._golden_popover = True
            ws.projects_layer.open_card_menu()
            m = ws.sysmenu
            if menu == "delete" and m.open:
                m.sel = next((i for i, it in enumerate(m.items) if it[0] == "item"
                              and it[3] == console_notices._DANGER_ICON), m.sel)
            _EXPECT[0] = _card_menu_open("the PROJECTS card")
        if editor:
            ws.launch_selected(grid)

            def check(ws):
                key = ws.wm.input_holder()
                win = getattr(ws.wm, "_wins", {}).get(key)
                ok = (win is not None and not win.minimized and ws.screen == "menu"
                      and getattr(ws.wm, "mode", None) == "desktop")
                return [("editor window", ok, "the Make pick opens the Editor in a "
                         "window: %s" % ("focused" if ok else "none"))]
            _EXPECT[0] = check
    return setup


def _home_search(g, ws):
    """The home's search field open: a query typed where something types,
    else the field's word on pairing a keyboard."""
    _home(g, ws)
    _settle(g, ws, 2)
    ws.open_search()
    if ws.can_type():
        _typed(g, ws, SEARCH_TYPED)


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


def _delete_asks(base, projects=False):
    """`base`, then Delete on its selected card (the home's, or the desk
    PROJECTS window's): the question every route asks before a cart goes,
    focus on Cancel."""
    def setup(g, ws):
        base(g, ws)
        _settle(g, ws, 2)
        (ws.projects_layer if projects else ws.launcher_layer)._do_verb("delete")

        def check(ws):
            c = ws.confirm
            ok = c.open and c.title.startswith("Delete ") and c.focused == "Cancel"
            return [("delete asks", ok, "%s, focus on %s" % (
                c.title if c.open else "no dialog", c.focused))]
        _EXPECT[0] = check
    return setup


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
                                       "the playtest minimised: its chip's pause glyph"),
        "desktop_playtest_restored": (DESK, _playtest_restored,
                                      "the minimised playtest restored: the pause menu"),
        "handheld_sysmenu": (HANDHELD, _sysmenu_home,
                             "the system menu over the focused home: one focus mark"),
        "handheld_make_sysmenu": (HANDHELD, _sysmenu_over(_make_own, "over Make"),
                                  "the system menu over Make, a project of the kid's own"),
        "handheld_make_sysmenu-sleep": (
            HANDHELD, _sleep(_sysmenu_over(_make_own, "over Make with Sleep offered")),
            "the system menu over Make with Sleep offered: its body cut and faded"),
        "handheld_cart_sysmenu": (HANDHELD, _sysmenu_over(_cart_open, "over an open cart"),
                                  "the system menu over an open cart of the kid's own"),
        "desktop_sysmenu-sleep": (DESK, _sleep(_sysmenu_desk),
                                  "the system menu over the desk with Sleep offered"),
        "handheld_home_wifi-off": (HANDHELD, _wifi_off(_home), "the home, Wi-Fi off"),
        "desktop_home_wifi-off": (CONSOLE, _wifi_off(_console_home),
                                  "the console-mode home, Wi-Fi off"),
        "desktop_projects": (DESK, _projects(), "the desk's PROJECTS window on Make, "
                             "a project selected: hero, details and shelf"),
        "desktop_projects_narrow": (DESK, _projects((112, 54, 360, 534)),
                                    "the PROJECTS window narrowed: the hero shrinks"),
        "desktop_projects_short": (DESK, _projects((112, 54, 700, 400)),
                                   "the PROJECTS window shortened: the pane drops its facts"),
        "desktop_projects_own": (DESK, _projects_own(), "the PROJECTS window on a cart of "
                                 "the kid's own: Change, Duplicate, Delete set apart"),
        "desktop_projects_own_narrow": (DESK, _projects_own((112, 54, 360, 534)),
                                        "the PROJECTS window narrowed on a cart of the "
                                        "kid's own: its buttons wrap, none dropped"),
        "desktop_projects_card-menu": (DESK, _projects_own(menu="first"),
                                       "the PROJECTS card's menu, on the row it opens on"),
        "desktop_projects_card-menu_delete": (DESK, _projects_own(menu="delete"),
                                              "the PROJECTS card's menu, focus on Delete"),
        "desktop_projects_sysmenu": (DESK, _sysmenu_over(_projects_own(), "over PROJECTS"),
                                     "the system menu over the PROJECTS window"),
        "handheld_make_delete-confirm": (HANDHELD, _delete_asks(_make_own),
                                         "Make's X on a cart of the kid's own: "
                                         "the delete asks, Cancel focused"),
        "desktop_projects_delete-confirm": (DESK, _delete_asks(_projects_own(), True),
                                            "the PROJECTS window's Delete: the delete "
                                            "asks over the desk, Cancel focused"),
        "handheld_make_own_long-press": (HANDHELD, _long_press(_make_own),
                                         "a long press on a Make card of the kid's own: "
                                         "its verbs, Delete set apart"),
        "desktop_files": (DESK, _desk_files, "Files alone on the desk: covers, the cut "
                          "row's fade"),
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
            "handheld_home_search": (
                HANDHELD, _home_search, "the home's search field, %r typed where "
                "something types" % SEARCH_TYPED),
            "desktop_home": (CONSOLE, _console_home, "the console-mode home"),
            "desktop_projects_editor": (
                DESK, _projects_own(editor=True), "a cart of the kid's own opened from "
                "PROJECTS: the Editor's window above it")}.items():
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
    menu = ws.sysmenu.open or ws.pause.open or ws.confirm.open
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
# The structure ramp (D-05, owner 2026-10-19, "don't be too harsh"): a
# hairline, the theme's `border`/`edge`, holds 1.5:1 to the ground it lies
# on, and an Off toggle's track or a slider's or a stepped bar's rest 1.6:1,
# on a focused row's plate too; a text field's outline is a part, 3:1.
STRUCTURE_FLOOR = 1.5
STRUCTURE_TOKENS = ("border", "edge")
TRACK_FLOOR = 1.6
# The part kinds whose row holds a track: its value in the value's colour
# (a part), the rest of it the structure ramp's.
TRACK_KINDS = ("slider", "steps", "volume")
# The measures with a known, filed gap, by pair: its issue or review item
# in the owner column, and no FLAG while the gap is open.
KNOWN = {}


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


def _disabled_rgb(ws):
    """The live theme's disabled ink as the glass shows it, or None."""
    live = getattr(ws.look, "live", None)
    v = live.look[0].get("disabled") if live is not None else None
    return _index_rgb(ws, v) if v is not None and v >= 0 else None


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
    # a row on its card: the page's ground where the card's rounded corner
    # shows it is no ink of the row's
    if plate in _token_rgbs(ws, ("surface",)):
        page = _token_rgbs(ws, ("panel",)) - {plate}
        for k in inks:
            inks[k] = [c for c in inks[k] if c not in page]
    out = []
    ramp = _token_rgbs(ws, STRUCTURE_TOKENS)

    def add(name, c, floor, on=None):
        on = plate if on is None else on
        if c in ramp and kind != "field" and floor >= FLOOR:
            floor = STRUCTURE_FLOOR
        out.append((name, _hex(c), _hex(on), contrast(c, on), floor, ""))
    texts = inks["text"][:2]
    if kind == "field":
        # an empty field's placeholder is dim on purpose (D-05): held as a
        # disabled ink is, on the field's own ground
        dis = _disabled_rgb(ws)
        for c in [c for c in texts if dis is not None and _diff(c, dis) <= 12]:
            texts.remove(c)
            add("field placeholder vs its ground", c, DISABLED_FLOOR,
                on=_index_rgb(ws, ws.theme_colors["surface"]))
    for c in texts:
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
    elif kind == "toggle" and solids:
        # the track, On in the value's colour (a part) or Off in the
        # structure ramp's; the knob lies on the track and is measured there
        value = _token_rgbs(ws, ("value", "accent"))
        track = next((c for c in solids if c in value), None)
        ground = _token_rgbs(ws, ("surface", "ink", "value_ink"))
        if track is None:
            track = next((c for c in solids if c not in ground), solids[0])
            add(pair + " (Off track)", track, TRACK_FLOOR)
        else:
            add(pair, track, FLOOR)
        for c in solids:
            if c != track and c in ground:
                add("toggle knob vs its track", c, STRUCTURE_FLOOR, on=track)
        solids = []
    elif kind in TRACK_KINDS and solids:
        # the value in the value's colour and the knob in the ink are parts;
        # the rest of the track is the structure ramp's
        part = _token_rgbs(ws, ("value", "accent", "ink", "value_ink"))
        for c in solids:
            if c in part:
                add(pair, c, FLOOR)
            else:
                add(pair + " (rest track)", c, TRACK_FLOOR)
        solids = []
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
    """The label ink of tab `r` on `plate`: its commonest solid text ink,
    else the ink its icon shares with it (an icon's filled parts make the
    pair read as a solid: the Editor's Config tab)."""
    inner = (r[0] + 1, r[1] + 1, max(1, r[2] - 2), max(1, r[3] - 2))
    inks = _inks(img, inner, plate)
    got = inks["text"] or inks["line"] or inks["solid"]
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
            # a plain tab of a window behind (PROJECTS' under its Editor) is
            # none of this row's
            plain = [_tab_ink(img, q, ground) for q, act in tabs
                     if not act and q[2] >= 8 and not _covered(ws, q)]
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
    """(the Editor's `attr` layout, the glass x and y of its content): `own`,
    the handheld's, or None on a desk, whose Editor tabs stand in the
    workspaces' panes (their measures are tests/test_editor_desk.py's)."""
    if ws.editor_desk is not None and ws.editor_desk.active():
        return None, 0, 0
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
    if lay is None:
        return []
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
    if lay is None:
        return []
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
    if lay is None:
        return []
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
    pitch = 24 * lay.fs
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
    left third where captions start. A theme whose dim ink is its ink
    (Contrast, D-14) has no caption ink to tell apart."""
    if ws.screen != "settings" or ws.settings_layer.has_page():
        return []
    lay = ws.layout
    inks = _token_rgbs(ws, ("chrome_ink_dim", "ink_dim")) - _token_rgbs(ws, ("ink", "chrome_ink"))
    if not inks:
        return []
    px = img.load()
    got = sum(1 for y in (lay.cy, lay.cy + 1) for x in range(lay.cx, lay.cx + lay.cw // 3)
              if px[x, y] in inks)
    return [_measure("caption cut at the page top", got, 0, got == 0)]


def _rim_pairs(ws, probe, img):
    """A lifted card's rim hugs its cover (design review R3-05): between
    the hero's cover and its rim no run of the card's plate (Moy's lift)."""
    look = _theme_look(ws)
    if (look.get("focus") or {}).get("card") != "lift" or ws.screen != "launcher" \
            or ws.sysmenu.open or ws.pause.open or ws.confirm.open:
        return []           # a menu or a dialog over the home takes the hero's lift
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


def _meets(a, b):
    """Whether rects `a` and `b` share a pixel."""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] \
        and b[1] < a[1] + a[3]


def _strongest(img, r):
    """(ink, ground) of rect `r` as it shows: its commonest colour, and the
    colour farthest from it in contrast, or None."""
    r = _clip(r, (0, 0, img.width, img.height))
    hist = _colours(img, r, 1) if r is not None else {}
    if not hist:
        return None
    ground = max(hist, key=hist.get)
    return max(hist, key=lambda c: contrast(c, ground)), ground


def _menu_keys_words(ws, layer, ox=0, oy=0):
    """The glass rect of the words a home's hint strip names the open
    modal's first key with (`launcher_layer.modal_keys`: "Close" after its
    keycap under a menu, "Choose" under the delete confirm), `layer` the
    home's layer,
    its strip offset by (ox, oy); None where it draws none. The words, not
    the strip's strongest colour: a keycap's glyph can stand far stronger
    than the words beside it, so a scrim over the words would pass on the
    glyph (design review R5-03)."""
    from runtime import launcher_layer, ui_draw
    foot = layer.grid.foot()
    if foot is None or not foot[5][3]:
        return None
    rect = foot[5]
    keys = launcher_layer.modal_keys(ws)
    if keys is None:
        return None
    spans = ui_draw.hints(ws.sys_canvas, ws.theme_colors, layer._hint_inner(rect), keys,
                          layer.grid.desk(), draw=False)
    if not spans or spans[0][0] == spans[0][1]:
        return None
    x1 = spans[0][1]
    w = ui_draw.print_width(keys[1], ws.layout.fs)
    return (ox + x1 - w, oy + rect[1], w, rect[3])


def _menu_pairs(ws, img):
    """The system menu's and a card's menu's card as it shows (design review
    R4-33): clear of its anchor and 4 pixels at the scale inside the glass
    it takes (R4-04), each danger row's words on the card at 4.5:1, and
    the hint strip naming its keys (`_strip_pairs`)."""
    m = ws.sysmenu
    mu = getattr(ws, "menu_ui", None)
    if mu is None or not (m.open and mu.looked()):
        return []
    from runtime import console_notices
    out = []
    card = mu._host.box("System")
    if card is not None:
        fs = ws.sys_canvas.font_scale
        sx, sy, sw, sh = mu.surface()
        gap = min(card[0] - sx, card[1] - sy, sx + sw - card[0] - card[2],
                  sy + sh - card[1] - card[3])
        out.append(_measure("menu card to its glass's edge", gap, 4 * fs, gap >= 4 * fs))
        if m.widget == "menu":
            over = _meets(card, mu._menu_cell())
            out.append(_measure("menu card over its anchor", int(over), 0, not over))
        body = _popover_body(ws, card)
        for row in m.items:
            if row[0] == "item" and row[3] == console_notices._DANGER_ICON:
                b = mu._host.box(row[4])
                # a row the body's cut peeks under its fade is the fade's
                # measure (`_fade_pairs`), not its words'
                if b is not None and body[1] <= b[1] and b[1] + b[3] <= body[1] + body[3]:
                    out += [("danger row ink vs card",) + r[1:]
                            for r in _row_pairs(ws, img, b, "danger row", view=card)
                            if r[0].endswith("(text)")]
    return out + _strip_pairs(ws, img)


def _strip_pairs(ws, img):
    """The hint strip that names an open modal's keys (the system menu's,
    the delete confirm's, `launcher_layer.modal_keys`): over the home its
    ink on its ground at 4.5:1 (R4-05), and its words on the desk's
    PROJECTS window's strip unwashed (R5-03, F6-02)."""
    out = []
    grid = getattr(ws.launcher_layer, "grid", None)
    foot = grid.foot() if grid is not None and ws.screen == "launcher" else None
    if foot is not None and not grid.desk():
        got = _strongest(img, foot[5])
        if got is not None:
            out.append(("hint ink vs strip ground", _hex(got[0]), _hex(got[1]),
                        contrast(*got), TEXT_FLOOR, ""))
    words = _projects_strip(ws)
    if words is not None:
        # the window's strip names the menu's keys too: it stays unwashed (R5-03)
        got = _strongest(img, words)
        if got is not None:
            pair = "PROJECTS hint ink vs strip ground"
            ratio = contrast(*got)
            out.append((pair, _hex(got[0]), _hex(got[1]), ratio, TEXT_FLOOR,
                        "" if ratio >= TEXT_FLOOR else KNOWN.get(pair, "")))
    return out


def _selection_pairs(ws, img):
    """The sprite selection's ring against the grid's lines (design review
    R4-02, R4-33): one of its two tones stands at 3:1 off the lines."""
    if ws.screen != "menu" or getattr(ws, "menu_view", None) != "paint" or ws.paint is None:
        return []
    sel = getattr(ws.paint, "sel", None)
    if sel is None:
        return []
    lay, ox, oy = _editor_origin(ws, "paint_layout", ws.paint_layer.layout)
    if lay is None:
        return []
    cell = lay.pg_span // ws.paint.dim
    x0 = ox + lay.pg_x0 + sel[0] * cell
    ys = range(oy + lay.pg_y0 + (sel[1] + 1) * cell, oy + lay.pg_y0 + sel[3] * cell)
    if x0 < 1 or x0 >= img.width or not ys:
        return []
    border = _index_rgb(ws, ws.theme_colors["border"])
    tones = {img.getpixel((x, y)) for x in (x0 - 1, x0) for y in ys
             if 0 <= y < img.height}
    tones.discard(border)
    if not tones:
        return [("selection ring vs grid line", _hex(border), _hex(border), 1.0, FLOOR, "")]
    best = max(tones, key=lambda c: contrast(c, border))
    return [("selection ring vs grid line", _hex(best), _hex(border),
             contrast(best, border), FLOOR, "")]


# A list's fade (native/moy_ui/moy_ui_api.c's FADE_H): the last this many
# rows at the font scale of a body cut to its room; a table's is one row.
FADE_H = 10
# A fade band shows rows when this share of the pixels of its half at the
# cut (a percentage) stands this far off the band's ground: a band of blank
# card says nothing runs on. Half a row's words under a fade, its peek, come
# to about 1%; blank card is 0.
FADE_INK_SHARE = 0.5
FADE_INK_FLOOR = 1.5
# The row a popover's cut body peeks reads where its fade begins (design
# review F6-01): in the strip of FADE_READ_ROWS rows at the scale each side
# of the fade's clear edge (its first rows barely tint), this share of the
# pixels (a percentage) stands FLOOR off the ground, a pixel row that is
# mostly ink (a rule) not counted. A peek whose words all lie deep in the
# fade leaves that strip blank card, or a rule.
FADE_READ_ROWS = 2
FADE_READ_SHARE = 2.0
FADE_RULE_SHARE = 0.6


def _popover_body(ws, card, origin=(0, 0)):
    """A popover's body on the glass: its card less the modal band
    (`moy_ui_modal_band`) all round."""
    from runtime import console_notices, ui_draw
    fs = ws.sys_canvas.font_scale
    d = 1 if console_notices._desk_class() else 0
    pad = ui_draw.metrics("value_row")[28 + d] * fs
    ox, oy = origin
    return (ox + card[0] + pad, oy + card[1] + pad, card[2] - 2 * pad, card[3] - 2 * pad)


def _popover_bands(ws, name, host, card_id, labels, origin=(0, 0)):
    """[(where, rect)] on the glass of the fades of popover `card_id` in
    `host` (an AppHost) whose body (`_popover_body`) its rows `labels` run
    on past: at its foot, and at its head where it is scrolled."""
    card = host.box(card_id)
    if card is None:
        return []
    h = FADE_H * ws.sys_canvas.font_scale
    ox, oy = origin
    body = _popover_body(ws, card, origin)
    below = above = False
    for label in labels:
        b = host.box(label)
        if b is None:
            continue
        below = below or oy + b[1] + b[3] > body[1] + body[3]
        above = above or oy + b[1] < body[1]
    out = []
    if below:
        out.append((name + "'s foot", (body[0], body[1] + body[3] - h, body[2], h)))
    if above:
        out.append((name + "'s head", (body[0], body[1], body[2], h)))
    return out


def _fade_bands(ws):
    """[(where, rect)] on the glass of every fade the scene's lists draw
    where rows run on past what shows (design review R4-10, R4-04, R5-07):
    the system menu's and a card's menu's body cut at its foot or scrolled
    at its head, the sprite tab's more menu's, and a table whose rows run
    on below its view."""
    out = []
    m = ws.sysmenu
    mu = getattr(ws, "menu_ui", None)
    if mu is not None and m.open and mu.looked():
        out += _popover_bands(ws, "the menu", mu._host, "System",
                              [row[4] for row in m.items if row[0] == "item"])
    pl = getattr(ws, "paint_layer", None)
    if pl is not None and getattr(pl, "_pop", None) == "more" and ws.screen == "menu" \
            and getattr(ws, "menu_view", None) == "paint":
        from runtime import paint_layer, ui_draw
        keys = paint_layer._OP_KEYS.get(ui_draw.input_kind(), {})
        labels = []
        for oid, label, _slot in pl._more_rows(pl.layout.desk):
            k = keys.get(oid)
            labels.append(label + ("\t" + k if k else ""))
        origin = (0, 0)
        if ws.wm.desk_open():
            win = getattr(ws.wm, "_wins", {}).get(ws.wm.input_holder())
            if win is not None:
                origin = win.content_rect()[:2]
        out += _popover_bands(ws, "the more menu", pl._host,
                              "Shift" if pl._shifts else "More", labels, origin)
    files = _app(ws, "files") if "files" in ws._apps_by_id else None
    win = getattr(ws.wm, "_wins", {}).get("files")
    shown = ws.screen == "files" or (win is not None and not getattr(win, "minimized", False)
                                     and ws.wm.desk_open())
    host = getattr(files, "_ui", None)
    if shown and host is not None and getattr(files, "rows", None) and not m.open:
        # (under a menu's scrim the list is no focus, its fade washed out)
        from runtime import ui_draw
        got = ui_draw._C.api_table_rows(host.ctx)
        if got is not None and len(files.rows) * got[1] > got[2][3]:
            ox, oy = (win.content_rect()[:2] if win is not None and ws.wm.desk_open()
                      else (0, 0))
            vx, vy, vw, vh = got[2]
            out.append(("Files' rows", (ox + vx, oy + vy + vh - got[1], vw, got[1])))
    return out


def _fade_pairs(ws, probe, img):
    """Every fade over rows that run on lies over rows (design review
    R5-07): in the half of each band `_fade_bands` finds toward its cut
    edge, at least FADE_INK_SHARE percent of the pixels stand
    FADE_INK_FLOOR off the band's ground (its commonest colour). A fade
    over blank card, or none at all, is a FLAG: nothing tells the kid the
    list goes on. The half at the cut, since a cut that falls between rows
    still has the last whole row's descenders in the band's other half."""
    out = []
    for where, r in _fade_bands(ws):
        if where.endswith(("foot", "head")):
            out.append(_fade_read(where, r, img))
        half = r[3] // 2
        r = (r[0], r[1] if where.endswith("head") else r[1] + r[3] - half, r[2], half)
        r = _clip(r, (0, 0, img.width, img.height))
        pair = "fade band shows rows (%s)" % where
        hist = _colours(img, r, 1) if r is not None else {}
        total = sum(hist.values())
        if not total:
            out.append((pair, "0.0%", "%.1f%%" % FADE_INK_SHARE, 0.0, 1.0,
                        KNOWN.get(pair, "")))
            continue
        ground = max(hist, key=hist.get)
        ink = sum(n for c, n in hist.items() if contrast(c, ground) >= FADE_INK_FLOOR)
        share = 100.0 * ink / total
        ok = share >= FADE_INK_SHARE
        out.append((pair, "%.1f%%" % share, "%.1f%%" % FADE_INK_SHARE, 1.0 if ok else 0.0,
                    1.0, "" if ok else KNOWN.get(pair, "")))
    return out


def _fade_read(where, band, img):
    """The pair that holds the row a popover's fade lies over readable
    where the fade begins (FADE_READ_SHARE): the strip FADE_READ_ROWS at
    the scale each side of the band's clear edge, its ink FLOOR off the
    band's ground."""
    pair = "fade's row reads over it (%s)" % where
    x, y, w, h = band
    k = FADE_READ_ROWS * max(1, h // FADE_H)
    edge = y + h if where.endswith("head") else y
    strip = _clip((x, edge - k, w, 2 * k), (0, 0, img.width, img.height))
    hist = _colours(img, band, 1)
    if strip is None or not hist:
        return (pair, "0.0%", "%.1f%%" % FADE_READ_SHARE, 0.0, 1.0, KNOWN.get(pair, ""))
    ground = max(hist, key=hist.get)
    sx, sy, sw, sh = strip
    ink = total = 0
    for yy in range(sy, sy + sh):
        n = sum(contrast(img.getpixel((xx, yy)), ground) >= FLOOR for xx in range(sx, sx + sw))
        if n <= FADE_RULE_SHARE * sw:
            ink += n
        total += sw
    share = 100.0 * ink / total if total else 0.0
    ok = share >= FADE_READ_SHARE
    return (pair, "%.1f%%" % share, "%.1f%%" % FADE_READ_SHARE, 1.0 if ok else 0.0, 1.0,
            "" if ok else KNOWN.get(pair, ""))


def _projects_strip(ws):
    """The words naming the open menu's keys on the desk PROJECTS window's
    hint strip, as a glass rect (`_menu_keys_words`), where the window
    shows, else None."""
    win = getattr(ws.wm, "_wins", {}).get("projects")
    if win is None or getattr(win, "minimized", False) or not ws.wm.desk_open():
        return None
    layer = getattr(ws, "projects_layer", None)
    if layer is None:
        return None
    cx, cy, _w, _h = win.content_rect()
    ws.wm._install(win.ctx)
    try:
        return _menu_keys_words(ws, layer, cx, cy)
    finally:
        ws.wm._install(ws.wm._root_ctx)


def audit(ws, probe, img):
    """[(pair, a, b, ratio, floor, owner)] of one render: the focused row's
    parts against its plate and its mark against the row, the caret against
    its line, the open tab's current mark against the ground beside it, the
    focused desk chip's label, a disabled history icon's ink against the
    enabled ink, an Off toggle's row in Settings, a lifted hero's rim, the
    desk Calc's backspace, a menu's card, danger rows and the strip under
    it, the sprite selection's ring, and rows under every fade where a list
    runs on, reading where the fade begins (`_fade_pairs`).
    A measure with a known gap names it in `owner` (`KNOWN`); the rest
    under their floor are the FLAGs."""
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
                v = _viewport(ws, b, hinted) if b is not None else None
                # only a row wholly on screen: a sliver cut by the hint
                # strip shows a track's edge, no toggle
                if b is not None and v is not None and _clip(b, v) == tuple(b):
                    out += _row_pairs(ws, img, b, "Off toggle vs row ground", kind, v)
    t = getattr(ws.code_layer, "_t", None)
    # a menu over the editor dims it: its caret is no focus to measure
    modal = ws.sysmenu.open or ws.pause.open or ws.confirm.open
    if ws.screen == "menu" and t is not None and "caret" in t and not modal:
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
    out += [] if modal else _caret_pairs(ws, probe, img)
    out += _sprite_pairs(ws, img)
    out += _foot_pairs(ws, img)
    out += _calc_pairs(ws, img)
    out += _menu_pairs(ws, img)
    if ws.confirm.open:
        out += _strip_pairs(ws, img)
    out += _selection_pairs(ws, img)
    out += _fade_pairs(ws, probe, img)
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
        failed=None, unheld=False):
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
                        audit_csv, fails, bars, unheld)
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


# The run's (FLAGs, known gaps), summed as each render reports.
_AUDIT_COUNT = [(0, 0)]


def _headline(since):
    """The index's audit line: the FLAGs and the known gaps (`KNOWN`) its
    renders reported since `_AUDIT_COUNT` read `since`."""
    flags = _AUDIT_COUNT[0][0] - since[0]
    known = _AUDIT_COUNT[0][1] - since[1]
    return "Audit: %d FLAG%s, %d known gap%s (KNOWN) under their floors." % (
        flags, "" if flags == 1 else "s", known, "" if known == 1 else "s")


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
                "known gap " + owner if owner else "FLAG", pair, a, b, ratio, floor))
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
    since = _AUDIT_COUNT[0]
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
            "audit's flags follow it.",
            "Input: %s." % ("every scene draws %s's keys (--input), but a screen that is "
                            "one input's own" % inputs if inputs else
                            "a handheld scene draws the T-Deck's keys and a desk scene the "
                            "keyboard's (no --input), a profile scene its profile's"),
            _headline(since), ""]
    with open(os.path.join(out, "index.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(head + lines))
    return rows


def _run(g, out, filters, step, theme, variant, quiet, parts=False, inputs=None,
         audit_csv=False, fails=None, bars=None, unheld=False):
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
    since = _AUDIT_COUNT[0]
    for name, what, want_theme, want_mode, size in scenes:
        config, _steps, wallpaper, setup, shows = table[screen_of(name)]
        if parts and screen_of(name) in PARTS:
            setup = _parts(PARTS[screen_of(name)])
            shows = "the parts sheet's %s page" % PARTS[screen_of(name)]
        target_theme = theme or want_theme or DEFAULT
        mode = variant or want_mode or "dark"
        missing = []
        drawn = target_theme
        if target_theme not in held and not unheld:
            lines.append("%s  [SKIPPED]  theme %s is not in the shell (--unheld draws it "
                         "with %s)" % (name, target_theme, DEFAULT))
            lines.append("")
            rows.append((name, False, ["theme %s is not in the shell" % target_theme]))
            if not quiet:
                print("%-40s skipped (theme not held)" % name)
            continue
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
        lines.append("  render: %s; theme %s, %s; input %s; config %s; %dx%d%s" % (
            shows, drawn, mode, inputs or (HANDHELD_INPUT if config == HANDHELD else "keys"),
            config, shot.width, shot.height,
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
        _headline(since),
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
    ap.add_argument("--unheld", action="store_true",
                    help="draw the scenes of a theme the shell does not hold in the "
                    "default theme instead of skipping them")
    args = ap.parse_args(argv)
    failed = []
    rows = run(args.out, args.filters, args.step, args.theme, args.variant,
               parts=args.parts, inputs=args.input, kit=args.kit, profiles=args.profiles,
               only_kit=args.only_kit, audit_csv=args.audit, failed=failed,
               unheld=args.unheld)
    print("%d scenes, %d built -> %s" % (
        len(rows), sum(1 for r in rows if r[1]),
        os.path.join(args.out, "index.txt")))
    if args.audit:
        print("%d audit flags, %d known gaps (KNOWN)" % _AUDIT_COUNT[0])
    for name, check, detail in failed:
        print("FAILED %s: %s (%s)" % (name, check, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
