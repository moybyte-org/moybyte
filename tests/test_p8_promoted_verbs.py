"""The p8 stdlib verbs libmoy promotes into C, checked from THIS side (#67).

moy-spec holds both halves of every promotion -- the C in `libmoy/src/moy_p8.c`
and the line in `p8_lua_port.py` that makes a ported cart reach for it -- and
`libmoy/test/p8lib.moy` holds them equal to the Lua they replace. None of that
runs here. What runs here is the RE-VENDOR, and the failure this file exists
for is the quiet one: a re-vendor that brings the C across and leaves the
binding behind, or the reverse. Either way every cart still works, every test
still passes, and the verb is simply never taken -- which is exactly what the
promotion was for.

So this pins the SEAM, in both directions:

  * the vendored importer emits the binding, for each promoted verb;
  * the vendored C answers when a cart takes it, through the same host binding
    (`runtime/lua_binding.py`) that compiles libmoy's real sources.

The arity case is spelled out because it is the bug split shipped with
upstream: a C verb that reads its second argument after pushing anything sees
what it pushed, and `split"91,51"` cut the string on its own text and answered
two empty strings. Every data row in a ported cart is a one-argument call and
none of the three-argument tests noticed.
"""

import pathlib
import sys

import pytest

from runtime import lua_binding as lb

# The porter is vendored beside its own asset converter and imports it by bare
# name, so tools/ has to be ON the path rather than a package -- the same way
# tests/test_import_p8.py reaches it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import p8_lua_port                                            # noqa: E402


needs_cc = pytest.mark.skipif(
    not lb.HostLuaRun.available(),
    reason="no C compiler for the host lua binding")


def run(body):
    """One cart's worth of Lua against the real C, answers back as text.

    The answer comes out through error(): the binding hands a cart's error
    back as a STRING (that is what crash-to-code is built on), and at level 0
    it is the message and nothing else -- which is the only channel out of a
    run that does not exist to carry values."""
    buf = bytearray(32 * 32)
    r = lb.HostLuaRun(buf, 32, 32)
    try:
        cart = ("local out = {}\n"
                "function say(s) out[#out + 1] = tostring(s) end\n"
                "function _update(dt) end\nfunction _draw() end\n"
                "function _init()\n" + body +
                "\n  error(table.concat(out, '|'), 0)\nend\n")
        said = r.load(cart, "@verbs")     # load runs _init, and _init reports
        assert said is not None, "the cart loaded without reporting"
        return said
    finally:
        r.close()


# ---- the binding half ---------------------------------------------------

@pytest.mark.parametrize("line", [
    # split: bound like tonum, guarded so a host without the C keeps the Lua
    "if __moy_split ~= nil then split = __moy_split end",
    # rnd and srand are ONE generator and move together or not at all
    'if p8c("rnd") ~= nil and p8c("srand") ~= nil then',
    'rnd, srand = p8c("rnd"), p8c("srand")',
])
def test_the_vendored_importer_binds_the_promoted_verbs(line):
    text = open(p8_lua_port.__file__).read()
    assert line in text, (
        "the vendored importer no longer emits %r -- the C verb is still "
        "there and no cart reaches it. Re-vendor both halves "
        "(make vendor-p8-import)." % line)


def test_a_ported_cart_carries_the_bindings(tmp_path):
    """Not the source of the importer: the SHIM IT WRITES, which is what a
    cart on a board actually loads."""
    p8 = tmp_path / "t.p8"
    p8.write_text("pico-8 cartridge // http://www.pico-8.com\nversion 42\n"
                  "__lua__\nfunction _draw() cls(1) end\n")
    out = tmp_path / "t.moy"
    p8_lua_port.port(str(p8), str(out), title="T")
    main = (out / "main.lua").read_text()
    assert "split = __moy_split" in main
    assert 'rnd, srand = p8c("rnd"), p8c("srand")' in main


# ---- the C half ---------------------------------------------------------

@needs_cc
def test_split_reads_its_arity_before_it_pushes():
    """THE regression. A one-argument call must not read the subject as its
    own separator -- which is what every data row in a ported cart is."""
    got = run("""
      say(#__moy_split("91,51,1"))
      for _, v in ipairs(__moy_split("91,51,1")) do say(v) end
      say(math.type(__moy_split("91,51,1")[1]))
    """)
    assert got == "3|91|51|1|integer", got


@needs_cc
def test_split_cuts_the_three_ways_a_cart_cuts():
    got = run("""
      say(table.concat(__moy_split("a,b,c"), "/"))
      say(table.concat(__moy_split("a|b|c", "|"), "/"))
      say(table.concat(__moy_split("abcdef", 2), "/"))
      say(math.type(__moy_split("1,2", ",", false)[1]))
      say(#__moy_split(nil))
    """)
    assert got == "a/b/c|a/b/c|ab/cd/ef|nil|0", got


@needs_cc
def test_the_generator_is_reproducible_and_seeded_state_is_shared():
    """A cart seeded the same way lays out the same level -- and srand has to
    reach the generator rnd draws from, or a cart seeds something nothing
    reads and the level differs every run with nothing to point at."""
    got = run("""
      local function draw5()
        local t = {}
        for i = 1, 5 do t[i] = string.format("%.7f", __moy_p8_rnd()) end
        return table.concat(t, ",")
      end
      __moy_p8_srand(42) local a = draw5()
      __moy_p8_srand(42) local b = draw5()
      __moy_p8_srand(43) local c = draw5()
      say(a == b) say(a ~= c)
      __moy_p8_srand(7) say(__moy_p8_rnd(0) == 0)
      say(__moy_p8_rnd({}) == nil)
      __moy_p8_srand(1)
      local t, seen = {"a", "b", "c"}, {}
      for _ = 1, 200 do seen[__moy_p8_rnd(t)] = true end
      say(seen.a and seen.b and seen.c)
    """)
    assert got == "true|true|true|true|true", got
