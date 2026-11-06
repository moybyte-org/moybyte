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
