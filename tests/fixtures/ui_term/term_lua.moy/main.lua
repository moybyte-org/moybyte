-- A terminal on the public UI API's text area (ui.text_view,
-- docs/ui_api_2026-10.md): term_py.moy's app, the same calls in Lua.
-- pmem 0 is the lines written, 1 the first line shown (from 0), 2 the rows
-- shown.

log = {"\27[36mmoy\27[0m: hello", "\27[32mana\27[0m: hi there", "\27[90m-- joined --\27[0m"}
typed = "ls -la"
frames = 0

function _draw()
  frames = frames + 1
  if frames % 3 == 0 and frames <= 30 then
    local n = #log
    log[n + 1] = string.format("\27[3%dmuser%d\27[0m: line %d of the log, a little longer than most",
                               1 + n % 6, n % 7, n)
  end
  ui.page("Term")
  local top, rows = ui.text_view("log", log, {rows = 9, follow = true, wrap = true})
  ui.text_view("input", {"> " .. typed}, {rows = 1, caret = {1, 3 + #typed}, syntax = "plain"})
  pmem(0, #log)
  pmem(1, top - 1)
  pmem(2, rows)
end
