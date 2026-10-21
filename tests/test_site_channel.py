"""The dev site beside the stable one (site/build.py --channel).

The two sites share the origin moybyte-org.github.io, so they share its browser
storage. The dev page's player is stamped with its channel, the worker opens
every store under that channel's directory (moy_store.channelNav, whose
isolation store_test.mjs drives), and a page that could not be stamped ships
without a player rather than with one that writes the stable site's carts.
"""

import gzip
import importlib.util
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "firmware", "web_runner")


def _load_build():
    path = os.path.join(ROOT, "site", "build.py")
    spec = importlib.util.spec_from_file_location("_moy_site_build_channel", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


build = _load_build()


def _player(root, line=None):
    """A stand-in for firmware/web_runner/dist: an index.html carrying the
    channel line page_tail.js ships (or `line`), plus its .gz twin."""
    src = os.path.join(root, "dist")
    os.makedirs(src)
    html = ("<!doctype html><html><head><meta charset=utf-8>\n<script>\n%s\n"
            "</script></head><body></body></html>\n"
            % (build.CHANNEL_LINE if line is None else line))
    with open(os.path.join(src, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with gzip.open(os.path.join(src, "index.html.gz"), "wb") as f:
        f.write(html.encode())
    with open(os.path.join(src, "worker.js"), "w") as f:
        f.write("// worker\n")
    return src


def _firmware(root):
    """Both variants for every board, as tools/fetch_ci_firmware.py lays them."""
    src = os.path.join(root, "ci-firmware")
    for variant in ("stable", "beta"):
        for board in build.BOARDS:
            folder = os.path.join(src, variant, board["id"])
            os.makedirs(folder)
            with open(os.path.join(folder, board["images"][0]), "wb") as f:
                f.write((variant + board["id"]).encode() * 64)
    return src


def _site(tmp_path, monkeypatch, *flags, line=None):
    monkeypatch.setattr(build, "PLAYER_SRC", _player(str(tmp_path), line))
    out = str(tmp_path / "_site")
    monkeypatch.setattr(sys, "argv", ["build.py", "--out", out, "--firmware",
                                      _firmware(str(tmp_path))] + list(flags))
    build.main()
    html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    return out, html


def test_the_dev_player_is_stamped_with_its_channel(tmp_path, monkeypatch):
    out, _ = _site(tmp_path, monkeypatch, "--channel", "dev")
    index = os.path.join(out, "player", "index.html")
    html = open(index, encoding="utf-8").read()
    assert 'const MOY_CHANNEL = "dev";' in html
    assert build.CHANNEL_LINE not in html
    assert '<meta name="robots" content="noindex,nofollow">' in html
    # The precompressed twin says the same thing, never the stable page.
    with gzip.open(index + ".gz", "rb") as f:
        assert f.read().decode("utf-8") == html


def test_the_stable_player_is_the_bundle_untouched(tmp_path, monkeypatch):
    out, page = _site(tmp_path, monkeypatch)
    html = open(os.path.join(out, "player", "index.html"), encoding="utf-8").read()
    assert build.CHANNEL_LINE in html and "noindex" not in html
    assert "noindex" not in page and 'class="chbar"' not in page
    assert "<title>moybyte" in page


def test_a_player_that_cannot_be_stamped_is_not_published(tmp_path, monkeypatch):
    """A dev player sharing the stable site's storage is the one outcome worse
    than no player: the page builds, says the player is missing, and ships no
    bundle."""
    out, page = _site(tmp_path, monkeypatch, "--channel", "dev",
                      line="const MOY_BUILD = 1;")
    assert not os.path.exists(os.path.join(out, "player"))
    assert 'class="warnbox"' in page


def test_the_dev_page_says_what_it_is(tmp_path, monkeypatch):
    _, page = _site(tmp_path, monkeypatch, "--channel", "dev")
    assert '<meta name="robots" content="noindex,nofollow">' in page
    assert "<title>[dev] moybyte" in page
    bar = re.search(r'<div class="chbar".*?</div></div>', page, re.S).group(0)
    assert "Dev build" in bar and "may be broken" in bar
    assert '<a href="../">Stable site' in bar
    name = re.search(r'^FIRMWARE_NAME = "([^"]*)"', open(
        os.path.join(ROOT, "device", "moy_ota.py"), encoding="utf-8").read(), re.M).group(1)
    assert "after %s" % name in bar
    commit = build.provenance()[1]
    if commit:                         # a checkout with git: the commit is linked
        assert "/commit/%s\">%s</a>" % (commit, commit[:7]) in bar


def test_the_dev_flasher_offers_the_beta_first(tmp_path, monkeypatch):
    out, page = _site(tmp_path, monkeypatch, "--channel", "dev")
    manifest = json.load(open(os.path.join(out, "firmware", "manifest.json")))
    for board in manifest["boards"]:
        assert board["variant"] == "beta", board["id"]
        assert [b["variant"] for b in board["builds"]] == ["beta", "stable"]
    # Both builds stay flashable from the dev page.
    assert '<option value="beta" selected>' in page
    assert '<option value="stable">' in page


def test_the_stable_flasher_offers_the_release_first(tmp_path, monkeypatch):
    out, _ = _site(tmp_path, monkeypatch)
    manifest = json.load(open(os.path.join(out, "firmware", "manifest.json")))
    for board in manifest["boards"]:
        assert [b["variant"] for b in board["builds"]] == ["stable", "beta"]


def test_the_page_carries_the_line_the_stamp_rewrites():
    """ROUTING: the stamp has exactly one line to rewrite, and the page hands
    the channel to the worker, which opens every store through it."""
    tail = open(os.path.join(RUNNER, "page_tail.js"), encoding="utf-8").read()
    assert tail.count(build.CHANNEL_LINE) == 1
    assert "channel: MOY_CHANNEL" in tail
    worker = open(os.path.join(RUNNER, "worker.js"), encoding="utf-8").read()
    assert "opfsNav = store.channelNav(navigator, channel)" in worker
    opens = re.findall(r"store\.open(?:Store|Written)\((\w+)", worker)
    assert opens and set(opens) == {"opfsNav"}, opens


@pytest.mark.skipif(not os.path.exists(os.path.join(RUNNER, "dist", "index.html")),
                    reason="no browser console built")
def test_the_built_page_carries_the_line_too():
    """build.sh copies page_tail.js into index.html verbatim; a minifier that
    reformatted the line would ship a dev site with no player."""
    html = open(os.path.join(RUNNER, "dist", "index.html"), encoding="utf-8").read()
    assert html.count(build.CHANNEL_LINE) == 1
