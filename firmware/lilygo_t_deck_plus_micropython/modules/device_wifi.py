"""The device WiFi service (#38), extracted from moy_runtime.py.

DeviceWifi is the injected `wifi` backend the shared Workstation exposes to a cart
whose manifest opts into the "network" permission (host == device: it mirrors the
host make_wifi). It is a SYSTEM service -- the connection persists when the manager
cart exits, so the web editor (#22) and the AI helper (#8) can bind to / make
requests over the IP it reports (status() -> ip). Credentials persist to the
moy_carts wifi.json store and are used by autoconnect_wifi() at boot.

Radio coexistence caveat: WiFi shares the ESP32-S3 radio with BLE (#26) and is a
different mode from LoRa / ESP-NOW (#7) -- only one radio user can be active at a
time. WiFi STA and the display SPI bus are SEPARATE peripherals (unlike SD), so
there is no SPI-host fight, but ALL of this is UNVERIFIED on hardware here. The
whole class is wrapped in try/except so a board/build without WiFi degrades to a
never-connected service instead of crashing the console.

Device-only module (authored in modules/, not staged from runtime/). Imports only
`time` + the leaf device_util._diag_note, so it sits low in the device import DAG
with no moy_runtime cycle.
"""
import time

from device_util import _diag_note


class DeviceWifi:
    """network.WLAN(STA_IF) wrapper. `store`/`root` are the moy_carts credential
    store + carts dir; connect()/forget() persist there so the next boot can
    autoconnect. UNVERIFIED on hardware -- treat the WLAN calls as a sketch."""

    def __init__(self, store=None, root=None):
        self._store = store
        self._root = root
        self._ssid = None        # last ssid we associated with (status fallback)
        # LAZY: bringing the WiFi stack up reserves a large chunk of INTERNAL RAM that
        # the LCD DMA flush (lcd_panel_io_tx_color) also needs. Doing it at boot starved
        # the panel flush -> OSError 257 (ESP_ERR_NO_MEM) and froze the desktop. So spin
        # the radio up only on first real use (scan/connect), never at boot. Whether WiFi
        # and the display can coexist at all on this RAM budget is an open #38 question.
        self.wlan = None

    def _ensure_wlan(self):
        """Bring the radio up on demand (never at boot -- see __init__)."""
        if self.wlan is not None:
            return self.wlan
        try:
            import network
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
        except Exception as exc:  # noqa: BLE001 -- no radio / no network module -> degrade
            _diag_note("wifi", "WLAN unavailable, offline: %s" % (exc,))
            self.wlan = None
        return self.wlan

    # -- the injected `wifi` API surface (host == device) ----------------
    def scan(self):
        """Nearby networks as (ssid, signal%, locked?) -- NEEDS ON-DEVICE VERIFICATION.
        WLAN.scan() returns (ssid, bssid, channel, RSSI, security, hidden) tuples;
        map RSSI (~-100..-30 dBm) to a 0..100 bar and security!=0 to locked."""
        if self._ensure_wlan() is None:
            return []
        try:
            out = []
            for net in self.wlan.scan():
                ssid = net[0].decode() if isinstance(net[0], (bytes, bytearray)) else str(net[0])
                rssi = net[3] if len(net) > 3 else -100
                sig = max(0, min(100, 2 * (int(rssi) + 100)))   # -100->0%, -50->100%
                locked = bool(net[4]) if len(net) > 4 else False
                if ssid:
                    out.append((ssid, sig, locked))
            return out
        except Exception as exc:  # noqa: BLE001 -- a scan failure must not crash the cart
            print("Moybyte wifi scan failed:", exc)
            return []

    def connect(self, ssid, password=""):
        """Associate with `ssid`, remember the creds, and report whether the link
        came up. An EMPTY password resolves to the stored one first -- the panel's
        known-network reconnect passes "" (it has no credential access), and the
        old behavior associated with "" AND remembered it, destroying the saved
        password (the on-glass P4 "says not connected after i exit", 2026-07-25).
        NEEDS ON-DEVICE VERIFICATION (the connect()/isconnected() poll
        timing below is a sketch -- a real impl waits on a status callback/timeout)."""
        ssid = str(ssid)
        if not password and self._store is not None and self._root is not None:
            try:
                for n in self._store.load_wifi(self._root):
                    if n["ssid"] == ssid:
                        password = n.get("password", "") or ""
                        break
            except Exception:  # noqa: BLE001 -- a store hiccup falls back to ""
                pass
        ok = False
        if self._ensure_wlan() is not None:
            try:
                self.wlan.connect(ssid, password)
                # Brief poll for association. The single-threaded desktop loop calls
                # this between frames, so keep the budget small; a real impl should
                # spread this across frames rather than block.
                for _ in range(40):
                    if self.wlan.isconnected():
                        ok = True
                        break
                    time.sleep_ms(100)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte wifi connect failed:", exc)
                ok = False
        if ok:
            self._ssid = ssid
        if self._store is not None and self._root is not None:
            try:
                self._store.remember_wifi(ssid, password, self._root)
            except Exception as exc:  # noqa: BLE001 -- save failure must not crash the cart
                print("Moybyte wifi remember failed:", exc)
        return ok

    def disconnect(self):
        if self.wlan is not None:
            try:
                self.wlan.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def status(self):
        """(connected, ssid, ip): the live link state #22/#8 read to use the net.

        The LINK question (isconnected) is answered independently of the
        DETAIL reads (ifconfig / essid): a port where either detail raises --
        the P4's C6-over-SDIO is a candidate -- used to report the whole link
        DOWN, so every status surface (the WIFI row, the panel line, the bar
        icon) said NOT CONNECTED over a working connection. A detail that
        can't be read now degrades to the remembered ssid / a null ip."""
        if self.wlan is None:
            return (False, None, None)
        try:
            live = bool(self.wlan.isconnected())
        except Exception as exc:  # noqa: BLE001
            print("Moybyte wifi status failed:", exc)
            return (False, None, None)
        if not live:
            return (False, None, None)
        ip = None
        try:
            ip = self.wlan.ifconfig()[0]
        except Exception as exc:  # noqa: BLE001 -- link is up; the detail isn't
            print("Moybyte wifi ifconfig failed:", exc)
        ssid = None
        try:
            ssid = self.wlan.config("essid") or None
        except Exception:  # noqa: BLE001 -- essid not always queryable
            ssid = None
        return (True, ssid or self._ssid, ip)

    def forget(self, ssid):
        ssid = str(ssid)
        if self._store is not None and self._root is not None:
            try:
                self._store.forget_wifi(ssid, self._root)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte wifi forget failed:", exc)
        # If we're on that network, drop it.
        try:
            if self.wlan is not None and self.wlan.isconnected():
                self.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return True

    def known(self):
        if self._store is not None and self._root is not None:
            try:
                return [n["ssid"] for n in self._store.load_wifi(self._root)]
            except Exception as exc:  # noqa: BLE001
                print("Moybyte wifi known failed:", exc)
        return []


