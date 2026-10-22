# Map (grep -n a name to jump there):
#   MusicLayout        the Music tab's content rect
#   MusicEditorUI      the music editor's UI: the handheld page, the keys, the preview
"""The music/sound editor's UI layer (issue #50): the MusicEditor over the
cart's AudioBank -- SFX view (note/wave/vol steps) and SONG view (a phrase of
SFX-id slots) -- its keys, and the live preview (played on the same audio
session the running cart uses). On the handheld the Music tab is the page
`runtime/editor_music.py` draws; a desk Editor's Sound pane is
`runtime/editor_sound.py` over the same core, and its Sounds and Song panes
`runtime/editor_parts.py`'s.

One instance lives on Workstation (`ws.music_ui`), with a back-reference to
it for the cart's audio session and `save_sounds`. Workstation.go_home() does
not reset musicedit/music_preview (blocks/map's go_home does reset their
active editor). Ctrl+Z/Ctrl+Y walk the editor's undo (`_music_input`)."""

try:
    from editors import MusicEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import MusicEditor, KeyEdge
try:
    from audio import MusicTrack, SFX
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio import MusicTrack, SFX

try:
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui_draw as _ui_draw

try:
    from layout_base import LayoutBase
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import LayoutBase


class MusicLayout(LayoutBase):
    """The Music tab's geometry: the content rect, which the handheld page
    (`editor_music.MusicPage`) fills."""


class MusicEditorUI:
    """The music/sound editor's UI: the handheld page, the keys and the
    preview (draw + input/pointer). One instance lives on Workstation
    (`self.music_ui`), built once in Workstation.__init__; `ws.music_ui.build()`
    is called lazily from `set_menu_view("music")` the first time a cart's
    music editor is opened, exactly like the pre-extraction code did inline."""

    def __init__(self, ws, names):
        self.ws = ws
        # Injected instead of imported back from console.py -- see module docstring.
        self._NAMES = names
        # `musicedit` is built lazily on first open. `music_preview` tracks what
        # the cart's session is previewing so the frame loop ticks the mixer
        # and shows STOP; None when nothing is playing.
        self.musicedit = None         # MusicEditor while menu_view == "music"
        self.music_preview = None     # ("sfx", n) | ("song", track) | None (preview)
        self._mkey = KeyEdge()        # Ctrl+Z/Y edge tracker (#92)
        self._page = None             # the handheld's page on the verbs (editor_music.py)
        sc = ws.sys_canvas
        self.layout = MusicLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def page(self):
        """The handheld's Music page on the verbs, built at its first use."""
        if self._page is None:
            try:
                from editor_music import MusicPage
            except ImportError:  # pragma: no cover - host fallback
                from runtime.editor_music import MusicPage
            self._page = MusicPage(self, _ui_draw.AppHost("editor_music"))
        return self._page

    def pointer(self, px, py, click):
        """A pointer sample on the tab (the layer's, after the bar's): the
        page's."""
        ws = self.ws
        if self.page().point(px, py, click, ws.pointer.down):
            ws._dirty = True

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the responsive geometry (#39 step 3) -- called by ws._relayout on
        a font-scale change."""
        self.layout = MusicLayout(w, h, fs, chrome_scale=cs)

    def build(self):
        """Build the MusicEditor over the open cart's live AudioBank (#50): the
        SAME bank the running cart plays through, so an edit is heard immediately
        by the preview AND by the cart on resume. The bank is the run's audio
        session's (ws.audio.bank); SFX/MusicTrack are injected as
        factories so the editor core stays import-free. Called from
        Workstation.set_menu_view("music")."""
        ws = self.ws
        if self.musicedit is None and ws.audio is not None:
            bank = ws.audio.bank
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
        the SONG view play the current phrase (looping). Plays on the cart's own
        session, focused so it is the one heard, on every tier. The frame loop
        ticks the mixer + redraws while a preview is up."""
        ws = self.ws
        me = self.musicedit
        au = ws.audio
        if me is None or au is None:
            return
        au.focus()
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
        loop keeps ticking the mixer + redrawing the PLAY/STOP button). The
        session answers from the kernel, which holds the sequencers."""
        if self.music_preview is None:
            return False
        au = self.ws.audio
        return au is not None and au.is_active()

    # -- input -------------------------------------------------------------------

    def _music_input(self):
        """The page's popover takes the keys while it is open; otherwise
        up/down move the step/slot cursor, left/right change the value under
        it (pitch / SFX-id), A plays/stops the preview, B leaves (#50). Called
        from the Music layer's handle_input."""
        ws = self.ws
        i = ws.input
        me = self.musicedit
        changed = False
        page = self._page
        if page is not None:
            if page.pop is not None:
                if page.keys(i):
                    ws._dirty = True
                if i.pressed("b"):
                    page.pop = None
                    ws._dirty = True
                return
            if page.owed():
                changed = True
        if me is not None:
            if i.pressed("up"):
                me.move_cursor(-1)
                changed = True
            if i.pressed("down"):
                me.move_cursor(1)
                changed = True
            song = me.view == MusicEditor.SONG_VIEW
            if i.pressed("left"):
                (me.nudge_slot if song else me.nudge_pitch)(-1)
                changed = True
            if i.pressed("right"):
                (me.nudge_slot if song else me.nudge_pitch)(1)
                changed = True
            if i.pressed("a"):
                if self.music_preview is not None:
                    self._stop_music_preview()
                else:
                    self._play_music_preview()
            # Ctrl+Z/Ctrl+Y (#92), the code editor's control bytes. Edge-detected against the previous frame's byte (the code editor's
            # _ekey_prev pattern): a LEVEL key source that holds last_key across
            # frames (e.g. a BLE keyboard) must fire ONE undo per press, not one
            # per frame -- a held Ctrl+Z would otherwise drain the whole stack.
            if self._mkey.undo_redo(i.last_key, me.undo, me.redo):
                changed = True
        if i.pressed("b") or i.pressed("home"):
            changed = True
        ws._leave_or_home(ws.exit)
        if changed:
            ws._dirty = True

    # -- drawing -------------------------------------------------------------------

    def _draw_music(self):
        """The Music tab: the handheld page in the content rect."""
        lay = self.layout
        self.page().draw(self.ws.sys_canvas, self.ws.theme_colors,
                         (lay.cx, lay.cy, lay.cw, lay.ch), False)
