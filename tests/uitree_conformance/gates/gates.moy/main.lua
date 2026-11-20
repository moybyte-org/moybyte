-- Conformance fixture `gates` (level 1), the Lua half: an action hidden while
-- a toggle is off (off = "hide"), one greyed while a count is 0
-- (off = "grey"), and a node shown by a condition. Its Python half is
-- ../app.py.
ui.app("Gates", { icon = "calc" })
local s = ui.state { open = false, n = 0 }

ui.action("add", "Add one", function() s.n = s.n + 1 end, { when = "open", off = "hide" })
ui.action("take", "Take one", function() s.n = s.n - 1 end, { when = "n", off = "grey" })

ui.page("gates", function()
  ui.group("Gate", function()
    ui.toggle("open", { label = "Open" })
    ui.link("add")
    ui.link("take")
    ui.label("{n} inside", { shown = "open" })
  end)
end, { title = "Gates" })
