-- The UI tree layer's reference back end (runtime/run/uitree.lua is the
-- layer): each frame it replays the built page into today's `ui` verbs,
-- laying it out, drawing it and moving focus in Lua. The Player loads it after
-- the layer when the app runs on the reference engine (runtime/run/lua_ext.py);
-- uitree_engine.lua is the C engine's back end.

local I = ui._in
ui._in = nil
local E, T, V, DECL, ACTIONS, KEYMAP, SYS, STOPS = I.E, I.T, I.V, I.DECL, I.ACTIONS, I.KEYMAP, I.SYS, I.STOPS
local stack, R, ModelMT = I.stack, I.R, I.ModelMT
local fail, q, show, trace, lookup, render, cond, truthy = I.fail, I.q, I.show, I.trace, I.lookup, I.render, I.cond, I.truthy
local frozen, setv, fire, fire_fn, build_page, bump = I.frozen, I.setv, I.fire, I.fire_fn, I.build_page, I.bump

local RECT, ROW_ORDER = {}, {}
local DREW = {}

local L = {}
local function metrics(row)
  local fs = SYS.fs
  if row >= 36 then
    L = { g = 8 * fs, field = row, send = 40 * fs, zgap = 4 * fs, inset = 10 * fs, desk = false }
  elseif row >= 28 * fs then
    L = { g = 12 * fs, field = 30 * fs, send = 36 * fs, zgap = 6 * fs, inset = 12 * fs, desk = true }
  else
    L = { g = 8 * fs, field = 22 * fs, send = 28 * fs, zgap = 2 * fs, inset = 10 * fs, desk = false }
  end
  L.row = row
  if SYS.desk ~= L.desk then SYS.desk, SYS.handheld = L.desk, not L.desk; bump() end
end

-- an action is on when its when= holds and, at a site that hands no row, its
-- row= state holds a row
local function can(name, row)
  local a = ACTIONS[name]
  if not a then return false end
  if a.row and row == nil and V[a.row] == nil then return false end
  return a.when == nil or cond(a.when) or false
end

-- the engine's options for a node: its templates rendered; tone is the
-- engine's ink (a bubble's tone is its own)
local ENGINE_SKIP = { key = 1, on = 1, shown = 1, focus = 1, grow = 1, icon = 1, when = 1, off = 1, commit = 1,
                      kind = 1, current = 1, cur = 1, anchor = 1, label = 1, value = 1, by = 1, cut = 1,
                      changed = 1, close = 1, buttons = 1, default = 1, arg = 1, lines = 1, empty = 1, check = 1,
                      new = 1, follow = 1, input = 1, hint = 1 }
local function over(n, o, row, id)
  local t = { id = id }
  for k, v in pairs(o) do
    if not ENGINE_SKIP[k] then
      local ek = (k == "tone" and n.kind ~= "bubble") and "ink" or k
      if type(v) == "table" then
      elseif type(v) == "string" then
        local whole = v:match("^{([%w_.@]+)}$")
        if whole then t[ek] = lookup(whole, row) else t[ek] = render(v, row) end
        if t[ek] == "" and (k == "tone" or k == "tag" or k == "status" or k == "head") then t[ek] = nil end
      else t[ek] = v end
    end
  end
  return t
end

local function icon(o, row, act)
  local s = o.icon or (act and act.icon)
  local i = s and render(s, row)
  if i and i ~= "" then E.icon(i) end
end

local first_frame = false
local function drew(n, id, txt, tpl)
  local was = DREW[id]
  DREW[id] = txt
  if first_frame or was == nil or was == txt then return end
  trace("drew", n.kind, "\"" .. tostring(tpl or n.a[1] or "") .. "\" -> \"" .. txt .. "\"")
end

