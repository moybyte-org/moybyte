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


# -- the two channels are the two branches ----------------------------------
#
# `dev` publishes beta images and `master` publishes the ones people get. The
# tests below are about the seam where that can silently break: an image that
# introduces itself with the wrong channel, a manifest advertising a version its
# image does not carry (which offers the same install forever), or a beta asset
# reaching the site's flasher.

STAMP = {"channel": "unstable", "version": 1754161200, "label": "beta 2026-08-02 18:00"}


def _load_moy_ota():
    return _load("firmware/lilygo_t_deck_plus_micropython/modules/moy_ota.py",
                 "_moy_ota_device")


def _artifacts_with_ota(tmp_path, stamp=STAMP, board="tdeck", app=True):
    """An artifacts folder as the build job uploads it: the flashable image, the
    OTA app image, and build.sh's identity stamp beside them."""
    folder = tmp_path / "artifacts" / ("moybyte-firmware-%s" % board)
    folder.mkdir(parents=True)
    spec = next(b for b in build.BOARDS if b["id"] == board)
    (folder / spec["images"][0]).write_bytes(b"IMAGE:" + board.encode())
    if app:
        (folder / publish.OTA_IMAGES[board]).write_bytes(
            b"\xe9APP:" + board.encode() * 8)
    if stamp is not None:
        (folder / publish.OTA_STAMP).write_text(json.dumps(dict(stamp, board=board)))
    return tmp_path / "artifacts"


def test_each_channel_rolls_its_own_release():
    assert publish.CHANNELS["stable"]["tag"] == fetch.RELEASE_TAG
    assert publish.CHANNELS["unstable"]["tag"] != fetch.RELEASE_TAG
    # ... which is what keeps a dev build off the site: the fetcher (and so the
    # page's flasher) only ever reads the stable tag.
    assert publish.CHANNELS["stable"]["branch"] == "master"
    assert publish.CHANNELS["unstable"]["branch"] == "dev"


def test_the_ota_manifest_describes_the_app_image_not_the_flashed_one(tmp_path):
    """An OTA writes the app slot; the site flashes the whole chip at 0x0. The
    manifest must point at the former -- handing a device a 0x0 image would
    write a bootloader into an app partition."""
    artifacts = _artifacts_with_ota(tmp_path)
    out = tmp_path / "release"
    out.mkdir()
    manifest = publish.stage_ota("firmware-beta", "unstable", str(artifacts),
                                 str(out), repo="moybyte-org/moybyte")
    name = "tdeck-%s" % publish.OTA_IMAGES["tdeck"]
    assert manifest["url"].endswith("/firmware-beta/" + name)
    assert manifest["filename"] == name
    assert (out / name).exists()
    assert (out / publish.manifest_name("tdeck")).exists()
    # size + sha256 are of the bytes actually staged, not of anything remembered.
    payload = (out / name).read_bytes()
    assert manifest["size"] == len(payload)
    on_disk = json.load(open(str(out / publish.manifest_name("tdeck"))))
    assert on_disk == manifest

    flashed = next(b for b in build.BOARDS if b["id"] == "tdeck")["images"][0]
    assert publish.OTA_IMAGES["tdeck"] != flashed


def test_the_manifest_version_is_the_one_baked_into_the_image(tmp_path):
    """A beta's version is a build-time epoch. Re-deriving it at publish time
    would advertise a number the image does not carry, so every check after the
    install would offer that same install again."""
    artifacts = _artifacts_with_ota(tmp_path)
    out = tmp_path / "release"
    out.mkdir()
    manifest = publish.stage_ota("firmware-beta", "unstable", str(artifacts),
                                 str(out))
    assert manifest["version"] == STAMP["version"]
    assert manifest["label"] == STAMP["label"]
    assert manifest["channel"] == "unstable"


def test_the_image_says_which_channel_it_is(tmp_path, capsys):
    """Publisher and image disagreeing means the build was stamped for the other
    channel. The image is what the device will run, so it wins -- loudly."""
    artifacts = _artifacts_with_ota(tmp_path)          # stamped unstable
    out = tmp_path / "release"
    out.mkdir()
    manifest = publish.stage_ota("firmware-latest", "stable", str(artifacts),
                                 str(out))
    assert manifest["channel"] == "unstable"
    assert "WARNING" in capsys.readouterr().out


