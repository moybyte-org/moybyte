"""Moy Mesh (tests/fixtures/apps/moymesh.moy, moybyte-org/carts' carts/moymesh/):
a Meshtastic node on the LoRa radio, a Lua window over a Lua service.

The codec in its service (service.lua, after mesh.lua), run on the host's Lua
VM with the boards' 32-bit integers (LUA_32BITS), against the Meshtastic
firmware's own numbers (2.7.26) and an independent implementation here (pyca's
cryptography, the protobuf wire format read by hand): the presets' radio
numbers and the frequency slots, the channels' keys and hashes (LongFast's 8,
Mesh Serbia's "Srbija"), the channel URL (the Meshtastic Python CLI's example
link, and a link made here read back), the 16-byte header, the AES-CTR counter
block, the Data, User and Routing protobufs, and a direct message both ways
(the firmware's own PKI test packet opened, one sealed here opened by pyca).
Then the app on the console with the stand-in radio: the service announces
its NodeInfo, holds the radio by handle on the setup's slot, sends what the
window typed, keeps what another node sent with its RSSI and SNR, learns a
node's User and answers its request, and takes, sends, acknowledges and
tracks direct messages."""

import base64
import json
import os
import shutil
import struct

import pytest

import crypto_vectors as V
from runtime import moybyte, scheduler as S
from runtime import moy_lora as N
from runtime.host import lora_sim

from ws_helpers import build_desktop_ws, build_ws, make_drv, open_cart, select_on_home

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CART = os.path.join(ROOT, "tests", "fixtures", "apps", "moymesh.moy")
TITLE = "Moy Mesh"
H = bytes.fromhex
DEFAULT_KEY = H("d4f1bb3a20290759f0bcffabcf4e6901")
EU = 869525000
# The Meshtastic Python CLI's example channel link (example_config.yaml):
# the default channel and a LoRa config of region US, hop limit 3, TX on and
# 30 dBm.
CLI_URL = "https://www.meshtastic.org/e/#CgMSAQESCDgBQANIAVAe"

# Glue the tests read the codec through: X(hex) bytes in, H(bytes) and J(...)
# (each number as unsigned hex) out, D(...) a decoded packet, P(...) a Data
# protobuf, T(v) any value as text, JS(v) any value as JSON.
GLUE = """
function X(h) return (h:gsub("..", function(c) return string.char(tonumber(c, 16)) end)) end
function H(s) return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end)) end
function J(...)
  local t = table.pack(...)
  for i = 1, t.n do t[i] = string.format("%x", t[i]) end
  return table.concat(t, ",", 1, t.n)
end
function T(v) return tostring(v) end
function D(p, key, ch)
  local h, port, pl = decode(p, key, ch)
  if h == nil then return "nil" end
  return string.format("%x,%x,%d,", h[2], h[3], port) .. H(pl)
end
function P(b)
  local port, pl = data_decode(b)
  if port == nil then return "nil" end
  return port .. ":" .. pl
end
local function js(v)
  local t = type(v)
  if t == "table" then
    if #v > 0 or next(v) == nil then
      local o = {}
      for i = 1, #v do o[i] = js(v[i]) end
      return "[" .. table.concat(o, ",") .. "]"
    end
    local keys, o = {}, {}
    for k in pairs(v) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    for _, k in ipairs(keys) do o[#o + 1] = string.format("%q", k) .. ":" .. js(v[k]) end
    return "{" .. table.concat(o, ",") .. "}"
  elseif t == "string" then
    return '"' .. H(v) .. '"'
  elseif v == nil then
    return "null"
  end
  return tostring(v)
end
JS = js
"""


class _Codec:
    """service.lua's functions on a Lua state of their own, after mesh.lua
    as the service runs them."""

    def __init__(self):
        from runtime import lua_binding
        self.run = lua_binding.HostLuaRun(bytearray(64 * 64 * 2), 64, 64)
        for name in ("mesh.lua", "service.lua"):
            with open(os.path.join(CART, name)) as f:
                self.run.exec(f.read(), name)
        self.run.exec(GLUE)

    def __call__(self, expr):
        self.run.exec("__R = " + expr)
        return self.run.get_global("__R")

    def json(self, expr):
        """A Lua value as JSON, its texts hex (JS above), decoded back."""
        def unhex(v):
            if isinstance(v, dict):
                return {k: unhex(x) for k, x in v.items()}
            if isinstance(v, list):
                return [unhex(x) for x in v]
            if isinstance(v, str):
                return H(v).decode("utf-8", "surrogateescape")
            return v
        return unhex(json.loads(self("JS(%s)" % expr)))

    def close(self):
        self.run.close()


@pytest.fixture
def c():
    codec = _Codec()
    yield codec
    codec.close()


# -- an independent implementation -------------------------------------------------------


def _ctr(key, block, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    e = Cipher(algorithms.AES(key), modes.CTR(block)).encryptor()
    return e.update(data) + e.finalize()


def _djb2(text):
    h = 5381
    for ch in text.encode():
        h = (h * 33 + ch) & 0xFFFFFFFF
    return h


def _xor(data):
    h = 0
    for ch in data:
        h ^= ch
    return h


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb(b):
    """A protobuf message's fields: {field: [value, ...]}, a varint or
    fixed32 an int, a length-delimited field its bytes."""
    out, at = {}, 0
    while at < len(b):
        tag, at = _read_varint(b, at)
        field, kind = tag >> 3, tag & 7
        if kind == 0:
            v, at = _read_varint(b, at)
        elif kind == 2:
            n, at = _read_varint(b, at)
            v, at = bytes(b[at:at + n]), at + n
        elif kind == 5:
            v, at = struct.unpack_from("<I", b, at)[0], at + 4
        else:
            raise ValueError(kind)
        out.setdefault(field, []).append(v)
    assert at == len(b)
    return out


def _read_varint(b, at):
    n, shift = 0, 0
    while True:
        c = b[at]
        at += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, at
        shift += 7


def _data(port, payload, want_response=False, request_id=0):
    b = b"\x08" + _varint(port) + b"\x12" + _varint(len(payload)) + payload
    if want_response:
        b += b"\x18\x01"
    if request_id:
        b += b"\x35" + struct.pack("<I", request_id)
    return b


def _user(num, long, short, key, hw=50):
    def s(n, v):
        return bytes([n << 3 | 2]) + _varint(len(v)) + v
    return (s(1, ("!%08x" % num).encode()) + s(2, long.encode()) + s(3, short.encode())
            + b"\x28" + _varint(hw) + s(8, key))


def _packet(data, frm, pid, to=0xFFFFFFFF, chash=8, key=DEFAULT_KEY, flags=0x63):
    """A packet as a Meshtastic node sends it on a channel: the header, then
    the Data protobuf under AES-CTR."""
    block = struct.pack("<Q", pid) + struct.pack("<I", frm) + bytes(4)
    head = struct.pack("<IIIBBBB", to, frm, pid, flags, chash, 0, 0)
    return head + _ctr(key, block, data)


def _node_packet(text, frm, pid, chash=8, key=DEFAULT_KEY, to=0xFFFFFFFF):
    return _packet(b"\x08\x01\x12" + bytes([len(text)]) + text + b"\x48\x00", frm, pid,
                   to=to, chash=chash, key=key)


def _open(packet, key=DEFAULT_KEY):
    """A channel packet read: its header fields and its Data's fields."""
    head = struct.unpack("<IIIBBBB", packet[:16])
    block = struct.pack("<Q", head[2]) + struct.pack("<I", head[1]) + bytes(4)
    return head, _pb(_ctr(key, block, packet[16:]))


def _x25519_public(secret):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    return X25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _pki_key(secret, peer):
    import hashlib
    from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                                  X25519PublicKey)
    shared = X25519PrivateKey.from_private_bytes(secret).exchange(
        X25519PublicKey.from_public_bytes(peer))
    return hashlib.sha256(shared).digest()


