-- The UI tree layer's C back end (runtime/run/uitree.lua is the layer): each
-- page the layer builds and checks is lowered into the C engine
-- (native/moy_uitree, through native/moycore/moycore_uitree.h's store), one
-- node call a node and one prop call a prop, the way runtime/uitree/uitree_c.py
-- lowers a Python app's; each frame is then one call into C, which lays the
-- page out, draws it into the run's canvas, moves focus and draws the hints,
-- and hands back what the user did as events: a control's writes, pulled into
-- the layer's state, and the actions to run, queued for the layer's handlers.
-- A handler's writes are pushed to the engine's slots before the next frame.
-- What the engine does not draw is an error (E00), never drawn wrong. The
-- Player loads this after the layer when the app runs on the C engine
-- (runtime/run/lua_ext.py); uitree_replay.lua is the reference.

local I = ui._in
ui._in = nil
local NEW = __moy_uitree
__moy_uitree = nil
local E, V, VER, DECL, ACTIONS, ACTION_ORDER = I.E, I.V, I.VER, I.DECL, I.ACTIONS, I.ACTION_ORDER
local LABELS, KEYMAP, DERIVED, stack, R = I.LABELS, I.KEYMAP, I.DERIVED, I.stack, I.R
local fail, q, trace, frozen, fire, fire_fn = I.fail, I.q, I.trace, I.frozen, I.fire, I.fire_fn
local fire_placed = I.fire_placed
local build_page, touched, T = I.build_page, I.touched, I.T

-- the engine's numbers (native/moy_uitree/moy_uitree.h)
local BOOL, INT, NUM, TEXT, ENUM, ROWOF, MODEL = 1, 2, 3, 4, 5, 6, 7
local EV_ACTION, EV_WRITE, EV_CELL = 1, 2, 3
local F_BAND, F_TRACE, F_PAGES = 4, 16, 256
local A_HOOK, A_ROLE_CONFIRM, A_ROLE_BACK = 1, 2, 4
local E_CHECK = -6
local KINDS = { page = 1, group = 2, label = 3, info = 4, link = 5, button = 6, toggle = 7, rows = 8, dialog = 9,
                slider = 11, field = 12, empty = 13, scroll = 16, switch = 22, case = 23, foot = 26, popover = 30,
                row = 32, rule = 41, note = 42 }
local BASE = { bool = BOOL, int = INT, number = NUM, text = TEXT, enum = ENUM, rowof = ROWOF, model = MODEL }
local FIELD = { bool = BOOL, int = INT, number = NUM, text = TEXT, enum = TEXT }
-- a prop whose value is a literal the dump prints as one (its Python repr)
local LIT = { kind = 1, wrap = 1, face = 1, size = 1, align = 1, alone = 1, span = 1, arg = 1, grow = 1, max = 1,
              secret = 1, commit = 1, look = 1, cur = 1, current = 1, check = 1, follow = 1 }
local FIX = "run this app on the reference engine: uitree_app.use_engine(\"reference\")"

local X = nil                   -- the store, made at the first frame
local SLOT, NAMES, TYPES = {}, {}, {}   -- a state's slot by name; the name and declaration by slot
local AIDX, ACTS = {}, {}       -- an action's index by name; the action by index
local PUSHED = {}               -- each state's write count (a derive's value) as last pushed
local LOWERED = nil             -- the page whose tree the store holds
local KEYS = {}                 -- the keys of the lowered page's nodes
local ROWS = false              -- the lowered page lists rows (whose keys are data)

if NEW == nil then
  fail("E00", "load", "the C engine is not in this build", "a build with native/moy_uitree's Lua face", "none", FIX)
end

-- a refusal of the engine's at `at`: a mistake the spec names, or what it does not draw yet
local function check(r, what, at)
  if r >= 0 then return r end
  if r == E_CHECK then fail(X:error_code(), at, what .. ": " .. X:error()) end
  fail("E00", at, "the C engine does not draw " .. what .. " yet (" .. X:error() .. ")", nil, nil, FIX)
end

