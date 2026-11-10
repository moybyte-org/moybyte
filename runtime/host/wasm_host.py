"""Host wasm cart runtime: a "runtime": "wasm" cart on the boards' own binding.

`WasmHostRun(ws, ns, src)` runs the cart through `runtime/wasm_binding` --
libmoy's wasm import table over WAMR built for Linux at the boards' pin, on
moycore_run.c's console -- so the host is not a different program from the
device (native/moy_wasm/README.md: one engine, one import table, every
tier; there is no wasmtime tier). Its frame is lua_ext's seam -- the snapshot
in, the tick, quit, view and the audio queue out -- as the boards' glue runs
it for a run the kernel's Player does not take.

A trap surfaces as a normal Python exception out of `update()`, which is what
the Player's crash path already takes; the run never calls the cart again and
its canvas holds no partial frame. A compiled cart has no source line to throw
the kid at, so the Player opens the error panel with no EDIT action.

No compiler, no WAMR, no wasm carts on the host -- the rule the host's Lua
binding states, and the Player says so through the runtime-missing panel exactly as a
device build without the module does.

A cart bigger than the host's configured limit (`MEMORY_LIMIT`) gets the
notice a board with too little free PSRAM gives it, by the boards' own
footprint arithmetic (`WasmHostRuntime`).

The cart's written files (moy-spec SPEC.md 16.12) are kept beside the carts
store, `apps/<id>/`, by native/moy_store/moy_files.c, as on a board.

Its frames are the kernel's Player's (native/moy_play), launched before the
load as a Lua run's are: `play_begin` binds the launched run and the Player
calls `play_frame`.

The cart's `snd` stream is the run's; while it runs, the console's pull of
the mix adds it to every block (`audio_session.PcmPump.stream`), so it drains
at the pace the host plays, as a board's speaker drains it.

Canonical home is runtime/; tests import it as runtime.wasm_host.
"""

try:
    from runtime.lua_ext import (MOY_BUTTONS, snap_slots, audio_ops, snap_shared, run_room,
                                 sync_view, drain_audio)
    from runtime.moy_input import pointer_state
    from runtime.ticks import _since_ms
except ImportError:                                  # pragma: no cover
    from lua_ext import (MOY_BUTTONS, snap_slots, audio_ops, snap_shared, run_room,
                         sync_view, drain_audio)
    from moy_input import pointer_state
    from ticks import _since_ms


# What the host gives a compiled cart: the most any console board has, the
# P4's 32 MB of PSRAM, so a cart the host refuses is one no board could run.
MEMORY_LIMIT = 32 * 1024 * 1024


def available():
    """True when the host wasm binding builds and loads."""
    try:
        from runtime.wasm_binding import HostWasmRun
    except ImportError:                              # pragma: no cover
        return False
    return HostWasmRun.available()


def _wire_swapped():
    """Whether the canvas class stores its words byte-swapped -- the module's
    fact (`device_canvas.PAL565_WIRE`), which a cart palette follows too."""
    try:
        import device_canvas as dc
    except ImportError:
        from device import device_canvas as dc
    return dc.PAL565_WIRE is not dc.PAL565


