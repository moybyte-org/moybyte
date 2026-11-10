-- The ui parts an app composes a chat from (docs/ui_api_2026-10.md), one
-- page each, as a Lua app: parts_py.moy is the same app in Python, and
-- tests/test_app_ui_parts.py holds the tiers to the same pixels. pmem 0
-- names the page the test asks for.

function _layout(w, h, fs) end

function _draw()
  local page = pmem(0)
  ui.page("Parts")
  if page == 1 then
    ui.empty("bubble", "No messages yet", {"Say hello to the mesh", "Messages show here"})
    ui.button("Write one", "primary")
  end
end
