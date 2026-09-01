# Ray Test -- a textured raycaster that measures the #167 3D verbs.
#
# The point of this cart is a RATIO: the ray march runs here in Python, one
# iteration per screen column, and each wall is one sspr(). It used to pack the
# whole frame's walls into a span buffer and issue ONE rect_batch, on the belief
# that the MP->C crossing was what made software 3D affordable. Measured on glass
# 2026-08-14 (plan 6.10): it is not. A draw call is ~3us of dispatch, so 160 of
# them are ~0.5ms of a 20ms frame, and the batch verbs were deleted -- they cost
# every kid a second vocabulary (Lua could not call them at all) to buy that.
# ray_lua.moy is now this cart line for line, which is what makes the pair a
# language measurement instead of two different programs.
#
# The level is map.moymap and the walls are sprites.moygfx, so both are editable:
# a cell holds the sheet tile its wall is built from, and mget() is the only
# thing the ray march asks about the world. A wall's SIDE-ON face is the tile one
# sheet row below its front face (tile 1 lit -> tile 17 dim), which is the
# two-tone shading that reads as 3D.
#
# The ceiling and floor are TWO BIG RECTS, not per-column spans. Painting each
# pixel exactly once (a ceiling/wall/floor span per column) sounds better and
# measured 2x WORSE on glass: 320 one-column-wide vertical strips walk a 640-byte
# stride, so every pixel lands in a different cache line, while two wide rects are
# sequential writes. #163's finding in cart form -- "the win is fewer AND WIDER
# spans, contiguity as much as call count". Overdraw is cheaper than a cache miss.
#
# "low_res" declares a smaller LOGICAL viewport with view(): half the rays AND a
# quarter of the pixels, the only lever that moves both halves of the frame at
# once. It is why PICO-8 sits at 128x128.
#
# Press B to switch to the spinning tetrahedron, which exercises tri() instead.
#
# No trig is needed anywhere: turning rotates the direction/plane vectors by a
# FIXED angle, so the only constants required are one cos/sin pair.

CEIL = 1                     # dark_blue
FLOOR = 5                    # dark_grey

# One turn step, precomputed: cos/sin of ~0.07 rad. Rotating the basis vectors
# by a constant angle is why this cart needs no math library.
TC = 0.99755
TS = 0.06994


def _init():
    global px, py, dx, dy, plx, ply
    global cols, step, mode, frames, t_mark, fps, rc, rs
    global VW, VH, VX, VY, bench

    px, py = 8.5, 11.5           # start in the open corridor, facing north
    dx, dy = 0.0, -1.0
    plx, ply = 0.66, 0.0         # camera plane = 66 degree FOV

    # The LOGICAL viewport. low_res halves it in both axes and hands the console
    # a view() to scale back up -- 1/2 the rays, 1/4 the pixels.
    if cfg("low_res", 0):
        VW, VH = 160, 120
        view(VW, VH)
    else:
        VW, VH = W, H
    VX = (W - VW) >> 1           # view() shows the CENTERED region, so draw there
    VY = (H - VH) >> 1

    # bench splits the frame into its parts so each can be timed alone (#167):
    #   0  normal
    #   1  geometry ONLY  -- the ray march, nothing drawn (VM throughput)
    #   2  background ONLY -- the two wide rects, no ray march (fill bandwidth)
    #   3  background via cls + one rect -- A/B of the linear whole-buffer fill
    #      (gfx.fill) against the strided row fill (gfx.fill_rect) over the same
    #      pixel count. NOTE cls ignores the viewport and clears the whole canvas,
    #      so only compare 2 vs 3 at full res.
    bench = cfg("bench", 0)

    step = cfg("ray_step", 2)    # screen pixels per ray -- the perf knob
    if step < 1:
        step = 1
    cols = (VW + step - 1) // step

    mode = 0
    rc = 1.0                     # the turntable's cos/sin, advanced one fixed
    rs = 0.0                     # step per frame -- O(1), never recomputed
    frames = 0
    t_mark = time()
    fps = 0


def _update(dt):
    global px, py, dx, dy, plx, ply, mode, rc, rs

    if btnp("b"):
        mode = 1 - mode

    if mode:
        rc, rs = rc * TC - rs * TS, rc * TS + rs * TC
        return

    if btn("left"):              # rotate the basis by +TS
        odx = dx
        dx = dx * TC + dy * TS
        dy = -odx * TS + dy * TC
        opx = plx
        plx = plx * TC + ply * TS
        ply = -opx * TS + ply * TC
    if btn("right"):             # rotate by -TS
        odx = dx
        dx = dx * TC - dy * TS
        dy = odx * TS + dy * TC
        opx = plx
        plx = plx * TC - ply * TS
        ply = opx * TS + ply * TC

    sp = 2.4 * dt
    mx = 0.0
    my = 0.0
    if btn("up"):
        mx = dx * sp
        my = dy * sp
    if btn("down"):
        mx = -dx * sp
        my = -dy * sp
    if mx or my:                 # slide along walls instead of sticking to them
        if mget(int(px + mx), int(py)) < 0:
            px = px + mx
        if mget(int(px), int(py + my)) < 0:
            py = py + my


