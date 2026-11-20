-- The port's radio, over data.lua: the same fates and delays as the
-- hand-written thread's, answering the app by posts from its own clock (the
-- driver's callback), which runs once a frame before the app's.
function radio_send(text, dm)
  local r = { t = NOW, from = "", text = text, state = "sending" }
  if dm then r.dm = dm else r.ch = 0 end
  P.log[#P.log + 1] = r
  local i = #P.log
  local fate = radio_fate()
  radio_later(1.2, function() r.state = fate; ui.post("answered", i) end)
  return i
end

function radio_resend(i)
  P.log[i].state = "sending"
  radio_later(1.0, function() P.log[i].state = "delivered"; ui.post("answered", i) end)
end

function radio_ask(peer)
  P.nodes[peer].asked = NOW
  radio_later(1.5, function()
    P.nodes[peer].long = "Iva"; P.nodes[peer].key = "k3Yc9pQ2lWm1xZr8sT4uVw=="
    ui.post("key_heard", peer)
  end)
end

local draw = _draw
function _draw()
  radio_tick()
  draw()
end
