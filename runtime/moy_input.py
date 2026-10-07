"""Input as the kernel's native `moy_input` will expose it: its Python twin
(docs/kernel_survival_2026-10.md section 4.1).

  NAMES            the buttons, in libmoy's moy_button order with the console's
                   own names after it; HOST_NAMES is the host's prefix of it
  InputSource      one producer's held set and key, a row of kind SRC
  InputTable       the merged input every surface reads, and the masks
  Pointer          a screen-space cursor: trackball-relative, touch-absolute
  pointer_state    the pointer a CART sees, one answer for every tier

ONE table for every tier. A table accepts the names its BUTTONS holds: the
boards' InputState (device/moybyte/input.py) all fifteen, the host's
(runtime/input.py) the first eight -- the browser and the host present no
console-only button, and a name outside a table's vocabulary is refused, never
ignored. Bit order belongs to whoever asks for a mask (`button_masks(order,
player)`), never to the vocabulary, which is why the two vocabularies could
not agree on an order for a year without any cart noticing.

MULTI-SOURCE. Every producer owns a NAMED SOURCE and writes only there; the
shared held set is their MERGE, computed in begin_frame():

    src = inp.source("kbd")     # "ble", "touch", "net0", ...
    src.release_all()           # I hold nothing -- NOT "everybody let go"
    src.set_held("up", True)
    src.last_key = 0x1b

A source write touches ONLY that source, and `begin_frame` is the union's one
author -- so read `held()`/`pressed()`/`button_masks()` after begin_frame, the
way the frame loop does (poll every source, then begin_frame, then
handle_input). A read between a source write and the next begin_frame answers
for the frame that is still current, which is the point: a frame's input does
not change under the code reading it. device/moybyte/input.py has the long
note on the bug the model exists for.
"""

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff
try:
    from moy_spine import KIND_SRC, Table
except ImportError:                     # host: the runtime package
    from runtime.moy_spine import KIND_SRC, Table

# libmoy's moy_button enum (lua_ext.MOY_BUTTONS, pinned against the vendored
# header by tests/test_moy_button_order.py), then the console's own buttons.
NAMES = ("left", "right", "up", "down", "a", "b", "run", "home",
         "x", "y", "stop", "save", "share", "select", "start")
HOST_NAMES = NAMES[:8]


class InputSource:
    """One producer's half of the input: its own held set and its own key.

    `release_all` here means "I hold nothing", which is NOT the shared
    object's "everybody let go" -- the two meanings were conflated in one
    method and are separated on purpose. A driver clears ITS source; a modal
    that wants every button dropped calls the shared InputState.release_all().
    """

    def __init__(self, state, name, player=0):
        self.state = state
        self.name = name
        self._player = player
        self._held = set()
        self._key = 0
        self._names = state.BUTTONS     # the vocabulary this table accepts
        self.h = 0                      # its row in the table's SRC rows

    @property
    def player(self):
        return self._player

    @player.setter
    def player(self, value):
        # A property so the state can re-scan WHO is on which slot exactly
        # when that changes, instead of re-deriving it from every source on
        # every frame. Assigning a player is a configuration event (a keyboard
        # is paired, a pad is plugged in); the merge is a hot path.
        self._player = value
        self.state._rescan_players()

    # -- what a producer writes ------------------------------------------
    def release_all(self):
        """*I* hold nothing (my buttons, nobody else's).

        Writes THIS source and nothing else: begin_frame's merge is the
        union's one author, so there is no second copy to keep in step (and a
        driver calls this every poll, where clearing one set beats re-merging).
        """
        self._held.clear()

    def set_button(self, name, held):
        if name not in self._names:
            raise ValueError("unknown button: " + name)
        if held:
            self._held.add(name)
        else:
            self._held.discard(name)

    # The host tier's spelling of the same verb (runtime/input.py:set_held), so
    # a driver shared by both tiers can write a source without knowing which
    # InputState it landed on.
    set_held = set_button

    @property
    def last_key(self):
        return self._key

    @last_key.setter
    def last_key(self, value):
        """The merge rule, and the line the live bug was on: a source that did
        not type MUST NOT zero another source's key.

        Ownership moves on a NEW nonzero value, so the most recent keypress
        wins and a source merely re-reporting a key it already holds does not
        steal the slot from a fresher press. The owner re-asserts on every
        write (so a stray direct write to `inp.last_key` heals next frame),
        and only the OWNER going quiet hands the slot to whoever else is still
        typing."""
        value = value or 0
        old = self._key
        self._key = value
        st = self.state
        if value:
            if value != old or st._key_src is None:
                st._key_src = self
                st.last_key = value
            elif st._key_src is self:
                st.last_key = value
        elif st._key_src is self:
            k = 0
            owner = None
            for s in st._srcs:
                if s._key:
                    k = s._key
                    owner = s
                    break
            st._key_src = owner
            st.last_key = k


