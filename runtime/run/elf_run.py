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
(0, None) or (REFUSED, why), step and cancel -> (code, why). A service is
the scheduler's (runtime/system/scheduler.py, COMPILED SERVICES):
service(cart, pid, run) -> None or why, stop(pid), reap() and figures(pid).

A GAME is the cart played in the Player (any type but a command or a
service, which the Terminal and the scheduler run): called with `(ns, src)`
the runtime starts the cart's module as the run's process (moy_os.h, A GAME)
and answers an ElfGame, the shape every runtime's run has
(`init`/`update`/`draw`/`close`). Each tick posts the frame message with the
buttons and waits up to the tick's time for a present; the draw puts the
presented screen into the run's canvas through the one canvas class's
`blit_indices`. An APP (its manifest's type "app") draws with the toolkit
it links (sdk/include/moybyte/ui.h): its screen is RGB565, drawn with
`blit565`, and its frame message carries the input words the toolkit reads
(moy_os.h's MOY_OS_FRAME_IN_*: the pointer, the key typed, the window and
the canvas's byte order); a key typed while the app has not read the last
message waits for the next one (key_ring). A game whose main returns 0 quits; any other end is a stop
the stop card reports, a signal by its name and, where the console keeps
one (a P4's isolation), its fault record. The Player asks `footprint` and
`memory` first, as it asks a compiled cart's runtime.

A command granted the "network" permission runs with the socket rows
(`net=True`, the engine's NET flag), and its first socket holds the radio
for it under the lease tag "native" (`NET_TAG`) until its end: the engine
calls `_net(pid, hold)` on the task that serves the session, and the radio
is let go when no native process holds it.

A native cart carries one module a chip, `main.<chip>.elf` beside its
manifest (tools/elf_cart.py), and its manifest's `"moybyte": {"native":
[chips]}` names them; a cart with none for this console does not run here.
An app's service entry `<stem>.elf` (scheduler.as_service) is a module
`<stem>.<chip>.elf` a chip, the chips those the folder carries.
A module that carries a signature must verify; one with none runs only when
its consent admits it (`app_perms.run_admits_unsigned`, the engine's UNSIGNED
flag), the one rule the compiled tier's AOT modules follow too.
"""

REFUSED = -3
RUNNING = -100          # moy_os.h's MOY_OS_RUNNING
ENDED = -4              # moy_os.h's MOY_OS_ENDED
FRAME_SENT, FRAME_SHOWN = 1, 2
NET_TAG = "native"
STACK = 16 * 1024       # moy_os_default_caps' stack: a game's one thread
# The longest a tick waits for a present, so a game that falls behind costs
# the console a frame at its slowest.
WAIT_US_MAX = 50000
# A signal's name, its exit status 128 + the number (moy_os.h, the faults).
SIGNALS = {4: "SIGILL", 5: "SIGTRAP", 7: "SIGBUS", 9: "SIGKILL", 11: "SIGSEGV",
           31: "SIGSYS"}
WHAT = {4: "an illegal instruction", 5: "a breakpoint", 7: "a misaligned access",
        9: "killed", 11: "a memory fault", 31: "a call the console lacks"}

try:
    from lua_ext import MOY_BUTTONS, run_room
    from moy_input import pointer_state
except ImportError:  # host: the runtime package
    from runtime.lua_ext import MOY_BUTTONS, run_room
    from runtime.moy_input import pointer_state

from array import array

# moy_os.h's MOY_OS_FRAME_IN_*: an app's input words.
IN_X, IN_Y, IN_PTR, IN_KEY, IN_WINDOW, IN_SWAP = range(6)
IN_ROOM_X, IN_ROOM_Y, IN_ROOM_W, IN_ROOM_H = range(6, 10)
IN_LAYOUT_W, IN_LAYOUT_H, IN_LAYOUT_FS = range(10, 13)
IN_WORDS = 13


def elf_defines(path, name):
    """True when the module at `path` (an ELF shared object, signed or not:
    a signature trails the module) defines dynamic symbol `name` -- what a
    native app exports, as the SDK's `moybyte/ui.h` exports its `_layout`.
    Reads the section headers, the dynamic symbol table and its strings,
    nothing of the code."""
    import struct
    want = name.encode()
    with open(path, "rb") as f:
        ident = f.read(64)
        if ident[:4] != b"\x7fELF" or ident[5] != 1:
            return False
        wide = ident[4] == 2
        if wide:
            shoff, = struct.unpack_from("<Q", ident, 40)
            shentsize, shnum = struct.unpack_from("<HH", ident, 58)
        else:
            shoff, = struct.unpack_from("<I", ident, 32)
            shentsize, shnum = struct.unpack_from("<HH", ident, 46)
        f.seek(shoff)
        shdrs = f.read(shentsize * shnum)

        def sect(i):
            o = i * shentsize
            if wide:
                t, = struct.unpack_from("<I", shdrs, o + 4)
                off, size = struct.unpack_from("<QQ", shdrs, o + 24)
                link, = struct.unpack_from("<I", shdrs, o + 40)
                es, = struct.unpack_from("<Q", shdrs, o + 56)
            else:
                t, = struct.unpack_from("<I", shdrs, o + 4)
                off, size, link = struct.unpack_from("<III", shdrs, o + 16)
                es, = struct.unpack_from("<I", shdrs, o + 36)
            return t, off, size, link, es

        for i in range(shnum):
            t, off, size, link, es = sect(i)
            if t != 11 or link >= shnum:             # SHT_DYNSYM
                continue
            _t, soff, ssize, _l, _e = sect(link)
            f.seek(soff)
            strs = f.read(ssize)
            f.seek(off)
            syms = f.read(size)
            es = es or (24 if wide else 16)
            for k in range(es, size - es + 1, es):
                nm, = struct.unpack_from("<I", syms, k)
                shndx, = struct.unpack_from("<H", syms, k + (6 if wide else 14))
                if shndx and strs[nm:nm + len(want) + 1] == want + b"\0":
                    return True
            return False
    return False


def read_buttons(inp, masks, out):
    """The buttons held and pressed this frame into out[0] and out[1], a bit
    each in libmoy's moy_button order: through `masks`, the input's
    button_masks bound once by the caller (None: it has none)."""
    if masks is not None:
        masks(MOY_BUTTONS, None, out)
        return
    held = pressed = 0
    for i, name in enumerate(MOY_BUTTONS):
        if inp.held(name):
            held |= 1 << i
        if inp.pressed(name):
            pressed |= 1 << i
    out[0] = held
    out[1] = pressed


# The keys typed that no frame message has carried yet, oldest first: a
# frame posts no message while the app has not read the last, and a key is
# one frame's, so it waits here for the next message (key_ring, key_sent).
KEYS_HELD = 8


def key_ring():
    """An empty ring of typed keys: [first, count, the keys...]."""
    return array("i", bytes(4 * (2 + KEYS_HELD)))


def key_sent(ring):
    """The ring's first key carried by a posted frame message."""
    if ring[1]:
        ring[0] = (ring[0] + 1) % KEYS_HELD
        ring[1] -= 1


def fill_words(ws, words, ptr, ring, lay=None):
    """An app's input words for this frame (IN_*), into the array('i')
    `words`; `ptr` a four-item list pointer_state fills, `ring` the keys
    typed and not yet sent (key_ring): this frame's joins it, and the
    oldest is the word; `lay` the (w, h, fs) its `_layout` was last told
    (the Player's), None for none."""
    inp = ws.input
    try:
        pointer_state(inp, ptr)
    except Exception:  # noqa: BLE001 -- no pointer this frame, not a dead app
        ptr[2] = 0
    words[IN_X] = int(ptr[0])
    words[IN_Y] = int(ptr[1])
    words[IN_PTR] = int(ptr[2])
    key = int(getattr(inp, "last_key", 0) or 0)
    if key and ring[1] < KEYS_HELD:
        ring[2 + (ring[0] + ring[1]) % KEYS_HELD] = key
        ring[1] += 1
    words[IN_KEY] = ring[2 + ring[0]] if ring[1] else 0
    words[IN_WINDOW] = 1 if getattr(ws, "windowed_chrome", False) else 0
    words[IN_SWAP] = 1 if getattr(ws.run.canvas, "swapped565", False) else 0
    room = run_room(ws.run)
    if room is None:
        words[IN_ROOM_X] = words[IN_ROOM_Y] = words[IN_ROOM_W] = words[IN_ROOM_H] = 0
    else:
        words[IN_ROOM_X] = room[0]
        words[IN_ROOM_Y] = room[1]
        words[IN_ROOM_W] = room[2]
        words[IN_ROOM_H] = room[3]
    if lay is not None:
        words[IN_LAYOUT_W] = lay[0]
        words[IN_LAYOUT_H] = lay[1]
        words[IN_LAYOUT_FS] = lay[2]


def stop_text(code, fault=None):
    """What the stop card says of a native game that ended with `code`, its
    fault record `fault` ((pid, name, cause, pc, addr), or None) beside it."""
    sig = code - 128 if code > 128 else 0
    if sig in SIGNALS:
        text = "native code stopped: %s, %s" % (SIGNALS[sig], WHAT[sig])
    else:
        text = "native code ended with status %d" % code
    if fault is not None:
        text += " (at 0x%x, address 0x%x, cause 0x%x)" % (fault[3], fault[4], fault[2])
    return text


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
        under the process's pid until its end, and on the first hold the
        console's last-joined network asked for (the link is the rows' to
        wait for)."""
        ws = self.ws
        if hold:
            first = not self.netted
            self.netted.add(pid)
            if getattr(ws, "wifi_hold", None) is not None:
                ws.wifi_hold(NET_TAG, pid)
                dial = getattr(getattr(ws, "cart_net", None), "dial", None)
                if first and dial is not None:
                    dial()
        elif pid in self.netted:
            self.netted.discard(pid)
            if getattr(ws, "wifi_release", None) is not None:
                ws.wifi_release(NET_TAG, pid)

    def _flags(self, cart, net):
        try:
            import app_perms
        except ImportError:  # host: the runtime package
            from runtime import app_perms
        unsigned = app_perms.run_admits_unsigned(self.ws, cart)
        return ((getattr(self.engine, "NET", 1) if net else 0)
                | (getattr(self.engine, "UNSIGNED", 2) if unsigned else 0))

    def __call__(self, ns, src):
        """The cart in the Player as a game (A GAME, above)."""
        del src                         # a native cart has no source text
        ws = self.ws
        cart = ws.cart or {}
        if cart.get("type") in ("command", "service"):
            raise RuntimeError("a native %s runs in the Terminal" % cart["type"])
        return ElfGame(self, ws, cart, ns.get("_moy_app"), ns.get("_moy_layout"))

    def wants_layout(self, cart):
        """Whether the cart's module for this console exports `_layout`
        (`elf_defines`): a native app's responsive opt-in, answered before
        the canvas is chosen."""
        path = self.module(cart)
        if path is None:
            return False
        try:
            return elf_defines(path, "_layout")
        except (OSError, ValueError):
            return False

    def footprint(self, cart, w=320, h=240):
        """(total, block) a game's start takes: its module (its file bounds
        what loads), its `w` x `h` screen twice (the module's and the
        process's frame), at two bytes a pixel for an app, and its thread's
        stack (an app's twice a game's); None with no module."""
        path = self.module(cart)
        if path is None:
            return None
        try:
            import os
            size = os.stat(path)[6]
        except OSError:
            return None
        canvas = getattr(getattr(self.ws, "run", None), "canvas", None)
        if canvas is not None:
            w, h = canvas.w, canvas.h
        app = cart.get("type") == "app"
        px = w * h * (2 if app else 1)
        stack = STACK * (2 if app else 1)       # moy_os_game's for an app
        return size + 2 * px + stack, max(size, px, stack)

    def memory(self):
        """(free, largest block) of the memory a module and its frame take:
        a board's PSRAM (moy_elf.mem), the host's limit."""
        mem = getattr(self.engine, "mem", None)
        if mem is None:
            return 32 * 1024 * 1024, 32 * 1024 * 1024
        got = mem()
        return got[2], got[3]

    def module(self, cart):
        """The path of the cart's module for this console, or None: its
        main's stem (`main` of `main.<chip>.elf`, `svc` of an app's service
        entry `svc.elf`) and this console's chip."""
        chip = self.engine.CHIP
        chips = cart.get("native") or ()
        if chip not in chips:
            return None
        main = cart.get("main") or ""
        stem = "main"
        if main.endswith(".elf"):
            stem = main[:-4]
            head, _, tail = stem.rpartition(".")
            if head and tail in chips:
                stem = head
        path = "%s/%s.%s.elf" % (cart["path"], stem, chip)
        try:
            open(path, "rb").close()
        except OSError:
            return None
        return path

    def command(self, cart, argv, roots=(), pid=0, bg_pid=0, budget_ms=0, net=False,
                run=None):
        """The cart's module run as `argv` in the shell session the door's
        crossing runs in (runtime/shell/sh_door.py), reaching the `roots`
        (store-relative folders) its permissions grant, with sockets when
        `net`, a module with no signature admitted when its consent admits
        one (app_perms.run_admits_unsigned), its app calls served for `run`
        (the bytes of its moy_app_run_t, None for none): (exit status,
        None), or (REFUSED, why) when it did not run."""
        path = self.module(cart)
        if path is None:
            return REFUSED, "it has no module for this console (%s)" % self.engine.CHIP
        joined = "".join(r + "\0" for r in roots) or None
        return self.engine.command(path, tuple(argv), joined, pid, cart.get("id") or "",
                                   bg_pid, budget_ms, self._flags(cart, net), run)

    def start(self, cart, argv, roots=(), pid=0, budget_ms=0, net=False, run=None):
        """command's run started as process `pid` and left running: (0,
        None), or (REFUSED, why)."""
        path = self.module(cart)
        if path is None:
            return REFUSED, "it has no module for this console (%s)" % self.engine.CHIP
        joined = "".join(r + "\0" for r in roots) or None
        return self.engine.start(path, tuple(argv), joined, pid, cart.get("id") or "",
                                 budget_ms, self._flags(cart, net), run)

    def service(self, cart, pid, run=None):
        """The cart's module started as service `pid` (native/moy_elf/
        moy_os.h, A SERVICE): its main on a task of its own, its app calls
        answered for `run` as command's are, hopping to the VM's task.
        None, or why it did not start."""
        path = self.module(cart)
        if path is None:
            return "it has no module for this console (%s)" % self.engine.CHIP
        return self.engine.service(path, pid, cart.get("id") or "", self._flags(cart, False),
                                   run)

    def stop(self, pid):
        """Service `pid` stopped: its descriptor hung up, killed (137) past
        its time. Its exit status, or -1 for no such service."""
        return self.engine.stop(pid)

    def reap(self):
        """[(pid, exit status)] of the services that ended: by themselves, or
        killed by the watchdog (moy_os_reap)."""
        return self.engine.reap()

    def figures(self, pid):
        """(bytes mapped, its wakes' cost in us) of live service `pid`, or
        None once it has ended."""
        got = self.engine.info(pid)
        if got is None:
            return None
        if isinstance(got, dict):
            return got["mapped"], got["cpu_us"]
        return got[4], got[5]

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


class ElfGame:
    """A native game's run in the Player (A GAME in this module's head)."""

    def __init__(self, rt, ws, cart, run, lay=None):
        self._e = rt.engine
        self._ws = ws
        path = rt.module(cart)
        if path is None:
            raise RuntimeError("it has no module for this console (%s)" % rt.engine.CHIP)
        canvas = ws.run.canvas
        self.pid = ws.run.player.pid
        self._w, self._h = canvas.w, canvas.h
        self._app = cart.get("type") == "app"
        self._words = array("i", bytes(4 * IN_WORDS)) if self._app else None
        # A responsive app's surface rides its frame words: the toolkit it
        # links calls its `_layout` when they change (sdk/src/moyui_link.c).
        self._lay = list(lay) if lay is not None and self._app else None
        self._ptr = [0, 0, 0, 0]
        self._keys = key_ring()
        why = self._e.game(path, self.pid, cart.get("id") or "", rt._flags(cart, False),
                           self._w, self._h, run, 2 if self._app else 1)
        if why is not None:
            raise RuntimeError(why)
        self._px = self._e.screen(self.pid)
        self._masks = [0, 0]
        self._mask_of = getattr(ws.input, "button_masks", None)
        self._pressed = 0
        self._ms = 0                    # the time since the last frame message
        self._shown = False
        self.presented = 0              # the frames it has presented
        self._live = True
        self.draw_next = True
        self.init = None
        self.layout = self._set_layout if self._lay is not None else None
        self.update = self._update
        self.draw = self._draw

    def _set_layout(self, w, h, fs):
        """The next frame message's layout words (IN_LAYOUT_*)."""
        lay = self._lay
        lay[0] = w
        lay[1] = h
        lay[2] = fs

    def _update(self, dt):
        if not self._live:
            return
        out = self._masks
        read_buttons(self._ws.input, self._mask_of, out)
        self._pressed |= out[1]
        self._shown = False             # the call lets the last frame go
        tick = int(dt * 1000)
        self._ms += tick
        wait = tick * 1000
        if wait > WAIT_US_MAX:
            wait = WAIT_US_MAX
        words = self._words
        if words is not None:
            fill_words(self._ws, words, self._ptr, self._keys, self._lay)
        got = self._e.frame(self.pid, out[0], self._pressed, self._ms, wait, words)
        if got == ENDED or got < 0:
            self._end()
            return
        if got & FRAME_SENT:
            self._pressed = 0
            self._ms = 0
            key_sent(self._keys)
        if got & FRAME_SHOWN:
            self._shown = True
            self.presented += 1

    def _end(self):
        self._live = False
        self._px = None
        code = self._e.game_stop(self.pid)
        if code == 0:
            self._ws.input.cart_quit = True
            return
        fault = None
        faults = getattr(self._e, "faults", None)
        if faults is not None:
            for f in faults():
                if f[0] == self.pid:
                    fault = f
                    break
        raise RuntimeError(stop_text(code, fault))

    def _draw(self):
        if self._shown:
            if self._app:
                self._ws.run.canvas.blit565(self._px, self._w, self._h, 0, 0)
            else:
                self._ws.run.canvas.blit_indices(self._px, self._w, self._h, 0, 0)
            self._shown = False

    def close(self):
        if self._live:
            self._live = False
            self._px = None
            self._e.game_stop(self.pid)
