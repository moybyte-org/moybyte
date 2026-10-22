# Map (grep -n a name to jump there):
#   MusicPage          the handheld Music tab on the verbs
#   MusicPage.draw     the views, the object, the steps, the pad and the foot
#   MusicPage.point    a pointer sample: the verbs' taps
#   MusicPage.keys     a frame's keys while the speed popover is open
"""The handheld Editor's Music tab on the public UI API
(docs/media/look_2026-10/EDITOR_LAYOUT.md's `music` pictures): Sound and
Song as tabs, the sound or the song being edited between its chevrons (a
sound by its number and its name, moy-spec's optional `name`), its speed,
the steps (a song's slots) as rows, the edit pad, and the foot's Hear,
Loop and the step count.

The keys are the tab's own (`MusicEditorUI._music_input`: up and down the
cursor, left and right its value, A hears it) and the frame begins with no
stop holding focus; the verbs take the taps. Speed opens a popover with a
slider, the keys its own while it is open. Every edit is the core's
(`MusicEditor`), its undo step included."""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
WAVES = ("SQR", "TRI", "SAW", "NOI", "PLS", "ORG", "TLT", "PHA")

# The pad, two to a row: (icon, word, the sound view's verb, the song view's).
PAD = (
    (("arrow_down", "Note", "pitch-", None), ("arrow_up", "Note", "pitch+", None)),
    (("rotate", "Wave", "wave", None), ("music", "Vol", "vol", None)),
    (("mute", "Rest", "rest", None), ("new", "Add", "add", "add")),
    (("dup", "Dup", "dup", "dup"), ("del", "Del", "del", "del")),
    (("copy", "Copy", "copy", "copy"), ("paste", "Paste", "paste", "paste")),
    (("to_front", "Up", "up", "up"), ("to_back", "Down", "down", "down")),
)
# The song view's first row: the slot's sound, back and forward.
SONG_PAD = (("back", "Sfx", "slot-"), ("forward", "Sfx", "slot+"))

ROW = 17                         # a step's row
PAD_W, PAD_H, PAD_DX, PAD_DY = 60, 22, 64, 26
FOOT = 27                        # the foot: its rule and its buttons


