from kidcode_sim.headless_backend import HeadlessSimulator


def test_music_player_stub_logs_audio_call():
    sim = HeadlessSimulator("examples/music_player_stub.kcproj")
    context = sim.run(frames=3)

    assert ("play", "assets/music/song1.mp3") in context.audio.calls
