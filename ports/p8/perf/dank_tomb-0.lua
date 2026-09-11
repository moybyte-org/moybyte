
-- PERF VARIANT: start in dungeon room 5,3 (50 entities, the most of any room
-- that loads from above) instead of the title room 4,10. Clearing hf makes
-- dy() skip its relative step and load kr outright, which is exactly what
-- fz() does at boot.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  hf = nil
  kr = v(5, 3)
  dy(3)
end
