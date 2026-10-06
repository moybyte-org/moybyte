-- Bullet Storm -- dodge the storm core's bullet patterns as long as you can.
-- A Lua cart (#67): hundreds of live bullets is exactly the workload the Lua
-- runtime exists for -- every bullet is one spr() through the native batch,
-- and the whole swarm updates in plain Lua with no GC hiccups.
--
-- It models the fast-cart habits (docs/moy_cart_api.md "Make it fast"):
--   * the scrolling starfield is a LAYER built once and window-copied per frame
--   * bullets/sparks live in POOLS (tables reused via swap-remove, so the
--     steady-state play loop allocates nothing)
--   * every bullet is spr(tile, x, y, 0) -- one contiguous batch run per frame
--
-- Make it mine (config cards): STORM intensity, ship SPEED, LIVES, PATTERN.

-- sheet tiles
T_SHIP, T_FLAME_A, T_FLAME_B = 1, 2, 3
T_ORB, T_ORB2, T_STAR, T_DART = 4, 5, 6, 7
T_SPARKLE, T_SPARK = 8, 9
T_CORE_A, T_CORE_B = 10, 11
T_BG1, T_BG2, T_HEART = 12, 13, 14

MAXB = 500                 -- bullet pool cap (the device batch queue is 512)
MAXS = 60                  -- spark/sparkle pool cap

-- tuning (read from the config cards in _init)
storm, ship_speed, max_lives, pattern = 5, 160, 3, "cycle"

-- state
px, py = 160, 200          -- the ship
lives, iframe = 3, 0       -- iframe = invincible seconds after a hit
score_f, graze, best = 0, 0, 0
t, wave, toast, toast_t = 0, 1, "", 0
over = false
bullets, nb = {}, 0        -- pooled: {x, y, vx, vy, tile, grazed}
sparks, ns = {}, 0         -- pooled: {x, y, vx, vy, life, tile}
lay = nil                  -- the starfield layer (built once)
ex, ey = 160, 55           -- the storm core
spiral_a, spiral_acc, ring_cd, fan_cd = 0, 0, 0, 0
flash = 0

local function trunc(x)
  if x >= 0 then return math.floor(x) end
  return math.ceil(x)
end

-- ---------------------------------------------------------------- the pools

local function spawn(x, y, vx, vy, tile)
  if nb >= MAXB then return end
  nb = nb + 1
  local b = bullets[nb]
  if b == nil then b = {} bullets[nb] = b end      -- allocated once, reused forever
  b.x, b.y, b.vx, b.vy, b.tile, b.grazed = x, y, vx, vy, tile, false
end

local function kill_bullet(i)
  bullets[i], bullets[nb] = bullets[nb], bullets[i]  -- swap-remove keeps the table
  nb = nb - 1
end

local function spark(x, y, tile)
  if ns >= MAXS then return end
  ns = ns + 1
  local s = sparks[ns]
  if s == nil then s = {} sparks[ns] = s end
  s.x, s.y, s.life, s.tile = x, y, 0.35, tile
  local a = rnd(6.28)
  s.vx, s.vy = math.cos(a) * 40, math.sin(a) * 40
end

-- ------------------------------------------------------------- the patterns

local function eff()                     -- intensity grows with the wave
  local e = storm + (wave - 1) * 0.6
  local warm = 0.35 + t * 0.11           -- the first ~6s ramp up gently
  if warm < 1 then e = e * warm end
  if e > 14 then e = 14 end
  return e
end

local function pat_spiral(dt)
  local e = eff()
  spiral_acc = spiral_acc + dt * (6 + e * 2.2)
  local arms = 1 + math.floor(e / 4)
  while spiral_acc >= 1 do
    spiral_acc = spiral_acc - 1
    for arm = 0, arms - 1 do
      local a = spiral_a + arm * (6.28 / arms)
      spawn(ex, ey, math.cos(a) * (45 + e * 3), math.sin(a) * (45 + e * 3), T_ORB)
    end
    spiral_a = spiral_a + 2.39996              -- the golden angle never repeats
  end
end

local function pat_rings(dt)
  local e = eff()
  ring_cd = ring_cd - dt
  if ring_cd <= 0 then
    ring_cd = 2.0 - e * 0.13
    if ring_cd < 0.7 then ring_cd = 0.7 end
    local k = 8 + math.floor(e) * 2
    local base = rnd(6.28)
    for i = 0, k - 1 do
      local a = base + i * (6.28 / k)
      spawn(ex, ey, math.cos(a) * 55, math.sin(a) * 55, T_ORB2)
    end
  end
end

local function pat_fans(dt)
  local e = eff()
  fan_cd = fan_cd - dt
  if fan_cd <= 0 then
    fan_cd = 1.1 - e * 0.05
    if fan_cd < 0.45 then fan_cd = 0.45 end
    local aim = math.atan(py - ey, px - ex)    -- straight at the ship
    local shots = 3 + math.floor(e / 3)
    for i = 0, shots - 1 do
      local a = aim + (i - (shots - 1) / 2) * 0.22
      spawn(ex, ey, math.cos(a) * 95, math.sin(a) * 95, T_DART)
    end
    spawn(ex, ey, math.cos(aim) * 60, math.sin(aim) * 60, T_STAR)
  end
end

-- ---------------------------------------------------------------- lifecycle

local function reset()
  nb, ns = 0, 0
  lives = max_lives
  score_f, graze = 0, 0
  t, wave = 0, 1
  spiral_a, spiral_acc, ring_cd, fan_cd = 0, 0, 1.5, 2.0
  iframe, flash = 1, 0
  px, py = W // 2, H - 40
  over = false
  toast, toast_t = "DODGE THE STORM!", 2.5
end

function _init()
  storm = cfg("storm", 5)
  ship_speed = cfg("ship_speed", 160)
  max_lives = cfg("lives", 3)
  pattern = cfg("pattern", "cycle")
  best = pmem(0)
  -- The starfield: a double-height layer whose star pattern repeats every H
  -- pixels, so the camera can loop over it seamlessly (built ONCE, stamped
  -- per frame -- the #54 scroll-layer habit).
  lay = make_layer(W, H * 2)
  lay:cls(0)
  for i = 1, 70 do
    local sx, sy = trunc(rnd(W - 8)), trunc(rnd(H))
    local tile = T_BG1
    if i % 7 == 0 then tile = T_BG2 end
    lay:spr(tile, sx, sy, 0)
    lay:spr(tile, sx, sy + H, 0)                 -- the wrap twin
  end
  reset()
end

local function move_ship(dt)
  local sp = ship_speed
  if btn("left") then px = px - sp * dt end
  if btn("right") then px = px + sp * dt end
  if btn("up") then py = py - sp * dt end
  if btn("down") then py = py + sp * dt end
  local tx, ty, tapped, held = touch()
  if held then                                   -- the ship chases the finger
    local gx, gy = tx, ty - 18                   -- sit above it, not under it
    local step = sp * 1.6 * dt
    if px < gx - step then px = px + step elseif px > gx + step then px = px - step else px = gx end
    if py < gy - step then py = py + step elseif py > gy + step then py = py - step else py = gy end
  end
  if px < 6 then px = 6 elseif px > W - 6 then px = W - 6 end
  if py < 24 then py = 24 elseif py > H - 6 then py = H - 6 end
end

local function hit_ship()
  lives = lives - 1
  iframe = 2
  flash = 0.3
  beep(180, 0.25)
  spark(px, py, T_SPARK) spark(px, py, T_SPARK) spark(px, py, T_SPARK)
  -- the shockwave: clear the swarm around the ship so respawning is fair
  local i = 1
  while i <= nb do
    local b = bullets[i]
    local dx, dy = b.x - px, b.y - py
    if dx * dx + dy * dy < 4900 then kill_bullet(i) else i = i + 1 end
  end
  if lives <= 0 then
    over = true
    local sc = math.floor(score_f) + graze * 5
    if sc > best then best = sc pmem(0, best) end
    beep(120, 0.5)
  end
end

function _update(dt)
  t = t + dt
  if flash > 0 then flash = flash - dt end
  if toast_t > 0 then toast_t = toast_t - dt end
  -- sparks always animate (they decorate the game-over screen too)
  local i = 1
  while i <= ns do
    local s = sparks[i]
    s.x, s.y = s.x + s.vx * dt, s.y + s.vy * dt
    s.life = s.life - dt
    if s.life <= 0 then
      sparks[i], sparks[ns] = sparks[ns], sparks[i]
      ns = ns - 1
    else
      i = i + 1
    end
  end
  if over then
    -- bullets drift on behind the panel; a tap (or A) restarts
    i = 1
    while i <= nb do
      local b = bullets[i]
      b.x, b.y = b.x + b.vx * dt, b.y + b.vy * dt
      if b.x < -8 or b.x > W + 8 or b.y < -8 or b.y > H + 8 then
        kill_bullet(i)
      else
        i = i + 1
      end
    end
    local _, _, tapped = touch()
    if tapped or btnp("a") then reset() end
    return
  end
  score_f = score_f + dt * 10
  if iframe > 0 then iframe = iframe - dt end
  local w = 1 + math.floor(t / 18)               -- a new wave every 18s
  if w ~= wave then
    wave = w
    toast, toast_t = "WAVE " .. wave, 1.5
    beep(660, 0.12)
  end
  move_ship(dt)
  -- the storm core wanders
  ex = W / 2 + math.sin(t * 0.5) * 90
  ey = 55 + math.sin(t * 0.83) * 22
  -- emit this wave's pattern
  local p = pattern
  if p == "cycle" then
    local pick = wave % 3
    if pick == 1 then p = "spiral" elseif pick == 2 then p = "rings" else p = "fans" end
  end
  if p == "spiral" then pat_spiral(dt)
  elseif p == "rings" then pat_rings(dt)
  else pat_fans(dt) end
  -- the swarm: move, cull, graze, collide -- one tight loop
  i = 1
  while i <= nb do
    local b = bullets[i]
    b.x, b.y = b.x + b.vx * dt, b.y + b.vy * dt
    if b.x < -8 or b.x > W + 8 or b.y < -8 or b.y > H + 8 then
      kill_bullet(i)
    else
      local dx, dy = b.x - px, b.y - py
      local d2 = dx * dx + dy * dy
      if d2 < 36 and iframe <= 0 then            -- the hitbox is TINY (that's the genre)
        kill_bullet(i)
        hit_ship()
        if over then return end
      elseif d2 < 144 and not b.grazed then      -- a graze: brave, and worth points
        b.grazed = true
        graze = graze + 1
        spark(px + dx / 2, py + dy / 2, T_SPARKLE)
        i = i + 1
      else
        i = i + 1
      end
    end
  end
end

function _draw()
  -- the starfield scrolls by stamping a moving window of the layer (no cls)
  draw_layer(lay, 0, H - (t * 14) % H)
  -- the storm core (scale 2 = its own batch run)
  local core = T_CORE_A
  if math.floor(t * 4) % 2 == 1 then core = T_CORE_B end
  spr(core, ex - 8, ey - 8, 0, 2)
  -- the swarm: same colorkey+scale for every bullet -> ONE native batch run
  for i = 1, nb do
    local b = bullets[i]
    spr(b.tile, b.x - 4, b.y - 4, 0)
  end
  for i = 1, ns do
    local s = sparks[i]
    spr(s.tile, s.x - 4, s.y - 4, 0)
  end
  if not over and (iframe <= 0 or math.floor(t * 10) % 2 == 0) then
    spr(T_SHIP, px - 4, py - 4, 0)
    local fl = T_FLAME_A
    if math.floor(t * 12) % 2 == 1 then fl = T_FLAME_B end
    spr(fl, px - 4, py + 4, 0)
    pix(px, py, 7)                               -- the true hitbox, always visible
  end
  for i = 1, lives do
    spr(T_HEART, W - 10 * i - 2, 4, 0)
  end
  if flash > 0 then
    rectb(0, 0, W, H, 7)
    rectb(1, 1, W - 2, H - 2, 7)
  end
  print("SCORE " .. (math.floor(score_f) + graze * 5), 6, 4, col("white"))
  print("GRAZE " .. graze, 6, 14, col("light_grey"))
  if toast_t > 0 then
    print(toast, W // 2 - #toast * 4, 60, col("yellow"))
  end
  if over then
    local sc = math.floor(score_f) + graze * 5
    rect(62, 86, 196, 74, 0)
    rectb(62, 86, 196, 74, 8)
    print("THE STORM WINS", 104, 96, col("red"))
    print("SCORE " .. sc, 104, 112, col("white"))
    print("BEST  " .. best, 104, 122, col("yellow"))
    print("TAP TO FLY AGAIN", 96, 138, col("green"))
    print("HOLD BACKSPACE TO LEAVE", 68, 148, col("dark_grey"))
  end
end
