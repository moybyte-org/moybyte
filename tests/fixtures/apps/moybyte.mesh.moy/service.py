"""Mesh's service: Meshtastic text on the LoRa radio, in the background.

It holds the radio (`moybyte.lora`) on Meshtastic's default channel, the
LongFast preset in the region the app's prefs name (`region`, EU_868 until
one is chosen), listens, and keeps the last KEEP text messages it heard or
sent in the app's prefs (`log`), telling the app `heard` after each and at
its start, when its node number (`node`) is there to read. The app
sends `send(text)`, which goes out on the default channel, and `retune()`
after it changes the region. It answers no one and relays nothing: a
Meshtastic CLIENT_MUTE that hears the channel and talks on it.

THE PROTOCOL, from the Meshtastic firmware (src/mesh/RadioInterface.*,
Channels.cpp, CryptoEngine.cpp) and its protobufs (mesh.proto, portnums.proto):

  the air      LongFast: SF11, 250 kHz, 4/5, sync word 0x2b, preamble 16,
               explicit header, CRC on. The frequency is the region's slot
               for the channel's name: the djb2 hash of "LongFast" modulo
               the slots the region holds (its span over the bandwidth),
               the slot's centre (EU_868: 869.525 MHz; US: 906.875 MHz).
  a packet     a 16-byte header, little-endian: to, from, id (u32 each),
               flags (hop limit in bits 0-2, want-ack 0x08, via MQTT 0x10,
               hop start in bits 5-7), the channel hash, the next hop's and
               the relay's last byte; then the payload.
  a channel    its key the PSK, a one-byte PSK an index into the well-known
               key ("AQ==", 1, is the key itself; n bumps its last byte by
               n - 1); its hash the XOR of the name's bytes and the key's.
  encryption   AES-CTR under the channel's key (AES-128 or -256), the
               counter block the packet id as a little-endian u64, the
               sender as a u32, then a zero 32-bit counter.
  a direct message to a node's key (PKI, channel hash 0): AES-CCM under
               SHA-256 of the X25519 secret, a 13-byte nonce (the packet
               id, the 4-byte extra nonce the payload ends with, the
               sender), the ciphertext, an 8-byte tag, the extra nonce.
  the payload  the Data protobuf: portnum (field 1, varint; 1 is
               TEXT_MESSAGE_APP) and payload (field 2, bytes), its other
               fields read past.
"""

import struct

BROADCAST = 0xFFFFFFFF
TEXT = 1                                # portnums.proto: TEXT_MESSAGE_APP
KEEP = 30                               # messages the log keeps
TEXT_MAX = 200                          # bytes a message carries
HOPS = 3

# LongFast: bandwidth (Hz), spreading factor, coding rate (4/n).
PRESET = "LongFast"
BW, SF, CR = 250000, 11, 5
SYNC, PREAMBLE = 0x2B, 16
HEADER = 16

# The well-known key one-byte PSKs index (Channels.h, defaultpsk).
DEFAULT_KEY = (b"\xd4\xf1\xbb\x3a\x20\x29\x07\x59"
               b"\xf0\xbc\xff\xab\xcf\x4e\x69\x01")

# Meshtastic's regions an SX1262 reaches (RadioInterface.cpp, RDEF): the
# band's start and end (Hz) and its power limit (dBm). The radio's own
# ceiling is 22 dBm.
REGIONS = {
    "EU_868": (869400000, 869650000, 27),
    "EU_433": (433000000, 434000000, 10),
    "US": (902000000, 928000000, 30),
    "ANZ": (915000000, 928000000, 30),
    "ANZ_433": (433050000, 434790000, 14),
    "CN": (470000000, 510000000, 19),
    "JP": (920500000, 923500000, 13),
    "RU": (868700000, 869200000, 20),
    "KR": (920000000, 923000000, 23),
    "TW": (920000000, 925000000, 27),
    "IN": (865000000, 867000000, 30),
    "NZ_865": (864000000, 868000000, 36),
    "TH": (920000000, 925000000, 27),
    "UA_433": (433000000, 434700000, 10),
    "UA_868": (868000000, 868600000, 14),
    "MY_433": (433000000, 435000000, 20),
    "MY_919": (919000000, 924000000, 27),
    "SG_923": (917000000, 925000000, 20),
    "PH_433": (433000000, 434700000, 10),
    "PH_868": (868000000, 869400000, 14),
    "PH_915": (915000000, 918000000, 24),
    "KZ_433": (433075000, 434775000, 10),
    "KZ_863": (863000000, 868000000, 30),
    "NP_865": (865000000, 868000000, 30),
    "BR_902": (902000000, 907500000, 30),
}
REGION = "EU_868"
POWER_MAX = 22


