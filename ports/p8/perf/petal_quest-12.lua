
-- PERF VARIANT: start in room 14 (the meadow with the bird flock, 30 actors
-- + 13 fx) instead of the title. Same three assignments the title screen's
-- "B to start" makes, with a mid-game room's coordinates.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  G_player.x, G_player.y, G_player.is_hidden = 800, 181, false
  G_dark = 30
  Map_update()
end
