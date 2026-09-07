# Bench -- the on-glass performance meter (2026-08-03).
#
# Runs itself, no input needed, results stay on screen (exit like any game).
# There is ONE Python bench and one Lua twin, and everything else in the store
# is a game or an app: the ray/tetra/scroll/layer scenes below were separate
# carts (Ray Test, Ray Lua, Layer Test) until 2026-09-06, which meant four
# carts to run, four report shapes to read and three of them measured by eye
# off an on-screen fps counter. The float/table phases came the same way on
# 2026-09-07, off the boards' own diagnostic shelf (membench, membench_lua,
# opbench): what those measured and nothing here did was the language away
# from the draw verbs -- the float lane, and an indexed container plus a call.
#
# A micro pass, then a scene per thing worth timing, then a report:
#
#   MICRO  one draw verb per frame, an adaptively-sized batch timed with
#          time() (ms clock, so batches auto-grow until they span >=25ms).
#          Best-of-8 per verb -> the verb's clean cost, GC landings excluded.
#   SCENES a fixed workload for a fixed number of frames. Each frame records
#          the time() delta since the previous _update -- wall clock between
#          two cart ticks, so it includes the console's routing, composite and
#          flush, and (unlike the dt argument, clamped to 100ms by the loop)
#          it keeps the real size of the worst frames. p50 says the steady
#          rate, p99/worst say the stutter.
#
# The report prints on screen AND once to serial ("BENCHCART ..." lines), so
# a build A/B is: run Bench, read the glass (or the capture) -- no play skill,
# no feel, same workload every time.

PHASE_MICRO = 0
PHASE_IDLE = 1        # the floor: what a frame costs when the cart does nothing
PHASE_LOGIC = 2       # IDLE + arithmetic only   -> LOGIC - IDLE = the language
PHASE_DRAW = 3        # IDLE + draw calls only   -> DRAW  - IDLE = the draw path
PHASE_GAME = 4        # the scene, silent
PHASE_GAME_SND = 5    # the SAME scene + a beep every ~0.4s: the audio-cost A/B
PHASE_RAY = 6         # the software 3D frame (#167)
PHASE_TETRA = 7       # the same frame's tri() half
PHASE_SCROLL = 8      # a scrolling level re-rendered by map() every frame
PHASE_LAYER = 9       # the SAME pixels window-copied from a layer (#54)
PHASE_FLOAT = 10      # IDLE + float arithmetic only
PHASE_TABLE = 11      # IDLE + an indexed container and a call
PHASE_DONE = 12

GAME_FRAMES = 400          # ~10s at 40fps (the "GAME FRAMES" card overrides)
SCENE_FRAMES = 200         # the three isolation phases (~5s each)
FOLD_FRAMES = 90           # the four folded scenes. 90 is the RAY/TETRA
                           # turntable's full revolution at the TS step below,
                           # so the phase sweeps every sightline exactly once;
                           # the two scroll phases take the same count to stay
                           # comparable with each other.
REPS = 8                   # best-of per verb
TARGET_MS = 25             # grow a batch until it costs at least this

# The isolation phases exist because a whole-frame number cannot say WHERE the
# time went, and the cross-language comparison kept stalling on exactly that:
# per-verb costs said Lua should win the game scene and the measured frame said
# it lost. So measure the floor, then add one ingredient at a time. IDLE is the
# console's own frame -- routing, composite, flush -- and every other scene is
# read as a DELTA from it, which is the only way the two languages can be
# compared on a term they both actually pay. It is also what makes the folded
# scenes honest: each one draws the same single clear and the same one label
# the floor does, so subtracting the floor leaves the ray march, the tri fan,
# the map() call or the layer copy and nothing else.
LOGIC_ITERS = 3000         # per frame, in the LOGIC phase
DRAW_OPS = 300             # per frame, in the DRAW phase
FLOAT_ITERS = 3000         # per frame, in the FLOAT phase -- LOGIC's count on
                           # purpose: same loop, different lane, so the two
                           # deltas are read against each other directly
TABLE_ITERS = 3000         # per frame, in the TABLE phase
TABLE_N = 1024             # entries in the container that phase walks. Big
                           # enough that the indices miss cache, small enough
                           # that holding it does not move the heap under the
                           # phases that come first (it is built at its own
                           # phase's first frame, like the layer, anyway)

