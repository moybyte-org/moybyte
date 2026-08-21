"""Minimal input state for the v0.4 userland: edge-detected buttons + pointer.

Mirrors the firmware `moybyte` input contract (held / pressed / released) so
cartridges poll the same way on host and device.

MULTI-SOURCE (the twin of device/moybyte/input.py -- read the long note there
for the bug this exists for). Every producer owns a NAMED SOURCE and writes
only there; the shared held-set is their MERGE, computed in begin_frame():

    src = inp.source("kbd")     # "ble", "touch", "net0", ...
    src.release_all()           # I hold nothing -- NOT "everybody let go"
    src.set_held("up", True)
    src.last_key = 0x1b

These two InputState classes are deliberately NOT one (different button
vocabularies, different orders, different primary verb -- set_held here,
set_button there), so the model lands in both, in step.
"""


class InputSource:
    """One producer's half of the input: its own held set and its own key."""

    def __init__(self, state, name, player=0):
        self.state = state
        self.name = name
        self._player = player
        self._held = set()
        self._key = 0

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

    def release_all(self):
        """*I* hold nothing (my buttons, nobody else's).

        Maintains the union INCREMENTALLY rather than re-merging: a driver
        calls this every poll, and a full re-merge here made the frame pay for
        two (44us each on the T-Deck's S3, measured on glass 2026-08-21).
        begin_frame's merge stays the authority."""
        h = self._held
        if not h:
            return
        st = self.state
        if st._only_holder(self):
            st._held.clear()        # nobody else holds anything: the union IS mine
            h.clear()
        else:
            self._release_shared(h, st._drop)

    def _release_shared(self, h, drop):
        """The rare half of release_all: another source is holding buttons too,
        so each of mine leaves the union only if nobody else has it."""
        while h:
            drop(h.pop())

    def set_held(self, name, down):
        if down:
            self._held.add(name)
            self.state._held.add(name)      # mirror: mid-frame reads see it
        else:
            self._held.discard(name)
            self.state._drop(name)          # ...unless another source holds it

    # The device tier's spelling of the same verb, so a driver shared by both
    # tiers can write a source without knowing which InputState it landed on.
    set_button = set_held

    @property
    def last_key(self):
        return self._key

    @last_key.setter
    def last_key(self, value):
        """Ownership moves on a NEW nonzero value, so the most recent keypress
        wins and a source merely re-reporting a key it already holds does not
        steal the slot. A source that did not type never zeroes another's key;
        only the OWNER going quiet hands the slot on."""
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


class InputState:
    BUTTONS = ("left", "right", "up", "down", "a", "b", "run", "home")

    def __init__(self):
        self._held = set()   # DERIVED: the union of the sources
        self._prev = set()
        self._pressed = set()
        self._released = set()
        self.pointer = None  # (x, y) or None
        self.last_key = 0    # last typed ASCII byte (for the shared code editor)
        self._key_src = None
        self._srcs = []
        self._by_name = {}
        self._solo = 0
        self._multi = False
        self._p_held = None
        self._p_pressed = None
        self._p_last = None
        self._default = self.source("local")

    # -- sources -----------------------------------------------------------
    def source(self, name, player=0):
        """The named source a producer writes through. Idempotent."""
        s = self._by_name.get(name)
        if s is None:
            s = InputSource(self, name, player)
            self._by_name[name] = s
            self._srcs.append(s)
            self._rescan_players()
        return s

    def _merge(self):
        """Union the sources into _held (and the per-player buckets once two
        sources disagree about `player`). The ONE place the union is computed."""
        h = self._held
        h.clear()
        for s in self._srcs:
            sh = s._held
            if sh:
                h.update(sh)
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

    def _only_holder(self, src):
        """True when no source OTHER than `src` is holding anything -- the
        universal case, and what lets a driver's release_all drop the union
        wholesale instead of testing every button against every source."""
        for s in self._srcs:
            if s is not src and s._held:
                return False
        return True

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

    def _drop(self, name):
        """A source let go of `name`: leave it in the union if anyone else
        still holds it."""
        for s in self._srcs:
            if name in s._held:
                return
        self._held.discard(name)

    def set_held(self, name, down):
        """Legacy single-writer shim: writes the implicit default source."""
        self._default.set_held(name, down)

    def begin_frame(self):
        # Snapshot for edge detection; call once per frame before polling.
        self._merge()
        held = self._held
        self._pressed = held - self._prev
        self._released = self._prev - held
        self._prev = set(held)
        if self._multi:
            self._player_edges()

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

    _mask_order = None      # the tuple _mask_bit was built from (identity key)
    _mask_bit = None

    def button_masks(self, order, player=None):
        """(held, pressed) as bitmasks over `order`, in ONE call.

        Exists because moycore's per-frame snapshot needs exactly these two
        integers and was building them with sixteen `held`/`pressed` calls --
        ~100us of pure call overhead on the S3, every frame, in the glue whose
        entire job is to stop the cart making calls like that. Walks the held
        set (usually 0-2 entries) rather than the order, so the common case is
        a couple of dict lookups.

        `order` IS REQUIRED, and that is the fix for a bug rather than an API
        preference. This method used to bit-pack in `BUTTONS` order, which read
        as tidy single-sourcing and was wrong: the boards run a DIFFERENT
        InputState whose BUTTONS is a different tuple in a different order, so
        the same call returned different bits on host and device and every Lua
        cart on both boards played with its d-pad rotated. The consumer names
        the order it wants (moycore wants lua_ext.MOY_BUTTONS -- libmoy's ABI),
        and a caller that forgets now gets a TypeError instead of wrong bits.

        `player` is an argument for the same reason: a mask silently packed for
        the wrong player fails exactly as quietly as one packed in the wrong
        order. None means the union of every source -- which is what moycore
        asks for, and the two integers it reads are unchanged.
        """
        if self._mask_order is not order:
            self._mask_order = order
            self._mask_bit = {n: 1 << i for i, n in enumerate(order)}
        if player is None or not self._multi:
            if player is not None and player != self._solo:
                return 0, 0
            held = self._held
            pressed = self._pressed
        else:
            held = self._p_held.get(player)
            pressed = self._p_pressed.get(player) if self._p_pressed else None
            if held is None:
                return 0, 0
            if pressed is None:
                pressed = ()
        h = p = 0
        bit = self._mask_bit
        for n in held:
            h |= bit.get(n, 0)
        for n in pressed:
            p |= bit.get(n, 0)
        return h, p

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

    def release_all(self):
        """EVERYBODY let go -- the modal's meaning (cards_layer,
        block_editor_ui). A driver saying "I hold nothing" wants
        source.release_all()."""
        for s in self._srcs:
            s._held.clear()
        self._held.clear()
        if self._p_held:
            for b in self._p_held.values():
                b.clear()
