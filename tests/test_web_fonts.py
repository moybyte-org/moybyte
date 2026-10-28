"""The browser console draws in the theme's fonts (docs/look_2026-10.md, the
phase gate: "the browser console the Zero serves draws in RGB with the theme's
fonts").

The browser build carries the font set the way a console board does: `build.sh`
generates `fonts_data` with `tools/make_font.py module` for the tier
firmware/web_runner/board.toml's `font_tier` names, and it freezes into the
wasm. Without it every theme falls back to the pixel font and nothing fails --
`fontset.file` answers None, `appearance._install_fonts` leaves each role on
petme128 -- so this holds three things:

* the module the build generates names every face a seed theme draws in on
  each class the browser lays out on, in both variants;
* each arrangement of the real wasm console lays out on its board's class, one
  the module was built for (the desk on 1024x600 the P4's, the handheld on
  320x240 the T-Deck's, each booted from dist/ in node);
* in that console a theme's roles load the faces the host loads (same
  `role_fonts`, same file headers), not the pixel font.
"""

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from runtime import appearance_app, host_app  # noqa: F401  (import order: the runtime path)
from runtime import appearance, fontset, layout_base

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import make_font  # noqa: E402

pytest.importorskip("PIL")

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "firmware" / "web_runner"
# The seed themes: the files native/moy_ui/themes/ carries.
SEEDS = sorted(Path(p).stem for p in glob.glob(str(ROOT / "native" / "moy_ui" / "themes" / "*.json")))
VARIANTS = ("dark", "light")
ROLES = ("ui", "title", "caption", "mono")


def _module():
    ns = {}
    exec(compile(make_font.render_module(make_font.board_fonts(str(WEB))), "fonts_data", "exec"), ns)
    return ns


def test_the_browser_module_names_every_face_a_seed_theme_draws():
    assert SEEDS == ["blocks", "contrast", "moy"], SEEDS    # a new seed joins this net
    assert sorted(make_font.board_fonts(str(WEB))) == ["desktop", "handheld"]
    carried = set(_module()["FACES"])
    for desk in (False, True):
        for name in SEEDS:
            for variant in VARIANTS:
                files = appearance.role_fonts(name, variant, desk)
                assert files, (name, variant, desk)
                for role, (_pkg, f) in files.items():
                    if fontset.face(f):
                        assert f[:-4] in carried, (
                            "the browser build carries no %s: theme %s %s draws %s in it"
                            % (f, name, variant, role))
                assert "ui" in files and "mono" in files, (name, variant, sorted(files))


# The font tier each metric class takes.
CLASS_TIER = {layout_base.HANDHELD: "handheld", layout_base.DESK: "desktop"}


def test_the_browser_carries_the_faces_of_each_class_it_lays_out_on():
    """The handheld arrangement declares no glass, the desk the P4's
    (tests/test_web_look_parity.py holds the number); a class whose tier
    board.toml's font_tier leaves out would draw every theme in petme128."""
    tiers = make_font.board_fonts(str(WEB))
    for glass in (None, 7.0):
        assert CLASS_TIER[layout_base.class_of(glass)] in tiers, glass


def _scenario(tmp_path, windowed, size):
    py = ("import json, ui_draw\n"
          "got = {'class': _ws.metric_class}\n"
          "for n in %r:\n"
          "    for v in %r:\n"
          "        _ws.look.set_theme(n, persist=False, variant=v)\n"
          "        got[n + '/' + v] = {r: list(ui_draw.font(r)) for r in %r}\n"
          "print('FONTS ' + json.dumps(got))\n" % (SEEDS, VARIANTS, ROLES))
    steps = [{"frames": 30}, {"py": py}]
    path = tmp_path / ("fonts_%s.json" % ("desk" if windowed else "handheld"))
    path.write_text(json.dumps({"size": size, "windowed": windowed, "steps": steps}))
    return path


def _header(face):
    d = fontset.file(face)
    assert d is not None and d[:4] == b"MFNT", face
    return (d[8], d[6], d[7])       # the cell, the line, the ascent


# The wasm half runs where the browser suites run (MOYBYTE_WEB_E2E: the Web
# console E2E job and preflight's browser step, both after build.sh); the
# plain suite has no dist/, or a stale one.
@pytest.mark.skipif(not os.environ.get("MOYBYTE_WEB_E2E"),
                    reason="MOYBYTE_WEB_E2E not set (boots the built wasm console)")
@pytest.mark.parametrize("windowed,size", [(True, [1024, 600]), (False, [320, 240])])
def test_the_browser_console_draws_a_themes_roles_in_its_faces(tmp_path, windowed, size):
    from web_e2e import require
    require()
    if not (WEB / "dist" / "micropython.mjs").exists():
        pytest.skip("no built firmware/web_runner/dist")
    scenario = _scenario(tmp_path, windowed, size)
    r = subprocess.run(["node", "pageshot.mjs", str(scenario), str(tmp_path / "shots")],
                       cwd=WEB, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "Traceback" not in r.stdout, r.stdout[-3000:] + r.stderr[-500:]
    line = [ln for ln in r.stdout.splitlines() if "FONTS " in ln]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0].split("FONTS ", 1)[1])
    want_class = layout_base.DESK if windowed else layout_base.HANDHELD
    assert got.pop("class") == want_class
    assert CLASS_TIER[want_class] in make_font.board_fonts(str(WEB))
    for key, roles in got.items():
        name, variant = key.split("/")
        files = appearance.role_fonts(name, variant, windowed)
        for role in ROLES:
            fid, cell, line_h, ascent = roles[role]
            assert fid != 0, "%s draws %s in petme128 in the browser" % (key, role)
            assert (cell, line_h, ascent) == _header(files[role][1]), (key, role, roles[role])
