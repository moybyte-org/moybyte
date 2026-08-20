"""`CrashGuard` -- three strikes and a broken app stops being able to take the
console down with it (#160, ui_refactor_2026-08 Phase 8).

## The failure this exists for

`Player` already turns any exception a cart raises into the friendly on-canvas
panel plus the crash-to-code throw, so a cart that merely RAISES is handled and
has been since 2026-07-23. What is not handled is a cart that does not raise --
one that hangs, exhausts the heap, or trips a native fault. The Python-level
`try/except` never sees any of those; the board resets, and if the thing that
died was AUTO-RUN, it runs again on the next boot and dies again. That is a boot
loop, and on a console with no keyboard shortcut and no safe mode it is a brick.

An in-process `except` cannot detect it, by definition. The only thing that can
is a mark written to storage BEFORE the code runs and cleared after it is seen
to work, so the evidence outlives the death:

    arm(id)   -> strike++, remember `id` as OPEN, persist    (before the cart runs)
    frame()   -> after HEAL_FRAMES painted frames: strike = 0, OPEN = None, persist
    disabled(id) -> strikes >= STRIKES
    forgive(id)  -> strikes = 0                              (the kid edited the CODE)

## How a struck-out app comes back

`Player` refuses a disabled cart into the ordinary error panel, whose bar
carries EDIT/CODE -- and committing new source through that editor is what
clears the strikes: `Project.commit_code` -> `Workstation.forgive_app` ->
`forgive` here. Code and nothing else, because code is the only edit that can
change whether the cart hangs or faults; the reasoning is in `forgive_app`.

A cart that crashes in-process never reaches `HEAL_FRAMES` either -- the crash
panel paints instead of the cart -- so the same counter catches both shapes with
one mechanism, and "three strikes" means three failed opens whether the console
survived them or not.

## What it costs, stated plainly

Two small `system.json` writes per guarded open: one at `arm`, one at the heal.
On the P4 a settings write is ~800 ms of flash, so this is NOT free and it is
NOT armed for games -- a game that always crashes shows the panel and the kid
moves on, which is not a brick. It is armed for the content that runs itself, or
that the shell runs on the kid's behalf: `type: "app"` carts today (see
`Player.start`), the wallpaper next (the actual #160 report). Both are opened
deliberately and rarely, alongside a cart load and a compile that already cost
more than the write does.

`HEAL_FRAMES` is small on purpose (3): the window between "armed" and "healed"
is the window in which an unrelated power pull charges a false strike, and three
frames is ~100 ms. A false strike is cleared by the next healthy open anyway.

## Storage

One key inside the shell's existing `system.json` (`ws.system` +
`ws._persist_system`) -- no new store surface, no new file, and it inherits that
store's atomic write, so an interrupted write cannot leave a half-parsed guard
that disables everything. The state is injected as a `(store, save)` pair rather
than a `ws`, so this stays a leaf that a test can drive with a plain dict.

A build with no writable store degrades to RAM-only counting: the strikes hold
for the session and are forgotten on reboot. That is the honest floor -- a
console that cannot write cannot remember, and refusing to run apps at all
because of it would be worse than the loop.
"""

# The `system.json` key the whole guard lives under.
KEY = "app_guard"