# -- the RAY/TETRA scenes (were ray_test.moy / ray_lua.moy, #167) -------------
#
# A textured raycaster: the DDA march runs in the cart language, one iteration
# per screen column, and each wall is one sspr(). The cart used to pack a
# frame's walls into a span buffer and issue ONE rect_batch, on the belief that
# the MP->C crossing was what made software 3D affordable. Measured on glass
# 2026-08-14 (plan 6.10): it is not. A draw call is ~3us of dispatch, so 160 of
# them are ~0.5ms of a 20ms frame, and the batch verbs were deleted -- they
# cost every kid a second vocabulary (Lua could not call them at all) to buy
# that. What is left is a language measurement, which is why the Lua twin runs
# this scene line for line.
#
# The maze is map.moymap cols 0..11, rows 8..29 -- the band the scroll scenes'
# window never shows -- and the walls are sprites.moygfx tiles 64..67, so both
# are editable. A wall's SIDE-ON face is the tile one sheet row below its front
# face (64 lit -> 80 dim), which is the two-tone shading that reads as 3D.
#
# The ceiling and floor are TWO BIG RECTS, not per-column spans. Painting each
# pixel exactly once (a ceiling/wall/floor span per column) sounds better and
# measured 2x WORSE on glass: 320 one-column-wide vertical strips walk a
# 640-byte stride, so every pixel lands in a different cache line, while two
# wide rects are sequential writes. #163's finding in cart form -- "the win is
# fewer AND WIDER spans, contiguity as much as call count".
#
# WHAT THE NUMBER MEANS NOW. Ray Test declared `"fps": "free"` and reported its
# own free-running fps, because a dt-scaled cart a kid drives should run as
# fast as the board can. The bench is frame-paced like every other cart here,
# so this row is a FRAME TIME under the console's pacing: on a board where the
# scene costs more than the tick budget (every board so far -- the march is
# tens of ms) it is the work, and where it costs less the row reads the pace
# and the RAY-FLOOR delta is what still measures the march. The camera turns
# itself instead of being driven, so the workload is the same every run.
CEIL = 1                     # dark_blue
FLOOR = 5                    # dark_grey
TC = 0.99755                 # one turn step, precomputed: cos/sin of ~0.07 rad.
TS = 0.06994                 # Rotating the basis by a constant angle is why
                             # this cart needs no math library at all.
RAY_STEP = 2                 # screen pixels per ray (160 rays), Ray Test's default
RAY_PX = 5.5                 # a parked camera in the maze's long corridor: it
RAY_PY = 16.5                # turns, it never walks, so nothing here is input

# -- the SCROLL/LAYER scenes (were layer_test.moy, #54) -----------------------
#
# Does the scroll layer still pay for itself? A layer trades 120KB of RAM for a
# cheaper per-frame background: instead of re-running map() over the visible
# level every frame, pre-render the level once into a wide off-screen buffer
# and window-copy the visible part. The original measurement (#54) was ~12-14ms
# of map() against ~7ms of copy, taken BEFORE the moy_gfx -O3 pragma (#77) cut
# render ~40% on the S3 -- which sped up BOTH sides, so the absolute saving
# shrank and nobody re-measured.
#
# Two phases from a FIXED camera rather than one cart alternating routes:
# identical pixels, identical content, only the route differs, and each side
# gets the same percentile treatment every other scene gets. SCROLL - FLOOR is
# the map() call, LAYER - FLOOR is the copy, and their ratio (the "X" on the
# report line) is what the old cart printed. Note what this is NOT about:
# neither path runs in the interpreter, so the Lua tier cannot move it and a
# faster cart language is not an argument for dropping layers.
CAM = 96                     # fixed scroll position: deterministic, mid-level
LW = 512                     # layer width in px (the map is 64 tiles = 512px)

state = {}


