"""The store from a task that is not the VM's (native/moy_store/moy_vol.h,
THE STORE'S LOCK and THE VOLUME TABLE), on the desktop MicroPython: a thread
of no VM (moy_store.task, the tests' task: what a compiled or Lua program's
own task does through the files rows) publishes, reads back, renames and lists
files on a volume while the VM's Python hammers the same volume through its
VFS -- open, write, read, rename, listdir -- and the store's own calls. On the
kernel's internal flash (KVfs, its RAM medium) and on the card's FAT volume
(its native RAM medium): no op is refused, every file of either side reads
back as written, and the lock's meter has counted the holds. A volume the
table does not hold (VfsLfs2 over a Python block device) is refused to the
task, which then belongs on the VM's task."""

import subprocess

import pytest

from unix_mp import find_unix_mp, require_unix_mp

ROUNDS = 400

SCRIPT = """
import os, vfs, moy_store
FS = "@FS@"
if FS == "kv":
    moy_store.kvol_load(None)
    vfs.mount(moy_store.KVfs(), "/v")
elif FS == "fat":
    vfs.mount(moy_store.card_ram(16384), "/v")
else:
    class Ram:
        def __init__(self, n):
            self.n = n
            self.data = bytearray(n * 4096)
        def readblocks(self, b, buf, off=0):
            a = b * 4096 + off
            buf[:] = self.data[a:a + len(buf)]
        def writeblocks(self, b, buf, off=None):
            a = b * 4096 + (off or 0)
            self.data[a:a + len(buf)] = buf
        def ioctl(self, op, arg):
            return {4: self.n, 5: 4096, 6: 0}.get(op)
    bd = Ram(256)
    vfs.VfsLfs2.mkfs(bd)
    vfs.mount(vfs.VfsLfs2(bd), "/v")
moy_store.mkdir("/v/task")
os.mkdir("/v/py")
moy_store.lock_stats(True)
moy_store.task("/v/task", @ROUNDS@)
bad = []
for i in range(@PYROUNDS@):
    a = "/v/py/p%d.txt" % i
    b = "/v/py/q%d.txt" % i
    text = "python round %d " % i * 8
    with open(a, "w") as f:
        f.write(text)
    with open(a) as f:
        if f.read() != text:
            bad.append(("read", i))
    os.rename(a, b)
    if "q%d.txt" % i not in os.listdir("/v/py"):
        bad.append(("list", i))
    moy_store.publish("/v/py/s%d.txt" % i, text)
    if moy_store.read_recover("/v/py/s%d.txt" % i) != text:
        bad.append(("publish", i))
ok, at, rc, refused = moy_store.task_join()
print("task", ok, at, rc, refused)
for i in range(@ROUNDS@):
    try:
        with open("/v/task/r%d.txt" % i) as f:
            if f.read() != "task round %d" % i:
                bad.append(("task file", i))
    except OSError as e:
        bad.append(("task file", i, e.errno))
for i in range(@PYROUNDS@):
    with open("/v/py/q%d.txt" % i) as f:
        if f.read() != "python round %d " % i * 8:
            bad.append(("python file", i))
print("bad", bad[:5])
holds, hold_us, wait_us, vm_wait_us, waits = moy_store.lock_stats()
print("held", holds > @ROUNDS@, waits > 0 or FS == "lfs2")
print("longest", hold_us, wait_us, vm_wait_us)
"""


def _run(tmp_path, exe, fs):
    script = tmp_path / ("tasks_%s.py" % fs)
    py = ROUNDS if fs != "lfs2" else 0
    script.write_text(SCRIPT.replace("@FS@", fs).replace("@ROUNDS@", str(ROUNDS))
                      .replace("@PYROUNDS@", str(py)))
    done = subprocess.run([str(exe), "-X", "heapsize=32M", str(script)], capture_output=True, text=True,
                          timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.splitlines()


@pytest.mark.parametrize("fs", ["kv", "fat"])
def test_a_task_and_the_vm_work_one_volume_and_every_file_reads_back(tmp_path, fs):
    exe = require_unix_mp("moy_store", why="the store from a task that is not the VM's")
    out = _run(tmp_path, exe, fs)
    assert out[0] == "task %d -1 0 0" % ROUNDS, out
    assert out[1] == "bad []", out
    assert out[2] == "held True True", out


@pytest.mark.parametrize("fs", ["kv", "fat"])
def test_on_the_boards_object_model_with_one_gil_too(tmp_path, fs):
    """The boards' build runs its threads under one GIL: the task, holding no
    GIL, takes the store's lock while the VM's thread holds the GIL."""
    exe = find_unix_mp("moy_store", board_model=True)
    if exe is None:
        pytest.skip("no 32-bit desktop MicroPython (gcc-multilib)")
    out = _run(tmp_path, exe, fs)
    assert out[:3] == ["task %d -1 0 0" % ROUNDS, "bad []", "held True True"], out


def test_a_volume_the_table_does_not_hold_is_refused_to_the_task(tmp_path):
    exe = require_unix_mp("moy_store", why="the store from a task that is not the VM's")
    out = _run(tmp_path, exe, "lfs2")
    assert out[0] == "task 0 0 %d %d" % (19, ROUNDS), out


WALK = """
import os, time, vfs, moy_store
vfs.mount(moy_store.card_ram(16384), "/v")
moy_store.mkdir("/v/w")
moy_store.lock_stats(True)
moy_store.task("/v/w", -@MS@)
time.sleep_ms(50)
worst = 0
for i in range(@OPS@):
    t = time.ticks_us()
    moy_store.publish("/v/w/vm%d.txt" % i, "vm %d" % i)
    worst = max(worst, time.ticks_diff(time.ticks_us(), t))
    time.sleep_ms(20)
ok, at, rc, refused = moy_store.task_join()
holds, hold_us, wait_us, vm_wait_us, waits = moy_store.lock_stats()
print("walk", ok > 0, refused)
print("vm", worst, vm_wait_us)
"""


@pytest.mark.parametrize("board_model", [False, True])
def test_a_long_walk_lets_the_vm_in_between_its_entries(tmp_path, board_model):
    """A program's op that walks a folder entry by entry holds the gate the
    whole walk and yields between entries (moy_store_yield) while the VM waits:
    the VM's ops on the same volume each wait for one entry, never for the
    walk."""
    exe = (find_unix_mp("moy_store", board_model=True) if board_model
           else require_unix_mp("moy_store", why="the store from a task that is not the VM's"))
    if exe is None:
        pytest.skip("no 32-bit desktop MicroPython (gcc-multilib)")
    ms, ops = 1000, 20
    script = tmp_path / "walk.py"
    script.write_text(WALK.replace("@MS@", str(ms)).replace("@OPS@", str(ops)))
    done = subprocess.run([str(exe), "-X", "heapsize=32M", str(script)], capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout.splitlines()
    assert out[0] == "walk True 0", out
    worst, vm_wait = (int(x) for x in out[1].split()[1:])
    assert worst < ms * 1000 // 4 and vm_wait < ms * 1000 // 4, out
