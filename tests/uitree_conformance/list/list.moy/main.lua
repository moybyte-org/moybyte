-- Conformance fixture `list` (level 1), the Lua half: a model filled at open,
-- rows with a cursor, a row action and a label it writes. Its Python half is
-- ../app.py.
ui.app("Shopping", { icon = "list" })
local s = ui.state { items = ui.model { key = "id", id = "", name = "" }, sel = ui.row_of("items"), msg = "" }

ui.hook("opened", function()
  s.items:set({ { id = "a", name = "Apples" }, { id = "b", name = "Bread" }, { id = "c", name = "Cheese" } })
end)
ui.action("pick", "Pick", function(row) s.msg = "Picked " .. row.name end, { takes = "row" })

ui.page("shopping", function()
  ui.group("Things", function()
    ui.rows("items", { cur = "sel" }, function()
      ui.link("pick", { label = "{.name}" })
    end)
    ui.label("{msg}")
  end)
end, { title = "Shopping" })
