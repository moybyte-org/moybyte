"""The dev channel's audio words (see runtime/devch_input.py for the shape of
a word): `vol N`, the console's level, stored in its settings row and applied
to the kernel's; `hush`, every session silent from the next block."""

try:
    from audio_session import console_volume, hush as _hush
except ImportError:                     # host: the runtime package
    from runtime.audio_session import console_volume, hush as _hush


def vol(chan, ws, parts, line):
    lvl = int(parts[1]) if len(parts) == 2 else 0
    ws.system.set("volume", lvl)
    print("REMOTE vol %d" % console_volume(lvl))


def hush(chan, ws, parts, line):
    _hush()
    print("REMOTE hush")


WORDS = {"vol": vol, "hush": hush}
