"""Every icon of the Editor's narrow bar draws ink: the open tab's pill, the
other tabs, UNDO, REDO and PLAY.

The ladder draws the theme's icon set where its look names one, and the set's
own slots for a few tabs (`editor_app._SET_KIND`: Config's sliders, Sprites'
face, Play's bare glyph), which the bar's IconSheet does not hold. An image
that carries no set's files draws the shell's own icons instead
(`iconsets.carried`), every one of them, where drawing the set's slots it
lacks would leave the Config pill, the Sprites tab and Play blank under Moy,
the default theme, which names the smooth set. The browser carries the
handheld boards' sets, so its bar draws Moy's smooth set as the T-Deck's
does.

The probe is one source, run on the host's handheld workstation (with the
image's sets and without them) and in the real wasm console (the browser
suites' MOYBYTE_WEB_E2E, booted from dist/ in node at the handheld's
320x240).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from runtime import fontset, iconsets
from ws_helpers import build_ws, device_frames

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "firmware" / "web_runner"

# Wraps the Editor's draw_zone: after each draw, per ladder entry, the
# distinct pixels in the middle of its icon's box (an inset of a quarter of
# the box each side, which every icon's art crosses). One colour there is a
# blank icon, a bare pill on the open tab. Plain Python, as MicroPython runs
# it in the wasm console.
PROBE = '''
def ladder_probe(ws):
    ed = ws.editor_app
    orig = ed.draw_zone
    got = []
    def draw_zone(cv, rect):
        orig(cv, rect)
        buf = cv._buf
        stride = getattr(cv, "_stride", 0) or cv.w
        ox = getattr(cv, "_ox", 0) or 0
        oy = getattr(cv, "_oy", 0) or 0
        out = []
        for tab, glyph, r in ed._ladder_rects(rect):
            x, y, w, ic = r
            q = ic // 4
            x0 = ox + x + (w - ic) // 2 + q
            seen = set()
            for yy in range(oy + y + q, oy + y + ic - q):
                i = 2 * (yy * stride + x0)
                for k in range(ic - 2 * q):
                    j = i + 2 * k
                    seen.add(buf[j] << 8 | buf[j + 1])
            out.append([tab, glyph, tab is not None and tab == ed.tab, len(seen)])
        got.append(out)
    ed.draw_zone = draw_zone
    return got
'''


def _blank(drawn):
    """The ladder entries the last draw left blank, as (tab, glyph, open)."""
    assert drawn, "the Editor's bar never drew its zone"
    return [(tab, glyph, is_open) for tab, glyph, is_open, n in drawn[-1] if n < 2]


def _ladder(tmp_path, variant):
    ns = {}
    exec(PROBE, ns)
    ws = build_ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant=variant)
    drawn = ns["ladder_probe"](ws)
    cart = next(c for c in ws.carts.all if c.get("title") == "Sky Run")
    ws.open_in_editor(cart)
    ws.set_menu_view("cards")
    ws.bar_layer.invalidate()
    device_frames(ws, 3)
    return ws, drawn


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_every_ladder_icon_draws_from_the_set(tmp_path, variant):
    ws, drawn = _ladder(tmp_path, variant)
    assert ws.icon_spec() == ("smooth", "default", None)
    assert _blank(drawn) == []
    assert any(is_open for _t, _g, is_open, _n in drawn[-1])


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_an_image_without_the_set_draws_the_shells_icons(tmp_path, monkeypatch, variant):
    monkeypatch.setattr(fontset, "icon_file", lambda name: None)
    ws, drawn = _ladder(tmp_path, variant)
    assert _blank(drawn) == []
    assert ws.icon_spec() is None
    assert any(is_open for _t, _g, is_open, _n in drawn[-1])


def test_carried_answers_what_the_image_holds(monkeypatch):
    smooth, pixel = ("smooth", "luna", None), ("pixel", None, None)
    assert iconsets.carried(smooth) == smooth
    assert iconsets.carried(pixel) == pixel
    assert iconsets.carried(None) is None
    monkeypatch.setattr(fontset, "icon_file", lambda name: None)
    monkeypatch.setattr(iconsets, "_PIXEL", [])
    assert iconsets.carried(smooth) is None
    assert iconsets.carried(pixel) is None


@pytest.mark.skipif(not os.environ.get("MOYBYTE_WEB_E2E"),
                    reason="MOYBYTE_WEB_E2E not set (boots the built wasm console)")
@pytest.mark.parametrize("variant", ["light", "dark"])
def test_the_browsers_ladder_draws_every_icon(variant):
    from web_e2e import require
    require()
    if not (WEB / "dist" / "micropython.mjs").exists():
        pytest.skip("no built firmware/web_runner/dist")
    setup = PROBE + (
        "_ws.look.set_theme('moy', persist=False, variant=%r)\n"
        "_drawn = ladder_probe(_ws)\n"
        "_ws.open_in_editor([c for c in _ws.carts.all if c.get('title') == 'Sky Run'][0])\n"
        "_ws.set_menu_view('cards')\n"
        "_ws.bar_layer.invalidate()\n" % variant)
    report = ("import json\n"
              "print('LADDER ' + json.dumps({'spec': _ws.icon_spec(), 'drawn': _drawn}))\n")
    steps = [{"frames": 30}, {"py": setup}, {"frames": 10}, {"py": report}]
    with tempfile.TemporaryDirectory() as tmp:
        scenario = Path(tmp) / "ladder.json"
        scenario.write_text(json.dumps({"size": [320, 240], "windowed": False, "steps": steps}))
        r = subprocess.run(["node", "pageshot.mjs", str(scenario), str(Path(tmp) / "shots")],
                           cwd=WEB, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "Traceback" not in r.stdout, r.stdout[-3000:] + r.stderr[-500:]
    line = [ln for ln in r.stdout.splitlines() if "LADDER " in ln]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0].split("LADDER ", 1)[1])
    assert got["spec"] == ["smooth", "default", None]   # the T-Deck's set
    assert _blank(got["drawn"]) == []
    assert any(is_open for _t, _g, is_open, _n in got["drawn"][-1])
