-- Ray Lua -- the line-faithful Lua twin of ray_test (#167), for the A/B that
-- decides whether the Lua tier rescues software 3D on the S3.
--
-- The Python original is 99% interpreter: with the native fill_spans kernel in
-- place its pixels cost ~0.03ms and its DDA costs ~45ms. So the ONLY question
-- this cart asks is how fast the same arithmetic runs in the Lua VM.
--
-- This drew per column while the Python twin batched, which made the pair two
-- different programs and its number a ceiling rather than a measurement. Since
-- 2026-08-14 ray_test.moy is this cart line for line: the batch verbs are gone
-- (plan 6.10 -- 160 draw upcalls measured under a millisecond, and Lua could
-- never call them anyway), so the pair now differs only in language.
--
-- The level is map.moymap and the walls are sprites.moygfx -- both editable,
-- both identical to the Python twin's. A cell holds the sheet tile its wall is
-- built from, and the side-on face is the tile one sheet row below it.
--
-- flr() where the Python twin writes int(): those disagree on a negative
-- number, and every coordinate here is inside a walled map, so they cannot.

local CEIL, FLOOR = 1, 5

local TC, TS = 0.99755, 0.06994   -- one turn step; no trig needed anywhere

local px, py, dx, dy, plx, ply
local cols, step, VW, VH, VX, VY, bench
local mode, rc, rs
local frames, t_mark, fps

function _init()
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

  -- bench splits the frame into its parts so each can be timed alone (#167):
  --   0 normal  1 the ray march with nothing drawn (pure VM throughput)
  --   2 the two background rects alone (fill bandwidth)
  --   3 the same pixels, ceiling via cls -- linear whole-buffer fill against
  --     strided row fill. cls ignores the viewport, so compare 2 vs 3 at full
  --     res only.
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
    if mget(flr(px + mx), flr(py)) < 0 then px = px + mx end
    if mget(flr(px), flr(py + my)) < 0 then py = py + my end
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

    local side, cell = 0, -1
    for _ = 1, 64 do
      if sidex < sidey then
        sidex = sidex + ddx; mapx = mapx + sx; side = 0
      else
        sidey = sidey + ddy; mapy = mapy + sy; side = 1
      end
      cell = mget(mapx, mapy)
      if cell >= 0 then break end
    end

    local dist = (side == 1) and (sidey - ddy) or (sidex - ddx)
    if dist < 0.02 then dist = 0.02 end

    local lh = flr(VH / dist)
    local top = half - lh // 2   -- unclipped: the crop below needs the real extent

    if lh > 0 and cell >= 0 and bench == 0 then
      -- Where along the wall face the ray landed picks the texture COLUMN,
      -- and the side picks the row: the dim twin is one sheet row down.
      local hit = (side == 1) and (px + dist * rdx) or (py + dist * rdy)
      local u = (cell % 16) * 8 + flr((hit - flr(hit)) * 8)
      local v = (cell // 16) * 8 + side * 8
      local wide = VW - i * step
      if wide > step then wide = step end
      -- A slice taller than the view is CROPPED, not squashed into what fits:
      -- walking into a wall magnifies its texture, never shrinks it.
      if lh > VH then
        local v0 = (-top * 8) // lh
        local v1 = ((VH - top) * 8 + lh - 1) // lh
        if v1 > 8 then v1 = 8 end
        sspr(u, v + v0, 1, v1 - v0, VX + i * step, VY, wide, VH)
      else
        sspr(u, v, 1, 8, VX + i * step, VY + top, wide, lh)
      end
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
  elseif bench == 1 then
    cast()                       -- geometry only: cast() skips its own sspr()
  elseif bench == 2 then
    local half = VH // 2         -- background only: the two wide rects
    rect(VX, VY, VW, half, CEIL)
    rect(VX, VY + half, VW, VH - half, FLOOR)
  elseif bench == 3 then
    local half = VH // 2         -- background only, but ceiling via linear cls
    cls(CEIL)
    rect(VX, VY + half, VW, VH - half, FLOOR)
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
    print("BENCH " .. bench, VX + 3, VY + 12, 6)
  else
    print(cols .. " RAYS B=TRI", VX + 3, VY + 12, 6)
  end
end
