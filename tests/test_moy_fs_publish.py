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
    middle of any of them. `crash_at` is a 1-based op index; `tear` decides
    whether that op leaves half its bytes behind (FAT truncates on open-for-write)
    or nothing at all."""

    def __init__(self):
        self.files = {}
        self.ops = []
        self.crash_at = None
        self.tear = False

    # -- the recorder --------------------------------------------------------
    def _op(self, name, path):
        self.ops.append(name + " " + path)
        if self.crash_at is not None and len(self.ops) == self.crash_at:
            return True
        return False

    def arm(self, step, tear=False):
        """Die in the `step`-th op FROM HERE (the recorder restarts), leaving half
        that op's bytes behind when `tear`."""
        self.ops = []
        self.crash_at, self.tear = step, tear

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


class _Crash(Exception):
    """A power loss: raised from the op the test chose to die in."""


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
            # half the bytes reached the card, or none of them did
            self.fs.files[self.path] = text[: len(text) // 2] if self.fs.tear else ""
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
    return f


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

def test_a_save_costs_two_metadata_ops(fs):
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


def test_a_recovered_read_costs_one_op_more_than_a_plain_one(fs):
    """What the scheme charges for the torn-write detection: the published file,
    then the stamp line beside it -- never the backup's payload unless the stamp
    says the published file cannot be trusted."""
    moy_fs._write_atomic("/c/main.py", NEW)
    fs.ops = []
    assert moy_fs._read_recover("/c/main.py") == NEW

    assert fs.ops == ["read /c/main.py", "read /c/main.py.bak"]


# -- the crash ladder -------------------------------------------------------

@pytest.mark.parametrize("step", [1, 2])
@pytest.mark.parametrize("tear", [False, True])
def test_a_crash_at_any_step_still_reads_a_whole_file(fs, step, tear):
    """Omit or truncate each of the two writes in turn. Whatever the reader gets
    back is one of the two WHOLE versions -- never a fragment, never nothing."""
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(step, tear)
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None

    assert moy_fs._read_recover("/c/main.py") in (OLD, NEW)


def test_a_crash_in_the_backup_keeps_the_published_file(fs):
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(1, tear=True)
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None

    # `path` was never opened, so the previous save stands and the half-written
    # backup is refused rather than published over it.
    assert moy_fs._read_recover("/c/main.py") == OLD


def test_a_crash_in_the_publish_recovers_the_save_that_reached_the_backup(fs):
    moy_fs._write_atomic("/c/main.py", OLD)
    fs.arm(2, tear=True)
    with pytest.raises(_Crash):
        moy_fs._write_atomic("/c/main.py", NEW)
    fs.crash_at = None

    assert moy_fs._read_recover("/c/main.py") == NEW
    assert fs.files["/c/main.py"] == NEW      # healed on disk, not re-derived per read


def test_a_same_length_tear_is_caught_by_the_stamp(fs):
    """Length alone would miss this one: the publish is overwritten with a file of
    exactly the right size and the wrong bytes."""
    moy_fs._write_atomic("/c/main.py", NEW)
    fs.files["/c/main.py"] = "x" * len(NEW)

    assert moy_fs._read_recover("/c/main.py") == NEW


def test_a_missing_publish_recovers_from_the_backup(fs):
    moy_fs._write_atomic("/c/main.py", NEW)
    del fs.files["/c/main.py"]

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
