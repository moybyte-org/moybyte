"""A live station plays in real headless Chrome for a minute with no
underrun, no gap, no repeat and no starved decode, on both of the page's
sinks (page_core.html, THE SINK): the AudioWorklet a secure page has, and
the page's own AudioBufferSource schedule a board serves over plain HTTP
(the page served from a name that is not localhost stands in for one). And starved
on purpose, it plays silence and says so.

The station is `web_station.LiveStation`: the 1 kHz fixture (44.1 kHz stereo
128 kbps, SomaFM's shape) looped with no Content-Length at its own byte rate
after a 64 KB connect burst. The page is the P4's 1024x600 desk, which is
what the owner heard stutter: before the sink took the samples off the
frame slot, the page dropped every frame a newer one superseded before its
next paint, samples and all, and the pull, steered by a depth that did not
count them, drained the decoder faster than real time until a live stream's
lead ran out.

What is read: the sink's counters (`__moyState().sink`), the pull's
(`state.pcm`: samples sent, the sink's played), the decoder's (`media`:
`decoded`, `pulled`, `starved`), and the page's output itself, tapped after
the sink's gain node: its level, its tone and its runs of silence.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_audio_stream_e2e.py
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import web_e2e  # noqa: E402
import web_station  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

PLAY_S = 60
# The page served from a name that is not localhost has no secure context, so
# no AudioWorklet: the page's own sink, as on a board-served page.
INSECURE = "board.test"
SILENCE = 4096                  # samples: ~90 ms, twice the stream's own seam
SEAM = 1767

# The page's output after the sink: zero-runs of SILENCE samples or more once
# the tone began are silences (`longest` the longest zero-run of all); the
# 1 kHz tone crosses zero about 2,000 times a second of what sounded. The
# stream carries a run of its own each loop: the fixture's Info frame and
# encoder delay, which minimp3 plays as 1,767 zeros at 44.1 kHz.
TAP = r"""(() => {
  const sp = actx.createScriptProcessor(4096, 1, 1);
  const T = window.__out = {n: 0, sound: 0, peak: 0, cross: 0, last: 0, run: 0,
                            silences: 0, silent: 0, longest: 0, rate: actx.sampleRate};
  sp.onaudioprocess = (e) => {
    const d = e.inputBuffer.getChannelData(0);
    for (let i = 0; i < d.length; i++) {
      const v = Math.round(d[i] * 32768);
      T.n++;
      if (v === 0) { if (T.sound) T.run++; continue; }
      if (T.run >= %d) { T.silences++; T.silent += T.run; }
      T.longest = Math.max(T.longest, T.run);
      T.run = 0; T.sound++; T.peak = Math.max(T.peak, Math.abs(v));
      if ((v < 0) !== (T.last < 0) && T.last) T.cross++;
      T.last = v;
    }
  };
  audioOut.connect(sp); sp.connect(actx.destination);
  window.__keep = sp;
  return 'tapped ' + (actx.audioWorklet ? 'worklet' : 'page');
})()""" % SILENCE

# One snapshot of every counter, the wall clock with it.
SNAP = ("__moyState().then(s => 'SNAP ' + JSON.stringify({t: performance.now() / 1000,"
        " sink: s.sink, pcm: s.pcm, media: s.media, out: Object.assign({}, window.__out)}))")


def _snaps(js):
    return [json.loads(j[5:]) for j in js if isinstance(j, str) and j.startswith("SNAP ")]


def _music_station(url):
    """Music from the desk, its Add a station by keys: the address, a name."""
    return ([{"click": [143, 80]}, {"wait": 2500}, {"key": "Enter"}, {"wait": 800}]
            + [{"key": c} for c in url] + [{"wait": 300}, {"key": "Enter"}, {"wait": 300}]
            + [{"key": c} for c in "Test FM"] + [{"wait": 300}, {"key": "Enter"}]
            + [{"waitFor": "__moyState().then(s => s.media && s.media.state === 'playing'"
                           " && s.sink && s.sink.played > 0)", "timeout": 20000}])


def _run(tmp_path, name, steps, page_sink):
    station = web_station.LiveStation()
    server, base = web_e2e.serve(web_e2e.DIST, web_e2e.free_port())
    env = None
    if page_sink:
        base = base.replace("127.0.0.1", INSECURE)
        env = {"MOY_CHROME_ARGS": json.dumps(["--host-resolver-rules=MAP %s 127.0.0.1" % INSECURE])}
    try:
        out, js = web_e2e.run(
            tmp_path, name, "?dev=1&desktop=1",
            [{"wait": 2500}] + _music_station(station.base + "/live") + [{"js": TAP}]
            + steps(station),
            base, tmp_path / "chrome", boot_ms=40000,
            env=env)
    finally:
        station.stop()
        server.terminate()
        server.wait(timeout=10)
    assert ("tapped page" if page_sink else "tapped worklet") in js, (js, out[-3000:])
    return out, _snaps(js)


def _delta(a, b, key):
    return b[key] - a[key]


@pytest.mark.parametrize("page_sink", [False, True], ids=["worklet", "page"])
def test_a_live_station_plays_a_minute_at_the_sinks_rate_with_no_underrun(tmp_path, page_sink):
    steps = lambda station: (                                   # noqa: E731
        [{"wait": 3000}, {"js": SNAP}]
        + [s for _ in range(PLAY_S // 10) for s in ({"wait": 10000}, {"js": SNAP})])
    out, snaps = _run(tmp_path, "minute", steps, page_sink)
    assert len(snaps) == PLAY_S // 10 + 1, out[-3000:]
    a, b = snaps[0], snaps[-1]
    print("first", json.dumps(a))
    print("last ", json.dumps(b))
    sink, pcm, media, tap = b["sink"], b["pcm"], b["media"], b["out"]
    rate = pcm["rate"]
    assert rate == tap["rate"], "the console mixes at the context's rate: %r" % b
    assert media["rate"] == 44100 and media["state"] == "playing", media
    # nothing lost, skipped, repeated or refused between the pull and the speaker
    assert sink["under"] == 0 and sink["dry"] == 0, sink
    assert sink["gap"] == 0 and sink["dup"] == 0 and sink["over"] == 0, sink
    assert sink["got"] == pcm["sent"] or pcm["sent"] - sink["got"] < rate // 10, (sink, pcm)
    # the decoder never ran dry, and its samples are the sink's, one for one
    assert media["starved"] == 0, media
    # taken at the sink's own rate: a minute of wall clock is a minute of samples
    wall = b["t"] - a["t"]
    took = _delta(a["sink"], b["sink"], "played") / rate
    assert abs(took - wall) < 0.02 * wall, (took, wall)
    pulled = _delta(a["media"], b["media"], "pulled") / rate
    assert abs(pulled - wall) < 0.02 * wall, (pulled, wall)
    # what came out is the tone, unbroken
    assert tap["silences"] == 0 and tap["longest"] <= SEAM * tap["rate"] // 44100 + 8, tap
    hz = _delta(a["out"], b["out"], "cross") / 2 / wall
    assert abs(hz - 1000) < 60, (hz, tap)
    assert tap["peak"] > 1000, tap


def test_a_station_that_stalls_plays_silence_and_counts_it(tmp_path):
    """The station stops for 12 s, past the lead its burst gave: the decoder
    runs dry (`starved`) and the page plays silence, never the stream again;
    when the bytes come back the tone does."""
    steps = lambda station: (                                   # noqa: E731
        [{"wait": 3000}, {"js": SNAP},
         {"js": "fetch(%s).then(() => 'stalled')" % json.dumps(station.base + "/stall?s=12")}]
        + [s for _ in range(6) for s in ({"wait": 5000}, {"js": SNAP})])
    out, snaps = _run(tmp_path, "stall", steps, False)
    assert len(snaps) == 7, out[-3000:]
    a, b = snaps[0], snaps[-1]
    print("first", json.dumps(a))
    print("last ", json.dumps(b))
    assert b["media"]["starved"] > 0, "the stall never reached the decoder: %r" % b["media"]
    assert b["out"]["silences"] >= 1 and b["out"]["silent"] > 0, b["out"]
    # the sink itself never ran dry: the silence is what the mix sent
    assert b["sink"]["under"] == 0 and b["sink"]["dup"] == 0 and b["sink"]["gap"] == 0, b["sink"]
    assert b["out"]["sound"] > snaps[-2]["out"]["sound"], "the tone did not come back: %r" % b


def test_a_page_sink_starved_on_purpose_plays_silence_and_counts_the_underrun(tmp_path):
    """The page's own sink, its main thread held for 600 ms: what it had
    scheduled plays out, the sink runs dry, and the next chunk counts the
    underrun and the dry samples it played as silence."""
    hold = "(() => { const e = performance.now() + 600; while (performance.now() < e); return 'held'; })()"
    steps = lambda station: [                                   # noqa: E731
        {"wait": 3000}, {"js": SNAP}, {"js": hold}, {"wait": 2000}, {"js": SNAP}]
    out, snaps = _run(tmp_path, "starve", steps, True)
    a, b = snaps[0], snaps[-1]
    print("first", json.dumps(a))
    print("last ", json.dumps(b))
    assert a["sink"]["under"] == 0, a["sink"]
    assert b["sink"]["under"] >= 1 and b["sink"]["dry"] > 0.2 * b["pcm"]["rate"], b["sink"]
    assert b["sink"]["dup"] == 0 and b["sink"]["gap"] == 0, b["sink"]
