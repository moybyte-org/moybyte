# The ui parts an app composes a chat from (docs/ui_api_2026-10.md), one
# page each, as a Python app: parts_lua.moy and parts_wasm.moy are the same
# app on the other tiers, and tests/test_app_ui_parts.py holds the three to
# the same pixels. pmem 0 names the page the test asks for.


def _layout(w, h, fs):
    pass


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
    elif page == 5:
        ui.list("Region", ["EU_868", "US", "CN"], 1,
                {"values": ["Europe", "United States", "China"], "check": True})