local draw
local ANCHOR_KEY, ANCHOR_AT = nil, false
-- the anchor names a row by its key: the row's node, or a node in it
local function anchored(id)
  if ANCHOR_KEY == nil then return false end
  local seg = "/" .. ANCHOR_KEY
  local i = id:find(seg, 1, true)
  while i do
    local c = id:sub(i + #seg, i + #seg)
    if c == "" or c == "/" then return true end
    i = id:find(seg, i + 1, true)
  end
  return false
end
local ROWKEY, ROWCUR = nil, nil
local function nname(n) return (ROWKEY and ROWKEY .. "/" or "") .. (n.rkey or n.kind) end
local function reg(id, n, row) R.ids[id] = { n = n, row = row, name = nname(n), key = ROWKEY, cur = ROWCUR } end

-- is the action a node names on, and how does it look while off
local function site_off(n, row)
  local act = n.action and ACTIONS[n.action]
  if not act or (act.when == nil and act.row == nil) then return nil end
  if can(n.action, row) then return nil end
  return act.off
end

local function leafdraw(n, row, id)
  local k, a, o = n.kind, n.a, n.o
  if k == "rule" then return E.rule() end
  if k ~= "note" then icon(o, row, n.action and ACTIONS[n.action]) end
  local eo = over(n, o, row, id)
  if k == "label" then
    local t = render(a[1], row); drew(n, id, t, a[1]); E.label(t, eo) return
  end
  if k == "info" then
    local t1, t2 = render(a[1], row), render(o.value, row)
    drew(n, id .. "#label", t1, a[1]); drew(n, id, t2, o.value)
    E.info(t1, t2, eo) return
  end
  if k == "note" then
    local lines = {}
    for i, l in ipairs(o.lines) do lines[i] = render(l, row) end
    return E.note(render(o.icon or "", row), lines)
  end
  reg(id, n, row)
  if k == "link" then
    local t1, t2 = render(a[1], row), render(o.value, row)
    drew(n, id .. "#label", t1, a[1]); drew(n, id, t2, o.value)
    if site_off(n, row) == "grey" then eo.disabled = true end
    if E.link(t1, t2, eo) then fire(n.action, "link " .. q(nname(n)), o.arg ~= nil and o.arg or row) end
  elseif k == "button" then
    if site_off(n, row) == "grey" then eo.disabled = true end
    if E.button(render(a[1], row), o.kind or "normal", eo) then fire(n.action, "button " .. q(nname(n)), o.arg ~= nil and o.arg or row) end
  elseif k == "toggle" or k == "slider" then
    local name = a[2]
    local was = V[name]
    if o.when ~= nil and not cond(o.when, row) then eo.disabled = true end
    local now
    if k == "toggle" then now = E.toggle(render(a[1], row), was, eo)
    else now = E.slider(render(a[1], row), was, o.lo, o.hi, eo) end
    if now ~= was and not eo.disabled then
      fire_fn(function() setv(name, now) end, k .. " " .. q(name), k .. " " .. q(nname(n)))
      if o.on then fire(o.on, k .. " " .. q(nname(n))) end
    end
  elseif k == "field" then
    local name = a[2]
    if o.when ~= nil and not cond(o.when, row) then eo.disabled = true end
    eo.hint = o.hint and render(o.hint, row) or nil
    eo.secret = o.secret
    local shown = n.draft or V[name] or ""
    local text, enter = E.field(render(a[1], row), shown, o.max or 64, eo)
    if eo.disabled then return end
    if o.commit then
      n.draft = text ~= V[name] and text or nil
      if enter then
        n.draft = nil
        fire_fn(function() setv(name, text) end, "field " .. q(name), "field " .. q(nname(n)))
        if o.on then fire(o.on, "field " .. q(nname(n))) end
      end
    else
      if text ~= V[name] then
        fire_fn(function() setv(name, text) end, "field " .. q(name), "field " .. q(nname(n)))
        if o.changed then fire(o.changed, "field " .. q(nname(n))) end
      end
      if enter and o.on then fire(o.on, "field " .. q(nname(n))) end
    end
  elseif k == "bubble" then
    eo.anchor = anchored(id) or nil
    local t = render(a[1], row)
    drew(n, id, t, a[1]); drew(n, id .. "#status", tostring(eo.status or ""), o.status)
    local x, y, w, h, st = E.bubble(t, render(o.side or "in", row), eo)
    RECT[id] = { x, y, w, h }
    -- the anchor's rect is placed where it is drawn, in its view
    if eo.anchor then E.at(SYS.room[1] + L.g, y, SYS.room[3] - 2 * L.g, h); ANCHOR_AT = true end
    if st & 2 ~= 0 then
      if st & E.CUT ~= 0 and o.cut then fire(o.cut, "bubble " .. q(nname(n)) .. " (cut)", row)
      elseif o.on then fire(o.on, "bubble " .. q(nname(n)), row) end
    end
  end
end

local function rows_list(n, id)
  -- rows with a current mark are the engine's list: the row's link gives its
  -- label and value
  local m = V[n.a[1]]
  local o = n.o
  local lk = n.body and n.body.kids[1]
  local items, values, cur = {}, {}, 0
  for i, r in ipairs(m.rows) do
    items[i] = render(lk.o.label or lk.a[1], r, n.a[1])
    values[i] = render(lk.o.value, r, n.a[1])
    if r[m.key] == V[o.current] then cur = i end
  end
  reg(id, { kind = "list", action = lk.action, o = {} }, nil)
  local new = E.list("", items, cur, { id = id, values = values, check = o.check or nil })
  if new ~= cur and m.rows[new] then
    fire(lk.action, "rows " .. q(id), frozen(m.rows[new], "row " .. q(m.rows[new][m.key]) .. " of " .. q(n.a[1]), m.ty))
  end
end

local function rowsdraw(n, id)
  local m = V[n.a[1]]
  if #m.rows == 0 and n.o.empty then
    local t = render(n.o.empty)
    if t ~= "" then E.label(t, { id = id .. "/empty" }) end
    return
  end
  if n.o.current ~= nil and n.body and #n.body.kids == 1 and n.body.kids[1].kind == "link" then return rows_list(n, id) end
  for _, r in ipairs(m.rows) do
    local c = n.body or n.cases[r[n.o.by]]
    local fr = frozen(r, "row " .. q(r[m.key]) .. " of " .. q(n.a[1]), m.ty)
    local rid = id .. "/" .. tostring(r[m.key])
    local one = #c.kids == 1
    ROWKEY, ROWCUR = tostring(r[m.key]), n.o.cur
    for _, k in ipairs(c.kids) do
      if cond(k.o.shown, fr, n.a[1]) ~= false then
        local act = k.action and ACTIONS[k.action]
        if not (act and act.off == "hide" and not can(k.action, fr)) then
          leafdraw(k, fr, one and rid or rid .. "/" .. k.rkey)
        end
      end
    end
    ROW_ORDER[#ROW_ORDER + 1] = rid
  end
  ROWKEY, ROWCUR = nil, nil
end

local function stacked(kids) for _, k in ipairs(kids) do draw(k) end end

local function foot_rows(n)
  local out = {}
  for _, k in ipairs(n.kids) do if cond(k.o.shown) ~= false then out[#out + 1] = k end end
  return out
end

local function foot_height(n)
  local h = 0
  for _, k in ipairs(foot_rows(n)) do
    h = h + (k.kind == "note" and (k.h or 3 * L.row) or L.field + 2 * L.zgap)
  end
  return h
end

local function cw_of(c)
  if c.kind == "button" and c.a[1] == "" then return L.send end
  return 80 * SYS.fs
end

local function foot(n, x, y, w)
  local fs = SYS.fs
  for _, k in ipairs(foot_rows(n)) do
    if k.kind == "note" then
      E.at(x + L.g, y, w - 2 * L.g, k.h or 3 * L.row)
      local yu = leafdraw(k, nil, k.path)
      k.h = yu and yu - y or k.h
      y = y + (k.h or 3 * L.row)
    else
      local kids = k.kind == "row" and k.kids or { k }
      local shown, fixed, grow = {}, 0, nil
      for _, c in ipairs(kids) do
        if cond(c.o.shown) ~= false then
          shown[#shown + 1] = c
          if c.o.grow then grow = c else fixed = fixed + cw_of(c) + 4 * fs end
        end
      end
      -- look = "share": the row's controls share its width
      local share = n.o.look == "share" and #shown > 0 and (w - 2 * L.g - 4 * fs * (#shown - 1)) // #shown
      local cx = (grow or share) and x + L.g or x + w - L.g - fixed + 4 * fs
      local by = k.kind == "row" and y + L.zgap or y + L.zgap
      for _, c in ipairs(shown) do
        local cw = share or (c.o.grow and (w - 2 * L.g - fixed) or cw_of(c))
        if c.kind == "field" then E.at(cx - L.inset, by - 2 * fs, cw + 2 * L.inset, L.field + 4 * fs)
        else E.at(cx, by, cw, L.field) end
        draw(c)
        cx = cx + cw + 4 * fs
      end
      y = y + L.field + 2 * L.zgap
    end
  end
end

local function dismiss_value(name)
  local ty = DECL[name]
  if not ty or ty.derived then return nil, false end
  if ty.opt then return nil, true end
  if ty.t == "bool" then return false, true end
  return nil, false
end

local REFOCUS = nil
local function overlay(n)
  if not cond(n.o.shown) then
    if n.open and n.opener then REFOCUS = n.opener end
    n.open, n.opener = nil, nil
    return
  end
  local fresh = not n.open
  if fresh then n.opener = R.focused end
  n.open = (n.open or 0) + 1
  if fresh then trace("open", n.kind, q(n.rkey)) end
  if n.kind == "popover" then
    local w = n.o.w or SYS.room[3] - 2 * L.g
    local r = nil
    if ANCHOR_KEY and not ANCHOR_AT then
      for id, rr in pairs(RECT) do if anchored(id) then r = rr end end
    end
    if ANCHOR_KEY and not ANCHOR_AT and not r then
      fail("N06", n.at, "the popover " .. q(n.a[1]) .. "'s anchor " .. q(ANCHOR_KEY) .. " names no node on the page",
           "a node's key", q(ANCHOR_KEY), "anchor = the key of a node the page draws")
    end
    if r then E.at(SYS.room[1] + L.g, r[2], w, r[4]) end
    if E.popover(n.a[1], w, over(n, n.o, nil, nil)) then
      stacked(n.kids)
      E.popover()
    else
      E.popover()
      if n.o.close then fire(n.o.close, "dismissal of " .. q(n.path))
      else
        local s = type(n.o.shown) == "string" and n.o.shown:match("^[%w_]+$")
        local v, ok = nil, false
        if s then v, ok = dismiss_value(s) end
        if ok then fire_fn(function() setv(s, v, "dismissal of " .. q(n.path)) end, "dismiss", "popover " .. q(n.path)) end
      end
    end
  else
    local labels, focus = {}, nil
    for i, b in ipairs(n.o.buttons) do
      labels[i] = render(ACTIONS[b].label)
      R.ids[labels[i]] = { n = { kind = "button", action = b, o = {}, a = { labels[i] } } }
      if b == n.o.default then focus = labels[i] end
    end
    local b = E.dialog(render(n.a[1]), labels, "primary", { focus = focus })
    stacked(n.kids)
    E.dialog()
    if b > 0 then fire(n.o.buttons[b], "button " .. q(n.o.buttons[b]) .. " of dialog " .. q(render(n.a[1]))) end
  end
  if n.open <= 2 then n.want_focus = true end
end

function draw(n)
  if cond(n.o.shown) == false then return end
  local k = n.kind
  if n.action and site_off(n) == "hide" then return end
  if k == "group" then
    icon(n.o)
    E.group(render(n.a[1]), over(n, n.o))
    stacked(n.kids)
    E.group()
  elseif k == "case" or k == "row" then stacked(n.kids)
  elseif k == "switch" then
    local c = n.built[tostring(lookup(n.a[1]))]
    if c then stacked(c.kids) end
  elseif k == "empty" then
    local lines = {}
    for _, l in ipairs(n.o.lines or {}) do
      local t = render(l)
      if t ~= "" then lines[#lines + 1] = t end
    end
    local r = SYS.body
    local acts = 0
    for _, c in ipairs(n.kids) do if c.kind == "button" and cond(c.o.shown) ~= false then acts = L.field + 8 * SYS.fs end end
    E.at(r[1], r[2], r[3], r[4] - acts)
    local yu = E.empty(render(n.o.icon or ""), render(n.a[1]), lines)
    yu = math.min(yu, r[2] + r[4] - acts)
    for _, c in ipairs(n.kids) do
      if cond(c.o.shown) ~= false then
        local bw = math.min(r[3] - 2 * L.g, 140 * SYS.fs)
        if c.kind == "button" then E.at(r[1] + (r[3] - bw) // 2, yu + 4 * SYS.fs, bw, L.field)
        else E.at(r[1] + L.g, yu + 4 * SYS.fs, r[3] - 2 * L.g, L.row) end
        draw(c)
      end
    end
  elseif k == "rows" then rowsdraw(n, n.path)
  elseif k == "popover" or k == "dialog" or k == "foot" then return
  else leafdraw(n, nil, n.path) end
end

local function want_focus(n, found)
  if found[1] then return end
  if cond(n.o.shown) == false then return end
  if n.want_focus and (n.kind == "popover" or n.kind == "dialog") then
    n.want_focus = nil
    for _, k in ipairs(n.kids) do want_focus(k, found) end
    return
  end
  if n.kind == "popover" or n.kind == "dialog" then return end
  if STOPS[n.kind] and n.o.focus ~= nil and cond(n.o.focus) then found[1] = n.path return end
  -- rows with a current mark are one stop, the list: its link's focus is the list's
  local lk = n.kind == "rows" and n.o.current ~= nil and n.body and n.body.kids[1]
  if lk and lk.kind == "link" and lk.o.focus ~= nil and cond(lk.o.focus) then found[1] = n.path return end
  for _, k in ipairs(n.kids) do want_focus(k, found) end
  if n.kind == "switch" then
    local c = n.built[tostring(lookup(n.a[1]))]
    if c then for _, k in ipairs(c.kids) do want_focus(k, found) end end
  end
end

-- the hints band: the focused node's confirm, then its row's role keys in the
-- order the actions are declared, each key once (a role naming the confirm's
-- own action is not shown twice); a field's own hints are the engine's
local function verb(name, row)
  local a = ACTIONS[name]
  if not a or not can(name, row) then return nil end
  return a.hint or render(a.label, row)
end
local ROLE_KEYS = { "more", "menu", "search", "undo", "redo", "play", "tools" }
-- an open overlay the focus is not in yet: its first shown button confirms
local function first_button(n)
  if cond(n.o.shown) == false then return nil end
  if n.kind == "button" and n.action and can(n.action) then return n end
  local kids = n.kids
  if n.kind == "switch" then
    local c = n.built[tostring(lookup(n.a[1]))]
    kids = c and c.kids or {}
  end
  for _, k in ipairs(kids or {}) do
    local b = first_button(k)
    if b then return b end
  end
end
local function hints(p)
  local h = {}
  local f = R.ids[R.focused or ""]
  local fname = nil
  for _, k in ipairs(p.root.kids) do
    if (k.kind == "popover" or k.kind == "dialog") and cond(k.o.shown) and k.kind == "popover"
       and not (f and f.n.path and f.n.path:sub(1, #k.path) == k.path) then
      local b = first_button(k)
      f = b and { n = b } or nil
    end
  end
  if f then
    local n, does = f.n, nil
    if n.kind == "field" then
      local v = n.draft or V[n.a[2]] or ""
      if n.o.commit then
        if n.draft then h = { "confirm", "Save", "cancel", "Cancel" } else h = { "confirm", "Done" } end
      elseif v ~= "" then
        does = n.o.on and verb(n.o.on)
        if does then h = { "confirm", does } end
        h[#h + 1] = "cancel"; h[#h + 1] = "Cancel"
      else
        h = { "cancel", #stack > 1 and "Back" or "Back" }
      end
      return E.hints(h)
    end
    fname = n.action or n.o.on
    if n.kind == "list" then does = verb(n.action, nil) or "Choose"
    elseif fname then does = verb(fname, f.row) end
    if does and does ~= "" then h[#h + 1] = "confirm"; h[#h + 1] = does end
  end
  for _, role in ipairs(ROLE_KEYS) do
    local name = KEYMAP["role:" .. role]
    local a = name and ACTIONS[name]
    if a and name ~= fname and (a.takes ~= "row" or (f and f.row)) then
      local d = verb(name, f and f.row)
      if d then h[#h + 1] = role; h[#h + 1] = d end
    end
  end
  local shut = false
  for _, k in ipairs(p.root.kids) do
    if (k.kind == "popover" or k.kind == "dialog") and cond(k.o.shown) then shut = true end
  end
  if shut then h[#h + 1] = "back"; h[#h + 1] = "Close"
  elseif not (f and f.row) and #stack > 1 then h[#h + 1] = "back"; h[#h + 1] = "Back" end
  E.hints(h)
end

local function pressed(role)
  if role == "more" then return btnp("y") or keyp(109) end
  return false
end

local function replay(p)
  local root = p.root
  local title = render(root.a[1] or "")
  local dlg = nil
  for _, k in ipairs(root.kids) do
    if k.kind == "dialog" and cond(k.o.shown) then dlg = k end
  end
  if E.bar(title, {}, {}, #stack > 1 or dlg ~= nil) == -1 then
    if dlg then fire(dlg.o.buttons[1], "back on dialog " .. q(dlg.rkey))
    else fire_fn(T.pop, "back", "the bar's back") end
  end
  RECT, ROW_ORDER = {}, {}
  local _, py = E.page(title)
  local was = R.focused
  R.focused = E.focus()
  if R.focused ~= was then SYS.focus = R.focused or ""; bump() end
  local on = R.ids[R.focused or ""]
  if on and on.cur and V[on.cur] ~= on.key then
    local cur, key = on.cur, on.key
    fire_fn(function() setv(cur, key) end, "engine: focus", "focus")
  end
  hints(p)
  R.ids = {}
  local head, ft, overs, scroll = {}, nil, {}, nil
  for _, k in ipairs(root.kids) do
    if k.kind == "foot" then ft = k
    elseif k.kind == "popover" or k.kind == "dialog" then overs[#overs + 1] = k
    elseif k.kind == "scroll" then scroll = k
    else head[#head + 1] = k end
  end
  local body = nil
  if not scroll then body, head = head, {} end
  ANCHOR_KEY, ANCHOR_AT = nil, false
  for _, k in ipairs(overs) do
    if k.o.anchor and cond(k.o.shown) then ANCHOR_KEY = render(k.o.anchor) end
  end
  local r1, r2 = {}, {}
  local rx, ry, rw, rh
  if #head > 0 then
    stacked(head)
    E.split("room", "y", { 1 }, {}, r1)
    metrics(r1[2] - py)
    rx, ry, rw, rh = r1[1], r1[2], r1[3], r1[4]
    E.at(rx + L.g, ry, rw - 2 * L.g, 1)
    E.rule()
    ry, rh = ry + 1, rh - 1
  else
    E.split(root.path .. ".room", "y", { 1 }, {}, r1)
    E.label("")
    E.split(root.path .. ".row", "y", { 1 }, {}, r2)
    metrics(r2[2] - r1[2])
    rx, ry, rw, rh = r1[1], r1[2], r1[3], r1[4]
  end
  SYS.room = { rx, ry, rw, rh }
  local fh = ft and foot_height(ft) or 0
  local tall = rh - fh >= 160 * SYS.fs
  if SYS.tall ~= tall then SYS.tall, SYS.wide = tall, not tall; bump() end
  SYS.body = { rx, ry, rw, rh - fh }
  local kids = scroll and scroll.kids or body
  local only = nil
  for _, k in ipairs(kids) do
    local seen = cond(k.o.shown) ~= false and k.kind ~= "switch"
    if seen and k.kind == "rows" then seen = truthy(V[k.a[1]]) or k.o.empty ~= nil end
    if seen then only = only == nil and k or false end
  end
  if only and only.kind == "empty" then draw(only)
  else
    E.at(rx, ry, rw, rh - fh)
    local so = scroll and scroll.o or {}
    -- a scroll whose room the foot changes is a view of its own height: a
    -- following view opens at its foot
    E.scroll(scroll and (fh > L.field + 2 * L.zgap and scroll.path .. "#" .. fh or scroll.path) or root.path,
             { snap = true, follow = so.follow or nil })
    stacked(kids)
    E.scroll()
    -- the engine's write: new= counts the rows past the view's foot that come,
    -- in model order, after the last row the view has shown. The host layer
    -- measures bubbles only: today's verbs hand back no other row's rect.
    if so.new then
      local pfx, foot_y, nn = scroll.path .. "/", ry + rh - fh, 0
      local first = scroll.seen == nil
      scroll.seen = scroll.seen or {}
      local last = 0
      for i, id in ipairs(ROW_ORDER) do
        local r = RECT[id]
        if r and id:sub(1, #pfx) == pfx then
          if first or r[2] < foot_y then scroll.seen[id] = true end
          if scroll.seen[id] then last = i end
        end
      end
      for i, id in ipairs(ROW_ORDER) do
        local r = RECT[id]
        if r and id:sub(1, #pfx) == pfx and r[2] >= foot_y and i > last then nn = nn + 1 end
      end
      if V[so.new] ~= nn then
        trace("engine", so.new, show(V[so.new]) .. " -> " .. show(nn) .. "   (in engine: rows past the view)")
        V[so.new] = nn
        bump()
      end
    end
  end
  -- a popover is the view's (its wash is the view's), a dialog the page's
  for _, k in ipairs(overs) do if k.kind == "popover" then overlay(k) end end
  if ft then foot(ft, rx, ry + rh - fh, rw) end
  for _, k in ipairs(overs) do if k.kind == "dialog" then overlay(k) end end
  -- the role keys: the focused row's actions, then the page's
  local f = R.ids[R.focused or ""]
  for _, role in ipairs(ROLE_KEYS) do
    local name = KEYMAP["role:" .. role]
    local a = name and ACTIONS[name]
    if a and pressed(role) then
      if a.takes ~= "row" then fire(name, "key " .. role .. " on " .. q(root.path))
      elseif f and f.row then fire(name, "key " .. role .. " on " .. q(R.focused), f.row) end
    end
  end
  -- rows cur: the engine writes the focused row's key
  local want = nil
  if p.fresh > 0 then
    want = p.refocus
    if not want then local found = {}; want_focus(root, found); want = found[1] end
    p.fresh = p.fresh - 1
    if p.fresh == 0 then p.refocus = nil end
  end
  for _, k in ipairs(overs) do
    if k.want_focus then local found = {}; want_focus(k, found); want = found[1] or want end
  end
  if REFOCUS then want, REFOCUS = REFOCUS, nil end
  if R.pending_focus then
    local key, hit = R.pending_focus.key, nil
    for id in pairs(R.ids) do
      if id == key or id:sub(-#key - 1) == "/" .. key then hit = id end
    end
    if not hit then fail("N08", R.pending_focus.at, "ui.focus(" .. q(key) .. ") names no node on the page", "a node's key", q(key)) end
    want = hit
    R.pending_focus = nil
  end
  if want then E.focus(want) end
end

-- a kind or a prop only the C engine draws: refused by name, never drawn wrong
local C_KINDS = { progress = 1, seg = 1, text_view = 1, menu = 1, item = 1, table = 1, col = 1, split = 1, grid = 1,
                  panel = 1, canvas = 1, tabs = 1, cards = 1, card = 1 }
local C_PROPS = { group = { close = 1 }, row = { close = 1, gap = 1, pad = 1, mid = 1 },
                  button = { marked = 1, tone = 1, w = 1 }, slider = { look = 1 },
                  rows = { changed = 1, filter = 1, group = 1, dir = 1 } }
local function refuse(n)
  if C_KINDS[n.kind] then
    fail("E00", n.at, "the reference back end does not draw ui." .. n.kind, "the C engine", "the reference",
         "run this app on the C engine: uitree_app.use_engine(\"c\")")
  end
  for k in pairs(C_PROPS[n.kind] or {}) do
    if n.o[k] ~= nil then
      fail("E00", n.at, "the reference back end does not draw ui." .. n.kind .. "'s " .. k, "the C engine", "the reference",
           "run this app on the C engine: uitree_app.use_engine(\"c\")")
    end
  end
  for _, k in ipairs(n.kids) do refuse(k) end
  if n.body then refuse(n.body) end
  for _, c in pairs(n.built or {}) do refuse(c) end
  for _, c in pairs(n.cases or {}) do refuse(c) end
end

I.BACK.frame = function(p)
  first_frame = false
  if not p.root then build_page(p); refuse(p.root); first_frame = true end
  replay(p)
end
