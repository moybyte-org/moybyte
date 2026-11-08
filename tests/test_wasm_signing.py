"""A per-architecture module loads only when it is admitted: signed the way OTA
images are, or unsigned under its cart's consent to `native`.

The board's admission is one call, `moy_ota_admit_module`
(native/moy_net/moy_ota.h), which both compiled tiers make -- an AOT module's
load (native/moy_wasm/modmoy_wasm.c) and a native module's
(native/moy_elf/modmoy_elf.c). It finds the signature trailer, hashes the
module and checks the signed text with the keys the image trusts.
tools/wasm_module.py signs, and its `verify` IS that C call, through the
host's binding (runtime/host/net_binding.py): these tests execute the board's
own admission with the throwaway key tests/test_ota_signing.py publishes. A
signed module verifies and comes back exactly as it was built, a byte changed
anywhere in it -- its provenance key included -- is refused, and so is a
module with no signature, a malformed trailer, another chip's signature or
another key's. The on-glass suites hold the board to the same answers.

With the cart's consent given, a module with NO signature is admitted (its
provenance key is still checked, on the run's thread); every module that
carries a trailer is held to it exactly as before, so the decision table
below is the consent crossed with every kind of file.
"""

import hashlib
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


def test_the_signed_text_is_the_one_the_tool_signs():
    """The admission checks the text the header states -- the scheme, the
    chip, the module's length, its sha256 -- which is exactly what the tool
    signs, and one magic ends every module file."""
    chip, n = "esp32s3", len(MODULE)
    c_text = ("%s\n%s\n%u\n" % (wm.sig_scheme(), chip, n)).encode() \
        + hashlib.sha256(MODULE).hexdigest().encode()
    assert c_text == wm.signed_text(MODULE, chip)
    assert wm.sig_magic() == b"moybyte-sig1"
    src = "".join(p.read_text() for p in (ROOT / "native").rglob("*.h")
                  if "wamr" not in p.parts)
    assert len(re.findall(r'#define \w+ "moybyte-sig1"', src)) == 1, \
        "the magic is defined once, in moy_ota.h"


def test_the_admission_strips_the_trailer_and_names_the_module_s_length():
    from runtime import net_binding
    data = signed()
    assert net_binding.ota_admit_module(data, wm.sig_scheme(), "esp32s3", False,
                                        TEST_KEYS) == (None, len(MODULE))
    assert net_binding.ota_admit_module(MODULE, wm.sig_scheme(), "esp32s3", True,
                                        TEST_KEYS) == (None, len(MODULE))


def test_both_tiers_admit_through_the_one_call():
    """No tier carries a verifier of its own: the AOT load and the native
    load call moy_ota_admit_module, and the board, the host and the tool
    reach the same C."""
    for path in ("native/moy_wasm/modmoy_wasm.c", "native/moy_elf/modmoy_elf.c",
                 "runtime/host/moyhost_elf.c"):
        src = (ROOT / path).read_text()
        assert src.count("moy_ota_admit_module(") == 1, path
        assert "mbedtls_sha256" not in src and "verify_sig" not in src, path


def _tampered():
    data = bytearray(signed())
    data[data.index(KEY_TEXT) + 10] ^= 0x01
    return bytes(data)


def _malformed():
    data = signed()
    magic = wm.sig_magic()
    return data[:-len(magic) - 4] + (7).to_bytes(4, "little") + magic


# The load decision, file kind by consent: None loads (on to the provenance
# check), anything else is the board's refusal. The consent changes exactly
# one cell: an unsigned module. "another key" is the throwaway key's signature
# checked against the image's own OTA_PUBLIC_KEYS, which never signed it.
DECISIONS = (
    ("signed", signed, TEST_KEYS, None, None),
    ("unsigned", lambda: MODULE, TEST_KEYS, "unsigned module", None),
    ("tampered", _tampered, TEST_KEYS, "bad signature", "bad signature"),
    ("another chip", lambda: signed(chip="esp32p4"), TEST_KEYS,
     "bad signature", "bad signature"),
    ("another key", signed, None, "bad signature", "bad signature"),
    ("malformed trailer", _malformed, TEST_KEYS,
     "malformed signature", "malformed signature"),
)


@pytest.mark.parametrize("name,make,keys,off,on", DECISIONS,
                         ids=[d[0] for d in DECISIONS])
def test_the_load_decision_without_and_with_consent(name, make, keys, off, on):
    data = make()
    assert wm.verify(data, "esp32s3", keys) == off, name
    assert wm.verify(data, "esp32s3", keys, consented=False) == off, name
    assert wm.verify(data, "esp32s3", keys, consented=True) == on, name


def test_an_unsigned_module_passes_whole_and_its_key_is_left_to_the_thread():
    """What the consent lets through is the file as it is -- no trailer to
    strip -- and it carries its provenance key into the load, where the
    board's key check (the same one every signed module meets) decides."""
    assert wm.verify(MODULE, "esp32s3", TEST_KEYS, consented=True) is None
    assert wm.split(MODULE) == (MODULE, None)
    assert KEY_TEXT in MODULE


def test_the_consent_is_native_granted():
    """The consent the admission takes is the cart's `native`, the install
    prompt's "Full access to the console", on either compiled tier; a cart
    with no consent record runs on its manifest as written, and one whose
    `native` was turned off in Settings is not admitted."""
    from runtime import app_perms as P
    cart = {"id": "a.b", "runtime": "wasm", "permissions": ["native"]}
    assert P.admits_unsigned(P.check(cart, None, ())[0])
    assert not P.admits_unsigned(P.check(dict(cart, permissions=[]), None, ())[0])
    assert P.line("native") == "Full access to the console"
    off = {"perms": ["native"], "off": ["native"]}
    assert not P.admits_unsigned(P.check(cart, off, ())[0])
    native = {"id": "a.c", "native": ["host"], "permissions": ["native"]}
    assert P.admits_unsigned(P.check(native, None, ())[0])


def test_only_tamper_evidence_stays_a_hard_refusal():
    """device.moycore_glue._AOT_TAMPER_EVIDENCE is the engine's own refusal
    text for a SIGNATURE that does not verify -- the one case a WasmRun does
    not retry on the interpreter (native/moy_wasm/README.md, "A cart
    survives its firmware", 2026-09-30). "unsigned module" (no signature at
    all, without the consent) is deliberately NOT in that tuple: it is
    retried, never a panel."""
    from device import moycore_glue as glue
    src = (ROOT / "native" / "moy_wasm" / "modmoy_wasm.c").read_text()
    ota = (ROOT / "native" / "moy_net" / "moy_ota.c").read_text()
    assert 'snprintf(err, errlen, "refused: %s", why);' in src
    for text in glue._AOT_TAMPER_EVIDENCE:
        assert ('"%s"' % text[len("refused: "):]) in ota, text
    assert not any("unsigned" in text for text in glue._AOT_TAMPER_EVIDENCE)


def test_the_verify_command_answers_with_and_without_consent(tmp_path, capsys):
    path = tmp_path / "m.aot"
    path.write_bytes(MODULE)
    assert wm.main(["verify", str(path), "--chip", "esp32s3"]) == 1
    assert "REFUSED: unsigned module" in capsys.readouterr().out
    assert wm.main(["verify", str(path), "--chip", "esp32s3", "--consented"]) == 0
    assert 'unsigned, loads for a cart granted "native"' in capsys.readouterr().out
    path.write_bytes(_tampered())
    assert wm.main(["verify", str(path), "--chip", "esp32s3", "--consented"]) == 1
    assert "REFUSED: bad signature" in capsys.readouterr().out


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
