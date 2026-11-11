-- Moy Mesh's window: the conversations on the LoRa radio, Meshtastic's
-- channels and direct messages, over the service (service.lua) that holds
-- the radio and keeps running when the window closes. Its contract with the
-- service is service.lua's header (the prefs it keeps, the messages both
-- ways); the setup and the channel links are mesh.lua's.
--
-- THE PAGES, a stack the bar's way back pops (ui.bar's back):
--   home      every conversation: the channels, the replaced channels kept
--             read-only, then the direct threads newest first; then Nodes
--             and Radio
--   thread    one conversation: its channel row (a direct thread's header),
--             the history (bubbles on a snapping, following scroll), the
--             field and its send key; a bubble's details hang in a popover,
--             a cut message's are the message page
--   message   a cut message whole, and how it was heard
--   nodes     the nodes heard, newest first: a row opens its direct thread
--   radio     This node (the names), Mesh (preset, region), Channels
--   region, preset   the choices, the current one checked; a preset asks
--   channel   a channel's name and key, its link, its removal
--   add       a channel from a link (typed, pasted, or offered from the
--             console's clipboard) or by name
--   link      what a link sets, before it is applied
--
-- One layout on every board: `_layout` gives the room, and the page reads
-- its row height from where its first row ended (ui.split), so the gutter,
-- the field and the history follow the board's class (20 px rows on the
-- handheld, 36 under the tap floor, 28 on the desk).

PLACES = {
  EU_868 = "Europe", EU_433 = "Europe 433", US = "United States", ANZ = "Australia, NZ",
  ANZ_433 = "Australia, NZ 433", CN = "China", JP = "Japan", RU = "Russia", KR = "Korea",
  TW = "Taiwan", IN = "India", NZ_865 = "New Zealand 865", TH = "Thailand",
  UA_433 = "Ukraine 433", UA_868 = "Ukraine 868", MY_433 = "Malaysia 433",
  MY_919 = "Malaysia 919", SG_923 = "Singapore", PH_433 = "Philippines 433",
  PH_868 = "Philippines 868", PH_915 = "Philippines 915", KZ_433 = "Kazakhstan 433",
  KZ_863 = "Kazakhstan 863", NP_865 = "Nepal", BR_902 = "Brazil",
}
HINTS = {
  LONG_FAST = "far \u{b7} default", LONG_MODERATE = "farther, slower",
  LONG_SLOW = "farthest, slowest", LONG_TURBO = "far, quicker",
  MEDIUM_SLOW = "middle, steadier", MEDIUM_FAST = "middle, quick",
  SHORT_SLOW = "short range", SHORT_FAST = "fast, shorter range",
  SHORT_TURBO = "fastest, shortest",
}
-- LoRa's demodulation floor by spreading factor (dB), for the signal's bars
FLOOR = { [7] = -7.5, [8] = -10, [9] = -12.5, [10] = -15, [11] = -17.5, [12] = -20 }
ASK_AGAIN = 60                  -- seconds before Ask for their key returns
TEXT_MAX = 200
LINK_MAX = 255

room_w, room_h, room_fs = 320, 240, 1
log, nodes, threads, order = {}, {}, {}, {}
setup_now = nil
me = ""
now, zone, synced = 0, 0, false
stack = { { kind = "home" } }
drafts = {}                     -- a thread's unsent text, by its key
fresh = true                    -- the page on top opened this frame
rowh = 20

function _layout(w, h, fs)
  room_w, room_h, room_fs = w, h, fs
end

-- -- what the service keeps, read ----------------------------------------------

local function upper_id(id)
  return "!" .. id:sub(2):upper()
end

function short_id(id)
  return id:sub(-4):upper()
end

function name_of(id)
  local n = nodes[id]
  if n and type(n.long) == "string" and n.long ~= "" then
    return n.long
  end
  return short_id(id)
end

-- How a node's key stands: "known", "none" (its User came with none),
-- "waiting" (no User heard), "changed" (it announced another).
function key_state(id)
  local n = nodes[id]
  if n == nil or n.long == nil then
    return n and n.key and "known" or "waiting"
  end
  if n.key_new then
    return "changed"
  end
  return n.key and "known" or "none"
end

function bars(rssi, snr)
  if rssi == nil then
    return nil
  end
  local r = rssi >= -100 and 4 or rssi >= -110 and 3 or rssi >= -120 and 2
            or rssi > -130 and 1 or 0
  local m = modem(setup_now.region, setup_now.preset)
  local s = (snr or 0) + (-17.5 - (FLOOR[m.sf] or -17.5))
  local q = s >= 5 and 4 or s >= 0 and 3 or s >= -7 and 2 or s >= -15 and 1 or 0
  return math.min(r, q)
end

function age(t)
  if type(t) ~= "number" then
    return ""
  end
  local d = now - t
  if d < 60 then
    return "now"
  elseif d < 3600 then
    return string.format("%d min", d // 60)
  elseif d < 86400 then
    return string.format("%d h", d // 3600)
  end
  return string.format("%d d", d // 86400)
end

local MONTHS = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                 "Nov", "Dec" }
local DAYS = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" }

-- The local day `t` falls on, counted from 1970-01-01.
local function day_of(t)
  return (t + zone) // 86400
end

-- A day's head: "Today", "Yesterday", else "Mon 9 Nov".
function day_head(t)
  local d, today = day_of(t), day_of(now)
  if d == today then
    return "Today"
  elseif d == today - 1 then
    return "Yesterday"
  end
  -- the civil date of day d (Howard Hinnant's days_from_civil, inverted)
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

function key_kind(c, i)
  if c.psk == "" and i > 1 then
    return "Primary's key"
  elseif c.psk == "AA==" then
    return "No key"
  elseif #c.psk <= 4 then
    return "Default key"
  end
  local n = #(b64_bytes(c.psk) or "")
  return n == 16 and "128-bit key" or n == 32 and "256-bit key" or "Key"
end

local function preset_title(s)
  local _, p = modem(s.region, s.preset)
  return MODEMS[p].title
end

local function thread_of(key)
  local t = threads[key]
  if t == nil then
    t = { key = key, msgs = {}, last = 0, unread = 0 }
    threads[key] = t
  end
  return t
end

function _reload()
  local l = prefs.get("log")
  log = type(l) == "table" and l or {}
  local n = prefs.get("nodes")
  nodes = type(n) == "table" and n or {}
  setup_now = setup()
  local id = prefs.get("node")
  me = type(id) == "string" and id or ""
  now, zone, synced = wall()
  local seen = prefs.get("seen")
  seen = type(seen) == "table" and seen or {}
  threads, order = {}, {}
  for i = 1, #setup_now.channels do
    local t = thread_of("ch" .. (i - 1))
    t.kind, t.ci, t.name = "ch", i - 1, channel_name(setup_now, i)
    t.public = public(setup_now.channels[i])
    order[#order + 1] = t
  end
  local olds, dms = {}, {}
  for i, r in ipairs(log) do
    local t
    if r.dm then
      t = thread_of(r.dm)
      if t.kind == nil then
        t.kind, t.peer = "dm", r.dm
        dms[#dms + 1] = t
      end
    elseif r.old then
      t = thread_of("old:" .. r.old)
      if t.kind == nil then
        t.kind, t.name, t.old = "old", r.old, true
        olds[#olds + 1] = t
      end
    else
      t = threads["ch" .. (r.ch or 0)]
    end
    if t then
      t.msgs[#t.msgs + 1] = { i = i, r = r }
      t.last = math.max(t.last, r.t or 0)
      if r.from ~= "" and (r.t or 0) > (seen[t.key] or 0) then
        t.unread = t.unread + 1
      end
    end
  end
  -- a direct thread opened from a node with nothing in it yet
  for _, p in ipairs(stack) do
    if p.kind == "thread" and p.key:sub(1, 1) == "!" and threads[p.key] == nil then
      local t = thread_of(p.key)
      t.kind, t.peer = "dm", p.key
      dms[#dms + 1] = t
    end
  end
  table.sort(dms, function(a, b) return a.last > b.last end)
  for _, t in ipairs(olds) do order[#order + 1] = t end
  for _, t in ipairs(dms) do
    t.name = name_of(t.peer)
    order[#order + 1] = t
  end
  local top = stack[#stack]
  if top.kind == "thread" and threads[top.key] and threads[top.key].unread > 0 then
    seen[top.key] = threads[top.key].last
    threads[top.key].unread = 0
    prefs.set("seen", seen)
  end
end

function _init()
  moybyte.on("heard", _reload)
  moybyte.on("nodes", _reload)
  moybyte.every(30000, function() now, zone, synced = wall() end)
  moybyte.service_start()
  _reload()
end

-- -- the page stack ----------------------------------------------------------------

function push(p)
  stack[#stack + 1] = p
  fresh = true
  _reload()
end

function pop()
  if #stack > 1 then
    local was = stack[#stack]
    stack[#stack] = nil
    fresh = true
    stack[#stack].back_from = was
  end
end

function open_thread(key)
  push({ kind = "thread", key = key })
end

local function retune(s)
  setup_save(s)
  moybyte.send(ME, "retune")
  _reload()
end

-- -- the thread -----------------------------------------------------------------------

-- The send target the service takes: a channel's index or a node's id.
local function target(t)
  return t.kind == "dm" and t.peer or t.ci
end

local function failed(r)
  return r.failed or r.state == "failed"
end

local function bubble_of(t, m)
  local r = m.r
  local o = { id = "m" .. m.i }
  local side = "in"
  if r.from == "" then
    side = "out"
    if failed(r) then
      o.tone, o.status = "failed", "Not sent"
    elseif r.state == "sending" or r.state == "sent" then
      o.status = "Sending\u{2026}"
    end
  else
    if t.kind ~= "dm" then
      o.head = name_of(r.from)
    end
    o.signal = bars(r.rssi, r.snr)
  end
  return side, o
end

-- The first line of a thread: who can read it.
local function first_line(t)
  if t.kind == "dm" then
    local k = key_state(t.peer)
    if k == "none" then
      local c = setup_now.channels[(nodes[t.peer] and nodes[t.peer].ch or 0) + 1]
      return "warn", "Not private: others on " .. (c and channel_name(setup_now, 1) or "the channel")
        .. " can read this."
    end
    return "lock", "Only " .. name_of(t.peer) .. "'s radio can read these."
  end
  if t.public then
    return "bubble", "Public: anyone who knows the name can read it."
  end
  return nil
end

-- A message's details: the popover's two rows, or with `whole` the message
-- page's, Signal and SNR each a row.
local function details_rows(t, m, whole)
  local r = m.r
  if r.from ~= "" then
    local who = name_of(r.from)
    ui.info("From", who == short_id(r.from) and upper_id(r.from)
                    or who .. " \u{b7} " .. upper_id(r.from))
    if whole then
      ui.info("Signal", string.format("%d dBm", r.rssi or 0), { signal = bars(r.rssi, r.snr) or 0 })
      ui.info("SNR", string.format("%.1f dB", r.snr or 0))
    else
      ui.info("Signal", string.format("%d dBm \u{b7} SNR %.1f dB", r.rssi or 0, r.snr or 0),
              { signal = bars(r.rssi, r.snr) or 0 })
    end
    return
  end
  if t.kind == "dm" then
    ui.info("To", name_of(t.peer))
  else
    ui.info("Channel", t.name)
  end
  local st = failed(r) and "Not sent" or r.state == "delivered" and "Delivered"
             or (r.state == "sending" or r.state == "sent") and t.kind == "dm" and "Sending"
             or "Sent"
  ui.info("Status", st, failed(r) and { ink = "danger" } or nil)
end

-- The details' answer: "message" (Message the sender), "resend", "delete",
-- or nil.
local function details_actions(t, m)
  local r = m.r
  if r.from ~= "" then
    if ui.button("Message " .. name_of(r.from), "primary") then
      return "message"
    end
  elseif failed(r) then
    local del = ui.button("Delete", "danger")
    local again = ui.button("Send again", "primary")
    return del and "delete" or again and "resend" or nil
  end
  return nil
end

local function act_on(t, m, what)
  if what == "message" then
    open_thread(m.r.from)
  elseif what == "delete" or what == "resend" then
    moybyte.send(ME, "act", what, m.i, m.r.text)
  end
end

local function send_text(p, t, text)
  if text ~= "" then
    moybyte.send(ME, "send", text, target(t))
  end
  drafts[p.key] = ""
end

local function focused_msg(t, f)
  local i = f and math.tointeger(tonumber(f:match("^m(%d+)$")))
  if i == nil then
    return nil
  end
  for _, m in ipairs(t.msgs) do
    if m.i == i then
      return m
    end
  end
  return nil
end

local function thread_hints(p, t, f)
  if p.pop then
    local r = p.pop.r
    if r.from ~= "" then
      return { "confirm", "Message", "back", "Close" }
    elseif failed(r) then
      return { "confirm", "Send again", "back", "Close" }
    end
    return { "back", "Close" }
  end
  local k = t.kind == "dm" and key_state(t.peer)
  if k == "waiting" then
    local n = nodes[t.peer]
    if n and n.asked and now - n.asked < ASK_AGAIN then
      return { "back", "Back" }
    end
    return { "confirm", "Ask for key", "back", "Back" }
  elseif k == "changed" then
    return { "confirm", "Not now", "back", "Back" }
  end
  if f == "chan" then
    return { "confirm", "Channel", "back", "Back" }
  end
  local m = focused_msg(t, f)
  if m then
    if m.r.from == "" and failed(m.r) then
      return { "confirm", "Send again", "more", "Details" }
    end
    return { "confirm", "Details" }
  end
  if (drafts[p.key] or "") ~= "" then
    return { "confirm", "Send", "cancel", "Back" }
  end
  return { "cancel", "Back" }
end

-- Whether the press that activated a stop this frame was a key's.
local function by_key()
  return btnp("run") or btnp("a")
end

-- The page's numbers for its row height `row` (20 px at the scale on the
-- handheld, 28 on the desk, 36 under the tap floor): the gutter, the field's
-- and the send key's size, the gap between the zones, and the inset the
-- toolkit lays a placed field's box in (a row's pad; a group's on the desk).
function metrics(row)
  local fs = room_fs
  rowh = row
  if row >= 36 then
    return { g = 8 * fs, field = row, send = 40 * fs, zgap = 4 * fs, inset = 10 * fs, desk = false }
  elseif row >= 28 * fs then
    return { g = 12 * fs, field = 30 * fs, send = 36 * fs, zgap = 6 * fs, inset = 12 * fs, desk = true }
  end
  return { g = 8 * fs, field = 22 * fs, send = 28 * fs, zgap = 2 * fs, inset = 10 * fs, desk = false }
end

-- A field whose box lies at x, y, w, h: the toolkit insets a placed field's
-- box by the row's pad across and its ring's room down.
local function field_at(L, x, y, w, h)
  ui.at(x - L.inset, y - 2 * room_fs, w + 2 * L.inset, h + 4 * room_fs)
end

local function draw_thread(p)
  local t = threads[p.key]
  if t == nil then
    pop()
    return
  end
  local f = ui.focus()
  local x, y, w, h = ui.page("")
  ui.hints(thread_hints(p, t, f))
  local k = t.kind == "dm" and key_state(t.peer)
  -- the channel row, a direct thread's header
  if t.kind == "dm" then
    local n = nodes[t.peer] or {}
    local who = name_of(t.peer)
    local label = who == short_id(t.peer) and who or who .. "  " .. short_id(t.peer)
    local state = k == "known" and "Encrypted" or k == "none" and "Not private"
                  or k == "changed" and "Key changed" or "Waiting for key"
    ui.icon(k == "known" and "lock" or k == "waiting" and "key" or "warn")
    ui.info(label, state, { signal = bars(n.rssi, n.snr) })
  else
    local s = setup_now
    local label = t.name
    if t.kind == "ch" and t.name ~= MODEMS[s.preset].title then
      label = t.name .. " \u{b7} " .. preset_title(s)
    end
    if t.kind == "old" then
      if ui.link(label, "", { id = "chan", tag = "Read-only" }) then
        push({ kind = "channel", old = t.name })
      end
    elseif t.public then
      if ui.link(label, "", { id = "chan", tag = "Public" }) then
        push({ kind = "channel", ci = t.ci })
      end
    else
      ui.icon("lock")
      if ui.link(label, "", { id = "chan" }) then
        push({ kind = "channel", ci = t.ci })
      end
    end
  end
  local out = {}
  ui.split("room", "y", { 1 }, {}, out)
  local rx, ry, rw, rh = out[1], out[2], out[3], out[4]
  local L = metrics(ry - y)
  local g = L.g
  ui.at(rx + g, ry, rw - 2 * g, 1)
  ui.rule()
  local fh, sw, zgap = L.field, L.send, L.zgap
  local hy, hh = ry + 1, rh - 1 - fh - 2 * zgap
  local fy = ry + rh - fh - zgap
  -- the history
  local readonly = t.kind == "old" or k == "waiting" or k == "changed"
  local cut_open = nil
  if #t.msgs == 0 and k ~= "waiting" and k ~= "changed" then
    ui.at(rx, hy, rw, hh)
    local lines = { "Messages on " .. t.name .. " show up here." }
    if t.kind == "dm" then
      local _, l = first_line(t)
      lines = { l }
    elseif t.public then
      lines[2] = "Public: anyone who knows the name can read it."
    end
    ui.empty("bubble", "No messages yet", lines)
  elseif k == "waiting" then
    local who = short_id(t.peer)
    ui.at(rx, hy, rw, hh - fh - 8 * room_fs)
    local yu = ui.empty("key", "Waiting for " .. name_of(t.peer) .. "'s key",
                        hh >= 160 * room_fs
                          and { "Only " .. who .. "'s radio will be able to read it.",
                                "Its key comes with its name;", "you can write once it's heard." }
                          or { "Only " .. who .. "'s radio will be able to read it.",
                               "Its key comes with its name." })
    local n = nodes[t.peer]
    local bw = math.min(rw - 2 * g, 140 * room_fs)
    yu = math.min(yu, hy + hh - fh - 4 * room_fs)
    if n and n.asked and now - n.asked < ASK_AGAIN then
      ui.at(rx + g, yu + 4 * room_fs, rw - 2 * g, rowh)
      ui.icon("check")
      ui.label("Asked \u{b7} " .. (now - n.asked < 60 and "just now" or age(n.asked) .. " ago"))
    else
      ui.at(rx + (rw - bw) // 2, yu + 4 * room_fs, bw, fh)
      if ui.button("Ask for their key", "primary", { id = "ask" }) then
        moybyte.send(ME, "act", "ask", t.peer)
      end
      if fresh then ui.focus("ask") end
    end
  else
    local held = k == "changed" and (p.held or 3 * rowh) + fh + 2 * zgap or 0
    local anchor = nil
    ui.at(rx, hy, rw, hh - held)
    -- the history under a key-change card is a view of its own height: a
    -- following view opens at its foot
    ui.scroll(held > 0 and "hist" .. held or "hist", { snap = true, follow = true })
    local icon, line = first_line(t)
    if line then
      ui.icon(icon)
      ui.label(line)
    end
    local day = nil
    local many = synced and #t.msgs > 0 and day_of(t.msgs[1].r.t or now) ~= day_of(now)
    for _, m in ipairs(t.msgs) do
      if many and m.r.t and day_of(m.r.t) ~= day then
        day = day_of(m.r.t)
        ui.label(day_head(m.r.t))
      end
      local side, o = bubble_of(t, m)
      local open = p.pop and p.pop.i == m.i
      o.anchor = open
      local bx, by, bw2, bh, st = ui.bubble(m.r.text or "", side, o)
      if st & 2 ~= 0 and not p.pop then
        if (st & ui.CUT) ~= 0 then
          cut_open = m
        elseif m.r.from == "" and failed(m.r) and by_key() then
          moybyte.send(ME, "act", "resend", m.i, m.r.text)
        else
          p.pop, p.pop_fresh = m, 2
        end
      end
      if open then
        ui.at(rx + g, by, rw - 2 * g, bh)
        anchor = m
      end
    end
    ui.scroll()
    if anchor then
      local m = anchor
      if ui.popover("details", rw - 2 * g, { wash = "view" }) then
        details_rows(t, m)
        local what = details_actions(t, m)
        ui.popover()
        if (p.pop_fresh or 0) > 0 then
          p.pop_fresh = p.pop_fresh - 1
          if m.r.from == "" and failed(m.r) then ui.focus("Send again") end
        end
        if what then
          p.pop = nil
          act_on(t, m, what)
        end
      else
        ui.popover()
        p.pop = nil
      end
    end
    if k == "changed" then
      local who = name_of(t.peer)
      ui.at(rx + g, hy + hh - held, rw - 2 * g, held)
      local y0 = hy + hh - held
      local yu = ui.note("warn", { who .. "'s key changed.",
                                   "This can mean a reset radio, or someone pretending. "
                                   .. "Accept the new key?" })
      p.held = yu - y0
      local bw = 80 * room_fs
      ui.at(rx + rw - g - 2 * bw - 4 * room_fs, yu + 2 * room_fs, bw, fh)
      local nn = ui.button("Not now", "primary", { id = "notnow" })
      ui.at(rx + rw - g - bw, yu + 2 * room_fs, bw, fh)
      if ui.button("Accept", "normal", { id = "accept" }) then
        moybyte.send(ME, "act", "accept", t.peer)
      end
      if nn then
        pop()
      end
      if fresh then ui.focus("notnow") end
    end
  end
  -- the field and its send key
  local hint
  if t.kind == "old" then
    hint = "Replaced by " .. threads.ch0.name .. " \u{b7} read-only"
  elseif k == "waiting" then
    hint = "Waiting for their key"
  elseif k == "changed" then
    hint = "Accept the new key to write"
  else
    hint = "Message " .. t.name
  end
  field_at(L, rx + g, fy, rw - 2 * g - sw - 4 * room_fs, fh)
  local text, enter = ui.field("", drafts[p.key] or "", TEXT_MAX,
                               { id = "msg", hint = hint, disabled = readonly or nil })
  drafts[p.key] = text
  ui.at(rx + rw - g - sw, fy, sw, fh)
  ui.icon("arrow_up")
  local go = ui.button("", "primary", { id = "send", disabled = readonly or nil })
  if not readonly and (enter or go) then
    send_text(p, t, text)
  end
  if not p.pop and keyp(109) or btnp("y") then
    local m = focused_msg(t, f)
    if m and m.r.from == "" and failed(m.r) then
      p.pop, p.pop_fresh = m, 2
    end
  end
  if cut_open then
    push({ kind = "message", key = p.key, i = cut_open.i })
  end
  if fresh and not readonly then
    ui.focus(p.back_from and p.back_from.i and "m" .. p.back_from.i or "msg")
  elseif fresh and t.kind == "old" then
    ui.focus("chan")
  end
end

-- A page's body: a snapping scroll region over the room the hints leave,
-- less a band `foot` (true: a row of buttons) at its foot. Answers the
-- page's numbers (metrics) and the band's rect.
local function body(id, foot)
  local o1, o2 = {}, {}
  ui.split(id .. ".room", "y", { 1 }, {}, o1)
  ui.label("")                  -- a row, to read the row height by
  ui.split(id .. ".row", "y", { 1 }, {}, o2)
  local L = metrics(o2[2] - o1[2])
  local x, y, w, h = o1[1], o1[2], o1[3], o1[4]
  local fh = foot and L.field + 2 * L.zgap or 0
  ui.at(x, y, w, h - fh)
  ui.scroll(id, { snap = true })
  return L, x + L.g, y + h - fh + L.zgap, w - 2 * L.g, L.field
end

-- Buttons right-aligned in a band (x, y, w, h), in `names`' order, the last
-- `kind` and the rest normal: the one activated, else nil.
local function buttons(x, y, w, h, names, kind)
  local bw, gap = 76 * room_fs, 6 * room_fs
  local at = x + w - #names * bw - (#names - 1) * gap
  local got = nil
  for i, n in ipairs(names) do
    ui.at(at + (i - 1) * (bw + gap), y, bw, h)
    if ui.button(n, i == #names and kind or "normal", { id = n }) then
      got = n
    end
  end
  return got
end

local function draw_message(p)
  local t = threads[p.key]
  local m = t and focused_msg(t, "m" .. p.i)
  if m == nil then
    pop()
    return
  end
  ui.page("Message")
  ui.hints({ "back", "Back" })
  body("message")
  local side, o = bubble_of(t, m)
  o.max, o.lines, o.id = 1, 0, "whole"
  ui.bubble(m.r.text or "", "in", o)
  ui.group(m.r.from ~= "" and "Heard" or "Sent")
  details_rows(t, m, true)
  ui.group()
  ui.scroll()
end

-- -- the home and the nodes -----------------------------------------------------

local function preview(t)
  local m = t.msgs[#t.msgs]
  if m == nil then
    if t.kind == "dm" then
      local k = key_state(t.peer)
      return k == "waiting" and "Waiting for their key" or "No messages yet"
    end
    return "No messages yet"
  end
  local who = m.r.from == "" and "You: " or (t.kind == "dm" and "" or name_of(m.r.from) .. ": ")
  return who .. (m.r.text or "")
end

local function draw_home(p)
  ui.page("")
  ui.hints({ "confirm", "Open" })
  body("home")
  local items, subs, values, icons, counts, tags, marks = {}, {}, {}, {}, {}, {}, {}
  for i, t in ipairs(order) do
    items[i] = t.name
    subs[i] = preview(t)
    values[i] = t.last > 0 and age(t.last) or ""
    icons[i] = t.kind == "dm" and "@" .. t.name or "bubble"
    counts[i] = t.unread > 0 and t.unread or ""
    if t.kind == "old" then
      tags[i], marks[i] = "Read-only", ""
    elseif t.kind == "ch" then
      tags[i], marks[i] = t.public and "Public" or "", t.public and "" or "lock"
    else
      tags[i], marks[i] = "", key_state(t.peer) == "known" and "lock" or ""
    end
  end
  ui.group("", { card = true })
  local cur = ui.list("", items, 0, { id = "convs", subs = subs, values = values,
                                       icons = icons, counts = counts, tags = tags,
                                       marks = marks })
  ui.group()
  ui.group("", { card = true })
  local heard = 0
  for _ in pairs(nodes) do heard = heard + 1 end
  local go_nodes = ui.link("Nodes", heard .. " heard")
  local go_radio = ui.link("Radio", preset_title(setup_now) .. ", " .. setup_now.region)
  ui.group()
  ui.scroll()
  if cur > 0 then
    open_thread(order[cur].key)
  elseif go_nodes then
    push({ kind = "nodes" })
  elseif go_radio then
    push({ kind = "radio" })
  end
  if fresh then ui.focus("convs") end
end

local function draw_nodes(p)
  ui.page("Nodes")
  ui.hints({ "confirm", "Message", "back", "Back" })
  local ids = {}
  for id in pairs(nodes) do ids[#ids + 1] = id end
  table.sort(ids, function(a, b) return (nodes[a].heard or 0) > (nodes[b].heard or 0) end)
  if #ids == 0 then
    ui.empty("radio", "No nodes heard yet", { "A node shows here once its radio is heard." })
    return
  end
  body("nodes")
  ui.group("Heard \u{b7} " .. #ids)
  for i, id in ipairs(ids) do
    local n = nodes[id]
    local who = name_of(id)
    local label = who == short_id(id) and who or who .. "  " .. short_id(id)
    if ui.link(label, age(n.heard), { id = "n" .. id, signal = bars(n.rssi, n.snr) }) then
      open_thread(id)
    end
    if fresh and i == 1 then ui.focus("n" .. id) end
  end
  ui.group()
  ui.scroll()
end

-- -- the radio's pages --------------------------------------------------------------

local function console_name()
  local ok, n = pcall(moybyte.name)
  return ok and type(n) == "string" and n or ""
end

local function draw_radio(p)
  ui.page("Radio")
  local f = ui.focus()
  local long = prefs.get("long_name") or ""
  long = long ~= "" and long or console_name()
  local short = prefs.get("short_name") or ""
  local own = prefs.get("long_name") or ""
  own = own ~= "" and own or console_name()
  -- the service's default (service.lua's short_default)
  local auto = (own == "" or own:sub(1, 7):lower() == "moybyte") and me:sub(-4) or clip(own, 4)
  short = short ~= "" and short or auto
  if (f == "Name" and (p.long or long) ~= long)
      or (f == "Short name" and (p.short or short) ~= short) then
    ui.hints({ "confirm", "Save", "cancel", "Cancel" })
  else
    ui.hints({ "confirm", "Edit", "back", "Back" })
  end
  local s = setup_now
  body("radio")
  ui.group("This node")
  local nl, el = ui.field("Name", p.long or long, 39, { hint = console_name() })
  p.long = nl
  if el then
    -- the console's own name, or none, is the console's: it follows it
    prefs.set("long_name", nl ~= console_name() and nl or "")
    p.long = nil
    moybyte.send(ME, "retune")
  end
  local ns, es = ui.field("Short name", p.short or short, 4, { hint = auto })
  p.short = ns
  if es then
    prefs.set("short_name", ns ~= auto and ns or "")
    p.short = nil
    moybyte.send(ME, "retune")
  end
  if me ~= "" then
    ui.info("Id", upper_id(me))
  end
  ui.group("Mesh")
  local go_preset = ui.link("Preset", preset_title(s))
  local go_region = ui.link("Region", s.region)
  ui.group("Channels")
  local open_ci = nil
  for i, c in ipairs(s.channels) do
    if ui.link(channel_name(s, i), i == 1 and "Primary" or key_kind(c, i), { id = "c" .. i }) then
      open_ci = i - 1
    end
  end
  local add = false
  if #s.channels < CHANNELS_MAX then
    ui.icon("plus")
    add = ui.link("Add channel", "", { ink = "value" })
  end
  ui.group()
  ui.scroll()
  if go_preset then
    push({ kind = "preset" })
  elseif go_region then
    push({ kind = "region" })
  elseif open_ci then
    push({ kind = "channel", ci = open_ci })
  elseif add then
    push({ kind = "add" })
  end
  if fresh and not p.back_from then ui.focus("Name") end
  if fresh and p.back_from then
    local b = p.back_from
    ui.focus(b.kind == "preset" and "Preset" or b.kind == "region" and "Region"
             or b.kind == "channel" and b.ci and "c" .. (b.ci + 1) or "Add channel")
  end
end

local function draw_region(p)
  ui.page("Region")
  ui.hints({ "confirm", "Choose", "back", "Back" })
  body("region")
  local cur = 1
  local places = {}
  for i, r in ipairs(REGIONS) do
    places[i] = PLACES[r] or ""
    if r == setup_now.region then cur = i end
  end
  ui.group("Region")
  local new = ui.list("", REGIONS, cur, { id = "regions", values = places, check = true })
  ui.group()
  ui.scroll()
  if new ~= cur and REGIONS[new] then
    local s = setup()
    s.region = REGIONS[new]
    retune(s)
  end
  if fresh then ui.focus("regions") end
end

local function draw_preset(p)
  ui.page("Preset")
  if p.ask then
    ui.hints({ "confirm", "Cancel", "back", "Back" })
  else
    ui.hints({ "confirm", "Choose", "back", "Back" })
  end
  body("preset")
  ui.note("warn", { "Every radio on a mesh uses one preset.",
                    "On another one you hear none of them." })
  local cur, titles, hints = 1, {}, {}
  local _, now_p = modem(setup_now.region, setup_now.preset)
  for i, k in ipairs(PRESETS) do
    titles[i] = MODEMS[k].title
    hints[i] = HINTS[k] or ""
    if k == now_p then cur = i end
  end
  ui.group("Preset")
  local new = ui.list("", titles, cur, { id = "presets", values = hints, check = true })
  ui.group()
  ui.scroll()
  if new ~= cur and PRESETS[new] and not p.ask then
    p.ask = new
  end
  if p.ask then
    local title = titles[p.ask]
    local b = ui.dialog("Switch to " .. title .. "?", { "Cancel", "Switch" }, "primary",
                        { focus = "Cancel" })
    ui.label("You will hear only radios on " .. title .. ",")
    ui.label("and only they hear you.")
    ui.label("Your channels and messages stay.")
    ui.dialog()
    if b == 2 then
      local s = setup()
      s.preset = PRESETS[p.ask]
      p.ask = nil
      retune(s)
    elseif b == 1 then
      p.ask = nil
    end
  end
  if fresh then ui.focus("presets") end
end

local KEYS = { "Default key", "No key", "Random key", "Paste a key" }

local function draw_channel(p)
  local s = setup()
  if p.old then
    ui.page(p.old)
    ui.hints({ "confirm", "Remove", "back", "Back" })
    ui.label("This channel was replaced; its messages stay, read-only.")
    if ui.button("Remove channel", "danger") then
      p.ask = true
    end
    if p.ask then
      local b = ui.dialog("Remove " .. p.old .. "?", { "Cancel", "Remove" }, "danger",
                          { focus = "Cancel" })
      ui.label("Its messages go with it.")
      ui.dialog()
      if b == 2 then
        local l = prefs.get("log") or {}
        for i = #l, 1, -1 do
          if l[i].old == p.old then table.remove(l, i) end
        end
        prefs.set("log", l)
        p.ask = nil
        stack = { { kind = "home" } }
        fresh = true
        _reload()
      elseif b == 1 then
        p.ask = nil
      end
    end
    return
  end
  local i = p.ci + 1
  local c = s.channels[i]
  if c == nil then
    pop()
    return
  end
  ui.page(channel_name(s, i))
  local f = ui.focus()
  if (f == "Name" and (p.name or c.name) ~= c.name) or (f == "Key" and p.paste) then
    ui.hints({ "confirm", "Save", "cancel", "Cancel" })
  else
    ui.hints({ "confirm", "Edit", "back", "Back" })
  end
  body("channel")
  ui.group("Channel")
  local nm, done = ui.field("Name", p.name or c.name, CHANNEL_NAME_MAX,
                            { hint = MODEMS[s.preset].title })
  p.name = nm
  if done and nm ~= c.name then
    c.name = nm
    p.edited = true
    retune(s)
  end
  ui.info("Role", i == 1 and "Primary" or "Secondary")
  if p.edited then
    ui.group()
    ui.note("warn", { "Radios that do not have the new name and key no longer read it.",
                      "Share the channel's link with them." })
  end
  ui.group("Key")
  local kind = key_kind(c, i)
  local cur = kind == "Default key" and 1 or kind == "No key" and 2 or 3
  local new = ui.list("", KEYS, cur, { id = "keys", check = true, values = { "", "", "\u{203a}", "\u{203a}" } })
  if new ~= cur then
    if new == 1 then
      c.psk = DEFAULT_PSK
      p.edited = true
      retune(s)
    elseif new == 2 then
      c.psk = "AA=="
      p.edited = true
      retune(s)
    elseif new == 3 then
      p.random = true
    elseif new == 4 then
      p.paste = ""
    end
  end
  if p.paste then
    local txt, ok = ui.field("Key", p.paste, 44, { hint = "Base64, 16 or 32 bytes" })
    p.paste = txt
    if ok then
      local raw = b64_bytes(txt)
      if raw and (#raw == 16 or #raw == 32) then
        c.psk = b64(raw)
        p.paste, p.bad, p.edited = nil, nil, true
        retune(s)
      else
        p.bad = true
      end
    end
    if p.bad then
      ui.label("That is not a key of 16 or 32 bytes.")
    end
  end
  if cur <= 2 then
    ui.group()
    ui.label("Anyone who knows the name can read it.")
  end
  ui.group("Share")
  if clipboard and ui.link("Copy channel link", "") then
    local one = { region = s.region, preset = s.preset, slot = s.slot, hops = s.hops,
                  channels = { c } }
    clipboard.put_text(url_make(one))
    p.copied = true
  end
  if p.copied then
    ui.info("Copied", "")
  end
  ui.group()
  if i > 1 and ui.button("Remove channel", "danger") then
    p.ask = true
  end
  ui.scroll()
  if p.random then
    ui.popover("random", 0)
    local got = ui.menu("Random key", { "128-bit key", "256-bit key" }, { "key", "key" })
    if not ui.popover() then end
    if got and got > 0 then
      moybyte.send(ME, "act", "key", p.ci, got == 1 and 16 or 32)
      p.random, p.edited = nil, true
    end
  end
  if p.ask then
    local b = ui.dialog("Remove " .. channel_name(s, i) .. "?", { "Cancel", "Remove" }, "danger",
                        { focus = "Cancel" })
    ui.label("Radios on it no longer reach this console.")
    ui.dialog()
    if b == 2 then
      table.remove(s.channels, i)
      p.ask = nil
      retune(s)
      pop()
    elseif b == 1 then
      p.ask = nil
    end
  end
  if fresh then ui.focus("Name") end
end

local function clip_link()
  if clipboard == nil then
    return nil
  end
  local ok, k = pcall(clipboard.kind)
  if not ok or k == nil then
    return nil
  end
  local ok2, txt = pcall(clipboard.text)
  if not ok2 or type(txt) ~= "string" then
    return nil
  end
  txt = txt:match("^%s*(.-)%s*$")
  if txt:find("^meshtastic%.org/e/") or txt:find("^https://meshtastic%.org/e/") then
    return txt
  end
  return nil
end

local function read_link(p, url)
  local link = url:find("^https?://") and url or "https://" .. url
  local prev, why = url_parse(link)
  if prev == nil then
    p.bad = why or "That is not a channel link."
    return
  end
  p.bad = nil
  push({ kind = "link", prev = prev })
end

local function draw_add(p)
  ui.page("Add channel")
  if p.clip == nil then
    p.clip = clip_link() or false
  end
  local f = ui.focus()
  if p.clip and f ~= "link" then
    ui.hints({ "confirm", "Paste", "back", "Back" })
  elseif f == "link" then
    ui.hints({ "confirm", "Read link", "cancel", "Cancel" })
  else
    ui.hints({ "confirm", "Edit", "back", "Back" })
  end
  local L, bx, by, bw, bh = body("add", true)
  ui.group("From a link")
  local url, enter = ui.field("", p.url or "", LINK_MAX,
                              { id = "link", hint = "meshtastic.org/e/#\u{2026}" })
  p.url = url
  ui.group()
  if p.bad then
    ui.label(p.bad)
  elseif p.clip then
    ui.note("paste", { "Your clipboard holds a channel link." })
    ui.group("", { card = true })
    ui.icon("paste")
    if ui.link("Paste link", "", { id = "paste", ink = "value" }) then
      read_link(p, p.clip)
    end
    ui.group()
  else
    ui.label("Paste or type a meshtastic.org/e/ link.")
  end
  if enter and url ~= "" then
    read_link(p, url)
  end
  ui.group("By name")
  local nm, done = ui.field("Name", p.name or "", CHANNEL_NAME_MAX, { hint = "Channel name" })
  p.name = nm
  ui.info("Key", "Random key")
  ui.group()
  ui.scroll()
  local add = buttons(bx, by, bw, bh, { "Add" }, p.clip and "normal" or "primary") ~= nil
  if (add or done) and nm ~= "" then
    local s = setup()
    if #s.channels < CHANNELS_MAX then
      s.channels[#s.channels + 1] = { name = nm, psk = DEFAULT_PSK }
      setup_save(s)
      moybyte.send(ME, "act", "key", #s.channels - 1, 32)
      pop()
      _reload()
    end
  end
  if fresh then ui.focus(p.clip and "paste" or "link") end
end

local function from_to(a, b)
  return a == b and a or a .. " to " .. b
end

local function draw_link(p)
  local prev = p.prev
  local cur = setup()
  local next_s = setup_merge(prev, cur)
  ui.page("Channel link")
  ui.hints({ "confirm", "Cancel", "back", "Back" })
  local L, bx, by, bw, bh = body("link", true)
  ui.group("", { card = true })
  for _, c in ipairs(prev.channels) do
    local nm = c.name ~= "" and c.name or MODEMS[next_s.preset].title
    ui.info("Channel", nm .. " \u{b7} " .. (public(c) and "Public" or "Keyed"))
  end
  ui.info("Preset", from_to(MODEMS[cur.preset].title, MODEMS[next_s.preset].title))
  ui.info("Region", from_to(cur.region, next_s.region))
  if ui.link("Details", "") then
    p.details = not p.details
  end
  if p.details then
    ui.info("Frequency slot", tostring(next_s.slot))
    ui.info("Hop limit", tostring(next_s.hops))
    for _, wn in ipairs(prev.warnings or {}) do
      ui.label(wn)
    end
  end
  ui.group()
  local lines = {}
  if prev.add then
    lines[#lines + 1] = "Adds " .. table.concat((function()
      local n = {}
      for _, c in ipairs(prev.channels) do n[#n + 1] = c.name end
      return n
    end)(), ", ") .. "."
  else
    lines[#lines + 1] = channel_name(cur, 1) .. " is replaced; its messages stay, read-only."
  end
  if next_s.preset ~= cur.preset then
    lines[#lines + 1] = "You will hear only " .. MODEMS[next_s.preset].title .. " radios."
  end
  if next_s.region ~= cur.region then
    lines[#lines + 1] = "Region is law: set the one you are in."
  end
  ui.note("warn", lines)
  ui.scroll()
  local got = buttons(bx, by, bw, bh, { "Cancel", "Apply" }, "primary")
  local cancel, apply = got == "Cancel", got == "Apply"
  if apply then
    if not prev.add then
      moybyte.send(ME, "act", "retire")
    end
    setup_save(next_s)
    moybyte.send(ME, "retune")
    stack = { { kind = "home" } }
    fresh = true
    _reload()
    return
  elseif cancel then
    pop()
  end
  if fresh then ui.focus("Cancel") end
end

-- -- the frame ------------------------------------------------------------------------

local TITLES = { message = "Message", nodes = "Nodes", radio = "Radio", region = "Region",
                 preset = "Preset", add = "Add channel", link = "Channel link" }

local DRAW = { home = draw_home, thread = draw_thread, message = draw_message,
               nodes = draw_nodes, radio = draw_radio, region = draw_region,
               preset = draw_preset, channel = draw_channel, add = draw_add,
               link = draw_link }

function _draw()
  local p = stack[#stack]
  local title = TITLES[p.kind] or ""
  if p.kind == "channel" then
    title = p.old or (setup_now.channels[p.ci + 1] and channel_name(setup_now, p.ci + 1)) or ""
  end
  if ui.bar(title, {}, {}, #stack > 1) == -1 then
    pop()
    p = stack[#stack]
  end
  local was = fresh
  DRAW[p.kind](p)
  if was then
    fresh = false
    p.back_from = nil
  end
end