def make_wifi(store=None, root=None):
    """Injected backend factory (#38): the device network.WLAN service over the
    moy_carts store. run_desktop hands this to the shared Workstation -- the mirror
    of the host's make_wifi. NEEDS ON-DEVICE VERIFICATION (DeviceWifi is a sketch)."""
    return DeviceWifi(store, root)


def autoconnect_wifi(wifi):
    """Boot-time autoconnect (#38): try the most-recently-remembered known network
    first (moy_carts stores it at the front), so the kid joins once and the console
    is online thereafter. Best-effort + guarded: a no-WiFi build or no saved creds
    just no-ops. NEEDS ON-DEVICE VERIFICATION -- the credential store round-trip is
    host-tested, but the actual WLAN association at boot is unproven on hardware."""
    if wifi is None:
        return False
    try:
        connected, _ssid, _ip = wifi.status()
        if connected:
            return True
        nets = []
        store = getattr(wifi, "_store", None)
        root = getattr(wifi, "_root", None)
        if store is not None and root is not None:
            nets = store.load_wifi(root)
        for n in nets:                      # front-of-list = last joined
            if wifi.connect(n["ssid"], n.get("password", "")):
                _diag_note("wifi", "autoconnected: %s" % (n["ssid"],))
                return True
    except Exception as exc:  # noqa: BLE001 -- autoconnect must never block/crash boot
        _diag_note("wifi", "autoconnect failed: %s" % (exc,))
    return False
