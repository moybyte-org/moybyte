"""The browser console add-on where a release and a LAN mirror publish it.

An image names its page by digest, and a console installs the add-on cart
whose index entry fits that digest, from the index beside the payload its
channel offers, whether or not anyone has installed it before. So a release's
manifest, the digest its image carries, the cart and the index have to be one
page on every board and channel, and every earlier cart has to stay published
and listed for the images that still name it."""

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
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
    image = publish.OTA_IMAGES.get(board) or publish.NO_OTA[board]
    (folder / image).write_bytes(
        b"\xe9APP:" + board.encode() + b"\0" * 64 + named_digest + b"\0" * 64)
    (folder / publish.OTA_STAMP).write_text(json.dumps(
        {"channel": channel, "version": 1754161200 + seed,
         "label": "beta", "board": board}))
    return digest


@pytest.fixture(autouse=True)
def _unsigned(monkeypatch):
    monkeypatch.delenv("MOYBYTE_OTA_SIGNING_KEY", raising=False)


def _addon(folder, board, index_url, digest):
    """Hold the add-on cart of the page `digest`, in `folder`, to that page,
    and find it in the board's index as the console's Get Carts reads it:
    the entry whose moybyte.fits is the digest. -> (that entry, its URL
    resolved; every entry's digest, in the index's order)."""
    from runtime import cart_index as ci
    cart = folder / (web_pack.CART_NAME % digest)
    with zipfile.ZipFile(str(cart)) as z:
        members = {n: z.read(n) for n in z.namelist()}
    top = web_pack.cart_id(digest) + ".moy/"
    assert sorted(members) == [top + "LICENSE.md", top + "manifest.json",
                               top + web_pack.PACK_FILE]
    assert web_pack.digest_of(members[top + web_pack.PACK_FILE]) == digest
    manifest = json.loads(members[top + "manifest.json"])
    assert manifest["moybyte"]["fits"] == digest
    assert manifest["main"] == web_pack.PACK_FILE

    got = ci.parse_index((folder / (web_pack.INDEX_NAME % board)).read_bytes(),
                         index_url)
    assert len({c["id"] for c in got}) == len(got)           # an id once
    entry, = [c for c in got if c["moybyte"]["fits"] == digest]
    assert entry["id"] == web_pack.cart_id(digest)
    assert entry["folder"] == entry["id"] + ".moy"
    asset, = entry["assets"]
    data = cart.read_bytes()
    assert (asset["size"], asset["sha256"]) == (len(data), hashlib.sha256(data).hexdigest())
    for name, meta in asset["files"].items():
        body = members[top + name]
        assert (meta["size"], meta["sha256"]) == (len(body), hashlib.sha256(body).hexdigest())
    lic = (folder / entry["licence"]["url"]).read_bytes()
    assert entry["licence"]["sha256"] == hashlib.sha256(lic).hexdigest()
    return ci.resolve(index_url, asset["url"]), [c["moybyte"]["fits"] for c in got]


def test_the_build_writes_the_pack_the_publisher_reads():
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
def test_a_release_its_manifests_its_images_and_its_carts_agree(tmp_path, channel):
    """On every channel, every board's manifest names the add-on cart of the
    page its image carries the digest of, and the board's index lists that
    cart, under that digest, where the manifest's payload is."""
    artifacts = tmp_path / "artifacts"
    for i, board in enumerate(sorted(publish.OTA_IMAGES)):
        _artifacts(artifacts, board, seed=1, channel=channel, as_hex=i % 2)
    out = tmp_path / "release"
    out.mkdir()
    tag = publish.CHANNELS[channel]["tag"]
    got = publish.stage_ota_all(tag, channel, str(artifacts), str(out), repo=REPO)
    assert set(got) == set(publish.OTA_IMAGES)
    carts = [n for n in os.listdir(str(out)) if n.endswith(".moy.zip")]
    assert len(carts) == 1                  # one bundle, one cart, any number of boards
    assert not [n for n in os.listdir(str(out)) if n.endswith(".pack")]
    for board, m in got.items():
        image = (out / m["filename"]).read_bytes()
        assert hashlib.sha256(image).hexdigest() == m["sha256"]
        digest = m["cart"][len("moybyte_web-"):-len(".moy.zip")]
        assert m["cart"] == web_pack.CART_NAME % digest
        assert bytes.fromhex(digest) in image or digest.encode() in image
        assert "page" not in m
        on_disk = json.loads((out / publish.manifest_name(board)).read_text())
        assert on_disk["cart"] == m["cart"]
        index_url = publish.asset_url(tag, web_pack.INDEX_NAME % board, repo=REPO)
        url, fits = _addon(out, board, index_url, digest)
        assert url == publish.asset_url(tag, m["cart"], repo=REPO)
        assert url.rsplit("/", 1)[0] == m["url"].rsplit("/", 1)[0]
        assert fits == [digest]


