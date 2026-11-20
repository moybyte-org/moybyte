-- The data both Moy Mesh threads read, the hand-written one and its UI tree
-- port: the nodes heard, a channel's log of 40 messages over two days, and the
-- prefs and clock the console would hand Moy Mesh's window. No radio, no store.

NOW = 1763553600 + 12 * 3600          -- Wed 19 Nov 2025, noon UTC
NODES = {
  ["!0badf00d"] = { long = "Ana on the hill", rssi = -96, snr = 6.5, key = true },
  ["!c0ffee12"] = { long = "Marko", rssi = -118, snr = -9.25, key = true },
  ["!77aa0011"] = { rssi = -104, snr = 1.0 },            -- heard, no User yet: no key
}

local said = {
  "Morning all", "Anyone up on the ridge?", "Clear skies here",
  "Battery at 40%, solar is slow today", "Copy that",
  "Testing the new antenna, how do I come in?",
  "Loud and clear from the valley, about 7 km out",
  "Meet at the hut at 3?", "On my way", "Heading back down before the rain",
  "The relay on the tower restarted twice this morning. If your messages "
    .. "vanished between ten and eleven, that is why; it is back now and "
    .. "should stay up. Tell me if you still see gaps after lunch.",
}
local who = { "!0badf00d", "!c0ffee12", "" }

P = { node = "!a1b2c3d4", seen = {}, region = "EU_868", preset = "LONG_FAST",
      channels = { { name = "", psk = "AQ==" }, { name = "Friends", psk = "k3Yc9pQ2lWm1xZr8sT4uVw==" },
                   { name = "Hike", psk = "AA==" } },
      log = {}, nodes = {} }
for i = 1, 40 do
  local from = who[(i * 7) % 3 + 1]
  P.log[i] = { t = NOW - (40 - i) * 1500, from = from, text = said[(i * 5) % #said + 1], ch = 0,
               rssi = from ~= "" and NODES[from].rssi or nil, snr = from ~= "" and NODES[from].snr or nil,
               state = from == "" and "delivered" or nil }
end
for id, n in pairs(NODES) do
  P.nodes[id] = { long = n.long, key = n.key and "k3Yc9pQ2lWm1xZr8sT4uVw==" or nil, rssi = n.rssi,
                  snr = n.snr, heard = NOW - 600 }
end
prefs = { get = function(k) return P[k] end, set = function(k, v) P[k] = v end }

local base = moybyte
moybyte = setmetatable({
  on = function() end, every = function() end, service_start = function() end,
  clock = function(here) return NOW end,
  name = function() return "moybyte-7f3a" end,
  send = function() end,
}, { __index = base })

-- the radio's answers come a moment later, counted in frames
RADIO_DUE = {}
function radio_later(sec, fn) RADIO_DUE[#RADIO_DUE + 1] = { n = math.floor(sec * 30), fn = fn } end
function radio_tick()
  for i = #RADIO_DUE, 1, -1 do
    local t = RADIO_DUE[i]
    t.n = t.n - 1
    if t.n <= 0 then table.remove(RADIO_DUE, i); t.fn() end
  end
end
-- a message goes out "sending"; the first one fails, any resend arrives
RADIO_SENDS = 0
function radio_fate()
  RADIO_SENDS = RADIO_SENDS + 1
  return RADIO_SENDS == 1 and "failed" or "delivered"
end
