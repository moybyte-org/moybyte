-- Conformance fixture `lists` (level 2), the Lua half: a tab strip bound to
-- an enum, rows of links, and the same rows as cards
-- whose card fires an action with its row. Its Python half is
-- ../app.py.
ui.app("Lists", { icon = "list" })
local s = ui.state { tab = ui.enum("all", "few", { init = "all" }), picked = "",
                     items = ui.model { key = "id", id = "", name = "", kind = ui.enum("fruit", "bread", { init = "fruit" }) },
                     sel = ui.row_of("items") }
ui.labels("TABS", { all = "All", few = "Few" })

ui.hook("opened", function()
  s.items:set({ { id = "apple", name = "Apples", kind = "fruit" }, { id = "bread", name = "Bread", kind = "bread" },
                { id = "pear", name = "Pears", kind = "fruit" } })
end)
ui.action("pick", "Pick", function(row) s.picked = "Picked " .. row.name end, { takes = "row" })
ui.action("open", "Open", function(row) s.picked = "Opened " .. row.id end, { takes = "row" })

ui.page("lists", function()
  ui.tabs("tab", { labels = "TABS" })
  ui.group("Things", function()
    ui.rows("items", function()
      ui.link("pick", { label = "{.name}" })
    end)
  end)
  ui.group("Shelf", function()
    ui.cards("items", { cur = "sel", w = 96, h = 64 }, function()
      ui.card("open", { label = "{.name}", sub = "{.kind}" })
    end)
  end)
  ui.label("{picked}")
end, { title = "Lists" })
