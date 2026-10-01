"""Host wasm cart runtime: a "runtime": "wasm" cart on the boards' own binding.

`WasmHostRun(ws, ns, src)` runs the cart through `runtime/wasm_binding` --
libmoy's wasm import table over WAMR built for Linux at the boards' pin, on
the console the Lua shim uses too -- so the host is not a different program
from the device (docs/wasm_tier_plan_2026-09.md: one engine, one import table,
every tier; there is no wasmtime tier). It is `lua_host.MoycoreHostRun`'s
twin and shares its frame: the snapshot in, the tick, quit, view and the audio
queue out are the same body, so only construction is written here.

A trap surfaces as a normal Python exception out of `update()`, which is what
the Player's crash path already takes; the run never calls the cart again and
its canvas holds no partial frame. A compiled cart has no source line to throw
the kid at, so the Player opens the error panel with no EDIT action.

No compiler, no WAMR, no wasm carts on the host -- the rule `lua_host` states
for Lua, and the Player says so through the runtime-missing panel exactly as a
device build without the module does.

A cart bigger than the host's configured limit (`MEMORY_LIMIT`) gets the
notice a board with too little free PSRAM gives it, by the boards' own
footprint arithmetic (`WasmHostRuntime`).

The cart's `snd` stream is the run's; while it runs, the console's audio
backend mixes it into every block it renders (`host_api.FakeAudio.stream`),
so it drains at the pace the host plays, as a board's speaker drains it.

Canonical home is runtime/; tests import it as runtime.wasm_host.
"""

try:
    from runtime.lua_host import MoycoreHostRun
    from runtime.lua_ext import snap_slots, audio_ops
except ImportError:                                  # pragma: no cover
    from lua_host import MoycoreHostRun
    from lua_ext import snap_slots, audio_ops


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
    a WasmHostRun, and before it does, the Player asks it what the cart's load
    needs (`footprint`) and what the host gives (`memory`) -- the device
    glue's `moycore_glue.WasmRuntime`, with `MEMORY_LIMIT` for free PSRAM."""

    def __init__(self, ws):
        self.ws = ws

    def __call__(self, ns, src):
        return WasmHostRun(self.ws, ns, src)

    def footprint(self, cart):
        """(total, block) by the boards' sizing, the host's module being
        the cart's own main.wasm; None with nothing to measure.

        The host always interprets (there is no AOT tier here -- "How a host
        EXECUTES a module is host policy", and this one's policy is WAMR's
        interpreter on every cart), so it sizes by the INTERPRETED rule, not
        the AOT one a board uses only when it has a matching module."""
        import os
        from runtime import wasm_binding
        pages = cart.get("memory")
        path = cart.get("path")
        if not pages or not path:
            return None
        try:
            size = os.path.getsize(os.path.join(path, cart.get("main", "main.wasm")))
        except OSError:
            return None
        return wasm_binding.interp_footprint(int(pages) * 65536, size)

    def memory(self):
        return MEMORY_LIMIT, MEMORY_LIMIT


class WasmHostRun(MoycoreHostRun):
    """A compiled cart run on the host. Same shape as MoycoreHostRun."""

    def __init__(self, ws, ns, src):
        del src                          # a compiled cart has no source text
        from runtime import wasm_binding
        from runtime.wasm_binding import HostWasmRun
        project = getattr(ws, "project", None)
        cart = getattr(project, "cart", None) or ws.cart or {}
        canvas = ws.canvas
        buf = getattr(canvas, "_buf", None)
        if buf is None:
            raise RuntimeError("a compiled cart needs an RGB565 canvas")
        self._run = HostWasmRun(
            buf, canvas.w, canvas.h, cart["path"], cart.get("main", "main.wasm"),
            cart.get("memory"),
            sheet=getattr(project, "sheet", None),
            tilemap=getattr(project, "tilemap", None),
            wire=getattr(canvas, "_wire", None), wire_swapped=_wire_swapped(),
            flags=getattr(project, "flags", None), cfg=ns.get("_moy_cfg"))
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
        self._audio = getattr(ws, "audio", None)
        if self._audio is not None and hasattr(self._audio, "stream"):
            self._audio.stream = self._run
        self.init = None
        self.update = self._update
        self.draw = self._draw_noop

    def _update(self, dt):
        # A tier that swaps framebuffers under the canvas re-points the run,
        # as the Lua glue's retarget does.
        buf = self._ws.canvas._buf
        if buf is not self._buf:
            self._run.retarget(buf)
            self._buf = buf
        super()._update(dt)

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
            super().close()
