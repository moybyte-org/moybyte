"""`BandedCompositor` -- the compositor both banded-panel boards run.

ONE body with two thin subclasses: `tdeck_panel.TDeckCompositor` over
`native/moy_lcd` (ST7789, esp_lcd over SPI) and `guition_panel.GuitionCompositor`
over `native/moy_axs` (AXS15231B, raw QSPI with the whole frame under one CS).
It is the Python half of what `native/moy_flush` is in C: the TRANSPORT is
per-board and stays per-board; the frame machine over it is not.

Promoted here 2026-08-21 under Phase C's rule -- a driver moves into the shared
`device/` tree the day a SECOND board carries the hardware, and not one day
earlier (#206 item 1, `docs/board_ports_2026-08.md`). Note which way the
promotion ran, because it is the same story `moy_flush` tells one tier down:
the trigger was not a third board but these two CONVERGING. Until `d9aa73e`
the T-Deck fed its bands from a 2 ms `machine.Timer` on the VM core and this
class could not have existed; that commit moved its feed onto the Guition's
core-0 task, and what had been two files became twelve identical methods and
two constructors that differ by an import.

WHAT A BACKEND MUST BE. The small interface the shared console reaches a
backend through -- the one `DeviceCanvas.__init__` and `moy_runtime.run_desktop`
actually call, and the one `docs/surface_model_v1.md` §4 pins:

    size()          -> (w, h)
    framebuffer()   -> the buffer to draw into THIS frame
    back_buffer()   -> alias of framebuffer(), named for the ping-pong call site
    gfx()           -> the native moy_gfx kernel, or None
    flush()         -> present what was drawn
    sync()          -> guarantee no panel DMA is in flight

Nothing new is invented here (`docs/backend_contract_v1.md` L8: strategy stays
the backend's, and a new backend implements the contract rather than a new
mechanism). The P4's `p4_display.P4Compositor` is deliberately NOT a subclass:
its DSI peripheral scans a PSRAM framebuffer continuously, so it has no bands,
no bounce slots and no flush at all -- which is also why its `board.toml`
denies `moy_flush` and does not stage this module.

THE FLUSH OVERLAPS THE NEXT FRAME'S RENDER (#66/#43).
  A full frame is 150-300 KB over a serial bus -- 15-20 ms on either board's
  glass -- and paid synchronously it caps the loop before anything is drawn.
  (Each subclass's module docstring carries its own arithmetic and its own
  measured before/after; the numbers are board facts and do not belong here.)
  So `flush()` is three steps:

      1. drain the PREVIOUS frame's bands (mostly already done -- the render
         that just ran is what hid them),
      2. swap the ping-pong so the buffer just drawn becomes the FRONT,
      3. kick it, and RETURN, so the CPU renders the next frame while the
         panel reads.

  `flush()` therefore costs the drain RESIDUE plus the kick, not the transfer.
  The residue is what the console's `flush=` reports, and it is the number that
  should fall. The front buffer is immutable while it ships -- that is what the
  ping-pong is for -- so the bands are tear-free by construction.

WHO FEEDS THE REST: the panel module's CORE-0 FEEDER task (`moy_flush`'s, on
  both boards since 2026-08-21), woken per band by the SPI done-ISR while
  MicroPython's task stays pinned to core 1. So this compositor has NO pump
  timer and does NOT export `pump_if_pending`: the 2 ms soft-timer feeder and
  `DeviceCanvas`'s every-N-ops draw pokes are retired on both boards (they fed
  a pump that no longer needs the VM core; each panel module's `pump` survives
  as a no-op for the verb-set shape). `DeviceCanvas` getattrs `pump_if_pending`,
  finds nothing, and skips its pokes; `moy_gfx`'s `set_pump` is never armed.

  What the timer's retirement also bought, and the reason not to re-introduce a
  VM-side pump when tuning band size: under the timer a band had to transfer
  for LONGER than the pump period or the SPI starved between fires, which is
  what pinned the T-Deck's bands at 48 rows (32-row bands measured 53.9 -> 51.8
  fps on Brick Siege, 2026-08-21). With the feeder there is no such floor.

WHAT EACH SUBCLASS OWNS, and why the list is short: its native module (imported
  in its own `__init__` and passed in here, so the staging-closure check can
  still SEE which board depends on which C module), its `WIDTH`/`HEIGHT`, its
  `ASYNC_FLUSH` revert flag, its module-level `set_backlight()`, and its
  board-only levers -- the T-Deck's `LAYER_COPY_ASYNC` and `sd_bracket`, the
  Guition's `fold_supported` + the three `*_fold` verbs.
"""


