"""Manifest signing: the thing standing between a board and a forged firmware.

The device's TLS does not verify certificates, so anyone who can answer for
github.com on the kid's network can serve their own image -- and the manifest's
sha256 is no defence, because the same attacker writes the manifest. What
answers it is a signature over the manifest, checked against a public key baked
into the image the owner flashed over a cable.

The VERIFIER is the security-critical half and it lives on the device, in
MicroPython, where there is no crypto library -- just `pow` and a byte compare.
So the tests carry their own 2048-bit key and sign with `pow(m, d, n)` rather
than reaching for `cryptography`, which means the whole round trip runs in CI
(where only the dev extra is installed) and not merely on a release machine.

`cryptography` is needed for real KEYGEN and for signing with a PEM, and those
two tests skip without it.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ota_sign  # noqa: E402

# A THROWAWAY key, generated for this file and published in it on purpose --
# never used to sign anything a device will see. The real public key lives in
# moy_ota.OTA_PUBLIC_KEYS and its private half never leaves the maintainer's
# GitHub secret.
TEST_N = int(
    "c395caa5f0e155f0b50c0ab75642b3b9d71586c578aeb48bc15bebb630"
    "cf33f4dc9d0078f245a9b6a1fc300a75296b5111f6757e1aec5502eda3"
    "1c0474b4ef1316f922a6f3974d2df162d512bb07d14b310b9f6b36a6c8"
    "c29027953664d0ad458f98d83cbe1ad22175370c828141ffe9d844ad5c"
    "bcba7899a89e8b721e53d6bf88d7f90fd6238b844028e642eca990d5be"
    "4c5071454299e1eccee2b22012a47caff6e9b165078acfd203161e519a"
    "21083756ae255e5bee487831c126ea349754b490a5ae0a20b7a66ab3a7"
    "8f672efbcd038a946b228dd10a47a11a76cbd98168223263897161aa9a"
    "eb9a6b5c1e0a9388216020ba2aab9de86e2a2ddf3dc29b59", 16)
TEST_D = int(
    "5e3fa788b17c14aacac3c3c2374a2b4b698f1103c5b50281ba2aae7a7c"
    "28cd13b8dfdb636cf40ee55847ab6aceaca7ef4825a8d69ce8b7ca9273"
    "2044316d232be2cd295aa4558bb690f49c52cb57e80e40d325fe4736b5"
    "d5b41baef6a83c3ad3237076fb466cac47bd314ad0f4b2b63c9c9ff39e"
    "95bf91f011e65cb220552c4d065dcc972974ddc4b8c621e9384210890d"
    "94278f381ee3bc85851f6eae1177227fdc9a5788f2610cdbcb5e801b00"
    "36eaddb2d9a4c78f2ba6c9e04324267b622ac4202486b614aa4ce3be6f"
    "1c2ba3c19cfa3e8ff029fcd970a60fb6d6370384b174eb87d32a79396b"
    "7eac01a8f7f8ce87697c73f45f4d279b9f1a5d4e74630ad5", 16)
TEST_KEYS = (("%x" % TEST_N, 65537),)

MANIFEST = {
    "board": "tdeck",
    "channel": "stable",
    "version": 3,
    "size": 4290656,
    "sha256": "166428828e3c5d8576e9a4c43a8a1c02f570e57c427bc399eabf7c24f222d0a5",
    "url": "https://github.com/moybyte-org/moybyte/releases/download/x/app.bin",
    "label": "v3",
}


def sign_with_test_key(manifest, n=TEST_N, d=TEST_D):
    """RSA signing IS modexp once you have the private exponent, so this needs
    no library -- which is what lets the round trip run in plain CI."""
    digest = hashlib.sha256(ota_sign.canonical(manifest)).digest()
    block = ota_sign.pkcs1_v15_block(digest, (n.bit_length() + 7) // 8)
    return "%x" % pow(int.from_bytes(block, "big"), d, n)


def _load_moy_ota():
    p = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules" / "moy_ota.py"
    spec = importlib.util.spec_from_file_location("moy_ota_signing", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def device():
    m = _load_moy_ota()
    return m, m.OtaUpdater(with_sd=lambda fn: fn())


def signed(**overrides):
    m = dict(MANIFEST, **overrides)
    m["sig"] = sign_with_test_key(m)
    return m


# -- the round trip ----------------------------------------------------------

def test_the_device_accepts_what_the_publisher_signs(device):
    _m, u = device
    assert u.verify_manifest(signed(), TEST_KEYS) is True


def test_the_two_canonical_forms_agree(device):
    """The signed bytes are written twice -- once in Python, once in MicroPython
    -- and nothing executes them together but this. If they drift, every update
    stops verifying and the only symptom is 'unsigned update' on the glass."""
    _m, u = device
    for manifest in (MANIFEST,
                     dict(MANIFEST, channel="unstable", version=1785659788),
                     dict(MANIFEST, sha256="", size=0),
                     dict(MANIFEST, channel=None, version=None, size=None)):
        assert u._canonical(manifest) == ota_sign.canonical(manifest)


def test_the_host_verifier_mirrors_the_device_one(device):
    """tools/ota_sign.verify exists so a release can be checked before it ships;
    it has to answer exactly what the device will."""
    _m, u = device
    for manifest in (signed(), signed(channel="unstable"),
                     dict(signed(), version=99)):
        sig = manifest.get("sig")
        assert ota_sign.verify(manifest, sig, TEST_N) == \
            u.verify_manifest(manifest, TEST_KEYS)


# -- what a network attacker gets to try -------------------------------------

@pytest.mark.parametrize("field, value", [
    ("version", 999),                    # a downgrade or a fake "newer" build
    ("size", 4290000),                   # a different image
    ("sha256", "0" * 64),                # THE one that picks the payload
    ("channel", "unstable"),             # a beta smuggled onto stable
    ("board", "p4"),                     # an Xtensa image aimed at a RISC-V chip
])
def test_editing_a_signed_field_breaks_the_signature(device, field, value):
    _m, u = device
    tampered = signed()
    assert tampered[field] != value
    tampered[field] = value
    assert u.verify_manifest(tampered, TEST_KEYS) is False


def test_the_url_and_label_are_deliberately_not_signed(device):
    """So a school can mirror the official manifest to a LAN host and rewrite
    the url. The bytes are still pinned: sha256 is signed, and the download is
    rejected unless it hashes to it."""
    _m, u = device
    moved = dict(signed(), url="http://192.168.1.9:8000/firmware.bin",
                 label="mirrored")
    assert u.verify_manifest(moved, TEST_KEYS) is True


def test_a_signature_from_another_key_is_refused(device):
    _m, u = device
    other_n = TEST_N - 2         # same size, not the key
    forged = dict(MANIFEST)
    forged["sig"] = sign_with_test_key(forged, n=other_n, d=TEST_D)
    assert u.verify_manifest(forged, TEST_KEYS) is False


@pytest.mark.parametrize("sig", [None, "", "not hex", "00", "ff" * 256,
                                 "%x" % (TEST_N + 1)])
def test_a_junk_signature_is_refused_without_raising(device, sig):
    """Whatever arrives off the wire lands straight in int(sig, 16) and pow();
    a crash here is a denial of the update path, not just a rejection."""
    _m, u = device
    m = dict(MANIFEST)
    if sig is not None:
        m["sig"] = sig
    assert u.verify_manifest(m, TEST_KEYS) is False


def test_an_unsigned_manifest_is_not_a_valid_one(device):
    _m, u = device
    assert u.verify_manifest(MANIFEST, TEST_KEYS) is False


# -- when a signature is REQUIRED --------------------------------------------

def test_a_baked_url_requires_a_signature_and_a_card_one_does_not(device):
    """Choosing a host by writing to the SD card is a physical act of consent,
    and it keeps the LAN dev loop key-free. The baked urls are the ones an
    attacker on the network gets to answer for."""
    m, u = device
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    assert u._require_signature(from_card=False) is True
    assert u._require_signature(from_card=True) is False


def test_a_build_with_no_baked_key_cannot_require_one(device):
    """Requiring a signature no key can check would only brick the update path."""
    m, u = device
    m.OTA_PUBLIC_KEYS = ()
    assert u._require_signature(from_card=False) is False


def _online(u, manifest, from_card=False):
    u._manifest_source = lambda channel=None: ("https://h/latest.json", from_card)
    u.ensure_online = lambda: True
    u._http_get_text = lambda url, limit=8192: json.dumps(manifest)
    return u.check_online()


def test_check_online_refuses_an_unsigned_manifest_from_a_baked_url(device):
    m, u = device
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    assert _online(u, MANIFEST) is None
    assert u.error == "unsigned update"


def test_check_online_accepts_a_signed_one(device):
    m, u = device
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    got = _online(u, signed())
    assert got is not None and got["version"] == MANIFEST["version"]
    assert u.error is None


def test_a_tampered_signature_is_refused_even_where_none_was_required(device):
    """From the card, unsigned is fine -- but a manifest carrying a signature
    that does not check out has been meddled with, and that is worse news than
    one carrying none."""
    m, u = device
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    assert _online(u, dict(signed(), version=999), from_card=True) is None
    assert u.error == "bad signature"


def test_an_unsigned_manifest_from_the_card_still_works(device):
    m, u = device
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    assert _online(u, MANIFEST, from_card=True) is not None


# -- the publisher -----------------------------------------------------------

def test_the_workflow_hands_the_key_to_the_publisher():
    wf = (ROOT / ".github" / "workflows" / "firmware-build.yml").read_text()
    assert "MOYBYTE_OTA_SIGNING_KEY: ${{ secrets.MOYBYTE_OTA_SIGNING_KEY }}" in wf
    assert "cryptography" in wf                     # signing needs it installed
    assert ota_sign.ENV_KEY == "MOYBYTE_OTA_SIGNING_KEY"


def test_an_unsigned_publish_says_so_loudly(tmp_path, monkeypatch, capsys):
    """A missing secret must not publish quietly: the result is an update no
    device will install."""
    monkeypatch.delenv(ota_sign.ENV_KEY, raising=False)
    publish = _load_publish()
    artifacts = _stage_artifacts(tmp_path)
    out = tmp_path / "release"
    out.mkdir()
    manifest = publish.stage_ota("firmware-beta", "unstable", str(artifacts),
                                 str(out))
    assert "sig" not in manifest
    assert "UNSIGNED" in capsys.readouterr().out


def _load_publish():
    spec = importlib.util.spec_from_file_location(
        "_moy_publish_sign", ROOT / "tools" / "publish_firmware_release.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_moy_publish_sign"] = mod
    spec.loader.exec_module(mod)
    return mod


def _stage_artifacts(tmp_path):
    publish = _load_publish()
    folder = tmp_path / "artifacts" / "moybyte-firmware-tdeck"
    folder.mkdir(parents=True)
    (folder / publish.OTA_IMAGES["tdeck"]).write_bytes(b"\xe9APP" * 64)
    (folder / publish.OTA_STAMP).write_text(json.dumps(
        {"channel": "unstable", "version": 1785659788, "label": "beta",
         "board": "tdeck"}))
    return tmp_path / "artifacts"


needs_cryptography = pytest.mark.skipif(
    importlib.util.find_spec("cryptography") is None,
    reason="signing needs the `release` extra; verifying does not")


@needs_cryptography
def test_a_generated_key_signs_something_the_device_accepts(tmp_path):
    """The real path, end to end: keygen -> the constant we bake in -> a signed
    manifest -> the device's verifier."""
    n, e = ota_sign.keygen(str(tmp_path / "k.pem"))
    pem = (tmp_path / "k.pem").read_bytes()
    assert (tmp_path / "k.pem").stat().st_mode & 0o777 == 0o600

    manifest = dict(MANIFEST)
    manifest["sig"] = ota_sign.sign(manifest, pem)
    _m, u = _load_moy_ota(), None
    u = _m.OtaUpdater(with_sd=lambda fn: fn())
    assert u.verify_manifest(manifest, (("%x" % n, e),)) is True

    # And the constant the tool tells you to paste really is that key.
    ns = ota_sign.key_constant(n, e)
    assert "".join(ns.split("'")[1::2]) == "%x" % n


