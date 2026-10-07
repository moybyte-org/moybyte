"""Audio sessions as the kernel's native audio face will expose them: the
Python twin (docs/kernel_survival_2026-10.md section 5.1).

  _SilentAudio     the no-op backend a console without a speaker plays into
"""


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
