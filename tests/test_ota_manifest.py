"""Tests for tools/gen_ota_manifest.py (#53 Phase 3): the OTA manifest generator that
emits latest.json (version/url/size/sha256) from a built firmware image."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_ota_manifest as gom  # noqa: E402


def test_build_manifest_computes_size_and_sha256(tmp_path):
    blob = b"\xe9kidcode firmware bytes"
    b = tmp_path / "fw.bin"
    b.write_bytes(blob)
    m = gom.build_manifest(str(b), "http://host/fw.bin", 7)
    assert m["version"] == 7
    assert m["url"] == "http://host/fw.bin"
    assert m["size"] == len(blob)
    assert m["sha256"] == hashlib.sha256(blob).hexdigest()
    assert m["filename"] == "fw.bin"


def test_read_firmware_version_matches_kc_ota():
    # The manifest version defaults to the firmware's FIRMWARE_VERSION so they can't drift.
    v = gom.read_firmware_version()
    assert isinstance(v, int) and v >= 1


def test_resolve_url_appends_filename_to_base():
    class A:
        url = None
        base_url = "https://h.example.com/kidcode/"
        port = 8000
    assert gom._resolve_url(A(), Path("/d/kidcode_micropython_tdeck.bin")) == \
        "https://h.example.com/kidcode/kidcode_micropython_tdeck.bin"


def test_main_writes_manifest_with_explicit_url_and_version(tmp_path):
    b = tmp_path / "kidcode_micropython_tdeck.bin"
    b.write_bytes(b"\xe9" + b"\x00" * 1000)
    out = tmp_path / "latest.json"
    rc = gom.main([str(b), "--url", "https://x/y.bin", "--version", "3", "--out", str(out)])
    assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["version"] == 3
    assert m["url"] == "https://x/y.bin"
    assert m["size"] == 1001
    assert m["sha256"] == hashlib.sha256(b.read_bytes()).hexdigest()


def test_main_defaults_version_from_kc_ota(tmp_path):
    b = tmp_path / "img.bin"
    b.write_bytes(b"\xe9abc")
    out = tmp_path / "m.json"
    gom.main([str(b), "--url", "http://h/img.bin", "--out", str(out)])
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["version"] == gom.read_firmware_version()


def test_build_manifest_includes_channel_and_label(tmp_path):
    # #53 two-channel: the manifest carries channel + a human label (a beta's version
    # is an epoch int, so the label keeps the update screen readable).
    b = tmp_path / "f.bin"
    b.write_bytes(b"\xe9x")
    m = gom.build_manifest(str(b), "http://h/f.bin", 7)
    assert m["channel"] == "stable" and m["label"] == "v7"        # defaults
    m2 = gom.build_manifest(str(b), "u", 1700000000, channel="unstable", label="beta z")
    assert m2["channel"] == "unstable" and m2["label"] == "beta z"


def test_publish_mode_stages_channel_dir(tmp_path):
    # --root publish: copy the image to ROOT/<channel>/firmware.bin + a matching manifest,
    # url pointing at the bin. This is what `make ota-publish-unstable` runs.
    b = tmp_path / "app.bin"
    b.write_bytes(b"\xe9" + b"\x00" * 500)
    root = tmp_path / "ota"
    rc = gom.main([str(b), "--root", str(root), "--channel", "unstable",
                   "--version", "1700000000", "--label", "beta x", "--base-url", "http://h:8000"])
    assert rc == 0
    man = json.loads((root / "unstable" / "latest.json").read_text(encoding="utf-8"))
    assert man["channel"] == "unstable"
    assert man["label"] == "beta x"
    assert man["url"] == "http://h:8000/unstable/firmware.bin"
    assert man["size"] == 501
    assert (root / "unstable" / "firmware.bin").read_bytes() == b.read_bytes()


def _load_kc_ota():
    import importlib.util
    p = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules" / "kc_ota.py"
    spec = importlib.util.spec_from_file_location("kc_ota", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_kc_ota_offer_logic_is_channel_aware():
    # The device offers an install when the manifest is a different channel (a switch,
    # incl. beta->stable) OR a newer version within the running channel. Host default
    # (no _ota_build stamp) is stable / FIRMWARE_VERSION.
    m = _load_kc_ota()
    u = m.OtaUpdater(with_sd=lambda fn: fn())
    assert u.channel() == "stable"
    assert u.version_label() == "v%d" % m.FIRMWARE_VERSION
    assert u.offers({"version": m.FIRMWARE_VERSION + 1, "channel": "stable"}) is True
    assert u.offers({"version": m.FIRMWARE_VERSION, "channel": "stable"}) is False
    assert u.offers({"version": 0, "channel": "unstable"}) is True      # switch to beta
    assert u.offers({"version": 0}, "unstable") is True                 # channel from arg
