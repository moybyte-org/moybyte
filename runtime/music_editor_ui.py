"""The music/sound editor's UI layer (issue #50): a tracker-style step editor
over the cart's AudioBank -- SFX view (note/wave/vol steps) and SONG view (a
phrase of SFX-id slots), a scrolling step/slot list, a right-hand edit pad, a
bottom PLAY/SAVE/LOOP/CLOSE bar, and the live preview (routed through the same
AudioEngine the running cart uses).

Extracted from Workstation (runtime/console.py), mirroring block_editor_ui.py's
BlockEditorUI / map_editor_ui.py's MapEditorUI: this class owns the music
editor's UI state (musicedit/music_preview) and its _music_*/_mu_* methods,
verbatim (no renaming), via a back-reference to the owning Workstation
(`self.ws`) for the handful of primitives it shares with the rest of the
console (canvas, _btn, _leave_menu, _leave_or_home, audio, save_sounds --
the last two stay on Workstation: `audio` is the cart's live AudioEngine
backend, also used by the running game, and `save_sounds` uses the shared
`self.save_status` field like save_code/save_sprites/save_map, not a
dedicated one, so it stays alongside them as a Workstation-level "persist
this editor's content" method). `NAMES`/`_in` are injected at construction
instead of imported back from console.py, which would be a real circular
import: console.py imports MusicEditorUI to build the one instance a
Workstation holds (same reasoning as BlockEditorUI/MapEditorUI).

Kept name-for-name with the pre-extraction Workstation methods/fields (no
renaming): Workstation._open_music/set_menu_view/open/handle_input/
handle_pointer/frame/the redraw-on-change check all just gained one level of
indirection (`self.music_ui.X` instead of `self.X`), and so did the tests
that poke the music editor's internals directly.

NOTE one asymmetry versus blocks/map, preserved as-is (not a bug to fix here):
Workstation.go_home() does NOT reset musicedit/music_preview (blocks/map's
go_home DOES reset their active editor) -- pre-existing behavior.

#92 adds copy/paste/duplicate/reorder/undo/redo, all touch-first: two extra pad
rows (COPY/PASTE, MOVE-/MOVE+) shared verbatim by both views, a DUP button
folded into the row 2 slot the song view left blank, and UNDO/REDO tucked into
the bottom bar's unused width beside PLAY/LOOP -- see MusicEditor's docstring
(runtime/editors.py) for the underlying model. Ctrl+Z/Ctrl+Y also work
(_music_input), a host-only convenience mirroring the code editor's durable
undo (code_layer.py) -- the device has no Ctrl, so the on-screen buttons are
the device-identical path."""

try:
    from editors import MusicEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import MusicEditor, KeyEdge
try:
    from audio import MusicTrack, SFX
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio import MusicTrack, SFX

# The shared pre-literate glyph vocabulary (#92 icon pass): the copy/paste/duplicate/
# reorder pads lead with a 12x12 chrome glyph; the bar UNDO/REDO draw glyph-only.
# Imported for the membership check that keeps the word label as a fallback
# (the pads row still checks it; the glyph-button body itself is chrome._gbtn).
try:
    from chrome import _GLYPHS, _gbtn as _chrome_gbtn
except ImportError:  # pragma: no cover - direct host import (chrome not yet aliased)
    from runtime.chrome import _GLYPHS, _gbtn as _chrome_gbtn

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H)

