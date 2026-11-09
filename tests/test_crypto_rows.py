"""The crypto rows (native/os_calls.json's moybyte.random, sha256, hmac_sha256,
aes_gcm_seal, aes_gcm_open and x25519; native/moy_net/moy_crypto.h, the one C
body) answer the same known answers on every tier, with no permission, and a
length the call refuses is an error answered, never a crash:

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
           .replace("@ALICE@", V.X25519_ALICE).replace("@BOB_PUB@", V.X25519_BOB_PUB))
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
    moybyte.app imports: the answers, and FULL, BAD and FORGED negated."""
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
    ], lines
