"""The crypto rows (native/os_calls.json's moybyte.random, sha256, hmac_sha256,
aes_gcm_seal, aes_gcm_open, aes_ctr, aes_ccm_seal, aes_ccm_open, x25519,
ed25519_public, ed25519_sign and ed25519_verify; native/moy_net/moy_crypto.h,
the one C body) answer the same known answers on every tier, with no
permission, and a length the call refuses is an error answered, never a
crash:

  * Python: the `moybyte` module's functions over the module moy_app (the
    native module on the desktop MicroPython is tests/test_moy_app.py's);
  * Lua: a Lua app's `moybyte` table, its role binding's C
    (native/moy_app/moy_app_lua.c; a service's is tests/test_lua_services.py's);
  * a compiled app: tests/fixtures/wasm/app_crypto.moy's moybyte.app imports;
  * a native and a WASI program: tests/fixtures/elf/elf_crypto.moy, in
    tests/test_elf_commands.py and tests/test_wasi_commands.py.

The vectors are tests/crypto_vectors.py's.
"""

import binascii
import os

import pytest

import crypto_vectors as V
from runtime import moybyte, os_log
from ws_helpers import build_ws, open_cart, select_on_home

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "tests", "fixtures", "wasm", "app_crypto.moy")
H = bytes.fromhex
DT = 1.0 / 30.0


def test_python_s_moybyte_functions_answer_the_known_answers():
    assert moybyte.sha256(b"abc").hex() == V.SHA256_ABC
    assert moybyte.sha256("").hex() == V.SHA256_EMPTY
    for key, data, mac in V.HMAC:
        assert moybyte.hmac_sha256(key, data).hex() == mac
    for key, sealed in V.GCM:
        assert moybyte.aes_gcm_open(H(key), H(V.GCM_NONCE + sealed)) == H(V.GCM_PLAIN)
    assert moybyte.x25519(H(V.X25519_ALICE)).hex() == V.X25519_ALICE_PUB
    assert moybyte.x25519(H(V.X25519_BOB)).hex() == V.X25519_BOB_PUB
    assert moybyte.x25519(H(V.X25519_ALICE), H(V.X25519_BOB_PUB)).hex() == V.X25519_SHARED
    assert moybyte.x25519(H(V.X25519_BOB), H(V.X25519_ALICE_PUB)).hex() == V.X25519_SHARED


def test_python_s_ed25519_answers_rfc_8032_s_vectors():
    for secret, pub, msg, sig in V.ED25519:
        assert moybyte.ed25519_public(H(secret)).hex() == pub
        assert moybyte.ed25519_sign(H(secret), H(msg)).hex() == sig
        assert moybyte.ed25519_verify(H(pub), H(msg), H(sig)) is True


def test_python_s_ed25519_verify_refuses_what_was_not_signed():
    secret, pub, msg, sig = (H(v) for v in V.ED25519[2])
    assert moybyte.ed25519_verify(pub, msg + b"!", sig) is False
    assert moybyte.ed25519_verify(pub, msg[:1], sig) is False
    assert moybyte.ed25519_verify(H(V.ED25519[1][1]), msg, sig) is False
    for at in (0, 31, 32, 63):
        changed = bytearray(sig)
        changed[at] ^= 1
        assert moybyte.ed25519_verify(pub, msg, bytes(changed)) is False, at
    assert moybyte.ed25519_verify(bytes(32), msg, sig) is False, "a key that does not decode"
    mine = moybyte.random(32)
    said = moybyte.ed25519_sign(mine, "a mesh packet")
    assert moybyte.ed25519_verify(moybyte.ed25519_public(mine), b"a mesh packet", said)


def test_python_s_aes_ctr_answers_nist_s_and_rfc_3686_s_vectors():
    for key, block, plain, cipher in V.CTR:
        assert moybyte.aes_ctr(H(key), H(block + plain)).hex() == cipher
        assert moybyte.aes_ctr(H(key), H(block + cipher)).hex() == plain, "its own inverse"
    assert moybyte.aes_ctr(H(V.CTR[0][0]), H(V.CTR[0][1])) == b"", "a counter block alone"
    # a counter block that wraps all 128 bits counts on from zero
    key = H(V.CTR[0][0])
    ones = moybyte.aes_ctr(key, b"\xff" * 16 + bytes(32))
    assert ones[16:] == moybyte.aes_ctr(key, bytes(16) + bytes(16))


