-- The public UI API's parts page (docs/look_2026-10.md step 12), as a Lua
-- app. parts_py.moy and tests/fixtures/wasm/app_parts.moy draw the same page
-- with the same calls, and tests/test_ui_parts.py holds the three to the
-- same pixels. Into pmem: 0 frames, 1 the link's activations, 2 the size
-- (from 0), 3 the mode, 4 the accent, 5 the focused id's length, 7 the
-- content's top.

opens = 0
size = 2
mode = 1
accent = 1
frames = 0

function _draw()
  local x, y, w, h = ui.page("Parts")
  ui.hints({"enter", "Open", "left right", "Change"})
  ui.group("General")
  if ui.link("Wi-Fi", "Home") then opens = opens + 1 end
  size = ui.cycle("Size", {"S", "M", "L"}, size)
  ui.info("Board", "T-Deck")
  ui.group("Look")
  mode = ui.seg("Mode", {"Day", "Night"}, mode)
  accent = ui.swatches("Accent", {0xff0000, 0x00c000, 0x0000ff}, accent)
  ui.group()
  local f = ui.focus()
  frames = frames + 1
  pmem(0, frames)
  pmem(1, opens)
  pmem(2, size - 1)
  pmem(3, mode - 1)
  pmem(4, accent - 1)
  pmem(5, f and #f or 0)
  pmem(7, y)
end