class InputTable:
    BUTTONS = NAMES

    def __init__(self):
        self.pointer = None         # the screen-space Pointer, once wired
        self._rows = Table(KIND_SRC, "src")   # the sources, as SRC rows
        self._held = set()          # DERIVED: written by _merge() and
                                    # release_all() and by nothing else --
                                    # see the note above
        self._last = set()
        self._pressed = set()
        self._released = set()
        self.last_key = 0           # DERIVED, but a plain attribute: it is read
                                    # several times a frame and a property get
                                    # is ~10x a plain one on MicroPython
        self._key_src = None        # which source owns last_key right now
        self._srcs = []
        self._by_name = {}
        self._solo = 0              # the one player id, when there is only one
        self._multi = False         # True once two sources disagree about player
        self._p_held = None         # per-player views, built only when _multi
        self._p_pressed = None
        self._p_last = None
        self._kept = set()          # press edges kept for a paced cart's next tick (#217)
        self._kept_p = None
        self._taken = False         # a logic tick already took this frame's edges
        self._default = self.source("local")

    # -- sources -----------------------------------------------------------
    def source(self, name, player=0):
        """The named source a producer writes through. Idempotent: the same
        name always returns the same object, so a driver may re-resolve it."""
        s = self._by_name.get(name)
        if s is None:
            s = InputSource(self, name, player)
            s.h = self._rows.new(s)
            self._by_name[name] = s
            self._srcs.append(s)
            self._rescan_players()
        return s

    def _merge(self):
        """Union the sources into _held (and, when more than one player is
        assigned, into the per-player buckets). The ONE place the union is
        computed, and begin_frame is its only caller. In place: no per-frame set
        allocation on top of the edge math below."""
        h = self._held
        if h:
            h.clear()
        for s in self._srcs:
            sh = s._held
            if sh:
                for n in sh:           # add(), not update(): update() builds
                    h.add(n)           # an iterator on the heap
        if self._multi:
            self._merge_players()          # split out: see _player_edges

    def _rescan_players(self):
        """Which player slots the sources sit on. NOT part of the per-frame
        merge: it changes when a source is created or reassigned, which is a
        configuration event, and reading `player` off every source every frame
        was pure tax on a path that runs at 60Hz."""
        solo = None
        multi = False
        for s in self._srcs:
            p = s._player
            if solo is None:
                solo = p
            elif p != solo:
                multi = True
        self._solo = 0 if solo is None else solo
        self._multi = multi

    def _merge_players(self):
        """The per-player buckets, once two sources disagree about `player`."""
        ph = {}
        for s in self._srcs:
            b = ph.get(s._player)
            if b is None:
                b = set()
                ph[s._player] = b
            if s._held:
                b.update(s._held)
        self._p_held = ph

    def begin_frame(self):
        self._merge()
        held = self._held
        last = self._last
        # The edge sets are rewritten IN PLACE: this runs every loop frame,
        # and set arithmetic here would be three new sets a frame for the
        # collector to find.
        pressed = self._pressed
        released = self._released
        if pressed:
            pressed.clear()
        if released:
            released.clear()
        for n in held:
            if n not in last:
                pressed.add(n)
        for n in last:
            if n not in held:
                released.add(n)
        if pressed or released:
            last.clear()
            for n in held:
                last.add(n)
        self._taken = False
        if self._multi:
            self._player_edges()

    # A paced cart's press edges (#217). begin_frame stays the shell's
    # per-frame edge set; the Player KEEPS a frame's edges when no logic tick
    # ran in it (a 30Hz cart on a 60Hz loop must not lose the press that
    # landed between its ticks), and the first tick of a frame takes what was
    # kept while a second tick in the same frame sees no edge at all. Nothing
    # here runs while the shell owns the input, so a menu over a parked game
    # reads fresh edges every frame.
    def keep_edges(self):
        if self._pressed:
            self._kept |= self._pressed
        pp = self._p_pressed
        if pp:
            kp = self._kept_p
            if kp is None:
                kp = self._kept_p = {}
            for p, b in pp.items():
                if b:
                    k = kp.get(p)
                    if k is None:
                        kp[p] = set(b)
                    else:
                        k |= b

    def tick_edges(self):
        if self._taken:
            self._pressed.clear()
            if self._p_pressed:
                for b in self._p_pressed.values():
                    b.clear()
            return
        self._taken = True
        if self._kept:
            self._pressed |= self._kept
            self._kept.clear()
        kp = self._kept_p
        if kp:
            pp = self._p_pressed
            for p, k in kp.items():
                if pp is not None and p in pp:
                    pp[p] |= k
            kp.clear()

    def drop_edges(self):
        self._kept.clear()
        if self._kept_p:
            self._kept_p.clear()

    # SPLIT OUT OF begin_frame ON PURPOSE, and measured: MicroPython sizes a
    # call frame from the whole function, and one that needs enough locals
    # spills it to the HEAP (the #63 call-frame tax). Inlining these seven
    # names cost begin_frame 0.7us -> 11.6us PER FRAME on a path where the
    # branch is not even taken -- a bigger regression than everything this
    # model is for. Keep begin_frame small.
    def _player_edges(self):
        """Per-player press edges, for the frame the merge just built."""
        prev = self._p_last or {}
        pp = {}
        pl = {}
        for p, b in self._p_held.items():
            old = prev.get(p)
            pp[p] = (b - old) if old else set(b)
            pl[p] = set(b)
        self._p_pressed = pp
        self._p_last = pl

    def release_all(self):
        """EVERYBODY let go -- the modal's meaning (cards_layer,
        block_editor_ui): entering a panel drops every button from every
        source. A driver saying "I hold nothing" wants source.release_all().

        The one write to `_held` outside _merge, and not a second author of it:
        it empties every SOURCE first, so the set it leaves behind is exactly
        what the next merge would build. Immediate on purpose -- callers blank
        the edge sets in the same breath, because the tap that opened the modal
        must not also act inside it."""
        for s in self._srcs:
            s._held.clear()
        self._held.clear()
        if self._p_held:
            for b in self._p_held.values():
                b.clear()

    def set_button(self, name, held):
        """Legacy single-writer shim: writes the implicit default source."""
        self._default.set_button(name, held)

    # Both spellings on the STATE too, not just on InputSource: shared code
    # (runtime/web_input.py) calls one spelling against whichever InputState it
    # was handed.
    set_held = set_button

    _mask_order = None      # the tuple _mask_bit was built from (identity key)
    _mask_bit = None

    def button_masks(self, order, player=None, out=None):
        """(held, pressed) as bitmasks over `order`, in ONE call -- moycore's
        per-frame snapshot needs exactly these two integers and was building
        them with sixteen held/pressed calls (~6.35us each here).

        Twin of runtime/input.py's, and the reason `order` is an ARGUMENT is
        this file: these two classes are not one, and their BUTTONS tuples are
        not the same tuple. The host's starts left/right/up/down (libmoy's own
        order, by luck); this one starts up/down/left/right and carries fifteen
        names. When this method packed bits in BUTTONS order, the same call
        meant two different things on the two tiers, and moycore -- which reads
        the mask against libmoy's moy_button enum -- gave every Lua cart on
        both boards a d-pad rotated a quarter turn, with `run` on a bit libmoy
        never looks at. Nothing failed: no crash, no test, no frame hash.

        So the ORDER belongs to the protocol, not to whoever is holding the
        buttons. Callers pass lua_ext.MOY_BUTTONS. See the note there.

        `player` is an argument for exactly the same reason: which slot a
        caller wants is the CALLER's business, and a mask silently packed for
        the wrong player is the same shape of failure as a mask packed in the
        wrong order -- no crash, no test, no frame hash. None means the union
        (every source, every player), which is what moycore's snapshot asks
        for and what it has always got: the two integers it reads are
        unchanged.

        `out`, a caller-owned two-slot list, is filled and returned instead
        of a new tuple: moycore asks every frame."""
        if self._mask_order is not order:
            self._mask_order = order
            self._mask_bit = {n: 1 << i for i, n in enumerate(order)}
        h = p = 0
        if player is None or not self._multi:
            if player is not None and player != self._solo:
                held = pressed = ()
            else:
                held = self._held
                pressed = self._pressed
        else:
            held = self._p_held.get(player)
            pressed = self._p_pressed.get(player) if self._p_pressed else None
            if held is None:
                held = pressed = ()
            elif pressed is None:
                pressed = ()
        bit = self._mask_bit
        for n in held:
            h |= bit.get(n, 0)
        for n in pressed:
            p |= bit.get(n, 0)
        if out is None:
            return h, p
        out[0] = h
        out[1] = p
        return out

    # -- the two read views ------------------------------------------------
    #
    # player=None is the OS/shell view: the union of EVERY source, so any
    # connected controller drives the console and no shell code has to know
    # which one. player=n is the cart view (btn(name, n)).
    def held(self, name, player=None):
        if player is None or not self._multi:
            if player is not None and player != self._solo:
                return False
            return name in self._held
        b = self._p_held.get(player)
        return b is not None and name in b

    def pressed(self, name, player=None):
        if player is None or not self._multi:
            if player is not None and player != self._solo:
                return False
            return name in self._pressed
        b = self._p_pressed.get(player) if self._p_pressed else None
        return b is not None and name in b

    def released(self, name):
        return name in self._released

    def source_players(self):
        """The distinct player slots the SOURCES are assigned to (always at
        least (0,)). PlayerRouter.count() unions this with any transport slot,
        so `players()` counts a BLE keyboard given `src.player = 1` without a
        transport ever registering anything."""
        if not self._multi:
            return (self._solo,)
        seen = []
        for s in self._srcs:
            if s._player not in seen:
                seen.append(s._player)
        return tuple(seen)

    def player_count(self):
        """How many distinct player slots the sources are assigned to (>=1)."""
        if not self._multi:
            return 1
        return len(self.source_players())


