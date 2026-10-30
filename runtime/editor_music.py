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

from array import array

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
WAVES = ("SQR", "TRI", "SAW", "NOI", "PLS", "ORG", "TLT", "PHA")

# The pad, two to a row: (icon, word, the sound view's verb, the song view's).
PAD = (
    (("arrow_down", "Note", "pitch-", None), ("arrow_up", "Note", "pitch+", None)),
    (("rotate", "Wave", "wave", None), ("volume", "Vol", "vol", None)),
    (("mute", "Rest", "rest", None), ("new", "Add", "add", "add")),
    (("dup", "Dup", "dup", "dup"), ("del", "Del", "del", "del")),
    (("copy", "Copy", "copy", "copy"), ("paste", "Paste", "paste", "paste")),
    (("to_front", "Up", "up", "up"), ("to_back", "Down", "down", "down")),
)
# The song view's first row: the slot's sound, back and forward.
SONG_PAD = (("back", "Sfx", "slot-"), ("forward", "Sfx", "slot+"))

ROW = 17                         # a step's row
# A step's volume: seven bars, the lit ones first (`fill_rects(_METER, n)`).
_METER = array("h", [v for k in range(7) for v in (k * 9, 0, 7, ROW - 10, 0)])
PAD_W, PAD_H, PAD_DX, PAD_DY = 60, 22, 64, 26
FOOT = 27                        # the foot: its rule and its buttons

# What a frame would otherwise make, made once: the options its verbs take,
# the rows' numbers and digits, the note names.
_COLS = ("",)
_O_STEPS = {"id": "Steps", "part": (0, ROW), "bar": True}
_NUMS = tuple("%02d" % i for i in range(1, 257))
_DIGITS = tuple(str(i) for i in range(16))
_O_NUM = {"face": "mono", "tone": "dim"}
_O_MONO = {"face": "mono"}
_O_MONO_DIM = {"face": "mono", "tone": "dim"}
_O_CENTER = {"align": "center"}
_O_DIM_CENTER = {"tone": "dim", "align": "center"}
_O_DIM = {"tone": "dim"}
_O_PREV = {"id": "Previous"}
_O_NEXT = {"id": "Next"}
_O_SPEED = {"id": "Speed", "current": False}
_O_HEAR = {"id": "Hear", "current": False}
_O_LOOP = {"id": "Loop", "current": False}
_O_PAD = {}
_NOTE_NAMES = {}


