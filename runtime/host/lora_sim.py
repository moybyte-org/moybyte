"""The host's stand-in LoRa radio and the air around it (docs/os_2026-11/
hardware.md section 1): the C driver's stand-in radio (`moy_lora.sim`), what
it transmits (`sent`), what the air delivers to it (`air`), and a LoRaWAN
network server, the server side of the MAC written apart from the C (over
the `cryptography` package), so a join and an uplink cross between two
implementations.

  install() / remove()     the console has the radio, or not
  sent()                   the next transmission: (bytes, freq, sf, bw,
                           sync, iq_inverted, preamble) or None
  air(data, freq, sf, rssi=-60, snr=32, bw=None, sync=None)
                           a packet on the air (snr in quarter dB): whether
                           the radio heard it, tuned there and receiving (at
                           `bw` and `sync` where given, else whatever it is
                           tuned to)
  Network(dev_eui, join_eui, app_key, ...)
                           a network server for one device: `answer(frame)`
                           the reply to an uplink or a join request (None for
                           none), `uplinks` what it decrypted, `down(port,
                           data)` a downlink for the next uplink's RX1"""

import os
import struct

import moy_lora as _N

EU868_RX2 = 869525000


def install():
    _N.sim(True)


def remove():
    _N.reset()


def sent():
    return _N.sim_sent()


def air(data, freq, sf, rssi=-60, snr=32, bw=None, sync=None):
    return _N.sim_air(bytes(data), freq, sf, bw or 0, -1 if sync is None else sync, rssi, snr)


def _aes(key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.ECB())


def _cmac(key, msg):
    from cryptography.hazmat.primitives import cmac
    from cryptography.hazmat.primitives.ciphers import algorithms
    c = cmac.CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def _enc(key, block):
    e = _aes(key).encryptor()
    return e.update(block) + e.finalize()


def _dec(key, block):
    d = _aes(key).decryptor()
    return d.update(block) + d.finalize()


def _crypt(key, down, addr, fcnt, data):
    out = bytearray()
    for i in range(0, len(data), 16):
        a = bytes([1, 0, 0, 0, 0, down]) + struct.pack("<II", addr, fcnt) + bytes([0, i // 16 + 1])
        s = _enc(key, a)
        out += bytes(x ^ y for x, y in zip(data[i:i + 16], s))
    return bytes(out)


def _mic(key, down, addr, fcnt, msg):
    b0 = bytes([0x49, 0, 0, 0, 0, down]) + struct.pack("<II", addr, fcnt) + bytes([0, len(msg)])
    return _cmac(key, b0 + msg)[:4]


class Network:
    """A LoRaWAN 1.0 network server for one OTAA device, EU868."""

    def __init__(self, dev_eui, join_eui, app_key, net_id=0x000013, dev_addr=0x260B1C2D,
                 rx1_dr_offset=0, rx2_dr=3, cflist=None):
        self.dev_eui, self.join_eui, self.app_key = bytes(dev_eui), bytes(join_eui), bytes(app_key)
        self.net_id, self.dev_addr = net_id, dev_addr
        self.rx1_dr_offset, self.rx2_dr, self.cflist = rx1_dr_offset, rx2_dr, cflist
        self.nwk_skey = self.app_skey = None
        self.uplinks = []           # (fcnt, port, payload, confirmed)
        self.joins = 0
        self.fcnt_down = 0
        self._down = None
        self.bad = []

    def down(self, port, data):
        self._down = (port, bytes(data))

    def answer(self, frame):
        frame = bytes(frame)
        if frame[0] == 0x00:
            return self._join(frame)
        if frame[0] in (0x40, 0x80):
            return self._uplink(frame)
        self.bad.append(("mhdr", frame))
        return None

    def _join(self, f):
        if len(f) != 23 or _cmac(self.app_key, f[:19])[:4] != f[19:]:
            self.bad.append(("join mic", f))
            return None
        if f[1:9][::-1] != self.join_eui or f[9:17][::-1] != self.dev_eui:
            self.bad.append(("join eui", f))
            return None
        dev_nonce = f[17:19]
        app_nonce = os.urandom(3)
        body = app_nonce + struct.pack("<I", self.net_id)[:3] + struct.pack("<I", self.dev_addr)
        body += bytes([(self.rx1_dr_offset << 4) | self.rx2_dr, 1])
        if self.cflist:
            cf = b"".join(struct.pack("<I", hz // 100)[:3] for hz in self.cflist)
            body += cf.ljust(15, b"\0") + b"\0"
        mic = _cmac(self.app_key, b"\x20" + body)[:4]
        plain = body + mic
        enc = b"".join(_dec(self.app_key, plain[i:i + 16]) for i in range(0, len(plain), 16))
        keyb = app_nonce + struct.pack("<I", self.net_id)[:3] + dev_nonce
        self.nwk_skey = _enc(self.app_key, (b"\x01" + keyb).ljust(16, b"\0"))
        self.app_skey = _enc(self.app_key, (b"\x02" + keyb).ljust(16, b"\0"))
        self.joins += 1
        self.fcnt_down = 0
        return b"\x20" + enc

    def _uplink(self, f):
        addr = struct.unpack("<I", f[1:5])[0]
        if self.nwk_skey is None or addr != self.dev_addr:
            self.bad.append(("addr", f))
            return None
        fctrl = f[5]
        fcnt = struct.unpack("<H", f[6:8])[0]
        if _mic(self.nwk_skey, 0, addr, fcnt, f[:-4]) != f[-4:]:
            self.bad.append(("mic", f))
            return None
        at = 8 + (fctrl & 0x0F)
        port = f[at]
        payload = _crypt(self.app_skey if port else self.nwk_skey, 0, addr, fcnt, f[at + 1:-4])
        confirmed = f[0] == 0x80
        self.uplinks.append((fcnt, port, payload, confirmed))
        if self._down is None and not confirmed:
            return None
        msg = bytearray([0x60]) + struct.pack("<I", addr)
        msg += bytes([0x20 if confirmed else 0x00]) + struct.pack("<H", self.fcnt_down & 0xFFFF)
        if self._down is not None:
            dport, data = self._down
            self._down = None
            msg += bytes([dport]) + _crypt(self.app_skey, 1, addr, self.fcnt_down, data)
        msg += _mic(self.nwk_skey, 1, addr, self.fcnt_down, bytes(msg))
        self.fcnt_down += 1
        return bytes(msg)
