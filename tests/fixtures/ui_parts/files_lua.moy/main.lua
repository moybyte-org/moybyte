-- The public UI API's Files verbs (docs/look_2026-10.md step 21) as a Lua
-- app: a list in columns, a scroll region, a field, an icon on a button and
-- a dialog. files_py.moy and tests/fixtures/wasm/app_files.moy draw the same
-- page with the same calls, and tests/test_ui_parts.py holds the three to
-- the same pixels. Into pmem: 0 frames, 1 the current row (from 0), 2 the
-- rows opened, 3 the name's length, 4 the times Enter was pressed in it, 5
-- the focused id's length, 7 the dialog's answers (each the button + 1).

rows = {{"paint", "castle", "Drawing"}, {"music", "theme", "Song"},
        {"notes", "story", "Note"}, {"game", "pong", "Game"},
        {"paint", "dragon", "Drawing"}}
cur = 1
opened = 0
name = "pic"
dones = 0
asking = false
picks = 0
frames = 0

function _draw()
  local x, y, w, h = ui.page("Files")
  ui.at(x, y, w, 97)
  local o
  cur, o = ui.table("Art", {"", "Name", "Kind"}, rows, cur)
  if o then opened = opened + 1 end
  ui.at(x, y + 97, w, 40)
  ui.scroll("more")
  ui.link("One", "1")
  ui.link("Two", "2")
  ui.link("Three", "3")
  ui.scroll()
  ui.at(x, y + 141, w, 20)
  local d
  name, d = ui.field("Name", name, 8)
  if d then dones = dones + 1 end
  ui.at(x + 8, y + 165, 96, 20)
  ui.icon("lock")
  if ui.button("Delete", "danger") then asking = true end
  if asking then
    local p = ui.dialog("Delete?", {"Cancel", "Delete"}, "danger")
    ui.label("For good.")
    ui.dialog()
    if p > 0 then
      asking = false
      picks = picks + p
    end
  end
  local f = ui.focus()
  frames = frames + 1
  pmem(0, frames)
  pmem(1, cur - 1)
  pmem(2, opened)
  pmem(3, #name)
  pmem(4, dones)
  pmem(5, f and #f or 0)
  pmem(7, picks)
end
