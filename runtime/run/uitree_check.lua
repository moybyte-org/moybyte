-- The UI tree checker's Lua half: run a Lua app's scripts once over a stub
-- engine, which runs every declaration at its call, then build every page and
-- every case (ui._check); print what tools/uitree_lua_check.py reads.
--
--   hostlua uitree_check.lua LAYER_FILE CART_DIR SCRIPT... [-- AFTER...]
--
-- The AFTER scripts run once the pages are built: a test's ui._fire calls,
-- which run a handler as a frame would.
--
-- It runs in a host Lua with the debug library (the console's has none), so
-- a handler's parameter count is checked against its takes (A03). Each line
-- printed is one fact: ERR <message>, PAGE, STATE, FIELD, ACTION, PUSH,
-- ENGINE, LABELS, SNAP.

local LAYER, CART = arg[1], arg[2]
local scripts, after = {}, nil
for i = 3, #arg do
  if arg[i] == "--" then after = {}
  elseif after then after[#after + 1] = arg[i]
  else scripts[#scripts + 1] = arg[i] end
end

local function readfile(p)
  local f = io.open(p, "r")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

-- the stub engine: every verb answers nothing and draws nothing
ui = setmetatable({ CUT = 4 }, { __index = function() return function() end end })
moybyte = setmetatable({ log = function() end }, { __index = function() return function() end end })
W, H = 320, 240
for _, v in ipairs({ "btn", "btnp", "key", "keyp", "touch", "pmem", "time", "cfg", "read", "write", "beep", "sfx" }) do
  _G[v] = function() end
end

local function run(path, chunkname)
  local text = readfile(path)
  if not text then return "check: no file " .. path end
  local chunk, err = load(text, "=" .. chunkname)
  if not chunk then return err end
  local ok, e = pcall(chunk)
  if not ok then return tostring(e) end
end

local err = run(LAYER, "uitree.lua")
for _, name in ipairs(scripts) do
  if err then break end
  err = run(CART .. "/" .. name, name)
end
if err then
  print("ERR " .. err:gsub("\n", " "))
  return
end

local report = ui._check()
for _, e in ipairs(report.errors) do print("ERR " .. tostring(e):gsub("\n", " ")) end
for _, name in ipairs(report.page_order or {}) do print("PAGE " .. name) end
for name, ty in pairs(report.decl or {}) do
  local t = ty.derived and "derive" or ty.t
  print("STATE " .. name .. " " .. t .. (ty.t == "rowof" and " " .. ty.model or ""))
  local decl = ty.t == "record" and ty or (ty.t == "rowof" and report.decl[ty.model]) or nil
  if decl and decl.fields then
    for f in pairs(decl.fields) do print("FIELD " .. name .. " " .. f) end
  end
end
for name, a in pairs(report.actions or {}) do
  print("ACTION " .. name .. " " .. a.takes .. " " .. (a.hook and "hook" or "action"))
end
for name in pairs(report.engine_owned or {}) do print("ENGINE " .. name) end
for _, at in ipairs(report.state_at or {}) do print("STATEAT " .. at) end
for name in pairs(report.labels or {}) do print("LABELS " .. name) end
for _, name in ipairs(after or {}) do
  local e = run(CART .. "/" .. name, name)
  if e then print("ERR " .. e:gsub("\n", " ")) end
end
for _, name in ipairs(report.page_order or {}) do
  local root = report.pages and report.pages[name]
  if root then
    for _, l in ipairs(ui._snapshot(root)) do print("SNAP " .. l) end
  end
end
