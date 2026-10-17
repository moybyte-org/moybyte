-- The public UI API's Appearance verbs (docs/look_2026-10.md step 20) as a
-- Lua app: a tab row and a grid of cards whose pictures the app draws.
-- look_py.moy and tests/fixtures/wasm/app_look.moy draw the same page with
-- the same calls, and tests/test_ui_parts.py holds the three to the same
-- pixels. Into pmem: 0 frames, 1 the tab (from 0), 2 the card picked (from
-- 0), 3 the cards activated, 5 the focused id's length, 7 the first card's
-- picture's left.

tab = 1
picked = 1
acts = 0
frames = 0
names = {"Red", "Green", "Blue", "Grey"}
subs = {"one", "two", "three", "four"}
colours = {8, 11, 12, 6}

function _draw()
  local x, y, w, h = ui.page("Look")
  tab = ui.tabs("Kind", {"Pictures", "Carts"}, tab)
  ui.grid("cards", 60, 40)
  local left = 0
  for i = 1, 4 do
    local cx, cy, cw, ch, st = ui.card(names[i], subs[i], {current = i == picked})
    if i == 1 then left = cx end
    if cw > 0 then
      rect(cx, cy, cw, ch, colours[i])
      rect(cx + 4, cy + 4, 8, 8, 7)
    end
    if st & 2 ~= 0 then
      picked = i
      acts = acts + 1
    end
  end
  ui.grid()
  ui.link("After", "")
  local f = ui.focus()
  frames = frames + 1
  pmem(0, frames)
  pmem(1, tab - 1)
  pmem(2, picked - 1)
  pmem(3, acts)
  pmem(5, f and #f or 0)
  pmem(7, left)
end