def _pki_open(secret, peer, packet):
    """Meshtastic's CryptoEngine::decryptCurve25519 over pyca."""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    to, frm, pid = struct.unpack("<III", packet[:12])
    body = packet[16:]
    nonce = struct.pack("<I", pid) + body[-4:] + struct.pack("<I", frm) + b"\0"
    return AESCCM(_pki_key(secret, peer), tag_length=8).decrypt(nonce, body[:-4], None)


def _pki_packet(secret, peer, to, frm, pid, data, extra=b"\x11\x22\x33\x44", flags=0x6B):
    """Meshtastic's CryptoEngine::encryptCurve25519 over pyca."""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    nonce = struct.pack("<I", pid) + extra + struct.pack("<I", frm) + b"\0"
    sealed = AESCCM(_pki_key(secret, peer), tag_length=8).encrypt(nonce, data, None)
    return struct.pack("<IIIBBBB", to, frm, pid, flags, 0, 0, 0) + sealed + extra


def _x(b):
    return 'X"%s"' % bytes(b).hex()


# -- the codec --------------------------------------------------------------------------


def test_the_long_fast_channel_is_meshtastic_s(c):
    assert c('H(channel_key("\\1"))') == DEFAULT_KEY.hex()
    assert c('channel_key("\\2"):byte(16)') == 0x02 and c('channel_key("\\0")') == ""
    assert c('channel_key("\\10"):byte(16)') == 0x0A
    assert c('channel_hash("LongFast", channel_key("\\1"))') == 0x08
    assert c('frequency("EU_868")') == 869525000
    assert c('frequency("US")') == 906875000
    # a name whose hash is past 31 bits: unsigned on 32-bit integers
    assert c('J(djb2("LongModerate"))') == "afa60066" == "%x" % _djb2("LongModerate")
    assert c('frequency("US", "LongModerate")') == 923625000
    assert c('power("EU_868")') == 22 and c('power("JP")') == 13
    assert c('J(SYNC, PREAMBLE)') == "2b,10"


# MeshRadio.h's modemPresetToParams for a region that is not wide-band, and
# DisplayFormatters' names: (sf, bandwidth, coding rate, the code, the name).
PRESETS = {
    "LONG_FAST": (11, 250000, 5, 0, "LongFast"),
    "LONG_MODERATE": (11, 125000, 8, 7, "LongMod"),
    "LONG_SLOW": (12, 125000, 8, 1, "LongSlow"),
    "LONG_TURBO": (11, 500000, 8, 9, "LongTurbo"),
    "MEDIUM_SLOW": (10, 250000, 5, 3, "MediumSlow"),
    "MEDIUM_FAST": (9, 250000, 5, 4, "MediumFast"),
    "SHORT_SLOW": (8, 250000, 5, 5, "ShortSlow"),
    "SHORT_FAST": (7, 250000, 5, 6, "ShortFast"),
    "SHORT_TURBO": (7, 500000, 5, 8, "ShortTurbo"),
}


def test_every_preset_is_the_firmware_s(c):
    for name, (sf, bw, cr, code, title) in PRESETS.items():
        m = c.json("MODEMS.%s" % name)
        assert (m["sf"], m["bw"], m["cr"], m["code"], m["title"]) == (sf, bw, cr, code, title)
    assert c.json("PRESETS")[0] == "LONG_FAST" and set(c.json("PRESETS")) == set(PRESETS)
    # a preset wider than the region's band runs as LongFast
    assert c('select(2, modem("EU_868", "SHORT_TURBO"))') == "LONG_FAST"
    assert c('select(2, modem("US", "SHORT_TURBO"))') == "SHORT_TURBO"


def _slot_hz(start, end, bw, name, slot=0):
    """RadioInterface::applyModemConfig's frequency, in Hz."""
    n = (end - start) // bw
    i = (slot - 1) % n if slot else _djb2(name) % n
    return start + bw // 2 + i * bw


def test_a_slot_is_hashed_from_the_primary_s_name_or_given(c):
    # EU_868 holds one 250 kHz slot: every 250 kHz preset sits at 869.525
    assert c('slots("EU_868", 250000)') == 1
    for name in ("LongFast", "MediumFast", "Srbija"):
        assert c('frequency("EU_868", "%s", 250000)' % name) == EU
    assert c('frequency("EU_868", "Srbija", 250000, 1)') == EU
    assert c('slots("EU_868", 125000)') == 2
    for region, start, end in (("US", 902000000, 928000000), ("EU_433", 433000000, 434000000),
                               ("ANZ", 915000000, 928000000)):
        for name, bw in (("LongFast", 250000), ("MediumFast", 250000), ("LongMod", 125000),
                         ("ShortTurbo", 500000)):
            want = _slot_hz(start, end, bw, name)
            assert c('frequency("%s", "%s", %d)' % (region, name, bw)) == want, (region, name)
        for slot in (1, 20, 104, 105):
            assert c('frequency("%s", "x", 250000, %d)' % (region, slot)) == \
                _slot_hz(start, end, 250000, "x", slot)
    # the slots the community guides publish for the US: LongFast 20, MediumFast 45
    assert c('frequency("US", "LongFast", 250000)') == 906875000 == \
        c('frequency("US", "x", 250000, 20)')
    assert c('frequency("US", "MediumFast", 250000)') == 913125000 == \
        c('frequency("US", "x", 250000, 45)')


