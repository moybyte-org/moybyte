"""Portable radio service facade."""


class RadioService:
    def __init__(self, permissions=None):
        self.permissions = permissions
        self.messages = []
        self.handler = None

    def send(self, message):
        if self.permissions is not None:
            self.permissions.require_radio()
        self.messages.append(message)

    def on_message(self, fn):
        self.handler = fn
        return fn

    def receive(self, message):
        if self.handler is not None:
            self.handler(message)
