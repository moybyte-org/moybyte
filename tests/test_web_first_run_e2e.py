"""A first run's theme and the start option, in a real browser (OPFS and all).

  * The console's theme VARIANT on a first run -- no saved theme in the browser's
    store -- follows the page's colour scheme: dark is Moy Night, light is Moy
    Day. The scheme is the browser's own `prefers-color-scheme`, or the url's
    `?scheme=` which the project site passes to its embedded console. A choice
    the visitor saved in Settings wins on every later visit.
  * `?edit=<cart>&tab=code` boots into the Editor on that cart's Code tab, where
    a plain visit boots to the home; it opens the cart the browser's store holds,
    so a visitor's edits are kept, never re-seeded over.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_first_run_e2e.py

Env-gated like its siblings (`tests/web_e2e.py`: a missing toolchain skips on a
bench and fails under CI). The profile and the port are pinned where a second
load has to see the first's store (OPFS is per origin, in the profile).
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

CART = "moybyte.star_catcher.moy"
EDIT = "&edit=%s&tab=code" % CART
STATE = ("window.__moyState().then(s => 'STATE ' + JSON.stringify("
         "{variant: s.theme.variant, name: s.theme.name, tab: s.edit.tab, "
         "screen: s.screen, code: s.edit.code}))")


@pytest.fixture
def base():
    web_e2e.require("store", "firstrun")
    port = web_e2e.free_port()          # fixed for the test: OPFS is per origin
    p = subprocess.Popen([sys.executable, "serve.py", str(port), "dist"], cwd=RUNNER,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(url + "/index.html", timeout=1).read(64)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("serve.py never answered")
        yield url
    finally:
        p.terminate()
        p.wait(timeout=10)


def _load(tmp_path, base, profile, query, scheme, tag, steps=(), tier="desktop"):
    """One boot of `query` under `scheme` in `profile`: the console's state,
    after `steps` (which run first, once it is up). `start=1` skips the page's
    play button, as the site's hero does. `tier` is "desktop" or "handheld"."""
    out, js = web_e2e.run(
        tmp_path, tag, "?%s=1&start=1" % tier + query,
        [{"wait": 3000}] + list(steps) + [{"js": STATE}], base, profile,
        boot_ms=60000, scheme=scheme)
    for j in reversed(js):
        if isinstance(j, str) and j.startswith("STATE "):
            return json.loads(j[6:]), out
    pytest.fail("no state reported:\n" + out[-3000:])


@pytest.mark.parametrize("scheme,variant", [("dark", "dark"), ("light", "light")])
def test_a_first_run_follows_the_browsers_scheme(tmp_path, base, scheme, variant):
    got, out = _load(tmp_path, base, tmp_path / "chrome", "", scheme, "first_" + scheme)
    assert (got["name"], got["variant"]) == ("moy", variant), out[-2000:]


def test_the_url_scheme_is_what_the_site_passes(tmp_path, base):
    """The embedded console is told its page's scheme, whatever the frame's."""
    got, out = _load(tmp_path, base, tmp_path / "chrome", "&scheme=light", "dark", "url")
    assert got["variant"] == "light", out[-2000:]


def test_a_saved_choice_wins_over_the_scheme(tmp_path, base):
    """Day picked in Settings -> Appearance on a dark browser's first run: the
    next visit, still dark, is Day -- and an unpicked first run is not saved,
    so before the pick the scheme still decided."""
    profile = tmp_path / "chrome"
    got, _ = _load(tmp_path, base, profile, "", "dark", "pick", steps=[
        {"note": "Settings from the system menu, Appearance, Mode: Day"},
        {"click": [16, 14]}, {"wait": 600},
        {"click": [77, 104]}, {"wait": 1200},
        {"click": [443, 273]}, {"wait": 1500},
        {"click": [923, 330]}, {"wait": 6000}])
    assert got["variant"] == "light", "the Day pick did not take"
    again, out = _load(tmp_path, base, profile, "", "dark", "after_pick")
    assert again["variant"] == "light", \
        "a dark browser overrode the saved Day choice:\n" + out[-2000:]
    # ...and the url's scheme does not override it either.
    url, out = _load(tmp_path, base, profile, "&scheme=dark", "dark", "after_url")
    assert url["variant"] == "light", out[-2000:]


def test_the_start_option_opens_the_editor_on_code(tmp_path, base):
    home, _ = _load(tmp_path, base, tmp_path / "chrome_home", "", "dark", "home")
    assert home["tab"] != "code", "a plain visit must boot to the home: %r" % home["tab"]
    got, out = _load(tmp_path, base, tmp_path / "chrome_edit", EDIT, "dark", "edit")
    assert got["tab"] == "code", out[-2000:]
    assert got["code"][0].startswith("# Star Catcher"), got["code"]


def test_the_start_option_works_on_the_handheld_and_leaves_to_the_home(tmp_path, base):
    """Both tiers open on Code, and the way out is the console's own: Escape on
    the handheld goes to its launcher, the editor's X on the desk to the desk."""
    hand, out = _load(tmp_path, base, tmp_path / "chrome_h", EDIT, "dark", "hand",
                      tier="handheld")
    assert (hand["tab"], hand["screen"]) == ("code", "menu"), out[-2000:]
    left, out = _load(tmp_path, base, tmp_path / "chrome_h2", EDIT, "dark", "hand_esc",
                      tier="handheld", steps=[{"key": "Escape"}, {"wait": 900}])
    assert left["screen"] == "launcher", out[-2000:]
    desk, out = _load(tmp_path, base, tmp_path / "chrome_d", EDIT, "dark", "desk_x",
                      steps=[{"click": [998, 20]}, {"wait": 900}])
    assert desk["screen"] == "desk", out[-2000:]


def test_the_start_option_opens_the_visitors_edits(tmp_path, base):
    """An edited Star Catcher in the browser's store is what opens, never the
    seed again."""
    profile = tmp_path / "chrome"
    typed, _ = _load(tmp_path, base, profile, EDIT, "dark", "typed", steps=[
        {"js": "document.getElementById('cv').focus(), 'focused'"},
        {"key": "q"}, {"key": "q"}, {"wait": 400},
        {"key": "s", "mods": ["ctrl"]}, {"wait": 6000}])
    assert typed["code"][0].startswith("qq# Star Catcher"), typed["code"]
    back, out = _load(tmp_path, base, profile, EDIT, "dark", "kept")
    assert back["code"][0].startswith("qq# Star Catcher"), \
        "the visitor's edit was replaced by the seed:\n" + out[-2000:]


def test_an_unknown_cart_leaves_the_home(tmp_path, base):
    got, out = _load(tmp_path, base, tmp_path / "chrome", "&edit=nothing.here&tab=code",
                     "dark", "unknown")
    assert got["tab"] != "code", out[-2000:]
