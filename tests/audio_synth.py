"""A session of the kernel's audio driven like a standalone synth, for the
tests that hear what a bank renders (tests/test_audio.py and its siblings).

The kernel mixes only the FOCUSED session, so every call that renders focuses
this one first; two Synths advance independently, each frozen while the other
renders. Each holds an owner id of its own and closes its session when it goes.
"""

import itertools
import json

from runtime import audio_session

_OWNERS = itertools.count(1000)


def available():
    return audio_session.native() is not None


class Synth:
    def __init__(self, bank, rate=8000):
        self.bank = bank
        self.rate = int(rate)
        self.na = na = audio_session.native()
        self.h = na.open(next(_OWNERS))
        na.bank(self.h, json.dumps(bank.to_dict()))
        self._rev = bank.rev

    def __del__(self):
        try:
            self.na.close(self.h)
        except Exception:   # noqa: BLE001 -- closed already, or teardown
            pass

    def _sync(self):
        if self.bank.rev != self._rev:
            self.na.bank(self.h, json.dumps(self.bank.to_dict()))
            self._rev = self.bank.rev

    def play_sfx(self, n, chan=None):
        self._sync()
        self.na.sfx(self.h, int(n), -1 if chan is None else int(chan))

    def play_beep(self, freq, dur=0.15):
        self.na.beep(self.h, float(freq), float(dur))

    def play_music(self, track, loop=True):
        self._sync()
        m = self.bank.get_music(int(track))
        if m is None:
            return
        lp = m.loop if loop is None else bool(loop)
        self.na.music(self.h, int(track), 1 if lp else 0)

    def stop_music(self):
        self.na.music_stop(self.h)

    def stop(self, chan=None):
        self.na.stop(self.h, -1 if chan is None else int(chan))

    def set_volume(self, level):
        self.na.level(self.h, int(level))

    def active_channels(self):
        return self.na.active(self.h)

    def is_active(self):
        return self.active_channels() != 0

    def render_into(self, out, nframes):
        nframes = int(nframes)
        if nframes <= 0:
            return 0
        self.na.focus(self.h)
        self.na.set_rate(self.rate)
        return self.na.render(out, nframes)

    def render(self, nframes):
        out = bytearray(2 * max(0, int(nframes)))
        self.render_into(out, nframes)
        return bytes(out)