def test_python_s_aes_ccm_answers_nist_s_vectors_and_meshtastic_s_packet():
    for key, nonce, tag, plain, sealed in V.CCM:
        assert moybyte.aes_ccm_seal(H(key), H(nonce + plain), tag).hex() == sealed
        assert moybyte.aes_ccm_open(H(key), H(nonce + sealed), tag).hex() == plain
    radio = H(V.MESH_PKI_RADIO)
    shared = moybyte.x25519(H(V.MESH_PKI_SECRET), H(V.MESH_PKI_PEER))
    key = moybyte.sha256(shared)
    assert key.hex() == V.MESH_PKI_KEY
    nonce = radio[8:12] + radio[-4:] + radio[4:8] + b"\0"
    assert nonce.hex() == V.MESH_PKI_NONCE
    assert moybyte.aes_ccm_open(key, nonce + radio[16:-4], 8).hex() == V.MESH_PKI_PLAIN
    assert moybyte.aes_ccm_seal(key, nonce + H(V.MESH_PKI_PLAIN), 8) == radio[16:-4]


def test_python_s_aes_ccm_open_refuses_what_was_not_sealed():
    key, nonce, tag, plain, sealed = V.CCM[1]
    whole = H(nonce + sealed)
    for at in (0, 13, 13 + 23, len(whole) - 1):
        changed = bytearray(whole)
        changed[at] ^= 1
        assert moybyte.aes_ccm_open(H(key), bytes(changed), tag) is None, at
    assert moybyte.aes_ccm_open(H(key), whole, 8) is None, "another tag length"
    assert moybyte.aes_ccm_open(H(key), whole[:13 + 15], tag) is None, "shorter than a tag"
    assert moybyte.aes_ccm_open(moybyte.random(16), whole, tag) is None


def test_python_s_seal_opens_under_its_key_alone():
    key = moybyte.random(32)
    sealed = moybyte.aes_gcm_seal(key, b"a mesh packet")
    assert len(sealed) == len(b"a mesh packet") + 28
    assert sealed[:12] != moybyte.aes_gcm_seal(key, b"a mesh packet")[:12], "a fresh nonce"
    assert moybyte.aes_gcm_open(key, sealed) == b"a mesh packet"
    assert moybyte.aes_gcm_open(moybyte.random(32), sealed) is None
    changed = bytearray(sealed)
    changed[15] ^= 1
    assert moybyte.aes_gcm_open(key, bytes(changed)) is None
    assert moybyte.aes_gcm_open(key, sealed[:27]) is None
    assert moybyte.aes_gcm_open(key, moybyte.aes_gcm_seal(key, b"")) == b""


@pytest.mark.parametrize("call", [
    lambda: moybyte.random(-1), lambda: moybyte.random(4097),
    lambda: moybyte.aes_gcm_seal(b"short", b"x"), lambda: moybyte.aes_gcm_seal(b"", b"x"),
    lambda: moybyte.aes_gcm_open(bytes(33), bytes(40)),
    lambda: moybyte.x25519(bytes(31)), lambda: moybyte.x25519(bytes(32), bytes(31)),
    lambda: moybyte.x25519(H(V.X25519_ALICE), bytes(32)),
    lambda: moybyte.ed25519_public(bytes(31)), lambda: moybyte.ed25519_sign(bytes(33), b"x"),
    lambda: moybyte.ed25519_verify(bytes(31), b"x", bytes(64)),
    lambda: moybyte.ed25519_verify(bytes(32), b"x", bytes(63)),
    lambda: moybyte.aes_ctr(bytes(15), bytes(20)), lambda: moybyte.aes_ctr(bytes(16), bytes(15)),
    lambda: moybyte.aes_ccm_seal(bytes(16), bytes(12), 8),
    lambda: moybyte.aes_ccm_seal(bytes(16), bytes(20), 5),
    lambda: moybyte.aes_ccm_seal(bytes(16), bytes(20), 18),
    lambda: moybyte.aes_ccm_seal(bytes(16), bytes(20), -8),
    lambda: moybyte.aes_ccm_seal(bytes(20), bytes(20), 8),
    lambda: moybyte.aes_ccm_open(bytes(16), bytes(30), 3),
])
def test_python_s_length_mistakes_raise_value_error(call):
    with pytest.raises(ValueError):
        call()