# Music / sound editor (#50): a tracker-style step editor over the cart's AudioBank.
# Two views: SFX (a vertical column of [note, wave, vol] steps for one effect) and
# SONG (a column of SFX-id slots making the looping phrase). The cursor picks a
# step/slot; the right-hand button pad edits the value under it; a bottom bar plays/
# stops the preview. Drawn 320x240 with the indexed API + petme128 font only (host ==
# device); pointer/trackball/keyboard driven, mirroring the map editor's conventions.
#
# Stage-4 bar rollout: the music editor's own top title band was dissolved into the
# unified zoned bar (tab ladder + PLAY + SAVE + X). The editing controls that used to
# live in that band -- the SFX<->SONG toggle, the object <n> stepper and the speed
# +/- -- moved DOWN into a control row in the body just below the 18px bar (they're
# editing tools, not chrome); SAVE -> the bar's SAVE icon (save_sounds), CLOSE -> the
# bar's context X. The list/pad shift to y=34 (below that control row) and keep 10 rows.
_MU_TITLE_Y = 21                   # title/SPD/status text baseline (the control row)
_MU_VIEW = (2, 19, 46, 14)         # SFX <-> SONG view toggle (control row, far left)
# Step/slot list (left): a scrolling vertical column. Each row shows the index +
# the value (a note name + wave letter + a small volume bar, or an SFX id).
_MU_LIST_X = 8
_MU_LIST_Y0 = 34                   # below the control row + the unified bar
_MU_ROW_H = 16
_MU_ROWS = 10                      # visible rows (Y0 .. above the bottom bar)
_MU_LIST_W = 150
_MU_LIST_AREA = (_MU_LIST_X, _MU_LIST_Y0, _MU_LIST_W, _MU_ROWS * _MU_ROW_H)
# Object selector (which SFX / track): < n > stepper in the control row, around the title.
_MU_OBJ_PREV = (52, 19, 16, 14)
_MU_OBJ_NEXT = (134, 19, 16, 14)
# Edit pad (right): bump the value under the cursor. Two columns of buttons.
_MU_PAD_X = 168
_MU_PAD_Y = 34
_MU_PAD_W = 68                     # one button's width
_MU_PAD_H = 22
_MU_PAD_GAP = 4
_MU_PAD_ROWS = 6                   # 0-3 = #50's edit pad, 4-5 = #92's copy/move
# Buttons (filled in by _mu_pad_rect via row index):
#   row 0: NOTE- / NOTE+  (pitch down/up, or SFX-id down/up in song view)
#   row 1: WAVE  / VOL    (cycle waveform / cycle volume) -- sfx view only
#   row 2: REST  / DUP    (toggle rest, sfx only / duplicate the WHOLE SFX or
#                          SONG into a new bank slot -- both views, #92)
#   row 3: ADD   / DEL    (insert/remove a step or slot)
#   row 4: COPY  / PASTE  (#92 -- the item under the cursor; both views)
#   row 5: MOVE- / MOVE+  (#92 -- reorder the cursor item earlier/later)
# Rows 4-5 fit the leftover vertical band below row 3 and above the bottom bar
# (34 + 6*(22+4) - 4 == 186, still clear of the y=198 PLAY/LOOP row).
_MU_SPEED_DN = (206, 19, 16, 14)   # speed - (control row, right of the SPD label)
_MU_SPEED_UP = (228, 19, 16, 14)   # speed +
# Bottom action bar: PLAY (preview) + LOOP (SAVE/CLOSE moved to the unified bar),
# plus UNDO/REDO (#92) tucked into the unused width to their right (216..320).
_MU_PLAY = (8, 198, 100, 24)
_MU_LOOP = (116, 198, 100, 24)
_MU_UNDO = (220, 198, 46, 24)
_MU_REDO = (268, 198, 46, 24)
# Note names for rendering a pitch index (semitone 0..95 -> e.g. "C4"). Sharps only,
# matching audio._NOTE_OFFSETS; kept here so the console renders labels without
# reaching into audio's private table.
_MU_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F",
                  "F#", "G", "G#", "A", "A#", "B")
# WAVE_SQUARE/TRIANGLE/SAW/NOISE + the #170 PULSE/ORGAN/TILTED/PHASER
_MU_WAVE_LABELS = ("SQ", "TRI", "SAW", "NOI", "PLS", "ORG", "TLT", "PHA")
# The pre-literate glyph for each #92 edit-pad button (icon + word), keyed by label:
# COPY/PASTE/DUP + the reorder arrows (the step/slot list is vertical, so MOVE-/MOVE+
# read as up/down). The other pad buttons (NOTE-, WAVE, ADD, ...) stay plain labels.
_MU_PAD_GLYPH = {"COPY": "copy", "PASTE": "paste", "DUP": "duplicate",
                 "MOVE-": "arr_u", "MOVE+": "arr_d"}


