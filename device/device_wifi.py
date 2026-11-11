"""The device WiFi service (#38), extracted from moy_runtime.py.

DeviceWifi is the injected `wifi` backend the shared Workstation exposes to a cart
whose manifest opts into the "network" permission (host == device: it mirrors the
host make_wifi). It is a SYSTEM service, and THE RADIO IS A LEASE (2026-09-07):
off unless something holds it. The holds are the kernel's lease table's radio
rows, and the kernel's WiFi driver powers the station from them
(native/moy_net/moy_net.h's THE BOOK): `Workstation.wifi_hold(tag)` takes a
row, whose first holder powers the station up, and the last
`wifi_release(tag)` lets it go, which powers it down; radio_on and radio_off
here keep this service's view of the station in step. A console on a shelf
spends nothing on WiFi. The holders are the web console, the online update
screen, the Settings WIFI panel, a cart with the "network" permission for the
length of its run, the ESP-NOW link for a match, and the Get Carts app while it
fetches. What persists is the
CREDENTIAL: the moy_carts wifi.json store, which autoconnect_wifi() replays when
a holder needs the link.

Radio coexistence caveat: WiFi shares the ESP32-S3 radio with BLE (#26) and is a
different mode from LoRa / ESP-NOW (#7) -- only one radio user can be active at a
time. WiFi STA and the display SPI bus are SEPARATE peripherals (unlike SD), so
there is no SPI-host fight (hardware-confirmed on both boards -- WiFi scan/
connect, the OTA download and moy_webhost all run on glass). The
whole class is wrapped in try/except so a board/build without WiFi degrades to a
never-connected service instead of crashing the console.

Device-only module (authored in modules/, not staged from runtime/). Imports only
`time` + the leaf device_util._diag_note, so it sits low in the device import DAG
with no moy_runtime cycle.
"""
import time

from device_util import _diag_note

# How long a join waits for an address before it reads as failed, and the
# disconnect reasons that end it sooner: the network refused the password
# (4-way handshake timeout, auth fail, handshake timeout).
JOIN_MS = 15000
REFUSED = (15, 202, 204)


def _keep_alive():
    """moy_ota.keep_alive: the connect's poll holds the frame on purpose."""
    try:
        from moy_ota import keep_alive
    except ImportError:  # pragma: no cover -- a build with no updater
        return
    keep_alive()
try:
    from moy_net import wifi_password, wifi_remember
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.net_binding import wifi_password, wifi_remember


def _rest_after_vm_start():
    """A new VM starts with no lease held, but the kernel's station outlives a
    soft reset: put it down unless the kernel's ESP-NOW link or its webhost,
    which ride it, is up (the console adopts a serving webhost and takes its
    lease back). Without this a Ctrl-D left the radio associated with nobody
    holding it."""
    try:
        import moy_net
        if not hasattr(moy_net, "wifi_status") or not moy_net.wifi_status()[1]:
            return
        link = getattr(moy_net, "Link", None)
        if link is not None and link().stats()[5]:
            return
        web = getattr(moy_net, "web_state", None)
        if web is not None and (web()[0] or web()[1]):
            return
        try:
            import moy_lease
            if moy_lease.Wifi().mask():
                return                  # the book holds it (a return start keeps the rows)
        except (ImportError, AttributeError):
            pass
        moy_net.wifi_off()
    except Exception as exc:  # noqa: BLE001 -- the console boots regardless
        _diag_note("wifi", "rest after start failed: %s" % (exc,))


class KernelWlan:
    """The station as the kernel's WiFi driver serves it (native/moy_net/
    moy_wifi.c), in the shape of the port's network.WLAN that this service and
    the link were written against. Constructing one initialises nothing;
    active(True) is the driver's start, active(False) its stop."""

    PM_NONE = 0

    def __init__(self):
        import moy_net
        self._n = moy_net

    def active(self, on=None):
        if on is True or on == 1:
            self._n.wifi_on()
        elif on is not None:
            self._n.wifi_off()
        return self._n.wifi_status()[1]

    def scan(self):
        return self._shape(self._n.wifi_scan())

    def scan_poll(self):
        """scan() without the wait: None until the scan this starts ends."""
        nets = self._n.wifi_scan(False)
        return None if nets is None else self._shape(nets)

    @staticmethod
    def _shape(nets):
        return [(s, b"", 0, rssi, auth, False) for s, rssi, auth in nets]

    def reason(self):
        """The last disconnect's reason since the connect asked, 0 for none."""
        return self._n.wifi_status()[5]

    def connect(self, ssid, password=""):
        self._n.wifi_connect(ssid, password or "")

    def disconnect(self):
        self._n.wifi_disconnect()

    def isconnected(self):
        return self._n.wifi_status()[2]

    def ifconfig(self):
        ip = self._n.wifi_status()[4] or "0.0.0.0"
        return (ip, "255.255.255.0", "0.0.0.0", "0.0.0.0")

    def config(self, key=None, **kw):
        if "pm" in kw:
            self._n.wifi_ps(kw["pm"])
            return None
        if key == "essid":
            return self._n.wifi_status()[3] or ""
        if key == "mac":
            return self._n.wifi_mac()
        if key == "pm":
            return self._n.wifi_ps()
        raise ValueError(key)


