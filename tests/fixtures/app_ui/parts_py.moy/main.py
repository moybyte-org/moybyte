# The ui parts an app composes a chat from (docs/ui_api_2026-10.md), one
# page each, as a Python app: parts_lua.moy and parts_wasm.moy are the same
# app on the other tiers, and tests/test_app_ui_parts.py holds the three to
# the same pixels. pmem 0 names the page the test asks for.


def _layout(w, h, fs):
    pass


HOME = ("Srbija", "Planinari", "Ana Ridge", "Base Camp")
CHANNELS = ("LongFast", "Planinari", "Base", "Open", "Family")


def _draw():
    page = pmem(0)
    px, py, pw, ph = ui.page("Parts")
    if page == 1:
        ui.empty("bubble", "No messages yet", ["Say hello to the mesh", "Messages show here"])
        ui.button("Write one", "primary")
    elif page == 2:
        ui.bubble("See you at the top", "in", {"head": "Ana Ridge", "signal": 3})
        ui.bubble("Ten minutes", "out", {"status": "Sending\u2026"})
        ui.bubble("Wait for me at the gate", "out",
                  {"tone": "failed", "status": "Not sent", "id": "gate"})
        st = ui.bubble("Clear skies up here, wind from the north. Bring a warm layer, the hut is cold after dark and the stove is out. Bring tea for the morning, and the long rope from the shed by the gate.", "in", {"head": "1E2F", "signal": 1, "anchor": True, "max": 0.5, "lines": 3})[4]
        pmem(1, st & ui.CUT)
        ui.focus("gate")
    elif page == 3:
        ui.group("Heard")
        ui.info("From", "Ana Ridge")
        ui.info("Signal", "-104 dBm", {"signal": 2})
        ui.info("SNR", "2.0 dB")
        ui.group()
        ui.link("Base Camp", "-96 dBm", {"signal": 4})
    elif page == 4:
        x, y, w, h, st = ui.bubble("Wait for me at the gate", "out",
                                   {"tone": "failed", "status": "Not sent", "anchor": True})
        ui.at(x, y, w, h)
        ui.popover("details", pw - 16)
        ui.info("Channel", "LongFast")
        ui.info("Status", "Not sent")
        ui.button("Delete", "danger")
        ui.button("Send again", "primary")
        ui.popover()
        ui.focus("Send again")
    elif page == 6:
        ui.label("LongFast")
        ui.scroll("history")
        ui.bubble("See you at the top", "in", {"head": "Ana Ridge", "signal": 3})
        x, y, w, h, st = ui.bubble("Wait for me at the gate", "out",
                                   {"tone": "failed", "status": "Not sent", "anchor": True})
        ui.at(x, y, w, h)
        ui.scroll()
        ui.popover("details", pw - 16, {"wash": "view"})
        ui.info("Status", "Not sent")
        ui.button("Send again", "primary")
        ui.popover()
        ui.focus("Send again")
    elif page == 5:
        ui.list("Region", ["EU_868", "US", "CN"], 1,
                {"values": ["Europe", "United States", "China"], "check": True})
    elif page == 7:
        ui.group("", {"card": True})
        ui.list("", HOME, -1, {"values": ["2 min", "20 min", "4 min", "1 h"],
                               "subs": ["Base Camp: Gate opens at seven",
                                        "Milo's Deck: Snow above the hut",
                                        "Bring the spare headlamp, mine is dead",
                                        "You: Back by noon"],
                               "icons": ["bubble", "bubble", "@Ana Ridge", "@base camp"],
                               "counts": [0, 3, "2", ""], "tags": ["Public", "", "", ""],
                               "marks": ["", "lock", "lock", "lock"]})
        ui.group()
        ui.link("Nodes", "4 heard")
    elif page == 8:
        ui.scroll("radio", {"snap": True})
        ui.group("This node")
        ui.info("Name", "moybyte-beef")
        ui.info("Short name", "BEEF")
        ui.info("Id", "!A1B2BEEF")
        ui.group("Mesh")
        ui.link("Preset", "MediumFast")
        ui.link("Region", "EU_868")
        ui.group("Channels")
        ui.list("", CHANNELS, 0, {"values": ["Primary", "256-bit key", "128-bit key",
                                             "No key", "256-bit key"]})
        ui.group()
        ui.scroll()
    elif page == 9:
        ui.note("warn", ["Every radio on a mesh uses one preset.",
                         "On another one you hear none of them."])
        ui.link("LongFast", "", {"tag": "Public"})
        ui.group("Channel")
        ui.info("Key", "256-bit", {"tag": "Private"})
        ui.group()
        ui.note("paste", ["Your clipboard holds a channel link."])
