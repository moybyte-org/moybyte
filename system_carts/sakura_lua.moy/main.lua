-- Sakura Lua -- the #67 A/B twin of sakura.moy, a SEPARATE cart so both sit on
-- the shelf and the same scene can be measured under either runtime.
--
-- The backdrop (images/bg.moyimg, byte-identical to sakura.moy's) is an image
-- supplied by the project owner (AI-generated; the project's own, no outside
-- rights holder), converted to the 320x240 MOY64 bitmap
-- by tools/import_sakura_bg.py. That same script generates the EMIT table
-- below, whose shedding points have to sit on THIS image's canopy; re-importing
-- regenerates both carts' tables together, and they must stay identical or the
-- parity test fails.
--
-- The port is line-faithful by design: same globals, same helper split, same
-- arithmetic in the same order, so the two runtimes produce bit-identical petal
-- state and draw streams (verified per frame by
-- experiments/lua_bridge/host_parity.py).
-- Kept in lockstep with sakura.moy/main.py: edit BOTH or the parity test fails.
-- Launches through the manifest "runtime": "lua" seam (Phase 2): the host runs
-- it via runtime/lua_host.py (lupa); the device shows the runtime-missing panel
-- until the moy_lua native module (Phase 1) lands.
--
-- Port conventions (the canonical .lua cart shapes, to be written up in the
-- #67 Phase 5 docs pass):
--   * same call names + args as Python (spr/cfg/rnd/make_layer/draw_layer/image)
--   * layers use colon calls: lay:spr(img, x, y)
--   * touch() returns MULTIPLE VALUES (x, y, tapped, held) or nil -- no table
--     allocated per call, which is the whole point of the Lua tier's GC story
--   * Python's int() is truncation toward zero: use trunc() below, NOT
--     math.floor (they differ on the negative x a wrapped petal can have)

EMIT = { {2, 143}, {6, 113}, {16, 102}, {24, 97}, {25, 80}, {26, 73}, {26, 106}, {40,
80}, {42, 107}, {44, 100}, {49, 54}, {49, 64}, {53, 98}, {57, 53}, {57, 110}, {59,
74}, {62, 44}, {66, 80}, {74, 40}, {75, 72}, {75, 105}, {79, 45}, {79, 77}, {80, 103},
{84, 27}, {86, 101}, {87, 29}, {96, 70}, {99, 40}, {99, 52}, {99, 84}, {99, 110},
{104, 71}, {105, 111}, {107, 57}, {113, 86}, {116, 93}, {117, 28}, {118, 40}, {119,
28}, {121, 59}, {121, 76}, {122, 33}, {122, 90}, {126, 71}, {128, 105}, {138, 69},
{139, 49}, {139, 102}, {145, 29}, {149, 77}, {152, 42}, {157, 13}, {158, 40}, {159,
68}, {164, 22}, {164, 59}, {167, 121}, {168, 77}, {169, 94}, {169, 105}, {171, 23},
{171, 122}, {174, 14}, {174, 68}, {174, 90}, {176, 109}, {177, 36}, {177, 86}, {182,
59}, {188, 19}, {189, 13}, {189, 57}, {192, 90}, {197, 67}, {198, 81}, {199, 106},
{201, 134}, {202, 38}, {202, 138}, {204, 110}, {207, 25}, {207, 41}, {207, 134}, {215,
55}, {217, 100}, {218, 60}, {218, 89}, {220, 140}, {222, 119}, {224, 126}, {228, 42},
{229, 65}, {231, 96}, {231, 150}, {234, 85}, {236, 55}, {236, 144}, {240, 46}, {240,
114}, {241, 131}, {245, 91}, {248, 39}, {248, 72}, {249, 151}, {250, 147}, {253, 80},
{259, 85}, {259, 153}, {260, 104}, {262, 48}, {262, 126}, {262, 139}, {264, 74}, {265,
113}, {274, 118}, {279, 83}, {281, 71}, {282, 136}, {285, 90}, {285, 123}, {290, 118},
{290, 128}, {292, 96}, {296, 88}, {306, 92} }

SIN = {}            -- sine LUT (built once); the hot loop indexes it, never calls math.sin
lay = nil           -- the static scene, inflated + painted once, copied per frame (#54)
petals = {}         -- each: {x, y, fall_speed, sway_phase, sway_amp, shade(0 near..2 far)}
base = 0            -- the run's blossom sheet column (base tile); each petal draws base + shade
t = 0.0

-- The petal colours live in sprites.moygfx: 12 tiles, one column per blossom
-- choice, three shades deep (0 near / 1 mid / 2 far, the near tile carrying a
-- white glint). Recolour a blossom in the sprite editor -- nothing here to match.
BLOSSOM_ORDER = {"pink", "white", "peach", "mixed"}   -- sheet column order (base = (i-1)*3)

-- Python int(): truncation toward zero. floor works for the non-negative cases
-- (_sin's phase, _shed's EMIT pick) but a wrapped petal's x can sit in (-8, 0),
-- where floor(-3.7) = -4 but Python int(-3.7) = -3 -- so _draw must use this.
local function trunc(v)
    if v >= 0 then return math.floor(v) end
    return math.ceil(v)
end

function _blossom_base()
    -- A run's blossom colour fixes the sheet column; each petal's tile is base + shade.
    -- Unknown names fall back to pink (base 0).
    local name = cfg("blossom", "pink")
    for i = 1, #BLOSSOM_ORDER do
        if BLOSSOM_ORDER[i] == name then
            return (i - 1) * 3
        end
    end
    return 0
end

function _build_sin()
    if #SIN == 0 then
        for i = 0, 255 do
            SIN[i + 1] = math.sin(i / 256.0 * 6.2831853)
        end
    end
end

function _sin(turn)
    return SIN[(math.floor(turn * 256.0) & 255) + 1]
end

function _shed(p, fresh)
    -- Place a petal at a random canopy blossom -- the tree shedding it. fresh=true
    -- starts it right at the cluster; else scatter it down the column so the air
    -- starts full.
    local n = #EMIT
    local ex, ey
    if n > 0 then
        local e = EMIT[(trunc(rnd(n)) % n) + 1]
        ex = e[1]
        ey = e[2]
    else
        ex = rnd(W)
        ey = 0.0
    end
    p[1] = ex + rnd(7.0) - 3.0
    if fresh then
        p[2] = ey - 2.0
    else
        p[2] = ey + rnd(H - ey + 10.0)
    end
    p[4] = rnd(1.0)
end

function _init()
    _build_sin()
    if lay == nil then                     -- allocate the scene buffer only once
        lay = make_layer(W, H)
    end
    local bg = image("bg")                 -- the painted cherry-tree scene (images/bg.moyimg)
    if bg ~= nil then
        lay:spr(bg, 0, 0)                  -- ONE native blit_indices bake
    end
    local n = trunc(tonumber(cfg("petal_count", 120)))
    local fall = tonumber(cfg("fall_speed", 30)) * 1.0
    base = _blossom_base()                 -- blossom fixes the tile column for the run
    petals = {}
    for i = 0, n - 1 do
        local shade = i % 3
        local spd = fall * (1.0 - 0.18 * shade) * (0.7 + rnd(0.6))
        local p = { 0.0, 0.0, spd, 0.0, 4.0 + rnd(9.0), shade }
        _shed(p, false)
        petals[#petals + 1] = p
    end
    t = 0.0
end

function _update(dt)
    if dt > 0.1 then
        dt = 0.1
    end
    t = t + dt
    local breeze = tonumber(cfg("breeze", 18)) * 1.0
    local tx, ty = touch()
    local cx = -999.0
    local cy = -999.0
    if tx ~= nil then
        cx = tx
        cy = ty
    end
    local R = 52.0
    for i = 1, #petals do
        local p = petals[i]
        p[4] = p[4] + dt * (0.32 + 0.06 * p[6])
        local sway = _sin(p[4]) * p[5]
        p[1] = p[1] + (breeze * (1.0 - 0.15 * p[6]) + sway) * dt
        p[2] = p[2] + p[3] * dt
        local dx = p[1] - cx
        local dy = p[2] - cy
        if -R < dx and dx < R and -R < dy and dy < R then
            local far = dx >= 0 and dx or -dx
            local ady = dy >= 0 and dy or -dy
            if ady > far then
                far = ady
            end
            local k = (R - far) / R * 130.0
            local inv = 1.0 / (far + 4.0)
            p[1] = p[1] + dx * inv * k * dt
            p[2] = p[2] + dy * inv * k * dt
        end
        if p[2] > H + 4.0 then
            _shed(p, true)
        elseif p[1] < -8.0 then
            p[1] = p[1] + W + 16.0
        elseif p[1] > W + 8.0 then
            p[1] = p[1] - W - 16.0
        end
    end
end

function _draw()
    -- Background: one flat blit. Petals: the naive per-petal spr() loop -- on the
    -- device bridge this feeds the SAME int16 batch array the Python cart feeds
    -- (hot verbs write it in C; see #67 Phase 1), so it stays one blit_batch.
    draw_layer(lay, 0, 0)
    for i = 1, #petals do
        local p = petals[i]
        spr(base + p[6], trunc(p[1]), trunc(p[2]), 0)
    end
end
