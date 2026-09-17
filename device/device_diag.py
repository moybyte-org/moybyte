"""Serial diagnostics for the device desktop loop.

Pure logging functions (#43/#63/#66/#68/#69), every one of them called by the
T-Deck's run_desktop between frames when perf capture is on: _diag_flush (ring
-> SD), _diag_hitch (HITCH), _diag_drawbrk / _diag_draw2 (the draw-cost
splits), _diag_loop (the average frame by stage), _diag_pump (bounce-feed
pacing), _diag_i2cstat (#69 kbd/touch I2C latency) and _diag_webhost (the web
console's socket). The T-Deck is the only board that stages this module.

Every one takes its inputs explicitly (diag / ws / comp / keyboard / touch) and
logs via the passed `diag` handle -- no shared class state -- so they import
only the leaf device_util tick helpers. Device-only module (modules/,
auto-frozen); no moy_runtime cycle.
"""
from device_util import _ticks_ms, _ticks_diff


def _diag_flush(diag, ws):
    """Flush the diag RAM ring to /sd/moybyte/diag.log via the workstation's live
    SD session wrapper (with_sd_live). Guarded: a flush failure is a no-op so it
    can never crash the loop. Skips the write when SD management is disabled (the
    embedded-carts fallback, where carts_root is None -> no writable SD root).
    Returns the elapsed ms (0 if skipped/failed) so callers get their _t_sd
    timing for free instead of each wrapping their own _t0/_ticks_diff pair."""
    if diag is None:
        return 0
    t0 = _ticks_ms()
    try:
        if not getattr(ws, "can_manage", False):
            return 0
        with_sd = getattr(ws, "_with_sd", None)
        diag.flush_to_sd(with_sd)
    except Exception:
        return 0
    return _ticks_diff(_ticks_ms(), t0)


# The PERF sample is not a diag helper (#206 item 2): it rides
# device_boot.PerfSampler on the shared FrameLoop.account hook, in the one
# format every board emits. This board still PERSISTS it -- run_desktop's emit
# rings the finished line -- but it no longer composes a second one.


HITCH_MS = 80