LUA = """
local function hex(s)
  if s == nil then return "nil" end
  return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end
local function unhex(s)
  return (s:gsub("..", function(x) return string.char(tonumber(x, 16)) end))
end
local function err(fn, ...)
  local ok, e = pcall(fn, ...)
  return ok and "answered" or tostring(e):gsub("^.-: ", "")
end
function _init()
  SHA = hex(moybyte.sha256("abc"))
  HMAC = hex(moybyte.hmac_sha256("Jefe", "what do ya want for nothing?"))
  RND = #moybyte.random(16)
  local key = unhex("@KEY@")
  OPENED = hex(moybyte.aes_gcm_open(key, unhex("@SEALED@")))
  local s = moybyte.aes_gcm_seal(key, "moybyte")
  SEALED, BACK = #s, moybyte.aes_gcm_open(key, s)
  FORGED = hex(moybyte.aes_gcm_open(key, s:sub(1, -2) .. string.char(s:byte(-1) ~ 1)))
  PUB = hex(moybyte.x25519(unhex("@ALICE@")))
  SHARED = hex(moybyte.x25519(unhex("@ALICE@"), unhex("@BOB_PUB@")))
  BADKEY = err(moybyte.aes_gcm_seal, "short", "x")
  BADN = err(moybyte.random, 5000)
  SMALL = err(moybyte.x25519, unhex("@ALICE@"), string.rep("\\0", 32))
  local sk, msg = unhex("@ED_SK@"), unhex("@ED_MSG@")
  EDPUB = hex(moybyte.ed25519_public(sk))
  EDSIG = hex(moybyte.ed25519_sign(sk, msg))
  EDOK = tostring(moybyte.ed25519_verify(moybyte.ed25519_public(sk), msg, unhex(EDSIG)))
  EDBAD = tostring(moybyte.ed25519_verify(moybyte.ed25519_public(sk), msg .. "!", unhex(EDSIG)))
  EDLEN = err(moybyte.ed25519_verify, sk, msg, "short")
  CTR = hex(moybyte.aes_ctr(unhex("@CTR_KEY@"), unhex("@CTR_DATA@")))
  local mk = unhex("@MESH_KEY@")
  MESH = hex(moybyte.aes_ccm_open(mk, unhex("@MESH_SEALED@"), 8))
  RESEALED = hex(moybyte.aes_ccm_seal(mk, unhex("@MESH_NONCE@") .. unhex(MESH), 8))
  UNOPENED = hex(moybyte.aes_ccm_open(mk, unhex("@MESH_SEALED@"), 4))
  BADTAG = err(moybyte.aes_ccm_seal, mk, unhex("@MESH_NONCE@"), 7)
  LOG = type(moybyte.log)
end
function _draw() cls(0) end
"""


def _write_lua_app(carts, name, src):
    import json
    d = carts / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"title": name, "canvas": "320x240", "runtime": "lua", "main": "main.lua",
         "moybyte": {"type": "app", "permissions": ["graphics"]}}))
    (d / "main.lua").write_text(src)
    (d / "config.json").write_text("{}")


