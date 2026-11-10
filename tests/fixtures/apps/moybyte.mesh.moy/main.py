# Mesh: Meshtastic text on the LoRa radio. The window lists what the service
# (service.lua) heard and sent on the default channel, newest first, and sends
# what is typed; the service holds the radio and keeps running when the window
# closes. The region picks the frequency and must match the law where the
# console is: EU_868 until another is chosen.

import moybyte

ME = "moybyte.mesh"
REGIONS = ["EU_868", "EU_433", "US", "ANZ", "ANZ_433", "CN", "JP", "RU", "KR", "TW",
           "IN", "NZ_865", "TH", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923",
           "PH_433", "PH_868", "PH_915", "KZ_433", "KZ_863", "NP_865", "BR_902"]

rows = []
cur = 0
text = ""
region = 0
node = ""


def _reload(*_):
    global rows, region, node
    log = prefs.get("log") or []
    rows = []
    for m in reversed(log):
        who = m.get("from") or "me"
        line = "%s: %s" % (who, m.get("text", ""))
        if m.get("failed"):
            line += " (not sent)"
        elif m.get("from"):
            line += "  %d dBm %.1f dB" % (m.get("rssi", 0), m.get("snr", 0))
        rows.append(line)
    r = prefs.get("region")
    region = REGIONS.index(r) if r in REGIONS else 0
    n = prefs.get("node")
    node = n if isinstance(n, str) else ""


def send(words):
    if words:
        moybyte.send(ME, "send", words)


def _init():
    moybyte.on("heard", _reload)
    moybyte.service_start()
    _reload()


def _draw():
    global cur, text, region
    ui.page("Mesh")
    ui.hints(("confirm", "Send"))
    text, enter = ui.field("Message", text, 200)
    if (enter or ui.button("Send", "primary")) and text:
        send(text)
        text = ""
    cur = ui.list("LongFast", rows or ["Nothing heard yet"], cur)
    new = ui.cycle("Region", REGIONS, region)
    if new != region:
        region = new
        prefs.set("region", REGIONS[region])
        moybyte.send(ME, "retune")
    if node:
        ui.info("This node", node)