def _diag_hitch(diag, ws, comp, elapsed, kbd_ms, inp_ms, sb_ms, ws_ms,
                diag_ms, sd_ms, web_ms, hi_ms=-1, hp_ms=-1):
    """Log a HITCH line (#66): one frame blew past HITCH_MS. Names every measured
    loop stage: kbd (I2C keyboard poll), inp (trackball + touch + pointer), sb
    (canvas.sync_back = buffer repoint + GDMA layer kick, unmeasured until v3),
    ws (input handlers + ws.frame), diag sample, diag SD write, web poll. v3
    prints the RAW phase split of the hitch frame itself (the v2 EMAs hid which
    phase a single 150ms spike lived in: alpha=0.15 moves an EMA only 15% of the
    spike), plus pump= (the bounce-flush band feeding, ms this frame) and lw=
    (cumulative copy_wait trips). If nothing named sums to the spike, the pause
    is an implicit GC collect (alloc-triggered, invisible to stage timers)."""
    try:
        s = ws.perf_sample()
        raw = None
        get_raw = getattr(ws, "perf_breakdown_raw", None)
        if get_raw is not None:
            raw = get_raw()
        pump_ms = getattr(comp, "pump_last_us", 0) / 1000.0
        trips = getattr(getattr(ws, "canvas", None), "_lcopy_trips", -1)
        # Launcher-frame section split (#66 instrument-before-cutting): the
        # home layer stashes (wallpaper, shelf grid, bar) ms under perf_capture
        # -- DRAWBRK is cart-gated, so this is the one split a launcher hitch
        # gets.
        home = getattr(ws, "_pf_home", None)
        home_s = (" home(wp=%d grid=%d bar=%d)" % home) if home else ""
        # ws= lumps handle_input + handle_pointer + ws.frame, which is one lump too
        # coarse for #183: a 37s stall showed ws=37156 with raw(...) summing to 20ms,
        # so "somewhere in the ws step" was as far as it could be narrowed. Split the
        # three (the loop already measures them) -- ws(hi/hp/frm) says which.
        ws_s = " ws(hi=%d hp=%d frm=%d)" % (hi_ms, hp_ms, ws_ms - hi_ms - hp_ms) \
            if hi_ms >= 0 else ""
        # #184: hp= is itself one lump, and a 1.7-1.9s pointer stall lives inside
        # it with no named stage -- the same shape ws= had before #183. Split it
        # on the spike frame: pre = pointer bookkeeping before the routing walk,
        # worst@id = the dearest single layer.handle_pointer in the walk, claim =
        # the layer that consumed the tap, n = layers visited. If tot is far
        # BELOW hp=, the time is outside handle_pointer's body altogether.
        hp_s = ""
        pp = getattr(ws, "perf_pointer", None)
        if pp is not None and hp_ms > 0:
            q = pp()
            if q is not None:
                hp_s = (" hp(tot=%.1f pre=%.1f worst=%.1f@%s claim=%s n=%d)"
                        % (q[0], q[1], q[2], q[3], q[4], q[5]))
        if raw is not None:
            diag.log("HITCH",
                     "frame=%dms kbd=%d inp=%d sb=%d ws=%d diag=%d sdflush=%d "
                     "web=%d pump=%.1f lw=%d raw(logic=%.1f render=%.1f "
                     "audio=%.1f chrome=%.1f flush=%.1f)%s%s"
                     % (elapsed, kbd_ms, inp_ms, sb_ms, ws_ms, diag_ms, sd_ms,
                        web_ms, pump_ms, trips,
                        raw[0], raw[1], raw[2], raw[3], raw[4],
                        ws_s + hp_s, home_s))
        else:
            b = ws.perf_breakdown()
            diag.log("HITCH",
                     "frame=%dms kbd=%d inp=%d sb=%d ws=%d diag=%d sdflush=%d "
                     "web=%d flush=%.1f ema(logic=%.1f render=%.1f chrome=%.1f)"
                     % (elapsed, kbd_ms, inp_ms, sb_ms, ws_ms, diag_ms, sd_ms,
                        web_ms, (s[2] if s is not None else -1.0),
                        b[0], b[1], b[3]))
    except Exception:
        pass


def _diag_drawbrk(diag, ws):
    """Log a DRAWBRK line splitting the frame's draw cost into cart _update (game
    LOGIC) / cart _draw (RENDERING) / audio.tick / console chrome (the bar+cursor+
    overlays remainder) -- the breakdown that says where draw= goes (logic vs render
    vs audio vs chrome). Guarded -> a no-op on any failure (only meaningful while a
    cart runs)."""
    if diag is None:
        return
    try:
        if ws.perf_sample() is None:        # only while a cart is actively running
            return
        b = ws.perf_breakdown()             # (logic, render, audio, chrome) ms
        # bg= is render's declared-backdrop share (#172), printed INSIDE render
        # rather than beside it -- it is the cart's own drawing, and showing it
        # as a peer would re-create the reading that sent #172 hunting the shell.
        pb = getattr(ws, "perf_backdrop", None)
        bg_s = (" (bg=%.2f)" % pb()) if pb is not None else ""
        diag.log("DRAWBRK", "logic=%.2f render=%.2f%s audio=%.2f chrome=%.2f"
                 % (b[0], b[1], bg_s, b[2], b[3]))
        # #63 auto-batch profiling: flushes=1/maxrun=N means the cart's N-sprite loop
        # coalesced into ONE native blit_batch; flushes=N/maxrun=1 means it did NOT.
        pb = getattr(ws, "perf_batch", None)
        if pb is not None:
            bt = pb()
            diag.log("BATCH", "flushes=%d sprites=%d maxrun=%d" % (bt[0], bt[1], bt[2]))
    except Exception:
        pass


