# Map (grep -n a name to jump there):
#   is_cart                           a compiled cart whose C builds for both targets
#   scene_rows                        a scene's actor records and flags texts, as moybyte.scenes answers
#   game_data                         what a compiled cart's native module reads at its start
#   elf_defines                       whether a module defines a dynamic symbol
#   read_buttons                      the frame's held and pressed button masks
#   key_ring                          the typed keys a run has not yet been sent
#   post_paste                        the clipboard into a native app's queue
#   fill_words                        an app's input words for this frame
#   LinkedUi                          what the Player asks of a toolkit the app links
#   stop_text                         a run's end code and fault as a sentence
#   ElfRuntime                        ws.runtimes["elf"]: commands, services and games
#   make_elf_runtime                  the native tier, or None when the image carries none
#   ElfGame                           a native game or app run in steps, frame by frame
#   ElfGame._release                  the process a run with no player began, ended
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
the scheduler's (runtime/system/sched_compiled.py, COMPILED SERVICES):
service(cart, pid, run) -> None or why, stop(pid), reap() and figures(pid).

A GAME is the cart played in the Player (any type but a command or a
service, which the Terminal and the scheduler run): called with `(ns, src)`
the runtime starts the cart's module as the run's process (moy_os.h, A GAME)
and answers an ElfGame, the shape every runtime's run has
(`init`/`update`/`draw`/`close`). Each tick posts the frame message with the
buttons and waits up to the tick's time for a present; the draw puts the
presented screen into the run's canvas through the one canvas class's
`blit_indices`, or, for a run set `from_slot`, leaves it in the screen's slot,
where `kept` hands the last frame taken until the next tick (a wallpaper's,
which the desk scales from there: wallpaper._Hosted). An APP (its manifest's type "app") draws with the toolkit
it links (sdk/include/moybyte/ui.h): its screen is RGB565, drawn with
`blit565`, and its frame message carries the input words the toolkit reads
(moy_os.h's MOY_OS_FRAME_IN_*: the pointer, the key typed, the ui face and
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
A compiled cart (`"runtime": "wasm"`) naming chips carries both modules
(sdk/README.md, "A compiled cart"), and where the console runs its native
one (app_perms.tier_of) its game is handed `game_data` at its start and
hears the pointer and the key typed in its frame words.
An app's service entry `<stem>.elf` (scheduler.as_service) is a module
`<stem>.<chip>.elf` a chip, the chips those the folder carries.
A module that carries a signature must verify; one with none runs only when
its consent admits it (`app_perms.run_admits_unsigned`, the engine's UNSIGNED
flag), the one rule the compiled tier's AOT modules follow too.
"""

REFUSED = -3
RUNNING = -100          # moy_os.h's MOY_OS_RUNNING
ENDED = -4              # moy_os.h's MOY_OS_ENDED
FRAME_SENT, FRAME_SHOWN, FRAME_DIRECT = 1, 2, 4
MIXED = 3               # moy_screen_slot.h's MOY_SCREEN_SLOT_MIXED: a compiled cart's screen
# moy_os.h's MOY_OS_SOUND_*, in moycore_run.h's AQ_* order (lua_ext.drain_audio's
# ops), and MOY_OS_SOUND_MAX.
SOUND_OPS = (0, 1, 2, 3, 4, 5)
SOUND_MAX = 32
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

from lua_ext import MOY_BUTTONS, cfg_blob, drain_audio, put_scenes, run_face, run_room, wasm_exts
from moy_input import pointer_state
from ui_draw import KEY_PASTE, paste_text
import moy_evq as _E
import os_log as _os_log

from array import array

# moy_os.h's MOY_OS_FRAME_IN_*: an app's input words.
IN_X, IN_Y, IN_PTR, IN_KEY, IN_FACE, IN_SWAP = range(6)
IN_ROOM_X, IN_ROOM_Y, IN_ROOM_W, IN_ROOM_H = range(6, 10)
IN_LAYOUT_W, IN_LAYOUT_H, IN_LAYOUT_FS = range(10, 13)
IN_REP, IN_KEYREP, IN_ASK = range(13, 16)
IN_WORDS = 16
# moy_os.h's MOY_OS_ASK_* (the ask word's bits) and MOY_OS_TOLD_* (what the
# toolkit an app links told after its last frame: moy_ui_tell).
ASK_CANCEL, ASK_BACK = 1, 2
TOLD_TYPING, TOLD_CANCEL, TOLD_BACK = 1, 2, 4


# The data a compiled cart's native module reads at its start (moy_game_data;
# its layout is sdk/src/moycart.c's): an entry's kind.
D_SHEET, D_FLAGS, D_MAP, D_CFG, D_PMEM, D_SCENE, D_IMAGE, D_WIRE = range(1, 9)
ACTOR_TAG = 16          # moycore_lua.h's MOYCORE_ACTOR_TAG


def is_cart(cart):
    """Whether `cart` is a compiled cart's native module (a `"runtime": "wasm"`
    cart with a module a chip, sdk/include/moybyte/cart.h): its game reads
    `game_data` and hears the pointer and keys in its frame words."""
    return bool(cart) and bool(cart.get("native")) and cart.get("runtime") == "wasm"


def _i32(v):
    """A scene field as moycore_lua.c's row_record reads it: a number made an
    int (truncated), anything else 0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    try:
        return max(-0x80000000, min(0x7FFFFFFF, int(v)))
    except (OverflowError, ValueError):
        return 0


def _tag(v):
    """An actor's tag as row_record spells it, cut at ACTOR_TAG - 1 bytes."""
    if v is True:
        t = "True"
    elif v is False:
        t = "False"
    elif v is None:
        t = "None"
    else:
        t = v if isinstance(v, str) else str(v)
    b = t.encode()[:ACTOR_TAG - 1]
    return b + bytes(ACTOR_TAG - len(b))


def scene_rows(text):
    """A scene's JSON text as moybyte.scenes answers it (moycore_wasm_ext.h):
    its actors' records and their "flags" objects as JSON texts."""
    import json
    import struct
    try:
        rows = json.loads(text)
    except ValueError:
        rows = None
    recs, flags = bytearray(), []
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, dict):
            continue
        recs += struct.pack("<iiii", _i32(r.get("tile")), _i32(r.get("x")), _i32(r.get("y")),
                            _i32(r.get("flip")))
        recs += _tag(r.get("tag", ""))
        f = r.get("flags")
        flags.append(json.dumps(f).encode() if isinstance(f, dict) else b"")
    out = bytearray(struct.pack("<I", len(flags))) + recs
    for f in flags:
        out += struct.pack("<I", len(f)) + f
    return out


