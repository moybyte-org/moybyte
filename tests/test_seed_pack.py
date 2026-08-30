"""The PACKED seed roster: the same carts, 3.6x smaller, inflated on the board.

WHY THIS EXISTS. Every board freezes the seed roster as `carts_data.py` --
except the Zero, which carried none at all and whose carts therefore arrived
over a USB cable, or never. Its `provision.sh` header called that "what an image
cannot carry: a kid's carts". Since 2026-08-30 the image carries them
compressed: one raw-deflate stream per cart, 201,716 B, inflated one cart at a
time into an EMPTY store on first boot.

MEASURED, on the two things a change like this can get wrong:

  * FLASH. Zero app image 2,194,112 B carrying no roster, 2,399,232 B carrying
    the packed one -- about the blob's own 201,716 B, so the bytes land in ROM
    and cost their size and an index. The plain form was BUILT TOO, and the
    interesting answer is that it fits: 2,830,672 B of the 2,883,584 B slot,
    51 KB left, under the #168 warning floor and one cart from a build failure
    -- in a slot the board pays for twice. So this is not "it would not fit"
    but "it fit with 1.8% to spare", which is not a margin.
  * RAM. `test_the_inflate_runs_on_micropython` seeds the whole roster under a
    CAPPED MicroPython heap, which is the only measurement of peak that cannot
    be fooled by an allocator that has not got round to collecting.

WHAT IS PINNED HERE, in the order the failures would happen:

  * the two sides of the deflate window agree (a mismatch is not a crash, it is
    a wrong-looking inflate);
  * a packed roster seeds a store BYTE FOR BYTE identically to a plain one --
    which is what makes this a change of representation and not of content;
  * the emptiness gate the Zero seeds behind, in both directions;
  * and the whole thing running on a REAL MicroPython, because none of the
    above proves `deflate` accepts what CPython's `zlib` produced.
"""

import json
import os
import subprocess
import sys

import pytest

import unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import gen_device_carts as gen              # noqa: E402
import moy_carts                            # noqa: E402

SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
ZERO = os.path.join(ROOT, "firmware", "seeed_xiao_esp32s3_zero")


# -- the two sides of the wire ---------------------------------------------

def test_the_writers_window_is_the_readers_window():
    """A mismatch here does not raise -- it inflates to garbage or to nothing,
    on a board, at first boot, with no screen to say so. So it is asserted
    where both names are visible at once."""
    assert gen.SEED_WBITS == moy_carts._SEED_WBITS
    assert gen.SEED_FORMAT == "deflate-raw-15"


def test_every_cart_round_trips_through_the_blob():
    carts = gen.build_carts(SYSTEM_CARTS)
    packed = gen.build_packed(SYSTEM_CARTS)
    assert len(packed) == len(carts) and carts
    for cart, (title, version, blob) in zip(carts, packed):
        # title + version ride OUTSIDE the blob so the already-there check can
        # be answered without inflating; they must be the cart's own.
        assert title == cart["title"]
        assert version == int(cart.get("version", 0))
        assert moy_carts.unpack_seed(blob) == cart


def test_the_packed_roster_is_much_smaller_than_the_plain_one():
    """The whole reason the Zero can carry a roster. A floor rather than a
    pinned number: this is a property of the carts, and it moves when they do.
    """
    packed = gen.build_packed(SYSTEM_CARTS)
    comp = sum(len(b) for _t, _v, b in packed)
    plain = len(gen.render_module(gen.build_carts(SYSTEM_CARTS)).encode("utf-8"))
    assert comp * 2 < plain, (
        "the packed roster (%d B) is no longer worth its decoder against the "
        "plain one (%d B)" % (comp, plain))


# -- the store the two forms produce ---------------------------------------

def _tree(root):
    out = {}
    for base, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