CURSOR_IDLE_MS = 2000  # hide the trackball cursor after this long with no movement
# How long a touchscreen pointer OUTLIVES the finger that made it. A touch
# panel reports a position only while it is being touched, and a cart wants a
# MOUSE: hold and drag, then let go and the pointer is still where you left it
# for a moment before it is gone. Without the linger a released finger reads as
# "no pointer" on the very next frame, which is not a mouse and is not what a
# cart's cursor, drag handle or hover highlight is written against.
POINTER_LINGER_MS = 1500


class Pointer:
    """A screen-space cursor. The trackball drives it relatively (and shows it);
    touch places it absolutely (finger is the pointer, so it stays hidden). The
    cursor auto-hides after CURSOR_IDLE_MS without trackball movement."""

    def __init__(self, w, h, idle_ms=CURSOR_IDLE_MS):
        self.w = w
        self.h = h
        self.x = w // 2
        self.y = h // 2
        self.click = False
        self.down = False         # touch/button currently held (for drag gestures)
        # Does this pointer's SOURCE report a position with no button down? A
        # mouse does (host, browser) and a touch panel does not, and that is
        # the whole of the difference: a hovering pointer never expires, a
        # touched one lingers (POINTER_LINGER_MS) and then reads as absent.
        # Set by whoever feeds a hover, so a console inherits its own answer
        # rather than being told which kind of machine it is.
        self.hovers = False
        self._sampled = _ticks_ms()
        # Did THIS frame's sample come from the input hardware, or is it a repeat
        # of the last one? A mouse always reports a level, so the host never sets
        # this False; the T-Deck's GT911 hands over ~20-30 samples/s while a
        # finger drags (it clock-stretches 20-45ms on most finger-down reads,
        # #74), which is well under the frame rate -- so its backend holds the
        # last point and marks the repeats stale. Kinetic scrolling (#113) reads
        # it: a repeat carries NO new information about finger speed, so charging
        # it a zero delta would decay the fling velocity toward nothing.
        self.fresh = True
        self.visible = True
        self.idle_ms = idle_ms
        self._last_move = _ticks_ms()

    def move(self, dx, dy):
        # Relative move from the trackball: clamp, and wake the cursor.
        self.x = max(0, min(self.w - 1, self.x + dx))
        self.y = max(0, min(self.h - 1, self.y + dy))
        self.visible = True
        self._last_move = self._sampled = _ticks_ms()

    def place(self, x, y):
        # Absolute position from touch: hit-test there, but keep the cursor
        # hidden (the finger already shows where you are).
        self.x = max(0, min(self.w - 1, x))
        self.y = max(0, min(self.h - 1, y))
        self.visible = False
        self._sampled = _ticks_ms()

    def live(self):
        """Is there a pointer for a CART to read right now?

        Held is live, hovered is live, and a just-released touch stays live for
        POINTER_LINGER_MS so a drag that ends does not teleport the cart's
        cursor into nowhere on the next frame. Past that a touch console
        honestly has no pointer, which is what `touch()` answers None/nil for.
        """
        return (self.down or self.hovers
                or _ticks_diff(_ticks_ms(), self._sampled) < POINTER_LINGER_MS)

    def tick(self, now):
        # Auto-hide once the trackball has been idle long enough.
        if self.visible and _ticks_diff(now, self._last_move) >= self.idle_ms:
            self.visible = False


