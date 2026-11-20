-- Conformance fixture `pages` (level 1), the Lua half: a dialog shown by a
-- state, its two buttons, and a pushed page Back pops. Its Python half is
-- ../app.py.
ui.app("Pages", { icon = "calc" })
local s = ui.state { count = 0, asking = false }

ui.action("add", "Add one", function() s.count = s.count + 1 end)
ui.action("ask", "Reset", function() s.asking = true end)
ui.action("keep", "Keep", function() s.asking = false end)
ui.action("reset", "Reset", function() s.count = 0; s.asking = false end)
ui.action("more", "Details", function() ui.push("details") end)

ui.page("pages", function()
  ui.group("Count", function()
    ui.info("Count", { value = "{count}" })
    ui.link("add")
    ui.link("ask")
    ui.link("more")
  end)
  ui.dialog("Reset the count?", { buttons = { "keep", "reset" }, shown = "asking" }, function()
    ui.label("It goes back to 0.")
  end)
end, { title = "Pages" })

ui.page("details", function()
  ui.group("Now", function()
    ui.info("Count", { value = "{count}" })
  end)
end, { title = "Details" })
