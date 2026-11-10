"""Moy Mesh (tests/fixtures/apps/moymesh.moy, moybyte-org/carts' carts/moymesh/):
text messages on the LoRa radio, works with Meshtastic, a Lua window over a
Lua service.

The codec in its Lua service (service.lua), run on the host's Lua VM with
the boards' 32-bit integers (LUA_32BITS), against the Meshtastic firmware's
own numbers and an independent implementation over pyca's cryptography: the
LongFast channel's hash (8) and frequency slots (EU_868 869.525 MHz, US
906.875 MHz), the 16-byte header, the AES-CTR counter block, the Data
protobuf, and the firmware's own PKI test packet (test/test_crypto) decoded
and opened. Then the app on the console with the stand-in radio: the
service holds the radio by handle and tunes LongFast on the region's slot,
sends what the window typed as a packet a Meshtastic node decodes, keeps
what another node sent with its RSSI and SNR, and drops another channel's
packets, its own echo and a repeat."""

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

# Glue the tests read the codec through: X(hex) bytes in, H(bytes) and J(...)
# (each number as unsigned hex) out, D(...) a decoded packet, P(...) a Data
# protobuf, T(v) any value as text.
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

    def close(self):
        self.run.close()


@pytest.fixture
def c():
    codec = _Codec()
    yield codec
    codec.close()


