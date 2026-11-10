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
