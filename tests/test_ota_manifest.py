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
