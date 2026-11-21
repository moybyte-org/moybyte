-- Conformance fixture `kinds` (level 2), the Lua half: the kinds the C engine
-- draws beyond level 1 -- a seg over an enum worded by a label table, a
-- progress bar, a menu and its item, a table with its columns, a lines view --
-- and an action a table's row fires. Its Python half is ../app.py.
ui.app("Kinds", { icon = "list" })
local s = ui.state { mode = ui.enum("a", "b", "c", { init = "a" }), done = 2, log = "one\ntwo",
                     items = ui.model { key = "id", id = "", name = "", size = 0 }, sel = ui.row_of("items"), picked = "" }
ui.labels("MODES", { a = "Apple", b = "Bean", c = "Corn" })

ui.hook("opened", function()
  s.items:set({ { id = "a", name = "Apples", size = 3 }, { id = "b", name = "Bread", size = 1 } })
end)
ui.action("pick", "Pick", function(row) s.picked = "Picked " .. row.name end, { takes = "row" })
ui.action("bump", "More", function() s.done = s.done + 1 end)

ui.page("kinds", function()
  ui.seg("mode", { label = "Mode", labels = "MODES" })
  ui.progress("done", { max = 5, label = "Done" })
  ui.menu("Do", function() ui.item("bump") end)
  ui.table("items", { cur = "sel", cols = { "Name", "Size" }, on = "pick" }, function()
    ui.col("{.name}")
    ui.col("{.size}")
  end)
  ui.text_view("log")
  ui.label("{picked}")
end, { title = "Kinds" })