def test_packed_and_plain_seed_byte_identical_stores(tmp_path):
    """The claim that makes this a change of REPRESENTATION.

    Not a spot-check of a few fields: every file of every cart, compared byte
    for byte. It has already caught one real difference -- `sort_keys=True` in
    the packer reordered the nested `cfg` / `edit` / `sounds` dicts, so a
    packed-seeded board's config.json differed from a plain-seeded one's while
    every field in it was the same.
    """
    plain_root = str(tmp_path / "plain")
    packed_root = str(tmp_path / "packed")
    moy_carts.ensure_dirs(plain_root)
    moy_carts.ensure_dirs(packed_root)
    moy_carts.seed_builtins(gen.build_carts(SYSTEM_CARTS), plain_root)
    written = moy_carts.seed_packed(gen.build_packed(SYSTEM_CARTS), packed_root)

    a, b = _tree(plain_root), _tree(packed_root)
    assert written == len(gen.build_packed(SYSTEM_CARTS))
    assert a and sorted(a) == sorted(b)
    differing = sorted(k for k in a if a[k] != b[k])
    assert not differing, "packed seeding wrote different bytes: %s" % differing


def test_reseeding_a_current_store_inflates_nothing(tmp_path, monkeypatch):
    """The warm-boot path costs 35 directory stats and no decompression.

    Asserted by BREAKING the decoder: with `unpack_seed` replaced by something
    that raises, a second pass over a seeded store must still succeed -- which
    it can only do if it never called it.
    """
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    packed = gen.build_packed(SYSTEM_CARTS)
    assert moy_carts.seed_packed(packed, root) == len(packed)

    def boom(_blob):
        raise AssertionError("seed_packed inflated a cart that was already there")

    monkeypatch.setattr(moy_carts, "unpack_seed", boom)
    assert moy_carts.seed_packed(packed, root) == 0


def test_a_newer_baked_version_still_reseeds(tmp_path):
    """The mirror of the test above: the #47 rules are `seed_builtins`' and
    this decoder must not have quietly replaced them with "never overwrite"."""
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = {"title": "Hop", "type": "game", "version": 1, "src": "OLD",
            "cfg": {}, "edit": []}
    moy_carts.seed_packed([("Hop", 1, gen.pack_cart(cart))], root)
    assert moy_carts._read(root + "/hop.moy/main.py") == "OLD"

    cart2 = dict(cart, version=2, src="NEW")
    assert moy_carts.seed_packed([("Hop", 2, gen.pack_cart(cart2))], root) == 1
    assert moy_carts._read(root + "/hop.moy/main.py") == "NEW"


# -- the Zero's own gate ---------------------------------------------------

def _zero_host(tmp_path, monkeypatch):
    """`zero_host`, importable on the host (it is written to be)."""
    monkeypatch.syspath_prepend(os.path.join(ZERO, "modules"))
    monkeypatch.syspath_prepend(os.path.join(ROOT, "runtime"))
    import zero_host
    return zero_host


def test_an_empty_store_is_one_with_no_cart_in_it(tmp_path, monkeypatch):
    """`store_is_empty` no longer gates the seed (that is per-cart presence
    now), but provision.sh still asks it -- over the cable, on the board, with
    this exact read -- to decide whether a board needs the roster pushed at all.
    Sidecars a sync leaves behind are not carts."""
    zero_host = _zero_host(tmp_path, monkeypatch)
    root = str(tmp_path / "carts")
    os.makedirs(root)
    assert zero_host.store_is_empty(root) is True

    # Sidecars are not carts: a store holding only these is still empty.
    os.makedirs(root + "/.history")
    os.makedirs(root + "/journal")
    assert zero_host.store_is_empty(root) is True

    os.makedirs(root + "/hop_quest.moy")
    assert zero_host.store_is_empty(root) is False


def _fake_roster(monkeypatch):
    """A `carts_data` module with the real packed roster in it.

    Injected even by the test that expects NOTHING to be seeded, and that is
    the point: without it `seed_carts` takes its no-roster-in-this-image branch
    and returns 0 for a reason that has nothing to do with the store. Removing
    the emptiness gate then leaves the suite green -- which is exactly what
    happened when this was written, so it is asserted here rather than trusted.
    """
    import types
    mod = types.ModuleType("carts_data")
    mod.CARTS_Z = gen.build_packed(SYSTEM_CARTS)
    monkeypatch.setitem(sys.modules, "carts_data", mod)
    return mod


