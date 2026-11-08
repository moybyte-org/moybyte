"""`ws.runtimes["elf"]`: the native tier (native/moy_elf/README.md) on every
tier, over the engine that tier carries -- `moy_elf` on a board, the host's
`elf_binding` (runtime/host/elf_binding.py) in the simulator. Both answer the
same calls: CHIP, the chip a module must be built for;
command(path, argv, roots, pid, id, bg_pid, budget_ms) -> (status, why), the
whole run; and the same run in steps (native/moy_elf/moy_os.h), which the
shell's door takes for a foreground command so the console goes on while it
runs: start(path, argv, roots, pid, id, budget_ms) -> (0 or REFUSED, why),
step(pid, max_ms) -> RUNNING or the exit status, cancel(pid) -> 130. The
runtime answers the door's job contract, the WASI runtime's too: start ->
(0, None) or (REFUSED, why), step and cancel -> (code, why).

A command granted the "network" permission runs with the socket rows
(`net=True`, the engine's NET flag), and its first socket holds the radio
for it under the lease tag "native" (`NET_TAG`) until its end: the engine
calls `_net(pid, hold)` on the task that serves the session, and the radio
is let go when no native process holds it.

A native cart carries one module a chip, `main.<chip>.elf` beside its
manifest (tools/elf_cart.py), and its manifest's `"moybyte": {"native":
[chips]}` names them; a cart with none for this console does not run here.
"""

REFUSED = -3
RUNNING = -100          # moy_os.h's MOY_OS_RUNNING
NET_TAG = "native"


class ElfRuntime:

    RUNNING = RUNNING

    def __init__(self, ws, engine):
        self.ws = ws
        self.engine = engine
        self.netted = set()             # the pids holding the network
        on_net = getattr(engine, "on_net", None)
        if on_net is not None:
            on_net(self._net)

    def _net(self, pid, hold):
        """A process's first socket (hold 1) or its end (0): the radio held
        while any native process holds it, and on the hold the console's
        last-joined network asked for (the link is the rows' to wait for)."""
        ws = self.ws
        if hold:
            first = not self.netted
            self.netted.add(pid)
            if first and getattr(ws, "wifi_hold", None) is not None:
                ws.wifi_hold(NET_TAG)
                dial = getattr(getattr(ws, "cart_net", None), "dial", None)
                if dial is not None:
                    dial()
        elif pid in self.netted:
            self.netted.discard(pid)
            if not self.netted and getattr(ws, "wifi_release", None) is not None:
                ws.wifi_release(NET_TAG)

    def _flags(self, net):
        return getattr(self.engine, "NET", 1) if net else 0

    def module(self, cart):
        """The path of the cart's module for this console, or None."""
        chip = self.engine.CHIP
        if chip not in (cart.get("native") or ()):
            return None
        path = "%s/main.%s.elf" % (cart["path"], chip)
        try:
            open(path, "rb").close()
        except OSError:
            return None
        return path

    def command(self, cart, argv, roots=(), pid=0, bg_pid=0, budget_ms=0, net=False):
        """The cart's module run as `argv` in the shell session the door's
        crossing runs in (runtime/shell/sh_door.py), reaching the `roots`
        (store-relative folders) its permissions grant, with sockets when
        `net`: (exit status, None), or (REFUSED, why) when it did not run."""
        path = self.module(cart)
        if path is None:
            return REFUSED, "it has no module for this console (%s)" % self.engine.CHIP
        joined = "".join(r + "\0" for r in roots) or None
        return self.engine.command(path, tuple(argv), joined, pid, cart.get("id") or "",
                                   bg_pid, budget_ms, self._flags(net))

    def start(self, cart, argv, roots=(), pid=0, budget_ms=0, net=False):
        """command's run started as process `pid` and left running: (0,
        None), or (REFUSED, why)."""
        path = self.module(cart)
        if path is None:
            return REFUSED, "it has no module for this console (%s)" % self.engine.CHIP
        joined = "".join(r + "\0" for r in roots) or None
        return self.engine.start(path, tuple(argv), joined, pid, cart.get("id") or "",
                                 budget_ms, self._flags(net))

    def step(self, pid, max_ms):
        """Serve the run's session calls for `max_ms` at most: (RUNNING,
        None), or its exit status and None once it has ended."""
        return self.engine.step(pid, max_ms), None

    def cancel(self, pid):
        """End the run as Ctrl-C does: (130, None)."""
        return self.engine.cancel(pid), None


def make_elf_runtime(ws):
    """The native tier on a board, or None when the image carries none."""
    try:
        import moy_elf
    except ImportError:
        return None
    return ElfRuntime(ws, moy_elf)