def kernel_wlan():
    """The station: the kernel's driver where the image has one, else the
    port's network.WLAN (the Zero, which serves its setup access point
    through the port's)."""
    try:
        import moy_net
        if hasattr(moy_net, "wifi_on"):
            return KernelWlan()
    except ImportError:
        pass
    import network
    return network.WLAN(network.STA_IF)


class DeviceWifi:
    """The station service over kernel_wlan(). `store`/`root` are the moy_carts credential
    store + carts dir; connect()/forget() persist there so the next boot can
    autoconnect."""

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
        # Whether the driver has been brought up this boot. One-way: radio_off()
        # stops the radio but the driver keeps its internal-RAM allocation, so a
        # measurement of the idle desk's internal SRAM has to know.
        self.driver_up = False
        self._join = None        # (ssid, password, stored, started) while a join runs
        self._joined = None      # the last join's verdict
        _rest_after_vm_start()

    def _ensure_wlan(self):
        """Bring the radio up on demand (never at boot -- see __init__)."""
        if self.wlan is not None:
            return self.wlan
        try:
            self.wlan = kernel_wlan()
            self.driver_up = True
            self.wlan.active(True)
        except Exception as exc:  # noqa: BLE001 -- no radio / no network module -> degrade
            _diag_note("wifi", "WLAN unavailable, offline: %s" % (exc,))
            self.wlan = None
        return self.wlan

    # -- the injected `wifi` API surface (host == device) ----------------
    def scan(self):
        """Nearby networks as (ssid, signal%, locked?), waited for (a cart's
        `wifi.scan()`, a few seconds; the console polls scan_poll instead).
        WLAN.scan() returns (ssid, bssid, channel, RSSI, security, hidden) tuples;
        map RSSI (~-100..-30 dBm) to a 0..100 bar and security!=0 to locked."""
        if self._ensure_wlan() is None:
            return []
        try:
            return self._nets(self.wlan.scan())
        except Exception as exc:  # noqa: BLE001 -- a scan failure must not crash the cart
            print("Moybyte wifi scan failed:", exc)
            return []

    def scan_poll(self):
        """scan() without the wait: None while the radio scans (the first call
        starts it), then its networks. A station with no polled scan (the
        port's) answers at once."""
        if self._ensure_wlan() is None:
            return []
        poll = getattr(self.wlan, "scan_poll", None)
        try:
            nets = poll() if poll is not None else self.wlan.scan()
            return None if nets is None else self._nets(nets)
        except Exception as exc:  # noqa: BLE001 -- a scan failure must not crash Settings
            print("Moybyte wifi scan failed:", exc)
            return []

    @staticmethod
    def _nets(raw):
        out = []
        for net in raw:
            ssid = net[0].decode() if isinstance(net[0], (bytes, bytearray)) else str(net[0])
            rssi = net[3] if len(net) > 3 else -100
            sig = max(0, min(100, 2 * (int(rssi) + 100)))   # -100->0%, -50->100%
            locked = bool(net[4]) if len(net) > 4 else False
            if ssid:
                out.append((ssid, sig, locked))
        return out

    def _stored_password(self, ssid):
        """The password wifi.json holds for `ssid`, else None.

        None also covers a store that could not be read: "unknown" is not the
        same as "open", and connect() below turns on that distinction.
        """
        if self._store is None or self._root is None:
            return None
        try:
            for n in self._store.load_wifi(self._root):
                if n["ssid"] == ssid:
                    return n.get("password", "") or ""
        except Exception:  # noqa: BLE001 -- a store hiccup reads as "unknown"
            pass
        return None

    def join(self, ssid, password=""):
        """Ask the station for `ssid` and return at once: True when the link
        is up, False when the radio refused the ask, None while it associates
        (joined() answers then; the console polls it, never waits). An EMPTY
        password resolves to the stored one first -- the panel's known-network
        reconnect passes "" (it has no credential access), and associating
        with "" AND remembering it destroyed the saved password (the on-glass
        P4 "says not connected after i exit", 2026-07-25).

        A credential is stored when the join settles, and only when the radio
        ASSOCIATED, or when it is a non-blank password that differs from what
        is on disk. Never a blank one the radio did not verify: the resolve
        above yields "" both for an open network and for one the store could
        not tell us about, so remembering it unconditionally lets a single
        unreadable load rewrite wifi.json as one empty-password entry and drop
        every other saved network. Storing a non-blank password even on
        FAILURE is deliberate: a join that read as failed can still associate
        later (the kernel's driver retries a loss), and moy_ota.poll_online's
        budget is what watches for that."""
        ssid = str(ssid)
        stored = self._stored_password(ssid)
        password = wifi_password(password, stored)
        self._join = None
        if self._ensure_wlan() is None:
            return self._settle(ssid, password, stored, False)
        try:
            self.wlan.connect(ssid, password)
        except Exception as exc:  # noqa: BLE001
            print("Moybyte wifi connect failed:", exc)
            return self._settle(ssid, password, stored, False)
        self._join = (ssid, password, stored, time.ticks_ms())
        return self.joined()

    def joined(self):
        """The running join's verdict: None while it associates, True once
        the link is up, False once the network refused the password or
        JOIN_MS passed; the last verdict when none runs."""
        j = self._join
        if j is None:
            return self._joined
        w = self.wlan
        up = False
        refused = False
        try:
            up = w is not None and bool(w.isconnected())
            reason = getattr(w, "reason", None)
            refused = not up and reason is not None and reason() in REFUSED
        except Exception:  # noqa: BLE001 -- a station that cannot say has not joined
            pass
        if not up and not refused and time.ticks_diff(time.ticks_ms(), j[3]) < JOIN_MS:
            return None
        self._join = None
        return self._settle(j[0], j[1], j[2], up)

    def _settle(self, ssid, password, stored, ok):
        if ok:
            self._ssid = ssid
        if wifi_remember(ok, password, stored) \
                and self._store is not None and self._root is not None:
            try:
                self._store.remember_wifi(ssid, password, self._root)
            except Exception as exc:  # noqa: BLE001 -- save failure must not crash the cart
                print("Moybyte wifi remember failed:", exc)
        self._joined = ok
        return ok

    def connect(self, ssid, password=""):
        """join(), waited out for about 4 s (a cart's `wifi.connect`): whether
        the link came up. A network that associates later is moy_ota's
        poll_online's to catch, never this wait's."""
        ok = self.join(ssid, password)
        for _ in range(40):
            if ok is not None:
                return ok
            _keep_alive()
            time.sleep_ms(100)
            ok = self.joined()
        if ok is None:
            j = self._join
            self._join = None
            ok = self._settle(j[0], j[1], j[2], False)
        return ok

    def disconnect(self):
        if self.wlan is not None:
            try:
                self.wlan.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def radio_on(self):
        """The view's half of a hold: the STA interface this service reads,
        made at once (the kernel's book has powered the station; its start
        again is a no-op). Eager on purpose -- every holder scans or connects
        right after, and the ESP-NOW link must find THIS service owning the
        interface it activates: an interface's first start is what
        initialises the driver."""
        return self._ensure_wlan() is not None

    def radio_off(self):
        """The view's half of the last release: forgotten. The kernel's book
        powered the station down as its last row went; the port's
        network.WLAN, which has no book, is disconnected and stopped here
        (`active(False)`, esp_wifi_stop: the radio and its task go idle, and
        the driver's static allocation stays). Never constructs an interface
        (see radio_on)."""
        w = self.wlan
        self.wlan = None
        self._ssid = None
        if w is None or isinstance(w, KernelWlan):
            return
        try:
            w.disconnect()
        except Exception:  # noqa: BLE001 -- nothing to disconnect is fine
            pass
        try:
            w.active(False)
        except Exception as exc:  # noqa: BLE001
            _diag_note("wifi", "radio off failed: %s" % (exc,))

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
        try:
            import moy_net
            if hasattr(moy_net, "wifi_forget"):
                moy_net.wifi_forget(ssid)   # the network the kernel keeps for its floor
        except ImportError:
            pass
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
    """Injected backend factory (#38): the device station service over the
    moy_carts store. run_desktop hands this to the shared Workstation -- the mirror
    of the host's make_wifi."""
    return DeviceWifi(store, root)


def autoconnect_wifi(wifi):
    """The link asked for from saved credentials (#38), never waited for: the
    station is started toward the most-recently-joined network (moy_carts
    keeps it at the front). True when the link is already up, None while the
    station associates, False with nothing saved or no radio. A caller that
    needs the link polls it a frame at a time (moy_ota.poll_online) or lets a
    worker task wait (moy_net_dialing)."""
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
        if not nets:
            return False
        n = nets[0]
        join = getattr(wifi, "join", None) or wifi.connect
        ok = join(n["ssid"], n.get("password", ""))
        if ok:
            _diag_note("wifi", "autoconnected: %s" % (n["ssid"],))
        return ok
    except Exception as exc:  # noqa: BLE001 -- autoconnect must never block/crash boot
        _diag_note("wifi", "autoconnect failed: %s" % (exc,))
    return False
