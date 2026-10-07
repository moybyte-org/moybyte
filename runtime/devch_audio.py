"""The dev channel's audio word: `vol` (see runtime/devch_input.py for the
shape of a word)."""


def vol(chan, ws, parts, line):
    lvl = int(parts[1]) if len(parts) == 2 else 0
    # PERSIST first, apply second. ws.audio exists only while a cart
    # holds the backend, so at the launcher this used to print "no
    # audio backend" and change nothing -- which reads as a mute that
    # worked right up until the next game started playing at full
    # volume. Storing it means the level is waiting for the backend
    # that has not been built yet (project._build_audio applies it).
    ws.system.set("volume", lvl)
    au = getattr(ws, "audio", None)
    if au is not None:
        au.volume(lvl)
    print("REMOTE vol %d%s" % (lvl, "" if au is not None else " (stored)"))


WORDS = {"vol": vol}