def test_mesh_serbia_s_setup_tunes_and_hashes(c):
    serbia = '{region = "EU_868", preset = "MEDIUM_FAST", slot = 1, hops = 3, ' \
             'channels = {{name = "Srbija", psk = "AQ=="}}}'
    c.run.exec("SRB = setup_check(%s)" % serbia)
    c.run.exec("F, SF_, BW_, CR_, PW_, PR_ = tuning(SRB)")
    assert c("J(F, SF_, BW_, CR_, PW_)") == ",".join("%x" % v for v in (EU, 9, 250000, 5, 22))
    assert c("PR_") == "MEDIUM_FAST"
    want = _xor(b"Srbija") ^ _xor(DEFAULT_KEY)
    assert c("channel_keys(SRB)[1].hash") == want == c(
        'channel_hash("Srbija", channel_key("\\1"))')
    assert c('channel_keys(SRB)[1].name') == "Srbija"


def test_a_channel_s_key_and_name_are_the_firmware_s(c):
    key16, key32 = bytes(range(16)), bytes(range(32))
    s = ('setup_check({preset = "MEDIUM_FAST", channels = {{psk = "AQ=="}, '
         '{name = "two", psk = "Ag=="}, {name = "aes", psk = "%s"}, '
         '{name = "big", psk = "%s"}, {name = "same", psk = ""}, {name = "open", psk = "AA=="}}})'
         % (base64.b64encode(key16).decode(), base64.b64encode(key32).decode()))
    c.run.exec("K = channel_keys(%s)" % s)
    got = c.json("K")
    keys = [DEFAULT_KEY, DEFAULT_KEY[:15] + b"\x02", key16, key32, DEFAULT_KEY, b""]
    names = ["MediumFast", "two", "aes", "big", "same", "open"]
    assert [g["name"].encode() for g in got] == [n.encode() for n in names]
    assert [c("H(K[%d].key)" % (i + 1)) for i in range(len(keys))] == [k.hex() for k in keys]
    assert [g["hash"] for g in got] == [_xor(n.encode()) ^ _xor(k) for n, k in zip(names, keys)]
    # a short key is padded with zeros; a primary with none is LongFast's
    assert c.json('setup_check({channels = {{psk = "AQI="}}}).channels')[0]["psk"] == \
        base64.b64encode(b"\x01\x02" + bytes(14)).decode()
    assert c.json("setup_check({}).channels") == [{"name": "", "psk": "AQ=="}]
    assert c.json("setup_check({hops = 9, slot = -1, region = 'XX', preset = 'NO'})") == {
        "region": "EU_868", "preset": "LONG_FAST", "slot": 0, "hops": 3,
        "channels": [{"name": "", "psk": "AQ=="}]}
    nine = "{" + ", ".join('{name = "c%d"}' % i for i in range(9)) + "}"
    assert len(c.json("setup_check({channels = %s}).channels" % nine)) == 8


def test_base64_is_python_s(c):
    for raw in (b"", b"\x01", b"ab", b"abc", bytes(range(200)), b"\xfb\xff\xfe"):
        assert c("H(b64(%s))" % _x(raw)) == base64.b64encode(raw).hex()
        assert c("H(b64(%s, true))" % _x(raw)) == \
            base64.urlsafe_b64encode(raw).rstrip(b"=").hex()
        for text in (base64.b64encode(raw), base64.urlsafe_b64encode(raw).rstrip(b"=")):
            assert c('H(b64_bytes("%s"))' % text.decode()) == raw.hex()
    assert c('T(b64_bytes("a$b"))') == "nil" and c('T(b64_bytes("abcde"))') == "nil"


def test_the_cli_s_channel_link_reads(c):
    c.run.exec('PV = url_parse("%s")' % CLI_URL)
    p = c.json("PV")
    assert p["channels"] == [{"name": "", "psk": "AQ=="}]
    assert (p["region"], p["preset"], p["slot"], p["hops"], p["add"]) == \
        ("US", "LONG_FAST", 0, 3, False)
    assert c.json("setup_merge(PV, setup_check({}))")["region"] == "US"
    # read back: the link Moy Mesh makes of what it read is the same setup
    c.run.exec("AGAIN = url_parse(url_make(setup_merge(PV, setup_check({}))))")
    assert c.json("AGAIN")["channels"] == p["channels"]
    assert c.json("AGAIN")["region"] == "US"


def test_a_link_made_here_reads_in_an_independent_reader(c):
    url = c('url_make({region = "EU_868", preset = "MEDIUM_FAST", slot = 1, hops = 3, '
            'channels = {{name = "Srbija", psk = "AQ=="}, {name = "Tajni", psk = "%s"}}})'
            % base64.b64encode(bytes(range(32))).decode())
    assert url.startswith("https://meshtastic.org/e/#") and "=" not in url
    frag = url.split("#", 1)[1]
    cs = _pb(base64.urlsafe_b64decode(frag + "=" * (-len(frag) % 4)))
    settings = [_pb(s) for s in cs[1]]
    assert settings[0] == {2: [b"\x01"], 3: [b"Srbija"]}
    assert settings[1] == {2: [bytes(range(32))], 3: [b"Tajni"]}
    lora = _pb(cs[2][0])
    # use_preset, modem_preset, region, hop_limit, tx_enabled, channel_num
    assert lora == {1: [1], 2: [4], 7: [3], 8: [3], 9: [1], 11: [1]}
    c.run.exec('PV = url_parse("%s")' % url)
    p = c.json("PV")
    assert (p["region"], p["preset"], p["slot"], p["hops"]) == ("EU_868", "MEDIUM_FAST", 1, 3)
    assert [ch["name"] for ch in p["channels"]] == ["Srbija", "Tajni"]
    assert p["warnings"] == []


