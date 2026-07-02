# MoyByte Zero -- boot entry (Seeed XIAO ESP32-S3, headless).
#
# MicroPython runs boot.py then this file on every power-up. Milestone M0: bring up
# the WiFi the browser will reach the console on, and stop -- leaving the REPL free
# for development. M1 will start the headless console + web view here (the browser
# renders the streamed draw-commands; the device never rasterizes).
#
# Mode is AP by default (host our own "MoyByte-Zero" network -- works with no router).
# Drop a `zero_config.py` on the board to override: MODE='sta', plus WIFI_SSID/WIFI_KEY
# to join your home WiFi instead.

import zero_net

MODE = "ap"                 # "ap" = host our own network; "sta" = join yours
WIFI_SSID = ""              # for MODE='sta'
WIFI_KEY = ""

try:
    import zero_config       # optional on-device override
    MODE = getattr(zero_config, "MODE", MODE)
    WIFI_SSID = getattr(zero_config, "WIFI_SSID", WIFI_SSID)
    WIFI_KEY = getattr(zero_config, "WIFI_KEY", WIFI_KEY)
except ImportError:
    pass


def _banner(ip):
    print("=" * 40)
    print("  MoyByte Zero")
    if MODE == "sta":
        print("  joined WiFi:", WIFI_SSID)
    else:
        print("  network:", zero_net.AP_SSID, " key:", zero_net.AP_KEY)
    print("  reach me at: http://%s" % (ip or "?"))
    print("=" * 40)


def start_network():
    if MODE == "sta" and WIFI_SSID:
        ip = zero_net.start_sta(WIFI_SSID, WIFI_KEY)
        if ip is None:
            print("[zero] STA join failed -- falling back to AP")
            ip = zero_net.start_ap()
    else:
        ip = zero_net.start_ap()
    return ip


ip = start_network()
_banner(ip)

# M1: start the headless console + web view. The device runs cart logic and streams
# draw-commands; the browser rasterizes. Serves on port 80 so the URL is just the IP.
# A crash raises out to the REPL (recoverable) instead of silently looping.
try:
    import moy_zero
    moy_zero.run_zero(ip=ip, port=80)
except Exception as exc:
    print("[zero] console failed:", exc)
    raise