def _diag_draw2(diag, ws):
    """Log a DRAW2 line (#63): the last frame's microseconds inside the two native pixel ops
    that dominate a full-frame cart -- layer=the draw_layer window-copy (blit_window),
    batch=the sprite blit_batch. The DRAWBRK `render` EMA lumps _draw's Python + these C ops
    together; this says which native op is the real cost (e.g. is sakura's ~120ms render the
    layer copy or the 120-petal batch?). Cheap (two ticks_us reads per op); guarded."""
    if diag is None:
        return
    try:
        cv = getattr(ws, "canvas", None)
        if cv is None or ws.perf_sample() is None:
            return
        # #66: map/text/fill joined so the WHOLE render ms attributes to named C
        # ops -- (DRAWBRK render) - (these) = Python dispatch + circ/line/pix.
        #
        # ...except fill and text READ ZERO for any cart whose rect/print reach
        # the #155 native gates, because a gated call never enters the Python
        # method that holds the _t_ timer. The gates have always timed
        # themselves (ST_T_FILL/ST_T_TEXT, behind ST_PROF) and DeviceCanvas has
        # always exposed gate_counts() -- nothing ever CALLED it, so the
        # measurement existed and was thrown away every frame. Zoomed celeste is
        # what made that visible: 29.6ms of render with fill=0.00ms and 20.6ms
        # in no bucket at all. Fold the gated microseconds into the bucket they
        # belong to -- `fill` means time spent filling, whichever lane did it --
        # and carry the call counts, which are the other half of the question
        # (a big fill and 300 small ones cost the same ms and want different
        # fixes).
        nf = nt = gf = gt = 0
        gc = getattr(cv, "gate_counts", None)
        if gc is not None:
            nf, nt, gf, gt = gc()
        diag.log("DRAW2", "layer=%.2fms batch=%.2fms map=%.2fms text=%.2fms "
                          "fill=%.2fms gated(fill=%d text=%d)"
                 % (getattr(cv, "_t_layer_us", 0) / 1000.0,
                    getattr(cv, "_t_batch_us", 0) / 1000.0,
                    getattr(cv, "_t_map_us", 0) / 1000.0,
                    (getattr(cv, "_t_text_us", 0) + gt) / 1000.0,
                    (getattr(cv, "_t_fill_us", 0) + gf) / 1000.0,
                    nf, nt))
    except Exception:
        pass


def _diag_loop(diag, ws, acc):
    """Log a LOOP line: the AVERAGE frame, split by loop stage, over the diag window.

    HITCH already names these stages -- but only for frames past HITCH_MS, so a
    steady-state cost that never spikes is invisible to it. The 2026-07-29 fps hunt
    needed exactly that: Sky Run's DRAWBRK summed to 12ms and flush to 3ms, yet the
    frame was 19.6ms (51fps), so ~4.6ms per frame was going somewhere no counter
    watched. `other` is that number -- frame minus every measured stage. `sleep` is
    the frame-pacing wait, which is deliberate idle, not lost time (if sleep is
    large the loop is capped, not slow).

    `acc` is the loop's accumulator list (see moy_runtime.run_desktop):
    [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep] in ms; reset by the caller."""
    if diag is None or not acc or acc[0] <= 0:
        return
    try:
        n = acc[0]
        stages = acc[2] + acc[3] + acc[4] + acc[5] + acc[6] + acc[7] + acc[8] + acc[9]
        # div= is the tick model's draw divisor (#217). It belongs on every
        # measurement line because it changes what fps MEANS -- at div=2 the
        # cart draws every second tick, so a rate read against another run's
        # is only comparable beside it. `-` while no game is paced: a frozen
        # number there would look like a lever that died. A setting that
        # silently redefines a metric has to be printed beside it.
        pl = getattr(ws, "player", None)
        div = pl.sched.div if (pl is not None and getattr(pl, "tick_ms", 0)) else None
        diag.log("LOOP",
                 "div=%s " % ("-" if div is None else div) +
                 "n=%d frame=%.1f kbd=%.1f inp=%.1f sb=%.1f ws=%.1f "
                 "(hi=%.1f hp=%.1f frm=%.1f) web=%.1f diag=%.1f sd=%.1f "
                 "sleep=%.1f other=%.1f"
                 % (n, acc[1] / n, acc[2] / n, acc[3] / n, acc[4] / n, acc[5] / n,
                    acc[10] / n, acc[11] / n, (acc[5] - acc[10] - acc[11]) / n,
                    acc[6] / n, acc[7] / n, acc[8] / n, acc[9] / n,
                    (acc[1] - stages) / n))
    except Exception:
        pass