@needs_cryptography
def test_the_publisher_signs_when_the_key_is_present(tmp_path, monkeypatch):
    ota_sign.keygen(str(tmp_path / "k.pem"))
    monkeypatch.setenv(ota_sign.ENV_KEY, str(tmp_path / "k.pem"))
    publish = _load_publish()
    out = tmp_path / "release"
    out.mkdir()
    manifest = publish.stage_ota("firmware-beta", "unstable",
                                 str(_stage_artifacts(tmp_path)), str(out))
    n, _e = ota_sign.public_numbers((tmp_path / "k.pem").read_bytes())
    sig = manifest.pop("sig")
    assert ota_sign.verify(manifest, sig, n) is True
    # and it is the file that ships, not just the dict
    on_disk = json.loads((out / publish.manifest_name("tdeck")).read_text())
    assert on_disk["sig"] == sig


# -- one board must never install another's app image ------------------------

def test_a_manifest_for_another_board_is_refused_by_name(device):
    """Checked BEFORE the signature, so the error names the real problem. This
    is the case a second board created: the payload is an app-partition image,
    Xtensa on the T-Deck and RISC-V on the P4, so the wrong one is a valid
    image that cannot boot -- rollback territory, for one field's prevention."""
    m, u = device
    m.OTA_PUBLIC_KEYS = TEST_KEYS
    other = signed(board="p4")            # correctly signed, wrong silicon
    assert u.verify_manifest(other, TEST_KEYS) is True

    u._manifest_source = lambda channel=None: ("https://h/latest-p4.json", False)
    u.ensure_online = lambda: True
    u._http_get_text = lambda url, limit=8192: json.dumps(other)
    assert u.check_online() is None
    assert u.error == "wrong board"


def test_the_default_url_carries_the_board(device):
    """A T-Deck asks for latest-tdeck.json and a P4 for latest-p4.json, off the
    same release -- so the wrong manifest is not even fetched in the first
    place; the check above is the backstop for a hand-written ota.json."""
    m, _u = device
    assert m.BOARD == "tdeck"                       # the committed default
    for channel in ("stable", "unstable"):
        assert m.default_manifest_url(channel).endswith("/latest-tdeck.json")
        assert m.default_manifest_url(channel, "p4").endswith("/latest-p4.json")
    assert m.default_manifest_url("nonesuch") is None
