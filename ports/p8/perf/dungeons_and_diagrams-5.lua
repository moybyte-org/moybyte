
-- PERF VARIANT: skip the PLAY/TUTORIAL menu into the board, on a puzzle from
-- the middle of the set rather than the first one. launch_game() is what
-- "PLAY" calls; the reload is puzzle_select's own.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  launch_game()
  puzzle_id = flr(puzzle_id_max / 2)
  gboard = board:new(puzzle_id)
  gboard:load()
  update_menu_item()
end
