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
PHASE_GAME = 1        # the scene, silent
PHASE_GAME_SND = 2    # the SAME scene + a beep every ~0.4s: the audio-cost A/B
PHASE_DONE = 3

GAME_FRAMES = 400          # ~10s at 40fps (the "GAME FRAMES" card overrides)
REPS = 8                   # best-of per verb
TARGET_MS = 25             # grow a batch until it costs at least this

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

    def v_sprb(i):
        spr_batch(state["sprb_items"])

    def v_map(i):
        map(0, 0, 15, 8, (i * 7) % 40, (i * 11) % 40)

    def v_sspr(i):
        sspr((i & 7) * 8, 0, 8, 8, (i * 37) % 300, (i * 53) % 220, 20, 20)

    def v_tline(i):
        # SPEC.md 6.1 tline: one full-width textured scanline per call, 16.16
        # fixed point, sampling the _init-mset field -- the Mode 7 shape.
        tline(0, (i * 13) % 240, 319, (i * 13) % 240,
              (i * 7) << 14, (i * 11) << 13, 16384 + ((i & 15) << 7), i << 6)

    return [("cls", v_cls, 4), ("rect", v_rect, 100), ("circ", v_circ, 100),
            ("line", v_line, 100), ("pix", v_pix, 500), ("print", v_print, 50),
            ("rectb", v_rectb, 100), ("circb", v_circb, 100),
            ("tri", v_tri, 50), ("spr", v_spr, 500), ("sprb", v_sprb, 8),
            ("map", v_map, 8), ("sspr", v_sspr, 50),
            ("tline", v_tline, 50)]


def _init():
    # the map verb's field: a deterministic 15x8 region (tiles 0-7)
    y = 0
    while y < 8:
        x = 0
        while x < 15:
            mset(x, y, (x + y) & 7)
            x += 1
        y += 1
    # sprb's prebuilt items (64 tiles, LCG positions) -- built ONCE so the
    # measure times the CALL, not per-frame list construction
    items = []
    i = 0
    while i < 64:
        items.append((i & 7, (i * 37) % 310, (i * 53) % 230))
        i += 1
    state["sprb_items"] = items
    state["phase"] = PHASE_MICRO
    state["verbs"] = _verbs()
    state["vi"] = 0            # which verb
    state["rep"] = 0           # which repetition
    state["k"] = state["verbs"][0][2]
    state["best"] = None
    state["reps"] = []         # this verb's timed batches, ms
    state["micro"] = []        # (name, k, best_ms, med_ms, max_ms)
    state["dts"] = []          # silent game-phase frame times, ms (floats)
    state["dts_snd"] = []      # sound game-phase frame times, ms
    state["frame"] = 0
    state["stats"] = None
    state["stats_snd"] = None
    state["reported"] = False
    state["warm"] = 5          # skip the first frames (start spike)


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
            state["phase"] = PHASE_GAME
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


def _update(dt):
    ph = state["phase"]
    if ph == PHASE_GAME or ph == PHASE_GAME_SND:
        now = time()
        prev = state.get("t_prev")
        state["t_prev"] = now
        if state["warm"] > 0:
            state["warm"] -= 1
        elif prev is not None:
            dst = state["dts"] if ph == PHASE_GAME else state["dts_snd"]
            dst.append(1.0 * (now - prev))
        if ph == PHASE_GAME_SND and state["frame"] % 15 == 0:
            # a fresh trigger every ~0.4s -- the brick-hit cadence. The mixer
            # then has an ACTIVE voice most of the phase, which is the load
            # being A/B'd against the silent phase.
            beep(220 + (state["frame"] // 15 % 8) * 55, 0.3)
        state["frame"] += 1
        if state["frame"] >= cfg("frames", GAME_FRAMES):
            if ph == PHASE_GAME:
                state["stats"] = _stats_of(state["dts"])
                state["phase"] = PHASE_GAME_SND
                state["frame"] = 0
                state["warm"] = 5
            else:
                state["stats_snd"] = _stats_of(state["dts_snd"])
                state["phase"] = PHASE_DONE


def _draw():
    ph = state["phase"]
    if ph == PHASE_MICRO:
        # the batch IS this frame's drawing (deliberately heavy)
        _measure_one()
        name = (state["verbs"][state["vi"]][0]
                if state["vi"] < len(state["verbs"]) else "")
        rect(0, 226, 320, 14, 0)
        print("BENCH MICRO " + name + " k=" + str(state["k"]), 8, 229, 7)
    elif ph == PHASE_GAME or ph == PHASE_GAME_SND:
        _game_scene(state["frame"])
        if ph == PHASE_GAME_SND:
            print("+ SOUND", 250, 226, 10)
    else:
        _report()


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
    for label, s in (("SILENT", st), ("SOUND ", state["stats_snd"])):
        if s is None:
            continue
        print(label + " n=" + str(s["n"]) + " fps=" + _f1(s["fps"]), 8, y, 11)
        y += 12
        print("  p50=" + _f1(s["p50"]) + " p90=" + _f1(s["p90"])
              + " p99=" + _f1(s["p99"]) + " worst=" + _f1(s["worst"]), 8, y, 7)
        y += 12
    y += 4
    print("HOLD BACK TO EXIT", 8, y, 6)
    if not state["reported"]:
        state["reported"] = True
        _serial_report()


def _f1(v):
    return str(int(v * 10) / 10.0)


def _serial_report():
    # One machine-readable block; harmless if nobody is listening.
    for name, k, best, med, mx in state["micro"]:
        _p("BENCHCART verb=" + name + " k=" + str(k) + " best_ms=" + str(best)
           + " med_ms=" + str(med) + " max_ms=" + str(mx))
    for label, s in (("silent", state["stats"]), ("sound", state["stats_snd"])):
        if s is None:
            continue
        _p("BENCHCART phase=" + label + " n=" + str(s["n"])
           + " p50=" + _f1(s["p50"]) + " p90=" + _f1(s["p90"])
           + " p99=" + _f1(s["p99"]) + " worst=" + _f1(s["worst"])
           + " fps=" + _f1(s["fps"]))


def _p(line):
    # The cart's print() is the CANVAS verb; reach Python's real print for the
    # serial line. MicroPython does not inject __builtins__ into an exec'd
    # namespace (the first Bench run's report never hit serial), but
    # __import__ resolves via the builtins fallback on both VMs.
    try:
        __import__("builtins").print(line)
    except Exception:
        pass