# -- the codec ------------------------------------------------------------------------


def djb2(text):
    h = 5381
    for c in text.encode():
        h = (h * 33 + c) & 0xFFFFFFFF
    return h


def frequency(region, name=PRESET, bw=BW):
    """The centre (Hz) of the slot `name` hashes to in `region`."""
    start, end, _limit = REGIONS[region]
    slots = (end - start) // bw
    return start + bw // 2 + (djb2(name) % slots) * bw


def power(region):
    return min(REGIONS[region][2], POWER_MAX)


def channel_key(psk):
    """A channel's AES key from its PSK: a one-byte PSK indexes the
    well-known key (0: none), a short one is padded to 16 bytes."""
    psk = bytes(psk)
    if len(psk) == 1:
        if psk[0] == 0:
            return b""
        return DEFAULT_KEY[:15] + bytes([(DEFAULT_KEY[15] + psk[0] - 1) & 0xFF])
    if 0 < len(psk) < 16:
        return psk + bytes(16 - len(psk))
    if 16 < len(psk) < 32:
        return psk + bytes(32 - len(psk))
    return psk


def _xor(data):
    h = 0
    for b in data:
        h ^= b
    return h


def channel_hash(name, key):
    return _xor(name.encode()) ^ _xor(key)


def header(to, frm, pid, flags, channel, next_hop=0, relay=0):
    return struct.pack("<IIIBBBB", to, frm, pid, flags, channel, next_hop, relay)


def parse_header(packet):
    """(to, from, id, flags, channel, next_hop, relay), None for a packet
    shorter than the header."""
    if len(packet) < HEADER:
        return None
    return struct.unpack("<IIIBBBB", bytes(packet[:HEADER]))


def flags(hop_limit=HOPS, hop_start=HOPS, want_ack=False):
    return (hop_limit & 7) | (0x08 if want_ack else 0) | ((hop_start & 7) << 5)


def counter_block(pid, frm):
    return struct.pack("<QII", pid, frm, 0)


def crypt(key, pid, frm, data):
    """The channel's AES-CTR, both ways; the bytes as they are for no key."""
    if not key:
        return bytes(data)
    return moybyte.aes_ctr(key, counter_block(pid, frm) + bytes(data))


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(b, at):
    n = shift = 0
    while at < len(b):
        c = b[at]
        at += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, at
        shift += 7
        if shift > 63:
            break
    return None, at


def data_encode(portnum, payload):
    payload = bytes(payload)
    return b"\x08" + _varint(portnum) + b"\x12" + _varint(len(payload)) + payload


def data_decode(b):
    """(portnum, payload) of a Data protobuf, None where it does not parse."""
    b = bytes(b)
    portnum, payload, at = None, b"", 0
    while at < len(b):
        tag, at = _read_varint(b, at)
        if tag is None:
            return None
        field, kind = tag >> 3, tag & 7
        if kind == 0:
            v, at = _read_varint(b, at)
            if v is None:
                return None
            if field == 1:
                portnum = v
        elif kind == 2:
            n, at = _read_varint(b, at)
            if n is None or at + n > len(b):
                return None
            if field == 2:
                payload = b[at:at + n]
            at += n
        elif kind == 5:
            at += 4
        elif kind == 1:
            at += 8
        else:
            return None
    if portnum is None or at != len(b):
        return None
    return portnum, payload


