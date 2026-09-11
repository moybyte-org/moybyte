
-- PERF VARIANT: the intro auto-warps to the nearest system and lands on a
-- planet once `o` passes `ci` -- normally 320..420 update ticks of star field.
-- Zeroing ci takes the cart's own warp path on the first tick, so the run is
-- spent on the planet surface (state a=3) instead of the star map.
local __perf_init = p8_init
function p8_init()
  __perf_init()
  ci = 0
end
