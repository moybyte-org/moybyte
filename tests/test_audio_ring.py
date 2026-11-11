"""The render-ahead audio ring (docs/os_2026-11/hardware.md section 2): the
mix's stream at a depth the app chooses (native/moy_audio/moy_audio_snd.h),
its Python face (`moybyte.snd_open`/`snd`/`snd_close`), a compiled cart's
manifest field, and the constants the C restates from THE SHARES.

The host's ctypes build of moy_aud.c is the same C a board runs, so the
ring's depth, its room and its starvation are read here as a board reads
them; what only a board has (the RINGS row's refusal, the timed lanes and
their deadline monitor) is its on-glass suite's."""

import array
import json
import os
import re

import pytest

from runtime.host import audio_binding

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel)) as fh:
        return fh.read()


def _share_row(name):
    m = re.search(r"MOY_SHARE_%s = (\d+)" % name, _src("native/moy_play/moy_play.h"))
    return int(m.group(1))


def test_the_c_that_restates_a_shares_row_names_the_same_row():
    assert int(re.search(r"#define AUD_SHARE_RINGS (\d+)",
                         _src("native/moy_audio/moy_aud.c")).group(1)) == _share_row("RINGS")
    assert int(re.search(r"#define TL_SHARE_LANES (\d+)",
                         _src("native/moy_wasm/moy_wasm_tlane.c")).group(1)) == _share_row("LANES")


def test_the_python_face_restates_the_depths_the_c_takes():
    from runtime import moybyte
    h = _src("native/moy_audio/moy_audio_snd.h")
    for name, v in (("DEPTH", moybyte.SND_DEPTH), ("MIN", moybyte.SND_MIN),
                    ("MAX", moybyte.SND_MAX)):
        assert int(re.search(r"#define MOY_AUDIO_SND_%s (\d+)" % name, h).group(1)) == v
    a = audio_binding.Module
    assert (a.SND_DEPTH, a.SND_MIN, a.SND_MAX) == (moybyte.SND_DEPTH, moybyte.SND_MIN,
                                                    moybyte.SND_MAX)


@pytest.fixture
def aud():
    a = audio_binding.get()
    if a is None:
        pytest.skip("no C compiler: the host has no audio")
    a.snd_close()
    yield a
    a.snd_close()


def _drain(a, frames):
    out = bytearray(2 * frames)
    a.render(out, frames)
    return out


def test_the_ring_is_as_deep_as_the_app_asks(aud):
    assert aud.snd_open(256) == 1
    assert aud.snd() == 256
    tone = array.array("h", [1000] * 300)
    assert aud.snd(tone) == 256          # the rest is the caller's to offer again
    assert aud.snd() == 0
    c = aud.snd_counts()
    assert c[0] == 256 and c[4] is True and c[5] == 256
    assert aud.snd_open(8192) == 1       # deeper than the reserved ring: its own
    assert aud.snd() == 8192 and aud.snd_counts()[5] == 8192
    assert aud.snd_open(0) == 1 and aud.snd() == 2048
    for bad in (1, 63, 65537):
        assert aud.snd_open(bad) == 0


def test_a_shallow_ring_starves_where_a_deep_one_rides_the_same_stall(aud):
    """A writer that refills once per 50 ms (a collection's stall) keeps a
    deep ring fed and starves a shallow one: the depth is the app's trade."""
    rate = aud.rate()
    tone = array.array("h", [800] * 8192)
    starved = {}
    for depth in (256, 4096):
        aud.snd_open(depth)
        for _ in range(20):
            aud.snd(tone)
            _drain(aud, rate // 20)
        starved[depth] = aud.snd_counts()[2]
    assert starved[4096] == 0
    assert starved[256] > rate // 2


def test_the_face_gives_the_ring_to_one_process_and_sweeps_it(aud):
    from runtime import moybyte
    moybyte.SND[0] = 0
    assert moybyte.snd_open(512) is True
    pid = moybyte.SND[0]
    assert pid and moybyte.snd() == 512
    moybyte.SND[0] = pid + 1             # another process's
    with pytest.raises(OSError):
        moybyte.snd_open(512)
    moybyte.SND[0] = pid
    moybyte.WAKES.end(pid)               # its end sweeps it
    assert moybyte.SND[0] == 0 and moybyte.snd_counts()[4] is False
    assert moybyte.snd(array.array("h", [1] * 8)) == 0


def test_a_compiled_cart_holds_the_ring_against_a_process(aud):
    from runtime import moybyte
    moybyte.SND[0] = 0
    aud.snd_open(0)                      # what moycore opens for a cart's `snd`
    with pytest.raises(OSError):
        moybyte.snd_open(512)


def test_the_manifest_field_is_moybyte_s_own(tmp_path):
    from runtime.carts import moy_carts
    for man, want in (({"moybyte": {"audio_ring": 384}}, 384),
                      ({"audio_ring": 384}, None),
                      ({"moybyte": {"audio_ring": "x"}}, None), ({}, None)):
        d = tmp_path / ("c%d.moy" % len(list(tmp_path.iterdir())))
        d.mkdir()
        base = {"format": "moy-1", "title": "R", "runtime": "wasm", "main": "main.wasm",
                "memory": 1}
        base.update(man)
        (d / "manifest.json").write_text(json.dumps(base))
        (d / "main.wasm").write_bytes(b"\0asm\1\0\0\0")
        cart = moy_carts.load(str(d))
        assert cart["audio_ring"] == want, man
