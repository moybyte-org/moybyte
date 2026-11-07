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
        if tag not in self.held:  # idempotent per tag, as the console's
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
                        lambda root=None: str(tmp_path / "settings"))
    return tmp_path


def test_the_hash_is_the_server_s_form():
    salt = test_moy_ssh.SALT
    assert ssh_service.hashed("moybyte-test", salt) == test_moy_ssh.hashed("moybyte-test")


def test_passwd_sets_and_removes_the_password(system):
    ws = WS()
    d = Door(ws)
    assert not ssh_service.is_set(ws)
    assert sh_roles.r_passwd(d, ["s3cret-pw"]) == 0
    text = (system / "password").read_text()
    salt = text.split("$")[2]
    assert text == ssh_service.hashed("s3cret-pw", salt) + "\n"
    assert ssh_service.is_set(ws)
    assert sh_roles.r_passwd(d, []) == 0 and d.text.startswith("password set\nhost key ")
    assert sh_roles.r_passwd(d, ["-d"]) == 0
    assert not (system / "password").exists()
    assert sh_roles.r_passwd(d, ["a", "b"]) == 2
    # no server on the host: nothing serves, so the radio is never held
    assert ws.held == []


@pytest.mark.parametrize("name, label", [
    ("", "moybyte-beef"), ("Nikola's T-Deck", "nikola-s-t-deck"), ("  --  ", "moybyte-beef"),
    ("ЖЖ box 2", "box-2"), ("x" * 80, "x" * 63)])
def test_the_mdns_name_is_settings_name_as_a_label(name, label):
    assert ssh_service.hostname(WS(name)) == label


def test_a_console_with_no_name_is_moybyte_and_its_mac_s_last_four_hex_digits():
    assert ssh_service.name(WS("")) == "moybyte-beef"
    assert ssh_service.name(WS("Den")) == "Den"
    # never stored: a sync carries only a name the user set
    ws = WS("")
    ssh_service.name(ws)
    assert ws.system.get("name", "") == ""


class Server:
    def __init__(self):
        self.calls = []
        self.is_parked = True

    def parked(self):
        return self.is_parked

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
    monkeypatch.setattr(ssh_service, "_state", {"serving": False, "name": None, "park": None})
    names = []
    monkeypatch.setitem(__import__("sys").modules, "moy_net",
                        type("N", (), {"wifi_mdns": staticmethod(lambda n: names.append(n) or True)}))
    ws = WS("Desk 2")
    ws.wifi = Wifi(up)
    assert not ssh_service.start(ws) and ws.held == []
    assert ssh_service.set_password(ws, "s3cret-pw")
    assert ws.held == ["ssh"] and server.calls == [("start", str(system))]
    # the station starts toward the last-joined network unless it is up
    assert ws.wifi.wlan.dialled == ([] if up else [("last", "pw")])
    assert names == ["desk-2"]
    assert ssh_service.set_password(ws, None)
    assert ws.held == [] and server.calls[-1] == ("stop",)
    # the responder ends with the server: its task is internal SRAM
    assert names == ["desk-2", ""]


class Wakes:
    def __init__(self):
        self.live, self.next = {}, 1

    def every(self, ms, fn):
        wid, self.next = self.next, self.next + 1
        self.live[wid] = fn
        return wid

    def cancel(self, wid):
        return self.live.pop(wid, None) is not None

    def tick(self):
        for fn in list(self.live.values()):
            fn()


def test_a_stop_from_a_session_lets_the_radio_and_the_name_go_once_the_server_parks(
        system, monkeypatch):
    """`passwd -d` over SSH: the server ends after the session that asked, so
    the radio (perhaps its only holder) and the mDNS name stay until the run
    reports its tasks parked; a `passwd x` before then keeps both."""
    server = Server()
    wakes = Wakes()
    monkeypatch.setattr(ssh_service, "_N", server)
    monkeypatch.setattr(ssh_service._moybyte, "WAKES", wakes)
    monkeypatch.setattr(ssh_service, "_state", {"serving": False, "name": None, "park": None})
    names = []
    monkeypatch.setitem(__import__("sys").modules, "moy_net",
                        type("N", (), {"wifi_mdns": staticmethod(lambda n: names.append(n) or True)}))
    ws = WS("Desk")
    ws.wifi = Wifi(True)
    assert ssh_service.set_password(ws, "s3cret-pw")
    server.is_parked = False
    assert ssh_service.set_password(ws, None)
    assert server.calls[-1] == ("stop",)
    assert ws.held == ["ssh"] and names == ["desk"] and len(wakes.live) == 1
    wakes.tick()
    assert ws.held == ["ssh"] and names == ["desk"]
    server.is_parked = True
    wakes.tick()
    assert ws.held == [] and names == ["desk", ""] and wakes.live == {}
    # a start in the same session withdraws the stop: the radio stays held
    assert ssh_service.set_password(ws, "s3cret-pw")
    server.is_parked = False
    assert ssh_service.set_password(ws, None)
    assert ssh_service.set_password(ws, "again-pw")
    assert wakes.live == {} and ws.held == ["ssh"]
    wakes.tick()
    assert ws.held == ["ssh"] and names[-1] == "desk"


def test_the_sign_in_folder_is_beside_the_carts_the_console_booted_on():
    """No card: the console's carts are the internal store's, and so are its
    settings and the server's sign-in files (/moy/system), never the card's."""
    ws = WS()
    ws.carts_root = "/moy/carts"
    assert ssh_service.system_dir(ws) == "/moy/system"
    ws.carts_root = "/sd/moybyte/carts"
    assert ssh_service.system_dir(ws) == "/sd/moybyte/system"