class BandedCompositor:
    """RGB565 framebuffer(s) in PSRAM, shipped band by band by a native module.

    Subclass it, import your panel module, hand it to `__init__`. The module
    must export `init(nfbs=)`, `WIDTH`, `HEIGHT`, `fb(i)`, `nfbs()`, `show(i)`,
    `drain()`, `backlight(on)`, `stats()`, `pump_stats()` and -- for the
    overlap -- `kick(i)`. `moy_lcd` and `moy_axs` agree on that verb set on
    purpose; it is the half of the port that made this class possible.
    """

    def __init__(self, lcd, nfbs=2, async_flush=True):
        self._lcd = lcd
        # Dark until the first composed frame (#45): a freshly-powered panel's
        # GRAM is noise, so every panel module's init() leaves the backlight
        # off for exactly this reason. run_desktop (or a smoke) lights it after
        # the first flush.
        lcd.init(nfbs=nfbs)
        self._w = lcd.WIDTH
        self._h = lcd.HEIGHT
        # Cache the memoryviews ONCE. back_buffer() is called every frame and on
        # every layer bind; re-creating a memoryview per call would allocate on
        # the hot path for no reason.
        self._fbs = [lcd.fb(i) for i in range(lcd.nfbs())]
        self._back = 0
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None

        # The overlap needs BOTH a panel module that can split the flush and two
        # distinct buffers to ping-pong between: with one framebuffer the DMA
        # would read exactly what the next frame draws into.
        self._async = bool(async_flush) and len(self._fbs) > 1 \
            and hasattr(lcd, "kick")
        # `bounce_flush` is what device_diag._diag_pump gates the PUMP line on.
        self.bounce_flush = self._async

    # -- the contract --------------------------------------------------------

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._fbs[self._back]

    def back_buffer(self):
        return self._fbs[self._back]

    def gfx(self):
        return self._gfx

    def has_gfx(self):
        return self._gfx is not None

    def flush(self):
        lcd = self._lcd
        if not self._async:
            lcd.show(self._back)
            self._swap()
            return
        # 1. Finish the previous frame. Most of it already happened behind the
        #    render that just ran, so this is the residue -- and it is what the
        #    console times as `flush=`.
        lcd.drain()
        # 2. The buffer just drawn becomes the FRONT; drawing moves to the other,
        #    which the drain above just made safe to overwrite.
        front = self._back
        self._swap()
        # 3. Hand the frame to the panel module's core-0 feeder and return.
        lcd.kick(front)

    def _swap(self):
        # Ping-pong so the next frame is drawn into a buffer the panel is not
        # reading. This is what DeviceCanvas.sync_back re-points at, and what
        # DeviceCanvas.RETAINED_FRAMES = 2 is calibrated against.
        n = len(self._fbs)
        if n > 1:
            self._back = (self._back + 1) % n

    def sync(self):
        """Leave NO panel transfer in flight.

        The fence the backlight gate and the idle-band drain take, and the one
        anything reaching around the console must take first.

        On a board whose panel SHARES its bus this is load-bearing rather than
        hygiene: the T-Deck's SD card sits on the same SPI host, and an SD op
        overlapping an in-flight panel DMA is the documented way to hang that
        board -- `run_desktop`'s session wrapper calls this before every
        session. Since the core-0 feeder that is no longer SUFFICIENT there,
        because a paint DURING a session queues bands from the other core; see
        `TDeckCompositor.sd_bracket`, which is the stronger guarantee. Nothing
        else is known to share the Guition's QSPI host, so there this is the
        drain and the fence only.
        """
        self._lcd.drain()

    # -- diagnostics (#66 lever 4) -------------------------------------------

    @property
    def pump_last_us(self):
        """us spent feeding bands during the last shipped frame (HITCH's
        `pump=`). Since the core-0 feeder this is CORE-0 CPU, not billed to
        the frame; it stays reported because a zero means the feeder never
        ran. A property because device_diag reads it as an attribute."""
        return self._lcd.pump_stats()[0] if self._async else 0

    def bounce_stats(self):
        """The whole `moy_flush` pump tuple, all eight fields:

            (pump_us, idle_us, idle_n, feed_us, bands,
             blocked_us, timeouts, errs)

        idle_us is the one to read for PACING: time the SPI sat starved because
        a band was fed only after the previous one had already finished. ~0
        means the flush ceiling is real transfer time; a big number means the
        feeder is being preempted (a core-0 radio burst) or the slot count is
        the wall. blocked_us is the CPU the VM core spent waiting in drain.

        timeouts and errs must both stay 0. `moy_flush` cannot RAISE a queue
        error hit during a drain -- a drain must not throw into the frame loop
        -- so these counters are the only place such a failure is visible at
        all: a flush that is quietly failing looks healthy from every other
        angle.
        """
        if not self._async:
            # Serialized fallback: show() blocks, so there is no feed to pace
            # and no drain to block in, and it raises its failures instead of
            # banking them. feed_us keeps its -1 "never measured" sentinel.
            return (0, 0, 0, -1, 0, 0, 0, 0)
        st = self._lcd.pump_stats()
        return (st[0], st[1], st[2], st[3], st[4], st[5], st[6], st[7])

    # -- board bits ----------------------------------------------------------

    def set_backlight(self, on=True):
        self._lcd.backlight(on)

    def stats(self):
        """(flushes, last_flush_us) -- the WALL span of the last frame's
        transfer, kick to fully out. It does not shrink when the overlap lands;
        what shrinks is the share of it the CPU waits for (bounce_stats, and the
        console's own `flush=`)."""
        return self._lcd.stats()
