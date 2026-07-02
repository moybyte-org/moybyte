# MoyByte Zero -- boot entry (Seeed XIAO ESP32-S3, headless).
#
# MicroPython runs boot.py then this file on every power-up. Start the headless console +
# web view; it brings up the network itself:
#   * JOIN a saved WiFi (STA mode) if the WiFi cart has provisioned one -- best streaming,
#     the same mode the T-Deck uses. Reachable at http://moybyte.local (mDNS) or its IP.
#   * else HOST its own AP ("MoyByte-Zero") so you can join it and use the WiFi cart to save
#     a network. Reachable at http://192.168.4.1.
#
# The device runs cart logic and streams draw-commands; the browser rasterizes. A crash
# raises out to the REPL (recoverable) instead of silently looping.

try:
    import moy_zero
    moy_zero.run_zero(port=80)
except Exception as exc:
    print("[zero] console failed:", exc)
    raise
