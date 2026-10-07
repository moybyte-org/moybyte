# Map (grep -n a name to jump there):
#   OtaHealth                         did the update work: the boot-side check
#   frame_slot_ms                     the cadence one frame is measured against
#   FramePump                         the frame loop's dt clock, head and tail
#   IdleBlank                         blank the backlight after a spell with no input
#   poll_webhost                      one webhost slice per frame
#   poll_link                         one radio slice per frame
#   PerfSampler                       the serial PERF line, one body for every board
#   -- #210: the frame loop's per-stage deadline meters  StageMeters, FrameLoop
#   StageMeters                       per-stage deadline accounting
#   FrameLoop                         the device frame loop's invariant order
"""The device frame loop -- ONE implementation, every board (#161, #202): the
frame half of runtime/device_boot.py, whose boot spine hands over to it.

The loop's order, its pump, the idle blank, the OTA rollback confirm, the PERF
sampler, the stage meters and the per-frame service polls. This file is what
the kernel's native loop replaces (docs/kernel_survival_2026-10.md section
7.1), so it imports no board module and nothing of the boot's: every
board-specific object arrives as an argument, exactly as device_boot's do.
"""

try:
    from chrome import _ticks_ms, _ticks_diff
    from ticks import _ticks_us, _sleep_ms
    from perf_line import FAILED as PERF_FAILED, format_perf
    from audio_session import probe_line as _audio_probe
except ImportError:  # pragma: no cover - host package lane
    from runtime.chrome import _ticks_ms, _ticks_diff
    from runtime.ticks import _ticks_us, _sleep_ms
    from runtime.perf_line import FAILED as PERF_FAILED, format_perf
    from runtime.audio_session import probe_line as _audio_probe

try:
    import moy_kernel as _kernel
except ImportError:  # every tier but a console board's
    _kernel = None

try:
    from gc import pauses as _gc_pauses
except ImportError:  # a host, or a MicroPython without tools/patch_gc_meters.py
    _gc_pauses = None


class OtaHealth:
    """"Did the update work?" -- the two halves of it (#53).

    The boot VERDICT (`boot_check`) is read on the boot path, before anything
    can overwrite the evidence. The rollback CONFIRM (`tick`) is NOT: reaching
    the end of the boot path proves only that the desktop was CONSTRUCTED, and
    an image that never paints a pixel has shipped once already (#56). So the
    confirm is fired from the frame loop, where `confirm_when_healthy` counts
    real painted frames and surviving loop iterations before it certifies the
    image. Cheap (an int compare) and self-disarming after it fires.

    `log` is the board's sink for these lines: `print` on the P4, the diag ring
    on the T-Deck, which has no serial RX to be asked afterwards.
    """

    def __init__(self, ws, log=None):
        self.ws = ws
        self.log = log or print
        # Cleared once the confirm has fired (or on a non-OTA build), so the
        # frame loop stops asking.
        self.ota = getattr(ws, "updater", None)

    def boot_check(self):
        ota = self.ota
        if ota is None:
            return
        try:
            verdict = ota.boot_check()
            if verdict:
                self.log("last update %s (%s)" % verdict)
                self.ws.announce_update()   # and say so on the desktop, not just here
        except Exception as exc:  # noqa: BLE001 -- never block the desktop
            self.log("boot_check failed: %s" % (exc,))

    def tick(self):
        ota = self.ota
        if ota is None:
            return
        try:
            if ota.confirm_when_healthy(getattr(self.ws, "_frames_drawn", 0)):
                self.log("marked app valid (slot %s)" % ota.slot())
            if ota.confirmed:
                self.ota = None       # fired (or a non-OTA build): stop asking
        except Exception as exc:  # noqa: BLE001 -- never break a frame over this
            self.log("confirm failed: %s" % (exc,))
            self.ota = None


def frame_slot_ms(ws, floor_ms):
    """The cadence ONE frame is measured against, in ms: the running cart's
    tick period while the Player paces a game (#217 -- 33 at the 30 the
    console guarantees, 16 for a manifest `"fps": 60`), else the loop's own
    cap; never FASTER than the cap the board booted with.

    One author, two readers. `StageMeters` cuts its budgets out of it, so a
    cart that halves the cadence doubles every stage's allowance instead of
    turning the whole loop into a permanent miss; `FramePump.pace` sleeps a
    frame into it -- except under a paced game, where it does not sleep at
    all (see pace). Never raises -- pacing must not die of a console.
    """
    try:
        fms = ws.player.tick_ms
    except Exception:  # noqa: BLE001
        fms = 0
    return fms if fms > floor_ms else floor_ms


