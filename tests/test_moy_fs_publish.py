"""#154: the store's publish scheme (native/moy_store/moy_fs.c, the module
`moy_store` on every tier, runtime/carts/moy_fs.py's names for it) -- what a
read makes of each state a power loss can leave, built by hand on real
files. The cut at every block write, on FAT and littlefs, is the power-cut
matrix's (tests/test_moy_store.py, tools/native_check.py --component fs)."""

import os

import pytest

from runtime import moy_fs
import moy_store

OLD = "print('old')\n"
NEW = "print('a rather longer new file')\n"


@pytest.fixture
def d(tmp_path):
    return str(tmp_path)


@pytest.fixture
def rooted(tmp_path):
    """A store root, the way moy_carts.ensure_dirs registers one -- so the
    reader is on the marker path rather than the per-file fallback. The
    table is the process's, eight roots long, so it starts empty."""
    moy_store._roots_clear()
    moy_fs.set_publish_root(str(tmp_path))
    return str(tmp_path)


def _put(path, text):
    with open(path, "w") as f:
        f.write(text)


def _get(path):
    with open(path) as f:
        return f.read()


def test_a_save_leaves_a_marker_a_backup_and_the_payload(rooted):
    p = rooted + "/main.py"
    moy_fs._write_atomic(p, NEW)
    assert _get(p) == NEW
    assert _get(p + ".bak") == moy_fs._stamp_line(NEW) + NEW
    n, crc = moy_fs._stamp_of(NEW)
    assert _get(rooted + "/" + moy_fs._PUBLISH) == "#moyfs1 %d %d %s\n" % (n, crc, p)


def test_a_save_outside_every_root_writes_no_marker(d):
    moy_fs._write_atomic(d + "/main.py", NEW)
    assert sorted(os.listdir(d)) == ["main.py", "main.py.bak"]


def test_a_stepped_save_is_the_same_save(rooted):
    p = rooted + "/a.py"
    steps = moy_fs._write_atomic_steps(p, NEW)
    next(steps)
    assert not os.path.exists(p) and _get(p + ".bak").endswith(NEW)
    with pytest.raises(StopIteration):
        next(steps)
    assert _get(p) == NEW and moy_fs._read_recover(p) == NEW


def test_a_marker_naming_a_file_whose_backup_is_torn_trusts_the_file(rooted):
    p = rooted + "/main.py"
    moy_fs._write_atomic(p, OLD)
    moy_fs._write_atomic(p, NEW)
    _put(p, OLD)
    _put(p + ".bak", (moy_fs._stamp_line(NEW) + NEW)[:-6])     # torn
    moy_store._reboot()
    assert moy_fs._read_recover(p) == OLD


def test_a_stale_marker_from_a_previous_process_reads_the_file(rooted):
    p = rooted + "/main.py"
    moy_fs._write_atomic(p, NEW)
    moy_store._reboot()
    assert moy_fs._read_recover(p) == NEW


def test_a_torn_backup_keeps_the_published_file(d):
    p = d + "/main.py"
    moy_fs._write_atomic(p, OLD)
    _put(p + ".bak", (moy_fs._stamp_line(NEW) + NEW)[:9])
    assert moy_fs._read_recover(p) == OLD


@pytest.mark.parametrize("cut", (0, 5, len(NEW) - 1))
def test_a_truncated_publish_is_finished_from_the_backup(rooted, cut):
    """A FAT open-for-write truncates and then grows the file, so an
    interrupted publish leaves a PREFIX of the new bytes."""
    p = rooted + "/main.py"
    moy_fs._write_atomic(p, OLD)
    moy_fs._write_atomic(p, NEW)
    _put(p, NEW[:cut])
    moy_store._reboot()
    assert moy_fs._read_recover(p) == NEW
    assert _get(p) == NEW                     # healed on disk


def test_a_crash_between_the_two_writes_keeps_the_previous_save(d):
    p = d + "/main.py"
    moy_fs._write_atomic(p, OLD)
    _put(p + ".bak", moy_fs._stamp_line(NEW) + NEW)
    assert moy_fs._read_recover(p) == OLD
    assert not os.path.exists(p + ".bak")


@pytest.mark.parametrize("other", ("print('pushed from the PC')\n", "x" * len(NEW)))
def test_a_foreign_write_is_trusted_and_retires_the_stale_stamp(d, other):
    """tools/push_cart.py places a file over the dev channel; recovering the
    kid's version over it would undo a deliberate write. A same-length one is
    indistinguishable from it, the rule's honest cost."""
    p = d + "/main.py"
    moy_fs._write_atomic(p, NEW)
    _put(p, other)
    assert moy_fs._read_recover(p) == other
    assert not os.path.exists(p + ".bak")


def test_a_missing_publish_recovers_from_the_backup(d):
    p = d + "/main.py"
    moy_fs._write_atomic(p, NEW)
    os.remove(p)
    assert moy_fs._read_recover(p) == NEW


def test_no_backup_at_all_still_reads_and_still_raises(d):
    _put(d + "/plain.py", OLD)
    assert moy_fs._read_recover(d + "/plain.py") == OLD
    with pytest.raises(OSError):
        moy_fs._read_recover(d + "/absent.py")


def test_a_legacy_backup_is_read_whole_and_never_second_guessed(d):
    p = d + "/main.py"
    _put(p, NEW)
    _put(p + ".bak", OLD)
    assert moy_fs._read_recover(p) == NEW
    os.remove(p)
    assert moy_fs._read_recover(p) == OLD


def test_a_file_whose_first_line_looks_like_a_stamp_is_not_one(d):
    p = d + "/main.py"
    _put(p, NEW)
    _put(p + ".bak", "#moyfs1 nope\n" + OLD)
    assert moy_fs._read_recover(p) == NEW
    os.remove(p)
    assert moy_fs._read_recover(p) == "#moyfs1 nope\n" + OLD


def test_forget_bak_drops_a_stamp_a_foreign_writer_would_strand(d):
    p = d + "/main.py"
    moy_fs._write_atomic(p, OLD)
    moy_fs._forget_bak(p)
    _put(p, NEW)
    assert moy_fs._read_recover(p) == NEW


def test_a_multibyte_payload_round_trips(d):
    p = d + "/main.py"
    text = "# café — über\nprint('✓')\n"
    moy_fs._write_atomic(p, text)
    with open(p, "wb") as f:
        f.write(text.encode()[:4])
    assert moy_fs._read_recover(p) == text


def test_an_empty_payload_is_a_payload(d):
    p = d + "/main.py"
    moy_fs._write_atomic(p, "")
    assert moy_fs._read_recover(p) == ""
    os.remove(p)
    assert moy_fs._read_recover(p) == ""


def test_a_claimed_backup_moves_and_reads_back_stamped(d):
    p, dest = d + "/main.py", d + "/snap"
    moy_fs._write_atomic(p, NEW)
    assert not moy_fs._claim_bak(p, dest, (1, 2))
    assert moy_fs._claim_bak(p, dest, moy_fs._stamp_of(NEW))
    assert not os.path.exists(p + ".bak") and moy_fs._read_stamped(dest) == NEW
