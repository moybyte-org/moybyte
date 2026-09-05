"""#154: the store's publish scheme -- its cost, and its behaviour at every point
a power loss can land in the middle of it.

The cost model is METADATA OPS, not bytes: on glass a 450-byte save and a 16KB
save cost the same, and the difference between them and the ~50ms an in-place
overwrite costs was the five-op rename dance. So these count the ops through a
fake filesystem, and drive a crash through every one of them."""

import pytest

from runtime import moy_fs


class FakeFs:
    """An in-memory filesystem that RECORDS every metadata op and can die in the
    middle of any of them. `crash_at` is a 1-based op index and `landing` says how
    much of that op reached the card: NOTHING (open-for-write truncated and no
    more), HALF (FAT grows the file from empty, so a torn write is a prefix) or
    WHOLE (the write completed and the power went a moment later)."""

    def __init__(self):
        self.files = {}
        self.ops = []
        self.crash_at = None
        self.landing = "nothing"

    # -- the recorder --------------------------------------------------------
    def _op(self, name, path):
        self.ops.append(name + " " + path)
        if self.crash_at is not None and len(self.ops) == self.crash_at:
            return True
        return False

    def arm(self, step, landing="nothing"):
        """Die in the `step`-th op FROM HERE (the recorder restarts), leaving
        `landing` of that op's bytes behind."""
        self.ops = []
        self.crash_at, self.landing = step, landing

    # -- the os module moy_fs reaches for ------------------------------------
    def stat(self, path):
        self._op("stat", path)
        if path not in self.files:
            raise OSError("ENOENT")
        return (0,) * 10

    def remove(self, path):
        self._op("remove", path)
        if path not in self.files:
            raise OSError("ENOENT")
        del self.files[path]

    def rename(self, src, dst):
        self._op("rename", src)
        if src not in self.files:
            raise OSError("ENOENT")
        self.files[dst] = self.files.pop(src)

    def mkdir(self, path):
        self._op("mkdir", path)

    # -- open() -------------------------------------------------------------
    def open(self, path, mode="r"):
        if "w" in mode:
            dying = self._op("write", path)
            return _FakeWrite(self, path, dying)
        self._op("read", path)
        if path not in self.files:
            raise OSError("ENOENT")
        return _FakeRead(self.files[path])


class _Crash(BaseException):
    """A power loss: raised from the op the test chose to die in. NOT an
    Exception, because the machine stopping is not an ENOSPC -- nothing after it
    runs, including _write_atomic's own cleanup, which is what makes this a
    faithful model of pulling the plug."""


