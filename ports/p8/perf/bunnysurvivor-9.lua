
-- PERF VARIANT: boot straight into the survival arena instead of the title
-- manual. start_game() is what killing the title skull does; the two lines
-- after it are the end of draw_manual's intro countdown.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  start_game()
  starting, starting_time = false, 0
  start = false
  setup_enemy_waves()
end

-- A hands-off run has nobody dodging, so the bunny dies in ~14s and the rest
-- of the measurement is a game-over screen. Keep it alive and out of the
-- level-up picker, which also waits for a button that is never pressed.
local __perf_update = p8_update60
function p8_update60()
  playerhp = playerhp_max
  show_lvlup = false
  __perf_update()
end
