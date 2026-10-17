#!/usr/bin/env python3
"""The 2026-10 look's review kit: every mockup beside the real shell's render.

usage: .venv/bin/python tools/look_shots.py OUT [FILTER ...] [--step N]
                                            [--theme NAME] [--variant MODE]
                                            [--parts]

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
becomes FALLBACK's) and marked "not built yet" with what is missing.

FILTER keeps the scene it names, else the scenes whose name contains it
(shell globs work too);
`--step N` keeps the scenes step N targets. `--theme`/`--variant` render
every kept scene in that theme instead of its own. `--parts` draws the
handheld Settings and Appearance scenes with the public UI API's parts sheet
(tests/fixtures/ui_parts/parts_app.py, docs/look_2026-10.md step 12) in
place of the shell's screens, which have not moved onto the verbs yet.
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

# The current theme a target theme the shell does not hold is drawn with,
# by `theme-mode` first, then by theme.
FALLBACK = {"moy-light": "platinum", "moy": "night", "blocks": "platinum",
            "luna": "win95", "coast": "platinum", "pico": "night",
            "bench": "platinum", "adw": "platinum"}
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
        "desktop_playtest": (DESK, ("9", "13", "23"), False, _code,
                             "nearest screen: the Editor on the desk"),
        "handheld_sysmenu": (HANDHELD, ("11", "12", "16", "25a"), False,
                             _home, "nearest screen: home"),
        "desktop_sysmenu": (DESK, ("11", "12", "16", "25a"), False, _desk,
                            "nearest screen: the desk"),
        "handheld_pause": (HANDHELD, ("11", "16", "25a"), False, _home,
                           "nearest screen: home"),
        "desktop_pause": (DESK, ("11", "16", "25a"), False, _desk,
                          "nearest screen: the desk"),
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


def _settle(g, ws, frames=8):
    """Draw until two frames are byte-identical (or `frames` run out)."""
    prev = None
    for _ in range(frames):
        g._quiesce(ws)
        ws._dirty = True
        ws.frame(g._DT)
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


def _desk_wall(g, ws):
    _desk(g, ws, calc_x=592)


class _HomeWifi:
    """The targets' Wi-Fi: on the network called Home."""

    def status(self):
        return (True, "Home", "192.168.1.20")

    def scan(self):
        return [("Home", 80, True)]

    def known(self):
        return ["Home"]


def _settings(g, ws):
    # The target's facts: Wi-Fi on Home, the battery at 82%, Appearance focused.
    ws.wifi = _HomeWifi()
    ws.battery_level = lambda: 82
    ws.open_settings()
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


def _code(g, ws):
    _editor(g, ws, "code")


def _sprites(g, ws):
    _editor(g, ws, "paint")


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


def render(g, config, theme, mode, setup, wallpaper):
    """(image, settled) for one scene on a fresh workstation."""
    import moy_ui
    moy_ui.theme_uninstall()
    tmp = tempfile.mkdtemp(prefix="look_shots_")
    try:
        cfg = dict(g.CONFIGS[config], variant=mode)
        if "layout" not in cfg:
            cfg["theme"] = theme
        ws = g._build(cfg, os.path.join(tmp, "carts"))
        if not wallpaper:
            ws.look.select_wallpaper(None, persist=False)
        setup(g, ws)
        settled = _settle(g, ws)
        return _image(ws), settled
    finally:
        moy_ui.theme_uninstall()
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


def run(out, filters=(), step=None, theme=None, variant=None, quiet=False, parts=False):
    """Render, pair and index the kept scenes into `out`. Returns the rows
    written: [(scene, built, missing)]. The cover slice and the bar's clock
    are pinned for the run and put back after it."""
    g = _goldens()
    from runtime import cover_cache
    saved = cover_cache._COVER_SLICE_MS, g._CLOCK
    cover_cache._COVER_SLICE_MS = g._COVER_SLICE_UNBOUNDED
    g._CLOCK = CLOCK
    try:
        return _run(g, out, filters, step, theme, variant, quiet, parts)
    finally:
        cover_cache._COVER_SLICE_MS, g._CLOCK = saved


def _run(g, out, filters, step, theme, variant, quiet, parts=False):
    from PIL import Image
    table = _setup_table()
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
            drawn = theme if theme in held else FALLBACK.get(
                "%s-%s" % (target_theme, mode),
                FALLBACK.get(target_theme, "night"))
            missing.append("theme %s is not in the shell (drawn with %s)"
                           % (target_theme, drawn))
        todo = [s for s in scene_steps(name, target_theme)
                if not landed.get(s)]
        if todo:
            missing.append("steps %s have not landed" % ", ".join(todo))
        if want_theme is None:
            missing.append("no shell screen draws this sheet")
        shot, settled = render(g, config, drawn, mode, setup, wallpaper)
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
    args = ap.parse_args(argv)
    rows = run(args.out, args.filters, args.step, args.theme, args.variant,
               parts=args.parts)
    print("%d scenes, %d built -> %s" % (
        len(rows), sum(1 for r in rows if r[1]),
        os.path.join(args.out, "index.txt")))


if __name__ == "__main__":
    main()