# The pointer a CART sees, resolved once for every tier.
#
# Python carts reach it through cart_api's `touch()`; Lua carts reach it through
# the snapshot the glues fill (device/moycore_glue.py, runtime/lua_host.py) and
# libmoy's `touch()` reads. Those are two code paths and they must not be two
# ANSWERS -- "one cart, every tier" is the whole contract, and a pointer that
# expires on one tier and not the other breaks it silently, in a cart that draws
# a cursor.
#
# ONE INTEGER, because the Lua snapshot has one slot to say all three things in
# (see moycore_glue / moyhost_lua's h_touch). FLAGS and not a ladder: `click`
# and `down` are independent, not nested -- a scripted tap (host_api.click, the
# suites) raises the edge with the finger already lifted, and a ladder that
# assumed down >= click swallowed it, which `letter blitz` scores with.
P_NONE = 0
P_LIVE = 1                     # there is a pointer at all
P_HELD = 2                     # the finger/button is down this frame
P_CLICK = 4                    # ...and it went down THIS frame (the press edge)


def pointer_state(inp, out):
    """Fill `out` as `[x, y, state, ms]` and return it. `state` is P_*.

    `ms` is 0 and the slot is vestigial: it carried a press duration only so
    h_touch could read `held` out of it, and `held` is a flag now. The slot
    stays because it is in the C snapshot ABI, not because anything reads it.

    `out` is CALLER-OWNED and reused: this runs once per frame on the play
    path, and a fresh tuple here is an allocation the S3 charges most of a
    millisecond for when the collector comes round (#66).
    """
    out[2] = P_NONE
    # A LINKED MATCH HAS NO POINTER. Only buttons cross the radio, so a touch
    # read here would move this screen's player and not the other one's -- a
    # divergence the lockstep exchange cannot see and cannot heal, the same
    # class of bug as drawing from the shared random stream. Reporting "no
    # pointer" makes a touch-driven cart fall back to its button path, which is
    # the honest answer while two consoles share one game.
    if getattr(inp, "netplay_live", False):
        return out
    p = getattr(inp, "pointer", None)
    if p is None:
        return out
    # LIVENESS IS THE POINTER'S, always, and asking it FIRST is the whole point.
    # A touch panel reports a position only while it is touched; the pointer
    # outlives the finger by POINTER_LINGER_MS so a released drag does not
    # teleport a cart's cursor into nowhere, and a hovering source never
    # expires at all. The game-space publication below is a coordinate MAPPING
    # of this pointer, not a second opinion about whether there is one --
    # console.py republishes it with a position every frame whether or not the
    # pointer is alive, so reading liveness off it left a p8 cart holding a
    # cursor over its board forever and its d-pad stamped over every frame.
    if hasattr(p, "live") and not p.live():   # hasattr: no bound method
        return out
    # Two-domain seam (#39): the game-space publication wins where the console
    # makes one (a distinct big system canvas, or a cart with a smaller
    # canvas), so a cart reads its own viewport coordinates rather than the
    # desktop's -- and its own tap/hold flags, which an overlay may have
    # stripped on the way through.
    gp = getattr(inp, "game_pointer", None)
    if gp is not None:
        out[0], out[1] = gp[0], gp[1]
        out[2] = (P_LIVE | (P_CLICK if bool(gp[2]) else 0)
                  | (P_HELD if (len(gp) > 3 and gp[3]) else 0))
        out[3] = 0
        return out
    out[0], out[1] = p.x, p.y
    out[2] = (P_LIVE | (P_HELD if getattr(p, "down", False) else 0)
              | (P_CLICK if getattr(p, "click", False) else 0))
    out[3] = 0
    return out
