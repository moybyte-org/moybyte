-- Tabs that switch with Q and E (docs/ui_api_2026-10.md §2, ui.tabs), as a
-- Lua app: tabs_py.moy is the same app in Python, and tests/test_app_ui.py
-- holds both to the same tabs and pixels. The app reads no key of its own:
-- the tab row takes Q and E wherever the keys do not type into the field.
-- Into pmem: 0 the tab (from 1), 1 the field's length, 2 whether the ui
-- took the frame's keys.

tab = 1
name = ""

function _layout(w, h, fs) end

function _draw()
  ui.page("Tabs")
  ui.hints({"tabs", "Switch"})
  tab = ui.tabs("Tabs", {"One", "Two", "Three"}, tab)
  ui.label("Page " .. tab)
  ui.button("Go", "primary")
  name = ui.field("Name", name, 8)
  pmem(0, tab)
  pmem(1, #name)
  pmem(2, ui.keys_taken() and 1 or 0)
end