def note_name(pitch):
    if pitch is None or pitch < 0:
        return "--"
    n = _NOTE_NAMES.get(pitch)
    if n is None:
        n = _NOTE_NAMES[pitch] = NOTE_NAMES[pitch % 12] + str(pitch // 12)
    return n


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


def _key(v):
    """A step's (or a song slot's) key, the row's cell its part is drawn from."""
    return str(v)


class MusicPage:
    """The Music tab over `mu`, the workstation's `MusicEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, mu, host):
        self.mu = mu
        self.host = host
        self.pop = None                  # "speed" while its popover is open
        # The steps (a song's slots) are a table whose rows are parts
        # (`ui.table_part`), each row's cell its step's key: a frame draws
        # the rows whose step or marks changed, the rest carried as drawn.
        self._rows = []                  # [key] a row
        self._src = []                   # each row's step as its key was made
        self._song = None
        self._r = array("i", [0] * 4)
        self._hc = [None, None, None, None, 0, None, None, 0]   # the head's words and widths
        self._fc = [None, None, None, None, None, None, 0]      # the foot's

    # -- drawing -----------------------------------------------------------------

    def _keys(self, me, song):
        """The table's rows: each step's key, made again only where the
        step changed."""
        src = me.cur_track().pattern if song else me.cur_sfx().steps
        rows = self._rows
        seen = self._src
        if song is self._song and src == seen:
            return rows
        if song is not self._song or len(src) != len(seen):
            self._song = song
            del rows[:]
            del seen[:]
            for v in src:
                rows.append([_key(v)])
                seen.append(list(v) if isinstance(v, list) else v)
            return rows
        for i in range(len(src)):
            v = src[i]
            if v != seen[i]:
                rows[i] = [_key(v)]
                seen[i] = list(v) if isinstance(v, list) else v
        return rows

    def draw(self, cv, th, rect, desk):
        host = self.host
        me = self.mu.musicedit
        x, y, w, h = rect
        host.begin(cv, th, rect, desk, False, elsewhere=self.pop is None)
        ui.at(x, y, w, h)
        ui.page("")
        if me is None:
            ui.at(*rect)
            ui.empty("music", "No sounds", ("This cart has no sound bank",))
            ui.end()
            return
        song = me.view == me.SONG_VIEW
        foot = y + h - FOOT
        self._head(me, song, x, y, w)
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
        idx = me.track_idx if song else me.sfx_idx
        sf = None if song else (me.bank.sfx[idx] if 0 <= idx < len(me.bank.sfx) else None)
        nm = getattr(sf, "name", None)
        hc = self._hc
        if hc[0] != song or hc[1] != idx or hc[2] is not nm:
            hc[0] = song
            hc[1] = idx
            hc[2] = nm
            hc[3] = "Song %d" % idx if song else sound_name(me.bank, idx)
            hc[4] = _width(hc[3])
        sx = x + 104
        ui.at(sx, y + 2, 20, 20)
        if ui.tool("back", "", _O_PREV):
            (me.select_track if song else me.select_sfx)(-1)
            self._stop()
        tw = min(hc[4] + 12, w - 224)
        ui.at(sx + 20, y + 4, tw, 20)
        ui.label(hc[3], _O_CENTER)
        ui.at(sx + 20 + tw, y + 2, 20, 20)
        if ui.tool("forward", "", _O_NEXT):
            (me.select_track if song else me.select_sfx)(1)
            self._stop()
        obj = me.cur_track() if song else me.cur_sfx()
        sp = obj.speed if obj is not None else 0
        if hc[5] != sp:
            hc[5] = sp
            hc[6] = "Speed %d" % sp
            hc[7] = _width(hc[6]) + 8
        tw = hc[7]
        ui.at(x + w - 4 - tw, y + 2, tw, 20)
        _O_SPEED["current"] = self.pop == "speed"
        if ui.tool("", "", _O_SPEED):
            self.pop = None if self.pop == "speed" else "speed"
        ui.at(x + w - 4 - tw, y + 4, tw, 20)
        ui.label(hc[6], _O_DIM_CENTER)

    def _steps(self, cv, th, me, song, x, y, foot):
        """The steps (a song's slots) as the table's rows, the cursor its
        current row with the cursor bar; each row its number, its step (or
        its slot's sound) and a step's volume meter, drawn in its part."""
        rows = self._keys(me, song)
        cur = me.slot if song else me.step
        ui.at(x + 4, y, 176, max(1, (foot - y - 2) // ROW) * ROW)
        got, _opened = ui.table("", _COLS, rows, cur, _O_STEPS)
        if got != cur and got >= 0:
            me.select_cursor(got)
            cur = got
        src = me.cur_track().pattern if song else me.cur_sfx().steps
        r = self._r
        i = ui.table_part(r)
        while i >= 0:
            px = r[0] - 4
            ry = r[1]
            ui.at(px + 10, ry, 20, ROW)
            ui.label(_NUMS[i] if i < 256 else str(i + 1), _O_MONO if i == cur else _O_NUM)
            if song:
                self._slot(me, src[i], px, ry)
            else:
                step = src[i]
                self._step(step, px, ry)
                lit = max(0, min(7, step[2]))
                if lit < 7:
                    cv.fill_rects(_METER, -1, px + 112, ry + 5, th["surface_alt"])
                if lit:
                    cv.fill_rects(_METER, lit, px + 112, ry + 5, th["value"])
            i = ui.table_part(r)

    def _step(self, step, x, ry):
        pitch, wave, vol = step[0], step[1], step[2]
        ui.at(x + 34, ry, 30, ROW)
        ui.label(note_name(pitch) if pitch >= 0 else "..", _O_MONO)
        ui.at(x + 66, ry, 30, ROW)
        ui.label(WAVES[wave & 7], _O_MONO_DIM)
        ui.at(x + 98, ry, 12, ROW)
        ui.label(_DIGITS[vol & 15], _O_MONO)

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
        ui.label(word, _O_MONO)

    def _pad(self, me, song, x, y):
        for r in range(len(PAD)):
            pair = PAD[r]
            for c in range(2):
                if song and r == 0:
                    e = SONG_PAD[c]
                    verb = e[2]
                else:
                    e = pair[c]
                    verb = e[3] if song else e[2]
                if verb is None:
                    continue
                ui.at(x + c * PAD_DX, y + r * PAD_DY, PAD_W, PAD_H)
                ui.icon(e[0])
                po = _O_PAD.get(verb)
                if po is None:
                    po = _O_PAD[verb] = {"id": "Pad " + verb}
                if ui.button(e[1], "danger" if verb == "del" else "normal", po):
                    self.act(verb)

    def _foot(self, me, song, x, y, w):
        ui.at(x, y, w, 1)
        ui.rule()
        playing = self.mu.music_preview is not None
        fc = self._fc
        if fc[0] is None:
            fc[0] = _width("Stop") + 34
            fc[1] = _width("Hear") + 34
            fc[2] = _width("Loop") + 34
        bw = fc[0] if playing else fc[1]
        ui.at(x + 8, y + 4, bw, 20)
        ui.icon("close" if playing else "music")
        _O_HEAR["current"] = playing
        if ui.button("Stop" if playing else "Hear", "normal", _O_HEAR):
            self.act("hear")
        obj = me.cur_track() if song else me.cur_sfx()
        ui.at(x + 14 + bw, y + 4, fc[2], 20)
        ui.icon("loop")
        _O_LOOP["current"] = bool(obj.loop) if obj is not None else False
        if ui.button("Loop", "normal", _O_LOOP):
            me.toggle_loop()
        n = me.slot_count() if song else me.step_count()
        if fc[3] != n or fc[4] != song:
            fc[3] = n
            fc[4] = song
            fc[5] = "%d %s" % (n, ("slot" if song else "step") + ("" if n == 1 else "s"))
            fc[6] = _width(fc[5])
        ui.at(x + w - 6 - fc[6], y + 6, fc[6], 20)
        ui.label(fc[5], _O_DIM)

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
