-- The Lua layer's own tables, one fact a line, for tests/test_uitree_contract.py:
--
--   hostlua lua_tables.lua LAYER_FILE
--
-- KIND k, PROP k p, COMMON p, WORD p w, ACTION p, HOOK p, PAGE p, APP f,
-- TAKES t, ROLE r, SHELLROLE r, VERB v (every name the layer's `ui` table has).

ui = setmetatable({ CUT = 4 }, { __index = function() return function() end end })
moybyte = setmetatable({ log = function() end }, { __index = function() return function() end end })
W, H = 320, 240

local f = assert(io.open(arg[1], "r"))
local chunk = assert(load(f:read("a"), "=uitree.lua"))
f:close()
chunk()

local function keys(t)
  local out = {}
  for k in pairs(t) do out[#out + 1] = tostring(k) end
  table.sort(out)
  return out
end

local c = ui._contract()
for _, k in ipairs(keys(c.props)) do
  print("KIND " .. k)
  for _, p in ipairs(keys(c.props[k])) do print("PROP " .. k .. " " .. p) end
end
for _, p in ipairs(keys(c.common)) do print("COMMON " .. p) end
for _, p in ipairs(keys(c.words)) do
  for _, v in ipairs(keys(c.words[p])) do print("WORD " .. p .. " " .. v) end
end
for tag, t in pairs({ ACTION = c.action, HOOK = c.hook, PAGE = c.page, APP = c.app, TAKES = c.takes,
                      ROLE = c.roles, SHELLROLE = c.shell_roles }) do
  for _, k in ipairs(keys(t)) do print(tag .. " " .. k) end
end
for _, v in ipairs(keys(ui)) do print("VERB " .. v) end
