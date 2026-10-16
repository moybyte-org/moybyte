-- The public UI API's fixture page (docs/ui_api_2026-10.md), as a Lua app:
-- the vendor extension moybyte.ui. page_py.moy and
-- tests/fixtures/wasm/app_ui.moy draw the same page with the same calls, and
-- tests/test_ui_public.py holds the three to the same pixels. Into pmem: 0
-- frames, 1 sound, 2 volume, 3 the theme row (from 0), 4 saves, 5 keys
-- taken, 6 the swatch's state, 7 the focused id's length.

sound = true
vol = 5
cur = 2
saves = 0
frames = 0

function _draw()
  ui.page("UI page")
  ui.label("Hello")
  sound = ui.toggle("Sound", sound)
  vol = ui.slider("Volume", vol, 0, 10)
  cur = ui.list("Theme", {"Moy", "Night", "Berry"}, cur)
  local x, y, w, h, st = ui.part("swatch", 0, 16)
  rect(x + 2, y + 2, w - 4, h - 4, 8)
  ui.at(200, 214, 100, 20)
  if ui.button("Save", "primary") then saves = saves + 1 end
  local f = ui.focus()
  frames = frames + 1
  pmem(0, frames)
  pmem(1, sound and 1 or 0)
  pmem(2, vol)
  pmem(3, cur - 1)
  pmem(4, saves)
  pmem(5, ui.keys_taken() and 1 or 0)
  pmem(6, st)
  pmem(7, f and #f or 0)
end
