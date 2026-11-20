-- Moy Mesh's thread (draw_thread, draw_message and their helpers in
-- tests/fixtures/apps/moymesh.moy/main.lua) in the UI tree API. The pages
-- describe; the handlers shape what the service keeps into rows and answer
-- what the radio posts.

ui.app("Moy Mesh", { icon = "bubble" })

local STATE = { "sending", "sent", "failed", "delivered", { init = "sent" } }
local s = ui.state {
  t = ui.record {
    kind = ui.enum("ch", "dm", { init = "ch" }), name = "", label = "", short = "",
    first = "", first_icon = "", empty = "", empty2 = "", hint = "",
    public = ui.opt(false), keystate = ui.opt(ui.enum("known", "none", "changed", "waiting", { init = "known" })),
    bars = ui.opt(0),
  },
  msgs = ui.model {
    key = "i", i = "", kind = ui.enum("day", "msg", "failed", { init = "msg" }), text = "",
    n = ui.opt(0), side = ui.opt(ui.enum("in", "out", { init = "in" })), head = ui.opt(""), bars = ui.opt(0),
    state = ui.opt(ui.enum(table.unpack(STATE))),
  },
  draft = "",
  pick = ui.opt(ui.record {
    i = 0, dir = ui.enum("in", "out", { init = "in" }), status = "", who = ui.opt(""), name = ui.opt(""),
    rssi = ui.opt(0), snr = ui.opt(0.0), bars = ui.opt(0), failed = false,
  }),
  asked = false,
  msg = ui.opt(ui.row_of("msgs")),
}
ui.derive("readonly", "bool", "t.kind=dm && (t.keystate=waiting || t.keystate=changed)")
ui.labels("STATUS", { sending = "Sending\u{2026}", sent = "Sending\u{2026}", failed = "Not sent", delivered = "" })
ui.labels("KEYSTATE", { known = "Encrypted", none = "Not private", changed = "Key changed",
                        waiting = "Waiting for key" })
ui.labels("KEYICON", { known = "lock", waiting = "key", none = "warn", changed = "warn" })

-- -- what the service keeps, read (main.lua's helpers, as the window has them) --

log, nodes, setup_now, now, zone, synced = {}, {}, nil, 0, 0, false
local key = "ch0"

local function upper_id(id) return "!" .. id:sub(2):upper() end
function short_id(id) return id:sub(-4):upper() end
function name_of(id)
  local n = nodes[id]
  if n and type(n.long) == "string" and n.long ~= "" then return n.long end
  return short_id(id)
end
local function key_state(id)
  local n = nodes[id]
  if n == nil or n.long == nil then return n and n.key and "known" or "waiting" end
  if n.key_new then return "changed" end
  return n.key and "known" or "none"
end
function bars(rssi, snr)
  if rssi == nil then return nil end
  local r = rssi >= -100 and 4 or rssi >= -110 and 3 or rssi >= -120 and 2 or rssi > -130 and 1 or 0
  local m = modem(setup_now.region, setup_now.preset)
  local q_ = (snr or 0) + (-17.5 - (({ [7] = -7.5, [8] = -10, [9] = -12.5, [10] = -15, [11] = -17.5,
                                       [12] = -20 })[m.sf] or -17.5))
  local q = q_ >= 5 and 4 or q_ >= 0 and 3 or q_ >= -7 and 2 or q_ >= -15 and 1 or 0
  return math.min(r, q)
end
local MONTHS = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }
local DAYS = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" }
local function day_of(t) return (t + zone) // 86400 end
local function day_head(t)
  local d, today = day_of(t), day_of(now)
  if d == today then return "Today" elseif d == today - 1 then return "Yesterday" end
  local z = d + 719468
  local era = z // 146097
  local doe = z - era * 146097
  local yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
  local doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
  local mp = (5 * doy + 2) // 153
  local dd = doy - (153 * mp + 2) // 5 + 1
  local mm = mp < 10 and mp + 3 or mp - 9
  return string.format("%s %d %s", DAYS[(d + 3) % 7 + 1], dd, MONTHS[mm])