def test_a_link_s_preview_says_what_it_does_not_take(c):
    def link(settings, lora, add=False):
        body = b"".join(b"\x0a" + _varint(len(s)) + s for s in settings)
        body += b"\x12" + _varint(len(lora)) + lora
        return "https://meshtastic.org/e/%s#%s" % (
            "?add=true" if add else "",
            base64.urlsafe_b64encode(body).rstrip(b"=").decode())
    aead = b"\x12\x01\x01\x1a\x01A\x40\x01"
    c.run.exec('PV = url_parse("%s")' % link([aead], b"\x08\x01\x10\x10\x38\x63"))
    p = c.json("PV")
    assert p["preset"] == "LONG_FAST" and p.get("region") is None
    assert len(p["warnings"]) == 3, p["warnings"]       # AEAD, the preset, the region
    for bad in ("https://example.com/e/#CgMSAQE", "meshtastic.org/x/#CgMSAQE",
                "https://meshtastic.org/e/#$$$", "https://meshtastic.org/e/#EgA"):
        assert c('T(url_parse("%s"))' % bad) == "nil", bad
    # an add link's new channels join this setup's; a name it has stays
    c.run.exec('PV = url_parse("%s")' % link([b"\x1a\x03new\x12\x01\x02",
                                              b"\x1a\x03old\x12\x01\x03"], b"\x38\x01", add=True))
    assert c.json("PV")["add"] is True
    got = c.json('setup_merge(PV, setup_check({channels = {{psk = "AQ=="}, {name = "old"}}}))')
    assert [ch["name"] for ch in got["channels"]] == ["", "old", "new"]
    assert got["region"] == "EU_868", "an add link leaves the radio as it is"


def test_the_header_reads_the_firmware_s_pki_packet(c):
    radio = H(V.MESH_PKI_RADIO)
    got = c("J(parse_header(%s))" % _x(radio)).split(",")
    to, frm, pid, flags, chash, next_hop, relay = (int(v, 16) for v in got)
    assert (to, frm, pid) == (0x7A6D648C, 0x0929, 0x13B2D662)
    assert (flags & 7, flags >> 5, bool(flags & 8)) == (3, 3, True)
    assert (chash, next_hop, relay) == (0, 0, 0)
    assert c("H(header(0x%x, 0x%x, 0x%x, %d, %d))" % (to, frm, pid, flags, chash)) == radio[:16].hex()
    assert c("T(parse_header(%s))" % _x(radio[:15])) == "nil"
    assert c("hops_away(0x%x)" % flags) == 0 and c("T(hops_away(0x03))") == "nil"
    assert c("hops_away(0x61)") == 2 and c("answer_hops(0x61, 3)") == 3
    assert c("answer_hops(0x63, 3)") == 2 and c("answer_hops(0xE0, 3)") == 7


def _key_of(secret, peer):
    """The codec's AES-CCM key of X25519 `secret` and `peer`, as Lua."""
    return "pki_key(moybyte.x25519(%s, %s))" % (_x(secret), _x(peer))


def test_a_direct_message_opens_as_the_firmware_s_test_says(c):
    radio = H(V.MESH_PKI_RADIO)
    key = _key_of(H(V.MESH_PKI_SECRET), H(V.MESH_PKI_PEER))
    assert c("H(pki_open(%s, %s))" % (key, _x(radio))) == V.MESH_PKI_PLAIN
    assert c("P(%s)" % _x(H(V.MESH_PKI_PLAIN))) == "1:test"
    changed = bytearray(radio)
    changed[20] ^= 1
    assert c("T(pki_open(%s, %s))" % (key, _x(changed))) == "nil"


def test_a_direct_message_sealed_here_opens_in_pyca(c):
    alice, bob = H(V.X25519_ALICE), H(V.X25519_BOB)
    alice_pub, bob_pub = _x25519_public(alice), _x25519_public(bob)
    assert c("H(moybyte.x25519(%s))" % _x(alice)) == alice_pub.hex()
    plain = _data(1, b"hello Bob")
    packet = H(c("H(pki_packet(%s, 0x%x, 0x%x, 0x%x, flags(3, 3, true), %s, %s, 0x56))" % (
        _key_of(alice, bob_pub), 0xB0B, 0xA11CE, 0xDEADBEEF, _x(plain), _x(b"\x01\x02\x03\x04"))))
    assert packet[:16] == struct.pack("<IIIBBBB", 0xB0B, 0xA11CE, 0xDEADBEEF, 0x6B, 0, 0, 0x56)
    assert len(packet) == 16 + len(plain) + 12 and packet[-4:] == b"\x01\x02\x03\x04"
    assert _pki_open(bob, alice_pub, packet) == plain
    # and what pyca seals, the codec opens
    theirs = _pki_packet(bob, alice_pub, 0xA11CE, 0xB0B, 77, _data(1, b"hi Alice"))
    assert c("P(pki_open(%s, %s))" % (_key_of(alice, bob_pub), _x(theirs))) == "1:hi Alice"


def test_a_channel_packet_is_what_an_independent_encoder_makes(c):
    c.run.exec('KEY = channel_key("\\1")')
    mine = H(c('H(encode_text("hello mesh", 0x12345678, 0xCAFEF00D, KEY, 8))'))
    head = mine[:16]
    assert head == struct.pack("<IIIBBBB", 0xFFFFFFFF, 0x12345678, 0xCAFEF00D, 0x63, 8, 0, 0)
    block = struct.pack("<QII", 0xCAFEF00D, 0x12345678, 0)
    assert _ctr(DEFAULT_KEY, block, mine[16:]) == b"\x08\x01\x12\x0ahello mesh"
    theirs = _node_packet(b"from a node", 0xA1B2C3D4, 77)
    assert c("D(%s, KEY, 8)" % _x(theirs)) == "a1b2c3d4,4d,1," + b"from a node".hex()
    assert c("D(%s, KEY, 9)" % _x(theirs)) == "nil", "another channel's hash"
    assert c("D(%s, KEY, 8)" % _x(theirs[:10])) == "nil"
    long = 'encode_text(("\\xc3\\xa9"):rep(150), 1, 2, KEY, 8)'
    assert c("D(%s, KEY, 8)" % long).endswith(("é" * 100).encode().hex()), \
        "whole characters, 200 bytes"