def _diag_pump(diag, comp):
    """Log a PUMP line (#66 lever 4, measure-before-touching): the bounce-flush
    feed pacing for the last shipped frame -- pump (CPU ms inside the band feed;
    core-0 CPU since the feeder task, so not billed to the frame -- but a ZERO
    means the feeder never ran), idle (ms the SPI sat starved because every
    fired band completed before the next was fed -- the tunable waste; ~0 means
    the flush ceiling is real transfer time and band size / a third slot won't
    buy fps), gaps (how many bands were fed late), feed (kick -> last band
    queued), blocked (ms the VM core spent waiting in drain).

    `timeouts=`, `errs=` and `stopfail=` must all stay 0. `moy_flush` cannot
    RAISE a queue error hit during a drain (a drain must not throw into the
    frame loop), so `errs` is the only place such a failure is visible
    anywhere: a flush that is quietly failing looks exactly like a healthy one
    until this number moves. `stopfail=` is deinit giving up on the feeder and
    leaving the bounce slots allocated rather than freeing them under a live
    ISR -- also unraisable, for the same reason."""
    if diag is None:
        return
    try:
        if not getattr(comp, "bounce_flush", False):
            return
        st = comp.bounce_stats()
        # #190: folded flushes since boot -- nonzero proves the scale fold is
        # live (a small-canvas game frame's bands were SYNTHESIZED, the root
        # composite skipped). Steadily climbing during play = every quiet
        # frame folds; frozen = something disarms each frame.
        # BOTH banded boards have the lever (moy_fold), so a 0
        # here now means something disarms -- read it against `fold_supported`,
        # which is what a board WITHOUT the lever leaves absent.
        fold = getattr(comp, "fold_count", 0)
        # The snapshot meters (moy_fold.h): snap=DMA/memcpy snapshots since
        # boot, snapto= copies that never landed (the engine retires itself
        # on the first), snapwait= ms the last snap fence waited -- ~0 is the
        # design, and a number here means the copy outlived the loop head.
        # Absent on a board whose compositor has no snapshot, never 0.
        snap = getattr(comp, "snap_stats", None)
        tail = ""
        if snap is not None:
            ss = snap()
            tail = " snap=%d/%d snapto=%d snapwait=%.2f" % (
                ss[0], ss[1], ss[2], ss[3] / 1000.0)
        diag.log("PUMP",
                 "pump=%.2f idle=%.2f gaps=%d feed=%.2f blocked=%.2f "
                 "bands=%d fold=%d timeouts=%d errs=%d stopfail=%d%s"
                 % (st[0] / 1000.0, st[1] / 1000.0, st[2],
                    st[3] / 1000.0, st[5] / 1000.0, st[4], fold,
                    st[6], st[7], st[8], tail))
    except Exception:
        pass


