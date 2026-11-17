"""The browser console's page where a release and a LAN mirror publish it.

The browser console is an add-on: an image names its page by digest, and a
console installs that page from beside the payload its channel offers
(moy_ota.page_url), whether or not anyone has installed it before. So a
release's manifest, the digest its image carries and the pack published
beside it have to be one page on every board and channel, and an earlier page
has to stay published for the images that still name it."""

import hashlib
import importlib.util
import json
import os
import stat
import sys
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_ota_manifest as gom                                    # noqa: E402
import gen_web_blob as gwb                                        # noqa: E402
import web_pack                                                   # noqa: E402


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


publish = _load("tools/publish_firmware_release.py", "_moy_publish_pages")
REPO = "moybyte-org/moybyte"


def _pack(folder, seed):
    """Write the build's pack into `folder` as gen_web_blob does; -> digest."""
    asset = folder / ("asset-%s" % seed)
    asset.write_bytes(bytes((seed * 31 + i) % 251 for i in range(3000 + seed)))
    data, digest = gwb.pack([("index.html.gz", str(asset), asset.stat().st_size, "-")])
    asset.unlink()
    (folder / web_pack.PACK_FILE).write_bytes(data)
    return digest


def _artifacts(root, board, seed=1, pack=True, named=True, channel="unstable",
               as_hex=False):
    """One board's artifacts folder as the build job uploads it: the app image
    carrying the pack's digest (its bytes, or `as_hex` their 64 hex), and the
    pack."""
    folder = root / ("moybyte-firmware-%s" % board)
    folder.mkdir(parents=True, exist_ok=True)
    digest = _pack(folder, seed) if pack else hashlib.sha256(b"other").digest()
    named_digest = digest if named else hashlib.sha256(b"stale").digest()
    if as_hex:
        named_digest = named_digest.hex().encode()
    (folder / publish.OTA_IMAGES[board]).write_bytes(
        b"\xe9APP:" + board.encode() + b"\0" * 64 + named_digest + b"\0" * 64)
    (folder / publish.OTA_STAMP).write_text(json.dumps(
        {"channel": channel, "version": 1754161200 + seed,
         "label": "beta", "board": board}))
    return digest


@pytest.fixture(autouse=True)
def _unsigned(monkeypatch):
    monkeypatch.delenv("MOYBYTE_OTA_SIGNING_KEY", raising=False)


def _device_page_url(monkeypatch, payload_url, want_hex):
    """What the device asks for: moy_ota.page_url under an image naming
    `want_hex`."""
    import moy_ota
    monkeypatch.setitem(sys.modules, "moy_web",
                        types.SimpleNamespace(page_want=lambda: want_hex))
    return moy_ota.page_url(payload_url)


def _addon(folder, board, index_url, digest):
    """Hold what a console installs the add-on from, in `folder`, to the page
    `digest` names, and read its index as the console's Get Carts does.
    -> the index's one entry, its URLs resolved."""
    from runtime import cart_index as ci
    page = (folder / (web_pack.PAGE_NAME % digest)).read_bytes()
    assert web_pack.digest_of(page) == digest
    cart = folder / (web_pack.CART_NAME % digest)
    with zipfile.ZipFile(str(cart)) as z:
        members = {n: z.read(n) for n in z.namelist()}
    top = web_pack.CART_FOLDER + "/"
    assert sorted(members) == [top + "LICENSE.md", top + "manifest.json",
                               top + web_pack.PACK_FILE]
    assert members[top + web_pack.PACK_FILE] == page
    manifest = json.loads(members[top + "manifest.json"])
    assert manifest["moybyte"]["fits"] == digest
    assert manifest["main"] == web_pack.PACK_FILE

    got = ci.parse_index((folder / (web_pack.INDEX_NAME % board)).read_bytes(),
                         index_url)
    assert [c["id"] for c in got] == [web_pack.CART_ID]
    entry = got[0]
    assert entry["moybyte"]["fits"] == digest
    asset, = entry["assets"]
    data = cart.read_bytes()
    assert (asset["size"], asset["sha256"]) == (len(data), hashlib.sha256(data).hexdigest())
    for name, meta in asset["files"].items():
        body = members[top + name]
        assert (meta["size"], meta["sha256"]) == (len(body), hashlib.sha256(body).hexdigest())
    lic = (folder / entry["licence"]["url"]).read_bytes()
    assert entry["licence"]["sha256"] == hashlib.sha256(lic).hexdigest()
    entry["asset_url"] = ci.resolve(index_url, asset["url"])
    return entry


def test_one_name_for_the_page_everywhere():
    import moy_ota
    assert web_pack.PAGE_NAME == moy_ota.PAGE_NAME
    build = (ROOT / "tools" / "esp32_build_lib.sh").read_text()
    assert "${DIST_DIR}/%s" % web_pack.PACK_FILE in build


