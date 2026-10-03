"""`CrashGuard` -- three strikes and a broken app or wallpaper stops being able
to take the console down with it (#160, ui_refactor_2026-08 Phase 8).

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
    frame()   -> after HEAL_FRAMES painted frames: heal()
    heal()    -> strike = 0, OPEN = None, persist
    disabled(id) -> strikes >= STRIKES
    forgive(id)  -> strikes = 0                              (the kid edited the CODE)

A cart that crashes in-process never reaches the heal either -- the crash panel
paints instead of the cart, or the backdrop drops to its fill -- so the same
counter catches both shapes with one mechanism, and "three strikes" means three
failed runs whether the console survived them or not.

## Two roles, two ledgers

`Workstation` holds two guards over the one `system.json`, each under its own
key: `app_guard` (`KEY`), armed in `Player.start` for `type: "app"` carts, and
`wallpaper_guard` (`WALLPAPER_KEY`), armed in `Wallpaper.compile` for the
backdrop. The strikes are per ROLE because the risk is: a cart that is fine
when tapped can be fatal as the backdrop, which runs it at every boot with no
kid choosing to. A wallpaper that strikes out stays playable and editable, and
an app that strikes out stays a wallpaper. Two instances also keep two in-RAM
armings, so a wallpaper picked while an app is still healing cannot take the
app's heal.

## How a struck-out cart comes back

`Player` refuses a disabled app into the ordinary error panel, whose bar carries
EDIT/CODE; the backdrop refuses a disabled wallpaper onto its solid fill and puts
up a notice naming it. Committing new source is what clears the strikes in both
roles: `Project.commit_code` -> `Workstation.forgive_app` /
`Workstation.forgive_wallpaper` -> `forgive` here. Code and nothing else,
because code is the only edit that can change whether the cart hangs or faults;
the reasoning is in `forgive_app`. A wallpaper has one more way back: picking it
again in Appearance is the kid saying "try again", and `select_wallpaper`
forgives it before it compiles.

## What it costs, stated plainly

An armed run costs two small `system.json` writes: one at `arm`, one at the
heal. A settings write is flash, so this is NOT free, and it is NOT armed for
games -- a game that always crashes shows the panel and the kid moves on, which
is not a brick. An app pays it per open, deliberately and rarely, beside a cart
load and a compile that already cost more. What a write costs on each board is
#154's to state: a per-write floor plus the payload, and the first write of a
boot is the cold one.

The wallpaper runs at EVERY boot, so the same bracket would put those two writes,
the cold one included, on every boot. Two writes per healthy run is the floor for
a ledger kept only in the store: the run that dies has to have written before its
code ran, and the run that lives has to leave the store different from the one
that died. So the wallpaper arms with a PROOF -- a hash of the firmware build and
the cart's source -- and the heal records it in the write it already makes. An
`arm` whose proof is on record writes nothing and returns True: the code has
already run healthy on this firmware. A wallpaper boots for free from its second
boot on, and pays the two writes again only when its source or the firmware
changes. What that gives up is attribution for a PROVEN wallpaper that starts
dying with neither changed (a hang that depends on chance or the clock, a heap
grown tight around it). A failure that repeats on every boot is the boot loop
this exists for, and code that dies on every boot never earns a proof.

`HEAL_FRAMES` is small on purpose (3): the window between "armed" and "healed"
is the window in which an unrelated power pull charges a false strike, and three
frames is ~100 ms. A false strike is cleared by the next healthy run anyway.

## Storage

One key per role inside the shell's existing `system.json` (`ws.system` +
`ws.prefs.persist`) -- no new store surface, no new file, and it inherits that
store's atomic write, so an interrupted write cannot leave a half-parsed guard
that disables everything. The state is injected as a `(store, save)` pair rather
than a `ws`, so this stays a leaf that a test can drive with a plain dict.

A build with no writable store degrades to RAM-only counting: the strikes hold
for the session and are forgotten on reboot. That is the honest floor -- a
console that cannot write cannot remember, and refusing to run apps at all
because of it would be worse than the loop.
"""

# The `system.json` keys the two roles' ledgers live under.
KEY = "app_guard"
WALLPAPER_KEY = "wallpaper_guard"


