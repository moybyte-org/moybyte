"""The Zero: a pocketable cart-store the browser console pairs with (#41).

This is the re-based Zero (moycore plan 3.2, owner call 2026-08-12): no panel,
no console, no canvas -- the BROWSER runs the whole console (the wasm head),
and this board contributes exactly what a browser cannot: the kid's cart store
on its own flash, served and SYNCED over WiFi, and its PINS (#9). It is
deliberately the smallest possible host of the 3.4 sync RPC:

    boot -> join WiFi (creds in /moy/wifi.json, the console's own shape)
         -> WebHost(/moy/carts, /moy/web) + /gpio   [the SAME class the boards
         -> poll() forever                           inject, one endpoint more]

    ...no joinable network
         -> zero_setup.run()  the SoftAP + form a first boot is configured
                              through, which reboots back into the line above

`moy_webhost` serves the wasm bundle from /moy/web (pushed by provision.sh --
this board runs STOCK MicroPython, so there is no baked moy_web image copy to
fall back on), streams the store as carts.json, and applies POST /sync batches
back into /moy/carts. A cart made in the browser lands on this flash about a
second after its commit, and is still there -- served back -- on the next
visit. That is the whole product: a console in your pocket whose screen is
whatever browser is nearby.

`POST /gpio` is the second half of the same idea (zero_gpio): the cart in the
browser calls `pin_write`, the page batches it here, and this board -- which
has the pins -- does the driving.

Runs on stock MicroPython v1.28 (ESP32_GENERIC_S3-SPIRAM_OCT) with the shared
modules PUSHED as plain files (see provision.sh) -- no ESP-IDF build, which is
this port's founding arrangement (the deleted 2026-07 tree worked the same
way). STA is the SERVING mode: the SoftAP lane measured too weak for the old
streaming view and is not re-attempted for serving megabyte bundles -- setup
is the one thing it does, because a board nobody can configure is worse than a
board that serves slowly for a minute.
"""

import json
import os
import time

try:
    import network
except ImportError:                  # host: this module is import-only there
    network = None

import zero_gpio

ROOT = "/moy"
CARTS_DIR = ROOT + "/carts"
WEB_DIR = ROOT + "/web"
WIFI_STORE = ROOT + "/wifi.json"
ZERO_STORE = ROOT + "/zero.json"   # this board's own name + write pin (setup)
HOSTNAME = "moybyte-zero"          # mDNS: a headless board needs a findable name


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _networks():
    """The known-networks list out of /moy/wifi.json -- the same document the
    console's WiFi panel writes ({"networks": [{"ssid", "password"}]}), so a
    card/creds file moved from a console board just works."""
    try:
        with open(WIFI_STORE) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    nets = doc.get("networks") if isinstance(doc, dict) else doc
    return [n for n in (nets or []) if isinstance(n, dict) and n.get("ssid")]


def identity():
    """This board's own name and write PIN (/moy/zero.json), or {}.

    Written by first-run setup and by nothing else. Absent on a board whose
    credentials were pushed over USB, which is why every reader defaults: the
    provisioning path is not going away just because the AP path exists.
    """
    try:
        with open(ZERO_STORE) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def connect(wait_ms=15000, hostname=None):
    """Join the first known network that answers. Returns the STA IP or None.
    The wait is per-network and generous for the same reason moy_ota's
    ensure_online waits: cold association routinely outlives a short poll."""
    try:
        network.hostname(hostname or HOSTNAME)
    except Exception:                    # noqa: BLE001 -- older port: no mDNS
        pass
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        return sta.ifconfig()[0]
    for net in _networks():
        try:
            sta.connect(net["ssid"], net.get("password") or net.get("key") or "")
        except OSError:
            continue
        for _ in range(wait_ms // 250):
            if sta.isconnected():
                print("ZERO wifi:", net["ssid"], sta.ifconfig()[0])
                return sta.ifconfig()[0]
            time.sleep_ms(250)
        print("ZERO wifi: no answer from", net["ssid"])
    return None


def zero_host_class():
    """`WebHost` + this board's /gpio, built on first use.

    A function because the subclass cannot exist before its base is imported,
    and `moy_webhost` is imported inside `serve()` on purpose -- importing this
    module must stay free of everything that only works on the board.
    """
    from moy_webhost import WebHost

    class ZeroHost(WebHost):
        """The shared store host plus THIS board's pins.

        A subclass and not an edit to `moy_webhost`, because /gpio is not a
        console-board endpoint: the other three boards spend their GPIOs on a
        panel, a touch controller and an SD card, and would be handing out the
        pins their own screen is drawn through. The Zero has a spare header and
        no screen, which is the whole difference.
        """

        def __init__(self, *a, **kw):
            WebHost.__init__(self, *a, **kw)
            self._pins = None       # built on the first /gpio, not at boot: a
                                    # board nobody wires anything to should not
                                    # import machine.Pin to find that out

        def handle_http(self, method, path, body):
            if path.split("?", 1)[0] == "/gpio":
                if self._pins is None:
                    self._pins = zero_gpio.pin_factory()
                return zero_gpio.handle(method, body, pin=self.pin,
                                        get_pin=self._pins)
            return WebHost.handle_http(self, method, path, body)

    return ZeroHost


def serve():
    """Bring the store host up and pump it forever. Ctrl-C drops to the REPL
    (which is how provision.sh and mpremote get the board back)."""
    _mkdir(ROOT)
    _mkdir(CARTS_DIR)
    _mkdir(WEB_DIR)
    me = identity()
    name = me.get("name") or HOSTNAME
    ip = connect(hostname=name)
    if ip is None:
        # NO WAY IN: host the setup AP instead of printing and giving up. This
        # is the one branch that never returns -- a filled-in form resets the
        # board, and a board nobody fills in stays reachable until someone does.
        print("ZERO no wifi -- hosting the setup AP")
        import zero_setup
        zero_setup.run(WIFI_STORE, ZERO_STORE)
        return
    host = zero_host_class()(CARTS_DIR, WEB_DIR, pin=me.get("pin"))
    host.start(ip)
    # The pin is PRINTED, and printed as part of the url. A page reaches this
    # board's write half (POST /sync, POST /gpio) by carrying `?pin=`, so a pin
    # nobody is told is a board that silently refuses every edit made on it.
    # Serial is the right channel for it: reading this line takes the cable.
    pin = me.get("pin")
    print("ZERO serving http://%s:%d/%s  (mDNS %s.local)%s"
          % (ip, host.port, ("?pin=" + pin) if pin else "", name,
             "" if pin else "  [no pin -- writes are open]"))
    while True:
        # The console boards poll between frames; this board HAS no frames, so
        # a short sleep is its whole duty cycle. 10ms keeps a page load snappy
        # while idling at ~zero.
        host.poll()
        time.sleep_ms(10)
