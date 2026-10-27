"""A first run's theme in a real browser (OPFS and all).

  * The console's theme VARIANT on a first run -- no saved theme in the browser's
    store -- follows the page's colour scheme: dark is Moy Night, light is Moy
    Day. The scheme is the browser's own `prefers-color-scheme`, or the url's
    `?scheme=` which the project site passes to its embedded console. A choice
    the visitor saved in Settings wins on every later visit.

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
    after `steps` (which run first, once it is up). `dev=1` skips the page's
    play button. `tier` is "desktop" or "handheld"."""
    out, js = web_e2e.run(
        tmp_path, tag, "?%s=1&dev=1" % tier + query,
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