class CrashGuard:
    """Per-cart strike counting around a risky open.

    `store` is the persisted settings dict itself (`ws.system`); `save` is the
    callable that writes it (`ws.prefs.persist`); `key` is the role's slot in
    it (`KEY` or `WALLPAPER_KEY`). It used to be a zero-argument
    CALLABLE returning the dict, because the old `load_system()` REBOUND
    `ws.system` to what it read off the card and a guard holding the boot-time
    dict would have counted strikes into an object nobody persists. `SystemStore`
    owns that dict now and loads it IN PLACE (#209 landing B), so the object a
    guard is handed at construction is the object the card's contents arrive in
    -- there is nothing left for the indirection to protect against. Neither
    argument is touched at construction, so a guard built before the store is
    even wired still works."""

    # Three failed opens disable it. Deliberately not two: an app can lose one
    # open to something that is not its fault (a card pulled mid-save, a
    # first-boot migration), and a kid whose app turns itself off after one bad
    # afternoon learns the wrong lesson about their own code.
    STRIKES = 3

    # Painted frames that count as "it works". A cart that drew three frames ran
    # its body, its _init, its _update and its _draw without raising.
    HEAL_FRAMES = 3

    def __init__(self, store, save=None, key=KEY):
        self._store = store
        self._save = save
        self._key = key
        self._armed = None        # the id this run is holding a strike for
        self._proof = None        # what the heal records for it, or None
        self._frames = 0

    # -- state ---------------------------------------------------------------

    def _data(self):
        """The guard's slot inside the settings dict, created lazily.

        Tolerant of garbage: a hand-edited or half-migrated `system.json` whose
        guard slot is not a dict is REPLACED, never allowed to raise. A corrupt
        guard must not be able to do what the guard exists to prevent."""
        store = self._store
        d = store.get(self._key)
        if not isinstance(d, dict):
            d = {}
            store[self._key] = d
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

    def arm(self, cid, proof=None):
        """Record a strike against `cid` and mark it OPEN. Returns False -- and
        records nothing further -- when `cid` is already disabled.

        Called BEFORE the cart's code runs, which is the whole point: the mark
        has to survive a death the interpreter never gets to observe.

        `proof` names exactly what is about to run. When the last heal of
        `cid` recorded the same proof, the run is not bracketed at all: True,
        no strike, no write (the module docstring's "What it costs").

        Re-arming the id this guard is still holding is the SAME attempt, not
        a new one: the process that armed it is alive to ask, so that run did
        not kill the board, and it has not failed either -- a failure calls
        `release`. The strike it already holds stands for both; no write."""
        cid = str(cid)
        if self.disabled(cid):
            self.release()
            return False
        if cid == self._armed and proof == self._proof:
            self._frames = 0
            return True
        d = self._data()
        if proof is not None:
            proven = d.get("proven")
            if isinstance(proven, dict) and proven.get(cid) == proof:
                self.release()
                return True
        d["strikes"][cid] = self.strikes(cid) + 1
        d["open"] = cid
        self._armed = cid
        self._proof = proof
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
        return self.heal()

    def heal(self):
        """The armed run works: clear its strikes and its OPEN mark, record its
        proof, persist. For a caller whose run has nothing left to prove after
        one frame (a static backdrop that may not be painted again for
        minutes); everyone else reaches it through `frame`."""
        cid = self._armed
        if cid is None:
            return False
        self._armed = None
        d = self._data()
        d["strikes"].pop(cid, None)      # forgiven wholesale, not decremented
        if d.get("open") == cid:
            d["open"] = None
        if self._proof is not None:
            proven = d.get("proven")
            if not isinstance(proven, dict):
                proven = {}
                d["proven"] = proven
            proven[cid] = self._proof
            self._proof = None
        self._persist()
        return True

    def release(self):
        """The run ended. Any strike it took STANDS -- an exit before the heal
        is exactly the evidence we keep. Only drops the in-RAM arming so the
        next run starts clean."""
        self._armed = None
        self._proof = None
        self._frames = 0

    def forgive(self, cid):
        """Clear `cid`'s strikes -- re-enable a cart the owner has fixed.

        Reached from `Workstation.forgive_app` / `forgive_wallpaper` when a
        code commit lands, and from `Appearance.select_wallpaper` on a
        deliberate pick.

        An arming of `cid` still held in RAM is dropped with the mark it stood
        for, so the next `arm` writes a fresh one rather than reading as the
        same attempt over a store that no longer records it."""
        cid = str(cid)
        if self._armed == cid:
            self.release()
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
        (docs/history/ui_refactor_2026-08.md), which needs a `launcher_layer` chrome
        idiom and a visual-identity call, not more guard state. Kept because
        that doc names this as the half already built, and pinned by
        `tests/test_user_apps.py`."""
        d = self._data()
        return sorted(k for k in d["strikes"] if self.disabled(k))

    def _persist(self):
        if self._save is not None:
            self._save()
