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


def _meets(f, k, x, y, w, h):
    """Whether rect x, y, w, h meets one of the `k` rects in `f`."""
    for i in range(0, 4 * k, 4):
        if f[i] < x + w and x < f[i] + f[i + 2] and f[i + 1] < y + h and y < f[i + 1] + f[i + 3]:
            return True
    return False


class MusicPage:
    """The Music tab over `mu`, the workstation's `MusicEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, mu, host):
        self.mu = mu
        self.host = host
        self.pop = None                  # "speed" while its popover is open
        self.rows = []                   # the visible rows' (index, rect)
        # The steps' cursor bar and meters are the page's own pixels: a
        # frame draws the rows a step changed alone (`AppHost.rows`), never
        # a plan the engine makes without them. The page's gen says what
        # else it shows.
        host.own_rows = True
        self._o = {"gen": 0}
        self._shows = None
        self._drawn = None               # (gen, cursor, top) of the last frame drawn
        self._asked = False              # this frame asked for its rows
        self._rects = array("i", [0] * 24)
        self._floor = array("i", [0] * 64)

    # -- drawing -----------------------------------------------------------------

    def _window(self, me, song, y, foot):
        """(count, cursor, rows shown, first shown) of the steps (a song's
        slots) between y and foot: the cursor kept in the middle."""
        n = me.slot_count() if song else me.step_count()
        cur = me.slot if song else me.step
        rows = max(1, (foot - y - 2) // ROW)
        top = 0
        if n > rows:
            top = max(0, min(n - rows, cur - rows // 2))
        return n, cur, rows, top

    def _gen(self, me, song, n, rect, desk):
        """The page's gen: it moves with everything the page shows but the
        steps' cursor and their scroll."""
        obj = me.cur_track() if song else me.cur_sfx()
        shows = (id(me), me._hist.gen, song, me.sfx_idx, me.track_idx, n,
                 sound_name(me.bank, me.sfx_idx), len(me.bank.sfx),
                 obj.speed if obj is not None else 0, bool(obj.loop) if obj is not None else False,
                 self.pop, self.mu.music_preview is not None, rect, desk)
        if shows != self._shows:
            self._shows = shows
            self._o["gen"] += 1
        return self._o["gen"]

    def _plan(self, gen, x, y, cur, rows, top):
        """Ask the host for this frame's rows where the last frame drawn
        differs from it only by the cursor and the scroll: the cursor's
        rows, then and now, and the rows a scroll shows, the steps moved
        out of the last frame by the rows they scrolled."""
        self._asked = False
        d = self._drawn
        if d is None or d[0] != gen:
            return
        dt = top - d[2]
        if dt >= rows or -dt >= rows:
            return
        r = self._rects
        n = 0
        for vi in (cur - top, d[1] - top):
            if 0 <= vi < rows and (n == 0 or r[1] != y + vi * ROW):
                r[4 * n] = x
                r[4 * n + 1] = y + vi * ROW
                r[4 * n + 2] = 184
                r[4 * n + 3] = ROW
                n += 1
        if dt:
            a = rows - dt if dt > 0 else 0
            r[4 * n] = x
            r[4 * n + 1] = y + a * ROW
            r[4 * n + 2] = 184
            r[4 * n + 3] = (dt if dt > 0 else -dt) * ROW
            n += 1
        self._asked = True
        self.host.rows(r, n, (x, y, 184, rows * ROW, -dt * ROW) if dt else None)

    def draw(self, cv, th, rect, desk):
        host = self.host
        me = self.mu.musicedit
        x, y, w, h = rect
        foot = y + h - FOOT
        if me is not None:
            song = me.view == me.SONG_VIEW
            n, cur, rows, top = self._window(me, song, y + 24, foot)
            self._plan(self._gen(me, song, n, rect, desk), x, y + 24, cur, rows, top)
        host.begin(cv, th, rect, desk, False, elsewhere=self.pop is None)
        ui.at(*rect)
        ui.page("", self._o)
        if me is None:
            ui.at(*rect)
            ui.empty("music", "No sounds", ("This cart has no sound bank",))
            ui.end()
            self._drawn = None
            return
        self._head(me, song, x, y, w)
        self._steps(cv, th, me, song, x, y + 24, n, cur, rows, top)
        self._pad(me, song, x + w - 132, y + 24)
        self._foot(me, song, x, foot, w)
        if self.pop == "speed":
            self._speed(me, song)
        ui.end()
        self._drawn = (self._o["gen"], cur, top)

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

    def _steps(self, cv, th, me, song, x, y, n, cur, rows, top):
        """The rows' verbs, then their own pixels (the cursor's bar, the
        meters), which overlap none of them: on a frame of the page's rows
        only where its floor reaches."""
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
            ui.at(x + 10, ry, 20, ROW)
            ui.label("%02d" % (i + 1), {"face": "mono", "tone": None if i == cur else "dim"})
            if song:
                self._slot(me, t.pattern[i], x, ry)
            else:
                self._step(s.steps[i], x, ry)
        host = self.host
        f = self._floor
        k = host.floor_rects(f) if self._asked and host.shifting else -1
        for vi in range(rows):
            i = top + vi
            if i >= n:
                break
            ry = y + vi * ROW
            if k >= 0 and not _meets(f, k, x, ry, 184, ROW):
                continue
            if i == cur:
                cv.rect(x + 4, ry, 2, ROW, th["value"])
            if not song:
                lit = max(0, min(7, s.steps[i][2]))
                if lit < 7:
                    cv.fill_rects(_METER, -1, x + 112, ry + 5, th["surface_alt"])
                if lit:
                    cv.fill_rects(_METER, lit, x + 112, ry + 5, th["value"])

    def _step(self, step, x, ry):
        pitch, wave, vol = step[0], step[1], step[2]
        ui.at(x + 34, ry, 30, ROW)
        ui.label(note_name(pitch) if pitch >= 0 else "..", {"face": "mono"})
        ui.at(x + 66, ry, 30, ROW)
        ui.label(WAVES[wave & 7], {"face": "mono", "tone": "dim"})
        ui.at(x + 98, ry, 12, ROW)
        ui.label(str(vol), {"face": "mono"})

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
        ui.icon("loop")
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
