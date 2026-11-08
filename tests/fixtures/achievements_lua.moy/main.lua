-- Achievements as a Lua service: the twin of
-- system_carts/moybyte.achievements.moy, the tier-conformance test's Lua
-- process (docs/os_2026-11.md, phase 5; tests/test_achievements_proc.py runs
-- the same test bodies over both).
--
-- It wakes only on award(id) and progress(id, value), from a game or the
-- console, the sender the app id the OS stamped: an id the sender's
-- manifest does not declare is refused and the refusal logged. What is found
-- and each goal's last value live in its prefs ("badges", read strictly by
-- the console: runtime/system/achievements.py's read_badges); a fresh find
-- posts a notification. The console hands it game(app) (the app's name and
-- declared badges, nil for one that declares none) and clock_day() (today
-- as YYYYMMDD, 0 without a clock).

local KEY = "badges"

local function head(who, name, secret)
    if who == "system" then
        return secret and "You found a secret!" or "You found a badge!"
    end
    return name .. (secret and ": secret found!" or ": badge found!")
end

local function refuse(who, id, why)
    print("achievements: " .. tostring(who) .. "'s " .. tostring(id) .. " refused: " .. why)
end

-- The row as the console's strict reader takes it, or {} where it does not
-- read; an empty map is left out (the store writes it as {}).
local function read()
    local doc = prefs.get(KEY)
    if type(doc) ~= "table" then return {} end
    for app, rec in pairs(doc) do
        if type(app) ~= "string" or type(rec) ~= "table" or type(rec.name) ~= "string" then
            return {}
        end
    end
    return doc
end

local function take(who, id, value)
    local g = game(who)
    local a
    for _, b in ipairs(g and g.achievements or {}) do
        if b.id == id then a = b end
    end
    if a == nil then return refuse(who, id, "not declared") end
    if value ~= nil then
        if a.goal == nil then return refuse(who, id, "no goal") end
        if math.type(value) ~= "integer" then return refuse(who, id, "not a whole number") end
    end
    local doc = read()
    local rec = doc[who] or {}
    local found, values = rec.found or {}, rec.value or {}
    if found[id] then return end
    local fresh = value == nil or value >= a.goal
    if fresh then
        found[id] = {clock_day() or 0, a.title, a.icon, a.hint}
        values[id] = nil
    elseif values[id] == value then
        return
    else
        values[id] = value
    end
    rec = {name = g.name}
    if next(found) then rec.found = found end
    if next(values) then rec.value = values end
    doc[who] = rec
    prefs.set(KEY, doc)
    if fresh then
        moybyte.notify(head(who, g.name, a.secret), a.title, a.icon)
    end
end

function moy_start()
    moybyte.on("award", function(who, id) take(who, id, nil) end)
    moybyte.on("progress", function(who, id, value)
        if value == nil then return refuse(who, id, "no value") end
        take(who, id, value)
    end)
end
