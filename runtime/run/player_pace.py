"""The pacing half of a run and its front hand-off: the tick model a game
is armed on at its start and disarmed from at its exit (#217), the pause
menu's hold, the parking of a game something covers, the DIAG modes, and the
kernel's side of a runtime cart -- its launch in the kernel's Player
(native/moy_play), the stop the kernel may decide there, its frame bound to
the kernel and the front handed to it and taken back
(docs/history/kernel_cartpath_2026-10.md).

`Pace` is one object the Player holds (`player.pace`). What a frame reads
-- the scheduler, `tick_ms`, `n_ticks`, the latched edges, `front` -- stays
on the Player, which plans and runs the ticks itself (`frame_plan`,
`_run_ticks`): a frame calls no method through this object. The front's
two frame steps, `resume_from_front` and `front_again`, the Player binds
once (`Player.__init__`).

Canonical home is runtime/; build.sh stages a copy into the firmware modules/
tree, as it does player.py.
"""

import os_log as _os_log
import moybyte as _moybyte
import scheduler as _scheduler
import moy_play as _moy_play
import app_perms as _perms
from audio_session import native as _audio_native
from ticks import _ticks_ms, _ticks_diff, _ticks_add
# The kernel's frame loop (native/moy_kernel), on a tier whose frames are the
# kernel's: a paced game's cadence is told to it.
try:
    import moy_loop as _loop
except ImportError:                     # host CPython: the host drives its frames
    _loop = None


