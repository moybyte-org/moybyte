-- The UI tree API for Lua apps (docs: the UI API specification, revision 3):
-- an app declares typed state, label tables, derives, named actions and hooks
-- at its top level, and registers each page as a build function that
-- describes nodes and reads no state. Templates and conditions are compiled
-- and type-checked at the call that holds them; every mistake is an error
-- with a code, the app's file and line, the fix, and what was expected and
-- found. A back end draws the pages: uitree_engine.lua lowers each built page
-- into the C engine (native/moy_uitree), which lays it out, draws it, moves
-- focus and hands back what the user did; uitree_replay.lua replays the tree
-- into today's `ui` verbs every frame instead, for the kinds the C engine does
-- not draw yet (bubble, rows by=: Moy Mesh's ports, their parity check alone).
--
-- The Player loads this chunk, then one back end, ahead of a cart that
-- declares the extension "moybyte.uitree" (runtime/run/lua_ext.py); it takes
-- the engine's `ui` as E and installs its own. tools/uitree_lua_check.py runs
-- it alone over a stub engine in a host Lua with `debug`, where `ui._check`
-- builds every page.

local E = ui
local T = {}
local HAS_DEBUG = type(debug) == "table" and type(debug.getinfo) == "function"

local DECL, V, NAMES, LABELS, ACTIONS, PAGES, PAGE_ORDER = {}, {}, {}, {}, {}, {}, {}
local ACTION_ORDER = {}     -- the actions' and hooks' names in the order they are declared
local DERIVES, DERIVED, ENGINE_OWNED, KEYMAP = {}, {}, {}, {}
local SYS = { desk = false, handheld = true, tall = false, wide = false, focus = "" }
local APP = nil
local stack, queue, timers, posted = {}, {}, {}, {}
local building, parent, in_handler, ROWCTX = nil, nil, nil, nil
local frame_no, GEN, sealed = 0, 0, false
-- what the back end and the commands share: the id focus is on after the
-- last frame, the replay's ids drawn, a focus a handler asked for
local R = { focused = nil, ids = {}, pending_focus = nil }
local VER = {}              -- a state's writes, counted: the C back end pushes what moved
local USES = {}
local STATE_AT = {}
T.stats = { builds = 0, frames = 0, handlers = 0 }

-- -- errors ------------------------------------------------------------------------

-- the first stack frame outside this file, as "file:line"; no debug library is
-- needed: error()'s own position prefix is read at each level. The Player names
-- a cart's main chunk "cart"; it is main.lua to its author.
local function where()
  for l = 2, 60 do
    local _, m = pcall(error, "", l)
    if type(m) == "string" and m ~= "" and not m:find("uitree%.lua:") then
      m = m:gsub(":%s*$", "")
      return (m:gsub("^cart:", (rawget(_G, "UI_MAIN") or "main.lua") .. ":"))
    end
  end
  return "?"
end

-- error[CODE] WHERE: WHAT; fix: FIX (expected E, found F). The fix comes before
-- expected and found: the console cuts a cart's error at about 200 characters.
-- a code's fix where its site names none: every error says what to do
local FIXES = {
  A01 = "name an action declared with ui.action", A02 = "declare takes = \"row\", or hand it what it takes",
  A03 = "give the handler one parameter per thing its takes hands it", A08 = "a label a person reads",
  A09 = "a word: ui.action(\"add\", \"Add one\", add)", A10 = "call ui.after from a handler or the opened hook",
  B01 = "bind it: a template \"{name}\" or a condition", B02 = "write state in a handler",
  B03 = "declare it at the top level of the app", C01 = "a condition text, such as \"done && !busy\"",
  C02 = "a ui.derive for each part", C03 = "a ui.derive for the bracketed part",
  D01 = "one ui.app for the app", D02 = "size = { w, h } in desk units", D04 = "ui.app(\"TITLE\", { icon = \"...\" })",
  E01 = "ui.enum(..., { init = \"...\" })", E02 = "an init that is one of the values", E03 = "values that are texts",
  G01 = "a derive reads state, or derives that read no cycle", H01 = "an action, which a person sees",
  H03 = "takes alone on a hook", I02 = "an icon's name", K01 = "a role of the app's, such as role:more",
  K02 = "one key, one action", K03 = "a list of keys", N01 = "a prop the node has",
  N02 = "a key per node: key = \"...\"", N03 = "rows at the page's level, and the row's parts in its body",
  N04 = "the verb or prop the error names", N08 = "a node's key on the page shown",
  O01 = "off = \"hide\" or off = \"grey\"", O02 = "drop off, or give the action a when",
  O04 = "when and off on the action", P01 = "ui.page(NAME, BUILD) for it", P02 = "drop takes; the page's arg is its row",
  P03 = "a push action has no handler", P04 = "arg names a ui.row_of state",
  P05 = "Back leaves the app; ui.pop() closes a page ui.push() opened", S01 = "declare it in ui.state",
  S02 = "a value of the field's type", S03 = "replace the whole value", S04 = "a declared field",
  S05 = "a table of the declared fields", S06 = "write state in a handler", S07 = "ui.model { key = \"id\", id = \"\", ... }",
  S09 = "write the names the derive reads", S10 = "read it; the engine writes it", S11 = "ui.row_of(\"MODEL\")",
  S13 = "ui.opt(...) for a value that may be absent", T01 = "a transform the template language has",
  T02 = "a system value: @desk, @handheld, @tall, @wide or @focus", T03 = "read a field of a record or a row_of",
  T04 = "a label for every value", T05 = "a row's field inside the rows' body", U02 = "rename one",
  W01 = "an action that takes nothing, reading the state", X01 = "report it: the layer missed a check",
}

local function fail(code, at, what, expected, found, fix)
  local msg = "error[" .. code .. "] " .. (at or where()) .. ": " .. what
  fix = fix or FIXES[code]
  if fix then msg = msg .. "; fix: " .. fix end
  if expected then msg = msg .. " (expected " .. expected .. (found and ", found " .. found or "") .. ")"
  elseif found then msg = msg .. " (found " .. found .. ")" end
  error(msg, 0)
end

local function q(s) return "'" .. tostring(s) .. "'" end

local function describe(v)
  local t = type(v)
  if v == nil then return "nil" end
  if t == "boolean" then return "the bool " .. tostring(v) end
  if t == "number" then return (math.type(v) == "integer" and "the int " or "the number ") .. tostring(v) end
  if t == "string" then return "the text \"" .. v .. "\"" end
  if t == "function" then return "a function" end
  return "a table"
end

local function sorted_keys(t, pre)
  local ks = {}
  for k in pairs(t) do ks[#ks + 1] = (pre or "") .. tostring(k) end
  table.sort(ks)
  return ks
end

-- the nearest of `names` to `k`: a shared prefix or one edit apart
local function near(k, names)
  local best, bd = nil, 3
  for _, n in ipairs(names) do
    local d
    if n:sub(1, 3) == k:sub(1, 3) and #k > 2 then d = 1
    elseif math.abs(#n - #k) <= 1 then
      d = 0
      local i, j = 1, 1
      while i <= #n and j <= #k do
        if n:sub(i, i) ~= k:sub(j, j) then
          d = d + 1
          if #n > #k then i = i + 1 elseif #k > #n then j = j + 1 else i, j = i + 1, j + 1 end
        else i, j = i + 1, j + 1 end
      end
      d = d + (#n - i + 1) + (#k - j + 1)
    end
    if d and d < bd then best, bd = n, d end
  end
  return best
end

local function no_build(what, at)
  if building then
    fail("B03", at, what .. " inside the build of " .. q(building), "declarations at the module's top level",
         "a build", "declare it at the top level, beside ui.state")
  end
end

-- -- the trace: a ring of events and writes --------------------------------------

local RING, RING_N, ring_at = {}, 400, 0
local function stamp()
  local ms = frame_no * 1000 // 30
  return string.format("%02d:%02d:%02d.%03d", ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
end
local function trace(kind, name, detail)
  local line = string.format("%s  %-6s  %-8s %s", stamp(), kind, name, detail or "")
  ring_at = ring_at % RING_N + 1
  RING[ring_at] = line
  if moybyte and moybyte.log then moybyte.log(6, "[trace] " .. line) end
end
function T.trace_dump()
  local out = {}
  for i = 1, RING_N do
    local l = RING[(ring_at + i - 1) % RING_N + 1]
    if l then out[#out + 1] = l end
  end
  return out
end

-- -- types -------------------------------------------------------------------------

local EnumMT, RecordMT, ModelDeclMT, RowOfMT, ModelMT, TypeMT = {}, {}, {}, {}, {}, {}
ModelMT.__index = ModelMT
local BOOL, INT, NUM, TEXT = { t = "bool" }, { t = "int" }, { t = "number" }, { t = "text" }

local function tname(ty)
  local s
  if ty.t == "enum" then
    local v = ty.values
    s = #v > 6 and "an enum of " .. #v .. " values (" .. v[1] .. ", " .. v[2] .. ", ..., " .. v[#v] .. ")"
        or "an enum (" .. table.concat(v, ", ") .. ")"
  elseif ty.t == "rowof" then s = "a row of " .. q(ty.model)
  else
    s = ({ bool = "a bool", int = "an int", number = "a number", text = "a text", record = "a record",
           model = "a model" })[ty.t]
  end
  return s .. (ty.opt and " (optional)" or "")
end

local function zero(ty)
  if ty.t == "bool" then return false elseif ty.t == "int" then return 0
  elseif ty.t == "number" then return 0.0 elseif ty.t == "text" then return "" end
  return nil
end

function T.enum(...)
  local at = where()
  local args = table.pack(...)
  local o = {}
  if type(args[args.n]) == "table" then o = args[args.n]; args.n = args.n - 1 end
  local values, set = {}, {}
  for i = 1, args.n do
    local v = args[i]
    if type(v) ~= "string" or v == "" then
      fail("E03", at, "an enum's value is " .. describe(v), "non-empty texts", describe(v))
    end
    if set[v] then fail("E03", at, "the enum names " .. q(v) .. " twice", "distinct values", q(v) .. " twice") end
    set[v] = true
    values[#values + 1] = v
  end
  if #values < 2 then
    fail("E03", at, "an enum of " .. #values .. " value" .. (#values == 1 and "" or "s"), "two or more values",
         tostring(#values), "a bool, or a second value")
  end
  for k in pairs(o) do
    if k ~= "init" then fail("N01", at, "ui.enum takes no option " .. q(k), "init", q(k)) end
  end
  if o.init == nil then
    fail("E01", at, "the enum (" .. table.concat(values, ", ") .. ") has no init", "{ init = ... }", "none",
         "ui.enum(\"" .. table.concat(values, "\", \"") .. "\", { init = \"" .. values[1] .. "\" })")
  end
  if not set[o.init] then
    fail("E02", at, "the enum's init " .. describe(o.init) .. " is not one of its values",
         "one of " .. table.concat(values, ", "), describe(o.init))
  end
  return setmetatable({ t = "enum", values = values, set = set, init = o.init }, EnumMT)
end

local typeof
local function field_types(t, what, at)
  local fields, order = {}, {}
  for k, v in pairs(t) do
    if type(k) ~= "string" then
      fail("N01", at, what .. " has a positional entry", "name = initial value", describe(v), "name every field")
    end
    local ty = typeof(v, what .. " field " .. q(k), at)
    if ty.t == "record" or ty.t == "model" or ty.t == "rowof" then
      fail("S13", at, what .. " field " .. q(k) .. " is " .. tname(ty), "a bool, int, number, text or enum",
           tname(ty), "flatten it into fields")
    end
    fields[k] = ty
    order[#order + 1] = k
  end
  table.sort(order)
  return fields, order
end

-- an optional: absent until written, nothing in a template, false in a
-- condition, nil to a handler
function T.opt(v)
  local ty = typeof(v, "ui.opt", where())
  local c = {}
  for k, x in pairs(ty) do c[k] = x end
  c.opt = true
  return setmetatable(c, getmetatable(ty) == EnumMT and EnumMT or TypeMT)
end

function T.record(t)
  local at = where()
  if type(t) ~= "table" or getmetatable(t) then fail("N01", at, "ui.record takes a table", "ui.record { who = \"\", ... }", describe(t)) end
  local fields, order = field_types(t, "ui.record", at)
  return setmetatable({ t = "record", fields = fields, order = order }, RecordMT)
end

function T.model(t)
  local at = where()
  if type(t) ~= "table" or type(t.key) ~= "string" then
    fail("S07", at, "ui.model names no key field", "ui.model { key = \"id\", id = \"\", ... }",
         describe(t and t.key), "ui.model { key = \"id\", id = \"\" }")
  end
  local key = t.key
  local rest = {}
  for k, v in pairs(t) do if k ~= "key" then rest[k] = v end end
  local fields, order = field_types(rest, "ui.model", at)
  if not fields[key] then
    fail("S07", at, "the model's key " .. q(key) .. " is not one of its fields", "a field named " .. q(key),
         "fields " .. table.concat(order, ", "), "add " .. key .. " = \"\" (or 0)")
  end
  if (fields[key].t ~= "text" and fields[key].t ~= "int") or fields[key].opt then
    fail("S07", at, "the model's key " .. q(key) .. " is " .. tname(fields[key]), "a text or an int", tname(fields[key]))
  end
  return setmetatable({ t = "model", key = key, fields = fields, order = order }, ModelDeclMT)
end

function T.row_of(model)
  if type(model) ~= "string" then fail("S11", where(), "ui.row_of names " .. describe(model), "a model's name", describe(model)) end
  return setmetatable({ t = "rowof", model = model }, RowOfMT)
end

function typeof(v, what, at)
  local tv = type(v)
  if tv == "boolean" then return BOOL end
  if tv == "number" then return math.type(v) == "integer" and INT or NUM end
  if tv == "string" then return TEXT end
  local mt = getmetatable(v)
  if mt == EnumMT or mt == RecordMT or mt == ModelDeclMT or mt == RowOfMT or mt == TypeMT then return v end
  fail("S13", at, what .. " has no type: " .. describe(v), "a bool, int, number, text, ui.enum, ui.record, "
       .. "ui.model, ui.row_of or ui.opt", describe(v), "ui.opt(0) for a value that may be absent")
end

-- does v suit ty? nil, or a description of what was found
local function mismatch(ty, v)
  if v == nil and ty.opt then return nil end
  local t = ty.t
  if t == "bool" then return type(v) ~= "boolean" and describe(v) or nil end
  if t == "int" then return math.type(v) ~= "integer" and describe(v) or nil end
  if t == "number" then return type(v) ~= "number" and describe(v) or nil end
  if t == "text" then return type(v) ~= "string" and describe(v) or nil end
  if t == "enum" then return (type(v) ~= "string" or not ty.set[v]) and describe(v) or nil end
  return describe(v)
end

-- a row or a record written whole: every non-optional field, no other (S05);
-- each of its type (S02)
local function check_fields(ty, row, what, at)
  if type(row) ~= "table" then
    fail("S05", at, what .. " is not a table", "{ " .. table.concat(ty.order, ", ") .. " }", describe(row))
  end
  for _, f in ipairs(ty.order) do
    local v = row[f]
    if v == nil and not ty.fields[f].opt then
      fail("S05", at, what .. " has no field " .. q(f), "every field: " .. table.concat(ty.order, ", "), "none",
           "give " .. f .. " a value (" .. tname(ty.fields[f]) .. "), or declare it ui.opt(...)")
    end
    local bad = mismatch(ty.fields[f], v)
    if bad then fail("S02", at, what .. " field " .. q(f) .. " is " .. tname(ty.fields[f]), tname(ty.fields[f]), bad) end
  end
  local raw = getmetatable(row) and getmetatable(row).__raw or row
  for k in pairs(raw) do
    if not ty.fields[k] then
      fail("S05", at, what .. " has a field " .. q(k) .. " nothing declares", "only " .. table.concat(ty.order, ", "),
           q(k), "declare it, or drop it")
    end
  end
end

-- a read-only view of a record or a row, as a handler is handed it
local function frozen(t, what, ty)
  return setmetatable({}, {
    __raw = t,
    __index = function(_, k)
      local v = t[k]
      if v == nil and not (ty and ty.fields[k]) then
        fail("S04", nil, what .. " has no field " .. q(k), "a declared field", q(k))
      end
      return v
    end,
    __newindex = function(_, k, v)
      fail("S03", nil, "a write into " .. what .. "." .. tostring(k), "the whole value replaced",
           "a write into a field", what:match("^row") and "s.MODEL:update(key, { " .. tostring(k) .. " = ... })"
           or "s." .. what .. " = { every field, with " .. tostring(k) .. " = " .. describe(v) .. " }")
    end,
    __pairs = function() return next, t, nil end,
    __len = function() return #t end,
  })
end
local function raw(v) local m = getmetatable(v); return m and m.__raw or v end

-- -- state -------------------------------------------------------------------------

local function undeclared(k, at)
  local names = sorted_keys(DECL)
  local n = near(k, names)
  fail("S01", at, q(k) .. " is not declared in ui.state", "one of " .. table.concat(names, ", "), q(k),
       n and "did you mean " .. q(n) .. "?" or "declare it in ui.state { ... } with its initial value")
end

local function model_of(name) return V[name] end

local function show(x)
  if type(x) == "string" then return "\"" .. x .. "\"" end
  if type(x) == "boolean" then return x and "True" or "False" end
  return x == nil and "(empty)" or tostring(x)
end
local function diff(order, a, b)
  local out = {}
  for _, f in ipairs(order) do
    if a[f] ~= b[f] then out[#out + 1] = f .. " " .. show(a[f]) .. " -> " .. show(b[f]) end
  end
  return #out > 0 and table.concat(out, ", ") or nil
end

local function writable(k, at)
  if not in_handler then
    fail("S06", at, "a write to " .. q(k) .. " outside a handler", "a write in a handler",
         sealed and "a write from a service or a callback" or "a write at the module's top level",
         sealed and "ui.post(\"HOOK\", arg) from the service, and write it in that hook"
         or "write it in the opened hook: ui.hook(\"opened\", function() ... end)")
  end
end

local function touched(k) GEN = GEN + 1; VER[k] = (VER[k] or 0) + 1 end

local function setv(k, v, why, at)
  local ty = DECL[k]
  if not ty then undeclared(k, at) end
  if ty.derived then
    fail("S09", at, q(k) .. " is a derive, and nobody writes it", "a write to a name it reads", "a write",
         "write the names it reads: " .. ty.derived)
  end
  if ENGINE_OWNED[k] then
    fail("S10", at, q(k) .. " is written by the engine (" .. ENGINE_OWNED[k] .. ")", "a read", "a write",
         "read it; the engine writes it")
  end
  if building then
    fail("B02", at, "the build of " .. q(building) .. " writes " .. q(k), "a build that only describes",
         "a write", "name an action the node fires: { on = \"add\" }")
  end
  local old = V[k]
  if ty.t == "model" then
    fail("S03", at, q(k) .. " is a model, written by its verbs", "s." .. k .. ":set(rows)", "an assignment",
         "s." .. k .. ":set(rows), :put(row), :update(key, fields) or :remove(key)")
  elseif ty.t == "record" then
    if v == nil and not ty.opt then
      fail("S02", at, "the record " .. q(k) .. " cannot be absent", "every field", "nil",
           "declare it ui.opt(ui.record { ... }) to let it be absent")
    end
    if v ~= nil then
      check_fields(ty, v, "the record " .. q(k), at)
      local c = {}
      for f, x in pairs(raw(v)) do c[f] = x end
      v = c
    end
  elseif ty.t == "rowof" then
    local m = model_of(ty.model)
    if type(v) == "table" then v = v[m.key] end
    if v ~= nil and not m.index[v] then
      fail("S02", at, q(k) .. " is a row of " .. q(ty.model) .. " and no row is keyed " .. describe(v),
           "a row, a key of a row, or nil", describe(v))
    end
    if v == nil and not ty.opt then
      fail("S02", at, q(k) .. " is " .. tname(ty) .. " and cannot be nil", tname(ty), "nil",
           "declare it ui.opt(ui.row_of(\"" .. ty.model .. "\"))")
    end
  else
    if v == nil and not ty.opt then
      fail("S02", at, q(k) .. " is " .. tname(ty) .. " and cannot be nil", tname(ty), "nil",
           ty.t == "bool" and "false" or ty.t == "text" and "\"\"" or "declare it ui.opt(...)")
    end
    local bad = mismatch(ty, v)
    if bad then
      local fix = nil
      if ty.t == "int" and type(v) == "number" then fix = "math.tointeger(x) or x // 1"
      elseif ty.t == "int" or ty.t == "number" then fix = "tonumber(x)"
      elseif ty.t == "text" then fix = "tostring(x)"
      elseif ty.t == "enum" then fix = "one of " .. table.concat(ty.values, ", ") end
      fail("S02", at, q(k) .. " is " .. tname(ty), tname(ty), bad, fix)
    end
  end
  local what
  if ty.t == "record" and old ~= nil and v ~= nil then
    what = diff(ty.order, old, v)
    if not what then return end
  elseif ty.t == "record" then
    if old == v then return end
    what = old == nil and "(empty) -> {" .. (diff(ty.order, {}, v) or ""):gsub("%(empty%) %-> ", "") .. "}"
           or "-> (empty)"
  else
    if old == v then return end
    what = show(old) .. " -> " .. show(v)
  end
  writable(k, at)
  V[k] = v
  touched(k)
  trace("state", k, what .. "   (in " .. (why or in_handler) .. ")")
end

local SYS_T = { desk = BOOL, handheld = BOOL, tall = BOOL, wide = BOOL, focus = TEXT }
local RETIRED_SYS = { w = 1, h = 1, fs = 1, symbols = 1 }

local function no_read_in_build(what, at)
  if building then
    fail("B01", at, "the build of " .. q(building) .. " reads " .. what,
         "a template, a condition, a switch or rows naming it", what .. " read in Lua",
         "write \"{NAME}\" in a template, or shown = \"NAME\"")
  end
end

local function getv(k, at)
  local ty = DECL[k]
  if not ty then undeclared(k, at) end
  no_read_in_build(q(k), at)
  if ty.derived then return DERIVED[k]() end
  local v = V[k]
  if ty.t == "record" then return v and frozen(v, k, ty) or nil end
  if ty.t == "rowof" then
    if v == nil then return nil end
    local m = model_of(ty.model)
    return frozen(m.rows[m.index[v]], "row " .. q(v) .. " of " .. q(ty.model), m.ty)
  end
  return v
end

local S = setmetatable({}, {
  __index = function(_, k) return getv(k) end,
  __newindex = function(_, k, v) setv(k, v) end,
})

local function an(w) return (w:match("^[aeiou]") and "an " or "a ") .. w end
local function claim(name, what, at)
  if NAMES[name] then
    fail("U02", at, q(name) .. " is declared twice", "one name, one thing",
         an(what) .. " and " .. an(NAMES[name].what) .. " (" .. NAMES[name].at .. ")", "rename one")
  end
  NAMES[name] = { what = what, at = at }
end

-- -- models --------------------------------------------------------------------------

local function index_rows(m)
  m.index = {}
  for i, r in ipairs(m.rows) do m.index[r[m.key]] = i end
end
local function copy_row(m, r, at, what)
  check_fields(m.ty, r, what, at)
  local c = {}
  for f, v in pairs(raw(r)) do c[f] = v end
  return c
end
local function model_write(m, at)
  writable(m.name, at)
  if building then fail("B02", at, "the build of " .. q(building) .. " writes " .. q(m.name), "no write", "a model write") end
end
local function model_changed(m, detail)
  touched(m.name)
  trace("state", m.name, detail .. "   (in " .. (in_handler or "?") .. ")")
end

function ModelMT:set(rows)
  local at = where()
  model_write(self, at)
  local out, seen = {}, {}
  for i, r in ipairs(rows) do
    local c = copy_row(self, r, at, "row " .. i .. " of " .. q(self.name))
    local k = c[self.key]
    if seen[k] then fail("S05", at, "two rows of " .. q(self.name) .. " keyed " .. describe(k), "unique keys", describe(k) .. " twice") end
    seen[k] = true
    out[i] = c
  end
  local add, gone, chg, ch = 0, 0, 0, {}
  for _, r in ipairs(out) do
    local i = self.index[r[self.key]]
    if not i then add = add + 1
    else
      local d = diff(self.ty.order, self.rows[i], r)
      if d then chg = chg + 1; ch[#ch + 1] = show(r[self.key]) .. ": " .. d end
    end
  end
  for _, r in ipairs(self.rows) do if not seen[r[self.key]] then gone = gone + 1 end end
  -- moved: kept rows whose place among the kept rows changed
  local was, now_, moved = {}, {}, 0
  for _, r in ipairs(self.rows) do if seen[r[self.key]] then was[#was + 1] = r[self.key] end end
  for _, r in ipairs(out) do if self.index[r[self.key]] then now_[#now_ + 1] = r[self.key] end end
  for i, k in ipairs(now_) do if was[i] ~= k then moved = moved + 1 end end
  if add == 0 and gone == 0 and chg == 0 and moved == 0 and #out == #self.rows then return end
  self.rows = out
  index_rows(self)
  model_changed(self, (add + gone + moved == 0 and chg <= 2 and #ch > 0) and table.concat(ch, "; ")
                or "[" .. #out .. " rows] +" .. add .. " -" .. gone .. " ~" .. chg .. " moved " .. moved)
end
function ModelMT:put(row)
  local at = where()
  model_write(self, at)
  local c = copy_row(self, row, at, "the row put into " .. q(self.name))
  local i = self.index[c[self.key]]
  if i then self.rows[i] = c else self.rows[#self.rows + 1] = c end
  index_rows(self)
  model_changed(self, "put " .. show(c[self.key]))
end
function ModelMT:update(key, fields)
  local at = where()
  model_write(self, at)
  local i = self.index[key]
  if not i then fail("S05", at, q(self.name) .. " has no row keyed " .. describe(key), "a key of a row", describe(key)) end
  local c = {}
  for f, v in pairs(self.rows[i]) do c[f] = v end
  for f, v in pairs(fields) do
    if f == self.key then fail("S05", at, "an update of " .. q(self.name) .. " changes its key", "other fields", q(f), "remove the row and put a new one") end
    c[f] = v
  end
  c = copy_row(self, c, at, "the row " .. describe(key) .. " of " .. q(self.name) .. " as updated")
  local d = diff(self.ty.order, self.rows[i], c)
  if not d then return end
  self.rows[i] = c
  model_changed(self, show(key) .. ": " .. d)
end
function ModelMT:remove(key)
  local at = where()
  model_write(self, at)
  local i = self.index[key]
  if not i then fail("S05", at, q(self.name) .. " has no row keyed " .. describe(key), "a key of a row", describe(key)) end
  table.remove(self.rows, i)
  index_rows(self)
  model_changed(self, "remove " .. show(key))
end
function ModelMT:find(key)
  local i = self.index[key]
  return i and frozen(self.rows[i], "row " .. q(key) .. " of " .. q(self.name), self.ty) or nil
end
function ModelMT:at(i)
  if math.type(i) ~= "integer" then fail("S02", where(), q(self.name) .. ":at takes an int", "an int", describe(i)) end
  if i < 0 then i = #self.rows + 1 + i end
  local r = self.rows[i]
  return r and frozen(r, "row " .. q(r[self.key]) .. " of " .. q(self.name), self.ty) or nil
end
function ModelMT:count() return #self.rows end
ModelMT.__newindex = function(m, k) fail("S03", where(), "a write into the model " .. q(m.name) .. " by index", "its verbs", "s." .. m.name .. "[" .. tostring(k) .. "] = ...", "s." .. m.name .. ":put(row) or :update(key, fields)") end

function T.state(init)
  local at = where()
  no_build("ui.state", at)
  if type(init) ~= "table" or getmetatable(init) then fail("N01", at, "ui.state takes a table", "ui.state { name = initial, ... }", describe(init)) end
  STATE_AT[#STATE_AT + 1] = at
  for k, v in pairs(init) do
    if type(k) ~= "string" then fail("S13", at, "ui.state has a positional entry", "name = initial", describe(v)) end
    local ty = typeof(v, "state " .. q(k), at)
    claim(k, "state", at)
    DECL[k] = ty
    if ty.opt then V[k] = nil
    elseif ty.t == "enum" then V[k] = ty.init
    elseif ty.t == "record" then
      local z = {}
      for f, fty in pairs(ty.fields) do
        if fty.t == "enum" then z[f] = not fty.opt and fty.init or nil
        elseif not fty.opt then z[f] = zero(fty) end
      end
      V[k] = z
    elseif ty.t == "rowof" then V[k] = nil
    elseif ty.t == "model" then
      V[k] = setmetatable({ name = k, ty = ty, key = ty.key, rows = {}, index = {} }, ModelMT)
    else V[k] = v end
  end
  return S
end

function T.labels(name, t)
  local at = where()
  no_build("ui.labels", at)
  if type(name) ~= "string" or type(t) ~= "table" then
    fail("N01", at, "ui.labels takes a name and a table", "ui.labels(\"TABS\", { code = \"Code\" })", describe(name))
  end
  if LABELS[name] then fail("U02", at, "the labels " .. q(name) .. " are declared twice", "one table per name", "two") end
  local c = {}
  for k, v in pairs(t) do
    if type(v) ~= "string" or (type(k) ~= "string" and type(k) ~= "boolean") then
      fail("N01", at, "ui.labels(" .. q(name) .. ") maps " .. describe(k) .. " to " .. describe(v), "a value = a text", describe(v))
    end
    c[tostring(k)] = v
  end
  LABELS[name] = c
end

-- -- the small language: templates, conditions, derives --------------------------------

local TPL, COND, CHECKED = {}, {}, {}
local DEPS = nil           -- the names a derive's source reads, while it compiles

local function resolve(name, ctx, at, src)
  local c = name:sub(1, 1)
  if c == "@" then
    local w = name:sub(2)
    if RETIRED_SYS[w] then
      fail("N04", at, q(src) .. " reads the retired " .. q(name), "a size class", q(name),
           "@desk, @handheld, @wide or @tall")
    end
    local ty = SYS_T[w]
    if not ty then
      fail("T02", at, q(src) .. " names the system value " .. q(name), "one of " .. table.concat(sorted_keys(SYS_T, "@"), ", "), q(name))
    end
    return ty
  end
  if c == "." then
    if not ctx then
      fail("T05", at, q(src) .. " reads the row's field " .. q(name) .. " outside a row", "a state name or record.field",
           q(name), "{NAME.field}; {.field} only inside ui.rows")
    end
    local f = name:sub(2)
    local ty = ctx.fields[f]
    if not ty then
      local n = near(f, ctx.order)
      fail("T02", at, q(src) .. " names " .. q(name) .. ", and the rows have no field " .. q(f),
           "one of ." .. table.concat(ctx.order, ", ."), q(name), n and "." .. n or nil)
    end
    return ty
  end
  local head, rest = name:match("^([^.]+)%.(.+)$")
  local root = head or name
  local ty = DECL[root]
  if not ty then
    local names = sorted_keys(DECL)
    local n = near(root, names)
    fail("T02", at, q(src) .. " names " .. q(root) .. ", which nothing declares", "a declared name", q(root),
         n and "did you mean " .. q(n) .. "?" or "declare it in ui.state { ... }")
  end
  if DEPS then DEPS[root] = true end
  if not head then return ty end
  local decl = ty.t == "record" and ty or ty.t == "rowof" and DECL[ty.model] or nil
  if not decl then
    fail("T03", at, q(src) .. " reads " .. q(name) .. ", and " .. q(root) .. " is " .. tname(ty), "a record or a row_of", tname(ty))
  end
  local fty = decl.fields[rest]
  if not fty then
    local n = near(rest, decl.order)
    fail("T02", at, q(src) .. " names " .. q(name) .. ", and " .. q(root) .. " has no field " .. q(rest),
         "one of " .. table.concat(decl.order, ", "), q(rest), n and root .. "." .. n or nil)
  end
  return fty
end

-- an operand's transforms, in a template piece or a condition's operand
local FMT_INT = { d = 1, i = 1, x = 1, X = 1, c = 1 }
local FMT_NUM = { f = 1, F = 1, e = 1, E = 1, g = 1, G = 1 }
local function xform(w, arg, ty, name, src, at)
  if w == "count" then
    if arg ~= "" then fail("T01", at, q(src) .. ": |count takes nothing", "|count", q(arg)) end
    if ty.t ~= "model" then fail("T03", at, q(src) .. ": |count of " .. q(name), "a model", tname(ty)) end
    return INT
  elseif w == "fmt" then
    local conv = arg:match("^%%[-+ #0]*%d*%.?%d*([diouxXfFeEgGsc])$")
    if not conv then fail("T01", at, q(src) .. ": |fmt takes one printf conversion", "e.g. |fmt %.1f", q(arg)) end
    local ok = (FMT_INT[conv] and ty.t == "int") or (FMT_NUM[conv] and (ty.t == "number" or ty.t == "int"))
    if not ok then
      fail("T03", at, q(src) .. ": |fmt " .. arg .. " does not suit " .. q(name),
           FMT_INT[conv] and "an int" or FMT_NUM[conv] and "a number" or "an int or a number", tname(ty))
    end
  elseif w == "map" then
    local t = LABELS[arg]
    if not t then
      local ks = sorted_keys(LABELS)
      fail("T02", at, q(src) .. ": no labels named " .. q(arg), "one of " .. table.concat(ks, ", "), q(arg),
           "ui.labels(\"" .. arg .. "\", { ... }) at the top level")
    end
    if ty.t == "enum" then
      local missing, extra = {}, {}
      for _, v in ipairs(ty.values) do if t[v] == nil then missing[#missing + 1] = v end end
      for k in pairs(t) do if not ty.set[k] then extra[#extra + 1] = k end end
      table.sort(extra)
      if #missing > 0 then
        fail("T04", at, q(src) .. ": the labels " .. q(arg) .. " miss values of " .. q(name), "a label for every value",
             "none for " .. table.concat(missing, ", "), "add " .. missing[1] .. " = \"...\" (\"\" for none)")
      end
      if #extra > 0 then
        fail("T04", at, q(src) .. ": the labels " .. q(arg) .. " name what " .. q(name) .. " cannot be",
             "only " .. table.concat(ty.values, ", "), table.concat(extra, ", "), "drop them")
      end
    elseif ty.t == "bool" then
      if t["true"] == nil or t["false"] == nil then
        fail("T04", at, q(src) .. ": the labels " .. q(arg) .. " over the bool " .. q(name), "true and false", "one of them")
      end
    elseif ty.t == "int" or ty.t == "text" then
      if t["_"] == nil then fail("T04", at, q(src) .. ": the labels " .. q(arg) .. " over " .. tname(ty), "a label for _ (every other value)", "none", "_ = \"\"") end
    else
      fail("T03", at, q(src) .. ": |map of " .. q(name), "an enum, a bool, an int or a text", tname(ty))
    end
  elseif w == "plural" then
    if ty.t ~= "int" then fail("T03", at, q(src) .. ": |plural of " .. q(name), "an int", tname(ty)) end
    if not arg:match("^[^,]+,[^,]+$") then fail("T01", at, q(src) .. ": |plural needs both forms", "|plural one,many", q(arg)) end
  elseif w == "bytes" then
    if ty.t ~= "int" then fail("T03", at, q(src) .. ": |bytes of " .. q(name), "an int", tname(ty)) end
  else
    fail("T01", at, q(src) .. ": " .. q(w) .. " is not a transform", "fmt, map, plural, count or bytes", q(w),
         "a value worked out further belongs in a state name or a row field a handler writes")
  end
  return TEXT
end

local function operand(body, src, ctx, at)
  local name, rest = body:match("^%s*([%w_.@]+)(.*)$")
  if not name then fail("T01", at, "cannot read " .. q(body) .. " in " .. q(src), "a name", q(body)) end
  local ty = resolve(name, ctx, at, src)
  local xfs = {}
  local n = 0
  for xf in rest:gmatch("|([^|]*)") do
    n = n + 1
    local w, arg = xf:match("^%s*(%a*)%s*(.-)%s*$")
    if n == 2 and xfs[1][1] ~= "count" then
      fail("T01", at, q(src) .. " has two transforms", "a second transform only after count", q(rest))
    end
    if n > 2 then fail("T01", at, q(src) .. " has " .. n .. " transforms", "at most count and one more", q(rest)) end
    ty = xform(w, arg, ty, name, src, at)
    xfs[n] = { w, arg }
  end
  if rest:gsub("|[^|]*", "") ~= "" then fail("T01", at, "cannot read " .. q(body) .. " in " .. q(src), "a name and transforms", q(rest)) end
  return { name = name, xfs = xfs }, ty
end

-- a condition: terms joined by && or by ||; a derive's may mix them under
-- brackets. A term is name, !name, or operand CMP literal.
local CMPS = { "<=", ">=", "!=", "=", "<", ">" }
local function cond_term(part, e, ctx, at)
  local neg, body = part:match("^(!?)%s*(.-)$")
  local op, lhs, lit
  for _, c in ipairs(CMPS) do
    local i = body:find(c, 1, true)
    if i then op, lhs, lit = c, body:sub(1, i - 1), body:sub(i + #c); break end
  end
  lhs = (lhs or body):match("^%s*(.-)%s*$")
  if lhs == "" then fail("T01", at, "the condition " .. q(e) .. " has an empty term", "name, !name or name=value", q(part)) end
  if lhs:find("%s") or (lit and lit:find("%s")) then
    fail("T01", at, "cannot read the condition " .. q(e), "terms joined by && or ||", q(part))
  end
  if neg == "!" and op then fail("T01", at, "the condition " .. q(part) .. " negates a comparison", "name!=value", q(part)) end
  local p, ty = operand(lhs, e, ctx, at)
  p.k, p.neg, p.op = "term", neg == "!", op
  if op == nil then
    if ty.t == "enum" and not ty.opt then
      fail("T03", at, "the condition " .. q(e) .. " tests the enum " .. q(lhs) .. " bare", "a comparison", q(part),
           lhs .. "=" .. ty.values[1])
    end
    if ty.t == "record" and not ty.opt then
      fail("T03", at, "the condition " .. q(e) .. " tests the record " .. q(lhs) .. " bare", "a field of it", q(part))
    end
    return p
  end
  if ty.t == "bool" then
    fail("T03", at, "the condition " .. q(e) .. " compares the bool " .. q(lhs), "name or !name", q(part),
         ((op == "=") == (lit == "true") and "" or "!") .. lhs)
  end
  if op ~= "=" and op ~= "!=" and ty.t ~= "int" and ty.t ~= "number" then
    fail("T03", at, "the condition " .. q(e) .. " orders " .. tname(ty) .. " with " .. op, "an int or a number", tname(ty))
  end
  if ty.t == "enum" then
    if not ty.set[lit] then
      local n = near(lit, ty.values)
      fail("T03", at, "the condition " .. q(e) .. " compares " .. q(lhs) .. " with " .. q(lit),
           "one of " .. table.concat(ty.values, ", "), q(lit), n and lhs .. op .. n or nil)
    end
    p.lit = lit
  elseif ty.t == "int" or ty.t == "number" then
    p.lit = tonumber(lit)
    if not p.lit then fail("T03", at, "the condition " .. q(e) .. " compares the number " .. q(lhs) .. " with " .. q(lit), "a number", q(lit)) end
  elseif ty.t == "text" then p.lit = lit
  else
    fail("T03", at, "the condition " .. q(e) .. " compares " .. tname(ty), "a bool test, an int, a number, a text or an enum", tname(ty))
  end
  return p
end

local DERIVE_MODE = false
local function cond_expr(e, ctx, at)
  -- and is &&, or is ||: a single & or | is C01
  local lone = e:gsub("&&", ""):gsub("||", ""):match("[&|]")
  if lone then
    fail("C01", at, "the condition " .. q(e) .. " joins with a single " .. lone, "&& or ||", q(lone),
         "and is &&, or is ||: " .. q((e:gsub("&+", "&&"):gsub("|+", "||"))))
  end
  local pos, ands, ors = 1, false, false
  local function skip() pos = e:find("[^%s]", pos) or #e + 1 end
  local expr
  local function unary()
    skip()
    local neg = e:sub(pos, pos) == "!" and e:sub(pos + 1, pos + 1):match("[%s(]") and true
    local bpos = pos
    if neg then pos = pos + 1; skip() end
    if e:sub(pos, pos) == "(" then
      if not DERIVE_MODE then
        fail("C03", at, "the condition " .. q(e) .. " has brackets", "an inline condition without brackets", "brackets",
             "ui.derive(\"NAME\", { cond = \"" .. e .. "\" }) and test NAME")
      end
      pos = pos + 1
      local n = expr()
      skip()
      if e:sub(pos, pos) ~= ")" then fail("T01", at, "the condition " .. q(e) .. " opens ( and never closes it", ")", q(e:sub(pos))) end
      pos = pos + 1
      return neg and { k = "not", n } or n
    end
    pos = bpos
    local z = pos
    while z <= #e do
      local two = e:sub(z, z + 1)
      local c = e:sub(z, z)
      if two == "&&" or two == "||" or c == "(" or c == ")" then break end
      z = z + 1
    end
    local part = e:sub(pos, z - 1):match("^%s*(.-)%s*$")
    pos = z
    return cond_term(part, e, ctx, at)
  end
  local function chain(op, k)
    local n = { k = k }
    while true do
      n[#n + 1] = unary()
      skip()
      if e:sub(pos, pos + 1) ~= op then break end
      pos = pos + 2
    end
    return n
  end
  function expr()
    skip()
    local first = unary()
    skip()
    local op = e:sub(pos, pos + 1)
    if op ~= "&&" and op ~= "||" then return first end
    local n = { k = op == "&&" and "and" or "or", first }
    if op == "&&" then ands = true else ors = true end
    while e:sub(pos, pos + 1) == op do
      pos = pos + 2
      n[#n + 1] = unary()
      skip()
    end
    local other = e:sub(pos, pos + 1)
    if (other == "&&" or other == "||") and other ~= op then
      fail("C02", at, "the condition " .. q(e) .. " mixes && and ||" .. (DERIVE_MODE and " at one bracket level" or ""),
           "&& or ||, not both", q(e), DERIVE_MODE and "brackets around one side"
           or "ui.derive(\"NAME\", { cond = \"a && (b || c)\" }) and test NAME")
    end
    return n
  end
  local tree = expr()
  skip()
  if pos <= #e then fail("T01", at, "cannot read the condition " .. q(e), "a condition", q(e:sub(pos))) end
  return tree
end

local function compile_cond(e, ctx, at)
  if ctx == nil then ctx = ROWCTX elseif ctx == false then ctx = nil end
  if type(e) == "boolean" then return { k = "const", v = e } end
  if type(e) ~= "string" then
    fail("C01", at, "a condition is " .. describe(e), "a condition text, or true or false", describe(e))
  end
  if e:find("{", 1, true) then
    fail("C01", at, "the condition " .. q(e) .. " is a template", "a condition", q(e), (e:gsub("[{}]", "")))
  end
  local key = e .. "\1" .. (ctx and ctx.name or "")
  if CHECKED[key] then return COND[key] end
  local tree = cond_expr(e, ctx, at)
  COND[key], CHECKED[key] = tree, true
  COND[e] = COND[e] or tree
  return tree
end

-- a template: literal text, {{ and }}, and {piece}s; a piece is a reference
-- with transforms, or cond?a:b whose branches hold plain references
local compile_tpl
local function split_branches(body, src, at)
  local branches, cur, i, depth = {}, {}, 1, 0
  while i <= #body do
    local c = body:sub(i, i)
    if c == "\\" and body:sub(i + 1, i + 1) == ":" then cur[#cur + 1] = ":"; i = i + 2
    else
      if c == "{" then depth = depth + 1 elseif c == "}" then depth = depth - 1 end
      if c == ":" and depth == 0 then branches[#branches + 1] = table.concat(cur); cur = {}
      else cur[#cur + 1] = c end
      i = i + 1
    end
  end
  branches[#branches + 1] = table.concat(cur)
  if #branches ~= 2 then
    fail("T01", at, "the template " .. q(src) .. " has " .. (#branches < 2 and "a choice with one branch" or "a second ':'"),
         "{cond?a:b}", q(body), #branches > 2 and "\\: for a colon in a branch" or nil)
  end
  return branches
end

local function tpl_parts(s, ctx, at, src, in_branch)
  local parts, pos = {}, 1
  local lit = {}
  local function flush() if #lit > 0 then parts[#parts + 1] = table.concat(lit); lit = {} end end
  while pos <= #s do
    local c = s:sub(pos, pos)
    if c == "{" and s:sub(pos + 1, pos + 1) == "{" then lit[#lit + 1] = "{"; pos = pos + 2
    elseif c == "}" and s:sub(pos + 1, pos + 1) == "}" then lit[#lit + 1] = "}"; pos = pos + 2
    elseif c == "}" then fail("T01", at, "the template " .. q(src) .. " has a lone }", "{{ and }} for braces", q(s:sub(pos)))
    elseif c == "{" then
      local depth, z = 0, nil
      for i = pos, #s do
        local d = s:sub(i, i)
        if d == "{" then depth = depth + 1 elseif d == "}" then depth = depth - 1; if depth == 0 then z = i break end end
      end
      if not z then fail("T01", at, "the template " .. q(src) .. " opens { and never closes it", "{...}", q(s:sub(pos))) end
      flush()
      local body = s:sub(pos + 1, z - 1)
      local qm = body:find("?", 1, true)
      if qm then
        if in_branch then fail("T01", at, "the template " .. q(src) .. " has a choice inside a branch", "plain references in a branch", q(body)) end
        local c_src = body:sub(1, qm - 1)
        local br = split_branches(body:sub(qm + 1), src, at)
        parts[#parts + 1] = { cond = compile_cond(c_src, ctx, at), a = tpl_parts(br[1], ctx, at, src, true),
                              b = tpl_parts(br[2], ctx, at, src, true) }
      else
        local p, ty = operand(body, src, ctx, at)
        if ty.t == "bool" or ty.t == "record" or ty.t == "model" or ty.t == "rowof" then
          fail("T03", at, q(src) .. " shows " .. q(p.name) .. " as text, and it is " .. tname(ty),
               "a text, an int, a number or an enum", tname(ty),
               ty.t == "bool" and "{" .. p.name .. "?yes:no}" or ty.t == "model" and "{" .. p.name .. "|count}" or "name a field")
        end
        parts[#parts + 1] = p
      end
      pos = z + 1
    else lit[#lit + 1] = c; pos = pos + 1 end
  end
  flush()
  return parts
end

function compile_tpl(s, ctx, at)
  if ctx == nil then ctx = ROWCTX elseif ctx == false then ctx = nil end
  if s == nil then return end
  if type(s) ~= "string" then
    fail("N01", at, "a template is " .. describe(s), "a text", describe(s),
         type(s) == "number" and "\"{name}\" for a state's value, or tostring(x) for a constant" or nil)
  end
  local key = s .. "\1" .. (ctx and ctx.name or "")
  if CHECKED[key] then return end
  TPL[key] = tpl_parts(s, ctx, at, s, false)
  TPL[s] = TPL[s] or TPL[key]
  CHECKED[key] = true
end

-- run time: lookups and rendering, over compiled forms only
local function lookup(name, row)
  local d = DERIVED[name]
  if d then return d() end
  local c = name:sub(1, 1)
  if c == "." then return row and row[name:sub(2)] end
  if c == "@" then return SYS[name:sub(2)] end
  local head, rest = name:match("^([^.]+)%.(.+)$")
  if head then
    local ty, v = DECL[head], V[head]
    if ty.t == "rowof" then
      if v ~= nil then v = model_of(ty.model).rows[model_of(ty.model).index[v]] end
      if v == nil then return zero(DECL[ty.model].fields[rest]) end
    elseif v == nil then return zero(ty.fields[rest]) end
    return v[rest]
  end
  return V[name]
end

local function truthy(v)
  if getmetatable(v) == ModelMT then return #v.rows > 0 end
  return v ~= nil and v ~= false and v ~= "" and v ~= 0
end

local function bytes(n)
  if n < 1024 then return n .. " B" end
  for _, u in ipairs({ "KB", "MB", "GB" }) do
    n = n / 1024
    if n < 1024 or u == "GB" then return string.format(n < 10 and "%.1f %s" or "%.0f %s", n, u) end
  end
end

local function value_of(p, row)
  local v = lookup(p.name, row)
  for _, x in ipairs(p.xfs) do
    local w, arg = x[1], x[2]
    if w == "count" then v = v and #v.rows or 0
    elseif v == nil then v = nil
    elseif w == "fmt" then v = string.format(arg, v)
    elseif w == "map" then
      local t = LABELS[arg]
      local s = t[tostring(v)]
      if s == nil then s = t["_"] end
      v = s or ""
    elseif w == "plural" then
      local one, many = arg:match("^([^,]+),([^,]+)$")
      v = tostring(v) .. " " .. (v == 1 and one or many)
    elseif w == "bytes" then v = bytes(v) end
  end
  return v
end

local function eval(n, row)
  local k = n.k
  if k == "and" then
    for _, c in ipairs(n) do if not eval(c, row) then return false end end
    return true
  elseif k == "or" then
    for _, c in ipairs(n) do if eval(c, row) then return true end end
    return false
  elseif k == "not" then return not eval(n[1], row)
  elseif k == "const" then return n.v end
  local v = value_of(n, row)
  local op, r = n.op, nil
  if op == "=" then r = v == n.lit or (v == nil and n.lit == "")
  elseif op == "!=" then r = not (v == n.lit or (v == nil and n.lit == ""))
  elseif op == nil then r = truthy(v)
  elseif v == nil then r = false
  elseif op == "<" then r = v < n.lit elseif op == ">" then r = v > n.lit
  elseif op == "<=" then r = v <= n.lit else r = v >= n.lit end
  if n.neg then r = not r end
  return r
end

-- memoised while nothing moved (GEN): a still frame re-renders nothing
local CGEN, CMEMO, RMEMO = -1, {}, {}
local function cond(e, row, ctxname)
  if e == nil then return nil end
  if type(e) == "boolean" then return e end
  local tree = COND[e .. "\1" .. (ctxname or "")] or COND[e]
  if not tree then fail("X01", nil, "the condition " .. q(e) .. " was never checked at build", "a build-time check", "none") end
  if row then return eval(tree, row) end
  if CGEN ~= GEN then CGEN, CMEMO, RMEMO = GEN, {}, {} end
  local r = CMEMO[e]
  if r == nil then r = eval(tree, nil); CMEMO[e] = r end
  return r
end

local function render_parts(parts, row)
  if #parts == 1 and type(parts[1]) == "string" then return parts[1] end
  local out = {}
  for i, p in ipairs(parts) do
    if type(p) == "string" then out[i] = p
    elseif p.cond then out[i] = render_parts(eval(p.cond, row) and p.a or p.b, row)
    else
      local v = value_of(p, row)
      out[i] = v == nil and "" or tostring(v)
    end
  end
  return table.concat(out)
end

local function render(s, row, ctxname)
  if s == nil then return "" end
  if type(s) ~= "string" then return tostring(s) end
  if not s:find("[{}]") then return s end
  if not row then
    if CGEN ~= GEN then CGEN, CMEMO, RMEMO = GEN, {}, {} end
    local r = RMEMO[s]
    if r then return r end
  end
  local parts = TPL[s .. "\1" .. (ctxname or "")] or TPL[s]
  if not parts then fail("X01", nil, "the template " .. q(s) .. " was never checked at build", "a build-time check", "none") end
  local r = render_parts(parts, row)
  if not row then RMEMO[s] = r end
  return r
end

-- derives: a bool condition or a text template over state, computed by the
-- layer and written by nobody. Compiled once every declaration is in (a
-- derive may read one declared below it); a cycle is G01.
function T.derive(name, o, extra)
  local at = where()
  no_build("ui.derive", at)
  if type(name) ~= "string" or not name:match("^[%a_][%w_]*$") then
    fail("N01", at, "a derive's name is " .. describe(name), "a word", describe(name))
  end
  local form = "ui.derive(\"" .. name .. "\", { cond = \"a && b\" }), or { text = \"{n} left\" }"
  if type(o) ~= "table" or getmetatable(o) ~= nil or extra ~= nil then
    fail("N01", at, "the derive " .. q(name) .. " is given " .. describe(o), "a table of cond = or text =", describe(o), form)
  end
  for k in pairs(o) do
    if k ~= "cond" and k ~= "text" then fail("N01", at, "the derive " .. q(name) .. " takes no " .. q(k), "cond or text", q(k), form) end
  end
  if (o.cond == nil) == (o.text == nil) then
    fail("N01", at, "the derive " .. q(name) .. " takes one of cond and text", "one of them", o.cond and "both" or "neither", form)
  end
  local kind, src = o.cond ~= nil and "bool" or "text", o.cond or o.text
  if type(src) ~= "string" then fail("N01", at, "the derive " .. q(name) .. "'s source is " .. describe(src), "a text", describe(src)) end
  claim(name, "derive", at)
  DECL[name] = { t = kind, derived = src }
  DERIVES[#DERIVES + 1] = { name = name, kind = kind, src = src, at = at }
end

local function compile_derives()
  local reads = {}
  for _, d in ipairs(DERIVES) do
    DEPS = {}
    if d.kind == "bool" then
      DERIVE_MODE = true
      local ok, tree = pcall(compile_cond, d.src, false, d.at)
      DERIVE_MODE = false
      if not ok then DEPS = nil; error(tree, 0) end
      DERIVED[d.name] = function() return eval(tree, nil) end
    else
      local ok, err = pcall(compile_tpl, d.src, false, d.at)
      if not ok then DEPS = nil; error(err, 0) end
      local src = d.src
      DERIVED[d.name] = function() return render_parts(TPL[src .. "\1"] or TPL[src], nil) end
    end
    reads[d.name] = DEPS
    DEPS = nil
  end
  local state = {}
  local function visit(n, path)
    if state[n] == 2 then return end
    if state[n] == 1 then
      local d
      for _, x in ipairs(DERIVES) do if x.name == n then d = x end end
      fail("G01", d.at, "the derives " .. table.concat(path, " -> ") .. " read each other in a cycle", "derives that reach no cycle",
           "a cycle", "compute one of them in a handler")
    end
    state[n] = 1
    for r in pairs(reads[n] or {}) do
      if reads[r] then path[#path + 1] = r; visit(r, path); path[#path] = nil end
    end
    state[n] = 2
  end
  for _, d in ipairs(DERIVES) do visit(d.name, { d.name }) end
end

-- -- actions and hooks ---------------------------------------------------------------

local APP_ROLES = { confirm = 1, back = 1, more = 1, menu = 1, left = 1, right = 1, up = 1, down = 1, space = 1,
                    search = 1, ["goto"] = 1, complete = 1, undo = 1, redo = 1, play = 1, tools = 1 }
local SHELL_ROLES = { home = 1, full = 1, system = 1 }
local TAKES_N = { none = 0, row = 1, arg = 1, char = 1, pointer = 1 }

local function check_arity(name, fn, takes, at)
  if not HAS_DEBUG or type(fn) ~= "function" then return end
  local info = debug.getinfo(fn, "u")
  local want = TAKES_N[takes]
  if info.isvararg or info.nparams ~= want then
    local fix = takes == "none" and "function() ... end"
                or "function(" .. (takes == "row" and "row" or takes == "char" and "ch" or takes == "pointer" and "ev" or "arg") .. ") ... end"
    fail("A03", at, q(name) .. "'s handler takes " .. (info.isvararg and "..." or tostring(info.nparams))
         .. " parameter" .. (info.nparams == 1 and "" or "s") .. ", and it is declared takes=" .. q(takes),
         want .. " parameter" .. (want == 1 and "" or "s"), info.isvararg and "a vararg" or tostring(info.nparams),
         fix .. (takes == "none" and ", or { takes = \"row\" }" or ""))
  end
end

local function check_keys(name, keys, at)
  if type(keys) ~= "table" or getmetatable(keys) then
    fail("A09", at, q(name) .. "'s keys are " .. describe(keys), "a list: { \"role:more\", \"ctrl+f\", \"s\" }", describe(keys))
  end
  for k, v in pairs(keys) do
    if type(k) ~= "number" then
      fail("A09", at, q(name) .. "'s keys name " .. q(k) .. " = " .. describe(v), "a list: { \"role:" .. tostring(k) .. "\" }",
           "a table of roles", "keys = { \"role:" .. tostring(k) .. "\" }")
    end
    if type(v) ~= "string" then fail("A09", at, q(name) .. "'s key is " .. describe(v), "a text", describe(v)) end
    local role = v:match("^role:(.+)$")
    if role then
      if SHELL_ROLES[role] then
        fail("K01", at, q(name) .. " takes the shell's role " .. q(role), "a role of the app's", q(v), "a role such as role:more")
      end
      if not APP_ROLES[role] then
        fail("K03", at, q(name) .. " names the role " .. q(role), "one of " .. table.concat(sorted_keys(APP_ROLES), ", "), q(role))
      end
    elseif not (v:match("^ctrl%+%l$") or v:match("^%l$")) then
      fail("K03", at, q(name) .. "'s key " .. q(v), "role:NAME, ctrl+LETTER or a single letter", q(v), "keys = { \"ctrl+f\", \"role:search\" }")
    end
    if KEYMAP[v] then
      fail("K02", at, "the key " .. q(v) .. " is on " .. q(KEYMAP[v]) .. " and on " .. q(name), "one key, one action",
           "two actions", "give the key to one of them")
    end
    KEYMAP[v] = name
  end
end

local ACTION_OPTS = { hint = 1, when = 1, off = 1, takes = 1, row = 1, chars = 1, keys = 1, icon = 1, push = 1 }
local RETIRED_ACTION = { key = "keys = { \"role:NAME\" }", does = "hint = \"...\"", disabled = "when = \"...\" with off = \"grey\"" }

function T.action(name, label, fn, o)
  local at = where()
  no_build("ui.action", at)
  if type(name) ~= "string" or not name:match("^[%a_][%w_]*$") then
    fail("A09", at, "an action's name is " .. describe(name), "a word", describe(name), "ui.action(\"add\", \"Add one\", add)")
  end
  if type(label) ~= "string" then
    fail("A09", at, "the action " .. q(name) .. "'s label is " .. describe(label), "a text", describe(label),
         type(label) == "function" and "ui.action(NAME, LABEL, HANDLER): the label comes before the handler" or nil)
  end
  if label == "" then
    fail("A08", at, "the action " .. q(name) .. " has an empty label", "a label a person reads", "\"\"",
         "ui.hook(\"" .. name .. "\", handler)")
  end
  o = o or {}
  if type(o) ~= "table" then fail("N01", at, "ui.action's options are " .. describe(o), "{ when = ..., ... }", describe(o)) end
  for k in pairs(o) do
    if RETIRED_ACTION[k] then fail("N04", at, "ui.action's " .. q(k) .. " is retired", RETIRED_ACTION[k], q(k), RETIRED_ACTION[k]) end
    if not ACTION_OPTS[k] then
      local n = near(k, sorted_keys(ACTION_OPTS))
      fail("N01", at, "ui.action takes no option " .. q(k), "one of " .. table.concat(sorted_keys(ACTION_OPTS), ", "), q(k), n)
    end
  end
  if o.push ~= nil then
    if fn ~= nil then
      fail("P03", at, "the action " .. q(name) .. " pushes " .. describe(o.push) .. " and has a handler", "no handler", "a handler",
           "ui.action(\"" .. name .. "\", \"" .. label .. "\", nil, { push = \"" .. tostring(o.push) .. "\" })")
    end
    if o.takes ~= nil then fail("P02", at, "the action " .. q(name) .. " pushes a page and declares takes", "no takes", describe(o.takes), "drop takes") end
    if type(o.push) ~= "string" then fail("A09", at, "the action " .. q(name) .. "'s push is " .. describe(o.push), "a page's name", describe(o.push)) end
  elseif type(fn) ~= "function" then
    fail("A09", at, "the action " .. q(name) .. "'s handler is " .. describe(fn), "a function", describe(fn),
         fn ~= nil and "pass the function, not its result: " .. name .. ", not " .. name .. "()" or nil)
  end
  local takes = o.takes
  if o.row ~= nil then
    local ty = DECL[o.row]
    if type(o.row) ~= "string" or not ty or ty.t ~= "rowof" then
      fail("A09", at, "the action " .. q(name) .. "'s row " .. describe(o.row) .. " names no row_of", "a ui.row_of state",
           ty and tname(ty) or "undeclared")
    end
    if takes ~= nil and takes ~= "row" then
      fail("A02", at, "the action " .. q(name) .. " acts on the row " .. q(o.row) .. " and takes " .. q(takes), "takes = \"row\"", q(takes))
    end
    takes = "row"
  end
  takes = takes or "none"
  if not TAKES_N[takes] then
    fail("A09", at, "the action " .. q(name) .. " takes " .. describe(takes), "\"none\", \"row\", \"arg\", \"char\" or \"pointer\"", describe(takes))
  end
  if o.chars ~= nil and takes ~= "char" then
    fail("A02", at, "the action " .. q(name) .. " has chars and takes " .. q(takes), "takes = \"char\"", q(takes))
  end
  if o.off ~= nil and o.off ~= "hide" and o.off ~= "grey" then
    fail("A09", at, "the action " .. q(name) .. "'s off is " .. describe(o.off), "\"hide\" or \"grey\"", describe(o.off))
  end
  if (o.when ~= nil or o.row ~= nil) and o.off == nil then
    fail("O01", at, "the action " .. q(name) .. " can be off (" .. (o.when and "when" or "row") .. ") and says no off",
         "off = \"hide\" or \"grey\"", "none", "{ off = \"grey\" }")
  end
  if o.off ~= nil and o.when == nil and o.row == nil then
    fail("O02", at, "the action " .. q(name) .. " has off and can never be off", "off with when or row", "off alone", "drop off")
  end
  if o.icon ~= nil and type(o.icon) ~= "string" then
    fail("A09", at, "the action " .. q(name) .. "'s icon is " .. describe(o.icon), "an icon's name", describe(o.icon))
  end
  if o.icon ~= nil and o.icon:find("{", 1, true) then
    fail("I02", at, "the action " .. q(name) .. "'s icon is " .. describe(o.icon), "an icon's name", describe(o.icon),
         "a node's icon = \"{...}\" where it must change")
  end
  if o.hint ~= nil and type(o.hint) ~= "string" then fail("A09", at, "the action " .. q(name) .. "'s hint is " .. describe(o.hint), "a text", describe(o.hint)) end
  claim(name, "action", at)
  if o.keys ~= nil then check_keys(name, o.keys, at) end
  check_arity(name, fn, takes, at)
  compile_tpl(label, false, at)
  if o.when ~= nil then compile_cond(o.when, false, at) end
  ACTION_ORDER[#ACTION_ORDER + 1] = name
  ACTIONS[name] = { name = name, label = label, fn = fn, takes = takes, when = o.when, off = o.off, row = o.row,
                    keys = o.keys, icon = o.icon, hint = o.hint, push = o.push, chars = o.chars, at = at }
end

local HOOK_OPTS = { takes = 1 }
function T.hook(name, fn, o)
  local at = where()
  no_build("ui.hook", at)
  if type(name) ~= "string" or not name:match("^[%a_][%w_]*$") then
    fail("A09", at, "a hook's name is " .. describe(name), "a word", describe(name))
  end
  if type(fn) ~= "function" then
    fail("A09", at, "the hook " .. q(name) .. "'s handler is " .. describe(fn), "a function", describe(fn),
         type(fn) == "string" and "ui.hook(NAME, HANDLER, { takes = ... }): a hook has no label" or nil)
  end
  o = o or {}
  for k in pairs(o) do
    if k == "label" or k == "icon" or k == "keys" or k == "when" or k == "hint" then
      fail("H03", at, "the hook " .. q(name) .. " has " .. k, "takes alone", q(k), "an action, if a person sees it")
    elseif not HOOK_OPTS[k] then fail("N01", at, "ui.hook takes no option " .. q(k), "takes", q(k)) end
  end
  local takes = o.takes or "none"
  if takes ~= "none" and takes ~= "row" and takes ~= "arg" and takes ~= "pointer" then
    fail("A09", at, "the hook " .. q(name) .. " takes " .. describe(takes), "\"none\", \"row\", \"arg\" or \"pointer\"", describe(takes))
  end
  claim(name, "hook", at)
  check_arity(name, fn, takes, at)
  ACTION_ORDER[#ACTION_ORDER + 1] = name
  ACTIONS[name] = { name = name, label = "", fn = fn, takes = takes, hook = true, at = at }
end

-- the built-in `back`: label Back, the back role; pops the page
ACTIONS.back = { name = "back", label = "Back", takes = "none", builtin = true, at = "built-in",
                 fn = function() T.pop() end }
NAMES.back = { what = "built-in action", at = "built-in" }
ACTION_ORDER[1] = "back"

-- a site's reference to an action: it exists, it is no hook where a person
-- sees it, and what the site hands suits its takes
local function use_action(name, hands, at, what, person)
  if type(name) == "function" then
    fail("A01", at, what .. " is given a function", "an action's name", "a function",
         "ui.action(\"NAME\", \"Label\", fn) at the top level, and its name here")
  end
  if type(name) ~= "string" then fail("A01", at, what .. " is " .. describe(name), "an action's name", describe(name)) end
  local a = ACTIONS[name]
  if not a then
    for k, x in pairs(ACTIONS) do
      if x.label == name and not x.hook then
        fail("A01", at, what .. ": " .. q(name) .. " is the label of the action " .. q(k), "an action's name",
             "its label", (what:match("^(ui%.%a+)%(") or what) .. "(\"" .. k .. "\")")
      end
    end
    local ks = sorted_keys(ACTIONS)
    local n = near(name, ks)
    fail("A01", at, what .. " names " .. q(name) .. ", which is no action", "one of " .. table.concat(ks, ", "), q(name),
         n and "did you mean " .. q(n) .. "?" or "ui.action(\"" .. name .. "\", \"Label\", handler) at the top level")
  end
  if person and a.hook then
    fail("H01", at, what .. " names the hook " .. q(name) .. ", where a person sees an action", "an action", "a hook",
         "ui.action(\"" .. name .. "\", \"Label\", handler)")
  end
  local takes = a.takes
  local ok = hands == takes or (a.row and hands == "none")
  if not ok then
    fail("A02", at, what .. " hands " .. (hands == "none" and "nothing" or "a " .. hands) .. " to " .. q(name)
         .. ", which takes " .. q(takes), "takes = \"" .. hands .. "\"", "takes = \"" .. takes .. "\"",
         hands == "row" and "{ takes = \"row\" } on " .. name .. " and function(row)" or
         takes == "row" and "use it inside the rows, or give the action row = \"A_ROW_OF\"" or nil)
  end
  USES[#USES + 1] = { name = name, at = at, what = what }
  return a
end

-- -- the app declaration and pages -------------------------------------------------------

local APP_FIELDS = { icon = 1, size = 1, min = 1, back = 1, glass = 1, slots = 1 }
function T.app(title, o)
  local at = where()
  no_build("ui.app", at)
  if APP then fail("D01", at, "a second ui.app", "one app declaration", "a second (the first at " .. APP.at .. ")") end
  if type(title) ~= "string" then fail("N01", at, "ui.app's title is " .. describe(title), "a text", describe(title)) end
  o = o or {}
  for k, v in pairs(o) do
    if not APP_FIELDS[k] then
      fail("D01", at, "ui.app takes no field " .. q(k), "one of " .. table.concat(sorted_keys(APP_FIELDS), ", "), q(k),
           (k == "x" or k == "y" or k == "place") and "drop it: the shell places windows" or "drop it")
    end
    if k == "size" or k == "min" then
      local ok = type(v) == "table" and #v == 2
      for i = 1, 2 do
        local x = ok and v[i]
        local a, b = type(x) == "string" and x:match("^(%d+)/(%d+)$")
        if not (math.type(x) == "integer" and x > 0) and not (a and tonumber(a) > 0 and tonumber(a) <= tonumber(b)) then ok = false end
      end
      if not ok then fail("D02", at, "ui.app's " .. k .. " is " .. describe(v), "{ w, h } of desk units or \"a/b\" fractions", describe(v), "size = { \"7/8\", \"9/10\" }") end
    end
  end
  APP = { title = title, o = o, at = at }
end

local PAGE_OPTS = { title = 1, arg = 1 }
function T.page(name, build, o)
  local at = where()
  no_build("ui.page", at)
  if type(name) ~= "string" or not name:match("^[%a_][%w_]*$") then
    fail("N01", at, "a page's name is " .. describe(name), "a word: ui.page(\"home\", home, { title = \"Home\" })", describe(name))
  end
  if PAGES[name] then fail("U02", at, "the page " .. q(name) .. " is registered twice", "one page per name", "a second (the first at " .. PAGES[name].at .. ")") end
  if type(build) ~= "function" then
    fail("N01", at, "the page " .. q(name) .. "'s build is " .. describe(build), "a function", describe(build),
         "pass the function, not its result")
  end
  o = o or {}
  for k in pairs(o) do
    if not PAGE_OPTS[k] then fail("N01", at, "ui.page takes no option " .. q(k), "title, arg", q(k)) end
  end
  if o.title ~= nil and type(o.title) ~= "string" then fail("N01", at, "the page " .. q(name) .. "'s title is " .. describe(o.title), "a template", describe(o.title)) end
  PAGES[name] = { name = name, fn = build, title = o.title or "", arg = o.arg, at = at }
  PAGE_ORDER[#PAGE_ORDER + 1] = name
end

-- every declaration is in: derives compile, pages' titles and arguments check
local function seal()
  if sealed then return end
  sealed = true
  compile_derives()
  for _, name in ipairs(PAGE_ORDER) do
    local p = PAGES[name]
    compile_tpl(p.title, false, p.at)
    if p.arg ~= nil then
      local ty = DECL[p.arg]
      if type(p.arg) ~= "string" or not ty or ty.t ~= "rowof" then
        fail("P04", p.at, "the page " .. q(name) .. "'s arg " .. describe(p.arg) .. " names no row_of", "a ui.row_of state the push writes",
             ty and tname(ty) or "undeclared")
      end
    end
  end
  for name, a in pairs(ACTIONS) do
    if a.push and not PAGES[a.push] then
      fail("P01", a.at, "the action " .. q(name) .. " pushes " .. q(a.push) .. ", which is no page",
           "one of " .. table.concat(PAGE_ORDER, ", "), q(a.push), "ui.page(\"" .. a.push .. "\", build)")
    end
  end
end

-- -- nodes -----------------------------------------------------------------------------

local NodeMT = {}
-- the contract's table (native/moy_uitree's schema) checks every prop's name, class and words:
-- __moy_uitree_check(kind, prop, class, text) answers its type, or nil and the code and message
local CHECK = __moy_uitree_check
__moy_uitree_check = nil
if CHECK == nil then
  fail("E00", "load", "the UI tree's checks are not in this build", "a build with native/moy_uitree", "none")
end
-- where this layer differs from the table: what the table only designs and the layer builds
-- (true; a kind's every prop), and what the table has that the layer does not build yet (false)
local OWN = { bubble = true, ["dialog.default"] = true, ["field.grow"] = true, ["field.input"] = true,
              ["field.when"] = true, ["field.off"] = true, ["info.tag"] = true, ["info.tone"] = true,
              ["link.tag"] = true, ["link.tone"] = true, ["popover.wash"] = true, ["rows.by"] = true,
              ["rows.check"] = true, ["scroll.new"] = true, ["scroll.follow"] = true, ["slider.when"] = true,
              ["slider.off"] = true, ["toggle.when"] = true, ["toggle.off"] = true, ["card.image"] = false }
-- a parent's prop the table lets every child carry: the one kind the layer builds it on
local CHILD = { span = "button", grow = "field" }
local BUILDS = 16
local STOPS = { button = 1, link = 1, toggle = 1, slider = 1, field = 1, bubble = 1, rows = 1 }

-- a value's class, as the table's check names them (moy_uitree_check.h's UIT_VC_*)
local function vclass(v)
  local t = type(v)
  if t == "string" then return 1 end
  if t == "number" then return math.type(v) == "integer" and 2 or 7 end
  if t == "boolean" then return 3 end
  if t == "function" then return 6 end
  if t == "table" then return (v[1] ~= nil or next(v) == nil) and 4 or 5 end
  return 0
end

local function check_opts(kind, o, at)
  if o == nil then return {} end
  if type(o) ~= "table" or getmetatable(o) ~= nil then
    fail("N01", at, "ui." .. kind .. "'s props are " .. describe(o), "a table of NAME = VALUE", describe(o))
  end
  if o[1] ~= nil then
    fail("N01", at, "ui." .. kind .. " is given a positional prop " .. describe(o[1]), "the head positional, every other prop named",
         describe(o[1]), "name it: { value = ... }")
  end
  for k, v in pairs(o) do
    local own = OWN[kind .. "." .. k]
    if own == false or (CHILD[k] and CHILD[k] ~= kind) then
      fail("E00", at, "the Lua layer does not build ui." .. kind .. "'s " .. k .. " yet",
           CHILD[k] and "it on ui." .. CHILD[k] .. " alone" or "a prop this layer builds", q(k), "drop it")
    end
    local vc = vclass(v)
    if own or OWN[kind] then vc = vc | BUILDS end
    local ty, code, what, expected, fix = CHECK(kind, k, vc, type(v) == "string" and v or nil)
    if code then
      if not ty and (k == "when" or k == "off") then
        fail("O04", at, "ui." .. kind .. " has " .. k .. ", and it is neither an action's site nor a two-way control",
             "when and off on the action", q(k), "ui.action(NAME, LABEL, fn, { when = ..., off = \"grey\" })")
      end
      fail(code, at, what, expected ~= "" and expected or fix, ty and describe(v) or q(k), fix ~= "" and fix or nil)
    end
    -- the table took the value: compile its templates and conditions here, at the call's line
    if ty == "cond" then compile_cond(v, nil, at)
    elseif ty == "conds" then
      for _, c in pairs(v) do
        if type(c) ~= "string" then fail("N01", at, "ui." .. kind .. "'s " .. k .. " holds " .. describe(c), "a condition (a text)", describe(c)) end
        compile_cond(c, nil, at)
      end
    elseif ty == "list" or ty == "list:tpl" then
      if ty == "list" and #v == 0 then fail("N01", at, "ui." .. kind .. "'s " .. k .. " is an empty table", "a list: { ... }", "{}") end
      for _, x in ipairs(v) do
        if not (type(x) == "string" or (k == "sizes" and math.type(x) == "integer")) then
          fail("N01", at, "ui." .. kind .. "'s " .. k .. " holds " .. describe(x), k == "sizes" and "ints or weights (\"2*\")" or "texts", describe(x))
        end
        if k ~= "sizes" then compile_tpl(x, ty == "list:tpl" and nil or false, at) end
      end
    elseif ty == "int" and type(v) == "string" then
      local ref = k == "signal" and v:match("^{([%w_.@]+)}$")
      if ref then
        local _, rt = operand(ref, v, ROWCTX, at)
        if rt.t ~= "int" then fail("T03", at, "ui." .. kind .. "'s signal reads " .. q(ref) .. ", which is " .. tname(rt), "an int", tname(rt)) end
      end
      compile_tpl(v, nil, at)
    elseif ty == "int" and kind == "grid" and v < 1 then
      fail("N01", at, "ui.grid's " .. k .. " is " .. describe(v), "an int, at least 1", describe(v))
    elseif ty == "tpl" and k ~= "group" then compile_tpl(v, nil, at)   -- a group's caption compiles over its rows
    end
  end
  return o
end

local function node(kind, at)
  if not parent then
    fail("B03", at, "ui." .. kind .. " outside a page's build", "nodes made inside a function given to ui.page",
         in_handler and "a handler (" .. in_handler .. ")" or "the module's top level",
         "ui.page(\"NAME\", function() ui." .. kind .. "(...) end)")
  end
  local n = setmetatable({ kind = kind, a = {}, o = {}, kids = {}, at = at, row = ROWCTX }, NodeMT)
  parent.kids[#parent.kids + 1] = n
  n.up = parent
  return n
end

local function run_body(n, body)
  if type(body) ~= "function" then
    fail("N01", n.at, "ui." .. n.kind .. "'s body is " .. describe(body), "a function, the last argument", describe(body))
  end
  n.kids = {}
  local sp = parent
  parent = n
  local ok, r = pcall(body)
  parent = sp
  if not ok then error(r, 0) end
  T.stats.builds = T.stats.builds + 1
end

-- a node's head: a text that is a template
local function head_text(kind, v, at, what)
  if type(v) ~= "string" then
    fail("N01", at, "ui." .. kind .. "'s " .. what .. " is " .. describe(v), "a text (a template)", describe(v),
         type(v) == "table" and "ui." .. kind .. "(TEXT, { props }" .. ") : the head comes first" or nil)
  end
  compile_tpl(v, nil, at)
  return v
end

-- (head?, props?, body?) after the head
local function tail(kind, args, from, at)
  local o, body
  for i = from, args.n do
    local v = args[i]
    if type(v) == "function" then
      if i ~= args.n then fail("N01", at, "ui." .. kind .. "'s body is not its last argument", "the body last", "more after it") end
      body = v
    elseif v ~= nil then
      if o then fail("N01", at, "ui." .. kind .. " has an extra argument " .. describe(v), "(head, { props }, body)", describe(v)) end
      o = v
    end
  end
  return o, body
end

local function container(kind, has_head, needs_body)
  return function(...)
    local at = where()
    local args = table.pack(...)
    local n = node(kind, at)
    local from = 1
    if has_head then n.a[1] = head_text(kind, args[1], at, "caption"); from = 2 end
    local o, body = tail(kind, args, from, at)
    n.o = check_opts(kind, o, at)
    if n.o.close ~= nil then use_action(n.o.close, "none", at, "ui." .. kind .. "'s close", false) end
    if needs_body or body then run_body(n, body) end
    return n
  end
end
T.group = container("group", true, true)
T.foot = container("foot", false, true)

function T.row(...)
  local at = where()
  local args = table.pack(...)
  local n = node("row", at)
  local from = 1
  if type(args[1]) == "string" then n.o_key = args[1]; from = 2 end
  local o, body = tail("row", args, from, at)
  n.o = check_opts("row", o, at)
  if n.o_key then n.o.key = n.o_key end
  if n.o.close ~= nil then use_action(n.o.close, "none", at, "ui.row's close", false) end
  run_body(n, body)
  local grow = 0
  for _, k in ipairs(n.kids) do if k.o.grow then grow = grow + 1 end end
  if grow > 1 then fail("N01", at, "ui.row has " .. grow .. " children with grow", "one child that grows", tostring(grow)) end
  return n
end

function T.scroll(key, ...)
  local at = where()
  local n = node("scroll", at)
  if type(key) ~= "string" or not key:match("^[%a_][%w_]*$") then
    fail("N01", at, "ui.scroll's key is " .. describe(key), "a word: ui.scroll(\"list\", function() ... end)", describe(key))
  end
  local o, body = tail("scroll", table.pack(...), 1, at)
  n.o = check_opts("scroll", o, at)
  n.o.key = key
  if n.o.new ~= nil then
    local b = n.o.new
    local ty = DECL[b]
    if not ty or ty.t ~= "int" or ty.derived or ty.opt then
      fail("T03", at, "ui.scroll's new " .. describe(b), "an int state the engine writes", ty and tname(ty) or "undeclared")
    end
    ENGINE_OWNED[b] = "the scroll's new="
  end
  run_body(n, body)
  return n
end

function T.rule(o)
  local at = where()
  local n = node("rule", at)
  n.o = check_opts("rule", o, at)
  return n
end

local function leaf(kind)
  return function(text, o, extra)
    local at = where()
    local n = node(kind, at)
    if extra ~= nil then fail("N01", at, "ui." .. kind .. " has an extra argument " .. describe(extra), "(TEXT, { props })", describe(extra)) end
    n.a[1] = head_text(kind, text, at, kind == "info" and "label" or "text")
    n.o = check_opts(kind, o, at)
    return n
  end
end
T.label = leaf("label")
T.info = leaf("info")

function T.note(o, extra)
  local at = where()
  local n = node("note", at)
  if type(o) ~= "table" or extra ~= nil then
    fail("N01", at, "ui.note takes its props alone", "ui.note({ icon = \"warn\", lines = { ... } })", describe(o))
  end
  n.o = check_opts("note", o, at)
  if type(n.o.lines) ~= "table" then fail("N01", at, "ui.note has no lines", "lines = { \"...\" }", "none") end
  return n
end

function T.empty(title, o, body)
  local at = where()
  local n = node("empty", at)
  if type(o) == "function" then o, body = nil, o end
  n.a[1] = head_text("empty", title, at, "title")
  n.o = check_opts("empty", o, at)
  if body then run_body(n, body) end
  return n
end

local function row_hands() return ROWCTX and "row" or "none" end

function T.bubble(text, o)
  local at = where()
  local n = node("bubble", at)
  n.a[1] = head_text("bubble", text, at, "text")
  n.o = check_opts("bubble", o, at)
  if n.o.on then use_action(n.o.on, row_hands(), at, "ui.bubble's on", true) end
  if n.o.cut then use_action(n.o.cut, row_hands(), at, "ui.bubble's cut", true) end
  return n
end

-- a button and a link name their action; the label, icon and hint are the
-- action's unless the site gives its own
local function actioned(kind)
  return function(name, o, extra)
    local at = where()
    local n = node(kind, at)
    if extra ~= nil or (o ~= nil and type(o) ~= "table") then
      fail("N01", at, "ui." .. kind .. "(" .. q(name) .. ") has a positional " .. describe(extra or o), "ui." .. kind .. "(ACTION, { props })",
           describe(extra or o), kind == "link" and "{ value = ... }" or "{ kind = ... }")
    end
    n.o = check_opts(kind, o, at)
    local hands = n.o.arg ~= nil and "arg" or row_hands()
    local a = use_action(name, hands, at, "ui." .. kind .. "(" .. q(name) .. ")", true)
    n.action = name
    n.a[1] = n.o.label or a.label
    if n.o.label == nil then compile_tpl(a.label, nil, at) end
    if n.o.arg ~= nil and type(n.o.arg) ~= "string" and math.type(n.o.arg) ~= "integer" then
      fail("N01", at, "ui." .. kind .. "'s arg is " .. describe(n.o.arg), "a literal text or int", describe(n.o.arg))
    end
    return n
  end
end
T.button = actioned("button")
T.link = actioned("link")

-- a two-way control names its state; its on= names an action or hook that
-- takes nothing (the value is in state)
local function two_way(kind, name, want, o, at)
  local n = node(kind, at)
  if type(name) ~= "string" then
    fail("N01", at, "ui." .. kind .. "'s head is " .. describe(name), "the state it edits, by name", describe(name),
         type(o) == "string" and "ui." .. kind .. "(\"" .. o .. "\", { label = \"" .. name .. "\" })" or nil)
  end
  local ty = DECL[name]
  if not ty then undeclared(name, at) end
  if ty.derived then fail("S09", at, "ui." .. kind .. " edits the derive " .. q(name), "a state", "a derive") end
  local ok = false
  for _, t in ipairs(want) do if ty.t == t and not ty.opt then ok = true end end
  if not ok then fail("T03", at, "ui." .. kind .. " edits " .. q(name) .. ", which is " .. tname(ty), table.concat(want, " or "), tname(ty)) end
  n.a[2] = name
  n.o = check_opts(kind, o, at)
  n.a[1] = n.o.label or ""
  if n.o.when ~= nil and n.o.off == nil then
    fail("O01", at, "ui." .. kind .. "(" .. q(name) .. ") can be off and says no off", "off = \"hide\" or \"grey\"", "none", "{ off = \"grey\" }")
  end
  if n.o.off ~= nil and n.o.when == nil then
    fail("O02", at, "ui." .. kind .. "(" .. q(name) .. ") has off and no when", "off with when", "off alone", "drop off")
  end
  for _, p in ipairs({ "on", "changed" }) do
    local h = n.o[p]
    if h ~= nil then
      local a = use_action(h, ACTIONS[h] and ACTIONS[h].takes or "none", at, "ui." .. kind .. "'s " .. p, false)
      if a.takes ~= "none" then
        fail("W01", at, "ui." .. kind .. "'s " .. p .. " names " .. q(h) .. ", which takes " .. q(a.takes), "takes = \"none\"",
             q(a.takes), "takes = \"none\", reading s." .. name)
      end
    end
  end
  return n
end
function T.toggle(name, o) return two_way("toggle", name, { "bool" }, o, where()) end
function T.slider(name, o)
  local at = where()
  local n = two_way("slider", name, { "int", "number" }, o, at)
  if n.o.lo == nil or n.o.hi == nil then fail("N01", at, "ui.slider(" .. q(name) .. ") has no range", "lo = and hi =", "none") end
  return n
end
function T.field(name, o) return two_way("field", name, { "text" }, o, where()) end

-- a list's filter (a text state the rows' text key is matched against) and its
-- group (a caption over the rows: a template of the row, drawn where it changes)
local function list_opts(kind, n, m, ctx, at)
  if n.o.filter ~= nil then
    local ty = DECL[n.o.filter]
    if not ty then undeclared(n.o.filter, at) end
    if ty.t ~= "text" then fail("T03", at, "ui." .. kind .. "'s filter " .. q(n.o.filter) .. " is " .. tname(ty), "a text state", tname(ty)) end
    if m.fields[m.key].t ~= "text" then
      fail("T03", at, "ui." .. kind .. "'s filter: the rows' key " .. q(m.key) .. " is " .. tname(m.fields[m.key]), "a text key",
           tname(m.fields[m.key]), "filter matches the model's key field, a text")
    end
  end
  if n.o.group ~= nil then
    local sr = ROWCTX
    ROWCTX = ctx
    local ok, err = pcall(compile_tpl, n.o.group, nil, at)
    ROWCTX = sr
    if not ok then error(err, 0) end
  end
end

local function model_arg(kind, name, at)
  local ty = type(name) == "string" and DECL[name]
  if not ty then undeclared(tostring(name), at) end
  if ty.t ~= "model" then fail("T03", at, "ui." .. kind .. " lists " .. q(name) .. ", which is " .. tname(ty), "a model", tname(ty)) end
  ty.name = name
  return ty
end

function T.rows(name, o, body)
  local at = where()
  if body == nil and type(o) == "function" then o, body = nil, o end
  local n = node("rows", at)
  if ROWCTX then fail("N03", at, "ui.rows inside a row", "rows at a page's level", "rows in rows") end
  local m = model_arg("rows", name, at)
  n.a[1] = name
  n.o = check_opts("rows", o, at)
  if n.o.cur ~= nil then
    local ty = DECL[n.o.cur]
    if not ty or ty.t ~= "rowof" or ty.model ~= name then
      fail("T03", at, "ui.rows' cur " .. describe(n.o.cur), "a ui.row_of(\"" .. name .. "\") state", ty and tname(ty) or "undeclared")
    end
  end
  if n.o.current ~= nil then
    local ty = DECL[n.o.current]
    local kt = m.fields[m.key]
    if not ty then undeclared(n.o.current, at) end
    if not (ty.t == kt.t or (ty.t == "enum" and kt.t == "text")) then
      fail("T03", at, "ui.rows' current " .. q(n.o.current) .. " is " .. tname(ty), "a state of the key's type (" .. tname(kt) .. ")", tname(ty))
    end
  end
  if n.o.check and n.o.current == nil then fail("N01", at, "ui.rows has check and no current", "check with current", "check alone") end
  if n.o.changed ~= nil then use_action(n.o.changed, "row", at, "ui.rows' changed", false) end
  local ctx = { fields = m.fields, order = m.order, name = name }
  n.ctx = ctx
  list_opts("rows", n, m, ctx, at)
  local sr = ROWCTX
  if n.o.by == nil then
    if type(body) ~= "function" then
      fail("N01", at, "ui.rows(" .. q(name) .. ") has no body", "ui.rows(MODEL, { props }, function() ... end)", describe(body),
           type(body) == "table" and "a table of cases needs by = \"FIELD\"" or nil)
    end
    local c = setmetatable({ kind = "case", a = {}, o = {}, kids = {}, at = at }, NodeMT)
    ROWCTX = ctx
    local ok, err = pcall(run_body, c, body)
    ROWCTX = sr
    if not ok then error(err, 0) end
    n.body = c
  else
    local by = n.o.by
    local fty = m.fields[by]
    if not fty or fty.t ~= "enum" or fty.opt then
      fail("T03", at, "ui.rows(" .. q(name) .. ") picks kinds by " .. describe(by), "an enum field of the rows",
           fty and tname(fty) or "no such field")
    end
    if type(body) ~= "table" then
      fail("N01", at, "ui.rows(" .. q(name) .. ", { by = " .. q(by) .. " }) has no cases", "a table of functions, one per value",
           describe(body))
    end
    for k, f in pairs(body) do
      if k ~= "_" and not fty.set[k] then
        fail("T02", at, "ui.rows(" .. q(name) .. ") has a case " .. q(k) .. " that " .. by .. " cannot be",
             "one of " .. table.concat(fty.values, ", "), q(k))
      end
      if type(f) ~= "function" then
        fail("N01", at, "the case " .. q(k) .. " of ui.rows(" .. q(name) .. ") is " .. describe(f), "a function", describe(f),
             type(f) == "table" and "function() ui." .. tostring(f[1]) .. "(...) end" or nil)
      end
    end
    local missing = {}
    for _, v in ipairs(fty.values) do if not body[v] and not body["_"] then missing[#missing + 1] = v end end
    if #missing > 0 then
      fail("T04", at, "ui.rows(" .. q(name) .. ") has no case for " .. by .. " = " .. table.concat(missing, ", "),
           "a case for every value, or _", "none", missing[1] .. " = function() ... end")
    end
    n.cases = {}
    local made = {}
    for _, v in ipairs(fty.values) do
      local f = body[v] or body["_"]
      if made[f] then n.cases[v] = made[f]
      else
        local c = setmetatable({ kind = "case", a = { v }, o = {}, kids = {}, at = at }, NodeMT)
        ROWCTX = ctx
        local ok, err = pcall(run_body, c, f)
        ROWCTX = sr
        if not ok then error(err, 0) end
        made[f], n.cases[v] = c, c
      end
    end
  end
  if n.o.empty ~= nil then compile_tpl(n.o.empty, false, at) end
  return n
end

function T.switch(name, cases, extra)
  local at = where()
  local n = node("switch", at)
  if type(name) ~= "string" then fail("N01", at, "ui.switch's head is " .. describe(name), "a state's name", describe(name)) end
  if extra ~= nil then fail("N01", at, "ui.switch has an extra argument", "ui.switch(NAME, { value = function() ... end })", describe(extra)) end
  local ty = resolve(name, nil, at, name)
  local values
  if ty.t == "enum" then values = ty.values
  elseif ty.t == "bool" then values = { "true", "false" }
  else
    fail("T03", at, "ui.switch(" .. q(name) .. ") switches on " .. tname(ty), "an enum or a bool", tname(ty),
         "shown = \"NAME=value\" on each part")
  end
  if ty.opt then fail("T03", at, "ui.switch(" .. q(name) .. ") switches on an optional", "an enum or a bool that is never absent", tname(ty)) end
  local set = {}
  for _, v in ipairs(values) do set[v] = true end
  if type(cases) ~= "table" then fail("N01", at, "ui.switch's cases are " .. describe(cases), "{ value = function() ... end }", describe(cases)) end
  for k, f in pairs(cases) do
    k = tostring(k)
    if k ~= "_" and not set[k] then
      fail("T02", at, "ui.switch(" .. q(name) .. ") has a case " .. q(k) .. " it cannot be", "one of " .. table.concat(values, ", "), q(k))
    end
    if type(f) ~= "function" then fail("N01", at, "the case " .. q(k) .. " is " .. describe(f), "a function", describe(f)) end
  end
  local missing = {}
  for _, v in ipairs(values) do if cases[v] == nil and cases[v == "true" or (v ~= "false" and v)] == nil and not cases["_"] then missing[#missing + 1] = v end end
  if #missing > 0 then
    fail("T04", at, "ui.switch(" .. q(name) .. ") has no case for " .. table.concat(missing, ", "), "a case for every value, or _",
         "none", missing[1] .. " = function() end")
  end
  n.a[1], n.o = name, { key = "switch:" .. name }
  n.built = {}
  for _, v in ipairs(values) do
    local f = cases[v] or (v == "true" and cases[true]) or (v == "false" and cases[false]) or cases["_"]
    local c = setmetatable({ kind = "case", a = { v }, o = { key = v }, kids = {}, at = at }, NodeMT)
    run_body(c, f)
    n.built[v] = c
  end
  return n
end

function T.popover(key, o, body)
  local at = where()
  local n = node("popover", at)
  if type(key) ~= "string" then fail("N01", at, "ui.popover's key is " .. describe(key), "a text", describe(key)) end
  n.a[1] = key
  n.o = check_opts("popover", o, at)
  n.o.key = key
  if n.o.close ~= nil then use_action(n.o.close, "none", at, "ui.popover's close", false) end
  if n.o.anchor ~= nil then compile_tpl(n.o.anchor, nil, at) end
  run_body(n, body)
  return n
end

function T.dialog(title, o, body)
  local at = where()
  local n = node("dialog", at)
  n.a[1] = head_text("dialog", title, at, "title")
  n.o = check_opts("dialog", o, at)
  local buttons = n.o.buttons
  if type(buttons) ~= "table" or #buttons == 0 then
    fail("N01", at, "ui.dialog's buttons are " .. describe(buttons), "buttons = { \"cancel\", \"switch\" }: actions", describe(buttons))
  end
  for _, b in ipairs(buttons) do use_action(b, "none", at, "ui.dialog's button " .. q(b), true) end
  if n.o.default ~= nil then
    local ok = false
    for _, b in ipairs(buttons) do if b == n.o.default then ok = true end end
    if not ok then fail("N01", at, "ui.dialog's default " .. q(n.o.default), "one of its buttons", q(n.o.default)) end
  end
  run_body(n, body)
  return n
end

-- -- the C engine's kinds ------------------------------------------------------------

-- a state named by a node's head, of one of the types `want` names
local function head_state(kind, name, want, at)
  if type(name) ~= "string" then fail("N01", at, "ui." .. kind .. "'s head is " .. describe(name), "a state's name", describe(name)) end
  local ty = DECL[name]
  if not ty then undeclared(name, at) end
  for _, t in ipairs(want) do if ty.t == t and not ty.opt then return ty end end
  fail("T03", at, "ui." .. kind .. " shows " .. q(name) .. ", which is " .. tname(ty), table.concat(want, " or "), tname(ty))
end

-- a bar of how much of max= is done: its head an int state, or a template of an int
function T.progress(value, o)
  local at = where()
  local n = node("progress", at)
  if type(value) == "string" and not value:find("{", 1, true) then head_state("progress", value, { "int" }, at); value = "{" .. value .. "}" end
  n.a[1] = head_text("progress", value, at, "value")
  n.o = check_opts("progress", o, at)
  if n.o.max == nil then fail("N01", at, "ui.progress(" .. q(value) .. ") has no max", "max = \"{NAME}\" (an int), or 0 for work of no known size", "none") end
  return n
end

-- one of several values: an enum's, each worded by a label table, or a row_of's rows, each worded by text=
function T.seg(name, o)
  local at = where()
  local n = node("seg", at)
  local ty = head_state("seg", name, { "enum", "rowof" }, at)
  n.a[2] = name
  if ty.t == "rowof" then
    local m = DECL[ty.model]
    local ctx = { fields = m.fields, order = m.order, name = ty.model }
    local sr = ROWCTX
    ROWCTX = ctx
    local ok, err = pcall(check_opts, "seg", o, at)
    ROWCTX = sr
    if not ok then error(err, 0) end
    n.o = err
    if n.o.text == nil or n.o.labels ~= nil then
      fail("N01", at, "ui.seg(" .. q(name) .. ") over a row_of words each row with text", "text = \"{.FIELD}\"", n.o.labels and "labels" or "none")
    end
  else
    n.o = check_opts("seg", o, at)
    if n.o.text ~= nil then fail("N01", at, "ui.seg(" .. q(name) .. "): text words a row_of's rows", "labels = \"TABLE\" for an enum", "text") end
    local tab = n.o.labels and LABELS[n.o.labels]
    if not tab then
      fail("T02", at, "ui.seg(" .. q(name) .. ") has no label table " .. describe(n.o.labels), "labels = the name of a ui.labels table",
           describe(n.o.labels), "ui.labels(\"NAME\", { value = \"Word\", ... })")
    end
    local miss = {}
    for _, v in ipairs(ty.values) do if tab[v] == nil then miss[#miss + 1] = v end end
    if #miss > 0 then
      fail("T04", at, "ui.seg(" .. q(name) .. "): the labels " .. q(n.o.labels) .. " have no word for " .. table.concat(miss, ", "),
           "a word for each value", "none for " .. miss[1])
    end
  end
  n.a[1] = n.o.label or ""
  if n.o.on ~= nil then use_action(n.o.on, "none", at, "ui.seg's on", false) end
  return n
end

-- a text state's lines, or a model's rows' first text field, read only
function T.text_view(name, o)
  local at = where()
  local n = node("text_view", at)
  local ty = head_state("text_view", name, { "text", "model" }, at)
  n.a[2] = name
  n.o = check_opts("text_view", o, at)
  if n.o.face == "ui" and n.o.syntax ~= nil then fail("N01", at, "ui.text_view with face = \"ui\" and syntax", "prose takes no syntax", "syntax") end
  if ty.t == "model" then
    for _, f in ipairs(ty.order) do
      if f ~= ty.key and ty.fields[f].t == "text" and not ty.fields[f].opt then n.field = f; break end
    end
    if not n.field then fail("T03", at, "ui.text_view(" .. q(name) .. "): its model has no text field", "a text field beside the key", "none") end
  end
  return n
end

function T.menu(caption, body)
  local at = where()
  local n = node("menu", at)
  if type(caption) == "function" then caption, body = "", caption end
  n.a[1] = head_text("menu", caption or "", at, "caption")
  n.o = {}
  run_body(n, body)
  return n
end

-- an item of a menu: its action's label and icon unless it gives its own
function T.item(name, o)
  local at = where()
  local n = node("item", at)
  if n.up.kind ~= "menu" then fail("N03", at, "ui.item outside a ui.menu", "items in a menu's body", "ui." .. n.up.kind) end
  n.o = check_opts("item", o, at)
  use_action(name, row_hands(), at, "ui.item(" .. q(name) .. ")", true)
  n.action = name
  return n
end

-- rows as a table: a header of cols, one ui.col a column, each a template over the row
function T.table(name, o, body)
  local at = where()
  if body == nil and type(o) == "function" then o, body = nil, o end
  local n = node("table", at)
  if ROWCTX then fail("N03", at, "ui.table inside a row", "a table at a page's level", "a table in rows") end
  local m = model_arg("table", name, at)
  n.a[1] = name
  n.o = check_opts("table", o, at)
  if n.o.cur ~= nil then
    local ty = DECL[n.o.cur]
    if not ty or ty.t ~= "rowof" or ty.model ~= name then
      fail("T03", at, "ui.table's cur " .. describe(n.o.cur), "a ui.row_of(\"" .. name .. "\") state", ty and tname(ty) or "undeclared")
    end
  end
  for _, p in ipairs({ "on", "changed" }) do
    if n.o[p] ~= nil then use_action(n.o[p], "row", at, "ui.table's " .. p, false) end
  end
  local c = setmetatable({ kind = "case", a = {}, o = {}, kids = {}, at = at }, NodeMT)
  local sr = ROWCTX
  ROWCTX = { fields = m.fields, order = m.order, name = name }
  local ok, err = pcall(run_body, c, body)
  ROWCTX = sr
  if not ok then error(err, 0) end
  for _, k in ipairs(c.kids) do
    if k.kind ~= "col" then fail("N03", k.at, "ui." .. k.kind .. " in a ui.table", "ui.col, one a column", "ui." .. k.kind) end
  end
  if n.o.cols and #n.o.cols ~= #c.kids then
    fail("N01", at, "ui.table(" .. q(name) .. ") has " .. #n.o.cols .. " headers and " .. #c.kids .. " columns", "one header a column", tostring(#c.kids))
  end
  n.body = c
  return n
end
function T.col(text, extra)
  local at = where()
  local n = node("col", at)
  if extra ~= nil then fail("N01", at, "ui.col takes its text alone", "ui.col(\"{.FIELD}\")", describe(extra)) end
  n.a[1] = head_text("col", text, at, "text")
  n.o = {}
  return n
end

-- a tab strip bound two-way to an enum: a tab a value, worded by the label table
-- labels= (and drawn with icons=), offered= a condition per value it shows
function T.tabs(name, o, extra)
  local at = where()
  local n = node("tabs", at)
  if extra ~= nil then fail("N01", at, "ui.tabs takes its state and its props", "ui.tabs(NAME, { labels = \"TABLE\" })", describe(extra)) end
  local ty = head_state("tabs", name, { "enum" }, at)
  n.a[2] = name
  n.o = check_opts("tabs", o, at)
  for _, p in ipairs({ "labels", "icons" }) do
    local tn = n.o[p]
    if tn == nil and p == "labels" then
      fail("N01", at, "ui.tabs(" .. q(name) .. ") has no labels", "labels = the name of a ui.labels table", "none")
    end
    if tn ~= nil and not LABELS[tn] then
      fail("T02", at, "ui.tabs(" .. q(name) .. ") has no label table " .. q(tn), p .. " = the name of a ui.labels table", q(tn),
           "ui.labels(\"" .. tn .. "\", { value = \"Word\", ... })")
    end
  end
  for v in pairs(n.o.offered or {}) do
    if not ty.set[v] then
      fail("N01", at, "ui.tabs(" .. q(name) .. ")'s offered names " .. describe(v) .. ", not a value", "one of " .. table.concat(ty.values, ", "), describe(v))
    end
  end
  local miss = {}
  for _, v in ipairs(ty.values) do if LABELS[n.o.labels][v] == nil then miss[#miss + 1] = v end end
  if #miss > 0 then
    fail("T04", at, "ui.tabs(" .. q(name) .. "): the labels " .. q(n.o.labels) .. " have no word for " .. table.concat(miss, ", "),
         "a word for each value", "none for " .. miss[1])
  end
  if n.o.on ~= nil then use_action(n.o.on, "none", at, "ui.tabs' on", false) end
  return n
end

-- rows as cards: one ui.card a row, a stop that fires its action with the row
function T.cards(name, o, body)
  local at = where()
  if body == nil and type(o) == "function" then o, body = nil, o end
  local n = node("cards", at)
  if ROWCTX then fail("N03", at, "ui.cards inside a row", "cards at a page's level", "cards in rows") end
  local m = model_arg("cards", name, at)
  n.a[1] = name
  n.o = check_opts("cards", o, at)
  if n.o.cur ~= nil then
    local ty = DECL[n.o.cur]
    if not ty or ty.t ~= "rowof" or ty.model ~= name then
      fail("T03", at, "ui.cards' cur " .. describe(n.o.cur), "a ui.row_of(\"" .. name .. "\") state", ty and tname(ty) or "undeclared")
    end
  end
  if n.o.current ~= nil then
    local ty = DECL[n.o.current]
    local kt = m.fields[m.key]
    if not ty then undeclared(n.o.current, at) end
    if not (ty.t == kt.t or (ty.t == "enum" and kt.t == "text")) then
      fail("T03", at, "ui.cards' current " .. q(n.o.current) .. " is " .. tname(ty), "a state of the key's type (" .. tname(kt) .. ")", tname(ty))
    end
  end
  if n.o.new ~= nil then use_action(n.o.new, "none", at, "ui.cards' new", true) end
  if n.o.changed ~= nil then use_action(n.o.changed, "row", at, "ui.cards' changed", false) end
  local ctx = { fields = m.fields, order = m.order, name = name }
  n.ctx = ctx
  list_opts("cards", n, m, ctx, at)
  local c = setmetatable({ kind = "case", a = {}, o = {}, kids = {}, at = at }, NodeMT)
  local sr = ROWCTX
  ROWCTX = ctx
  local ok, err = pcall(run_body, c, body)
  ROWCTX = sr
  if not ok then error(err, 0) end
  if #c.kids ~= 1 or c.kids[1].kind ~= "card" then
    fail("N03", at, "ui.cards(" .. q(name) .. ")'s body is not one ui.card", "one ui.card, drawn a row", #c.kids == 1 and "ui." .. c.kids[1].kind or #c.kids .. " nodes")
  end
  n.body = c
  return n
end
function T.card(name, o)
  local at = where()
  local n = node("card", at)
  if n.up.kind ~= "case" or not ROWCTX then fail("N03", at, "ui.card outside a ui.cards", "a card in a ui.cards' body", "ui." .. n.up.kind) end
  n.o = check_opts("card", o, at)
  use_action(name, row_hands(), at, "ui.card(" .. q(name) .. ")", true)
  n.action = name
  return n
end

-- the room parted along dir= by sizes=, a child a part; a grid of cols x rows cells, a child a cell (or span of them)
function T.split(o, body)
  local at = where()
  local n = node("split", at)
  n.o = check_opts("split", o, at)
  if n.o.dir == nil or n.o.sizes == nil then fail("N01", at, "ui.split takes dir and sizes", "ui.split({ dir = \"row\", sizes = { 1, 2 } }, body)", "none") end
  run_body(n, body)
  return n
end
function T.grid(o, body)
  local at = where()
  local n = node("grid", at)
  n.o = check_opts("grid", o, at)
  if n.o.cols == nil or n.o.rows == nil then fail("N01", at, "ui.grid takes cols and rows", "ui.grid({ cols = 2, rows = 3 }, body)", "none") end
  run_body(n, body)
  return n
end
function T.panel(key, body)
  local at = where()
  local n = node("panel", at)
  if type(key) ~= "string" or not key:match("^[%a_][%w_]*$") then
    fail("N01", at, "ui.panel's key is " .. describe(key), "a word: ui.panel(\"side\", function() ... end)", describe(key))
  end
  n.o = { key = key }
  run_body(n, body)
  return n
end

-- a rect the app draws itself: draw(x, y, w, h) is called inside each frame,
-- with the cart's own verbs, and writes no state
function T.canvas(key, o)
  local at = where()
  local n = node("canvas", at)
  if type(key) ~= "string" or not key:match("^[%a_][%w_]*$") then
    fail("N01", at, "ui.canvas's key is " .. describe(key), "a word: ui.canvas(\"dial\", { draw = draw })", describe(key))
  end
  n.o = check_opts("canvas", o, at)
  if n.o.draw == nil then fail("N01", at, "ui.canvas(" .. q(key) .. ") has no draw", "draw = function(x, y, w, h) ... end", "none") end
  if n.o.pointer ~= nil then use_action(n.o.pointer, "pointer", at, "ui.canvas(" .. q(key) .. ")'s pointer", false) end
  n.o.key = key
  return n
end

-- names an app no longer has, each with its replacement
local function retired(what, instead)
  return function() fail("N04", nil, what .. " is retired", instead, what, instead) end
end
T.at = retired("ui.at", "the engine lays out: ui.row, ui.foot, an anchor")
T.peek = retired("ui.peek", "state the engine writes")
T.scope = retired("ui.scope", "rows by kind, or a switch")
T.cell = retired("ui.cell", "ui.state { ... }")
T.key = retired("ui.key", "keys = { \"role:NAME\" } on the action")
T.per = retired("ui.per", "a template")
T.fmt = retired("ui.fmt", "a template's |fmt")
T.ref = retired("ui.ref", "a template")
T.rebuild = retired("ui.rebuild", "a template, a condition, a switch or rows")
T.head = retired("ui.head", "a tools node")
T.text = retired("ui.text", "ui.label(TEXT, { face = ..., size = \"fit\" })")
local function not_here(what, instead)
  return function() fail("N01", nil, what .. " is not in the UI tree API", instead, what, instead) end
end
T.pages = not_here("ui.pages", "ui.page(NAME, BUILD, { title = ... }) per page; the first registered opens")
T.run = not_here("ui.run", "the first page ui.page registers opens")
T.list = not_here("ui.list", "ui.rows(MODEL, { current = ..., check = true }, function() ui.link(...) end)")
T.on = not_here("ui.on", "the handler is the third argument of ui.action or the second of ui.hook")

-- identity: a node's key is its head where that is a name, else its kind and a
-- number; two siblings with one key are N02
local function ident(k)
  if k.o.key then return k.o.key end
  if k.kind == "item" then return k.action end
  if k.kind == "seg" or k.kind == "text_view" then return k.a[2] end
  if k.kind == "table" then return k.a[1] end
  if k.kind == "button" or k.kind == "link" then return k.action end
  if k.kind == "toggle" or k.kind == "slider" or k.kind == "field" then return k.a[2] end
  if k.kind == "rows" then return k.a[1] end
  return nil
end
local function finish(n, path)
  n.path = path
  local seen = {}
  for i, k in ipairs(n.kids) do
    local key = ident(k)
    if not key then
      key = k.kind .. "#" .. (i - 1)        -- a node with no key: its kind and place (§7)
    elseif seen[key] then
      fail("N02", k.at, "two nodes keyed " .. q(key) .. " under " .. q(path), "one node per key",
           "a second " .. k.kind .. " (the first at " .. seen[key] .. ")", "a key of its own: { key = \"...\" }")
    end
    seen[key] = k.at
    k.rkey = key
    -- the engine's id: the keys from the page, an unkeyed node by its place
    -- alone (an id is at most 31 bytes, F-17)
    finish(k, path .. "/" .. (ident(k) or tostring(i - 1)))

  end
  for v, c in pairs(n.built or {}) do finish(c, path .. "/" .. v) end
  if n.body then finish(n.body, path) end
  for _, c in pairs(n.cases or {}) do finish(c, path) end
end

-- page regions: a head, one scroll, a foot; overlays anywhere at the root
local function regions(root)
  local scroll = nil
  for _, k in ipairs(root.kids) do
    if k.kind == "scroll" then
      if scroll then fail("N03", k.at, "a second ui.scroll on the page", "one scroll", "two") end
      scroll = k
    elseif scroll and k.kind ~= "foot" and k.kind ~= "popover" and k.kind ~= "dialog" then
      fail("N03", k.at, "ui." .. k.kind .. " follows the page's scroll outside the foot", "it inside the scroll, or in ui.foot",
           "after the scroll")
    end
  end
end

-- -- pages, the commit and the commands --------------------------------------------------

local function build_page(p)
  local root = setmetatable({ kind = "page", a = { p.def.title }, o = {}, kids = {}, at = p.def.at }, NodeMT)
  building = p.def.name
  local ok, err = pcall(run_body, root, p.def.fn)
  building = nil
  if not ok then error(err, 0) end
  regions(root)
  finish(root, p.def.name)
  p.root = root
  trace("build", p.def.name, "page (builds " .. T.stats.builds .. ")")
end

local function page_def(name, at)
  local p = PAGES[name]
  if not p then
    fail("P01", at, "no page named " .. describe(name), "one of " .. table.concat(PAGE_ORDER, ", "), describe(name),
         "ui.page(\"" .. tostring(name) .. "\", build)")
  end
  return p
end

-- the event a canvas's pointer handler is handed: kind ("down", "drag",
-- "up"), x and y in the canvas, source ("touch"); read-only
local EvMT = { __newindex = function(_, k)
  fail("S03", where(), "an event is read-only (ev." .. tostring(k) .. " = ...)", "a read", "a write",
       "a canvas handler reads the event; it writes state")
end }
local function event(kind, x, y, source)
  return setmetatable({}, { __index = { kind = kind, x = x, y = y, source = source }, __newindex = EvMT.__newindex })
end

local function fire(name, from, ...)
  if name == nil then return end
  queue[#queue + 1] = { a = ACTIONS[name], args = table.pack(...), from = from }
end
-- an action the C engine fired: its `from` names the page already
local function fire_placed(name, from, ...)
  queue[#queue + 1] = { a = ACTIONS[name], args = table.pack(...), from = from, placed = true }
end
local function fire_fn(fn, label, from) queue[#queue + 1] = { fn = fn, label = label, from = from } end

local function no_command_in_build(what, at)
  if building then
    fail("B02", at, what .. " in the build of " .. q(building), "a command in a handler", "a build",
         "name an action the node fires: { on = \"NAME\" }")
  end
end

function T.push(name, row)
  local at = where()
  no_command_in_build("ui.push", at)
  local p = page_def(name, at)
  if p.arg then
    if row == nil then
      fail("P04", at, "the page " .. q(name) .. " takes a row of " .. q(DECL[p.arg].model), "ui.push(\"" .. name .. "\", row)", "none")
    end
    local m = model_of(DECL[p.arg].model)
    local key = type(row) == "table" and row[m.key] or row
    if not m.index[key] then
      fail("P04", at, "ui.push(" .. q(name) .. ") hands " .. describe(row) .. ", which is no row of " .. q(m.name),
           "a row of " .. q(m.name), describe(row))
    end
    setv(p.arg, key, "push " .. name, at)
  elseif row ~= nil then
    fail("P04", at, "the page " .. q(name) .. " takes no argument", "ui.push(\"" .. name .. "\")", describe(row),
         "arg = \"A_ROW_OF\" on ui.page")
  end
  local top = stack[#stack]
  local by = R.ids[R.focused or ""]
  local name = by and by.name or R.focused
  if name == "" then name = nil end
  if top then top.refocus, top.refocus_name = R.focused, name end
  stack[#stack + 1] = { def = p, fresh = 1 }
  GEN = GEN + 1
  trace("page", "push", "page " .. #stack .. (name and ", opened from " .. q(name) or ""))
end
function T.pop()
  no_command_in_build("ui.pop", where())
  if #stack > 1 then
    local under = stack[#stack - 1]
    trace("page", "back", "page " .. (#stack - 1) .. (under.refocus_name and ", focus back to " .. q(under.refocus_name) or ""))
    stack[#stack] = nil
    stack[#stack].fresh = 1
    GEN = GEN + 1
  else
    fail("P05", where(), "ui.pop() on the app's first page", "a page ui.push opened", "the first page")
  end
end

local function key_arg(a, key, at, what)
  if a.takes == "arg" then
    if not (type(key) == "string" or math.type(key) == "integer") then
      fail("A02", at, what .. " " .. q(a.name) .. " takes an arg and is handed " .. describe(key), "a text or an int", describe(key),
           what .. "(" .. (what == "ui.after" and "ms, " or "") .. "\"" .. a.name .. "\", key)")
    end
  elseif key ~= nil then
    fail("A02", at, what .. " hands " .. describe(key) .. " to " .. q(a.name) .. ", which takes " .. q(a.takes), "nothing",
         describe(key), "{ takes = \"arg\" } on " .. a.name .. ", or no key")
  end
end

-- the console's clock in ms since the app began (the cart's time()); a
-- host with no clock counts frames
local function now_ms()
  local t = type(time) == "function" and time() or nil
  if type(t) == "number" then return math.floor(t * 1000) end
  return frame_no * 1000 // 30
end

-- a timer's key is its identity: a second timer of one key replaces the
-- first; a timer with no key is keyed by its action
function T.after(ms, name, key)
  local at = where()
  no_command_in_build("ui.after", at)
  if not in_handler then
    fail("A10", at, "ui.after outside a handler", "a call from a handler or the opened hook", sealed and "a service or a callback" or "the top level")
  end
  if math.type(ms) ~= "integer" or ms < 0 then
    fail("N01", at, "ui.after's delay is " .. describe(ms), "an int of milliseconds", describe(ms), "ui.after(1200, \"NAME\")")
  end
  local a = ACTIONS[name] or use_action(name, "none", at, "ui.after", false)
  key_arg(a, key, at, "ui.after")
  local id = key ~= nil and key or name
  local replaced = ""
  for i = #timers, 1, -1 do
    if timers[i].id == id then table.remove(timers, i); replaced = ", replacing the timer keyed " .. show(id) end
  end
  timers[#timers + 1] = { at = now_ms() + ms, name = name, key = key, id = id }
  trace("timer", name, (key ~= nil and show(key) .. " " or "") .. "in " .. ms .. " ms" .. replaced .. "   (in " .. in_handler .. ")")
end

-- a timer cancelled runs nothing: KEY is the key it was armed with, or its
-- action's name for one armed with none (F-41)
function T.cancel(key)
  local at = where()
  no_command_in_build("ui.cancel", at)
  if not in_handler then
    fail("A10", at, "ui.cancel outside a handler", "a call from a handler or the opened hook",
         sealed and "a service or a callback" or "the top level")
  end
  if key == nil then fail("N01", at, "ui.cancel names no timer", "a timer's key, or its action's name", "nil") end
  for i = #timers, 1, -1 do
    if timers[i].id == key then
      trace("timer", timers[i].name, show(key) .. " cancelled   (in " .. in_handler .. ")")
      table.remove(timers, i)
    end
  end
end

-- a service reaches the app by posting a hook, run before the next frame
function T.post(name, arg)
  local at = where()
  no_command_in_build("ui.post", at)
  local a = ACTIONS[name]
  if not a or not a.hook then
    fail("A01", at, "ui.post names " .. describe(name) .. ", which is no hook", "a hook", a and "the action " .. q(name) or describe(name),
         "ui.hook(\"" .. tostring(name) .. "\", handler, { takes = \"arg\" })")
  end
  key_arg(a, arg, at, "ui.post")
  local from = in_handler and "post in " .. in_handler or "post from outside a handler"
  posted[#posted + 1] = { a = a, args = arg ~= nil and table.pack(arg) or { n = 0 }, from = from }
  trace("post", name, (arg ~= nil and show(arg) or "") .. "   (" .. from .. ")")
end

function T.focus(key)
  local at = where()
  if key == nil then
    fail("N04", at, "ui.focus() reads focus back", "ui.focus(KEY), a command", "a read", "\"{@focus}\" in a template")
  end
  if type(key) ~= "string" then fail("N01", at, "ui.focus's key is " .. describe(key), "a node's key", describe(key)) end
  no_command_in_build("ui.focus", at)
  R.pending_focus = { key = key, at = at }
end

T.sys = setmetatable({}, {
  __index = function(_, k)
    no_read_in_build("@" .. tostring(k), where())
    if SYS_T[k] == nil then fail("T02", where(), "ui.sys has no " .. q(k), "one of " .. table.concat(sorted_keys(SYS_T), ", "), q(k)) end
    return SYS[k]
  end,
  __newindex = function(_, k) fail("S03", where(), "a write into ui.sys." .. tostring(k), "a read", "a write") end,
})

-- the page a site was on, by its title; an overlay's site names its overlay
local function page_title(p)
  local t = p and p.root and render(p.root.a[1] or "") or ""
  return t ~= "" and t or (p and p.def.name or "")
end
local function where_on(from)
  if from:find(" dialog ", 1, true) or from:find("^timer") or from:find("^the ") then return "" end
  return " on " .. q(page_title(stack[#stack]))
end

local function run_queue()
  local q_ = queue
  queue = {}
  for _, h in ipairs(q_) do
    T.stats.handlers = T.stats.handlers + 1
    if h.a then
      local a = h.a
      if a.row and h.args.n == 0 then
        local m = model_of(DECL[a.row].model)
        local key = V[a.row]
        if key ~= nil then h.args = table.pack(frozen(m.rows[m.index[key]], "row " .. q(key) .. " of " .. q(m.name), m.ty)) end
      end
      if a.takes == "none" then h.args = { n = 0 } end
      if a.when and not cond(a.when) then
        trace("skip", a.name, "from " .. h.from .. " (when " .. a.when .. " is false)")
      elseif a.row and h.args.n == 0 then
        trace("skip", a.name, "from " .. h.from .. " (" .. a.row .. " holds no row)")
      elseif a.push then
        trace("action", a.name, "from " .. h.from .. (h.placed and "" or where_on(h.from)))
        in_handler = a.name
        T.push(a.push)
        in_handler = nil
      else
        trace("action", a.name, "from " .. h.from .. (h.placed and "" or where_on(h.from)))
        in_handler = a.name
        a.fn(table.unpack(h.args, 1, h.args.n))
        in_handler = nil
      end
    else
      in_handler = h.label
      h.fn()
      in_handler = nil
    end
  end
end

-- one action's or hook's handler, run as a frame's queue runs it, with no
-- page drawn: the checker's run of what a build never reaches
function T._fire(name, ...)
  local a = ACTIONS[name]
  if not a then fail("A01", where(), q(name) .. " is no action or hook", "a declared name", q(name)) end
  in_handler = name
  local ok, e = pcall(a.fn, ...)
  in_handler = nil
  if not ok then error(e, 0) end
end

function _layout(w, h, fs) SYS.fs = fs or 1 end
SYS.fs = 1

-- the back end (uitree_replay.lua or uitree_engine.lua): { frame = function(p) },
-- which builds page p where it holds no tree, draws it and queues what the user did
local BACK = { frame = nil }

local opened = false
local function open()
  seal()
  if not APP then fail("D04", "load", "the app has no ui.app declaration", "ui.app(TITLE, { icon = ... })", "none",
                       "ui.app(\"Counter\", { icon = \"calc\" })") end
  if not PAGE_ORDER[1] then fail("P01", "load", "the app registers no page", "ui.page(NAME, BUILD)", "none") end
  for i = #stack, 1, -1 do stack[i] = nil end
  stack[1] = { def = PAGES[PAGE_ORDER[1]], fresh = 1 }
  local h = ACTIONS.opened
  if h and h.hook then
    local arg = rawget(_G, "UI_LAUNCH") or ""
    T.stats.handlers = T.stats.handlers + 1
    trace("action", "opened", "from the engine (open)")
    in_handler = "opened"
    if h.takes == "arg" then h.fn(arg) else h.fn() end
    in_handler = nil
  end
end

function _draw()
  frame_no = frame_no + 1
  T.stats.frames = T.stats.frames + 1
  if not opened then opened = true; open() end
  for _, p in ipairs(posted) do queue[#queue + 1] = p end
  posted = {}
  for i = #timers, 1, -1 do
    local t = timers[i]
    if now_ms() >= t.at then
      if t.key ~= nil then fire(t.name, "timer " .. show(t.key), t.key) else fire(t.name, "timer") end
      table.remove(timers, i)
    end
  end
  run_queue()
  BACK.frame(stack[#stack])
  run_queue()
  if pmem then pmem(60, T.stats.builds); pmem(61, T.stats.frames); pmem(62, T.stats.handlers) end
end

-- -- the checker's entry: every page and case built over the stub engine ---------------

function T._check()
  local report = { errors = {}, pages = {} }
  local function err(e) report.errors[#report.errors + 1] = e end
  local ok, e = pcall(seal)
  if not ok then err(e) return report end
  if not APP then
    err("error[D04] load: the app has no ui.app declaration; fix: ui.app(\"TITLE\", { icon = \"...\" }) (expected an app declaration, found none)")
  end
  if not PAGE_ORDER[1] then err("error[P01] load: the app registers no page; fix: ui.page(NAME, BUILD) (expected a page, found none)") end
  for _, name in ipairs(PAGE_ORDER) do
    local p = { def = PAGES[name] }
    local ok2, e2 = pcall(build_page, p)
    if ok2 then report.pages[name] = p.root else err(e2) end
  end
  report.uses, report.actions, report.state_at, report.decl, report.page_order = USES, ACTIONS, STATE_AT, DECL, PAGE_ORDER
  report.labels, report.engine_owned = LABELS, ENGINE_OWNED
  return report
end

-- the layer's own tables, which tests/test_uitree_contract.py holds against
-- the contract table both layers obey
function T._contract()
  return { own = OWN, child = CHILD, action = ACTION_OPTS, hook = HOOK_OPTS,
           page = PAGE_OPTS, app = APP_FIELDS, takes = TAKES_N, roles = APP_ROLES, shell_roles = SHELL_ROLES }
end

-- a text snapshot of a built tree: kinds, keys and each prop's binding
function T._snapshot(n, depth, out)
  out = out or {}
  depth = depth or 0
  local props = {}
  for k, v in pairs(n.o) do
    if k ~= "key" then props[#props + 1] = k .. "=" .. (type(v) == "table" and "{...}" or "'" .. tostring(v) .. "'") end
  end
  table.sort(props)
  out[#out + 1] = string.rep("  ", depth) .. n.kind .. " '" .. (n.rkey or n.path or "?") .. "'"
                  .. (n.a[1] and "  '" .. tostring(n.a[1]) .. "'" or "") .. (#props > 0 and "  " .. table.concat(props, "  ") or "")
  for _, k in ipairs(n.kids) do T._snapshot(k, depth + 1, out) end
  for _, v in ipairs(sorted_keys(n.built or {})) do
    out[#out + 1] = string.rep("  ", depth + 1) .. "case " .. v
    for _, k in ipairs(n.built[v].kids) do T._snapshot(k, depth + 2, out) end
  end
  if n.body then for _, k in ipairs(n.body.kids) do T._snapshot(k, depth + 1, out) end end
  if n.cases then
    for _, v in ipairs(sorted_keys(n.cases)) do
      out[#out + 1] = string.rep("  ", depth + 1) .. "case " .. v
      for _, k in ipairs(n.cases[v].kids) do T._snapshot(k, depth + 2, out) end
    end
  end
  return out
end

-- what a back end reads and writes of the layer; it takes the table and
-- clears it before a cart's own scripts run
T._in = {
  E = E, T = T, V = V, VER = VER, DECL = DECL, ACTIONS = ACTIONS, ACTION_ORDER = ACTION_ORDER, LABELS = LABELS,
  KEYMAP = KEYMAP, SYS = SYS, STOPS = STOPS, DERIVED = DERIVED, ENGINE_OWNED = ENGINE_OWNED, stack = stack, R = R,
  BACK = BACK, ModelMT = ModelMT,
  fail = fail, q = q, show = show, trace = trace, lookup = lookup, render = render, cond = cond, truthy = truthy,
  frozen = frozen, event = event, setv = setv, fire = fire, fire_placed = fire_placed, fire_fn = fire_fn, build_page = build_page, page_title = page_title,
  model_of = model_of, touched = touched, bump = function() GEN = GEN + 1 end,
}

ui = T
