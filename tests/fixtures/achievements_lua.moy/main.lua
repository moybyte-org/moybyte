-- Achievements as a Lua service: the twin of
-- system_carts/moybyte.achievements.moy, the tier-conformance test's Lua
-- process (docs/os_2026-11.md, phase 5; tests/test_achievements_proc.py runs
-- the same test bodies over both).
--
-- It wakes only on the notify role's messages: achieve(kind, key), a
-- milestone, and award(id, quiet, day), a badge by its id. It records into
-- its document, hands it back through badges_save(doc), and posts a
-- notification on a fresh find. The console hands it `badges` (the
-- document its store holds, or nil), `badges_save` and `clock_day` (today as
-- YYYYMMDD off the bar's clock, 0 without one); the role's reads answer from
-- the document it hands back.

-- id, title, glyph, hidden, how (runtime/toolkit/widgets.py's ACHIEVEMENTS)
local CATALOGUE = {
    {"first_open",   "First Steps",     "app",      false, "Open a cart"},
    {"first_run",    "Lift Off!",       "run",      false, "Run a game"},
    {"first_paint",  "Little Artist",   "paint",    false, "Save a drawing"},
    {"first_map",    "Map Maker",       "map",      false, "Save a map"},
    {"first_code",   "Code Wizard",     "code",     false, "Save some code"},
    {"play_five",    "Cart Explorer",   "star",     false, "Play 5 carts"},
    {"toolbox",      "Toolbox Master",  "settings", false, "Use 3 editors"},
    {"decorator",    "Home Decorator",  "heart",    false, "Pick a wallpaper"},
    {"konami",       "Secret Coder",    "spark",    true,  "Type the secret code"},
    {"clock_tinker", "Time Traveler",   "smile",    true,  "Tap the clock 7 times"},
    {"secret_door",  "Secret Finder",   "key",      true,  "Knock on Settings 5 times"},
}
local TITLE, GLYPH, HINT = {}, {}, {}
for _, a in ipairs(CATALOGUE) do
    TITLE[a[1]], GLYPH[a[1]], HINT[a[1]] = a[2], a[3], a[5]
end

local EVENT = {
    open = "first_open", run = "first_run", paint_save = "first_paint",
    map_save = "first_map", code_save = "first_code", wallpaper_change = "decorator",
}
local TOOLBOX = {"code", "paint", "map"}
local PLAY_GOAL = 5

local found = {}                -- id -> the day it was found (0 unknown)
local played, played_keys = {}, {}
local seen, seen_keys = {}, {}

-- achievements.json's document: an empty map is left out (the store writes
-- it as {}), so no empty table crosses as a list.
local function doc()
    local d, count = {}, {}
    if next(found) then d.found = found end
    if #played_keys > 0 and not found.play_five then count.play_five = played_keys end
    if #seen_keys > 0 and not found.toolbox then count.toolbox = seen_keys end
    if next(count) then d.count = count end
    return d
end

local function save()
    if badges_save then badges_save(doc()) end
end

local function award(id, quiet, day)
    if found[id] or not TITLE[id] then return false end
    if day == nil then
        day = clock_day and clock_day() or 0
    end
    found[id] = day or 0
    save()
    if not quiet then
        moybyte.notify(TITLE[id], HINT[id], GLYPH[id] or "trophy")
    end
    return true
end

local function note(event, key)
    if event == "open" then
        if key ~= nil and not found.play_five and not played[key] then
            played[key] = true
            played_keys[#played_keys + 1] = key
            if #played_keys >= PLAY_GOAL then award("play_five") else save() end
        end
        award("first_open")
    elseif event == "editor" then
        local tool = false
        for _, v in ipairs(TOOLBOX) do tool = tool or v == key end
        if tool and not found.toolbox and not seen[key] then
            seen[key] = true
            seen_keys[#seen_keys + 1] = key
            if #seen_keys >= #TOOLBOX then award("toolbox") else save() end
        end
    elseif EVENT[event] then
        award(EVENT[event])
    end
end

function moy_start()
    if badges then
        for k, v in pairs(badges.found or {}) do
            if TITLE[k] then found[k] = v end
        end
        local count = badges.count or {}
        for _, k in ipairs(count.play_five or {}) do
            if not played[k] then played[k] = true; played_keys[#played_keys + 1] = k end
        end
        for _, k in ipairs(count.toolbox or {}) do
            if not seen[k] then seen[k] = true; seen_keys[#seen_keys + 1] = k end
        end
    end
    moybyte.on("achieve", function(who, ...) note(...) end)
    moybyte.on("award", function(who, ...) award(...) end)
end
