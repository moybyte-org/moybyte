"""Audio sessions as the kernel's native audio face will expose them: the
Python twin (docs/kernel_survival_2026-10.md section 5.1).

  AudioSessions    a session per owner, its backend and bank; `focus` names the
                   audible one; the six verbs take the session they act on
  _SilentAudio     the no-op backend a console without a speaker plays into

A session is a row of kind AUDIO. Its owner is a tag ("cart" for the run a
Player started); opening a session for an owner that already holds one ends
the old one first, so an owner holds at most one. The verbs are SPEC.md 8.2's
six -- sfx, beep, music, music_stop, sound_stop, volume -- and each names its
session, so two sessions cannot share one global verb face.
"""

try:
    from moy_spine import KIND_AUDIO, Table
except ImportError:                     # host: the runtime package
    from runtime.moy_spine import KIND_AUDIO, Table


class AudioSessions:
    """The console's audio sessions."""

    SLOTS = 8

    def __init__(self):
        self._t = Table(KIND_AUDIO, "audio", self.SLOTS)
        self._by_owner = {}
        self.focused = 0             # the audible session's handle, 0 for none

    def open(self, owner, backend):
        """A session for `owner` over `backend` (the object the verbs reach),
        focused; the owner's previous session, if any, is ended first."""
        old = self._by_owner.get(owner)
        if old:
            self.end(old)
        h = self._t.new((owner, backend))
        self._by_owner[owner] = h
        self.focused = h
        return h

    def end(self, h):
        """End a session. Ending one that is already gone is a no-op."""
        if not self._t.valid(h):
            return
        owner, _backend = self._t.release(h)
        if self._by_owner.get(owner) == h:
            del self._by_owner[owner]
        if self.focused == h:
            self.focused = 0

    def of(self, owner):
        """The owner's live session, or 0."""
        return self._by_owner.get(owner, 0)

    def backend(self, h):
        return self._t.get(h)[1]

    def focus(self, h):
        """Make `h` the audible session (0: none)."""
        if h:
            self._t.get(h)           # a stale handle is refused, loudly
        self.focused = h

    def sfx(self, h, n, chan=None):
        return self.backend(h).sfx(n, chan)

    def beep(self, h, freq, dur=0.15):
        return self.backend(h).beep(freq, dur)

    def music(self, h, track, loop=True):
        return self.backend(h).music(track, loop)

    def music_stop(self, h):
        return self.backend(h).music_stop()

    def sound_stop(self, h, chan=None):
        return self.backend(h).sound_stop(chan)

    def volume(self, h, level):
        return self.backend(h).volume(level)


class _SilentAudio:
    """No-op audio backend (#16): wraps an AudioEngine but never produces sound.
    The default when no make_audio backend was injected. Exposes the same control
    surface the api binds to, so make_api stays identical whether or not real
    playback is wired. (Permission-gating audio on the manifest 'sound' permission
    is future work -- the v0.4 console doesn't yet enforce any cart permissions.)"""

    def __init__(self, engine):
        self.engine = engine

    def sfx(self, n, chan=None):
        pass

    def beep(self, freq, dur=0.15):
        pass

    def music(self, track, loop=True):
        pass

    def music_stop(self):
        pass

    def sound_stop(self, chan=None):
        pass

    def volume(self, level):
        pass

    def is_active(self):
        return False        # nothing is ever audible on the silent backend

    def tick(self, dt):
        pass
