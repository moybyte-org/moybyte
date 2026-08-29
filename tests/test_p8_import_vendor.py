"""The PICO-8 import is still moy-spec's, and it still agrees with the synth.

TWO files are VENDORED from moy-spec (`make vendor-p8-import`), and they travel
together. `tools/p8_import.py` converts the ASSETS; it is here rather than
reimplemented because SPEC.md is what says what its output MEANS -- 8.1 fixes
`57 = A4 = 440 Hz`, which is what puts PICO-8's pitch offset at 24, and 8.1's
keyed rest is what makes a ported slide glide from the right note. libmoy's
synth implements the far end of that same contract and is vendored here too, so
the converter and the synth have to come from one upstream, not two.

`tools/p8_lua_port.py` converts the CODE -- the cart's own Lua under a generated
PICO-8 compat shim -- and writes the `.moy` folder. Same rule, stronger reason:
the shim is written against SPEC.md's verb table verb for verb, so a shim
maintained here would drift from the very thing `.moy` exists to share. It
joined on 2026-08-29, when the browser's drop stopped emitting a Python porting
scaffold and started emitting a cart that RUNS.

WHAT WENT WRONG, and why this file exists. `tools/import_p8.py` used to carry
its own copy of the converter. Upstream worked out that PICO-8's tracker labels
its pitches two octaves below concert naming and corrected the offset 0 -> 24;
the copy here never heard about it, so every cart imported through this repo
came out two octaves flat. Nothing went red -- this repo's own tests had pinned
the wrong model, so re-syncing would have meant deliberately breaking a green
test, which is a thing people do not do. Ten days.

Four ways the copy stops being a copy, and a test for each:

  1. Somebody edits the vendored file (fastest way to fix something you found;
     survives exactly until the next re-vendor silently reverts it). The
     manifest's hashes make that red on the same day.
  2. Somebody edits moy-spec's copy and does not re-vendor -- the ACTUAL
     failure above. Only visible from here when a moy-spec checkout is around,
     so that test is conditional, and only fires when the checkout sits at the
     commit the manifest pins. Upstream having moved on is not drift; it is
     what a pin is for.
  3. Somebody re-implements a vendored verb in `tools/import_p8.py` or
     `tools/p8_writer.py` "just for now", and the file grows a second copy back.
     Both are checked, against BOTH vendored files: p8_writer is the half the
     BROWSER also runs (#194), so it is the one most likely to sprout "just a
     small local fix".
  4. Nobody edits anything, and the two vendored halves simply disagree --
     which is what a wrong pitch offset IS. Checked directly, against no
     checkout and no manifest: p8's A4 must be moy's A4 must be 440 Hz.
"""

import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
MANIFEST = os.path.join(TOOLS, "p8_import_vendor.json")
VENDORED = os.path.join(TOOLS, "p8_import.py")
PORTER = os.path.join(TOOLS, "p8_lua_port.py")
DRIVER = os.path.join(TOOLS, "import_p8.py")
WRITER = os.path.join(TOOLS, "p8_writer.py")
LIBMOY_AUDIO = os.path.join(
    ROOT, "native", "moy_audio", "libmoy", "moy_audio.c")


from vendor_check import (check_files_match, check_manifest_not_empty,
                          load_manifest, pinned_spec_or_skip, sha256)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-p8-import")


def test_manifest_is_not_empty(manifest):
    check_manifest_not_empty(manifest)


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "moy-spec's p8_import.py + p8_lua_port.py",
                      "vendor-p8-import")


def test_the_manifest_covers_both_vendored_files(manifest):
    """A second vendored file is only protected once it is IN the manifest --
    `VENDOR` and the stamp are written by the same run, so the way this goes
    wrong is somebody adding a file to the copy list and hand-editing the
    stamp."""
    import tools.vendor_p8_import as v
    assert set(manifest["files"]) == {"tools/" + n for n in v.VENDOR}


def test_no_drift_from_the_pinned_upstream(manifest):
    """When a moy-spec checkout is at the pinned commit, the bytes agree
    (vendor_check.pinned_spec_or_skip carries the skip ladder)."""
    spec = pinned_spec_or_skip(manifest, "p8_import.py")
    import tools.vendor_p8_import as v
    for name, rel in sorted(v.VENDOR.items()):
        src, dst = os.path.join(spec, rel), os.path.join(TOOLS, name)
        assert os.path.isfile(src), "%s is not in that checkout" % rel
        assert sha256(src) == sha256(dst), (
            "tools/%s differs from moy-spec's %s at the SAME commit -- one side "
            "was edited in place. Re-run `make vendor-p8-import` if upstream is "
            "right; otherwise the edit belongs upstream, because this is the "
            "copy that goes stale." % (name, rel))