def test_a_board_with_no_ota_gets_its_add_on_and_no_manifest(tmp_path):
    """A one-slot board (the C3 Zero) installs its browser console by cable
    from the release's index, so the release carries its cart and index
    though no manifest can name an image for it."""
    artifacts = tmp_path / "artifacts"
    board = sorted(publish.NO_OTA)[0]
    digest = _artifacts(artifacts, board, seed=3).hex()
    out = tmp_path / "release"
    out.mkdir()
    tag = publish.CHANNELS["unstable"]["tag"]
    got = publish.stage_ota_all(tag, "unstable", str(artifacts), str(out), repo=REPO)
    assert board not in got
    assert not (out / publish.manifest_name(board)).exists()
    index_url = publish.asset_url(tag, web_pack.INDEX_NAME % board, repo=REPO)
    url, fits = _addon(out, board, index_url, digest)
    assert url == publish.asset_url(tag, web_pack.CART_NAME % digest, repo=REPO)
    assert fits == [digest]


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
elif argv[:2] == ["release", "delete-asset"] and "--yes" in argv:
    os.remove(os.path.join(rel, argv[3]))
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


def _fake_gh(tmp_path, monkeypatch):
    """`gh` on PATH as FAKE_GH, over a release in a folder: -> the folder."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    release = tmp_path / "release"
    monkeypatch.setenv("MOY_FAKE_RELEASE", str(release))
    return release


def test_every_add_on_published_stays_in_the_index(tmp_path, monkeypatch):
    """Three publishes, two consoles: both carts stay on the release, and
    the board's index lists each once, newest first, so a console a step
    behind its channel still finds the one its image fits."""
    release = _fake_gh(tmp_path, monkeypatch)

    digests = []
    for n, seed in enumerate((1, 2, 2)):
        artifacts = tmp_path / ("artifacts%d" % n)
        digests.append(_artifacts(artifacts, "tdeck", seed=seed).hex())
        monkeypatch.setattr(sys, "argv", ["publish", "--artifacts", str(artifacts),
                                          "--channel", "unstable"])
        assert publish.main() == 0
    old, new = digests[0], digests[1]
    assert old != new == digests[2]
    m = json.loads((release / publish.manifest_name("tdeck")).read_text())
    assert m["cart"] == web_pack.CART_NAME % new
    for digest in (old, new):
        url, fits = _addon(release, "tdeck", "https://x/carts-tdeck.json", digest)
        assert url == "https://x/" + web_pack.CART_NAME % digest
        assert fits == [new, old]


def test_a_publish_deletes_the_official_carts_no_index_needs(tmp_path, monkeypatch,
                                                              capsys):
    """Each changed seed cart is a new asset. A publish keeps every one an
    index on the release references, now or before this publish, and
    deletes the rest; the add-on carts all stay, and a dry run says what
    it would delete and deletes nothing."""
    release = _fake_gh(tmp_path, monkeypatch)
    run = [0]
    real = web_pack.seed_files

    def seed_files(src):
        files = real(src)
        if os.path.basename(src) == "moybyte.star_catcher.moy":
            files["main.py"] += b"\n# build %d\n" % run[0]
        return files
    monkeypatch.setattr(web_pack, "seed_files", seed_files)

    def caught(board):
        doc = json.loads((release / (web_pack.INDEX_NAME % board)).read_text())
        entry, = [c for c in doc["carts"] if c["id"] == "moybyte.star_catcher"]
        return entry["assets"][0]["url"]

    def official():
        return {n for n in os.listdir(str(release)) if web_pack.OFFICIAL_ASSET.match(n)}

    def publish_run(n, boards, *flags):
        run[0] = n
        artifacts = tmp_path / ("artifacts%d" % n)
        for board in boards:
            _artifacts(artifacts, board, seed=n + 1)
        monkeypatch.setattr(sys, "argv", ["publish", "--artifacts", str(artifacts),
                                          "--channel", "unstable"] + list(flags))
        assert publish.main() == 0

    publish_run(0, ("tdeck", "p4"))
    z = {0: caught("tdeck")}
    assert caught("p4") == z[0]
    others = official() - {z[0]}
    assert len(others) > 10 and any(n.endswith(".png") for n in others)
    for n in (1, 2):
        publish_run(n, ("tdeck",))
        z[n] = caught("tdeck")
    # p4's index still names the first, tdeck's had the one before its own
    assert official() == others | {z[0], z[1], z[2]}
    publish_run(3, ("tdeck", "p4"))
    z[3] = caught("tdeck")
    assert caught("p4") == z[3]
    assert official() == others | {z[0], z[2], z[3]}
    addons = [n for n in os.listdir(str(release)) if n.startswith("moybyte_web-")
              and n.endswith(".moy.zip")]
    assert len(addons) == 4, "every add-on stays"
    capsys.readouterr()
    publish_run(4, ("tdeck",), "--dry-run")
    said = capsys.readouterr().out
    assert sorted(ln.split()[-1] for ln in said.splitlines()
                  if ln.startswith("would delete")) == sorted([z[0], z[2]])
    assert official() == others | {z[0], z[2], z[3]}


def test_an_earlier_signed_index_keeps_its_entries(tmp_path):
    first = tmp_path / "a"
    first.mkdir()
    digest = _pack(first, 1).hex()
    page = (str(first / web_pack.PACK_FILE), digest)
    web_pack.publish_addon(page, str(first), "p4", 7)
    signed = (first / "carts-p4.json").read_bytes().rstrip()[:-1] + b', "sig": "00"}\n'
    second = tmp_path / "b"
    second.mkdir()
    other = _pack(second, 2).hex()
    web_pack.publish_addon((str(second / web_pack.PACK_FILE), other), str(second),
                           "p4", 8, earlier=signed)
    doc = json.loads((second / "carts-p4.json").read_text())
    addons = [c for c in doc["carts"] if c["runtime"] == web_pack.CART_RUNTIME]
    assert [c["moybyte"]["fits"] for c in addons] == [other, digest]
    assert [c["version"] for c in addons] == [8, 7]
    official = [c for c in doc["carts"] if c not in addons]
    assert len({c["id"] for c in official}) == len(official) and official, \
        "the official entries are this build's alone"
    assert {c["moybyte"]["api"] for c in official} == {web_pack.API_LEVEL}
    assert not any("fits" in c["moybyte"] for c in official), \
        "an official entry names its API level, not a page"


def test_a_signed_release_signs_the_index(tmp_path, monkeypatch):
    """The add-on's index is signed as a carts index is, and a console that
    trusts the key reads it."""
    from runtime import cart_index as ci, net_binding
    from test_pkg import _sign
    from test_ota_signing import TEST_KEYS
    monkeypatch.setattr(publish.ota_sign, "read_key", lambda: b"test key")
    monkeypatch.setattr(publish.ota_sign, "sign", lambda m, k: "00")
    monkeypatch.setattr(publish.ota_sign, "sign_index", lambda d, k: _sign(d))
    # the compiled seed carts' modules go unsigned: the stand-in key is no PEM
    from tools import wasm_module
    monkeypatch.setattr(wasm_module, "signing_key", lambda source=None: None)
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
    # nothing published to read back: the release is never asked
    monkeypatch.setattr(publish, "gh", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    monkeypatch.setattr(sys, "argv", ["publish", "--artifacts", str(artifacts),
                                      "--channel", "unstable", "--dry-run"])
    assert publish.main() == 1


def test_the_build_job_carries_the_pack_to_the_publisher():
    wf = (ROOT / ".github" / "workflows" / "firmware-build.yml").read_text()
    collect = wf.split("- name: Collect images", 1)[1].split("- name:", 1)[0]
    assert "dist/*/%s out/" % web_pack.PACK_FILE in collect


# -- the official compiled carts' native modules ------------------------------

SAKURA = "moybyte.sakura_c"
FAKE_MODULE = {"esp32s3": b"\x7fELF s3 module " * 40, "esp32p4": b"\x7fELF p4 module " * 50}


def _module(artifacts, board, chip):
    """A board build's native module of Sakura C, as board_modules names it."""
    folder = artifacts / ("moybyte-firmware-%s" % board)
    name = web_pack.NATIVE_ASSET % (SAKURA, "main.%s.elf" % chip)
    (folder / name).write_bytes(FAKE_MODULE[chip])


