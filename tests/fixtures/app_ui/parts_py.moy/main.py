# The ui parts an app composes a chat from (docs/ui_api_2026-10.md), one
# page each, as a Python app: parts_lua.moy and parts_wasm.moy are the same
# app on the other tiers, and tests/test_app_ui_parts.py holds the three to
# the same pixels. pmem 0 names the page the test asks for.


def _layout(w, h, fs):
    pass


def _draw():
    page = pmem(0)
    ui.page("Parts")
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
