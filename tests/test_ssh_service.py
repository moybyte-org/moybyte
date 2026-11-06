"""The SSH server's console side (runtime/system/ssh_service.py) on the host,
where no server is built: the password's hash is moy_ssh's form, `passwd`
sets and removes it, and the mDNS name is Settings' Name as a DNS label."""

import pytest

from runtime import ssh_service, sh_roles

import test_moy_ssh


class Rows(dict):
    pass


class WS:
    def __init__(self, name=""):
        self.system = Rows(name=name)
        self.held = []

    def wifi_hold(self, tag):
        self.held.append(tag)

    def wifi_release(self, tag):
        self.held.remove(tag)


class Door:
    def __init__(self, ws):
        self.ws = ws
        self.text, self.errs = "", []

    def out(self, t):
        self.text += t

    def err(self, name, t):
        self.errs.append((name, t))


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh_service._docs, "system_store_path",
                        lambda: str(tmp_path / "settings"))
    return tmp_path


def test_the_hash_is_the_server_s_form():
    salt = test_moy_ssh.SALT
    assert ssh_service.hashed("moybyte-test", salt) == test_moy_ssh.hashed("moybyte-test")


def test_passwd_sets_and_removes_the_password(system):
    ws = WS()
    d = Door(ws)
    assert not ssh_service.is_set()
    assert sh_roles.r_passwd(d, ["s3cret"]) == 0
    text = (system / "password").read_text()
    salt = text.split("$")[2]
    assert text == ssh_service.hashed("s3cret", salt) + "\n"
    assert ssh_service.is_set()
    assert sh_roles.r_passwd(d, []) == 0 and d.text == "password set\n"
    assert sh_roles.r_passwd(d, ["-d"]) == 0
    assert not (system / "password").exists()
    assert sh_roles.r_passwd(d, ["a", "b"]) == 2
    # no server on the host: nothing serves, so the radio is never held
    assert ws.held == []


@pytest.mark.parametrize("name, label", [
    ("", "moybyte"), ("Nikola's T-Deck", "nikola-s-t-deck"), ("  --  ", "moybyte"),
    ("ЖЖ box 2", "box-2"), ("x" * 80, "x" * 63)])
def test_the_mdns_name_is_settings_name_as_a_label(name, label):
    assert ssh_service.hostname(WS(name)) == label


class Server:
    def __init__(self):
        self.calls = []

    def start(self, path):
        self.calls.append(("start", path))

    def stop(self):
        self.calls.append(("stop",))

    def hash(self, password, salt):
        return test_moy_ssh.hashed(password)


class Wlan:
    def __init__(self):
        self.dialled = []

    def connect(self, ssid, password):
        self.dialled.append((ssid, password))


class Store:
    def load_wifi(self, root):
        return [{"ssid": "last", "password": "pw"}, {"ssid": "older", "password": ""}]


class Wifi:
    def __init__(self, up=False):
        self.up, self.wlan, self._store, self._root = up, Wlan(), Store(), "/r"

    def status(self):
        return (self.up, None, None)


@pytest.mark.parametrize("up", [False, True])
def test_serving_holds_the_ssh_lease_dials_the_last_network_and_answers_by_name(system, monkeypatch, up):
    server = Server()
    monkeypatch.setattr(ssh_service, "_N", server)
    monkeypatch.setattr(ssh_service, "_state", {"serving": False, "name": None})
    names = []
    monkeypatch.setitem(__import__("sys").modules, "moy_net",
                        type("N", (), {"wifi_mdns": staticmethod(lambda n: names.append(n) or True)}))
    ws = WS("Desk 2")
    ws.wifi = Wifi(up)
    assert not ssh_service.start(ws) and ws.held == []
    assert ssh_service.set_password(ws, "s3cret")
    assert ws.held == ["ssh"] and server.calls == [("start", str(system))]
    # the station starts toward the last-joined network unless it is up
    assert ws.wifi.wlan.dialled == ([] if up else [("last", "pw")])
    assert names == ["desk-2"]
    assert ssh_service.set_password(ws, None)
    assert ws.held == [] and server.calls[-1] == ("stop",)
    # the responder ends with the server: its task is internal SRAM
    assert names == ["desk-2", ""]
