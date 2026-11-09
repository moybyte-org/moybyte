"""The P4s' 44.1 kHz stereo output (docs/os_2026-11/hardware.md section 3):
the 2x upsampler the feeder runs the sessions' 22.05 kHz mix through
(native/moy_audio/moy_aud.h's moy_aud_up2), the stereo render that adds the
render-ahead stream and the background stream after it (moy_aud_render_st),
and the media service decoding a stereo stream into a stereo output with its
channels apart (native/moy_media/moy_media.h).

The host's ctypes build of moy_aud.c carries the stereo output for these
tests (runtime/host/audio_binding.py's MOY_AUDIO_STEREO); the host's own mix
stays 22.05 kHz mono, as the T-Deck's, the Guition S3's and the browser's do."""

import ctypes
import math
import re
from array import array
from pathlib import Path

import pytest

from runtime.host import audio_binding

ROOT = Path(__file__).resolve().parent.parent
STEREO = ROOT / "tests" / "fixtures" / "media" / "tone_1k_2k5_stereo.mp3"
IN_RATE, OUT_RATE = 22050, 44100
TAPS = int(re.search(r"#define MOY_AUD_UP_TAPS (\d+)",
                     (ROOT / "native" / "moy_audio" / "moy_aud.h").read_text()).group(1))
# What the design promises (moy_aud.h): an image at or above 12.05 kHz 74 dB
# down; held here at 70 on what the C plays, rounding to 16 bits included.
IMAGE_DB = 70.0


class _Up(ctypes.Structure):
    _fields_ = [("h", ctypes.c_int16 * (4 * TAPS)), ("at", ctypes.c_uint32)]


@pytest.fixture
def aud():
    a = audio_binding.get()
    if a is None:
        pytest.skip("no C compiler: the host has no audio")
    was = a.rate()
    a.set_rate(IN_RATE)                 # the sessions' rate, as a board's feeder sets it
    a.snd_close()
    a.bg(None)
    a.focus(0)
    yield a
    a.snd_close()
    a.bg(None)
    a.focus(0)
    a.set_rate(was)


def _up(a, pcm):
    u = _Up()
    src = (ctypes.c_int16 * len(pcm))(*pcm)
    out = (ctypes.c_int16 * (2 * len(pcm)))()
    a._d.moy_aud_up2(ctypes.byref(u), src, len(pcm), out)
    return list(out)


