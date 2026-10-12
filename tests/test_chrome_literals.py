"""The chrome-literal ratchet (docs/theming_2026-09.md §9, phase 2a).

A theme reaches a pixel only through a role. Every `NAMES["..."]` colour
literal in a `runtime/` module is a pixel no theme reaches, and every
`light_chrome()` / `is_light()` branch is a variant difference written as code
instead of as a token. This pins both counts per module: a count may fall,
never rise. When one falls, lower its pin here in the same commit, so the pin
stays the count and the next rise is caught.

Content is not exempted by name: a literal in the Paint palette or a block
colour counts too. The ratchet asks only that the total never grows; phases
2b and 3 are what bring it down.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")

_LITERAL = re.compile(r'NAMES\["[a-z_]+"\]')
_BRANCH = re.compile(r"light_chrome\(\)|\bis_light\(")

# module -> (NAMES literals, light branches); a mention in a comment or a
# docstring counts too, which only makes the ratchet stricter.
PINNED = {
    "achievements_ui.py": (11, 0),
    "appearance.py": (0, 1),
    "block_editor_ui.py": (53, 0),
    "calc_app.py": (2, 0),
    "cards_layer.py": (26, 0),
    "code_layer.py": (17, 0),
    "console.py": (9, 0),
    "editor_app.py": (2, 0),
    "launcher_layer.py": (39, 0),
    "map_editor_ui.py": (44, 0),
    "music_editor_ui.py": (34, 0),
    "paint_layer.py": (24, 0),
    "perf_hud.py": (4, 0),
    "scene_editor_ui.py": (36, 0),
    "settings_layer.py": (30, 0),
    "text_console.py": (6, 0),
    "update_ui.py": (2, 0),
    "wallpaper.py": (4, 0),
    "web_console_ui.py": (3, 0),
    "wm_chrome.py": (3, 0),
}


def _counts():
    out = {}
    for name in sorted(os.listdir(RUNTIME)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(RUNTIME, name)) as f:
            lines = [ln for ln in f if not ln.lstrip().startswith("def ")]
        text = "".join(lines)
        n = (len(_LITERAL.findall(text)), len(_BRANCH.findall(text)))
        if n != (0, 0):
            out[name] = n
    return out


def test_chrome_literals_only_fall():
    now = _counts()
    rose = {m: (PINNED.get(m, (0, 0)), n) for m, n in now.items()
            if n[0] > PINNED.get(m, (0, 0))[0] or n[1] > PINNED.get(m, (0, 0))[1]}
    assert not rose, (
        "a colour literal or a light branch was added (module: (pinned, now)); "
        "draw through a theme role instead: %r" % rose)
    fell = {m: (p, now.get(m, (0, 0))) for m, p in PINNED.items()
            if now.get(m, (0, 0)) != p}
    assert not fell, (
        "the count fell -- lower PINNED in tests/test_chrome_literals.py to "
        "(module: (pinned, now)): %r" % fell)