def test_the_data_protobuf_reads_past_what_it_does_not_need(c):
    assert c('P(data_encode(1, ("x"):rep(200)))') == "1:" + "x" * 200
    # want_response, dest (fixed32), a nonce (fixed64), bitfield: read past
    full = b"\x08\x01\x12\x02hi\x18\x01\x25\x01\x02\x03\x04\x49" + bytes(8) + b"\x48\x01"
    assert c("P(%s)" % _x(full)) == "1:hi"
    # a varint of ten bytes (a negative int32 field): read past
    assert c("P(%s)" % _x(b"\x08\x01\x12\x02hi\x18" + b"\xff" * 9 + b"\x01")) == "1:hi"
    assert c("P(%s)" % _x(b"\x12\x02hi")) == "nil", "no portnum"
    assert c("P(%s)" % _x(b"\x08\x01\x12\x09hi")) == "nil", "a length past the end"
    assert c("P(%s)" % _x(b"\x08\x01\x0b")) == "nil", "a wire type it does not know"
    # want_response, by its field or the bitfield's bit 1; request_id
    assert c.json("select(3, data_decode(%s))" % _x(_data(4, b"", want_response=True))) == \
        {"want": True, "request": 0, "reply": 0}
    assert c.json("select(3, data_decode(%s))" % _x(b"\x08\x04\x12\x00\x48\x02")) == \
        {"want": True, "request": 0, "reply": 0}
    assert c("select(3, data_decode(%s)).request" % _x(_data(5, b"", request_id=0xCAFEF00D))) \
        == struct.unpack("<i", struct.pack("<I", 0xCAFEF00D))[0]
    mine = H(c("H(data_encode(5, routing_encode(35), {request_id = 0xCAFEF00D}))"))
    assert _pb(mine) == {1: [5], 2: [b"\x18\x23"], 6: [0xCAFEF00D]}
    assert _pb(H(c("H(routing_encode(0))"))) == {3: [0]}
    assert c("routing_decode(%s)" % _x(b"\x18\x23")) == 35 and c('routing_decode("")') == 0


def test_a_user_is_the_protobuf_meshtastic_reads(c):
    key = _x25519_public(H(V.X25519_BOB))
    mine = H(c('H(user_encode({id = "!0000abcd", long = "Kuhinja", short = "Kuh", '
               'hw = HW_MODEL, role = ROLE, key = %s}))' % _x(key)))
    assert _pb(mine) == {1: [b"!0000abcd"], 2: [b"Kuhinja"], 3: [b"Kuh"], 5: [255], 7: [1],
                         8: [key]}
    c.run.exec("U = user_decode(%s)" % _x(_user(0x1234, "Node \u0161", "N\u0161", key)))
    got = c.json("U")
    assert (got["id"], got["long"], got["short"], got["hw"]) == \
        ("!00001234", "Node \u0161", "N\u0161", 50)
    assert c("H(U.key)") == key.hex()
    assert "key" not in c.json("user_decode(%s)" % _x(_user(1, "a", "b", b"short")))
    assert c('short_default("moybyte-beef", 0x12345678)') == "5678"
    assert c('short_default("Kuhinja", 1)') == "Kuhi"
    assert c('short_default("\\u{0161}\\u{0161}\\u{0161}", 1)') == "šš"


