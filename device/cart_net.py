"""The network Get Carts fetches through on a board (#124):
`runtime/cart_index.py`'s transport, over the OTA's streaming HTTP(S) client.

Two verbs, the shape the host's urllib transport has too (runtime/host_app.py):

    online()   dial the saved network if the link is down and WAIT for it --
               moy_ota.wait_online, whose wait is measured: a saved network on
               the P4 came up 1.5s after connect() had given up
    open(url)  GET with redirects followed (a GitHub release asset is a 302 to
               its CDN); the body stays in the socket, read with `readinto`

The radio itself is not this module's: the store app takes the console's lease
(`ws.wifi_hold("carts")`) before it asks for anything here and lets go after.

TLS is MicroPython's `ssl.wrap_socket`, which verifies no certificate -- the
reason cart_index trusts nothing for having arrived over TLS: every file is
checked against the index's sha256, and what it installs runs sandboxed or
is a signed module.
"""

import moy_ota

AGENT = "moybyte-carts"


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
        sock, code, clen, rest = moy_ota.http_open(url, agent=AGENT, log=_log)
        return _Response(sock, code, clen, rest)


def make_cart_net(wifi, autoconnect=None):
    return CartNet(wifi, autoconnect)
