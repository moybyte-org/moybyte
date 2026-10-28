-- The public UI API's restyle verbs (docs/look_2026-10.md step 31) as a Lua
-- app: a verb, three tools (one current, one disabled), a rule and the bar's
-- title and tools. restyle_py.moy and
-- tests/fixtures/wasm/app_restyle.moy draw the same page with the same calls,
-- and tests/test_ui_parts.py holds the three to the same pixels. Into pmem: 0
-- frames, 1 the verb's activations, 2 the tools' (Select 1, Cut 10), 3 the
-- bar's answer (from 1; 0 none), 5 the focused id's length.

makes = 0
tools = 0
frames = 0

function _draw()
  ui.page("")
  ui.at(98, 120, 124, 26)
  ui.icon("new")
  if ui.button("Make", "primary") then makes = makes + 1 end
  ui.at(0, 196, 320, 1)
  ui.rule()
  ui.at(10, 200, 42, 32)
  if ui.tool("select", "Select", {current = true}) then tools = tools + 1 end
  ui.at(56, 200, 42, 32)
  ui.tool("copy", "Copy\tCtrl+C", {disabled = true})
  ui.at(102, 200, 42, 32)
  if ui.tool("cut", "Cut") then tools = tools + 10 end
  local b = ui.bar("Title", {"undo", "redo"}, {false, true})
  local f = ui.focus()
  frames = frames + 1
  pmem(0, frames)
  pmem(1, makes)
  pmem(2, tools)
  pmem(3, b)
  pmem(5, f and #f or 0)
end
