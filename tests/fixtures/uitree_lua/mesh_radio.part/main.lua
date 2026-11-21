-- Moy Mesh's Radio and Region pages (draw_radio and draw_region in
-- tests/fixtures/apps/moymesh.moy/main.lua) in the UI tree API.

ui.app("Moy Mesh", { icon = "bubble" })

local s = ui.state {
  long = "", short = "", console = "", auto = "", me = "",
  preset = "", region = "",
  chans = ui.model { key = "i", i = "", name = "", kind = "" },
  regions = ui.model { key = "i", i = "", place = "" },
  can_add = false,
}

local PLACES = {
  EU_868 = "Europe", EU_433 = "Europe 433", US = "United States", ANZ = "Australia, NZ",
  ANZ_433 = "Australia, NZ 433", CN = "China", JP = "Japan", RU = "Russia", KR = "Korea",
  TW = "Taiwan", IN = "India", NZ_865 = "New Zealand 865", TH = "Thailand",
  UA_433 = "Ukraine 433", UA_868 = "Ukraine 868", MY_433 = "Malaysia 433",
  MY_919 = "Malaysia 919", SG_923 = "Singapore", PH_433 = "Philippines 433",
  PH_868 = "Philippines 868", PH_915 = "Philippines 915", KZ_433 = "Kazakhstan 433",
  KZ_863 = "Kazakhstan 863", NP_865 = "Nepal", BR_902 = "Brazil",
}

local function key_kind(c, i)
  if c.psk == "" and i > 1 then return "Primary's key"
  elseif c.psk == "AA==" then return "No key"
  elseif #c.psk <= 4 then return "Default key" end
  local n = #(b64_bytes(c.psk) or "")
  return n == 16 and "128-bit key" or n == 32 and "256-bit key" or "Key"
end

local function console_name()
  local ok, n = pcall(moybyte.name)
  return ok and type(n) == "string" and n or ""
end

local function reload()
  local st = setup()
  local _, p = modem(st.region, st.preset)
  local own = prefs.get("long_name") or ""
  own = own ~= "" and own or console_name()
  s.console = console_name()
  s.long = own
  s.auto = (own == "" or own:sub(1, 7):lower() == "moybyte") and (prefs.get("node") or ""):sub(-4) or clip(own, 4)
  local short = prefs.get("short_name") or ""
  s.short = short ~= "" and short or s.auto
  s.me = type(prefs.get("node")) == "string" and "!" .. prefs.get("node"):sub(2):upper() or ""
  s.preset, s.region = MODEMS[p].title, st.region
  local rows = {}
  for i, c in ipairs(st.channels) do
    rows[i] = { i = "c" .. i, name = channel_name(st, i), kind = i == 1 and "Primary" or key_kind(c, i) }
  end
  s.chans:set(rows)
  rows = {}
  for i, r in ipairs(REGIONS) do rows[i] = { i = r, place = PLACES[r] or "" } end
  s.regions:set(rows)
  s.can_add = #st.channels < CHANNELS_MAX
end

ui.hook("opened", function() reload(); ui.push("radio") end)

ui.action("save_long", "Save", function()
  prefs.set("long_name", s.long ~= s.console and s.long or "")
  reload()
end)
ui.action("save_short", "Save", function()
  prefs.set("short_name", s.short ~= s.auto and s.short or "")
  reload()
end)
ui.action("open_preset", "Preset", function() end, { hint = "Edit" })
ui.action("open_region", "Region", function() ui.push("region") end, { hint = "Edit" })
ui.action("channel", "Channel", function(row) end, { takes = "row", hint = "Edit" })
ui.action("add", "Add channel", function() end, { hint = "Edit" })
ui.action("pick_region", "Choose", function(row)
  local st = setup()
  st.region = row.i
  setup_save(st)
  reload()
end, { takes = "row" })

ui.page("home", function() ui.label("Moy Mesh") end, { title = "" })

ui.page("radio", function()
  ui.scroll("radio", function()
    ui.group("This node", function()
      ui.field("long", { label = "Name", max = 39, hint = "{console}", commit = true, on = "save_long" })
      ui.field("short", { label = "Short name", max = 4, hint = "{auto}", commit = true, on = "save_short" })
      ui.info("Id", { value = "{me}", shown = "me" })
    end)
    ui.group("Mesh", function()
      ui.link("open_preset", { value = "{preset}", focus = true })
      ui.link("open_region", { value = "{region}" })
    end)
    ui.group("Channels", function()
      ui.rows("chans", function() ui.link("channel", { label = "{.name}", value = "{.kind}" }) end)
      ui.link("add", { icon = "plus", value = "", tone = "value", shown = "can_add" })
    end)
  end)
end, { title = "Radio" })

ui.page("region", function()
  ui.scroll("regions", function()
    ui.group("Region", function()
      ui.rows("regions", { current = "region", check = true }, function()
        ui.link("pick_region", { label = "{.i}", value = "{.place}", focus = true })
      end)
    end)
  end)
end, { title = "Region" })