def _cast():
    """March one ray per column and draw its wall slice.

    Textbook DDA: step whole map cells along the ray until one is solid, then
    take the PERPENDICULAR distance (not the ray length) so the walls come out
    flat instead of fish-eyed. mget() is -1 on an empty cell, so "did I hit
    something" and "which tile do I draw" are the same read.

    The sspr() is inside this loop, not collected and issued afterwards, so
    `bench == 1` (geometry only) is exactly this function with the draw skipped --
    the same arrangement ray_lua.moy uses.
    """
    half = VH >> 1
    for i in range(cols):
        cam = 2.0 * i / cols - 1.0
        rdx = dx + plx * cam
        rdy = dy + ply * cam

        mapx = int(px)
        mapy = int(py)

        # 1e30 stands in for "this ray never crosses that axis"
        ddx = 1e30 if rdx == 0 else abs(1.0 / rdx)
        ddy = 1e30 if rdy == 0 else abs(1.0 / rdy)

        if rdx < 0:
            sx = -1
            sidex = (px - mapx) * ddx
        else:
            sx = 1
            sidex = (mapx + 1.0 - px) * ddx
        if rdy < 0:
            sy = -1
            sidey = (py - mapy) * ddy
        else:
            sy = 1
            sidey = (mapy + 1.0 - py) * ddy

        side = 0
        cell = -1
        for _ in range(64):      # bounded: the map is walled, but never loop forever
            if sidex < sidey:
                sidex = sidex + ddx
                mapx = mapx + sx
                side = 0
            else:
                sidey = sidey + ddy
                mapy = mapy + sy
                side = 1
            cell = mget(mapx, mapy)
            if cell >= 0:
                break

        if side:
            dist = sidey - ddy
        else:
            dist = sidex - ddx
        if dist < 0.02:
            dist = 0.02

        lh = int(VH / dist)
        top = half - (lh >> 1)   # unclipped: the crop below needs the real extent

        if lh > 0 and cell >= 0 and bench == 0:
            # Where along the wall face the ray landed picks the texture COLUMN,
            # and the side picks the row: the dim twin is one sheet row down.
            if side:
                hit = px + dist * rdx
            else:
                hit = py + dist * rdy
            u = (cell % 16) * 8 + int((hit - int(hit)) * 8)
            v = (cell // 16) * 8 + side * 8
            wide = VW - i * step   # the last column may be narrower than `step`
            if wide > step:
                wide = step
            # A slice taller than the view is CROPPED, not squashed into what
            # fits: walking into a wall magnifies its texture, never shrinks it.
            if lh > VH:
                v0 = (-top * 8) // lh
                v1 = ((VH - top) * 8 + lh - 1) // lh
                if v1 > 8:
                    v1 = 8
                sspr(u, v + v0, 1, v1 - v0, VX + i * step, VY, wide, VH)
            else:
                sspr(u, v, 1, 8, VX + i * step, VY + top, wide, lh)


# -- the tri() half: a spinning flat-shaded tetrahedron ---------------------

TETRA = (((0.0, -1.0, 0.0), (-0.94, 0.47, -0.54), (0.94, 0.47, -0.54)),
         ((0.0, -1.0, 0.0), (0.94, 0.47, -0.54), (0.0, 0.47, 1.08)),
         ((0.0, -1.0, 0.0), (0.0, 0.47, 1.08), (-0.94, 0.47, -0.54)),
         ((-0.94, 0.47, -0.54), (0.94, 0.47, -0.54), (0.0, 0.47, 1.08)))
FACE = (8, 9, 10, 12)


def _draw_tetra():
    # Rotate about Y by the turntable angle, project, then paint back-to-front.
    # A painter's sort is all the depth handling four faces need.
    c = rc
    s = rs
    cx = VX + (VW >> 1)
    cy = VY + (VH >> 1)
    k = VW * 0.8                 # projection scale, relative to the viewport
    order = []
    for f in range(4):
        zs = 0.0
        pts = []
        for v in TETRA[f]:
            x = v[0] * c + v[2] * s
            z = -v[0] * s + v[2] * c
            z = z + 3.0          # push the model away from the camera
            zs = zs + z
            m = k / z
            pts.append((cx + int(x * m), cy + int(v[1] * m)))
        order.append((zs, f, pts))
    order.sort()
    for i in range(3, -1, -1):
        item = order[i]
        p = item[2]
        tri(p[0][0], p[0][1], p[1][0], p[1][1], p[2][0], p[2][1], FACE[item[1]])


def _draw():
    global frames, t_mark, fps

    if mode:
        rect(VX, VY, VW, VH, 0)
        _draw_tetra()
    elif bench == 1:
        _cast()                  # geometry only: _cast skips its own sspr()
    elif bench == 2:
        half = VH >> 1           # background only: the two wide rects
        rect(VX, VY, VW, half, CEIL)
        rect(VX, VY + half, VW, VH - half, FLOOR)
    elif bench == 3:
        half = VH >> 1           # background only, but ceiling via linear cls
        cls(CEIL)
        rect(VX, VY + half, VW, VH - half, FLOOR)
    else:
        half = VH >> 1
        rect(VX, VY, VW, half, CEIL)          # two WIDE sequential fills beat
        rect(VX, VY + half, VW, VH - half, FLOOR)   # per-column strips (see top)
        _cast()                  # each wall is one sspr(), drawn as it is found

    frames = frames + 1
    now = time()
    if now - t_mark >= 500:
        fps = frames * 1000 // (now - t_mark)
        frames = 0
        t_mark = now

    print("FPS " + str(fps), VX + 3, VY + 3, 7)
    if mode:
        print("TRI B=RAYS", VX + 3, VY + 12, 6)
    elif bench:
        print("BENCH " + str(bench), VX + 3, VY + 12, 6)
    else:
        print(str(cols) + " RAYS B=TRI", VX + 3, VY + 12, 6)
