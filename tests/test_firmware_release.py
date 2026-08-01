"""The delivery pipeline: CI build -> `firmware-latest` release -> the website.

Two programs have to agree on one thing here. tools/publish_firmware_release.py
flattens each board's image into a release asset (`<board>-<image>`, because
release assets have no folders), and tools/fetch_ci_firmware.py unflattens it
again into the per-board tree site/build.py reads. Nothing in between checks
that they still agree -- a rename on either side would publish a page whose
flash buttons quietly vanish, on a workflow that only runs when someone
dispatches it by hand.

So this runs the whole path with a stub `gh`: stage assets the way the publisher
would, serve them the way a release does, fetch them back, and build the site
from the result. What is asserted at the end is the manifest the BROWSER reads.
"""

import importlib.util
import json
import os
import stat
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


publish = _load("tools/publish_firmware_release.py", "_moy_publish")
fetch = _load("tools/fetch_ci_firmware.py", "_moy_fetch")
build = _load("site/build.py", "_moy_site_build")

# What the publisher writes into the fake release, as the workflow would.
ENV = {
    "GITHUB_REPOSITORY": "moybyte-org/moybyte",
    "GITHUB_SHA": "abc1234def5678000000000000000000000000ff",
    "GITHUB_RUN_ID": "30484539277",
    "GITHUB_RUN_NUMBER": "14",
    "GITHUB_REF_NAME": "master",
}

# A stub `gh` covering exactly the two release calls the fetcher makes. It reads
# the "release" out of a directory named by MOY_FAKE_RELEASE.
FAKE_GH = '''#!/usr/bin/env python3
import json, os, shutil, sys, glob
argv = sys.argv[1:]
rel = os.environ["MOY_FAKE_RELEASE"]
if argv[:2] == ["release", "view"]:
    if not os.path.isdir(rel):
        sys.stderr.write("release not found\\n"); sys.exit(1)
    names = sorted(os.listdir(rel))
    print(json.dumps({"assets": [{"name": n} for n in names]}))
elif argv[:2] == ["release", "download"]:
    pat = argv[argv.index("-p") + 1]
    dest = argv[argv.index("-D") + 1]
    os.makedirs(dest, exist_ok=True)
    hit = glob.glob(os.path.join(rel, pat))
    if not hit:
        sys.stderr.write("no assets matched\\n"); sys.exit(1)
    for src in hit:
        shutil.copyfile(src, os.path.join(dest, os.path.basename(src)))
else:
    sys.stderr.write("unexpected gh call: %s\\n" % argv); sys.exit(2)
'''


@pytest.fixture
def released(tmp_path, monkeypatch):
    """A fake `firmware-latest` release, staged by the real publisher."""
    artifacts = tmp_path / "artifacts"
    for board in build.BOARDS:
        folder = artifacts / ("moybyte-firmware-%s" % board["id"])
        folder.mkdir(parents=True)
        (folder / board["images"][0]).write_bytes(
            b"IMAGE:" + board["id"].encode() + bytes(range(256)))
        (folder / "decoy.bin").write_bytes(b"\xff" * 32)

    release = tmp_path / "release"
    release.mkdir()
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    staged = publish.stage(build.BOARDS, str(artifacts), str(release))
    assert len(staged) == len(build.BOARDS)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("MOY_FAKE_RELEASE", str(release))
    return release


def test_board_ids_survive_being_an_asset_prefix():
    """`<board>-<image>` is split on the first dash on the way back."""
    for board in build.BOARDS:
        assert "-" not in board["id"], board["id"]


def test_the_publisher_names_assets_the_fetcher_can_read(released):
    names = sorted(os.listdir(str(released)))
    for board in build.BOARDS:
        assert "%s-%s" % (board["id"], board["images"][0]) in names
        assert "%s-source.json" % board["id"] in names
    src = json.load(open(os.path.join(str(released), "tdeck-source.json")))
    assert src["commit"] == ENV["GITHUB_SHA"]
    assert src["run_url"].endswith(ENV["GITHUB_RUN_ID"])
    assert src["built"].endswith("Z")


def test_round_trip_to_the_page_the_browser_reads(released, tmp_path):
    out = tmp_path / "ci-firmware"
    sys.argv =["fetch_ci_firmware.py", "--out", str(out), "--source", "release",
                "--repo", ENV["GITHUB_REPOSITORY"]]
    assert fetch.main() == 0

    # Unflattened: the per-board tree site/build.py expects.
    for board in build.BOARDS:
        image = out / board["id"] / board["images"][0]
        assert image.exists(), "release asset did not land as %s" % image
        assert image.read_bytes().startswith(b"IMAGE:" + board["id"].encode())
        src = json.load(open(str(out / board["id"] / "source.json")))
        assert src["commit"] == ENV["GITHUB_SHA"]

    site = tmp_path / "_site"
    sys.argv = ["build.py", "--out", str(site), "--no-player",
                "--firmware", str(out)]
    build.main()
    manifest = json.load(open(str(site / "firmware" / "manifest.json")))
    assert [b["id"] for b in manifest["boards"]] == [b["id"] for b in build.BOARDS]
    for entry, board in zip(manifest["boards"], build.BOARDS):
        assert entry["file"] == board["images"][0]
        assert entry["offset"] == board["offset"]
        assert entry["commit"] == ENV["GITHUB_SHA"]
        assert entry["size"] == os.path.getsize(str(site / entry["url"]))
    html = open(str(site / "index.html"), encoding="utf-8").read()
    assert ENV["GITHUB_SHA"][:7] in html          # the page names its build


def test_a_board_missing_from_the_release_is_left_alone(tmp_path, monkeypatch):
    """A single-board dispatch must not withdraw the other board's image."""
    artifacts = tmp_path / "artifacts"
    only = build.BOARDS[1]
    folder = artifacts / ("moybyte-firmware-%s" % only["id"])
    folder.mkdir(parents=True)
    (folder / only["images"][0]).write_bytes(b"one board only")
    release = tmp_path / "release"
    release.mkdir()
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    staged = publish.stage(build.BOARDS, str(artifacts), str(release))
    assert [b["id"] for b, _, _ in staged] == [only["id"]]
    # Nothing for the other board is staged, so `gh release upload` cannot
    # replace or remove what is already published for it.
    assert not [n for n in os.listdir(str(release))
                if n.startswith(build.BOARDS[0]["id"] + "-")]


def test_the_workflow_publishes_what_the_fetcher_looks_for():
    wf = open(os.path.join(ROOT, ".github", "workflows", "firmware-build.yml"),
              encoding="utf-8").read()
    assert "tools/publish_firmware_release.py" in wf
    assert "contents: write" in wf
    assert "pattern: moybyte-firmware-*" in wf
    assert publish.TAG == fetch.RELEASE_TAG
    pages = open(os.path.join(ROOT, ".github", "workflows", "pages.yml"),
                 encoding="utf-8").read()
    assert "tools/fetch_ci_firmware.py" in pages
    # The artifact fallback still needs its read scope.
    assert "actions: read" in pages