def _mu_note_name(pitch):
    """Render a semitone index as a note name ("C4"), or "--" for a rest (<0)."""
    if pitch is None or pitch < 0:
        return "--"
    return _MU_NOTE_NAMES[pitch % 12] + str(pitch // 12)


def _mu_pad_rect(col, row):
    """The (x, y, w, h) of edit-pad button at (col 0/1, row 0..5 -- #92 added rows
    4-5) -- the frozen 320x240 baseline (MusicLayout.pad_rect is the responsive
    equivalent)."""
    x = _MU_PAD_X + col * (_MU_PAD_W + _MU_PAD_GAP)
    y = _MU_PAD_Y + row * (_MU_PAD_H + _MU_PAD_GAP)
    return (x, y, _MU_PAD_W, _MU_PAD_H)


class MusicLayout(LayoutBase):
    """Responsive music-editor geometry (#39 step 3): the control row (view toggle /
    object stepper / speed ticks), the scrolling step/slot list, the right-hand edit
    pad and the bottom PLAY/LOOP bar, derived from the SYSTEM canvas size (w, h) +
    font scale.

    The single hard contract (mirrors Layout/CodeLayout/PaintLayout/MapLayout): at
    (w, h, fs) == (320, 240, 1) every field equals the frozen `_MU_*` module
    constant, byte for byte (the `_base` branch); the responsive formulas only run
    on a larger canvas / bigger font. The LIST is the star of the reflow: a bigger
    panel shows more steps/slots at once."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        LayoutBase.__init__(self, w, h, font_scale)
        fs = self.fs
        if self._base:
            self.title_y = _MU_TITLE_Y
            self.view_btn = _MU_VIEW
            self.obj_prev, self.obj_next = _MU_OBJ_PREV, _MU_OBJ_NEXT
            self.title_x = 74
            self.spd_x = 160
            self.speed_dn, self.speed_up = _MU_SPEED_DN, _MU_SPEED_UP
            self.status_x = 250
            self.list_x, self.list_y0 = _MU_LIST_X, _MU_LIST_Y0
            self.row_h, self.rows, self.list_w = _MU_ROW_H, _MU_ROWS, _MU_LIST_W
            self.list_area = _MU_LIST_AREA
            self.pad_x, self.pad_y = _MU_PAD_X, _MU_PAD_Y
            self.pad_w, self.pad_h, self.pad_gap = _MU_PAD_W, _MU_PAD_H, _MU_PAD_GAP
            self.play_btn, self.loop_btn = _MU_PLAY, _MU_LOOP
            self.undo_btn, self.redo_btn = _MU_UNDO, _MU_REDO
            return
        # -- responsive: the control row hangs under the bar, the bottom bar anchors
        # to the canvas floor, and the list gains rows to fill the band between ----
        bar_h = 18 * fs
        ctl_y = bar_h + 1 * fs
        self.title_y = bar_h + 3 * fs
        self.view_btn = (2 * fs, ctl_y, 46 * fs, 14 * fs)
        self.obj_prev = (52 * fs, ctl_y, 16 * fs, 14 * fs)
        self.obj_next = (134 * fs, ctl_y, 16 * fs, 14 * fs)
        self.title_x = 74 * fs
        self.spd_x = 160 * fs
        self.speed_dn = (206 * fs, ctl_y, 16 * fs, 14 * fs)
        self.speed_up = (228 * fs, ctl_y, 16 * fs, 14 * fs)
        self.status_x = 250 * fs
        bot_y = self.h - 42 * fs                  # PLAY/LOOP row (base 198)
        self.list_x = _MU_LIST_X * fs
        self.list_y0 = bar_h + 16 * fs
        self.row_h = _MU_ROW_H * fs
        self.rows = max(4, (bot_y - 4 * fs - self.list_y0) // self.row_h)
        self.list_w = _MU_LIST_W * fs
        self.list_area = (self.list_x, self.list_y0, self.list_w,
                          self.rows * self.row_h)
        self.pad_x = self.list_x + self.list_w + 10 * fs
        self.pad_y = self.list_y0
        self.pad_w, self.pad_h, self.pad_gap = 68 * fs, 22 * fs, 4 * fs
        self.play_btn = (8 * fs, bot_y, 100 * fs, 24 * fs)
        self.loop_btn = (116 * fs, bot_y, 100 * fs, 24 * fs)
        self.undo_btn = (220 * fs, bot_y, 46 * fs, 24 * fs)
        self.redo_btn = (268 * fs, bot_y, 46 * fs, 24 * fs)

    def pad_rect(self, col, row):
        """The (x, y, w, h) of edit-pad button at (col 0/1, row 0..5)."""
        x = self.pad_x + col * (self.pad_w + self.pad_gap)
        y = self.pad_y + row * (self.pad_h + self.pad_gap)
        return (x, y, self.pad_w, self.pad_h)


class MusicEditorUI:
    """The music/sound editor's UI: step/slot list + edit pad + preview + bottom
    bar (draw + input/pointer). One instance lives on Workstation
    (`self.music_ui`), built once in Workstation.__init__; `ws.music_ui.build()`
    is called lazily from `set_menu_view("music")` the first time a cart's
    music editor is opened, exactly like the pre-extraction code did inline."""

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        # Injected instead of imported back from console.py -- see module docstring.
        self._NAMES = names
        self._in = in_rect
        # `musicedit` is built lazily on first open. `music_preview` tracks what
        # the live AudioEngine is previewing so the frame loop ticks the mixer
        # and shows STOP; None when nothing is playing.
        self.musicedit = None         # MusicEditor while menu_view == "music"
        self.music_preview = None     # ("sfx", n) | ("song", track) | None (preview)
        self._mkey = KeyEdge()        # Ctrl+Z/Y edge tracker (#92)
        sc = ws.sys_canvas
        self.layout = MusicLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs):
        """Rebuild the responsive geometry (#39 step 3) -- called by ws._relayout on
        a font-scale change."""
        self.layout = MusicLayout(w, h, fs)

    def build(self):
        """Build the MusicEditor over the open cart's live AudioBank (#50): the
        SAME bank the running cart plays through, so an edit is heard immediately
        by the preview AND by the cart on resume. The bank lives on the audio
        backend's engine (ws.audio.engine.bank); SFX/MusicTrack are injected as
        factories so the editor core stays import-free. Called from
        Workstation.set_menu_view("music")."""
        ws = self.ws
        if self.musicedit is None and ws.audio is not None:
            bank = ws.audio.engine.bank
            self.musicedit = MusicEditor(bank, sfx_factory=SFX,
                                         track_factory=MusicTrack)

    def reset(self):
        """Drop the active editor + preview flag -- called from Workstation.open()
        (switching carts). NOTE: unlike blocks/map, Workstation.go_home() does NOT
        call this (pre-existing asymmetry, preserved as-is)."""
        self.musicedit = None
        self.music_preview = None

    # -- preview ---------------------------------------------------------------

    def _play_music_preview(self):
        """Preview what the cursor is on: in the SFX view play the current SFX, in
        the SONG view play the current phrase (looping). Routes through the live
        AudioEngine (the same backend the cart uses), so it sounds on the host and
        the device. The frame loop ticks the mixer + redraws while a preview is up."""
        ws = self.ws
        me = self.musicedit
        au = ws.audio
        if me is None or au is None:
            return
        au.sound_stop()                          # cut any prior preview first
        if me.view == MusicEditor.SONG_VIEW:
            au.music(me.track_idx, True)
            self.music_preview = ("song", me.track_idx)
        else:
            au.sfx(me.sfx_idx)
            self.music_preview = ("sfx", me.sfx_idx)
        ws._dirty = True

    def _stop_music_preview(self):
        """Stop any music-editor preview + clear the preview flag."""
        ws = self.ws
        if ws.audio is not None:
            ws.audio.sound_stop()
            ws.audio.music_stop()
        self.music_preview = None
        ws._dirty = True

    def _music_preview_active(self):
        """True while a music-editor preview is still producing sound (so the frame
        loop keeps ticking the mixer + redrawing the PLAY/STOP button).

        Asks the BACKEND, not `au.engine`: on the device and the web runner the
        sequencers live in libmoy, so the Python engine's voices sit idle no
        matter what the speaker is doing (#97). Backends without the hook fall
        back to their engine, which is the host's answer anyway."""
        if self.music_preview is None:
            return False
        au = self.ws.audio
        if au is None:
            return False
        hook = getattr(au, "is_active", None)
        if hook is not None:
            return bool(hook())
        eng = getattr(au, "engine", None)
        return bool(eng is not None and eng.is_active())

    # -- input -------------------------------------------------------------------

    def _music_input(self):
        """D-pad navigates the tracker (#50): up/down move the step/slot cursor,
        left/right change the value under it (pitch / SFX-id), A plays/stops the
        preview, B leaves. Tap remains the primary path; this gives the trackball +
        keyboard parity with the other editors. Called from Workstation.handle_input's
        menu_view == "music" branch."""
        ws = self.ws
        i = ws.input
        me = self.musicedit
        if me is not None:
            if i.pressed("up"):
                me.move_cursor(-1)
            if i.pressed("down"):
                me.move_cursor(1)
            song = me.view == MusicEditor.SONG_VIEW
            if i.pressed("left"):
                (me.nudge_slot if song else me.nudge_pitch)(-1)
            if i.pressed("right"):
                (me.nudge_slot if song else me.nudge_pitch)(1)
            if i.pressed("a"):
                if self.music_preview is not None:
                    self._stop_music_preview()
                else:
                    self._play_music_preview()
            # Host-only convenience (#92): Ctrl+Z/Ctrl+Y, same control bytes + same
            # "typed key, not a game button" wiring as the code editor's durable
            # undo/redo (code_layer.py) -- device has no Ctrl, so the on-screen
            # UNDO/REDO buttons (the bottom bar) are the touch-first, device-
            # identical path; this is purely a host keyboard shortcut on top.
            # Edge-detected against the previous frame's byte (the code editor's
            # _ekey_prev pattern): a LEVEL key source that holds last_key across
            # frames (e.g. a BLE keyboard) must fire ONE undo per press, not one
            # per frame -- a held Ctrl+Z would otherwise drain the whole stack.
            k = i.last_key
            self._mkey.undo_redo(k, me.undo, me.redo)
        ws._leave_or_home(ws._leave_menu)
        ws._dirty = True

    def _music_click(self, px, py):
        """Route a tap in the music editor: the step/slot list places the cursor; the
        right-hand edit pad bumps the value under it; the title-strip steppers pick the
        SFX/track + tempo; the bottom bar plays/saves/loops/closes. Mirrors _map_click's
        button-dispatch shape."""
        ws = self.ws
        me = self.musicedit
        lay = self.layout
        if me is None:
            return                         # nothing to edit; exit via the bar's X
        song = me.view == MusicEditor.SONG_VIEW
        # The step/slot list: tap a row to select it.
        if self._in(px, py, lay.list_area):
            total = me.slot_count() if song else me.step_count()
            cur = me.slot if song else me.step
            top = self._mu_visible_top(cur, total)
            row = (py - lay.list_y0) // lay.row_h
            me.select_cursor(top + row)
            return
        # Title-strip controls.
        if self._in(px, py, lay.obj_prev):
            (me.select_track if song else me.select_sfx)(-1)
            return
        if self._in(px, py, lay.obj_next):
            (me.select_track if song else me.select_sfx)(1)
            return
        if self._in(px, py, lay.speed_dn):
            me.nudge_speed(-1); return
        if self._in(px, py, lay.speed_up):
            me.nudge_speed(1); return
        if self._in(px, py, lay.view_btn):
            me.toggle_view()
            self._stop_music_preview()         # don't carry a preview across views
            return
        # The bottom action bar.
        if self._in(px, py, lay.play_btn):
            if self.music_preview is not None:
                self._stop_music_preview()
            else:
                self._play_music_preview()
            return
        if self._in(px, py, lay.loop_btn):
            me.toggle_loop(); return
        if self._in(px, py, lay.undo_btn):
            me.undo(); return
        if self._in(px, py, lay.redo_btn):
            me.redo(); return
        # The right-hand edit pad (per-view button grid).
        self._music_pad_click(px, py, song)

    def _music_pad_click(self, px, py, song):
        me = self.musicedit
        if me is None:
            return
        # Find which pad button was hit (col 0/1, row 0..5 -- #92 added rows 4-5).
        for row in range(_MU_PAD_ROWS):
            for col in range(2):
                if self._in(px, py, self.layout.pad_rect(col, row)):
                    self._music_pad_action(row, col, song)
                    return

    def _music_pad_action(self, row, col, song):
        """Apply the edit-pad button at (row, col) for the active view (#50/#92). The
        labels are wired in _draw_music_pad; this is their behavior. Rows 4-5 (copy/
        paste/reorder) and row 2 col 1 (duplicate) are shared verbatim across both
        views -- only the target object (step vs. slot, SFX vs. track) differs."""
        me = self.musicedit
        if me is None:
            return
        if row == 4:                           # COPY / PASTE (#92, both views)
            me.copy() if col == 0 else me.paste()
            return
        if row == 5:                           # MOVE- / MOVE+ (#92, both views)
            (me.move_slot if song else me.move_step)(-1 if col == 0 else 1)
            return
        if song:
            if row == 0:                       # SFX- / SFX+
                me.nudge_slot(-1 if col == 0 else 1)
            elif row == 2:                     # DUP (col 1) -- col 0 unused
                if col == 1:
                    me.duplicate_track()
            elif row == 3:                     # ADD / DEL
                me.add_slot() if col == 0 else me.del_slot()
            return
        # SFX view.
        if row == 0:                           # NOTE- / NOTE+
            me.nudge_pitch(-1 if col == 0 else 1)
        elif row == 1:                         # WAVE / VOL (both wrap: one tap cycles)
            me.cycle_wave(1) if col == 0 else me.cycle_vol(1)
        elif row == 2:                         # REST (col 0) / DUP (col 1, #92)
            me.toggle_rest() if col == 0 else me.duplicate_sfx()
        elif row == 3:                         # ADD / DEL
            me.add_step() if col == 0 else me.del_step()

    # -- drawing -------------------------------------------------------------------

    def _draw_music(self):
        """The tracker-style sound editor (#50): a title row (which SFX/track + its
        tempo), a scrolling step/slot list with the cursor highlighted, a right-hand
        edit pad, and a bottom PLAY/SAVE/LOOP/CLOSE bar. Drawn with the indexed API +
        petme128 font only, so host == device."""
        ws = self.ws
        NAMES = self._NAMES
        cv = ws.sys_canvas
        lay = self.layout
        me = self.musicedit
        # Phase 3 (visual identity v1): the warm tool surface + dark ink on the
        # shelf tiers; the frozen dark-blue body at 320x240, byte-identical.
        th = ws.theme_colors
        # Themed on EVERY responsive tier (owner ask 2026-07-23 -- the tokens
        # resolve per theme/variant); only the frozen 320x240 _base branch keeps
        # its byte-identical literals.
        light = (not lay._base) or ws.light_chrome()
        self._light = light
        ink = th["ink"] if light else NAMES["white"]
        ink_dim = th["ink_dim"] if light else NAMES["light_grey"]
        cv.cls(th["surface"] if light else NAMES["dark_blue"])
        # The old black title band is gone -- the unified bar owns the top strip
        # (drawn by _MusicLayer AFTER this). The controls that were in it now sit in
        # a control row just below the bar (lay.title_y).
        if me is None:
            cv.print("NO SOUND BANK", lay.list_x, lay.title_y, ink, 1)
            return                         # exit via the bar's context X
        song = me.view == MusicEditor.SONG_VIEW
        # Title: which object + its tempo + a dirty *.
        if song:
            obj = me.cur_track()
            title = "SONG " + str(me.track_idx)
        else:
            obj = me.cur_sfx()
            title = "SFX " + str(me.sfx_idx)
        speed = obj.speed if obj is not None else 0
        loop = bool(obj.loop) if obj is not None else False
        # View toggle (far left) | < obj > + title | SPD + tempo +/- | save status.
        ws._btn("SONG" if not song else "SFX", lay.view_btn, NAMES["dark_purple"], cv)
        ws._btn("<", lay.obj_prev, NAMES["indigo"], cv)
        cv.print(title, lay.title_x, lay.title_y, ink, 1)
        ws._btn(">", lay.obj_next, NAMES["indigo"], cv)
        cv.print("SPD " + str(speed), lay.spd_x, lay.title_y, ink_dim, 1)
        self._mu_tick(lay.speed_dn, "-")
        self._mu_tick(lay.speed_up, "+")
        # The scrolling step/slot list.
        if song:
            self._draw_music_song(me)
        else:
            self._draw_music_sfx(me)
        # Right-hand edit pad.
        self._draw_music_pad(song)
        # Bottom bar: PLAY/STOP toggles the preview; LOOP flag (SAVE/CLOSE in the bar).
        playing = self.music_preview is not None
        ws._btn("STOP" if playing else "PLAY", lay.play_btn,
                  NAMES["red"] if playing else NAMES["green"], cv)
        ws._btn("LOOP" if loop else "1X", lay.loop_btn,
                  NAMES["orange"] if loop else NAMES["dark_grey"], cv)
        # UNDO/REDO (#92) -- always tappable, same "just don't crash" style as DEL
        # on a single-step SFX (undo()/redo() are no-ops at either end of the
        # bounded stack; me.can_undo()/can_redo() are there for callers/tests that
        # want to know without tapping). Glyph-only (the bar slots are too narrow for
        # an icon + word), so the #92 icon pass reaches this pair too.
        self._gbtn("undo", "UNDO", lay.undo_btn, NAMES["dark_grey"], cv)
        self._gbtn("redo", "REDO", lay.redo_btn, NAMES["dark_grey"], cv)
        if ws.save_status:
            # Failure surface only: commit_* writes save_status on ERRORS
            # ("CAN'T SAVE...", "SYNTAX...") -- the "SAVED" happy path was
            # removed (save is invisible, #111).
            cv.print(ws.save_status[:8], lay.status_x, lay.title_y,
                     th["author"] if light else NAMES["yellow"], 1)

    def _gbtn(self, kind, label, rect, fill, cv):
        # #92 icon pass -- one shared body, chrome._gbtn.
        _chrome_gbtn(self.ws, self._NAMES, kind, label, rect, fill, cv)

    def _mu_tick(self, rect, label):
        """A small +/- tick button (smaller text than _btn for the title-strip
        nudges). Drawn as a `ui.row` with an explicit `colors=` triple: the
        palette is a frozen literal set with no theme token behind it, which is
        the case that escape hatch exists for. `pad`/`text_dy` carry the frozen
        centring -- the label is one character, so the row's own clip is a no-op
        at every font scale."""
        x, y, w, h = rect
        NAMES = self._NAMES
        fs = self.layout.fs
        _ui.row(self.ws.sys_canvas, self.ws.theme_colors, rect, label,
                colors=(NAMES["blue"], NAMES["black"], NAMES["white"]),
                pad=(w - 8 * fs) // 2, text_dy=(h - 8 * fs) // 2, fs=fs)

    def _mu_visible_top(self, cur, total):
        """First list row to show so the cursor stays in view (simple scrolloff)."""
        rows = self.layout.rows
        if total <= rows:
            return 0
        top = cur - rows // 2
        if top < 0:
            top = 0
        if top > total - rows:
            top = total - rows
        return top

    def _draw_music_sfx(self, me):
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.layout
        fs = lay.fs
        s = me.cur_sfx()
        if s is None:
            return
        total = len(s.steps)
        top = self._mu_visible_top(me.step, total)
        for vi in range(lay.rows):
            idx = top + vi
            if idx >= total:
                break
            x = lay.list_x
            y = lay.list_y0 + vi * lay.row_h
            cur = (idx == me.step)
            light = getattr(self, "_light", False)
            row_bg = self.ws.theme_colors["hilite"] if light else NAMES["indigo"]
            if cur:
                cv.rect(x, y, lay.list_w, lay.row_h - 1, row_bg)
            pitch, wave, vol = s.steps[idx][0], s.steps[idx][1], s.steps[idx][2]
            # The CURSOR row keeps light ink on its dark highlight; quiet rows use
            # the surface ink (dark on the warm surface at shelf density).
            base_ink = self.ws.theme_colors["ink"] if light else NAMES["light_grey"]
            base_dim = self.ws.theme_colors["ink_dim"] if light else NAMES["dark_grey"]
            sel_ink = self.ws.theme_colors["selection_ink"] if light \
                else NAMES["white"]
            tc = sel_ink if cur else base_ink
            cv.print("%02d" % idx, x + 2 * fs, y + 4 * fs, base_dim
                     if not cur else (sel_ink if light
                                      else NAMES["light_grey"]), 1)
            note = _mu_note_name(pitch)
            note_c = (sel_ink if light else NAMES["peach"]) if cur else (
                NAMES["brown"] if self.ws.light_chrome() else NAMES["peach"])
            cv.print(note, x + 24 * fs, y + 4 * fs, note_c if pitch >= 0 else
                     base_dim, 1)
            cv.print(_MU_WAVE_LABELS[wave & 7], x + 64 * fs, y + 4 * fs, tc, 1)
            # a little volume bar (0..7) -> up to 7 ticks
            bx = x + 96 * fs
            for v in range(7):
                off = base_dim if light else NAMES["dark_grey"]
                col = NAMES["green"] if v < vol else off
                cv.rect(bx + v * 7 * fs, y + 4 * fs, 5 * fs, 8 * fs, col)

    def _draw_music_song(self, me):
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lay = self.layout
        fs = lay.fs
        t = me.cur_track()
        if t is None:
            return
        total = len(t.pattern)
        top = self._mu_visible_top(me.slot, total)
        for vi in range(lay.rows):
            idx = top + vi
            if idx >= total:
                break
            x = lay.list_x
            y = lay.list_y0 + vi * lay.row_h
            cur = (idx == me.slot)
            light = getattr(self, "_light", False)
            th = self.ws.theme_colors
            if cur:
                cv.rect(x, y, lay.list_w, lay.row_h - 1,
                        th["hilite"] if light else NAMES["indigo"])
            row = t.pattern[idx]
            # A multi-channel row (#170) reads "SFX 3+2ch": channel 0's id plus
            # how many more channels sound this row; the editor edits channel 0.
            if isinstance(row, list):
                extra = sum(1 for s in row[1:] if s >= 0)
                sid = row[0] if row else -1
                label = "SFX %s+%dch" % (sid, extra) if extra else "SFX " + str(sid)
            else:
                label = "SFX " + str(row)
            sel_ink = th["selection_ink"] if light else NAMES["white"]
            cv.print("%02d" % idx, x + 2 * fs, y + 4 * fs,
                     (th["ink_dim"] if light else NAMES["dark_grey"])
                     if not cur else (sel_ink if light
                                      else NAMES["light_grey"]), 1)
            cv.print(label, x + 30 * fs, y + 4 * fs,
                     sel_ink if cur else
                     (th["ink"] if light else NAMES["light_grey"]), 1)

    def _draw_music_pad(self, song):
        # Two columns x six rows of edit buttons; labels differ per view. Rows 4-5
        # (COPY/PASTE, MOVE-/MOVE+) and row 2's DUP are #92 additions, shared
        # verbatim across both views (only the target object differs).
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        if song:
            labels = (("SFX-", "SFX+"), ("", ""), ("", "DUP"), ("ADD", "DEL"),
                      ("COPY", "PASTE"), ("MOVE-", "MOVE+"))
            cols = ((NAMES["blue"], NAMES["blue"]), (None, None),
                    (None, NAMES["peach"]),
                    (NAMES["dark_green"], NAMES["red"]),
                    (NAMES["indigo"], NAMES["indigo"]),
                    (NAMES["pink"], NAMES["pink"]))
        else:
            labels = (("NOTE-", "NOTE+"), ("WAVE", "VOL"), ("REST", "DUP"),
                      ("ADD", "DEL"), ("COPY", "PASTE"), ("MOVE-", "MOVE+"))
            cols = ((NAMES["blue"], NAMES["blue"]),
                    (NAMES["dark_purple"], NAMES["orange"]),
                    (NAMES["brown"], NAMES["peach"]),
                    (NAMES["dark_green"], NAMES["red"]),
                    (NAMES["indigo"], NAMES["indigo"]),
                    (NAMES["pink"], NAMES["pink"]))
        for row in range(_MU_PAD_ROWS):
            for col in range(2):
                lbl = labels[row][col]
                if not lbl:
                    continue
                rect = self.layout.pad_rect(col, row)
                fill = cols[row][col]
                # The #92 pads (COPY/PASTE/DUP/MOVE) lead with an icon + keep the word
                # (the pads are wide enough); the rest stay plain labels. A missing
                # glyph kind falls back to the plain labeled button.
                kind = _MU_PAD_GLYPH.get(lbl)
                if kind is not None and kind in _GLYPHS:
                    self.ws._icon_btn(kind, lbl, rect, fill, cv)
                else:
                    self.ws._btn(lbl, rect, fill, cv)