def test_a_text_is_utf8_and_a_node_number_is_unsigned(c):
    for ok in ("hi", "été", "€", "\U0001f600"):
        assert c("T(utf8_ok(%s))" % _x(ok.encode())) == "true", ok
    for bad in (b"\xff", b"\xc3", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"):
        assert c("T(utf8_ok(%s))" % _x(bad)) == "false", bad
    assert c("node_name(0xA1B2C3D4)") == "!a1b2c3d4"
    assert c('J(node_number("!a1b2c3d4"))') == "a1b2c3d4"
    assert c('T(node_number("!a1b2c3"))') == "nil" and c("T(node_number(5))") == "nil"
    assert [c("T(node_ok(%s))" % n) for n in ("3", "4", "0xA1B2C3D4", "0xFFFFFFFE",
                                               "0xFFFFFFFF")] == \
        ["false", "true", "true", "true", "false"]


# -- the app on the console -------------------------------------------------------------


def _install(ws, src=CART, tweak=None):
    """Moy Mesh into `ws`'s store, as `pkg install moymesh` leaves it;
    `tweak(service_source)` the service as installed."""
    dst = os.path.join(ws.carts_root, "moymesh.moy")
    shutil.copytree(src, dst)
    if tweak is not None:
        path = os.path.join(dst, "service.lua")
        with open(path) as f:
            text = tweak(f.read())
        with open(path, "w") as f:
            f.write(text)
    ws.carts.rescan()


class _Window:
    """The window's Lua state: its globals read, its functions called."""

    def __init__(self, pl):
        self.lua = pl._lua
        assert self.lua is not None, pl.cart_error

    def get(self, name):
        return self.lua.get_global(name)

    def run(self, src):
        err = self.lua.exec(src, "test")
        assert err is None, err

    def row(self, i):
        self.run("__R = rows[%d]" % i)
        return self.get("__R")

    def rows(self):
        return [self.row(i + 1) for i in range(self.lua.get_global_len("rows"))]


def _open_mesh(tmp_path, tweak=None, name=None):
    lora_sim.install()
    ws = build_ws(tmp_path)
    if name is not None:
        ws.system.set("name", name)
    _install(ws, tweak=tweak)
    S.attach(ws)
    assert select_on_home(ws, TITLE) is not None
    ws.open()
    pl = ws.run.player
    assert pl.cart_error is None, pl.cart_error
    drv = make_drv(ws)
    for _ in range(6):
        drv.frame(1 / 30)
        S.SCHED.poll()
    svc = S.SCHED.find("moymesh")
    assert svc is not None and svc.state != S.EXITED
    return ws, pl, drv, svc


def _close_mesh(svc):
    S.SCHED.stop(svc.pid)
    S.SCHED.poll()
    while N.owner():
        moybyte.end(N.owner())
    lora_sim.remove()


@pytest.fixture
def mesh(tmp_path):
    got = _open_mesh(tmp_path)
    yield got
    _close_mesh(got[3])


def _frames(drv, n=4):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(1 / 30)
        S.SCHED.poll()


def _sent(drv):
    """Every transmission the service makes now, each one's air time done
    before the next."""
    out = []
    for _ in range(16):
        _frames(drv)
        got = lora_sim.sent()
        if got is None:
            return out
        out.append(got)
        N.step(0)
    return out


def _hear(drv, packet, freq=EU, sf=11, rssi=-90, snr=24):
    """`packet` on the air, once the radio is done sending what it sends
    (each transmission stays for `_sent` to read)."""
    for _ in range(8):
        if lora_sim.air(packet, freq, sf, rssi=rssi, snr=snr, bw=250000, sync=0x2B):
            break
        N.step(0)
        _frames(drv)
    else:
        raise AssertionError("the radio never listened")
    N.step(0)
    _frames(drv)


def _prefs(pl, key):
    return pl.ns["prefs"].get(key)


def _me(pl):
    return int(_prefs(pl, "node")[1:], 16)


def _my_public(ws):
    """The service's public key, as the console keeps its pair."""
    from runtime import app_keys
    return H(app_keys.public(ws.carts_root, "moymesh", "node"))


def _u32(v):
    return v & 0xFFFFFFFF


THEM, THEIR_SECRET = 0xA1B2C3D4, H(V.X25519_BOB)


def _introduce(drv, pl, num=THEM, secret=THEIR_SECRET, pid=900, want_response=False, to=None):
    """Node `num` announces its User (key from `secret`) on LongFast."""
    user = _user(num, "Their Node", "THEM", _x25519_public(secret))
    _hear(drv, _packet(_data(4, user, want_response=want_response), num, pid,
                       to=0xFFFFFFFF if to is None else to))


def test_the_service_announces_its_node_info_at_its_start(mesh):
    ws, pl, drv, svc = mesh
    (packet, freq, sf, bw, sync, iq, pre), = _sent(drv)
    assert (freq, sf, bw, sync, iq, pre) == (EU, 11, 250000, 0x2B, False, 16)
    head, data = _open(packet)
    me = _me(pl)
    assert head[:5] == (0xFFFFFFFF, me, head[2], 0x63, 8)
    assert head[6] == me & 0xFF, "the relay byte is its own"
    assert data[1] == [4] and data[3] == [1], "NODEINFO_APP, asking for replies"
    user = _pb(data[2][0])
    assert user[1] == [("!%08x" % me).encode()]
    assert user[2] == [b"moybyte-beef"], "the console's name (ssh_service.default_name)"
    assert user[3] == [("%04x" % (me & 0xFFFF)).encode()]
    assert (user[5], user[7]) == ([255], [1]), "PRIVATE_HW, CLIENT_MUTE"
    assert user[8] == [_my_public(ws)], "its key pair's public half"
    assert _prefs(pl, "key") is None, "the console keeps the pair, the prefs nothing of it"
    assert moybyte.name() == "moybyte-beef"
    win = _Window(pl)
    win.run("NAME = moybyte.name()")
    assert win.get("NAME") == "moybyte-beef", "a Lua app's run holds the console call"


def test_its_names_are_the_console_s_or_the_prefs(tmp_path):
    ws, pl, drv, svc = _open_mesh(tmp_path, name="Kuhinja š")
    try:
        user = _pb(_open(_sent(drv)[0][0])[1][2][0])
        assert (user[2], user[3]) == (["Kuhinja š".encode()], [b"Kuhi"])
        key = _my_public(ws)
        pl.ns["prefs"].set("long_name", "x" * 50)
        pl.ns["prefs"].set("short_name", "ššš")
        moybyte.send("moymesh", "retune")
        user = _pb(_open(_sent(drv)[0][0])[1][2][0])
        assert (user[2], user[3]) == ([b"x" * 24], ["šš".encode()])
        assert _my_public(ws) == key == user[8][0], "made once"
        moybyte.send("moymesh", "retune")
        assert _sent(drv) == [], "nothing it announces moved"
    finally:
        _close_mesh(svc)


def test_the_service_holds_the_radio_and_sends_what_the_window_typed(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    _sent(drv)
    assert N.owner() == svc.pid
    name = _prefs(pl, "node")
    node = int(name[1:], 16)
    assert 3 < node < 0xFFFFFFFF and win.get("node") == name == "!%08x" % node
    win.run('send("hi from moybyte")')
    (packet, freq, sf, bw, sync, iq, pre), = _sent(drv)
    assert (freq, sf, bw, sync, iq, pre) == (EU, 11, 250000, 0x2B, False, 16)
    to, frm, pid, flags, chash = struct.unpack("<IIIBB", packet[:14])
    assert (to, frm, flags, chash) == (0xFFFFFFFF, node, 0x63, 8)
    block = struct.pack("<QII", pid, frm, 0)
    assert _ctr(DEFAULT_KEY, block, packet[16:]) == b"\x08\x01\x12\x0fhi from moybyte"
    _frames(drv)
    assert win.row(1) == "me: hi from moybyte"
    rec, = _prefs(pl, "log")
    assert (rec["ch"], _u32(rec["id"]), rec["from"]) == (0, pid, "")


def test_the_service_keeps_another_node_s_text_with_its_signal(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    _sent(drv)
    p = _node_packet(b"hello T-Deck", 0xA1B2C3D4, 4242)
    _hear(drv, p, rssi=-112, snr=-34)
    assert win.rows() == ["!a1b2c3d4 -112 dBm -8.5 dB: hello T-Deck"]
    rec, = _prefs(pl, "log")
    assert {k: rec[k] for k in ("from", "text", "rssi", "snr", "ch")} == \
        {"from": "!a1b2c3d4", "text": "hello T-Deck", "rssi": -112, "snr": -8.5, "ch": 0}
    node = _prefs(pl, "nodes")["!a1b2c3d4"]
    assert (node["rssi"], node["snr"], node["hops"], node["ch"]) == (-112, -8.5, 0, 0)
    # a node heard with no User yet is sent this one's, asking for its own
    (ask, *_), = _sent(drv)
    head, data = _open(ask)
    assert (head[0], data[1], data[3]) == (0xA1B2C3D4, [4], [1])
    # a repeat (a relay's copy), another channel, its own echo and a text
    # to another node are dropped
    me = _me(pl)
    for again in (p, _node_packet(b"other", 0xA1B2C3D4, 4243, chash=9),
                  _node_packet(b"echo", me, 99),
                  _node_packet(b"not yours", 0xA1B2C3D4, 4244, to=0x0BADCAFE)):
        _hear(drv, again)
    assert len(_prefs(pl, "log")) == 1


def test_a_node_s_user_is_kept_and_its_request_answered(mesh):
    ws, pl, drv, svc = mesh
    _sent(drv)
    _introduce(drv, pl, want_response=True, pid=0x1234)
    node = _prefs(pl, "nodes")["!a1b2c3d4"]
    assert (node["long"], node["short"], node["hw"]) == ("Their Node", "THEM", 50)
    assert node["key"] == _x25519_public(THEIR_SECRET).hex()
    (reply, *_), = _sent(drv)
    head, data = _open(reply)
    assert head[0] == THEM and data[1] == [4] and data[6] == [0x1234]
    assert 3 not in data, "a reply asks for none"
    assert _pb(data[2][0])[1] == [("!%08x" % _me(pl)).encode()]
    # asked again at once: answered once in QUIET_TICKS
    _introduce(drv, pl, want_response=True, pid=0x1235)
    assert _sent(drv) == []
    # a NodeInfo with another key is dropped: the first key is the one kept
    user = _user(THEM, "Impostor", "IMP", _x25519_public(H(V.X25519_ALICE)))
    _hear(drv, _packet(_data(4, user), THEM, 0x1236))
    assert _prefs(pl, "nodes")["!a1b2c3d4"]["long"] == "Their Node"


def _dm_log(pl):
    return [r for r in _prefs(pl, "log") or () if r.get("dm")]


def test_a_direct_message_to_a_node_without_a_key_is_not_sent(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    _sent(drv)
    win.run('moybyte.send(ME, "send", "psst", "!a1b2c3d4")')
    sent = _sent(drv)
    rec, = _dm_log(pl)
    assert (rec["dm"], rec["state"], rec["why"]) == ("!a1b2c3d4", "failed", "no key")
    (ask, *_), = sent
    head, data = _open(ask)
    assert (head[0], data[1], data[3]) == (THEM, [4], [1]), "its NodeInfo asked for"


def test_a_direct_message_goes_under_the_node_s_key_and_is_delivered(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    _sent(drv)
    _introduce(drv, pl)
    _sent(drv)
    win.run('moybyte.send(ME, "send", "psst \\u{0161}", "!a1b2c3d4")')
    (packet, *_), = _sent(drv)
    me = _me(pl)
    to, frm, pid, flags, chash, nh, relay = struct.unpack("<IIIBBBB", packet[:16])
    assert (to, frm, flags, chash, relay) == (THEM, me, 0x6B, 0, me & 0xFF), "want-ack, PKI"
    my_pub = _my_public(ws)
    assert _pb(_pki_open(THEIR_SECRET, my_pub, packet)) == {1: [1], 2: ["psst š".encode()]}
    rec, = _dm_log(pl)
    assert (rec["state"], rec["pki"], _u32(rec["id"])) == ("sent", True, pid)
    # an ack from another node is not the node's
    _hear(drv, _packet(_data(5, b"\x18\x00", request_id=pid), 0x0BADCAFE, 1, to=me))
    assert _dm_log(pl)[0]["state"] == "sent"
    _hear(drv, _packet(_data(5, b"\x18\x00", request_id=pid), THEM, 2, to=me))
    assert _dm_log(pl)[0]["state"] == "delivered"


def test_a_nak_fails_a_direct_message_and_names_why(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    _sent(drv)
    _introduce(drv, pl)
    _sent(drv)
    win.run('moybyte.send(ME, "send", "one", "!a1b2c3d4")')
    (packet, *_), = _sent(drv)
    pid = struct.unpack("<I", packet[8:12])[0]
    _hear(drv, _packet(_data(5, b"\x18\x23", request_id=pid), THEM, 3, to=_me(pl)))
    rec, = _dm_log(pl)
    assert rec["state"] == "failed" and "key" in rec["why"]
    (info, *_), = _sent(drv)
    head, data = _open(info)
    assert head[0] == THEM and data[1] == [4], "its NodeInfo, for the key the node lacks"


def test_a_direct_message_with_no_ack_is_sent_three_times_then_fails(tmp_path):
    ws, pl, drv, svc = _open_mesh(
        tmp_path, tweak=lambda s: s.replace("TICK_MS = 5000", "TICK_MS = 10"))
    try:
        win = _Window(pl)
        _sent(drv)
        _introduce(drv, pl)
        _sent(drv)
        win.run('moybyte.send(ME, "send", "anyone?", "!a1b2c3d4")')
        tries = []
        for _ in range(400):
            for got in _sent(drv):
                if struct.unpack("<B", got[0][13:14])[0] == 0:
                    tries.append(got[0])
            if _dm_log(pl)[0]["state"] == "failed":
                break
        assert len(tries) == 3 and len(set(tries)) == 1, "the same packet, the same id"
        assert _dm_log(pl)[0]["why"] == "no ack"
    finally:
        _close_mesh(svc)


def test_a_direct_message_to_this_node_is_kept_and_acknowledged(mesh):
    ws, pl, drv, svc = mesh
    _sent(drv)
    me, my_pub = _me(pl), _my_public(ws)
    # from a node whose key it lacks: PKI_UNKNOWN_PUBKEY on the primary
    dm = _pki_packet(THEIR_SECRET, my_pub, me, THEM, 0x5150, _data(1, b"secret"))
    _hear(drv, dm)
    (nak, *_), = _sent(drv)
    head, data = _open(nak)
    assert head[0] == THEM and data[1] == [5] and data[6] == [0x5150]
    assert _pb(data[2][0]) == {3: [35]}
    assert _dm_log(pl) == []
    _introduce(drv, pl)
    _sent(drv)
    dm = _pki_packet(THEIR_SECRET, my_pub, me, THEM, 0x5151, _data(1, b"secret"))
    _hear(drv, dm, rssi=-80, snr=10)
    rec, = _dm_log(pl)
    assert (rec["from"], rec["dm"], rec["text"], rec["pki"], rec["rssi"]) == \
        ("!a1b2c3d4", "!a1b2c3d4", "secret", True, -80)
    (ack, *_), = _sent(drv)
    head, data = _open(ack)
    assert (head[0], head[3] & 0x08, head[4]) == (THEM, 0x08, 8), "a DM's ack wants one"
    assert data[1] == [5] and data[6] == [0x5151] and _pb(data[2][0]) == {3: [0]}
    # a copy (the sender missed the ack) is acked again, kept once
    _hear(drv, dm)
    assert len(_sent(drv)) == 1 and len(_dm_log(pl)) == 1
    # a legacy direct message, under the channel's key
    _hear(drv, _node_packet(b"old style", THEM, 0x5152, to=me))
    assert _dm_log(pl)[-1]["pki"] is False and _dm_log(pl)[-1]["text"] == "old style"


def test_a_setup_retunes_and_a_secondary_channel_is_heard(mesh):
    ws, pl, drv, svc = mesh
    _sent(drv)
    prefs = pl.ns["prefs"]
    secret = bytes(range(16))
    prefs.set("preset", "MEDIUM_FAST")
    prefs.set("slot", 1)
    prefs.set("hops", 5)
    prefs.set("channels", [{"name": "Srbija", "psk": "AQ=="},
                           {"name": "Tajni", "psk": base64.b64encode(secret).decode()}])
    moybyte.send("moymesh", "retune")
    (info, freq, sf, bw, *_), = _sent(drv)
    srbija = _xor(b"Srbija") ^ _xor(DEFAULT_KEY)
    assert (freq, sf, bw) == (EU, 9, 250000) and info[12] == 0xA5 and info[13] == srbija
    _hear(drv, _node_packet(b"zdravo", THEM, 1, chash=srbija), sf=9)
    tajni = _xor(b"Tajni") ^ _xor(secret)
    _hear(drv, _node_packet(b"tajna", THEM, 2, chash=tajni, key=secret), sf=9)
    log = _prefs(pl, "log")
    assert [(r["text"], r["ch"]) for r in log] == [("zdravo", 0), ("tajna", 1)]
    _sent(drv)
    _Window(pl).run('moybyte.send(ME, "send", "na drugom", 1)')
    (packet, *_), = _sent(drv)
    assert packet[13] == tajni
    assert _open(packet, key=secret)[1][2] == [b"na drugom"]


def test_the_log_keeps_its_budget_and_every_thread(mesh):
    ws, pl, drv, svc = mesh
    _sent(drv)
    me = _me(pl)
    _hear(drv, _node_packet(b"a direct one", THEM, 7, to=me))
    for i in range(40):
        _hear(drv, _node_packet(b"chatter %d" % i, 0x0BADCAFE, 100 + i))
    log = _prefs(pl, "log")
    assert len(log) == 30 and log[0]["text"] == "a direct one"
    assert log[-1]["text"] == "chatter 39"


def test_the_manifest_asks_for_the_radio_and_names_its_service():
    with open(os.path.join(CART, "manifest.json")) as f:
        man = json.load(f)
    assert (man["id"], man["title"]) == ("moymesh", TITLE)
    assert man["moybyte"]["type"] == "app"
    assert "Meshtastic" in man["description"]
    assert (man["runtime"], man["main"]) == ("lua", "main.lua")
    assert man["moybyte"]["entries"] == {"service": "service.lua"}
    assert set(man["moybyte"]["required"]) == {"lora", "prefs"}


def test_a_new_region_retunes_the_radio(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    _sent(drv)
    pl.ns["prefs"].set("region", "US")
    moybyte.send("moymesh", "retune")
    _frames(drv)
    assert not lora_sim.air(_node_packet(b"eu", 1234, 1), EU, 11)
    _sent(drv)
    assert lora_sim.air(_node_packet(b"us", 1234, 2), 906875000, 11)
    N.step(0)
    _frames(drv)
    assert win.row(1).startswith("!000004d2 ") and win.row(1).endswith(": us")
    assert win.get("region") == 3, "the window's region is the one prefs name"


def test_without_a_radio_mesh_is_no_run_tile_but_stays_a_project(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True)
    _install(ws)

    def titles(rows):
        return [c.get("title") for c in rows if c]
    desk = ws.wm._backdrop_layer
    assert TITLE not in titles(ws._runnable_items())
    assert TITLE not in titles(c for _k, _l, c in desk._icon_catalog())
    assert TITLE in titles(ws._launcher_items(ws.carts.all, "make"))
    lora_sim.install()
    try:
        assert TITLE in titles(ws._launcher_items(ws.carts.all, "apps"))
        assert ("moymesh", TITLE) in [(k, lb) for k, lb, _c in desk._icon_catalog()], \
            "its tile is the mesh icon slot"
    finally:
        lora_sim.remove()


def test_on_the_handheld_the_window_opens_on_the_field_below_the_bar(tmp_path):
    from runtime import host_app
    lora_sim.install()
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(320, 240))
    try:
        _install(ws)
        assert select_on_home(ws, TITLE) is not None
        ws.open()
        win = _Window(ws.run.player)
        win.run("local page = ui.page\n"
                "ui.page = function(...)\n"
                "  PX, PY, PW, PH = page(...)\n"
                "  CX, CY, CW, CH = content_rect()\n"
                "  FOCUS = ui.focus()\n"
                "  return PX, PY, PW, PH\n"
                "end")
        drv = make_drv(ws)
        for _ in range(3):
            ws.mark_dirty()
            drv.frame(1 / 30)
        got = [win.get(k) for k in ("PX", "PY", "PW", "PH")]
        room = [win.get(k) for k in ("CX", "CY", "CW", "CH")]
        assert room[1] > 0 and got == room          # the page lies below the bar
        win.run("FOCUS = ui.focus()")
        assert win.get("FOCUS") == "Message"
    finally:
        S.SCHED.reset()         # opening Mesh started its service on the one scheduler
        while N.owner():
            moybyte.end(N.owner())
        lora_sim.remove()


def test_on_the_desk_mesh_is_a_window_laid_out_for_its_room(tmp_path):
    lora_sim.install()
    ws = build_desktop_ws(tmp_path)
    try:
        ws.open_desk()
        _install(ws)
        open_cart(ws, TITLE)
        pl = ws.run.player
        assert pl.cart_error is None, pl.cart_error
        assert ws.run.app_window_canvas, "it opens in a window, not a scaled raster"
        drv = make_drv(ws)
        for _ in range(3):
            ws.mark_dirty()
            drv.frame(1 / 30)
        win = _Window(pl)
        w, h = ws.app_window_size()
        assert (w, h) != (ws.glass_canvas.w, ws.glass_canvas.h)
        assert [win.get(k) for k in ("room_w", "room_h", "room_fs")] == \
            [w, h, ws.look.effective_font_scale()]
        pl.ns["prefs"].set("log", [{"from": "!5e6f7a8b", "text": "hi", "rssi": -98, "snr": 6.5}])
        win.run("_layout(200, %d, 1)" % h)
        # the readout leads the text: a row the room cuts short loses its text
        assert win.rows() == ["!5e6f7a8b -98 dBm 6.5 dB: hi"]
    finally:
        S.SCHED.reset()         # opening Mesh started its service on the one scheduler
        while N.owner():
            moybyte.end(N.owner())
        lora_sim.remove()
