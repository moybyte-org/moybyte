"""Serial diagnostics for the device desktop loop (extracted from moy_runtime.py).

A set of pure logging functions (#43/#63/#66/#68/#69) the run_desktop loop calls
between frames when perf capture is on: _diag_flush (ring -> SD), _diag_perf_sample
(PERF), _diag_hitch (HITCH), _diag_drawbrk / _diag_draw2 / _diag_chromebrk (the
draw-cost splits), _diag_pump (bounce-feed pacing), _diag_i2cstat (#69 kbd/touch
I2C latency), _diag_calib (interpreter cost model), _diag_gc (the forced-collect
sample). Every one takes its inputs explicitly (diag / ws / comp / keyboard /
touch) and logs via the passed `diag` handle -- no shared class state -- so they
lift out cleanly and import only the leaf device_util tick helpers (+ local gc /
array imports). Device-only module (modules/, auto-frozen); no moy_runtime cycle.
"""
from device_util import _ticks_ms, _ticks_us, _ticks_diff


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


def _diag_perf_sample(diag, ws):
    """Log a PERF sample line from the workstation's current frame numbers, if a
    cart is actively running. Guarded -> a no-op on any failure."""
    if diag is None:
        return
    try:
        sample = ws.perf_sample()      # (name, fps, flush_ms, draw_ms) or None
        if sample is not None:
            diag.log_perf(sample[0], sample[1], sample[2], sample[3])
    except Exception:
        pass


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
        # -- DRAWBRK/CHROMEBRK are cart-gated, so this is the one split a
        # launcher hitch gets.
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
    LOGIC) / cart _draw (RENDERING) / audio.tick / console chrome (the dock+cursor+
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


def _diag_draw3(diag, ws):
    """Log a DRAW3 line: the REST of the render ms, and what's left after it.

    DRAW2's five buckets never covered the whole render slice -- its own comment
    called the leftover "Python dispatch + circ/line/pix", which is a guess, and
    on the 2026-07-29 fps regression hunt that guess was 3.6ms of a 9.2ms Sky Run
    render. So: spr = the per-sprite blit565 path (every spr that did NOT coalesce
    into blit_batch -- DRAW2 batch=0.00 with sprites on screen means ALL of them),
    shape = circ/line, img = paint-image blits, n= their call counts. resid is
    render minus every named bucket: what's genuinely interpreter dispatch.

    Read it as: a big `spr` says the pixel work moved, a big `resid` with flat
    counts says dispatch got dearer, and a jump in `n` says something started
    calling more often. Guarded; only meaningful while a cart runs."""
    if diag is None:
        return
    try:
        cv = getattr(ws, "canvas", None)
        if cv is None or ws.perf_sample() is None:
            return
        named = (getattr(cv, "_t_layer_us", 0) + getattr(cv, "_t_batch_us", 0)
                 + getattr(cv, "_t_map_us", 0) + getattr(cv, "_t_text_us", 0)
                 + getattr(cv, "_t_fill_us", 0) + getattr(cv, "_t_spr_us", 0)
                 + getattr(cv, "_t_shape_us", 0) + getattr(cv, "_t_img_us", 0))
        # render is the DRAWBRK EMA and the buckets are last-frame, so resid is
        # approximate frame to frame -- it's the TREND that answers the question.
        render_ms = ws.perf_breakdown()[1]
        diag.log("DRAW3",
                 "spr=%.2fms shape=%.2fms img=%.2fms nspr=%d nshape=%d "
                 "named=%.2fms resid=%.2fms"
                 % (getattr(cv, "_t_spr_us", 0) / 1000.0,
                    getattr(cv, "_t_shape_us", 0) / 1000.0,
                    getattr(cv, "_t_img_us", 0) / 1000.0,
                    getattr(cv, "_n_spr", 0), getattr(cv, "_n_shape", 0),
                    named / 1000.0, render_ms - named / 1000.0))
    except Exception:
        pass


def _diag_luamem(diag, ws):
    """Log a LUAMEM line (#67, 2026-08-10): where the running Lua cart's heap
    LIVES -- live bytes internal SRAM vs PSRAM, the floor-denied demand, and
    the live size-class split per region (<=64/<=256/<=2048/>2048 -- small
    classes are the VM's hot objects: stack segments, table nodes). This is
    the pricing input for any structural SRAM proposal (#66: an indexed SRAM
    canvas would take ~77KB from the same pool the allocator feeds on).
    Guarded; prints only while a lua cart's VM is alive (live bytes > 0)."""
    if diag is None:
        return
    try:
        if ws.perf_sample() is None:
            return
        # Whichever runtime is holding the cart. moycore reports the same four
        # leading fields and stops there -- the size-class buckets existed to
        # CHOOSE the SRAM-first policy, and the policy is chosen; what is left
        # to watch is whether it took. A short tuple prints a short line rather
        # than nothing, which is what the old unconditional st[15] would have
        # done here.
        st = None
        try:
            import moycore
            if moycore.active():
                st = moycore.alloc_stats()
        except ImportError:
            pass
        if st is None:
            import moy_lua
            st = moy_lua.alloc_stats()
        if not st or (st[0] + st[1]) == 0:
            return
        # In-play internal-SRAM headroom rides along (#66 census): free +
        # largest block, internal regions only (>=1MB regions are PSRAM).
        int_free = int_big = 0
        try:
            import esp32
            for reg in esp32.idf_heap_info(esp32.HEAP_DATA):
                if reg[0] < 1024 * 1024:
                    int_free += reg[1]
                    if reg[2] > int_big:
                        int_big = reg[2]
        except Exception:
            pass
        k = 1024.0
        if len(st) < 16:                    # moycore's four
            diag.log("LUAMEM",
                     "sram=%.1fKB psram=%.1fKB peak=%.1fKB denied=%d "
                     "int=%d/%dk core=1"
                     % (st[0] / k, st[1] / k, st[2] / k, st[3],
                        int_free // 1024, int_big // 1024))
            return
        diag.log("LUAMEM",
                 "sram=%.1fKB psram=%.1fKB peak=%.1fKB denied=%.0fKB "
                 "sc=%.1f/%.1f/%.1f/%.1f pc=%.1f/%.1f/%.1f/%.1f n=%d/%d "
                 "int=%d/%dk"
                 % (st[0] / k, st[1] / k, st[2] / k, st[7] / k,
                    st[8] / k, st[9] / k, st[10] / k, st[11] / k,
                    st[12] / k, st[13] / k, st[14] / k, st[15] / k,
                    st[3], st[4], int_free // 1024, int_big // 1024))
    except Exception:
        pass


def _diag_chromebrk(diag, ws):
    """Log a CHROMEBRK line (#66 lever 5, instrument-before-cutting): the sub-split
    of DRAWBRK's chrome remainder -- bar (_draw_status_strip), cmp (the game->system
    viewport composite; ~0 on the 320x240 device where the canvases are one object),
    cur (the cursor layer), stk (every other layer's draw in the WM stack walk),
    other (the router: the walk itself, the surface/fold probes, _flush_batches).

    Read `other` as a real quantity now. Until 2026-08-14 it was a residual of a
    residual over six millisecond-quantized brackets, so it absorbed every term's
    rounding on top of whatever was genuinely unnamed -- on the S3 it read ~7.6ms
    with bar/cmp/cur all ~0.00, which is an instrument saying "somewhere else" as
    loudly as it can. The brackets are microseconds now and the stack walk is
    measured, so a large `other` means the ROUTER, and a large `stk` means a
    layer -- which LAYERBRK will then name.

    Says which chrome cost a trim should target. Guarded; cart-running only."""
    if diag is None:
        return
    try:
        if ws.perf_sample() is None:
            return
        pc = getattr(ws, "perf_chrome", None)
        if pc is None:
            return
        c = pc()
        if len(c) < 5:                      # a console older than the stk bucket
            diag.log("CHROMEBRK", "bar=%.2f cmp=%.2f cur=%.2f other=%.2f"
                     % (c[0], c[1], c[2], c[3]))
            return
        diag.log("CHROMEBRK", "bar=%.2f cmp=%.2f cur=%.2f stk=%.2f other=%.2f"
                 % (c[0], c[1], c[2], c[3], c[4]))
    except Exception:
        pass


def _diag_layerbrk(diag, ws):
    """Log a LAYERBRK line (#172): the last PAINTED frame's WM stack walk, split
    per layer and printed dearest first.

    CHROMEBRK's `other` IS this walk. On the 2026-07-29 T-Deck regression that
    remainder was 6.7ms of a Brick Siege frame while bar/cmp/cur all read ~0.00
    -- every named bucket saying "not me", which is as far as narrowing could
    go. This names the layer instead. Read it as: one big row = that layer's
    draw got dearer; cost spread evenly across `n` rows = the stack machinery
    itself (the draw_stack walk, the surface probes, the batch guard), not any
    one layer.

    Deliberately NOT cart-gated, unlike DRAWBRK/CHROMEBRK: the launcher and
    editor walks have no other instrument at all, and `sum` vs the frame's draw
    ms is the check on whether the walk is even where the time goes."""
    if diag is None:
        return
    try:
        pl = getattr(ws, "perf_layers", None)
        if pl is None:
            return
        rows = pl()
        if not rows:
            return
        total = 0.0
        for _, ms in rows:
            total += ms
        # Six is the whole stack on the fullscreen tiers and the dear end of a
        # windowed one; a truncated tail is named so the sum is never read as
        # covering fewer layers than it does.
        head = rows[:6]
        parts = " ".join("%s=%.2f" % (lid, ms) for lid, ms in head)
        more = "" if len(rows) == len(head) else " +%d more" % (len(rows) - len(head))
        # pre/post are the frame's unmeasured EDGES (#172) -- printed here
        # because this line is the frame's anatomy and is not cart-gated:
        # pre + sum + flush + post should account for the loop's whole `frm`.
        pe = getattr(ws, "perf_frame_edges", None)
        edge = ""
        if pe is not None:
            p0, p1 = pe()
            edge = "pre=%.2f post=%.2f " % (p0, p1)
        diag.log("LAYERBRK", "%sn=%d sum=%.2f %s%s"
                 % (edge, len(rows), total, parts, more))
    except Exception:
        pass


def _diag_homebrk(diag, ws):
    """Log a HOMEBRK line: the LAUNCHER frame's section split (wallpaper /
    shelf grid / bar ms, stashed by launcher_layer under perf_capture). The
    steady scroll-drag frames sit UNDER the HITCH threshold, so this periodic
    line is how the launcher's repaint cost gets named on-glass (DRAWBRK /
    CHROMEBRK are cart-gated). Prints only when the LAST frame actually drew
    the home screen -- silent while idle or inside a cart/app."""
    if diag is None:
        return
    try:
        home = getattr(ws, "_pf_home", None)
        if home:
            diag.log("HOMEBRK", "wp=%d grid=%d bar=%d" % home)
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
        # skip= is the #77 frameskip gate. It belongs on every measurement line
        # because it changes what fps MEANS -- with it on, logic ticks every loop
        # frame but render/composite/flush run every second one, so a cart reads
        # far higher than the same build with it off. #66's last full-roster
        # T-Deck session was dated the day frameskip shipped, and nothing in any
        # log said which way the toggle sat, so the ledger's numbers could not be
        # compared with a later run's at all. A setting that silently redefines a
        # metric has to be printed beside it.
        diag.log("LOOP",
                 "skip=%d " % (1 if getattr(ws, "frameskip", False) else 0) +
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
    feed pacing for the last shipped frame -- pump (CPU ms inside pump()), idle
    (ms the SPI sat starved because every fired band completed before the next
    was fed -- the tunable waste; ~0 means the flush ceiling is real transfer
    time and band size / pump period / a third slot won't buy fps), gaps (how
    many bands were fed late), feed (kick -> last band queued). The data that
    decides whether the Sky Run 40-46 vs ~55-60fps gap is pacing or physics."""
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
        fold = getattr(comp, "fold_count", 0)
        diag.log("PUMP", "pump=%.2f idle=%.2f gaps=%d feed=%.2f bands=%d fold=%d"
                 % (st[0] / 1000.0, st[1] / 1000.0, st[2],
                    st[3] / 1000.0, st[4], fold))
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


_CALIB_DONE = [False]


def _diag_calib(diag):
    """One-shot CALIB line (#63): the interpreter cost model measured on THIS device
    in THIS heap state -- the numbers that explain where a kid cart's frame goes.
      call4 = 4-arg Python call, small frame (stays on the C stack)
      spill = 8-arg Python call with a real body -- frame > ~11 words HEAP-ALLOCATES
              on every call; on a warm fragmented heap this is the ~1.5ms/call
              pathology that made 120-sprite kid loops collapse (the spr_gate fix)
      tup   = small tuple alloc+append (pool-sized: cheap even warm)
      arr   = 4x array('h') stores (the gate's append shape)
      flt   = float multiply-add (REPR_C: no boxing on this port)
    us per 100 ops. Run once, ~3s into the first cart, so it reflects the REAL
    runtime heap, not a fresh boot."""
    if diag is None or _CALIB_DONE[0]:
        return
    _CALIB_DONE[0] = True
    try:
        from array import array as _arr_t
        r = range(100)

        def f4(a, b, c, d):
            pass

        def f8(a, b, c, d=-1, e=1, f=0, g=1, h=1):
            q = a + b
            s = c + d
            u = e + f
            v = g + h
            return q + s + u + v

        t0 = _ticks_us()
        for i in r:
            pass
        base = _ticks_diff(_ticks_us(), t0)
        t0 = _ticks_us()
        for i in r:
            f4(1, 2, 3, 0)
        call4 = _ticks_diff(_ticks_us(), t0) - base
        t0 = _ticks_us()
        for i in r:
            f8(1, 2, 3, 0)
        spill = _ticks_diff(_ticks_us(), t0) - base
        li = []
        ap = li.append
        t0 = _ticks_us()
        for i in r:
            ap((3, 100, 60, 0))
        tup = _ticks_diff(_ticks_us(), t0) - base
        qa = _arr_t("h", bytearray(2 * 512))
        t0 = _ticks_us()
        k = 4
        for i in r:
            qa[k] = 3
            qa[k + 1] = 100
            qa[k + 2] = 60
            qa[k + 3] = 0
        arr = _ticks_diff(_ticks_us(), t0) - base
        x = 1.5
        y = 0.25
        z = 0.0
        t0 = _ticks_us()
        for i in r:
            z = x * y + 0.3
        flt = _ticks_diff(_ticks_us(), t0) - base
        diag.log("CALIB", "call4=%d spill=%d tup=%d arr=%d flt=%d us/100"
                 % (call4, spill, tup, arr, flt))
    except Exception:
        pass


_GC_BASE = [0]      # #63: last-sample gc.mem_alloc() live-set baseline, for the churn delta.
_GC_TICK = [0]      # #63: sample counter -- the forced collect runs 1-in-10, not every 3s.


def _diag_gc(diag):
    """Log a GC line (#63, the sakura ~14fps profiling): the forced-collect PAUSE (what an
    auto-GC costs when it fires mid-frame -> the render-time variance), free heap, the live
    set, and the CHURN (bytes allocated since the last sample -> the pressure that sets how
    OFTEN auto-GC fires). A high churn + a non-trivial collect = GC-bound, and the stutter is
    that collect landing at random frames.

    CADENCE: the forced collect costs ~130ms on a cart-sized live set, and gc.mem_alloc()/
    mem_free() WALK the heap (tens of ms) -- running that every 3s sample was itself a
    visible periodic hitch (the perf capture is on by default at boot). So the full
    collect+report now runs on the FIRST sample of a cart run and then 1-in-10 (~30s);
    other samples skip entirely. Never per frame."""
    if diag is None:
        return
    tick = _GC_TICK[0]
    _GC_TICK[0] = tick + 1
    if tick % 10 != 0:
        return
    try:
        import gc
        pre = gc.mem_alloc()                 # live set + garbage accumulated since last GC
        t = _ticks_ms()
        gc.collect()
        collect_ms = _ticks_diff(_ticks_ms(), t)
        free = gc.mem_free()
        live = gc.mem_alloc()                # post-collect: the retained (live) set
        churn = pre - _GC_BASE[0]            # allocated since the last sample (mod auto-GC)
        _GC_BASE[0] = live
        diag.log("GC", "collect=%dms free=%dk live=%dk churn=%dk"
                 % (collect_ms, free >> 10, live >> 10, churn >> 10))
    except Exception:
        pass