end
local function public(c)
  local psk = c.psk
  return psk == "" or psk == DEFAULT_PSK or psk == "AA==" or (#psk <= 4 and psk ~= "")
end
local function failed(r) return r.failed or r.state == "failed" end
local function mine(r)
  if key:sub(1, 1) == "!" then return r.dm == key end
  return r.dm == nil and not r.old and "ch" .. (r.ch or 0) == key
end

-- -- shaping: the header and the rows ------------------------------------------

local function header()
  if key:sub(1, 1) ~= "!" then
    local ci = tonumber(key:sub(3))
    local name = channel_name(setup_now, ci + 1)
    local _, p = modem(setup_now.region, setup_now.preset)
    local pub = public(setup_now.channels[ci + 1])
    return { kind = "ch", name = name, short = "", public = pub,
             label = name ~= MODEMS[p].title and name .. " \u{b7} " .. MODEMS[p].title or name,
             first = pub and "Public: anyone who knows the name can read it." or "", first_icon = "bubble",
             empty = "Messages on " .. name .. " show up here.",
             empty2 = pub and "Public: anyone who knows the name can read it." or "",
             hint = "Message " .. name }
  end
  local k, who, n = key_state(key), name_of(key), nodes[key] or {}
  local first = "Only " .. who .. "'s radio can read these."
  local icon = "lock"
  if k == "none" then
    first, icon = "Not private: others on " .. channel_name(setup_now, 1) .. " can read this.", "warn"
  end
  return { kind = "dm", name = who, short = short_id(key), keystate = k, bars = bars(n.rssi, n.snr),
           label = who == short_id(key) and who or who .. "  " .. short_id(key),
           first = first, first_icon = icon, empty = first, empty2 = "",
           hint = k == "waiting" and "Waiting for their key" or k == "changed" and "Accept the new key to write"
                  or "Message " .. who }
end

local function msg_row(i, r)
  local row = { i = "m" .. i, n = i, kind = r.from == "" and failed(r) and "failed" or "msg", text = r.text or "",
                side = r.from == "" and "out" or "in" }
  if r.from == "" then
    if r.state == "sending" or r.state == "sent" then row.state = r.state end
  else
    if key:sub(1, 1) ~= "!" then row.head = name_of(r.from) end
    row.bars = bars(r.rssi, r.snr)
  end
  return row
end

local function reload()
  local l = prefs.get("log")
  log = type(l) == "table" and l or {}
  local n = prefs.get("nodes")
  nodes = type(n) == "table" and n or {}
  setup_now = setup()
  now, zone, synced = wall()
  s.t = header()
  local mine_rows, first_t = {}, nil
  for i, r in ipairs(log) do
    if mine(r) then
      first_t = first_t or r.t or now
      mine_rows[#mine_rows + 1] = i
    end
  end
  local many = synced and #mine_rows > 0 and day_of(first_t) ~= day_of(now)
  local rows, day = {}, nil
  for _, i in ipairs(mine_rows) do
    local r = log[i]
    if many and r.t and day_of(r.t) ~= day then
      day = day_of(r.t)
      rows[#rows + 1] = { i = "d" .. day, kind = "day", text = day_head(r.t) }
    end
    rows[#rows + 1] = msg_row(i, r)
  end
  s.msgs:set(rows)
  s.asked = key:sub(1, 1) == "!" and nodes[key] ~= nil and nodes[key].asked ~= nil
end

local function details(row)
  local r = log[row.n]
  s.msg = row
  if r.from ~= "" then
    local who = name_of(r.from)
    s.pick = { i = row.n, dir = "in", status = "", failed = false, name = who, rssi = r.rssi or 0, snr = r.snr or 0.0,
               bars = bars(r.rssi, r.snr) or 0,
               who = who == short_id(r.from) and upper_id(r.from) or who .. " \u{b7} " .. upper_id(r.from) }
  else
    s.pick = { i = row.n, dir = "out", failed = failed(r),
               status = failed(r) and "Not sent" or r.state == "delivered" and "Delivered"
                        or (r.state == "sending" or r.state == "sent") and key:sub(1, 1) == "!" and "Sending" or "Sent" }
  end
end

-- -- actions and hooks ---------------------------------------------------------

ui.hook("opened", function(arg)
  key = arg ~= "" and arg or "ch0"
  reload()
  ui.push("thread")
end, { takes = "arg" })
ui.hook("answered", function(i) reload() end, { takes = "arg" })
ui.hook("key_heard", function(peer) reload() end, { takes = "arg" })

ui.action("send", "Send", function()
  if s.draft ~= "" then radio_send(s.draft, key:sub(1, 1) == "!" and key or nil) end
  s.draft = ""
  reload()
end, { icon = "arrow_up", when = "!readonly", off = "grey" })
ui.action("resend", "Send again", function(row)
  radio_resend(row.n)
  s.pick = nil
  reload()
end, { row = "msg", off = "hide" })
ui.action("details", "Details", details, { takes = "row", keys = { "role:more" } })
ui.action("open_message", "Open", function(row) details(row); ui.push("message", row) end, { takes = "row" })
ui.action("delete", "Delete", function(row) s.pick = nil; reload() end, { row = "msg", off = "hide" })
ui.action("ask", "Ask for their key", function() s.asked = true; radio_ask(key) end, { hint = "Ask for key" })
ui.action("accept", "Accept", function() nodes[key].key_new = nil; reload() end)
ui.action("notnow", "Not now", function() ui.pop() end)
ui.action("channel", "Channel", function() end)
ui.action("message_sender", "Message {pick.name}", function() end, { hint = "Message" })

-- -- pages ---------------------------------------------------------------------

local function home()
  ui.label("Moy Mesh")
end

local function thread()
  ui.switch("t.kind", {
    dm = function()
      ui.info("{t.label}", { value = "{t.keystate|map KEYSTATE}", icon = "{t.keystate|map KEYICON}",
                             signal = "{t.bars}" })
    end,
    ch = function()
      ui.link("channel", { label = "{t.label}", value = "", tag = "{t.public?Public:}", icon = "{t.public?:lock}" })
    end,
  })
  ui.scroll({ follow = true }, function()
    ui.label("{t.first}", { icon = "{t.first_icon}", shown = "t.first && msgs" })
    ui.rows("msgs", { by = "kind" }, {
      day = function() ui.label("{.text}") end,
      msg = function()
        ui.bubble("{.text}", { side = "{.side}", head = "{.head}", signal = "{.bars}", status = "{.state|map STATUS}",
                               on = "details", cut = "open_message" })
      end,
      failed = function()
        ui.bubble("{.text}", { side = "out", tone = "failed", status = "Not sent", on = "resend" })
      end,
    })
    ui.empty("No messages yet", { icon = "bubble", lines = { "{t.empty}", "{t.empty2}" }, shown = "!msgs && !readonly" })
    ui.empty("Waiting for {t.name}'s key", {
      icon = "key", shown = "t.keystate=waiting",
      lines = { "Only {t.short}'s radio will be able to read it.",
                "{@tall?Its key comes with its name;:Its key comes with its name.}",
                "{@tall?you can write once it's heard.:}" },
    }, function()
      ui.button("ask", { kind = "primary", focus = true, shown = "!asked" })
      ui.label("Asked \u{b7} just now", { icon = "check", shown = "asked" })
    end)
  end)
  ui.foot(function()
    ui.note({ icon = "warn", lines = { "{t.name}'s key changed.",
                                       "This can mean a reset radio, or someone pretending. Accept the new key?" },
              shown = "t.keystate=changed" })
    ui.row("decide", { shown = "t.keystate=changed" }, function()
      ui.button("notnow", { kind = "primary", focus = true })
      ui.button("accept")
    end)
    ui.row("write", function()
      ui.field("draft", { label = "", max = 200, hint = "{t.hint}", grow = true, focus = "!readonly",
                          on = "send", when = "!readonly", off = "grey" })
      ui.button("send", { label = "", kind = "primary" })
    end)
  end)
  ui.popover("details", { shown = "pick", anchor = "m{pick.i}", wash = "view" }, function()
    ui.switch("pick.dir", {
      ["in"] = function()
        ui.info("From", { value = "{pick.who}" })
        ui.info("Signal", { value = "{pick.rssi} dBm \u{b7} SNR {pick.snr|fmt %.1f} dB", signal = "{pick.bars}" })
        ui.button("message_sender", { kind = "primary" })
      end,
      out = function()
        ui.info("{t.kind=dm?To:Channel}", { value = "{t.name}" })
        ui.info("Status", { value = "{pick.status}", tone = "{pick.failed?danger:}" })
        ui.button("delete", { kind = "danger", shown = "pick.failed" })
        ui.button("resend", { kind = "primary", shown = "pick.failed", focus = true })
      end,
    })
  end)
end

local function message()
  ui.scroll(function()
    ui.bubble("{msg.text}", { max = 1, lines = 0, head = "{msg.head}", signal = "{msg.bars}" })
    ui.switch("pick.dir", {
      ["in"] = function()
        ui.group("Heard", function()
          ui.info("From", { value = "{pick.who}" })
          ui.info("Signal", { value = "{pick.rssi} dBm", signal = "{pick.bars}" })
          ui.info("SNR", { value = "{pick.snr|fmt %.1f} dB" })
        end)
      end,
      out = function()
        ui.group("Sent", function()
          ui.info("{t.kind=dm?To:Channel}", { value = "{t.name}" })
          ui.info("Status", { value = "{pick.status}", tone = "{pick.failed?danger:}" })
        end)
      end,
    })
  end)
end

ui.page("home", home, { title = "" })
ui.page("thread", thread, { title = "" })
ui.page("message", message, { title = "Message", arg = "msg" })
