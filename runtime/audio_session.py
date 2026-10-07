"""Audio sessions, as Python reaches the kernel's (native/moy_audio/moy_aud.h;
docs/kernel_survival_2026-10.md section 5).

  native()        the `moy_audio` module this tier has, or None: the usermod on
                  a board, in the browser and on the desktop MicroPython; on
                  CPython the same C over ctypes (runtime/audio_binding.py)
  AudioSession    one owner's session: its handle, the bank model it plays, the
                  six SPEC.md 8.2 verbs addressed to it
  PcmPump         where no task feeds a speaker (the host, the browser): pulls
                  the kernel's mix once a frame
  console_volume  the console's level (Settings' row), applied to every session

A session is a row of kind AUDIO in the kernel's table; an owner holds at most
one, and opening another for it closes the old, whose verbs then do nothing.
The kernel plays only the FOCUSED session; the others are muted, not stopped. On a console without
`moy_audio` a session holds no handle and its verbs do nothing: the bank model
is still there for the Music editor to edit and the store to save.
"""

import json

# The owners a session is opened for. The kernel keys "at most one session"
# on these.
OWNERS = {"cart": 1, "music_editor": 2, "wallpaper": 3}

_NA = [None]        # [module] once resolved; False: this tier has none


def native():
    """The `moy_audio` module, or None."""
    na = _NA[0]
    if na is None:
        na = False
        try:
            import moy_audio as na
        except ImportError:
            try:
                from runtime import audio_binding
                na = audio_binding.install() or False
            except ImportError:
                na = False
        if na is not False and not hasattr(na, "open"):
            na = False
        _NA[0] = na
    return na or None


def console_volume(level=None):
    """Set the console's level (0..7) when given; the level now (7 with no
    module)."""
    na = native()
    if na is None:
        return 7 if level is None else level
    return na.volume() if level is None else na.volume(int(level))


def hush():
    """Every session silent from the next block; nothing is closed."""
    na = native()
    if na is not None:
        na.hush()


def probe_line():
    """The kernel's AUDIORATE line when it has a new one, else None."""
    na = native()
    if na is None:
        return None
    try:
        return na.probe()
    except Exception:  # noqa: BLE001 -- a meter never breaks the frame
        return None


class AudioSession:
    """One owner's session over `bank` (the AudioBank the Music editor edits in
    place; a change of its `rev` reaches the kernel at the next verb)."""

    def __init__(self, bank, owner="cart", focus=True):
        self.bank = bank
        self.owner = owner
        self.h = 0
        self._na = na = native()
        self._pushed = None
        self._rev = -1
        if na is not None:
            self.h = na.open(OWNERS[owner])
            self._push()
            if focus:
                na.focus(self.h)

    def _push(self):
        b = self.bank
        self.ok = self._na.bank(self.h, json.dumps(b.to_dict()))
        self._pushed = b
        self._rev = b.rev

    def _sync(self):
        b = self.bank
        if b is not self._pushed or b.rev != self._rev:
            self._push()

    def _gone(self):
        """A newer open for this owner closed this session (on a host, another
        console in the same process): from now on its verbs do nothing."""
        self.h = 0

    # -- SPEC.md 8.2 ------------------------------------------------------

    def sfx(self, n, chan=None):
        if self.h:
            try:
                self._sync()
                self._na.sfx(self.h, int(n), -1 if chan is None else int(chan))
            except ValueError:
                self._gone()

    def beep(self, freq, dur=0.15):
        if self.h:
            freq, dur = float(freq), float(dur)
            if freq > 0.0 and dur > 0.0:
                try:
                    self._na.beep(self.h, freq, dur)
                except ValueError:
                    self._gone()

    def music(self, track, loop=True):
        if self.h:
            m = self.bank.get_music(int(track))
            if m is None:
                return
            lp = m.loop if loop is None else bool(loop)
            try:
                self._sync()
                self._na.music(self.h, int(track), 1 if lp else 0)
            except ValueError:
                self._gone()

    def music_stop(self):
        if self.h:
            try:
                self._na.music_stop(self.h)
            except ValueError:
                self._gone()

    def sound_stop(self, chan=None):
        if self.h:
            try:
                self._na.stop(self.h, -1 if chan is None else int(chan))
            except ValueError:
                self._gone()

    def volume(self, level):
        """The cart's own level, 0..7: never louder than the console's."""
        if self.h:
            try:
                self._na.level(self.h, int(level))
            except ValueError:
                self._gone()

    # -- the session ------------------------------------------------------

    def is_active(self):
        """True while anything of this session sounds."""
        if not self.h:
            return False
        try:
            return self._na.active(self.h) != 0
        except ValueError:
            self._gone()
            return False

    def focus(self):
        if self.h:
            try:
                self._na.focus(self.h)
            except ValueError:
                self._gone()

    def close(self):
        if self.h:
            try:
                self._na.close(self.h)
            except ValueError:
                pass
            self.h = 0


class PcmPump:
    """The kernel's mix pulled once a frame, where no feeder task runs: the
    host's SDL stream and headless runs, and the browser's page. `rate` is the
    rate the mix is made at; the fraction of a frame `dt` leaves over is
    carried, so the blocks add up to the rate exactly. `stream` is a compiled
    cart's `snd` queue on the host (`wasm_binding.HostWasmRun`), mixed in after
    the synth under the console's level."""

    def __init__(self, rate=11025):
        self.rate = int(rate)
        self._na = native()
        if self._na is not None:
            self._na.set_rate(self.rate)
        self._carry = 0.0
        self.rendered = 0
        self.last_pcm = b""
        self.stream = None

    def frames(self, dt):
        want = self.rate * max(0.0, dt) + self._carry
        n = int(want)
        self._carry = want - n
        return n

    def block(self, n):
        """The next `n` frames of the mix as signed 16-bit mono bytes."""
        if n <= 0:
            return b""
        buf = bytearray(2 * n)
        if self._na is not None:
            self._na.render(buf, n)
        if self.stream is not None:
            self.stream.snd_mix(buf, n, self.rate, console_volume())
        self.rendered += n
        return bytes(buf)

    def tick(self, dt):
        pcm = self.block(self.frames(dt))
        if pcm:
            self.last_pcm = pcm

    def take_pcm(self):
        """The last tick's PCM, handed off once."""
        pcm = self.last_pcm
        self.last_pcm = b""
        return pcm