def test_the_zeros_seed_never_rewrites_a_cart_that_is_there(tmp_path, monkeypatch):
    """The rule that protects a kid's work: PRESENCE, not version.

    A folder that exists is left exactly as it is, even though the roster
    carries a cart of that name at a newer version -- because on this board that
    folder may be the only copy of something a kid changed in a browser.
    """
    zero_host = _zero_host(tmp_path, monkeypatch)
    mod = _fake_roster(monkeypatch)
    root = str(tmp_path / "carts")
    os.makedirs(root + "/hop_quest.moy")
    with open(root + "/hop_quest.moy/main.py", "w") as f:
        f.write("the kid's own")

    written = zero_host.seed_carts(root)

    with open(root + "/hop_quest.moy/main.py") as f:
        assert f.read() == "the kid's own", \
            "a baked version bump overwrote a cart the kid edited"
    # ...and everything the store did NOT have arrived, which is the half the
    # emptiness gate used to refuse.
    assert written == len(mod.CARTS_Z) - 1
    assert len(os.listdir(root)) == len(mod.CARTS_Z)


def test_a_zero_that_has_been_used_still_receives_a_new_builtin(tmp_path, monkeypatch):
    """The gap that made presence-gating necessary (2026-08-30).

    Under the old emptiness gate a Zero with any cart at all took NOTHING from
    a new image, so `Pin Light` shipped in four firmwares and could not reach
    the one board it was written for.
    """
    zero_host = _zero_host(tmp_path, monkeypatch)
    mod = _fake_roster(monkeypatch)
    root = str(tmp_path / "carts")
    os.makedirs(root)
    assert zero_host.seed_carts(root) == len(mod.CARTS_Z)

    # A newer image: same roster plus one cart this store has never seen.
    fresh = gen.build_carts(SYSTEM_CARTS)[0]
    fresh = dict(fresh, title="Brand New")
    mod.CARTS_Z = mod.CARTS_Z + [("Brand New", 1, gen.pack_cart(fresh))]

    assert zero_host.seed_carts(root) == 1, \
        "a used Zero must still receive a built-in it has never had"
    assert os.path.isdir(root + "/brand_new.moy")


def test_the_zeros_seed_fills_an_empty_store(tmp_path, monkeypatch):
    """...and the same call on an empty store DOES seed, so the tests above are
    measuring the gate and not a function that never runs."""
    zero_host = _zero_host(tmp_path, monkeypatch)
    mod = _fake_roster(monkeypatch)
    root = str(tmp_path / "carts")
    os.makedirs(root)

    assert zero_host.seed_carts(root) == len(mod.CARTS_Z)
    seeded = sorted(n for n in os.listdir(root) if n.endswith(".moy"))
    assert len(seeded) == len(mod.CARTS_Z)
    assert zero_host.seed_carts(root) == 0          # ...and nothing on a warm boot


def test_a_half_seeded_store_is_finished_on_the_next_boot(tmp_path, monkeypatch):
    """The power-cut case the emptiness gate named as its own price: a store
    with SOME carts in it read as "not empty" and was never completed."""
    zero_host = _zero_host(tmp_path, monkeypatch)
    mod = _fake_roster(monkeypatch)
    root = str(tmp_path / "carts")
    os.makedirs(root)
    # Interrupted: only the first three ever landed.
    moy_carts.seed_packed(mod.CARTS_Z[:3], root)
    assert len([n for n in os.listdir(root) if n.endswith(".moy")]) == 3

    assert zero_host.seed_carts(root) == len(mod.CARTS_Z) - 3
    assert len([n for n in os.listdir(root) if n.endswith(".moy")]) == len(mod.CARTS_Z)


# -- the build wiring ------------------------------------------------------

# Every build target that bakes a roster, and the runtime module that reads it.
# The Zero is headless (zero_host), the other three boot the shared console.
_BOARDS = {
    "seeed_xiao_esp32s3_zero": "zero_host.py",
    "lilygo_t_deck_plus_mainline": "moy_runtime.py",
    "esp32_p4_wifi6_touch_lcd_7b": "moy_runtime.py",
    "guition_jc3248w535": "moy_runtime.py",
}