def _verbs():
    # (name, per-op callable, starting batch size). Positions come from a tiny
    # LCG so the workload is identical every run on every build.
    def v_cls(i):
        cls(1 + (i & 7))

    def v_rect(i):
        rect((i * 37) % 290, (i * 53) % 225, 24, 12, 2 + (i & 15))

    def v_circ(i):
        circ(10 + (i * 41) % 300, 10 + (i * 29) % 220, 7, 2 + (i & 15))

    def v_line(i):
        line((i * 17) % 320, (i * 31) % 240, (i * 59) % 320, (i * 43) % 240,
             2 + (i & 15))

    def v_pix(i):
        pix((i * 13) % 320, (i * 7) % 240, 2 + (i & 15))

    def v_print(i):
        print("BENCH", (i * 47) % 260, (i * 23) % 230, 7)

    # 2026-08-04 (#163): the rest of the drawing verb set. APPENDED so the
    # first six lines stay comparable with every earlier capture. spr rides
    # the bundled sprites.moygfx (tiles 0-7); map rides the _init-mset field.
    def v_rectb(i):
        rectb((i * 37) % 280, (i * 53) % 215, 30, 18, 2 + (i & 15))

    def v_circb(i):
        circb(10 + (i * 41) % 300, 10 + (i * 29) % 220, 8, 2 + (i & 15))

    def v_tri(i):
        tri((i * 17) % 300, (i * 31) % 230, (i * 59) % 300 + 10,
            (i * 43) % 230, (i * 23) % 300, ((i * 13) % 230) + 8, 2 + (i & 15))

    def v_spr(i):
        spr(i & 7, (i * 37) % 310, (i * 53) % 230)

    # There was a "sprb" scene here (one spr_batch of 64 prebuilt tiles) until
    # 2026-08-14. The verb is gone (plan 6.10) and so is the asymmetry it created:
    # the Lua twin never had this scene, because a trampoline cannot marshal a
    # list, so the two Bench carts disagreed by one row and every table taken from
    # them had a hole in it. The "spr" scene above measures the same lane.

    def v_map(i):
        map(0, 0, 15, 8, (i * 7) % 40, (i * 11) % 40)

    def v_sspr(i):
        sspr((i & 7) * 8, 0, 8, 8, (i * 37) % 300, (i * 53) % 220, 20, 20)

    def v_tline(i):
        # SPEC.md 6.1 tline: one full-width textured scanline per call, 16.16
        # fixed point, sampling the _init-mset field -- the Mode 7 shape.
        tline(0, (i * 13) % 240, 319, (i * 13) % 240,
              (i * 7) << 14, (i * 11) << 13, 16384 + ((i & 15) << 7), i << 6)

    # APPENDED when moy core 0.3 promoted SPEC.md 6.1: a promoted verb owes a
    # cost, and these three had no measured row anywhere. `fillp` is NOT a
    # scene: it is draw STATE, so its cost belongs to whichever shape carries
    # the pattern -- v_oval_p is that row, oval's own geometry under a dither,
    # so the pair reads as the pattern's price rather than as a fourth shape.
    def v_trib(i):
        trib((i * 17) % 300, (i * 31) % 230, (i * 59) % 300 + 10,
             (i * 43) % 230, (i * 23) % 300, ((i * 13) % 230) + 8, 2 + (i & 15))

    def v_oval(i):
        oval((i * 17) % 280, (i * 31) % 200, 8 + (i & 31), 8 + ((i >> 2) & 31),
             2 + (i & 15))

    def v_ovalb(i):
        ovalb((i * 17) % 280, (i * 31) % 200, 8 + (i & 31), 8 + ((i >> 2) & 31),
              2 + (i & 15))

    def v_oval_p(i):
        # Same call as v_oval, under a pattern: the difference IS fillp's cost.
        fillp(0xA5A5)
        oval((i * 17) % 280, (i * 31) % 200, 8 + (i & 31), 8 + ((i >> 2) & 31),
             2 + (i & 15))
        fillp()

    return [("cls", v_cls, 4), ("rect", v_rect, 100), ("circ", v_circ, 100),
            ("line", v_line, 100), ("pix", v_pix, 500), ("print", v_print, 50),
            ("rectb", v_rectb, 100), ("circb", v_circb, 100),
            ("tri", v_tri, 50), ("spr", v_spr, 500),
            ("map", v_map, 8), ("sspr", v_sspr, 50),
            ("tline", v_tline, 50), ("trib", v_trib, 50),
            ("oval", v_oval, 100), ("ovalb", v_ovalb, 100),
            ("oval_p", v_oval_p, 100)]