def test_a_pack_is_checked_against_its_own_head(tmp_path):
    digest = _pack(tmp_path, 3)
    data = (tmp_path / web_pack.PACK_FILE).read_bytes()
    assert web_pack.digest_of(data) == digest.hex()
    flipped = bytearray(data)
    flipped[-5] ^= 1
    for bad in (bytes(flipped), data[:-4], b"NOTAPACK" + data[8:], data[:100]):
        with pytest.raises(web_pack.PageError):
            web_pack.digest_of(bad)


@pytest.mark.parametrize("channel", sorted(publish.CHANNELS))
def test_a_release_its_manifests_its_images_and_its_pages_agree(
        tmp_path, monkeypatch, channel):
    """On every channel, every board's manifest names the page its image
    carries the digest of; that page is published beside the payload, under
    the name a console installs it by; and its bytes hash to that digest."""
    artifacts = tmp_path / "artifacts"
    for i, board in enumerate(sorted(publish.OTA_IMAGES)):
        _artifacts(artifacts, board, seed=1, channel=channel, as_hex=i % 2)
    out = tmp_path / "release"
    out.mkdir()
    tag = publish.CHANNELS[channel]["tag"]
    got = publish.stage_ota_all(tag, channel, str(artifacts), str(out), repo=REPO)
    assert set(got) == set(publish.OTA_IMAGES)
    pages = [n for n in os.listdir(str(out)) if n.endswith(".pack")]
    assert len(pages) == 1                  # one bundle, one page, any number of boards
    for board, m in got.items():
        page = out / m["page"]
        digest = web_pack.digest_of(page.read_bytes())
        assert m["page"] == web_pack.PAGE_NAME % digest
        image = (out / m["filename"]).read_bytes()
        assert bytes.fromhex(digest) in image or digest.encode() in image
        assert hashlib.sha256((out / m["filename"]).read_bytes()).hexdigest() == m["sha256"]
        assert _device_page_url(monkeypatch, m["url"], digest) == \
            publish.asset_url(tag, m["page"], repo=REPO)
        on_disk = json.loads((out / publish.manifest_name(board)).read_text())
        assert on_disk["page"] == m["page"]
        entry = _addon(out, board, publish.asset_url(
            tag, web_pack.INDEX_NAME % board, repo=REPO), digest)
        assert entry["asset_url"] == publish.asset_url(tag, m["cart"], repo=REPO)
        assert entry["version"] == m["version"]


def test_a_board_whose_image_names_a_page_it_has_not_got_is_refused(
        tmp_path):
    artifacts = tmp_path / "artifacts"
    _artifacts(artifacts, "tdeck", pack=False)
    _artifacts(artifacts, "p4", named=False)
    _artifacts(artifacts, "guition_s3")
    out = tmp_path / "release"
    out.mkdir()
    refused = []
    got = publish.stage_ota_all("firmware-beta", "unstable", str(artifacts),
                                str(out), refused=refused)
    assert set(got) == {"guition_s3"}
    assert sorted(refused) == ["p4", "tdeck"]
    # Nothing of theirs is staged, so what is published for them stays.
    assert not [n for n in os.listdir(str(out)) if n.startswith(("tdeck", "p4"))]
    assert not (out / publish.manifest_name("tdeck")).exists()


FAKE_GH = '''#!/usr/bin/env python3
import glob, json, os, shutil, sys
argv = sys.argv[1:]
rel = os.environ["MOY_FAKE_RELEASE"]
if argv[:2] == ["release", "view"]:
    if not os.path.isdir(rel):
        sys.exit(1)
    print(json.dumps({"assets": [{"name": n} for n in sorted(os.listdir(rel))]}))
elif argv[:2] == ["release", "create"]:
    os.makedirs(rel)
elif argv[:2] == ["release", "upload"]:
    for f in argv[3:]:
        if not f.startswith("--"):
            shutil.copyfile(f, os.path.join(rel, os.path.basename(f)))
elif argv[:2] == ["release", "edit"]:
    pass
elif argv[:2] == ["release", "download"]:
    hit = glob.glob(os.path.join(rel, argv[argv.index("-p") + 1]))
    if not hit:
        sys.exit(1)
    dest = argv[argv.index("-D") + 1]
    for src in hit:
        shutil.copyfile(src, os.path.join(dest, os.path.basename(src)))
else:
    sys.stderr.write("unexpected gh call: %s\\n" % argv); sys.exit(2)
'''


