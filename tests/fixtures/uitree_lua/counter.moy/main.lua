-- The first-hour app in the UI tree API: a page of state, an action and the
-- templates that show them.
ui.app("Counter", { icon = "calc" })
local s = ui.state { wifi = false, level = 80, vol = 5, count = 0 }

ui.action("add", "Add one", function() s.count = s.count + 1 end, { hint = "Add" })

ui.page("demo", function()
  ui.toggle("wifi", { label = "Wi-Fi" })
  ui.info("Battery", { value = "{level}%" })
  ui.slider("vol", { label = "Volume", lo = 0, hi = 10 })
  ui.button("add", { focus = true })
  ui.label("Count is {count}")
  ui.label("{count|plural press,presses} so far, Wi-Fi {wifi?on:off}")
end, { title = "Demo" })
