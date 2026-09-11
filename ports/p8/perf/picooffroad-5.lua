
-- PERF VARIANT: skip the title menu straight into a race on track 1 with the
-- full field, and let the cart's own AI drive the player car so a hands-off
-- run measures racing rather than an idle start line. 20 laps so the race
-- does not end into the results screen mid-measurement.
car_player = car_ai

local __perf_game_init = game_states[2].init
game_states[2].init = function()
  __perf_game_init()
  start_time = time()          -- drop the 3.5s pre-race countdown
end

local __perf_init = p8_init
function p8_init()
  __perf_init()
  tour = nil
  laps_count = 20
  cars_count = 4
  set_state(2)                 -- what the setup menu's "start!" does
end
