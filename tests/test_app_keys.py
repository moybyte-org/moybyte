"""The console's key store for apps (runtime/system/app_keys.py): moybyte's
key_make, key_public and key_shared, console calls served by the scheduler
as the calling process's app, on the host and on the VM a board runs (the
unix MicroPython); one app never reaches another's pair, the secret is a
file of the store's system folder that the web sync does not carry, and a
cart's removal (pkg remove, the shell's Delete) deletes its keys."""

import os
import shutil
import subprocess

import pytest

import crypto_vectors as V
from runtime import app_keys, moybyte, scheduler as S
from runtime import moy_log as L
from unix_mp import require_unix_mp, runtime_tree

H = bytes.fromhex
WEAK = "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"
APPS = {5: "t.one", 6: "T.two"}


class _Procs:
    def app_id(self, pid):
        return APPS.get(pid)


class _Console:
    def __init__(self, root):
        self.carts_root = root
        self.procs = _Procs()


@pytest.fixture
def console(tmp_path):
    root = str(tmp_path / "carts")
    os.mkdir(root)
    S.attach(_Console(root))
    moybyte.WAKES.keys = S.SCHED.keys
    yield root
    S.SCHED.ws = None
    S.SCHED.app_of = None
    S.SCHED.cart_of = None


def _as(pid, name, *args):
    was = L.current()
    L.current(pid)
    try:
        return S._CALLS["moybyte." + name](*args)
    finally:
        L.current(was)


def _x25519(secret, peer=None):
    from runtime import moy_app
    return moy_app.x25519(secret, peer)


def test_an_app_s_pair_is_made_once_and_answers_its_exchange(console):
    one = _as(5, "key_make", "node")
    assert len(one) == 64 and _as(5, "key_make", "node") == one, "made once"
    assert _as(5, "key_public", "node") == one
    assert _as(5, "key_public", "other") is None
    two = _as(6, "key_make", "node")
    assert two != one
    shared = _as(5, "key_shared", "node", two)
    assert len(shared) == 64 and shared == _as(6, "key_shared", "node", one.upper())
    # the secret is a file of the store's system folder, beside the settings
    folder = os.path.join(os.path.dirname(console), "system", "keys")
    assert sorted(os.listdir(folder)) == ["%54.two", "t.one"]
    with open(os.path.join(folder, "t.one", "node"), "rb") as f:
        secret = f.read()
    assert len(secret) == 32 and _x25519(secret).hex() == one
    assert _x25519(secret, H(two)).hex() == shared
    # RFC 7748's vector, through a pair the store holds
    with open(os.path.join(folder, "t.one", "vector"), "wb") as f:
        f.write(H(V.X25519_ALICE))
    bob = _x25519(H(V.X25519_BOB)).hex()
    assert _as(5, "key_shared", "vector", bob) == V.X25519_SHARED


def test_one_app_never_reaches_another_s_pair(console):
    _as(5, "key_make", "node")
    assert _as(6, "key_public", "node") is None
    assert _as(6, "key_shared", "node", _as(5, "key_public", "node")) is None
    for pid in (S.SHELL, 7):
        assert _as(pid, "key_make", "node") is None, "a process of no app has none"
        assert _as(pid, "key_public", "node") is None
    for bad in ("", "../t.one/node", "Node", "a" * 33, "t.one/node", 5):
        with pytest.raises(ValueError):
            _as(6, "key_public", bad)
    assert _as(6, "key_make", "node") != _as(5, "key_public", "node")


def test_an_exchange_refuses_a_point_of_small_order_and_a_key_that_is_none(console):
    _as(5, "key_make", "node")
    assert _as(5, "key_shared", "node", WEAK) is None
    assert _as(5, "key_shared", "node", "0" * 64) is None
    for bad in ("ab", "zz" * 32, None, b"\x09" * 32):
        with pytest.raises(ValueError):
            _as(5, "key_shared", "node", bad)


def test_a_torn_pair_reads_as_none_and_is_made_again(console):
    one = _as(5, "key_make", "node")
    path = os.path.join(app_keys.folder(console), "t.one", "node")
    with open(path, "wb") as f:
        f.write(b"\x01" * 31)
    assert _as(5, "key_public", "node") is None
    assert _as(5, "key_make", "node") not in (None, one)


def test_forget_deletes_one_app_s_keys(console):
    _as(5, "key_make", "node")
    _as(5, "key_make", "two")
    two = _as(6, "key_make", "node")
    app_keys.forget(console, "t.one")
    assert _as(5, "key_public", "node") is None and _as(5, "key_public", "two") is None
    assert _as(6, "key_public", "node") == two
    assert os.listdir(app_keys.folder(console)) == ["%54.two"]
    app_keys.forget(console, "t.one")