def _sakura(out, board):
    """(index entry, {file: bytes} of the zip) of Sakura C in the board's
    index in the release folder `out`."""
    doc = json.loads((out / (web_pack.INDEX_NAME % board)).read_text())
    entry, = [c for c in doc["carts"] if c["id"] == SAKURA]
    asset, = entry["assets"]
    data = (out / asset["url"]).read_bytes()
    assert (asset["size"], asset["sha256"]) == (len(data), hashlib.sha256(data).hexdigest())
    top = SAKURA + ".moy/"
    with zipfile.ZipFile(str(out / asset["url"])) as z:
        files = {n[len(top):]: z.read(n) for n in z.namelist()}
    for name, meta in asset["files"].items():
        assert (meta["size"], meta["sha256"]) == (
            len(files[name]), hashlib.sha256(files[name]).hexdigest())
    assert sorted(asset["files"]) == sorted(files)
    return entry, files


@pytest.fixture
def _no_aot(tmp_path, monkeypatch):
    from tools import wasm_module
    monkeypatch.setattr(wasm_module, "DIST", str(tmp_path / "no-wamrc"))
    return wasm_module


def test_a_release_carries_the_module_each_chip_s_board_built(tmp_path, monkeypatch, _no_aot):
    """Two S3 boards and a P4 built; the publish puts each chip's module,
    signed, into the one Sakura C every board's index lists, and its
    `native` names both chips."""
    from test_ota_signing import TEST_KEYS
    from test_wasm_signing import sign_with_test_key
    from tools import elf_cart
    monkeypatch.setattr(_no_aot, "signing_key", lambda source=None: sign_with_test_key)
    artifacts = tmp_path / "artifacts"
    boards = ("tdeck", "guition_s3", "p4")
    for board in boards:
        _artifacts(artifacts, board)
    for board, chip in (("tdeck", "esp32s3"), ("guition_s3", "esp32s3"), ("p4", "esp32p4")):
        _module(artifacts, board, chip)
    out = tmp_path / "release"
    out.mkdir()
    assert set(publish.stage_ota_all("firmware-beta", "unstable", str(artifacts),
                                     str(out), repo=REPO)) == set(boards)
    urls = set()
    for board in boards:
        entry, files = _sakura(out, board)
        urls.add(entry["assets"][0]["url"])
        assert json.loads(files["manifest.json"])["moybyte"]["native"] == ["esp32s3", "esp32p4"]
        assert "main.wasm" in files
        for chip, body in FAKE_MODULE.items():
            data = files[elf_cart.module_name(chip)]
            module, sig = _no_aot.split(data)
            assert module == body and sig
            assert _no_aot.verify(data, chip, TEST_KEYS, scheme=elf_cart.sig_scheme()) is None
    assert len(urls) == 1                   # one cart, one asset, on every board's index
    assert not [n for n in os.listdir(str(out)) if n.endswith(".elf")]