def _diag_webhost(diag, ws):
    """Log a WEBHOST line: what the web console's SOCKET is actually doing.

    Written 2026-08-16 because the T-Deck's console served one page and then
    refused every connection afterwards, and there was no way to ask it why.
    The desktop stayed responsive (2.9ms frames), `web=` stayed 0.0 in LOOP,
    ICMP answered at 289-391ms, and TCP was REFUSED rather than timing out --
    which is the signature of nothing bound, not a full backlog. But `poll()`
    returns instantly both when `sock is None` and when there is simply no
    traffic, so `web=0.0` could not distinguish "dead" from "idle", and this
    board has no serial RX under the desktop to ask with.

    The P4 runs this same shared code and does not do it, so the cause is
    environmental rather than logical -- the S3's on-die WLAN shares internal
    RAM with the LCD DMA (the documented ESP_ERR_NO_MEM coexistence hazard),
    where the P4's radio is a separate C6 over SDIO.

    So: print the state, not the symptom. `sock=` none means the listener is
    gone (the interesting case); `err=` carries whatever killed it; `mem=` is
    internal-SRAM free, because if the socket died of allocation that is where
    it shows. Costs one line per diag tick and only when the row is on."""
    if diag is None:
        return
    wh = getattr(ws, "webhost", None)
    if wh is None:
        return
    try:
        sock = getattr(wh, "sock", None)
        # ALWAYS print, including when the row is off. The first version
        # returned early in exactly that case, and then "is it off, or is the
        # diagnostic not running?" became the question the diagnostic existed to
        # answer -- silence reads as "nothing to report" and cost a round trip.
        # INTERNAL SRAM, not the GC heap. The first version of this line printed
        # `gc.mem_free()` and reported 6045k while nothing could connect -- that
        # is the MicroPython heap in PSRAM, which is not the pool lwIP and the
        # WLAN stack allocate from, so it was reassuring and irrelevant. The
        # documented S3 hazard is precisely internal-RAM contention between the
        # WLAN stack and the LCD DMA (ESP_ERR_NO_MEM / 257, which is why WiFi is
        # not brought up at boot on this board), and a listening TCP PCB comes
        # out of that same internal pool. ICMP does not, which is exactly the
        # shape observed: pings answered, every TCP port refused.
        try:
            import esp32 as _esp32
            free = sum(r[1] for r in _esp32.idf_heap_info(_esp32.HEAP_DATA))
        except Exception:
            try:
                import gc as _gc
                free = _gc.mem_free()
            except Exception:
                free = -1
        url = ""
        try:
            url = wh.url() or ""
        except Exception:
            url = "?"
        diag.log("WEBHOST",
                 "sock=%s serving=%s err=%s url=%s sram=%dk"
                 % ("none" if sock is None else "open",
                    bool(getattr(wh, "serving", False)),
                    getattr(wh, "error", None) or "-",
                    url or "-", free // 1024))
    except Exception:
        pass


def _diag_i2cstat(diag, keyboard, touch):
    """Log an I2CSTAT line (#69): per-session I2C latency stats for the two
    peripherals sharing I2C0 -- the keyboard C3 and the GT911 touch. n=reads,
    max=worst transaction (kbd: with the mode it happened in), >5/>20=stalls past
    those ms. A 5-byte read at 400kHz is ~135us nominal, so ms-scale maxima mean
    clock-stretching/contention -- this line sizes the 13-60ms kbd= HITCH spikes
    (which only surface inside >80ms frames) across a whole session."""
    if diag is None:
        return
    try:
        # kbd to= counts CAPPED stalls (#69: reads that raised at I2C_TIMEOUT_US and
        # were held over as one stale frame) -- they never complete, so they are NOT
        # in n=/max=. Touch failures ARE timed (its _stat runs on the except path).
        # #74: the one-shot first-big-stall fingerprint -- boot ms, the transaction
        # phase that ate it (status/point/clear), the status byte (None = the status
        # read itself stalled/failed), and how many reads preceded it. Answers the
        # issue's "boot wake or steady state, and WHERE inside read_raw".
        fb = getattr(touch, "stat_first_big", None)
        first = ("" if fb is None
                 else " tfirst(t=%dms %s st=%s n=%d)" % (fb[0], fb[1], fb[2], fb[3]))
        # #74 INT-gate verdict fields: int= GT911 INT edges observed (stuck at 0
        # all session = the line never fired -> miswired/mispolarized, the gate
        # never engaged and polling stayed blind); skip= passes the gate saved
        # (climbing skip + quiet touch maxima is the fix working).
        diag.log("I2CSTAT",
                 "kbd(n=%d max=%.1fms%s >5=%d >20=%d to=%d) "
                 "touch(n=%d max=%.1fms >5=%d >20=%d int=%d skip=%d)%s"
                 % (getattr(keyboard, "stat_n", 0),
                    getattr(keyboard, "stat_max_us", 0) / 1000.0,
                    " raw" if getattr(keyboard, "stat_max_raw", False) else "",
                    getattr(keyboard, "stat_over5", 0),
                    getattr(keyboard, "stat_over20", 0),
                    getattr(keyboard, "stat_timeouts", 0),
                    getattr(touch, "stat_n", 0),
                    getattr(touch, "stat_max_us", 0) / 1000.0,
                    getattr(touch, "stat_over5", 0),
                    getattr(touch, "stat_over20", 0),
                    getattr(touch, "stat_int_edges", 0),
                    getattr(touch, "stat_skipped", 0),
                    first))
    except Exception:
        pass
