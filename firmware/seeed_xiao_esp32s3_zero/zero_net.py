# MoyByte Zero -- WiFi bring-up (Seeed XIAO ESP32-S3).
#
# The Zero is HEADLESS: it has no screen/keyboard/SD, so its whole UI is streamed
# to a browser over WiFi (the "browser is the GPU" web-view model, #41/#22). The
# first thing the board must do on boot is get a network the phone/laptop can reach
# it on. Two modes, config-driven (see zero_config):
#
#   AP  (default): the board hosts its OWN network ("MoyByte-Zero"). Join it, then
#       open http://192.168.4.1  -- works anywhere, no router needed.
#   STA (optional): the board JOINS your home WiFi and gets an IP on your LAN, so a
#       phone already on that WiFi reaches it without switching networks.
#
# This module ONLY brings the interface up and returns its IP; it does not touch the
# console/web-view (that's main.py). Pure `network` + `time`, nothing device-specific
# beyond the ESP32 WLAN, so it stays trivially testable from the REPL.

import network
import time

AP_SSID = "MoyByte-Zero"
AP_KEY = "moybyte123"          # >= 8 chars for WPA2
AP_CHANNEL = 6


def start_ap(ssid=AP_SSID, key=AP_KEY, channel=AP_CHANNEL):
    """Host our own WiFi network. Returns the AP IP (always 192.168.4.1 on ESP32)."""
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    # v1.28 prefers config(ssid=, security=, key=); older firmware wants essid/authmode/
    # password. Try the new form, fall back so this file is firmware-version-proof.
    try:
        ap.config(ssid=ssid, security=network.AUTH_WPA2_PSK, key=key)
    except (ValueError, OSError, TypeError):
        ap.config(essid=ssid, authmode=network.AUTH_WPA2_PSK, password=key)
    try:
        ap.config(channel=channel)
    except (ValueError, OSError):
        pass
    _wait_active(ap)
    return ap.ifconfig()[0]


def start_sta(ssid, key, timeout_ms=15000):
    """Join an existing WiFi. Returns the leased IP, or None on timeout."""
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        sta.connect(ssid, key)
        t = time.ticks_ms()
        while not sta.isconnected():
            if time.ticks_diff(time.ticks_ms(), t) > timeout_ms:
                return None
            time.sleep_ms(200)
    return sta.ifconfig()[0]


def _wait_active(iface, timeout_ms=3000):
    t = time.ticks_ms()
    while not iface.active():
        if time.ticks_diff(time.ticks_ms(), t) > timeout_ms:
            break
        time.sleep_ms(50)
