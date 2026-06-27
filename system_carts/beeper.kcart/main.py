# Beeper -- the v0.4 AUDIO demo cartridge (#16). The console is finally noisy.
#
# Three big tappable pads play sounds from this cart's sound bank (sounds.json):
#   COIN  -> sfx(0)    a rising blip
#   JUMP  -> sfx(1)    a short hop
#   THUD  -> sfx(2)    a low noise hit
# A fourth pad makes a raw tone with beep(freq) -- the no-data escape hatch. The
# "MUSIC" card (cards editor) toggles a looping background phrase via music()/
# music_stop(). The same audio API runs on host (FakeAudio/SDL) and device (I2S);
# see docs/audio_design_v04.md.

PADS = [
    ("COIN", 0, "yellow"),
    ("JUMP", 1, "green"),
    ("THUD", 2, "orange"),
]
flash = [0.0, 0.0, 0.0, 0.0]      # per-pad glow timer (the 4th = the beep pad)
auto = 0.0
which = 0


def _pad_rect(i):
    # 2x2 grid of pads
    cx = (i % 2)
    cy = (i // 2)
    return (20 + cx * 150, 60 + cy * 80, 130, 64)


def _init():
    global flash, auto, which
    flash = [0.0, 0.0, 0.0, 0.0]
    auto = 0.0
    which = 0
    if cfg("music_on", 1):
        music(0)              # start the looping background phrase
    else:
        music_stop()


def _hit(i):
    flash[i] = 0.3
    if i < 3:
        sfx(PADS[i][1])       # play SFX from this cart's bank
    else:
        beep(660, 0.12)       # raw tone -- no bank entry needed


def _update(dt):
    global auto, which
    for i in range(len(flash)):
        if flash[i] > 0:
            flash[i] = max(0.0, flash[i] - dt)
    tp = touch()
    tapped = False
    if tp is not None and tp[2]:
        for i in range(4):
            x, y, w, h = _pad_rect(i)
            if x <= tp[0] < x + w and y <= tp[1] < y + h:
                _hit(i)
                tapped = True
    # attract mode: auto-cycle the pads. OFF by default so you trigger sounds
    # yourself; set cfg("autoplay", 1) to make the simulator demo audible.
    if cfg("autoplay", 0):
        auto += dt
        if not tapped and auto > 0.6:
            auto = 0.0
            _hit(which)
            which = (which + 1) % 4


def _draw():
    cls(col("dark_blue"))
    print("BEEPER", 8, 8, col("white"), 2)
    print("TAP A PAD TO MAKE SOUND", 8, 30, col("light_grey"), 1)
    labels = ["COIN", "JUMP", "THUD", "BEEP"]
    fills = ["yellow", "green", "orange", "pink"]
    for i in range(4):
        x, y, w, h = _pad_rect(i)
        lit = flash[i] > 0
        rect(x, y, w, h, col(fills[i] if lit else "dark_grey"))
        rectb(x, y, w, h, col("white"))
        print(labels[i], x + 10, y + h // 2 - 4, col("black" if lit else "white"), 2)
    if cfg("music_on", 1):
        print("MUSIC: ON", 8, 220, col("green"), 1)
    else:
        print("MUSIC: OFF", 8, 220, col("light_grey"), 1)
