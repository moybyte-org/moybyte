# Map (grep -n a name to jump there):
#   PlayerDiag             the Player's diagnostics: its exit lines, the SRAM report, the run diag
#   PlayerDiag.sram_report the internal-SRAM headroom the cart run had
#   PlayerDiag._diag_frag  the heap the next cart inherits (MEMX)
#   _heap_stats            (free, alloc) of the MicroPython heap
"""The Player's diagnostics (#66, #210, #211): the lines a run prints at its
start, its exit and an error in measurement mode, the SRAM report the dev
channel's `state` carries, and the frame loop's stage meters reset at both
ends of a run.

`PlayerDiag` is the Player's FIRST base and holds no per-frame method: a
run's frame path (`tick`, `_maybe_diag_slow_logic` and what they call each
frame) stays on Player itself, so its lookups never reach here; these run
at a start, an exit or a report. MicroPython-safe.
"""

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms
# The kernel's frame loop (native/moy_kernel), on a tier whose frames are the
# kernel's; host CPython drives its own frames.
try:
    import moy_loop as _loop
except ImportError:
    _loop = None


def _safe_len(obj):
    try:
        return len(obj)
    except Exception:  # noqa: BLE001
        return -1


def _heap_stats():
    """Return (free, alloc) for MicroPython, (-1, -1) on host/unsupported builds."""
    try:
        import gc
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        return free, alloc
    except Exception:  # noqa: BLE001
        return -1, -1


def _moycore_sram():
    """#211: `{sram_free_min, psram_fallback, floor}` for the Lua run in the
    allocator's meters, or None where the concept does not apply.

    None is the honest answer on a tier whose Lua allocator has one region to
    choose from -- the host, the wasm head, a board with no PSRAM -- and it is
    NOT the same answer as a zero: a run that fitted with nothing to spare and a
    run that could never have been squeezed must stay distinguishable. Same
    rule as the pump/fold meters in dev_channel.

    `sram_free_min` is itself None until the run's Lua allocates something big
    enough to reach the floor test, which is where moycore samples it.
    """
    try:
        import moycore
    except ImportError:
        return None
    fn = getattr(moycore, "sram_report", None)
    if fn is None:
        return None
    try:
        r = fn()
    except Exception:  # noqa: BLE001 -- a meter never blocks an exit
        return None
    if not r:
        return None
    return {"sram_free_min": r[0], "psram_fallback": bool(r[1]), "floor": r[2]}