@pytest.mark.parametrize("board", sorted(_BOARDS))
def test_every_board_bakes_the_packed_roster(board):
    """The one line that turns a flashed board into a board with carts on it.

    `--packed` specifically. On the Zero the plain invocation would emit
    731,592 B into an image with 689,472 B of room and the #168 guard would fail
    the build -- the good failure, but this is the line that keeps it from being
    reached. On the three console boards it fits and the argument is the margin:
    a roster that only grows against a slot that does not, and the P4's 4 MB
    slots on a 32 MB chip are the ones with the least room to be wrong about.

    PARAMETRIZED because that is the whole point (2026-08-30). This was one
    board's test while it was one board's mechanism, and a per-board test is
    exactly how a lever ends up shipped on one target and forgotten on the rest.
    """
    fw = os.path.join(ROOT, "firmware", board)
    with open(os.path.join(fw, "build.sh")) as f:
        sh = f.read()
    assert "gen_device_carts.py\" --packed" in sh.replace("\\\n", "")
    from tools import board_config
    keep = board_config.load(fw).get("modules", {}).get("keep", [])
    assert "carts_data.py" in keep, (
        "the stager would prune the generated roster before the freeze")
    # ...and the module that consumes it must read the PACKED name, or the board
    # freezes a compressed roster and imports a plain one that is not there.
    with open(os.path.join(fw, "modules", _BOARDS[board])) as f:
        assert "CARTS_Z" in f.read()


def test_the_seeder_is_chosen_by_the_rosters_form_not_by_the_board():
    """`seed_any` is the one door every board's boot calls. The dispatch lives
    with the two bodies it chooses between, so a board cannot pass the wrong
    flag beside the right roster -- there is no flag."""
    plain = gen.build_carts(SYSTEM_CARTS)
    packed = gen.build_packed(SYSTEM_CARTS)
    assert moy_carts.is_packed(packed) and not moy_carts.is_packed(plain)
    # An empty roster is not "packed" -- it has no form at all, and reading a
    # form off nothing is how an empty build would pick the wrong decoder.
    assert not moy_carts.is_packed([])


def test_the_read_only_floor_inflates_a_packed_roster(tmp_path):
    """The floor a board reaches only if it has NO writable store at all. It
    must hand back cart DICTS whichever roster the board froze, because the
    launcher renders them directly -- a board that fell this far showing tuples
    would be a crash on top of a dead filesystem."""
    packed = gen.build_packed(SYSTEM_CARTS)
    plain = gen.build_carts(SYSTEM_CARTS)
    floor = moy_carts.embedded_floor(packed)
    assert [c["title"] for c in floor] == [c["title"] for c in plain]
    assert floor == plain, "the floor is the same carts, not a lossy summary"


# -- and on a real MicroPython ---------------------------------------------

DRIVER = """
import gc, os, sys, time
sys.path.insert(0, ".")
import moy_carts
from carts_data import CARTS_Z

try:
    os.mkdir("store")
except OSError:
    pass

gc.collect()
before = gc.mem_alloc()
t0 = time.ticks_ms()
written = moy_carts.seed_packed(CARTS_Z, "store")
elapsed = time.ticks_diff(time.ticks_ms(), t0)
gc.collect()

# The biggest single cart, inflated on its own with the heap measured either
# side: what one unit of work RETAINS, which is the number the streaming parse
# was chosen for.
worst = 0
worst_title = ""
for title, version, blob in CARTS_Z:
    gc.collect()
    a = gc.mem_alloc()
    cart = moy_carts.unpack_seed(blob)
    held = gc.mem_alloc() - a
    if held > worst:
        worst, worst_title = held, title
    cart = None

out = {"written": written, "elapsed_ms": elapsed, "retained": worst,
       "retained_title": worst_title, "leaked": gc.mem_alloc() - before,
       "carts": len(os.listdir("store"))}
print("RESULT " + __import__("json").dumps(out))
"""


