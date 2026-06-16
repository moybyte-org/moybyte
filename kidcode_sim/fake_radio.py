"""Fake radio helpers for simulator tests."""


class FakeRadioBus:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