class PlayerDiag:
    """The Player's diagnostics, mixed in as its first base (see above)."""

    def _reset_stage_meters(self):
        """#210: the frame loop's per-stage deadline meters start clean at both
        ends of a run. A run's misses must not carry the shell's and the
        shell's must not carry the run's -- and these are also the two moments
        the pacing slot the budgets are cut from changes, because frame_slot_ms
        follows the open cart's tick."""
        if _loop is not None:
            _loop.meters_reset()

    def sram_report(self):
        """#211: the internal-SRAM headroom the cart RUN had, or None.

        Live while a Lua run is open, the ended run's once it is gone, and None
        for a Python cart (no such allocator) or a tier with one region. The dev
        channel's `state` is the route -- it is the one every board serves.
        """
        if self._lua is not None:
            return _moycore_sram()
        return self._sram_run

    def _diag_sram(self):
        """One line per Lua cart exit: what the run had, and whether it tipped.

        `fallback=1` is the whole point -- a cart that outgrows the floor does
        not fail and does not warn, it starts allocating from PSRAM and gets
        about twice as slow (#67), which reads as "the cart got slower" with no
        cause attached. Nothing is printed for a run with no such allocator,
        because a line of `none`s at every Python cart's exit is not a report.
        """
        if not self._diag_enabled():
            return
        try:
            r = self._sram_run
            if not r:
                return
            lo = r["sram_free_min"]
            print("Moybyte %d SRAM exit=%d min=%s fallback=%d floor=%d"
                  % (_ticks_ms(), self._run_seq,
                     "none" if lo is None else lo,
                     1 if r["psram_fallback"] else 0, r["floor"]))
        except Exception:  # noqa: BLE001 -- a diag never blocks an exit
            pass

    def _diag_frag(self, tag="MEMX"):
        """One line per cart exit: the largest allocatable block and total free
        on the heap the next cart inherits -- #66's repeat-run fragmentation as
        a number instead of a feel. MicroPython's GC does not compact, so freed
        bytes pile into holes and the largest CONTIGUOUS run -- what a layer or
        the p8 machine's 64KB must fit -- shrinks over a session even while
        total free does not. Emitted after release_world's collect, so it reads
        the compact-as-it-gets state, and carries `exit=` (the run counter) so
        the series over repeated opens IS the degradation curve.

        The block is PROBED, which #66 line 1125 prescribes for a reason:
        `mem_free()` reads high on these ports (it folds in a potential PSRAM
        split), so only a probe that actually allocates is honest. Probed by
        DOUBLING from 16KB up to the 8MB cap, stopping at the first refusal:
        the answer is a x2 bucket, which is what the line is read for (does
        the p8 machine's 64KB still fit, does a layer) -- and at most ONE
        probe fails. That matters because a failing probe IS a collect (the
        allocator collects and retries before it raises): the binary search
        this replaced failed about twelve times per exit, 5.1 seconds of MEMX
        on a Guition P4 whose collect costs 430ms (2026-09-09). Runs after
        release_world's own collect, so it takes none of its own. Off unless
        measurement mode is on, like every line here."""
        if not self._diag_enabled():
            return
        try:
            import gc
            free = gc.mem_free()
            cap = min(free if free > 0 else (1 << 20), 8 << 20)
            big = 0
            n = 16 * 1024
            while n <= cap:
                try:
                    bytearray(n)            # unnamed: collectable at once
                except MemoryError:
                    break
                big = n
                n *= 2
            # The internal-SRAM pool the p8 machine, the flush bounce and the
            # framebuffers compete in -- regions under 1MB. Device only; -1 off it.
            int_free = int_big = -1
            try:
                import esp32
                int_free = int_big = 0
                for reg in esp32.idf_heap_info(esp32.HEAP_DATA):
                    if reg[0] < 1024 * 1024:
                        int_free += reg[1]
                        if reg[2] > int_big:
                            int_big = reg[2]
            except Exception:  # noqa: BLE001 -- host / no esp32: leave -1
                pass
            print("Moybyte %d %s exit=%d free=%dk big=%dk int=%dk/%dk"
                  % (_ticks_ms(), tag, self._run_seq, free // 1024, big // 1024,
                     int_free // 1024, int_big // 1024))
        except Exception:  # noqa: BLE001 -- a diag never blocks an exit
            pass

    def _diag_enabled(self):
        """Only emit the extra lifecycle/profiling lines in measurement mode."""
        try:
            return bool(getattr(self.ws, "diag_live", False) or getattr(self.ws, "perf_hud", False))
        except Exception:  # noqa: BLE001
            return False

    def _audio_diag(self):
        """The kernel's audio as the run found it: the output's state, the
        focused session's voices, the mix's verb count."""
        try:
            from audio_session import native
        except ImportError:
            from runtime.audio_session import native
        try:
            na = native()
            if na is None:
                return "audio=none"
            st = na.stats()
            return ("audio(out=%d mask=%d verbs=%d under=%d)"
                    % (na.out()[0], na.active(), st[3], st[2]))
        except Exception:  # noqa: BLE001
            return "audio=err"

    def _cart_state_diag(self):
        """Small generic namespace snapshot. The Sakura fields are intentional:
        they prove whether the repeated-open drop is cart-state growth or runtime
        overhead while staying harmless for every other cart."""
        ns = self.ns or {}
        petals = _safe_len(ns.get("petals"))
        sin = _safe_len(ns.get("SIN"))
        lay = 1 if ns.get("lay") is not None else 0
        return "ns=%d petals=%d sin=%d lay=%d native=%d" % (
            _safe_len(ns), petals, sin, lay, 1 if self._native_ins else 0)

    def _print_run_diag(self, tag, extra=""):
        if not self._diag_enabled():
            return
        try:
            cart = self.ws.cart or {}
            name = str(cart.get("title") or cart.get("path") or "?")
            typ = str(cart.get("type") or "?")
            d = self._start_diag
            if d is None:
                phases = ""
            else:
                phases = (" phases(reclaim=%d audio=%d api=%d compile=%d exec=%d "
                          "init=%d total=%d) heap(free=%d->%d alloc=%d->%d)"
                          % (d[0], d[1], d[2], d[3], d[4], d[5], d[6],
                             d[7], d[8], d[9], d[10]))
            if self._native_fail:
                phases += " nfail=%s" % str(self._native_fail).replace(" ", "_")
            if extra:
                extra = " " + extra
            print("Moybyte %d %s cart=%s type=%s run=%d %s %s%s%s"
                  % (_ticks_ms(), tag, name.replace(" ", "_"), typ, self._run_seq,
                     self._cart_state_diag(), self._audio_diag(), phases, extra))
        except Exception:  # noqa: BLE001 -- diagnostics must never affect play
            pass