def test_a_chip_whose_board_built_no_module_has_none(tmp_path, monkeypatch, _no_aot, capsys):
    """The P4's build left no module: the cart has the S3's alone, as it was
    built (no key here), and a P4 runs its wasm one."""
    monkeypatch.setattr(_no_aot, "signing_key", lambda source=None: None)
    artifacts = tmp_path / "artifacts"
    for board in ("tdeck", "p4"):
        _artifacts(artifacts, board)
    _module(artifacts, "tdeck", "esp32s3")
    out = tmp_path / "release"
    out.mkdir()
    publish.stage_ota_all("firmware-beta", "unstable", str(artifacts), str(out), repo=REPO)
    assert "native %s: main.esp32s3.elf" % SAKURA in capsys.readouterr().out
    for board in ("tdeck", "p4"):
        _entry, files = _sakura(out, board)
        assert json.loads(files["manifest.json"])["moybyte"]["native"] == ["esp32s3"]
        assert files["main.esp32s3.elf"] == FAKE_MODULE["esp32s3"]
        assert "main.esp32p4.elf" not in files and "main.wasm" in files


def test_a_run_with_no_modules_publishes_the_wasm_cart_alone(tmp_path, _no_aot):
    artifacts = tmp_path / "artifacts"
    _artifacts(artifacts, "tdeck")
    out = tmp_path / "release"
    out.mkdir()
    publish.stage_ota_all("firmware-beta", "unstable", str(artifacts), str(out), repo=REPO)
    _entry, files = _sakura(out, "tdeck")
    assert json.loads(files["manifest.json"])["moybyte"]["native"] == []
    assert not [n for n in files if n.endswith(".elf")]


