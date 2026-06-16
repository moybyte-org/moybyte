from kidcode import *

songs = [
    "assets/music/song1.mp3",
    "assets/music/song2.mp3",
    "assets/music/song3.mp3",
]
index = 0
playing = False
started = False


def start_song():
    global playing
    audio.play(songs[index])
    playing = True


@game.update
def update(dt):
    global index, playing, started

    if not started:
        started = True
        start_song()

    if button_pressed("right"):
        index = (index + 1) % len(songs)
        start_song()
    if button_pressed("left"):
        index = (index - 1) % len(songs)
        start_song()
    if button_pressed("a"):
        if playing:
            audio.pause()
            playing = False
        else:
            start_song()


@game.draw
def draw():
    clear()
    text("Music", 4, 4)
    text("Track " + str(index + 1), 4, 18)
    text("Playing" if playing else "Paused", 4, 32)


run()
