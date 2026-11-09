"""A WASI app in the Player: a compiled cart whose manifest says `"wasi": true`
(`"moybyte": {"type": "app", "wasi": true}`) is a WASI command
(native/moy_wasm/moy_wasi.h) drawing with the toolkit it links
(sdk/include/moybyte/ui.h) on a screen of its own, instead of exporting a
cart's loop. The wasm runtime (`ws.runtimes["wasm"]`,
runtime/host/wasm_host.py on the host, device/moycore_glue.py on a board)
answers such a cart with a WasiApp.

Its `_start` runs as a WASI service for the Player run's pid, on a task of
its own (`rt.service`), kept out of the scheduler's reap (`rt.keep`): its end
is this run's. Its screen is the run's canvas size in RGB565, a slot the
kernel holds (native/moy_kernel/moy_screen_slot.h): each tick posts the
system's "frame" message ([held, pressed, ms] and the input words a native
app's carry, elf_run.IN_*) unless the last waits unread, and waits up to the
tick's time for a present; the draw puts the presented screen on the run's
canvas with `blit565`. A `_start` that returns with 0 quits; any other status
is a stop the stop card reports.
"""

from array import array

try:
    import moy_evq as _E
    from elf_run import IN_WORDS, fill_words, key_ring, key_sent, read_buttons
except ImportError:  # host: the runtime package
    from runtime import moy_evq as _E
    from runtime.elf_run import IN_WORDS, fill_words, key_ring, key_sent, read_buttons

# The longest a tick waits for a present (elf_run.WAIT_US_MAX's).
WAIT_MS_MAX = 50
SENT, SHOWN = 1, 2                      # moy_screen_slot.h's MOY_SCREEN_SLOT_*


class WasiApp:
    """A WASI app's run in the Player (this module's head)."""

    def __init__(self, rt, ws, cart, run):
        self._rt = rt
        self._ws = ws
        self.pid = pid = ws.run.player.pid
        canvas = ws.run.canvas
        self._w, self._h = canvas.w, canvas.h
        self._own_q = not _E.has(pid)
        if self._own_q:
            _E.open(pid, cart.get("id") or "")
        _E.listen(pid, "frame", True)
        if _E.screen_open(pid, self._w, self._h, 2) != 0:
            self._release()
            raise RuntimeError("no screen for it")
        why = rt.service(cart, pid, run)
        if why is not None:
            self._release()
            raise RuntimeError(why)
        rt.keep(pid)
        self._px = _E.screen_pixels(pid)
        self._masks = [0, 0]
        self._mask_of = getattr(ws.input, "button_masks", None)
        self._words = array("i", bytes(4 * IN_WORDS))
        self._ptr = [0, 0, 0, 0]
        self._keys = key_ring()
        self._pressed = 0
        self._ms = 0                    # the time since the last frame message
        self._shown = False
        self.presented = 0              # the frames it has presented
        self._live = True
        self.draw_next = True
        self.init = None
        self.update = self._update
        self.draw = self._draw

    def _release(self):
        self._px = None
        _E.screen_close(self.pid)
        if self._own_q:
            _E.close(self.pid)

    def _update(self, dt):
        if not self._live:
            return
        out = self._masks
        read_buttons(self._ws.input, self._mask_of, out)
        self._pressed |= out[1]
        self._shown = False             # the call lets the last frame go
        fill_words(self._ws, self._words, self._ptr, self._keys)
        tick = int(dt * 1000)
        self._ms += tick
        got = _E.screen_frame(self.pid, out[0], self._pressed, self._ms,
                              tick if tick < WAIT_MS_MAX else WAIT_MS_MAX, self._words)
        if got > 0 and got & SENT:
            self._pressed = 0
            self._ms = 0
            key_sent(self._keys)
        if got > 0 and got & SHOWN:
            self._shown = True
            self.presented += 1
        if self._rt.done(self.pid):
            self._end()

    def _end(self):
        self._live = False
        self._release()
        code = self._rt.stop(self.pid)
        if code == 0:
            self._ws.input.cart_quit = True
            return
        raise RuntimeError("the program ended with status %d" % code)

    def _draw(self):
        if self._shown and self._px is not None:
            self._ws.run.canvas.blit565(self._px, self._w, self._h, 0, 0)
            self._shown = False

    def close(self):
        if self._live:
            self._live = False
            self._release()
            self._rt.stop(self.pid)
