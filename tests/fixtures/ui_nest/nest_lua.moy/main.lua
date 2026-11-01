-- A nest opened past the engine's depth (moy_ui_api.h's MOY_UI_API_NEST) is
-- an error, as a Lua app. nest_py.moy and tests/fixtures/wasm/app_nest.moy
-- make the same calls; tests/test_ui_nest.py reads their answers. Into pmem:
-- 0..3 1 where ui.scroll, ui.text_view, ui.dialog and ui.popover raised
-- inside two open scroll regions, 4 1 where a scroll region opened after
-- them did not, 5 frames.

frames = 0

local function deep(i, verb, ...)
  local ok, err = pcall(verb, ...)
  if ok then
    pmem(i, 0)
  elseif string.find(tostring(err), "nested too deep", 1, true) then
    pmem(i, 1)
  else
    pmem(i, 2)
  end
end

function _draw()
  ui.page("Nest")
  ui.scroll("a")
  ui.scroll("b")
  deep(0, ui.scroll, "c")
  deep(1, ui.text_view, "t", {"x"})
  deep(2, ui.dialog, "D", {"OK"})
  deep(3, ui.popover, "p", 100)
  ui.scroll()
  ui.scroll()
  ui.scroll("d")
  ui.label("after")
  ui.scroll()
  pmem(4, 1)
  frames = frames + 1
  pmem(5, frames)
end