class FramePump:
    """The frame loop's shared head and tail: the dt clock, the once-only boot
    housekeeping, and the cadence.

    Deliberately NOT the whole loop. The middle of a frame is where the two
    boards genuinely diverge -- trackball + poller thread + SRAM-bounce flush on
    one, BLE HID + serial dev channel + async-PPA `present_pending` on the other
    -- and flattening that into a hook-per-line abstraction would hide real
    hardware behind a false shared shape. What IS shared is the arithmetic
    around it, which is exactly where the asymmetry had grown.
    """

    def __init__(self, boot, ota=None, fps_cap=60):
        self.boot = boot
        self.ota = ota
        self.frame_ms = 1000 // fps_cap
        # The slot pace() last put a frame into (frame_slot_ms above). Published
        # because it is the cadence the whole frame is measured against: #210's
        # stage budgets are shares of it, and deriving them a second time is how
        # a budget and the pacing it judges drift apart.
        self.slot = self.frame_ms
        # Pacing debt (#77, 2026-08-10): ms the loop is BEHIND its cadence.
        # See pace() for what it buys.
        self.debt = 0
        # Sleep-overshoot slack (#202, 2026-08-17): a learned estimate of how
        # much longer time.sleep_ms actually sleeps than asked, subtracted
        # from future sleeps. Measured on the P4: FREERTOS_HZ=100 (a 10ms
        # tick, upstream MicroPython's own sdkconfig) makes every paced sleep
        # overshoot by ~4.2ms -- and a MEMORYLESS pace() pays that every
        # frame, which is exactly how a roster that runs 74fps uncapped
        # paced itself down to 48. The slack is an integer EMA fed by begin()
        # (the one place that owns the real clock), floored at 0 and capped
        # small so a hitch stays debt's business; on a platform whose sleeps
        # are exact it converges to 0 and changes nothing.
        self.slack = 0
        self._expected = 0      # what pace() scheduled the last frame to total
        self._slept = False     # ...and whether it actually asked for a sleep
        self.last = _ticks_ms()
        self._now_dt = [0, 0.0]  # begin()'s answer, reused: it runs every frame

    def begin(self):
        """Top of the loop: `[now, dt]`, with dt clamped to 0..100ms so a hitch
        (a 200ms GC, an SD write) can't teleport a cart's physics. Also the
        slack learner: the real period of the frame that just ended, compared
        against what pace() scheduled for it -- only on frames that SLEPT
        (a no-sleep frame's overrun is debt's business), with the per-sample
        error clamped so one GC pause cannot slam the estimate."""
        now = _ticks_ms()
        real = _ticks_diff(now, self.last)
        if self._slept:
            over = real - self._expected
            # A saturating +-1ms/frame walker, not an integer EMA (whose
            # floor-division stalls 1-3ms under the true overshoot): converges
            # in a handful of frames and then dithers +-1ms around it, which
            # at a 16ms budget is fps noise. Clamps keep a hitch from slamming
            # it; debt owns real overruns.
            if over > 0 and self.slack < 8:
                self.slack += 1
            elif over < 0 and self.slack > 0:
                self.slack -= 1
        dt = max(0.0, min(0.1, real / 1000.0))
        self.last = now
        out = self._now_dt
        out[0] = now
        out[1] = dt
        return out

    def tail(self, ws):
        """The once-only frame housekeeping both boards run after `ws.frame()`:
        the splash hand-over + timing report, and the OTA rollback confirm.

        Called AFTER the board's own backlight gate, which stays board-side --
        the P4's idle screen blank owns that panel light too, and its
        `not _asleep` guard is a real difference, not an oversight.
        """
        self.boot.first_frame(ws)
        if self.ota is not None:
            self.ota.tick()

    def pace(self, ws, elapsed):
        """How long to sleep after a frame that took `elapsed` ms. Pure integer
        arithmetic on an INJECTED elapsed -- never a clock -- so a test can walk
        an exact trajectory (same rule as ui.ScrollRegion's physics).

        A PACED GAME DOES NOT SLEEP (#217, owner call 2026-09-01: these boards
        draw about the same power idle as loaded, so a cap buys nothing). The
        Player's scheduler places logic ticks on the cart's own clock, and a
        sleep here would quantize them onto an integer-ms grid -- 33 against a
        33.33 period is a tick dropped every few seconds, and the P4's 10ms
        FreeRTOS tick makes any sleep at all a 10ms one. The loop spins on
        cheap no-tick frames instead and a tick lands within one of them.
        The slot is still published, because it is the cadence #210's budgets
        are cut from.

        Console screens and tools keep the loop's own fps_cap so the pointer
        stays responsive. Re-read every iteration: it changes on cart open/exit.

        THE DEBT (#77, 2026-08-10, learned on zoomed celeste). A per-frame clamp
        can only slow FAST frames, so a cart whose full frame overruns the 33ms
        budget produced 50 + 33-padded pairs = 83ms under frameskip -- the game
        20% slow (audio still ahead) at 12fps, worse on both axes than no skip
        at all. An over-budget frame now accrues debt that the following frames'
        sleeps pay down, so the PAIR totals two budget slots (50 + 16 = 66ms).
        Capped at one pair so a real hitch (a 200ms GC) doesn't eat the sleeps
        for a second afterwards. Inert while frames fit their budget.
        """
        fms = self.slot = frame_slot_ms(ws, self.frame_ms)
        try:
            paced = bool(ws.player.tick_ms)
        except Exception:  # noqa: BLE001
            paced = False
        if paced:
            self.debt = 0
            self._expected = elapsed
            self._slept = False
            return 0
        if elapsed < fms:
            sleep = fms - elapsed
            if self.debt:                       # pay the debt out of this sleep
                take = sleep if sleep < self.debt else self.debt
                sleep -= take
                self.debt -= take
            cut = 0
            if self.slack and sleep:            # #202: pre-pay the overshoot
                cut = self.slack if self.slack < sleep else sleep
                sleep -= cut
            self._expected = fms                # the slot this frame should total
            # A frame whose sleep the slack CUT (even to zero) stays learnable:
            # if the cut was too deep the real period lands under the slot,
            # over goes negative and the walker steps back down. Marking only
            # sleep>0 frames froze the walker at its ceiling the moment it
            # swallowed a whole sleep -- measured as the roster sailing PAST
            # the cap (73fps under a 60 cap) with slack stuck at 8.
            self._slept = sleep > 0 or cut > 0
            return sleep
        self._expected = elapsed
        self._slept = False
        self.debt += elapsed - fms
        if self.debt > 2 * fms:
            self.debt = 2 * fms                 # unpayable: just run flat out
        return 0