def game_data(ws, cart, ns):
    """What a compiled cart's native module reads at its start (moy_game_data,
    sdk/src/moycart.c): the run's sprites, flags, map, config and pmem, the
    scenes and paint images of the extensions its manifest declares, as the
    console hands a wasm run of the same cart (runtime/host/wasm_host.py,
    device/moycore_glue.py's WasmRun), and the canvas's words for the
    palette, which a direct-colour frame's drawn pixels become."""
    import struct
    project = getattr(getattr(ws, "run", None), "project", None)
    parts = []

    def put(kind, name, data):
        nb = name.encode()[:255]
        parts.append(struct.pack("<BB", kind, len(nb)) + nb + struct.pack("<I", len(data)))
        parts.append(data)

    sheet = getattr(project, "sheet", None)
    pix = getattr(sheet, "pix", sheet)
    if pix is not None:
        put(D_SHEET, "", pix)
    flags = getattr(project, "flags", None)
    if flags is not None:
        put(D_FLAGS, "", bytes(flags))
    tm = getattr(project, "play_map", None) or getattr(project, "tilemap", None)
    cells = getattr(tm, "cells", None)
    if cells is not None:
        put(D_MAP, "", struct.pack("<HH", int(tm.w), int(tm.h)) + bytes(cells))
    cfg = cfg_blob(ns.get("_moy_cfg"))
    if cfg:
        put(D_CFG, "", cfg)
    pm = getattr(getattr(project, "pmem", None), "cells", None)
    if pm is not None:
        vals = [int(c) for c in list(pm)[:256]]
        put(D_PMEM, "", struct.pack("<%di" % len(vals), *vals))
    exts = wasm_exts(cart)
    if exts & 1:
        put_scenes(ns, lambda name, text: put(D_SCENE, name, scene_rows(text)))
    if exts & 2:
        from moyimg import decode_moyimg
        imgs = ns.get("_moy_images") or {}
        for name in imgs:
            blob = imgs[name]
            got = decode_moyimg(blob) if isinstance(blob, (str, bytes)) else None
            if got is not None:
                put(D_IMAGE, name, struct.pack("<HH", got[0], got[1]) + bytes(got[2]))
    wire = getattr(getattr(getattr(ws, "run", None), "canvas", None), "_wire", None)
    if wire is not None and len(wire) >= 64:
        put(D_WIRE, "", struct.pack("<64H", *[int(wire[i]) for i in range(64)]))
    total = 12 + sum(len(p) for p in parts)
    out = bytearray(b"MOYG" + struct.pack("<II", total, len(parts) // 2))
    for p in parts:
        out += p
    return out


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


# The "paste" message's arguments at most: a JSON array of one string, which
# the linked toolkit reads into a frame record of 256 bytes.
PASTE_ARGS = 250


def post_paste(pid):
    """The console's Paste for the app `pid` drawing with the toolkit it
    links (sdk/src/moyui_link.c): the clipboard's text as the system's
    "paste" message, posted ahead of the frame message carrying Ctrl+V, its
    field inserting it. A string of no escapes but a quote's and a
    backslash's (a control byte is a space), cut at a character boundary."""
    t = paste_text()
    if not t:
        return
    b = bytes(c if c >= 32 else 32 for c in t).replace(b"\\", b"\\\\").replace(b'"', b'\\"')
    cut = len(b)
    if cut > PASTE_ARGS - 4:
        cut = PASTE_ARGS - 4
        while cut and b[cut] & 0xC0 == 0x80:
            cut -= 1
        k = cut
        while k and b[k - 1] == 0x5C:
            k -= 1
        if (cut - k) & 1:
            cut -= 1                    # never half an escape
    _E.post(pid, 0, "paste", b'["' + b[:cut] + b'"]')


def fill_words(ws, words, ptr, ring, lay=None, pid=0, quiet=False):
    """An app's input words for this frame (IN_*), into the array('i')
    `words`; `ptr` a four-item list pointer_state fills, `ring` the keys
    typed and not yet sent (key_ring): this frame's joins it, and the
    oldest is the word; `lay` the (w, h, fs) its `_layout` was last told
    (the Player's), None for none. A Ctrl+V typed posts app `pid` the
    console's Paste (`post_paste`) as it joins the ring. `quiet` (a run
    held aside) reads no pointer, key or step."""
    inp = ws.input
    try:
        if quiet:
            ptr[2] = 0
        else:
            pointer_state(inp, ptr)
    except Exception:  # noqa: BLE001 -- no pointer this frame, not a dead app
        ptr[2] = 0
    words[IN_X] = int(ptr[0])
    words[IN_Y] = int(ptr[1])
    words[IN_PTR] = int(ptr[2])
    key = 0 if quiet else int(getattr(inp, "last_key", 0) or 0)
    if key and ring[1] < KEYS_HELD:
        ring[2 + (ring[0] + ring[1]) % KEYS_HELD] = key
        ring[1] += 1
        if key == KEY_PASTE and pid:
            post_paste(pid)
    words[IN_KEY] = ring[2 + ring[0]] if ring[1] else 0
    words[IN_FACE] = run_face(ws)
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
    if not quiet:
        add_steps(inp, words)


def add_steps(inp, words):
    """The frame's steps beyond its presses (the input table's `rep_word`, a
    byte a direction, and a held key's typing repeats) added to those
    `words` hold while their message waits unread (IN_REP, IN_KEYREP; a
    frame or two, so no byte carries); `sent` clears them."""
    rw = getattr(inp, "rep_word", 0)
    if rw:
        v = (words[IN_REP] + rw) & 0xFFFFFFFF
        words[IN_REP] = v - 0x100000000 if v >> 31 else v
    kn = getattr(inp, "key_repeats", 0)
    if kn:
        key = inp.repeat_key & 0xFFFF
        was = words[IN_KEYREP]
        words[IN_KEYREP] = key | (kn + (was >> 16 if was & 0xFFFF == key else 0)) << 16


def sent(words):
    """A posted frame message carried the steps and the asks."""
    words[IN_REP] = words[IN_KEYREP] = words[IN_ASK] = 0


# Why a native cart did not run here, as the shell says it: in words a kid
# reads, the chip it lacks a module for after them.
NO_MODULE = "it was built for another kind of console (it has no %s module)"


class LinkedUi:
    """What the Player asks a run's toolkit (ui_draw.run_typing, run_cancel,
    run_back), asked of an app drawing with the toolkit it links
    (sdk/src/moyui_link.c): what its last frame told (moy_ui_tell, TOLD_*),
    a cancel or a back taken going in on the next frame message (ASK_*).
    And the input its frame message carries, which a run held aside
    (`mute`, runtime/shell/procs.py) does not read."""

    _muted = False

    def mute(self, on):
        """Held aside while `on`: its frames carry no buttons, pointer or key."""
        self._muted = bool(on)

    def _input(self):
        """This frame's buttons into `_masks` (held, pressed), the presses
        joining those its next message carries, and an app's input words:
        none of them while it is muted."""
        out = self._masks
        if self._muted:
            out[0] = out[1] = 0
        else:
            read_buttons(self._ws.input, self._mask_of, out)
        self._pressed |= out[1]
        if self._words is not None:
            fill_words(self._ws, self._words, self._ptr, self._keys, self._lay, self.pid,
                       self._muted)
        return out

    def _ask(self, told, ask, take):
        words = self._words
        if not self._live or words is None or not _E.screen_told(self.pid) & told:
            return False
        if take:
            words[IN_ASK] |= ask
        return True

    def ui_typing(self):
        return self._ask(TOLD_TYPING, 0, False)

    def ui_cancel(self, take=True):
        return self._ask(TOLD_CANCEL, ASK_CANCEL, take)

    def ui_back(self, take=True):
        return self._ask(TOLD_BACK, ASK_BACK, take)


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
            if getattr(getattr(ws, "spine", None), "wifi_hold", None) is not None:
                ws.spine.wifi_hold(NET_TAG, pid)
                dial = getattr(getattr(ws, "cart_net", None), "dial", None)
                if first and dial is not None:
                    dial()
        elif pid in self.netted:
            self.netted.discard(pid)
            if getattr(getattr(ws, "spine", None), "wifi_release", None) is not None:
                ws.spine.wifi_release(NET_TAG, pid)

    def _flags(self, cart, net):
        import app_perms
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
        return ElfGame(self, ws, cart, ns.get("_moy_app"), ns.get("_moy_layout"), ns)

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
            return REFUSED, NO_MODULE % self.engine.CHIP
        joined = "".join(r + "\0" for r in roots) or None
        return self.engine.command(path, tuple(argv), joined, pid, cart.get("id") or "",
                                   bg_pid, budget_ms, self._flags(cart, net), run)

    def start(self, cart, argv, roots=(), pid=0, budget_ms=0, net=False, run=None):
        """command's run started as process `pid` and left running: (0,
        None), or (REFUSED, why)."""
        path = self.module(cart)
        if path is None:
            return REFUSED, NO_MODULE % self.engine.CHIP
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
            return NO_MODULE % self.engine.CHIP
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


class ElfGame(LinkedUi):
    """A native game's run in the Player (A GAME in this module's head)."""

    own_ui = True                       # an app's toolkit is its own: a paste is its message
    _dual = _snd = _direct = False      # a compiled cart's module: its channels
    _sound = _pmem = None

    def __init__(self, rt, ws, cart, run, lay=None, ns=None):
        self._e = rt.engine
        self._ws = ws
        path = rt.module(cart)
        if path is None:
            raise RuntimeError(NO_MODULE % rt.engine.CHIP)
        canvas = ws.run.canvas
        player = ws.run.player
        # A run with no player (a wallpaper's) is a process of its own.
        self._own = player is None
        self.pid = _os_log.begin(cart.get("id") or "cart") if self._own else player.pid
        self._w, self._h = canvas.w, canvas.h
        self._app = cart.get("type") == "app"
        dual = is_cart(cart)
        self._words = array("i", bytes(4 * IN_WORDS)) if self._app or dual else None
        # A responsive app's surface rides its frame words: the toolkit it
        # links calls its `_layout` when they change (sdk/src/moyui_link.c).
        self._lay = list(lay) if lay is not None and self._app else None
        self._ptr = [0, 0, 0, 0]
        self._keys = key_ring()
        data = game_data(ws, cart, ns if ns is not None else {}) if dual else None
        if dual:
            # Its files are the store a wasm run's are (moycore_files.h).
            writable = cart.get("writable") or ()
            why = self._e.game(path, self.pid, cart.get("id") or "", rt._flags(cart, False),
                               self._w, self._h, run, MIXED, data, cart.get("path"),
                               "\0".join(writable).encode() if writable else None)
        else:
            why = self._e.game(path, self.pid, cart.get("id") or "", rt._flags(cart, False),
                               self._w, self._h, run, 2 if self._app else 1, data)
        data = None
        if why is not None:
            self._release()
            raise RuntimeError(why)
        self._dual = dual
        self._direct = False
        self._sound = array("i", bytes(12 * SOUND_MAX)) if dual else None
        self._pmem = array("i", bytes(4 * 256)) if dual else None
        self._snd = dual and self._snd_open(cart)
        self._px = self._e.screen(self.pid)
        self._kept = (self._px, self._w, self._h, 1)
        self._kept565 = (self._px, self._w, self._h, 2)
        self._masks = [0, 0]
        self._mask_of = getattr(ws.input, "button_masks", None)
        self._pressed = 0
        self._ms = 0                    # the time since the last frame message
        self._shown = False
        self.presented = 0              # the frames it has presented
        self.from_slot = False          # its frames are shown from `kept`
        self.owe = False                # the next draw puts `kept` in the canvas
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
        out = self._input()
        self._shown = False             # the call lets the last frame go
        tick = int(dt * 1000)
        self._ms += tick
        wait = tick * 1000
        if wait > WAIT_US_MAX:
            wait = WAIT_US_MAX
        words = self._words
        got = self._e.frame(self.pid, out[0], self._pressed, self._ms, wait, words)
        if got == ENDED or got < 0:
            self._end()
            return
        if got & FRAME_SENT:
            self._pressed = 0
            self._ms = 0
            key_sent(self._keys)
            if words is not None:
                sent(words)
        if got & FRAME_SHOWN:
            self._shown = True
            self._direct = bool(got & FRAME_DIRECT)
            self.presented += 1
        if self._sound is not None:
            self._drain_sound()

    def _drain_sound(self):
        """The audio verbs the game queued, onto the run's session as a Lua or
        wasm run's queue goes (lua_ext.drain_audio)."""
        q = self._sound
        n = self._e.game_sound(self.pid, q)
        if n > 0:
            drain_audio(self._ws.run.audio, SOUND_OPS,
                        ((q[3 * i], q[3 * i + 1], q[3 * i + 2]) for i in range(n)))

    def _snd_open(self, cart):
        """The speaker's render-ahead stream for the game's snd, as deep as
        the manifest's "audio_ring" asks, where nothing else holds it
        (runtime/system/moybyte.py's snd_open): the audio module when it is
        the game's, else False."""
        try:
            import moy_audio
        except ImportError:  # an image without audio, a host without a compiler
            return False
        try:
            c = moy_audio.snd_counts()
            if c is not None and c[4]:
                return False            # a compiled cart or a process holds it
            if moy_audio.snd_open(int(cart.get("audio_ring") or 0)) <= 0:
                return False
        except (AttributeError, OSError, TypeError, ValueError):
            return False
        if self._e.game_snd(self.pid, True) != 0:
            moy_audio.snd_close()
            return False
        return moy_audio

    def _snd_close(self):
        audio = self._snd
        if audio:
            self._snd = False
            self._e.game_snd(self.pid, False)
            audio.snd_close()

    def flush_pmem(self):
        """The game's pmem image into the console's Pmem, if it moved, as a
        wasm run's (runtime/host/wasm_host.py's flush_pmem)."""
        img = self._pmem
        if img is None or not self._live:
            return False
        pmem = getattr(self._ws.run.project, "pmem", None)
        cell = getattr(pmem, "cell", None) if pmem is not None else None
        if cell is None or self._e.game_pmem(self.pid, img) != 1:
            return False
        for i in range(256):
            cell(i, int(img[i]))
        return True

    def _end(self):
        self._closing()
        self._live = False
        self._px = self._kept = self._kept565 = None
        code = self._e.game_stop(self.pid)
        self._release()
        if code == 0:
            if not self._own:
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

    def kept(self):
        """(screen, w, h, depth) of the last frame the run presented, which
        its slot holds until the next tick's update (moy_screen_slot.h), or
        None before the first."""
        if not self.presented or self._px is None:
            return None
        return self._kept565 if self._app or self._direct else self._kept

    def _draw(self):
        if (self._shown or self.owe) and not self.from_slot and self._px is not None \
                and self.presented:
            self.owe = False
            if self._app or self._direct:
                self._ws.run.canvas.blit565(self._px, self._w, self._h, 0, 0)
            else:
                self._ws.run.canvas.blit_indices(self._px, self._w, self._h, 0, 0)
            self._shown = False

    def close(self):
        if self._live:
            self._closing()
            self._live = False
            self._px = self._kept = self._kept565 = None
            self._e.game_stop(self.pid)
            self._release()

    def _closing(self):
        """What the game handed over last, while its record holds it: its
        audio verbs and its pmem; its stream let go."""
        if self._dual:
            self._drain_sound()
            self.flush_pmem()
            self._snd_close()

    def _release(self):
        if self._own:
            self._own = False
            _os_log.end(self.pid)
