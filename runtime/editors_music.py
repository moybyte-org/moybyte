"""MusicEditor (#50/#92) -- the tracker-style step editor over a cart's
AudioBank. Split out of editors.py (which re-exports it); history via the
shared editors_base discipline."""

try:
    from editors_base import UndoStack, UndoRedoMixin
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_base import UndoStack, UndoRedoMixin


_ME_REST = -1
_ME_PITCH_MIN = 0
_ME_PITCH_MAX = 95
_ME_WAVE_MIN = 0
_ME_WAVE_MAX = 3
_ME_VOL_MIN = 0
_ME_VOL_MAX = 7
_ME_SPEED_MIN = 1
_ME_SPEED_MAX = 30          # steps/slots per second (kid-sane upper bound)
_ME_STEPS_MAX = 32          # most steps a single SFX may hold
_ME_PATTERN_MAX = 32        # most slots a music track may hold
_ME_BANK_MAX = 64           # most SFX/tracks duplicate_sfx/duplicate_track may grow the bank to
_ME_UNDO_MAX = 30           # bounded in-editor undo/redo depth (#92)


def _me_clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


class MusicEditor(UndoRedoMixin):
    """Tracker/step-editor state over a cart's AudioBank (#50) -- the sound analogue
    of MapEditor/PaintEditor. Pure logic: no canvas, no synth, no I/O, so the *same*
    file backs the host console and the frozen device console. The console wraps it
    with rendering + input + live preview (it drives the injected AudioEngine; this
    core never makes sound itself).

    It edits the bank IN PLACE through two views the kid flips between:

      view == "sfx"  -- a column of STEPS for one SFX. Each step is [pitch, wave,
                        vol]; the cursor picks a step, and nudge_pitch/cycle_wave/
                        nudge_vol/toggle_rest edit it. select_sfx walks the bank's
                        SFX list (creating a fresh empty SFX past the end so the kid
                        can author new effects), and nudge_speed sets playback speed.

      view == "song" -- the looping PHRASE: a row of SLOTS, each an SFX id. The
                        cursor picks a slot; set_slot / nudge_slot point it at an SFX,
                        add_slot/del_slot grow/shrink the phrase, nudge_speed sets the
                        phrase tempo. select_track walks the bank's music tracks.

    The bank is guaranteed non-empty on construction (a default SFX + track are
    created if missing) so the grid is never blank. `dirty` tracks unsaved edits so
    the console can show a `*` and SAVE; the bank's own to_dict drives sounds.json.

    #92 adds copy/paste, whole-object duplicate, step/slot reorder, and a bounded
    in-editor undo/redo, all pure in-RAM state (no OS clipboard, no disk journal --
    that's moy_carts' separate durable per-project journal for code):

      copy/paste -- `copy()` snapshots the item under the cursor (a step in the sfx
                    view, a slot's SFX id in the song view) into `self._clip`;
                    `paste()` writes it back at the (possibly moved) cursor. A
                    mismatched clipboard kind for the active view is a no-op.

      duplicate  -- `duplicate_sfx`/`duplicate_track` deep-copy the WHOLE active
                    object into a fresh bank slot APPENDED at the end and select
                    the copy -- appended because bank indices are cross-referenced
                    ids (song slots + cart sfx(n)/music(n) calls), so a mid-bank
                    insert would retarget every later reference (the per-item
                    "duplicate" -- a new step/slot seeded from the current one --
                    already exists as add_step/add_slot).

      reorder    -- `move_step`/`move_slot` swap the cursor item with its neighbor
                    (d = +-1) and move the cursor along with it; a no-op at either
                    edge (mirrors add/del's "keep at least one" edge behavior).

      undo/redo  -- a bounded stack (depth _ME_UNDO_MAX) of snapshots of just the
                    ACTIVE object's editable fields (steps/pattern + speed + loop),
                    pushed before every content-mutating call. Switching which
                    SFX/track is active, or duplicating one, is navigation, not a
                    tracked edit -- undo only walks back through what was DONE to
                    the object currently open, restoring the object + cursor + view
                    the edit happened in (so an undo after `select_sfx` still finds
                    its way back). A fresh edit after an undo drops the redo tail,
                    same rule as moy_carts' durable journal."""

    SFX_VIEW = "sfx"
    SONG_VIEW = "song"

    def __init__(self, bank, sfx_factory=None, track_factory=None):
        # `bank` is an audio.AudioBank. The factories build a fresh SFX / MusicTrack
        # WITHOUT importing audio here (dependency-free): the console passes them, or
        # we fall back to cloning the type of an existing entry. They take no args and
        # return an empty-ish SFX / MusicTrack.
        self.bank = bank
        self._sfx_factory = sfx_factory
        self._track_factory = track_factory
        self.view = self.SFX_VIEW
        self.sfx_idx = 0          # which SFX is being edited (sfx view)
        self.step = 0             # selected step within that SFX (sfx view)
        self.track_idx = 0        # which music track is being edited (song view)
        self.slot = 0             # selected slot within that track (song view)
        self.dirty = False
        self._clip = None         # internal clipboard: ("step", [p, w, v]) | ("slot", id)
        # bounded snapshot stacks (#92) over the shared discipline -- see docstring
        self._hist = UndoStack(_ME_UNDO_MAX)
        self._ensure_nonempty()

    # -- bank bootstrapping --------------------------------------------------
    def _new_sfx(self):
        """A fresh, single-rest SFX (so a new effect has one editable step)."""
        if self._sfx_factory is not None:
            s = self._sfx_factory()
        elif self.bank.sfx:
            s = type(self.bank.sfx[0])()      # clone the concrete SFX class, empty
        else:
            return None
        if not s.steps:
            s.steps = [[_ME_REST, _ME_WAVE_MIN, _ME_VOL_MAX - 1]]
        return s

    def _new_track(self):
        """A fresh music track with one slot pointing at SFX 0."""
        if self._track_factory is not None:
            t = self._track_factory()
        elif self.bank.music:
            t = type(self.bank.music[0])()
        else:
            return None
        if not t.pattern:
            t.pattern = [0]
        return t

    def _ensure_nonempty(self):
        """The editor must never face an empty bank/SFX/track -- seed minimal ones."""
        if not self.bank.sfx:
            s = self._new_sfx()
            if s is not None:
                self.bank.sfx.append(s)
        if not self.bank.music:
            t = self._new_track()
            if t is not None:
                self.bank.music.append(t)
        self._clamp()
        # A loaded SFX could carry zero steps; give the cursor a real step to land on.
        s = self.cur_sfx()
        if s is not None and not s.steps:
            s.steps.append([_ME_REST, _ME_WAVE_MIN, _ME_VOL_MAX - 1])

    # -- current selection ---------------------------------------------------
    def cur_sfx(self):
        if 0 <= self.sfx_idx < len(self.bank.sfx):
            return self.bank.sfx[self.sfx_idx]
        return None

    def cur_track(self):
        if 0 <= self.track_idx < len(self.bank.music):
            return self.bank.music[self.track_idx]
        return None

    def cur_step(self):
        """The [pitch, wave, vol] list under the cursor in the sfx view, or None."""
        s = self.cur_sfx()
        if s is not None and 0 <= self.step < len(s.steps):
            return s.steps[self.step]
        return None

    def step_count(self):
        s = self.cur_sfx()
        return len(s.steps) if s is not None else 0

    def slot_count(self):
        t = self.cur_track()
        return len(t.pattern) if t is not None else 0

    def _clamp(self):
        self.sfx_idx = _me_clamp(self.sfx_idx, 0, max(0, len(self.bank.sfx) - 1))
        self.track_idx = _me_clamp(self.track_idx, 0, max(0, len(self.bank.music) - 1))
        n = self.step_count()
        self.step = _me_clamp(self.step, 0, max(0, n - 1)) if n else 0
        m = self.slot_count()
        self.slot = _me_clamp(self.slot, 0, max(0, m - 1)) if m else 0

    # -- view + cursor -------------------------------------------------------
    def toggle_view(self):
        """Flip between the SFX step grid and the SONG phrase."""
        self.view = self.SONG_VIEW if self.view == self.SFX_VIEW else self.SFX_VIEW

    def select_cursor(self, i):
        """Place the cursor on step / slot index `i` (clamped) for the active view."""
        if self.view == self.SFX_VIEW:
            n = self.step_count()
            if n:
                self.step = _me_clamp(int(i), 0, n - 1)
        else:
            m = self.slot_count()
            if m:
                self.slot = _me_clamp(int(i), 0, m - 1)

    def move_cursor(self, d):
        """Move the step/slot cursor by d (honors magnitude, clamped to the ends)."""
        if self.view == self.SFX_VIEW:
            self.select_cursor(self.step + d)
        else:
            self.select_cursor(self.slot + d)

    # -- SFX selection -------------------------------------------------------
    def select_sfx(self, d):
        """Step the edited-SFX index by d. Walking PAST the last SFX appends a fresh
        empty one (so the kid grows the bank just by pressing >), then clamps. Going
        before 0 clamps at 0. Resets the step cursor to the start of the new SFX."""
        target = self.sfx_idx + d
        if target >= len(self.bank.sfx):
            s = self._new_sfx()
            if s is not None:
                self.bank.sfx.append(s)
                self.dirty = True
        self.sfx_idx = _me_clamp(target, 0, max(0, len(self.bank.sfx) - 1))
        self.step = 0
        self._clamp()

    def select_track(self, d):
        """Step the edited-track index by d; past the end appends a fresh track."""
        target = self.track_idx + d
        if target >= len(self.bank.music):
            t = self._new_track()
            if t is not None:
                self.bank.music.append(t)
                self.dirty = True
        self.track_idx = _me_clamp(target, 0, max(0, len(self.bank.music) - 1))
        self.slot = 0
        self._clamp()

    # -- SFX step edits ------------------------------------------------------
    def nudge_pitch(self, d):
        """Raise/lower the current step's pitch by d semitones. A rest stays a rest
        until toggle_rest gives it a real pitch (so nudging a rest is a no-op)."""
        st = self.cur_step()
        if st is None or st[0] < 0:
            return
        self._push_undo()
        st[0] = _me_clamp(st[0] + d, _ME_PITCH_MIN, _ME_PITCH_MAX)
        self.dirty = True

    def set_pitch(self, pitch):
        """Set the current step to an explicit pitch (a semitone index, or <0 rest)."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        st[0] = _ME_REST if pitch < 0 else _me_clamp(int(pitch), _ME_PITCH_MIN, _ME_PITCH_MAX)
        self.dirty = True

    def toggle_rest(self, default_pitch=57):
        """Toggle the current step between a rest and a real note. Leaving a rest
        restores `default_pitch` (A4=57 by default) so the kid hears something."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        if st[0] < 0:
            st[0] = _me_clamp(int(default_pitch), _ME_PITCH_MIN, _ME_PITCH_MAX)
        else:
            st[0] = _ME_REST
        self.dirty = True

    def cycle_wave(self, d=1):
        """Step the current step's waveform (square/triangle/saw/noise), wrapping."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        span = _ME_WAVE_MAX - _ME_WAVE_MIN + 1
        st[1] = _ME_WAVE_MIN + (st[1] - _ME_WAVE_MIN + d) % span
        self.dirty = True

    def nudge_vol(self, d):
        """Raise/lower the current step's volume (0=silent .. 7=loud), clamped."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        st[2] = _me_clamp(st[2] + d, _ME_VOL_MIN, _ME_VOL_MAX)
        self.dirty = True

    def cycle_vol(self, d=1):
        """Step the current step's volume with wraparound (7 -> 0), so a single
        tap-only button can cycle through every level (touch UI convenience)."""
        st = self.cur_step()
        if st is None:
            return
        self._push_undo()
        span = _ME_VOL_MAX - _ME_VOL_MIN + 1
        st[2] = _ME_VOL_MIN + (st[2] - _ME_VOL_MIN + d) % span
        self.dirty = True

    def add_step(self):
        """Append a step (a copy of the current one, or a default) to the current SFX
        and move the cursor to it. Capped at _ME_STEPS_MAX."""
        s = self.cur_sfx()
        if s is None or len(s.steps) >= _ME_STEPS_MAX:
            return
        self._push_undo()
        src = self.cur_step()
        new = list(src) if src is not None else [_ME_REST, _ME_WAVE_MIN, _ME_VOL_MAX - 1]
        s.steps.insert(self.step + 1, new)
        self.step += 1
        self.dirty = True

    def del_step(self):
        """Remove the current step (keeps at least one step so the grid never empties)."""
        s = self.cur_sfx()
        if s is None or len(s.steps) <= 1:
            return
        self._push_undo()
        del s.steps[self.step]
        if self.step >= len(s.steps):
            self.step = len(s.steps) - 1
        self.dirty = True

    def move_step(self, d):
        """Reorder (#92): swap the current step with its neighbor d away (+1 right/
        later, -1 left/earlier) and move the cursor along with it. A no-op past
        either edge, mirroring add/del's "keep at least one" boundary style."""
        s = self.cur_sfx()
        if s is None:
            return
        j = self.step + d
        if not (0 <= j < len(s.steps)):
            return
        self._push_undo()
        s.steps[self.step], s.steps[j] = s.steps[j], s.steps[self.step]
        self.step = j
        self.dirty = True

    # -- tempo / length ------------------------------------------------------
    def nudge_speed(self, d):
        """Change the playback speed of the ACTIVE object: the current SFX in the sfx
        view (steps/sec), the current track in the song view (slots/sec). Clamped."""
        obj = self.cur_sfx() if self.view == self.SFX_VIEW else self.cur_track()
        if obj is None:
            return
        self._push_undo()
        obj.speed = _me_clamp(obj.speed + d, _ME_SPEED_MIN, _ME_SPEED_MAX)
        self.dirty = True

    def toggle_loop(self):
        """Flip the loop flag of the active object (SFX in sfx view, track in song)."""
        obj = self.cur_sfx() if self.view == self.SFX_VIEW else self.cur_track()
        if obj is None:
            return
        self._push_undo()
        obj.loop = not obj.loop
        self.dirty = True

    # -- song (phrase) edits -------------------------------------------------
    def cur_slot_value(self):
        """The SFX id at the cursor slot in the song view, or None."""
        t = self.cur_track()
        if t is not None and 0 <= self.slot < len(t.pattern):
            return t.pattern[self.slot]
        return None

    def nudge_slot(self, d):
        """Point the current phrase slot at the next/previous SFX id, clamped to the
        bank's SFX range (you can only sequence effects that exist)."""
        t = self.cur_track()
        if t is None or not (0 <= self.slot < len(t.pattern)):
            return
        self._push_undo()
        hi = max(0, len(self.bank.sfx) - 1)
        t.pattern[self.slot] = _me_clamp(t.pattern[self.slot] + d, 0, hi)
        self.dirty = True

    def set_slot(self, sfx_id):
        """Set the current phrase slot to a specific SFX id (clamped to the bank)."""
        t = self.cur_track()
        if t is None or not (0 <= self.slot < len(t.pattern)):
            return
        self._push_undo()
        hi = max(0, len(self.bank.sfx) - 1)
        t.pattern[self.slot] = _me_clamp(int(sfx_id), 0, hi)
        self.dirty = True

    def add_slot(self):
        """Append a phrase slot (copying the current slot's SFX id) and move to it."""
        t = self.cur_track()
        if t is None or len(t.pattern) >= _ME_PATTERN_MAX:
            return
        self._push_undo()
        val = t.pattern[self.slot] if 0 <= self.slot < len(t.pattern) else 0
        t.pattern.insert(self.slot + 1, val)
        self.slot += 1
        self.dirty = True

    def del_slot(self):
        """Remove the current phrase slot (keeps at least one so a track always plays)."""
        t = self.cur_track()
        if t is None or len(t.pattern) <= 1:
            return
        self._push_undo()
        del t.pattern[self.slot]
        if self.slot >= len(t.pattern):
            self.slot = len(t.pattern) - 1
        self.dirty = True

    def move_slot(self, d):
        """Reorder (#92): swap the current phrase slot with its neighbor d away
        (+1 later, -1 earlier), moving the cursor along. Mirrors move_step."""
        t = self.cur_track()
        if t is None:
            return
        j = self.slot + d
        if not (0 <= j < len(t.pattern)):
            return
        self._push_undo()
        t.pattern[self.slot], t.pattern[j] = t.pattern[j], t.pattern[self.slot]
        self.slot = j
        self.dirty = True

    # -- copy / paste (#92) ---------------------------------------------------
    def copy(self):
        """Copy the item under the cursor to the internal clipboard: the current
        step (a [pitch, wave, vol] list) in the sfx view, the current slot's SFX id
        in the song view. In-RAM only, device-identical -- no OS clipboard."""
        if self.view == self.SFX_VIEW:
            st = self.cur_step()
            if st is not None:
                self._clip = ("step", list(st))
        else:
            v = self.cur_slot_value()
            if v is not None:
                self._clip = ("slot", v)

    def paste(self):
        """Paste the clipboard over the item under the cursor. A no-op if nothing
        was copied yet, or the clipboard holds the other view's kind of item
        (a copied step can't paste into a song slot and vice versa)."""
        if self._clip is None:
            return
        kind, val = self._clip
        if self.view == self.SFX_VIEW and kind == "step":
            st = self.cur_step()
            if st is None:
                return
            self._push_undo()
            st[0], st[1], st[2] = val
            self.dirty = True
        elif self.view == self.SONG_VIEW and kind == "slot":
            t = self.cur_track()
            if t is None or not (0 <= self.slot < len(t.pattern)):
                return
            self._push_undo()
            hi = max(0, len(self.bank.sfx) - 1)
            t.pattern[self.slot] = _me_clamp(int(val), 0, hi)
            self.dirty = True

    # -- whole-object duplicate (#92) ------------------------------------------
    def duplicate_sfx(self):
        """Duplicate the WHOLE current SFX (steps/speed/loop) into a fresh bank
        slot APPENDED at the end, and select the copy. Appended -- never inserted
        mid-bank -- because a bank index is a cross-referenced id: song pattern
        slots store raw SFX indices and cart code calls sfx(n) by the same
        integer, so an insert would silently retarget every later reference
        (every other grow path here appends for the same reason). The per-item
        duplicate (a new step seeded from the current one) already exists as
        add_step; this is the bank-level "clone this effect"."""
        s = self.cur_sfx()
        if s is None or len(self.bank.sfx) >= _ME_BANK_MAX:
            return
        dup = self._new_sfx()
        if dup is None:
            return
        dup.steps = [list(st) for st in s.steps]
        dup.speed = s.speed
        dup.loop = s.loop
        self.bank.sfx.append(dup)
        self.sfx_idx = len(self.bank.sfx) - 1
        self.step = 0
        self.dirty = True
        self._clamp()

    def duplicate_track(self):
        """Duplicate the WHOLE current SONG (track: pattern/speed/loop) into a
        fresh bank slot APPENDED at the end, and select the copy. Mirrors
        duplicate_sfx (same append-only rule: music(n) calls index the bank)."""
        t = self.cur_track()
        if t is None or len(self.bank.music) >= _ME_BANK_MAX:
            return
        dup = self._new_track()
        if dup is None:
            return
        dup.pattern = list(t.pattern)
        dup.speed = t.speed
        dup.loop = t.loop
        self.bank.music.append(dup)
        self.track_idx = len(self.bank.music) - 1
        self.slot = 0
        self.dirty = True
        self._clamp()

    # -- bounded in-editor undo/redo (#92) -------------------------------------
    # Scoped to the ACTIVE object's own editable fields (steps/pattern + speed +
    # loop) -- a snapshot per content edit, depth _ME_UNDO_MAX. Navigation (which
    # SFX/track is selected) and whole-object duplicate are NOT tracked; only what
    # was actually done to an object is undoable, and undoing restores the view +
    # object + cursor the edit happened in.
    def _snapshot(self):
        """Capture the active object's mutable fields + the cursor pointing at it,
        or None if there is nothing to snapshot (an empty bank)."""
        if self.view == self.SFX_VIEW:
            obj = self.cur_sfx()
            if obj is None:
                return None
            return ("sfx", self.sfx_idx, [list(st) for st in obj.steps],
                    obj.speed, obj.loop, self.step)
        obj = self.cur_track()
        if obj is None:
            return None
        return ("song", self.track_idx, list(obj.pattern),
                obj.speed, obj.loop, self.slot)

    def _snapshot_of(self, snap):
        """Snapshot the CURRENT state of the object a popped undo/redo entry names
        (its kind + bank idx) -- NOT whatever object the editor happens to be
        showing. undo/redo push this onto the opposite stack, so the pair is
        always a true before/after of the SAME object even when the kid walked
        the selection elsewhere between the edit and the undo. The cursor
        recorded is the live one when that object is still active, else the
        popped entry's (best available -- _restore clamps it anyway)."""
        kind, idx, _data, _speed, _loop, cursor = snap
        if kind == "sfx":
            if not (0 <= idx < len(self.bank.sfx)):
                return None
            obj = self.bank.sfx[idx]
            if self.view == self.SFX_VIEW and self.sfx_idx == idx:
                cursor = self.step
            return ("sfx", idx, [list(st) for st in obj.steps],
                    obj.speed, obj.loop, cursor)
        if not (0 <= idx < len(self.bank.music)):
            return None
        obj = self.bank.music[idx]
        if self.view == self.SONG_VIEW and self.track_idx == idx:
            cursor = self.slot
        return ("song", idx, list(obj.pattern), obj.speed, obj.loop, cursor)

    def _restore(self, snap):
        """Write a _snapshot() tuple back over the bank + re-point the cursor."""
        kind, idx, data, speed, loop, cursor = snap
        if kind == "sfx":
            if not (0 <= idx < len(self.bank.sfx)):
                return
            obj = self.bank.sfx[idx]
            obj.steps = [list(st) for st in data]
            obj.speed = speed
            obj.loop = loop
            self.view = self.SFX_VIEW
            self.sfx_idx = idx
            self.step = cursor
        else:
            if not (0 <= idx < len(self.bank.music)):
                return
            obj = self.bank.music[idx]
            obj.pattern = list(data)
            obj.speed = speed
            obj.loop = loop
            self.view = self.SONG_VIEW
            self.track_idx = idx
            self.slot = cursor
        self._clamp()

    def _push_undo(self):
        """Record the active object's pre-edit state. Called by every content-
        mutating method BEFORE it changes anything. A fresh edit always drops the
        redo tail (same rule as moy_carts' durable journal)."""
        snap = self._snapshot()
        if snap is None:
            return
        self._hist.push(snap)

    # undo/redo come from UndoRedoMixin over these hooks.

    def _hist_reverse(self, entry):
        """A fresh _snapshot_of the POPPED entry's object (pre-restore) -- the
        pair is a true before/after even if the selection walked elsewhere; a
        stale object (None) simply isn't stashed."""
        return self._snapshot_of(entry)

    def _hist_apply(self, snap, is_redo):
        self._restore(snap)
        self.dirty = True
