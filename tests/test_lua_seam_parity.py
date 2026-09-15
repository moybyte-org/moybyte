"""ONE frame seam, two tiers -- and they must dispatch identically.

The device glue (`device/moycore_glue.py`) and the host runtime
(`runtime/lua_host.py`) do the same three things around every Lua tick: fill the
snapshot slots libmoy reads, apply the cart's `view()`, and drain the audio
queue through the api closures. Until the seam moved into `runtime/lua_ext.py`
they were two copies, identical down to `None if b < 0 else b`, `b / 1000.0` and
the guard comments -- differing only in where the ABI numbers came from
(`moycore` the C module on a board, `runtime.lua_binding` on the host).

That is the shape `tests/test_moy_button_order.py` was written about, one level
up: a divergence in a seam like this raises nothing and fails no golden. A cart
would simply sound different on glass than it does in the sim, or lose its
pointer on one tier, and the per-cart fps number would be unchanged because the
work is the same work aimed at the wrong verb.

So the pin is on BEHAVIOUR and on the ABI underneath it: the two tiers' op codes
and slot indices are read from their own sources (the C enum, parsed; the host
binding, imported) and one logical script is run through the shared body with
each, and the recorded api calls must agree.
"""

import types

from test_moycore_glue import C_CONSTS       # the C enums, parsed from the .c

from runtime import lua_binding, lua_ext


def _abi(consts):
    """A stand-in for a tier's ABI module: nothing but its constants."""
    mod = types.ModuleType("abi")
    for name, value in consts.items():
        setattr(mod, name, value)
    return mod


DEVICE = _abi(C_CONSTS)                      # what `import moycore` exports
HOST = lua_binding                           # what the ctypes binding declares

AUDIO_NAMES = ("sfx", "music", "beep", "music_stop", "sound_stop", "volume")

# (verb, a, b) in the tier-independent spelling. Every branch of the switch,
# plus an op code no tier claims, plus the two negative sentinels that mean
# "omitted" in the C ABI and `None` in the api closures.
SCRIPT = (("sfx", 3, -1),
          ("sfx", 4, 2),
          ("music", 1, 1),
          ("music", 0, 0),
          ("beep", 440, 250),
          ("music_stop", 0, 0),
          ("sound_stop", -1, 0),
          ("sound_stop", 2, 0),
          ("volume", 7, 0),
          (None, 9, 9))                      # an op neither tier defines


def _recording_ns():
    """A cart api namespace that records the calls instead of making sound."""
    calls = []

    def _make(name):
        def _call(*args):
            calls.append((name,) + args)
        return _call

    return {name: _make(name) for name in AUDIO_NAMES}, calls


def _rows(mod):
    """SCRIPT as this tier's (op, a, b) rows."""
    unknown = max(getattr(mod, "AQ_" + n.upper()) for n in AUDIO_NAMES) + 1
    return [(unknown if verb is None else getattr(mod, "AQ_" + verb.upper()),
             a, b)
            for verb, a, b in SCRIPT]


def _played(mod):
    ns, calls = _recording_ns()
    lua_ext.drain_audio(ns, lua_ext.audio_ops(mod), _rows(mod))
    return calls


def test_both_tiers_number_the_audio_ops_the_same_way():
    """The op codes ARE libmoy's ABI -- the C enum on a board, a `range(6)` in
    the host binding. They agree by construction only as long as somebody
    compares them."""
    assert lua_ext.audio_ops(DEVICE) == lua_ext.audio_ops(HOST)


def test_both_tiers_number_the_snapshot_slots_the_same_way():
    assert lua_ext.snap_slots(DEVICE) == lua_ext.snap_slots(HOST)


def test_one_op_sequence_reaches_the_same_api_calls_on_both_tiers():
    """The assertion this file exists for: same script, same calls, in order."""
    assert _played(DEVICE) == _played(HOST)


def test_the_script_actually_exercises_every_branch():
    """A parity test over two empty lists passes and says nothing."""
    played = _played(HOST)
    assert {call[0] for call in played} == set(AUDIO_NAMES)
    assert len(played) == len(SCRIPT) - 1, "the unknown op was dispatched"


def test_the_negative_sentinels_cross_as_none_not_as_minus_one():
    """`sfx(n, chan)` and `sound_stop(chan)` take an OMITTED channel, which the
    queue can only carry as a negative -- so the translation back is part of the
    seam, and it was one of the lines written twice."""
    played = _played(HOST)
    assert ("sfx", 3, None) in played and ("sfx", 4, 2) in played
    assert ("sound_stop", None) in played and ("sound_stop", 2) in played


def test_a_beep_crosses_in_seconds_because_the_queue_carries_milliseconds():
    assert ("beep", 440, 0.25) in _played(HOST)


def test_the_two_glues_hold_no_private_copy_of_the_seam():
    """The bodies are lua_ext's now. A tier that re-grows one of them would pass
    every test above -- they drive the shared body, not the glue -- so the pin
    on "there is only one" has to be on the sources."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in ("device/moycore_glue.py", "runtime/lua_host.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for name in ("drain_audio", "snap_shared", "sync_view"):
            assert name in src, "%s stopped using the shared %s" % (rel, name)
        assert '"music_stop"' not in src, "%s re-grew the audio switch" % rel
        assert "game_view" not in src, "%s re-grew the view sync" % rel
        assert "button_masks(MOY_BUTTONS, 1)" not in src, (
            "%s re-grew the player-two snapshot" % rel)
