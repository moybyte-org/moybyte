"""A per-architecture module loads only when it is signed the way OTA images are.

The board's check (`verify_module` in native/moy_wasm/modmoy_wasm.c) finds the
signature trailer native/moy_wasm/moy_wasm_key.h states, hashes the module,
and hands the signed text to `moy_ota.verify_sig` -- the device's own verifier,
with the keys its image trusts. tools/wasm_module.py signs. These tests run the
device's verifier on CPython with the throwaway key tests/test_ota_signing.py
publishes, so the whole round trip runs in CI with no crypto library: a signed
module verifies and comes back exactly as it was built, a byte changed anywhere
in it -- its provenance key included -- is refused, and so is a module with no
signature, a malformed trailer, another chip's signature or another key's. The
on-glass suites hold the board to the same answers.
"""

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_ota_signing import TEST_D, TEST_KEYS, TEST_N  # noqa: E402
from tools import wasm_module as wm  # noqa: E402
from tools.wasm_module import ota_sign  # noqa: E402

K = (TEST_N.bit_length() + 7) // 8
KEY_TEXT = (wm.KEY_MAGIC + "wasm " + "ab" * 32 + "\n" + wm.key_tail("esp32s3")).encode()
# Shaped like an AOT file: the magic, a body, the key section's name and text,
# more body. The verifier reads none of it; the point is where the bytes sit.
MODULE = (b"\0aot\x05\0\0\0" + bytes(range(256)) * 3 + wm.KEY_SECTION.encode()
          + KEY_TEXT + bytes(range(255, -1, -1)) * 2)


def sign_with_test_key(text):
    """RSA signing is modexp once the private exponent is known."""
    block = ota_sign.pkcs1_v15_block(hashlib.sha256(text).digest(), K)
    return pow(int.from_bytes(block, "big"), TEST_D, TEST_N).to_bytes(K, "big")


def signed(module=MODULE, chip="esp32s3"):
    return wm.sign(module, chip, sign_with_test_key)


def test_a_signed_module_verifies_and_loads_as_it_was_built():
    data = signed()
    assert wm.verify(data, "esp32s3", TEST_KEYS) is None
    module, sig = wm.split(data)
    assert module == MODULE
    assert len(sig) == K
    assert data.endswith(wm.sig_magic())


def test_a_byte_changed_anywhere_in_the_module_is_refused():
    data = signed()
    key_at = data.index(KEY_TEXT)
    for at in (0, 40, key_at, key_at + len(KEY_TEXT) - 2, len(MODULE) - 1,
               len(MODULE) + 7):          # the last is inside the signature
        bad = bytearray(data)
        bad[at] ^= 0x01
        assert wm.verify(bytes(bad), "esp32s3", TEST_KEYS) == "bad signature", at


def test_a_module_cut_or_grown_is_refused():
    data = signed()
    module, sig = wm.split(data)
    assert wm.verify(wm.attach(module[:-1], sig), "esp32s3", TEST_KEYS) == "bad signature"
    assert wm.verify(wm.attach(module + b"\0", sig), "esp32s3", TEST_KEYS) == "bad signature"


def test_an_unsigned_module_is_refused():
    assert wm.verify(MODULE, "esp32s3", TEST_KEYS) == "unsigned module"
    assert wm.split(MODULE) == (MODULE, None)


def test_a_malformed_trailer_is_refused():
    data = signed()
    magic = wm.sig_magic()
    for k in (0, 63, 1025, len(data)):
        bad = data[:-len(magic) - 4] + k.to_bytes(4, "little") + magic
        assert wm.verify(bad, "esp32s3", TEST_KEYS) == "malformed signature", k


def test_a_signature_for_another_chip_is_refused():
    data = signed(chip="esp32p4")
    assert wm.verify(data, "esp32p4", TEST_KEYS) is None
    assert wm.verify(data, "esp32s3", TEST_KEYS) == "bad signature"


def test_a_key_the_image_does_not_trust_is_refused():
    """With no keys named, the verifier uses the image's own OTA_PUBLIC_KEYS --
    the release key, which never signed this throwaway module."""
    assert wm.verify(signed(), "esp32s3") == "bad signature"


def test_the_board_checks_with_the_ota_verifier_and_the_ota_keys():
    """The engine's check is moy_ota.verify_sig over the text the header
    states -- the scheme, the chip, the module's length, its sha256 -- which
    is exactly what the tool signs."""
    src = (ROOT / "native" / "moy_wasm" / "modmoy_wasm.c").read_text()
    body = src[src.index("static const char *verify_module("):]
    body = body[:body.index("\n}\n")]
    assert 'MOY_WASM_SIG_SCHEME "\\n%s\\n%u\\n", KEY_CHIP' in body
    assert "MP_QSTR_moy_ota" in body and "MP_QSTR_verify_sig" in body
    assert "MOY_WASM_SIG_MAGIC" in body and "k < 64 || k > 1024" in body
    chip, n = "esp32s3", len(MODULE)
    c_text = ("%s\n%s\n%u\n" % (wm.sig_scheme(), chip, n)).encode() \
        + hashlib.sha256(MODULE).hexdigest().encode()
    assert c_text == wm.signed_text(MODULE, chip)
    # Both halves of the engine refuse through it: the phase-1 run and the
    # cart's session.
    assert src.count("verify_module(") == 3


def test_the_signed_text_is_not_an_ota_manifest_text():
    """A signature over one scheme's text never verifies another's."""
    assert not wm.signed_text(MODULE, "esp32s3").startswith(ota_sign.SCHEME.encode())
    assert wm.sig_scheme() != ota_sign.SCHEME


def test_build_refuses_to_sign_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv(ota_sign.ENV_KEY, raising=False)
    monkeypatch.setattr(ota_sign, "DEFAULT_KEY", str(tmp_path / "absent.pem"))
    assert wm.signing_key() is None
    with pytest.raises(wm.ToolError, match="no signing key"):
        wm.build(b"\0asm\x01\0\0\0", "esp32s3", str(tmp_path / "x.aot"))


def test_the_verify_command_says_what_a_board_says(tmp_path, capsys):
    path = tmp_path / "m.aot"
    path.write_bytes(MODULE)
    assert wm.main(["verify", str(path), "--chip", "esp32s3"]) == 1
    assert "REFUSED: unsigned module" in capsys.readouterr().out


def test_a_pem_key_signs_what_the_verifier_accepts(tmp_path):
    pytest.importorskip("cryptography")
    n, e = ota_sign.keygen(str(tmp_path / "k.pem"))
    pem = (tmp_path / "k.pem").read_bytes()
    data = wm.sign(MODULE, "esp32p4", pem)
    assert wm.verify(data, "esp32p4", (("%x" % n, e),)) is None
    assert wm.verify(data, "esp32p4", TEST_KEYS) == "bad signature"


def test_the_verifier_is_the_devices_own():
    spec = importlib.util.spec_from_file_location("moy_ota_probe", wm.MOY_OTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = wm.signed_text(MODULE, "esp32s3")
    assert mod.verify_sig(text, sign_with_test_key(text).hex(), TEST_KEYS)
    assert os.path.samefile(wm.MOY_OTA, ROOT / "device" / "moy_ota.py")
    assert re.search(r"^def verify_sig\(", (ROOT / "device" / "moy_ota.py").read_text(), re.M)
