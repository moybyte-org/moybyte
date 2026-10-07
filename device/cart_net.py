"""The network Get Carts fetches through on a board (#124):
`runtime/cart_index.py`'s transport, over the streaming HTTP(S) client
(device/moy_http.py) the OTA updater fetches through.

Two verbs, the shape the host's urllib transport has too (runtime/host_app.py):

    online()   dial the saved network if the link is down and WAIT for it --
               moy_ota.wait_online, whose wait is measured: a saved network on
               the P4 came up 1.5s after connect() had given up
    open(url)  GET with redirects followed (a GitHub release asset is a 302 to
               its CDN); the body stays in the socket, read with `readinto`

and one the host's has no use for: `out_of_memory()`, which cart_index asks
after a fetch fails, so a console whose internal RAM is spent says so.

The radio itself is not this module's: the store app takes the console's lease
(`ws.wifi_hold("carts")`) before it asks for anything here and lets go after.

TLS is MicroPython's `ssl.wrap_socket`, which verifies no certificate -- the
reason cart_index trusts nothing for having arrived over TLS: every file is
checked against the index's sha256, and what it installs runs sandboxed or
is a signed module.
"""

import moy_http
import moy_ota

AGENT = "moybyte-carts"

# Internal DMA-capable RAM (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA) a download
# needs. The radio's driver takes RADIO_SRAM of it the first time it comes up
# (its receive buffers, kept after the lease powers it down), and a TLS
# download then takes TLS_SRAM_MIN more a record at a time -- the hardware
# AES's DMA descriptors and bounce buffers among it. Measured on the T-Deck
# 2026-10-02: the driver 41 KB, a download about 13 KB. Short of the first,
# the radio does not come up ("Expected to init 16 rx buffer, actual is 0");
# short of the second, a download stops partway with a bare EPERM, the
# hardware AES failing to allocate. Only a restart gives the memory back.
TLS_SRAM_MIN = 16384
RADIO_SRAM = 41 * 1024
_INTERNAL_DMA = 0x808


def _log(*a):
    try:
        print("Moybyte carts:", *a)
    except Exception:  # noqa: BLE001
        pass


class _Response:
    """One GET's body: `status`, `length` (None when the server sent none)
    and the socket's `readinto`. Bytes the header read already pulled in
    (`rest`) come first."""

    def __init__(self, sock, status, length, rest):
        self.sock = sock
        self.status = status
        self.length = length or None
        self.rest = rest or b""

    def readinto(self, buf):
        if self.rest:
            n = min(len(buf), len(self.rest))
            buf[:n] = self.rest[:n]
            self.rest = self.rest[n:]
            return n
        return self.sock.readinto(buf)

    def close(self):
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


class CartNet:
    """`ws.cart_net` on a board."""

    def __init__(self, wifi, autoconnect=None):
        self.wifi = wifi
        self.autoconnect = autoconnect

    def _up(self):
        try:
            return bool(self.wifi.status()[0])
        except Exception:  # noqa: BLE001 -- a radio that cannot say is down
            return False

    def online(self):
        if self.wifi is None:
            return False
        return moy_ota.wait_online(self._up, None if self.autoconnect is None
                                   else self._dial)

    def _dial(self):
        return self.autoconnect(self.wifi)

    def open(self, url):
        sock, code, clen, rest = moy_http.http_open(url, agent=AGENT, log=_log)
        return _Response(sock, code, clen, rest)

    def out_of_memory(self):
        """True when this console's internal DMA-capable RAM is short of what
        a download needs: TLS_SRAM_MIN, and RADIO_SRAM besides while the
        radio's driver has not come up yet this boot. cart_index and the app
        ask after something fails, so the kid reads that a restart is the
        way on rather than that the download stopped or WiFi is off."""
        try:
            import esp32
            free = sum(h[1] for h in esp32.idf_heap_info(_INTERNAL_DMA))
        except Exception:  # noqa: BLE001 -- a console that cannot say is not out
            return False
        need = TLS_SRAM_MIN
        if not getattr(self.wifi, "driver_up", True):
            need += RADIO_SRAM
        _log("internal RAM free %d after the failure, a download needs %d"
             % (free, need))
        return free < need


def make_cart_net(wifi, autoconnect=None):
    return CartNet(wifi, autoconnect)