def note_name(pitch):
    if pitch is None or pitch < 0:
        return "--"
    return NOTE_NAMES[pitch % 12] + str(pitch // 12)


def sound_name(bank, n):
    """Sound `n` as the tab names it: its number and its name where it has
    one, else "Sound n"."""
    s = bank.sfx[n] if 0 <= n < len(bank.sfx) else None
    name = getattr(s, "name", None)
    return "%d %s" % (n, name) if name else "Sound %d" % n


def _width(text):
    ui.face("ui")
    w = ui.text_size(text)[0]
    ui.face()
    return w


class MusicPage:
    """The Music tab over `mu`, the workstation's `MusicEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, mu, host):
        self.mu = mu
        self.host = host
        self.pop = None                  # "speed" while its popover is open
        self.rows = []                   # the visible rows' (index, rect)

    # -- drawing -----------------------------------------------------------------

    def draw(self, cv, th, rect, desk):
        host = self.host
        host.begin(cv, th, rect, desk, False, elsewhere=self.pop is None)
        ui.at(*rect)
        ui.page("")
        me = self.mu.musicedit
        if me is None:
            ui.at(*rect)
            ui.empty("music", "No sounds", ("This cart has no sound bank",))
            ui.end()
            return
        x, y, w, h = rect
        song = me.view == me.SONG_VIEW
        self._head(me, song, x, y, w)
        foot = y + h - FOOT
        self._steps(cv, th, me, song, x, y + 24, foot)
        self._pad(me, song, x + w - 132, y + 24)
        self._foot(me, song, x, foot, w)
        if self.pop == "speed":
            self._speed(me, song)
        ui.end()

    def _head(self, me, song, x, y, w):
        ui.at(x + 4, y + 2, 98, 20)
        got = ui.tabs("View", ("Sound", "Song"), 1 if song else 0)
        if got is not None and got != (1 if song else 0):
            self._view()
        if song:
            title = "Song %d" % me.track_idx
            pick = me.select_track
        else:
            title = sound_name(me.bank, me.sfx_idx)
            pick = me.select_sfx
        sx = x + 104
        ui.at(sx, y + 2, 20, 20)
        if ui.tool("back", "", {"id": "Previous"}):
            pick(-1)
            self._stop()
        tw = min(_width(title) + 12, w - 224)
        ui.at(sx + 20, y + 4, tw, 20)
        ui.label(title, {"align": "center"})
        ui.at(sx + 20 + tw, y + 2, 20, 20)
        if ui.tool("forward", "", {"id": "Next"}):
            pick(1)
            self._stop()
        obj = me.cur_track() if song else me.cur_sfx()
        word = "Speed %d" % (obj.speed if obj is not None else 0)
        tw = _width(word) + 8
        ui.at(x + w - 4 - tw, y + 2, tw, 20)
        if ui.tool("", "", {"id": "Speed", "current": self.pop == "speed"}):
            self.pop = None if self.pop == "speed" else "speed"
        ui.at(x + w - 4 - tw, y + 4, tw, 20)
        ui.label(word, {"tone": "dim", "align": "center"})

    def _steps(self, cv, th, me, song, x, y, foot):
        n = me.slot_count() if song else me.step_count()
        cur = me.slot if song else me.step
        rows = max(1, (foot - y - 2) // ROW)
        top = 0
        if n > rows:
            top = max(0, min(n - rows, cur - rows // 2))
        self.rows = []
        s = None if song else me.cur_sfx()
        t = me.cur_track() if song else None
        for vi in range(rows):
            i = top + vi
            if i >= n:
                break
            ry = y + vi * ROW
            r = (x + 4, ry, 176, ROW)
            self.rows.append((i, r))
            ui.at(*r)
            if ui.tool("", "", {"id": "Row %d" % vi, "current": i == cur}):
                me.select_cursor(i)
            if i == cur:
                cv.rect(x + 4, ry, 2, ROW, th["value"])
            ui.at(x + 10, ry, 20, ROW)
            ui.label("%02d" % (i + 1), {"face": "mono", "tone": None if i == cur else "dim"})
            if song:
                self._slot(me, t.pattern[i], x, ry)
            else:
                self._step(cv, th, s.steps[i], x, ry)

    def _step(self, cv, th, step, x, ry):
        pitch, wave, vol = step[0], step[1], step[2]
        ui.at(x + 34, ry, 30, ROW)
        ui.label(note_name(pitch) if pitch >= 0 else "..", {"face": "mono"})
        ui.at(x + 66, ry, 30, ROW)
        ui.label(WAVES[wave & 7], {"face": "mono", "tone": "dim"})
        ui.at(x + 98, ry, 12, ROW)
        ui.label(str(vol), {"face": "mono"})
        for k in range(7):
            cv.rect(x + 112 + k * 9, ry + 5, 7, ROW - 10,
                    th["value"] if k < vol else th["surface_alt"])

    def _slot(self, me, row, x, ry):
        if isinstance(row, list):
            sid = row[0] if row else -1
            extra = sum(1 for v in row[1:] if v >= 0)
        else:
            sid, extra = row, 0
        word = sound_name(me.bank, sid) if sid >= 0 else "--"
        if extra:
            word += "  +%d" % extra
        ui.at(x + 34, ry, 146, ROW)
        ui.label(word, {"face": "mono"})

    def _pad(self, me, song, x, y):
        for r, pair in enumerate(PAD):
            for c, (icon, word, verb_sfx, verb_song) in enumerate(pair):
                verb = verb_song if song else verb_sfx
                if song and r == 0:
                    icon, word, verb = SONG_PAD[c]
                if verb is None:
                    continue
                ui.at(x + c * PAD_DX, y + r * PAD_DY, PAD_W, PAD_H)
                ui.icon(icon)
                if ui.button(word, "danger" if verb == "del" else "normal",
                             {"id": "Pad %s" % verb}):
                    self.act(verb)

    def _foot(self, me, song, x, y, w):
        ui.at(x, y, w, 1)
        ui.rule()
        playing = self.mu.music_preview is not None
        word = "Stop" if playing else "Hear"
        bw = _width(word) + 34
        ui.at(x + 8, y + 4, bw, 20)
        ui.icon("close" if playing else "music")
        if ui.button(word, "normal", {"id": "Hear", "current": playing}):
            self.act("hear")
        obj = me.cur_track() if song else me.cur_sfx()
        loop = bool(obj.loop) if obj is not None else False
        lw = _width("Loop") + 34
        ui.at(x + 14 + bw, y + 4, lw, 20)
        ui.icon("rotate")
        if ui.button("Loop", "normal", {"id": "Loop", "current": loop}):
            me.toggle_loop()
        n = me.slot_count() if song else me.step_count()
        word = "%d %s" % (n, ("slot" if song else "step") + ("" if n == 1 else "s"))
        tw = _width(word)
        ui.at(x + w - 6 - tw, y + 6, tw, 20)
        ui.label(word, {"tone": "dim"})

    def _speed(self, me, song):
        obj = me.cur_track() if song else me.cur_sfx()
        if obj is None:
            self.pop = None
            return
        box = self.host.box("Speed")
        if box is not None:
            ui.at(*box)
        if not ui.popover("Speed popover", 180):
            self.pop = None
        v = ui.slider("Speed", obj.speed, 1, 30, {"id": "Speed value"})
        if v != obj.speed:
            me.nudge_speed(v - obj.speed)
        ui.popover()

    # -- the verbs ------------------------------------------------------------------

    def _view(self):
        self.mu.musicedit.toggle_view()
        self._stop()

    def _stop(self):
        if self.mu.music_preview is not None:
            self.mu._stop_music_preview()

    def act(self, verb):
        """Pad verb `verb` (its word in `PAD`), or "hear"."""
        mu = self.mu
        me = mu.musicedit
        song = me.view == me.SONG_VIEW
        if verb == "hear":
            if mu.music_preview is not None:
                mu._stop_music_preview()
            else:
                mu._play_music_preview()
        elif verb == "pitch-":
            me.nudge_pitch(-1)
        elif verb == "pitch+":
            me.nudge_pitch(1)
        elif verb == "slot-":
            me.nudge_slot(-1)
        elif verb == "slot+":
            me.nudge_slot(1)
        elif verb == "wave":
            me.cycle_wave(1)
        elif verb == "vol":
            me.cycle_vol(1)
        elif verb == "rest":
            me.toggle_rest()
        elif verb == "add":
            me.add_slot() if song else me.add_step()
        elif verb == "del":
            me.del_slot() if song else me.del_step()
        elif verb == "dup":
            me.duplicate_track() if song else me.duplicate_sfx()
        elif verb == "copy":
            me.copy()
        elif verb == "paste":
            me.paste()
        elif verb in ("up", "down"):
            (me.move_slot if song else me.move_step)(-1 if verb == "up" else 1)

    # -- input ------------------------------------------------------------------------

    def keys(self, i):
        """A frame's keys while the speed popover is open: the engine's.
        Whether the page owes a frame."""
        return self.host.keys(i)

    def point(self, px, py, click, down):
        """A pointer sample in the page's surface. Whether the page owes a
        frame."""
        return self.host.point(px, py, click, down)

    def owed(self):
        """Whether a tap or a rest frame is still to be drawn."""
        h = self.host
        return h.owed or h.tap != 0