def _init():
    # the map verb's field: a deterministic 15x8 region (tiles 0-7) written over
    # the shipped map's top-left corner, which the ray maze and the scroll
    # window both stay clear of
    y = 0
    while y < 8:
        x = 0
        while x < 15:
            mset(x, y, (x + y) & 7)
            x += 1
        y += 1
    state["phase"] = PHASE_MICRO
    state["verbs"] = _verbs()
    state["vi"] = 0            # which verb
    state["rep"] = 0           # which repetition
    state["k"] = state["verbs"][0][2]
    state["best"] = None
    state["reps"] = []         # this verb's timed batches, ms
    state["micro"] = []        # (name, k, best_ms, med_ms, max_ms)
    state["dts"] = []          # the CURRENT phase's frame times, ms (floats)
    state["frame"] = 0
    state["stats"] = {}        # label -> stats, one entry per timed phase
    state["scenes"] = _scenes()
    state["sink"] = 0
    state["reported"] = False
    state["warm"] = 5          # skip the first frames (start spike)
    state["ray"] = (0.0, -1.0, 0.66, 0.0)    # dir + camera plane (66 deg FOV)
    state["rc"] = 1.0          # the tetra turntable's cos/sin, advanced one
    state["rs"] = 0.0          # fixed step per frame -- O(1), never recomputed
    state["lay"] = None        # the scroll layer, built at its phase's first frame
    state["tab"] = None        # the TABLE phase's container, same arrangement
    pmem(3, 0)                 # arm the pmem report: a PREVIOUS run's done
                               # flag persists (pmem is the save file), and a
                               # harness polling cell 3 must not read it


