# The public UI API's fixture page (docs/ui_api_2026-10.md), as a Python app.
# page_lua.moy and tests/fixtures/wasm/app_ui.moy draw the same page with the
# same calls, and tests/test_ui_public.py holds the three to the same pixels.
# Into pmem: 0 frames, 1 sound, 2 volume, 3 the theme row (from 0), 4 saves,
# 5 keys taken, 6 the swatch's state, 7 the focused id's length.

sound = True
vol = 5
cur = 1
saves = 0
frames = 0


def _draw():
    global sound, vol, cur, saves, frames
    ui.page("UI page")
    ui.label("Hello")
    sound = ui.toggle("Sound", sound)
    vol = ui.slider("Volume", vol, 0, 10)
    cur = ui.list("Theme", ["Moy", "Night", "Berry"], cur)
    x, y, w, h, st = ui.part("swatch", 0, 16)
    rect(x + 2, y + 2, w - 4, h - 4, 8)
    ui.at(200, 214, 100, 20)
    if ui.button("Save", "primary"):
        saves += 1
    f = ui.focus()
    frames += 1
    pmem(0, frames)
    pmem(1, 1 if sound else 0)
    pmem(2, vol)
    pmem(3, cur)
    pmem(4, saves)
    pmem(5, 1 if ui.keys_taken() else 0)
    pmem(6, st)
    pmem(7, len(f) if f else 0)
