
-- PERF VARIANT: start in room 22 (36 actors: spikes, clouds, gems, mossable
-- surfaces) instead of the home room. Moving the player and re-running the
-- cart's own Map_update is how the cart itself changes rooms.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  G_player.x, G_player.y = 804, 340
  Map_update()
end