def _tone(freq, n, amp=16000, rate=IN_RATE):
    return [int(round(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)]


def _fit(pcm, freq, rate):
    """The tone at `freq` in `pcm` by least squares: its amplitude and the RMS
    of everything else (an image, the filter's ripple, the rounding)."""
    n = len(pcm)
    w = 2 * math.pi * freq / rate
    s = sum(v * math.sin(w * i) for i, v in enumerate(pcm)) * 2 / n
    c = sum(v * math.cos(w * i) for i, v in enumerate(pcm)) * 2 / n
    res = math.sqrt(sum((v - s * math.sin(w * i) - c * math.cos(w * i)) ** 2
                        for i, v in enumerate(pcm)) / n)
    return math.hypot(s, c), res


def _goertzel(pcm, freq, rate):
    w = 2 * math.pi * freq / rate
    s = sum(v * math.sin(w * i) for i, v in enumerate(pcm)) * 2 / len(pcm)
    c = sum(v * math.cos(w * i) for i, v in enumerate(pcm)) * 2 / len(pcm)
    return math.hypot(s, c)


def _freq(pcm, rate):
    crossings = sum(1 for a, b in zip(pcm, pcm[1:]) if (a < 0) != (b < 0))
    return crossings / 2 / (len(pcm) / rate)


def _db(x, ref):
    return 20 * math.log10(max(x, 1e-9) / ref)


def test_a_1khz_tone_reads_1000hz_with_no_image_past_the_stated_attenuation(aud):
    out = _up(aud, _tone(1000, 2 * IN_RATE // 10 + 4 * TAPS))
    body = out[4 * TAPS:4 * TAPS + OUT_RATE // 10]       # 100 whole periods, settled
    assert abs(_freq(body, OUT_RATE) - 1000) < 1
    amp, rest = _fit(body, 1000, OUT_RATE)
    assert abs(_db(amp, 16000)) < 0.01
    # Everything that is not the tone -- the 21.05 kHz image and any other --
    # is the stated attenuation below it.
    assert _db(rest * math.sqrt(2), amp) < -IMAGE_DB
    assert _db(_goertzel(body, IN_RATE - 1000, OUT_RATE), amp) < -IMAGE_DB


@pytest.mark.parametrize("freq", [100, 3000, 6000, 9000, 10000])
def test_the_passband_is_flat_and_every_image_above_11khz_is_down(aud, freq):
    out = _up(aud, _tone(freq, IN_RATE // 5 + 4 * TAPS))
    body = out[4 * TAPS:4 * TAPS + OUT_RATE // 5]
    amp, _ = _fit(body, freq, OUT_RATE)
    assert abs(_db(amp, 16000)) < 0.05
    assert _db(_goertzel(body, IN_RATE - freq, OUT_RATE), amp) < -IMAGE_DB


def test_the_upsampler_passes_each_input_frame_through_late_by_its_taps(aud):
    pcm = _tone(440, 400)
    out = _up(aud, pcm)
    assert out[0::2][TAPS:] == pcm[:len(pcm) - TAPS]


def test_the_stereo_render_is_the_mono_mix_upsampled_with_the_streams_after_it(aud):
    """A session's beep plays the same in both channels at twice the rate, and
    the render-ahead stream is added at the output's rate."""
    h = aud.open(9101)
    try:
        aud.focus(h)
        aud.beep(h, 1000.0, 2.0)
        n = 4096
        buf = (ctypes.c_int16 * (2 * n))()
        aud._d.moy_aud_render_st(buf, n)
        left, right = list(buf[0::2]), list(buf[1::2])
        assert left == right
        assert abs(_freq(left[n // 4:], OUT_RATE) - 1000) < 15
        st0 = aud.stats()
        aud._d.moy_aud_render_st(buf, 64)
        assert aud.stats()[0] - st0[0] == 64           # rendered: output frames
    finally:
        aud.focus(0)
        aud.close(h)
    assert aud.snd_open(2048) == 1
    ramp = array("h", (i * 100 for i in range(64)))
    assert aud.snd(ramp) == 64
    buf = (ctypes.c_int16 * 256)()
    aud._d.moy_aud_render_st(buf, 128)
    played = list(buf[0::2])
    assert played == list(buf[1::2])
    # 64 frames of the 22.05 kHz stream become 128 at 44.1 kHz, the last
    # held as the point the next interpolates from.
    assert aud.snd_counts()[1] in (63, 64)
    assert max(played) > 0 and played[-1] >= played[64]


@pytest.fixture
def p4_line():
    from runtime import moy_play as mp
    mp.share_use("p4")
    yield mp
    mp.share_use(None)


def test_a_stereo_stream_keeps_its_channels_on_a_stereo_output(aud, p4_line):
    from runtime import moy_media as N
    mp = p4_line
    mono = N.share()
    assert N.open(OUT_RATE, 2) == 0
    try:
        # The decoded ring holds its frames in two channels: twice a mono
        # one's bytes, and the P4's MEDIA row is ample for it.
        held = mp.share_held(mp.SHARE_MEDIA)
        assert held == mono + 16384 * 2 <= mp.shares("p4")[mp.SHARE_MEDIA] // 4
        N.volume(100)
        assert N.play(str(STEREO)) == 1
        mix = N._lib().moy_media_mix
        mix.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        buf = (ctypes.c_int16 * (2 * 1024))()
        left, right = [], []
        st = None
        for _ in range(2000):
            N.pump()                                     # the frame's job: the file read, decoded
            ctypes.memset(buf, 0, ctypes.sizeof(buf))
            mix(buf, 1024, 7, 0)
            left.extend(buf[0::2])
            right.extend(buf[1::2])
            now = N.status()
            if now is None:                              # its list over and played out: the run ended
                break
            st = now
        assert N.last()[3] == N.ENDED and st[8] == 44100 and st[9] == 2
        assert st[17] == 0                               # never starved
        body = slice(OUT_RATE // 5, OUT_RATE * 4 // 5)
        lb, rb = [v for v in left if v][body], [v for v in right if v][body]
        assert abs(_freq(lb, OUT_RATE) - 1000) < 10
        assert abs(_freq(rb, OUT_RATE) - 2500) < 10
        # Each channel is its own tone; the other's is not in it.
        la, _ = _fit(lb, 1000, OUT_RATE)
        ra, _ = _fit(rb, 2500, OUT_RATE)
        assert _db(_goertzel(lb, 2500, OUT_RATE), la) < -40
        assert _db(_goertzel(rb, 1000, OUT_RATE), ra) < -40
    finally:
        _close_media(N)
    assert N.pid() == 0
    assert mp.share_held(mp.SHARE_MEDIA) == 0


def _media_playing(N):
    """The stereo fixture opened on a stereo output and decoded far enough
    that the pull has frames to give."""
    assert N.open(OUT_RATE, 2) == 0
    N.volume(100)
    assert N.play(str(STEREO)) == 1
    for _ in range(200):
        N.pump()
        if N.status()[14] > 4096:
            return
    raise AssertionError("the fixture never decoded")


def _close_media(N):
    N.stop()
    for _ in range(50):
        if not N.pid():
            break
        N.pump()


def test_the_stereo_block_is_silent_at_volume_0_and_on_an_empty_mix(aud, p4_line):
    """Every source the 44.1 kHz block carries -- a session's beep, the
    render-ahead stream and the media's stereo stream -- is silent at the
    console's level 0, and a mix with nothing in it is all zeros."""
    from runtime import moy_media as N
    n = 2048
    buf = (ctypes.c_int16 * (2 * n))()
    was = aud.volume()
    for _ in range(4):                          # the upsampler's history drains
        aud._d.moy_aud_render_st(buf, n)
    assert not any(buf)                         # the empty mix
    h = aud.open(9102)
    try:
        aud.focus(h)
        aud.beep(h, 440.0, 5.0)
        assert aud.snd_open(4096) == 1
        aud.snd(array("h", [12000] * 2048))
        _media_playing(N)
        aud.bg(ctypes.cast(N._lib().moy_media_mix, ctypes.c_void_p).value)
        aud.volume(0)
        for _ in range(8):
            aud._d.moy_aud_render_st(buf, n)
            assert not any(buf)
        aud.volume(7)                           # the same sources, heard
        aud._d.moy_aud_render_st(buf, n)
        assert any(buf)
    finally:
        aud.volume(was)
        aud.bg(None)
        aud.focus(0)
        aud.close(h)
        _close_media(N)
    for _ in range(4):
        aud._d.moy_aud_render_st(buf, n)
    assert not any(buf)                         # nothing left playing
