-- Ray Lua -- the line-faithful Lua twin of ray_test (#167), for the A/B that
-- decides whether the Lua tier rescues software 3D on the S3.
--
-- The Python original is 99% interpreter: with the native fill_spans kernel in
-- place its pixels cost ~0.03ms and its DDA costs ~45ms. So the ONLY question
-- this cart asks is how fast the same arithmetic runs in the Lua VM.
--
-- IMPORTANT (why this draws per column instead of batching): the Lua bridge
-- marshals only nil/bool/number/string -- lua_to_mp errors on a table -- so a
-- Lua cart cannot hand rect_batch a span buffer today. Every wall is therefore
-- its own rect() upcall, which is the dispatch cost the batch exists to delete.
-- Read this cart's number as "Lua DDA + 160 upcalls", i.e. a CEILING on the
-- frame time a batched Lua cart would reach, not the best case.

local MAP = {
  "1111111111111111",
  "1..............1",
  "1..2222....33..1",
  "1..2...........1",
  "1..2........3..1",
  "1..2222.....3..1",
  "1..............1",
  "1....444.......1",
  "1....4.4.......1",
  "1....4.4...22..1",
  "1..............1",
  "1..33..........1",
  "1..33....444...1",
  "1..............1",
  "1..............1",
  "1111111111111111",
}

-- grid[y][x] = 0 for open, else the wall kind 1..4. Built once from the strings
-- so the hot loop indexes numbers instead of doing string work per DDA step.
local grid = {}

local LIT = {8, 12, 11, 9}     -- facing you
local DIM = {2, 1, 3, 4}       -- side-on
local CEIL, FLOOR = 1, 5

local TC, TS = 0.99755, 0.06994   -- one turn step; no trig needed anywhere

local px, py, dx, dy, plx, ply
local cols, step, VW, VH, VX, VY, bench
local mode, rc, rs
local frames, t_mark, fps

function _init()
  for y = 1, #MAP do
    local row, g = MAP[y], {}
    for x = 1, #row do
      local c = row:byte(x)
      g[x] = (c == 46) and 0 or (c - 48)     -- '.' is 46, '1'..'4' are 49..52
    end
    grid[y] = g
  end

  px, py = 8.5, 11.5
  dx, dy = 0.0, -1.0
  plx, ply = 0.66, 0.0

  if cfg("low_res", 0) ~= 0 then
    VW, VH = 160, 120
    -- SPEC.md 6: view is core and always present. A console that cannot
    -- composite a scaled region draws this one unscaled, which the centering
    -- below already handles -- so no guard, and nothing to declare.
    view(VW, VH)
  else
    VW, VH = W, H
  end
  VX = (W - VW) // 2
  VY = (H - VH) // 2

  -- bench: run the ray march and draw NOTHING but the HUD, so `render` is pure
  -- VM throughput. Without it this cart's 160 rect() upcalls and the Python
  -- twin's single batched call make the two frames incomparable (#167).
  bench = cfg("bench", 0)

  step = cfg("ray_step", 2)
  if step < 1 then step = 1 end
  cols = (VW + step - 1) // step

  mode = 0
  rc, rs = 1.0, 0.0
  frames, t_mark, fps = 0, time(), 0
end

function _update(dt)
  if btnp("b") then mode = 1 - mode end

  if mode ~= 0 then
    rc, rs = rc * TC - rs * TS, rc * TS + rs * TC
    return
  end

  if btn("left") then
    local odx, opx = dx, plx
    dx = dx * TC + dy * TS
    dy = -odx * TS + dy * TC
    plx = plx * TC + ply * TS
    ply = -opx * TS + ply * TC
  end
  if btn("right") then
    local odx, opx = dx, plx
    dx = dx * TC - dy * TS
    dy = odx * TS + dy * TC
    plx = plx * TC - ply * TS
    ply = opx * TS + ply * TC
  end

  local sp = 2.4 * dt
  local mx, my = 0.0, 0.0
  if btn("up") then mx, my = dx * sp, dy * sp end
  if btn("down") then mx, my = -dx * sp, -dy * sp end
  if mx ~= 0.0 or my ~= 0.0 then
    if grid[flr(py) + 1][flr(px + mx) + 1] == 0 then px = px + mx end
    if grid[flr(py + my) + 1][flr(px) + 1] == 0 then py = py + my end
  end
end

local function cast()
  local half = VH // 2
  for i = 0, cols - 1 do
    local cam = 2.0 * i / cols - 1.0
    local rdx = dx + plx * cam
    local rdy = dy + ply * cam

    local mapx, mapy = flr(px), flr(py)

    local ddx = (rdx == 0.0) and 1e30 or (rdx < 0 and -1.0 / rdx or 1.0 / rdx)
    local ddy = (rdy == 0.0) and 1e30 or (rdy < 0 and -1.0 / rdy or 1.0 / rdy)

    local sx, sy, sidex, sidey
    if rdx < 0 then sx, sidex = -1, (px - mapx) * ddx
    else sx, sidex = 1, (mapx + 1.0 - px) * ddx end
    if rdy < 0 then sy, sidey = -1, (py - mapy) * ddy
    else sy, sidey = 1, (mapy + 1.0 - py) * ddy end

    local side, cell = 0, 1
    for _ = 1, 64 do
      if sidex < sidey then
        sidex = sidex + ddx; mapx = mapx + sx; side = 0
      else
        sidey = sidey + ddy; mapy = mapy + sy; side = 1
      end
      cell = grid[mapy + 1][mapx + 1]
      if cell ~= 0 then break end
    end

    local dist = (side == 1) and (sidey - ddy) or (sidex - ddx)
    if dist < 0.02 then dist = 0.02 end

    local lh = flr(VH / dist)
    local y0 = half - lh // 2
    if y0 < 0 then y0 = 0 end
    local y1 = half + lh // 2
    if y1 > VH then y1 = VH end

    if y1 > y0 and bench == 0 then
      local wide = VW - i * step
      if wide > step then wide = step end
      rect(VX + i * step, VY + y0, wide, y1 - y0,
           (side == 1) and DIM[cell] or LIT[cell])
    end
  end
end

-- the tri() half: a spinning flat-shaded tetrahedron (numbers only, so it
-- crosses the bridge fine)
local TETRA = {
  {{0.0, -1.0, 0.0}, {-0.94, 0.47, -0.54}, {0.94, 0.47, -0.54}},
  {{0.0, -1.0, 0.0}, {0.94, 0.47, -0.54}, {0.0, 0.47, 1.08}},
  {{0.0, -1.0, 0.0}, {0.0, 0.47, 1.08}, {-0.94, 0.47, -0.54}},
  {{-0.94, 0.47, -0.54}, {0.94, 0.47, -0.54}, {0.0, 0.47, 1.08}},
}
local FACE = {8, 9, 10, 12}

local function draw_tetra()
  local cx, cy = VX + VW // 2, VY + VH // 2
  local k = VW * 0.8
  local order = {}
  for f = 1, 4 do
    local zs, pts = 0.0, {}
    for v = 1, 3 do
      local p = TETRA[f][v]
      local x = p[1] * rc + p[3] * rs
      local z = -p[1] * rs + p[3] * rc + 3.0
      zs = zs + z
      local m = k / z
      pts[v] = {cx + flr(x * m), cy + flr(p[2] * m)}
    end
    order[f] = {zs, f, pts}
  end
  table.sort(order, function(a, b) return a[1] < b[1] end)
  for i = 4, 1, -1 do
    local it = order[i]
    local p = it[3]
    tri(p[1][1], p[1][2], p[2][1], p[2][2], p[3][1], p[3][2], FACE[it[2]])
  end
end

function _draw()
  if mode ~= 0 then
    rect(VX, VY, VW, VH, 0)
    draw_tetra()
  elseif bench ~= 0 then
    cast()                       -- geometry only: cast() skips its own rect()
  else
    local half = VH // 2
    rect(VX, VY, VW, half, CEIL)
    rect(VX, VY + half, VW, VH - half, FLOOR)
    cast()
  end

  frames = frames + 1
  local now = time()
  if now - t_mark >= 500 then
    fps = (frames * 1000) // (now - t_mark)
    frames, t_mark = 0, now
  end

  print("FPS " .. fps, VX + 3, VY + 3, 7)
  if mode ~= 0 then
    print("TRI B=RAYS", VX + 3, VY + 12, 6)
  elseif bench ~= 0 then
    print(cols .. " RAYS BENCH", VX + 3, VY + 12, 6)
  else
    print(cols .. " RAYS B=TRI", VX + 3, VY + 12, 6)
  end
end