def _toplevel_names(path):
    """Names this module DEFINES at the top level (imports don't count)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


@pytest.mark.parametrize("ours", [DRIVER, WRITER])
@pytest.mark.parametrize("theirs", [VENDORED, PORTER])
def test_our_half_does_not_redefine_a_vendored_name(ours, theirs):
    """Our two files DRIVE the vendored ones; neither re-grows one.

    A re-implemented verb beside the vendored one is how the duplication came
    back last time -- and it reads as an innocent local fix right up until the
    two disagree. `p8_writer.py` is checked as well as the CLI because it is the
    half that also runs inside the browser (#194), which is precisely where
    "just do the PNG bit here, it is only a few lines" is most tempting. And the
    PORTER is checked as well as the converter, because our writer stopped
    writing on 2026-08-29 -- the tempting local copy now is a `_mkdirs` or a
    `manifest` beside the one that already crossed.
    """
    # `main` is not a converter verb, it is what every one of these four files
    # is called through -- two CLIs cannot avoid colliding on it.
    shared = (_toplevel_names(ours) & _toplevel_names(theirs)) - {"main"}
    assert not shared, (
        "tools/%s defines %s, which tools/%s already defines. Import it from "
        "there instead: the vendored files are the ones moy-spec's SPEC.md and "
        "libmoy's synth are written against."
        % (os.path.basename(ours), ", ".join(sorted(shared)),
           os.path.basename(theirs)))


def test_the_porter_runs_where_it_has_to():
    """`p8_lua_port.py` is FROZEN INTO THE BROWSER, so it runs on MicroPython --
    and MicroPython's `re` is a subset that rejects a lookbehind, a `(?:...)`,
    an inline `(?m)` and a lookahead at COMPILE time ("regex too complex"). The
    upstream file used all four in `localization_lua` and `str.isalnum` in three
    more places; those were fixed UPSTREAM (2026-08-29) rather than shimmed,
    because neither a regex engine nor a str method can be injected from out
    here the way `os.path.basename` is.

    This is the cheap static half -- a re-vendor that reintroduces one fails
    here in milliseconds. tests/test_p8_micropython.py is the half that
    actually RUNS it, which is what proves the list below is complete.
    """
    with open(PORTER, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    def bad(what):
        raise AssertionError(
            "tools/p8_lua_port.py uses %s, which MicroPython does not have -- "
            "and the browser freezes and runs this file. Fix it UPSTREAM in "
            "moy-spec and re-vendor; a local edit here is a red test one line "
            "up." % what)

    # Docstrings are skipped deliberately: this file's header NAMES these
    # constructs to say it must not use them, and a raw substring scan would
    # red-flag the very sentence that keeps the rule.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    RE_ONLY = (("(?<", "a lookbehind"), ("(?=", "a lookahead"),
               ("(?!", "a negative lookahead"),
               ("(?:", "a non-capturing group"), ("(?m", "an inline flag"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            for needle, why in RE_ONLY:
                if needle in node.value:
                    bad(why + " in a pattern")
        elif isinstance(node, ast.Attribute):
            if node.attr == "isalnum":
                bad("str.isalnum")
            if (node.attr in ("path", "makedirs")
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "os"):
                bad("os." + node.attr)
        elif isinstance(node, ast.keyword) and node.arg == "indent":
            bad("json's indent=")


def test_the_converter_reaches_os_for_exactly_one_thing():
    """`p8_writer._ensure_os_path` supplies the converter with an
    `os.path.basename` on a tier that has no `os.path` (MicroPython has none,
    and no way to grow one). That shim is only sufficient while `basename` is
    the ONLY thing the converter asks `os` for -- a re-vendor that reached for
    `os.stat` or `os.sep` would make the browser and device import fail on a
    line nobody changed here.
    """
    with open(VENDORED, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    uses = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id == "os"):
            uses.add("os." + node.attr)
        elif (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "os"):
            uses.add("os.%s.%s" % (node.value.attr, node.attr))
    assert uses == {"os.path", "os.path.basename"}, (
        "tools/p8_import.py now reaches os for %s. The MicroPython tiers get "
        "`os.path.basename` injected by tools/p8_writer._ensure_os_path and "
        "nothing else -- widen that shim (or take the fix upstream) before this "
        "converter can run in the browser again." % ", ".join(sorted(uses)))


# -- 4. the two vendored halves have to agree on where A4 is -----------------

def _pico8_pitch_c0():
    """PICO8_PITCH_C0 straight out of the vendored source (no import)."""
    with open(VENDORED, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "PICO8_PITCH_C0":
                    return ast.literal_eval(node.value)
    raise AssertionError("PICO8_PITCH_C0 is not defined in tools/p8_import.py")


def test_pico8_a4_converts_to_440_hz():
    """SPEC.md 8.1: moy pitch 57 is A4 is 440 Hz. PICO-8's synth tunes ITS
    pitch 33 to 440 Hz (`key_to_freq(k) = 440 * 2^((k-33)/12)`), even though its
    tracker LABELS pitch 0 as "C0" -- the labels sit two octaves below concert
    naming. So the offset is 57 - 33 = 24, and a converter that reads the label
    literally lands every note two octaves flat while looking perfectly
    reasonable in a diff."""
    from runtime.audio import note_to_freq
    assert note_to_freq(_pico8_pitch_c0() + 33) == pytest.approx(440.0)
    # and the ends of PICO-8's 0..63 range stay inside moy's 0..95 pitch space
    assert 0 <= _pico8_pitch_c0() <= _pico8_pitch_c0() + 63 <= 95


def test_the_converter_and_the_vendored_synth_place_a4_together():
    """libmoy's synth is the other half of the same contract, vendored beside
    this one: `moy_audio.c` shifts a moy pitch back to a p8 key to pick the
    noise instrument's colour. If these two ever disagree, imported carts play
    in a key the synth was never told about."""
    with open(LIBMOY_AUDIO, encoding="utf-8") as f:
        src = f.read()
    needle = "pitch - %.1ff" % _pico8_pitch_c0()      # e.g. "pitch - 24.0f"
    assert needle in src, (
        "the vendored converter puts PICO-8's C0 at moy pitch %d, but "
        "%s does not contain %r -- the two vendored halves disagree about "
        "where A4 is, or one of them was re-vendored alone."
        % (_pico8_pitch_c0(), os.path.relpath(LIBMOY_AUDIO, ROOT), needle))
