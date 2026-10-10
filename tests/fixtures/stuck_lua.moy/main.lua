-- Stuck Lua: a cart whose third frame never ends, for the runaway watch
-- (native/moy_play/moy_play.h). The watch ends it on line 8.
n = 0
function _update()
  n = n + 1
  if n > 2 then
    while true do
      n = n + 1
    end
  end
end
function _draw() cls(1) end
