"""The undefined-name gate over the extracted modules.

A moved function body that names a symbol its new namespace never imported
back NameErrors only at CALL time -- a cart draws, a browser hits /assets --
so a module-load and the golden single-frame renders cannot see it; it reaches
hardware as `NameError: name _X isn't defined`. pyflakes' static undefined-name
analysis catches exactly this class across a whole file.
"""

from pathlib import Path


ROOT = Path("firmware/lilygo_t_deck_plus_mainline")


def test_no_undefined_names_in_extracted_modules():
    try:
        from pyflakes.checker import Checker
        from pyflakes.messages import UndefinedName
    except ImportError:  # pragma: no cover - pyflakes is a dev dep; skip if absent
        import pytest
        pytest.skip("pyflakes not installed")
    import ast

    targets = sorted((ROOT / "modules").glob("device_*.py"))
    targets.append(ROOT / "modules" / "moy_runtime.py")
    targets += [Path("runtime") / n for n in (
        "console.py", "console_perf.py", "console_settings.py", "console_saves.py",
        "console_notices.py", "wm_desk.py", "wm_chrome.py",
        "project.py", "player.py", "editor_app.py", "wm.py", "perf_hud.py", "update_ui.py", "system_menu_ui.py",
        "achievements_ui.py", "layers.py", "bar_layer.py", "cards_layer.py", "paint_layer.py", "settings_layer.py", "code_layer.py", "widgets.py", "wallpaper.py", "launcher_layer.py",
        "block_editor_ui.py", "map_editor_ui.py", "music_editor_ui.py")]

    bad = []
    for path in targets:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, str(path))
        for m in Checker(tree, str(path)).messages:
            if isinstance(m, UndefinedName):
                bad.append("%s:%d %s" % (path.name, m.lineno,
                                         m.message % m.message_args))
    assert not bad, "undefined names (would NameError at runtime): " + "; ".join(bad)
