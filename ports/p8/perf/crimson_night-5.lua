
-- PERF VARIANT: skip the two tutorial screens and their slide-in; this is the
-- exact pair of assignments p8_update makes when tutorial_slide reaches 1.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  tutorial_screen, tutorial_slide = 2, 1
  game_state = "playing"
  t = time
end

-- Same reason as bunnysurvivor's: a standing player dies, and the level-up
-- card picker waits for a button. Neither may end the measurement window.
local __perf_update = p8_update
function p8_update()
  player_health = 5
  game_over = false
  level_up_pending = false
  __perf_update()
end
