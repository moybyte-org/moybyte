"""The output rate where no feeder runs (native/moy_audio/moy_aud.h, THE
OUTPUT RATE): the browser's page plays at its AudioContext's rate, the
sessions still mix at 22.05 kHz as on every board, and moy_aud_render_out
upsamples that mix once -- exactly the P4's 2x at 44.1 kHz, carried on
linearly to 48 kHz -- with the render-ahead stream and the background stream
added at the output's rate, which the media service resamples to."""

import ctypes
import math

import pytest

from runtime.host import audio_binding

MIX = 22050


@pytest.fixture
def aud():
    a = audio_binding.get()
    if a is None:
        pytest.skip("no C compiler: the host has no audio")
    was = a.rate()
    a.set_rate(MIX)
    a.set_out_rate(0)
    a.snd_close()
    a.bg(None)
    a.focus(0)
    yield a
    a.snd_close()
    a.bg(None)
    a.focus(0)
    a.set_out_rate(0)
    a.set_rate(was)


def _render_out(a, n):
    buf = bytearray(2 * n)
    a.render_out(buf, n)
    return list(memoryview(buf).cast("h"))


def _freq(pcm, rate):
    crossings = sum(1 for x, y in zip(pcm, pcm[1:]) if (x < 0) != (y < 0))
    return crossings / 2 / (len(pcm) / rate)


def _goertzel(pcm, freq, rate):
    w = 2 * math.pi * freq / rate
    s = sum(v * math.sin(w * i) for i, v in enumerate(pcm)) * 2 / len(pcm)
    c = sum(v * math.cos(w * i) for i, v in enumerate(pcm)) * 2 / len(pcm)
    return math.hypot(s, c)


def test_the_output_rate_is_the_mixs_until_one_is_set(aud):
    assert aud._d.moy_aud_out_rate() == MIX
    aud.set_out_rate(48000)
    assert aud._d.moy_aud_out_rate() == 48000 and aud.rate() == MIX
    aud.set_out_rate(MIX)
    assert aud._d.moy_aud_out_rate() == MIX


@pytest.mark.parametrize("out", [44100, 48000])
def test_a_beep_mixed_at_22k_plays_at_its_pitch_at_the_output_rate(aud, out):
    """A session's 1 kHz beep, rendered at the output's rate: its pitch, and
    the mix's image at 22.05 kHz - 1 kHz far down (the 2x's filter)."""
    aud.set_out_rate(out)
    h = aud.open(9201)
    try:
        aud.focus(h)
        aud.beep(h, 1000.0, 2.0)
        pcm = _render_out(aud, out // 2)
        body = pcm[out // 10:]
        assert abs(_freq(body, out) - 1000) < 15
        tone = _goertzel(body, 1000, out)
        assert tone > 1000
        image = _goertzel(body, MIX - 1000, out)
        assert 20 * math.log10(max(image, 1e-9) / tone) < -50
        st0 = aud.stats()
        _render_out(aud, 100)
        assert aud.stats()[0] - st0[0] == 100          # rendered: output frames
    finally:
        aud.focus(0)
        aud.close(h)


def test_at_twice_the_mix_the_output_is_the_p4s_upsampler_exactly(aud):
    """At 44.1 kHz the carry is the identity: render_out is moy_aud_up2 of the
    mix, two frames late, as the P4's feeder plays it."""
    aud.set_out_rate(2 * MIX)
    h = aud.open(9202)
    try:
        aud.focus(h)
        aud.beep(h, 700.0, 2.0)
        got = _render_out(aud, 2048)
    finally:
        aud.focus(0)
        aud.close(h)
    aud.set_out_rate(0)
    h = aud.open(9202)
    try:
        aud.focus(h)
        aud.beep(h, 700.0, 2.0)
        mono = bytearray(2048)
        aud.render(mono, 1024)
    finally:
        aud.focus(0)
        aud.close(h)
    from test_audio_upsample import _up
    want = _up(aud, list(memoryview(mono).cast("h")))
    assert got[2:] == want[:2046]


def test_the_render_ahead_stream_is_added_at_the_output_rate(aud):
    """A compiled cart's `snd` (22.05 kHz frames) plays at its own pitch in a
    48 kHz output: 2.18 output frames a frame."""
    aud.set_out_rate(48000)
    assert aud.snd_open(4096) == 1
    tone = [int(8000 * math.sin(2 * math.pi * 500 * i / MIX)) for i in range(2048)]
    buf = (ctypes.c_int16 * len(tone))(*tone)
    aud._d.moy_audio_snd(buf, len(tone))
    pcm = _render_out(aud, 4000)
    assert abs(_freq(pcm[200:4000], 48000) - 500) < 15
