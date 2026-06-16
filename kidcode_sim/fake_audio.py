"""Fake audio helpers for simulator tests."""


class FakeAudioLog:
    def __init__(self):
        self.calls = []

    def record(self, *parts):
        self.calls.append(tuple(parts))
