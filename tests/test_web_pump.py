"""The page's pull of the mix (web_boot._RunnerPump) by count: it tops the
sink's depth up to its target from the samples the sink says it played, so a
stream is taken at the sink's own rate however the frames fall; every sample
it renders is handed over once, in order (`at`), the first after a silence
marked fresh; with no sink it renders the frame's share and hands nothing
over. And the mix is made at the page's AudioContext's rate."""

import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def web_boot():
    for p in ("firmware/web_runner", "device", "runtime"):
        d = os.path.join(ROOT, p)
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    was = web_boot._AUDIO_RATE_HZ[0]
    yield web_boot
    web_boot._AUDIO_RATE_HZ[0] = was


class _Mix:
    """The kernel's mix: each sample is its own index, so order shows."""

    def __init__(self):
        self.on = True
        self.k = 0

    def active(self):
        return self.on

    def render(self, buf, n):
        for i in range(n):
            v = (self.k + i) & 0x7fff
            buf[2 * i] = v & 0xff
            buf[2 * i + 1] = v >> 8
        self.k += n

    render_out = render


def _pump(web_boot, rate=48000):
    web_boot.audio_rate(rate)
    p = web_boot._RunnerPump()
    p._na = _Mix()
    return p


def _samples(p, n):
    b = p._buf
    return [b[2 * i] | (b[2 * i + 1] << 8) for i in range(n)]


def test_the_mix_is_made_at_the_contexts_rate(web_boot):
    assert _pump(web_boot, 44100).rate == 44100
    web_boot.audio_rate(0)                       # no Web Audio: the default kept
    assert web_boot._RunnerPump().rate == 44100
    assert web_boot._audio_rate() == 44100


def test_with_no_sink_a_frame_renders_its_share_and_hands_nothing_over(web_boot):
    p = _pump(web_boot)
    p.tick(1 / 60)
    assert p.rendered == 800 and p.take() is None and p.sent == 0


def test_the_pull_tops_the_sinks_depth_up_to_its_target(web_boot):
    p = _pump(web_boot)
    target = int(p.rate * web_boot._AUDIO_TARGET)
    p.played = 0
    p.tick(1 / 60)
    p.tick(1 / 60)                               # a second tick in one frame adds nothing
    n, at, fresh = p.take()
    assert (n, at, fresh) == (target, 0, True)
    assert _samples(p, n) == list(range(n))
    p.tick(1 / 60)
    assert p.take() is None                      # the sink played nothing: nothing more
    p.played = 1000
    p.tick(1 / 60)
    assert p.take() == (1000, target, False)     # what it played, after what it had
    p.played = target + 1000 + 10 * p.rate       # a long stall: one frame's cap
    p.tick(1 / 60)
    assert p.take()[0] == int(p.rate * web_boot._AUDIO_MAX_STEP)


def test_the_first_chunk_after_a_silence_is_fresh(web_boot):
    p = _pump(web_boot)
    p.played = 0
    p.tick(1 / 60)
    p.take()
    p._na.on = False
    p.played = p.sent
    p.tick(1 / 60)
    assert p.take() is None
    p._na.on = True
    p.tick(1 / 60)
    assert p.take()[2] is True


def test_jittery_frames_take_the_stream_at_the_sinks_rate_with_no_underrun(web_boot):
    """Frames from 4 to 60 ms apart, the sink's count a report behind: the
    depth never runs out, every sample arrives once, in order, and a minute
    of the sink's clock takes a minute of samples."""
    p = _pump(web_boot)
    rnd = random.Random(7)
    p.played = 0
    played, depth, got, t = 0.0, 0, 0, 0.0
    while t < 60.0:
        dt = rnd.uniform(0.004, 0.060)
        t += dt
        took = min(depth, int(p.rate * dt))
        assert took == int(p.rate * dt) or t < 0.1, "the sink ran dry at %.2f s" % t
        depth -= took
        played += took
        p.tick(dt)
        c = p.take()
        p.played = int(played)                   # the report reaches the next frame
        if c is not None:
            n, at, _ = c
            assert at == got
            got += n
            depth += n
    assert abs(played / p.rate - t) < 0.1
    assert got - played <= p.rate * web_boot._AUDIO_TARGET
