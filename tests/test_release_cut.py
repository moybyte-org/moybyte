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