def test_a_lua_app_s_moybyte_table_answers_the_known_answers(tmp_path):
    """A Lua app holds the rows in its `moybyte` table beside the console
    calls and the log (the prelude keeps what its role binding opened), with
    no permission; a forged seal is nil, a length refused an error."""
    key, sealed = V.GCM[0]
    src = (LUA.replace("@KEY@", key).replace("@SEALED@", V.GCM_NONCE + sealed)
           .replace("@ALICE@", V.X25519_ALICE).replace("@BOB_PUB@", V.X25519_BOB_PUB)
           .replace("@ED_SK@", V.ED25519[3][0]).replace("@ED_MSG@", V.ED25519[3][2])
           .replace("@CTR_KEY@", V.CTR[0][0]).replace("@CTR_DATA@", V.CTR[0][1] + V.CTR[0][2])
           .replace("@MESH_KEY@", V.MESH_PKI_KEY)
           .replace("@MESH_SEALED@", V.MESH_PKI_NONCE + V.MESH_PKI_RADIO[32:68])
           .replace("@MESH_NONCE@", V.MESH_PKI_NONCE))
    _write_lua_app(tmp_path / "carts", "Lua Crypto", src)
    ws = build_ws(tmp_path)
    assert select_on_home(ws, "Lua Crypto") is not None
    ws.open()
    ws.input.begin_frame()
    ws.frame(DT)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    g = ws.run.player._lua.get_global
    assert g("SHA") == V.SHA256_ABC
    assert g("HMAC") == V.HMAC[1][2]
    assert g("RND") == 16
    assert g("OPENED") == V.GCM_PLAIN
    assert (g("SEALED"), g("BACK"), g("FORGED")) == (35, "moybyte", "nil")
    assert g("PUB") == V.X25519_ALICE_PUB
    assert g("SHARED") == V.X25519_SHARED
    assert g("BADKEY") == "a length the call refuses"
    assert g("BADN") == "a length the call refuses"
    assert g("SMALL") == "a peer key of small order"
    assert (g("EDPUB"), g("EDSIG")) == (V.ED25519[3][1], V.ED25519[3][3])
    assert (g("EDOK"), g("EDBAD")) == ("true", "false")
    assert g("EDLEN") == "a length the call refuses"
    assert g("CTR") == V.CTR[0][3]
    assert (g("MESH"), g("RESEALED")) == (V.MESH_PKI_PLAIN, V.MESH_PKI_RADIO[32:68])
    assert g("UNOPENED") == "nil"
    assert g("BADTAG") == "a length the call refuses"
    assert g("LOG") == "function", "the prelude kept its own names"


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def test_a_compiled_app_s_imports_answer_the_known_answers(tmp_path):
    """Crypto Wasm, a C app with no permission, calls each row through its
    moybyte.app imports: the answers, RFC 8032's TEST 3 signed and checked,
    and FULL, BAD and FORGED negated."""
    import shutil
    from runtime import host_app
    from runtime import moy_log as L
    from tools import wasm_cart
    _binding_or_skip()
    try:
        built = str(tmp_path / "built" / "app_crypto.moy")
        wasm_cart.build(APP, built)
    except wasm_cart.ToolchainMissing as exc:
        pytest.skip(str(exc))
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    shutil.copytree(built, os.path.join(root, "app_crypto.moy"))
    ws.carts.rescan()
    since = L.next()
    open_cart(ws, "Crypto Wasm")
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    pid = ws.run.player.pid
    ws._dirty = True
    ws.frame(DT)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    lines = [t for _s, _t, _l, q, t in os_log.records(since, 256)
             if q == pid and not t.startswith("start ")]
    ws.exit()
    assert lines == [
        "random 16", "random.short -2",
        "sha256 " + V.SHA256_ABC, "sha256.short -2",
        "hmac_sha256 " + V.HMAC[1][2],
        "aes_gcm_open " + V.GCM_PLAIN, "aes_gcm_open.key -7", "aes_gcm_open.changed -10",
        "aes_gcm_seal 35", "aes_gcm_seal.opened " + binascii.hexlify(b"moybyte").decode(),
        "x25519.public " + V.X25519_ALICE_PUB, "x25519.shared " + V.X25519_SHARED,
        "x25519.small -7",
        "ed25519.public " + V.ED25519[2][1], "ed25519.sign " + V.ED25519[2][3],
        "ed25519.verify 1", "ed25519.verify.changed 0", "ed25519.verify.sig -7",
        "ed25519.sign.short -2",
        "aes_ctr " + V.CTR[1][3], "aes_ctr.block -7",
        "aes_ccm_open " + V.MESH_PKI_PLAIN, "aes_ccm.tag -7", "aes_ccm_seal.short -2",
        "aes_ccm_seal " + V.MESH_PKI_RADIO[32:68], "aes_ccm_open.changed -10",
    ], lines


def test_random_draws_past_one_drbg_request_and_never_repeat():
    """moy_seal_random's HMAC-DRBG answers a draw longer than one of its
    requests (1024 bytes) whole, and no two draws agree."""
    a, b = moybyte.random(4096), moybyte.random(4096)
    assert len(a) == len(b) == 4096
    assert a != b and a[1024:2048] != a[:1024]
    assert len(set(moybyte.random(16) for _ in range(64))) == 64


# The one source (moy_seal.h, THE RANDOM SOURCE) and the one other reader of
# the chip's RNG: the U-mode seal, a canary no U-mode code can read.
RNG_READERS = {"native/moy_net/moy_seal.c", "native/moy_elf/moy_umode.c"}
VENDORED = ("/dropbear/", "/mbedtls/", "/libmoy/", "/wamr", "/elf_loader", "/dash/")


def test_only_the_random_source_reads_the_chip_s_rng():
    found = set()
    for top, _dirs, files in os.walk(os.path.join(ROOT, "native")):
        for f in files:
            path = os.path.join(top, f)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if not f.endswith((".c", ".h")) or any(v in "/" + rel for v in VENDORED):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if "esp_fill_random(" in text or "esp_random(" in text:
                found.add(rel)
    assert found == RNG_READERS
