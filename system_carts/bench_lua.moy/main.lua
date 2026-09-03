-- Bench Lua -- the on-glass performance meter's LUA TWIN (#163 door 2).
--
-- Line-faithful port of bench.moy/main.py: same phases, same LCG workload,
-- same constants -- so a glass A/B of the two carts reads the Python-vs-Lua
-- VERB PATH cost directly (the sprites' C lane is shared; everything else
-- rides each runtime's own dispatch). The report draws ON SCREEN and, since
-- 2026-08-17, is also written into PMEM (the layout below, shared with the
-- Python twin and tools/p4_cart_bench.py) -- the Lua sandbox has no serial
-- print, and pmem is the spec-pure side-channel a harness can read live
-- through moycore.pmem_image. Cells are the bench's own save file; the
-- numbers persisting is harmless and even handy.
--
-- PMEM REPORT LAYOUT v1 (int32 cells; keep the three copies in lock-step --
-- this cart, bench.moy/main.py, tools/p4_cart_bench.py):
--   0 magic 45948   1 version   2 n_verbs   3 done flag (written LAST)
--   8 + i*3:  verb_id, k, best_ms          (verb ids in VERB_ID below)
--   64 + i*8: phase_id, n, p50*10, p90*10, p99*10, worst*10, fps*10
--             (phases in order: idle=0 logic=1 draw=2 silent=3 sound=4)

local PHASE_MICRO = 0
local PHASE_IDLE = 1        -- the floor: a frame where the cart does nothing
local PHASE_LOGIC = 2       -- IDLE + arithmetic  -> LOGIC - IDLE = the language
local PHASE_DRAW = 3        -- IDLE + draw calls  -> DRAW  - IDLE = the draw path
local PHASE_GAME = 4        -- the scene, silent
local PHASE_GAME_SND = 5    -- the SAME scene + a beep every ~0.4s
local PHASE_DONE = 6

local GAME_FRAMES = 400     -- ~10s at 40fps (the "GAME FRAMES" card overrides)
local SCENE_FRAMES = 200    -- the three isolation phases (~5s each)
local REPS = 8              -- best-of per verb
local TARGET_MS = 25        -- grow a batch until it costs at least this

-- The isolation phases exist because a whole-frame number cannot say WHERE the
-- time went, and the Python-vs-Lua comparison kept stalling on exactly that:
-- per-verb costs said Lua should win the game scene and the measured frame said
-- it lost. So measure the floor, then add one ingredient at a time.
local LOGIC_ITERS = 3000    -- per frame, in the LOGIC phase
local DRAW_OPS = 300        -- per frame, in the DRAW phase

local st = {}

-- (name, per-op fn, starting batch size). Positions from the same tiny LCG
-- as the Python twin, so the workload is identical every run on every build.
local VERBS = {
  { name = "cls", k0 = 4, fn = function(i)
      cls(1 + (i & 7))
    end },
  { name = "rect", k0 = 100, fn = function(i)
      rect((i * 37) % 290, (i * 53) % 225, 24, 12, 2 + (i & 15))
    end },
  { name = "circ", k0 = 100, fn = function(i)
      circ(10 + (i * 41) % 300, 10 + (i * 29) % 220, 7, 2 + (i & 15))
    end },
  { name = "line", k0 = 100, fn = function(i)
      line((i * 17) % 320, (i * 31) % 240, (i * 59) % 320, (i * 43) % 240,
           2 + (i & 15))
    end },
  { name = "pix", k0 = 500, fn = function(i)
      pix((i * 13) % 320, (i * 7) % 240, 2 + (i & 15))
    end },
  { name = "print", k0 = 50, fn = function(i)
      print("BENCH", (i * 47) % 260, (i * 23) % 230, 7)
    end },
  -- 2026-08-04 (#163): the rest of the verb set, same workloads as the
  -- Python twin -- and as of 2026-08-14 that is literally true, with no
  -- exceptions to remember. The twin used to carry an extra "sprb" scene this
  -- cart could not have (a trampoline cannot marshal a list), so the pair
  -- disagreed by one row; spr_batch is deleted (plan 6.10) and the scene with
  -- it. The per-call spr below is the batched lane either way.
  { name = "rectb", k0 = 100, fn = function(i)
      rectb((i * 37) % 280, (i * 53) % 215, 30, 18, 2 + (i & 15))
    end },
  { name = "circb", k0 = 100, fn = function(i)
      circb(10 + (i * 41) % 300, 10 + (i * 29) % 220, 8, 2 + (i & 15))
    end },
  { name = "tri", k0 = 50, fn = function(i)
      tri((i * 17) % 300, (i * 31) % 230, (i * 59) % 300 + 10,
          (i * 43) % 230, (i * 23) % 300, ((i * 13) % 230) + 8, 2 + (i & 15))
    end },
  { name = "spr", k0 = 500, fn = function(i)
      spr(i & 7, (i * 37) % 310, (i * 53) % 230)
    end },
  { name = "map", k0 = 8, fn = function(i)
      map(0, 0, 15, 8, (i * 7) % 40, (i * 11) % 40)
    end },
  { name = "sspr", k0 = 50, fn = function(i)
      sspr((i & 7) * 8, 0, 8, 8, (i * 37) % 300, (i * 53) % 220, 20, 20)
    end },
  { name = "tline", k0 = 50, fn = function(i)
      -- SPEC.md 6.1 tline: one full-width textured scanline per call, 16.16
      -- fixed point, sampling the _init-mset field -- the Mode 7 shape.
      tline(0, (i * 13) % 240, 319, (i * 13) % 240,
            (i * 7) << 14, (i * 11) << 13, 16384 + ((i & 15) << 7), i << 6)
    end },
  -- APPENDED with the Python twin when moy core 0.3 promoted SPEC.md 6.1.
  { name = "trib", k0 = 50, fn = function(i)
      trib((i * 17) % 300, (i * 31) % 230, (i * 59) % 300 + 10,
           (i * 43) % 230, (i * 23) % 300, ((i * 13) % 230) + 8, 2 + (i & 15))
    end },
  { name = "oval", k0 = 100, fn = function(i)
      oval((i * 17) % 280, (i * 31) % 200, 8 + (i & 31), 8 + ((i >> 2) & 31),
           2 + (i & 15))
    end },
  { name = "ovalb", k0 = 100, fn = function(i)
      ovalb((i * 17) % 280, (i * 31) % 200, 8 + (i & 31), 8 + ((i >> 2) & 31),
            2 + (i & 15))
    end },
  -- Same call as oval, under a pattern: the difference IS fillp's cost.
  { name = "oval_p", k0 = 100, fn = function(i)
      fillp(0xA5A5)
      oval((i * 17) % 280, (i * 31) % 200, 8 + (i & 31), 8 + ((i >> 2) & 31),
           2 + (i & 15))
      fillp()
    end },
}

function _init()
  -- the map verb's field: same deterministic 15x8 region as the Python twin
  for y = 0, 7 do
    for x = 0, 14 do
      mset(x, y, (x + y) & 7)
    end
  end
  st.phase = PHASE_MICRO
  st.vi = 1               -- which verb (1-based, Lua)
  st.rep = 0
  st.k = VERBS[1].k0
  st.best = nil
  st.micro = {}           -- {name=, k=, best=}
  st.dts = {}             -- the CURRENT phase's frame times, ms
  st.frame = 0
  st.stats = {}           -- label -> stats, one entry per timed phase
  st.scenes = nil         -- built at the first tick: `scenes` and the scene
                          -- functions are locals declared BELOW _init, so a
                          -- call here compiles as a nil global (Lua resolves
                          -- names at closure-creation, not at call time)
  st.sink = 0
  st.warm = 5
  pmem(3, 0)              -- arm the pmem report: a PREVIOUS run's done flag
                          -- persists (pmem is the save file), and a harness
                          -- polling cell 3 must not read it
end

local function measure_one()
  local v = VERBS[st.vi]
  local k = st.k
  local t0 = time()
  local fn = v.fn
  for i = 0, k - 1 do
    fn(i)
  end
  local ms = time() - t0
  if ms < TARGET_MS and k < 50000 then
    st.k = k * 2
    return
  end
  if st.best == nil or ms < st.best then
    st.best = ms
  end
  st.rep = st.rep + 1
  if st.rep >= REPS then
    st.micro[#st.micro + 1] = { name = v.name, k = k, best = st.best }
    st.vi = st.vi + 1
    st.rep = 0
    st.best = nil
    if st.vi > #VERBS then
      st.phase = PHASE_IDLE
      st.frame = 0
    else
      st.k = VERBS[st.vi].k0
    end
  end
end

local function game_scene(f)
  cls(1)
  for row = 0, 3 do
    for col = 0, 9 do
      rect(8 + col * 30, 28 + row * 14, 26, 10, 2 + ((row + col) & 7))
    end
  end
  for b = 0, 5 do
    local x = (f * (3 + b) + b * 53) % 300
    local y = 100 + ((f * (2 + b) + b * 31) % 120)
    circ(10 + x, y, 6, 10 + b)
  end
  line(0, 96, 319, 96, 7)
  rect((f * 4) % 250, 226, 40, 8, 12)
  print("SCORE 1234", 8, 6, 7)
  print("LIVES 3", 120, 6, 7)
  print("BENCH LUA GAME", 190, 6, 6)
end

local function idle_scene(f)
  -- THE FLOOR. One clear, one label -- whatever this frame costs is the
  -- console's own overhead, and the other phases are read as deltas from it.
  cls(1)
  print("IDLE", 8, 6, 7)
end

local function logic_scene(f)
  -- Arithmetic only, drawn exactly like IDLE, so LOGIC - IDLE is what the
  -- LANGUAGE costs and nothing else. Small-magnitude integer math on purpose:
  -- the Python twin would allocate a bignum past 31 bits and measure its
  -- allocator instead. The float chain rides along because carts do float
  -- physics and the two VMs differ there (LUA_32BITS vs packed floats).
  cls(1)
  local x = 1 + (f & 15)
  local s = 0
  local fx = 0.5
  for i = 0, LOGIC_ITERS - 1 do
    x = (x * 37 + 11) % 1021
    s = s + (x & 31) - 15
    fx = fx + 0.25
    if fx > 100.0 then fx = fx - 100.0 end
  end
  st.sink = s + flr(fx)              -- keep the loop from being dead code
  print("LOGIC", 8, 6, 7)
end

local function draw_scene(f)
  -- Draw calls only, trivial arithmetic, over the same IDLE floor, so
  -- DRAW - IDLE is what the DRAW PATH costs at a per-frame call count a real
  -- cart reaches.
  cls(1)
  for i = 0, DRAW_OPS - 1 do
    rect((i * 37) % 290, (i * 53) % 225, 8, 6, 2 + (i & 15))
  end
  print("DRAW", 8, 6, 7)
end

local function pct(s, p)
  local i = (p * #s) // 100 + 1
  if i > #s then i = #s end
  return s[i]
end

local function stats_of(raw)
  local dts = {}
  for i = 1, #raw do dts[i] = raw[i] end
  table.sort(dts)
  local n = #dts
  if n == 0 then
    dts = { 0.0 }
    n = 1
  end
  local s = {
    n = n,
    p50 = pct(dts, 50),
    p90 = pct(dts, 90),
    p99 = pct(dts, 99),
    worst = dts[n],
    best = dts[1],
  }
  if s.p50 > 0 then s.fps = 1000.0 / s.p50 else s.fps = 0.0 end
  return s
end

-- phase -> { label, scene fn, frames }. One table instead of a chain of
-- branches, matching the Python twin's _scenes() line for line.
local function scenes()
  local n = cfg("frames", GAME_FRAMES)
  return {
    [PHASE_IDLE] = { "idle", idle_scene, SCENE_FRAMES },
    [PHASE_LOGIC] = { "logic", logic_scene, SCENE_FRAMES },
    [PHASE_DRAW] = { "draw", draw_scene, SCENE_FRAMES },
    [PHASE_GAME] = { "silent", game_scene, n },
    [PHASE_GAME_SND] = { "sound", game_scene, n },
  }
end

function _update(dt)
  if st.scenes == nil then st.scenes = scenes() end
  local ph = st.phase
  local sc = st.scenes[ph]
  if sc ~= nil then
    local now = time()
    local prev = st.t_prev
    st.t_prev = now
    if st.warm > 0 then
      st.warm = st.warm - 1
    elseif prev ~= nil then
      st.dts[#st.dts + 1] = 1.0 * (now - prev)
    end
    if ph == PHASE_GAME_SND and st.frame % 15 == 0 then
      beep(220 + (st.frame // 15 % 8) * 55, 0.3)
    end
    st.frame = st.frame + 1
    if st.frame >= sc[3] then
      st.stats[sc[1]] = stats_of(st.dts)
      st.dts = {}
      st.phase = ph + 1
      st.frame = 0
      st.warm = 5
      st.t_prev = nil
    end
  end
end

local function f1(v)
  return string.format("%.1f", v)
end

-- The pmem report (layout in the header). Written ONCE, done flag last, so a
-- reader polling cell 3 never sees a half-written block.
local VERB_ID = { cls = 0, rect = 1, circ = 2, line = 3, pix = 4, print = 5,
                  rectb = 6, circb = 7, tri = 8, spr = 9, map = 10,
                  sspr = 11, tline = 12, trib = 13, oval = 14, ovalb = 15,
                  oval_p = 16 }
local PHASE_ORDER = { { "idle", 0 }, { "logic", 1 }, { "draw", 2 },
                      { "silent", 3 }, { "sound", 4 } }

local function pmem_report()
  pmem(0, 45948)
  pmem(1, 1)
  pmem(2, #st.micro)
  for i = 1, #st.micro do
    local m = st.micro[i]
    local base = 8 + (i - 1) * 3
    pmem(base, VERB_ID[m.name] or -1)
    pmem(base + 1, m.k)
    pmem(base + 2, flr(m.best))
  end
  for i = 1, #PHASE_ORDER do
    local s = st.stats[PHASE_ORDER[i][1]]
    if s ~= nil then
      local base = 64 + (i - 1) * 8
      pmem(base, PHASE_ORDER[i][2])
      pmem(base + 1, s.n)
      pmem(base + 2, flr(s.p50 * 10))
      pmem(base + 3, flr(s.p90 * 10))
      pmem(base + 4, flr(s.p99 * 10))
      pmem(base + 5, flr(s.worst * 10))
      pmem(base + 6, flr(s.fps * 10))
    end
  end
  pmem(3, 1)
end

local function report()
  if not st.pmem_done then
    st.pmem_done = true
    pmem_report()
  end
  cls(0)
  print("MOYBYTE BENCH LUA", 8, 8, 11)
  local y = 26
  for i = 1, #st.micro do
    local m = st.micro[i]
    local us = (m.best * 1000.0) / m.k
    print(m.name .. " x" .. m.k .. " = " .. m.best .. "ms  ("
          .. f1(us) .. "us/op)", 8, y, 7)
    y = y + 10                       -- 12 verbs: tight rows
  end
  y = y + 4
  -- The isolation phases as ONE line: the floor absolute, the other two as
  -- deltas from it, because the delta is the whole point and 320px is 40
  -- characters.
  local fl, lo, dr = st.stats["idle"], st.stats["logic"], st.stats["draw"]
  if fl ~= nil then
    local line1 = "FLOOR " .. f1(fl.p50)
    if lo ~= nil then line1 = line1 .. "  LOGIC +" .. f1(lo.p50 - fl.p50) end
    if dr ~= nil then line1 = line1 .. "  DRAW +" .. f1(dr.p50 - fl.p50) end
    print(line1, 8, y, 14)
    y = y + 11
  end
  local rows = { { "SILENT", "silent" }, { "SOUND", "sound" } }
  for i = 1, 2 do
    local s = st.stats[rows[i][2]]
    if s ~= nil then
      print(rows[i][1] .. " n=" .. s.n .. " fps=" .. f1(s.fps)
            .. " p50=" .. f1(s.p50) .. " w=" .. f1(s.worst), 8, y, 11)
      y = y + 11
    end
  end
  y = y + 4
  print("HOLD BACK TO EXIT", 8, y, 6)
end

function _draw()
  if st.scenes == nil then st.scenes = scenes() end
  local ph = st.phase
  if ph == PHASE_MICRO then
    measure_one()
    local name = ""
    if st.vi <= #VERBS then name = VERBS[st.vi].name end
    rect(0, 226, 320, 14, 0)
    print("BENCH LUA MICRO " .. name .. " k=" .. st.k, 8, 229, 7)
  else
    local sc = st.scenes[ph]
    if sc == nil then
      report()
    else
      sc[2](st.frame)
      if ph == PHASE_GAME_SND then
        print("+ SOUND", 250, 226, 10)
      end
    end
  end
end
