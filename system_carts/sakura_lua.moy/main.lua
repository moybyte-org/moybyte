-- Sakura Lua -- the #67 A/B twin of sakura.moy, a SEPARATE cart so both sit on
-- the shelf and the same scene can be measured under either runtime. The port
-- is line-faithful by design: same globals, same helper split, same arithmetic
-- in the same order, so the two runtimes produce bit-identical petal state and
-- draw streams (verified per frame by experiments/lua_bridge/host_parity.py).
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

EMIT = { {20, 44}, {21, 76}, {21, 114}, {24, 133}, {28, 61}, {28, 152}, {30,
95}, {36, 114}, {36, 133}, {36, 152}, {38, 76}, {40, 95}, {44, 19}, {46, 57},
{53, 55}, {54, 76}, {54, 95}, {54, 114}, {54, 133}, {54, 152}, {59, 57}, {65,
38}, {67, 7}, {70, 19}, {72, 7}, {72, 57}, {72, 76}, {72, 95}, {72, 114}, {73,
133}, {73, 152}, {75, 38}, {77, 19}, {89, 38}, {89, 76}, {89, 95}, {89, 114},
{89, 133}, {90, 57}, {91, 19}, {100, 152}, {101, 13}, {107, 19}, {108, 38},
{108, 57}, {108, 95}, {110, 76}, {112, 133}, {113, 12}, {117, 114}, {119,
153}, {125, 38}, {125, 57}, {125, 95}, {125, 114}, {126, 135}, {127, 76},
{134, 152}, {135, 19}, {139, 14}, {143, 19}, {143, 38}, {143, 57}, {143, 76},
{143, 95}, {143, 114}, {143, 133}, {149, 152}, {156, 8}, {160, 19}, {160, 38},
{160, 57}, {160, 95}, {161, 76}, {161, 114}, {161, 133}, {170, 6}, {173, 152},
{178, 19}, {178, 57}, {178, 76}, {178, 95}, {178, 152}, {179, 114}, {180, 38},
{180, 133}, {184, 6}, {196, 6}, {196, 38}, {196, 76}, {196, 114}, {196, 133},
{197, 57}, {197, 95}, {197, 152}, {198, 19}, {214, 38}, {214, 57}, {214, 76},
{214, 95}, {214, 114}, {214, 133}, {218, 19}, {218, 152}, {229, 10}, {232,
19}, {232, 38}, {232, 57}, {232, 95}, {232, 133}, {233, 114}, {235, 17}, {238,
76}, {241, 152}, {249, 38}, {249, 57}, {249, 76}, {249, 95}, {249, 114}, {249,
133}, {249, 152}, {260, 19}, {266, 18}, {267, 57}, {267, 76}, {267, 95}, {267,
114}, {267, 158}, {269, 39}, {282, 17}, {283, 20}, {283, 135}, {285, 57},
{285, 76}, {285, 95}, {285, 114}, {288, 20}, {290, 141}, {291, 7}, {293, 44},
{298, 154}, {303, 49}, {303, 76}, {303, 95}, {303, 114}, {303, 154}, {304, 62}
}

SIN = {}            -- sine LUT (built once); the hot loop indexes it, never calls math.sin
lay = nil           -- the static scene, inflated + painted once, copied per frame (#54)
petals = {}         -- each: {x, y, fall_speed, sway_phase, sway_amp, shade(0 near..2 far)}
base = 0            -- the run's blossom sheet column (base tile); each petal draws base + shade
t = 0.0

-- Falling-petal palette by depth: (near, mid, far). See main.py -- these colours
-- are BAKED INTO sprites.moygfx; change one and you must regenerate the sheet.
BLOSSOMS = {
    pink  = {14, 14, 2},
    white = {7, 6, 13},
    peach = {15, 9, 4},
    mixed = {14, 15, 7},
}
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
