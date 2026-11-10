-- The ui parts an app composes a chat from (docs/ui_api_2026-10.md), one
-- page each, as a Lua app: parts_py.moy is the same app in Python, and
-- tests/test_app_ui_parts.py holds the tiers to the same pixels. pmem 0
-- names the page the test asks for.

function _layout(w, h, fs) end

function _draw()
  local page = pmem(0)
  local px, py, pw, ph = ui.page("Parts")
  if page == 1 then
    ui.empty("bubble", "No messages yet", {"Say hello to the mesh", "Messages show here"})
    ui.button("Write one", "primary")
  elseif page == 2 then
    ui.bubble("See you at the top", "in", {head = "Ana Ridge", signal = 3})
    ui.bubble("Ten minutes", "out", {status = "Sending\u{2026}"})
    ui.bubble("Wait for me at the gate", "out",
              {tone = "failed", status = "Not sent", id = "gate"})
    local _, _, _, _, st = ui.bubble("Clear skies up here, wind from the north. Bring a warm layer, the hut is cold after dark and the stove is out. Bring tea for the morning, and the long rope from the shed by the gate.", "in", {head = "1E2F", signal = 1, anchor = true, max = 0.5, lines = 3})
    pmem(1, st & ui.CUT)
    ui.focus("gate")
  elseif page == 3 then
    ui.group("Heard")
    ui.info("From", "Ana Ridge")
    ui.info("Signal", "-104 dBm", {signal = 2})
    ui.info("SNR", "2.0 dB")
    ui.group()
    ui.link("Base Camp", "-96 dBm", {signal = 4})
  elseif page == 4 then
    local x, y, w, h = ui.bubble("Wait for me at the gate", "out",
                                 {tone = "failed", status = "Not sent", anchor = true})
    ui.at(x, y, w, h)
    ui.popover("details", pw - 16)
    ui.info("Channel", "LongFast")
    ui.info("Status", "Not sent")
    ui.button("Delete", "danger")
    ui.button("Send again", "primary")
    ui.popover()
    ui.focus("Send again")
  elseif page == 6 then
    ui.label("LongFast")
    ui.scroll("history")
    ui.bubble("See you at the top", "in", {head = "Ana Ridge", signal = 3})
    local x, y, w, h = ui.bubble("Wait for me at the gate", "out",
                                 {tone = "failed", status = "Not sent", anchor = true})
    ui.at(x, y, w, h)
    ui.scroll()
    ui.popover("details", pw - 16, {wash = "view"})
    ui.info("Status", "Not sent")
    ui.button("Send again", "primary")
    ui.popover()
    ui.focus("Send again")
  elseif page == 5 then
    ui.list("Region", {"EU_868", "US", "CN"}, 2,
            {values = {"Europe", "United States", "China"}, check = true})
  elseif page == 7 then
    ui.group("", {card = true})
    ui.list("", {"Srbija", "Planinari", "Ana Ridge", "Base Camp"}, -1,
            {values = {"2 min", "20 min", "4 min", "1 h"},
             subs = {"Base Camp: Gate opens at seven", "Milo's Deck: Snow above the hut",
                     "Bring the spare headlamp, mine is dead", "You: Back by noon"},
             icons = {"bubble", "bubble", "@Ana Ridge", "@base camp"},
             counts = {0, 3, "2", ""}, tags = {"Public", "", "", ""},
             marks = {"", "lock", "lock", "lock"}})
    ui.group()
    ui.link("Nodes", "4 heard")
  end
end