def _stage_for_micropython(tmp_path):
    """The Zero's own staged set for this path, as a flat directory.

    By NAME rather than by running the stager: this is checking the FILES, and
    the set itself is `board.toml`'s -- `tests/test_staging_closure.py` is what
    fails if these names stop being the ones the board freezes.
    """
    for name in ("moy_carts.py", "moy_image.py", "moy_fs.py", "moy_journal.py",
                 "ticks.py"):
        with open(os.path.join(ROOT, "runtime", name), encoding="utf-8") as f:
            body = f.read()
        with open(os.path.join(str(tmp_path), name), "w", encoding="utf-8") as f:
            f.write(body)
    text = gen.render_packed_module(gen.build_packed(SYSTEM_CARTS))
    with open(os.path.join(str(tmp_path), "carts_data.py"), "w",
              encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(str(tmp_path), "run.py"), "w", encoding="utf-8") as f:
        f.write(DRIVER)


def _drive(tmp_path, heap=None):
    exe = unix_mp.require_unix_mp(
        why="This is the only lane where the board's OWN inflater sees the\n"
            "blob. CPython's zlib produced it and CPython's zlib is what every\n"
            "other check reads it back with; `deflate` is a different\n"
            "implementation, and MicroPython has no zlib at all.")
    cmd = [exe]
    if heap:
        cmd += ["-X", "heapsize=%s" % heap]
    return subprocess.run(cmd + ["run.py"], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=300)


def test_the_inflate_runs_on_micropython(tmp_path):
    """The roster inflates and seeds under the interpreter the board runs.

    Nothing above this proves it: the blob is written by CPython's `zlib` and
    read back by CPython's `zlib`, and the board has no `zlib` at all --
    MicroPython replaced it with `deflate` in v1.21, which is a different
    inflater reading a stream one byte at a time. It is also the tier where
    `json.load(<stream>)` either exists or does not.
    """
    _stage_for_micropython(tmp_path)
    r = _drive(tmp_path)
    assert r.returncode == 0, "MicroPython refused the seed:\n%s\n%s" % (
        r.stdout[-3000:], r.stderr[-3000:])
    line = [l for l in r.stdout.split("\n") if l.startswith("RESULT ")]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0][len("RESULT "):])

    expected = len(gen.build_packed(SYSTEM_CARTS))
    assert got["written"] == expected and got["carts"] == expected
    # A cart's worth of objects, not a roster's: the whole roster inflates to
    # 731 KB and this must never be holding it.
    assert 0 < got["retained"] < 400 * 1024, got
    print("\nunix MicroPython: seeded %d carts in %d ms; worst cart %r retains "
          "%d B; %d B still held after"
          % (got["written"], got["elapsed_ms"], got["retained_title"],
             got["retained"], got["leaked"]))


def test_the_whole_roster_seeds_inside_a_small_heap(tmp_path):
    """PEAK RAM, measured the only way an allocator cannot flatter: cap the
    heap and see whether the work completes.

    768 KB against a 731 KB roster, and it is a REAL bound rather than a
    generous one. Measured floors on this 64-bit build (where every dict/list
    node is twice an ESP32's, so a board has more room than these suggest):

        680 KB   `json.load(<the inflating stream>)` -- what ships
        896 KB   `json.loads(stream.read())` -- the obvious version, which
                 materializes the whole inflated document beside the objects
                 parsed out of it, and is ~40% slower for it

    So 768 KB sits between them on purpose: it passes what ships and fails the
    variant somebody will reach for while editing `unpack_seed`. The ceiling is
    set by ONE cart -- `Sakura Lua`, whose `cover` image is a single
    72,079-character JSON string -- so a roster that grows a bigger single
    asset is the other thing this catches, and the message says so.
    """
    _stage_for_micropython(tmp_path)
    r = _drive(tmp_path, heap="768k")
    assert r.returncode == 0, (
        "the packed seed no longer fits a 768 KB heap. Either the decoder is "
        "holding more than one cart at a time, or the roster grew a single "
        "asset bigger than sakura_lua's 72 KB cover image:\n%s\n%s"
        % (r.stdout[-2000:], r.stderr[-2000:]))
    assert "RESULT " in r.stdout
