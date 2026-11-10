"""Mesh (tests/fixtures/apps/moybyte.mesh.moy): Meshtastic text on the LoRa radio.

The codec in its service (service.py) against the Meshtastic firmware's own
numbers and an independent implementation over pyca's cryptography: the
LongFast channel's hash (8) and frequency slots (EU_868 869.525 MHz, US
906.875 MHz), the 16-byte header, the AES-CTR counter block, the Data
protobuf, and the firmware's own PKI test packet (test/test_crypto) decoded
and opened. Then the app on the console with the stand-in radio: the service
tunes LongFast on the region's slot, sends what the window typed as a
packet a Meshtastic node decodes, keeps what another node sent with its RSSI
and SNR, and drops another channel's packets, its own echo and a repeat."""

import json
import os
import shutil
import struct

import pytest

import crypto_vectors as V
from runtime import moybyte, scheduler as S
from runtime import moy_input as mi
from runtime import moy_lora as N
from runtime.host import lora_sim

from ws_helpers import build_ws, make_drv, select_on_home

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CART = os.path.join(ROOT, "tests", "fixtures", "apps", "moybyte.mesh.moy")
H = bytes.fromhex
DEFAULT_KEY = H("d4f1bb3a20290759f0bcffabcf4e6901")


class _Prefs(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def set(self, k, v):
        self[k] = v


def _codec():
    ns = {"__name__": "mesh_service", "moybyte": moybyte, "prefs": _Prefs()}
    with open(os.path.join(CART, "service.py")) as f:
        exec(compile(f.read(), "service.py", "exec"), ns)
    return ns


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


# -- the codec --------------------------------------------------------------------------


def test_the_long_fast_channel_is_meshtastic_s():
    c = _codec()
    key = c["channel_key"](b"\x01")
    assert key == DEFAULT_KEY
    assert c["channel_key"](b"\x02")[-1] == 0x02 and c["channel_key"](b"\x00") == b""
    assert c["channel_hash"]("LongFast", key) == 0x08
    assert c["frequency"]("EU_868") == 869525000
    assert c["frequency"]("US") == 906875000
    assert c["power"]("EU_868") == 22 and c["power"]("JP") == 13
    assert (c["BW"], c["SF"], c["CR"], c["SYNC"], c["PREAMBLE"]) == (250000, 11, 5, 0x2B, 16)


def test_the_header_reads_the_firmware_s_pki_packet():
    c = _codec()
    radio = H(V.MESH_PKI_RADIO)
    to, frm, pid, flags, chash, next_hop, relay = c["parse_header"](radio)
    assert (to, frm, pid) == (0x7A6D648C, 0x0929, 0x13B2D662)
    assert (flags & 7, flags >> 5, bool(flags & 8)) == (3, 3, True)
    assert (chash, next_hop, relay) == (0, 0, 0)
    assert c["header"](to, frm, pid, flags, chash) == radio[:16]
    assert c["parse_header"](radio[:15]) is None


def test_a_direct_message_opens_as_the_firmware_s_test_says():
    c = _codec()
    radio = H(V.MESH_PKI_RADIO)
    data = c["pki_open"](H(V.MESH_PKI_SECRET), H(V.MESH_PKI_PEER), radio)
    assert data.hex() == V.MESH_PKI_PLAIN
    assert c["data_decode"](data) == (1, b"test")
    changed = bytearray(radio)
    changed[20] ^= 1
    assert c["pki_open"](H(V.MESH_PKI_SECRET), H(V.MESH_PKI_PEER), bytes(changed)) is None


def test_a_channel_packet_is_what_an_independent_encoder_makes():
    c = _codec()
    key = c["channel_key"](b"\x01")
    mine = c["encode_text"]("hello mesh", 0x12345678, 0xCAFEF00D, key, 8)
    head = mine[:16]
    assert head == struct.pack("<IIIBBBB", 0xFFFFFFFF, 0x12345678, 0xCAFEF00D, 0x63, 8, 0, 0)
    block = struct.pack("<QII", 0xCAFEF00D, 0x12345678, 0)
    assert _ctr(key, block, mine[16:]) == b"\x08\x01\x12\x0ahello mesh"
    theirs = _node_packet(b"from a node", 0xA1B2C3D4, 77)
    h, portnum, payload = c["decode"](theirs, key, 8)
    assert (h[1], h[2], portnum, payload) == (0xA1B2C3D4, 77, 1, b"from a node")
    assert c["decode"](theirs, key, 9) is None, "another channel's hash"
    assert c["decode"](theirs[:10], key, 8) is None
    long = c["encode_text"]("\u00e9" * 150, 1, 2, key, 8)
    assert c["decode"](long, key, 8)[2] == ("\u00e9" * 100).encode(), "whole characters, 200 bytes"


def test_the_data_protobuf_reads_past_what_it_does_not_need():
    c = _codec()
    d = c["data_decode"]
    assert d(c["data_encode"](1, b"x" * 200)) == (1, b"x" * 200)
    # want_response, dest (fixed32), a nonce (fixed64), bitfield: read past
    full = b"\x08\x01\x12\x02hi\x18\x01\x25\x01\x02\x03\x04\x49" + bytes(8) + b"\x48\x01"
    assert d(full) == (1, b"hi")
    assert d(b"\x12\x02hi") is None, "no portnum"
    assert d(b"\x08\x01\x12\x09hi") is None, "a length past the end"
    assert d(b"\x08\x01\x0b") is None, "a wire type it does not know"


# -- the app on the console -------------------------------------------------------------


@pytest.fixture
def mesh(tmp_path):
    ws = build_ws(tmp_path)
    shutil.copytree(CART, os.path.join(ws.carts_root, "moybyte.mesh.moy"))
    ws.carts.rescan()
    lora_sim.install()
    # The radio posts on the kernel's input table, the one a board's console
    # binds; the host's console binds a table of its own.
    was = moybyte.WAKES.table
    moybyte.bind(mi.kernel())
    S.attach(ws)
    assert select_on_home(ws, "Mesh") is not None
    ws.open()
    pl = ws.run.player
    assert pl.cart_error is None, pl.cart_error
    drv = make_drv(ws)
    for _ in range(6):
        drv.frame(1 / 30)
        S.SCHED.poll()
    svc = S.SCHED.find("moybyte.mesh")
    assert svc is not None and svc.state != S.EXITED
    yield ws, pl, drv, svc
    S.SCHED.stop(svc.pid)
    S.SCHED.poll()
    while N.owner():
        moybyte.end(N.owner())
    lora_sim.remove()
    moybyte.bind(was)


def _frames(drv, n=4):
    for _ in range(n):
        drv.ws.mark_dirty()
        drv.frame(1 / 30)
        S.SCHED.poll()


def test_the_service_holds_the_radio_and_sends_what_the_window_typed(mesh):
    ws, pl, drv, svc = mesh
    assert N.owner() == svc.pid
    node = pl.ns["prefs"].get("node")
    assert 3 < node < 0xFFFFFFFF and pl.ns["node"] == "!%08x" % node
    pl.ns["send"]("hi from moybyte")
    _frames(drv)
    packet, freq, sf, bw, sync, iq, pre = lora_sim.sent()
    assert (freq, sf, bw, sync, iq, pre) == (869525000, 11, 250000, 0x2B, False, 16)
    to, frm, pid, flags, chash = struct.unpack("<IIIBB", packet[:14])
    assert (to, frm, flags, chash) == (0xFFFFFFFF, node, 0x63, 8)
    block = struct.pack("<QII", pid, frm, 0)
    assert _ctr(DEFAULT_KEY, block, packet[16:]) == b"\x08\x01\x12\x0fhi from moybyte"
    N.step(0)
    _frames(drv)
    assert pl.ns["rows"][0] == "me: hi from moybyte"


def test_the_service_keeps_another_node_s_text_with_its_signal(mesh):
    ws, pl, drv, svc = mesh
    p = _node_packet(b"hello T-Deck", 0xA1B2C3D4, 4242)
    assert lora_sim.air(p, 869525000, 11, rssi=-112, snr=-34, bw=250000, sync=0x2B)
    N.step(0)
    _frames(drv)
    assert pl.ns["rows"] == ["!a1b2c3d4: hello T-Deck  -112 dBm -8.5 dB"]
    log = pl.ns["prefs"].get("log")
    assert log == [{"from": "!a1b2c3d4", "text": "hello T-Deck", "rssi": -112, "snr": -8.5}]
    # a repeat (a relay's copy), another channel and its own echo are dropped
    node = pl.ns["prefs"].get("node")
    for again in (p, _node_packet(b"other", 0xA1B2C3D4, 4243, chash=9),
                  _node_packet(b"echo", node, 99)):
        assert lora_sim.air(again, 869525000, 11, bw=250000, sync=0x2B)
        N.step(0)
        _frames(drv)
    assert len(pl.ns["prefs"].get("log")) == 1


def test_a_new_region_retunes_the_radio(mesh):
    ws, pl, drv, svc = mesh
    pl.ns["prefs"].set("region", "US")
    moybyte.send("moybyte.mesh", "retune")
    _frames(drv)
    assert not lora_sim.air(_node_packet(b"eu", 1234, 1), 869525000, 11)
    assert lora_sim.air(_node_packet(b"us", 1234, 2), 906875000, 11)
    N.step(0)
    _frames(drv)
    assert pl.ns["rows"][0].startswith("!000004d2: us")


def test_the_manifest_asks_for_the_radio_and_names_its_service():
    with open(os.path.join(CART, "manifest.json")) as f:
        man = json.load(f)
    assert man["id"] == "moybyte.mesh" and man["moybyte"]["type"] == "app"
    assert man["moybyte"]["entries"] == {"service": "service.py"}
    assert set(man["moybyte"]["required"]) == {"lora", "prefs"}