def test_the_web_sync_carries_no_key():
    from runtime import moy_sync
    assert not any("keys" in f for f in moy_sync.SYSTEM_FILES)


def _install(ws, folder, ident):
    import json
    d = os.path.join(ws.carts_root, folder)
    os.mkdir(d)
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"title": ident, "id": ident, "runtime": "lua", "main": "main.lua",
                   "moybyte": {"type": "app"}}, f)
    with open(os.path.join(d, "main.lua"), "w") as f:
        f.write("function _draw() end\n")
    ws.carts.rescan()
    return next(c for c in ws.carts.all if c.get("path", "").endswith(folder))


def test_a_cart_s_delete_deletes_its_keys(tmp_path):
    from ws_helpers import build_ws
    from runtime import app_perms
    ws = build_ws(tmp_path)
    cart = _install(ws, "keyed.moy", "t.keyed")
    other = _install(ws, "kept.moy", "t.kept")
    aid = app_perms.app_id(cart)
    app_keys.make(ws.carts_root, aid, "node")
    kept = app_keys.make(ws.carts_root, app_perms.app_id(other), "node")
    ws.carts.delete(cart)
    assert app_keys.public(ws.carts_root, aid, "node") is None
    assert app_keys.public(ws.carts_root, app_perms.app_id(other), "node") == kept


VM_KEYS = r'''
import sys
sys.path[:0] = [@TREE@]
import os
import moy_log as L
import moybyte
import scheduler as S
import app_keys
ROOT = @ROOT@


class Procs:
    def app_id(self, pid):
        return {5: "t.one", 6: "T.two"}.get(pid)


class Console:
    carts_root = ROOT
    procs = Procs()


S.attach(Console())


def call(pid, name, *args):
    was = L.current()
    L.current(pid)
    try:
        return S._CALLS["moybyte." + name](*args)
    except ValueError:
        return "ValueError"
    finally:
        L.current(was)


one = call(5, "key_make", "node")
print("MADE", len(one), call(5, "key_make", "node") == one, call(5, "key_public", "node") == one)
print("OTHER", call(6, "key_public", "node"), call(6, "key_shared", "node", one), call(1, "key_make", "node"))
two = call(6, "key_make", "node")
print("SHARED", call(5, "key_shared", "node", two) == call(6, "key_shared", "node", one), two != one)
print("WEAK", call(5, "key_shared", "node", "@WEAK@"), call(5, "key_shared", "node", "ab"),
      call(5, "key_public", "../x"))
print("FILES", sorted(os.listdir(app_keys.folder(ROOT))))
app_keys.forget(ROOT, "t.one")
print("FORGOT", call(5, "key_public", "node"), call(6, "key_public", "node") == two)
'''


def test_on_the_vm_a_board_runs_the_rows_are_the_same(tmp_path):
    exe = require_unix_mp("moy_app", "moy_log", "moy_input",
                          why="the key rows on the VM a board runs")
    root = tmp_path / "carts"
    root.mkdir()
    script = tmp_path / "keys.py"
    script.write_text(VM_KEYS.replace("@TREE@", repr(runtime_tree()))
                      .replace("@ROOT@", repr(str(root))).replace("@WEAK@", WEAK))
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "MADE 64 True True",
        "OTHER None None None",
        "SHARED True True",
        "WEAK None ValueError ValueError",
        "FILES ['%54.two', 't.one']",
        "FORGOT None True",
    ]
    shutil.rmtree(str(tmp_path / "system"), True)


VM_SVC = """
import moycore, moy_evq
src = "local p = moybyte.key_make('node'); moybyte.exit(#p + #moybyte.key_shared('node', p))"
h = moycore.svc_start(9, "t.keys", src)
moycore.svc_step(0)
t = moy_evq.next_call()
print(t[1:])
moy_evq.answer(t[0], '"%s"')
moycore.svc_step(1)
t = moy_evq.next_call()
print(t[1:])
moy_evq.answer(t[0], '"%s"')
moycore.svc_step(2)
print(moycore.svc_info(h)[:2])
moycore.svc_reap(h)
""" % ("ab" * 32, "cd" * 32)


def test_a_lua_service_on_the_vm_a_board_runs_asks_the_console(tmp_path):
    exe = require_unix_mp("moycore", why="the Lua services on the VM a board runs")
    script = tmp_path / "svc.py"
    script.write_text(VM_SVC)
    done = subprocess.run([str(exe), str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "(9, 'moybyte.key_make', '[\"node\"]')",
        "(9, 'moybyte.key_shared', '[\"node\", \"%s\"]')" % ("ab" * 32),
        "(4, 128)",
    ]
