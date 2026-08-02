"""Cutting a release: the version bump that rides the dev -> master merge.

`make release` (tools/release.py) is the only thing that touches
FIRMWARE_VERSION, so the transform is worth pinning: it is edited into a frozen
device module by regex, and a bad edit ships a firmware whose version is a
syntax error or -- worse, because nothing would notice -- unchanged.

The git sequence around it isn't simulated here; the pieces that can silently go
wrong are the source edit and the preconditions, and those are pure.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import release  # noqa: E402


SRC = '''\
#   FIRMWARE_VERSION -- monotonic build number, bumped per stable release.
#       Standing guidance lives up here and must survive a bump.
FIRMWARE_VERSION = 2            # v2: the SD<->display boot fix (#56), Sky Run
                                # (#54), and the OTA online path (#53).
FIRMWARE_CHANNEL = "stable"
'''


def test_the_bump_increments_and_rewrites_the_note():
    out, new = release.bump_text(SRC, "the dev/master split")
    assert new == 3
    assert "FIRMWARE_VERSION = 3            # v3: the dev/master split\n" in out
    # The note described v2, so it goes with v2 -- all of it, including the
    # lines it ran onto.
    assert "Sky Run" not in out
    assert "(#54)" not in out
    # Standing guidance above the constant is not a release note; it stays.
    assert "Standing guidance lives up here" in out
    # Nothing below it moved.
    assert out.endswith('FIRMWARE_CHANNEL = "stable"\n')


def test_a_bump_without_notes_still_labels_the_version():
    out, new = release.bump_text(SRC)
    assert new == 3
    assert "FIRMWARE_VERSION = 3            # v3\n" in out


def test_bumps_compose():
    """Two releases in a row land on N+2, not on a mangled line."""
    once, _ = release.bump_text(SRC, "first")
    twice, new = release.bump_text(once, "second")
    assert new == 4
    assert "FIRMWARE_VERSION = 4            # v4: second\n" in twice
    assert "first" not in twice


def test_a_missing_constant_stops_the_release():
    with pytest.raises(release.Stop):
        release.bump_text("FIRMWARE_CHANNEL = 'stable'\n")


def test_the_real_module_is_still_shaped_the_way_the_bump_expects():
    """The regex edits a file this tool does not own. If moy_ota's declaration
    ever changes shape, fail here rather than in the middle of a release."""
    text = Path(release.MOY_OTA).read_text(encoding="utf-8")
    assert len(release.VERSION_RE.findall(text)) == 1
    out, new = release.bump_text(text, "smoke")
    assert new == release.read_version() + 1
    assert re.search(r"^FIRMWARE_VERSION = %d\b" % new, out, re.MULTILINE)


def test_it_releases_dev_into_master_and_not_the_other_way():
    assert release.DEV == "dev"
    assert release.MAIN == "master"


def test_make_release_is_wired_up():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "tools/release.py" in mk
    assert re.search(r"^release:", mk, re.MULTILINE)
    assert "release" in re.search(r"^VENV_TARGETS :=(.+?)\n\n", mk,
                                  re.MULTILINE | re.DOTALL).group(1)


# -- the human name (the only version anyone outside the code reads) ---------

def test_the_name_is_a_pure_transform():
    src = 'FIRMWARE_NAME = "0.6"\nFIRMWARE_CHANNEL = "stable"\n'
    out = release.name_text(src, "0.7")
    assert 'FIRMWARE_NAME = "0.7"' in out
    assert 'FIRMWARE_CHANNEL = "stable"' in out      # nothing else disturbed


def test_the_name_keeps_its_trailing_comment():
    src = 'FIRMWARE_NAME = "0.6"   # what a human calls it\n'
    assert release.name_text(src, "1.0") == \
        'FIRMWARE_NAME = "1.0"   # what a human calls it\n'


def test_a_missing_name_constant_is_a_clear_stop():
    try:
        release.name_text("FIRMWARE_VERSION = 2\n", "0.7")
    except release.Stop as exc:
        assert "FIRMWARE_NAME" in str(exc)
    else:
        raise AssertionError("a missing constant passed silently")


def _load_moy_ota():
    import importlib.util
    spec = importlib.util.spec_from_file_location("moy_ota_rel", release.MOY_OTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_shipped_module_carries_a_well_shaped_name():
    name = release.read_name()
    assert release.NAME_SHAPE.match(name), name
    assert _load_moy_ota().FIRMWARE_NAME == name


def test_the_label_a_kid_reads_prefers_the_name_over_the_counter():
    """Precedence: the build's stamp, else the release name, else the counter.

    Asserted by setting the module globals rather than by reading whatever this
    machine last built -- moy_ota imports a gitignored `_ota_build` when one is
    lying around, so "what does version_label() say here" is a fact about the
    developer's last build, not about the source.
    """
    mod = _load_moy_ota()
    u = mod.OtaUpdater(lambda fn: fn())

    mod.FIRMWARE_LABEL, mod.FIRMWARE_NAME, mod.FIRMWARE_VERSION = None, "0.6", 3
    assert u.version_label() == "0.6", "the counter leaked out to the kid"

    # A beta stamps its own label, and it must win -- its VERSION is an epoch.
    mod.FIRMWARE_LABEL = "beta 2026-08-02 14:02"
    assert u.version_label() == "beta 2026-08-02 14:02"

    # And with neither, the raw counter is the honest last resort.
    mod.FIRMWARE_LABEL, mod.FIRMWARE_NAME = None, None
    assert u.version_label() == "v3"


def test_a_malformed_name_never_becomes_a_tag():
    # It ends up in a git tag and on the kid's update screen, so "v0.6" or "0,6"
    # has to fail here rather than halfway through a merge.
    for bad in ("v0.6", "0,6", "0.6-rc1", "0", "", "0.6.1.2"):
        assert not release.NAME_SHAPE.match(bad), bad
    for good in ("0.6", "1.0", "0.6.1", "12.34.56"):
        assert release.NAME_SHAPE.match(good), good