def _measure_one():
    """One timed batch of the current verb; grows k until the batch is
    readable on a 1ms clock, then keeps the best of REPS runs."""
    name, fn, _k0 = state["verbs"][state["vi"]]
    k = state["k"]
    t0 = time()
    i = 0
    while i < k:
        fn(i)
        i += 1
    ms = time() - t0
    if ms < TARGET_MS and k < 50000:
        state["k"] = k * 2          # too fast to read: bigger batch, same rep
        return
    if state["best"] is None or ms < state["best"]:
        state["best"] = ms
    # Keep every batch, not just the winner. `best` alone reported 31.2us/op
    # for `line` on one run and 62.5 on the next with no hint in the output
    # that anything was unstable -- a min cannot say how far the other seven
    # landed. Purely additive: the k ladder above is untouched, because a
    # version that also changed WHEN k locks moved every small-k verb by 6-7x
    # (line 62.5 -> 450) and had to be reverted.
    state["reps"].append(ms)
    state["rep"] += 1
    if state["rep"] >= REPS:
        s = sorted(state["reps"])
        state["micro"].append((name, k, state["best"], s[len(s) // 2], s[-1]))
        state["vi"] += 1
        state["rep"] = 0
        state["reps"] = []
        state["best"] = None
        if state["vi"] >= len(state["verbs"]):
            state["phase"] = PHASE_IDLE
            state["frame"] = 0
        else:
            state["k"] = state["verbs"][state["vi"]][2]


def _game_scene(f):
    """A Brick-Siege-shaped frame: full clear, brick field, moving balls,
    HUD text -- the same ops every run."""
    cls(1)
    row = 0
    while row < 4:
        col = 0
        while col < 10:
            rect(8 + col * 30, 28 + row * 14, 26, 10, 2 + ((row + col) & 7))
            col += 1
        row += 1
    b = 0
    while b < 6:
        x = (f * (3 + b) + b * 53) % 300
        y = 100 + ((f * (2 + b) + b * 31) % 120)
        circ(10 + x, y, 6, 10 + b)
        b += 1
    line(0, 96, 319, 96, 7)
    rect((f * 4) % 250, 226, 40, 8, 12)
    print("SCORE 1234", 8, 6, 7)
    print("LIVES 3", 120, 6, 7)
    print("BENCH GAME PHASE", 190, 6, 6)


def _idle_scene(f):
    """THE FLOOR. One clear, one label -- whatever this frame costs is the
    console's own overhead, and every other phase is read as a delta from it."""
    cls(1)
    print("IDLE", 8, 6, 7)


def _logic_scene(f):
    """Arithmetic only, drawn exactly like IDLE, so LOGIC - IDLE is what the
    LANGUAGE costs and nothing else.

    Small-magnitude integer math on purpose: a multiply that overflowed 31 bits
    would allocate a bignum on MicroPython, and this would end up measuring the
    allocator. The float chain rides along because carts do float physics and
    the two boards' VMs differ there (LUA_32BITS vs MicroPython's packed
    floats)."""
    cls(1)
    x = 1 + (f & 15)
    s = 0
    fx = 0.5
    i = 0
    while i < LOGIC_ITERS:
        x = (x * 37 + 11) % 1021
        s = s + (x & 31) - 15
        fx = fx + 0.25
        if fx > 100.0:
            fx = fx - 100.0
        i += 1
    state["sink"] = s + int(fx)      # keep the loop from being dead code
    print("LOGIC", 8, 6, 7)


def _draw_scene(f):
    """Draw calls only, trivial arithmetic, drawn over the same IDLE floor, so
    DRAW - IDLE is what the DRAW PATH costs at a per-frame call count a real
    cart reaches. Deliberately the plainest verb there is: a rect is a memset
    per row in every backend, so what is left in the delta is the crossing."""
    cls(1)
    i = 0
    while i < DRAW_OPS:
        rect((i * 37) % 290, (i * 53) % 225, 8, 6, 2 + (i & 15))
        i += 1
    print("DRAW", 8, 6, 7)


def _cast(dx, dy, plx, ply):
    """March one ray per column and draw its wall slice.

    Textbook DDA: step whole map cells along the ray until one is solid, then
    take the PERPENDICULAR distance (not the ray length) so the walls come out
    flat instead of fish-eyed. mget() is -1 on an empty cell, so "did I hit
    something" and "which tile do I draw" are the same read -- and the maze's
    border is solid, so a ray can never leave it.

    The sspr() is inside this loop, not collected and issued afterwards, which
    is the arrangement the Lua twin copies line for line."""
    cols = W // RAY_STEP
    half = H >> 1
    for i in range(cols):
        cam = 2.0 * i / cols - 1.0
        rdx = dx + plx * cam
        rdy = dy + ply * cam

        mapx = int(RAY_PX)
        mapy = int(RAY_PY)

        # 1e30 stands in for "this ray never crosses that axis"
        ddx = 1e30 if rdx == 0 else abs(1.0 / rdx)
        ddy = 1e30 if rdy == 0 else abs(1.0 / rdy)

        if rdx < 0:
            sx = -1
            sidex = (RAY_PX - mapx) * ddx
        else:
            sx = 1
            sidex = (mapx + 1.0 - RAY_PX) * ddx
        if rdy < 0:
            sy = -1
            sidey = (RAY_PY - mapy) * ddy
        else:
            sy = 1
            sidey = (mapy + 1.0 - RAY_PY) * ddy

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

        lh = int(H / dist)
        top = half - (lh >> 1)   # unclipped: the crop below needs the real extent

        if lh > 0 and cell >= 0:
            # Where along the wall face the ray landed picks the texture COLUMN,
            # and the side picks the row: the dim twin is one sheet row down.
            if side:
                hit = RAY_PX + dist * rdx
            else:
                hit = RAY_PY + dist * rdy
            u = (cell % 16) * 8 + int((hit - int(hit)) * 8)
            v = (cell // 16) * 8 + side * 8
            # A slice taller than the view is CROPPED, not squashed into what
            # fits: walking into a wall magnifies its texture, never shrinks it.
            if lh > H:
                v0 = (-top * 8) // lh
                v1 = ((H - top) * 8 + lh - 1) // lh
                if v1 > 8:
                    v1 = 8
                sspr(u, v + v0, 1, v1 - v0, i * RAY_STEP, 0, RAY_STEP, H)
            else:
                sspr(u, v, 1, 8, i * RAY_STEP, top, RAY_STEP, lh)


def _ray_scene(f):
    """The software 3D frame: two wide background rects, then one marched,
    textured column per RAY_STEP pixels. The camera TURNS one fixed step a
    frame -- a turntable, not a walk -- so FOLD_FRAMES sweeps the maze once."""
    dx, dy, plx, ply = state["ray"]
    dx, dy = dx * TC + dy * TS, -dx * TS + dy * TC
    plx, ply = plx * TC + ply * TS, -plx * TS + ply * TC
    state["ray"] = (dx, dy, plx, ply)
    half = H >> 1
    rect(0, 0, W, half, CEIL)             # two WIDE sequential fills beat
    rect(0, half, W, H - half, FLOOR)     # per-column strips (see the header)
    _cast(dx, dy, plx, ply)
    print("RAY", 8, 6, 7)


TETRA = (((0.0, -1.0, 0.0), (-0.94, 0.47, -0.54), (0.94, 0.47, -0.54)),
         ((0.0, -1.0, 0.0), (0.94, 0.47, -0.54), (0.0, 0.47, 1.08)),
         ((0.0, -1.0, 0.0), (0.0, 0.47, 1.08), (-0.94, 0.47, -0.54)),
         ((-0.94, 0.47, -0.54), (0.94, 0.47, -0.54), (0.0, 0.47, 1.08)))
FACE = (8, 9, 10, 12)


def _tetra_scene(f):
    """The tri() half of the same 3D frame: a spinning flat-shaded tetrahedron.
    Rotate about Y by the turntable angle, project, then paint back-to-front --
    a painter's sort is all the depth handling four faces need."""
    c = state["rc"]
    s = state["rs"]
    state["rc"] = c * TC - s * TS
    state["rs"] = c * TS + s * TC
    rect(0, 0, W, H, 0)
    cx = W >> 1
    cy = H >> 1
    k = W * 0.8                  # projection scale, relative to the viewport
    order = []
    for fi in range(4):
        zs = 0.0
        pts = []
        for v in TETRA[fi]:
            x = v[0] * c + v[2] * s
            z = -v[0] * s + v[2] * c
            z = z + 3.0          # push the model away from the camera
            zs = zs + z
            m = k / z
            pts.append((cx + int(x * m), cy + int(v[1] * m)))
        order.append((zs, fi, pts))
    order.sort()
    for i in range(3, -1, -1):
        item = order[i]
        p = item[2]
        tri(p[0][0], p[0][1], p[1][0], p[1][1], p[2][0], p[2][1], FACE[item[1]])
    print("TETRA", 8, 6, 7)


def _scroll_scene(f):
    """The layerless scroller's frame: re-render the visible level. 41 tile
    columns covers 320px plus the sub-tile offset."""
    cls(1)
    map(CAM // 8, 0, (W // 8) + 1, H // 8, -(CAM % 8), 0)
    print("SCROLL", 8, 6, 7)


def _layer_scene(f):
    """The same pixels, window-copied out of a layer pre-rendered ONCE -- the
    cost a layer front-loads is this build, and the point is that it never
    recurs. Built at the phase's first frame (inside the warm-up, so it is not
    in any sample) and only then, because it is 120KB the earlier phases have
    no reason to be measured under."""
    cls(1)
    lay = state["lay"]
    if lay is None:
        try:
            lay = make_layer(LW, H)
            lay.cls(0)
            lay.map(0, 0, LW // 8, H // 8, 0, 0)
        except Exception:        # no room for the buffer: the row reads as the
            lay = 0              # floor, which is honest and is not a crash
        state["lay"] = lay
    if lay:
        draw_layer(lay, CAM, 0)
    print("LAYER", 8, 6, 7)


def _float_scene(f):
    """Float arithmetic only, drawn over the same IDLE floor, so FLOAT - IDLE
    is the FLOAT lane alone -- the one LOGIC dilutes on purpose (its chain is
    mostly small integers, and the two VMs differ most here: LUA_32BITS floats
    against MicroPython's packed ones, which is the lane #66's float-boxing
    work moved). The rotate is the raycaster's own, at its own constants, so
    what this prices is the arithmetic the ray and tetra scenes are made of."""
    cls(1)
    x = 0.5 + (f & 7)
    y = 1.25
    a = 1.0
    n = 0
    i = 0
    while i < FLOAT_ITERS:
        x, y = x * TC - y * TS, x * TS + y * TC
        a = a * 1.0001
        if a > 100.0:
            a = a * 0.01
        if x * a > y:
            n = n + 1
        i += 1
    state["sink"] = n                # keep the loop from being dead code
    print("FLOAT", 8, 6, 7)


def _bump(v):
    return v + 1


def _table_scene(f):
    """An indexed container and a call, over the same IDLE floor: TABLE - IDLE
    is what a store, a load and one function crossing cost, which LOGIC (all
    locals) never touches. It is the retired mem shelf's own question -- what a
    PICO-8 style memory map costs as a table -- and #63's call-frame spill in
    the same row. Built at this phase's first frame, like the layer."""
    cls(1)
    t = state["tab"]
    if t is None:
        t = [0] * TABLE_N
        state["tab"] = t
    s = 0
    i = 0
    while i < TABLE_ITERS:
        j = i & (TABLE_N - 1)
        t[j] = _bump(i)
        s = s + t[j]
        i += 1
    state["sink"] = s
    print("TABLE", 8, 6, 7)


def _pct(s, p):
    return s[min(len(s) - 1, (p * len(s)) // 100)]


def _stats_of(raw):
    dts = sorted(raw)
    n = len(dts)
    if n == 0:
        dts = [0.0]
        n = 1
    st = {
        "n": n,
        "p50": _pct(dts, 50),
        "p90": _pct(dts, 90),
        "p99": _pct(dts, 99),
        "worst": dts[-1],
        "best": dts[0],
    }
    st["fps"] = (1000.0 / st["p50"]) if st["p50"] > 0 else 0.0
    return st


def _scenes():
    """phase -> (label, scene fn, frames). One table instead of a chain of
    branches, because there are eleven timed phases now and the Lua twin has to
    match this structure line for line."""
    n = cfg("frames", GAME_FRAMES)
    return {
        PHASE_IDLE: ("idle", _idle_scene, SCENE_FRAMES),
        PHASE_LOGIC: ("logic", _logic_scene, SCENE_FRAMES),
        PHASE_DRAW: ("draw", _draw_scene, SCENE_FRAMES),
        PHASE_GAME: ("silent", _game_scene, n),
        PHASE_GAME_SND: ("sound", _game_scene, n),
        PHASE_RAY: ("ray", _ray_scene, FOLD_FRAMES),
        PHASE_TETRA: ("tetra", _tetra_scene, FOLD_FRAMES),
        PHASE_SCROLL: ("scroll", _scroll_scene, FOLD_FRAMES),
        PHASE_LAYER: ("layer", _layer_scene, FOLD_FRAMES),
        PHASE_FLOAT: ("float", _float_scene, SCENE_FRAMES),
        PHASE_TABLE: ("table", _table_scene, SCENE_FRAMES),
    }


def _update(dt):
    ph = state["phase"]
    sc = state["scenes"].get(ph)
    if sc is not None:
        now = time()
        prev = state.get("t_prev")
        state["t_prev"] = now
        if state["warm"] > 0:
            state["warm"] -= 1
        elif prev is not None:
            state["dts"].append(1.0 * (now - prev))
        if ph == PHASE_GAME_SND and state["frame"] % 15 == 0:
            # a fresh trigger every ~0.4s -- the brick-hit cadence. The mixer
            # then has an ACTIVE voice most of the phase, which is the load
            # being A/B'd against the silent phase.
            beep(220 + (state["frame"] // 15 % 8) * 55, 0.3)
        state["frame"] += 1
        if state["frame"] >= sc[2]:
            state["stats"][sc[0]] = _stats_of(state["dts"])
            state["dts"] = []
            state["phase"] = ph + 1
            state["frame"] = 0
            state["warm"] = 5
            state["t_prev"] = None


def _draw():
    ph = state["phase"]
    if ph == PHASE_MICRO:
        # the batch IS this frame's drawing (deliberately heavy)
        _measure_one()
        name = (state["verbs"][state["vi"]][0]
                if state["vi"] < len(state["verbs"]) else "")
        rect(0, 226, 320, 14, 0)
        print("BENCH MICRO " + name + " k=" + str(state["k"]), 8, 229, 7)
    else:
        sc = state["scenes"].get(ph)
        if sc is None:
            _report()
        else:
            sc[1](state["frame"])
            if ph == PHASE_GAME_SND:
                print("+ SOUND", 250, 226, 10)


def _report():
    st = state["stats"]
    cls(0)
    print("MOYBYTE BENCH", 8, 8, 11)
    y = 26
    for name, k, best, med, mx in state["micro"]:
        us = (best * 1000.0) / k
        print(name + " x" + str(k) + " = " + str(best) + "-" + str(mx)
              + "ms  (" + str(int(us * 10) / 10.0) + "us/op)", 8, y, 7)
        y += 9                       # 17 verbs, five scene lines and the
                                     # exit hint: 240px exactly
    # The scenes as delta lines: the floor absolute, everything else as its
    # distance from the floor, because the delta is the whole point and 320px
    # is 40 characters. Full percentiles go to serial and to pmem.
    fl = st.get("idle")
    lo = st.get("logic")
    dr = st.get("draw")
    if fl is not None:
        line1 = "FLOOR " + _f1(fl["p50"])
        if lo is not None:
            line1 += "  LOGIC +" + _f1(lo["p50"] - fl["p50"])
        if dr is not None:
            line1 += "  DRAW +" + _f1(dr["p50"] - fl["p50"])
        print(line1, 8, y, 14)
        y += 10
        line1b = ""
        for label, key in (("FLOAT", "float"), ("TABLE", "table")):
            sc = st.get(key)
            if sc is not None:
                line1b += ("  " if line1b else "") + label + " +" \
                    + _f1(sc["p50"] - fl["p50"])
        if line1b:
            print(line1b, 8, y, 14)
            y += 10
        line2 = ""
        for label, key in (("RAY", "ray"), ("TET", "tetra"),
                           ("MAP", "scroll"), ("LAY", "layer")):
            sc = st.get(key)
            if sc is not None:
                line2 += label + "+" + _f1(sc["p50"] - fl["p50"]) + " "
        sc = st.get("scroll")
        la = st.get("layer")
        if sc is not None and la is not None and la["p50"] > fl["p50"]:
            # >1 means the layer is still winning, which is what #54 asks
            line2 += _f1((sc["p50"] - fl["p50"]) / (la["p50"] - fl["p50"])) + "X"
        if line2:
            print(line2, 8, y, 14)
            y += 10
    for label, key in (("SILENT", "silent"), ("SOUND", "sound")):
        sc = st.get(key)
        if sc is None:
            continue
        print(label + " n=" + str(sc["n"]) + " fps=" + _f1(sc["fps"])
              + " p50=" + _f1(sc["p50"]) + " w=" + _f1(sc["worst"]), 8, y, 11)
        y += 10
    print("HOLD BACK TO EXIT", 8, y, 6)
    if not state["reported"]:
        state["reported"] = True
        _serial_report()
        _pmem_report()


def _f1(v):
    return str(int(v * 10) / 10.0)


def _serial_report():
    # One machine-readable block; harmless if nobody is listening.
    for name, k, best, med, mx in state["micro"]:
        _p("BENCHCART verb=" + name + " k=" + str(k) + " best_ms=" + str(best)
           + " med_ms=" + str(med) + " max_ms=" + str(mx))
    for label in ("idle", "logic", "draw", "silent", "sound",
                  "ray", "tetra", "scroll", "layer", "float", "table"):
        s = state["stats"].get(label)
        if s is None:
            continue
        _p("BENCHCART phase=" + label + " n=" + str(s["n"])
           + " p50=" + _f1(s["p50"]) + " p90=" + _f1(s["p90"])
           + " p99=" + _f1(s["p99"]) + " worst=" + _f1(s["worst"])
           + " fps=" + _f1(s["fps"]))


# PMEM REPORT LAYOUT v1 (int32 cells; keep the three copies in lock-step --
# this cart, bench_lua.moy/main.lua, tools/p4_cart_bench.py). The Lua twin
# has no serial print (SPEC sandbox), so the report also goes into pmem, which
# a harness reads live through moycore.pmem_image; this cart writes the SAME
# cells so the channel itself is A/B-able. Cells are the bench's own save
# file; the numbers persisting is harmless and even handy.
#   0 magic 45948   1 version   2 n_verbs   3 done flag (written LAST)
#   8 + i*3:  verb_id, k, best_ms          (verb ids in _VERB_ID)
#   64 + i*8: phase_id, n, p50*10, p90*10, p99*10, worst*10, fps*10
#   Verb rows run 8..63 (three cells each), so the roster caps at 18; a phase
#   row is eight cells at 64 + id*8, and pmem is 256 cells, so 24 phases fit.
_VERB_ID = {"cls": 0, "rect": 1, "circ": 2, "line": 3, "pix": 4, "print": 5,
            "rectb": 6, "circb": 7, "tri": 8, "spr": 9, "map": 10,
            "sspr": 11, "tline": 12, "trib": 13, "oval": 14, "ovalb": 15,
            "oval_p": 16}
_PHASE_ORDER = (("idle", 0), ("logic", 1), ("draw", 2),
                ("silent", 3), ("sound", 4), ("ray", 5), ("tetra", 6),
                ("scroll", 7), ("layer", 8), ("float", 9), ("table", 10))


def _pmem_report():
    pmem(0, 45948)
    pmem(1, 1)
    pmem(2, len(state["micro"]))
    for i, (name, k, best, med, mx) in enumerate(state["micro"]):
        base = 8 + i * 3
        pmem(base, _VERB_ID.get(name, -1))
        pmem(base + 1, k)
        pmem(base + 2, int(best))
    for label, pid in _PHASE_ORDER:
        s = state["stats"].get(label)
        if s is None:
            continue
        base = 64 + pid * 8
        pmem(base, pid)
        pmem(base + 1, s["n"])
        pmem(base + 2, int(s["p50"] * 10))
        pmem(base + 3, int(s["p90"] * 10))
        pmem(base + 4, int(s["p99"] * 10))
        pmem(base + 5, int(s["worst"] * 10))
        pmem(base + 6, int(s["fps"] * 10))
    pmem(3, 1)


def _p(line):
    # The cart's print() is the CANVAS verb; reach Python's real print for the
    # serial line. MicroPython does not inject __builtins__ into an exec'd
    # namespace (the first Bench run's report never hit serial), but
    # __import__ resolves via the builtins fallback on both VMs.
    try:
        __import__("builtins").print(line)
    except Exception:
        pass