local function sorted(t)
  local out = {}
  for k in pairs(t) do out[#out + 1] = k end
  table.sort(out)
  return out
end

-- -- declarations -----------------------------------------------------------------

local function declare()
  local names = sorted(DECL)
  for _, name in ipairs(names) do
    local ty = DECL[name]
    local base = BASE[ty.t]
    if not base then fail("E00", "load", "the C engine does not hold the state " .. q(name) .. " (" .. ty.t .. ") yet", nil, nil, FIX) end
    local vals = ty.t == "enum" and table.concat(ty.values, "\0") or nil
    local s = check(X:state(name, base, vals), "the state " .. q(name), "load")
    SLOT[name], NAMES[s], TYPES[s] = s, name, ty
  end
  for _, name in ipairs(names) do
    local ty = DECL[name]
    if ty.t == "model" then
      local types, key = {}, 0
      for i, f in ipairs(ty.order) do
        local ft = FIELD[ty.fields[f].t]
        if not ft or ty.fields[f].opt then
          fail("E00", "load", "the C engine does not hold the field " .. q(f) .. " of " .. q(name) .. " yet", nil, nil, FIX)
        end
        types[i] = string.char(ft)
        if f == ty.key then key = i - 1 end
      end
      check(X:model(SLOT[name], table.concat(ty.order, "\0"), table.concat(types), key), "the model " .. q(name), "load")
    elseif ty.t == "rowof" then
      check(X:row_of(SLOT[name], SLOT[ty.model]), "the row_of " .. q(name), "load")
    end
  end
  for _, name in ipairs(sorted(LABELS)) do
    local t = LABELS[name]
    local keys, words = {}, {}
    for _, k in ipairs(sorted(t)) do keys[#keys + 1] = k; words[#words + 1] = t[k] end
    check(X:labels(name, table.concat(keys, "\0"), table.concat(words, "\0")), "the labels " .. q(name), "load")
  end
  for _, name in ipairs(ACTION_ORDER) do
    local a = ACTIONS[name]
    if a.chars then fail("E00", a.at, "the C engine does not take the action " .. q(name) .. "'s chars yet", nil, nil, FIX) end
    local flags, letters = a.hook and A_HOOK or 0, {}
    for _, k in ipairs(a.keys or {}) do
      if k == "role:confirm" then flags = flags | A_ROLE_CONFIRM
      elseif k == "role:back" then flags = flags | A_ROLE_BACK
      elseif k:match("^%l$") then letters[#letters + 1] = k end
    end
    local off = a.off == "hide" and 1 or a.off == "grey" and 2 or 0
    local i = check(X:action(name, a.label or "", a.hint or "", a.when, off, flags, table.concat(letters), a.icon or ""),
                    "the action " .. q(name), a.at)
    AIDX[name], ACTS[i] = i, a
    if a.row then check(X:action_row(i, SLOT[a.row]), "the action " .. q(name) .. "'s row", a.at) end
  end
end

-- -- state across the line ----------------------------------------------------------

local function cell(ft, v)
  if ft == "bool" then return v and "1" or "0" end
  if ft == "number" then
    local s = tostring(v + 0.0)
    return s
  end
  return tostring(v)
end

local function push(name)
  local s, ty = SLOT[name], DECL[name]
  local v = V[name]
  if ty.derived then
    v = DERIVED[name]()
    if PUSHED[name] == v and PUSHED[name] ~= nil then return end
    PUSHED[name] = v
  else
    PUSHED[name] = VER[name] or 0
  end
  local t = ty.t
  if t == "model" then
    local out = {}
    for _, r in ipairs(v.rows) do
      for _, f in ipairs(ty.order) do out[#out + 1] = cell(ty.fields[f].t, r[f]) end
    end
    local raw = #out > 0 and table.concat(out, "\0") .. "\0" or ""
    check(X:set_model(s, #v.rows, raw), "the model " .. q(name), "frame")
  elseif v == nil and ty.opt then X:set_absent(s)
  elseif t == "bool" then X:set_int(s, v and 1 or 0)
  elseif t == "int" then X:set_int(s, v)
  elseif t == "number" then X:set_num(s, v)
  else X:set_text(s, v == nil and "" or tostring(v)) end
end

-- each state whose writes moved since it was pushed, pushed; each derive whose value moved
local function committed()
  for name, ty in pairs(DECL) do
    if ty.derived or PUSHED[name] ~= (VER[name] or 0) then push(name) end
  end
end

-- a value the engine wrote into slot `s`, into the layer's state
local function pull(s)
  local name, ty = NAMES[s], TYPES[s]
  local v
  if ty.opt and X:absent(s) then v = nil
  elseif ty.t == "bool" then v = X:get_int(s) ~= 0
  elseif ty.t == "int" then v = X:get_int(s)
  elseif ty.t == "rowof" then
    v = X:get_text(s)
    if v == "" then v = nil
    elseif DECL[ty.model].fields[DECL[ty.model].key].t == "int" then v = math.tointeger(tonumber(v)) end
  else v = X:get_text(s) end
  V[name] = v
  touched(name)
  PUSHED[name] = VER[name]
end

-- a row's field the engine wrote (a toggle bound to .field)
local function pull_cell(s, ri, fi)
  local name, ty = NAMES[s], TYPES[s]
  local f = ty.order[fi + 1]
  local ft, raw = ty.fields[f].t, X:get_cell(s, ri, fi)
  local v = raw
  if ft == "bool" then v = raw == "1" elseif ft == "int" then v = math.tointeger(tonumber(raw))
  elseif ft == "number" then v = tonumber(raw) end
  V[name].rows[ri + 1][f] = v
  touched(name)
  PUSHED[name] = VER[name]
end

-- the engine's trace lines, then its events: writes pulled, actions queued
local function drain()
  while true do
    local line = X:trace_next()
    if line == nil then break end
    local kind, name, text = line:match("^(.-)\1(.-)\1(.*)$")
    trace(kind, name, text)
  end
  while true do
    local k, a, m, r, text = X:next()
    if k == nil then break end
    if k == EV_WRITE then pull(a)
    elseif k == EV_CELL then pull_cell(a, m, r)
    elseif k == EV_ACTION then
      local act = ACTS[a]
      local src = text:match("^([^\2]*)")
      if m >= 0 and r >= 0 then
        local model = V[NAMES[m]]
        local row = model.rows[r + 1]
        fire_placed(act.name, src, frozen(row, "row " .. q(row[model.key]) .. " of " .. q(model.name), model.ty))
      else
        fire_placed(act.name, src)
      end
    end
  end
end

-- -- the build ----------------------------------------------------------------------

local function lit(v)
  if type(v) == "boolean" then return v and "True" or "False", true end
  if math.type(v) == "integer" then return tostring(v), true end
  return v, false
end

local function c_key(n)
  local k = n.kind
  if k == "page" then return "page" end
  if k == "button" or k == "link" then return n.o.arg ~= nil and n.action .. "/" .. tostring(n.o.arg) or n.action end
  if k == "toggle" or k == "slider" or k == "field" then return n.a[2] end
  if k == "rows" then return n.a[1] end
  if k == "switch" then return "switch:" .. n.a[1] end
  if k == "foot" then return "foot" end
  return n.o.key or n.rkey
end

-- the props a kind takes from its head, in the order the Python layer gives them, after the rest
local HEADS = {
  page = function(n) return { { "title", n.a[1] } } end,
  group = function(n) return { { "caption", n.a[1] } } end,
  label = function(n) return { { "text", n.a[1] } } end,
  info = function(n) return { { "label", n.a[1] }, { "value", n.o.value or "" } } end,
  link = function(n) return { { "value", n.o.value or "" } } end,
  button = function(n) return { { "kind", n.o.kind or "normal" } } end,
  toggle = function(n) return { { "label", n.a[1] } } end,
  slider = function(n) return { { "label", n.a[1] }, { "lo", n.o.lo }, { "hi", n.o.hi } } end,
  field = function(n) return { { "label", n.a[1] }, { "max", n.o.max or 32 }, n.o.commit and { "commit", true } or nil } end,
  empty = function(n) return { { "title", n.a[1] } } end,
  note = function(n) return { { "lines", n.o.lines }, { "icon", n.o.icon or "" } } end,
  dialog = function(n) return { { "title", n.a[1] } } end,
}
local SKIP = { key = 1, value = 1, kind = 1, label = 1, lo = 1, hi = 1, max = 1, commit = 1, lines = 1, icon = 1,
               buttons = 1, by = 1 }
local KEEP = { label = { link = 1, button = 1 }, icon = { label = 1, info = 1, link = 1, button = 1, slider = 1, empty = 1 },
               lines = { label = 1, empty = 1 } }

local function lower_node(n, parent, path)
  local kind = KINDS[n.kind]
  if not kind then fail("E00", n.at, "the C engine does not draw ui." .. n.kind .. " yet", nil, nil, FIX) end
  if n.kind == "rows" and n.o.by ~= nil then fail("E00", n.at, "the C engine does not draw ui.rows by= yet", nil, nil, FIX) end
  local key = c_key(n)
  KEYS[key] = true
  local me = check(X:node(kind, key, parent), "ui." .. n.kind .. " " .. q(key), n.at)
  local function prop(k, v)
    if v == nil then return end
    if type(v) == "table" then
      for _, line in ipairs(v) do check(X:prop(me, k, line, nil), "ui." .. n.kind .. " " .. k .. "=" .. q(line), n.at) end
      return
    end
    local src, islit = lit(v)
    local rep = (islit or LIT[k]) and (type(v) == "string" and "'" .. v .. "'" or src) or nil
    check(X:prop(me, k, src, rep), "ui." .. n.kind .. " " .. k .. "=" .. q(src), n.at)
  end
  if n.kind == "toggle" or n.kind == "slider" or n.kind == "field" then
    check(X:prop(me, "bind", n.a[2], nil), "ui." .. n.kind .. " " .. q(n.a[2]), n.at)
  elseif n.kind == "rows" then
    check(X:prop(me, "bind", n.a[1], nil), "ui.rows " .. q(n.a[1]), n.at)
    ROWS = true
  end
  for _, k in ipairs(sorted(n.o)) do
    if not SKIP[k] or (KEEP[k] and KEEP[k][n.kind]) then
      prop(k, n.o[k])
    end
  end
  local heads = HEADS[n.kind]
  if heads then
    local h = heads(n)
    for i = 1, 3 do if h[i] then prop(h[i][1], h[i][2]) end end
  end
  if n.action then check(X:node_action(me, AIDX[n.action]), "ui." .. n.kind .. " " .. q(key) .. "'s action", n.at) end
  if n.kind == "dialog" then
    for _, b in ipairs(n.o.buttons) do check(X:node_action(me, AIDX[b]), "ui.dialog's button " .. q(b), n.at) end
  end
  if n.kind == "popover" and type(n.o.anchor) == "string" and not n.o.anchor:find("{", 1, true) then
    n.anchor_key = n.o.anchor
  end
  local kids = n.body and n.body.kids or n.kids
  for _, k in ipairs(kids) do lower_node(k, me, path .. "/" .. key) end
  if n.built then
    local ty = DECL[n.a[1]]
    for _, v in ipairs(ty.t == "enum" and ty.values or { "true", "false" }) do
      local c = n.built[v]
      local cv = v == "true" and "True" or v == "false" and "False" or v
      local cm = check(X:node(KINDS.case, "case:" .. cv, me), "ui.switch case " .. q(v), n.at)
      for _, k in ipairs(c.kids) do lower_node(k, cm, path .. "/" .. v) end
    end
  end
end

-- a popover's anchor names a node of the page (N06): the rows' keys are data, so a page with rows is not held to it
local function anchors(n)
  if n.anchor_key and not KEYS[n.anchor_key] and not ROWS then
    fail("N06", n.at, "the popover " .. q(n.a[1]) .. "'s anchor " .. q(n.anchor_key) .. " names no node on the page",
         "a node's key", q(n.anchor_key), "anchor = the key of a node the page draws")
  end
  for _, k in ipairs(n.kids) do anchors(k) end
end

local function lower(p)
  KEYS, ROWS = {}, false
  X:build_begin(string.format("%d %s", #stack, p.def.name):sub(1, 31))
  lower_node(p.root, -1, p.def.name)
  check(X:build_end(), "the page " .. q(p.def.name), p.def.at)
  anchors(p.root)
end

-- -- the frame -----------------------------------------------------------------------

local function made()
  if X then return X end
  X = NEW()
  declare()
  for name in pairs(DECL) do push(name) end
  X:open()
  return X
end

-- Back on the bar: a shown dialog's first button, a shown popover's close=,
-- the page under this one, else the app's back role
local function back()
  if X:dialog() >= 0 then
    local a, key = X:back_dialog()
    if a >= 0 then fire(ACTS[a].name, "back on dialog " .. q(key)) end
    return
  end
  local a, key = X:back_popover()
  if a ~= -2 then
    if a >= 0 then fire(ACTS[a].name, "back on popover " .. q(key)) end
    return
  end
  if #stack > 1 then fire_fn(T.pop, "back", "the bar's back") return end
  local name = KEYMAP["role:back"]
  if name and X:can(AIDX[name], -1) then fire(name, "role back") end
end

local function wants_back()
  return #stack > 1 or X:dialog() >= 0 or X:popover() ~= nil or KEYMAP["role:back"] ~= nil
end

local ROLE_KEYS = { more = function() return btnp("y") or keyp(109) end }

I.BACK.frame = function(p)
  made()
  if LOWERED ~= p then
    if not p.root then build_page(p) end
    lower(p)
    LOWERED = p
    if p.refocus then X:want_focus(p.refocus); p.refocus = nil end
  end
  committed()
  local pf = R.pending_focus
  if pf then
    R.pending_focus = nil
    if not KEYS[pf.key] and not (ROWS and pf.key:find("/", 1, true)) then
      fail("N08", pf.at, "ui.focus(" .. q(pf.key) .. ") names no node on the page", "a node's key", q(pf.key))
    end
    X:want_focus(pf.key)
  end
  -- the bar's title is the app's (its window's): the page's title is the page's to draw
  if E.bar("", {}, {}, wants_back()) == -1 then back() end
  local r = X:frame(F_BAND | F_TRACE | math.min(255, #stack) * F_PAGES)
  if r < 0 then fail("X01", "frame", "the C engine's frame found no run open", "a run", "none") end
  drain()
  R.focused = X:focused()
  for role, pressed in pairs(ROLE_KEYS) do
    local name = KEYMAP["role:" .. role]
    if name and pressed() and X:can(AIDX[name], -1) then fire(name, "key " .. role .. " on " .. q(p.def.name)) end
  end
end

-- the store's tree as text (the reference's snapshot): the page the last frame drew
function T._dump() return X and X:dump() or "" end
