"""The links' provider: everything the console reaches the outside through,
constructed in one place for every console board (docs/kernel_survival_2026-10.md
section 2 item 1).

`build_desktop` (device/desktop_spine.py) calls `wire_links` once, after the
shared core wiring; the P4 desk asks `c6_updater_class` for its companion
radio's updater. What it builds, in this order, each a missing Settings row or
a notice rather than a boot failure when it cannot be built:

  ws.link / ws.net     the ESP-NOW link, INERT until a multiplayer cart arms it
  ws.updater           the OTA updater over the board's store gate
  ws.cart_net          Get Carts' network (`CartNet`, over the kernel's client)
  ws.c6_updater        the companion radio's updater, where there is one
  ws.reboot_hook       the system menu's real reset
  ws.webhost           the baked web console, constructed and not started

The WiFi service itself is `make_wifi`, which the core wiring takes.
"""

from device_wifi import autoconnect_wifi
import device_wifi


def make_wifi(store, root):
    """The console's WiFi service over the store (device/device_wifi.py)."""
    return device_wifi.make_wifi(store, root)


def c6_updater_class():
    """The companion C6's updater class, or None on a build without a C6."""
    try:
        import moy_c6              # the radio this updater talks to
        from moy_ota import C6Updater
    except ImportError:            # no C6 on this board: no Settings row
        return None
    del moy_c6
    return C6Updater


# Internal DMA-capable RAM (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA) a download
# needs. The radio's driver takes RADIO_SRAM of it the first time it comes up
# (its receive buffers, kept after the lease powers it down), and a TLS
# download then takes TLS_SRAM_MIN more a record at a time: with mbedtls's
# state in PSRAM (each console's sdkconfig.board), what is left internal is the
# hardware AES/SHA's DMA bounce, 4.7 KB at its peak on the Guition S3
# (2026-10-07, a 786 KB GitHub download). The driver measured 41 KB on the
# T-Deck (2026-10-02). Short of the first, the radio does not come up
# ("Expected to init 16 rx buffer, actual is 0"); short of the second, a
# download stops partway. Only a restart gives the memory back.
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
    """`ws.cart_net` on a board: Get Carts' transport (runtime/cart_index.py),
    the shape the host's urllib one has (runtime/host_app.py).

        online()   dial the saved network if the link is down and WAIT for it
                   (moy_ota.wait_online)
        open(url)  GET with redirects followed (a GitHub release asset is a
                   302 to its CDN)

    and `out_of_memory()`, which cart_index asks after a fetch fails, so a
    console whose internal RAM is spent says so. TLS verifies no certificate:
    cart_index checks every file against its index's sha256. The radio is the
    app's lease (`ws.wifi_hold("carts")`), never this."""

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
        import moy_ota
        return moy_ota.wait_online(self._up, None if self.autoconnect is None
                                   else self._dial)

    def _dial(self):
        return self.autoconnect(self.wifi)

    def open(self, url):
        import moy_net
        h, status, length = moy_net.http_open(url, CARTS_AGENT)
        return _Response(h, status, length)

    def out_of_memory(self):
        """True when internal DMA-capable RAM is short of what a download
        needs: TLS_SRAM_MIN, and RADIO_SRAM besides while the radio's driver
        has not come up yet this boot."""
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


def wire_links(ws, link_id, update_dir, carts_root, with_sd, c6_updater, log,
               _census):
    """Attach the links to `ws`. `update_dir` is where the board's store put
    the updater's directory, `with_sd` the store gate the web console's writes
    go through (None where there is none), `c6_updater` the companion radio's
    updater class or None, `log(tag, msg)` the boot's sink and `_census` its
    stage meter."""
    # THE RADIO LINK (#7/#65): the console's one ESP-NOW owner. Built here,
    # INERT until a cart with the "multiplayer" permission runs (ws.link_arm
    # starts the radio; pm=PM_NONE costs power and a console on its shelf has
    # nobody to talk to).
    try:
        from moy_espnow import make_link
        ws.link = make_link(board=link_id,
                            name=ws.system.get("name", link_id))
        ws.net = ws.link.net
    except Exception as exc:  # noqa: BLE001 -- no radio must never cost a console
        log("boot", "link unavailable: %s" % exc)
        ws.link = None
    _census("link")
    # OTA (#53): update_dir is where the board's store said (a copied image,
    # the pending marker), and every write -- the slot's included -- goes
    # through the console's store gate: the SD bracket where the card shares
    # the panel's bus, a plain call-through elsewhere.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(ws._with_sd, update_dir=update_dir)
    except Exception as exc:  # noqa: BLE001
        log("boot", "OTA updater unavailable: %s" % exc)
    if ws.updater is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:  # noqa: BLE001
            log("boot", "OTA wifi wiring failed: %s" % exc)
    # The network Get Carts fetches indexes and carts through (#124), over the
    # kernel's client; the app takes its own radio lease.
    ws.cart_net = CartNet(ws.wifi, autoconnect_wifi)
    if c6_updater is not None and ws.updater is not None:
        # The companion radio's own updater (#7/#58): Settings -> UPGRADE C6
        # RADIO. Failure is a missing Settings row, never a boot failure.
        try:
            ws.c6_updater = c6_updater(ws.updater)
        except Exception as exc:  # noqa: BLE001
            log("boot", "C6 updater unavailable: %s" % exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        log("boot", "reboot hook unavailable: %s" % exc)
    _census("ota")
    # WEB CONSOLE (moycore plan 3.4 pull half): the wasm console baked into
    # the image, served from the board. Constructed, NOT started -- __init__
    # binds no socket, so injecting it only makes the Settings row appear.
    try:
        from moy_webhost import make_webhost
        ws.webhost = make_webhost(ws, carts_root,
                                  autoconnect=autoconnect_wifi,
                                  with_sd=with_sd)
    except Exception as exc:  # noqa: BLE001
        log("boot", "web console unavailable: %s" % exc)
    _census("webhost")