class _FakeWrite:
    def __init__(self, fs, path, dying):
        self.fs = fs
        self.path = path
        self.dying = dying
        self.buf = []
        fs.files[path] = ""            # open-for-write truncates, like FAT

    def write(self, data):
        self.buf.append(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        text = "".join(self.buf)
        if self.dying:
            land = self.fs.landing
            self.fs.files[self.path] = (
                text if land == "whole" else
                text[: len(text) // 2] if land == "half" else "")
            raise _Crash("power lost")
        self.fs.files[self.path] = text
        return False


class _FakeRead:
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def read(self):
        out = self.text[self.pos:]
        self.pos = len(self.text)
        return out

    def readline(self):
        nl = self.text.find("\n", self.pos)
        end = len(self.text) if nl < 0 else nl + 1
        out = self.text[self.pos:end]
        self.pos = end
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fs(monkeypatch):
    f = FakeFs()
    monkeypatch.setattr(moy_fs, "os", f)
    monkeypatch.setattr(moy_fs, "open", f.open, raising=False)
    monkeypatch.setattr(moy_fs, "_roots", [])
    monkeypatch.setattr(moy_fs, "_marks", {})
    return f


@pytest.fixture
def rooted(fs):
    """A store root, the way moy_carts.ensure_dirs registers one -- so the reader
    is on the marker path rather than the per-file fallback."""
    moy_fs.set_publish_root("/c")
    return fs


def _rename_dance(path, data):
    """The pre-#154 body, verbatim in shape, so the op counts compare on the same
    recorder rather than against a number written down in a report."""
    tmp, bak = path + ".tmp", path + ".bak"
    moy_fs._write(tmp, data)
    if moy_fs._exists(path):
        moy_fs._remove(bak)
        moy_fs.os.rename(path, bak)
    moy_fs.os.rename(tmp, path)


OLD = "print('old')\n"
NEW = "print('a rather longer new file')\n"


# -- the cost ---------------------------------------------------------------

def test_a_save_costs_three_metadata_ops(rooted):
    fs = rooted
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.ops = []
    moy_fs._write_atomic("/c/main.py", NEW)

    assert fs.ops == ["write /c/.publish", "write /c/main.py.bak",
                      "write /c/main.py"]


def test_a_save_outside_every_root_costs_two(fs):
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.ops = []
    moy_fs._write_atomic("/c/main.py", NEW)

    assert fs.ops == ["write /c/main.py.bak", "write /c/main.py"]


def test_the_rename_dance_it_replaced_cost_five(fs):
    _rename_dance("/c/main.py", OLD)
    fs.ops = []
    _rename_dance("/c/main.py", NEW)

    assert fs.ops == ["write /c/main.py.tmp", "stat /c/main.py",
                      "remove /c/main.py.bak", "rename /c/main.py",
                      "rename /c/main.py.tmp"]


def test_a_boot_shaped_scan_reads_the_marker_once_not_a_backup_per_file(rooted):
    """The shape of a P4 boot: every manifest in the store read through
    _read_recover. On littlefs a path lookup is the expensive op, so the cost of
    the torn-write detection has to be ONE read for the whole scan, not one per
    file -- opening a `.bak` per manifest measured +6.0s of boot on 75 carts."""
    fs = rooted
    for i in range(20):
        moy_fs._write_atomic("/c/cart%d/manifest.json" % i, '{"n": %d}' % i)
    moy_fs._marks.clear()             # a fresh boot
    fs.ops = []

    for i in range(20):
        assert moy_fs._read_recover("/c/cart%d/manifest.json" % i) == '{"n": %d}' % i

    assert len(fs.ops) == 21
    assert fs.ops.count("read /c/.publish") == 1


def test_without_a_root_the_reader_falls_back_to_a_lookup_per_read(fs):
    """A path under no registered root -- a bare moy_journal on a tmp dir -- keeps
    the pre-marker behaviour: correct, one extra lookup, and never wrong."""
    moy_fs._write_atomic("/c/main.py", NEW)
    fs.ops = []
    assert moy_fs._read_recover("/c/main.py") == NEW

    assert fs.ops == ["read /c/main.py", "read /c/main.py.bak"]


# -- the crash ladder -------------------------------------------------------

@pytest.mark.parametrize("step", [1, 2, 3])
@pytest.mark.parametrize("landing", ["nothing", "half", "whole"])
def test_a_crash_at_any_landing_point_still_reads_a_whole_file(rooted, step,
                                                               landing):
    """Every point a power loss can land: three writes (marker, backup, publish)
    times three ways each can land. Whatever the reader gets back is one of the
    two WHOLE versions -- never a fragment, never nothing."""
    fs = rooted
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(step, landing)
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None
    moy_fs._marks.clear()             # the next boot re-reads the marker from disk

    assert moy_fs._read_recover("/c/main.py") in (OLD, NEW)


def test_a_marker_naming_a_file_whose_backup_is_torn_trusts_the_file(rooted):
    """The crash landed between the marker and the backup: the marker says a
    publish of this file was in flight, and the backup cannot vouch for itself.
    A torn backup is refused, so the previous save stands."""
    fs = rooted
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(2, "half")
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None
    moy_fs._marks.clear()

    assert moy_fs._read_recover("/c/main.py") == OLD


def test_a_stale_marker_from_a_previous_process_costs_nothing(rooted):
    """The marker is never cleared, so at boot it names the last file published --
    normally one that completed. That must cost a read of the file and nothing
    else: the marker's own stamp answers it, with no lookup of the backup."""
    fs = rooted
    moy_fs._write_atomic("/c/main.py", NEW)
    moy_fs._marks.clear()             # a fresh process
    fs.ops = []

    assert moy_fs._read_recover("/c/main.py") == NEW
    assert fs.ops == ["read /c/main.py", "read /c/.publish"]


def test_a_crash_in_the_backup_keeps_the_published_file(fs):
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(1, "half")
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None

    # `path` was never opened, so the previous save stands and the half-written
    # backup is refused rather than published over it.
    assert moy_fs._read_recover("/c/main.py") == OLD


def test_a_truncated_publish_is_finished_from_the_backup(fs):
    """A FAT open-for-write truncates and then grows the file, so an interrupted
    publish leaves a PREFIX of the new bytes -- anywhere from empty to whole."""
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(2, "half")
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None

    assert moy_fs._read_recover("/c/main.py") == NEW
    assert fs.files["/c/main.py"] == NEW      # healed on disk, not re-derived per read


def test_an_empty_publish_is_finished_from_the_backup(fs):
    """The far end of the same prefix: the truncation landed and nothing else did."""
    moy_fs._write_atomic("/c/main.py", OLD)
    moy_fs._write_atomic("/c/main.py", NEW)
    fs.files["/c/main.py"] = ""

    assert moy_fs._read_recover("/c/main.py") == NEW


def test_a_crash_between_the_two_writes_keeps_the_previous_save(fs):
    """The backup is whole and the publish never started, so `path` holds the
    previous save -- not a prefix of anything. That save is what survives (the
    guarantee the rename dance gave), and the stamp that outlived its publish is
    dropped rather than left to confuse a later read."""
    moy_fs._write_atomic("/c/main.py", OLD)
    moy_fs._write("/c/main.py.bak", moy_fs._stamp_line(NEW) + NEW)

    assert moy_fs._read_recover("/c/main.py") == OLD
    assert "/c/main.py.bak" not in fs.files


def test_a_foreign_write_is_trusted_and_retires_the_stale_stamp(fs):
    """tools/push_cart.py places a file with remove+rename over the dev channel,
    and the kid may have saved that same file on the board first. Recovering the
    kid's version over the push would be a silent undo of a deliberate write."""
    pushed = "print('pushed from the PC')\n"
    assert len(pushed) != len(NEW)
    moy_fs._write_atomic("/c/main.py", NEW)          # the kid's save, stamped
    moy_fs._write("/c/main.py", pushed)              # ...then the push lands

    assert moy_fs._read_recover("/c/main.py") == pushed
    assert "/c/main.py.bak" not in fs.files          # and cannot bite the next read


def test_a_same_length_foreign_write_is_trusted_too(fs):
    """Same rule, and the honest cost of it: a same-length wrong-content publish
    is indistinguishable from a deliberate write, so it is not recoverable."""
    moy_fs._write_atomic("/c/main.py", NEW)
    other = "x" * len(NEW)
    moy_fs._write("/c/main.py", other)

    assert moy_fs._read_recover("/c/main.py") == other
    assert "/c/main.py.bak" not in fs.files


def test_a_missing_publish_recovers_from_the_backup(fs):
    moy_fs._write_atomic("/c/main.py", NEW)
    del fs.files["/c/main.py"]

    assert moy_fs._read_recover("/c/main.py") == NEW


def test_a_recovery_that_cannot_republish_still_returns_the_bytes(fs, monkeypatch):
    """The heal is a courtesy -- it saves the NEXT read the same work. A medium
    that refuses the write must not turn a read that DID recover into a failure."""
    moy_fs._write_atomic("/c/main.py", NEW)
    del fs.files["/c/main.py"]

    def read_only(path, mode="r"):
        if "w" in mode:
            raise OSError("EROFS")
        return fs.open(path, mode)
    monkeypatch.setattr(moy_fs, "open", read_only)

    assert moy_fs._read_recover("/c/main.py") == NEW


def test_no_backup_at_all_still_reads_and_still_raises(fs):
    moy_fs._write("/c/plain.py", OLD)
    assert moy_fs._read_recover("/c/plain.py") == OLD
    with pytest.raises(OSError):
        moy_fs._read_recover("/c/absent.py")


# -- the upgrade-in-place cases ---------------------------------------------

def test_a_legacy_backup_is_read_whole_and_never_second_guessed(fs):
    """Every .bak already on a board's card was written by the rename dance and
    carries no stamp. It stays a usable backup, and a published file beside one is
    trusted as-is -- there is nothing to check it against."""
    moy_fs._write("/c/main.py", NEW)
    moy_fs._write("/c/main.py.bak", OLD)

    assert moy_fs._read_recover("/c/main.py") == NEW
    del fs.files["/c/main.py"]
    assert moy_fs._read_recover("/c/main.py") == OLD


def test_a_file_whose_first_line_looks_like_a_stamp_is_not_one(fs):
    moy_fs._write("/c/main.py", NEW)
    moy_fs._write("/c/main.py.bak", "#moyfs1 nope\n" + OLD)

    assert moy_fs._read_recover("/c/main.py") == NEW
    del fs.files["/c/main.py"]
    assert moy_fs._read_recover("/c/main.py") == "#moyfs1 nope\n" + OLD


def test_forget_bak_drops_a_stamp_a_foreign_writer_would_strand(fs):
    """The scheme's one ask of the rest of the system: a writer that publishes
    different bytes without going through _write_atomic must drop the backup, or
    the stamp beside it describes content that is no longer there."""
    moy_fs._write_atomic("/c/main.py", OLD)
    moy_fs._forget_bak("/c/main.py")
    moy_fs._write("/c/main.py", NEW)

    assert moy_fs._read_recover("/c/main.py") == NEW


# -- nothing of ours reaches the published file ------------------------------

def test_the_published_file_is_the_payload_and_nothing_else(fs):
    """The store scan, the web sync RPC and tools/ all read these files directly.
    The stamp lives in the backup precisely so none of them ever sees it."""
    moy_fs._write_atomic("/c/manifest.json", '{"title": "X"}')

    assert fs.files["/c/manifest.json"] == '{"title": "X"}'
    assert fs.files["/c/manifest.json.bak"].endswith('{"title": "X"}')
    assert fs.files["/c/manifest.json.bak"].startswith("#moyfs1 ")


def test_a_multibyte_payload_round_trips(fs):
    text = "# café — über\nprint('✓')\n"
    moy_fs._write_atomic("/c/main.py", text)
    fs.files["/c/main.py"] = text[:4]

    assert moy_fs._read_recover("/c/main.py") == text


def test_an_empty_payload_is_a_payload(fs):
    moy_fs._write_atomic("/c/main.py", "")
    assert moy_fs._read_recover("/c/main.py") == ""
    del fs.files["/c/main.py"]
    assert moy_fs._read_recover("/c/main.py") == ""
