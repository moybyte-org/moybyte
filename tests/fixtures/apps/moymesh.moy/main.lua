-- Moy Mesh: text messages on the LoRa radio; it works with Meshtastic. The
-- window lists what the service (service.lua) heard and sent on the default
-- channel, newest first, and sends what is typed; the service holds the radio
-- and keeps running when the window closes. The region picks the frequency
-- and must match the law where the console is: EU_868 until another is
-- chosen. The window is responsive: the ui verbs lay the page out in
-- whatever room `_layout` was given. A heard row names its sender and signal
-- before its text, so a row too long for the room loses the end of the
-- text, never the readout.

rows = {}
cur = 1
text = ""
region = 1
node = ""
room_w, room_h, room_fs = 320, 240, 1

function _layout(w, h, fs)
  room_w, room_h, room_fs = w, h, fs
  _reload()
end

function _reload()
  local log = prefs.get("log") or {}
  rows = {}
  for i = #log, 1, -1 do
    local m = log[i]
    local heard = m.from ~= nil and m.from ~= ""
    local line
    if heard then
      line = string.format("%s %d dBm %.1f dB: %s", m.from, m.rssi or 0, m.snr or 0,
                           m.text or "")
    else
      line = "me: " .. (m.text or "") .. (m.failed and " (not sent)" or "")
    end
    rows[#rows + 1] = line
  end
  local r = prefs.get("region")
  region = 1
  for i = 1, #REGIONS do
    if REGIONS[i] == r then region = i end
  end
  local n = prefs.get("node")
  node = type(n) == "string" and n or ""
end

function send(words)
  if words ~= "" then
    moybyte.send(ME, "send", words)
  end
end

function _init()
  moybyte.on("heard", _reload)
  moybyte.service_start()
  _reload()
end

function _draw()
  ui.page("")             -- the app bar and the window name it
  ui.hints({"confirm", "Send"})
  local enter
  text, enter = ui.field("Message", text, 200)
  if (enter or ui.button("Send", "primary")) and text ~= "" then
    send(text)
    text = ""
  end
  cur = ui.list("LongFast", #rows > 0 and rows or {"Nothing heard yet"}, cur)
  local new = ui.cycle("Region", REGIONS, region)
  if new ~= region then
    region = new
    prefs.set("region", REGIONS[region])
    moybyte.send(ME, "retune")
  end
  if node ~= "" then
    ui.info("This node", node)
  end
end
