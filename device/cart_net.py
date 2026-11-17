"""Get Carts' transport on a board: the kernel's HTTP client (moy_net.http_*)
behind the shape runtime/carts/cart_index.py reads, the host's twin being
runtime/host/host_app.py's. The consoles hand it to `ws.cart_net`
(device/wire_links.py); the Zero installs the browser console add-on through it
(firmware/seeed_xiao_esp32s3_zero/modules/zero_host.py)."""

# Internal DMA-capable RAM (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA) a download
# needs. The radio's driver takes RADIO_SRAM of it while it is up (its
# receive buffers; on an S3 the station's stop gives them back), and a TLS
# download then takes TLS_SRAM_MIN more a record at a time: with mbedtls's
# state in PSRAM (each console's sdkconfig.board), what is left internal is the
# hardware AES/SHA's DMA bounce, 4.7 KB at its peak on the Guition S3
# (a 786 KB GitHub download). The driver measured 41 KB on the
# T-Deck. Short of the first, the radio does not come up
# ("Expected to init 16 rx buffer, actual is 0"); short of the second, a
# download stops partway.
TLS_SRAM_MIN = 8192
RADIO_SRAM = 41 * 1024
_INTERNAL_DMA = 0x808
CARTS_AGENT = "moybyte-carts"


class _Response:
    """One GET's body over the kernel's client (moy_net.http_*): `status`,
    `length` (None when the server sent none), `readinto` and `close`."""

    def __init__(self, handle, status, length):
        self.handle = handle
        self.status = status
        self.length = length or None

    def readinto(self, buf):
        if self.handle is None:
            return 0
        import moy_net
        return moy_net.http_readinto(self.handle, buf)

    def close(self):
        h, self.handle = self.handle, None
        if h is not None:
            import moy_net
            moy_net.http_close(h)


class CartNet:
    """`ws.cart_net` on a board: Get Carts' transport (runtime/carts/cart_index.py),
    the shape the host's urllib one has (runtime/host/host_app.py).

        dial()     dial it and return at once: a request on a worker's task
                   waits for the link there (moy_net_dialing)
        ready()    the link polled (moy_ota.poll_online): True up, None
                   while the dial runs (the first call dials), False once
                   its budget passed
        open(url)  GET with redirects followed (a GitHub release asset is a
                   302 to its CDN)

    and `out_of_memory()`, which cart_index asks after a fetch fails, so a
    console whose internal RAM is spent says so. TLS verifies the server's
    certificate (native/moy_net/moy_ota.h), and cart_index checks every file
    against its index's sha256 besides. The radio is the
    app's lease (`ws.spine.wifi_hold("carts")`), never this."""

    def __init__(self, wifi, autoconnect=None):
        self.wifi = wifi
        self.autoconnect = autoconnect
        self._dialled = None        # ticks_ms of the dial ready() waits on

    def _up(self):
        try:
            return bool(self.wifi.status()[0])
        except Exception:  # noqa: BLE001 -- a radio that cannot say is down
            return False

    def _dial(self):
        return self.autoconnect(self.wifi)

    def dial(self):
        if self.wifi is None or self.autoconnect is None or self._up():
            return
        try:
            self._dial()
        except Exception:  # noqa: BLE001 -- the request reports what failed
            pass

    def ready(self):
        if self.wifi is None:
            return False
        import moy_ota
        up, self._dialled = moy_ota.poll_online(
            self._up, None if self.autoconnect is None else self._dial, self._dialled)
        return up

    def open(self, url):
        import moy_net
        h, status, length = moy_net.http_open(url, CARTS_AGENT)
        return _Response(h, status, length)

    def out_of_memory(self):
        """True when internal DMA-capable RAM is short of what a download
        needs: TLS_SRAM_MIN, and RADIO_SRAM besides while the radio's driver
        is not up."""
        try:
            import esp32
            free = sum(h[1] for h in esp32.idf_heap_info(_INTERNAL_DMA))
        except Exception:  # noqa: BLE001 -- a console that cannot say is not out
            return False
        need = TLS_SRAM_MIN
        if not getattr(self.wifi, "driver_up", True):
            need += RADIO_SRAM
        print("Moybyte carts: internal RAM free %d after the failure, a download "
              "needs %d" % (free, need))
        return free < need