def test_an_earlier_page_stays_published(tmp_path, monkeypatch):
    """Two publishes with two consoles: the first image's page is still there
    for a board rolled back to it, or given it by hand."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    release = tmp_path / "release"
    monkeypatch.setenv("MOY_FAKE_RELEASE", str(release))

    names = []
    for seed in (1, 2):
        artifacts = tmp_path / ("artifacts%d" % seed)
        digest = _artifacts(artifacts, "tdeck", seed=seed).hex()
        names.append(web_pack.PAGE_NAME % digest)
        monkeypatch.setattr(sys, "argv", ["publish", "--artifacts", str(artifacts),
                                          "--channel", "unstable"])
        assert publish.main() == 0
    assert names[0] != names[1]
    for name in names:
        assert (release / name).exists()
        assert (release / name.replace(".pack", ".moy.zip")).exists()
    m = json.loads((release / publish.manifest_name("tdeck")).read_text())
    assert m["page"] == names[1]
    _addon(release, "tdeck", "https://x/carts-tdeck.json", digest)


def test_a_signed_release_signs_the_index(tmp_path, monkeypatch):
    """The add-on's index is signed as a carts index is, and a console that
    trusts the key reads it."""
    from runtime import cart_index as ci, net_binding
    from test_pkg import _sign
    from test_ota_signing import TEST_KEYS
    monkeypatch.setattr(publish.ota_sign, "read_key", lambda: b"test key")
    monkeypatch.setattr(publish.ota_sign, "sign", lambda m, k: "00")
    monkeypatch.setattr(publish.ota_sign, "sign_index", lambda d, k: _sign(d))
    monkeypatch.setitem(sys.modules, "moy_net", net_binding)
    monkeypatch.setattr(ci, "INDEX_KEYS", TEST_KEYS)
    artifacts = tmp_path / "artifacts"
    digest = _artifacts(artifacts, "tdeck").hex()
    out = tmp_path / "release"
    out.mkdir()
    publish.stage_ota("firmware-beta", "unstable", str(artifacts), str(out),
                      board="tdeck")
    assert b'"sig": "' in (out / "carts-tdeck.json").read_bytes()
    _addon(out, "tdeck", "https://x/carts-tdeck.json", digest)
    assert ci.check_signature((out / "carts-tdeck.json").read_bytes(), "x") is True


def test_a_refused_page_fails_the_publish(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    _artifacts(artifacts, "tdeck", pack=False)
    monkeypatch.setattr(sys, "argv", ["publish", "--artifacts", str(artifacts),
                                      "--channel", "unstable", "--dry-run"])
    assert publish.main() == 1


def test_the_build_job_carries_the_pack_to_the_publisher():
    wf = (ROOT / ".github" / "workflows" / "firmware-build.yml").read_text()
    collect = wf.split("- name: Collect images", 1)[1].split("- name:", 1)[0]
    assert "dist/*/%s out/" % web_pack.PACK_FILE in collect


# -- the LAN mirror ------------------------------------------------------------


def _built(tmp_path, seed=1):
    """dist/<board>/ as a build leaves it: the app image and the pack."""
    d = tmp_path / ("dist%d" % seed)
    d.mkdir()
    digest = _pack(d, seed)
    app = d / "moybyte_tdeck_app.bin"
    app.write_bytes(b"\xe9" + b"\0" * 100 + digest + b"\0" * 100)
    return app, digest.hex()


def test_the_mirror_puts_the_page_beside_the_payload(tmp_path, monkeypatch):
    root = tmp_path / "ota"
    seen = []
    for seed in (1, 2):
        app, digest = _built(tmp_path, seed)
        seen.append(web_pack.PAGE_NAME % digest)
        assert gom.main([str(app), "--root", str(root), "--channel", "unstable",
                         "--base-url", "http://h:8000"]) == 0
    chan = root / "unstable"
    man = json.loads((chan / "latest.json").read_text())
    assert man["page"] == seen[1]
    for name in seen:                       # the earlier add-on stays served
        assert (chan / name).exists()
        assert (chan / name.replace(".pack", ".moy.zip")).exists()
    entry = _addon(chan, "tdeck", "http://h:8000/unstable/carts-tdeck.json", digest)
    assert entry["asset_url"] == "http://h:8000/unstable/" + man["cart"]
    assert web_pack.digest_of((chan / seen[1]).read_bytes()) in seen[1]
    assert _device_page_url(monkeypatch, man["url"], digest) == \
        "http://h:8000/unstable/" + seen[1]


def test_a_card_mirror_serves_the_page_from_the_build_folder(tmp_path):
    app, digest = _built(tmp_path)
    assert gom.main([str(app), "--base-url", "http://h:8000"]) == 0
    man = json.loads((app.parent / "latest.json").read_text())
    assert man["url"] == "http://h:8000/" + app.name
    assert man["page"] == web_pack.PAGE_NAME % digest
    _addon(app.parent, "tdeck", "http://h:8000/carts-tdeck.json", digest)


def test_the_mirror_refuses_an_image_without_its_page(tmp_path):
    app, _digest = _built(tmp_path)
    (app.parent / web_pack.PACK_FILE).unlink()
    with pytest.raises(SystemExit):
        gom.main([str(app), "--root", str(tmp_path / "ota"), "--base-url", "http://h"])
    assert not (tmp_path / "ota").exists()
