-- Conformance fixture `counter` (level 1), the Lua half: a count, an action,
-- an action that is off while the count is 0, a toggle and the templates that
-- show them. Its Python half is ../app.py.
ui.app("Counter", { icon = "calc" })
local s = ui.state { count = 0, loud = false }

ui.action("add", "Add one", function() s.count = s.count + 1 end)
ui.action("reset", "Reset", function() s.count = 0 end, { when = "count", off = "grey" })

ui.page("counter", function()
  ui.group("Count", function()
    ui.info("Count", { value = "{count}" })
    ui.toggle("loud", { label = "Loud" })
    ui.link("add")
    ui.link("reset")
    ui.label("{count|plural press,presses} so far, {loud?loud:quiet}")
  end)
end, { title = "Counter" })