def encode_text(text, frm, pid, key, chash, to=BROADCAST):
    """A text message on a channel, as the radio sends it."""
    words = text.encode()
    while len(words) > TEXT_MAX:            # whole characters only
        text = text[:-1]
        words = text.encode()
    body = data_encode(TEXT, words)
    return header(to, frm, pid, flags(), chash) + crypt(key, pid, frm, body)


def decode(packet, key, chash):
    """A channel packet's (header, portnum, payload), None where it is
    another channel's, too short, or does not decode."""
    h = parse_header(packet)
    if h is None or h[4] != chash:
        return None
    got = data_decode(crypt(key, h[2], h[1], packet[HEADER:]))
    return None if got is None else (h, got[0], got[1])


def pki_open(secret, peer, packet):
    """A direct message's Data protobuf to the node of X25519 `secret` from
    the node of public key `peer`, None where it does not open."""
    h = parse_header(packet)
    body = bytes(packet[HEADER:])
    if h is None or len(body) < 12:
        return None
    key = moybyte.sha256(moybyte.x25519(secret, peer))
    nonce = struct.pack("<I", h[2]) + body[-4:] + struct.pack("<I", h[1]) + b"\x00"
    return moybyte.aes_ccm_open(key, nonce + body[:-4], 8)


# -- the service ----------------------------------------------------------------------

_S = {"node": 0, "key": b"", "hash": 0, "seen": [], "buf": None, "meta": None}
SEEN = 32


def node_name(n):
    return "!%08x" % n


def _node():
    n = prefs.get("node")
    if not isinstance(n, int) or n <= 3 or n >= BROADCAST:
        n = 0
        while n <= 3 or n >= BROADCAST:
            n = int.from_bytes(moybyte.random(4), "little")
        prefs.set("node", n)
    return n


def _region():
    r = prefs.get("region")
    return r if r in REGIONS else REGION


def _tune():
    r = _region()
    moybyte.lora.config(freq=frequency(r), sf=SF, bw=BW, cr=CR, power=power(r),
                        sync=SYNC, preamble=PREAMBLE)
    moybyte.lora.listen()


def _log(rec):
    log = prefs.get("log") or []
    log.append(rec)
    prefs.set("log", log[-KEEP:])
    moybyte.send("moybyte.mesh", "heard")


def _fresh(frm, pid):
    seen = _S["seen"]
    if (frm, pid) in seen:
        return False
    seen.append((frm, pid))
    if len(seen) > SEEN:
        seen.pop(0)
    return True


def _heard(packet, rssi, snr):
    got = decode(packet, _S["key"], _S["hash"])
    if got is None:
        return
    h, portnum, payload = got
    if h[1] == _S["node"] or not _fresh(h[1], h[2]) or portnum != TEXT:
        return
    try:
        text = payload.decode()
    except UnicodeError:
        return
    _log({"from": node_name(h[1]), "text": text, "rssi": rssi, "snr": snr})


def _radio(event, n, rssi, snr):
    if event != moybyte.lora.EV_RX:
        return
    buf, meta = _S["buf"], _S["meta"]
    while True:
        got = moybyte.lora.recv(buf, meta)
        if not got:
            return
        _heard(bytes(buf[:got]), meta[0], meta[1] / 4)


def send(who, text):
    text = str(text)
    if not text:
        return
    pid = 0
    while not pid:
        pid = int.from_bytes(moybyte.random(4), "little")
    packet = encode_text(text, _S["node"], pid, _S["key"], _S["hash"])
    _fresh(_S["node"], pid)
    try:
        moybyte.lora.send(packet)
    except OSError:
        _log({"from": "", "text": text, "failed": True})
        return
    _log({"from": "", "text": text})


def retune(who):
    _tune()


def moy_start():
    from array import array
    _S["buf"], _S["meta"] = bytearray(256), array("i", [0, 0, 0])
    _S["node"] = _node()
    _S["key"] = channel_key(b"\x01")
    _S["hash"] = channel_hash(PRESET, _S["key"])
    _tune()
    moybyte.lora.on(_radio)
    moybyte.on("send", send)
    moybyte.on("retune", retune)
    moybyte.send("moybyte.mesh", "heard")


def moy_stop():
    moybyte.lora.close()
