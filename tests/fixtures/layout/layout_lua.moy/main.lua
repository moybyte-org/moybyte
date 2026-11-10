-- The responsive fixture app (docs/app_api_v1.md section 3) as a Lua app:
-- layout_py.moy's page, `_layout` a global function. Into pmem 0: the calls
-- `_layout` had heard when `_init` ran.

calls = 0
last = "none"

function _layout(w, h, fs)
  calls = calls + 1
  last = w .. " " .. h .. " " .. fs
end

function _init()
  pmem(0, calls)
end

function _draw()
  ui.page("Layout")
  ui.label("calls " .. calls)
  ui.label(last)
end