class Pace:
    """The pacing half of the Player `pl` (above)."""

    def __init__(self, pl):
        self.pl = pl
        self.audio_held = 0          # the session the pause took the mix from

    def arm(self, cart):
        """A GAME runs on the tick model (#217): logic at its manifest rate --
        60 opt-in, anything else the 30 the console guarantees -- and draw on
        the scheduler's divisor. A tool/app is event-driven and ticks with the
        loop it serves, so it is not paced."""
        pl = self.pl
        if pl._tool:
            return
        fps = cart.get("fps")
        if fps == "free":
            # SPEC 5's opt-out: the cart says its logic is dt-scaled and asks
            # to run with the loop -- the tool path, real dt, every loop frame
            # draws. What a board can draw is what it gets (a P4 desk ~60, the
            # Guition ~45); a frame-counting cart must never say this. The one
            # thing the tick model still owes it is stall protection: dt is
            # clamped (FREE_DT_MAX) so a radio scan slows time, never jumps it.
            pl._free = True
            return
        try:
            rate = int(fps or 30)
        except (TypeError, ValueError):
            rate = 30
        ws = pl.ws
        pl.sched.start(rate, getattr(ws, "steady", True))
        pl.sched.uncap_mode(bool(getattr(ws, "uncap", False)))
        pl.tick_ms = pl.sched.tick_ms
        # The dt a cart's tick is handed, in this VM's own float: the model
        # paces in C's single precision, the cart reads the exact period.
        pl._tick_dt = 1.0 / pl.sched.rate
        if _loop is not None:
            _loop.tick(pl.tick_ms)
        pl.keyp_latch = 0
        inp = ws.input
        pl.tick_edges = getattr(inp, "tick_edges", None)
        pl.keep_edges = getattr(inp, "keep_edges", None)

    def launch(self, cart):
        """Launch a runtime cart's run in the kernel's Player (native/moy_play)
        BEFORE its runtime loads, so the load and _init are in the run's books
        (its upcalls by class since the launch): the run's handle, or 0 where
        the image has no Player or the cart no folder. A lockstep match's
        frame is the Player's too (moy_play_lockstep)."""
        pl = self.pl
        launch = getattr(_moy_play, "launch", None)
        path = cart.get("path") if cart else None
        if launch is None or not path:
            return 0
        paced = not pl._tool and cart.get("fps") != "free"
        ws = pl.ws
        # What the kernel's stop verdict reads beside the catalogue entry:
        # the run's route is HOME (the home shelf started it), and whether the
        # cart's consent admits an unsigned module (app_perms.run_admits_unsigned),
        # which the load after a stop applies.
        flags = 0
        if getattr(ws, "home_launch", False) and not pl._tool:
            flags |= getattr(_moy_play, "HOME", 0)
        if _perms.run_admits_unsigned(ws, cart):
            flags |= getattr(_moy_play, "UNSIGNED", 0)
        try:
            return launch(path, paced, flags) if flags else launch(path, paced)
        except RuntimeError as exc:
            print("PLAY launch:", exc)
            return 0

    def music_keeps_the_vm(self, run, cart):
        """True when the kernel would stop the VM for the run and it fits
        with the VM stopped only without the music (the verdict's `media`,
        moy_play.h's KEEP_MEDIA: the media service plays on through a stop,
        so what it holds is not given back), a file's or a station's alike:
        the launch ends unrun and the shell asks first, "Stop the music to
        play <game>?", whose Stop & play stops the stream and opens the cart
        again once the service has given its memory back
        (`Notices.ask_stop_music`). The Player stays where it was:
        `declined` tells the opener not to run it, and the run's process
        ends, so the shell is current again for the dialog's answer."""
        pl = self.pl
        info_of = getattr(_moy_play, "info", None)
        info = info_of(run) if info_of is not None else None
        if info is None or len(info) < 13 or info[11] != "media":
            return False
        ws = pl.ws
        try:
            _moy_play.end(run)
        except RuntimeError:
            pass
        pl._run_handle = 0
        pl.declined = True
        _os_log.end(pl.pid, "declined")
        _moybyte.end(pl.pid)
        pl.pid = None
        ws.notices.ask_stop_music(cart)
        return True

    def stop_for(self, run, cart):
        """True when the kernel decided at the launch to stop the VM for the
        run (docs/history/kernel_cartpath_2026-10.md 5.2, the `need` policy: a VM-free
        cart that does not fit with the VM up): nothing of the cart loads here.
        The shelf's place goes to the resume record, and the loop ends at
        this frame; the kernel loads and runs the cart with no VM and starts
        the next VM, which lands on the shelf where it was left."""
        pl = self.pl
        info_of = getattr(_moy_play, "info", None)
        info = info_of(run) if info_of is not None else None
        if info is None or len(info) < 13 or info[11] != "stops":
            return False
        try:
            import moy_kernel
        except ImportError:
            return False
        stop = getattr(moy_kernel, "stop", None)
        if stop is None:
            return False
        ws = pl.ws
        rec = getattr(getattr(ws, "home", None), "resume_record", None)
        resume = getattr(moy_kernel, "resume", None)
        if rec is not None and resume is not None:
            try:
                resume(rec())
            except Exception as exc:  # noqa: BLE001 -- a lost place is not a lost run
                print("PLAY resume:", exc)
        # The VM's heap goes with the stop: every Python service TERMed now,
        # started again by the console after it (the resume record's `svc`);
        # a service on a task of its own runs on through it and that console
        # takes it back (`keep`, scheduler.take_back).
        _scheduler.SCHED.pause()
        pl._stop_grant(cart)
        print("PLAY stop: %s runs with the VM down" % (cart.get("title") or "cart"))
        pl._stopping = True
        stop()
        return True

    def bind(self, run):
        """Give the launched run's frame to the kernel's Player when its
        runtime offers it (`play_begin`): every tick of a frame then runs in C
        -- the press edges, the snapshot, the cart, its audio -- and
        `_run_ticks` makes one call. A runtime that offers it holds the run
        and ends it at its close (its loans go with it), taken or not; one
        that does not is ended here, and its frames tick through Python."""
        pl = self.pl
        pl.play = None
        if not run:
            return
        begin = getattr(pl.lua, "play_begin", None)
        if begin is None:
            _moy_play.end(run)
            return
        try:
            pl.play = begin(run, pl.sched if pl.tick_ms else None)
        except Exception as exc:  # noqa: BLE001 -- the Python frame still runs it
            print("PLAY:", exc)
            pl.play = None

    def take_front(self):
        """Hand a VM-free game's frame to the kernel (moy_play_front): from the
        next loop frame its input, ticks, chrome and present are C and the
        loop makes no console upcall until the run ends or needs the console
        (docs/history/kernel_cartpath_2026-10.md §4). Where the board's compositor
        gives no front (`ws.front_bind`), or this canvas is not the one it
        composes, the console's frame keeps driving the same Player. Where the
        WM has no rect for it yet (`play_viewport`: a game full screen on the
        desk before the glass around it is still, a window, a cart's view),
        `_front_want` stays up and the console's next frames ask again
        (`front_again`); a bind the kernel then refuses asks no more until
        the next start or resume."""
        pl = self.pl
        pl._front_want = False
        if (pl.play is None or pl._tool or pl.cart_error is not None
                or not (pl.verdict and pl.verdict[1])):
            return False
        ws = pl.ws
        bind = getattr(ws, "front_bind", None)
        take = getattr(_moy_play, "front", None)
        if bind is None or take is None:
            return False
        try:
            if not bind():
                pl._front_want = True
                return False
            return bool(take(pl._run_handle))
        except Exception as exc:  # noqa: BLE001 -- the console's frame still runs it
            print("PLAY front:", exc)
            return False

    def front_again(self):
        """A console frame of a run that wants the front: ask for it again."""
        pl = self.pl
        if pl.paused_at or pl.detached:
            return
        pl.front = self.take_front()

    def resume_from_front(self, ws):
        """The console's first frame after the kernel's front gave it back:
        True when the run ENDED there (quit(), `end`, a Ctrl-C),
        whose route this takes; False when it is live and needs the console
        (a view it composes, an error it reports, a cart the link asked for).
        While the kernel still has the front -- a console frame in the same
        loop frame that took it, as a link's re-run does -- the frame is not
        the console's: True, and the run stays the kernel's."""
        pl = self.pl
        if _moy_play.front_live():
            return True
        pl.front = False
        info = _moy_play.info()
        if info is None or not info[6]:
            return False
        ws.reset_canvas_state()
        ws.spine.exit_to_caller()
        return True

    # -- the pause menu's hold (docs/history/look_2026-10.md step 25a) -----------------

    @property
    def paused(self):
        return self.pl.paused_at != 0

    def pause(self):
        """Hold the run for the pause menu: no tick, no draw, its time() and
        its music stopped where they are (the mix loses its session, which
        keeps its state unheard), and the keys it kept dropped."""
        pl = self.pl
        if pl.paused_at:
            return
        pl.paused_at = _ticks_ms() or 1
        na = _audio_native()
        if na is not None:
            try:
                self.audio_held = na.focused()
                na.focus(0)
            except Exception:  # noqa: BLE001 -- a pause never fails on sound
                self.audio_held = 0
        self.park()

    def resume(self):
        """Give the run back as if no time passed: its clock moves by the
        pause, the mix takes its session again, its next tick sees fresh
        edges and the kernel takes the frame again where it had it."""
        pl = self.pl
        if not pl.paused_at:
            return
        span = _ticks_diff(_ticks_ms(), pl.paused_at)
        pl.paused_at = 0
        if span > 0:
            pl._cart_start_ms = _ticks_add(pl._cart_start_ms, span)
            r = pl.run
            if r.cart_start_ms is not None:
                r.cart_start_ms = _ticks_add(r.cart_start_ms, span)
            run = pl._run_handle if pl.play is not None else 0
            if run:
                _moy_play.hold(run, span)
        held, self.audio_held = self.audio_held, 0
        na = _audio_native()
        if na is not None and held:
            try:
                na.focus(held)
            except Exception:  # noqa: BLE001 -- the session may have closed
                pass
        self.park()
        pl.front = self.take_front()

    def disarm(self):
        pl = self.pl
        pl.tick_ms = 0
        if _loop is not None:
            _loop.tick(0)
        pl._free = False
        pl.n_ticks = 1
        pl.tick_edges = None
        pl.keep_edges = None
        self.park()

    def park(self):
        """A paced game that is not the surface on top (an Editor or menu
        over it) neither ticks nor keeps edges: what it kept for its next
        tick is dropped, so the shell reads fresh edges and the game does not
        resume on a press made before it was covered."""
        pl = self.pl
        pl.keyp_latch = 0
        if pl.detached:
            return                      # the edges are the front run's
        de = getattr(pl.ws.input, "drop_edges", None)
        if de is not None:
            de()

    def steady_mode(self, on):
        self.pl.sched.steady_mode(on)

    def uncap_mode(self, on):
        """The DIAG uncap (serial `uncap 0|1`): every loop frame draws, logic
        keeps its rate. Reaches a running cart here; the next run reads
        ws.uncap at start."""
        self.pl.sched.uncap_mode(on)