def _ctr(key, block, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    e = Cipher(algorithms.AES(key), modes.CTR(block)).encryptor()
    return e.update(data) + e.finalize()


def _node_packet(text, frm, pid, chash=8, key=DEFAULT_KEY, to=0xFFFFFFFF):
    """A text message as a Meshtastic node sends it, built here apart from
    the cart: the header, then the Data protobuf under AES-CTR."""
    body = b"\x08\x01\x12" + bytes([len(text)]) + text + b"\x48\x00"
    block = struct.pack("<Q", pid) + struct.pack("<I", frm) + bytes(4)
    head = struct.pack("<IIIBBBB", to, frm, pid, 0x63, chash, 0, 0)
    return head + _ctr(key, block, body)


def _x(b):
    return 'X"%s"' % bytes(b).hex()


# -- the codec --------------------------------------------------------------------------


def test_the_long_fast_channel_is_meshtastic_s(c):
    assert c('H(channel_key("\\1"))') == DEFAULT_KEY.hex()
    assert c('channel_key("\\2"):byte(16)') == 0x02 and c('channel_key("\\0")') == ""
    assert c('channel_hash("LongFast", channel_key("\\1"))') == 0x08
    assert c('frequency("EU_868")') == 869525000
    assert c('frequency("US")') == 906875000
    # a name whose hash is past 31 bits: unsigned on 32-bit integers
    assert c('J(djb2("LongModerate"))') == "afa60066"
    assert c('frequency("US", "LongModerate")') == 923625000
    assert c('power("EU_868")') == 22 and c('power("JP")') == 13
    assert c('J(BW, SF, CR, SYNC, PREAMBLE)') == "3d090,b,5,2b,10"


def test_the_header_reads_the_firmware_s_pki_packet(c):
    radio = H(V.MESH_PKI_RADIO)
    got = c("J(parse_header(%s))" % _x(radio)).split(",")
    to, frm, pid, flags, chash, next_hop, relay = (int(v, 16) for v in got)
    assert (to, frm, pid) == (0x7A6D648C, 0x0929, 0x13B2D662)
    assert (flags & 7, flags >> 5, bool(flags & 8)) == (3, 3, True)
    assert (chash, next_hop, relay) == (0, 0, 0)
    assert c("H(header(0x%x, 0x%x, 0x%x, %d, %d))" % (to, frm, pid, flags, chash)) == radio[:16].hex()
    assert c("T(parse_header(%s))" % _x(radio[:15])) == "nil"


def test_a_direct_message_opens_as_the_firmware_s_test_says(c):
    radio = H(V.MESH_PKI_RADIO)
    args = "%s, %s" % (_x(H(V.MESH_PKI_SECRET)), _x(H(V.MESH_PKI_PEER)))
    assert c("H(pki_open(%s, %s))" % (args, _x(radio))) == V.MESH_PKI_PLAIN
    assert c("P(%s)" % _x(H(V.MESH_PKI_PLAIN))) == "1:test"
    changed = bytearray(radio)
    changed[20] ^= 1
    assert c("T(pki_open(%s, %s))" % (args, _x(changed))) == "nil"


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
    assert c("D(%s, KEY, 8)" % long).endswith(("\u00e9" * 100).encode().hex()), \
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


def test_a_text_is_utf8_and_a_node_number_is_unsigned(c):
    for ok in ("hi", "\u00e9t\u00e9", "\u20ac", "\U0001f600"):
        assert c("T(utf8_ok(%s))" % _x(ok.encode())) == "true", ok
    for bad in (b"\xff", b"\xc3", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"):
        assert c("T(utf8_ok(%s))" % _x(bad)) == "false", bad
    assert c("node_name(0xA1B2C3D4)") == "!a1b2c3d4"
    assert [c("T(node_ok(%s))" % n) for n in ("3", "4", "0xA1B2C3D4", "0xFFFFFFFE",
                                               "0xFFFFFFFF")] == \
        ["false", "true", "true", "true", "false"]


# -- the app on the console -------------------------------------------------------------


def _install(ws):
    """Moy Mesh into `ws`'s store, as `pkg install moymesh` leaves it."""
    shutil.copytree(CART, os.path.join(ws.carts_root, "moymesh.moy"))
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


@pytest.fixture
def mesh(tmp_path):
    lora_sim.install()
    ws = build_ws(tmp_path)
    _install(ws)
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
    yield ws, pl, drv, svc
    S.SCHED.stop(svc.pid)
    S.SCHED.poll()
    while N.owner():
        moybyte.end(N.owner())
    lora_sim.remove()


def _frames(drv, n=4):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(1 / 30)
        S.SCHED.poll()


def test_the_service_holds_the_radio_and_sends_what_the_window_typed(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    assert N.owner() == svc.pid
    name = pl.ns["prefs"].get("node")
    node = int(name[1:], 16)
    assert 3 < node < 0xFFFFFFFF and win.get("node") == name == "!%08x" % node
    win.run('send("hi from moybyte")')
    _frames(drv)
    packet, freq, sf, bw, sync, iq, pre = lora_sim.sent()
    assert (freq, sf, bw, sync, iq, pre) == (869525000, 11, 250000, 0x2B, False, 16)
    to, frm, pid, flags, chash = struct.unpack("<IIIBB", packet[:14])
    assert (to, frm, flags, chash) == (0xFFFFFFFF, node, 0x63, 8)
    block = struct.pack("<QII", pid, frm, 0)
    assert _ctr(DEFAULT_KEY, block, packet[16:]) == b"\x08\x01\x12\x0fhi from moybyte"
    N.step(0)
    _frames(drv)
    assert win.row(1) == "me: hi from moybyte"


def test_the_service_keeps_another_node_s_text_with_its_signal(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    p = _node_packet(b"hello T-Deck", 0xA1B2C3D4, 4242)
    assert lora_sim.air(p, 869525000, 11, rssi=-112, snr=-34, bw=250000, sync=0x2B)
    N.step(0)
    _frames(drv)
    assert win.rows() == ["!a1b2c3d4: hello T-Deck  -112 dBm -8.5 dB"]
    log = pl.ns["prefs"].get("log")
    assert log == [{"from": "!a1b2c3d4", "text": "hello T-Deck", "rssi": -112, "snr": -8.5}]
    # a repeat (a relay's copy), another channel and its own echo are dropped
    node = int(pl.ns["prefs"].get("node")[1:], 16)
    for again in (p, _node_packet(b"other", 0xA1B2C3D4, 4243, chash=9),
                  _node_packet(b"echo", node, 99)):
        assert lora_sim.air(again, 869525000, 11, bw=250000, sync=0x2B)
        N.step(0)
        _frames(drv)
    assert len(pl.ns["prefs"].get("log")) == 1


def test_a_new_region_retunes_the_radio(mesh):
    ws, pl, drv, svc = mesh
    win = _Window(pl)
    pl.ns["prefs"].set("region", "US")
    moybyte.send("moymesh", "retune")
    _frames(drv)
    assert not lora_sim.air(_node_packet(b"eu", 1234, 1), 869525000, 11)
    assert lora_sim.air(_node_packet(b"us", 1234, 2), 906875000, 11)
    N.step(0)
    _frames(drv)
    assert win.row(1).startswith("!000004d2: us")
    assert win.get("region") == 3, "the window's region is the one prefs name"


def test_the_manifest_asks_for_the_radio_and_names_its_service():
    with open(os.path.join(CART, "manifest.json")) as f:
        man = json.load(f)
    assert (man["id"], man["title"]) == ("moymesh", TITLE)
    assert man["moybyte"]["type"] == "app"
    assert "Meshtastic" in man["description"]
    assert (man["runtime"], man["main"]) == ("lua", "main.lua")
    assert man["moybyte"]["entries"] == {"service": "service.lua"}
    assert set(man["moybyte"]["required"]) == {"lora", "prefs"}


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
        assert win.rows() == ["!5e6f7a8b: hi"], "a narrow window drops the readout"
        win.run("_layout(%d, %d, 1)" % (w, h))
        assert win.rows() == ["!5e6f7a8b: hi  -98 dBm 6.5 dB"]
    finally:
        while N.owner():
            moybyte.end(N.owner())
        lora_sim.remove()
