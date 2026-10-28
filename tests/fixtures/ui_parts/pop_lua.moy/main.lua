-- The public UI API's popover (docs/look_2026-10.md step 24) as a Lua app:
-- a button opens a popover hung under it, holding a seg and a button that
-- closes it. pop_py.moy and tests/fixtures/wasm/app_pop.moy draw the same
-- page with the same calls, and tests/test_ui_parts.py holds the three to
-- the same pixels. Into pmem: 0 frames, 1 whether the popover is open, 2
-- the size (from 0), 3 the closes, 5 the focused id's length, 7 the link
-- under it activated.

shown = false
size = 1
closes = 0
under = 0
frames = 0

function _draw()
  ui.page("Pop")
  if ui.button("Pick") then shown = true end
  if ui.link("Under", "") then under = under + 1 end
  if shown then
    shown = ui.popover("pick", 200)
    size = ui.seg("Size", {"1x1", "2x2", "3x3"}, size)
    if ui.button("Done") then
      shown = false
      closes = closes + 1
    end
    ui.popover()
  end
  local f = ui.focus()
  frames = frames + 1
  pmem(0, frames)
  pmem(1, shown and 1 or 0)
  pmem(2, size - 1)
  pmem(3, closes)
  pmem(5, f and #f or 0)
  pmem(7, under)
end