class CrashGuard:
    """Per-cart strike counting around a risky open.

    `store` is the persisted settings dict (`ws.system`) or a zero-argument
    callable returning it; `save` is the callable that writes it
    (`ws._persist_system`). A CALLABLE is what the shell passes, because
    `load_system()` REBINDS `ws.system` to the dict it read off the card -- a
    guard holding the boot-time dict would then be counting strikes into an
    object nobody persists. Neither is touched at construction, so a guard built
    before the store is loaded still works."""

    # Three failed opens disable it. Deliberately not two: an app can lose one
    # open to something that is not its fault (a card pulled mid-save, a
    # first-boot migration), and a kid whose app turns itself off after one bad
    # afternoon learns the wrong lesson about their own code.
    STRIKES = 3

    # Painted frames that count as "it works". A cart that drew three frames ran
    # its body, its _init, its _update and its _draw without raising.
    HEAL_FRAMES = 3

    def __init__(self, store, save=None):
        self._store = store if callable(store) else (lambda: store)
        self._save = save
        self._armed = None        # the id this run is holding a strike for
        self._frames = 0

    # -- state ---------------------------------------------------------------

    def _data(self):
        """The guard's slot inside the settings dict, created lazily.

        Tolerant of garbage: a hand-edited or half-migrated `system.json` whose
        `app_guard` is not a dict is REPLACED, never allowed to raise. A corrupt
        guard must not be able to do what the guard exists to prevent."""
        store = self._store()
        d = store.get(KEY)
        if not isinstance(d, dict):
            d = {}
            store[KEY] = d
        strikes = d.get("strikes")
        if not isinstance(strikes, dict):
            d["strikes"] = {}
        return d

    def strikes(self, cid):
        """Failed opens recorded against `cid`."""
        try:
            return int(self._data()["strikes"].get(str(cid), 0))
        except (TypeError, ValueError):
            return 0

    def disabled(self, cid):
        """True when `cid` has used up its strikes and must not be run."""
        return self.strikes(cid) >= self.STRIKES

    def last_open(self):
        """The id that was OPEN when the console last stopped, or None.

        Set by `arm` and cleared by the heal, so on a fresh boot a non-None
        value means the previous run of that id never reached
        `HEAL_FRAMES` -- i.e. it crashed, hung or took the board with it.

        No shell surface reads this: acting on a bad open is the strike
        COUNT's job, and it already happens without anyone naming the cart.
        What the accessor is for is the marker itself -- `arm`/`frame`/
        `forgive` maintain it, and `tests/test_user_apps.py` observes the
        arm-heal bracket through here rather than reaching into the stored
        dict's layout."""
        return self._data().get("open")

    # -- the run bracket -----------------------------------------------------

    def arm(self, cid):
        """Record a strike against `cid` and mark it OPEN. Returns False -- and
        records nothing further -- when `cid` is already disabled.

        Called BEFORE the cart's code runs, which is the whole point: the mark
        has to survive a death the interpreter never gets to observe."""
        cid = str(cid)
        if self.disabled(cid):
            self._armed = None
            return False
        d = self._data()
        d["strikes"][cid] = self.strikes(cid) + 1
        d["open"] = cid
        self._armed = cid
        self._frames = 0
        self._persist()
        return True

    def frame(self):
        """Count one PAINTED frame of the armed run; heal at `HEAL_FRAMES`.

        Returns True on the frame that heals (nothing reads it today; it makes
        the transition testable without inspecting the store)."""
        if self._armed is None:
            return False
        self._frames += 1
        if self._frames < self.HEAL_FRAMES:
            return False
        cid = self._armed
        self._armed = None
        d = self._data()
        d["strikes"].pop(cid, None)      # forgiven wholesale, not decremented
        if d.get("open") == cid:
            d["open"] = None
        self._persist()
        return True

    def release(self):
        """The run ended. Any strike it took STANDS -- an exit before the heal
        is exactly the evidence we keep. Only drops the in-RAM arming so the
        next run starts clean."""
        self._armed = None
        self._frames = 0

    def forgive(self, cid):
        """Clear `cid`'s strikes -- re-enable an app the owner has fixed.

        Reached from `Workstation.forgive_app` when a code commit lands."""
        cid = str(cid)
        d = self._data()
        if d["strikes"].pop(cid, None) is None and d.get("open") != cid:
            return False
        if d.get("open") == cid:
            d["open"] = None
        self._persist()
        return True

    def broken_ids(self):
        """Every disabled id.

        Nothing calls it yet: the picker BADGE is Phase 8's recorded open tail
        (docs/ui_refactor_2026-08.md), which needs a `launcher_layer` chrome
        idiom and a visual-identity call, not more guard state. Kept because
        that doc names this as the half already built, and pinned by
        `tests/test_user_apps.py`."""
        d = self._data()
        return sorted(k for k in d["strikes"] if self.disabled(k))

    def _persist(self):
        if self._save is not None:
            self._save()
