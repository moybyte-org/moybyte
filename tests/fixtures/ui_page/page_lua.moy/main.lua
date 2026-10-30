-- The public UI API's fixture page (docs/ui_api_2026-10.md), as a Lua app:
-- the vendor extension moybyte.ui. page_py.moy and
-- tests/fixtures/wasm/app_ui.moy draw the same page with the same calls, and
-- tests/test_ui_public.py holds the three to the same pixels. Into pmem: 0
-- frames, 1 sound, 2 volume, 3 the theme row (from 0), 4 saves, 5 keys
-- taken, 6 the swatch's state, 7 the focused id's length, 10 the focused
-- pane (from 0), 11 the first pane's size, 12 where a tap activated the second
-- pane's part (x * 256 + y + 1; 0 none). A table at the right draws its rows
-- as parts (ui.table_part); two panes at the foot (ui.split) have a divider
-- the script drags and a part in the second it taps (ui.part_at).

sound = true
vol = 5
cur = 2
saves = 0
frames = 0
sizes = {80, 0}
spl = {}
pane = 1
tap = 0

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
  ui.at(230, 2, 80, 34)
  ui.table("", {""}, {{"a"}, {"b"}, {"c"}}, 2, {id = "parts", part = {0, 17}, bar = true})
  local i, cx, cy, cw, ch = ui.table_part()
  while i do
    rect(cx + 4, cy + 3, 20, ch - 6, 8 + i)
    i, cx, cy, cw, ch = ui.table_part()
  end
  ui.at(10, 214, 180, 20)
  pane = ui.split("panes", "x", sizes, {drag = true, focus = pane, gap = 6, mins = {40, 40}}, spl)
  ui.at(spl[5], spl[6], spl[7], spl[8])
  local px, py, pw, ph, pst = ui.part("cell")
  if pst & 2 ~= 0 then
    local ax, ay = ui.part_at()
    if ax then tap = ax * 256 + ay + 1 end
  end
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
  pmem(10, pane - 1)
  pmem(11, sizes[1])
  pmem(12, tap)
end
