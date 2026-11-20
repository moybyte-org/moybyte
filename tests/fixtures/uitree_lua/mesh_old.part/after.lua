-- Open the hand-written window on the thread its port opens on, over a home,
-- and tick the radio a frame at a time.
stack = { { kind = "home" }, { kind = "thread", key = rawget(_G, "UI_LAUNCH") or "ch0" } }
fresh = true
_reload()
local draw = _draw
function _draw()
  radio_tick()
  draw()
end
