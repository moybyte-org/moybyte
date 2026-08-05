-- Bench Lua -- the on-glass performance meter's LUA TWIN (#163 door 2).
--
-- Line-faithful port of bench.moy/main.py: same phases, same LCG workload,
-- same constants -- so a glass A/B of the two carts reads the Python-vs-Lua
-- VERB PATH cost directly (the sprites' C lane is shared; everything else
-- rides each runtime's own dispatch). The report stays ON SCREEN only: the
-- Lua sandbox has no serial print -- read the glass.

local PHASE_MICRO = 0
local PHASE_GAME = 1        -- the scene, silent
local PHASE_GAME_SND = 2    -- the SAME scene + a beep every ~0.4s
local PHASE_DONE = 3

local GAME_FRAMES = 400     -- ~10s at 40fps (the "GAME FRAMES" card overrides)
local REPS = 8              -- best-of per verb
local TARGET_MS = 25        -- grow a batch until it costs at least this

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
  -- Python twin. NO sprb: the Lua bridge marshals only scalars (lua_to_mp
  -- errors on a table), and the per-call spr below IS the batched lane --
  -- moy_lua's spr appends quads in C (the spr_gate protocol).
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
  st.dts = {}
  st.dts_snd = {}
  st.frame = 0
  st.stats = nil
  st.stats_snd = nil
  st.warm = 5
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
      st.phase = PHASE_GAME
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

function _update(dt)
  local ph = st.phase
  if ph == PHASE_GAME or ph == PHASE_GAME_SND then
    local now = time()
    local prev = st.t_prev
    st.t_prev = now
    if st.warm > 0 then
      st.warm = st.warm - 1
    elseif prev ~= nil then
      local dst = st.dts
      if ph == PHASE_GAME_SND then dst = st.dts_snd end
      dst[#dst + 1] = 1.0 * (now - prev)
    end
    if ph == PHASE_GAME_SND and st.frame % 15 == 0 then
      beep(220 + (st.frame // 15 % 8) * 55, 0.3)
    end
    st.frame = st.frame + 1
    if st.frame >= cfg("frames", GAME_FRAMES) then
      if ph == PHASE_GAME then
        st.stats = stats_of(st.dts)
        st.phase = PHASE_GAME_SND
        st.frame = 0
        st.warm = 5
      else
        st.stats_snd = stats_of(st.dts_snd)
        st.phase = PHASE_DONE
      end
    end
  end
end

local function f1(v)
  return string.format("%.1f", v)
end

local function report()
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
  local rows = { { "SILENT", st.stats }, { "SOUND ", st.stats_snd } }
  for i = 1, 2 do
    local s = rows[i][2]
    if s ~= nil then
      print(rows[i][1] .. " n=" .. s.n .. " fps=" .. f1(s.fps), 8, y, 11)
      y = y + 12
      print("  p50=" .. f1(s.p50) .. " p90=" .. f1(s.p90)
            .. " p99=" .. f1(s.p99) .. " worst=" .. f1(s.worst), 8, y, 7)
      y = y + 12
    end
  end
  y = y + 4
  print("HOLD BACK TO EXIT", 8, y, 6)
end

function _draw()
  local ph = st.phase
  if ph == PHASE_MICRO then
    measure_one()
    local name = ""
    if st.vi <= #VERBS then name = VERBS[st.vi].name end
    rect(0, 226, 320, 14, 0)
    print("BENCH LUA MICRO " .. name .. " k=" .. st.k, 8, 229, 7)
  elseif ph == PHASE_GAME or ph == PHASE_GAME_SND then
    game_scene(st.frame)
    if ph == PHASE_GAME_SND then
      print("+ SOUND", 250, 226, 10)
    end
  else
    report()
  end
end
