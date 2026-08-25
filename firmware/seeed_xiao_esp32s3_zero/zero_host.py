"""The Zero: a pocketable cart-store the browser console pairs with (#41).

This is the re-based Zero (moycore plan 3.2, owner call 2026-08-12): no panel,
no console, no canvas -- the BROWSER runs the whole console (the wasm head),
and this board contributes exactly what a browser cannot: the kid's cart store
on its own flash, served and SYNCED over WiFi. It is deliberately the smallest
possible host of the 3.4 sync RPC:

    boot -> join WiFi (creds in /moy/wifi.json, the console's own shape)
         -> WebHost(/moy/carts, /moy/web)   [the SAME class the boards inject]
         -> poll() forever

`moy_webhost` serves the wasm bundle from /moy/web (pushed by provision.sh --
this board runs STOCK MicroPython, so there is no baked moy_web image copy to
fall back on), streams the store as carts.json, and applies POST /sync batches
back into /moy/carts. A cart made in the browser lands on this flash about a
second after its commit, and is still there -- served back -- on the next
visit. That is the whole product: a console in your pocket whose screen is
whatever browser is nearby.

Runs on stock MicroPython v1.28 (ESP32_GENERIC_S3-SPIRAM_OCT) with the shared
modules PUSHED as plain files (see provision.sh) -- no ESP-IDF build, which is
this port's founding arrangement (the deleted 2026-07 tree worked the same
way). STA only: the SoftAP lane measured too weak for the old streaming view
and is not re-attempted for serving megabyte bundles; a headless board with no
saved network prints and drops to the REPL.
"""

import json
import os
import time

import network

ROOT = "/moy"
CARTS_DIR = ROOT + "/carts"
WEB_DIR = ROOT + "/web"
WIFI_STORE = ROOT + "/wifi.json"
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


def connect(wait_ms=15000):
    """Join the first known network that answers. Returns the STA IP or None.
    The wait is per-network and generous for the same reason moy_ota's
    ensure_online waits: cold association routinely outlives a short poll."""
    try:
        network.hostname(HOSTNAME)
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


def serve():
    """Bring the store host up and pump it forever. Ctrl-C drops to the REPL
    (which is how provision.sh and mpremote get the board back)."""
    _mkdir(ROOT)
    _mkdir(CARTS_DIR)
    _mkdir(WEB_DIR)
    ip = connect()
    if ip is None:
        print("ZERO no wifi -- put creds in %s and reboot" % WIFI_STORE)
        return
    from moy_webhost import WebHost
    host = WebHost(CARTS_DIR, WEB_DIR)
    host.start(ip)
    print("ZERO serving http://%s:%d/  (mDNS %s.local)"
          % (ip, host.port, HOSTNAME))
    while True:
        # The console boards poll between frames; this board HAS no frames, so
        # a short sleep is its whole duty cycle. 10ms keeps a page load snappy
        # while idling at ~zero.
        host.poll()
        time.sleep_ms(10)
