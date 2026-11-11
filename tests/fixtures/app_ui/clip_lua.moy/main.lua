-- Tables of parts whose rows hold placed ui verbs (docs/ui_api_2026-10.md
-- §2, "A table of parts"), as a Lua app: clip_py.moy is the same app in
-- Python, and tests/test_app_ui.py holds both to the tables' views. Each
-- view is a row and a half short of whole rows; the left opens on its first
-- row and the right on its last, so a row shows cut at the left's foot and
-- at the right's top. Into pmem 0..7: each view's x, y, w and h.

rows = {}
for i = 1, 10 do rows[i] = {"row " .. i} end
cur = {1, 10}

function _layout(w, h, fs) end

function _draw()
  local x, y, w = ui.page("Clip")
  local vw = (w - 24) // 2
  for t = 1, 2 do
    local vx, vy, vh = x + 8 + (t - 1) * (vw + 8), y + 30, 60
    ui.at(vx, vy, vw, vh)
    cur[t] = ui.table("", {""}, rows, cur[t], {id = "rows" .. t, part = {0, 24}})
    local i, cx, cy, cw, ch = ui.table_part()
    while i do
      ui.at(cx, cy, cw - 40, ch)
      ui.label(rows[i][1])
      ui.at(cx + cw - 36, cy + 2, 32, ch - 4)
      ui.button("Go", "primary")
      i, cx, cy, cw, ch = ui.table_part()
    end
    pmem(4 * t - 4, vx)
    pmem(4 * t - 3, vy)
    pmem(4 * t - 2, vw)
    pmem(4 * t - 1, vh)
  end
end