class WasmHostRuntime:
    """`ws.runtimes["wasm"]` on the host: called with `(ns, src)` it starts
    a WasmHostRun, and before it does, the Player asks it what the cart
    imports that the host's table lacks (`missing`), what the cart's load
    needs (`footprint`) and what the host gives (`memory`) -- the device
    glue's `moycore_glue.WasmRuntime`, with `MEMORY_LIMIT` for free PSRAM."""

    def __init__(self, ws):
        self.ws = ws

    def __call__(self, ns, src):
        cart = getattr(self.ws, "cart", None) or {}
        if cart.get("wasi"):
            from runtime.wasi_run import WasiApp
            return WasiApp(self, self.ws, cart, ns.get("_moy_app"))
        return WasmHostRun(self.ws, ns, src)

    def missing(self, cart):
        """The names the cart's main.wasm imports that the host's table --
        the boards' own, compiled into the binding -- lacks, by the device
        glue's one comparison (`moycore_glue.missing_imports`). [] when
        there is no module to read; the load answers for itself."""
        from runtime import wasm_binding
        try:
            from device.moycore_glue import wasm_head, missing_imports
        except ImportError:                          # pragma: no cover
            from moycore_glue import wasm_head, missing_imports
        path = cart.get("path")
        if not path:
            return []
        try:
            head = wasm_head(path + "/" + cart.get("main", "main.wasm"))
        except OSError:
            return []
        return missing_imports(head, wasm_binding.table())

    def footprint(self, cart):
        """(total, block) by the boards' sizing, the host's module being
        the cart's own main.wasm; None with nothing to measure.

        The host always interprets (there is no AOT tier here -- "How a host
        EXECUTES a module is host policy", and this one's policy is WAMR's
        interpreter on every cart), so it sizes by the INTERPRETED rule, not
        the AOT one a board uses only when it has a matching module."""
        import os
        pages = cart.get("memory")
        path = cart.get("path")
        if not pages or not path:
            return None
        try:
            size = os.path.getsize(os.path.join(path, cart.get("main", "main.wasm")))
        except OSError:
            return None
        return self.footprint_of(pages, size, True)

    def footprint_of(self, pages, module_len, interp):
        """`footprint` from the numbers alone (Get Carts asks it off the
        index, #124). Interpreted whatever `interp` says: the host's policy."""
        from runtime import wasm_binding
        return wasm_binding.interp_footprint(int(pages) * 65536, int(module_len))

    def memory(self):
        return MEMORY_LIMIT, MEMORY_LIMIT

    RUNNING = -100                       # moy_wasi.h's MOY_WASI_RUNNING

    def command(self, cart, argv, roots=(), bg_pid=0, budget_ms=0, run=None):
        """A WASI command cart run as `argv` in the shell session running on
        this thread (runtime/shell/sh_door.py), its app rows answered for
        `run` (compiled_run's), to its end: (exit status, None), or a
        negative kind and the reason (`wasm_binding.TRAPPED`, `REFUSED`).
        The host interprets the cart's own main.wasm, as it does a cart."""
        from runtime import wasm_binding
        return wasm_binding.run_command(self._main(cart), argv, roots, bg_pid, budget_ms, run)

    def start(self, cart, argv, roots=(), pid=0, budget_ms=0, run=None):
        """`command`'s run started in the foreground and left running, as
        the shell door's job (moy_wasi.h's "IN STEPS"): (0, None), or
        (REFUSED, why). `step` serves it and `cancel` ends it."""
        del pid
        from runtime import wasm_binding
        return wasm_binding.start_command(self._main(cart), argv, roots, 0, budget_ms, run)

    def step(self, pid, max_ms):
        """The run's calls served for `max_ms` at most: (RUNNING, None), or
        `command`'s answer once it has ended."""
        del pid
        from runtime import wasm_binding
        return wasm_binding.step_command(max_ms)

    def cancel(self, pid):
        """The run ended as Ctrl-C ends it: (130, None) unless it had exited."""
        del pid
        from runtime import wasm_binding
        return wasm_binding.stop_command()

    def service(self, cart, pid, run=None):
        """The cart's main.wasm started as service `pid` (native/moy_wasm/
        moy_wasi.h, A SERVICE): its `_start` on a thread of its own, its
        argv the cart's id, its app rows answered for `run`, the app grant
        compiled_run made. None, or why it did not start."""
        from runtime import wasm_binding
        return wasm_binding.start_service(self._main(cart), pid, cart.get("id") or "", run)

    def stop(self, pid):
        """Service `pid` stopped: its descriptor hung up, killed (137) past
        its time. Its exit status, or -1 for no such service."""
        from runtime import wasm_binding
        return wasm_binding.stop_service(pid)

    def reap(self):
        """[(pid, exit status)] of the services that ended: by themselves, a
        trap (134), or killed by the watchdog (137)."""
        from runtime import wasm_binding
        return wasm_binding.reap_services()

    def figures(self, pid):
        """(linear memory bytes, its wakes' cost in us) of live service
        `pid`, or None once it has ended."""
        from runtime import wasm_binding
        return wasm_binding.service_info(pid)

    def keep(self, pid):
        """Service `pid` made a Player app's run (runtime/run/wasi_run.py):
        the reap leaves its end to its stop. Whether there is one."""
        from runtime import wasm_binding
        return wasm_binding.keep_service(pid)

    def done(self, pid):
        """Whether service `pid`'s `_start` has returned."""
        from runtime import wasm_binding
        return wasm_binding.service_done(pid)

    @staticmethod
    def _main(cart):
        import os
        return os.path.join(cart["path"], cart.get("main", "main.wasm"))


