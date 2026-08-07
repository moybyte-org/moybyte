"""Render a parity scenario through the NATIVE moy_audio module, under a real
MicroPython VM. The third half of the harness (see audio_parity.py).

audio_parity.py compares the host's Python twin against libmoy. This compares the
BINDING against libmoy: same scenario, same script file, but driven through
`import moy_audio` -- the actual C module the T-Deck and the web runner load,
running on MicroPython's actual VM and allocator. What it proves is that the
shim forwards the SPEC.md 8.2 verbs faithfully and that the bank survives the
one JSON crossing; what it cannot reach is I2S and the core-1 task, which need a
board.

Run under the unix port with the usermod built in -- audio_parity.py does this
for you (`native` mode), or by hand:

    ln -s <repo>/firmware/.../native/moy_audio /tmp/usermods/moy_audio
    make -C <micropython>/ports/unix VARIANT=standard \\
         BUILD=build-moyaudio USER_C_MODULES=/tmp/usermods
    build-moyaudio/micropython mp_render.py <script> <out.pcm>
"""
import sys

import moy_audio


def main():
    if len(sys.argv) != 3:
        print("usage: mp_render.py <script> <out.pcm>")
        return 2
    script, out_path = sys.argv[1], sys.argv[2]
    rate = 22050
    fo = open(out_path, "wb")
    fs = open(script, "r")
    for line in fs:
        parts = line.split()
        if not parts or parts[0][0] == "#":
            continue
        cmd = parts[0]
        args = parts[1:]
        if cmd == "rate":
            rate = int(args[0])
            moy_audio.set_rate(rate)
        elif cmd == "bank":
            fb = open(args[0], "r")
            text = fb.read()
            fb.close()
            if not moy_audio.bank_load(text):
                print("BANK LOAD FAILED:", args[0])
                return 2
            moy_audio.set_rate(rate)
        elif cmd == "sfx":
            moy_audio.sfx(int(args[0]), int(args[1]) if len(args) > 1 else -1)
        elif cmd == "beep":
            moy_audio.beep(float(args[0]), float(args[1]))
        elif cmd == "music":
            moy_audio.music(int(args[0]),
                            int(args[1]) if len(args) > 1 else 1)
        elif cmd == "music_stop":
            moy_audio.music_stop()
        elif cmd == "sound_stop":
            moy_audio.sound_stop(int(args[0]) if args else -1)
        elif cmd == "volume":
            moy_audio.volume(int(args[0]))
        elif cmd == "render":
            n = int(args[0])
            buf = bytearray(n * 2)
            moy_audio.render(buf, n)
            fo.write(buf)
        else:
            print("unknown command:", cmd)
            return 2
    fs.close()
    fo.close()
    return 0


sys.exit(main())