def test_a_board_build_makes_its_chip_s_modules_unsigned(tmp_path):
    from tools import elf_cart, wasm_module
    assert web_pack.main(["modules", str(ROOT / "firmware" / "esp32c3_zero"),
                          str(tmp_path / "c3")]) == 0
    assert not (tmp_path / "c3").exists()   # no native tier on the C3
    chip = next((c for c in web_pack.BOARD_CHIPS if elf_cart.available(c)), None)
    if chip is None:
        pytest.skip("no ESP toolchain here")
    got = web_pack.board_modules(chip, str(tmp_path / "out"))
    assert got == [web_pack.NATIVE_ASSET % (SAKURA, "main.%s.elf" % chip)]
    data = (tmp_path / "out" / got[0]).read_bytes()
    assert data[:4] == b"\x7fELF" and wasm_module.split(data)[1] is None
    assert web_pack.collected_modules([str(tmp_path / "out")]) == {
        SAKURA: {"main.%s.elf" % chip: str(tmp_path / "out" / got[0])}}


def test_the_build_job_builds_the_modules_and_carries_them():
    wf = (ROOT / ".github" / "workflows" / "firmware-build.yml").read_text()
    step = wf.split("- name: The official compiled carts' native modules", 1)[1]
    upload = step.split("- name: Upload firmware artifacts", 1)
    assert len(upload) == 2                 # before the upload, which carries out/*
    assert "tools/web_pack.py modules ${{ matrix.dir }} out" in upload[0]
    assert "continue-on-error: true" in upload[0]


# -- the LAN mirror ------------------------------------------------------------


def _built(tmp_path, seed=1):
    """dist/<board>/ as a build leaves it: the app image and the pack."""
    d = tmp_path / ("dist%d" % seed)
    d.mkdir()
    digest = _pack(d, seed)
    app = d / "moybyte_tdeck_app.bin"
    app.write_bytes(b"\xe9" + b"\0" * 100 + digest + b"\0" * 100)
    return app, digest.hex()


def test_the_mirror_puts_the_add_on_beside_the_payload(tmp_path):
    root = tmp_path / "ota"
    digests = []
    for seed in (1, 2):
        app, digest = _built(tmp_path, seed)
        digests.append(digest)
        assert gom.main([str(app), "--root", str(root), "--channel", "unstable",
                         "--base-url", "http://h:8000"]) == 0
    chan = root / "unstable"
    man = json.loads((chan / "latest.json").read_text())
    assert man["cart"] == web_pack.CART_NAME % digests[1]
    assert not [n for n in os.listdir(str(chan)) if n.endswith(".pack")]
    for digest in digests:                  # the earlier add-on stays served and listed
        url, fits = _addon(chan, "tdeck", "http://h:8000/unstable/carts-tdeck.json",
                           digest)
        assert url == "http://h:8000/unstable/" + web_pack.CART_NAME % digest
        assert fits == digests[::-1]


def test_a_card_mirror_serves_the_add_on_from_the_build_folder(tmp_path):
    app, digest = _built(tmp_path)
    assert gom.main([str(app), "--base-url", "http://h:8000"]) == 0
    man = json.loads((app.parent / "latest.json").read_text())
    assert man["url"] == "http://h:8000/" + app.name
    url, _fits = _addon(app.parent, "tdeck", "http://h:8000/carts-tdeck.json", digest)
    assert url == "http://h:8000/" + man["cart"]


def test_the_mirror_refuses_an_image_without_its_page(tmp_path):
    app, _digest = _built(tmp_path)
    (app.parent / web_pack.PACK_FILE).unlink()
    with pytest.raises(SystemExit):
        gom.main([str(app), "--root", str(tmp_path / "ota"), "--base-url", "http://h"])
    assert not (tmp_path / "ota").exists()