class IdleBlank:
    """Blank the panel backlight after a spell with no input, restore it on the next.

    ONE implementation for both boards. The P4 shipped this first (#58) and the
    T-Deck grew a hand-rolled second copy on 2026-08-16 that got three things
    wrong -- all three are behaviours, not details, and all three are why this is
    shared rather than re-typed per board:

      1. The touch that WAKES the screen must not also press what it landed on,
         or a wake tap launches a cart.
      2. `ws._dirty` has to be set on wake. The panel may still hold a frame from
         before the blank and the partial-paint machinery will happily leave it
         there.
      3. An EXPLICIT blank has to outrank activity. `power off` arrives on the
         serial channel, which is itself activity, so without this it wakes again
         in the very same iteration.

    Drives the backlight and nothing else: the board keeps RENDERING while dark,
    so an unattended bench run still produces the frames it is measuring.

    `set_backlight` is injected because the two boards reach their panel
    differently, and `ws` is passed per-tick rather than held so this owns no
    console reference.
    """

    def __init__(self, set_backlight, timeout_ms=300000):
        self._set = set_backlight
        self.timeout_ms = timeout_ms   # 0 disables
        self.asleep = False
        self.force = False             # an explicit blank is pending
        self._idle_at = 0

    def wake(self, now):
        self._idle_at = now
        return self._resume() if self.asleep else False

    def _resume(self):
        self.asleep = False
        self._set(True)
        return True

    def blank(self):
        """Ask for a blank at the next tick, outranking that tick's activity."""
        self.force = True

    def tick(self, now, active, ws, pointer=None, click=False):
        """Returns the (possibly cleared) click for this frame.

        Call after EVERY input source has been read and before the pointer is
        handed to the console -- that ordering is what lets the waking touch be
        swallowed.
        """
        if self.force:
            self.force = False
            self._idle_at = now
            if not self.asleep:
                self.asleep = True
                self._set(False)
            ws._psave_asleep = True
            return click
        if active:
            self._idle_at = now
            if self.asleep:
                self._resume()
                ws._psave_asleep = False
                ws._dirty = True            # (2)
                if pointer is not None:     # (1)
                    pointer.down = False
                return False
            return click
        if (self.timeout_ms and not self.asleep
                and _ticks_diff(now, self._idle_at) >= self.timeout_ms):
            self.asleep = True
            self._set(False)
            ws._psave_asleep = True
            # Say so. The panel going dark is indistinguishable from a hang or a
            # dead backlight otherwise, and this is the one event here nobody
            # can see happen.
            print("Moybyte power save: screen off (idle %ds)"
                  % (self.timeout_ms // 1000))
        return click


def poll_webhost(ws):
    """One non-blocking webhost accept/serve per frame (plan 3.4 pull half).

    Costs a poll on a non-blocking listener when nobody is connected, and a
    whole asset transfer when someone is -- which is why the boards call this
    at the frame TAIL, outside every timing bracket: a browser loading the
    console will visibly stall the desktop, and that is the honest behaviour
    for a single-threaded board. Never breaks a frame. Returns elapsed ms
    (0 when idle/absent) so the T-Deck's HITCH line can carry web=."""
    wh = getattr(ws, "webhost", None)
    if wh is None:
        return 0
    # `closing` as well as `serving`: a host saying goodbye (moy_webhost.stop's
    # grace window) has already gone `serving = False` so the Settings row and
    # the glass follow the kid's tap at once, and its socket outlives that by a
    # few seconds purely to tell the browser this was deliberate. Polling only
    # on `serving` would leave nobody to answer, which is the bug the window
    # exists to fix.
    if not (getattr(wh, "serving", False) or getattr(wh, "closing", None)):
        return 0
    t0 = _ticks_ms()
    try:
        wh.poll()
    except Exception as exc:  # noqa: BLE001 -- never break a frame
        print("WEB ERR %s: %s" % (type(exc).__name__, exc))
    return _ticks_diff(_ticks_ms(), t0)


def poll_link(ws):
    """One radio slice per frame, at the frame TAIL beside the webhost poll.

    At 30Hz an input frame carries ~2 messages and the ring holds hundreds, so
    a per-frame slice is comfortable -- and draining on the frame loop is what
    keeps ESP-NOW off a thread fighting the panel flush for the VM core. A
    no-op while the link is inert, which is every frame nobody is playing
    together, and on a board that built no link at all."""
    lk = getattr(ws, "link", None)
    if lk is not None and lk.active:
        lk.poll(ws)


class PerfSampler:
    """The serial PERF line, ONE body, every board (#206 item 2).

    WHY. The line's shape is a contract two host tools parse, and it had THREE
    producers with three different shapes: the P4's, the Guition's copy of it
    (which said so in its own comment), and the T-Deck's, which went through the
    offline diag ring in a fourth field order entirely. `runtime/perf_line.py`
    owns the format; this owns the MEASUREMENT, and `FrameLoop.account` -- which
    runs after pace and exists for frame accounting -- is where both copies
    already sat.

    ONE FIELD SET, FROM ONE ACCOUNTING PATH (owner call 2026-08-28). Every value
    below is read here, once, for every board: the loop's own frame/busy/drawn
    accumulators and the shared Workstation meters. A board does not choose its
    fields; it either HAS a lever or it does not, and one it does not have
    prints `-`. The only per-board argument is `overlap`, because the async-PPA
    counters exist on exactly one compositor:

      overlap  the P4's `comp.overlap_stats` -- a cumulative 7-tuple. Passed
               RAW: the delta, the index-to-field mapping and the us->ms divisor
               are the FORMAT's business and live here, so a second board with
               an overlap engine hands over the same shape and says nothing
               about layout. None (the S3 boards) leaves ppa/fence_ms/gfence_ms
               reading `-`.

    The windowed WM columns need no argument at all: `wm_windowed` stamps
    `_pf_wm_*` on the Workstation and a board that does not stage it never has
    them, so `getattr(..., None)` IS the capability probe. Same for the
    launcher's `_pf_home`. A board with no lever reports None, never 0 -- the
    2026-08-22 doctrine, which `fold=0` cost weeks by breaking.

    Every read is inside the guard and OUTSIDE the frame `try`: this hook runs
    after pace, so while these reads were bare a rename in the shared
    `runtime/console.py` dropped the P4 to the REPL about two seconds after boot
    -- a measurement killing the loop it measures. A failure PRINTS, and the
    timer resets either way, so a broken sample cannot become a per-frame retry
    flooding the serial it is measured over.

    `pauses` is the collector's meter, `gc.pauses()` on a board whose build
    takes tools/patch_gc_meters.py: cumulative (collections, pause us), and the
    longest pause since the previous read, which the read resets -- so this is
    its one reader. None (a host) leaves gc= reading `-`, and so does the first
    line after the diag comes on, which has no baseline.

    `emit` is the sink. The P4 and the Guition print; the T-Deck prints AND
    rings the same line for its offline SD log, because that board's serial was
    unreadable for months and the ring is why anything was known about it.

    THE LINE IS PERF DIAG'S (owner call 2026-09-30). With Settings -> PERF DIAG
    off -- kid mode, the default -- nothing periodic is formatted, printed or
    ringed on any board, because every line is garbage the collector comes back
    for in a stop-the-world pass. Whatever reads the line turns the diag on for
    its measurement and puts it back (`--diag` on `tools/p4_perf.py` and
    `tools/board.py perf`, `tests/on_glass.py`'s `perf_diag`); the tools'
    default, the shipping fps, reads `ws._frames_drawn` -- the counter `fps=`
    is taken from -- with the diag off. The window still closes every period
    while it is off, so the first line after it comes on is a whole period of
    its own; the PPA deltas, whose baseline is only read under the diag, print
    `-` in that one line.

    The boot-time arm (`ws.perf_capture = bool(getattr(ws, "diag_live",
    False))`) stays in each board's `run_desktop`: it is a service assignment on
    the boot path, which is what `tests/test_board_service_parity.py` reads. The
    LIVE re-sync is here, so flipping Settings -> PERF DIAG needs no reboot --
    for the capture meters and for the kernel's AUDIORATE line, which is
    printed beside PERF while the diag is on and never otherwise.
    """

    def __init__(self, ws, overlap=None, period_ms=2000, emit=print,
                 pauses=_gc_pauses):
        self.ws = ws
        self._overlap = overlap
        self._pauses = pauses
        self._gcp = None
        self._period = period_ms
        self._emit = emit
        # Whole seconds per sample: fps= and the loop count are RATES.
        self._secs = max(1, period_ms // 1000)
        self._at = _ticks_ms() + period_ms
        self._n = 0
        self._busy = 0
        self._drawn = 0
        self._miss = 0
        self._sched = None    # WHOSE misses _miss is a baseline for
        self._ov = overlap() if overlap is not None else None
        # The sample's values, REUSED: every field is written on every sample
        # (None where nothing measured it), so one dict and its two pairs serve
        # every line the board prints for as long as it is on.
        self._v = {}
        self._fps = [0, 0]
        self._tick = [0, 0]
        self._gc = [0, 0, 0]

    def _take(self, name):
        """Read one windowed-WM meter and CLEAR it: it says what THIS sample
        measured, and only a frame that drew that layer may answer.

        `wm_windowed` stamps these from inside its layers, and a FULLSCREEN cart
        runs a different WM entirely -- so the layer stops writing the moment a
        cart opens while the attribute keeps its last desktop value forever. Read
        bare, the column then prints a live-looking number for a body that has not
        run in minutes: on 2026-09-11 both P4s reported `wmw=46` under every cart,
        identical across carts whose whole frame differed by 8x, and it was taken
        for a fixed window-manager tax before the layer split showed the WM was
        not in the stack at all. Absence is the honest reading and the line can
        already say it.

        The clear is conditional so a board that never had the attribute never
        grows one -- `tests/test_console_facade.py` holds those three names to
        ABSENT on a host console, which is the same doctrine one level up.
        """
        v = getattr(self.ws, name, None)
        if v is not None:
            setattr(self.ws, name, None)
        return v

    def account(self, now, elapsed, sleep_ms):
        """The `FrameLoop.account` hook: accumulate, and once a period follow
        PERF DIAG and emit the line when it is on."""
        self._n += 1
        self._busy += elapsed
        if _ticks_diff(_ticks_ms(), self._at) < 0:
            return
        ws = self.ws
        drawn = getattr(ws, "_frames_drawn", 0)
        try:
            live = bool(getattr(ws, "diag_live", False))
            if ws.perf_capture != live:
                ws.perf_capture = live
            if live:
                self._sample(ws, drawn)
                line = _audio_probe()
                if line:
                    self._emit(line)
            else:
                self._ov = None          # re-read when the diag comes back
                self._gcp = None
                self._baseline_misses(ws)
        except Exception as exc:  # noqa: BLE001 -- a diag never kills the loop
            self._emit(PERF_FAILED % (type(exc).__name__, exc))
        self._at = _ticks_ms() + self._period
        self._n = 0
        self._busy = 0
        self._drawn = drawn

    def _baseline_misses(self, ws):
        """The tick model's miss baseline, kept current whether or not a line
        is printed, so the first line after the diag comes on counts its own
        window. `-` while nothing is paced. Returns the misses this window, or
        None."""
        pl = getattr(ws, "player", None)
        if pl is None or not pl.tick_ms:
            self._sched = None
            self._miss = 0
            return None
        sc = pl.sched
        # The baseline belongs to THAT scheduler. Every cart start builds a
        # new one counting from 0, so subtracting the previous cart's total
        # reported a NEGATIVE miss in the first sample of each run
        # (`tick=60/1 miss=-424`, on glass) whenever no sample landed at the
        # launcher in between -- which is what a `run` straight after an
        # `exit` does.
        if sc is not self._sched:
            self._sched = sc
            self._miss = 0
        n = sc.misses - self._miss
        self._miss = sc.misses
        return n

    def _sample(self, ws, drawn):
        """One PERF line from this window's accumulators and the meters."""
        cart = getattr(ws, "cart", None)
        v = self._v
        fps = self._fps
        fps[0] = (drawn - self._drawn) // self._secs
        fps[1] = self._n // self._secs
        v["cart"] = cart.get("title") if cart else None
        v["fps"] = fps
        v["busy"] = self._busy // (self._n or 1)
        v["draw"] = getattr(ws, "_draw_ms", 0)
        v["flush"] = getattr(ws, "_flush_ms", 0)
        v["logic"] = getattr(ws, "_upd_ms", 0)
        v["render"] = getattr(ws, "_cart_ms", 0)
        v["chrome"] = getattr(ws, "_chrome_ms", 0)
        # No windowed WM on this board, or the deep meters are off, or the WM
        # did not run this window: either way nothing measured them, which is
        # not a zero. TAKEN, not read -- see _take.
        v["wmr"] = self._take("_pf_wm_restore")
        v["wmw"] = self._take("_pf_wm_windows")
        v["wms"] = self._take("_pf_wm_stamp")
        v["home"] = getattr(ws, "_pf_home", None)
        v["gc"] = None
        if self._pauses is not None:
            cur = self._pauses()
            prev = self._gcp
            self._gcp = cur
            if prev is not None:
                g = self._gc
                g[0] = (cur[0] - prev[0]) & 0xFFFFFFFF
                g[1] = (cur[1] - prev[1]) & 0xFFFFFFFF
                g[2] = cur[2]
                v["gc"] = g
        v["ppa"] = v["fence_ms"] = v["gfence_ms"] = None
        v["tick"] = None
        if self._overlap is not None:
            # DELTAS over this sample (the counters are cumulative), and
            # gfence_ms otherwise hides entirely: the game fence runs inside
            # FrameLoop's UNTIMED present() hook, so it lands in busy= and in
            # no phase meter. The timeout count must stay 0.
            cur = self._overlap()
            prev = self._ov
            self._ov = cur
            if prev is not None:
                # A slot a compositor cannot measure is None the whole way
                # through: a 0 would read as a count this board never took.
                d = [None if (a is None or b is None) else a - b
                     for a, b in zip(cur, prev)]
                v["ppa"] = (d[0], d[1], d[2], d[4], d[6])
                v["fence_ms"] = None if d[3] is None else d[3] / 1000.0
                v["gfence_ms"] = None if d[5] is None else d[5] / 1000.0
        # LAST, and BARE where every field beside it is a getattr: perf_net
        # CONSUMES its window, and `-` is a legitimate reading here, so a
        # getattr default would let a renamed meter forge "no match" forever.
        # A rename costs the whole line and says so.
        v["net"] = ws.perf_net()
        # The tick model (#217): the rate the cart's logic holds, the draw
        # divisor it holds it at, and the frames this sample wrote debt off in
        # -- `-` while nothing is paced, never a frozen 0.
        v["miss"] = self._baseline_misses(ws)
        if v["miss"] is not None:
            sc = self._sched
            tick = self._tick
            tick[0] = sc.rate
            tick[1] = sc.div
            v["tick"] = tick
        self._emit(format_perf(v))


# -- #210: the frame loop's per-stage deadline meters -------------------------
#
# THE BUDGETS, and this tuple is the only copy of them. A stage's allowance is
# a share of the PACING SLOT in per-mille, never a fixed microsecond count: the
# slot is 16ms while the desktop runs at the loop cap and the cart's tick under
# a paced game (frame_slot_ms above), and a budget cut from a constant would read as a
# permanent miss on one of those two. They sum to 1000 -- the frame's own 780
# plus 220 of overhead is the STATEMENT: four fifths of every slot is supposed
# to reach the glass.
#
# `None` is a DECLARATION, not a gap. `tail` is where poll_webhost runs, and a
# browser pulling the console bundle owns the frame it lands in -- the honest
# behaviour of a single-threaded board, written down in poll_webhost's own
# docstring -- so there is no deadline to miss and the stage says so.
#
# Boards do not get their own copy. Where the loop genuinely differs per board
# it differs by which HOOKS exist, and a stage no hook fills is never sampled:
# it reports None rather than a budget nothing ever measured against.
STAGE_BUDGETS = (
    ("inputs", 60),         # every input source: trackball, keyboard, GT911
    ("dev", 20),            # the serial dev channel's byte-at-a-time read
    ("idle", 5),            # the idle blank's arithmetic
    ("pointer", 10),        # click latch + pointer.tick
    ("present", 30),        # pre-frame buffer work (sync_back, present_pending)
    ("frame", 780),         # handle_input + handle_pointer + draw/composite/flush
    ("backlight", 5),       # the one-shot first-frame gate, and its fence
    ("pump_tail", 30),      # boot.first_frame + the OTA rollback confirm
    ("tail", None),         # webhost/diag/SD services -- see above
    ("pace", 10),           # the cadence arithmetic, never the sleep
    ("account", 50),        # HITCH/LOOP accumulators + the PERF sample
)

STAGE_ORDER = tuple(name for name, _share in STAGE_BUDGETS)

# Where StageMeters halves a rolling sum and its count (see the class). Under
# the boards' REPR_C build a small int is 30 bits, so this sits two bits below
# the boundary a per-frame accumulator must never cross.
_MEAN_CAP = 1 << 28

# Index constants for the mark sites in step(), derived from the table so a
# stage cannot be added without one. tests/test_device_boot.py pins that the
# loop marks them in exactly STAGE_ORDER.
(_S_INPUTS, _S_DEV, _S_IDLE, _S_POINTER, _S_PRESENT, _S_FRAME, _S_BACKLIGHT,
 _S_PUMP_TAIL, _S_TAIL, _S_PACE, _S_ACCOUNT) = range(len(STAGE_BUDGETS))


class StageMeters:
    """Per-stage deadline accounting for the shared frame loop (#210).

    WHY. Attribution used to be detective work: the frame was measured in
    aggregate (PERF/HITCH/DRAWBRK/CHROMEBRK/PUMP), and when a number moved
    somebody picked a suspect and hand-added a probe for it. This turns "the
    frame was slow" into "THIS stage was slow" with no hunt, because the order
    is already an invariant with one author.

    `misses` is the field that matters. A high-water mark alone is noise on a
    console that GCs; a COUNT of frames over a declared budget is a statement
    about intent, and it survives being read once a minute over serial.

    NOTHING HERE IS 0 BY DEFAULT. A stage that was never sampled -- a hook this
    board does not have, or a console with perf_capture off -- reports None for
    every measured field, and a stage with no declared budget reports None for
    `misses` however many frames it saw. A frozen 0 is also what a broken meter
    looks like, and that ambiguity is what hid `fold=0` for weeks.

    `avg_us` is what ATTRIBUTION reads, and the meter shipped without it: last
    is one arbitrary frame and max is the worst GC of the run, so neither
    answers "where does the frame GO". A rolling sum and its count do, for one
    add and one compare more per mark. The sum HALVES itself with its count at
    `_MEAN_CAP` rather than growing forever -- an accumulator that walks past
    the 30-bit small int allocates a bignum on every frame, which is a meter
    that pays for itself in exactly the pathology it exists to find. The
    halving also makes the mean a WINDOW, the more useful reading on a console
    whose cadence changes with the cart.

    COST. Seven preallocated integer lists, indexed; `mark` is a ticks_us pair,
    five list stores and three compares, and allocates nothing on any frame. The
    loop only calls it under `perf_capture`, so kid mode pays one attribute read
    and eleven `is not None` tests a frame and never reads the clock.
    """

    def __init__(self, ws, floor_ms=1000 // 60):
        self.floor_ms = floor_ms
        n = len(STAGE_BUDGETS)
        self.budget = [None] * n
        self.last = [0] * n
        self.max = [0] * n
        self.misses = [0] * n
        self.n = [0] * n
        # The rolling mean's pair. `n` stays the LIFETIME count `misses` is
        # only readable against, so these are separate lists rather than a
        # reuse: halving the miss denominator would misreport the misses.
        self.total = [0] * n
        self.seen = [0] * n
        self.slot_ms = 0
        self._t = 0
        self._skip = False    # see reset(): the frame that reset is not a sample
        self.rebudget(frame_slot_ms(ws, floor_ms))

    def rebudget(self, slot_ms):
        """Re-cut every declared budget out of a new pacing slot."""
        self.slot_ms = slot_ms
        us = slot_ms * 1000
        i = 0
        for _name, share in STAGE_BUDGETS:
            self.budget[i] = None if share is None else us * share // 1000
            i += 1

    def reset(self):
        """Drop every sample, AND the rest of the frame that asked for the drop.
        Called at cart start AND at cart exit, so a run's numbers never carry the
        launcher's and the launcher's never carry the run's -- and the budgets
        re-cut themselves on the next measured frame, because the pacing slot
        changes at exactly those two moments.

        THE FRAME THAT RESETS IS NOT A FRAME OF THE RUN. It is the one that read
        the cart off the card, built the machine and drew the first screen, and
        its remaining stages land in the window this call just cleared -- one
        sample of hundreds, and hundreds of milliseconds. It poisons exactly the
        field the class tells you to attribute with: on 2026-09-11 the Guition
        read `dev` at 12.9ms a loop under moss moss against 1.6ms under Star
        Catcher, which is the same one-off launch divided by each cart's frame
        count, and it reads as a per-frame cost that scales with the cart. What
        said otherwise was `misses` -- 2 frames of 174 -- which is why that is
        the field this class puts first.
        """
        i = 0
        n = len(self.n)
        while i < n:
            self.last[i] = 0
            self.max[i] = 0
            self.misses[i] = 0
            self.n[i] = 0
            self.total[i] = 0
            self.seen[i] = 0
            i += 1
        self._skip = True

    def start(self, slot_ms):
        """Top of a measured frame: re-cut the budgets if the cadence moved,
        then stamp the clock the first stage is measured from. Also ends a
        reset's skip -- THIS frame is a frame of the new run."""
        if slot_ms != self.slot_ms:
            self.rebudget(slot_ms)
        self._skip = False
        self._t = _ticks_us()

    def mark(self, i):
        """Close stage `i` at the current clock and open the next one. A no-op
        for the remainder of a frame that called reset()."""
        if self._skip:
            return
        t = _ticks_us()
        us = _ticks_diff(t, self._t)
        self._t = t
        self.last[i] = us
        if us > self.max[i]:
            self.max[i] = us
        self.n[i] += 1
        t = self.total[i] + us
        k = self.seen[i] + 1
        if t > _MEAN_CAP:
            t >>= 1
            k >>= 1
        self.total[i] = t
        self.seen[i] = k
        b = self.budget[i]
        if b is not None and us > b:
            self.misses[i] += 1

    def report(self):
        """`{stage: {budget_us, avg_us, last_us, max_us, misses, n}}` for the
        `state` blob. Built on demand, never on a frame. `n` is the denominator
        the miss count is only readable against: three misses in thirty frames
        and three in thirty thousand are opposite findings. `avg_us` is the
        rolling mean -- the field that ATTRIBUTES a frame, because `last_us` is
        one arbitrary frame and `max_us` is the run's worst GC."""
        out = {}
        i = 0
        for name, _share in STAGE_BUDGETS:
            b = self.budget[i]
            seen = self.n[i]
            k = self.seen[i]
            out[name] = {
                "budget_us": b,
                "avg_us": self.total[i] // k if k else None,
                "last_us": self.last[i] if seen else None,
                "max_us": self.max[i] if seen else None,
                "misses": self.misses[i] if (seen and b is not None) else None,
                "n": seen,
            }
            i += 1
        return out


class FrameLoop:
    """The device frame loop's INVARIANT ORDER, one copy for every board
    (#202 Phase B -- the extraction #161 declined while the loop middles were
    still large and the T-Deck had no on-glass harness; both premises expired
    on 2026-08-17).

    What this class owns is exactly the ordering whose per-board copies are
    where this repo's worst bugs have lived (#56 was an order bug; so was
    PURR's F13, quoted in #161):

      pump.begin -> poll_inputs (EVERY input source) -> dev channel ->
      idle.tick (the wake-swallow needs all inputs read first) ->
      pointer.click/tick -> present (pre-frame buffer work: the P4's
      present_pending must precede sync_back, which re-points at the freed
      buffer) -> ws.handle_input/handle_pointer/frame -> the first-frame
      backlight gate -> pump.tail -> tail -> pace -> account -> sleep.

    Boards supply the hooks; everything hardware stays theirs:

      poll_inputs(now) -> (click, active)  read every input source, feed the
                          pointer's place/down/fresh. `active` is the idle
                          blank's wake condition MINUS the dev channel (the
                          loop adds `ran` itself).
      present()         pre-frame buffer work (sync_back and friends), or None.
      frame_error(exc)  the board's crash note (default: print). Runs for any
                          Exception; KeyboardInterrupt always propagates (the
                          Ctrl-C -> shell -> REPL contract).
      tail(now)         per-frame services after pump.tail (webhost poll, diag
                          ticks, SD flush cadence), counted INSIDE the frame's
                          elapsed, or None.
      account(now, elapsed, sleep_ms)  frame accounting after pace (HITCH,
                          LOOP accumulators, PERF samplers), or None.

    The loop also exposes the per-frame ws-phase splits every board's
    diagnostics want (t_hi/t_hp/t_ws -- handle_input/handle_pointer/frame ms)
    and the drew/frames_before pair the T-Deck's SD bracket and idle-band
    drain read. run() returns "quit" when the dev channel asked for the REPL;
    the board prints its own goodbye.

    Every stage above is METERED against a declared budget (#210): `self.meters`
    is a StageMeters, stamped on the console as `ws.stage_meters` so the dev
    channel's `state` can dump it and the Player can reset it per run. A stage
    whose hook this board does not have is never marked, which is what makes
    "no such stage here" read as None instead of as a stage that cost nothing.
    """

    def __init__(self, ws, pump, pointer, poll_inputs,
                 idle=None, serial=None, present=None, tail=None,
                 account=None, frame_error=None,
                 set_backlight=None, lit=False):
        self.ws = ws
        self.pump = pump
        self.pointer = pointer
        self.poll_inputs = poll_inputs
        self.idle = idle
        self.serial = serial
        self.present = present
        self.tail = tail
        self.account = account
        self.frame_error = frame_error
        self.set_backlight = set_backlight
        self._lit = lit
        self.t_hi = 0            # ws.handle_input ms, this frame
        self.t_hp = 0            # ws.handle_pointer ms
        self.t_ws = 0            # the whole ws phase (input+pointer+frame) ms
        self.frames_before = 0   # _frames_drawn entering the ws phase
        self.drew = False        # did this frame reach the glass
        # #210. Built here rather than injected per board: the stages ARE this
        # class's order, so a board cannot own a different set of them.
        self.meters = StageMeters(ws, getattr(pump, "frame_ms", 1000 // 60))
        ws.stage_meters = self.meters
        # The kernel's task watchdog (native/moy_kernel, #160): a frame that
        # ends feeds it, a frame that never does panics the board into a crash
        # record. None on every tier but a console board's.
        self._feed = getattr(_kernel, "feed", None)

    def step(self):
        """One frame. Returns "quit" when the dev channel asked for the REPL,
        else None. Split from run() so a test can drive single frames."""
        ws = self.ws
        pointer = self.pointer
        now, dt = self.pump.begin()
        # #210: gated exactly like every other measurement in this loop. `m` is
        # None for the whole frame when it is off, so kid mode never reads the
        # microsecond clock -- and the slot comes from pace(), the one author of
        # the cadence the budgets are cut from.
        m = self.meters
        if getattr(ws, "perf_capture", False):
            m.start(getattr(self.pump, "slot", m.floor_ms))
        else:
            m = None
        click, active = self.poll_inputs(now)
        if m is not None:
            m.mark(_S_INPUTS)
        ran = False
        if self.serial is not None:
            ran = self.serial.poll(ws)
            click = self.serial.click or click
            if self.serial.quit:
                return "quit"
            if m is not None:
                m.mark(_S_DEV)
        if self.idle is not None:
            # After EVERY input source (poll_inputs + the dev channel) and
            # before the pointer reaches the console -- the ordering that lets
            # the waking touch be swallowed instead of pressing what it landed
            # on. A dev command or scripted gesture frame counts as activity.
            click = self.idle.tick(now, bool(active) or ran, ws, pointer, click)
            if m is not None:
                m.mark(_S_IDLE)
        pointer.click = click
        pointer.tick(now)
        self.frames_before = getattr(ws, "_frames_drawn", 0)
        if m is not None:
            m.mark(_S_POINTER)
        if self.present is not None:
            self.present()
            if m is not None:
                m.mark(_S_PRESENT)
        t0 = _ticks_ms()
        self.t_hi = 0
        self.t_hp = 0
        try:
            ws.handle_input()
            self.t_hi = _ticks_diff(_ticks_ms(), t0)
            ws.handle_pointer()
            self.t_hp = _ticks_diff(_ticks_ms(), t0) - self.t_hi
            ws.frame(dt)         # draw + composite + flush
        except KeyboardInterrupt:
            raise                # Ctrl-C -> shell -> REPL, never swallowed
        except Exception as exc:  # noqa: BLE001 -- one bad frame must not brick it
            if self.frame_error is not None:
                self.frame_error(exc)
            else:
                print("Moybyte frame error:", exc)
        self.t_ws = _ticks_diff(_ticks_ms(), t0)
        self.drew = getattr(ws, "_frames_drawn", 0) != self.frames_before
        if m is not None:
            m.mark(_S_FRAME)
        # First composed frame lights the panel (#45): _frames_drawn ticks past
        # 0 only inside frame() after the flush, so the first sight is the
        # desktop, not power-on GRAM noise. `not idle.asleep` keeps the gate
        # from re-lighting a deliberately blanked panel (the boards keep
        # RENDERING while dark).
        if not self._lit and (self.idle is None or not self.idle.asleep) \
                and getattr(ws, "_frames_drawn", 0) > 0:
            # The same FENCE DeviceBoot.note's gate takes, for the same #45
            # reason: on a banded backend flush() returns with most of the frame
            # still going out, so lighting here would light power-on GRAM noise.
            # Reached whenever the splash never lit the panel.
            _sync = getattr(getattr(ws, "comp", None), "sync", None)
            try:
                if _sync is not None:
                    _sync()
                if self.set_backlight is not None:
                    self.set_backlight(True)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte backlight on failed:", exc)
            self._lit = True
        if m is not None:
            m.mark(_S_BACKLIGHT)
        self.pump.tail(ws)
        if m is not None:
            m.mark(_S_PUMP_TAIL)
        if self.tail is not None:
            self.tail(now)
            if m is not None:
                m.mark(_S_TAIL)
        elapsed = _ticks_diff(_ticks_ms(), now)
        sleep_ms = self.pump.pace(ws, elapsed)
        if m is not None:
            m.mark(_S_PACE)
        if self.account is not None:
            self.account(now, elapsed, sleep_ms)
            if m is not None:
                m.mark(_S_ACCOUNT)
        if self._feed is not None:
            self._feed()
        if sleep_ms:
            _sleep_ms(sleep_ms)
        return None

    def run(self):
        try:
            while True:
                if self.step() == "quit":
                    return "quit"
        finally:
            rest = getattr(_kernel, "rest", None)
            if rest is not None:
                rest()          # a loop that ended is not a hang: the REPL is never watched
