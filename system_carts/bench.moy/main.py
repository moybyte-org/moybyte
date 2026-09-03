# Bench -- the on-glass performance meter (2026-08-03).
#
# Runs itself, no input needed, results stay on screen (exit like any game).
# Two measurements, then a report:
#
#   MICRO  one draw verb per frame, an adaptively-sized batch timed with
#          time() (ms clock, so batches auto-grow until they span >=25ms).
#          Best-of-8 per verb -> the verb's clean cost, GC landings excluded.
#   GAME   ~10s of a busy Brick-Siege-shaped scene. Each frame records the
#          time() delta since the previous _update -- wall clock between two
#          cart ticks, so it includes the console's routing, composite and
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
PHASE_DONE = 6

GAME_FRAMES = 400          # ~10s at 40fps (the "GAME FRAMES" card overrides)
SCENE_FRAMES = 200         # the three isolation phases (~5s each)
REPS = 8                   # best-of per verb
TARGET_MS = 25             # grow a batch until it costs at least this

# The isolation phases exist because a whole-frame number cannot say WHERE the
# time went, and the cross-language comparison kept stalling on exactly that:
# per-verb costs said Lua should win the game scene and the measured frame said
# it lost. So measure the floor, then add one ingredient at a time. IDLE is the
# console's own frame -- routing, composite, flush -- and the other two are read
# as deltas from it, which is the only way the two languages can be compared on
# a term they both actually pay.
LOGIC_ITERS = 3000         # per frame, in the LOGIC phase
DRAW_OPS = 300             # per frame, in the DRAW phase

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
    # the map verb's field: a deterministic 15x8 region (tiles 0-7)
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
    branches, because there are five timed phases now and the Lua twin has to
    match this structure line for line."""
    n = cfg("frames", GAME_FRAMES)
    return {
        PHASE_IDLE: ("idle", _idle_scene, SCENE_FRAMES),
        PHASE_LOGIC: ("logic", _logic_scene, SCENE_FRAMES),
        PHASE_DRAW: ("draw", _draw_scene, SCENE_FRAMES),
        PHASE_GAME: ("silent", _game_scene, n),
        PHASE_GAME_SND: ("sound", _game_scene, n),
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
        y += 10                      # 13 verbs since 2026-08-04: tight rows
    y += 4
    # The isolation phases as ONE line: the floor absolute, the other two as
    # deltas from it, because the delta is the whole point and 320px is 40
    # characters. Full percentiles go to serial.
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
        y += 11
    for label, key in (("SILENT", "silent"), ("SOUND", "sound")):
        sc = st.get(key)
        if sc is None:
            continue
        print(label + " n=" + str(sc["n"]) + " fps=" + _f1(sc["fps"])
              + " p50=" + _f1(sc["p50"]) + " w=" + _f1(sc["worst"]), 8, y, 11)
        y += 11
    y += 4
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
    for label in ("idle", "logic", "draw", "silent", "sound"):
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
#   Verb rows run 8..63 (three cells each), so the roster caps at 18.
_VERB_ID = {"cls": 0, "rect": 1, "circ": 2, "line": 3, "pix": 4, "print": 5,
            "rectb": 6, "circb": 7, "tri": 8, "spr": 9, "map": 10,
            "sspr": 11, "tline": 12, "trib": 13, "oval": 14, "ovalb": 15,
            "oval_p": 16}
_PHASE_ORDER = (("idle", 0), ("logic", 1), ("draw", 2),
                ("silent", 3), ("sound", 4))


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