class WasmHostRun:
    """A compiled cart run on the host: the shape every runtime exposes --
    `init`/`update`/`draw`/`close` -- as the boards' glue's runs have it."""

    def __init__(self, ws, ns, src):
        del src                          # a compiled cart has no source text
        from runtime import wasm_binding
        from runtime.wasm_binding import HostWasmRun
        project = ws.run.project
        cart = getattr(project, "cart", None) or ws.cart or {}
        canvas = ws.run.canvas
        buf = getattr(canvas, "_buf", None)
        if buf is None:
            raise RuntimeError("a compiled cart needs an RGB565 canvas")
        self._run = HostWasmRun(
            buf, canvas.w, canvas.h, cart["path"], cart.get("main", "main.wasm"),
            cart.get("memory"),
            sheet=getattr(project, "sheet", None),
            tilemap=getattr(project, "play_map", None) or getattr(project, "tilemap", None),
            wire=getattr(canvas, "_wire", None), wire_swapped=_wire_swapped(),
            flags=getattr(project, "flags", None), cfg=ns.get("_moy_cfg"),
            files=cart["path"], writable=cart.get("writable"),
            ring=cart.get("audio_ring"), app=ns.get("_moy_app"))
        self._ns = ns
        self._ws = ws
        self._layers = self._images = None
        self._view = None
        self._touch_out = [0, 0, 0, 0]
        self.draw_next = True
        self._I_SNAP = snap_slots(wasm_binding)
        self._aq_ops = audio_ops(wasm_binding)
        pmem = getattr(ws, "pmem", None)
        cells = getattr(pmem, "cells", None) if pmem is not None else None
        if cells is not None:
            self._run.pmem_load(cells)
        self._buf = buf
        # _init runs here, as the Lua tier's load() runs it: the Player's
        # `init` hook has nothing left to do, and a trap in it is a start
        # error the Player reports like any other.
        err = self._run.init()
        if err:
            self._run.close()
            raise RuntimeError(err)
        self._sync_view()
        self._audio = getattr(ws, "audio_out", None)
        if self._audio is not None and hasattr(self._audio, "stream"):
            self._audio.stream = self._run
        self.init = None
        self.update = self._update
        self.draw = self._draw_noop

    def _update(self, dt):
        # A tier that swaps framebuffers under the canvas re-points the run,
        # as the Lua glue's retarget does.
        buf = self._ws.run.canvas._buf
        if buf is not self._buf:
            self._run.retarget(buf)
            self._buf = buf
        from runtime.wasm_binding import SNAP_BTN, SNAP_BTNP, SNAP_QUIT
        s = self._run.snap
        inp = self._ws.input
        # MOY_BUTTONS: the order is lua_ext's, never restated here.
        masks = getattr(inp, "button_masks", None)
        if masks is not None:
            held, pressed = masks(MOY_BUTTONS)
        else:
            held = pressed = 0
            for i, name in enumerate(MOY_BUTTONS):
                try:
                    if inp.held(name):
                        held |= 1 << i
                    if inp.pressed(name):
                        pressed |= 1 << i
                except Exception:  # noqa: BLE001
                    pass
        s[SNAP_BTN], s[SNAP_BTNP] = held, pressed
        snap_shared(s, inp, self._ws.run.cart_start_ms, self._I_SNAP, pointer_state,
                    self._touch_out, _since_ms,
                    getattr(self._ws, "windowed_chrome", False),
                    run_room(self._ws.run))
        err = self._run.tick(dt, self.draw_next)
        # quit() sets SNAP_QUIT; the Player honours cart_quit after the tick.
        if s[SNAP_QUIT]:
            s[SNAP_QUIT] = 0
            inp.cart_quit = True
        self._sync_view()
        drain_audio(self._ws.run.audio, self._aq_ops,
                    self._run.audio())
        if err:
            raise RuntimeError(err)

    def _sync_view(self):
        self._view = sync_view(self._ws, self._run.view(), self._view)

    # -- the frame in C (native/moy_play) -------------------------------------

    _prun = 0

    def play_begin(self, run, tick):
        """Hand this run's frame to the kernel's Player, which launched `run`
        before the load: the console's input table, the cart's audio session
        and the Tick a paced run notes into (None: unpaced). Returns
        `play_frame`, or None for an input that is no kernel table (the run
        then ticks through `update`). The run is this one's from here, ended
        at its close."""
        from runtime import moy_play
        self._prun = run
        try:
            au = self._ws.run.audio
            moy_play.bind(run, self._ws.input, int(getattr(au, "h", 0) or 0), tick)
            moy_play.open(run)
        except (RuntimeError, TypeError) as exc:
            print("PLAY bind:", exc)
            return None
        return self.play_frame

    def play_frame(self, n, dt, draw):
        """`n` ticks of `dt` in C, the last drawing when `draw`."""
        from runtime import moy_play
        ws = self._ws
        inp = ws.input
        buf = ws.run.canvas._buf
        if buf is not self._buf:
            self._run.retarget(buf)
            self._buf = buf
        out = self._touch_out
        try:
            pointer_state(inp, out)
            x, y, st = int(out[0]), int(out[1]), int(out[2])
        except Exception:  # noqa: BLE001 -- no pointer this frame
            x = y = st = 0
        bits = moy_play.frame(self._prun, n, dt, draw, x, y, st,
                              getattr(ws, "windowed_chrome", False),
                              ws.run.player.ui_room())
        if bits:
            if bits & moy_play.QUIT:
                inp.cart_quit = True
            if bits & moy_play.VIEW:
                self._sync_view()

    def _draw_noop(self):
        return None

    def flush_pmem(self):
        """The run's pmem image into the console's Pmem, if it moved."""
        pmem = getattr(self._ws, "pmem", None)
        cell = getattr(pmem, "cell", None) if pmem is not None else None
        if cell is None or getattr(self._run, "_r", None) is None:
            return False
        dirty, img = self._run.pmem()
        if not dirty:
            return False
        for i, v in enumerate(img):
            cell(i, int(v))
        return True

    def get_global(self, name):
        return None

    def get_global_len(self, name):
        return None

    def exec(self, src, name="probe"):
        return "a compiled cart has no chunks to run"

    def snd_counts(self):
        """The cart's stream, as `HostWasmRun.snd_counts` counts it."""
        return self._run.snd_counts()

    def close(self):
        audio = getattr(self, "_audio", None)
        if audio is not None and getattr(audio, "stream", None) is self._run:
            audio.stream = None
        try:
            self.flush_pmem()
        finally:
            # The hooks go with the state: the Player reads `update is None`
            # as "this run is over".
            self.update = None
            self.draw = None
            self._run.close()
            if self._prun:
                from runtime import moy_play
                moy_play.end(self._prun)
                self._prun = 0
