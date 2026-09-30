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

With the owner's Settings -> UNKNOWN SOURCES on, a module with NO signature
passes this check (its provenance key is still checked, on the run's thread);
every module that carries a trailer is held to it exactly as before, so the
decision table below is the switch crossed with every kind of file.
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


def _tampered():
    data = bytearray(signed())
    data[data.index(KEY_TEXT) + 10] ^= 0x01
    return bytes(data)


def _malformed():
    data = signed()
    magic = wm.sig_magic()
    return data[:-len(magic) - 4] + (7).to_bytes(4, "little") + magic


# The load decision, file kind by switch: None loads (on to the provenance
# check), anything else is the board's refusal. The switch changes exactly one
# cell: an unsigned module. "another key" is the throwaway key's signature
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
def test_the_load_decision_with_unknown_sources_off_and_on(name, make, keys, off, on):
    data = make()
    assert wm.verify(data, "esp32s3", keys) == off, name
    assert wm.verify(data, "esp32s3", keys, unknown_sources=False) == off, name
    assert wm.verify(data, "esp32s3", keys, unknown_sources=True) == on, name


def test_an_unsigned_module_passes_whole_and_its_key_is_left_to_the_thread():
    """What the switch lets through is the file as it is -- no trailer to
    strip -- and it carries its provenance key into the load, where the
    board's key check (the same one every signed module meets) decides."""
    assert wm.verify(MODULE, "esp32s3", TEST_KEYS, unknown_sources=True) is None
    assert wm.split(MODULE) == (MODULE, None)
    assert KEY_TEXT in MODULE


def test_the_board_decides_the_switch_where_it_decides_the_signature():
    """The engine's check takes the switch as an argument, from both of its
    callers, and lets an unsigned module through only on it; a trailer that
    is present is verified before the switch is ever read. The switch is the
    caller's on each load -- the cart session's comes from moycore's
    wasm_open, which the device glue hands ws.unknown_sources."""
    src = (ROOT / "native" / "moy_wasm" / "modmoy_wasm.c").read_text()
    body = src[src.index("static const char *verify_module("):]
    body = body[:body.index("\n}\n")]
    assert "bool unsigned_ok" in body
    head = body[:body.index('"malformed signature"')]
    assert 'return unsigned_ok ? NULL : "unsigned module";' in head
    assert body.count("unsigned_ok") == 2, "the switch is read once, for a missing trailer"
    assert "a[ARG_allow_unsigned].u_bool" in src
    assert "verify_module(s->file, s->file_len, allow_unsigned != 0" in src
    core = (ROOT / "native" / "moycore" / "modmoycore.c").read_text()
    assert "moy_wasm_session_open(path, sha, memory, allow_unsigned," in core
    assert "n_args > 7 && mp_obj_is_true(a[7])" in core
    glue = (ROOT / "device" / "moycore_glue.py").read_text()
    assert 'bool(getattr(ws, "unknown_sources", False))' in glue


def test_only_tamper_evidence_stays_a_hard_refusal():
    """device.moycore_glue._AOT_TAMPER_EVIDENCE is the engine's own refusal
    text for a SIGNATURE that does not verify -- the one case a WasmRun does
    not retry on the interpreter (docs/wasm_tier_plan_2026-09.md, "A cart
    survives its firmware", 2026-09-30). "unsigned module" (no signature at
    all, the Unknown sources case) is deliberately NOT in that tuple: it is
    retried, never a panel."""
    from device import moycore_glue as glue
    src = (ROOT / "native" / "moy_wasm" / "modmoy_wasm.c").read_text()
    assert 'snprintf(err, errlen, "refused: %s", why);' in src
    for text in glue._AOT_TAMPER_EVIDENCE:
        assert ('"%s"' % text[len("refused: "):]) in src, text
    assert not any("unsigned" in text for text in glue._AOT_TAMPER_EVIDENCE)


def test_the_verify_command_answers_for_either_setting(tmp_path, capsys):
    path = tmp_path / "m.aot"
    path.write_bytes(MODULE)
    assert wm.main(["verify", str(path), "--chip", "esp32s3",
                    "--unknown-sources"]) == 0
    assert "unsigned, loads with Unknown sources on" in capsys.readouterr().out
    path.write_bytes(_tampered())
    assert wm.main(["verify", str(path), "--chip", "esp32s3",
                    "--unknown-sources"]) == 1
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


def test_the_verifier_is_the_devices_own():
    spec = importlib.util.spec_from_file_location("moy_ota_probe", wm.MOY_OTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = wm.signed_text(MODULE, "esp32s3")
    assert mod.verify_sig(text, sign_with_test_key(text).hex(), TEST_KEYS)
    assert os.path.samefile(wm.MOY_OTA, ROOT / "device" / "moy_ota.py")
    assert re.search(r"^def verify_sig\(", (ROOT / "device" / "moy_ota.py").read_text(), re.M)
