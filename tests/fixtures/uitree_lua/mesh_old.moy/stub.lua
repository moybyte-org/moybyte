-- The hand-written thread's radio (Moy Mesh's main.lua and mesh.lua, as
-- tests/fixtures/apps/moymesh.moy has them): the service's send, over data.lua.
moybyte.send = function(_, what, a, b)
  if what == "send" then
    local r = { t = NOW, from = "", text = a, state = "sending" }
    if type(b) == "string" then r.dm = b else r.ch = b end
    P.log[#P.log + 1] = r
    local fate = radio_fate()
    radio_later(1.2, function() r.state = fate; _reload() end)
  elseif what == "act" and a == "resend" then
    P.log[b].state = "sending"
    radio_later(1.0, function() P.log[b].state = "delivered"; _reload() end)
  elseif what == "act" and a == "ask" then
    P.nodes[b].asked = NOW
    radio_later(1.5, function() P.nodes[b].long = "Iva"; P.nodes[b].key = "k3Yc9pQ2lWm1xZr8sT4uVw=="; _reload() end)
  end
  _reload()
end
