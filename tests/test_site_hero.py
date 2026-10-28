"""The site's hero is a click-to-start console (site/build.py).

Before the click the page holds a STILL of the real console and nothing else:
no iframe, no reference to the player in its markup, so the visitor's browser
never fetches the wasm. The stills are captured from the player the build ships
(`capture_stills`, run with node and Chrome by the Pages job; the browser
suites own the driver), so what is pinned here is the wiring: the build asks
for them against its own copy of the player, a failed capture stops the build,
and the page starts the console the way the stills were taken.
"""

import importlib.util
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_build():
    path = os.path.join(ROOT, "site", "build.py")
    spec = importlib.util.spec_from_file_location("_moy_site_build_hero", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


build = _load_build()


def _player(root):
    src = os.path.join(root, "dist")
    os.makedirs(src)
    with open(os.path.join(src, "index.html"), "w") as f:
        f.write("<!doctype html><script>\n%s\n</script>\n" % build.CHANNEL_LINE)
    return src


def _site(tmp_path, monkeypatch, capture):
    monkeypatch.setattr(build, "PLAYER_SRC", _player(str(tmp_path)))
    monkeypatch.setattr(build, "capture_stills", capture)
    out = str(tmp_path / "_site")
    monkeypatch.setattr(sys, "argv", ["build.py", "--out", out, "--firmware",
                                      str(tmp_path / "none")])
    build.main()
    return out, open(os.path.join(out, "index.html"), encoding="utf-8").read()


def _fake_capture(calls):
    def capture(player, media):
        calls.append(player)
        os.makedirs(media, exist_ok=True)
        for name, _scheme in build.STILLS:
            with open(os.path.join(media, "console-%s.png" % name), "wb") as f:
                f.write(b"png")
    return capture


def test_the_stills_are_captured_from_the_player_the_build_ships(tmp_path, monkeypatch):
    calls = []
    out, html = _site(tmp_path, monkeypatch, _fake_capture(calls))
    assert calls == [os.path.join(out, "player")]
    for name in ("night", "day"):
        assert os.path.exists(os.path.join(out, "media", "console-%s.png" % name))
    # Night under a dark scheme, Day (the <img>) otherwise.
    assert re.search(r'<source media="\(prefers-color-scheme: dark\)" '
                     r'srcset="media/console-night.png">', html)
    assert 'src="media/console-day.png"' in html


def test_no_player_is_loaded_before_the_click(tmp_path, monkeypatch):
    _, html = _site(tmp_path, monkeypatch, _fake_capture([]))
    markup, script = html.split("<script>", 1)
    assert "<iframe" not in markup and "player/" not in markup
    assert 'id="try"' not in html, "the Try it section is folded into the hero"
    # The start is a button (Enter works) and the tabs wait for it.
    assert re.search(r'<button class="still" id="still"', markup)
    assert re.search(r'<div class="tabs" id="tabs" hidden>', markup)
    # The iframe is only ever made by the start.
    assert script.index('createElement("iframe")') > script.index("function show(")


def test_the_console_starts_as_the_stills_were_taken(tmp_path, monkeypatch):
    _, html = _site(tmp_path, monkeypatch, _fake_capture([]))
    assert "&start=1&edit=moybyte.star_catcher.moy&tab=code&scheme=" in html
    assert build.STILL_QUERY.endswith("&edit=moybyte.star_catcher.moy&tab=code")
    assert "&start=1" in build.STILL_QUERY


def test_a_failed_capture_stops_the_build(tmp_path, monkeypatch):
    def broken(player, media):
        raise SystemExit("!! capturing the night still failed")
    with pytest.raises(SystemExit):
        _site(tmp_path, monkeypatch, broken)


def test_the_hero_gif_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "site", "hero.gif"))