def test_a_board_without_an_app_image_leaves_its_manifest_alone(tmp_path):
    """Same rule as an unbuilt board's flashable image: publish nothing rather
    than something stale."""
    artifacts = _artifacts_with_ota(tmp_path, stamp=None, app=False)
    out = tmp_path / "release"
    out.mkdir()
    assert publish.stage_ota("firmware-latest", "stable", str(artifacts),
                             str(out)) is None
    assert os.listdir(str(out)) == []


def test_each_board_gets_its_own_manifest_and_payload(tmp_path):
    """An OTA payload is an app-partition image -- Xtensa on the T-Deck, RISC-V
    on the P4 -- so one manifest per board, named for it, and the board is IN
    the manifest (and inside the signature) so neither can be served as the
    other."""
    artifacts = _artifacts_with_ota(tmp_path)
    _artifacts_with_ota(tmp_path, board="p4")
    out = tmp_path / "release"
    out.mkdir()
    got = publish.stage_ota_all("firmware-beta", "unstable", str(artifacts),
                                str(out))
    assert set(got) == {"tdeck", "p4"}
    for board, manifest in got.items():
        assert manifest["board"] == board
        assert (out / publish.manifest_name(board)).exists()
        assert manifest["filename"] == "%s-%s" % (board, publish.OTA_IMAGES[board])
    assert got["tdeck"]["sha256"] != got["p4"]["sha256"]


def test_the_device_looks_where_ci_publishes():
    """moy_ota's baked defaults and the publisher's asset URLs are two halves of
    one contract, written in two languages and never executed together."""
    moy_ota = _load_moy_ota()
    assert set(moy_ota.DEFAULT_CHANNEL_RELEASES) == set(publish.CHANNELS)
    for channel, spec in publish.CHANNELS.items():
        for board in publish.OTA_IMAGES:
            want = publish.asset_url(spec["tag"], publish.manifest_name(board),
                                     repo="moybyte-org/moybyte")
            got = moy_ota.default_manifest_url(channel, board)
            assert got == want, (channel, board)


def test_the_workflows_keep_the_branches_apart():
    wf = open(os.path.join(ROOT, ".github", "workflows", "firmware-build.yml"),
              encoding="utf-8").read()
    # The channel is derived from the ref, so a dev build cannot introduce
    # itself to a device as a stable release.
    assert "MOYBYTE_OTA_CHANNEL: ${{ github.ref == 'refs/heads/master' " \
           "&& 'stable' || 'unstable' }}" in wf
    assert "--channel \"$CHANNEL\"" in wf
    # Both branches build; only those two publish.
    assert "branches: [master, dev]" in wf
    assert "github.ref == 'refs/heads/master' || github.ref == 'refs/heads/dev'" in wf
    # The stamp has to reach the publish job or the manifest version is a guess.
    assert publish.OTA_STAMP in wf
    assert "path: out/*\n" in wf
    # EVERY board's stamp, not just the one whose dist/ lives under firmware/.
    # Collecting only the T-Deck's published a P4 beta whose manifest said
    # "unstable" over an image stamped "stable v2" -- so a P4 on the beta
    # channel was offered that same install on every check, forever.
    for board, path in (("tdeck", "firmware/lilygo_t_deck_plus_micropython/dist/current"),
                        ("p4", "dist/p4")):
        assert "cp %s/%s out/" % (path, publish.OTA_STAMP) in wf, board

    ci = open(os.path.join(ROOT, ".github", "workflows", "ci.yml"),
              encoding="utf-8").read()
    assert '"master", "main", "dev"' in ci     # the untested branch is tested

    pages = open(os.path.join(ROOT, ".github", "workflows", "pages.yml"),
                 encoding="utf-8").read()
    # The public site follows master only -- a beta build must not republish it.
    assert "github.event.workflow_run.head_branch == 'master'" in pages
    assert "branches: [master]" in pages
