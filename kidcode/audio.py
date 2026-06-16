"""Portable audio service facade."""


class AudioService:
    def __init__(self, permissions=None):
        self.permissions = permissions
        self.calls = []

    def _check(self):
        if self.permissions is not None:
            self.permissions.require_audio()

    def beep(self):
        self._check()
        self.calls.append(("beep",))

    def play_sfx(self, name):
        self._check()
        self.calls.append(("play_sfx", name))

    def play(self, path):
        self._check()
        self.calls.append(("play", path))

    def pause(self):
        self._check()
        self.calls.append(("pause",))

    def stop(self):
        self._check()
        self.calls.append(("stop",))

    def volume(self, value):
        self._check()
        self.calls.append(("volume", value))
